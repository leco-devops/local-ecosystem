#!/usr/bin/env bash
# Safe to `source` from ecosystem-stack/core.sh; also runnable as ./ecosystem-stack/services/traefik.sh …
if [ -z "${PROJECT_ROOT:-}" ]; then
  PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
fi

# Bind mounts in `docker run` are resolved on the Docker *host* (e.g. macOS). Inside the Ops
# dashboard container PROJECT_ROOT is /project, which does not exist on the host — set
# DASHBOARD_DOCKER_BIND_ROOT to the real repo path (dashboard.sh does this automatically).
DOCKER_BIND="${DASHBOARD_DOCKER_BIND_ROOT:-$PROJECT_ROOT}"
HOSTING_TRAEFIK_DIR="$DOCKER_BIND/hosting/traefik"
HOSTING_DYNAMIC="$HOSTING_TRAEFIK_DIR/dynamic.yml"
CORE_DYNAMIC="$DOCKER_BIND/traefik/dynamic.yml"
# Real file copy (not a symlink): Traefik's fsnotify watches each entry under
# hosting/traefik/. Docker Desktop often returns ENOENT for watchers on symlinks that
# point outside the mounted dir → file provider fails to start → zero HTTP routers → 404.
CORE_DYNAMIC_COPY="$HOSTING_TRAEFIK_DIR/01-stack-core.yml"
NORMALIZE_SCRIPT="$DOCKER_BIND/ecosystem-stack/scripts/normalize-hosting-traefik-dynamic.py"

NAME="traefik"

# Without PyYAML: fix the common invalid stub Traefik v3 rejects.
_normalize_hosting_dynamic_bash() {
  local f="$1"
  [ -f "$f" ] || return 0
  local compact
  compact=$(sed 's/#.*$//' "$f" 2>/dev/null | tr -d '[:space:]' | sed 's/^---//')
  case "$compact" in
  http:\{\})
    printf '%s\n' '{}' >"$f"
    echo "ℹ️  Normalized $f (empty http → {}); install PyYAML for full YAML repair."
    ;;
  esac
}

_normalize_hosting_dynamic_yaml() {
  local f="$1"
  [ -f "$f" ] || return 0
  if [ -f "$NORMALIZE_SCRIPT" ]; then
    local rc=0
    python3 "$NORMALIZE_SCRIPT" "$f" || rc=$?
    if [ "$rc" -eq 0 ] || [ "$rc" -eq 1 ]; then
      return "$rc"
    fi
    if [ "$rc" -eq 3 ]; then
      _normalize_hosting_dynamic_bash "$f"
      return 0
    fi
    echo "⚠️  normalize-hosting-traefik-dynamic.py exited $rc — trying bash fallback"
    _normalize_hosting_dynamic_bash "$f"
    return 0
  fi
  _normalize_hosting_dynamic_bash "$f"
  return 0
}

