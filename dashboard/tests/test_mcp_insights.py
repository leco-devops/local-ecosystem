"""Tests for mcp_insights: activity-log parsing, aggregation, filtering, and the app join."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_DASH = Path(__file__).resolve().parents[1]
if str(_DASH) not in sys.path:
    sys.path.insert(0, str(_DASH))

import mcp_insights  # noqa: E402
from mcp_insights import (  # noqa: E402
    app_slug_from_target_id,
    build_install,
    build_server_block,
    event_app_key,
    load_events,
    normalize_event,
    parse_ts,
    read_tail_lines,
    summarize_events,
)

REPO_ROOT = _DASH.parent


def iso(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def call(
    *,
    ts: str,
    session: str = "s1",
    tool: str = "leco_status",
    kind: str | None = "service",
    target_id: str | None = "ai-ollama",
    ok: bool = True,
    error: str | None = None,
    blocked: bool = False,
    destructive: bool = False,
    duration_ms: int | None = 100,
    args: dict | None = None,
    client: str = "claude-code",
    transport: str = "stdio",
    event: str = "tool_call",
) -> dict:
    return {
        "ts": ts,
        "event": event,
        "session_id": session,
        "transport": transport,
        "client": {"name": client, "version": "2.0.1"},
        "tool": tool,
        "args_summary": args if args is not None else {},
        "target": {"kind": kind, "id": target_id},
        "ok": ok,
        "error": error,
        "blocked": blocked,
        "destructive": destructive,
        "duration_ms": duration_ms,
    }


def write_log(path: Path, records: list, *, trailing_newline: bool = True) -> None:
    body = "\n".join(r if isinstance(r, str) else json.dumps(r) for r in records)
    if trailing_newline:
        body += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


class TempProjectRoot:
    """Point DASHBOARD_PROJECT_ROOT at a throwaway tree for the duration of a test."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._prev_root: str | None = None
        self._prev_log: str | None = None

    def __enter__(self) -> "TempProjectRoot":
        self._prev_root = os.environ.get("DASHBOARD_PROJECT_ROOT")
        self._prev_log = os.environ.get("LECO_MCP_ACTIVITY_LOG")
        os.environ["DASHBOARD_PROJECT_ROOT"] = str(self.root)
        os.environ.pop("LECO_MCP_ACTIVITY_LOG", None)
        return self

    def __exit__(self, *exc) -> None:
        for key, prev in (
            ("DASHBOARD_PROJECT_ROOT", self._prev_root),
            ("LECO_MCP_ACTIVITY_LOG", self._prev_log),
        ):
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev
        self._tmp.cleanup()

    @property
    def log(self) -> Path:
        return self.root / mcp_insights.ACTIVITY_LOG_REL


# ---------------------------------------------------------------------------


class TestTimestamps(unittest.TestCase):
    def test_offset_and_z_forms(self) -> None:
        a = parse_ts("2026-08-17T10:00:00.000000+00:00")
        b = parse_ts("2026-08-17T10:00:00Z")
        self.assertEqual(a, b)

    def test_naive_treated_as_utc(self) -> None:
        self.assertEqual(parse_ts("2026-08-17T10:00:00").tzinfo, timezone.utc)

    def test_garbage_returns_none(self) -> None:
        for bad in ("", None, "not-a-date", 17, {}):
            self.assertIsNone(parse_ts(bad))


