"""Dedicated in-compose Cloudflare-local adapter URL defaults."""

from __future__ import annotations

import os

import pytest

from leco_app.local_cf_provision import adapter_http_bases


@pytest.fixture(autouse=True)
def _clear_dedicated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "LECO_DEDICATED_KV_ADAPTER_URL",
        "LECO_DEDICATED_R2_ADAPTER_URL",
        "LECO_DEDICATED_D1_ADAPTER_URL",
    ):
        monkeypatch.delenv(k, raising=False)


def test_adapter_http_bases_dedicated_default_ports() -> None:
    b = adapter_http_bases(dedicated=True)
    assert b["kv"] == "http://leco-local-kv-adapter:8082"
    assert b["r2"] == "http://leco-local-r2-adapter:8081"
    assert b["d1"] == "http://leco-local-d1-adapter:8083"


def test_adapter_http_bases_dedicated_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LECO_DEDICATED_KV_ADAPTER_URL", "http://custom-kv:9999")
    b = adapter_http_bases(dedicated=True)
    assert b["kv"] == "http://custom-kv:9999"
