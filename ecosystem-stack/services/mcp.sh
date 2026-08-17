#!/usr/bin/env bash
# LEco DevOps MCP server — Model Context Protocol access for AI agents.
# Safe to `source` from ecosystem-stack/core.sh; also runnable as ./ecosystem-stack/services/mcp.sh …
#
# This container serves the *streamable HTTP* transport at https://mcp.lh/mcp so remote
# agents and other machines can attach. Local Claude Code uses the stdio transport instead
# (pipx install ./tools/mcp-server), which needs no container at all.
#
# The server only makes HTTP calls to the dashboard API — it gets no Docker socket. Its one
# bind mount is ecosystem-stack/config/generated, where it appends the activity log the
# dashboard reads; nothing else from the repo is visible to it.
#
# Usage:
#   ./ecosystem-stack/services/mcp.sh start
#   LECO_MCP_ALLOW_DESTRUCTIVE=1 ./ecosystem-stack/services/mcp.sh restart

NAME="leco-mcp"
IMAGE="local/leco-mcp:latest"
PORT="${LECO_MCP_HOST_PORT:-8099}"

if [ -z "${PROJECT_ROOT:-}" ]; then
  PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

build() {
  if [ ! -f "$PROJECT_ROOT/tools/mcp-server/Dockerfile" ]; then
    echo "❌ Missing $PROJECT_ROOT/tools/mcp-server/Dockerfile"
    return 1
  fi
  echo "🔨 Building $IMAGE…"
  docker build -t "$IMAGE" -f "$PROJECT_ROOT/tools/mcp-server/Dockerfile" "$PROJECT_ROOT"
}

start() {
  # Always build: layers are cached, and this is what picks up edits to tools/mcp-server
  # instead of silently re-running a stale image (same pattern as update-catalog.sh).
  build || return 1
  docker network inspect lh-network >/dev/null 2>&1 || docker network create lh-network >/dev/null
  docker rm -f "$NAME" 2>/dev/null

  # Activity telemetry (JSONL, read by the dashboard). ONLY this one directory is mounted,
  # and still no Docker socket: the container must not be able to reach the rest of the
  # repo or the host daemon just because it keeps a log.
  GENERATED_DIR="$PROJECT_ROOT/ecosystem-stack/config/generated"
  mkdir -p "$GENERATED_DIR"
  # The mount is owned by the host user, and the image's default user (uid 10001) could not
  # write to it on Linux. Run as the invoking user instead — unless that is root, where the
  # image's non-root default is the safer choice and the mount is writable anyway.
  RUN_AS=""
  if [ "$(id -u)" != "0" ]; then
    RUN_AS="--user $(id -u):$(id -g)"
  fi

  # Destructive tools and credential tools are opt-in; they stay off unless the operator
  # sets them in the environment that starts this service.
  # shellcheck disable=SC2086 # RUN_AS is a fixed "--user uid:gid" pair, intentionally split
  docker run -d \
    --name "$NAME" \
    --restart unless-stopped \
    --network lh-network \
    -p "$PORT:8099" \
    $RUN_AS \
    -v "$GENERATED_DIR:/project/ecosystem-stack/config/generated:rw" \
    -e "LECO_MCP_PROJECT_ROOT=/project" \
    -e "LECO_MCP_ACTIVITY_MAX_EVENTS=${LECO_MCP_ACTIVITY_MAX_EVENTS:-2000}" \
    -e "LECO_MCP_DASHBOARD_URL=${LECO_MCP_DASHBOARD_URL:-http://service-dashboard:8090}" \
    -e "LECO_MCP_CONTROL_TOKEN=${LECO_MCP_CONTROL_TOKEN:-${DASHBOARD_CONTROL_TOKEN:-}}" \
    -e "LECO_MCP_ALLOW_DESTRUCTIVE=${LECO_MCP_ALLOW_DESTRUCTIVE:-0}" \
    -e "LECO_MCP_ALLOW_CREDENTIALS=${LECO_MCP_ALLOW_CREDENTIALS:-0}" \
    -e "LECO_MCP_HTTP_HOST=0.0.0.0" \
    -e "LECO_MCP_HTTP_PORT=8099" \
    -e "LECO_MCP_LOG_LEVEL=${LECO_MCP_LOG_LEVEL:-INFO}" \
    "$IMAGE" http >/dev/null || return 1

  echo "✅ $NAME started — https://mcp.lh/mcp (host: http://localhost:$PORT/mcp)"
  echo "📊 Activity: http://localhost:$PORT/insights → $GENERATED_DIR/mcp-activity.jsonl"
  if [ "${LECO_MCP_ALLOW_DESTRUCTIVE:-0}" = "1" ]; then
    echo "⚠️  Destructive tools ENABLED (remove/reset/destroy are callable with confirm=true)"
  fi
  if [ -z "${LECO_MCP_CONTROL_TOKEN:-${DASHBOARD_CONTROL_TOKEN:-}}" ]; then
    echo "ℹ️  No control token set — matches a dashboard without DASHBOARD_CONTROL_TOKEN."
  fi
}

stop() { docker stop "$NAME" 2>/dev/null; }
remove() { docker rm -f "$NAME" 2>/dev/null; }
restart() { stop; start; }
pause() { docker pause "$NAME" 2>/dev/null; }
unpause() { docker unpause "$NAME" 2>/dev/null; }
status() { docker ps -a --filter "name=^/$NAME$" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"; }

reset() {
  # Stateless service: no volumes to drop, so reset is remove + rebuild from source.
  remove
  docker image rm -f "$IMAGE" 2>/dev/null
  start
}

logs() { docker logs -f "$NAME"; }

# Only act when executed directly; sourcing must not run anything (core.sh sources this).
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  case "${1:-}" in
    build) build ;;
    start) start ;;
    stop) stop ;;
    remove) remove ;;
    restart) restart ;;
    pause) pause ;;
    unpause) unpause ;;
    reset) reset ;;
    status) status ;;
    logs) logs ;;
    *)
      echo "Usage: $0 {build|start|stop|remove|restart|pause|unpause|reset|status|logs}"
      exit 1
      ;;
  esac
fi
