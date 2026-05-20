"""Public URL helpers and post-start URL repair for ready dev-stack templates."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from dev_stack_compose import STACKS_ROOT, _slugify
from dev_stack_routes import load_stack_meta, stack_hostname

# Templates that may persist wrong localhost URLs in app config / DB.
_URL_AWARE_TEMPLATES = frozenset(
    {
        "wordpress",
        "woocommerce",
        "ghost",
        "joomla",
        "magento-min",
        "magento-full",
    }
)

_BAD_BASES = (
    "http://localhost",
    "https://localhost",
    "http://127.0.0.1",
    "https://127.0.0.1",
)

_MAGENTO_CLI = "/bitnami/magento/bin/magento"


def stack_public_host(stack_id: str) -> str:
    return stack_hostname(_slugify(stack_id))


def stack_public_url(stack_id: str) -> str:
    return f"http://{stack_public_host(stack_id)}"


def format_compose_log(raw: str) -> str:
    """Normalize docker compose progress output into readable line-oriented text."""
    if not raw:
        return ""
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for chunk in text.split("\n"):
        line = chunk.strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _compose_file(stack_id: str) -> Path | None:
    compose = STACKS_ROOT / _slugify(stack_id) / "docker-compose.yml"
    return compose if compose.is_file() else None


def _compose_services(stack_id: str) -> set[str]:
    compose = _compose_file(stack_id)
    if not compose:
        return set()
    proc = subprocess.run(
        ["docker", "compose", "-f", str(compose), "config", "--services"],
        capture_output=True,
        text=True,
        cwd=str(compose.parent),
        env=_compose_project_env(_slugify(stack_id)),
        timeout=30,
    )
    if proc.returncode != 0:
        return set()
    return {ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()}


def _compose_project_env(stack_id: str) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("COMPOSE_PROJECT_NAME", f"leco-devstack-{_slugify(stack_id)}")
    return env


def _compose_exec(stack_id: str, service: str, *cmd: str, timeout: int = 300) -> tuple[int, str]:
    sid = _slugify(stack_id)
    compose = STACKS_ROOT / sid / "docker-compose.yml"
    if not compose.is_file():
        return 1, f"compose file missing for {sid}"
    proc = subprocess.run(
        ["docker", "compose", "-f", str(compose), "exec", "-T", service, *cmd],
        capture_output=True,
        text=True,
        cwd=str(compose.parent),
        env=_compose_project_env(sid),
        timeout=timeout,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out.strip()


def _wp_cli(stack_id: str, *args: str) -> tuple[int, str]:
    sid = _slugify(stack_id)
    compose = STACKS_ROOT / sid / "docker-compose.yml"
    if not compose.is_file():
        return 1, f"compose file missing for {sid}"
    proc = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(compose),
            "--profile",
            "cli",
            "run",
            "--rm",
            "wp-cli",
            *args,
            "--allow-root",
        ],
        capture_output=True,
        text=True,
        cwd=str(compose.parent),
        env=_compose_project_env(sid),
        timeout=300,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out.strip()


def _compose_wait_service(stack_id: str, service: str, *, timeout: int = 300) -> tuple[int, str]:
    compose = _compose_file(stack_id)
    if not compose:
        return 1, f"compose file missing for {stack_id}"
    proc = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(compose),
            "wait",
            service,
            "--timeout",
            str(timeout),
        ],
        capture_output=True,
        text=True,
        cwd=str(compose.parent),
        env=_compose_project_env(_slugify(stack_id)),
        timeout=timeout + 30,
    )
    out = format_compose_log((proc.stdout or "") + (proc.stderr or ""))
    return proc.returncode, out


def _magento_cli(stack_id: str, *args: str, timeout: int = 300) -> tuple[int, str]:
    return _compose_exec(stack_id, "magento", _MAGENTO_CLI, *args, timeout=timeout)


def _wait_magento_ready(stack_id: str, *, timeout: int = 900) -> tuple[bool, str]:
    """Wait for Bitnami first-boot install (bin/magento appears, setup:db:status OK)."""
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        code, out = _compose_exec(stack_id, "magento", "test", "-x", _MAGENTO_CLI, timeout=60)
        if code != 0:
            last = out or "Waiting for Magento install (bin/magento)…"
        else:
            code2, out2 = _magento_cli(stack_id, "setup:db:status", timeout=120)
            if code2 == 0:
                return True, "Magento is installed."
            last = out2 or "Magento CLI ready; setup still in progress…"
        time.sleep(10)
    return False, last or f"Timed out after {timeout}s waiting for Magento install."


def _wait_wordpress_installed(stack_id: str, *, timeout: int = 180) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        code, out = _wp_cli(stack_id, "core", "is-installed")
        if code == 0:
            return True, "WordPress is installed."
        last = out or "Waiting for WordPress install…"
        time.sleep(3)
    return False, last or f"Timed out after {timeout}s waiting for WordPress install."


def wait_for_stack_app_ready(stack_id: str) -> str:
    """Block until one-shot init containers finish and app DB install is ready."""
    meta = load_stack_meta(stack_id)
    template = str(meta.get("template") or "").strip().lower()
    sections: list[str] = []

    services = _compose_services(stack_id)

    if "wp-sample-init" in services:
        sections.append("--- Sample data init ---")
        code, out = _compose_wait_service(stack_id, "wp-sample-init", timeout=300)
        if out:
            sections.append(out)
        elif code == 0:
            sections.append("wp-sample-init completed.")
        else:
            sections.append("wp-sample-init did not complete successfully.")

    if "wc-setup" in services:
        sections.append("--- WooCommerce setup ---")
        code, out = _compose_wait_service(stack_id, "wc-setup", timeout=300)
        if out:
            sections.append(out)
        elif code == 0:
            sections.append("wc-setup completed.")
        else:
            sections.append("wc-setup did not complete successfully.")

    if template in ("wordpress", "woocommerce"):
        sections.append("--- WordPress install ---")
        if "wp-sample-init" in services:
            ok, msg = _wait_wordpress_installed(stack_id)
            sections.append(msg)
            if not ok:
                sections.append("(URL repair will run after install completes on a later start.)")
        else:
            code, out = _wp_cli(stack_id, "core", "is-installed")
            if code == 0:
                sections.append("WordPress is installed.")
            else:
                hint = out or "Complete setup in the browser, then Start again for URL repair."
                sections.append(hint)

    if template in ("magento-min", "magento-full"):
        sections.append("--- Magento install ---")
        ok, msg = _wait_magento_ready(stack_id)
        sections.append(msg)
        if not ok:
            sections.append("(URL repair will run after install completes on a later start.)")

    return "\n".join(sections).strip()


def repair_wordpress_urls(stack_id: str) -> dict[str, object]:
    url = stack_public_url(stack_id)
    code, check_out = _wp_cli(stack_id, "core", "is-installed")
    if code != 0:
        return {
            "ok": False,
            "skipped": True,
            "url": url,
            "error": "WordPress is not installed yet",
            "output": check_out or "Run stack Start again after wp-sample-init finishes.",
        }

    logs: list[str] = []
    ok = True
    for opt in ("siteurl", "home"):
        code, out = _wp_cli(stack_id, "option", "update", opt, url)
        logs.append(out)
        if code != 0:
            ok = False
    for old in _BAD_BASES:
        if old == url:
            continue
        code, out = _wp_cli(stack_id, "search-replace", old, url, "--all-tables", "--skip-columns=guid")
        if out:
            logs.append(out)
        if code not in (0, 1):
            ok = False
    flush_code, flush_out = _wp_cli(stack_id, "rewrite", "flush", "--hard")
    if flush_out:
        logs.append(flush_out)
    if flush_code != 0:
        ok = False
    return {"ok": ok, "url": url, "output": "\n".join(logs)[-4000:]}


def repair_ghost_urls(stack_id: str) -> dict[str, object]:
    url = stack_public_url(stack_id)
    code, out = _compose_exec(stack_id, "ghost", "ghost", "config", "url", url, "--no-prompt")
    logs = [out] if out else []
    if code != 0:
        return {"ok": False, "url": url, "output": "\n".join(logs)[-4000:]}
    return {"ok": True, "url": url, "output": "\n".join(logs)[-4000:]}


def repair_joomla_urls(stack_id: str) -> dict[str, object]:
    url = stack_public_url(stack_id)
    logs: list[str] = []
    ok = True
    for key, val in (("live_site", url), ("force_ssl", "0")):
        code, out = _compose_exec(
            stack_id,
            "joomla",
            "php",
            "cli/joomla.php",
            "config:set",
            key,
            val,
        )
        if out:
            logs.append(out)
        if code != 0:
            ok = False
    return {"ok": ok, "url": url, "output": "\n".join(logs)[-4000:]}


def repair_magento_urls(stack_id: str) -> dict[str, object]:
    base = stack_public_url(stack_id).rstrip("/") + "/"
    code, check_out = _compose_exec(stack_id, "magento", "test", "-x", _MAGENTO_CLI, timeout=60)
    if code != 0:
        return {
            "ok": False,
            "skipped": True,
            "url": base,
            "error": "Magento is not installed yet",
            "output": check_out or "Run stack Start again after the Magento container finishes first boot.",
        }
    code, status_out = _magento_cli(stack_id, "setup:db:status", timeout=120)
    if code != 0:
        return {
            "ok": False,
            "skipped": True,
            "url": base,
            "error": "Magento setup is not complete yet",
            "output": status_out or "Wait for install, then Start again for URL repair.",
        }

    logs: list[str] = []
    ok = True
    commands = [
        ["setup:store-config:set", f"--base-url={base}"],
        ["config:set", "web/unsecure/base_url", base],
        ["config:set", "web/secure/base_url", base],
        ["cache:flush"],
    ]
    for cmd in commands:
        code, out = _magento_cli(stack_id, *cmd, timeout=600)
        if out:
            logs.append(out)
        if code != 0:
            ok = False
    return {"ok": ok, "url": base, "output": "\n".join(logs)[-4000:]}


def repair_stack_public_urls(stack_id: str) -> dict[str, object]:
    meta = load_stack_meta(stack_id)
    template = str(meta.get("template") or "").strip().lower()
    if template not in _URL_AWARE_TEMPLATES:
        return {"ok": True, "skipped": True, "reason": "template does not use public URL repair"}

    if template in ("wordpress", "woocommerce"):
        return repair_wordpress_urls(stack_id)
    if template == "ghost":
        return repair_ghost_urls(stack_id)
    if template == "joomla":
        return repair_joomla_urls(stack_id)
    if template in ("magento-min", "magento-full"):
        return repair_magento_urls(stack_id)
    return {"ok": True, "skipped": True}
