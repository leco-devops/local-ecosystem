"""Read curated popular-model JSON catalogs for the dashboard dropdowns.

Catalogs live in `ecosystem-stack/config/popular-{ollama,airllm}-models.json` and
are mounted into the container at `/project/...`. If the file is missing or
malformed, we fall back to a small built-in list so the UI still has something to
show.

Each entry has the shape:
    {
        "name": "llama3.2:3b" | "Qwen/Qwen2.5-7B-Instruct",
        "label": "Llama 3.2 · 3B (Q4)",
        "family": "llama",
        "size": "~2 GB",
        "tags": ["small", "instruct"],
        "description": "..."
    }

`name` is the only required field; everything else is presentational.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

PROJECT_ROOT = Path(os.getenv("DASHBOARD_PROJECT_ROOT", "/project"))
_CONFIG_DIR = PROJECT_ROOT / "ecosystem-stack" / "config"

OLLAMA_CATALOG = _CONFIG_DIR / "popular-ollama-models.json"
AIRLLM_CATALOG = _CONFIG_DIR / "popular-airllm-models.json"

_OLLAMA_FALLBACK: list[dict] = [
    {"name": "llama3.2:3b", "label": "Llama 3.2 · 3B", "family": "llama", "size": "~2 GB"},
    {"name": "llama3.1:8b", "label": "Llama 3.1 · 8B", "family": "llama", "size": "~4.7 GB"},
    {"name": "qwen2.5:7b", "label": "Qwen 2.5 · 7B", "family": "qwen", "size": "~4.7 GB"},
    {"name": "deepseek-r1:7b", "label": "DeepSeek R1 · 7B", "family": "deepseek", "size": "~4.7 GB"},
]

_AIRLLM_FALLBACK: list[dict] = [
    {
        "name": "Qwen/Qwen2.5-0.5B-Instruct",
        "label": "Qwen 2.5 · 0.5B Instruct",
        "family": "qwen",
        "size": "~1 GB",
    },
    {
        "name": "Qwen/Qwen2.5-7B-Instruct",
        "label": "Qwen 2.5 · 7B Instruct",
        "family": "qwen",
        "size": "~15 GB",
    },
]


def _load_catalog(path: Path, fallback: list[dict]) -> dict:
    """Return `{ok, path, source, models, error?}`. `source` is 'file' or 'fallback'."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "ok": True,
            "path": str(path),
            "source": "fallback",
            "models": list(fallback),
            "error": "catalog file not found",
        }
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "path": str(path),
            "source": "fallback",
            "models": list(fallback),
            "error": f"could not parse catalog: {exc}",
        }
    models = raw.get("models") if isinstance(raw, dict) else raw
    if not isinstance(models, list):
        return {
            "ok": False,
            "path": str(path),
            "source": "fallback",
            "models": list(fallback),
            "error": "catalog 'models' key missing or not a list",
        }
    cleaned: list[dict] = []
    for m in models:
        if not isinstance(m, dict):
            continue
        name = str(m.get("name") or "").strip()
        if not name:
            continue
        cleaned.append(
            {
                "name": name,
                "label": str(m.get("label") or name),
                "family": str(m.get("family") or ""),
                "size": str(m.get("size") or ""),
                "tags": list(m.get("tags") or []),
                "description": str(m.get("description") or ""),
            }
        )
    if not cleaned:
        return {
            "ok": True,
            "path": str(path),
            "source": "fallback",
            "models": list(fallback),
            "error": "catalog had no valid entries",
        }
    return {
        "ok": True,
        "path": str(path),
        "source": "file",
        "models": cleaned,
    }


def load_ollama_catalog() -> dict:
    return _load_catalog(OLLAMA_CATALOG, _OLLAMA_FALLBACK)


def load_airllm_catalog() -> dict:
    return _load_catalog(AIRLLM_CATALOG, _AIRLLM_FALLBACK)
