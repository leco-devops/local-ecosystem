"""Generate Traefik file-provider YAML fragments for optional *.lh routing."""

from __future__ import annotations

import re
from typing import Any

from leco_app.schema import ApplicationManifest, LocalRuntimeSpec, RoutingEntry, RoutingUpstreamRule


def _safe_id(hostname: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", hostname.lower()).strip("-")
    return s or "app"


def _runtime_container_dns(app_slug: str, runtime_id: str) -> str:
    """Mirror :meth:`leco_runtimes.RuntimeBuildContext.runtime_container` shape.

    Kept inline (instead of importing the dashboard package) so leco-app stays
    a standalone CLI deployable without the dashboard module tree.
    """
    s = re.sub(r"[^a-z0-9-]+", "-", (app_slug or "").lower()).strip("-") or "x"
    r = re.sub(r"[^a-z0-9-]+", "-", (runtime_id or "").lower()).strip("-") or "x"
    return f"leco-rt-{s[:40]}-{r[:40]}"


def _runtime_lookup(manifest: ApplicationManifest, runtime_id: str) -> LocalRuntimeSpec | None:
    """Find a runtime by id across the manifest (post-profile merge)."""
    for rt in (manifest.runtimes or []):
        if rt.id == runtime_id:
            return rt
    return None


def _legacy_routing_fragment(manifest: ApplicationManifest, entry: RoutingEntry) -> dict[str, Any]:
    sid = f"{_safe_id(manifest.name)}-{_safe_id(entry.hostname)}-svc"
    rid_http = f"{_safe_id(manifest.name)}-{_safe_id(entry.hostname)}-http"
    rid_https = f"{_safe_id(manifest.name)}-{_safe_id(entry.hostname)}-https"
    backend = f"http://{entry.backend_host.strip()}:{entry.backend_port}"
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


def _split_routing_fragment(manifest: ApplicationManifest, entry: RoutingEntry) -> dict[str, Any]:
    """Host + PathPrefix(api) → API container; Host → UI (matches CrawlerVision / local-ecosystem pattern)."""
    assert entry.frontend is not None and entry.api_backend is not None
    name = _safe_id(manifest.name)
    h = _safe_id(entry.hostname)
    prefix = entry.api_path_prefix
    api_rule = f"Host(`{entry.hostname}`) && PathPrefix(`{prefix}`)"
    ui_rule = f"Host(`{entry.hostname}`)"
    fe_url = f"http://{entry.frontend.host}:{entry.frontend.port}"
    api_url = f"http://{entry.api_backend.host}:{entry.api_backend.port}"
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


def _upstream_rule_target(
    manifest: ApplicationManifest, rule: RoutingUpstreamRule
) -> tuple[str, int] | None:
    """Resolve a routing rule to a ``(host, port)`` Traefik forwards to."""
    if rule.target == "runtime":
        rt = _runtime_lookup(manifest, rule.runtime or "")
        if rt is None:
            return None
        return _runtime_container_dns(manifest.name, rt.id), rt.port
    if rule.service is None:
        return None
    return rule.service.host, rule.service.port


def _priority_for_prefix(prefix: str) -> int:
    """Longer prefixes win. ``/`` is the catch-all (lowest priority).

    Traefik resolves routers by descending ``priority``. We avoid using the
    same number twice when prefix lengths collide by appending +1 increments
    based on the source order — this is handled by the caller, which renders
    rules in the order they appear in YAML.
    """
    p = (prefix or "/").strip()
    if p == "/" or not p:
        return 1
    # Match Traefik's "deepest path wins" semantics: 10 base + path length keeps
    # /api at 13, /api/v1 at 16, /, '' at 1.
    return 10 + len(p)


def _upstream_routing_fragment(
    manifest: ApplicationManifest, entry: RoutingEntry
) -> dict[str, Any]:
    """Emit one Traefik router per ``upstream`` rule on this routing entry."""
    name = _safe_id(manifest.name)
    h = _safe_id(entry.hostname)
    routers: dict[str, Any] = {}
    services: dict[str, Any] = {}
    used_priorities: dict[int, int] = {}
    for idx, rule in enumerate(entry.upstream):
        endpoint = _upstream_rule_target(manifest, rule)
        if endpoint is None:
            continue
        host, port = endpoint
        # Short stable suffix per rule (prefix + index keeps it deterministic).
        prefix = rule.prefix or "/"
        suffix_src = prefix.strip("/").replace("/", "-") or "root"
        suffix = re.sub(r"[^a-z0-9-]+", "-", suffix_src.lower()).strip("-") or f"r{idx}"
        if len(suffix) > 24:
            suffix = suffix[:24]
        svc_key = f"{name}-{h}-{suffix}-svc"
        services[svc_key] = {
            "loadBalancer": {"servers": [{"url": f"http://{host}:{port}"}]}
        }
        if prefix == "/" or not prefix:
            rule_expr = f"Host(`{entry.hostname}`)"
        else:
            rule_expr = f"Host(`{entry.hostname}`) && PathPrefix(`{prefix}`)"
        priority = _priority_for_prefix(prefix)
        # Disambiguate same-priority rules (Traefik picks routers with higher
        # priority first; equal priorities are non-deterministic).
        while priority in used_priorities and used_priorities[priority] != idx:
            priority += 1
        used_priorities[priority] = idx
        for scheme, entrypoint, extras in (
            ("http", "web", {}),
            ("https", "websecure", {"tls": True}),
        ):
            rid = f"{name}-{h}-{suffix}-{scheme}"
            router: dict[str, Any] = {
                "rule": rule_expr,
                "service": svc_key,
                "entryPoints": [entrypoint],
                "priority": priority,
            }
            router.update(extras)
            routers[rid] = router
    return {"http": {"routers": routers, "services": services}}


def routing_entry_fragment(manifest: ApplicationManifest, entry: RoutingEntry) -> dict[str, Any]:
    if entry.upstream:
        return _upstream_routing_fragment(manifest, entry)
    if entry.frontend is not None and entry.api_backend is not None:
        return _split_routing_fragment(manifest, entry)
    return _legacy_routing_fragment(manifest, entry)


def local_cf_adapter_host_aliases_fragment(manifest: ApplicationManifest) -> dict[str, Any] | None:
    """Traefik routers: {prefix}-kv.lh / -r2.lh / -d1.lh → existing kv-service, r2-service, d1-service."""
    if not manifest.cloudflare or not manifest.cloudflare.local_cf_public_prefix:
        return None
    prefix = manifest.cloudflare.local_cf_public_prefix
    key_id = _safe_id(manifest.name)
    routers: dict[str, Any] = {}
    for kind, host, svc in (
        ("kv", f"{prefix}-kv.lh", "kv-service"),
        ("r2", f"{prefix}-r2.lh", "r2-service"),
        ("d1", f"{prefix}-d1.lh", "d1-service"),
    ):
        base = f"{key_id}-cf-{kind}"
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

    frags: list[dict[str, Any]] = []
    if manifest.routing and manifest.routing.entries:
        frags.extend([routing_entry_fragment(manifest, e) for e in manifest.routing.entries])
    cf_frag = local_cf_adapter_host_aliases_fragment(manifest)
    if cf_frag:
        frags.append(cf_frag)
    if not frags:
        return (
            "# No routing.entries and no cloudflare.localCfPublicPrefix — nothing to merge.\n"
            "# Add routing.entries and/or localCfPublicPrefix (e.g. cv → cv-kv.lh, cv-r2.lh, cv-d1.lh).\n"
        )
    merged = merge_fragments(frags)
    return (
        "# Paste under hosting/traefik/dynamic.yml → http.routers / http.services (merge keys manually).\n"
        "# Traefik watches the file and reloads it automatically.\n"
        "# Upstream routes (routing.entries[].upstream[]): one router per prefix; longer prefix = higher priority.\n"
        "# Split routes (frontend + apiBackend): PathPrefix → API (priority 20), Host → UI (priority 10).\n"
        "# localCfPublicPrefix adds Host rules for {prefix}-kv.lh → kv-service (shared adapter).\n"
        "# target=runtime forwards to leco-rt-<slug>-<runtime.id>:<runtime.port> (lh-network).\n\n"
        + yaml.safe_dump(merged, default_flow_style=False, sort_keys=False, allow_unicode=True)
    )
