#!/usr/bin/env bash
#
# Start LEco DevOps automatically at boot.
#
#   ./ecosystem-stack/scripts/install-autostart.sh            install
#   ./ecosystem-stack/scripts/install-autostart.sh --status    report only
#   ./ecosystem-stack/scripts/install-autostart.sh --remove    uninstall
#   ./ecosystem-stack/scripts/install-autostart.sh --system    force the boot-time variant
#
# Why this is not already covered by `--restart unless-stopped`
# ------------------------------------------------------------
# Docker's restart policy brings a *container* back when the daemon returns. It does not
# start services in dependency order, and it does nothing at all until the daemon itself
# starts — which on macOS means someone logging in and Docker Desktop launching. So a
# rebooted machine comes back with containers racing each other: Traefik after the apps
# that wanted to register routes, n8n before its Postgres. They are "up" and broken.
#
# This installs a unit that runs ./start.sh, which walks START_ORDER properly.
#
# Variants, because "autostart" means two different things:
#   Linux    systemd system unit                 — boot time, no login required
#   macOS    LaunchAgent (gui/<uid>)             — needs a logged-in session
#            LaunchDaemon (system)  --system     — boot time, for a headless server
#
# The macOS distinction matters: a LaunchAgent in the `gui/` domain cannot load on a Mac
# that nobody has logged into, which is exactly the "macOS server" case.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# shellcheck source=/dev/null
. "$PROJECT_ROOT/ecosystem-stack/lib/compat.sh"

LABEL="us.leco-project.stack"
SYSTEMD_UNIT="leco-devops.service"
MODE="install"
FORCE_SYSTEM=0

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
info() { printf "ℹ️  %s\n" "$*"; }
ok()   { printf "✅ %s\n" "$*"; }
warn() { printf "⚠️  %s\n" "$*"; }
err()  { printf "❌ %s\n" "$*" >&2; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --status) MODE="status" ;;
    --remove) MODE="remove" ;;
    --system) FORCE_SYSTEM=1 ;;
    -h|--help) sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) err "Unknown option: $1"; exit 2 ;;
  esac
  shift
done

# --------------------------------------------------------------------------- systemd

SYSTEMD_PATH="/etc/systemd/system/$SYSTEMD_UNIT"

linux_install() {
  if ! compat_has_cmd systemctl; then
    err "systemd is not available — no autostart mechanism to install."
    info "Start the stack from your init system of choice: $PROJECT_ROOT/start.sh"
    return 1
  fi

  local user
  user="$(id -un)"

  # Type=oneshot with RemainAfterExit: start.sh returns once every container is up, and
  # the unit should then be considered active rather than restarted forever.
  compat_sudo tee "$SYSTEMD_PATH" >/dev/null <<UNIT
[Unit]
Description=LEco DevOps local platform
Documentation=https://leco-project.us
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
User=$user
WorkingDirectory=$PROJECT_ROOT
ExecStart=$PROJECT_ROOT/start.sh
ExecStop=$PROJECT_ROOT/start.sh --stop
TimeoutStartSec=900
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

  ok "Wrote $SYSTEMD_PATH"
  compat_sudo systemctl daemon-reload
  compat_sudo systemctl enable "$SYSTEMD_UNIT" >/dev/null
  ok "Enabled $SYSTEMD_UNIT (starts at boot)"

  # Docker itself must also come back at boot — the installer never did this, so a
  # rebooted Linux host could have the unit enabled and no daemon for it to talk to.
  if ! systemctl is-enabled docker >/dev/null 2>&1; then
    warn "docker.service is not enabled at boot — enabling it, or this unit has nothing to talk to."
    compat_sudo systemctl enable docker >/dev/null 2>&1 || warn "Could not enable docker.service."
  fi

  info "Start now with:  sudo systemctl start $SYSTEMD_UNIT"
}

linux_remove() {
  compat_has_cmd systemctl || return 0
  compat_sudo systemctl disable --now "$SYSTEMD_UNIT" >/dev/null 2>&1 || true
  compat_sudo rm -f "$SYSTEMD_PATH"
  compat_sudo systemctl daemon-reload
  ok "Removed $SYSTEMD_UNIT"
}

linux_status() {
  if [ -f "$SYSTEMD_PATH" ]; then
    ok "$SYSTEMD_UNIT installed"
    systemctl is-enabled "$SYSTEMD_UNIT" 2>/dev/null | sed 's/^/    enabled: /'
    systemctl is-active "$SYSTEMD_UNIT" 2>/dev/null | sed 's/^/    active:  /'
  else
    info "No autostart unit installed."
  fi
}

