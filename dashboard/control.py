import os
import shlex
import subprocess
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

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

# Subset passed as a shell function name after `source` (must be identifier-safe).
_AI_SCRIPT_FN_ACTIONS = frozenset(
    {"start", "stop", "restart", "remove", "pause", "unpause", "reset"}
)

_BY_ID = {t["id"]: t for t in CF_TARGETS + AI_TARGETS}


def _format_invoked_cmd(cmd: list) -> str:
    return " ".join(shlex.quote(str(x)) for x in cmd)


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
            "actions": sorted(ALLOWED_ACTIONS),
            "runtime": {
                "kind": "stack",
                "status": "bulk",
                "label": "Full action set via ai-stack/core.sh (stop/pause/remove/reset/recreate skip this dashboard so the API can finish)",
                "running": None,
            },
        }
    ]
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


def _yield_run(cmd, cwd=None, timeout=600) -> Iterator[dict[str, Any]]:
    """Stream merged stdout/stderr as log events; return (exit_code, full_text) via StopIteration."""
    parts: list[str] = []
    header = f"$ {_format_invoked_cmd(cmd)}\n"
    if cwd:
        header += f"# cwd: {cwd}\n"
    header += "\n"
    parts.append(header)
    yield {"type": "log", "text": header}
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=0,
        )
    except Exception as exc:
        msg = f"{exc}\n"
        parts.append(msg)
        yield {"type": "log", "text": msg}
        return 1, "".join(parts)

    if proc.stdout is None:
        return 1, "".join(parts)

    try:
        while True:
            chunk = proc.stdout.read(8192)
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace")
            parts.append(text)
            yield {"type": "log", "text": text}
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
            tail = "\n[timeout]\n"
            parts.append(tail)
            yield {"type": "log", "text": tail}
            code = 124
    except Exception as exc:
        try:
            proc.kill()
        except Exception:
            pass
        msg = f"\n[error reading output: {exc}]\n"
        parts.append(msg)
        yield {"type": "log", "text": msg}
        code = 1
    return code, "".join(parts)


def _compose(args, timeout=600):
    if not os.path.isfile(COMPOSE_FILE):
        return 1, f"compose file missing: {COMPOSE_FILE}"
    return _run(["docker", "compose", "-f", COMPOSE_FILE, *args], cwd=os.path.dirname(COMPOSE_FILE), timeout=timeout)


def _ai_script(script, action, timeout=600):
    path = os.path.join(SERVICES_DIR, f"{script}.sh")
    if not os.path.isfile(path):
        return 1, f"script missing: {path}"
    if action not in _AI_SCRIPT_FN_ACTIONS:
        return 1, f"action not invokable as service function: {action}"
    # Service scripts define bash functions; ai-stack/core.sh uses `source` + call.
    # A bare `bash script.sh stop` only defines functions and exits 0 without running them.
    root_q = shlex.quote(PROJECT_ROOT)
    path_q = shlex.quote(path)
    src = f"export PROJECT_ROOT={root_q} && source {path_q} && {action}"
    return _run(["/bin/bash", "-c", src], cwd=PROJECT_ROOT, timeout=timeout)


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


def _bulk_ecosystem_backup() -> dict:
    """Postgres dump + D1 backups + cloudflare-local backup script (best-effort aggregate)."""
    parts: list[str] = []
    ok_pg, res_pg = _backup_postgres()
    parts.append(f"postgres: {'OK ' + str(res_pg) if ok_pg else 'FAIL ' + str(res_pg)}")
    ok_d1, res_d1 = _backup_d1_all()
    parts.append(f"d1: {res_d1}")
    code, log = _ai_script("cloudflare-local", "backup")
    parts.append((log or "")[-8000:])
    ok = ok_pg and ok_d1 and code == 0
    return {"ok": ok, "exit_code": 0 if ok else 1, "log": "\n".join(parts)[-12000:]}


_ECOSYSTEM_BULK_BASH_ACTIONS = frozenset(
    {"start", "stop", "restart", "deploy", "pause", "unpause", "remove", "reset", "recreate"}
)


def _stack_ecosystem_all(action: str):
    if action == "backup":
        body = _bulk_ecosystem_backup()
        return body
    if action not in _ECOSYSTEM_BULK_BASH_ACTIONS:
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


# --- Streaming control (NDJSON / live log) ---------------------------------


def _stream_compose(args: list, timeout: int = 600) -> Iterator[dict[str, Any] | Any]:
    if not os.path.isfile(COMPOSE_FILE):
        yield {"type": "log", "text": f"compose file missing: {COMPOSE_FILE}\n"}
        return (1, "")
    code, log = yield from _yield_run(
        ["docker", "compose", "-f", COMPOSE_FILE, *args],
        cwd=os.path.dirname(COMPOSE_FILE),
        timeout=timeout,
    )
    return (code, log)


