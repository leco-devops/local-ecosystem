let refreshTimer = null;
let tickTimer = null;
let nextRefreshEpoch = null;
let charts = { line: null, bar: null, pie: null };
let metricsCharts = { cpu: null, ram: null, net: null, iops: null, temp: null };
let metricsTimer = null;
let activeTab = "overviewTab";
let hostedAppsList = [];
let hostedSelectedSlug = "";
let hostedAppCharts = { cpu: null, mem: null, net: null };
let hostedLogStreamAbort = null;
let hostedLogStreamStarted = false;
/** Slug|tail|service — restart stream when this changes while live. */
let hostedLogStreamLiveKey = "";
let hostedExpandChart = null;
const trendHistory = { labels: [], cpu: [], memory: [], errors: [] };
const MAX_POINTS = 25;
const METRICS_MAX = 60;
let lastOverviewData = null;
let lastCloudflareData = null;
let lastMetricsData = null;

const LS_OVERVIEW_CACHE_KEY = "local_ecosystem_dashboard_overview_v1";
const LS_OVERVIEW_MAX_AGE_MS = 1000 * 60 * 60 * 48;
const LS_METRICS_CACHE_KEY = "local_ecosystem_dashboard_metrics_v1";
/** Deep metrics history can stay useful longer than overview snapshots (separate key + TTL). */
const LS_METRICS_MAX_AGE_MS = 1000 * 60 * 60 * 24 * 7;
const LS_TREND_CACHE_KEY = "local_ecosystem_dashboard_infra_trend_v1";
const LS_ACTIVE_TAB_KEY = "dashboard_active_tab";
/** Bump when cache shape changes so stale / corrupted entries are dropped. */
const CACHE_SCHEMA_VERSION = 5;

/** Server-rendered in index.html: token_required, optional prefill_control_token. */
(function initDashboardBoot() {
  const el = document.getElementById("dashboard-boot-config");
  const fallback = { token_required: false, prefill_control_token: null };
  let boot = { ...fallback };
  if (el?.textContent?.trim()) {
    try {
      boot = { ...fallback, ...JSON.parse(el.textContent) };
    } catch (_) {
      /* ignore malformed boot */
    }
  }
  window.__dashboardBoot = boot;
  if (boot.prefill_control_token) {
    try {
      localStorage.setItem("dashboard_control_token", String(boot.prefill_control_token));
    } catch (_) {
      /* private mode / quota */
    }
    const injectHint = document.getElementById("controlTokenInjectHint");
    if (injectHint) injectHint.style.display = "block";
  }
})();

/** Small Cloudflare mark for local CF adapter cards (not an official trademark asset). */
const CLOUDFLARE_MARK_SVG = `<svg class="cf-local-card__mark" viewBox="0 0 40 28" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" width="32" height="22"><path fill="#F48120" d="M28 8c2 0 3.5 1.3 4 3.2h6.2c-.4-5-4.7-8.8-10-8.8-4.2 0-7.7 2.4-9.3 5.9-1.2-.7-2.6-1.1-4-1.1-4.4 0-8 3.6-8 8s3.6 8 8 8h10v-6H13.3c-.8 0-1.3-.9-.9-1.6l.6-1.2c.3-.6 1-.9 1.6-.9H35c.3-1 .5-2 .5-3.1C35.5 11.4 32.2 8 28 8z"/><path fill="#FAAD3F" d="M30 19h-7v6h7c4.4 0 8-3.6 8-8 0-2.6-1.3-5-3.4-6.5l-1.2 2.2c1.1.9 1.8 2.2 1.8 3.8 0 2.8-2.2 5-5 5z"/></svg>`;

function wrapCfLocalCard(innerHtml) {
  return `<div class="cf-local-card">${CLOUDFLARE_MARK_SVG}<div class="cf-local-card__inner">${innerHtml}</div></div>`;
}