class TestLogLoading(unittest.TestCase):
    def test_missing_file_is_not_an_error(self) -> None:
        with TempProjectRoot() as tmp:
            events, stats = load_events()
            self.assertEqual(events, [])
            self.assertFalse(stats["exists"])
            self.assertEqual(stats["events"], 0)
            self.assertEqual(stats["malformed_lines"], 0)
            self.assertEqual(stats["path"], str(tmp.log))

    def test_malformed_lines_are_skipped_and_counted(self) -> None:
        with TempProjectRoot() as tmp:
            write_log(
                tmp.log,
                [
                    call(ts=iso(5)),
                    "{ this is not json",
                    "",
                    "[1,2,3]",  # valid JSON, wrong shape
                    call(ts=iso(4)),
                ],
            )
            events, stats = load_events()
            self.assertEqual(len(events), 2)
            self.assertEqual(stats["events"], 2)
            self.assertEqual(stats["malformed_lines"], 2)

    def test_partial_last_line_is_not_malformed(self) -> None:
        with TempProjectRoot() as tmp:
            write_log(
                tmp.log,
                [call(ts=iso(5)), '{"ts":"2026-08-17T10:00:00+00:00","event":"tool_'],
                trailing_newline=False,
            )
            events, stats = load_events()
            self.assertEqual(len(events), 1)
            self.assertEqual(stats["malformed_lines"], 0)

    def test_unknown_keys_tolerated_and_dropped(self) -> None:
        record = call(ts=iso(1))
        record["future_field"] = {"nested": True}
        with TempProjectRoot() as tmp:
            write_log(tmp.log, [record])
            events, _ = load_events()
            self.assertNotIn("future_field", events[0])

    def test_unreadable_path_reports_absent(self) -> None:
        with TempProjectRoot() as tmp:
            tmp.log.parent.mkdir(parents=True, exist_ok=True)
            tmp.log.mkdir()  # a directory where the log should be
            events, stats = load_events()
            self.assertEqual(events, [])
            self.assertFalse(stats["exists"])

    def test_tail_is_bounded_and_drops_partial_head(self) -> None:
        with TempProjectRoot() as tmp:
            write_log(tmp.log, [call(ts=iso(i), session=f"s{i}") for i in range(50)])
            lines, partial = read_tail_lines(tmp.log, max_bytes=600, max_lines=10)
            self.assertFalse(partial)
            self.assertLessEqual(len(lines), 10)
            for line in lines:
                json.loads(line)  # every returned line is a whole record

    def test_max_lines_keeps_the_newest(self) -> None:
        with TempProjectRoot() as tmp:
            write_log(tmp.log, [call(ts=iso(60 - i), tool=f"t{i}") for i in range(20)])
            lines, _ = read_tail_lines(tmp.log, max_lines=3)
            self.assertEqual(len(lines), 3)
            self.assertEqual(json.loads(lines[-1])["tool"], "t19")


class TestNormalize(unittest.TestCase):
    def test_sensitive_values_are_redacted(self) -> None:
        event = normalize_event(
            call(ts=iso(1), args={"target_id": "ai-ollama", "password": "hunter2", "api_key": "sk-x"})
        )
        self.assertEqual(event["args_summary"]["target_id"], "ai-ollama")
        self.assertEqual(event["args_summary"]["password"], "***")
        self.assertEqual(event["args_summary"]["api_key"], "***")

    def test_missing_fields_degrade_to_none(self) -> None:
        event = normalize_event({"event": "tool_call"})
        self.assertIsNone(event["session_id"])
        self.assertIsNone(event["tool"])
        self.assertIsNone(event["ok"])
        self.assertFalse(event["blocked"])
        self.assertEqual(event["target"], {"kind": None, "id": None})

    def test_bool_is_not_a_duration(self) -> None:
        self.assertIsNone(normalize_event({"duration_ms": True})["duration_ms"])


