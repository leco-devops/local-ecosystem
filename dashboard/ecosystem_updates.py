"""Read update-catalog output written by leco-update-catalog Docker service."""

from __future__ import annotations

import json
import os
from pathlib import Path

PROJECT_ROOT = Path(os.getenv("DASHBOARD_PROJECT_ROOT", "/project"))
GENERATED = PROJECT_ROOT / "ecosystem-stack" / "config" / "generated"

UPDATES_JSON = GENERATED / "ecosystem-updates.json"
OLLAMA_CATALOG_JSON = GENERATED / "llm-catalog-ollama.json"
AIRLLM_CATALOG_JSON = GENERATED / "llm-catalog-airllm.json"
META_JSON = GENERATED / "catalog-meta.json"


def _read(path: Path, default: dict) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else default
    except (OSError, ValueError):
        return default


def load_ecosystem_updates() -> dict:
    data = _read(UPDATES_JSON, {"ok": False, "error": "catalog not generated yet"})
    if not data.get("generated_at"):
        data.setdefault(
            "hint",
            "Start leco-update-catalog: ./ecosystem-stack/services/update-catalog.sh start",
        )
    return data


def load_llm_catalog(backend: str) -> dict:
    path = OLLAMA_CATALOG_JSON if backend == "ollama" else AIRLLM_CATALOG_JSON
    data = _read(path, {"ok": False, "backend": backend, "models": [], "error": "catalog not generated yet"})
    data.setdefault("backend", backend)
    if not data.get("generated_at"):
        data.setdefault(
            "hint",
            "Run ./ecosystem-stack/services/update-catalog.sh run-once",
        )
    return data


def load_catalog_meta() -> dict:
    return _read(META_JSON, {})
