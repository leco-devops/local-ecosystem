"""Merge manifest routing into local-ecosystem traefik/dynamic.yml (atomic write + .bak)."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from leco_app.schema import ApplicationManifest
from leco_app.traefik_fragment import (
    local_cf_adapter_host_aliases_fragment,
    merge_fragments,
    routing_entry_fragment,
)


def atomic_write_dynamic_yaml(p: Path, data: dict[str, Any]) -> tuple[bool, str | None]:
    """Write YAML via temp file + os.replace; copy previous file to .bak."""
    text = yaml.safe_dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
    p.parent.mkdir(parents=True, exist_ok=True)
    bak = p.with_suffix(p.suffix + ".bak")
    if p.is_file():
        shutil.copy2(p, bak)
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".tmp.yml",
            prefix="dynamic.",
            dir=str(p.parent),
            delete=False,
        ) as fh:
            fh.write(text)
            tmp_path = fh.name
        os.replace(tmp_path, p)
        tmp_path = ""
        return True, None
    except OSError as exc:
        return False, str(exc)
    finally:
        if tmp_path and os.path.isfile(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def merge_manifest_routing_into_dynamic_yml(
    manifest: ApplicationManifest,
    dynamic_yml: Path,
) -> tuple[bool, str]:
    """Upsert http.routers and http.services from manifest.routing + optional local CF host aliases."""
    frags: list[dict[str, Any]] = []
    if manifest.routing and manifest.routing.entries:
        frags.extend([routing_entry_fragment(manifest, e) for e in manifest.routing.entries])
    cf_frag = local_cf_adapter_host_aliases_fragment(manifest)
    if cf_frag:
        frags.append(cf_frag)
    if not frags:
        return True, "No routing.entries and no cloudflare.localCfPublicPrefix — Traefik merge skipped."
    dp = dynamic_yml.resolve()
    if not dp.is_file():
        return False, f"Traefik dynamic file not found: {dp}"

    piece = merge_fragments(frags)
    f_http = piece.get("http") or {}
    fr = f_http.get("routers")
    fs = f_http.get("services")
    if not isinstance(fr, dict) or not isinstance(fs, dict):
        return False, "Internal error: empty routing fragment"

    try:
        raw = dp.read_text(encoding="utf-8")
        base: Any = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError) as e:
        return False, f"Read dynamic.yml: {e}"
    if not isinstance(base, dict):
        return False, "dynamic.yml root must be a mapping"

    http = base.get("http")
    if not isinstance(http, dict):
        http = {}
    routers = http.get("routers")
    services = http.get("services")
    if not isinstance(routers, dict):
        routers = {}
    if not isinstance(services, dict):
        services = {}

    routers.update(fr)
    services.update(fs)
    http["routers"] = routers
    http["services"] = services
    base["http"] = http

    ok, err = atomic_write_dynamic_yaml(dp, base)
    if not ok:
        return False, err or "atomic write failed"
    return (
        True,
        f"Traefik: merged {len(fr)} router(s) and {len(fs)} service(s) into {dp.name} "
        "(backup dynamic.yml.bak; reloads via file watch).",
    )
