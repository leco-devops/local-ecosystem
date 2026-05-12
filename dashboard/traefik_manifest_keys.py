"""Derive Traefik router/service keys from a leco.app.yaml dict (no deploy-cli dependency)."""

from __future__ import annotations

import re
from typing import Any


def _safe_id(hostname: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", hostname.lower()).strip("-")
    return s or "app"


def _pick(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _merge_fragments(fragments: list[dict[str, Any]]) -> dict[str, Any]:
    routers: dict[str, Any] = {}
    services: dict[str, Any] = {}
    for frag in fragments:
        http = frag.get("http") or {}
        for k, v in (http.get("routers") or {}).items():
            routers[k] = v
        for k, v in (http.get("services") or {}).items():
            services[k] = v
    return {"http": {"routers": routers, "services": services}}


def _legacy_fragment(manifest_name: str, entry: dict[str, Any]) -> dict[str, Any]:
    hn = str(_pick(entry, "hostname", "hostName") or "").strip()
    bh = str(_pick(entry, "backendHost", "backend_host") or "").strip()
    bp = int(_pick(entry, "backendPort", "backend_port") or 8080)
    sid = f"{_safe_id(manifest_name)}-{_safe_id(hn)}-svc"
    rid_http = f"{_safe_id(manifest_name)}-{_safe_id(hn)}-http"
    rid_https = f"{_safe_id(manifest_name)}-{_safe_id(hn)}-https"
    backend = f"http://{bh}:{bp}"
    return {
        "http": {
            "routers": {
                rid_http: {
                    "rule": f"Host(`{hn}`)",
                    "service": sid,
                    "entryPoints": ["web"],
                },
                rid_https: {
                    "rule": f"Host(`{hn}`)",
                    "service": sid,
                    "entryPoints": ["websecure"],
                    "tls": True,
                },
            },
            "services": {sid: {"loadBalancer": {"servers": [{"url": backend}]}}},
        }
    }


def _split_fragment(manifest_name: str, entry: dict[str, Any]) -> dict[str, Any]:
    hn = str(_pick(entry, "hostname", "hostName") or "").strip()
    prefix = str(_pick(entry, "apiPathPrefix", "api_path_prefix") or "/api").strip()
    if not prefix.startswith("/"):
        prefix = "/api"
    fe = _pick(entry, "frontend", "Frontend")
    api = _pick(entry, "apiBackend", "api_backend", "ApiBackend")
    if not isinstance(fe, dict) or not isinstance(api, dict):
        return {"http": {"routers": {}, "services": {}}}
    fh = str(_pick(fe, "host", "Host") or "").strip()
    fp = int(_pick(fe, "port", "Port") or 3000)
    ah = str(_pick(api, "host", "Host") or "").strip()
    ap = int(_pick(api, "port", "Port") or 8080)
    name = _safe_id(manifest_name)
    h = _safe_id(hn)
    api_rule = f"Host(`{hn}`) && PathPrefix(`{prefix}`)"
    ui_rule = f"Host(`{hn}`)"
    fe_url = f"http://{fh}:{fp}"
    api_url = f"http://{ah}:{ap}"
    fe_svc = f"{name}-{h}-fe-svc"
    api_svc = f"{name}-{h}-api-svc"
    return {
        "http": {
            "routers": {
                f"{name}-{h}-api-http": {
                    "rule": api_rule,
                    "service": api_svc,
                    "entryPoints": ["web"],
                    "priority": 20,
                },
                f"{name}-{h}-api-https": {
                    "rule": api_rule,
                    "service": api_svc,
                    "entryPoints": ["websecure"],
                    "tls": True,
                    "priority": 20,
                },
                f"{name}-{h}-http": {
                    "rule": ui_rule,
                    "service": fe_svc,
                    "entryPoints": ["web"],
                    "priority": 10,
                },
                f"{name}-{h}-https": {
                    "rule": ui_rule,
                    "service": fe_svc,
                    "entryPoints": ["websecure"],
                    "tls": True,
                    "priority": 10,
                },
            },
            "services": {
                fe_svc: {"loadBalancer": {"servers": [{"url": fe_url}]}},
                api_svc: {"loadBalancer": {"servers": [{"url": api_url}]}},
            },
        }
    }


def _local_cf_alias_fragment(manifest_name: str, prefix: str) -> dict[str, Any]:
    """Match deploy-cli traefik_fragment.local_cf_adapter_host_aliases_fragment router keys."""
    name = _safe_id(manifest_name)
    p = prefix.strip().lower()
    if not p:
        return {"http": {"routers": {}, "services": {}}}
    routers: dict[str, Any] = {}
    for kind, host, svc in (
        ("kv", f"{p}-kv.lh", "kv-service"),
        ("r2", f"{p}-r2.lh", "r2-service"),
        ("d1", f"{p}-d1.lh", "d1-service"),
    ):
        base = f"{name}-cf-{kind}"
        routers[f"{base}-http"] = {
            "rule": f"Host(`{host}`)",
            "service": svc,
            "entryPoints": ["web"],
        }
        routers[f"{base}-https"] = {
            "rule": f"Host(`{host}`)",
            "service": svc,
            "entryPoints": ["websecure"],
            "tls": True,
        }
    return {"http": {"routers": routers, "services": {}}}


def _upstream_fragment(manifest_name: str, entry: dict[str, Any]) -> dict[str, Any]:
    """Mirror traefik_fragment.py::_upstream_routing_fragment router key shape."""
    hn = str(_pick(entry, "hostname", "hostName") or "").strip()
    upstream = entry.get("upstream") or []
    if not isinstance(upstream, list) or not upstream:
        return {"http": {"routers": {}, "services": {}}}
    name = _safe_id(manifest_name)
    h = _safe_id(hn)
    routers: dict[str, Any] = {}
    services: dict[str, Any] = {}
    for idx, rule in enumerate(upstream):
        if not isinstance(rule, dict):
            continue
        prefix = str(rule.get("prefix") or "/").strip() or "/"
        suffix_src = prefix.strip("/").replace("/", "-") or "root"
        suffix = re.sub(r"[^a-z0-9-]+", "-", suffix_src.lower()).strip("-") or f"r{idx}"
        if len(suffix) > 24:
            suffix = suffix[:24]
        svc_key = f"{name}-{h}-{suffix}-svc"
        services[svc_key] = {"loadBalancer": {"servers": []}}
        for scheme in ("http", "https"):
            routers[f"{name}-{h}-{suffix}-{scheme}"] = {}
    return {"http": {"routers": routers, "services": services}}


def _entry_fragment(manifest_name: str, entry: dict[str, Any]) -> dict[str, Any]:
    upstream = entry.get("upstream")
    if isinstance(upstream, list) and upstream:
        return _upstream_fragment(manifest_name, entry)
    fe = _pick(entry, "frontend", "Frontend")
    api = _pick(entry, "apiBackend", "api_backend", "ApiBackend")
    if isinstance(fe, dict) and isinstance(api, dict):
        return _split_fragment(manifest_name, entry)
    bh = _pick(entry, "backendHost", "backend_host")
    if bh and str(bh).strip():
        return _legacy_fragment(manifest_name, entry)
    return {"http": {"routers": {}, "services": {}}}


def manifest_traefik_keys_dict(manifest: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Return (router_keys, service_keys) to remove for this manifest."""
    tc = manifest.get("traefikCleanup") or manifest.get("traefik_cleanup")
    if isinstance(tc, dict):
        r = tc.get("routers") or []
        s = tc.get("services") or []
        if r or s:
            return [str(x) for x in r if x], [str(x) for x in s if x]

    name = str(manifest.get("name") or "app")
    frags: list[dict[str, Any]] = []
    routing = manifest.get("routing") or {}
    entries = routing.get("entries") if isinstance(routing, dict) else None
    if isinstance(entries, list):
        for e in entries:
            if isinstance(e, dict):
                frags.append(_entry_fragment(name, e))
    cf = manifest.get("cloudflare") or {}
    if isinstance(cf, dict):
        pfx = cf.get("localCfPublicPrefix") or cf.get("local_cf_public_prefix")
        if isinstance(pfx, str) and pfx.strip():
            frags.append(_local_cf_alias_fragment(name, pfx))
    if not frags:
        return [], []

    merged = _merge_fragments(frags)
    http = merged.get("http") or {}
    r = list((http.get("routers") or {}).keys())
    s = list((http.get("services") or {}).keys())
    return r, s
