"""NDJSON streaming for dev stack lifecycle actions (live compose / init logs)."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from typing import Any

from control import _yield_run
from dev_stack_app_urls import (
    _compose_exec,
    _compose_services,
    _MAGENTO_CLI,
    _magento_cli,
    _wp_cli,
    repair_stack_public_urls,
)
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


def _stream_wait_magento(stack_id: str, *, timeout: int = 900) -> Iterator[dict[str, Any] | Any]:
    deadline = time.monotonic() + timeout
    last_emit = 0.0
    while time.monotonic() < deadline:
        code_cli, cli_out = _compose_exec(stack_id, "magento", "test", "-x", _MAGENTO_CLI, timeout=30)
        if code_cli == 0:
            code, out = _magento_cli(stack_id, "setup:db:status", timeout=120)
            if code == 0:
                yield _log("Magento is installed.")
                return (True, "")
            hint = (out or "Magento CLI ready; setup still in progress…").strip()
        else:
            hint = (cli_out or "Waiting for Magento install (bin/magento)…").strip()
        now = time.monotonic()
        if now - last_emit >= 15:
            yield _log(hint)
            last_emit = now
        time.sleep(10)
    yield _log(f"Timed out after {timeout}s waiting for Magento install.")
    return (False, "")


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

    if action == "repair":
        yield from _stream_repair(sid)
        return

    if action in ("reinstall", "redeploy"):
        yield from _stream_reinstall(sid)
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


def _stream_apply_config_updates(sid: str, *, action: str) -> Iterator[dict[str, Any] | bool]:
    from dev_stack_redeploy import apply_stack_config_updates

    yield _log("--- Apply configuration updates (keeps manual file edits) ---")
    try:
        logs = apply_stack_config_updates(sid)
    except Exception as exc:
        yield _log(f"Error: {exc}\n")
        yield _emit_done(False, error=str(exc), action=action)
        return False
    for line in logs:
        yield _log(line)
    return True


def _stream_regenerate_files(sid: str, *, action: str) -> Iterator[dict[str, Any] | bool]:
    from dev_stack_redeploy import regenerate_stack_files

    yield _log("--- Regenerate stack files from template (reverts manual config edits) ---")
    try:
        _, logs = regenerate_stack_files(sid)
    except Exception as exc:
        yield _log(f"Error: {exc}\n")
        yield _emit_done(False, error=str(exc), action=action)
        return False
    for line in logs:
        yield _log(line)
    return True


def _stream_repair(sid: str) -> Iterator[dict[str, Any]]:
    ok = yield from _stream_apply_config_updates(sid, action="repair")
    if not ok:
        return
    yield _log("--- Docker Compose up ---")
    code, _ = yield from _stream_stack_compose(sid, "up", "-d")
    if code != 0:
        yield _emit_done(False, error="docker compose up failed", action="repair")
        return
    cfg = load_platform_config()
    for s in cfg.get("dev_stacks") or []:
        if str(s.get("id")) == sid or _slugify(str(s.get("id") or "")) == _slugify(sid):
            s["state"] = "running"
    save_platform_config(cfg)
    yield from _stream_post_start_finish(sid, action="repair")


def _stream_reinstall(sid: str) -> Iterator[dict[str, Any]]:
    ok = yield from _stream_regenerate_files(sid, action="reinstall")
    if not ok:
        return
    yield _log("--- Docker Compose down (volumes removed) ---")
    code, _ = yield from _stream_stack_compose(sid, "down", "-v", "--remove-orphans")
    if code != 0:
        yield _emit_done(False, error="docker compose down failed", action="reinstall")
        return
    project = _compose_project_name(sid)
    prune = _prune_devstack_project(project)
    if prune:
        yield _log("--- Docker prune ---")
        yield _log(prune)
    yield from _stream_start(sid, lifecycle_action="reinstall")


def _stream_post_start_finish(sid: str, *, action: str) -> Iterator[dict[str, Any]]:
    """Magento/WordPress wait + URL repair after repair (not full compose up log)."""
    from dev_stack_app_urls import repair_stack_public_urls, wait_for_stack_app_ready
    from dev_stack_routes import sync_dev_stack_routes

    services = _compose_services(sid)
    meta = load_stack_meta(sid)
    template = str(meta.get("template") or "").strip().lower()

    if "wp-sample-init" in services or template in ("wordpress", "woocommerce"):
        yield _log("--- WordPress install ---")
        if "wp-sample-init" in services:
            ok, _ = yield from _stream_wait_wordpress(sid)
            if not ok:
                yield _log("(URL repair skipped until install completes; try Repair again later.)")
                sync_dev_stack_routes(sid)
                yield _emit_done(True, action=action, state="partial", public_url_repair_warning="WordPress not installed yet")
                return

    if template in ("magento-min", "magento-full"):
        yield _log("--- Magento install ---")
        ok, _ = yield from _stream_wait_magento(sid)
        if not ok:
            yield _log("(URL repair skipped until install completes; try Repair again later.)")
            sync_dev_stack_routes(sid)
            yield _emit_done(True, action=action, state="partial", public_url_repair_warning="Magento not installed yet")
            return

    yield _log("--- Public URL repair ---")
    repair = repair_stack_public_urls(sid)
    if repair.get("output"):
        yield _log(str(repair["output"]))
    if repair.get("ok") is False and not repair.get("skipped"):
        yield _log(f"Warning: {repair.get('error') or 'URL repair failed'}")

    sync_dev_stack_routes(sid)
    from dev_stack_access import stack_access_info

    try:
        access = stack_access_info(sid)
    except Exception as exc:
        access = {"stack_id": sid, "error": str(exc)}

    yield _emit_done(
        True,
        action=action,
        state="running",
        public_url_repair=repair,
        access=access,
    )


def _stream_start(sid: str, *, lifecycle_action: str = "start") -> Iterator[dict[str, Any]]:
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
            action=lifecycle_action,
            image_errors=image_errors,
        )
        return
    yield _log("Container images OK (local cache or registry).\n")

    yield _log("--- Docker Compose up ---")
    code, _ = yield from _stream_stack_compose(sid, "up", "-d")
    if code != 0:
        yield _emit_done(False, error="docker compose up failed", action=lifecycle_action)
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
                yield _emit_done(
                    True,
                    action=lifecycle_action,
                    state="partial",
                    public_url_repair_warning="WordPress not installed yet",
                )
                return
        else:
            code, out = _wp_cli(sid, "core", "is-installed")
            if code == 0:
                yield _log("WordPress is installed.")
            else:
                yield _log(out or "Complete setup in the browser, then Start again for URL repair.")

    if template in ("magento-min", "magento-full"):
        yield _log("--- Magento install ---")
        ok, _ = yield from _stream_wait_magento(sid)
        if not ok:
            yield _log("(URL repair skipped until install completes; try Start again later.)")
            from dev_stack_routes import sync_dev_stack_routes

            sync_dev_stack_routes(sid)
            yield _emit_done(
                True,
                action=lifecycle_action,
                state="partial",
                public_url_repair_warning="Magento not installed yet",
            )
            return

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
        action=lifecycle_action,
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
