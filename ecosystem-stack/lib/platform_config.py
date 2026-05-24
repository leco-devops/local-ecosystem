"""LEco platform config: install profiles, enabled services, leco-platform.yaml."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

_LIB = Path(__file__).resolve().parent
_STACK = _LIB.parent
PROJECT_ROOT = _STACK.parent
PROFILES_FILE = _STACK / "config" / "install-profiles.yaml"
CATALOG_FILE = _STACK / "config" / "component-catalog.yaml"
PLATFORM_EXAMPLE = PROJECT_ROOT / "config" / "leco-platform.yaml.example"
PLATFORM_FILE = PROJECT_ROOT / "config" / "leco-platform.yaml"
LEGACY_SELECTION = _STACK / "config" / "install-selection.env"

START_ORDER = [
    "traefik",
    "postgres",
    "ollama",
    "airllm",
    "webui",
    "n8n",
    "dashboard",
    "update-catalog",
    "cloudflare-local",
    "infra",
    "file-transfer",
]

BUNDLE_TO_SERVICE = {
    "edge": ["traefik", "dashboard"],
    "cloudflare-full": ["cloudflare-local"],
    "ai-full": ["ollama", "airllm", "webui", "update-catalog"],
    "infra-full": ["infra"],
    "file-transfer-full": ["file-transfer"],
}


def _yaml_load(path: Path) -> Any:
    if not path.is_file():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_profiles() -> dict[str, Any]:
    raw = _yaml_load(PROFILES_FILE) or {}
    return raw.get("profiles") if isinstance(raw.get("profiles"), dict) else {}


def resolve_profile(name: str) -> dict[str, Any]:
    profiles = load_profiles()
    if name not in profiles:
        raise KeyError(f"Unknown install profile: {name}")
    prof = profiles[name]
    services: list[str] = []
    bundles: list[str] = []
    if prof.get("extends"):
        parent = resolve_profile(str(prof["extends"]))
        services = list(parent.get("services") or [])
        bundles = list(parent.get("bundles") or [])
    for s in prof.get("services") or []:
        if s not in services:
            services.append(str(s))
    for b in prof.get("bundles") or []:
        if b not in bundles:
            bundles.append(str(b))
    return {
        "services": services,
        "bundles": bundles,
        "ai": prof.get("ai") or {},
        "deployment_mode": prof.get("deployment_mode"),
    }


def profile_to_enabled_services(profile_name: str) -> list[str]:
    resolved = resolve_profile(profile_name)
    enabled: set[str] = set()
    for bundle in resolved.get("bundles") or []:
        for svc in BUNDLE_TO_SERVICE.get(bundle, []):
            enabled.add(svc)
    for svc in resolved.get("services") or []:
        enabled.add(svc)
    order = [s for s in START_ORDER if s in enabled]
    for s in sorted(enabled):
        if s not in order:
            order.append(s)
    return order


def _parse_legacy_selection() -> list[str]:
    if not LEGACY_SELECTION.is_file():
        return []
    text = LEGACY_SELECTION.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("SELECTED_SERVICES="):
            val = line.split("=", 1)[1].strip().strip('"').strip("'")
            if val:
                return val.split()
    return []


def load_platform_config() -> dict[str, Any]:
    raw = _yaml_load(PLATFORM_FILE)
    if isinstance(raw, dict):
        return raw
    legacy = _parse_legacy_selection()
    if legacy:
        return {
            "deployment_mode": "local",
            "enabled_services": legacy,
            "enabled_bundles": [],
        }
    return {}


def enabled_services_list(cfg: dict[str, Any] | None = None) -> list[str] | None:
    """Return ordered enabled services, or None if unrestricted (all START_ORDER)."""
    cfg = cfg if cfg is not None else load_platform_config()
    if not cfg:
        return None
    services = cfg.get("enabled_services")
    if not isinstance(services, list) or not services:
        bundles = cfg.get("enabled_bundles") or []
        enabled: set[str] = set(cfg.get("enabled_services") or [])
        for bundle in bundles:
            for svc in BUNDLE_TO_SERVICE.get(str(bundle), []):
                enabled.add(svc)
        if not enabled and not bundles:
            return None
        services = list(enabled)
    order = [s for s in START_ORDER if s in services]
    for s in services:
        if s not in order:
            order.append(s)
    return order


def default_platform_config() -> dict[str, Any]:
    return {
        "deployment_mode": "local",
        "base_domain": "lh",
        "tls": {"mode": "mkcert"},
        "enabled_bundles": [],
        "enabled_services": list(START_ORDER),
        "dev_stacks": [],
        "ai": {"default_provider": "none"},
    }


def save_platform_config(cfg: dict[str, Any]) -> None:
    PLATFORM_FILE.parent.mkdir(parents=True, exist_ok=True)
    PLATFORM_FILE.write_text(
        yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def seed_ai_providers(cfg: dict[str, Any]) -> None:
    """Copy ai-providers.yaml.example when profile prefers cloud APIs and file is absent."""
    ai = cfg.get("ai") if isinstance(cfg.get("ai"), dict) else {}
    prof = str(cfg.get("install_profile") or "")
    prefer_cloud = bool(ai.get("prefer_cloud")) or prof == "ai-cloud"
    if not prefer_cloud and str(ai.get("default_provider") or "none") in ("none", "ollama"):
        return
    example = PROJECT_ROOT / "config" / "ai-providers.yaml.example"
    target = PROJECT_ROOT / "config" / "ai-providers.yaml"
    if not example.is_file():
        return
    if target.is_file():
        return
    text = example.read_text(encoding="utf-8")
    default = str(ai.get("default_provider") or "openai").strip() or "openai"
    if prefer_cloud and "default_provider:" in text:
        import re

        text = re.sub(
            r"^default_provider:\s*\S+",
            f"default_provider: {default}",
            text,
            count=1,
            flags=re.MULTILINE,
        )
    target.write_text(text, encoding="utf-8")
    try:
        target.chmod(0o600)
    except OSError:
        pass


def write_platform_from_profile(
    profile_name: str,
    *,
    deployment_mode: str = "local",
    base_domain: str = "lh",
    tls_mode: str | None = None,
) -> dict[str, Any]:
    resolved = resolve_profile(profile_name)
    cfg = default_platform_config()
    cfg["deployment_mode"] = deployment_mode
    cfg["base_domain"] = base_domain
    cfg["enabled_services"] = profile_to_enabled_services(profile_name)
    cfg["enabled_bundles"] = list(resolved.get("bundles") or [])
    if tls_mode:
        cfg["tls"] = {"mode": tls_mode}
    elif deployment_mode == "cloud":
        cfg["tls"] = {"mode": "acme", "acme_email": ""}
    ai = resolved.get("ai")
    if isinstance(ai, dict) and ai:
        cfg["ai"] = ai
    cfg["generated_at"] = datetime.now(timezone.utc).isoformat()
    cfg["install_profile"] = profile_name
    save_platform_config(cfg)
    seed_ai_providers(cfg)
    return cfg


def load_component_catalog() -> dict[str, Any]:
    raw = _yaml_load(CATALOG_FILE) or {}
    return raw if isinstance(raw, dict) else {}


def cli() -> int:
    parser = argparse.ArgumentParser(description="LEco platform config")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list-start-order", help="Print START_ORDER")
    p_en = sub.add_parser("enabled-services", help="Print enabled services for core.sh")
    p_en.add_argument("--all", action="store_true", help="Ignore platform file; print full order")
    p_prof = sub.add_parser("profile-services", help="Print services for a profile name")
    p_prof.add_argument("name")
    sub.add_parser("load-json", help="Print platform config as JSON")
    args = parser.parse_args()
    if args.cmd == "list-start-order":
        for s in START_ORDER:
            print(s)
        return 0
    if args.cmd == "enabled-services":
        if args.all:
            items = START_ORDER
        else:
            items = enabled_services_list()
            if items is None:
                items = START_ORDER
        for s in items:
            print(s)
        return 0
    if args.cmd == "profile-services":
        for s in profile_to_enabled_services(args.name):
            print(s)
        return 0
    if args.cmd == "load-json":
        print(json.dumps(load_platform_config() or default_platform_config()))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(cli())
