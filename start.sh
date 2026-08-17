#!/usr/bin/env bash
#
# LEco DevOps — start the platform.
#
#   ./start.sh                 start every enabled service, in dependency order
#   ./start.sh dashboard       start one service (and whatever it depends on)
#   ./start.sh --status        show what is running without changing anything
#   ./start.sh --stop          stop everything that is running
#   ./start.sh --restart       stop then start
#
# Dependency order is not optional: Traefik must exist before anything registers a route,
# postgres before n8n, paperclip-postgres before paperclip, and the dashboard before the
# MCP server that proxies it. Starting services in the order they happen to appear in a
# directory listing produces containers that are "up" but broken, which is the single most
# common way this platform looks unhealthy for no visible reason.
#
# Reads the enabled-services list written by ./setup.sh, so it starts what you chose —
# not everything that exists.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK_DIR="$PROJECT_ROOT/ecosystem-stack"
STACK_SH="$STACK_DIR/ecosystem-stack.sh"

# shellcheck source=/dev/null
. "$STACK_DIR/lib/compat.sh"

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
info() { printf "ℹ️  %s\n" "$*"; }
ok()   { printf "✅ %s\n" "$*"; }
warn() { printf "⚠️  %s\n" "$*"; }
err()  { printf "❌ %s\n" "$*" >&2; }

ACTION="start"
TARGET=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --status)  ACTION="status" ;;
    --stop)    ACTION="stop" ;;
    --restart) ACTION="restart" ;;
    -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) err "Unknown option: $1"; exit 2 ;;
    *)  TARGET="$1" ;;
  esac
  shift
done

require_docker() {
  if ! compat_has_cmd docker || ! docker info >/dev/null 2>&1; then
    err "Docker is not reachable."
    info "$(compat_docker_start_hint)"
    exit 1
  fi
}

# Enabled services in dependency order. get_services_in_start_order already intersects
# the configured list with START_ORDER, and now warns loudly if it cannot read the config
# rather than silently returning everything.
enabled_in_order() {
  # shellcheck source=/dev/null
  ( . "$STACK_DIR/core.sh" >/dev/null 2>&1; get_services_in_start_order )
}

case "$ACTION" in
  status)
    require_docker
    if [ -n "$TARGET" ]; then
      exec "$STACK_SH" status "$TARGET"
    fi
    exec "$STACK_SH" status
    ;;

  stop)
    require_docker
    if [ -n "$TARGET" ]; then
      exec "$STACK_SH" stop "$TARGET"
    fi
    bold "Stopping services (reverse dependency order)"
    # Reverse the start order so dependents go down before what they depend on.
    enabled_in_order | sed '1!G;h;$!d' | while IFS= read -r svc; do
      [ -z "$svc" ] && continue
      info "stopping $svc…"
      "$STACK_SH" stop "$svc" >/dev/null 2>&1 || warn "$svc did not stop cleanly"
    done
    ok "Stopped."
    ;;

  restart)
    "$0" --stop ${TARGET:+"$TARGET"}
    exec "$0" ${TARGET:+"$TARGET"}
    ;;

  start)
    require_docker
    if [ -n "$TARGET" ]; then
      if [ ! -f "$STACK_DIR/services/$TARGET.sh" ]; then
        err "Unknown service: $TARGET"
        info "Available: $(cd "$STACK_DIR/services" && ls *.sh | sed 's/\.sh$//' | tr '\n' ' ')"
        exit 2
      fi
      exec "$STACK_SH" start "$TARGET"
    fi

    services="$(enabled_in_order)"
    if [ -z "$services" ]; then
      err "No services are enabled."
      info "Run ./setup.sh first, or start one explicitly: ./start.sh dashboard"
      exit 1
    fi

    bold "Starting services (dependency order)"
    failed=""
    for svc in $services; do
      info "starting $svc…"
      if "$STACK_SH" start "$svc" >/dev/null 2>&1; then
        ok "$svc"
      else
        warn "$svc failed"
        failed="$failed $svc"
      fi
    done

    echo
    if [ -n "$failed" ]; then
      warn "Failed:$failed"
      info "Logs: ./ecosystem-stack/ecosystem-stack.sh logs <service>"
    else
      ok "All enabled services started."
    fi

    if ! compat_dns_lookup dashboard.lh >/dev/null 2>&1; then
      warn "dashboard.lh does not resolve — services are up but unreachable by hostname."
      info "Reach it directly meanwhile: http://localhost:8090"
    else
      info "Dashboard: http://dashboard.lh"
    fi
    ;;
esac
