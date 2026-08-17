#!/usr/bin/env bash
#
# LEco DevOps — one-command setup.
#
# Autonomous by default: every question has a defensible answer, so `./setup.sh --yes`
# runs start to finish without a prompt. Interactive mode only exists to let you override
# those answers, never to make progress depend on you being at the keyboard.
#
#   ./setup.sh                          interactive, sensible defaults preselected
#   ./setup.sh --yes                    fully autonomous, no prompts at all
#   ./setup.sh --profile ai-full --yes  autonomous with a named service profile
#   ./setup.sh --services traefik,dashboard,postgres
#   ./setup.sh --admin-user admin --admin-password 'secret'
#   ./setup.sh --no-auth                leave the dashboard unauthenticated
#
# Runs on macOS (workstation and headless server), Debian/Ubuntu, RHEL/Fedora, Arch,
# SUSE, Alpine, and inside WSL2 on Windows. Windows-side steps that WSL2 cannot perform
# (trusting the CA in the Windows store, wildcard DNS for the Windows browser) are
# reported at the end rather than silently skipped.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK_DIR="$PROJECT_ROOT/ecosystem-stack"

# shellcheck source=/dev/null
. "$STACK_DIR/lib/compat.sh"

# ---------------------------------------------------------------------------- output

bold()  { printf "\033[1m%s\033[0m\n" "$*"; }
info()  { printf "ℹ️  %s\n" "$*"; }
ok()    { printf "✅ %s\n" "$*"; }
warn()  { printf "⚠️  %s\n" "$*"; }
err()   { printf "❌ %s\n" "$*" >&2; }
step()  { printf "\n\033[1m▸ %s\033[0m\n" "$*"; }

# ------------------------------------------------------------------------- essentials

# Services the platform cannot function without, and which therefore cannot be
# deselected:
#   traefik   — the edge. Without it no *.lh hostname resolves to anything, so every
#               other service becomes unreachable even when its container is healthy.
#   dashboard — the control plane and single source of truth. Every lifecycle action,
#               the MCP server, and the CLI all operate through its API; without it the
#               platform can still run containers but can no longer be operated.
ESSENTIAL_SERVICES="traefik dashboard"

is_essential() {
  case " $ESSENTIAL_SERVICES " in
    *" $1 "*) return 0 ;;
    *) return 1 ;;
  esac
}

# ---------------------------------------------------------------------------- options

ASSUME_YES=0
PROFILE=""
SERVICES_CSV=""
ADMIN_USER=""
ADMIN_PASSWORD=""
AUTH_CHOICE=""          # "", "on", "off"
INSTALL_MODE="local"
BASE_DOMAIN=""
START_AFTER=1

usage() {
  sed -n '2,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

parse_args() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      -y|--yes|--non-interactive) ASSUME_YES=1 ;;
      --profile)        PROFILE="${2:-}"; shift ;;
      --services)       SERVICES_CSV="${2:-}"; shift ;;
      --admin-user)     ADMIN_USER="${2:-}"; AUTH_CHOICE="on"; shift ;;
      --admin-password) ADMIN_PASSWORD="${2:-}"; AUTH_CHOICE="on"; shift ;;
      --no-auth)        AUTH_CHOICE="off" ;;
      --mode)           INSTALL_MODE="${2:-local}"; shift ;;
      --domain)         BASE_DOMAIN="${2:-}"; shift ;;
      --no-start)       START_AFTER=0 ;;
      -h|--help)        usage; exit 0 ;;
      *) err "Unknown option: $1"; usage; exit 2 ;;
    esac
    shift
  done
}

# Prompt unless running autonomously, in which case take the default without asking.
ask() {
  local prompt="$1" default="$2" ans
  if [ "$ASSUME_YES" = "1" ] || [ ! -t 0 ]; then
    printf '%s\n' "$default"
    return 0
  fi
  read -r -p "$prompt [$default] " ans </dev/tty || ans=""
  printf '%s\n' "${ans:-$default}"
}

ask_yn() {
  local prompt="$1" default="$2" ans
  ans="$(ask "$prompt (y/n)" "$default")"
  case "$(printf '%s' "$ans" | tr '[:upper:]' '[:lower:]')" in
    y|yes) return 0 ;;
    *) return 1 ;;
  esac
}

# --------------------------------------------------------------------------- preflight

