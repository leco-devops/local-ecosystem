#!/usr/bin/env python3
"""
AirLLM Ollama-Compatible HTTP Shim Server

Exposes an Ollama-compatible API at http://localhost:11435 that:
- Lists installed HF models (GET /api/tags)
- Shows loaded model (GET /api/ps)
- Pulls models from HuggingFace (POST /api/pull)
- Generates text via AirLLM (POST /api/generate, /api/chat)
- Streams NDJSON like Ollama for UI compatibility

Environment variables:
  AIRLLM_PORT               Server port (default: 11435)
  AIRLLM_HF_HOME          HuggingFace cache dir (default: ~/.local-eco-airllm/hf-cache)
  AIRLLM_LAYER_SHARDS_DIR AirLLM layer shards dir (default: ~/.local-eco-airllm/shards)
  AIRLLM_COMPRESSION      Default compression: none, 4bit, 8bit
  HF_TOKEN                HuggingFace token for gated models
  AIRLLM_KEEP_ALIVE       Default seconds to keep model in memory (default: 300)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

try:
    from airllm import AutoModel
except ImportError as exc:
    print(f"ERROR: airllm not installed: {exc}", file=sys.stderr)
    print("Run: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

# Environment configuration
PORT = int(os.getenv("AIRLLM_PORT", "11435"))
HF_HOME = Path(os.getenv("AIRLLM_HF_HOME", Path.home() / ".local-eco-airllm" / "hf-cache"))
LAYER_SHARDS_DIR = Path(os.getenv("AIRLLM_LAYER_SHARDS_DIR", Path.home() / ".local-eco-airllm" / "shards"))
DEFAULT_COMPRESSION = os.getenv("AIRLLM_COMPRESSION", "none").lower()
HF_TOKEN = os.getenv("HF_TOKEN", "")
KEEP_ALIVE_DEFAULT = int(os.getenv("AIRLLM_KEEP_ALIVE", "300"))

# Ensure directories exist
HF_HOME.mkdir(parents=True, exist_ok=True)
LAYER_SHARDS_DIR.mkdir(parents=True, exist_ok=True)

# Set HF cache env so huggingface_hub uses it
os.environ["HF_HOME"] = str(HF_HOME)
if HF_TOKEN:
    os.environ["HF_TOKEN"] = HF_TOKEN

# Global model state
@dataclass
class ModelState:
    model_id: str | None = None
    model: Any | None = None
    compression: str = "none"
    loaded_at: float = 0.0
    last_used: float = 0.0
    lock: threading.RLock = field(default_factory=threading.RLock)
    keep_alive_seconds: int = KEEP_ALIVE_DEFAULT

_state = ModelState()

# Active pull tracking
_active_pulls: dict[str, dict] = {}
_pull_lock = threading.Lock()


def _sanitize_model_id(model_id: str) -> str:
    """Sanitize model ID for filesystem use."""
    return re.sub(r"[^a-zA-Z0-9_\-\./]", "_", model_id)


def _model_shard_path(model_id: str) -> Path:
    """Get the AirLLM shard path for a model."""
    return LAYER_SHARDS_DIR / _sanitize_model_id(model_id)


def _list_cached_models() -> list[dict]:
    """List models that have been downloaded (have HF cache entries)."""
    from huggingface_hub import scan_cache_dir

    models = []
    try:
        cache_info = scan_cache_dir(str(HF_HOME))
        seen_repos: set[str] = set()
        for repo in cache_info.repos:
            repo_id = repo.repo_id
            if repo_id in seen_repos:
                continue
            seen_repos.add(repo_id)

            # Check if this repo has AirLLM shards
            shard_path = _model_shard_path(repo_id)
            has_shards = shard_path.exists() and any(shard_path.iterdir())

            # Get total size from cache
            size_bytes = sum(r.size_on_disk for r in repo.revisions)

            models.append({
                "name": repo_id,
                "model": repo_id,
                "modified_at": datetime.fromtimestamp(repo.last_accessed, tz=timezone.utc).isoformat(),
                "size": size_bytes,
                "details": {
                    "family": "airllm",
                    "format": "safetensors",
                    "parameter_size": "",
                    "quantization_level": "",
                },
                "airllm_ready": has_shards,
            })
    except Exception as exc:
        print(f"Warning: could not scan cache: {exc}", file=sys.stderr)

    return models


def _get_loaded_model_info() -> dict | None:
    """Get info about currently loaded model."""
    with _state.lock:
        if _state.model is None or _state.model_id is None:
            return None
        return {
            "name": _state.model_id,
            "model": _state.model_id,
            "size": 0,
            "digest": "",
            "expires_at": datetime.fromtimestamp(
                _state.last_used + _state.keep_alive_seconds, tz=timezone.utc
            ).isoformat(),
            "size_vram": 0,
            "details": {
                "family": "airllm",
                "format": "safetensors",
                "parameter_size": "",
                "quantization_level": _state.compression,
            },
        }


def _unload_model() -> bool:
    """Unload the currently loaded model to free memory."""
    with _state.lock:
        if _state.model is not None:
            try:
                # AirLLM doesn't have explicit unload; rely on GC
                import gc

                del _state.model
                gc.collect()
            except Exception:
                pass
            _state.model = None
            _state.model_id = None
            _state.compression = "none"
            return True
        return False


def _load_model(model_id: str, compression: str = "none") -> Any:
    """Load a model with AirLLM, using layer shards if available."""
    global _state

    with _state.lock:
        # If already loaded, check if same model
        if _state.model is not None and _state.model_id == model_id:
            _state.last_used = time.time()
            return _state.model

        # Unload existing model first
        if _state.model is not None:
            _unload_model()

        # Prepare compression arg
        comp_arg = None
        if compression in ("4bit", "8bit"):
            comp_arg = compression

        shard_path = _model_shard_path(model_id)

        try:
            model = AutoModel.from_pretrained(
                model_id,
                compression=comp_arg,
                layer_shards_saving_path=str(shard_path) if shard_path.exists() else None,
            )
            _state.model = model
            _state.model_id = model_id
            _state.compression = compression
            _state.loaded_at = time.time()
            _state.last_used = time.time()
            return model
        except Exception as exc:
            raise RuntimeError(f"Failed to load model {model_id}: {exc}") from exc


def _ensure_model_loaded(model_id: str, compression: str = "none") -> Any:
    """Ensure the model is loaded, loading if necessary."""
    with _state.lock:
        if _state.model is not None and _state.model_id == model_id:
            _state.last_used = time.time()
            return _state.model
    return _load_model(model_id, compression)


# Pydantic models for API
class PullRequest(BaseModel):
    name: str = Field(..., description="HuggingFace model ID to pull")
    insecure: bool | None = None
    stream: bool = True


class GenerateRequest(BaseModel):
    model: str = Field(..., description="Model ID to use")
    prompt: str = Field(default="", description="Prompt text")
    system: str | None = None
    template: str | None = None
    context: list[int] | None = None
    stream: bool = True
    raw: bool = False
    format: str | None = None  # "json" or None
    options: dict[str, Any] = Field(default_factory=dict)
    keep_alive: int | str | None = None


class ChatMessage(BaseModel):
    role: str
    content: str
    images: list[str] | None = None


class ChatRequest(BaseModel):
    model: str = Field(..., description="Model ID to use")
    messages: list[ChatMessage] = Field(default_factory=list)
    stream: bool = True
    format: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    keep_alive: int | str | None = None


class ShowRequest(BaseModel):
    name: str = Field(..., description="Model name to show")


class DeleteRequest(BaseModel):
    name: str = Field(..., description="Model name to delete")


# Lifespan context manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Server lifespan management."""
    print(f"AirLLM Shim Server starting on port {PORT}", file=sys.stderr)
    print(f"HF_HOME: {HF_HOME}", file=sys.stderr)
    print(f"LAYER_SHARDS_DIR: {LAYER_SHARDS_DIR}", file=sys.stderr)
    yield
    # Shutdown: unload model
    _unload_model()
    print("AirLLM Shim Server shutting down", file=sys.stderr)


