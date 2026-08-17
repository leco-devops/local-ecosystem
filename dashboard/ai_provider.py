"""
AI Provider abstraction layer for LEco DevOps AI-assisted onboarding.

Normalises Ollama, OpenAI, Anthropic, Google, and OpenAI-compatible
endpoints behind a single interface.  Each provider handles its own
authentication, JSON extraction, and streaming format.
"""

from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

import requests


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

# Upper bound on how many models one discovery response may carry.  Set high
# enough that real aggregators (OpenRouter is ~330 models) come back complete,
# low enough that a misbehaving gateway cannot flood the browser.  When the cap
# bites we say so via ProviderStatus.truncated rather than silently trimming.
MODEL_LIST_LIMIT = 1000

# Longest model description we forward to the browser.
MODEL_DESCRIPTION_LIMIT = 240


@dataclass
class ModelInfo:
    """Describes one model available on a provider."""
    name: str
    provider: str
    context_window: int = 32_768
    description: str = ""
    label: str = ""
    owned_by: str = ""
    pricing: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Bounded, credential-free projection for API responses."""
        out: dict[str, Any] = {
            "id": self.name,
            "name": self.name,
            "label": self.label or self.name,
            "provider": self.provider,
            "context_window": int(self.context_window or 0),
            "description": (self.description or "")[:MODEL_DESCRIPTION_LIMIT],
        }
        if self.owned_by:
            out["owned_by"] = self.owned_by
        if self.pricing:
            out["pricing"] = self.pricing
        return out


@dataclass
class ProviderStatus:
    """Result of a health / connectivity check."""
    ok: bool
    provider: str
    message: str = ""
    models: list[ModelInfo] = field(default_factory=list)
    # "api"      — the list came from the provider's own model endpoint
    # "curated"  — no discovery endpoint reachable; this is a maintained list
    # "none"     — nothing was listed
    discovery: str = "api"
    latency_ms: int = 0
    total_models: int = 0
    truncated: bool = False
    endpoint: str = ""


@dataclass
class AnalysisResult:
    """Structured output from an AI analysis call."""
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    raw_text: str = ""
    error: str = ""
    model: str = ""
    provider: str = ""
    elapsed_seconds: float = 0.0


@dataclass
class StreamChunk:
    """One piece of a streaming response."""
    type: str          # "token" | "done" | "error"
    text: str = ""
    data: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# JSON extraction helpers
# ---------------------------------------------------------------------------

def _extract_json_from_text(text: str) -> dict | None:
    """Try to pull a JSON object from raw LLM output."""
    # Try direct parse first
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    # Look for ```json ... ``` blocks
    m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Look for <json>...</json> tags (Anthropic style)
    m = re.search(r"<json>\s*(.*?)\s*</json>", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Look for first { ... } block
    depth = 0
    start = None
    for i, c in enumerate(text):
        if c == "{":
            if depth == 0:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    start = None
    return None


# ---------------------------------------------------------------------------
# Model-list normalization
# ---------------------------------------------------------------------------

def describe_conn_error(exc: Exception) -> str:
    """Turn a requests/urllib3 exception into one short operator-readable line.

    Raw urllib3 errors are three lines of nested pool noise; the screen needs
    "connection refused", not a stack of retry wrappers.
    """
    text = str(exc)
    low = text.lower()
    if "connection refused" in low:
        return "connection refused"
    if "name or service not known" in low or "nodename nor servname" in low or "failed to resolve" in low:
        return "host name could not be resolved"
    if "timed out" in low or isinstance(exc, requests.exceptions.Timeout):
        return "timed out"
    if "certificate" in low or "ssl" in low:
        return "TLS/certificate error"
    if "no route to host" in low or "network is unreachable" in low:
        return "network unreachable"
    return text[:180]


def _as_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


# Aggregators disagree on pricing field names for the same number.
# OpenRouter/Together use prompt/completion; Eden AI v3 uses
# input_cost_per_token/output_cost_per_token.  Normalize to prompt/completion
# (USD per token) so the UI has one shape to format.
_PRICING_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("prompt", ("prompt", "input", "input_cost_per_token")),
    ("completion", ("completion", "output", "output_cost_per_token")),
    ("request", ("request", "request_cost")),
    ("image", ("image", "input_cost_per_image_token", "output_cost_per_image")),
)


def _clean_pricing(raw: Any) -> dict[str, Any] | None:
    """Keep the small, well-known pricing fields aggregators expose."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    for canonical, aliases in _PRICING_ALIASES:
        for alias in aliases:
            val = raw.get(alias)
            if val not in (None, ""):
                out[canonical] = str(val)
                break
    return out or None