def _stream_ai_script(script: str, action: str, timeout: int = 600) -> Iterator[dict[str, Any] | Any]:
    path = os.path.join(SERVICES_DIR, f"{script}.sh")
    if not os.path.isfile(path):
        yield {"type": "log", "text": f"script missing: {path}\n"}
        return (1, "")
    if action not in _AI_SCRIPT_FN_ACTIONS:
        yield {"type": "log", "text": f"action not invokable as service function: {action}\n"}
        return (1, "")
    root_q = shlex.quote(PROJECT_ROOT)
    path_q = shlex.quote(path)
    src = f"export PROJECT_ROOT={root_q} && source {path_q} && {action}"
    code, log = yield from _yield_run(["/bin/bash", "-c", src], cwd=PROJECT_ROOT, timeout=timeout)
    return (code, log)


def _emit_done(ok: bool, **extra) -> dict[str, Any]:
    body: dict[str, Any] = {"ok": ok, **extra}
    return {"type": "done", "result": body}


def run_action_streaming(target_id: str, action: str) -> Iterator[dict[str, Any]]:
    if action not in ALLOWED_ACTIONS:
        yield _emit_done(False, error=f"unsupported action: {action}")
        return

    if target_id == "stack-cf-all":
        yield from _stream_stack_cf_all_stream(action)
        return
    if target_id == "stack-ecosystem-all":
        yield from _stream_stack_ecosystem_all_stream(action)
        return

    meta = _BY_ID.get(target_id)
    if not meta:
        yield _emit_done(False, error="unknown target")
        return

    if "compose_service" in meta:
        yield from _stream_cf_service_action_stream(meta, action)
        return

    yield from _stream_ai_service_action_stream(meta, action)


def _stream_stack_ecosystem_all_stream(action: str) -> Iterator[dict[str, Any]]:
    if action == "backup":
        yield {"type": "log", "text": "Ecosystem bulk backup (Postgres + D1 + cloudflare-local)…\n\n"}
        ok_pg, res_pg = _backup_postgres()
        yield {"type": "log", "text": f"Postgres: {res_pg}\n"}
        ok_d1, res_d1 = _backup_d1_all()
        yield {"type": "log", "text": f"D1: {res_d1}\n\n"}
        yield {"type": "log", "text": "cloudflare-local backup:\n"}
        code, log = yield from _stream_ai_script("cloudflare-local", "backup", timeout=600)
        ok = ok_pg and ok_d1 and code == 0
        yield _emit_done(ok, exit_code=0 if ok else 1, log=(log or "")[-8000:])
        return
    if action not in _ECOSYSTEM_BULK_BASH_ACTIONS:
        yield _emit_done(False, error=f"action {action} not supported for ecosystem bulk")
        return
    core_sh = os.path.join(PROJECT_ROOT, "ai-stack", "core.sh")
    if not os.path.isfile(core_sh):
        yield _emit_done(False, error=f"missing {core_sh}")
        return
    src = f"source {shlex.quote(core_sh)} && bulk_ecosystem {shlex.quote(action)}"
    code, log = yield from _yield_run(["/bin/bash", "-c", src], cwd=PROJECT_ROOT, timeout=3600)
    yield _emit_done(code == 0, exit_code=code, log=log[-12000:])


def _stream_stack_cf_all_stream(action: str) -> Iterator[dict[str, Any]]:
    if action == "deploy":
        code, log = yield from _stream_compose(["up", "-d", "--build"])
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "stop":
        code, log = yield from _stream_compose(["stop"])
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "restart":
        code, log = yield from _stream_compose(["restart"])
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "remove":
        code, log = yield from _stream_compose(["down", "--remove-orphans"])
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "reset":
        code, log = yield from _stream_compose(["down", "-v", "--remove-orphans"])
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "backup":
        yield {"type": "log", "text": "Backing up D1 databases…\n"}
        ok, res = _backup_d1_all()
        yield _emit_done(ok, detail=res)
        return
    yield _emit_done(False, error=f"action {action} not supported for full stack")


