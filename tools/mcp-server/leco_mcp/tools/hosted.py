"""Hosted LEco apps: inspect, control lifecycle, read logs, import seed data, offboard.

These are apps registered in ``config/leco-registry.yaml`` and materialized under
``hosting/app-available/<slug>/``. Lifecycle runs through the same Control API as stack
services, using the ``leco-stack-<slug>`` target id.
"""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations

from ..runtime import Deps
from ..safety import guard_destructive, guard_explicit_destructive, is_destructive, validate_action
from ..shaping import compact_hosted_app, guard_size, pick, tail

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)
MUTATING = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False)

APP_ACTIONS = frozenset(
    {"start", "stop", "restart", "pause", "unpause", "deploy", "recreate", "remove", "reset"}
)

# Top-level snapshot keys per section.
SNAPSHOT_TOP_KEYS: dict[str, tuple[str, ...]] = {
    "identity": ("ok", "slug", "generated_at"),
    "runtime": ("runtime", "aggregate", "services", "compose_ps_ok"),
    "urls": ("url_probes",),
    "manifest": ("compose_docker_args",),
    "services": ("attached_services",),
    "data": ("data_import",),
}

# ``manifest_ui`` carries most of the detail; map sections onto its sub-keys so a caller
# can ask for URLs without also paying for the resolved compose wiring.
SNAPSHOT_MANIFEST_KEYS: dict[str, tuple[str, ...]] = {
    "identity": (
        "application_version",
        "source_location",
        "local_host_profile",
        "localhost_archetype",
        "deploy_fingerprint",
    ),
    "urls": (
        "main_url",
        "main_urls",
        "main_url_source",
        "derived_main_url",
        "derived_main_urls",
        "localhost_urls",
        "endpoint_urls",
        "health_urls",
        "routes",
    ),
    "manifest": (
        "resolved_paths",
        "profile_docker_compose",
        "effective_has_docker_compose",
        "platform",
        "dev_stack_id",
        "localhost_lifecycle",
        "local_cf",
        "local_cf_adapter_hosts",
        "local_cf_public_prefix",
        "dedicated_local_adapters",
        "wrangler_expected",
        "profile_cloudflare",
    ),
}

SNAPSHOT_SECTIONS = sorted(set(SNAPSHOT_TOP_KEYS) | set(SNAPSHOT_MANIFEST_KEYS))


def _target_id(slug: str) -> str:
    return f"leco-stack-{slug.strip()}"


