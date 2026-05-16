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

import yaml

from control import CONTROL_TOKEN

PROJECT_ROOT = Path(os.getenv("DASHBOARD_PROJECT_ROOT", "/project"))
CONFIG_DIR = PROJECT_ROOT / "config"
CONFIG_FILE = CONFIG_DIR / "ui-credentials.yaml"
REGISTRY_FILE = PROJECT_ROOT / "ecosystem-stack" / "config" / "ui-login-registry.json"

LAUNCH_TOKEN_TTL_SEC = 60
_MASK = "••••••••"


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
    return {k: str(v) for k, v in defaults.items()}


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
    for k, v in data.items():
        if v is None:
            continue
        s = str(v)
        if _is_secret_key(k) and (_MASK in s or s == _MASK):
            continue
        current[k] = s
    services[slug] = current
    save_vault(vault)
    return credentials_for_ui(slug)


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


def catalog_for_ui() -> dict[str, Any]:
    from ui_credential_reset import _container_running

    entries = []
    for entry in load_registry():
        slug = str(entry.get("hub_slug") or entry.get("id") or "").strip()
        if not slug:
            continue
        auth_type = entry.get("auth_type") or "none"
        creds = credentials_for_ui(slug)
        container = str(entry.get("container") or "").strip()
        entries.append(
            {
                "slug": slug,
                "id": entry.get("id") or slug,
                "label": entry.get("label") or slug,
                "login_url": entry.get("login_url") or "",
                "auth_type": auth_type,
                "can_auto_login": auth_type in ("form_post", "json_post"),
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
