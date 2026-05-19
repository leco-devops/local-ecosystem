"""
Shared Control target metadata (no Docker / heavy deps).
Imported by control.py and docs_catalog.py.
"""

COMPOSE_REL = "cloudflare-local/docker-compose.yml"
INFRA_COMPOSE_REL = "infra/docker-compose.yml"

# Cloudflare-local: compose service name == container_name in this repo
CF_TARGETS = [
    {"id": "cf-minio", "label": "MinIO", "compose_service": "minio", "container": "minio"},
    {"id": "cf-valkey", "label": "Valkey", "compose_service": "valkey", "container": "valkey"},
    {"id": "cf-r2-adapter", "label": "R2 adapter", "compose_service": "r2-adapter", "container": "r2-adapter"},
    {"id": "cf-kv-adapter", "label": "KV adapter", "compose_service": "kv-adapter", "container": "kv-adapter"},
    {"id": "cf-d1-adapter", "label": "D1 adapter", "compose_service": "d1-adapter", "container": "d1-adapter"},
    {"id": "cf-workers-runtime", "label": "Workers (Miniflare)", "compose_service": "workers-runtime", "container": "workers-runtime"},
    {"id": "cf-browser-rendering-local", "label": "Browser rendering (local)", "compose_service": "browser-rendering-local", "container": "browser-rendering-local"},
    {"id": "cf-autoscale-demo", "label": "Autoscale demo", "compose_service": "autoscale-demo", "container": "autoscale-demo"},
    {"id": "cf-autoscaler", "label": "Autoscaler", "compose_service": "autoscaler", "container": "autoscaler"},
]

# Ecosystem stack: script basename (without .sh) and container name
AI_TARGETS = [
    {"id": "ai-traefik", "label": "Traefik", "script": "traefik", "container": "traefik"},
    {"id": "ai-open-webui", "label": "Open WebUI", "script": "webui", "container": "open-webui"},
    {"id": "ai-ollama", "label": "Ollama", "script": "ollama", "container": "ollama"},
    {"id": "ai-airllm", "label": "AirLLM (large HF models)", "script": "airllm", "container": "airllm"},
    {"id": "ai-n8n", "label": "n8n", "script": "n8n", "container": "n8n"},
    {"id": "ai-postgres", "label": "PostgreSQL (n8n)", "script": "postgres", "container": "n8n_postgres", "reset_volume": "n8n_postgres_data"},
    {"id": "ai-dashboard", "label": "LEco DevOps", "script": "dashboard", "container": "service-dashboard"},
    {"id": "ai-update-catalog", "label": "Update catalog", "script": "update-catalog", "container": "leco-update-catalog"},
    {"id": "ai-cloudflare-local", "label": "Cloudflare local (compose)", "script": "cloudflare-local", "container": None},
    {"id": "ai-infra", "label": "Infra stack (MySQL, Redis, Mailpit, …)", "script": "infra", "container": None},
]

# Infra compose: same pattern as CF — per-service lifecycle in Control + API.
# Ecosystem service scripts: dependents are stopped before the dependency on stop/remove.
ECOSYSTEM_SERVICE_REQUIRES: dict[str, tuple[str, ...]] = {
    "n8n": ("postgres",),
}

# Infra compose services (per-service Control targets).
COMPOSE_SERVICE_REQUIRES: dict[str, tuple[str, ...]] = {
    "cache-varnish": ("cache-nginx",),
    "redis-commander": ("redis",),
}


def _compose_dependents(requires_map: dict[str, tuple[str, ...]], service: str) -> tuple[str, ...]:
    return tuple(s for s, reqs in requires_map.items() if service in reqs)


def compose_action_services(service: str, action: str, requires_map: dict[str, tuple[str, ...]]) -> list[str]:
    """Order of compose service names for a lifecycle action (infra / cloudflare-local)."""
    a = (action or "").strip().lower()
    requires = requires_map.get(service, ())
    dependents = _compose_dependents(requires_map, service)
    if a in ("start", "deploy", "unpause"):
        return [*requires, service]
    if a in ("stop", "pause", "remove", "reset"):
        seen: set[str] = set()
        ordered: list[str] = []
        for s in [*dependents, service, *requires]:
            if s not in seen:
                seen.add(s)
                ordered.append(s)
        return ordered
    return [service]


INFRA_TARGETS = [
    {"id": "infra-mysql", "label": "MySQL", "compose_service": "mysql", "container": "mysql", "compose_project": "infra"},
    {"id": "infra-redis", "label": "Redis", "compose_service": "redis", "container": "redis", "compose_project": "infra"},
    {"id": "infra-mailpit", "label": "Mailpit", "compose_service": "mailpit", "container": "mailpit", "compose_project": "infra"},
    {
        "id": "infra-telegram-gateway",
        "label": "Telegram gateway",
        "compose_service": "telegram-gateway",
        "container": "telegram-gateway",
        "compose_project": "infra",
    },
    {"id": "infra-cache-nginx", "label": "Cache lab (Nginx)", "compose_service": "cache-nginx", "container": "cache-nginx", "compose_project": "infra"},
    {
        "id": "infra-cache-varnish",
        "label": "Cache lab (Varnish)",
        "compose_service": "cache-varnish",
        "container": "cache-varnish",
        "compose_project": "infra",
    },
    {"id": "infra-adminer", "label": "Adminer", "compose_service": "adminer", "container": "adminer", "compose_project": "infra"},
    {
        "id": "infra-redis-commander",
        "label": "Redis Commander",
        "compose_service": "redis-commander",
        "container": "redis-commander",
        "compose_project": "infra",
    },
]
