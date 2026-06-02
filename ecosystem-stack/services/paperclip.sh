#!/usr/bin/env bash
# Safe to `source` from ecosystem-stack/core.sh; also runnable as ./ecosystem-stack/services/paperclip.sh …
NAME="paperclip"
VOLUME="paperclip_data"
IMAGE="${PAPERCLIP_IMAGE:-ghcr.io/paperclipai/paperclip:latest}"
BETTER_AUTH_SECRET="${PAPERCLIP_BETTER_AUTH_SECRET:-leco-paperclip-local-dev-better-auth-secret-32}"

if [ -z "${PROJECT_ROOT:-}" ]; then
  PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi
POSTGRES_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/paperclip-postgres.sh"

_fix_data_permissions() {
  # `docker exec` defaults to root; onboard/bootstrap can create root-owned files under
  # /paperclip/instances while the server runs as node (uid 1000) and crashes on .env.
  if ! docker volume inspect "$VOLUME" >/dev/null 2>&1; then
    return 0
  fi
  if docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null | grep -q true; then
    docker exec -u root "$NAME" sh -c 'chown -R node:node /paperclip/instances 2>/dev/null || true'
    return 0
  fi
  # Paperclip image always runs as node; use alpine for offline volume chown.
  docker run --rm \
    -v "$VOLUME:/paperclip" \
    alpine sh -c 'chown -R 1000:1000 /paperclip/instances 2>/dev/null || true'
}

_ensure_postgres_running() {
  if docker inspect -f '{{.State.Running}}' paperclip_postgres 2>/dev/null | grep -q true; then
    return 0
  fi
  echo "🔄 Starting paperclip_postgres (required by Paperclip)…"
  if [ -f "$POSTGRES_SCRIPT" ]; then
    bash "$POSTGRES_SCRIPT" start
  else
    echo "❌ Missing $POSTGRES_SCRIPT"
    return 1
  fi
}

_stop_postgres_if_present() {
  if docker inspect paperclip_postgres >/dev/null 2>&1; then
    echo "🛑 Stopping paperclip_postgres…"
    docker stop paperclip_postgres 2>/dev/null || true
  fi
}

start() {
  echo "🚀 Starting Paperclip (AI agent orchestration)…"
  _ensure_postgres_running || return 1

  docker network inspect lh-network >/dev/null 2>&1 || docker network create lh-network >/dev/null
  docker rm -f "$NAME" 2>/dev/null

  if [ "$RESET" = "true" ]; then
    echo "⚠️ Resetting Paperclip data volume…"
    docker volume rm "$VOLUME" 2>/dev/null
  fi

  _fix_data_permissions

  docker run -d \
    --name "$NAME" \
    --restart unless-stopped \
    --network lh-network \
    -e DATABASE_URL=postgres://paperclip:paperclip@paperclip_postgres:5432/paperclip \
    -e PORT=3100 \
    -e HOST=0.0.0.0 \
    -e SERVE_UI=true \
    -e PAPERCLIP_PUBLIC_URL=http://paperclip.lh \
    -e PAPERCLIP_DEPLOYMENT_MODE=authenticated \
    -e PAPERCLIP_DEPLOYMENT_EXPOSURE=private \
    -e BETTER_AUTH_SECRET="$BETTER_AUTH_SECRET" \
    -e PAPERCLIP_TELEMETRY_DISABLED=1 \
    -e DO_NOT_TRACK=1 \
    -v "$VOLUME:/paperclip" \
    "$IMAGE"

  echo "✅ Paperclip started (http://paperclip.lh)"
  echo "💡 First admin: ./ecosystem-stack/ecosystem-stack.sh paperclip-bootstrap-ceo"
  echo "   Or LEco Dashboard → Infrastructure → Paperclip → Bootstrap CEO"
}

bootstrap_ceo() {
  local base_url="${PAPERCLIP_PUBLIC_URL:-http://paperclip.lh}"
  local force_flag=""
  if [ "${1:-}" = "--force" ] || [ "${FORCE:-}" = "1" ]; then
    force_flag=" --force"
  fi
  if ! docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null | grep -q true; then
    echo "❌ Paperclip container is not running"
    return 1
  fi
  echo "🔐 Creating Paperclip bootstrap CEO invite…"
  docker exec -u node -i "$NAME" sh -c "cd /app && pnpm paperclipai auth bootstrap-ceo -d /paperclip --base-url '${base_url}'${force_flag}"
  _fix_data_permissions
}

stop() {
  docker stop "$NAME" 2>/dev/null || true
  _stop_postgres_if_present
}

restart() {
  stop
  start
}

remove() {
  docker rm -f "$NAME" 2>/dev/null || true
  docker rm -f paperclip_postgres 2>/dev/null || true
}

pause() {
  docker pause "$NAME" 2>/dev/null || true
  docker pause paperclip_postgres 2>/dev/null || true
}
unpause() {
  docker unpause paperclip_postgres 2>/dev/null || true
  docker unpause "$NAME" 2>/dev/null || true
}
status() { docker ps -a --filter "name=^/$NAME$" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"; }
reset() {
  remove
  docker volume rm "$VOLUME" 2>/dev/null
  docker volume rm paperclip_postgres_data 2>/dev/null
}

logs() {
  docker logs -f "$NAME"
}

# Runnable directly: ./ecosystem-stack/services/paperclip.sh bootstrap-ceo [--force]
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  ACTION="${1:-status}"
  case "${ACTION}" in
    start) start ;;
    stop) stop ;;
    restart) restart ;;
    status) status ;;
    logs) logs ;;
    remove) remove ;;
    reset) reset ;;
    pause) pause ;;
    unpause) unpause ;;
    bootstrap-ceo|bootstrap_ceo)
      shift || true
      bootstrap_ceo "$@"
      ;;
    *)
      echo "Usage: $0 {start|stop|restart|status|logs|remove|reset|pause|unpause|bootstrap-ceo [--force]}"
      exit 1
      ;;
  esac
fi
