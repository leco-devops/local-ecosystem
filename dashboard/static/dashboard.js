let refreshTimer = null;
let tickTimer = null;
let nextRefreshEpoch = null;
let charts = { line: null, bar: null, pie: null };
let metricsCharts = { cpu: null, ram: null, net: null, iops: null, temp: null };
let metricsTimer = null;
let activeTab = "overviewTab";
const trendHistory = { labels: [], cpu: [], memory: [], errors: [] };
const MAX_POINTS = 25;
const METRICS_MAX = 60;
let lastOverviewData = null;
let lastCloudflareData = null;
let lastMetricsData = null;

const LS_OVERVIEW_CACHE_KEY = "local_ecosystem_dashboard_overview_v1";
const LS_OVERVIEW_MAX_AGE_MS = 1000 * 60 * 60 * 48;
const LS_METRICS_CACHE_KEY = "local_ecosystem_dashboard_metrics_v1";
const LS_ACTIVE_TAB_KEY = "dashboard_active_tab";

function saveOverviewCache(data, cloudflareData, metricsData) {
  try {
    const payload = {
      savedAt: Date.now(),
      overview: data,
      cloudflare: cloudflareData,
      metrics: metricsData,
    };
    const s = JSON.stringify(payload);
    if (s.length > 4_500_000) return;
    localStorage.setItem(LS_OVERVIEW_CACHE_KEY, s);
  } catch (_) {
    /* quota or private mode */
  }
}

function hydrateOverviewFromCache() {
  try {
    const raw = localStorage.getItem(LS_OVERVIEW_CACHE_KEY);
    if (!raw) return false;
    const payload = JSON.parse(raw);
    if (!payload?.overview || Date.now() - (payload.savedAt || 0) > LS_OVERVIEW_MAX_AGE_MS) return false;
    lastOverviewData = payload.overview;
    lastCloudflareData = payload.cloudflare || {};
    lastMetricsData = payload.metrics || { points: [] };
    lastReferencePayload = payload.overview.reference || {};
    setCompactHeaderSummary(payload.overview);
    updateOverviewDashboard(payload.overview, payload.cloudflare || {}, payload.metrics || { points: [] });
    renderInfrastructurePanel(payload.overview, payload.cloudflare || {}, { skipTrendCharts: true });
    return true;
  } catch (_) {
    return false;
  }
}

function saveMetricsCache(metricsData) {
  try {
    const payload = { savedAt: Date.now(), metrics: metricsData };
    const s = JSON.stringify(payload);
    if (s.length > 4_500_000) return;
    localStorage.setItem(LS_METRICS_CACHE_KEY, s);
  } catch (_) {
    /* ignore */
  }
}

function hydrateMetricsFromCache() {
  try {
    const raw = localStorage.getItem(LS_METRICS_CACHE_KEY);
    if (!raw) return null;
    const payload = JSON.parse(raw);
    if (!payload?.metrics?.points || Date.now() - (payload.savedAt || 0) > LS_OVERVIEW_MAX_AGE_MS) return null;
    return payload.metrics;
  } catch (_) {
    return null;
  }
}
const overviewCharts = { cpu: null, ram: null, url: null, cf: null, engine: null, svcBar: null };
const OVERVIEW_METRICS_LIMIT = 45;

/** Chart line colours — diversified hues (temp is orange so it never blends with CPU lines). */
const THEME = {
  docker: "#22d3ee",
  system: "#e879f9",
  temp: "#fb923c",
  block: "#eab308",
  grid: "rgba(148, 163, 184, 0.2)",
};

const LINE_STYLE = { borderWidth: 2.5, pointRadius: 0, pointHoverRadius: 4 };

function tempAxisBounds(sTemp) {
  const nums = sTemp.filter((x) => x != null && x !== "" && !Number.isNaN(Number(x))).map((x) => Number(x));
  if (!nums.length) return { min: 0, max: 90 };
  const lo = Math.min(...nums);
  const hi = Math.max(...nums);
  return { min: Math.max(0, lo - 8), max: Math.min(115, hi + 12) };
}

function serviceBrandUi() {
  if (typeof SERVICE_BRAND_UI !== "undefined") return SERVICE_BRAND_UI;
  return {
    getBrandForControlTarget() {
      return "default";
    },
    getBrandForManagedService() {
      return "default";
    },
    emojiFor() {
      return "📦";
    },
    iconHtml() {
      return '<span class="svc-svg-icon"></span>';
    },
    partitionControlActions(actions) {
      const d = ["remove", "reset"];
      const danger = (actions || []).filter((a) => d.includes(a));
      const safe = (actions || []).filter((a) => !d.includes(a));
      return { safe, danger };
    },
    actionButtonClasses(action) {
      const a = (action || "").toLowerCase();
      const base = "ctrl-act";
      if (a === "remove" || a === "reset") return `${base} danger ctrl-act--destructive`;
      if (a === "backup" || a === "start" || a === "unpause") return `${base} ctrl-act--safe`;
      if (a === "stop" || a === "pause") return `${base} ctrl-act--caution`;
      return `${base} ctrl-act--ops`;
    },
  };
}

function maxInSeriesList(series) {
  let m = 0;
  for (const arr of series) {
    for (const x of arr) {
      if (x != null && !Number.isNaN(Number(x))) m = Math.max(m, Number(x));
    }
  }
  return m;
}

/** Primary RAM line: % of host MemTotal (Docker-reported), else legacy single field. */
function dockerRamLineHost(pts) {
  return pts.map((p) => {
    const h = p.docker?.memory_percent_of_host;
    if (h != null) return Number(h);
    return p.docker?.memory_percent != null ? Number(p.docker.memory_percent) : null;
  });
}

/**
 * Second RAM line: host /proc MemAvailable-based % when mounted; otherwise Docker usage / Σ cgroup limits
 * (different denominator from host % — explains “50% vs 4%” on Docker Desktop).
 */
function ramChartSecondLine(pts) {
  const hasProc = pts.some((p) => p.system?.memory_percent != null);
  if (hasProc) return pts.map((p) => p.system?.memory_percent ?? null);
  return pts.map((p) => p.docker?.memory_percent_of_limits ?? p.docker?.memory_percent ?? null);
}

function ramSecondLineLabel(pts) {
  return pts.some((p) => p.system?.memory_percent != null)
    ? "System RAM (% used)"
    : "Docker / Σ cgroup limits %";
}

function computeRamYMax(pts) {
  const a = dockerRamLineHost(pts);
  const b = ramChartSecondLine(pts);
  const mx = maxInSeriesList([a, b]);
  if (mx <= 0) return 100;
  return Math.min(100, Math.max(10, Math.ceil(mx * 1.28)));
}

function computeCpuYMax(pts) {
  const dock = pts.map((p) => p.docker?.cpu_percent ?? null);
  const sys = pts.map((p) => p.system?.cpu_percent ?? null);
  const mx = maxInSeriesList([dock, sys]);
  if (mx <= 0) return 100;
  return Math.min(100, Math.max(6, Math.ceil(mx * 1.4)));
}

function seriesHasNumber(arr) {
  return arr.some((x) => x != null && x !== "" && !Number.isNaN(Number(x)));
}

function seriesHasPositive(arr) {
  return arr.some((x) => x != null && Number(x) > 0);
}

function badge(ok, txt, title) {
  const t = title ? ` title="${escapeHtml(title)}"` : "";
  if (ok === true) return `<span class="pill ok"${t}>${txt}</span>`;
  if (ok === false) return `<span class="pill bad"${t}>${txt}</span>`;
  return `<span class="pill warn"${t}>${txt}</span>`;
}

