"""
Local-dev UI credential vault and login-assist tokens.

Registry: ecosystem-stack/config/ui-login-registry.json (committed)
Secrets: config/ui-credentials.yaml (gitignored)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

from control import CONTROL_TOKEN

PROJECT_ROOT = Path(os.getenv("DASHBOARD_PROJECT_ROOT", "/project"))
CONFIG_DIR = PROJECT_ROOT / "config"
CONFIG_FILE = CONFIG_DIR / "ui-credentials.yaml"
REGISTRY_FILE = PROJECT_ROOT / "ecosystem-stack" / "config" / "ui-login-registry.json"

LAUNCH_TOKEN_TTL_SEC = 60
_MASK = "••••••••"
DEFAULT_FILE_TRANSFER_PASSWORD = "leco#localhost-192"
DEFAULT_SFTP_PORT = "2222"
DEFAULT_FTP_PORT = "21"


def _normalize_port(value: str | None, default: str) -> str:
    raw = str(value or default).strip() or default
    try:
        n = int(raw)
    except ValueError as exc:
        raise ValueError(f"invalid port: {value}") from exc
    if not 1 <= n <= 65535:
        raise ValueError(f"port must be 1–65535, got {value}")
    return str(n)


def _secret() -> str:
    return (
        os.getenv("DASHBOARD_UI_CREDENTIAL_SECRET", "").strip()
        or CONTROL_TOKEN
        or "leco-local-dev-ui-vault"
    )


def _ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_registry() -> list[dict[str, Any]]:
    if not REGISTRY_FILE.is_file():
        return []
    try:
        raw = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
        services = raw.get("services") if isinstance(raw, dict) else None
        return list(services) if isinstance(services, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def registry_by_slug() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for entry in load_registry():
        slug = str(entry.get("hub_slug") or entry.get("id") or "").strip()
        if slug:
            out[slug] = entry
    return out


def get_registry_entry(slug: str) -> dict[str, Any] | None:
    return registry_by_slug().get(str(slug or "").strip())


def load_vault() -> dict[str, Any]:
    if CONFIG_FILE.is_file():
        try:
            raw = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except (OSError, yaml.YAMLError):
            pass
    return {"version": 1, "services": {}}


def save_vault(data: dict[str, Any]) -> None:
    _ensure_config_dir()
    header = (
        "# UI credentials for local *.lh service logins.\n"
        "# Gitignored. Managed by LEco DevOps dashboard.\n\n"
    )
    body = yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
    CONFIG_FILE.write_text(header + body, encoding="utf-8")
    try:
        CONFIG_FILE.chmod(0o600)
    except OSError:
        pass


def _enrich_sftp_credentials(merged: dict[str, str]) -> dict[str, str]:
    from ui_credential_reset import _load_file_transfer_env, _normalize_sftp_auth_mode, _read_sftp_public_key

    user = merged.get("username", "leco")
    if not merged.get("auth_mode"):
        env = _load_file_transfer_env()
        merged["auth_mode"] = _normalize_sftp_auth_mode(env.get("SFTP_AUTH_MODE"))
    else:
        merged["auth_mode"] = _normalize_sftp_auth_mode(merged.get("auth_mode"))
    if not merged.get("public_key"):
        disk_key = _read_sftp_public_key(user)
        if disk_key:
            merged["public_key"] = disk_key
    if not merged.get("port"):
        env = _load_file_transfer_env()
        merged["port"] = _normalize_port(env.get("SFTP_PORT"), DEFAULT_SFTP_PORT)
    else:
        merged["port"] = _normalize_port(merged.get("port"), DEFAULT_SFTP_PORT)
    return merged


def _enrich_ftp_credentials(merged: dict[str, str]) -> dict[str, str]:
    from ui_credential_reset import _load_file_transfer_env

    if not merged.get("port"):
        env = _load_file_transfer_env()
        merged["port"] = _normalize_port(env.get("FTP_PORT"), DEFAULT_FTP_PORT)
    else:
        merged["port"] = _normalize_port(merged.get("port"), DEFAULT_FTP_PORT)
    return merged


def _merged_credentials(slug: str) -> dict[str, str]:
    entry = get_registry_entry(slug)
    if not entry:
        return {}
    defaults = dict(entry.get("default_credentials") or {})
    vault = load_vault().get("services") or {}
    stored = vault.get(slug) if isinstance(vault, dict) else None
    if isinstance(stored, dict):
        for k, v in stored.items():
            if v is not None and str(v) != "":
                defaults[k] = str(v)
    merged = {k: str(v) for k, v in defaults.items()}
    if slug == "sftp":
        merged = _enrich_sftp_credentials(merged)
    elif slug == "ftp":
        merged = _enrich_ftp_credentials(merged)
    return merged


def _is_secret_key(key: str) -> bool:
    k = key.lower()
    return "password" in k or "secret" in k or k in ("api_key",)


def mask_value(key: str, value: str) -> str:
    if not value:
        return ""
    if _is_secret_key(key):
        return _MASK if len(value) < 12 else value[:2] + _MASK + value[-2:]
    return value


def credentials_for_ui(slug: str) -> dict[str, Any]:
    merged = _merged_credentials(slug)
    masked = {k: mask_value(k, v) for k, v in merged.items()}
    entry = get_registry_entry(slug) or {}
    defaults = entry.get("default_credentials") or {}
    is_custom = False
    vault = load_vault().get("services") or {}
    if isinstance(vault, dict) and slug in vault:
        is_custom = True
    return {
        "values": masked,
        "is_custom": is_custom,
        "matches_defaults": merged == {k: str(v) for k, v in dict(defaults).items()},
    }


def credentials_for_assist(slug: str) -> dict[str, str]:
    """Full credentials for server-side login assist only."""
    return _merged_credentials(slug)


def save_credentials(slug: str, data: dict[str, Any]) -> dict[str, Any]:
    entry = get_registry_entry(slug)
    if not entry:
        raise ValueError(f"unknown service slug: {slug}")
    vault = load_vault()
    services = vault.setdefault("services", {})
    if not isinstance(services, dict):
        services = {}
        vault["services"] = services
    current = dict(services.get(slug) or {})
    auth_mode = str(data.get("auth_mode") or current.get("auth_mode") or "password").lower()
    for k, v in data.items():
        if v is None:
            continue
        s = str(v).strip()
        if k == "auth_mode":
            current["auth_mode"] = _normalize_save_auth_mode(s)
            continue
        if k == "public_key":
            if s:
                current["public_key"] = s
            elif auth_mode == "password":
                current.pop("public_key", None)
            continue
        if k == "port":
            current["port"] = _normalize_port(s, DEFAULT_SFTP_PORT if slug == "sftp" else DEFAULT_FTP_PORT)
            continue
        if _is_secret_key(k) and (_MASK in s or s == _MASK):
            continue
        if s:
            current[k] = s
    services[slug] = current
    save_vault(vault)
    return credentials_for_ui(slug)


def _normalize_save_auth_mode(value: str) -> str:
    mode = str(value or "password").strip().lower()
    return mode if mode in ("password", "key", "both") else "password"


def reset_vault_to_defaults(slug: str) -> dict[str, str]:
    entry = get_registry_entry(slug)
    if not entry:
        raise ValueError(f"unknown service slug: {slug}")
    defaults = {k: str(v) for k, v in dict(entry.get("default_credentials") or {}).items()}
    vault = load_vault()
    services = vault.setdefault("services", {})
    if not isinstance(services, dict):
        services = {}
        vault["services"] = services
    services[slug] = dict(defaults)
    save_vault(vault)
    return defaults


def _connection_hint(entry: dict[str, Any], creds: dict[str, str]) -> str:
    auth_type = entry.get("auth_type") or ""
    slug = str(entry.get("hub_slug") or entry.get("id") or "").strip()
    if auth_type == "protocol":
        user = creds.get("username", "leco")
        password = creds.get("password", DEFAULT_FILE_TRANSFER_PASSWORD)
        port = creds.get("port", DEFAULT_SFTP_PORT if slug == "sftp" else DEFAULT_FTP_PORT)
        if slug == "sftp":
            auth_mode = str(creds.get("auth_mode") or "password").lower()
            if auth_mode == "key":
                return f"sftp -P {port} -i ~/.ssh/id_ed25519 {user}@localhost"
            return f"sftp -P {port} {user}@localhost"
        if slug == "ftp":
            return f"ftp://{user}:{quote(password, safe='')}@localhost:{port}"
    if auth_type == "browse_only":
        return str(entry.get("connection_hint") or "Read-only · no login")
    return str(entry.get("connection_hint") or "")


def _login_details(entry: dict[str, Any], creds: dict[str, str]) -> dict[str, Any]:
    """Copy-paste friendly login block for the UI access table."""
    auth_type = entry.get("auth_type") or "none"
    slug = str(entry.get("hub_slug") or entry.get("id") or "").strip()
    login_url = str(entry.get("login_url") or "")

    if auth_type == "protocol":
        user = creds.get("username", "leco")
        password = creds.get("password", DEFAULT_FILE_TRANSFER_PASSWORD)
        auth_mode = str(creds.get("auth_mode") or "password").lower()
        public_key = str(creds.get("public_key") or "").strip()
        if slug == "sftp":
            host, alt_host = "localhost", "sftp.lh"
            port = creds.get("port", DEFAULT_SFTP_PORT)
            if auth_mode == "key":
                summary = f"User: {user} · Auth: public key"
                connection_strings = [
                    f"sftp -P {port} -i ~/.ssh/id_ed25519 {user}@{host}",
                    f"sftp -P {port} -i ~/.ssh/id_ed25519 {user}@{alt_host}",
                ]
                password = ""
            elif auth_mode == "both":
                summary = f"User: {user} · Password: {password} · Auth: password + public key"
                connection_strings = [
                    f"sftp -P {port} {user}@{host}",
                    f"sftp -P {port} -i ~/.ssh/id_ed25519 {user}@{host}",
                    f"sftp -P {port} -i ~/.ssh/id_ed25519 {user}@{alt_host}",
                ]
            else:
                summary = f"User: {user} · Password: {password}"
                connection_strings = [
                    f"sftp -P {port} {user}@{host}",
                    f"sftp -P {port} {user}@{alt_host}",
                ]
        elif slug == "ftp":
            auth_mode = "password"
            host, alt_host = "localhost", "ftp.lh"
            port = creds.get("port", DEFAULT_FTP_PORT)
            connection_strings = [
                f"ftp://{user}:{quote(password, safe='')}@{host}:{port}",
                f"ftp://{user}:{quote(password, safe='')}@{alt_host}:{port}",
            ]
            summary = f"User: {user} · Password: {password}"
        else:
            host, alt_host, port, connection_strings = "", "", "", []
            summary = f"User: {user} · Password: {password}"
        return {
            "kind": "protocol",
            "summary": summary,
            "username": user,
            "password": password,
            "auth_mode": auth_mode,
            "public_key": public_key,
            "host": host,
            "alt_host": alt_host,
            "port": port,
            "connection_strings": connection_strings,
            "footnote": "Override via Edit or file-transfer/.env",
            "browser_url": login_url,
        }

    if auth_type == "browse_only":
        return {
            "kind": "browse_only",
            "summary": "Read-only · no login",
            "browser_url": login_url or "http://files.lh",
            "browser_urls": [
                u
                for u in [
                    login_url or "http://files.lh",
                    "http://ftp-files.lh",
                    "http://sftp-files.lh",
                ]
                if u
            ],
        }

    user = creds.get("username") or creds.get("email") or creds.get("driver") or ""
    password = creds.get("password") or creds.get("secretKey") or ""
    masked_pw = mask_value("password", password) if password else ""
    summary_parts = []
    if user:
        summary_parts.append(f"User: {user}")
    if password:
        summary_parts.append(f"Password: {masked_pw}")
    return {
        "kind": "web",
        "login_url": login_url,
        "username": user,
        "password": masked_pw,
        "summary": " · ".join(summary_parts) if summary_parts else "",
    }


def catalog_for_ui() -> dict[str, Any]:
    from ui_credential_reset import _container_running

    entries = []
    for entry in load_registry():
        slug = str(entry.get("hub_slug") or entry.get("id") or "").strip()
        if not slug:
            continue
        auth_type = entry.get("auth_type") or "none"
        creds = credentials_for_ui(slug)
        merged = _merged_credentials(slug)
        container = str(entry.get("container") or "").strip()
        defaults = entry.get("default_credentials") or {}
        entries.append(
            {
                "slug": slug,
                "id": entry.get("id") or slug,
                "label": entry.get("label") or slug,
                "login_url": entry.get("login_url") or "",
                "connection_hint": _connection_hint(entry, merged),
                "login_details": _login_details(entry, merged),
                "auth_type": auth_type,
                "can_auto_login": auth_type in ("form_post", "json_post"),
                "can_edit": bool(defaults),
                "can_reset": (entry.get("reset_handler") or "none") != "none",
                "container": container or None,
                "container_running": _container_running(container) if container else None,
                "credentials": creds,
            }
        )
    return {
        "local_dev_only": True,
        "token_required": bool(CONTROL_TOKEN),
        "services": entries,
    }


def make_launch_token(slug: str) -> str:
    entry = get_registry_entry(slug)
    if not entry:
        raise ValueError(f"unknown service slug: {slug}")
    auth_type = entry.get("auth_type") or "none"
    if auth_type not in ("form_post", "json_post"):
        raise ValueError(f"service {slug} does not support auto-login")
    exp = int(time.time()) + LAUNCH_TOKEN_TTL_SEC
    payload = f"{slug}:{exp}"
    sig = hmac.new(_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    raw = f"{payload}:{sig}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def verify_launch_token(slug: str, token: str) -> bool:
    if not token or not slug:
        return False
    try:
        pad = "=" * (-len(token) % 4)
        decoded = base64.urlsafe_b64decode(token + pad).decode()
        parts = decoded.rsplit(":", 2)
        if len(parts) != 3:
            return False
        tok_slug, exp_s, sig = parts
        if tok_slug != slug:
            return False
        exp = int(exp_s)
        if exp < int(time.time()):
            return False
        payload = f"{slug}:{exp}"
        expected = hmac.new(_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig)
    except (ValueError, OSError):
        return False


def build_assist_context(slug: str) -> dict[str, Any] | None:
    entry = get_registry_entry(slug)
    if not entry:
        return None
    creds = credentials_for_assist(slug)
    form_fields = entry.get("form_fields") or {}
    fields: list[dict[str, str]] = []
    for html_name, vault_key in form_fields.items():
        fields.append({"name": html_name, "value": creds.get(vault_key, "")})
    login_url = str(entry.get("login_url") or "").rstrip("/")
    auth_type = entry.get("auth_type") or "none"
    ctx: dict[str, Any] = {
        "slug": slug,
        "label": entry.get("label") or slug,
        "login_url": login_url,
        "auth_type": auth_type,
        "fields": fields,
    }
    if auth_type == "form_post":
        ctx["form_action"] = entry.get("form_action") or login_url
        ctx["form_method"] = "post"
    elif auth_type == "json_post":
        submit_path = entry.get("submit_path") or "/api/v1/login"
        ctx["json_url"] = f"{login_url}{submit_path}"
        body = {}
        for json_key, vault_key in form_fields.items():
            body[json_key] = creds.get(vault_key, "")
        ctx["json_body"] = body
    return ctx
