"""Docker compose command builder for file-transfer — always uses host bind paths."""

from __future__ import annotations

import os

from project_paths import host_project_root

COMPOSE_REL = "file-transfer/docker-compose.yml"
KEYS_COMPOSE_REL = "file-transfer/docker-compose.sftp-keys.yml"
ENV_REL = "file-transfer/.env"


def sftp_pub_keys_present(username: str | None = None) -> bool:
    keys_dir = os.path.join(host_project_root(), "file-transfer", "keys", "sftp")
    if not os.path.isdir(keys_dir):
        return False
    user = str(username or "leco").strip() or "leco"
    if os.path.isfile(os.path.join(keys_dir, f"{user}.pub")):
        return True
    for name in os.listdir(keys_dir):
        if name.endswith(".pub") and os.path.isfile(os.path.join(keys_dir, name)):
            return True
    return False


def compose_argv(*extra: str, include_keys: bool | None = None) -> tuple[list[str], str]:
    root = host_project_root()
    compose = os.path.join(root, COMPOSE_REL)
    compose_dir = os.path.dirname(compose)
    if not os.path.isfile(compose):
        raise FileNotFoundError(f"compose file missing: {compose}")
    cmd = ["docker", "compose", "-f", compose, "--project-directory", compose_dir]
    use_keys = sftp_pub_keys_present() if include_keys is None else include_keys
    if use_keys:
        keys_compose = os.path.join(root, KEYS_COMPOSE_REL)
        if os.path.isfile(keys_compose):
            cmd.extend(["-f", keys_compose])
    env_path = os.path.join(root, ENV_REL)
    if os.path.isfile(env_path):
        cmd.extend(["--env-file", env_path])
    cmd.extend(extra)
    return cmd, compose_dir