/** Render HTTP + HTTPS links for *.lh hosts (Traefik serves both). */
function cfDualUrlRow(label, httpUrl) {
  if (!httpUrl) return "";
  if (!httpUrl.startsWith("http://") || !httpUrl.includes(".lh")) {
    const one = escapeAttr(httpUrl);
    return `<div class="row"><span>${escapeHtml(label)}</span><a href="${one}"${externalNavigationAttrs(httpUrl)}>${escapeHtml(httpUrl)}</a></div>`;
  }
  const httpsUrl = httpUrl.replace(/^http:\/\//, "https://");
  const hA = escapeAttr(httpUrl);
  const sA = escapeAttr(httpsUrl);
  const hT = externalNavigationAttrs(httpUrl);
  const sT = externalNavigationAttrs(httpsUrl);
  return `<div class="row cf-dual-url-row"><span>${escapeHtml(label)}</span><span class="cf-dual-urls"><a href="${hA}"${hT}>HTTP</a><span class="cf-dual-sep">·</span><a href="${sA}"${sT}>HTTPS</a><span class="cf-dual-full muted small"><code>${escapeHtml(httpUrl)}</code></span></span></div>`;
}

/** Single management/GUI link row: HTTP·HTTPS for *.lh when Traefik serves both. */
function hubGuiLinkRow(link) {
  const u = (link && link.url) || "";
  const lab = (link && link.label) || "Link";
  if (!u) return "";
  const httpBase =
    u.startsWith("https://") && u.includes(".lh") ? u.replace(/^https:\/\//, "http://") : u;
  return cfDualUrlRow(lab, httpBase);
}

function lsRemove(key) {
  try {
    localStorage.removeItem(key);
  } catch (_) {
    /* ignore */
  }
}

function saveOverviewCache(data, cloudflareData, metricsData) {
  try {
    const payload = {
      schemaVersion: CACHE_SCHEMA_VERSION,
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
    if (payload.schemaVersion !== CACHE_SCHEMA_VERSION) {
      lsRemove(LS_OVERVIEW_CACHE_KEY);
      return false;
    }
    if (!payload?.overview || Date.now() - (payload.savedAt || 0) > LS_OVERVIEW_MAX_AGE_MS) return false;
    const m = payload.metrics;
    const metricsSafe =
      m && typeof m === "object" && Array.isArray(m.points)
        ? m
        : { points: [], generated_at: null, max_points: 0, notes: "" };
    lastOverviewData = payload.overview;
    lastCloudflareData = payload.cloudflare || {};
    lastMetricsData = metricsSafe;
    lastReferencePayload = payload.overview.reference || {};
    setCompactHeaderSummary(payload.overview);
    updateOverviewDashboard(payload.overview, payload.cloudflare || {}, metricsSafe);
    renderInfrastructurePanel(payload.overview, payload.cloudflare || {}, { skipTrendCharts: true });
    return true;
  } catch (_) {
    lsRemove(LS_OVERVIEW_CACHE_KEY);
    return false;
  }
}

function saveMetricsCache(metricsData) {
  try {
    if (!metricsData || typeof metricsData !== "object" || !Array.isArray(metricsData.points)) return;
    const payload = { schemaVersion: CACHE_SCHEMA_VERSION, savedAt: Date.now(), metrics: metricsData };
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
    if (payload.schemaVersion !== CACHE_SCHEMA_VERSION) {
      lsRemove(LS_METRICS_CACHE_KEY);
      return null;
    }
    if (Date.now() - (payload.savedAt || 0) > LS_METRICS_MAX_AGE_MS) return null;
    const m = payload.metrics;
    if (!m || typeof m !== "object" || !Array.isArray(m.points)) {
      lsRemove(LS_METRICS_CACHE_KEY);
      return null;
    }
    return m;
  } catch (_) {
    lsRemove(LS_METRICS_CACHE_KEY);
    return null;
  }
}

/** Prefer the richest metrics series (dedicated cache vs overview-embedded); avoids empty seed when one source expired. */
function getSeededMetricsHistory() {
  const fromKey = hydrateMetricsFromCache();
  const fromOverview = lastMetricsData;
  const nKey = fromKey?.points?.length ?? 0;
  const nOv = fromOverview && Array.isArray(fromOverview.points) ? fromOverview.points.length : 0;
  if (nKey >= nOv && nKey > 0) return fromKey;
  if (nOv > 0) return fromOverview;
  return null;
}

function hydrateTrendHistoryFromCache() {
  try {
    const raw = localStorage.getItem(LS_TREND_CACHE_KEY);
    if (!raw) return;
    const payload = JSON.parse(raw);
    if (payload.schemaVersion !== CACHE_SCHEMA_VERSION) {
      lsRemove(LS_TREND_CACHE_KEY);
      return;
    }
    if (Date.now() - (payload.savedAt || 0) > LS_OVERVIEW_MAX_AGE_MS) return;
    const { labels, cpu, memory, errors } = payload;
    if (!Array.isArray(labels) || !Array.isArray(cpu)) return;
    trendHistory.labels = labels.slice(-MAX_POINTS);
    const n = trendHistory.labels.length;
    const align = (arr) => {
      const a = (arr || []).slice(-MAX_POINTS).map((x) => Number(x));
      if (a.length > n) return a.slice(a.length - n);
      while (a.length < n) a.push(0);
      return a;
    };
    trendHistory.cpu = align(cpu);
    trendHistory.memory = align(memory);
    trendHistory.errors = align(errors);
  } catch (_) {
    lsRemove(LS_TREND_CACHE_KEY);
  }
}

function saveTrendHistoryCache() {
  try {
    const payload = {
      schemaVersion: CACHE_SCHEMA_VERSION,
      savedAt: Date.now(),
      labels: trendHistory.labels,
      cpu: trendHistory.cpu,
      memory: trendHistory.memory,
      errors: trendHistory.errors,
    };
    const s = JSON.stringify(payload);
    if (s.length > 500_000) return;
    localStorage.setItem(LS_TREND_CACHE_KEY, s);
  } catch (_) {
    /* quota */
  }
}

/** Sync infrastructure line chart from in-memory trend history (after cache hydrate, before live tick). */
function applyTrendHistoryToLineChart() {
  if (!ensureCharts() || !charts.line) return;
  charts.line.data.labels = [...trendHistory.labels];
  charts.line.data.datasets[0].data = [...trendHistory.cpu];
  charts.line.data.datasets[1].data = [...trendHistory.memory];
  charts.line.data.datasets[2].data = [...trendHistory.errors];
  charts.line.update();
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

/** Y-axis tick labels for 0–100% style charts */
function ticksPercentStyle(color, fontSize = 11) {
  return {
    color,
    font: { size: fontSize },
    callback(raw) {
      const v = typeof raw === "number" ? raw : Number(raw);
      if (!Number.isFinite(v)) return String(raw);
      return `${v}%`;
    },
  };
}

function metricsTooltipCpu(ctx) {
  const y = ctx.parsed.y;
  const name = ctx.dataset.label || "";
  if (y == null || Number.isNaN(Number(y))) return `${name}: —`;
  if (ctx.datasetIndex === 2) return `${name}: ${y}°C`;
  return `${name}: ${y}%`;
}

function metricsTooltipRam(ctx) {
  const y = ctx.parsed.y;
  const name = ctx.dataset.label || "";
  if (y == null || Number.isNaN(Number(y))) return `${name}: —`;
  const pct = `${y}%`;
  const chart = ctx.chart;
  const pts = chart._dashboardRamPts || [];
  const p = pts[ctx.dataIndex];
  if (!p) return `${name}: ${pct}`;

  const total = Number(p.system?.host_memory_total_bytes_effective);
  const dockUse = p.docker?.memory_usage;
  const lim = p.docker?.memory_limit_sum;

  if (ctx.datasetIndex === 0) {
    if (dockUse != null && Number.isFinite(total) && total > 0) {
      return `${name}: ${pct} · ${formatBytes(dockUse)} / ${formatBytes(total)}`;
    }
    if (dockUse != null) return `${name}: ${pct} · ${formatBytes(dockUse)}`;
    return `${name}: ${pct}`;
  }

  const hasAvail = pts.some((x) => x.system?.memory_percent_available != null);
  if (hasAvail) {
    if (Number.isFinite(total) && total > 0) {
      const approx = Math.round((total * Number(y)) / 100);
      return `${name}: ${pct} · ~${formatBytes(approx)} avail of ${formatBytes(total)}`;
    }
    return `${name}: ${pct}`;
  }
  if (pts.some((x) => x.system?.memory_percent != null)) {
    if (Number.isFinite(total) && total > 0) {
      const approx = Math.round((total * Number(y)) / 100);
      return `${name}: ${pct} · ~${formatBytes(approx)} used of ${formatBytes(total)}`;
    }
    return `${name}: ${pct}`;
  }
  if (dockUse != null && lim != null && Number(lim) > 0) {
    return `${name}: ${pct} · ${formatBytes(dockUse)} / ${formatBytes(lim)}`;
  }
  return `${name}: ${pct}`;
}

function metricsTooltipNet(ctx) {
  const y = ctx.parsed.y;
  const name = ctx.dataset.label || "";
  if (y == null || Number.isNaN(Number(y))) return `${name}: —`;
  return `${name}: ${y} Mb/s`;
}

function metricsTooltipIops(ctx) {
  const y = ctx.parsed.y;
  const name = ctx.dataset.label || "";
  if (y == null || Number.isNaN(Number(y))) return `${name}: —`;
  if (ctx.dataset.yAxisID === "y1") return `${name}: ${y} Mb/s`;
  return `${name}: ${y}`;
}

/**
 * Y-axis bounds for temperature (°C). Flat or narrow samples use a minimum span so small
 * fluctuations are visible; wider ranges get modest padding only (avoids 45–65°C for ~53°C data).
 */
function tempAxisBounds(sTemp) {
  const nums = sTemp.filter((x) => x != null && x !== "" && !Number.isNaN(Number(x))).map((x) => Number(x));
  if (!nums.length) return { min: 0, max: 90 };

  const lo = Math.min(...nums);
  const hi = Math.max(...nums);
  const span = hi - lo;
  const ABS_MAX = 115;
  /** Minimum axis height (°C) when data is constant or nearly constant */
  const MIN_VISIBLE_SPAN = 5;

  if (span <= 0.0001) {
    const half = MIN_VISIBLE_SPAN / 2;
    return {
      min: Math.max(0, lo - half),
      max: Math.min(ABS_MAX, lo + half),
    };
  }

  if (span < MIN_VISIBLE_SPAN) {
    const extra = (MIN_VISIBLE_SPAN - span) / 2;
    let vmin = lo - extra;
    let vmax = hi + extra;
    if (vmin < 0) {
      vmax -= vmin;
      vmin = 0;
    }
    if (vmax > ABS_MAX) {
      vmin -= vmax - ABS_MAX;
      vmax = ABS_MAX;
      vmin = Math.max(0, vmin);
    }
    return { min: vmin, max: vmax };
  }

  const pad = Math.max(0.35, span * 0.12);
  return {
    min: Math.max(0, lo - pad),
    max: Math.min(ABS_MAX, hi + pad),
  };
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

/**
 * Chart Y max from one or more series: tight headroom so small spikes are visible (no fixed 0–6 band).
 * @param {object} [opts]
 * @param {number|null} [opts.cap] — e.g. 100 for %
 * @param {number} [opts.emptyDefault] — when all series are empty/zero
 * @param {number} [opts.headroom] — multiply max by this before rounding
 * @param {number} [opts.minCeiling] — smallest axis max when there is data (avoids 0–0.01 flicker)
 */
function computeLinearYMax(seriesList, opts = {}) {
  const { cap = null, emptyDefault = 1, headroom = 1.22, minCeiling = 0 } = opts;
  const mx = maxInSeriesList(seriesList);
  if (mx <= 0) return emptyDefault;
  let hi = mx * headroom;
  if (hi < 0.5) {
    hi = Math.max(minCeiling || 0.05, Math.ceil(hi * 100) / 100);
  } else if (hi < 3) {
    hi = Math.ceil(hi * 20) / 20;
  } else if (hi < 15) {
    hi = Math.ceil(hi * 10) / 10;
  } else {
    hi = Math.ceil(hi);
  }
  if (minCeiling > 0 && hi < minCeiling) hi = minCeiling;
  const out = hi;
  return cap != null ? Math.min(cap, out) : out;
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
 * Second RAM line: MemAvailable/MemTotal (% still considered available by kernel) when /proc works —
 * visually distinct from Docker Σ/MemTotal. Fallback: host “used” %, then Docker vs cgroup limits.
 */
function ramChartSecondLine(pts) {
  const hasAvail = pts.some((p) => p.system?.memory_percent_available != null);
  if (hasAvail) return pts.map((p) => p.system?.memory_percent_available ?? null);
  const hasProcUsed = pts.some((p) => p.system?.memory_percent != null);
  if (hasProcUsed) return pts.map((p) => p.system?.memory_percent ?? null);
  return pts.map((p) => p.docker?.memory_percent_of_limits ?? p.docker?.memory_percent ?? null);
}

function ramSecondLineLabel(pts) {
  if (pts.some((p) => p.system?.memory_percent_available != null)) return "Approx. RAM available %";
  if (pts.some((p) => p.system?.memory_percent != null)) return "System RAM used %";
  return "Docker / Σ cgroup limits %";
}

function computeRamYMax(pts) {
  const a = dockerRamLineHost(pts);
  const b = ramChartSecondLine(pts);
  return computeLinearYMax([a, b], { cap: 100, emptyDefault: 100, minCeiling: 0.5 });
}

function computeCpuYMax(pts) {
  const dock = pts.map((p) => p.docker?.cpu_percent ?? null);
  const sys = pts.map((p) => p.system?.cpu_percent ?? null);
  return computeLinearYMax([dock, sys], { cap: 100, emptyDefault: 100, minCeiling: 0.5 });
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

/** Percent plus byte sizes (GB when large enough) for Docker Σ vs MemTotal or vs limits. */
function formatRamPctWithBytes(pct, usedBytes, totalBytes) {
  const p =
    pct != null && pct !== "" && !Number.isNaN(Number(pct)) ? `${Number(pct).toFixed(1)}%` : "—";
  const u = usedBytes != null ? Number(usedBytes) : NaN;
  const t = totalBytes != null ? Number(totalBytes) : NaN;
  if (Number.isFinite(u) && u >= 0 && Number.isFinite(t) && t > 0) {
    return `${p} · ${formatBytes(u)} / ${formatBytes(t)}`;
  }
  if (Number.isFinite(u) && u >= 0) return `${p} · ${formatBytes(u)}`;
  return p;
}

/** Used / limit in GB (2 decimal places) for hosted-app RAM display. */
function formatRamGbUsedLimit(usedBytes, limitBytes) {
  const toGb = (b) => {
    const n = Number(b);
    if (!Number.isFinite(n) || n < 0) return "—";
    return `${(n / 1024 ** 3).toFixed(2)} GB`;
  };
  const u = toGb(usedBytes);
  const l = toGb(limitBytes);
  if (u === "—" && l === "—") return "— / —";
  return `${u} / ${l}`;
}

/** RAM % of MemTotal → approximate bytes (exact when series is MemAvailable or kernel used%). */
function formatRamPctOfTotalPhrase(label, pct, totalBytes) {
  const p =
    pct != null && pct !== "" && !Number.isNaN(Number(pct)) ? `${Number(pct).toFixed(1)}%` : "—";
  const t = totalBytes != null ? Number(totalBytes) : NaN;
  const pv = Number(pct);
  if (!Number.isFinite(t) || t <= 0 || !Number.isFinite(pv)) return `${label} ${p}`;
  const approxBytes = Math.round((t * pv) / 100);
  return `${label} ${p} · ~${formatBytes(approxBytes)} of ${formatBytes(t)}`;
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

function escapeAttr(s) {
  return String(s || "").replaceAll("&", "&amp;").replaceAll('"', "&quot;");
}

function isDashboardLocalHostname(hostname) {
  const h = String(hostname || "").toLowerCase();
  return h === "localhost.lh" || h === "localhost" || h === "127.0.0.1" || h === "::1";
}

/**
 * Use on dashboard-generated <a href>: new tab for other *.lh services and external sites;
 * same tab for this app (localhost.lh, or direct http://localhost:port).
 */
function externalNavigationAttrs(href) {
  if (!href || href.startsWith("#")) return "";
  try {
    const u = new URL(href, window.location.href);
    if (u.protocol !== "http:" && u.protocol !== "https:") return "";
    if (isDashboardLocalHostname(u.hostname)) return "";
    return ' target="_blank" rel="noopener noreferrer"';
  } catch {
    return "";
  }
}

/** Markdown doc links: open in a new tab when leaving the dashboard origin. */
function docLinkShouldOpenNewTab(href) {
  return externalNavigationAttrs(href).length > 0;
}

/** repo-relative path → catalog doc id (filled when Docs tab loads). */
let docPathToId = new Map();

function normalizeRepoRelPath(p) {
  let s = String(p || "")
    .trim()
    .replace(/\\/g, "/");
  if (s.startsWith("./")) s = s.slice(2);
  const parts = s.split("/").filter((x) => x && x !== ".");
  const out = [];
  for (const x of parts) {
    if (x === "..") out.pop();
    else out.push(x);
  }
  return out.join("/");
}

/**
 * Resolve markdown href relative to current doc file path (repo-relative, e.g. docs/SETUP.md).
 * @returns {{ kind: string, hash?: string, repoPath?: string }}
 */
function resolveDocMarkdownHref(baseRelPath, href) {
  const raw = String(href || "").trim();
  const hashIdx = raw.indexOf("#");
  const pathPart = hashIdx >= 0 ? raw.slice(0, hashIdx) : raw;
  const hash = hashIdx >= 0 ? raw.slice(hashIdx) : "";
  if (!pathPart && hash) return { kind: "same-doc-hash", hash };
  if (!pathPart) return { kind: "empty" };
  if (/^[a-z][a-z0-9+.-]*:/i.test(pathPart)) {
    const proto = pathPart.split(":")[0].toLowerCase();
    if (proto !== "http" && proto !== "https") return { kind: "external" };
    try {
      const u = new URL(pathPart);
      if (!isDashboardLocalHostname(u.hostname)) return { kind: "external" };
    } catch {
      return { kind: "external" };
    }
    return { kind: "external" };
  }
  if (pathPart.startsWith("//")) return { kind: "external" };
  const baseDir = baseRelPath.includes("/") ? baseRelPath.replace(/[^/]+$/, "") : "";
  try {
    const baseUrl = `http://doc.invalid/${baseDir}`;
    const resolved = new URL(pathPart, baseUrl);
    let p = resolved.pathname.replace(/^\/+/, "");
    p = normalizeRepoRelPath(p);
    return { kind: "relative-md", repoPath: p, hash };
  } catch {
    return { kind: "external" };
  }
}

function scrollDocToHash(rootEl, hash) {
  if (!rootEl || !hash || hash === "#") return;
  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  let decoded = raw;
  try {
    decoded = decodeURIComponent(raw);
  } catch {
    /* keep raw */
  }
  const tryFrags = raw === decoded ? [raw] : [raw, decoded];
  const escId =
    typeof CSS !== "undefined" && CSS.escape ? (s) => CSS.escape(s) : (s) => String(s).replace(/([^a-zA-Z0-9_-])/g, "\\$1");
  requestAnimationFrame(() => {
    for (const frag of tryFrags) {
      if (!frag) continue;
      let el = null;
      try {
        el = rootEl.querySelector(`#${escId(frag)}`);
      } catch {
        /* invalid id selector */
      }
      if (!el) {
        rootEl.querySelectorAll("a[name]").forEach((node) => {
          const n = node.getAttribute("name");
          if (n === frag) el = node;
        });
      }
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "start" });
        break;
      }
    }
  });
}

function docContentLinkClick(e) {
  const a = e.target.closest("a[href]");
  if (!a) return;
  const docRoot = document.getElementById("docContent");
  if (!docRoot?.contains(a)) return;
  const href = a.getAttribute("href");
  if (!href || href.startsWith("javascript:")) return;
  if (docLinkShouldOpenNewTab(href)) return;

  const curPath = normalizeRepoRelPath(window.__docCurrentRelPath || "");
  const parsed = resolveDocMarkdownHref(window.__docCurrentRelPath || "", href);

  if (parsed.kind === "same-doc-hash") {
    e.preventDefault();
    scrollDocToHash(docRoot, parsed.hash);
    return;
  }
  if (parsed.kind !== "relative-md") return;

  let repoPath = parsed.repoPath;
  let docId = docPathToId.get(repoPath);
  if (!docId && repoPath && !repoPath.endsWith(".md") && !repoPath.endsWith(".markdown")) {
    docId = docPathToId.get(`${repoPath}.md`);
  }
  if (!docId) return;

  if (repoPath === curPath) {
    e.preventDefault();
    if (parsed.hash) scrollDocToHash(docRoot, parsed.hash);
    return;
  }

  e.preventDefault();
  loadDocContent(docId, { hash: parsed.hash || "" });
}

function initDocPanelLinkNavigation() {
  const el = document.getElementById("docContent");
  if (!el || el.dataset.internalNavBound === "1") return;
  el.dataset.internalNavBound = "1";
  el.addEventListener("click", docContentLinkClick);
}

function setDocSidebarActive(id) {
  document.querySelectorAll("#docSidebar [data-doc]").forEach((b) => {
    const on = b.getAttribute("data-doc") === id;
    b.classList.toggle("doc-link--active", on);
  });
}

function decorateDocsProseLinks(rootEl) {
  if (!rootEl) return;
  rootEl.querySelectorAll("a[href]").forEach((a) => {
    const href = a.getAttribute("href");
    if (!href) return;
    if (docLinkShouldOpenNewTab(href)) {
      a.setAttribute("target", "_blank");
      a.setAttribute("rel", "noopener noreferrer");
    }
  });
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
                  const ua = escapeAttr(chk.url);
                  const xt = externalNavigationAttrs(chk.url);
                  return `<span class="url-check"><a href="${ua}"${xt}${err}>${escapeHtml(chk.url)}</a> <span class="pill ${st}">${ok ? "OK" : "ERR"}${code}</span></span>`;
                })
              : (it.urls || []).map((u) => {
                  const ua = escapeAttr(u);
                  const xt = externalNavigationAttrs(u);
                  return `<span class="url-check"><a href="${ua}"${xt}>${escapeHtml(u)}</a></span>`;
                });
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

async function loadDocsCatalog(preferredDocId = null) {
  const sb = document.getElementById("docSidebar");
  const content = document.getElementById("docContent");
  if (!sb || !content) return;
  initDocPanelLinkNavigation();
  sb.innerHTML = "<div class='muted'>Loading catalog…</div>";
  try {
    const res = await fetch("/api/docs/catalog");
    const data = await res.json();
    const modules = data.modules || [];
    docPathToId = new Map();
    modules.forEach((m) => {
      if (m.path) docPathToId.set(normalizeRepoRelPath(m.path), m.id);
    });
    const byCat = {};
    modules.forEach((m) => {
      byCat[m.category] = byCat[m.category] || [];
      byCat[m.category].push(m);
    });
    const catOrder = ["Develop", "DevOps", "Cloudflare Local", "Extending the platform", "Operations", "Overview"];
    const catRank = (c) => {
      const i = catOrder.indexOf(c);
      return i === -1 ? 500 + String(c).charCodeAt(0) : i;
    };
    sb.innerHTML = Object.keys(byCat)
      .sort((a, b) => catRank(a) - catRank(b) || a.localeCompare(b))
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
    let pick = null;
    if (preferredDocId) {
      pick = modules.find((m) => m.id === preferredDocId) || null;
    }
    if (!pick) pick = modules.find((m) => m.available) || modules[0];
    if (pick) await loadDocContent(pick.id);
    else content.innerHTML = "<p class='muted'>No documentation modules configured.</p>";
  } catch (e) {
    sb.innerHTML = "";
    content.innerHTML = `<p class='muted'>${escapeHtml(String(e))}</p>`;
  }
}

/** Switch to Docs tab and optionally open a catalog entry (e.g. from Develop tab). */
function openDocumentationDoc(docId) {
  activateTab("docsTab", { preferredDocId: docId || null });
}

async function loadDocContent(id, opts = {}) {
  const content = document.getElementById("docContent");
  const toolbar = document.getElementById("docToolbar");
  if (!content || !id) return;
  setDocSidebarActive(id);
  content.innerHTML = "<p class='muted'>Loading…</p>";
  try {
    const res = await fetch(`/api/docs/content?id=${encodeURIComponent(id)}`);
    const data = await res.json();
    if (!data.ok) {
      content.innerHTML = `<p class='muted'>${escapeHtml(data.error || "Error")}</p>`;
      return;
    }
    window.__docCurrentRelPath = data.path || "";
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
    decorateDocsProseLinks(content);
    content.classList.add("prose");
    const syn = data.synthetic ? " (preview — mount /project for full file)" : "";
    toolbar.innerHTML = `
      <span><strong>${escapeHtml(data.title || "")}</strong>${escapeHtml(syn)}</span>
      <button type="button" id="docReloadBtn">Reload</button>`;
    document.getElementById("docReloadBtn")?.addEventListener("click", () => loadDocContent(id));
    if (opts.hash) scrollDocToHash(content, opts.hash);
  } catch (e) {
    content.innerHTML = `<p class='muted'>${escapeHtml(String(e))}</p>`;
  }
}

function renderDevelopCards() {
  const el = document.getElementById("developCards");
  if (!el || el.dataset.rendered === "1") return;
  el.dataset.rendered = "1";
  el.innerHTML = `
    <div class="card dev-card dev-card--readme">
      <strong>Local ecosystem</strong>
      <p class="muted small" style="margin:8px 0 0">
        Root <code>README.md</code> — stack overview, <code>*.lh</code> URLs, CLI, and links to setup / deployment docs.
      </p>
      <button type="button" class="dev-open-doc" data-doc-id="ecosystem-readme">Open README in Docs viewer</button>
    </div>
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
        <li><code>GET /api/ollama/models</code> — all tagged models, pinned file, /api/ps + version summary</li>
        <li><code>GET /api/ollama/model/inspect?model=…</code> — full <code>/api/show</code> (control token header)</li>
        <li><code>GET /api/ollama/backups</code> — list manifest JSON files</li>
        <li>
          <code>POST /api/ollama/models/action</code> — pull, pull_all, warm, unload, unload_all, delete, pin, unpin, set_pinned, clear_pinned,
          backup_manifest, list_backups, restore_backup (body <code>filename</code>)
        </li>
      </ul>
    </div>
    <div class="card dev-card">
      <strong>More in Docs</strong>
      <p class="muted small" style="margin:8px 0 0">
        The <strong>Docs</strong> tab lists all guides (DevOps, Cloudflare local, development playbook, etc.).
      </p>
      <button type="button" class="dev-open-doc" data-doc-id="dev-playbook">Open development playbook</button>
    </div>`;
  el.querySelectorAll(".dev-open-doc").forEach((btn) => {
    btn.addEventListener("click", () => openDocumentationDoc(btn.getAttribute("data-doc-id")));
  });
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

/** Map docker-py `container.status` to engine donut segment (matches Docker engine counts). */
function classifyEngineContainerStatus(status) {
  const s = String(status || "").toLowerCase();
  if (s === "paused") return "paused";
  if (s === "running" || s === "restarting") return "running";
  return "stopped";
}

/** Build sorted name lists per segment for engine chart tooltips. */
function bucketEngineContainerNames(containers) {
  const by = { running: [], paused: [], stopped: [] };
  for (const c of containers || []) {
    const name = c?.name;
    if (!name) continue;
    by[classifyEngineContainerStatus(c.status)].push(name);
  }
  by.running.sort((a, b) => a.localeCompare(b));
  by.paused.sort((a, b) => a.localeCompare(b));
  by.stopped.sort((a, b) => a.localeCompare(b));
  return by;
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
            ticks: ticksPercentStyle("#bae6fd", 10),
          },
          y1: {
            type: "linear",
            position: "right",
            display: true,
            beginAtZero: false,
            min: 0,
            max: 90,
            grid: { display: false },
            ticks: { color: THEME.temp, font: { size: 10 }, callback: (v) => `${v}°C` },
          },
        },
        plugins: {
          legend: overviewLegend,
          tooltip: { callbacks: { label: metricsTooltipCpu } },
        },
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
          { label: "Approx. RAM available %", data: [], ...sysLine },
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
            ticks: ticksPercentStyle("#fbcfe8", 10),
          },
        },
        plugins: {
          legend: overviewLegend,
          tooltip: { callbacks: { label: metricsTooltipRam } },
        },
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
        labels: ["R2", "KV", "D1", "Wrk", "Br", "Au"],
        datasets: [
          {
            label: "Reachable",
            data: [0, 0, 0, 0, 0, 0],
            backgroundColor: ["#a78bfa", "#a78bfa", "#a78bfa", "#a78bfa", "#a78bfa", "#a78bfa"],
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
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 8, font: { size: 10 } } },
          tooltip: {
            bodySpacing: 2,
            callbacks: {
              footer(tooltipItems) {
                const ti = tooltipItems[0];
                if (!ti) return "";
                const chart = ti.chart;
                const idx = ti.dataIndex;
                const keys = ["running", "paused", "stopped"];
                const key = keys[idx];
                const names = chart._engineNames?.[key] ?? [];
                const max = 35;
                if (names.length === 0) {
                  return "No containers listed";
                }
                const lines = names.slice(0, max).map((n) => `• ${n}`);
                if (names.length > max) {
                  lines.push(`• … +${names.length - max} more`);
                }
                return lines.join("\n");
              },
            },
          },
        },
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
  const dt = s.docker_totals_all_running || {};
  const ref = data.reference || {};
  const d = data.docker_overview?.counts || {};
  const host = data.docker_overview?.host || {};
  const svc = cf?.services || {};
  const cfUp = [svc.r2, svc.kv, svc.d1, svc.workers, svc.browser, svc.autoscale].filter((x) => x?.reachable).length;
  // Prefer live docker_totals from this overview response so KPIs match Infrastructure / Deep metrics.
  const dockCpu = dt.cpu_percent ?? lastPt?.docker?.cpu_percent;
  const dockRam = dt.memory_percent ?? lastPt?.docker?.memory_percent;
  const cpuShow = dockCpu != null ? `${Number(dockCpu).toFixed(1)}%` : "—";
  const ramTotalEff =
    dt.host_memory_total_bytes_effective ||
    lastPt?.system?.host_memory_total_bytes_effective ||
    host.memory_total ||
    null;
  const ramUsage = dt.memory_usage ?? lastPt?.docker?.memory_usage;
  const ramShow =
    dockRam != null || ramUsage != null
      ? formatRamPctWithBytes(dockRam, ramUsage, ramTotalEff && ramTotalEff > 0 ? ramTotalEff : null)
      : "—";

  mount.innerHTML = `
    <div class="kpi-cell">
      <div class="kpi-value hl-${s.level || "unknown"}">${(s.level || "—").toUpperCase()}</div>
      <div class="kpi-label">${s.services_running ?? "—"}/${s.services_total ?? "—"} services</div>
    </div>
    <div class="kpi-cell">
      <div class="kpi-value">${cpuShow}</div>
      <div class="kpi-label">Docker CPU <span class="kpi-sublabel">all running ÷ vCPUs</span></div>
    </div>
    <div class="kpi-cell">
      <div class="kpi-value">${ramShow}</div>
      <div class="kpi-label">Docker RAM <span class="kpi-sublabel">Σ usage ÷ MemTotal</span></div>
    </div>
    <div class="kpi-cell">
      <div class="kpi-value">${data.healthy_urls ?? "—"}/${data.url_count ?? "—"}</div>
      <div class="kpi-label">URL probes</div>
    </div>
    <div class="kpi-cell">
      <div class="kpi-value">${cfUp}/6</div>
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
  const cfUp = [svc.r2, svc.kv, svc.d1, svc.workers, svc.browser, svc.autoscale].filter((x) => x?.reachable).length;
  const parts = [
    `<span class="chip">Reference — all <code>*.lh</code> links</span>`,
    `<span class="chip">Infrastructure — engine &amp; tables</span>`,
    `<span class="chip">Metrics — net &amp; IOPS</span>`,
    `<span class="chip">Control — lifecycle</span>`,
  ];
  if (alerts > 0) {
    parts.unshift(`<span class="chip chip--warn">${alerts} alert(s) — see Infrastructure</span>`);
  }
  if (cfUp < 6) {
    parts.unshift(`<span class="chip chip--bad">CF local ${cfUp}/6</span>`);
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
  const hasAvailRam = pts.some((p) => p.system?.memory_percent_available != null);
  const hasProcRamUsed = pts.some((p) => p.system?.memory_percent != null);

  renderOverviewKpi(data, cf, pts.length ? pts[pts.length - 1] : null);
  renderOverviewChips(data, cf);

  const cpuMeta = document.getElementById("overviewCpuMeta");
  const ramMeta = document.getElementById("overviewRamMeta");
  const last = pts.length ? pts[pts.length - 1] : null;
  const s = data.system_status || {};
  const dt = s.docker_totals_all_running || {};
  const tempStr = last?.system?.cpu_temp_c_max != null ? `${last.system.cpu_temp_c_max}°C` : "—";
  if (cpuMeta) {
    const dcpu = last?.docker?.cpu_percent ?? dt.cpu_percent;
    const draw = last?.docker?.cpu_sum_raw ?? dt.cpu_sum_raw;
    cpuMeta.textContent = last
      ? `Now · Docker ${dcpu ?? "—"}% (raw Σ ${draw ?? "—"}%) · System ${last.system?.cpu_percent ?? "—"}% · Temp ${tempStr}`
      : `Same as Deep metrics · Docker ${dcpu ?? "—"}% (all containers ÷ vCPUs) · Temp ${tempStr}`;
  }
  if (ramMeta) {
    const hostPct = last?.docker?.memory_percent_of_host ?? last?.docker?.memory_percent ?? dt.memory_percent;
    const totalEff =
      last?.system?.host_memory_total_bytes_effective ||
      dt.host_memory_total_bytes_effective ||
      data.docker_overview?.host?.memory_total ||
      0;
    const dockUse = last?.docker?.memory_usage ?? dt.memory_usage;
    const primary = formatRamPctWithBytes(hostPct, dockUse, totalEff > 0 ? totalEff : null);
    let secondPart = "—";
    if (hasAvailRam) {
      const v = last?.system?.memory_percent_available;
      secondPart =
        v != null && v !== ""
          ? formatRamPctOfTotalPhrase("Avail", v, totalEff > 0 ? totalEff : null)
          : "—";
    } else if (hasProcRamUsed) {
      const v = last?.system?.memory_percent;
      secondPart =
        v != null && v !== ""
          ? formatRamPctOfTotalPhrase("Host used", v, totalEff > 0 ? totalEff : null)
          : "—";
    } else {
      const v = last?.docker?.memory_percent_of_limits ?? dt.memory_percent_of_limits;
      const limSum = last?.docker?.memory_limit_sum ?? dt.memory_limit_sum;
      secondPart =
        v != null && v !== ""
          ? formatRamPctWithBytes(v, dockUse, limSum != null && limSum > 0 ? limSum : null)
          : "—";
    }
    ramMeta.textContent = last
      ? `Now · Docker Σ / MemTotal ${primary} · ${secondPart}`
      : `Same as Deep metrics · Docker RAM ${formatRamPctWithBytes(dt.memory_percent, dt.memory_usage, dt.host_memory_total_bytes_effective || data.docker_overview?.host?.memory_total || null)} vs MemTotal`;
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
  overviewCharts.ram._dashboardRamPts = pts;
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
    { k: "browser", l: "Br" },
    { k: "autoscale", l: "Au" },
  ];
  const upVals = keys.map(({ k }) => (cfSvc[k]?.reachable ? 100 : 0));
  const cfPaletteUp = ["#22d3ee", "#a78bfa", "#34d399", "#fbbf24", "#fb923c", "#f472b6"];
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
  overviewCharts.engine._engineNames = bucketEngineContainerNames(data.containers);
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
  loadOllamaBackupSelect();
}

function ollamaApiHeaders(jsonBody = false) {
  const h = { "X-Control-Token": controlToken() };
  if (jsonBody) h["Content-Type"] = "application/json";
  return h;
}

function formatOllamaExpires(iso) {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return String(iso).slice(0, 19);
  const min = Math.round((t - Date.now()) / 60000);
  if (min < 0) return "due unload";
  if (min < 90) return `${min}m`;
  return `${Math.round(min / 60)}h`;
}

function showOllamaInspectModal(title, obj) {
  initAppModal();
  const overlay = document.getElementById("appModalOverlay");
  const cancel = document.getElementById("appModalCancel");
  const primary = document.getElementById("appModalPrimary");
  const msg = document.getElementById("appModalMessage");
  document.getElementById("appModalTitle").textContent = title;
  msg.textContent = "";
  const pre = document.createElement("pre");
  pre.className = "ollama-inspect-pre";
  pre.textContent = JSON.stringify(obj, null, 2);
  msg.appendChild(pre);
  overlay.dataset.mode = "alert";
  overlay.classList.add("app-modal-overlay--inspect");
  cancel.classList.add("is-hidden");
  primary.textContent = "Close";
  _appModalSetPrimaryVariant(primary, "primary");
  return new Promise((resolve) => {
    _appModalResolve = () => resolve();
    overlay.hidden = false;
    primary.focus();
  });
}

async function loadOllamaBackupSelect() {
  const sel = document.getElementById("ollamaBackupSelect");
  if (!sel) return;
  try {
    const res = await fetch("/api/ollama/backups", { headers: ollamaApiHeaders(false) });
    const data = await res.json().catch(() => ({}));
    const cur = sel.value;
    sel.innerHTML = '<option value="">Select backup manifest…</option>';
    if (data.ok && Array.isArray(data.backups)) {
      for (const b of data.backups) {
        const fn = b.filename || "";
        if (!fn) continue;
        const opt = document.createElement("option");
        opt.value = fn;
        opt.textContent = `${fn}${b.size != null ? ` (${formatBytes(b.size)})` : ""}`;
        sel.appendChild(opt);
      }
    }
    if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
  } catch (_) {
    /* ignore */
  }
}

function ollamaActionWrap(act, label, apiModel, canonical, variant, { disabled, activeDot, title } = {}) {
  const dis = disabled ? " disabled" : "";
  const tit = title ? ` title="${escapeAttr(title)}"` : "";
  const dot = activeDot ? '<span class="action-state-dot" aria-hidden="true"></span>' : "";
  return `<span class="ollama-act-wrap"><button type="button" class="ollama-act ollama-act--${variant}" data-ollama-act="${escapeAttr(
    act,
  )}" data-ollama-model="${escapeAttr(apiModel)}" data-ollama-canonical="${escapeAttr(canonical || "")}"${dis}${tit}>${escapeHtml(label)}</button>${dot}</span>`;
}

function controlRuntimeActionState(action, rt) {
  const a = (action || "").toLowerCase();
  // Bulk stack cards (ecosystem / cf-all / infra-all) use running: null — every action stays available.
  // Compose apps (leco-registry) use kind "stack" too but set running true/false from compose ps.
  if (rt.running === null || rt.running === undefined) return { disabled: false, activeDot: false };
  const st = (rt.status || "").toLowerCase();
  const partial = st === "partial";
  const running = rt.running === true;
  const paused = st === "paused";
  const up = running || paused;

  if (a === "start") {
    if (partial) return { disabled: false, activeDot: false, title: "" };
    return { disabled: up, activeDot: up, title: up ? "Already running" : "" };
  }
  if (a === "unpause")
    return {
      disabled: !paused,
      activeDot: running && !paused,
      title: !paused ? "Not paused" : "",
    };
  if (a === "pause") return { disabled: !running || paused, activeDot: paused, title: paused ? "Already paused" : "" };
  if (a === "stop") return { disabled: !up, activeDot: !up, title: !up ? "Already stopped" : "" };
  if (a === "restart") {
    const hasWork = running || partial;
    return { disabled: !hasWork, activeDot: false, title: !hasWork ? "Nothing running" : "" };
  }
  return { disabled: false, activeDot: false, title: "" };
}

function controlActionButtonHtml(action, buttonClass, targetId, cardLabel, rt) {
  const { disabled, activeDot, title } = controlRuntimeActionState(action, rt);
  const dis = disabled ? " disabled" : "";
  const tit = title ? ` title="${escapeAttr(title)}"` : "";
  const dot = activeDot ? '<span class="action-state-dot" aria-hidden="true"></span>' : "";
  return `<span class="ctrl-act-wrap"><button type="button" class="${buttonClass}" data-control-target="${escapeAttr(
    targetId,
  )}" data-action="${escapeAttr(action)}" data-label="${escapeAttr(cardLabel)}"${dis}${tit}>${escapeHtml(action)}</button>${dot}</span>`;
}

