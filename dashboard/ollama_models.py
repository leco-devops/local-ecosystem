"""
Ollama pinned models: read ai-stack/config/ollama-pinned-models.txt, status via Ollama HTTP API.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import requests

from control import check_control_token

PROJECT_ROOT = os.getenv("DASHBOARD_PROJECT_ROOT", "/project")
PINNED_MODELS_FILE = Path(PROJECT_ROOT) / "ai-stack" / "config" / "ollama-pinned-models.txt"
OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434").rstrip("/")


def read_pinned_models() -> list[str]:
    if not PINNED_MODELS_FILE.is_file():
        return []
    out = []
    for line in PINNED_MODELS_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.split("#", 1)[0].strip()
        if s:
            out.append(s)
    return out


def _ollama_get(path: str, timeout=8):
    try:
        r = requests.get(f"{OLLAMA_BASE}{path}", timeout=timeout)
        return r.status_code, r.json() if r.content else {}
    except Exception as exc:
        return None, {"error": str(exc)}


def _ollama_tags() -> tuple[bool, list[dict]]:
    code, data = _ollama_get("/api/tags")
    if code != 200 or not isinstance(data, dict):
        return False, []
    models = data.get("models") or []
    return True, models if isinstance(models, list) else []


def _ollama_ps() -> tuple[bool, list[dict]]:
    code, data = _ollama_get("/api/ps")
    if code != 200 or not isinstance(data, dict):
        return False, []
    models = data.get("models") or []
    return True, models if isinstance(models, list) else []


def _norm_name(entry: dict) -> str:
    return (entry.get("name") or entry.get("model") or "").strip()


def build_models_payload() -> dict:
    pinned = read_pinned_models()
    ok_tags, installed = _ollama_tags()
    ok_ps, running = _ollama_ps()
    reachable = ok_tags or ok_ps

    by_name: dict[str, dict] = {}
    for m in installed:
        if not isinstance(m, dict):
            continue
        n = _norm_name(m)
        if not n:
            continue
        by_name[n] = {
            "name": n,
            "size": m.get("size"),
            "digest": m.get("digest"),
            "modified_at": m.get("modified_at"),
            "installed": True,
        }

    running_names = set()
    running_detail: dict[str, dict] = {}
    for m in running:
        if not isinstance(m, dict):
            continue
        n = _norm_name(m)
        if n:
            running_names.add(n)
            running_detail[n] = m

    # Include pinned rows first, then other installed
    rows = []
    seen = set()
    for name in pinned:
        base = by_name.get(name, {"name": name})
        rows.append(
            {
                "name": name,
                "pinned": True,
                "installed": name in by_name,
                "running": name in running_names,
                "size": base.get("size"),
                "digest": base.get("digest"),
                "modified_at": base.get("modified_at"),
                "runtime": running_detail.get(name),
            }
        )
        seen.add(name)

    for n, info in sorted(by_name.items()):
        if n in seen:
            continue
        rows.append(
            {
                "name": n,
                "pinned": False,
                "installed": True,
                "running": n in running_names,
                "size": info.get("size"),
                "digest": info.get("digest"),
                "modified_at": info.get("modified_at"),
                "runtime": running_detail.get(n),
            }
        )

    return {
        "ok": True,
        "ollama_reachable": reachable,
        "ollama_base": OLLAMA_BASE,
        "pinned_file": str(PINNED_MODELS_FILE),
        "pinned": pinned,
        "rows": rows,
    }


def _pull_bg(model: str):
    try:
        with requests.post(
            f"{OLLAMA_BASE}/api/pull",
            json={"name": model.strip()},
            stream=True,
            timeout=(15, 7200),
        ) as r:
            for _ in r.iter_lines():
                pass
    except Exception:
        pass


def start_pull(model: str) -> dict:
    model = (model or "").strip()
    if not model:
        return {"ok": False, "error": "model name required"}
    t = threading.Thread(target=_pull_bg, args=(model,), daemon=True)
    t.start()
    return {"ok": True, "started": True, "model": model, "note": "Pull runs in background; refresh status in a few minutes."}


def start_pull_all() -> dict:
    pinned = read_pinned_models()
    if not pinned:
        return {"ok": False, "error": "no pinned models in config file"}

    def run_all():
        for m in pinned:
            _pull_bg(m)

    threading.Thread(target=run_all, daemon=True).start()
    return {"ok": True, "started": True, "models": pinned, "note": "Sequential pulls in background."}


def ollama_delete(model: str) -> dict:
    model = (model or "").strip()
    if not model:
        return {"ok": False, "error": "model name required"}
    try:
        r = requests.delete(f"{OLLAMA_BASE}/api/delete", json={"name": model}, timeout=120)
        if r.status_code == 200:
            return {"ok": True, "model": model}
        try:
            err = r.json()
        except Exception:
            err = {"raw": r.text[:500]}
        return {"ok": False, "error": err, "status_code": r.status_code}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def ollama_unload(model: str) -> dict:
    """Ask Ollama to drop model from memory (keep_alive 0)."""
    model = (model or "").strip()
    if not model:
        return {"ok": False, "error": "model name required"}
    try:
        r = requests.post(
            f"{OLLAMA_BASE}/api/generate",
            json={"model": model, "prompt": "", "stream": False, "keep_alive": 0},
            timeout=60,
        )
        if r.status_code == 200:
            return {"ok": True, "model": model, "note": "Unload requested (keep_alive=0)."}
        return {"ok": False, "error": r.text[:500], "status_code": r.status_code}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def handle_models_action(request, data: dict | None) -> tuple[dict, int]:
    if data is None:
        data = request.get_json(silent=True) or {}
    if not check_control_token(request, data):
        return {"ok": False, "error": "unauthorized"}, 401

    action = (data.get("action") or "").strip().lower()
    model = (data.get("model") or "").strip()

    if action == "pull":
        body = start_pull(model)
        return body, 200 if body.get("ok") else 400
    if action == "pull_all":
        body = start_pull_all()
        return body, 200 if body.get("ok") else 400
    if action == "delete":
        body = ollama_delete(model)
        return body, 200 if body.get("ok") else 400
    if action == "unload" or action == "off":
        body = ollama_unload(model)
        return body, 200 if body.get("ok") else 400
    if action in ("on", "reinstall", "ensure"):
        # Same as pull (idempotent reinstall)
        body = start_pull(model)
        return body, 200 if body.get("ok") else 400

    return {"ok": False, "error": f"unknown action: {action}"}, 400
