"""
MCP insights — which AI agents are connected over MCP, what they are doing, and to which apps.

Read-only aggregation over three inputs, every one of them optional:

1. ``ecosystem-stack/config/generated/mcp-activity.jsonl`` — append-only telemetry written by
   the MCP server (``tools/mcp-server``). Absent until the server has run at least once, which
   is the *normal* state on a fresh machine, so nothing here may raise when it is missing.
2. A best-effort HTTP probe of the running server (``http://leco-mcp:8099/insights``) for live
   session state, version and gate configuration. Short timeout; failure is not an error.
3. The Docker container state for ``leco-mcp`` and the hosted-app fleet, used to join MCP
   activity onto real applications.

Everything is defensive by construction: a malformed line is counted and skipped, a truncated
tail is expected, an unreachable probe downgrades the payload instead of failing it. The panel
has to render on a machine that has never set any of this up — that is the primary case.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ``requests``, ``docker`` and ``hosted_apps`` are imported lazily inside the functions that
# need them, so this module stays importable (and unit-testable) with nothing but the stdlib.

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONTAINER_NAME = "leco-mcp"
ACTIVITY_LOG_REL = "ecosystem-stack/config/generated/mcp-activity.jsonl"

DEFAULT_WINDOW_HOURS = 24
MAX_WINDOW_HOURS = 720
DEFAULT_RECENT_LIMIT = 50
DEFAULT_ACTIVITY_LIMIT = 100
MAX_LIMIT = 500

# Bounded tail read: the log is append-only and can grow without limit, so we never
# read the whole file. 4 MB of tail is ~15-25k events, far more than any window needs.
MAX_TAIL_BYTES = 4 * 1024 * 1024
MAX_TAIL_LINES = 20000

PROBE_TOTAL_BUDGET_SEC = 2.5
PROBE_TIMEOUT = (1.0, 2.0)  # (connect, read)

REPOSITORY_URL = "https://github.com/leco-devops/local-ecosystem"
PLUGIN_REL = "tools/claude-plugin"
DEFAULT_PLUGIN_NAME = "leco"
DEFAULT_SKILL_NAME = "operate"
SKILL_REL = f"tools/claude-plugin/skills/{DEFAULT_SKILL_NAME}"
MCP_PACKAGE_REL = "tools/mcp-server"

ENDPOINTS = {
    "http": "https://mcp.lh/mcp",
    "host": "http://localhost:8099/mcp",
    "stdio": "leco-mcp stdio",
}

# Keys whose values never leave this process. args_summary is written by the MCP server and
# should already be scrubbed, but the dashboard is the last line of defence.
_SENSITIVE_KEY_RE = re.compile(
    r"(pass(word|wd)?|secret|token|credential|authorization|api[_-]?key|access[_-]?key|private[_-]?key)",
    re.IGNORECASE,
)
_MAX_SUMMARY_KEYS = 24
_MAX_SUMMARY_VALUE_CHARS = 240

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Paths (resolved per call so tests can repoint DASHBOARD_PROJECT_ROOT)
# ---------------------------------------------------------------------------


def project_root() -> Path:
    return Path(os.getenv("DASHBOARD_PROJECT_ROOT", "/project"))


def activity_log_path() -> Path:
    override = (os.getenv("LECO_MCP_ACTIVITY_LOG") or "").strip()
    if override:
        return Path(override)
    return project_root() / ACTIVITY_LOG_REL


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def clamp_int(raw: Any, default: int, low: int, high: int) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _truthy(raw: Any) -> bool:
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


def parse_ts(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp, tolerating ``Z`` and naive values (assumed UTC)."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sort_key(event: dict[str, Any]) -> datetime:
    """Events without a usable timestamp sort to the very end (and out of any window)."""
    return event.get("_ts") or _EPOCH


def _redact(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "…"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= _MAX_SUMMARY_KEYS:
                out["…"] = f"+{len(value) - _MAX_SUMMARY_KEYS} more"
                break
            name = str(key)
            out[name] = "***" if _SENSITIVE_KEY_RE.search(name) else _redact(item, depth + 1)
        return out
    if isinstance(value, list):
        return [_redact(item, depth + 1) for item in value[:_MAX_SUMMARY_KEYS]]
    if isinstance(value, str) and len(value) > _MAX_SUMMARY_VALUE_CHARS:
        return value[:_MAX_SUMMARY_VALUE_CHARS] + "…"
    return value


# ---------------------------------------------------------------------------
# Activity log
# ---------------------------------------------------------------------------


def read_tail_lines(
    path: Path,
    *,
    max_bytes: int = MAX_TAIL_BYTES,
    max_lines: int = MAX_TAIL_LINES,
) -> tuple[list[str], bool]:
    """Return ``(lines, last_line_may_be_partial)`` from the tail of ``path``.

    Never loads more than ``max_bytes``. When the read starts mid-file the first (partial)
    line is dropped. A file not ending in a newline has a last line that may still be
    mid-write, which the caller must not count as malformed.
    """
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        start = max(0, size - max_bytes)
        handle.seek(start)
        data = handle.read()

    ends_clean = data.endswith(b"\n")
    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if start > 0 and lines:
        lines = lines[1:]  # partial head line from seeking into the middle of a record
    if lines and lines[-1] == "":
        lines.pop()  # trailing newline artefact
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    return lines, (not ends_clean) and bool(lines)


def load_events(path: Path | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load the activity log tail. Returns ``(events, log_stats)``; never raises."""
    target = path or activity_log_path()
    stats: dict[str, Any] = {
        "path": str(target),
        "exists": False,
        "events": 0,
        "malformed_lines": 0,
    }
    try:
        if not target.is_file():
            return [], stats
        stats["exists"] = True
        lines, last_partial = read_tail_lines(target)
    except OSError:
        # Unreadable (permissions, races, a directory in its place) — report as absent.
        return [], stats
    except Exception:  # noqa: BLE001 - the panel must render regardless
        return [], stats

    events: list[dict[str, Any]] = []
    malformed = 0
    last_index = len(lines) - 1
    for index, line in enumerate(lines):
        raw = line.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except Exception:  # noqa: BLE001
            if index == last_index and last_partial:
                continue  # a half-written final record, not corruption
            malformed += 1
            continue
        if not isinstance(record, dict):
            malformed += 1
            continue
        events.append(normalize_event(record))

    events.sort(key=_sort_key)
    stats["events"] = len(events)
    stats["malformed_lines"] = malformed
    return events, stats


