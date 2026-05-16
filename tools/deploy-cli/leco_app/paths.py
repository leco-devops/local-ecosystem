"""State/config paths and hosted-app path resolution (host vs container)."""

from __future__ import annotations

import os
from pathlib import Path

_WSP_CONTAINER_DEFAULT = "/workspace-parent"


def state_root() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / "leco"
    return Path.home() / ".local" / "share" / "leco"


def app_state_dir(slug: str) -> Path:
    d = state_root() / "apps" / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_manifest_name() -> str:
    return "leco.app.yaml"


def default_localhost_profile_name() -> str:
    return "leco.yaml"


def find_ecosystem_root_from_manifest(manifest_path: Path) -> Path | None:
    """Walk parents from ``leco.app.yaml`` until ``config/leco-registry.yaml`` is found."""
    p = manifest_path.resolve()
    for cand in (p.parent, *p.parents):
        if (cand / "config" / "leco-registry.yaml").is_file():
            return cand
    return None


def remap_workspace_parent_path(
    target: Path,
    *,
    ecosystem_root: Path | None = None,
) -> Path | None:
    """
    Map ``/workspace-parent/UtilityServer/foo`` → host sibling checkout when running
    ``leco-devops`` on the workstation (outside ``service-dashboard``).
    """
    ts = os.path.normpath(str(target))
    wsp_c = (os.environ.get("LECO_WORKSPACE_PARENT_CONTAINER") or _WSP_CONTAINER_DEFAULT).rstrip("/")
    if not (ts == wsp_c or ts.startswith(wsp_c + os.sep)):
        return None
    rel = ts[len(wsp_c) :].lstrip(os.sep)
    host_wsp = (os.environ.get("LECO_WORKSPACE_PARENT_HOST") or "").strip()
    if not host_wsp and ecosystem_root is not None:
        host_wsp = str(ecosystem_root.parent)
    if not host_wsp or not rel:
        return None
    cand = Path(host_wsp) / rel
    try:
        return cand.resolve() if cand.exists() else None
    except OSError:
        return cand if cand.exists() else None


def _resolve_symlink_target(link: Path, *, ecosystem_root: Path | None) -> Path | None:
    if not link.is_symlink():
        return link.resolve() if link.exists() else None
    raw = os.readlink(link)
    target = Path(raw)
    if not target.is_absolute():
        target = link.parent / target
    if target.exists():
        try:
            return target.resolve()
        except OSError:
            return target
    return remap_workspace_parent_path(target, ecosystem_root=ecosystem_root)


def resolve_app_root(manifest_path: Path, root_field: str = ".") -> Path:
    """
    Resolved upstream app tree for a materialized manifest (``root: source`` symlink).

    Inside Docker, ``source`` → ``/workspace-parent/...`` works. On the host, remap to
    ``$LECO_WORKSPACE_PARENT_HOST`` or the ecosystem repo's parent directory.
    """
    mp = manifest_path.resolve()
    eco = find_ecosystem_root_from_manifest(mp)
    r = Path((root_field or ".").strip() or ".")
    base = r if r.is_absolute() else (mp.parent / r)

    if base.is_symlink():
        resolved = _resolve_symlink_target(base, ecosystem_root=eco)
        if resolved is not None and resolved.is_dir():
            return resolved

    if base.is_dir():
        return base.resolve()

    if base.exists():
        return base.resolve()

    if base.is_symlink():
        resolved = _resolve_symlink_target(base, ecosystem_root=eco)
        if resolved is not None:
            return resolved

    return base.resolve(strict=False)


def resolve_reference_file(manifest_path: Path, root_field: str, rel: str) -> Path | None:
    """Find a manifest-relative file (wrangler, compose) on host or in-container layouts."""
    rel_s = (rel or "").strip().lstrip("/")
    if not rel_s:
        return None
    mp = manifest_path.resolve()
    eco = find_ecosystem_root_from_manifest(mp)
    app_root = resolve_app_root(mp, root_field)
    for cand in (app_root / rel_s, mp.parent / rel_s):
        if cand.is_file():
            return cand.resolve()
        if cand.is_symlink():
            resolved = _resolve_symlink_target(cand, ecosystem_root=eco)
            if resolved is not None and resolved.is_file():
                return resolved
    return None
