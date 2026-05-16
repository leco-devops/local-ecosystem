"""Create local-dev UI accounts after volume reset (n8n owner, Open WebUI first user)."""

from __future__ import annotations

import subprocess
import time

import requests

DEFAULT_DEV_PASSWORD = "Localdev1"


def _run(cmd: list[str], timeout: int = 60) -> tuple[bool, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode == 0, out.strip()[:2000]
    except subprocess.TimeoutExpired:
        return False, "command timed out"
    except Exception as exc:
        return False, str(exc)


def _n8n_cli_user_reset() -> tuple[bool, str]:
    """Clear n8n owners via CLI (lighter than dropping the whole Postgres DB)."""
    return _run(["docker", "exec", "n8n", "n8n", "user-management:reset"], timeout=90)


def _wait_url(url: str, *, timeout_sec: int = 180, interval_sec: float = 3.0) -> bool:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            resp = requests.get(url, timeout=8)
            if resp.status_code < 500:
                return True
        except requests.RequestException:
            pass
        time.sleep(interval_sec)
    return False


def _n8n_owner_setup(base: str, email: str, password: str) -> requests.Response:
    return requests.post(
        f"{base}/rest/owner/setup",
        json={
            "email": email,
            "password": password,
            "firstName": "Admin",
            "lastName": "Local",
        },
        timeout=25,
    )


def _n8n_login(base: str, email: str, password: str) -> requests.Response:
    return requests.post(
        f"{base}/rest/login",
        json={"emailOrLdapLoginId": email, "password": password},
        timeout=25,
    )


def provision_n8n(creds: dict[str, str]) -> tuple[bool, str]:
    email = str(creds.get("email") or "admin@local.lh").strip()
    password = str(creds.get("password") or DEFAULT_DEV_PASSWORD)
    base = "http://n8n:5678"
    if not _wait_url(f"{base}/healthz", timeout_sec=120):
        if not _wait_url(f"{base}/rest/settings", timeout_sec=60):
            return False, "n8n did not become ready after reset (wait and try Auto-login again)."

    login = _n8n_login(base, email, password)
    if login.ok:
        return True, "n8n account ready (existing owner matches vault)."

    setup = _n8n_owner_setup(base, email, password)
    if setup.ok:
        return True, f"n8n owner created ({email})."

    detail = (setup.text or login.text or "")[:300]
    if "already setup" in detail.lower():
        cli_ok, cli_msg = _n8n_cli_user_reset()
        if not cli_ok:
            return False, f"n8n user reset failed: {cli_msg}"
        setup2 = _n8n_owner_setup(base, email, password)
        if setup2.ok:
            verify = _n8n_login(base, email, password)
            if verify.ok:
                return True, f"n8n owner reset and provisioned ({email})."
        return False, (setup2.text or "")[:300] or "n8n owner setup failed after user reset"

    if "password" in detail.lower():
        return (
            False,
            f"n8n rejected password: {detail}. Use at least one uppercase letter and one number "
            f"(default: {DEFAULT_DEV_PASSWORD}).",
        )
    return False, detail or "n8n owner setup failed"


def resync_n8n_to_vault(creds: dict[str, str]) -> bool:
    """One-shot: align n8n owner password with vault (auto-login recovery)."""
    ok, _ = provision_n8n(creds)
    return ok


def provision_webui(creds: dict[str, str]) -> tuple[bool, str]:
    email = str(creds.get("email") or "admin@local.lh").strip()
    password = str(creds.get("password") or DEFAULT_DEV_PASSWORD)
    name = str(creds.get("name") or "Admin Local").strip()
    base = "http://open-webui:8080"
    if not _wait_url(f"{base}/health", timeout_sec=180):
        if not _wait_url(f"{base}/api/config", timeout_sec=60):
            return False, "Open WebUI did not become ready after reset (may still be starting)."

    signin = requests.post(
        f"{base}/api/v1/auths/signin",
        json={"email": email, "password": password},
        timeout=25,
    )
    if signin.ok and (signin.json() or {}).get("token"):
        return True, "Open WebUI account ready (existing user matches vault)."

    signup = requests.post(
        f"{base}/api/v1/auths/signup",
        json={"email": email, "password": password, "name": name},
        timeout=25,
    )
    if signup.ok and (signup.json() or {}).get("token"):
        return True, f"Open WebUI admin created ({email})."

    detail = (signup.text or signin.text or "")[:300]
    if signup.status_code == 403:
        return (
            False,
            "Open WebUI signup blocked (instance already has users). Run Reset & apply to wipe "
            "the open-webui volume, wait until the container is healthy, then Auto-login.",
        )
    return False, detail or "Open WebUI signup failed"


def provision_after_reset(slug: str, creds: dict[str, str]) -> tuple[bool, str]:
    if slug == "n8n":
        return provision_n8n(creds)
    if slug == "webui":
        return provision_webui(creds)
    return True, ""
