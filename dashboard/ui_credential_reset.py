"""
Apply default UI credentials to running stack services (local dev only).
"""

from __future__ import annotations

import os
import shlex
import subprocess
from typing import Any

from ui_credentials import get_registry_entry, reset_vault_to_defaults
from ui_provision import provision_after_reset

PROJECT_ROOT = os.getenv("DASHBOARD_PROJECT_ROOT", "/project")
SERVICES_DIR = os.path.join(PROJECT_ROOT, "ecosystem-stack", "services")


def _run(cmd: list[str], timeout: int = 60) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode == 0, out.strip()[:2000]
    except subprocess.TimeoutExpired:
        return False, "command timed out"
    except Exception as exc:
        return False, str(exc)


def _container_running(name: str) -> bool:
    if not name:
        return False
    ok, msg = _run(["docker", "inspect", "-f", "{{.State.Running}}", name], timeout=15)
    return ok and msg.strip().lower() == "true"


def _restart_container(name: str | None) -> tuple[bool, str]:
    if not name:
        return False, "no container to restart"
    if not _container_running(name):
        return False, f"container {name} is not running"
    ok, msg = _run(["docker", "restart", name], timeout=120)
    return ok, msg or ("restarted" if ok else "restart failed")


def _recreate_compose_service(compose_rel: str, service: str) -> tuple[bool, str]:
    compose = os.path.join(PROJECT_ROOT, compose_rel)
    if not os.path.isfile(compose):
        return False, f"compose file missing: {compose}"
    return _run(
        ["docker", "compose", "-f", compose, "up", "-d", "--force-recreate", "--no-deps", service],
        timeout=180,
    )


def _start_service_script(script: str) -> tuple[bool, str]:
    """Start via ecosystem-stack/services/<script>.sh (build + run when container was removed)."""
    path = os.path.join(SERVICES_DIR, f"{script}.sh")
    if not os.path.isfile(path):
        return False, f"service script missing: {path}"
    root_q = shlex.quote(PROJECT_ROOT)
    path_q = shlex.quote(path)
    cmd = f"export PROJECT_ROOT={root_q} && source {path_q} && start"
    return _run(["/bin/bash", "-c", cmd], timeout=300)


def _reset_postgres(creds: dict[str, str], container: str) -> tuple[bool, str]:
    user = creds.get("username", "postgres")
    password = creds.get("password", "password")
    sql = f"ALTER USER {user} WITH PASSWORD '{password.replace(chr(39), chr(39) + chr(39))}';"
    ok, msg = _run(
        [
            "docker",
            "exec",
            container,
            "psql",
            "-U",
            user,
            "-d",
            creds.get("database") or "postgres",
            "-c",
            sql,
        ]
    )
    return ok, msg


def _reset_mysql(creds: dict[str, str], container: str) -> tuple[bool, str]:
    user = creds.get("username", "root")
    password = creds.get("password", "localdev")
    # Try without old password first (socket auth inside container).
    sql = (
        f"ALTER USER '{user}'@'%' IDENTIFIED BY '{password.replace(chr(39), chr(39) + chr(39))}'; "
        f"ALTER USER '{user}'@'localhost' IDENTIFIED BY '{password.replace(chr(39), chr(39) + chr(39))}'; "
        "FLUSH PRIVILEGES;"
    )
    ok, msg = _run(["docker", "exec", container, "mysql", "-uroot", "-e", sql])
    if not ok:
        ok, msg = _run(
            [
                "docker",
                "exec",
                container,
                "mysql",
                "-uroot",
                f"-p{password}",
                "-e",
                "SELECT 1",
            ]
        )
        if ok:
            ok, msg = _run(["docker", "exec", container, "mysql", "-uroot", f"-p{password}", "-e", sql])
    return ok, msg


def _docker_rm_quiet(name: str) -> None:
    _run(["/bin/sh", "-c", f"docker rm -f {shlex.quote(name)} 2>/dev/null || true"], timeout=30)


def _reset_n8n_postgres_db() -> tuple[bool, str]:
    """n8n stores owners in Postgres when DB_TYPE=postgresdb — wiping n8n_data alone is not enough."""
    _docker_rm_quiet("n8n")
    drop_ok, drop_msg = _run(
        [
            "docker",
            "exec",
            "n8n_postgres",
            "psql",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-c",
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'n8n' AND pid <> pg_backend_pid();",
        ],
        timeout=30,
    )
    _run(
        [
            "docker",
            "exec",
            "n8n_postgres",
            "psql",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-c",
            "DROP DATABASE IF EXISTS n8n;",
        ],
        timeout=30,
    )
    create_ok, create_msg = _run(
        [
            "docker",
            "exec",
            "n8n_postgres",
            "psql",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-c",
            "CREATE DATABASE n8n;",
        ],
        timeout=30,
    )
    if not create_ok:
        return False, create_msg or drop_msg or "could not recreate n8n database"
    return True, "n8n postgres database recreated"


