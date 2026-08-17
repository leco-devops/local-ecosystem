"""Environment configuration for the LEco DevOps MCP server.

The server never talks to Docker or the filesystem directly: every capability is a call
against the LEco DevOps dashboard API, so the dashboard stays the single source of truth
for lifecycle, registry, and routing semantics.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .activity import (
    DEFAULT_MAX_EVENTS,
    ENV_MAX_EVENTS,
    default_project_root,
    resolve_activity_log,
)

# Tried in order until one answers ``GET /api/version``. Host port first (works from the
# host shell where Claude Code runs), then the Traefik hostnames, then the in-network name
# used when the MCP server itself runs as a container on ``lh-network``.
DEFAULT_BASE_URLS: tuple[str, ...] = (
    "http://localhost:8090",
    "http://dashboard.lh",
    "https://dashboard.lh",
    "http://localhost.lh",
    "http://service-dashboard:8090",
)

_TRUE = {"1", "true", "yes", "on"}


def _flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in _TRUE


def _number(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _integer(name: str, default: int) -> int:
    return int(_number(name, default))


def _first_env(*names: str) -> str:
    for n in names:
        v = (os.getenv(n) or "").strip()
        if v:
            return v
    return ""


@dataclass(frozen=True)
class Settings:
    """Immutable server settings resolved from the environment at startup."""

    base_urls: tuple[str, ...] = DEFAULT_BASE_URLS
    control_token: str = ""
    allow_destructive: bool = False
    allow_credentials: bool = False
    read_timeout: float = 120.0
    action_timeout: float = 900.0
    verify_tls: bool = True
    max_response_chars: int = 60_000
    max_log_chars: int = 12_000
    # streamable-http transport
    http_host: str = "127.0.0.1"
    http_port: int = 8099
    http_path: str = "/mcp"
    http_stateless: bool = False
    log_level: str = "INFO"
    # activity telemetry. The default here is *off* so an embedded or test-constructed
    # Settings() never writes to the repo; ``from_env`` fills in the real default path.
    project_root: str = ""
    activity_log_path: str = ""
    activity_max_events: int = 2000
    _extra: dict = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_env(cls) -> "Settings":
        explicit = _first_env("LECO_MCP_DASHBOARD_URL", "LECO_DASHBOARD_URL")
        if explicit:
            # Explicit wins, but keep the defaults as fallbacks so a stale value in a shell
            # profile does not make every tool fail when the dashboard is actually reachable.
            urls = (explicit.rstrip("/"),) + tuple(
                u for u in DEFAULT_BASE_URLS if u != explicit.rstrip("/")
            )
        else:
            urls = DEFAULT_BASE_URLS
        return cls(
            base_urls=urls,
            control_token=_first_env("LECO_MCP_CONTROL_TOKEN", "DASHBOARD_CONTROL_TOKEN"),
            allow_destructive=_flag("LECO_MCP_ALLOW_DESTRUCTIVE", False),
            allow_credentials=_flag("LECO_MCP_ALLOW_CREDENTIALS", False),
            read_timeout=_number("LECO_MCP_READ_TIMEOUT", 120.0),
            action_timeout=_number("LECO_MCP_ACTION_TIMEOUT", 900.0),
            verify_tls=_flag("LECO_MCP_VERIFY_TLS", True),
            max_response_chars=_integer("LECO_MCP_MAX_RESPONSE_CHARS", 60_000),
            max_log_chars=_integer("LECO_MCP_MAX_LOG_CHARS", 12_000),
            http_host=_first_env("LECO_MCP_HTTP_HOST") or "127.0.0.1",
            http_port=_integer("LECO_MCP_HTTP_PORT", 8099),
            http_path=_first_env("LECO_MCP_HTTP_PATH") or "/mcp",
            http_stateless=_flag("LECO_MCP_HTTP_STATELESS", False),
            log_level=(_first_env("LECO_MCP_LOG_LEVEL") or "INFO").upper(),
            project_root=str(default_project_root()),
            # "" is the documented off switch, so this stays a raw string rather than a
            # path: unset means "use the default", set-to-empty means "no telemetry".
            activity_log_path=resolve_activity_log(),
            activity_max_events=_integer(ENV_MAX_EVENTS, DEFAULT_MAX_EVENTS),
        )

    @property
    def has_token(self) -> bool:
        return bool(self.control_token)

    @property
    def activity_enabled(self) -> bool:
        return bool(self.activity_log_path)

    def describe(self) -> dict:
        """Non-secret snapshot for the ``leco_server_info`` tool and startup logging."""
        return {
            "dashboard_url_candidates": list(self.base_urls),
            "control_token_configured": self.has_token,
            "destructive_actions_enabled": self.allow_destructive,
            "credential_tools_enabled": self.allow_credentials,
            "read_timeout_seconds": self.read_timeout,
            "action_timeout_seconds": self.action_timeout,
            "verify_tls": self.verify_tls,
            "max_response_chars": self.max_response_chars,
            "project_root": self.project_root,
            "activity_log": self.activity_log_path or None,
            "activity_enabled": self.activity_enabled,
            "activity_max_events": self.activity_max_events,
        }