async function loadOllamaModelsPanel() {
  const panel = document.getElementById("ollamaModelsPanel");
  const sum = document.getElementById("ollamaModelsSummary");
  const ins = document.getElementById("ollamaModelsInsights");
  if (!panel) return;
  try {
    const res = await fetch("/api/ollama/models");
    const data = await res.json();
    const reach = data.ollama_reachable;
    const ver = data.server_version || {};
    const verStr = [ver.version, ver.ollama_version].filter(Boolean).join(" · ") || "—";
    const pinnedList = data.pinned || [];
    const runningN = data.running_count ?? 0;

    const pullAllBtn = document.getElementById("ollamaPullAllBtn");
    if (pullAllBtn) {
      pullAllBtn.disabled = !reach || pinnedList.length === 0;
      pullAllBtn.title = !reach ? "Ollama unreachable" : pinnedList.length === 0 ? "No pinned models" : "";
    }
    const unloadAllBtn = document.getElementById("ollamaUnloadAllBtn");
    if (unloadAllBtn) {
      unloadAllBtn.disabled = !reach || runningN === 0;
      unloadAllBtn.title = !reach ? "Ollama unreachable" : runningN === 0 ? "No models in RAM" : "";
    }

    if (sum) {
      sum.textContent = reach
        ? `API ${data.ollama_base || "—"} · ${data.installed_count ?? 0} on disk · ${runningN} in RAM · ${pinnedList.length} pinned`
        : `Ollama unreachable from dashboard (check <code>ollama</code> on lh-network): ${data.ollama_base || "http://ollama:11434"}`;
    }
    if (ins) {
      ins.textContent = reach
        ? `Server: ${verStr} · manifest: ${data.pinned_file || "—"}`
        : "Start the Ollama container, then refresh.";
    }
    const rows = data.rows || [];
    if (!rows.length) {
      panel.innerHTML = reach
        ? `<p class="muted">No models installed yet. Use <strong>Pull by name</strong> above or <code>ollama pull &lt;model&gt;</code> on the host. Pinned names without a local blob still appear here once added to the pinned file.</p>`
        : `<p class="muted">Cannot list models until Ollama is reachable.</p>`;
      return;
    }
    const thead = `<thead><tr><th>Model</th><th>Pinned</th><th>Disk</th><th>RAM</th><th>Size</th><th>Params / quant</th><th>VRAM</th><th>Keep-alive</th><th>Actions</th></tr></thead>`;
    const tbody = rows
      .map((r) => {
        const name = escapeHtml(r.name);
        const apiModel = r.api_model || r.name;
        const canonical = r.canonical || "";
        const p = !!r.pinned;
        const i = !!r.installed;
        const run = !!r.running;
        const pq = [r.parameter_size, r.quantization_level].filter(Boolean).join(" · ") || "—";
        const vram = r.size_vram != null ? formatBytes(r.size_vram) : run ? "0 B" : "—";
        const exp = run ? formatOllamaExpires(r.expires_at) : "—";
        const acts = [
          ollamaActionWrap("pin", "Pin", apiModel, canonical, "safe", {
            disabled: p,
            activeDot: p,
            title: p ? "Already pinned" : "",
          }),
          ollamaActionWrap("unpin", "Unpin", apiModel, canonical, "caution", {
            disabled: !p,
            activeDot: !p,
            title: !p ? "Not pinned" : "",
          }),
          ollamaActionWrap("warm", "Load", apiModel, canonical, "safe", {
            disabled: run || !i,
            activeDot: run,
            title: run ? "Already in RAM" : !i ? "Not on disk — pull first" : "Load into RAM",
          }),
          ollamaActionWrap("unload", "Off", apiModel, canonical, "caution", {
            disabled: !run,
            activeDot: !run,
            title: !run ? "Not loaded in RAM" : "Unload from RAM",
          }),
          ollamaActionWrap("pull", "Pull", apiModel, canonical, "safe", {
            disabled: i,
            activeDot: i,
            title: i ? "Already on disk" : "",
          }),
          ollamaActionWrap("reinstall", "Reinstall", apiModel, canonical, "ops", {
            disabled: !i,
            activeDot: !i,
            title: !i ? "Not on disk" : "Re-pull from registry",
          }),
          ollamaActionWrap("delete", "Remove", apiModel, canonical, "destructive", {
            disabled: !i,
            activeDot: !i,
            title: !i ? "Nothing on disk to remove" : "",
          }),
          ollamaActionWrap("inspect", "Insights", apiModel, canonical, "safe", {
            disabled: !i,
            activeDot: !i,
            title: !i ? "Not on disk" : "",
          }),
        ].join(" ");
        return `<tr>
          <td><code>${name}</code></td>
          <td>${p ? "✓" : "—"}</td>
          <td>${i ? "✓" : "—"}</td>
          <td>${run ? "✓" : "—"}</td>
          <td>${r.size != null ? formatBytes(r.size) : "—"}</td>
          <td class="ollama-models-meta">${escapeHtml(pq)}</td>
          <td>${escapeHtml(vram)}</td>
          <td>${escapeHtml(exp)}</td>
          <td class="ollama-models-actions">${acts}</td>
        </tr>`;
      })
      .join("");
    panel.innerHTML = `<div class="ollama-models-scroll"><table class="ollama-models-table">${thead}<tbody>${tbody}</tbody></table></div>`;
  } catch (e) {
    if (sum) sum.textContent = `Error: ${e.message || e}`;
    if (ins) ins.textContent = "";
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
    overlay.classList.remove("app-modal-overlay--inspect");
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

async function runOllamaModelAction(action, model, extra = {}) {
  try {
    const res = await fetch("/api/ollama/models/action", {
      method: "POST",
      headers: ollamaApiHeaders(true),
      body: JSON.stringify({ action, model: model || "", token: controlToken(), ...extra }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      await showAppAlert(data.error ? JSON.stringify(data.error) : `HTTP ${res.status}`, "Ollama action failed");
      return;
    }
    if (data.note || data.path || data.restored_from) {
      await showAppAlert(
        [data.note, data.path ? `File: ${data.path}` : "", data.restored_from ? `Restored: ${data.restored_from}` : ""]
          .filter(Boolean)
          .join("\n"),
        "Ollama",
      );
    }
    loadOllamaModelsPanel();
    loadOllamaBackupSelect();
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
  document.getElementById("ollamaModelsRefreshBtn")?.addEventListener("click", () => {
    loadOllamaModelsPanel();
    loadOllamaBackupSelect();
  });
  document.getElementById("ollamaBackupBtn")?.addEventListener("click", async () => {
    const ok = await showAppConfirm({
      title: "Backup Ollama manifest",
      message: "Write JSON to .local-eco-backups (models, running, pinned list snapshot)?",
      confirmText: "Save backup",
    });
    if (!ok) return;
    runOllamaModelAction("backup_manifest", "");
  });
  document.getElementById("ollamaUnloadAllBtn")?.addEventListener("click", async () => {
    const ok = await showAppConfirm({
      title: "Unload all models from RAM",
      message: "Request unload (keep_alive=0) for every model currently in memory?",
      confirmText: "Unload all",
      danger: true,
    });
    if (!ok) return;
    runOllamaModelAction("unload_all", "");
  });
  document.getElementById("ollamaPullNameBtn")?.addEventListener("click", async () => {
    const inp = document.getElementById("ollamaPullNameInput");
    const name = (inp?.value || "").trim();
    if (!name) {
      await showAppAlert("Enter a model name (e.g. llama3.2:latest).", "Pull");
      return;
    }
    runOllamaModelAction("pull", name);
  });
  document.getElementById("ollamaRefreshBackupsBtn")?.addEventListener("click", () => loadOllamaBackupSelect());
  document.getElementById("ollamaRestoreBackupBtn")?.addEventListener("click", async () => {
    const sel = document.getElementById("ollamaBackupSelect");
    const fn = (sel?.value || "").trim();
    if (!fn) {
      await showAppAlert("Choose a backup file first (List backups).", "Restore");
      return;
    }
    const ok = await showAppConfirm({
      title: "Restore pinned list",
      message: `Overwrite ai-stack/config/ollama-pinned-models.txt with pinned names from ${fn}? Models on disk are not deleted.`,
      confirmText: "Restore pinned",
      danger: true,
    });
    if (!ok) return;
    runOllamaModelAction("restore_backup", "", { filename: fn });
  });
  document.getElementById("ollamaClearPinnedBtn")?.addEventListener("click", async () => {
    const ok = await showAppConfirm({
      title: "Clear pinned file",
      message: "Remove all model names from the pinned file? (Does not delete Ollama blobs.)",
      confirmText: "Clear pinned",
      danger: true,
    });
    if (!ok) return;
    runOllamaModelAction("clear_pinned", "");
  });
  document.getElementById("ollamaModelsPanel")?.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-ollama-act]");
    if (!btn) return;
    const act = btn.getAttribute("data-ollama-act");
    const model = btn.getAttribute("data-ollama-model") || "";
    if (act === "inspect") {
      try {
        const q = new URLSearchParams({ model });
        const res = await fetch(`/api/ollama/model/inspect?${q}`, { headers: ollamaApiHeaders(false) });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          await showAppAlert(data.error ? JSON.stringify(data.error) : `HTTP ${res.status}`, "Inspect failed");
          return;
        }
        await showOllamaInspectModal(`Insights · ${model}`, data.show || data);
      } catch (err) {
        await showAppAlert(String(err.message || err), "Error");
      }
      return;
    }
    if (act === "delete") {
      const ok = await showAppConfirm({
        title: "Remove model",
        message: `Remove "${model}" from disk? This cannot be undone.`,
        confirmText: "Remove",
        danger: true,
      });
      if (!ok) return;
    }
    if (act === "warm") {
      const ok = await showAppConfirm({
        title: "Load model into RAM",
        message: `Run a minimal generation for "${model}" with keep_alive until you unload? First load can take a while.`,
        confirmText: "Load",
      });
      if (!ok) return;
    }
    const extra = {};
    if (act === "unpin") {
      const c = (btn.getAttribute("data-ollama-canonical") || "").trim();
      if (c) extra.canonical = c;
    }
    runOllamaModelAction(act, model, extra);
  });
}

function stopHostedLogStream() {
  if (hostedLogStreamAbort) {
    hostedLogStreamAbort.abort();
    hostedLogStreamAbort = null;
  }
  hostedLogStreamStarted = false;
}

/** When checked, log panel scrolls to bottom on each content update (live or refresh). */
function hostedLogsScrollToBottomIfFollow() {
  const pre = document.getElementById("hostedAppsLogPre");
  if (!pre) return;
  const follow = document.getElementById("hostedLogFollowBottom");
  if (follow && !follow.checked) return;
  requestAnimationFrame(() => {
    pre.scrollTop = pre.scrollHeight;
  });
}

function initHostedLogFollowScrollSync() {
  const pre = document.getElementById("hostedAppsLogPre");
  const chk = document.getElementById("hostedLogFollowBottom");
  if (!pre || !chk || pre.dataset.followScrollSync === "1") return;
  pre.dataset.followScrollSync = "1";
  const syncFromScroll = () => {
    const slack = 12;
    const nearBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight <= slack;
    chk.checked = nearBottom;
  };
  pre.addEventListener("scroll", syncFromScroll, { passive: true });
}

async function startHostedLogStream() {
  if (activeTab !== "hostedAppsTab" || !hostedSelectedSlug) return;
  if (!document.getElementById("hostedLogLive")?.checked) return;
  stopHostedLogStream();
  hostedLogStreamStarted = true;
  hostedLogStreamAbort = new AbortController();
  const pre = document.getElementById("hostedAppsLogPre");
  const slug = hostedSelectedSlug;
  const u = encodeURIComponent(slug);
  const tail = document.getElementById("hostedLogTail")?.value || "200";
  const svcRaw = document.getElementById("hostedLogService")?.value || "";
  const svc = svcRaw.trim();
  const url = `/api/hosted-apps/${u}/logs/stream?tail=${encodeURIComponent(tail)}${svc ? `&service=${encodeURIComponent(svc)}` : ""}`;
  if (pre) {
    pre.textContent = "Connecting live tail (docker compose logs -f)…\n";
    hostedLogsScrollToBottomIfFollow();
  }
  try {
    const res = await fetch(url, { signal: hostedLogStreamAbort.signal });
    if (!res.ok || !res.body?.getReader) {
      if (pre) {
        pre.textContent = `Live tail failed: HTTP ${res.status}`;
        hostedLogsScrollToBottomIfFollow();
      }
      return;
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let carry = "";
    let acc = "";
    const maxChars = 450000;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      carry += dec.decode(value, { stream: true });
      const ix = carry.lastIndexOf("\n");
      if (ix >= 0) {
        acc = (acc + carry.slice(0, ix + 1)).slice(-maxChars);
        carry = carry.slice(ix + 1);
        if (pre) {
          pre.textContent = acc + carry;
          hostedLogsScrollToBottomIfFollow();
        }
      }
    }
    if (carry && pre) {
      acc = (acc + carry).slice(-maxChars);
      pre.textContent = acc;
      hostedLogsScrollToBottomIfFollow();
    }
  } catch (e) {
    const aborted = e && (e.name === "AbortError" || e.code === 20);
    if (!aborted && pre) {
      pre.textContent += `\n[live tail error: ${String(e.message || e)}]\n`;
      hostedLogsScrollToBottomIfFollow();
    }
  } finally {
    hostedLogStreamAbort = null;
    hostedLogStreamStarted = false;
  }
}

function closeHostedChartExpand() {
  const ov = document.getElementById("hostedChartExpandOverlay");
  if (hostedExpandChart) {
    try {
      hostedExpandChart.destroy();
    } catch (_) {
      /* noop */
    }
    hostedExpandChart = null;
  }
  if (ov) ov.hidden = true;
}

/** Plain Chart options for the modal — do not clone Chart instances (callbacks / internals break clone/JSON). */
function hostedExpandChartBuildOptions(key) {
  const gridColor = THEME.grid;
  const tickStyle = { color: "#e9d5ff", font: { size: 13 } };
  const legendBottom = { position: "bottom", labels: { boxWidth: 12, font: { size: 13, color: "#faf5ff" } } };
  const base = {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: "index", intersect: false },
    plugins: { legend: legendBottom },
  };
  const tooltipCpu = {
    callbacks: {
      label(ctx) {
        const y = ctx.parsed.y;
        if (y == null || Number.isNaN(Number(y))) return `${ctx.dataset.label || ""}: —`;
        return `${ctx.dataset.label}: ${Number(y).toFixed(2)}%`;
      },
    },
  };
  const tooltipMem = {
    callbacks: {
      label(ctx) {
        const y = ctx.parsed.y;
        const name = ctx.dataset.label || "";
        if (y == null || Number.isNaN(Number(y))) return `${name}: —`;
        if (ctx.datasetIndex === 0) return `${name}: ${Number(y).toFixed(2)}%`;
        return `${name}: ${Number(y).toFixed(2)} GB`;
      },
    },
  };
  if (key === "cpu") {
    return {
      ...base,
      scales: {
        x: { ticks: tickStyle, grid: { color: gridColor } },
        y: { beginAtZero: true, grid: { color: gridColor }, ticks: ticksPercentStyle("#e9d5ff", 13) },
      },
      plugins: { ...base.plugins, tooltip: tooltipCpu },
    };
  }
  if (key === "mem") {
    return {
      ...base,
      scales: {
        x: { ticks: tickStyle, grid: { color: gridColor } },
        y: {
          position: "left",
          beginAtZero: true,
          max: 100,
          grid: { color: gridColor },
          ticks: ticksPercentStyle("#e9d5ff", 13),
        },
        y1: {
          position: "right",
          beginAtZero: true,
          grid: { drawOnChartArea: false },
          ticks: {
            color: "#e9d5ff",
            font: { size: 13 },
            callback(raw) {
              const v = typeof raw === "number" ? raw : Number(raw);
              if (!Number.isFinite(v)) return String(raw);
              return `${v.toFixed(2)} GB`;
            },
          },
        },
      },
      plugins: { ...base.plugins, tooltip: tooltipMem },
    };
  }
  return {
    ...base,
    scales: {
      x: { ticks: tickStyle, grid: { color: gridColor } },
      y: { beginAtZero: true, grid: { color: gridColor }, ticks: tickStyle },
    },
  };
}

function openHostedChartExpand(key) {
  if (typeof Chart === "undefined") return;
  const src = hostedAppCharts[key];
  if (!src) return;
  const ov = document.getElementById("hostedChartExpandOverlay");
  const wrap = document.querySelector(".hosted-chart-expand-canvas-wrap");
  const titleEl = document.getElementById("hostedChartExpandTitle");
  if (!ov || !wrap || !titleEl) return;
  const titles = {
    cpu: "CPU (app stack)",
    mem: "Memory % of limits · GB used",
    net: "Net Mb/s (RX / TX)",
  };
  titleEl.textContent = titles[key] || "Chart";
  closeHostedChartExpand();

  const oldCanvas = document.getElementById("hostedChartExpandCanvas");
  if (oldCanvas && oldCanvas.parentNode === wrap) {
    const fresh = document.createElement("canvas");
    fresh.id = "hostedChartExpandCanvas";
    fresh.setAttribute("aria-label", "Expanded chart");
    wrap.replaceChild(fresh, oldCanvas);
  }

  const h = Math.min(Math.round(window.innerHeight * 0.62), 540);
  wrap.style.minHeight = `${Math.max(h, 320)}px`;

  ov.hidden = false;

  const data = {
    labels: [...src.data.labels],
    datasets: src.data.datasets.map((ds) => ({
      label: ds.label,
      data: [...ds.data],
      borderColor: ds.borderColor,
      backgroundColor: ds.backgroundColor || "transparent",
      tension: ds.tension,
      spanGaps: ds.spanGaps,
      pointRadius: ds.pointRadius ?? 2,
      pointHoverRadius: ds.pointHoverRadius ?? 4,
      borderWidth: ds.borderWidth ?? 2,
      yAxisID: ds.yAxisID || "y",
      fill: false,
    })),
  };
  const opts = hostedExpandChartBuildOptions(key);

  const buildChart = () => {
    const canvas = document.getElementById("hostedChartExpandCanvas");
    if (!canvas) return;
    if (hostedExpandChart) {
      try {
        hostedExpandChart.destroy();
      } catch (_) {
        /* noop */
      }
      hostedExpandChart = null;
    }
    hostedExpandChart = new Chart(canvas, {
      type: "line",
      data,
      options: opts,
    });
    try {
      hostedExpandChart.resize();
    } catch (_) {
      /* noop */
    }
  };
  requestAnimationFrame(() => {
    requestAnimationFrame(buildChart);
  });
}

function initHostedChartExpandModal() {
  const root = document.body;
  if (root.dataset.hostedExpandWired === "1") return;
  root.dataset.hostedExpandWired = "1";
  document.getElementById("hostedChartExpandClose")?.addEventListener("click", closeHostedChartExpand);
  document.getElementById("hostedChartExpandOverlay")?.addEventListener("click", (e) => {
    if (e.target?.id === "hostedChartExpandOverlay") closeHostedChartExpand();
  });
  document.querySelectorAll("[data-hosted-expand-chart]").forEach((btn) => {
    btn.addEventListener("click", () => openHostedChartExpand(btn.getAttribute("data-hosted-expand-chart") || ""));
  });
}

