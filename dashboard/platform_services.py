"""Platform bundle and ecosystem service management."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

from platform_config import (
    BUNDLE_TO_SERVICE,
    START_ORDER,
    _PROJECT_ROOT,
    enabled_services,
    load_platform_config,
    load_profiles,
    save_platform_config,
)

STACK_SH = _PROJECT_ROOT / "ecosystem-stack" / "ecosystem-stack.sh"

BUNDLE_META: dict[str, dict[str, str]] = {
    "cloudflare-full": {
        "label": "Cloudflare-local (full mimic)",
        "service": "cloudflare-local",
    },
    "ai-full": {
        "label": "AI / LLM plane (Ollama, AirLLM, WebUI, update-catalog)",
        "service": "ai-full",
    },
    "infra-full": {
        "label": "Shared infra (MySQL, Redis, Mailpit, …)",
        "service": "infra",
    },
}


def _service_running(svc: str) -> bool:
    script = _PROJECT_ROOT / "ecosystem-stack" / "services" / f"{svc}.sh"
    if not script.is_file():
        return False
    try:
        text = script.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith('NAME="'):
                name = line.split("=", 1)[1].strip().strip('"')
                break
        else:
            return False
        proc = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True,
            text=True,
        )
        return proc.stdout.strip() == "true"
    except OSError:
        return False


def catalog() -> dict[str, Any]:
    profiles = load_profiles()
    return {
        "profiles": {k: v.get("description", "") for k, v in profiles.items()},
        "bundles": BUNDLE_META,
        "start_order": list(START_ORDER),
    }


def list_services() -> list[dict[str, Any]]:
    cfg = load_platform_config()
    enabled = set(enabled_services())
    items: list[dict[str, Any]] = []
    for svc in START_ORDER:
        items.append(
            {
                "id": svc,
                "enabled": svc in enabled,
                "running": _service_running(svc),
                "type": "ecosystem",
            }
        )
    for bid, meta in BUNDLE_META.items():
        active = bid in (cfg.get("enabled_bundles") or [])
        items.append(
            {
                "id": bid,
                "label": meta["label"],
                "enabled": active,
                "running": _service_running(meta["service"]) if active else False,
                "type": "bundle",
            }
        )
    return items


def _run_stack(action: str, service: str) -> tuple[bool, str]:
    if not STACK_SH.is_file():
        return False, "ecosystem-stack.sh missing"
    proc = subprocess.run(
        [str(STACK_SH), action, service],
        capture_output=True,
        text=True,
        cwd=str(_PROJECT_ROOT),
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, out.strip()


def service_action(service_id: str, action: str) -> dict[str, Any]:
    cfg = load_platform_config()
    if service_id in BUNDLE_META:
        svc = BUNDLE_META[service_id]["service"]
        if action == "install" or action == "start":
            bundles = list(cfg.get("enabled_bundles") or [])
            if service_id not in bundles:
                bundles.append(service_id)
            cfg["enabled_bundles"] = bundles
            for s in BUNDLE_TO_SERVICE.get(service_id, []):
                es = list(cfg.get("enabled_services") or enabled_services())
                if s not in es:
                    es.append(s)
                cfg["enabled_services"] = es
            save_platform_config(cfg)
            ok, out = _run_stack("start", svc)
            return {"ok": ok, "output": out}
        if action in ("stop", "disable"):
            ok, out = _run_stack("stop", svc)
            if action == "disable":
                cfg["enabled_bundles"] = [b for b in (cfg.get("enabled_bundles") or []) if b != service_id]
                save_platform_config(cfg)
            return {"ok": ok, "output": out}
    if action == "install":
        action = "start"
        es = list(cfg.get("enabled_services") or enabled_services())
        if service_id not in es:
            es.append(service_id)
        cfg["enabled_services"] = es
        save_platform_config(cfg)
    if action == "disable":
        es = [s for s in (cfg.get("enabled_services") or []) if s != service_id]
        cfg["enabled_services"] = es
        save_platform_config(cfg)
        ok, out = _run_stack("stop", service_id)
        return {"ok": ok, "output": out}
    ok, out = _run_stack(action, service_id)
    return {"ok": ok, "output": out}


def apply_traefik() -> dict[str, Any]:
    script = _PROJECT_ROOT / "scripts" / "render-platform-traefik.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--write"],
        capture_output=True,
        text=True,
        cwd=str(_PROJECT_ROOT),
    )
    ok_render = proc.returncode == 0
    proc2 = subprocess.run(
        [str(_PROJECT_ROOT / "ecosystem-stack" / "services" / "traefik.sh"), "heal"],
        capture_output=True,
        text=True,
        cwd=str(_PROJECT_ROOT),
    )
    return {
        "ok": ok_render and proc2.returncode == 0,
        "render": (proc.stdout or proc.stderr or "").strip(),
        "heal": ((proc2.stdout or "") + (proc2.stderr or "")).strip(),
    }
