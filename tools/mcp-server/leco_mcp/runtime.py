"""Shared dependencies handed to every tool module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver import Context

from .client import LecoClient
from .config import Settings
from .shaping import stream_result


@dataclass
class Deps:
    """Settings + dashboard client, passed to each ``register()`` at build time."""

    settings: Settings
    client: LecoClient

    async def run_stream(
        self,
        path: str,
        body: dict[str, Any],
        *,
        ctx: Context | None = None,
        timeout: float | None = None,
        progress_label: str = "",
    ) -> dict[str, Any]:
        """Consume an NDJSON action stream, mirroring log lines to the MCP client.

        Streaming is what makes long deploys usable: the caller sees compose output as it
        happens instead of waiting blind for a 10-minute call to return.
        """
        logs: list[str] = []
        result: dict[str, Any] | None = None
        events = 0
        async for event in self.client.stream_ndjson(
            path, json_body=body, timeout=timeout or self.settings.action_timeout
        ):
            kind = event.get("type")
            if kind == "log":
                text = str(event.get("text") or "")
                logs.append(text)
                events += 1
                # Progress notifications carry the line itself, so a client that supports
                # them sees compose output live. (The logging capability is deprecated in
                # the SDK, so progress is the supported channel for this.)
                if ctx is not None and text.strip():
                    try:
                        await ctx.report_progress(
                            events,
                            None,
                            f"{progress_label}: {text.strip()}" if progress_label else text.strip(),
                        )
                    except Exception:  # noqa: BLE001 - never fail an action over reporting
                        pass
            elif kind == "done":
                raw = event.get("result")
                result = raw if isinstance(raw, dict) else {"ok": bool(raw)}
            else:
                # Unknown event kinds are kept verbatim so new dashboard event types are
                # visible rather than silently dropped.
                logs.append(f"[{kind}] {event}\n")
                events += 1
        return stream_result(
            logs, result, max_log_chars=self.settings.max_log_chars, events=events
        )
