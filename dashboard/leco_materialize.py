"""Generate / save registration YAML on disk (before ecosystem-register)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from hosting_layout import (
    HOSTING_SOURCE_LINK_NAME,
    compute_source_target,
    hosting_manifest_logical_path,
    hosting_staging_dir,
    is_dir_writable,
    patch_manifest_root_for_hosting,
    refresh_symlink,
    registry_manifest_relpath,
    sync_hosting_config_ref_symlinks,
)
from leco_detect import (
    build_default_manifest_and_localhost,
    compute_hosting_source_symlink_target,
    enrich_infrastructure_wrangler_binding_preview,
    ensure_docker_compose_in_manifest,
    ensure_docker_compose_in_profile_infrastructure,
    ensure_wrangler_in_manifest,
    ensure_wrangler_in_profile_infrastructure,
    fill_resolved_paths_in_manifest,
    host_slug_from_app_id,
    registration_scan_root,
    require_registration_app_id,
    resolve_registration_path,
    slugify_app_id,
)
from leco_subprocess import PROJECT_ROOT
from leco_app.schema import ApplicationManifest, LocalhostProfile

_YAML_DUMP_KW: dict[str, Any] = {
    "default_flow_style": False,
    "sort_keys": False,
    "allow_unicode": True,
}

try:
    from leco_app.compose_runner import path_for_docker_daemon
except ImportError:

    def path_for_docker_daemon(container_path: Path) -> Path:  # type: ignore[misc]
        try:
            return container_path.resolve()
        except OSError:
            return container_path


def _profile_relpath(manifest_dict: dict[str, Any]) -> str:
    prof = manifest_dict.get("localHostProfile") or manifest_dict.get("local_host_profile") or "leco.yaml"
    if not isinstance(prof, str) or not prof.strip():
        return "leco.yaml"
    return prof.strip()


def _profile_docker_compose_dict(profile: dict[str, Any]) -> dict[str, Any] | None:
    infra = profile.get("infrastructure")
    if not isinstance(infra, dict):
        return None
    dc = infra.get("dockerCompose") or infra.get("docker_compose")
    if not isinstance(dc, dict):
        return None
    cf = (dc.get("composeFile") or dc.get("compose_file") or "").strip()
    if not cf:
        return None
    return dc


def _is_workers_only_routing(entries: Any) -> bool:
    if not isinstance(entries, list) or not entries:
        return False
    rows = [e for e in entries if isinstance(e, dict)]
    if not rows:
        return False
    if all(str(e.get("backendHost") or "").strip() == "workers-runtime" for e in rows):
        return True
    return all(
        isinstance(e.get("frontend"), dict)
        and isinstance(e.get("apiBackend"), dict)
        and str(e["frontend"].get("host") or "").strip() == "workers-runtime"
        and str(e["apiBackend"].get("host") or "").strip() == "workers-runtime"
        for e in rows
    )


def _preserve_hosting_compose_state(
    staging: Path,
    app_id: str,
    manifest: dict[str, Any],
    localhost: dict[str, Any],
) -> None:
    """
    Guard against accidental compose downgrade when re-generating from a sub-path
    that only exposes Wrangler signals. If the hosted app already had compose, keep it.
    """
    if not isinstance(manifest, dict) or not isinstance(localhost, dict):
        return
    infra = localhost.get("infrastructure")
    if not isinstance(infra, dict):
        infra = {}
        localhost["infrastructure"] = infra

    existing_manifest_path = staging / "leco.app.yaml"
    existing_manifest: dict[str, Any] = {}
    existing_profile: dict[str, Any] = {}
    if existing_manifest_path.is_file():
        try:
            raw = yaml.safe_load(existing_manifest_path.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                existing_manifest = raw
                p = (raw.get("localHostProfile") or "leco.yaml").strip() or "leco.yaml"
                ex_prof = staging / p
                if ex_prof.is_file():
                    lp = yaml.safe_load(ex_prof.read_text(encoding="utf-8")) or {}
                    if isinstance(lp, dict):
                        existing_profile = lp
        except (OSError, yaml.YAMLError):
            pass

    current_dc = _profile_docker_compose_dict(localhost)
    existing_dc = _profile_docker_compose_dict(existing_profile)
    hosted_compose = staging / "docker-compose.yml"
    hosted_compose_path = f"/project/hosting/app-available/{app_id}/docker-compose.yml"

    if current_dc is None:
        if existing_dc is not None:
            kept = dict(existing_dc)
            if not (kept.get("projectName") or kept.get("project_name")):
                kept["projectName"] = app_id
            infra["dockerCompose"] = kept
            current_dc = kept
        elif hosted_compose.is_file():
            infra["dockerCompose"] = {"composeFile": hosted_compose_path, "projectName": app_id}
            current_dc = infra["dockerCompose"]

    if current_dc is None:
        return

    source_base: Path | None = None
    src_link = staging / HOSTING_SOURCE_LINK_NAME
    if src_link.exists() or src_link.is_symlink():
        try:
            source_base = src_link.resolve()
        except OSError:
            source_base = None

    def _path_exists_from_source(raw: Any) -> bool:
        if not source_base or not isinstance(raw, str) or not raw.strip():
            return False
        s = raw.strip()
        p = Path(s)
        q = p.resolve() if p.is_absolute() else (source_base / p).resolve()
        return q.is_file()

    def _prefer_existing_wrangler(current: Any, existing: Any) -> Any:
        if _path_exists_from_source(existing) and not _path_exists_from_source(current):
            return existing
        return current

    ex_cfg_all = existing_manifest.get("configRefs")
    ex_cfg = ex_cfg_all if isinstance(ex_cfg_all, dict) else {}
    cfg = manifest.get("configRefs")
    if not isinstance(cfg, dict):
        cfg = {}
        manifest["configRefs"] = cfg
    cfg["wranglerConfig"] = _prefer_existing_wrangler(cfg.get("wranglerConfig"), ex_cfg.get("wranglerConfig"))

    mcf = manifest.get("cloudflare")
    if not isinstance(mcf, dict):
        mcf = {}
        manifest["cloudflare"] = mcf
    ex_mcf = existing_manifest.get("cloudflare")
    ex_mcf_d = ex_mcf if isinstance(ex_mcf, dict) else {}
    mcf["wranglerConfig"] = _prefer_existing_wrangler(mcf.get("wranglerConfig"), ex_mcf_d.get("wranglerConfig"))

    cf = infra.get("cloudflare")
    if not isinstance(cf, dict):
        cf = {}
        infra["cloudflare"] = cf
    ex_cf = None
    ex_infra = existing_profile.get("infrastructure")
    if isinstance(ex_infra, dict):
        ex_cf = ex_infra.get("cloudflare")
    ex_cf_d = ex_cf if isinstance(ex_cf, dict) else {}
    cf["wranglerConfig"] = _prefer_existing_wrangler(cf.get("wranglerConfig"), ex_cf_d.get("wranglerConfig"))

    if not (current_dc.get("projectName") or current_dc.get("project_name")):
        current_dc["projectName"] = app_id

    if not (cfg.get("dockerComposeFile") or "").strip():
        ex_cf = ""
        if isinstance(ex_cfg_all, dict):
            ex_cf = (ex_cfg.get("dockerComposeFile") or "").strip()
        cfg["dockerComposeFile"] = ex_cf or (hosted_compose_path if hosted_compose.is_file() else str(current_dc.get("composeFile") or "").strip())

    existing_entries = None
    ex_infra = existing_profile.get("infrastructure")
    if isinstance(ex_infra, dict):
        ex_rt = ex_infra.get("routing")
        if isinstance(ex_rt, dict):
            ex_entries = ex_rt.get("entries")
            if isinstance(ex_entries, list) and ex_entries:
                existing_entries = ex_entries
    curr_rt = infra.get("routing")
    curr_entries = curr_rt.get("entries") if isinstance(curr_rt, dict) else None
    if existing_entries and (not isinstance(curr_entries, list) or not curr_entries or _is_workers_only_routing(curr_entries)):
        infra["routing"] = {"entries": existing_entries}

    ex_runtimes = None
    if isinstance(ex_infra, dict):
        raw_rt = ex_infra.get("runtimes")
        if isinstance(raw_rt, list) and raw_rt:
            ex_runtimes = raw_rt
    curr_runtimes = infra.get("runtimes")
    if ex_runtimes and (not isinstance(curr_runtimes, list) or not curr_runtimes):
        infra["runtimes"] = ex_runtimes


def _staging_existing_profile_has_compose(staging: Path) -> bool:
    man = staging / "leco.app.yaml"
    if not man.is_file():
        return False
    try:
        md = yaml.safe_load(man.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return False
    if not isinstance(md, dict):
        return False
    prof = (md.get("localHostProfile") or "leco.yaml").strip() or "leco.yaml"
    lp = staging / prof
    if not lp.is_file():
        return False
    try:
        ld = yaml.safe_load(lp.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return False
    if not isinstance(ld, dict):
        return False
    return _profile_docker_compose_dict(ld) is not None


def _maybe_write_hosted_compose_scaffold(
    staging: Path,
    app_id: str,
    source_target: Path,
    manifest: dict[str, Any],
    localhost: dict[str, Any],
) -> bool:
    """
    Generic fallback for mixed app roots without a compose file:
    if both ``frontend/`` and ``backend/`` exist, scaffold compose under hosting staging.
    """
    if _profile_docker_compose_dict(localhost) is not None:
        return False
    src = source_target.resolve()
    if not (src / "frontend").is_dir() or not (src / "backend").is_dir():
        return False
    back_host = path_for_docker_daemon(src / "backend")
    front_host = path_for_docker_daemon(src / "frontend")
    host_slug = host_slug_from_app_id(app_id)
    compose_path = staging / "docker-compose.yml"
    compose_text = (
        "services:\n"
        "  mongodb:\n"
        "    image: mongo:7\n"
        "    restart: unless-stopped\n"
        "    volumes:\n"
        "      - hosted_mongo_data:/data/db\n"
        "    networks:\n"
        "      - app-network\n"
        "\n"
        "  backend:\n"
        "    image: python:3.11-slim\n"
        "    restart: unless-stopped\n"
        "    working_dir: /work/backend\n"
        "    command: >\n"
        "      sh -lc \"mkdir -p /work/backend && (cp -a /src/backend/. /work/backend/ || true) &&\n"
        "      cd /work/backend && (pip install --no-cache-dir fastapi uvicorn python-dotenv pymongo python-multipart motor || true) &&\n"
        "      (uvicorn server:app --host 0.0.0.0 --port 8001 || python -m http.server 8001)\"\n"
        f"    volumes:\n      - {str(back_host)}:/src/backend:ro\n"
        "    environment:\n"
        "      MONGO_URL: mongodb://mongodb:27017\n"
        "      DB_NAME: app\n"
        f"      CORS_ORIGINS: \"http://{host_slug}.lh,https://{host_slug}.lh,http://{host_slug}.lh/api,https://{host_slug}.lh/api,http://localhost:3000\"\n"
        "    depends_on:\n"
        "      - mongodb\n"
        "    networks:\n"
        "      - app-network\n"
        "      - lh-network\n"
        "\n"
        "  frontend:\n"
        "    image: node:20-alpine\n"
        "    restart: unless-stopped\n"
        "    working_dir: /src/frontend\n"
        "    command: >\n"
        "      sh -lc \"npx --yes http-server /src/frontend/public -p 3000 || npx --yes http-server /src/frontend -p 3000\"\n"
        f"    volumes:\n      - {str(front_host)}:/src/frontend:ro\n"
        "    environment:\n"
        "      HOST: 0.0.0.0\n"
        "      PORT: 3000\n"
        "      WDS_SOCKET_PORT: \"443\"\n"
        f"      REACT_APP_BACKEND_URL: https://{host_slug}.lh/api\n"
        "      ENABLE_HEALTH_CHECK: \"false\"\n"
        "    depends_on:\n"
        "      - backend\n"
        "    networks:\n"
        "      - app-network\n"
        "      - lh-network\n"
        "\n"
        "volumes:\n"
        "  hosted_mongo_data:\n"
        "\n"
        "networks:\n"
        "  app-network:\n"
        "    driver: bridge\n"
        "  lh-network:\n"
        "    external: true\n"
    )
    compose_path.write_text(compose_text, encoding="utf-8")
    infra = localhost.get("infrastructure")
    if not isinstance(infra, dict):
        infra = {}
        localhost["infrastructure"] = infra
    compose_ref = f"/project/hosting/app-available/{app_id}/docker-compose.yml"
    infra["dockerCompose"] = {"composeFile": compose_ref, "projectName": app_id}
    rt = infra.get("routing")
    entries = rt.get("entries") if isinstance(rt, dict) else None
    if not isinstance(entries, list) or not entries or _is_workers_only_routing(entries):
        infra["routing"] = {
            "entries": [
                {
                    "hostname": f"{host_slug}.lh",
                    "apiPathPrefix": "/api",
                    "frontend": {"host": f"{app_id}-frontend-1", "port": 3000},
                    "apiBackend": {"host": f"{app_id}-backend-1", "port": 8001},
                }
            ]
        }
    cfg = manifest.get("configRefs")
    if not isinstance(cfg, dict):
        cfg = {}
        manifest["configRefs"] = cfg
    cfg["dockerComposeFile"] = compose_ref
    return True


def _maybe_write_static_site_compose_scaffold(
    staging: Path,
    app_id: str,
    source_target: Path,
    manifest: dict[str, Any],
    localhost: dict[str, Any],
) -> bool:
    """Generic static-site fallback: serve app root via nginx when compose is absent."""
    if _profile_docker_compose_dict(localhost) is not None:
        return False
    src = source_target.resolve()
    if not (src / "index.html").is_file():
        return False
    src_host = path_for_docker_daemon(src)
    compose_path = staging / "docker-compose.yml"
    host_slug = host_slug_from_app_id(app_id)
    compose_text = (
        "services:\n"
        "  frontend:\n"
        "    image: nginx:alpine\n"
        "    restart: unless-stopped\n"
        f"    volumes:\n      - {str(src_host)}:/usr/share/nginx/html:ro\n"
        "    networks:\n"
        "      - lh-network\n"
        "\n"
        "networks:\n"
        "  lh-network:\n"
        "    external: true\n"
    )
    compose_path.write_text(compose_text, encoding="utf-8")
    infra = localhost.get("infrastructure")
    if not isinstance(infra, dict):
        infra = {}
        localhost["infrastructure"] = infra
    compose_ref = f"/project/hosting/app-available/{app_id}/docker-compose.yml"
    infra["dockerCompose"] = {"composeFile": compose_ref, "projectName": app_id}
    rt = infra.get("routing")
    entries = rt.get("entries") if isinstance(rt, dict) else None
    if not isinstance(entries, list) or not entries or _is_workers_only_routing(entries):
        infra["routing"] = {
            "entries": [
                {"hostname": f"{host_slug}.lh", "backendHost": f"{app_id}-frontend-1", "backendPort": 80},
            ]
        }
    cfg = manifest.get("configRefs")
    if not isinstance(cfg, dict):
        cfg = {}
        manifest["configRefs"] = cfg
    cfg["dockerComposeFile"] = compose_ref
    return True


def registration_yaml_status(path_rel: str, app_id: str | None = None) -> dict[str, Any]:
    """Whether leco.app.yaml + localhost profile exist (app root or hosting staging)."""
    orig_root = resolve_registration_path(path_rel)
    eco = Path(PROJECT_ROOT).resolve()
    writable = is_dir_writable(orig_root)
    raw_id = (app_id or "").strip()

    if writable:
        man_path = orig_root / "leco.app.yaml"
        manifest_exists = man_path.is_file()
        profile = "leco.yaml"
        if manifest_exists:
            try:
                data = yaml.safe_load(man_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    p = data.get("localHostProfile") or data.get("local_host_profile")
                    if isinstance(p, str) and p.strip():
                        profile = p.strip()
            except (OSError, yaml.YAMLError):
                pass
        loc_path = orig_root / profile
        return {
            "writable": True,
            "materialized": False,
            "manifest_exists": manifest_exists,
            "localhost_profile": profile,
            "localhost_exists": loc_path.is_file(),
            "registration_ready": manifest_exists and loc_path.is_file(),
            "manifest_path": str(man_path.resolve()),
            "localhost_path": str(loc_path.resolve()),
        }

    if not raw_id:
        return {
            "writable": False,
            "materialized": True,
            "manifest_exists": False,
            "localhost_profile": "leco.yaml",
            "localhost_exists": False,
            "registration_ready": False,
            "manifest_path": "",
            "localhost_path": "",
            "needs_app_id_for_staging": True,
        }

    aid = slugify_app_id(raw_id)
    staging = hosting_staging_dir(eco, aid)
    man_path = staging / "leco.app.yaml"
    manifest_exists = man_path.is_file()
    profile = "leco.yaml"
    if manifest_exists:
        try:
            data = yaml.safe_load(man_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                p = data.get("localHostProfile") or data.get("local_host_profile")
                if isinstance(p, str) and p.strip():
                    profile = p.strip()
        except (OSError, yaml.YAMLError):
            pass
    loc_path = (staging / profile).resolve()
    localhost_exists = loc_path.is_file()
    logical_man = hosting_manifest_logical_path(eco, aid)
    return {
        "writable": False,
        "materialized": True,
        "manifest_exists": manifest_exists,
        "localhost_profile": profile,
        "localhost_exists": localhost_exists,
        "registration_ready": manifest_exists and localhost_exists,
        "manifest_path": str(man_path.resolve()),
        "localhost_path": str(loc_path),
        "logical_manifest_path": str(logical_man.resolve()),
        "registry_manifest_relpath": registry_manifest_relpath(aid),
    }


def materialize_registration_yaml(path_rel: str, app_id: str) -> dict[str, Any]:
    """
    Scan app root (compose, wrangler, archetype) and write ``leco.app.yaml`` + localhost profile.
    Overwrites existing files. Writable roots write into the app directory; read-only roots write
    under ``hosting/app-available/<id>/``, refresh the ``source`` symlink, and add symlinks for
    paths in ``configRefs`` plus compose / env / wrangler from the profile (when targets exist).
    """
    orig_root = resolve_registration_path(path_rel)
    aid = require_registration_app_id(app_id)
    eco = Path(PROJECT_ROOT).resolve()
    scan_root = registration_scan_root(orig_root)

    m, lo = build_default_manifest_and_localhost(scan_root, aid)
    ensure_wrangler_in_manifest(m, scan_root)
    if scan_root != orig_root:
        try:
            rel_root = os.path.relpath(str(scan_root.resolve()), str(orig_root.resolve())).replace("\\", "/")
        except ValueError:
            rel_root = ""
        if rel_root and rel_root != ".":
            m["root"] = rel_root

    if is_dir_writable(orig_root):
        ensure_docker_compose_in_profile_infrastructure(
            lo, scan_root, m, app_tree_base=scan_root, allow_compose_discovery=True
        )
        ensure_wrangler_in_profile_infrastructure(lo, scan_root, m, app_tree_base=scan_root)
        enrich_infrastructure_wrangler_binding_preview(lo.get("infrastructure") or {}, scan_root)
        man_path = orig_root / "leco.app.yaml"
        prof = _profile_relpath(m)
        loc_path = orig_root / prof
        man_path.parent.mkdir(parents=True, exist_ok=True)
        fill_resolved_paths_in_manifest(m, man_path, lo)
        man_path.write_text(yaml.safe_dump(m, **_YAML_DUMP_KW), encoding="utf-8")
        loc_path.write_text(yaml.safe_dump(lo, **_YAML_DUMP_KW), encoding="utf-8")
        return {
            "ok": True,
            "materialized": False,
            "manifest_path": str(man_path.resolve()),
            "localhost_path": str(loc_path.resolve()),
            "manifest_yaml": man_path.read_text(encoding="utf-8"),
            "localhost_yaml": loc_path.read_text(encoding="utf-8"),
        }

    staging = hosting_staging_dir(eco, aid)
    staging.mkdir(parents=True, exist_ok=True)
    man_path = staging / "leco.app.yaml"
    source_target = compute_hosting_source_symlink_target(scan_root, m)
    existing_src = staging / HOSTING_SOURCE_LINK_NAME
    if _staging_existing_profile_has_compose(staging) and (existing_src.exists() or existing_src.is_symlink()):
        try:
            existing_target = existing_src.resolve()
            if source_target != existing_target and source_target.is_relative_to(existing_target):
                source_target = existing_target
        except OSError:
            pass
    ensure_docker_compose_in_profile_infrastructure(
        lo,
        scan_root,
        m,
        app_tree_base=source_target,
        allow_compose_discovery=True,
        manifest_parent=staging,
    )
    ensure_wrangler_in_profile_infrastructure(lo, scan_root, m, app_tree_base=source_target)
    _maybe_write_static_site_compose_scaffold(staging, aid, source_target, m, lo)
    _maybe_write_hosted_compose_scaffold(staging, aid, source_target, m, lo)
    _preserve_hosting_compose_state(staging, aid, m, lo)
    enrich_infrastructure_wrangler_binding_preview(lo.get("infrastructure") or {}, source_target)
    patch_manifest_root_for_hosting(m)
    prof = _profile_relpath(m)
    loc_path = staging / prof
    loc_path.parent.mkdir(parents=True, exist_ok=True)
    fill_resolved_paths_in_manifest(m, man_path, lo)
    man_path.write_text(yaml.safe_dump(m, **_YAML_DUMP_KW), encoding="utf-8")
    loc_path.write_text(yaml.safe_dump(lo, **_YAML_DUMP_KW), encoding="utf-8")
    src_link = staging / HOSTING_SOURCE_LINK_NAME
    refresh_symlink(src_link, source_target, target_is_dir=True)
    cfg_sync = sync_hosting_config_ref_symlinks(staging, source_target, m, lo)
    logical_man = hosting_manifest_logical_path(eco, aid)
    return {
        "ok": True,
        "materialized": True,
        "manifest_path": str(logical_man.resolve()),
        "localhost_path": str(loc_path.resolve()),
        "staging_dir": str(staging.resolve()),
        "manifest_yaml": man_path.read_text(encoding="utf-8"),
        "localhost_yaml": loc_path.read_text(encoding="utf-8"),
        "registry_manifest_relpath": registry_manifest_relpath(aid),
        "config_symlinks": cfg_sync.get("created") or [],
        "config_symlinks_skipped": cfg_sync.get("skipped") or [],
    }


def save_registration_yaml(
    path_rel: str,
    app_id: str,
    manifest_yaml: str,
    localhost_yaml: str,
) -> dict[str, Any]:
    """Validate editor YAML and write manifest + profile files."""
    orig_root = resolve_registration_path(path_rel)
    aid = require_registration_app_id(app_id)
    eco = Path(PROJECT_ROOT).resolve()

    m_blob = (manifest_yaml or "").strip()
    l_blob = (localhost_yaml or "").strip()
    if not m_blob:
        raise ValueError("manifest_yaml is required")
    if not l_blob:
        raise ValueError("localhost_yaml is required")

    try:
        parsed_m = yaml.safe_load(m_blob)
    except yaml.YAMLError as exc:
        raise ValueError(f"Manifest YAML parse error: {exc}") from exc
    if not isinstance(parsed_m, dict):
        raise ValueError("Manifest must be a YAML mapping")
    try:
        ApplicationManifest.model_validate(parsed_m)
    except ValidationError as exc:
        raise ValueError(f"Manifest schema: {exc}") from exc

    try:
        parsed_l = yaml.safe_load(l_blob)
    except yaml.YAMLError as exc:
        raise ValueError(f"Localhost YAML parse error: {exc}") from exc
    if not isinstance(parsed_l, dict):
        raise ValueError("Localhost profile must be a YAML mapping")
    try:
        LocalhostProfile.model_validate(parsed_l)
    except ValidationError as exc:
        raise ValueError(f"Localhost schema: {exc}") from exc

    prof = _profile_relpath(parsed_m)
    loc_dump = yaml.safe_dump(parsed_l, **_YAML_DUMP_KW)

    def _apply_infra_ensures(app_tree: Path) -> None:
        if isinstance(parsed_l.get("infrastructure"), dict):
            ensure_docker_compose_in_profile_infrastructure(
                parsed_l, orig_root, parsed_m, app_tree_base=app_tree, allow_compose_discovery=False
            )
            ensure_wrangler_in_profile_infrastructure(
                parsed_l, orig_root, parsed_m, app_tree_base=app_tree
            )
            enrich_infrastructure_wrangler_binding_preview(parsed_l["infrastructure"], app_tree)
        else:
            ensure_docker_compose_in_manifest(parsed_m, orig_root)
            ensure_wrangler_in_manifest(parsed_m, orig_root)

    if is_dir_writable(orig_root):
        app_tree = compute_source_target(orig_root, parsed_m).resolve()
        _apply_infra_ensures(app_tree)
        try:
            LocalhostProfile.model_validate(parsed_l)
        except ValidationError as exc:
            raise ValueError(f"Localhost schema: {exc}") from exc
        try:
            ApplicationManifest.model_validate(parsed_m)
        except ValidationError as exc:
            raise ValueError(f"Manifest schema: {exc}") from exc
        man_path = orig_root / "leco.app.yaml"
        loc_path = orig_root / prof
        man_path.parent.mkdir(parents=True, exist_ok=True)
        loc_path.parent.mkdir(parents=True, exist_ok=True)
        fill_resolved_paths_in_manifest(parsed_m, man_path, parsed_l)
        man_path.write_text(yaml.safe_dump(parsed_m, **_YAML_DUMP_KW), encoding="utf-8")
        loc_path.write_text(loc_dump, encoding="utf-8")
        return {
            "ok": True,
            "materialized": False,
            "manifest_path": str(man_path.resolve()),
            "localhost_path": str(loc_path.resolve()),
        }

    staging = hosting_staging_dir(eco, aid)
    staging.mkdir(parents=True, exist_ok=True)
    tree_root = compute_hosting_source_symlink_target(orig_root, parsed_m)
    refresh_symlink(staging / HOSTING_SOURCE_LINK_NAME, tree_root, target_is_dir=True)
    _apply_infra_ensures(tree_root)
    try:
        LocalhostProfile.model_validate(parsed_l)
    except ValidationError as exc:
        raise ValueError(f"Localhost schema: {exc}") from exc
    try:
        ApplicationManifest.model_validate(parsed_m)
    except ValidationError as exc:
        raise ValueError(f"Manifest schema: {exc}") from exc
    patch_manifest_root_for_hosting(parsed_m)
    prof2 = _profile_relpath(parsed_m)
    man_path = staging / "leco.app.yaml"
    loc_path = staging / prof2
    loc_path.parent.mkdir(parents=True, exist_ok=True)
    fill_resolved_paths_in_manifest(parsed_m, man_path, parsed_l)
    man_path.write_text(yaml.safe_dump(parsed_m, **_YAML_DUMP_KW), encoding="utf-8")
    loc_path.write_text(loc_dump, encoding="utf-8")
    cfg_sync = sync_hosting_config_ref_symlinks(staging, tree_root, parsed_m, parsed_l)
    logical_man = hosting_manifest_logical_path(eco, aid)
    return {
        "ok": True,
        "materialized": True,
        "manifest_path": str(logical_man.resolve()),
        "localhost_path": str(loc_path.resolve()),
        "staging_dir": str(staging.resolve()),
        "registry_manifest_relpath": registry_manifest_relpath(aid),
        "config_symlinks": cfg_sync.get("created") or [],
        "config_symlinks_skipped": cfg_sync.get("skipped") or [],
    }