PREFLIGHT_FATAL=0

preflight() {
  step "Preflight"
  info "OS: $(compat_os)  |  package manager: $(compat_pkg_manager || echo none)"
  if compat_is_linux && [ "$(compat_linux_family)" != "unknown" ]; then
    info "Linux family: $(compat_linux_family)"
  fi
  if compat_is_wsl; then
    info "Running inside WSL2 — Windows-side steps are listed at the end."
    if compat_is_on_windows_drive "$PROJECT_ROOT"; then
      warn "This checkout lives on the Windows drive ($PROJECT_ROOT)."
      warn "Every file operation crosses the 9p boundary; expect it to be slow and for"
      warn "bind mounts to behave differently. Cloning inside the WSL2 filesystem is strongly advised."
    fi
  fi
  if [ "$(compat_os)" = "windows" ]; then
    err "This is Git Bash/MSYS, not a Linux userland. LEco DevOps needs WSL2 on Windows."
    err "Install WSL2, then run this script from inside the distro."
    exit 1
  fi

  if ! compat_has_cmd docker; then
    err "Docker is not installed."
    info "$(compat_docker_start_hint)"
    PREFLIGHT_FATAL=1
  elif ! docker info >/dev/null 2>&1; then
    err "Docker is installed but the daemon is not reachable."
    info "$(compat_docker_start_hint)"
    PREFLIGHT_FATAL=1
  else
    ok "Docker daemon reachable"
  fi

  if docker compose version >/dev/null 2>&1; then
    ok "Compose v2 plugin present"
  else
    warn "docker compose (v2) is unavailable — dev stacks and infra bundles need it."
  fi

  if compat_has_cmd python3; then
    ok "python3: $(python3 -V 2>&1)"
  else
    err "python3 is missing."
    PREFLIGHT_FATAL=1
  fi

  if [ "$PREFLIGHT_FATAL" = "1" ]; then
    err "Preflight failed. Resolve the above and re-run."
    exit 1
  fi
}

# PyYAML is load-bearing: without it the platform config reads as empty, which silently
# starts every service and reports tls.mode as "mkcert" whatever you configured.
ensure_pyyaml() {
  step "Python dependencies"
  if python3 -c "import yaml" >/dev/null 2>&1; then
    ok "PyYAML available"
    return 0
  fi
  info "PyYAML missing — installing (required to read config/leco-platform.yaml)."
  local pkg
  pkg="$(compat_pkg_name pyyaml)"
  if [ -n "$pkg" ] && compat_pkg_install pyyaml >/dev/null 2>&1 \
     && python3 -c "import yaml" >/dev/null 2>&1; then
    ok "PyYAML installed ($pkg)"
    return 0
  fi
  if python3 -m pip install "PyYAML>=6.0" >/dev/null 2>&1 \
     || python3 -m pip install --break-system-packages "PyYAML>=6.0" >/dev/null 2>&1; then
    if python3 -c "import yaml" >/dev/null 2>&1; then
      ok "PyYAML installed via pip"
      return 0
    fi
  fi
  err "Could not install PyYAML for $(command -v python3)."
  err "Install it and re-run: python3 -m pip install --break-system-packages 'PyYAML>=6.0'"
  exit 1
}

# ------------------------------------------------------------------- service selection

all_services() {
  for f in "$STACK_DIR"/services/*.sh; do
    [ -f "$f" ] || continue
    basename "$f" .sh
  done
}

# Echoes the chosen service list. Essentials are always included, whatever was asked for.
choose_services() {
  local chosen="" svc

  if [ -n "$SERVICES_CSV" ]; then
    chosen="$(printf '%s' "$SERVICES_CSV" | tr ',' ' ')"
  elif [ -n "$PROFILE" ]; then
    chosen="$(python3 "$STACK_DIR/lib/platform_config.py" profile-services "$PROFILE" 2>/dev/null || true)"
    if [ -z "$chosen" ]; then
      warn "Profile '$PROFILE' produced no services; falling back to essentials only." >&2
    fi
  elif [ "$ASSUME_YES" = "1" ] || [ ! -t 0 ]; then
    # Autonomous with no profile: the smallest working platform.
    chosen="$ESSENTIAL_SERVICES"
  else
    bold "" >&2
    bold "Service selection" >&2
    info "Essential services are always installed: $ESSENTIAL_SERVICES" >&2
    info "Everything else can be added now or started later with ./start.sh <service>." >&2
    bold "" >&2
    while IFS= read -r svc; do
      [ -z "$svc" ] && continue
      if is_essential "$svc"; then
        printf "   \033[1m%-20s\033[0m essential — always on\n" "$svc" >&2
        continue
      fi
      if ask_yn "   start '$svc'?" "n"; then
        chosen="$chosen $svc"
      fi
    done < <(all_services | sort)
  fi

  # Union with essentials, order-preserving and de-duplicated.
  local final="" s
  for s in $ESSENTIAL_SERVICES; do
    case " $final " in *" $s "*) ;; *) final="$final $s" ;; esac
  done
  for s in $chosen; do
    [ -f "$STACK_DIR/services/$s.sh" ] || { warn "Unknown service '$s' — ignored." >&2; continue; }
    case " $final " in *" $s "*) ;; *) final="$final $s" ;; esac
  done
  printf '%s\n' "$(printf '%s' "$final" | sed 's/^ *//')"
}

