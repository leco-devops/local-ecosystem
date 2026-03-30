#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/cloudflare-local/docker-compose.yml"

docker network inspect lh-network >/dev/null 2>&1 || docker network create lh-network >/dev/null
docker compose -f "$COMPOSE_FILE" up -d --build

echo "Cloudflare-local stack started."
echo "Use: docker compose -f \"$COMPOSE_FILE\" ps"
