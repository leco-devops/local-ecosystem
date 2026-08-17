"""AI provider configuration: persistence, masking, presets, model normalization.

The load-bearing invariant here is that a full API key must never escape the
server: not through ``config_for_ui()``, not through a probe aimed at an
endpoint the operator merely typed into the form.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))

REAL_KEY = "sk-test-ABCDEFGHIJKLMNOPQRSTUVWXYZ-0123456789"


@pytest.fixture()
def ai_config(tmp_path, monkeypatch):
    """A fresh ai_config module rooted at a throwaway project dir."""
    monkeypatch.setenv("DASHBOARD_PROJECT_ROOT", str(tmp_path))
    import ai_config as mod

    importlib.reload(mod)
    # Platform hints read the real repo; keep the unit under test hermetic.
    monkeypatch.setattr(mod, "_platform_ai_hints", lambda: {})
    yield mod
    importlib.reload(mod)


@pytest.fixture()
def ai_provider():
    import ai_provider as mod

    return mod


# ---------------------------------------------------------------------------
# Load / save / merge
# ---------------------------------------------------------------------------

def test_load_returns_defaults_when_file_missing(ai_config):
    cfg = ai_config.load_config()
    assert cfg["default_provider"] == "none"
    assert "ollama" in cfg["providers"]


def test_defaults_are_not_mutated_by_updates(ai_config):
    """A missing config file must not let writes leak into module defaults."""
    ai_config.update_from_ui({"provider": "openai", "providers": {"openai": {"api_key": REAL_KEY}}})
    pristine = ai_config.default_config()
    assert pristine["providers"]["openai"]["api_key"] == ""
    assert pristine["default_provider"] == "none"


def test_save_then_load_round_trip(ai_config):
    ai_config.update_from_ui(
        {"provider": "ollama", "default_model": "qwen2.5-coder", "timeout": 240,
         "providers": {"ollama": {"base_url": "http://ollama:11434"}}}
    )
    cfg = ai_config.load_config()
    assert cfg["default_provider"] == "ollama"
    assert cfg["timeout"] == 240
    assert ai_config.CONFIG_FILE.is_file()


def test_merge_adds_providers_absent_from_the_saved_file(ai_config):
    ai_config.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ai_config.CONFIG_FILE.write_text(
        "default_provider: ollama\nproviders:\n  ollama:\n    base_url: http://custom:1234\n",
        encoding="utf-8",
    )
    cfg = ai_config.load_config()
    assert cfg["providers"]["ollama"]["base_url"] == "http://custom:1234"
    # Untouched key from defaults survives the merge
    assert cfg["providers"]["ollama"]["default_model"] == "qwen2.5-coder"
    # Provider missing from the file is still present after upgrade merge
    assert "openai-compatible" in cfg["providers"]


def test_malformed_yaml_falls_back_to_defaults(ai_config):
    ai_config.CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ai_config.CONFIG_FILE.write_text("::: not yaml :::\n  - [", encoding="utf-8")
    assert ai_config.load_config()["default_provider"] == "none"


def test_timeout_is_clamped(ai_config):
    ai_config.update_from_ui({"timeout": 99999})
    assert ai_config.load_config()["timeout"] == 900
    ai_config.update_from_ui({"timeout": 1})
    assert ai_config.load_config()["timeout"] == 10


# ---------------------------------------------------------------------------
# Masking — the never-leak-a-key invariant
# ---------------------------------------------------------------------------

def test_mask_key_shape():
    masked = REAL_KEY  # sanity: fixture key is long enough to be masked, not blanked
    assert len(masked) >= 12


def test_mask_key_hides_the_middle(ai_config):
    masked = ai_config.mask_key(REAL_KEY)
    assert masked.startswith(REAL_KEY[:4])
    assert masked.endswith(REAL_KEY[-4:])
    assert REAL_KEY not in masked
    assert "•" in masked


def test_mask_key_blanks_short_and_empty(ai_config):
    assert ai_config.mask_key("") == ""
    assert ai_config.mask_key("short") == "••••"


def test_config_for_ui_never_contains_a_full_key(ai_config):
    ai_config.update_from_ui(
        {
            "provider": "openai",
            "providers": {
                "openai": {"api_key": REAL_KEY},
                "hybrid": {"cloud_api_key": REAL_KEY},
                "openai-compatible": {"api_key": REAL_KEY, "base_url": "https://openrouter.ai/api/v1"},
            },
        }
    )
    safe = ai_config.config_for_ui()
    blob = repr(safe)
    assert REAL_KEY not in blob
    assert "api_key" not in safe["providers"]["openai"]
    assert safe["providers"]["openai"]["api_key_set"] is True
    assert safe["providers"]["hybrid"]["cloud_api_key_set"] is True
    assert "cloud_api_key" not in safe["providers"]["hybrid"]
    # The stored key itself is intact on disk
    assert ai_config.load_config()["providers"]["openai"]["api_key"] == REAL_KEY


def test_config_for_ui_strips_any_secret_shaped_field(ai_config):
    ai_config.update_from_ui({"providers": {"openai-compatible": {"auth_token": REAL_KEY}}})
    safe = ai_config.config_for_ui()
    section = safe["providers"]["openai-compatible"]
    assert "auth_token" not in section
    assert section["auth_token_set"] is True
    assert REAL_KEY not in repr(safe)


def test_config_for_ui_exposes_storage_disclosure(ai_config):
    storage = ai_config.config_for_ui()["storage"]
    assert storage["path"] == "config/ai-providers.yaml"
    assert storage["gitignored"] is True
    assert storage["server_side_only"] is True


def test_masked_echo_does_not_overwrite_the_stored_key(ai_config):
    ai_config.update_from_ui({"providers": {"openai": {"api_key": REAL_KEY}}})
    masked = ai_config.mask_key(REAL_KEY)
    ai_config.update_from_ui({"providers": {"openai": {"api_key": masked}}})
    assert ai_config.load_config()["providers"]["openai"]["api_key"] == REAL_KEY


def test_key_can_be_explicitly_cleared(ai_config):
    ai_config.update_from_ui({"providers": {"openai": {"api_key": REAL_KEY}}})
    ai_config.update_from_ui({"providers": {"openai": {"api_key_clear": True}}})
    assert ai_config.load_config()["providers"]["openai"]["api_key"] == ""


def test_ui_projection_fields_are_never_persisted(ai_config):
    ai_config.update_from_ui(
        {"providers": {"openai": {"api_key_set": True, "api_key_masked": "sk-t••••1234"}}}
    )
    section = ai_config.load_config()["providers"]["openai"]
    assert "api_key_set" not in section
    assert "api_key_masked" not in section


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

def test_known_preset_resolves_to_its_base_url(ai_config):
    assert ai_config.resolve_preset("openrouter") == "https://openrouter.ai/api/v1"
    assert ai_config.resolve_preset("groq") == "https://api.groq.com/openai/v1"


def test_explicit_base_url_overrides_the_preset(ai_config):
    assert ai_config.resolve_preset("openrouter", "http://litellm:4000/v1") == "http://litellm:4000/v1"


def test_unknown_preset_falls_back_to_custom(ai_config):
    assert ai_config.resolve_preset("does-not-exist") == ""
    assert ai_config.preset_meta("does-not-exist")["kind"] == "custom"


def test_preset_catalogue_is_well_formed(ai_config):
    presets = ai_config.presets_for_ui()
    ids = {p["id"] for p in presets}
    assert {"custom", "edenai", "openrouter", "groq", "together", "mistral", "deepseek",
            "litellm", "vllm", "lmstudio"} <= ids
    for p in presets:
        assert p["label"]
        assert p["privacy"] in {"full", "cloud", "depends"}
        if p["id"] != "custom":
            assert p["base_url"].startswith("http")


def test_every_preset_declares_its_verification_state(ai_config):
    """An unexercised preset must never read as tested."""
    for p in ai_config.presets_for_ui():
        assert "verified" in p
        assert p["verified"] in (True, False, "partial")
        if p["verified"] is not True and p["id"] != "custom":
            assert p["verified_note"], f"{p['id']} must explain why it is not verified"


def test_edenai_is_an_openai_compatible_preset(ai_config):
    """Eden AI v3 speaks the OpenAI wire format, so it needs no provider class."""
    eden = ai_config.preset_meta("edenai")
    assert eden["base_url"] == "https://api.edenai.run/v3"
    assert eden["kind"] == "aggregator"
    assert eden["verified"] is True
    assert ai_config.resolve_preset("edenai") == "https://api.edenai.run/v3"


def test_saving_a_preset_fills_the_base_url(ai_config):
    ai_config.update_from_ui(
        {"provider": "openai-compatible", "providers": {"openai-compatible": {"preset": "openrouter"}}}
    )
    section = ai_config.load_config()["providers"]["openai-compatible"]
    assert section["base_url"] == "https://openrouter.ai/api/v1"


# ---------------------------------------------------------------------------
# probe_config — credential blast radius
# ---------------------------------------------------------------------------

def test_probe_uses_the_stored_key_for_the_stored_endpoint(ai_config):
    ai_config.update_from_ui(
        {"provider": "openai-compatible",
         "providers": {"openai-compatible": {"preset": "openrouter", "api_key": REAL_KEY}}}
    )
    cfg = ai_config.probe_config({"provider": "openai-compatible", "preset": "openrouter"})
    assert cfg["providers"]["openai-compatible"]["api_key"] == REAL_KEY


def test_probe_never_forwards_a_stored_key_to_a_different_endpoint(ai_config):
    ai_config.update_from_ui(
        {"provider": "openai-compatible",
         "providers": {"openai-compatible": {"preset": "openrouter", "api_key": REAL_KEY}}}
    )
    cfg = ai_config.probe_config(
        {"provider": "openai-compatible", "preset": "custom", "base_url": "http://someone-elses-host:9000/v1"}
    )
    section = cfg["providers"]["openai-compatible"]
    assert section["base_url"] == "http://someone-elses-host:9000/v1"
    assert section["api_key"] == ""


def test_probe_uses_a_submitted_key_verbatim(ai_config):
    cfg = ai_config.probe_config(
        {"provider": "openai-compatible", "base_url": "http://gw:9000/v1", "api_key": "  fresh-key  "}
    )
    assert cfg["providers"]["openai-compatible"]["api_key"] == "fresh-key"


def test_probe_ignores_a_masked_key_echo(ai_config):
    ai_config.update_from_ui({"provider": "openai", "providers": {"openai": {"api_key": REAL_KEY}}})
    cfg = ai_config.probe_config({"provider": "openai", "api_key": ai_config.mask_key(REAL_KEY)})
    assert cfg["providers"]["openai"]["api_key"] == REAL_KEY


# ---------------------------------------------------------------------------
# Model list normalization
# ---------------------------------------------------------------------------

def test_normalize_plain_openai_payload(ai_provider):
    models = ai_provider.normalize_openai_models(
        {"data": [{"id": "gpt-4o-mini", "owned_by": "openai"}, {"id": "gpt-4o"}]}, "openai"
    )
    assert [m.name for m in models] == ["gpt-4o", "gpt-4o-mini"]
    assert models[1].owned_by == "openai"


def test_normalize_aggregator_payload_keeps_context_and_pricing(ai_provider):
    models = ai_provider.normalize_openai_models(
        {
            "data": [
                {
                    "id": "anthropic/claude-sonnet-4",
                    "name": "Anthropic: Claude Sonnet 4",
                    "description": "A long description",
                    "context_length": 200000,
                    "pricing": {"prompt": "0.000003", "completion": "0.000015", "junk": "x"},
                }
            ]
        },
        "openai-compatible",
    )
    m = models[0]
    assert m.context_window == 200000
    assert m.label == "Anthropic: Claude Sonnet 4"
    assert m.pricing == {"prompt": "0.000003", "completion": "0.000015"}
    assert "junk" not in (m.pricing or {})


def test_normalize_handles_top_provider_and_max_model_len(ai_provider):
    models = ai_provider.normalize_openai_models(
        {"data": [
            {"id": "a", "top_provider": {"context_length": 65536}},
            {"id": "b", "max_model_len": 8192},
        ]},
        "openai-compatible",
    )
    by_id = {m.name: m.context_window for m in models}
    assert by_id["a"] == 65536
    assert by_id["b"] == 8192


def test_normalize_handles_bare_list_and_string_rows(ai_provider):
    models = ai_provider.normalize_openai_models(["llama3", {"id": "mistral"}], "openai-compatible")
    assert [m.name for m in models] == ["llama3", "mistral"]


def test_normalize_dedupes_and_skips_junk(ai_provider):
    models = ai_provider.normalize_openai_models(
        {"data": [{"id": "x"}, {"id": "x"}, {"no_id": 1}, 42, {"id": "  "}]}, "openai-compatible"
    )
    assert [m.name for m in models] == ["x"]


def test_normalize_tolerates_garbage_payloads(ai_provider):
    assert ai_provider.normalize_openai_models(None, "p") == []
    assert ai_provider.normalize_openai_models({"error": "nope"}, "p") == []
    assert ai_provider.normalize_openai_models("a string", "p") == []


def test_normalize_edenai_pricing_shape(ai_provider):
    """Eden AI names the same numbers differently; normalize to prompt/completion."""
    models = ai_provider.normalize_openai_models(
        {"object": "list", "data": [{
            "id": "openai/gpt-4o",
            "owned_by": "openai",
            "context_length": 128000,
            "pricing": {"input_cost_per_token": 2.5e-06, "output_cost_per_token": 1e-05,
                        "cache_read_input_token_cost": 1.25e-06},
        }]},
        "openai-compatible",
    )
    m = models[0]
    assert m.context_window == 128000
    assert m.owned_by == "openai"
    assert m.pricing == {"prompt": "2.5e-06", "completion": "1e-05"}


# ---------------------------------------------------------------------------
# Capability tiers — curated hints, never presented as measurements
# ---------------------------------------------------------------------------

def test_tier_matches_on_token_boundaries_not_substrings(ai_config):
    """Regression: "mini" is a substring of "gemini" and mis-tiered every Gemini model."""
    assert ai_config.model_tier("google/gemini-2.5-pro") == "frontier"
    assert ai_config.model_tier("~google/gemini-flash-latest") == "balanced"
    assert ai_config.model_tier("openai/gpt-4o-mini") == "fast"


def test_tier_classification_examples(ai_config):
    assert ai_config.model_tier("anthropic/claude-opus-4") == "frontier"
    assert ai_config.model_tier("anthropic/claude-sonnet-4") == "balanced"
    assert ai_config.model_tier("anthropic/claude-3-5-haiku") == "fast"
    assert ai_config.model_tier("meta-llama/llama-3.1-405b") == "frontier"
    assert ai_config.model_tier("meta-llama/llama-3.1-8b") == "fast"
    assert ai_config.model_tier("") == ""
    assert ai_config.model_tier("some-unknown-model") == ""


def test_annotate_model_labels_the_tier_as_a_curated_hint(ai_config):
    out = ai_config.annotate_model({"id": "anthropic/claude-opus-4"})
    assert out["tier"] == "frontier"
    assert out["tier_label"] == "Frontier"
    assert out["tier_rank"] == 0
    assert out["quality_source"] == "curated-hint"


def test_annotate_model_leaves_unrated_models_unclaimed(ai_config):
    out = ai_config.annotate_model({"id": "totally-unknown"})
    assert out["tier"] == ""
    assert out["quality_source"] == ""


def test_annotate_model_never_overwrites_a_provider_ranking(ai_config):
    out = ai_config.annotate_model(
        {"id": "anthropic/claude-opus-4", "quality_source": "provider", "tier": "top"}
    )
    assert out["quality_source"] == "provider"
    assert out["tier"] == "top"


def test_model_to_dict_is_bounded_and_key_free(ai_provider):
    m = ai_provider.ModelInfo(
        name="m", provider="p", context_window=1000, description="x" * 5000, pricing={"prompt": "1"}
    )
    d = m.to_dict()
    assert len(d["description"]) == ai_provider.MODEL_DESCRIPTION_LIMIT
    assert d["id"] == "m" and d["label"] == "m"
    assert "api_key" not in d


# ---------------------------------------------------------------------------
# Provider construction
# ---------------------------------------------------------------------------

def test_every_provider_class_is_instantiable(ai_provider):
    """Regression: mis-indented methods once left OllamaProvider abstract."""
    for cls, kwargs in (
        (ai_provider.OllamaProvider, {}),
        (ai_provider.AirLLMProvider, {}),
        (ai_provider.OpenAIProvider, {"api_key": "k"}),
        (ai_provider.AnthropicProvider, {"api_key": "k"}),
        (ai_provider.GoogleProvider, {"api_key": "k"}),
        (ai_provider.OpenAICompatibleProvider, {"base_url": "http://x/v1"}),
    ):
        inst = cls(**kwargs)
        assert callable(inst.list_models)
        assert callable(inst.health_check)
        assert callable(inst.discover)


def test_create_provider_resolves_a_preset_base_url(ai_provider):
    p = ai_provider.create_provider(
        {"provider": "openai-compatible", "providers": {"openai-compatible": {"preset": "groq", "api_key": "k"}}}
    )
    assert isinstance(p, ai_provider.OpenAICompatibleProvider)
    assert p.base_url == "https://api.groq.com/openai/v1"


def test_create_provider_returns_none_without_credentials(ai_provider):
    assert ai_provider.create_provider({"provider": "none"}) is None
    assert ai_provider.create_provider({"provider": "openai", "providers": {"openai": {"api_key": ""}}}) is None
    assert ai_provider.create_provider(
        {"provider": "openai-compatible", "providers": {"openai-compatible": {}}}
    ) is None


def test_discover_reports_unreachable_without_raising(ai_provider):
    p = ai_provider.OpenAICompatibleProvider(base_url="http://127.0.0.1:1/v1", timeout=10)
    status = p.discover()
    assert status.ok is False
    assert status.models == []
    assert status.discovery == "none"
    assert status.message


def test_discover_truncates_and_flags_huge_catalogues(ai_provider, monkeypatch):
    huge = [ai_provider.ModelInfo(name=f"m{i}", provider="x") for i in range(ai_provider.MODEL_LIST_LIMIT + 5)]
    p = ai_provider.OpenAICompatibleProvider(base_url="http://x/v1")
    monkeypatch.setattr(
        p, "health_check",
        lambda: ai_provider.ProviderStatus(ok=True, provider="openai-compatible", models=huge),
    )
    status = p.discover()
    assert status.total_models == ai_provider.MODEL_LIST_LIMIT + 5
    assert len(status.models) == ai_provider.MODEL_LIST_LIMIT
    assert status.truncated is True


def test_anthropic_curated_fallback_is_labelled(ai_provider, monkeypatch):
    p = ai_provider.AnthropicProvider(api_key="k")

    class _Resp:
        status_code = 404
        text = "not found"

        def json(self):
            raise ValueError("no json")

    monkeypatch.setattr(ai_provider.requests, "get", lambda *a, **kw: _Resp())
    status = p.discover()
    assert status.ok is True
    assert status.discovery == "curated"
    assert status.models
    assert "curated" in status.message.lower()


def test_describe_conn_error_is_short(ai_provider):
    exc = Exception(
        "HTTPConnectionPool(host='127.0.0.1', port=1): Max retries exceeded with url: /v1/models "
        "(Caused by NewConnectionError('...: Failed to establish a new connection: [Errno 111] Connection refused'))"
    )
    assert ai_provider.describe_conn_error(exc) == "connection refused"
