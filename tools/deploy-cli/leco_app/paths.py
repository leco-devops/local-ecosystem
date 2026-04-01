"""State and config paths under the user home directory."""

import os
from pathlib import Path


def state_root() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / "leco"
    return Path.home() / ".local" / "share" / "leco"


def app_state_dir(slug: str) -> Path:
    d = state_root() / "apps" / slug
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_manifest_name() -> str:
    return "leco.app.yaml"


def default_localhost_profile_name() -> str:
    return "leco.yaml"
