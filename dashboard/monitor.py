import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

import docker
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SERVICE_MAP = [
    {
        "service": "Traefik",
        "container": "traefik",
        "urls": ["http://traefik.lh"],
        "notes": "Reverse proxy and dashboard",
    },
    {
        "service": "Open WebUI",
        "container": "open-webui",
        "urls": ["http://ai.lh"],
        "notes": "AI chat user interface",
    },
    {
        "service": "n8n",
        "container": "n8n",
        "urls": ["http://n8n.lh"],
        "notes": "Workflow automation",
    },
    {
        "service": "Ollama",
        "container": "ollama",
        "urls": ["http://ollama.lh"],
        "notes": "LLM runtime",
    },
    {
        "service": "Service Dashboard",
        "container": "service-dashboard",
        "urls": ["http://localhost.lh"],
        "notes": "Ops monitoring dashboard",
        "hub_slug": "dashboard",
        "insights": [
            "Service hubs live under /hub/<name> for credentials, TCP hints, and database GUIs.",
        ],
        "management_links": [
            {"label": "Hub · dashboard", "url": "http://localhost.lh/hub/dashboard"},
        ],
    },
    {
        "service": "PostgreSQL",
        "container": "n8n_postgres",
        "urls": ["http://localhost.lh/hub/postgres"],
        "notes": "n8n database · postgres.lh:5432 on host (Docker service n8n_postgres)",
        "hub_slug": "postgres",
        "credentials": [
            "User postgres · password password · database n8n (from ai-stack/services/postgres.sh defaults).",
        ],
        "connection_strings": [
            "postgresql://postgres:password@postgres.lh:5432/n8n",
            "psql -h postgres.lh -p 5432 -U postgres -d n8n",
        ],
        "insights": [
            "Host: postgres.lh:5432 (published to loopback; same DNS as other *.lh). In Adminer use server mysql / n8n_postgres (Docker service names from the Adminer container).",
        ],
        "database_guis": [
            {"label": "Adminer (SQL UI · pick PostgreSQL)", "url": "http://adminer.lh"},
        ],
        "management_links": [
            {"label": "Service hub", "url": "http://localhost.lh/hub/postgres"},
        ],
    },
    {
        "service": "R2 (Cloudflare local)",
        "container": "r2-adapter",
        "urls": ["http://r2.lh"],
        "notes": "S3-compatible API (MinIO backend)",
        "hub_slug": "r2",
        "credentials": [
            "S3/MinIO (dev): access key minioadmin · secret minioadmin",
        ],
        "connection_strings": [
            "S3-style API (adapter): http://r2.lh and https://r2.lh",
            "Direct MinIO S3 API: http://s3.lh and https://s3.lh (same keys as MinIO console)",
        ],
        "insights": [
            "Bucket and object counts appear in Cloudflare local panel when reachable.",
        ],
        "database_guis": [
            {"label": "Adapter panel (buckets / API)", "url": "http://r2.lh/panel"},
            {"label": "MinIO object browser", "url": "http://minio-console.lh"},
        ],
        "management_links": [
            {"label": "Management & bucket explorer", "url": "http://r2.lh/panel"},
            {"label": "Health JSON", "url": "http://r2.lh/health"},
            {"label": "MinIO console (same credentials)", "url": "http://minio-console.lh"},
            {"label": "Service hub", "url": "http://localhost.lh/hub/r2"},
        ],
    },
    {
        "service": "KV (Cloudflare local)",
        "container": "kv-adapter",
        "urls": ["http://kv.lh"],
        "notes": "KV-style API on Valkey",
        "hub_slug": "kv",
        "credentials": [
            "Default Valkey: no password. Published as valkey.lh:6380 → container :6379 (infra Redis uses redis.lh:6379).",
        ],
        "connection_strings": [
            "redis://valkey.lh:6380/0",
            "redis://redis.lh:6379 (separate infra Redis)",
        ],
        "insights": [
            "KV adapter namespaces map to Valkey key prefixes; see adapter /panel.",
        ],
        "database_guis": [
            {"label": "KV adapter panel", "url": "http://kv.lh/panel"},
            {"label": "Redis Commander (infra Redis)", "url": "http://redis-ui.lh"},
        ],
        "management_links": [
            {"label": "Management & namespace explorer", "url": "http://kv.lh/panel"},
            {"label": "Health JSON", "url": "http://kv.lh/health"},
            {"label": "Service hub", "url": "http://localhost.lh/hub/kv"},
        ],
    },
    {
        "service": "D1 (Cloudflare local)",
        "container": "d1-adapter",
        "urls": ["http://d1.lh"],
        "notes": "SQLite D1-style API",
        "hub_slug": "d1",
        "credentials": [
            "No API auth — SQLite files under adapter volume (local dev only).",
        ],
        "connection_strings": [
            "HTTP API: http://d1.lh and https://d1.lh — SQLite files live in the d1-adapter volume (not on .lh TCP).",
        ],
        "insights": [
            "Use /panel for SQL explorer; backups via adapter API when configured.",
        ],
        "database_guis": [
            {"label": "D1 adapter SQL panel", "url": "http://d1.lh/panel"},
            {"label": "Adminer (MySQL/Postgres — not SQLite files)", "url": "http://adminer.lh"},
        ],
        "management_links": [
            {"label": "Management & SQL explorer", "url": "http://d1.lh/panel"},
            {"label": "Health JSON", "url": "http://d1.lh/health"},
            {"label": "Service hub", "url": "http://localhost.lh/hub/d1"},
        ],
    },
    {
        "service": "Autoscaler",
        "container": "autoscaler",
        "urls": ["http://autoscale.lh"],
        "notes": "Docker replica scaler demo API",
        "hub_slug": "autoscale",
        "credentials": [
            "No API auth in local stack.",
        ],
        "insights": [
            "Policy and replica counts mirror Overview / Infrastructure CF charts.",
        ],
        "management_links": [
            {"label": "Management panel (live status)", "url": "http://autoscale.lh/panel"},
            {"label": "Status JSON", "url": "http://autoscale.lh/status"},
            {"label": "Service hub", "url": "http://localhost.lh/hub/autoscale"},
        ],
    },
    {
        "service": "MinIO Console",
        "container": "minio",
        "urls": ["http://minio-console.lh"],
        "notes": "Object store web UI",
        "hub_slug": "minio",
        "credentials": [
            "Console login: minioadmin / minioadmin (dev defaults from compose).",
        ],
        "connection_strings": [
            "Console: http://minio-console.lh · S3 API: http://s3.lh / https://s3.lh",
        ],
        "management_links": [
            {"label": "Open console", "url": "http://minio-console.lh"},
            {"label": "Service hub", "url": "http://localhost.lh/hub/minio"},
        ],
    },
    {
        "service": "Workers (Miniflare)",
        "container": "workers-runtime",
        "urls": ["http://workers.lh"],
        "notes": "Local Cloudflare Workers runtime (Miniflare)",
        "hub_slug": "workers",
        "credentials": [
            "No auth — fetch handler only. Root URL returns JSON.",
        ],
        "management_links": [
            {"label": "Info & troubleshooting", "url": "http://workers.lh/panel"},
            {"label": "Health JSON", "url": "http://workers.lh/health"},
            {"label": "Service hub", "url": "http://localhost.lh/hub/workers"},
        ],
    },
    {
        "service": "Browser rendering (local)",
        "container": "browser-rendering-local",
        "urls": ["http://browser.lh"],
        "notes": "Headless Chromium via Playwright or system CDP — not Cloudflare edge",
        "hub_slug": "browser",
        "credentials": [
            "Set BROWSER_BACKEND=playwright or chromium in compose.",
        ],
        "management_links": [
            {"label": "Panel", "url": "http://browser.lh/panel"},
            {"label": "Health JSON", "url": "http://browser.lh/health"},
            {"label": "Service hub", "url": "http://localhost.lh/hub/browser"},
        ],
    },
    {
        "service": "MySQL (infra)",
        "container": "mysql",
        "urls": ["http://localhost.lh/hub/mysql"],
        "notes": "MySQL · host access mysql.lh:3306 (published); in Docker use service name mysql",
        "hub_slug": "mysql",
        "credentials": [
            "Root password: localdev (default MYSQL_ROOT_PASSWORD in infra/docker-compose.yml unless overridden).",
            "Database localdev created by default.",
        ],
        "connection_strings": [
            "mysql://root:localdev@mysql.lh:3306/localdev",
            "mysql -h mysql.lh -P 3306 -u root -plocaldev",
        ],
        "insights": [
            "Adminer (adminer.lh): System MySQL, Server **mysql** (Docker DNS). CLI from Mac/Windows: **mysql.lh:3306** with *.lh → loopback.",
        ],
        "database_guis": [
            {"label": "Adminer", "url": "http://adminer.lh"},
        ],
        "management_links": [
            {"label": "Service hub", "url": "http://localhost.lh/hub/mysql"},
        ],
    },
    {
        "service": "Redis (infra)",
        "container": "redis",
        "urls": ["http://localhost.lh/hub/redis"],
        "notes": "Infra Redis · redis.lh:6379 on host; KV stack uses valkey.lh:6380 separately",
        "hub_slug": "redis",
        "credentials": [
            "No password by default (infra redis).",
        ],
        "connection_strings": [
            "redis://redis.lh:6379",
            "redis-cli -h redis.lh -p 6379",
        ],
        "insights": [
            "KV adapter uses Valkey at valkey.lh:6380. This Redis is for app cache/queues.",
        ],
        "database_guis": [
            {"label": "Redis Commander", "url": "http://redis-ui.lh"},
        ],
        "management_links": [
            {"label": "Service hub", "url": "http://localhost.lh/hub/redis"},
        ],
    },
    {
        "service": "Mailpit",
        "container": "mailpit",
        "urls": ["http://mail.lh"],
        "notes": "SMTP catch-all (container :1025) + web UI",
        "hub_slug": "mailpit",
        "connection_strings": [
            "SMTP: mailpit.lh:1025 (from host) · inside Docker: mailpit:1025",
        ],
        "management_links": [
            {"label": "Web UI", "url": "http://mail.lh"},
            {"label": "Service hub", "url": "http://localhost.lh/hub/mailpit"},
        ],
    },
    {
        "service": "Telegram gateway",
        "container": "telegram-gateway",
        "urls": ["http://telegram.lh"],
        "notes": "Bot webhook + sendMessage helper — set TELEGRAM_BOT_TOKEN",
        "hub_slug": "telegram",
        "management_links": [
            {"label": "Panel", "url": "http://telegram.lh/panel"},
            {"label": "Health", "url": "http://telegram.lh/health"},
            {"label": "Service hub", "url": "http://localhost.lh/hub/telegram"},
        ],
    },
    {
        "service": "Varnish cache lab",
        "container": "cache-varnish",
        "urls": ["http://cache.lh"],
        "notes": "Traefik → Varnish → Nginx static origin",
        "hub_slug": "cache-lab",
        "insights": [
            "VCL TTL and backend are under infra/varnish/default.vcl.",
        ],
        "management_links": [
            {"label": "Cached page", "url": "http://cache.lh"},
            {"label": "Service hub", "url": "http://localhost.lh/hub/cache-lab"},
        ],
    },
    {
        "service": "Adminer (SQL GUI)",
        "container": "adminer",
        "urls": ["http://adminer.lh"],
        "notes": "Web UI for MySQL & PostgreSQL on lh-network",
        "hub_slug": "adminer",
        "credentials": [
            "System → MySQL or PostgreSQL. Server host: **mysql** or **n8n_postgres** (Docker names from Adminer). Credentials: see MySQL / PostgreSQL hubs.",
        ],
        "management_links": [
            {"label": "Open Adminer", "url": "http://adminer.lh"},
            {"label": "Service hub", "url": "http://localhost.lh/hub/adminer"},
        ],
    },
    {
        "service": "Redis Commander",
        "container": "redis-commander",
        "urls": ["http://redis-ui.lh"],
        "notes": "Web UI for infra Redis (not Valkey/KV)",
        "hub_slug": "redis-ui",
        "management_links": [
            {"label": "Open Redis Commander", "url": "http://redis-ui.lh"},
            {"label": "Service hub", "url": "http://localhost.lh/hub/redis-ui"},
        ],
    },
]