function activateTab(tabId, opts = {}) {
  if (activeTab === "hostedAppsTab" && tabId !== "hostedAppsTab") {
    hostedLogStreamLiveKey = "";
    stopHostedLogStream();
  }
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
  if (tabId === "hostedAppsTab") {
    loadHostedAppsList();
  }
  if (tabId === "routesTab") {
    loadTraefikRoutesPanel();
  }
  if (tabId === "referenceTab") {
    loadReferenceTab();
  }
  if (tabId === "docsTab") {
    loadDocsCatalog(opts.preferredDocId ?? null);
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
      <strong>Runtime Summary (Deep metrics alignment)</strong>
      <div class="row"><span>Docker CPU</span><span>${s.docker_totals_all_running?.cpu_percent ?? "—"}% <span class="muted small">(all running ÷ ${s.docker_totals_all_running?.host_cpus ?? "—"} vCPUs, raw Σ ${s.docker_totals_all_running?.cpu_sum_raw ?? "—"}%)</span></span></div>
      <div class="row"><span>Docker RAM vs MemTotal</span><span>${(() => {
        const d = s.docker_totals_all_running || {};
        const pc = d.memory_percent;
        const du = d.memory_usage;
        const mt = d.host_memory_total_bytes_effective;
        if (pc == null && pc !== 0) return "—";
        return formatRamPctWithBytes(pc, du, mt != null && mt > 0 ? mt : null);
      })()}</span></div>
      <div class="row"><span>Docker RAM vs Σ limits</span><span>${(() => {
        const d = s.docker_totals_all_running || {};
        const pc = d.memory_percent_of_limits;
        const du = d.memory_usage;
        const lm = d.memory_limit_sum;
        if (pc == null && pc !== 0) return "—";
        return formatRamPctWithBytes(pc, du, lm != null && lm > 0 ? lm : null);
      })()}</span></div>
      <div class="row"><span>Running containers sampled</span><span>${s.docker_totals_all_running?.running_container_count ?? "—"}</span></div>
      <div class="row"><span>Error lines (5m)</span><span>${s.total_error_lines || 0}</span></div>
      <div class="row"><span>Error rate</span><span>${s.total_error_rate_per_min || 0}/min</span></div>
      <p class="muted small" style="margin:0.5rem 0 0">Managed stack only (URL-probed services): CPU raw Σ ${s.aggregate_cpu_percent ?? "—"}% · RAM ${formatRamPctWithBytes(s.aggregate_memory_percent, s.aggregate_memory_usage, s.aggregate_memory_limit != null && s.aggregate_memory_limit > 0 ? s.aggregate_memory_limit : null)} of Σ cgroup limits — differs from “all containers” above.</p>
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
    ${wrapCfLocalCard(`
      <strong>Service Reachability</strong>
      <div class="row"><span>R2</span><span class="pill ${badgeColor(!!svc.r2?.reachable)}">${badgeText(!!svc.r2?.reachable)}</span></div>
      <div class="row"><span>KV</span><span class="pill ${badgeColor(!!svc.kv?.reachable)}">${badgeText(!!svc.kv?.reachable)}</span></div>
      <div class="row"><span>D1</span><span class="pill ${badgeColor(!!svc.d1?.reachable)}">${badgeText(!!svc.d1?.reachable)}</span></div>
      <div class="row"><span>Workers</span><span class="pill ${badgeColor(!!svc.workers?.reachable)}">${badgeText(!!svc.workers?.reachable)}</span></div>
      <div class="row"><span>Browser</span><span class="pill ${badgeColor(!!svc.browser?.reachable)}">${badgeText(!!svc.browser?.reachable)}</span></div>
      <div class="row"><span>Autoscaler</span><span class="pill ${badgeColor(!!svc.autoscale?.reachable)}">${badgeText(!!svc.autoscale?.reachable)}</span></div>
    `)}
    ${wrapCfLocalCard(`
      <strong>Resource Counts</strong>
      <div class="row"><span>R2 buckets</span><span>${cnt.buckets || 0}</span></div>
      <div class="row"><span>KV namespaces</span><span>${cnt.namespaces || 0}</span></div>
      <div class="row"><span>D1 databases</span><span>${cnt.databases || 0}</span></div>
      <div class="row"><span>Autoscale replicas</span><span>${cnt.autoscale_replicas || 0}</span></div>
    `)}
    ${wrapCfLocalCard(`
      <strong>Autoscaler Policy</strong>
      <div class="row"><span>Min replicas</span><span>${svc.autoscale?.status?.min_replicas ?? "-"}</span></div>
      <div class="row"><span>Max replicas</span><span>${svc.autoscale?.status?.max_replicas ?? "-"}</span></div>
      <div class="row"><span>Avg CPU</span><span>${svc.autoscale?.status?.avg_cpu_percent ?? 0}%</span></div>
      <div class="row"><span>Last events</span><span>${(svc.autoscale?.status?.events || []).length}</span></div>
    `)}
    ${wrapCfLocalCard(`
      <strong>Quick links</strong>
      ${cfDualUrlRow("R2", "http://r2.lh")}
      ${cfDualUrlRow("KV", "http://kv.lh")}
      ${cfDualUrlRow("D1", "http://d1.lh")}
      ${cfDualUrlRow("Workers", "http://workers.lh")}
      ${cfDualUrlRow("Browser", "http://browser.lh")}
      ${cfDualUrlRow("Autoscaler", "http://autoscale.lh")}
      ${cfDualUrlRow("MinIO UI", "http://minio-console.lh")}
      <div class="row muted small" style="margin-top:6px"><a href="/hub">Service hubs</a> · credentials &amp; DB GUIs</div>
    `)}
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
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        tooltip: {
          callbacks: {
            label(ctx) {
              const y = ctx.parsed.y;
              const name = ctx.dataset.label || "";
              if (y == null || Number.isNaN(Number(y))) return `${name}: —`;
              if (ctx.datasetIndex <= 1) return `${name}: ${y}%`;
              return `${name}: ${y}`;
            },
          },
        },
      },
    },
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
      scales: {
        y: { beginAtZero: true, max: 100, ticks: ticksPercentStyle("#94a3b8", 11) },
      },
      plugins: {
        tooltip: {
          callbacks: {
            label(ctx) {
              const y = ctx.parsed.y;
              const name = ctx.dataset.label || "";
              if (y == null || Number.isNaN(Number(y))) return `${name}: —`;
              return `${name}: ${y}%`;
            },
          },
        },
      },
    },
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
        y: { beginAtZero: true, max: 100, grid: { color: gridColor }, ticks: ticksPercentStyle("#e9d5ff", 11) },
        y1: {
          type: "linear",
          position: "right",
          display: true,
          beginAtZero: false,
          min: 0,
          max: 90,
          grid: { display: false },
          ticks: { color: THEME.temp, font: { size: 11 }, callback: (v) => `${v}°C` },
        },
      },
      plugins: {
        legend: legendBottom,
        tooltip: { callbacks: { label: metricsTooltipCpu } },
      },
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
        { label: "Approx. RAM available %", data: [], ...sysLine },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        x: { ticks: tickStyle, grid: { color: gridColor } },
        y: { beginAtZero: true, max: 100, grid: { color: gridColor }, ticks: ticksPercentStyle("#e9d5ff", 11) },
      },
      plugins: {
        legend: legendBottom,
        tooltip: { callbacks: { label: metricsTooltipRam } },
      },
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
        y: {
          beginAtZero: true,
          grid: { color: gridColor },
          ticks: {
            ...tickStyle,
            callback(raw) {
              const v = typeof raw === "number" ? raw : Number(raw);
              if (!Number.isFinite(v)) return String(raw);
              return `${v} Mb/s`;
            },
          },
        },
      },
      plugins: {
        legend: legendBottom,
        tooltip: { callbacks: { label: metricsTooltipNet } },
      },
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
        y: {
          beginAtZero: true,
          grid: { color: gridColor },
          ticks: {
            ...tickStyle,
            callback(raw) {
              const v = typeof raw === "number" ? raw : Number(raw);
              if (!Number.isFinite(v)) return String(raw);
              return `${v} /s`;
            },
          },
        },
        y1: {
          type: "linear",
          position: "right",
          beginAtZero: true,
          grid: { display: false },
          ticks: {
            color: "#fde047",
            font: { size: 11 },
            callback(raw) {
              const v = typeof raw === "number" ? raw : Number(raw);
              if (!Number.isFinite(v)) return String(raw);
              return `${v} Mb/s`;
            },
          },
        },
      },
      plugins: {
        legend: legendBottom,
        tooltip: { callbacks: { label: metricsTooltipIops } },
      },
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
            beginAtZero: false,
            min: 0,
            max: 90,
            grid: { color: gridColor },
            ticks: { color: THEME.temp, font: { size: 11 }, callback: (v) => `${v}°C` },
          },
        },
        plugins: {
          legend: legendBottom,
          tooltip: {
            callbacks: {
              label(ctx) {
                const y = ctx.parsed.y;
                const name = ctx.dataset.label || "";
                if (y == null || Number.isNaN(Number(y))) return `${name}: —`;
                return `${name}: ${y}°C`;
              },
            },
          },
        },
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
  metricsCharts.ram._dashboardRamPts = pts;
  metricsCharts.ram.update();

  metricsCharts.net.data.labels = labels;
  metricsCharts.net.data.datasets[0].data = d("net_total_mbps");
  metricsCharts.net.data.datasets[1].data = sNet;
  metricsCharts.net.data.datasets[1].hidden = !seriesHasNumber(sNet);
  const dockNet = d("net_total_mbps");
  if (metricsCharts.net.options.scales?.y) {
    metricsCharts.net.options.scales.y.max = computeLinearYMax([dockNet, sNet], { emptyDefault: 0.25, minCeiling: 0.05 });
  }
  metricsCharts.net.update();

  metricsCharts.iops.data.labels = labels;
  metricsCharts.iops.data.datasets[0].data = d("iops_total_est");
  metricsCharts.iops.data.datasets[1].data = sIops;
  metricsCharts.iops.data.datasets[2].data = blkMb;
  metricsCharts.iops.data.datasets[1].hidden = !seriesHasNumber(sIops);
  metricsCharts.iops.data.datasets[2].hidden = !seriesHasPositive(blkMb);
  if (metricsCharts.iops.options.scales?.y) {
    metricsCharts.iops.options.scales.y.max = computeLinearYMax([d("iops_total_est"), sIops], {
      emptyDefault: 0.5,
      minCeiling: 0.05,
    });
  }
  if (metricsCharts.iops.options.scales?.y1) {
    metricsCharts.iops.options.scales.y1.max = computeLinearYMax([blkMb], { emptyDefault: 0.25, minCeiling: 0.02 });
  }
  metricsCharts.iops.update();

  const last = pts[pts.length - 1];
  const procOk = last?.system?.host_proc_available;
  const procSrc = last?.system?.proc_metrics_source;
  const hasTemp = pts.some((p) => p.system?.cpu_temp_c_max != null);
  if (hint) {
    const parts = [];
    if (procOk) {
      if (procSrc === "host_mount") parts.push("Host /proc mount — system CPU/RAM/net/IOPS match the Docker host.");
      else parts.push("Container /proc (e.g. Docker Desktop Linux VM) — system lines are that environment, not macOS.");
      parts.push(
        "RAM chart: cyan = Docker Σ memory ÷ kernel MemTotal; magenta = MemAvailable/MemTotal (headroom), not the same quantity.",
      );
    } else parts.push("No readable /proc — system CPU/net/IOPS unavailable; RAM second line uses Docker Σ vs cgroup limits.");
    if (hasTemp) {
      if (pts.some((p) => p.system?.cpu_temp_source === "host_file_thermal_proxy"))
        parts.push(
          "CPU chart value is a thermal-pressure + loadavg proxy from macOS (not die °C); see ai-stack/scripts/macos-write-cpu-temp.sh.",
        );
      else if (pts.some((p) => p.system?.cpu_temp_source === "host_file"))
        parts.push("CPU temp from macOS host file (~/.local-eco-host-metrics/cpu_temp_c.txt).");
      else parts.push("CPU temp from /sys thermal zones.");
    } else {
      parts.push(
        "No CPU temp: macOS — start dashboard via ai-stack/services/dashboard.sh (installs host temp LaunchAgent) or run ai-stack/scripts/macos-write-cpu-temp.sh; Linux — mount host /sys (see dashboard.sh).",
      );
    }
    parts.push("IOPS often 0 without blkio; use right axis block Mb/s.");
    parts.push(
      "Overview KPI tiles use the same Docker CPU/RAM numbers as this tab (all running containers, CPU ÷ host vCPUs, cyan RAM ÷ MemTotal).",
    );
    hint.textContent = parts.join(" ");
  }
  const tempBit = last?.system?.cpu_temp_c_max != null ? ` temp ${last.system.cpu_temp_c_max}°C` : "";
  const rawCpu = last?.docker?.cpu_sum_raw != null ? ` (raw Σ ${last.docker.cpu_sum_raw}%)` : "";
  let ramTail = "";
  if (last) {
    const total = last.system?.host_memory_total_bytes_effective;
    const du = last.docker?.memory_usage;
    const lim = last.docker?.memory_limit_sum;
    const rh = last.docker?.memory_percent_of_host ?? last.docker?.memory_percent;
    ramTail = ` RAM Σ/host ${formatRamPctWithBytes(rh, du, total != null && total > 0 ? total : null)}`;
    if (last.system?.memory_percent_available != null) {
      ramTail += ` · ${formatRamPctOfTotalPhrase("avail", last.system.memory_percent_available, total != null && total > 0 ? total : null)}`;
    } else if (last.system?.memory_percent != null) {
      ramTail += ` · ${formatRamPctOfTotalPhrase("host-used", last.system.memory_percent, total != null && total > 0 ? total : null)}`;
    }
    const lp = last.docker?.memory_percent_of_limits;
    ramTail += ` · limits ${formatRamPctWithBytes(lp, du, lim != null && lim > 0 ? lim : null)}`;
  }
  let line =
    `Samples: ${pts.length} (max ${data.max_points || METRICS_MAX}) · ` +
    `Updated ${data.generated_at ? new Date(data.generated_at).toLocaleString() : ""}` +
    (last
      ? ` · Docker: CPU ${last.docker?.cpu_percent}%${rawCpu}${ramTail} net ${last.docker?.net_total_mbps} Mb/s IOPS~ ${last.docker?.iops_total_est} block ${last.docker?.blk_total_mbps ?? "—"} Mb/s${tempBit}`
      : "");
  if (cacheNote) line = `${cacheNote} ${line}`;
  sum.textContent = line;
}

function renderHostInjectedMetricsPanel(payload) {
  const panel = document.getElementById("hostInjectedMetricsPanel");
  const body = document.getElementById("hostInjectedMetricsBody");
  const ul = document.getElementById("hostInjectedMetricsInsights");
  if (!panel || !body || !ul) return;

  if (!payload || !payload.configured) {
    body.innerHTML =
      "<p><strong>Host temp file not configured.</strong> This panel appears when <code>DASHBOARD_HOST_CPU_TEMP_FILE</code> is set (macOS dashboard deploy).</p>";
    ul.innerHTML = "";
    return;
  }

  const w = payload.writer_status || {};
  const sch = payload.scheduler_meta || {};
  const fm = payload.file_metadata || {};
  const ok = w.success !== false;
  const writerLine = w.updated_at
    ? `${ok ? "OK" : "Failed"} · last writer run <time datetime="${escapeHtml(w.updated_at)}">${escapeHtml(w.updated_at)}</time>${w.message ? ` — ${escapeHtml(w.message)}` : ""}`
    : "No <code>writer_status.json</code> yet — run the host script once or wait for LaunchAgent.";

  body.innerHTML = `
    <div class="host-metrics-grid">
      <div><strong>Temp file</strong><br /><code>${escapeHtml(payload.path || "")}</code><br />exists: ${payload.file_exists ? "yes" : "no"}</div>
      <div><strong>Current read</strong><br />${payload.cpu_temp_c != null ? `${payload.cpu_temp_c}°C` : "—"} <span class="muted">(${escapeHtml(payload.cpu_temp_source || "—")})</span></div>
      <div><strong>File payload</strong><br />source ${escapeHtml(fm.file_source || "—")}${fm.thermal_pressure ? ` · pressure <strong>${escapeHtml(fm.thermal_pressure)}</strong>` : ""}${fm.proxy_model ? ` · model <code>${escapeHtml(fm.proxy_model)}</code>` : ""}${fm.proxy_baseline_c != null && fm.proxy_baseline_c !== "" ? ` · baseline ${escapeHtml(String(fm.proxy_baseline_c))}°C` : ""}${fm.proxy_load_add_c != null && fm.proxy_load_add_c !== "" ? ` + load <strong>${escapeHtml(String(fm.proxy_load_add_c))}°C</strong> (ratio ${escapeHtml(String(fm.proxy_load_ratio ?? "—"))})` : ""}</div>
      <div><strong>Host writer</strong><br />${writerLine}</div>
      <div><strong>Scheduler (on-disk)</strong><br />${
        sch.label
          ? `${escapeHtml(sch.label)} · every ${escapeHtml(String(sch.interval_sec))}s · installed ${escapeHtml(sch.installed_at || "—")}<br /><span class="muted">Verify on Mac: <code>launchctl print gui/$(id -u)/${escapeHtml(sch.label)}</code></span>`
          : "<span class='muted'>No scheduler_meta.json — run <code>dashboard.sh deploy</code> on macOS or install <code>macos-host-metrics-scheduler.sh</code>.</span>"
      }</div>
    </div>
    <p class="muted small" style="margin-top:0.75rem"><strong>Manual host run (Mac terminal):</strong> <code>bash /path/to/repo/ai-stack/scripts/macos-write-cpu-temp.sh</code> — the container cannot execute macOS <code>powermetrics</code> for you.</p>
  `;

  ul.innerHTML = (payload.insights || [])
    .map((t) => `<li>${escapeHtml(t)}</li>`)
    .join("");
}

async function fetchHostInjectedMetricsPanel(options = {}) {
  const logLine = typeof options.logLine === "function" ? options.logLine : null;
  try {
    logLine?.(`GET /api/host-metrics/injected`);
    const res = await fetch("/api/host-metrics/injected");
    logLine?.(`  → HTTP ${res.status} ${res.ok ? "OK" : res.statusText || ""}`.trim());
    if (!res.ok) {
      const body = document.getElementById("hostInjectedMetricsBody");
      if (body) body.textContent = "Could not load host metrics status.";
      logLine?.(`  (response body not shown)`);
      return;
    }
    const data = await res.json();
    renderHostInjectedMetricsPanel(data);
    if (logLine) {
      logLine(`  cpu_temp_c: ${data.cpu_temp_c ?? "—"} · source: ${data.cpu_temp_source ?? "—"}`);
      const w = data.writer_status;
      if (w && typeof w === "object") {
        logLine(
          `  writer_status: success=${w.success} · updated_at=${w.updated_at ?? "—"}${w.message ? ` · ${w.message}` : ""}`,
        );
      }
      const sch = data.scheduler_meta;
      if (sch && sch.label) logLine(`  scheduler: ${sch.label} · interval ${sch.interval_sec ?? "—"}s`);
    }
  } catch (e) {
    logLine?.(`  → error: ${e.message || e}`);
    const body = document.getElementById("hostInjectedMetricsBody");
    if (body) body.textContent = "Could not load host metrics status.";
  }
}

async function loadMetricsCharts(opts = {}) {
  const interactive = opts.interactiveReload === true;
  const btn = document.getElementById("hostMetricsRefreshCharts");
  const pre = document.getElementById("hostMetricsReloadPreloader");
  const logMount = document.getElementById("hostMetricsReloadLog");

  const logLine = (msg) => {
    if (!interactive || !logMount) return;
    logMount.classList.remove("is-hidden");
    const line = document.createElement("div");
    line.className = "host-metrics-reload-log__line";
    line.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
    logMount.appendChild(line);
    logMount.scrollTop = logMount.scrollHeight;
  };

  const setBusy = (on) => {
    if (!interactive) return;
    if (pre) pre.classList.toggle("is-hidden", !on);
    if (btn) btn.disabled = !!on;
  };

  if (interactive) {
    logMount.innerHTML = "";
    logLine("Mac host (not run from container) — update the temp file with:");
    logLine("  bash /project/ai-stack/scripts/macos-write-cpu-temp.sh");
    logLine("  # optional kick: launchctl kickstart -k gui/$(id -u)/com.local-ecosystem.host-cpu-temp");
    logLine("—");
    logLine("Dashboard APIs (this browser → dashboard container):");
  }

  setBusy(true);
  try {
    const sum = document.getElementById("metricsSummary");
    const hint = document.getElementById("metricsProcHint");
    if (!ensureMetricsCharts()) {
      if (sum) sum.textContent = "Chart.js failed to load.";
      logLine?.("ERROR: Chart.js failed to load.");
      return;
    }
    const seeded = getSeededMetricsHistory();
    if (seeded?.points?.length) {
      logLine?.(`render: ${seeded.points.length} point(s) from local cache (seed)`);
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
      const url = `/api/metrics/history?limit=${METRICS_MAX}`;
      logLine?.(`GET ${url}`);
      const res = await fetch(url);
      logLine?.(`  → HTTP ${res.status} ${res.ok ? "OK" : res.statusText || ""}`.trim());
      const data = await res.json();
      if (!data || !Array.isArray(data.points)) throw new Error("Invalid metrics response");
      logLine?.(`  points: ${data.points.length} · max_points: ${data.max_points ?? "—"} · generated_at: ${data.generated_at ?? "—"}`);
      renderMetricsChartsFromPayload(data, sum, hint, "");
      saveMetricsCache(data);
    } catch (e) {
      logLine?.(`  → error: ${e.message || e}`);
      const cached = getSeededMetricsHistory();
      if (cached && cached.points.length > 0) {
        logLine?.(`fallback: rendering ${cached.points.length} cached point(s)`);
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
      } else if (sum) {
        sum.textContent = `Metrics error: ${e.message || e}`;
      }
    }

    await fetchHostInjectedMetricsPanel({ logLine });
    logLine?.("—");
    logLine?.("Done.");
  } finally {
    setBusy(false);
  }
}

function initHostMetricsPanelRefresh() {
  const btn = document.getElementById("hostMetricsRefreshCharts");
  if (!btn || btn.dataset.wired === "1") return;
  btn.dataset.wired = "1";
  btn.addEventListener("click", () => {
    loadMetricsCharts({ interactiveReload: true });
  });
}

function dashboardTokenRequired() {
  return window.__dashboardBoot?.token_required === true;
}

function controlToken() {
  return localStorage.getItem("dashboard_control_token") || "";
}

const CONTROL_GROUP_ORDER = ["ecosystem", "ai-stack", "infra", "cloudflare-local"];

const CONTROL_GROUP_META = {
  ecosystem: {
    title: "Bulk & orchestration",
    lead: "Same full-stack actions as the toolbar above, exposed as a control target for the API.",
    sectionClass: "",
  },
  "ai-stack": {
    title: "AI stack & Traefik",
    lead: "Edge proxy, apps, Ollama, n8n, Postgres, and this dashboard.",
    sectionClass: "",
  },
  "cloudflare-local": {
    title: "Cloudflare local",
    lead: "MinIO, Valkey, adapters, Workers runtime, demo, autoscaler, and whole compose stack.",
    sectionClass: " control-target-group--cf",
  },
  infra: {
    title: "Infra add-ons",
    lead: "MySQL, Redis, Mailpit, Adminer, Redis Commander, Telegram gateway, and cache lab — from infra/docker-compose.yml.",
    sectionClass: "",
  },
};

function partitionControlTargets(targets) {
  const map = new Map();
  for (const t of targets) {
    const g = t.group || "other";
    if (!map.has(g)) map.set(g, []);
    map.get(g).push(t);
  }
  const out = [];
  for (const g of CONTROL_GROUP_ORDER) {
    if (map.has(g)) {
      out.push({ group: g, items: map.get(g) });
      map.delete(g);
    }
  }
  for (const [g, items] of map) {
    out.push({ group: g, items });
  }
  return out;
}

