NAME="n8n"
VOLUME="n8n_data"

start() {
  echo "🚀 Starting n8n (HTTP + HTTPS compatible mode)..."

  # Force clean start (IMPORTANT for env changes)
  docker rm -f "$NAME" 2>/dev/null

  # Optional: reset volume if explicitly requested
  if [ "$RESET" = "true" ]; then
    echo "⚠️ Resetting n8n data volume..."
    docker volume rm "$VOLUME" 2>/dev/null
  fi

  docker run -d \
    --name "$NAME" \
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
    docker.n8n.io/n8nio/n8n

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
