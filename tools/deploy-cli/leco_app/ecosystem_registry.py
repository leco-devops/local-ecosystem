"""Register / unregister leco manifests in local-ecosystem config/leco-registry.yaml."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

_SLUG = re.compile(r"[^a-zA-Z0-9._-]+")


def _slug(s: str) -> str:
    t = _SLUG.sub("-", (s or "").strip().lower()).strip("-")
    return t or "app"


def _registry_path(ecosystem_root: Path) -> Path:
    return ecosystem_root / "config" / "leco-registry.yaml"


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"apps": []}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"apps": []}
    apps = data.get("apps")
    if not isinstance(apps, list):
        data["apps"] = []
    return data


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def register_in_ecosystem(
    ecosystem_root: Path,
    manifest_path: Path,
    *,
    app_id: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    """Add or update an app entry. Returns the entry dict."""
    eco = ecosystem_root.resolve()
    man = manifest_path.resolve()
    if not man.is_file():
        raise FileNotFoundError(f"manifest not found: {man}")
    try:
        rel = os.path.relpath(man, eco)
    except ValueError as exc:
        raise ValueError(
            "manifest must live under the ecosystem repo or a path relpath can compute; "
            "try moving the app or use a symlink under local-ecosystem."
        ) from exc
    rel_posix = rel.replace(os.sep, "/")
    data_m = yaml.safe_load(man.read_text(encoding="utf-8"))
    if not isinstance(data_m, dict):
        raise ValueError("invalid manifest YAML")
    name = data_m.get("name")
    slug = (app_id or "").strip() if app_id else _slug(str(name or man.parent.name))
    if not slug:
        slug = _slug(man.parent.name)
    display = (label or "").strip() if label else ""
    if not display and name:
        display = str(name).replace("-", " ").strip() or slug
    if not display:
        display = slug
    entry = {
        "id": slug,
        "label": display,
        "manifest": rel_posix,
    }
    reg = _registry_path(eco)
    doc = _load(reg)
    apps: list[dict[str, Any]] = doc["apps"]
    replaced = False
    for i, a in enumerate(apps):
        if isinstance(a, dict) and a.get("id") == slug:
            apps[i] = entry
            replaced = True
            break
    if not replaced:
        apps.append(entry)
    _save(reg, doc)
    return entry


def resolve_registered_manifest_path(ecosystem_root: Path, app_id: str) -> Path | None:
    """Absolute path to leco.app.yaml for a registry id, if the file exists."""
    eco = ecosystem_root.resolve()
    reg = _registry_path(eco)
    doc = _load(reg)
    nid = app_id.strip()
    for a in doc.get("apps") or []:
        if not isinstance(a, dict):
            continue
        if str(a.get("id") or "").strip() != nid:
            continue
        mf = (a.get("manifest") or "").strip()
        if not mf:
            return None
        try:
            p = (eco / mf).resolve()
        except OSError:
            return None
        return p if p.is_file() else None
    return None


def unregister_from_ecosystem(ecosystem_root: Path, app_id: str) -> bool:
    """Remove app by id. Returns True if something was removed."""
    eco = ecosystem_root.resolve()
    reg = _registry_path(eco)
    doc = _load(reg)
    apps: list = doc["apps"]
    nid = app_id.strip()
    new_apps = [a for a in apps if not (isinstance(a, dict) and str(a.get("id")) == nid)]
    if len(new_apps) == len(apps):
        return False
    doc["apps"] = new_apps
    _save(reg, doc)
    return True
