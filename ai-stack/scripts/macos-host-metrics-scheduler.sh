#!/usr/bin/env bash
# Install or remove a LaunchAgent that runs macos-write-cpu-temp.sh on an interval while the
# dashboard stack is in use. Called from ai-stack/services/dashboard.sh on macOS (start → install,
# stop/remove → uninstall).
#
# macOS user crontab is limited to 1-minute granularity; launchd supports StartInterval (e.g. 30s).
#
# Usage:
#   macos-host-metrics-scheduler.sh install
#   macos-host-metrics-scheduler.sh uninstall
#   macos-host-metrics-scheduler.sh status
#
# Optional env:
#   LOCAL_ECO_HOST_METRICS_INTERVAL_SEC  (default 30)
#
# For unattended powermetrics (Apple Silicon proxy path), passwordless sudo may be required:
#   youruser ALL=(root) NOPASSWD: /usr/bin/powermetrics

set -euo pipefail

LABEL="com.local-ecosystem.host-cpu-temp"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WRITE_SCRIPT="${SCRIPT_DIR}/macos-write-cpu-temp.sh"
AGENT_DIR="${HOME}/Library/LaunchAgents"
PLIST_PATH="${AGENT_DIR}/${LABEL}.plist"
DOMAIN="gui/$(id -u)"
INTERVAL_SEC="${LOCAL_ECO_HOST_METRICS_INTERVAL_SEC:-30}"
LOG_DIR="${HOME}/Library/Logs"
OUT_LOG="${LOG_DIR}/local-ecosystem-host-cpu-temp.out.log"
ERR_LOG="${LOG_DIR}/local-ecosystem-host-cpu-temp.err.log"
# Same directory the dashboard mounts for cpu_temp_c.txt — ops UI reads scheduler_meta.json
HOST_METRICS_STATE_DIR="${HOME}/.local-eco-host-metrics"
SCHEDULER_META_JSON="${HOST_METRICS_STATE_DIR}/scheduler_meta.json"

usage() {
  echo "Usage: $0 {install|uninstall|status}" >&2
  exit 1
}

ensure_darwin() {
  if [ "$(uname -s)" != "Darwin" ]; then
    echo "ℹ️  Host metrics scheduler is macOS-only; skipping." >&2
    return 1
  fi
  return 0
}

do_unload() {
  launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null || true
  # Older pattern (still seen on some setups)
  launchctl unload -w "$PLIST_PATH" 2>/dev/null || true
}

cmd_install() {
  ensure_darwin || return 0
  if [ ! -x "$WRITE_SCRIPT" ] && [ ! -f "$WRITE_SCRIPT" ]; then
    echo "❌ Missing: $WRITE_SCRIPT" >&2
    return 1
  fi

  mkdir -p "$AGENT_DIR" "$LOG_DIR"

  cat >"$PLIST_PATH.tmp" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${WRITE_SCRIPT}</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>StartInterval</key>
  <integer>${INTERVAL_SEC}</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${OUT_LOG}</string>
  <key>StandardErrorPath</key>
  <string>${ERR_LOG}</string>
</dict>
</plist>
EOF
  mv "$PLIST_PATH.tmp" "$PLIST_PATH"

  do_unload
  if ! launchctl bootstrap "$DOMAIN" "$PLIST_PATH" 2>/dev/null; then
    if ! launchctl load -w "$PLIST_PATH" 2>/dev/null; then
      echo "❌ launchctl bootstrap/load failed (try: log out/in, or check Console)." >&2
      return 1
    fi
  fi

  mkdir -p "$HOST_METRICS_STATE_DIR"
  export WTS_LABEL="$LABEL" WTS_INT="$INTERVAL_SEC" WTS_SCR="$WRITE_SCRIPT" WTS_PATH="$SCHEDULER_META_JSON"
  python3 - <<'PY' 2>/dev/null || true
import json, os, datetime

path = os.environ["WTS_PATH"]
obj = {
    "label": os.environ["WTS_LABEL"],
    "interval_sec": int(os.environ["WTS_INT"]),
    "installed_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "write_script": os.environ["WTS_SCR"],
    "stdout_log": os.path.expanduser("~/Library/Logs/local-ecosystem-host-cpu-temp.out.log"),
}
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fp:
    json.dump(obj, fp, indent=2)
os.replace(tmp, path)
PY

  echo "✅ Host CPU metrics LaunchAgent installed: ${LABEL} (every ${INTERVAL_SEC}s) → ${WRITE_SCRIPT}"
  echo "   Logs: ${OUT_LOG} / ${ERR_LOG}"
}

cmd_uninstall() {
  ensure_darwin || return 0
  do_unload
  rm -f "$PLIST_PATH"
  rm -f "$SCHEDULER_META_JSON"
  echo "✅ Host CPU metrics LaunchAgent removed (${LABEL})."
}

cmd_status() {
  ensure_darwin || return 0
  if [ -f "$PLIST_PATH" ]; then
    echo "Plist: $PLIST_PATH"
  else
    echo "Plist: (not installed)"
  fi
  if launchctl print "${DOMAIN}/${LABEL}" &>/dev/null; then
    echo "State: loaded (${DOMAIN}/${LABEL})"
  else
    echo "State: not loaded"
  fi
}

main() {
  case "${1:-}" in
    install) cmd_install ;;
    uninstall) cmd_uninstall ;;
    status) cmd_status ;;
    *) usage ;;
  esac
}

main "$@"
