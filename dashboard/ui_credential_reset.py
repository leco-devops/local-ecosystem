"""
Apply default UI credentials to running stack services (local dev only).
"""

from __future__ import annotations

import os
import shlex
import subprocess
from typing import Any

from ui_credentials import (
    DEFAULT_FILE_TRANSFER_PASSWORD,
    DEFAULT_FTP_PORT,
    DEFAULT_SFTP_PORT,
    get_registry_entry,
    reset_vault_to_defaults,
    _normalize_port,
)
from ui_provision import provision_after_reset

PROJECT_ROOT = os.getenv("DASHBOARD_PROJECT_ROOT", "/project")
SERVICES_DIR = os.path.join(PROJECT_ROOT, "ecosystem-stack", "services")


def _host_project_root() -> str:
    """Host filesystem path for docker compose bind mounts (dashboard runs in Docker with /project)."""
    for key in ("LECO_PROJECT_ROOT_HOST", "DASHBOARD_PROJECT_ROOT_HOST", "DASHBOARD_DOCKER_BIND_ROOT"):
        val = (os.getenv(key) or "").strip()
        if val and os.path.isdir(val):
            return val
    return PROJECT_ROOT


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


def _recreate_compose_service(
    compose_rel: str,
    service: str,
    *,
    env_rel: str | None = None,
    compose_extra: list[str] | None = None,
) -> tuple[bool, str]:
    root = _host_project_root()
    compose = os.path.join(root, compose_rel)
    if not os.path.isfile(compose):
        return False, f"compose file missing: {compose}"
    compose_dir = os.path.dirname(compose)
    cmd = ["docker", "compose", "-f", compose, "--project-directory", compose_dir]
    for extra_rel in compose_extra or []:
        extra = os.path.join(root, extra_rel)
        if os.path.isfile(extra):
            cmd.extend(["-f", extra])
    if env_rel:
        env_path = os.path.join(root, env_rel)
        if os.path.isfile(env_path):
            cmd.extend(["--env-file", env_path])
    cmd.extend(["up", "-d", "--force-recreate", "--no-deps", service])
    return _run(cmd, timeout=180)


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


FILE_TRANSFER_COMPOSE = "file-transfer/docker-compose.yml"
FILE_TRANSFER_SFTP_KEYS_COMPOSE = "file-transfer/docker-compose.sftp-keys.yml"
FILE_TRANSFER_VOLUME = "file-transfer_file_transfer_data"
FILE_TRANSFER_ENV = os.path.join(PROJECT_ROOT, "file-transfer", ".env")
FILE_TRANSFER_ENV_EXAMPLE = os.path.join(PROJECT_ROOT, "file-transfer", ".env.example")


def _sftp_keys_dir() -> str:
    return os.path.join(_host_project_root(), "file-transfer", "keys", "sftp")


def _ensure_file_transfer_keys_dir() -> None:
    os.makedirs(_sftp_keys_dir(), exist_ok=True)


def _sftp_pub_keys_present(username: str | None = None) -> bool:
    keys_dir = _sftp_keys_dir()
    if not os.path.isdir(keys_dir):
        return False
    user = str(username or "leco").strip() or "leco"
    if os.path.isfile(os.path.join(keys_dir, f"{user}.pub")):
        return True
    for name in os.listdir(keys_dir):
        if name.endswith(".pub") and os.path.isfile(os.path.join(keys_dir, name)):
            return True
    return False


def _sftp_compose_extra(creds: dict[str, str] | None = None) -> list[str]:
    auth_mode = _normalize_sftp_auth_mode((creds or {}).get("auth_mode"))
    user = (creds or {}).get("username", "leco")
    if auth_mode in ("key", "both") and _sftp_pub_keys_present(user):
        return [FILE_TRANSFER_SFTP_KEYS_COMPOSE]
    return []


def _prepare_sftp_data_volume(creds: dict[str, str]) -> tuple[bool, str]:
    auth_mode = _normalize_sftp_auth_mode(creds.get("auth_mode"))
    if auth_mode in ("key", "both") and _sftp_pub_keys_present(creds.get("username")):
        return True, "key auth volume ok"
    ok, msg = _run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{FILE_TRANSFER_VOLUME}:/home/leco",
            "alpine",
            "sh",
            "-c",
            "rm -rf /home/leco/.ssh",
        ],
        timeout=60,
    )
    return ok, msg or "cleared stale .ssh from SFTP data volume"


