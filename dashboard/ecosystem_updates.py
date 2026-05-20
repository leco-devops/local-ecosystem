"""Read update-catalog output written by leco-update-catalog Docker service."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(os.getenv("DASHBOARD_PROJECT_ROOT", "/project"))
CONFIG_DIR = PROJECT_ROOT / "ecosystem-stack" / "config"
GENERATED = CONFIG_DIR / "generated"

UPDATES_JSON = GENERATED / "ecosystem-updates.json"
OLLAMA_CATALOG_JSON = GENERATED / "llm-catalog-ollama.json"
AIRLLM_CATALOG_JSON = GENERATED / "llm-catalog-airllm.json"
META_JSON = GENERATED / "catalog-meta.json"
SERVICES_CFG = CONFIG_DIR / "update-watcher-services.json"
SCHEDULE_JSON = CONFIG_DIR / "update-catalog-schedule.json"
READ_STATE_JSON = GENERATED / "update-catalog-read-state.json"

WATCHER_CONTAINER = "leco-update-catalog"

DEFAULT_SCHEDULE: dict[str, Any] = {
    "mode": "interval",
    "interval_hours": 6,
    "fixed_times_utc": ["06:00", "18:00"],
}


def _read(path: Path, default: dict) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else default
    except (OSError, ValueError):
        return default


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_ecosystem_updates() -> dict:
    data = _read(UPDATES_JSON, {"ok": False, "error": "catalog not generated yet"})
    if not data.get("generated_at"):
        data.setdefault(
            "hint",
            "Start leco-update-catalog: ./ecosystem-stack/services/update-catalog.sh start",
        )
    return data


def load_llm_catalog(backend: str) -> dict:
    path = OLLAMA_CATALOG_JSON if backend == "ollama" else AIRLLM_CATALOG_JSON
    data = _read(path, {"ok": False, "backend": backend, "models": [], "error": "catalog not generated yet"})
    data.setdefault("backend", backend)
    if not data.get("generated_at"):
        data.setdefault(
            "hint",
            "Run ./ecosystem-stack/services/update-catalog.sh run-once",
        )
    return data


def load_catalog_meta() -> dict:
    return _read(META_JSON, {})


def load_schedule() -> dict:
    raw = _read(SCHEDULE_JSON, dict(DEFAULT_SCHEDULE))
    mode = str(raw.get("mode") or "interval").strip().lower()
    if mode not in ("interval", "fixed"):
        mode = "interval"
    interval = float(raw.get("interval_hours") or DEFAULT_SCHEDULE["interval_hours"])
    interval = max(1.0, min(interval, 168.0))
    times = raw.get("fixed_times_utc") or DEFAULT_SCHEDULE["fixed_times_utc"]
    if not isinstance(times, list):
        times = DEFAULT_SCHEDULE["fixed_times_utc"]
    cleaned_times: list[str] = []
    for t in times:
        s = str(t).strip()
        if len(s) >= 4 and ":" in s:
            cleaned_times.append(s[:5])
    if not cleaned_times:
        cleaned_times = list(DEFAULT_SCHEDULE["fixed_times_utc"])
    return {
        "mode": mode,
        "interval_hours": interval,
        "fixed_times_utc": cleaned_times,
        "path": str(SCHEDULE_JSON),
    }


def save_schedule(mode: str, interval_hours: float, fixed_times_utc: list[str]) -> dict:
    mode_l = str(mode or "interval").strip().lower()
    if mode_l not in ("interval", "fixed"):
        mode_l = "interval"
    interval = max(1.0, min(float(interval_hours or 6), 168.0))
    times: list[str] = []
    for t in fixed_times_utc or []:
        s = str(t).strip()
        if ":" in s:
            parts = s.split(":")
            try:
                h, m = int(parts[0]), int(parts[1][:2])
                if 0 <= h <= 23 and 0 <= m <= 59:
                    times.append(f"{h:02d}:{m:02d}")
            except (TypeError, ValueError):
                continue
    if mode_l == "fixed" and not times:
        times = list(DEFAULT_SCHEDULE["fixed_times_utc"])
    payload = {
        "_comment": "Edited from LEco DevOps Overview. Restart leco-update-catalog to apply immediately.",
        "mode": mode_l,
        "interval_hours": interval,
        "fixed_times_utc": times,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _write(SCHEDULE_JSON, payload)
    _restart_watcher_if_running()
    return {"ok": True, "schedule": load_schedule()}


def _restart_watcher_if_running() -> None:
    try:
        st = _watcher_container_status()
        if st.get("exists") and st.get("status") == "running":
            subprocess.run(
                ["docker", "restart", WATCHER_CONTAINER],
                capture_output=True,
                timeout=30,
            )
    except (OSError, subprocess.TimeoutExpired):
        pass


def load_read_state() -> dict:
    return _read(
        READ_STATE_JSON,
        {
            "marked_read_at": None,
            "updates_generated_at": None,
            "acknowledged_models": [],
            "acknowledged_stack": [],
        },
    )


def _stack_fingerprint(svc: dict) -> str:
    sid = str(svc.get("id") or "")
    latest = (svc.get("latest") or {}).get("full") or (svc.get("latest") or {}).get("tag") or ""
    return f"{sid}|{latest}"


def mark_all_read() -> dict:
    updates = load_ecosystem_updates()
    stack_pending = [s for s in (updates.get("services") or []) if s.get("status") == "update_available"]
    models = [str(m.get("name") or "") for m in (updates.get("model_alerts") or []) if m.get("name")]
    payload = {
        "marked_read_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updates_generated_at": updates.get("generated_at"),
        "acknowledged_models": models,
        "acknowledged_stack": [_stack_fingerprint(s) for s in stack_pending],
    }
    _write(READ_STATE_JSON, payload)
    return {"ok": True, "read_state": payload}


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        s = str(ts).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _parse_hhmm_utc(s: str) -> tuple[int, int] | None:
    try:
        parts = str(s).strip().split(":")
        h, m = int(parts[0]), int(parts[1])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except (IndexError, TypeError, ValueError):
        pass
    return None


def compute_next_check(schedule: dict, last_check_at: datetime | None, now: datetime | None = None) -> dict:
    """Next catalog check from schedule mode (interval or fixed UTC times)."""
    now = now or datetime.now(timezone.utc)
    mode = schedule.get("mode") or "interval"

    if mode == "fixed":
        times = schedule.get("fixed_times_utc") or []
        candidates: list[datetime] = []
        for t in times:
            hm = _parse_hhmm_utc(t)
            if not hm:
                continue
            h, m = hm
            for day_off in (0, 1):
                base = (now + timedelta(days=day_off)).replace(
                    hour=h, minute=m, second=0, microsecond=0, tzinfo=timezone.utc
                )
                if base > now:
                    candidates.append(base)
        if last_check_at:
            for t in times:
                hm = _parse_hhmm_utc(t)
                if not hm:
                    continue
                h, m = hm
                base = now.replace(hour=h, minute=m, second=0, microsecond=0, tzinfo=timezone.utc)
                if base <= now and base > last_check_at:
                    candidates.append(base + timedelta(days=1))
        next_dt = min(candidates) if candidates else now + timedelta(hours=6)
        label = "at " + ", ".join(str(x) for x in times) + " UTC"
        return {
            "mode": "fixed",
            "interval_hours": None,
            "interval_label": label,
            "fixed_times_utc": times,
            "last_check_at": last_check_at.isoformat().replace("+00:00", "Z") if last_check_at else None,
            "next_check_at": next_dt.isoformat().replace("+00:00", "Z"),
            "next_check_in_minutes": max(0, int((next_dt - now).total_seconds() / 60)),
            "overdue": False,
        }

    interval_h = float(schedule.get("interval_hours") or 6)
    next_dt = (last_check_at + timedelta(hours=interval_h)) if last_check_at else None
    overdue = bool(next_dt and next_dt < now)
    return {
        "mode": "interval",
        "interval_hours": interval_h,
        "interval_label": f"every {int(interval_h)}h" if interval_h == int(interval_h) else f"every {interval_h}h",
        "fixed_times_utc": schedule.get("fixed_times_utc") or [],
        "last_check_at": last_check_at.isoformat().replace("+00:00", "Z") if last_check_at else None,
        "next_check_at": next_dt.isoformat().replace("+00:00", "Z") if next_dt else None,
        "next_check_in_minutes": int((next_dt - now).total_seconds() / 60) if next_dt and next_dt > now else 0,
        "overdue": overdue,
    }


def sleep_seconds_for_schedule(schedule: dict) -> float:
    """Seconds until next run (for watcher loop)."""
    now = datetime.now(timezone.utc)
    meta = load_catalog_meta()
    last = _parse_iso(meta.get("generated_at"))
    nxt = compute_next_check(schedule, last, now)
    next_dt = _parse_iso(nxt.get("next_check_at"))
    if not next_dt:
        return max(3600.0, float(schedule.get("interval_hours") or 6) * 3600)
    secs = (next_dt - now).total_seconds()
    return max(300.0, secs)


def _is_unread_model(name: str, read_state: dict, updates_generated_at: str | None) -> bool:
    if not name:
        return False
    if name not in (read_state.get("acknowledged_models") or []):
        return True
    marked_at = read_state.get("marked_read_at")
    gen_at = read_state.get("updates_generated_at")
    if marked_at and updates_generated_at and gen_at and updates_generated_at > gen_at:
        return name not in (read_state.get("acknowledged_models") or [])
    return False


def _is_unread_stack(svc: dict, read_state: dict, updates_generated_at: str | None) -> bool:
    fp = _stack_fingerprint(svc)
    if fp not in (read_state.get("acknowledged_stack") or []):
        return True
    gen_at = read_state.get("updates_generated_at")
    marked_at = read_state.get("marked_read_at")
    if marked_at and updates_generated_at and gen_at and updates_generated_at > gen_at:
        return fp not in (read_state.get("acknowledged_stack") or [])
    return False


def _watcher_container_status() -> dict:
    try:
        out = subprocess.run(
            [
                "docker",
                "inspect",
                "-f",
                "{{.State.Status}}|{{.State.StartedAt}}|{{.Config.Image}}",
                WATCHER_CONTAINER,
            ],
            capture_output=True,
            text=True,
            timeout=12,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"exists": False, "status": "unknown", "error": str(exc)[:120], "container": WATCHER_CONTAINER}
    if out.returncode != 0:
        return {"exists": False, "status": "not_deployed", "container": WATCHER_CONTAINER}
    parts = (out.stdout or "").strip().split("|", 2)
    return {
        "exists": True,
        "status": parts[0] if parts else "unknown",
        "started_at": parts[1] if len(parts) > 1 else "",
        "image": parts[2] if len(parts) > 2 else "",
        "container": WATCHER_CONTAINER,
    }


def collect_update_catalog_panel() -> dict:
    """Summary for Overview homepage: watcher status, schedule, available updates."""
    schedule = load_schedule()
    meta = load_catalog_meta()
    updates = load_ecosystem_updates()
    ollama_cat = load_llm_catalog("ollama")
    airllm_cat = load_llm_catalog("airllm")
    watcher = _watcher_container_status()
    read_state = load_read_state()

    last_dt = _parse_iso(meta.get("generated_at") or updates.get("generated_at"))
    sched_view = compute_next_check(schedule, last_dt)

    services = updates.get("services") or []
    stack_pending = [s for s in services if s.get("status") == "update_available"]
    model_alerts = updates.get("model_alerts") or []
    gen_at = updates.get("generated_at")

    stack_with_flags = []
    unread_stack = 0
    for s in stack_pending[:12]:
        unread = _is_unread_stack(s, read_state, gen_at)
        if unread:
            unread_stack += 1
        stack_with_flags.append({**s, "unread": unread})

    models_with_flags = []
    unread_models = 0
    for m in model_alerts[:20]:
        name = str(m.get("name") or "")
        unread = _is_unread_model(name, read_state, gen_at)
        if unread:
            unread_models += 1
        models_with_flags.append({**m, "unread": unread})

    watcher_ui = "not_deployed"
    if watcher.get("exists"):
        st = str(watcher.get("status") or "")
        if st == "running":
            watcher_ui = "running"
        elif st in ("exited", "dead", "paused"):
            watcher_ui = st
        else:
            watcher_ui = st or "unknown"

    return {
        "ok": True,
        "watcher": {
            **watcher,
            "ui_status": watcher_ui,
            "start_cmd": "./ecosystem-stack/services/update-catalog.sh start",
            "run_once_cmd": "./ecosystem-stack/services/update-catalog.sh run-once",
            "logs_cmd": "./ecosystem-stack/services/update-catalog.sh logs",
        },
        "schedule": {
            **sched_view,
            "config": schedule,
            "editable": True,
        },
        "read_state": {
            "marked_read_at": read_state.get("marked_read_at"),
            "has_marked_read": bool(read_state.get("marked_read_at")),
        },
        "catalog": {
            "ollama_model_count": ollama_cat.get("model_count") or len(ollama_cat.get("models") or []),
            "airllm_model_count": airllm_cat.get("model_count") or len(airllm_cat.get("models") or []),
            "ollama_new_count": ollama_cat.get("new_online_count", updates.get("ollama_new_count", 0)),
        },
        "updates": {
            "generated_at": gen_at,
            "stack_updates_available": len(stack_pending),
            "stack_pending": stack_with_flags[:8],
            "stack_unread_count": unread_stack,
            "model_alerts_count": len(model_alerts),
            "model_alerts": models_with_flags[:15],
            "model_unread_count": unread_models,
            "has_any": bool(stack_pending or model_alerts),
            "has_unread": unread_stack > 0 or unread_models > 0,
        },
        "help_links": {
            "updates": "/help?topic=ecosystem-updates",
            "ollama_catalog": "/help?topic=llm-catalog-ollama",
            "airllm_catalog": "/help?topic=llm-catalog-airllm",
            "service_doc": "/help?topic=update-catalog-service",
        },
    }
