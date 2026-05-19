#!/usr/bin/env bash
# Safe to `source` from ecosystem-stack/core.sh; also runnable as ./ecosystem-stack/services/postgres.sh …
NAME="n8n_postgres"
VOLUME="n8n_postgres_data"

_stop_n8n_if_running() {
  if docker inspect -f '{{.State.Running}}' n8n 2>/dev/null | grep -q true; then
    echo "🛑 Stopping n8n (depends on this database)…"
    docker stop n8n 2>/dev/null || true
  fi
}

start() {
  docker network inspect lh-network >/dev/null 2>&1 || docker network create lh-network >/dev/null
  docker rm -f "$NAME" 2>/dev/null
  docker run -d \
    --name "$NAME" \
    --restart unless-stopped \
    --network lh-network \
    -p 5432:5432 \
    -e POSTGRES_USER=postgres \
    -e POSTGRES_PASSWORD=password \
    -e POSTGRES_DB=n8n \
    -v "$VOLUME:/var/lib/postgresql/data" \
    postgres:15
}

stop() {
  _stop_n8n_if_running
  docker stop "$NAME" 2>/dev/null || true
}
restart() { stop; start; }
remove() {
  _stop_n8n_if_running
  docker rm -f n8n 2>/dev/null || true
  docker rm -f "$NAME" 2>/dev/null || true
}
pause() {
  _stop_n8n_if_running
  docker pause "$NAME" 2>/dev/null || true
}
unpause() { docker unpause "$NAME"; }
status() { docker ps -a --filter "name=^/$NAME$" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"; }
reset() {
  remove
  docker volume rm "$VOLUME" 2>/dev/null
}
logs() { docker logs -f "$NAME"; }
