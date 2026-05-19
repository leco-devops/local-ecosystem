"""Dashboard access to LEco platform config (cloud VM)."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(os.getenv("DASHBOARD_PROJECT_ROOT", Path(__file__).resolve().parents[1]))
_LIB = _PROJECT_ROOT / "ecosystem-stack" / "lib"
_eco_path = _LIB / "platform_config.py"
_spec = importlib.util.spec_from_file_location("leco_ecosystem_platform_config", _eco_path)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load ecosystem platform_config from {_eco_path}")
_pc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pc)

PLATFORM_FILE = _pc.PLATFORM_FILE
PLATFORM_EXAMPLE = _pc.PLATFORM_EXAMPLE
START_ORDER = _pc.START_ORDER
BUNDLE_TO_SERVICE = _pc.BUNDLE_TO_SERVICE


def load_platform_config() -> dict[str, Any]:
    cfg = _pc.load_platform_config()
    return cfg if cfg else _pc.default_platform_config()


def save_platform_config(cfg: dict[str, Any]) -> None:
    _pc.save_platform_config(cfg)


def enabled_services() -> list[str]:
    items = _pc.enabled_services_list()
    return items if items else list(_pc.START_ORDER)


def deployment_mode() -> str:
    return str(load_platform_config().get("deployment_mode") or "local")


def base_domain() -> str:
    cfg = load_platform_config()
    dom = str(cfg.get("base_domain") or "lh").strip()
    return dom if dom else "lh"


def public_hostname(service: str, slug: str = "") -> str:
    dom = base_domain()
    if deployment_mode() == "local" or dom == "lh":
        if slug:
            return f"{slug}.lh"
        return f"{service}.lh"
    if slug:
        return f"{slug}.{dom}"
    return f"{service}.{dom}"


def load_component_catalog() -> dict[str, Any]:
    return _pc.load_component_catalog()


def load_profiles() -> dict[str, Any]:
    return _pc.load_profiles()


def install_profile() -> str:
    return str(load_platform_config().get("install_profile") or "").strip()


def ai_platform_hints() -> dict[str, Any]:
    """Cloud-first AI onboarding hints from leco-platform.yaml."""
    cfg = load_platform_config()
    ai = cfg.get("ai") if isinstance(cfg.get("ai"), dict) else {}
    prof = install_profile()
    prefer_cloud = bool(ai.get("prefer_cloud")) or prof == "ai-cloud"
    default_provider = str(ai.get("default_provider") or "none")
    return {
        "install_profile": prof,
        "prefer_cloud": prefer_cloud,
        "default_provider": default_provider,
        "cloud_first": prefer_cloud,
        "suggested_providers": (
            ["openai", "anthropic", "google", "openai-compatible", "hybrid", "ollama", "airllm", "none"]
            if prefer_cloud
            else ["ollama", "airllm", "hybrid", "openai", "anthropic", "google", "openai-compatible", "none"]
        ),
    }


def lh_to_public_host(hostname: str) -> str:
    """Rewrite ``service.lh`` to ``service.<base_domain>`` in cloud mode."""
    h = (hostname or "").strip()
    if not h:
        return h
    if deployment_mode() != "cloud":
        return h
    dom = base_domain()
    if dom == "lh":
        return h
    if h.endswith(".lh"):
        return f"{h[:-3]}.{dom}"
    return h


def public_url_from_lh(url: str) -> str:
    """Rewrite https://slug.lh/... to cloud base_domain when configured."""
    u = (url or "").strip()
    if not u or deployment_mode() != "cloud":
        return u
    dom = base_domain()
    if dom == "lh":
        return u
    try:
        from urllib.parse import urlsplit, urlunsplit

        p = urlsplit(u)
        if p.hostname and p.hostname.endswith(".lh"):
            new_host = f"{p.hostname[:-3]}.{dom}"
            return urlunsplit((p.scheme, new_host, p.path, p.query, p.fragment))
    except Exception:
        pass
    return u