def normalize_openai_models(payload: Any, provider_name: str) -> list[ModelInfo]:
    """Normalize a ``GET /v1/models`` payload into ModelInfo objects.

    Handles the OpenAI shape (``{"data": [{"id": ...}]}``), the richer
    aggregator shapes (OpenRouter adds ``name``/``description``/
    ``context_length``/``pricing``; Together adds ``context_length``; vLLM
    adds ``max_model_len``), and the bare-list shape some gateways return.
    """
    if isinstance(payload, dict):
        rows = payload.get("data")
        if not isinstance(rows, list):
            rows = payload.get("models")
    else:
        rows = payload
    if not isinstance(rows, list):
        return []

    out: list[ModelInfo] = []
    seen: set[str] = set()
    for row in rows:
        if isinstance(row, str):
            mid, meta = row, {}
        elif isinstance(row, dict):
            meta = row
            mid = meta.get("id") or meta.get("name") or meta.get("model") or ""
        else:
            continue
        mid = str(mid).strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)

        top = meta.get("top_provider") if isinstance(meta.get("top_provider"), dict) else {}
        ctx = (
            _as_int(meta.get("context_length"))
            or _as_int(meta.get("context_window"))
            or _as_int(top.get("context_length"))
            or _as_int(meta.get("max_model_len"))
            or _as_int((meta.get("config") or {}).get("context_length") if isinstance(meta.get("config"), dict) else 0)
        )
        label = str(meta.get("display_name") or meta.get("name") or "").strip()
        if label == mid:
            label = ""
        out.append(
            ModelInfo(
                name=mid,
                provider=provider_name,
                context_window=ctx,
                description=str(meta.get("description") or "").strip(),
                label=label,
                owned_by=str(meta.get("owned_by") or meta.get("organization") or "").strip(),
                pricing=_clean_pricing(meta.get("pricing")),
            )
        )
    out.sort(key=lambda m: m.name)
    return out


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class AIProvider(ABC):
    """Abstract base for all AI providers."""

    provider_name: str = "base"

    @abstractmethod
    def analyze(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        stream: bool = False,
    ) -> AnalysisResult | Iterator[StreamChunk]:
        ...

    @abstractmethod
    def health_check(self) -> ProviderStatus:
        ...

    @abstractmethod
    def list_models(self) -> list[ModelInfo]:
        ...

    def default_token_budget(self) -> int:
        """Suggested token budget for file collection based on provider capability."""
        return 12_000

    # -- connect + discover -------------------------------------------------

    def discover(self) -> ProviderStatus:
        """One coherent "connect and list models" step.

        Verifies reachability via ``health_check()`` and guarantees the status
        carries the provider's full model list, timing, and an honest
        ``discovery`` label so the UI never implies it probed an endpoint that
        does not exist.
        """
        t0 = time.perf_counter()
        try:
            status = self.health_check()
        except Exception as exc:  # never let a provider bug become a 500
            return ProviderStatus(
                ok=False,
                provider=self.provider_name,
                message=str(exc),
                discovery="none",
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )
        if status.ok and not status.models:
            try:
                status.models = self.list_models()
            except Exception as exc:
                status.message = f"{status.message} (model listing failed: {exc})".strip()
        status.latency_ms = int((time.perf_counter() - t0) * 1000)
        status.total_models = len(status.models)
        if status.total_models > MODEL_LIST_LIMIT:
            status.models = status.models[:MODEL_LIST_LIMIT]
            status.truncated = True
        if not status.models and status.discovery == "api":
            status.discovery = "none"
        return status


# ---------------------------------------------------------------------------
# Ollama (local)
# ---------------------------------------------------------------------------

class OllamaProvider(AIProvider):
    """Local Ollama instance — POST http://host:11434/api/generate."""

    provider_name = "ollama"

    def __init__(self, base_url: str = "http://ollama:11434", default_model: str = "qwen2.5-coder", timeout: int = 300):
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self.timeout = max(10, timeout)

    def default_token_budget(self) -> int:
        return 12_000

    def health_check(self) -> ProviderStatus:
        endpoint = f"{self.base_url}/api/tags"
        try:
            r = requests.get(endpoint, timeout=8)
            if r.status_code != 200:
                return ProviderStatus(
                    ok=False, provider=self.provider_name, discovery="none", endpoint=endpoint,
                    message=f"HTTP {r.status_code} from {endpoint}",
                )
            models: list[ModelInfo] = []
            for m in (r.json().get("models") or []):
                if not isinstance(m, dict):
                    continue
                name = str(m.get("name") or m.get("model") or "").strip()
                if not name:
                    continue
                details = m.get("details") if isinstance(m.get("details"), dict) else {}
                bits = [b for b in (details.get("parameter_size"), details.get("quantization_level"), details.get("family")) if b]
                size_gb = round(_as_int(m.get("size")) / 1_000_000_000, 1) if m.get("size") else 0
                if size_gb:
                    bits.append(f"{size_gb} GB on disk")
                models.append(
                    ModelInfo(name=name, provider=self.provider_name, context_window=0, description=" · ".join(str(b) for b in bits))
                )
            models.sort(key=lambda m: m.name)
            msg = f"{len(models)} model(s) installed" if models else "reachable, but no models are pulled yet (ollama pull <model>)"
            return ProviderStatus(ok=True, provider=self.provider_name, message=msg, models=models, endpoint=endpoint)
        except Exception as exc:
            return ProviderStatus(
                ok=False, provider=self.provider_name, discovery="none", endpoint=endpoint,
                message=f"{endpoint} unreachable: {describe_conn_error(exc)}",
            )

    def list_models(self) -> list[ModelInfo]:
        status = self.health_check()
        return status.models if status.ok else []

    def analyze(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        stream: bool = False,
    ) -> AnalysisResult | Iterator[StreamChunk]:
        mdl = model or self.default_model
        payload = {
            "model": mdl,
            "system": system_prompt,
            "prompt": user_prompt,
            "format": "json",
            "stream": stream,
            "options": {"temperature": 0.1, "num_predict": 4096},
        }
        if stream:
            return self._stream(payload, mdl)
        return self._generate(payload, mdl)

    def _generate(self, payload: dict, mdl: str) -> AnalysisResult:
        t0 = time.time()
        try:
            r = requests.post(f"{self.base_url}/api/generate", json=payload, timeout=self.timeout)
            elapsed = time.time() - t0
            if r.status_code != 200:
                return AnalysisResult(ok=False, error=f"Ollama HTTP {r.status_code}: {r.text[:500]}", model=mdl, provider=self.provider_name, elapsed_seconds=elapsed)
            body = r.json()
            raw = body.get("response", "")
            parsed = _extract_json_from_text(raw)
            if parsed is None:
                return AnalysisResult(ok=False, raw_text=raw, error="Failed to extract JSON from Ollama response", model=mdl, provider=self.provider_name, elapsed_seconds=elapsed)
            return AnalysisResult(ok=True, data=parsed, raw_text=raw, model=mdl, provider=self.provider_name, elapsed_seconds=elapsed)
        except Exception as exc:
            return AnalysisResult(ok=False, error=str(exc), model=mdl, provider=self.provider_name, elapsed_seconds=time.time() - t0)

    def _stream(self, payload: dict, mdl: str) -> Iterator[StreamChunk]:
        try:
            r = requests.post(f"{self.base_url}/api/generate", json=payload, timeout=self.timeout, stream=True)
            if r.status_code != 200:
                yield StreamChunk(type="error", text=f"Ollama HTTP {r.status_code}")
                return
            full_text = ""
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                token = obj.get("response", "")
                if token:
                    full_text += token
                    yield StreamChunk(type="token", text=token)
                if obj.get("done"):
                    parsed = _extract_json_from_text(full_text)
                    yield StreamChunk(type="done", text=full_text, data=parsed)
                    return
        except Exception as exc:
            yield StreamChunk(type="error", text=str(exc))


