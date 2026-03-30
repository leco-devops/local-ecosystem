"""Detect docker-compose files and published host ports."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

COMPOSE_NAMES = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)


@dataclass
class ComposeDetection:
    compose_files: list[Path] = field(default_factory=list)
    """Relative paths from scan root."""
    services: dict[str, dict[str, Any]] = field(default_factory=dict)
    host_ports: list[int] = field(default_factory=list)
    suggested_env_file: str | None = None


def _parse_ports(ports_val: Any) -> list[int]:
    out: list[int] = []
    if ports_val is None:
        return out
    items = ports_val if isinstance(ports_val, list) else [ports_val]
    for p in items:
        if isinstance(p, int):
            out.append(p)
            continue
        if not isinstance(p, str):
            continue
        # "8001:8001" or "127.0.0.1:8001:8001"
        part = p.strip().split(":")[-2] if p.count(":") >= 2 else p.split(":")[0]
        try:
            out.append(int(part))
        except ValueError:
            # named port or range — skip
            pass
    return out


def _scan_compose_file(root: Path, rel: Path) -> tuple[dict[str, dict[str, Any]], list[int]]:
    path = root / rel
    if not path.is_file():
        return {}, []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}, []
    services = data.get("services") or {}
    if not isinstance(services, dict):
        return {}, []
    host_ports: list[int] = []
    normalized: dict[str, dict[str, Any]] = {}
    for name, spec in services.items():
        if not isinstance(spec, dict):
            continue
        normalized[name] = {"build": spec.get("build"), "image": spec.get("image")}
        hp = _parse_ports(spec.get("ports"))
        host_ports.extend(hp)
    return normalized, host_ports


def detect_compose(root: Path) -> ComposeDetection:
    root = root.resolve()
    result = ComposeDetection()
    for name in COMPOSE_NAMES:
        rel = Path(name)
        if (root / rel).is_file():
            result.compose_files.append(rel)
    docker_dir = root / "docker"
    if docker_dir.is_dir():
        for name in COMPOSE_NAMES:
            rel = Path("docker") / name
            if (root / rel).is_file():
                result.compose_files.append(rel)

    # Prefer root docker-compose.yml
    primary: Path | None = None
    for rel in result.compose_files:
        if rel.name == "docker-compose.yml" and rel.parent == Path("."):
            primary = rel
            break
    if primary is None and result.compose_files:
        primary = result.compose_files[0]

    if primary:
        svc, ports = _scan_compose_file(root, primary)
        result.services = svc
        result.host_ports = sorted(set(ports))

    if (root / "docker" / ".env").is_file() or (root / "docker" / "env.example").is_file():
        result.suggested_env_file = "docker/.env"

    return result