# When Traefik edge probes fail (502, redirect chains to *.lh from inside the dashboard, etc.),
# verify the app container on lh-network directly.
INTERNAL_PROBE_BY_CONTAINER = {
    "n8n": "http://n8n:5678/",
    "open-webui": "http://open-webui:8080/",
    "ollama": "http://ollama:11434/",
    "traefik": "http://traefik:8080/api/version",
    "service-dashboard": "http://service-dashboard:8090/",
    "browser-rendering-local": "http://browser-rendering-local:8085/health",
    "mailpit": "http://mailpit:8025/",
    "telegram-gateway": "http://telegram-gateway:8091/health",
    "cache-varnish": "http://cache-varnish:80/",
    "mysql": "http://service-dashboard:8090/hub/mysql",
    "n8n_postgres": "http://service-dashboard:8090/hub/postgres",
    "redis": "http://service-dashboard:8090/hub/redis",
    "adminer": "http://adminer:8080/",
    "redis-commander": "http://redis-commander:8081/",
}

ERROR_REGEX = re.compile(r"\b(error|exception|fatal|panic|failed|traceback)\b", re.IGNORECASE)
WARN_REGEX = re.compile(r"\b(warn|warning|deprecated|timeout)\b", re.IGNORECASE)
INFO_REGEX = re.compile(r"\b(info|started|ready|listening|connected|ok)\b", re.IGNORECASE)

