"""Per-service hub pages: credentials, connection strings, GUI links, live container status."""

from __future__ import annotations

from datetime import datetime, timezone

from monitor import (
    SERVICE_MAP,
    get_container,
    get_container_info,
    get_container_metrics,
    get_docker_client,
    get_log_metrics,
    normalize_lh_probe_urls,
)


def list_hub_slugs() -> list[dict]:
    """Slug + title for hub index."""
    out = []
    for item in SERVICE_MAP:
        slug = item.get("hub_slug")
        if not slug:
            continue
        out.append({"slug": slug, "service": item["service"], "container": item["container"]})
    return sorted(out, key=lambda x: x["service"].lower())


def get_hub_detail(slug: str) -> dict | None:
    item = next((i for i in SERVICE_MAP if i.get("hub_slug") == slug), None)
    if not item:
        return None

    client = get_docker_client()
    container = get_container(client, item["container"]) if client else None
    container_info = get_container_info(container)
    metrics = get_container_metrics(client, container) if client else {}
    logs = get_log_metrics(container, item["container"]) if container else {}

    urls = normalize_lh_probe_urls(list(item.get("urls") or []))
    if not urls and slug:
        urls = normalize_lh_probe_urls([f"http://localhost.lh/hub/{slug}"])

    return {
        "slug": slug,
        "service": item["service"],
        "container": item["container"],
        "notes": item.get("notes") or "",
        "credentials": item.get("credentials") or [],
        "connection_strings": item.get("connection_strings") or [],
        "insights": item.get("insights") or [],
        "database_guis": item.get("database_guis") or [],
        "management_links": item.get("management_links") or [],
        "public_urls": [
            {"label": "HTTPS" if u.startswith("https://") else "HTTP", "url": u} for u in urls
        ],
        "container_info": container_info,
        "metrics": metrics,
        "logs": logs,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