class TestAggregation(unittest.TestCase):
    def build(self) -> list[dict]:
        raw = [
            call(ts=iso(10), event="session_start", tool=None, kind=None, target_id=None),
            call(ts=iso(9), tool="leco_status", duration_ms=100),
            call(ts=iso(8), tool="leco_status", duration_ms=300),
            call(ts=iso(7), tool="leco_control", target_id="ai-ollama", ok=False,
                 error="boom", duration_ms=50),
            call(ts=iso(6), tool="leco_control", target_id="ai-ollama", blocked=True,
                 destructive=True, ok=False, error="blocked", duration_ms=None),
            call(ts=iso(5), session="s2", client="cursor", transport="http",
                 tool="leco_app_control", kind="app", target_id="leco-stack-shop"),
            call(ts=iso(2000), tool="leco_status"),  # ~33h ago, outside a 24h window
        ]
        return [normalize_event(r) for r in raw]

    def test_counts(self) -> None:
        out = summarize_events(self.build(), hours=24, limit=50)
        self.assertEqual(out["counts"]["tool_calls"], 5)
        self.assertEqual(out["counts"]["errors"], 2)
        self.assertEqual(out["counts"]["blocked"], 1)
        self.assertEqual(out["counts"]["destructive"], 1)
        self.assertEqual(out["counts"]["sessions"], 2)
        self.assertEqual(out["counts"]["window_hours"], 24)

    def test_window_widening_includes_older_events(self) -> None:
        out = summarize_events(self.build(), hours=72, limit=50)
        self.assertEqual(out["counts"]["tool_calls"], 6)

    def test_sessions(self) -> None:
        out = summarize_events(self.build(), hours=24, limit=50)
        by_id = {s["session_id"]: s for s in out["sessions"]}
        s1 = by_id["s1"]
        self.assertEqual(s1["call_count"], 4)
        self.assertEqual(s1["error_count"], 2)
        self.assertEqual(s1["blocked_count"], 1)
        self.assertEqual(s1["tools"], ["leco_control", "leco_status"])
        self.assertEqual(s1["transport"], "stdio")
        self.assertEqual(s1["client_name"], "claude-code")
        self.assertLess(s1["first_seen"], s1["last_seen"])
        self.assertEqual(by_id["s2"]["client_name"], "cursor")
        self.assertEqual(by_id["s2"]["transport"], "http")
        # newest activity first
        self.assertEqual(out["sessions"][0]["session_id"], "s2")

    def test_by_tool(self) -> None:
        out = summarize_events(self.build(), hours=24, limit=50)
        by_tool = {r["tool"]: r for r in out["by_tool"]}
        self.assertEqual(by_tool["leco_status"]["count"], 2)
        self.assertEqual(by_tool["leco_status"]["avg_ms"], 200.0)
        self.assertEqual(by_tool["leco_status"]["errors"], 0)
        self.assertEqual(by_tool["leco_control"]["count"], 2)
        self.assertEqual(by_tool["leco_control"]["errors"], 2)
        self.assertEqual(by_tool["leco_control"]["blocked"], 1)
        self.assertEqual(by_tool["leco_control"]["avg_ms"], 50.0)  # only the timed call
        self.assertEqual(out["by_tool"][0]["count"], 2)  # sorted by count desc

    def test_by_target_labels_resolve(self) -> None:
        apps = [{"id": "shop", "label": "Shop", "runtime": {"status": "running"}}]
        out = summarize_events(self.build(), hours=24, limit=50, apps=apps)
        by_id = {r["id"]: r for r in out["by_target"]}
        self.assertEqual(by_id["ai-ollama"]["label"], "Ollama")  # from control_targets
        self.assertEqual(by_id["ai-ollama"]["count"], 4)
        self.assertEqual(by_id["leco-stack-shop"]["label"], "Shop")  # hosted app label
        self.assertEqual(by_id["leco-stack-shop"]["last_tool"], "leco_app_control")

    def test_recent_is_newest_first_and_capped(self) -> None:
        out = summarize_events(self.build(), hours=24, limit=2)
        self.assertEqual(len(out["recent"]), 2)
        self.assertEqual(out["recent"][0]["tool"], "leco_app_control")
        self.assertTrue(all(e["event"] == "tool_call" for e in out["recent"]))
        self.assertNotIn("_ts", out["recent"][0])

    def test_events_without_timestamps_are_excluded_from_the_window(self) -> None:
        events = self.build() + [normalize_event({"event": "tool_call", "tool": "x"})]
        out = summarize_events(events, hours=24, limit=50)
        self.assertEqual(out["counts"]["tool_calls"], 5)