/** URL probe pill; * = OK via Docker network fallback when Traefik edge failed. */
function urlProbePill(c) {
  if (!c || c.ok !== true) {
    return badge(c?.ok, c?.status_code != null ? String(c.status_code) : "ERR", c?.error);
  }
  if (c.probe_via === "internal") {
    const edge = c.edge_status_code != null ? String(c.edge_status_code) : c.edge_error || "?";
    return badge(
      true,
      "OK*",
      `Service answered on the Docker network; Traefik edge probe was not OK (${edge}). Browser may still work via *.lh.`,
    );
  }
  return badge(true, "OK");
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

function escapeHtml(s) {
  return String(s || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

let lastReferencePayload = null;

function filterReferencePayload(ref, filterText) {
  const q = (filterText || "").trim().toLowerCase();
  if (!q || !ref?.categories) return ref;
  const categories = ref.categories
    .map((cat) => ({
      ...cat,
      items: (cat.items || []).filter((it) => {
        const hay = `${it.label} ${it.notes || ""} ${(it.urls || []).join(" ")}`.toLowerCase();
        return hay.includes(q);
      }),
    }))
    .filter((cat) => (cat.items || []).length > 0);
  return { ...ref, categories };
}

function buildEncyclopediaHtml(ref) {
  if (!ref?.categories?.length) {
    return "<div class='card'>No URLs match the filter.</div>";
  }
  return ref.categories
    .map((cat) => {
      const rows = (cat.items || [])
        .map((it) => {
          const checks = it.url_checks || [];
          const linkParts =
            checks.length > 0
              ? checks.map((chk) => {
                  const ok = chk.ok === true;
                  const st = ok ? "ok" : chk.ok === false ? "bad" : "warn";
                  const code = chk.status_code != null ? ` ${chk.status_code}` : "";
                  const err = chk.error ? ` title="${escapeHtml(chk.error)}"` : "";
                  return `<span class="url-check"><a href="${escapeHtml(chk.url)}" target="_blank" rel="noopener"${err}>${escapeHtml(
                    chk.url,
                  )}</a> <span class="pill ${st}">${ok ? "OK" : "ERR"}${code}</span></span>`;
                })
              : (it.urls || []).map(
                  (u) =>
                    `<span class="url-check"><a href="${escapeHtml(u)}" target="_blank" rel="noopener">${escapeHtml(u)}</a></span>`,
                );
          const copyBtns = (it.urls || [])
            .map(
              (u) =>
                `<button type="button" class="small-copy" data-copy-url="${escapeHtml(u)}">Copy</button>`,
            )
            .join("");
          return `
            <div class="url-row">
              <div class="url-row-info">
                <div class="label">${escapeHtml(it.label)}</div>
                <div class="notes">${escapeHtml(it.notes || "")}</div>
              </div>
              <div class="url-links">${linkParts.join(" ")}</div>
              <div class="url-actions">${copyBtns}</div>
            </div>`;
        })
        .join("");
      return `
        <div class="url-cat" data-ref-cat="${escapeHtml(cat.id)}">
          <h3>${escapeHtml(cat.title)}</h3>
          <div class="cat-desc">${escapeHtml(cat.description || "")}</div>
          <div class="url-rows">${rows}</div>
        </div>`;
    })
    .join("");
}

function renderUrlEncyclopedia(ref, mountId, filterText) {
  const mount = document.getElementById(mountId);
  if (!mount) return;
  if (!ref || !ref.categories) {
    mount.innerHTML = "<div class='card'>URL directory unavailable (rebuild dashboard or check API).</div>";
    return;
  }
  const filtered = filterText ? filterReferencePayload(ref, filterText) : ref;
  mount.innerHTML = buildEncyclopediaHtml(filtered);
}

function applyReferenceFilter() {
  if (!lastReferencePayload) return;
  const q = document.getElementById("referenceFilter")?.value || "";
  renderUrlEncyclopedia(lastReferencePayload, "urlEncyclopediaRef", q);
}

async function loadReferenceTab() {
  const mount = document.getElementById("urlEncyclopediaRef");
  if (mount) mount.innerHTML = "<div class='muted'>Loading…</div>";
  try {
    const res = await fetch("/api/reference");
    lastReferencePayload = await res.json();
    applyReferenceFilter();
  } catch (e) {
    if (mount) mount.innerHTML = `<div class='card'>${escapeHtml(String(e))}</div>`;
  }
}

async function loadDocsCatalog() {
  const sb = document.getElementById("docSidebar");
  const content = document.getElementById("docContent");
  if (!sb || !content) return;
  sb.innerHTML = "<div class='muted'>Loading catalog…</div>";
  try {
    const res = await fetch("/api/docs/catalog");
    const data = await res.json();
    const modules = data.modules || [];
    const byCat = {};
    modules.forEach((m) => {
      byCat[m.category] = byCat[m.category] || [];
      byCat[m.category].push(m);
    });
    sb.innerHTML = Object.keys(byCat)
      .sort()
      .map(
        (cat) => `
        <div class="doc-cat">
          <strong>${escapeHtml(cat)}</strong>
          ${byCat[cat]
            .map(
              (m) =>
                `<button type="button" class="doc-link${m.available ? "" : " muted"}" data-doc="${escapeHtml(
                  m.id,
                )}">${escapeHtml(m.title)}</button>`,
            )
            .join("")}
        </div>`,
      )
      .join("");
    sb.querySelectorAll("[data-doc]").forEach((b) => {
      b.addEventListener("click", () => loadDocContent(b.getAttribute("data-doc")));
    });
    const first = modules.find((m) => m.available) || modules[0];
    if (first) await loadDocContent(first.id);
    else content.innerHTML = "<p class='muted'>No documentation modules configured.</p>";
  } catch (e) {
    sb.innerHTML = "";
    content.innerHTML = `<p class='muted'>${escapeHtml(String(e))}</p>`;
  }
}

async function loadDocContent(id) {
  const content = document.getElementById("docContent");
  const toolbar = document.getElementById("docToolbar");
  if (!content || !id) return;
  content.innerHTML = "<p class='muted'>Loading…</p>";
  try {
    const res = await fetch(`/api/docs/content?id=${encodeURIComponent(id)}`);
    const data = await res.json();
    if (!data.ok) {
      content.innerHTML = `<p class='muted'>${escapeHtml(data.error || "Error")}</p>`;
      return;
    }
    const raw = data.markdown || "";
    let html;
    if (typeof marked !== "undefined" && marked.parse) {
      html = marked.parse(raw);
    } else {
      html = `<pre>${escapeHtml(raw)}</pre>`;
    }
    if (typeof DOMPurify !== "undefined") {
      html = DOMPurify.sanitize(html);
    }
    content.innerHTML = html;
    content.classList.add("prose");
    const syn = data.synthetic ? " (preview — mount /project for full file)" : "";
    toolbar.innerHTML = `
      <span><strong>${escapeHtml(data.title || "")}</strong>${escapeHtml(syn)}</span>
      <button type="button" id="docReloadBtn">Reload</button>`;
    document.getElementById("docReloadBtn")?.addEventListener("click", () => loadDocContent(id));
  } catch (e) {
    content.innerHTML = `<p class='muted'>${escapeHtml(String(e))}</p>`;
  }
}

function renderDevelopCards() {
  const el = document.getElementById("developCards");
  if (!el || el.dataset.rendered === "1") return;
  el.dataset.rendered = "1";
  el.innerHTML = `
    <div class="card dev-card">
      <strong>Rebuild &amp; run</strong>
      <ul>
        <li><code>./ai-stack/ai-stack.sh restart dashboard</code> — after editing <code>dashboard/</code></li>
        <li><code>./ai-stack/services/cloudflare-local.sh recreate workers-runtime</code> — after editing Workers</li>
        <li><code>./ai-stack/ai-stack.sh repair-network</code> — if a container lost <code>lh-network</code></li>
      </ul>
    </div>
    <div class="card dev-card">
      <strong>New <code>*.lh</code> route</strong>
      <ul>
        <li>Add router + service in <code>traefik/dynamic.yml</code></li>
        <li>Put the container on <code>lh-network</code></li>
        <li>Optional: extend <code>monitor.py</code> <code>SERVICE_MAP</code> for probes</li>
      </ul>
    </div>
    <div class="card dev-card">
      <strong>Dashboard APIs</strong>
      <ul>
        <li><code>GET /api/reference</code> — URL encyclopedia + probes</li>
        <li><code>GET /api/docs/catalog</code> — doc modules</li>
        <li><code>GET /api/docs/content?id=…</code> — Markdown body</li>
        <li><code>POST /api/control</code> — lifecycle (needs Docker + <code>/project</code>)</li>
        <li><code>GET /api/ollama/models</code> — pinned + installed + in-memory status</li>
        <li><code>POST /api/ollama/models/action</code> — pull / pull_all / delete / unload (control token)</li>
      </ul>
    </div>
    <div class="card dev-card">
      <strong>Long-form docs</strong>
      <p class="muted small" style="margin:8px 0 0">Open the <strong>Docs</strong> tab for architecture, user manual, implementation guide, and development playbook (from the repo).</p>
    </div>`;
}

function setCompactHeaderSummary(data) {
  const el = document.getElementById("summary");
  if (!el) return;
  const ref = data.reference || {};
  const s = data.system_status || {};
  const lvl = s.level || "unknown";
  const dotClass = lvl === "healthy" ? "ok" : lvl === "critical" ? "critical" : "warn";
  const time = data.generated_at ? new Date(data.generated_at).toLocaleTimeString() : "—";
  el.innerHTML = `
    <span class="status-dot ${dotClass}" title="Health"></span>
    <span>${time}</span>
    <span class="header-sep">·</span>
    <span>${data.service_count ?? "—"} svc</span>
    <span class="header-sep">·</span>
    <span>URLs ${data.healthy_urls ?? "—"}/${data.url_count ?? "—"}</span>
    <span class="header-sep">·</span>
    <span>dir ${ref.healthy_urls ?? "—"}/${ref.total_urls ?? "—"}</span>
    <span class="header-sep">·</span>
    <span class="hl-${lvl}">${lvl.toUpperCase()}</span>`;
}

function destroyOverviewChartsIfStale() {
  const c = overviewCharts.cpu;
  if (!c) return;
  if (c.data.datasets.length >= 3) return;
  try {
    Object.values(overviewCharts).forEach((ch) => ch?.destroy?.());
  } catch (_) {
    /* ignore */
  }
  overviewCharts.cpu = null;
  overviewCharts.ram = null;
  overviewCharts.url = null;
  overviewCharts.cf = null;
  overviewCharts.engine = null;
  overviewCharts.svcBar = null;
}

function ensureOverviewCharts() {
  if (typeof Chart === "undefined") return false;
  destroyOverviewChartsIfStale();
  const cpuEl = document.getElementById("overviewChartCpu");
  const ramEl = document.getElementById("overviewChartRam");
  const urlEl = document.getElementById("overviewChartUrl");
  const cfEl = document.getElementById("overviewChartCf");
  const engEl = document.getElementById("overviewChartEngine");
  const barEl = document.getElementById("overviewChartServicesBar");
  if (!cpuEl || !ramEl || !urlEl || !cfEl || !engEl || !barEl) return false;

  const dockerLine = { borderColor: THEME.docker, tension: 0.2, spanGaps: true, ...LINE_STYLE };
  const sysLine = { borderColor: THEME.system, tension: 0.2, spanGaps: true, ...LINE_STYLE };
  const overviewLegend = {
    position: "bottom",
    labels: { boxWidth: 10, font: { size: 11, color: "#faf5ff" } },
  };

  if (!overviewCharts.cpu) {
    overviewCharts.cpu = new Chart(cpuEl, {
      type: "line",
      data: {
        labels: [],
        datasets: [
          { label: "Docker CPU (≈% vCPUs)", data: [], yAxisID: "y", ...dockerLine },
          { label: "System CPU (/proc)", data: [], yAxisID: "y", ...sysLine },
          {
            label: "Host temp (°C)",
            data: [],
            yAxisID: "y1",
            borderColor: THEME.temp,
            tension: 0.2,
            spanGaps: true,
            ...LINE_STYLE,
            borderWidth: 3,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: { ticks: { color: "#e9d5ff", font: { size: 10 } }, grid: { color: THEME.grid } },
          y: {
            beginAtZero: true,
            max: 100,
            grid: { color: THEME.grid },
            ticks: { color: "#bae6fd", font: { size: 10 } },
          },
          y1: {
            type: "linear",
            position: "right",
            display: true,
            beginAtZero: true,
            min: 0,
            max: 90,
            grid: { display: false },
            ticks: { color: THEME.temp, font: { size: 10 }, callback: (v) => `${v}°` },
          },
        },
        plugins: { legend: overviewLegend },
      },
    });
  }
  if (!overviewCharts.ram) {
    overviewCharts.ram = new Chart(ramEl, {
      type: "line",
      data: {
        labels: [],
        datasets: [
          { label: "Docker / host RAM %", data: [], ...dockerLine },
          { label: "System RAM (% used)", data: [], ...sysLine },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: { ticks: { color: "#e9d5ff", font: { size: 10 } }, grid: { color: THEME.grid } },
          y: {
            beginAtZero: true,
            max: 100,
            grid: { color: THEME.grid },
            ticks: { color: "#fbcfe8", font: { size: 10 } },
          },
        },
        plugins: { legend: overviewLegend },
      },
    });
  }
  if (!overviewCharts.url) {
    overviewCharts.url = new Chart(urlEl, {
      type: "doughnut",
      data: {
        labels: ["OK", "Fail / unknown"],
        datasets: [{ data: [0, 0], backgroundColor: ["#34d399", "#fb7185"], borderWidth: 0 }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        cutout: "52%",
        plugins: { legend: { position: "bottom", labels: { boxWidth: 8, font: { size: 10 } } } },
      },
    });
  }
  if (!overviewCharts.cf) {
    overviewCharts.cf = new Chart(cfEl, {
      type: "bar",
      data: {
        labels: ["R2", "KV", "D1", "Wrk", "Au"],
        datasets: [
          {
            label: "Reachable",
            data: [0, 0, 0, 0, 0],
            backgroundColor: ["#a78bfa", "#a78bfa", "#a78bfa", "#a78bfa", "#a78bfa"],
            borderRadius: 4,
            minBarLength: 8,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: { legend: { display: false } },
        scales: {
          y: { beginAtZero: true, max: 100, ticks: { callback: (v) => `${v}%` } },
          x: { grid: { display: false } },
        },
      },
    });
  }
  if (!overviewCharts.engine) {
    overviewCharts.engine = new Chart(engEl, {
      type: "doughnut",
      data: {
        labels: ["Running", "Paused", "Stopped"],
        datasets: [
          {
            data: [0, 0, 0],
            backgroundColor: ["#4ade80", "#fb923c", "#818cf8"],
            borderWidth: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        cutout: "45%",
        plugins: { legend: { position: "bottom", labels: { boxWidth: 8, font: { size: 10 } } } },
      },
    });
  }
  if (!overviewCharts.svcBar) {
    overviewCharts.svcBar = new Chart(barEl, {
      type: "bar",
      data: {
        labels: [],
        datasets: [{ label: "CPU %", data: [], backgroundColor: "#22d3ee", borderRadius: 6 }],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { beginAtZero: true, max: 100, ticks: { callback: (v) => `${v}%` } },
          y: { grid: { display: false } },
        },
      },
    });
  }
  return true;
}

function renderOverviewKpi(data, cf, lastPt) {
  const mount = document.getElementById("overviewKpi");
  if (!mount) return;
  const s = data.system_status || {};
  const ref = data.reference || {};
  const d = data.docker_overview?.counts || {};
  const host = data.docker_overview?.host || {};
  const svc = cf?.services || {};
  const cfUp = [svc.r2, svc.kv, svc.d1, svc.workers, svc.autoscale].filter((x) => x?.reachable).length;
  const dockCpu = lastPt?.docker?.cpu_percent;
  const dockRam = lastPt?.docker?.memory_percent;
  const aggCpu = s.aggregate_cpu_percent;
  const aggRam = s.aggregate_memory_percent;
  const cpuShow = dockCpu != null ? `${Number(dockCpu).toFixed(1)}%` : aggCpu != null ? `${aggCpu}%` : "—";
  const ramShow = dockRam != null ? `${Number(dockRam).toFixed(1)}%` : aggRam != null ? `${aggRam}%` : "—";

  mount.innerHTML = `
    <div class="kpi-cell">
      <div class="kpi-value hl-${s.level || "unknown"}">${(s.level || "—").toUpperCase()}</div>
      <div class="kpi-label">${s.services_running ?? "—"}/${s.services_total ?? "—"} services</div>
    </div>
    <div class="kpi-cell">
      <div class="kpi-value">${cpuShow}</div>
      <div class="kpi-label">Docker CPU Σ</div>
    </div>
    <div class="kpi-cell">
      <div class="kpi-value">${ramShow}</div>
      <div class="kpi-label">Docker RAM %</div>
    </div>
    <div class="kpi-cell">
      <div class="kpi-value">${data.healthy_urls ?? "—"}/${data.url_count ?? "—"}</div>
      <div class="kpi-label">URL probes</div>
    </div>
    <div class="kpi-cell">
      <div class="kpi-value">${cfUp}/5</div>
      <div class="kpi-label">CF local</div>
    </div>
    <div class="kpi-cell">
      <div class="kpi-value">${d.containers_running ?? "—"}</div>
      <div class="kpi-label">containers · ${escapeHtml(host.server_version || "Docker")}</div>
    </div>
    <div class="kpi-cell kpi-cell--soft">
      <div class="kpi-value">${ref.healthy_urls ?? "—"}/${ref.total_urls ?? "—"}</div>
      <div class="kpi-label">Directory URLs</div>
    </div>`;
}

function renderOverviewChips(data, cf) {
  const el = document.getElementById("overviewChips");
  if (!el) return;
  const s = data.system_status || {};
  const alerts = (s.alerts || []).length;
  const svc = cf?.services || {};
  const cfUp = [svc.r2, svc.kv, svc.d1, svc.workers, svc.autoscale].filter((x) => x?.reachable).length;
  const parts = [
    `<span class="chip">Reference — all <code>*.lh</code> links</span>`,
    `<span class="chip">Infrastructure — engine &amp; tables</span>`,
    `<span class="chip">Metrics — net &amp; IOPS</span>`,
    `<span class="chip">Control — lifecycle</span>`,
  ];
  if (alerts > 0) {
    parts.unshift(`<span class="chip chip--warn">${alerts} alert(s) — see Infrastructure</span>`);
  }
  if (cfUp < 5) {
    parts.unshift(`<span class="chip chip--bad">CF local ${cfUp}/5</span>`);
  }
  el.innerHTML = parts.join("");
}

function updateOverviewDashboard(data, cf, metricsData) {
  const pts = metricsData?.points || [];
  const labels = pts.map((p) => {
    try {
      return new Date(p.ts).toLocaleTimeString();
    } catch {
      return "";
    }
  });
  const d = (key) => pts.map((p) => p.docker?.[key] ?? null);
  const sCpu = sysVal("cpu_percent")(pts);
  const sTemp = sysVal("cpu_temp_c_max")(pts);
  const ramSecond = ramChartSecondLine(pts);
  const hasProcRam = pts.some((p) => p.system?.memory_percent != null);

  renderOverviewKpi(data, cf, pts.length ? pts[pts.length - 1] : null);
  renderOverviewChips(data, cf);

  const cpuMeta = document.getElementById("overviewCpuMeta");
  const ramMeta = document.getElementById("overviewRamMeta");
  const last = pts.length ? pts[pts.length - 1] : null;
  const s = data.system_status || {};
  const tempStr = last?.system?.cpu_temp_c_max != null ? `${last.system.cpu_temp_c_max}°C` : "—";
  if (cpuMeta) {
    cpuMeta.textContent = last
      ? `Now · Docker ${last.docker?.cpu_percent ?? "—"}% · System ${last.system?.cpu_percent ?? "—"}% · Temp ${tempStr}`
      : `Aggregate · Docker ${s.aggregate_cpu_percent ?? "—"}% (open Metrics for history)`;
  }
  if (ramMeta) {
    const hostPct = last?.docker?.memory_percent_of_host ?? last?.docker?.memory_percent;
    const secondVal = hasProcRam ? last?.system?.memory_percent : last?.docker?.memory_percent_of_limits;
    const secondFmt = secondVal != null && secondVal !== "" ? `${secondVal}%` : "—";
    ramMeta.textContent = last
      ? `Now · Docker/host ${hostPct ?? "—"}% · ${hasProcRam ? "System" : "Σ limits"} ${secondFmt}`
      : `Aggregate · ${s.aggregate_memory_percent ?? "—"}% of limits (open Metrics for history)`;
  }

  if (!ensureOverviewCharts()) return;

  overviewCharts.cpu.data.labels = labels;
  overviewCharts.cpu.data.datasets[0].data = d("cpu_percent");
  overviewCharts.cpu.data.datasets[1].data = sCpu;
  overviewCharts.cpu.data.datasets[2].data = sTemp;
  overviewCharts.cpu.data.datasets[1].hidden = !seriesHasNumber(sCpu);
  overviewCharts.cpu.data.datasets[2].hidden = false;
  if (overviewCharts.cpu.options.scales?.y) {
    overviewCharts.cpu.options.scales.y.max = computeCpuYMax(pts);
  }
  const tbCpu = tempAxisBounds(sTemp);
  if (overviewCharts.cpu.options.scales?.y1) {
    overviewCharts.cpu.options.scales.y1.display = true;
    overviewCharts.cpu.options.scales.y1.min = tbCpu.min;
    overviewCharts.cpu.options.scales.y1.max = tbCpu.max;
  }
  overviewCharts.cpu.update();

  overviewCharts.ram.data.labels = labels;
  overviewCharts.ram.data.datasets[0].data = dockerRamLineHost(pts);
  overviewCharts.ram.data.datasets[0].label = "Docker / host RAM %";
  overviewCharts.ram.data.datasets[1].data = ramSecond;
  overviewCharts.ram.data.datasets[1].label = ramSecondLineLabel(pts);
  if (overviewCharts.ram.options.scales?.y) {
    overviewCharts.ram.options.scales.y.max = computeRamYMax(pts);
  }
  overviewCharts.ram.update();

  const ok = Number(data.healthy_urls || 0);
  const total = Number(data.url_count || 0);
  const fail = Math.max(0, total - ok);
  overviewCharts.url.data.datasets[0].data = total > 0 ? [ok, fail] : [1, 0];
  overviewCharts.url.update();

  const cfSvc = cf?.services || {};
  const keys = [
    { k: "r2", l: "R2" },
    { k: "kv", l: "KV" },
    { k: "d1", l: "D1" },
    { k: "workers", l: "Wrk" },
    { k: "autoscale", l: "Au" },
  ];
  const upVals = keys.map(({ k }) => (cfSvc[k]?.reachable ? 100 : 0));
  const cfPaletteUp = ["#22d3ee", "#a78bfa", "#34d399", "#fbbf24", "#f472b6"];
  const colors = keys.map(({ k }, i) =>
    cfSvc[k]?.reachable ? cfPaletteUp[i % cfPaletteUp.length] : "#fb7185",
  );
  overviewCharts.cf.data.datasets[0].data = upVals;
  overviewCharts.cf.data.datasets[0].backgroundColor = colors;
  overviewCharts.cf.update();

  const counts = data.docker_overview?.counts || {};
  const run = Number(counts.containers_running || 0);
  const paused = Number(counts.containers_paused || 0);
  const stopped = Number(counts.containers_stopped || 0);
  overviewCharts.engine.data.datasets[0].data = [run, paused, stopped];
  overviewCharts.engine.update();

  const topSvc = [...(data.services || [])]
    .sort((a, b) => Number(b.metrics?.cpu_percent || 0) - Number(a.metrics?.cpu_percent || 0))
    .slice(0, 10);
  overviewCharts.svcBar.data.labels = topSvc.map((x) => x.service);
  overviewCharts.svcBar.data.datasets[0].data = topSvc.map((x) => Number(x.metrics?.cpu_percent || 0));
  overviewCharts.svcBar.update();
}

function renderInfrastructurePanel(data, cf, opts = {}) {
  if (!document.getElementById("systemStatus")) return;
  renderSystemStatus(data.system_status);
  renderDockerOverview(data.docker_overview);
  renderCloudflareLocal(cf);
  if (!opts.skipTrendCharts) {
    updateCharts(data);
  }
  renderServices(data);
  renderContainers(data);
  loadOllamaModelsPanel();
}

async function loadOllamaModelsPanel() {
  const panel = document.getElementById("ollamaModelsPanel");
  const sum = document.getElementById("ollamaModelsSummary");
  if (!panel) return;
  try {
    const res = await fetch("/api/ollama/models");
    const data = await res.json();
    const reach = data.ollama_reachable;
    if (sum) {
      sum.textContent = reach
        ? `Ollama API reachable (${data.ollama_base || "—"}) · ${data.rows?.length ?? 0} model row(s) · pinned: ${(data.pinned || []).length}`
        : `Ollama API not reachable — is the ollama container running on lh-network? (${data.ollama_base || "http://ollama:11434"})`;
    }
    const rows = data.rows || [];
    if (!rows.length) {
      panel.innerHTML = `<p class="muted">No models listed. Add names to <code>ai-stack/config/ollama-pinned-models.txt</code> or pull models from the CLI.</p>`;
      return;
    }
    const thead = `<thead><tr><th>Model</th><th>Pinned</th><th>On disk</th><th>In memory</th><th>Size</th><th>Actions</th></tr></thead>`;
    const tbody = rows
      .map((r) => {
        const name = escapeHtml(r.name);
        const acts = [
          `<button type="button" class="ollama-act ollama-act--safe" data-ollama-act="on" data-ollama-model="${name}" title="Pull / ensure on disk">On</button>`,
          `<button type="button" class="ollama-act ollama-act--safe" data-ollama-act="pull" data-ollama-model="${name}">Pull</button>`,
          `<button type="button" class="ollama-act ollama-act--ops" data-ollama-act="reinstall" data-ollama-model="${name}" title="Re-download">Reinstall</button>`,
          `<button type="button" class="ollama-act ollama-act--caution" data-ollama-act="unload" data-ollama-model="${name}" title="Unload from VRAM">Off</button>`,
          `<button type="button" class="ollama-act ollama-act--destructive" data-ollama-act="delete" data-ollama-model="${name}">Remove</button>`,
        ].join(" ");
        return `<tr>
          <td><code>${name}</code></td>
          <td>${r.pinned ? "✓" : "—"}</td>
          <td>${r.installed ? "✓" : "—"}</td>
          <td>${r.running ? "✓" : "—"}</td>
          <td>${r.size != null ? formatBytes(r.size) : "—"}</td>
          <td class="ollama-models-actions">${acts}</td>
        </tr>`;
      })
      .join("");
    panel.innerHTML = `<table class="ollama-models-table">${thead}<tbody>${tbody}</tbody></table>`;
  } catch (e) {
    if (sum) sum.textContent = `Error: ${e.message || e}`;
    panel.innerHTML = `<p class="muted">Failed to load Ollama models.</p>`;
  }
}

let _appModalResolve = null;

function initAppModal() {
  const overlay = document.getElementById("appModalOverlay");
  if (!overlay || overlay.dataset.wired === "1") return;
  overlay.dataset.wired = "1";
  const cancel = document.getElementById("appModalCancel");
  const primary = document.getElementById("appModalPrimary");

  const finish = (value) => {
    const fn = _appModalResolve;
    _appModalResolve = null;
    overlay.hidden = true;
    if (typeof fn === "function") fn(value);
  };

  cancel.addEventListener("click", () => finish(false));
  primary.addEventListener("click", () => {
    const mode = overlay.dataset.mode || "confirm";
    finish(mode === "alert" ? undefined : true);
  });
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay && overlay.dataset.mode === "confirm") finish(false);
  });
  document.addEventListener("keydown", (e) => {
    if (overlay.hidden) return;
    if (e.key !== "Escape") return;
    e.preventDefault();
    if (overlay.dataset.mode === "alert") finish(undefined);
    else finish(false);
  });
}

function _appModalSetPrimaryVariant(btn, variant) {
  btn.classList.remove("ctrl-overlay-btn--primary", "ctrl-overlay-btn--ops", "ctrl-overlay-btn--danger");
  if (variant === "danger") btn.classList.add("ctrl-overlay-btn--danger");
  else if (variant === "primary") btn.classList.add("ctrl-overlay-btn--primary");
  else btn.classList.add("ctrl-overlay-btn--ops");
}

function showAppAlert(message, title = "Notice") {
  initAppModal();
  const overlay = document.getElementById("appModalOverlay");
  const cancel = document.getElementById("appModalCancel");
  const primary = document.getElementById("appModalPrimary");
  document.getElementById("appModalTitle").textContent = title;
  document.getElementById("appModalMessage").textContent = message;
  overlay.dataset.mode = "alert";
  cancel.classList.add("is-hidden");
  primary.textContent = "OK";
  _appModalSetPrimaryVariant(primary, "primary");
  return new Promise((resolve) => {
    _appModalResolve = () => resolve();
    overlay.hidden = false;
    primary.focus();
  });
}

function showAppConfirm({
  title = "Confirm",
  message = "",
  confirmText = "Continue",
  cancelText = "Cancel",
  danger = false,
} = {}) {
  initAppModal();
  const overlay = document.getElementById("appModalOverlay");
  const cancel = document.getElementById("appModalCancel");
  const primary = document.getElementById("appModalPrimary");
  document.getElementById("appModalTitle").textContent = title;
  document.getElementById("appModalMessage").textContent = message;
  overlay.dataset.mode = "confirm";
  cancel.classList.remove("is-hidden");
  cancel.textContent = cancelText;
  primary.textContent = confirmText;
  _appModalSetPrimaryVariant(primary, danger ? "danger" : "ops");
  return new Promise((resolve) => {
    _appModalResolve = resolve;
    overlay.hidden = false;
    primary.focus();
  });
}

async function runOllamaModelAction(action, model) {
  try {
    const res = await fetch("/api/ollama/models/action", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Control-Token": controlToken() },
      body: JSON.stringify({ action, model: model || "", token: controlToken() }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      await showAppAlert(data.error ? JSON.stringify(data.error) : `HTTP ${res.status}`, "Ollama action failed");
      return;
    }
    if (data.note) {
      await showAppAlert(data.note, "Ollama");
    }
    loadOllamaModelsPanel();
  } catch (e) {
    await showAppAlert(String(e.message || e), "Error");
  }
}

function initOllamaModelsPanel() {
  document.getElementById("ollamaPullAllBtn")?.addEventListener("click", async () => {
    const ok = await showAppConfirm({
      title: "Pull all pinned models",
      message: "Start background pull for all pinned models? This can take a long time.",
      confirmText: "Start pull",
    });
    if (!ok) return;
    runOllamaModelAction("pull_all", "");
  });
  document.getElementById("ollamaModelsRefreshBtn")?.addEventListener("click", () => loadOllamaModelsPanel());
  document.getElementById("ollamaModelsPanel")?.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-ollama-act]");
    if (!btn) return;
    const act = btn.getAttribute("data-ollama-act");
    const model = btn.getAttribute("data-ollama-model") || "";
    if (act === "delete") {
      const ok = await showAppConfirm({
        title: "Remove model",
        message: `Remove "${model}" from disk? This cannot be undone.`,
        confirmText: "Remove",
        danger: true,
      });
      if (!ok) return;
    }
    runOllamaModelAction(act, model);
  });
}

function activateTab(tabId) {
  activeTab = tabId;
  try {
    localStorage.setItem(LS_ACTIVE_TAB_KEY, tabId);
  } catch (_) {
    /* ignore */
  }
  if (metricsTimer) {
    clearInterval(metricsTimer);
    metricsTimer = null;
  }
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tabId);
  });
  document.querySelectorAll(".tab-content").forEach((tab) => {
    tab.classList.toggle("active", tab.id === tabId);
  });
  if (tabId === "logsTab") {
    loadLogs();
  }
  if (tabId === "metricsTab") {
    loadMetricsCharts();
    metricsTimer = setInterval(loadMetricsCharts, 5000);
  }
  if (tabId === "controlTab") {
    loadControlTargets();
  }
  if (tabId === "referenceTab") {
    loadReferenceTab();
  }
  if (tabId === "docsTab") {
    loadDocsCatalog();
  }
  if (tabId === "developTab") {
    renderDevelopCards();
  }
  if (tabId === "overviewTab" && lastOverviewData) {
    updateOverviewDashboard(lastOverviewData, lastCloudflareData, lastMetricsData || { points: [] });
  }
  if (tabId === "infrastructureTab") {
    if (lastOverviewData) {
      renderInfrastructurePanel(lastOverviewData, lastCloudflareData);
    } else {
      loadOllamaModelsPanel();
    }
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

function renderCloudflareLocal(cf) {
  if (!cf || !cf.services) {
    document.getElementById("cloudflareLocal").innerHTML = "<div class='card'>Cloudflare local status unavailable</div>";
    return;
  }

  const svc = cf.services || {};
  const cnt = cf.counts || {};
  const badgeText = (ok) => (ok ? "reachable" : "down");
  const badgeColor = (ok) => (ok ? "ok" : "bad");

  document.getElementById("cloudflareLocal").innerHTML = `
    <div class="card">
      <strong>Service Reachability</strong>
      <div class="row"><span>R2</span><span class="pill ${badgeColor(!!svc.r2?.reachable)}">${badgeText(!!svc.r2?.reachable)}</span></div>
      <div class="row"><span>KV</span><span class="pill ${badgeColor(!!svc.kv?.reachable)}">${badgeText(!!svc.kv?.reachable)}</span></div>
      <div class="row"><span>D1</span><span class="pill ${badgeColor(!!svc.d1?.reachable)}">${badgeText(!!svc.d1?.reachable)}</span></div>
      <div class="row"><span>Workers</span><span class="pill ${badgeColor(!!svc.workers?.reachable)}">${badgeText(!!svc.workers?.reachable)}</span></div>
      <div class="row"><span>Autoscaler</span><span class="pill ${badgeColor(!!svc.autoscale?.reachable)}">${badgeText(!!svc.autoscale?.reachable)}</span></div>
    </div>
    <div class="card">
      <strong>Resource Counts</strong>
      <div class="row"><span>R2 buckets</span><span>${cnt.buckets || 0}</span></div>
      <div class="row"><span>KV namespaces</span><span>${cnt.namespaces || 0}</span></div>
      <div class="row"><span>D1 databases</span><span>${cnt.databases || 0}</span></div>
      <div class="row"><span>Autoscale replicas</span><span>${cnt.autoscale_replicas || 0}</span></div>
    </div>
    <div class="card">
      <strong>Autoscaler Policy</strong>
      <div class="row"><span>Min replicas</span><span>${svc.autoscale?.status?.min_replicas ?? "-"}</span></div>
      <div class="row"><span>Max replicas</span><span>${svc.autoscale?.status?.max_replicas ?? "-"}</span></div>
      <div class="row"><span>Avg CPU</span><span>${svc.autoscale?.status?.avg_cpu_percent ?? 0}%</span></div>
      <div class="row"><span>Last events</span><span>${(svc.autoscale?.status?.events || []).length}</span></div>
    </div>
    <div class="card">
      <strong>Quick links</strong>
      <div class="row"><span>R2</span><a href="http://r2.lh" target="_blank" rel="noopener">http://r2.lh</a></div>
      <div class="row"><span>KV</span><a href="http://kv.lh" target="_blank" rel="noopener">http://kv.lh</a></div>
      <div class="row"><span>D1</span><a href="http://d1.lh" target="_blank" rel="noopener">http://d1.lh</a></div>
      <div class="row"><span>Workers</span><a href="http://workers.lh" target="_blank" rel="noopener">http://workers.lh</a></div>
      <div class="row"><span>Autoscaler</span><a href="http://autoscale.lh" target="_blank" rel="noopener">http://autoscale.lh</a></div>
      <div class="row"><span>MinIO UI</span><a href="http://minio-console.lh" target="_blank" rel="noopener">http://minio-console.lh</a></div>
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
        { label: "CPU %", data: [], borderColor: "#38bdf8", backgroundColor: "rgba(56,189,248,0.25)", tension: 0.25, fill: true },
        { label: "Memory %", data: [], borderColor: "#a78bfa", backgroundColor: "rgba(167,139,250,0.25)", tension: 0.25, fill: true },
        { label: "Errors/min", data: [], borderColor: "#fb7185", backgroundColor: "rgba(251,113,133,0.22)", tension: 0.25, fill: true }
      ]
    },
    options: { responsive: true, maintainAspectRatio: false, animation: false }
  });

  charts.bar = new Chart(document.getElementById("barChart"), {
    type: "bar",
    data: {
      labels: [],
      datasets: [
        { label: "CPU %", data: [], backgroundColor: "#38bdf8" },
        { label: "Memory %", data: [], backgroundColor: "#e879f9" }
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
      datasets: [{ data: [0, 0, 0], backgroundColor: ["#4ade80", "#fbbf24", "#fb7185"] }]
    },
    options: { responsive: true, maintainAspectRatio: false, animation: false }
  });
  return true;
}

