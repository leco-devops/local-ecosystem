"""Platform tab: deployment mode, ecosystem bundles, and isolated dev stacks.

Platform settings live in ``config/leco-platform.yaml`` (deployment mode, base domain, TLS
mode, which ecosystem services are enabled). Dev stacks are generated Compose projects under
``platform/dev-stacks/<id>/`` with their own network and Traefik routes — one per CMS,
framework, or customer.
"""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations

from ..runtime import Deps
from ..safety import (
    DEV_STACK_ACTIONS,
    PLATFORM_SERVICE_ACTIONS,
    guard_destructive,
    validate_action,
)
from ..shaping import guard_size, pick

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)
MUTATING = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False)


def register(server: MCPServer, deps: Deps) -> None:
    client = deps.client
    settings = deps.settings

    @server.tool(name="leco_platform_config", annotations=READ_ONLY)
    async def leco_platform_config(set_config: dict[str, Any] | None = None) -> dict[str, Any]:
        """Read or replace platform settings (deployment mode, base domain, TLS, services).

        Call with no arguments to read config/leco-platform.yaml. Pass set_config with the
        FULL config object to write it — this replaces the file, so read first and merge.

        Changing base_domain or TLS mode affects every route on the machine; apply the new
        Traefik config afterwards with leco_platform_traefik_apply.
        """
        if set_config is not None:
            if not isinstance(set_config, dict):
                raise ValueError("set_config must be the full platform config object.")
            return await client.post("/api/platform/config", json_body={"config": set_config})
        return guard_size(await client.get("/api/platform/config"), settings.max_response_chars)

    @server.tool(name="leco_platform_catalog", annotations=READ_ONLY)
    async def leco_platform_catalog(
        section: Literal["all", "profiles", "bundles", "components", "presets"] = "all",
    ) -> dict[str, Any]:
        """What the platform can install: install profiles, service bundles, dev-stack presets.

        section=presets lists the one-click dev stacks (WordPress, Magento, Laravel, LAMP …)
        you can pass to leco_dev_stack_create.
        """
        payload = await client.get("/api/platform/catalog")
        if section == "all":
            return guard_size(
                {
                    "profiles": list((payload.get("profiles") or {}).keys()),
                    "bundles": list((payload.get("bundles") or {}).keys()),
                    "start_order": payload.get("start_order"),
                    "components": list((payload.get("components") or {}).keys()),
                    "dev_stack_presets": [
                        pick(p, "id", "name", "label", "description")
                        if isinstance(p, dict)
                        else p
                        for p in (payload.get("dev_stack_presets") or [])
                    ],
                    "_hint": "Call with section=profiles|bundles|components|presets for detail.",
                },
                settings.max_response_chars,
            )
        key = {"presets": "dev_stack_presets"}.get(section, section)
        return guard_size({key: payload.get(key)}, settings.max_response_chars)

    @server.tool(name="leco_platform_services", annotations=READ_ONLY)
    async def leco_platform_services() -> dict[str, Any]:
        """Which ecosystem services the platform has enabled, and whether they are running."""
        return guard_size(await client.get("/api/platform/services"), settings.max_response_chars)

    @server.tool(name="leco_platform_service_action", annotations=WRITES)
    async def leco_platform_service_action(
        service_id: str,
        action: Literal["install", "start", "stop", "disable"],
    ) -> dict[str, Any]:
        """Install, start, stop, or disable a platform-managed ecosystem service.

        service_id: from leco_platform_services (e.g. "ollama", "paperclip", "cloudflare-local").

        "install"/"disable" change whether the service is enabled in leco-platform.yaml;
        "start"/"stop" only affect the running container. For per-container lifecycle across
        the whole machine (including infra add-ons and Cloudflare adapters) use leco_control.
        """
        act = validate_action(action, PLATFORM_SERVICE_ACTIONS, label="platform service")
        return guard_size(
            await client.post(
                f"/api/platform/services/{service_id.strip()}/action",
                json_body={"action": act},
                timeout=settings.action_timeout,
            ),
            settings.max_response_chars,
        )

    @server.tool(name="leco_platform_traefik_apply", annotations=WRITES)
    async def leco_platform_traefik_apply() -> dict[str, Any]:
        """Regenerate and apply Traefik routes from current platform settings.

        Run this after changing base_domain or TLS mode, or when new platform hosts do not
        resolve. This is also the fix for a global 404 caused by a stale
        hosting/traefik/01-stack-core.yml.
        """
        return guard_size(
            await client.post(
                "/api/platform/traefik/apply", json_body={}, timeout=settings.action_timeout
            ),
            settings.max_response_chars,
        )

    # ------------------------------------------------------------------ dev stacks

    @server.tool(name="leco_dev_stacks", annotations=READ_ONLY)
    async def leco_dev_stacks() -> dict[str, Any]:
        """List isolated dev stacks with their status, components, and URLs."""
        return guard_size(await client.get("/api/dev-stacks"), settings.max_response_chars)

    @server.tool(name="leco_dev_stack_create", annotations=WRITES)
    async def leco_dev_stack_create(
        stack_id: str,
        name: str = "",
        preset: str = "",
        template: str = "",
        components: list[str] | None = None,
        sample_data: bool = False,
    ) -> dict[str, Any]:
        """Generate a new isolated dev stack (its own Compose project, network, and routes).

        Provide exactly one of:
          preset      one-click stack from leco_platform_catalog(section="presets")
          template    framework template (WordPress, Magento, Laravel, …)
          components  explicit component list, e.g. ["mysql", "redis", "php-fpm", "nginx"]

        sample_data=true seeds the stack with demo content where the template supports it.
        Creating does not start it — call leco_dev_stack_action(action="start") next.
        """
        if not stack_id.strip():
            raise ValueError("stack_id is required.")
        if not preset and not template and not components:
            raise ValueError("Provide one of: preset, template, or components[].")
        body: dict[str, Any] = {
            "id": stack_id.strip(),
            "name": name.strip() or stack_id.strip(),
            "sample_data": bool(sample_data),
        }
        if preset:
            body["preset"] = preset.strip()
        if template:
            body["template"] = template.strip()
        if components:
            body["components"] = components
        return guard_size(
            await client.post("/api/dev-stacks", json_body=body, timeout=settings.action_timeout),
            settings.max_response_chars,
        )

    @server.tool(name="leco_dev_stack_action", annotations=MUTATING)
    async def leco_dev_stack_action(
        stack_id: str,
        action: Literal["start", "stop", "repair", "redeploy", "reinstall", "destroy"],
        confirm: bool = False,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Run a lifecycle action on a dev stack, streaming live output.

        start/stop/repair/redeploy are safe — repair fixes routing and network attachment in
        place without touching data.

        reinstall regenerates the stack from its template (data is recreated) and destroy
        deletes the stack and its volumes. Both require confirm=true and
        LECO_MCP_ALLOW_DESTRUCTIVE=1.
        """
        act = validate_action(action, DEV_STACK_ACTIONS, label="dev stack")
        guard_destructive(
            settings,
            act,
            confirm,
            what=f"{act} on dev stack {stack_id!r} — this deletes stack data",
            stack=True,
        )
        out = await deps.run_stream(
            f"/api/dev-stacks/{stack_id.strip()}/action/stream",
            {"action": act},
            ctx=ctx,
            progress_label=f"{act} {stack_id}",
        )
        out.update({"stack_id": stack_id.strip(), "action": act})
        return guard_size(out, settings.max_response_chars)

    @server.tool(name="leco_dev_stack_snapshot", annotations=READ_ONLY)
    async def leco_dev_stack_snapshot(stack_id: str) -> dict[str, Any]:
        """Detailed state of one dev stack: services, containers, health, generated files."""
        return guard_size(
            await client.get(f"/api/dev-stacks/{stack_id.strip()}/snapshot"),
            settings.max_response_chars,
        )

    @server.tool(name="leco_dev_stack_access", annotations=READ_ONLY)
    async def leco_dev_stack_access(stack_id: str) -> dict[str, Any]:
        """URLs, admin logins, and DB endpoints for a dev stack.

        Returns local development credentials — treat the output as sensitive.
        """
        return guard_size(
            await client.get(f"/api/dev-stacks/{stack_id.strip()}/access"),
            settings.max_response_chars,
        )

    @server.tool(name="leco_dev_stack_files", annotations=READ_ONLY)
    async def leco_dev_stack_files(
        stack_id: str, path: str = "", write_content: str | None = None
    ) -> dict[str, Any]:
        """List, read, or write a dev stack's generated config files.

        path=""                     list the stack's editable files
        path="docker-compose.yml"   read that file
        write_content=...           overwrite it (redeploy the stack for changes to apply)
        """
        sid = stack_id.strip()
        if not path.strip():
            return guard_size(
                await client.get(f"/api/dev-stacks/{sid}/config"), settings.max_response_chars
            )
        if write_content is None:
            return guard_size(
                await client.get(f"/api/dev-stacks/{sid}/files", params={"path": path.strip()}),
                settings.max_response_chars,
            )
        return await client.put(
            f"/api/dev-stacks/{sid}/files",
            json_body={"path": path.strip(), "content": write_content},
        )

    @server.tool(name="leco_dev_stack_reset_admin", annotations=WRITES)
    async def leco_dev_stack_reset_admin(stack_id: str) -> dict[str, Any]:
        """Reset the template admin password for a dev stack (WordPress, Magento, …).

        Returns the new credentials. Does not touch stack data beyond the admin account.
        """
        return guard_size(
            await client.post(
                f"/api/dev-stacks/{stack_id.strip()}/reset-admin",
                json_body={},
                timeout=settings.action_timeout,
            ),
            settings.max_response_chars,
        )
