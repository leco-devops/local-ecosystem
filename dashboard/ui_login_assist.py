"""Server-side UI login assist (local dev)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests

from ui_credential_reset import _container_running
from ui_credentials import credentials_for_assist, get_registry_entry


@dataclass
class AssistLoginResult:
    ok: bool
    mode: str = ""
    error: str = ""
    token: str = ""
    storage_key: str = "token"
    cookies: requests.cookies.RequestsCookieJar | None = None


def build_assist_public_url(slug: str, token: str) -> str:
    """Assist page on the service host so Set-Cookie / storage apply to the right origin."""
    entry = get_registry_entry(slug) or {}
    login_url = str(entry.get("login_url") or "").strip()
    parsed = urlparse(login_url)
    if parsed.scheme and parsed.netloc:
        base = f"{parsed.scheme}://{parsed.netloc}"
    else:
        base = ""
    return f"{base}/assist/login/{slug}?token={token}"


def _json_login_body(entry: dict[str, Any], creds: dict[str, str]) -> dict[str, str]:
    body: dict[str, str] = {}
    for json_key, vault_key in (entry.get("form_fields") or {}).items():
        body[json_key] = str(creds.get(vault_key, ""))
    return body


def try_server_side_login(slug: str) -> AssistLoginResult:
    entry = get_registry_entry(slug)
    if not entry or (entry.get("auth_type") or "") != "json_post":
        return AssistLoginResult(False, error="not a JSON login service")
    container = str(entry.get("container") or "").strip()
    if container and not _container_running(container):
        return AssistLoginResult(
            False,
            error=f"Container {container} is not running. Start it from Control → Ecosystem services, then try again.",
        )
    internal = str(entry.get("internal_login_url") or "").strip()
    if not internal:
        return AssistLoginResult(False, error="missing internal_login_url")
    creds = credentials_for_assist(slug)
    body = _json_login_body(entry, creds)
    try:
        resp = requests.post(internal, json=body, timeout=25)
    except requests.RequestException as exc:
        return AssistLoginResult(False, error=str(exc)[:300])
    if not resp.ok and slug == "n8n" and resp.status_code == 401:
        from ui_provision import resync_n8n_to_vault

        if resync_n8n_to_vault(creds):
            try:
                resp = requests.post(internal, json=body, timeout=25)
            except requests.RequestException as exc:
                return AssistLoginResult(False, error=str(exc)[:300])
    if not resp.ok:
        detail = (resp.text or "")[:300]
        return AssistLoginResult(False, error=detail or f"HTTP {resp.status_code}")
    mode = str(entry.get("session_mode") or "cookie")
    if mode == "local_storage":
        try:
            data = resp.json()
        except ValueError:
            return AssistLoginResult(False, error="invalid JSON from login API")
        token = str(data.get("token") or "")
        if not token:
            return AssistLoginResult(False, error="login OK but no token in response")
        return AssistLoginResult(
            ok=True,
            mode="local_storage",
            token=token,
            storage_key=str(entry.get("session_storage_key") or "token"),
        )
    return AssistLoginResult(ok=True, mode="cookie", cookies=resp.cookies)


def apply_cookies_to_flask_response(
    flask_resp,
    cookie_jar: requests.cookies.RequestsCookieJar,
    *,
    login_url: str = "",
) -> None:
    """Set session cookies on the service host (omit internal n8n.local domain)."""
    parsed = urlparse(login_url) if login_url else None
    host = parsed.hostname if parsed else None
    for c in cookie_jar:
        kwargs: dict[str, Any] = {
            "path": c.path or "/",
            "secure": bool(c.secure),
            "httponly": True,
            "samesite": "Lax",
        }
        # n8n returns Domain=n8n.local from internal URL; host-only cookie works on n8n.lh.
        if host and c.domain and c.domain != host and not host.endswith(c.domain.lstrip(".")):
            pass
        elif c.domain and host and c.domain == host:
            kwargs["domain"] = host
        flask_resp.set_cookie(c.name, c.value, **kwargs)