function destroyMetricsChartsIfStale() {
  const needCpu = (c) => !c || c.data.datasets.length < 3;
  const needIops = (c) => !c || c.data.datasets.length < 3;
  if (!metricsCharts.cpu || !metricsCharts.ram || !metricsCharts.net || !metricsCharts.iops) return;
  if (needCpu(metricsCharts.cpu) || needIops(metricsCharts.iops)) {
    try {
      metricsCharts.cpu?.destroy();
      metricsCharts.ram?.destroy();
      metricsCharts.net?.destroy();
      metricsCharts.iops?.destroy();
      metricsCharts.temp?.destroy();
    } catch (_) {
      /* ignore */
    }
    metricsCharts.cpu = null;
    metricsCharts.ram = null;
    metricsCharts.net = null;
    metricsCharts.iops = null;
    metricsCharts.temp = null;
  }
}

function ensureMetricsCharts() {
  if (typeof Chart === "undefined") return false;
  destroyMetricsChartsIfStale();

  const cpuEl = document.getElementById("metricsChartCpu");
  const ramEl = document.getElementById("metricsChartRam");
  const netEl = document.getElementById("metricsChartNet");
  const iopsEl = document.getElementById("metricsChartIops");
  const tempEl = document.getElementById("metricsChartTemp");
  if (!cpuEl || !ramEl || !netEl || !iopsEl) return false;

  const dockerLine = { borderColor: THEME.docker, tension: 0.2, spanGaps: true, ...LINE_STYLE };
  const sysLine = { borderColor: THEME.system, tension: 0.2, spanGaps: true, ...LINE_STYLE };
  const blockLine = { borderColor: THEME.block, tension: 0.2, spanGaps: true, ...LINE_STYLE };
  const gridColor = THEME.grid;
  const tickStyle = { color: "#e9d5ff", font: { size: 11 } };

  const legendBottom = { position: "bottom", labels: { boxWidth: 10, font: { size: 11, color: "#faf5ff" } } };

  if (!metricsCharts.cpu) {
  metricsCharts.cpu = new Chart(cpuEl, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        { label: "Docker CPU (≈% of host vCPUs)", data: [], yAxisID: "y", ...dockerLine },
        { label: "System CPU (host %, /proc)", data: [], yAxisID: "y", ...sysLine },
        {
          label: "Host temp (°C, /sys)",
          data: [],
          yAxisID: "y1",
          borderColor: THEME.temp,
          tension: 0.2,
          spanGaps: true,
          ...LINE_STYLE,
          borderWidth: 3,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { ticks: tickStyle, grid: { color: gridColor } },
        y: { beginAtZero: true, max: 100, grid: { color: gridColor }, ticks: tickStyle },
        y1: {
          type: "linear",
          position: "right",
          display: true,
          beginAtZero: true,
          min: 0,
          max: 90,
          grid: { display: false },
          ticks: { color: THEME.temp, font: { size: 11 }, callback: (v) => `${v}°` },
        },
      },
      plugins: { legend: legendBottom },
    },
  });
  }

  if (!metricsCharts.ram) {
  metricsCharts.ram = new Chart(ramEl, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        { label: "Docker / host RAM %", data: [], ...dockerLine },
        { label: "System RAM (% used)", data: [], ...sysLine },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { ticks: tickStyle, grid: { color: gridColor } },
        y: { beginAtZero: true, max: 100, grid: { color: gridColor }, ticks: tickStyle },
      },
      plugins: { legend: legendBottom },
    },
  });
  }

  if (!metricsCharts.net) {
  metricsCharts.net = new Chart(netEl, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        { label: "Docker net (RX+TX Mb/s)", data: [], ...dockerLine },
        { label: "System net (host Mb/s)", data: [], ...sysLine },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { ticks: tickStyle, grid: { color: gridColor } },
        y: { beginAtZero: true, grid: { color: gridColor }, ticks: tickStyle },
      },
      plugins: { legend: legendBottom },
    },
  });
  }

  if (!metricsCharts.iops) {
  metricsCharts.iops = new Chart(iopsEl, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        { label: "Docker IOPS (est.)", data: [], yAxisID: "y", ...dockerLine },
        { label: "System IOPS (diskstats)", data: [], yAxisID: "y", ...sysLine },
        { label: "Docker block (Σ Mb/s)", data: [], yAxisID: "y1", ...blockLine },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { ticks: tickStyle, grid: { color: gridColor } },
        y: { beginAtZero: true, grid: { color: gridColor }, ticks: tickStyle },
        y1: {
          type: "linear",
          position: "right",
          beginAtZero: true,
          grid: { display: false },
          ticks: { color: "#fde047", font: { size: 11 } },
        },
      },
      plugins: { legend: legendBottom },
    },
  });
  }

  if (tempEl && !metricsCharts.temp) {
    metricsCharts.temp = new Chart(tempEl, {
      type: "line",
      data: {
        labels: [],
        datasets: [
          {
            label: "Max thermal zone (°C)",
            data: [],
            borderColor: THEME.temp,
            backgroundColor: "rgba(251, 146, 60, 0.15)",
            tension: 0.25,
            fill: true,
            spanGaps: true,
            borderWidth: 3,
            pointRadius: 0,
            pointHoverRadius: 5,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: { ticks: tickStyle, grid: { color: gridColor } },
          y: {
            beginAtZero: true,
            min: 0,
            max: 90,
            grid: { color: gridColor },
            ticks: { color: THEME.temp, font: { size: 11 }, callback: (v) => `${v}°C` },
          },
        },
        plugins: { legend: legendBottom },
      },
    });
  }

  return true;
}

