"""Redis import — FLUSHALL + RDB or redis-cli pipe."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from leco_app.data_import.context import ImportContext
from leco_app.data_import.runners import docker_cp, docker_exec


def import_redis(ctx: ImportContext, entry: dict[str, Any]) -> tuple[bool, str]:
    rel = str(entry.get("path") or "").strip()
    src = ctx.data_dir / rel
    if not src.is_file():
        return False, f"Redis file not found: {rel}"

    service = str(entry.get("service") or "redis")
    container = ctx.container_for_service(service)
    drop = bool(entry.get("drop_before_import", True)) and ctx.reimport

    if ctx.dry_run:
        ctx.log(f"[redis] dry-run: would import {rel}")
        return True, ""

    if drop:
        ctx.log("[redis] FLUSHALL…")
        code, out = docker_exec(container, ["redis-cli", "FLUSHALL"], timeout=60)
        if code != 0:
            return False, out

    if src.name == "commands.txt" or src.suffix == ".txt":
        ctx.log(f"[redis] Piping {rel}…")
        raw = src.read_bytes()
        code, out = docker_exec(container, ["redis-cli", "--pipe"], input_data=raw)
        return code == 0, out

    if src.suffix == ".rdb" or src.name.endswith(".rdb"):
        ctx.log(f"[redis] Copying RDB {rel} (requires redis restart to load)…")
        remote = "/data/dump.rdb"
        code, out = docker_cp(src, container, remote)
        if code != 0:
            return False, out
        ctx.log("[redis] Note: restart redis container to load .rdb if not using volume mount.")
        return True, out

    return False, f"Unsupported redis file: {rel}"
