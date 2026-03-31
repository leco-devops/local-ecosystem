"""Remove Traefik file-provider routers/services that belong to a manifest."""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

from leco_app.schema import ApplicationManifest
from leco_app.traefik_fragment import merge_fragments, routing_entry_fragment


def manifest_traefik_keys(manifest: ApplicationManifest) -> tuple[list[str], list[str]]:
    """Router and service keys to delete from ``dynamic.yml`` for this app."""
    tc = manifest.traefik_cleanup
    if tc and (tc.routers or tc.services):
        return list(tc.routers), list(tc.services)
    if not manifest.routing or not manifest.routing.entries:
        return [], []
    merged = merge_fragments([routing_entry_fragment(manifest, e) for e in manifest.routing.entries])
    http = merged.get("http") or {}
    r = list((http.get("routers") or {}).keys())
    s = list((http.get("services") or {}).keys())
    return r, s


def strip_traefik_dynamic_yml(
    path: Path,
    router_keys: Sequence[str],
    service_keys: Sequence[str],
    *,
    dry_run: bool,
) -> tuple[int, int, Path | None]:
    """Remove listed routers/services. Returns (routers_removed, services_removed, backup_path).

    Writes a ``.yml.bak`` copy beside ``path`` when anything is removed (not in dry-run).
    """
    raw = path.read_text(encoding="utf-8")
    data: Any = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError(f"Traefik file must be a YAML mapping: {path}")
    http = data.get("http")
    if not isinstance(http, dict):
        raise ValueError(f"Expected top-level 'http' mapping in {path}")
    routers = http.get("routers")
    services = http.get("services")
    if not isinstance(routers, dict):
        routers = {}
    if not isinstance(services, dict):
        services = {}

    to_drop_r = [k for k in router_keys if k in routers]
    to_drop_s = [k for k in service_keys if k in services]
    rr, ss = len(to_drop_r), len(to_drop_s)

    if dry_run:
        return rr, ss, None
    if rr == 0 and ss == 0:
        return 0, 0, None

    for k in to_drop_r:
        del routers[k]
    for k in to_drop_s:
        del services[k]
    http["routers"] = routers
    http["services"] = services
    data["http"] = http

    bak = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, bak)
    path.write_text(
        yaml.safe_dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return rr, ss, bak
