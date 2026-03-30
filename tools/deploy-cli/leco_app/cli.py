"""leco-app CLI entrypoint."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Annotated, Optional

import typer

from leco_app import __version__
from leco_app.compose_runner import run_compose, run_compose_capture
from leco_app.detectors.compose import detect_compose
from leco_app.detectors.ports import check_host_ports
from leco_app.detectors.wrangler import detect_wrangler, list_likely_secret_var_keys
from leco_app.paths import app_state_dir, default_manifest_name
from leco_app.schema import (
    ApplicationManifest,
    CloudflareSpec,
    DockerComposeSpec,
    RoutingEntry,
    RoutingSpec,
    load_manifest,
    save_manifest,
)
from leco_app.traefik_fragment import manifest_to_traefik_yaml

app = typer.Typer(
    name="leco-app",
    help="Plug-and-play deploy helper: Docker Compose + optional Wrangler (see README resource model).",
    no_args_is_help=True,
)


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", name.strip()).strip("-").lower()
    return s or "app"


def _find_manifest(start: Path, explicit: Path | None) -> Path:
    if explicit:
        p = explicit.resolve()
        if not p.is_file():
            typer.secho(f"Manifest not found: {p}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        return p
    cur = start.resolve()
    for _ in range(20):
        cand = cur / default_manifest_name()
        if cand.is_file():
            return cand
        if cur.parent == cur:
            break
        cur = cur.parent
    typer.secho(
        f"No {default_manifest_name()} found (walk up from {start}). "
        "Run `leco-app init` or pass --manifest.",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(1)


@app.command("version")
def cmd_version() -> None:
    typer.echo(__version__)


@app.command("init")
def cmd_init(
    path: Annotated[Path, typer.Argument(help="Application root directory")] = Path("."),
    manifest_out: Annotated[
        Optional[Path],
        typer.Option("--out", "-o", help=f"Write manifest (default: <path>/{default_manifest_name()})"),
    ] = None,
    non_interactive: Annotated[bool, typer.Option("--yes", "-y", help="Skip prompts; use defaults")] = False,
) -> None:
    """Analyze the repo and write leco.app.yaml (interactive unless -y)."""
    root = path.resolve()
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
        entries: list[RoutingEntry] = []
        while True:
            hn = typer.prompt("Hostname (e.g. myapp.lh)", default="")
            if not hn.strip():
                break
            bh = typer.prompt("Backend Docker DNS name (container/service)", default="")
            bp = typer.prompt("Backend port", default=8080, type=int)
            entries.append(RoutingEntry(hostname=hn.strip(), backend_host=bh.strip(), backend_port=bp))
        if entries:
            routing = RoutingSpec(entries=entries)

    health_urls: list[str] = []
    if cd.host_ports and not non_interactive:
        if typer.confirm("Add a healthcheck URL on localhost?", default=False):
            hp = typer.prompt("Full URL", default=f"http://127.0.0.1:{cd.host_ports[0]}/")
            health_urls.append(hp)

    if not docker_spec and not cf_spec:
        typer.secho(
            "No docker-compose file or wrangler.toml found — nothing to configure.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    manifest = ApplicationManifest(
        leco_app_version="1",
        name=name,
        root=".",
        docker_compose=docker_spec,
        cloudflare=cf_spec,
        routing=routing,
        healthcheck_urls=health_urls,
    )

    out = manifest_out or (root / default_manifest_name())
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


@app.command("deploy")
def cmd_deploy(
    cwd: Annotated[Path, typer.Option("--cwd", help="Search manifest from this directory")] = Path("."),
    manifest: Annotated[
        Optional[Path],
        typer.Option("--manifest", "-f", help="Path to leco.app.yaml"),
    ] = None,
) -> None:
    """docker compose up -d --build"""
    mp = _find_manifest(cwd, manifest)
    m = load_manifest(mp)
    if not m.docker_compose:
        typer.secho("Manifest has no dockerCompose section.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
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
    raise typer.Exit(code)


@app.command("stop")
def cmd_stop(
    cwd: Path = Path("."),
    manifest: Optional[Path] = None,
) -> None:
    """docker compose stop"""
    mp = _find_manifest(cwd, manifest)
    m = load_manifest(mp)
    if not m.docker_compose:
        typer.secho("Manifest has no dockerCompose section.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    code = run_compose(m, mp, ["stop"])
    raise typer.Exit(code)


@app.command("down")
def cmd_down(
    cwd: Path = Path("."),
    manifest: Optional[Path] = None,
    volumes: Annotated[bool, typer.Option("--volumes", "-v", help="docker compose down -v")] = False,
) -> None:
    """docker compose down"""
    mp = _find_manifest(cwd, manifest)
    m = load_manifest(mp)
    if not m.docker_compose:
        typer.secho("Manifest has no dockerCompose section.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    args = ["down", "--remove-orphans"]
    if volumes:
        args.append("-v")
    code = run_compose(m, mp, args)
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
    m = load_manifest(mp)
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
    manifest: Optional[Path] = None,
) -> None:
    """docker compose ps and optional HTTP health checks"""
    mp = _find_manifest(cwd, manifest)
    m = load_manifest(mp)
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
    manifest: Optional[Path] = None,
    out: Annotated[Optional[Path], typer.Option("--out", "-o", help="Write YAML fragment")] = None,
) -> None:
    """Print YAML to paste into traefik/dynamic.yml (no auto-merge)."""
    mp = _find_manifest(cwd, manifest)
    m = load_manifest(mp)
    text = manifest_to_traefik_yaml(m)
    if out:
        out.write_text(text, encoding="utf-8")
        typer.secho(f"Wrote {out}", fg=typer.colors.GREEN)
    else:
        typer.echo(text)


@app.command("cf-deploy")
def cmd_cf_deploy(
    cwd: Path = Path("."),
    manifest: Optional[Path] = None,
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
    m = load_manifest(mp)
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
    manifest: Optional[Path] = None,
    env: Annotated[Optional[str], typer.Option("--env", "-e")] = None,
) -> None:
    """List [vars] keys that look like secrets from wrangler.toml."""
    mp = _find_manifest(cwd, manifest)
    m = load_manifest(mp)
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
