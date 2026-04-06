"""Normalize hosting Traefik YAML for Traefik v3 file provider validation."""

from __future__ import annotations

from typing import Any


def prune_empty_http_maps(data: dict[str, Any]) -> None:
    """
    Traefik v3 file provider rejects:
    - empty ``http.routers`` / ``http.services`` / … as standalone elements;
    - an empty ``http: {}`` block (“http cannot be a standalone element”).
    Drop empty maps and remove ``http`` when nothing remains.
    """
    http = data.get("http")
    if not isinstance(http, dict):
        return
    for key in ("routers", "services", "middlewares", "serversTransports"):
        block = http.get(key)
        if isinstance(block, dict) and len(block) == 0:
            http.pop(key, None)
    if len(http) == 0:
        data.pop("http", None)