def _normalize_sftp_auth_mode(value: str | None) -> str:
    mode = str(value or "password").strip().lower()
    return mode if mode in ("password", "key", "both") else "password"


def _read_sftp_public_key(username: str) -> str:
    user = str(username or "leco").strip() or "leco"
    path = os.path.join(_sftp_keys_dir(), f"{user}.pub")
    if not os.path.isfile(path):
        return ""
    try:
        return open(path, encoding="utf-8").read().strip()
    except OSError:
        return ""


def _validate_openssh_public_key(value: str) -> tuple[bool, str]:
    key = " ".join(str(value or "").split())
    if not key:
        return False, "public_key is required for key-based SFTP auth"
    if not key.startswith(("ssh-rsa ", "ssh-ed25519 ", "ecdsa-", "ssh-dss ")):
        return False, "public_key must be an OpenSSH public key (ssh-ed25519, ssh-rsa, …)"
    return True, key


def _write_sftp_public_key(creds: dict[str, str]) -> tuple[bool, str]:
    user = creds.get("username", "leco")
    auth_mode = _normalize_sftp_auth_mode(creds.get("auth_mode"))
    key_path = os.path.join(_sftp_keys_dir(), f"{user}.pub")
    os.makedirs(_sftp_keys_dir(), exist_ok=True)

    if auth_mode in ("key", "both"):
        ok, key_or_msg = _validate_openssh_public_key(creds.get("public_key", ""))
        if not ok:
            existing = _read_sftp_public_key(user)
            if existing:
                key_or_msg = existing
            else:
                return False, key_or_msg
        try:
            with open(key_path, "w", encoding="utf-8") as f:
                f.write(str(key_or_msg).strip() + "\n")
            os.chmod(key_path, 0o644)
            return True, key_path
        except OSError as exc:
            return False, str(exc)

    if os.path.isfile(key_path):
        try:
            os.remove(key_path)
        except OSError as exc:
            return False, str(exc)
    return True, "password auth (no public key file)"


def _remove_sftp_public_key(username: str) -> None:
    path = os.path.join(_sftp_keys_dir(), f"{str(username or 'leco').strip() or 'leco'}.pub")
    if os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass


