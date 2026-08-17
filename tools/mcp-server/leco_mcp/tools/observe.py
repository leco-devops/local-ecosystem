"""Read-only tools: stack health, services, logs, URL probes, metrics."""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ..runtime import Deps
from ..shaping import guard_size, pick, summarize_services, summarize_system, tail

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)


def register(server: MCPServer, deps: Deps) -> None:
    client = deps.client
    settings = deps.settings

    @server.tool(name="leco_server_info", annotations=READ_ONLY)
    async def leco_server_info() -> dict[str, Any]:
        """How this MCP server is configured and whether the LEco DevOps stack answers.

        Call this first when something fails: it reports the resolved dashboard URL, whether
        a control token is configured, and whether destructive actions are enabled.
        """
        info: dict[str, Any] = {"settings": settings.describe()}
        try:
            info["dashboard_url"] = await client.base_url()
            info["version"] = await client.get("/api/version")
            info["reachable"] = True
        except Exception as exc:  # noqa: BLE001 - this tool exists to report failure
            info["reachable"] = False
            info["error"] = str(exc)
        return info

    @server.tool(name="leco_status", annotations=READ_ONLY)
    async def leco_status(
        detail: Literal["summary", "services", "full"] = "summary",
    ) -> dict[str, Any]:
        """Overall stack health: service counts, URL probes, CPU/RAM, alerts.

        detail=summary  → health headline only (default, cheapest)
        detail=services → headline plus one row per service with URL check results
        detail=full     → the entire /api/overview payload (large; ~56 KB)

        This call collects live Docker stats and can take 10-15 seconds.
        """
        payload = await client.get("/api/overview", timeout=settings.read_timeout)
        if detail == "full":
            return guard_size(payload, settings.max_response_chars)
        out = summarize_system(payload)
        if detail == "services":
            out["services_detail"] = summarize_services(payload)
        return guard_size(out, settings.max_response_chars)

    @server.tool(name="leco_services", annotations=READ_ONLY)
    async def leco_services() -> dict[str, Any]:
        """Catalog of managed stack services (name, container, purpose).

        Cheap and fast — use it to map a friendly service name onto its container name
        before calling leco_logs or leco_control.
        """
        return await client.get("/api/services")

    @server.tool(name="leco_logs", annotations=READ_ONLY)
    async def leco_logs(
        container: str,
        search: str = "",
        level: Literal["all", "error", "warn", "info"] = "all",
        tail_lines: int = 300,
        since_seconds: int = 1800,
    ) -> dict[str, Any]:
        """Container logs with server-side search and level filtering.

        container: Docker container name, e.g. "traefik", "service-dashboard", "n8n".
        Use leco_services or leco_control_targets to discover valid container names.
        """
        payload = await client.get(
            "/api/logs",
            params={
                "service": container,
                "search": search,
                "level": level,
                "tail": max(1, min(int(tail_lines), 2000)),
                "since": max(1, int(since_seconds)),
            },
        )
        entries = payload.get("entries") or []
        # Entries are {timestamp, level, message}; flatten to text so the model reads log
        # output the way it appears in a terminal instead of paying for JSON scaffolding.
        text = "\n".join(
            f"{e.get('timestamp', '')} [{e.get('level', '')}] {e.get('message', '')}".strip()
            for e in entries
        )
        return guard_size(
            {
                "container": payload.get("container"),
                "container_exists": payload.get("exists"),
                "generated_at": payload.get("generated_at"),
                "returned": payload.get("returned"),
                "total_scanned": payload.get("total_scanned"),
                "level_counts": payload.get("level_counts"),
                "text": tail(text, settings.max_log_chars),
            },
            settings.max_response_chars,
        )

    @server.tool(name="leco_urls", annotations=READ_ONLY)
    async def leco_urls(
        only_unhealthy: bool = False,
        category: str = "",
    ) -> dict[str, Any]:
        """Every *.lh URL the stack publishes, with live probe results.

        only_unhealthy=True narrows to endpoints that failed their probe — the fastest way
        to find what is broken after a deploy.
        """
        payload = await client.get("/api/reference")
        rows: list[dict[str, Any]] = []
        for cat in payload.get("categories") or []:
            cat_id = str(cat.get("id") or "")
            cat_title = str(cat.get("title") or cat_id)
            if category and category.lower() not in f"{cat_id} {cat_title}".lower():
                continue
            for item in cat.get("items") or []:
                for check in item.get("url_checks") or []:
                    ok = check.get("ok")
                    if only_unhealthy and ok is not False:
                        continue
                    rows.append(
                        {
                            "category": cat_title,
                            "label": item.get("label"),
                            "url": check.get("url"),
                            "ok": ok,
                            "status": check.get("status_code"),
                            "latency_ms": check.get("latency_ms"),
                            "error": check.get("error"),
                        }
                    )
        return guard_size(
            {
                "generated_at": payload.get("generated_at"),
                "total_urls": payload.get("total_urls"),
                "healthy_urls": payload.get("healthy_urls"),
                "filtered_count": len(rows),
                "urls": rows,
            },
            settings.max_response_chars,
        )

    @server.tool(name="leco_metrics", annotations=READ_ONLY)
    async def leco_metrics(limit: int = 60) -> dict[str, Any]:
        """Recent CPU / memory / health time series for the whole stack.

        Each call also appends a fresh sample, so repeated polling builds history.
        """
        payload = await client.get(
            "/api/metrics/history", params={"limit": max(1, min(int(limit), 500))}
        )
        return guard_size(payload, settings.max_response_chars)

    @server.tool(name="leco_cloudflare_local", annotations=READ_ONLY)
    async def leco_cloudflare_local() -> dict[str, Any]:
        """Health of the Cloudflare-local adapters (R2, KV, D1, Workers, browser rendering)."""
        payload = await client.get("/api/cloudflare-local")
        return guard_size(payload, settings.max_response_chars)

    @server.tool(name="leco_traefik_routes", annotations=READ_ONLY)
    async def leco_traefik_routes(hostname: str = "") -> dict[str, Any]:
        """Traefik routers and services currently loaded, with hosted-app ownership hints.

        hostname filters to routes matching a substring, e.g. "myapp.lh".
        """
        payload = await client.get("/api/traefik/routes")
        if hostname:
            needle = hostname.lower()
            routes = [
                r
                for r in (payload.get("routes") or [])
                if needle in str(r.get("hostname") or r.get("rule") or "").lower()
            ]
            payload = {**{k: v for k, v in payload.items() if k != "routes"}, "routes": routes}
        return guard_size(payload, settings.max_response_chars)

    @server.tool(name="leco_version", annotations=READ_ONLY)
    async def leco_version() -> dict[str, Any]:
        """Platform, CLI, and update-catalog versions plus documentation pointers."""
        payload = await client.get("/api/version")
        return pick(
            payload,
            "project",
            "application",
            "version",
            "released",
            "license",
            "components",
            "documentation",
        )
