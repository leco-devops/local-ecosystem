"""Activity telemetry: line schema, redaction, rotation, and target resolution.

The written line is a contract other tools parse, so these tests assert the shape as
written to disk rather than the shape of the in-memory dict.
"""

import json

import pytest

from leco_mcp.activity import (
    DEFAULT_LOG_RELPATH,
    EVENT_KEYS,
    MAX_ARG_CHARS,
    ActivityLog,
    begin_call,
    default_activity_log,
    derived_session_id,
    make_event,
    note_blocked,
    resolve_activity_log,
    resolve_target,
    summarize_args,
)
from leco_mcp.config import Settings
from leco_mcp.safety import ActionBlocked, guard_credentials


def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def tool_event(**over):
    base = dict(
        session_id="s1",
        transport="stdio",
        client={"name": "claude-code", "version": "2.0.1"},
        tool="leco_control",
        args_summary={"target_id": "ai-ollama", "action": "start"},
        target={"kind": "service", "id": "ai-ollama"},
        ok=True,
        error=None,
        blocked=False,
        destructive=False,
        duration_ms=12,
    )
    base.update(over)
    return make_event("tool_call", **base)


# ----------------------------------------------------------------- schema shape


def test_written_line_carries_exactly_the_contract_keys(tmp_path):
    path = tmp_path / "activity.jsonl"
    log = ActivityLog(path)
    log.record(tool_event())

    (line,) = read_lines(path)
    assert tuple(line.keys()) == EVENT_KEYS
    assert line["event"] == "tool_call"
    assert line["client"] == {"name": "claude-code", "version": "2.0.1"}
    assert line["target"] == {"kind": "service", "id": "ai-ollama"}
    assert line["ts"].endswith("+00:00")


def test_session_events_null_out_the_call_scoped_half(tmp_path):
    path = tmp_path / "activity.jsonl"
    log = ActivityLog(path)
    log.record(make_event("session_start", session_id="s1", transport="http", client=None))
    log.record(make_event("session_end", session_id="s1", transport="http", client=None))

    start, end = read_lines(path)
    assert start["event"] == "session_start"
    assert end["event"] == "session_end"
    for key in ("tool", "args_summary", "target", "ok", "error", "blocked", "destructive",
                "duration_ms"):
        assert start[key] is None, key
    # No client info from the handshake still produces a well-formed client object.
    assert start["client"] == {"name": "unknown"}


def test_blocked_is_recorded_separately_from_a_plain_failure(tmp_path):
    path = tmp_path / "activity.jsonl"
    log = ActivityLog(path)
    log.record(tool_event(ok=False, error="boom", blocked=False))
    log.record(tool_event(ok=False, error="Blocked destructive action", blocked=True,
                          destructive=True))

    failed, blocked = read_lines(path)
    assert (failed["ok"], failed["blocked"]) == (False, False)
    assert (blocked["ok"], blocked["blocked"], blocked["destructive"]) == (False, True, True)
    assert log.counts() == {"tool_calls": 2, "blocked": 1, "errors": 1, "sessions": 0}


# -------------------------------------------------------------------- redaction


def test_credential_bearing_arguments_never_reach_the_file(tmp_path):
    path = tmp_path / "activity.jsonl"
    log = ActivityLog(path)
    args = {
        "slug": "sftp",
        "values": {"password": "hunter2-do-not-log", "username": "admin"},
        "write_content": "SECRET-FILE-BODY",
        "manifest_yaml": "token: SECRET-YAML",
        "set_config": {"api_key": "SECRET-KEY"},
        "password": "SECRET-SCALAR",
    }
    log.record(tool_event(tool="leco_ui_credentials_set", args_summary=summarize_args(args)))

    raw = path.read_text()
    for secret in ("hunter2-do-not-log", "SECRET-FILE-BODY", "SECRET-YAML", "SECRET-KEY",
                   "SECRET-SCALAR"):
        assert secret not in raw
    (line,) = read_lines(path)
    assert line["args_summary"] == {"slug": "sftp"}


def test_summarize_keeps_whitelisted_scalars_and_truncates_them():
    summary = summarize_args(
        {
            "target_id": "ai-ollama",
            "action": "reset",
            "confirm": True,
            "dry_run": False,
            "tail_lines": 500,  # not whitelisted
            "path": "wsp:" + "x" * 500,
            "routers": ["a", "b"],  # list payload
        }
    )
    assert summary["target_id"] == "ai-ollama"
    assert summary["confirm"] is True
    assert summary["dry_run"] is False
    assert "tail_lines" not in summary
    assert "routers" not in summary
    assert len(summary["path"]) == MAX_ARG_CHARS


# ------------------------------------------------------------ target resolution


