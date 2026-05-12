"""Register via LEco DevOps (leco-app ecosystem-register); YAML is materialized separately."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import yaml

from hosting_layout import (
    HOSTING_SOURCE_LINK_NAME,
    hosting_manifest_logical_path,
    hosting_staging_dir,
    is_dir_writable,
    registry_manifest_relpath,
    refresh_symlink,
)
from leco_detect import (
    compute_hosting_source_symlink_target,
    ensure_lh_network_hosting_overlay,
    host_slug_from_app_id,
    normalize_profile_compose_backend_hosts,
    require_registration_app_id,
    resolve_registration_path,
    slugify_app_id,
)
from leco_materialize import registration_yaml_status
from leco_subprocess import (
    PROJECT_ROOT,
    iter_ecosystem_register,
    iter_leco_deploy,
    run_ecosystem_register,
    run_leco_deploy,
)

_YAML_DUMP_KW: dict[str, Any] = {
    "default_flow_style": False,
    "sort_keys": False,
    "allow_unicode": True,
}


@dataclass(frozen=True)
class RegisterPrepared:
    """Paths and ids ready for ecosystem-register."""

    manifest_abs: Path
    app_id: str
    display: str
    registry_manifest_relpath: str | None
    materialized: bool
    app_root: str
    manifest_path_str: str
    localhost_path_str: str
    hosting_staging: str | None = None
    source_symlink_target: str | None = None


def effective_manifest_has_docker_compose(manifest_abs: Path) -> bool:
    """Whether ``leco-app deploy`` would run compose (merged bridge + localhost profile)."""
    try:
        from leco_app.schema import load_effective_manifest

        m = load_effective_manifest(manifest_abs.resolve())
        return m.docker_compose is not None
    except Exception:
        return False


def effective_manifest_has_traefik_sources(manifest_abs: Path) -> bool:
    """Whether ecosystem-register would merge Traefik fragments (routing.entries or local CF prefix)."""
    try:
        from leco_app.schema import load_effective_manifest

        m = load_effective_manifest(manifest_abs.resolve())
        if m.routing and m.routing.entries:
            return True
        cf = m.cloudflare
        if cf and cf.local_cf_public_prefix and str(cf.local_cf_public_prefix).strip():
            return True
        return False
    except Exception:
        return False


def register_infrastructure_gaps(manifest_abs: Path) -> list[str]:
    """Human-readable gaps when registry succeeds but no compose / Traefik inputs exist."""
    gaps: list[str] = []
    if not effective_manifest_has_docker_compose(manifest_abs):
        gaps.append(
            "No infrastructure.dockerCompose in leco.yaml — no compose project for this app "
            "(hosted-apps services list and post-register deploy stay empty)."
        )
    if not effective_manifest_has_traefik_sources(manifest_abs):
        gaps.append(
            "No infrastructure.routing.entries and no cloudflare.localCfPublicPrefix — "
            "Traefik merge skipped (no *.lh routes added)."
        )
    return gaps


def effective_manifest_url_summary(manifest_abs: Path) -> dict[str, Any]:
    """Main URL + source metadata from merged manifest/profile with derived fallback."""
    out: dict[str, Any] = {
        "main_url": "",
        "main_url_source": "",
        "derived_main_url": "",
        "main_urls": {},
        "derived_main_urls": {},
        "explicit_urls": [],
        "route_hosts": [],
    }
    def _dual_scheme_urls(url: str) -> dict[str, str]:
        u = (url or "").strip()
        if not u:
            return {}
        try:
            p = urlsplit(u)
        except Exception:
            return {}
        if not p.netloc:
            return {}
        https_u = urlunsplit(("https", p.netloc, p.path, p.query, p.fragment))
        http_u = urlunsplit(("http", p.netloc, p.path, p.query, p.fragment))
        return {"https": https_u, "http": http_u}
    try:
        from leco_app.schema import load_merged_manifest

        merged = load_merged_manifest(manifest_abs.resolve())
        m = merged.manifest
        host_slug = host_slug_from_app_id(m.name or "app")
        derived_main = f"https://{host_slug}.lh"
        out["derived_main_url"] = derived_main
        out["derived_main_urls"] = {"https": f"https://{host_slug}.lh", "http": f"http://{host_slug}.lh"}

        explicit_urls: list[dict[str, str]] = []
        frontend_url = ""
        for u in merged.localhost.urls or []:
            pu = (u.public_url or "").strip()
            if not pu:
                continue
            role = str(u.role or "other")
            label = (u.label or "").strip()
            explicit_urls.append({"role": role, "label": label, "public_url": pu})
            if not frontend_url and role == "frontend":
                frontend_url = pu
        out["explicit_urls"] = explicit_urls

        route_hosts: list[str] = []
        if m.routing and m.routing.entries:
            for e in m.routing.entries:
                h = str(e.hostname or "").strip()
                if h:
                    route_hosts.append(h)
        out["route_hosts"] = route_hosts

        if frontend_url:
            out["main_url"] = frontend_url
            out["main_url_source"] = "localhost.urls.frontend"
        elif explicit_urls:
            out["main_url"] = explicit_urls[0]["public_url"]
            out["main_url_source"] = "localhost.urls"
        elif route_hosts:
            out["main_url"] = f"https://{route_hosts[0]}"
            out["main_url_source"] = "routing.entries"
        else:
            out["main_url"] = derived_main
            out["main_url_source"] = "derived_slug"
        out["main_urls"] = _dual_scheme_urls(out["main_url"])
    except Exception:
        pass
    return out


def _register_result_dict(
    prep: RegisterPrepared,
    log: str,
    *,
    deploy_code: int | None = None,
    deploy_log: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": True,
        "app_root": prep.app_root,
        "manifest_path": prep.manifest_path_str,
        "localhost_path": prep.localhost_path_str,
        "registry_entry": {"id": prep.app_id, "label": prep.display},
        "leco_register_log": log[-8000:] if log else "",
        "materialized": prep.materialized,
        "deploy_stack_ran": deploy_code is not None,
    }
    if prep.materialized:
        out["hosting_staging"] = prep.hosting_staging or ""
        out["registry_manifest_relpath"] = prep.registry_manifest_relpath or ""
        out["source_symlink_target"] = prep.source_symlink_target or ""
    out["effective_has_docker_compose"] = effective_manifest_has_docker_compose(prep.manifest_abs)
    out["effective_has_traefik_sources"] = effective_manifest_has_traefik_sources(prep.manifest_abs)
    ig = register_infrastructure_gaps(prep.manifest_abs)
    if ig:
        out["register_infrastructure_gaps"] = ig
    out["url_sources"] = effective_manifest_url_summary(prep.manifest_abs)
    out["main_url"] = str((out["url_sources"] or {}).get("main_url") or "")
    if deploy_code is not None:
        out["deploy_exit_code"] = deploy_code
        out["deploy_ok"] = deploy_code == 0
        out["deploy_log"] = (deploy_log or "")[-12000:]
    return out


def _apply_register_url_overrides(prep: RegisterPrepared, url_overrides: list[dict[str, Any]] | None) -> None:
    """Apply operator-confirmed localhost URLs before ecosystem-register."""
    rows = url_overrides if isinstance(url_overrides, list) else []
    if not rows:
        return
    normalized: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "other").strip() or "other"
        label = str(row.get("label") or "").strip()
        public_url = str(row.get("public_url") or row.get("publicUrl") or "").strip()
        if not public_url:
            continue
        normalized.append({"role": role, "label": label, "publicUrl": public_url})
    if not normalized:
        return
    profile_path = Path(prep.localhost_path_str).resolve()
    try:
        raw = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Cannot read localhost profile for URL overrides: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("localhost profile must be a YAML mapping to apply URL overrides.")
    raw["urls"] = normalized
    try:
        from leco_app.schema import LocalhostProfile

        LocalhostProfile.model_validate(raw)
    except Exception as exc:
        raise ValueError(f"localhost URL override validation failed: {exc}") from exc
    try:
        profile_path.write_text(yaml.safe_dump(raw, **_YAML_DUMP_KW), encoding="utf-8")
    except OSError as exc:
        raise OSError(f"Failed writing localhost profile URL overrides: {exc}") from exc


def prepare_register_from_disk(path_rel: str, app_id: str, label: str) -> RegisterPrepared:
    """Require leco.app.yaml + localhost profile on disk (or under hosting staging when read-only)."""
    orig_root = resolve_registration_path(path_rel)
    aid = require_registration_app_id(app_id)
    display = (label or "").strip() or aid
    eco = Path(PROJECT_ROOT).resolve()

    st = registration_yaml_status(path_rel, aid)
    if not st.get("registration_ready"):
        raise ValueError(
            "Registration YAML is not ready — click **Generate YAML** (or **Save YAML**) so "
            "`leco.app.yaml` and the localhost profile file (e.g. `leco.yaml`) exist."
        )

    writable = is_dir_writable(orig_root)
    if writable:
        man_path = Path(st["manifest_path"])
        loc_path = Path(st["localhost_path"])
        return RegisterPrepared(
            manifest_abs=man_path,
            app_id=aid,
            display=display,
            registry_manifest_relpath=None,
            materialized=False,
            app_root=str(orig_root.resolve()),
            manifest_path_str=str(man_path.resolve()),
            localhost_path_str=str(loc_path.resolve()),
        )

    staging = hosting_staging_dir(eco, aid)
    man_st = staging / "leco.app.yaml"
    raw = man_st.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Materialized leco.app.yaml must be a YAML mapping")
    tree_root = compute_hosting_source_symlink_target(orig_root, parsed)
    refresh_symlink(staging / HOSTING_SOURCE_LINK_NAME, tree_root, target_is_dir=True)
    logical_man = hosting_manifest_logical_path(eco, aid)
    reg_rel = registry_manifest_relpath(aid)
    return RegisterPrepared(
        manifest_abs=logical_man,
        app_id=aid,
        display=display,
        registry_manifest_relpath=reg_rel,
        materialized=True,
        app_root=str(staging.resolve()),
        manifest_path_str=str(logical_man.resolve()),
        localhost_path_str=str(st["localhost_path"]),
        hosting_staging=str(staging.resolve()),
        source_symlink_target=str(tree_root),
    )


def register_app_wizard(
    path_rel: str,
    app_id: str,
    label: str,
    *,
    url_overrides: list[dict[str, Any]] | None = None,
    deploy_stack: bool = False,
) -> dict[str, Any]:
    """
    Run ``leco-app ecosystem-register`` using YAML already on disk.
    Use :mod:`leco_materialize` to generate or save files first.
    """
    prep = prepare_register_from_disk(path_rel, app_id, label)
    _apply_register_url_overrides(prep, url_overrides)
    normalize_profile_compose_backend_hosts(prep.manifest_abs)
    ensure_lh_network_hosting_overlay(prep.manifest_abs)
    code, log = run_ecosystem_register(
        prep.manifest_abs,
        app_id=prep.app_id,
        label=prep.display,
        timeout=300,
        registry_manifest_relpath=prep.registry_manifest_relpath,
    )
    if code != 0:
        raise OSError(log[-4000:] if log else f"leco-app ecosystem-register failed (exit {code})")

    if deploy_stack:
        if not effective_manifest_has_docker_compose(prep.manifest_abs):
            skip = (
                "\n--- post-register deploy ---\n"
                "Skipped: effective manifest has no dockerCompose section "
                "(Workers-only / Wrangler apps have no compose to run). "
                "Use `wrangler dev` / `wrangler deploy` for this stack, or add "
                "`infrastructure.dockerCompose` in leco.yaml if you add sidecar containers.\n"
            )
            return _register_result_dict(prep, (log or "") + skip)
        dcode, dlog = run_leco_deploy(prep.manifest_abs)
        return _register_result_dict(prep, log, deploy_code=dcode, deploy_log=dlog)

    return _register_result_dict(prep, log)


def iterate_register_app_wizard(
    path_rel: str,
    app_id: str,
    label: str,
    *,
    url_overrides: list[dict[str, Any]] | None = None,
    deploy_stack: bool = False,
) -> Iterator[dict[str, Any]]:
    """NDJSON-style events for the dashboard."""
    yield {"type": "log", "text": "Checking leco.app.yaml + localhost profile on disk…\n"}
    try:
        prep = prepare_register_from_disk(path_rel, app_id, label)
        _apply_register_url_overrides(prep, url_overrides)
        if isinstance(url_overrides, list) and url_overrides:
            yield {
                "type": "log",
                "text": f"Applied URL configuration ({len(url_overrides)} entr{'y' if len(url_overrides) == 1 else 'ies'}) to localhost profile.\n",
            }
        fix = normalize_profile_compose_backend_hosts(prep.manifest_abs)
        if (fix.get("updated") or 0) > 0:
            yield {
                "type": "log",
                "text": f"Normalized routing backend hosts for isolation ({fix.get('updated')} entry/entries).\n",
            }
        overlay = ensure_lh_network_hosting_overlay(prep.manifest_abs)
        overlay_svcs = overlay.get("services") or []
        reset_svcs = overlay.get("ports_reset_services") or []
        if overlay_svcs or reset_svcs:
            net_txt = (
                f"attach to lh-network for {', '.join(overlay_svcs)}"
                if isinstance(overlay_svcs, list) and overlay_svcs
                else ""
            )
            reset_txt = (
                f"reset upstream host ports for {', '.join(reset_svcs)}"
                if isinstance(reset_svcs, list) and reset_svcs
                else ""
            )
            detail = "; ".join(x for x in (net_txt, reset_txt) if x)
            yield {
                "type": "log",
                "text": (
                    "Ensured hosting overlay: added or updated docker-compose.leco-hosting.yml "
                    f"({detail}).\n"
                ),
            }
    except (ValueError, OSError, yaml.YAMLError) as exc:
        yield {"type": "done", "result": {"ok": False, "error": str(exc)}}
        return

    yield {"type": "log", "text": "Running leco-app ecosystem-register (merge Traefik, local CF if enabled)…\n"}

    combined: list[str] = []
    exit_code = 0
    try:
        for kind, payload in iter_ecosystem_register(
            prep.manifest_abs,
            app_id=prep.app_id,
            label=prep.display,
            timeout=300,
            registry_manifest_relpath=prep.registry_manifest_relpath,
        ):
            if kind == "line":
                combined.append(str(payload))
                yield {"type": "log", "text": str(payload)}
            elif kind == "end":
                exit_code = int(payload)
    except OSError as exc:
        yield {"type": "done", "result": {"ok": False, "error": str(exc)}}
        return

    log = "".join(combined)
    if exit_code != 0:
        err = log[-4000:] if log else f"leco-app ecosystem-register failed (exit {exit_code})"
        yield {"type": "done", "result": {"ok": False, "error": err}}
        return

    if not deploy_stack:
        yield {"type": "done", "result": _register_result_dict(prep, log)}
        return

    if not effective_manifest_has_docker_compose(prep.manifest_abs):
        skip = (
            "\n--- post-register deploy ---\n"
            "Skipped: no dockerCompose in effective manifest (Workers-only — no `leco-app deploy`).\n"
        )
        yield {"type": "log", "text": skip}
        yield {"type": "done", "result": _register_result_dict(prep, log + skip)}
        return

    yield {"type": "log", "text": "\n--- leco-app deploy (docker compose up) ---\n"}
    dcombined: list[str] = []
    dcode = 0
    try:
        for kind, payload in iter_leco_deploy(prep.manifest_abs):
            if kind == "line":
                dcombined.append(str(payload))
                yield {"type": "log", "text": str(payload)}
            elif kind == "end":
                dcode = int(payload)
    except OSError as exc:
        yield {"type": "done", "result": {"ok": False, "error": str(exc)}}
        return

    dlog = "".join(dcombined)
    yield {
        "type": "done",
        "result": _register_result_dict(prep, log, deploy_code=dcode, deploy_log=dlog),
    }
