"""Response shaping keeps tool results inside an agent's context budget."""

import json

from leco_mcp.shaping import (
    clip,
    compact_hosted_app,
    compact_target,
    guard_size,
    pick,
    stream_result,
    summarize_services,
    summarize_system,
    tail,
)


def test_pick_skips_missing_keys():
    assert pick({"a": 1, "b": 2}, "a", "c") == {"a": 1}
    assert pick(None, "a") == {}
    assert pick("not a dict", "a") == {}


def test_clip_keeps_head_and_reports_loss():
    out = clip("x" * 100, 10)
    assert out.startswith("x" * 10)
    assert "90 more characters truncated" in out
    assert clip("short", 10) == "short"
    assert clip(None, 10) == ""


def test_tail_keeps_end_because_command_output_ends_with_the_error():
    out = tail("A" * 50 + "FAILED", 10, )
    assert out.endswith("AAAAFAILED")
    assert "truncated" in out


def test_guard_size_passes_small_payloads_through_unchanged():
    payload = {"a": 1}
    assert guard_size(payload, 1000) is payload


def test_guard_size_truncates_and_explains():
    out = guard_size({"blob": "x" * 5000}, 500)
    assert out["truncated"] is True
    assert "LECO_MCP_MAX_RESPONSE_CHARS" in out["reason"]
    assert len(out["preview"]) == 500


def test_guard_size_stringifies_rather_than_raising_on_odd_values():
    # default=str keeps sizing from blowing up on datetimes/objects the API may return.
    payload = {"obj": object()}
    assert guard_size(payload, 10_000) is payload
    truncated = guard_size(payload, 10)
    assert truncated["truncated"] is True


def test_summarize_system_pulls_the_health_headline():
    raw = {
        "generated_at": "2026-08-16T00:00:00Z",
        "system_status": {
            "level": "degraded",
            "services_total": 28,
            "services_running": 12,
            "services_missing": 16,
            "services_missing_names": ["ollama"],
            "total_urls": 40,
            "healthy_urls": 22,
            "unhealthy_urls": 18,
            "aggregate_cpu_percent": 1.2,
            "alerts": [{"msg": "x"}] * 50,
        },
        "docker_overview": {"docker_available": True, "counts": {"running": 12}},
        "services": [],
    }
    out = summarize_system(raw)
    assert out["level"] == "degraded"
    assert out["services"]["running"] == 12
    assert out["urls"]["unhealthy"] == 18
    assert out["docker"]["available"] is True
    # Alerts are capped so one noisy stack cannot flood the response.
    assert len(out["alerts"]) == 20


def test_summarize_services_flattens_url_checks():
    raw = {
        "services": [
            {
                "service": "Traefik",
                "container": "traefik",
                "container_info": {"status": "running", "running": True},
                "url_checks": [{"url": "http://traefik.lh", "ok": True, "status_code": 302}],
                "notes": "edge",
                "hub_slug": "traefik",
                "connection_strings": ["ignored"],
            }
        ]
    }
    rows = summarize_services(raw)
    assert rows[0]["running"] is True
    assert rows[0]["urls"][0]["status"] == 302
    assert "connection_strings" not in rows[0]


def test_compact_target_lifts_runtime_state():
    row = compact_target(
        {
            "id": "ai-traefik",
            "label": "Traefik",
            "group": "ecosystem-stack",
            "container": "traefik",
            "actions": ["start", "stop"],
            "default_policy": "start",
            "runtime": {"status": "running", "running": True, "label": "Running"},
        }
    )
    assert row["status"] == "running"
    assert row["state"] == "Running"
    assert row["actions"] == ["start", "stop"]


def test_compact_hosted_app_uses_slug_not_id():
    row = compact_hosted_app(
        {
            "id": "botfeed",
            "label": "botfeed",
            "target_id": "leco-stack-botfeed",
            "main_url": "https://botfeed.lh",
            "runtime": {"status": "stopped", "running": False, "label": "down"},
            "routes": [{"hostname": "botfeed.lh", "backend": {"host": "x", "port": 80}}],
            "localhost_archetype": "node",
        }
    )
    assert row["slug"] == "botfeed"
    assert row["target_id"] == "leco-stack-botfeed"
    assert row["running"] is False


def test_stream_result_marks_missing_done_event_as_failure():
    out = stream_result(["line\n"], None, max_log_chars=100, events=1)
    assert out["ok"] is False
    assert "without a final result" in out["error"]


def test_stream_result_drops_duplicated_log_from_result():
    out = stream_result(
        ["a\n", "b\n"],
        {"ok": True, "exit_code": 0, "log": "a\nb\n"},
        max_log_chars=100,
        events=2,
    )
    assert out["ok"] is True
    assert out["log"] == "a\nb\n"
    assert "log" not in out["result"]
    assert out["result"]["exit_code"] == 0


def test_stream_result_surfaces_error_from_failed_action():
    out = stream_result([], {"ok": False, "error": "boom"}, max_log_chars=100)
    assert out["ok"] is False
    assert out["error"] == "boom"


def test_stream_result_is_json_serializable():
    out = stream_result(["x\n"], {"ok": True}, max_log_chars=10)
    json.dumps(out)
