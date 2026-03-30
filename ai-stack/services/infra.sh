if [ -z "${PROJECT_ROOT:-}" ]; then
  PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

NAME="infra-stack"
COMPOSE_FILE="$PROJECT_ROOT/infra/docker-compose.yml"

# Varnish reads VCL only at daemon start. `compose up` alone can leave an old cache-varnish
# container if only ./varnish/default.vcl changed — always recreate so the bind mount is loaded.
_recreate_cache_varnish_for_vcl() {
  docker compose -f "$COMPOSE_FILE" up -d --no-deps --force-recreate cache-varnish
}

start() {
  docker network inspect lh-network >/dev/null 2>&1 || docker network create lh-network >/dev/null
  docker compose -f "$COMPOSE_FILE" up -d --build
  _recreate_cache_varnish_for_vcl
}

deploy() {
  start
}

stop() {
  docker compose -f "$COMPOSE_FILE" stop
}

restart() {
  stop
  start
}

pause() {
  docker compose -f "$COMPOSE_FILE" pause
}

unpause() {
  docker compose -f "$COMPOSE_FILE" unpause
}

status() {
  docker compose -f "$COMPOSE_FILE" ps
}

logs() {
  docker compose -f "$COMPOSE_FILE" logs -f
}

remove() {
  docker compose -f "$COMPOSE_FILE" down --remove-orphans
}

reset() {
  docker compose -f "$COMPOSE_FILE" down -v --remove-orphans
}

recreate() {
  docker network inspect lh-network >/dev/null 2>&1 || docker network create lh-network >/dev/null
  local svc="${1:-}"
  if [ -z "$svc" ]; then
    docker compose -f "$COMPOSE_FILE" up -d --force-recreate
  else
    docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps "$svc"
  fi
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  act="${1:-}"
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
    recreate) recreate "$@" ;;
    *)
      echo "Usage: $0 {start|deploy|stop|restart|remove|pause|unpause|status|logs|reset|recreate [service]}"
      exit 1
      ;;
  esac
fi
