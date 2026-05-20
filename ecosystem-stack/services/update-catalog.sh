#!/usr/bin/env bash
# LEco update-catalog — background Docker service for stack + LLM release checks.
# Writes ecosystem-stack/config/generated/*.json and docs/help/generated/*.md
#
# Usage:
#   ./ecosystem-stack/services/update-catalog.sh start
#   ./ecosystem-stack/services/update-catalog.sh run-once   # single check, exit
#   UPDATE_CATALOG_INTERVAL_HOURS=12 ./ecosystem-stack/services/update-catalog.sh start

NAME="leco-update-catalog"
IMAGE="local/leco-update-catalog:latest"

if [ -z "${PROJECT_ROOT:-}" ]; then
  PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi
BUILD_CONTEXT="${PROJECT_ROOT}/ecosystem-stack/update-catalog"

_image_exists() {
  docker image inspect "$IMAGE" >/dev/null 2>&1
}

build() {
  if [ ! -f "$BUILD_CONTEXT/Dockerfile" ]; then
    echo "❌ Missing $BUILD_CONTEXT/Dockerfile"
    return 1
  fi
  echo "🔨 Building $IMAGE…"
  docker build -t "$IMAGE" "$BUILD_CONTEXT"
}

start() {
  build || return 1
  docker network inspect lh-network >/dev/null 2>&1 || docker network create lh-network >/dev/null
  docker rm -f "$NAME" 2>/dev/null
  mkdir -p "$PROJECT_ROOT/ecosystem-stack/config/generated"
  mkdir -p "$PROJECT_ROOT/docs/help/generated"
  docker run -d \
    --name "$NAME" \
    --restart unless-stopped \
    --network lh-network \
    -v "$PROJECT_ROOT:/project:rw" \
    -v /var/run/docker.sock:/var/run/docker.sock:ro \
    -e "PROJECT_ROOT=/project" \
    -e "UPDATE_CATALOG_INTERVAL_HOURS=${UPDATE_CATALOG_INTERVAL_HOURS:-6}" \
    "$IMAGE"
  echo "✅ $NAME started (checks every ${UPDATE_CATALOG_INTERVAL_HOURS:-6}h)"
}

run_once() {
  build || return 1
  docker network inspect lh-network >/dev/null 2>&1 || docker network create lh-network >/dev/null
  mkdir -p "$PROJECT_ROOT/ecosystem-stack/config/generated"
  mkdir -p "$PROJECT_ROOT/docs/help/generated"
  docker run --rm \
    --network lh-network \
    -v "$PROJECT_ROOT:/project:rw" \
    -v /var/run/docker.sock:/var/run/docker.sock:ro \
    -e "PROJECT_ROOT=/project" \
    -e "UPDATE_CATALOG_RUN_ONCE=1" \
    "$IMAGE"
}

stop() { docker stop "$NAME" 2>/dev/null; }
remove() { docker rm -f "$NAME" 2>/dev/null; }
restart() { stop; start; }
status() { docker ps -a --filter "name=^/${NAME}$" --format "table {{.Names}}\t{{.Status}}\t{{.Image}}"; }
logs() { docker logs -f "$NAME"; }

case "${1:-}" in
  build) build ;;
  start) start ;;
  stop) stop ;;
  remove) remove ;;
  restart) restart ;;
  status) status ;;
  logs) logs ;;
  run-once) run_once ;;
  *)
    echo "Usage: $0 {build|start|stop|remove|restart|status|logs|run-once}"
    exit 1
    ;;
esac
