"""Run docker compose with manifest context."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence

from leco_app.schema import ApplicationManifest


def _compose_cmd() -> list[str]:
    return ["docker", "compose"]


def path_for_docker_daemon(container_path: Path) -> Path:
    """
    Map a path inside the LEco DevOps (or similar) container to the path the Docker daemon expects.

    When ``docker compose`` runs in a container with only the socket mounted, bind-mount sources in
    the API must be paths on the **Docker host** (e.g. macOS paths Docker Desktop has in File
    Sharing). Paths like ``/workspace-parent/...`` exist in the container but are unknown to the
    daemon; remap using ``LECO_WORKSPACE_PARENT_HOST`` / ``LECO_PROJECT_ROOT_HOST`` when the same
    trees are also bind-mounted at those host paths (see ``ecosystem-stack/services/dashboard.sh``).
    """
    try:
        rp = container_path.resolve()
    except OSError:
        rp = container_path

    hw = (os.environ.get("LECO_WORKSPACE_PARENT_HOST") or "").strip()
    if hw:
        wsp_in = Path(os.environ.get("LECO_WORKSPACE_PARENT_CONTAINER", "/workspace-parent"))
        try:
            wsp_res = wsp_in.resolve()
        except OSError:
            wsp_res = wsp_in
        try:
            rel = rp.relative_to(wsp_res)
            return Path(hw).joinpath(rel).resolve()
        except ValueError:
            pass

    hp = (os.environ.get("LECO_PROJECT_ROOT_HOST") or "").strip()
    if hp:
        proj_in = Path(os.environ.get("LECO_PROJECT_CONTAINER", "/project"))
        try:
            proj_res = proj_in.resolve()
        except OSError:
            proj_res = proj_in
        try:
            rel = rp.relative_to(proj_res)
            return Path(hp).joinpath(rel).resolve()
        except ValueError:
            pass

    return rp


def compose_subprocess_cwd(manifest: ApplicationManifest, manifest_path: Path) -> Path:
    """Directory for docker compose subprocess cwd — always paths visible to this process (not host-remapped)."""
    if not manifest.docker_compose:
        return manifest.resolved_root(manifest_path).resolve()
    if (manifest.docker_compose.compose_file_from_manifest or "").strip():
        return manifest_path.parent.resolve()
    return manifest.resolved_root(manifest_path).resolve()


def _env_for_manifest(manifest: ApplicationManifest, manifest_path: Path) -> dict[str, str]:
    """Pass through host env; optional COMPOSE_PROJECT_NAME only if set in manifest."""
    env = os.environ.copy()
    if manifest.docker_compose and manifest.docker_compose.project_name:
        env["COMPOSE_PROJECT_NAME"] = manifest.docker_compose.project_name
    return env


def primary_compose_path(manifest: ApplicationManifest, manifest_path: Path) -> Path | None:
    """Filesystem path to the primary compose file (root composeFile or composeFileFromManifest)."""
    if not manifest.docker_compose:
        return None
    dc = manifest.docker_compose
    cfm = (dc.compose_file_from_manifest or "").strip()
    if cfm:
        er = Path(cfm)
        return er.resolve() if er.is_absolute() else (manifest_path.parent / er).resolve()
    root = manifest.resolved_root(manifest_path)
    rel = Path(dc.compose_file)
    return rel.resolve() if rel.is_absolute() else (root / rel).resolve()


def compose_args(manifest: ApplicationManifest, manifest_path: Path) -> list[str]:
    if not manifest.docker_compose:
        raise ValueError("Manifest has no dockerCompose section")
    root = manifest.resolved_root(manifest_path)
    root_d = path_for_docker_daemon(root)
    manifest_parent = manifest_path.parent.resolve()
    dc = manifest.docker_compose
    compose_paths: list[str] = []
    cfm = (dc.compose_file_from_manifest or "").strip()
    if cfm:
        er = Path(cfm)
        cfm_abs = er.resolve() if er.is_absolute() else (manifest_parent / er).resolve()
        compose_paths.append(str(path_for_docker_daemon(cfm_abs)))
    else:
        rel = Path(dc.compose_file)
        if rel.is_absolute():
            compose_paths.append(str(path_for_docker_daemon(rel.resolve())))
        else:
            compose_paths.append(str(path_for_docker_daemon((root_d / rel).resolve())))
    for extra in dc.additional_compose_files or []:
        er = Path(str(extra).strip())
        if not str(er):
            continue
        if er.is_absolute():
            compose_paths.append(str(path_for_docker_daemon(er.resolve())))
        else:
            compose_paths.append(str(path_for_docker_daemon((root_d / er).resolve())))
    for extra in dc.additional_compose_files_from_manifest or []:
        er = Path(str(extra).strip())
        if not str(er):
            continue
        cand = er.resolve() if er.is_absolute() else (manifest_parent / er).resolve()
        if not cand.is_file():
            continue
        compose_paths.append(str(path_for_docker_daemon(cand)))
    args = [*_compose_cmd()]
    for fp in compose_paths:
        args.extend(["-f", fp])
    if manifest.docker_compose.env_file:
        ef = Path(manifest.docker_compose.env_file)
        ef_path = (root / ef) if not ef.is_absolute() else ef
        if ef_path.is_file():
            args.extend(["--env-file", str(path_for_docker_daemon(ef_path.resolve()))])
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
    cmd = [*compose_args(manifest, manifest_path), *subcmd]
    scwd = compose_subprocess_cwd(manifest, manifest_path)
    return subprocess.call(cmd, cwd=cwd or scwd, env=_env_for_manifest(manifest, manifest_path))


def run_compose_capture(
    manifest: ApplicationManifest,
    manifest_path: Path,
    subcmd: Sequence[str],
) -> subprocess.CompletedProcess[str]:
    cmd = [*compose_args(manifest, manifest_path), *subcmd]
    scwd = compose_subprocess_cwd(manifest, manifest_path)
    return subprocess.run(
        cmd,
        cwd=scwd,
        env=_env_for_manifest(manifest, manifest_path),
        capture_output=True,
        text=True,
        check=False,
    )
