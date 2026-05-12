#!/usr/bin/env bash
# Rebuild and run LEco DevOps (`service-dashboard`). Safe to `source` from ecosystem-stack/core.sh.
#
# Speed: full `deploy` always runs `docker build` (slow when Dockerfile/layers change or cache cold).
# For day-to-day work, app code is read from the /project bind mount — use:
#   DASHBOARD_SKIP_BUILD=1 ./ecosystem-stack/services/dashboard.sh start
#   or: ./ecosystem-stack/services/dashboard.sh quick
# Rebuild when you change Dockerfile, requirements.txt, or tools/deploy-cli baked into the image.
if [ -z "${PROJECT_ROOT:-}" ]; then
  PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

SCHED_SCRIPT="$PROJECT_ROOT/ecosystem-stack/scripts/macos-host-metrics-scheduler.sh"

_darwin_host_metrics_sched() {
  [ "$(uname -s)" = "Darwin" ] || return 0
  [ -f "$SCHED_SCRIPT" ] || return 0
  bash "$SCHED_SCRIPT" "$1"
}

NAME="service-dashboard"
IMAGE="local/service-dashboard:latest"
APP_DIR="$PROJECT_ROOT/dashboard"
HOST_PORT="${DASHBOARD_HOST_PORT:-8090}"
CONTAINER_PORT=8090

# Optional: host /proc for real "System" CPU/RAM/Net/IOPS lines in Metrics (Linux hosts only).
HOST_PROC_MOUNT=""
if [ "$(uname -s)" = "Linux" ] && [ -r /proc/stat ]; then
  HOST_PROC_MOUNT="-v /proc:/host/proc:ro -e DASHBOARD_HOST_PROC=/host/proc"
fi

# Optional: host /sys for CPU thermal (thermal_zone temps) in metrics — Linux only; harmless if empty.
HOST_SYS_MOUNT=""
if [ "$(uname -s)" = "Linux" ] && [ -d /sys/class/thermal ]; then
  HOST_SYS_MOUNT="-v /sys:/host/sys:ro -e DASHBOARD_HOST_SYS=/host/sys"
fi

# macOS + Docker Desktop: Linux container cannot read Apple SMC. Mount a small host dir and read
# cpu_temp_c.txt (float °C) updated by ecosystem-stack/scripts/macos-write-cpu-temp.sh or your own job.
HOST_MAC_TEMP_MOUNT=""
if [ "$(uname -s)" = "Darwin" ]; then
  HOST_METRICS_DIR="${HOME}/.local-eco-host-metrics"
  mkdir -p "$HOST_METRICS_DIR"
  HOST_MAC_TEMP_MOUNT="-v ${HOST_METRICS_DIR}:/host-mac-metrics:ro -e DASHBOARD_HOST_CPU_TEMP_FILE=/host-mac-metrics/cpu_temp_c.txt"
fi

