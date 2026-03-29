let refreshTimer = null;
let tickTimer = null;
let nextRefreshEpoch = null;
let charts = { line: null, bar: null, pie: null };
let activeTab = "overviewTab";
const trendHistory = { labels: [], cpu: [], memory: [], errors: [] };
const MAX_POINTS = 25;

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
    idx += 1;
  }
  return `${size.toFixed(size >= 10 ? 1 : 2)} ${units[idx]}`;
}

function fmtPorts(ports) {
  const entries = Object.entries(ports || {});
  if (entries.length === 0) return "-";
  return entries.map(([k, v]) => `${k} => ${JSON.stringify(v)}`).join("<br/>");
}

function activateTab(tabId) {
  activeTab = tabId;
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tabId);
  });
  document.querySelectorAll(".tab-content").forEach((tab) => {
    tab.classList.toggle("active", tab.id === tabId);
  });
  if (tabId === "logsTab") {
    loadLogs();
  }
}

function initTabs() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => activateTab(btn.dataset.tab));
  });
}

function renderSystemStatus(s) {
  if (!s) {
    document.getElementById("systemStatus").innerHTML = "<div class='card'>System status unavailable</div>";
    return;
  }

  const levelClass = s.level === "healthy" ? "ok-text" : "bad-text";
  const alerts = (s.alerts || []).length
    ? `<ul class="alert-list">${s.alerts.map((a) => `<li>${a}</li>`).join("")}</ul>`
    : `<div class="small ok-text">No critical alerts.</div>`;

  document.getElementById("systemStatus").innerHTML = `
    <div class="card">
      <strong>Operational Health</strong>
      <div class="row"><span>Status</span><span class="${levelClass}">${(s.level || "unknown").toUpperCase()}</span></div>
      <div class="row"><span>Docker API</span><span>${s.docker_available ? "reachable" : "unreachable"}</span></div>
      <div class="row"><span>Services</span><span>${s.services_running}/${s.services_total} running</span></div>
      <div class="row"><span>URLs</span><span>${s.healthy_urls}/${s.total_urls} healthy</span></div>
      <div class="row"><span>Paused / Missing</span><span>${s.services_paused} / ${s.services_missing}</span></div>
      ${alerts}
    </div>
    <div class="card">
      <strong>Runtime Summary</strong>
      <div class="row"><span>Total CPU</span><span>${s.aggregate_cpu_percent || 0}%</span></div>
      <div class="row"><span>Total Memory</span><span>${formatBytes(s.aggregate_memory_usage || 0)}</span></div>
      <div class="row"><span>Memory Cap</span><span>${formatBytes(s.aggregate_memory_limit || 0)}</span></div>
      <div class="row"><span>Memory %</span><span>${s.aggregate_memory_percent || 0}%</span></div>
      <div class="row"><span>Error lines (5m)</span><span>${s.total_error_lines || 0}</span></div>
      <div class="row"><span>Error rate</span><span>${s.total_error_rate_per_min || 0}/min</span></div>
    </div>
  `;
}

function renderDockerOverview(d) {
  if (!d || !d.docker_available) {
    document.getElementById("dockerOverview").innerHTML = "<div class='card'>Docker API unavailable</div>";
    return;
  }

  const host = d.host || {};
  const counts = d.counts || {};
  const disk = d.disk || {};

  document.getElementById("dockerOverview").innerHTML = `
    <div class="card">
      <strong>Host</strong>
      <div class="row"><span>OS</span><span>${host.operating_system || "-"}</span></div>
      <div class="row"><span>Kernel</span><span>${host.kernel_version || "-"}</span></div>
      <div class="row"><span>CPUs</span><span>${host.cpus ?? "-"}</span></div>
      <div class="row"><span>Memory</span><span>${formatBytes(host.memory_total || 0)}</span></div>
    </div>
    <div class="card">
      <strong>Runtime</strong>
      <div class="row"><span>Docker</span><span>${host.server_version || "-"}</span></div>
      <div class="row"><span>Running</span><span>${counts.containers_running ?? 0}</span></div>
      <div class="row"><span>Paused</span><span>${counts.containers_paused ?? 0}</span></div>
      <div class="row"><span>Stopped</span><span>${counts.containers_stopped ?? 0}</span></div>
    </div>
    <div class="card">
      <strong>Capacity</strong>
      <div class="row"><span>Images</span><span>${formatBytes(disk.images || 0)}</span></div>
      <div class="row"><span>Containers RW</span><span>${formatBytes(disk.container_rw || 0)}</span></div>
      <div class="row"><span>Volumes</span><span>${formatBytes(disk.volumes || 0)}</span></div>
      <div class="row"><span>Total tracked</span><span>${formatBytes(disk.total_tracked || 0)}</span></div>
    </div>
  `;
}