def _reset_n8n_volume(creds: dict[str, str], _container: str) -> tuple[bool, str]:
    """Wipe n8n Postgres DB + data volume, start n8n, provision owner from vault."""
    db_ok, db_msg = _reset_n8n_postgres_db()
    if not db_ok:
        return False, db_msg
    vol_ok, vol_msg = _run(["docker", "volume", "rm", "n8n_data"], timeout=30)
    if not vol_ok and "no such volume" not in vol_msg.lower():
        return False, vol_msg
    started, start_msg = _start_service_script("n8n")
    if not started:
        return False, start_msg or vol_msg
    prov_ok, prov_msg = provision_after_reset("n8n", creds)
    if prov_ok:
        return True, f"n8n data reset and owner provisioned. {prov_msg}"
    return False, f"n8n data reset but account setup failed: {prov_msg}"


def _reset_webui_volume(creds: dict[str, str], _container: str) -> tuple[bool, str]:
    """Wipe open-webui volume so the next visit runs first-user signup again."""
    _docker_rm_quiet("open-webui")
    vol_ok, vol_msg = _run(["docker", "volume", "rm", "open-webui"], timeout=30)
    if not vol_ok and "no such volume" not in vol_msg.lower():
        return False, vol_msg
    started, start_msg = _start_service_script("webui")
    if not started:
        return False, start_msg or vol_msg
    prov_ok, prov_msg = provision_after_reset("webui", creds)
    if prov_ok:
        return True, f"Open WebUI data reset and admin provisioned. {prov_msg}"
    return False, f"Open WebUI data reset but signup failed: {prov_msg}"


def _reset_minio(creds: dict[str, str], container: str) -> tuple[bool, str]:
    access = creds.get("username", "minioadmin")
    secret = creds.get("password", "minioadmin")
    # mc inside minio image: re-add root user with new secret key.
    alias_cmd = [
        "docker",
        "exec",
        container,
        "sh",
        "-c",
        f"mc alias set local http://127.0.0.1:9000 {access} {secret} 2>/dev/null || true",
    ]
    _run(alias_cmd)
    rm_ok, _ = _run(
        [
            "docker",
            "exec",
            container,
            "sh",
            "-c",
            f"mc admin user rm local {access} 2>/dev/null || true",
        ]
    )
    add_ok, add_msg = _run(
        [
            "docker",
            "exec",
            container,
            "sh",
            "-c",
            f"mc admin user add local {access} {secret}",
        ]
    )
    if add_ok:
        recreated, rec_msg = _recreate_compose_service("cloudflare-local/docker-compose.yml", "minio")
        if recreated:
            return True, "MinIO credentials applied and console recreated."
        return True, f"MinIO credentials applied (recreate warning: {rec_msg})"
    probe_ok, probe_msg = _run(
        [
            "docker",
            "exec",
            container,
            "sh",
            "-c",
            f"mc alias set local http://127.0.0.1:9000 {access} {secret} && mc admin info local",
        ]
    )
    if probe_ok:
        recreated, rec_msg = _recreate_compose_service("cloudflare-local/docker-compose.yml", "minio")
        msg = "MinIO credentials verified."
        if not recreated:
            msg += f" Recreate minio to fix console login: {rec_msg}"
        return True, msg
    recreated, rec_msg = _recreate_compose_service("cloudflare-local/docker-compose.yml", "minio")
    if recreated:
        return True, "MinIO recreated with stack defaults (console login should work after ~30s)."
    return False, probe_msg or add_msg or rec_msg


_HANDLERS = {
    "postgres": _reset_postgres,
    "mysql": _reset_mysql,
    "minio": _reset_minio,
    "n8n_volume": _reset_n8n_volume,
    "webui_volume": _reset_webui_volume,
}


def apply_reset(slug: str) -> dict[str, Any]:
    entry = get_registry_entry(slug)
    if not entry:
        return {"ok": False, "error": f"unknown slug: {slug}"}
    handler_name = entry.get("reset_handler") or "none"
    if handler_name == "none":
        creds = reset_vault_to_defaults(slug)
        return {
            "ok": True,
            "slug": slug,
            "vault_reset": True,
            "applied": False,
            "restarted": False,
            "message": "Vault reset to defaults (no service apply handler).",
        }
    creds = reset_vault_to_defaults(slug)
    container = str(entry.get("container") or "").strip()
    fn = _HANDLERS.get(handler_name)
    if not fn:
        return {"ok": False, "error": f"unsupported reset handler: {handler_name}"}
    applied, apply_msg = fn(creds, container)
    restarted = False
    restart_msg = ""
    restart_name = entry.get("restart_service") or container
    if handler_name in ("n8n_volume", "webui_volume"):
        restarted = applied
    elif restart_name and applied:
        restarted, restart_msg = _restart_container(str(restart_name))
    return {
        "ok": applied,
        "slug": slug,
        "vault_reset": True,
        "applied": applied,
        "restarted": restarted,
        "message": apply_msg,
        "restart_message": restart_msg,
        "error": None if applied else apply_msg,
    }