@pytest.mark.parametrize(
    "tool, args, expected",
    [
        # one representative per family
        ("leco_control", {"target_id": "ai-ollama"}, {"kind": "service", "id": "ai-ollama"}),
        ("leco_app_control", {"slug": "botfeed"}, {"kind": "app", "id": "leco-stack-botfeed"}),
        ("leco_dev_stack_action", {"stack_id": "wp-demo"},
         {"kind": "dev_stack", "id": "wp-demo"}),
        ("leco_llm_model_action", {"model": "llama3:8b"}, {"kind": "model", "id": "llama3:8b"}),
        ("leco_detect", {"path": "wsp:CrawlerVision"},
         {"kind": "path", "id": "wsp:CrawlerVision"}),
        ("leco_manifest_save", {"path": "wsp:App"}, {"kind": "path", "id": "wsp:App"}),
        ("leco_platform_service_action", {"service_id": "ollama"},
         {"kind": "service", "id": "ollama"}),
        ("leco_ui_credentials", {"slug": "sftp"}, {"kind": "app", "id": "sftp"}),
        # a hosted-app control target is an app even when reached through leco_control
        ("leco_control", {"target_id": "leco-stack-botfeed"},
         {"kind": "app", "id": "leco-stack-botfeed"}),
        # no identifiable target
        ("leco_status", {}, {"kind": "none", "id": None}),
        ("leco_llm_models", {"runtime": "ollama"}, {"kind": "none", "id": None}),
        ("leco_apps", {}, {"kind": "none", "id": None}),
    ],
)
def test_target_resolution_per_tool_family(tool, args, expected):
    assert resolve_target(tool, args) == expected


# --------------------------------------------------------------------- rotation


def test_file_is_trimmed_to_the_cap_once_it_overshoots(tmp_path):
    path = tmp_path / "activity.jsonl"
    log = ActivityLog(path, max_events=10)
    for i in range(40):
        log.record(tool_event(duration_ms=i))

    lines = read_lines(path)
    # Trims at ~1.5x the cap, so the file never exceeds that and keeps the newest events.
    assert len(lines) <= 15
    assert lines[-1]["duration_ms"] == 39
    assert lines == sorted(lines, key=lambda e: e["duration_ms"])


def test_recent_ring_is_newest_first_and_bounded(tmp_path):
    log = ActivityLog(tmp_path / "activity.jsonl", max_events=1000, recent_events=5)
    for i in range(20):
        log.record(tool_event(duration_ms=i))

    recent = log.recent(50)
    assert len(recent) == 5
    assert [e["duration_ms"] for e in recent] == [19, 18, 17, 16, 15]


# ------------------------------------------------------------------ off / broken


def test_disabled_mode_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("LECO_MCP_ACTIVITY_LOG", "")
    settings = Settings.from_env()
    assert settings.activity_log_path == ""
    assert settings.activity_enabled is False

    log = ActivityLog(settings.activity_log_path or None)
    assert log.enabled is False
    log.record(tool_event())
    assert list(tmp_path.iterdir()) == []
    # In-memory history still works, so /insights stays useful with the file switched off.
    assert len(log.recent()) == 1


def test_unset_env_falls_back_to_the_generated_directory(monkeypatch):
    monkeypatch.delenv("LECO_MCP_ACTIVITY_LOG", raising=False)
    monkeypatch.setenv("LECO_MCP_PROJECT_ROOT", "/project")
    assert resolve_activity_log() == f"/project/{DEFAULT_LOG_RELPATH}"
    assert default_activity_log("/somewhere").as_posix() == f"/somewhere/{DEFAULT_LOG_RELPATH}"


def test_a_malformed_path_never_raises(tmp_path, capsys):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("i am a file")
    log = ActivityLog(blocker / "nested" / "activity.jsonl")

    log.record(tool_event())  # must not raise
    log.record(tool_event())

    assert log.event_count() == 0
    assert "activity log" in capsys.readouterr().err


def test_record_survives_an_unserializable_payload(tmp_path):
    path = tmp_path / "activity.jsonl"
    log = ActivityLog(path)
    log.record(tool_event(args_summary={"detail": object()}))
    (line,) = read_lines(path)
    assert line["args_summary"]["detail"].startswith("<object object")


# ------------------------------------------------------------------ safety marks


def test_a_safety_refusal_marks_the_in_flight_call():
    marks = begin_call()
    with pytest.raises(ActionBlocked):
        guard_credentials(Settings(allow_credentials=False))
    assert marks["blocked"] is True


def test_derived_session_id_is_stable_per_caller():
    claude = {"name": "claude-code", "version": "2.0.1"}
    first = derived_session_id(claude, "172.18.0.1", "python-httpx/0.28")
    assert first == derived_session_id(claude, "172.18.0.1", "python-httpx/0.28")
    assert first != derived_session_id(claude, "172.18.0.9", "python-httpx/0.28")
    assert first != derived_session_id({"name": "other", "version": "1"}, "172.18.0.1",
                                       "python-httpx/0.28")
    assert len(first) == 12


def test_note_blocked_outside_a_call_is_a_no_op():
    note_blocked()  # no ContextVar record in scope; must not raise