class AirLLMProvider(OllamaProvider):
    """Local AirLLM shim — same protocol as Ollama, different base URL.

    AirLLM serves HuggingFace models using layer-by-layer loading,
    enabling large models (70B/405B) on limited VRAM.
    """

    provider_name = "airllm"

    def __init__(self, base_url: str = "http://airllm:11435", default_model: str = "Qwen/Qwen2.5-7B-Instruct", timeout: int = 600):
        super().__init__(base_url=base_url, default_model=default_model, timeout=timeout)

    def default_token_budget(self) -> int:
        # AirLLM is optimized for large models; can handle more tokens
        return 16_000


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

class OpenAIProvider(AIProvider):
    """OpenAI chat completions API."""

    provider_name = "openai"

    def __init__(self, api_key: str, default_model: str = "gpt-4o-mini", base_url: str = "https://api.openai.com/v1", timeout: int = 120):
        self.api_key = api_key
        self.default_model = default_model
        self.base_url = base_url.rstrip("/")
        self.timeout = max(10, timeout)

    def default_token_budget(self) -> int:
        return 30_000

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    # Model id fragments that are never usable for chat/JSON analysis.  Used
    # only to keep the OpenAI first-party picker readable; aggregators are
    # never filtered because their catalogues are the point.
    _NON_CHAT_FRAGMENTS = (
        "embedding", "whisper", "tts", "dall-e", "moderation",
        "audio", "transcribe", "image", "search-", "similarity",
    )

    def _fetch_models(self) -> tuple[bool, str, list[ModelInfo], str]:
        """Return (ok, message, models, endpoint) from GET {base_url}/models."""
        endpoint = f"{self.base_url}/models"
        try:
            # Generous: Eden AI's 897-model catalogue takes ~10 s to serve.
            r = requests.get(endpoint, headers=self._headers(), timeout=30)
        except Exception as exc:
            return False, f"{endpoint} unreachable: {describe_conn_error(exc)}", [], endpoint
        if r.status_code in (401, 403):
            return False, "Rejected by the provider — check the API key (HTTP %d)" % r.status_code, [], endpoint
        if r.status_code == 404:
            return False, f"No model catalogue at {endpoint} (HTTP 404) — check the base URL includes the API path, e.g. /v1", [], endpoint
        if r.status_code != 200:
            return False, f"HTTP {r.status_code} from {endpoint}: {r.text[:200]}", [], endpoint
        try:
            payload = r.json()
        except ValueError:
            return False, f"{endpoint} did not return JSON — is this an OpenAI-compatible endpoint?", [], endpoint
        return True, "", normalize_openai_models(payload, self.provider_name), endpoint

    def health_check(self) -> ProviderStatus:
        ok, err, models, endpoint = self._fetch_models()
        if not ok:
            return ProviderStatus(ok=False, provider=self.provider_name, message=err, discovery="none", endpoint=endpoint)
        total = len(models)
        chat = [m for m in models if not any(f in m.name.lower() for f in self._NON_CHAT_FRAGMENTS)]
        if chat:
            hidden = total - len(chat)
            msg = f"{len(chat)} chat model(s)" + (f" ({hidden} non-chat model(s) hidden)" if hidden else "")
            models = chat
        else:
            msg = f"{total} model(s)"
        return ProviderStatus(ok=True, provider=self.provider_name, message=msg, models=models, endpoint=endpoint)

    def list_models(self) -> list[ModelInfo]:
        status = self.health_check()
        return status.models if status.ok else []

    def analyze(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        stream: bool = False,
    ) -> AnalysisResult | Iterator[StreamChunk]:
        mdl = model or self.default_model
        payload: dict[str, Any] = {
            "model": mdl,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 4096,
            "response_format": {"type": "json_object"},
        }
        if stream:
            payload["stream"] = True
            return self._stream(payload, mdl)
        return self._generate(payload, mdl)

    def _generate(self, payload: dict, mdl: str) -> AnalysisResult:
        t0 = time.time()
        try:
            r = requests.post(f"{self.base_url}/chat/completions", headers=self._headers(), json=payload, timeout=self.timeout)
            elapsed = time.time() - t0
            if r.status_code != 200:
                return AnalysisResult(ok=False, error=f"OpenAI HTTP {r.status_code}: {r.text[:500]}", model=mdl, provider=self.provider_name, elapsed_seconds=elapsed)
            body = r.json()
            raw = body.get("choices", [{}])[0].get("message", {}).get("content", "")
            parsed = _extract_json_from_text(raw)
            if parsed is None:
                return AnalysisResult(ok=False, raw_text=raw, error="Failed to extract JSON from OpenAI response", model=mdl, provider=self.provider_name, elapsed_seconds=elapsed)
            return AnalysisResult(ok=True, data=parsed, raw_text=raw, model=mdl, provider=self.provider_name, elapsed_seconds=elapsed)
        except Exception as exc:
            return AnalysisResult(ok=False, error=str(exc), model=mdl, provider=self.provider_name, elapsed_seconds=time.time() - t0)

    def _stream(self, payload: dict, mdl: str) -> Iterator[StreamChunk]:
        try:
            r = requests.post(f"{self.base_url}/chat/completions", headers=self._headers(), json=payload, timeout=self.timeout, stream=True)
            if r.status_code != 200:
                yield StreamChunk(type="error", text=f"OpenAI HTTP {r.status_code}")
                return
            full_text = ""
            for line in r.iter_lines():
                if not line:
                    continue
                decoded = line.decode("utf-8", errors="replace")
                if decoded.startswith("data: "):
                    decoded = decoded[6:]
                if decoded.strip() == "[DONE]":
                    break
                try:
                    obj = json.loads(decoded)
                except json.JSONDecodeError:
                    continue
                delta = obj.get("choices", [{}])[0].get("delta", {})
                token = delta.get("content", "")
                if token:
                    full_text += token
                    yield StreamChunk(type="token", text=token)
            parsed = _extract_json_from_text(full_text)
            yield StreamChunk(type="done", text=full_text, data=parsed)
        except Exception as exc:
            yield StreamChunk(type="error", text=str(exc))


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

