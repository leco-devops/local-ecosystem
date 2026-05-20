"""MongoDB import via docker cp + mongorestore."""

from __future__ import annotations

import gzip
import shutil
import tempfile
from pathlib import Path
from typing import Any

from leco_app.data_import.context import ImportContext
from leco_app.data_import.runners import docker_cp, docker_exec


def _dir_has_bson(path: Path) -> bool:
    if not path.is_dir():
        return False
    return any(path.rglob("*.bson"))


def mongorestore_target_in_container(remote: str, src: Path, database: str) -> str:
    """
    Path inside the container for mongorestore after docker cp.

    ``mongodump --out=.../mongo --db=admin`` yields ``mongo/admin/*.bson`` (collections at the
    DB folder root). A nested ``mongo/admin/admin/*.bson`` layout is also supported.
    """
    if database:
        nested = src / database
        if nested.is_dir() and _dir_has_bson(nested):
            return f"{remote}/{database}"
    return remote


def import_mongodb(ctx: ImportContext, entry: dict[str, Any]) -> tuple[bool, str]:
    rel = str(entry.get("path") or "").strip()
    src = ctx.data_dir / rel
    if not src.exists():
        return False, f"Path not found: {rel}"

    service = str(entry.get("service") or "mongo")
    database = str(entry.get("database") or "").strip()
    container = ctx.container_for_service(service)
    drop = bool(entry.get("drop_before_import", True)) and ctx.reimport

    if ctx.dry_run:
        ctx.log(f"[mongodb] dry-run: would restore {rel} into {container}")
        return True, ""

    if src.is_file() and (src.suffix == ".archive" or src.name.endswith(".archive")):
        return _restore_archive(ctx, container, src, drop)

    if src.is_dir():
        return _restore_dir(ctx, container, src, database, drop)

    return False, f"Unsupported mongo path: {rel}"


def _restore_dir(
    ctx: ImportContext,
    container: str,
    src: Path,
    database: str,
    drop: bool,
) -> tuple[bool, str]:
    ctx.log(f"[mongodb] Copying {src.name} into {container}…")
    remote = f"/tmp/leco-seed-{src.name}"
    code, out = docker_cp(src, container, remote)
    if code != 0:
        return False, out

    restore_path = mongorestore_target_in_container(remote, src, database)
    args = ["mongorestore"]
    if drop:
        args.append("--drop")
    if database:
        args.extend(["--db", database, restore_path])
    else:
        args.append(remote)

    ctx.log(
        f"[mongodb] mongorestore {'--drop ' if drop else ''}{database or 'all DBs'} "
        f"({restore_path})…"
    )
    code, out = docker_exec(container, args, timeout=3600)
    docker_exec(container, ["rm", "-rf", remote], timeout=60)
    return code == 0, out


def _restore_archive(
    ctx: ImportContext,
    container: str,
    src: Path,
    drop: bool,
) -> tuple[bool, str]:
    remote = "/tmp/leco-seed.archive"
    ctx.log(f"[mongodb] Copying archive into {container}…")
    code, out = docker_cp(src, container, remote)
    if code != 0:
        return False, out

    args = ["mongorestore", f"--archive={remote}"]
    if drop:
        args.append("--drop")
    ctx.log("[mongodb] mongorestore --archive …")
    code, out = docker_exec(container, args, timeout=3600)
    docker_exec(container, ["rm", "-f", remote], timeout=60)
    return code == 0, out
