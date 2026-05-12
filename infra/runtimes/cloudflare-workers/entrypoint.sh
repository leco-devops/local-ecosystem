#!/bin/sh
# LEco DevOps — Cloudflare Workers runtime entrypoint.
#
# Contract (set by dashboard/leco_runtimes/cloudflare_workers.py):
#   LECO_APP_SLUG          - registry/host slug (logs only)
#   LECO_RUNTIME_ID        - runtime id from manifest (logs only)
#   LECO_PORT              - container port wrangler dev binds (default 8787)
#   LECO_WRANGLER_CONFIG   - path to wrangler.toml inside the container
#
# Volume contract:
#   /app                   - bind-mount of upstream source (read-only in spirit;
#                            LEco never writes here — write paths are masked).
#   /app/node_modules      - LEco-owned named volume (npm ci writes here).
#   /app/.wrangler         - LEco-owned named volume (miniflare state).
#   /app/.dev.vars         - optional secrets file mounted read-only.
#
# The image is generic — it never special-cases an individual hosted app.

set -eu

PORT="${LECO_PORT:-8787}"
CONFIG="${LECO_WRANGLER_CONFIG:-/app/wrangler.toml}"
APP="${LECO_APP_SLUG:-app}"
RID="${LECO_RUNTIME_ID:-runtime}"

log() {
    printf '[leco-runtime cf-workers] %s\n' "$*" >&2
}

log "starting (app=${APP} runtime=${RID} port=${PORT})"

if [ ! -f "$CONFIG" ]; then
    log "ERROR: wrangler config not found at ${CONFIG}"
    log "Hint: set infrastructure.runtimes[].config to the wrangler.toml path inside sourceDir."
    exit 64
fi

cd "$(dirname "$CONFIG")"

# Install upstream deps into LEco-owned /app/node_modules volume if empty.
# We deliberately work inside the wrangler.toml directory (not /app) because
# many monorepos keep workers under cloudflare/ or worker/ with their own package.json.
if [ -f "package.json" ]; then
    if [ ! -d node_modules ] || [ -z "$(ls -A node_modules 2>/dev/null)" ]; then
        log "installing node_modules (one-time per LEco volume)…"
        installed=0
        if [ -f "package-lock.json" ] || [ -f "npm-shrinkwrap.json" ]; then
            # Try strict ci first (reproducible). Fall back to npm install when
            # the lockfile is stale (common when LEco runs an older committed
            # lockfile while package.json drifted in the upstream repo). This
            # is the bulletproof behavior — local edge runtimes must boot even
            # when the upstream lockfile lags.
            if npm ci --no-audit --no-fund --progress=false; then
                installed=1
            else
                log "npm ci failed — falling back to npm install (upstream lockfile may be stale)"
            fi
        fi
        if [ "$installed" = "0" ]; then
            npm install --no-audit --no-fund --progress=false
        fi
        log "node_modules ready"
    else
        log "node_modules present, skipping install"
    fi
else
    log "no package.json beside ${CONFIG} — running wrangler with image-global modules"
fi

# Persist miniflare state across container restarts (LEco volume mounted at .wrangler).
mkdir -p .wrangler

# Prefer the upstream's locally-installed wrangler. Two reasons:
#   1. Upstream pinned a specific wrangler version compatible with its bindings.
#   2. The local install brings the right platform-specific `workerd` binary —
#      LEco's image-global wrangler sometimes misses optional deps for the
#      runner's arch (Apple Silicon arm64 / x86_64) and would spawn ENOENT.
WRANGLER_BIN=""
# Walk up from the config dir looking for a local node_modules/.bin/wrangler so
# monorepo layouts (deps hoisted to repo root) also work.
PROBE_DIR="$(pwd)"
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
    log "ERROR: no wrangler binary found (neither local node_modules nor PATH)"
    exit 65
fi
log "using wrangler: $WRANGLER_BIN"

# -----------------------------------------------------------------------------
# D1 schema bootstrap (production-faithful).
#
# Cloudflare's production D1 databases are migrated with
#   `wrangler d1 migrations apply <binding> --remote`
# which reads SQL files from the project's `migrations/` directory (or the
# directory pointed at by `migrations_dir` on the corresponding
# `[[d1_databases]]` block in wrangler.toml).
#
# Locally Wrangler creates an empty SQLite file but never applies these
# migrations — so every app that depends on a D1-backed table (e.g.
# CrawlerVision's `admin_prompt_logs`) returns 500 the first time it queries.
# LEco closes that gap here: for every top-level `[[d1_databases]]` binding we
# find in the sanitized wrangler.toml, we run
#   `wrangler d1 migrations apply <binding> --local`
# which is idempotent (wrangler tracks applied migrations in a `d1_migrations`
# table inside the D1 itself) and bounded by the lifetime of the LEco-owned
# `.wrangler/state` volume.
#
# Override via env:
#   LECO_APPLY_D1_MIGRATIONS=auto   (default) apply when a migrations dir exists
#                          =off    skip entirely (use when upstream applies
#                                   them via a custom script you'd rather run)
#                          =always force the attempt even with no obvious
#                                   migrations dir (lets wrangler error noisily)
# Always-applied tip: failures are logged but do NOT abort the runtime — the
# Worker still boots, the operator can re-apply manually with
# `docker exec leco-rt-<slug>-<rid> sh -c 'cd /app && wrangler d1 migrations apply <db> --local'`.
# -----------------------------------------------------------------------------

