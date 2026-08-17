#!/usr/bin/env bash
#
# LEco DevOps — remove everything an install put on this machine.
#
#   ./uninstall.sh              interactive; asks before each destructive group
#   ./uninstall.sh --dry-run    show what would be removed, touch nothing
#   ./uninstall.sh --yes        no prompts (still keeps data volumes unless --purge)
#   ./uninstall.sh --purge      also delete data volumes — databases, models, uploads
#
# Installation reaches well outside this directory, and deleting the clone leaves all of
# it behind: a dnsmasq rule and a resolver file, a trusted root CA in the system keychain,
# a launchd agent or systemd unit, Docker networks and volumes. This removes them in
# dependency order and says what it could not.
#
# Data volumes are kept unless you pass --purge, because "uninstall" and "delete my
# databases" are different intentions and only one of them is reversible.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=/dev/null
. "$PROJECT_ROOT/ecosystem-stack/lib/compat.sh"

DRY_RUN=0
ASSUME_YES=0
PURGE=0

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
info() { printf "ℹ️  %s\n" "$*"; }
ok()   { printf "✅ %s\n" "$*"; }
warn() { printf "⚠️  %s\n" "$*"; }
err()  { printf "❌ %s\n" "$*" >&2; }
step() { printf "\n\033[1m▸ %s\033[0m\n" "$*"; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    -y|--yes)  ASSUME_YES=1 ;;
    --purge)   PURGE=1 ;;
    -h|--help) sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) err "Unknown option: $1"; exit 2 ;;
  esac
  shift
done

run() {
  if [ "$DRY_RUN" = "1" ]; then
    printf '   would run: %s\n' "$*"
    return 0
  fi
  "$@"
}

# Same, but silences the command's own chatter on a real run. Call sites must not add
# their own `>/dev/null`, or it would hide the dry-run preview instead of the output.
run_quiet() {
  if [ "$DRY_RUN" = "1" ]; then
    printf '   would run: %s\n' "$*"
    return 0
  fi
  "$@" >/dev/null 2>&1
}

# Success message that stays silent during a dry run. Without this the preview claims
# work it did not do, which is the one thing an uninstaller must never do.
did() {
  [ "$DRY_RUN" = "1" ] && return 0
  ok "$*"
}

confirm() {
  local prompt="$1"
  [ "$DRY_RUN" = "1" ] && return 1
  [ "$ASSUME_YES" = "1" ] && return 0
  local ans
  read -r -p "$prompt [y/N] " ans </dev/tty || ans=""
  case "$(printf '%s' "$ans" | tr '[:upper:]' '[:lower:]')" in
    y|yes) return 0 ;;
    *) return 1 ;;
  esac
}

bold "LEco DevOps — uninstall"
[ "$DRY_RUN" = "1" ] && warn "DRY RUN — nothing will be changed."
info "Repo: $PROJECT_ROOT"

# ------------------------------------------------------------------------ 1 · autostart

step "1/7  Autostart"
if [ -x "$PROJECT_ROOT/ecosystem-stack/scripts/install-autostart.sh" ]; then
  run "$PROJECT_ROOT/ecosystem-stack/scripts/install-autostart.sh" --remove || warn "autostart removal reported an error"
else
  info "No autostart script present."
fi

# macOS metrics writer — installed by dashboard.sh, and orphaned if the dashboard
# container was stopped by hand rather than through the service script.
if compat_is_darwin && [ -x "$PROJECT_ROOT/ecosystem-stack/scripts/macos-host-metrics-scheduler.sh" ]; then
  if run_quiet "$PROJECT_ROOT/ecosystem-stack/scripts/macos-host-metrics-scheduler.sh" uninstall; then
    did "Removed host-metrics LaunchAgent"
  else
    info "No host-metrics LaunchAgent to remove"
  fi
fi

# ------------------------------------------------------------------------ 2 · services

step "2/7  Stop services"
if compat_has_cmd docker && docker info >/dev/null 2>&1; then
  if [ -x "$PROJECT_ROOT/start.sh" ]; then
    run "$PROJECT_ROOT/start.sh" --stop || warn "Some services did not stop cleanly"
  fi
else
  warn "Docker is not reachable — skipping container teardown."
  info "$(compat_docker_start_hint)"
fi

# ---------------------------------------------------------------------- 3 · containers

