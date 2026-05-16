"""
Configuration management for AI-assisted onboarding.

Reads/writes config/ai-providers.yaml (gitignored, server-side only).
API keys are never sent to the browser — only masked versions.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(os.getenv("DASHBOARD_PROJECT_ROOT", "/project"))
CONFIG_DIR = PROJECT_ROOT / "config"
CONFIG_FILE = CONFIG_DIR / "ai-providers.yaml"

_DEFAULT_CONFIG: dict[str, Any] = {
    "default_provider": "none",
    "default_model": "",
    "timeout": 180,
    "providers": {
        "ollama": {
            "base_url": "http://ollama:11434",
            "default_model": "qwen2.5-coder",
            "timeout": 300,
        },
        "airllm": {
            "base_url": "http://airllm:11435",
            "default_model": "Qwen/Qwen2.5-7B-Instruct",
            "timeout": 600,
        },
        "openai": {
            "api_key": "",
            "default_model": "gpt-4o-mini",
            "timeout": 120,
        },
        "anthropic": {
            "api_key": "",
            "default_model": "claude-sonnet-4-20250514",
            "timeout": 120,
        },
        "google": {
            "api_key": "",
            "default_model": "gemini-2.0-flash",
            "timeout": 120,
        },
        "openai-compatible": {
            "base_url": "",
            "api_key": "",
            "default_model": "",
            "timeout": 180,
        },
        "hybrid": {
            "local_provider": "ollama",
            "local_model": "qwen2.5-coder",
            "cloud_provider": "openai",
            "cloud_model": "gpt-4o-mini",
            "cloud_api_key": "",
            "local_timeout": 300,
            "cloud_timeout": 120,
        },
    },
}

# Provider display metadata (label, supports_key, supports_base_url)
PROVIDER_META: dict[str, dict[str, Any]] = {
    "none": {"label": "No AI (deterministic only)", "needs_key": False, "needs_url": False, "privacy": "full"},
    "ollama": {"label": "Ollama (local)", "needs_key": False, "needs_url": True, "privacy": "full"},
    "airllm": {"label": "AirLLM (local large models)", "needs_key": False, "needs_url": True, "privacy": "full"},
    "openai": {"label": "OpenAI", "needs_key": True, "needs_url": False, "privacy": "cloud"},
    "anthropic": {"label": "Anthropic", "needs_key": True, "needs_url": False, "privacy": "cloud"},
    "google": {"label": "Google (Gemini)", "needs_key": True, "needs_url": False, "privacy": "cloud"},
    "openai-compatible": {"label": "OpenAI-Compatible", "needs_key": True, "needs_url": True, "privacy": "depends"},
    "hybrid": {"label": "Hybrid (SLM + LLM)", "needs_key": True, "needs_url": False, "privacy": "hybrid"},
}


def _ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    """Load ai-providers.yaml or return defaults if missing."""
    if CONFIG_FILE.is_file():
        try:
            raw = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                # Merge with defaults so new providers get added on upgrade
                merged = dict(_DEFAULT_CONFIG)
                merged.update(raw)
                merged_providers = dict(_DEFAULT_CONFIG["providers"])
                for k, v in raw.get("providers", {}).items():
                    if k in merged_providers and isinstance(v, dict):
                        merged_providers[k] = {**merged_providers[k], **v}
                    else:
                        merged_providers[k] = v
                merged["providers"] = merged_providers
                return merged
        except (yaml.YAMLError, OSError):
            pass
    return dict(_DEFAULT_CONFIG)


def save_config(cfg: dict[str, Any]) -> None:
    """Write config to ai-providers.yaml."""
    _ensure_config_dir()
    header = (
        "# AI provider configuration for LEco DevOps onboarding.\n"
        "# This file is gitignored. Do not commit API keys.\n"
        "# Managed by the dashboard AI Settings panel.\n\n"
    )
    body = yaml.dump(cfg, default_flow_style=False, sort_keys=False, allow_unicode=True)
    CONFIG_FILE.write_text(header + body, encoding="utf-8")
    try:
        CONFIG_FILE.chmod(0o600)
    except OSError:
        pass


def mask_key(key: str) -> str:
    """Mask an API key for display: show first 4 + last 4 chars."""
    if not key or len(key) < 12:
        return "••••" if key else ""
    return key[:4] + "•" * (len(key) - 8) + key[-4:]


def config_for_ui() -> dict[str, Any]:
    """Return config safe for browser display (keys masked)."""
    cfg = load_config()
    safe = {
        "default_provider": cfg.get("default_provider", "none"),
        "default_model": cfg.get("default_model", ""),
        "timeout": cfg.get("timeout", 180),
        "providers": {},
        "provider_meta": PROVIDER_META,
    }
    for pname, pcfg in cfg.get("providers", {}).items():
        p = dict(pcfg)
        if "api_key" in p:
            p["api_key_set"] = bool(p["api_key"])
            p["api_key_masked"] = mask_key(p["api_key"])
            del p["api_key"]
        if "cloud_api_key" in p:
            p["cloud_api_key_set"] = bool(p["cloud_api_key"])
            p["cloud_api_key_masked"] = mask_key(p["cloud_api_key"])
            del p["cloud_api_key"]
        safe["providers"][pname] = p
    # Remap default_provider → provider for UI consistency
    safe["provider"] = safe.pop("default_provider")
    return safe


def update_from_ui(data: dict[str, Any]) -> dict[str, Any]:
    """Merge UI-submitted settings into the existing config.

    Only overwrites api_key if the submitted value is not masked (contains
    real characters, not just dots).  This prevents the masked display
    value from overwriting the real key.
    """
    cfg = load_config()
    if "provider" in data:
        cfg["default_provider"] = data["provider"]
    elif "default_provider" in data:
        cfg["default_provider"] = data["default_provider"]
    if "default_model" in data:
        cfg["default_model"] = data["default_model"]
    if "timeout" in data:
        cfg["timeout"] = max(10, min(900, int(data["timeout"])))
    for pname, pcfg in data.get("providers", {}).items():
        if pname not in cfg["providers"]:
            cfg["providers"][pname] = {}
        for k, v in pcfg.items():
            if k in ("api_key", "cloud_api_key"):
                # Only overwrite if the value looks like a real key (not masked)
                if v and "•" not in v:
                    cfg["providers"][pname][k] = v
            else:
                cfg["providers"][pname][k] = v
    save_config(cfg)
    return config_for_ui()


def get_provider_config() -> dict[str, Any]:
    """Return full config with real keys (server-side use only)."""
    cfg = load_config()
    return {
        "provider": cfg.get("default_provider", "none"),
        "default_model": cfg.get("default_model", ""),
        "timeout": cfg.get("timeout", 180),
        "providers": cfg.get("providers", {}),
    }
