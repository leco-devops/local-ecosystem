"""
Ollama: pinned file, /api/tags + /api/ps + /api/version, backups, full model management.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import requests

from control import check_control_token

PROJECT_ROOT = os.getenv("DASHBOARD_PROJECT_ROOT", "/project")
PINNED_MODELS_FILE = Path(PROJECT_ROOT) / "ecosystem-stack" / "config" / "ollama-pinned-models.txt"
OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434").rstrip("/")
BACKUP_DIR = os.path.join(PROJECT_ROOT, ".local-eco-backups")
os.makedirs(BACKUP_DIR, mode=0o755, exist_ok=True)

_PINNED_HEADER = """# Models pulled automatically after `ollama` container starts (ollama.sh).
# One model name per line; lines starting with # are ignored.
"""


def read_pinned_models() -> list[str]:
    if not PINNED_MODELS_FILE.is_file():
        return []
    out = []
    for line in PINNED_MODELS_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.split("#", 1)[0].strip()
        if s:
            out.append(s)
    return out


def write_pinned_models(names: list[str]) -> None:
    PINNED_MODELS_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = [n.strip() for n in names if n and str(n).strip()]
    body = "\n".join(lines)
    PINNED_MODELS_FILE.write_text(_PINNED_HEADER + ("\n" + body if body else "\n"), encoding="utf-8")


def _ollama_get(path: str, timeout=12):
    try:
        r = requests.get(f"{OLLAMA_BASE}{path}", timeout=timeout)
        return r.status_code, r.json() if r.content else {}
    except Exception as exc:
        return None, {"error": str(exc)}


def _ollama_post(path: str, payload: dict, timeout=120):
    try:
        r = requests.post(f"{OLLAMA_BASE}{path}", json=payload, timeout=timeout)
        if r.status_code == 200:
            return True, r.json() if r.content else {}
        try:
            err = r.json()
        except Exception:
            err = {"raw": (r.text or "")[:800]}
        return False, err
    except Exception as exc:
        return False, {"error": str(exc)}


def _ollama_version() -> dict | None:
    code, data = _ollama_get("/api/version", timeout=6)
    if code == 200 and isinstance(data, dict):
        return data
    return None


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


def canonical_model_name(name: str) -> str:
    """Stable key: bare name and :latest tag merge; other tags stay distinct."""
    s = (name or "").strip().lower()
    if not s:
        return ""
    if ":" not in s:
        return s
    base, tag = s.rsplit(":", 1)
    if tag == "latest":
        return base if base else s
    return s


def build_models_payload() -> dict:
    pinned = read_pinned_models()
    ok_tags, installed = _ollama_tags()
    ok_ps, running = _ollama_ps()
    reachable = ok_tags or ok_ps
    version = _ollama_version() if reachable else None

    by_name: dict[str, dict] = {}
    for m in installed:
        if not isinstance(m, dict):
            continue
        n = _norm_name(m)
        if not n:
            continue
        details = m.get("details") if isinstance(m.get("details"), dict) else {}
        by_name[n] = {
            "name": n,
            "size": m.get("size"),
            "digest": m.get("digest"),
            "modified_at": m.get("modified_at"),
            "installed": True,
            "details": details,
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

    def merged_row_for_canonical(canon: str) -> dict:
        aliases = {n for n in (list(by_name) + list(running_names) + pinned) if canonical_model_name(n) == canon}
        aliases_installed = [n for n in aliases if n in by_name]
        aliases_running = [n for n in aliases if n in running_names]
        pinned_in_group = [p for p in pinned if canonical_model_name(p) == canon]

        if aliases_running:
            api_model = next((n for n in aliases_running if n in by_name), aliases_running[0])
        elif aliases_installed:
            api_model = max(aliases_installed, key=len)
        elif pinned_in_group:
            api_model = pinned_in_group[0]
        else:
            api_model = sorted(aliases, key=len)[-1] if aliases else canon

        if pinned_in_group:
            display_name = min(pinned_in_group, key=len)
        else:
            display_name = api_model

        base = by_name.get(api_model)
        if not base and aliases_installed:
            pick = max(aliases_installed, key=len)
            base = by_name[pick]
        base = base or {"name": api_model, "details": {}}

        rd: dict = {}
        if api_model in running_detail:
            rd = running_detail[api_model]
        elif aliases_running:
            rd = running_detail.get(aliases_running[0], {})

        details = base.get("details") or {}
        return {
            "name": display_name,
            "api_model": api_model,
            "canonical": canon,
            "pinned": bool(pinned_in_group),
            "installed": bool(aliases_installed),
            "running": bool(aliases_running),
            "size": base.get("size"),
            "digest": base.get("digest"),
            "modified_at": base.get("modified_at"),
            "parameter_size": details.get("parameter_size"),
            "quantization_level": details.get("quantization_level"),
            "model_family": details.get("family"),
            "size_vram": rd.get("size_vram"),
            "expires_at": rd.get("expires_at"),
            "runtime": rd if rd else None,
        }

    emitted: set[str] = set()
    rows: list[dict] = []
    for name in pinned:
        c = canonical_model_name(name)
        if not c or c in emitted:
            continue
        emitted.add(c)
        rows.append(merged_row_for_canonical(c))

    for n in sorted(by_name.keys()):
        c = canonical_model_name(n)
        if c in emitted:
            continue
        emitted.add(c)
        rows.append(merged_row_for_canonical(c))

    return {
        "ok": True,
        "ollama_reachable": reachable,
        "ollama_base": OLLAMA_BASE,
        "pinned_file": str(PINNED_MODELS_FILE),
        "pinned": pinned,
        "pinned_file_exists": PINNED_MODELS_FILE.is_file(),
        "server_version": version,
        "installed_count": len(by_name),
        "running_count": len(running_names),
        "rows": rows,
    }


def ollama_inspect(model: str) -> dict:
    model = (model or "").strip()
    if not model:
        return {"ok": False, "error": "model name required"}
    ok, data = _ollama_post("/api/show", {"name": model}, timeout=60)
    if ok:
        return {"ok": True, "model": model, "show": data}
    return {"ok": False, "error": data, "model": model}


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


def ollama_warm(model: str, keep_alive: str = "-1") -> dict:
    """Load model into memory (minimal generate). keep_alive -1 = until unloaded."""
    model = (model or "").strip()
    if not model:
        return {"ok": False, "error": "model name required"}
    try:
        r = requests.post(
            f"{OLLAMA_BASE}/api/generate",
            json={
                "model": model,
                "prompt": ".",
                "stream": False,
                "keep_alive": keep_alive,
                "options": {"num_predict": 2},
            },
            timeout=600,
        )
        if r.status_code == 200:
            return {"ok": True, "model": model, "note": f"Model loaded in memory (keep_alive={keep_alive})."}
        return {"ok": False, "error": (r.text or "")[:800], "status_code": r.status_code}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def ollama_unload_all() -> dict:
    ok, running = _ollama_ps()
    if not ok:
        return {"ok": False, "error": "could not list running models (/api/ps)"}
    errors = []
    for m in running:
        n = _norm_name(m)
        if n:
            res = ollama_unload(n)
            if not res.get("ok"):
                errors.append({"model": n, "error": res.get("error")})
    return {"ok": True, "unloaded": len(running), "errors": errors or None}


def backup_manifest() -> dict:
    ok_tags, installed = _ollama_tags()
    ok_ps, running = _ollama_ps()
    pinned = read_pinned_models()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    fname = f"ollama-manifest-{ts}.json"
    path = os.path.join(BACKUP_DIR, fname)
    body = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "ollama_base": OLLAMA_BASE,
        "tags_ok": ok_tags,
        "pinned": pinned,
        "pinned_file": str(PINNED_MODELS_FILE),
        "models": installed,
        "running": running,
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(body, f, indent=2)
        return {"ok": True, "path": path, "filename": fname}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


def list_manifest_backups() -> dict:
    try:
        names = sorted(
            [f for f in os.listdir(BACKUP_DIR) if f.startswith("ollama-manifest-") and f.endswith(".json")],
            reverse=True,
        )[:80]
        items = []
        for n in names:
            p = os.path.join(BACKUP_DIR, n)
            try:
                st = os.stat(p)
                items.append({"filename": n, "size": st.st_size, "mtime": int(st.st_mtime)})
            except OSError:
                items.append({"filename": n})
        return {"ok": True, "backups": items}
    except OSError as exc:
        return {"ok": False, "error": str(exc), "backups": []}


def restore_manifest(filename: str) -> dict:
    base = os.path.basename((filename or "").strip())
    if not base or ".." in base or "/" in base or "\\" in base:
        return {"ok": False, "error": "invalid backup filename"}
    if not (base.startswith("ollama-manifest-") and base.endswith(".json")):
        return {"ok": False, "error": "invalid backup filename"}
    path = os.path.join(BACKUP_DIR, base)
    if not os.path.isfile(path):
        return {"ok": False, "error": "backup not found"}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": str(exc)}
    pinned = data.get("pinned")
    if not isinstance(pinned, list):
        return {"ok": False, "error": "manifest has no pinned array"}
    clean = [str(x).strip() for x in pinned if str(x).strip()]
    write_pinned_models(clean)
    return {
        "ok": True,
        "pinned": clean,
        "restored_from": base,
        "note": "Pinned list restored from backup. Pull models if they are missing on disk.",
    }


def pin_model(model: str) -> dict:
    model = (model or "").strip()
    if not model:
        return {"ok": False, "error": "model name required"}
    cur = read_pinned_models()
    cn = canonical_model_name(model)
    if any(canonical_model_name(x) == cn for x in cur):
        return {"ok": True, "pinned": cur, "note": "Already pinned (this model group)."}
    cur.append(model)
    write_pinned_models(cur)
    return {"ok": True, "pinned": cur, "note": f"Pinned {model}."}


def unpin_model(model: str) -> dict:
    model = (model or "").strip()
    if not model:
        return {"ok": False, "error": "model name required"}
    cur = read_pinned_models()
    new = [x for x in cur if x != model]
    write_pinned_models(new)
    return {"ok": True, "pinned": new, "note": f"Removed pin for {model}."}


def unpin_by_canonical(canonical: str) -> dict:
    key = canonical_model_name(canonical)
    if not key:
        return {"ok": False, "error": "canonical required"}
    cur = read_pinned_models()
    new = [x for x in cur if canonical_model_name(x) != key]
    if len(new) == len(cur):
        return {"ok": True, "pinned": cur, "note": "Nothing to unpin for this model group."}
    write_pinned_models(new)
    return {"ok": True, "pinned": new, "note": "Removed pin(s) for this model group."}


def set_pinned_models(models: list) -> dict:
    if not isinstance(models, list):
        return {"ok": False, "error": "models must be an array of strings"}
    clean = [str(x).strip() for x in models if str(x).strip()]
    write_pinned_models(clean)
    return {"ok": True, "pinned": clean}


def clear_pinned_file() -> dict:
    write_pinned_models([])
    return {"ok": True, "pinned": [], "note": "Pinned list cleared (file kept with comments only)."}


def handle_models_action(request, data: dict | None) -> tuple[dict, int]:
    if data is None:
        data = request.get_json(silent=True) or {}
    if not check_control_token(request, data):
        return {"ok": False, "error": "unauthorized"}, 401

    action = (data.get("action") or "").strip().lower()
    model = (data.get("model") or "").strip()
    canonical = (data.get("canonical") or "").strip()
    filename = (data.get("filename") or "").strip()
    keep_alive = (data.get("keep_alive") or "-1").strip() or "-1"

    if action == "pull":
        body = start_pull(model)
        return body, 200 if body.get("ok") else 400
    if action == "pull_all":
        body = start_pull_all()
        return body, 200 if body.get("ok") else 400
    if action == "delete":
        body = ollama_delete(model)
        return body, 200 if body.get("ok") else 400
    if action in ("unload", "off"):
        body = ollama_unload(model)
        return body, 200 if body.get("ok") else 400
    if action in ("on", "reinstall", "ensure"):
        body = start_pull(model)
        return body, 200 if body.get("ok") else 400
    if action in ("warm", "load", "load_ram"):
        body = ollama_warm(model, keep_alive=keep_alive)
        return body, 200 if body.get("ok") else 400
    if action == "unload_all":
        body = ollama_unload_all()
        return body, 200 if body.get("ok") else 400
    if action == "backup_manifest":
        body = backup_manifest()
        return body, 200 if body.get("ok") else 400
    if action == "list_backups":
        body = list_manifest_backups()
        return body, 200 if body.get("ok") else 400
    if action == "restore_backup":
        body = restore_manifest(filename)
        return body, 200 if body.get("ok") else 400
    if action == "pin":
        body = pin_model(model)
        return body, 200 if body.get("ok") else 400
    if action == "unpin":
        body = unpin_by_canonical(canonical) if canonical else unpin_model(model)
        return body, 200 if body.get("ok") else 400
    if action == "set_pinned":
        body = set_pinned_models(data.get("models") or [])
        return body, 200 if body.get("ok") else 400
    if action == "clear_pinned":
        body = clear_pinned_file()
        return body, 200

    return {"ok": False, "error": f"unknown action: {action}"}, 400


def handle_inspect(request) -> tuple[dict, int]:
    if not check_control_token(request, None):
        return {"ok": False, "error": "unauthorized"}, 401
    model = (request.args.get("model") or "").strip()
    if not model:
        return {"ok": False, "error": "model query parameter required"}, 400
    body = ollama_inspect(model)
    return body, 200 if body.get("ok") else 400
