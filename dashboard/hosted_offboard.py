"""Remove hosted app from registry via LEco DevOps ecosystem-unregister (Traefik strip, local CF, registry)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from leco_control import load_leco_registry_entries, resolve_manifest_path
from hosting_layout import manifest_rel_uses_hosting_layout, remove_hosting_for_slug
from leco_subprocess import PROJECT_ROOT, run_ecosystem_unregister
from traefik_dynamic_file import list_routers_services_summary
from traefik_manifest_keys import manifest_traefik_keys_dict


def offboard_hosted_app(
    slug: str,
    *,
    strip_traefik: bool,
    clean_local_cf: bool,
    compose_down: bool = True,
    compose_volumes: bool = False,
) -> dict[str, Any]:
    nid = slug.strip()
    eco = Path(PROJECT_ROOT).resolve()
    entries = load_leco_registry_entries()
    in_registry = any(
        isinstance(e, dict) and str(e.get("id") or "").strip() == nid for e in entries
    )
    manifest_rel = ""
    for e in entries:
        if isinstance(e, dict) and str(e.get("id") or "").strip() == nid:
            manifest_rel = str(e.get("manifest") or "").strip()
            break

    if not in_registry:
        hr = remove_hosting_for_slug(eco, nid)
        errs = hr.get("hosting_cleanup_errors")
        ok = not errs
        log_txt = (
            f"No registry entry for {slug!r} — removed hosting materialization only "
            f"(app-available when present).\n"
            f"Paths touched: {hr.get('hosting_paths_removed') or []}\n"
        )
        return {
            "ok": ok,
            "slug": slug,
            "strip_traefik": strip_traefik,
            "clean_local_cf": clean_local_cf,
            "exit_code": 0 if ok else 1,
            "leco_log": log_txt,
            "traefik": {"via": "skipped (not in registry)"},
            "local_cf": {"via": "skipped (not in registry)"},
            "registry_removed": False,
            "not_in_registry": True,
            "hosting_cleanup": hr,
            **({"error": "; ".join(errs)} if errs else {}),
        }

    code, log = run_ecosystem_unregister(
        slug,
        strip_traefik=strip_traefik,
        clean_local_cf=clean_local_cf,
        compose_down=compose_down,
        compose_volumes=compose_volumes,
        timeout=300,
    )
    ok = code == 0
    result: dict[str, Any] = {
        "ok": ok,
        "slug": slug,
        "strip_traefik": strip_traefik,
        "clean_local_cf": clean_local_cf,
        "exit_code": code,
        "leco_log": log[-16000:] if log else "",
        "traefik": {"via": "leco-devops ecosystem-unregister"},
        "local_cf": {"via": "leco-devops ecosystem-unregister"},
        "registry_removed": ok,
    }
    if not ok:
        result["error"] = log[-2000:] if log else f"leco-devops exited {code}"
        if manifest_rel_uses_hosting_layout(manifest_rel):
            hr = remove_hosting_for_slug(eco, nid)
            result["hosting_fallback"] = hr
            if hr.get("hosting_removed") and not hr.get("hosting_cleanup_errors"):
                result["leco_log"] = (
                    (result.get("leco_log") or "")
                    + "\n--- hosting fallback: removed app-available after unregister failure ---\n"
                    + str(hr.get("hosting_paths_removed") or [])
                )
    return result


def traefik_routes_with_hosted_hints() -> dict[str, Any]:
    """GET payload: routers/services plus per-registry manifest key overlap."""
    base = list_routers_services_summary()
    if not base.get("ok"):
        return base

    router_key_set = {r["key"] for r in base.get("routers") or [] if isinstance(r, dict)}
    service_key_set = {s["key"] for s in base.get("services") or [] if isinstance(s, dict)}

    hints: list[dict[str, Any]] = []
    for entry in load_leco_registry_entries():
        slug = str(entry.get("id") or "").strip()
        if not slug:
            continue
        mp = entry.get("manifest")
        if not mp:
            continue
        abs_m = resolve_manifest_path(str(mp).strip())
        if not abs_m or not Path(abs_m).is_file():
            continue
        try:
            man = yaml.safe_load(Path(abs_m).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError, UnicodeDecodeError):
            continue
        if not isinstance(man, dict):
            continue
        rkeys, skeys = manifest_traefik_keys_dict(man)
        if not rkeys and not skeys:
            continue
        r_hit = [k for k in rkeys if k in router_key_set]
        s_hit = [k for k in skeys if k in service_key_set]
        hints.append(
            {
                "slug": slug,
                "label": str(entry.get("label") or slug),
                "manifest": mp,
                "router_keys": rkeys,
                "service_keys": skeys,
                "routers_present": r_hit,
                "services_present": s_hit,
                "in_traefik": bool(r_hit or s_hit),
            }
        )

    base["hosted_hints"] = hints
    return base
