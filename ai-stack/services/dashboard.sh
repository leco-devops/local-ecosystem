if [ -z "${PROJECT_ROOT:-}" ]; then
  PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

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

start() {
  if [ ! -d "$APP_DIR" ]; then
    echo "❌ Dashboard source not found at: $APP_DIR"
    return 1
  fi

  echo "🔨 Building dashboard image (always rebuild on start)…"
  docker build -t "$IMAGE" "$APP_DIR" || return 1
  docker rm -f "$NAME" 2>/dev/null

  # shellcheck disable=SC2086
  docker run -d \
    --name "$NAME" \
    --restart unless-stopped \
    --network lh-network \
    -p "$HOST_PORT:$CONTAINER_PORT" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "$PROJECT_ROOT:/project:rw" \
    $HOST_PROC_MOUNT \
    $HOST_SYS_MOUNT \
    "$IMAGE"
}

# Alias: full image rebuild + container recreate (same as start).
deploy() {
  start
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
