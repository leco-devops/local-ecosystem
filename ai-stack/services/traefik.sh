NAME="traefik"

start() {
  docker rm -f "$NAME" 2>/dev/null

  docker run -d \
    --name "$NAME" \
    --network lh-network \
    -p 80:80 \
    -p 443:443 \
    -p 8080:8080 \
    -v "$PROJECT_ROOT/traefik:/etc/traefik" \
    -v "$PROJECT_ROOT/certs:/certs" \
    traefik:v3.0 \
    --api.insecure=true \
    --providers.file.filename=/etc/traefik/dynamic.yml \
    --entrypoints.web.address=:80 \
    --entrypoints.websecure.address=:443
}

stop() { docker stop "$NAME"; }
restart() { stop; start; }
remove() { docker rm -f "$NAME"; }
pause() { docker pause "$NAME"; }
unpause() { docker unpause "$NAME"; }
status() { docker ps -a --filter "name=^/$NAME$" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"; }
logs() { docker logs -f "$NAME"; }
reset() { remove; }