# Traefik emits these when the process or entrypoints shut down (e.g. docker restart); not service faults.
_TRAEFIK_SHUTDOWN_NOISE = re.compile(r"use of closed network connection", re.IGNORECASE)
# Idle or slow TCP to :80/:443 (host probes, preconnect) — not a misconfigured route.
_TRAEFIK_PEEK_NOISE = re.compile(
    r"Error while Peeking first byte|peeking first byte", re.IGNORECASE
)


def _is_traefik_log_noise(container_name: str, line: str) -> bool:
    if not container_name or container_name.strip("/") != "traefik":
        return False
    if _TRAEFIK_SHUTDOWN_NOISE.search(line):
        return True
    return bool(_TRAEFIK_PEEK_NOISE.search(line))
LOG_WINDOW_SECONDS = 300
LOG_TAIL_LINES = 300
CLOUDFLARE_ENDPOINTS = {
    "r2": "http://r2-adapter:8081",
    "kv": "http://kv-adapter:8082",
    "d1": "http://d1-adapter:8083",
    "autoscale": "http://autoscaler:8084",
    "workers": "http://workers-runtime:8787",
    "browser": "http://browser-rendering-local:8085",
}


def to_float(value):
    try:
        return float(value)
    except Exception:
        return 0.0


def get_docker_client():
    try:
        return docker.from_env()
    except Exception:
        return None