class TestAppJoin(unittest.TestCase):
    APPS = [
        {"id": "shop", "label": "Shop", "runtime": {"status": "running"},
         "main_url": "https://shop.lh", "target_id": "leco-stack-shop"},
        {"id": "blog", "label": "Blog", "runtime": {"status": "stopped"},
         "main_url": "https://blog.lh", "target_id": "leco-stack-blog"},
        {"id": "idle", "label": "Idle app", "runtime": {"status": "stopped"},
         "main_url": "", "target_id": "leco-stack-idle"},
    ]

    def events(self) -> list[dict]:
        return [
            normalize_event(r)
            for r in [
                # matched by leco-stack-<slug> target id
                call(ts=iso(9), tool="leco_app_control", kind="app", target_id="leco-stack-shop"),
                call(ts=iso(8), tool="leco_app_logs", kind="app", target_id="leco-stack-shop"),
                # matched by bare slug
                call(ts=iso(7), tool="leco_app_snapshot", kind="app", target_id="shop",
                     ok=False, error="nope"),
                # matched by args_summary when the target is untyped
                call(ts=iso(6), tool="leco_app_metrics", kind=None, target_id=None,
                     args={"slug": "blog"}),
                # a service target must never be counted as an app
                call(ts=iso(5), tool="leco_control", kind="service", target_id="ai-ollama"),
            ]
        ]

    def test_slug_and_target_id_both_join(self) -> None:
        out = summarize_events(self.events(), hours=24, limit=50, apps=self.APPS)
        by_slug = {a["slug"]: a for a in out["apps"]}
        self.assertEqual(by_slug["shop"]["mcp_calls"], 3)
        self.assertEqual(by_slug["blog"]["mcp_calls"], 1)
        self.assertEqual(by_slug["idle"]["mcp_calls"], 0)
        self.assertNotIn("ai-ollama", by_slug)

    def test_last_call_fields(self) -> None:
        out = summarize_events(self.events(), hours=24, limit=50, apps=self.APPS)
        shop = next(a for a in out["apps"] if a["slug"] == "shop")
        self.assertEqual(shop["last_tool"], "leco_app_snapshot")
        self.assertIs(shop["last_ok"], False)
        self.assertTrue(shop["running"])
        self.assertEqual(shop["main_url"], "https://shop.lh")

    def test_whole_fleet_present_with_active_first(self) -> None:
        out = summarize_events(self.events(), hours=24, limit=50, apps=self.APPS)
        self.assertEqual([a["slug"] for a in out["apps"]], ["shop", "blog", "idle"])
        self.assertEqual(out["apps"][-1]["mcp_calls"], 0)

    def test_activity_for_an_unregistered_app_still_shows(self) -> None:
        events = self.events() + [
            normalize_event(call(ts=iso(3), tool="leco_app_logs", kind="app",
                                 target_id="leco-stack-ghost"))
        ]
        out = summarize_events(events, hours=24, limit=50, apps=self.APPS)
        ghost = next(a for a in out["apps"] if a["slug"] == "ghost")
        self.assertEqual(ghost["mcp_calls"], 1)
        self.assertFalse(ghost["running"])
        self.assertFalse(ghost["registered"])
        self.assertTrue(next(a for a in out["apps"] if a["slug"] == "shop")["registered"])

    def test_target_id_helpers(self) -> None:
        self.assertEqual(app_slug_from_target_id("leco-stack-shop"), "shop")
        self.assertEqual(app_slug_from_target_id("ai-ollama"), "ai-ollama")
        self.assertIsNone(app_slug_from_target_id(None))
        self.assertIsNone(event_app_key(normalize_event(call(ts=iso(1), kind="service",
                                                             target_id="ai-ollama"))))
        self.assertEqual(
            event_app_key(normalize_event(call(ts=iso(1), kind="app", target_id="shop"))), "shop"
        )


