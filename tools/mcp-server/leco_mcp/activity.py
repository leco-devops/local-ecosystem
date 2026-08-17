"""Activity telemetry: a durable record of which agent did what through MCP.

A dashboard has to answer "who is calling this server, and what are they doing" — and the
protocol itself remembers nothing: once a tool result goes back on the wire the call is
gone. This module is the only place that keeps it. Every tool call and session boundary is
appended as one JSON object per line to a file the dashboard can tail, and the same events
are held in a small in-memory ring so the server's own ``/insights`` route can answer
without reading the file back.

Two rules shape every decision here:

1. **Telemetry never breaks a tool call.** Every write path swallows its own errors. A
   read-only mount or a bad path must degrade to "no telemetry", never to a failed deploy.
2. **Telemetry never leaks secrets.** Arguments are recorded through a name whitelist of
   scalars, so a credential map, a manifest body, or a config blob simply has no route into
   the file even if a future tool grows one.

The line schema is a published contract — dashboards parse these files — so ``EVENT_KEYS``
below is the authority on it, and every line carries all of the keys (``null`` where a key
does not apply to that event kind).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import uuid
from collections import OrderedDict, deque
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps the sink import-light
    from mcp.server.context import CallNext, HandlerResult, ServerRequestContext

ENV_LOG_PATH = "LECO_MCP_ACTIVITY_LOG"
ENV_MAX_EVENTS = "LECO_MCP_ACTIVITY_MAX_EVENTS"
ENV_PROJECT_ROOT = "LECO_MCP_PROJECT_ROOT"

DEFAULT_MAX_EVENTS = 2000
DEFAULT_LOG_RELPATH = "ecosystem-stack/config/generated/mcp-activity.jsonl"

# Keys present on every written line, in write order. Session events carry `null` for the
# call-scoped half rather than omitting it, so a consumer can parse every line the same way.
EVENT_KEYS: tuple[str, ...] = (
    "ts",
    "event",
    "session_id",
    "transport",
    "client",
    "tool",
    "args_summary",
    "target",
    "ok",
    "error",
    "blocked",
    "destructive",
    "duration_ms",
)

# How many events the in-memory ring keeps for `/insights`. Independent of the file cap:
# this only needs to cover "what happened just now", the file covers history.
RECENT_EVENTS = 200

# Sessions tracked for `/insights`. Old ones are evicted rather than accumulating for the
# lifetime of a long-running container.
MAX_TRACKED_SESSIONS = 200


# --------------------------------------------------------------------- redaction

# Argument names that may be recorded, all of them scalars that identify *what* was acted
# on. Anything not named here is dropped, which is what keeps credential maps, manifest
# bodies, and config blobs out of the log by construction rather than by vigilance.
ARG_WHITELIST: frozenset[str] = frozenset(
    {
        "target_id",
        "action",
        "slug",
        "stack_id",
        "app_id",
        "path",
        "container",
        "service",
        "model",
        "runtime",
        "detail",
        "confirm",
        "dry_run",
        "preset",
        "template",
        "group",
        "query",
        "doc_id",
        "topic_id",
        "service_id",
        "hostname",
    }
)

# Redundant with the whitelist, and deliberately so: these are the payload-carrying
# arguments in the current tool set, and naming them makes it obvious to anyone widening
# ARG_WHITELIST later that these must never be added.
NEVER_RECORD: frozenset[str] = frozenset(
    {
        "values",
        "set_config",
        "manifest_yaml",
        "localhost_yaml",
        "yaml_fragment",
        "write_content",
        "set_policies",
    }
)

# Last line of defence for a future argument whose name says it carries a secret.
_SECRET_HINTS = ("password", "passwd", "secret", "token", "credential", "api_key", "apikey")

MAX_ARG_CHARS = 120


def _is_secretish(name: str) -> bool:
    low = name.lower()
    return any(hint in low for hint in _SECRET_HINTS)


def summarize_args(args: Mapping[str, Any] | None) -> dict[str, Any]:
    """Reduce tool arguments to whitelisted, truncated scalars."""
    out: dict[str, Any] = {}
    for name, value in (args or {}).items():
        if name not in ARG_WHITELIST or name in NEVER_RECORD or _is_secretish(name):
            continue
        if isinstance(value, bool) or isinstance(value, (int, float)) or value is None:
            out[name] = value
        elif isinstance(value, str):
            out[name] = value[:MAX_ARG_CHARS]
        # Anything else (list, dict, model) is a payload, not an identifier: drop it.
    return out


# ----------------------------------------------------------------- target mapping


@dataclass(frozen=True)
class _TargetRule:
    """Where a tool's target id lives: first non-empty argument in ``keys`` wins."""

    kind: str
    keys: tuple[str, ...]
    prefix: str = ""


