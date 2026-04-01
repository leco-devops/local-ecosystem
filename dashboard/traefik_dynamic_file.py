"""Read / write Traefik file-provider dynamic.yml under PROJECT_ROOT."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = os.getenv("DASHBOARD_PROJECT_ROOT", "/project")
TRAEFIK_DYNAMIC = os.path.join(PROJECT_ROOT, "traefik", "dynamic.yml")


def traefik_dynamic_path() -> str:
    return TRAEFIK_DYNAMIC


def _atomic_write_dynamic_yaml(p: Path, data: dict[str, Any]) -> tuple[bool, str | None]:
    """
    Serialize data to dynamic.yml via a temp file in the same directory + os.replace,
    so Traefik's file watcher never reads a half-written file. Backs up the previous file to .bak.
    """
    text = yaml.safe_dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
    p.parent.mkdir(parents=True, exist_ok=True)
    bak = p.with_suffix(p.suffix + ".bak")
    if p.is_file():
        shutil.copy2(p, bak)
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".tmp.yml",
            prefix="dynamic.",
            dir=str(p.parent),
            delete=False,
        ) as fh:
            fh.write(text)
            tmp_path = fh.name
        os.replace(tmp_path, p)
        tmp_path = ""
        return True, None
    except OSError as exc:
        return False, str(exc)
    finally:
        if tmp_path and os.path.isfile(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def read_dynamic() -> dict[str, Any] | None:
    p = Path(TRAEFIK_DYNAMIC)
    if not p.is_file():
        return None
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def list_routers_services_summary() -> dict[str, Any]:
    """Structured summary for dashboard API."""
    data = read_dynamic()
    if not data:
        return {"ok": False, "error": "dynamic.yml missing or invalid", "path": TRAEFIK_DYNAMIC}
    http = data.get("http")
    if not isinstance(http, dict):
        return {"ok": False, "error": "no http: mapping", "path": TRAEFIK_DYNAMIC}
    routers = http.get("routers")
    services = http.get("services")
    if not isinstance(routers, dict):
        routers = {}
    if not isinstance(services, dict):
        services = {}

    r_out: list[dict[str, Any]] = []
    for key, rv in sorted(routers.items()):
        if not isinstance(rv, dict):
            continue
        r_out.append(
            {
                "key": key,
                "rule": rv.get("rule"),
                "service": rv.get("service"),
                "entryPoints": rv.get("entryPoints"),
                "priority": rv.get("priority"),
                "tls": bool(rv.get("tls")),
            }
        )

    s_out: list[dict[str, Any]] = []
    for key, sv in sorted(services.items()):
        if not isinstance(sv, dict):
            continue
        lb = sv.get("loadBalancer") or {}
        servers = lb.get("servers") if isinstance(lb, dict) else None
        urls: list[str] = []
        if isinstance(servers, list):
            for s in servers:
                if isinstance(s, dict) and s.get("url"):
                    urls.append(str(s["url"]))
        s_out.append({"key": key, "urls": urls})

    return {
        "ok": True,
        "path": TRAEFIK_DYNAMIC,
        "routers": r_out,
        "services": s_out,
    }


def strip_router_service_keys(router_keys: list[str], service_keys: list[str]) -> tuple[int, int, str | None]:
    """Remove keys; backup .bak; returns (n_routers, n_services, error)."""
    p = Path(TRAEFIK_DYNAMIC)
    if not p.is_file():
        return 0, 0, f"not found: {p}"
    try:
        raw = p.read_text(encoding="utf-8")
        data: Any = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError) as e:
        return 0, 0, str(e)
    if not isinstance(data, dict):
        return 0, 0, "invalid yaml root"
    http = data.get("http")
    if not isinstance(http, dict):
        return 0, 0, "no http"
    routers = http.get("routers")
    services = http.get("services")
    if not isinstance(routers, dict):
        routers = {}
    if not isinstance(services, dict):
        services = {}

    to_r = [k for k in router_keys if k in routers]
    to_s = [k for k in service_keys if k in services]
    if not to_r and not to_s:
        return 0, 0, None

    for k in to_r:
        del routers[k]
    for k in to_s:
        del services[k]
    http["routers"] = routers
    http["services"] = services
    data["http"] = http

    ok, err = _atomic_write_dynamic_yaml(p, data)
    if not ok:
        return len(to_r), len(to_s), err or "atomic write failed"
    return len(to_r), len(to_s), None


def merge_http_fragment(fragment_yaml: str) -> tuple[bool, str]:
    """Merge http.routers and http.services from fragment into dynamic.yml (by key)."""
    p = Path(TRAEFIK_DYNAMIC)
    if not p.is_file():
        return False, f"not found: {p}"
    try:
        frag = yaml.safe_load(fragment_yaml)
    except yaml.YAMLError as e:
        return False, f"fragment yaml: {e}"
    if not isinstance(frag, dict):
        return False, "fragment must be a mapping"
    f_http = frag.get("http")
    if not isinstance(f_http, dict):
        return False, "fragment must contain http: mapping"

    base = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(base, dict):
        return False, "dynamic.yml invalid"
    http = base.get("http")
    if not isinstance(http, dict):
        http = {}
    routers = http.get("routers")
    services = http.get("services")
    if not isinstance(routers, dict):
        routers = {}
    if not isinstance(services, dict):
        services = {}

    fr = f_http.get("routers")
    fs = f_http.get("services")
    if isinstance(fr, dict):
        routers.update(fr)
    if isinstance(fs, dict):
        services.update(fs)
    http["routers"] = routers
    http["services"] = services
    base["http"] = http

    ok, err = _atomic_write_dynamic_yaml(p, base)
    if not ok:
        return False, err or "atomic write failed"
    return True, "merged; backup dynamic.yml.bak (Traefik reloads via file watch; no container restart)"