class TestActivityQuery(unittest.TestCase):
    def seed(self, tmp: TempProjectRoot) -> None:
        write_log(
            tmp.log,
            [
                call(ts=iso(9), tool="leco_status", kind="none", target_id=None),
                call(ts=iso(8), tool="leco_control", target_id="ai-ollama", ok=False, error="boom"),
                call(ts=iso(7), tool="leco_control", target_id="ai-ollama", blocked=True),
                call(ts=iso(6), session="s2", tool="leco_app_logs", kind="app",
                     target_id="leco-stack-shop"),
                call(ts=iso(3000), tool="leco_status", kind="none", target_id=None),  # outside 24h
            ],
        )

    def test_filters(self) -> None:
        with TempProjectRoot() as tmp:
            self.seed(tmp)
            q = mcp_insights.query_activity

            self.assertEqual(q()["total_matched"], 4)
            self.assertEqual(q(hours=720)["total_matched"], 5)
            self.assertEqual(q(tool="leco_control")["total_matched"], 2)
            self.assertEqual(q(session="s2")["total_matched"], 1)
            self.assertEqual(q(target="ai-ollama")["total_matched"], 2)
            self.assertEqual(q(target="shop")["total_matched"], 1)
            self.assertEqual(q(target="app")["total_matched"], 1)
            self.assertEqual(q(blocked="true")["total_matched"], 1)
            self.assertEqual(q(errors_only="true")["total_matched"], 1)
            self.assertEqual(q(tool="leco_control", blocked="true")["total_matched"], 1)

    def test_newest_first_and_limit(self) -> None:
        with TempProjectRoot() as tmp:
            self.seed(tmp)
            out = mcp_insights.query_activity(limit=2)
            self.assertEqual(out["returned"], 2)
            self.assertEqual(out["total_matched"], 4)
            self.assertEqual(out["events"][0]["tool"], "leco_app_logs")
            self.assertNotIn("_ts", out["events"][0])

    def test_bounds_are_clamped(self) -> None:
        with TempProjectRoot() as tmp:
            self.seed(tmp)
            self.assertEqual(mcp_insights.query_activity(limit=99999)["limit"], 500)
            self.assertEqual(mcp_insights.query_activity(limit="abc")["limit"], 100)
            self.assertEqual(mcp_insights.query_activity(hours=99999)["window_hours"], 720)
            self.assertEqual(mcp_insights.query_activity(hours=-5)["window_hours"], 1)

    def test_empty_state(self) -> None:
        with TempProjectRoot():
            out = mcp_insights.query_activity()
            self.assertTrue(out["ok"])
            self.assertEqual(out["events"], [])
            self.assertFalse(out["activity_log"]["exists"])


class TestServerBlock(unittest.TestCase):
    def setUp(self) -> None:
        self._container = mcp_insights.container_running
        mcp_insights.container_running = lambda name=mcp_insights.CONTAINER_NAME: (False, False)

    def tearDown(self) -> None:
        mcp_insights.container_running = self._container

    def test_never_run_reports_not_installed(self) -> None:
        with TempProjectRoot():
            _, stats = load_events()
            block = build_server_block(stats, None)
            self.assertFalse(block["installed"])
            self.assertFalse(block["running"])
            self.assertFalse(block["reachable"])
            self.assertEqual(block["container"], "leco-mcp")
            self.assertEqual(block["endpoints"]["http"], "https://mcp.lh/mcp")
            self.assertIsNone(block["destructive_enabled"])
            self.assertFalse(block["activity_log"]["exists"])

    def test_probe_supplies_live_fields(self) -> None:
        with TempProjectRoot():
            _, stats = load_events()
            block = build_server_block(
                stats,
                {"version": "9.9.9", "tool_count": 60, "allow_destructive": True,
                 "credentials_enabled": False},
            )
            self.assertTrue(block["reachable"])
            self.assertTrue(block["running"])
            self.assertTrue(block["installed"])
            self.assertEqual(block["version"], "9.9.9")
            self.assertEqual(block["tool_count"], 60)
            self.assertTrue(block["destructive_enabled"])
            self.assertFalse(block["credentials_enabled"])

    def test_probe_fields_nested_under_server(self) -> None:
        """The live server reports {"ok":…,"server":{…},"sessions":[…]} — unwrap it."""
        with TempProjectRoot():
            _, stats = load_events()
            block = build_server_block(
                stats,
                {
                    "ok": True,
                    "server": {"name": "leco-devops", "version": "0.1.0", "transport": "http",
                               "tool_count": 60, "destructive_enabled": False,
                               "credentials_enabled": False},
                    "sessions": [],
                },
            )
            self.assertEqual(block["version"], "0.1.0")
            self.assertEqual(block["tool_count"], 60)
            self.assertIs(block["destructive_enabled"], False)
            self.assertIs(block["credentials_enabled"], False)

    def test_activity_log_alone_counts_as_installed(self) -> None:
        with TempProjectRoot() as tmp:
            write_log(tmp.log, [call(ts=iso(1))])
            _, stats = load_events()
            block = build_server_block(stats, None)
            self.assertTrue(block["installed"])
            self.assertFalse(block["running"])
            self.assertEqual(block["activity_log"]["events"], 1)

    def test_version_and_tool_count_from_the_real_repo(self) -> None:
        prev = os.environ.get("DASHBOARD_PROJECT_ROOT")
        os.environ["DASHBOARD_PROJECT_ROOT"] = str(REPO_ROOT)
        mcp_insights._TOOL_COUNT_CACHE["root"] = None
        try:
            _, stats = load_events()
            block = build_server_block(stats, None)
            self.assertEqual(block["version"], "0.1.0")
            self.assertIsInstance(block["tool_count"], int)
            self.assertGreaterEqual(block["tool_count"], 50)
        finally:
            mcp_insights._TOOL_COUNT_CACHE["root"] = None
            if prev is None:
                os.environ.pop("DASHBOARD_PROJECT_ROOT", None)
            else:
                os.environ["DASHBOARD_PROJECT_ROOT"] = prev


