"""CLI entry point: ``leco-mcp [stdio|http|doctor]``."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .activity import ActivityLog
from .client import LecoClient
from .config import Settings
from .server import SERVER_NAME, SERVER_VERSION, build_server


def _doctor(settings: Settings) -> int:
    """Print a connectivity + configuration report without starting a transport."""

    async def probe() -> dict:
        client = LecoClient(settings)
        try:
            url = await client.base_url()
            version = await client.get("/api/version")
            targets = await client.get("/api/control/targets")
            return {
                "reachable": True,
                "dashboard_url": url,
                "platform_version": version.get("version"),
                "control_token_required_by_dashboard": targets.get("token_required"),
                "control_targets": len(targets.get("targets") or []),
            }
        except Exception as exc:  # noqa: BLE001 - the whole point is to report failure
            return {"reachable": False, "error": str(exc)}
        finally:
            await client.aclose()

    activity = ActivityLog(
        settings.activity_log_path or None, max_events=settings.activity_max_events
    )
    report = {
        "server": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "settings": settings.describe(),
        "dashboard": asyncio.run(probe()),
        "activity": {
            "enabled": activity.enabled,
            "log_path": str(activity.path) if activity.path else None,
            "writable": activity.writable(),
            "event_count": activity.event_count(),
            "max_events": settings.activity_max_events,
        },
    }
    tools = build_server(settings)._tool_manager.list_tools()  # noqa: SLF001 - diagnostics
    report["tools"] = sorted(t.name for t in tools)
    report["tool_count"] = len(tools)

    dashboard = report["dashboard"]
    if dashboard.get("reachable") and dashboard.get("control_token_required_by_dashboard") and not settings.has_token:
        report["warning"] = (
            "The dashboard enforces a control token but none is configured here — "
            "read-only tools will work, control actions will return 401. "
            "Set LECO_MCP_CONTROL_TOKEN to the dashboard's DASHBOARD_CONTROL_TOKEN."
        )

    print(json.dumps(report, indent=2))
    return 0 if report["dashboard"].get("reachable") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="leco-mcp",
        description="MCP server for LEco DevOps — deployment, onboarding, infrastructure "
        "control, and monitoring for AI agents.",
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default="stdio",
        choices=("stdio", "http", "doctor"),
        help="stdio (default) for local clients such as Claude Code; http for a shared "
        "streamable-HTTP endpoint; doctor to print a connectivity report and exit.",
    )
    parser.add_argument("--host", default=None, help="HTTP bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="HTTP port (default 8099)")
    parser.add_argument("--path", default=None, help="HTTP route path (default /mcp)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {SERVER_VERSION}")
    args = parser.parse_args(argv)

    settings = Settings.from_env()

    if args.mode == "doctor":
        return _doctor(settings)

    server = build_server(settings)

    if args.mode == "stdio":
        server.run("stdio")
        return 0

    host = args.host or settings.http_host
    port = args.port or settings.http_port
    path = args.path or settings.http_path
    # stdout is the stdio transport channel, so status goes to stderr in every mode.
    print(
        f"leco-mcp {SERVER_VERSION} listening on http://{host}:{port}{path}",
        file=sys.stderr,
    )
    server.run(
        "streamable-http",
        host=host,
        port=port,
        streamable_http_path=path,
        stateless_http=settings.http_stateless,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
