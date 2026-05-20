"""Discover wrangler TOML configs (including ``wrangler.api.toml`` monorepo layouts)."""

from __future__ import annotations

import os
import re
from pathlib import Path

WRANGLER_PATHS = (
    Path("wrangler.toml"),
    Path("cloudflare") / "wrangler.toml",
)

_WRANGLER_TOML_NAME_RE = re.compile(r"^wrangler(\.[a-zA-Z0-9_.-]+)?\.toml$", re.IGNORECASE)

_SCAN_PRUNE_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".next",
        ".nuxt",
        "dist",
        "build",
        "coverage",
        ".turbo",
        ".wrangler",
        "vendor",
    }
)


def is_wrangler_pages_config(name: str) -> bool:
    """Pages projects use ``wrangler pages dev``, not the Workers runtime adapter."""
    n = (name or "").lower()
    return n == "wrangler.pages.toml" or ".pages." in n


def runtime_id_from_wrangler_relpath(rel: Path) -> str:
    """``infra/wrangler.api.toml`` → ``api``; ``wrangler.toml`` → ``worker``."""
    name = rel.name.lower()
    if name == "wrangler.toml":
        return "worker"
    if name.startswith("wrangler.") and name.endswith(".toml"):
        core = name[len("wrangler.") : -len(".toml")]
        return core.replace("_", "-") or "worker"
    return "worker"


def _wrangler_config_sort_key(rel: Path) -> tuple:
    name = rel.name.lower()
    if name == "wrangler.api.toml":
        return (0, str(rel))
    if name == "wrangler.toml":
        return (1, str(rel))
    if rel.parts and rel.parts[0].lower() == "infra":
        return (2, str(rel))
    if is_wrangler_pages_config(name):
        return (9, str(rel))
    return (3, str(rel))


def list_wrangler_config_files(root: Path, *, max_depth: int = 6) -> list[Path]:
    """All Worker-oriented wrangler TOML paths under ``root`` (relative), best-first."""
    r = root.resolve()
    seen: set[str] = set()
    found: list[Path] = []

    for rel in WRANGLER_PATHS:
        if (r / rel).is_file():
            p = rel.as_posix()
            if p not in seen:
                seen.add(p)
                found.append(rel)

    for dirpath, dirs, files in os.walk(r):
        rp = Path(dirpath)
        try:
            depth = len(rp.relative_to(r).parts)
        except ValueError:
            continue
        dirs[:] = [d for d in dirs if d not in _SCAN_PRUNE_DIRS]
        if depth > max_depth:
            dirs[:] = []
            continue
        for fn in files:
            if not _WRANGLER_TOML_NAME_RE.match(fn):
                continue
            if is_wrangler_pages_config(fn):
                continue
            rel = (rp / fn).relative_to(r)
            p = rel.as_posix()
            if p not in seen:
                seen.add(p)
                found.append(rel)

    found.sort(key=_wrangler_config_sort_key)
    return found


def pick_primary_wrangler_config(root: Path) -> Path | None:
    configs = list_wrangler_config_files(root)
    return configs[0] if configs else None


_PAGES_OUTPUT_DIR_RE = re.compile(
    r'^pages_build_output_dir\s*=\s*["\']?([^"\']+)["\']?\s*$',
    re.MULTILINE,
)


def list_wrangler_pages_config_files(root: Path, *, max_depth: int = 6) -> list[Path]:
    """Wrangler TOML files that declare a Pages static output (``wrangler.pages.toml``, etc.)."""
    r = root.resolve()
    seen: set[str] = set()
    found: list[Path] = []

    for dirpath, dirs, files in os.walk(r):
        rp = Path(dirpath)
        try:
            depth = len(rp.relative_to(r).parts)
        except ValueError:
            continue
        dirs[:] = [d for d in dirs if d not in _SCAN_PRUNE_DIRS]
        if depth > max_depth:
            dirs[:] = []
            continue
        for fn in files:
            if not _WRANGLER_TOML_NAME_RE.match(fn):
                continue
            if not is_wrangler_pages_config(fn):
                continue
            rel = (rp / fn).relative_to(r)
            p = rel.as_posix()
            if p not in seen:
                seen.add(p)
                found.append(rel)

    def _sort_key(rel: Path) -> tuple:
        if rel.name.lower() == "wrangler.pages.toml":
            return (0, str(rel))
        if rel.parts and rel.parts[0].lower() == "infra":
            return (1, str(rel))
        return (2, str(rel))

    found.sort(key=_sort_key)
    return found


def pages_runtime_id_from_config(rel: Path) -> str:
    """``infra/wrangler.pages.toml`` → ``dashboard``; other ``*.pages.*`` → ``pages``."""
    name = rel.name.lower()
    if "pages" in name:
        return "dashboard"
    return "pages"


def read_pages_build_output_dir(config_path: Path) -> str | None:
    """Parse ``pages_build_output_dir`` from a wrangler Pages TOML (path relative to config dir)."""
    try:
        text = config_path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = _PAGES_OUTPUT_DIR_RE.search(text)
    if not m:
        return None
    return m.group(1).strip().strip('"').strip("'")


def resolve_pages_asset_dir(app_root: Path, config_rel: Path) -> Path | None:
    """Resolved absolute directory of built static assets for ``wrangler pages dev``."""
    cfg = (app_root / config_rel).resolve()
    if not cfg.is_file():
        return None
    out_rel = read_pages_build_output_dir(cfg)
    if not out_rel:
        return None
    out = Path(out_rel)
    if out.is_absolute():
        return out if out.is_dir() else None
    return (cfg.parent / out).resolve()
