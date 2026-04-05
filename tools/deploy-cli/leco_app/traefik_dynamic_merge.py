"""Merge manifest routing into local-ecosystem hosting/traefik/dynamic.yml (atomic write)."""

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


def ensure_dynamic_yaml_file(p: Path) -> tuple[bool, str | None]:
    """Create recoverable writable dynamic YAML file when missing."""
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.is_file():
            return True, None
        bak = p.with_suffix(p.suffix + ".bak")
        if bak.is_file():
            shutil.copy2(bak, p)
            return True, None
        p.write_text("http:\n  routers: {}\n  services: {}\n", encoding="utf-8")
        return True, None
    except OSError as exc:
        return False, str(exc)


def atomic_write_dynamic_yaml(p: Path, data: dict[str, Any]) -> tuple[bool, str | None]:
    """Write YAML via temp file + os.replace."""
    text = yaml.safe_dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
    p.parent.mkdir(parents=True, exist_ok=True)
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
    ok_ensure, ensure_err = ensure_dynamic_yaml_file(dp)
    if not ok_ensure:
        return False, f"Traefik dynamic file create failed: {ensure_err}"

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
        "(reloads via file watch).",
    )