def _parse_env_lines(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip()
    return out


def _format_env_lines(values: dict[str, str]) -> str:
    return "\n".join(f"{k}={v}" for k, v in sorted(values.items())) + "\n"


def _load_file_transfer_env() -> dict[str, str]:
    path = FILE_TRANSFER_ENV if os.path.isfile(FILE_TRANSFER_ENV) else FILE_TRANSFER_ENV_EXAMPLE
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return _parse_env_lines(f.read())
    except OSError:
        return {}


def _write_file_transfer_env(updates: dict[str, str]) -> tuple[bool, str]:
    merged = _load_file_transfer_env()
    merged.update({k: str(v) for k, v in updates.items() if v is not None})
    os.makedirs(os.path.dirname(FILE_TRANSFER_ENV), exist_ok=True)
    try:
        with open(FILE_TRANSFER_ENV, "w", encoding="utf-8") as f:
            f.write("# Managed by LEco DevOps UI access (local dev)\n")
            f.write(_format_env_lines(merged))
        try:
            os.chmod(FILE_TRANSFER_ENV, 0o600)
        except OSError:
            pass
        return True, FILE_TRANSFER_ENV
    except OSError as exc:
        return False, str(exc)


def _sftp_users_value(creds: dict[str, str]) -> str:
    user = creds.get("username", "leco")
    auth_mode = _normalize_sftp_auth_mode(creds.get("auth_mode"))
    password = creds.get("password", DEFAULT_FILE_TRANSFER_PASSWORD)
    if auth_mode == "key":
        password = ""
    return f"{user}:{password}:1000:1000"


def _sftp_env_updates(creds: dict[str, str]) -> dict[str, str]:
    user = creds.get("username", "leco")
    auth_mode = _normalize_sftp_auth_mode(creds.get("auth_mode"))
    port = _normalize_port(creds.get("port"), DEFAULT_SFTP_PORT)
    return {
        "SFTP_USER": user,
        "SFTP_AUTH_MODE": auth_mode,
        "SFTP_PORT": port,
        "SFTP_USERS": _sftp_users_value(creds),
    }


def _ftp_env_updates(creds: dict[str, str]) -> dict[str, str]:
    port = _normalize_port(creds.get("port"), DEFAULT_FTP_PORT)
    return {
        "FTP_USERS": _ftp_users_value(creds),
        "FTP_PORT": port,
    }


def _apply_file_transfer_sftp(creds: dict[str, str], _container: str = "") -> tuple[bool, str]:
    auth_mode = _normalize_sftp_auth_mode(creds.get("auth_mode"))
    if auth_mode in ("key", "both"):
        ok, key_or_msg = _validate_openssh_public_key(creds.get("public_key", ""))
        if not ok and not _read_sftp_public_key(creds.get("username", "leco")):
            return False, key_or_msg
    try:
        updates = _sftp_env_updates(creds)
    except ValueError as exc:
        return False, str(exc)
    ok, msg = _write_file_transfer_env(updates)
    if not ok:
        return False, msg
    ok, key_msg = _write_sftp_public_key(creds)
    if not ok:
        return False, key_msg
    _ensure_file_transfer_keys_dir()
    _prepare_sftp_data_volume(creds)
    recreated, rec_msg = _recreate_compose_service(
        FILE_TRANSFER_COMPOSE,
        "sftp",
        env_rel="file-transfer/.env",
        compose_extra=_sftp_compose_extra(creds),
    )
    if recreated:
        return True, f"SFTP recreated ({auth_mode} auth, port {updates['SFTP_PORT']}). {key_msg}"
    return False, rec_msg


def _ftp_users_value(creds: dict[str, str]) -> str:
    user = creds.get("username", "leco")
    password = creds.get("password", DEFAULT_FILE_TRANSFER_PASSWORD)
    return f"{user}|{password}|/home/leco"


def _apply_file_transfer_ftp(creds: dict[str, str], _container: str = "") -> tuple[bool, str]:
    try:
        updates = _ftp_env_updates(creds)
    except ValueError as exc:
        return False, str(exc)
    ok, msg = _write_file_transfer_env(updates)
    if not ok:
        return False, msg
    _ensure_file_transfer_keys_dir()
    recreated, rec_msg = _recreate_compose_service(
        FILE_TRANSFER_COMPOSE,
        "ftp",
        env_rel="file-transfer/.env",
    )
    if recreated:
        return True, f"FTP recreated on port {updates['FTP_PORT']}."
    return False, rec_msg


_HANDLERS = {
    "postgres": _reset_postgres,
    "mysql": _reset_mysql,
    "minio": _reset_minio,
    "n8n_volume": _reset_n8n_volume,
    "webui_volume": _reset_webui_volume,
    "file_transfer_sftp": _apply_file_transfer_sftp,
    "file_transfer_ftp": _apply_file_transfer_ftp,
}


def apply_saved_credentials(slug: str) -> dict[str, Any]:
    """Apply vault credentials to a running service without resetting vault to defaults."""
    from ui_credentials import credentials_for_assist

    entry = get_registry_entry(slug)
    if not entry:
        return {"ok": False, "error": f"unknown slug: {slug}"}
    handler_name = entry.get("reset_handler") or "none"
    fn = _HANDLERS.get(handler_name)
    if not fn or handler_name == "none":
        return {"ok": True, "applied": False, "message": "No apply handler for this service."}
    creds = credentials_for_assist(slug)
    container = str(entry.get("container") or "").strip()
    applied, apply_msg = fn(creds, container)
    return {
        "ok": applied,
        "applied": applied,
        "message": apply_msg,
        "error": None if applied else apply_msg,
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
    if handler_name in ("file_transfer_sftp", "file_transfer_ftp"):
        restarted = applied
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
