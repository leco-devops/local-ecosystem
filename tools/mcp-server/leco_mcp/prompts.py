"""Reusable workflow prompts exposed to MCP clients.

These encode the order LEco expects operations in, so an agent driving the stack for the
first time does not have to infer it from tool names alone.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer


def register(server: MCPServer) -> None:
    @server.prompt(name="onboard_app")
    def onboard_app(path: str, app_id: str = "") -> str:
        """Onboard an application repository onto LEco and verify it serves."""
        return (
            f"Onboard the application at {path!r} onto LEco DevOps"
            + (f" as app id {app_id!r}" if app_id else "")
            + ".\n\n"
            "Follow this order:\n"
            "1. leco_server_info — confirm the dashboard is reachable.\n"
            "2. leco_detect(path) — review archetype, compose files, host ports, and "
            "main_url_warnings. If there is no compose or wrangler signal, say so before "
            "continuing: the app will not route without infrastructure.\n"
            "3. leco_onboard(path, app_id) — this generates the manifest, registers the app, "
            "merges Traefik routes, and deploys.\n"
            "4. Verify: leco_app_snapshot(slug, sections=['runtime','urls']). If a URL does "
            "not answer, use leco_app_logs and leco_app_validate to find out why rather than "
            "redeploying blindly.\n"
            "5. Report the main URL, the container state, and anything you had to fix."
        )

    @server.prompt(name="diagnose_stack")
    def diagnose_stack(symptom: str = "") -> str:
        """Diagnose a broken or degraded LEco stack."""
        return (
            "Diagnose the LEco DevOps stack"
            + (f". Reported symptom: {symptom}" if symptom else "")
            + ".\n\n"
            "1. leco_status(detail='services') — note the level, missing services, and alerts.\n"
            "2. leco_urls(only_unhealthy=True) — which endpoints fail their probe.\n"
            "3. For each failing service: leco_logs(container, level='error').\n"
            "4. If routes 404 globally, check leco_traefik_routes and consider "
            "leco_platform_traefik_apply (regenerates hosting/traefik/01-stack-core.yml).\n"
            "5. If a hosted app 502s, its containers are probably off lh-network — check "
            "leco_app_snapshot(slug, sections=['runtime','urls']) and leco_app_validate.\n"
            "Report root cause and the minimal fix. Do not run destructive actions while "
            "diagnosing."
        )

    @server.prompt(name="bring_up_stack")
    def bring_up_stack(profile: str = "core") -> str:
        """Start the ecosystem stack and confirm it is healthy."""
        return (
            f"Bring up the LEco DevOps stack ({profile} services) and confirm health.\n\n"
            "1. leco_control_targets(running='no') — see what is down.\n"
            "2. Start Traefik first, then dependencies before dependents (postgres before "
            "n8n, paperclip-postgres before paperclip). leco_control(target_id, 'start') "
            "streams progress. For everything at once, use target_id='stack-ecosystem-all'.\n"
            "3. leco_status() until level is healthy, then leco_urls(only_unhealthy=True).\n"
            "4. Report what started, what stayed down, and why."
        )
