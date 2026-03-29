import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import docker
import requests
import urllib3
from flask import Flask, jsonify

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

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
        "notes": "This monitoring dashboard",
    },
    {
        "service": "PostgreSQL",
        "container": "n8n_postgres",
        "urls": [],
        "notes": "n8n database",
    },
]

ERROR_REGEX = re.compile(r"\b(error|exception|fatal|panic|failed|traceback)\b", re.IGNORECASE)
LOG_WINDOW_SECONDS = 300
LOG_TAIL_LINES = 300


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
        # Containers cannot resolve your host DNS. Probe through Traefik with Host header.
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


def get_container_metrics(client, container):
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

    stats = {}
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

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "service_count": len(services),
        "url_count": len(all_urls),
        "healthy_urls": sum(1 for u in all_urls if u.get("ok")),
        "services": services,
        "containers": sorted(containers, key=lambda c: c["name"]),
        "docker_overview": docker_overview,
    }


@app.get("/api/overview")
def api_overview():
    return jsonify(collect_overview())


@app.get("/")
def home():
    return """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Local Ecosystem Dashboard</title>
    <style>
      :root { color-scheme: dark; }
      body { font-family: Arial, sans-serif; margin: 0; background:#0f172a; color:#e2e8f0; }
      .wrap { max-width: 1250px; margin: 0 auto; padding: 20px; }
      h1 { margin: 0 0 8px; font-size: 24px; }
      .muted { color:#94a3b8; font-size: 14px; }
      .grid { display:grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; margin-top: 16px; }
      .grid-mini { display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; margin-top: 10px; }
      .card { border:1px solid #334155; border-radius:10px; padding:14px; background:#111827; }
      .row { display:flex; justify-content:space-between; gap:8px; margin:6px 0; }
      .pill { border-radius:999px; padding:2px 8px; font-size:12px; }
      .ok { background:#14532d; color:#bbf7d0; }
      .bad { background:#7f1d1d; color:#fecaca; }
      .warn { background:#78350f; color:#fde68a; }
      a { color:#93c5fd; text-decoration:none; }
      table { width:100%; border-collapse:collapse; margin-top:12px; font-size: 13px; }
      th, td { text-align:left; border-bottom:1px solid #334155; padding:8px 4px; vertical-align: top; }
      th { color:#93c5fd; font-weight:600; }
      code { color:#cbd5e1; }
      .toolbar { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin:8px 0 10px; }
      select, button { background:#0b1222; color:#e2e8f0; border:1px solid #334155; border-radius:8px; padding:6px 10px; }
      button { cursor:pointer; }
      .small { font-size:12px; color:#94a3b8; }
      .errs { margin-top:8px; padding:8px; background:#1f2937; border-radius:8px; font-size:12px; color:#fecaca; white-space:pre-wrap; }
    </style>
  </head>
  <body>
    <div class="wrap">
      <h1>Local Ecosystem Dashboard</h1>
      <div id="summary" class="muted">Loading...</div>
      <div class="toolbar">
        <label for="refreshRate">Auto refresh:</label>
        <select id="refreshRate">
          <option value="5000">5s</option>
          <option value="10000" selected>10s</option>
          <option value="30000">30s</option>
          <option value="60000">60s</option>
          <option value="0">Manual</option>
        </select>
        <button id="refreshNow">Refresh now</button>
        <span id="nextRefresh" class="small"></span>
      </div>
      <div id="dockerOverview" class="grid-mini"></div>
      <div id="services" class="grid"></div>
      <h2>All Docker Containers</h2>
      <table>
        <thead><tr><th>Name</th><th>Image</th><th>Status</th><th>Restart</th><th>Ports</th></tr></thead>
        <tbody id="containers"></tbody>
      </table>
    </div>
    <script>
      let refreshTimer = null;
      let tickTimer = null;
      let nextRefreshEpoch = null;

      function badge(ok, txt) {
        if (ok === true) return `<span class="pill ok">${txt}</span>`;
        if (ok === false) return `<span class="pill bad">${txt}</span>`;
        return `<span class="pill warn">${txt}</span>`;
      }

      function formatBytes(bytes) {
        if (!bytes || bytes <= 0) return "0 B";
        const units = ["B", "KB", "MB", "GB", "TB"];
        let size = bytes;
        let idx = 0;
        while (size >= 1024 && idx < units.length - 1) {
          size /= 1024;
          idx++;
        }
        return `${size.toFixed(size >= 10 ? 1 : 2)} ${units[idx]}`;
      }

      function fmtPorts(ports) {
        const entries = Object.entries(ports || {});
        if (entries.length === 0) return "-";
        return entries.map(([k, v]) => `${k} => ${JSON.stringify(v)}`).join("<br/>");
      }

      function renderDockerOverview(d) {
        if (!d || !d.docker_available) {
          document.getElementById("dockerOverview").innerHTML = `<div class="card">Docker API unavailable</div>`;
          return;
        }
        const host = d.host || {};
        const counts = d.counts || {};
        const disk = d.disk || {};
        document.getElementById("dockerOverview").innerHTML = `
          <div class="card">
            <strong>Docker Host</strong>
            <div class="row"><span>OS</span><span>${host.operating_system || "-"}</span></div>
            <div class="row"><span>Kernel</span><span>${host.kernel_version || "-"}</span></div>
            <div class="row"><span>CPUs</span><span>${host.cpus ?? "-"}</span></div>
            <div class="row"><span>Memory</span><span>${formatBytes(host.memory_total || 0)}</span></div>
          </div>
          <div class="card">
            <strong>Docker Runtime</strong>
            <div class="row"><span>Version</span><span>${host.server_version || "-"}</span></div>
            <div class="row"><span>Running</span><span>${counts.containers_running ?? 0}</span></div>
            <div class="row"><span>Paused</span><span>${counts.containers_paused ?? 0}</span></div>
            <div class="row"><span>Stopped</span><span>${counts.containers_stopped ?? 0}</span></div>
          </div>
          <div class="card">
            <strong>Docker Disk Usage</strong>
            <div class="row"><span>Images</span><span>${formatBytes(disk.images || 0)}</span></div>
            <div class="row"><span>Containers RW</span><span>${formatBytes(disk.container_rw || 0)}</span></div>
            <div class="row"><span>Volumes</span><span>${formatBytes(disk.volumes || 0)}</span></div>
            <div class="row"><span>Total tracked</span><span>${formatBytes(disk.total_tracked || 0)}</span></div>
          </div>
        `;
      }

      function scheduleRefresh() {
        const ms = Number(document.getElementById("refreshRate").value || "0");
        if (refreshTimer) clearInterval(refreshTimer);
        if (tickTimer) clearInterval(tickTimer);

        if (ms > 0) {
          nextRefreshEpoch = Date.now() + ms;
          refreshTimer = setInterval(() => {
            load();
            nextRefreshEpoch = Date.now() + ms;
          }, ms);
          tickTimer = setInterval(() => {
            const left = Math.max(0, Math.round((nextRefreshEpoch - Date.now()) / 1000));
            document.getElementById("nextRefresh").textContent = `Next refresh in ${left}s`;
          }, 250);
        } else {
          document.getElementById("nextRefresh").textContent = "Manual refresh only";
        }
      }

      async function load() {
        const res = await fetch("/api/overview");
        const data = await res.json();
        document.getElementById("summary").innerHTML =
          `Updated: ${new Date(data.generated_at).toLocaleString()} | ` +
          `Services: ${data.service_count} | URLs healthy: ${data.healthy_urls}/${data.url_count}`;
        renderDockerOverview(data.docker_overview);

        document.getElementById("services").innerHTML = data.services.map(s => {
          const running = s.container_info.status === "running";
          const checks = s.url_checks.length
            ? s.url_checks.map(c => `<div class="row"><a href="${c.url}" target="_blank">${c.url}</a>${badge(c.ok, c.ok ? "OK" : (c.status_code || "ERR"))}</div>`).join("")
            : `<div class="muted">No HTTP endpoint</div>`;
          const m = s.metrics || {};
          const l = s.logs || {};
          const hasLogErrors = (l.error_lines || 0) > 0;
          const errorSamples = (l.last_errors || []).slice(-2).map(e => e.length > 140 ? `${e.slice(0, 140)}...` : e).join("\\n");
          return `<div class="card">
            <div class="row"><strong>${s.service}</strong>${badge(running, s.container_info.status)}</div>
            <div class="muted">${s.notes}</div>
            <div class="row"><span>Container</span><code>${s.container}</code></div>
            <div class="row"><span>Networks</span><code>${(s.container_info.networks || []).join(", ") || "-"}</code></div>
            <div class="row"><span>CPU</span><span>${m.cpu_percent || 0}%</span></div>
            <div class="row"><span>Memory</span><span>${formatBytes(m.memory_usage || 0)} / ${formatBytes(m.memory_limit || 0)} (${m.memory_percent || 0}%)</span></div>
            <div class="row"><span>Network I/O</span><span>RX ${formatBytes(m.network_rx || 0)} | TX ${formatBytes(m.network_tx || 0)}</span></div>
            <div class="row"><span>Block I/O</span><span>R ${formatBytes(m.blk_read || 0)} | W ${formatBytes(m.blk_write || 0)}</span></div>
            <div class="row"><span>Container disk</span><span>RW ${formatBytes(m.size_rw || 0)} | FS ${formatBytes(m.size_root_fs || 0)}</span></div>
            <div class="row"><span>Errors (${Math.round((l.window_seconds || 300)/60)}m)</span><span>${badge(!hasLogErrors, `${l.error_lines || 0} (${l.error_rate_per_min || 0}/min)`)}</span></div>
            <div style="margin-top:8px">${checks}</div>
            ${hasLogErrors && errorSamples ? `<div class="errs">${errorSamples}</div>` : ``}
          </div>`;
        }).join("");

        document.getElementById("containers").innerHTML = data.containers.map(c => `
          <tr>
            <td><code>${c.name}</code></td>
            <td>${c.image}</td>
            <td>${c.status}</td>
            <td>${c.restart_count || 0}</td>
            <td>${fmtPorts(c.ports)}</td>
          </tr>
        `).join("");
      }

      document.getElementById("refreshNow").addEventListener("click", load);
      document.getElementById("refreshRate").addEventListener("change", scheduleRefresh);
      load();
      scheduleRefresh();
    </script>
  </body>
</html>
"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("DASHBOARD_PORT", "8090")))