def get_container(client, container_name):
    if client is None or not container_name:
        return None
    cn = container_name.lstrip("/")
    try:
        return client.containers.get(cn)
    except docker.errors.NotFound:
        pass
    except Exception:
        return None
    try:
        for c in client.containers.list(all=True):
            if (c.name or "").lstrip("/") == cn:
                return c
    except Exception:
        pass
    return None


def get_container_info(container):
    if container is None:
        return {
            "exists": False,
            "status": "missing",
            "state": "not-found",
            "health": "unknown",
            "restart_count": 0,
            "started_at": None,
            "networks": [],
        }

    attrs = container.attrs or {}
    state = attrs.get("State", {})
    network_map = attrs.get("NetworkSettings", {}).get("Networks", {})
    health = (state.get("Health") or {}).get("Status", "none")
    return {
        "exists": True,
        "status": container.status,
        "state": state.get("Status", container.status),
        "health": health,
        "restart_count": state.get("RestartCount", 0),
        "started_at": state.get("StartedAt"),
        "networks": sorted(network_map.keys()),
    }


def get_probe_target(url):
    parsed = urlparse(url)
    host = parsed.hostname or ""

    if host.endswith(".lh"):
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        probe_url = f"{parsed.scheme}://traefik{path}"
        headers = {"Host": host}
        return probe_url, headers

    return url, {}


def expand_lh_urls(urls: list[str] | None) -> list[str]:
    """Build unique http+https pairs for *.lh URLs (Traefik serves both). Sorted: host, path, http before https."""
    if not urls:
        return []
    pool: set[str] = set()
    for raw in urls:
        u = (raw or "").strip()
        if not u:
            continue
        pool.add(u)
        try:
            p = urlparse(u)
        except Exception:
            continue
        host = (p.hostname or "").lower()
        if not host.endswith(".lh"):
            continue
        path = p.path or "/"
        netloc = p.netloc
        if u.startswith("http://"):
            pool.add(urlunparse(("https", netloc, path, p.params, p.query, p.fragment)))
        elif u.startswith("https://"):
            pool.add(urlunparse(("http", netloc, path, p.params, p.query, p.fragment)))

    def sort_key(x: str) -> tuple:
        p = urlparse(x)
        sch = 0 if p.scheme == "http" else 1
        return ((p.hostname or "").lower(), p.path or "/", sch)

    return sorted(pool, key=sort_key)


def normalize_lh_probe_urls(urls: list[str] | None) -> list[str]:
    """At most one http and one https per (host, path) — avoids triple probes if SERVICE_MAP listed both schemes."""
    expanded = expand_lh_urls(urls)
    order: list[tuple[str, str]] = []
    buckets: dict[tuple[str, str], dict[str, str | None]] = {}
    for u in expanded:
        try:
            p = urlparse(u)
        except Exception:
            continue
        host = (p.hostname or "").lower()
        path = p.path or "/"
        if host.endswith(".lh"):
            key = (host, path)
            if key not in buckets:
                order.append(key)
                buckets[key] = {"http": None, "https": None}
            if u.startswith("http://"):
                buckets[key]["http"] = u
            elif u.startswith("https://"):
                buckets[key]["https"] = u
        else:
            key = ("*", u)
            if key not in buckets:
                order.append(key)
                buckets[key] = {"only": u}
    out: list[str] = []
    for key in order:
        b = buckets[key]
        if "only" in b:
            out.append(b["only"])
        else:
            if b.get("http"):
                out.append(b["http"])
            if b.get("https"):
                out.append(b["https"])
    return out


def check_url(url):
    """Probe public URL. Do not follow redirects to https://*.lh — that host often breaks inside the dashboard container."""
    probe_url, headers = get_probe_target(url)
    try:
        response = requests.get(
            probe_url,
            timeout=5,
            verify=False,
            allow_redirects=False,
            headers=headers,
        )
        return {
            "url": url,
            "probe_url": probe_url,
            "ok": response.status_code < 500,
            "status_code": response.status_code,
            "latency_ms": round(response.elapsed.total_seconds() * 1000, 2),
        }
    except Exception as exc:
        return {
            "url": url,
            "probe_url": probe_url,
            "ok": False,
            "status_code": None,
            "error": str(exc),
            "latency_ms": None,
        }