start() {
  if [ ! -d "$APP_DIR" ]; then
    echo "❌ Dashboard source not found at: $APP_DIR"
    return 1
  fi

  # Build helper: prefer buildx when available, else fall back to the legacy builder.
  # The minimal Docker CLI inside the LEco DevOps container has no buildx plugin, so
  # forcing DOCKER_BUILDKIT=1 here used to fail with "BuildKit is enabled but the buildx
  # component is missing or broken." (Same issue n8n.sh handles below.)
  _dashboard_docker_build() {
    if docker buildx version >/dev/null 2>&1; then
      docker buildx build --load -t "$IMAGE" -f "$APP_DIR/Dockerfile" "$PROJECT_ROOT"
    else
      echo "→ DOCKER_BUILDKIT=0 docker build …  (legacy builder — no buildx in this environment)"
      DOCKER_BUILDKIT=0 docker build -t "$IMAGE" -f "$APP_DIR/Dockerfile" "$PROJECT_ROOT"
    fi
  }

  if [ "${DASHBOARD_SKIP_BUILD:-0}" = "1" ] && docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "⏭️  Skipping docker build (DASHBOARD_SKIP_BUILD=1, image exists). Use deploy without skip to rebuild."
  elif [ "${DASHBOARD_SKIP_BUILD:-0}" = "1" ]; then
    echo "⚠️  DASHBOARD_SKIP_BUILD=1 but $IMAGE missing — building…"
    _dashboard_docker_build || return 1
  else
    echo "🔨 Building dashboard image…"
    _dashboard_docker_build || return 1
  fi
  echo "ℹ️  Flask runs from /project/dashboard when mounted — for app.py/static/template edits use: docker restart $NAME (or quick start) without rebuilding."
  docker network inspect lh-network >/dev/null 2>&1 || docker network create lh-network >/dev/null
  docker rm -f "$NAME" 2>/dev/null

  # Sibling repos (e.g. ../CrawlerVision/leco.app.yaml in config/leco-registry.yaml) must be visible
  # inside the container; /project alone cannot resolve ".." to the host parent directory.
  WORKSPACE_PARENT="$(cd "$PROJECT_ROOT/.." && pwd)"
  WORKSPACE_PARENT_MOUNT=()
  if [ -d "$WORKSPACE_PARENT" ]; then
    # Mount sibling workspace twice: /workspace-parent (stable in-container path) and the host
    # absolute path. leco-app remaps /workspace-parent → host path for docker compose so bind mounts
    # resolve on Docker Desktop (daemon needs host paths, not paths only visible inside this container).
    WORKSPACE_PARENT_MOUNT=(
      -v "$WORKSPACE_PARENT:/workspace-parent:ro"
      -v "$WORKSPACE_PARENT:$WORKSPACE_PARENT:ro"
      -e DASHBOARD_WORKSPACE_PARENT=/workspace-parent
      -e "DASHBOARD_WORKSPACE_PARENT_HOST=$WORKSPACE_PARENT"
      -e "LECO_WORKSPACE_PARENT_HOST=$WORKSPACE_PARENT"
    )
  fi

  # Optional: require a shared secret for Control / Hosted apps / Routes mutations:
  #   -e "DASHBOARD_CONTROL_TOKEN=your-secret"
  # Trusted local only — embed same token in HTML and seed the browser (see docs/DEPLOYMENT.md):
  #   -e "DASHBOARD_INJECT_CONTROL_TOKEN_UI=1"

  # Register/Deploy runs leco-app here; https://kv.lh from inside the container often hits connection
  # refused (Traefik/DNS not reachable the same way). Talk to cloudflare-local adapters on lh-network;
  # leco.local-cf.yaml still gets public https://kv.lh / r2 / d1 unless you set LECO_LOCAL_*_URL.
  LOCAL_CF_INTERNAL=(
    -e LECO_LOCAL_KV_INTERNAL_URL=http://kv-adapter:8082
    -e LECO_LOCAL_R2_INTERNAL_URL=http://r2-adapter:8081
    -e LECO_LOCAL_D1_INTERNAL_URL=http://d1-adapter:8083
  )

  # shellcheck disable=SC2086
  docker run -d \
    --name "$NAME" \
    --restart unless-stopped \
    --network lh-network \
    -p "$HOST_PORT:$CONTAINER_PORT" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "$PROJECT_ROOT:/project:rw" \
    -v "$PROJECT_ROOT:$PROJECT_ROOT:rw" \
    -e "DASHBOARD_DOCKER_BIND_ROOT=$PROJECT_ROOT" \
    -e "DASHBOARD_PROJECT_ROOT_HOST=$PROJECT_ROOT" \
    -e "LECO_PROJECT_ROOT_HOST=$PROJECT_ROOT" \
    "${LOCAL_CF_INTERNAL[@]}" \
    "${WORKSPACE_PARENT_MOUNT[@]}" \
    $HOST_PROC_MOUNT \
    $HOST_SYS_MOUNT \
    $HOST_MAC_TEMP_MOUNT \
    "$IMAGE" || return 1

  # Keep hosting/traefik in a Traefik v3–safe shape (real 01-stack-core copy, no http: {} stub).
  # heal restarts Traefik when its container exists so the file provider reloads reliably on Docker Desktop.
  if [ "${DASHBOARD_SKIP_TRAEFIK_HEAL:-0}" != "1" ]; then
    local traefik_heal="$PROJECT_ROOT/ecosystem-stack/services/traefik.sh"
    if [ -f "$traefik_heal" ]; then
      bash "$traefik_heal" heal || echo "⚠️  Traefik heal failed — if *.lh shows Traefik 404, run: bash $traefik_heal restart"
    fi
  fi

  _darwin_host_metrics_sched install
}

# Alias: full image rebuild + container recreate (same as start).
deploy() {
  # start() runs traefik heal unless skipped; avoid double Traefik restart here.
  DASHBOARD_SKIP_TRAEFIK_HEAL=1 start || return 1
  local traefik_script="$PROJECT_ROOT/ecosystem-stack/services/traefik.sh"
  if [ -f "$traefik_script" ]; then
    echo "🔄 Reloading Traefik after dashboard deploy (hosting/traefik repair + restart)…"
    bash "$traefik_script" heal || return 1
  else
    echo "⚠️  Traefik service script not found at $traefik_script — skipped Traefik reload."
  fi
}

# Recreate container without `docker build` (uses existing image). OK when only bind-mounted code changed.
quick() {
  DASHBOARD_SKIP_BUILD=1 start
}

stop() {
  _darwin_host_metrics_sched uninstall
  docker stop "$NAME" 2>/dev/null || true
}
restart() { stop; start; }
remove() {
  _darwin_host_metrics_sched uninstall
  docker rm -f "$NAME" 2>/dev/null || true
}
pause() { docker pause "$NAME"; }
unpause() { docker unpause "$NAME"; }
status() { docker ps -a --filter "name=^/$NAME$" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"; }
logs() { docker logs -f "$NAME"; }
reset() { remove; }

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  act="${1:-start}"
  shift || true
  case "$act" in
    start) start ;;
    deploy) deploy ;;
    quick) quick ;;
    stop) stop ;;
    restart) restart ;;
    remove) remove ;;
    pause) pause ;;
    unpause) unpause ;;
    status) status ;;
    logs) logs ;;
    reset) reset ;;
    *)
      echo "Usage: $0 {start|deploy|quick|stop|restart|remove|pause|unpause|status|logs|reset}"
      exit 1
      ;;
  esac
fi