def normalize_event(record: dict[str, Any]) -> dict[str, Any]:
    """Coerce one raw JSONL record into the stable shape the API returns.

    Unknown extra keys are dropped rather than echoed, so a future telemetry field cannot
    leak something unexpected into an unauthenticated response.
    """
    client = record.get("client") if isinstance(record.get("client"), dict) else {}
    target = record.get("target") if isinstance(record.get("target"), dict) else {}
    args = record.get("args_summary")
    ts_raw = record.get("ts")
    parsed = parse_ts(ts_raw)

    target_kind = str(target.get("kind") or "").strip() or None
    target_id = target.get("id")
    target_id = str(target_id).strip() if isinstance(target_id, (str, int)) else None

    duration = record.get("duration_ms")
    if not isinstance(duration, (int, float)) or isinstance(duration, bool):
        duration = None

    return {
        "ts": ts_raw if isinstance(ts_raw, str) else None,
        "event": str(record.get("event") or "").strip() or "unknown",
        "session_id": str(record.get("session_id") or "").strip() or None,
        "transport": str(record.get("transport") or "").strip() or None,
        "client": {
            "name": str(client.get("name") or "").strip() or None,
            "version": str(client.get("version") or "").strip() or None,
        },
        "tool": str(record.get("tool") or "").strip() or None,
        "args_summary": _redact(args) if isinstance(args, dict) else None,
        "target": {"kind": target_kind, "id": target_id or None},
        "ok": record.get("ok") if isinstance(record.get("ok"), bool) else None,
        "error": str(record.get("error")) if record.get("error") not in (None, "") else None,
        "blocked": bool(record.get("blocked")),
        "destructive": bool(record.get("destructive")),
        "duration_ms": duration,
        "_ts": parsed,
    }


_UNSET = object()


def _public(event: dict[str, Any], label: Any = _UNSET) -> dict[str, Any]:
    """Strip internal fields (``_ts``) before an event leaves the process."""
    out = {k: v for k, v in event.items() if not k.startswith("_")}
    if label is not _UNSET:
        out["target_label"] = label
    return out


def window_events(events: list[dict[str, Any]], hours: int) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, hours))
    return [e for e in events if (e.get("_ts") or _EPOCH) >= cutoff]


def _is_error(event: dict[str, Any]) -> bool:
    return event.get("ok") is False or bool(event.get("error"))


# ---------------------------------------------------------------------------
# Label resolution
# ---------------------------------------------------------------------------


def _control_target_labels() -> dict[str, str]:
    """Control-target id → label, from the metadata module only.

    ``control_targets`` is deliberately dependency-free; ``control.py`` pulls in the Docker
    client and is far too expensive to import for a lookup table.
    """
    try:
        from control_targets import AI_TARGETS, CF_TARGETS, FILE_TRANSFER_TARGETS, INFRA_TARGETS
    except Exception:  # noqa: BLE001
        return {}
    labels: dict[str, str] = {}
    for group in (AI_TARGETS, CF_TARGETS, INFRA_TARGETS, FILE_TRANSFER_TARGETS):
        for row in group:
            tid = str(row.get("id") or "").strip()
            if tid:
                labels[tid] = str(row.get("label") or tid)
    return labels


def app_slug_from_target_id(target_id: str | None) -> str | None:
    """``leco-stack-<slug>`` → ``<slug>``; anything else is returned unchanged."""
    if not target_id:
        return None
    text = str(target_id).strip()
    if text.startswith("leco-stack-"):
        return text[len("leco-stack-") :] or None
    return text or None


