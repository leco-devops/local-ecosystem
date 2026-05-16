import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import docker
import requests

from control_targets import AI_TARGETS, CF_TARGETS, COMPOSE_REL, INFRA_COMPOSE_REL, INFRA_TARGETS
from leco_control import resolve_leco_target
from leco_subprocess import run_leco_app

PROJECT_ROOT = os.getenv("DASHBOARD_PROJECT_ROOT", "/project")
COMPOSE_FILE = os.path.join(PROJECT_ROOT, COMPOSE_REL)
INFRA_COMPOSE_FILE = os.path.join(PROJECT_ROOT, INFRA_COMPOSE_REL)
SERVICES_DIR = os.path.join(PROJECT_ROOT, "ecosystem-stack", "services")
CONTROL_TOKEN = os.getenv("DASHBOARD_CONTROL_TOKEN", "").strip()

D1_BASE = os.getenv("DASHBOARD_D1_URL", "http://d1-adapter:8083").rstrip("/")
BACKUP_DIR = os.path.join(PROJECT_ROOT, ".local-eco-backups")
os.makedirs(BACKUP_DIR, mode=0o755, exist_ok=True)

ALLOWED_ACTIONS = frozenset(
    {"start", "stop", "restart", "remove", "pause", "unpause", "deploy", "recreate", "reset", "backup", "staging"}
)

# Subset passed as a shell function name after `source` (must be identifier-safe).
_AI_SCRIPT_FN_ACTIONS = frozenset(
    {"start", "stop", "restart", "remove", "pause", "unpause", "reset"}
)

_BY_ID = {t["id"]: t for t in CF_TARGETS + AI_TARGETS + INFRA_TARGETS}


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
            "label": "All ecosystem-stack services (bulk)",
            "group": "ecosystem",
            "container": None,
            "actions": sorted(ALLOWED_ACTIONS),
            "runtime": {
                "kind": "stack",
                "status": "bulk",
                "label": "Full action set via ecosystem-stack/core.sh (stop/pause/remove/reset/recreate skip this dashboard so the API can finish)",
                "running": None,
            },
        }
    ]
    for t in AI_TARGETS:
        entry = {
            "id": t["id"],
            "label": t["label"],
            "group": "ecosystem-stack",
            "container": t.get("container"),
            "actions": sorted(ALLOWED_ACTIONS),
            "runtime": _container_runtime(dc, t.get("container")),
        }
        if t.get("container") == "service-dashboard" and t["script"] == "dashboard":
            entry["actions"] = sorted(a for a in ALLOWED_ACTIONS if a not in {"remove", "reset"})
        out.append(entry)
    # Infra before Cloudflare so Control UI shows MySQL, Adminer, etc. without scrolling past the CF stack.
    for t in INFRA_TARGETS:
        out.append(
            {
                "id": t["id"],
                "label": t["label"],
                "group": "infra",
                "container": t["container"],
                "actions": sorted(ALLOWED_ACTIONS),
                "runtime": _container_runtime(dc, t.get("container")),
            }
        )
    out.append(
        {
            "id": "stack-infra-all",
            "label": "Infra stack — entire compose",
            "group": "infra",
            "container": None,
            "actions": ["deploy", "stop", "restart", "remove", "reset", "backup"],
            "runtime": {
                "kind": "stack",
                "status": "compose",
                "label": "Whole infra/docker-compose.yml — status per service above",
                "running": None,
            },
        }
    )
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


def _line_buffered_cmd(cmd: list) -> list:
    """Force line-buffered stdio for piped subprocesses (live Control UI logs).

    Without this, bash/docker often use full block buffering when stdout is a pipe,
    so the dashboard shows no text until the command finishes or the buffer fills.
    """
    if not cmd:
        return cmd
    stdbuf = shutil.which("stdbuf")
    if not stdbuf:
        return cmd
    # docker compose / docker: Go CLI + child processes benefit from line-buffered pipes
    if cmd[0] == "docker":
        return [stdbuf, "-oL", "-eL"] + cmd
    if len(cmd) >= 2 and cmd[0] == "/bin/bash" and cmd[1] == "-c":
        return [stdbuf, "-oL", "-eL"] + cmd
    return cmd


