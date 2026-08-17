#!/usr/bin/env bash
# Generate the local TLS certificate for every *.lh hostname the stack serves.
#
# Why this exists instead of `mkcert "*.lh"`:
#
#   A wildcard certificate for `*.lh` is rejected by every TLS client. RFC 6125 and the
#   CA/Browser Forum rules refuse a wildcard in the position directly below a top-level
#   domain, because `*.lh` would assert ownership of an entire TLD. curl, Chrome, Safari,
#   Firefox, and Python all apply this: `*.lh` matches NOTHING — not even `dashboard.lh`.
#   The chain verifies (the mkcert CA is trusted) but the hostname check fails, which is why
#   the browser still shows "not secure" after `mkcert -install` appeared to work.
#
#   The fix is explicit SANs: one entry per hostname. Wildcards are still used where they are
#   legal — `*.myapp.lh` has three labels and is accepted — so an app that publishes
#   `panel.myapp.lh` and `ops.myapp.lh` is covered by a single wildcard entry.
#
# Re-run this after adding a hosted app with a new hostname, then restart Traefik:
#   ./certs/generate-certs.sh && ./ecosystem-stack/ecosystem-stack.sh restart traefik
#
# This script is for LOCAL `.lh` development only. mkcert issues certificates from a CA that
# exists only in the trust store of the machine that ran `mkcert -install`; on a public server
# every visitor would see an untrusted certificate. When config/leco-platform.yaml selects
# tls.mode `acme` or `cloudflare` the script explains who issues certificates there and exits
# without writing anything (see --force to override deliberately).
#
# Usage:
#   ./certs/generate-certs.sh                 # discover hostnames + generate
#   ./certs/generate-certs.sh --list          # show what would be included, generate nothing
#   ./certs/generate-certs.sh --force         # generate even when tls.mode is not mkcert
#   ./certs/generate-certs.sh extra.lh …      # add hostnames on top of the discovered set

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_DIR="$PROJECT_ROOT/certs"
# Filenames are unchanged from the old wildcard cert so Traefik's tls.certificates block and
# the container mounts keep working without edits.
CERT_FILE="$CERT_DIR/wildcard.lh.pem"
KEY_FILE="$CERT_DIR/wildcard.lh-key.pem"

# Always present, even before Traefik config exists (fresh clone, first install).
BASE_HOSTS=(
  localhost.lh
  dashboard.lh
  traefik.lh
)

LIST_ONLY=0
FORCE=0
EXTRA_HOSTS=()
for arg in "$@"; do
  case "$arg" in
    --list) LIST_ONLY=1 ;;
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,31p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) EXTRA_HOSTS+=("$arg") ;;
  esac
done