# Exact tool names. Keep every mapping in these two tables — resolution logic scattered
# through per-tool branches is how a telemetry schema quietly stops matching its tools.
_TOOL_TARGETS: dict[str, _TargetRule] = {
    "leco_control": _TargetRule("service", ("target_id",)),
    "leco_logs": _TargetRule("service", ("container",)),
    "leco_platform_service_action": _TargetRule("service", ("service_id",)),
    "leco_ui_credentials": _TargetRule("app", ("slug",)),
    "leco_ui_credentials_set": _TargetRule("app", ("slug",)),
    "leco_ui_credentials_reset": _TargetRule("app", ("slug",)),
    "leco_route_fragment_from_app": _TargetRule("app", ("slug",)),
    "leco_browse": _TargetRule("path", ("path",)),
    "leco_detect": _TargetRule("path", ("path",)),
    "leco_register": _TargetRule("path", ("path",)),
    "leco_onboard": _TargetRule("path", ("path",)),
}

# Tool-name prefixes, longest match first. `leco_app_*` reports the control target id
# (`leco-stack-<slug>`) so a dashboard can join it against leco_control rows.
_FAMILY_TARGETS: tuple[tuple[str, _TargetRule], ...] = (
    ("leco_dev_stack_", _TargetRule("dev_stack", ("stack_id",))),
    ("leco_manifest_", _TargetRule("path", ("path",))),
    ("leco_app_", _TargetRule("app", ("slug",), prefix="leco-stack-")),
    ("leco_llm_", _TargetRule("model", ("model",))),
)

NO_TARGET: dict[str, Any] = {"kind": "none", "id": None}

# Hosted-app control targets carry this prefix, so a `leco_control` call against one is an
# app rather than a platform service.
_APP_TARGET_PREFIX = "leco-stack-"


def resolve_target(tool: str, args: Mapping[str, Any] | None) -> dict[str, Any]:
    """Map a tool name plus its arguments to the thing the call acted on."""
    rule = _TOOL_TARGETS.get(tool)
    if rule is None:
        for family, candidate in sorted(_FAMILY_TARGETS, key=lambda x: -len(x[0])):
            if tool.startswith(family):
                rule = candidate
                break
    if rule is None:
        return dict(NO_TARGET)
    for key in rule.keys:
        raw = (args or {}).get(key)
        if isinstance(raw, str) and raw.strip():
            target_id = rule.prefix + raw.strip()
            kind = rule.kind
            if kind == "service" and target_id.startswith(_APP_TARGET_PREFIX):
                kind = "app"
            return {"kind": kind, "id": target_id[:MAX_ARG_CHARS]}
    return dict(NO_TARGET)


# Actions that delete containers, volumes, stacks, or models. The authoritative gate lives
# in safety.py and marks calls at the raise site; this table covers the calls that fail
# validation before reaching a gate, so intent is still visible.
_DESTRUCTIVE_ACTIONS = frozenset({"remove", "reset", "destroy", "reinstall", "delete"})

_ALWAYS_DESTRUCTIVE_TOOLS = frozenset(
    {"leco_app_offboard", "leco_route_strip_keys", "leco_ui_credentials_reset"}
)


def looks_destructive(tool: str, args: Mapping[str, Any] | None) -> bool:
    if tool in _ALWAYS_DESTRUCTIVE_TOOLS:
        return True
    action = (args or {}).get("action")
    return isinstance(action, str) and action.strip().lower() in _DESTRUCTIVE_ACTIONS


