"""Platform config and profile resolution."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ecosystem-stack" / "lib"))

from platform_config import (  # noqa: E402
    profile_to_enabled_services,
    resolve_profile,
)


def test_minimal_profile_services():
    svcs = profile_to_enabled_services("minimal")
    assert "traefik" in svcs
    assert "dashboard" in svcs
    assert "ollama" not in svcs


def test_ai_cloud_extends_ai_full():
    resolved = resolve_profile("ai-cloud")
    assert "ollama" in (resolved.get("services") or [])
    ai = resolved.get("ai") or {}
    assert ai.get("prefer_cloud") is True
    assert ai.get("default_provider") == "openai"


def test_cloudflare_full_bundle():
    svcs = profile_to_enabled_services("cloudflare-full")
    assert "cloudflare-local" in svcs