def _stream_cf_service_action_stream(meta: dict, action: str) -> Iterator[dict[str, Any]]:
    svc = meta["compose_service"]
    cname = meta["container"]

    if action == "deploy":
        code, log = yield from _stream_compose(["up", "-d", "--build", svc])
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "recreate":
        code, log = yield from _stream_compose(["up", "-d", "--force-recreate", "--no-deps", svc])
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "reset":
        yield {"type": "log", "text": f"=== compose stop {svc} ===\n"}
        code1, log1 = yield from _stream_compose(["stop", svc])
        yield {"type": "log", "text": f"=== compose rm {svc} ===\n"}
        code2, log2 = yield from _stream_compose(["rm", "-sf", svc])
        yield {"type": "log", "text": f"=== compose up {svc} ===\n"}
        code3, log3 = yield from _stream_compose(["up", "-d", svc])
        log = f"{log1}\n{log2}\n{log3}"
        yield _emit_done(code1 == 0 and code2 == 0 and code3 == 0, exit_code=code3, log=log[-8000:])
        return
    if action == "remove":
        code, log = yield from _stream_compose(["rm", "-sf", svc])
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "backup":
        if svc == "d1-adapter":
            yield {"type": "log", "text": "Backing up D1 databases…\n"}
            ok, res = _backup_d1_all()
            yield _emit_done(ok, detail=res)
            return
        yield _emit_done(False, error="backup only defined for d1-adapter or full stack")
        return

    if action == "start":
        code, log = yield from _stream_compose(["start", svc])
        if code != 0:
            yield {"type": "log", "text": f"=== compose up -d {svc} (start failed) ===\n"}
            code, log = yield from _stream_compose(["up", "-d", svc])
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return

    dc = _docker_client()
    if dc is None:
        yield _emit_done(False, error="docker unavailable")
        return
    try:
        c = dc.containers.get(cname)
    except Exception:
        yield _emit_done(False, error=f"container {cname} not found")
        return

    try:
        yield {"type": "log", "text": f"Docker {action} {cname} …\n"}
        if action == "stop":
            c.stop(timeout=30)
        elif action == "restart":
            c.restart(timeout=30)
        elif action == "pause":
            c.pause()
        elif action == "unpause":
            c.unpause()
        else:
            yield _emit_done(False, error=f"unsupported action {action}")
            return
        yield {"type": "log", "text": "Done.\n"}
        yield _emit_done(True, container=cname, action=action)
    except Exception as exc:
        yield _emit_done(False, error=str(exc))


def _stream_ai_service_action_stream(meta: dict, action: str) -> Iterator[dict[str, Any]]:
    script = meta["script"]
    cname = meta.get("container")

    if script == "cloudflare-local":
        yield from _stream_cloudflare_local(meta, action)
        return

    if action == "deploy":
        code, log = yield from _stream_ai_script(script, "start")
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "recreate":
        yield {"type": "log", "text": "=== remove ===\n"}
        code1, log1 = yield from _stream_ai_script(script, "remove")
        yield {"type": "log", "text": "=== start ===\n"}
        code2, log2 = yield from _stream_ai_script(script, "start")
        yield _emit_done(code1 == 0 and code2 == 0, exit_code=code2, log=(log1 + log2)[-8000:])
        return
    if action == "reset":
        if meta.get("reset_volume"):
            dc = _docker_client()
            if dc is None:
                yield _emit_done(False, error="docker unavailable")
                return
            vol = meta["reset_volume"]
            try:
                if cname:
                    yield {"type": "log", "text": f"Removing container {cname} (if present)…\n"}
                    try:
                        dc.containers.get(cname).remove(force=True)
                    except Exception:
                        pass
                yield {"type": "log", "text": f"Removing volume {vol} (if present)…\n"}
                try:
                    dc.volumes.get(vol).remove(force=True)
                except Exception:
                    pass
            except Exception as exc:
                yield _emit_done(False, error=str(exc))
                return
            code, log = yield from _stream_ai_script(script, "start")
            yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
            return
        code, log = yield from _stream_ai_script(script, "reset")
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "backup":
        if cname == "n8n_postgres":
            yield {"type": "log", "text": "Running pg_dump on n8n_postgres…\n"}
            ok, res = _backup_postgres()
            if ok:
                yield {"type": "log", "text": f"Wrote {res}\n"}
            else:
                yield {"type": "log", "text": f"{res}\n"}
            yield _emit_done(ok, detail=res if ok else None, error=None if ok else str(res))
            return
        yield _emit_done(False, error="no backup defined for this service")
        return

    if action in {"start", "stop", "restart", "remove", "pause", "unpause"}:
        code, log = yield from _stream_ai_script(script, action)
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return

    yield _emit_done(False, error=f"unsupported action {action}")


def _stream_cloudflare_local(_meta: dict, action: str) -> Iterator[dict[str, Any]]:
    if action == "deploy":
        code, log = yield from _stream_ai_script("cloudflare-local", "start")
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "stop":
        code, log = yield from _stream_ai_script("cloudflare-local", "stop")
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "restart":
        code, log = yield from _stream_ai_script("cloudflare-local", "restart")
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "remove":
        code, log = yield from _stream_ai_script("cloudflare-local", "remove")
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "reset":
        code, log = yield from _stream_ai_script("cloudflare-local", "reset")
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "backup":
        yield {"type": "log", "text": "Backing up D1 databases…\n"}
        ok, res = _backup_d1_all()
        yield _emit_done(ok, detail=res)
        return
    if action in {"start", "pause", "unpause"}:
        code, log = yield from _stream_ai_script("cloudflare-local", action)
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "recreate":
        yield {"type": "log", "text": "=== remove ===\n"}
        code1, log1 = yield from _stream_ai_script("cloudflare-local", "remove")
        yield {"type": "log", "text": "=== start ===\n"}
        code2, log2 = yield from _stream_ai_script("cloudflare-local", "start")
        yield _emit_done(code1 == 0 and code2 == 0, log=(log1 + log2)[-8000:])
        return
    yield _emit_done(False, error=f"unsupported action {action} for cloudflare-local script")