class TestLiveSessionMerge(unittest.TestCase):
    def test_live_sessions_are_flagged_and_added(self) -> None:
        sessions = [{"session_id": "s1", "call_count": 2}]
        merged = mcp_insights._merge_live_sessions(
            sessions,
            {"sessions": [{"session_id": "s1"},
                          {"session_id": "s9", "transport": "http",
                           "client": {"name": "cursor", "version": "1.0"}}]},
        )
        by_id = {s["session_id"]: s for s in merged}
        self.assertTrue(by_id["s1"]["live"])
        self.assertTrue(by_id["s9"]["live"])
        self.assertEqual(by_id["s9"]["client_name"], "cursor")

    def test_nested_probe_sessions_are_read(self) -> None:
        merged = mcp_insights._merge_live_sessions(
            [{"session_id": "s1"}], {"ok": True, "server": {"sessions": [{"session_id": "s1"}]}}
        )
        self.assertTrue(merged[0]["live"])

    def test_no_probe_marks_everything_offline(self) -> None:
        merged = mcp_insights._merge_live_sessions([{"session_id": "s1"}], None)
        self.assertFalse(merged[0]["live"])


class TestInstall(unittest.TestCase):
    def test_reflects_the_real_repository(self) -> None:
        prev = os.environ.get("DASHBOARD_PROJECT_ROOT")
        os.environ["DASHBOARD_PROJECT_ROOT"] = str(REPO_ROOT)
        try:
            out = build_install()
        finally:
            if prev is None:
                os.environ.pop("DASHBOARD_PROJECT_ROOT", None)
            else:
                os.environ["DASHBOARD_PROJECT_ROOT"] = prev

        self.assertTrue(out["ok"])
        self.assertEqual(out["repository"], "https://github.com/leco-devops/local-ecosystem")
        self.assertEqual(out["plugin"]["name"], "leco@leco-devops-open-project")
        self.assertTrue(out["plugin"]["available"])
        self.assertEqual(
            out["plugin"]["marketplace_github"],
            "claude plugin marketplace add leco-devops/local-ecosystem",
        )
        for command in ("/leco:status", "/leco:up", "/leco:diagnose", "/leco:onboard", "/leco:routes"):
            self.assertIn(command, out["plugin"]["commands"])
        self.assertTrue(out["skill"]["available"])
        self.assertTrue(out["mcp"]["available"])
        self.assertTrue(out["docs"]["available"])
        self.assertEqual(out["mcp"]["http_url"], "https://mcp.lh/mcp")
        self.assertEqual(out["mcp"]["stdio_install"], "pipx install ./tools/mcp-server")

        # Every advertised path exists on disk.
        for rel in (out["plugin"]["path"], out["skill"]["path"]):
            self.assertTrue((REPO_ROOT / rel).is_dir(), rel)
        for rel in out["docs"].values():
            if isinstance(rel, str):
                self.assertTrue((REPO_ROOT / rel).is_file(), rel)

    def test_missing_tree_degrades_instead_of_raising(self) -> None:
        with TempProjectRoot():
            out = build_install()
            self.assertTrue(out["ok"])
            self.assertFalse(out["plugin"]["available"])
            self.assertFalse(out["skill"]["available"])
            self.assertFalse(out["mcp"]["available"])
            self.assertFalse(out["docs"]["available"])
            # Commands and install strings stay populated so the onboarding panel renders.
            self.assertEqual(out["plugin"]["name"], "leco@leco-devops-open-project")
            self.assertEqual(out["repository"], "https://github.com/leco-devops/local-ecosystem")


