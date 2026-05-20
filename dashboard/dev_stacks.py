"""Dev stack lifecycle: create, start, stop, destroy, snapshot."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from dev_stack_compose import (
    STACKS_ROOT,
    _slugify,
    generate_compose,
    list_stacks_from_platform,
    register_stack_in_platform,
    stack_dir_for,
)
from hosted_app_services import (
    _add_connection_endpoint,
    _build_connection_endpoints,
    _build_host_mongodb_uri,
    _extract_credentials,
    _host_port_from_publish,
)
from dev_stack_access import stack_access_info
from platform_config import base_domain, deployment_mode, load_component_catalog, load_platform_config, save_platform_config


def _ensure_lh_network() -> None:
    subprocess.run(
        ["docker", "network", "create", "lh-network"],
        capture_output=True,
        text=True,
    )


def _compose_services_on_lh_network(stack_id: str) -> set[str]:
    """Service keys in docker-compose.yml that attach to external lh-network."""
    import yaml

    path = stack_dir_for(stack_id) / "docker-compose.yml"
    if not path.is_file():
        return set()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return set()
    if not isinstance(raw, dict):
        return set()
    services = raw.get("services")
    if not isinstance(services, dict):
        return set()
    on_lh: set[str] = set()
    for name, spec in services.items():
        if not isinstance(spec, dict):
            continue
        nets = spec.get("networks")
        if isinstance(nets, list) and "lh-network" in nets:
            on_lh.add(str(name))
        elif isinstance(nets, dict) and "lh-network" in nets:
            on_lh.add(str(name))
    return on_lh


def repair_stack_lh_network(stack_id: str) -> list[str]:
    """Ensure lh-network exists and connect stack edge containers that lost it."""
    sid = _slugify(stack_id)
    logs: list[str] = []
    _ensure_lh_network()
    expected = _compose_services_on_lh_network(sid)
    if not expected:
        return logs
    project = _compose_project_name(sid)
    proc = subprocess.run(
        ["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={project}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    cids = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    if not cids:
        return logs
    for cid in cids:
        insp = subprocess.run(
            ["docker", "inspect", cid, "--format", "{{json .Name}}\t{{json .Config.Labels}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if insp.returncode != 0:
            continue
        line = (insp.stdout or "").strip()
        if not line:
            continue
        try:
            name_raw, labels_raw = line.split("\t", 1)
            name = json.loads(name_raw).lstrip("/")
            labels = json.loads(labels_raw)
        except (ValueError, json.JSONDecodeError):
            continue
        svc = str(labels.get("com.docker.compose.service") or "")
        if svc not in expected:
            continue
        net_proc = subprocess.run(
            ["docker", "inspect", cid, "--format", "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        attached = set((net_proc.stdout or "").split())
        if "lh-network" in attached:
            continue
        conn = subprocess.run(
            ["docker", "network", "connect", "lh-network", cid],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if conn.returncode == 0:
            logs.append(f"Connected {name} ({svc}) to lh-network")
        else:
            err = (conn.stderr or conn.stdout or "connect failed").strip()
            logs.append(f"Could not connect {name} ({svc}) to lh-network: {err[:200]}")
    return logs


def _compose_cmd(stack_id: str, *args: str) -> tuple[int, str]:
    d = stack_dir_for(stack_id)
    compose_file = d / "docker-compose.yml"
    if not compose_file.is_file():
        return 1, f"Missing compose file for stack {stack_id}"
    sid = stack_id.strip().lower()
    if args and args[0] in ("up", "start", "restart"):
        _ensure_lh_network()
    env = os.environ.copy()
    env.setdefault("COMPOSE_PROJECT_NAME", f"leco-devstack-{sid}")
    cmd = ["docker", "compose", "-f", str(compose_file), *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(d), env=env)
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out.strip()


def _docker_ps_state(stack_id: str) -> str:
    code, out = _compose_cmd(stack_id, "ps", "--format", "json")
    if code != 0:
        return "not_installed"
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if not lines:
        return "stopped"
    running = 0
    for ln in lines:
        try:
            row = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if str(row.get("State") or "").lower().startswith("running"):
            running += 1
    if running == 0:
        return "stopped"
    if running < len(lines):
        return "partial"
    return "running"


def _compose_project_name(stack_id: str) -> str:
    return f"leco-devstack-{_slugify(stack_id)}"


def _prune_devstack_project(project: str) -> str:
    """Remove leftover containers, volumes, and networks for a compose project."""
    lines: list[str] = []

    proc = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    cids = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    if cids:
        subprocess.run(
            ["docker", "rm", "-f", *cids],
            capture_output=True,
            text=True,
            timeout=120,
        )
        lines.append(f"Removed {len(cids)} container(s)")

    vol_ids: set[str] = set()
    proc = subprocess.run(
        ["docker", "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    vol_ids.update(ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip())
    proc = subprocess.run(
        ["docker", "volume", "ls", "-q"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    prefixes = (f"{project}_", f"{project}-")
    for ln in (proc.stdout or "").splitlines():
        name = ln.strip()
        if name and name.startswith(prefixes):
            vol_ids.add(name)
    if vol_ids:
        subprocess.run(
            ["docker", "volume", "rm", *sorted(vol_ids)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        lines.append(f"Removed {len(vol_ids)} volume(s)")

    proc = subprocess.run(
        ["docker", "network", "ls", "-q", "--filter", f"name={project}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    net_ids = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip() and ln.strip() != "lh-network"]
    if net_ids:
        for nid in net_ids:
            subprocess.run(
                ["docker", "network", "rm", nid],
                capture_output=True,
                text=True,
                timeout=60,
            )
        lines.append(f"Removed {len(net_ids)} network(s)")

    return "\n".join(lines)


def destroy_stack(stack_id: str) -> dict[str, Any]:
    """Stop compose project, delete volumes, remove stack files, config, and Traefik routes."""
    import shutil

    from dev_stack_app_urls import format_compose_log
    from dev_stack_routes import sync_dev_stack_routes

    sid = stack_id.strip().lower()
    slug = _slugify(sid)
    project = _compose_project_name(sid)
    stack_dir = stack_dir_for(sid)
    compose_file = stack_dir / "docker-compose.yml"
    logs: list[str] = []

    if compose_file.is_file():
        code, out = _compose_cmd(sid, "down", "-v", "--remove-orphans")
        logs.append("--- Docker Compose down ---")
        logs.append(format_compose_log(out) or "(no output)")
        if code != 0:
            return {
                "ok": False,
                "error": "docker compose down failed; stack files were not removed",
                "output": "\n".join(logs),
                "state": _docker_ps_state(sid),
            }
    else:
        logs.append("Compose file missing; pruning leftover Docker resources.")

    prune = _prune_devstack_project(project)
    if prune:
        logs.append("--- Docker prune ---")
        logs.append(prune)

    if stack_dir.is_dir():
        shutil.rmtree(stack_dir)
        logs.append(f"Removed stack directory: {stack_dir}")

    cfg = load_platform_config()
    cfg["dev_stacks"] = [
        s
        for s in (cfg.get("dev_stacks") or [])
        if _slugify(str(s.get("id") or "")) != slug
    ]
    save_platform_config(cfg)
    logs.append("Removed stack from platform config.")

    sync_dev_stack_routes(sid)
    logs.append("Updated Traefik dev-stack routes.")

    return {
        "ok": True,
        "output": "\n".join(logs),
        "state": "destroyed",
        "project": project,
    }


def list_stacks() -> list[dict[str, Any]]:
    catalog = load_component_catalog()
    items: list[dict[str, Any]] = []
    for row in list_stacks_from_platform():
        sid = str(row.get("id") or "")
        if not sid:
            continue
        state = _docker_ps_state(sid)
        item: dict[str, Any] = {
                "id": sid,
                "name": row.get("name") or sid,
                "state": state,
                "components": row.get("components") or [],
                "catalog_labels": {
                    str(c.get("id")): (catalog.get("components") or {})
                    .get(str(c.get("id")), {})
                    .get("label", c.get("id"))
                    for c in (row.get("components") or [])
                },
                "compose_dir": str(stack_dir_for(sid)),
            }
        if row.get("template"):
            item["template"] = row.get("template")
        if row.get("sample_data") is not None:
            item["sample_data"] = bool(row.get("sample_data"))
        try:
            item["access"] = stack_access_info(sid)
        except Exception as exc:
            item["access"] = {
                "stack_id": sid,
                "hostname": None,
                "error": str(exc),
            }
        items.append(item)
    return items


def create_stack(
    stack_id: str,
    name: str,
    components: list[dict[str, str]] | None = None,
    *,
    preset: str | None = None,
    template: str | None = None,
    sample_data: bool = False,
) -> dict[str, Any]:
    if preset:
        from dev_stack_templates import create_from_preset

        _, meta = create_from_preset(
            preset,
            stack_id=stack_id or None,
            name=name or None,
            sample_data=sample_data,
        )
    elif template:
        from dev_stack_templates import generate_from_template

        _, meta = generate_from_template(stack_id, name, template, sample_data=sample_data)
    else:
        if not components:
            return {"ok": False, "error": "components[] required when no preset/template"}
        _, meta = generate_compose(stack_id, name, components)
    register_stack_in_platform(meta, state="stopped")
    from dev_stack_routes import sync_dev_stack_routes

    sync_dev_stack_routes(meta.get("id"))
    sid = str(meta.get("id") or stack_id)
    try:
        meta["access"] = stack_access_info(sid)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Stack files were written but access metadata failed: {exc}",
            "stack_id": sid,
        }
    return {"ok": True, "stack": meta}


def stack_action(stack_id: str, action: str) -> dict[str, Any]:
    sid = stack_id.strip().lower()
    if action == "start":
        from dev_stack_images import normalize_stack_compose_file, verify_stack_compose_file

        normalize_stack_compose_file(sid)
        image_errors = verify_stack_compose_file(sid, skip_registry=False)
        if image_errors:
            return {
                "ok": False,
                "error": "Container image preflight failed",
                "output": "\n".join(image_errors),
                "image_errors": image_errors,
                "state": _docker_ps_state(sid),
            }
        code, out = _compose_cmd(sid, "up", "-d")
    elif action == "stop":
        code, out = _compose_cmd(sid, "stop")
    elif action == "destroy":
        return destroy_stack(sid)
    elif action == "repair":
        from dev_stack_redeploy import repair_stack

        return _finish_stack_lifecycle_action(sid, repair_stack(sid), action)
    elif action in ("reinstall", "redeploy"):
        from dev_stack_redeploy import reinstall_stack, redeploy_stack

        fn = redeploy_stack if action == "redeploy" else reinstall_stack
        return _finish_stack_lifecycle_action(sid, fn(sid), action)
    else:
        return {"ok": False, "error": f"Unknown action: {action}"}
    if action in ("start", "stop") and code == 0:
        cfg = load_platform_config()
        for s in cfg.get("dev_stacks") or []:
            if str(s.get("id")) == sid:
                s["state"] = "running" if action == "start" else "stopped"
        save_platform_config(cfg)
    from dev_stack_routes import sync_dev_stack_routes

    sync_dev_stack_routes(sid)
    result: dict[str, Any] = {"ok": code == 0, "output": out, "state": _docker_ps_state(sid)}
    if code != 0 and out:
        result["error"] = out.splitlines()[-1] if out else "compose failed"
    if action == "start" and result.get("ok"):
        result = _append_post_start_sections(sid, result, compose_raw=out)
    elif out:
        from dev_stack_app_urls import format_compose_log

        result["output"] = format_compose_log(out)
    result["access"] = stack_access_info(sid)
    return result


def _append_post_start_sections(
    sid: str, result: dict[str, Any], *, compose_raw: str = ""
) -> dict[str, Any]:
    from dev_stack_app_urls import (
        format_compose_log,
        repair_stack_public_urls,
        wait_for_stack_app_ready,
    )

    sections: list[str] = []
    if result.get("output"):
        sections.append(str(result["output"]))
    compose_log = format_compose_log(compose_raw)
    if compose_log:
        sections.append("--- Docker Compose ---")
        sections.append(compose_log)
    ready_log = wait_for_stack_app_ready(sid)
    if ready_log:
        sections.append(ready_log)
    repair = repair_stack_public_urls(sid)
    if repair.get("output"):
        sections.append("--- Public URL repair ---")
        sections.append(str(repair["output"]))
    if sections:
        result["output"] = "\n".join(sections)
    if repair.get("ok") is False and not repair.get("skipped"):
        result["public_url_repair_warning"] = repair.get("error") or "URL repair failed"
    result["public_url_repair"] = repair
    return result


def _finish_stack_lifecycle_action(sid: str, result: dict[str, Any], action: str) -> dict[str, Any]:
    if result.get("ok"):
        cfg = load_platform_config()
        for s in cfg.get("dev_stacks") or []:
            if str(s.get("id")) == sid or _slugify(str(s.get("id") or "")) == sid:
                s["state"] = _docker_ps_state(sid)
        save_platform_config(cfg)
        from dev_stack_routes import sync_dev_stack_routes

        sync_dev_stack_routes(sid)
        result = _append_post_start_sections(sid, result)
    result["access"] = stack_access_info(sid)
    return result


def _parse_compose_services(stack_id: str) -> dict[str, dict[str, Any]]:
    import yaml

    path = stack_dir_for(stack_id) / "docker-compose.yml"
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}
    svcs = raw.get("services")
    return svcs if isinstance(svcs, dict) else {}


def stack_snapshot(stack_id: str) -> dict[str, Any]:
    sid = stack_id.strip().lower()
    services = _parse_compose_services(sid)
    items: list[dict[str, Any]] = []
    from hosted_app_services import classify_compose_service

    for sname, spec in services.items():
        if not isinstance(spec, dict):
            continue
        spec = dict(spec)
        spec["_compose_file"] = str(stack_dir_for(sid) / "docker-compose.yml")
        kind = classify_compose_service(sname, spec)
        if kind not in ("mysql", "postgres", "redis", "mongodb", "minio"):
            continue
        env = {k: str(v) for k, v in (spec.get("environment") or {}).items()}
        creds = _extract_credentials(kind, env, sname.split("-")[0])
        endpoints = _build_connection_endpoints(kind, sname, spec, creds, [], [])
        dom = base_domain()
        if deployment_mode() == "cloud" and dom != "lh":
            for ep in endpoints:
                if ep.get("scope") == "host" and "127.0.0.1" in str(ep.get("uri", "")):
                    pub = str(ep["uri"]).replace("127.0.0.1", f"{sname}.{dom}", 1)
                    _add_connection_endpoint(endpoints, "host_lh", pub, label=f"Public ({dom})")
        items.append(
            {
                "name": sname,
                "kind": kind,
                "credentials": creds,
                "connection_endpoints": endpoints,
            }
        )
    return {
        "stack_id": sid,
        "state": _docker_ps_state(sid),
        "data_stores": items,
        "local_dev_only": True,
    }