def _yield_run(cmd, cwd=None, timeout=600) -> Iterator[dict[str, Any]]:
    """Stream merged stdout/stderr as log events; return (exit_code, full_text) via StopIteration."""
    cmd = _line_buffered_cmd(list(cmd))
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
            # Smaller reads: with line-buffered children, lines arrive promptly; avoids waiting for 8KiB.
            chunk = proc.stdout.read(1024)
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


def _infra_compose(args, timeout=600):
    if not os.path.isfile(INFRA_COMPOSE_FILE):
        return 1, f"compose file missing: {INFRA_COMPOSE_FILE}"
    return _run(
        ["docker", "compose", "-f", INFRA_COMPOSE_FILE, *args],
        cwd=os.path.dirname(INFRA_COMPOSE_FILE),
        timeout=timeout,
    )


def _ai_script(script, action, timeout=600):
    path = os.path.join(SERVICES_DIR, f"{script}.sh")
    if not os.path.isfile(path):
        return 1, f"script missing: {path}"
    if action not in _AI_SCRIPT_FN_ACTIONS:
        return 1, f"action not invokable as service function: {action}"
    # Service scripts define bash functions; ecosystem-stack/core.sh uses `source` + call.
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

    tid = (target_id or "").strip()
    if not tid:
        return {
            "ok": False,
            "error": "missing target_id — UI sent an empty control target; refresh the page or re-open the Hosted apps tab.",
        }

    if tid == "stack-cf-all":
        return _stack_cf_all(action)

    if tid == "stack-infra-all":
        return _stack_infra_all(action)

    if tid == "stack-ecosystem-all":
        return _stack_ecosystem_all(action)

    leco_m = resolve_leco_target(tid)
    if leco_m:
        return _leco_stack_action(leco_m, action)

    meta = _BY_ID.get(tid)
    if not meta:
        return {
            "ok": False,
            "error": (
                f"unknown target {tid!r} — not a LEco stack (expected leco-stack-<registry id>), "
                "infra/cf bulk target, or dashboard service id. Refresh the UI if controls look stale."
            ),
        }

    if "compose_service" in meta:
        if meta.get("compose_project") == "infra":
            return _infra_service_action(meta, action)
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
    core_sh = os.path.join(PROJECT_ROOT, "ecosystem-stack", "core.sh")
    if not os.path.isfile(core_sh):
        return {"ok": False, "error": f"missing {core_sh}"}
    src = f"source {shlex.quote(core_sh)} && bulk_ecosystem {shlex.quote(action)}"
    code, log = _run(["/bin/bash", "-c", src], cwd=PROJECT_ROOT, timeout=3600)
    return {"ok": code == 0, "exit_code": code, "log": log[-12000:]}


def _stack_infra_all(action: str):
    if action == "deploy":
        code, log = _infra_compose(["up", "-d", "--build"])
        return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
    if action == "stop":
        code, log = _infra_compose(["stop"])
        return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
    if action == "restart":
        code, log = _infra_compose(["restart"])
        return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
    if action == "remove":
        code, log = _infra_compose(["down", "--remove-orphans"])
        return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
    if action == "reset":
        code, log = _infra_compose(["down", "-v", "--remove-orphans"])
        return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
    if action == "backup":
        return {"ok": False, "error": "no backup defined for infra stack"}
    return {"ok": False, "error": f"action {action} not supported for infra stack"}


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


def _leco_compose_run(meta: dict, args: list, *, timeout: int) -> tuple[int, str]:
    tail = meta["compose_tail"]
    root = meta["root"]
    return _run(["docker", "compose", *tail, *args], cwd=root, timeout=timeout)


def _leco_app_manifest_run(
    meta: dict,
    subcommand: str,
    extra_args: list | None = None,
    *,
    timeout: int,
) -> tuple[int, str]:
    """Run LEco DevOps CLI (leco-devops) with --manifest (deploy/stop/down)."""
    mp = meta["manifest_path"]
    # leco-devops runs in this container; cwd must be the manifest directory (exists here).
    leco_cwd = str(Path(mp).resolve().parent)
    # Refresh the local-runtime overlay so its bind-mount paths reflect the
    # *current* host-side mapping (LECO_WORKSPACE_PARENT_HOST / LECO_PROJECT_ROOT_HOST).
    # Idempotent — a no-op when the manifest declares no runtimes.
    if subcommand in {"deploy"}:
        try:
            from leco_detect import ensure_local_runtime_overlay
            ensure_local_runtime_overlay(Path(mp))
        except Exception:
            pass
    argv = [subcommand, "--manifest", mp]
    if extra_args:
        argv.extend(extra_args)
    code, out, err = run_leco_app(argv, cwd=leco_cwd, timeout=timeout)
    log = ((out or "") + ("\n" if out and err else "") + (err or "")).strip() or "(no output)"
    return code, log


