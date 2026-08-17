"""Traefik routing: generate, merge, and strip route fragments.

Canonical stack routes live in ``traefik/dynamic.yml`` (copied to
``hosting/traefik/01-stack-core.yml`` on Traefik start). Per-app routes are merged into the
writable fragment ``hosting/traefik/dynamic.yml`` — that is what these tools edit.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ..runtime import Deps
from ..safety import guard_explicit_destructive
from ..shaping import guard_size

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)
MUTATING = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False)


def register(server: MCPServer, deps: Deps) -> None:
    client = deps.client
    settings = deps.settings

    @server.tool(name="leco_route_fragment_from_app", annotations=READ_ONLY)
    async def leco_route_fragment_from_app(slug: str) -> dict[str, Any]:
        """Generate the Traefik YAML fragment a registered app's manifest implies.

        Read-only: returns the YAML without merging it. Use this to diff what routes SHOULD
        exist against what leco_traefik_routes reports, then merge with
        leco_route_merge_fragment.
        """
        return guard_size(
            await client.post(
                "/api/traefik/fragment-from-manifest", json_body={"slug": slug.strip()}
            ),
            settings.max_response_chars,
        )

    @server.tool(name="leco_route_merge_fragment", annotations=WRITES)
    async def leco_route_merge_fragment(yaml_fragment: str) -> dict[str, Any]:
        """Merge an http routers/services YAML fragment into hosting/traefik/dynamic.yml.

        Additive: existing keys with the same name are replaced, everything else is kept.
        Traefik reloads the file provider automatically. Pass a fragment shaped like:

            http:
              routers:
                myapp: {rule: "Host(`myapp.lh`)", service: myapp, entryPoints: [websecure], tls: true}
              services:
                myapp: {loadBalancer: {servers: [{url: "http://myapp:8080"}]}}
        """
        if not yaml_fragment.strip():
            raise ValueError("yaml_fragment is required.")
        return await client.post(
            "/api/traefik/merge-fragment", json_body={"yaml": yaml_fragment}
        )

    @server.tool(name="leco_route_strip_keys", annotations=MUTATING)
    async def leco_route_strip_keys(
        routers: list[str] | None = None,
        services: list[str] | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Delete named routers/services from the writable Traefik fragment.

        Removing a route takes an app offline at its hostname, so this needs confirm=true and
        LECO_MCP_ALLOW_DESTRUCTIVE=1. Normal app teardown goes through leco_app_offboard,
        which strips routes for you.
        """
        routers = routers or []
        services = services or []
        if not routers and not services:
            raise ValueError("Provide at least one router or service key to strip.")
        guard_explicit_destructive(
            settings,
            confirm,
            what=f"strip Traefik routers={routers} services={services} from "
            "hosting/traefik/dynamic.yml",
        )
        return await client.post(
            "/api/traefik/strip-keys", json_body={"routers": routers, "services": services}
        )