def register(server: MCPServer, deps: Deps) -> None:
    client = deps.client
    settings = deps.settings

    @server.tool(name="leco_apps", annotations=READ_ONLY)
    async def leco_apps(
        running: Literal["any", "yes", "no"] = "any",
        query: str = "",
    ) -> dict[str, Any]:
        """List every hosted LEco app with its status, main URL, and Traefik routes.

        Includes apps materialized but not yet registered (pending_registration=true).
        """
        payload = await client.get("/api/hosted-apps")
        rows = [compact_hosted_app(a) for a in payload.get("apps") or []]
        if running == "yes":
            rows = [r for r in rows if r.get("running") is True]
        elif running == "no":
            rows = [r for r in rows if r.get("running") is not True]
        if query:
            q = query.lower()
            rows = [
                r
                for r in rows
                if q in str(r.get("slug") or "").lower() or q in str(r.get("label") or "").lower()
            ]
        return guard_size({"count": len(rows), "apps": rows}, settings.max_response_chars)

    @server.tool(name="leco_app_snapshot", annotations=READ_ONLY)
    async def leco_app_snapshot(
        slug: str,
        sections: list[Literal["identity", "runtime", "urls", "manifest", "services", "data"]]
        | None = None,
        full: bool = False,
    ) -> dict[str, Any]:
        """Deep detail for one hosted app: effective manifest, compose state, URLs, services.

        The raw snapshot is large. By default this returns identity + runtime + urls.
        Ask for specific sections, or full=true for everything.

        sections:
          identity  registry id, source repo, archetype, deploy fingerprint
          runtime   compose/container state and CPU/RAM aggregate
          urls      main + endpoint + health URLs, Traefik routes, live URL probes
          manifest  resolved paths, compose wiring, dev-stack binding, Cloudflare bindings
          services  attached services (DB, cache, storage) with connection strings
          data      seed data / import plan
        """
        payload = await client.get(f"/api/hosted-apps/{slug.strip()}/snapshot")
        if full:
            return guard_size(payload, settings.max_response_chars)
        wanted = list(sections or ["identity", "runtime", "urls"])
        top_keys: list[str] = []
        manifest_keys: list[str] = []
        for name in wanted:
            top_keys.extend(SNAPSHOT_TOP_KEYS.get(name, ()))
            manifest_keys.extend(SNAPSHOT_MANIFEST_KEYS.get(name, ()))
        out = pick(payload, *top_keys)
        if manifest_keys:
            manifest = pick(payload.get("manifest_ui") or {}, *manifest_keys)
            if manifest:
                out["manifest_ui"] = manifest
        out.setdefault("slug", slug.strip())
        out["_sections_returned"] = wanted
        out["_available_sections"] = SNAPSHOT_SECTIONS
        return guard_size(out, settings.max_response_chars)

    @server.tool(name="leco_app_control", annotations=MUTATING)
    async def leco_app_control(
        slug: str,
        action: Literal[
            "start", "stop", "restart", "pause", "unpause", "deploy", "recreate", "remove", "reset"
        ],
        confirm: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Lifecycle for one hosted app — the buttons on the app's Controls row.

        deploy/stop use `leco-devops` with the app manifest; restart/recreate/pause use
        docker compose directly. `remove` tears the stack down and offboards the app
        (unregister + Traefik strip); `reset` also deletes its volumes. Both require
        confirm=true and LECO_MCP_ALLOW_DESTRUCTIVE=1.
        """
        act = validate_action(action, APP_ACTIONS, label="hosted app")
        target = _target_id(slug)
        guard_destructive(
            settings,
            act,
            confirm,
            what=f"{act} on hosted app {slug!r}"
            + (" — this also deletes its data volumes" if act == "reset" else ""),
        )
        out = await deps.run_stream(
            "/api/control/stream",
            {"target_id": target, "action": act},
            ctx=ctx,
            progress_label=f"{act} {slug}",
        )
        out.update({"slug": slug.strip(), "target_id": target, "action": act,
                    "destructive": is_destructive(act)})
        return guard_size(out, settings.max_response_chars)

    @server.tool(name="leco_app_logs", annotations=READ_ONLY)
    async def leco_app_logs(
        slug: str,
        service: str = "",
        search: str = "",
        tail_lines: int = 300,
        since_seconds: int = 1800,
    ) -> dict[str, Any]:
        """Compose logs for a hosted app.

        service="" (default) covers every compose service in the app; pass a compose service
        name to narrow (see attached_services in leco_app_snapshot).
        """
        payload = await client.get(
            f"/api/hosted-apps/{slug.strip()}/logs",
            params={
                "service": service.strip(),
                "search": search,
                "tail": max(50, min(int(tail_lines), 5000)),
                "since": max(60, min(int(since_seconds), 86400)),
            },
        )
        return guard_size(
            {
                **{k: v for k, v in payload.items() if k != "log"},
                "text": tail(payload.get("log"), settings.max_log_chars),
            },
            settings.max_response_chars,
        )

    @server.tool(name="leco_app_insights", annotations=READ_ONLY)
    async def leco_app_insights(slug: str) -> dict[str, Any]:
        """Detected problems for a hosted app — restart loops, error spikes, routing gaps."""
        return guard_size(
            await client.get(f"/api/hosted-apps/{slug.strip()}/insights"),
            settings.max_response_chars,
        )

    @server.tool(name="leco_app_metrics", annotations=READ_ONLY)
    async def leco_app_metrics(slug: str, limit: int = 60) -> dict[str, Any]:
        """CPU / memory / network time series for one hosted app's containers."""
        return guard_size(
            await client.get(
                f"/api/hosted-apps/{slug.strip()}/metrics/history",
                params={"limit": max(1, min(int(limit), 500))},
            ),
            settings.max_response_chars,
        )

    @server.tool(name="leco_app_validate", annotations=READ_ONLY)
    async def leco_app_validate(slug: str) -> dict[str, Any]:
        """Validate a registered app's manifest + profile against the schema and disk paths.

        Run this when an app deploys but does not route, or after editing leco.app.yaml.
        """
        return guard_size(
            await client.post(
                f"/api/hosted-apps/{slug.strip()}/validate-configuration",
                json_body={},
                authed=False,
            ),
            settings.max_response_chars,
        )

    @server.tool(name="leco_app_bind_dev_stack", annotations=MUTATING)
    async def leco_app_bind_dev_stack(slug: str, dev_stack_id: str = "") -> dict[str, Any]:
        """Bind a hosted app to an isolated dev stack (shared DB/cache) or unbind it.

        Writes platform.devStackId into the app's leco.yaml. Pass dev_stack_id="" to unbind.
        Redeploy the app afterwards for the binding to take effect.
        """
        return await client.post(
            f"/api/hosted-apps/{slug.strip()}/platform-binding",
            json_body={"dev_stack_id": dev_stack_id.strip()},
        )

    @server.tool(name="leco_app_data_import_plan", annotations=READ_ONLY)
    async def leco_app_data_import_plan(slug: str) -> dict[str, Any]:
        """Discover seed data (SQL/dump files) an app ships and what importing it would do."""
        return guard_size(
            await client.get(f"/api/hosted-apps/{slug.strip()}/data-import/discover"),
            settings.max_response_chars,
        )

    @server.tool(name="leco_app_data_import", annotations=MUTATING)
    async def leco_app_data_import(
        slug: str,
        dry_run: bool = True,
        reimport: bool = True,
        selected_ids: list[str] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Import an app's seed data into its running stack (databases, dumps).

        dry_run=True (default) reports the commands without executing them. Set dry_run=False
        to actually import. selected_ids narrows to specific items from
        leco_app_data_import_plan; omit for everything.
        """
        body: dict[str, Any] = {"dry_run": bool(dry_run), "reimport": bool(reimport)}
        if selected_ids is not None:
            body["selected_ids"] = selected_ids
        out = await deps.run_stream(
            f"/api/hosted-apps/{slug.strip()}/data-import/stream",
            body,
            ctx=ctx,
            progress_label=f"data import {slug}",
        )
        out["slug"] = slug.strip()
        out["dry_run"] = bool(dry_run)
        return guard_size(out, settings.max_response_chars)

    @server.tool(name="leco_app_offboard", annotations=MUTATING)
    async def leco_app_offboard(slug: str, confirm: bool = False) -> dict[str, Any]:
        """Remove an app from the ecosystem: unregister, strip Traefik routes, drop registry.

        This does not delete the app's source repo, but it does undo registration. Requires
        confirm=true and LECO_MCP_ALLOW_DESTRUCTIVE=1. To also stop and delete containers,
        use leco_app_control(action="remove") instead.
        """
        guard_explicit_destructive(
            settings, confirm, what=f"offboard hosted app {slug!r} from the ecosystem registry"
        )
        return guard_size(
            await client.post(f"/api/hosted-apps/{slug.strip()}/offboard", json_body={}),
            settings.max_response_chars,
        )
