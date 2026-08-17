"""Environment configuration for the LEco DevOps MCP server.

The server never talks to Docker or the filesystem directly: every capability is a call
against the LEco DevOps dashboard API, so the dashboard stays the single source of truth
for lifecycle, registry, and routing semantics.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .activity import (
    DEFAULT_MAX_EVENTS,
    ENV_MAX_EVENTS,
    default_project_root,
    resolve_activity_log,
)

ENV_BASE_DOMAIN = "LECO_MCP_BASE_DOMAIN"
ENV_HOST_PORT = "DASHBOARD_HOST_PORT"
DEFAULT_HOST_PORT = 8090
# The dashboard's own port inside ``lh-network`` — a container port, so unlike the host
# port it is not affected by ``DASHBOARD_HOST_PORT``.
IN_NETWORK_URL = "http://service-dashboard:8090"

_TRUE = {"1", "true", "yes", "on"}
_DNS_NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")


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


# ------------------------------------------------------------------ dashboard address


def _scan_platform_config(root: Path) -> dict[str, str]:
    """Top-level scalar keys of ``config/leco-platform.yaml``, without a YAML dependency.

    This package depends on nothing but ``mcp`` and ``httpx`` — adding PyYAML to read two
    strings would make the server harder to install than it needs to be. The file is
    machine-written with unindented top-level keys, and a file that is missing, unreadable,
    or shaped unexpectedly yields ``{}`` so the caller falls back to the local defaults.
    """
    try:
        text = (root / "config" / "leco-platform.yaml").read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line[0].isspace() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep or not key.strip():
            continue
        value = value.split("#", 1)[0].strip().strip("'\"")
        if value:
            out[key.strip()] = value
    return out


def routing_domain(project_root: Path | str | None = None) -> str:
    """Base DNS domain the dashboard is routed on.

    ``lh`` on a workstation; the configured ``base_domain`` when the platform is in cloud
    mode. Mirrors ``dashboard/leco_detect.routing_domain`` and ``platform_config.
    public_hostname`` exactly — cloud mode is the only thing that moves hostnames off
    ``.lh`` — so a local install keeps resolving the same ``*.lh`` names it always has.

    Never raises: an unreadable config or a ``base_domain`` that is not a legal DNS name
    falls back to ``lh`` rather than producing an address nothing can answer on.
    """
    explicit = _first_env(ENV_BASE_DOMAIN).strip().strip(".").lower()
    if explicit:
        return explicit if _DNS_NAME.match(explicit) else "lh"
    root = Path(project_root) if project_root is not None else default_project_root()
    cfg = _scan_platform_config(root)
    if (cfg.get("deployment_mode") or "local").strip().lower() != "cloud":
        return "lh"
    dom = (cfg.get("base_domain") or "lh").strip().strip(".").lower()
    return dom if dom != "lh" and _DNS_NAME.match(dom) else "lh"


def _host_port() -> int:
    raw = (os.getenv(ENV_HOST_PORT) or "").strip()
    try:
        return int(raw) if raw else DEFAULT_HOST_PORT
    except ValueError:
        return DEFAULT_HOST_PORT


def dashboard_candidates(project_root: Path | str | None = None) -> tuple[str, ...]:
    """Addresses to try, in order, until one answers ``GET /api/version``.

    The routed hostname comes first so every URL the tools report back is the address a
    person can actually open — ``http://localhost:8090`` is an implementation detail of the
    host port publish, not where the dashboard lives. The host port is kept immediately
    after it, because Traefik being down is exactly when these tools are most needed, and
    the in-network container name last for when the MCP server itself runs on ``lh-network``.
    """
    dom = routing_domain(project_root)
    if dom == "lh":
        # ``localhost.lh`` is the dashboard's own router; ``dashboard.lh`` is its alias.
        # Plain HTTP first: the mkcert CA that signs ``*.lh`` is trusted by the system
        # store, not by the certifi bundle httpx verifies against.
        routed = (
            "http://localhost.lh",
            "http://dashboard.lh",
            "https://localhost.lh",
            "https://dashboard.lh",
        )
    else:
        # Cloud mode: the certificate is publicly trusted and plain HTTP usually redirects.
        routed = (f"https://dashboard.{dom}", f"http://dashboard.{dom}")
    return routed + (f"http://localhost:{_host_port()}", IN_NETWORK_URL)


# Static fallback for a ``Settings()`` built without consulting the environment (tests,
# embedding). ``Settings.from_env`` resolves the real list through ``dashboard_candidates``.
DEFAULT_BASE_URLS: tuple[str, ...] = (
    "http://localhost.lh",
    "http://dashboard.lh",
    "https://localhost.lh",
    "https://dashboard.lh",
    f"http://localhost:{DEFAULT_HOST_PORT}",
    IN_NETWORK_URL,
)


@dataclass(frozen=True)
class Settings:
    """Immutable server settings resolved from the environment at startup."""

    base_urls: tuple[str, ...] = DEFAULT_BASE_URLS
    routing_domain: str = "lh"
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
        root = default_project_root()
        domain = routing_domain(root)
        discovered = dashboard_candidates(root)
        explicit = _first_env("LECO_MCP_DASHBOARD_URL", "LECO_DASHBOARD_URL")
        if explicit:
            # Explicit wins, but keep the discovered addresses as fallbacks so a stale value
            # in a shell profile does not make every tool fail when the dashboard is
            # actually reachable.
            urls = (explicit.rstrip("/"),) + tuple(
                u for u in discovered if u != explicit.rstrip("/")
            )
        else:
            urls = discovered
        return cls(
            base_urls=urls,
            routing_domain=domain,
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
            project_root=str(root),
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
            "routing_domain": self.routing_domain,
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
