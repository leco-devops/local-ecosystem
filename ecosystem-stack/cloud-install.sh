#!/usr/bin/env bash
# Cloud VM installer — preflight + non-interactive wrapper around install-foundation.sh.
#
# The wrapper exists because the generic installer has local-workstation defaults that are wrong,
# and silently wrong, on a public server. Each check below refuses rather than guesses.
#
#   ./ecosystem-stack/cloud-install.sh --profile minimal --domain leco.mydomain.com --tls acme
#
# Options consumed here (everything else is passed straight through):
#   --domain <fqdn>   required; becomes base_domain in config/leco-platform.yaml
#   --profile <name>  required; see ecosystem-stack/config/install-profiles.yaml
#   --tls <mode>      acme (default) | cloudflare | static
#   --skip-preflight  run the installer without the safety gate (you own the outcome)
set -euo pipefail
STACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$STACK_DIR/.." && pwd)"

DOMAIN=""
PROFILE=""
TLS_MODE=""
SKIP_PREFLIGHT=0
PASSTHRU=()

# Parse a copy of the arguments: --domain/--profile/--tls are still forwarded (the installer
# needs them), we only need to see their values to validate before anything starts.
while [ "$#" -gt 0 ]; do
  case "$1" in
    --domain)
      DOMAIN="${2:-}"; PASSTHRU+=("$1" "${2:-}"); shift 2 ;;
    --domain=*)
      DOMAIN="${1#*=}"; PASSTHRU+=("$1"); shift ;;
    --profile)
      PROFILE="${2:-}"; PASSTHRU+=("$1" "${2:-}"); shift 2 ;;
    --profile=*)
      PROFILE="${1#*=}"; PASSTHRU+=("$1"); shift ;;
    --tls)
      TLS_MODE="${2:-}"; PASSTHRU+=("$1" "${2:-}"); shift 2 ;;
    --tls=*)
      TLS_MODE="${1#*=}"; PASSTHRU+=("$1"); shift ;;
    --skip-preflight)
      SKIP_PREFLIGHT=1; shift ;;
    *)
      PASSTHRU+=("$1"); shift ;;
  esac
done

fail() { printf '❌ %s\n' "$1" >&2; exit 1; }

if [ "$SKIP_PREFLIGHT" != "1" ]; then

  # --- domain -------------------------------------------------------------
  # install-foundation.sh defaults a missing --domain to "dev.example.com" without saying so,
  # which produces an install whose every generated hostname is unroutable.
  [ -n "$DOMAIN" ] || fail "--domain <fqdn> is required (e.g. --domain leco.mydomain.com).
   Without it the installer silently uses dev.example.com and every generated hostname is wrong."

  case "$DOMAIN" in
    lh|*.lh|localhost|*.localhost|*.local)
      fail "--domain '$DOMAIN' is a local-only name. Cloud mode needs a domain with public DNS." ;;
  esac
  if ! printf '%s' "$DOMAIN" | grep -qE '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$'; then
    fail "--domain '$DOMAIN' is not a valid lowercase DNS name with at least two labels."
  fi

  # --- profile ------------------------------------------------------------
  # Without --profile, install-foundation.sh takes its interactive selection path, which writes
  # config/leco-platform.yaml *after* every service has already started. Traefik therefore boots
  # with no base_domain and routes .lh — the domain never takes effect on the first start.
  [ -n "$PROFILE" ] || fail "--profile <name> is required (minimal | platform | ai-cloud | full | …).
   Without it the installer starts services before writing config/leco-platform.yaml, so
   base_domain and tls.mode are not applied to the first Traefik start.
   Profiles: $PROJECT_ROOT/ecosystem-stack/config/install-profiles.yaml"

  if [ -f "$PROJECT_ROOT/ecosystem-stack/config/install-profiles.yaml" ]; then
    if ! grep -qE "^[[:space:]]+${PROFILE}:" "$PROJECT_ROOT/ecosystem-stack/config/install-profiles.yaml"; then
      fail "--profile '$PROFILE' is not defined in ecosystem-stack/config/install-profiles.yaml"
    fi
  fi

  # --- tls ----------------------------------------------------------------
  TLS_MODE="${TLS_MODE:-acme}"
  case "$TLS_MODE" in
    acme|cloudflare|static) ;;
    mkcert) fail "--tls mkcert issues certificates from a CA trusted only on the machine that
   created it. Public visitors would see an untrusted certificate. Use acme, cloudflare, or static." ;;
    *) fail "--tls '$TLS_MODE' is not one of: acme, cloudflare, static." ;;
  esac
  # Forward the resolved default so the installer records tls.mode explicitly.
  case " ${PASSTHRU[*]} " in
    *" --tls "*|*" --tls="*) ;;
    *) PASSTHRU+=(--tls "$TLS_MODE") ;;
  esac

  # --- known-broken ACME selection ---------------------------------------
  # services/traefik.sh reads tls.mode inside a single-quoted heredoc, so "$DOCKER_BIND" is never
  # expanded, the import fails, and the function always answers "mkcert". The consequence is that
  # traefik-static-acme.yaml is never mounted and /acme is never bind-mounted, so no certificate
  # is requested and any ACME account created in-container is destroyed on the next restart.
  if [ "$TLS_MODE" = "acme" ] \
     && grep -q "python3 - <<'PY'" "$PROJECT_ROOT/ecosystem-stack/services/traefik.sh" 2>/dev/null \
     && grep -q 'sys.path.insert(0, "\$DOCKER_BIND/ecosystem-stack/lib")' "$PROJECT_ROOT/ecosystem-stack/services/traefik.sh" 2>/dev/null; then
    cat >&2 <<'EOF'