# ------------------------------------------------------------------- call marks

# A tool that hits a safety gate raises ActionBlocked, but the SDK turns any handler
# exception into an `isError` result before the middleware sees it — the distinction
# between "refused by policy" and "failed" is lost by then. safety.py therefore marks the
# in-flight call, and the middleware reads the mark. A mutable dict (not a rebound
# ContextVar) so the mark is visible to the middleware regardless of context copying.
_CALL_MARKS: ContextVar[dict[str, bool] | None] = ContextVar("leco_mcp_call_marks", default=None)


def begin_call() -> dict[str, bool]:
    """Start a fresh mark record for one tool call and return it to the caller."""
    marks = {"blocked": False, "destructive": False}
    _CALL_MARKS.set(marks)
    return marks


def note_blocked() -> None:
    """Record that a safety gate refused the in-flight call."""
    marks = _CALL_MARKS.get()
    if marks is not None:
        marks["blocked"] = True


def note_destructive() -> None:
    """Record that the in-flight call was for a destructive action."""
    marks = _CALL_MARKS.get()
    if marks is not None:
        marks["destructive"] = True


# ------------------------------------------------------------------- resolution


def default_project_root() -> Path:
    """Where the repo lives, from the container's perspective or the host's.

    ``/project`` is the read-write mount the leco-mcp container gets; on the host the
    package sits at ``<repo>/tools/mcp-server/leco_mcp``, so the repo root is four levels up.
    """
    explicit = (os.getenv(ENV_PROJECT_ROOT) or "").strip()
    if explicit:
        return Path(explicit)
    if Path("/project").is_dir():
        return Path("/project")
    return Path(__file__).resolve().parents[3]


def default_activity_log(project_root: Path | str | None = None) -> Path:
    root = Path(project_root) if project_root is not None else default_project_root()
    return root / DEFAULT_LOG_RELPATH


def resolve_activity_log() -> str:
    """The configured log path, or ``""`` when telemetry is switched off.

    Setting ``LECO_MCP_ACTIVITY_LOG`` to an empty string is the documented off switch, so
    unset (use the default) and set-to-empty (disabled) must stay distinguishable.
    """
    raw = os.getenv(ENV_LOG_PATH)
    if raw is None:
        return str(default_activity_log())
    return raw.strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------------- sink


