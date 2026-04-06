#!/usr/bin/env bash
# Safe to `source` from ecosystem-stack/core.sh; also runnable as ./ecosystem-stack/services/ollama.sh …
NAME="ollama"
VOLUME="ollama"

# ecosystem-stack/services -> ecosystem-stack
_STACK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
PINNED_MODELS_FILE="${_STACK_ROOT}/config/ollama-pinned-models.txt"

wait_ollama_ready() {
  local i=0
  while [ "$i" -lt 90 ]; do
    if docker exec "$NAME" ollama list >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
    i=$((i + 1))
  done
  echo "⚠️ Ollama did not become ready in 90s; model pulls skipped."
  return 1
}

# Pull every non-comment line from ollama-pinned-models.txt (same list as dashboard).
pull_pinned_models() {
  if ! docker inspect "$NAME" >/dev/null 2>&1; then
    echo "❌ Container '$NAME' is not running. Start ollama first."
    return 1
  fi
  if [ ! -f "$PINNED_MODELS_FILE" ]; then
    echo "ℹ️ No pinned models file: $PINNED_MODELS_FILE"
    return 0
  fi
  if ! wait_ollama_ready; then
    return 1
  fi
  echo "📥 Pulling pinned Ollama models (from $PINNED_MODELS_FILE)…"
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"
    line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [ -z "$line" ] && continue
    echo "  → ollama pull $line"
    docker exec "$NAME" ollama pull "$line" || echo "  ⚠️ pull failed for: $line"
  done <"$PINNED_MODELS_FILE"
  echo "✅ Pinned model pulls finished."
}

start() {
  docker network inspect lh-network >/dev/null 2>&1 || docker network create lh-network >/dev/null
  docker rm -f "$NAME" 2>/dev/null
  docker run -d \
    --name "$NAME" \
    --restart unless-stopped \
    --network lh-network \
    -v "$VOLUME:/root/.ollama" \
    ollama/ollama
  pull_pinned_models || true
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
