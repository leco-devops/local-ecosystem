import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import docker
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SERVICE_MAP = [
    {
        "service": "Traefik",
        "container": "traefik",
        "urls": ["http://traefik.lh", "https://traefik.lh"],
        "notes": "Reverse proxy and dashboard",
    },
    {
        "service": "Open WebUI",
        "container": "open-webui",
        "urls": ["http://ai.lh", "https://ai.lh"],
        "notes": "AI chat user interface",
    },
    {
        "service": "n8n",
        "container": "n8n",
        "urls": ["http://n8n.lh", "https://n8n.lh"],
        "notes": "Workflow automation",
    },
    {
        "service": "Ollama",
        "container": "ollama",
        "urls": ["http://ollama.lh", "https://ollama.lh"],
        "notes": "LLM runtime",
    },
    {
        "service": "Service Dashboard",
        "container": "service-dashboard",
        "urls": ["http://localhost.lh"],
        "notes": "Ops monitoring dashboard",
    },
    {
        "service": "PostgreSQL",
        "container": "n8n_postgres",
        "urls": [],
        "notes": "n8n database",
    },
    {
        "service": "R2 (Cloudflare local)",
        "container": "r2-adapter",
        "urls": ["http://r2.lh"],
        "notes": "S3-compatible API (MinIO backend)",
        "credentials": [
            "S3/MinIO (dev): access key minioadmin · secret minioadmin",
        ],
        "management_links": [
            {"label": "Management & bucket explorer", "url": "http://r2.lh/panel"},
            {"label": "Health JSON", "url": "http://r2.lh/health"},
            {"label": "MinIO console (same credentials)", "url": "http://minio-console.lh"},
        ],
    },
    {
        "service": "KV (Cloudflare local)",
        "container": "kv-adapter",
        "urls": ["http://kv.lh"],
        "notes": "KV-style API on Valkey",
        "credentials": [
            "Default Valkey: no password (redis://valkey:6379 inside compose).",
        ],
        "management_links": [
            {"label": "Management & namespace explorer", "url": "http://kv.lh/panel"},
            {"label": "Health JSON", "url": "http://kv.lh/health"},
        ],
    },
    {
        "service": "D1 (Cloudflare local)",
        "container": "d1-adapter",
        "urls": ["http://d1.lh"],
        "notes": "SQLite D1-style API",
        "credentials": [
            "No API auth — SQLite files under adapter volume (local dev only).",
        ],
        "management_links": [
            {"label": "Management & SQL explorer", "url": "http://d1.lh/panel"},
            {"label": "Health JSON", "url": "http://d1.lh/health"},
        ],
    },
    {
        "service": "Autoscaler",
        "container": "autoscaler",
        "urls": ["http://autoscale.lh"],
        "notes": "Docker replica scaler demo API",
        "credentials": [
            "No API auth in local stack.",
        ],
        "management_links": [
            {"label": "Management panel (live status)", "url": "http://autoscale.lh/panel"},
            {"label": "Status JSON", "url": "http://autoscale.lh/status"},
        ],
    },
    {
        "service": "MinIO Console",
        "container": "minio",
        "urls": ["http://minio-console.lh"],
        "notes": "Object store web UI",
        "credentials": [
            "Console login: minioadmin / minioadmin (dev defaults from compose).",
        ],
        "management_links": [
            {"label": "Open console", "url": "http://minio-console.lh"},
        ],
    },
    {
        "service": "Workers (Miniflare)",
        "container": "workers-runtime",
        "urls": ["http://workers.lh"],
        "notes": "Local Cloudflare Workers runtime (Miniflare)",
        "credentials": [
            "No auth — fetch handler only. Root URL returns JSON.",
        ],
        "management_links": [
            {"label": "Info & troubleshooting", "url": "http://workers.lh/panel"},
            {"label": "Health JSON", "url": "http://workers.lh/health"},
        ],
    },
]

ERROR_REGEX = re.compile(r"\b(error|exception|fatal|panic|failed|traceback)\b", re.IGNORECASE)
WARN_REGEX = re.compile(r"\b(warn|warning|deprecated|timeout)\b", re.IGNORECASE)
INFO_REGEX = re.compile(r"\b(info|started|ready|listening|connected|ok)\b", re.IGNORECASE)
LOG_WINDOW_SECONDS = 300
LOG_TAIL_LINES = 300
CLOUDFLARE_ENDPOINTS = {
    "r2": "http://r2-adapter:8081",
    "kv": "http://kv-adapter:8082",
    "d1": "http://d1-adapter:8083",
    "autoscale": "http://autoscaler:8084",
    "workers": "http://workers-runtime:8787",
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
    if client is None:
        return None
    try:
        return client.containers.get(container_name)
    except Exception:
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


def check_url(url):
    probe_url, headers = get_probe_target(url)
    try:
        response = requests.get(
            probe_url,
            timeout=5,
            verify=False,
            allow_redirects=True,
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


def get_log_metrics(container):
    if container is None:
        return {
            "window_seconds": LOG_WINDOW_SECONDS,
            "total_lines": 0,
            "error_lines": 0,
            "error_rate_per_min": 0.0,
            "last_errors": [],
        }

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

    error_lines = [line for line in lines if ERROR_REGEX.search(line)]
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
        "aggregate_cpu_percent": round(aggregate_cpu_percent, 2),
        "aggregate_memory_usage": aggregate_memory_usage,
        "aggregate_memory_limit": aggregate_memory_limit,
        "aggregate_memory_percent": round(aggregate_memory_percent, 2),
        "alerts": alerts,
    }


def collect_reference_status():
    from reference_data import REFERENCE_CATEGORIES

    categories = []
    for cat in REFERENCE_CATEGORIES:
        items_out = []
        for item in cat["items"]:
            url_checks = [check_url(url) for url in item.get("urls", [])]
            items_out.append({**item, "url_checks": url_checks})
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
        checks = [check_url(url) for url in item["urls"]]
        all_urls.extend(checks)
        services.append(
            {
                "service": item["service"],
                "container": item["container"],
                "notes": item["notes"],
                "credentials": item.get("credentials") or [],
                "management_links": item.get("management_links") or [],
                "container_info": container_info,
                "metrics": get_container_metrics(client, container),
                "logs": get_log_metrics(container),
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
        from timeseries import append_snapshot

        append_snapshot(client, docker_overview)
    except Exception:
        pass

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
