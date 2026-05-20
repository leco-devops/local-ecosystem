"""
AI-assisted onboarding orchestrator.

Ties together the 3-phase pipeline:
  1. Collect — smart file reading from app directory
  2. Analyze — single LLM call for structured JSON extraction
  3. Generate — deterministic Python templates produce LEco config files

Supports both synchronous (returns final result) and streaming (NDJSON
events for the dashboard) modes.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from ai_config import get_provider_config
from ai_file_collector import CollectedContext, collect_app_context
from ai_prompts import SYSTEM_PROMPT, build_analysis_prompt
from ai_provider import AIProvider, AnalysisResult, StreamChunk, create_provider
from ai_template_generator import generate_from_analysis


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class OnboardingResult:
    """Full result of the AI onboarding pipeline."""
    ok: bool
    phase: str = ""                         # last completed phase
    error: str = ""
    # Phase 1: collect
    files_collected: int = 0
    tokens_used: int = 0
    files_skipped: list[str] = field(default_factory=list)
    # Phase 2: analyze
    analysis: dict[str, Any] = field(default_factory=dict)
    raw_ai_text: str = ""
    model: str = ""
    provider: str = ""
    ai_elapsed: float = 0.0
    # Phase 3: generate
    generated_files: dict[str, str] = field(default_factory=dict)
    # Timing
    total_elapsed: float = 0.0


@dataclass
class StreamEvent:
    """One NDJSON event for the dashboard stream."""
    type: str       # "phase" | "log" | "progress" | "ai_token" | "file" | "done" | "error"
    text: str = ""
    data: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"type": self.type}
        if self.text:
            d["text"] = self.text
        if self.data is not None:
            d["data"] = self.data
        return d


# ---------------------------------------------------------------------------
# Synchronous pipeline
# ---------------------------------------------------------------------------

def run_onboarding(
    app_path: str,
    slug: str,
    source_path: str = ".",
    *,
    health_path: str | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
) -> OnboardingResult:
    """Run the full collect → analyze → generate pipeline synchronously.

    Returns an OnboardingResult with all phases filled in.
    """
    t0 = time.time()

    # --- Resolve provider ---
    cfg = get_provider_config()
    if provider_override:
        cfg["provider"] = provider_override
    provider = create_provider(cfg)
    if provider is None:
        return OnboardingResult(
            ok=False,
            error="No AI provider configured. Set a provider in AI Settings.",
            total_elapsed=time.time() - t0,
        )

    # --- Phase 1: Collect ---
    budget = provider.default_token_budget()
    ctx = collect_app_context(app_path, token_budget=budget)
    if not ctx.files:
        return OnboardingResult(
            ok=False,
            phase="collect",
            error=f"No readable source files found in {app_path}",
            total_elapsed=time.time() - t0,
        )

    # --- Phase 2: Analyze ---
    files_for_prompt = [
        {"name": f.name, "content": f.content, "lines": f.lines, "truncated": f.truncated}
        for f in ctx.files
    ]
    user_prompt = build_analysis_prompt(files_for_prompt)

    result: AnalysisResult = provider.analyze(
        SYSTEM_PROMPT,
        user_prompt,
        model=model_override,
        stream=False,
    )
    if not isinstance(result, AnalysisResult):
        return OnboardingResult(
            ok=False,
            phase="analyze",
            error="Unexpected streaming response in synchronous mode",
            files_collected=len(ctx.files),
            tokens_used=ctx.total_tokens,
            total_elapsed=time.time() - t0,
        )
    if not result.ok:
        return OnboardingResult(
            ok=False,
            phase="analyze",
            error=result.error or "AI analysis failed",
            files_collected=len(ctx.files),
            tokens_used=ctx.total_tokens,
            raw_ai_text=result.raw_text,
            model=result.model,
            provider=result.provider,
            ai_elapsed=result.elapsed_seconds,
            total_elapsed=time.time() - t0,
        )

    analysis = result.data

    # --- Phase 3: Generate ---
    try:
        generated = generate_from_analysis(
            analysis, slug, source_path, health_path=health_path,
        )
    except Exception as exc:
        return OnboardingResult(
            ok=False,
            phase="generate",
            error=f"Template generation failed: {exc}",
            files_collected=len(ctx.files),
            tokens_used=ctx.total_tokens,
            analysis=analysis,
            raw_ai_text=result.raw_text,
            model=result.model,
            provider=result.provider,
            ai_elapsed=result.elapsed_seconds,
            total_elapsed=time.time() - t0,
        )

    return OnboardingResult(
        ok=True,
        phase="generate",
        files_collected=len(ctx.files),
        tokens_used=ctx.total_tokens,
        files_skipped=ctx.skipped,
        analysis=analysis,
        raw_ai_text=result.raw_text,
        model=result.model,
        provider=result.provider,
        ai_elapsed=result.elapsed_seconds,
        generated_files=generated,
        total_elapsed=time.time() - t0,
    )


# ---------------------------------------------------------------------------
# Streaming pipeline (NDJSON events for the dashboard)
# ---------------------------------------------------------------------------

def stream_onboarding(
    app_path: str,
    slug: str,
    source_path: str = ".",
    *,
    health_path: str | None = None,
    provider_override: str | None = None,
    model_override: str | None = None,
) -> Iterator[StreamEvent]:
    """Yield StreamEvent objects for each stage of the pipeline.

    The dashboard serialises these as NDJSON lines.
    """
    t0 = time.time()

    # --- Resolve provider ---
    yield StreamEvent(type="phase", text="Initializing AI provider...")
    cfg = get_provider_config()
    if provider_override:
        cfg["provider"] = provider_override
    provider = create_provider(cfg)
    if provider is None:
        pname = cfg.get("provider", "none")
        if pname == "hybrid":
            msg = ("Hybrid provider failed to initialize. "
                   "Check that Ollama is running and the cloud API key is set in AI Settings.")
        elif pname == "none":
            msg = "No AI provider configured. Set a provider in AI Settings."
        else:
            msg = f"Provider '{pname}' failed to initialize. Check API key or connectivity in AI Settings."
        yield StreamEvent(type="error", text=msg)
        yield StreamEvent(
            type="done",
            data={"ok": False, "error": msg},
        )
        return

    timeout_str = ""
    if hasattr(provider, "timeout"):
        timeout_str = f" | Timeout: {provider.timeout}s"
    elif hasattr(provider, "local") and hasattr(provider.local, "timeout"):
        timeout_str = f" | Timeout: local {provider.local.timeout}s, cloud {provider.cloud.timeout}s"
    yield StreamEvent(
        type="log",
        text=f"Provider: {cfg['provider']} | Token budget: {provider.default_token_budget():,}{timeout_str}",
    )

    # --- Phase 1: Collect ---
    yield StreamEvent(type="phase", text="Phase 1/3: Collecting source files...")
    budget = provider.default_token_budget()
    ctx = collect_app_context(app_path, token_budget=budget)

    if not ctx.files:
        yield StreamEvent(
            type="error",
            text=f"No readable source files found in {app_path}",
        )
        return

    yield StreamEvent(
        type="progress",
        text=f"Collected {len(ctx.files)} files ({ctx.total_tokens:,} estimated tokens)",
        data={
            "files": [f.name for f in ctx.files],
            "tokens": ctx.total_tokens,
            "budget": budget,
            "skipped": ctx.skipped,
        },
    )

    for f in ctx.files:
        trunc = " [truncated]" if f.truncated else ""
        yield StreamEvent(type="log", text=f"  → {f.name} ({f.lines} lines, ~{f.tokens_est} tokens{trunc})")

    # --- Phase 2: Analyze ---
    yield StreamEvent(type="phase", text="Phase 2/3: AI analysis in progress...")

    files_for_prompt = [
        {"name": f.name, "content": f.content, "lines": f.lines, "truncated": f.truncated}
        for f in ctx.files
    ]
    user_prompt = build_analysis_prompt(files_for_prompt)

    # Try streaming first
    ai_t0 = time.time()
    response = provider.analyze(
        SYSTEM_PROMPT,
        user_prompt,
        model=model_override,
        stream=True,
    )

    analysis: dict[str, Any] | None = None
    raw_text = ""
    model_used = model_override or ""
    provider_name = cfg["provider"]

    if isinstance(response, AnalysisResult):
        # Provider returned synchronous result (e.g. Google Gemini)
        ai_elapsed = time.time() - ai_t0
        if not response.ok:
            yield StreamEvent(type="error", text=f"AI analysis failed: {response.error}")
            return
        analysis = response.data
        raw_text = response.raw_text
        model_used = response.model
        yield StreamEvent(
            type="log",
            text=f"AI analysis complete ({ai_elapsed:.1f}s, model: {response.model})",
        )
    else:
        # Streaming — yield tokens as they arrive
        for chunk in response:
            if chunk.type == "token":
                yield StreamEvent(type="ai_token", text=chunk.text)
            elif chunk.type == "done":
                ai_elapsed = time.time() - ai_t0
                raw_text = chunk.text
                analysis = chunk.data
                yield StreamEvent(
                    type="log",
                    text=f"AI analysis complete ({ai_elapsed:.1f}s)",
                )
            elif chunk.type == "error":
                yield StreamEvent(type="error", text=f"AI stream error: {chunk.text}")
                return

    if analysis is None:
        yield StreamEvent(type="error", text="Failed to extract structured JSON from AI response")
        yield StreamEvent(
            type="done",
            data={
                "ok": False,
                "error": "JSON extraction failed",
                "raw_ai_text": raw_text[:2000],
            },
        )
        return

    yield StreamEvent(
        type="progress",
        text="Analysis extracted successfully",
        data={
            "app_name": analysis.get("app_name", slug),
            "services": len(analysis.get("services", [])),
            "data_stores": analysis.get("data_stores", []),
            "cache_layer": analysis.get("cache_layer"),
        },
    )

    # --- Phase 3: Generate ---
    yield StreamEvent(type="phase", text="Phase 3/3: Generating LEco config files...")

    try:
        generated = generate_from_analysis(
            analysis, slug, source_path, health_path=health_path,
        )
    except Exception as exc:
        yield StreamEvent(type="error", text=f"Template generation failed: {exc}")
        return

    for fname in generated:
        yield StreamEvent(
            type="file",
            text=fname,
            data={"name": fname, "size": len(generated[fname])},
        )

    total_elapsed = time.time() - t0

    yield StreamEvent(
        type="done",
        text=f"Generated {len(generated)} files in {total_elapsed:.1f}s",
        data={
            "ok": True,
            "files_collected": len(ctx.files),
            "tokens_used": ctx.total_tokens,
            "analysis": analysis,
            "model": model_used,
            "provider": provider_name,
            "ai_elapsed": ai_elapsed,
            "generated_files": generated,
            "total_elapsed": total_elapsed,
        },
    )


# ---------------------------------------------------------------------------
# Write generated files to disk
# ---------------------------------------------------------------------------

def write_generated_files(
    generated: dict[str, str],
    target_dir: str | Path,
    *,
    dry_run: bool = False,
) -> list[dict[str, str]]:
    """Write generated config files into target_dir.

    Returns a list of dicts with keys: path, action ("created" | "overwritten" | "dry_run").
    Creates subdirectories (e.g. conf/varnish/) as needed.
    """
    root = Path(target_dir).resolve()
    results: list[dict[str, str]] = []

    for fname, content in generated.items():
        fp = root / fname
        action = "dry_run"
        if not dry_run:
            fp.parent.mkdir(parents=True, exist_ok=True)
            action = "overwritten" if fp.exists() else "created"
            fp.write_text(content, encoding="utf-8")
        results.append({"path": str(fp), "name": fname, "action": action})

    return results
