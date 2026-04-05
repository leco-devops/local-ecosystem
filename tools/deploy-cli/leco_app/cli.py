"""LEco DevOps CLI entrypoint (command: leco-app or leco-devops)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
import yaml
from pydantic import ValidationError

from leco_app import DISPLAY_NAME, __version__
from leco_app.compose_runner import run_compose, run_compose_capture
from leco_app.detectors.archetype import detect_archetype
from leco_app.detectors.compose import detect_compose
from leco_app.detectors.ports import check_host_ports
from leco_app.hooks_runner import run_hooks_phase
from leco_app.detectors.wrangler import detect_wrangler, list_likely_secret_var_keys
from leco_app.ecosystem_registry import (
    register_in_ecosystem,
    resolve_registered_manifest_path,
    unregister_from_ecosystem,
)
from leco_app.local_cf_teardown import teardown_from_leco_local_cf_path
from leco_app.local_cf_provision import provision_from_manifest
from leco_app.onboarding import (
    resolve_ecosystem_root,
    run_registry_and_provision,
    run_traefik_merge_for_manifest,
)
from leco_app.paths import app_state_dir, default_localhost_profile_name, default_manifest_name
from leco_app.schema import (
    ApplicationManifest,
    CloudflareSpec,
    DockerComposeSpec,
    LocalhostProfile,
    MergedApplication,
    ProfileInfrastructureSpec,
    RoutingEntry,
    RoutingSpec,
    ServiceTarget,
    load_effective_manifest,
    load_merged_manifest,
    save_localhost_profile,
    save_manifest,
)
from leco_app.traefik_dynamic_cleanup import manifest_traefik_keys, strip_traefik_dynamic_yml
from leco_app.traefik_fragment import manifest_to_traefik_yaml

app = typer.Typer(
    name="leco-app",
    help=f"{DISPLAY_NAME} — Docker Compose + optional Wrangler; manifests and Traefik hints (see README).",
    no_args_is_help=True,
)

# Shared manifest path (``-f`` is not used on ``logs`` — that reserves ``-f`` for ``--follow``).
ManifestPathOption = Annotated[
    Optional[Path],
    typer.Option("--manifest", "-f", help="Path to leco.app.yaml"),
]

EcosystemRootOption = Annotated[
    Optional[Path],
    typer.Option(
        "--ecosystem-root",
        "-E",
        help="local-ecosystem repo root (or set LECO_ECOSYSTEM_ROOT)",
    ),
]


def _run_onboard_after_deploy(
    mp: Path,
    *,
    ecosystem_root_opt: Path | None,
    register_id: str | None,
    register_label: str | None,
    no_provision_local_cf: bool,
    wrangler_env: str | None,
    traefik_dynamic: Path | None,
) -> None:
    """Register + merge Traefik (raises typer.Exit on registry failure)."""
    er = resolve_ecosystem_root(ecosystem_root_opt)
    if er is None:
        typer.secho(
            "--onboard requires --ecosystem-root / -E or LECO_ECOSYSTEM_ROOT.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    try:
        run_registry_and_provision(
            er,
            mp.resolve(),
            app_id=register_id,
            label=register_label,
            wrangler_env=wrangler_env,
            no_provision_local_cf=no_provision_local_cf,
            echo=typer.secho,
        )
    except (OSError, ValueError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    m = load_effective_manifest(mp)
    run_traefik_merge_for_manifest(
        m,
        ecosystem_root=er,
        traefik_dynamic=traefik_dynamic,
        echo=typer.secho,
    )


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", name.strip()).strip("-").lower()
    return s or "app"


def _try_find_manifest(start: Path, explicit: Path | None) -> Path | None:
    """Return path to leco.app.yaml if it exists, else None."""
    if explicit:
        p = explicit.resolve()
        return p if p.is_file() else None
    cur = start.resolve()
    for _ in range(20):
        cand = cur / default_manifest_name()
        if cand.is_file():
            return cand
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def _find_manifest(start: Path, explicit: Path | None) -> Path:
    mp = _try_find_manifest(start, explicit)
    if mp is None:
        if explicit:
            p = explicit.resolve()
            typer.secho(f"Manifest not found: {p}", fg=typer.colors.RED, err=True)
        else:
            typer.secho(
                f"No {default_manifest_name()} found (walk up from {start}). "
                "Run `leco-app init` or pass --manifest.",
                fg=typer.colors.RED,
                err=True,
            )
        raise typer.Exit(1)
    return mp


def _validate_existing_manifest_tree(mp: Path) -> tuple[MergedApplication | None, str | None]:
    """Parse leco.app.yaml + profile; ensure referenced files exist. Returns (merged, error_message)."""
    try:
        merged = load_merged_manifest(mp)
    except (ValidationError, ValueError, yaml.YAMLError) as e:
        return None, str(e)
    except OSError as e:
        return None, str(e)
    m = merged.manifest
    if m.local_host_profile:
        rel = Path(m.local_host_profile.strip())
        prof = (mp.parent / rel).resolve()
        if not prof.is_file():
            return None, f"localHostProfile not found: {prof} (manifest references {m.local_host_profile!r})"
    root = m.resolved_root(mp)
    if m.docker_compose:
        cf = root / Path(m.docker_compose.compose_file)
        if not cf.is_file():
            return None, f"dockerCompose.composeFile not found: {cf}"
        for rel in m.docker_compose.additional_compose_files or []:
            p = Path(str(rel).strip())
            if not str(p):
                continue
            ap = p.resolve() if p.is_absolute() else (root / p).resolve()
            if not ap.is_file():
                return None, f"dockerCompose.additionalComposeFiles entry not found: {ap}"
    if m.cloudflare and m.cloudflare.wrangler_config:
        wc = root / Path(m.cloudflare.wrangler_config)
        if not wc.is_file():
            return None, f"cloudflare.wranglerConfig not found: {wc}"
    return merged, None


def _deploy_from_manifest(mp: Path, *, strict_compose: bool = True) -> int:
    """docker compose up -d --build from manifest paths only (same as deploy command).

    If ``strict_compose`` is False (e.g. after ``init``), missing dockerCompose logs a warning and returns 0.
    """
    m = load_effective_manifest(mp)
    if not m.docker_compose:
        msg = (
            "Manifest has no dockerCompose — skipping Docker deploy "
            "(add dockerCompose to leco.yaml infrastructure or leco.app.yaml, or use leco-app cf-deploy for Workers)."
        )
        if strict_compose:
            typer.secho("Manifest has no dockerCompose section.", fg=typer.colors.RED, err=True)
            return 1
        typer.secho(msg, fg=typer.colors.YELLOW, err=True)
        return 0
    root = m.resolved_root(mp)
    cd = detect_compose(root)
    if cd.host_ports:
        conflicts = check_host_ports(cd.host_ports)
        busy = [p for p, used in conflicts.items() if used]
        if busy:
            typer.secho(
                f"Warning: possible port conflicts: {busy} — continuing.",
                fg=typer.colors.YELLOW,
                err=True,
            )
    code = run_compose(m, mp, ["up", "-d", "--build"])
    if code == 0:
        typer.secho("Deploy finished.", fg=typer.colors.GREEN)
        meta = app_state_dir(m.name)
        (meta / "last-deploy.json").write_text(
            json.dumps({"manifest": str(mp.resolve()), "root": str(root)}),
            encoding="utf-8",
        )
    return code


@app.command("version")
def cmd_version() -> None:
    typer.echo(f"{DISPLAY_NAME} {__version__}")


@app.command("detect")
def cmd_detect(
    path: Annotated[Path, typer.Argument(help="Application root directory")] = Path("."),
    as_json: Annotated[bool, typer.Option("--json/--text", help="Emit JSON (default) or one-line summary")] = True,
) -> None:
    """Print detected compose/wrangler/archetype hints (for dashboard registration wizard)."""
    root = path.resolve()
    if not root.is_dir():
        typer.secho(f"Not a directory: {root}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    cd = detect_compose(root)
    wd = detect_wrangler(root)
    arch = detect_archetype(root)
    manifest_path = root / default_manifest_name()
    wc = wd.config_path
    data = {
        "root": str(root),
        "has_wrangler": wc is not None,
        "wrangler_config": wc.as_posix() if wc else None,
        "compose_files": [p.as_posix() for p in cd.compose_files],
        "host_ports": cd.host_ports,
        "suggested_archetype": arch,
        "existing_manifest": manifest_path.is_file(),
        "manifest_path": manifest_path.resolve().as_posix(),
    }
    if as_json:
        typer.echo(json.dumps(data, indent=2))
    else:
        typer.echo(
            f"archetype={arch} wrangler={data['has_wrangler']} "
            f"compose={len(cd.compose_files)} manifest={data['existing_manifest']}"
        )


@app.command("run-hooks")
def cmd_run_hooks(
    phase: Annotated[str, typer.Argument(help="prepare | build | preStart")],
    cwd: Annotated[Path, typer.Option("--cwd", help="Search manifest from this directory")] = Path("."),
    manifest: ManifestPathOption = None,
) -> None:
    """Run localhost profile lifecycle commands for the given phase."""
    valid = frozenset({"prepare", "build", "preStart"})
    if phase not in valid:
        typer.secho(f"phase must be one of: {', '.join(sorted(valid))}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    mp = _find_manifest(cwd, manifest)
    code = run_hooks_phase(mp, phase, echo=typer.echo)  # type: ignore[arg-type]
    raise typer.Exit(code)


@app.command("init")
def cmd_init(
    path: Annotated[
        Path,
        typer.Argument(
            help="Directory to search upward for leco.app.yaml, or app root when creating files",
        ),
    ] = Path("."),
    manifest: ManifestPathOption = None,
    manifest_out: Annotated[
        Optional[Path],
        typer.Option(
            "--out",
            "-o",
            help=f"When creating files: write manifest here (default: <path>/{default_manifest_name()})",
        ),
    ] = None,
    non_interactive: Annotated[bool, typer.Option("--yes", "-y", help="Skip prompts; use defaults")] = False,
    provision_local_cf: Annotated[
        bool,
        typer.Option(
            "--provision-local-cf",
            help="After init, create KV/R2/D1 on local cloudflare-local (kv.lh, r2.lh, d1.lh) from wrangler.toml",
        ),
    ] = False,
    no_provision_local_cf: Annotated[
        bool,
        typer.Option(
            "--no-provision-local-cf",
            help="Never run local CF provision (overrides prompt / --provision-local-cf)",
        ),
    ] = False,
    manifest_only: Annotated[
        bool,
        typer.Option(
            "--manifest-only",
            help="Allow init with no docker-compose and no wrangler (profile + registry-oriented manifest)",
        ),
    ] = False,
    ecosystem_root: EcosystemRootOption = None,
    onboard: Annotated[
        bool,
        typer.Option(
            "--onboard",
            help="After deploy: ecosystem-register + merge routing into traefik/dynamic.yml (needs -E or LECO_ECOSYSTEM_ROOT)",
        ),
    ] = False,
    register_id: Annotated[
        Optional[str],
        typer.Option("--register-id", help="With --onboard: registry id (default: manifest name)"),
    ] = None,
    register_label: Annotated[
        Optional[str],
        typer.Option("--register-label", "-L", help="With --onboard: Hosted apps card title"),
    ] = None,
) -> None:
    """Validate **leco.app.yaml** + sidecar profile and deploy, or run the wizard then deploy.

    If a manifest already exists (walk up from *path* or ``--manifest``): load and validate YAML, ensure
    ``localHostProfile`` file exists when set, ensure compose / wrangler paths exist, then run Docker
    deploy. On error, print and stop (no prompts). Use ``--provision-local-cf`` to provision local CF.

    If no manifest is found: prompts (unless ``-y``) create **leco.app.yaml** and default **leco.yaml**,
    then deploy. The hub file is always **leco.app.yaml** (not manifest.yaml); **leco.yaml** is the
    optional profile for URLs and lifecycle hooks (see README).

    Use ``--onboard`` with ``-E`` (or ``LECO_ECOSYSTEM_ROOT``) to register the app for Hosted apps and
    merge ``routing.entries`` into ``traefik/dynamic.yml`` (same as ``leco-app onboard``).
    """
    start = path.resolve()
    mp_existing = _try_find_manifest(start, manifest)

    if mp_existing is not None:
        merged, err = _validate_existing_manifest_tree(mp_existing)
        if err:
            typer.secho(err, fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        typer.secho(f"Validated {mp_existing}", fg=typer.colors.GREEN)
        app_root = merged.manifest.resolved_root(mp_existing)
        st = app_state_dir(merged.manifest.name)
        (st / "last-init.json").write_text(
            json.dumps({"root": str(app_root), "manifest": str(mp_existing.resolve())}, indent=2),
            encoding="utf-8",
        )
        cf = merged.manifest.cloudflare
        if (
            not onboard
            and cf
            and not no_provision_local_cf
            and provision_local_cf
        ):
            code_cf = provision_from_manifest(
                mp_existing.resolve(),
                app_slug=merged.manifest.name,
                wrangler_env=cf.wrangler_env,
                echo=typer.echo,
                no_provision_local_cf=no_provision_local_cf,
            )
            if code_cf != 0:
                typer.secho(
                    "Local CF provision had failures (is cloudflare-local up? Try LECO_LOCAL_KV_URL etc.).",
                    fg=typer.colors.YELLOW,
                    err=True,
                )
        code = _deploy_from_manifest(mp_existing, strict_compose=False)
        if onboard and code == 0:
            w_env = cf.wrangler_env if cf else None
            _run_onboard_after_deploy(
                mp_existing,
                ecosystem_root_opt=ecosystem_root,
                register_id=register_id,
                register_label=register_label,
                no_provision_local_cf=no_provision_local_cf,
                wrangler_env=w_env,
                traefik_dynamic=None,
            )
        raise typer.Exit(code)

    root = start
    if not root.is_dir():
        typer.secho(f"Not a directory: {root}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    cd = detect_compose(root)
    wd = detect_wrangler(root)

    default_name = _slugify(root.name)
    if non_interactive:
        name = default_name
    else:
        name = _slugify(typer.prompt("Application slug (manifest name)", default=default_name))

    compose_file: str | None = None
    if cd.compose_files:
        if len(cd.compose_files) == 1:
            compose_file = str(cd.compose_files[0])
        elif non_interactive:
            compose_file = str(cd.compose_files[0])
        else:
            typer.echo("Found compose files:")
            for i, f in enumerate(cd.compose_files):
                typer.echo(f"  [{i}] {f}")
            idx = typer.prompt("Select index", default=0, type=int)
            compose_file = str(cd.compose_files[idx])

    env_file = cd.suggested_env_file
    if compose_file and env_file and not (root / env_file).is_file():
        if non_interactive or typer.confirm(f"Env file {env_file} missing — still record it?", default=False):
            pass
        else:
            env_file = None

    docker_spec: DockerComposeSpec | None = None
    if compose_file:
        docker_spec = DockerComposeSpec(compose_file=compose_file, env_file=env_file)

    cf_spec: CloudflareSpec | None = None
    if wd.config_path:
        include = non_interactive or typer.confirm(
            f"Include Cloudflare Worker config {wd.config_path}?", default=True
        )
        if include:
            w_env: str | None = None
            if wd.env_sections and not non_interactive:
                typer.echo(f"Wrangler env tables found: {', '.join(wd.env_sections)}")
                w_env = typer.prompt("Default wrangler --env (empty for top-level only)", default="").strip() or None
            cf_spec = CloudflareSpec(wrangler_config=str(wd.config_path), wrangler_env=w_env)

    routing: RoutingSpec | None = None
    if not non_interactive and typer.confirm("Add optional Traefik routing (*.lh) entries?", default=False):
        typer.echo("Enter each route, then press Enter on an empty hostname to finish.")
        entries: list[RoutingEntry] = []
        while True:
            hn = typer.prompt(
                "Hostname (e.g. myapp.lh; empty = done)",
                default="",
                show_default=False,
            )
            if not hn.strip():
                break
            split_ui_api = typer.confirm(
                "Split route (browser UI + same-host /api → backend)? Recommended for React + FastAPI.",
                default=False,
            )
            if split_ui_api:
                fh = typer.prompt("Frontend container/DNS name (e.g. cv-frontend)", default="")
                fp = typer.prompt("Frontend container port", default=3000, type=int)
                ap = typer.prompt("API path prefix", default="/api")
                ah = typer.prompt("API backend container/DNS name (e.g. cv-backend)", default="")
                api_p = typer.prompt("API backend container port", default=8001, type=int)
                if not fh.strip() or not ah.strip():
                    typer.secho("Split mode needs non-empty frontend and API host names.", fg=typer.colors.RED, err=True)
                    raise typer.Exit(1)
                entries.append(
                    RoutingEntry(
                        hostname=hn.strip(),
                        api_path_prefix=ap.strip() or "/api",
                        frontend=ServiceTarget(host=fh.strip(), port=fp),
                        api_backend=ServiceTarget(host=ah.strip(), port=api_p),
                    )
                )
            else:
                bh = typer.prompt("Backend Docker DNS name (container/service)", default="")
                bp = typer.prompt("Backend port", default=8080, type=int)
                if not bh.strip():
                    typer.secho("Skipped this host: backend name empty.", fg=typer.colors.YELLOW, err=True)
                    continue
                entries.append(RoutingEntry(hostname=hn.strip(), backend_host=bh.strip(), backend_port=bp))
        if entries:
            routing = RoutingSpec(entries=entries)

    health_urls: list[str] = []
    if cd.host_ports and not non_interactive:
        if typer.confirm("Add a healthcheck URL on localhost?", default=False):
            hp = typer.prompt("Full URL", default=f"http://127.0.0.1:{cd.host_ports[0]}/")
            health_urls.append(hp)

    if not docker_spec and not cf_spec:
        if not manifest_only:
            typer.secho(
                "No docker-compose file or wrangler.toml found — pass --manifest-only or add files.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(1)
        if not non_interactive and not typer.confirm(
            "No compose/wrangler: create manifest-only entry with leco.yaml?",
            default=True,
        ):
            raise typer.Exit(0)

    out = manifest_out or (root / default_manifest_name())
    arche = detect_archetype(root)
    localhost_name = default_localhost_profile_name()
    lp_path = out.parent / localhost_name

    infra_kw: dict[str, Any] = {}
    if docker_spec is not None:
        infra_kw["docker_compose"] = docker_spec
    if cf_spec is not None:
        infra_kw["cloudflare"] = cf_spec
    if routing is not None and routing.entries:
        infra_kw["routing"] = routing
    if health_urls:
        infra_kw["healthcheck_urls"] = list(health_urls)
    profile_infra = ProfileInfrastructureSpec(**infra_kw) if infra_kw else None
    init_profile = LocalhostProfile(schema_version=2, archetype=arche, infrastructure=profile_infra)
    if not lp_path.exists():
        save_localhost_profile(lp_path, init_profile)
        typer.secho(f"Wrote {lp_path}", fg=typer.colors.GREEN)

    manifest = ApplicationManifest(
        leco_app_version="3",
        name=name,
        root=".",
        local_host_profile=localhost_name,
    )

    save_manifest(out, manifest)

    typer.secho(f"Wrote {out}", fg=typer.colors.GREEN)

    if cd.host_ports:
        conflicts = check_host_ports(cd.host_ports)
        busy = [p for p, used in conflicts.items() if used]
        if busy:
            typer.secho(
                f"Warning: host ports may be in use (bind check): {busy}",
                fg=typer.colors.YELLOW,
                err=True,
            )

    if (root / "docker" / "docker-dev.sh").is_file():
        typer.echo("Tip: this repo has docker/docker-dev.sh — you can still use leco-app deploy with the manifest.")

    if wd.config_path:
        typer.echo("Wrangler bindings detected:")
        if wd.kv_bindings:
            typer.echo(f"  KV: {', '.join(wd.kv_bindings)}")
        if wd.r2_bindings:
            typer.echo(f"  R2: {', '.join(wd.r2_bindings)}")
        if wd.d1_bindings:
            typer.echo(f"  D1: {', '.join(wd.d1_bindings)}")
        if wd.browser_binding:
            typer.echo(f"  Browser: {wd.browser_binding}")

    st = app_state_dir(name)
    (st / "last-init.json").write_text(
        json.dumps({"root": str(root), "manifest": str(out.resolve())}, indent=2),
        encoding="utf-8",
    )

    if cf_spec and not no_provision_local_cf and not onboard:
        do_pv = False
        if provision_local_cf:
            do_pv = True
        elif not non_interactive:
            do_pv = typer.confirm(
                "Create dedicated local KV namespaces, R2 buckets, and D1 DBs from wrangler (cloudflare-local)?",
                default=True,
            )
        if do_pv:
            code = provision_from_manifest(
                out.resolve(),
                app_slug=name,
                wrangler_env=cf_spec.wrangler_env,
                echo=typer.echo,
                no_provision_local_cf=no_provision_local_cf,
            )
            if code != 0:
                typer.secho(
                    "Local CF provision had failures (is cloudflare-local up? Try LECO_LOCAL_KV_URL etc.).",
                    fg=typer.colors.YELLOW,
                    err=True,
                )

    merged_post, err_post = _validate_existing_manifest_tree(out.resolve())
    if err_post:
        typer.secho(f"Invalid generated manifest: {err_post}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    code_dep = _deploy_from_manifest(out.resolve(), strict_compose=False)
    if onboard and code_dep == 0:
        w_env = cf_spec.wrangler_env if cf_spec else None
        _run_onboard_after_deploy(
            out.resolve(),
            ecosystem_root_opt=ecosystem_root,
            register_id=register_id,
            register_label=register_label,
            no_provision_local_cf=no_provision_local_cf,
            wrangler_env=w_env,
            traefik_dynamic=None,
        )
    raise typer.Exit(code_dep)


@app.command("deploy")
def cmd_deploy(
    cwd: Annotated[Path, typer.Option("--cwd", help="Search manifest from this directory")] = Path("."),
    manifest: ManifestPathOption = None,
    no_provision_local_cf: Annotated[
        bool,
        typer.Option(
            "--no-provision-local-cf",
            help="Skip local KV/R2/D1 from wrangler after compose (default: provision when cloudflare.wranglerConfig is set)",
        ),
    ] = False,
) -> None:
    """docker compose up -d --build; then local KV/R2/D1 from wrangler when policy allows (see README)."""
    mp = _find_manifest(cwd, manifest)
    merged, err = _validate_existing_manifest_tree(mp)
    if err:
        typer.secho(f"Invalid manifest or missing files: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    code = _deploy_from_manifest(mp, strict_compose=True)
    if code != 0:
        raise typer.Exit(code)
    m = load_effective_manifest(mp)
    code_cf = provision_from_manifest(
        mp.resolve(),
        app_slug=m.name,
        wrangler_env=m.cloudflare.wrangler_env if m.cloudflare else None,
        echo=typer.secho,
        no_provision_local_cf=no_provision_local_cf,
    )
    if code_cf != 0:
        typer.secho(
            "Local CF provision had failures (Docker deploy succeeded). "
            "Fix adapters/DNS and run: leco-app provision-local-cf",
            fg=typer.colors.YELLOW,
            err=True,
        )
    raise typer.Exit(0)


@app.command("onboard")
def cmd_onboard(
    cwd: Annotated[Path, typer.Option("--cwd", help="Search manifest from this directory")] = Path("."),
    manifest: ManifestPathOption = None,
    ecosystem_root: EcosystemRootOption = None,
    app_id: Annotated[Optional[str], typer.Option("--id", help="Registry slug (default: manifest name)")] = None,
    label: Annotated[Optional[str], typer.Option("--label", "-l", help="Hosted apps card title")] = None,
    no_deploy: Annotated[bool, typer.Option("--no-deploy", help="Skip docker compose up -d --build")] = False,
    no_register: Annotated[bool, typer.Option("--no-register", help="Skip leco-registry.yaml")] = False,
    no_traefik_merge: Annotated[
        bool,
        typer.Option("--no-traefik-merge", help="Skip merging routing into traefik/dynamic.yml"),
    ] = False,
    traefik_dynamic: Annotated[
        Optional[Path],
        typer.Option(
            "--traefik-dynamic",
            help="Path to dynamic.yml (default: <ecosystem-root>/traefik/dynamic.yml)",
        ),
    ] = None,
    wrangler_env: Annotated[
        Optional[str],
        typer.Option("--wrangler-env", help="Override for local KV/R2/D1 provision"),
    ] = None,
    no_provision_local_cf: Annotated[
        bool,
        typer.Option("--no-provision-local-cf", help="Skip local cloudflare-local resource creation"),
    ] = False,
) -> None:
    """Deploy stack, register for Hosted apps, merge Traefik routes — typical new-app flow for local-ecosystem."""
    mp = _find_manifest(cwd, manifest)
    merged, err = _validate_existing_manifest_tree(mp)
    if err:
        typer.secho(f"Invalid manifest or missing files: {err}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    m = merged.manifest
    if not no_deploy:
        if m.docker_compose:
            code = _deploy_from_manifest(mp, strict_compose=True)
            if code != 0:
                raise typer.Exit(code)
        else:
            typer.secho("No dockerCompose in manifest — deploy step skipped.", fg=typer.colors.YELLOW, err=True)
    er = resolve_ecosystem_root(ecosystem_root)
    if not no_register:
        if er is None:
            typer.secho(
                "Registration needs --ecosystem-root / -E or LECO_ECOSYSTEM_ROOT.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(1)
        try:
            run_registry_and_provision(
                er,
                mp.resolve(),
                app_id=app_id,
                label=label,
                wrangler_env=wrangler_env,
                no_provision_local_cf=no_provision_local_cf,
                echo=typer.secho,
            )
        except (OSError, ValueError) as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc
    if not no_traefik_merge:
        run_traefik_merge_for_manifest(
            load_effective_manifest(mp),
            ecosystem_root=er,
            traefik_dynamic=traefik_dynamic,
            echo=typer.secho,
        )


@app.command("stop")
def cmd_stop(
    cwd: Path = Path("."),
    manifest: ManifestPathOption = None,
) -> None:
    """docker compose stop"""
    mp = _find_manifest(cwd, manifest)
    m = load_effective_manifest(mp)
    if not m.docker_compose:
        typer.secho("Manifest has no dockerCompose section.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    code = run_compose(m, mp, ["stop"])
    raise typer.Exit(code)


@app.command("down")
def cmd_down(
    cwd: Path = Path("."),
    manifest: ManifestPathOption = None,
    volumes: Annotated[bool, typer.Option("--volumes", "-v", help="docker compose down -v")] = False,
) -> None:
    """docker compose down"""
    mp = _find_manifest(cwd, manifest)
    m = load_effective_manifest(mp)
    if not m.docker_compose:
        typer.secho("Manifest has no dockerCompose section.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    root = m.resolved_root(mp)
    cf = root / Path(m.docker_compose.compose_file)
    if not cf.is_file():
        typer.secho(
            f"Compose file not on disk ({cf}) — treating stack as already removed (exit 0).",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(0)
    args = ["down", "--remove-orphans"]
    if volumes:
        args.append("-v")
    code = run_compose(m, mp, args)
    raise typer.Exit(code)


@app.command("offload")
def cmd_offload(
    cwd: Path = Path("."),
    manifest: ManifestPathOption = None,
    volumes: Annotated[bool, typer.Option("--volumes", "-v", help="docker compose down -v (delete volumes)")] = False,
    traefik_dynamic: Annotated[
        Optional[Path],
        typer.Option(
            "--traefik-dynamic",
            help="Remove this app's routers/services from Traefik file-provider YAML (e.g. ../local-ecosystem/traefik/dynamic.yml)",
        ),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print plan only; no compose or file changes")] = False,
    yes: Annotated[bool, typer.Option("-y", "--yes", help="Skip confirmation")] = False,
) -> None:
    """Remove app from localhost: optional Traefik routes + docker compose down."""
    mp = _find_manifest(cwd, manifest)
    m = load_effective_manifest(mp)

    has_compose = bool(m.docker_compose)
    rkeys, skeys = manifest_traefik_keys(m)
    has_traefik_keys = bool(rkeys or skeys)

    if traefik_dynamic and not has_traefik_keys:
        typer.secho(
            "Manifest has no routing-derived Traefik keys and no traefikCleanup block — "
            "nothing to remove from dynamic.yml (add traefikCleanup.routers/services if you renamed keys).",
            fg=typer.colors.YELLOW,
            err=True,
        )

    if not has_compose and not traefik_dynamic:
        typer.secho(
            "Nothing to do: manifest has no dockerCompose and --traefik-dynamic not set.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    if not dry_run and not yes:
        parts = []
        if traefik_dynamic and has_traefik_keys:
            parts.append(f"edit Traefik file ({len(rkeys)} routers, {len(skeys)} services)")
        if has_compose:
            parts.append("docker compose down" + (" -v" if volumes else ""))
        if parts and not typer.confirm(f"Offload: {'; '.join(parts)} — continue?", default=False):
            raise typer.Exit(0)

    if traefik_dynamic and has_traefik_keys:
        tf = traefik_dynamic.resolve()
        if not tf.is_file():
            typer.secho(f"Not a file: {tf}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        try:
            rr, ss, bak = strip_traefik_dynamic_yml(tf, rkeys, skeys, dry_run=dry_run)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            typer.secho(f"Traefik YAML error: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc
        if dry_run:
            typer.echo(f"Traefik [dry-run]: would remove {rr} router(s), {ss} service(s) from {tf}")
        elif rr == 0 and ss == 0:
            typer.secho(
                f"No matching router/service keys in {tf} (already removed or keys differ from manifest).",
                fg=typer.colors.YELLOW,
                err=True,
            )
        else:
            typer.secho(
                f"Traefik: removed {rr} router(s), {ss} service(s); backup {bak}",
                fg=typer.colors.GREEN,
            )

    if has_compose:
        if dry_run:
            typer.echo(f"[dry-run] docker compose down{' -v' if volumes else ''} (app {m.name}, {mp.parent})")
        else:
            args = ["down", "--remove-orphans"]
            if volumes:
                args.append("-v")
            code = run_compose(m, mp, args)
            if code == 0:
                typer.secho("Compose stack removed.", fg=typer.colors.GREEN)
            raise typer.Exit(code)


@app.command("logs")
def cmd_logs(
    cwd: Path = Path("."),
    manifest: Optional[Path] = None,
    follow: Annotated[bool, typer.Option("-f", "--follow", help="Stream logs")] = False,
    tail: Annotated[Optional[int], typer.Option("--tail", help="Number of lines")] = None,
    service: Annotated[Optional[str], typer.Option("--service", "-s")] = None,
) -> None:
    """docker compose logs"""
    mp = _find_manifest(cwd, manifest)
    m = load_effective_manifest(mp)
    if not m.docker_compose:
        typer.secho("Manifest has no dockerCompose section.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    args: list[str] = ["logs"]
    if follow:
        args.append("-f")
    if tail is not None:
        args.extend(["--tail", str(tail)])
    if service:
        args.append(service)
    code = run_compose(m, mp, args)
    raise typer.Exit(code)


@app.command("status")
def cmd_status(
    cwd: Path = Path("."),
    manifest: ManifestPathOption = None,
) -> None:
    """docker compose ps and optional HTTP health checks"""
    mp = _find_manifest(cwd, manifest)
    m = load_effective_manifest(mp)
    if not m.docker_compose:
        typer.secho("Manifest has no dockerCompose section.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    cp = run_compose_capture(m, mp, ["ps", "-a"])
    typer.echo(cp.stdout)
    if cp.returncode != 0:
        typer.secho(cp.stderr, fg=typer.colors.RED, err=True)
        raise typer.Exit(cp.returncode)
    for url in m.healthcheck_urls:
        typer.echo(f"GET {url} …")
        try:
            import urllib.request

            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                typer.secho(f"  -> {resp.status}", fg=typer.colors.GREEN)
        except Exception as exc:
            typer.secho(f"  -> failed: {exc}", fg=typer.colors.YELLOW)


@app.command("traefik-fragment")
def cmd_traefik_fragment(
    cwd: Path = Path("."),
    manifest: ManifestPathOption = None,
    out: Annotated[Optional[Path], typer.Option("--out", "-o", help="Write YAML fragment")] = None,
) -> None:
    """Print YAML fragment (use ``onboard`` / ``init --onboard`` / ``ecosystem-register --merge-traefik`` to merge automatically)."""
    mp = _find_manifest(cwd, manifest)
    m = load_effective_manifest(mp)
    text = manifest_to_traefik_yaml(m)
    if out:
        out.write_text(text, encoding="utf-8")
        typer.secho(f"Wrote {out}", fg=typer.colors.GREEN)
    else:
        typer.echo(text)


@app.command("ecosystem-register")
def cmd_ecosystem_register(
    cwd: Path = Path("."),
    manifest: ManifestPathOption = None,
    ecosystem_root: EcosystemRootOption = None,
    app_id: Annotated[Optional[str], typer.Option("--id", help="Registry slug (default: manifest name)")] = None,
    label: Annotated[Optional[str], typer.Option("--label", "-l", help="LEco DevOps Hosted apps card title")] = None,
    wrangler_env: Annotated[
        Optional[str],
        typer.Option(
            "--wrangler-env",
            help="Which [env.NAME] to read for KV/R2/D1 (default: manifest cloudflare.wranglerEnv or top-level)",
        ),
    ] = None,
    no_provision_local_cf: Annotated[
        bool,
        typer.Option(
            "--no-provision-local-cf",
            help="Skip creating local KV/R2/D1 from wrangler on kv.lh / r2.lh / d1.lh",
        ),
    ] = False,
    merge_traefik: Annotated[
        bool,
        typer.Option(
            "--merge-traefik/--no-merge-traefik",
            help="Merge routing.entries into traefik/dynamic.yml (atomic write + .bak)",
        ),
    ] = False,
    registry_manifest_relpath: Annotated[
        Optional[str],
        typer.Option(
            "--registry-manifest-relpath",
            help="Store this path in leco-registry.yaml (must be same inode as --manifest; e.g. hosting/app-available/slug/leco.app.yaml)",
        ),
    ] = None,
) -> None:
    """Register this app in local-ecosystem config/leco-registry.yaml for LEco DevOps (Hosted apps / Control)."""
    er = resolve_ecosystem_root(ecosystem_root)
    if er is None:
        typer.secho(
            "Pass --ecosystem-root /path/to/local-ecosystem or export LECO_ECOSYSTEM_ROOT.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    mp = _find_manifest(cwd, manifest)
    try:
        entry = register_in_ecosystem(
            er.resolve(),
            mp.resolve(),
            app_id=app_id,
            label=label,
            registry_manifest_relpath=registry_manifest_relpath,
        )
    except (OSError, ValueError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.secho(
        f"Registered {entry['id']} → {er / 'config' / 'leco-registry.yaml'} (Control target leco-stack-{entry['id']})",
        fg=typer.colors.GREEN,
    )
    code = provision_from_manifest(
        mp.resolve(),
        app_slug=entry["id"],
        wrangler_env=wrangler_env,
        echo=typer.echo,
        no_provision_local_cf=no_provision_local_cf,
    )
    if code != 0:
        typer.secho(
            "Local CF provision had failures — registry entry is still saved. "
            "Fix adapters/DNS and run: leco-app provision-local-cf",
            fg=typer.colors.YELLOW,
            err=True,
        )
    if merge_traefik:
        run_traefik_merge_for_manifest(
            load_effective_manifest(mp),
            ecosystem_root=er,
            traefik_dynamic=None,
            echo=typer.secho,
        )


@app.command("provision-local-cf")
def cmd_provision_local_cf(
    cwd: Path = Path("."),
    manifest: ManifestPathOption = None,
    wrangler_env: Annotated[
        Optional[str],
        typer.Option("--wrangler-env", help="Override manifest cloudflare.wranglerEnv"),
    ] = None,
    app_slug: Annotated[
        Optional[str],
        typer.Option("--app-slug", help="Override app id for KV namespace prefix (default: manifest name)"),
    ] = None,
) -> None:
    """Create KV namespaces, R2 buckets, and D1 databases on local adapters from wrangler.toml (manifest path)."""
    mp = _find_manifest(cwd, manifest)
    m = load_effective_manifest(mp)
    slug = (app_slug or "").strip() or m.name
    code = provision_from_manifest(
        mp.resolve(),
        app_slug=slug,
        wrangler_env=wrangler_env,
        echo=typer.echo,
        ignore_policy=True,
    )
    raise typer.Exit(code)


@app.command("ecosystem-unregister")
def cmd_ecosystem_unregister(
    app_id: Annotated[str, typer.Argument(help="Registry slug (same as ecosystem-register id)")],
    ecosystem_root: Annotated[
        Optional[Path],
        typer.Option("--ecosystem-root", "-E", help="local-ecosystem repo root"),
    ] = None,
    compose_down: Annotated[
        bool,
        typer.Option(
            "--compose-down/--no-compose-down",
            help="Run docker compose down after local CF cleanup (when both on) so in-project adapters stay up for DELETE",
        ),
    ] = True,
    compose_volumes: Annotated[
        bool,
        typer.Option(
            "--compose-volumes/--no-compose-volumes",
            help="With compose-down: pass -v to docker compose down (delete volumes)",
        ),
    ] = False,
    strip_traefik: Annotated[
        bool,
        typer.Option(
            "--strip-traefik/--no-strip-traefik",
            help="Remove routers/services derived from manifest (traefik/dynamic.yml)",
        ),
    ] = True,
    clean_local_cf: Annotated[
        bool,
        typer.Option(
            "--clean-local-cf/--no-clean-local-cf",
            help="Delete KV/R2/D1 using bases in leco.local-cf.yaml (runs before compose-down when both on)",
        ),
    ] = True,
    traefik_dynamic: Annotated[
        Optional[Path],
        typer.Option(
            "--traefik-dynamic",
            help="Traefik file-provider YAML (default: <ecosystem-root>/traefik/dynamic.yml)",
        ),
    ] = None,
) -> None:
    """Remove an app from leco-registry.yaml; local CF cleanup before compose down when both are enabled (dedicated adapters need this)."""
    er = ecosystem_root
    if er is None:
        raw = (os.environ.get("LECO_ECOSYSTEM_ROOT") or "").strip()
        er = Path(raw).expanduser() if raw else None
    if er is None or not er.is_dir():
        typer.secho("Pass --ecosystem-root or set LECO_ECOSYSTEM_ROOT.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    eco = er.resolve()
    mp = resolve_registered_manifest_path(eco, app_id)
    if not mp:
        typer.secho(
            f"No registry entry or manifest file for {app_id!r} — only registry row will be removed if present.",
            fg=typer.colors.YELLOW,
            err=True,
        )

    tf = traefik_dynamic.resolve() if traefik_dynamic else (eco / "traefik" / "dynamic.yml")

    # Tear down KV/R2/D1 on adapters while the stack is still up. Dedicated adapters
    # (leco-local-*) live in the compose project; compose down first makes DELETEs fail (HTTP -1).
    if mp and clean_local_cf:
        cf_yaml = mp.parent / "leco.local-cf.yaml"
        typer.secho("Local CF cleanup (before docker compose down when enabled)…", fg=typer.colors.BLUE)
        fails = teardown_from_leco_local_cf_path(cf_yaml, echo=typer.echo)
        if fails:
            typer.secho(
                "Local CF cleanup failed — registry not changed. Fix adapters or use --no-clean-local-cf.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(1)

    if mp and compose_down:
        try:
            m = load_effective_manifest(mp)
            if m.docker_compose:
                root = m.resolved_root(mp)
                cf = root / Path(m.docker_compose.compose_file)
                if cf.is_file():
                    args = ["down", "--remove-orphans"]
                    if compose_volumes:
                        args.append("-v")
                    dcode = run_compose(m, mp, args)
                    if dcode == 0:
                        typer.secho(
                            "docker compose down completed.",
                            fg=typer.colors.GREEN,
                        )
                    else:
                        typer.secho(
                            f"docker compose down exited {dcode} — continuing with unregister",
                            fg=typer.colors.YELLOW,
                            err=True,
                        )
                else:
                    typer.secho(
                        f"Compose file not on disk ({cf}) — skipping docker compose down.",
                        fg=typer.colors.YELLOW,
                        err=True,
                    )
        except (OSError, ValidationError, ValueError) as exc:
            typer.secho(f"Compose down skipped: {exc}", fg=typer.colors.YELLOW, err=True)

    if mp and strip_traefik:
        try:
            m = load_effective_manifest(mp)
            rkeys, skeys = manifest_traefik_keys(m)
            if rkeys or skeys:
                rr, ss, bak = strip_traefik_dynamic_yml(tf, rkeys, skeys, dry_run=False)
                typer.secho(f"Traefik: removed {rr} router(s), {ss} service(s)" + (f" (backup {bak.name})" if bak else ""), fg=typer.colors.GREEN)
            else:
                typer.secho("Traefik: no routing-derived keys in manifest — skipped.", fg=typer.colors.YELLOW, err=True)
        except (OSError, ValueError) as exc:
            typer.secho(f"Traefik strip skipped: {exc}", fg=typer.colors.YELLOW, err=True)

    if unregister_from_ecosystem(eco, app_id):
        typer.secho(f"Unregistered {app_id}", fg=typer.colors.GREEN)
    else:
        typer.secho(f"No entry with id {app_id!r}", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(1)


@app.command("cf-deploy")
def cmd_cf_deploy(
    cwd: Path = Path("."),
    manifest: ManifestPathOption = None,
    env: Annotated[
        Optional[str],
        typer.Option("--env", "-e", help="wrangler --env (e.g. staging, production)"),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print wrangler command only")] = False,
    confirm_production: Annotated[
        bool,
        typer.Option(
            "--confirm-production",
            help="Required when deploying to production env",
        ),
    ] = False,
) -> None:
    """Run wrangler deploy using manifest cloudflare.wranglerConfig."""
    mp = _find_manifest(cwd, manifest)
    m = load_effective_manifest(mp)
    if not m.cloudflare or not m.cloudflare.wrangler_config:
        typer.secho("Manifest has no cloudflare.wranglerConfig.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    root = m.resolved_root(mp)
    cfg = Path(m.cloudflare.wrangler_config)
    cfg_path = (root / cfg) if not cfg.is_absolute() else cfg
    if not cfg_path.is_file():
        typer.secho(f"Wrangler config not found: {cfg_path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    wenv = env or m.cloudflare.wrangler_env
    if wenv and wenv.lower() in ("production", "prod") and not confirm_production:
        typer.secho(
            "Refusing production deploy without --confirm-production",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    wrangler = shutil.which("wrangler") or shutil.which("npx")
    cmd: list[str]
    if shutil.which("wrangler"):
        cmd = ["wrangler", "deploy", "-c", str(cfg_path)]
    else:
        cmd = ["npx", "--yes", "wrangler", "deploy", "-c", str(cfg_path)]

    if wenv:
        cmd.extend(["--env", wenv])

    if dry_run:
        typer.echo(" ".join(cmd))
        raise typer.Exit(0)

    typer.echo("Checking `wrangler whoami` …")
    who = subprocess.run(
        ["wrangler", "whoami"] if shutil.which("wrangler") else ["npx", "--yes", "wrangler", "whoami"],
        cwd=cfg_path.parent,
        capture_output=True,
        text=True,
    )
    if who.returncode != 0:
        typer.secho(who.stderr or who.stdout, fg=typer.colors.RED, err=True)
        typer.secho("Install wrangler and run `wrangler login`.", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(who.returncode)

    secret_keys = list_likely_secret_var_keys(root, Path(m.cloudflare.wrangler_config), wenv)
    if secret_keys:
        typer.secho(
            "Consider moving these to secrets (wrangler secret put <NAME> --env …):",
            fg=typer.colors.YELLOW,
        )
        for k in secret_keys:
            typer.echo(f"  - {k}")

    typer.echo("Running: " + " ".join(cmd))
    proc = subprocess.run(cmd, cwd=cfg_path.parent)
    raise typer.Exit(proc.returncode)


@app.command("cf-secrets-checklist")
def cmd_cf_secrets_checklist(
    cwd: Path = Path("."),
    manifest: ManifestPathOption = None,
    env: Annotated[Optional[str], typer.Option("--env", "-e")] = None,
) -> None:
    """List [vars] keys that look like secrets from wrangler.toml."""
    mp = _find_manifest(cwd, manifest)
    m = load_effective_manifest(mp)
    if not m.cloudflare or not m.cloudflare.wrangler_config:
        typer.secho("Manifest has no cloudflare.wranglerConfig.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    root = m.resolved_root(mp)
    wenv = env or m.cloudflare.wrangler_env
    keys = list_likely_secret_var_keys(root, Path(m.cloudflare.wrangler_config), wenv)
    if not keys:
        typer.echo("No heuristic secret-like [vars] keys found.")
        raise typer.Exit(0)
    for k in keys:
        typer.echo(k)
