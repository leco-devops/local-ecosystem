"""In-app documentation, operator/developer help, and the ecosystem update catalog.

Lets an agent look up how LEco expects something to be done before doing it, instead of
guessing at manifest fields or repair procedures.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ..runtime import Deps
from ..shaping import clip, guard_size, pick

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)


def register(server: MCPServer, deps: Deps) -> None:
    client = deps.client
    settings = deps.settings

    @server.tool(name="leco_docs", annotations=READ_ONLY)
    async def leco_docs(doc_id: str = "", category: str = "") -> dict[str, Any]:
        """Architecture and operations docs served by the dashboard Docs tab.

        Call with no arguments to list available modules (id, title, category, path).
        Pass doc_id to read one — e.g. "ecosystem-readme", "leco-app-blueprint".
        """
        if not doc_id.strip():
            payload = await client.get("/api/docs/catalog")
            modules = payload.get("modules") or []
            if category:
                c = category.lower()
                modules = [m for m in modules if c in str(m.get("category") or "").lower()]
            return guard_size(
                {
                    "count": len(modules),
                    "categories": sorted(
                        {str(m.get("category") or "") for m in payload.get("modules") or []}
                    ),
                    "modules": [
                        pick(m, "id", "title", "category", "blurb", "path", "available")
                        for m in modules
                    ],
                },
                settings.max_response_chars,
            )
        payload = await client.get("/api/docs/content", params={"id": doc_id.strip()})
        if isinstance(payload.get("content"), str):
            payload["content"] = clip(payload["content"], settings.max_response_chars - 2000)
        elif isinstance(payload.get("markdown"), str):
            payload["markdown"] = clip(payload["markdown"], settings.max_response_chars - 2000)
        return guard_size(payload, settings.max_response_chars)

    @server.tool(name="leco_help", annotations=READ_ONLY)
    async def leco_help(topic_id: str = "", search: str = "") -> dict[str, Any]:
        """Operator and developer manuals (the dashboard Help tab).

        No arguments   → the help tree (ids and titles)
        search="502"   → full-text search across every help page
        topic_id="..." → read one page, e.g. "install-stack", "dash-platform"
        """
        if search.strip():
            return guard_size(
                await client.get("/api/help/search", params={"q": search.strip()}),
                settings.max_response_chars,
            )
        if not topic_id.strip():
            return guard_size(await client.get("/api/help/tree"), settings.max_response_chars)
        payload = await client.get("/api/help/content", params={"id": topic_id.strip()})
        for key in ("content", "markdown", "body"):
            if isinstance(payload.get(key), str):
                payload[key] = clip(payload[key], settings.max_response_chars - 2000)
        return guard_size(payload, settings.max_response_chars)

    @server.tool(name="leco_updates", annotations=READ_ONLY)
    async def leco_updates(unread_only: bool = False) -> dict[str, Any]:
        """Tracked ecosystem, stack, and upstream image updates from the update catalog."""
        payload = await client.get("/api/update-catalog/panel")
        if unread_only and isinstance(payload.get("updates"), list):
            payload["updates"] = [u for u in payload["updates"] if not u.get("read")]
        return guard_size(payload, settings.max_response_chars)

    @server.tool(name="leco_updates_mark_read", annotations=WRITES)
    async def leco_updates_mark_read() -> dict[str, Any]:
        """Mark every tracked ecosystem update as read."""
        return await client.post("/api/update-catalog/mark-read", json_body={})