function ensureCharts() {
  if (typeof Chart === "undefined") return false;
  if (charts.line && charts.bar && charts.pie) return true;

  charts.line = new Chart(document.getElementById("lineChart"), {
    type: "line",
    data: {
      labels: [],
      datasets: [
        { label: "CPU %", data: [], borderColor: "#60a5fa", backgroundColor: "#60a5fa33", tension: 0.25, fill: true },
        { label: "Memory %", data: [], borderColor: "#34d399", backgroundColor: "#34d39933", tension: 0.25, fill: true },
        { label: "Errors/min", data: [], borderColor: "#f87171", backgroundColor: "#f8717133", tension: 0.25, fill: true }
      ]
    },
    options: { responsive: true, maintainAspectRatio: false, animation: false }
  });

  charts.bar = new Chart(document.getElementById("barChart"), {
    type: "bar",
    data: {
      labels: [],
      datasets: [
        { label: "CPU %", data: [], backgroundColor: "#60a5fa" },
        { label: "Memory %", data: [], backgroundColor: "#34d399" }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: { y: { beginAtZero: true, max: 100 } }
    }
  });

  charts.pie = new Chart(document.getElementById("pieChart"), {
    type: "pie",
    data: {
      labels: ["Running", "Paused", "Other"],
      datasets: [{ data: [0, 0, 0], backgroundColor: ["#22c55e", "#f59e0b", "#ef4444"] }]
    },
    options: { responsive: true, maintainAspectRatio: false, animation: false }
  });
  return true;
}

function updateCharts(data) {
  if (!ensureCharts()) return;
  const s = data.system_status || {};
  const services = data.services || [];

  const stamp = new Date(data.generated_at).toLocaleTimeString();
  trendHistory.labels.push(stamp);
  trendHistory.cpu.push(Number(s.aggregate_cpu_percent || 0));
  trendHistory.memory.push(Number(s.aggregate_memory_percent || 0));
  trendHistory.errors.push(Number(s.total_error_rate_per_min || 0));
  if (trendHistory.labels.length > MAX_POINTS) {
    trendHistory.labels.shift();
    trendHistory.cpu.shift();
    trendHistory.memory.shift();
    trendHistory.errors.shift();
  }

  charts.line.data.labels = trendHistory.labels;
  charts.line.data.datasets[0].data = trendHistory.cpu;
  charts.line.data.datasets[1].data = trendHistory.memory;
  charts.line.data.datasets[2].data = trendHistory.errors;
  charts.line.update();

  charts.bar.data.labels = services.map((x) => x.service);
  charts.bar.data.datasets[0].data = services.map((x) => Number(x.metrics?.cpu_percent || 0));
  charts.bar.data.datasets[1].data = services.map((x) => Number(x.metrics?.memory_percent || 0));
  charts.bar.update();

  let running = 0;
  let paused = 0;
  let other = 0;
  services.forEach((svc) => {
    const st = svc.container_info?.status || "unknown";
    if (st === "running") running += 1;
    else if (st === "paused") paused += 1;
    else other += 1;
  });
  charts.pie.data.datasets[0].data = [running, paused, other];
  charts.pie.update();
}

function renderServices(data) {
  document.getElementById("services").innerHTML = data.services.map((s) => {
    const running = s.container_info.status === "running";
    const checks = s.url_checks.length
      ? s.url_checks.map((c) => `<div class="row"><a href="${c.url}" target="_blank">${c.url}</a>${badge(c.ok, c.ok ? "OK" : (c.status_code || "ERR"))}</div>`).join("")
      : "<div class='muted'>No HTTP endpoint</div>";

    const m = s.metrics || {};
    const l = s.logs || {};
    const hasLogErrors = (l.error_lines || 0) > 0;
    const errorSamples = (l.last_errors || []).slice(-2).map((e) => (e.length > 140 ? `${e.slice(0, 140)}...` : e)).join("\n");

    return `
      <div class="card">
        <div class="row"><strong>${s.service}</strong>${badge(running, s.container_info.status)}</div>
        <div class="muted">${s.notes}</div>
        <div class="row"><span>Container</span><code>${s.container}</code></div>
        <div class="row"><span>Networks</span><code>${(s.container_info.networks || []).join(", ") || "-"}</code></div>
        <div class="row"><span>CPU</span><span>${m.cpu_percent || 0}%</span></div>
        <div class="row"><span>Memory</span><span>${formatBytes(m.memory_usage || 0)} / ${formatBytes(m.memory_limit || 0)} (${m.memory_percent || 0}%)</span></div>
        <div class="row"><span>Network I/O</span><span>RX ${formatBytes(m.network_rx || 0)} | TX ${formatBytes(m.network_tx || 0)}</span></div>
        <div class="row"><span>Block I/O</span><span>R ${formatBytes(m.blk_read || 0)} | W ${formatBytes(m.blk_write || 0)}</span></div>
        <div class="row"><span>Container disk</span><span>RW ${formatBytes(m.size_rw || 0)} | FS ${formatBytes(m.size_root_fs || 0)}</span></div>
        <div class="row"><span>Errors (${Math.round((l.window_seconds || 300) / 60)}m)</span><span>${badge(!hasLogErrors, `${l.error_lines || 0} (${l.error_rate_per_min || 0}/min)`)}</span></div>
        <div style="margin-top:8px">${checks}</div>
        ${hasLogErrors && errorSamples ? `<div class="errs">${errorSamples}</div>` : ""}
      </div>
    `;
  }).join("");
}

function renderContainers(data) {
  document.getElementById("containers").innerHTML = data.containers.map((c) => `
      <tr>
        <td><code>${c.name}</code></td>
        <td>${c.image}</td>
        <td>${c.status}</td>
        <td>${c.restart_count || 0}</td>
        <td>${fmtPorts(c.ports)}</td>
      </tr>
    `).join("");
}

async function initLogsPanel() {
  const res = await fetch("/api/services");
  const data = await res.json();
  const sel = document.getElementById("logService");
  sel.innerHTML = (data.services || [])
    .map((svc) => `<option value="${svc.container}">${svc.service} (${svc.container})</option>`)
    .join("");
  sel.value = "traefik";
}

function getLogFilters() {
  return {
    service: document.getElementById("logService").value || "traefik",
    level: document.getElementById("logLevel").value || "all",
    since: document.getElementById("logSince").value || "1800",
    tail: document.getElementById("logTail").value || "500",
    search: document.getElementById("logSearch").value || ""
  };
}

function renderLogRows(entries) {
  const tbody = document.getElementById("logsBody");
  if (!entries || entries.length === 0) {
    tbody.innerHTML = "<tr><td colspan='3' class='muted'>No logs found for selected filters.</td></tr>";
    return;
  }

  tbody.innerHTML = entries.map((entry) => `
      <tr>
        <td>${entry.timestamp || "-"}</td>
        <td><span class="level-${entry.level || "other"}">${entry.level || "other"}</span></td>
        <td>${(entry.message || "").replaceAll("<", "&lt;").replaceAll(">", "&gt;")}</td>
      </tr>
    `).join("");
}

async function loadLogs() {
  const filters = getLogFilters();
  const params = new URLSearchParams(filters);
  const res = await fetch(`/api/logs?${params.toString()}`);
  const data = await res.json();
  const c = data.level_counts || {};
  document.getElementById("logSummary").textContent =
    `Scanned ${data.total_scanned || 0}, showing ${data.returned || 0} | ` +
    `error:${c.error || 0} warn:${c.warn || 0} info:${c.info || 0} other:${c.other || 0}`;
  renderLogRows(data.entries || []);
}

function initLogEvents() {
  document.getElementById("logApply").addEventListener("click", loadLogs);
  document.getElementById("logService").addEventListener("change", loadLogs);
  document.getElementById("logLevel").addEventListener("change", loadLogs);
  document.getElementById("logSince").addEventListener("change", loadLogs);
  document.getElementById("logTail").addEventListener("change", loadLogs);
  document.getElementById("logSearch").addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadLogs();
  });
}

