"""The evidence / compose-merge / verify / overlay / certs tools.

Two things these tests exist to hold down:

* **A tool that writes must never replace a file it did not just read.** The overlay writer
  follows ``dashboard/ai_orchestrator.write_generated_files`` because an AI run once replaced
  a deployed, verified configuration with no backup and no diff, in a directory that is
  gitignored (``docs/AI-ONBOARDING-FINDINGS.md`` §0).
* **An overlay that does not merge cleanly is refused, not written.** Validation before the
  write is the whole reason the tool exists rather than a plain file write.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp import Client

from leco_mcp.client import LecoClient
from leco_mcp.config import Settings
from leco_mcp.server import build_server
from leco_mcp.tools.onboarding import _safe_slug

OVERLAY = "docker-compose.leco-hosting.yml"

GOOD_MERGE = {
    "ok": True,
    "summary": {"service_count": 1, "published_ports": 1, "services_on_lh_network": ["edge"]},
    "services": [
        {
            "service_name": "edge",
            "container_name": "bf-edge",
            "networks": ["default", "lh-network"],
            "port_pairs": [{"published": 18787, "target": 8787}],
        }
    ],
}

BAD_MERGE = {
    "ok": False,
    "error": 'service "edge" refers to undefined network lh-network',
    "stderr": 'service "edge" refers to undefined network lh-network: invalid compose project',
    "exit_code": 15,
}

EVIDENCE = {
    "ok": True,
    "root": "/wsp/app",
    "path_field": "wsp:app",
    "summary": {"compose_files": 1, "wrangler_configs": 14, "unknown_count": 2},
    "compose": {
        "count": 1,
        "files": [
            {
                "file": "infra/docker/docker-compose.yml",
                "path_from_hosting_dir": "source/infra/docker/docker-compose.yml",
                "services": [{"service_name": "edge", "container_name": "bf-edge"}],
            }
        ],
    },
    "workers": {"count": 14, "worker_names": ["core-router"]},
    "declared_ports": {"entry_count": 10, "sources": []},
    "port_attribution": {"attributed": [], "unattributed": []},
    "entry_points": {"scripts": {}},
    "unknowns": ["something could not be determined"],
}


class Recorder:
    """Stands in for the dashboard: records calls, replays canned payloads."""

    def __init__(self, **routes):
        self.routes = routes
        self.calls: list[tuple[str, str, dict]] = []

    def install(self, monkeypatch):
        recorder = self

        async def fake_get(self, path, params=None, **_):  # noqa: ANN001
            recorder.calls.append(("GET", path, dict(params or {})))
            return recorder._answer(path)

        async def fake_post(self, path, json_body=None, **_):  # noqa: ANN001
            recorder.calls.append(("POST", path, dict(json_body or {})))
            return recorder._answer(path)

        monkeypatch.setattr(LecoClient, "get", fake_get)
        monkeypatch.setattr(LecoClient, "post", fake_post)
        return self

    def _answer(self, path):
        for key, value in self.routes.items():
            if key in path:
                return json.loads(json.dumps(value))
        return {"ok": True}

    def body(self, needle: str) -> dict:
        for _, path, payload in self.calls:
            if needle in path:
                return payload
        raise AssertionError(f"no call to {needle}; saw {[c[1] for c in self.calls]}")

    def count(self, needle: str) -> int:
        return sum(1 for _, path, _ in self.calls if needle in path)


def _server(tmp_path: Path):
    return build_server(Settings(project_root=str(tmp_path)))


def _slot(tmp_path: Path, slug: str = "demo") -> Path:
    slot = tmp_path / "hosting" / "app-available" / slug
    slot.mkdir(parents=True)
    return slot


async def _call(server, name, args):
    async with Client(server, raise_exceptions=False) as client:
        return await client.call_tool(name, args)


def _data(result):
    assert not result.is_error, _text(result)
    return result.structured_content


def _text(result):
    return " ".join(getattr(c, "text", str(c)) for c in result.content)


# ------------------------------------------------------------------- registration


def test_the_new_tools_are_registered_with_honest_annotations(tmp_path):
    by_name = {t.name: t for t in _server(tmp_path)._tool_manager.list_tools()}  # noqa: SLF001
    for name in ("leco_app_evidence", "leco_compose_validate", "leco_manifest_overlay",
                 "leco_verify", "leco_certs_refresh"):
        assert name in by_name, f"missing tool {name}"

    # Reading facts and running `docker compose config` change nothing.
    assert by_name["leco_app_evidence"].annotations.read_only_hint is True
    assert by_name["leco_compose_validate"].annotations.read_only_hint is True
    assert by_name["leco_verify"].annotations.read_only_hint is True
    # Writing an overlay and reissuing certificates do.
    assert by_name["leco_manifest_overlay"].annotations.read_only_hint is False
    assert by_name["leco_certs_refresh"].annotations.read_only_hint is False
    assert by_name["leco_certs_refresh"].annotations.destructive_hint is True


def test_the_docstrings_teach_the_rules_that_have_already_cost_a_deployment(tmp_path):
    by_name = {t.name: t for t in _server(tmp_path)._tool_manager.list_tools()}  # noqa: SLF001
    onboard = by_name["leco_onboard"].description
    assert "leco_app_evidence" in onboard
    assert "never from invention" in onboard or "Never invent a port" in onboard
    assert "container port" in onboard

    overlay = by_name["leco_manifest_overlay"].description
    assert "!override" in overlay and "!reset" in overlay
    assert "overwrite=true" in overlay

    validate = by_name["leco_compose_validate"].description
    assert "!reset" in validate and "!override" in validate

    from leco_mcp.server import INSTRUCTIONS

    assert "leco_app_evidence" in INSTRUCTIONS
    assert "!override" in INSTRUCTIONS and "!reset" in INSTRUCTIONS
    assert "container" in INSTRUCTIONS


# ---------------------------------------------------------------------- evidence


@pytest.mark.anyio
async def test_evidence_passes_the_path_through_and_returns_every_section(tmp_path, monkeypatch):
    rec = Recorder(evidence=EVIDENCE).install(monkeypatch)
    out = _data(await _call(_server(tmp_path), "leco_app_evidence", {"path": "wsp:app"}))
    assert rec.calls[0] == ("GET", "/api/leco/evidence", {"path": "wsp:app"})
    assert out["workers"]["count"] == 14
    assert out["unknowns"] == ["something could not be determined"]


@pytest.mark.anyio
async def test_sections_narrow_the_payload_but_always_keep_summary_and_unknowns(tmp_path, monkeypatch):
    Recorder(evidence=EVIDENCE).install(monkeypatch)
    out = _data(
        await _call(
            _server(tmp_path),
            "leco_app_evidence",
            {"path": "wsp:app", "sections": ["compose", "unknowns"]},
        )
    )
    assert set(out) <= {"ok", "root", "path_field", "scan_root_path_field", "summary",
                        "compose", "unknowns"}
    assert "workers" not in out
    assert out["summary"]["wrangler_configs"] == 14
    # `unknowns` survives even when the caller forgets to ask for it.
    assert out["unknowns"]


@pytest.mark.anyio
async def test_unknowns_are_never_filtered_away(tmp_path, monkeypatch):
    Recorder(evidence=EVIDENCE).install(monkeypatch)
    out = _data(
        await _call(
            _server(tmp_path), "leco_app_evidence", {"path": "wsp:app", "sections": ["workers"]}
        )
    )
    assert out["unknowns"] == ["something could not be determined"]
    assert out["workers"]["count"] == 14
    assert "compose" not in out


# --------------------------------------------------------------- compose validate


@pytest.mark.anyio
async def test_compose_validate_forwards_overlays_and_clamps_the_timeout(tmp_path, monkeypatch):
    rec = Recorder(**{"compose/validate": GOOD_MERGE}).install(monkeypatch)
    out = _data(
        await _call(
            _server(tmp_path),
            "leco_compose_validate",
            {
                "path": "hosting/app-available/demo",
                "compose_file": "source/infra/docker/docker-compose.yml",
                "overlay_files": ["docker-compose.leco-hosting.yml", "  "],
                "timeout": 9000,
            },
        )
    )
    body = rec.body("compose/validate")
    assert body["overlay_files"] == ["docker-compose.leco-hosting.yml"]
    assert body["timeout"] == 180  # bounded, not whatever the caller asked for
    assert out["summary"]["services_on_lh_network"] == ["edge"]


@pytest.mark.anyio
async def test_compose_validate_requires_both_a_path_and_a_compose_file(tmp_path, monkeypatch):
    Recorder().install(monkeypatch)
    result = await _call(_server(tmp_path), "leco_compose_validate", {"path": "x", "compose_file": " "})
    assert result.is_error
    assert "compose_file" in _text(result)


# ------------------------------------------------------------------------ verify


@pytest.mark.anyio
async def test_verify_refuses_a_call_with_neither_slug_nor_urls(tmp_path, monkeypatch):
    Recorder().install(monkeypatch)
    result = await _call(_server(tmp_path), "leco_verify", {})
    assert result.is_error
    assert "slug" in _text(result)


@pytest.mark.anyio
async def test_verify_sends_slug_and_urls_and_bounds_the_timeout(tmp_path, monkeypatch):
    rec = Recorder(verify={"ok": True, "counts": {"backend_unreachable": 1}}).install(monkeypatch)
    out = _data(
        await _call(
            _server(tmp_path),
            "leco_verify",
            {"slug": "demo", "urls": ["https://demo.lh/", " "], "timeout": 999},
        )
    )
    body = rec.body("/api/leco/verify")
    assert body == {"timeout": 30.0, "slug": "demo", "urls": ["https://demo.lh/"]}
    assert out["counts"]["backend_unreachable"] == 1


# ----------------------------------------------------------------------- overlay


@pytest.mark.anyio
@pytest.mark.parametrize("bad", ["../../etc", "Demo/../x", "", "a b", "/abs"])
async def test_overlay_rejects_a_slug_that_is_not_a_slug(tmp_path, monkeypatch, bad):
    Recorder().install(monkeypatch)
    result = await _call(_server(tmp_path), "leco_manifest_overlay", {"slug": bad})
    assert result.is_error


def test_safe_slug_accepts_the_real_shapes_and_rejects_traversal():
    assert _safe_slug("Utility-Server-Edge") == "utility-server-edge"
    for bad in ("../x", "a/b", "-leading-dash-is-fine-later" * 10):
        with pytest.raises(ValueError):
            _safe_slug(bad)


@pytest.mark.anyio
async def test_overlay_without_a_hosting_slot_says_so_instead_of_creating_one(tmp_path, monkeypatch):
    Recorder().install(monkeypatch)
    result = await _call(_server(tmp_path), "leco_manifest_overlay", {"slug": "demo"})
    assert result.is_error
    message = _text(result)
    assert "No hosting slot" in message
    assert "stdio" in message
    assert not (tmp_path / "hosting").exists()


@pytest.mark.anyio
async def test_overlay_read_returns_what_is_on_disk(tmp_path, monkeypatch):
    slot = _slot(tmp_path)
    (slot / OVERLAY).write_text("services: {}\n", encoding="utf-8")
    Recorder().install(monkeypatch)
    out = _data(await _call(_server(tmp_path), "leco_manifest_overlay", {"slug": "demo"}))
    assert out["exists"] is True
    assert out["overlay_yaml"] == "services: {}\n"


@pytest.mark.anyio
async def test_a_write_is_refused_when_the_merge_fails_and_nothing_lands_on_disk(tmp_path, monkeypatch):
    slot = _slot(tmp_path)
    Recorder(evidence=EVIDENCE, **{"compose/validate": BAD_MERGE}).install(monkeypatch)
    out = _data(
        await _call(
            _server(tmp_path),
            "leco_manifest_overlay",
            {"slug": "demo", "action": "write", "overlay_yaml": "services:\n  edge:\n    networks: [lh-network]\n"},
        )
    )
    assert out["ok"] is False
    assert out["written"] is False
    assert "undefined network" in out["compose_error"]
    assert not (slot / OVERLAY).exists()


@pytest.mark.anyio
async def test_the_compose_file_is_taken_from_evidence_rather_than_assumed(tmp_path, monkeypatch):
    _slot(tmp_path)
    rec = Recorder(evidence=EVIDENCE, **{"compose/validate": GOOD_MERGE}).install(monkeypatch)
    out = _data(
        await _call(
            _server(tmp_path),
            "leco_manifest_overlay",
            {"slug": "demo", "action": "validate", "overlay_yaml": "services: {}\n"},
        )
    )
    assert rec.body("compose/validate")["compose_file"] == "source/infra/docker/docker-compose.yml"
    assert out["compose_file"] == "source/infra/docker/docker-compose.yml"


@pytest.mark.anyio
async def test_a_first_write_creates_the_overlay_after_the_merge_passes(tmp_path, monkeypatch):
    slot = _slot(tmp_path)
    rec = Recorder(evidence=EVIDENCE, **{"compose/validate": GOOD_MERGE}).install(monkeypatch)
    text = "services:\n  edge:\n    ports: !override\n      - '18787:8787'\n"
    out = _data(
        await _call(
            _server(tmp_path),
            "leco_manifest_overlay",
            {"slug": "demo", "action": "write", "overlay_yaml": text},
        )
    )
    assert out["action_taken"] == "created"
    assert (slot / OVERLAY).read_text(encoding="utf-8") == text
    # The merge really was checked before the write, not after it.
    assert rec.count("compose/validate") == 1


@pytest.mark.anyio
async def test_rewriting_identical_bytes_is_reported_as_unchanged(tmp_path, monkeypatch):
    slot = _slot(tmp_path)
    text = "services: {}\n"
    (slot / OVERLAY).write_text(text, encoding="utf-8")
    Recorder(evidence=EVIDENCE, **{"compose/validate": GOOD_MERGE}).install(monkeypatch)
    out = _data(
        await _call(
            _server(tmp_path),
            "leco_manifest_overlay",
            {"slug": "demo", "action": "write", "overlay_yaml": text},
        )
    )
    assert out["action_taken"] == "unchanged"
    assert out["written"] is False
    assert not list(slot.glob("*.bak-*"))  # nothing to back up


@pytest.mark.anyio
async def test_a_differing_overlay_is_reported_not_replaced(tmp_path, monkeypatch):
    slot = _slot(tmp_path)
    original = "# hand-authored, deployed and verified\nservices: {}\n"
    (slot / OVERLAY).write_text(original, encoding="utf-8")
    Recorder(evidence=EVIDENCE, **{"compose/validate": GOOD_MERGE}).install(monkeypatch)
    out = _data(
        await _call(
            _server(tmp_path),
            "leco_manifest_overlay",
            {"slug": "demo", "action": "write", "overlay_yaml": "services:\n  new: {}\n"},
        )
    )
    assert out["ok"] is False
    assert out["action_taken"] == "skipped_exists"
    assert "overwrite=true" in out["reason"]
    # The file someone deployed is still exactly the file someone deployed.
    assert (slot / OVERLAY).read_text(encoding="utf-8") == original


@pytest.mark.anyio
async def test_overwrite_replaces_the_file_but_always_keeps_a_timestamped_backup(tmp_path, monkeypatch):
    slot = _slot(tmp_path)
    original = "# the deployed one\nservices: {}\n"
    replacement = "services:\n  edge:\n    ports: !override\n      - '18787:8787'\n"
    (slot / OVERLAY).write_text(original, encoding="utf-8")
    Recorder(evidence=EVIDENCE, **{"compose/validate": GOOD_MERGE}).install(monkeypatch)
    out = _data(
        await _call(
            _server(tmp_path),
            "leco_manifest_overlay",
            {"slug": "demo", "action": "write", "overlay_yaml": replacement, "overwrite": True},
        )
    )
    assert out["action_taken"] == "overwritten"
    assert (slot / OVERLAY).read_text(encoding="utf-8") == replacement
    backup = Path(out["backup"])
    assert backup.name.startswith(f"{OVERLAY}.bak-")
    assert backup.read_text(encoding="utf-8") == original


@pytest.mark.anyio
async def test_an_unknown_action_is_rejected(tmp_path, monkeypatch):
    _slot(tmp_path)
    Recorder().install(monkeypatch)
    result = await _call(
        _server(tmp_path), "leco_manifest_overlay", {"slug": "demo", "action": "delete"}
    )
    assert result.is_error


# ------------------------------------------------------------------------- certs


@pytest.mark.anyio
async def test_certs_refresh_without_a_repository_names_the_reason(tmp_path, monkeypatch):
    Recorder().install(monkeypatch)
    result = await _call(_server(tmp_path), "leco_certs_refresh", {})
    assert result.is_error
    message = _text(result)
    assert "generate-certs.sh" in message
    assert "stdio" in message


@pytest.mark.anyio
async def test_certs_refresh_rejects_a_hostname_that_is_not_one(tmp_path, monkeypatch):
    script = tmp_path / "certs" / "generate-certs.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")
    Recorder().install(monkeypatch)
    result = await _call(
        _server(tmp_path), "leco_certs_refresh", {"hostnames": ["a.lh; rm -rf /"]}
    )
    assert result.is_error
    assert "valid hostname" in _text(result)


@pytest.mark.anyio
async def test_certs_dry_run_lists_without_touching_traefik(tmp_path, monkeypatch):
    script = tmp_path / "certs" / "generate-certs.sh"
    script.parent.mkdir(parents=True)
    script.write_text(
        '#!/usr/bin/env bash\necho "would include: $*"\n', encoding="utf-8"
    )
    rec = Recorder().install(monkeypatch)
    out = _data(
        await _call(
            _server(tmp_path),
            "leco_certs_refresh",
            {"dry_run": True, "hostnames": ["panel.demo.lh"]},
        )
    )
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert out["traefik_restarted"] is False
    assert "--list" in out["output"] and "panel.demo.lh" in out["output"]
    assert rec.count("/api/control") == 0


@pytest.mark.anyio
async def test_a_failed_certificate_run_does_not_restart_traefik(tmp_path, monkeypatch):
    script = tmp_path / "certs" / "generate-certs.sh"
    script.parent.mkdir(parents=True)
    script.write_text('#!/usr/bin/env bash\necho "mkcert missing" >&2\nexit 3\n', encoding="utf-8")
    rec = Recorder().install(monkeypatch)
    out = _data(await _call(_server(tmp_path), "leco_certs_refresh", {}))
    assert out["ok"] is False
    assert out["exit_code"] == 3
    assert out["traefik_restarted"] is False
    assert "mkcert missing" in out["output"]
    assert rec.count("/api/control") == 0


@pytest.fixture
def anyio_backend():
    return "asyncio"
