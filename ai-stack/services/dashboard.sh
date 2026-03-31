if [ -z "${PROJECT_ROOT:-}" ]; then
  PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

SCHED_SCRIPT="$PROJECT_ROOT/ai-stack/scripts/macos-host-metrics-scheduler.sh"

_darwin_host_metrics_sched() {
  [ "$(uname -s)" = "Darwin" ] || return 0
  [ -f "$SCHED_SCRIPT" ] || return 0
  bash "$SCHED_SCRIPT" "$1"
}

NAME="service-dashboard"
IMAGE="local/service-dashboard:latest"
APP_DIR="$PROJECT_ROOT/dashboard"
HOST_PORT="${DASHBOARD_HOST_PORT:-8090}"
CONTAINER_PORT=8090

# Optional: host /proc for real "System" CPU/RAM/Net/IOPS lines in Metrics (Linux hosts only).
HOST_PROC_MOUNT=""
if [ "$(uname -s)" = "Linux" ] && [ -r /proc/stat ]; then
  HOST_PROC_MOUNT="-v /proc:/host/proc:ro -e DASHBOARD_HOST_PROC=/host/proc"
fi

# Optional: host /sys for CPU thermal (thermal_zone temps) in metrics — Linux only; harmless if empty.
HOST_SYS_MOUNT=""
if [ "$(uname -s)" = "Linux" ] && [ -d /sys/class/thermal ]; then
  HOST_SYS_MOUNT="-v /sys:/host/sys:ro -e DASHBOARD_HOST_SYS=/host/sys"
fi

# macOS + Docker Desktop: Linux container cannot read Apple SMC. Mount a small host dir and read
# cpu_temp_c.txt (float °C) updated by ai-stack/scripts/macos-write-cpu-temp.sh or your own job.
HOST_MAC_TEMP_MOUNT=""
if [ "$(uname -s)" = "Darwin" ]; then
  HOST_METRICS_DIR="${HOME}/.local-eco-host-metrics"
  mkdir -p "$HOST_METRICS_DIR"
  HOST_MAC_TEMP_MOUNT="-v ${HOST_METRICS_DIR}:/host-mac-metrics:ro -e DASHBOARD_HOST_CPU_TEMP_FILE=/host-mac-metrics/cpu_temp_c.txt"
fi

start() {
  if [ ! -d "$APP_DIR" ]; then
    echo "❌ Dashboard source not found at: $APP_DIR"
    return 1
  fi

  echo "🔨 Building dashboard image (always rebuild on start)…"
  docker build -t "$IMAGE" "$APP_DIR" || return 1
  docker network inspect lh-network >/dev/null 2>&1 || docker network create lh-network >/dev/null
  docker rm -f "$NAME" 2>/dev/null

  # Sibling repos (e.g. ../CrawlerVision/leco.app.yaml in config/leco-registry.yaml) must be visible
  # inside the container; /project alone cannot resolve ".." to the host parent directory.
  WORKSPACE_PARENT="$(cd "$PROJECT_ROOT/.." && pwd)"
  WORKSPACE_PARENT_MOUNT=()
  if [ -d "$WORKSPACE_PARENT" ]; then
    WORKSPACE_PARENT_MOUNT=(
      -v "$WORKSPACE_PARENT:/workspace-parent:ro"
      -e DASHBOARD_WORKSPACE_PARENT=/workspace-parent
    )
  fi

  # shellcheck disable=SC2086
  docker run -d \
    --name "$NAME" \
    --restart unless-stopped \
    --network lh-network \
    -p "$HOST_PORT:$CONTAINER_PORT" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "$PROJECT_ROOT:/project:rw" \
    -e "DASHBOARD_DOCKER_BIND_ROOT=$PROJECT_ROOT" \
    "${WORKSPACE_PARENT_MOUNT[@]}" \
    $HOST_PROC_MOUNT \
    $HOST_SYS_MOUNT \
    $HOST_MAC_TEMP_MOUNT \
    "$IMAGE" || return 1

  _darwin_host_metrics_sched install
}

# Alias: full image rebuild + container recreate (same as start).
deploy() {
  start
}

stop() {
  _darwin_host_metrics_sched uninstall
  docker stop "$NAME" 2>/dev/null || true
}
restart() { stop; start; }
remove() {
  _darwin_host_metrics_sched uninstall
  docker rm -f "$NAME" 2>/dev/null || true
}
pause() { docker pause "$NAME"; }
unpause() { docker unpause "$NAME"; }
status() { docker ps -a --filter "name=^/$NAME$" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"; }
logs() { docker logs -f "$NAME"; }
reset() { remove; }

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  act="${1:-start}"
  shift || true
  case "$act" in
    start | deploy) deploy ;;
    stop) stop ;;
    restart) restart ;;
    remove) remove ;;
    pause) pause ;;
    unpause) unpause ;;
    status) status ;;
    logs) logs ;;
    reset) reset ;;
    *)
      echo "Usage: $0 {start|deploy|stop|restart|remove|pause|unpause|status|logs|reset}"
      exit 1
      ;;
  esac
fi