def _leco_autooffboard_after_teardown(meta: dict[str, Any], *, compose_volumes: bool = False) -> dict[str, Any]:
    """Hosted offboard via leco-devops ecosystem-unregister: local CF teardown, compose down, Traefik strip, registry (order fixed in CLI)."""
    slug = str(meta.get("leco_slug") or "").strip()
    if not slug:
        return {"ok": False, "error": "missing leco_slug"}
    from hosted_offboard import offboard_hosted_app

    return offboard_hosted_app(
        slug,
        strip_traefik=True,
        clean_local_cf=True,
        compose_down=True,
        compose_volumes=compose_volumes,
    )


def _leco_offboard_log(offboard: dict[str, Any]) -> str:
    return ((offboard.get("leco_log") or "").strip())[-12000:]


def _leco_stack_action(meta: dict, action: str) -> dict:
    """leco.app.yaml stack: LEco DevOps deploy/stop/down where supported; compose for restart/recreate/pause."""
    if action == "backup":
        return {"ok": False, "error": "backup not defined for leco compose stacks"}
    to = 3600 if action in {"deploy", "recreate"} else 600
    if action == "deploy":
        code, log = _leco_app_manifest_run(meta, "deploy", timeout=to)
        return {"ok": code == 0, "exit_code": code, "log": log[-12000:]}
    if action == "recreate":
        # leco-devops has no force-recreate; use compose directly
        code, log = _leco_compose_run(meta, ["up", "-d", "--force-recreate"], timeout=to)
        return {"ok": code == 0, "exit_code": code, "log": log[-12000:]}
    if action == "stop":
        code, log = _leco_app_manifest_run(meta, "stop", timeout=to)
        return {"ok": code == 0, "exit_code": code, "log": log[-12000:]}
    if action == "restart":
        code, log = _leco_compose_run(meta, ["restart"], timeout=to)
        return {"ok": code == 0, "exit_code": code, "log": log[-12000:]}
    if action == "remove":
        ob = _leco_autooffboard_after_teardown(meta, compose_volumes=False)
        out: dict[str, Any] = {
            "ok": bool(ob.get("ok")),
            "exit_code": int(ob.get("exit_code") or (0 if ob.get("ok") else 1)),
            "log": _leco_offboard_log(ob),
            "offboard": ob,
        }
        if not out["ok"] and ob.get("error"):
            out["error"] = ob["error"]
        return out
    if action == "reset":
        ob = _leco_autooffboard_after_teardown(meta, compose_volumes=True)
        out = {
            "ok": bool(ob.get("ok")),
            "exit_code": int(ob.get("exit_code") or (0 if ob.get("ok") else 1)),
            "log": _leco_offboard_log(ob),
            "offboard": ob,
        }
        if not out["ok"] and ob.get("error"):
            out["error"] = ob["error"]
        return out
    if action == "start":
        code, log = _leco_compose_run(meta, ["start"], timeout=to)
        if code != 0:
            code, log = _leco_compose_run(meta, ["up", "-d"], timeout=to)
        return {"ok": code == 0, "exit_code": code, "log": log[-12000:]}
    if action == "pause":
        code, log = _leco_compose_run(meta, ["pause"], timeout=to)
        return {"ok": code == 0, "exit_code": code, "log": log[-12000:]}
    if action == "unpause":
        code, log = _leco_compose_run(meta, ["unpause"], timeout=to)
        return {"ok": code == 0, "exit_code": code, "log": log[-12000:]}
    if action == "staging":
        # Tear down containers + volumes but keep hosting config files.
        # 1. docker compose down -v --remove-orphans
        code, log = _leco_compose_run(meta, ["down", "-v", "--remove-orphans"], timeout=to)
        # 2. Strip Traefik routes so the hostname is freed.
        try:
            from traefik_dynamic_file import read_dynamic, strip_router_service_keys
            slug = str(meta.get("leco_slug") or "").strip()
            if slug:
                data = read_dynamic() or {}
                http = data.get("http") or {}
                rkeys = [k for k in (http.get("routers") or {}) if k.startswith(slug + "-")]
                skeys = [k for k in (http.get("services") or {}) if k.startswith(slug + "-")]
                if rkeys or skeys:
                    strip_router_service_keys(rkeys, skeys)
                    log += f"\nTraefik routes stripped for staging ({len(rkeys)} routers, {len(skeys)} services)."
        except Exception as exc:
            log += f"\nWarning: could not strip Traefik routes: {exc}"
        return {"ok": code == 0, "exit_code": code, "log": log[-12000:]}
    return {"ok": False, "error": f"unsupported action {action} for leco stack"}