# ---------------------------------------------------------------------------- launchd

agent_plist()  { printf '%s/Library/LaunchAgents/%s.plist' "$HOME" "$LABEL"; }
daemon_plist() { printf '/Library/LaunchDaemons/%s.plist' "$LABEL"; }

macos_write_plist() {
  local path="$1" system="$2" tmp
  tmp="$(mktemp)"
  cat > "$tmp" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PROJECT_ROOT/start.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$PROJECT_ROOT</string>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/tmp/$LABEL.out.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/$LABEL.err.log</string>
PLIST
  if [ "$system" = "1" ]; then
    # A LaunchDaemon runs as root unless told otherwise; run as the invoking user so
    # Docker Desktop's per-user socket and this repo's file ownership still line up.
    cat >> "$tmp" <<PLIST
  <key>UserName</key>
  <string>$(id -un)</string>
PLIST
  fi
  cat >> "$tmp" <<'PLIST'
</dict>
</plist>
PLIST
  if [ "$system" = "1" ]; then
    compat_sudo cp "$tmp" "$path"
    compat_sudo chown root:wheel "$path"
    compat_sudo chmod 644 "$path"
  else
    mkdir -p "$(dirname "$path")"
    cp "$tmp" "$path"
    chmod 644 "$path"
  fi
  rm -f "$tmp"
}

macos_install() {
  local system=0
  if [ "$FORCE_SYSTEM" = "1" ]; then
    system=1
  fi

  if [ "$system" = "1" ]; then
    local path; path="$(daemon_plist)"
    macos_write_plist "$path" 1
    compat_sudo launchctl bootout system "$path" >/dev/null 2>&1 || true
    compat_sudo launchctl bootstrap system "$path"
    ok "Installed LaunchDaemon: $path"
    info "Runs at boot without anyone logging in."
    warn "Docker Desktop does not start without a login session. On a headless Mac, install"
    warn "Docker's daemon differently (colima, or Docker Desktop with auto-login enabled)."
  else
    local path; path="$(agent_plist)"
    macos_write_plist "$path" 0
    launchctl bootout "gui/$(id -u)" "$path" >/dev/null 2>&1 || true
    launchctl bootstrap "gui/$(id -u)" "$path"
    ok "Installed LaunchAgent: $path"
    info "Starts when you log in."
    info "For a headless/server Mac, re-run with --system."
  fi
}

macos_remove() {
  local a d
  a="$(agent_plist)"; d="$(daemon_plist)"
  if [ -f "$a" ]; then
    launchctl bootout "gui/$(id -u)" "$a" >/dev/null 2>&1 || true
    rm -f "$a"; ok "Removed LaunchAgent"
  fi
  if [ -f "$d" ]; then
    compat_sudo launchctl bootout system "$d" >/dev/null 2>&1 || true
    compat_sudo rm -f "$d"; ok "Removed LaunchDaemon"
  fi
}

macos_status() {
  local a d found=0
  a="$(agent_plist)"; d="$(daemon_plist)"
  [ -f "$a" ] && { ok "LaunchAgent installed ($a)"; found=1; }
  [ -f "$d" ] && { ok "LaunchDaemon installed ($d)"; found=1; }
  [ "$found" = "0" ] && info "No autostart installed."
  launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 && info "  agent is loaded"
  return 0
}

# ------------------------------------------------------------------------------ main

bold "LEco DevOps — autostart"
info "Platform: $(compat_os)$(compat_is_wsl && printf ' (WSL2)' || true)"

if compat_is_wsl; then
  echo
  warn "Inside WSL2 there is usually no systemd and no boot event to hook."
  info "Enable systemd in /etc/wsl.conf ([boot] systemd=true) for this to work, or start"
  info "the stack from your shell profile / a Windows scheduled task running wsl.exe."
  echo
fi

case "$MODE" in
  status)
    if compat_is_darwin; then macos_status; else linux_status; fi
    ;;
  remove)
    if compat_is_darwin; then macos_remove; else linux_remove; fi
    ;;
  install)
    if compat_is_darwin; then macos_install; else linux_install; fi
    echo
    ok "Autostart configured."
    info "Verify with: $0 --status"
    ;;
esac