def _anthropic_rejects_temperature(model: str) -> bool:
    """True for Claude generations that refuse a `temperature` parameter.

    Claude 5 (and the Fable line) return HTTP 400 `temperature is deprecated for this model`
    rather than ignoring it, so the parameter has to be omitted per model instead of always
    sent. Unknown names are treated as new, because omitting temperature degrades gracefully
    while sending it is a hard failure.
    """
    name = (model or "").strip().lower()
    if not name:
        return True
    legacy_markers = ("claude-3", "claude-4", "-4-", "-3-")
    return not any(marker in name for marker in legacy_markers)


class AnthropicProvider(AIProvider):
    """Anthropic Messages API."""

    provider_name = "anthropic"

    def __init__(self, api_key: str, default_model: str = "claude-sonnet-4-20250514", timeout: int = 120):
        self.api_key = api_key
        self.default_model = default_model
        self.base_url = "https://api.anthropic.com/v1"
        self.timeout = max(10, timeout)

    def default_token_budget(self) -> int:
        return 50_000

    def _headers(self) -> dict:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    def _curated_models(self) -> list[ModelInfo]:
        """Maintained fallback list — always reported as ``discovery="curated"``."""
        try:
            from ai_config import CURATED_MODELS

            rows = CURATED_MODELS.get("anthropic") or []
        except Exception:
            rows = []
        return [
            ModelInfo(
                name=str(row.get("name", "")),
                provider=self.provider_name,
                context_window=_as_int(row.get("context_window")) or 200_000,
                description=str(row.get("description") or ""),
            )
            for row in rows
            if row.get("name")
        ]

    def health_check(self) -> ProviderStatus:
        """Prefer the real ``GET /v1/models`` catalogue; fall back to curated.

        Anthropic historically had no model-list endpoint.  Modern API versions
        do, so we probe it first and only fall back to the curated list when the
        probe fails — in which case the status says ``discovery="curated"`` so
        the screen can label it honestly.
        """
        endpoint = f"{self.base_url}/models"
        try:
            r = requests.get(endpoint, headers=self._headers(), params={"limit": 100}, timeout=15)
        except Exception as exc:
            return ProviderStatus(
                ok=False, provider=self.provider_name, discovery="none", endpoint=endpoint,
                message=f"{endpoint} unreachable: {describe_conn_error(exc)}",
            )
        if r.status_code in (401, 403):
            return ProviderStatus(
                ok=False, provider=self.provider_name, discovery="none", endpoint=endpoint,
                message=f"Rejected by Anthropic — check the API key (HTTP {r.status_code})",
            )
        if r.status_code == 200:
            try:
                rows = r.json().get("data") or []
            except ValueError:
                rows = []
            models = [
                ModelInfo(
                    name=str(row.get("id")),
                    provider=self.provider_name,
                    context_window=200_000,
                    label=str(row.get("display_name") or ""),
                )
                for row in rows
                if isinstance(row, dict) and row.get("id")
            ]
            models.sort(key=lambda m: m.name, reverse=True)
            if models:
                return ProviderStatus(
                    ok=True, provider=self.provider_name, endpoint=endpoint,
                    message=f"API key valid · {len(models)} model(s) from the Anthropic catalogue",
                    models=models,
                )
        # Endpoint unavailable on this account/API version — curated fallback.
        curated = self._curated_models()
        if not curated:
            return ProviderStatus(
                ok=False, provider=self.provider_name, discovery="none", endpoint=endpoint,
                message=f"HTTP {r.status_code} from {endpoint}: {r.text[:200]}",
            )
        return ProviderStatus(
            ok=True, provider=self.provider_name, discovery="curated", endpoint=endpoint,
            message=(
                f"Model catalogue endpoint unavailable (HTTP {r.status_code}) — "
                "showing a curated list, not a live probe"
            ),
            models=curated,
        )

    def list_models(self) -> list[ModelInfo]:
        status = self.health_check()
        return status.models if status.models else self._curated_models()

    def analyze(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        stream: bool = False,
    ) -> AnalysisResult | Iterator[StreamChunk]:
        mdl = model or self.default_model
        payload: dict[str, Any] = {
            "model": mdl,
            "max_tokens": 4096,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        # Newer Claude models reject `temperature` outright ("deprecated for this model",
        # HTTP 400), so it is sent only to the older generations that still accept it.
        if not _anthropic_rejects_temperature(mdl):
            payload["temperature"] = 0.1
        if stream:
            payload["stream"] = True
            return self._stream(payload, mdl)
        return self._generate(payload, mdl)

    def _generate(self, payload: dict, mdl: str) -> AnalysisResult:
        t0 = time.time()
        try:
            r = requests.post(f"{self.base_url}/messages", headers=self._headers(), json=payload, timeout=self.timeout)
            elapsed = time.time() - t0
            if r.status_code != 200:
                return AnalysisResult(ok=False, error=f"Anthropic HTTP {r.status_code}: {r.text[:500]}", model=mdl, provider=self.provider_name, elapsed_seconds=elapsed)
            body = r.json()
            raw = ""
            for block in body.get("content", []):
                if block.get("type") == "text":
                    raw += block.get("text", "")
            parsed = _extract_json_from_text(raw)
            if parsed is None:
                return AnalysisResult(ok=False, raw_text=raw, error="Failed to extract JSON from Anthropic response", model=mdl, provider=self.provider_name, elapsed_seconds=elapsed)
            return AnalysisResult(ok=True, data=parsed, raw_text=raw, model=mdl, provider=self.provider_name, elapsed_seconds=elapsed)
        except Exception as exc:
            return AnalysisResult(ok=False, error=str(exc), model=mdl, provider=self.provider_name, elapsed_seconds=time.time() - t0)

    def _stream(self, payload: dict, mdl: str) -> Iterator[StreamChunk]:
        try:
            r = requests.post(f"{self.base_url}/messages", headers=self._headers(), json=payload, timeout=self.timeout, stream=True)
            if r.status_code != 200:
                yield StreamChunk(type="error", text=f"Anthropic HTTP {r.status_code}")
                return
            full_text = ""
            for line in r.iter_lines():
                if not line:
                    continue
                decoded = line.decode("utf-8", errors="replace")
                if decoded.startswith("data: "):
                    decoded = decoded[6:]
                try:
                    obj = json.loads(decoded)
                except json.JSONDecodeError:
                    continue
                evt = obj.get("type", "")
                if evt == "content_block_delta":
                    token = obj.get("delta", {}).get("text", "")
                    if token:
                        full_text += token
                        yield StreamChunk(type="token", text=token)
                elif evt == "message_stop":
                    break
            parsed = _extract_json_from_text(full_text)
            yield StreamChunk(type="done", text=full_text, data=parsed)
        except Exception as exc:
            yield StreamChunk(type="error", text=str(exc))


# ---------------------------------------------------------------------------
# Google Gemini
# ---------------------------------------------------------------------------

class GoogleProvider(AIProvider):
    """Google Generative AI (Gemini) API."""

    provider_name = "google"

    def __init__(self, api_key: str, default_model: str = "gemini-2.0-flash", timeout: int = 120):
        self.api_key = api_key
        self.default_model = default_model
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"
        self.timeout = max(10, timeout)

    def default_token_budget(self) -> int:
        return 50_000

    def health_check(self) -> ProviderStatus:
        # The key travels as a query param per Google's API contract; the URL is
        # never echoed back to the browser or into a log line.
        endpoint = f"{self.base_url}/models"
        try:
            r = requests.get(endpoint, params={"key": self.api_key, "pageSize": 200}, timeout=15)
        except Exception as exc:
            return ProviderStatus(
                ok=False, provider=self.provider_name, discovery="none", endpoint=endpoint,
                message=f"{endpoint} unreachable: {describe_conn_error(exc)}",
            )
        if r.status_code in (400, 401, 403):
            return ProviderStatus(
                ok=False, provider=self.provider_name, discovery="none", endpoint=endpoint,
                message=f"Rejected by Google — check the API key (HTTP {r.status_code})",
            )
        if r.status_code != 200:
            return ProviderStatus(
                ok=False, provider=self.provider_name, discovery="none", endpoint=endpoint,
                message=f"HTTP {r.status_code} from {endpoint}",
            )
        try:
            rows = r.json().get("models", [])
        except ValueError:
            return ProviderStatus(
                ok=False, provider=self.provider_name, discovery="none", endpoint=endpoint,
                message=f"{endpoint} did not return JSON",
            )
        models: list[ModelInfo] = []
        for m in rows:
            if not isinstance(m, dict):
                continue
            name = str(m.get("name") or "").replace("models/", "")
            if not name:
                continue
            methods = m.get("supportedGenerationMethods") or []
            if methods and "generateContent" not in methods:
                continue
            models.append(
                ModelInfo(
                    name=name,
                    provider=self.provider_name,
                    context_window=_as_int(m.get("inputTokenLimit")),
                    description=str(m.get("description") or "").strip(),
                    label=str(m.get("displayName") or "").strip(),
                )
            )
        models.sort(key=lambda m: m.name)
        return ProviderStatus(
            ok=True, provider=self.provider_name, endpoint=endpoint,
            message=f"{len(models)} generateContent-capable model(s)", models=models,
        )

    def list_models(self) -> list[ModelInfo]:
        status = self.health_check()
        return status.models if status.ok else []

    def analyze(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        stream: bool = False,
    ) -> AnalysisResult | Iterator[StreamChunk]:
        mdl = model or self.default_model
        # Gemini uses system_instruction + contents
        payload: dict[str, Any] = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 4096,
                "responseMimeType": "application/json",
            },
        }
        # Gemini doesn't do streaming the same way — use non-stream for now
        return self._generate(payload, mdl)

    def _generate(self, payload: dict, mdl: str) -> AnalysisResult:
        t0 = time.time()
        url = f"{self.base_url}/models/{mdl}:generateContent?key={self.api_key}"
        try:
            r = requests.post(url, json=payload, timeout=self.timeout)
            elapsed = time.time() - t0
            if r.status_code != 200:
                return AnalysisResult(ok=False, error=f"Google HTTP {r.status_code}: {r.text[:500]}", model=mdl, provider=self.provider_name, elapsed_seconds=elapsed)
            body = r.json()
            raw = ""
            for cand in body.get("candidates", []):
                for part in cand.get("content", {}).get("parts", []):
                    raw += part.get("text", "")
            parsed = _extract_json_from_text(raw)
            if parsed is None:
                return AnalysisResult(ok=False, raw_text=raw, error="Failed to extract JSON from Google response", model=mdl, provider=self.provider_name, elapsed_seconds=elapsed)
            return AnalysisResult(ok=True, data=parsed, raw_text=raw, model=mdl, provider=self.provider_name, elapsed_seconds=elapsed)
        except Exception as exc:
            return AnalysisResult(ok=False, error=str(exc), model=mdl, provider=self.provider_name, elapsed_seconds=time.time() - t0)


