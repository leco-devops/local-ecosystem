#!/usr/bin/env bash
#
# Make `*.lh` resolve to 127.0.0.1 — automatically, per OS.
#
#   ./ecosystem-stack/scripts/dns-setup.sh            configure (idempotent)
#   ./ecosystem-stack/scripts/dns-setup.sh --check     report only, change nothing
#   ./ecosystem-stack/scripts/dns-setup.sh --remove    undo everything this installed
#   ./ecosystem-stack/scripts/dns-setup.sh --hosts     force the static /etc/hosts fallback
#
# Why this exists
# ---------------
# Every service on this platform is addressed by hostname (`dashboard.lh`, `n8n.lh`, and
# one per onboarded app). Until now, making those resolve was a manual step, and the two
# documented approaches disagreed:
#
#   docs/SETUP.md            dnsmasq + /etc/resolver/lh   — a real wildcard
#   docs/help/02-install-dns.md  a fixed list in /etc/hosts — not a wildcard
#
# The difference matters the moment you onboard an app: a wildcard covers `myapp.lh`
# automatically, whereas the hosts-file list needs editing (as root) for every new app,
# and silently 404s until you do. So wildcard is the default here, and the hosts file is
# the explicit fallback for hosts where a resolver cannot be installed.
#
# Mechanisms, by platform:
#   macOS          dnsmasq + /etc/resolver/lh   (the resolver directory is macOS-only)
#   systemd-resolved  a drop-in that routes the `lh` domain to a local dnsmasq
#   NetworkManager    dnsmasq plugin + a `.lh` address rule
#   anything else     /etc/hosts, enumerated from the routes actually configured
#
# WSL2 configures the Linux side only. The Windows browser uses the Windows resolver,
# which cannot see any of this — see windows/Install-LhDns.ps1.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# shellcheck source=/dev/null
. "$PROJECT_ROOT/ecosystem-stack/lib/compat.sh"

TLD="${LECO_LOCAL_TLD:-lh}"
MARK_BEGIN="# >>> LEco DevOps (*.${TLD}) >>>"
MARK_END="# <<< LEco DevOps (*.${TLD}) <<<"
HOSTS_FILE="/etc/hosts"

MODE="apply"

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
info() { printf "ℹ️  %s\n" "$*"; }
ok()   { printf "✅ %s\n" "$*"; }
warn() { printf "⚠️  %s\n" "$*"; }
err()  { printf "❌ %s\n" "$*" >&2; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --check)  MODE="check" ;;
    --remove) MODE="remove" ;;
    --hosts)  MODE="hosts" ;;
    -h|--help) sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) err "Unknown option: $1"; exit 2 ;;
  esac
  shift
done

# --------------------------------------------------------------------------- probing

resolves() {
  # A name that cannot exist unless a wildcard is in play.
  local probe="leco-wildcard-probe.${TLD}"
  compat_dns_lookup "$probe" >/dev/null 2>&1
}

resolves_known() {
  compat_dns_lookup "dashboard.${TLD}" >/dev/null 2>&1
}

report_state() {
  if resolves; then
    ok "Wildcard *.${TLD} resolves (a new app hostname will work immediately)."
    return 0
  fi
  if resolves_known; then
    warn "dashboard.${TLD} resolves but *.${TLD} does not — this is a static hosts list."
    warn "Newly onboarded apps will 404 until their hostname is added."
    return 1
  fi
  err "*.${TLD} does not resolve at all."
  return 2
}

# ------------------------------------------------------------------ hostname discovery

# Hostnames actually configured, for the non-wildcard fallback. Read from the same files
# generate-certs.sh reads, so the hosts list and the certificate SANs cannot drift apart.
discover_hosts() {
  {
    printf 'dashboard.%s\ntraefik.%s\nlocalhost.%s\n' "$TLD" "$TLD" "$TLD"
    grep -rhoE "Host\(\`[a-zA-Z0-9][a-zA-Z0-9.*-]*\.${TLD}\`\)" \
      "$PROJECT_ROOT/traefik/dynamic.yml" \
      "$PROJECT_ROOT/hosting/traefik/" 2>/dev/null \
      | sed -E "s/Host\(\`([^\`]*)\`\)/\1/"
  } | grep -v '^\*' | sort -u
}