# Ensure hosting/traefik files exist and are valid before Traefik starts (or for dashboard deploy).
ensure_hosting_files() {
  mkdir -p "$HOSTING_TRAEFIK_DIR"
  if [ ! -f "$CORE_DYNAMIC" ]; then
    echo "❌ Missing required persistent Traefik base file: $CORE_DYNAMIC"
    echo "   Restore it from git before starting Traefik."
    return 1
  fi
  rm -f "$HOSTING_TRAEFIK_DIR/00-core.yml" 2>/dev/null
  # Always refresh stack routes from traefik/dynamic.yml (git) so new hosts like paperclip.lh appear after heal/start.
  if [ -f "$DOCKER_BIND/config/leco-platform.yaml" ] && [ -f "$DOCKER_BIND/scripts/render-platform-traefik.py" ]; then
    if ! python3 "$DOCKER_BIND/scripts/render-platform-traefik.py" --write 2>/dev/null; then
      cp "$CORE_DYNAMIC" "$CORE_DYNAMIC_COPY" || {
        echo "❌ Could not copy stack core to $CORE_DYNAMIC_COPY"
        return 1
      }
    fi
  else
    cp "$CORE_DYNAMIC" "$CORE_DYNAMIC_COPY" || {
      echo "❌ Could not copy stack core to $CORE_DYNAMIC_COPY"
      return 1
    }
  fi
  # Do not write http: {} — Traefik v3 rejects an empty http block. Use a no-op root mapping.
  if [ ! -f "$HOSTING_DYNAMIC" ]; then
    printf '%s\n' '{}' >"$HOSTING_DYNAMIC"
    echo "ℹ️ Created hosting/traefik/dynamic.yml (empty merge stub; leco-devops adds routes here)"
  fi
  for f in "$HOSTING_TRAEFIK_DIR"/*.yml; do
    [ -f "$f" ] || continue
    _normalize_hosting_dynamic_yaml "$f" || return 1
  done
}

# Fix files on disk; restart Traefik if a container exists (picks up copy vs symlink / repaired YAML).
heal() {
  ensure_hosting_files || return 1
  if docker ps -a --filter "name=^/${NAME}$" --format '{{.Names}}' | grep -qx "$NAME"; then
    echo "🔄 Restarting Traefik after hosting/traefik file repair…"
    restart
  fi
}

_traefik_tls_mode() {
  if [ ! -f "$DOCKER_BIND/config/leco-platform.yaml" ]; then
    echo "mkcert"
    return 0
  fi
  # The lib path travels in the environment rather than being interpolated into the
  # heredoc. A quoted heredoc ('PY') does not expand $DOCKER_BIND, so interpolating it
  # here silently produced a literal "$DOCKER_BIND/..." on sys.path — the import then
  # always failed and this function always answered "mkcert", regardless of configured
  # tls.mode. That silently disabled ACME and kept the insecure Traefik API published.
  # Passing it through the environment keeps the heredoc quoted (no shell expansion of
  # the Python source) while still delivering the real path, spaces and all.
  local _mode _err _status
  _err="$(mktemp -t leco-tls-mode)"
  _mode="$(LECO_LIB_DIR="$DOCKER_BIND/ecosystem-stack/lib" python3 - <<'PY' 2>"$_err"
import os
import sys
sys.path.insert(0, os.environ["LECO_LIB_DIR"])
from platform_config import load_platform_config
cfg = load_platform_config() or {}
tls = cfg.get("tls") if isinstance(cfg.get("tls"), dict) else {}
print(str(tls.get("mode") or "mkcert").strip().lower() or "mkcert")
PY
)"
  _status=$?

  if [ "$_status" -eq 0 ] && [ -n "$_mode" ]; then
    rm -f "$_err"
    printf '%s\n' "$_mode"
    return 0
  fi

  # A config file exists but could not be read. Falling back to "mkcert" silently is how
  # a cloud deployment ends up serving traefik-static.yaml — which publishes the Traefik
  # API unauthenticated on 0.0.0.0:8080 — while the operator believes tls.mode: acme is
  # in force. Say so loudly; the fallback still happens so the stack can start.
  echo "⚠️  Could not read tls.mode from config/leco-platform.yaml — assuming 'mkcert'." >&2
  if grep -q "No module named 'yaml'" "$_err" 2>/dev/null; then
    echo "    Cause: PyYAML is not installed for $(command -v python3)." >&2
    echo "    Fix:   python3 -m pip install --user pyyaml   (or: pip install -r ecosystem-stack/requirements.txt)" >&2
  elif [ -s "$_err" ]; then
    sed 's/^/    /' "$_err" >&2
  fi
  echo "    If this host is meant to use ACME or a real certificate, do NOT expose it until this is resolved:" >&2
  echo "    the mkcert fallback loads traefik-static.yaml, which serves the Traefik API insecurely on :8080." >&2
  rm -f "$_err"
  echo "mkcert"
}

_traefik_static_config() {
  local mode
  mode="$(_traefik_tls_mode)"
  case "$mode" in
    acme) echo "traefik-static-acme.yaml" ;;
    static|cloudflare) echo "traefik-static.yaml" ;;
    *) echo "traefik-static.yaml" ;;
  esac
}

_prepare_acme_static() {
  local src="$DOCKER_BIND/traefik/traefik-static-acme.yaml"
  local dst="$HOSTING_TRAEFIK_DIR/traefik-static-runtime.yaml"
  mkdir -p "$HOSTING_TRAEFIK_DIR" "$DOCKER_BIND/certs/acme"
  if [ ! -f "$src" ]; then
    echo "❌ Missing $src"
    return 1
  fi
  LECO_LIB_DIR="$DOCKER_BIND/ecosystem-stack/lib" \
  LECO_ACME_SRC="$src" \
  LECO_ACME_DST="$dst" \
  python3 - <<'PY' || cp "$src" "$dst"
import os
import sys
from pathlib import Path
sys.path.insert(0, os.environ["LECO_LIB_DIR"])
from platform_config import load_platform_config
src = Path(os.environ["LECO_ACME_SRC"])
dst = Path(os.environ["LECO_ACME_DST"])
text = src.read_text(encoding="utf-8")
cfg = load_platform_config() or {}
tls = cfg.get("tls") if isinstance(cfg.get("tls"), dict) else {}
email = str(tls.get("acme_email") or "ops@example.com").strip() or "ops@example.com"
text = text.replace('email: "ops@example.com"', f'email: "{email}"')
dst.write_text(text, encoding="utf-8")
PY
}

start() {
  docker network inspect lh-network >/dev/null 2>&1 || docker network create lh-network >/dev/null
  docker rm -f "$NAME" 2>/dev/null

  ensure_hosting_files || return 1

  local tls_mode static_cfg
  tls_mode="$(_traefik_tls_mode)"
  static_cfg="$(_traefik_static_config)"
  local -a extra_vols=()
  local config_arg="--configFile=/etc/traefik/${static_cfg}"

  if [ "$tls_mode" = "acme" ]; then
    _prepare_acme_static || return 1
    extra_vols+=(-v "$HOSTING_TRAEFIK_DIR/traefik-static-runtime.yaml:/etc/traefik/traefik-static-acme.yaml:ro")
    extra_vols+=(-v "$DOCKER_BIND/certs/acme:/acme")
    config_arg="--configFile=/etc/traefik/traefik-static-acme.yaml"
  fi

  docker run -d \
    --name "$NAME" \
    --restart unless-stopped \
    --network lh-network \
    -p 80:80 \
    -p 443:443 \
    -p 8080:8080 \
    -v "$DOCKER_BIND/traefik:/etc/traefik" \
    -v "$HOSTING_TRAEFIK_DIR:/etc/traefik-dynamic" \
    -v "$DOCKER_BIND/certs:/certs" \
    "${extra_vols[@]}" \
    traefik:v3.3 \
    $config_arg
}

stop() { docker stop "$NAME"; }
restart() { stop; start; }
remove() { docker rm -f "$NAME"; }
pause() { docker pause "$NAME"; }
unpause() { docker unpause "$NAME"; }
status() { docker ps -a --filter "name=^/$NAME$" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"; }
logs() { docker logs -f "$NAME"; }
reset() { remove; }

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  act="${1:-}"
  case "$act" in
    start) start ;;
    stop) stop ;;
    restart) restart ;;
    heal) heal ;;
    ensure-hosting-files) ensure_hosting_files ;;
    remove) remove ;;
    pause) pause ;;
    unpause) unpause ;;
    status) status ;;
    logs) logs ;;
    reset) reset ;;
    *)
      echo "Usage: $0 {start|stop|restart|heal|ensure-hosting-files|remove|pause|unpause|status|logs|reset}"
      exit 1
      ;;
  esac
fi
