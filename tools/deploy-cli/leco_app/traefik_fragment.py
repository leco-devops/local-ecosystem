"""Generate Traefik file-provider YAML fragments for optional *.lh routing."""

from __future__ import annotations

import re
from typing import Any

from leco_app.schema import ApplicationManifest, RoutingEntry


def _safe_id(hostname: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", hostname.lower()).strip("-")
    return s or "app"


def routing_entry_fragment(manifest: ApplicationManifest, entry: RoutingEntry) -> dict[str, Any]:
    sid = f"{_safe_id(manifest.name)}-{_safe_id(entry.hostname)}-svc"
    rid_http = f"{_safe_id(manifest.name)}-{_safe_id(entry.hostname)}-http"
    rid_https = f"{_safe_id(manifest.name)}-{_safe_id(entry.hostname)}-https"
    backend = f"http://{entry.backend_host}:{entry.backend_port}"
    return {
        "http": {
            "routers": {
                rid_http: {
                    "rule": f"Host(`{entry.hostname}`)",
                    "service": sid,
                    "entryPoints": ["web"],
                },
                rid_https: {
                    "rule": f"Host(`{entry.hostname}`)",
                    "service": sid,
                    "entryPoints": ["websecure"],
                    "tls": True,
                },
            },
            "services": {
                sid: {"loadBalancer": {"servers": [{"url": backend}]}},
            },
        }
    }


def merge_fragments(fragments: list[dict[str, Any]]) -> dict[str, Any]:
    routers: dict[str, Any] = {}
    services: dict[str, Any] = {}
    for frag in fragments:
        http = frag.get("http") or {}
        for k, v in (http.get("routers") or {}).items():
            routers[k] = v
        for k, v in (http.get("services") or {}).items():
            services[k] = v
    return {"http": {"routers": routers, "services": services}}


def manifest_to_traefik_yaml(manifest: ApplicationManifest) -> str:
    import yaml

    if not manifest.routing or not manifest.routing.entries:
        return "# No routing.entries in manifest — add hosts under routing:\n"
    frags = [routing_entry_fragment(manifest, e) for e in manifest.routing.entries]
    merged = merge_fragments(frags)
    return (
        "# Paste under traefik/dynamic.yml → http.routers / http.services (merge keys manually).\n"
        "# Traefik watches the file; backup dynamic.yml first.\n\n"
        + yaml.safe_dump(merged, default_flow_style=False, sort_keys=False, allow_unicode=True)
    )