def _stream_leco_compose(meta: dict, args: list, *, timeout: int) -> Iterator[dict[str, Any] | Any]:
    tail = meta["compose_tail"]
    root = meta["root"]
    code, log = yield from _yield_run(["docker", "compose", *tail, *args], cwd=root, timeout=timeout)
    return (code, log)


def _stream_leco_stack_action(meta: dict, action: str) -> Iterator[dict[str, Any]]:
    if action == "backup":
        yield _emit_done(False, error="backup not defined for leco compose stacks")
        return
    to = 3600 if action in {"deploy", "recreate"} else 600
    if action == "deploy":
        code, log = _leco_app_manifest_run(meta, "deploy", timeout=to)
        if log:
            yield {"type": "log", "text": log}
        yield _emit_done(code == 0, exit_code=code, log=log[-12000:])
        return
    if action == "recreate":
        code, log = yield from _stream_leco_compose(meta, ["up", "-d", "--force-recreate"], timeout=to)
        yield _emit_done(code == 0, exit_code=code, log=log[-12000:])
        return
    if action == "stop":
        code, log = _leco_app_manifest_run(meta, "stop", timeout=to)
        if log:
            yield {"type": "log", "text": log}
        yield _emit_done(code == 0, exit_code=code, log=log[-12000:])
        return
    if action == "restart":
        code, log = yield from _stream_leco_compose(meta, ["restart"], timeout=to)
        yield _emit_done(code == 0, exit_code=code, log=log[-12000:])
        return
    if action == "remove":
        ob = _leco_autooffboard_after_teardown(meta, compose_volumes=False)
        if ob.get("leco_log"):
            yield {"type": "log", "text": ob["leco_log"]}
        ok_done = bool(ob.get("ok"))
        ec = int(ob.get("exit_code") or (0 if ok_done else 1))
        extra: dict[str, Any] = {
            "exit_code": ec,
            "log": _leco_offboard_log(ob),
            "offboard": ob,
        }
        if not ok_done and ob.get("error"):
            extra["error"] = ob["error"]
        yield _emit_done(ok_done, **extra)
        return
    if action == "reset":
        ob = _leco_autooffboard_after_teardown(meta, compose_volumes=True)
        if ob.get("leco_log"):
            yield {"type": "log", "text": ob["leco_log"]}
        ok_done = bool(ob.get("ok"))
        ec = int(ob.get("exit_code") or (0 if ok_done else 1))
        extra = {
            "exit_code": ec,
            "log": _leco_offboard_log(ob),
            "offboard": ob,
        }
        if not ok_done and ob.get("error"):
            extra["error"] = ob["error"]
        yield _emit_done(ok_done, **extra)
        return
    if action == "start":
        code, log = yield from _stream_leco_compose(meta, ["start"], timeout=to)
        if code != 0:
            code, log = yield from _stream_leco_compose(meta, ["up", "-d"], timeout=to)
        yield _emit_done(code == 0, exit_code=code, log=log[-12000:])
        return
    if action == "pause":
        code, log = yield from _stream_leco_compose(meta, ["pause"], timeout=to)
        yield _emit_done(code == 0, exit_code=code, log=log[-12000:])
        return
    if action == "unpause":
        code, log = yield from _stream_leco_compose(meta, ["unpause"], timeout=to)
        yield _emit_done(code == 0, exit_code=code, log=log[-12000:])
        return
    if action == "staging":
        code, log = yield from _stream_leco_compose(meta, ["down", "-v", "--remove-orphans"], timeout=to)
        try:
            from traefik_dynamic_file import read_dynamic, strip_router_service_keys
            slug = str(meta.get("leco_slug") or "").strip()
            if slug:
                data = read_dynamic() or {}
                http = data.get("http") or {}
                rkeys = [k for k in (http.get("routers") or {}) if k.startswith(slug + "-")]
                skeys = [k for k in (http.get("services") or {}) if k.startswith(slug + "-")]
                if rkeys or skeys:
                    strip_router_service_keys(rkeys, skeys)
                    yield {"type": "log", "text": f"Traefik routes stripped for staging ({len(rkeys)} routers, {len(skeys)} services).\n"}
        except Exception as exc:
            yield {"type": "log", "text": f"Warning: could not strip Traefik routes: {exc}\n"}
        yield _emit_done(code == 0, exit_code=code, log=log[-12000:])
        return
    yield _emit_done(False, error=f"unsupported action {action} for leco stack")


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


