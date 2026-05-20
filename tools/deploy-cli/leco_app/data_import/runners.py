"""Subprocess helpers for import importers."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def run_cmd(
    args: list[str],
    *,
    cwd: Path | None = None,
    input_data: bytes | None = None,
    timeout: int = 3600,
) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            input=input_data,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        out = (proc.stdout or b"") + (proc.stderr or b"")
        return proc.returncode, out.decode("utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return 124, "Command timed out"
    except OSError as exc:
        return 1, str(exc)


def docker_exec(
    container: str,
    args: list[str],
    *,
    input_data: bytes | None = None,
    timeout: int = 3600,
) -> tuple[int, str]:
    return run_cmd(
        ["docker", "exec", "-i", container, *args],
        input_data=input_data,
        timeout=timeout,
    )


def docker_cp(src: Path, container: str, dest: str) -> tuple[int, str]:
    return run_cmd(["docker", "cp", str(src), f"{container}:{dest}"], timeout=600)


def docker_compose_exec(
    compose_tail: list[str],
    service: str,
    args: list[str],
    *,
    cwd: Path,
    input_data: bytes | None = None,
    timeout: int = 3600,
) -> tuple[int, str]:
    return run_cmd(
        ["docker", "compose", *compose_tail, "exec", "-T", service, *args],
        cwd=cwd,
        input_data=input_data,
        timeout=timeout,
    )
