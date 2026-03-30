import os
import shlex
import subprocess
import time
from datetime import datetime, timezone

import docker
import requests

from control_targets import AI_TARGETS, CF_TARGETS, COMPOSE_REL

PROJECT_ROOT = os.getenv("DASHBOARD_PROJECT_ROOT", "/project")
COMPOSE_FILE = os.path.join(PROJECT_ROOT, COMPOSE_REL)
SERVICES_DIR = os.path.join(PROJECT_ROOT, "ai-stack", "services")
CONTROL_TOKEN = os.getenv("DASHBOARD_CONTROL_TOKEN", "").strip()

D1_BASE = os.getenv("DASHBOARD_D1_URL", "http://d1-adapter:8083").rstrip("/")
BACKUP_DIR = os.path.join(PROJECT_ROOT, ".local-eco-backups")
os.makedirs(BACKUP_DIR, mode=0o755, exist_ok=True)

ALLOWED_ACTIONS = frozenset(
    {"start", "stop", "restart", "remove", "pause", "unpause", "deploy", "recreate", "reset", "backup"}
)

_BY_ID = {t["id"]: t for t in CF_TARGETS + AI_TARGETS}


def _container_runtime(dc, container_name: str | None) -> dict:
    """Live Docker status for a single container (for Control UI)."""
    if not container_name:
        return {
            "kind": "stack",
            "status": "n/a",
            "label": "Stack / compose — no single container",
            "running": None,
        }
    if dc is None:
        return {
            "kind": "unknown",
            "status": "unknown",
            "label": "Docker unreachable",
            "running": None,
        }
    try:
        c = dc.containers.get(container_name)
        st = (c.status or "unknown").lower()
        pretty = st[:1].upper() + st[1:] if st else "Unknown"
        return {
            "kind": "container",
            "status": st,
            "label": pretty,
            "running": st == "running",
            "short_id": getattr(c, "short_id", None) or (c.id[:12] if c.id else None),
        }
    except docker.errors.NotFound:
        return {
            "kind": "container",
            "status": "missing",
            "label": "Not running / not found",
            "running": False,
        }
    except Exception as exc:
        return {
            "kind": "error",
            "status": "error",
            "label": str(exc)[:160],
            "running": None,
        }


def check_control_token(request, data=None) -> bool:
    if not CONTROL_TOKEN:
        return True
    if request.headers.get("X-Control-Token", "") == CONTROL_TOKEN:
        return True
    if data is None:
        data = request.get_json(silent=True) or {}
    return data.get("token", "") == CONTROL_TOKEN


def list_targets():
    dc = _docker_client()
    out = [
        {
            "id": "stack-ecosystem-all",
            "label": "All AI-stack services (bulk)",
            "group": "ecosystem",
            "container": None,
            "actions": ["start", "stop", "restart", "deploy"],
            "runtime": {
                "kind": "stack",
                "status": "bulk",
                "label": "One-click start/stop/redeploy via ai-stack/core.sh (stop skips this dashboard)",
                "running": None,
            },
        }
    ]
    for t in CF_TARGETS:
        out.append(
            {
                "id": t["id"],
                "label": t["label"],
                "group": "cloudflare-local",
                "container": t["container"],
                "actions": sorted(ALLOWED_ACTIONS),
                "runtime": _container_runtime(dc, t.get("container")),
            }
        )
    for t in AI_TARGETS:
        entry = {
            "id": t["id"],
            "label": t["label"],
            "group": "ai-stack",
            "container": t.get("container"),
            "actions": sorted(ALLOWED_ACTIONS),
            "runtime": _container_runtime(dc, t.get("container")),
        }
        if t.get("container") == "service-dashboard" and t["script"] == "dashboard":
            entry["actions"] = sorted(a for a in ALLOWED_ACTIONS if a not in {"remove", "reset"})
        out.append(entry)
    out.append(
        {
            "id": "stack-cf-all",
            "label": "Cloudflare local — entire stack",
            "group": "cloudflare-local",
            "container": None,
            "actions": ["deploy", "stop", "restart", "remove", "reset", "backup"],
            "runtime": {
                "kind": "stack",
                "status": "compose",
                "label": "Whole compose file — status per service above",
                "running": None,
            },
        }
    )
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "token_required": bool(CONTROL_TOKEN), "targets": out}


