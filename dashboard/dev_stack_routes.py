"""Traefik file-provider routes for isolated dev stacks on lh-network."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from dev_stack_compose import STACKS_ROOT, _slugify
from platform_config import _PROJECT_ROOT, base_domain

TRAEFIK_DEVSTACKS_FILE = _PROJECT_ROOT / "hosting" / "traefik" / "20-dev-stacks.yml"


def http_container_name(stack_id: str, role: str = "app") -> str:
    return f"leco-devstack-{_slugify(stack_id)}-{role}"


def stack_hostname(stack_id: str) -> str:
    sid = _slugify(stack_id)
    dom = (base_domain() or "lh").strip() or "lh"
    if dom == "lh":
        return f"{sid}.lh"
    return f"{sid}.{dom}"


def load_stack_meta(stack_id: str) -> dict[str, Any]:
    path = STACKS_ROOT / _slugify(stack_id) / "stack.yaml"
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _http_backend(stack_id: str, meta: dict[str, Any]) -> tuple[str, int] | None:
    from dev_stack_access import template_http_backend

    return template_http_backend(str(meta.get("template") or ""), stack_id)


def render_dev_stack_traefik_file() -> Path:
    """Rewrite hosting/traefik/20-dev-stacks.yml from on-disk dev stacks."""
    routers: dict[str, Any] = {}
    services: dict[str, Any] = {}
    if STACKS_ROOT.is_dir():
        for stack_yaml in sorted(STACKS_ROOT.glob("*/stack.yaml")):
            meta = yaml.safe_load(stack_yaml.read_text(encoding="utf-8"))
            if not isinstance(meta, dict):
                continue
            sid = str(meta.get("id") or stack_yaml.parent.name).strip()
            if not sid:
                continue
            backend = _http_backend(sid, meta)
            if not backend:
                continue
            host, port = backend
            hostname = stack_hostname(sid)
            svc_key = f"devstack-{_slugify(sid)}"
            services[svc_key] = {
                "loadBalancer": {
                    "servers": [{"url": f"http://{host}:{port}"}],
                }
            }
            routers[f"{svc_key}-http"] = {
                "rule": f"Host(`{hostname}`)",
                "service": svc_key,
                "entryPoints": ["web"],
            }
            routers[f"{svc_key}-https"] = {
                "rule": f"Host(`{hostname}`)",
                "service": svc_key,
                "entryPoints": ["websecure"],
                "tls": True,
            }
    body: dict[str, Any] = {}
    if routers or services:
        body = {"http": {"routers": routers, "services": services}}
        from traefik_dynamic_file import _prune_empty_http_maps

        _prune_empty_http_maps(body)
        if not body:
            body = {}
    TRAEFIK_DEVSTACKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    text = "{}\n" if not body else yaml.safe_dump(body, sort_keys=False)
    TRAEFIK_DEVSTACKS_FILE.write_text(text, encoding="utf-8")
    return TRAEFIK_DEVSTACKS_FILE


def sync_dev_stack_routes(stack_id: str | None = None) -> dict[str, Any]:
    """Regenerate dev-stack Traefik routes (optional: after one stack change)."""
    path = render_dev_stack_traefik_file()
    return {"ok": True, "path": str(path), "stack_id": stack_id}
