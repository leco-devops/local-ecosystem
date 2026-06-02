"""Resolve host filesystem paths when the dashboard runs inside Docker."""

from __future__ import annotations

import os

PROJECT_ROOT = os.getenv("DASHBOARD_PROJECT_ROOT", "/project")


def host_project_root() -> str:
    """Host path for docker compose bind mounts (not in-container /project)."""
    for key in ("LECO_PROJECT_ROOT_HOST", "DASHBOARD_PROJECT_ROOT_HOST", "DASHBOARD_DOCKER_BIND_ROOT"):
        val = (os.getenv(key) or "").strip()
        if val and os.path.isdir(val):
            return val
    return PROJECT_ROOT
