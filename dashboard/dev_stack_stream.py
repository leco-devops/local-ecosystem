"""NDJSON streaming for dev stack lifecycle actions (live compose / init logs)."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from typing import Any

from control import _yield_run
from dev_stack_app_urls import _compose_services, _wp_cli, repair_stack_public_urls
from dev_stack_compose import _slugify, stack_dir_for
from dev_stack_routes import load_stack_meta
from dev_stacks import _compose_project_name, _ensure_lh_network, _prune_devstack_project
from platform_config import load_platform_config, save_platform_config


def _emit_done(ok: bool, **extra: Any) -> dict[str, Any]:
    return {"type": "done", "result": {"ok": ok, **extra}}


def _log(text: str) -> dict[str, Any]:
    return {"type": "log", "text": text if text.endswith("\n") else f"{text}\n"}


def _stream_stack_compose(stack_id: str, *args: str, timeout: int = 900) -> Iterator[dict[str, Any] | Any]:
    d = stack_dir_for(stack_id)
    compose = d / "docker-compose.yml"
    if not compose.is_file():
        yield _log(f"Missing compose file for {stack_id}")
        return (1, "")
    project = _compose_project_name(stack_id)
    if args and args[0] in ("up", "start", "restart"):
        _ensure_lh_network()
    code, log = yield from _yield_run(
        ["docker", "compose", "-p", project, "-f", str(compose), *args],
        cwd=str(d),
        timeout=timeout,
    )
    return (code, log)


def _stream_wait_wordpress(stack_id: str, *, timeout: int = 180) -> Iterator[dict[str, Any] | Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code, out = _wp_cli(stack_id, "core", "is-installed")
        if code == 0:
            yield _log("WordPress is installed.")
            return (True, "")
        hint = (out or "Waiting for WordPress install…").strip()
        yield _log(hint)
        time.sleep(3)
    yield _log(f"Timed out after {timeout}s waiting for WordPress install.")
    return (False, "")


def stack_action_streaming(stack_id: str, action: str) -> Iterator[dict[str, Any]]:
    sid = stack_id.strip().lower()
    action = action.strip().lower()

    if action == "destroy":
        yield from _stream_destroy(sid)
        return

    if action == "start":
        yield from _stream_start(sid)
        return

    if action == "stop":
        yield _log("--- Docker Compose stop ---")
        code, _ = yield from _stream_stack_compose(sid, "stop")
        if code == 0:
            cfg = load_platform_config()
            for s in cfg.get("dev_stacks") or []:
                if str(s.get("id")) == sid or _slugify(str(s.get("id") or "")) == _slugify(sid):
                    s["state"] = "stopped"
            save_platform_config(cfg)
            from dev_stack_routes import sync_dev_stack_routes

            sync_dev_stack_routes(sid)
        yield _emit_done(code == 0, action=action, state="stopped" if code == 0 else "partial")
        return

    yield _emit_done(False, error=f"Unknown action: {action}")


def _stream_start(sid: str) -> Iterator[dict[str, Any]]:
    from dev_stack_images import normalize_stack_compose_file, verify_stack_compose_file

    migrate_logs = normalize_stack_compose_file(sid)
    if migrate_logs:
        yield _log("--- Compose image updates ---")
        for line in migrate_logs:
            yield _log(line)

    yield _log("--- Image preflight ---")
    image_errors = verify_stack_compose_file(sid, skip_registry=False)
    if image_errors:
        for err in image_errors:
            yield _log(f"✗ {err}\n")
        yield _emit_done(
            False,
            error="Container image preflight failed — fix images or destroy/recreate the stack",
            action="start",
            image_errors=image_errors,
        )
        return
    yield _log("Container images OK (local cache or registry).\n")

    yield _log("--- Docker Compose up ---")
    code, _ = yield from _stream_stack_compose(sid, "up", "-d")
    if code != 0:
        yield _emit_done(False, error="docker compose up failed", action="start")
        return

    cfg = load_platform_config()
    for s in cfg.get("dev_stacks") or []:
        if str(s.get("id")) == sid or _slugify(str(s.get("id") or "")) == _slugify(sid):
            s["state"] = "running"
    save_platform_config(cfg)

    services = _compose_services(sid)
    meta = load_stack_meta(sid)
    template = str(meta.get("template") or "").strip().lower()

    if "wp-sample-init" in services:
        yield _log("--- Sample data init (live) ---")
        init_code, _ = yield from _stream_stack_compose(
            sid,
            "logs",
            "-f",
            "--no-color",
            "wp-sample-init",
            timeout=600,
        )
        if init_code != 0:
            yield _log("(init logs ended with non-zero exit; checking install state…)")

    if "wc-setup" in services:
        yield _log("--- WooCommerce setup (live) ---")
        wc_code, _ = yield from _stream_stack_compose(
            sid,
            "logs",
            "-f",
            "--no-color",
            "wc-setup",
            timeout=600,
        )
        if wc_code != 0:
            yield _log("(WooCommerce setup logs ended with non-zero exit; continuing…)")

    if template in ("wordpress", "woocommerce"):
        yield _log("--- WordPress install ---")
        if "wp-sample-init" in services:
            ok, _ = yield from _stream_wait_wordpress(sid)
            if not ok:
                yield _log("(URL repair skipped until install completes; try Start again later.)")
                from dev_stack_routes import sync_dev_stack_routes

                sync_dev_stack_routes(sid)
                yield _emit_done(True, action="start", state="partial", public_url_repair_warning="WordPress not installed yet")
                return
        else:
            code, out = _wp_cli(sid, "core", "is-installed")
            if code == 0:
                yield _log("WordPress is installed.")
            else:
                yield _log(out or "Complete setup in the browser, then Start again for URL repair.")

    yield _log("--- Public URL repair ---")
    repair = repair_stack_public_urls(sid)
    if repair.get("output"):
        yield _log(str(repair["output"]))
    if repair.get("ok") is False and not repair.get("skipped"):
        yield _log(f"Warning: {repair.get('error') or 'URL repair failed'}")

    from dev_stack_routes import sync_dev_stack_routes

    sync_dev_stack_routes(sid)
    from dev_stack_access import stack_access_info

    try:
        access = stack_access_info(sid)
    except Exception as exc:
        access = {"stack_id": sid, "error": str(exc)}

    yield _emit_done(
        True,
        action="start",
        state="running",
        public_url_repair=repair,
        access=access,
    )


def _stream_destroy(sid: str) -> Iterator[dict[str, Any]]:
    import shutil

    from dev_stack_routes import sync_dev_stack_routes

    slug = _slugify(sid)
    project = _compose_project_name(sid)
    stack_dir = stack_dir_for(sid)
    compose_file = stack_dir / "docker-compose.yml"

    if compose_file.is_file():
        yield _log("--- Docker Compose down ---")
        code, _ = yield from _stream_stack_compose(sid, "down", "-v", "--remove-orphans")
        if code != 0:
            yield _emit_done(
                False,
                error="docker compose down failed; stack files were not removed",
                action="destroy",
            )
            return
    else:
        yield _log("Compose file missing; pruning leftover Docker resources.")

    prune = _prune_devstack_project(project)
    if prune:
        yield _log("--- Docker prune ---")
        yield _log(prune)

    if stack_dir.is_dir():
        shutil.rmtree(stack_dir)
        yield _log(f"Removed stack directory: {stack_dir}")

    cfg = load_platform_config()
    cfg["dev_stacks"] = [
        s for s in (cfg.get("dev_stacks") or []) if _slugify(str(s.get("id") or "")) != slug
    ]
    save_platform_config(cfg)
    yield _log("Removed stack from platform config.")

    sync_dev_stack_routes(sid)
    yield _log("Updated Traefik dev-stack routes.")

    yield _emit_done(True, action="destroy", state="destroyed", project=project)