function sysVal(key) {
  return (pts) =>
    pts.map((p) => {
      const v = p.system?.[key];
      return v == null ? null : v;
    });
}

function renderMetricsChartsFromPayload(data, sum, hint, cacheNote) {
  const pts = data.points || [];
  const labels = pts.map((p) => {
    try {
      return new Date(p.ts).toLocaleTimeString();
    } catch {
      return "";
    }
  });
  const d = (key) => pts.map((p) => p.docker?.[key] ?? null);
  const sCpu = sysVal("cpu_percent")(pts);
  const sTemp = sysVal("cpu_temp_c_max")(pts);
  const sNet = sysVal("net_total_mbps")(pts);
  const sIops = sysVal("iops_total_est")(pts);
  const ramSecond = ramChartSecondLine(pts);
  const blkMb = d("blk_total_mbps");

  metricsCharts.cpu.data.labels = labels;
  metricsCharts.cpu.data.datasets[0].data = d("cpu_percent");
  metricsCharts.cpu.data.datasets[1].data = sCpu;
  metricsCharts.cpu.data.datasets[2].data = sTemp;
  metricsCharts.cpu.data.datasets[1].hidden = !seriesHasNumber(sCpu);
  metricsCharts.cpu.data.datasets[2].hidden = false;
  if (metricsCharts.cpu.options.scales?.y) {
    metricsCharts.cpu.options.scales.y.max = computeCpuYMax(pts);
  }
  const tbM = tempAxisBounds(sTemp);
  if (metricsCharts.cpu.options.scales?.y1) {
    metricsCharts.cpu.options.scales.y1.display = true;
    metricsCharts.cpu.options.scales.y1.min = tbM.min;
    metricsCharts.cpu.options.scales.y1.max = tbM.max;
  }
  metricsCharts.cpu.update();

  const ph = document.getElementById("metricsTempPlaceholder");
  const wrap = document.getElementById("metricsTempChartWrap");
  if (metricsCharts.temp) {
    metricsCharts.temp.data.labels = labels;
    metricsCharts.temp.data.datasets[0].data = sTemp;
    const tbT = tempAxisBounds(sTemp);
    if (metricsCharts.temp.options.scales?.y) {
      metricsCharts.temp.options.scales.y.min = tbT.min;
      metricsCharts.temp.options.scales.y.max = tbT.max;
    }
    metricsCharts.temp.update();
    const showTemp = seriesHasNumber(sTemp);
    if (ph) ph.classList.toggle("is-hidden", showTemp);
    if (wrap) wrap.classList.toggle("is-hidden", !showTemp);
  }

  metricsCharts.ram.data.labels = labels;
  metricsCharts.ram.data.datasets[0].data = dockerRamLineHost(pts);
  metricsCharts.ram.data.datasets[0].label = "Docker / host RAM %";
  metricsCharts.ram.data.datasets[1].data = ramSecond;
  metricsCharts.ram.data.datasets[1].label = ramSecondLineLabel(pts);
  if (metricsCharts.ram.options.scales?.y) {
    metricsCharts.ram.options.scales.y.max = computeRamYMax(pts);
  }
  metricsCharts.ram.update();

  metricsCharts.net.data.labels = labels;
  metricsCharts.net.data.datasets[0].data = d("net_total_mbps");
  metricsCharts.net.data.datasets[1].data = sNet;
  metricsCharts.net.data.datasets[1].hidden = !seriesHasNumber(sNet);
  metricsCharts.net.update();

  metricsCharts.iops.data.labels = labels;
  metricsCharts.iops.data.datasets[0].data = d("iops_total_est");
  metricsCharts.iops.data.datasets[1].data = sIops;
  metricsCharts.iops.data.datasets[2].data = blkMb;
  metricsCharts.iops.data.datasets[1].hidden = !seriesHasNumber(sIops);
  metricsCharts.iops.data.datasets[2].hidden = !seriesHasPositive(blkMb);
  metricsCharts.iops.update();

  const last = pts[pts.length - 1];
  const procOk = last?.system?.host_proc_available;
  const hasTemp = pts.some((p) => p.system?.cpu_temp_c_max != null);
  if (hint) {
    const parts = [];
    if (procOk) parts.push("/proc mounted — host CPU/RAM/net/IOPS lines live.");
    else parts.push("No host /proc (typical on Docker Desktop) — second RAM line is Docker Σ vs host physical %.");
    if (hasTemp) parts.push("Temperature from /sys thermal zones.");
    else parts.push("Temperature needs Linux + /sys mount (see dashboard deploy script).");
    parts.push("IOPS often 0 without blkio; use right axis block Mb/s.");
    hint.textContent = parts.join(" ");
  }
  const tempBit = last?.system?.cpu_temp_c_max != null ? ` temp ${last.system.cpu_temp_c_max}°C` : "";
  const rawCpu = last?.docker?.cpu_sum_raw != null ? ` (raw Σ ${last.docker.cpu_sum_raw}%)` : "";
  let line =
    `Samples: ${pts.length} (max ${data.max_points || METRICS_MAX}) · ` +
    `Updated ${data.generated_at ? new Date(data.generated_at).toLocaleString() : ""}` +
    (last
      ? ` · Docker: CPU ${last.docker?.cpu_percent}%${rawCpu} RAM/host ${last.docker?.memory_percent_of_host ?? last.docker?.memory_percent}% limits ${last.docker?.memory_percent_of_limits ?? "—"}% net ${last.docker?.net_total_mbps} Mb/s IOPS~ ${last.docker?.iops_total_est} block ${last.docker?.blk_total_mbps ?? "—"} Mb/s${tempBit}`
      : "");
  if (cacheNote) line = `${cacheNote} ${line}`;
  sum.textContent = line;
}

