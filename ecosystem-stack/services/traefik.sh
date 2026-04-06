#!/usr/bin/env bash
# Safe to `source` from ecosystem-stack/core.sh; also runnable as ./ecosystem-stack/services/traefik.sh …
if [ -z "${PROJECT_ROOT:-}" ]; then
  PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

# Bind mounts in `docker run` are resolved on the Docker *host* (e.g. macOS). Inside the Ops
# dashboard container PROJECT_ROOT is /project, which does not exist on the host — set
# DASHBOARD_DOCKER_BIND_ROOT to the real repo path (dashboard.sh does this automatically).
DOCKER_BIND="${DASHBOARD_DOCKER_BIND_ROOT:-$PROJECT_ROOT}"
HOSTING_TRAEFIK_DIR="$DOCKER_BIND/hosting/traefik"
HOSTING_DYNAMIC="$HOSTING_TRAEFIK_DIR/dynamic.yml"
CORE_DYNAMIC="$DOCKER_BIND/traefik/dynamic.yml"
# Real file copy (not a symlink): Traefik's fsnotify watches each entry under
# hosting/traefik/. Docker Desktop often returns ENOENT for watchers on symlinks that
# point outside the mounted dir → file provider fails to start → zero HTTP routers → 404.
CORE_DYNAMIC_COPY="$HOSTING_TRAEFIK_DIR/01-stack-core.yml"
NORMALIZE_SCRIPT="$DOCKER_BIND/ecosystem-stack/scripts/normalize-hosting-traefik-dynamic.py"

NAME="traefik"

# Without PyYAML: fix the common invalid stub Traefik v3 rejects.
_normalize_hosting_dynamic_bash() {
  local f="$1"
  [ -f "$f" ] || return 0
  local compact
  compact=$(sed 's/#.*$//' "$f" 2>/dev/null | tr -d '[:space:]' | sed 's/^---//')
  case "$compact" in
  http:\{\})
    printf '%s\n' '{}' >"$f"
    echo "ℹ️  Normalized $f (empty http → {}); install PyYAML for full YAML repair."
    ;;
  esac
}

_normalize_hosting_dynamic_yaml() {
  local f="$1"
  [ -f "$f" ] || return 0
  if [ -f "$NORMALIZE_SCRIPT" ]; then
    local rc=0
    python3 "$NORMALIZE_SCRIPT" "$f" || rc=$?
    if [ "$rc" -eq 0 ] || [ "$rc" -eq 1 ]; then
      return "$rc"
    fi
    if [ "$rc" -eq 3 ]; then
      _normalize_hosting_dynamic_bash "$f"
      return 0
    fi
    echo "⚠️  normalize-hosting-traefik-dynamic.py exited $rc — trying bash fallback"
    _normalize_hosting_dynamic_bash "$f"
    return 0
  fi
  _normalize_hosting_dynamic_bash "$f"
  return 0
}

# Ensure hosting/traefik files exist and are valid before Traefik starts (or for dashboard deploy).
ensure_hosting_files() {
  mkdir -p "$HOSTING_TRAEFIK_DIR"
  if [ ! -f "$CORE_DYNAMIC" ]; then
    echo "❌ Missing required persistent Traefik base file: $CORE_DYNAMIC"
    echo "   Restore it from git before starting Traefik."
    return 1
  fi
  rm -f "$HOSTING_TRAEFIK_DIR/00-core.yml" 2>/dev/null
  rm -f "$CORE_DYNAMIC_COPY" 2>/dev/null
  cp "$CORE_DYNAMIC" "$CORE_DYNAMIC_COPY" || {
    echo "❌ Could not copy stack core to $CORE_DYNAMIC_COPY"
    return 1
  }
  # Do not write http: {} — Traefik v3 rejects an empty http block. Use a no-op root mapping.
  if [ ! -f "$HOSTING_DYNAMIC" ]; then
    printf '%s\n' '{}' >"$HOSTING_DYNAMIC"
    echo "ℹ️ Created hosting/traefik/dynamic.yml (empty merge stub; leco-app adds routes here)"
  fi
  _normalize_hosting_dynamic_yaml "$HOSTING_DYNAMIC" || return 1
}

# Fix files on disk; restart Traefik if a container exists (picks up copy vs symlink / repaired YAML).
heal() {
  ensure_hosting_files || return 1
  if docker ps -a --filter "name=^/${NAME}$" --format '{{.Names}}' | grep -qx "$NAME"; then
    echo "🔄 Restarting Traefik after hosting/traefik file repair…"
    restart
  fi
}

start() {
  docker network inspect lh-network >/dev/null 2>&1 || docker network create lh-network >/dev/null
  docker rm -f "$NAME" 2>/dev/null

  ensure_hosting_files || return 1

  docker run -d \
    --name "$NAME" \
    --restart unless-stopped \
    --network lh-network \
    -p 80:80 \
    -p 443:443 \
    -p 8080:8080 \
    -v "$DOCKER_BIND/traefik:/etc/traefik" \
    -v "$HOSTING_TRAEFIK_DIR:/etc/traefik-dynamic" \
    -v "$DOCKER_BIND/certs:/certs" \
    traefik:v3.3 \
    --configFile=/etc/traefik/traefik-static.yaml
}

stop() { docker stop "$NAME"; }
restart() { stop; start; }
remove() { docker rm -f "$NAME"; }
pause() { docker pause "$NAME"; }
unpause() { docker unpause "$NAME"; }
status() { docker ps -a --filter "name=^/$NAME$" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"; }
logs() { docker logs -f "$NAME"; }
reset() { remove; }

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  act="${1:-}"
  case "$act" in
    start) start ;;
    stop) stop ;;
    restart) restart ;;
    heal) heal ;;
    ensure-hosting-files) ensure_hosting_files ;;
    remove) remove ;;
    pause) pause ;;
    unpause) unpause ;;
    status) status ;;
    logs) logs ;;
    reset) reset ;;
    *)
      echo "Usage: $0 {start|stop|restart|heal|ensure-hosting-files|remove|pause|unpause|status|logs|reset}"
      exit 1
      ;;
  esac
fi
