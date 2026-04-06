/**
 * LEco Docker preloader — patches config.js exports at require-time so
 * hardcoded localhost URIs resolve to Docker service names.
 *
 * Usage:  node -r /opt/leco/leco-docker-preload.js server.js
 *
 * HOW IT WORKS
 *   Node's Module._load is monkey-patched. When any non-node_modules file
 *   does require('./config'), we intercept the returned object and overwrite
 *   keys that match LECO_* environment variables.
 *
 *   This avoids modifying the upstream repo — all patching happens at runtime
 *   via environment variables set in docker-compose.leco-hosting.yml.
 *
 * ADDING NEW PATCHES
 *   1. Add a LECO_* env var in docker-compose.leco-hosting.yml
 *   2. Add a corresponding if-block below that patches the config key
 *   3. The preloader logs each patch to stdout for debugging
 *
 * ENVIRONMENT VARIABLES (set by docker-compose.leco-hosting.yml):
 *   LECO_MONGO_URI           — e.g. mongodb://mongo:27017/
 *   LECO_REDIS_URI           — e.g. redis://redis:6379
 *   LECO_REDIS_HOST          — e.g. redis
 *   LECO_REDIS_PORT          — e.g. 6379
 *   LECO_VARNISH_HOST        — e.g. varnish  (Docker service name)
 *   LECO_OWN_DOMAINS         — e.g. my-app.lh  (comma-separated hostnames)
 *   LECO_CHROME_USER_DATA_DIR — e.g. /mnt/tmpfs-user-data
 *
 * This file lives in hosting/app-available/<slug>/ and is volume-mounted
 * into /opt/leco/ (NOT /app — that's the source bind mount).
 */

const Module = require('module');
const origLoad = Module._load;

Module._load = function (request, parent, isMain) {
  const result = origLoad.apply(this, arguments);

  // Only patch the app's own config module, not any node_modules/config.
  // Adjust the require path ('./config') to match your app's config import.
  if (request === './config' && parent && parent.filename && !parent.filename.includes('node_modules')) {
    const env = process.env;

    // ── MongoDB ──
    if (env.LECO_MONGO_URI && result.MONGODB_URI !== undefined) {
      result.MONGODB_URI = env.LECO_MONGO_URI;
      console.log('[leco-preload] MONGODB_URI → ' + env.LECO_MONGO_URI);
    }

    // ── Redis ──
    if (env.LECO_REDIS_URI && result.REDIS_URI !== undefined) {
      result.REDIS_URI = env.LECO_REDIS_URI;
      console.log('[leco-preload] REDIS_URI → ' + env.LECO_REDIS_URI);
    }
    if (result.REDIS_CREDS) {
      if (env.LECO_REDIS_HOST) result.REDIS_CREDS.HOST = env.LECO_REDIS_HOST;
      if (env.LECO_REDIS_PORT) result.REDIS_CREDS.PORT = env.LECO_REDIS_PORT;
      if (env.LECO_REDIS_HOST || env.LECO_REDIS_PORT) {
        console.log('[leco-preload] REDIS_CREDS → ' + result.REDIS_CREDS.HOST + ':' + result.REDIS_CREDS.PORT);
      }
    }

    // ── Varnish ──
    if (env.LECO_VARNISH_HOST && result.varnishHost !== undefined) {
      result.varnishHost = env.LECO_VARNISH_HOST;
      console.log('[leco-preload] varnishHost → ' + env.LECO_VARNISH_HOST);
    }

    // ── Chrome / Puppeteer ──
    if (env.LECO_CHROME_USER_DATA_DIR && result.chromeUserDataDir !== undefined) {
      result.chromeUserDataDir = env.LECO_CHROME_USER_DATA_DIR;
      console.log('[leco-preload] chromeUserDataDir → ' + env.LECO_CHROME_USER_DATA_DIR);
    }

    // ── OWN_DOMAINS (hostname → allowed modules map) ──
    // Add *.lh hostnames so the app accepts requests on them.
    if (env.LECO_OWN_DOMAINS && result.OWN_DOMAINS) {
      env.LECO_OWN_DOMAINS.split(',').forEach(function (h) {
        h = h.trim();
        if (h && !result.OWN_DOMAINS[h]) {
          // List the modules/features your app exposes per hostname.
          // Use ['*'] to allow all, or list specific module names.
          result.OWN_DOMAINS[h] = ['*'];
          console.log('[leco-preload] OWN_DOMAINS += ' + h);
        }
      });
    }

    // ── Add your own patches below ──
    // Example: patch a custom API base URL
    // if (env.LECO_API_BASE_URL && result.apiBaseUrl !== undefined) {
    //   result.apiBaseUrl = env.LECO_API_BASE_URL;
    //   console.log('[leco-preload] apiBaseUrl → ' + env.LECO_API_BASE_URL);
    // }
  }

  return result;
};
