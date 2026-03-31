"""Remove hosted app from registry; optionally strip Traefik routes and clean leco.local-cf resources."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from leco_control import LECO_REGISTRY_PATH, leco_meta_for_slug, load_leco_registry_entries
from local_cf_teardown import teardown_from_leco_local_cf_file
from traefik_dynamic_file import list_routers_services_summary, strip_router_service_keys
from traefik_manifest_keys import manifest_traefik_keys_dict


def _remove_registry_slug(slug: str) -> bool:
    if not os.path.isfile(LECO_REGISTRY_PATH):
        return False
    try:
        doc = yaml.safe_load(Path(LECO_REGISTRY_PATH).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, UnicodeDecodeError):
        return False
    if not isinstance(doc, dict):
        return False
    apps = doc.get("apps")
    if not isinstance(apps, list):
        return False
    nid = slug.strip()
    new_apps = [a for a in apps if not (isinstance(a, dict) and str(a.get("id") or "").strip() == nid)]
    if len(new_apps) == len(apps):
        return False
    doc["apps"] = new_apps
    Path(LECO_REGISTRY_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(LECO_REGISTRY_PATH).write_text(
        yaml.safe_dump(doc, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return True


def offboard_hosted_app(
    slug: str,
    *,
    strip_traefik: bool,
    clean_local_cf: bool,
) -> dict[str, Any]:
    meta = leco_meta_for_slug(slug)
    if not meta:
        return {"ok": False, "error": f"Unknown hosted app id: {slug!r}"}

    manifest_path = meta["manifest_path"]
    result: dict[str, Any] = {
        "ok": True,
        "slug": slug,
        "strip_traefik": strip_traefik,
        "clean_local_cf": clean_local_cf,
        "traefik": None,
        "local_cf": None,
    }

    try:
        raw = Path(manifest_path).read_text(encoding="utf-8")
        manifest = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError, UnicodeDecodeError):
        manifest = None

    if strip_traefik:
        if isinstance(manifest, dict):
            rkeys, skeys = manifest_traefik_keys_dict(manifest)
            if rkeys or skeys:
                nr, ns, err = strip_router_service_keys(rkeys, skeys)
                result["traefik"] = {
                    "routers_removed": nr,
                    "services_removed": ns,
                    "router_keys": rkeys,
                    "service_keys": skeys,
                    "warning": err,
                }
            else:
                result["traefik"] = {"skipped": "no routing keys in manifest"}
        else:
            result["traefik"] = {"skipped": "manifest unreadable"}

    if clean_local_cf:
        cf_file = Path(manifest_path).parent / "leco.local-cf.yaml"
        result["local_cf"] = teardown_from_leco_local_cf_file(cf_file)
        lc = result["local_cf"]
        if isinstance(lc, dict) and not lc.get("skipped") and not lc.get("ok", True):
            errs = lc.get("errors") or []
            result["ok"] = False
            result["error"] = "Local CF cleanup failed: " + "; ".join(str(x) for x in errs)[:500]
            return result

    if not _remove_registry_slug(slug):
        result["registry_removed"] = False
        result["ok"] = False
        result["error"] = "registry entry not removed (missing file or id mismatch)"
    else:
        result["registry_removed"] = True

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
        from leco_control import resolve_manifest_path

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
