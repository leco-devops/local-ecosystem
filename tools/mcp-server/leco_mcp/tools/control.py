"""Infrastructure control: start / stop / restart / deploy every stack service.

Mirrors the dashboard **Control** tab. Targets cover ecosystem-stack services (Traefik,
Postgres, Ollama, AirLLM, n8n, Paperclip, update-catalog), infra add-ons (MySQL, Redis,
Mailpit, Adminer, cache lab), file transfer (SFTP/FTP/browser), Cloudflare-local adapters,
hosted LEco apps, and the bulk ``stack-*-all`` pseudo-targets.
"""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations

from ..runtime import Deps
from ..safety import CONTROL_ACTIONS, guard_destructive, is_destructive, validate_action
from ..shaping import compact_target, guard_size, tail

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)
MUTATING = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False)

BULK_TARGETS = {
    "stack-ecosystem-all": "every ecosystem-stack service",
    "stack-infra-all": "the whole infra compose stack",
    "stack-file-transfer-all": "the whole file-transfer compose stack",
    "stack-cf-all": "the whole cloudflare-local stack",
}


def register(server: MCPServer, deps: Deps) -> None:
    client = deps.client
    settings = deps.settings

    @server.tool(name="leco_control_targets", annotations=READ_ONLY)
    async def leco_control_targets(
        group: str = "",
        running: Literal["any", "yes", "no"] = "any",
        query: str = "",
    ) -> dict[str, Any]:
        """List everything that can be started, stopped, or deployed, with live status.

        group  filters by group: ecosystem, ecosystem-stack, infra, cloudflare-local, leco-apps
        running filters by current state
        query  matches the target id or label (substring, case-insensitive)

        Returns each target's id, current runtime state, and the actions it accepts. Use the
        returned id with leco_control.
        """
        payload = await client.get("/api/control/targets")
        rows = [compact_target(t) for t in payload.get("targets") or []]
        if group:
            g = group.lower()
            rows = [r for r in rows if g in str(r.get("group") or "").lower()]
        if running == "yes":
            rows = [r for r in rows if r.get("running") is True]
        elif running == "no":
            rows = [r for r in rows if r.get("running") is not True]
        if query:
            q = query.lower()
            rows = [
                r
                for r in rows
                if q in str(r.get("id") or "").lower() or q in str(r.get("label") or "").lower()
            ]
        return guard_size(
            {
                "generated_at": payload.get("generated_at"),
                "token_required": payload.get("token_required"),
                "count": len(rows),
                "bulk_targets": BULK_TARGETS,
                "targets": rows,
            },
            settings.max_response_chars,
        )

    @server.tool(name="leco_control", annotations=MUTATING)
    async def leco_control(
        target_id: str,
        action: Literal[
            "start",
            "stop",
            "restart",
            "pause",
            "unpause",
            "deploy",
            "recreate",
            "backup",
            "staging",
            "remove",
            "reset",
        ],
        confirm: bool = False,
        stream: bool = True,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Run a lifecycle action against one control target (turn infrastructure on/off).

        target_id: from leco_control_targets — e.g. "ai-ollama", "infra-mysql",
                   "cf-r2-adapter", "ft-sftp", "leco-stack-<app>", or a bulk
                   "stack-ecosystem-all" / "stack-infra-all" / "stack-cf-all".

        Non-destructive actions run without extra ceremony. `remove` (delete container) and
        `reset` (delete container AND its data volume) require confirm=true and a server
        started with LECO_MCP_ALLOW_DESTRUCTIVE=1.

        stream=True (default) streams compose/script output live and returns the tail.
        """
        act = validate_action(action, CONTROL_ACTIONS, label="control")
        target = target_id.strip()
        if not target:
            raise ValueError("target_id is required — call leco_control_targets first.")
        guard_destructive(
            settings,
            act,
            confirm,
            what=f"{act} on control target {target!r}"
            + (f" ({BULK_TARGETS[target]})" if target in BULK_TARGETS else ""),
        )

        body = {"target_id": target, "action": act}
        if stream:
            out = await deps.run_stream(
                "/api/control/stream",
                body,
                ctx=ctx,
                progress_label=f"{act} {target}",
            )
        else:
            raw = await client.post(
                "/api/control", json_body=body, timeout=settings.action_timeout
            )
            out = {
                "ok": bool(raw.get("ok")),
                "result": {k: v for k, v in raw.items() if k != "log"},
                "log": tail(raw.get("log"), settings.max_log_chars),
            }
        out["target_id"] = target
        out["action"] = act
        out["destructive"] = is_destructive(act)
        return guard_size(out, settings.max_response_chars)

    @server.tool(name="leco_control_policies", annotations=READ_ONLY)
    async def leco_control_policies(
        set_policies: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Read or update per-target default start policies.

        A policy decides how a target behaves during a bulk stack start. Valid values are
        "start", "stop", and "offloaded". Call with no arguments to read; pass
        set_policies={"ai-ollama": "offloaded"} to update (merged, not replaced).
        """
        if set_policies:
            return await client.put(
                "/api/control/default-policies", json_body={"policies": set_policies}
            )
        return await client.get("/api/control/default-policies")