function controlTargetCardHtml(SB, t) {
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
  const primaryBtns = safe
    .map((a) => {
      const cls = SB.actionButtonClasses(a);
      return controlActionButtonHtml(a, cls, t.id, t.label || t.id, rt);
    })
    .join("");
  const dangerBtns = danger
    .map((a) => {
      const cls = SB.actionButtonClasses(a);
      return controlActionButtonHtml(a, cls, t.id, t.label || t.id, rt);
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
}

const HOSTED_APP_ACTIONS = ["deploy", "recreate", "pause", "remove", "reset", "restart", "start", "stop", "unpause"];

function destroyHostedAppCharts() {
  ["cpu", "mem", "net"].forEach((k) => {
    const c = hostedAppCharts[k];
    if (c) {
      try {
        c.destroy();
      } catch (_) {
        /* noop */
      }
      hostedAppCharts[k] = null;
    }
  });
}

function ensureHostedAppCharts() {
  if (typeof Chart === "undefined") return false;
  const cpuEl = document.getElementById("hostedChartCpu");
  const memEl = document.getElementById("hostedChartMem");
  const netEl = document.getElementById("hostedChartNet");
  if (!cpuEl || !memEl || !netEl) return false;
  const gridColor = THEME.grid;
  const tickStyle = { color: "#e9d5ff", font: { size: 11 } };
  const dockerLine = { borderColor: THEME.docker, tension: 0.2, spanGaps: true, ...LINE_STYLE };
  const sysLine = { borderColor: THEME.system, tension: 0.2, spanGaps: true, ...LINE_STYLE };
  const legendBottom = { position: "bottom", labels: { boxWidth: 10, font: { size: 11, color: "#faf5ff" } } };

  if (!hostedAppCharts.cpu) {
    hostedAppCharts.cpu = new Chart(cpuEl, {
      type: "line",
      data: {
        labels: [],
        datasets: [
          { label: "CPU Σ (raw %)", data: [], yAxisID: "y", ...dockerLine },
          { label: "CPU (÷ host vCPUs)", data: [], yAxisID: "y", ...sysLine },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: { ticks: tickStyle, grid: { color: gridColor } },
          y: { beginAtZero: true, grid: { color: gridColor }, ticks: ticksPercentStyle("#e9d5ff", 11) },
        },
        plugins: {
          legend: legendBottom,
          tooltip: {
            callbacks: {
              label(ctx) {
                const y = ctx.parsed.y;
                if (y == null || Number.isNaN(Number(y))) return `${ctx.dataset.label || ""}: —`;
                return `${ctx.dataset.label}: ${Number(y).toFixed(2)}%`;
              },
            },
          },
        },
      },
    });
  }
  if (hostedAppCharts.mem && hostedAppCharts.mem.data.datasets.length < 2) {
    try {
      hostedAppCharts.mem.destroy();
    } catch (_) {
      /* noop */
    }
    hostedAppCharts.mem = null;
  }
  if (!hostedAppCharts.mem) {
    hostedAppCharts.mem = new Chart(memEl, {
      type: "line",
      data: {
        labels: [],
        datasets: [
          { label: "Memory % of limits", data: [], yAxisID: "y", ...dockerLine },
          { label: "RAM used (GB)", data: [], yAxisID: "y1", ...sysLine },
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
            position: "left",
            beginAtZero: true,
            max: 100,
            grid: { color: gridColor },
            ticks: ticksPercentStyle("#e9d5ff", 11),
          },
          y1: {
            position: "right",
            beginAtZero: true,
            grid: { drawOnChartArea: false },
            ticks: {
              color: "#e9d5ff",
              font: { size: 11 },
              callback(raw) {
                const v = typeof raw === "number" ? raw : Number(raw);
                if (!Number.isFinite(v)) return String(raw);
                return `${v.toFixed(2)} GB`;
              },
            },
          },
        },
        plugins: {
          legend: legendBottom,
          tooltip: {
            callbacks: {
              label(ctx) {
                const y = ctx.parsed.y;
                const name = ctx.dataset.label || "";
                if (y == null || Number.isNaN(Number(y))) return `${name}: —`;
                if (ctx.datasetIndex === 0) return `${name}: ${Number(y).toFixed(2)}%`;
                return `${name}: ${Number(y).toFixed(2)} GB`;
              },
            },
          },
        },
      },
    });
  }
  if (!hostedAppCharts.net) {
    hostedAppCharts.net = new Chart(netEl, {
      type: "line",
      data: {
        labels: [],
        datasets: [
          { label: "RX Mb/s", data: [], ...dockerLine },
          { label: "TX Mb/s", data: [], ...sysLine },
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
  return true;
}

function updateHostedAppChartsFromHistory(hist) {
  const pts = (hist && hist.points) || [];
  const labels = pts.map((p) => {
    try {
      return new Date(p.ts).toLocaleTimeString();
    } catch {
      return "";
    }
  });
  const cpuRaw = pts.map((p) => (p.app && p.app.cpu_sum_raw != null ? Number(p.app.cpu_sum_raw) : null));
  const cpuN = pts.map((p) => (p.app && p.app.cpu_percent != null ? Number(p.app.cpu_percent) : null));
  const memPct = pts.map((p) => (p.app && p.app.memory_percent_limits != null ? Number(p.app.memory_percent_limits) : null));
  const memGbUsed = pts.map((p) => {
    const b = p.app && p.app.memory_usage != null ? Number(p.app.memory_usage) : NaN;
    return Number.isFinite(b) && b >= 0 ? b / 1024 ** 3 : null;
  });
  const rx = pts.map((p) => (p.app && p.app.net_rx_mbps != null ? Number(p.app.net_rx_mbps) : null));
  const tx = pts.map((p) => (p.app && p.app.net_tx_mbps != null ? Number(p.app.net_tx_mbps) : null));
  if (!ensureHostedAppCharts()) return;
  hostedAppCharts.cpu.data.labels = labels;
  hostedAppCharts.cpu.data.datasets[0].data = cpuRaw;
  hostedAppCharts.cpu.data.datasets[1].data = cpuN;
  hostedAppCharts.cpu.update();
  hostedAppCharts.mem.data.labels = labels;
  hostedAppCharts.mem.data.datasets[0].data = memPct;
  hostedAppCharts.mem.data.datasets[1].data = memGbUsed;
  hostedAppCharts.mem.update();
  hostedAppCharts.net.data.labels = labels;
  hostedAppCharts.net.data.datasets[0].data = rx;
  hostedAppCharts.net.data.datasets[1].data = tx;
  hostedAppCharts.net.update();
}

function renderHostedAppsSidebar() {
  const nav = document.getElementById("hostedAppsSidebar");
  if (!nav) return;
  if (!hostedAppsList.length) {
    nav.innerHTML = "";
    return;
  }
  nav.innerHTML = hostedAppsList
    .map((a) => {
      const ver = a.application_version
        ? ` <span class="muted small">${escapeHtml(a.application_version)}</span>`
        : "";
      return `<button type="button" data-hosted-slug="${escapeAttr(a.id)}" class="${a.id === hostedSelectedSlug ? "is-active" : ""}">${escapeHtml(a.label || a.id)}${ver}</button>`;
    })
    .join("");
  nav.querySelectorAll("[data-hosted-slug]").forEach((btn) => {
    btn.addEventListener("click", () => {
      hostedSelectedSlug = btn.getAttribute("data-hosted-slug") || "";
      renderHostedAppsSidebar();
      refreshHostedAppsPanel();
    });
  });
}

async function loadHostedAppsList() {
  const empty = document.getElementById("hostedAppsEmpty");
  const detail = document.getElementById("hostedAppsDetail");
  const hint = document.getElementById("hostedAppsTokenHint");
  try {
    const res = await fetch("/api/hosted-apps");
    const data = await res.json();
    if (hint) {
      if (data.token_required) {
        hint.textContent =
          "Control token is required for lifecycle actions — open the Control tab and save your token in the field above (shared with Control).";
        hint.classList.remove("is-hidden");
      } else {
        hint.classList.add("is-hidden");
      }
    }
    hostedAppsList = data.apps || [];
    if (!hostedAppsList.length) {
      if (empty) {
        empty.classList.remove("is-hidden");
        empty.innerHTML =
          'No registered apps. Add <code>config/leco-registry.yaml</code> via <code>leco-app ecosystem-register</code>. See <code>config/leco-registry.example.yaml</code>.';
      }
      if (detail) detail.classList.add("is-hidden");
      const nav = document.getElementById("hostedAppsSidebar");
      if (nav) nav.innerHTML = "";
      destroyHostedAppCharts();
      return;
    }
    if (empty) empty.classList.add("is-hidden");
    if (detail) detail.classList.remove("is-hidden");
    const still = hostedAppsList.some((a) => a.id === hostedSelectedSlug);
    if (!still) hostedSelectedSlug = hostedAppsList[0].id;
    renderHostedAppsSidebar();
    await refreshHostedAppsPanel();
  } catch (e) {
    if (empty) {
      empty.classList.remove("is-hidden");
      empty.textContent = `Failed to load hosted apps: ${e.message || e}`;
    }
    if (detail) detail.classList.add("is-hidden");
  }
}

function hostedComposeControlActionsHtml(SB, target) {
  const rt = target.runtime || {};
  const { safe, danger } = SB.partitionControlActions(target.actions);
  const primaryBtns = safe
    .map((a) => {
      const cls = SB.actionButtonClasses(a);
      return controlActionButtonHtml(a, cls, target.id, target.label || target.id, rt);
    })
    .join("");
  const dangerBtns = danger
    .map((a) => {
      const cls = SB.actionButtonClasses(a);
      return controlActionButtonHtml(a, cls, target.id, target.label || target.id, rt);
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
  return `<div class="${wrapClass}">
    <div class="control-actions control-actions--primary">${primaryBtns}</div>
    ${aside}
  </div>`;
}

function renderHostedResourceLedger(manifestUi, snap) {
  const el = document.getElementById("hostedAppsResourceLedger");
  if (!el) return;
  if (!manifestUi && !snap) {
    el.innerHTML = "";
    return;
  }
  const mu = manifestUi || {};
  const lhp = mu.local_host_profile || "—";
  const pdc = mu.profile_docker_compose;
  const pcf = mu.profile_cloudflare;
  const dedicatedAdapters = mu.dedicated_local_adapters === true;
  const lc = mu.local_cf || {};
  const services = (snap && snap.services) || [];
  const nKv = Array.isArray(lc.kv) ? lc.kv.length : 0;
  const nR2 = Array.isArray(lc.r2) ? lc.r2.length : 0;
  const nD1 = Array.isArray(lc.d1) ? lc.d1.length : 0;
  const cfProvisioned = nKv + nR2 + nD1;
  const w = mu.wrangler_expected || {};
  const expKv = Array.isArray(w.expected_kv) ? w.expected_kv.length : 0;
  const expR2 = Array.isArray(w.expected_r2) ? w.expected_r2.length : 0;
  const expD1 = Array.isArray(w.expected_d1) ? w.expected_d1.length : 0;
  const expTotal = expKv + expR2 + expD1;

  let html = '<div class="hosted-resource-ledger__title">Resources for this app (from <code>leco.yaml</code> + bridge)</div>';
  html += `<p class="hosted-resource-ledger__lead muted small">The dashboard uses your <strong>effective manifest</strong>: <code>leco.app.yaml</code> plus the profile file (<code>${escapeHtml(String(lhp))}</code>). Nothing here is inferred from disk outside those files.</p>`;
  html += '<div class="hosted-resource-ledger__grid">';

  html += '<div class="hosted-resource-ledger__card">';
  html += '<div class="hosted-resource-ledger__card-title">Docker / Compose</div>';
  if (pdc && pdc.compose_file) {
    html += `<p class="hosted-resource-ledger__card-body"><strong>Compose file</strong> (profile <code>infrastructure.dockerCompose.composeFile</code>): <code>${escapeHtml(String(pdc.compose_file))}</code>`;
    const ax = pdc.additional_compose_files;
    if (Array.isArray(ax) && ax.length) {
      html += `<br /><strong>Additional <code>-f</code> files:</strong> ${ax.map((x) => `<code>${escapeHtml(String(x))}</code>`).join(", ")}`;
    }
    html += "</p>";
    html += `<p class="muted small">${services.length} service row(s) below are <strong>all</strong> resources from that compose project. They are the only containers that appear under <em>this</em> app in Docker Desktop.</p>`;
  } else {
    html +=
      '<p class="muted small">No <code>infrastructure.dockerCompose</code> in the profile — this app is not driving a compose stack from <code>leco.yaml</code>.</p>';
  }
  html += "</div>";

  html += '<div class="hosted-resource-ledger__card hosted-resource-ledger__card--cf">';
  html += '<div class="hosted-resource-ledger__card-title">Dedicated local KV / R2 / D1</div>';
  if (pcf && pcf.wrangler_config) {
    html += `<p class="hosted-resource-ledger__card-body"><strong>Wrangler</strong> (profile <code>infrastructure.cloudflare.wranglerConfig</code>): <code>${escapeHtml(String(pcf.wrangler_config))}</code></p>`;
  }
  if (cfProvisioned > 0) {
    html += `<p class="hosted-resource-ledger__counts"><span class="hosted-resource-ledger__count"><strong>${nKv}</strong> KV</span> · <span class="hosted-resource-ledger__count"><strong>${nR2}</strong> R2</span> · <span class="hosted-resource-ledger__count"><strong>${nD1}</strong> D1</span></p>`;
    if (dedicatedAdapters) {
      html +=
        '<p class="muted small">This app uses <strong>dedicatedLocalAdapters</strong>: resources live on <strong>in-compose</strong> services (<code>leco-local-kv-adapter</code>, <code>leco-local-r2-adapter</code>, <code>leco-local-d1-adapter</code> plus Valkey/MinIO) in <strong>your</strong> compose project — they show under this app in Docker Desktop. <code>leco.local-cf.yaml</code> records the same Docker DNS bases the adapters use. See <code>docker-compose.leco-dedicated-cf.example.yml</code> in the sample-cloudflare-application hosting pack.</p>';
    } else {
      html +=
        '<p class="muted small">These are <strong>provisioned names</strong> on the ecosystem&rsquo;s shared <code>kv-adapter</code>, <code>r2-adapter</code>, and <code>d1-adapter</code> (the <strong>cloudflare-local</strong> Docker stack). They are <strong>not</strong> extra containers inside your app&rsquo;s compose file — same model as Cloudflare production (managed APIs). The full list is in the table below and in <code>leco.local-cf.yaml</code>.</p>';
    }
  } else if (expTotal > 0 && w.wrangler_configured) {
    const dedHint = dedicatedAdapters
      ? " With <strong>dedicatedLocalAdapters</strong>, start the stack (including <code>leco-local-*</code> services) first so provision can reach the adapters on <code>lh-network</code>."
      : "";
    html += `<p class="muted small">Wrangler lists <strong>${expKv}</strong> KV, <strong>${expR2}</strong> R2, <strong>${expD1}</strong> D1 bindings, but <code>leco.local-cf.yaml</code> is missing or empty — run <strong>Deploy</strong> (local CF provision on) or <code>leco-app provision-local-cf</code> so dedicated names appear here.${dedHint}</p>`;
  } else if (w.wrangler_configured) {
    html += '<p class="muted small">Wrangler is configured; no KV/R2/D1 tables in this env or nothing provisioned yet.</p>';
  } else {
    html += '<p class="muted small">No Wrangler-driven local CF resources for this manifest.</p>';
  }
  html += "</div>";

  html += "</div>";
  el.innerHTML = html;
}

function renderHostedLocalProfile(manifestUi) {
  const el = document.getElementById("hostedAppsLocalProfile");
  if (!el) return;
  if (!manifestUi) {
    el.innerHTML = "";
    return;
  }
  const arch = manifestUi.localhost_archetype;
  const lhp = manifestUi.local_host_profile;
  const urls = manifestUi.localhost_urls || [];
  const lc = manifestUi.localhost_lifecycle || {};
  const phases = ["prepare", "build", "preStart"];
  const hasLifecycle = phases.some((p) => Array.isArray(lc[p]) && lc[p].length);
  if (!arch && !lhp && !urls.length && !hasLifecycle) {
    el.innerHTML = "";
    return;
  }
  let html = '<div class="hosted-local-profile__title">Local profile</div>';
  html += `<p class="muted small">Archetype <strong>${escapeHtml(String(arch || "—"))}</strong> · Sidecar <code>${escapeHtml(String(lhp || "—"))}</code></p>`;
  if (urls.length) {
    html +=
      "<table><thead><tr><th>Role</th><th>Label</th><th>Public URL</th></tr></thead><tbody>";
    urls.forEach((u) => {
      const role = u.role || "—";
      const lab = u.label || "—";
      const pub = u.publicUrl != null ? u.publicUrl : u.public_url != null ? u.public_url : "—";
      html += `<tr><td>${escapeHtml(String(role))}</td><td>${escapeHtml(String(lab))}</td><td><code>${escapeHtml(String(pub))}</code></td></tr>`;
    });
    html += "</tbody></table>";
  }
  if (hasLifecycle) {
    html += '<div class="hosted-local-profile__lifecycle">';
    phases.forEach((phase) => {
      const steps = lc[phase];
      if (!Array.isArray(steps) || !steps.length) return;
      html += `<div class="hosted-lifecycle-phase"><strong>${escapeHtml(phase)}</strong><ul>`;
      steps.forEach((st) => {
        const cmd = typeof st === "object" && st != null && st.command != null ? st.command : String(st);
        html += `<li><code>${escapeHtml(String(cmd))}</code></li>`;
      });
      html += "</ul></div>";
    });
    html += "</div>";
  }
  el.innerHTML = html;
}

function renderHostedCfResources(manifestUi) {
  const el = document.getElementById("hostedAppsCfResources");
  if (!el) return;
  if (!manifestUi) {
    el.innerHTML = "";
    return;
  }
  const w = manifestUi.wrangler_expected || {};
  const lc = manifestUi.local_cf || { present: false };
  if (w.available === false && w.note) {
    el.innerHTML = `<div class="muted small">${escapeHtml(w.note)}</div>`;
    return;
  }
  const hasW = w.wrangler_configured === true;
  const hasLc = lc.present === true;
  const browser = w.browser_binding;
  const expKv = w.expected_kv || [];
  const expR2 = w.expected_r2 || [];
  const expD1 = w.expected_d1 || [];
  const anyExpected = expKv.length + expR2.length + expD1.length > 0;
  const lcRows =
    hasLc && !lc.error
      ? (lc.kv || []).length + (lc.r2 || []).length + (lc.d1 || []).length
      : 0;

  if (!hasW && !hasLc && !browser) {
    el.innerHTML = "";
    return;
  }

  const dedicatedAdapters = manifestUi.dedicated_local_adapters === true;
  let html = '<div class="hosted-cf-resources__title">Local Cloudflare bindings (detail)</div>';
  html += dedicatedAdapters
    ? `<div class="hosted-cf-resources__why muted small" role="note">
    Same rows as the <strong>Dedicated local KV / R2 / D1</strong> card. With <strong>dedicatedLocalAdapters</strong>, adapter containers are part of your compose project; <code>leco.local-cf.yaml</code> stores bases such as <code>http://leco-local-kv-adapter:8082</code> for services on the same network.
  </div>`
    : `<div class="hosted-cf-resources__why muted small" role="note">
    Same rows as the <strong>Dedicated local KV / R2 / D1</strong> card above. These bindings come from your profile / <code>wrangler.toml</code> and <code>leco.local-cf.yaml</code>; with the default shared adapters they do not add containers to your app&rsquo;s compose project.
  </div>`;
  const hosts = manifestUi.local_cf_adapter_hosts;
  if (hosts && hosts.kv && !dedicatedAdapters) {
    html += `<p class="muted small">This app uses <strong>localCfPublicPrefix</strong>: public API bases <code>${escapeHtml(hosts.kv)}</code>, <code>${escapeHtml(hosts.r2)}</code>, <code>${escapeHtml(hosts.d1)}</code> (Traefik routes to the same shared adapters). Resolve <code>*.lh</code> like other ecosystem hosts.</p>`;
  } else if (!dedicatedAdapters) {
    html +=
      '<p class="muted small">By default, <code>leco.local-cf.yaml</code> records <code>https://kv.lh</code>, <code>https://r2.lh</code>, <code>https://d1.lh</code> for your app. Set <code>cloudflare.localCfPublicPrefix: cv</code> in <code>leco.app.yaml</code> for <code>https://cv-kv.lh</code> etc., then re-run <strong>ecosystem-register</strong> (merge Traefik) and <strong>Deploy</strong>.</p>';
  }
  if (browser) {
    html += `<p class="muted small"><strong>Browser</strong> (<code>${escapeHtml(browser)}</code>): not a separate container — it uses the shared Workers / browser-rendering stack in this ecosystem when you run Workers locally.</p>`;
  }
  if (w.provision_local_resources === false && hasW) {
    html +=
      '<p class="muted small">This manifest sets <code>cloudflare.provisionLocalResources: false</code>, so deploy will not create KV/R2/D1 on the adapters unless you run <code>leco-app provision-local-cf</code> manually.</p>';
  }
  if (w.note && String(w.note).includes("missing")) {
    html += `<p class="hosted-cf-resources__warn small">${escapeHtml(w.note)}</p>`;
  }

  const cidShort = (s) => {
    if (s == null || s === "") return "—";
    const t = String(s);
    return t.length > 28 ? `${escapeHtml(t.slice(0, 26))}…` : escapeHtml(t);
  };

  html +=
    '<table class="hosted-apps-table hosted-cf-resources__table"><thead><tr><th>Kind</th><th>Binding</th><th>Local name / expected</th></tr></thead><tbody>';

  if (lcRows > 0) {
    (lc.kv || []).forEach((row) => {
      html += `<tr><td>KV</td><td><code>${escapeHtml(row.binding || "—")}</code></td><td><code>${escapeHtml(row.local_namespace || "—")}</code></td></tr>`;
    });
    (lc.r2 || []).forEach((row) => {
      html += `<tr><td>R2</td><td><code>${escapeHtml(row.binding || "—")}</code></td><td><code>${escapeHtml(row.bucket || "—")}</code></td></tr>`;
    });
    (lc.d1 || []).forEach((row) => {
      html += `<tr><td>D1</td><td><code>${escapeHtml(row.binding || "—")}</code></td><td><code>${escapeHtml(row.database || "—")}</code></td></tr>`;
    });
  } else if (anyExpected) {
    expKv.forEach((row) => {
      html += `<tr><td>KV</td><td><code>${escapeHtml(row.binding)}</code></td><td class="muted">expected · CF id ${cidShort(row.cf_id)}</td></tr>`;
    });
    expR2.forEach((row) => {
      html += `<tr><td>R2</td><td><code>${escapeHtml(row.binding)}</code></td><td class="muted">expected · <code>${escapeHtml(row.bucket_name)}</code></td></tr>`;
    });
    expD1.forEach((row) => {
      html += `<tr><td>D1</td><td><code>${escapeHtml(row.binding)}</code></td><td class="muted">expected · <code>${escapeHtml(row.database_name)}</code></td></tr>`;
    });
    html +=
      '<tr><td colspan="3" class="muted small">No <code>leco.local-cf.yaml</code> rows — run <strong>Deploy</strong> (with local CF provision enabled) or <code>leco-app provision-local-cf</code>.</td></tr>';
  } else if (hasW && !browser) {
    html += '<tr><td colspan="3" class="muted small">No KV/R2/D1 tables in wrangler for this env.</td></tr>';
  }

  html += "</tbody></table>";
  if (lc.path && hasLc) {
    html += `<p class="muted small">Resource map: <code>${escapeHtml(lc.path)}</code></p>`;
  }
  if (lc.error) {
    html += `<p class="hosted-cf-resources__warn small">${escapeHtml(lc.error)}</p>`;
  }

  el.innerHTML = html;
}

async function refreshHostedAppsPanel() {
  if (activeTab !== "hostedAppsTab" || !hostedSelectedSlug) return;
  const slug = hostedSelectedSlug;
  const titleEl = document.getElementById("hostedAppsTitle");
  const metaEl = document.getElementById("hostedAppsMeta");
  const rtEl = document.getElementById("hostedAppsRuntime");
  const linksEl = document.getElementById("hostedAppsLinks");
  const kpiEl = document.getElementById("hostedAppsKpi");
  const tbody = document.getElementById("hostedAppsServicesBody");
  const insightsEl = document.getElementById("hostedAppsInsights");
  const logPre = document.getElementById("hostedAppsLogPre");
  const controlsEl = document.getElementById("hostedAppsControls");
  const svcSel = document.getElementById("hostedLogService");

  const app = hostedAppsList.find((x) => x.id === slug);
  const tailSel = document.getElementById("hostedLogTail");
  const sinceSel = document.getElementById("hostedLogSince");
  const tail = tailSel ? tailSel.value : "400";
  const since = sinceSel ? sinceSel.value : "1800";
  const searchEl = document.getElementById("hostedLogSearch");
  const search = searchEl ? searchEl.value.trim() : "";
  const svc = svcSel && svcSel.value ? svcSel.value : "";
  const liveLogs = !!document.getElementById("hostedLogLive")?.checked;

  let snap = { ok: false };
  let hist = { points: [] };
  let ins = { ok: false, insights: [] };
  let logs = { ok: false, log: "" };
  try {
    const u = encodeURIComponent(slug);
    const fetches = [
      fetch(`/api/hosted-apps/${u}/snapshot`),
      fetch(`/api/hosted-apps/${u}/metrics/history?limit=120`),
      fetch(`/api/hosted-apps/${u}/insights`),
    ];
    if (!liveLogs) {
      fetches.push(
        fetch(
          `/api/hosted-apps/${u}/logs?tail=${tail}&since=${since}&search=${encodeURIComponent(search)}${svc ? `&service=${encodeURIComponent(svc)}` : ""}`,
        ),
      );
    }
    const results = await Promise.all(fetches);
    snap = await results[0].json();
    hist = await results[1].json();
    ins = await results[2].json();
    if (!liveLogs && results[3]) {
      logs = await results[3].json();
    }
  } catch (e) {
    if (logPre && !liveLogs) {
      logPre.textContent = String(e.message || e);
      hostedLogsScrollToBottomIfFollow();
    }
    return;
  }

  if (!liveLogs) {
    hostedLogStreamLiveKey = "";
    stopHostedLogStream();
  }

  if (titleEl && app) titleEl.textContent = app.label || slug;
  if (metaEl && app) {
    const mu = snap.manifest_ui || {};
    const v = mu.application_version;
    const fp = mu.deploy_fingerprint;
    const snapAt = snap.generated_at ? ` · snapshot ${snap.generated_at}` : "";
    let bits = `Registry id: ${slug} · Target: ${app.target_id || "—"}`;
    if (v) bits += ` · App version: ${String(v)}`;
    if (fp && fp.short_hash) {
      bits += ` · Manifest fingerprint ${String(fp.short_hash)}`;
      if (fp.mtime_iso) bits += ` (mtime ${String(fp.mtime_iso)})`;
    }
    bits += snapAt;
    metaEl.textContent = bits;
  }

  const rt = snap.runtime || (app && app.runtime) || {};
  const rtLabel = escapeHtml(rt.label || "—");
  const rtClass =
    rt.running === true
      ? "control-runtime--up"
      : rt.running === false
        ? "control-runtime--down"
        : rt.kind === "stack"
          ? "control-runtime--stack"
          : "control-runtime--na";
  if (rtEl) {
    rtEl.className = `control-card__runtime ${rtClass}`;
    rtEl.title = rt.label || "";
    rtEl.innerHTML = `<span class="control-runtime-dot" aria-hidden="true"></span><span class="control-runtime-text">${rtLabel}</span>`;
  }

  if (linksEl && app) {
    const parts = [];
    (app.health_urls || []).forEach((u) => {
      parts.push(`<a href="${escapeAttr(u)}" target="_blank" rel="noopener">Health · ${escapeHtml(u)}</a>`);
    });
    (app.routes || []).forEach((r) => {
      const h = r.hostname || "";
      if (h) parts.push(`<span><strong>${escapeHtml(h)}</strong>${r.api_path_prefix ? ` API ${escapeHtml(r.api_path_prefix)}` : ""}</span>`);
    });
    linksEl.innerHTML = parts.length ? parts.join(" · ") : '<span class="muted">No routes or health URLs in manifest.</span>';
  }

  const agg = snap.aggregate;
  const unregisterHintEl = document.getElementById("hostedAppsUnregisterHint");
  const rs = agg != null && snap.ok ? Number(agg.running_services) : NaN;
  const stackLooksDown = app && (rt.running === false || (Number.isFinite(rs) && rs === 0));
  if (unregisterHintEl) {
    if (stackLooksDown) {
      unregisterHintEl.classList.remove("is-hidden");
      unregisterHintEl.innerHTML = `<div class="hosted-apps-unregister-hint__inner">
        <strong>Stack is down, but this app is still registered.</strong>
        The sidebar lists <code>config/leco-registry.yaml</code> — stopping or removing containers does not remove that entry.
        Traefik may still have routes to old service names, which often shows as <strong>Bad Gateway</strong>.
        Use <strong>Remove from ecosystem</strong> (Control token) to unregister and strip manifest-derived Traefik keys, or run
        <code>leco-app ecosystem-unregister ${escapeHtml(slug)} --ecosystem-root …</code>.
        If routes were added manually to <code>traefik/dynamic.yml</code>, edit that file (Traefik reloads it via file watch; restart Traefik only if you changed <code>traefik-static.yaml</code> or mounts).
        <div class="hosted-apps-unregister-hint__actions">
          <button type="button" class="ctrl-act ctrl-act--ops" data-hosted-offboard="${escapeAttr(slug)}">Remove from ecosystem…</button>
        </div>
      </div>`;
    } else {
      unregisterHintEl.classList.add("is-hidden");
      unregisterHintEl.innerHTML = "";
    }
  }

  if (kpiEl) {
    if (agg && snap.ok) {
      const memPctKpi =
        agg.memory_percent_limits != null && agg.memory_percent_limits !== ""
          ? `${Number(agg.memory_percent_limits).toFixed(2)}%`
          : "—";
      kpiEl.innerHTML = `
        <span class="hosted-kpi-chip">Services running <strong>${agg.running_services ?? "—"}</strong> / ${agg.total_services ?? "—"}</span>
        <span class="hosted-kpi-chip">CPU <strong>${agg.cpu_sum_raw ?? "—"}%</strong> (compose Σ) · <strong>${agg.cpu_percent_normalized ?? "—"}%</strong> of host · ${agg.host_cpus ?? "—"} vCPU</span>
        <span class="hosted-kpi-chip">RAM <strong>${memPctKpi}</strong> of limits · <strong>${formatRamGbUsedLimit(agg.memory_usage, agg.memory_limit_sum)}</strong></span>
        <span class="hosted-kpi-chip">Compose ps <strong>${snap.compose_ps_ok ? "ok" : "failed"}</strong></span>`;
    } else {
      kpiEl.innerHTML = `<span class="muted">${escapeHtml(snap.error || "No aggregate (compose unreachable?)")}</span>`;
    }
  }

  renderHostedResourceLedger(snap.manifest_ui, snap);
  renderHostedLocalProfile(snap.manifest_ui);
  renderHostedCfResources(snap.manifest_ui);

  if (tbody) {
    tbody.innerHTML = (snap.services || [])
      .map((s) => {
        const m = s.metrics || {};
        const cpuCell =
          m.cpu_percent != null && m.cpu_percent !== "" ? `${escapeHtml(String(m.cpu_percent))}%` : "—";
        const memPctCell =
          m.memory_percent != null && m.memory_percent !== ""
            ? `${Number(m.memory_percent).toFixed(1)}%`
            : "—";
        const memGbCell = formatRamGbUsedLimit(m.memory_usage, m.memory_limit);
        return `<tr>
          <td>${escapeHtml(s.service || "—")}</td>
          <td><code>${escapeHtml(s.container || "—")}</code></td>
          <td>${escapeHtml(s.state || "—")}</td>
          <td>${cpuCell}</td>
          <td><strong>${memPctCell}</strong> · ${memGbCell}</td>
          <td>${s.restart_count != null ? escapeHtml(String(s.restart_count)) : "—"}</td>
        </tr>`;
      })
      .join("");
  }

  if (svcSel && snap.services) {
    const prev = svcSel.value;
    const opts = ['<option value="">(all)</option>']
      .concat(
        snap.services
          .filter((s) => s.service && s.service !== "—")
          .map((s) => `<option value="${escapeAttr(s.service)}">${escapeHtml(s.service)}</option>`),
      )
      .join("");
    svcSel.innerHTML = opts;
    if (prev && [...svcSel.options].some((o) => o.value === prev)) svcSel.value = prev;
  }

  if (insightsEl) {
    const lvl = (x) => (["ok", "warn", "info"].includes(x) ? x : "info");
    const items = (ins.insights || []).map(
      (it) =>
        `<li class="insight-${lvl((it.level || "info").toLowerCase())}"><strong>${escapeHtml(it.title || "")}</strong> — ${escapeHtml(it.detail || "")}</li>`,
    );
    insightsEl.innerHTML = items.length ? items : '<li class="muted">No insights.</li>';
    if (ins.health_probes && ins.health_probes.length) {
      const probeLine = ins.health_probes
        .map(
          (p) =>
            `${p.url}: ${p.ok ? "ok" : "fail"}${p.status_code != null ? ` HTTP ${p.status_code}` : ""}${p.ms != null ? ` ${p.ms}ms` : ""}${p.error ? ` ${escapeHtml(p.error)}` : ""}`,
        )
        .join(" · ");
      insightsEl.innerHTML += `<li class="insight-info muted small"><strong>Probes</strong> — ${probeLine}</li>`;
    }
  }

  if (logPre && !liveLogs) {
    logPre.textContent = logs.ok ? logs.log || "(empty)" : logs.error || "Logs request failed";
    hostedLogsScrollToBottomIfFollow();
  }

  updateHostedAppChartsFromHistory(hist);

  if (liveLogs) {
    const streamKey = `${slug}|${tail}|${svc}`;
    const streamRunning = hostedLogStreamAbort != null;
    if (!streamRunning || streamKey !== hostedLogStreamLiveKey) {
      hostedLogStreamLiveKey = streamKey;
      startHostedLogStream();
    }
  }

  if (controlsEl && app) {
    const SB = serviceBrandUi();
    const target = {
      id: app.target_id,
      label: app.label || slug,
      actions: HOSTED_APP_ACTIONS,
      runtime: snap.runtime || app.runtime,
    };
    controlsEl.innerHTML = hostedComposeControlActionsHtml(SB, target);
    controlsEl.querySelectorAll("button.ctrl-act").forEach((btn) => {
      btn.addEventListener("click", () =>
        runControlAction(
          btn.getAttribute("data-control-target") || "",
          btn.getAttribute("data-action") || "",
          btn.getAttribute("data-label") || "",
        ),
      );
    });
  }
}

const hostedRegSamplesById = {};
let hostedBrowseLast = null;

function hostedRegisterPathToSlug(path) {
  const stripped = path.replace(/^wsp:\/?/i, "").replace(/\/+$/, "");
  const seg = stripped.split(/[/\\]/).filter(Boolean).pop() || "";
  return seg
    .replace(/[^a-zA-Z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .toLowerCase();
}

/** From server boot JSON (same-origin); used to build a host path after native folder pick. */
function hostedBootPathHints() {
  const b = (typeof window !== "undefined" && window.__dashboardBoot) || {};
  return {
    workspace_parent_host: String(b.workspace_parent_host || "").replace(/[/\\]+$/, ""),
    project_root_host: String(b.project_root_host || "").replace(/[/\\]+$/, ""),
  };
}

/**
 * Browsers do not expose the full filesystem path for a picked folder. We only get its name;
 * combine with workspace_parent_host when set (dashboard mount), else wsp:name.
 */
function suggestedPathForPickedFolderName(dirName, hints) {
  const n = String(dirName || "")
    .trim()
    .replace(/[/\\]+/g, "");
  if (!n || n === "." || n === "..") return "";
  if (hints.workspace_parent_host) {
    return `${hints.workspace_parent_host}/${n}`.replace(/\/+/g, "/");
  }
  return `wsp:${n}`;
}

/**
 * Opens the browser’s native file picker; reads UTF-8 text into the textarea.
 * @param {(text: string, isErr: boolean) => void} [setMsg]
 */
function wireHostedYamlFilePicker(buttonId, inputId, textareaId, setMsg) {
  const btn = document.getElementById(buttonId);
  const inp = document.getElementById(inputId);
  const ta = document.getElementById(textareaId);
  if (!btn || !inp || !ta) return;
  btn.addEventListener("click", () => inp.click());
  inp.addEventListener("change", () => {
    const f = inp.files && inp.files[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => {
      ta.value = typeof reader.result === "string" ? reader.result : "";
      inp.value = "";
      if (typeof setMsg === "function") {
        setMsg(`Loaded file: ${f.name}`, false);
      }
    };
    reader.onerror = () => {
      inp.value = "";
      if (typeof setMsg === "function") {
        setMsg("Could not read file.", true);
      }
    };
    reader.readAsText(f);
  });
}

async function ensureRegisterSamplesLoaded() {
  const sel = document.getElementById("hostedRegSampleSelect");
  if (!sel || sel.dataset.loaded === "1") return;
  try {
    const res = await fetch("/api/leco/register-samples");
    const data = await res.json();
    if (!data.ok || !Array.isArray(data.samples)) return;
    data.samples.forEach((s) => {
      hostedRegSamplesById[s.id] = s;
      const opt = document.createElement("option");
      opt.value = s.id;
      opt.textContent = s.title;
      if (s.description) opt.title = s.description;
      sel.appendChild(opt);
    });
    sel.dataset.loaded = "1";
  } catch (_) {
    /* ignore */
  }
}

function initHostedBrowseModal(pathIn, setMsg, onFolderPicked) {
  const modal = document.getElementById("hostedBrowseModal");
  const backdrop = document.getElementById("hostedBrowseBackdrop");
  const btnBrowse = document.getElementById("hostedRegBrowse");
  const rootSel = document.getElementById("hostedBrowseRoot");
  const listEl = document.getElementById("hostedBrowseList");
  const crumbEl = document.getElementById("hostedBrowseCrumb");
  const errEl = document.getElementById("hostedBrowseErr");
  const btnUp = document.getElementById("hostedBrowseUp");
  const btnSelect = document.getElementById("hostedBrowseSelectHere");
  const btnClose = document.getElementById("hostedBrowseClose");
  if (!modal || !btnBrowse || !pathIn || !rootSel || !listEl) return;

  let subpath = "";

  function closeModal() {
    modal.classList.add("is-hidden");
    document.body.style.overflow = "";
  }

  function openModal() {
    subpath = "";
    modal.classList.remove("is-hidden");
    document.body.style.overflow = "hidden";
    loadBrowse();
  }

  async function loadBrowse() {
    if (errEl) errEl.textContent = "";
    listEl.innerHTML = "";
    const root = rootSel.value || "project";
    try {
      const q = new URLSearchParams({ root, path: subpath });
      const res = await fetch(`/api/leco/browse?${q}`);
      const data = await res.json();
      hostedBrowseLast = data;
      if (!data.ok) {
        if (errEl) errEl.textContent = data.error || "Browse failed";
        if (crumbEl) crumbEl.textContent = "";
        return;
      }
      const label = root === "wsp" ? "workspace-parent" : "/project";
      if (crumbEl) {
        crumbEl.textContent = data.subpath
          ? `${label} → ${data.subpath.replace(/\//g, " → ")}`
          : `${label} (root)`;
      }
      const entries = data.entries || [];
      if (!entries.length) {
        listEl.innerHTML = '<li class="muted" style="padding:12px">No subfolders</li>';
      } else {
        entries.forEach((ent) => {
          const li = document.createElement("li");
          const b = document.createElement("button");
          b.type = "button";
          b.textContent = `📁 ${ent.name}`;
          b.addEventListener("click", () => {
            subpath = ent.rel;
            loadBrowse();
          });
          li.appendChild(b);
          listEl.appendChild(li);
        });
      }
      if (btnUp) btnUp.disabled = !data.subpath;
      if (btnSelect) {
        const needSub =
          data.root_kind === "project" && !(data.subpath && String(data.subpath).trim());
        btnSelect.disabled = !!needSub;
        btnSelect.title = needSub
          ? "Pick a subfolder under the repo (not the repo root itself)"
          : "";
      }
    } catch (e) {
      if (errEl) errEl.textContent = String(e.message || e);
    }
  }

  btnBrowse.addEventListener("click", () => {
    ensureRegisterSamplesLoaded();
    openModal();
  });
  rootSel.addEventListener("change", () => {
    subpath = "";
    loadBrowse();
  });
  btnUp?.addEventListener("click", () => {
    if (hostedBrowseLast && hostedBrowseLast.parent_subpath != null) {
      subpath = hostedBrowseLast.parent_subpath;
      loadBrowse();
    }
  });
  btnSelect?.addEventListener("click", () => {
    if (hostedBrowseLast && hostedBrowseLast.ok && hostedBrowseLast.current_path_field != null) {
      pathIn.value = hostedBrowseLast.current_path_field;
      closeModal();
      if (typeof onFolderPicked === "function") {
        onFolderPicked();
      } else {
        setMsg("Folder selected. Click Detect to scan and load YAML.");
      }
    }
  });
  btnClose?.addEventListener("click", closeModal);
  backdrop?.addEventListener("click", closeModal);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !modal.classList.contains("is-hidden")) closeModal();
  });
}

function initHostedRegisterWizard() {
  const detectBtn = document.getElementById("hostedRegDetect");
  const genBtn = document.getElementById("hostedRegGenerateYaml");
  const saveYamlBtn = document.getElementById("hostedRegSaveYaml");
  const submitBtn = document.getElementById("hostedRegSubmit");
  const pathIn = document.getElementById("hostedRegPath");
  const idIn = document.getElementById("hostedRegId");
  const labelIn = document.getElementById("hostedRegLabel");
  const pre = document.getElementById("hostedRegDetectOut");
  const msg = document.getElementById("hostedRegMsg");
  const manTa = document.getElementById("hostedRegManifestYaml");
  const locTa = document.getElementById("hostedRegLocalhostYaml");
  const loadExistingChk = document.getElementById("hostedRegLoadExisting");
  const sampleSel = document.getElementById("hostedRegSampleSelect");
  const sampleApply = document.getElementById("hostedRegSampleApply");
  const regPanel = document.getElementById("hostedRegisterPanel");
  const valBtn = document.getElementById("hostedRegValidateYaml");
  const yamlRep = document.getElementById("hostedRegYamlReport");
  const busyReg = document.getElementById("hostedRegBusy");
  const busyRegText = busyReg?.querySelector("[data-hosted-reg-busy-text]");
  const deployChk = document.getElementById("hostedRegDeployAfter");
  const workflowEl = document.getElementById("hostedRegWorkflow");
  const workflowHintEl = document.getElementById("hostedRegWorkflowHint");
  /** Path for which the last Detect succeeded (step 1 “done” until path changes). */
  let hostedRegDetectOkForPath = "";
  if (!detectBtn || !submitBtn || !pathIn) return;

  function syncHostedRegWorkflow(st, busy, opts) {
    if (!workflowEl) return;
    const pathOk = pathIn.value.trim().length > 0;
    const idOk = idIn && idIn.value.trim().length > 0;
    const canMutate = pathOk && idOk;
    const yamlFilled = !!(manTa?.value?.trim() && locTa?.value?.trim());
    const registrationReady = !!(st && st.registration_ready);
    const detectDone = !!(pathOk && hostedRegOkForPathMatches());
    const tokReq = dashboardTokenRequired();
    const tokOk = !!controlToken();
    const registerEnabled = registrationReady && canMutate && (!tokReq || tokOk);
    const rails = workflowEl.querySelectorAll(".hosted-reg-workflow__rail");
    workflowEl.dataset.busy = busy ? "1" : "0";

    const steps = workflowEl.querySelectorAll(".hosted-reg-workflow__step");
    steps.forEach((step) => {
      const btn = step.querySelector(".hosted-reg-workflow__btn");
      const locked = !!(btn && btn.disabled);
      step.classList.toggle("hosted-reg-workflow__step--locked", locked);
      step.classList.toggle("hosted-reg-workflow__step--enabled", !locked);
      step.classList.remove("hosted-reg-workflow__step--current");
    });

    const stepEls = Array.from(steps);
    const markDone = (idx, on) => {
      if (stepEls[idx]) stepEls[idx].classList.toggle("hosted-reg-workflow__step--done", !!on);
    };
    markDone(0, detectDone);
    markDone(1, registrationReady);
    markDone(2, registrationReady);
    markDone(3, false);
    markDone(4, registerEnabled);

    let currentSet = false;
    for (let i = 0; i < stepEls.length; i++) {
      const btn = stepEls[i].querySelector(".hosted-reg-workflow__btn");
      const done = stepEls[i].classList.contains("hosted-reg-workflow__step--done");
      const locked = !!(btn && btn.disabled);
      if (!locked && !done && !currentSet) {
        stepEls[i].classList.add("hosted-reg-workflow__step--current");
        currentSet = true;
      }
    }
    if (!currentSet && stepEls.length) {
      const last = stepEls[stepEls.length - 1];
      const btn = last.querySelector(".hosted-reg-workflow__btn");
      if (
        btn &&
        !btn.disabled &&
        !last.classList.contains("hosted-reg-workflow__step--done")
      ) {
        last.classList.add("hosted-reg-workflow__step--current");
      }
    }

    rails.forEach((rail, i) => {
      const leftDone = stepEls[i] ? stepEls[i].classList.contains("hosted-reg-workflow__step--done") : false;
      rail.classList.toggle("hosted-reg-workflow__rail--active", !!leftDone);
    });

    if (!workflowHintEl || busy) {
      if (workflowHintEl && busy) workflowHintEl.textContent = opts?.busyLabel || "Working…";
      return;
    }
    let hint = "";
    if (!pathOk) hint = "Step 1: enter an app root path, then run Detect.";
    else if (!idOk) hint = "Enter app id (slug) for steps 2–5.";
    else if (!detectDone) hint = "Step 1: run Detect to scan compose / wrangler / archetype.";
    else if (!registrationReady)
      hint = "Steps 2–3: Generate YAML from the scan and/or Save YAML to write files to disk.";
    else if (tokReq && !tokOk) hint = "Set the control token on the Control tab to enable Register.";
    else hint = "Step 5: Register runs ecosystem-register (and optional deploy).";

    workflowHintEl.textContent = hint;
  }

  function hostedRegOkForPathMatches() {
    const p = pathIn.value.trim();
    return !!(p && hostedRegDetectOkForPath && p === hostedRegDetectOkForPath);
  }

  function applyRegistrationYamlStatus(st) {
    const pathOk = pathIn.value.trim().length > 0;
    const idOk = idIn && idIn.value.trim().length > 0;
    const ready = !!(st && st.registration_ready) && pathOk && idOk;
    const tokReq = dashboardTokenRequired();
    const tokOk = !!controlToken();
    const registerEnabled = ready && (!tokReq || tokOk);
    if (submitBtn) {
      submitBtn.disabled = !registerEnabled;
      submitBtn.title = registerEnabled
        ? ""
        : !pathOk || !idOk
          ? "Enter app root path and app id"
          : !ready
            ? "Generate YAML or Save YAML so leco.app.yaml and the profile file exist on disk"
            : "Set the control token on the Control tab";
    }
    const canMutate = pathOk && idOk;
    const yamlFilled = !!(manTa?.value?.trim() && locTa?.value?.trim());
    if (genBtn) genBtn.disabled = !canMutate;
    if (saveYamlBtn) saveYamlBtn.disabled = !canMutate;
    if (valBtn) valBtn.disabled = !yamlFilled;
    if (detectBtn) detectBtn.disabled = !pathOk;
    syncHostedRegWorkflow(st, false, {});
  }

  async function refreshYamlStatus() {
    const path = pathIn.value.trim();
    const app_id = idIn && idIn.value.trim() ? idIn.value.trim() : "";
    if (!path) {
      applyRegistrationYamlStatus(null);
      return;
    }
    try {
      const res = await fetch("/api/leco/yaml-status", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, app_id }),
        cache: "no-store",
      });
      const data = await res.json();
      if (data.ok) applyRegistrationYamlStatus(data);
      else applyRegistrationYamlStatus(null);
    } catch (_) {
      applyRegistrationYamlStatus(null);
    }
  }

  function setRegFormBusy(on, label) {
    if (busyReg) {
      busyReg.classList.toggle("is-hidden", !on);
      if (busyRegText && label) busyRegText.textContent = label;
    }
    [detectBtn, valBtn, submitBtn, genBtn, saveYamlBtn].forEach((b) => {
      if (b) b.disabled = !!on;
    });
    if (on) {
      syncHostedRegWorkflow(null, true, { busyLabel: label || "Working…" });
      return;
    }
    refreshYamlStatus();
  }

  function setMsg(text, isErr) {
    if (!msg) return;
    msg.textContent = text || "";
    msg.classList.toggle("hosted-reg-msg--error", !!isErr);
  }

  /** After a successful register: collapse panel, clear fields, sync workflow UI. */
  function resetHostedRegisterFormAfterSuccess() {
    setRegFormBusy(false);
    hostedRegDetectOkForPath = "";
    if (pathIn) pathIn.value = "";
    if (idIn) idIn.value = "";
    if (labelIn) labelIn.value = "";
    if (manTa) manTa.value = "";
    if (locTa) locTa.value = "";
    if (sampleSel) sampleSel.value = "";
    if (loadExistingChk) loadExistingChk.checked = true;
    if (deployChk) deployChk.checked = true;
    if (pre) {
      pre.textContent = "";
      pre.classList.add("is-hidden");
    }
    if (yamlRep) {
      yamlRep.textContent = "";
      yamlRep.classList.add("is-hidden");
      yamlRep.classList.remove("hosted-reg-yaml-report--pass", "hosted-reg-yaml-report--fail");
    }
    setMsg("");
    const hostedRegRootDirInp = document.getElementById("hostedRegRootDir");
    if (hostedRegRootDirInp) hostedRegRootDirInp.value = "";
    if (regPanel && regPanel.tagName === "DETAILS") {
      regPanel.open = false;
    }
    refreshYamlStatus();
  }

  /** @param {{ fromBrowse?: boolean }} [how] */
  async function runHostedRegisterDetect(how) {
    const path = pathIn.value.trim();
    if (!path) {
      setMsg("Enter an app root path.", true);
      return;
    }
    const loadExisting = loadExistingChk ? loadExistingChk.checked : true;
    if (how && how.fromBrowse && !loadExisting) {
      setMsg("Folder selected. Turn on auto-load or click Detect to scan.");
      return;
    }
    const busyLabel = how && how.fromBrowse ? "Loading folder — calling server…" : "Detecting — calling server…";
    setRegFormBusy(true, busyLabel);
    setMsg(how && how.fromBrowse ? "Loading configuration from folder…" : "Detecting…");
    try {
      let previewId = idIn && idIn.value.trim();
      if (!previewId) {
        previewId = hostedRegisterPathToSlug(path);
      }
      const body = { path };
      if (previewId) body.app_id = previewId;
      const res = await fetch("/api/leco/detect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        cache: "no-store",
      });
      const data = await res.json();
      if (!data.ok) {
        hostedRegDetectOkForPath = "";
        if (pre) {
          pre.classList.add("is-hidden");
          pre.textContent = "";
        }
        setMsg(data.error || "Detect failed", true);
        return;
      }
      if (data.path_field && pathIn) {
        pathIn.value = data.path_field;
      }
      if (pre) {
        pre.classList.remove("is-hidden");
        const {
          manifest_yaml_preview: _my,
          localhost_yaml_preview: _ly,
          existing_manifest_yaml: _em,
          existing_localhost_yaml: _el,
          path_field: _pf,
          ...rest
        } = data;
        pre.textContent = JSON.stringify(rest, null, 2);
      }
      if (loadExisting) {
        if (manTa && data.existing_manifest_yaml) manTa.value = data.existing_manifest_yaml;
        if (locTa && data.existing_localhost_yaml) locTa.value = data.existing_localhost_yaml;
      }
      if (manTa && !manTa.value.trim() && data.manifest_yaml_preview != null) {
        manTa.value = data.manifest_yaml_preview;
      }
      if (locTa && !locTa.value.trim() && data.localhost_yaml_preview != null) {
        locTa.value = data.localhost_yaml_preview;
      }
      if (idIn && !idIn.value.trim() && previewId) {
        idIn.value = previewId;
      }
      if (labelIn && !labelIn.value.trim() && idIn && idIn.value.trim()) {
        const suggest =
          data.suggested_label != null && String(data.suggested_label).trim()
            ? String(data.suggested_label).trim()
            : "";
        if (suggest) {
          labelIn.value = suggest;
        } else {
          const slug = idIn.value.trim();
          labelIn.value = slug
            .split(/[-_.]+/)
            .filter(Boolean)
            .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
            .join(" ");
        }
      }
      hostedRegDetectOkForPath = pathIn.value.trim();
      await refreshYamlStatus();
      if (how && how.fromBrowse) {
        setMsg(
          dashboardTokenRequired()
            ? "Detect ok — use Generate YAML or Save YAML, then Register (control token)."
            : "Detect ok — use Generate YAML or Save YAML, then Register.",
        );
      } else {
        setMsg(
          dashboardTokenRequired()
            ? "Detect ok — Generate YAML writes files from scan; Save YAML persists edits; Register needs both files on disk (control token)."
            : "Detect ok — Generate YAML writes files from scan; Save YAML persists edits; Register needs both files on disk.",
        );
      }
    } catch (e) {
      hostedRegDetectOkForPath = "";
      setMsg(String(e.message || e), true);
    } finally {
      setRegFormBusy(false);
    }
  }

  initHostedBrowseModal(pathIn, setMsg, () => {
    runHostedRegisterDetect({ fromBrowse: true });
  });

  wireHostedYamlFilePicker(
    "hostedRegManifestBrowseFile",
    "hostedRegManifestFile",
    "hostedRegManifestYaml",
    setMsg,
  );
  wireHostedYamlFilePicker(
    "hostedRegLocalhostBrowseFile",
    "hostedRegLocalhostFile",
    "hostedRegLocalhostYaml",
    setMsg,
  );

  function tryAutoDetectFromPathField() {
    const loadExisting = loadExistingChk ? loadExistingChk.checked : true;
    if (!loadExisting) return;
    const p = pathIn.value.trim();
    if (!p) return;
    if ((manTa && manTa.value.trim()) || (locTa && locTa.value.trim())) return;
    runHostedRegisterDetect({});
  }

  let pathInputDebounce = null;
  pathIn.addEventListener("input", () => {
    const p = pathIn.value.trim();
    if (hostedRegDetectOkForPath && p !== hostedRegDetectOkForPath) hostedRegDetectOkForPath = "";
    if (pathInputDebounce) clearTimeout(pathInputDebounce);
    pathInputDebounce = setTimeout(() => refreshYamlStatus(), 220);
  });
  pathIn.addEventListener("blur", () => {
    tryAutoDetectFromPathField();
    refreshYamlStatus();
  });
  pathIn.addEventListener("paste", () => {
    setTimeout(() => tryAutoDetectFromPathField(), 0);
    setTimeout(() => refreshYamlStatus(), 100);
  });
  let yamlFieldDebounce = null;
  function scheduleYamlFieldSync() {
    if (yamlFieldDebounce) clearTimeout(yamlFieldDebounce);
    yamlFieldDebounce = setTimeout(() => refreshYamlStatus(), 200);
  }
  manTa?.addEventListener("input", scheduleYamlFieldSync);
  locTa?.addEventListener("input", scheduleYamlFieldSync);
  let yamlStatusDebounce = null;
  idIn?.addEventListener("blur", () => refreshYamlStatus());
  idIn?.addEventListener("input", () => {
    if (yamlStatusDebounce) clearTimeout(yamlStatusDebounce);
    yamlStatusDebounce = setTimeout(() => refreshYamlStatus(), 400);
  });

  const dirInp = document.getElementById("hostedRegRootDir");
  function hostedHtmlDirectoryPickerSupported() {
    try {
      const probe = document.createElement("input");
      probe.type = "file";
      probe.webkitdirectory = true;
      return probe.webkitdirectory === true;
    } catch (_) {
      return false;
    }
  }

  function applyHostedPathAfterFsapiFolder(dirName) {
    const path = suggestedPathForPickedFolderName(dirName, hostedBootPathHints());
    if (!path) {
      setMsg("Could not build a path from the selected folder.", true);
      return;
    }
    pathIn.value = path;
    if (idIn && !idIn.value.trim()) {
      idIn.value = hostedRegisterPathToSlug(path);
    }
    // Label: leave empty here so auto-Detect can fill suggested_label from package.json / wrangler / compose.
    setMsg(
      `Folder “${dirName}” → ${path}. If the app root is nested (e.g. …/Repo/subapp), edit the path. Then Detect or Register.`,
      false,
    );
    tryAutoDetectFromPathField();
  }

  dirInp?.addEventListener("change", () => {
    dirInp.value = "";
    const hints = hostedBootPathHints();
    if (hints.workspace_parent_host) {
      const base = hints.workspace_parent_host.replace(/[/\\]+$/, "");
      pathIn.value = `${base}/`;
      pathIn.focus();
      setMsg(
        "Folder selected. Add the folder name after the trailing slash (same as in the picker), or paste the full path. Then tab away or click Detect.",
        false,
      );
    } else {
      pathIn.value = "wsp:";
      pathIn.focus();
      const len = pathIn.value.length;
      try {
        pathIn.setSelectionRange(len, len);
      } catch (_) {
        /* ignore */
      }
      setMsg(
        "Folder selected. Finish with wsp:YourRepo/subpath or paste a full host path, then Detect.",
        false,
      );
    }
  });

  const btnChooseFolder = document.getElementById("hostedRegChooseFolder");
  btnChooseFolder?.addEventListener("click", async () => {
    if (typeof window.showDirectoryPicker === "function") {
      let handle;
      try {
        handle = await window.showDirectoryPicker();
      } catch (e) {
        if (e && e.name === "AbortError") return;
        /* SecurityError / insecure context / policy — try HTML folder input */
      }
      if (handle && handle.name) {
        applyHostedPathAfterFsapiFolder(String(handle.name));
        return;
      }
    }
    if (dirInp && hostedHtmlDirectoryPickerSupported()) {
      dirInp.click();
      return;
    }
    setMsg(
      "No folder picker in this browser. Use Browse (server tree) or paste repo-relative, wsp:…, or a full host path.",
      true,
    );
  });

  regPanel?.addEventListener("toggle", () => {
    if (regPanel.open) {
      ensureRegisterSamplesLoaded();
      refreshYamlStatus();
    } else {
      const ov = document.getElementById("controlActionOverlay");
      if (!ov || ov.hidden) setRegFormBusy(false);
    }
  });

  sampleApply?.addEventListener("click", () => {
    const id = sampleSel?.value || "";
    if (!id || !hostedRegSamplesById[id]) {
      setMsg("Choose a sample template first.", true);
      return;
    }
    const s = hostedRegSamplesById[id];
    if (manTa && s.manifest_yaml) manTa.value = s.manifest_yaml;
    if (locTa && s.localhost_yaml) locTa.value = s.localhost_yaml;
    setMsg(`Applied sample: ${s.title}. Edit, then Save YAML (or Generate YAML) before Register.`);
    refreshYamlStatus();
  });

  detectBtn.addEventListener("click", () => {
    runHostedRegisterDetect({});
  });

  genBtn?.addEventListener("click", async () => {
    const path = pathIn.value.trim();
    const app_id = idIn ? idIn.value.trim() : "";
    if (!path || !app_id) {
      setMsg("Path and app id are required for Generate YAML.", true);
      return;
    }
    const tok = controlToken();
    if (dashboardTokenRequired() && !tok) {
      setMsg("Set the control token on the Control tab (or localStorage).", true);
      return;
    }
    setRegFormBusy(true, "Generating YAML on server…");
    setMsg("Writing leco.app.yaml + profile from detected compose / wrangler / archetype…");
    try {
      const res = await fetch("/api/leco/generate-yaml", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Control-Token": tok },
        body: JSON.stringify({ path, app_id, token: tok }),
        cache: "no-store",
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        setMsg(data.error || `HTTP ${res.status}`, true);
        return;
      }
      if (manTa && data.manifest_yaml != null) manTa.value = data.manifest_yaml;
      if (locTa && data.localhost_yaml != null) locTa.value = data.localhost_yaml;
      setMsg(
        data.materialized
          ? "YAML materialized under hosting (read-only root). You can edit and Save YAML, then Register."
          : "YAML written under the app root. Edit if needed, Save YAML to persist changes, then Register.",
      );
      await refreshYamlStatus();
    } catch (e) {
      setMsg(String(e.message || e), true);
    } finally {
      setRegFormBusy(false);
    }
  });

  saveYamlBtn?.addEventListener("click", async () => {
    const path = pathIn.value.trim();
    const app_id = idIn ? idIn.value.trim() : "";
    if (!path || !app_id) {
      setMsg("Path and app id are required for Save YAML.", true);
      return;
    }
    const tok = controlToken();
    if (dashboardTokenRequired() && !tok) {
      setMsg("Set the control token on the Control tab (or localStorage).", true);
      return;
    }
    if (!manTa || !manTa.value.trim() || !locTa || !locTa.value.trim()) {
      setMsg("Both manifest and localhost YAML fields must be non-empty to save.", true);
      return;
    }
    setRegFormBusy(true, "Saving YAML on server…");
    setMsg("Validating and writing files…");
    try {
      const res = await fetch("/api/leco/save-yaml", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Control-Token": tok },
        body: JSON.stringify({
          path,
          app_id,
          manifest_yaml: manTa.value,
          localhost_yaml: locTa.value,
          token: tok,
        }),
        cache: "no-store",
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        setMsg(data.error || `HTTP ${res.status}`, true);
        return;
      }
      setMsg(`Saved: ${data.manifest_path || ""} · ${data.localhost_path || ""}`);
      await refreshYamlStatus();
    } catch (e) {
      setMsg(String(e.message || e), true);
    } finally {
      setRegFormBusy(false);
    }
  });

  valBtn?.addEventListener("click", async () => {
    if (!yamlRep) return;
    setRegFormBusy(true, "Validating YAML on server…");
    setMsg("Validating YAML…");
    yamlRep.classList.remove("hosted-reg-yaml-report--pass", "hosted-reg-yaml-report--fail");
    yamlRep.classList.add("is-hidden");
    yamlRep.textContent = "";
    try {
      const body = {
        manifest_yaml: manTa ? manTa.value : "",
        localhost_yaml: locTa ? locTa.value : "",
      };
      const res = await fetch("/api/leco/validate-yaml", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        cache: "no-store",
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        setMsg(data.error || `HTTP ${res.status}`, true);
        return;
      }
      const pass = !!data.validation_ok;
      const jsonBlock = JSON.stringify(data.report || {}, null, 2);
      yamlRep.textContent = `${data.summary_text || ""}\n\n— structured report —\n${jsonBlock}`;
      yamlRep.classList.remove("is-hidden");
      yamlRep.classList.toggle("hosted-reg-yaml-report--pass", pass);
      yamlRep.classList.toggle("hosted-reg-yaml-report--fail", !pass);
      setMsg(pass ? "Validation passed — see report below." : "Validation failed — see report below.", !pass);
    } catch (e) {
      setMsg(String(e.message || e), true);
    } finally {
      setRegFormBusy(false);
    }
  });

  submitBtn.addEventListener("click", async () => {
    const path = pathIn.value.trim();
    const app_id = idIn ? idIn.value.trim() : "";
    const label = labelIn ? labelIn.value.trim() : "";
    if (!path || !app_id) {
      setMsg("Path and app id are required.", true);
      return;
    }
    const tok = controlToken();
    if (dashboardTokenRequired() && !tok) {
      setMsg("Set the control token on the Control tab (or localStorage).", true);
      return;
    }
    setMsg("Registering — overlay shows progress; server runs registry + optional docker deploy (works via Traefik).");
    const regBody = {
      path,
      app_id,
      label,
      deploy_stack: deployChk ? !!deployChk.checked : true,
    };
    setRegFormBusy(true, "Registering…");
    try {
      const out = await runDashboardSyncRegisterOverlay({
        body: regBody,
        title: `Register · ${app_id}`,
        actionVerb: "register",
        async onFinally() {
          await loadHostedAppsList();
          await refreshHostedAppsPanel().catch(() => {});
          loadOverview().catch(() => {});
        },
      });
      const regId = out.result?.registry_entry?.id;
      if (regId) {
        resetHostedRegisterFormAfterSuccess();
        if (!out.ok) {
          setMsg(
            out.error ||
              "Registry updated; docker deploy reported an issue — see overlay for the log.",
            true,
          );
        } else {
          hideControlActionOverlay();
        }
      } else if (out.error) {
        setMsg(out.error, true);
      } else {
        setMsg("Finished — see overlay for full output.", false);
      }
    } catch (e) {
      setMsg(String(e.message || e), true);
    } finally {
      setRegFormBusy(false);
    }
  });

  refreshYamlStatus();
}

function initHostedAppsLogToolbar() {
  const root = document.getElementById("hostedAppsTab");
  if (!root || root.dataset.logWired === "1") return;
  root.dataset.logWired = "1";
  initHostedLogFollowScrollSync();
  const refresh = document.getElementById("hostedLogRefresh");
  const search = document.getElementById("hostedLogSearch");
  if (refresh) {
    refresh.addEventListener("click", () => refreshHostedAppsPanel());
  }
  if (search) {
    search.addEventListener("keydown", (e) => {
      if (e.key === "Enter") refreshHostedAppsPanel();
    });
  }
  ["hostedLogTail", "hostedLogSince", "hostedLogService"].forEach((id) => {
    document.getElementById(id)?.addEventListener("change", () => refreshHostedAppsPanel());
  });
  const liveChk = document.getElementById("hostedLogLive");
  if (liveChk) {
    liveChk.addEventListener("change", () => {
      if (!liveChk.checked) {
        hostedLogStreamLiveKey = "";
        stopHostedLogStream();
      }
      if (activeTab === "hostedAppsTab" && hostedSelectedSlug) {
        refreshHostedAppsPanel();
      }
    });
  }
  document.getElementById("hostedLogFollowBottom")?.addEventListener("change", (e) => {
    if (e.target.checked) {
      hostedLogsScrollToBottomIfFollow();
    }
  });

  root.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-hosted-offboard]");
    if (!btn) return;
    const sl = btn.getAttribute("data-hosted-offboard") || "";
    if (sl) runHostedOffboard(sl, true, true);
  });
}

function initRoutesTab() {
  const tab = document.getElementById("routesTab");
  if (!tab || tab.dataset.wired === "1") return;
  tab.dataset.wired = "1";
  document.getElementById("traefikRoutesRefresh")?.addEventListener("click", () => loadTraefikRoutesPanel());
  document.getElementById("traefikMergeBtn")?.addEventListener("click", () => traefikMergeFragment());
  document.getElementById("traefikLoadFragmentBtn")?.addEventListener("click", () => traefikFetchManifestFragment(false));
  document.getElementById("traefikLoadMergeBtn")?.addEventListener("click", () => traefikFetchManifestFragment(true));
}

async function traefikFetchManifestFragment(alsoMerge) {
  const slugIn = document.getElementById("traefikFragmentSlug");
  const ta = document.getElementById("traefikMergeYaml");
  const msg = document.getElementById("traefikRoutesMsg");
  const slug = slugIn?.value?.trim() || "";
  if (!slug) {
    if (msg) msg.textContent = "Enter a registry id (hosted app slug).";
    return;
  }
  const tok = controlToken();
  if (dashboardTokenRequired() && !tok) {
    if (msg) msg.textContent = "Set the control token on the Control tab.";
    return;
  }
  if (msg) msg.textContent = alsoMerge ? "Loading and merging…" : "Loading fragment…";
  try {
    const res = await fetch("/api/traefik/fragment-from-manifest", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Control-Token": tok,
      },
      body: JSON.stringify({ slug, token: tok }),
    });
    const data = await res.json();
    if (!data.ok) {
      if (msg) msg.textContent = data.error || `HTTP ${res.status}`;
      return;
    }
    const yamlText = data.yaml || "";
    if (ta) ta.value = yamlText;
    if (!alsoMerge) {
      if (msg) msg.textContent = "Fragment loaded into the textarea. Review, then Merge, or use Load and merge.";
      return;
    }
    const mres = await fetch("/api/traefik/merge-fragment", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Control-Token": tok,
      },
      body: JSON.stringify({ yaml: yamlText, token: tok }),
    });
    const mdata = await mres.json();
    if (!mdata.ok) {
      if (msg) msg.textContent = mdata.message || mdata.error || "Merge failed";
      return;
    }
    if (msg) msg.textContent = mdata.message || "Merged.";
    await loadTraefikRoutesPanel();
  } catch (e) {
    if (msg) msg.textContent = String(e.message || e);
  }
}