# ------------------------------------------------- platform TLS mode gate
#
# Emits "<tls.mode>|<deployment_mode>|<base_domain>" from config/leco-platform.yaml, or the
# local defaults when there is no platform config yet (fresh clone / workstation install).
read_platform_tls() {
  local pf="$PROJECT_ROOT/config/leco-platform.yaml"
  if [ ! -f "$pf" ]; then
    echo "mkcert|local|lh"
    return 0
  fi
  # The heredoc is quoted so the shell cannot expand anything inside it; the path is passed
  # through the environment instead. (An unquoted heredoc here would silently produce a
  # useless answer if the path contained a $ or a backtick.)
  local out=""
  if command -v python3 >/dev/null 2>&1; then
    out="$(LECO_PLATFORM_FILE="$pf" python3 - <<'PY' 2>/dev/null || true
import os
try:
    import yaml
except ImportError:
    raise SystemExit(1)
try:
    with open(os.environ["LECO_PLATFORM_FILE"], encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
except Exception:
    raise SystemExit(1)
if not isinstance(cfg, dict):
    raise SystemExit(1)
tls = cfg.get("tls") if isinstance(cfg.get("tls"), dict) else {}
mode = str(tls.get("mode") or "mkcert").strip() or "mkcert"
dm = str(cfg.get("deployment_mode") or "local").strip() or "local"
dom = str(cfg.get("base_domain") or "lh").strip() or "lh"
print(f"{mode}|{dm}|{dom}")
PY
)"
  fi
  if [ -z "$out" ]; then
    # No python3 or no PyYAML: read the three flat keys we need directly.
    out="$(awk '
      /^tls:[[:space:]]*$/ { in_tls = 1; next }
      /^[^[:space:]#]/     { in_tls = 0 }
      in_tls && /^[[:space:]]+mode:/ { sub(/^[^:]*:[[:space:]]*/, ""); gsub(/["\x27]/, ""); mode = $0 }
      /^deployment_mode:/  { sub(/^[^:]*:[[:space:]]*/, ""); gsub(/["\x27]/, ""); dm = $0 }
      /^base_domain:/      { sub(/^[^:]*:[[:space:]]*/, ""); gsub(/["\x27]/, ""); dom = $0 }
      END {
        printf "%s|%s|%s\n", (mode ? mode : "mkcert"), (dm ? dm : "local"), (dom ? dom : "lh")
      }
    ' "$pf")"
  fi
  echo "${out:-mkcert|local|lh}"
}

PLATFORM_TLS="$(read_platform_tls)"
TLS_MODE="${PLATFORM_TLS%%|*}"
PLATFORM_REST="${PLATFORM_TLS#*|}"
DEPLOY_MODE="${PLATFORM_REST%%|*}"
BASE_DOMAIN="${PLATFORM_REST#*|}"

case "$TLS_MODE" in
  acme)
    if [ "$FORCE" != "1" ]; then
      cat >&2 <<EOF
ℹ️  config/leco-platform.yaml has tls.mode: acme (deployment_mode: $DEPLOY_MODE, base_domain: $BASE_DOMAIN).

    Certificates for '$BASE_DOMAIN' are obtained by Traefik from Let's Encrypt — this script
    would only produce a mkcert bundle signed by a CA that nothing on the internet trusts.
    Nothing was generated.

    For ACME to succeed:
      • every hostname must resolve publicly to this VM (A/AAAA records)
      • inbound TCP 80 must reach Traefik — HTTP-01 is answered on the 'web' entrypoint
      • the ACME store must survive restarts (bind-mount certs/acme to /acme in the container)
      • routers must name the resolver: tls.certResolver: lecoacme
      • set a real contact address in tls.acme_email

    Check issuance with:  docker logs leco-traefik 2>&1 | grep -i acme
    Override deliberately (local testing only):  ./certs/generate-certs.sh --force
EOF
      exit 0
    fi
    echo "⚠️  tls.mode is acme — continuing because --force was given." >&2
    ;;
  cloudflare)
    if [ "$FORCE" != "1" ]; then
      cat >&2 <<EOF
ℹ️  config/leco-platform.yaml has tls.mode: cloudflare (deployment_mode: $DEPLOY_MODE, base_domain: $BASE_DOMAIN).

    Cloudflare terminates TLS at its edge; the origin uses a Cloudflare Origin Certificate or
    a Tunnel, neither of which mkcert can produce. See docs/CLOUDFLARE_SSL_INSTALL.md.
    Nothing was generated.

    Override deliberately (local testing only):  ./certs/generate-certs.sh --force
EOF
      exit 0
    fi
    echo "⚠️  tls.mode is cloudflare — continuing because --force was given." >&2
    ;;
  static)
    if [ "$FORCE" != "1" ]; then
      cat >&2 <<EOF
ℹ️  config/leco-platform.yaml has tls.mode: static (deployment_mode: $DEPLOY_MODE, base_domain: $BASE_DOMAIN).

    Operator-supplied PEM files are the source of truth in this mode. Point Traefik at them
    instead of running mkcert. Nothing was generated.

    Override deliberately (local testing only):  ./certs/generate-certs.sh --force
EOF
      exit 0
    fi
    echo "⚠️  tls.mode is static — continuing because --force was given." >&2
    ;;
esac

# mkcert mode on a non-.lh domain is still the wrong tool — say so rather than emit a bundle
# whose SANs cannot match the hostnames Traefik actually serves.
if [ "$TLS_MODE" = "mkcert" ] && [ "$BASE_DOMAIN" != "lh" ] && [ "$FORCE" != "1" ]; then
  cat >&2 <<EOF
ℹ️  base_domain is '$BASE_DOMAIN', not 'lh', but tls.mode is still 'mkcert'.

    This script only discovers and covers *.lh hostnames, so the certificate it produces
    could not match anything Traefik serves on '$BASE_DOMAIN' — and a mkcert CA is trusted
    only on the machine that installed it. Set tls.mode to acme, cloudflare, or static.
    Nothing was generated.

    Override deliberately:  ./certs/generate-certs.sh --force
EOF
  exit 0
fi

if ! command -v mkcert >/dev/null 2>&1; then
  echo "❌ mkcert is not installed. macOS: brew install mkcert" >&2
  exit 1
fi

# ---------------------------------------------------------------- discovery

# Directories that never declare a hostname but can be enormous: seeded databases, dependency
# trees, build output. `hosting/app-available/<app>/data/` is the one that bites — it holds an
# application's seed data, 3.1 GB of BSON in one real case here.
#
# Two reasons this pruning is not merely an optimisation:
#
#   * **Speed.** `grep -r` over that tree took ~95 s, and BSD grep (what this script gets on
#     macOS) follows symlinks, so a `source` link into an app checkout drags its `node_modules`
#     in too. Pruning takes it to ~0.03 s.
#   * **Correctness.** Every match becomes a SAN on the certificate this machine trusts. Grepping
#     binary database dumps means any byte sequence that happens to look like `http://x.lh` —
#     inside a captured HTTP log, say — silently lands in the certificate. Hostnames should come
#     from configuration, never from an application's data.
PRUNE_DIRS=(node_modules data .git dist build vendor __pycache__ .venv venv coverage tmp)

find_config_files() {
  # -print0/-0 so paths with spaces survive; the repo sits under "Working/GitHub" here but a
  # user's checkout may not be so lucky.
  local dir="$1" expr=()
  local d
  for d in "${PRUNE_DIRS[@]}"; do
    expr+=(-name "$d" -o)
  done
  unset 'expr[${#expr[@]}-1]'
  find "$dir" \( "${expr[@]}" \) -prune -o -type f -print0 2>/dev/null
}

discover_hosts() {
  # Traefik router rules: Host(`something.lh`)
  grep -rhoE 'Host\(`[^`]+`\)' \
    "$PROJECT_ROOT/traefik/dynamic.yml" \
    "$PROJECT_ROOT/hosting/traefik/" \
    2>/dev/null | sed -E 's/Host\(`([^`]+)`\)/\1/' || true

  # Registered and materialized apps may declare URLs Traefik has not merged yet.
  grep -hoE 'https?://[a-zA-Z0-9][a-zA-Z0-9.-]*\.lh' \
    "$PROJECT_ROOT/config/leco-registry.yaml" \
    2>/dev/null | sed -E 's|https?://||' || true

  if [ -d "$PROJECT_ROOT/hosting/app-available" ]; then
    find_config_files "$PROJECT_ROOT/hosting/app-available" \
      | xargs -0 grep -hoE 'https?://[a-zA-Z0-9][a-zA-Z0-9.-]*\.lh' 2>/dev/null \
      | sed -E 's|https?://||' || true
  fi
}

# A hostname with three or more labels (panel.myapp.lh) also contributes a legal wildcard
# (*.myapp.lh) so sibling subdomains of the same app do not need a regeneration.
wildcard_parent() {
  local host="$1" label_count
  label_count="$(awk -F. '{print NF}' <<<"$host")"
  if [ "$label_count" -ge 3 ]; then
    echo "*.${host#*.}"
  fi
}

HOSTS=()
while IFS= read -r host; do
  [ -n "$host" ] && HOSTS+=("$host")
done < <(
  {
    printf '%s\n' "${BASE_HOSTS[@]}"
    [ ${#EXTRA_HOSTS[@]} -gt 0 ] && printf '%s\n' "${EXTRA_HOSTS[@]}"
    discover_hosts
  } | grep -E '^[a-zA-Z0-9*][a-zA-Z0-9.*-]*\.lh$' | sort -u
)

# Add the legal wildcards for any multi-label hosts found.
WILDCARDS=()
for host in "${HOSTS[@]}"; do
  w="$(wildcard_parent "$host")"
  [ -n "$w" ] && WILDCARDS+=("$w")
done
if [ ${#WILDCARDS[@]} -gt 0 ]; then
  while IFS= read -r w; do
    HOSTS+=("$w")
  done < <(printf '%s\n' "${WILDCARDS[@]}" | sort -u)
fi

# Final de-duplicated list.
mapfile -t HOSTS < <(printf '%s\n' "${HOSTS[@]}" | sort -u) 2>/dev/null || {
  # macOS bash 3.2 has no mapfile.
  OLD=("${HOSTS[@]}"); HOSTS=()
  while IFS= read -r h; do HOSTS+=("$h"); done < <(printf '%s\n' "${OLD[@]}" | sort -u)
}

if [ ${#HOSTS[@]} -eq 0 ]; then
  echo "❌ No *.lh hostnames discovered. Is this the repository root?" >&2
  exit 1
fi

echo "🔎 ${#HOSTS[@]} hostnames for the certificate:"
printf '   %s\n' "${HOSTS[@]}"

if [ "$LIST_ONLY" = "1" ]; then
  exit 0
fi

# ---------------------------------------------------------------- generate

if [ ! -f "$(mkcert -CAROOT 2>/dev/null)/rootCA.pem" ]; then
  echo "⚠️  No local CA found. Installing one (you may be asked for your password)…"
  # JAVA_HOME pointing at a missing JDK makes mkcert abort on the Java trust store.
  ( unset JAVA_HOME; mkcert -install )
fi

mkdir -p "$CERT_DIR"
if [ -f "$CERT_FILE" ]; then
  BACKUP="$CERT_FILE.bak-$(date +%Y%m%d-%H%M%S)"
  cp "$CERT_FILE" "$BACKUP"
  [ -f "$KEY_FILE" ] && cp "$KEY_FILE" "${BACKUP%.pem}-key.pem"
  echo "🗄  Previous certificate backed up to $(basename "$BACKUP")"
fi

echo "🔏 Generating certificate…"
# TRUST_STORES=system skips the Java/NSS stores, which are the usual source of mkcert
# failures on machines with a stale JAVA_HOME. The CA itself is already trusted.
( cd "$CERT_DIR" && unset JAVA_HOME && TRUST_STORES="${TRUST_STORES:-system}" \
    mkcert -cert-file "$CERT_FILE" -key-file "$KEY_FILE" "${HOSTS[@]}" )

echo
echo "✅ Certificate written:"
echo "   $CERT_FILE"
echo "   $KEY_FILE"

# ---------------------------------------------------------------- verify

echo
echo "🔍 Verifying hostname coverage:"
FAILED=0
for host in "${HOSTS[@]}"; do
  # Wildcard entries cannot be checked directly; check a sample child instead.
  probe="$host"
  case "$host" in \*.*) probe="probe.${host#*.}" ;; esac
  if openssl x509 -in "$CERT_FILE" -noout -checkhost "$probe" >/dev/null 2>&1; then
    printf '   ✅ %s\n' "$probe"
  else
    printf '   ❌ %s\n' "$probe"
    FAILED=1
  fi
done

if [ "$FAILED" = "1" ]; then
  echo
  echo "❌ Some hostnames are not covered — do not restart Traefik until this is resolved." >&2
  exit 1
fi

echo
echo "Next: restart Traefik so it loads the new certificate:"
echo "   ./ecosystem-stack/ecosystem-stack.sh restart traefik"
