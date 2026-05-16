#!/usr/bin/env bash
set -euo pipefail

STACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$STACK_DIR/.." && pwd)"

# shellcheck source=/dev/null
source "$STACK_DIR/core.sh"

PROFILE_FILE="$STACK_DIR/config/install-selection.env"
OS_NAME="$(uname -s)"

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
info() { printf "ℹ️  %s\n" "$*"; }
ok() { printf "✅ %s\n" "$*"; }
warn() { printf "⚠️  %s\n" "$*"; }
err() { printf "❌ %s\n" "$*"; }

has_cmd() { command -v "$1" >/dev/null 2>&1; }

ask_yes_no() {
  local prompt=$1
  local default=${2:-N}
  local ans
  local suffix
  if [ "$default" = "Y" ]; then
    suffix="[Y/n]"
  else
    suffix="[y/N]"
  fi
  read -r -p "$prompt $suffix " ans
  ans="$(printf '%s' "$ans" | tr '[:upper:]' '[:lower:]')"
  if [ -z "$ans" ]; then
    [ "$default" = "Y" ] && return 0 || return 1
  fi
  case "$ans" in
    y|yes) return 0 ;;
    *) return 1 ;;
  esac
}

install_with_brew() {
  local formula=$1
  local is_cask=${2:-0}
  if ! has_cmd brew; then
    err "Homebrew is not installed. Install Homebrew first: https://brew.sh"
    return 1
  fi
  if [ "$is_cask" = "1" ]; then
    brew list --cask "$formula" >/dev/null 2>&1 || brew install --cask "$formula"
  else
    brew list "$formula" >/dev/null 2>&1 || brew install "$formula"
  fi
}

install_with_apt() {
  local pkg_list=("$@")
  if ! has_cmd apt-get; then
    err "apt-get is not available on this host."
    return 1
  fi
  sudo apt-get update
  sudo apt-get install -y "${pkg_list[@]}"
}

ensure_cert_files() {
  local cert_dir="$PROJECT_ROOT/certs"
  local cert_file="$cert_dir/wildcard.lh.pem"
  local key_file="$cert_dir/wildcard.lh-key.pem"
  mkdir -p "$cert_dir"
  if [ -f "$cert_file" ] && [ -f "$key_file" ]; then
    ok "TLS certs already present: certs/wildcard.lh.pem + certs/wildcard.lh-key.pem"
    return 0
  fi
  if ! has_cmd mkcert; then
    warn "mkcert not found. Skipping TLS cert generation."
    return 0
  fi
  if ask_yes_no "Generate local TLS certs for *.lh now?" "Y"; then
    mkcert -install || true
    mkcert -cert-file "$cert_file" -key-file "$key_file" "*.lh" localhost 127.0.0.1 ::1
    ok "Generated TLS certs under certs/."
  else
    warn "Skipped cert generation. You can run this later:"
    printf "   mkcert -cert-file certs/wildcard.lh.pem -key-file certs/wildcard.lh-key.pem \"*.lh\" localhost 127.0.0.1 ::1\n"
  fi
}

ensure_dependency() {
  local name=$1
  local cmd=$2
  local required=${3:-1}
  if has_cmd "$cmd"; then
    ok "$name is installed ($cmd)"
    return 0
  fi

  if [ "$required" = "1" ]; then
    warn "$name is missing."
  else
    info "$name is optional and currently missing."
  fi

  if ! ask_yes_no "Install $name now?" "N"; then
    [ "$required" = "1" ] && warn "$name remains missing."
    return 1
  fi

  case "$OS_NAME" in
    Darwin)
      case "$cmd" in
        docker)
          install_with_brew docker 1
          ;;
        mkcert|dnsmasq|jq|curl|python3|git)
          install_with_brew "$cmd" 0
          ;;
        *)
          err "No auto-install mapping for $name on macOS."
          return 1
          ;;
      esac
      ;;
    Linux)
      case "$cmd" in
        docker)
          install_with_apt docker.io docker-compose-plugin
          ;;
        mkcert|dnsmasq|jq|curl|python3|git)
          install_with_apt "$cmd"
          ;;
        *)
          err "No auto-install mapping for $name on Linux."
          return 1
          ;;
      esac
      ;;
    *)
      err "Unsupported OS for auto-install: $OS_NAME"
      return 1
      ;;
  esac

  if has_cmd "$cmd"; then
    ok "$name installed successfully."
    return 0
  fi
  err "$name install did not provide command '$cmd'."
  return 1
}

