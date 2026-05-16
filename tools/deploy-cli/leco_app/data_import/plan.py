"""Build import plans from data/manifest.yaml and auto-discovery."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

KINDS = frozenset({"mongodb", "mysql", "postgres", "redis", "d1", "r2", "kv", "files"})


def data_dir_for_manifest(manifest_path: Path) -> Path:
    return manifest_path.resolve().parent / "data"


def _dir_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def _has_bson_tree(path: Path) -> bool:
    if not path.is_dir():
        return False
    for p in path.rglob("*.bson"):
        return True
    return False


def _load_manifest(data_dir: Path) -> list[dict[str, Any]]:
    mf = data_dir / "manifest.yaml"
    if not mf.is_file():
        return []
    try:
        raw = yaml.safe_load(mf.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    imports = raw.get("imports")
    if not isinstance(imports, list):
        return []
    out: list[dict[str, Any]] = []
    for row in imports:
        if isinstance(row, dict) and row.get("kind") in KINDS:
            out.append(dict(row))
    return out


def _infer_service_for_kind(kind: str, services: dict[str, dict[str, Any]]) -> str:
    patterns = {
        "mongodb": re.compile(r"mongo", re.I),
        "mysql": re.compile(r"mysql|mariadb|^db$", re.I),
        "postgres": re.compile(r"postgres", re.I),
        "redis": re.compile(r"redis|valkey", re.I),
    }
    pat = patterns.get(kind)
    if not pat:
        return ""
    for name in services:
        if pat.search(name):
            return name
    return ""


def _auto_discover(data_dir: Path, services: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not data_dir.is_dir():
        return items

    mongo_root = data_dir / "mongo"
    if mongo_root.is_dir():
        for child in sorted(mongo_root.iterdir()):
            if child.name.startswith("."):
                continue
            if child.is_dir() and _has_bson_tree(child):
                items.append(
                    {
                        "id": f"mongo-{child.name}",
                        "kind": "mongodb",
                        "service": _infer_service_for_kind("mongodb", services) or "mongo",
                        "database": child.name,
                        "path": str(child.relative_to(data_dir)),
                        "drop_before_import": True,
                        "source": "auto",
                    }
                )
            elif child.suffix == ".archive" or child.name.endswith(".archive"):
                items.append(
                    {
                        "id": f"mongo-archive-{child.name}",
                        "kind": "mongodb",
                        "service": _infer_service_for_kind("mongodb", services) or "mongo",
                        "database": "",
                        "path": str(child.relative_to(data_dir)),
                        "drop_before_import": True,
                        "source": "auto",
                    }
                )

    mysql_dir = data_dir / "mysql"
    if mysql_dir.is_dir():
        for f in sorted(mysql_dir.glob("*.sql*")):
            db = f.stem.replace(".sql", "")
            items.append(
                {
                    "id": f"mysql-{db}",
                    "kind": "mysql",
                    "service": _infer_service_for_kind("mysql", services) or "db",
                    "database": db,
                    "path": str(f.relative_to(data_dir)),
                    "drop_before_import": True,
                    "source": "auto",
                }
            )

    pg_dir = data_dir / "postgres"
    if pg_dir.is_dir():
        for f in sorted(pg_dir.glob("*.sql*")):
            db = f.stem.replace(".sql", "")
            items.append(
                {
                    "id": f"postgres-{db}",
                    "kind": "postgres",
                    "service": _infer_service_for_kind("postgres", services) or "postgres",
                    "database": db,
                    "path": str(f.relative_to(data_dir)),
                    "drop_before_import": True,
                    "source": "auto",
                }
            )

    redis_dir = data_dir / "redis"
    if redis_dir.is_dir():
        for f in redis_dir.iterdir():
            if f.suffix == ".rdb" or f.name == "dump.rdb":
                items.append(
                    {
                        "id": "redis-rdb",
                        "kind": "redis",
                        "service": _infer_service_for_kind("redis", services) or "redis",
                        "path": str(f.relative_to(data_dir)),
                        "drop_before_import": True,
                        "source": "auto",
                    }
                )
            elif f.name == "commands.txt":
                items.append(
                    {
                        "id": "redis-commands",
                        "kind": "redis",
                        "service": _infer_service_for_kind("redis", services) or "redis",
                        "path": str(f.relative_to(data_dir)),
                        "drop_before_import": True,
                        "source": "auto",
                    }
                )

    d1_dir = data_dir / "d1"
    if d1_dir.is_dir():
        for f in sorted(d1_dir.glob("*.sql*")):
            db = f.stem.replace(".sql", "")
            items.append(
                {
                    "id": f"d1-{db}",
                    "kind": "d1",
                    "database": db,
                    "path": str(f.relative_to(data_dir)),
                    "drop_before_import": True,
                    "source": "auto",
                }
            )

    r2_dir = data_dir / "r2"
    if r2_dir.is_dir():
        for bucket in sorted(r2_dir.iterdir()):
            if bucket.is_dir() and not bucket.name.startswith("."):
                items.append(
                    {
                        "id": f"r2-{bucket.name}",
                        "kind": "r2",
                        "bucket": bucket.name,
                        "path": str(bucket.relative_to(data_dir)),
                        "drop_before_import": True,
                        "source": "auto",
                    }
                )

    kv_dir = data_dir / "kv"
    if kv_dir.is_dir():
        for ns in sorted(kv_dir.iterdir()):
            if ns.is_dir() and not ns.name.startswith("."):
                items.append(
                    {
                        "id": f"kv-{ns.name}",
                        "kind": "kv",
                        "namespace": ns.name,
                        "path": str(ns.relative_to(data_dir)),
                        "drop_before_import": True,
                        "source": "auto",
                    }
                )
            elif ns.name == "keys.json":
                items.append(
                    {
                        "id": "kv-keys-json",
                        "kind": "kv",
                        "namespace": "",
                        "path": str(ns.relative_to(data_dir)),
                        "drop_before_import": True,
                        "source": "auto",
                    }
                )

    files_dir = data_dir / "files"
    if files_dir.is_dir() and any(files_dir.iterdir()):
        items.append(
            {
                "id": "files-tree",
                "kind": "files",
                "service": _infer_service_for_kind("mongodb", services) or "server",
                "path": "files",
                "container_path": "/data/import",
                "drop_before_import": True,
                "source": "auto",
            }
        )

    return items


def build_import_plan(
    manifest_path: Path,
    *,
    services: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return discovery payload: present, path, items, warnings."""
    mp = manifest_path.resolve()
    data_dir = data_dir_for_manifest(mp)
    svc = services or {}
    warnings: list[str] = []
    items: list[dict[str, Any]] = []

    if not data_dir.is_dir():
        return {
            "present": False,
            "path": str(data_dir),
            "items": [],
            "warnings": [],
            "total_bytes": 0,
        }

    manifest_items = _load_manifest(data_dir)
    if manifest_items:
        items = manifest_items
        for row in items:
            row.setdefault("source", "manifest")
            rel = str(row.get("path") or "").strip()
            if rel:
                fp = data_dir / rel
                if not fp.exists():
                    warnings.append(f"Missing path for {row.get('kind')}: {rel}")
    else:
        items = _auto_discover(data_dir, svc)
        if not items and any(data_dir.iterdir()):
            warnings.append("data/ has files but no manifest.yaml and nothing auto-detected")

    total_bytes = 0
    enriched: list[dict[str, Any]] = []
    for row in items:
        rel = str(row.get("path") or "").strip()
        fp = data_dir / rel if rel else data_dir
        size = _dir_size(fp) if fp.exists() else 0
        total_bytes += size
        if size > 500 * 1024 * 1024:
            warnings.append(f"Large import ({size // (1024*1024)} MiB): {rel or row.get('kind')}")
        entry = dict(row)
        entry.setdefault("id", entry_id(entry))
        entry["size_bytes"] = size
        entry["label"] = _item_label(entry)
        enriched.append(entry)

    if total_bytes > 100 * 1024 * 1024:
        warnings.append(
            f"Total seed data ~{total_bytes // (1024 * 1024)} MiB — import may take several minutes"
        )

    return {
        "present": True,
        "path": str(data_dir),
        "items": enriched,
        "warnings": warnings,
        "total_bytes": total_bytes,
    }


def entry_id(entry: dict[str, Any]) -> str:
    """Stable id for UI selection and API filtering."""
    explicit = str(entry.get("id") or "").strip()
    if explicit:
        return explicit
    kind = str(entry.get("kind") or "item")
    path = str(entry.get("path") or "").strip()
    if path:
        return f"{kind}:{path}"
    db = str(entry.get("database") or entry.get("bucket") or entry.get("namespace") or "").strip()
    if db:
        return f"{kind}:{db}"
    return kind


def _item_label(entry: dict[str, Any]) -> str:
    kind = entry.get("kind") or "?"
    if kind == "mongodb":
        return f"mongodb · {entry.get('database') or entry.get('path', '')}"
    if kind in ("mysql", "postgres", "d1"):
        return f"{kind} · {entry.get('database') or entry.get('path', '')}"
    if kind == "redis":
        return f"redis · {entry.get('path', '')}"
    if kind == "r2":
        return f"r2 · {entry.get('bucket', '')}"
    if kind == "kv":
        return f"kv · {entry.get('namespace', '')}"
    if kind == "files":
        return f"files · {entry.get('path', '')}"
    return str(kind)
