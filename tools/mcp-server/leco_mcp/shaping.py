"""Response shaping.

Dashboard payloads are built for a browser: ``/api/overview`` alone is ~56 KB and a hosted
app snapshot carries compose files, metrics series, and connection strings. Returning those
verbatim would burn an agent's context on one call, so every tool returns a compact view by
default and exposes a ``detail`` knob for the full payload.
"""

from __future__ import annotations

import json
from typing import Any


def pick(source: Any, *keys: str) -> dict[str, Any]:
    """Subset of a dict, skipping keys that are absent."""
    if not isinstance(source, dict):
        return {}
    return {k: source[k] for k in keys if k in source}


def clip(text: Any, limit: int) -> str:
    """Head-clip a string, marking how much was dropped."""
    s = "" if text is None else str(text)
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n… [{len(s) - limit} more characters truncated]"


def tail(text: Any, limit: int) -> str:
    """Tail-clip a string — for command output the end matters most."""
    s = "" if text is None else str(text)
    if len(s) <= limit:
        return s
    return f"… [{len(s) - limit} earlier characters truncated]\n" + s[-limit:]


def guard_size(payload: dict[str, Any], max_chars: int) -> dict[str, Any]:
    """Final backstop so no tool result can blow past the configured budget."""
    try:
        encoded = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return payload
    if len(encoded) <= max_chars:
        return payload
    return {
        "truncated": True,
        "reason": (
            f"Response was {len(encoded)} characters, above the {max_chars} limit. "
            "Re-run with a narrower filter, or raise LECO_MCP_MAX_RESPONSE_CHARS."
        ),
        "preview": encoded[:max_chars],
    }


# --------------------------------------------------------------------- overview


def summarize_system(payload: dict[str, Any]) -> dict[str, Any]:
    """Health headline from ``/api/overview`` — the part an operator reads first."""
    status = payload.get("system_status") or {}
    docker = (payload.get("docker_overview") or {}).get("counts") or {}
    return {
        "generated_at": payload.get("generated_at"),
        "level": status.get("level"),
        "services": {
            "total": status.get("services_total"),
            "running": status.get("services_running"),
            "missing": status.get("services_missing"),
            "paused": status.get("services_paused"),
            "missing_names": status.get("services_missing_names") or [],
        },
        "urls": {
            "total": status.get("total_urls"),
            "healthy": status.get("healthy_urls"),
            "unhealthy": status.get("unhealthy_urls"),
        },
        "resources": {
            "cpu_percent": status.get("aggregate_cpu_percent"),
            "memory_percent": status.get("aggregate_memory_percent"),
            "memory_usage": status.get("aggregate_memory_usage"),
            "memory_limit": status.get("aggregate_memory_limit"),
        },
        "errors": {
            "total_error_lines": status.get("total_error_lines"),
            "error_rate_per_min": status.get("total_error_rate_per_min"),
        },
        "alerts": (status.get("alerts") or [])[:20],
        "docker": {
            "available": (payload.get("docker_overview") or {}).get("docker_available"),
            "counts": docker,
        },
    }


def summarize_services(payload: dict[str, Any], *, limit: int = 200) -> list[dict[str, Any]]:
    """One compact row per service from ``/api/overview``."""
    rows: list[dict[str, Any]] = []
    for svc in (payload.get("services") or [])[:limit]:
        info = svc.get("container_info") or {}
        checks = svc.get("url_checks") or []
        rows.append(
            {
                "service": svc.get("service"),
                "container": svc.get("container"),
                "status": info.get("status") or info.get("state"),
                "running": info.get("running"),
                "urls": [
                    {
                        "url": c.get("url"),
                        "ok": c.get("ok"),
                        "status": c.get("status_code") or c.get("status"),
                    }
                    for c in checks[:6]
                ],
                "notes": svc.get("notes"),
                "hub_slug": svc.get("hub_slug"),
            }
        )
    return rows


# ---------------------------------------------------------------- control tools


def compact_target(target: dict[str, Any]) -> dict[str, Any]:
    runtime = target.get("runtime") or {}
    return {
        "id": target.get("id"),
        "label": target.get("label"),
        "group": target.get("group"),
        "container": target.get("container"),
        "status": runtime.get("status"),
        "running": runtime.get("running"),
        "state": runtime.get("label"),
        "actions": target.get("actions") or [],
        "default_policy": target.get("default_policy"),
    }


def compact_hosted_app(app: dict[str, Any]) -> dict[str, Any]:
    runtime = app.get("runtime") or {}
    return {
        "slug": app.get("id"),
        "label": app.get("label"),
        "target_id": app.get("target_id"),
        "status": runtime.get("status"),
        "running": runtime.get("running"),
        "state": runtime.get("label"),
        "main_url": app.get("main_url"),
        "pending_registration": app.get("pending_registration"),
        "archetype": app.get("localhost_archetype"),
        "application_version": app.get("application_version"),
        "routes": [
            {"hostname": r.get("hostname"), "backend": r.get("backend")}
            for r in (app.get("routes") or [])[:10]
        ],
    }


# ------------------------------------------------------------------- streaming


def stream_result(
    logs: list[str],
    result: dict[str, Any] | None,
    *,
    max_log_chars: int,
    events: int = 0,
) -> dict[str, Any]:
    """Normalize a consumed NDJSON stream into one tool result."""
    joined = "".join(logs)
    out: dict[str, Any] = {
        "ok": bool((result or {}).get("ok", False)),
        "log_events": events,
        "log": tail(joined, max_log_chars),
    }
    if result:
        # ``log`` inside the result duplicates what we already streamed; drop it.
        out["result"] = {k: v for k, v in result.items() if k not in ("log", "logs")}
        if not result.get("ok") and result.get("error"):
            out["error"] = result.get("error")
    else:
        out["ok"] = False
        out["error"] = "Stream ended without a final result event."
    return out