# ------------------------------------------------------------------- dashboard auth

# Hash a password in a format Traefik's basicAuth accepts, using tools that exist on all
# four platforms. openssl's apr1 works identically on OpenSSL and macOS's LibreSSL;
# htpasswd bcrypt is preferred when present because apr1 is MD5-based.
hash_password() {
  local user="$1" password="$2"
  if compat_has_cmd htpasswd; then
    htpasswd -nbB "$user" "$password" 2>/dev/null && return 0
  fi
  local ssl hash
  ssl="$(compat_openssl)"
  hash="$("$ssl" passwd -apr1 "$password" 2>/dev/null)" || return 1
  [ -n "$hash" ] || return 1
  printf '%s:%s\n' "$user" "$hash"
}

configure_dashboard_auth() {
  step "Dashboard access"

  if [ "$AUTH_CHOICE" = "off" ]; then
    info "Skipping dashboard authentication (--no-auth)."
    write_dashboard_auth "" ""
    return 0
  fi

  if [ -z "$AUTH_CHOICE" ]; then
    info "The dashboard's control token only guards actions that change things."
    info "Without a login, anyone who can reach the host can read apps, logs and config."
    if ! ask_yn "Protect the dashboard with a username and password?" "y"; then
      info "Leaving the dashboard unauthenticated."
      write_dashboard_auth "" ""
      return 0
    fi
  fi

  if [ -z "$ADMIN_USER" ]; then
    ADMIN_USER="$(ask "Admin username" "admin")"
  fi

  if [ -z "$ADMIN_PASSWORD" ]; then
    if [ "$ASSUME_YES" = "1" ] || [ ! -t 0 ]; then
      # Autonomous and no password supplied: generate one rather than silently leaving
      # the dashboard open, and print it once so it is not lost.
      ADMIN_PASSWORD="$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20 || true)"
      if [ -z "$ADMIN_PASSWORD" ]; then
        warn "Could not generate a password; leaving the dashboard unauthenticated."
        write_dashboard_auth "" ""
        return 0
      fi
      GENERATED_PASSWORD="$ADMIN_PASSWORD"
    else
      local p1 p2
      read -r -s -p "Password for '$ADMIN_USER': " p1 </dev/tty; echo
      read -r -s -p "Confirm password: " p2 </dev/tty; echo
      if [ -z "$p1" ] || [ "$p1" != "$p2" ]; then
        err "Passwords did not match (or were empty) — leaving the dashboard unauthenticated."
        write_dashboard_auth "" ""
        return 0
      fi
      ADMIN_PASSWORD="$p1"
    fi
  fi

  local entry
  if ! entry="$(hash_password "$ADMIN_USER" "$ADMIN_PASSWORD")"; then
    err "Could not hash the password (no htpasswd, and openssl passwd failed)."
    err "Leaving the dashboard unauthenticated."
    write_dashboard_auth "" ""
    return 0
  fi

  write_dashboard_auth "$ADMIN_USER" "$entry"
  ok "Dashboard will require a login for user '$ADMIN_USER'."
}

