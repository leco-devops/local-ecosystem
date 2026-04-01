#!/usr/bin/env bash
# Safe to `source` from ai-stack/core.sh; also runnable as ./ai-stack/services/traefik.sh …
if [ -z "${PROJECT_ROOT:-}" ]; then
  PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

# Bind mounts in `docker run` are resolved on the Docker *host* (e.g. macOS). Inside the Ops
# dashboard container PROJECT_ROOT is /project, which does not exist on the host — set
# DASHBOARD_DOCKER_BIND_ROOT to the real repo path (dashboard.sh does this automatically).
DOCKER_BIND="${DASHBOARD_DOCKER_BIND_ROOT:-$PROJECT_ROOT}"

NAME="traefik"

start() {
  docker network inspect lh-network >/dev/null 2>&1 || docker network create lh-network >/dev/null
  docker rm -f "$NAME" 2>/dev/null

  docker run -d \
    --name "$NAME" \
    --restart unless-stopped \
    --network lh-network \
    -p 80:80 \
    -p 443:443 \
    -p 8080:8080 \
    -v "$DOCKER_BIND/traefik:/etc/traefik" \
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
    remove) remove ;;
    pause) pause ;;
    unpause) unpause ;;
    status) status ;;
    logs) logs ;;
    reset) reset ;;
    *)
      echo "Usage: $0 {start|stop|restart|remove|pause|unpause|status|logs|reset}"
      exit 1
      ;;
  esac
fi
