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
SFTP_KEYS_COMPOSE="$PROJECT_ROOT/file-transfer/docker-compose.sftp-keys.yml"
ENV_FILE="$PROJECT_ROOT/file-transfer/.env"
SFTP_DATA_VOLUME="file-transfer_file_transfer_data"

_sftp_pub_keys_present() {
  local keys_dir="$PROJECT_ROOT/file-transfer/keys/sftp"
  [ -d "$keys_dir" ] || return 1
  compgen -G "$keys_dir/*.pub" >/dev/null 2>&1
}

_sftp_auth_mode() {
  if [ -f "$ENV_FILE" ]; then
    grep -E '^SFTP_AUTH_MODE=' "$ENV_FILE" | tail -1 | cut -d= -f2-
  else
    echo "password"
  fi
}

_prepare_sftp_data_volume() {
  local mode
  mode="$(_sftp_auth_mode)"
  if { [ "$mode" = "key" ] || [ "$mode" = "both" ]; } && _sftp_pub_keys_present; then
    return 0
  fi
  docker run --rm -v "${SFTP_DATA_VOLUME}:/home/leco" alpine sh -c 'rm -rf /home/leco/.ssh' >/dev/null 2>&1 || true
}

_compose() {
  local args=("$@")
  local files=(-f "$COMPOSE_FILE")
  if _sftp_pub_keys_present; then
    files+=(-f "$SFTP_KEYS_COMPOSE")
  fi
  if [ -f "$ENV_FILE" ]; then
    docker compose --env-file "$ENV_FILE" --project-directory "$PROJECT_ROOT/file-transfer" "${files[@]}" "${args[@]}"
  else
    docker compose --project-directory "$PROJECT_ROOT/file-transfer" "${files[@]}" "${args[@]}"
  fi
}

start() {
  docker network inspect lh-network >/dev/null 2>&1 || docker network create lh-network >/dev/null
  _prepare_sftp_data_volume
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
  _prepare_sftp_data_volume
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