def _run(cmd, cwd=None, timeout=300):
    try:
        p = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except Exception as exc:
        return 1, str(exc)


def _compose(args, timeout=600):
    if not os.path.isfile(COMPOSE_FILE):
        return 1, f"compose file missing: {COMPOSE_FILE}"
    return _run(["docker", "compose", "-f", COMPOSE_FILE, *args], cwd=os.path.dirname(COMPOSE_FILE), timeout=timeout)


def _ai_script(script, action, timeout=600):
    path = os.path.join(SERVICES_DIR, f"{script}.sh")
    if not os.path.isfile(path):
        return 1, f"script missing: {path}"
    return _run(["/bin/bash", path, action], cwd=PROJECT_ROOT, timeout=timeout)


def _docker_client():
    try:
        return docker.from_env()
    except Exception:
        return None


def _backup_postgres():
    ts = time.strftime("%Y%m%d-%H%M%S")
    out_path = os.path.join(BACKUP_DIR, f"n8n_postgres-{ts}.sql")
    try:
        p = subprocess.run(
            ["docker", "exec", "n8n_postgres", "pg_dump", "-U", "postgres", "n8n"],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except Exception as exc:
        return False, str(exc)
    if p.returncode != 0:
        return False, (p.stderr or p.stdout or "pg_dump failed")[:4000]
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(p.stdout or "")
    except OSError as exc:
        return False, str(exc)
    return True, out_path


def _backup_d1_all():
    try:
        r = requests.get(f"{D1_BASE}/databases", timeout=10)
        r.raise_for_status()
        names = r.json().get("databases") or []
    except Exception as exc:
        return False, str(exc)
    files = []
    for name in names:
        try:
            rr = requests.post(f"{D1_BASE}/databases/{name}/backup", timeout=120)
            rr.raise_for_status()
            data = rr.json()
            if data.get("backup_file"):
                files.append(data["backup_file"])
        except Exception as exc:
            files.append(f"{name}: error {exc}")
    return True, files


def run_action(target_id: str, action: str):
    if action not in ALLOWED_ACTIONS:
        return {"ok": False, "error": f"unsupported action: {action}"}

    if target_id == "stack-cf-all":
        return _stack_cf_all(action)

    if target_id == "stack-ecosystem-all":
        return _stack_ecosystem_all(action)

    meta = _BY_ID.get(target_id)
    if not meta:
        return {"ok": False, "error": "unknown target"}

    if "compose_service" in meta:
        return _cf_service_action(meta, action)

    return _ai_service_action(meta, action)


def _stack_ecosystem_all(action: str):
    if action not in {"start", "stop", "restart", "deploy"}:
        return {"ok": False, "error": f"action {action} not supported for ecosystem bulk"}
    core_sh = os.path.join(PROJECT_ROOT, "ai-stack", "core.sh")
    if not os.path.isfile(core_sh):
        return {"ok": False, "error": f"missing {core_sh}"}
    src = f"source {shlex.quote(core_sh)} && bulk_ecosystem {shlex.quote(action)}"
    code, log = _run(["/bin/bash", "-c", src], cwd=PROJECT_ROOT, timeout=3600)
    return {"ok": code == 0, "exit_code": code, "log": log[-12000:]}


def _stack_cf_all(action: str):
    if action == "deploy":
        code, log = _compose(["up", "-d", "--build"])
        return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
    if action == "stop":
        code, log = _compose(["stop"])
        return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
    if action == "restart":
        code, log = _compose(["restart"])
        return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
    if action == "remove":
        code, log = _compose(["down", "--remove-orphans"])
        return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
    if action == "reset":
        code, log = _compose(["down", "-v", "--remove-orphans"])
        return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
    if action == "backup":
        ok, res = _backup_d1_all()
        return {"ok": ok, "detail": res}
    return {"ok": False, "error": f"action {action} not supported for full stack"}


def _cf_service_action(meta: dict, action: str):
    svc = meta["compose_service"]
    cname = meta["container"]

    if action == "deploy":
        code, log = _compose(["up", "-d", "--build", svc])
        return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
    if action == "recreate":
        code, log = _compose(["up", "-d", "--force-recreate", "--no-deps", svc])
        return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
    if action == "reset":
        code1, log1 = _compose(["stop", svc])
        code2, log2 = _compose(["rm", "-sf", svc])
        code3, log3 = _compose(["up", "-d", svc])
        log = f"{log1}\n{log2}\n{log3}"
        return {"ok": code1 == 0 and code2 == 0 and code3 == 0, "exit_code": code3, "log": log[-8000:]}
    if action == "remove":
        code, log = _compose(["rm", "-sf", svc])
        return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
    if action == "backup":
        if svc == "d1-adapter":
            ok, res = _backup_d1_all()
            return {"ok": ok, "detail": res}
        return {"ok": False, "error": "backup only defined for d1-adapter or full stack"}

    if action == "start":
        code, log = _compose(["start", svc])
        if code != 0:
            code, log = _compose(["up", "-d", svc])
        return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}

    dc = _docker_client()
    if dc is None:
        return {"ok": False, "error": "docker unavailable"}
    try:
        c = dc.containers.get(cname)
    except Exception:
        return {"ok": False, "error": f"container {cname} not found"}

    try:
        if action == "stop":
            c.stop(timeout=30)
        elif action == "restart":
            c.restart(timeout=30)
        elif action == "pause":
            c.pause()
        elif action == "unpause":
            c.unpause()
        else:
            return {"ok": False, "error": f"unsupported action {action}"}
        return {"ok": True, "container": cname, "action": action}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _ai_service_action(meta: dict, action: str):
    script = meta["script"]
    cname = meta.get("container")

    if script == "cloudflare-local":
        if action == "deploy":
            code, log = _ai_script("cloudflare-local", "start")
            return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
        if action == "stop":
            code, log = _ai_script("cloudflare-local", "stop")
            return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
        if action == "restart":
            code, log = _ai_script("cloudflare-local", "restart")
            return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
        if action == "remove":
            code, log = _ai_script("cloudflare-local", "remove")
            return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
        if action == "reset":
            code, log = _ai_script("cloudflare-local", "reset")
            return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
        if action == "backup":
            ok, res = _backup_d1_all()
            return {"ok": ok, "detail": res}
        if action in {"start", "pause", "unpause"}:
            code, log = _ai_script("cloudflare-local", action)
            return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
        if action == "recreate":
            code1, log1 = _ai_script("cloudflare-local", "remove")
            code2, log2 = _ai_script("cloudflare-local", "start")
            return {"ok": code1 == 0 and code2 == 0, "log": (log1 + log2)[-8000:]}
        return {"ok": False, "error": f"unsupported action {action} for cloudflare-local script"}

    if action == "deploy":
        code, log = _ai_script(script, "start")
        return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
    if action == "recreate":
        code1, log1 = _ai_script(script, "remove")
        code2, log2 = _ai_script(script, "start")
        return {"ok": code1 == 0 and code2 == 0, "log": (log1 + log2)[-8000:]}
    if action == "reset":
        if meta.get("reset_volume"):
            dc = _docker_client()
            if dc is None:
                return {"ok": False, "error": "docker unavailable"}
            vol = meta["reset_volume"]
            try:
                if cname:
                    try:
                        dc.containers.get(cname).remove(force=True)
                    except Exception:
                        pass
                try:
                    dc.volumes.get(vol).remove(force=True)
                except Exception:
                    pass
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
            code, log = _ai_script(script, "start")
            return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
        code, log = _ai_script(script, "reset")
        return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
    if action == "backup":
        if cname == "n8n_postgres":
            ok, res = _backup_postgres()
            return {"ok": ok, "detail": res}
        return {"ok": False, "error": "no backup defined for this service"}

    if action in {"start", "stop", "restart", "remove", "pause", "unpause"}:
        code, log = _ai_script(script, action)
        return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}

    return {"ok": False, "error": f"unsupported action {action}"}
