"""Server assembly, config resolution, and end-to-end tool dispatch against a fake API."""

import json

import pytest

from leco_mcp.client import LecoApiError, LecoClient
from leco_mcp.config import (
    DEFAULT_BASE_URLS,
    Settings,
    dashboard_candidates,
    routing_domain,
)
from leco_mcp.server import build_server


# ------------------------------------------------------------------ configuration


def _clear_env(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("LECO_MCP_") or key in ("DASHBOARD_CONTROL_TOKEN", "DASHBOARD_HOST_PORT"):
            monkeypatch.delenv(key, raising=False)


def test_settings_defaults_when_env_is_empty(monkeypatch):
    _clear_env(monkeypatch)
    s = Settings.from_env()
    assert s.base_urls == DEFAULT_BASE_URLS
    assert s.routing_domain == "lh"
    assert s.allow_destructive is False
    assert s.allow_credentials is False
    assert s.has_token is False


def test_routed_hostname_is_tried_before_the_published_host_port(monkeypatch):
    """The address people can open comes first; the host port stays as a Traefik-down fallback."""
    _clear_env(monkeypatch)
    urls = Settings.from_env().base_urls
    assert urls[0] == "http://localhost.lh"
    assert urls.index("http://localhost.lh") < urls.index("http://localhost:8090")
    assert urls[-1] == "http://service-dashboard:8090"


def test_host_port_fallback_follows_dashboard_host_port(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("DASHBOARD_HOST_PORT", "9190")
    assert "http://localhost:9190" in Settings.from_env().base_urls


def test_explicit_dashboard_url_wins_but_defaults_remain_as_fallback(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("LECO_MCP_DASHBOARD_URL", "http://box.local:9000/")
    s = Settings.from_env()
    assert s.base_urls[0] == "http://box.local:9000"
    # Fallbacks are kept so a stale profile value cannot brick every tool.
    assert "http://localhost:8090" in s.base_urls


# --------------------------------------------------------------- routed hostname


def _platform_yaml(tmp_path, body: str):
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "leco-platform.yaml").write_text(body, encoding="utf-8")
    return tmp_path


def test_routing_domain_is_lh_on_a_local_install(monkeypatch, tmp_path):
    monkeypatch.delenv("LECO_MCP_BASE_DOMAIN", raising=False)
    root = _platform_yaml(tmp_path, "deployment_mode: local\nbase_domain: mydomain.com\n")
    # Local mode never leaves .lh, exactly like leco_detect.routing_domain.
    assert routing_domain(root) == "lh"


def test_cloud_mode_uses_the_configured_base_domain(monkeypatch, tmp_path):
    monkeypatch.delenv("LECO_MCP_BASE_DOMAIN", raising=False)
    monkeypatch.delenv("DASHBOARD_HOST_PORT", raising=False)
    root = _platform_yaml(tmp_path, "deployment_mode: cloud\nbase_domain: mydomain.com\ntls:\n  mode: acme\n")
    assert routing_domain(root) == "mydomain.com"
    urls = dashboard_candidates(root)
    assert urls[0] == "https://dashboard.mydomain.com"
    assert "http://localhost:8090" in urls


def test_unreadable_or_nonsense_platform_config_falls_back_to_lh(monkeypatch, tmp_path):
    monkeypatch.delenv("LECO_MCP_BASE_DOMAIN", raising=False)
    assert routing_domain(tmp_path) == "lh"  # no config file at all
    root = _platform_yaml(tmp_path, "deployment_mode: cloud\nbase_domain: not a domain!\n")
    assert routing_domain(root) == "lh"


def test_base_domain_env_overrides_the_platform_config(monkeypatch, tmp_path):
    monkeypatch.setenv("LECO_MCP_BASE_DOMAIN", "Box.Local.")
    root = _platform_yaml(tmp_path, "deployment_mode: local\n")
    assert routing_domain(root) == "box.local"


def test_control_token_accepts_the_dashboard_env_name(monkeypatch):
    monkeypatch.delenv("LECO_MCP_CONTROL_TOKEN", raising=False)
    monkeypatch.setenv("DASHBOARD_CONTROL_TOKEN", "secret")
    assert Settings.from_env().control_token == "secret"


def test_flags_parse_truthy_spellings(monkeypatch):
    for value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("LECO_MCP_ALLOW_DESTRUCTIVE", value)
        assert Settings.from_env().allow_destructive is True
    for value in ("0", "false", "no", ""):
        monkeypatch.setenv("LECO_MCP_ALLOW_DESTRUCTIVE", value)
        assert Settings.from_env().allow_destructive is False


def test_describe_never_leaks_the_token():
    s = Settings(control_token="super-secret")
    blob = json.dumps(s.describe())
    assert "super-secret" not in blob
    assert '"control_token_configured": true' in blob


# -------------------------------------------------------------------- assembly


def test_server_registers_every_tool_family():
    server = build_server(Settings())
    names = {t.name for t in server._tool_manager.list_tools()}  # noqa: SLF001
    for expected in (
        "leco_server_info",
        "leco_status",
        "leco_control",
        "leco_control_targets",
        "leco_apps",
        "leco_app_control",
        "leco_onboard",
        "leco_register",
        "leco_detect",
        "leco_dev_stack_action",
        "leco_platform_traefik_apply",
        "leco_route_merge_fragment",
        "leco_llm_models",
        "leco_docs",
        "leco_ui_credentials",
    ):
        assert expected in names, f"missing tool {expected}"
    assert all(n.startswith("leco_") for n in names)


def test_tools_declare_read_only_hints_correctly():
    server = build_server(Settings())
    by_name = {t.name: t for t in server._tool_manager.list_tools()}  # noqa: SLF001
    assert by_name["leco_status"].annotations.read_only_hint is True
    assert by_name["leco_control"].annotations.read_only_hint is False
    assert by_name["leco_control"].annotations.destructive_hint is True


def test_every_tool_has_a_description():
    server = build_server(Settings())
    for tool in server._tool_manager.list_tools():  # noqa: SLF001
        assert tool.description and len(tool.description) > 40, tool.name


def test_prompts_are_registered():
    server = build_server(Settings())
    names = {p.name for p in server._prompt_manager.list_prompts()}  # noqa: SLF001
    assert {"onboard_app", "diagnose_stack", "bring_up_stack"} <= names


# ---------------------------------------------------------------------- client


@pytest.mark.anyio
async def test_unreachable_dashboard_reports_every_candidate():
    settings = Settings(base_urls=("http://127.0.0.1:1",), read_timeout=1.0)
    client = LecoClient(settings)
    try:
        with pytest.raises(LecoApiError) as exc:
            await client.base_url()
        message = str(exc.value)
        assert "not reachable" in message
        assert "http://127.0.0.1:1" in message
        assert "ecosystem-stack.sh" in message
    finally:
        await client.aclose()


@pytest.fixture
def anyio_backend():
    return "asyncio"
