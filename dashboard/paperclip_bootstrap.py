"""Paperclip first-admin bootstrap (CEO invite URL) via docker exec."""

from __future__ import annotations

import os
import re
import shlex
from typing import Any, Iterator

CONTAINER = "paperclip"
DATA_DIR = "/paperclip"
APP_DIR = "/app"
CONFIG_PATH = f"{DATA_DIR}/instances/default/config.json"
DEFAULT_BASE_URL = os.environ.get("PAPERCLIP_PUBLIC_URL", "http://paperclip.lh")

INVITE_URL_RE = re.compile(r"https?://[^\s]+/invite/[^\s]+", re.I)


def _docker_client():
    import docker

    return docker.from_env()


def paperclip_container_running(dc=None) -> bool:
    try:
        dc = dc or _docker_client()
        c = dc.containers.get(CONTAINER)
        return (c.status or "").lower() == "running"
    except Exception:
        return False


def paperclip_config_exists(dc=None) -> bool:
    try:
        dc = dc or _docker_client()
        if not paperclip_container_running(dc):
            return False
        c = dc.containers.get(CONTAINER)
        code, _ = c.exec_run(["test", "-f", CONFIG_PATH])
        return code == 0
    except Exception:
        return False


def parse_invite_url(text: str) -> str | None:
    m = INVITE_URL_RE.search(text or "")
    if not m:
        return None
    return m.group(0).rstrip(".,)|]")


def bootstrap_ceo_argv(*, base_url: str | None = None, force: bool = False) -> list[str]:
    url = (base_url or DEFAULT_BASE_URL).strip()
    force_flag = " --force" if force else ""
    inner = (
        f"cd {shlex.quote(APP_DIR)} && "
        f"pnpm paperclipai auth bootstrap-ceo "
        f"-d {shlex.quote(DATA_DIR)} "
        f"--base-url {shlex.quote(url)}{force_flag}"
    )
    return ["docker", "exec", CONTAINER, "sh", "-c", inner]


def onboard_quick_argv() -> list[str]:
    inner = (
        f"cd {shlex.quote(APP_DIR)} && "
        f"pnpm paperclipai onboard -d {shlex.quote(DATA_DIR)} -y --bind lan"
    )
    return ["docker", "exec", CONTAINER, "sh", "-c", inner]


def bootstrap_ceo_status() -> dict[str, Any]:
    try:
        dc = _docker_client()
        running = paperclip_container_running(dc)
        config = paperclip_config_exists(dc) if running else False
        return {
            "ok": True,
            "running": running,
            "config_exists": config,
            "base_url": DEFAULT_BASE_URL,
            "container": CONTAINER,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def bootstrap_ceo_streaming(
    *,
    auto_onboard: bool = True,
    base_url: str | None = None,
    force: bool = False,
) -> Iterator[dict[str, Any]]:
    from control import _emit_done, _yield_run

    try:
        dc = _docker_client()
        if not paperclip_container_running(dc):
            yield _emit_done(
                False,
                error="Paperclip container is not running. Start it from Control → Paperclip first.",
            )
            return
    except Exception as exc:
        yield _emit_done(False, error=str(exc))
        return

    url = (base_url or DEFAULT_BASE_URL).strip()
    logs: list[str] = []

    if auto_onboard and not paperclip_config_exists(dc):
        yield {
            "type": "log",
            "text": (
                "No Paperclip config at /paperclip/instances/default — "
                "running non-interactive onboard first…\n\n"
            ),
        }
        code, log = yield from _yield_run(onboard_quick_argv(), timeout=300)
        if log:
            logs.append(log)
        if code != 0:
            yield _emit_done(
                False,
                exit_code=code,
                error="Paperclip onboard failed",
                log="\n".join(logs)[-12000:],
            )
            return
        yield {"type": "log", "text": "\nOnboard complete. Creating bootstrap CEO invite…\n\n"}

    yield {"type": "log", "text": "Creating bootstrap CEO invite (one-time admin URL)…\n\n"}
    code, log = yield from _yield_run(
        bootstrap_ceo_argv(base_url=url, force=force),
        timeout=180,
    )
    if log:
        logs.append(log)
    combined = "\n".join(logs)
    invite = parse_invite_url(combined)

    if invite:
        yield _emit_done(True, exit_code=0, log=combined[-12000:], invite_url=invite)
        return

    err = "No invite URL in output"
    if "admin already exists" in combined.lower() or "already exists" in combined.lower():
        err = "Admin may already exist — enable Force new invite or open https://paperclip.lh to sign in"
    yield _emit_done(
        False,
        exit_code=code or 1,
        log=combined[-12000:],
        error=err,
    )
