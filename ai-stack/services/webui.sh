NAME="open-webui"
VOLUME="open-webui"

start() {
  docker rm -f "$NAME" 2>/dev/null
  docker run -d \
    --name "$NAME" \
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
