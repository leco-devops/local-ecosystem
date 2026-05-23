let refreshTimer = null;
let tickTimer = null;
let nextRefreshEpoch = null;
let charts = { line: null, bar: null, pie: null };
let metricsCharts = { cpu: null, ram: null, net: null, iops: null, temp: null };
let metricsTimer = null;
let activeTab = "overviewTab";
let hostedAppsList = [];
let hostedSelectedSlug = "";
/** Latest hosted app snapshot for detail panel (seed import confirm, dump CLI). */
let hostedLastDetailSnap = null;
/** Per-slug seed import checkbox state: { [slug]: { [importId]: boolean } }. */
const hostedSeedImportPrefs = {};
let hostedAppCharts = { cpu: null, mem: null, net: null };
let hostedLogStreamAbort = null;
let hostedLogStreamStarted = false;
/** Slug|tail|service — restart stream when this changes while live. */
let hostedLogStreamLiveKey = "";
let hostedPanelRequestSeq = 0;
let hostedExpandChart = null;
const routePanelState = {
  routersByKey: new Map(),
  servicesByKey: new Map(),
};
const trendHistory = { labels: [], cpu: [], memory: [], errors: [] };
const MAX_POINTS = 25;
const METRICS_MAX = 60;
let lastOverviewData = null;
let lastCloudflareData = null;
let lastMetricsData = null;
let overviewHostedAppsPayload = { apps: [], generated_at: null };
let overviewHostedAppsCachedAtMs = 0;
let overviewHostedAppsInFlight = null;

const LS_OVERVIEW_CACHE_KEY = "local_ecosystem_dashboard_overview_v1";
const LS_OVERVIEW_MAX_AGE_MS = 1000 * 60 * 60 * 48;
const LS_METRICS_CACHE_KEY = "local_ecosystem_dashboard_metrics_v1";
/** Persist a rolling 24h metrics window for historical overview / deep-metrics seed. */
const LS_METRICS_MAX_AGE_MS = 1000 * 60 * 60 * 24;
const METRICS_LOCAL_RETENTION_MS = 1000 * 60 * 60 * 24;
const METRICS_LOCAL_MAX_POINTS = 5000;
const METRICS_CACHE_MAX_BYTES = 4_500_000;
const LS_TREND_CACHE_KEY = "local_ecosystem_dashboard_infra_trend_v1";
const LS_ACTIVE_TAB_KEY = "dashboard_active_tab";
const LS_REFRESH_RATE_KEY = "dashboard_refresh_rate_ms";
const REFRESH_RATE_OPTIONS = new Set(["5000", "10000", "30000", "60000", "0"]);
/** Bump when cache shape changes so stale / corrupted entries are dropped. */
const CACHE_SCHEMA_VERSION = 7;
const LS_PRELOADER_COLLAPSED_KEY = "dashboard_preloader_collapsed";
const GLOBAL_PRELOADER_MIN_VISIBLE_MS = 220;
const OVERVIEW_HOSTED_APPS_CACHE_MS = 90 * 1000;
const TAB_LABELS = {
  overviewTab: "Overview",
  referenceTab: "Reference",
  infrastructureTab: "Infrastructure",
  metricsTab: "Metrics",
  controlTab: "Control",
  platformTab: "Platform",
  hostedAppsTab: "Hosted apps",
  routesTab: "Routes",
  docsTab: "Docs",
  developTab: "Develop",
  logsTab: "Logs",
};
/** Overview CF bar chart + KPI — mirrors cloudflare-local stack (cf-leco-service-registry.json). */
const CF_LOCAL_OVERVIEW_SERVICES = [
  { key: "r2", label: "R2", color: "#22d3ee" },
  { key: "kv", label: "KV", color: "#a78bfa" },
  { key: "d1", label: "D1", color: "#34d399" },
  { key: "workers", label: "Wrk", color: "#fbbf24" },
  { key: "browser", label: "Br", color: "#fb923c" },
  { key: "autoscale", label: "Au", color: "#f472b6" },
  { key: "valkey", label: "Vk", color: "#818cf8" },
  { key: "minio", label: "MinIO", color: "#c084fc", altKeys: ["s3"] },
];
const CF_LOCAL_OVERVIEW_TOTAL = CF_LOCAL_OVERVIEW_SERVICES.length;

function cfServiceReachable(svc, entry) {
  if (!svc || !entry) return false;
  if (svc[entry.key]?.reachable) return true;
  for (const alt of entry.altKeys || []) {
    if (svc[alt]?.reachable) return true;
  }
  return false;
}

function countCfLocalReachable(cf) {
  const svc = cf?.services || {};
  return CF_LOCAL_OVERVIEW_SERVICES.filter((entry) => cfServiceReachable(svc, entry)).length;
}

const globalPreloaderState = {
  root: null,
  summary: null,
  list: null,
  toggle: null,
  seq: 0,
  active: new Map(),
  sharedGetRequests: new Map(),
  nativeFetch: null,
  preloaderWired: false,
  clickWired: false,
};

function globalPreloaderCollapsedTitle() {
  if (!globalPreloaderState.active.size) return "";
  return Array.from(globalPreloaderState.active.values())
    .map((r) => {
      const d = r.detail ? ` — ${r.detail}` : "";
      const p = r.path ? ` · ${r.path}` : "";
      return `${r.label}${d}${p}`;
    })
    .join(" · ");
}

function syncGlobalPreloaderCollapsedUi() {
  const root = globalPreloaderState.root;
  const toggle = globalPreloaderState.toggle;
  if (!root) return;
  const collapsed = root.classList.contains("dashboard-global-preloader--collapsed");
  if (toggle) {
    toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    toggle.textContent = collapsed ? "▼" : "▲";
    toggle.title = collapsed ? "Expand status" : "Collapse to thin progress bar";
  }
  if (collapsed) {
    const t = globalPreloaderCollapsedTitle();
    if (t) root.title = t;
    else root.removeAttribute("title");
  } else {
    root.removeAttribute("title");
  }
}

function initGlobalPreloader() {
  if (globalPreloaderState.root) return;
  globalPreloaderState.root = document.getElementById("dashboardGlobalPreloader");
  globalPreloaderState.summary = document.getElementById("dashboardGlobalPreloaderSummary");
  globalPreloaderState.list = document.getElementById("dashboardGlobalPreloaderList");
  globalPreloaderState.toggle = document.getElementById("dashboardGlobalPreloaderToggle");
  const root = globalPreloaderState.root;
  if (root) {
    try {
      if (localStorage.getItem(LS_PRELOADER_COLLAPSED_KEY) === "1") {
        root.classList.add("dashboard-global-preloader--collapsed");
      }
    } catch (_) {
      /* ignore */
    }
    syncGlobalPreloaderCollapsedUi();
  }
  if (globalPreloaderState.toggle && !globalPreloaderState.preloaderWired) {
    globalPreloaderState.preloaderWired = true;
    globalPreloaderState.toggle.addEventListener("click", (e) => {
      e.stopPropagation();
      const r = globalPreloaderState.root;
      if (!r) return;
      r.classList.toggle("dashboard-global-preloader--collapsed");
      const on = r.classList.contains("dashboard-global-preloader--collapsed");
      try {
        localStorage.setItem(LS_PRELOADER_COLLAPSED_KEY, on ? "1" : "0");
      } catch (_) {
        /* ignore */
      }
      syncGlobalPreloaderCollapsedUi();
    });
  }
}

function syncTabInlineProgress() {
  const byTab = new Map();
  for (const rec of globalPreloaderState.active.values()) {
    const tid = (rec.tabId || "").trim();
    if (!tid) continue;
    byTab.set(tid, (byTab.get(tid) || 0) + 1);
  }
  document.querySelectorAll(".tabs .tab-btn").forEach((btn) => {
    const tid = btn.dataset.tab || "";
    const n = byTab.get(tid) || 0;
    const on = n > 0;
    btn.classList.toggle("tab-btn--loading", on);
    btn.setAttribute("aria-busy", on ? "true" : "false");
    if (on) btn.title = `${n} backend request(s) for this tab`;
    else btn.removeAttribute("title");
  });
}

function normalizeHostedSidebarSlug(s) {
  const raw = String(s || "").trim();
  if (!raw) return "";
  try {
    return decodeURIComponent(raw);
  } catch (_) {
    return raw;
  }
}

function syncHostedAppSidebarProgress() {
  const nav = document.getElementById("hostedAppsSidebar");
  if (!nav) return;
  const bySlug = new Map();
  const tipsBySlug = new Map();
  for (const rec of globalPreloaderState.active.values()) {
    const key = normalizeHostedSidebarSlug(rec.hostedSlug);
    if (!key) continue;
    bySlug.set(key, (bySlug.get(key) || 0) + 1);
    if (rec.label) {
      const arr = tipsBySlug.get(key) || [];
      if (arr.length < 4) arr.push(rec.label);
      tipsBySlug.set(key, arr);
    }
  }
  nav.querySelectorAll("[data-hosted-slug]").forEach((btn) => {
    const slug = normalizeHostedSidebarSlug(btn.getAttribute("data-hosted-slug"));
    const n = bySlug.get(slug) || 0;
    const on = n > 0;
    btn.classList.toggle("hosted-app-sidebar-btn--loading", on);
    btn.setAttribute("aria-busy", on ? "true" : "false");
    if (on) {
      const tips = tipsBySlug.get(slug) || [];
      btn.title =
        tips.length > 0
          ? tips.join(" · ") + (n > tips.length ? ` (+${n - tips.length} more)` : "")
          : `${n} backend request(s) for this app`;
    } else {
      btn.removeAttribute("title");
    }
  });
}

function renderGlobalPreloader() {
  const root = globalPreloaderState.root;
  if (!root) return;
  const hasActive = globalPreloaderState.active.size > 0;
  root.classList.toggle("is-hidden", !hasActive);
  const n = globalPreloaderState.active.size;
  const summaryEl = globalPreloaderState.summary;
  const listEl = globalPreloaderState.list;
  if (hasActive && summaryEl) {
    summaryEl.textContent = n === 1 ? "1 request in progress" : `${n} requests in progress`;
  }
  if (listEl) {
    if (!hasActive) {
      listEl.innerHTML = "";
    } else {
      const rows = Array.from(globalPreloaderState.active.entries())
        .map(([reqId, rec]) => {
          const path = rec.path ? `<span class="dashboard-global-preloader__path">${escapeHtml(rec.path)}</span>` : "";
          const detail = rec.detail
            ? `<span class="dashboard-global-preloader__item-detail">${escapeHtml(rec.detail)}</span>`
            : "";
          return `<li><span class="dashboard-global-preloader__item-label">${escapeHtml(rec.label)}</span>${detail}${path}</li>`;
        })
        .join("");
      listEl.innerHTML = rows;
    }
  }
  syncGlobalPreloaderCollapsedUi();
  syncTabInlineProgress();
  syncHostedAppSidebarProgress();
}

/** @param {string | { label?: string, path?: string, detail?: string, tabId?: string, hostedSlug?: string }} labelOrOpts */
function beginGlobalPreloader(labelOrOpts) {
  initGlobalPreloader();
  let label = "Working…";
  let path = "";
  let detail = "";
  let tabId = "";
  let hostedSlug = "";
  if (typeof labelOrOpts === "string") {
    label = labelOrOpts.trim() || "Working…";
    tabId = typeof activeTab === "string" ? activeTab : "";
  } else if (labelOrOpts && typeof labelOrOpts === "object") {
    label = String(labelOrOpts.label || "").trim() || "Working…";
    path = String(labelOrOpts.path || "").trim();
    detail = String(labelOrOpts.detail || "").trim();
    tabId = String(labelOrOpts.tabId || "").trim();
    hostedSlug = String(labelOrOpts.hostedSlug || "").trim();
  }
  const id = ++globalPreloaderState.seq;
  globalPreloaderState.active.set(id, {
    label,
    path,
    detail,
    tabId,
    hostedSlug,
    startedAt: Date.now(),
  });
  renderGlobalPreloader();
  return id;
}

function endGlobalPreloader(id) {
  const rec = globalPreloaderState.active.get(id);
  if (!rec) return;
  const elapsed = Date.now() - (rec.startedAt || Date.now());
  const delay = Math.max(0, GLOBAL_PRELOADER_MIN_VISIBLE_MS - elapsed);
  window.setTimeout(() => {
    if (!globalPreloaderState.active.has(id)) return;
    globalPreloaderState.active.delete(id);
    renderGlobalPreloader();
  }, delay);
}

/** Inline spinner on the button/control that triggered a backend call. */
const inlineActionState = new WeakMap();
const actionTriggerState = { el: null, at: 0 };

function noteActionTrigger(el) {
  if (!el || !(el instanceof Element)) return;
  actionTriggerState.el = el;
  actionTriggerState.at = Date.now();
}

function peekActionTrigger(maxAgeMs = 220) {
  const { el, at } = actionTriggerState;
  if (!el || Date.now() - at > maxAgeMs) return null;
  return el;
}

function beginInlineAction(el) {
  if (!el || !(el instanceof Element)) return null;
  let st = inlineActionState.get(el);
  if (!st) {
    st = { count: 0, hadDisabled: !!el.disabled };
    inlineActionState.set(el, st);
  }
  st.count += 1;
  if (st.count === 1) {
    el.classList.add("btn--inline-loading");
    el.setAttribute("aria-busy", "true");
    if (!st.hadDisabled) el.disabled = true;
  }
  return el;
}

function endInlineAction(el) {
  if (!el || !(el instanceof Element)) return;
  const st = inlineActionState.get(el);
  if (!st) return;
  st.count = Math.max(0, st.count - 1);
  if (st.count > 0) return;
  inlineActionState.delete(el);
  el.classList.remove("btn--inline-loading");
  el.removeAttribute("aria-busy");
  if (!st.hadDisabled) el.disabled = false;
}

function resolveFetchTriggerEl(init) {
  const fromInit = init && init.dashboardTriggerEl;
  if (fromInit instanceof Element) return fromInit;
  return peekActionTrigger();
}

function tabLabel(tabId) {
  return TAB_LABELS[tabId] || "tab";
}

function toFetchUrl(input) {
  if (typeof input === "string") return input;
  if (input instanceof URL) return input.toString();
  if (input && typeof input.url === "string") return input.url;
  return "";
}

function isBackendApiUrl(rawUrl) {
  if (!rawUrl) return false;
  if (rawUrl.startsWith("/api/")) return true;
  try {
    const u = new URL(rawUrl, window.location.origin);
    return u.origin === window.location.origin && u.pathname.startsWith("/api/");
  } catch (_) {
    return false;
  }
}

function backendAreaLabel(pathname) {
  const key = (pathname.replace(/^\/api\//, "").split("/")[0] || "").toLowerCase();
  const names = {
    overview: "overview",
    metrics: "metrics",
    logs: "logs",
    control: "control",
    hosted: "hosted apps",
    "hosted-apps": "hosted apps",
    traefik: "routes",
    docs: "docs",
    reference: "reference",
    leco: "registration",
    ollama: "ollama",
    airllm: "airllm",
    ai: "AI settings",
    "cloudflare-local": "cloudflare local",
    "update-catalog": "update catalog",
    ecosystem: "ecosystem updates",
  };
  return names[key] || "LEco DevOps data";
}

function prettyRegistrySlug(slug) {
  try {
    const s = decodeURIComponent(String(slug || "").trim());
    if (!s) return "—";
    return s.replace(/[-_]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  } catch (_) {
    return String(slug || "");
  }
}

function inferDashboardTab(pathname, search) {
  const q = new URLSearchParams(search || "");
  if (
    pathname.startsWith("/api/hosted-apps/") ||
    pathname === "/api/hosted-apps" ||
    pathname.startsWith("/api/leco/") ||
    pathname.startsWith("/api/hosted/")
  ) {
    return "hostedAppsTab";
  }
  if (pathname.startsWith("/api/traefik")) return "routesTab";
  if (pathname.startsWith("/api/docs/")) return "docsTab";
  if (pathname === "/api/reference") return "referenceTab";
  if (pathname === "/api/logs") return "logsTab";
  if (pathname.startsWith("/api/control")) return "controlTab";
  if (pathname.startsWith("/api/ollama/")) return "infrastructureTab";
  if (pathname === "/api/services") return "infrastructureTab";
  if (pathname === "/api/host-metrics/injected") return "metricsTab";
  if (pathname === "/api/metrics/history") {
    const lim = q.get("limit");
    const n = lim != null ? parseInt(lim, 10) : NaN;
    if (!Number.isNaN(n) && n <= 80) return "overviewTab";
    return "metricsTab";
  }
  if (pathname === "/api/overview" || pathname === "/api/cloudflare-local") return "overviewTab";
  return typeof activeTab === "string" ? activeTab : "overviewTab";
}

function resolveFetchPreloaderMeta(input, init) {
  const rawUrl = toFetchUrl(input);
  let pathname = "/api";
  let search = "";
  try {
    const u = new URL(rawUrl, window.location.origin);
    pathname = u.pathname || "/api";
    search = u.search || "";
  } catch (_) {
    const s = typeof rawUrl === "string" ? rawUrl : "";
    const cut = s.indexOf("?");
    pathname = (cut >= 0 ? s.slice(0, cut) : s).split("#")[0] || "/api";
    search = cut >= 0 ? s.slice(cut) : "";
  }
  const fullPath = pathname + (search || "");
  if (init && typeof init.dashboardStatus === "string" && init.dashboardStatus.trim()) {
    const tabId = (init.dashboardTab && String(init.dashboardTab).trim()) || inferDashboardTab(pathname, search);
    return {
      label: init.dashboardStatus.trim(),
      detail: String(init.dashboardDetail || "").trim() || `HTTP ${String((init && init.method) || "GET").toUpperCase()} · LEco DevOps API`,
      path: fullPath,
      tabId,
      hostedSlug: String(init.dashboardHostedSlug || "").trim(),
    };
  }
  const methodFromReq = input instanceof Request ? input.method : "";
  const method = String((init && init.method) || methodFromReq || "GET").toUpperCase();
  const tabId = inferDashboardTab(pathname, search);
  const httpLine = `HTTP ${method} · LEco DevOps backend`;

  const hostedSub = pathname.match(
    /^\/api\/hosted-apps\/([^/]+)\/(snapshot|metrics\/history|insights|logs\/stream|logs|offboard)(?:\/.*)?$/
  );
  if (hostedSub) {
    const slug = hostedSub[1];
    const op = hostedSub[2];
    const opTitles = {
      snapshot: "Compose snapshot · ps / stats",
      "metrics/history": "Metrics history · CPU / RAM / net",
      insights: "Insights · capacity & gaps",
      logs: "Compose logs · tail",
      "logs/stream": "Compose logs · live stream",
      offboard: "Offboard · remove from ecosystem",
    };
    const opTitle = opTitles[op] || op;
    return {
      label: `Hosted app · ${prettyRegistrySlug(slug)} · ${opTitle}`,
      detail: `Registry id: ${slug} · Docker compose project containers · ${httpLine}`,
      path: fullPath,
      tabId: "hostedAppsTab",
      hostedSlug: normalizeHostedSidebarSlug(slug),
    };
  }
  if (pathname === "/api/hosted-apps" || pathname === "/api/hosted-apps/") {
    return {
      label: "Hosted apps · registry list",
      detail: `LEco DevOps registry · ${httpLine}`,
      path: fullPath,
      tabId: "hostedAppsTab",
      hostedSlug: "",
    };
  }

  if (pathname === "/api/logs") {
    const svc = new URLSearchParams(search).get("service") || "—";
    return {
      label: `Service logs · container “${svc}”`,
      detail: `Docker logs API · container name: ${svc} · ${httpLine}`,
      path: fullPath,
      tabId: "logsTab",
    };
  }

  if (pathname === "/api/overview") {
    return {
      label: "Overview · services, probes, reference",
      detail: "Docker API + Traefik HTTP probes + URL encyclopedia · single refresh",
      path: fullPath,
      tabId: "overviewTab",
    };
  }
  if (pathname === "/api/cloudflare-local") {
    return {
      label: "Cloudflare local · adapter status",
      detail: "R2 / KV / D1 / Workers / browser adapters on lh-network · HTTP",
      path: fullPath,
      tabId: "overviewTab",
    };
  }
  if (pathname === "/api/services") {
    return {
      label: "Infrastructure · managed service map",
      detail: "SERVICE_MAP + hub links · Docker container names",
      path: fullPath,
      tabId: "infrastructureTab",
    };
  }
  if (pathname === "/api/reference") {
    return {
      label: "Reference · URL encyclopedia probes",
      detail: "Static catalog + *.lh health checks · parallel probes",
      path: fullPath,
      tabId: "referenceTab",
    };
  }
  if (pathname === "/api/metrics/history") {
    const lim = new URLSearchParams(search).get("limit") || "";
    const where = tabId === "overviewTab" ? "Overview charts" : "Deep metrics tab";
    return {
      label: `Metrics history · ${where}${lim ? ` · last ${lim} points` : ""}`,
      detail: `Time-series snapshot · Docker + host /proc · ${httpLine}`,
      path: fullPath,
      tabId,
    };
  }
  if (pathname === "/api/host-metrics/injected") {
    return {
      label: "Host metrics · macOS temp file / LaunchAgent",
      detail: `Injected CPU temperature file status · ${httpLine}`,
      path: fullPath,
      tabId: "metricsTab",
    };
  }

  if (pathname === "/api/control/targets") {
    return {
      label: "Control · stack & service targets",
      detail: "Ecosystem-stack scripts + compose + LEco DevOps stacks · catalog",
      path: fullPath,
      tabId: "controlTab",
    };
  }
  if (pathname === "/api/control" || pathname.startsWith("/api/control/")) {
    return {
      label: "Control · docker / shell action",
      detail: "Subprocess: compose or ecosystem-stack script · streaming optional",
      path: fullPath,
      tabId: "controlTab",
    };
  }

  if (pathname === "/api/traefik/routes") {
    return {
      label: "Traefik · routers & services",
      detail: "dynamic.yml file provider · merge keys",
      path: fullPath,
      tabId: "routesTab",
    };
  }
  if (pathname.startsWith("/api/traefik/")) {
    return {
      label: "Traefik · route maintenance",
      detail: `Fragment merge / strip keys · ${httpLine}`,
      path: fullPath,
      tabId: "routesTab",
    };
  }

  if (pathname.startsWith("/api/docs/catalog")) {
    return {
      label: "Docs · module catalog",
      detail: "Markdown index · repo docs tree",
      path: fullPath,
      tabId: "docsTab",
    };
  }
  if (pathname.startsWith("/api/docs/content")) {
    return {
      label: "Docs · markdown body",
      detail: "Single doc render · marked + sanitize",
      path: fullPath,
      tabId: "docsTab",
    };
  }

  if (pathname.startsWith("/api/ollama/")) {
    const tail = pathname.replace(/^\/api\/ollama\/?/, "") || "api";
    return {
      label: `Ollama · ${tail.replace(/\//g, " · ")}`,
      detail: `Docker service: ollama · gateway http://ollama.lh · ${httpLine}`,
      path: fullPath,
      tabId: "infrastructureTab",
    };
  }

  if (pathname.startsWith("/api/airllm/")) {
    const tail = pathname.replace(/^\/api\/airllm\/?/, "") || "api";
    return {
      label: `AirLLM · ${tail.replace(/\//g, " · ")}`,
      detail: `Docker service: airllm · gateway http://airllm.lh · ${httpLine}`,
      path: fullPath,
      tabId: "infrastructureTab",
    };
  }

  if (pathname === "/api/update-catalog/schedule") {
    return {
      label: "Update catalog · save schedule",
      detail: `Writes update-catalog-schedule.json · ${httpLine}`,
      path: fullPath,
      tabId: "overviewTab",
    };
  }
  if (pathname === "/api/update-catalog/mark-read") {
    return {
      label: "Update catalog · mark all read",
      detail: `Acknowledges stack/model alerts · ${httpLine}`,
      path: fullPath,
      tabId: "overviewTab",
    };
  }
  if (pathname.startsWith("/api/ecosystem/") || pathname.startsWith("/api/llm-catalog/")) {
    return {
      label: "Update catalog · releases & tables",
      detail: `leco-update-catalog generated JSON · ${httpLine}`,
      path: fullPath,
      tabId: "overviewTab",
    };
  }

  if (pathname === "/api/ai-news" || pathname.startsWith("/api/ai-news/")) {
    return {
      label: "AI news · live RSS headlines",
      detail: `Filtered feeds · ${httpLine}`,
      path: fullPath,
      tabId: "developTab",
    };
  }

  if (pathname.startsWith("/api/ai/")) {
    const tail = pathname.replace(/^\/api\/ai\/?/, "") || "api";
    return {
      label: `AI settings · ${tail.replace(/\//g, " · ")}`,
      detail: `Onboarding provider config · ${httpLine}`,
      path: fullPath,
      tabId: "infrastructureTab",
    };
  }

  if (pathname.startsWith("/api/leco/")) {
    const tail = pathname.replace(/^\/api\/leco\/?/, "") || "api";
    const lecoTitles = {
      detect: "LEco DevOps · detect project (compose / wrangler)",
      "generate-yaml": "LEco DevOps · generate manifest + profile",
      "save-yaml": "LEco DevOps · save YAML to hosting layout",
      "validate-yaml": "LEco DevOps · validate YAML",
      "yaml-status": "LEco DevOps · YAML on-disk status",
      register: "LEco DevOps · ecosystem-register",
      "register/stream": "LEco DevOps · register (stream)",
      browse: "LEco DevOps · browse directories",
      "register-samples": "LEco DevOps · sample templates",
    };
    let key = tail.split("/")[0] || tail;
    if (tail.startsWith("register/stream")) key = "register/stream";
    const title = lecoTitles[key] || `LEco DevOps · ${key}`;
    return {
      label: title,
      detail: `CLI: leco-devops · materialize under hosting/app-available · ${httpLine}`,
      path: fullPath,
      tabId: "hostedAppsTab",
    };
  }

  if (pathname.startsWith("/api/hosted-apps/") && pathname.includes("/validate-configuration")) {
    return {
      label: "Hosted apps · validate configuration",
      detail: `Schema + on-disk paths · ${httpLine}`,
      path: fullPath,
      tabId: "hostedAppsTab",
    };
  }

  if (pathname.startsWith("/api/hosted/")) {
    return {
      label: "Hosted apps · upload / auxiliary API",
      detail: `Hosting layout under hosting/app-available · ${httpLine}`,
      path: fullPath,
      tabId: "hostedAppsTab",
    };
  }

  const area = backendAreaLabel(pathname);
  let label;
  if (method === "GET") label = `Loading ${area}…`;
  else if (method === "DELETE") label = `Removing ${area}…`;
  else if (method === "POST" || method === "PUT" || method === "PATCH") label = `Updating ${area}…`;
  else label = `Calling backend (${method})…`;
  return { label, detail: httpLine, path: fullPath, tabId };
}

function resolveFetchStatus(input, init) {
  return resolveFetchPreloaderMeta(input, init).label;
}

function instrumentBackendFetchPreloader() {
  if (globalPreloaderState.nativeFetch || typeof window.fetch !== "function") return;
  if (!globalPreloaderState.clickWired) {
    globalPreloaderState.clickWired = true;
    document.addEventListener(
      "click",
      (e) => {
        const t = e.target.closest(
          'button, [role="button"], .btn, a.btn, input[type="submit"], input[type="button"]',
        );
        if (!t || t.disabled || t.classList.contains("tab-btn")) return;
        if (t.id === "dashboardGlobalPreloaderToggle") return;
        noteActionTrigger(t);
      },
      true,
    );
  }
  const nativeFetch = window.fetch.bind(window);
  globalPreloaderState.nativeFetch = nativeFetch;
  const requestMethod = (input, init) => {
    const fromReq = input instanceof Request ? input.method : "";
    return String((init && init.method) || fromReq || "GET").toUpperCase();
  };
  const sharedGetKey = (input, init) => {
    if (requestMethod(input, init) !== "GET") return "";
    if (init && init.signal) return "";
    if (input instanceof Request && input.signal) return "";
    const raw = toFetchUrl(input);
    if (!isBackendApiUrl(raw)) return "";
    try {
      const u = new URL(raw, window.location.origin);
      return `GET:${u.pathname}${u.search}`;
    } catch (_) {
      return "";
    }
  };
  window.fetch = async (input, init) => {
    const rawUrl = toFetchUrl(input);
    const track = isBackendApiUrl(rawUrl);
    const dedupeKey = sharedGetKey(input, init || {});
    if (dedupeKey) {
      const shared = globalPreloaderState.sharedGetRequests.get(dedupeKey);
      if (shared) {
        const sharedRes = await shared;
        return sharedRes.clone();
      }
    }
    const initObj = init || {};
    const meta = resolveFetchPreloaderMeta(input, initObj);
    const triggerEl = track ? resolveFetchTriggerEl(initObj) : null;
    const inlineTok = triggerEl ? beginInlineAction(triggerEl) : null;
    if (track && !String(meta.hostedSlug || "").trim()) {
      try {
        const u = new URL(rawUrl, window.location.origin);
        const m = u.pathname.match(/^\/api\/hosted-apps\/([^/]+)\//);
        if (m) meta.hostedSlug = normalizeHostedSidebarSlug(m[1]);
      } catch (_) {
        /* ignore */
      }
    }
    const token = track ? beginGlobalPreloader(meta) : null;
    const run = (async () => {
      try {
        return await nativeFetch(input, init);
      } finally {
        if (token) endGlobalPreloader(token);
        if (inlineTok) endInlineAction(inlineTok);
      }
    })();
    if (dedupeKey) {
      globalPreloaderState.sharedGetRequests.set(dedupeKey, run);
      try {
        const sharedRes = await run;
        return sharedRes.clone();
      } finally {
        if (globalPreloaderState.sharedGetRequests.get(dedupeKey) === run) {
          globalPreloaderState.sharedGetRequests.delete(dedupeKey);
        }
      }
    }
    return await run;
  };
}

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
  const verEl = document.getElementById("footerPlatformVersion");
  if (verEl && boot.platform_version) {
    verEl.textContent = `v${boot.platform_version}`;
  }
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
    const now = Date.now();
    const horizon = now - METRICS_LOCAL_RETENTION_MS;
    const prev = hydrateMetricsFromCache();
    const mergedByTs = new Map();
    const pushPoint = (p) => {
      if (!p || typeof p !== "object") return;
      const tsRaw = typeof p.ts === "string" ? p.ts : "";
      if (!tsRaw) return;
      const tsMs = Date.parse(tsRaw);
      if (!Number.isFinite(tsMs) || tsMs < horizon || tsMs > now + 1000 * 60 * 30) return;
      const docker = p.docker && typeof p.docker === "object" ? p.docker : {};
      const system = p.system && typeof p.system === "object" ? p.system : {};
      mergedByTs.set(tsRaw, {
        ts: tsRaw,
        docker: {
          cpu_percent: docker.cpu_percent ?? null,
          memory_percent: docker.memory_percent ?? null,
          memory_percent_of_host: docker.memory_percent_of_host ?? null,
          memory_percent_of_limits: docker.memory_percent_of_limits ?? null,
          memory_usage: docker.memory_usage ?? null,
          memory_limit_sum: docker.memory_limit_sum ?? null,
          net_total_mbps: docker.net_total_mbps ?? null,
          iops_total_est: docker.iops_total_est ?? null,
          blk_total_mbps: docker.blk_total_mbps ?? null,
        },
        system: {
          cpu_percent: system.cpu_percent ?? null,
          cpu_temp_c_max: system.cpu_temp_c_max ?? null,
          net_total_mbps: system.net_total_mbps ?? null,
          iops_total_est: system.iops_total_est ?? null,
          memory_percent_available: system.memory_percent_available ?? null,
          memory_percent: system.memory_percent ?? null,
          host_memory_total_bytes_effective: system.host_memory_total_bytes_effective ?? null,
        },
      });
    };
    (prev?.points || []).forEach(pushPoint);
    (metricsData.points || []).forEach(pushPoint);
    let points = Array.from(mergedByTs.values()).sort((a, b) => Date.parse(a.ts) - Date.parse(b.ts));
    if (points.length > METRICS_LOCAL_MAX_POINTS) {
      points = points.slice(points.length - METRICS_LOCAL_MAX_POINTS);
    }
    let payload = {
      schemaVersion: CACHE_SCHEMA_VERSION,
      savedAt: now,
      metrics: {
        points,
        generated_at: metricsData.generated_at || null,
        max_points: points.length,
        notes: "local rolling cache (24h)",
      },
    };
    let s = JSON.stringify(payload);
    while (s.length > METRICS_CACHE_MAX_BYTES && points.length > 1200) {
      points = points.filter((_, idx) => idx % 2 === 0 || idx === points.length - 1);
      payload = {
        schemaVersion: CACHE_SCHEMA_VERSION,
        savedAt: now,
        metrics: {
          points,
          generated_at: metricsData.generated_at || null,
          max_points: points.length,
          notes: "local rolling cache (24h, compacted)",
        },
      };
      s = JSON.stringify(payload);
    }
    if (s.length > METRICS_CACHE_MAX_BYTES) return;
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
      const d = ["remove", "reset", "staging"];
      const danger = (actions || []).filter((a) => d.includes(a));
      const safe = (actions || []).filter((a) => !d.includes(a));
      return { safe, danger };
    },
    actionButtonClasses(action) {
      const a = (action || "").toLowerCase();
      const base = "ctrl-act";
      if (a === "remove" || a === "reset") return `${base} danger ctrl-act--destructive`;
      if (a === "staging") return `${base} danger ctrl-act--caution`;
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
  if (c?.skipped) {
    return badge(
      null,
      "n/a",
      c.skip_reason || "Service stopped — HTTP probe skipped (not a failure)",
    );
  }
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

const DASHBOARD_APP_HOSTNAMES = new Set([
  "localhost",
  "127.0.0.1",
  "::1",
  "localhost.lh",
  "dashboard.lh",
]);

function isDashboardLocalHostname(hostname) {
  return DASHBOARD_APP_HOSTNAMES.has(String(hostname || "").toLowerCase());
}

/** Separate HTML pages (not the main tab SPA at /). */
function isDashboardSubAppPath(pathname) {
  const p = String(pathname || "");
  return p === "/help" || p.startsWith("/help/") || p === "/hub" || p.startsWith("/hub/");
}

/** Main dashboard SPA: / with ?tab= / ?doc= / ?app= and optional #infra-* hash. */
function isDashboardSpaUrl(href) {
  if (!href || href.startsWith("#")) return false;
  try {
    const u = new URL(href, window.location.href);
    if (u.protocol !== "http:" && u.protocol !== "https:") return false;
    const path = u.pathname.replace(/\/+$/, "") || "/";
    if (path !== "/") return false;
    if (u.origin === window.location.origin) return true;
    return isDashboardLocalHostname(u.hostname);
  } catch {
    return false;
  }
}

/** True when the link should open in a new browser tab. */
function shouldOpenLinkInNewTab(href) {
  if (!href || href.startsWith("#")) return false;
  try {
    const u = new URL(href, window.location.href);
    if (u.protocol !== "http:" && u.protocol !== "https:") return false;
    if (isDashboardSpaUrl(href)) return false;
    if (u.origin === window.location.origin && isDashboardSubAppPath(u.pathname)) {
      // /hub and /help are same-origin dashboard sub-apps — never open in a new tab.
      return false;
    }
    if (!isDashboardLocalHostname(u.hostname)) return true;
    return false;
  } catch {
    return false;
  }
}

/**
 * Use on dashboard-generated <a href>: new tab for other *.lh service UIs and external sites;
 * same tab for SPA (/?tab=…), /hub, and /help on the dashboard origin.
 */
function externalNavigationAttrs(href) {
  return shouldOpenLinkInNewTab(href) ? ' target="_blank" rel="noopener noreferrer"' : "";
}

function applyExternalLinkAttrs(root = document) {
  const scope = root?.querySelectorAll ? root : document;
  scope.querySelectorAll("a[href]").forEach((a) => {
    const href = a.getAttribute("href");
    if (!href || a.target === "_blank") return;
    if (shouldOpenLinkInNewTab(href)) {
      a.setAttribute("target", "_blank");
      a.setAttribute("rel", "noopener noreferrer");
    }
  });
}

function parseDashboardLocation(loc = window.location) {
  const qs = new URLSearchParams(loc.search || "");
  return {
    tab: String(qs.get("tab") || "overviewTab").trim(),
    doc: String(qs.get("doc") || "").trim(),
    app: String(qs.get("app") || "").trim(),
    hash: loc.hash || "",
  };
}

function buildDashboardLocation(tabId, opts = {}) {
  const params = new URLSearchParams();
  if (tabId) params.set("tab", tabId);
  const docId =
    opts.docId !== undefined
      ? opts.docId
      : tabId === "docsTab"
        ? window.__docCurrentId || ""
        : "";
  const appSlug =
    opts.appSlug !== undefined
      ? opts.appSlug
      : tabId === "hostedAppsTab"
        ? hostedSelectedSlug || ""
        : "";
  if (docId && tabId === "docsTab") params.set("doc", docId);
  if (appSlug && tabId === "hostedAppsTab") params.set("app", appSlug);
  const search = params.toString();
  let url = "/" + (search ? `?${search}` : "");
  const hash = opts.hash ?? "";
  if (hash) url += hash.startsWith("#") ? hash : `#${hash}`;
  return url;
}

function syncDashboardUrl(tabId, opts = {}) {
  if (!document.getElementById("overviewTab") || opts.skipUrl) return;
  const url = buildDashboardLocation(tabId, {
    docId: opts.docId,
    appSlug: opts.appSlug,
    hash:
      opts.hash !== undefined
        ? opts.hash
        : tabId === "infrastructureTab"
          ? window.location.hash
          : "",
  });
  const current = window.location.pathname + window.location.search + window.location.hash;
  if (current === url) return;
  const method = opts.replace ? "replaceState" : "pushState";
  window.history[method]({ dashboardTab: tabId }, "", url);
}

function handleDashboardLinkClick(e) {
  if (!document.getElementById("overviewTab")) return;
  const a = e.target.closest("a[href]");
  if (!a || a.target === "_blank") return;
  if (e.defaultPrevented) return;
  if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  const href = a.getAttribute("href");
  if (!href || href.startsWith("javascript:")) return;
  if (href.startsWith("#")) return;

  if (shouldOpenLinkInNewTab(href)) {
    if (!a.target) {
      e.preventDefault();
      try {
        window.open(new URL(href, window.location.href).href, "_blank", "noopener,noreferrer");
      } catch {
        /* ignore */
      }
    }
    return;
  }

  let u;
  try {
    u = new URL(href, window.location.href);
  } catch {
    return;
  }
  if (!isDashboardSpaUrl(u)) return;

  e.preventDefault();
  const tab = String(u.searchParams.get("tab") || "overviewTab").trim();
  const doc = String(u.searchParams.get("doc") || "").trim();
  const app = String(u.searchParams.get("app") || "").trim();
  const hash = u.hash || "";
  if (!document.getElementById(tab)) return;
  activateTab(tab, {
    preferredDocId: doc || null,
    hostedSlug: app || null,
    hash,
  });
  if (hash && tab === "infrastructureTab") {
    requestAnimationFrame(() => scrollInfraHashAnchor(hash));
  }
}

function initDashboardUrlRouting() {
  if (!document.getElementById("overviewTab")) return;
  document.body.addEventListener("click", handleDashboardLinkClick, true);
  window.addEventListener("popstate", () => {
    const { tab, doc, app, hash } = parseDashboardLocation();
    if (!document.getElementById(tab)) return;
    activateTab(tab, {
      preferredDocId: doc || null,
      hostedSlug: app || null,
      hash,
      skipUrl: true,
    });
    if (hash && tab === "infrastructureTab") {
      requestAnimationFrame(() => scrollInfraHashAnchor(hash));
    }
  });
}

function hostedMainUrlProbeSummary(app) {
  const probe = app?.main_url_probe;
  if (!probe || probe.checked !== true || probe.ok !== false) return "";
  const code = Number(probe.status_code);
  if (Number.isFinite(code)) return `main URL ${code}`;
  return "main URL unreachable";
}

function hostedRuntimeBadge(runtime, app) {
  const status = String(runtime?.status || "").toLowerCase();
  if (status === "running") {
    const probeLabel = hostedMainUrlProbeSummary(app);
    if (probeLabel) return { cls: "overview-hosted-urls__dot--yellow", label: probeLabel };
    return { cls: "overview-hosted-urls__dot--green", label: "running" };
  }
  if (status === "partial") return { cls: "overview-hosted-urls__dot--yellow", label: "partial" };
  if (status === "stopped" || status === "compose") return { cls: "overview-hosted-urls__dot--red", label: "down" };
  return { cls: "overview-hosted-urls__dot--gray", label: status || "unknown" };
}

function hostedOverviewPrimaryUrl(app) {
  const mainUrls = app?.main_urls || {};
  const preferred =
    (typeof mainUrls.https === "string" && mainUrls.https.trim()) ||
    (typeof mainUrls.http === "string" && mainUrls.http.trim()) ||
    (typeof app?.main_url === "string" && app.main_url.trim()) ||
    "";
  if (preferred) return preferred;
  const firstPublic = (app?.localhost_urls || []).find((u) => {
    const candidate = (u && (u.public_url || u.publicUrl)) || "";
    return typeof candidate === "string" && candidate.trim();
  });
  return firstPublic ? String(firstPublic.public_url || firstPublic.publicUrl || "").trim() : "";
}

function renderOverviewHostedAppsCard(payload) {
  const el = document.getElementById("overviewHostedUrls");
  if (!el) return;
  const apps = Array.isArray(payload?.apps) ? payload.apps : [];
  if (!apps.length) {
    el.innerHTML =
      '<p class="muted small overview-hosted-urls__empty">No hosted apps yet. Register via <code>leco-devops ecosystem-register</code> or add <code>hosting/app-available/&lt;dir&gt;/leco.app.yaml</code> and refresh.</p>';
    return;
  }
  const rows = [...apps]
    .sort((a, b) => String(a?.label || a?.id || "").localeCompare(String(b?.label || b?.id || "")))
    .map((app) => {
      const dot = hostedRuntimeBadge(app?.runtime || {}, app);
      const labelText = String(app?.label || app?.id || "App");
      const stagingBadge =
        app?.pending_registration === true
          ? ' <span class="hosted-app-sidebar-badge" title="Staging — not in registry">Staging</span>'
          : "";
      const url = hostedOverviewPrimaryUrl(app);
      const urlHtml = url
        ? `<a class="overview-hosted-urls__link" href="${escapeAttr(url)}"${externalNavigationAttrs(url)}>${escapeHtml(url)}</a>`
        : '<span class="muted small">No main URL in manifest</span>';
      const runtimeLabel = escapeHtml(String(app?.runtime?.label || dot.label));
      return `<li class="overview-hosted-urls__item">
        <div class="overview-hosted-urls__row">
          <span class="overview-hosted-urls__dot ${dot.cls}" aria-hidden="true"></span>
          <span class="overview-hosted-urls__app">${escapeHtml(labelText)}${stagingBadge}</span>
        </div>
        <p class="overview-hosted-urls__meta small muted">${urlHtml}<span class="header-sep">·</span>${runtimeLabel}</p>
      </li>`;
    });
  el.innerHTML = `<ul class="overview-hosted-urls__list">${rows.join("")}</ul>`;
}

async function loadOverviewHostedApps(opts = {}) {
  const force = opts?.force === true;
  const fresh = Date.now() - overviewHostedAppsCachedAtMs < OVERVIEW_HOSTED_APPS_CACHE_MS;
  if (!force && fresh && Array.isArray(overviewHostedAppsPayload?.apps)) return overviewHostedAppsPayload;
  if (overviewHostedAppsInFlight) return overviewHostedAppsInFlight;
  overviewHostedAppsInFlight = (async () => {
    const res = await fetch("/api/hosted-apps");
    if (!res.ok) throw new Error(`hosted apps request failed (${res.status})`);
    const data = await res.json();
    overviewHostedAppsPayload = data && typeof data === "object" ? data : { apps: [] };
    overviewHostedAppsCachedAtMs = Date.now();
    return overviewHostedAppsPayload;
  })();
  try {
    return await overviewHostedAppsInFlight;
  } finally {
    overviewHostedAppsInFlight = null;
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
    mount.innerHTML = "<div class='card'>URL directory unavailable (redeploy LEco DevOps or check API).</div>";
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
  window.__docCurrentId = id;
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
    if (activeTab === "docsTab" && !opts.skipUrl) {
      syncDashboardUrl("docsTab", { docId: id, replace: !!opts.replace });
    }
  } catch (e) {
    content.innerHTML = `<p class='muted'>${escapeHtml(String(e))}</p>`;
  }
}

let aiNewsCategoriesLoaded = false;

async function loadAiNewsPanel(refresh = false) {
  const list = document.getElementById("aiNewsList");
  const meta = document.getElementById("aiNewsMeta");
  if (!list) return;
  const cat = document.getElementById("aiNewsCategory")?.value || "";
  const tags = document.getElementById("aiNewsTags")?.value || "";
  const q = document.getElementById("aiNewsSearch")?.value || "";
  const params = new URLSearchParams();
  if (refresh) params.set("refresh", "1");
  if (cat) params.set("category", cat);
  if (tags) params.set("tags", tags);
  if (q) params.set("q", q);
  list.innerHTML = `<li class="muted small">Loading AI news…</li>`;
  try {
    const res = await fetch(`/api/ai-news?${params.toString()}`, {
      dashboardStatus: refresh ? "AI news · refresh feeds" : "AI news · load headlines",
      dashboardTab: "developTab",
    });
    const data = await res.json();
    if (!aiNewsCategoriesLoaded) {
      const sel = document.getElementById("aiNewsCategory");
      if (sel && data.categories) {
        for (const c of data.categories) {
          const o = document.createElement("option");
          o.value = c;
          o.textContent = c;
          sel.appendChild(o);
        }
        aiNewsCategoriesLoaded = true;
      }
    }
    const items = data.filtered_items || data.items || [];
    if (meta) {
      meta.textContent = `${items.length} shown · ${data.item_count || 0} total · ${data.feeds_configured || 0} feeds · updated ${data.generated_at || "—"}`;
    }
    if (!items.length) {
      list.innerHTML = `<li class="muted small">No headlines match filters.</li>`;
      return;
    }
    list.innerHTML = items
      .map((it) => {
        const tagsHtml = (it.tags || [])
          .slice(0, 6)
          .map((t) => `<span class="ai-news-tag">${escapeHtml(t)}</span>`)
          .join("");
        const when = it.published_at ? escapeHtml(formatCatalogWhen(it.published_at)) : "";
        return `<li class="ai-news-item">
          <a class="ai-news-item__title" href="${escapeAttr(it.url || "#")}" target="_blank" rel="noopener noreferrer">${escapeHtml(it.title || "")}</a>
          <span class="ai-news-item__meta muted small">${escapeHtml(it.source_title || "")}${when ? ` · ${when}` : ""} · ${escapeHtml(it.category || "")}</span>
          ${it.summary ? `<p class="ai-news-item__summary muted small">${escapeHtml(it.summary)}</p>` : ""}
          <div class="ai-news-item__tags">${tagsHtml}</div>
        </li>`;
      })
      .join("");
  } catch (e) {
    list.innerHTML = `<li class="muted small">${escapeHtml(String(e))}</li>`;
  }
}

function initAiNewsPanel() {
  if (document.getElementById("aiNewsPanel")?.dataset.wired === "1") return;
  const panel = document.getElementById("aiNewsPanel");
  if (!panel) return;
  panel.dataset.wired = "1";
  document.getElementById("aiNewsRefresh")?.addEventListener("click", () => void loadAiNewsPanel(true));
  document.getElementById("aiNewsApply")?.addEventListener("click", () => void loadAiNewsPanel(false));
  document.getElementById("aiNewsSearch")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") void loadAiNewsPanel(false);
  });
  document.getElementById("aiNewsRefine")?.addEventListener("click", async () => {
    const interest = document.getElementById("aiNewsInterest")?.value?.trim() || "";
    const hint = document.getElementById("aiNewsRefineHint");
    if (!interest) {
      if (hint) hint.textContent = "Enter an interest description first.";
      return;
    }
    if (hint) hint.textContent = "Asking local LLM for filter suggestions…";
    try {
      const res = await fetch("/api/ai-news/refine", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: interest }),
        dashboardStatus: "AI news · LLM filter suggestions",
        dashboardTab: "developTab",
      });
      const data = await res.json();
      const cats = (data.categories || []).join(", ");
      const tags = (data.tags || []).join(", ");
      const kw = (data.keywords || []).join(" ");
      if (data.categories?.[0]) document.getElementById("aiNewsCategory").value = data.categories[0];
      if (tags) document.getElementById("aiNewsTags").value = tags;
      if (kw) document.getElementById("aiNewsSearch").value = kw;
      if (hint) {
        hint.textContent = data.fallback
          ? `Heuristic filters (LLM unavailable): categories [${cats}] tags [${tags}]`
          : `LLM suggested: categories [${cats}] tags [${tags}] keywords [${kw}]`;
      }
      void loadAiNewsPanel(false);
    } catch (e) {
      if (hint) hint.textContent = String(e);
    }
  });
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
        <li><code>./ecosystem-stack/ecosystem-stack.sh restart dashboard</code> — after editing <code>dashboard/</code></li>
        <li><code>./ecosystem-stack/services/cloudflare-local.sh recreate workers-runtime</code> — after editing Workers</li>
        <li><code>./ecosystem-stack/ecosystem-stack.sh repair-network</code> — if a container lost <code>lh-network</code></li>
      </ul>
    </div>
    <div class="card dev-card">
      <strong>New <code>*.lh</code> route</strong>
      <ul>
        <li>Add router + service in <code>hosting/traefik/dynamic.yml</code></li>
        <li>Put the container on <code>lh-network</code></li>
        <li>Optional: extend <code>monitor.py</code> <code>SERVICE_MAP</code> for probes</li>
      </ul>
    </div>
    <div class="card dev-card">
      <strong>LEco DevOps APIs</strong>
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

/** Chart counts from enumerated containers (matches tooltips), not Docker daemon globals. */
function countEngineContainersForChart(containers, dockerCounts) {
  const names = bucketEngineContainerNames(containers);
  const running = names.running.length;
  const paused = names.paused.length;
  const stopped = names.stopped.length;
  const daemonStopped = Number(dockerCounts?.containers_stopped || 0);
  const ghostStopped = Math.max(0, daemonStopped - stopped);
  return { running, paused, stopped, names, ghostStopped };
}

function destroyOverviewChartsIfStale() {
  const c = overviewCharts.cpu;
  const cfStale =
    overviewCharts.cf && overviewCharts.cf.data?.labels?.length !== CF_LOCAL_OVERVIEW_TOTAL;
  if (!c && !cfStale) return;
  if (c && c.data.datasets.length >= 3 && !cfStale) return;
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
        labels: CF_LOCAL_OVERVIEW_SERVICES.map((s) => s.label),
        datasets: [
          {
            label: "Reachable",
            data: CF_LOCAL_OVERVIEW_SERVICES.map(() => 0),
            backgroundColor: CF_LOCAL_OVERVIEW_SERVICES.map((s) => s.color),
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
                const lines = [];
                if (names.length === 0) {
                  lines.push("No containers in this state (listed)");
                } else {
                  lines.push(...names.slice(0, max).map((n) => `• ${n}`));
                  if (names.length > max) {
                    lines.push(`• … +${names.length - max} more`);
                  }
                }
                if (key === "stopped" && chart._engineGhostStopped > 0) {
                  lines.push(
                    `• Docker reports ${chart._engineGhostStopped} extra stopped (removed/unlisted)`,
                  );
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

function formatCatalogWhen(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  } catch {
    return iso;
  }
}

function renderOverviewUpdateCatalog(uc) {
  const el = document.getElementById("overviewUpdateCatalog");
  if (!el) return;
  if (!uc || uc.ok === false) {
    el.innerHTML = `<p class="muted small">${escapeHtml(uc?.error || "Update catalog unavailable")}. Run <code>./ecosystem-stack/services/update-catalog.sh run-once</code></p>`;
    return;
  }
  const watcher = uc.watcher || {};
  const sched = uc.schedule || {};
  const upd = uc.updates || {};
  const cat = uc.catalog || {};
  const links = uc.help_links || {};
  const ui = watcher.ui_status || "unknown";
  const statusClass =
    ui === "running" ? "uc-status--ok" : ui === "not_deployed" ? "uc-status--muted" : "uc-status--warn";
  const schedCfg = sched.config || {};
  const stackUnread = upd.stack_unread_count || 0;
  const stackTotal = upd.stack_updates_available || 0;
  const modelUnread = upd.model_unread_count || 0;
  const modelTotal = upd.model_alerts_count || 0;
  const hasUnread = upd.has_unread || stackUnread > 0 || modelUnread > 0;
  const schedMode = schedCfg.mode || sched.mode || "interval";
  const fixedTimes = (schedCfg.fixed_times_utc || sched.fixed_times_utc || ["06:00", "18:00"]).join(", ");
  const intervalH = schedCfg.interval_hours ?? sched.interval_hours ?? 6;
  const nextLabel =
    sched.next_check_in_minutes > 0
      ? `in ~${sched.next_check_in_minutes} min`
      : sched.overdue
        ? "due now"
        : "—";

  let stackHtml = "";
  (upd.stack_pending || []).slice(0, 4).forEach((s) => {
    const latest = (s.latest || {}).full || (s.latest || {}).tag || "—";
    const run = s.running_image || "not running";
    const unreadCls = s.unread ? " uc-list__item--unread" : "";
    stackHtml += `<li class="uc-list__item${unreadCls}">
      <strong>${escapeHtml(s.label || s.id)}</strong>
      <span class="uc-list__meta">running: <code>${escapeHtml(run)}</code> · latest: <code>${escapeHtml(latest)}</code></span>
    </li>`;
  });

  let modelsHtml = "";
  (upd.model_alerts || []).slice(0, 5).forEach((m) => {
    const unreadCls = m.unread ? " uc-list__item--unread" : "";
    modelsHtml += `<li class="uc-list__item${unreadCls}">
      <code>${escapeHtml(m.name || "")}</code>
      <span class="uc-list__meta">${escapeHtml(m.install || "")}</span>
    </li>`;
  });

  const stackStatN = stackUnread || stackTotal;
  const modelStatN = modelUnread || modelTotal;
  const stackStatSub =
    stackUnread && stackTotal > stackUnread ? `<span class="uc-stat__sub">${stackTotal} total</span>` : "";
  const modelStatSub =
    modelUnread && modelTotal > modelUnread ? `<span class="uc-stat__sub">${modelTotal} total</span>` : "";
  const needToken = dashboardTokenRequired() && !controlToken();
  const tokenBlock = needToken
    ? `<div class="uc-token-hint" id="ucTokenHint">
        <label class="uc-schedule-form__row">
          <span class="muted small">Control token</span>
          <input id="ucControlToken" type="password" class="uc-input" placeholder="DASHBOARD_CONTROL_TOKEN" autocomplete="off" />
        </label>
        <p class="muted small uc-token-hint__msg" id="ucTokenMsg">Enter your control token to save the schedule or mark updates read. You can also save it on the <a href="/?tab=controlTab">Control</a> tab.</p>
      </div>`
    : "";

  el.innerHTML = `
    <div class="uc-grid">
      <div class="uc-block">
        <div class="uc-row">
          <span class="uc-label">Watcher</span>
          <span class="uc-status ${statusClass}">${escapeHtml(ui)}</span>
        </div>
        ${
          ui === "not_deployed"
            ? `<p class="muted small uc-hint">Start: <code>${escapeHtml(watcher.start_cmd || "")}</code></p>`
            : `<p class="muted small uc-hint">Container <code>${escapeHtml(watcher.container || "leco-update-catalog")}</code>${watcher.started_at ? ` · since ${escapeHtml(formatCatalogWhen(watcher.started_at))}` : ""}</p>`
        }
      </div>
      <div class="uc-block uc-block--schedule">
        <div class="uc-row"><span class="uc-label">Schedule</span><strong>${escapeHtml(sched.interval_label || "")}</strong></div>
        <p class="muted small uc-hint">Last: ${escapeHtml(formatCatalogWhen(sched.last_check_at))} · Next: ${escapeHtml(formatCatalogWhen(sched.next_check_at))} (${escapeHtml(nextLabel)})</p>
        <div class="uc-schedule-form">
          <label class="uc-schedule-form__row">
            <span class="muted small">Mode</span>
            <select id="ucScheduleMode" class="uc-input">
              <option value="interval"${schedMode === "interval" ? " selected" : ""}>Every N hours</option>
              <option value="fixed"${schedMode === "fixed" ? " selected" : ""}>Fixed UTC times</option>
            </select>
          </label>
          <label class="uc-schedule-form__row uc-schedule-form__row--interval">
            <span class="muted small">Hours</span>
            <input id="ucIntervalHours" type="number" min="1" max="168" step="1" class="uc-input" value="${escapeHtml(String(intervalH))}" />
          </label>
          <label class="uc-schedule-form__row uc-schedule-form__row--fixed">
            <span class="muted small">UTC times</span>
            <input id="ucFixedTimes" type="text" class="uc-input" placeholder="06:00, 18:00" value="${escapeHtml(fixedTimes)}" />
          </label>
          <button type="button" class="btn btn--sm btn--ghost" id="ucScheduleSave">Save schedule</button>
        </div>
      </div>
      <div class="uc-block uc-block--summary">
        <div class="uc-stat${stackUnread ? " uc-stat--alert" : ""}"><span class="uc-stat__n">${stackStatN}</span><span class="uc-stat__l">stack update(s)</span>${stackStatSub}</div>
        <div class="uc-stat${modelUnread ? " uc-stat--alert" : ""}"><span class="uc-stat__n">${modelStatN}</span><span class="uc-stat__l">new Ollama</span>${modelStatSub}</div>
        <div class="uc-stat"><span class="uc-stat__n">${cat.ollama_model_count ?? "—"}</span><span class="uc-stat__l">Ollama catalog</span></div>
        <div class="uc-stat"><span class="uc-stat__n">${cat.airllm_model_count ?? "—"}</span><span class="uc-stat__l">AirLLM catalog</span></div>
      </div>
    </div>
    ${tokenBlock}
    ${
      hasUnread
        ? `<div class="uc-actions"><button type="button" class="btn btn--sm" id="ucMarkAllRead">Mark all read</button></div>`
        : ""
    }
    ${
      stackHtml
        ? `<div class="uc-section"><div class="uc-section__title">Stack updates</div><ul class="uc-list">${stackHtml}</ul></div>`
        : ""
    }
    ${
      modelsHtml
        ? `<div class="uc-section"><div class="uc-section__title">New Ollama models</div><ul class="uc-list">${modelsHtml}</ul></div>`
        : !stackHtml && !modelTotal
          ? `<p class="muted small uc-ok">No pending stack or model alerts.</p>`
          : ""
    }
    <p class="uc-foot muted small">
      <a href="${escapeHtml(links.updates || "/help?topic=ecosystem-updates")}">All updates</a>
      · <a href="${escapeHtml(links.ollama_catalog || "/help?topic=llm-catalog-ollama")}">Ollama table</a>
      · <a href="${escapeHtml(links.airllm_catalog || "/help?topic=llm-catalog-airllm")}">AirLLM table</a>
    </p>`;

  syncUcScheduleFormVisibility();
  document.getElementById("ucScheduleMode")?.addEventListener("change", syncUcScheduleFormVisibility);
  document.getElementById("ucScheduleSave")?.addEventListener("click", () => void saveUpdateCatalogSchedule());
  document.getElementById("ucMarkAllRead")?.addEventListener("click", () => void markUpdateCatalogRead());
}

function syncUcScheduleFormVisibility() {
  const mode = document.getElementById("ucScheduleMode")?.value || "interval";
  document.querySelectorAll(".uc-schedule-form__row--interval").forEach((n) => {
    n.style.display = mode === "interval" ? "" : "none";
  });
  document.querySelectorAll(".uc-schedule-form__row--fixed").forEach((n) => {
    n.style.display = mode === "fixed" ? "" : "none";
  });
}

async function saveUpdateCatalogSchedule() {
  const tok = resolveControlToken();
  if (tok === null) {
    emphasizeUpdateCatalogTokenHint("Enter your control token above, then click Save schedule again.");
    return;
  }
  const mode = document.getElementById("ucScheduleMode")?.value || "interval";
  const interval = parseFloat(document.getElementById("ucIntervalHours")?.value || "6");
  const timesRaw = document.getElementById("ucFixedTimes")?.value || "";
  const fixed_times_utc = timesRaw
    .split(/[,;\s]+/)
    .map((x) => x.trim())
    .filter(Boolean);
  const btn = document.getElementById("ucScheduleSave");
  try {
    const res = await fetch("/api/update-catalog/schedule", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Control-Token": tok },
      body: JSON.stringify({ mode, interval_hours: interval, fixed_times_utc, token: tok }),
      dashboardTriggerEl: btn,
    });
    const data = await res.json();
    if (res.status === 401) {
      try {
        localStorage.removeItem("dashboard_control_token");
      } catch (_) {}
      emphasizeUpdateCatalogTokenHint("Token rejected. Check DASHBOARD_CONTROL_TOKEN and try again.");
      return;
    }
    if (!res.ok || !data.ok) {
      await showAppAlert(data.error || "Failed to save schedule", "Update catalog");
      return;
    }
    await loadOverview();
  } catch (e) {
    await showAppAlert(String(e), "Update catalog");
  }
}

async function markUpdateCatalogRead() {
  const tok = resolveControlToken();
  if (tok === null) {
    emphasizeUpdateCatalogTokenHint("Enter your control token above, then click Mark all read again.");
    return;
  }
  const btn = document.getElementById("ucMarkAllRead");
  try {
    const res = await fetch("/api/update-catalog/mark-read", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Control-Token": tok },
      body: JSON.stringify({ token: tok }),
      dashboardTriggerEl: btn,
    });
    const data = await res.json();
    if (res.status === 401) {
      try {
        localStorage.removeItem("dashboard_control_token");
      } catch (_) {}
      emphasizeUpdateCatalogTokenHint("Token rejected. Check DASHBOARD_CONTROL_TOKEN and try again.");
      return;
    }
    if (!res.ok || !data.ok) {
      await showAppAlert(data.error || "Failed to mark as read", "Update catalog");
      return;
    }
    await loadOverview();
  } catch (e) {
    await showAppAlert(String(e), "Update catalog");
  }
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
  const cfUp = countCfLocalReachable(cf);
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
  const uc = data.update_catalog || {};
  const ucUpd = uc.updates || {};
  const stackUp = ucUpd.stack_unread_count || ucUpd.stack_updates_available || 0;
  const modelUp = ucUpd.model_unread_count || ucUpd.model_alerts_count || 0;
  const ucWatcher = (uc.watcher || {}).ui_status || "—";

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
      <div class="kpi-value">${cfUp}/${CF_LOCAL_OVERVIEW_TOTAL}</div>
      <div class="kpi-label">CF local</div>
    </div>
    <div class="kpi-cell">
      <div class="kpi-value">${d.containers_running ?? "—"}</div>
      <div class="kpi-label">containers · ${escapeHtml(host.server_version || "Docker")}</div>
    </div>
    <div class="kpi-cell kpi-cell--soft">
      <div class="kpi-value">${ref.healthy_urls ?? "—"}/${ref.total_urls ?? "—"}</div>
      <div class="kpi-label">Directory URLs</div>
    </div>
    <div class="kpi-cell${stackUp || modelUp ? " kpi-cell--alert" : ""}">
      <div class="kpi-value">${stackUp + modelUp}</div>
      <div class="kpi-label">Updates <span class="kpi-sublabel">catalog · ${escapeHtml(ucWatcher)}</span></div>
    </div>`;
}

function renderOverviewChips(data, cf) {
  const el = document.getElementById("overviewChips");
  if (!el) return;
  const s = data.system_status || {};
  const alerts = (s.alerts || []).length;
  const svc = cf?.services || {};
  const cfUp = countCfLocalReachable(cf);
  const parts = [
    `<span class="chip">Reference — all <code>*.lh</code> links</span>`,
    `<span class="chip">Infrastructure — engine &amp; tables</span>`,
    `<span class="chip">Metrics — net &amp; IOPS</span>`,
    `<span class="chip">Control — lifecycle</span>`,
  ];
  if (alerts > 0) {
    parts.unshift(`<span class="chip chip--warn">${alerts} alert(s) — see Infrastructure</span>`);
  }
  if (cfUp < CF_LOCAL_OVERVIEW_TOTAL) {
    parts.unshift(`<span class="chip chip--bad">CF local ${cfUp}/${CF_LOCAL_OVERVIEW_TOTAL}</span>`);
  }
  const uc = data.update_catalog || {};
  const ucUpd = uc.updates || {};
  const stackChip = ucUpd.stack_unread_count || ucUpd.stack_updates_available || 0;
  const modelChip = ucUpd.model_unread_count || ucUpd.model_alerts_count || 0;
  if (stackChip > 0 || modelChip > 0) {
    const unreadOnly = ucUpd.has_unread;
    parts.unshift(
      `<span class="chip${unreadOnly ? " chip--warn" : ""}">${stackChip} stack · ${modelChip} Ollama${unreadOnly ? " new" : ""} — <a href="/help?topic=ecosystem-updates">updates</a></span>`,
    );
  }
  if ((uc.watcher || {}).ui_status === "not_deployed") {
    parts.unshift(`<span class="chip">Update catalog — <code>update-catalog.sh start</code></span>`);
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
  renderOverviewUpdateCatalog(data.update_catalog);

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
  const upVals = CF_LOCAL_OVERVIEW_SERVICES.map((entry) => (cfServiceReachable(cfSvc, entry) ? 100 : 0));
  const colors = CF_LOCAL_OVERVIEW_SERVICES.map((entry) =>
    cfServiceReachable(cfSvc, entry) ? entry.color : "#fb7185",
  );
  overviewCharts.cf.data.labels = CF_LOCAL_OVERVIEW_SERVICES.map((s) => s.label);
  overviewCharts.cf.data.datasets[0].data = upVals;
  overviewCharts.cf.data.datasets[0].backgroundColor = colors;
  overviewCharts.cf.update();

  const eng = countEngineContainersForChart(data.containers, data.docker_overview?.counts);
  overviewCharts.engine.data.datasets[0].data = [eng.running, eng.paused, eng.stopped];
  overviewCharts.engine._engineNames = eng.names;
  overviewCharts.engine._engineGhostStopped = eng.ghostStopped;
  overviewCharts.engine.update();

  const topSvc = [...(data.services || [])]
    .sort((a, b) => Number(b.metrics?.cpu_percent || 0) - Number(a.metrics?.cpu_percent || 0))
    .slice(0, 10);
  overviewCharts.svcBar.data.labels = topSvc.map((x) => x.service);
  overviewCharts.svcBar.data.datasets[0].data = topSvc.map((x) => Number(x.metrics?.cpu_percent || 0));
  overviewCharts.svcBar.update();
}

function uiAccessControlHeaders(json = true) {
  const h = { "X-Control-Token": controlToken() };
  if (json) h["Content-Type"] = "application/json";
  return h;
}

function resolveAssistUrl(data, loginUrlHint) {
  const raw = String(data?.assist_url || "").trim();
  if (!raw) return "";
  if (/^https?:\/\//i.test(raw)) return raw;
  const base = String(loginUrlHint || data?.login_url || "").trim();
  if (base) {
    try {
      return new URL(raw.startsWith("/") ? raw : `/${raw}`, base).href;
    } catch (_) {
      /* fall through */
    }
  }
  try {
    return new URL(raw, window.location.origin).href;
  } catch (_) {
    return raw;
  }
}

async function fetchUiLaunchUrl(slug, loginUrlHint) {
  const res = await fetch(`/api/ui-credentials/${encodeURIComponent(slug)}/launch-token`, {
    method: "POST",
    headers: uiAccessControlHeaders(),
    body: JSON.stringify({ token: controlToken() }),
  });
  const data = await res.json();
  if (!data.ok) {
    throw new Error(data.error || "Could not create login assist token. Save control token on Control tab.");
  }
  return resolveAssistUrl(data, loginUrlHint);
}

async function openUiSignedIn(slug, loginUrlHint, row) {
  if (row?.container && row.container_running === false) {
    await showAppAlert(
      `${row.label || slug} is not running. Start it from Control → Ecosystem services, then try Auto-login again.`,
      "Auto-login",
    );
    return;
  }
  try {
    const url = await fetchUiLaunchUrl(slug, loginUrlHint);
    window.open(url, "_blank", "noopener,noreferrer");
  } catch (e) {
    await showAppAlert(String(e.message || e), "Auto-login");
  }
}

async function copyUiMagicLink(slug, btn, loginUrlHint) {
  try {
    const url = await fetchUiLaunchUrl(slug, loginUrlHint);
    if (!navigator.clipboard) {
      await showAppCopyLinkModal(url, "Magic link");
      return;
    }
    await navigator.clipboard.writeText(url);
    if (btn) {
      const prev = btn.textContent;
      btn.textContent = "Copied";
      setTimeout(() => {
        btn.textContent = prev;
      }, 1400);
    }
  } catch (e) {
    await showAppAlert(String(e.message || e), "Magic link");
  }
}

function copyUiAccessText(text) {
  const t = String(text || "").trim();
  if (!t) return;
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(t).catch(() => {});
  }
}

function uiAccessCopyBtn(text, label = "Copy") {
  if (!text) return "";
  return `<button type="button" class="small-copy ui-access-copy" data-ui-copy="${escapeAttr(text)}" title="Copy ${escapeAttr(label)}">${escapeHtml(label)}</button>`;
}

function renderUiAccessLoginCell(row) {
  const d = row.login_details || {};
  const kind =
    d.kind ||
    (row.auth_type === "protocol" ? "protocol" : row.auth_type === "browse_only" ? "browse_only" : "web");

  if (kind === "protocol") {
    const parts = [
      `<div class="ui-access-login__summary">${escapeHtml(d.summary || "")} ${uiAccessCopyBtn(d.username, "user")} ${uiAccessCopyBtn(d.password, "password")}</div>`,
    ];
    if (d.host && d.port) {
      parts.push(
        `<div class="ui-access-login__row muted small">Host <code>${escapeHtml(d.host)}</code> · Port <code>${escapeHtml(String(d.port))}</code>${d.alt_host ? ` · Alt <code>${escapeHtml(d.alt_host)}</code>` : ""} ${uiAccessCopyBtn(`${d.host}:${d.port}`, "host:port")}</div>`,
      );
    }
    for (const cs of d.connection_strings || []) {
      parts.push(
        `<div class="ui-access-login__row"><code class="ui-access-login__mono">${escapeHtml(cs)}</code> ${uiAccessCopyBtn(cs, "connection")}</div>`,
      );
    }
    if (d.browser_url) {
      parts.push(
        `<div class="ui-access-login__row muted small"><a href="${escapeAttr(d.browser_url)}"${externalNavigationAttrs(d.browser_url)}>File browser</a> <code class="small">${escapeHtml(d.browser_url)}</code> ${uiAccessCopyBtn(d.browser_url, "URL")}</div>`,
      );
    }
    if (d.footnote) {
      parts.push(`<div class="ui-access-login__footnote muted small">${escapeHtml(d.footnote)}</div>`);
    }
    return `<div class="ui-access-login">${parts.join("")}</div>`;
  }

  if (kind === "browse_only") {
    const urls = d.browser_urls || (d.browser_url ? [d.browser_url] : []);
    return `<div class="ui-access-login">
      <div class="ui-access-login__summary muted small">${escapeHtml(d.summary || "Read-only · no login")}</div>
      ${urls
        .map(
          (u) =>
            `<div class="ui-access-login__row"><a href="${escapeAttr(u)}"${externalNavigationAttrs(u)}>${escapeHtml(u)}</a> ${uiAccessCopyBtn(u, "URL")}</div>`,
        )
        .join("")}
    </div>`;
  }

  const cred = row.credentials?.values || {};
  const user = d.username || cred.username || cred.email || cred.driver || "—";
  const pw = d.password || cred.password || cred.secretKey || "";
  const url = d.login_url || row.login_url || "";
  const summary =
    d.summary ||
    `User: ${user}${pw ? ` · Password: ${pw}` : ""}`;
  return `<div class="ui-access-login">
    <div class="ui-access-login__row"><code class="small">${escapeHtml(url)}</code> ${uiAccessCopyBtn(url, "URL")}</div>
    <div class="ui-access-login__summary muted small">${escapeHtml(summary)}</div>
  </div>`;
}

function uiAccessActionButtons(row) {
  const btns = [];
  if (row.can_auto_login) {
    btns.push(
      `<button type="button" class="ollama-act ollama-act--safe" data-ui-auto-login="${escapeAttr(row.slug)}">Auto-login</button>`,
    );
    btns.push(
      `<button type="button" class="ollama-act ollama-act--safe" data-ui-copy-link="${escapeAttr(row.slug)}">Copy magic link</button>`,
    );
  }
  btns.push(
    `<a class="ollama-act ollama-act--safe" href="${escapeAttr(row.login_url)}"${externalNavigationAttrs(row.login_url)}>Open manual</a>`,
  );
  if (row.can_edit) {
    btns.push(`<button type="button" class="ollama-act ollama-act--safe" data-ui-edit="${escapeAttr(row.slug)}">Edit</button>`);
  }
  if (row.can_reset) {
    btns.push(
      `<button type="button" class="ollama-act ollama-act--caution" data-ui-reset="${escapeAttr(row.slug)}">Reset &amp; apply</button>`,
    );
  }
  return btns.join(" ");
}

function bindUiAccessButtons(root, rows) {
  root.querySelectorAll("[data-ui-auto-login]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const slug = btn.getAttribute("data-ui-auto-login");
      const row = rows.find((r) => r.slug === slug);
      openUiSignedIn(slug, row?.login_url, row);
    });
  });
  root.querySelectorAll("[data-ui-copy-link]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const slug = btn.getAttribute("data-ui-copy-link");
      const row = rows.find((r) => r.slug === slug);
      copyUiMagicLink(slug, btn, row?.login_url);
    });
  });
  root.querySelectorAll("[data-ui-edit]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const slug = btn.getAttribute("data-ui-edit");
      const row = rows.find((r) => r.slug === slug);
      if (row) promptEditUiCredentials(slug, row.label, row.credentials);
    });
  });
  root.querySelectorAll("[data-ui-reset]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const slug = btn.getAttribute("data-ui-reset");
      const row = rows.find((r) => r.slug === slug);
      if (row) resetUiCredentials(slug, row.label);
    });
  });
  root.querySelectorAll("[data-ui-copy]").forEach((btn) => {
    btn.addEventListener("click", () => copyUiAccessText(btn.getAttribute("data-ui-copy")));
  });
  applyExternalLinkAttrs(root);
}

function renderOverviewUiAccess(catalog) {
  const el = document.getElementById("overviewUiAccess");
  if (!el) return;
  const rows = catalog?.services || [];
  const actionable = rows.filter((r) => r.can_auto_login || r.can_reset || r.can_edit || r.auth_type === "browse_only");
  if (!actionable.length) {
    el.innerHTML = "<p class='muted small'>No UI logins in registry.</p>";
    return;
  }
  const tokenNote =
    catalog.token_required && !controlToken()
      ? "<p class='overview-ui-access__warn'>Set control token on Control tab first.</p>"
      : "";
  el.innerHTML = `${tokenNote}<ul class="overview-ui-access__list">${actionable
    .map(
      (r) =>
        `<li><strong>${escapeHtml(r.label)}</strong> <span class="muted small">(${escapeHtml(r.slug)})</span></li>`,
    )
    .join("")}</ul><p class="muted small"><a href="/hub#hub-ui-access">Service hubs → UI access</a> for Auto-login, Copy magic link, Edit, Reset.</p>`;
}

async function resetUiCredentials(slug, label) {
  const ok = await showAppConfirm({
    title: `Reset ${label}`,
    message:
      "Restore compose defaults, apply on the service, and restart if needed. This cannot be undone.",
    confirmText: "Reset & apply",
    cancelText: "Cancel",
    danger: true,
  });
  if (!ok) return;
  const res = await fetch(`/api/ui-credentials/${encodeURIComponent(slug)}/reset`, {
    method: "POST",
    headers: uiAccessControlHeaders(),
    body: JSON.stringify({ token: controlToken() }),
  });
  const data = await res.json();
  await showAppAlert(
    data.ok
      ? `${label}: ${data.message || "reset OK"}${data.restarted ? " (container restarted)" : ""}`
      : data.error || data.message || "Reset failed",
    data.ok ? "Reset complete" : "Reset failed",
  );
  loadUiAccessPanel();
}

async function promptEditUiCredentials(slug, label, credMeta) {
  const values = credMeta?.values || {};
  const keys = Object.keys(values);
  if (!keys.length) {
    await showAppAlert("No editable fields for this service.", "Edit credentials");
    return;
  }
  const fields = keys.map((k) => ({
    key: k,
    label: k,
    value: values[k] || "",
    secret: /password|secret/i.test(k),
  }));
  const next = await showAppCredentialsForm({
    title: "Edit credentials",
    serviceLabel: label,
    fields,
  });
  if (!next) return;
  try {
    const res = await fetch(`/api/ui-credentials/${encodeURIComponent(slug)}`, {
      method: "PUT",
      headers: uiAccessControlHeaders(),
      body: JSON.stringify({ token: controlToken(), values: next }),
    });
    const data = await res.json();
    if (!data.ok) {
      await showAppAlert(data.error || data.message || "Save failed", "Edit credentials");
    } else {
      const msgParts = [`${label}: credentials saved.`];
      if (data.applied) msgParts.push(data.message || "Applied to running service.");
      else if (data.message) msgParts.push(data.message);
      await showAppAlert(msgParts.join(" "), "Edit credentials");
    }
    loadUiAccessPanel();
  } catch (e) {
    await showAppAlert(String(e), "Edit credentials");
  }
}

async function loadUiAccessPanel() {
  try {
    const res = await fetch("/api/ui-credentials/catalog");
    const data = await res.json();
    renderOverviewUiAccess(data);
    const el = document.getElementById("uiAccessPanel");
    if (!el) return;
    const rows = data.services || [];
    if (!rows.length) {
      el.innerHTML =
        "<p class='muted small'>No UI login registry entries. Ensure <code>ecosystem-stack/config/ui-login-registry.json</code> is mounted at <code>/project</code> and restart the dashboard.</p>";
      return;
    }
    const tokenBanner =
      data.token_required && !controlToken()
        ? `<div class="ui-access-banner ui-access-banner--warn">Control token required for auto-login, edit, and reset. Set it on the <a href="/?tab=controlTab">Control</a> tab, click Save, then try again.</div>`
        : `<div class="ui-access-banner muted small">Magic links expire in ~60s. Local dev only — not for production.</div>`;
    el.innerHTML = `${tokenBanner}
      <table class="ui-access-table">
        <thead><tr><th>Service</th><th>Login</th><th>Status</th><th>Actions</th></tr></thead>
        <tbody>
          ${rows
            .map((row) => {
              const cred = row.credentials || {};
              const status = cred.is_custom ? "custom" : "defaults";
              const containerPill =
                row.container && row.container_running === false
                  ? '<span class="pill warn" title="Start from Control tab">stopped</span>'
                  : row.container && row.container_running === true
                    ? '<span class="pill ok">running</span>'
                    : "";
              return `<tr>
                <td><strong>${escapeHtml(row.label)}</strong><br><code class="muted small">${escapeHtml(row.slug)}</code></td>
                <td>${renderUiAccessLoginCell(row)}</td>
                <td><span class="pill ${status === "custom" ? "warn" : "ok"}">${escapeHtml(status)}</span> ${containerPill}</td>
                <td class="ui-access-actions">${uiAccessActionButtons(row)}</td>
              </tr>`;
            })
            .join("")}
        </tbody>
      </table>`;
    bindUiAccessButtons(el, rows);
  } catch (e) {
    el.innerHTML = `<p class='muted'>${escapeHtml(String(e))}</p>`;
  }
}

function initHubUiAccess() {
  const root = document.getElementById("hubUiAccess");
  if (!root) return;
  const slug = root.getAttribute("data-hub-slug");
  if (!slug) return;
  fetch(`/api/ui-credentials/${encodeURIComponent(slug)}`)
    .then((r) => r.json())
    .then((row) => {
      if (!row.ok) {
        root.innerHTML = "";
        return;
      }
      root.innerHTML = `<div class="hub-ui-access__btns">${uiAccessActionButtons(row)}</div>`;
      bindUiAccessButtons(root, [row]);
    })
    .catch(() => {
      root.innerHTML = "";
    });
}

const CF_STACK_CONTAINERS = new Set([
  "minio",
  "valkey",
  "r2-adapter",
  "kv-adapter",
  "d1-adapter",
  "workers-runtime",
  "browser-rendering-local",
  "autoscaler",
  "autoscale-demo",
]);

function renderInfrastructurePanel(data, cf, opts = {}) {
  if (!document.getElementById("systemStatus")) return;
  renderSystemStatus(data.system_status);
  renderDockerOverview(data.docker_overview);
  renderCloudflareLocal(cf, data.services || []);
  renderFileTransferLocal(data.services || []);
  if (!opts.skipTrendCharts) {
    updateCharts(data);
  }
  renderServices(data);
  renderContainers(data);
  loadOllamaModelsPanel();
  loadOllamaBackupSelect();
}

function renderFileTransferLocal(services) {
  const el = document.getElementById("fileTransferLocal");
  if (!el) return;
  const FT_CONTAINERS = new Set(["leco-sftp", "leco-ftp", "leco-file-browser"]);
  const rows = (services || []).filter((s) => FT_CONTAINERS.has(s.container));
  const badge = (running, status) => {
    const ok = running || (status || "").toLowerCase() === "running";
    return `<span class="pill ${ok ? "ok" : "bad"}">${escapeHtml(status || (ok ? "running" : "down"))}</span>`;
  };
  const find = (name) => rows.find((s) => s.container === name);
  const sftp = find("leco-sftp");
  const ftp = find("leco-ftp");
  const browser = find("leco-file-browser");
  el.innerHTML = `
    <div class="cf-local-card">
      <strong>Start stack</strong>
      <p class="muted small" style="margin:0 0 8px">Starts <code>file-transfer/docker-compose.yml</code> (SFTP, FTP, read-only browser).</p>
      <button type="button" class="ctrl-bulk ctrl-bulk--deploy" data-infra-quickstart="stack-file-transfer-all" data-infra-quickstart-action="deploy" data-infra-quickstart-label="Start file transfer stack">Start FTP / SFTP stack</button>
    </div>
    <div class="cf-local-card">
      <strong>SFTP</strong>
      <div class="row"><span>Container</span><code>leco-sftp</code></div>
      <div class="row"><span>Status</span>${badge(!!sftp?.container_info?.status && sftp.container_info.status === "running", sftp?.container_info?.status || "missing")}</div>
      <div class="row"><span>Connect</span><code>sftp -P 2222 leco@localhost</code></div>
      <div class="row"><span>Hub</span><a href="/hub/sftp">/hub/sftp</a></div>
    </div>
    <div class="cf-local-card">
      <strong>FTP</strong>
      <div class="row"><span>Container</span><code>leco-ftp</code></div>
      <div class="row"><span>Status</span>${badge(!!ftp?.container_info?.status && ftp.container_info.status === "running", ftp?.container_info?.status || "missing")}</div>
      <div class="row"><span>Connect</span><code>ftp://leco:leco@localhost:21</code></div>
      <div class="row"><span>Hub</span><a href="/hub/ftp">/hub/ftp</a></div>
    </div>
    <div class="cf-local-card">
      <strong>Read-only browser</strong>
      <div class="row"><span>Container</span><code>leco-file-browser</code></div>
      <div class="row"><span>Status</span>${badge(!!browser?.container_info?.status && browser.container_info.status === "running", browser?.container_info?.status || "missing")}</div>
      <div class="row"><span>Browse</span><a href="http://files.lh"${externalNavigationAttrs("http://files.lh")}>files.lh</a> · <a href="http://ftp-files.lh"${externalNavigationAttrs("http://ftp-files.lh")}>ftp-files.lh</a> · <a href="http://sftp-files.lh"${externalNavigationAttrs("http://sftp-files.lh")}>sftp-files.lh</a></div>
    </div>
  `;
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
        : `Ollama unreachable from LEco DevOps (check <code>ollama</code> on lh-network): ${data.ollama_base || "http://ollama:11434"}`;
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

const APP_MODAL_HTML = `<div id="appModalOverlay" class="app-modal-overlay" hidden>
  <div class="app-modal-dialog" role="dialog" aria-modal="true" aria-labelledby="appModalTitle">
    <div class="app-modal-brand" aria-hidden="true">
      <img src="/static/leco-logo-mark.svg" alt="" width="28" height="28" />
      <span>LEco DevOps</span>
    </div>
    <div class="app-modal-inner">
      <h3 id="appModalTitle" class="app-modal-title">Confirm</h3>
      <div id="appModalMessage" class="app-modal-message"></div>
      <div class="app-modal-bar">
        <button type="button" id="appModalCancel" class="ctrl-overlay-btn ctrl-overlay-btn--caution">Cancel</button>
        <button type="button" id="appModalPrimary" class="ctrl-overlay-btn ctrl-overlay-btn--ops">Continue</button>
      </div>
    </div>
  </div>
</div>`;

function ensureAppModalDom() {
  if (document.getElementById("appModalMessage")) return;
  const tpl = document.createElement("template");
  tpl.innerHTML = APP_MODAL_HTML.trim();
  document.body.appendChild(tpl.content.firstElementChild);
}

function _appModalNodes() {
  ensureAppModalDom();
  initAppModal();
  const overlay = document.getElementById("appModalOverlay");
  const cancel = document.getElementById("appModalCancel");
  const primary = document.getElementById("appModalPrimary");
  const titleEl = document.getElementById("appModalTitle");
  const messageEl = document.getElementById("appModalMessage");
  if (!overlay || !cancel || !primary || !titleEl || !messageEl) return null;
  return { overlay, cancel, primary, titleEl, messageEl };
}

function initAppModal() {
  ensureAppModalDom();
  const overlay = document.getElementById("appModalOverlay");
  if (!overlay || overlay.dataset.wired === "1") return;
  overlay.dataset.wired = "1";
  const cancel = document.getElementById("appModalCancel");
  const primary = document.getElementById("appModalPrimary");
  if (!cancel || !primary) return;

  const finish = (value) => {
    const fn = _appModalResolve;
    _appModalResolve = null;
    overlay.classList.remove("app-modal-overlay--inspect");
    _appModalResetFormState();
    overlay.hidden = true;
    if (typeof fn === "function") fn(value);
  };

  cancel.addEventListener("click", () => finish(false));
  primary.addEventListener("click", () => {
    const mode = overlay.dataset.mode || "confirm";
    if (mode === "alert") finish(undefined);
    else finish(true);
  });
  overlay.addEventListener("click", (e) => {
    const mode = overlay.dataset.mode || "confirm";
    if (e.target === overlay && (mode === "confirm" || mode === "prompt")) finish(false);
  });
  document.addEventListener("keydown", (e) => {
    if (overlay.hidden) return;
    if (e.key !== "Escape") return;
    e.preventDefault();
    const mode = overlay.dataset.mode || "confirm";
    if (mode === "alert") finish(undefined);
    else finish(false);
  });
}

function _appModalDialogEl() {
  return document.querySelector("#appModalOverlay .app-modal-dialog");
}

function _appModalResetFormState() {
  const dialog = _appModalDialogEl();
  dialog?.classList.remove("app-modal-dialog--form", "app-modal-dialog--copy");
  const msg = document.getElementById("appModalMessage");
  if (msg) {
    msg.classList.remove("app-modal-message--form", "app-modal-message--copy");
  }
}

function _appModalSetPrimaryVariant(btn, variant) {
  btn.classList.remove("ctrl-overlay-btn--primary", "ctrl-overlay-btn--ops", "ctrl-overlay-btn--danger");
  if (variant === "danger") btn.classList.add("ctrl-overlay-btn--danger");
  else if (variant === "primary") btn.classList.add("ctrl-overlay-btn--primary");
  else btn.classList.add("ctrl-overlay-btn--ops");
}

function showAppAlert(message, title = "Notice") {
  const nodes = _appModalNodes();
  if (!nodes) return Promise.resolve();
  const { overlay, cancel, primary, titleEl, messageEl } = nodes;
  _appModalResetFormState();
  titleEl.textContent = title;
  messageEl.textContent = message;
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

/**
 * Same as showAppAlert but renders the body as HTML (innerHTML). Caller MUST
 * escape any untrusted strings before composing the HTML. Used by the
 * "Show CLI" modal that needs <pre> blocks for copyable command snippets.
 */
function showAppHtmlAlert(htmlMessage, title = "Notice") {
  const nodes = _appModalNodes();
  if (!nodes) return Promise.resolve();
  const { overlay, cancel, primary, titleEl, messageEl } = nodes;
  _appModalResetFormState();
  titleEl.textContent = title;
  messageEl.innerHTML = htmlMessage;
  overlay.dataset.mode = "alert";
  cancel.classList.add("is-hidden");
  primary.textContent = "Close";
  _appModalSetPrimaryVariant(primary, "primary");
  return new Promise((resolve) => {
    _appModalResolve = () => resolve();
    overlay.hidden = false;
    primary.focus();
  });
}

function showAppPrompt({
  title = "Input",
  message = "",
  label = "",
  defaultValue = "",
  placeholder = "",
  confirmText = "Save",
  cancelText = "Cancel",
  password = false,
} = {}) {
  const nodes = _appModalNodes();
  if (!nodes) return Promise.resolve(null);
  const { overlay, cancel, primary, titleEl, messageEl: msgEl } = nodes;
  _appModalResetFormState();
  _appModalDialogEl()?.classList.add("app-modal-dialog--form");
  msgEl.classList.add("app-modal-message--form");
  titleEl.textContent = title;
  const inputId = "appModalFieldInput";
  msgEl.innerHTML = `${message ? `<p class="app-modal-message__lead">${escapeHtml(message)}</p>` : ""}
    <label class="app-modal-field" for="${inputId}">
      <span class="app-modal-field-label">${escapeHtml(label || "Value")}</span>
      <input id="${inputId}" class="app-modal-input" type="${password ? "password" : "text"}" autocomplete="off" />
    </label>`;
  const input = document.getElementById(inputId);
  input.value = defaultValue || "";
  input.placeholder = placeholder || "";
  overlay.dataset.mode = "prompt";
  cancel.classList.remove("is-hidden");
  cancel.textContent = cancelText;
  primary.textContent = confirmText;
  _appModalSetPrimaryVariant(primary, "primary");
  return new Promise((resolve) => {
    _appModalResolve = (ok) => resolve(ok ? input.value : null);
    overlay.hidden = false;
    input.focus();
    if (!password) input.select();
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        primary.click();
      }
    });
  });
}

function showAppCredentialsForm({ title = "Edit credentials", serviceLabel = "", fields = [] } = {}) {
  const nodes = _appModalNodes();
  if (!nodes) return Promise.resolve(null);
  const { overlay, cancel, primary, titleEl, messageEl: msgEl } = nodes;
  _appModalResetFormState();
  _appModalDialogEl()?.classList.add("app-modal-dialog--form");
  msgEl.classList.add("app-modal-message--form");
  titleEl.textContent = title;
  const lead = serviceLabel
    ? `<p class="app-modal-message__lead muted small">${escapeHtml(serviceLabel)} — leave secrets blank to keep current values.</p>`
    : `<p class="app-modal-message__lead muted small">Leave secrets blank to keep current values.</p>`;
  msgEl.innerHTML =
    lead +
    fields
      .map((f) => {
        const isSecret = !!f.secret;
        const val = String(f.value || "");
        const showVal = isSecret && val.includes("•") ? "" : val;
        return `<label class="app-modal-field">
      <span class="app-modal-field-label">${escapeHtml(f.label || f.key)}</span>
      <input class="app-modal-input" type="${isSecret ? "password" : "text"}" data-field-key="${escapeAttr(f.key)}" value="${escapeAttr(showVal)}" autocomplete="off" placeholder="${isSecret ? "Leave blank to keep" : ""}" />
    </label>`;
      })
      .join("");
  overlay.dataset.mode = "prompt";
  cancel.classList.remove("is-hidden");
  cancel.textContent = "Cancel";
  primary.textContent = "Save";
  _appModalSetPrimaryVariant(primary, "primary");
  const inputs = [...msgEl.querySelectorAll("[data-field-key]")];
  return new Promise((resolve) => {
    _appModalResolve = (ok) => {
      if (!ok) {
        resolve(null);
        return;
      }
      const next = {};
      inputs.forEach((inp) => {
        const v = inp.value.trim();
        if (v) next[inp.getAttribute("data-field-key")] = v;
      });
      resolve(next);
    };
    overlay.hidden = false;
    inputs[0]?.focus();
  });
}

async function showAppCopyLinkModal(url, title = "Magic link") {
  const nodes = _appModalNodes();
  if (!nodes) return;
  const { overlay, cancel, primary, titleEl, messageEl: msgEl } = nodes;
  _appModalResetFormState();
  _appModalDialogEl()?.classList.add("app-modal-dialog--copy");
  msgEl.classList.add("app-modal-message--copy");
  titleEl.textContent = title;
  msgEl.innerHTML = `<p class="app-modal-message__lead">Copy this link (expires in ~60s):</p>
    <input type="text" class="app-modal-input app-modal-input--readonly" readonly value="${escapeAttr(url)}" id="appModalCopyField" />
    <button type="button" class="ollama-act ollama-act--safe app-modal-copy-btn" id="appModalCopyBtn">Copy to clipboard</button>`;
  const copyBtn = document.getElementById("appModalCopyBtn");
  const field = document.getElementById("appModalCopyField");
  copyBtn?.addEventListener("click", () => {
    const text = field?.value || url;
    if (navigator.clipboard) {
      navigator.clipboard.writeText(text).then(() => {
        copyBtn.textContent = "Copied";
        setTimeout(() => {
          copyBtn.textContent = "Copy to clipboard";
        }, 1400);
      });
    } else {
      field?.select();
    }
  });
  overlay.dataset.mode = "alert";
  cancel.classList.add("is-hidden");
  primary.textContent = "Done";
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
  const nodes = _appModalNodes();
  if (!nodes) return Promise.resolve(false);
  const { overlay, cancel, primary, titleEl, messageEl } = nodes;
  _appModalResetFormState();
  titleEl.textContent = title;
  messageEl.textContent = message;
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

/** Confirm dialog with HTML body (caller must escape untrusted strings). */
function showAppHtmlConfirm({
  title = "Confirm",
  htmlMessage = "",
  confirmText = "Continue",
  cancelText = "Cancel",
  danger = false,
  formLayout = true,
} = {}) {
  const nodes = _appModalNodes();
  if (!nodes) return Promise.resolve(false);
  const { overlay, cancel, primary, titleEl, messageEl } = nodes;
  _appModalResetFormState();
  titleEl.textContent = title;
  messageEl.innerHTML = htmlMessage;
  if (formLayout) {
    _appModalDialogEl()?.classList.add("app-modal-dialog--form");
    messageEl.classList.add("app-modal-message--form");
  }
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

/**
 * Show a copy-pasteable list of CLI commands for a given backend+model.
 * Modal-only — does NOT execute anything. Used by the "Show CLI" buttons in
 * Ollama and AirLLM toolbars so users can lift commands into a terminal.
 *
 *   backend: "ollama" | "airllm"
 *   model:   the model id/HF name (may be empty -> placeholder)
 */
function buildModelCliSnippets(backend, model) {
  const m = (model || "").trim();
  const safe = m || (backend === "airllm" ? "<HF_OWNER/MODEL>" : "<MODEL[:tag]>");
  if (backend === "airllm") {
    return [
      { title: "Install (pull) into AirLLM cache", lines: [
        `./leco-cli.sh airllm install '${safe}'`,
        `# or via API:`,
        `curl -X POST https://airllm.lh/api/pull -H 'Content-Type: application/json' \\`,
        `     -d '{"name":"${safe}","stream":false}'`,
      ] },
      { title: "Load into RAM (warm)", lines: [
        `./leco-cli.sh airllm load '${safe}'`,
        `# or:`,
        `curl -X POST https://airllm.lh/api/chat -H 'Content-Type: application/json' \\`,
        `     -d '{"model":"${safe}","messages":[{"role":"user","content":"hello"}],"stream":false,"keep_alive":-1}'`,
      ] },
      { title: "Unload from RAM (keep_alive=0)", lines: [
        `./leco-cli.sh airllm unload '${safe}'`,
        `# or:`,
        `curl -X POST https://airllm.lh/api/generate -H 'Content-Type: application/json' \\`,
        `     -d '{"model":"${safe}","prompt":"","keep_alive":0}'`,
      ] },
      { title: "Remove from disk", lines: [
        `./leco-cli.sh airllm remove-model '${safe}'`,
        `# or:`,
        `curl -X DELETE https://airllm.lh/api/delete -H 'Content-Type: application/json' \\`,
        `     -d '{"name":"${safe}"}'`,
      ] },
      { title: "List installed / pinned (informational)", lines: [
        `./leco-cli.sh airllm list`,
        `./leco-cli.sh airllm popular`,
      ] },
    ];
  }
  return [
    { title: "Install (pull) into Ollama", lines: [
      `./leco-cli.sh ollama install '${safe}'`,
      `# or via host ollama CLI:`,
      `ollama pull '${safe}'`,
      `# or via API:`,
      `curl -X POST http://ollama.lh/api/pull -H 'Content-Type: application/json' \\`,
      `     -d '{"name":"${safe}","stream":false}'`,
    ] },
    { title: "Load into RAM (warm)", lines: [
      `./leco-cli.sh ollama load '${safe}'`,
      `ollama run '${safe}' --keepalive=-1 ''`,
      `# or API:`,
      `curl -X POST http://ollama.lh/api/chat -H 'Content-Type: application/json' \\`,
      `     -d '{"model":"${safe}","messages":[{"role":"user","content":"hello"}],"stream":false,"keep_alive":-1}'`,
    ] },
    { title: "Unload from RAM (keep_alive=0)", lines: [
      `./leco-cli.sh ollama unload '${safe}'`,
      `# or API:`,
      `curl -X POST http://ollama.lh/api/generate -H 'Content-Type: application/json' \\`,
      `     -d '{"model":"${safe}","prompt":"","keep_alive":0}'`,
    ] },
    { title: "Remove from disk", lines: [
      `./leco-cli.sh ollama remove-model '${safe}'`,
      `ollama rm '${safe}'`,
      `# or API:`,
      `curl -X DELETE http://ollama.lh/api/delete -H 'Content-Type: application/json' \\`,
      `     -d '{"name":"${safe}"}'`,
    ] },
    { title: "List installed / pinned (informational)", lines: [
      `./leco-cli.sh ollama list`,
      `./leco-cli.sh ollama popular`,
    ] },
  ];
}

/** Render snippets in a generic <pre>-based modal. Reuses showAppAlert layout. */
async function showCliSnippetsModal(backend, model) {
  const groups = buildModelCliSnippets(backend, model);
  const heading = backend === "airllm" ? "AirLLM" : "Ollama";
  const body = groups
    .map((g) => {
      const block = g.lines.map((l) => l.replaceAll("<", "&lt;").replaceAll(">", "&gt;")).join("\n");
      return `<div style="margin:10px 0"><div style="font-weight:600;margin-bottom:4px">${g.title}</div><pre style="white-space:pre-wrap;background:rgba(0,0,0,.05);padding:8px;border-radius:6px;font-size:12px;overflow:auto">${block}</pre></div>`;
    })
    .join("");
  const html = `<div style="max-width:680px;font-family:system-ui,sans-serif">
    <div style="margin-bottom:8px" class="muted small">Copy any of the commands below — nothing was executed.</div>
    ${body}
  </div>`;
  // showAppAlert renders a string; pass HTML by allowing inner markup via a sentinel.
  if (typeof showAppHtmlAlert === "function") {
    await showAppHtmlAlert(html, `${heading} CLI · ${model || "(no model entered)"}`);
    return;
  }
  // Fallback: strip HTML and join with blank lines.
  const text = groups
    .map((g) => `# ${g.title}\n${g.lines.join("\n")}`)
    .join("\n\n");
  await showAppAlert(text, `${heading} CLI · ${model || "(no model entered)"}`);
}

/** Populate one of the "Popular …" <select> elements from /api/{backend}/popular. */
async function loadPopularModels(backend, selectId, inputId) {
  const sel = document.getElementById(selectId);
  const inp = document.getElementById(inputId);
  if (!sel) return;
  try {
    const res = await fetch(`/api/${backend}/popular`);
    const data = await res.json();
    const models = (data && data.models) || [];
    sel.innerHTML =
      '<option value="">Pick a popular model…</option>' +
      models
        .map((m) => {
          const label = [m.label || m.name, m.size ? `(${m.size})` : ""].filter(Boolean).join(" ");
          const title = (m.description || "").replaceAll('"', "&quot;");
          return `<option value="${escapeAttr(m.name)}" title="${title}">${escapeHtml(label)}</option>`;
        })
        .join("");
    sel.addEventListener("change", () => {
      const v = sel.value || "";
      if (v && inp) inp.value = v;
    }, { once: false });
  } catch (e) {
    sel.innerHTML = '<option value="">(catalog unavailable)</option>';
  }
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
      await showAppAlert("Enter a model name (e.g. llama3.2:latest).", "Install");
      return;
    }
    runOllamaModelAction("pull", name);
  });
  document.getElementById("ollamaLoadNameBtn")?.addEventListener("click", async () => {
    const inp = document.getElementById("ollamaPullNameInput");
    const name = (inp?.value || "").trim();
    if (!name) {
      await showAppAlert("Enter a model name (e.g. llama3.2:latest).", "Load");
      return;
    }
    const ok = await showAppConfirm({
      title: "Load model into RAM",
      message: `Warm "${name}" into RAM with keep_alive=-1? (First load may take a while.)`,
      confirmText: "Load",
    });
    if (!ok) return;
    runOllamaModelAction("warm", name);
  });
  document.getElementById("ollamaUnloadNameBtn")?.addEventListener("click", async () => {
    const inp = document.getElementById("ollamaPullNameInput");
    const name = (inp?.value || "").trim();
    if (!name) {
      await showAppAlert("Enter a model name to unload.", "Unload");
      return;
    }
    runOllamaModelAction("unload", name);
  });
  document.getElementById("ollamaRemoveNameBtn")?.addEventListener("click", async () => {
    const inp = document.getElementById("ollamaPullNameInput");
    const name = (inp?.value || "").trim();
    if (!name) {
      await showAppAlert("Enter a model name to remove.", "Remove");
      return;
    }
    const ok = await showAppConfirm({
      title: "Remove model",
      message: `Delete "${name}" from disk? This cannot be undone.`,
      confirmText: "Remove",
      danger: true,
    });
    if (!ok) return;
    runOllamaModelAction("delete", name);
  });
  document.getElementById("ollamaShowCmdBtn")?.addEventListener("click", async () => {
    const inp = document.getElementById("ollamaPullNameInput");
    const name = (inp?.value || "").trim();
    await showCliSnippetsModal("ollama", name);
  });
  loadPopularModels("ollama", "ollamaPopularSelect", "ollamaPullNameInput");
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
      message: `Overwrite ecosystem-stack/config/ollama-pinned-models.txt with pinned names from ${fn}? Models on disk are not deleted.`,
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
  const switchToken = beginGlobalPreloader({
    label: `Switching to ${tabLabel(tabId)}…`,
    detail: `UI tab · loading ${tabLabel(tabId)} data`,
    tabId,
  });
  if (activeTab === "hostedAppsTab" && tabId !== "hostedAppsTab") {
    hostedLogStreamLiveKey = "";
    stopHostedLogStream();
  }
  try {
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
    if (tabId === "platformTab") {
      loadPlatformTab();
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
      loadAiNewsPanel();
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
  } finally {
    endGlobalPreloader(switchToken);
  }
  if (!opts.skipUrl) {
    syncDashboardUrl(tabId, {
      replace: !!opts.replace,
      docId: opts.preferredDocId ?? (tabId === "docsTab" ? window.__docCurrentId || "" : undefined),
      appSlug: opts.hostedSlug,
      hash: opts.hash,
    });
  }
  if (tabId === "infrastructureTab") {
    scrollInfraHashAnchor(opts.hash);
  }
}

/** Scroll to #infra-ollama / #infra-airllm when opening Infrastructure with a hash. */
function scrollInfraHashAnchor(hashOverride) {
  const raw = hashOverride ?? window.location.hash ?? "";
  const hash = String(raw).replace(/^#/, "");
  if (!hash || !hash.startsWith("infra-")) return;
  requestAnimationFrame(() => {
    const el = document.getElementById(hash);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

function initTabs() {
  document.querySelectorAll(".tab-btn[data-tab]").forEach((btn) => {
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
      ${s.services_missing_names && s.services_missing_names.length ? `<ul class="missing-svc-list muted small">${s.services_missing_names.map((n) => `<li>${escapeHtml(n)}</li>`).join("")}</ul>` : ""}
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

function cfContainerStatusPill(info) {
  if (!info?.exists) return `<span class="pill bad">missing</span>`;
  const st = info.status || "unknown";
  if (st === "running") return `<span class="pill ok">running</span>`;
  if (st === "paused") return `<span class="pill warn">paused</span>`;
  return `<span class="pill bad">${escapeHtml(st)}</span>`;
}

function renderCloudflareLocalContainers(services) {
  const el = document.getElementById("cloudflareLocalContainers");
  if (!el) return;
  const rows = (services || []).filter((s) => CF_STACK_CONTAINERS.has(s.container));
  if (!rows.length) {
    el.innerHTML =
      '<p class="muted small">No stack data yet. Use <strong>Start Cloudflare local</strong> above or Control → Cloudflare local → Deploy.</p>';
    return;
  }
  const running = rows.filter((s) => s.container_info?.status === "running").length;
  el.innerHTML = `
    <p class="muted small" style="margin:0 0 8px">${running}/${rows.length} compose containers running · <a href="/?tab=controlTab">Control tab</a> for per-service actions</p>
    <table class="cf-stack-table muted small">
      <thead><tr><th>Container</th><th>Service</th><th>Docker</th><th>HTTP probe</th></tr></thead>
      <tbody>
        ${rows
          .map((s) => {
            const activeProbes = (s.url_checks || []).filter((u) => !u.skipped);
            const probe = !activeProbes.length
              ? '<span class="pill na">n/a</span>'
              : activeProbes.some((u) => u.ok)
                ? '<span class="pill ok">ok</span>'
                : '<span class="pill bad">fail</span>';
            return `<tr><td><code>${escapeHtml(s.container)}</code></td><td>${escapeHtml(s.service)}</td><td>${cfContainerStatusPill(s.container_info)}</td><td>${probe}</td></tr>`;
          })
          .join("")}
      </tbody>
    </table>`;
}

function renderCloudflareLocal(cf, services) {
  renderCloudflareLocalContainers(services);
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
      <strong>Start stack</strong>
      <p class="muted small" style="margin:0 0 8px">Starts all services in <code>cloudflare-local/docker-compose.yml</code> (MinIO, Valkey, adapters, Workers demo, browser, autoscaler).</p>
      <button type="button" class="ctrl-bulk ctrl-bulk--deploy" data-infra-quickstart="stack-cf-all" data-infra-quickstart-action="deploy" data-infra-quickstart-label="Start Cloudflare local">Start Cloudflare local stack</button>
    `)}
    ${wrapCfLocalCard(`
      <strong>Service Reachability</strong>
      <div class="row"><span>R2</span><span class="pill ${badgeColor(!!svc.r2?.reachable)}">${badgeText(!!svc.r2?.reachable)}</span></div>
      <div class="row"><span>KV</span><span class="pill ${badgeColor(!!svc.kv?.reachable)}">${badgeText(!!svc.kv?.reachable)}</span></div>
      <div class="row"><span>D1</span><span class="pill ${badgeColor(!!svc.d1?.reachable)}">${badgeText(!!svc.d1?.reachable)}</span></div>
      <div class="row"><span>Workers</span><span class="pill ${badgeColor(!!svc.workers?.reachable)}">${badgeText(!!svc.workers?.reachable)}</span></div>
      <div class="row"><span>Browser</span><span class="pill ${badgeColor(!!svc.browser?.reachable)}">${badgeText(!!svc.browser?.reachable)}</span></div>
      <div class="row"><span>Autoscaler</span><span class="pill ${badgeColor(!!svc.autoscale?.reachable)}">${badgeText(!!svc.autoscale?.reachable)}</span></div>
      <div class="row"><span>Valkey (KV backend)</span><span class="pill ${badgeColor(!!svc.valkey?.reachable)}">${badgeText(!!svc.valkey?.reachable)}</span></div>
      <div class="row"><span>MinIO (R2 backend)</span><span class="pill ${badgeColor(!!svc.minio?.reachable)}">${badgeText(!!svc.minio?.reachable)}</span></div>
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
      ${cfDualUrlRow("S3 API (SDK/CLI)", "http://s3.lh")}
      <div class="row muted small" style="margin-top:6px">Valkey TCP: <code>valkey.lh:6380</code> · <a href="/?tab=docsTab&amp;doc=cf-leco-service-map">CF ↔ LEco map</a> · <a href="/hub">Service hubs</a></div>
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
          "CPU chart value is a thermal-pressure + loadavg proxy from macOS (not die °C); see ecosystem-stack/scripts/macos-write-cpu-temp.sh.",
        );
      else if (pts.some((p) => p.system?.cpu_temp_source === "host_file"))
        parts.push("CPU temp from macOS host file (~/.local-eco-host-metrics/cpu_temp_c.txt).");
      else parts.push("CPU temp from /sys thermal zones.");
    } else {
      parts.push(
        "No CPU temp: macOS — start LEco DevOps via ecosystem-stack/services/dashboard.sh (installs host temp LaunchAgent) or run ecosystem-stack/scripts/macos-write-cpu-temp.sh; Linux — mount host /sys (see dashboard.sh).",
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
      "<p><strong>Host temp file not configured.</strong> This panel appears when <code>DASHBOARD_HOST_CPU_TEMP_FILE</code> is set (macOS LEco DevOps deploy).</p>";
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
    <p class="muted small" style="margin-top:0.75rem"><strong>Manual host run (Mac terminal):</strong> <code>bash /path/to/repo/ecosystem-stack/scripts/macos-write-cpu-temp.sh</code> — the container cannot execute macOS <code>powermetrics</code> for you.</p>
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
    logLine("  bash /project/ecosystem-stack/scripts/macos-write-cpu-temp.sh");
    logLine("  # optional kick: launchctl kickstart -k gui/$(id -u)/com.local-ecosystem.host-cpu-temp");
    logLine("—");
    logLine("LEco DevOps APIs (this browser → LEco DevOps container):");
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

/** Token for API calls: skip when server has no token; use saved or inline field in Updates panel. */
function resolveControlToken() {
  if (!dashboardTokenRequired()) return "";
  const saved = controlToken();
  if (saved) return saved;
  const inline = document.getElementById("ucControlToken")?.value?.trim();
  if (inline) {
    try {
      localStorage.setItem("dashboard_control_token", inline);
    } catch (_) {
      /* private mode */
    }
    return inline;
  }
  return null;
}

function emphasizeUpdateCatalogTokenHint(message) {
  const hint = document.getElementById("ucTokenHint");
  const msg = document.getElementById("ucTokenMsg");
  if (msg && message) msg.textContent = message;
  hint?.classList.add("uc-token-hint--warn");
  document.getElementById("ucControlToken")?.focus();
}

const BULK_ECOSYSTEM_TARGET_ID = "stack-ecosystem-all";

const CONTROL_GROUP_ORDER = ["ecosystem", "ecosystem-stack", "infra", "cloudflare-local"];

const CONTROL_GROUP_META = {
  ecosystem: {
    title: "Bulk & orchestration",
    lead: "Full-stack actions for every ecosystem service (same as the bulk toolbar above), exposed as a single API control target.",
    sectionClass: " control-target-group--bulk",
  },
  "ecosystem-stack": {
    title: "Ecosystem stack & Traefik",
    lead: "Edge proxy, apps, Ollama, n8n, Postgres, file transfer stack script, and LEco DevOps (this UI).",
    sectionClass: "",
  },
  "cloudflare-local": {
    title: "Cloudflare local",
    lead: "MinIO, Valkey, adapters, Workers runtime, demo, autoscaler, and whole compose stack.",
    sectionClass: " control-target-group--cf",
  },
  infra: {
    title: "Infra add-ons & file transfer",
    lead: "MySQL, Redis, Mailpit, Adminer, cache lab (infra/docker-compose.yml) plus FTP, SFTP, and read-only file browser (file-transfer/docker-compose.yml).",
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

function controlPolicyBadgeHtml(policy) {
  if (!policy || policy === "start") return "";
  const cls = policy === "offloaded" ? "control-policy-badge--offloaded" : "control-policy-badge--stop";
  return `<span class="control-policy-badge ${cls}">Default: ${escapeHtml(policy)}</span>`;
}

function controlPolicySelectorHtml(targetId, currentPolicy) {
  const pol = currentPolicy || "start";
  const opts = ["start", "stop", "offloaded"];
  const btns = opts.map((o) => {
    const active = o === pol ? " control-policy-btn--active" : "";
    return `<button type="button" class="control-policy-btn${active}" data-policy-target="${escapeAttr(targetId)}" data-policy-value="${escapeAttr(o)}">${escapeHtml(o)}</button>`;
  }).join("");
  return `<div class="control-policy-selector" data-policy-for="${escapeAttr(targetId)}">${btns}</div>`;
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
  const policyBadge = controlPolicyBadgeHtml(t.default_policy);
  const policySelector = t.id.startsWith("stack-") ? "" : controlPolicySelectorHtml(t.id, t.default_policy);
  const offloadedClass = t.default_policy === "offloaded" ? " control-card--offloaded" : "";
  const bulkClass = t.id === BULK_ECOSYSTEM_TARGET_ID ? " control-card--bulk" : "";
  return `
          <div class="control-card${offloadedClass}${bulkClass}" data-brand="${escapeHtml(brand)}">
            <div class="control-card__head">
              <div class="control-card__iconcol">
                <span class="control-card__emoji" aria-hidden="true">${SB.emojiFor(brand)}</span>
                ${SB.iconHtml(brand)}
              </div>
              <div class="control-card__titles">
                <h3>${escapeHtml(t.label)}${policyBadge}</h3>
                <div class="control-meta">${escapeHtml(t.group)}${t.container ? ` · <code>${escapeHtml(t.container)}</code>` : ""}</div>
              </div>
            </div>
            <p class="control-card__runtime ${rtClass}" title="${rtLabel}"><span class="control-runtime-dot" aria-hidden="true"></span><span class="control-runtime-text">${rtLabel}</span></p>
            ${policySelector}
            <div class="${wrapClass}">
              <div class="control-actions control-actions--primary">${primaryBtns}</div>
              ${aside}
            </div>
          </div>`;
}

const HOSTED_APP_ACTIONS = ["deploy", "recreate", "pause", "remove", "reset", "restart", "staging", "start", "stop", "unpause"];

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
      const st =
        a.pending_registration === true
          ? ' <span class="hosted-app-sidebar-badge" title="Materialized under hosting/app-available — not in leco-registry.yaml yet">Staging</span>'
          : "";
      return `<button type="button" data-hosted-slug="${escapeAttr(a.id)}" class="${a.id === hostedSelectedSlug ? "is-active" : ""}">${escapeHtml(a.label || a.id)}${st}${ver}</button>`;
    })
    .join("");
  nav.querySelectorAll("[data-hosted-slug]").forEach((btn) => {
    btn.addEventListener("click", () => {
      hostedSelectedSlug = btn.getAttribute("data-hosted-slug") || "";
      renderHostedAppsSidebar();
      resetHostedAppsDetailForLoading(hostedSelectedSlug);
      refreshHostedAppsPanel();
      syncDashboardUrl("hostedAppsTab", { appSlug: hostedSelectedSlug });
    });
  });
}

function resetHostedAppsDetailForLoading(slug) {
  const app = hostedAppsList.find((x) => x.id === slug);
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
  const unregisterHintEl = document.getElementById("hostedAppsUnregisterHint");
  const ledgerEl = document.getElementById("hostedAppsResourceLedger");
  const localEl = document.getElementById("hostedAppsLocalProfile");
  const cfEl = document.getElementById("hostedAppsCfResources");
  if (titleEl) titleEl.textContent = app?.label || slug || "—";
  if (metaEl) {
    const pend = app?.pending_registration === true;
    metaEl.textContent = pend
      ? `Staging · app id ${slug || "—"} (not in registry yet) · Loading app details…`
      : `Registry id: ${slug || "—"} · Loading app details…`;
  }
  if (rtEl) {
    rtEl.className = "control-card__runtime control-runtime--na";
    rtEl.title = "Loading";
    rtEl.innerHTML =
      '<span class="control-runtime-dot" aria-hidden="true"></span><span class="control-runtime-text">Loading…</span>';
  }
  if (linksEl) linksEl.innerHTML = '<span class="muted">Loading links…</span>';
  if (kpiEl) kpiEl.innerHTML = '<span class="muted">Loading runtime metrics…</span>';
  if (tbody) tbody.innerHTML = '<tr><td colspan="6" class="muted">Loading services…</td></tr>';
  if (insightsEl) insightsEl.innerHTML = '<li class="muted">Loading insights…</li>';
  if (logPre) logPre.textContent = "Loading logs…";
  if (controlsEl) controlsEl.innerHTML = '<p class="muted small">Loading controls…</p>';
  if (svcSel) svcSel.innerHTML = '<option value="">(loading)</option>';
  if (unregisterHintEl) {
    unregisterHintEl.classList.add("is-hidden");
    unregisterHintEl.innerHTML = "";
  }
  const valPre = document.getElementById("hostedAppsConfigValidation");
  if (valPre) {
    valPre.textContent = "";
    valPre.classList.add("is-hidden");
    valPre.classList.remove("hosted-apps-config-validation--pass", "hosted-apps-config-validation--fail");
  }
  const stagingBar = document.getElementById("hostedAppsStagingBar");
  if (stagingBar) {
    stagingBar.classList.toggle("is-hidden", !(app && app.pending_registration === true));
  }
  const sumEl = document.getElementById("hostedAppsManifestSummary");
  if (sumEl) sumEl.innerHTML = '<p class="muted small">Loading manifest summary…</p>';
  if (ledgerEl) ledgerEl.innerHTML = '<p class="muted small">Loading resource ledger…</p>';
  const attachedEl = document.getElementById("hostedAppsAttachedServicesBody");
  if (attachedEl) attachedEl.innerHTML = '<p class="muted small">Loading attached services…</p>';
  const seedEl = document.getElementById("hostedAppsSeedDataBody");
  if (seedEl) seedEl.innerHTML = '<p class="muted small">Loading seed data…</p>';
  if (localEl) localEl.innerHTML = '<p class="muted small">Loading local profile…</p>';
  if (cfEl) cfEl.innerHTML = '<p class="muted small">Loading Cloudflare bindings…</p>';
}

function resetHostedAppsRightPanelForMutation(message = "Refreshing hosted apps…") {
  if (activeTab !== "hostedAppsTab") return;
  const empty = document.getElementById("hostedAppsEmpty");
  const detail = document.getElementById("hostedAppsDetail");
  if (empty) {
    empty.classList.remove("is-hidden");
    empty.textContent = message;
  }
  if (detail) detail.classList.add("is-hidden");
  hostedLogStreamLiveKey = "";
  stopHostedLogStream();
  destroyHostedAppCharts();
}

async function syncHostedAppsAfterRegistryMutation(opts = {}) {
  const removedSlug = String(opts.removedSlug || "").trim();
  const message = String(opts.message || "").trim();
  hostedPanelRequestSeq += 1;
  if (removedSlug && hostedSelectedSlug === removedSlug) {
    hostedSelectedSlug = "";
  }
  resetHostedAppsRightPanelForMutation(
    message || (removedSlug ? `Removed ${removedSlug}. Refreshing hosted apps…` : "Refreshing hosted apps…"),
  );
  await loadHostedAppsList();
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
    overviewHostedAppsPayload = data && typeof data === "object" ? data : { apps: hostedAppsList };
    overviewHostedAppsCachedAtMs = Date.now();
    renderOverviewHostedAppsCard(overviewHostedAppsPayload);
    if (!hostedAppsList.length) {
      if (empty) {
        empty.classList.remove("is-hidden");
        empty.innerHTML =
          'No hosted apps found. Register via <code>leco-devops ecosystem-register</code> (<code>config/leco-registry.yaml</code>), or place generated <code>hosting/app-available/&lt;dir&gt;/leco.app.yaml</code> and reload — staging apps appear until you register them. See <code>config/leco-registry.example.yaml</code>.';
      }
      if (detail) detail.classList.add("is-hidden");
      const nav = document.getElementById("hostedAppsSidebar");
      if (nav) nav.innerHTML = "";
      destroyHostedAppCharts();
      return;
    }
    if (empty) empty.classList.add("is-hidden");
    if (detail) detail.classList.remove("is-hidden");
    const qsApp = String(new URLSearchParams(window.location.search).get("app") || "").trim();
    if (qsApp && hostedAppsList.some((a) => a.id === qsApp)) {
      hostedSelectedSlug = qsApp;
    }
    const still = hostedAppsList.some((a) => a.id === hostedSelectedSlug);
    if (!still) hostedSelectedSlug = hostedAppsList[0].id;
    renderHostedAppsSidebar();
    resetHostedAppsDetailForLoading(hostedSelectedSlug);
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

function renderHostedManifestSummary(manifestUi, snap) {
  const el = document.getElementById("hostedAppsManifestSummary");
  if (!el) return;
  const mu = manifestUi || {};
  const pdc = mu.profile_docker_compose;
  const routes = Array.isArray(mu.routes) ? mu.routes : [];
  const args = snap && Array.isArray(snap.compose_docker_args) ? snap.compose_docker_args : [];
  let html = '<div class="hosted-manifest-summary__title">Effective manifest (Compose + routing)</div>';
  const rp = mu.resolved_paths && typeof mu.resolved_paths === "object" ? mu.resolved_paths : {};
  const rpKeys = Object.keys(rp).filter((k) => typeof rp[k] === "string" && String(rp[k]).trim());
  if (rpKeys.length) {
    const rpLabel = (k) => {
      const m = {
        sourceRoot: "App root (resolved)",
        manifestPath: "Bridge manifest (leco.app.yaml)",
        localHostProfile: "Localhost profile (e.g. leco.yaml)",
        wranglerConfig: "Wrangler config",
        packageJson: "package.json",
        dockerComposeFile: "Docker Compose file",
        composeOverrideFile: "Compose override",
        envFile: "Env file",
        dockerfile: "Dockerfile",
        wordpressConfigPhp: "WordPress config",
        nginxConfig: "nginx config",
        varnishVcl: "Varnish VCL",
        phpFpmPool: "PHP-FPM pool",
        mysqlInit: "MySQL init",
        mongoInit: "Mongo init",
        redisConfig: "Redis config",
      };
      return m[k] || k;
    };
    rpKeys.sort((a, b) => {
      const pri = (x) =>
        ["sourceRoot", "manifestPath", "localHostProfile", "wranglerConfig", "packageJson", "dockerComposeFile"].indexOf(x);
      const pa = pri(a);
      const pb = pri(b);
      if (pa >= 0 && pb >= 0) return pa - pb;
      if (pa >= 0) return -1;
      if (pb >= 0) return 1;
      return a.localeCompare(b);
    });
    html += '<div class="hosted-manifest-summary__subtitle">Resolved paths (absolute)</div>';
    html += '<ul class="muted small hosted-manifest-summary__list hosted-manifest-summary__paths">';
    rpKeys.forEach((k) => {
      html += `<li><strong>${escapeHtml(rpLabel(k))}</strong> · <code>${escapeHtml(String(rp[k]))}</code></li>`;
    });
    html += "</ul>";
  }
  if (args.length) {
    html += `<p class="muted small hosted-manifest-summary__cmd"><code>docker compose ${args.map((x) => escapeHtml(String(x))).join(" ")}</code></p>`;
  } else if (mu.effective_has_docker_compose === true) {
    html +=
      '<p class="muted small hosted-manifest-summary__warn">Compose is declared in <code>leco.yaml</code> but LEco did not build a <code>-f</code> chain — check <code>composeFile</code> under resolved app root, optional <code>composeFileFromManifest</code> beside <code>leco.app.yaml</code>, container vs host paths, and <code>additionalComposeFilesFromManifest</code>.</p>';
  } else {
    html +=
      '<p class="muted small">No <code>infrastructure.dockerCompose</code> in profile (Workers-only is fine). Add compose under <code>leco.yaml</code> to drive Deploy / service rows.</p>';
  }
  if (pdc && (pdc.compose_file || pdc.compose_file_from_manifest)) {
    const mx = pdc.additional_compose_files_from_manifest || [];
    const ax = pdc.additional_compose_files || [];
    html += '<ul class="muted small hosted-manifest-summary__list">';
    if (pdc.compose_file_from_manifest) {
      html += `<li>Primary <code>composeFileFromManifest</code> (beside <code>leco.app.yaml</code>): <code>${escapeHtml(String(pdc.compose_file_from_manifest))}</code></li>`;
    }
    if (pdc.compose_file) {
      html += `<li>Primary <code>composeFile</code> (relative to app root): <code>${escapeHtml(String(pdc.compose_file))}</code></li>`;
    }
    if (ax.length)
      html += `<li>Extra <code>-f</code> (app root): ${ax.map((x) => `<code>${escapeHtml(String(x))}</code>`).join(", ")}</li>`;
    if (mx.length)
      html += `<li>Extra <code>-f</code> (manifest dir): ${mx.map((x) => `<code>${escapeHtml(String(x))}</code>`).join(", ")}</li>`;
    html += "</ul>";
  }
  if (routes.length) {
    const bits = routes.map((r) => {
      const h = r.hostname ? escapeHtml(String(r.hostname)) : "—";
      const pr = r.api_path_prefix ? ` · API ${escapeHtml(String(r.api_path_prefix))}` : "";
      let tgt = "";
      if (r.frontend && r.frontend.host) {
        tgt += ` → UI ${escapeHtml(String(r.frontend.host))}:${escapeHtml(String(r.frontend.port ?? ""))}`;
      }
      if (r.api_backend && r.api_backend.host) {
        tgt += ` · API ${escapeHtml(String(r.api_backend.host))}:${escapeHtml(String(r.api_backend.port ?? ""))}`;
      }
      if (r.backend && r.backend.host) {
        tgt = ` → ${escapeHtml(String(r.backend.host))}:${escapeHtml(String(r.backend.port ?? ""))}`;
      }
      return `<span class="hosted-manifest-summary__route">${h}${pr}${tgt}</span>`;
    });
    html += `<p class="muted small"><strong>Traefik routing</strong> · ${bits.join(" · ")}</p>`;
  }
  el.innerHTML = html;
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
  html += `<p class="hosted-resource-ledger__lead muted small">LEco DevOps uses your <strong>effective manifest</strong>: <code>leco.app.yaml</code> plus the profile file (<code>${escapeHtml(String(lhp))}</code>). Nothing here is inferred from disk outside those files.</p>`;
  html += '<div class="hosted-resource-ledger__grid">';

  html += '<div class="hosted-resource-ledger__card">';
  html += '<div class="hosted-resource-ledger__card-title">Docker / Compose</div>';
  if (pdc && (pdc.compose_file || pdc.compose_file_from_manifest)) {
    html += '<p class="hosted-resource-ledger__card-body">';
    if (pdc.compose_file_from_manifest) {
      html += `<strong>Compose entry</strong> (<code>composeFileFromManifest</code>): <code>${escapeHtml(String(pdc.compose_file_from_manifest))}</code> — hosting-only wrapper; upstream compose is usually <code>include</code>d from the <code>source</code> link.`;
    }
    if (pdc.compose_file) {
      html += `${pdc.compose_file_from_manifest ? "<br />" : ""}<strong>Compose file</strong> (<code>composeFile</code> relative to app root): <code>${escapeHtml(String(pdc.compose_file))}</code>`;
    }
    const ax = pdc.additional_compose_files;
    const mx = pdc.additional_compose_files_from_manifest;
    if (Array.isArray(ax) && ax.length) {
      html += `<br /><strong>Extra <code>-f</code> (app root):</strong> ${ax.map((x) => `<code>${escapeHtml(String(x))}</code>`).join(", ")}`;
    }
    if (Array.isArray(mx) && mx.length) {
      html += `<br /><strong>Extra <code>-f</code> (manifest dir):</strong> ${mx.map((x) => `<code>${escapeHtml(String(x))}</code>`).join(", ")}`;
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
        '<p class="muted small">This app uses <strong>dedicatedLocalAdapters</strong>: resources live on <strong>in-compose</strong> services (<code>leco-local-kv-adapter</code>, <code>leco-local-r2-adapter</code>, <code>leco-local-d1-adapter</code> plus Valkey/MinIO) in <strong>your</strong> compose project — they show under this app in Docker Desktop. <code>leco.local-cf.yaml</code> records the same Docker DNS bases the adapters use. See <code>docker-compose.leco-dedicated-cf.example.yml</code> in <code>hosting/samples/sample-cloudflare-application/</code>.</p>';
    } else {
      html +=
        '<p class="muted small">These are <strong>provisioned names</strong> on the ecosystem&rsquo;s shared <code>kv-adapter</code>, <code>r2-adapter</code>, and <code>d1-adapter</code> (the <strong>cloudflare-local</strong> Docker stack). They are <strong>not</strong> extra containers inside your app&rsquo;s compose file — same model as Cloudflare production (managed APIs). The full list is in the table below and in <code>leco.local-cf.yaml</code>.</p>';
    }
  } else if (expTotal > 0 && w.wrangler_configured) {
    const dedHint = dedicatedAdapters
      ? " With <strong>dedicatedLocalAdapters</strong>, start the stack (including <code>leco-local-*</code> services) first so provision can reach the adapters on <code>lh-network</code>."
      : "";
    html += `<p class="muted small">Wrangler lists <strong>${expKv}</strong> KV, <strong>${expR2}</strong> R2, <strong>${expD1}</strong> D1 bindings, but <code>leco.local-cf.yaml</code> is missing or empty — run <strong>Deploy</strong> (local CF provision on) or <code>leco-devops provision-local-cf</code> so dedicated names appear here.${dedHint}</p>`;
  } else if (w.wrangler_configured) {
    html += '<p class="muted small">Wrangler is configured; no KV/R2/D1 tables in this env or nothing provisioned yet.</p>';
  } else {
    html += '<p class="muted small">No Wrangler-driven local CF resources for this manifest.</p>';
  }
  html += "</div>";

  html += "</div>";
  el.innerHTML = html;
}

function copyHostedAttachedText(text) {
  const t = String(text || "").trim();
  if (!t) return;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(t).catch(() => {});
  }
}

function formatBytes(n) {
  const b = Number(n) || 0;
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KiB`;
  return `${(b / (1024 * 1024)).toFixed(1)} MiB`;
}

function hostedSeedDataStoresFromSnap(snap) {
  const groups = snap?.attached_services?.groups || [];
  const out = [];
  groups.forEach((grp) => {
    (grp.items || []).forEach((item) => {
      const kind = String(item.kind || "").toLowerCase();
      if (kind === "mongodb" || kind === "mysql" || kind === "postgres" || kind === "redis") {
        out.push(item);
      }
    });
  });
  return out;
}

function hostedSeedEndpoint(item, scope) {
  const eps = Array.isArray(item.connection_endpoints) ? item.connection_endpoints : [];
  return eps.find((e) => String(e.scope || "") === scope) || null;
}

/** Database name from Attached services only; otherwise a neutral placeholder. */
function hostedSeedMongoDatabase(item) {
  const creds = item.credentials && typeof item.credentials === "object" ? item.credentials : {};
  if (creds.database) return String(creds.database);
  const hostUri = hostedSeedEndpoint(item, "host")?.uri || "";
  const m = String(hostUri).match(/\/([^/?]+)(?:\?|$)/);
  if (m && m[1] && m[1] !== "" && m[1] !== "admin") return m[1];
  return "<database>";
}

/** URI safe for copy-paste in docs/commands — never embed username/password. */
function hostedSeedUriWithoutSecrets(uri) {
  const raw = String(uri || "").trim();
  if (!raw) return "mongodb://127.0.0.1:<host-port>/<database>";
  try {
    const u = new URL(raw);
    u.username = "";
    u.password = "";
    let out = u.toString();
    if (out.endsWith("/")) out = out.slice(0, -1);
    return out;
  } catch (_) {
    return raw.replace(/\/\/[^@]+@/, "//");
  }
}

function buildHostedSeedDumpSnippets(slug, snap) {
  const dataRel = `hosting/app-available/${slug}/data`;
  const snippets = [];
  const stores = hostedSeedDataStoresFromSnap(snap);

  stores.forEach((item) => {
    const kind = String(item.kind || "").toLowerCase();
    const name = String(item.name || item.id || kind);
    const hostEp = hostedSeedEndpoint(item, "host");
    const container = String(item.container || `${slug}-${name}`).trim();

    if (kind === "mongodb") {
      const db = hostedSeedMongoDatabase(item);
      const restoreBase = hostedSeedUriWithoutSecrets(hostEp?.uri);
      const restoreWithDb =
        db !== "<database>" && !restoreBase.includes(`/${db}`)
          ? `${restoreBase.replace(/\/$/, "")}/${db}`
          : restoreBase;
      const dbFlag = db === "<database>" ? "--db=<database>" : `--db=${db}`;
      const restorePath = db === "<database>" ? "<database>" : db;
      const restoreAll = restoreBase;
      snippets.push({
        id: `mongo-pipe-one-${name}`,
        title: `MongoDB · ${name} — one database (pipe)`,
        lines: [
          `# Single DB — omit --db on mongodump to dump the whole server (see next block).`,
          `mongodump --uri="mongodb://localhost:27017" ${dbFlag} --archive \\`,
          `  | mongorestore --uri="${restoreWithDb}" --archive --drop`,
        ],
      });
      snippets.push({
        id: `mongo-pipe-all-${name}`,
        title: `MongoDB · ${name} — full server / all databases (pipe)`,
        lines: [
          `# All DBs on the source instance (admin, config, app DBs, …). Target: host root URI (no /database path).`,
          `mongodump --uri="mongodb://localhost:27017" --archive \\`,
          `  | mongorestore --uri="${restoreAll}" --archive --drop`,
          `# If auth is required, add URI options or env — do not commit passwords.`,
        ],
      });
      snippets.push({
        id: `mongo-folder-one-${name}`,
        title: `MongoDB · ${name} — one database → ${dataRel}/mongo/`,
        lines: [
          `mkdir -p ${dataRel}/mongo`,
          `mongodump --uri="mongodb://localhost:27017" ${dbFlag} --out="${dataRel}/mongo"`,
          `# Creates ${dataRel}/mongo/<database>/ — Import data auto-detects each subfolder.`,
        ],
      });
      snippets.push({
        id: `mongo-folder-all-${name}`,
        title: `MongoDB · ${name} — full server → ${dataRel}/mongo/`,
        lines: [
          `mkdir -p ${dataRel}/mongo`,
          `mongodump --uri="mongodb://localhost:27017" --out="${dataRel}/mongo"`,
          `# Creates ${dataRel}/mongo/<database>/ for every DB — Import data runs one step per folder.`,
        ],
      });
      snippets.push({
        id: `mongo-exec-one-${name}`,
        title: `MongoDB · ${name} — one database (docker exec)`,
        lines: [
          `mongodump --uri="mongodb://localhost:27017" ${dbFlag} --out=/tmp/seed`,
          `docker cp /tmp/seed ${container}:/tmp/seed`,
          `docker exec ${container} mongorestore --drop --db=${restorePath} /tmp/seed/${restorePath}`,
        ],
      });
      snippets.push({
        id: `mongo-exec-all-${name}`,
        title: `MongoDB · ${name} — full server (docker exec)`,
        lines: [
          `mongodump --uri="mongodb://localhost:27017" --out=/tmp/seed`,
          `docker cp /tmp/seed ${container}:/tmp/seed`,
          `docker exec ${container} mongorestore --drop /tmp/seed`,
        ],
      });
    } else if (kind === "mysql") {
      const creds = item.credentials || {};
      const db = creds.database || "<database>";
      snippets.push({
        id: `mysql-${name}`,
        title: `MySQL · ${name}`,
        lines: [
          `# -p prompts for password (never put passwords in shell history)`,
          `mysqldump -h 127.0.0.1 -P <source-port> -u <user> -p ${db} > ${dataRel}/mysql/${db}.sql`,
          `docker exec -i ${container} mysql -u<user> -p ${db} < ${dataRel}/mysql/${db}.sql`,
          `# Host connection: see Attached services (copy URI there).`,
        ],
      });
    } else if (kind === "postgres") {
      const creds = item.credentials || {};
      const db = creds.database || "<database>";
      snippets.push({
        id: `pg-${name}`,
        title: `PostgreSQL · ${name}`,
        lines: [
          `pg_dump -h localhost -U <user> -W ${db} > ${dataRel}/postgres/${db}.sql`,
          `docker exec -i ${container} psql -U <user> -d ${db} < ${dataRel}/postgres/${db}.sql`,
        ],
      });
    } else if (kind === "redis") {
      const hostUri = hostedSeedUriWithoutSecrets(hostEp?.uri || "redis://127.0.0.1:<host-port>");
      snippets.push({
        id: `redis-${name}`,
        title: `Redis · ${name}`,
        lines: [
          `redis-cli -u "${hostUri}" --rdb /tmp/dump.rdb`,
          `cp /tmp/dump.rdb ${dataRel}/redis/dump.rdb`,
          `# Then use Import data, or FLUSHALL + copy RDB into ${container}`,
        ],
      });
    }
  });

  if (!snippets.length) {
    snippets.push({
      id: "mongo-pipe-one-example",
      title: "MongoDB — one database (pipe)",
      lines: [
        `mongodump --uri="mongodb://localhost:27017" --db=<source-database> --archive \\`,
        `  | mongorestore --uri="mongodb://127.0.0.1:<host-port>/<target-database>" --archive --drop`,
      ],
    });
    snippets.push({
      id: "mongo-pipe-all-example",
      title: "MongoDB — full server / all databases (pipe)",
      lines: [
        `mongodump --uri="mongodb://localhost:27017" --archive \\`,
        `  | mongorestore --uri="mongodb://127.0.0.1:<host-port>" --archive --drop`,
      ],
    });
    snippets.push({
      id: "mongo-folder-example",
      title: "MongoDB — dump to data/mongo/ (one DB or full server)",
      lines: [
        `mkdir -p ${dataRel}/mongo`,
        `# One database:`,
        `mongodump --uri="mongodb://localhost:27017" --db=<source-database> --out="${dataRel}/mongo"`,
        `# Or all databases (omit --db):`,
        `mongodump --uri="mongodb://localhost:27017" --out="${dataRel}/mongo"`,
        `# Replace placeholders from Hosted apps → Attached services.`,
      ],
    });
  }

  return snippets;
}

function hostedSeedItemId(it) {
  return String(it.id || `${it.kind || "item"}:${it.path || it.label || ""}`);
}

function hostedSeedIsItemChecked(slug, it) {
  const id = hostedSeedItemId(it);
  const prefs = hostedSeedImportPrefs[slug];
  if (prefs && Object.prototype.hasOwnProperty.call(prefs, id)) {
    return !!prefs[id];
  }
  return true;
}

function collectHostedSeedSelectedIds() {
  const tbody = document.getElementById("hostedSeedImportTbody");
  if (!tbody) return [];
  return [...tbody.querySelectorAll(".hosted-seed-import-cb:checked")]
    .map((cb) => cb.getAttribute("data-import-id"))
    .filter(Boolean);
}

function updateHostedSeedSelectSummary() {
  const el = document.getElementById("hostedSeedSelectSummary");
  const tbody = document.getElementById("hostedSeedImportTbody");
  if (!el || !tbody) return;
  const boxes = [...tbody.querySelectorAll(".hosted-seed-import-cb")];
  const checked = boxes.filter((b) => b.checked);
  let bytes = 0;
  checked.forEach((b) => {
    bytes += Number(b.getAttribute("data-size-bytes")) || 0;
  });
  el.textContent = `${checked.length} of ${boxes.length} selected · ~${formatBytes(bytes)} to import`;
}

function bindHostedSeedImportSelection(slug) {
  const tbody = document.getElementById("hostedSeedImportTbody");
  const selectAll = document.getElementById("hostedSeedSelectAll");
  if (!tbody || !slug) return;

  const syncPrefs = () => {
    if (!hostedSeedImportPrefs[slug]) hostedSeedImportPrefs[slug] = {};
    tbody.querySelectorAll(".hosted-seed-import-cb").forEach((cb) => {
      const id = cb.getAttribute("data-import-id");
      if (id) hostedSeedImportPrefs[slug][id] = cb.checked;
    });
    updateHostedSeedSelectSummary();
    if (selectAll) {
      const boxes = [...tbody.querySelectorAll(".hosted-seed-import-cb")];
      selectAll.checked = boxes.length > 0 && boxes.every((b) => b.checked);
      selectAll.indeterminate =
        boxes.some((b) => b.checked) && boxes.length > 0 && !selectAll.checked;
    }
  };

  tbody.querySelectorAll(".hosted-seed-import-cb").forEach((cb) => {
    cb.addEventListener("change", () => {
      const row = cb.closest("tr");
      if (row) row.classList.toggle("hosted-seed-data__row--off", !cb.checked);
      syncPrefs();
    });
  });
  if (selectAll) {
    selectAll.addEventListener("change", () => {
      const on = !!selectAll.checked;
      tbody.querySelectorAll(".hosted-seed-import-cb").forEach((cb) => {
        cb.checked = on;
      });
      syncPrefs();
    });
  }
  syncPrefs();
}

function renderHostedSeedDumpBlock(slug, snap) {
  const snippets = buildHostedSeedDumpSnippets(slug, snap);
  return `<div class="hosted-seed-data__panel hosted-seed-data__panel--dump">
    <h5 class="hosted-seed-data__panel-title">1 · Take a dump (on your Mac)</h5>
    <p class="muted small">Commands use <strong>${escapeHtml(slug)}</strong> paths and ports from <strong>Attached services</strong> above. Mac Mongo is usually <code>localhost:27017</code>; this stack’s published port may differ.</p>
    ${snippets
      .map((sn, idx) => {
        const text = sn.lines.join("\n");
        return `<details class="hosted-seed-data__cmd-block"${idx === 0 ? " open" : ""}>
          <summary>${escapeHtml(sn.title)}</summary>
          <pre class="hosted-seed-data__cli">${escapeHtml(text)}</pre>
          <button type="button" class="hosted-seed-data__copy ctrl-act ctrl-act--ops" data-copy="${escapeAttr(text)}">Copy command</button>
        </details>`;
      })
      .join("")}
  </div>`;
}

function renderHostedSeedData(snap, slug) {
  const el = document.getElementById("hostedAppsSeedDataBody");
  const section = document.getElementById("hostedAppsSeedData");
  const sub = document.getElementById("hostedAppsSeedDataSub");
  if (!el) return;
  const appSlug = (slug || hostedSelectedSlug || snap?.slug || "").trim();
  const dataRel = appSlug ? `hosting/app-available/${appSlug}/data` : "hosting/app-available/<slug>/data";
  if (section) section.classList.remove("is-hidden");
  if (sub) {
    sub.textContent = appSlug
      ? `After ${appSlug} is deployed — import does not run at register.`
      : "Select an app — import runs after deploy.";
  }

  let html = `<div class="hosted-seed-data__path"><span class="hosted-seed-data__path-label">Data folder</span><code>${escapeHtml(dataRel)}</code></div>`;

  html += renderHostedSeedDumpBlock(appSlug, snap);

  const di = (snap && snap.data_import) || {};
  html += `<div class="hosted-seed-data__panel hosted-seed-data__panel--plan">
    <h5 class="hosted-seed-data__panel-title">2 · Import into running stack</h5>`;

  if (di.error) {
    html += `<p class="hosted-seed-data__err">Discovery failed: <code>${escapeHtml(String(di.error))}</code></p>
      <p class="muted small">Restart the LEco DevOps dashboard container after pulling the latest <code>local-ecosystem</code> (needs <code>tools/deploy-cli/leco_app/data_import</code>).</p>`;
  } else if (!di.present) {
    html += `<p class="muted small">No <code>data/</code> on disk yet — use the dump commands above (pipe is fastest), or create the folder and copy files under <code>mongo/</code>, <code>mysql/</code>, etc.</p>`;
  } else {
    const items = Array.isArray(di.items) ? di.items : [];
    const warnings = Array.isArray(di.warnings) ? di.warnings : [];
    if (di.path) {
      html += `<p class="muted small">On disk: <code>${escapeHtml(String(di.path))}</code>`;
      if (di.total_bytes) html += ` · ~${formatBytes(di.total_bytes)}`;
      html += "</p>";
    }
    if (warnings.length) {
      html += `<ul class="hosted-seed-data__warnings">${warnings
        .map((w) => `<li>${escapeHtml(String(w))}</li>`)
        .join("")}</ul>`;
    }
    if (!items.length) {
      html += '<p class="muted small">Folder exists but nothing detected — add <code>manifest.yaml</code> or standard subdirs (<code>mongo/&lt;database&gt;/</code>, …).</p>';
    } else {
      html += `<div class="hosted-seed-data__select-bar">
        <label class="hosted-seed-data__select-all">
          <input type="checkbox" id="hostedSeedSelectAll" checked />
          Select all
        </label>
        <span id="hostedSeedSelectSummary" class="muted small"></span>
      </div>`;
      html += `<table class="hosted-seed-data__table hosted-seed-data__table--select"><thead><tr>
        <th class="hosted-seed-data__chk" aria-label="Import"></th>
        <th>Kind</th><th>Target</th><th>Path</th><th>Size</th>
      </tr></thead><tbody id="hostedSeedImportTbody">${items
        .map((it) => {
          const importId = escapeAttr(hostedSeedItemId(it));
          const checked = hostedSeedIsItemChecked(appSlug, it);
          const kind = escapeHtml(String(it.kind || "—"));
          const label = escapeHtml(String(it.label || it.database || it.bucket || it.namespace || "—"));
          const path = escapeHtml(String(it.path || "—"));
          const size = formatBytes(it.size_bytes);
          const sizeBytes = Number(it.size_bytes) || 0;
          return `<tr class="hosted-seed-data__row${checked ? "" : " hosted-seed-data__row--off"}">
            <td class="hosted-seed-data__chk">
              <input type="checkbox" class="hosted-seed-import-cb" data-import-id="${importId}"
                data-size-bytes="${sizeBytes}"${checked ? " checked" : ""} aria-label="Import ${label}" />
            </td>
            <td>${kind}</td><td>${label}</td><td><code>${path}</code></td><td>${size}</td></tr>`;
        })
        .join("")}</tbody></table>`;
      html += `<p class="muted small"><strong>Import data</strong> runs only checked rows, with <code>--drop</code> / reimport when enabled.</p>`;
    }
  }

  html += `<p class="hosted-seed-data__help muted small"><a href="/help#hosted-app-data-import" target="_blank" rel="noopener noreferrer">Help → Hosted app data import</a></p></div>`;

  el.innerHTML = html;
  el.querySelectorAll(".hosted-seed-data__copy").forEach((btn) => {
    btn.addEventListener("click", () => copyHostedAttachedText(btn.getAttribute("data-copy")));
  });
  if (appSlug && Array.isArray(di.items) && di.items.length) {
    bindHostedSeedImportSelection(appSlug);
  }
}

async function runHostedDataImport(slug, { dryRun = false } = {}) {
  if (!slug) return;
  const snap = hostedLastDetailSnap;
  const di = (snap && snap.data_import) || {};
  const allItems = Array.isArray(di.items) ? di.items : [];
  const selectedIds = collectHostedSeedSelectedIds();
  if (allItems.length && !selectedIds.length) {
    await showAppHtmlAlert(
      "<p>Select at least one row to import (checkboxes in the table above).</p>",
      "Nothing selected",
    );
    return;
  }
  const selectedSet = new Set(selectedIds);
  const items = allItems.filter((it) => selectedSet.has(hostedSeedItemId(it)));
  const itemLines = items.length
    ? `<ul class="app-modal-list app-modal-list--import">${items
        .map(
          (it) =>
            `<li><span class="app-modal-list__kind">${escapeHtml(String(it.kind || "?"))}</span> ${escapeHtml(String(it.label || it.path || ""))} <span class="muted">(${formatBytes(it.size_bytes)})</span></li>`,
        )
        .join("")}</ul>`
    : "<p class=\"muted small\">No files discovered under <code>data/</code> yet — you can still dry-run; import may no-op or fail until dumps exist.</p>";

  const warnBox = dryRun
    ? `<div class="app-modal-warn-box app-modal-warn-box--info" role="status">
        <strong>Dry-run only</strong>
        <p>Prints the import plan and logs — <strong>no writes</strong> to databases or files.</p>
      </div>`
    : `<div class="app-modal-warn-box" role="alert">
        <strong>Reimport — data will be replaced</strong>
        <p>Each checked step runs with <code>--drop</code> / reimport: existing data in those targets is <strong>deleted then restored</strong> from your dumps. This cannot be undone.</p>
        <p class="muted small">Unchecked rows in the table are skipped.</p>
      </div>`;

  if (!dryRun) {
    const ok = await showAppHtmlConfirm({
      title: `Import seed data · ${slug}`,
      htmlMessage: `<p class="app-modal-message__lead">Import <strong>${items.length}</strong> selected step(s) into <strong>${escapeHtml(slug)}</strong>?</p>
        ${warnBox}
        <p class="app-modal-list__heading">Steps to import</p>
        ${itemLines}`,
      confirmText: "Import data",
      cancelText: "Cancel",
      danger: true,
      formLayout: true,
    });
    if (!ok) return;
  }

  if (dryRun) {
    const ok = await showAppHtmlConfirm({
      title: `Dry-run import · ${slug}`,
      htmlMessage: `<p class="app-modal-message__lead">Preview the import plan for <strong>${escapeHtml(slug)}</strong>?</p>
        ${warnBox}
        <p class="app-modal-list__heading">Steps to validate (${items.length})</p>
        ${itemLines}`,
      confirmText: "Run dry-run",
      cancelText: "Cancel",
      danger: false,
      formLayout: true,
    });
    if (!ok) return;
  }

  if (dashboardTokenRequired() && !controlToken()) {
    await showAppHtmlAlert(
      "<p>Set the control token on the <strong>Control</strong> tab before importing.</p>",
      "Control token required",
    );
    return;
  }

  await runDashboardStreamOverlay({
    title: dryRun ? `Dry-run import · ${slug}` : `Import data · ${slug}`,
    url: `/api/hosted-apps/${encodeURIComponent(slug)}/data-import/stream`,
    body: {
      reimport: true,
      dry_run: dryRun,
      selected_ids: allItems.length ? selectedIds : undefined,
    },
    trackProgress: true,
    actionVerb: dryRun ? "dry-run" : "import",
    onFinally: async () => {
      if (activeTab === "hostedAppsTab" && hostedSelectedSlug === slug) {
        await refreshHostedAppsPanel();
      }
    },
  });
}

let hostedDataImportButtonsBound = false;
function bindHostedDataImportButtons() {
  if (hostedDataImportButtonsBound) return;
  hostedDataImportButtonsBound = true;
  const importBtn = document.getElementById("hostedDataImportBtn");
  const dryBtn = document.getElementById("hostedDataImportDryBtn");
  if (importBtn) {
    importBtn.addEventListener("click", () => {
      if (hostedSelectedSlug) runHostedDataImport(hostedSelectedSlug, { dryRun: false });
    });
  }
  if (dryBtn) {
    dryBtn.addEventListener("click", () => {
      if (hostedSelectedSlug) runHostedDataImport(hostedSelectedSlug, { dryRun: true });
    });
  }
}

function renderHostedAttachedServices(snap) {
  const el = document.getElementById("hostedAppsAttachedServicesBody");
  const section = document.getElementById("hostedAppsAttachedServices");
  if (!el) return;
  const att = snap && snap.attached_services;
  const groups = att && Array.isArray(att.groups) ? att.groups : [];
  if (att && att.error) {
    el.innerHTML = `<p class="muted small">Attached services could not be built: <code>${escapeHtml(String(att.error))}</code>. Redeploy the dashboard container to load the latest code.</p>`;
    if (section) section.classList.remove("is-hidden");
    return;
  }
  if (!groups.length) {
    el.innerHTML =
      '<p class="muted small">No attached services detected for this app (compose, runtimes, or Wrangler bindings). Redeploy the LEco DevOps dashboard if you do not see this section after updating the repo.</p>';
    if (section) section.classList.remove("is-hidden");
    return;
  }
  let html = "";
  groups.forEach((grp) => {
    const items = Array.isArray(grp.items) ? grp.items : [];
    if (!items.length) return;
    html += `<div class="hosted-attached-services__group">`;
    html += `<div class="hosted-attached-services__group-title">${escapeHtml(String(grp.label || grp.id || "Services"))}</div>`;
    html += '<div class="hosted-attached-services__cards">';
    items.forEach((item) => {
      const kind = escapeHtml(String(item.kind || "service"));
      const status = escapeHtml(String(item.status || "—"));
      const name = escapeHtml(String(item.name || item.id || "—"));
      const source = escapeHtml(String(item.source || ""));
      const creds = item.credentials && typeof item.credentials === "object" ? item.credentials : {};
      const credRows = Object.entries(creds)
        .filter(([, v]) => v != null && String(v).trim())
        .map(
          ([k, v]) =>
            `<tr><th>${escapeHtml(k)}</th><td><code class="hosted-attached-services__cred">${escapeHtml(String(v))}</code></td></tr>`,
        )
        .join("");
      const endpoints = Array.isArray(item.connection_endpoints) ? item.connection_endpoints : [];
      const flatConns = Array.isArray(item.connection_strings) ? item.connection_strings : [];
      const connRows = endpoints.length
        ? endpoints
        : flatConns.map((uri) => ({ scope: "host", label: "Connection", uri }));
      const connHtml = connRows.length
        ? `<ul class="hosted-attached-services__conns">${connRows
            .map((ep) => {
              const cs = escapeHtml(String(ep.uri || ""));
              const lab = escapeHtml(String(ep.label || ep.scope || "Connection"));
              const scope = escapeHtml(String(ep.scope || ""));
              return `<li class="hosted-attached-services__conn-row" data-scope="${scope}">
                <span class="hosted-attached-services__conn-label">${lab}</span>
                <code>${cs}</code>
                <button type="button" class="hosted-attached-services__copy" data-copy="${cs}" title="Copy">Copy</button>
              </li>`;
            })
            .join("")}</ul>`
        : "";
      const mgmt = Array.isArray(item.management_uis) ? item.management_uis : [];
      const mgmtHtml = mgmt.length
        ? `<div class="hosted-attached-services__mgmt">${mgmt
            .map((m) => {
              const u = escapeHtml(String(m.url || ""));
              const lab = escapeHtml(String(m.label || "Open"));
              const rawUrl = String(m.url || "");
              const isDataUri = /^mongodb:|^redis:/i.test(rawUrl);
              const title = isDataUri
                ? "Opens on your Mac via 127.0.0.1 (published compose port). Docker names like mongo only work inside containers."
                : "";
              const titleAttr = title ? ` title="${escapeHtml(title)}"` : "";
              return `<a href="${u}" target="_blank" rel="noopener noreferrer"${titleAttr}>${lab}</a>`;
            })
            .join(" · ")}</div>`
        : "";
      const hubSlug = String(item.hub_slug || "").trim();
      const loginUrl = String(item.login_url || "").trim();
      let actions = "";
      if (hubSlug && item.can_auto_login) {
        actions += `<button type="button" class="hosted-attached-services__auto" data-hub-slug="${escapeHtml(hubSlug)}" data-login-url="${escapeHtml(loginUrl)}">Auto-login</button>`;
      }
      if (item.hub_url) {
        actions += ` <a href="${escapeHtml(String(item.hub_url))}" target="_blank" rel="noopener noreferrer" class="hosted-attached-services__hub">Hub</a>`;
      }
      const notes = item.notes ? `<p class="muted small hosted-attached-services__notes">${escapeHtml(String(item.notes))}</p>` : "";
      const container = item.container
        ? `<p class="muted small">Container: <code>${escapeHtml(String(item.container))}</code></p>`
        : "";
      html += `<article class="hosted-attached-services__card">
        <header class="hosted-attached-services__card-head">
          <span class="hosted-attached-services__kind">${kind}</span>
          <strong class="hosted-attached-services__name">${name}</strong>
          <span class="hosted-attached-services__status">${status}</span>
        </header>
        <p class="muted small">Source: ${source}</p>
        ${container}
        ${credRows ? `<table class="hosted-attached-services__cred-table"><tbody>${credRows}</tbody></table>` : ""}
        ${connHtml}
        ${mgmtHtml}
        ${actions ? `<div class="hosted-attached-services__actions">${actions}</div>` : ""}
        ${notes}
      </article>`;
    });
    html += "</div></div>";
  });
  el.innerHTML = html || '<p class="muted small">No attached services.</p>';
  if (section) section.classList.remove("is-hidden");
  el.querySelectorAll(".hosted-attached-services__copy").forEach((btn) => {
    btn.addEventListener("click", () => copyHostedAttachedText(btn.getAttribute("data-copy")));
  });
  el.querySelectorAll(".hosted-attached-services__auto").forEach((btn) => {
    btn.addEventListener("click", () => {
      const slug = btn.getAttribute("data-hub-slug");
      const loginUrl = btn.getAttribute("data-login-url");
      openUiSignedIn(slug, loginUrl, { container_running: true, label: slug });
    });
  });
}

function hostedUrlProbeStatusLabel(url, probes) {
  const u = String(url || "").trim();
  if (!u || !probes || typeof probes !== "object") return "—";
  const p = probes[u];
  if (!p || p.checked !== true) return "Not checked";
  const code = Number(p.status_code);
  if (p.ok === true) return Number.isFinite(code) ? `OK (${code})` : "OK";
  if (Number.isFinite(code)) return `HTTP ${code}`;
  return "Unreachable";
}

function renderHostedLocalProfile(manifestUi, snapshot) {
  const el = document.getElementById("hostedAppsLocalProfile");
  if (!el) return;
  if (!manifestUi) {
    el.innerHTML = "";
    return;
  }
  const arch = manifestUi.localhost_archetype;
  const lhp = manifestUi.local_host_profile;
  const urls = manifestUi.localhost_urls || [];
  const probes = snapshot && typeof snapshot === "object" ? snapshot.url_probes || {} : {};
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
      "<table><thead><tr><th>Role</th><th>Label</th><th>Public URL</th><th>Status</th></tr></thead><tbody>";
    urls.forEach((u) => {
      const role = u.role || "—";
      const lab = u.label || "—";
      const pub = u.publicUrl != null ? u.publicUrl : u.public_url != null ? u.public_url : "—";
      const status = hostedUrlProbeStatusLabel(pub, probes);
      html += `<tr><td>${escapeHtml(String(role))}</td><td>${escapeHtml(String(lab))}</td><td><code>${escapeHtml(String(pub))}</code></td><td>${escapeHtml(String(status))}</td></tr>`;
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
      '<p class="muted small">This manifest sets <code>cloudflare.provisionLocalResources: false</code>, so deploy will not create KV/R2/D1 on the adapters unless you run <code>leco-devops provision-local-cf</code> manually.</p>';
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
      '<tr><td colspan="3" class="muted small">No <code>leco.local-cf.yaml</code> rows — run <strong>Deploy</strong> (with local CF provision enabled) or <code>leco-devops provision-local-cf</code>.</td></tr>';
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

let _hostedDevStacksCache = null;

async function loadHostedDevStacksList() {
  if (_hostedDevStacksCache) return _hostedDevStacksCache;
  try {
    const r = await fetch("/api/dev-stacks");
    const d = await r.json();
    _hostedDevStacksCache = Array.isArray(d.stacks) ? d.stacks : [];
  } catch (_) {
    _hostedDevStacksCache = [];
  }
  return _hostedDevStacksCache;
}

async function refreshHostedDevStackSelect(slug, snap) {
  const sel = document.getElementById("hostedDevStackSelect");
  if (!sel) return;
  const stacks = await loadHostedDevStacksList();
  const current = String(snap?.manifest_ui?.dev_stack_id || snap?.manifest_ui?.platform?.dev_stack_id || "");
  const opts = ['<option value="">— none (self-contained) —</option>'];
  stacks.forEach((s) => {
    const id = escapeHtml(String(s.id || ""));
    const name = escapeHtml(String(s.name || s.id || ""));
    const st = escapeHtml(String(s.state || ""));
    const selected = id === current ? " selected" : "";
    opts.push(`<option value="${id}"${selected}>${name} (${id}) — ${st}</option>`);
  });
  sel.innerHTML = opts.join("");
}

function initHostedDevStackBinding() {
  const saveBtn = document.getElementById("hostedDevStackSave");
  if (!saveBtn || saveBtn.dataset.bound === "1") return;
  saveBtn.dataset.bound = "1";
  saveBtn.addEventListener("click", async () => {
    const slug = hostedSelectedSlug;
    const statusEl = document.getElementById("hostedDevStackStatus");
    const sel = document.getElementById("hostedDevStackSelect");
    if (!slug || !sel) return;
    const token = controlToken();
    if (statusEl) statusEl.textContent = "Saving…";
    try {
      const res = await fetch(`/api/hosted-apps/${encodeURIComponent(slug)}/platform-binding`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dev_stack_id: sel.value.trim(), token }),
      });
      const data = await res.json();
      if (statusEl) {
        statusEl.textContent = data.ok
          ? data.dev_stack_id
            ? `Bound to ${data.dev_stack_id}`
            : "Binding cleared"
          : data.error || "Failed";
      }
      if (data.ok) refreshHostedAppsPanel();
    } catch (e) {
      if (statusEl) statusEl.textContent = String(e.message || e);
    }
  });
}

async function refreshHostedAppsPanel() {
  if (activeTab !== "hostedAppsTab" || !hostedSelectedSlug) return;
  const slug = hostedSelectedSlug;
  const reqSeq = ++hostedPanelRequestSeq;
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
  const stagingBarEl = document.getElementById("hostedAppsStagingBar");
  if (stagingBarEl) {
    stagingBarEl.classList.toggle("is-hidden", !(app && app.pending_registration === true));
  }
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
    if (reqSeq !== hostedPanelRequestSeq || hostedSelectedSlug !== slug) return;
  } catch (e) {
    if (reqSeq !== hostedPanelRequestSeq || hostedSelectedSlug !== slug) return;
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

  if (titleEl && app) {
    titleEl.textContent =
      app.pending_registration === true ? `${app.label || slug} · Staging` : app.label || slug;
  }
  if (metaEl && app) {
    const mu = snap.manifest_ui || {};
    const v = mu.application_version;
    const fp = mu.deploy_fingerprint;
    const snapAt = snap.generated_at ? ` · snapshot ${snap.generated_at}` : "";
    const src = typeof mu.source_location === "string" ? mu.source_location.trim() : "";
    const pend = app.pending_registration === true;
    let bits = pend
      ? `Staging (not in registry) · app id: ${slug} · Target: ${app.target_id || "—"}`
      : `Registry id: ${slug} · Target: ${app.target_id || "—"}`;
    if (v) bits += ` · App version: ${String(v)}`;
    if (fp && fp.short_hash) {
      bits += ` · Manifest fingerprint ${String(fp.short_hash)}`;
      if (fp.mtime_iso) bits += ` (mtime ${String(fp.mtime_iso)})`;
    }
    if (src) bits += ` · Source: ${src}`;
    bits += snapAt;
    metaEl.textContent = bits;
  }

  const rt = snap.runtime || (app && app.runtime) || {};

  /* ── Prefer the *fresh* snapshot url_probes over the cached sidebar
   *    main_url_probe so the status dot reflects current reality. ──── */
  if (app && snap.url_probes) {
    const mainUrl = (snap.manifest_ui?.main_url || app.main_url || "").trim();
    const freshProbe = mainUrl && snap.url_probes[mainUrl];
    if (freshProbe && freshProbe.checked) {
      app.main_url_probe = freshProbe;
    }
  }

  const probeLabel = hostedMainUrlProbeSummary(app);
  const rtLabel = escapeHtml(probeLabel || rt.label || "—");
  const rtClass = probeLabel
    ? "control-runtime--down"
    : rt.running === true
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

  refreshHostedDevStackSelect(slug, snap);

  if (linksEl && app) {
    const muLinks = snap.manifest_ui || {};
    const parts = [];
    const mainUrl = typeof muLinks.main_url === "string" ? muLinks.main_url.trim() : "";
    const mainUrls = muLinks.main_urls && typeof muLinks.main_urls === "object" ? muLinks.main_urls : {};
    const mainSource = typeof muLinks.main_url_source === "string" ? muLinks.main_url_source.trim() : "";
    const endpointUrls = Array.isArray(muLinks.endpoint_urls) ? muLinks.endpoint_urls : [];
    if (mainUrl) {
      const sourceTag =
        mainSource === "derived_slug" ? " (derived from app id)" : mainSource ? ` (${mainSource})` : "";
      const mainHttps = typeof mainUrls.https === "string" ? mainUrls.https.trim() : "";
      const mainHttp = typeof mainUrls.http === "string" ? mainUrls.http.trim() : "";
      if (mainHttps || mainHttp) {
        if (mainHttps) {
          parts.push(
            `<a href="${escapeAttr(mainHttps)}" target="_blank" rel="noopener"><strong>Main URL (HTTPS)</strong> · ${escapeHtml(mainHttps)}${escapeHtml(sourceTag)}</a>`,
          );
        }
        if (mainHttp) {
          parts.push(
            `<a href="${escapeAttr(mainHttp)}" target="_blank" rel="noopener"><strong>Main URL (HTTP)</strong> · ${escapeHtml(mainHttp)}${escapeHtml(sourceTag)}</a>`,
          );
        }
      } else {
        parts.push(
          `<a href="${escapeAttr(mainUrl)}" target="_blank" rel="noopener"><strong>Main URL</strong> · ${escapeHtml(mainUrl)}${escapeHtml(sourceTag)}</a>`,
        );
      }
    }
    endpointUrls
      .filter((u) => {
        const publicUrl = typeof u.public_url === "string" ? u.public_url.trim() : "";
        return !!publicUrl && publicUrl !== mainUrl;
      })
      .forEach((u) => {
        const publicUrl = u.public_url.trim();
        const role = typeof u.role === "string" ? u.role.trim() : "";
        const label = typeof u.label === "string" ? u.label.trim() : "";
        const text = label || role || "Endpoint";
        parts.push(
          `<a href="${escapeAttr(publicUrl)}" target="_blank" rel="noopener">${escapeHtml(text)} · ${escapeHtml(publicUrl)}</a>`,
        );
      });
    (app.health_urls || []).forEach((u) => {
      parts.push(`<a href="${escapeAttr(u)}" target="_blank" rel="noopener">Health · ${escapeHtml(u)}</a>`);
    });
    (app.routes || []).forEach((r) => {
      const h = r.hostname || "";
      if (h) parts.push(`<span><strong>${escapeHtml(h)}</strong>${r.api_path_prefix ? ` API ${escapeHtml(r.api_path_prefix)}` : ""}</span>`);
    });
    if (!parts.length) {
      const hosts = muLinks.local_cf_adapter_hosts || {};
      const kv = typeof hosts.kv === "string" ? hosts.kv : "";
      const r2 = typeof hosts.r2 === "string" ? hosts.r2 : "";
      const d1 = typeof hosts.d1 === "string" ? hosts.d1 : "";
      if (kv || r2 || d1) {
        linksEl.innerHTML = `<span class="muted">No main app URL is configured in this manifest yet. Adapter APIs only: ${escapeHtml(
          [kv, r2, d1].filter(Boolean).join(" · "),
        )}. Add <code>urls</code> and/or <code>infrastructure.routing.entries</code> in <code>leco.yaml</code> to expose a browse URL.</span>`;
      } else {
        linksEl.innerHTML = '<span class="muted">No routes or health URLs in manifest.</span>';
      }
    } else {
      linksEl.innerHTML = parts.join(" · ");
    }
  }

  const agg = snap.aggregate;
  const unregisterHintEl = document.getElementById("hostedAppsUnregisterHint");
  const rs = agg != null && snap.ok ? Number(agg.running_services) : NaN;
  const muUnreg = snap.manifest_ui || {};
  const expectsComposeStack = muUnreg.effective_has_docker_compose === true;
  const stackLooksDown =
    app &&
    expectsComposeStack &&
    (rt.running === false || (Number.isFinite(rs) && rs === 0));
  if (unregisterHintEl) {
    if (app && app.pending_registration === true) {
      if (stackLooksDown) {
        unregisterHintEl.classList.remove("is-hidden");
        unregisterHintEl.innerHTML = `<div class="hosted-apps-unregister-hint__inner hosted-apps-unregister-hint__inner--staging">
        <strong>Staging — not in <code>config/leco-registry.yaml</code> yet.</strong>
        Edit YAML under <code>hosting/app-available/</code>, use <strong>Validate YAML &amp; paths</strong> above, then <strong>Register application</strong> (or the wizard below / <code>leco-devops ecosystem-register</code>).
      </div>`;
      } else {
        unregisterHintEl.classList.add("is-hidden");
        unregisterHintEl.innerHTML = "";
      }
    } else if (stackLooksDown) {
      unregisterHintEl.classList.remove("is-hidden");
      unregisterHintEl.innerHTML = `<div class="hosted-apps-unregister-hint__inner">
        <strong>Stack is down, but this app is still registered.</strong>
        The sidebar lists <code>config/leco-registry.yaml</code> — stopping or removing containers does not remove that entry.
        Traefik may still have routes to old service names, which often shows as <strong>Bad Gateway</strong>.
        Use <strong>Remove from ecosystem</strong> (Control token) to unregister and strip manifest-derived Traefik keys, or run
        <code>leco-devops ecosystem-unregister ${escapeHtml(slug)} --ecosystem-root …</code>.
        If routes were added manually to <code>hosting/traefik/dynamic.yml</code>, edit that file (Traefik reloads it via file watch; restart Traefik only if you changed <code>traefik-static.yaml</code> or mounts).
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

  renderHostedManifestSummary(snap.manifest_ui, snap);
  renderHostedResourceLedger(snap.manifest_ui, snap);
  renderHostedAttachedServices(snap);
  hostedLastDetailSnap = snap;
  renderHostedSeedData(snap, slug);
  bindHostedDataImportButtons();
  renderHostedLocalProfile(snap.manifest_ui, snap);
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

  if (controlsEl) {
    const SB = serviceBrandUi();
    if (!app || !slug) {
      controlsEl.innerHTML =
        '<p class="muted small">No app selected or registry list is out of date — use <strong>Refresh</strong> in the sidebar.</p>';
    } else {
      const stackTargetId =
        typeof app.target_id === "string" && app.target_id.startsWith("leco-stack-")
          ? app.target_id
          : `leco-stack-${slug}`;
      const target = {
        id: stackTargetId,
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
  const urlsTbody = document.getElementById("hostedRegUrlsTbody");
  /** Path for which the last Detect succeeded (step 1 “done” until path changes). */
  let hostedRegDetectOkForPath = "";
  let hostedRegLastDetectData = null;
  /** Step 4 (Validate YAML) — set true after a successful validate-yaml call. */
  let hostedRegValidateOk = false;

  function hostedRegResetValidateStep() {
    hostedRegValidateOk = false;
  }
  if (!detectBtn || !submitBtn || !pathIn || !urlsTbody) return;

  const hostedRegUrlRoles = [
    "frontend",
    "api",
    "admin",
    "backend",
    "cdn",
    "websocket",
    "storybook",
    "graphql",
    "other",
  ];

  function hostedRegClearUrlRows() {
    urlsTbody.innerHTML = "";
  }

  function hostedRegAppendUrlRow(row) {
    const roleRaw = String(row.role || "other").trim() || "other";
    const label = row.label != null ? String(row.label) : "";
    const publicUrl = row.public_url != null ? String(row.public_url) : "";
    const tr = document.createElement("tr");
    tr.dataset.hostedRegUrlRow = "1";
    const sel = document.createElement("select");
    sel.setAttribute("aria-label", "URL role");
    const addRoleOpt = (value, selected) => {
      const o = document.createElement("option");
      o.value = value;
      o.textContent = value;
      if (selected) o.selected = true;
      sel.appendChild(o);
    };
    if (!hostedRegUrlRoles.includes(roleRaw)) {
      addRoleOpt(roleRaw, true);
    }
    hostedRegUrlRoles.forEach((r) => {
      addRoleOpt(r, hostedRegUrlRoles.includes(roleRaw) && r === roleRaw);
    });
    const labelIn = document.createElement("input");
    labelIn.type = "text";
    labelIn.value = label;
    labelIn.placeholder = "e.g. Main app (HTTPS)";
    labelIn.setAttribute("aria-label", "URL label");
    const urlIn = document.createElement("input");
    urlIn.type = "text";
    urlIn.value = publicUrl;
    urlIn.placeholder = "https://app.lh or https://app.lh/api/…";
    urlIn.setAttribute("aria-label", "Public URL");
    const rm = document.createElement("button");
    rm.type = "button";
    rm.className = "hosted-reg-urls__remove";
    rm.textContent = "Remove";
    rm.dataset.hostedRegUrlRemove = "1";
    const td0 = document.createElement("td");
    td0.appendChild(sel);
    const td1 = document.createElement("td");
    td1.appendChild(labelIn);
    const td2 = document.createElement("td");
    td2.appendChild(urlIn);
    const td3 = document.createElement("td");
    td3.appendChild(rm);
    tr.appendChild(td0);
    tr.appendChild(td1);
    tr.appendChild(td2);
    tr.appendChild(td3);
    urlsTbody.appendChild(tr);
  }

  function hostedRegRenderUrlRows(rows) {
    hostedRegClearUrlRows();
    (rows || []).forEach((r) => hostedRegAppendUrlRow(r));
  }

  function hostedRegCollectUrlRows() {
    const out = [];
    urlsTbody.querySelectorAll("tr[data-hosted-reg-url-row]").forEach((tr) => {
      const sel = tr.querySelector("select");
      const textInputs = tr.querySelectorAll('input[type="text"]');
      const labelIn = textInputs[0];
      const urlIn = textInputs[1];
      const public_url = urlIn && String(urlIn.value || "").trim();
      if (!public_url) return;
      out.push({
        role: sel ? String(sel.value || "other").trim() || "other" : "other",
        label: labelIn ? String(labelIn.value || "").trim() : "",
        public_url,
      });
    });
    return out;
  }

  function hostedRegHostSlugFromAppId(appId) {
    const raw = String(appId || "").trim().toLowerCase();
    let s = raw.replace(/[._]/g, "-").replace(/[^a-z0-9-]+/g, "-");
    s = s.replace(/-{2,}/g, "-").replace(/^-+|-+$/g, "");
    return s || "app";
  }

  function hostedRegSuggestedUrlOverrides(appId) {
    const detectHost = String(hostedRegLastDetectData?.main_url_host_slug || "").trim();
    const host = detectHost || hostedRegHostSlugFromAppId(appId);
    return [
      { role: "frontend", label: "Main app (HTTPS)", public_url: `https://${host}.lh` },
      { role: "frontend", label: "Main app (HTTP)", public_url: `http://${host}.lh` },
      { role: "api", label: "API (HTTPS)", public_url: `https://${host}.lh/api` },
      { role: "api", label: "API (HTTP)", public_url: `http://${host}.lh/api` },
    ];
  }

  function hostedRegFillSuggestedRows(appId) {
    hostedRegRenderUrlRows(hostedRegSuggestedUrlOverrides(appId));
  }

  async function hostedRegSyncUrlsFromLocTa(appIdForFallback, silent) {
    const raw = locTa && String(locTa.value || "").trim();
    const fallbackId =
      appIdForFallback || (idIn && idIn.value.trim()) || "";
    if (!raw) {
      hostedRegFillSuggestedRows(fallbackId);
      return;
    }
    try {
      const res = await fetch("/api/leco/extract-localhost-urls", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ localhost_yaml: locTa.value }),
        cache: "no-store",
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        if (!silent) setMsg(data.error || "Could not parse URLs from profile YAML.", true);
        return;
      }
      const rows = Array.isArray(data.urls) ? data.urls : [];
      if (rows.length) hostedRegRenderUrlRows(rows);
      else hostedRegFillSuggestedRows(fallbackId);
    } catch (e) {
      if (!silent) setMsg(String(e.message || e), true);
    }
  }

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
    markDone(3, hostedRegValidateOk);
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
      hint = "Steps 2–3: Generate YAML from the scan and/or Save YAML to disk; edit Public URLs above.";
    else if (!hostedRegValidateOk)
      hint = "Step 4: click Validate YAML (schema + wrangler/compose gap checks).";
    else if (tokReq && !tokOk) hint = "Set the control token on the Control tab to enable Register.";
    else hint = "Step 5: Register applies Public URLs to the profile, then ecosystem-register (and optional deploy).";

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
      return null;
    }
    try {
      const res = await fetch("/api/leco/yaml-status", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, app_id }),
        cache: "no-store",
      });
      const data = await res.json();
      if (data.ok) {
        applyRegistrationYamlStatus(data);
        return data;
      }
      applyRegistrationYamlStatus(null);
      return null;
    } catch (_) {
      applyRegistrationYamlStatus(null);
      return null;
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
    regPanel?.querySelectorAll("#hostedRegUrlsBlock .hosted-reg-urls__toolbar button").forEach((b) => {
      b.disabled = !!on;
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
    hostedRegLastDetectData = null;
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
    hostedRegClearUrlRows();
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
      let data = {};
      try {
        data = await res.json();
      } catch (_) {
        const raw = await res.text().catch(() => "");
        data = {
          ok: false,
          error:
            raw && raw.trim().startsWith("<!doctype")
              ? "Detect returned HTML instead of JSON (likely backend error or wrong host). Open LEco DevOps on port 8090 and retry."
              : raw || `HTTP ${res.status}`,
        };
      }
      if (!data.ok) {
        hostedRegDetectOkForPath = "";
        hostedRegLastDetectData = null;
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
      hostedRegLastDetectData = data;
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
      const aidAfter =
        idIn && idIn.value.trim() ? idIn.value.trim() : previewId || "";
      await hostedRegSyncUrlsFromLocTa(aidAfter, true);
      await refreshYamlStatus();
      const seedHint = (() => {
        const sd = data.seed_data;
        if (sd && sd.present) {
          const n = Number(sd.item_count) || 0;
          return ` Seed data/ detected (${n} import step${n === 1 ? "" : "s"}) — import manually after deploy.`;
        }
        if (sd && !sd.present) {
          return " Optional: add data/ under hosting for seed dumps (import after deploy).";
        }
        return "";
      })();
      if (how && how.fromBrowse) {
        setMsg(
          (dashboardTokenRequired()
            ? "Detect ok — use Generate YAML or Save YAML, then Register (control token)."
            : "Detect ok — use Generate YAML or Save YAML, then Register.") + seedHint,
        );
      } else {
        let base =
          dashboardTokenRequired()
            ? "Detect ok — Generate YAML writes files from scan; Save YAML persists edits; Register needs both files on disk (control token)."
            : "Detect ok — Generate YAML writes files from scan; Save YAML persists edits; Register needs both files on disk.";
        setMsg(base + seedHint);
      }
    } catch (e) {
      hostedRegDetectOkForPath = "";
      hostedRegLastDetectData = null;
      setMsg(String(e.message || e), true);
    } finally {
      setRegFormBusy(false);
    }
  }

  initHostedBrowseModal(pathIn, setMsg, async () => {
    await runHostedRegisterDetect({ fromBrowse: true });
    // Trigger AI analysis after browse+detect if toggle is on
    if (typeof window._runAiAnalysis === "function") {
      window._runAiAnalysis();
    }
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

  document.getElementById("hostedRegUrlsSuggested")?.addEventListener("click", () => {
    hostedRegFillSuggestedRows((idIn && idIn.value.trim()) || "");
    setMsg("Filled Public URLs with suggested defaults — edit or add rows as needed.", false);
  });
  document.getElementById("hostedRegUrlsFromYaml")?.addEventListener("click", () => {
    void hostedRegSyncUrlsFromLocTa((idIn && idIn.value.trim()) || "", false);
  });
  document.getElementById("hostedRegUrlsToYaml")?.addEventListener("click", async () => {
    if (!locTa) return;
    if (!locTa.value.trim()) {
      setMsg("Profile YAML textarea is empty — Generate YAML or paste leco.yaml first.", true);
      return;
    }
    const rows = hostedRegCollectUrlRows();
    if (!rows.length) {
      setMsg("Add at least one URL row with a non-empty public URL.", true);
      return;
    }
    setRegFormBusy(true, "Merging URLs into profile YAML…");
    try {
      const res = await fetch("/api/leco/merge-localhost-urls", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ localhost_yaml: locTa.value, urls: rows }),
        cache: "no-store",
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        setMsg(data.error || `HTTP ${res.status}`, true);
        return;
      }
      locTa.value = data.localhost_yaml;
      setMsg("URLs written into the profile YAML textarea — click Save YAML to persist.", false);
    } catch (e) {
      setMsg(String(e.message || e), true);
    } finally {
      setRegFormBusy(false);
    }
  });
  document.getElementById("hostedRegUrlsAddRow")?.addEventListener("click", () => {
    hostedRegAppendUrlRow({ role: "other", label: "", public_url: "" });
  });
  urlsTbody.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-hosted-reg-url-remove]");
    if (!btn) return;
    const tr = btn.closest("tr");
    if (tr) tr.remove();
  });

  async function tryAutoDetectFromPathField() {
    const loadExisting = loadExistingChk ? loadExistingChk.checked : true;
    if (!loadExisting) return;
    const p = pathIn.value.trim();
    if (!p) return;
    if ((manTa && manTa.value.trim()) || (locTa && locTa.value.trim())) return;
    await runHostedRegisterDetect({});
    // Trigger AI analysis on auto-detect if toggle is on
    if (typeof window._runAiAnalysis === "function") {
      window._runAiAnalysis();
    }
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

  /* ── Slug → URL auto-sync ───────────────────────────────────────────
   * When the user edits the App id (slug), rewrite the hostname portion
   * of every Public URL row so the URLs stay in sync.  If no URL rows
   * exist yet, populate them with the suggested defaults.
   *
   * Instead of tracking a "previous slug" variable (which gets out of
   * sync when Detect or Load-from-YAML fills URLs with a different
   * hostname), we extract the *actual* hostname from the first URL row
   * each time the slug changes.
   * ─────────────────────────────────────────────────────────────────── */
  function _extractHostFromUrlRows() {
    const firstUrl = urlsTbody?.querySelector("tr[data-hosted-reg-url-row] input[aria-label='Public URL']");
    if (!firstUrl || !firstUrl.value) return "";
    const m = firstUrl.value.match(/https?:\/\/([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)\.lh/);
    return m ? m[1] : "";
  }

  idIn?.addEventListener("input", () => {
    if (yamlStatusDebounce) clearTimeout(yamlStatusDebounce);
    yamlStatusDebounce = setTimeout(() => refreshYamlStatus(), 400);

    const newId = (idIn.value || "").trim();
    const newHost = hostedRegHostSlugFromAppId(newId);
    if (!newHost) return;

    const rows = urlsTbody?.querySelectorAll("tr[data-hosted-reg-url-row]");
    if (!rows || rows.length === 0) {
      /* No rows yet — fill the defaults. */
      if (newId) hostedRegFillSuggestedRows(newId);
      return;
    }

    /* Read the hostname that is *actually* in the URL fields right now. */
    const curHost = _extractHostFromUrlRows();
    if (!curHost || curHost === newHost) return;

    /* Rewrite hostnames inside every URL value. */
    const oldPat = new RegExp(
      "(https?://)(" + curHost.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")\\.lh",
      "g",
    );
    rows.forEach((tr) => {
      const urlIn = tr.querySelectorAll('input[type="text"]')[1];
      if (!urlIn) return;
      const cur = urlIn.value || "";
      if (cur) urlIn.value = cur.replace(oldPat, "$1" + newHost + ".lh");
    });
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
      if (urlsTbody && !urlsTbody.children.length && idIn && idIn.value.trim()) {
        hostedRegFillSuggestedRows(idIn.value.trim());
      }
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
    void hostedRegSyncUrlsFromLocTa((idIn && idIn.value.trim()) || "", false);
  });

  pathIn?.addEventListener("input", () => {
    hostedRegResetValidateStep();
    hostedRegDetectOkForPath = "";
  });
  manTa?.addEventListener("input", hostedRegResetValidateStep);
  locTa?.addEventListener("input", hostedRegResetValidateStep);

  detectBtn.addEventListener("click", async () => {
    hostedRegResetValidateStep();
    await runHostedRegisterDetect({});
    // Trigger AI analysis after detect completes, if toggle is on
    if (typeof window._runAiAnalysis === "function") {
      window._runAiAnalysis();
    }
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
    hostedRegResetValidateStep();
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
      await hostedRegSyncUrlsFromLocTa(app_id, true);
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
        path: pathIn ? pathIn.value.trim() : "",
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
      const infraWarn = Array.isArray(data.infrastructure_warnings) ? data.infrastructure_warnings : [];
      hostedRegValidateOk = pass;
      const jsonBlock = JSON.stringify(data.report || {}, null, 2);
      yamlRep.textContent = `${data.summary_text || ""}\n\n— structured report —\n${jsonBlock}`;
      yamlRep.classList.remove("is-hidden");
      yamlRep.classList.toggle("hosted-reg-yaml-report--pass", pass && !infraWarn.length);
      yamlRep.classList.toggle("hosted-reg-yaml-report--fail", !pass || infraWarn.length > 0);
      if (!pass) {
        setMsg("Validation failed — see report below.", true);
      } else if (infraWarn.length) {
        setMsg(
          "Schema OK — infrastructure warnings (wrangler on disk but empty leco.yaml). Re-run Generate YAML after restarting service-dashboard, or edit runtimes manually.",
          true,
        );
      } else {
        setMsg("Validation passed — see report below.");
      }
      const st = await refreshYamlStatus();
      syncHostedRegWorkflow(st, false, {});
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
    const deployStack = deployChk ? !!deployChk.checked : true;
    const urlOverrides = hostedRegCollectUrlRows();
    if (!urlOverrides.length) {
      setMsg(
        "Add at least one public URL in the Public URLs table (or click Use suggested after setting app id).",
        true,
      );
      return;
    }
    const regBody = {
      path,
      app_id,
      label,
      deploy_stack: deployStack,
      url_overrides: urlOverrides,
    };
    setRegFormBusy(true, "Registering…");
    try {
      const out = await runDashboardSyncRegisterOverlay({
        body: regBody,
        title: `Register · ${app_id}`,
        actionVerb: "register",
        async onFinally() {
          await syncHostedAppsAfterRegistryMutation({
            message: "Registered app. Refreshing hosted apps…",
          });
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

function initHostedAppsStagingActions() {
  const regBtn = document.getElementById("hostedAppsQuickRegister");
  const valBtn = document.getElementById("hostedAppsValidateConfig");
  const valPre = document.getElementById("hostedAppsConfigValidation");
  if (!regBtn || !valBtn || !valPre) return;
  if (regBtn.dataset.wired === "1") return;
  regBtn.dataset.wired = "1";
  valBtn.dataset.wired = "1";

  valBtn.addEventListener("click", async () => {
    const s = hostedSelectedSlug;
    if (!s) return;
    const ap = hostedAppsList.find((x) => x.id === s);
    if (!ap || ap.pending_registration !== true) return;
    valPre.classList.remove("hosted-apps-config-validation--pass", "hosted-apps-config-validation--fail");
    valPre.textContent = "Validating…";
    valPre.classList.remove("is-hidden");
    try {
      const res = await fetch(`/api/hosted-apps/${encodeURIComponent(s)}/validate-configuration`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        valPre.textContent = data.summary_text || data.error || `Request failed (${res.status})`;
        valPre.classList.add("hosted-apps-config-validation--fail");
        return;
      }
      valPre.textContent = data.summary_text || JSON.stringify(data, null, 2);
      valPre.classList.toggle("hosted-apps-config-validation--pass", !!data.validation_ok);
      valPre.classList.toggle("hosted-apps-config-validation--fail", !data.validation_ok);
    } catch (e) {
      valPre.textContent = String(e.message || e);
      valPre.classList.add("hosted-apps-config-validation--fail");
    }
  });

  regBtn.addEventListener("click", async () => {
    const s = hostedSelectedSlug;
    if (!s) return;
    const ap = hostedAppsList.find((x) => x.id === s);
    if (!ap || ap.pending_registration !== true) return;
    const tok = controlToken();
    if (dashboardTokenRequired() && !tok) {
      valPre.classList.remove("is-hidden", "hosted-apps-config-validation--pass");
      valPre.classList.add("hosted-apps-config-validation--fail");
      valPre.textContent =
        "Control token required — open the Control tab and save the token before registering.";
      return;
    }
    const path = String(ap.registration_path || `hosting/app-available/${s}`).trim();
    const label = String(ap.label || s).trim();
    valPre.classList.add("is-hidden");
    await runDashboardSyncRegisterOverlay({
      body: { path, app_id: s, label, deploy_stack: true },
      title: `Register · ${s}`,
      actionVerb: "register",
      async onFinally() {
        await syncHostedAppsAfterRegistryMutation({
          message: "Registered app. Refreshing hosted apps…",
        });
        loadOverview().catch(() => {});
      },
    });
  });
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
  document.getElementById("routeBuilderBuildBtn")?.addEventListener("click", () => routeBuilderBuildYaml(false));
  document.getElementById("routeBuilderMergeBtn")?.addEventListener("click", () => routeBuilderBuildYaml(true));
  document.getElementById("routeBuilderResetBtn")?.addEventListener("click", () => routeBuilderResetForm());
  document.getElementById("routeBuilderMode")?.addEventListener("change", () => routeBuilderSyncModeUi());
  routeBuilderSyncModeUi();
}

function routeBuilderMsg(text) {
  const msg = document.getElementById("traefikRoutesMsg");
  if (msg) msg.textContent = text || "";
}

function routeBuilderInput(id) {
  return document.getElementById(id);
}

function routeBuilderNormalizeHost(raw) {
  let s = String(raw || "").trim();
  if (!s) return "";
  s = s.replace(/^https?:\/\//i, "");
  const slash = s.indexOf("/");
  if (slash >= 0) s = s.slice(0, slash);
  return s.trim();
}

function routeBuilderNormalizePathPrefix(raw) {
  const s = String(raw || "").trim();
  if (!s) return "";
  if (s === "/") return "/";
  return s.startsWith("/") ? s : `/${s}`;
}

function routeBuilderSyncModeUi() {
  const mode = routeBuilderInput("routeBuilderMode")?.value || "proxy";
  const proxyDisabled = mode !== "proxy";
  const redirectDisabled = mode !== "redirect";
  const proxyFields = ["routeBuilderServiceKey", "routeBuilderEndpointUrl"];
  const redirectFields = ["routeBuilderRedirectUrl", "routeBuilderRedirectStatus"];
  proxyFields.forEach((id) => {
    const el = routeBuilderInput(id);
    if (el) el.disabled = proxyDisabled;
  });
  redirectFields.forEach((id) => {
    const el = routeBuilderInput(id);
    if (el) el.disabled = redirectDisabled;
  });
}

function routeBuilderResetForm() {
  const defaults = {
    routeBuilderMode: "proxy",
    routeBuilderRouterKey: "",
    routeBuilderHost: "",
    routeBuilderPathPrefix: "",
    routeBuilderServiceKey: "",
    routeBuilderEndpointUrl: "",
    routeBuilderRedirectUrl: "",
    routeBuilderRedirectStatus: "301",
    routeBuilderPriority: "",
  };
  Object.entries(defaults).forEach(([id, v]) => {
    const el = routeBuilderInput(id);
    if (el) el.value = v;
  });
  const web = routeBuilderInput("routeBuilderEntryWeb");
  const websecure = routeBuilderInput("routeBuilderEntryWebsecure");
  const tls = routeBuilderInput("routeBuilderTls");
  if (web) web.checked = true;
  if (websecure) websecure.checked = true;
  if (tls) tls.checked = true;
  routeBuilderSyncModeUi();
  routeBuilderMsg("Route builder form reset.");
}

function routeBuilderParseRule(ruleText) {
  const out = { host: "", pathPrefix: "" };
  const rule = String(ruleText || "");
  const hostM = rule.match(/Host\(`([^`]+)`\)/);
  const pathM = rule.match(/PathPrefix\(`([^`]+)`\)/);
  if (hostM) out.host = hostM[1] || "";
  if (pathM) out.pathPrefix = pathM[1] || "";
  return out;
}

function routeBuilderToYamlText(opts) {
  const mode = opts.mode;
  const routerKey = opts.routerKey;
  const host = opts.host;
  const pathPrefix = opts.pathPrefix;
  const serviceKey = opts.serviceKey;
  const endpointUrl = opts.endpointUrl;
  const redirectUrl = opts.redirectUrl;
  const redirectStatus = opts.redirectStatus;
  const entryPoints = opts.entryPoints;
  const tlsOn = !!opts.tlsOn;
  const priority = opts.priority;

  const lines = [];
  lines.push("http:");
  lines.push("  routers:");
  lines.push(`    ${routerKey}:`);
  const rule = pathPrefix
    ? `Host(\`${host}\`) && PathPrefix(\`${pathPrefix}\`)`
    : `Host(\`${host}\`)`;
  lines.push(`      rule: "${rule}"`);
  if (mode === "redirect") {
    lines.push("      service: noop@internal");
    const mwKey = `${routerKey}-redirect`;
    lines.push("      middlewares:");
    lines.push(`        - ${mwKey}`);
  } else {
    lines.push(`      service: ${serviceKey}`);
  }
  lines.push("      entryPoints:");
  entryPoints.forEach((ep) => lines.push(`        - ${ep}`));
  if (tlsOn) lines.push("      tls: true");
  if (priority != null) lines.push(`      priority: ${priority}`);
  if (mode === "redirect") {
    const mwKey = `${routerKey}-redirect`;
    const permanent = String(redirectStatus || "301") === "301";
    const regexHost = host.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    lines.push("  middlewares:");
    lines.push(`    ${mwKey}:`);
    lines.push("      redirectRegex:");
    lines.push(`        regex: "^https?://${regexHost}(?:/.*)?$"`);
    lines.push(`        replacement: "${redirectUrl}"`);
    lines.push(`        permanent: ${permanent ? "true" : "false"}`);
  } else {
    lines.push("  services:");
    lines.push(`    ${serviceKey}:`);
    lines.push("      loadBalancer:");
    lines.push("        servers:");
    lines.push(`          - url: "${endpointUrl}"`);
  }
  return lines.join("\n");
}

function routeBuilderCollectForm() {
  const mode = routeBuilderInput("routeBuilderMode")?.value || "proxy";
  const routerKey = String(routeBuilderInput("routeBuilderRouterKey")?.value || "").trim();
  const host = routeBuilderNormalizeHost(routeBuilderInput("routeBuilderHost")?.value || "");
  const pathPrefix = routeBuilderNormalizePathPrefix(routeBuilderInput("routeBuilderPathPrefix")?.value || "");
  const serviceKey = String(routeBuilderInput("routeBuilderServiceKey")?.value || "").trim();
  const endpointUrl = String(routeBuilderInput("routeBuilderEndpointUrl")?.value || "").trim();
  const redirectUrl = String(routeBuilderInput("routeBuilderRedirectUrl")?.value || "").trim();
  const redirectStatus = String(routeBuilderInput("routeBuilderRedirectStatus")?.value || "301");
  const priorityRaw = String(routeBuilderInput("routeBuilderPriority")?.value || "").trim();
  const priority = priorityRaw ? parseInt(priorityRaw, 10) : null;
  const entryPoints = [];
  if (routeBuilderInput("routeBuilderEntryWeb")?.checked) entryPoints.push("web");
  if (routeBuilderInput("routeBuilderEntryWebsecure")?.checked) entryPoints.push("websecure");
  const tlsOn = !!routeBuilderInput("routeBuilderTls")?.checked;
  return {
    mode,
    routerKey,
    host,
    pathPrefix,
    serviceKey,
    endpointUrl,
    redirectUrl,
    redirectStatus,
    priority,
    entryPoints,
    tlsOn,
  };
}

function routeBuilderValidateForm(d) {
  if (!d.routerKey) return "Router key is required.";
  if (!d.host) return "Public host is required.";
  if (!d.entryPoints.length) return "Select at least one entryPoint.";
  if (d.mode === "proxy") {
    if (!d.serviceKey) return "Service key is required in proxy mode.";
    if (!d.endpointUrl) return "Endpoint URL is required in proxy mode.";
  } else if (!d.redirectUrl) {
    return "Redirect URL is required in redirect mode.";
  }
  return "";
}

async function routeBuilderBuildYaml(alsoMerge) {
  const ta = document.getElementById("traefikMergeYaml");
  if (!ta) return;
  const d = routeBuilderCollectForm();
  const err = routeBuilderValidateForm(d);
  if (err) {
    routeBuilderMsg(err);
    return;
  }
  const yamlText = routeBuilderToYamlText(d);
  ta.value = yamlText;
  if (!alsoMerge) {
    routeBuilderMsg("YAML fragment built from form. Review and merge when ready.");
    return;
  }
  routeBuilderMsg("Built YAML from form; merging into dynamic.yml…");
  await traefikMergeFragment();
}

function routeBuilderEditRouter(routerKey) {
  const r = routePanelState.routersByKey.get(routerKey);
  if (!r) {
    routeBuilderMsg(`Router not found: ${routerKey}`);
    return;
  }
  const parsed = routeBuilderParseRule(r.rule);
  const modeIn = routeBuilderInput("routeBuilderMode");
  if (modeIn) modeIn.value = "proxy";
  const routerIn = routeBuilderInput("routeBuilderRouterKey");
  const hostIn = routeBuilderInput("routeBuilderHost");
  const pathIn = routeBuilderInput("routeBuilderPathPrefix");
  const svcIn = routeBuilderInput("routeBuilderServiceKey");
  const epIn = routeBuilderInput("routeBuilderEndpointUrl");
  const priIn = routeBuilderInput("routeBuilderPriority");
  const tlsIn = routeBuilderInput("routeBuilderTls");
  if (routerIn) routerIn.value = r.key || "";
  if (hostIn) hostIn.value = parsed.host || "";
  if (pathIn) pathIn.value = parsed.pathPrefix || "";
  if (svcIn) svcIn.value = r.service || "";
  const svc = routePanelState.servicesByKey.get(r.service || "");
  if (epIn) epIn.value = (svc?.urls && svc.urls[0]) || "";
  if (priIn) priIn.value = r.priority != null ? String(r.priority) : "";
  if (tlsIn) tlsIn.checked = !!r.tls;
  const eps = Array.isArray(r.entryPoints) ? r.entryPoints : [];
  const web = routeBuilderInput("routeBuilderEntryWeb");
  const websecure = routeBuilderInput("routeBuilderEntryWebsecure");
  if (web) web.checked = eps.includes("web");
  if (websecure) websecure.checked = eps.includes("websecure");
  routeBuilderSyncModeUi();
  routeBuilderMsg(`Loaded router ${r.key} into route builder. Adjust values and merge.`);
}

function routeBuilderEditService(serviceKey) {
  const s = routePanelState.servicesByKey.get(serviceKey);
  if (!s) {
    routeBuilderMsg(`Service not found: ${serviceKey}`);
    return;
  }
  const modeIn = routeBuilderInput("routeBuilderMode");
  if (modeIn) modeIn.value = "proxy";
  const svcIn = routeBuilderInput("routeBuilderServiceKey");
  const epIn = routeBuilderInput("routeBuilderEndpointUrl");
  if (svcIn) svcIn.value = s.key || "";
  if (epIn) epIn.value = (s.urls && s.urls[0]) || "";
  routeBuilderSyncModeUi();
  routeBuilderMsg(`Loaded service ${s.key}. Select a host/router key to complete the route.`);
}

async function routeBuilderDeleteKeys(routerKeys, serviceKeys, label) {
  const msg = document.getElementById("traefikRoutesMsg");
  const tok = controlToken();
  if (dashboardTokenRequired() && !tok) {
    if (msg) msg.textContent = "Set the control token on the Control tab.";
    return;
  }
  const ok = await showAppConfirm({
    title: `Delete ${label}`,
    message: "This removes keys from hosting/traefik/dynamic.yml (atomic write). Continue?",
    confirmText: "Delete",
  });
  if (!ok) return;
  try {
    const res = await fetch("/api/traefik/strip-keys", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Control-Token": tok,
      },
      body: JSON.stringify({
        routers: routerKeys || [],
        services: serviceKeys || [],
        token: tok,
      }),
    });
    const data = await res.json();
    if (!data.ok) {
      if (msg) msg.textContent = data.error || `HTTP ${res.status}`;
      return;
    }
    if (msg) {
      msg.textContent = `Deleted ${label}: routers ${data.routers_removed || 0}, services ${data.services_removed || 0}.`;
    }
    await loadTraefikRoutesPanel();
  } catch (e) {
    if (msg) msg.textContent = String(e.message || e);
  }
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
    routePanelState.routersByKey.clear();
    routePanelState.servicesByKey.clear();
    routers.forEach((r) => {
      if (r && r.key) routePanelState.routersByKey.set(r.key, r);
    });
    rb.innerHTML = routers.length
      ? routers
          .map(
            (r) =>
              `<tr><td><code>${escapeHtml(r.key)}</code></td><td>${escapeHtml(String(r.rule || "—"))}</td><td><code>${escapeHtml(String(r.service || "—"))}</code></td><td>${escapeHtml((r.entryPoints || []).join(", ") || "—")}</td><td>${r.tls ? "yes" : "—"}</td><td><div class="routes-actions"><button type="button" class="ctrl-act ctrl-act--ops" data-route-edit="${escapeAttr(r.key)}">Edit</button><button type="button" class="ctrl-act danger ctrl-act--destructive" data-route-del="${escapeAttr(r.key)}">Delete</button></div></td></tr>`,
          )
          .join("")
      : `<tr><td colspan="6" class="muted">No routers</td></tr>`;
    const services = data.services || [];
    services.forEach((s) => {
      if (s && s.key) routePanelState.servicesByKey.set(s.key, s);
    });
    sb.innerHTML = services.length
      ? services
          .map((s) => {
            const urls = (s.urls || []).join(", ") || "—";
            return `<tr><td><code>${escapeHtml(s.key)}</code></td><td>${escapeHtml(urls)}</td><td><div class="routes-actions"><button type="button" class="ctrl-act ctrl-act--ops" data-route-svc-edit="${escapeAttr(s.key)}">Edit</button><button type="button" class="ctrl-act danger ctrl-act--destructive" data-route-svc-del="${escapeAttr(s.key)}">Delete</button></div></td></tr>`;
          })
          .join("")
      : `<tr><td colspan="3" class="muted">No services</td></tr>`;

    rb.querySelectorAll("[data-route-edit]").forEach((btn) => {
      btn.addEventListener("click", () => routeBuilderEditRouter(btn.getAttribute("data-route-edit") || ""));
    });
    rb.querySelectorAll("[data-route-del]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.getAttribute("data-route-del") || "";
        if (key) routeBuilderDeleteKeys([key], [], `router ${key}`);
      });
    });
    sb.querySelectorAll("[data-route-svc-edit]").forEach((btn) => {
      btn.addEventListener("click", () => routeBuilderEditService(btn.getAttribute("data-route-svc-edit") || ""));
    });
    sb.querySelectorAll("[data-route-svc-del]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const key = btn.getAttribute("data-route-svc-del") || "";
        if (key) routeBuilderDeleteKeys([], [key], `service ${key}`);
      });
    });

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
    rb.innerHTML = `<tr><td colspan="6">${escapeHtml(e.message || String(e))}</td></tr>`;
  }
}

async function runHostedOffboard(slug, stripTraefik, cleanLocalCf) {
  const ok = await showAppConfirm({
    title: `Remove hosted app ${slug}`,
    message:
      "Runs leco-devops ecosystem-unregister: removes this id from config/leco-registry.yaml, optionally strips Traefik routers/services from hosting/traefik/dynamic.yml, and deletes local KV/R2/D1 resources listed in leco.local-cf.yaml. Traefik picks up dynamic.yml changes via file watch (no Traefik restart).",
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
    await syncHostedAppsAfterRegistryMutation({
      removedSlug: slug,
      message: `Removed ${slug}. Refreshing hosted apps…`,
    });
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

    grid.querySelectorAll("button.control-policy-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const tid = btn.getAttribute("data-policy-target");
        const val = btn.getAttribute("data-policy-value");
        if (!tid || !val) return;
        const body = { policies: {} };
        body.policies[tid] = val;
        const tok = controlToken();
        if (tok) body.token = tok;
        try {
          const res = await fetch("/api/control/default-policies", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
          if (res.ok) {
            const sel = btn.closest(".control-policy-selector");
            if (sel) {
              sel.querySelectorAll(".control-policy-btn").forEach((b) => b.classList.remove("control-policy-btn--active"));
              btn.classList.add("control-policy-btn--active");
            }
            const card = btn.closest(".control-card");
            if (card) {
              card.classList.toggle("control-card--offloaded", val === "offloaded");
              const badge = card.querySelector(".control-policy-badge");
              if (badge) {
                if (val === "start") { badge.remove(); }
                else {
                  badge.textContent = "Default: " + val;
                  badge.className = "control-policy-badge " + (val === "offloaded" ? "control-policy-badge--offloaded" : "control-policy-badge--stop");
                }
              } else if (val !== "start") {
                const h3 = card.querySelector("h3");
                if (h3) h3.insertAdjacentHTML("beforeend", controlPolicyBadgeHtml(val));
              }
            }
          }
        } catch (_) { /* silent */ }
      });
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

/** @type {{ startedAt: number, lastStepAt: number, completedSteps: number }} */
let streamProgressTiming = { startedAt: 0, lastStepAt: 0, completedSteps: 0 };

function resetStreamProgressUI() {
  streamProgressTiming = { startedAt: 0, lastStepAt: 0, completedSteps: 0 };
  const wrap = document.getElementById("controlActionProgress");
  const fill = document.getElementById("controlActionProgressFill");
  const meta = document.getElementById("controlActionProgressMeta");
  if (wrap) {
    wrap.classList.add("is-hidden");
    wrap.setAttribute("aria-hidden", "true");
  }
  if (fill) fill.style.width = "0%";
  if (meta) meta.textContent = "";
}

function formatStreamEta(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `~${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r ? `~${m}m ${r}s` : `~${m}m`;
}

/**
 * @param {{ step?: number, total?: number, label?: string }} ev
 */
function updateStreamProgressUI(ev) {
  const wrap = document.getElementById("controlActionProgress");
  const fill = document.getElementById("controlActionProgressFill");
  const meta = document.getElementById("controlActionProgressMeta");
  const liveEl = document.getElementById("controlActionLive");
  if (!wrap || !fill || !meta) return;

  const step = Number(ev.step);
  const total = Number(ev.total);
  const label = ev.label != null ? String(ev.label) : "";
  if (!Number.isFinite(step) || !Number.isFinite(total) || total <= 0) return;

  const now = Date.now();
  if (!streamProgressTiming.startedAt) streamProgressTiming.startedAt = now;
  if (streamProgressTiming.lastStepAt && step > streamProgressTiming.completedSteps + 1) {
    streamProgressTiming.completedSteps = Math.max(streamProgressTiming.completedSteps, step - 1);
  }
  streamProgressTiming.lastStepAt = now;

  const pct = Math.min(100, Math.max(0, Math.round((step / total) * 100)));
  wrap.classList.remove("is-hidden");
  wrap.setAttribute("aria-hidden", "false");
  fill.style.width = `${pct}%`;

  let etaText = "";
  const doneSteps = Math.max(0, step - 1);
  const elapsedSec = (now - streamProgressTiming.startedAt) / 1000;
  if (doneSteps > 0 && step < total) {
    const avgPerStep = elapsedSec / doneSteps;
    const remaining = total - step + 1;
    etaText = ` · ETA ${formatStreamEta(avgPerStep * remaining)}`;
  } else if (step >= total) {
    etaText = " · finishing";
  }

  meta.textContent = `Step ${step} of ${total} (${pct}%)${label ? ` · ${label}` : ""}${etaText}`;
  if (liveEl) liveEl.textContent = label ? `Running: ${label}` : `Step ${step} of ${total}`;
}

/**
 * @param {ReadableStreamDefaultReader<Uint8Array>} reader
 * @param {(t: string) => void} appendStreamLog
 * @param {(ev: { step?: number, total?: number, label?: string }) => void} [onProgress]
 */
async function readNdjsonLinesFromReader(reader, appendStreamLog, onProgress) {
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
      if (ev.type === "progress") {
        const step = ev.step != null ? ev.step : "?";
        const total = ev.total != null ? ev.total : "?";
        const label = ev.label != null ? ev.label : "";
        if (onProgress) onProgress(ev);
        appendStreamLog(`\n--- [${step}/${total}] ${label} ---\n`);
      }
      if (ev.type === "done") finalResult = ev.result || null;
    }
  }
  if (lineBuf.trim()) {
    try {
      const ev = JSON.parse(lineBuf);
      if (ev.type === "log" && ev.text != null) appendStreamLog(ev.text);
      if (ev.type === "progress") {
        const step = ev.step != null ? ev.step : "?";
        const total = ev.total != null ? ev.total : "?";
        const label = ev.label != null ? ev.label : "";
        if (onProgress) onProgress(ev);
        appendStreamLog(`\n--- [${step}/${total}] ${label} ---\n`);
      }
      if (ev.type === "done") finalResult = ev.result || finalResult;
    } catch (_) {
      /* trailing garbage */
    }
  }
  return finalResult;
}

/** When fetch body has no getReader (older browsers / proxies), parse buffered NDJSON. */
function parseNdjsonFromFullText(text, appendStreamLog, onProgress) {
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
    if (ev.type === "progress") {
      const step = ev.step != null ? ev.step : "?";
      const total = ev.total != null ? ev.total : "?";
      const label = ev.label != null ? ev.label : "";
      if (onProgress) onProgress(ev);
      appendStreamLog(`\n--- [${step}/${total}] ${label} ---\n`);
    }
    if (ev.type === "done") finalResult = ev.result || null;
  }
  return finalResult;
}

/**
 * Shared overlay + NDJSON stream reader (Control actions, Register app, …).
 * @param {{ title: string, url: string, body: Record<string, unknown>, actionVerb?: string, trackProgress?: boolean, onFinally?: (outcome: { ok: boolean, result?: object, error?: string }) => void | Promise<void> }} opts
 * @returns {Promise<{ ok: boolean, result?: object, error?: string }>}
 */
async function runDashboardStreamOverlay(opts) {
  const { title, url, body, actionVerb = "operation", trackProgress = false, onFinally } = opts;
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
    summaryEl.classList.remove("control-action-summary--ok", "control-action-summary--bad", "control-action-summary--warn");
  }
  if (snippetEl) {
    snippetEl.classList.remove("is-hidden");
    snippetEl.classList.add("control-action-snippet--live");
    snippetEl.textContent = "Waiting for output…\n";
  }
  if (trackProgress) resetStreamProgressUI();
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
  const streamTrigger = peekActionTrigger(400);
  const onProgress = trackProgress ? updateStreamProgressUI : undefined;

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Control-Token": tok },
      body: JSON.stringify(payload),
      signal: ac.signal,
      cache: "no-store",
      dashboardTriggerEl: streamTrigger || undefined,
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
          finalResult = await readNdjsonLinesFromReader(res.body.getReader(), appendStreamLog, onProgress);
        } catch (streamErr) {
          appendStreamLog(`\n[ui] stream read failed: ${String(streamErr.message || streamErr)}\n`);
          finalResult = { ok: false, error: String(streamErr.message || streamErr) };
        }
      } else {
        const txt = await res.text();
        finalResult = parseNdjsonFromFullText(txt, appendStreamLog, onProgress);
      }

      const data = finalResult || { ok: false, error: "no result from stream" };
      const fullLog = logChunks.join("");
      const merged = { ...data };
      if (fullLog) merged.log_stream = fullLog;
      const text = JSON.stringify(merged, null, 2);
      lastControlActionResultText = text;
      if (out) out.textContent = text;
      const details = document.querySelector(".control-response-details");

      const ok = data && data.ok === true;
      const infraGapsStream = Array.isArray(data.register_infrastructure_gaps)
        ? data.register_infrastructure_gaps
        : [];
      const composeGapStream =
        data?.effective_has_docker_compose === false || infraGapsStream.some((x) => String(x).includes("dockerCompose"));
      const routingGapStream =
        data?.effective_has_traefik_sources === false ||
        infraGapsStream.some((x) => String(x).includes("routing.entries") || String(x).includes("localCfPublicPrefix"));
      const infraIncompleteStream = ok && infraGapsStream.length > 0;
      if (details && (data?.ok === false || infraIncompleteStream)) details.open = true;

      outcome.ok = ok;
      outcome.result = data;
      if (!ok) outcome.error = data?.error ? String(data.error) : "stream failed";
      if (titleEl) titleEl.textContent = ok ? `Done · ${actionVerb}` : `Finished · ${actionVerb}`;
      if (liveEl) {
        const sec = Math.max(0, Math.round((Date.now() - t0) / 1000));
        const lines = fullLog ? fullLog.split("\n").length : 0;
        if (!ok) liveEl.textContent = `Completed in ${sec}s (check log below)`;
        else if (infraIncompleteStream) {
          if (composeGapStream && !routingGapStream) {
            liveEl.textContent = `Registry updated in ${sec}s (${lines} lines) — routes exist, but no compose stack configured`;
          } else if (!composeGapStream && routingGapStream) {
            liveEl.textContent = `Registry updated in ${sec}s (${lines} lines) — compose is configured, but no app routes are defined`;
          } else {
            liveEl.textContent = `Registry updated in ${sec}s (${lines} lines) — add compose/routing in leco.yaml`;
          }
        }
        else liveEl.textContent = `Succeeded in ${sec}s · ${lines} line(s) of output`;
      }
      if (summaryEl) {
        summaryEl.classList.remove("is-hidden");
        summaryEl.classList.remove("control-action-summary--ok", "control-action-summary--bad", "control-action-summary--warn");
        if (!ok) {
          summaryEl.classList.add("control-action-summary--bad");
          summaryEl.textContent = data.error || "Action reported failure.";
        } else if (infraIncompleteStream) {
          summaryEl.classList.add("control-action-summary--warn");
          if (composeGapStream && !routingGapStream) {
            summaryEl.textContent =
              "App is registered and routing is configured. No dockerCompose stack is configured in leco.yaml, so hosted compose controls/logs remain empty (Worker-only is fine).";
          } else if (!composeGapStream && routingGapStream) {
            summaryEl.textContent =
              "App is registered and compose is configured, but no app routes are defined (routing.entries/localCfPublicPrefix missing). Add routes in leco.yaml.";
          } else {
            summaryEl.textContent =
              "App is in the registry, but leco.yaml has no compose or routing yet — see register_infrastructure_gaps in JSON below.";
          }
        } else {
          summaryEl.classList.add("control-action-summary--ok");
          summaryEl.textContent = "Action succeeded.";
        }
      }
      if (snippetEl && !ok && data.error && !String(fullLog || "").trim()) {
        snippetEl.textContent = String(data.error);
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
    if (trackProgress) {
      const fill = document.getElementById("controlActionProgressFill");
      const meta = document.getElementById("controlActionProgressMeta");
      if (outcome.ok && fill) fill.style.width = "100%";
      if (meta && outcome.ok) meta.textContent = "Complete";
    }
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
 * Runs ecosystem-register + optional leco-devops deploy in one server round-trip.
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
    summaryEl.classList.remove("control-action-summary--ok", "control-action-summary--bad", "control-action-summary--warn");
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
    if (data.deploy_stack_ran && data.deploy_log) parts.push("--- leco-devops deploy ---\n" + String(data.deploy_log));
    const logText =
      parts.length > 0 ? parts.join("\n\n") : data.error ? String(data.error) : JSON.stringify(data, null, 2);
    if (snippetEl) {
      snippetEl.textContent = logText || "(empty response)";
      snippetEl.scrollTop = snippetEl.scrollHeight;
    }

    const registerOk = res.ok && data.ok === true;
    const deployFine = data.deploy_stack_ran !== true || data.deploy_ok === true;
    const ok = registerOk && deployFine;
    /** @param {unknown} deployLog */
    const deployPortConflictHint = (deployLog) => {
      const s = deployLog == null ? "" : String(deployLog);
      if (!/port is already allocated/i.test(s)) return "";
      const pg =
        /:5432\b/.test(s) || /postgresql/i.test(s)
          ? " Database services often publish :5432 on the host — use `ports: !reset []` on that service too (apps still use internal `postgresql:5432`)."
          : "";
      return ` Port conflict: a host port in compose is already in use (e.g. :80 with Traefik, or :5432 with another Postgres).${pg} In hosting-only mode use composeFileFromManifest + include upstream + \`ports: !reset []\` per publishing service (see hosting/samples/sample-hosting-compose-entry/), or free the port on the host.`;
    };
    const infraGaps = Array.isArray(data.register_infrastructure_gaps) ? data.register_infrastructure_gaps : [];
    const composeGap =
      data.effective_has_docker_compose === false || infraGaps.some((x) => String(x).includes("dockerCompose"));
    const routingGap =
      data.effective_has_traefik_sources === false ||
      infraGaps.some((x) => String(x).includes("routing.entries") || String(x).includes("localCfPublicPrefix"));
    const infraIncomplete = ok && infraGaps.length > 0;
    outcome.ok = ok;
    outcome.result = data;
    if (!ok) {
      outcome.error = data.error
        ? String(data.error)
        : !deployFine
          ? "Docker deploy failed (registry may still be updated). See log below." + deployPortConflictHint(data.deploy_log)
          : `HTTP ${res.status}`;
    }

    const text = JSON.stringify(data, null, 2);
    lastControlActionResultText = text;
    if (out) out.textContent = text;
    const details = document.querySelector(".control-response-details");
    if (details && (!ok || infraIncomplete)) details.open = true;
    if (titleEl) titleEl.textContent = ok ? `Done · ${actionVerb}` : `Finished · ${actionVerb}`;
    if (liveEl) {
      const sec = Math.max(0, Math.round((Date.now() - t0) / 1000));
      if (!ok) liveEl.textContent = `Completed in ${sec}s (see log)`;
      else if (infraIncomplete) {
        if (composeGap && !routingGap) liveEl.textContent = `Registry updated in ${sec}s — routes are ready, compose stack not configured`;
        else if (!composeGap && routingGap) liveEl.textContent = `Registry updated in ${sec}s — compose ready, app routes missing`;
        else liveEl.textContent = `Registry updated in ${sec}s — infrastructure still empty`;
      }
      else liveEl.textContent = `Succeeded in ${sec}s`;
    }
    if (summaryEl) {
      summaryEl.classList.remove("is-hidden");
      summaryEl.classList.remove("control-action-summary--ok", "control-action-summary--bad", "control-action-summary--warn");
      if (!ok) {
        summaryEl.classList.add("control-action-summary--bad");
        summaryEl.textContent = outcome.error || "Request failed.";
      } else if (infraIncomplete) {
        summaryEl.classList.add("control-action-summary--warn");
        if (composeGap && !routingGap) {
          summaryEl.textContent =
            "App is registered and routes/local CF are configured. No dockerCompose stack is configured in leco.yaml, so compose services/logs are empty (expected for Worker-only apps).";
        } else if (!composeGap && routingGap) {
          summaryEl.textContent =
            "App is registered and compose is configured, but no app routes are defined yet. Add infrastructure.routing.entries (or cloudflare.localCfPublicPrefix) in leco.yaml.";
        } else {
          summaryEl.textContent =
            "App is in the registry, but leco.yaml has no compose or routing yet — no containers or Traefik routes were created. Expand JSON below for register_infrastructure_gaps, or edit hosting/app-available/…/leco.yaml.";
        }
      } else {
        summaryEl.classList.add("control-action-summary--ok");
        summaryEl.textContent = "Registered and deploy finished (or skipped if unchecked).";
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

/** AirLLM service card: table of installed / running models from overview `airllm_llm`.
 *  Mirrors formatOllamaLlmBlock so the runtime grid shows a parallel inline list
 *  under the AirLLM card. Reuses the Ollama svc-llm CSS so styling stays consistent.
 */
function formatAirLlmBlock(airllm, s) {
  if (!s || s.container !== "airllm") return "";
  if (!airllm) {
    return `<div class="svc-card__extras svc-card__extras--llm"><div class="svc-card__extras-title">HF models (AirLLM)</div><p class="muted small">Model list not available.</p></div>`;
  }
  if (airllm.error) {
    return `<div class="svc-card__extras svc-card__extras--llm"><div class="svc-card__extras-title">HF models (AirLLM)</div><p class="muted small">${escapeHtml(airllm.error)}</p></div>`;
  }
  const ver = airllm.server_version;
  const verStr =
    ver && typeof ver === "object" ? [ver.version, ver.airllm_version].filter(Boolean).join(" · ") : "";
  const meta =
    verStr || airllm.airllm_base
      ? `<p class="muted small svc-llm-meta">${verStr ? `Server ${escapeHtml(verStr)}` : ""}${
          verStr && airllm.airllm_base ? " · " : ""
        }<code>${escapeHtml(String(airllm.airllm_base || ""))}</code></p>`
      : "";
  if (!airllm.airllm_reachable) {
    return `<div class="svc-card__extras svc-card__extras--llm"><div class="svc-card__extras-title">HF models (AirLLM)</div>${meta}<p class="muted small">API unreachable — start the <code>airllm</code> container.</p></div>`;
  }
  const rows = Array.isArray(airllm.rows) ? airllm.rows : [];
  if (rows.length === 0) {
    return `<div class="svc-card__extras svc-card__extras--llm"><div class="svc-card__extras-title">HF models (AirLLM)</div>${meta}<p class="muted small">No models on disk. Pull from the <strong>AirLLM</strong> section below or POST to <code>/api/pull</code>.</p></div>`;
  }
  const sorted = [...rows].sort((a, b) => {
    if (!!a.running !== !!b.running) return a.running ? -1 : 1;
    if (!!a.installed !== !!b.installed) return a.installed ? -1 : 1;
    return String(a.name || "").localeCompare(String(b.name || ""));
  });
  const sum = `<p class="muted small svc-llm-counts">${Number(airllm.running_count || 0)} loaded in RAM · ${Number(airllm.installed_count || 0)} on disk</p>`;
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
    <div class="svc-card__extras-title">HF models (AirLLM)</div>
    ${meta}
    ${sum}
    <div class="svc-llm-table-wrap"><table class="svc-llm-table">${thead}<tbody>${tbody}</tbody></table></div>
  </div>`;
}

function renderServices(data) {
  const SB = serviceBrandUi();
  const ollamaLlm = data.ollama_llm || null;
  const airllmLlm = data.airllm_llm || null;
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
        ${formatAirLlmBlock(airllmLlm, s)}
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
  const sel = document.getElementById("logService");
  if (!sel) return;
  const res = await fetch("/api/services");
  const data = await res.json();
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

function applySavedRefreshRate() {
  const rateEl = document.getElementById("refreshRate");
  if (!rateEl) return;
  try {
    const saved = localStorage.getItem(LS_REFRESH_RATE_KEY);
    if (saved == null || !REFRESH_RATE_OPTIONS.has(saved)) return;
    rateEl.value = saved;
  } catch (_) {
    /* ignore */
  }
}

function persistRefreshRate(ms) {
  try {
    localStorage.setItem(LS_REFRESH_RATE_KEY, String(ms));
  } catch (_) {
    /* ignore */
  }
}

function scheduleRefresh() {
  const rateEl = document.getElementById("refreshRate");
  const nextEl = document.getElementById("nextRefresh");
  if (!rateEl || !nextEl) return;
  const ms = Number(rateEl.value || "0");
  persistRefreshRate(ms);
  if (refreshTimer) clearInterval(refreshTimer);
  if (tickTimer) clearInterval(tickTimer);

  if (ms > 0) {
    nextRefreshEpoch = Date.now() + ms;
    const hubChromeOnly = !document.getElementById("overviewTab");
    refreshTimer = setInterval(() => {
      if (hubChromeOnly) {
        loadOverview();
      } else if (activeTab === "overviewTab" || activeTab === "infrastructureTab") {
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
      nextEl.textContent = `Next refresh in ${left}s`;
    }, 250);
  } else {
    nextEl.textContent = "Manual refresh only";
  }
}

async function loadOverview() {
  const hostedAppsPromise = loadOverviewHostedApps();
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
    renderOverviewHostedAppsCard(overviewHostedAppsPayload);
    saveOverviewCache(data, cloudflareData, metricsData);
    saveMetricsCache(metricsData);
  } catch (e) {
    console.warn("loadOverview failed", e);
  }
  hostedAppsPromise
    .then((payload) => {
      renderOverviewHostedAppsCard(payload);
    })
    .catch((e) => {
      console.warn("loadOverview hosted apps failed", e);
    });
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

function initControlFileTransferBulkBar() {
  const bar = document.getElementById("controlFileTransferBulkBar");
  if (!bar || bar.dataset.wired === "1") return;
  bar.dataset.wired = "1";
  const FT_STACK = "stack-file-transfer-all";
  bar.querySelectorAll("[data-ft-bulk-action]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const action = btn.getAttribute("data-ft-bulk-action");
      const lbl = btn.getAttribute("data-ft-bulk-label") || action;
      if (!action) return;
      const destructive = action === "stop" || action === "restart" || action === "deploy";
      if (destructive) {
        const ok = await showAppConfirm({
          title: lbl,
          message:
            "Runs docker compose for file-transfer/docker-compose.yml (SFTP, FTP, read-only browser). Can take a minute.",
          confirmText: "Continue",
        });
        if (!ok) return;
      }
      runControlAction(FT_STACK, action, lbl);
    });
  });
}

function initInfraQuickstartBar() {
  if (document.body.dataset.infraQuickstartWired === "1") return;
  document.body.dataset.infraQuickstartWired = "1";
  document.body.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-infra-quickstart]");
    if (!btn) return;
    const target = btn.getAttribute("data-infra-quickstart");
    const action = btn.getAttribute("data-infra-quickstart-action");
    const lbl = btn.getAttribute("data-infra-quickstart-label") || action;
    if (!target || !action) return;
    ev.preventDefault();
    const isCf = target === "stack-cf-all";
    const ok = await showAppConfirm({
      title: lbl,
      message: isCf
        ? "Starts the full cloudflare-local compose stack on lh-network."
        : "Starts Docker services via the Control API. This can take several minutes and uses significant CPU/RAM.",
      confirmText: "Start",
    });
    if (!ok) return;
    runControlAction(target, action, lbl);
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
              "Runs scripts under ecosystem-stack/services (may take several minutes). Stop, restart, and redeploy skip the LEco DevOps container so the UI can show the result.",
            confirmText: "Continue",
          });
          if (!ok) return;
        }
        runControlAction(ECOSYSTEM_TARGET, action, lbl);
      });
    });
  }
  initControlInfraBulkBar();
  initControlFileTransferBulkBar();
}

async function bootstrapHubChrome() {
  initAppModal();
  applySavedRefreshRate();
  applyExternalLinkAttrs();
  document.getElementById("refreshNow")?.addEventListener("click", () => {
    loadOverview();
    loadUiAccessPanel();
  });
  document.getElementById("refreshRate")?.addEventListener("change", scheduleRefresh);
  await loadOverview();
  await loadUiAccessPanel();
  initHubUiAccess();
  scheduleRefresh();
}

async function bootstrap() {
  initGlobalPreloader();
  instrumentBackendFetchPreloader();
  if (!document.getElementById("overviewTab")) {
    await bootstrapHubChrome();
    return;
  }
  initAppModal();
  initTabs();
  initDashboardUrlRouting();
  initHostMetricsPanelRefresh();
  initControlBulkBar();
  initInfraQuickstartBar();
  initHostedAppsLogToolbar();
  initHostedAppsStagingActions();
  initHostedDevStackBinding();
  initHostedRegisterWizard();
  initRoutesTab();
  initHostedChartExpandModal();
  hydrateTrendHistoryFromCache();
  hydrateOverviewFromCache();
  applyTrendHistoryToLineChart();
  initControlActionOverlay();
  initOllamaModelsPanel();
  initAiSettingsPanel();
  initAiWizardToggle();
  initAiNewsPanel();
  await initLogsPanel();
  initLogEvents();
  applySavedRefreshRate();

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

  document.body.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-doc-open]");
    if (!btn) return;
    const docId = String(btn.getAttribute("data-doc-open") || "").trim();
    if (!docId) return;
    openDocumentationDoc(docId);
  });

  document.getElementById("referenceFilter")?.addEventListener("input", applyReferenceFilter);

  await loadOverview();
  loadUiAccessPanel();
  applyExternalLinkAttrs();
  const { tab: requestedTab, doc: requestedDoc, app: requestedApp, hash: requestedHash } =
    parseDashboardLocation();
  const hasTabParam = new URLSearchParams(window.location.search || "").has("tab");
  if (hasTabParam && document.getElementById(requestedTab)) {
    activateTab(requestedTab, {
      preferredDocId: requestedTab === "docsTab" ? requestedDoc || null : null,
      hostedSlug: requestedApp || null,
      hash: requestedHash,
      replace: true,
    });
  } else if (requestedDoc) {
    activateTab("docsTab", { preferredDocId: requestedDoc, replace: true });
  } else {
    try {
      const savedTab = localStorage.getItem(LS_ACTIVE_TAB_KEY);
      if (savedTab && document.getElementById(savedTab)) {
        activateTab(savedTab, { replace: true });
      } else {
        syncDashboardUrl(activeTab, { replace: true });
      }
    } catch (_) {
      syncDashboardUrl(activeTab, { replace: true });
    }
  }
  scheduleRefresh();
}

/* ==========================================================================
   AI Settings panel (Infrastructure §6) + AI Wizard toggle
   ========================================================================== */

/** Which providers need an API key and/or base_url field. */
const AI_PROVIDER_META = {
  none:                { needsKey: false, needsUrl: false, label: "No AI" },
  ollama:              { needsKey: false, needsUrl: false, label: "Ollama (local SLM)" },
  openai:              { needsKey: true,  needsUrl: false, label: "OpenAI" },
  anthropic:           { needsKey: true,  needsUrl: false, label: "Anthropic" },
  google:              { needsKey: true,  needsUrl: false, label: "Google Gemini" },
  "openai-compatible": { needsKey: true,  needsUrl: true,  label: "OpenAI-compatible" },
  hybrid:              { needsKey: true,  needsUrl: false, label: "Hybrid (SLM + LLM)" },
};

/** Cached state so wizard toggle can read current provider. */
let _aiCurrentProvider = "none";
let _aiCurrentModel = "";

function initAiSettingsPanel() {
  const provSel    = document.getElementById("aiProviderSelect");
  const modelSel   = document.getElementById("aiModelSelect");
  const modelIn    = document.getElementById("aiModelCustom");
  const keyRow     = document.getElementById("aiKeyRow");
  const urlRow     = document.getElementById("aiBaseUrlRow");
  const keyIn      = document.getElementById("aiApiKeyInput");
  const urlIn      = document.getElementById("aiBaseUrlInput");
  const saveBtn    = document.getElementById("aiSettingsSave");
  const testBtn    = document.getElementById("aiSettingsTest");
  const refreshBtn = document.getElementById("aiModelsRefresh");
  const statusEl   = document.getElementById("aiSettingsStatus");
  const testOut    = document.getElementById("aiSettingsTestOutput");
  if (!provSel) return;

  const hybridRow  = document.getElementById("aiHybridRow");
  const hybridSlm  = document.getElementById("aiHybridSlmSelect");
  const hybridLlm  = document.getElementById("aiHybridLlmSelect");
  const hybridKey  = document.getElementById("aiHybridLlmKey");
  const timeoutRow = document.getElementById("aiTimeoutRow");
  const timeoutIn  = document.getElementById("aiTimeoutInput");

  function showFieldsForProvider(prov) {
    const meta = AI_PROVIDER_META[prov] || {};
    keyRow.style.display    = (meta.needsKey && prov !== "hybrid") ? "" : "none";
    urlRow.style.display    = meta.needsUrl ? "" : "none";
    if (hybridRow) hybridRow.style.display = (prov === "hybrid") ? "" : "none";
    if (timeoutRow) timeoutRow.style.display = (prov !== "none") ? "" : "none";
    const hybridHint = document.getElementById("aiHybridHint");
    if (hybridHint) hybridHint.classList.toggle("is-hidden", prov !== "hybrid");
    if (document.getElementById("aiModelField")) {
      document.getElementById("aiModelField").style.display = (prov === "hybrid") ? "none" : "";
    }
  }

  provSel.addEventListener("change", () => {
    showFieldsForProvider(provSel.value);
    // Set sensible default timeout per provider type
    if (timeoutIn) {
      const defaults = { ollama: 300, "openai-compatible": 300, hybrid: 300, openai: 120, anthropic: 120, google: 120 };
      timeoutIn.value = defaults[provSel.value] || 180;
    }
    // Auto-fetch models when provider changes (except none/hybrid)
    if (provSel.value !== "none" && provSel.value !== "hybrid") {
      refreshAiModels();
    }
  });

  /** Load current settings from backend. */
  async function loadAiSettings() {
    try {
      const r = await fetch("/api/ai/settings");
      const d = await r.json();
      if (!d.ok) return;
      const prov = d.provider || "none";
      const plat = d.platform || {};
      const cloudBanner = document.getElementById("aiCloudBanner");
      if (cloudBanner) {
        cloudBanner.classList.toggle("is-hidden", !plat.cloud_first);
      }
      if (plat.cloud_first && plat.default_provider && prov === "none") {
        provSel.value = plat.default_provider;
      } else {
        provSel.value = prov;
      }
      _aiCurrentProvider = provSel.value;
      showFieldsForProvider(provSel.value);

      const activeProv = provSel.value;
      const provCfg = (d.providers || {})[activeProv] || {};
      keyIn.value  = provCfg.api_key || "";
      urlIn.value  = provCfg.base_url || "";
      const mdl = provCfg.default_model || d.default_model || "";
      _aiCurrentModel = mdl;
      modelIn.value = mdl;

      // Populate timeout — use per-provider timeout if set, else global
      if (timeoutIn) {
        const provTimeout = provCfg.timeout || d.timeout || 180;
        timeoutIn.value = provTimeout;
      }

      // Populate hybrid fields if present
      const hybCfg = (d.providers || {}).hybrid || {};
      if (hybridSlm) hybridSlm.value = hybCfg.local_provider || "ollama";
      if (hybridLlm) hybridLlm.value = hybCfg.cloud_provider || "openai";
      if (hybridKey) hybridKey.value = hybCfg.cloud_api_key || "";

      updateWizardToggleProvider(activeProv, mdl);

      // Auto-fetch models for the active provider
      if (activeProv !== "none" && activeProv !== "hybrid") {
        refreshAiModels();
      }
    } catch (_) { /* offline / not running */ }
  }

  /** Save settings to backend. */
  async function saveAiSettings() {
    statusEl.textContent = "Saving…";
    statusEl.className = "ai-settings__status muted small";
    const prov = provSel.value;
    const model = modelSel.value || modelIn.value.trim();
    const timeoutVal = timeoutIn ? parseInt(timeoutIn.value, 10) || 180 : 180;
    const payload = {
      provider: prov,
      default_model: model,
      timeout: timeoutVal,
      providers: {},
    };
    const pc = {};
    if (prov === "hybrid") {
      // Hybrid stores both local + cloud config
      pc.local_provider = hybridSlm?.value || "ollama";
      pc.cloud_provider = hybridLlm?.value || "openai";
      if (hybridKey?.value.trim()) pc.cloud_api_key = hybridKey.value.trim();
      pc.local_timeout = timeoutVal;
      pc.cloud_timeout = Math.min(timeoutVal, 120);
    } else {
      if (keyIn.value.trim()) pc.api_key = keyIn.value.trim();
      if (urlIn.value.trim()) pc.base_url = urlIn.value.trim();
      if (model) pc.default_model = model;
      pc.timeout = timeoutVal;
    }
    payload.providers[prov] = pc;

    try {
      const tok = controlToken();
      const r = await fetch("/api/ai/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Control-Token": tok },
        body: JSON.stringify(payload),
      });
      const d = await r.json();
      if (d.ok) {
        statusEl.textContent = "Saved ✓";
        statusEl.className = "ai-settings__status muted small ai-settings__status--ok";
        _aiCurrentProvider = prov;
        _aiCurrentModel = model;
        updateWizardToggleProvider(prov, model);
      } else {
        statusEl.textContent = d.error || "Save failed";
        statusEl.className = "ai-settings__status muted small ai-settings__status--err";
      }
    } catch (e) {
      statusEl.textContent = e.message;
      statusEl.className = "ai-settings__status muted small ai-settings__status--err";
    }
  }

  /** Test provider connectivity. */
  async function testAiProvider() {
    testOut.classList.remove("is-hidden");
    testOut.textContent = "Testing connection…\n";
    try {
      const tok = controlToken();
      const r = await fetch("/api/ai/test", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Control-Token": tok },
        body: JSON.stringify({ provider: provSel.value }),
      });
      const d = await r.json();
      if (d.ok) {
        let out = `✓ ${d.provider} — ${d.message}\n`;
        if (d.models && d.models.length) {
          out += `\nAvailable models (${d.models.length}):\n`;
          d.models.forEach((m) => { out += `  • ${m.name}\n`; });
        }
        testOut.textContent = out;
      } else {
        testOut.textContent = `✗ ${d.provider || provSel.value}: ${d.error || d.message || "failed"}\n`;
      }
    } catch (e) {
      testOut.textContent = `✗ ${e.message}\n`;
    }
  }

  /** Refresh model dropdown from provider. */
  async function refreshAiModels() {
    modelSel.innerHTML = '<option value="">Loading…</option>';
    try {
      const r = await fetch("/api/ai/models");
      const d = await r.json();
      modelSel.innerHTML = '<option value="">— select model —</option>';
      if (d.ok && d.models) {
        d.models.forEach((m) => {
          const opt = document.createElement("option");
          opt.value = m.name;
          opt.textContent = m.name;
          modelSel.appendChild(opt);
        });
        // Pre-select current
        if (_aiCurrentModel) modelSel.value = _aiCurrentModel;
      }
    } catch (_) {
      modelSel.innerHTML = '<option value="">— error loading —</option>';
    }
  }

  modelSel.addEventListener("change", () => {
    if (modelSel.value) modelIn.value = modelSel.value;
  });

  saveBtn.addEventListener("click", saveAiSettings);
  testBtn.addEventListener("click", testAiProvider);
  refreshBtn.addEventListener("click", refreshAiModels);

  // Initial load
  loadAiSettings();
}

/** Update the wizard toggle indicator with current provider info. */
function updateWizardToggleProvider(prov, model) {
  const el = document.getElementById("aiToggleProvider");
  const toggle = document.getElementById("aiAssistToggle");
  if (!el) return;
  if (prov === "none" || !prov) {
    el.textContent = "No provider configured";
    el.classList.remove("ai-toggle__provider--active");
    if (toggle) { toggle.checked = false; toggle.disabled = true; }
  } else if (prov === "hybrid") {
    const hSlm = document.getElementById("aiHybridSlmSelect");
    const hLlm = document.getElementById("aiHybridLlmSelect");
    const slmLabel = hSlm ? (AI_PROVIDER_META[hSlm.value] || {}).label || hSlm.value : "Ollama";
    const llmLabel = hLlm ? (AI_PROVIDER_META[hLlm.value] || {}).label || hLlm.value : "Cloud";
    el.textContent = `Hybrid · ${slmLabel} → ${llmLabel}`;
    el.classList.add("ai-toggle__provider--active");
    if (toggle) toggle.disabled = false;
  } else {
    const meta = AI_PROVIDER_META[prov] || {};
    el.textContent = `${meta.label || prov}${model ? " · " + model : ""}`;
    el.classList.add("ai-toggle__provider--active");
    if (toggle) toggle.disabled = false;
  }
}

function initAiWizardToggle() {
  const toggle    = document.getElementById("aiAssistToggle");
  const panel     = document.getElementById("aiAnalysisPanel");
  const settBtn   = document.getElementById("aiToggleSettings");
  const logEl     = document.getElementById("aiAnalysisLog");
  const metaEl    = document.getElementById("aiAnalysisMeta");
  const timerEl   = document.getElementById("aiAnalysisTimer");
  const trackEl   = document.getElementById("aiAnalysisTrack");
  const stepsEl   = document.getElementById("aiAnalysisSteps");
  const summaryEl = document.getElementById("aiAnalysisSummary");
  const actEl     = document.getElementById("aiAnalysisActions");
  const applyBtn  = document.getElementById("aiAnalysisApply");
  const discBtn   = document.getElementById("aiAnalysisDiscard");
  const filesEl   = document.getElementById("aiAnalysisFiles");
  if (!toggle || !panel) return;

  const stepEls = [
    { el: document.getElementById("aiStep1"), detail: document.getElementById("aiStep1Detail") },
    { el: document.getElementById("aiStep2"), detail: document.getElementById("aiStep2Detail") },
    { el: document.getElementById("aiStep3"), detail: document.getElementById("aiStep3Detail") },
  ];

  /** Cached generated files from the AI stream. */
  let _generatedFiles = null;
  let _timerInterval = null;
  let _startTime = 0;

  function startTimer() {
    _startTime = Date.now();
    if (_timerInterval) clearInterval(_timerInterval);
    timerEl.textContent = "0.0s";
    _timerInterval = setInterval(() => {
      const sec = ((Date.now() - _startTime) / 1000).toFixed(1);
      timerEl.textContent = sec + "s";
    }, 100);
  }

  function stopTimer() {
    if (_timerInterval) { clearInterval(_timerInterval); _timerInterval = null; }
  }

  function setStep(n, state) {
    // n = 1, 2, 3; state = "active" | "done" | "error" | "pending"
    const idx = n - 1;
    if (!stepEls[idx] || !stepEls[idx].el) return;
    const el = stepEls[idx].el;
    el.classList.remove("ai-step--active", "ai-step--done", "ai-step--error");
    if (state === "active") el.classList.add("ai-step--active");
    else if (state === "done") el.classList.add("ai-step--done");
    else if (state === "error") el.classList.add("ai-step--error");
    // Update icon
    const iconEl = el.querySelector(".ai-step__icon");
    if (iconEl) {
      if (state === "done") iconEl.textContent = "✓";
      else if (state === "error") iconEl.textContent = "✗";
      else iconEl.textContent = ["①", "②", "③"][idx];
    }
  }

  function setStepDetail(n, text) {
    const idx = n - 1;
    if (stepEls[idx] && stepEls[idx].detail) stepEls[idx].detail.textContent = text;
  }

  function setPanelState(state) {
    panel.classList.remove("ai-analysis-panel--running", "ai-analysis-panel--done-ok", "ai-analysis-panel--done-err");
    if (state === "running") panel.classList.add("ai-analysis-panel--running");
    else if (state === "ok") panel.classList.add("ai-analysis-panel--done-ok");
    else if (state === "error") panel.classList.add("ai-analysis-panel--done-err");
  }

  function showSummary(ok, stats) {
    if (!summaryEl) return;
    summaryEl.classList.remove("is-hidden", "ai-analysis-panel__summary--ok", "ai-analysis-panel__summary--err");
    if (ok) {
      summaryEl.classList.add("ai-analysis-panel__summary--ok");
      summaryEl.innerHTML =
        `<strong>✓ Analysis complete</strong>` +
        (stats.files ? ` <span class="ai-summary__stat">${esc(String(stats.files))} files collected</span>` : "") +
        (stats.tokens ? ` <span class="ai-summary__stat">~${Number(stats.tokens).toLocaleString()} tokens</span>` : "") +
        (stats.generated ? ` <span class="ai-summary__stat">${esc(String(stats.generated))} configs generated</span>` : "") +
        (stats.model ? ` <span class="ai-summary__stat">${esc(stats.provider || "")} · ${esc(stats.model)}</span>` : "") +
        (stats.elapsed ? ` <span class="ai-summary__stat">${Number(stats.elapsed).toFixed(1)}s</span>` : "");
    } else {
      summaryEl.classList.add("ai-analysis-panel__summary--err");
      summaryEl.innerHTML = `<strong>✗ ${esc(stats.error || "Analysis failed")}</strong>`;
    }
  }

  /** Navigate to AI settings on Infrastructure tab. */
  settBtn?.addEventListener("click", () => {
    activateTab("infrastructureTab");
    setTimeout(() => {
      document.getElementById("aiSettingsGroup")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 120);
  });

  /** Toggle panel visibility. */
  toggle.addEventListener("change", () => {
    if (toggle.checked && _aiCurrentProvider === "none") {
      toggle.checked = false;
      settBtn?.click();
      return;
    }
    panel.classList.toggle("is-hidden", !toggle.checked);
    if (!toggle.checked) {
      resetAiPanel();
    } else {
      // Auto-run analysis if toggle turned ON and a path+slug are already set
      const path = (document.getElementById("hostedRegPath")?.value || "").trim();
      const slug = (document.getElementById("hostedRegId")?.value || "").trim();
      if (path && slug) {
        window._runAiAnalysis();
      } else {
        logEl.innerHTML = '<span class="muted">Click <b>Detect</b> or <b>Browse</b> to start AI-assisted analysis.</span>\n';
      }
    }
  });

  function resetAiPanel() {
    stopTimer();
    logEl.textContent = "";
    metaEl.textContent = "";
    timerEl.textContent = "";
    actEl.classList.add("is-hidden");
    trackEl.classList.add("is-hidden");
    stepsEl.classList.add("is-hidden");
    summaryEl.classList.add("is-hidden");
    filesEl.textContent = "";
    _generatedFiles = null;
    setPanelState("");
    stepEls.forEach((s, i) => { setStep(i + 1, "pending"); setStepDetail(i + 1, ""); });
  }

  /** Map backend phase text → step number. */
  function phaseToStep(text) {
    if (/phase 1|collect/i.test(text)) return 1;
    if (/phase 2|analy/i.test(text)) return 2;
    if (/phase 3|generat/i.test(text)) return 3;
    return 0;
  }

  /** Run AI analysis when Detect is clicked with AI toggle on.
   *  Called from the existing Detect handler hook. */
  window._runAiAnalysis = async function runAiAnalysis() {
    if (!toggle.checked || _aiCurrentProvider === "none") return;

    const path = (document.getElementById("hostedRegPath")?.value || "").trim();
    const slug = (document.getElementById("hostedRegId")?.value || "").trim();
    if (!path || !slug) return;

    panel.classList.remove("is-hidden");
    resetAiPanel();
    setPanelState("running");
    trackEl.classList.remove("is-hidden");
    stepsEl.classList.remove("is-hidden");
    logEl.innerHTML = '<span class="ai-phase">Initializing AI provider…</span>\n';
    metaEl.innerHTML = '<span class="ai-spin"></span> Running…';
    startTimer();

    let currentStep = 0;
    let collectedFiles = 0;
    let collectedTokens = 0;

    const tok = controlToken();
    try {
      const r = await fetch("/api/leco/ai-analyze/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Control-Token": tok },
        body: JSON.stringify({ path, slug, source_path: "." }),
      });

      if (!r.ok) {
        logEl.innerHTML += `<span class="ai-err">HTTP ${r.status}: ${await r.text()}</span>\n`;
        stopTimer();
        setPanelState("error");
        metaEl.textContent = "Failed";
        trackEl.classList.add("is-hidden");
        showSummary(false, { error: `HTTP ${r.status}` });
        return;
      }

      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop();

        for (const line of lines) {
          if (!line.trim()) continue;
          let ev;
          try { ev = JSON.parse(line); } catch (_) { continue; }

          if (ev.type === "phase") {
            logEl.innerHTML += `<span class="ai-phase">${esc(ev.text)}</span>\n`;
            // Advance step indicators
            const step = phaseToStep(ev.text);
            if (step > 0) {
              // Mark previous steps done
              for (let i = 1; i < step; i++) setStep(i, "done");
              setStep(step, "active");
              currentStep = step;
              metaEl.innerHTML = `<span class="ai-spin"></span> Step ${step}/3`;
            }
          } else if (ev.type === "log") {
            logEl.innerHTML += esc(ev.text) + "\n";
          } else if (ev.type === "progress") {
            logEl.innerHTML += `<span class="ai-ok">${esc(ev.text)}</span>\n`;
            // Extract stats from progress data
            if (ev.data) {
              if (ev.data.files) collectedFiles = Array.isArray(ev.data.files) ? ev.data.files.length : ev.data.files;
              if (ev.data.tokens) collectedTokens = ev.data.tokens;
              if (currentStep === 1 && collectedFiles) setStepDetail(1, `${collectedFiles} files`);
              if (ev.data.services !== undefined) setStepDetail(2, `${ev.data.services} service(s)`);
            }
          } else if (ev.type === "ai_token") {
            logEl.innerHTML += esc(ev.text);
          } else if (ev.type === "file") {
            logEl.innerHTML += `<span class="ai-file">  ✓ ${esc(ev.text)}</span>\n`;
          } else if (ev.type === "error") {
            logEl.innerHTML += `<span class="ai-err">✗ ${esc(ev.text)}</span>\n`;
            if (currentStep > 0) setStep(currentStep, "error");
            stopTimer();
            setPanelState("error");
            metaEl.textContent = "Error";
            trackEl.classList.add("is-hidden");
            showSummary(false, { error: ev.text });
          } else if (ev.type === "done") {
            stopTimer();
            trackEl.classList.add("is-hidden");
            const d = ev.data || {};
            if (d.ok && d.generated_files) {
              _generatedFiles = d.generated_files;
              const fnames = Object.keys(d.generated_files);
              // Mark all steps done
              setStep(1, "done"); setStep(2, "done"); setStep(3, "done");
              setStepDetail(1, collectedFiles ? `${collectedFiles} files` : "");
              setStepDetail(3, `${fnames.length} configs`);
              setPanelState("ok");
              metaEl.textContent = `Done — ${fnames.length} files generated`;
              filesEl.textContent = fnames.join(", ");
              actEl.classList.remove("is-hidden");
              logEl.innerHTML += `\n<span class="ai-ok">✓ Analysis complete. ${fnames.length} config files ready.</span>\n`;
              showSummary(true, {
                files: d.files_collected || collectedFiles,
                tokens: d.tokens_used || collectedTokens,
                generated: fnames.length,
                model: d.model || "",
                provider: d.provider || "",
                elapsed: d.total_elapsed || ((Date.now() - _startTime) / 1000),
              });
            } else {
              setPanelState("error");
              if (currentStep > 0) setStep(currentStep, "error");
              metaEl.textContent = "Completed with errors";
              logEl.innerHTML += `<span class="ai-err">${esc(d.error || "Unknown error")}</span>\n`;
              showSummary(false, { error: d.error || "Unknown error" });
            }
          }

          // Auto-scroll log
          logEl.scrollTop = logEl.scrollHeight;
        }
      }

      // If stream ended without a done event
      if (!_generatedFiles && metaEl.textContent.includes("Running")) {
        stopTimer();
        trackEl.classList.add("is-hidden");
        setPanelState("error");
        metaEl.textContent = "Stream ended unexpectedly";
        showSummary(false, { error: "Stream ended without completion event" });
      }
    } catch (e) {
      stopTimer();
      trackEl.classList.add("is-hidden");
      setPanelState("error");
      logEl.innerHTML += `<span class="ai-err">✗ ${esc(e.message)}</span>\n`;
      metaEl.textContent = "Failed";
      showSummary(false, { error: e.message });
    }
  };

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  /** Apply generated files — write to disk and populate YAML textareas. */
  applyBtn?.addEventListener("click", async () => {
    if (!_generatedFiles) return;
    const path = (document.getElementById("hostedRegPath")?.value || "").trim();
    const slug = (document.getElementById("hostedRegId")?.value || "").trim();
    if (!path || !slug) return;

    applyBtn.disabled = true;
    applyBtn.textContent = "Writing…";
    const tok = controlToken();
    try {
      const r = await fetch("/api/leco/ai-analyze/write", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Control-Token": tok },
        body: JSON.stringify({ path, slug, files: _generatedFiles }),
      });
      const d = await r.json();
      if (d.ok) {
        const targetLabel = d.target || "";
        logEl.innerHTML += `\n<span class="ai-ok">✓ ${d.written.length} files written to ${esc(targetLabel)}</span>\n`;
        d.written.forEach((w) => {
          logEl.innerHTML += `<span class="ai-file">  ${w.action}: ${esc(w.name)}</span>\n`;
        });
        // Populate YAML textareas if we generated them
        const mYaml = _generatedFiles["leco.app.yaml"];
        const lYaml = _generatedFiles["leco.yaml"];
        if (mYaml) {
          const ta = document.getElementById("hostedRegManifestYaml");
          if (ta) ta.value = mYaml;
        }
        if (lYaml) {
          const ta = document.getElementById("hostedRegLocalhostYaml");
          if (ta) ta.value = lYaml;
        }
        metaEl.textContent = "Files written ✓";
        actEl.classList.add("is-hidden");
        if (summaryEl) {
          summaryEl.innerHTML = '<strong>✓ Configs written to hosting/app-available/' + esc(slug) + '/</strong>';
          summaryEl.className = "ai-analysis-panel__summary ai-analysis-panel__summary--ok";
        }
      } else {
        logEl.innerHTML += `<span class="ai-err">Write failed: ${esc(d.error)}</span>\n`;
      }
    } catch (e) {
      logEl.innerHTML += `<span class="ai-err">Write error: ${esc(e.message)}</span>\n`;
    }
    applyBtn.disabled = false;
    applyBtn.textContent = "Apply generated configs";
  });

  /** Discard generated files. */
  discBtn?.addEventListener("click", () => {
    _generatedFiles = null;
    actEl.classList.add("is-hidden");
    logEl.innerHTML += '<span class="ai-err">Discarded generated files.</span>\n';
    metaEl.textContent = "Discarded";
    setPanelState("");
    if (summaryEl) { summaryEl.classList.add("is-hidden"); }
  });
}

// ============================================================================
// AirLLM Models Panel (mirrors Ollama panel)
// ============================================================================

async function loadAirllmBackupSelect() {
  const sel = document.getElementById("airllmBackupSelect");
  if (!sel) return;
  try {
    const res = await fetch("/api/airllm/backups", { headers: ollamaApiHeaders(false) });
    const data = await res.json().catch(() => ({}));
    const cur = sel.value;
    sel.innerHTML = '<option value="">Select backup manifest…</option>';
    const items = data.backups || [];
    items.forEach((b) => {
      const opt = document.createElement("option");
      opt.value = b.filename;
      const size = b.size != null ? ` (${formatBytes(b.size)})` : "";
      const dt = b.mtime ? new Date(b.mtime * 1000).toLocaleString() : "";
      opt.textContent = `${b.filename}${size}${dt ? ` · ${dt}` : ""}`;
      sel.appendChild(opt);
    });
    if (cur && Array.from(sel.options).some((o) => o.value === cur)) {
      sel.value = cur;
    }
  } catch {
    sel.innerHTML = '<option value="">Backups unavailable</option>';
  }
}

function airllmActionWrap(act, label, apiModel, canonical, variant, { disabled, activeDot, title } = {}) {
  const dis = disabled ? " disabled" : "";
  const tit = title ? ` title="${escapeAttr(title)}"` : "";
  const dot = activeDot ? '<span class="action-state-dot" aria-hidden="true"></span>' : "";
  return `<span class="ollama-act-wrap"><button type="button" class="ollama-act ollama-act--${variant}" data-airllm-act="${escapeAttr(
    act,
  )}" data-airllm-model="${escapeAttr(apiModel)}" data-airllm-canonical="${escapeAttr(canonical || "")}"${dis}${tit}>${escapeHtml(label)}</button>${dot}</span>`;
}

async function loadAirllmModelsPanel() {
  const panel = document.getElementById("airllmModelsPanel");
  const sum = document.getElementById("airllmModelsSummary");
  const ins = document.getElementById("airllmModelsInsights");
  if (!panel) return;
  try {
    const res = await fetch("/api/airllm/models");
    const data = await res.json();
    const reach = data.airllm_reachable;
    const ver = data.server_version || {};
    const verStr = [ver.version, ver.airllm_version].filter(Boolean).join(" · ") || "—";
    const pinnedList = data.pinned || [];
    const runningN = data.running_count ?? 0;

    const pullAllBtn = document.getElementById("airllmPullAllBtn");
    if (pullAllBtn) {
      pullAllBtn.disabled = !reach || pinnedList.length === 0;
      pullAllBtn.title = !reach ? "AirLLM shim unreachable" : pinnedList.length === 0 ? "No pinned models" : "";
    }
    const unloadAllBtn = document.getElementById("airllmUnloadAllBtn");
    if (unloadAllBtn) {
      unloadAllBtn.disabled = !reach || runningN === 0;
      unloadAllBtn.title = !reach ? "AirLLM shim unreachable" : runningN === 0 ? "No models in RAM" : "";
    }

    if (sum) {
      sum.textContent = reach
        ? `API ${data.airllm_base || "—"} · ${data.installed_count ?? 0} cached · ${runningN} in RAM · ${pinnedList.length} pinned`
        : `AirLLM unreachable (check airllm container on port 11435): ${data.airllm_base || "http://airllm:11435"}`;
    }
    if (ins) {
      ins.textContent = reach
        ? `Server: ${verStr} · manifest: ${data.pinned_file || "—"}`
        : "Start the AirLLM shim, then refresh.";
    }
    const rows = data.rows || [];
    if (!rows.length) {
      panel.innerHTML = reach
        ? `<p class="muted">No models cached yet. Use <strong>Pull by HF name</strong> above. Pinned names without cached blobs still appear here once added to the pinned file.</p>`
        : `<p class="muted">Cannot list models until AirLLM shim is reachable.</p>`;
      return;
    }
    const thead = `<thead><tr><th>Model</th><th>Pinned</th><th>Cached</th><th>RAM</th><th>Size</th><th>Format</th><th>Keep-alive</th><th>Actions</th></tr></thead>`;
    const tbody = rows
      .map((r) => {
        const apiModel = r.api_model || r.name;
        const canonical = r.canonical || apiModel;
        const p = !!r.pinned;
        const i = !!r.installed;
        const run = !!r.running;
        const fmt = r.model_family || r.quantization_level || "safetensors";
        const exp = run ? formatOllamaExpires(r.expires_at) : "—";
        const acts = [
          airllmActionWrap("pin", "Pin", apiModel, canonical, "safe", {
            disabled: p,
            activeDot: p,
            title: p ? "Already pinned" : "",
          }),
          airllmActionWrap("unpin", "Unpin", apiModel, canonical, "caution", {
            disabled: !p,
            activeDot: !p,
            title: !p ? "Not pinned" : "",
          }),
          airllmActionWrap("warm", "Load", apiModel, canonical, "safe", {
            disabled: run || !i,
            activeDot: run,
            title: run ? "Already in RAM" : !i ? "Not cached — pull first" : "Load into RAM",
          }),
          airllmActionWrap("unload", "Off", apiModel, canonical, "caution", {
            disabled: !run,
            activeDot: !run,
            title: !run ? "Not loaded in RAM" : "Unload from RAM",
          }),
          airllmActionWrap("pull", "Pull", apiModel, canonical, "safe", {
            disabled: i,
            activeDot: i,
            title: i ? "Already cached" : "",
          }),
          airllmActionWrap("reinstall", "Reinstall", apiModel, canonical, "ops", {
            disabled: !i,
            activeDot: !i,
            title: !i ? "Not cached" : "Re-pull from HuggingFace",
          }),
          airllmActionWrap("delete", "Remove", apiModel, canonical, "destructive", {
            disabled: !i,
            activeDot: !i,
            title: !i ? "Nothing cached to remove" : "",
          }),
        ].join("");
        return `<tr>
          <td><div class="ollama-model-name">${escapeHtml(r.name)}</div>${canonical !== r.name ? `<div class="ollama-model-canon muted small">${escapeHtml(canonical)}</div>` : ""}</td>
          <td>${p ? "✓" : "—"}</td>
          <td>${i ? "✓" : "—"}</td>
          <td>${run ? "✓" : "—"}</td>
          <td>${r.size != null ? formatBytes(r.size) : "—"}</td>
          <td class="ollama-models-meta">${escapeHtml(fmt)}</td>
          <td>${escapeHtml(exp)}</td>
          <td class="ollama-models-actions">${acts}</td>
        </tr>`;
      })
      .join("");
    panel.innerHTML = `<div class="ollama-models-scroll"><table class="ollama-models-table">${thead}<tbody>${tbody}</tbody></table></div>`;
  } catch (e) {
    if (sum) sum.textContent = `Error: ${e.message || e}`;
    if (ins) ins.textContent = "";
    panel.innerHTML = `<p class="muted">Failed to load AirLLM models.</p>`;
  }
}

async function runAirllmModelAction(action, model, extra = {}) {
  try {
    const res = await fetch("/api/airllm/models/action", {
      method: "POST",
      headers: ollamaApiHeaders(true),
      body: JSON.stringify({ action, model: model || "", token: controlToken(), ...extra }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      await showAppAlert(data.error ? JSON.stringify(data.error) : `HTTP ${res.status}`, "AirLLM action failed");
      return;
    }
    if (data.note || data.path || data.restored_from) {
      await showAppAlert(
        [data.note, data.path ? `File: ${data.path}` : "", data.restored_from ? `Restored: ${data.restored_from}` : ""]
          .filter(Boolean)
          .join("\n"),
        "AirLLM",
      );
    }
    loadAirllmModelsPanel();
    loadAirllmBackupSelect();
  } catch (e) {
    await showAppAlert(String(e.message || e), "Error");
  }
}

function initAirllmModelsPanel() {
  document.getElementById("airllmPullAllBtn")?.addEventListener("click", async () => {
    const ok = await showAppConfirm({
      title: "Pull all pinned AirLLM models",
      message: "Start background pull for all pinned HF models? This can take a long time for large models.",
      confirmText: "Start pull",
    });
    if (!ok) return;
    runAirllmModelAction("pull_all", "");
  });
  document.getElementById("airllmModelsRefreshBtn")?.addEventListener("click", () => {
    loadAirllmModelsPanel();
    loadAirllmBackupSelect();
  });
  document.getElementById("airllmBackupBtn")?.addEventListener("click", async () => {
    const ok = await showAppConfirm({
      title: "Backup AirLLM manifest",
      message: "Write JSON to .local-eco-backups (cached models, running, pinned list snapshot)?",
      confirmText: "Save backup",
    });
    if (!ok) return;
    runAirllmModelAction("backup_manifest", "");
  });
  document.getElementById("airllmUnloadAllBtn")?.addEventListener("click", async () => {
    const ok = await showAppConfirm({
      title: "Unload AirLLM model from RAM",
      message: "Request unload (keep_alive=0) for the currently loaded model?",
      confirmText: "Unload",
      danger: true,
    });
    if (!ok) return;
    runAirllmModelAction("unload_all", "");
  });
  document.getElementById("airllmPullNameBtn")?.addEventListener("click", async () => {
    const inp = document.getElementById("airllmPullNameInput");
    const name = (inp?.value || "").trim();
    if (!name) {
      await showAppAlert("Enter a HuggingFace model ID (e.g. Qwen/Qwen2.5-7B-Instruct).", "Install");
      return;
    }
    runAirllmModelAction("pull", name);
  });
  document.getElementById("airllmLoadNameBtn")?.addEventListener("click", async () => {
    const inp = document.getElementById("airllmPullNameInput");
    const name = (inp?.value || "").trim();
    if (!name) {
      await showAppAlert("Enter an HF model id (e.g. Qwen/Qwen2.5-7B-Instruct).", "Load");
      return;
    }
    const ok = await showAppConfirm({
      title: "Load model into RAM",
      message: `Warm "${name}" into RAM with keep_alive=-1? AirLLM loads one model at a time; loading a new one evicts the previous.`,
      confirmText: "Load",
    });
    if (!ok) return;
    runAirllmModelAction("warm", name);
  });
  document.getElementById("airllmUnloadNameBtn")?.addEventListener("click", async () => {
    const inp = document.getElementById("airllmPullNameInput");
    const name = (inp?.value || "").trim();
    if (!name) {
      await showAppAlert("Enter an HF model id to unload.", "Unload");
      return;
    }
    runAirllmModelAction("unload", name);
  });
  document.getElementById("airllmRemoveNameBtn")?.addEventListener("click", async () => {
    const inp = document.getElementById("airllmPullNameInput");
    const name = (inp?.value || "").trim();
    if (!name) {
      await showAppAlert("Enter an HF model id to remove.", "Remove");
      return;
    }
    const ok = await showAppConfirm({
      title: "Remove model",
      message: `Delete "${name}" from the AirLLM cache (HF weights + shards)? This cannot be undone.`,
      confirmText: "Remove",
      danger: true,
    });
    if (!ok) return;
    runAirllmModelAction("delete", name);
  });
  document.getElementById("airllmShowCmdBtn")?.addEventListener("click", async () => {
    const inp = document.getElementById("airllmPullNameInput");
    const name = (inp?.value || "").trim();
    await showCliSnippetsModal("airllm", name);
  });
  loadPopularModels("airllm", "airllmPopularSelect", "airllmPullNameInput");
  document.getElementById("airllmRefreshBackupsBtn")?.addEventListener("click", () => loadAirllmBackupSelect());
  document.getElementById("airllmRestoreBackupBtn")?.addEventListener("click", async () => {
    const sel = document.getElementById("airllmBackupSelect");
    const fn = (sel?.value || "").trim();
    if (!fn) {
      await showAppAlert("Choose a backup file first (List backups).", "Restore");
      return;
    }
    const ok = await showAppConfirm({
      title: "Restore pinned list",
      message: `Overwrite ecosystem-stack/config/airllm-pinned-models.txt with pinned names from ${fn}? Models on disk are not deleted.`,
      confirmText: "Restore pinned",
      danger: true,
    });
    if (!ok) return;
    runAirllmModelAction("restore_backup", "", { filename: fn });
  });
  document.getElementById("airllmClearPinnedBtn")?.addEventListener("click", async () => {
    const ok = await showAppConfirm({
      title: "Clear pinned file",
      message: "Remove all model names from the pinned file? (Does not delete cached models.)",
      confirmText: "Clear pinned",
      danger: true,
    });
    if (!ok) return;
    runAirllmModelAction("clear_pinned", "");
  });
  document.getElementById("airllmModelsPanel")?.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-airllm-act]");
    if (!btn) return;
    const act = btn.getAttribute("data-airllm-act");
    const model = btn.getAttribute("data-airllm-model") || "";
    if (act === "delete") {
      const ok = await showAppConfirm({
        title: "Remove cached model",
        message: `Remove "${model}" from cache? This cannot be undone.`,
        confirmText: "Remove",
        danger: true,
      });
      if (!ok) return;
    }
    if (act === "warm") {
      const ok = await showAppConfirm({
        title: "Load model into RAM",
        message: `Load "${model}" into RAM with keep_alive? First load can take a while as AirLLM builds layer shards.`,
        confirmText: "Load",
      });
      if (!ok) return;
    }
    const extra = {};
    if (act === "unpin") {
      const c = (btn.getAttribute("data-airllm-canonical") || "").trim();
      if (c) extra.canonical = c;
    }
    runAirllmModelAction(act, model, extra);
  });
}

// Initialize AirLLM panel alongside Ollama
const originalInitOllamaModelsPanel = initOllamaModelsPanel;
initOllamaModelsPanel = function() {
  originalInitOllamaModelsPanel();
  initAirllmModelsPanel();
  loadAirllmModelsPanel();
  loadAirllmBackupSelect();
};


async function fetchPlatformJson(url, options) {
  const res = await fetch(url, options);
  const ct = (res.headers.get("content-type") || "").toLowerCase();
  const text = await res.text();
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} ${url}: ${text.slice(0, 200)}`);
  }
  if (!ct.includes("application/json")) {
    throw new Error(`Expected JSON from ${url}, got: ${text.slice(0, 120)}`);
  }
  try {
    return JSON.parse(text);
  } catch (e) {
    throw new Error(`Invalid JSON from ${url}: ${String(e.message || e)}`);
  }
}

const DEVSTACK_CATEGORY_LABELS = {
  sql: "SQL",
  nosql: "NoSQL",
  cache: "Cache / Valkey",
  storage: "Object storage",
  toolchain: "Toolchains",
  messaging: "Messaging",
  proxy: "Proxy / edge",
  search: "Search",
  other: "Other",
};

let _devStackPresetsCatalog = { groups: [], presets: {} };

function renderInstallProfileSelect(profileMap, currentProfile) {
  const sel = document.getElementById("platformInstallProfile");
  if (!sel) return;
  const profs = profileMap && typeof profileMap === "object" ? profileMap : {};
  const entries = Object.entries(profs);
  sel.innerHTML =
    '<option value="">— keep current —</option>' +
    entries
      .map(([key, meta]) => {
        const desc = typeof meta === "string" ? meta : key;
        return `<option value="${escapeAttr(key)}">${escapeHtml(key)} — ${escapeHtml(desc)}</option>`;
      })
      .join("");
  if (currentProfile) sel.value = currentProfile;
}

function loadDevStackPresetsCatalog(catalogPresets) {
  const raw = catalogPresets && typeof catalogPresets === "object" ? catalogPresets : {};
  _devStackPresetsCatalog = {
    groups: Array.isArray(raw.groups) ? raw.groups : [],
    presets: raw.presets && typeof raw.presets === "object" ? raw.presets : {},
  };
}

function getDevStackPreset(presetKey) {
  return _devStackPresetsCatalog.presets?.[presetKey] || null;
}

function renderDevStackPresets() {
  const sel = document.getElementById("devStackPreset");
  if (!sel) return;
  const keep = sel.value;
  const groups = _devStackPresetsCatalog.groups || [];
  const presets = _devStackPresetsCatalog.presets || {};
  const byGroup = {};
  Object.entries(presets).forEach(([k, p]) => {
    const g = (p && p.group) || "infrastructure";
    if (!byGroup[g]) byGroup[g] = [];
    byGroup[g].push([k, p]);
  });
  let html = '<option value="">— custom (pick below) —</option>';
  const seen = new Set();
  groups.forEach((g) => {
    const gid = g.id || g.label;
    const items = byGroup[gid] || [];
    if (!items.length) return;
    seen.add(gid);
    html += `<optgroup label="${escapeHtml(g.label || gid)}">`;
    items
      .sort((a, b) => String(a[1].label || a[0]).localeCompare(String(b[1].label || b[0])))
      .forEach(([k, pr]) => {
        html += `<option value="${escapeAttr(k)}">${escapeHtml(pr.label || k)}</option>`;
      });
    html += "</optgroup>";
  });
  Object.entries(byGroup).forEach(([gid, items]) => {
    if (seen.has(gid)) return;
    html += `<optgroup label="${escapeHtml(gid)}">`;
    items.forEach(([k, pr]) => {
      html += `<option value="${escapeAttr(k)}">${escapeHtml(pr.label || k)}</option>`;
    });
    html += "</optgroup>";
  });
  sel.innerHTML = html;
  if (keep && presets[keep]) sel.value = keep;
}

function updateDevStackPresetUi(presetKey) {
  const preset = presetKey ? getDevStackPreset(presetKey) : null;
  const sampleWrap = document.getElementById("devStackSampleDataWrap");
  const sampleCb = document.getElementById("devStackSampleData");
  const picker = document.getElementById("devStackComponentPicker");
  const hint = document.getElementById("devStackTemplateHint");
  const isTemplate = !!(preset && preset.template);
  if (sampleWrap) sampleWrap.hidden = !(preset && preset.supports_sample_data);
  if (sampleCb && !preset?.supports_sample_data) sampleCb.checked = false;
  if (picker) picker.hidden = isTemplate;
  if (hint) {
    if (isTemplate) {
      const sampleNote = preset.supports_sample_data
        ? " Optional sample content can be enabled below (WordPress & Magento seed automatically; others use guided install)."
        : "";
      hint.hidden = false;
      hint.textContent = `Ready stack: ${preset.label || preset.template} — compose includes app + database.${sampleNote}`;
    } else {
      hint.hidden = true;
      hint.textContent = "";
    }
  }
}

function applyDevStackPreset(presetKey) {
  const preset = getDevStackPreset(presetKey);
  if (!preset) {
    updateDevStackPresetUi("");
    return;
  }
  const idEl = document.getElementById("devStackId");
  const nameEl = document.getElementById("devStackName");
  if (idEl) idEl.value = preset.id || presetKey;
  if (nameEl) nameEl.value = preset.name || preset.label || presetKey;
  updateDevStackPresetUi(presetKey);
  if (preset.template) {
    refreshDevStacksEmptyHint();
    return;
  }
  applyDevStackComponentSelection(preset.components || []);
}

function applyDevStackComponentSelection(components) {
  const picker = document.getElementById("devStackComponentPicker");
  if (!picker) return;
  const want = new Map((components || []).map((c) => [String(c.id), String(c.version)]));
  picker.querySelectorAll(".devstack-comp-row").forEach((row) => {
    const cid = row.getAttribute("data-component-id");
    const cb = row.querySelector('input[type="checkbox"]');
    const verSel = row.querySelector("select.devstack-comp-ver");
    if (!cb || !verSel) return;
    if (want.has(cid)) {
      cb.checked = true;
      verSel.disabled = false;
      const ver = want.get(cid);
      if (ver && [...verSel.options].some((o) => o.value === ver)) verSel.value = ver;
    } else {
      cb.checked = false;
      verSel.disabled = true;
    }
  });
  refreshDevStacksEmptyHint();
}

function collectDevStackComponents() {
  const picker = document.getElementById("devStackComponentPicker");
  const out = [];
  if (!picker) return out;
  picker.querySelectorAll(".devstack-comp-row").forEach((row) => {
    const cb = row.querySelector('input[type="checkbox"]');
    if (!cb?.checked) return;
    const cid = row.getAttribute("data-component-id");
    const verSel = row.querySelector("select.devstack-comp-ver");
    const ver = verSel?.value?.trim();
    if (cid && ver) out.push({ id: cid, version: ver });
  });
  return out;
}

function normalizeDevStackCatalogComponents(raw) {
  if (!raw || typeof raw !== "object") return {};
  const inner = raw.components;
  if (inner && typeof inner === "object" && !Array.isArray(inner)) {
    const sample = Object.values(inner)[0];
    if (sample && typeof sample === "object" && (sample.versions || sample.category || sample.label)) {
      return inner;
    }
  }
  return raw;
}

function renderDevStackComponentPicker(catalogComponents) {
  const el = document.getElementById("devStackComponentPicker");
  if (!el) return;
  _devStackCatalogComponents = normalizeDevStackCatalogComponents(catalogComponents || {});
  const comps = _devStackCatalogComponents;
  const ids = Object.keys(comps).sort();
  if (!ids.length) {
    el.innerHTML = '<p class="muted small">No components in catalog.</p>';
    return;
  }
  const byCat = {};
  ids.forEach((cid) => {
    const meta = comps[cid] || {};
    const cat = meta.category || "other";
    if (!byCat[cat]) byCat[cat] = [];
    byCat[cat].push({ id: cid, meta });
  });
  const catOrder = ["sql", "nosql", "cache", "search", "storage", "toolchain", "messaging", "proxy", "other"];
  const sections = [];
  catOrder.forEach((cat) => {
    const items = byCat[cat];
    if (!items?.length) return;
    const rows = items
      .map(({ id: cid, meta }) => {
        const label = escapeHtml(meta.label || cid);
        const versions = Object.keys(meta.versions || {}).sort();
        const defaultVer = meta.default_version || versions[versions.length - 1] || "";
        const opts = versions
          .map((v) => {
            const sel = v === String(defaultVer) ? " selected" : "";
            return `<option value="${escapeAttr(v)}"${sel}>${escapeHtml(v)}</option>`;
          })
          .join("");
        return `<div class="devstack-comp-row" data-component-id="${escapeAttr(cid)}">
          <input type="checkbox" id="devstack-cb-${escapeAttr(cid)}" data-component-id="${escapeAttr(cid)}" />
          <label class="devstack-comp-row__label" for="devstack-cb-${escapeAttr(cid)}"><strong>${label}</strong><span class="muted">${escapeHtml(cid)}</span></label>
          <span class="devstack-comp-row__ver"><select class="devstack-comp-ver" data-version-for="${escapeAttr(cid)}" disabled aria-label="Version for ${escapeAttr(cid)}">${opts}</select></span>
        </div>`;
      })
      .join("");
    sections.push(
      `<section class="devstack-picker__section">
        <h4 class="devstack-picker__title">${escapeHtml(DEVSTACK_CATEGORY_LABELS[cat] || cat)}</h4>
        <div class="devstack-picker__grid">${rows}</div>
      </section>`,
    );
  });
  el.innerHTML = sections.join("");
  el.querySelectorAll('.devstack-comp-row input[type="checkbox"]').forEach((cb) => {
    cb.addEventListener("change", () => {
      const row = cb.closest(".devstack-comp-row");
      const verSel = row?.querySelector("select.devstack-comp-ver");
      if (verSel) verSel.disabled = !cb.checked;
      refreshDevStacksEmptyHint();
    });
  });
  refreshDevStacksEmptyHint();
}

function devStackEmptyListHtml() {
  const presetKey = document.getElementById("devStackPreset")?.value?.trim() || "";
  const preset = presetKey ? getDevStackPreset(presetKey) : null;
  if (preset?.template) {
    const sample = document.getElementById("devStackSampleData")?.checked ? " with sample content" : "";
    const stackId = document.getElementById("devStackId")?.value?.trim() || preset.id || "";
    return `<p class="devstack-empty-hint muted small">Ready stack <strong>${escapeHtml(preset.label || preset.template)}</strong>${sample} configured — not created yet. Click <strong>Create stack</strong>${stackId ? ` to save <code>${escapeHtml(stackId)}</code>` : ""}.</p>`;
  }
  const picked = collectDevStackComponents();
  const stackId = document.getElementById("devStackId")?.value?.trim() || "";
  if (picked.length) {
    const summary = picked.map((c) => `${c.id}:${c.version}`).join(", ");
    const idBit = stackId
      ? ` Click <strong>Create stack</strong> to save <code>${escapeHtml(stackId)}</code>.`
      : " Enter a stack id and click <strong>Create stack</strong>.";
    return `<p class="devstack-empty-hint muted small"><strong>${picked.length} component(s) selected</strong> (${escapeHtml(summary)}) — not created yet.${idBit}</p>`;
  }
  return `<p class="devstack-empty-hint muted small">No dev stacks created yet. Pick a <strong>Quick preset</strong> or select components, then click <strong>Create stack</strong>.</p>`;
}

function refreshDevStacksEmptyHint() {
  const el = document.getElementById("devStacksList");
  if (!el || el.querySelector(".platform-stack-row")) return;
  el.innerHTML = devStackEmptyListHtml();
}

function devStackIsRunning(state) {
  const s = String(state || "").toLowerCase();
  return s === "running" || s === "partial";
}

/** @type {{ stackId: string, action: string } | null} */
let devStackActionInFlight = null;

function devStackActionLabel(action, inflightAction) {
  if (action === "start") return inflightAction === "start" ? "Starting…" : "Start";
  if (action === "stop") return inflightAction === "stop" ? "Stopping…" : "Stop";
  if (action === "destroy") return inflightAction === "destroy" ? "Destroying…" : "Destroy";
  if (action === "repair") return inflightAction === "repair" ? "Repairing…" : "Repair";
  if (action === "reinstall") return inflightAction === "reinstall" ? "Reinstalling…" : "Reinstall";
  if (action === "redeploy") return inflightAction === "redeploy" ? "Reinstalling…" : "Reinstall";
  return action;
}

function devStackRowActionButtons(rowId, state) {
  return (
    devStackActionBtn(rowId, "start", state) +
    devStackActionBtn(rowId, "stop", state) +
    devStackActionBtn(rowId, "repair", state) +
    devStackActionBtn(rowId, "reinstall", state) +
    devStackActionBtn(rowId, "destroy", state)
  );
}

function devStackActionBtn(id, action, state) {
  const inflight = devStackActionInFlight;
  const rowBusy = inflight && inflight.stackId === id;
  const inflightAction = rowBusy ? inflight.action : null;
  const label = devStackActionLabel(action, inflightAction);
  const actKey = action === "redeploy" ? "reinstall" : action;
  const baseCls = `ctrl-act platform-svc-act dev-stack-act dev-stack-act--${actKey}`;
  if (action === "repair" || action === "reinstall" || action === "redeploy") {
    const dis = rowBusy ? " disabled" : "";
    const busyCls = rowBusy && inflightAction === action ? " dev-stack-act--busy" : "";
    const ariaBusy = rowBusy && inflightAction === action ? ' aria-busy="true"' : "";
    return `<button type="button" class="${baseCls}${busyCls}" data-id="${escapeAttr(id)}" data-action="${action}"${dis}${ariaBusy} aria-label="${escapeAttr(label)} ${escapeAttr(id)}">${label}</button>`;
  }
  if (action === "destroy") {
    const dis = rowBusy ? " disabled" : "";
    const busyCls = inflightAction === "destroy" ? " dev-stack-act--busy" : "";
    const ariaBusy = inflightAction === "destroy" ? ' aria-busy="true"' : "";
    return `<button type="button" class="${baseCls}${busyCls}" data-id="${escapeAttr(id)}" data-action="destroy"${dis}${ariaBusy} aria-label="${escapeAttr(label)} ${escapeAttr(id)}">${label}</button>`;
  }
  const running = devStackIsRunning(state);
  const isStart = action === "start";
  let disabled = (isStart && running) || (!isStart && !running);
  if (rowBusy) disabled = true;
  const primary = !disabled && (isStart || action === "stop");
  const stateCls = disabled ? "platform-svc-act--inactive" : primary ? "platform-svc-act--primary" : "";
  const busyCls = rowBusy && inflightAction === action ? " dev-stack-act--busy" : "";
  const dis = disabled ? " disabled" : "";
  const ariaBusy = rowBusy && inflightAction === action ? ' aria-busy="true"' : "";
  return `<button type="button" class="${baseCls} ${stateCls}${busyCls}" data-id="${escapeAttr(id)}" data-action="${action}"${dis}${ariaBusy} aria-label="${escapeAttr(label)} ${escapeAttr(id)}">${label}</button>`;
}

function setDevStackActionBusy(stackId, action) {
  devStackActionInFlight = stackId && action ? { stackId: String(stackId), action: String(action) } : null;
  const spinner = document.getElementById("devStackLogSpinner");
  const wrap = document.getElementById("devStackLogWrap");
  const closeBtn = document.getElementById("devStackLogClose");
  const busy = !!devStackActionInFlight;
  if (spinner) {
    spinner.classList.toggle("is-hidden", !busy);
    spinner.hidden = !busy;
    spinner.setAttribute("aria-hidden", busy ? "false" : "true");
  }
  if (wrap) wrap.classList.toggle("devstack-log-wrap--busy", busy);
  if (closeBtn) closeBtn.disabled = busy;
  document.querySelectorAll(".platform-stack-row").forEach((row) => {
    const rowId = row.getAttribute("data-stack-id") || "";
    const isRow = busy && rowId === devStackActionInFlight?.stackId;
    row.classList.toggle("platform-stack-row--action-busy", isRow);
    if (!isRow) return;
    const state = row.getAttribute("data-stack-state") || "unknown";
    const actions = row.querySelector(".platform-svc-tile__actions");
    if (!actions) return;
    actions.innerHTML = devStackRowActionButtons(rowId, state);
    actions.querySelectorAll(".dev-stack-act").forEach((btn) => {
      btn.addEventListener("click", () => devStackAction(btn));
    });
  });
}

function inferPlatformDeploymentMode(cfg, hasPlatformFile) {
  const explicit = String(cfg?.deployment_mode || "").trim().toLowerCase();
  if (explicit === "cloud" || explicit === "local") return explicit;
  const dom = String(cfg?.base_domain || "").trim().toLowerCase();
  if (dom && dom !== "lh") return "cloud";
  const tls = String(cfg?.tls?.mode || cfg?.tls_mode || "").trim().toLowerCase();
  if (tls === "acme") return "cloud";
  const prof = String(cfg?.install_profile || "").trim();
  if (/cloud|ai-cloud/.test(prof)) return "cloud";
  if (hasPlatformFile === false) {
    try {
      const host = window.location.hostname || "";
      if (host && !host.endsWith(".lh") && host !== "localhost" && host !== "127.0.0.1") {
        return "cloud";
      }
    } catch (_) {
      /* ignore */
    }
  }
  return "local";
}

function applyPlatformDeploymentDefaults(mode, { userChangedDomain } = {}) {
  const dom = document.getElementById("platformBaseDomain");
  const tls = document.getElementById("platformTlsMode");
  if (!dom || !tls) return;
  if (mode === "cloud") {
    if (!userChangedDomain && (dom.value === "lh" || !dom.value.trim())) {
      dom.value = "dev.example.com";
    }
    if (tls.value === "mkcert") tls.value = "acme";
  } else if (mode === "local") {
    if (!userChangedDomain && (!dom.value.trim() || dom.value.includes("example.com"))) {
      dom.value = "lh";
    }
    if (tls.value === "acme") tls.value = "mkcert";
  }
}

function bindPlatformDeploymentAutoSelect() {
  const modeEl = document.getElementById("platformDeploymentMode");
  if (!modeEl || modeEl.dataset.bound === "1") return;
  modeEl.dataset.bound = "1";
  let domainTouched = false;
  const domEl = document.getElementById("platformBaseDomain");
  if (domEl) {
    domEl.addEventListener("input", () => {
      domainTouched = true;
    });
  }
  modeEl.addEventListener("change", () => {
    applyPlatformDeploymentDefaults(modeEl.value, { userChangedDomain: domainTouched });
  });
}

async function loadPlatformTab() {
  const statusEl = document.getElementById("platformConfigStatus");
  const svcEl = document.getElementById("platformServicesList");
  try {
    const catalogData = await fetchPlatformJson("/api/platform/catalog");
    loadDevStackPresetsCatalog(catalogData.dev_stack_presets || {});
    renderDevStackPresets();
    renderDevStackComponentPicker(catalogData.components || {});
    const cfgData = await fetchPlatformJson("/api/platform/config");
    const cfg = cfgData.config || {};
    const hasPlatformFile = cfgData.has_platform_file !== false;
    renderInstallProfileSelect(catalogData.profiles || {}, cfg.install_profile || "");
    const dom = document.getElementById("platformBaseDomain");
    const tls = document.getElementById("platformTlsMode");
    const mode = document.getElementById("platformDeploymentMode");
    const deployment = inferPlatformDeploymentMode(cfg, hasPlatformFile);
    if (dom) dom.value = cfg.base_domain || (deployment === "cloud" ? "dev.example.com" : "lh");
    if (tls) tls.value = (cfg.tls && cfg.tls.mode) || (deployment === "cloud" ? "acme" : "mkcert");
    if (mode) mode.value = deployment;
    bindPlatformDeploymentAutoSelect();
    const svcData = await fetchPlatformJson("/api/platform/services");
    renderPlatformServices(svcData.services || []);
    const stData = await fetchPlatformJson("/api/dev-stacks");
    renderDevStacksList(stData.stacks || []);
    if (statusEl) statusEl.textContent = "";
  } catch (e) {
    if (svcEl) {
      svcEl.innerHTML = `<p class="muted small">Could not load platform APIs. ${escapeHtml(String(e.message || e))}</p>`;
    }
    if (statusEl) statusEl.textContent = String(e.message || e);
  }
}

function platformSvcActionBtn(id, action, running) {
  const isStart = action === "start";
  const disabled = (isStart && running) || (!isStart && !running);
  const primary = !disabled;
  const label = isStart ? "Start" : "Stop";
  const tone = isStart ? "ctrl-act--safe" : "ctrl-act--caution";
  const stateCls = disabled ? "platform-svc-act--inactive" : primary ? "platform-svc-act--primary" : "";
  const dis = disabled ? " disabled" : "";
  return `<button type="button" class="ctrl-act ${tone} platform-svc-act ${stateCls}" data-id="${escapeAttr(id)}" data-action="${action}"${dis} aria-label="${escapeAttr(label)} ${escapeAttr(id)}">${label}</button>`;
}

function renderPlatformServiceTile(s) {
  const id = escapeHtml(String(s.id));
  const lab = escapeHtml(String(s.label || s.id));
  const running = !!s.running;
  const st = running ? "running" : "stopped";
  const en = s.enabled ? "enabled" : "disabled";
  const tileCls = running ? "platform-svc-tile--running" : "platform-svc-tile--stopped";
  return `<div class="platform-svc-tile ${tileCls}" data-id="${id}" data-running="${running ? "1" : "0"}">
    <div class="platform-svc-tile__head">
      <span class="platform-svc-tile__name">${lab}</span>
      <span class="platform-svc-tile__meta muted">${id} · ${en} · ${st}</span>
    </div>
    <div class="platform-svc-tile__actions">
      ${platformSvcActionBtn(String(s.id), "start", running)}
      ${platformSvcActionBtn(String(s.id), "stop", running)}
    </div>
  </div>`;
}

function renderPlatformServices(services) {
  const el = document.getElementById("platformServicesList");
  if (!el) return;
  if (!services.length) {
    el.innerHTML = '<p class="muted small">No services.</p>';
    return;
  }
  const ecosystem = services.filter((s) => (s.type || "ecosystem") === "ecosystem");
  const bundles = services.filter((s) => s.type === "bundle");
  const sections = [];
  if (ecosystem.length) {
    sections.push(
      `<section class="platform-services-section">
        <h4 class="platform-services-section__title">Ecosystem services</h4>
        <div class="platform-services-grid">${ecosystem.map(renderPlatformServiceTile).join("")}</div>
      </section>`,
    );
  }
  if (bundles.length) {
    sections.push(
      `<section class="platform-services-section">
        <h4 class="platform-services-section__title">Optional bundles</h4>
        <div class="platform-services-grid">${bundles.map(renderPlatformServiceTile).join("")}</div>
      </section>`,
    );
  }
  el.innerHTML = sections.join("");
  el.querySelectorAll(".platform-svc-act").forEach((btn) => {
    btn.addEventListener("click", () => platformServiceAction(btn));
  });
}

async function platformServiceAction(btn) {
  if (btn.disabled) return;
  const id = btn.getAttribute("data-id");
  const action = btn.getAttribute("data-action");
  const token = controlToken();
  const res = await fetch(`/api/platform/services/${encodeURIComponent(id)}/action`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, token }),
  });
  const data = await res.json();
  const statusEl = document.getElementById("platformConfigStatus");
  if (statusEl) statusEl.textContent = data.output || JSON.stringify(data);
  loadPlatformTab();
}

function devStackDualUrlRow(u) {
  const label = u.label || u.role || "URL";
  const httpUrl =
    u.url_http || (typeof u.url === "string" && u.url.startsWith("http://") ? u.url : "");
  if (httpUrl && httpUrl.includes(".lh")) {
    return cfDualUrlRow(label, httpUrl);
  }
  const one = (u.url_https || u.url || "").trim();
  if (!one) return "";
  const oneA = escapeAttr(one);
  return `<div class="row"><span>${escapeHtml(label)}</span><a href="${oneA}"${externalNavigationAttrs(one)}>${escapeHtml(one)}</a></div>`;
}

function devStackOpenLinkBtn(url, label, { primary = false, tone = "ops" } = {}) {
  const u = String(url || "").trim();
  if (!u) return "";
  const cls = primary ? "ctrl-act--safe" : tone === "deploy" ? "ctrl-act--deploy" : "ctrl-act--ops";
  return `<a class="ctrl-act ${cls} devstack-quick-link" href="${escapeAttr(u)}"${externalNavigationAttrs(u)}>${escapeHtml(label)}</a>`;
}

async function devStackCopyMagicLink(url, label) {
  const u = String(url || "").trim();
  if (!u) return;
  try {
    await navigator.clipboard.writeText(u);
    setDevStackStatus(`Copied ${label || "link"} to clipboard.`, { error: false });
  } catch (_) {
    await showAppCopyLinkModal(u, label || "Magic link");
  }
}

function renderDevStackNetworkDiagram(net) {
  if (!net || !Array.isArray(net.nodes) || !net.nodes.length) {
    return '<p class="muted small">No networking diagram for this stack.</p>';
  }
  const nodeMap = new Map(net.nodes.map((n) => [n.id, n]));
  const tierClass = (tier) => {
    const t = String(tier || "").toLowerCase();
    if (t === "edge") return "devstack-net-node--edge";
    if (t === "cache") return "devstack-net-node--cache";
    if (t === "data") return "devstack-net-node--data";
    return "devstack-net-node--app";
  };
  const nodeHtml = (id) => {
    const n = nodeMap.get(id);
    if (!n) return "";
    const detail = n.detail ? `<span class="devstack-net-node__detail">${escapeHtml(n.detail)}</span>` : "";
    return `<div class="devstack-net-node ${tierClass(n.tier)}" title="${escapeAttr(n.detail || n.label || id)}"><span class="devstack-net-node__label">${escapeHtml(n.label || id)}</span>${detail}</div>`;
  };
  let flow = "";
  const layers = Array.isArray(net.layers) ? net.layers : [];
  if (layers.length) {
    flow = layers
      .map((layer) => {
        const ids = Array.isArray(layer) ? layer : [];
        const parts = [];
        ids.forEach((id, idx) => {
          if (idx > 0) parts.push('<span class="devstack-net-arrow" aria-hidden="true">→</span>');
          parts.push(nodeHtml(id));
        });
        return `<div class="devstack-net-flow">${parts.join("")}</div>`;
      })
      .join("");
    flow = `<div class="devstack-net-layers">${flow}</div>`;
  } else {
    const edges = Array.isArray(net.edges) ? net.edges : [];
    if (edges.length) {
      const parts = [];
      edges.forEach((pair, idx) => {
        if (!Array.isArray(pair) || pair.length < 2) return;
        if (idx === 0) parts.push(nodeHtml(pair[0]));
        parts.push('<span class="devstack-net-arrow" aria-hidden="true">→</span>');
        parts.push(nodeHtml(pair[1]));
      });
      flow = `<div class="devstack-net-flow">${parts.join("")}</div>`;
    } else {
      flow = `<div class="devstack-net-flow devstack-net-flow--grid">${net.nodes.map((n) => nodeHtml(n.id)).join("")}</div>`;
    }
  }
  const host = net.hostname ? `<p class="devstack-net-host muted small"><strong>Public host</strong> <code>${escapeHtml(net.hostname)}</code></p>` : "";
  const route = net.route_file
    ? `<p class="devstack-net-route muted small"><strong>Traefik</strong> <code>${escapeHtml(net.route_file)}</code></p>`
    : "";
  return `${host}${route}${flow}`;
}

function renderDevStackQuickLinks(links) {
  const items = Array.isArray(links) ? links : [];
  if (!items.length) return "";
  return `<div class="devstack-quick-links">${items
    .map((link) => {
      const httpUrl = link.url_http || link.url || "";
      const httpsUrl = link.url_https || httpUrl;
      const openUrl = httpUrl.includes(".lh") ? httpUrl : httpsUrl || httpUrl;
      const kind = String(link.kind || "");
      const primary = !!link.primary || kind === "admin";
      const tone = kind === "database_gui" || kind === "redis_gui" ? "deploy" : "ops";
      const hint = link.hint ? `<p class="devstack-quick-link__hint muted small">${escapeHtml(link.hint)}</p>` : "";
      const copyTarget = kind === "admin" ? httpsUrl || httpUrl : openUrl;
      return `<div class="devstack-quick-link-card">
        <div class="devstack-quick-link-card__actions">
          ${devStackOpenLinkBtn(openUrl, link.label || "Open", { primary, tone })}
          ${copyTarget ? `<button type="button" class="ctrl-act ctrl-act--ops devstack-copy-magic-link" data-url="${escapeAttr(copyTarget)}" data-label="${escapeAttr(link.label || "Link")}">Copy link</button>` : ""}
        </div>
        ${hint}
      </div>`;
    })
    .join("")}</div>`;
}

function renderDevStackCredentialsBlock(access, rawId) {
  const creds = Array.isArray(access?.credentials) ? access.credentials : [];
  const adminReset = access?.admin_reset || {};
  const rows = creds
    .map((c) => {
      const httpUrl = c.login_url_http || "";
      const httpsUrl = c.login_url_https || c.login_url || "";
      const openUrl = httpUrl && httpUrl.includes(".lh") ? httpUrl : httpsUrl || httpUrl;
      return `<li class="devstack-cred-row">
        <div class="devstack-cred-row__head"><strong>${escapeHtml(c.label || "Login")}</strong></div>
        <div class="devstack-cred-row__vals"><code>${escapeHtml(c.username || "")}</code> <span class="muted">/</span> <code>${escapeHtml(c.password || "")}</code></div>
        <div class="devstack-cred-row__actions">
          ${openUrl ? devStackOpenLinkBtn(openUrl, "Open admin", { primary: true }) : ""}
          ${openUrl ? `<button type="button" class="ctrl-act ctrl-act--ops devstack-copy-magic-link" data-url="${escapeAttr(openUrl)}" data-label="${escapeAttr(c.label || "Admin")}">Copy magic link</button>` : ""}
        </div>
      </li>`;
    })
    .join("");
  const resetBtn = adminReset.supported
    ? `<button type="button" class="ctrl-act ctrl-act--caution dev-stack-reset-admin" data-id="${escapeAttr(rawId)}">Reset credentials${adminReset.username ? ` → ${escapeHtml(adminReset.username)} / ${escapeHtml(adminReset.password || "•••")}` : ""}</button>`
    : "";
  if (!rows && !resetBtn) {
    return '<p class="muted small">No preset admin credentials — complete the installer in the browser.</p>';
  }
  return `<ul class="devstack-cred-list">${rows}</ul>${resetBtn ? `<div class="devstack-cred-reset">${resetBtn}</div>` : ""}`;
}

function renderDevStackDataStoresBlock(dataStores) {
  const stores = Array.isArray(dataStores) ? dataStores : [];
  if (!stores.length) return "";
  return `<ul class="devstack-data-stores">${stores
    .map((store) => {
      const creds = store.credentials || {};
      const user = creds.username || creds.user || "—";
      const pw = creds.password !== undefined && creds.password !== null ? creds.password : "—";
      const db = creds.database || creds.dbname || "";
      const eps = (store.connection_endpoints || [])
        .slice(0, 3)
        .map((ep) => `<li><span class="muted small">${escapeHtml(ep.label || ep.scope || "")}</span> <code>${escapeHtml(ep.uri || "")}</code></li>`)
        .join("");
      const cli = store.cli_hint
        ? `<p class="muted small devstack-data-store__cli"><strong>CLI</strong> <code>${escapeHtml(store.cli_hint)}</code></p>`
        : "";
      return `<li class="devstack-data-store">
        <strong>${escapeHtml(store.name || store.kind)}</strong> <span class="muted small">(${escapeHtml(store.kind || "")} · Docker <code>${escapeHtml(store.docker_host || store.name || "")}</code>)</span>
        <div class="devstack-data-store__creds muted small">${db ? `DB <code>${escapeHtml(db)}</code> · ` : ""}User <code>${escapeHtml(user)}</code> · Pass <code>${escapeHtml(pw === "" ? "(empty)" : pw)}</code></div>
        ${eps ? `<ul class="devstack-data-store__eps">${eps}</ul>` : ""}
        ${cli}
      </li>`;
    })
    .join("")}</ul>`;
}

function renderDevStackCardBody(access, rawId) {
  if (!access || typeof access !== "object") return "";
  if (access.error) {
    return `<p class="devstack-status-line--error muted small">${escapeHtml(access.error)}</p>`;
  }
  const notes = Array.isArray(access.notes) ? access.notes : [];
  const noteRows = notes.map((n) => `<li class="muted small">${escapeHtml(n)}</li>`).join("");
  return `<div class="platform-stack-row__grid">
    <section class="devstack-card-panel devstack-card-panel--network">
      <h4 class="devstack-card-panel__title">Networking</h4>
      ${renderDevStackNetworkDiagram(access.networking)}
    </section>
    <section class="devstack-card-panel devstack-card-panel--admin">
      <h4 class="devstack-card-panel__title">Admin &amp; credentials</h4>
      ${renderDevStackCredentialsBlock(access, rawId)}
    </section>
    <section class="devstack-card-panel devstack-card-panel--links devstack-card-panel--wide">
      <h4 class="devstack-card-panel__title">Quick open</h4>
      ${renderDevStackQuickLinks(access.quick_links)}
    </section>
    <section class="devstack-card-panel devstack-card-panel--data">
      <h4 class="devstack-card-panel__title">Data stores</h4>
      ${renderDevStackDataStoresBlock(access.data_stores) || '<p class="muted small">No database/cache services in this stack compose file.</p>'}
    </section>
    ${noteRows ? `<section class="devstack-card-panel devstack-card-panel--notes"><h4 class="devstack-card-panel__title">Notes</h4><ul class="devstack-access__notes">${noteRows}</ul></section>` : ""}
  </div>
  <p class="devstack-access__hint muted small">Stack files: <code>platform/dev-stacks/${escapeHtml(rawId)}/</code> · <strong>Advanced</strong> below to edit.</p>`;
}


let devStackFileEditorState = { stackId: "", path: "", readOnly: false, relatedId: "" };

function renderDevStackConfigHtml(cfg) {
  if (!cfg || !cfg.ok) {
    return `<p class="muted small">${escapeHtml(cfg?.error || "Could not load configuration.")}</p>`;
  }
  const pathRows = [
    `<li><strong>Stack root</strong> — <code>${escapeHtml(cfg.stack_dir || "")}</code></li>`,
    `<li><strong>Compose project</strong> — <code>${escapeHtml(cfg.compose_project || "")}</code></li>`,
    `<li><strong>Platform registry</strong> — <code>${escapeHtml(cfg.platform_registry_path || "")}</code></li>`,
  ];
  const related = (cfg.related_files || [])
    .map((r) => {
      const btn = `<button type="button" class="ctrl-act ctrl-act--ops dev-stack-view-related" data-related-id="${escapeAttr(r.id)}">${r.editable ? "Edit" : "View"}</button>`;
      return `<li class="devstack-config-file-row"><span><code>${escapeHtml(r.path || "")}</code> <span class="muted small">${escapeHtml(r.description || "")}</span></span>${btn}</li>`;
    })
    .join("");
  const files = (cfg.files || [])
    .map((f) => {
      return `<div class="devstack-config-file-row"><code>${escapeHtml(f.path)}</code><button type="button" class="ctrl-act ctrl-act--ops dev-stack-edit-file" data-file-path="${escapeAttr(f.path)}">Edit</button></div>`;
    })
    .join("");
  const notes = (cfg.notes || []).map((n) => `<li class="muted small">${escapeHtml(n)}</li>`).join("");
  return `<ul class="devstack-config-paths">${pathRows.join("")}</ul>
    ${related ? `<p class="muted small"><strong>Hosting / platform (shared)</strong></p><ul class="devstack-config-files">${related}</ul>` : ""}
    ${files ? `<p class="muted small"><strong>Stack files</strong> (under <code>${escapeHtml(cfg.stack_dir || "")}</code>)</p><div class="devstack-config-files devstack-config-files--grid">${files}</div>` : ""}
    ${notes ? `<ul class="devstack-access__notes">${notes}</ul>` : ""}`;
}

async function loadDevStackConfigPanel(stackId, bodyEl) {
  if (!bodyEl || bodyEl.dataset.loaded === "1") return;
  bodyEl.innerHTML = '<p class="muted small">Loading configuration…</p>';
  try {
    const cfg = await fetchPlatformJson(`/api/dev-stacks/${encodeURIComponent(stackId)}/config`);
    bodyEl.innerHTML = renderDevStackConfigHtml(cfg);
    bodyEl.dataset.loaded = "1";
    bodyEl.querySelectorAll(".dev-stack-edit-file").forEach((btn) => {
      btn.addEventListener("click", () =>
        openDevStackFileEditor(stackId, btn.getAttribute("data-file-path") || "", { readOnly: false }),
      );
    });
    bodyEl.querySelectorAll(".dev-stack-view-related").forEach((btn) => {
      btn.addEventListener("click", () =>
        openDevStackFileEditor(stackId, "", {
          readOnly: true,
          relatedId: btn.getAttribute("data-related-id") || "",
        }),
      );
    });
  } catch (e) {
    bodyEl.innerHTML = `<p class="muted small devstack-status-line--error">${escapeHtml(String(e.message || e))}</p>`;
  }
}

function closeDevStackFileEditor() {
  const overlay = document.getElementById("devStackFileOverlay");
  const editor = document.getElementById("devStackFileEditor");
  if (editor) editor.value = "";
  devStackFileEditorState = { stackId: "", path: "", readOnly: false, relatedId: "" };
  if (overlay) {
    overlay.hidden = true;
    overlay.classList.add("is-hidden");
  }
}

async function openDevStackFileEditor(stackId, relPath, { readOnly = false, relatedId = "" } = {}) {
  const overlay = document.getElementById("devStackFileOverlay");
  const titleEl = document.getElementById("devStackFileTitle");
  const pathEl = document.getElementById("devStackFilePath");
  const hintEl = document.getElementById("devStackFileHint");
  const editor = document.getElementById("devStackFileEditor");
  const saveBtn = document.getElementById("devStackFileSave");
  const statusEl = document.getElementById("devStackFileStatus");
  if (!overlay || !editor) return;
  devStackFileEditorState = { stackId, path: relPath, readOnly, relatedId };
  if (statusEl) statusEl.textContent = "";
  if (titleEl) titleEl.textContent = readOnly ? "View file" : "Edit file";
  if (pathEl) pathEl.textContent = relPath || relatedId || "";
  if (hintEl) {
    if (readOnly) {
      hintEl.textContent = "Read-only. Edit stack files below or change compose, then Start to regenerate Traefik routes.";
      hintEl.classList.remove("is-hidden");
    } else {
      hintEl.classList.add("is-hidden");
      hintEl.textContent = "";
    }
  }
  if (saveBtn) saveBtn.hidden = !!readOnly;
  editor.readOnly = !!readOnly;
  editor.value = "Loading…";
  overlay.hidden = false;
  overlay.classList.remove("is-hidden");
  try {
    let data;
    if (relatedId) {
      data = await fetchPlatformJson(
        `/api/dev-stacks/${encodeURIComponent(stackId)}/related-files/${encodeURIComponent(relatedId)}`,
      );
      if (pathEl && data.path) pathEl.textContent = data.path;
    } else {
      data = await fetchPlatformJson(
        `/api/dev-stacks/${encodeURIComponent(stackId)}/files?path=${encodeURIComponent(relPath)}`,
      );
    }
    editor.value = data.content ?? "";
    if (data.missing) {
      if (statusEl) statusEl.textContent = "File does not exist yet.";
    }
  } catch (e) {
    editor.value = "";
    if (statusEl) statusEl.textContent = String(e.message || e);
  }
}

async function saveDevStackFileEditor() {
  const { stackId, path, readOnly } = devStackFileEditorState;
  const statusEl = document.getElementById("devStackFileStatus");
  const editor = document.getElementById("devStackFileEditor");
  if (readOnly || !stackId || !path || !editor) return;
  if (dashboardTokenRequired() && !controlToken()) {
    if (statusEl) statusEl.textContent = "Control token required — set it on the Control tab.";
    return;
  }
  if (statusEl) statusEl.textContent = "Saving…";
  try {
    const data = await fetchPlatformJson(`/api/dev-stacks/${encodeURIComponent(stackId)}/files`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, content: editor.value, token: controlToken() }),
    });
    const logs = Array.isArray(data.logs) ? data.logs.join(" ") : "";
    if (statusEl) statusEl.textContent = data.ok ? `Saved.${logs ? ` ${logs}` : ""}` : data.error || "Save failed";
    if (data.ok) {
      document.querySelectorAll(`.devstack-advanced-panel__body[data-stack-id="${CSS.escape(stackId)}"]`).forEach((el) => {
        el.dataset.loaded = "0";
      });
    }
  } catch (e) {
    if (statusEl) statusEl.textContent = String(e.message || e);
  }
}

function wireDevStackFileEditorModal() {
  if (document.body.dataset.devStackFileEditorBound === "1") return;
  document.body.dataset.devStackFileEditorBound = "1";
  ["devStackFileClose", "devStackFileCancel"].forEach((id) => {
    document.getElementById(id)?.addEventListener("click", closeDevStackFileEditor);
  });
  document.getElementById("devStackFileSave")?.addEventListener("click", saveDevStackFileEditor);
  document.getElementById("devStackFileOverlay")?.addEventListener("click", (ev) => {
    if (ev.target?.id === "devStackFileOverlay") closeDevStackFileEditor();
  });
}

function renderDevStacksList(stacks) {
  const el = document.getElementById("devStacksList");
  if (!el) return;
  if (!stacks.length) {
    el.innerHTML = devStackEmptyListHtml();
    return;
  }
  el.innerHTML = stacks
    .map((s) => {
      const rawId = String(s.id);
      const id = escapeHtml(rawId);
      const name = escapeHtml(String(s.name || s.id));
      const state = String(s.state || "unknown");
      const st = escapeHtml(state);
      const template = s.template ? escapeHtml(String(s.template)) : "";
      const sampleTag = s.sample_data ? " · sample" : "";
      const comps = (s.components || [])
        .map((c) => escapeHtml(`${c.id || "?"}:${c.version || "?"}`))
        .join(", ");
      const rowCls = devStackIsRunning(state) ? "platform-stack-row--running" : "platform-stack-row--stopped";
      const cardBody = renderDevStackCardBody(s.access, rawId);
      return `<div class="platform-stack-row platform-stack-row--wide ${rowCls}" data-id="${id}" data-stack-id="${escapeAttr(rawId)}" data-stack-state="${escapeAttr(state)}">
        <header class="platform-stack-row__header">
          <div><strong>${name}</strong> <span class="muted small">(${id}) — ${st}${template ? ` · ${template}` : ""}${sampleTag}</span></div>
          ${comps ? `<div class="muted small platform-stack-row__comps">${comps}</div>` : ""}
        </header>
        ${cardBody}
        <details class="devstack-advanced-panel">
          <summary class="devstack-advanced-panel__summary">Advanced — configuration &amp; files</summary>
          <div class="devstack-advanced-panel__body" data-stack-id="${escapeAttr(rawId)}"></div>
        </details>
        <div class="platform-svc-tile__actions platform-svc-tile__actions--devstack">
          ${devStackRowActionButtons(rawId, state)}
        </div>
      </div>`;
    })
    .join("");
  el.querySelectorAll(".dev-stack-act").forEach((btn) => {
    btn.addEventListener("click", () => devStackAction(btn));
  });
  el.querySelectorAll(".dev-stack-reset-admin").forEach((btn) => {
    btn.addEventListener("click", () => devStackResetAdmin(btn.getAttribute("data-id") || ""));
  });
  el.querySelectorAll(".devstack-copy-magic-link").forEach((btn) => {
    btn.addEventListener("click", () => {
      devStackCopyMagicLink(btn.getAttribute("data-url") || "", btn.getAttribute("data-label") || "Link");
    });
  });
  el.querySelectorAll(".devstack-advanced-panel").forEach((panel) => {
    panel.addEventListener("toggle", () => {
      if (!panel.open) return;
      const body = panel.querySelector(".devstack-advanced-panel__body");
      const stackId = body?.getAttribute("data-stack-id") || "";
      if (stackId && body) loadDevStackConfigPanel(stackId, body);
    });
  });
  wireDevStackFileEditorModal();
}

let devStackLogBuffer = "";

function openDevStackLogPanel(title = "Operation log") {
  const wrap = document.getElementById("devStackLogWrap");
  const titleEl = document.getElementById("devStackLogTitle");
  const pre = document.getElementById("devStackLog");
  devStackLogBuffer = "";
  if (pre) {
    pre.textContent = "";
    pre.classList.remove("devstack-status-log--error");
  }
  if (titleEl) titleEl.textContent = title;
  if (wrap) {
    wrap.hidden = false;
    wrap.classList.remove("is-hidden");
  }
}

function closeDevStackLogPanel() {
  const wrap = document.getElementById("devStackLogWrap");
  const pre = document.getElementById("devStackLog");
  devStackLogBuffer = "";
  if (pre) {
    pre.textContent = "";
    pre.classList.remove("devstack-status-log--error");
  }
  if (wrap) {
    wrap.hidden = true;
    wrap.classList.add("is-hidden");
  }
  setDevStackStatus("");
}

function appendDevStackLog(text) {
  if (text === undefined || text === null) return;
  const chunk = typeof text === "string" ? text : String(text);
  devStackLogBuffer += chunk;
  const pre = document.getElementById("devStackLog");
  if (!pre) return;
  pre.textContent = devStackLogBuffer;
  pre.scrollTop = pre.scrollHeight;
}

function setDevStackStatus(message, { error = false } = {}) {
  const statusEl = document.getElementById("devStackStatus");
  if (!statusEl) return;
  if (!message) {
    statusEl.textContent = "";
    statusEl.innerHTML = "";
    return;
  }
  statusEl.textContent = message;
  if (error) statusEl.classList.add("devstack-status-line--error");
  else statusEl.classList.remove("devstack-status-line--error");
}

async function streamDevStackAction(stackId, action) {
  const tok = controlToken();
  setDevStackActionBusy(stackId, action);
  openDevStackLogPanel(`${action} · ${stackId}`);
  appendDevStackLog(`Running ${action} on stack ${stackId}…\n\n`);
  try {
  const res = await fetch(`/api/dev-stacks/${encodeURIComponent(stackId)}/action/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Control-Token": tok },
    body: JSON.stringify({ action, token: tok }),
    cache: "no-store",
  });
  if (!res.ok) {
    let err = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      err = data.error || err;
    } catch (_) {
      /* ignore */
    }
    appendDevStackLog(`\n[error] ${err}\n`);
    const pre = document.getElementById("devStackLog");
    if (pre) pre.classList.add("devstack-status-log--error");
    return { ok: false, error: err };
  }
  let final = null;
  if (res.body && typeof res.body.getReader === "function") {
    final = await readNdjsonLinesFromReader(res.body.getReader(), appendDevStackLog);
  } else {
    const txt = await res.text();
    final = parseNdjsonFromFullText(txt, appendDevStackLog);
  }
  if (!final) {
    appendDevStackLog("\n[error] No result from stream.\n");
    const pre = document.getElementById("devStackLog");
    if (pre) pre.classList.add("devstack-status-log--error");
    return { ok: false, error: "no result from stream" };
  }
  if (!final.ok) {
    const pre = document.getElementById("devStackLog");
    if (pre) pre.classList.add("devstack-status-log--error");
  }
  return final;
  } finally {
    setDevStackActionBusy(null);
  }
}

async function devStackResetAdmin(stackId) {
  const statusEl = document.getElementById("devStackStatus");
  if (dashboardTokenRequired() && !controlToken()) {
    setDevStackStatus("Control token required — set it on the Control tab.");
    return;
  }
  try {
    const res = await fetch(`/api/dev-stacks/${encodeURIComponent(stackId)}/reset-admin`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: controlToken() }),
    });
    const data = await res.json();
    setDevStackStatus(
      data.ok ? data.message || "Admin reset." : data.error || JSON.stringify(data),
      { error: !data.ok },
    );
    if (data.ok) await loadPlatformTab();
  } catch (e) {
    setDevStackStatus(String(e.message || e), { error: true });
  }
}

async function devStackAction(btn) {
  if (btn.disabled && btn.getAttribute("data-action") !== "destroy") return;
  const id = btn.getAttribute("data-id");
  let action = btn.getAttribute("data-action");
  if (dashboardTokenRequired() && !controlToken()) {
    setDevStackStatus("Control token required — set it on the Control tab.");
    return;
  }
  if (action === "repair") {
    const ok = await showAppConfirm({
      title: `Repair ${id}`,
      message:
        "Applies current LEco configuration fixes (images, edge configs), refreshes Traefik routes, reconnects lh-network, runs docker compose up -d, and repairs public URLs. Does not revert manual edits in Advanced. Keeps Docker volumes.",
      confirmText: "Repair",
    });
    if (!ok) return;
  }
  if (action === "reinstall" || action === "redeploy") {
    const ok = await showAppConfirm({
      title: `Reinstall ${id}`,
      message:
        "Regenerates all stack files from the template (reverts manual edits in Advanced), deletes Docker volumes for a clean database and app data, reconfigures the app, then deploys again. First boot can take 15–30 minutes for Magento with sample data.",
      confirmText: "Reinstall",
      danger: true,
    });
    if (!ok) return;
    if (action === "redeploy") action = "reinstall";
  }
  if (action === "destroy") {
    const ok = await showAppConfirm({
      title: `Destroy ${id}`,
      message:
        "Stops all stack containers, deletes Docker volumes (database and app data), removes generated files under platform/dev-stacks/, clears platform config, and updates Traefik routes. This cannot be undone.",
      confirmText: "Destroy",
      danger: true,
    });
    if (!ok) return;
  }
  try {
    const data = await streamDevStackAction(id, action);
    const head =
      action === "destroy"
        ? data.ok
          ? `Destroyed stack ${id}.`
          : `Destroy failed for ${id}.`
        : action === "repair"
          ? data.ok
            ? `Repaired stack ${id}.`
            : `Repair failed for ${id}.`
          : action === "reinstall" || action === "redeploy"
            ? data.ok
              ? `Reinstalled stack ${id}.`
              : `Reinstall failed for ${id}.`
            : data.ok
              ? `${action === "start" ? "Started" : "Stopped"} ${id}.`
              : `${action === "start" ? "Start" : "Stop"} failed for ${id}.`;
    const warn = data.public_url_repair_warning ? ` Warning: ${data.public_url_repair_warning}` : "";
    setDevStackStatus(`${head}${warn} See log below — Close clears it.`, { error: !data.ok });
    _hostedDevStacksCache = null;
    await loadPlatformTab();
  } catch (e) {
    appendDevStackLog(`\n[error] ${String(e.message || e)}\n`);
    const pre = document.getElementById("devStackLog");
    if (pre) pre.classList.add("devstack-status-log--error");
    setDevStackStatus(String(e.message || e), { error: true });
  }
}

function initPlatformTab() {
  bindPlatformDeploymentAutoSelect();
  const saveBtn = document.getElementById("platformSaveConfig");
  const applyBtn = document.getElementById("platformApplyTraefik");
  const logCloseBtn = document.getElementById("devStackLogClose");
  if (logCloseBtn && logCloseBtn.dataset.bound !== "1") {
    logCloseBtn.dataset.bound = "1";
    logCloseBtn.addEventListener("click", () => closeDevStackLogPanel());
  }
  const createBtn = document.getElementById("devStackCreate");
  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      const statusEl = document.getElementById("platformConfigStatus");
      try {
        const token = controlToken();
        const cfgData = await fetchPlatformJson("/api/platform/config");
        const cfg = cfgData.config || {};
        cfg.base_domain = document.getElementById("platformBaseDomain")?.value || "lh";
        cfg.deployment_mode = document.getElementById("platformDeploymentMode")?.value || "local";
        cfg.tls = cfg.tls || {};
        cfg.tls.mode = document.getElementById("platformTlsMode")?.value || "mkcert";
        const profSel = document.getElementById("platformInstallProfile")?.value?.trim();
        if (profSel) cfg.install_profile = profSel;
        const data = await fetchPlatformJson("/api/platform/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ config: cfg, token }),
        });
        if (statusEl) statusEl.textContent = data.ok ? "Saved." : JSON.stringify(data);
        loadPlatformTab();
      } catch (e) {
        if (statusEl) statusEl.textContent = String(e.message || e);
      }
    });
  }
  if (applyBtn) {
    applyBtn.addEventListener("click", async () => {
      const statusEl = document.getElementById("platformConfigStatus");
      try {
        const token = controlToken();
        const data = await fetchPlatformJson("/api/platform/traefik/apply", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token }),
        });
        if (statusEl) statusEl.textContent = JSON.stringify(data);
      } catch (e) {
        if (statusEl) statusEl.textContent = String(e.message || e);
      }
    });
  }
  const presetSel = document.getElementById("devStackPreset");
  if (presetSel && presetSel.dataset.bound !== "1") {
    presetSel.dataset.bound = "1";
    presetSel.addEventListener("change", () => {
      const key = presetSel.value;
      if (key) applyDevStackPreset(key);
      else updateDevStackPresetUi("");
      refreshDevStacksEmptyHint();
    });
  }
  const sampleCb = document.getElementById("devStackSampleData");
  if (sampleCb && sampleCb.dataset.bound !== "1") {
    sampleCb.dataset.bound = "1";
    sampleCb.addEventListener("change", refreshDevStacksEmptyHint);
  }
  if (createBtn) {
    createBtn.addEventListener("click", async () => {
      const statusEl = document.getElementById("devStackStatus");
      const id = document.getElementById("devStackId")?.value?.trim();
      const name = document.getElementById("devStackName")?.value?.trim() || id;
      const presetKey = document.getElementById("devStackPreset")?.value?.trim() || "";
      const preset = presetKey ? getDevStackPreset(presetKey) : null;
      const components = collectDevStackComponents();
      if (!id) {
        if (statusEl) statusEl.textContent = "Stack id is required.";
        return;
      }
      if (!preset && !components.length) {
        if (statusEl) statusEl.textContent = "Select a preset or at least one component.";
        return;
      }
      if (dashboardTokenRequired() && !controlToken()) {
        if (statusEl) statusEl.textContent = "Control token required — set it on the Control tab.";
        return;
      }
      createBtn.disabled = true;
      if (statusEl) statusEl.textContent = `Creating stack ${id}…`;
      try {
        const token = controlToken();
        const body = {
          id,
          name,
          token,
          sample_data: !!document.getElementById("devStackSampleData")?.checked,
        };
        if (presetKey) body.preset = presetKey;
        else body.components = components;
        const data = await fetchPlatformJson("/api/dev-stacks", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (data.ok) {
          if (statusEl) statusEl.textContent = `Created stack “${id}”. Click Start on the stack below to run containers.`;
        } else {
          if (statusEl) statusEl.textContent = data.error || JSON.stringify(data);
        }
        _hostedDevStacksCache = null;
        await loadPlatformTab();
      } catch (e) {
        if (statusEl) statusEl.textContent = String(e.message || e);
      } finally {
        createBtn.disabled = false;
      }
    });
  }
  ["devStackId", "devStackName"].forEach((fieldId) => {
    const field = document.getElementById(fieldId);
    if (field && field.dataset.bound !== "1") {
      field.dataset.bound = "1";
      field.addEventListener("input", refreshDevStacksEmptyHint);
    }
  });
}

initPlatformTab();

bootstrap();