# --------------------------------------------------------------------------- macOS

macos_apply() {
  local prefix conf
  if ! compat_has_cmd brew; then
    warn "Homebrew not found — cannot install dnsmasq. Falling back to $HOSTS_FILE."
    hosts_apply
    return $?
  fi
  compat_has_cmd dnsmasq || brew install dnsmasq
  prefix="$(brew --prefix)"
  conf="$prefix/etc/dnsmasq.conf"
  mkdir -p "$(dirname "$conf")"
  touch "$conf"

  if grep -q "^address=/\.${TLD}/127\.0\.0\.1" "$conf" 2>/dev/null; then
    info "dnsmasq already routes *.${TLD}"
  else
    printf '%s\naddress=/.%s/127.0.0.1\n%s\n' "$MARK_BEGIN" "$TLD" "$MARK_END" >> "$conf"
    ok "Added *.${TLD} to $conf"
  fi

  info "Restarting dnsmasq (needs sudo — it binds port 53)…"
  compat_sudo brew services restart dnsmasq >/dev/null 2>&1 || compat_sudo brew services start dnsmasq >/dev/null 2>&1 || {
    err "Could not start dnsmasq."
    return 1
  }

  # /etc/resolver/<tld> is what makes macOS consult 127.0.0.1 for this TLD only. It is a
  # macOS-specific mechanism with no Linux equivalent.
  compat_sudo mkdir -p /etc/resolver
  printf 'nameserver 127.0.0.1\n' | compat_sudo tee "/etc/resolver/${TLD}" >/dev/null
  ok "Wrote /etc/resolver/${TLD}"

  compat_sudo dscacheutil -flushcache 2>/dev/null || true
  compat_sudo killall -HUP mDNSResponder 2>/dev/null || true
}

macos_remove() {
  local prefix conf
  if compat_has_cmd brew; then
    prefix="$(brew --prefix)"
    conf="$prefix/etc/dnsmasq.conf"
    if [ -f "$conf" ]; then
      # Remove both our marked block and a bare address line added by the old docs.
      compat_sudo sed -i.leco-bak \
        -e "/^${MARK_BEGIN}$/,/^${MARK_END}$/d" \
        -e "/^address=\/\.${TLD}\/127\.0\.0\.1$/d" \
        "$conf" 2>/dev/null || true
      ok "Cleaned $conf"
    fi
  fi
  [ -f "/etc/resolver/${TLD}" ] && { compat_sudo rm -f "/etc/resolver/${TLD}"; ok "Removed /etc/resolver/${TLD}"; }
  compat_sudo dscacheutil -flushcache 2>/dev/null || true
  compat_sudo killall -HUP mDNSResponder 2>/dev/null || true
}

# ------------------------------------------------------------------ systemd-resolved

has_systemd_resolved() {
  compat_has_cmd resolvectl && [ -d /run/systemd/resolve ]
}

systemd_resolved_apply() {
  # Route just the `lh` domain at a local dnsmasq, leaving all other DNS untouched.
  if ! compat_has_cmd dnsmasq; then
    info "Installing dnsmasq…"
    compat_pkg_install dnsmasq || {
      warn "Could not install dnsmasq — falling back to $HOSTS_FILE."
      hosts_apply
      return $?
    }
  fi

  # dnsmasq must not fight systemd-resolved for port 53: bind it to a loopback alias.
  local dnsmasq_dir="/etc/dnsmasq.d"
  compat_sudo mkdir -p "$dnsmasq_dir"
  printf '%s\nlisten-address=127.0.0.54\nbind-interfaces\naddress=/.%s/127.0.0.1\n%s\n' \
    "$MARK_BEGIN" "$TLD" "$MARK_END" \
    | compat_sudo tee "$dnsmasq_dir/leco-${TLD}.conf" >/dev/null
  ok "Wrote $dnsmasq_dir/leco-${TLD}.conf"

  compat_sudo systemctl enable --now dnsmasq >/dev/null 2>&1 || compat_sudo systemctl restart dnsmasq >/dev/null 2>&1 || {
    warn "dnsmasq did not start — falling back to $HOSTS_FILE."
    hosts_apply
    return $?
  }

  local drop="/etc/systemd/resolved.conf.d"
  compat_sudo mkdir -p "$drop"
  printf '%s\n[Resolve]\nDNS=127.0.0.54\nDomains=~%s\n%s\n' "$MARK_BEGIN" "$TLD" "$MARK_END" \
    | compat_sudo tee "$drop/leco-${TLD}.conf" >/dev/null
  ok "Wrote $drop/leco-${TLD}.conf"

  compat_sudo systemctl restart systemd-resolved >/dev/null 2>&1 || true
}

