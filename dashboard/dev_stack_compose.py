"""Generate isolated dev-stack docker-compose from component catalog."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from platform_config import load_component_catalog, load_platform_config, save_platform_config

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
STACKS_ROOT = _PROJECT_ROOT / "platform" / "dev-stacks"
NETWORK_EXTERNAL = "lh-network"


def _slugify(stack_id: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", stack_id.lower()).strip("-")
    return s or "stack"


def _component_spec(catalog: dict[str, Any], comp_id: str, version: str) -> dict[str, Any]:
    comps = catalog.get("components") or {}
    meta = comps.get(comp_id)
    if not isinstance(meta, dict):
        raise ValueError(f"Unknown component: {comp_id}")
    versions = meta.get("versions") or {}
    ver_spec = versions.get(version) or versions.get(str(meta.get("default_version", "")))
    if not ver_spec and versions:
        ver_spec = next(iter(versions.values()))
    if not isinstance(ver_spec, dict):
        raise ValueError(f"Unknown version {version!r} for {comp_id}")
    return meta, ver_spec


def generate_compose(
    stack_id: str,
    name: str,
    components: list[dict[str, str]],
) -> tuple[Path, dict[str, Any]]:
    catalog = load_component_catalog()
    sid = _slugify(stack_id)
    stack_dir = STACKS_ROOT / sid
    stack_dir.mkdir(parents=True, exist_ok=True)
    internal_net = f"leco-devstack-{sid}-internal"
    project_name = f"leco-devstack-{sid}"

    services: dict[str, Any] = {}
    for pick in components:
        cid = str(pick.get("id") or "").strip()
        ver = str(pick.get("version") or "").strip()
        if not cid:
            continue
        meta, ver_spec = _component_spec(catalog, cid, ver)
        svc_name = f"{cid}-{ver.replace('.', '')}" if cid in ("node", "python", "java", "go", "php") else cid
        if svc_name in services:
            svc_name = f"{svc_name}-{len(services)}"
        image = str(ver_spec.get("image") or "")
        spec: dict[str, Any] = {
            "image": image,
            "restart": "unless-stopped",
            "networks": [internal_net],
        }
        if meta.get("command"):
            spec["command"] = meta["command"]
        env = meta.get("default_env")
        if isinstance(env, dict):
            spec["environment"] = dict(env)
        if meta.get("publish_http"):
            spec["networks"] = [internal_net, NETWORK_EXTERNAL]
        services[svc_name] = spec

    compose = {
        "name": project_name,
        "services": services,
        "networks": {
            internal_net: {"driver": "bridge"},
            NETWORK_EXTERNAL: {"external": True},
        },
        "volumes": {},
    }

    compose_path = stack_dir / "docker-compose.yml"
    compose_path.write_text(yaml.safe_dump(compose, sort_keys=False), encoding="utf-8")
    meta = {
        "id": sid,
        "name": name or sid,
        "project": project_name,
        "internal_network": internal_net,
        "components": components,
    }
    (stack_dir / "stack.yaml").write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")
    return compose_path, meta


def register_stack_in_platform(meta: dict[str, Any], *, state: str = "stopped") -> None:
    cfg = load_platform_config()
    stacks = list(cfg.get("dev_stacks") or [])
    sid = meta["id"]
    stacks = [s for s in stacks if str(s.get("id")) != sid]
    entry: dict[str, Any] = {
        "id": sid,
        "name": meta.get("name") or sid,
        "state": state,
        "components": meta.get("components") or [],
    }
    if meta.get("template"):
        entry["template"] = meta.get("template")
    if meta.get("sample_data") is not None:
        entry["sample_data"] = bool(meta.get("sample_data"))
    stacks.append(entry)
    cfg["dev_stacks"] = stacks
    save_platform_config(cfg)


def list_stacks_from_platform() -> list[dict[str, Any]]:
    """Stacks from platform config, merged with on-disk stack.yaml under platform/dev-stacks/."""
    by_id: dict[str, dict[str, Any]] = {}
    for row in load_platform_config().get("dev_stacks") or []:
        if isinstance(row, dict):
            sid = str(row.get("id") or "").strip()
            if sid:
                by_id[sid] = dict(row)
    if STACKS_ROOT.is_dir():
        for stack_yaml in sorted(STACKS_ROOT.glob("*/stack.yaml")):
            try:
                raw = yaml.safe_load(stack_yaml.read_text(encoding="utf-8"))
            except OSError:
                continue
            if not isinstance(raw, dict):
                continue
            sid = str(raw.get("id") or stack_yaml.parent.name).strip()
            if not sid:
                continue
            disk = {
                "id": sid,
                "name": raw.get("name") or sid,
                "components": raw.get("components") or [],
            }
            if raw.get("template"):
                disk["template"] = raw.get("template")
            if raw.get("sample_data") is not None:
                disk["sample_data"] = bool(raw.get("sample_data"))
            if sid in by_id:
                merged = dict(by_id[sid])
                merged.setdefault("name", disk["name"])
                if not merged.get("components"):
                    merged["components"] = disk["components"]
                by_id[sid] = merged
            else:
                by_id[sid] = disk
    return list(by_id.values())


def stack_dir_for(stack_id: str) -> Path:
    return STACKS_ROOT / _slugify(stack_id)
