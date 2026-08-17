"""
Configuration management for AI-assisted onboarding.

Reads/writes config/ai-providers.yaml (gitignored, server-side only).
API keys are never sent to the browser — only masked versions.

Nothing in this module ever writes a credential into the repository tree
outside of ``config/ai-providers.yaml`` (which is gitignored), into a
manifest, or into a response body.  ``config_for_ui()`` is the single
browser-facing projection and it strips every secret-shaped field.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(os.getenv("DASHBOARD_PROJECT_ROOT", "/project"))
CONFIG_DIR = PROJECT_ROOT / "config"
CONFIG_FILE = CONFIG_DIR / "ai-providers.yaml"

# Repo-relative path shown in the UI so the operator knows exactly where the
# credentials live.  Kept as a literal because the container mounts the repo
# at /project and the absolute path is not meaningful to the reader.
CONFIG_REL_PATH = "config/ai-providers.yaml"

# Any config field whose name matches one of these fragments is treated as a
# secret: never returned to the browser, never logged.
_SECRET_FIELD_FRAGMENTS = ("api_key", "apikey", "token", "secret", "password")

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
            "default_model": "claude-sonnet-5",
            "timeout": 120,
        },
        "google": {
            "api_key": "",
            "default_model": "gemini-2.0-flash",
            "timeout": 120,
        },
        "openai-compatible": {
            "preset": "custom",
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

# Provider display metadata.
#   privacy   — "full" (nothing leaves the machine), "cloud" (source excerpts
#               are sent to a third party), "depends" (operator-chosen
#               endpoint), "hybrid" (local summarize → cloud analyze).
#   discovery — "api" (provider exposes a real model-list endpoint),
#               "api+curated" (endpoint tried first, curated list as fallback),
#               "none".
PROVIDER_META: dict[str, dict[str, Any]] = {
    "none": {
        "label": "No AI (deterministic only)",
        "needs_key": False,
        "needs_url": False,
        "privacy": "full",
        "discovery": "none",
        "supports_presets": False,
        "privacy_note": "No model is contacted. Registration uses deterministic detection only.",
    },
    "ollama": {
        "label": "Ollama (local)",
        "needs_key": False,
        "needs_url": True,
        "privacy": "full",
        "discovery": "api",
        "supports_presets": False,
        "docs_url": "https://ollama.com/library",
        "site_url": "https://ollama.com",
        "privacy_note": "Runs on this machine. Source code never leaves the host.",
    },
    "airllm": {
        "label": "AirLLM (local large models)",
        "needs_key": False,
        "needs_url": True,
        "privacy": "full",
        "discovery": "api",
        "supports_presets": False,
        "docs_url": "https://github.com/lyogavin/airllm",
        "site_url": "https://github.com/lyogavin/airllm",
        "privacy_note": "Runs on this machine. Source code never leaves the host.",
    },
    "openai": {
        "label": "OpenAI",
        "needs_key": True,
        "needs_url": False,
        "privacy": "cloud",
        "discovery": "api",
        "supports_presets": False,
        "docs_url": "https://platform.openai.com/docs/models",
        "site_url": "https://openai.com",
        "key_hint": "sk-…",
        "privacy_note": "Source excerpts (truncated) are sent to OpenAI's API for analysis.",
    },
    "anthropic": {
        "label": "Anthropic (Claude API)",
        "needs_key": True,
        "needs_url": False,
        "privacy": "cloud",
        "discovery": "api+curated",
        "supports_presets": False,
        "docs_url": "https://docs.anthropic.com/en/docs/about-claude/models",
        "site_url": "https://www.anthropic.com",
        "key_hint": "sk-ant-…",
        "privacy_note": "Source excerpts (truncated) are sent to Anthropic's API for analysis.",
    },
    "google": {
        "label": "Google (Gemini API)",
        "needs_key": True,
        "needs_url": False,
        "privacy": "cloud",
        "discovery": "api",
        "supports_presets": False,
        "docs_url": "https://ai.google.dev/gemini-api/docs/models",
        "site_url": "https://ai.google.dev",
        "key_hint": "AIza…",
        "privacy_note": "Source excerpts (truncated) are sent to Google's Generative Language API.",
    },
    "openai-compatible": {
        "label": "Aggregator / OpenAI-compatible",
        "needs_key": True,
        "needs_url": True,
        "privacy": "depends",
        "discovery": "api",
        "supports_presets": True,
        "docs_url": "https://platform.openai.com/docs/api-reference/models",
        "privacy_note": "Privacy depends on the endpoint you point at — local gateways keep data on-host, hosted aggregators do not.",
    },
    "hybrid": {
        "label": "Hybrid (local SLM + cloud LLM)",
        "needs_key": True,
        "needs_url": False,
        "privacy": "hybrid",
        "discovery": "api",
        "supports_presets": False,
        "privacy_note": "Raw source stays local; only the local model's summary is sent to the cloud model.",
    },
}

# Aggregator / gateway presets for the generic openai-compatible provider.
# These are *metadata only* — they resolve to a base URL, they do not add
# provider classes.  `custom` is the escape hatch.
PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "custom": {
        "label": "Custom endpoint",
        "base_url": "",
        "kind": "custom",
        "privacy": "depends",
        "docs_url": "https://platform.openai.com/docs/api-reference/models",
        "note": "Any endpoint that serves GET /models and POST /chat/completions.",
    },
    "edenai": {
        "label": "Eden AI",
        # Eden AI v3 is an OpenAI-compatible surface: GET /v3/models returns the
        # standard {"object":"list","data":[…]} shape (with context_length and
        # per-token pricing) and POST /v3/chat/completions is a drop-in.  It
        # therefore needs no provider class of its own — only this base URL.
        "base_url": "https://api.edenai.run/v3",
        "kind": "aggregator",
        "privacy": "cloud",
        "docs_url": "https://www.edenai.co/docs/v3/llms/listing-models",
        "site_url": "https://www.edenai.co",
        "key_hint": "Bearer token from the Eden AI console",
        "note": (
            "One key across many vendors; model ids are 'provider/model' (e.g. openai/gpt-4o). "
            "Its v3 model catalogue is public, so Connect lists models even before you paste a key — "
            "a key is still required to run an analysis."
        ),
        # Model discovery exercised live against the public /v3/models endpoint.
        # Chat completions could not be exercised without a key.
        "verified": True,
        "verified_note": "Model discovery verified live. Chat completions wired per Eden AI's docs but not exercised without a key.",
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "kind": "aggregator",
        "privacy": "cloud",
        "docs_url": "https://openrouter.ai/docs/quickstart",
        "site_url": "https://openrouter.ai",
        "key_hint": "sk-or-v1-…",
        "note": "300+ models from many vendors behind one key.",
        "verified": True,
        "verified_note": "Model discovery verified live against the public catalogue.",
    },
    "groq": {
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "kind": "aggregator",
        "privacy": "cloud",
        "docs_url": "https://console.groq.com/docs/openai",
        "site_url": "https://groq.com",
        "key_hint": "gsk_…",
        "verified": "partial",
        "verified_note": "Endpoint and auth path reached live (a wrong key is correctly rejected). Listing with a valid key was not exercised.",
    },
    "together": {
        "label": "Together AI",
        "base_url": "https://api.together.xyz/v1",
        "kind": "aggregator",
        "privacy": "cloud",
        "docs_url": "https://docs.together.ai/docs/openai-api-compatibility",
        "site_url": "https://www.together.ai",
    },
    "deepinfra": {
        "label": "DeepInfra",
        "base_url": "https://api.deepinfra.com/v1/openai",
        "kind": "aggregator",
        "privacy": "cloud",
        "docs_url": "https://deepinfra.com/docs/openai_api",
        "site_url": "https://deepinfra.com",
    },
    "fireworks": {
        "label": "Fireworks AI",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "kind": "aggregator",
        "privacy": "cloud",
        "docs_url": "https://docs.fireworks.ai/tools-sdks/openai-compatibility",
        "site_url": "https://fireworks.ai",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "kind": "vendor",
        "privacy": "cloud",
        "docs_url": "https://api-docs.deepseek.com/",
        "site_url": "https://www.deepseek.com",
    },
    "mistral": {
        "label": "Mistral AI",
        "base_url": "https://api.mistral.ai/v1",
        "kind": "vendor",
        "privacy": "cloud",
        "docs_url": "https://docs.mistral.ai/api/",
        "site_url": "https://mistral.ai",
    },
    "xai": {
        "label": "xAI (Grok)",
        "base_url": "https://api.x.ai/v1",
        "kind": "vendor",
        "privacy": "cloud",
        "docs_url": "https://docs.x.ai/docs/api-reference",
        "site_url": "https://x.ai",
        "key_hint": "xai-…",
    },
    "cerebras": {
        "label": "Cerebras",
        "base_url": "https://api.cerebras.ai/v1",
        "kind": "aggregator",
        "privacy": "cloud",
        "docs_url": "https://inference-docs.cerebras.ai/",
        "site_url": "https://www.cerebras.ai",
        "key_hint": "csk-…",
    },
    "nvidia-nim": {
        "label": "NVIDIA NIM",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "kind": "aggregator",
        "privacy": "cloud",
        "docs_url": "https://docs.nvidia.com/nim/",
        "site_url": "https://build.nvidia.com",
        "key_hint": "nvapi-…",
    },
    "litellm": {
        "label": "LiteLLM proxy (self-hosted)",
        "base_url": "http://litellm:4000/v1",
        "kind": "gateway",
        "privacy": "depends",
        "docs_url": "https://docs.litellm.ai/docs/simple_proxy",
        "site_url": "https://www.litellm.ai",
        "note": "Self-hosted gateway — privacy depends on the upstreams it fans out to.",
    },
    "vllm": {
        "label": "vLLM (self-hosted)",
        "base_url": "http://vllm:8000/v1",
        "kind": "local",
        "privacy": "full",
        "docs_url": "https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html",
        "site_url": "https://docs.vllm.ai",
    },
    "lmstudio": {
        "label": "LM Studio (local app)",
        "base_url": "http://host.docker.internal:1234/v1",
        "kind": "local",
        "privacy": "full",
        "docs_url": "https://lmstudio.ai/docs/app/api/endpoints/openai",
        "site_url": "https://lmstudio.ai",
        "note": "LM Studio runs on the host; the dashboard reaches it via host.docker.internal.",
    },
    "localai": {
        "label": "LocalAI (self-hosted)",
        "base_url": "http://localai:8080/v1",
        "kind": "local",
        "privacy": "full",
        "docs_url": "https://localai.io/features/openai-functions/",
        "site_url": "https://localai.io",
    },
    "ollama-openai": {
        "label": "Ollama (OpenAI-compatible shim)",
        "base_url": "http://ollama:11434/v1",
        "kind": "local",
        "privacy": "full",
        "docs_url": "https://ollama.com/blog/openai-compatibility",
        "site_url": "https://ollama.com",
        "note": "Use the dedicated Ollama provider unless you need the OpenAI wire format.",
    },
}

# Stable ordering for the UI (dict order is preserved, but be explicit).
PRESET_ORDER: list[str] = list(PROVIDER_PRESETS.keys())

# Curated fallback model lists for providers whose discovery endpoint may be
# unavailable.  Always surfaced to the UI labelled as "curated" so the screen
# never implies we probed something we did not.
CURATED_MODELS: dict[str, list[dict[str, Any]]] = {
    "anthropic": [
        {"name": "claude-opus-4-20250514", "context_window": 200_000, "description": "Most capable Claude 4 model"},
        {"name": "claude-sonnet-5", "context_window": 200_000, "description": "Balanced — the usual default"},
        {"name": "claude-opus-5", "context_window": 200_000, "description": "Frontier capability"},
        {"name": "claude-haiku-4-5-20251001", "context_window": 200_000, "description": "Fast, low-cost"},
    ],
}


# ---------------------------------------------------------------------------
# Capability tiers — a CURATED HINT, never a measured benchmark
# ---------------------------------------------------------------------------
#
# No provider we talk to returns an accuracy or benchmark score through its
# model-list endpoint (OpenRouter, Groq, Together, OpenAI, Google and Anthropic
# all return identity + capacity + price, not quality).  So when the operator
# wants to choose "by accuracy" we can offer two honest things:
#
#   1. the real signals the provider *does* give — context window and price,
#   2. a hand-maintained family tier derived from each vendor's own public
#      positioning of its line-up.
#
# The second is a static hint and is always transported with
# ``quality_source: "curated-hint"`` so the screen can say so out loud.  If a
# provider ever does return a ranking, that becomes ``quality_source:
# "provider"`` and takes precedence.

TIER_META: dict[str, dict[str, Any]] = {
    "frontier": {"label": "Frontier", "rank": 0, "note": "Vendor's most capable line — best accuracy, highest cost/latency."},
    "balanced": {"label": "Balanced", "rank": 1, "note": "Vendor's workhorse line — strong accuracy at moderate cost."},
    "fast": {"label": "Fast / cheap", "rank": 2, "note": "Small or distilled line — lowest cost and latency, lower accuracy."},
    "": {"label": "Unrated", "rank": 3, "note": "No curated tier for this model family."},
}

# Ordered rules; the first match wins, so narrow patterns come before broad
# ones ("gpt-4o-mini" must hit the fast rule before the gpt-4 balanced rule).
#
# Fragments are matched as whole tokens, not raw substrings: "-" "/" "." "_"
# ":" and the string edges act as boundaries.  Plain substring matching is
# wrong here — "mini" is a substring of "gemini", which mis-tiered every
# Gemini model as fast/cheap.
_TIER_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("fast", ("mini", "haiku", "flash-lite", "nano", "instant", "lite", "tiny", "small", "scout")),
    ("frontier", ("opus", "gpt-5", "o3", "o1", "pro", "grok-4", "405b", "max", "large",
                  "deepseek-r1", "r1", "command-r-plus", "ultra", "671b", "235b")),
    ("balanced", ("sonnet", "gpt-4", "gpt-4o", "gpt-4.1", "gpt-oss", "flash", "medium",
                  "70b", "72b", "mixtral", "deepseek-v3", "deepseek-chat", "command-r", "32b", "27b")),
    ("fast", ("8b", "7b", "4b", "3b", "1.5b", "1b", "turbo", "gemma", "phi")),
)

# Pre-compiled token matchers, built once at import.
_TIER_PATTERNS: tuple[tuple[str, tuple[Any, ...]], ...] = tuple(
    (
        tier,
        tuple(
            __import__("re").compile(r"(?<![a-z0-9])" + __import__("re").escape(frag) + r"(?![a-z0-9])")
            for frag in fragments
        ),
    )
    for tier, fragments in _TIER_RULES
)


def model_tier(model_id: str) -> str:
    """Curated capability tier for a model id ("" when no rule matches)."""
    name = (model_id or "").lower()
    if not name:
        return ""
    for tier, patterns in _TIER_PATTERNS:
        if any(p.search(name) for p in patterns):
            return tier
    return ""


def tier_info(tier: str) -> dict[str, Any]:
    """Label / rank / note for a tier id."""
    return {"tier": tier, **TIER_META.get(tier, TIER_META[""])}


def annotate_model(entry: dict[str, Any]) -> dict[str, Any]:
    """Attach tier metadata to one model dict from a provider response.

    Never overwrites a ranking the provider itself supplied.
    """
    out = dict(entry)
    if out.get("quality_source") == "provider":
        return out
    tier = model_tier(out.get("id") or out.get("name") or "")
    info = tier_info(tier)
    out["tier"] = tier
    out["tier_label"] = info["label"]
    out["tier_rank"] = info["rank"]
    out["quality_source"] = "curated-hint" if tier else ""
    return out


def _ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def is_secret_field(name: str) -> bool:
    """True when a config field name looks like it holds a credential."""
    low = str(name).lower()
    return any(frag in low for frag in _SECRET_FIELD_FRAGMENTS)


def default_config() -> dict[str, Any]:
    """A deep copy of the built-in defaults (safe to mutate)."""
    return copy.deepcopy(_DEFAULT_CONFIG)


def load_config() -> dict[str, Any]:
    """Load ai-providers.yaml or return defaults if missing."""
    base = default_config()
    if not CONFIG_FILE.is_file():
        return base
    try:
        raw = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return base
    if not isinstance(raw, dict):
        return base
    merged = dict(base)
    for k, v in raw.items():
        if k != "providers":
            merged[k] = v
    merged_providers = dict(base["providers"])
    for k, v in (raw.get("providers") or {}).items():
        if isinstance(v, dict) and isinstance(merged_providers.get(k), dict):
            merged_providers[k] = {**merged_providers[k], **v}
        else:
            merged_providers[k] = v
    merged["providers"] = merged_providers
    return merged


def save_config(cfg: dict[str, Any]) -> None:
    """Write config to ai-providers.yaml (0600, gitignored)."""
    _ensure_config_dir()
    header = (
        "# AI provider configuration for LEco DevOps onboarding.\n"
        "# This file is gitignored. Do not commit API keys.\n"
        "# Managed by the dashboard AI configuration screen.\n\n"
    )
    body = yaml.dump(cfg, default_flow_style=False, sort_keys=False, allow_unicode=True)
    CONFIG_FILE.write_text(header + body, encoding="utf-8")
    try:
        CONFIG_FILE.chmod(0o600)
    except OSError:
        pass


# Dots shown between the visible head and tail of a masked key.  Fixed rather
# than proportional so a 100-character key does not stretch the field — and so
# the mask does not leak the key's exact length.
_MASK_DOTS = 12


def mask_key(key: str) -> str:
    """Mask an API key for display: show first 4 + last 4 chars."""
    if not key or len(key) < 12:
        return "••••" if key else ""
    return key[:4] + "•" * _MASK_DOTS + key[-4:]


def is_masked(value: str) -> bool:
    """True when the submitted value is a masked echo, not a real key."""
    return bool(value) and "•" in value


def preset_meta(preset_id: str) -> dict[str, Any]:
    """Return preset metadata, or the custom escape hatch when unknown."""
    pid = (preset_id or "").strip().lower()
    return PROVIDER_PRESETS.get(pid) or PROVIDER_PRESETS["custom"]


def resolve_preset(preset_id: str, base_url: str = "") -> str:
    """Resolve a preset id (+ optional operator override) to a base URL.

    An explicit ``base_url`` always wins — presets are a convenience, never a
    cage.  Unknown preset ids fall back to the submitted base URL.
    """
    explicit = (base_url or "").strip()
    if explicit:
        return explicit.rstrip("/")
    return str(preset_meta(preset_id).get("base_url") or "").rstrip("/")


def presets_for_ui() -> list[dict[str, Any]]:
    """Preset catalogue for the configuration screen.

    Every preset carries an explicit ``verified`` flag so the screen can say
    which endpoints were actually exercised against a live service and which
    are wired from vendor documentation only.  Defaulting to ``False`` keeps an
    unexercised preset from silently reading as tested.
    """
    out: list[dict[str, Any]] = []
    for pid in PRESET_ORDER:
        meta = dict(PROVIDER_PRESETS[pid])
        meta.setdefault("verified", pid == "custom")
        meta.setdefault(
            "verified_note",
            "" if pid == "custom" else
            "Base URL taken from the vendor's documentation; not exercised against a live key by the LEco maintainers.",
        )
        out.append({"id": pid, **meta})
    return out


def storage_info() -> dict[str, Any]:
    """Where the configuration lives — surfaced verbatim in the UI."""
    return {
        "path": CONFIG_REL_PATH,
        "gitignored": True,
        "server_side_only": True,
        "exists": CONFIG_FILE.is_file(),
        "mode": "0600",
        "note": (
            "Credentials are stored only in "
            f"{CONFIG_REL_PATH} on the dashboard host (mode 0600, gitignored). "
            "They are never written into the repository, a manifest, leco.app.yaml, "
            "a log line, or any API response — the browser only ever receives a mask."
        ),
    }


def _platform_ai_hints() -> dict[str, Any]:
    try:
        from platform_config import ai_platform_hints

        return ai_platform_hints()
    except Exception:
        return {}


def _safe_provider_section(pcfg: dict[str, Any]) -> dict[str, Any]:
    """Strip every secret-shaped field, replacing it with set/masked flags."""
    safe: dict[str, Any] = {}
    for key, value in pcfg.items():
        if is_secret_field(key):
            raw = value if isinstance(value, str) else ("" if value is None else str(value))
            safe[f"{key}_set"] = bool(raw)
            safe[f"{key}_masked"] = mask_key(raw)
        else:
            safe[key] = value
    return safe


def config_for_ui() -> dict[str, Any]:
    """Return config safe for browser display (no key ever, masked only)."""
    cfg = load_config()
    hints = _platform_ai_hints()
    if hints.get("cloud_first") and cfg.get("default_provider") in (None, "", "none", "ollama"):
        cfg = dict(cfg)
        cfg["default_provider"] = hints.get("default_provider") or "openai"
    safe: dict[str, Any] = {
        "default_provider": cfg.get("default_provider", "none"),
        "default_model": cfg.get("default_model", ""),
        "timeout": cfg.get("timeout", 180),
        "providers": {},
        "provider_meta": PROVIDER_META,
        "presets": presets_for_ui(),
        "storage": storage_info(),
        "platform": hints,
    }
    for pname, pcfg in (cfg.get("providers") or {}).items():
        if not isinstance(pcfg, dict):
            continue
        safe["providers"][pname] = _safe_provider_section(pcfg)
    # Remap default_provider → provider for UI consistency
    safe["provider"] = safe.pop("default_provider")
    return safe


def update_from_ui(data: dict[str, Any]) -> dict[str, Any]:
    """Merge UI-submitted settings into the existing config.

    Secret fields are only overwritten when the submitted value is a real key
    (masked echoes are ignored, so re-saving the screen never destroys a stored
    credential).  Sending ``{"<field>_clear": true}`` removes a stored key.
    """
    cfg = load_config()
    if "provider" in data:
        cfg["default_provider"] = str(data["provider"] or "none")
    elif "default_provider" in data:
        cfg["default_provider"] = str(data["default_provider"] or "none")
    if "default_model" in data:
        cfg["default_model"] = str(data["default_model"] or "")
    if "timeout" in data:
        try:
            cfg["timeout"] = max(10, min(900, int(data["timeout"])))
        except (TypeError, ValueError):
            pass

    providers = data.get("providers")
    if isinstance(providers, dict):
        for pname, pcfg in providers.items():
            if not isinstance(pcfg, dict):
                continue
            section = cfg["providers"].setdefault(pname, {})
            for k, v in pcfg.items():
                if k.endswith("_clear"):
                    target = k[: -len("_clear")]
                    if v and is_secret_field(target):
                        section[target] = ""
                    continue
                if k.endswith("_set") or k.endswith("_masked"):
                    # UI projection fields — never persisted.
                    continue
                if is_secret_field(k):
                    if isinstance(v, str) and v.strip() and not is_masked(v):
                        section[k] = v.strip()
                    continue
                section[k] = v
            # Preset convenience: fill the base URL when the operator picked a
            # preset and left the URL blank.
            if pname == "openai-compatible":
                resolved = resolve_preset(section.get("preset", ""), section.get("base_url", ""))
                if resolved:
                    section["base_url"] = resolved

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


def probe_config(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a server-side provider config for a connect / discover probe.

    ``overrides`` may carry not-yet-saved screen values:
    ``provider``, ``preset``, ``base_url``, ``api_key``, ``default_model``,
    ``timeout``.

    Credential safety rule: a stored key is only reused when the probe targets
    the *stored* endpoint.  If the caller supplies a different base URL, the
    stored key is dropped and only an explicitly-submitted key is used, so a
    saved credential can never be pushed to an endpoint the operator merely
    typed into the form.
    """
    o = overrides or {}
    cfg = get_provider_config()
    name = str(o.get("provider") or cfg.get("provider") or "none").strip().lower()
    cfg["provider"] = name
    if name in ("none", ""):
        return cfg

    section = dict(cfg["providers"].get(name) or {})
    stored_url = str(section.get("base_url") or "").rstrip("/")

    preset = str(o.get("preset") or section.get("preset") or "").strip()
    submitted_url = str(o.get("base_url") or "").strip().rstrip("/")
    if name == "openai-compatible":
        if o.get("preset") is not None or submitted_url:
            section["preset"] = preset or "custom"
            resolved = resolve_preset(preset, submitted_url)
            if resolved:
                section["base_url"] = resolved
    elif submitted_url:
        section["base_url"] = submitted_url

    # Hybrid keeps its cloud credential under a different field name.
    key_field = "cloud_api_key" if name == "hybrid" else "api_key"

    if name == "hybrid":
        for src, dst in (("local_provider", "local_provider"), ("cloud_provider", "cloud_provider"),
                         ("local_model", "local_model"), ("cloud_model", "cloud_model")):
            val = str(o.get(src) or "").strip()
            if val:
                section[dst] = val

    new_url = str(section.get("base_url") or "").rstrip("/")
    submitted_key = o.get("api_key") or o.get(key_field)
    if isinstance(submitted_key, str) and submitted_key.strip() and not is_masked(submitted_key):
        section[key_field] = submitted_key.strip()
    elif new_url and stored_url and new_url != stored_url:
        # Endpoint changed in the form and no key was supplied for it —
        # do not forward the stored credential to a different host.
        section[key_field] = ""

    model = str(o.get("default_model") or o.get("model") or "").strip()
    if model:
        if name == "hybrid":
            section["local_model"] = model
        else:
            section["default_model"] = model
        cfg["default_model"] = model

    timeout = o.get("timeout")
    if timeout is not None:
        try:
            section["timeout"] = max(10, min(900, int(timeout)))
        except (TypeError, ValueError):
            pass

    cfg["providers"] = {**cfg["providers"], name: section}
    return cfg