def _probe_backend_internal(container_name: str) -> tuple[bool, int | None, float | None]:
    internal = INTERNAL_PROBE_BY_CONTAINER.get(container_name)
    if not internal:
        return False, None, None
    try:
        t0 = time.perf_counter()
        r = requests.get(internal, timeout=4, verify=False, allow_redirects=False)
        ms = round((time.perf_counter() - t0) * 1000, 2)
        ok = r.status_code < 500
        return ok, r.status_code, ms
    except Exception:
        return False, None, None


def check_urls_for_service(urls: list[str], container_name: str) -> list[dict]:
    """Run edge probes; if all fail with gateway/connection issues, accept internal Docker-network probe as OK."""
    checks = [check_url(u) for u in urls]
    internal_url = INTERNAL_PROBE_BY_CONTAINER.get(container_name)
    if not internal_url or not checks:
        return checks

    def needs_recovery(ch: dict) -> bool:
        if ch.get("ok"):
            return False
        sc = ch.get("status_code")
        if sc is None:
            return True
        return sc >= 502

    if not any(needs_recovery(c) for c in checks):
        return checks

    be_ok, be_sc, be_ms = _probe_backend_internal(container_name)
    if not be_ok:
        return checks

    for c in checks:
        if c.get("ok"):
            continue
        if not needs_recovery(c):
            continue
        edge_sc = c.get("status_code")
        c["ok"] = True
        c["status_code"] = be_sc
        c["latency_ms"] = be_ms
        c["probe_via"] = "internal"
        c["edge_status_code"] = edge_sc
        if c.get("error"):
            c["edge_error"] = c.pop("error", None)
    return checks


def get_container_sizes(client, container):
    if client is None or container is None:
        return {"size_rw": 0, "size_root_fs": 0}
    try:
        detail = client.api.inspect_container(container.id, size=True)
        return {
            "size_rw": int(detail.get("SizeRw") or 0),
            "size_root_fs": int(detail.get("SizeRootFs") or 0),
        }
    except Exception:
        return {"size_rw": 0, "size_root_fs": 0}


def get_container_metrics(client, container_or_name):
    if client is None:
        return {
            "cpu_percent": 0.0,
            "memory_usage": 0,
            "memory_limit": 0,
            "memory_percent": 0.0,
            "network_rx": 0,
            "network_tx": 0,
            "blk_read": 0,
            "blk_write": 0,
            "size_rw": 0,
            "size_root_fs": 0,
        }

    container = container_or_name
    if isinstance(container_or_name, str):
        container = get_container(client, container_or_name)

    if container is None:
        return {
            "cpu_percent": 0.0,
            "memory_usage": 0,
            "memory_limit": 0,
            "memory_percent": 0.0,
            "network_rx": 0,
            "network_tx": 0,
            "blk_read": 0,
            "blk_write": 0,
            "size_rw": 0,
            "size_root_fs": 0,
        }

    try:
        stats = container.stats(stream=False)
    except Exception:
        stats = {}

    cpu_stats = stats.get("cpu_stats", {})
    precpu_stats = stats.get("precpu_stats", {})
    cpu_delta = (
        cpu_stats.get("cpu_usage", {}).get("total_usage", 0)
        - precpu_stats.get("cpu_usage", {}).get("total_usage", 0)
    )
    system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get("system_cpu_usage", 0)
    online_cpus = cpu_stats.get("online_cpus") or len(cpu_stats.get("cpu_usage", {}).get("percpu_usage") or [1])
    cpu_percent = (cpu_delta / system_delta) * online_cpus * 100 if system_delta > 0 else 0.0

    memory_stats = stats.get("memory_stats", {})
    memory_usage = int(memory_stats.get("usage") or 0)
    memory_limit = int(memory_stats.get("limit") or 0)
    memory_percent = (memory_usage / memory_limit) * 100 if memory_limit > 0 else 0.0

    networks = stats.get("networks") or {}
    network_rx = sum(int(v.get("rx_bytes") or 0) for v in networks.values())
    network_tx = sum(int(v.get("tx_bytes") or 0) for v in networks.values())

    blk_entries = stats.get("blkio_stats", {}).get("io_service_bytes_recursive") or []
    blk_read = sum(int(e.get("value") or 0) for e in blk_entries if e.get("op") == "Read")
    blk_write = sum(int(e.get("value") or 0) for e in blk_entries if e.get("op") == "Write")

    sizes = get_container_sizes(client, container)
    return {
        "cpu_percent": round(to_float(cpu_percent), 2),
        "memory_usage": memory_usage,
        "memory_limit": memory_limit,
        "memory_percent": round(to_float(memory_percent), 2),
        "network_rx": network_rx,
        "network_tx": network_tx,
        "blk_read": blk_read,
        "blk_write": blk_write,
        "size_rw": sizes["size_rw"],
        "size_root_fs": sizes["size_root_fs"],
    }