check_docker_ready() {
  if ! has_cmd docker; then
    err "Docker CLI is missing."
    return 1
  fi
  if docker info >/dev/null 2>&1; then
    ok "Docker daemon is reachable."
    return 0
  fi
  warn "Docker daemon is not reachable yet."
  if [ "$OS_NAME" = "Darwin" ] && ask_yes_no "Open Docker Desktop now?" "Y"; then
    open -a Docker || true
  fi
  warn "Start Docker, then rerun this installer if daemon is still unavailable."
  return 1
}

check_compose_plugin() {
  if docker compose version >/dev/null 2>&1; then
    ok "Docker Compose v2 plugin is available."
    return 0
  fi
  warn "Docker Compose v2 plugin is missing."
  if ask_yes_no "Attempt to install Docker Compose v2 plugin now?" "N"; then
    case "$OS_NAME" in
      Darwin)
        # Docker Desktop bundles compose plugin.
        warn "Compose plugin usually comes with Docker Desktop; ensure Docker Desktop is updated."
        ;;
      Linux)
        install_with_apt docker-compose-plugin || true
        ;;
    esac
  fi
  docker compose version >/dev/null 2>&1
}


run_service_selection() {
  local -a selected=()
  local -a skipped=()
  local svc

  bold ""
  bold "Service selection"
  info "Choose which ecosystem services to start now."
  info "Unselected services remain available to install/start later."
  bold ""

  while IFS= read -r svc; do
    [ -z "$svc" ] && continue
    if ask_yes_no "Start service '$svc' now?" "N"; then
      info "Starting $svc..."
      if run_service "$svc" start; then
        selected+=("$svc")
      else
        warn "Service '$svc' failed to start. You can retry later."
        skipped+=("$svc")
      fi
    else
      skipped+=("$svc")
    fi
  done < <(get_services_in_start_order)

  if [ "${#selected[@]}" -gt 0 ]; then
    repair_network_links || true
  fi

  {
    echo "# Generated by ecosystem-stack/install-foundation.sh"
    echo "GENERATED_AT=\"$(date -u +"%Y-%m-%dT%H:%M:%SZ")\""
    echo "SELECTED_SERVICES=\"${selected[*]}\""
    echo "SKIPPED_SERVICES=\"${skipped[*]}\""
  } >"$PROFILE_FILE"

  bold ""
  bold "Install summary"
  if [ "${#selected[@]}" -gt 0 ]; then
    ok "Started now: ${selected[*]}"
  else
    warn "No services were started in this run."
  fi
  if [ "${#skipped[@]}" -gt 0 ]; then
    info "Deferred: ${skipped[*]}"
  fi
  info "Selection file saved: $PROFILE_FILE"
  echo
  info "Install deferred services later with:"
  echo "  ./ecosystem-stack/ecosystem-stack.sh start <service>"
}

main() {
  bold "LEco DevOps foundation installer"
  info "Repo root: $PROJECT_ROOT"
  info "OS: $OS_NAME"
  echo

  ensure_dependency "Git" git 1 || true
  ensure_dependency "curl" curl 1 || true
  ensure_dependency "Python 3" python3 1 || true
  ensure_dependency "Docker" docker 1 || true
  check_docker_ready || true
  check_compose_plugin || warn "Compose plugin check failed."
  ensure_dependency "mkcert (TLS cert tooling)" mkcert 0 || true
  ensure_dependency "dnsmasq (*.lh local DNS)" dnsmasq 0 || true
  ensure_dependency "jq (optional CLI helper)" jq 0 || true

  ensure_cert_files
  ensure_network_exists
  ok "Docker network ensured: $NETWORK_NAME"

  run_service_selection
}

main "$@"