async function loadTraefikRoutesPanel() {
  const msg = document.getElementById("traefikRoutesMsg");
  const pathEl = document.getElementById("traefikRoutesPath");
  const rb = document.getElementById("traefikRoutersBody");
  const sb = document.getElementById("traefikServicesBody");
  const hh = document.getElementById("traefikHostedHints");
  if (!rb || !sb) return;
  try {
    const res = await fetch("/api/traefik/routes");
    const data = await res.json();
    if (!data.ok) {
      if (pathEl) pathEl.textContent = data.path || "—";
      rb.innerHTML = `<tr><td colspan="5">${escapeHtml(data.error || "Failed to load")}</td></tr>`;
      sb.innerHTML = "";
      if (hh) hh.innerHTML = "";
      return;
    }
    if (pathEl) pathEl.textContent = data.path || "—";
    const routers = data.routers || [];
    rb.innerHTML = routers.length
      ? routers
          .map(
            (r) =>
              `<tr><td><code>${escapeHtml(r.key)}</code></td><td>${escapeHtml(String(r.rule || "—"))}</td><td><code>${escapeHtml(String(r.service || "—"))}</code></td><td>${escapeHtml((r.entryPoints || []).join(", ") || "—")}</td><td>${r.tls ? "yes" : "—"}</td></tr>`,
          )
          .join("")
      : `<tr><td colspan="5" class="muted">No routers</td></tr>`;
    const services = data.services || [];
    sb.innerHTML = services.length
      ? services
          .map((s) => {
            const urls = (s.urls || []).join(", ") || "—";
            return `<tr><td><code>${escapeHtml(s.key)}</code></td><td>${escapeHtml(urls)}</td></tr>`;
          })
          .join("")
      : `<tr><td colspan="2" class="muted">No services</td></tr>`;

    if (hh) {
      const hints = data.hosted_hints || [];
      if (!hints.length) {
        hh.innerHTML =
          "<p class=\"muted small\">No hosted apps with <code>routing.entries</code> (or <code>traefikCleanup</code>) in the manifest.</p>";
      } else {
        hh.innerHTML = hints
          .map((h) => {
            return `<div class="routes-hint-card" data-hint-slug="${escapeAttr(h.slug)}">
            <div class="routes-hint-card__title">${escapeHtml(h.label || h.slug)} <span class="muted">(${escapeHtml(h.slug)})</span></div>
            <div class="routes-hint-card__meta">Manifest: <code>${escapeHtml(h.manifest || "")}</code></div>
            <div class="muted small">${h.in_traefik ? "" : "<strong>Not in dynamic.yml</strong> — "}Routers in file: ${(h.routers_present || []).map((x) => `<code>${escapeHtml(x)}</code>`).join(" ") || "—"}</div>
            <div class="muted small">Services in file: ${(h.services_present || []).map((x) => `<code>${escapeHtml(x)}</code>`).join(" ") || "—"}</div>
            <div class="routes-hint-actions">
              <label><input type="checkbox" class="routes-offboard-strip" checked /> Strip Traefik keys</label>
              <label><input type="checkbox" class="routes-offboard-cf" checked /> Clean KV/R2/D1 (<code>leco.local-cf.yaml</code>)</label>
              <button type="button" class="routes-offboard-btn" data-offboard-slug="${escapeAttr(h.slug)}">Remove from registry</button>
            </div></div>`;
          })
          .join("");
        hh.querySelectorAll("[data-offboard-slug]").forEach((btn) => {
          btn.addEventListener("click", () => {
            const slug = btn.getAttribute("data-offboard-slug") || "";
            const card = btn.closest("[data-hint-slug]");
            const strip = card?.querySelector(".routes-offboard-strip")?.checked !== false;
            const cleanCf = card?.querySelector(".routes-offboard-cf")?.checked !== false;
            runHostedOffboard(slug, strip, cleanCf);
          });
        });
      }
    }
    if (msg) msg.textContent = "";
  } catch (e) {
    rb.innerHTML = `<tr><td colspan="5">${escapeHtml(e.message || String(e))}</td></tr>`;
  }
}

