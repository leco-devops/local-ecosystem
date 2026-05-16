#!/usr/bin/env python3
"""Filter ecosystem service names by default policy.

Usage (from core.sh):
    echo "ollama airllm webui" | python3 scripts/filter-services-by-policy.py start
    # → prints only services whose policy allows "start" action

Exit 0 always; outputs filtered list to stdout (space-separated).
Reads ecosystem-stack/config/service-default-policies.json relative to
this script's grandparent directory.
"""
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_DIR = SCRIPT_DIR.parent / "config"
POLICIES_FILE = CONFIG_DIR / "service-default-policies.json"


def load_policies() -> dict[str, str]:
    try:
        data = json.loads(POLICIES_FILE.read_text())
        return {e["target_id"]: e["policy"] for e in data.get("policies", []) if "target_id" in e}
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return {}


def script_to_target_id(script_name: str) -> str | None:
    """Best-effort map from script/compose-service name to target ID prefix."""
    prefixes = [f"ai-{script_name}", f"cf-{script_name}", f"infra-{script_name}"]
    policies = load_policies()
    for p in prefixes:
        if p in policies:
            return p
    return None


def main():
    if len(sys.argv) < 2:
        print("Usage: echo 'svc1 svc2' | filter-services-by-policy.py <action>", file=sys.stderr)
        sys.exit(0)

    action = sys.argv[1]
    services = sys.stdin.read().split()
    policies = load_policies()

    result = []
    for svc in services:
        tid = script_to_target_id(svc)
        if tid is None:
            result.append(svc)
            continue
        pol = policies.get(tid, "start")
        if action in ("start", "deploy", "restart"):
            if pol not in ("stop", "offloaded"):
                result.append(svc)
        elif action in ("stop", "pause", "remove", "reset", "recreate", "unpause"):
            if pol != "offloaded":
                result.append(svc)
        else:
            result.append(svc)

    print(" ".join(result))


if __name__ == "__main__":
    main()
