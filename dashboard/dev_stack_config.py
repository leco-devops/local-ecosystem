"""Dev stack on-disk layout, file listing, and safe read/write for dashboard editing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from dev_stack_compose import STACKS_ROOT, _slugify
from dev_stack_routes import TRAEFIK_DEVSTACKS_FILE
from platform_config import _PROJECT_ROOT, load_platform_config

_MAX_FILE_BYTES = 512_000
_EDITABLE_SUFFIXES = frozenset(
    {".yml", ".yaml", ".vcl", ".conf", ".env", ".sh", ".json", ".md", ".txt", ".ini", ".xml"}
)

_RELATED_FILES: dict[str, tuple[str, Path, bool]] = {
    "traefik-routes": (
        "hosting/traefik/20-dev-stacks.yml",
        TRAEFIK_DEVSTACKS_FILE,
        False,
    ),
    "platform-config": (
        "config/leco-platform.yaml",
        _PROJECT_ROOT / "config" / "leco-platform.yaml",
        False,
    ),
}


def _stack_dir(stack_id: str) -> Path:
    return STACKS_ROOT / _slugify(stack_id)


def _resolve_stack_file(stack_id: str, rel_path: str) -> Path:
    sid = _slugify(stack_id)
    rel = (rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not rel or rel.startswith("..") or "/.." in rel:
        raise ValueError("invalid file path")
    stack_dir = _stack_dir(sid).resolve()
    target = (stack_dir / rel).resolve()
    if target != stack_dir and stack_dir not in target.parents:
        raise ValueError("path escapes stack directory")
    return target


def _is_editable_file(path: Path) -> bool:
    if not path.is_file():
        return False
    return path.suffix.lower() in _EDITABLE_SUFFIXES or path.name in (
        "docker-compose.yml",
        "stack.yaml",
    )


def stack_config_info(stack_id: str) -> dict[str, Any]:
    """Paths and editable files for a dev stack (for Platform UI)."""
    sid = _slugify(stack_id)
    stack_dir = _stack_dir(sid)
    if not stack_dir.is_dir():
        raise FileNotFoundError(f"Stack not found: {sid}")

    files: list[dict[str, Any]] = []
    for path in sorted(stack_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(stack_dir).as_posix()
        if not _is_editable_file(path):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        files.append(
            {
                "path": rel,
                "size": size,
                "editable": True,
            }
        )

    meta_path = stack_dir / "stack.yaml"
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            raw = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                meta = raw
        except (OSError, yaml.YAMLError):
            pass

    platform_row: dict[str, Any] | None = None
    for row in load_platform_config().get("dev_stacks") or []:
        if str(row.get("id") or "") == sid:
            platform_row = dict(row)
            break

    _descriptions = {
        "traefik-routes": (
            "Generated Traefik file provider routes for all dev stacks "
            "(regenerated on create, start, destroy)."
        ),
        "platform-config": "Platform install profile and dev_stacks registry (shared).",
    }
    related = [
        {
            "id": key,
            "path": display,
            "editable": editable,
            "description": _descriptions.get(key, ""),
        }
        for key, (display, _abs, editable) in _RELATED_FILES.items()
    ]

    return {
        "ok": True,
        "stack_id": sid,
        "stack_dir": f"platform/dev-stacks/{sid}",
        "compose_project": meta.get("project") or f"leco-devstack-{sid}",
        "template": meta.get("template"),
        "platform_registry_path": "config/leco-platform.yaml",
        "platform_row": platform_row,
        "related_files": related,
        "files": files,
        "notes": [
            "Dev stack compose and metadata live under platform/dev-stacks/<id>/ — not under hosting/app-available/ (that tree is for registered hosted apps).",
            "Traefik HTTP routes are written to hosting/traefik/20-dev-stacks.yml and point at the stack edge container on lh-network.",
            "After editing docker-compose.yml, use Stop then Start so image rewrites and route sync run.",
        ],
    }


def read_related_file(file_id: str) -> dict[str, Any]:
    entry = _RELATED_FILES.get(file_id)
    if not entry:
        raise ValueError(f"unknown related file: {file_id}")
    display, path, editable = entry
    if not path.is_file():
        return {
            "ok": True,
            "path": display,
            "content": "",
            "editable": editable,
            "missing": True,
        }
    content = path.read_text(encoding="utf-8")
    if len(content.encode("utf-8")) > _MAX_FILE_BYTES:
        content = content[:_MAX_FILE_BYTES] + "\n… (truncated)"
    return {
        "ok": True,
        "path": display,
        "content": content,
        "editable": editable,
        "missing": False,
    }


def read_stack_file(stack_id: str, rel_path: str) -> dict[str, Any]:
    target = _resolve_stack_file(stack_id, rel_path)
    if not target.is_file():
        raise FileNotFoundError(f"file not found: {rel_path}")
    if not _is_editable_file(target):
        raise ValueError("file type is not editable")
    content = target.read_text(encoding="utf-8")
    truncated = False
    if len(content.encode("utf-8")) > _MAX_FILE_BYTES:
        content = content[:_MAX_FILE_BYTES] + "\n… (truncated)"
        truncated = True
    return {
        "ok": True,
        "path": target.relative_to(_stack_dir(stack_id)).as_posix(),
        "content": content,
        "truncated": truncated,
    }


def write_stack_file(stack_id: str, rel_path: str, content: str) -> dict[str, Any]:
    target = _resolve_stack_file(stack_id, rel_path)
    if not _is_editable_file(target):
        raise ValueError("file type is not editable")
    if len((content or "").encode("utf-8")) > _MAX_FILE_BYTES:
        raise ValueError("file too large")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content or "", encoding="utf-8")

    logs: list[str] = []
    sid = _slugify(stack_id)
    if target.name == "docker-compose.yml":
        from dev_stack_images import normalize_stack_compose_file

        logs.extend(normalize_stack_compose_file(sid))
    if target.name in ("docker-compose.yml", "stack.yaml"):
        from dev_stack_routes import sync_dev_stack_routes

        sync_dev_stack_routes(sid)
        logs.append("Traefik dev-stack routes regenerated.")

    return {
        "ok": True,
        "path": target.relative_to(_stack_dir(sid)).as_posix(),
        "logs": logs,
    }
