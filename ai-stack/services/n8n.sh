NAME="n8n"
VOLUME="n8n_data"
IMAGE="${N8N_IMAGE:-local/n8n-with-python:latest}"

if [ -z "${PROJECT_ROOT:-}" ]; then
  PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi
N8N_DOCKER_DIR="$PROJECT_ROOT/ai-stack/docker/n8n-local"

start() {
  echo "🚀 Starting n8n (HTTP + HTTPS compatible mode)..."

  # Force clean start (IMPORTANT for env changes)
  docker network inspect lh-network >/dev/null 2>&1 || docker network create lh-network >/dev/null
  docker rm -f "$NAME" 2>/dev/null

  # Optional: reset volume if explicitly requested
  if [ "$RESET" = "true" ]; then
    echo "⚠️ Resetting n8n data volume..."
    docker volume rm "$VOLUME" 2>/dev/null
  fi

  if [ ! -f "$N8N_DOCKER_DIR/Dockerfile" ]; then
    echo "❌ Missing $N8N_DOCKER_DIR/Dockerfile"
    return 1
  fi
  echo "🔨 Building n8n image (python3 for task runner)…"
  if docker buildx version >/dev/null 2>&1; then
    echo "→ docker buildx build --load -t \"$IMAGE\" -f \"$N8N_DOCKER_DIR/Dockerfile\" \"$N8N_DOCKER_DIR\""
    docker buildx build --load -t "$IMAGE" -f "$N8N_DOCKER_DIR/Dockerfile" "$N8N_DOCKER_DIR" || return 1
  else
    # Minimal Docker CLIs (e.g. dashboard container static binary) have no buildx; BuildKit then errors.
    echo "→ DOCKER_BUILDKIT=0 docker build …  (legacy builder — no buildx in this environment)"
    DOCKER_BUILDKIT=0 docker build -t "$IMAGE" -f "$N8N_DOCKER_DIR/Dockerfile" "$N8N_DOCKER_DIR" || return 1
  fi

  docker run -d \
    --name "$NAME" \
    --restart unless-stopped \
    --network lh-network \
    -e DB_TYPE=postgresdb \
    -e DB_POSTGRESDB_HOST=n8n_postgres \
    -e DB_POSTGRESDB_DATABASE=n8n \
    -e DB_POSTGRESDB_USER=postgres \
    -e DB_POSTGRESDB_PASSWORD=password \
    -e N8N_HOST=n8n.lh \
    -e N8N_PORT=5678 \
    -e N8N_PROTOCOL=http \
    -e WEBHOOK_URL=http://n8n.lh \
    -e N8N_EDITOR_BASE_URL=http://n8n.lh \
    -e N8N_SECURE_COOKIE=false \
    -e N8N_TRUST_PROXY=true \
    -e GENERIC_TIMEZONE="Asia/Kolkata" \
    -e TZ="Asia/Kolkata" \
    -v "$VOLUME:/home/node/.n8n" \
    "$IMAGE"

  echo "✅ n8n started"
}

stop() {
  docker stop "$NAME"
}

restart() {
  stop
  start
}

remove() {
  docker rm -f "$NAME"
}

pause() { docker pause "$NAME"; }
unpause() { docker unpause "$NAME"; }
status() { docker ps -a --filter "name=^/$NAME$" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"; }
reset() {
  remove
  docker volume rm "$VOLUME" 2>/dev/null
}

logs() {
  docker logs -f "$NAME"
}
