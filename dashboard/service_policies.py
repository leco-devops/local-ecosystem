"""Per-service default policies for the Control tab.

Policies: start | stop | offloaded.
- start:     included in bulk start/restart (today's default).
- stop:      skipped on bulk start; still stopped on bulk stop.
- offloaded: excluded from all bulk ecosystem ops; manual card/CLI only.

Storage: ecosystem-stack/config/service-default-policies.json (runtime).
Missing targets default to "start".
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from control_targets import AI_TARGETS, CF_TARGETS, FILE_TRANSFER_TARGETS, INFRA_TARGETS

VALID_POLICIES = frozenset({"start", "stop", "offloaded"})

PROJECT_ROOT = os.getenv("DASHBOARD_PROJECT_ROOT", "/project")
_POLICIES_FILE = os.path.join(PROJECT_ROOT, "ecosystem-stack", "config", "service-default-policies.json")


def _all_target_ids() -> set[str]:
    ids: set[str] = set()
    for t in AI_TARGETS:
        ids.add(t["id"])
    for t in CF_TARGETS:
        ids.add(t["id"])
    for t in INFRA_TARGETS:
        ids.add(t["id"])
    for t in FILE_TRANSFER_TARGETS:
        ids.add(t["id"])
    ids.add("stack-file-transfer-all")
    return ids


def load_policies() -> dict[str, Any]:
    """Return {target_id: policy_str} for every known target."""
    raw: dict[str, str] = {}
    try:
        with open(_POLICIES_FILE, "r") as f:
            data = json.load(f)
        for entry in data.get("policies", []):
            tid = entry.get("target_id", "")
            pol = entry.get("policy", "start")
            if tid and pol in VALID_POLICIES:
                raw[tid] = pol
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        pass
    all_ids = _all_target_ids()
    return {tid: raw.get(tid, "start") for tid in all_ids}


def save_policies(updates: dict[str, str]) -> dict[str, Any]:
    """Merge updates into the policies file. Returns the saved state."""
    current = load_policies()
    now = datetime.now(timezone.utc).isoformat()
    for tid, pol in updates.items():
        if tid in current and pol in VALID_POLICIES:
            current[tid] = pol
    entries = [
        {"target_id": tid, "policy": pol, "updated_at": now}
        for tid, pol in sorted(current.items())
    ]
    payload = {"_comment": "Per-service default policies. See docs/CF_LECO_SERVICE_MAP.md.", "policies": entries}
    os.makedirs(os.path.dirname(_POLICIES_FILE), exist_ok=True)
    with open(_POLICIES_FILE, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return current


def policy_for(target_id: str) -> str:
    policies = load_policies()
    return policies.get(target_id, "start")


def targets_for_bulk_action(action: str) -> dict[str, list[str]]:
    """Return skip/include sets for Python → shell bulk ops.

    For "start"/"deploy"/"restart":  skip stop+offloaded
    For "stop"/"pause"/"remove"/"reset"/"recreate": skip offloaded only
    For "unpause": skip offloaded only
    """
    policies = load_policies()
    skip: list[str] = []
    include: list[str] = []
    for tid, pol in policies.items():
        if action in ("start", "deploy", "restart"):
            if pol in ("stop", "offloaded"):
                skip.append(tid)
            else:
                include.append(tid)
        elif action in ("stop", "pause", "remove", "reset", "recreate", "unpause"):
            if pol == "offloaded":
                skip.append(tid)
            else:
                include.append(tid)
        else:
            include.append(tid)
    return {"skip": skip, "include": include}


def policy_for_container(container_name: str) -> str:
    """Resolve default policy for a Docker container name (SERVICE_MAP / control card)."""
    name = (container_name or "").strip()
    if not name:
        return "start"
    for t in (*AI_TARGETS, *CF_TARGETS, *INFRA_TARGETS, *FILE_TRANSFER_TARGETS):
        if (t.get("container") or "").strip() == name:
            return policy_for(str(t.get("id") or ""))
    return "start"


def script_name_for_target(target_id: str) -> str | None:
    """Map a target ID to its ecosystem-stack script name (for shell integration)."""
    for t in AI_TARGETS:
        if t["id"] == target_id:
            return t.get("script")
    for t in CF_TARGETS:
        if t["id"] == target_id:
            return t.get("compose_service")
    for t in INFRA_TARGETS:
        if t["id"] == target_id:
            return t.get("compose_service")
    return None
