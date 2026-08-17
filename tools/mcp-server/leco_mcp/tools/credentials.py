"""UI credential vault — local development logins for stack services.

These tools return and change plaintext credentials for local services (Traefik dashboard,
n8n, Open WebUI, Adminer, SFTP/FTP, dev stacks). They are disabled unless the server is
started with ``LECO_MCP_ALLOW_CREDENTIALS=1``, so an agent cannot read secrets from the
vault as an incidental side effect of another task.

The vault is explicitly local-development only — see docs/UI_CREDENTIAL_VAULT.md.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ..runtime import Deps
from ..safety import guard_credentials, guard_explicit_destructive
from ..shaping import guard_size

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)


def register(server: MCPServer, deps: Deps) -> None:
    client = deps.client
    settings = deps.settings

    @server.tool(name="leco_ui_credentials", annotations=READ_ONLY)
    async def leco_ui_credentials(slug: str = "") -> dict[str, Any]:
        """List services with stored UI credentials, or read one service's login details.

        Requires LECO_MCP_ALLOW_CREDENTIALS=1. Returns plaintext local-dev credentials —
        do not echo them into logs, commits, or anywhere off this machine.

        slug="" lists the catalog; slug="sftp" (or "n8n", "traefik", …) reads one entry.
        """
        guard_credentials(settings)
        if not slug.strip():
            return guard_size(
                await client.get("/api/ui-credentials/catalog"), settings.max_response_chars
            )
        return guard_size(
            await client.get(f"/api/ui-credentials/{slug.strip()}"), settings.max_response_chars
        )

    @server.tool(name="leco_ui_credentials_set", annotations=WRITES)
    async def leco_ui_credentials_set(slug: str, values: dict[str, str]) -> dict[str, Any]:
        """Update stored credentials for a service and apply them where supported.

        For protocol services (SFTP, FTP) this rewrites file-transfer/.env and recreates the
        container, so active sessions drop. Requires LECO_MCP_ALLOW_CREDENTIALS=1.
        """
        guard_credentials(settings)
        if not isinstance(values, dict) or not values:
            raise ValueError("values must be a non-empty object of credential fields.")
        return guard_size(
            await client.put(
                f"/api/ui-credentials/{slug.strip()}",
                json_body={"values": values},
                timeout=settings.action_timeout,
            ),
            settings.max_response_chars,
        )

    @server.tool(name="leco_ui_credentials_reset", annotations=WRITES)
    async def leco_ui_credentials_reset(slug: str, confirm: bool = False) -> dict[str, Any]:
        """Reset a service's credentials back to the registry defaults and apply them.

        This invalidates the current login for that service, so it needs confirm=true and
        LECO_MCP_ALLOW_DESTRUCTIVE=1 in addition to LECO_MCP_ALLOW_CREDENTIALS=1.
        """
        guard_credentials(settings)
        guard_explicit_destructive(
            settings, confirm, what=f"reset stored credentials for {slug!r} to defaults"
        )
        return guard_size(
            await client.post(
                f"/api/ui-credentials/{slug.strip()}/reset",
                json_body={},
                timeout=settings.action_timeout,
            ),
            settings.max_response_chars,
        )
