"""Run docker compose with manifest context."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence

from leco_app.schema import ApplicationManifest


def _compose_cmd() -> list[str]:
    return ["docker", "compose"]


def _env_for_manifest(manifest: ApplicationManifest, manifest_path: Path) -> dict[str, str]:
    """Pass through host env; optional COMPOSE_PROJECT_NAME only if set in manifest."""
    env = os.environ.copy()
    if manifest.docker_compose and manifest.docker_compose.project_name:
        env["COMPOSE_PROJECT_NAME"] = manifest.docker_compose.project_name
    return env


def compose_args(manifest: ApplicationManifest, manifest_path: Path) -> list[str]:
    if not manifest.docker_compose:
        raise ValueError("Manifest has no dockerCompose section")
    root = manifest.resolved_root(manifest_path)
    rel = Path(manifest.docker_compose.compose_file)
    file_arg = str(root / rel) if not rel.is_absolute() else str(rel)
    args = [*_compose_cmd(), "-f", file_arg]
    if manifest.docker_compose.env_file:
        ef = Path(manifest.docker_compose.env_file)
        ef_path = (root / ef) if not ef.is_absolute() else ef
        if ef_path.is_file():
            args.extend(["--env-file", str(ef_path)])
    if manifest.docker_compose.project_name:
        args.extend(["-p", manifest.docker_compose.project_name])
    for prof in manifest.docker_compose.profiles or []:
        args.extend(["--profile", prof])
    return args


def run_compose(
    manifest: ApplicationManifest,
    manifest_path: Path,
    subcmd: Sequence[str],
    *,
    cwd: Path | None = None,
) -> int:
    root = manifest.resolved_root(manifest_path)
    cmd = [*compose_args(manifest, manifest_path), *subcmd]
    return subprocess.call(cmd, cwd=cwd or root, env=_env_for_manifest(manifest, manifest_path))


def run_compose_capture(
    manifest: ApplicationManifest,
    manifest_path: Path,
    subcmd: Sequence[str],
) -> subprocess.CompletedProcess[str]:
    root = manifest.resolved_root(manifest_path)
    cmd = [*compose_args(manifest, manifest_path), *subcmd]
    return subprocess.run(
        cmd,
        cwd=root,
        env=_env_for_manifest(manifest, manifest_path),
        capture_output=True,
        text=True,
        check=False,
    )