# ---------------------------------------------------------------------------
# OpenAI-Compatible (vLLM, LM Studio, LocalAI, text-generation-webui)
# ---------------------------------------------------------------------------

class OpenAICompatibleProvider(OpenAIProvider):
    """Any endpoint that speaks the OpenAI chat completions format.

    This is the aggregator path: OpenRouter, Groq, Together, DeepInfra,
    Fireworks, LiteLLM, vLLM, LM Studio and friends all serve
    ``GET {base_url}/models``.  Their catalogues run to hundreds of entries, so
    the full list is returned (no silent cap) and nothing is filtered — the
    breadth is the reason the operator chose an aggregator.
    """

    provider_name = "openai-compatible"

    def __init__(self, base_url: str, api_key: str = "", default_model: str = "", timeout: int = 180, preset: str = ""):
        super().__init__(api_key=api_key or "", default_model=default_model, base_url=base_url, timeout=timeout)
        self.preset = preset or "custom"

    def _headers(self) -> dict:
        """Send Authorization only when a key is actually configured.

        Sending a placeholder bearer is worse than sending none: Eden AI's
        public /v3/models catalogue answers 200 unauthenticated but 401 to a
        bogus token, and several self-hosted gateways behave the same way.
        """
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def health_check(self) -> ProviderStatus:
        ok, err, models, endpoint = self._fetch_models()
        if not ok:
            return ProviderStatus(ok=False, provider=self.provider_name, message=err, discovery="none", endpoint=endpoint)
        if not models:
            return ProviderStatus(
                ok=True, provider=self.provider_name, endpoint=endpoint,
                message=f"Reachable, but {endpoint} listed no models — type a model id manually",
                discovery="none",
            )
        return ProviderStatus(
            ok=True, provider=self.provider_name, endpoint=endpoint,
            message=f"{len(models)} model(s) from {endpoint}", models=models,
        )


