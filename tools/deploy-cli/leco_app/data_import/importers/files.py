"""Copy file trees into a container path."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from leco_app.data_import.context import ImportContext
from leco_app.data_import.runners import docker_cp, docker_exec


def import_files(ctx: ImportContext, entry: dict[str, Any]) -> tuple[bool, str]:
    rel = str(entry.get("path") or "files").strip()
    src = ctx.data_dir / rel
    if not src.is_dir():
        return False, f"Files directory not found: {rel}"

    service = str(entry.get("service") or "server")
    container = ctx.container_for_service(service)
    dest = str(entry.get("container_path") or "/tmp/leco-import").rstrip("/")
    drop = bool(entry.get("drop_before_import", True)) and ctx.reimport

    if ctx.dry_run:
        ctx.log(f"[files] dry-run: would copy {rel} → {container}:{dest}")
        return True, ""

    if drop:
        ctx.log(f"[files] Clearing {dest} in {container}…")
        docker_exec(container, ["rm", "-rf", dest], timeout=120)
        docker_exec(container, ["mkdir", "-p", dest], timeout=60)

    ctx.log(f"[files] Copying {rel} → {container}:{dest}…")
    code, out = docker_cp(src, container, dest)
    return code == 0, out
