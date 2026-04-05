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
                "urls": ["http://traefik.lh"],
                "notes": "Routes, middlewares, TLS. Use HTTPS if certs are trusted.",
            },
            {
                "id": "localhost-dash",
                "label": "LEco DevOps (this UI)",
                "urls": ["http://localhost.lh"],
                "notes": "Monitoring, metrics, control, docs. Service hubs: /hub for credentials & DB GUIs.",
            },
            {
                "id": "service-hubs",
                "label": "Service hubs (credentials / TCP / GUIs)",
                "urls": ["http://localhost.lh/hub"],
                "notes": "Per-service pages: MySQL, Postgres, Redis, R2, KV, D1, etc.",
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
                "urls": ["http://ai.lh"],
                "notes": "Chat interface for local models.",
            },
            {
                "id": "ollama",
                "label": "Ollama API",
                "urls": ["http://ollama.lh"],
                "notes": "REST API for models and generation.",
            },
            {
                "id": "n8n",
                "label": "n8n",
                "urls": ["http://n8n.lh"],
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
                "notes": "Buckets/objects via MinIO backend. Direct S3 endpoint: http://s3.lh (same MinIO :9000).",
            },
            {
                "id": "minio-s3",
                "label": "MinIO S3 API (direct)",
                "urls": ["http://s3.lh"],
                "notes": "S3-compatible API; use with aws-cli / SDKs (path-style or virtual-host per client).",
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
                "id": "browser-local",
                "label": "Browser rendering (local)",
                "urls": ["http://browser.lh"],
                "notes": "Screenshot/PDF/HTML APIs; BROWSER_BACKEND=playwright or chromium.",
            },
            {
                "id": "mailpit",
                "label": "Mailpit (infra)",
                "urls": ["http://mail.lh"],
                "notes": "SMTP :1025 from containers; web UI via Traefik.",
            },
            {
                "id": "telegram-gw",
                "label": "Telegram gateway (infra)",
                "urls": ["http://telegram.lh"],
                "notes": "Webhook + sendMessage; set TELEGRAM_BOT_TOKEN.",
            },
            {
                "id": "cache-lab",
                "label": "Varnish cache lab (infra)",
                "urls": ["http://cache.lh"],
                "notes": "Traefik → Varnish → Nginx static.",
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
            {
                "id": "adminer",
                "label": "Adminer · MySQL & PostgreSQL",
                "urls": ["http://adminer.lh"],
                "notes": "SQL GUI. Server host: mysql or n8n_postgres; use credentials from service hubs.",
            },
            {
                "id": "redis-ui",
                "label": "Redis Commander · infra Redis",
                "urls": ["http://redis-ui.lh"],
                "notes": "Keys for redis:6379. KV stack uses Valkey separately.",
            },
        ],
    },
]