def get_log_metrics(container, container_name: str | None = None):
    if container is None:
        return {
            "window_seconds": LOG_WINDOW_SECONDS,
            "total_lines": 0,
            "error_lines": 0,
            "error_rate_per_min": 0.0,
            "last_errors": [],
        }

    cname = (container_name or getattr(container, "name", None) or "").strip().lstrip("/")

    start_since = int(time.time()) - LOG_WINDOW_SECONDS
    try:
        raw_logs = container.logs(
            tail=LOG_TAIL_LINES,
            since=start_since,
            timestamps=True,
        )
        text = raw_logs.decode("utf-8", errors="ignore")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
    except Exception:
        lines = []

    error_lines = [
        line
        for line in lines
        if ERROR_REGEX.search(line) and not _is_traefik_log_noise(cname, line)
    ]
    return {
        "window_seconds": LOG_WINDOW_SECONDS,
        "total_lines": len(lines),
        "error_lines": len(error_lines),
        "error_rate_per_min": round(len(error_lines) / (LOG_WINDOW_SECONDS / 60), 2),
        "last_errors": error_lines[-3:],
    }


def get_docker_overview(client):
    if client is None:
        return {
            "docker_available": False,
            "host": {},
            "counts": {},
            "disk": {},
        }

    try:
        info = client.info()
    except Exception:
        info = {}
    try:
        df = client.df()
    except Exception:
        df = {}

    images = df.get("Images", []) or []
    containers = df.get("Containers", []) or []
    volumes = df.get("Volumes", []) or []
    build_cache = df.get("BuildCache", []) or []

    image_bytes = sum(int(i.get("Size") or 0) for i in images)
    container_rw_bytes = sum(int(c.get("SizeRw") or 0) for c in containers)
    container_root_bytes = sum(int(c.get("SizeRootFs") or 0) for c in containers)
    volume_bytes = sum(int(v.get("UsageData", {}).get("Size") or 0) for v in volumes)
    build_cache_bytes = sum(int(b.get("Size") or 0) for b in build_cache)

    return {
        "docker_available": True,
        "host": {
            "server_version": info.get("ServerVersion"),
            "operating_system": info.get("OperatingSystem"),
            "kernel_version": info.get("KernelVersion"),
            "cpus": info.get("NCPU"),
            "memory_total": int(info.get("MemTotal") or 0),
            "docker_root_dir": info.get("DockerRootDir"),
        },
        "counts": {
            "containers_running": info.get("ContainersRunning"),
            "containers_paused": info.get("ContainersPaused"),
            "containers_stopped": info.get("ContainersStopped"),
            "images": info.get("Images"),
            "volumes": len(volumes),
        },
        "disk": {
            "images": image_bytes,
            "container_rw": container_rw_bytes,
            "container_rootfs": container_root_bytes,
            "volumes": volume_bytes,
            "build_cache": build_cache_bytes,
            "total_tracked": image_bytes + container_rw_bytes + container_root_bytes + volume_bytes + build_cache_bytes,
        },
    }


def build_system_status(services, docker_overview):
    total_services = len(services)
    running_services = 0
    paused_services = 0
    missing_services = 0
    total_urls = 0
    healthy_urls = 0
    total_error_lines = 0
    total_error_rate_per_min = 0.0
    aggregate_cpu_percent = 0.0
    aggregate_memory_usage = 0
    aggregate_memory_limit = 0
    network_mismatches = []

    for service in services:
        info = service.get("container_info", {})
        metrics = service.get("metrics", {})
        logs = service.get("logs", {})
        checks = service.get("url_checks", [])

        status = info.get("status")
        if status == "running":
            running_services += 1
        elif status == "paused":
            paused_services += 1
        if not info.get("exists", False):
            missing_services += 1

        total_urls += len(checks)
        healthy_urls += sum(1 for check in checks if check.get("ok"))

        total_error_lines += int(logs.get("error_lines") or 0)
        total_error_rate_per_min += to_float(logs.get("error_rate_per_min"))

        aggregate_cpu_percent += to_float(metrics.get("cpu_percent"))
        aggregate_memory_usage += int(metrics.get("memory_usage") or 0)
        aggregate_memory_limit += int(metrics.get("memory_limit") or 0)

        networks = info.get("networks", [])
        if info.get("exists") and "lh-network" not in networks:
            network_mismatches.append(service.get("container"))

    aggregate_memory_percent = (
        (aggregate_memory_usage / aggregate_memory_limit) * 100 if aggregate_memory_limit > 0 else 0.0
    )
    unhealthy_urls = total_urls - healthy_urls

    alerts = []
    if not docker_overview.get("docker_available"):
        alerts.append("Docker API is unavailable.")
    if missing_services:
        alerts.append(f"{missing_services} managed service container(s) are missing.")
    if network_mismatches:
        alerts.append(f"Container(s) missing lh-network: {', '.join(network_mismatches)}")
    if unhealthy_urls:
        alerts.append(f"{unhealthy_urls} URL probe(s) failing.")
    if total_error_lines:
        alerts.append(f"{total_error_lines} log error line(s) detected in recent window.")

    if alerts:
        level = "critical" if not docker_overview.get("docker_available") else "degraded"
    else:
        level = "healthy"

    return {
        "level": level,
        "docker_available": docker_overview.get("docker_available", False),
        "services_running": running_services,
        "services_total": total_services,
        "services_paused": paused_services,
        "services_missing": missing_services,
        "healthy_urls": healthy_urls,
        "total_urls": total_urls,
        "unhealthy_urls": unhealthy_urls,
        "total_error_lines": total_error_lines,
        "total_error_rate_per_min": round(total_error_rate_per_min, 2),
        # Sum of per-container Docker stats for SERVICE_MAP entries only (not all running containers).
        "aggregate_cpu_percent": round(aggregate_cpu_percent, 2),
        "aggregate_memory_usage": aggregate_memory_usage,
        "aggregate_memory_limit": aggregate_memory_limit,
        "aggregate_memory_percent": round(aggregate_memory_percent, 2),
        "aggregate_note": (
            "managed_services_only: CPU is the raw sum of per-container % (can exceed 100% on multi-core); "
            "RAM is Σ usage / Σ limits for probed stack services only — not the same as Deep metrics."
        ),
        "alerts": alerts,
    }