systemd_resolved_remove() {
  compat_sudo rm -f "/etc/systemd/resolved.conf.d/leco-${TLD}.conf" && ok "Removed resolved drop-in"
  compat_sudo rm -f "/etc/dnsmasq.d/leco-${TLD}.conf" && ok "Removed dnsmasq rule"
  compat_sudo systemctl restart systemd-resolved >/dev/null 2>&1 || true
  compat_sudo systemctl restart dnsmasq >/dev/null 2>&1 || true
}

# ------------------------------------------------------------------------ /etc/hosts

# Non-wildcard fallback. Enumerates the hostnames that exist right now; re-run after
# onboarding an app. Written as a single marked block so --remove is exact.
hosts_apply() {
  local names line
  names="$(discover_hosts | tr '\n' ' ')"
  [ -n "$names" ] || { err "No ${TLD} hostnames discovered."; return 1; }
  line="127.0.0.1 $names"

  hosts_remove_block
  printf '%s\n%s\n%s\n' "$MARK_BEGIN" "$line" "$MARK_END" | compat_sudo tee -a "$HOSTS_FILE" >/dev/null
  ok "Wrote ${HOSTS_FILE} block covering: $names"
  warn "This is NOT a wildcard — re-run this script after onboarding a new app."
}

hosts_remove_block() {
  grep -q "^${MARK_BEGIN}$" "$HOSTS_FILE" 2>/dev/null || return 0
  compat_sudo sed -i.leco-bak "/^${MARK_BEGIN}$/,/^${MARK_END}$/d" "$HOSTS_FILE"
}

# ------------------------------------------------------------------------------ main

bold "LEco DevOps — *.${TLD} DNS"
info "Platform: $(compat_os)$(compat_is_wsl && printf ' (WSL2)' || true)"
echo

case "$MODE" in
  check)
    report_state || true
    if compat_is_wsl; then
      echo
      warn "Inside WSL2 this only reflects the Linux side."
      info "The Windows browser uses the Windows resolver: run windows/Install-LhDns.ps1 there."
    fi
    exit 0
    ;;

  remove)
    if compat_is_darwin; then macos_remove
    elif has_systemd_resolved; then systemd_resolved_remove
    fi
    hosts_remove_block && ok "Removed ${HOSTS_FILE} block (if present)"
    ok "Done. *.${TLD} will no longer resolve."
    exit 0
    ;;

  hosts)
    hosts_apply
    ;;

  apply)
    if resolves; then
      ok "*.${TLD} already resolves — nothing to do."
      exit 0
    fi
    if compat_is_darwin; then
      macos_apply
    elif has_systemd_resolved; then
      systemd_resolved_apply
    else
      info "No systemd-resolved and not macOS — using the ${HOSTS_FILE} fallback."
      hosts_apply
    fi
    ;;
esac

echo
bold "Verifying"
if report_state; then
  :
else
  warn "Resolution did not come up as expected. A shell or browser may be caching the old answer."
  info "Re-check with: $0 --check"
fi

if compat_is_wsl; then
  echo
  bold "Windows side (WSL2 cannot do this for you)"
  info "Run in an elevated PowerShell on Windows:  windows\\Install-LhDns.ps1"
fi
