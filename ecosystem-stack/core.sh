CORE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$CORE_DIR/.." && pwd)"

# Cross-platform primitives (OS + distro + package manager detection, sudo handling,
# LibreSSL-vs-OpenSSL differences). Sourced here so every service script, the CLI and
# the installer inherit it without each having to find it.
# shellcheck source=/dev/null
[ -f "$CORE_DIR/lib/compat.sh" ] && . "$CORE_DIR/lib/compat.sh"
SERVICES_DIR="$CORE_DIR/services"
NETWORK_NAME="${NETWORK_NAME:-lh-network}"
NETWORK_CONTAINERS="traefik open-webui ollama airllm n8n_postgres n8n paperclip_postgres paperclip service-dashboard leco-update-catalog leco-mcp minio valkey r2-adapter kv-adapter d1-adapter browser-rendering-local workers-runtime autoscaler autoscale-demo mysql redis mailpit telegram-gateway cache-nginx cache-varnish adminer redis-commander leco-sftp leco-ftp leco-file-browser"
# mcp starts after dashboard: it proxies the dashboard API and is useless before it is up.
START_ORDER="traefik postgres ollama airllm webui n8n paperclip-postgres paperclip dashboard mcp update-catalog cloudflare-local infra file-transfer"

get_services() {
  for file in $SERVICES_DIR/*.sh; do
    basename "$file" .sh
  done
}

_platform_enabled_services() {
  if [ ! -f "$PROJECT_ROOT/config/leco-platform.yaml" ] && \
     [ ! -f "$CORE_DIR/config/install-selection.env" ]; then
    return 0
  fi

  local _out _err _status
  _err="$(mktemp -t leco-enabled-services)"
  _out="$(python3 "$CORE_DIR/lib/platform_config.py" enabled-services 2>"$_err")"
  _status=$?

  if [ "$_status" -eq 0 ]; then
    rm -f "$_err"
    printf '%s\n' "$_out"
    return 0
  fi

  # Returning empty here means "unrestricted", so every service in START_ORDER starts —
  # the opposite of what an operator who curated enabled_services asked for. The warning
  # itself is emitted by _platform_config_warn_if_broken, which runs in the caller's
  # shell; this function is always invoked inside a command substitution, so a guard
  # variable set here would be discarded with the subshell and warn on every call.
  rm -f "$_err"
  return 0
}

# Warn once per shell if the platform config cannot be read. Must be called from the
# parent shell, not inside "$( )", for the guard variable to persist.
_platform_config_warn_if_broken() {
  [ -n "${_LECO_WARNED_PLATFORM_CONFIG:-}" ] && return 0
  if [ ! -f "$PROJECT_ROOT/config/leco-platform.yaml" ] && \
     [ ! -f "$CORE_DIR/config/install-selection.env" ]; then
    return 0
  fi
  local _err
  _err="$(mktemp -t leco-enabled-services)"
  if python3 "$CORE_DIR/lib/platform_config.py" enabled-services >/dev/null 2>"$_err"; then
    rm -f "$_err"
    return 0
  fi
  _LECO_WARNED_PLATFORM_CONFIG=1
  echo "⚠️  Could not read enabled_services from config/leco-platform.yaml — starting ALL services." >&2
  if grep -q "No module named 'yaml'" "$_err" 2>/dev/null; then
    echo "    Cause: PyYAML is not installed for $(command -v python3)." >&2
    echo "    Fix:   python3 -m pip install --user pyyaml   (or: pip install -r ecosystem-stack/requirements.txt)" >&2
  elif [ -s "$_err" ]; then
    sed 's/^/    /' "$_err" >&2
  fi
  rm -f "$_err"
  return 0
}

get_services_in_start_order() {
  local filtered
  _platform_config_warn_if_broken
  filtered="$(_platform_enabled_services)"
  if [ -n "$filtered" ]; then
    while IFS= read -r svc; do
      [ -z "$svc" ] && continue
      if [ -f "$SERVICES_DIR/$svc.sh" ]; then
        echo "$svc"
      fi
    done <<<"$filtered"
    return 0
  fi
  for svc in $START_ORDER; do
    if [ -f "$SERVICES_DIR/$svc.sh" ]; then
      echo "$svc"
    fi
  done
}

service_enabled() {
  local svc=$1
  local filtered
  filtered="$(_platform_enabled_services)"
  if [ -z "$filtered" ]; then
    return 0
  fi
  while IFS= read -r s; do
    [ "$s" = "$svc" ] && return 0
  done <<<"$filtered"
  return 1
}

service_exists() {
  [ -f "$SERVICES_DIR/$1.sh" ]
}

ensure_network_exists() {
  if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
    echo "🛠️ Creating missing Docker network: $NETWORK_NAME"
    docker network create "$NETWORK_NAME" >/dev/null
  fi
}

is_container_connected() {
  container_name=$1
  networks=$(docker inspect -f '{{range $k, $_ := .NetworkSettings.Networks}}{{printf "%s " $k}}{{end}}' "$container_name" 2>/dev/null)

  case " $networks " in
    *" $NETWORK_NAME "*) return 0 ;;
    *) return 1 ;;
  esac
}

connect_container_to_network() {
  container_name=$1

  if ! docker inspect "$container_name" >/dev/null 2>&1; then
    return 0
  fi

  if is_container_connected "$container_name"; then
    echo "ℹ️ $container_name is already connected to $NETWORK_NAME"
    return 0
  fi

  if docker network connect "$NETWORK_NAME" "$container_name" >/dev/null 2>&1; then
    echo "✅ Connected $container_name to $NETWORK_NAME"
  else
    echo "⚠️ Failed to connect $container_name to $NETWORK_NAME"
  fi
}

repair_network_links() {
  ensure_network_exists

  for container_name in $NETWORK_CONTAINERS; do
    connect_container_to_network "$container_name"
    # Align with service scripts / compose: survive Docker daemon restart unless explicitly stopped.
    if docker inspect "$container_name" >/dev/null 2>&1; then
      docker update --restart unless-stopped "$container_name" >/dev/null 2>&1 || true
    fi
  done
}

run_service() {
  svc=$1
  action=$2

  if [ "$action" = "start" ] || [ "$action" = "restart" ]; then
    if ! service_enabled "$svc"; then
      echo "ℹ️ Skipping disabled service: $svc (see config/leco-platform.yaml)"
      return 0
    fi
  fi

  if ! service_exists "$svc"; then
    echo "❌ Unknown service: $svc"
    return 1
  fi

  source "$SERVICES_DIR/$svc.sh"

  if declare -F "$action" >/dev/null 2>&1; then
    "$action"
    return $?
  fi

  if [ -z "$NAME" ]; then
    echo "❌ Service '$svc' is missing NAME in service script."
    return 1
  fi

  case "$action" in
    pause)
      docker pause "$NAME"
      ;;
    unpause)
      docker unpause "$NAME"
      ;;
    status)
      docker ps -a --filter "name=^/$NAME$" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
      ;;
    *)
      echo "❌ Action '$action' not supported for service '$svc'"
      return 1
      ;;
  esac
}

run_all() {
  action=$1
  has_errors=false

  if [ "$action" = "start" ] || [ "$action" = "restart" ]; then
    svc_list="$(get_services_in_start_order)"
  else
    svc_list="$(get_services)"
  fi

  for svc in $svc_list; do
    if ! run_service "$svc" "$action"; then
      has_errors=true
    fi
  done

  if [ "$action" = "start" ] || [ "$action" = "restart" ]; then
    repair_network_links
  fi

  if [ "$has_errors" = true ]; then
    return 1
  fi
}

# Bulk ops for the dashboard Control API (`bulk_ecosystem`):
# - Always skip `dashboard` on stop / pause / remove / reset so the HTTP request can finish.
# - Skip ECOSYSTEM_BULK_PLATFORM_SKIP (default: traefik postgres) on those same phases so routing
#   and the shared DB stay up. Legacy: ECOSYSTEM_BULK_PAUSE_SKIP is honored if PLATFORM_SKIP is unset.
# - After restart|deploy|recreate, the start phase skips `start` for platform services that are
#   still running (their service scripts often recreate the container on every start).

_bulk_platform_skip_csv() {
  printf '%s' "${ECOSYSTEM_BULK_PLATFORM_SKIP:-${ECOSYSTEM_BULK_PAUSE_SKIP:-traefik postgres}}"
}

_service_is_running() {
  local svc=$1
  local NAME
  service_exists "$svc" || return 1
  # shellcheck source=/dev/null
  source "$SERVICES_DIR/$svc.sh"
  [ -n "${NAME:-}" ] || return 1
  [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" = "true" ]
}

# Reverse start order: skip dashboard + platform list.
_bulk_foreach_reverse_reserve_core() {
  local op=$1
  local svc
  local skip_platform
  skip_platform=" $(_bulk_platform_skip_csv) "
  local -a ordered
  while IFS= read -r svc; do
    [ -n "$svc" ] && ordered+=("$svc")
  done < <(get_services_in_start_order)
  local i
  for ((i = ${#ordered[@]} - 1; i >= 0; i--)); do
    svc="${ordered[i]}"
    [ "$svc" = "dashboard" ] && continue
    case "$skip_platform" in *" $svc "*) continue ;; esac
    echo "▶ ecosystem bulk $op: $svc"
    run_service "$svc" "$op" || true
  done
}

_bulk_stop_preserving_core() {
  _bulk_foreach_reverse_reserve_core stop
}

# Forward start: start each service; leave platform services alone if already running (see header).
_bulk_start_all_after_bulk_teardown() {
  local svc
  local has_errors=false
  local skip_platform
  skip_platform=" $(_bulk_platform_skip_csv) "
  while IFS= read -r svc; do
    [ -z "$svc" ] && continue
    case "$skip_platform" in *" $svc "*)
      if _service_is_running "$svc"; then
        echo "ℹ️ ecosystem bulk start: skip $svc (core infra still running)"
        continue
      fi
      ;;
    esac
    echo "▶ ecosystem bulk start: $svc"
    if ! run_service "$svc" start; then
      has_errors=true
    fi
  done < <(get_services_in_start_order)
  repair_network_links
  if [ "$has_errors" = true ]; then
    return 1
  fi
}

# Forward start order, every service including dashboard (unpause after bulk pause).
_bulk_foreach_forward_all() {
  local op=$1
  local svc
  while IFS= read -r svc; do
    [ -z "$svc" ] && continue
    echo "▶ ecosystem bulk $op: $svc"
    run_service "$svc" "$op" || true
  done < <(get_services_in_start_order)
}

# start | stop | restart | deploy | pause | unpause | remove | reset | recreate
# (restart/deploy: teardown preserving core, then start-all with running-core skip)
# (recreate: remove preserving core, then same start phase)
bulk_ecosystem() {
  action=$1
  ensure_network_exists
  case "$action" in
    start)
      run_all start
      ;;
    stop)
      _bulk_stop_preserving_core
      ;;
    restart|deploy)
      _bulk_stop_preserving_core
      _bulk_start_all_after_bulk_teardown
      ;;
    pause)
      _bulk_foreach_reverse_reserve_core pause
      ;;
    unpause)
      _bulk_foreach_forward_all unpause
      ;;
    remove)
      _bulk_foreach_reverse_reserve_core remove
      ;;
    reset)
      _bulk_foreach_reverse_reserve_core reset
      ;;
    recreate)
      _bulk_foreach_reverse_reserve_core remove
      _bulk_start_all_after_bulk_teardown
      ;;
    *)
      echo "❌ bulk_ecosystem: unknown action: $action"
      return 1
      ;;
  esac
}
