#!/usr/bin/env bash
# Safe to `source` from ecosystem-stack/core.sh; also runnable as ./ecosystem-stack/services/paperclip-postgres.sh …
NAME="paperclip_postgres"
VOLUME="paperclip_postgres_data"

_stop_paperclip_if_running() {
  if docker inspect -f '{{.State.Running}}' paperclip 2>/dev/null | grep -q true; then
    echo "🛑 Stopping paperclip (depends on this database)…"
    docker stop paperclip 2>/dev/null || true
  fi
}

start() {
  docker network inspect lh-network >/dev/null 2>&1 || docker network create lh-network >/dev/null
  docker rm -f "$NAME" 2>/dev/null
  docker run -d \
    --name "$NAME" \
    --restart unless-stopped \
    --network lh-network \
    -e POSTGRES_USER=paperclip \
    -e POSTGRES_PASSWORD=paperclip \
    -e POSTGRES_DB=paperclip \
    -v "$VOLUME:/var/lib/postgresql/data" \
    postgres:17-alpine
}

stop() {
  _stop_paperclip_if_running
  docker stop "$NAME" 2>/dev/null || true
}
restart() { stop; start; }
remove() {
  _stop_paperclip_if_running
  docker rm -f paperclip 2>/dev/null || true
  docker rm -f "$NAME" 2>/dev/null || true
}
pause() {
  _stop_paperclip_if_running
  docker pause "$NAME" 2>/dev/null || true
}
unpause() { docker unpause "$NAME"; }
status() { docker ps -a --filter "name=^/$NAME$" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"; }
reset() {
  remove
  docker volume rm "$VOLUME" 2>/dev/null
}
logs() { docker logs -f "$NAME"; }