step "3/7  Containers"
if compat_has_cmd docker && docker info >/dev/null 2>&1; then
  # core.sh knows every container this platform creates.
  # shellcheck source=/dev/null
  names="$( . "$PROJECT_ROOT/ecosystem-stack/core.sh" >/dev/null 2>&1; printf '%s' "${NETWORK_CONTAINERS:-}" )"
  found=""
  for c in $names; do
    if docker ps -a --format '{{.Names}}' | grep -qx "$c"; then
      found="$found $c"
    fi
  done
  if [ -n "$found" ]; then
    info "Removing:$found"
    for c in $found; do
      run_quiet docker rm -f "$c" || warn "could not remove $c"
    done
    did "Containers removed"
  else
    info "No platform containers present."
  fi
fi

# ------------------------------------------------------------------------- 4 · volumes

step "4/7  Data volumes"
if compat_has_cmd docker && docker info >/dev/null 2>&1; then
  vols="$(docker volume ls --format '{{.Name}}' 2>/dev/null | grep -iE 'ollama|n8n|paperclip|postgres|minio|valkey|airllm|openwebui|open-webui|leco' || true)"
  if [ -z "$vols" ]; then
    info "No matching data volumes."
  elif [ "$PURGE" = "1" ]; then
    warn "These volumes hold databases, models and uploads — deletion is not reversible:"
    printf '%s\n' "$vols" | sed 's/^/     /'
    if confirm "Delete these volumes permanently?"; then
      printf '%s\n' "$vols" | while IFS= read -r v; do
        [ -z "$v" ] && continue
        run_quiet docker volume rm "$v" || warn "could not remove volume $v"
      done
      did "Volumes deleted"
    else
      info "Kept."
    fi
  else
    info "Keeping $(printf '%s\n' "$vols" | grep -c .) data volume(s). Pass --purge to delete them:"
    printf '%s\n' "$vols" | sed 's/^/     /'
  fi
fi

# ------------------------------------------------------------------------- 5 · network

step "5/7  Docker network"
if compat_has_cmd docker && docker info >/dev/null 2>&1; then
  net="${NETWORK_NAME:-lh-network}"
  if docker network inspect "$net" >/dev/null 2>&1; then
    if run_quiet docker network rm "$net"; then
      did "Removed network $net"
    else
      warn "Could not remove $net (something may still be attached)"
    fi
  else
    info "Network $net not present."
  fi
fi

# ----------------------------------------------------------------------------- 6 · DNS

step "6/7  DNS (*.lh)"
if [ -x "$PROJECT_ROOT/ecosystem-stack/scripts/dns-setup.sh" ]; then
  if confirm "Remove the *.lh DNS configuration (dnsmasq rule / resolver / hosts block)?" || [ "$ASSUME_YES" = "1" ]; then
    run "$PROJECT_ROOT/ecosystem-stack/scripts/dns-setup.sh" --remove || warn "DNS removal reported an error"
  else
    info "Kept — other projects may rely on the .lh resolver."
  fi
else
  info "No dns-setup.sh present."
fi

# --------------------------------------------------------------------------- 7 · certs

step "7/7  TLS trust"
if compat_has_cmd mkcert; then
  caroot="$(mkcert -CAROOT 2>/dev/null || true)"
  info "mkcert CA lives at: ${caroot:-<unknown>}"
  warn "The local CA is left installed on purpose — mkcert is shared, and removing it"
  warn "would break every other project that trusts certificates it signed."
  info "To remove it anyway (affects all mkcert users on this machine):  mkcert -uninstall"
else
  info "mkcert not installed."
fi

if compat_is_wsl; then
  echo
  warn "Windows-side leftovers this cannot reach:"
  info "  windows\\Install-LhDns.ps1 -Remove        (hosts entries)"
  info "  windows\\Import-LecoRootCa.ps1 -Remove    (CA in the Windows store)"
fi

# --------------------------------------------------------------------------- summary

echo
if [ "$DRY_RUN" = "1" ]; then
  bold "Dry run complete — nothing was changed."
else
  bold "Uninstall complete."
fi
echo
info "Left in place on purpose:"
info "  • this checkout — delete it yourself when ready"
info "  • config/leco-platform.yaml (gitignored local configuration)"
info "  • certs/ (regenerate with ./certs/generate-certs.sh)"
[ "$PURGE" = "0" ] && info "  • Docker data volumes — re-run with --purge to delete them"
