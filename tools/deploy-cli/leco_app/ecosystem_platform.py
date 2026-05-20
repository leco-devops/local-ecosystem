"""Platform & dev-stack operations against a local-ecosystem checkout."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Iterator

from leco_app.onboarding import resolve_ecosystem_root

Echo = Callable[..., None]

_BOOTSTRAPPED_ROOT: Path | None = None


def require_ecosystem_root(explicit: Path | None) -> Path:
    er = resolve_ecosystem_root(explicit)
    if er is None:
        raise ValueError(
            "Set LECO_ECOSYSTEM_ROOT or pass --ecosystem-root / -E to your local-ecosystem repo."
        )
    bootstrap_dashboard(er)
    return er


def bootstrap_dashboard(ecosystem_root: Path) -> None:
    """Put dashboard modules on sys.path (idempotent per root)."""
    global _BOOTSTRAPPED_ROOT
    er = ecosystem_root.resolve()
    if _BOOTSTRAPPED_ROOT == er:
        return
    dash = er / "dashboard"
    if not dash.is_dir():
        raise ValueError(f"Dashboard not found: {dash}")
    os.environ.setdefault("DASHBOARD_PROJECT_ROOT", str(er))
    dash_str = str(dash)
    if dash_str not in sys.path:
        sys.path.insert(0, dash_str)
    _BOOTSTRAPPED_ROOT = er


def parse_component_specs(specs: list[str]) -> list[dict[str, str]]:
    """Parse ``postgres:16`` style CLI tokens into component dicts."""
    out: list[dict[str, str]] = []
    for raw in specs:
        part = raw.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"Component must be id:version (got {part!r})")
        cid, ver = part.split(":", 1)
        cid, ver = cid.strip(), ver.strip()
        if not cid or not ver:
            raise ValueError(f"Invalid component spec: {raw!r}")
        out.append({"id": cid, "version": ver})
    return out


def load_platform_config_dict(_er: Path) -> dict[str, Any]:
    from platform_config import load_platform_config

    return load_platform_config()


def platform_catalog_dict(_er: Path) -> dict[str, Any]:
    from platform_services import catalog

    return catalog()


def component_catalog_dict(_er: Path) -> dict[str, Any]:
    from platform_config import load_component_catalog

    return load_component_catalog()


def dev_stack_presets_dict(_er: Path) -> dict[str, Any]:
    from dev_stack_templates import preset_catalog_for_api

    return preset_catalog_for_api()


def list_dev_stacks(_er: Path) -> list[dict[str, Any]]:
    from dev_stacks import list_stacks

    return list_stacks()


def create_dev_stack(
    _er: Path,
    stack_id: str,
    name: str,
    *,
    preset: str | None = None,
    template: str | None = None,
    components: list[dict[str, str]] | None = None,
    sample_data: bool = False,
) -> dict[str, Any]:
    from dev_stacks import create_stack

    return create_stack(
        stack_id,
        name,
        components,
        preset=preset,
        template=template,
        sample_data=sample_data,
    )


def dev_stack_action(
    _er: Path,
    stack_id: str,
    action: str,
    *,
    stream: bool = False,
    echo: Echo | None = None,
) -> dict[str, Any]:
    if stream:
        return _dev_stack_action_stream(stack_id, action, echo=echo)
    from dev_stacks import stack_action

    return stack_action(stack_id, action)


def _dev_stack_action_stream(
    stack_id: str,
    action: str,
    *,
    echo: Echo | None = None,
) -> dict[str, Any]:
    from dev_stack_stream import stack_action_streaming

    out = echo or print
    result: dict[str, Any] = {"ok": False, "error": "no events"}
    for ev in stack_action_streaming(stack_id, action):
        if ev.get("type") == "log":
            text = str(ev.get("text") or "")
            if text:
                out(text, end="" if text.endswith("\n") else "\n")
        elif ev.get("type") == "done":
            result = ev.get("result") if isinstance(ev.get("result"), dict) else {}
    return result


def dev_stack_snapshot_dict(_er: Path, stack_id: str) -> dict[str, Any]:
    from dev_stacks import stack_snapshot

    return stack_snapshot(stack_id)


def dev_stack_access_dict(_er: Path, stack_id: str) -> dict[str, Any]:
    from dev_stack_access import stack_access_info

    return stack_access_info(stack_id)


def platform_service_action(_er: Path, service_id: str, action: str) -> dict[str, Any]:
    from platform_services import service_action

    return service_action(service_id, action)


def apply_platform_traefik(_er: Path) -> dict[str, Any]:
    from platform_services import apply_traefik

    return apply_traefik()


def bind_manifest_dev_stack(manifest_path: Path, dev_stack_id: str | None) -> dict[str, Any]:
    from dev_stack_binding import set_platform_dev_stack

    return set_platform_dev_stack(str(manifest_path.resolve()), dev_stack_id)


def read_manifest_dev_stack_binding(manifest_path: Path) -> dict[str, Any]:
    from dev_stack_binding import read_platform_binding

    return read_platform_binding(str(manifest_path.resolve()))


def tail_dev_stack_logs(stack_id: str, *, follow: bool = False, tail: int = 100) -> Iterator[str]:
    from dev_stacks import stack_dir_for

    import subprocess

    d = stack_dir_for(stack_id)
    compose_file = d / "docker-compose.yml"
    if not compose_file.is_file():
        yield f"Missing compose file: {compose_file}\n"
        return
    sid = stack_id.strip().lower()
    env = os.environ.copy()
    env.setdefault("COMPOSE_PROJECT_NAME", f"leco-devstack-{sid}")
    cmd = ["docker", "compose", "-f", str(compose_file), "logs", "--tail", str(tail)]
    if follow:
        cmd.append("-f")
    proc = subprocess.Popen(
        cmd,
        cwd=str(d),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            yield line
    finally:
        if follow:
            proc.terminate()
        else:
            proc.wait()


def emit_json(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))
