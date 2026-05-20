"""Run LEco DevOps CLI (`leco-devops`) from the dashboard (pip install -e tools/deploy-cli)."""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

PROJECT_ROOT = os.getenv("DASHBOARD_PROJECT_ROOT", "/project")


def leco_app_argv0() -> list[str]:
    """Resolve argv0 for the LEco DevOps CLI subprocess.

    ``LECO_DEVOPS_CMD`` (preferred) or legacy ``LECO_APP_CMD`` may be a full
    command prefix, e.g. ``/opt/venv/bin/leco-devops`` or ``python -m leco_app``.
    """
    raw = os.getenv("LECO_DEVOPS_CMD", "").strip() or os.getenv("LECO_APP_CMD", "").strip()
    if raw:
        return shlex.split(raw)
    return ["leco-devops"]


def run_leco_app(
    args: list[str],
    *,
    cwd: str | Path | None = None,
    timeout: int | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """
    Run ``leco-devops`` with args (e.g. ["deploy", "--manifest", "/path/leco.app.yaml"]).
    Returns (exit_code, stdout, stderr).
    """
    argv = [*leco_app_argv0(), *args]
    base = os.environ.copy()
    base.setdefault("LECO_ECOSYSTEM_ROOT", PROJECT_ROOT)
    if extra_env:
        base.update(extra_env)
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd or PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=base,
        )
    except FileNotFoundError:
        return 127, "", "LEco DevOps CLI not found (install tools/deploy-cli in the dashboard image or set LECO_DEVOPS_CMD / LECO_APP_CMD)"
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s: {' '.join(argv)}"
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, proc.stdout or "", out


def iter_run_leco_app(
    args: list[str],
    *,
    cwd: str | Path | None = None,
    timeout: int | None = None,
    extra_env: dict[str, str] | None = None,
) -> Iterator[tuple[str, Any]]:
    """
    Stream ``leco-devops`` stdout+stderr line by line for live dashboard logs.

    Yields ("line", str) for each line (including newline when present), then ("end", exit_code:int).
    On missing CLI, yields one line and ("end", 127).
    """
    argv = [*leco_app_argv0(), *args]
    base = os.environ.copy()
    base.setdefault("LECO_ECOSYSTEM_ROOT", PROJECT_ROOT)
    if extra_env:
        base.update(extra_env)
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd or PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=base,
        )
    except FileNotFoundError:
        yield (
            "line",
            "LEco DevOps CLI not found (install tools/deploy-cli in the dashboard image or set LECO_DEVOPS_CMD / LECO_APP_CMD)\n",
        )
        yield ("end", 127)
        return

    assert proc.stdout is not None
    start = time.monotonic()
    deadline = start + float(timeout) if timeout is not None else None
    try:
        while True:
            if deadline is not None and time.monotonic() > deadline:
                proc.kill()
                try:
                    proc.wait(timeout=10)
                except (subprocess.TimeoutExpired, OSError):
                    pass
                yield ("line", f"\n[dashboard] leco-devops timed out after {timeout}s\n")
                yield ("end", 124)
                return

            line = proc.stdout.readline()
            if line:
                yield ("line", line)
                continue
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        code = int(proc.wait(timeout=30) or 0)
        yield ("end", code)
    except GeneratorExit:
        try:
            proc.kill()
        except OSError:
            pass
        raise


def run_ecosystem_register(
    manifest_abs: Path,
    *,
    app_id: str,
    label: str,
    timeout: int = 300,
    registry_manifest_relpath: str | None = None,
) -> tuple[int, str]:
    """leco-devops ecosystem-register -E PROJECT_ROOT --manifest ... --id ... --label ..."""
    eco = Path(PROJECT_ROOT).resolve()
    resolved = manifest_abs.resolve()
    args = [
        "ecosystem-register",
        "--ecosystem-root",
        str(eco),
        "--manifest",
        str(resolved),
        "--id",
        app_id,
        "--label",
        label,
        "--merge-traefik",
    ]
    if registry_manifest_relpath:
        args.extend(["--registry-manifest-relpath", registry_manifest_relpath])
    code, _stdout, combined = run_leco_app(args, cwd=str(resolved.parent), timeout=timeout)
    return code, combined


def iter_ecosystem_register(
    manifest_abs: Path,
    *,
    app_id: str,
    label: str,
    timeout: int = 300,
    registry_manifest_relpath: str | None = None,
) -> Iterator[tuple[str, Any]]:
    """Same as run_ecosystem_register but streams each output line."""
    eco = Path(PROJECT_ROOT).resolve()
    resolved = manifest_abs.resolve()
    args = [
        "ecosystem-register",
        "--ecosystem-root",
        str(eco),
        "--manifest",
        str(resolved),
        "--id",
        app_id,
        "--label",
        label,
        "--merge-traefik",
    ]
    if registry_manifest_relpath:
        args.extend(["--registry-manifest-relpath", registry_manifest_relpath])
    yield from iter_run_leco_app(args, cwd=str(resolved.parent), timeout=timeout)


def run_leco_deploy(manifest_abs: Path, *, timeout: int = 3600) -> tuple[int, str]:
    """leco-devops deploy --manifest … (docker compose up when manifest defines dockerCompose)."""
    resolved = manifest_abs.resolve()
    args = ["deploy", "--manifest", str(resolved)]
    code, _stdout, combined = run_leco_app(args, cwd=str(resolved.parent), timeout=timeout)
    return code, combined


def iter_leco_deploy(manifest_abs: Path, *, timeout: int = 3600) -> Iterator[tuple[str, Any]]:
    resolved = manifest_abs.resolve()
    args = ["deploy", "--manifest", str(resolved)]
    yield from iter_run_leco_app(args, cwd=str(resolved.parent), timeout=timeout)


def run_ecosystem_unregister(
    slug: str,
    *,
    strip_traefik: bool = True,
    clean_local_cf: bool = True,
    compose_down: bool = True,
    compose_volumes: bool = False,
    timeout: int = 300,
) -> tuple[int, str]:
    eco = Path(PROJECT_ROOT).resolve()
    tf = eco / "hosting" / "traefik" / "dynamic.yml"
    args = [
        "ecosystem-unregister",
        slug,
        "--ecosystem-root",
        str(eco),
        "--traefik-dynamic",
        str(tf),
    ]
    if not compose_down:
        args.append("--no-compose-down")
    if compose_volumes:
        args.append("--compose-volumes")
    if not strip_traefik:
        args.append("--no-strip-traefik")
    if not clean_local_cf:
        args.append("--no-clean-local-cf")
    code, _stdout, combined = run_leco_app(args, cwd=str(eco), timeout=timeout)
    return code, combined


def run_traefik_fragment(manifest_abs: Path, *, timeout: int = 60) -> tuple[int, str, str]:
    """stdout is YAML fragment; stderr may contain typer messages."""
    args = ["traefik-fragment", "--manifest", str(manifest_abs.resolve())]
    code, stdout, combined = run_leco_app(args, cwd=str(manifest_abs.parent), timeout=timeout)
    return code, stdout.strip(), combined