def collect_reference_status():
    from reference_data import REFERENCE_CATEGORIES

    categories = []
    for cat in REFERENCE_CATEGORIES:
        items_out = []
        for item in cat["items"]:
            expanded = normalize_lh_probe_urls(list(item.get("urls", [])))
            url_checks = [check_url(url) for url in expanded]
            items_out.append({**item, "urls": expanded, "url_checks": url_checks})
        categories.append(
            {
                "id": cat["id"],
                "title": cat["title"],
                "description": cat.get("description", ""),
                "items": items_out,
            }
        )
    healthy = sum(
        1
        for c in categories
        for it in c["items"]
        for chk in it.get("url_checks", [])
        if chk.get("ok")
    )
    total = sum(len(it.get("url_checks", [])) for c in categories for it in c["items"])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "categories": categories,
        "healthy_urls": healthy,
        "total_urls": total,
    }


def collect_overview():
    client = get_docker_client()
    docker_overview = get_docker_overview(client)

    services = []
    all_urls = []

    for item in SERVICE_MAP:
        container = get_container(client, item["container"])
        container_info = get_container_info(container)
        expanded_urls = normalize_lh_probe_urls(list(item.get("urls") or []))
        checks = check_urls_for_service(expanded_urls, item["container"])
        all_urls.extend(checks)
        slug = item.get("hub_slug")
        services.append(
            {
                "service": item["service"],
                "container": item["container"],
                "notes": item["notes"],
                "credentials": item.get("credentials") or [],
                "connection_strings": item.get("connection_strings") or [],
                "insights": item.get("insights") or [],
                "database_guis": item.get("database_guis") or [],
                "management_links": item.get("management_links") or [],
                "hub_slug": slug,
                "hub_path": f"/hub/{slug}" if slug else None,
                "container_info": container_info,
                "metrics": get_container_metrics(client, container),
                "logs": get_log_metrics(container, item["container"]),
                "url_checks": checks,
            }
        )

    containers = []
    if client is not None:
        try:
            for c in client.containers.list(all=True):
                ports = c.attrs.get("NetworkSettings", {}).get("Ports", {})
                state = c.attrs.get("State", {})
                containers.append(
                    {
                        "name": c.name,
                        "image": c.image.tags[0] if c.image.tags else c.image.short_id,
                        "status": c.status,
                        "restart_count": state.get("RestartCount", 0),
                        "ports": ports,
                    }
                )
        except Exception:
            pass

    system_status = build_system_status(services, docker_overview)

    try:
        from timeseries import aggregate_running_container_stats, append_snapshot, compute_docker_totals_aligned

        agg = aggregate_running_container_stats(client) if client is not None else None
        aligned = compute_docker_totals_aligned(client, docker_overview, agg) if agg is not None else None
        if aligned:
            system_status["docker_totals_all_running"] = aligned
        append_snapshot(client, docker_overview, precomputed_container_agg=agg)
    except Exception:
        pass

    ollama_llm = None
    try:
        from ollama_models import build_models_payload

        om = build_models_payload()
        ollama_llm = {
            "ollama_reachable": om.get("ollama_reachable"),
            "ollama_base": om.get("ollama_base"),
            "server_version": om.get("server_version"),
            "installed_count": om.get("installed_count"),
            "running_count": om.get("running_count"),
            "rows": om.get("rows") or [],
        }
    except Exception as exc:
        ollama_llm = {"ollama_reachable": False, "error": str(exc)[:200], "rows": []}

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "service_count": len(services),
        "url_count": len(all_urls),
        "healthy_urls": sum(1 for u in all_urls if u.get("ok")),
        "services": services,
        "containers": sorted(containers, key=lambda c: c["name"]),
        "docker_overview": docker_overview,
        "system_status": system_status,
        "reference": collect_reference_status(),
        "ollama_llm": ollama_llm,
    }