async function loadMetricsCharts() {
  const sum = document.getElementById("metricsSummary");
  const hint = document.getElementById("metricsProcHint");
  if (!ensureMetricsCharts()) {
    sum.textContent = "Chart.js failed to load.";
    return;
  }
  const seeded = hydrateMetricsFromCache();
  if (seeded?.points?.length) {
    renderMetricsChartsFromPayload(
      {
        points: seeded.points,
        max_points: seeded.max_points,
        generated_at: seeded.generated_at,
      },
      sum,
      hint,
      "Cached (loading live) ·"
    );
  }
  try {
    const res = await fetch(`/api/metrics/history?limit=${METRICS_MAX}`);
    const data = await res.json();
    renderMetricsChartsFromPayload(data, sum, hint, "");
    saveMetricsCache(data);
  } catch (e) {
    const cached = hydrateMetricsFromCache();
    if (cached && (cached.points || []).length) {
      renderMetricsChartsFromPayload(
        {
          points: cached.points,
          max_points: cached.max_points,
          generated_at: cached.generated_at,
        },
        sum,
        hint,
        `Cached · ${e.message || e} ·`
      );
    } else {
      sum.textContent = `Metrics error: ${e.message || e}`;
    }
  }
}

function controlToken() {
  return localStorage.getItem("dashboard_control_token") || "";
}

