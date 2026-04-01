"""Teardown must use INTERNAL adapter URLs from env when YAML holds public *.lh bases."""

from __future__ import annotations

import pytest

from leco_app.local_cf_teardown import teardown_http_bases


@pytest.fixture(autouse=True)
def _clear_internal_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in (
        "LECO_LOCAL_KV_INTERNAL_URL",
        "LECO_LOCAL_R2_INTERNAL_URL",
        "LECO_LOCAL_D1_INTERNAL_URL",
        "LECO_LOCAL_KV_URL",
        "LECO_LOCAL_R2_URL",
        "LECO_LOCAL_D1_URL",
    ):
        monkeypatch.delenv(k, raising=False)


def test_teardown_http_bases_public_lh_uses_adapter_http_bases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LECO_LOCAL_KV_INTERNAL_URL", "http://kv-adapter:8082")
    monkeypatch.setenv("LECO_LOCAL_R2_INTERNAL_URL", "http://r2-adapter:8081")
    monkeypatch.setenv("LECO_LOCAL_D1_INTERNAL_URL", "http://d1-adapter:8083")
    doc = {
        "adapters": {
            "kv": "https://kv.lh",
            "r2": "https://r2.lh",
            "d1": "https://d1.lh",
        }
    }
    b = teardown_http_bases(doc)
    assert b["kv"] == "http://kv-adapter:8082"
    assert b["r2"] == "http://r2-adapter:8081"
    assert b["d1"] == "http://d1-adapter:8083"


def test_teardown_http_bases_dedicated_uses_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = {
        "adapters": {
            "kv": "http://leco-local-kv-adapter:8082",
            "r2": "http://leco-local-r2-adapter:8081",
            "d1": "http://leco-local-d1-adapter:8083",
        }
    }
    b = teardown_http_bases(doc)
    assert b == doc["adapters"]


def test_teardown_http_bases_prefix_hosts_fallback_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """localCfPublicPrefix writes https://cv-kv.lh — same as public lh; use INTERNAL."""
    monkeypatch.setenv("LECO_LOCAL_KV_INTERNAL_URL", "http://kv-adapter:8082")
    monkeypatch.setenv("LECO_LOCAL_R2_INTERNAL_URL", "http://r2-adapter:8081")
    monkeypatch.setenv("LECO_LOCAL_D1_INTERNAL_URL", "http://d1-adapter:8083")
    doc = {
        "adapters": {
            "kv": "https://myapp-kv.lh",
            "r2": "https://myapp-r2.lh",
            "d1": "https://myapp-d1.lh",
        }
    }
    b = teardown_http_bases(doc)
    assert b["kv"] == "http://kv-adapter:8082"
