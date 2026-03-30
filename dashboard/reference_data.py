"""
Static URL encyclopedia for the dashboard. Health is merged client-side from /api/overview
or server-side from collect_reference_status().
"""

REFERENCE_CATEGORIES = [
    {
        "id": "platform",
        "title": "Core platform (Traefik)",
        "description": "Reverse proxy, TLS, and routing for *.lh hosts.",
        "items": [
            {
                "id": "traefik",
                "label": "Traefik dashboard / API",
                "urls": ["http://traefik.lh", "https://traefik.lh"],
                "notes": "Routes, middlewares, TLS. Use HTTPS if certs are trusted.",
            },
            {
                "id": "localhost-dash",
                "label": "Ops dashboard (this UI)",
                "urls": ["http://localhost.lh"],
                "notes": "Monitoring, metrics, control, docs.",
            },
        ],
    },
    {
        "id": "ai",
        "title": "AI & automation",
        "description": "LLM runtime, chat UI, workflows, database.",
        "items": [
            {
                "id": "ai",
                "label": "Open WebUI",
                "urls": ["http://ai.lh", "https://ai.lh"],
                "notes": "Chat interface for local models.",
            },
            {
                "id": "ollama",
                "label": "Ollama API",
                "urls": ["http://ollama.lh", "https://ollama.lh"],
                "notes": "REST API for models and generation.",
            },
            {
                "id": "n8n",
                "label": "n8n",
                "urls": ["http://n8n.lh", "https://n8n.lh"],
                "notes": "Workflow automation.",
            },
        ],
    },
    {
        "id": "cloudflare",
        "title": "Cloudflare local (emulation)",
        "description": "R2/KV/D1/Workers-style APIs and autoscaler demo; not Cloudflare production.",
        "items": [
            {
                "id": "r2",
                "label": "R2-style adapter (S3 API)",
                "urls": ["http://r2.lh"],
                "notes": "Buckets/objects via MinIO backend.",
            },
            {
                "id": "kv",
                "label": "KV-style adapter",
                "urls": ["http://kv.lh"],
                "notes": "Namespaces & keys on Valkey.",
            },
            {
                "id": "d1",
                "label": "D1-style adapter (SQLite)",
                "urls": ["http://d1.lh"],
                "notes": "Databases, SQL, backups API.",
            },
            {
                "id": "workers",
                "label": "Workers runtime (Miniflare)",
                "urls": ["http://workers.lh"],
                "notes": "Local fetch() worker; try /health.",
            },
            {
                "id": "autoscale",
                "label": "Autoscaler API",
                "urls": ["http://autoscale.lh"],
                "notes": "Status, scaling policy, replica counts.",
            },
            {
                "id": "minio-console",
                "label": "MinIO console",
                "urls": ["http://minio-console.lh"],
                "notes": "Web UI for object storage (default dev creds in compose).",
            },
        ],
    },
]