# ---------------------------------------------------------------------------
# Hybrid (SLM pre-summarize + LLM final analysis)
# ---------------------------------------------------------------------------

_SUMMARIZE_SYSTEM = (
    "You are a code summarizer. Given source files from a Node.js application, "
    "produce a concise technical summary covering: framework, entry points, "
    "config files and their exported keys, data stores, services/processes, "
    "ports, health endpoints, caching layers, and any special requirements "
    "(e.g. chromium, sharp). Be factual and terse. Output plain text."
)


class HybridProvider(AIProvider):
    """Two-stage provider: local SLM summarizes → cloud LLM analyzes.

    Benefits:
      - Speed: SLM runs locally with near-zero latency for summarization.
      - Accuracy: Cloud LLM does the structured JSON extraction.
      - Cost: Summarized context is ~3-5x smaller → fewer cloud tokens.
      - Privacy: Raw source stays local; only summaries go to cloud.
    """

    provider_name = "hybrid"

    def __init__(self, local: AIProvider, cloud: AIProvider):
        self.local = local
        self.cloud = cloud

    def default_token_budget(self) -> int:
        # Use local SLM's budget for collection (we'll summarize it down)
        return self.local.default_token_budget()

    def health_check(self) -> ProviderStatus:
        lstat = self.local.health_check()
        cstat = self.cloud.health_check()
        ok = lstat.ok and cstat.ok
        parts = []
        if lstat.ok:
            parts.append(f"Local ({self.local.provider_name}): OK")
        else:
            parts.append(f"Local ({self.local.provider_name}): {lstat.message}")
        if cstat.ok:
            parts.append(f"Cloud ({self.cloud.provider_name}): OK")
        else:
            parts.append(f"Cloud ({self.cloud.provider_name}): {cstat.message}")
        discovery = "curated" if "curated" in (lstat.discovery, cstat.discovery) else "api"
        return ProviderStatus(
            ok=ok,
            provider="hybrid",
            message=" | ".join(parts),
            models=lstat.models + cstat.models,
            discovery=discovery,
        )

    def list_models(self) -> list[ModelInfo]:
        return self.local.list_models() + self.cloud.list_models()

    def analyze(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        stream: bool = False,
    ) -> AnalysisResult | Iterator[StreamChunk]:
        if stream:
            return self._stream_hybrid(system_prompt, user_prompt)
        return self._sync_hybrid(system_prompt, user_prompt)

    # Minimum summary length to consider SLM output adequate
    _MIN_SUMMARY_LENGTH = 200

    def _slm_summary_ok(self, summary_result: AnalysisResult) -> bool:
        """Return True if the SLM summary is usable for the cloud LLM."""
        if not summary_result.ok:
            return False
        text = (summary_result.raw_text or "").strip()
        if len(text) < self._MIN_SUMMARY_LENGTH:
            return False
        return True

    def _sync_hybrid(self, system_prompt: str, user_prompt: str) -> AnalysisResult:
        """Phase 1: local SLM summarizes → Phase 2: cloud LLM analyzes.

        Falls back to sending original source files directly to the cloud
        LLM if the SLM fails or produces an inadequate summary.
        """
        t0 = time.time()

        # Phase 1: SLM summarization
        slm_failed = False
        summarized = ""
        try:
            summary_result = self.local.analyze(
                _SUMMARIZE_SYSTEM,
                user_prompt,
                stream=False,
            )
            if not isinstance(summary_result, AnalysisResult):
                slm_failed = True
            elif not self._slm_summary_ok(summary_result):
                slm_failed = True
                summarized = (summary_result.raw_text or "").strip()
            else:
                summarized = summary_result.raw_text
        except Exception:
            slm_failed = True

        # Phase 2: Cloud LLM analysis
        if slm_failed:
            # Fallback: send original source files directly to cloud LLM
            cloud_result = self.cloud.analyze(
                system_prompt,
                user_prompt,
                stream=False,
            )
        else:
            condensed_prompt = (
                "Below is a technical summary of an application's source files, "
                "pre-processed by a local model. Use this summary to produce "
                "the structured JSON analysis.\n\n"
                "--- APPLICATION SUMMARY ---\n"
                f"{summarized}\n"
                "--- END SUMMARY ---\n\n"
                "Now produce the JSON analysis following the schema in the system prompt."
            )
            cloud_result = self.cloud.analyze(
                system_prompt,
                condensed_prompt,
                stream=False,
            )

        if isinstance(cloud_result, AnalysisResult):
            cloud_result.provider = "hybrid"
            cloud_result.elapsed_seconds = time.time() - t0
            suffix = " (SLM fallback)" if slm_failed else ""
            cloud_result.model = f"{self.local.provider_name}→{self.cloud.provider_name}{suffix}"
        return cloud_result

    def _stream_hybrid(self, system_prompt: str, user_prompt: str) -> Iterator[StreamChunk]:
        """Streaming hybrid: yield progress from both phases.

        Falls back to sending original source files directly to the cloud
        LLM if the SLM fails or produces an inadequate summary.
        """
        # Phase 1: SLM summarization (non-streaming for simplicity)
        yield StreamChunk(type="token", text="[SLM summarizing...]\n")

        slm_failed = False
        summarized = ""
        try:
            summary_result = self.local.analyze(
                _SUMMARIZE_SYSTEM,
                user_prompt,
                stream=False,
            )
            if not isinstance(summary_result, AnalysisResult):
                slm_failed = True
                yield StreamChunk(
                    type="token",
                    text="[SLM returned unexpected response, falling back to direct cloud LLM...]\n",
                )
            elif not self._slm_summary_ok(summary_result):
                slm_failed = True
                raw_len = len((summary_result.raw_text or "").strip())
                reason = summary_result.error or f"inadequate summary ({raw_len} chars)"
                yield StreamChunk(
                    type="token",
                    text=f"[SLM {reason}, falling back to direct cloud LLM...]\n",
                )
            else:
                summarized = summary_result.raw_text
                yield StreamChunk(
                    type="token",
                    text=f"[SLM summary: {len(summarized)} chars, {summary_result.elapsed_seconds:.1f}s]\n"
                         f"[Sending to cloud LLM...]\n"
                )
        except Exception as exc:
            slm_failed = True
            yield StreamChunk(
                type="token",
                text=f"[SLM error: {exc}, falling back to direct cloud LLM...]\n",
            )

        # Phase 2: Cloud LLM analysis (streaming)
        if slm_failed:
            # Fallback: send original source files directly to cloud LLM
            cloud_prompt = user_prompt
        else:
            cloud_prompt = (
                "Below is a technical summary of an application's source files, "
                "pre-processed by a local model. Use this summary to produce "
                "the structured JSON analysis.\n\n"
                "--- APPLICATION SUMMARY ---\n"
                f"{summarized}\n"
                "--- END SUMMARY ---\n\n"
                "Now produce the JSON analysis following the schema in the system prompt."
            )

        cloud_response = self.cloud.analyze(
            system_prompt,
            cloud_prompt,
            stream=True,
        )

        if isinstance(cloud_response, AnalysisResult):
            # Cloud returned sync result
            if cloud_response.ok:
                yield StreamChunk(type="done", text=cloud_response.raw_text, data=cloud_response.data)
            else:
                yield StreamChunk(type="error", text=cloud_response.error)
        else:
            # Stream tokens from cloud
            for chunk in cloud_response:
                yield chunk


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_provider(config: dict) -> AIProvider | None:
    """Instantiate a provider from ai-providers.yaml config section.

    ``config`` must have at least ``{"provider": "ollama", ...}``.
    Returns None if provider is "none" or invalid.
    """
    name = config.get("provider", "none").lower().strip()
    if name == "none":
        return None

    providers_cfg = config.get("providers", {})
    pcfg = providers_cfg.get(name, {})
    # Global timeout fallback, then per-provider override
    global_timeout = config.get("timeout", 180)

    if name == "ollama":
        return OllamaProvider(
            base_url=pcfg.get("base_url", "http://ollama:11434"),
            default_model=pcfg.get("default_model", config.get("default_model", "qwen2.5-coder")),
            timeout=pcfg.get("timeout", global_timeout),
        )
    elif name == "airllm":
        return AirLLMProvider(
            base_url=pcfg.get("base_url", "http://airllm:11435"),
            default_model=pcfg.get("default_model", config.get("default_model", "Qwen/Qwen2.5-7B-Instruct")),
            timeout=pcfg.get("timeout", global_timeout),
        )
    elif name == "openai":
        key = pcfg.get("api_key", "")
        if not key:
            return None
        return OpenAIProvider(
            api_key=key,
            default_model=pcfg.get("default_model", config.get("default_model", "gpt-4o-mini")),
            timeout=pcfg.get("timeout", global_timeout),
        )
    elif name == "anthropic":
        key = pcfg.get("api_key", "")
        if not key:
            return None
        return AnthropicProvider(
            api_key=key,
            default_model=pcfg.get("default_model", config.get("default_model", "claude-sonnet-4-20250514")),
            timeout=pcfg.get("timeout", global_timeout),
        )
    elif name == "google":
        key = pcfg.get("api_key", "")
        if not key:
            return None
        return GoogleProvider(
            api_key=key,
            default_model=pcfg.get("default_model", config.get("default_model", "gemini-2.0-flash")),
            timeout=pcfg.get("timeout", global_timeout),
        )
    elif name == "openai-compatible":
        preset = str(pcfg.get("preset") or "").strip()
        base = str(pcfg.get("base_url") or "").strip()
        if not base and preset:
            # Presets are metadata only: they resolve a base URL, they do not
            # introduce a per-vendor provider class.
            try:
                from ai_config import resolve_preset

                base = resolve_preset(preset)
            except Exception:
                base = ""
        if not base:
            return None
        return OpenAICompatibleProvider(
            base_url=base,
            api_key=pcfg.get("api_key", ""),
            default_model=pcfg.get("default_model", config.get("default_model", "")),
            timeout=pcfg.get("timeout", global_timeout),
            preset=preset,
        )
    elif name == "hybrid":
        # Build both local and cloud providers from the hybrid config
        local_name = pcfg.get("local_provider", "ollama")
        cloud_name = pcfg.get("cloud_provider", "openai")
        local_cfg = dict(providers_cfg.get(local_name, {}))
        # Explicit over inherited: the sub-provider block usually already carries a
        # default_model, so setdefault silently discarded the hybrid's own choice and the
        # request went out with the wrong model name (e.g. an Anthropic call for a model
        # that account no longer serves → HTTP 404).
        if pcfg.get("local_model"):
            local_cfg["default_model"] = pcfg["local_model"]
        local_cfg.setdefault("default_model", pcfg.get("local_model", ""))
        # Apply hybrid-specific timeouts to sub-providers
        local_cfg.setdefault("timeout", pcfg.get("local_timeout", 300))
        cloud_cfg = dict(providers_cfg.get(cloud_name, {}))
        # Hybrid stores its own cloud_api_key for the cloud provider
        if pcfg.get("cloud_api_key"):
            cloud_cfg["api_key"] = pcfg["cloud_api_key"]
        if pcfg.get("cloud_model"):
            cloud_cfg["default_model"] = pcfg["cloud_model"]
        cloud_cfg.setdefault("default_model", pcfg.get("cloud_model", ""))
        cloud_cfg.setdefault("timeout", pcfg.get("cloud_timeout", 120))

        local_provider = create_provider({
            "provider": local_name,
            "default_model": pcfg.get("local_model", ""),
            "timeout": pcfg.get("local_timeout", 300),
            "providers": {local_name: local_cfg},
        })
        cloud_provider = create_provider({
            "provider": cloud_name,
            "default_model": pcfg.get("cloud_model", ""),
            "timeout": pcfg.get("cloud_timeout", 120),
            "providers": {cloud_name: cloud_cfg},
        })
        if local_provider is None or cloud_provider is None:
            return None
        return HybridProvider(local=local_provider, cloud=cloud_provider)
    return None