function scheduleRefresh() {
  const ms = Number(document.getElementById("refreshRate").value || "0");
  if (refreshTimer) clearInterval(refreshTimer);
  if (tickTimer) clearInterval(tickTimer);

  if (ms > 0) {
    nextRefreshEpoch = Date.now() + ms;
    refreshTimer = setInterval(() => {
      if (activeTab === "overviewTab") {
        loadOverview();
      } else {
        loadLogs();
      }
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

async function loadOverview() {
  const res = await fetch("/api/overview");
  const data = await res.json();

  document.getElementById("summary").innerHTML =
    `Updated: ${new Date(data.generated_at).toLocaleString()} | ` +
    `Services: ${data.service_count} | URLs healthy: ${data.healthy_urls}/${data.url_count} | ` +
    `System: ${(data.system_status?.level || "unknown").toUpperCase()}`;

  renderSystemStatus(data.system_status);
  renderDockerOverview(data.docker_overview);
  updateCharts(data);
  renderServices(data);
  renderContainers(data);
}

async function bootstrap() {
  initTabs();
  await initLogsPanel();
  initLogEvents();

  document.getElementById("refreshNow").addEventListener("click", () => {
    if (activeTab === "overviewTab") {
      loadOverview();
    } else {
      loadLogs();
    }
  });
  document.getElementById("refreshRate").addEventListener("change", scheduleRefresh);

  await loadOverview();
  scheduleRefresh();
}

bootstrap();