# Persist auth into config/leco-platform.yaml. The file is gitignored, and only the hash
# is written — never the plaintext password.
write_dashboard_auth() {
  local user="$1" entry="$2"
  LECO_LIB_DIR="$STACK_DIR/lib" LECO_AUTH_ENTRY="$entry" python3 - <<'PY'
import os
import sys
sys.path.insert(0, os.environ["LECO_LIB_DIR"])
from platform_config import load_platform_config, save_platform_config

entry = os.environ.get("LECO_AUTH_ENTRY", "").strip()
cfg = load_platform_config() or {}
if entry:
    cfg["dashboard_auth"] = {"enabled": True, "users": [entry]}
else:
    cfg["dashboard_auth"] = {"enabled": False, "users": []}
save_platform_config(cfg)
PY
}

# ----------------------------------------------------------------------- persistence

write_enabled_services() {
  local services="$1"
  step "Recording configuration"
  LECO_LIB_DIR="$STACK_DIR/lib" \
  LECO_SERVICES="$services" \
  LECO_MODE="$INSTALL_MODE" \
  LECO_DOMAIN="$BASE_DOMAIN" \
  python3 - <<'PY'
import os
import sys
sys.path.insert(0, os.environ["LECO_LIB_DIR"])
from platform_config import load_platform_config, save_platform_config

cfg = load_platform_config() or {}
cfg["enabled_services"] = os.environ["LECO_SERVICES"].split()
cfg["deployment_mode"] = os.environ.get("LECO_MODE") or "local"
domain = (os.environ.get("LECO_DOMAIN") or "").strip()
if domain:
    cfg["base_domain"] = domain
cfg.setdefault("base_domain", "lh")
cfg.setdefault("tls", {}).setdefault("mode", "mkcert" if cfg["base_domain"] == "lh" else "acme")
save_platform_config(cfg)
print(f"  enabled_services: {' '.join(cfg['enabled_services'])}")
print(f"  base_domain:      {cfg['base_domain']}")
print(f"  tls.mode:         {cfg['tls']['mode']}")
PY
}

render_routes() {
  step "Rendering Traefik routes"
  python3 "$PROJECT_ROOT/scripts/render-platform-traefik.py" --write
}

# --------------------------------------------------------------------------- startup

start_services() {
  local services="$1" svc
  step "Starting services"
  for svc in $services; do
    info "starting $svc…"
    if ! "$STACK_DIR/ecosystem-stack.sh" start "$svc" >/dev/null 2>&1; then
      warn "$svc did not start cleanly — check: ./ecosystem-stack/ecosystem-stack.sh logs $svc"
    else
      ok "$svc"
    fi
  done
}

# ---------------------------------------------------------------------------- summary

summary() {
  local services="$1"
  step "Done"
  bold "Services enabled: $services"

  if [ -n "${GENERATED_PASSWORD:-}" ]; then
    echo
    bold "Dashboard login (generated — save this now, it is not stored in plaintext):"
    printf "    username: %s\n    password: %s\n" "$ADMIN_USER" "$GENERATED_PASSWORD"
  fi

  echo
  info "Dashboard:  http://dashboard.lh   (also http://localhost:8090)"
  warn "Port 8090 bypasses Traefik, so the dashboard login does not apply to it."
  warn "On anything but a private workstation, bind it to 127.0.0.1 or firewall it."

  if compat_is_wsl; then
    echo
    bold "Windows-side steps WSL2 cannot do for you:"
    info "  1. Trust the mkcert CA in the Windows certificate store (the browser runs on Windows)."
    info "  2. Give the Windows resolver a wildcard for *.lh — the WSL2 resolver is invisible to it."
    info "     See docs/CONNECT_AI_AGENTS.md and the windows/ helper scripts."
  fi

  if ! compat_dns_lookup dashboard.lh >/dev/null 2>&1; then
    echo
    warn "dashboard.lh does not resolve yet — the stack is running but unreachable by name."
    info "See docs/SETUP.md §4 for the dnsmasq/resolver setup for your OS."
  fi
}

# ------------------------------------------------------------------------------ main

main() {
  parse_args "$@"

  bold "LEco DevOps setup"
  info "Repo: $PROJECT_ROOT"

  preflight
  ensure_pyyaml

  local services
  services="$(choose_services)"
  ok "Selected: $services"

  write_enabled_services "$services"
  configure_dashboard_auth
  render_routes

  if [ "$START_AFTER" = "1" ]; then
    start_services "$services"
  else
    info "Skipping startup (--no-start). Run ./start.sh when ready."
  fi

  summary "$services"
}

main "$@"
