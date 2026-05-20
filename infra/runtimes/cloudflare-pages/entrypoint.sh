#!/bin/sh
# LEco DevOps — Cloudflare Pages local runtime (wrangler pages dev).
#
#   LECO_WRANGLER_CONFIG  - e.g. /app/infra/wrangler.pages.toml
#   LECO_PAGES_ASSET_DIR  - optional override for static assets (absolute in container)
#   LECO_PORT             - listen port (default 8791)

set -eu

PORT="${LECO_PORT:-8791}"
CONFIG="${LECO_WRANGLER_CONFIG:-/app/infra/wrangler.pages.toml}"
ASSET_DIR="${LECO_PAGES_ASSET_DIR:-}"
APP="${LECO_APP_SLUG:-app}"
RID="${LECO_RUNTIME_ID:-pages}"

log() {
    printf '[leco-runtime cf-pages] %s\n' "$*" >&2
}

log "starting (app=${APP} runtime=${RID} port=${PORT})"

if [ ! -f "$CONFIG" ]; then
    log "ERROR: wrangler config not found at ${CONFIG}"
    exit 64
fi

CONFIG_DIR="$(dirname "$CONFIG")"
cd "$CONFIG_DIR"

if [ -z "$ASSET_DIR" ]; then
    ASSET_DIR="$(grep -E '^[[:space:]]*pages_build_output_dir[[:space:]]*=' "$CONFIG" 2>/dev/null \
        | head -1 \
        | sed -E 's/^[^=]*=[[:space:]]*"?([^"#]+)"?.*/\1/' \
        | tr -d '\r' \
        | sed 's/[[:space:]]*$//')"
    ASSET_DIR="${ASSET_DIR%\"}"
    ASSET_DIR="${ASSET_DIR#\"}"
fi

if [ -z "$ASSET_DIR" ]; then
    log "ERROR: pages_build_output_dir not set in ${CONFIG} and LECO_PAGES_ASSET_DIR empty"
    exit 66
fi

case "$ASSET_DIR" in
    /*) ;;
    *) ASSET_DIR="$CONFIG_DIR/$ASSET_DIR" ;;
esac

# Monorepos (Raven-style): build Vite output when dist/ is missing.
if [ ! -d "$ASSET_DIR" ] || [ -z "$(ls -A "$ASSET_DIR" 2>/dev/null)" ]; then
    log "Pages assets missing at ${ASSET_DIR} — attempting pnpm/npm build from /app"
    if [ -d /app ]; then
        cd /app
        if command -v pnpm >/dev/null 2>&1 && [ -f pnpm-workspace.yaml ]; then
            pnpm install --frozen-lockfile 2>/dev/null || pnpm install || true
            if [ -f apps/dashboard/package.json ]; then
                pnpm --filter @raven/dashboard build 2>/dev/null \
                    || pnpm --filter "./apps/dashboard" build 2>/dev/null \
                    || pnpm --filter "apps/dashboard" run build 2>/dev/null \
                    || true
            fi
        elif [ -f package.json ]; then
            npm install --no-audit --no-fund --progress=false 2>/dev/null || true
            if [ -d apps/dashboard ]; then
                (cd apps/dashboard && npm install --no-audit --no-fund --progress=false && npm run build) 2>/dev/null || true
            fi
        fi
        cd "$CONFIG_DIR"
    fi
fi

if [ ! -d "$ASSET_DIR" ]; then
    log "ERROR: Pages asset directory not found: ${ASSET_DIR}"
    log "Build locally: pnpm --filter @raven/dashboard build  (or set LECO_PAGES_ASSET_DIR)"
    exit 67
fi

PROBE_DIR="/app"
WRANGLER_BIN=""
while [ "$PROBE_DIR" != "/" ]; do
    if [ -x "$PROBE_DIR/node_modules/.bin/wrangler" ]; then
        WRANGLER_BIN="$PROBE_DIR/node_modules/.bin/wrangler"
        break
    fi
    PROBE_DIR="$(dirname "$PROBE_DIR")"
done
if [ -z "$WRANGLER_BIN" ]; then
    WRANGLER_BIN="$(command -v wrangler 2>/dev/null || true)"
fi
if [ -z "$WRANGLER_BIN" ]; then
    log "ERROR: no wrangler binary found"
    exit 65
fi
log "using wrangler: $WRANGLER_BIN"
log "serving Pages assets: $ASSET_DIR"

exec "$WRANGLER_BIN" pages dev "$ASSET_DIR" \
    --port "$PORT" \
    --ip 0.0.0.0
