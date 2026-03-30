CORE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$CORE_DIR/.." && pwd)"
SERVICES_DIR="$CORE_DIR/services"
NETWORK_NAME="${NETWORK_NAME:-lh-network}"
NETWORK_CONTAINERS="traefik open-webui ollama n8n_postgres n8n service-dashboard minio valkey r2-adapter kv-adapter d1-adapter workers-runtime autoscaler autoscale-demo"
START_ORDER="traefik postgres ollama webui n8n dashboard cloudflare-local"

get_services() {
  for file in $SERVICES_DIR/*.sh; do
    basename "$file" .sh
  done
}

get_services_in_start_order() {
  for svc in $START_ORDER; do
    if [ -f "$SERVICES_DIR/$svc.sh" ]; then
      echo "$svc"
    fi
  done
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

# Bulk ops for the dashboard Control API: stopping this dashboard mid-request would kill the HTTP
# connection, so we skip the "dashboard" service on stop-like phases. Start always runs full START_ORDER.

# Reverse start order, skip dashboard (stop / pause / remove / reset per service).
_bulk_foreach_reverse_skip_dashboard() {
  local op=$1
  local svc
  local -a ordered
  while IFS= read -r svc; do
    [ -n "$svc" ] && ordered+=("$svc")
  done < <(get_services_in_start_order)
  local i
  for ((i = ${#ordered[@]} - 1; i >= 0; i--)); do
    svc="${ordered[i]}"
    [ "$svc" = "dashboard" ] && continue
    echo "▶ ecosystem bulk $op: $svc"
    run_service "$svc" "$op" || true
  done
}

_bulk_stop_all_except_dashboard() {
  _bulk_foreach_reverse_skip_dashboard stop
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
# (restart/deploy: stop-all-except-dashboard, then start-all)
# (recreate: remove-all-except-dashboard, then start-all)
bulk_ecosystem() {
  action=$1
  ensure_network_exists
  case "$action" in
    start)
      run_all start
      ;;
    stop)
      _bulk_stop_all_except_dashboard
      ;;
    restart|deploy)
      _bulk_stop_all_except_dashboard
      run_all start
      ;;
    pause)
      _bulk_foreach_reverse_skip_dashboard pause
      ;;
    unpause)
      _bulk_foreach_forward_all unpause
      ;;
    remove)
      _bulk_foreach_reverse_skip_dashboard remove
      ;;
    reset)
      _bulk_foreach_reverse_skip_dashboard reset
      ;;
    recreate)
      _bulk_foreach_reverse_skip_dashboard remove
      run_all start
      ;;
    *)
      echo "❌ bulk_ecosystem: unknown action: $action"
      return 1
      ;;
  esac
}