def list_managed_services():
    return [
        {"service": item["service"], "container": item["container"], "notes": item["notes"]}
        for item in SERVICE_MAP
    ]


def infer_log_level(line):
    if ERROR_REGEX.search(line):
        return "error"
    if WARN_REGEX.search(line):
        return "warn"
    if INFO_REGEX.search(line):
        return "info"
    return "other"


def parse_log_line(line):
    parts = line.split(" ", 1)
    if len(parts) == 2 and "T" in parts[0]:
        return {"timestamp": parts[0], "message": parts[1]}
    return {"timestamp": "", "message": line}


def collect_service_logs(service_container, search="", level="all", tail=500, since_seconds=1800):
    client = get_docker_client()
    container = get_container(client, service_container)
    if container is None:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "container": service_container,
            "exists": False,
            "entries": [],
        }

    since = int(time.time()) - max(1, int(since_seconds))
    try:
        raw_logs = container.logs(
            tail=max(1, int(tail)),
            since=since,
            timestamps=True,
        )
        text = raw_logs.decode("utf-8", errors="ignore")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
    except Exception:
        lines = []

    search_q = (search or "").strip().lower()
    selected_level = (level or "all").lower()
    entries = []
    level_counts = {"error": 0, "warn": 0, "info": 0, "other": 0}

    for line in lines:
        parsed = parse_log_line(line)
        log_level = infer_log_level(parsed["message"])
        if _is_traefik_log_noise(service_container, line):
            log_level = "other"
        level_counts[log_level] = level_counts.get(log_level, 0) + 1

        if selected_level != "all" and log_level != selected_level:
            continue
        if search_q and search_q not in parsed["message"].lower():
            continue

        entries.append(
            {
                "timestamp": parsed["timestamp"],
                "level": log_level,
                "message": parsed["message"],
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "container": service_container,
        "exists": True,
        "total_scanned": len(lines),
        "returned": len(entries),
        "level_counts": level_counts,
        "entries": entries[-1000:],
    }


def _fetch_json(url):
    try:
        res = requests.get(url, timeout=3)
        res.raise_for_status()
        return True, res.json()
    except Exception as exc:
        return False, {"ok": False, "error": str(exc)}


def collect_cloudflare_local_status():
    r2_ok, r2_health = _fetch_json(f"{CLOUDFLARE_ENDPOINTS['r2']}/health")
    kv_ok, kv_health = _fetch_json(f"{CLOUDFLARE_ENDPOINTS['kv']}/health")
    d1_ok, d1_health = _fetch_json(f"{CLOUDFLARE_ENDPOINTS['d1']}/health")
    as_ok, as_status = _fetch_json(f"{CLOUDFLARE_ENDPOINTS['autoscale']}/status")
    w_ok, w_health = _fetch_json(f"{CLOUDFLARE_ENDPOINTS['workers']}/health")
    br_ok, br_health = _fetch_json(f"{CLOUDFLARE_ENDPOINTS['browser']}/health")

    buckets_ok, buckets_data = _fetch_json(f"{CLOUDFLARE_ENDPOINTS['r2']}/buckets")
    namespaces_ok, ns_data = _fetch_json(f"{CLOUDFLARE_ENDPOINTS['kv']}/namespaces")
    dbs_ok, dbs_data = _fetch_json(f"{CLOUDFLARE_ENDPOINTS['d1']}/databases")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "services": {
            "r2": {"reachable": r2_ok, "health": r2_health},
            "kv": {"reachable": kv_ok, "health": kv_health},
            "d1": {"reachable": d1_ok, "health": d1_health},
            "autoscale": {"reachable": as_ok, "status": as_status},
            "workers": {"reachable": w_ok, "health": w_health},
            "browser": {"reachable": br_ok, "health": br_health},
        },
        "counts": {
            "buckets": len(buckets_data.get("buckets", [])) if buckets_ok else 0,
            "namespaces": len(ns_data.get("namespaces", [])) if namespaces_ok else 0,
            "databases": len(dbs_data.get("databases", [])) if dbs_ok else 0,
            "autoscale_replicas": int(as_status.get("replicas_running", 0)) if as_ok else 0,
        },
        "raw": {
            "buckets": buckets_data if buckets_ok else {"error": buckets_data.get("error")},
            "namespaces": ns_data if namespaces_ok else {"error": ns_data.get("error")},
            "databases": dbs_data if dbs_ok else {"error": dbs_data.get("error")},
            "autoscale_status": as_status if as_ok else {"error": as_status.get("error")},
        },
    }
