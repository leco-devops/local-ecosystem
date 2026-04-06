#!/usr/bin/env bash
# Safe to `source` from ecosystem-stack/core.sh; also runnable as ./ecosystem-stack/services/webui.sh …
NAME="open-webui"
VOLUME="open-webui"

start() {
  docker network inspect lh-network >/dev/null 2>&1 || docker network create lh-network >/dev/null
  docker rm -f "$NAME" 2>/dev/null
  docker run -d \
    --name "$NAME" \
    --restart unless-stopped \
    --network lh-network \
    -e OLLAMA_BASE_URL=http://ollama:11434 \
    -v "$VOLUME:/app/backend/data" \
    ghcr.io/open-webui/open-webui:main
}

stop() { docker stop "$NAME"; }
restart() { stop; start; }
remove() { docker rm -f "$NAME"; }
pause() { docker pause "$NAME"; }
unpause() { docker unpause "$NAME"; }
status() { docker ps -a --filter "name=^/$NAME$" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"; }
reset() {
  remove
  docker volume rm "$VOLUME" 2>/dev/null
}
logs() { docker logs -f "$NAME"; }
