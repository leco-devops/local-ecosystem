"""Extract / merge ``urls`` in localhost profile YAML for the registration wizard."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import ValidationError

from leco_app.schema import LocalhostProfile

_YAML_DUMP_KW: dict[str, Any] = {
    "default_flow_style": False,
    "sort_keys": False,
    "allow_unicode": True,
}


def extract_localhost_urls(localhost_yaml: str) -> list[dict[str, str]]:
    """Return ``urls`` entries suitable for the dashboard table (``public_url`` in JSON)."""
    blob = (localhost_yaml or "").strip()
    if not blob:
        return []
    try:
        data = yaml.safe_load(blob)
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []
    urls = data.get("urls")
    if not isinstance(urls, list):
        return []
    out: list[dict[str, str]] = []
    for item in urls:
        if not isinstance(item, dict):
            continue
        pu = str(item.get("publicUrl") or item.get("public_url") or "").strip()
        if not pu:
            continue
        role = str(item.get("role") or "other").strip() or "other"
        label = str(item.get("label") or "").strip()
        out.append({"role": role, "label": label, "public_url": pu})
    return out


def _pick_role_url(urls: list[dict[str, str]], role: str, prefer_https: bool) -> str:
    schemes = ("https", "http") if prefer_https else ("http", "https")
    for scheme in schemes:
        for item in urls:
            pu = str(item.get("publicUrl") or item.get("public_url") or "").strip()
            if pu.startswith(f"{scheme}://") and str(item.get("role") or "").strip() == role:
                return pu
    return ""


def _norm_api_path_prefix(path: str) -> str:
    p = (path or "").strip()
    if not p or p == "/":
        return "/api"
    if not p.startswith("/"):
        p = "/" + p
    return p.rstrip("/") or "/"


def _sync_routing_entries_from_urls(data: dict[str, Any], url_rows: list[dict[str, str]]) -> None:
    """Keep ``infrastructure.routing.entries`` consistent with merged ``urls`` when we can infer shape."""
    fe_u = _pick_role_url(url_rows, "frontend", True)
    api_u = _pick_role_url(url_rows, "api", True)
    if not fe_u or not api_u:
        return
    try:
        p_fe = urlparse(fe_u)
        p_api = urlparse(api_u)
    except Exception:
        return
    if not p_fe.netloc or not p_api.netloc:
        return
    infra = data.get("infrastructure")
    if not isinstance(infra, dict):
        return
    routing = infra.get("routing")
    if not isinstance(routing, dict):
        return
    entries = routing.get("entries")
    if not isinstance(entries, list) or not entries:
        return

    fe_host = p_fe.netloc.lower()
    api_host = p_api.netloc.lower()

    # Same host + path → split entry (e.g. https://cv.lh and https://cv.lh/api)
    if fe_host == api_host:
        prefix = _norm_api_path_prefix(p_api.path or "")
        if len(entries) == 1 and isinstance(entries[0], dict):
            e0 = entries[0]
            if isinstance(e0.get("frontend"), dict) and isinstance(e0.get("apiBackend"), dict):
                e0["hostname"] = p_fe.netloc
                e0["apiPathPrefix"] = prefix
                return
        fe_entry: dict[str, Any] | None = None
        api_entry: dict[str, Any] | None = None
        for e in entries:
            if not isinstance(e, dict):
                continue
            hn = str(e.get("hostname") or "").strip().lower()
            bh = str(e.get("backendHost") or "").strip()
            if not bh:
                continue
            if hn == fe_host:
                fe_entry = e
            elif hn == f"api.{fe_host}":
                api_entry = e
        if fe_entry and api_entry:
            routing["entries"] = [
                {
                    "hostname": p_fe.netloc,
                    "apiPathPrefix": prefix,
                    "frontend": {
                        "host": fe_entry["backendHost"],
                        "port": int(fe_entry.get("backendPort") or 80),
                    },
                    "apiBackend": {
                        "host": api_entry["backendHost"],
                        "port": int(api_entry.get("backendPort") or 80),
                    },
                }
            ]
        return

    # Subdomain API (api.slug.lh): split → two legacy host entries
    if api_host == f"api.{fe_host}":
        for e in entries:
            if not isinstance(e, dict):
                continue
            fe = e.get("frontend")
            ab = e.get("apiBackend")
            if not isinstance(fe, dict) or not isinstance(ab, dict):
                continue
            fh = str(fe.get("host") or "").strip()
            ah = str(ab.get("host") or "").strip()
            if not fh or not ah:
                return
            routing["entries"] = [
                {
                    "hostname": p_fe.netloc,
                    "backendHost": fh,
                    "backendPort": int(fe.get("port") or 80),
                },
                {
                    "hostname": p_api.netloc,
                    "backendHost": ah,
                    "backendPort": int(ab.get("port") or 80),
                },
            ]
            return


def merge_localhost_urls(localhost_yaml: str, url_rows: list[dict[str, Any]] | None) -> str:
    """Replace ``urls`` in profile YAML; validate with :class:`LocalhostProfile`."""
    blob = (localhost_yaml or "").strip()
    if not blob:
        raise ValueError("localhost_yaml is required")
    rows = url_rows if isinstance(url_rows, list) else []
    normalized: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "other").strip() or "other"
        label = str(row.get("label") or "").strip()
        public_url = str(row.get("public_url") or row.get("publicUrl") or "").strip()
        if not public_url:
            continue
        normalized.append({"role": role, "label": label, "publicUrl": public_url})
    if not normalized:
        raise ValueError("At least one URL row with a non-empty public URL is required.")
    try:
        data = yaml.safe_load(blob)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML parse error: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("localhost profile must be a YAML mapping")
    data["urls"] = normalized
    _sync_routing_entries_from_urls(data, normalized)
    try:
        LocalhostProfile.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"localhost schema: {exc}") from exc
    return yaml.safe_dump(data, **_YAML_DUMP_KW)
