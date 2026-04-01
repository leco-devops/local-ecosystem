"""Generate / save registration YAML on disk (before ecosystem-register)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from hosting_layout import (
    HOSTING_SOURCE_LINK_NAME,
    compute_source_target,
    ensure_hosting_enabled_symlink,
    hosting_manifest_logical_path,
    hosting_staging_dir,
    is_dir_writable,
    patch_manifest_root_for_hosting,
    refresh_symlink,
    registry_manifest_relpath,
    sync_hosting_config_ref_symlinks,
)
from leco_detect import (
    build_default_manifest_and_localhost,
    compute_hosting_source_symlink_target,
    enrich_infrastructure_wrangler_binding_preview,
    ensure_docker_compose_in_manifest,
    ensure_docker_compose_in_profile_infrastructure,
    ensure_wrangler_in_manifest,
    ensure_wrangler_in_profile_infrastructure,
    resolve_registration_path,
    slugify_app_id,
)
from leco_subprocess import PROJECT_ROOT
from leco_app.schema import ApplicationManifest, LocalhostProfile

_YAML_DUMP_KW: dict[str, Any] = {
    "default_flow_style": False,
    "sort_keys": False,
    "allow_unicode": True,
}


def _profile_relpath(manifest_dict: dict[str, Any]) -> str:
    prof = manifest_dict.get("localHostProfile") or manifest_dict.get("local_host_profile") or "leco.yaml"
    if not isinstance(prof, str) or not prof.strip():
        return "leco.yaml"
    return prof.strip()


def registration_yaml_status(path_rel: str, app_id: str | None = None) -> dict[str, Any]:
    """Whether leco.app.yaml + localhost profile exist (app root or hosting staging)."""
    orig_root = resolve_registration_path(path_rel)
    eco = Path(PROJECT_ROOT).resolve()
    writable = is_dir_writable(orig_root)
    raw_id = (app_id or "").strip()

    if writable:
        man_path = orig_root / "leco.app.yaml"
        manifest_exists = man_path.is_file()
        profile = "leco.yaml"
        if manifest_exists:
            try:
                data = yaml.safe_load(man_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    p = data.get("localHostProfile") or data.get("local_host_profile")
                    if isinstance(p, str) and p.strip():
                        profile = p.strip()
            except (OSError, yaml.YAMLError):
                pass
        loc_path = orig_root / profile
        return {
            "writable": True,
            "materialized": False,
            "manifest_exists": manifest_exists,
            "localhost_profile": profile,
            "localhost_exists": loc_path.is_file(),
            "registration_ready": manifest_exists and loc_path.is_file(),
            "manifest_path": str(man_path.resolve()),
            "localhost_path": str(loc_path.resolve()),
        }

    if not raw_id:
        return {
            "writable": False,
            "materialized": True,
            "manifest_exists": False,
            "localhost_profile": "leco.yaml",
            "localhost_exists": False,
            "registration_ready": False,
            "manifest_path": "",
            "localhost_path": "",
            "needs_app_id_for_staging": True,
        }

    aid = slugify_app_id(raw_id)
    staging = hosting_staging_dir(eco, aid)
    man_path = staging / "leco.app.yaml"
    manifest_exists = man_path.is_file()
    profile = "leco.yaml"
    if manifest_exists:
        try:
            data = yaml.safe_load(man_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                p = data.get("localHostProfile") or data.get("local_host_profile")
                if isinstance(p, str) and p.strip():
                    profile = p.strip()
        except (OSError, yaml.YAMLError):
            pass
    loc_path = (staging / profile).resolve()
    localhost_exists = loc_path.is_file()
    logical_man = hosting_manifest_logical_path(eco, aid)
    return {
        "writable": False,
        "materialized": True,
        "manifest_exists": manifest_exists,
        "localhost_profile": profile,
        "localhost_exists": localhost_exists,
        "registration_ready": manifest_exists and localhost_exists,
        "manifest_path": str(man_path.resolve()),
        "localhost_path": str(loc_path),
        "logical_manifest_path": str(logical_man.resolve()),
        "registry_manifest_relpath": registry_manifest_relpath(aid),
    }


def materialize_registration_yaml(path_rel: str, app_id: str) -> dict[str, Any]:
    """
    Scan app root (compose, wrangler, archetype) and write ``leco.app.yaml`` + localhost profile.
    Overwrites existing files. Writable roots write into the app directory; read-only roots write
    under ``hosting/app-available/<id>/``, refresh the ``source`` symlink, and add symlinks for
    paths in ``configRefs`` plus compose / env / wrangler from the profile (when targets exist).
    """
    orig_root = resolve_registration_path(path_rel)
    aid = slugify_app_id(app_id)
    if not aid:
        raise ValueError("app_id required")
    eco = Path(PROJECT_ROOT).resolve()

    m, lo = build_default_manifest_and_localhost(orig_root, aid)
    ensure_wrangler_in_manifest(m, orig_root)

    if is_dir_writable(orig_root):
        man_path = orig_root / "leco.app.yaml"
        prof = _profile_relpath(m)
        loc_path = orig_root / prof
        man_path.parent.mkdir(parents=True, exist_ok=True)
        man_path.write_text(yaml.safe_dump(m, **_YAML_DUMP_KW), encoding="utf-8")
        loc_path.write_text(yaml.safe_dump(lo, **_YAML_DUMP_KW), encoding="utf-8")
        return {
            "ok": True,
            "materialized": False,
            "manifest_path": str(man_path.resolve()),
            "localhost_path": str(loc_path.resolve()),
            "manifest_yaml": man_path.read_text(encoding="utf-8"),
            "localhost_yaml": loc_path.read_text(encoding="utf-8"),
        }

    staging = hosting_staging_dir(eco, aid)
    staging.mkdir(parents=True, exist_ok=True)
    man_path = staging / "leco.app.yaml"
    source_target = compute_hosting_source_symlink_target(orig_root, m)
    ensure_docker_compose_in_profile_infrastructure(lo, orig_root, m, app_tree_base=source_target)
    ensure_wrangler_in_profile_infrastructure(lo, orig_root, m, app_tree_base=source_target)
    enrich_infrastructure_wrangler_binding_preview(lo.get("infrastructure") or {}, source_target)
    patch_manifest_root_for_hosting(m)
    prof = _profile_relpath(m)
    loc_path = staging / prof
    loc_path.parent.mkdir(parents=True, exist_ok=True)
    man_path.write_text(yaml.safe_dump(m, **_YAML_DUMP_KW), encoding="utf-8")
    loc_path.write_text(yaml.safe_dump(lo, **_YAML_DUMP_KW), encoding="utf-8")
    src_link = staging / HOSTING_SOURCE_LINK_NAME
    refresh_symlink(src_link, source_target, target_is_dir=True)
    cfg_sync = sync_hosting_config_ref_symlinks(staging, source_target, m, lo)
    ensure_hosting_enabled_symlink(eco, aid)
    logical_man = hosting_manifest_logical_path(eco, aid)
    return {
        "ok": True,
        "materialized": True,
        "manifest_path": str(logical_man.resolve()),
        "localhost_path": str(loc_path.resolve()),
        "staging_dir": str(staging.resolve()),
        "manifest_yaml": man_path.read_text(encoding="utf-8"),
        "localhost_yaml": loc_path.read_text(encoding="utf-8"),
        "registry_manifest_relpath": registry_manifest_relpath(aid),
        "config_symlinks": cfg_sync.get("created") or [],
        "config_symlinks_skipped": cfg_sync.get("skipped") or [],
    }


def save_registration_yaml(
    path_rel: str,
    app_id: str,
    manifest_yaml: str,
    localhost_yaml: str,
) -> dict[str, Any]:
    """Validate editor YAML and write manifest + profile files."""
    orig_root = resolve_registration_path(path_rel)
    aid = slugify_app_id(app_id)
    if not aid:
        raise ValueError("app_id required")
    eco = Path(PROJECT_ROOT).resolve()

    m_blob = (manifest_yaml or "").strip()
    l_blob = (localhost_yaml or "").strip()
    if not m_blob:
        raise ValueError("manifest_yaml is required")
    if not l_blob:
        raise ValueError("localhost_yaml is required")

    try:
        parsed_m = yaml.safe_load(m_blob)
    except yaml.YAMLError as exc:
        raise ValueError(f"Manifest YAML parse error: {exc}") from exc
    if not isinstance(parsed_m, dict):
        raise ValueError("Manifest must be a YAML mapping")
    try:
        ApplicationManifest.model_validate(parsed_m)
    except ValidationError as exc:
        raise ValueError(f"Manifest schema: {exc}") from exc

    try:
        parsed_l = yaml.safe_load(l_blob)
    except yaml.YAMLError as exc:
        raise ValueError(f"Localhost YAML parse error: {exc}") from exc
    if not isinstance(parsed_l, dict):
        raise ValueError("Localhost profile must be a YAML mapping")
    try:
        LocalhostProfile.model_validate(parsed_l)
    except ValidationError as exc:
        raise ValueError(f"Localhost schema: {exc}") from exc

    prof = _profile_relpath(parsed_m)
    loc_dump = yaml.safe_dump(parsed_l, **_YAML_DUMP_KW)

    def _apply_infra_ensures(app_tree: Path) -> None:
        if isinstance(parsed_l.get("infrastructure"), dict):
            ensure_docker_compose_in_profile_infrastructure(
                parsed_l, orig_root, parsed_m, app_tree_base=app_tree
            )
            ensure_wrangler_in_profile_infrastructure(
                parsed_l, orig_root, parsed_m, app_tree_base=app_tree
            )
            enrich_infrastructure_wrangler_binding_preview(parsed_l["infrastructure"], app_tree)
        else:
            ensure_docker_compose_in_manifest(parsed_m, orig_root)
            ensure_wrangler_in_manifest(parsed_m, orig_root)

    if is_dir_writable(orig_root):
        app_tree = compute_source_target(orig_root, parsed_m).resolve()
        _apply_infra_ensures(app_tree)
        try:
            LocalhostProfile.model_validate(parsed_l)
        except ValidationError as exc:
            raise ValueError(f"Localhost schema: {exc}") from exc
        try:
            ApplicationManifest.model_validate(parsed_m)
        except ValidationError as exc:
            raise ValueError(f"Manifest schema: {exc}") from exc
        man_path = orig_root / "leco.app.yaml"
        loc_path = orig_root / prof
        man_path.parent.mkdir(parents=True, exist_ok=True)
        loc_path.parent.mkdir(parents=True, exist_ok=True)
        man_path.write_text(yaml.safe_dump(parsed_m, **_YAML_DUMP_KW), encoding="utf-8")
        loc_path.write_text(loc_dump, encoding="utf-8")
        return {
            "ok": True,
            "materialized": False,
            "manifest_path": str(man_path.resolve()),
            "localhost_path": str(loc_path.resolve()),
        }

    staging = hosting_staging_dir(eco, aid)
    staging.mkdir(parents=True, exist_ok=True)
    tree_root = compute_hosting_source_symlink_target(orig_root, parsed_m)
    refresh_symlink(staging / HOSTING_SOURCE_LINK_NAME, tree_root, target_is_dir=True)
    _apply_infra_ensures(tree_root)
    try:
        LocalhostProfile.model_validate(parsed_l)
    except ValidationError as exc:
        raise ValueError(f"Localhost schema: {exc}") from exc
    try:
        ApplicationManifest.model_validate(parsed_m)
    except ValidationError as exc:
        raise ValueError(f"Manifest schema: {exc}") from exc
    patch_manifest_root_for_hosting(parsed_m)
    prof2 = _profile_relpath(parsed_m)
    man_path = staging / "leco.app.yaml"
    loc_path = staging / prof2
    loc_path.parent.mkdir(parents=True, exist_ok=True)
    man_path.write_text(yaml.safe_dump(parsed_m, **_YAML_DUMP_KW), encoding="utf-8")
    loc_path.write_text(loc_dump, encoding="utf-8")
    cfg_sync = sync_hosting_config_ref_symlinks(staging, tree_root, parsed_m, parsed_l)
    ensure_hosting_enabled_symlink(eco, aid)
    logical_man = hosting_manifest_logical_path(eco, aid)
    return {
        "ok": True,
        "materialized": True,
        "manifest_path": str(logical_man.resolve()),
        "localhost_path": str(loc_path.resolve()),
        "staging_dir": str(staging.resolve()),
        "registry_manifest_relpath": registry_manifest_relpath(aid),
        "config_symlinks": cfg_sync.get("created") or [],
        "config_symlinks_skipped": cfg_sync.get("skipped") or [],
    }