def _infra_service_action(meta: dict, action: str):
    svc = meta["compose_service"]
    cname = meta["container"]

    if action == "deploy":
        code, log = _infra_compose(["up", "-d", "--build", svc])
        return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
    if action == "recreate":
        code, log = _infra_compose(["up", "-d", "--force-recreate", "--no-deps", svc])
        return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
    if action == "reset":
        code1, log1 = _infra_compose(["stop", svc])
        code2, log2 = _infra_compose(["rm", "-sf", svc])
        code3, log3 = _infra_compose(["up", "-d", svc])
        log = f"{log1}\n{log2}\n{log3}"
        return {"ok": code1 == 0 and code2 == 0 and code3 == 0, "exit_code": code3, "log": log[-8000:]}
    if action == "remove":
        code, log = _infra_compose(["rm", "-sf", svc])
        return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
    if action == "backup":
        return {"ok": False, "error": "backup not defined for infra services"}

    if action == "start":
        code, log = _infra_compose(["start", svc])
        if code != 0:
            code, log = _infra_compose(["up", "-d", svc])
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

    if script == "infra":
        if action == "deploy":
            code, log = _ai_script("infra", "start")
            return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
        if action == "stop":
            code, log = _ai_script("infra", "stop")
            return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
        if action == "restart":
            code, log = _ai_script("infra", "restart")
            return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
        if action == "remove":
            code, log = _ai_script("infra", "remove")
            return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
        if action == "reset":
            code, log = _ai_script("infra", "reset")
            return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
        if action in {"start", "pause", "unpause"}:
            code, log = _ai_script("infra", action)
            return {"ok": code == 0, "exit_code": code, "log": log[-8000:]}
        if action == "recreate":
            code1, log1 = _ai_script("infra", "remove")
            code2, log2 = _ai_script("infra", "start")
            return {"ok": code1 == 0 and code2 == 0, "log": (log1 + log2)[-8000:]}
        if action == "backup":
            return {"ok": False, "error": "no backup defined for infra stack"}
        return {"ok": False, "error": f"unsupported action {action} for infra script"}

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


