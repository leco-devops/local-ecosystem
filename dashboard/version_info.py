"""Platform version metadata for API and dashboard boot."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(
    os.getenv("DASHBOARD_PROJECT_ROOT") or str(Path(__file__).resolve().parent.parent)
)


def _read_version_line() -> str:
    path = PROJECT_ROOT / "VERSION"
    try:
        line = path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        return line or "0.0.0"
    except (OSError, IndexError):
        return "0.0.0"


def _read_version_json() -> dict[str, Any]:
    path = PROJECT_ROOT / "version.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def load_version_payload() -> dict[str, Any]:
    """Merged version info for GET /api/version and client boot."""
    manifest = _read_version_json()
    version = str(manifest.get("version") or _read_version_line())
    return {
        "ok": True,
        "project": manifest.get("project") or "LEco DevOps Open Project",
        "application": manifest.get("application") or "LEco DevOps",
        "version": version,
        "released": manifest.get("released"),
        "license": manifest.get("license") or "MIT",
        "components": manifest.get("components") or {},
        "documentation": {
            "changelog": "CHANGELOG.md",
            "release_notes": "docs/RELEASE_NOTES.md",
            "versioning_policy": "docs/VERSIONING.md",
            "releases_dir": "releases/",
            "docs_tab_ids": {
                "changelog": "project-changelog",
                "release_notes": "project-release-notes",
                "versioning": "project-versioning",
            },
        },
    }
