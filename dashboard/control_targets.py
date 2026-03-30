"""
Shared Control target metadata (no Docker / heavy deps).
Imported by control.py and docs_catalog.py.
"""

COMPOSE_REL = "cloudflare-local/docker-compose.yml"

# Cloudflare-local: compose service name == container_name in this repo
CF_TARGETS = [
    {"id": "cf-minio", "label": "MinIO", "compose_service": "minio", "container": "minio"},
    {"id": "cf-valkey", "label": "Valkey", "compose_service": "valkey", "container": "valkey"},
    {"id": "cf-r2-adapter", "label": "R2 adapter", "compose_service": "r2-adapter", "container": "r2-adapter"},
    {"id": "cf-kv-adapter", "label": "KV adapter", "compose_service": "kv-adapter", "container": "kv-adapter"},
    {"id": "cf-d1-adapter", "label": "D1 adapter", "compose_service": "d1-adapter", "container": "d1-adapter"},
    {"id": "cf-workers-runtime", "label": "Workers (Miniflare)", "compose_service": "workers-runtime", "container": "workers-runtime"},
    {"id": "cf-autoscale-demo", "label": "Autoscale demo", "compose_service": "autoscale-demo", "container": "autoscale-demo"},
    {"id": "cf-autoscaler", "label": "Autoscaler", "compose_service": "autoscaler", "container": "autoscaler"},
]

# AI stack: script basename (without .sh) and container name
AI_TARGETS = [
    {"id": "ai-traefik", "label": "Traefik", "script": "traefik", "container": "traefik"},
    {"id": "ai-open-webui", "label": "Open WebUI", "script": "webui", "container": "open-webui"},
    {"id": "ai-ollama", "label": "Ollama", "script": "ollama", "container": "ollama"},
    {"id": "ai-n8n", "label": "n8n", "script": "n8n", "container": "n8n"},
    {"id": "ai-postgres", "label": "PostgreSQL (n8n)", "script": "postgres", "container": "n8n_postgres", "reset_volume": "n8n_postgres_data"},
    {"id": "ai-dashboard", "label": "Ops dashboard", "script": "dashboard", "container": "service-dashboard"},
    {"id": "ai-cloudflare-local", "label": "Cloudflare local (compose)", "script": "cloudflare-local", "container": None},
]