async function loadControlTargets() {
  const row = document.getElementById("controlTokenRow");
  const grid = document.getElementById("controlTargets");
  const SB = serviceBrandUi();
  try {
    const res = await fetch("/api/control/targets");
    const data = await res.json();
    if (row) row.style.display = data.token_required ? "flex" : "none";
    const input = document.getElementById("controlTokenInput");
    if (input && !input.value && controlToken()) input.value = controlToken();

    const targets = data.targets || [];
    if (!grid) return;

    grid.innerHTML = targets
      .map((t) => {
        const brand = SB.getBrandForControlTarget(t);
        const { safe, danger } = SB.partitionControlActions(t.actions);
        const rt = t.runtime || {};
        const rtLabel = escapeHtml(rt.label || "—");
        const rtClass =
          rt.running === true
            ? "control-runtime--up"
            : rt.running === false
              ? "control-runtime--down"
              : rt.kind === "stack"
                ? "control-runtime--stack"
                : "control-runtime--na";
        const cardLabel = escapeHtml(t.label || t.id);
        const primaryBtns = safe
          .map((a) => {
            const cls = SB.actionButtonClasses(a);
            return `<button type="button" class="${cls}" data-target="${escapeHtml(t.id)}" data-action="${escapeHtml(a)}" data-label="${cardLabel}">${escapeHtml(a)}</button>`;
          })
          .join("");
        const dangerBtns = danger
          .map((a) => {
            const cls = SB.actionButtonClasses(a);
            return `<button type="button" class="${cls}" data-target="${escapeHtml(t.id)}" data-action="${escapeHtml(a)}" data-label="${cardLabel}">${escapeHtml(a)}</button>`;
          })
          .join("");
        const wrapClass =
          danger.length === 0
            ? "control-card__actions-wrap control-card__actions-wrap--safe-only"
            : "control-card__actions-wrap";
        const aside =
          danger.length === 0
            ? ""
            : `<aside class="control-actions control-actions--danger" aria-label="Destructive actions">
            <div class="control-actions__label">Destructive</div>
            ${dangerBtns}
          </aside>`;
        return `
          <div class="control-card" data-brand="${escapeHtml(brand)}">
            <div class="control-card__head">
              <div class="control-card__iconcol">
                <span class="control-card__emoji" aria-hidden="true">${SB.emojiFor(brand)}</span>
                ${SB.iconHtml(brand)}
              </div>
              <div class="control-card__titles">
                <h3>${escapeHtml(t.label)}</h3>
                <div class="control-meta">${escapeHtml(t.group)}${t.container ? ` · <code>${escapeHtml(t.container)}</code>` : ""}</div>
              </div>
            </div>
            <p class="control-card__runtime ${rtClass}" title="${rtLabel}"><span class="control-runtime-dot" aria-hidden="true"></span><span class="control-runtime-text">${rtLabel}</span></p>
            <div class="${wrapClass}">
              <div class="control-actions control-actions--primary">${primaryBtns}</div>
              ${aside}
            </div>
          </div>`;
      })
      .join("");

    grid.querySelectorAll("button.ctrl-act").forEach((btn) => {
      btn.addEventListener("click", () =>
        runControlAction(btn.dataset.target, btn.dataset.action, btn.dataset.label || ""),
      );
    });
  } catch (e) {
    if (grid) grid.innerHTML = `<div class="muted">Failed to load targets: ${escapeHtml(String(e.message || e))}</div>`;
  }
}