D1_APPLY="${LECO_APPLY_D1_MIGRATIONS:-auto}"
D1_BOOTSTRAP_APPLY="${LECO_APPLY_D1_BOOTSTRAP:-auto}"
D1_BOOTSTRAP_DIR="${LECO_D1_BOOTSTRAP_DIR:-/leco-runtime/d1}"

# Extract top-level d1 binding names from the (sanitized) wrangler.toml. We
# intentionally skip `[[env.<name>.d1_databases]]` because wrangler-local does
# not enter env-scoped blocks unless `--env` is passed.
extract_d1_bindings() {
    awk '
        BEGIN { in_d1 = 0 }
        /^[[:space:]]*\[\[d1_databases\]\][[:space:]]*$/ { in_d1 = 1; next }
        /^[[:space:]]*\[/ { in_d1 = 0 }
        in_d1 && /^[[:space:]]*binding[[:space:]]*=/ {
            line = $0
            sub(/^[^"\x27]*["\x27]/, "", line)
            sub(/["\x27].*$/, "", line)
            if (length(line) > 0) print line
        }
    ' "$1"
}

# Locate the bootstrap SQL for a given binding. Order:
#   1. Per-binding file:        <dir>/d1-bootstrap-<BINDING>.sql
#   2. Per-binding lowercase:   <dir>/d1-bootstrap-<binding-lower>.sql
#   3. Global file:             <dir>/d1-bootstrap.sql
# Returns absolute path or empty string.
locate_d1_bootstrap() {
    _binding="$1"
    _dir="$2"
    if [ -d "$_dir" ]; then
        for _name in "d1-bootstrap-${_binding}.sql" \
                     "d1-bootstrap-$(echo "$_binding" | tr '[:upper:]' '[:lower:]').sql" \
                     "d1-bootstrap.sql"; do
            if [ -f "${_dir}/${_name}" ]; then
                printf '%s\n' "${_dir}/${_name}"
                return 0
            fi
        done
    fi
    printf '\n'
    return 1
}

# Default `migrations/` dir, or honor `migrations_dir = "..."` on the first
# top-level [[d1_databases]] block (wrangler scopes it per-database, but in
# practice projects keep one migrations dir per worker).
extract_d1_migrations_dir() {
    awk '
        BEGIN { in_d1 = 0 }
        /^[[:space:]]*\[\[d1_databases\]\][[:space:]]*$/ { in_d1 = 1; next }
        /^[[:space:]]*\[/ { in_d1 = 0 }
        in_d1 && /^[[:space:]]*migrations_dir[[:space:]]*=/ {
            line = $0
            sub(/^[^"\x27]*["\x27]/, "", line)
            sub(/["\x27].*$/, "", line)
            if (length(line) > 0) { print line; exit }
        }
    ' "$1"
}

if [ "$D1_APPLY" != "off" ] || [ "$D1_BOOTSTRAP_APPLY" != "off" ]; then
    D1_BINDINGS="$(extract_d1_bindings "$CONFIG" || true)"
    MIGRATIONS_REL="$(extract_d1_migrations_dir "$CONFIG" || true)"
    [ -z "$MIGRATIONS_REL" ] && MIGRATIONS_REL="migrations"
    MIGRATIONS_DIR="$(pwd)/$MIGRATIONS_REL"

    if [ -n "$D1_BINDINGS" ]; then
        OLD_IFS="$IFS"; IFS='
'
        for BINDING in $D1_BINDINGS; do
            IFS="$OLD_IFS"

            # --- Phase 1: bootstrap SQL (operator-owned base schema) ---
            # Many CF Worker apps keep the bulk of their schema OUTSIDE the
            # migrations/ directory (e.g. as a TypeScript constant the Worker
            # `exec()`s on an admin endpoint, or applied manually to remote
            # D1 once at project genesis). Locally we never get that bootstrap
            # for free, so we let operators drop SQL files under
            # ``hosting/app-available/<slug>/.leco-runtime/<runtime_id>/``
            # which LEco bind-mounts at ``$D1_BOOTSTRAP_DIR``. Idempotency is
            # delegated to the bootstrap SQL itself (use CREATE TABLE
            # IF NOT EXISTS, CREATE INDEX IF NOT EXISTS, INSERT OR IGNORE).
            if [ "$D1_BOOTSTRAP_APPLY" != "off" ]; then
                BOOTSTRAP_FILE="$(locate_d1_bootstrap "$BINDING" "$D1_BOOTSTRAP_DIR" || true)"
                SENTINEL=".wrangler/state/leco-d1-bootstrap-${BINDING}.applied"
                if [ -n "$BOOTSTRAP_FILE" ]; then
                    if [ "$D1_BOOTSTRAP_APPLY" = "always" ] || [ ! -f "$SENTINEL" ]; then
                        log "  -> bootstrap ${BINDING} from $(basename "$BOOTSTRAP_FILE")"
                        if "$WRANGLER_BIN" d1 execute "$BINDING" \
                                --local --persist-to ".wrangler/state" \
                                --config "$CONFIG" \
                                --file "$BOOTSTRAP_FILE" 2>&1 | sed 's/^/    /'; then
                            mkdir -p "$(dirname "$SENTINEL")"
                            : > "$SENTINEL"
                            log "  -> bootstrap ${BINDING} OK"
                        else
                            log "  !! D1 bootstrap failed for ${BINDING} (continuing)"
                        fi
                    else
                        log "  -> bootstrap ${BINDING} already applied (sentinel present); skipping"
                    fi
                fi
            fi

            # --- Phase 2: resilient wrangler d1 migrations apply ---
            # Wrangler's `d1 migrations apply` is all-or-nothing: a single
            # failing migration (e.g. an ALTER TABLE whose target column was
            # already created by the bootstrap, or a CREATE that references a
            # table the app expects to exist out-of-band) aborts the whole
            # batch and leaves later migrations unapplied — including the
            # ones the operator actually cares about.
            #
            # We wrap the apply in a loop: on failure, parse the failed
            # migration's filename out of wrangler's stderr, INSERT it into
            # the `d1_migrations` table as "applied" (so the next batch skips
            # it), and retry. The loop is bounded and guards against repeated
            # failure of the same file. The trade-off is local-only laxity:
            # migrations the operator marked as skipped here do NOT run, so
            # tables they would have created stay missing — exactly what the
            # operator's bootstrap SQL is supposed to make up for. Production
            # never enters this loop.
            if [ "$D1_APPLY" != "off" ]; then
                if [ -d "$MIGRATIONS_DIR" ] || [ "$D1_APPLY" = "always" ]; then
                    log "  -> wrangler d1 migrations apply ${BINDING} --local (dir=${MIGRATIONS_REL})"
                    iter=0
                    max_iter=50
                    last_failed=""
                    out_file="/tmp/leco-mig-${BINDING}.out"
                    while [ "$iter" -lt "$max_iter" ]; do
                        iter=$((iter + 1))
                        if yes '' 2>/dev/null | "$WRANGLER_BIN" d1 migrations apply "$BINDING" \
                                --local --persist-to ".wrangler/state" \
                                --config "$CONFIG" > "$out_file" 2>&1; then
                            sed 's/^/    /' "$out_file" | tail -10 >&2
                            log "  -> D1 migrations apply OK for ${BINDING} (iter=${iter})"
                            break
                        fi
                        # Strip ANSI color codes wrangler emits so the grep is reliable.
                        failed_name="$(sed -E 's/\x1B\[[0-9;]*[A-Za-z]//g' "$out_file" \
                            | grep -oE 'Migration [0-9A-Za-z._-]+ failed' \
                            | head -1 \
                            | awk '{print $2}')"
                        if [ -z "$failed_name" ] || [ "$failed_name" = "$last_failed" ]; then
                            log "  !! migrations stuck on '${failed_name:-?}' — last 20 lines:"
                            sed 's/^/    /' "$out_file" | tail -20 >&2
                            break
                        fi
                        esc_failed="$(printf '%s' "$failed_name" | sed "s/'/''/g")"
                        log "    SKIP ${failed_name} (local apply error — marking as applied to unblock the batch)"
                        "$WRANGLER_BIN" d1 execute "$BINDING" \
                            --local --persist-to ".wrangler/state" \
                            --config "$CONFIG" \
                            --command "INSERT OR IGNORE INTO d1_migrations (name) VALUES ('${esc_failed}')" \
                            >/dev/null 2>&1 || true
                        last_failed="$failed_name"
                    done
                    rm -f "$out_file"
                    if [ "$iter" -ge "$max_iter" ]; then
                        log "  !! D1 migration loop hit iteration cap (${max_iter}) — worker continues"
                    fi
                fi
            fi
            IFS='
'
        done
        IFS="$OLD_IFS"
        log "D1 bootstrap + migration pass complete"
    fi
fi

# Build wrangler dev arg list. We bind 0.0.0.0 so Traefik (on lh-network) can reach us.
# Wrangler dev's --local mode + persist keeps KV/R2/D1 file-backed under .wrangler.
exec "$WRANGLER_BIN" dev \
    --config "$CONFIG" \
    --ip 0.0.0.0 \
    --port "$PORT" \
    --local \
    --persist-to ".wrangler/state"