async function runHostedOffboard(slug, stripTraefik, cleanLocalCf) {
  const ok = await showAppConfirm({
    title: `Remove hosted app ${slug}`,
    message:
      "Runs leco-app ecosystem-unregister: removes this id from config/leco-registry.yaml, optionally strips Traefik routers/services from traefik/dynamic.yml, and deletes local KV/R2/D1 resources listed in leco.local-cf.yaml. Traefik picks up dynamic.yml changes via file watch (no Traefik restart).",
    confirmText: "Remove",
  });
  if (!ok) return;
  const msg = document.getElementById("traefikRoutesMsg");
  const tok = controlToken();
  if (dashboardTokenRequired() && !tok) {
    if (msg) msg.textContent = "Set the control token on the Control tab.";
    return;
  }
  try {
    const res = await fetch(`/api/hosted-apps/${encodeURIComponent(slug)}/offboard`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Control-Token": tok,
      },
      body: JSON.stringify({
        strip_traefik: stripTraefik,
        clean_local_cf: cleanLocalCf,
        token: tok,
      }),
    });
    const data = await res.json();
    if (!data.ok) {
      if (msg) msg.textContent = data.error || JSON.stringify(data);
      return;
    }
    if (msg)
      msg.textContent = `Removed ${slug}. registry_removed=${data.registry_removed}. See browser console for details.`;
    console.info("offboard result", data);
    await loadTraefikRoutesPanel();
    if (activeTab === "hostedAppsTab") loadHostedAppsList();
  } catch (e) {
    if (msg) msg.textContent = String(e.message || e);
  }
}