app = FastAPI(
    title="AirLLM Ollama-Compatible Shim",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/", response_class=HTMLResponse)
async def root():
    """Browser-friendly landing page (Ollama UIs hit /api/*; bare / used to 404)."""
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>AirLLM shim</title></head>"
        "<body style='font-family:system-ui,sans-serif;max-width:40rem;margin:2rem'>"
        "<h1>AirLLM Ollama-compatible shim</h1>"
        "<p>This service exposes the Ollama HTTP API. Useful paths:</p>"
        "<ul>"
        "<li><a href='/health'><code>/health</code></a> — liveness</li>"
        "<li><a href='/api/tags'><code>/api/tags</code></a> — cached HF models</li>"
        "<li><a href='/api/version'><code>/api/version</code></a> — versions</li>"
        "</ul>"
        "</body></html>"
    )


@app.get("/api/tags")
async def api_tags():
    """List installed models."""
    models = _list_cached_models()
    return {"models": models}


@app.get("/api/ps")
async def api_ps():
    """Show currently running/loaded model."""
    loaded = _get_loaded_model_info()
    models = [loaded] if loaded else []
    return {"models": models}


@app.get("/api/version")
async def api_version():
    """Return version info."""
    import airllm

    return {
        "version": "1.0.0",
        "airllm_version": getattr(airllm, "__version__", "unknown"),
    }