const CONTROL_ACTION_PHASES = [
  "Sending request…",
  "Running Docker / compose / script — can take a while…",
  "Still working…",
  "Holding connection open…",
];

let lastControlActionResultText = "";
let controlActionOverlayAbort = null;
let controlActionOverlayWired = false;
let controlActionToastTimer = null;

/** Works on http:// hosts where navigator.clipboard is blocked (insecure context). */
async function copyTextToClipboard(text) {
  if (typeof text !== "string" || !text.length) return false;
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) {
      /* fall through to execCommand */
    }
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    ta.style.top = "0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch (_) {
    return false;
  }
}

function showControlToast(message, kind) {
  const el = document.getElementById("controlActionToast");
  if (!el) return;
  if (controlActionToastTimer) {
    clearTimeout(controlActionToastTimer);
    controlActionToastTimer = null;
  }
  el.textContent = message;
  el.classList.remove("is-hidden", "control-action-toast--ok", "control-action-toast--bad");
  if (kind === "ok") el.classList.add("control-action-toast--ok");
  else if (kind === "bad") el.classList.add("control-action-toast--bad");
  controlActionToastTimer = setTimeout(() => {
    el.classList.add("is-hidden");
    el.textContent = "";
    controlActionToastTimer = null;
  }, 2800);
}

function initControlActionOverlay() {
  if (controlActionOverlayWired) return;
  const overlay = document.getElementById("controlActionOverlay");
  const cancel = document.getElementById("controlActionCancel");
  const copy = document.getElementById("controlActionCopy");
  const dismiss = document.getElementById("controlActionDismiss");
  if (!overlay || !dismiss) return;
  controlActionOverlayWired = true;
  cancel?.addEventListener("click", () => {
    if (controlActionOverlayAbort) controlActionOverlayAbort.abort();
  });
  copy?.addEventListener("click", async () => {
    const t = lastControlActionResultText || "";
    const prevLabel = copy.textContent;
    if (!t) {
      showControlToast("Nothing to copy yet.", "bad");
      return;
    }
    const ok = await copyTextToClipboard(t);
    if (ok) {
      showControlToast("Copied to clipboard.", "ok");
      copy.textContent = "Copied";
      setTimeout(() => {
        copy.textContent = prevLabel;
      }, 1600);
    } else {
      showControlToast("Copy failed — open “Full API response” below and copy manually.", "bad");
    }
  });
  dismiss.addEventListener("click", () => {
    overlay.hidden = true;
  });
}

