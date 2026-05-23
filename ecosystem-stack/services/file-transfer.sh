#!/usr/bin/env bash
# Safe to `source` from ecosystem-stack/core.sh; also runnable as ./ecosystem-stack/services/file-transfer.sh …
if [ -z "${PROJECT_ROOT:-}" ]; then
  if [ -n "${LECO_PROJECT_ROOT_HOST:-}" ] && [ -d "$LECO_PROJECT_ROOT_HOST" ]; then
    PROJECT_ROOT="$LECO_PROJECT_ROOT_HOST"
  else
    PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  fi
fi

NAME="file-transfer"
COMPOSE_FILE="$PROJECT_ROOT/file-transfer/docker-compose.yml"
ENV_FILE="$PROJECT_ROOT/file-transfer/.env"

_compose() {
  local args=("$@")
  if [ -f "$ENV_FILE" ]; then
    docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "${args[@]}"
  else
    docker compose -f "$COMPOSE_FILE" "${args[@]}"
  fi
}

start() {
  docker network inspect lh-network >/dev/null 2>&1 || docker network create lh-network >/dev/null
  _compose up -d --build
}

deploy() {
  start
}

stop() {
  _compose stop
}

restart() {
  stop
  start
}

pause() {
  _compose pause
}

unpause() {
  _compose unpause
}

status() {
  _compose ps
}

logs() {
  _compose logs -f
}

remove() {
  _compose down --remove-orphans
}

reset() {
  _compose down -v --remove-orphans
}

recreate() {
  docker network inspect lh-network >/dev/null 2>&1 || docker network create lh-network >/dev/null
  local svc="${1:-}"
  if [ -z "$svc" ]; then
    _compose up -d --force-recreate
  else
    _compose up -d --force-recreate --no-deps "$svc"
  fi
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
    *)
      echo "Usage: $0 {start|deploy|stop|restart|pause|unpause|status|logs|remove|reset|recreate [service]}"
      exit 1
      ;;
  esac
fi