def event_app_key(event: dict[str, Any]) -> str | None:
    """The hosted-app slug an event refers to, or None.

    One key per event, so an event carrying both a target id and an ``args_summary`` slug is
    counted exactly once.
    """
    target = event.get("target") or {}
    kind = (target.get("kind") or "").strip().lower()
    tid = str(target.get("id") or "").strip()
    if tid:
        # A ``leco-stack-`` prefix is unambiguous whatever the declared kind; otherwise only an
        # explicit ``app`` kind may name a slug. A bare ``ai-ollama`` service id must never be
        # folded into the app fleet.
        if tid.startswith("leco-stack-") or kind == "app":
            return app_slug_from_target_id(tid)
        if kind:
            return None
    args = event.get("args_summary")
    if isinstance(args, dict):
        for key in ("slug", "app_id", "app", "app_slug"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                return app_slug_from_target_id(value.strip())
    return None


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def summarize_events(
    events: list[dict[str, Any]],
    *,
    hours: int = DEFAULT_WINDOW_HOURS,
    limit: int = DEFAULT_RECENT_LIMIT,
    apps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Pure aggregation over already-loaded events. No I/O, no exceptions."""
    windowed = window_events(events, hours)
    app_labels = {
        str(a.get("id") or "").strip(): str(a.get("label") or a.get("id") or "")
        for a in (apps or [])
        if a.get("id")
    }
    target_labels = _control_target_labels()

    def label_for(kind: str | None, tid: str | None) -> str | None:
        _ = kind  # the kind alone is not a label; an unnamed target has none
        if not tid:
            return None
        slug = app_slug_from_target_id(tid)
        if slug and slug in app_labels:
            return app_labels[slug]
        if tid in target_labels:
            return target_labels[tid]
        return tid

    tool_calls = [e for e in windowed if e.get("event") == "tool_call"]

    counts = {
        "tool_calls": len(tool_calls),
        "errors": sum(1 for e in tool_calls if _is_error(e)),
        "blocked": sum(1 for e in windowed if e.get("blocked")),
        "destructive": sum(1 for e in windowed if e.get("destructive")),
        "sessions": len({e["session_id"] for e in windowed if e.get("session_id")}),
        "window_hours": hours,
    }

    # --- sessions -----------------------------------------------------------
    sessions: dict[str, dict[str, Any]] = {}
    for event in windowed:
        sid = event.get("session_id")
        if not sid:
            continue
        row = sessions.get(sid)
        if row is None:
            row = {
                "session_id": sid,
                "transport": None,
                "client_name": None,
                "client_version": None,
                "first_seen": None,
                "last_seen": None,
                "call_count": 0,
                "error_count": 0,
                "blocked_count": 0,
                "tools": [],
                "_tools": set(),
                "_first": None,
                "_last": None,
            }
            sessions[sid] = row
        if event.get("transport"):
            row["transport"] = event["transport"]
        client = event.get("client") or {}
        if client.get("name"):
            row["client_name"] = client["name"]
        if client.get("version"):
            row["client_version"] = client["version"]
        stamp = event.get("_ts")
        if stamp is not None:
            if row["_first"] is None or stamp < row["_first"]:
                row["_first"], row["first_seen"] = stamp, event.get("ts")
            if row["_last"] is None or stamp >= row["_last"]:
                row["_last"], row["last_seen"] = stamp, event.get("ts")
        if event.get("event") == "tool_call":
            row["call_count"] += 1
            if event.get("tool"):
                row["_tools"].add(event["tool"])
            if _is_error(event):
                row["error_count"] += 1
        if event.get("blocked"):
            row["blocked_count"] += 1

    session_rows = sorted(sessions.values(), key=lambda r: r["_last"] or _EPOCH, reverse=True)
    for row in session_rows:
        row["tools"] = sorted(row.pop("_tools"))
        row.pop("_first", None)
        row.pop("_last", None)

    # --- by_tool ------------------------------------------------------------
    tools: dict[str, dict[str, Any]] = {}
    for event in tool_calls:
        name = event.get("tool") or "(unknown)"
        row = tools.setdefault(
            name,
            {"tool": name, "count": 0, "errors": 0, "blocked": 0, "avg_ms": None, "last_ts": None,
             "_durations": [], "_last": None},
        )
        row["count"] += 1
        if _is_error(event):
            row["errors"] += 1
        if event.get("blocked"):
            row["blocked"] += 1
        if isinstance(event.get("duration_ms"), (int, float)):
            row["_durations"].append(float(event["duration_ms"]))
        stamp = event.get("_ts")
        if stamp is not None and (row["_last"] is None or stamp >= row["_last"]):
            row["_last"], row["last_ts"] = stamp, event.get("ts")
    tool_rows = sorted(tools.values(), key=lambda r: (-r["count"], r["tool"]))
    for row in tool_rows:
        durations = row.pop("_durations")
        row.pop("_last", None)
        row["avg_ms"] = round(sum(durations) / len(durations), 1) if durations else None

    # --- by_target ----------------------------------------------------------
    targets: dict[tuple[str, str], dict[str, Any]] = {}
    for event in tool_calls:
        target = event.get("target") or {}
        kind = (target.get("kind") or "").strip().lower()
        tid = target.get("id")
        if not tid or kind == "none":
            continue
        key = (kind, str(tid))
        row = targets.setdefault(
            key,
            {"kind": kind or None, "id": str(tid), "label": label_for(kind, str(tid)),
             "count": 0, "last_ts": None, "last_tool": None, "_last": None},
        )
        row["count"] += 1
        stamp = event.get("_ts")
        if stamp is not None and (row["_last"] is None or stamp >= row["_last"]):
            row["_last"] = stamp
            row["last_ts"] = event.get("ts")
            row["last_tool"] = event.get("tool")
    target_rows = sorted(targets.values(), key=lambda r: (-r["count"], r["id"]))
    for row in target_rows:
        row.pop("_last", None)

    # --- apps ---------------------------------------------------------------
    per_app: dict[str, dict[str, Any]] = {}
    for event in tool_calls:
        slug = event_app_key(event)
        if not slug:
            continue
        row = per_app.setdefault(slug, {"count": 0, "last_ts": None, "last_tool": None,
                                        "last_ok": None, "_last": None})
        row["count"] += 1
        stamp = event.get("_ts")
        if stamp is not None and (row["_last"] is None or stamp >= row["_last"]):
            row["_last"] = stamp
            row["last_ts"] = event.get("ts")
            row["last_tool"] = event.get("tool")
            row["last_ok"] = event.get("ok")

    app_rows: list[dict[str, Any]] = []
    seen_slugs: set[str] = set()
    for app in apps or []:
        slug = str(app.get("id") or "").strip()
        if not slug or slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        stats = per_app.get(slug) or {}
        runtime = app.get("runtime") if isinstance(app.get("runtime"), dict) else {}
        status = str(runtime.get("status") or "").strip().lower()
        app_rows.append(
            {
                "slug": slug,
                "label": str(app.get("label") or slug),
                "running": status == "running",
                "main_url": str(app.get("main_url") or "") or None,
                "mcp_calls": int(stats.get("count") or 0),
                "last_tool": stats.get("last_tool"),
                "last_ts": stats.get("last_ts"),
                "last_ok": stats.get("last_ok"),
                "registered": True,
            }
        )
    # Activity against a slug that is not in the fleet — an offboarded app, or a slug the MCP
    # server tagged ``kind: "app"`` that belongs to another namespace (e.g. a credential-vault
    # slug). Kept, but flagged, so the panel can separate the fleet from stray activity.
    for slug, stats in per_app.items():
        if slug in seen_slugs:
            continue
        app_rows.append(
            {
                "slug": slug,
                "label": slug,
                "running": False,
                "main_url": None,
                "mcp_calls": int(stats.get("count") or 0),
                "last_tool": stats.get("last_tool"),
                "last_ts": stats.get("last_ts"),
                "last_ok": stats.get("last_ok"),
                "registered": False,
            }
        )
    app_rows.sort(key=lambda r: (0 if r["mcp_calls"] else 1, -r["mcp_calls"], r["label"].lower()))

    # --- recent -------------------------------------------------------------
    recent = [
        _public(e, label_for((e.get("target") or {}).get("kind"), (e.get("target") or {}).get("id")))
        for e in sorted(tool_calls, key=_sort_key, reverse=True)[: max(1, limit)]
    ]

    return {
        "counts": counts,
        "sessions": session_rows,
        "recent": recent,
        "by_tool": tool_rows,
        "by_target": target_rows,
        "apps": app_rows,
    }


# ---------------------------------------------------------------------------
# Live server state
# ---------------------------------------------------------------------------


def _probe_urls() -> list[str]:
    override = (os.getenv("LECO_MCP_INSIGHTS_URL") or "").strip()
    urls = [override] if override else []
    urls += ["http://leco-mcp:8099/insights", "http://localhost:8099/insights"]
    return urls


def probe_live_server() -> dict[str, Any] | None:
    """Best-effort GET of the MCP server's live insights. Returns None on any failure.

    Bounded by a total wall-clock budget so a hung container cannot stall the endpoint.
    """
    try:
        import requests
    except Exception:  # noqa: BLE001
        return None

    deadline = time.monotonic() + PROBE_TOTAL_BUDGET_SEC
    for url in _probe_urls():
        if time.monotonic() >= deadline:
            break
        try:
            resp = requests.get(url, timeout=PROBE_TIMEOUT)
            if resp.status_code != 200:
                continue
            data = resp.json()
        except Exception:  # noqa: BLE001 - unreachable is the expected case
            continue
        if isinstance(data, dict):
            return data
    return None


def container_running(name: str = CONTAINER_NAME) -> tuple[bool, bool]:
    """Return ``(exists, running)`` for a container, reusing the dashboard's Docker helpers."""
    try:
        from monitor import get_container, get_docker_client

        client = get_docker_client()
        if client is None:
            return False, False
        container = get_container(client, name)
        if container is None:
            return False, False
        return True, str(getattr(container, "status", "")).strip().lower() == "running"
    except Exception:  # noqa: BLE001
        return False, False


def _package_version() -> str | None:
    """Version from the packaged pyproject — what a local ``pipx install`` would produce."""
    try:
        text = (project_root() / MCP_PACKAGE_REL / "pyproject.toml").read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return None
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else None


_TOOL_COUNT_CACHE: dict[str, Any] = {"root": None, "value": None}


def _package_tool_count() -> int | None:
    """Count ``@server.tool`` registrations so a never-run server still reports its size."""
    root = project_root() / MCP_PACKAGE_REL / "leco_mcp" / "tools"
    cache_key = str(root)
    if _TOOL_COUNT_CACHE["root"] == cache_key:
        return _TOOL_COUNT_CACHE["value"]
    count: int | None = None
    try:
        if root.is_dir():
            total = 0
            for path in sorted(root.glob("*.py")):
                total += len(re.findall(r"@server\.tool\b", path.read_text(encoding="utf-8")))
            count = total or None
    except Exception:  # noqa: BLE001
        count = None
    _TOOL_COUNT_CACHE["root"] = cache_key
    _TOOL_COUNT_CACHE["value"] = count
    return count


def _probe_scopes(probe: dict[str, Any]) -> list[dict[str, Any]]:
    """The probe may report server fields at the top level or nested under ``server``."""
    scopes = [probe]
    inner = probe.get("server")
    if isinstance(inner, dict):
        scopes.append(inner)
    return scopes


def _probe_value(probe: dict[str, Any], *keys: str) -> Any:
    for scope in _probe_scopes(probe):
        for key in keys:
            if key in scope and scope[key] is not None:
                return scope[key]
    return None


def _first_bool(payload: dict[str, Any], *keys: str) -> bool | None:
    for scope in _probe_scopes(payload):
        for key in keys:
            value = scope.get(key)
            if isinstance(value, bool):
                return value
    return None


def build_server_block(log_stats: dict[str, Any], probe: dict[str, Any] | None) -> dict[str, Any]:
    exists, running = container_running()
    reachable = probe is not None
    probe = probe or {}

    version = _probe_value(probe, "version")
    version = str(version) if isinstance(version, (str, int, float)) else _package_version()

    tool_count = _probe_value(probe, "tool_count")
    if not isinstance(tool_count, int) or isinstance(tool_count, bool):
        tool_count = _package_tool_count()

    # "installed" means this machine has actually set the MCP server up — not merely that the
    # source tree contains the package (it always does, in this repo).
    installed = bool(exists or reachable or log_stats.get("exists"))

    return {
        "installed": installed,
        "running": bool(running or reachable),
        "reachable": reachable,
        "container": CONTAINER_NAME,
        "endpoints": dict(ENDPOINTS),
        "version": version,
        "tool_count": tool_count,
        "destructive_enabled": _first_bool(probe, "destructive_enabled", "allow_destructive"),
        "credentials_enabled": _first_bool(probe, "credentials_enabled", "allow_credentials"),
        "activity_log": {
            "path": log_stats.get("path"),
            "exists": bool(log_stats.get("exists")),
            "events": int(log_stats.get("events") or 0),
            "malformed_lines": int(log_stats.get("malformed_lines") or 0),
        },
    }


def _merge_live_sessions(sessions: list[dict[str, Any]], probe: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Flag sessions the server reports as currently connected, and add ones the log misses."""
    live_raw = _probe_value(probe or {}, "sessions")
    if not isinstance(live_raw, list):
        for row in sessions:
            row["live"] = False
        return sessions

    live_by_id: dict[str, dict[str, Any]] = {}
    for item in live_raw:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("session_id") or item.get("id") or "").strip()
        if sid:
            live_by_id[sid] = item

    for row in sessions:
        row["live"] = row["session_id"] in live_by_id

    known = {row["session_id"] for row in sessions}
    for sid, item in live_by_id.items():
        if sid in known:
            continue
        client = item.get("client") if isinstance(item.get("client"), dict) else {}
        sessions.append(
            {
                "session_id": sid,
                "transport": str(item.get("transport") or "").strip() or None,
                "client_name": str(client.get("name") or item.get("client_name") or "").strip() or None,
                "client_version": str(client.get("version") or item.get("client_version") or "").strip() or None,
                "first_seen": item.get("first_seen") or item.get("started_at"),
                "last_seen": item.get("last_seen"),
                "call_count": int(item.get("call_count") or 0),
                "error_count": int(item.get("error_count") or 0),
                "blocked_count": int(item.get("blocked_count") or 0),
                "tools": [str(t) for t in (item.get("tools") or []) if isinstance(t, str)],
                "live": True,
            }
        )
    return sessions


def load_hosted_apps() -> list[dict[str, Any]]:
    """The hosted-app fleet, or an empty list if it cannot be collected."""
    try:
        from hosted_apps import list_hosted_apps

        payload = list_hosted_apps()
        apps = payload.get("apps") if isinstance(payload, dict) else None
        return apps if isinstance(apps, list) else []
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_insights(hours: Any = None, limit: Any = None) -> dict[str, Any]:
    """Full payload for ``GET /api/mcp/insights``. Never raises."""
    window = clamp_int(hours, DEFAULT_WINDOW_HOURS, 1, MAX_WINDOW_HOURS)
    cap = clamp_int(limit, DEFAULT_RECENT_LIMIT, 1, MAX_LIMIT)

    events, log_stats = load_events()
    probe = probe_live_server()
    apps = load_hosted_apps()

    summary = summarize_events(events, hours=window, limit=cap, apps=apps)
    summary["sessions"] = _merge_live_sessions(summary["sessions"], probe)
    summary["counts"]["sessions"] = len(summary["sessions"])

    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "server": build_server_block(log_stats, probe),
        **summary,
        "install": build_install(),
    }


def query_activity(
    *,
    limit: Any = None,
    tool: str | None = None,
    session: str | None = None,
    target: str | None = None,
    blocked: Any = None,
    errors_only: Any = None,
    hours: Any = None,
    event: str | None = None,
) -> dict[str, Any]:
    """Filtered raw events for ``GET /api/mcp/activity``, newest first. Never raises."""
    window = clamp_int(hours, DEFAULT_WINDOW_HOURS, 1, MAX_WINDOW_HOURS)
    cap = clamp_int(limit, DEFAULT_ACTIVITY_LIMIT, 1, MAX_LIMIT)

    events, log_stats = load_events()
    rows = window_events(events, window)

    tool_f = (tool or "").strip().lower()
    session_f = (session or "").strip()
    target_f = (target or "").strip().lower()
    event_f = (event or "").strip().lower()
    only_blocked = _truthy(blocked)
    only_errors = _truthy(errors_only)

    def keep(row: dict[str, Any]) -> bool:
        if tool_f and (row.get("tool") or "").lower() != tool_f:
            return False
        if session_f and row.get("session_id") != session_f:
            return False
        if event_f and (row.get("event") or "").lower() != event_f:
            return False
        if target_f:
            tgt = row.get("target") or {}
            candidates = {
                str(tgt.get("id") or "").lower(),
                str(tgt.get("kind") or "").lower(),
                str(app_slug_from_target_id(tgt.get("id")) or "").lower(),
            }
            if target_f not in candidates:
                return False
        if only_blocked and not row.get("blocked"):
            return False
        if only_errors and not _is_error(row):
            return False
        return True

    matched = [r for r in rows if keep(r)]
    matched.sort(key=_sort_key, reverse=True)

    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": window,
        "limit": cap,
        "total_matched": len(matched),
        "returned": min(len(matched), cap),
        "filters": {
            "tool": tool_f or None,
            "session": session_f or None,
            "target": target_f or None,
            "event": event_f or None,
            "blocked": only_blocked,
            "errors_only": only_errors,
        },
        "activity_log": {
            "path": log_stats.get("path"),
            "exists": bool(log_stats.get("exists")),
            "events": int(log_stats.get("events") or 0),
            "malformed_lines": int(log_stats.get("malformed_lines") or 0),
        },
        "events": [_public(r) for r in matched[:cap]],
    }


# ---------------------------------------------------------------------------
# Install guidance
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def _read_frontmatter(path: Path) -> dict[str, Any]:
    """Parse the YAML frontmatter block of a Markdown file.

    PyYAML is imported lazily so this module keeps loading on a bare interpreter; the
    fallback handles the flat ``key: value`` frontmatter that skills and commands use.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end].strip("\n")
    try:
        import yaml  # noqa: PLC0415 - lazy so the module stays stdlib-importable

        parsed = yaml.safe_load(block)
        if isinstance(parsed, dict):
            return parsed
    except Exception:  # noqa: BLE001 - fall through to the line parser
        pass
    out: dict[str, Any] = {}
    for line in block.splitlines():
        if line.startswith((" ", "\t", "#")) or ":" not in line:
            continue
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip().strip("\"'")
    return out


def _plugin_skill_dirs(plugin_dir: Path) -> list[Path]:
    try:
        return sorted(p for p in (plugin_dir / "skills").iterdir() if (p / "SKILL.md").is_file())
    except Exception:  # noqa: BLE001
        return []


def build_plugin_commands(plugin_name: str, repository: str) -> list[dict[str, Any]]:
    """Every command the plugin installs, read from disk with its description.

    Two layouts are read because Claude Code merged commands into skills: a plugin
    ``skills/<dir>/SKILL.md`` is invoked as ``/<plugin>:<name>`` (frontmatter ``name``
    overrides the directory), while a legacy ``commands/<file>.md`` is invoked as
    ``/<file>``. Reading both means this table stays correct across that migration
    instead of going blank the moment the plugin is restructured.
    """
    root = project_root()
    plugin_dir = root / PLUGIN_REL
    rows: list[dict[str, Any]] = []

    for skill_dir in _plugin_skill_dirs(plugin_dir):
        meta = _read_frontmatter(skill_dir / "SKILL.md")
        name = str(meta.get("name") or skill_dir.name).strip() or skill_dir.name
        rel = f"{PLUGIN_REL}/skills/{skill_dir.name}"
        rows.append(
            {
                "command": f"/{plugin_name}:{name}",
                "name": name,
                "kind": "skill",
                "description": str(meta.get("description") or "").strip(),
                "path": f"{rel}/SKILL.md",
                "url": f"{repository}/tree/main/{rel}",
                # A skill directory can ship reference files it loads on demand.
                "references": sorted(
                    p.name for p in (skill_dir / "references").glob("*.md")
                )
                if (skill_dir / "references").is_dir()
                else [],
            }
        )

    try:
        legacy = sorted((plugin_dir / "commands").glob("*.md"))
    except Exception:  # noqa: BLE001
        legacy = []
    for cmd in legacy:
        meta = _read_frontmatter(cmd)
        rel = f"{PLUGIN_REL}/commands/{cmd.name}"
        rows.append(
            {
                "command": f"/{cmd.stem}",
                "name": cmd.stem,
                "kind": "command",
                "description": str(meta.get("description") or "").strip(),
                "path": rel,
                "url": f"{repository}/tree/main/{rel}",
                "references": [],
            }
        )

    rows.sort(key=lambda r: (r["kind"] != "skill", r["command"]))
    return rows


def build_install() -> dict[str, Any]:
    """Everything needed to install the tooling on an agent, verified against disk."""
    from project_paths import host_project_root

    root = project_root()
    plugin_manifest = _read_json(root / PLUGIN_REL / ".claude-plugin" / "plugin.json") or {}
    marketplace = _read_json(root / ".claude-plugin" / "marketplace.json") or {}

    owner = marketplace.get("owner") if isinstance(marketplace.get("owner"), dict) else {}
    repository = REPOSITORY_URL
    for candidate in (plugin_manifest.get("repository"), owner.get("url")):
        if isinstance(candidate, str) and candidate.startswith("http"):
            repository = candidate.rstrip("/")
            break

    def tree_url(rel: str) -> str:
        return f"{repository}/tree/main/{rel}"

    # Fallback only matters when the plugin tree is absent; keep it in step with plugin.json.
    plugin_name = str(plugin_manifest.get("name") or DEFAULT_PLUGIN_NAME).strip() or DEFAULT_PLUGIN_NAME
    market_name = str(marketplace.get("name") or "leco-devops-open-project").strip() or "leco-devops-open-project"
    qualified = f"{plugin_name}@{market_name}"

    command_rows = build_plugin_commands(plugin_name, repository)
    commands = [row["command"] for row in command_rows]

    plugin_available = bool(plugin_manifest) and bool(marketplace)
    # The knowledge skill is whichever skill ships reference files; falling back to the
    # historical path keeps this correct while the plugin layout is in flux.
    knowledge = next(
        (row for row in command_rows if row["kind"] == "skill" and row["references"]),
        None,
    )
    skill_rel = knowledge["path"].rsplit("/SKILL.md", 1)[0] if knowledge else SKILL_REL
    skill_name = knowledge["name"] if knowledge else DEFAULT_SKILL_NAME
    try:
        skill_available = (root / skill_rel / "SKILL.md").is_file()
    except Exception:  # noqa: BLE001
        skill_available = False
    try:
        mcp_available = (root / MCP_PACKAGE_REL / "pyproject.toml").is_file()
    except Exception:  # noqa: BLE001
        mcp_available = False

    docs = {
        "guide": "docs/MCP_SERVER.md",
        "route_map": "START_HERE.md",
        "plugin_readme": "tools/claude-plugin/README.md",
        "connect_agents": "docs/CONNECT_AI_AGENTS.md",
    }
    try:
        docs_available = all((root / rel).is_file() for rel in docs.values())
    except Exception:  # noqa: BLE001
        docs_available = False

    try:
        project_config_available = (root / ".mcp.json").is_file()
    except Exception:  # noqa: BLE001
        project_config_available = False

    return {
        "ok": True,
        "repository": repository,
        "plugin": {
            "name": qualified,
            # Absolute, not "./". `marketplace add` resolves a relative path against the shell's
            # working directory, so "./" only works when you happen to be standing in this repo —
            # and the natural moment to run it is while you are inside the *application* you are
            # onboarding. The absolute path works from anywhere.
            "marketplace_local": f"claude plugin marketplace add {host_project_root().rstrip('/')}",
            # `marketplace add <owner>/<repo>` clones the repository's DEFAULT branch. If the
            # plugin has not been merged there yet, the clone succeeds and the marketplace file is
            # missing, which surfaces as "Marketplace file not found at …" — an error that reads
            # like a broken install rather than a branch that does not carry the plugin.
            "marketplace_github": f"claude plugin marketplace add {_repo_slug(repository)}",
            "marketplace_github_requires_default_branch": True,
            "install": f"claude plugin install {qualified}",
            "path": PLUGIN_REL,
            "url": tree_url(PLUGIN_REL),
            "commands": commands,
            "commands_detail": command_rows,
            "command_count": len(command_rows),
            "version": str(plugin_manifest.get("version") or "") or None,
            "available": plugin_available,
        },
        "skill": {
            "name": skill_name,
            "path": skill_rel,
            "url": tree_url(skill_rel),
            "available": skill_available,
        },
        "mcp": {
            "stdio_install": f"pipx install ./{MCP_PACKAGE_REL}",
            "stdio_register": "claude mcp add leco-devops -- leco-mcp stdio",
            "http_url": ENDPOINTS["http"],
            "http_register": f"claude mcp add --transport http leco-devops {ENDPOINTS['http']}",
            "project_config": ".mcp.json",
            "project_config_available": project_config_available,
            "doctor": "leco-mcp doctor",
            "available": mcp_available,
        },
        "clients": build_agent_clients(qualified),
        "docs": {**docs, "available": docs_available},
    }


def build_agent_clients(qualified_plugin: str) -> list[dict[str, Any]]:
    """Per-client setup steps for section 2 of the MCP tab.

    Two things make this worth generating rather than writing into the template:

    * **Absolute host paths.** A GUI agent (Claude Desktop, Antigravity, JetBrains) does not
      inherit the shell ``PATH``, so a bare ``leco-mcp`` resolves when you test it in a terminal
      and fails silently inside the app. That is the single most common setup failure, so every
      stdio snippet here carries the real host path — which the dashboard can only know because
      ``host_project_root()`` maps ``/project`` back to where the repo actually lives.
    * **Honesty about what was tested.** ``verified`` marks the clients exercised on a real
      machine. The rest carry standard MCP config shapes; the LEco-specific values are right, the
      surrounding key names are the client's business and they disagree with each other (``url``
      vs ``serverUrl`` vs ``httpUrl``). Saying so is more useful than implying every row was run.
    """
    from project_paths import host_project_root

    host_root = host_project_root().rstrip("/")
    # A venv puts its executables in bin/ on macOS and Linux but Scripts/ on Windows, and
    # the binary gains a .exe suffix there. The dashboard runs in a Linux container and
    # cannot know the client's OS, so — as with config_paths_other above — emit the POSIX
    # form as the primary and carry the Windows form alongside it rather than handing a
    # Windows user a path that silently does not exist.
    venv_dir = f"{host_root}/{MCP_PACKAGE_REL}/.venv"
    venv_bin = f"{venv_dir}/bin/leco-mcp"
    venv_bin_windows = f"{venv_dir}\\Scripts\\leco-mcp.exe".replace("/", "\\")
    http_local = ENDPOINTS["host"]

    def stdio_json(key: str = "mcpServers") -> str:
        return json.dumps(
            {key: {"leco-devops": {"command": venv_bin, "args": ["stdio"]}}},
            indent=2,
        )

    def http_json(url_key: str = "url", key: str = "mcpServers") -> str:
        return json.dumps({key: {"leco-devops": {url_key: http_local}}}, indent=2)

    return [
        {
            "id": "claude-code",
            "label": "Claude Code (CLI)",
            "transport": "plugin · stdio · HTTP",
            "verified": True,
            "summary": "The plugin is the fastest path: it brings the MCP server, the operate skill and the slash commands in one install.",
            "steps": [
                {
                    "label": "Add the marketplace",
                    "command": f"claude plugin marketplace add {host_root}",
                    "hint": "Absolute path, so it works from whatever directory you happen to be in.",
                },
                {"label": "Install the plugin", "command": f"claude plugin install {qualified_plugin}"},
                {
                    "label": "Or register HTTP directly (nothing to install)",
                    "command": f"claude mcp add --transport http leco-devops {http_local}",
                },
                {"label": "Verify", "command": "claude mcp list"},
            ],
            "note": "This repo also ships <code>.mcp.json</code>, so opening Claude Code in this checkout offers the server with no setup at all.",
        },
        {
            "id": "claude-desktop",
            "label": "Claude Desktop &amp; Cowork",
            "transport": "stdio",
            "verified": True,
            "summary": "Both read the same file — configuring one configures the other.",
            "config_path": "~/Library/Application Support/Claude/claude_desktop_config.json",
            "config_paths_other": [
                "Windows: %APPDATA%\\Claude\\claude_desktop_config.json",
                "Linux: ~/.config/Claude/claude_desktop_config.json",
            ],
            "config": stdio_json(),
            "steps": [
                {
                    "label": "Install the server first (stdio needs it)",
                    "command": f"python3 -m venv {venv_dir} && {venv_dir}/bin/pip install -e {host_root}/{MCP_PACKAGE_REL}",
                },
                {
                    "label": "On Windows, the venv layout differs — use this command instead",
                    "command": f"py -m venv {venv_dir} && {venv_dir}\\Scripts\\pip install -e {host_root}/{MCP_PACKAGE_REL}".replace(
                        host_root + "/", host_root.replace("/", "\\") + "\\"
                    ),
                },
                {
                    "label": "…and this as the \"command\" value in the config above",
                    "command": venv_bin_windows,
                },
            ],
            "note": "<strong>Merge</strong> into the existing <code>mcpServers</code> object rather than replacing the file. Then quit and reopen the app fully — closing the window is not a restart. Remote (HTTP) servers go through Settings → Connectors instead, and a connector cannot reach a <code>localhost</code> URL.",
        },
        {
            "id": "codex",
            "label": "Codex CLI",
            "transport": "HTTP · stdio",
            "verified": True,
            "summary": "Verified working over streamable HTTP — no install needed.",
            "steps": [
                {"label": "Register (HTTP)", "command": f"codex mcp add leco-devops --url {http_local}"},
                {"label": "Verify", "command": "codex mcp get leco-devops"},
                {"label": "Remove", "command": "codex mcp remove leco-devops"},
            ],
            "config_path": "~/.codex/config.toml",
            "config": f'[mcp_servers.leco-devops]\nurl = "{http_local}"',
            "config_lang": "toml",
            "note": "The hand-edited table is <code>mcp_servers</code> — underscore, not the <code>mcpServers</code> spelling every JSON client uses.",
        },
        {
            "id": "antigravity",
            "label": "Antigravity (Google)",
            "transport": "HTTP · stdio",
            "verified": False,
            "summary": "Config file location confirmed on this machine; the accepted remote-URL key was not exercised.",
            "config_path": "~/.gemini/antigravity/mcp_config.json",
            "config": http_json("serverUrl"),
            "note": "If the remote form does not connect, use the stdio block from the generic section — no client disagrees about that one.",
        },
        {
            "id": "generic",
            "label": "Cursor · VS Code · Windsurf · Cline · Zed · others",
            "transport": "HTTP · stdio",
            "verified": False,
            "summary": "Standard MCP client config. The LEco values are verified; the surrounding key names vary by client.",
            "config": stdio_json(),
            "config_alt_label": "HTTP form",
            "config_alt": http_json(),
            "paths": [
                "Cursor: ~/.cursor/mcp.json (global) or .cursor/mcp.json (per project)",
                "VS Code / Copilot: .vscode/mcp.json — uses a `servers` key, not `mcpServers`",
                "Windsurf: ~/.codeium/windsurf/mcp_config.json",
                "Zed: settings.json → context_servers",
                "Continue: ~/.continue/config.yaml",
                "Gemini CLI: ~/.gemini/settings.json",
                "JetBrains AI: Settings → Tools → AI Assistant → MCP",
            ],
            "note": "Clients disagree on the remote key — <code>url</code>, <code>serverUrl</code>, <code>httpUrl</code>, or a <code>type: http</code> field. Check that first when a remote entry is ignored, then fall back to stdio.",
        },
    ]


def _repo_slug(repository: str) -> str:
    """``https://github.com/owner/name`` → ``owner/name`` for the marketplace command."""
    text = repository.rstrip("/")
    parts = [p for p in text.split("/") if p]
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return "leco-devops/local-ecosystem"
