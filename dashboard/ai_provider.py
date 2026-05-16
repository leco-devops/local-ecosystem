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

@dataclass
class ModelInfo:
    """Describes one model available on a provider."""
    name: str
    provider: str
    context_window: int = 32_768
    description: str = ""


@dataclass
class ProviderStatus:
    """Result of a health / connectivity check."""
    ok: bool
    provider: str
    message: str = ""
    models: list[ModelInfo] = field(default_factory=list)


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

    def default_token_budget(self) -> int:
        return 12_000

    def health_check(self) -> ProviderStatus:
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=8)
            if r.status_code != 200:
                return ProviderStatus(ok=False, provider=self.provider_name, message=f"HTTP {r.status_code}")
            models = [
                ModelInfo(name=m.get("name", "?"), provider=self.provider_name)
                for m in (r.json().get("models") or [])
            ]
            return ProviderStatus(ok=True, provider=self.provider_name, message=f"{len(models)} model(s) installed", models=models)
        except Exception as exc:
            return ProviderStatus(ok=False, provider=self.provider_name, message=str(exc))

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

    def health_check(self) -> ProviderStatus:
        try:
            r = requests.get(f"{self.base_url}/models", headers=self._headers(), timeout=10)
            if r.status_code == 401:
                return ProviderStatus(ok=False, provider=self.provider_name, message="Invalid API key")
            if r.status_code != 200:
                return ProviderStatus(ok=False, provider=self.provider_name, message=f"HTTP {r.status_code}")
            models_raw = r.json().get("data", [])
            models = [
                ModelInfo(name=m["id"], provider=self.provider_name)
                for m in models_raw
                if "gpt" in m.get("id", "") or "o1" in m.get("id", "") or "o3" in m.get("id", "")
            ]
            return ProviderStatus(ok=True, provider=self.provider_name, message=f"{len(models)} model(s)", models=models[:30])
        except Exception as exc:
            return ProviderStatus(ok=False, provider=self.provider_name, message=str(exc))

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

    def health_check(self) -> ProviderStatus:
        """Anthropic has no /models list — do a minimal completion test."""
        try:
            r = requests.post(
                f"{self.base_url}/messages",
                headers=self._headers(),
                json={
                    "model": self.default_model,
                    "max_tokens": 5,
                    "messages": [{"role": "user", "content": "ping"}],
                },
                timeout=15,
            )
            if r.status_code == 401:
                return ProviderStatus(ok=False, provider=self.provider_name, message="Invalid API key")
            if r.status_code in (200, 429):
                # 429 = rate-limited but key is valid
                models = [
                    ModelInfo(name="claude-opus-4-20250514", provider=self.provider_name, context_window=200_000),
                    ModelInfo(name="claude-sonnet-4-20250514", provider=self.provider_name, context_window=200_000),
                    ModelInfo(name="claude-haiku-4-20250414", provider=self.provider_name, context_window=200_000),
                ]
                return ProviderStatus(ok=True, provider=self.provider_name, message="API key valid", models=models)
            return ProviderStatus(ok=False, provider=self.provider_name, message=f"HTTP {r.status_code}: {r.text[:200]}")
        except Exception as exc:
            return ProviderStatus(ok=False, provider=self.provider_name, message=str(exc))

    def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(name="claude-opus-4-20250514", provider=self.provider_name, context_window=200_000),
            ModelInfo(name="claude-sonnet-4-20250514", provider=self.provider_name, context_window=200_000),
            ModelInfo(name="claude-haiku-4-20250414", provider=self.provider_name, context_window=200_000),
        ]

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
            "temperature": 0.1,
        }
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
        try:
            r = requests.get(f"{self.base_url}/models?key={self.api_key}", timeout=10)
            if r.status_code == 400 or r.status_code == 403:
                return ProviderStatus(ok=False, provider=self.provider_name, message="Invalid API key")
            if r.status_code != 200:
                return ProviderStatus(ok=False, provider=self.provider_name, message=f"HTTP {r.status_code}")
            models_raw = r.json().get("models", [])
            models = [
                ModelInfo(name=m.get("name", "").replace("models/", ""), provider=self.provider_name)
                for m in models_raw
                if "gemini" in m.get("name", "")
            ]
            return ProviderStatus(ok=True, provider=self.provider_name, message=f"{len(models)} model(s)", models=models[:20])
        except Exception as exc:
            return ProviderStatus(ok=False, provider=self.provider_name, message=str(exc))

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
    """Any endpoint that speaks the OpenAI chat completions format."""

    provider_name = "openai-compatible"

    def __init__(self, base_url: str, api_key: str = "", default_model: str = "", timeout: int = 180):
        super().__init__(api_key=api_key or "no-key", default_model=default_model, base_url=base_url, timeout=timeout)

    def health_check(self) -> ProviderStatus:
        try:
            r = requests.get(f"{self.base_url}/models", headers=self._headers(), timeout=10)
            if r.status_code != 200:
                return ProviderStatus(ok=False, provider=self.provider_name, message=f"HTTP {r.status_code}")
            models_raw = r.json().get("data", [])
            models = [
                ModelInfo(name=m.get("id", "?"), provider=self.provider_name)
                for m in models_raw
            ]
            return ProviderStatus(ok=True, provider=self.provider_name, message=f"{len(models)} model(s)", models=models[:30])
        except Exception as exc:
            return ProviderStatus(ok=False, provider=self.provider_name, message=str(exc))


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
        return ProviderStatus(
            ok=ok,
            provider="hybrid",
            message=" | ".join(parts),
            models=lstat.models + cstat.models,
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
        base = pcfg.get("base_url", "")
        if not base:
            return None
        return OpenAICompatibleProvider(
            base_url=base,
            api_key=pcfg.get("api_key", ""),
            default_model=pcfg.get("default_model", config.get("default_model", "")),
            timeout=pcfg.get("timeout", global_timeout),
        )
    elif name == "hybrid":
        # Build both local and cloud providers from the hybrid config
        local_name = pcfg.get("local_provider", "ollama")
        cloud_name = pcfg.get("cloud_provider", "openai")
        local_cfg = dict(providers_cfg.get(local_name, {}))
        local_cfg.setdefault("default_model", pcfg.get("local_model", ""))
        # Apply hybrid-specific timeouts to sub-providers
        local_cfg.setdefault("timeout", pcfg.get("local_timeout", 300))
        cloud_cfg = dict(providers_cfg.get(cloud_name, {}))
        # Hybrid stores its own cloud_api_key for the cloud provider
        if pcfg.get("cloud_api_key"):
            cloud_cfg["api_key"] = pcfg["cloud_api_key"]
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
