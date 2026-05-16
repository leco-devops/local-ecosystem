"""MySQL import via docker compose exec."""

from __future__ import annotations

import gzip
from pathlib import Path
from typing import Any

from leco_app.data_import.context import ImportContext
from leco_app.data_import.runners import docker_compose_exec, docker_exec


def import_mysql(ctx: ImportContext, entry: dict[str, Any]) -> tuple[bool, str]:
    rel = str(entry.get("path") or "").strip()
    src = ctx.data_dir / rel
    if not src.is_file():
        return False, f"SQL file not found: {rel}"

    service = str(entry.get("service") or "db")
    database = str(entry.get("database") or "app").strip()
    container = ctx.container_for_service(service)
    drop = bool(entry.get("drop_before_import", True)) and ctx.reimport

    if ctx.dry_run:
        ctx.log(f"[mysql] dry-run: would import {rel} into {database}")
        return True, ""

    if drop:
        ctx.log(f"[mysql] Dropping database {database}…")
        code, out = docker_exec(
            container,
            ["mysql", "-e", f"DROP DATABASE IF EXISTS `{database}`; CREATE DATABASE `{database}`;"],
            timeout=120,
        )
        if code != 0:
            ctx.log(f"[mysql] drop warning: {out[:500]}")

    ctx.log(f"[mysql] Importing {rel}…")
    raw = _read_sql(src)
    if ctx.compose_tail:
        code, out = docker_compose_exec(
            ctx.compose_tail,
            service,
            ["mysql", database],
            cwd=ctx.compose_root,
            input_data=raw,
        )
    else:
        code, out = docker_exec(container, ["mysql", database], input_data=raw)
    return code == 0, out


def _read_sql(path: Path) -> bytes:
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as f:
            return f.read()
    return path.read_bytes()