def _stream_infra_compose(args: list, timeout: int = 600) -> Iterator[dict[str, Any] | Any]:
    if not os.path.isfile(INFRA_COMPOSE_FILE):
        yield {"type": "log", "text": f"compose file missing: {INFRA_COMPOSE_FILE}\n"}
        return (1, "")
    code, log = yield from _yield_run(
        ["docker", "compose", "-f", INFRA_COMPOSE_FILE, *args],
        cwd=os.path.dirname(INFRA_COMPOSE_FILE),
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

    tid = (target_id or "").strip()
    if not tid:
        yield _emit_done(
            False,
            error="missing target_id — UI sent an empty control target; refresh the page or re-open the Hosted apps tab.",
        )
        return

    if tid == "stack-cf-all":
        yield from _stream_stack_cf_all_stream(action)
        return
    if tid == "stack-infra-all":
        yield from _stream_stack_infra_all_stream(action)
        return
    if tid == "stack-ecosystem-all":
        yield from _stream_stack_ecosystem_all_stream(action)
        return

    leco_m = resolve_leco_target(tid)
    if leco_m:
        yield from _stream_leco_stack_action(leco_m, action)
        return

    meta = _BY_ID.get(tid)
    if not meta:
        yield _emit_done(
            False,
            error=(
                f"unknown target {tid!r} — not a LEco stack (expected leco-stack-<registry id>), "
                "infra/cf bulk target, or dashboard service id. Refresh the UI if controls look stale."
            ),
        )
        return

    if "compose_service" in meta:
        if meta.get("compose_project") == "infra":
            yield from _stream_infra_service_action_stream(meta, action)
        else:
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
    core_sh = os.path.join(PROJECT_ROOT, "ecosystem-stack", "core.sh")
    if not os.path.isfile(core_sh):
        yield _emit_done(False, error=f"missing {core_sh}")
        return
    yield {
        "type": "log",
        "text": (
            f"Running bulk_ecosystem {action} (stop phase skips dashboard container). "
            "First log lines should appear as each Docker call runs; full run can take several minutes.\n\n"
        ),
    }
    src = f"source {shlex.quote(core_sh)} && bulk_ecosystem {shlex.quote(action)}"
    code, log = yield from _yield_run(["/bin/bash", "-c", src], cwd=PROJECT_ROOT, timeout=3600)
    yield _emit_done(code == 0, exit_code=code, log=log[-12000:])


def _stream_stack_infra_all_stream(action: str) -> Iterator[dict[str, Any]]:
    if action == "deploy":
        code, log = yield from _stream_infra_compose(["up", "-d", "--build"])
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "stop":
        code, log = yield from _stream_infra_compose(["stop"])
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "restart":
        code, log = yield from _stream_infra_compose(["restart"])
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "remove":
        code, log = yield from _stream_infra_compose(["down", "--remove-orphans"])
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "reset":
        code, log = yield from _stream_infra_compose(["down", "-v", "--remove-orphans"])
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "backup":
        yield _emit_done(False, error="no backup defined for infra stack")
        return
    yield _emit_done(False, error=f"action {action} not supported for infra stack")


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


def _stream_infra_service_action_stream(meta: dict, action: str) -> Iterator[dict[str, Any]]:
    svc = meta["compose_service"]
    cname = meta["container"]

    if action == "deploy":
        code, log = yield from _stream_infra_compose(["up", "-d", "--build", svc])
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "recreate":
        code, log = yield from _stream_infra_compose(["up", "-d", "--force-recreate", "--no-deps", svc])
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "reset":
        yield {"type": "log", "text": f"=== compose stop {svc} ===\n"}
        code1, log1 = yield from _stream_infra_compose(["stop", svc])
        yield {"type": "log", "text": f"=== compose rm {svc} ===\n"}
        code2, log2 = yield from _stream_infra_compose(["rm", "-sf", svc])
        yield {"type": "log", "text": f"=== compose up {svc} ===\n"}
        code3, log3 = yield from _stream_infra_compose(["up", "-d", svc])
        log = f"{log1}\n{log2}\n{log3}"
        yield _emit_done(code1 == 0 and code2 == 0 and code3 == 0, exit_code=code3, log=log[-8000:])
        return
    if action == "remove":
        code, log = yield from _stream_infra_compose(["rm", "-sf", svc])
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "backup":
        yield _emit_done(False, error="backup not defined for infra services")
        return

    if action == "start":
        code, log = yield from _stream_infra_compose(["start", svc])
        if code != 0:
            yield {"type": "log", "text": f"=== compose up -d {svc} (start failed) ===\n"}
            code, log = yield from _stream_infra_compose(["up", "-d", svc])
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

    if script == "infra":
        yield from _stream_infra_stack(meta, action)
        return

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


def _stream_infra_stack(_meta: dict, action: str) -> Iterator[dict[str, Any]]:
    if action == "deploy":
        code, log = yield from _stream_ai_script("infra", "start")
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "stop":
        code, log = yield from _stream_ai_script("infra", "stop")
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "restart":
        code, log = yield from _stream_ai_script("infra", "restart")
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "remove":
        code, log = yield from _stream_ai_script("infra", "remove")
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "reset":
        code, log = yield from _stream_ai_script("infra", "reset")
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action in {"start", "pause", "unpause"}:
        code, log = yield from _stream_ai_script("infra", action)
        yield _emit_done(code == 0, exit_code=code, log=log[-8000:])
        return
    if action == "recreate":
        yield {"type": "log", "text": "=== remove ===\n"}
        code1, log1 = yield from _stream_ai_script("infra", "remove")
        yield {"type": "log", "text": "=== start ===\n"}
        code2, log2 = yield from _stream_ai_script("infra", "start")
        yield _emit_done(code1 == 0 and code2 == 0, log=(log1 + log2)[-8000:])
        return
    if action == "backup":
        yield _emit_done(False, error="no backup defined for infra stack")
        return
    yield _emit_done(False, error=f"unsupported action {action} for infra script")


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