@app.post("/api/show")
async def api_show(req: ShowRequest):
    """Show model details."""
    model_id = req.name
    shard_path = _model_shard_path(model_id)

    # Check if in cache
    cached_models = _list_cached_models()
    model_info = next((m for m in cached_models if m["name"] == model_id), None)

    if model_info is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"model '{model_id}' not found"},
        )

    return {
        "name": model_id,
        "details": model_info.get("details", {}),
        "capabilities": {
            "vision": False,
            "embedding": False,
        },
        "modelfile": f"FROM {model_id}\n",
        "parameters": "",
        "template": "",
        "airllm_shards_ready": model_info.get("airllm_ready", False),
    }


async def _pull_stream(model_id: str) -> AsyncIterator[str]:
    """Stream NDJSON progress for model pull."""
    from huggingface_hub import snapshot_download

    status = {"status": "pulling", "name": model_id}

    def update_status(key: str, val: Any):
        status[key] = val

    try:
        yield json.dumps({"status": "pulling manifest", "name": model_id}) + "\n"

        # Download the model
        update_status("status", "downloading")
        yield json.dumps(dict(status)) + "\n"

        snapshot_path = snapshot_download(
            repo_id=model_id,
            cache_dir=str(HF_HOME),
            token=HF_TOKEN if HF_TOKEN else None,
        )

        update_status("status", "preparing shards")
        update_status("snapshot_path", snapshot_path)
        yield json.dumps(dict(status)) + "\n"

        # AirLLM will create shards on first load
        # Optionally pre-create them by doing a minimal load/unload
        shard_path = _model_shard_path(model_id)
        shard_path.mkdir(parents=True, exist_ok=True)

        update_status("status", "success")
        update_status("completed", True)
        yield json.dumps(dict(status)) + "\n"

    except Exception as exc:
        yield json.dumps({"status": "error", "error": str(exc), "name": model_id}) + "\n"


@app.post("/api/pull")
async def api_pull(req: PullRequest):
    """Pull a model from HuggingFace."""
    if req.stream:
        return StreamingResponse(
            _pull_stream(req.name),
            media_type="application/x-ndjson",
        )
    else:
        # Non-streaming: collect all lines
        lines = []
        async for line in _pull_stream(req.name):
            lines.append(line)
        return Response(content="".join(lines), media_type="application/x-ndjson")


@app.delete("/api/delete")
async def api_delete(req: DeleteRequest):
    """Delete a model (remove from HF cache and AirLLM shards)."""
    model_id = req.name

    # Unload if currently loaded
    with _state.lock:
        if _state.model_id == model_id:
            _unload_model()

    # Remove from HF cache
    from huggingface_hub import scan_cache_dir

    try:
        cache_info = scan_cache_dir(str(HF_HOME))
        deleted = False
        for repo in cache_info.repos:
            if repo.repo_id == model_id:
                for revision in repo.revisions:
                    revision_path = Path(revision.snapshot_path)
                    if revision_path.exists():
                        import shutil

                        shutil.rmtree(revision_path, ignore_errors=True)
                deleted = True
                break

        # Remove AirLLM shards
        shard_path = _model_shard_path(model_id)
        if shard_path.exists():
            import shutil

            shutil.rmtree(shard_path, ignore_errors=True)

        if deleted or shard_path.exists():
            return {"deleted": True}
        else:
            return JSONResponse(
                status_code=404,
                content={"error": f"model '{model_id}' not found"},
            )
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": str(exc)},
        )


