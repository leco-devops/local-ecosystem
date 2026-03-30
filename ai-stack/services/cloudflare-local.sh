if [ -z "${PROJECT_ROOT:-}" ]; then
  PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

NAME="cloudflare-local"
COMPOSE_FILE="$PROJECT_ROOT/cloudflare-local/docker-compose.yml"

start() {
  docker network inspect lh-network >/dev/null 2>&1 || docker network create lh-network >/dev/null
  docker compose -f "$COMPOSE_FILE" up -d --build
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

backup() {
  local base="${D1_PUBLIC_URL:-http://d1.lh}"
  echo "Backing up D1 databases via ${base} …"
  local json
  json=$(curl -fsS "${base}/databases") || {
    echo "❌ Could not reach ${base}/databases (set D1_PUBLIC_URL if needed)"
    return 1
  }
  local dbs
  dbs=$(printf '%s' "$json" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(" ".join(d.get("databases") or []))')
  for db in $dbs; do
    echo "  → POST /databases/${db}/backup"
    curl -fsS -X POST "${base}/databases/${db}/backup" | python3 -m json.tool 2>/dev/null || true
  done
  echo "Done."
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  act="${1:-}"
  shift || true
  case "$act" in
    start) start ;;
    deploy) deploy ;;
    stop) stop ;;
    restart) restart ;;
    pause) pause ;;
    unpause) unpause ;;
    status) status ;;
    logs) logs ;;
    remove) remove ;;
    reset) reset ;;
    recreate) recreate "$@" ;;
    backup) backup ;;
    *)
      echo "Usage: $0 {start|deploy|stop|restart|pause|unpause|status|logs|remove|reset|backup|recreate [service]}"
      exit 1
      ;;
  esac
fi
