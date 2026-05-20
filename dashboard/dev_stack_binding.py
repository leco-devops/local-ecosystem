"""Bind hosted apps to isolated dev stacks (network + connection env)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from dev_stacks import stack_snapshot

_LECO_DEVSTACK_OVERLAY = "docker-compose.leco-devstack.yml"

_ENV_KEYS: dict[str, list[str]] = {
    "postgres": ["DATABASE_URL", "POSTGRES_HOST", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"],
    "mysql": ["DATABASE_URL", "MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE"],
    "redis": ["REDIS_URL"],
    "mongodb": ["MONGODB_URI", "MONGO_URL"],
    "minio": ["S3_ENDPOINT", "MINIO_ENDPOINT"],
}


def _platform_block(manifest: dict[str, Any], localhost: dict[str, Any]) -> dict[str, Any]:
    for src in (localhost, manifest):
        plat = src.get("platform")
        if isinstance(plat, dict):
            return plat
    return {}


def read_platform_binding(manifest_path: str) -> dict[str, Any]:
    """Read platform.devStackId from manifest + leco.yaml."""
    mp = Path(manifest_path).resolve()
    try:
        manifest = yaml.safe_load(mp.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {"dev_stack_id": "", "toolchain": {}}
    if not isinstance(manifest, dict):
        return {"dev_stack_id": "", "toolchain": {}}
    prof = (manifest.get("localHostProfile") or "leco.yaml").strip() or "leco.yaml"
    localhost: dict[str, Any] = {}
    prof_path = mp.parent / prof
    if prof_path.is_file():
        try:
            localhost = yaml.safe_load(prof_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            localhost = {}
    if not isinstance(localhost, dict):
        localhost = {}
    plat = _platform_block(manifest, localhost)
    tc = plat.get("toolchain") if isinstance(plat.get("toolchain"), dict) else {}
    return {
        "dev_stack_id": dev_stack_id_from_registration(manifest, localhost),
        "toolchain": tc,
    }


def set_platform_dev_stack(manifest_path: str, dev_stack_id: str | None) -> dict[str, Any]:
    """Persist platform.devStackId on leco.yaml (and inline manifest platform block if present)."""
    mp = Path(manifest_path).resolve()
    try:
        manifest = yaml.safe_load(mp.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return {"ok": False, "error": str(exc)}
    if not isinstance(manifest, dict):
        return {"ok": False, "error": "invalid manifest"}
    prof = (manifest.get("localHostProfile") or "leco.yaml").strip() or "leco.yaml"
    prof_path = mp.parent / prof
    try:
        localhost = yaml.safe_load(prof_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return {"ok": False, "error": str(exc)}
    if not isinstance(localhost, dict):
        localhost = {}
    plat = localhost.get("platform")
    if not isinstance(plat, dict):
        plat = {}
        localhost["platform"] = plat
    sid = (dev_stack_id or "").strip().lower()
    if sid:
        plat["devStackId"] = sid
    else:
        plat.pop("devStackId", None)
        plat.pop("dev_stack_id", None)
    prof_path.write_text(
        yaml.safe_dump(localhost, sort_keys=False, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )
    mplat = manifest.get("platform")
    if isinstance(mplat, dict):
        if sid:
            mplat["devStackId"] = sid
        else:
            mplat.pop("devStackId", None)
        mp.write_text(
            yaml.safe_dump(manifest, sort_keys=False, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )
    overlay = ensure_dev_stack_hosting_overlay(mp) if sid else {"ok": True, "skipped": "cleared"}
    return {"ok": True, "dev_stack_id": sid, "overlay": overlay}


def dev_stack_id_from_registration(manifest: dict[str, Any], localhost: dict[str, Any]) -> str:
    plat = _platform_block(manifest, localhost)
    raw = plat.get("devStackId") or plat.get("dev_stack_id") or ""
    return str(raw).strip().lower()


def _docker_uri_for_kind(snapshot: dict[str, Any], kind: str) -> str:
    for row in snapshot.get("data_stores") or []:
        if str(row.get("kind")) != kind:
            continue
        for ep in row.get("connection_endpoints") or []:
            if ep.get("scope") == "docker" and ep.get("uri"):
                return str(ep["uri"])
    return ""


def _env_from_snapshot(snapshot: dict[str, Any]) -> dict[str, str]:
    env: dict[str, str] = {}
    sid = str(snapshot.get("stack_id") or "")
    if sid:
        env["LECO_DEVSTACK_ID"] = sid
    for row in snapshot.get("data_stores") or []:
        kind = str(row.get("kind") or "")
        uri = _docker_uri_for_kind(snapshot, kind)
        if not uri:
            continue
        keys = _ENV_KEYS.get(kind, [])
        if keys:
            env[keys[0]] = uri
        if kind == "postgres" and len(keys) > 1:
            env.setdefault("POSTGRES_HOST", str(row.get("name") or "postgres"))
    return env


def ensure_dev_stack_hosting_overlay(manifest_abs: Path) -> dict[str, Any]:
    """Attach app services to dev stack internal network and inject stack env."""
    mp = manifest_abs.resolve()
    try:
        manifest = yaml.safe_load(mp.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {"ok": False, "error": "read manifest"}
    if not isinstance(manifest, dict):
        return {"ok": False, "error": "invalid manifest"}
    prof = (manifest.get("localHostProfile") or "leco.yaml").strip() or "leco.yaml"
    prof_path = mp.parent / prof
    try:
        localhost = yaml.safe_load(prof_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {"ok": False, "error": "read localhost profile"}
    if not isinstance(localhost, dict):
        return {"ok": False, "error": "invalid localhost profile"}

    stack_id = dev_stack_id_from_registration(manifest, localhost)
    if not stack_id:
        return {"ok": True, "skipped": "no platform.devStackId"}

    try:
        snapshot = stack_snapshot(stack_id)
    except Exception as exc:
        return {"ok": False, "error": f"stack snapshot: {exc}"}

    internal = f"leco-devstack-{stack_id}-internal"
    stack_env = _env_from_snapshot(snapshot)

    from leco_detect import _load_compose_services_for_localhost

    loaded = _load_compose_services_for_localhost(localhost, mp.parent, manifest)
    if not loaded:
        return {"ok": True, "skipped": "no compose services"}
    services, _dc, infra = loaded
    service_names = [n for n, spec in services.items() if isinstance(spec, dict)]

    overlay_path = mp.parent / _LECO_DEVSTACK_OVERLAY
    lines = [
        "# LEco dev-stack overlay — join isolated stack network and inject connection env.\n",
        "# Set platform.devStackId in leco.yaml or leco.app.yaml.\n\n",
        "services:\n",
    ]
    for sname in sorted(service_names, key=str.lower):
        lines.append(f"  {sname}:\n")
        lines.append("    networks:\n")
        lines.append(f"      - {internal}\n")
        if stack_env:
            lines.append("    environment:\n")
            for k, v in stack_env.items():
                vv = yaml.safe_dump(v, default_flow_style=True, allow_unicode=True).strip()
                lines.append(f"      {k}: {vv}\n")
        lines.append("\n")
    lines.append("networks:\n")
    lines.append(f"  {internal}:\n")
    lines.append("    external: true\n")
    overlay_path.write_text("".join(lines), encoding="utf-8")

    if not isinstance(infra.get("dockerCompose"), dict):
        infra["dockerCompose"] = {}
    dc = infra["dockerCompose"]
    extras = list(dc.get("additionalComposeFilesFromManifest") or dc.get("additional_compose_files_from_manifest") or [])
    rel = _LECO_DEVSTACK_OVERLAY
    if rel not in extras:
        extras.append(rel)
    dc["additionalComposeFilesFromManifest"] = extras
    prof_path.write_text(
        yaml.safe_dump(localhost, sort_keys=False, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )
    return {"ok": True, "stack_id": stack_id, "overlay": str(overlay_path), "env_keys": list(stack_env)}