❌ ACME cannot work with the current ecosystem-stack/services/traefik.sh.

   _traefik_tls_mode() runs its Python inside a quoted heredoc (<<'PY'), so $DOCKER_BIND is
   passed through literally, the import raises, and the function always returns "mkcert".
   Traefik is then started with traefik-static.yaml (no resolver, no /acme mount) no matter
   what tls.mode says, so:
     • no certificate is ever requested from Let's Encrypt
     • the unauthenticated API on :8080 stays enabled and published on 0.0.0.0

   Fix: change `python3 - <<'PY'` to `python3 - <<PY` in _traefik_tls_mode() (the sibling
   function _prepare_acme_static already uses the unquoted form), then re-run this installer.
   Verify afterwards with:
     docker inspect leco-traefik --format '{{json .Args}}'      # expect traefik-static-acme.yaml
     docker inspect leco-traefik --format '{{json .Mounts}}'    # expect a bind onto /acme

   Bypass this check with --skip-preflight if you are fixing it another way.
EOF
    exit 1
  fi

  # --- security posture ---------------------------------------------------
  # This stack mounts /var/run/docker.sock into the dashboard and exposes no authentication.
  # Refuse to build that on a public address by accident.
  if [ "${LECO_CLOUD_ACK:-0}" != "1" ]; then
    cat >&2 <<EOF
❌ Refusing to install on a public domain without an explicit acknowledgement.

   This is a control plane, not an app. As shipped it has NO authentication:

   1. dashboard/control.py check_control_token() returns True when DASHBOARD_CONTROL_TOKEN is
      unset, so every mutating endpoint — including POST /api/control, which runs container
      lifecycle commands against a mounted /var/run/docker.sock — is open to anyone who can
      reach the host. Setting the variable in your shell is NOT enough: services/dashboard.sh
      does not forward it into the container (it appears only in a comment near line 97).
      To enable it you must add  -e "DASHBOARD_CONTROL_TOKEN=\$DASHBOARD_CONTROL_TOKEN"  to the
      docker run in services/dashboard.sh.
   2. There is no login on the dashboard UI at all, and several credential endpoints
      (/api/ui-credentials/*, /api/dev-stacks/<id>/access, /hub/<slug>) return service
      passwords in cleartext with no token check even when the token IS set.
   3. Traefik publishes :8080 on 0.0.0.0 with api.insecure enabled (see traefik-static.yaml).
   4. Postgres :5432, MySQL :3306, MinIO, SFTP :2222 and FTP :21 publish on 0.0.0.0 with
      passwords that are constants committed to this public repository.
   5. Open WebUI, n8n and Paperclip claim admin on first signup — on a public host the first
      visitor becomes the administrator.

   Decide what to do about each of those before, not after, DNS points here. At minimum put the
   whole domain behind an authenticating proxy (Cloudflare Access, forwardAuth, or a VPN), and
   restrict inbound to 80/443 at the cloud provider's firewall — Docker's DNAT rules bypass ufw.

   Then re-run with:
     LECO_CLOUD_ACK=1 $0 --profile $PROFILE --domain $DOMAIN --tls $TLS_MODE
EOF
    exit 1
  fi

  printf '✅ Preflight passed: profile=%s domain=%s tls=%s\n' "$PROFILE" "$DOMAIN" "$TLS_MODE" >&2
fi

exec "$STACK_DIR/install-foundation.sh" \
  --mode cloud \
  --non-interactive \
  "${PASSTHRU[@]}"
