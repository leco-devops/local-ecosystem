"""Writable hosting/app-available layout for read-only register paths."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

HOSTING_SOURCE_LINK_NAME = "source"


def collect_config_ref_relative_paths(
    manifest_dict: dict[str, Any],
    profile_dict: dict[str, Any] | None,
) -> list[str]:
    """Paths (relative to app tree root) to mirror as symlinks under hosting staging."""
    out: list[str] = []
    seen: set[str] = set()

    def add(raw: Any) -> None:
        if not isinstance(raw, str):
            return
        s = raw.strip()
        if not s or s in seen:
            return
        seen.add(s)
        out.append(s)

    cr = manifest_dict.get("configRefs") or manifest_dict.get("config_refs")
    if isinstance(cr, dict):
        for v in cr.values():
            add(v)

    if isinstance(profile_dict, dict):
        infra = profile_dict.get("infrastructure")
        if isinstance(infra, dict):
            dc = infra.get("dockerCompose") or infra.get("docker_compose")
            if isinstance(dc, dict):
                add(dc.get("composeFile") or dc.get("compose_file"))
                add(dc.get("envFile") or dc.get("env_file"))
            cf = infra.get("cloudflare")
            if isinstance(cf, dict):
                add(cf.get("wranglerConfig") or cf.get("wrangler_config"))
    return out


def _safe_rel_path_under_root(rel: str) -> Path | None:
    """Return a relative Path with no ``..`` or absolute segments; else None."""
    p = Path(rel)
    if p.is_absolute():
        return None
    parts = p.parts
    if ".." in parts:
        return None
    return Path(*parts) if parts else None


def sync_hosting_config_ref_symlinks(
    staging: Path,
    app_tree: Path,
    manifest_dict: dict[str, Any],
    profile_dict: dict[str, Any] | None,
) -> dict[str, Any]:
    """Create symlinks under ``staging`` pointing at files/dirs under ``app_tree``.

    Skips missing targets, paths that escape ``app_tree``, and names that would
    overwrite ``leco.app.yaml``, the ``source`` link, or the localhost profile file.
    """
    prof = manifest_dict.get("localHostProfile") or manifest_dict.get("local_host_profile") or "leco.yaml"
    prof_name = prof.strip() if isinstance(prof, str) and prof.strip() else "leco.yaml"
    reserved_relpaths = {
        Path("leco.app.yaml").as_posix(),
        Path(HOSTING_SOURCE_LINK_NAME).as_posix(),
        Path(prof_name).as_posix(),
    }

    root = app_tree.resolve()
    created: list[str] = []
    skipped: list[str] = []

    for rel in collect_config_ref_relative_paths(manifest_dict, profile_dict):
        sub = _safe_rel_path_under_root(rel)
        if sub is None:
            skipped.append(rel)
            continue
        if sub.as_posix() in reserved_relpaths:
            skipped.append(rel)
            continue
        target_abs = (root / sub).resolve()
        try:
            target_abs.relative_to(root)
        except ValueError:
            skipped.append(rel)
            continue
        if not target_abs.exists():
            skipped.append(rel)
            continue

        link_path = (staging / sub).resolve()
        try:
            link_path.relative_to(staging.resolve())
        except ValueError:
            skipped.append(rel)
            continue

        is_dir = target_abs.is_dir()
        if link_path.is_symlink():
            try:
                if link_path.resolve() == target_abs:
                    continue
            except OSError:
                pass
            link_path.unlink(missing_ok=True)
        elif link_path.exists():
            skipped.append(rel)
            continue

        link_path.parent.mkdir(parents=True, exist_ok=True)
        link_path.symlink_to(target_abs, target_is_directory=is_dir)
        created.append(sub.as_posix())

    return {"created": created, "skipped": skipped}


def is_dir_writable(d: Path) -> bool:
    if not d.is_dir():
        return False
    try:
        probe = d / ".leco_write_probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def compute_source_target(orig_root: Path, manifest_dict: dict[str, Any]) -> Path:
    """Directory that manifest root used to resolve to (for docker compose / wrangler paths)."""
    r = manifest_dict.get("root", ".")
    if not isinstance(r, str):
        r = "."
    r = (r or ".").strip() or "."
    ore = orig_root.resolve()
    if r == "..":
        return ore.parent
    if r == ".":
        return ore
    p = Path(r)
    if p.is_absolute():
        return p.resolve()
    return (ore / p).resolve()


def patch_manifest_root_for_hosting(manifest_dict: dict[str, Any]) -> None:
    manifest_dict["root"] = HOSTING_SOURCE_LINK_NAME


def refresh_symlink(link_path: Path, target: Path, *, target_is_dir: bool) -> None:
    if link_path.is_symlink() or link_path.exists():
        link_path.unlink()
    link_path.symlink_to(target, target_is_directory=target_is_dir)


def _hosting_app_id_segment(app_id: str) -> str:
    """Reject invalid slug-like segments for hosting paths."""
    s = (app_id or "").strip()
    if not s or s in (".", "..") or "/" in s or "\\" in s:
        raise ValueError(f"Invalid app id for hosting layout: {app_id!r}")
    return s


def hosting_staging_dir(eco_root: Path, app_id: str) -> Path:
    sid = _hosting_app_id_segment(app_id)
    return eco_root / "hosting" / "app-available" / sid


def hosting_manifest_logical_path(eco_root: Path, app_id: str) -> Path:
    sid = _hosting_app_id_segment(app_id)
    return eco_root / "hosting" / "app-available" / sid / "leco.app.yaml"


def registry_manifest_relpath(app_id: str) -> str:
    return f"hosting/app-available/{app_id}/leco.app.yaml"


def manifest_rel_uses_hosting_layout(manifest_rel: str) -> bool:
    """True if registry manifest path is under hosting/ (materialized app)."""
    mr = (manifest_rel or "").strip().replace("\\", "/")
    return mr.startswith("hosting/app-available/")


def remove_hosting_for_slug(eco_root: Path, slug: str) -> dict[str, Any]:
    """
    Remove ``hosting/app-available/<slug>`` dir.
    Paths are constrained under ``hosting/``; invalid slugs are rejected.
    """
    out: dict[str, Any] = {
        "hosting_removed": False,
        "hosting_paths_removed": [],
        "hosting_cleanup_errors": None,
    }
    base = (eco_root / "hosting").resolve()
    if not base.is_dir():
        return out
    sid = slug.strip()
    if not sid or sid in (".", "..") or ".." in sid or "/" in sid or "\\" in sid:
        out["hosting_cleanup_errors"] = ["invalid slug"]
        return out
    available = (base / "app-available" / sid).resolve()
    try:
        available.relative_to(base)
    except ValueError:
        out["hosting_cleanup_errors"] = ["path outside hosting/"]
        return out

    removed: list[str] = []
    errs: list[str] = []

    def _rm(p: Path, label: str) -> None:
        if not p.exists() and not p.is_symlink():
            return
        try:
            if p.is_symlink():
                p.unlink()
            elif p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            removed.append(str(p))
        except OSError as exc:
            errs.append(f"{label}: {exc}")

    _rm(available, "app-available")
    out["hosting_paths_removed"] = removed
    if errs:
        out["hosting_cleanup_errors"] = errs
    out["hosting_removed"] = bool(removed) and not errs
    return out
