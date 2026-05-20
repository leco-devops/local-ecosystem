#!/usr/bin/env bash
# AirLLM Ollama-compatible shim — Docker container service.
#
# Replaces the earlier macOS LaunchAgent variant. The shim now runs in the
# `airllm` container on lh-network and is reachable at:
#   - http://airllm:11435            (intra-network DNS, used by dashboard etc.)
#   - http://127.0.0.1:${AIRLLM_PORT_HOST:-11435} (host port mapping for curl/dev)
#   - https://airllm.lh              (via Traefik)
#
# Safe to `source` from ecosystem-stack/core.sh and dashboard/control.py; also
# runnable directly: ./ecosystem-stack/services/airllm.sh <start|stop|...>.

NAME="airllm"
IMAGE="local-airllm:latest"
VOLUME_HF="airllm_hf_cache"
VOLUME_SHARDS="airllm_layer_shards"

# ecosystem-stack/services -> ecosystem-stack
_STACK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
PINNED_MODELS_FILE="${_STACK_ROOT}/config/airllm-pinned-models.txt"
BUILD_CONTEXT="${_STACK_ROOT}/airllm"

# Tunables (env-overridable so a host can pin AirLLM to a different port or
# rename volumes without touching this script).
AIRLLM_PORT="${AIRLLM_PORT:-11435}"
AIRLLM_PORT_HOST="${AIRLLM_PORT_HOST:-${AIRLLM_PORT}}"
AIRLLM_COMPRESSION="${AIRLLM_COMPRESSION:-none}"
AIRLLM_KEEP_ALIVE="${AIRLLM_KEEP_ALIVE:-300}"
# Optional: pass through HF_TOKEN for gated models. Falls back to ~/.local-eco-airllm/.env.
HF_TOKEN="${HF_TOKEN:-}"

_image_exists() {
  docker image inspect "$IMAGE" >/dev/null 2>&1
}

ensure_image() {
  if _image_exists && [ -z "${AIRLLM_FORCE_BUILD:-}" ]; then
    return 0
  fi
  if [ ! -f "$BUILD_CONTEXT/Dockerfile" ]; then
    echo "❌ Dockerfile missing at $BUILD_CONTEXT/Dockerfile"
    return 1
  fi
  echo "🛠  Building AirLLM image ($IMAGE) — first build downloads torch + airllm (~2GB)…"
  docker build -t "$IMAGE" "$BUILD_CONTEXT"
}

wait_airllm_ready() {
  local i=0
  local max_attempts="${AIRLLM_READY_TIMEOUT:-120}"
  printf 'Waiting for AirLLM shim'
  while [ "$i" -lt "$max_attempts" ]; do
    # Probe the container via docker exec to avoid host port assumptions.
    if docker exec "$NAME" curl -fsS "http://127.0.0.1:${AIRLLM_PORT}/api/tags" >/dev/null 2>&1; then
      printf ' ✓\n'
      return 0
    fi
    printf '.'
    sleep 1
    i=$((i + 1))
  done
  printf '\n'
  echo "⚠️  AirLLM shim did not become ready in ${max_attempts}s (check: docker logs $NAME)"
  return 1
}

# Pull every non-comment line from airllm-pinned-models.txt by POSTing to the
# shim's /api/pull endpoint. Mirrors ollama.sh `pull_pinned_models`.
pull_pinned_models() {
  if ! docker inspect "$NAME" >/dev/null 2>&1; then
    echo "❌ Container '$NAME' is not running. Start airllm first."
    return 1
  fi
  if [ ! -f "$PINNED_MODELS_FILE" ]; then
    echo "ℹ️ No pinned models file: $PINNED_MODELS_FILE"
    return 0
  fi
  if ! wait_airllm_ready; then
    return 1
  fi
  echo "📥 Pulling pinned AirLLM models (from $PINNED_MODELS_FILE)…"
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"
    line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [ -z "$line" ] && continue
    echo "  → airllm pull $line"
    # Best-effort: stream=false so curl returns when the pull job has started.
    # Actual download continues in the shim process.
    docker exec "$NAME" curl -fsS -X POST "http://127.0.0.1:${AIRLLM_PORT}/api/pull" \
        -H 'Content-Type: application/json' \
        -d "{\"name\": \"${line}\", \"stream\": false}" \
        >/dev/null 2>&1 || echo "  ⚠️ pull failed for: $line"
  done <"$PINNED_MODELS_FILE"
  echo "✅ Pinned model pulls dispatched. (Large models continue downloading in the shim.)"
}

start() {
  docker network inspect lh-network >/dev/null 2>&1 || docker network create lh-network >/dev/null
  ensure_image || return 1
  docker rm -f "$NAME" 2>/dev/null

  # HF_TOKEN passed via env when set (gated models). Volumes hold HF cache +
  # AirLLM layer shards so models survive container removal.
  local hf_env=()
  if [ -n "${HF_TOKEN}" ]; then
    hf_env=(-e "HF_TOKEN=${HF_TOKEN}")
  fi

  docker run -d \
    --name "$NAME" \
    --restart unless-stopped \
    --network lh-network \
    -p "${AIRLLM_PORT_HOST}:${AIRLLM_PORT}" \
    -v "${VOLUME_HF}:/data/hf-cache" \
    -v "${VOLUME_SHARDS}:/data/shards" \
    -e "AIRLLM_PORT=${AIRLLM_PORT}" \
    -e "AIRLLM_COMPRESSION=${AIRLLM_COMPRESSION}" \
    -e "AIRLLM_KEEP_ALIVE=${AIRLLM_KEEP_ALIVE}" \
    "${hf_env[@]}" \
    "$IMAGE" || return 1

  # Background pinned-model pulls so `start` returns quickly even on first boot.
  pull_pinned_models || true
}

stop() { docker stop "$NAME"; }
restart() { stop; start; }
remove() { docker rm -f "$NAME"; }
pause() { docker pause "$NAME"; }
unpause() { docker unpause "$NAME"; }
status() { docker ps -a --filter "name=^/$NAME$" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"; }

# Reset = remove container + delete the HF cache and shard volumes. Frees disk
# but forces a re-download of every model on next start.
reset() {
  remove
  docker volume rm "$VOLUME_HF" 2>/dev/null
  docker volume rm "$VOLUME_SHARDS" 2>/dev/null
}

logs() { docker logs -f "$NAME"; }

# Backwards-compat: `install` used to mean "create Python venv on host" when the
# shim was a LaunchAgent. With the container build it means "ensure the image
# exists" so old callers still work.
ensure_installed() { ensure_image; }

# Only dispatch when invoked directly (not when sourced from core.sh /
# dashboard/control.py). Matches the ollama.sh pattern.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    ACTION="${1:-status}"
    case "${ACTION}" in
        start) start ;;
        stop) stop ;;
        restart) restart ;;
        status) status ;;
        logs) logs ;;
        remove) remove ;;
        reset) reset ;;
        pause) pause ;;
        unpause) unpause ;;
        pull-models) pull_pinned_models ;;
        build|install) ensure_image ;;
        *)
            echo "Usage: $0 {start|stop|restart|status|logs|remove|reset|pause|unpause|pull-models|build}"
            exit 1
            ;;
    esac
fi