def _generate_stream(
    model_id: str,
    prompt: str,
    system: str | None,
    options: dict[str, Any],
    format_json: bool,
    keep_alive: int | str | None,
    compression: str,
) -> Iterator[str]:
    """Generate text and yield NDJSON lines."""
    try:
        # Load or ensure model is loaded
        model = _ensure_model_loaded(model_id, compression)

        # Set keep_alive
        if keep_alive is not None:
            if isinstance(keep_alive, str) and keep_alive.lower() == "-1":
                _state.keep_alive_seconds = -1
            else:
                _state.keep_alive_seconds = int(keep_alive)

        # Prepare input
        full_prompt = ""
        if system:
            full_prompt += f"{system}\n\n"
        full_prompt += prompt

        # Get max tokens from options
        max_new_tokens = options.get("num_predict", options.get("max_tokens", 256))

        # Tokenize
        input_tokens = model.tokenizer(
            full_prompt,
            return_tensors="pt",
            return_attention_mask=False,
            truncation=True,
            max_length=4096,
            padding=False,
        )

        # Determine device
        device = "cpu"
        try:
            import torch
            if torch.cuda.is_available():
                device = "cuda"
        except Exception:
            pass

        # Move to device
        input_ids = input_tokens["input_ids"]
        if device == "cuda":
            input_ids = input_ids.cuda()

        # Generate with AirLLM
        # Note: AirLLM's generate returns a GenerationOutput-like object
        generation_output = model.generate(
            input_ids,
            max_new_tokens=max_new_tokens,
            use_cache=True,
            return_dict_in_generate=True,
        )

        # Decode response
        output_text = model.tokenizer.decode(generation_output.sequences[0])

        # If there was a prompt prefix, strip it
        if output_text.startswith(full_prompt):
            output_text = output_text[len(full_prompt):]

        # Stream word-by-word to simulate Ollama streaming
        words = output_text.split()
        accumulated = ""

        for word in words:
            accumulated += word + " "
            yield json.dumps({"response": word + " ", "done": False}) + "\n"

        # Final response
        result: dict[str, Any] = {"response": "", "done": True}

        if format_json:
            # Try to extract JSON
            try:
                result["response"] = json.loads(accumulated.strip())
            except json.JSONDecodeError:
                result["response"] = accumulated.strip()
        else:
            result["response"] = accumulated.strip()

        # Add usage stats (approximate)
        result["prompt_eval_count"] = input_ids.shape[1]
        result["eval_count"] = len(words)

        yield json.dumps(result) + "\n"

    except Exception as exc:
        yield json.dumps({"error": str(exc), "done": True}) + "\n"


@app.post("/api/generate")
async def api_generate(req: GenerateRequest):
    """Generate text from a prompt."""
    model_id = req.model
    compression = req.options.get("compression", DEFAULT_COMPRESSION)
    format_json = req.format == "json"

    if req.stream:
        return StreamingResponse(
            _generate_stream(
                model_id=model_id,
                prompt=req.prompt,
                system=req.system,
                options=req.options,
                format_json=format_json,
                keep_alive=req.keep_alive,
                compression=compression,
            ),
            media_type="application/x-ndjson",
        )
    else:
        # Non-streaming: collect all
        full_text = ""
        final_data: dict[str, Any] = {"done": True}

        for line in _generate_stream(
            model_id=model_id,
            prompt=req.prompt,
            system=req.system,
            options=req.options,
            format_json=format_json,
            keep_alive=req.keep_alive,
            compression=compression,
        ):
            data = json.loads(line)
            if data.get("done"):
                final_data = data
            elif "response" in data and isinstance(data["response"], str):
                full_text += data["response"]

        return {
            "model": model_id,
            "response": full_text,
            "done": True,
            **{k: v for k, v in final_data.items() if k not in ("response", "done")},
        }


def _flatten_messages(messages: list[ChatMessage]) -> str:
    """Convert chat messages to a single prompt string."""
    parts = []
    for msg in messages:
        role_prefix = {"system": "System", "user": "User", "assistant": "Assistant"}.get(
            msg.role, msg.role.capitalize()
        )
        parts.append(f"{role_prefix}: {msg.content}")
    return "\n\n".join(parts)


@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    """Chat completion (flattened to generate)."""
    prompt = _flatten_messages(req.messages)
    compression = req.options.get("compression", DEFAULT_COMPRESSION)
    format_json = req.format == "json"

    if req.stream:
        return StreamingResponse(
            _generate_stream(
                model_id=req.model,
                prompt=prompt,
                system=None,
                options=req.options,
                format_json=format_json,
                keep_alive=req.keep_alive,
                compression=compression,
            ),
            media_type="application/x-ndjson",
        )
    else:
        full_text = ""
        final_data: dict[str, Any] = {"done": True}

        for line in _generate_stream(
            model_id=req.model,
            prompt=prompt,
            system=None,
            options=req.options,
            format_json=format_json,
            keep_alive=req.keep_alive,
            compression=compression,
        ):
            data = json.loads(line)
            if data.get("done"):
                final_data = data
            elif "response" in data and isinstance(data["response"], str):
                full_text += data["response"]

        return {
            "model": req.model,
            "message": {"role": "assistant", "content": full_text},
            "done": True,
            **{k: v for k, v in final_data.items() if k not in ("response", "done")},
        }


# Health endpoint for probes
@app.get("/health")
async def health():
    return {"status": "ok"}


def main():
    """Run the server."""
    # Bind to 0.0.0.0 so Docker's host port forward (and other containers on
    # lh-network reaching `http://airllm:11435`) can connect. 127.0.0.1-only
    # binds reject everything that doesn't originate from inside the container
    # with "Connection reset by peer", which silently breaks Traefik routing
    # and the dashboard's AirLLM provider. Override via AIRLLM_BIND if needed.
    host = os.getenv("AIRLLM_BIND", "0.0.0.0")
    uvicorn.run(app, host=host, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