async function traefikMergeFragment() {
  const ta = document.getElementById("traefikMergeYaml");
  const msg = document.getElementById("traefikRoutesMsg");
  const yamlText = ta?.value?.trim() || "";
  if (!yamlText) {
    if (msg) msg.textContent = "Paste YAML first.";
    return;
  }
  const tok = controlToken();
  if (dashboardTokenRequired() && !tok) {
    if (msg) msg.textContent = "Set the control token on the Control tab.";
    return;
  }
  try {
    const res = await fetch("/api/traefik/merge-fragment", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Control-Token": tok,
      },
      body: JSON.stringify({ yaml: yamlText, token: tok }),
    });
    const data = await res.json();
    if (msg) msg.textContent = data.message || JSON.stringify(data);
    if (res.ok && data.ok) await loadTraefikRoutesPanel();
  } catch (e) {
    if (msg) msg.textContent = String(e.message || e);
  }
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

    grid.innerHTML = partitionControlTargets(targets)
      .map(({ group, items }) => {
        const meta = CONTROL_GROUP_META[group] || {
          title: String(group).replace(/-/g, " "),
          lead: "",
          sectionClass: "",
        };
        const sid = `ctl-grp-${String(group).replace(/[^a-zA-Z0-9_-]/g, "-")}`;
        const cards = items.map((t) => controlTargetCardHtml(SB, t)).join("");
        const lead = meta.lead
          ? `<p class="control-target-group__lead">${escapeHtml(meta.lead)}</p>`
          : "";
        return `<section class="control-target-group${meta.sectionClass}" aria-labelledby="${escapeAttr(sid)}">
      <h3 class="control-target-group__title" id="${escapeAttr(sid)}">${escapeHtml(meta.title)}</h3>
      ${lead}
      <div class="control-grid">${cards}</div>
    </section>`;
      })
      .join("");

    grid.querySelectorAll("button.ctrl-act").forEach((btn) => {
      btn.addEventListener("click", () =>
        runControlAction(
          btn.getAttribute("data-control-target") || "",
          btn.getAttribute("data-action") || "",
          btn.getAttribute("data-label") || "",
        ),
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

/** Close modals that use high z-index inside `.wrap` so the control overlay is on top. */
function prepareDashboardGlobalOverlayLayer() {
  document.getElementById("hostedBrowseModal")?.classList.add("is-hidden");
  document.body.style.overflow = "";
  closeHostedChartExpand();
}

/**
 * @param {ReadableStreamDefaultReader<Uint8Array>} reader
 * @param {(t: string) => void} appendStreamLog
 */
async function readNdjsonLinesFromReader(reader, appendStreamLog) {
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
      /* trailing garbage */
    }
  }
  return finalResult;
}

/** When fetch body has no getReader (older browsers / proxies), parse buffered NDJSON. */
function parseNdjsonFromFullText(text, appendStreamLog) {
  let finalResult = null;
  for (const line of text.split("\n")) {
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
  return finalResult;
}

/**
 * Shared overlay + NDJSON stream reader (Control actions, Register app, …).
 * @param {{ title: string, url: string, body: Record<string, unknown>, actionVerb?: string, onFinally?: (outcome: { ok: boolean, result?: object, error?: string }) => void | Promise<void> }} opts
 * @returns {Promise<{ ok: boolean, result?: object, error?: string }>}
 */
async function runDashboardStreamOverlay(opts) {
  const { title, url, body, actionVerb = "operation", onFinally } = opts;
  /** @type {{ ok: boolean, result?: object, error?: string }} */
  const outcome = { ok: false };
  initControlActionOverlay();
  prepareDashboardGlobalOverlayLayer();
  const overlay = document.getElementById("controlActionOverlay");
  const titleEl = document.getElementById("controlActionTitle");
  const liveEl = document.getElementById("controlActionLive");
  const summaryEl = document.getElementById("controlActionSummary");
  const snippetEl = document.getElementById("controlActionSnippet");
  const spinnerEl = document.getElementById("controlActionSpinner");
  const runningBar = document.getElementById("controlActionRunningBar");
  const doneBar = document.getElementById("controlActionDoneBar");
  const out = document.getElementById("controlResult");

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

  if (overlay) {
    overlay.removeAttribute("hidden");
    overlay.hidden = false;
    overlay.style.zIndex = "32000";
  }
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
  if (titleEl) titleEl.textContent = title;
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

  const tok = controlToken();
  const payload = { ...body, token: tok };

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Control-Token": tok },
      body: JSON.stringify(payload),
      signal: ac.signal,
      cache: "no-store",
    });

    if (!res.ok) {
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
      outcome.ok = ok;
      outcome.result = data;
      if (!ok) outcome.error = data?.error ? String(data.error) : `HTTP ${res.status}`;
      if (titleEl) titleEl.textContent = ok ? `Done · ${actionVerb}` : `Finished · ${actionVerb}`;
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
      let finalResult = null;
      if (res.body && typeof res.body.getReader === "function") {
        try {
          finalResult = await readNdjsonLinesFromReader(res.body.getReader(), appendStreamLog);
        } catch (streamErr) {
          appendStreamLog(`\n[ui] stream read failed: ${String(streamErr.message || streamErr)}\n`);
          finalResult = { ok: false, error: String(streamErr.message || streamErr) };
        }
      } else {
        const txt = await res.text();
        finalResult = parseNdjsonFromFullText(txt, appendStreamLog);
      }

      const data = finalResult || { ok: false, error: "no result from stream" };
      const fullLog = logChunks.join("");
      const merged = { ...data };
      if (fullLog) merged.log_stream = fullLog;
      const text = JSON.stringify(merged, null, 2);
      lastControlActionResultText = text;
      if (out) out.textContent = text;
      const details = document.querySelector(".control-response-details");
      if (details && data?.ok === false) details.open = true;

      const ok = data && data.ok === true;
      outcome.ok = ok;
      outcome.result = data;
      if (!ok) outcome.error = data?.error ? String(data.error) : "stream failed";
      if (titleEl) titleEl.textContent = ok ? `Done · ${actionVerb}` : `Finished · ${actionVerb}`;
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
    outcome.ok = false;
    outcome.error = msg;
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
    try {
      await onFinally?.(outcome);
    } catch (_) {
      /* ignore */
    }
    try {
      if (lastControlActionResultText) {
        localStorage.setItem("dashboard_last_control_result", lastControlActionResultText);
      }
    } catch (_) {
      /* ignore */
    }
  }
  return outcome;
}

/**
 * Register via JSON POST (works through Traefik; streaming NDJSON is often buffered).
 * Runs ecosystem-register + optional leco-app deploy in one server round-trip.
 */
async function runDashboardSyncRegisterOverlay(opts) {
  const { body, title, actionVerb = "register", onFinally } = opts;
  /** @type {{ ok: boolean, result?: object, error?: string }} */
  const outcome = { ok: false };
  initControlActionOverlay();
  prepareDashboardGlobalOverlayLayer();
  const overlay = document.getElementById("controlActionOverlay");
  const titleEl = document.getElementById("controlActionTitle");
  const liveEl = document.getElementById("controlActionLive");
  const summaryEl = document.getElementById("controlActionSummary");
  const snippetEl = document.getElementById("controlActionSnippet");
  const spinnerEl = document.getElementById("controlActionSpinner");
  const runningBar = document.getElementById("controlActionRunningBar");
  const doneBar = document.getElementById("controlActionDoneBar");
  const out = document.getElementById("controlResult");

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

  if (overlay) {
    overlay.removeAttribute("hidden");
    overlay.hidden = false;
    overlay.style.zIndex = "32000";
  }
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
  if (titleEl) titleEl.textContent = title;
  if (summaryEl) {
    summaryEl.classList.add("is-hidden");
    summaryEl.textContent = "";
    summaryEl.classList.remove("control-action-summary--ok", "control-action-summary--bad");
  }
  if (snippetEl) {
    snippetEl.classList.remove("is-hidden");
    snippetEl.classList.add("control-action-snippet--live");
    snippetEl.textContent =
      "Running on server: ecosystem-register, local CF (if any), then optional docker compose deploy.\nThis can take several minutes — output appears here when finished.\n";
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

  const tok = controlToken();
  const payload = { ...body, token: tok };

  try {
    const res = await fetch("/api/leco/register", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Control-Token": tok },
      body: JSON.stringify(payload),
      signal: ac.signal,
      cache: "no-store",
    });
    let data = {};
    try {
      data = await res.json();
    } catch (_) {
      const raw = await res.text().catch(() => "");
      data = { ok: false, error: raw || `HTTP ${res.status}` };
    }
    if (spinnerEl) spinnerEl.classList.add("is-hidden");

    const parts = [];
    if (data.leco_register_log) parts.push(String(data.leco_register_log));
    if (data.deploy_stack_ran && data.deploy_log) parts.push("--- leco-app deploy ---\n" + String(data.deploy_log));
    const logText =
      parts.length > 0 ? parts.join("\n\n") : data.error ? String(data.error) : JSON.stringify(data, null, 2);
    if (snippetEl) {
      snippetEl.textContent = logText || "(empty response)";
      snippetEl.scrollTop = snippetEl.scrollHeight;
    }

    const registerOk = res.ok && data.ok === true;
    const deployFine = data.deploy_stack_ran !== true || data.deploy_ok === true;
    const ok = registerOk && deployFine;
    outcome.ok = ok;
    outcome.result = data;
    if (!ok) {
      outcome.error = data.error
        ? String(data.error)
        : !deployFine
          ? "Docker deploy failed (registry may still be updated). See log below."
          : `HTTP ${res.status}`;
    }

    const text = JSON.stringify(data, null, 2);
    lastControlActionResultText = text;
    if (out) out.textContent = text;
    const details = document.querySelector(".control-response-details");
    if (details && !ok) details.open = true;
    if (titleEl) titleEl.textContent = ok ? `Done · ${actionVerb}` : `Finished · ${actionVerb}`;
    if (liveEl) {
      const sec = Math.max(0, Math.round((Date.now() - t0) / 1000));
      liveEl.textContent = ok ? `Succeeded in ${sec}s` : `Completed in ${sec}s (see log)`;
    }
    if (summaryEl) {
      summaryEl.classList.remove("is-hidden");
      summaryEl.classList.toggle("control-action-summary--ok", ok);
      summaryEl.classList.toggle("control-action-summary--bad", !ok);
      summaryEl.textContent = ok
        ? "Registered and deploy finished (or skipped if unchecked)."
        : outcome.error || "Request failed.";
    }
  } catch (e) {
    const aborted = e && (e.name === "AbortError" || e.code === 20);
    const msg = aborted ? "Request cancelled." : String(e.message || e);
    outcome.ok = false;
    outcome.error = msg;
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
    if (snippetEl) snippetEl.textContent = msg;
  } finally {
    clearTimers();
    controlActionOverlayAbort = null;
    if (spinnerEl) spinnerEl.classList.add("is-hidden");
    if (runningBar) runningBar.classList.add("is-hidden");
    if (doneBar) doneBar.classList.remove("is-hidden");
    try {
      await onFinally?.(outcome);
    } catch (_) {
      /* ignore */
    }
    try {
      if (lastControlActionResultText) {
        localStorage.setItem("dashboard_last_control_result", lastControlActionResultText);
      }
    } catch (_) {
      /* ignore */
    }
  }
  return outcome;
}

function hideControlActionOverlay() {
  const o = document.getElementById("controlActionOverlay");
  if (!o) return;
  o.hidden = true;
  o.setAttribute("hidden", "");
}

async function runControlAction(targetId, action, cardLabel) {
  const label = (cardLabel || targetId || "").trim();
  const act = String(action || "").toLowerCase();
  return runDashboardStreamOverlay({
    title: `${action} · ${label}`,
    url: "/api/control/stream",
    body: { target_id: targetId, action },
    actionVerb: action,
    async onFinally() {
      loadControlTargets();
      if (act === "remove" || act === "reset") {
        await loadHostedAppsList();
      } else if (activeTab === "hostedAppsTab") {
        await refreshHostedAppsPanel().catch(() => {});
      }
      loadOverview().catch(() => {});
    },
  });
}

function updateCharts(data) {
  if (!ensureCharts()) return;
  const s = data.system_status || {};
  const services = data.services || [];

  const stamp = new Date(data.generated_at).toLocaleTimeString();
  const dt = s.docker_totals_all_running || {};
  trendHistory.labels.push(stamp);
  trendHistory.cpu.push(Number(dt.cpu_percent ?? s.aggregate_cpu_percent ?? 0));
  trendHistory.memory.push(Number(dt.memory_percent ?? s.aggregate_memory_percent ?? 0));
  trendHistory.errors.push(Number(s.total_error_rate_per_min || 0));
  if (trendHistory.labels.length > MAX_POINTS) {
    trendHistory.labels.shift();
    trendHistory.cpu.shift();
    trendHistory.memory.shift();
    trendHistory.errors.shift();
  }

  applyTrendHistoryToLineChart();
  saveTrendHistoryCache();

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

/** Ollama service card: table of installed / running models from overview `ollama_llm`. */
function formatOllamaLlmBlock(ollama, s) {
  if (!s || s.container !== "ollama") return "";
  if (!ollama) {
    return `<div class="svc-card__extras svc-card__extras--llm"><div class="svc-card__extras-title">LLM modules (Ollama)</div><p class="muted small">Model list not available.</p></div>`;
  }
  if (ollama.error) {
    return `<div class="svc-card__extras svc-card__extras--llm"><div class="svc-card__extras-title">LLM modules (Ollama)</div><p class="muted small">${escapeHtml(ollama.error)}</p></div>`;
  }
  const ver = ollama.server_version;
  const verStr =
    ver && typeof ver === "object" ? [ver.version, ver.ollama_version].filter(Boolean).join(" · ") : "";
  const meta =
    verStr || ollama.ollama_base
      ? `<p class="muted small svc-llm-meta">${verStr ? `Server ${escapeHtml(verStr)}` : ""}${
          verStr && ollama.ollama_base ? " · " : ""
        }<code>${escapeHtml(String(ollama.ollama_base || ""))}</code></p>`
      : "";
  if (!ollama.ollama_reachable) {
    return `<div class="svc-card__extras svc-card__extras--llm"><div class="svc-card__extras-title">LLM modules (Ollama)</div>${meta}<p class="muted small">API unreachable — start the <code>ollama</code> container.</p></div>`;
  }
  const rows = Array.isArray(ollama.rows) ? ollama.rows : [];
  if (rows.length === 0) {
    return `<div class="svc-card__extras svc-card__extras--llm"><div class="svc-card__extras-title">LLM modules (Ollama)</div>${meta}<p class="muted small">No models on disk. Pull from the <strong>Ollama</strong> section below or run <code>ollama pull &lt;model&gt;</code>.</p></div>`;
  }
  const sorted = [...rows].sort((a, b) => {
    if (!!a.running !== !!b.running) return a.running ? -1 : 1;
    if (!!a.installed !== !!b.installed) return a.installed ? -1 : 1;
    return String(a.name || "").localeCompare(String(b.name || ""));
  });
  const sum = `<p class="muted small svc-llm-counts">${Number(ollama.running_count || 0)} loaded in RAM · ${Number(ollama.installed_count || 0)} on disk</p>`;
  const thead = `<thead><tr><th>Model</th><th>State</th><th>Disk</th><th>VRAM</th><th>Params · quant</th></tr></thead>`;
  const tbody = sorted
    .map((r) => {
      const name = escapeHtml(r.name || "—");
      let stateLabel = "—";
      let stateClass = "pill svc-llm-pill--idle";
      if (r.running) {
        stateLabel = "Running";
        stateClass = "pill ok svc-llm-pill";
      } else if (r.installed) {
        stateLabel = "On disk";
        stateClass = "pill svc-llm-pill--disk";
      } else if (r.pinned) {
        stateLabel = "Pinned only";
        stateClass = "pill warn svc-llm-pill";
      }
      const pinNote = r.pinned ? ` <span class="muted">(pinned)</span>` : "";
      const disk = r.size != null ? formatBytes(r.size) : "—";
      let vramCell = "—";
      if (r.running) {
        const v = r.size_vram != null ? formatBytes(r.size_vram) : "0 B";
        const exp = r.expires_at ? ` · ${escapeHtml(formatOllamaExpires(r.expires_at))}` : "";
        vramCell = `${escapeHtml(v)}${exp}`;
      }
      const pq = [r.parameter_size, r.quantization_level].filter(Boolean).join(" · ") || "—";
      return `<tr>
        <td><code>${name}</code>${pinNote}</td>
        <td><span class="${stateClass}">${stateLabel}</span></td>
        <td class="muted">${escapeHtml(disk)}</td>
        <td class="muted">${vramCell}</td>
        <td class="muted small">${escapeHtml(pq)}</td>
      </tr>`;
    })
    .join("");
  return `<div class="svc-card__extras svc-card__extras--llm">
    <div class="svc-card__extras-title">LLM modules (Ollama)</div>
    ${meta}
    ${sum}
    <div class="svc-llm-table-wrap"><table class="svc-llm-table">${thead}<tbody>${tbody}</tbody></table></div>
  </div>`;
}

function renderServices(data) {
  const SB = serviceBrandUi();
  const ollamaLlm = data.ollama_llm || null;
  document.getElementById("services").innerHTML = data.services.map((s) => {
    const brand = SB.getBrandForManagedService(s);
    const running = s.container_info.status === "running";
    const checks = s.url_checks.length
      ? s.url_checks.map((c) => {
          const u = c.url || "";
          return `<div class="row"><a href="${escapeAttr(u)}"${externalNavigationAttrs(u)}>${escapeHtml(u)}</a>${urlProbePill(c)}</div>`;
        }).join("")
      : "<div class='muted'>No HTTP endpoint</div>";

    const credsBlock =
      (s.credentials || []).length > 0
        ? `<div class="svc-card__extras">
            <div class="svc-card__extras-title">Credentials</div>
            <ul class="svc-creds">${(s.credentials || []).map((c) => `<li>${escapeHtml(c)}</li>`).join("")}</ul>
          </div>`
        : "";
    const hubBlock =
      s.hub_path
        ? `<div class="svc-card__extras">
            <div class="svc-card__extras-title">Service hub</div>
            <div class="row svc-mgmt-row"><a href="${escapeHtml(s.hub_path)}">Open hub (credentials · TCP · GUIs)</a></div>
          </div>`
        : "";
    const connBlock =
      (s.connection_strings || []).length > 0
        ? `<div class="svc-card__extras">
            <div class="svc-card__extras-title">Connection strings</div>
            <ul class="svc-creds svc-creds--mono">${(s.connection_strings || [])
              .map((c) => `<li><code>${escapeHtml(c)}</code></li>`)
              .join("")}</ul>
          </div>`
        : "";
    const dbGuiBlock =
      (s.database_guis || []).length > 0
        ? `<div class="svc-card__extras">
            <div class="svc-card__extras-title">Database / storage GUI</div>
            ${(s.database_guis || []).map((l) => `<div class="row svc-mgmt-row">${hubGuiLinkRow(l)}</div>`).join("")}
          </div>`
        : "";
    const insightsBlock =
      (s.insights || []).length > 0
        ? `<div class="svc-card__extras">
            <div class="svc-card__extras-title">Insights &amp; analytics</div>
            <ul class="svc-creds">${(s.insights || []).map((c) => `<li>${escapeHtml(c)}</li>`).join("")}</ul>
          </div>`
        : "";
    const mgmtBlock =
      (s.management_links || []).length > 0
        ? `<div class="svc-card__extras">
            <div class="svc-card__extras-title">Management &amp; explorer</div>
            ${(s.management_links || []).map((l) => `<div class="row svc-mgmt-row">${hubGuiLinkRow(l)}</div>`).join("")}
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
        ${hubBlock}
        ${connBlock}
        ${dbGuiBlock}
        ${insightsBlock}
        ${mgmtBlock}
        ${formatOllamaLlmBlock(ollamaLlm, s)}
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
      } else if (activeTab === "hostedAppsTab") {
        refreshHostedAppsPanel();
      } else if (activeTab === "routesTab") {
        loadTraefikRoutesPanel();
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
  try {
    const [overviewRes, cfRes, metricsRes] = await Promise.all([
      fetch("/api/overview"),
      fetch("/api/cloudflare-local"),
      fetch(`/api/metrics/history?limit=${OVERVIEW_METRICS_LIMIT}`),
    ]);
    const data = await overviewRes.json();
    const cloudflareData = await cfRes.json();
    let metricsData = { points: [], generated_at: null };
    try {
      if (metricsRes.ok) {
        const mj = await metricsRes.json();
        if (mj && typeof mj === "object" && Array.isArray(mj.points)) metricsData = mj;
      }
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
    /* Do not saveMetricsCache here: overview uses a shorter history limit and would overwrite the Deep metrics tab cache. */
  } catch (e) {
    console.warn("loadOverview failed", e);
  }
}

function initControlInfraBulkBar() {
  const bar = document.getElementById("controlInfraBulkBar");
  if (!bar || bar.dataset.wired === "1") return;
  bar.dataset.wired = "1";
  const INFRA_STACK = "stack-infra-all";
  bar.querySelectorAll("[data-infra-bulk-action]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const action = btn.getAttribute("data-infra-bulk-action");
      const lbl = btn.getAttribute("data-infra-bulk-label") || action;
      if (!action) return;
      const destructive = action === "stop" || action === "restart" || action === "deploy";
      if (destructive) {
        const ok = await showAppConfirm({
          title: lbl,
          message:
            "Runs docker compose for infra/docker-compose.yml (MySQL, Redis, Mailpit, Adminer, cache lab, …). Can take a minute; stop/restart affect all infra services.",
          confirmText: "Continue",
        });
        if (!ok) return;
      }
      runControlAction(INFRA_STACK, action, lbl);
    });
  });
}

function initControlBulkBar() {
  const bar = document.getElementById("controlBulkBar");
  if (bar && bar.dataset.wired !== "1") {
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
  initControlInfraBulkBar();
}

async function bootstrap() {
  initAppModal();
  initTabs();
  initHostMetricsPanelRefresh();
  initControlBulkBar();
  initHostedAppsLogToolbar();
  initHostedRegisterWizard();
  initRoutesTab();
  initHostedChartExpandModal();
  hydrateTrendHistoryFromCache();
  hydrateOverviewFromCache();
  applyTrendHistoryToLineChart();
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
    } else if (activeTab === "hostedAppsTab") {
      refreshHostedAppsPanel();
    } else if (activeTab === "routesTab") {
      loadTraefikRoutesPanel();
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
  try {
    const savedTab = localStorage.getItem(LS_ACTIVE_TAB_KEY);
    if (savedTab && document.getElementById(savedTab)) activateTab(savedTab);
  } catch (_) {
    /* ignore */
  }
  scheduleRefresh();
}

bootstrap();