async function runControlAction(targetId, action, cardLabel) {
  initControlActionOverlay();
  const overlay = document.getElementById("controlActionOverlay");
  const titleEl = document.getElementById("controlActionTitle");
  const liveEl = document.getElementById("controlActionLive");
  const summaryEl = document.getElementById("controlActionSummary");
  const snippetEl = document.getElementById("controlActionSnippet");
  const spinnerEl = document.getElementById("controlActionSpinner");
  const runningBar = document.getElementById("controlActionRunningBar");
  const doneBar = document.getElementById("controlActionDoneBar");
  const out = document.getElementById("controlResult");

  const label = (cardLabel || targetId || "").trim();
  const ac = new AbortController();
  controlActionOverlayAbort = ac;
  const t0 = Date.now();
  let phaseIdx = 0;
  let phaseTimer = null;
  let elapsedTimer = null;

  const clearTimers = () => {
    if (phaseTimer) clearInterval(phaseTimer);
    if (elapsedTimer) clearInterval(elapsedTimer);
    phaseTimer = null;
    elapsedTimer = null;
  };

  const bumpLive = () => {
    if (!liveEl) return;
    const sec = Math.max(0, Math.round((Date.now() - t0) / 1000));
    const phase = CONTROL_ACTION_PHASES[phaseIdx % CONTROL_ACTION_PHASES.length];
    liveEl.textContent = `${phase} (${sec}s)`;
  };

  if (overlay) overlay.hidden = false;
  if (controlActionToastTimer) {
    clearTimeout(controlActionToastTimer);
    controlActionToastTimer = null;
  }
  const toastEl = document.getElementById("controlActionToast");
  if (toastEl) {
    toastEl.classList.add("is-hidden");
    toastEl.textContent = "";
    toastEl.classList.remove("control-action-toast--ok", "control-action-toast--bad");
  }
  if (titleEl) titleEl.textContent = `${action} · ${label}`;
  if (summaryEl) {
    summaryEl.classList.add("is-hidden");
    summaryEl.textContent = "";
    summaryEl.classList.remove("control-action-summary--ok", "control-action-summary--bad");
  }
  if (snippetEl) {
    snippetEl.classList.remove("is-hidden");
    snippetEl.classList.add("control-action-snippet--live");
    snippetEl.textContent = "Waiting for output…\n";
  }
  if (spinnerEl) spinnerEl.classList.remove("is-hidden");
  if (runningBar) runningBar.classList.remove("is-hidden");
  if (doneBar) doneBar.classList.add("is-hidden");
  bumpLive();
  phaseTimer = setInterval(() => {
    phaseIdx += 1;
    bumpLive();
  }, 750);
  elapsedTimer = setInterval(bumpLive, 500);
  if (out) out.textContent = "Running…";

  const logChunks = [];
  let sawStreamByte = false;

  const appendStreamLog = (t) => {
    if (t === undefined || t === null) return;
    logChunks.push(typeof t === "string" ? t : String(t));
    if (snippetEl) {
      snippetEl.textContent = logChunks.join("");
      snippetEl.scrollTop = snippetEl.scrollHeight;
    }
    if (!sawStreamByte && spinnerEl) {
      sawStreamByte = true;
      spinnerEl.classList.add("is-hidden");
    }
  };

  try {
    const res = await fetch("/api/control/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Control-Token": controlToken() },
      body: JSON.stringify({ target_id: targetId, action, token: controlToken() }),
      signal: ac.signal,
    });

    if (!res.ok || !res.body?.getReader) {
      let data;
      try {
        data = await res.json();
      } catch (_) {
        const raw = await res.text().catch(() => "");
        data = { ok: false, error: `HTTP ${res.status}${raw ? ` — ${raw.slice(0, 400)}` : ""}` };
      }
      const text = JSON.stringify(data, null, 2);
      lastControlActionResultText = text;
      if (out) out.textContent = text;
      const details = document.querySelector(".control-response-details");
      if (details && (!res.ok || data?.ok === false)) details.open = true;
      const ok = res.ok && data && data.ok === true;
      if (titleEl) titleEl.textContent = ok ? `Done · ${action}` : `Finished · ${action}`;
      if (liveEl) {
        const sec = Math.max(0, Math.round((Date.now() - t0) / 1000));
        liveEl.textContent = ok ? `Succeeded in ${sec}s` : `Completed in ${sec}s (check details)`;
      }
      if (summaryEl) {
        summaryEl.classList.remove("is-hidden");
        summaryEl.classList.toggle("control-action-summary--ok", ok);
        summaryEl.classList.toggle("control-action-summary--bad", !ok);
        summaryEl.textContent = ok ? "Action succeeded." : data.error || "Action reported failure.";
      }
      const logBit = typeof data.log === "string" ? data.log : data.error ? String(data.error) : "";
      if (snippetEl && logBit) snippetEl.textContent = logBit;
    } else {
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let lineBuf = "";
      let finalResult = null;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        lineBuf += dec.decode(value, { stream: true });
        const parts = lineBuf.split("\n");
        lineBuf = parts.pop() ?? "";
        for (const line of parts) {
          if (!line.trim()) continue;
          let ev;
          try {
            ev = JSON.parse(line);
          } catch (_) {
            appendStreamLog(`${line}\n`);
            continue;
          }
          if (ev.type === "log" && ev.text != null) appendStreamLog(ev.text);
          if (ev.type === "done") finalResult = ev.result || null;
        }
      }
      if (lineBuf.trim()) {
        try {
          const ev = JSON.parse(lineBuf);
          if (ev.type === "log" && ev.text != null) appendStreamLog(ev.text);
          if (ev.type === "done") finalResult = ev.result || finalResult;
        } catch (_) {
          /* ignore trailing garbage */
        }
      }

      const data = finalResult || { ok: false, error: "no result from stream" };
      const fullLog = logChunks.join("");
      const payload = { ...data };
      if (fullLog) payload.log_stream = fullLog;
      const text = JSON.stringify(payload, null, 2);
      lastControlActionResultText = text;
      if (out) out.textContent = text;
      const details = document.querySelector(".control-response-details");
      if (details && data?.ok === false) details.open = true;

      const ok = data && data.ok === true;
      if (titleEl) titleEl.textContent = ok ? `Done · ${action}` : `Finished · ${action}`;
      if (liveEl) {
        const sec = Math.max(0, Math.round((Date.now() - t0) / 1000));
        const lines = fullLog ? fullLog.split("\n").length : 0;
        liveEl.textContent = ok
          ? `Succeeded in ${sec}s · ${lines} line(s) of output`
          : `Completed in ${sec}s (check log below)`;
      }
      if (summaryEl) {
        summaryEl.classList.remove("is-hidden");
        summaryEl.classList.toggle("control-action-summary--ok", ok);
        summaryEl.classList.toggle("control-action-summary--bad", !ok);
        summaryEl.textContent = ok ? "Action succeeded." : data.error || "Action reported failure.";
      }
    }
  } catch (e) {
    const aborted = e && (e.name === "AbortError" || e.code === 20);
    const msg = aborted ? "Request cancelled." : String(e.message || e);
    lastControlActionResultText = msg;
    if (out) out.textContent = msg;
    if (titleEl) titleEl.textContent = aborted ? "Cancelled" : "Error";
    if (liveEl) liveEl.textContent = aborted ? "You cancelled the request." : msg;
    if (summaryEl) {
      summaryEl.classList.remove("is-hidden");
      summaryEl.classList.add("control-action-summary--bad");
      summaryEl.classList.remove("control-action-summary--ok");
      summaryEl.textContent = aborted ? "No changes applied." : "Request failed before a full response.";
    }
  } finally {
    clearTimers();
    controlActionOverlayAbort = null;
    if (spinnerEl) spinnerEl.classList.add("is-hidden");
    if (runningBar) runningBar.classList.add("is-hidden");
    if (doneBar) doneBar.classList.remove("is-hidden");
    loadControlTargets();
    try {
      if (lastControlActionResultText) {
        localStorage.setItem("dashboard_last_control_result", lastControlActionResultText);
      }
    } catch (_) {
      /* ignore */
    }
    loadOverview().catch(() => {
      /* refresh overview + infra in background so runtime badges stay accurate */
    });
  }
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
  const SB = serviceBrandUi();
  document.getElementById("services").innerHTML = data.services.map((s) => {
    const brand = SB.getBrandForManagedService(s);
    const running = s.container_info.status === "running";
    const checks = s.url_checks.length
      ? s.url_checks.map((c) => `<div class="row"><a href="${c.url}" target="_blank">${c.url}</a>${urlProbePill(c)}</div>`).join("")
      : "<div class='muted'>No HTTP endpoint</div>";

    const credsBlock =
      (s.credentials || []).length > 0
        ? `<div class="svc-card__extras">
            <div class="svc-card__extras-title">Credentials</div>
            <ul class="svc-creds">${(s.credentials || []).map((c) => `<li>${escapeHtml(c)}</li>`).join("")}</ul>
          </div>`
        : "";
    const mgmtBlock =
      (s.management_links || []).length > 0
        ? `<div class="svc-card__extras">
            <div class="svc-card__extras-title">Management &amp; explorer</div>
            ${(s.management_links || [])
              .map(
                (l) =>
                  `<div class="row svc-mgmt-row"><a href="${escapeHtml(l.url)}" target="_blank" rel="noopener">${escapeHtml(l.label)}</a></div>`,
              )
              .join("")}
          </div>`
        : "";

    const m = s.metrics || {};
    const l = s.logs || {};
    const hasLogErrors = (l.error_lines || 0) > 0;
    const errorSamples = (l.last_errors || []).slice(-2).map((e) => (e.length > 140 ? `${e.slice(0, 140)}...` : e)).join("\n");

    return `
      <div class="card" data-brand="${escapeHtml(brand)}">
        <div class="svc-card__head">
          <div class="svc-card__iconcol">
            <span class="svc-card__emoji" aria-hidden="true">${SB.emojiFor(brand)}</span>
            ${SB.iconHtml(brand)}
          </div>
          <div class="svc-card__titles">
            <div class="row"><strong>${s.service}</strong>${badge(running, s.container_info.status)}</div>
            <div class="muted">${s.notes}</div>
          </div>
        </div>
        ${credsBlock}
        ${mgmtBlock}
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
      if (activeTab === "overviewTab" || activeTab === "infrastructureTab") {
        loadOverview();
      } else if (activeTab === "logsTab") {
        loadLogs();
      } else if (activeTab === "metricsTab") {
        loadMetricsCharts();
      } else if (activeTab === "referenceTab") {
        loadReferenceTab();
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
  const [overviewRes, cfRes, metricsRes] = await Promise.all([
    fetch("/api/overview"),
    fetch("/api/cloudflare-local"),
    fetch(`/api/metrics/history?limit=${OVERVIEW_METRICS_LIMIT}`),
  ]);
  const data = await overviewRes.json();
  const cloudflareData = await cfRes.json();
  let metricsData = { points: [], generated_at: null };
  try {
    if (metricsRes.ok) metricsData = await metricsRes.json();
  } catch (_) {
    /* keep empty */
  }

  const ref = data.reference || {};
  lastOverviewData = data;
  lastCloudflareData = cloudflareData;
  lastMetricsData = metricsData;
  lastReferencePayload = ref;

  setCompactHeaderSummary(data);
  updateOverviewDashboard(data, cloudflareData, metricsData);
  renderInfrastructurePanel(data, cloudflareData);
  saveOverviewCache(data, cloudflareData, metricsData);
}

function initControlBulkBar() {
  const bar = document.getElementById("controlBulkBar");
  if (!bar || bar.dataset.wired === "1") return;
  bar.dataset.wired = "1";
  const ECOSYSTEM_TARGET = "stack-ecosystem-all";
  bar.querySelectorAll("[data-bulk-action]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const action = btn.getAttribute("data-bulk-action");
      const lbl = btn.getAttribute("data-bulk-label") || action;
      if (!action) return;
      const destructive = action === "stop" || action === "restart" || action === "deploy";
      if (destructive) {
        const ok = await showAppConfirm({
          title: lbl,
          message:
            "Runs scripts under ai-stack/services (may take several minutes). Stop, restart, and redeploy skip this dashboard container so the UI can show the result.",
          confirmText: "Continue",
        });
        if (!ok) return;
      }
      runControlAction(ECOSYSTEM_TARGET, action, lbl);
    });
  });
}

async function bootstrap() {
  initAppModal();
  initTabs();
  initControlBulkBar();
  hydrateOverviewFromCache();
  try {
    const savedTab = localStorage.getItem(LS_ACTIVE_TAB_KEY);
    if (savedTab && document.getElementById(savedTab)) activateTab(savedTab);
  } catch (_) {
    /* ignore */
  }
  initControlActionOverlay();
  initOllamaModelsPanel();
  await initLogsPanel();
  initLogEvents();

  const tokSave = document.getElementById("controlTokenSave");
  if (tokSave) {
    tokSave.addEventListener("click", () => {
      const v = document.getElementById("controlTokenInput").value || "";
      localStorage.setItem("dashboard_control_token", v);
    });
  }

  document.getElementById("refreshNow").addEventListener("click", () => {
    if (activeTab === "overviewTab" || activeTab === "infrastructureTab") {
      loadOverview();
    } else if (activeTab === "metricsTab") {
      loadMetricsCharts();
    } else if (activeTab === "referenceTab") {
      loadReferenceTab();
    } else if (activeTab === "docsTab") {
      loadDocsCatalog();
    } else if (activeTab === "controlTab") {
      loadControlTargets();
    } else {
      loadLogs();
    }
  });
  document.getElementById("refreshRate").addEventListener("change", scheduleRefresh);

  document.body.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-copy-url]");
    if (!btn) return;
    const url = btn.getAttribute("data-copy-url");
    if (!url || !navigator.clipboard) return;
    navigator.clipboard.writeText(url).then(() => {
      const prev = btn.textContent;
      btn.textContent = "Copied";
      setTimeout(() => {
        btn.textContent = prev;
      }, 1400);
    });
  });

  document.getElementById("referenceFilter")?.addEventListener("input", applyReferenceFilter);

  await loadOverview();
  scheduleRefresh();
}

bootstrap();
