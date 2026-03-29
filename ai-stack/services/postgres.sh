NAME="n8n_postgres"
VOLUME="n8n_postgres_data"

start() {
  docker rm -f "$NAME" 2>/dev/null
  docker run -d \
    --name "$NAME" \
    --network lh-network \
    -e POSTGRES_USER=postgres \
    -e POSTGRES_PASSWORD=password \
    -e POSTGRES_DB=n8n \
    -v "$VOLUME:/var/lib/postgresql/data" \
    postgres:15
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