def make_event(
    event: str,
    *,
    session_id: str,
    transport: str,
    client: Mapping[str, Any] | None,
    tool: str | None = None,
    args_summary: Mapping[str, Any] | None = None,
    target: Mapping[str, Any] | None = None,
    ok: bool | None = None,
    error: str | None = None,
    blocked: bool | None = None,
    destructive: bool | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    """Build one schema-complete event. The only place event shape is decided."""
    return {
        "ts": _utc_now(),
        "event": event,
        "session_id": session_id,
        "transport": transport,
        "client": dict(client) if client else {"name": "unknown"},
        "tool": tool,
        "args_summary": dict(args_summary) if args_summary is not None else None,
        "target": dict(target) if target is not None else None,
        "ok": ok,
        "error": error,
        "blocked": blocked,
        "destructive": destructive,
        "duration_ms": duration_ms,
    }


class ActivityLog:
    """Append-only JSONL sink plus the in-memory view ``/insights`` reads.

    Appends are a single ``os.write`` to an ``O_APPEND`` descriptor, which the kernel keeps
    atomic for line-sized payloads. That is what lets a stdio server on the host and the
    HTTP container write the same file without coordinating: no locks are held across a
    write, and no process needs to know about the others.
    """

    def __init__(
        self,
        path: str | os.PathLike[str] | None,
        *,
        max_events: int = DEFAULT_MAX_EVENTS,
        recent_events: int = RECENT_EVENTS,
    ) -> None:
        self.path = Path(path) if path else None
        self.max_events = max(1, int(max_events))
        self._recent: deque[dict[str, Any]] = deque(maxlen=max(1, int(recent_events)))
        self._sessions: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._counts = {"tool_calls": 0, "blocked": 0, "errors": 0, "sessions": 0}
        self._lock = threading.Lock()
        self._lines: int | None = None  # lazily counted, then tracked in-process
        self._warned = False

    # ------------------------------------------------------------ public surface

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def record(self, event: Mapping[str, Any]) -> None:
        """Remember an event in memory and append it to the file. Never raises."""
        try:
            payload = {key: event.get(key) for key in EVENT_KEYS}
            with self._lock:
                self._recent.append(payload)
                self._track(payload)
            self._append(payload)
        except Exception as exc:  # noqa: BLE001 - telemetry must never fail a tool call
            self._warn(f"record failed: {type(exc).__name__}: {exc}")

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Newest-first slice of the in-memory ring."""
        with self._lock:
            items = list(self._recent)
        items.reverse()
        return items[: max(0, int(limit))]

    def sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(s) for s in self._sessions.values()]

    def counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def event_count(self) -> int:
        """Lines currently in the file (``0`` when disabled or absent). For ``doctor``."""
        if self.path is None:
            return 0
        try:
            return self._count_lines()
        except Exception:  # noqa: BLE001 - a diagnostic must not raise either
            return 0

    def writable(self) -> bool:
        """Whether the log (or the directory that would hold it) can be written."""
        if self.path is None:
            return False
        try:
            if self.path.exists():
                return os.access(self.path, os.W_OK)
            return self.path.parent.is_dir() and os.access(self.path.parent, os.W_OK)
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------ internals

    def _track(self, event: Mapping[str, Any]) -> None:
        """Update the per-session view. Caller holds the lock."""
        kind = event.get("event")
        if kind == "tool_call":
            self._counts["tool_calls"] += 1
            if event.get("blocked"):
                self._counts["blocked"] += 1
            elif event.get("ok") is False:
                self._counts["errors"] += 1
        elif kind == "session_start":
            self._counts["sessions"] += 1

        sid = str(event.get("session_id") or "")
        if not sid:
            return
        row = self._sessions.get(sid)
        if row is None:
            row = {
                "session_id": sid,
                "transport": event.get("transport"),
                "client": event.get("client"),
                "started_at": event.get("ts"),
                "last_seen": event.get("ts"),
                "call_count": 0,
            }
            self._sessions[sid] = row
            while len(self._sessions) > MAX_TRACKED_SESSIONS:
                self._sessions.popitem(last=False)
        row["last_seen"] = event.get("ts")
        client = event.get("client")
        if isinstance(client, Mapping) and client.get("name") not in (None, "", "unknown"):
            row["client"] = dict(client)
        if kind == "tool_call":
            row["call_count"] = int(row.get("call_count") or 0) + 1
        self._sessions.move_to_end(sid)

    def _append(self, payload: Mapping[str, Any]) -> None:
        if self.path is None:
            return
        line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
        data = line.encode("utf-8")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o666)
            try:
                os.write(fd, data)
            finally:
                os.close(fd)
        except Exception as exc:  # noqa: BLE001 - see class docstring
            self._warn(f"append failed: {type(exc).__name__}: {exc}")
            return
        self._maybe_rotate()

    def _maybe_rotate(self) -> None:
        """Trim to the newest ``max_events`` once the file drifts past ~1.5x the cap.

        Rewriting on every write would be absurd, and never rewriting lets an agent session
        fill a disk, so the file is allowed to overshoot by half a cap between trims.
        """
        try:
            with self._lock:
                if self._lines is None:
                    self._lines = self._count_lines()
                else:
                    self._lines += 1
                if self._lines <= int(self.max_events * 1.5):
                    return
                self._lines = self._rewrite_tail()
        except Exception as exc:  # noqa: BLE001
            self._warn(f"rotation failed: {type(exc).__name__}: {exc}")

    def _count_lines(self) -> int:
        if self.path is None or not self.path.exists():
            return 0
        with self.path.open("rb") as handle:
            return sum(1 for _ in handle)

    def _rewrite_tail(self) -> int:
        """Replace the file with its last ``max_events`` lines. Caller holds the lock."""
        assert self.path is not None
        with self.path.open("r", encoding="utf-8", errors="replace") as handle:
            kept = deque(handle, maxlen=self.max_events)
        tmp = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            handle.writelines(kept)
        # 0666 (minus umask) because host and container may run as different uids and both
        # append here; os.replace is atomic, so a concurrent reader never sees a half file.
        try:
            os.chmod(tmp, 0o666)
        except OSError:
            pass
        os.replace(tmp, self.path)
        return len(kept)

    def _warn(self, message: str) -> None:
        """One line to stderr per process. stdout is the stdio transport, so never there."""
        if self._warned:
            return
        self._warned = True
        print(f"leco-mcp activity log disabled after error: {message}", file=sys.stderr)


# ------------------------------------------------------------------- middleware


def _session_state(ctx: Any) -> tuple[dict[str, Any] | None, Any]:
    """The per-connection scratch record this middleware keeps, and the connection.

    ``ServerSession`` deliberately exposes no connection accessor, but the connection is
    what carries session identity, per-connection scratch state, and the teardown hook the
    ``session_end`` event needs. Reaching for it is tolerated here and degrades to
    "no session tracking" if the SDK moves it.
    """
    connection = getattr(getattr(ctx, "session", None), "_connection", None)  # noqa: SLF001
    if connection is None:
        return None, None
    state = getattr(connection, "state", None)
    if not isinstance(state, dict):
        return None, connection
    return state.get("leco_activity"), connection


def _transport_kind(ctx: Any) -> str:
    """``"http"`` or ``"stdio"`` — the only two values the contract allows."""
    outbound = getattr(getattr(ctx, "session", None), "_request_outbound", None)  # noqa: SLF001
    kind = getattr(getattr(outbound, "transport", None), "kind", "")
    if isinstance(kind, str) and ("http" in kind or kind == "sse"):
        return "http"
    if getattr(ctx, "request", None) is not None:
        return "http"
    return "stdio"


def _client_info(ctx: Any) -> dict[str, Any]:
    """Client identity from the initialize handshake, however far along it is.

    During ``initialize`` the handshake has not committed yet (the runner commits after the
    middleware chain returns), so the raw params are the only source; afterwards the
    negotiated ``client_params`` is.
    """
    raw = getattr(ctx, "params", None)
    if isinstance(raw, Mapping):
        info = raw.get("clientInfo") or raw.get("client_info")
        if isinstance(info, Mapping) and info.get("name"):
            out = {"name": str(info["name"])}
            if info.get("version"):
                out["version"] = str(info["version"])
            return out
    params = getattr(getattr(ctx, "session", None), "client_params", None)
    info = getattr(params, "client_info", None)
    if info is not None and getattr(info, "name", ""):
        out = {"name": str(info.name)}
        if getattr(info, "version", ""):
            out["version"] = str(info.version)
        return out
    return {"name": "unknown"}


def _headers(ctx: Any) -> Mapping[str, str]:
    outbound = getattr(getattr(ctx, "session", None), "_request_outbound", None)  # noqa: SLF001
    headers = getattr(getattr(outbound, "transport", None), "headers", None)
    if headers is None:
        headers = getattr(getattr(ctx, "request", None), "headers", None)
    return headers if isinstance(headers, Mapping) else {}


def derived_session_id(client: Mapping[str, Any], peer: str, user_agent: str) -> str:
    """A stable session id for a transport that does not issue one.

    At protocol 2026-07-28 streamable HTTP is single-exchange: every POST is its own
    connection and the server never sets ``Mcp-Session-Id``. Keying on the connection would
    therefore report one "session" per tool call, which tells a dashboard nothing. Hashing
    the caller's identity instead groups one agent's calls the way an operator expects,
    at the cost of merging two identical clients behind the same address.
    """
    seed = f"{client.get('name')}/{client.get('version')}|{peer}|{user_agent}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]  # noqa: S324 - not a secret


def _error_text(result: Any) -> str | None:
    """Pull the message out of an ``isError`` tool result, if that is what this is."""
    if not isinstance(result, Mapping):
        return None
    if not (result.get("isError") or result.get("is_error")):
        return None
    parts: list[str] = []
    content = result.get("content")
    if isinstance(content, Iterable) and not isinstance(content, (str, bytes)):
        for item in content:
            if isinstance(item, Mapping) and item.get("text"):
                parts.append(str(item["text"]))
    return " ".join(parts).strip() or "tool reported an error"


class ActivityMiddleware:
    """Records ``tools/call`` and session boundaries around the SDK's dispatch.

    Registered as a ``ServerMiddleware`` (a Protocol — anything ``(ctx, call_next)``-shaped
    qualifies), so it wraps every inbound message: ``initialize`` opens a session, each
    ``tools/call`` emits one event, and connection teardown closes the session.

    Session boundaries are only as real as the transport makes them. stdio holds one
    connection open per client, so its ``session_start``/``session_end`` pair is exact.
    Modern streamable HTTP answers each POST on its own connection and issues no
    ``Mcp-Session-Id``, so its session is derived from caller identity (see
    ``derived_session_id``): ``session_start`` is emitted once per identity per process and
    ``session_end`` is never emitted, because nothing observable ends.
    """

    def __init__(self, log: ActivityLog) -> None:
        self._log = log
        # Session ids already announced, so a derived id shared by many single-exchange
        # connections produces one session_start rather than one per request. Bounded.
        self._announced: OrderedDict[str, None] = OrderedDict()
        self._announce_lock = threading.Lock()

    async def __call__(self, ctx: "ServerRequestContext[Any, Any]", call_next: "CallNext") -> "HandlerResult":
        if not self._log.enabled:
            return await call_next(ctx)
        method = getattr(ctx, "method", "")
        if method == "initialize":
            return await self._with_session_start(ctx, call_next)
        if method == "tools/call":
            return await self._with_tool_call(ctx, call_next)
        # Everything else (tools/list, ping, notifications) is protocol noise for a
        # "who is doing what" view; ensure the session exists and pass through.
        self._ensure_session(ctx)
        return await call_next(ctx)

    # --------------------------------------------------------------- session hooks

    async def _with_session_start(self, ctx: Any, call_next: Any) -> Any:
        # After the chain, so a rejected handshake leaves no session behind.
        result = await call_next(ctx)
        self._ensure_session(ctx, client=_client_info(ctx))
        return result

    def _ensure_session(self, ctx: Any, client: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        """Return this connection's session record, opening one the first time.

        A handshake normally opens it. Transports that answer a single exchange without an
        ``initialize`` request never reach that path, so the first request on the connection
        synthesizes the session instead — the ``session_start`` event is then derived from
        that request rather than from a real handshake.
        """
        state, connection = _session_state(ctx)
        if state is not None:
            if client and client.get("name") != "unknown" and state["client"].get("name") == "unknown":
                state["client"] = dict(client)
            return state
        if connection is None or not isinstance(getattr(connection, "state", None), dict):
            return None

        transport = _transport_kind(ctx)
        identity = dict(client or _client_info(ctx))
        assigned = getattr(connection, "session_id", None)
        if assigned:
            session_id, durable = str(assigned), True
        elif transport == "http":
            # No transport session and one connection per POST: derive an id that survives
            # the connection, and accept that there is no observable end for it.
            headers = _headers(ctx)
            peer = getattr(getattr(ctx, "request", None), "client", None)
            session_id = derived_session_id(
                identity,
                getattr(peer, "host", "") or headers.get("x-forwarded-for", ""),
                headers.get("user-agent", ""),
            )
            durable = False
        else:
            session_id, durable = uuid.uuid4().hex[:12], True

        state = {"session_id": session_id, "transport": transport, "client": identity}
        connection.state["leco_activity"] = state
        if self._announce(session_id):
            self._log.record(make_event("session_start", **state))
        if durable:
            self._register_session_end(connection, state)
        return state

    def _announce(self, session_id: str) -> bool:
        """True the first time this process sees a session id."""
        with self._announce_lock:
            if session_id in self._announced:
                return False
            self._announced[session_id] = None
            while len(self._announced) > MAX_TRACKED_SESSIONS:
                self._announced.popitem(last=False)
            return True

    def _register_session_end(self, connection: Any, state: Mapping[str, Any]) -> None:
        """Emit ``session_end`` when the connection tears down.

        ``Connection.exit_stack`` is unwound (shielded) by the SDK when the connection
        closes — that is the session-end hook. Only registered for sessions whose lifetime
        is the connection's; a derived single-exchange id outlives every connection that
        carries it, so no end can honestly be reported for one.
        """
        exit_stack = getattr(connection, "exit_stack", None)
        push = getattr(exit_stack, "push_async_callback", None)
        if not callable(push):
            return
        log = self._log
        payload = dict(state)

        async def _on_close() -> None:
            log.record(make_event("session_end", **payload))

        try:
            push(_on_close)
        except Exception as exc:  # noqa: BLE001 - a missing end event is not a failure
            self._log._warn(f"session_end hook failed: {type(exc).__name__}: {exc}")  # noqa: SLF001

    # ------------------------------------------------------------------ tool calls

    async def _with_tool_call(self, ctx: Any, call_next: Any) -> Any:
        session = self._ensure_session(ctx) or {
            "session_id": "unknown",
            "transport": _transport_kind(ctx),
            "client": _client_info(ctx),
        }
        params = getattr(ctx, "params", None) or {}
        tool = str(params.get("name") or "") if isinstance(params, Mapping) else ""
        raw_args = params.get("arguments") if isinstance(params, Mapping) else None
        args = raw_args if isinstance(raw_args, Mapping) else {}

        marks = begin_call()
        started = time.perf_counter()
        ok = True
        error: str | None = None
        try:
            result = await call_next(ctx)
        except Exception as exc:  # noqa: BLE001 - observed, recorded, and re-raised
            ok = False
            error = f"{type(exc).__name__}: {exc}"
            self._emit(session, tool, args, ok, error, marks, started)
            raise
        error = _error_text(result)
        ok = error is None
        self._emit(session, tool, args, ok, error, marks, started)
        return result

    def _emit(
        self,
        session: Mapping[str, Any],
        tool: str,
        args: Mapping[str, Any],
        ok: bool,
        error: str | None,
        marks: Mapping[str, bool],
        started: float,
    ) -> None:
        self._log.record(
            make_event(
                "tool_call",
                session_id=str(session.get("session_id") or "unknown"),
                transport=str(session.get("transport") or "stdio"),
                client=session.get("client"),
                tool=tool,
                args_summary=summarize_args(args),
                target=resolve_target(tool, args),
                ok=ok,
                error=(error[:600] if error else None),
                blocked=bool(marks.get("blocked")),
                destructive=bool(marks.get("destructive")) or looks_destructive(tool, args),
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        )


# ---------------------------------------------------------------- insights route


def register_insights_route(
    server: Any,
    log: ActivityLog,
    *,
    name: str,
    version: str,
    settings: Any,
    tool_count: Callable[[], int],
    started_at: float,
) -> None:
    """Expose ``GET /insights`` on the streamable-HTTP app.

    Read-only and served from memory: the dashboard polls this, so it must never cost more
    than a dict copy, and it must never surface configuration that carries a secret.
    """
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    @server.custom_route("/insights", methods=["GET"])
    async def insights(request: Request) -> JSONResponse:  # noqa: D401 - route handler
        try:
            limit = min(max(int(request.query_params.get("limit", "50")), 0), 500)
        except ValueError:
            limit = 50
        return JSONResponse(
            {
                "ok": True,
                "server": {
                    "name": name,
                    "version": version,
                    # This route only exists on the streamable-HTTP app.
                    "transport": "http",
                    "tool_count": tool_count(),
                    "destructive_enabled": bool(getattr(settings, "allow_destructive", False)),
                    "credentials_enabled": bool(getattr(settings, "allow_credentials", False)),
                    "dashboard_url": (getattr(settings, "base_urls", ()) or ("",))[0],
                    "uptime_seconds": round(time.monotonic() - started_at, 3),
                },
                "sessions": log.sessions(),
                "recent": log.recent(limit),
                "counts": log.counts(),
            }
        )
