NAME="service-dashboard"
IMAGE="local/service-dashboard:latest"
APP_DIR="$PROJECT_ROOT/dashboard"
HOST_PORT="${DASHBOARD_HOST_PORT:-8090}"
CONTAINER_PORT=8090

start() {
  if [ ! -d "$APP_DIR" ]; then
    echo "❌ Dashboard source not found at: $APP_DIR"
    return 1
  fi

  docker build -t "$IMAGE" "$APP_DIR" || return 1
  docker rm -f "$NAME" 2>/dev/null

  docker run -d \
    --name "$NAME" \
    --network lh-network \
    -p "$HOST_PORT:$CONTAINER_PORT" \
    -v /var/run/docker.sock:/var/run/docker.sock:ro \
    "$IMAGE"
}

stop() { docker stop "$NAME"; }
restart() { stop; start; }
remove() { docker rm -f "$NAME"; }
pause() { docker pause "$NAME"; }
unpause() { docker unpause "$NAME"; }
status() { docker ps -a --filter "name=^/$NAME$" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"; }
logs() { docker logs -f "$NAME"; }
reset() { remove; }
