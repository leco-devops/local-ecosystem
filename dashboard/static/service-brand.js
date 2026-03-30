/**
 * One accent colour + icon per service, shared across Control + Infrastructure cards.
 * Keys are stable "brand" slugs (not target ids).
 */
(function (g) {
  const CONTROL_TARGET_BRAND = {
    "cf-minio": "minio",
    "cf-valkey": "valkey",
    "cf-r2-adapter": "r2",
    "cf-kv-adapter": "kv",
    "cf-d1-adapter": "d1",
    "cf-workers-runtime": "workers",
    "cf-autoscale-demo": "autoscale-demo",
    "cf-autoscaler": "autoscaler",
    "ai-traefik": "traefik",
    "ai-open-webui": "open-webui",
    "ai-ollama": "ollama",
    "ai-n8n": "n8n",
    "ai-postgres": "postgres",
    "ai-dashboard": "dashboard",
    "ai-cloudflare-local": "cf-compose",
    "stack-cf-all": "cf-stack",
  };

  const CONTAINER_BRAND = {
    traefik: "traefik",
    "open-webui": "open-webui",
    n8n: "n8n",
    ollama: "ollama",
    "service-dashboard": "dashboard",
    n8n_postgres: "postgres",
    "r2-adapter": "r2",
    "kv-adapter": "kv",
    "d1-adapter": "d1",
    autoscaler: "autoscaler",
    minio: "minio",
    valkey: "valkey",
    "workers-runtime": "workers",
    "autoscale-demo": "autoscale-demo",
  };

  const SERVICE_NAME_BRAND = {
    Traefik: "traefik",
    "Open WebUI": "open-webui",
    n8n: "n8n",
    Ollama: "ollama",
    "Service Dashboard": "dashboard",
    PostgreSQL: "postgres",
    "R2 (Cloudflare local)": "r2",
    "KV (Cloudflare local)": "kv",
    "D1 (Cloudflare local)": "d1",
    Autoscaler: "autoscaler",
    "MinIO Console": "minio",
    "Workers (Miniflare)": "workers",
  };

  const EMOJI = {
    traefik: "🔀",
    "open-webui": "🧠",
    n8n: "⚡",
    ollama: "🦙",
    dashboard: "📊",
    postgres: "🐘",
    r2: "📦",
    kv: "🔑",
    d1: "🗃️",
    autoscaler: "📈",
    minio: "💧",
    valkey: "🧱",
    workers: "☁️",
    "autoscale-demo": "🎯",
    "cf-compose": "🧩",
    "cf-stack": "🌐",
    default: "📌",
  };

  const svg = (pathD, viewBox = "0 0 24 24") =>
    `<svg class="svc-svg-icon__shape" viewBox="${viewBox}" width="28" height="28" aria-hidden="true" focusable="false"><path fill="currentColor" d="${pathD}"/></svg>`;

  const ICONS = {
    default: svg(
      "M4 14a4 4 0 1 1 4 4H6a2 2 0 0 0-2-2v-2zm14-2a4 4 0 0 1 4 4v2a2 2 0 0 0-2 2h-2a4 4 0 0 1-4-4 4 4 0 0 1 4-4zm-8 2a2 2 0 1 1 0 4 2 2 0 0 1 0-4zM12 2a2 2 0 0 1 2 2v2a4 4 0 0 1 4 4h2a2 2 0 0 1 2 2v2h-2a4 4 0 0 1-4 4 4 4 0 0 1-4-4V8a4 4 0 0 1 4-4V2h2z",
    ),
    traefik: svg(
      "M12 2L2 7l10 5 10-5-10-5zm0 8.2L4.7 7 12 3.8 19.3 7 12 10.2zm-8 3.1L12 17l8-3.7v5.4L12 22l-8-3.3v-5.4z",
    ),
    "open-webui": svg(
      "M12 2a7 7 0 0 1 7 7c0 5-7 13-7 13S5 14 5 9a7 7 0 0 1 7-7zm0 3.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7z",
    ),
    n8n: svg("M13 2L3 14h7l-1 8 10-12h-7l1-8z"),
    ollama: svg(
      "M8.5 4C6 4 4 6 4 8.5c0 3 2.5 5.5 6 8.5 3.5-3 6-5.5 6-8.5C16 6 14 4 11.5 4c-1 0-2 .4-3 1.1C7.5 4.4 6.5 4 8.5 4h0zM9 10.2c.5-.6 1.2-1 2-1s1.5.4 2 1c-.3 1.2-1.3 2-2.5 2s-2.2-.8-2.5-2z",
    ),
    dashboard: svg(
      "M3 3h8v8H3V3zm10 0h8v5h-8V3zM3 13h5v8H3v-8zm7 5h11v3H10v-3zm8-5h3v8h-3v-8z",
    ),
    postgres: svg(
      "M12 3c-4 0-7 1.8-7 4v10c0 2 3 4 7 4s7-2 7-4V7c0-2.2-3-4-7-4zm0 2c2.8 0 5 1 5 2s-2.2 2-5 2-5-1-5-2 2.2-2 5-2zm-5 5.2c1.2.8 2.8 1.3 5 1.3s3.8-.5 5-1.3V17c0 1-2.2 2-5 2s-5-1-5-2v-6.8z",
    ),
    r2: svg(
      "M5 4h14v4H5V4zm0 6h10l4 4v6H5v-10zm4 2v2h6v-2H9zm0 4v2h4v-2H9z",
    ),
    kv: svg(
      "M6 3h12v18H6V3zm3 3v2h6V6H9zm0 4v2h6v-2H9zm0 4v2h6v-2H9zm0 4v2h4v-2H9z",
    ),
    d1: svg(
      "M4 6h16v2H4V6zm0 4h16v10H4V10zm3 3v6h10v-6H7zm2 2h6v2H9v-2z",
    ),
    autoscaler: svg(
      "M3 17h2v4H3v-4zm4-5h2v9H7v-9zm4-4h2v13h-2V8zm4-3h2v16h-2V5zm4 2h2v14h-2V7z",
    ),
    minio: svg(
      "M12 2l8 4v6c0 5-3.5 9.5-8 10-4.5-.5-8-5-8-10V6l8-4zm0 3.2L7 7.4V12c0 3.2 2.2 6.2 5 7 2.8-.8 5-3.8 5-7V7.4l-5-2.2z",
    ),
    valkey: svg(
      "M12 2C8 2 5 4 5 7v10c0 2 2.5 4 7 5 4.5-1 7-3 7-5V7c0-3-3-5-7-5zm0 3c2 0 4 .8 4 2s-2 2-4 2-4-.8-4-2 2-2 4-2zm-4 6h8v2H8v-2zm0 4h6v2H8v-2z",
    ),
    workers: svg(
      "M12 2a5 5 0 0 1 5 5v2h-2V7a3 3 0 0 0-6 0v8a3 3 0 0 0 6 0v-1h2v1a5 5 0 0 1-10 0V7a5 5 0 0 1 5-5zm-1 14h2v6h-2v-6z",
    ),
    "autoscale-demo": svg(
      "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zm8-2v4h-2V8h-2V6h4zm-2 12h2v4h-4v-2h2v-2zM4 6h2v2H4v4H2V6h2zm0 12h2v2h4v2H4v-4z",
    ),
    "cf-compose": svg(
      "M4 5h6v6H4V5zm10 0h6v6h-6V5zM4 13h6v6H4v-6zm10 4h6v2h-6v-2z",
    ),
    "cf-stack": svg(
      "M12 2l10 5-10 5L2 7l10-5zm0 8.5l6.5-3.25L12 4 5.5 7.25 12 10.5zm-8 4l8 4 8-4v6l-8 4-8-4v-6z",
    ),
  };

  function getBrandForControlTarget(t) {
    if (!t || !t.id) return "default";
    return CONTROL_TARGET_BRAND[t.id] || "default";
  }

  function getBrandForManagedService(s) {
    if (!s) return "default";
    if (SERVICE_NAME_BRAND[s.service]) return SERVICE_NAME_BRAND[s.service];
    if (s.container && CONTAINER_BRAND[s.container]) return CONTAINER_BRAND[s.container];
    return "default";
  }

  function emojiFor(brand) {
    return EMOJI[brand] || EMOJI.default;
  }

  function iconHtml(brand) {
    const b = ICONS[brand] ? brand : "default";
    return `<span class="svc-svg-icon">${ICONS[b]}</span>`;
  }

  const SAFE_ACTION_ORDER = ["start", "stop", "restart", "pause", "unpause", "deploy", "recreate", "backup"];
  const DANGER_ACTIONS = ["remove", "reset"];

  function partitionControlActions(actions) {
    const list = actions || [];
    const set = new Set(list);
    const safeOrdered = SAFE_ACTION_ORDER.filter((a) => set.has(a));
    const danger = DANGER_ACTIONS.filter((a) => set.has(a));
    const safeExtras = list.filter((a) => !DANGER_ACTIONS.includes(a) && !safeOrdered.includes(a));
    return { safe: [...safeOrdered, ...safeExtras], danger };
  }

  /**
   * Button CSS by risk: safe (green), caution (amber), ops (blue), destructive (red).
   */
  function actionButtonClasses(action) {
    const a = (action || "").toLowerCase();
    const base = "ctrl-act";
    if (a === "remove" || a === "reset") return `${base} danger ctrl-act--destructive`;
    if (a === "backup" || a === "start" || a === "unpause") return `${base} ctrl-act--safe`;
    if (a === "stop" || a === "pause") return `${base} ctrl-act--caution`;
    return `${base} ctrl-act--ops`;
  }

  g.SERVICE_BRAND_UI = {
    getBrandForControlTarget,
    getBrandForManagedService,
    emojiFor,
    iconHtml,
    partitionControlActions,
    actionButtonClasses,
  };
})(typeof window !== "undefined" ? window : globalThis);
