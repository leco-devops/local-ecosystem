"""Dev stack repair (config/network/URL) and reinstall (full template reset + volumes)."""

from __future__ import annotations

from typing import Any

from dev_stack_compose import (
    _slugify,
    list_stacks_from_platform,
    register_stack_in_platform,
    stack_dir_for,
)
from dev_stack_routes import load_stack_meta


def _resolve_stack_source(stack_id: str) -> tuple[str, str, str | None, bool, list[dict[str, str]]]:
    sid = _slugify(stack_id)
    meta = load_stack_meta(sid)
    if not stack_dir_for(sid).is_dir() and not meta:
        raise FileNotFoundError(f"Stack not found: {sid}")
    row: dict[str, Any] = {}
    for r in list_stacks_from_platform():
        if _slugify(str(r.get("id") or "")) == sid:
            row = dict(r)
            break
    name = str(row.get("name") or meta.get("name") or sid)
    template = meta.get("template") or row.get("template")
    template_s = str(template).strip() if template else None
    if meta.get("sample_data") is not None:
        sample_data = bool(meta.get("sample_data"))
    else:
        sample_data = bool(row.get("sample_data"))
    components = meta.get("components") or row.get("components") or []
    comp_list = [c for c in components if isinstance(c, dict)]
    return sid, name, template_s, sample_data, comp_list


def apply_stack_config_updates(stack_id: str) -> list[str]:
    """Apply LEco config fixes in place (images, edge configs, Traefik, lh-network). Keeps manual edits."""
    sid = _slugify(stack_id)
    if not stack_dir_for(sid).is_dir():
        raise FileNotFoundError(f"Stack not found: {sid}")
    logs: list[str] = []

    from dev_stack_images import normalize_stack_compose_file

    norm_logs = normalize_stack_compose_file(sid)
    if norm_logs:
        logs.append("Updated docker-compose.yml with current image and edge configuration fixes.")
        logs.extend(norm_logs)
    else:
        logs.append("Compose file already matches current image and edge configuration defaults.")

    from dev_stack_routes import sync_dev_stack_routes

    sync_dev_stack_routes(sid)
    logs.append("Refreshed hosting/traefik/20-dev-stacks.yml.")

    from dev_stacks import repair_stack_lh_network

    net_logs = repair_stack_lh_network(sid)
    if net_logs:
        logs.append("--- lh-network ---")
        logs.extend(net_logs)
    else:
        logs.append("lh-network is present; edge containers are attached.")

    meta = load_stack_meta(sid)
    if meta:
        from dev_stacks import _docker_ps_state

        register_stack_in_platform(meta, state=_docker_ps_state(sid))

    return logs


def regenerate_stack_files(stack_id: str) -> tuple[dict[str, Any], list[str]]:
    """Rewrite platform/dev-stacks/<id>/ from template or component list (reverts manual edits)."""
    sid, name, template, sample_data, components = _resolve_stack_source(stack_id)
    logs: list[str] = []

    if template:
        from dev_stack_templates import generate_from_template

        _, meta = generate_from_template(sid, name, template, sample_data=sample_data)
        logs.append(f"Rewrote stack files from template “{template}” (configuration edits reverted).")
    elif components:
        from dev_stack_compose import generate_compose

        _, meta = generate_compose(sid, name, components)
        logs.append("Rewrote stack files from the component catalog (configuration edits reverted).")
    else:
        raise ValueError(
            "This stack has no template or component list to regenerate from. "
            "Use Destroy, then create the stack again from the builder."
        )

    from dev_stacks import _docker_ps_state

    register_stack_in_platform(meta, state=_docker_ps_state(sid))

    from dev_stack_images import normalize_stack_compose_file

    for line in normalize_stack_compose_file(sid):
        logs.append(line)

    from dev_stack_routes import sync_dev_stack_routes

    sync_dev_stack_routes(sid)
    logs.append("Regenerated hosting/traefik/20-dev-stacks.yml.")

    return meta, logs


def repair_stack(stack_id: str) -> dict[str, Any]:
    """Apply config updates, refresh routing/networking, compose up -d, and repair public URLs (keeps volumes)."""
    from dev_stack_app_urls import format_compose_log
    from dev_stacks import _compose_cmd, _docker_ps_state, _ensure_lh_network

    sid = _slugify(stack_id)
    logs: list[str] = []
    try:
        logs.extend(apply_stack_config_updates(sid))
    except Exception as exc:
        return {"ok": False, "error": str(exc), "state": _docker_ps_state(sid), "action": "repair"}

    _ensure_lh_network()
    code, out = _compose_cmd(sid, "up", "-d")
    logs.append("--- Docker Compose up ---")
    logs.append(format_compose_log(out) or "(no output)")

    result: dict[str, Any] = {
        "ok": code == 0,
        "output": "\n".join(logs),
        "state": _docker_ps_state(sid),
        "action": "repair",
    }
    if code != 0:
        result["error"] = out.splitlines()[-1] if out else "compose up failed"
    return result


def reinstall_stack(stack_id: str) -> dict[str, Any]:
    """Regenerate stack files from template, remove volumes, and deploy fresh (full reinstall)."""
    from dev_stack_app_urls import format_compose_log
    from dev_stacks import _compose_cmd, _compose_project_name, _docker_ps_state, _prune_devstack_project

    sid = _slugify(stack_id)
    project = _compose_project_name(sid)
    logs: list[str] = []

    try:
        _, reg_logs = regenerate_stack_files(sid)
        logs.extend(reg_logs)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "state": _docker_ps_state(sid), "action": "reinstall"}

    code, out = _compose_cmd(sid, "down", "-v", "--remove-orphans")
    logs.append("--- Docker Compose down (volumes removed) ---")
    logs.append(format_compose_log(out) or "(no output)")
    if code != 0:
        return {
            "ok": False,
            "error": "docker compose down failed",
            "output": "\n".join(logs),
            "state": _docker_ps_state(sid),
            "action": "reinstall",
        }

    prune = _prune_devstack_project(project)
    if prune:
        logs.append("--- Docker prune ---")
        logs.append(prune)

    code, out = _compose_cmd(sid, "up", "-d")
    logs.append("--- Docker Compose up ---")
    logs.append(format_compose_log(out) or "(no output)")

    result: dict[str, Any] = {
        "ok": code == 0,
        "output": "\n".join(logs),
        "state": _docker_ps_state(sid),
        "action": "reinstall",
    }
    if code != 0:
        result["error"] = out.splitlines()[-1] if out else "compose up failed"
    return result


def redeploy_stack(stack_id: str) -> dict[str, Any]:
    """Backward-compatible alias for reinstall_stack."""
    result = reinstall_stack(stack_id)
    result["action"] = "redeploy"
    return result
