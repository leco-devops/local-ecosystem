"""
Per-agent setup snippets shown in MCP → 2 · Install on an agent.

The property that matters is that a snippet is *copy-paste correct on the reader's machine*.
The dashboard runs in a container where the repo is `/project`, so a naive implementation emits
`/project/...` paths that exist nowhere the user can see — and a GUI agent, which does not inherit
the shell PATH, then fails silently with no error to search for. These tests pin the host-path
mapping and the JSON shape.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mcp_insights  # noqa: E402

QUALIFIED = "leco@leco-devops-open-project"


@pytest.fixture
def clients(monkeypatch):
    monkeypatch.setenv("LECO_PROJECT_ROOT_HOST", "/Users/someone/GitHub/local-ecosystem")
    monkeypatch.setattr(
        "project_paths.host_project_root", lambda: "/Users/someone/GitHub/local-ecosystem"
    )
    return {c["id"]: c for c in mcp_insights.build_agent_clients(QUALIFIED)}


def test_covers_the_agents_people_actually_ask_about(clients):
    for expected in ("claude-code", "claude-desktop", "codex", "antigravity", "generic"):
        assert expected in clients


def test_stdio_snippets_use_an_absolute_host_path(clients):
    """A bare `leco-mcp` resolves in a terminal and fails silently inside a GUI app."""
    cfg = json.loads(clients["claude-desktop"]["config"])
    command = cfg["mcpServers"]["leco-devops"]["command"]

    assert command.startswith("/Users/someone/GitHub/local-ecosystem/"), command
    assert "/project/" not in command, "container path leaked into a user-facing snippet"
    assert command.endswith("/leco-mcp")
    assert cfg["mcpServers"]["leco-devops"]["args"] == ["stdio"]


def test_every_json_config_actually_parses(clients):
    for cid, c in clients.items():
        if c.get("config") and c.get("config_lang") != "toml":
            json.loads(c["config"])  # raises on malformed output
        if c.get("config_alt"):
            json.loads(c["config_alt"]), cid


def test_codex_uses_the_underscore_toml_table(clients):
    """`mcp_servers` in TOML vs `mcpServers` in JSON — getting this wrong silently no-ops."""
    cfg = clients["codex"]["config"]
    assert cfg.startswith("[mcp_servers.leco-devops]")
    assert "mcpServers" not in cfg
    assert clients["codex"]["config_lang"] == "toml"


def test_untested_clients_are_marked_rather_than_implied(clients):
    """Honesty about coverage: a shape copied from a spec is not a shape that was run."""
    assert clients["claude-code"]["verified"] is True
    assert clients["codex"]["verified"] is True
    assert clients["antigravity"]["verified"] is False
    assert clients["generic"]["verified"] is False


def test_http_snippets_point_at_the_endpoint_that_needs_no_install(clients):
    http_url = mcp_insights.ENDPOINTS["host"]
    assert "localhost" in http_url

    codex_http = [s for s in clients["codex"]["steps"] if "--url" in s["command"]]
    assert codex_http and http_url in codex_http[0]["command"]

    generic_alt = json.loads(clients["generic"]["config_alt"])
    assert generic_alt["mcpServers"]["leco-devops"]["url"] == http_url


def test_claude_code_offers_the_plugin_first(clients):
    """The plugin is one install for server + skill + commands; it should lead."""
    steps = clients["claude-code"]["steps"]
    assert "marketplace add" in steps[0]["command"]
    assert QUALIFIED in steps[1]["command"]