class TestBuildInsights(unittest.TestCase):
    def setUp(self) -> None:
        self._probe = mcp_insights.probe_live_server
        self._apps = mcp_insights.load_hosted_apps
        self._container = mcp_insights.container_running
        mcp_insights.probe_live_server = lambda: None
        mcp_insights.load_hosted_apps = lambda: []
        mcp_insights.container_running = lambda name=mcp_insights.CONTAINER_NAME: (False, False)

    def tearDown(self) -> None:
        mcp_insights.probe_live_server = self._probe
        mcp_insights.load_hosted_apps = self._apps
        mcp_insights.container_running = self._container

    def test_empty_state_is_fully_shaped(self) -> None:
        with TempProjectRoot():
            out = mcp_insights.build_insights()
        self.assertTrue(out["ok"])
        for key in ("server", "counts", "sessions", "recent", "by_tool", "by_target", "apps",
                    "install"):
            self.assertIn(key, out)
        self.assertEqual(out["sessions"], [])
        self.assertEqual(out["recent"], [])
        self.assertEqual(out["apps"], [])
        self.assertEqual(out["counts"]["tool_calls"], 0)
        self.assertFalse(out["server"]["installed"])
        self.assertTrue(out["install"]["plugin"]["name"])

    def test_populated_state_and_app_join(self) -> None:
        mcp_insights.load_hosted_apps = lambda: [
            {"id": "shop", "label": "Shop", "runtime": {"status": "running"},
             "main_url": "https://shop.lh"}
        ]
        with TempProjectRoot() as tmp:
            write_log(
                tmp.log,
                [
                    call(ts=iso(9), event="session_start", tool=None, kind=None, target_id=None),
                    call(ts=iso(8), tool="leco_app_logs", kind="app", target_id="leco-stack-shop"),
                    call(ts=iso(7), tool="leco_status"),
                ],
            )
            out = mcp_insights.build_insights(hours=24, limit=10)
        self.assertEqual(out["counts"]["tool_calls"], 2)
        self.assertEqual(out["counts"]["sessions"], 1)
        self.assertEqual(out["apps"][0]["slug"], "shop")
        self.assertEqual(out["apps"][0]["mcp_calls"], 1)
        self.assertTrue(out["server"]["installed"])
        self.assertEqual(out["server"]["activity_log"]["events"], 3)

    def test_clamps_query_bounds(self) -> None:
        with TempProjectRoot():
            self.assertEqual(mcp_insights.build_insights(hours="9999")["counts"]["window_hours"], 720)
            self.assertEqual(mcp_insights.build_insights(hours=None)["counts"]["window_hours"], 24)


class TestRoutes(unittest.TestCase):
    """The three routes exist, are unauthenticated, and return JSON."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            import app as dashboard_app
        except Exception as exc:  # noqa: BLE001 - flask/docker may be absent locally
            raise unittest.SkipTest(f"dashboard app not importable: {exc}")
        cls.client = dashboard_app.app.test_client()

    def setUp(self) -> None:
        self._probe = mcp_insights.probe_live_server
        self._apps = mcp_insights.load_hosted_apps
        self._container = mcp_insights.container_running
        mcp_insights.probe_live_server = lambda: None
        mcp_insights.load_hosted_apps = lambda: []
        mcp_insights.container_running = lambda name=mcp_insights.CONTAINER_NAME: (False, False)

    def tearDown(self) -> None:
        mcp_insights.probe_live_server = self._probe
        mcp_insights.load_hosted_apps = self._apps
        mcp_insights.container_running = self._container

    def test_endpoints(self) -> None:
        with TempProjectRoot():
            for path in ("/api/mcp/insights?hours=6&limit=5", "/api/mcp/activity?limit=5",
                         "/api/mcp/install"):
                resp = self.client.get(path)
                self.assertEqual(resp.status_code, 200, path)
                self.assertTrue(resp.get_json()["ok"], path)


if __name__ == "__main__":
    unittest.main()
