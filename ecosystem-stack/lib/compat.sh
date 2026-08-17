# Cross-platform compatibility layer.
#
# Sourced, never executed. Provides the OS-conditional primitives that were previously
# re-derived in each script, plus the handful of places where macOS and Linux genuinely
# need different commands.
#
# Targets: macOS (bash 3.2, BSD userland), Debian/Ubuntu, RHEL/Fedora, Arch, and WSL2
# (which reports itself as Linux and is treated as Linux everywhere except where the
# Windows host is involved — see compat_is_wsl).
#
# Deliberately bash-3.2 clean: no associative arrays, no ${var,,}, no mapfile without a
# fallback. macOS still ships bash 3.2 as /bin/bash and probably always will.

# Guard against double-sourcing: these are pure function definitions, but the cached
# lookups below should only run once.
if [ -n "${_LECO_COMPAT_SOURCED:-}" ]; then
  return 0 2>/dev/null || true
fi
_LECO_COMPAT_SOURCED=1

# --------------------------------------------------------------------------- basics

compat_has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

# compat_os -> darwin | linux | windows | other
#
# Cached after the first call; `uname` was previously shelled out to on every check.
_LECO_OS=""
compat_os() {
  if [ -z "$_LECO_OS" ]; then
    case "$(uname -s)" in
      Darwin) _LECO_OS="darwin" ;;
      Linux)  _LECO_OS="linux" ;;
      # Git Bash / MSYS2 / Cygwin. Not a supported way to run the stack — the platform
      # needs a real Linux userland — but naming it lets callers fail with a useful
      # message instead of "unsupported OS".
      MINGW*|MSYS*|CYGWIN*) _LECO_OS="windows" ;;
      *) _LECO_OS="other" ;;
    esac
  fi
  printf '%s\n' "$_LECO_OS"
}

compat_is_darwin() { [ "$(compat_os)" = "darwin" ]; }
compat_is_linux()  { [ "$(compat_os)" = "linux" ]; }

# True inside a WSL2 distro. Matters because the Linux side cannot reach the Windows
# certificate store, the Windows hosts file, or the Windows resolver — anything that has
# to happen "on the Windows side" must be handed to windows/*.ps1 instead.
compat_is_wsl() {
  [ "$(compat_os)" = "linux" ] || return 1
  [ -n "${WSL_DISTRO_NAME:-}" ] && return 0
  grep -qiE '(microsoft|wsl)' /proc/sys/kernel/osrelease 2>/dev/null
}

# Repo checkout living on the Windows drive rather than the WSL2 filesystem. Works, but
# every file operation crosses the 9p boundary and Docker bind mounts behave differently,
# so it is worth warning about rather than silently tolerating.
compat_is_on_windows_drive() {
  compat_is_wsl || return 1
  case "${1:-$PWD}" in
    /mnt/[a-z]/*) return 0 ;;
    *) return 1 ;;
  esac
}

# --------------------------------------------------------------- distro / packaging

# compat_linux_family -> debian | rhel | arch | suse | alpine | unknown
_LECO_LINUX_FAMILY=""
compat_linux_family() {
  if [ -z "$_LECO_LINUX_FAMILY" ]; then
    _LECO_LINUX_FAMILY="unknown"
    if [ -r /etc/os-release ]; then
      # ID and ID_LIKE together cover derivatives (Mint->debian, Rocky->rhel, ...)
      local id id_like probe
      id="$(. /etc/os-release 2>/dev/null && printf '%s' "${ID:-}")"
      id_like="$(. /etc/os-release 2>/dev/null && printf '%s' "${ID_LIKE:-}")"
      probe="$id $id_like"
      case "$probe" in
        *debian*|*ubuntu*)          _LECO_LINUX_FAMILY="debian" ;;
        *rhel*|*fedora*|*centos*)   _LECO_LINUX_FAMILY="rhel" ;;
        *arch*)                     _LECO_LINUX_FAMILY="arch" ;;
        *suse*)                     _LECO_LINUX_FAMILY="suse" ;;
        *alpine*)                   _LECO_LINUX_FAMILY="alpine" ;;
      esac
    fi
    # Fall back to whichever package manager is actually present. /etc/os-release is
    # absent or unhelpful on some minimal container images.
    if [ "$_LECO_LINUX_FAMILY" = "unknown" ]; then
      if   compat_has_cmd apt-get; then _LECO_LINUX_FAMILY="debian"
      elif compat_has_cmd dnf;     then _LECO_LINUX_FAMILY="rhel"
      elif compat_has_cmd yum;     then _LECO_LINUX_FAMILY="rhel"
      elif compat_has_cmd pacman;  then _LECO_LINUX_FAMILY="arch"
      elif compat_has_cmd zypper;  then _LECO_LINUX_FAMILY="suse"
      elif compat_has_cmd apk;     then _LECO_LINUX_FAMILY="alpine"
      fi
    fi
  fi
  printf '%s\n' "$_LECO_LINUX_FAMILY"
}

# compat_pkg_manager -> brew | apt | dnf | yum | pacman | zypper | apk | "" (none found)
compat_pkg_manager() {
  if compat_is_darwin; then
    compat_has_cmd brew && { printf 'brew\n'; return 0; }
    printf '\n'; return 0
  fi
  for mgr in apt-get dnf yum pacman zypper apk; do
    if compat_has_cmd "$mgr"; then
      [ "$mgr" = "apt-get" ] && mgr="apt"
      printf '%s\n' "$mgr"
      return 0
    fi
  done
  printf '\n'
}

# Translate a canonical package name to this platform's spelling.
#
# Package naming is the single largest source of "works on Ubuntu, breaks on Fedora":
# docker.io does not exist outside Debian, and mkcert is absent from older repos on
# every distro. Callers pass the canonical name; an empty result means "no package for
# this platform, install it another way".
compat_pkg_name() {
  local canonical="$1" mgr
  mgr="$(compat_pkg_manager)"
  case "$canonical:$mgr" in
    docker:brew)      printf 'docker\n' ;;          # cask, see compat_pkg_install
    docker:apt)       printf 'docker.io\n' ;;
    docker:dnf|docker:yum) printf 'docker\n' ;;
    docker:pacman)    printf 'docker\n' ;;
    docker:zypper)    printf 'docker\n' ;;
    docker:apk)       printf 'docker\n' ;;

    compose:apt)      printf 'docker-compose-plugin\n' ;;
    compose:dnf|compose:yum) printf 'docker-compose-plugin\n' ;;
    compose:pacman)   printf 'docker-compose\n' ;;
    compose:zypper)   printf 'docker-compose\n' ;;
    compose:apk)      printf 'docker-cli-compose\n' ;;
    compose:*)        printf '\n' ;;                # bundled with Docker Desktop

    python3:apt)      printf 'python3\n' ;;
    python3:pacman)   printf 'python\n' ;;
    python3:*)        printf 'python3\n' ;;

    pyyaml:apt)       printf 'python3-yaml\n' ;;
    pyyaml:dnf|pyyaml:yum) printf 'python3-pyyaml\n' ;;
    pyyaml:pacman)    printf 'python-yaml\n' ;;
    pyyaml:zypper)    printf 'python3-PyYAML\n' ;;
    pyyaml:apk)       printf 'py3-yaml\n' ;;
    pyyaml:brew)      printf '\n' ;;                # use pip; brew has no PyYAML formula

    mkcert:pacman)    printf 'mkcert\n' ;;
    mkcert:*)         printf 'mkcert\n' ;;

    dnsmasq:*)        printf 'dnsmasq\n' ;;
    git:*)            printf 'git\n' ;;
    curl:*)           printf 'curl\n' ;;
    jq:*)             printf 'jq\n' ;;
    openssl:apt)      printf 'openssl\n' ;;
    openssl:*)        printf 'openssl\n' ;;
    *)                printf '%s\n' "$canonical" ;;
  esac
}

# compat_pkg_install <canonical-name>... — install via whatever manager exists.
# Returns non-zero (and explains) rather than guessing when no manager is available.
compat_pkg_install() {
  local mgr names name pkg
  mgr="$(compat_pkg_manager)"
  names=""
  for name in "$@"; do
    pkg="$(compat_pkg_name "$name")"
    [ -n "$pkg" ] && names="$names $pkg"
  done
  # shellcheck disable=SC2086 # deliberate word-splitting: $names is a package list
  set -- $names
  if [ "$#" -eq 0 ]; then
    return 0
  fi

  case "$mgr" in
    brew)
      # Docker on macOS is Docker Desktop, which is a cask, not a formula.
      if [ "$1" = "docker" ]; then
        brew install --cask docker
      else
        brew install "$@"
      fi
      ;;
    apt)    compat_sudo apt-get update && compat_sudo apt-get install -y "$@" ;;
    dnf)    compat_sudo dnf install -y "$@" ;;
    yum)    compat_sudo yum install -y "$@" ;;
    pacman) compat_sudo pacman -Sy --noconfirm "$@" ;;
    zypper) compat_sudo zypper install -y "$@" ;;
    apk)    compat_sudo apk add "$@" ;;
    "")
      if compat_is_darwin; then
        printf 'No package manager found. Install Homebrew first: https://brew.sh\n' >&2
      else
        printf 'No supported package manager found (tried apt/dnf/yum/pacman/zypper/apk).\n' >&2
      fi
      return 1
      ;;
  esac
}

# --------------------------------------------------------------------------- sudo

# Run a command with elevation only when elevation is actually needed and available.
# Root in a container has no sudo binary and does not need one; a non-root user without
# sudo needs to be told rather than watching a cryptic "command not found".
compat_sudo() {
  if [ "$(id -u)" = "0" ]; then
    "$@"
    return $?
  fi
  if compat_has_cmd sudo; then
    sudo "$@"
    return $?
  fi
  printf 'This step needs root and sudo is not installed: %s\n' "$*" >&2
  return 1
}

compat_can_sudo() {
  [ "$(id -u)" = "0" ] && return 0
  compat_has_cmd sudo
}

# --------------------------------------------------------------------- diagnostics

# The OS-appropriate answer to "Docker is not running". Previously hardcoded to Docker
# Desktop, which is meaningless on a Linux server.
compat_docker_start_hint() {
  if compat_is_darwin; then
    printf 'Start Docker Desktop (open -a Docker), then wait for the whale icon to settle.\n'
  elif compat_is_wsl; then
    printf 'Start Docker Desktop on Windows and enable WSL integration for this distro (Settings > Resources > WSL Integration).\n'
  elif compat_is_linux; then
    printf 'Start the daemon: sudo systemctl start docker. If that succeeds but you still get permission denied, add yourself to the docker group: sudo usermod -aG docker "$USER" (then log out and back in).\n'
  else
    printf 'Start the Docker daemon.\n'
  fi
}

compat_open_url() {
  local url="$1"
  if compat_is_darwin && compat_has_cmd open; then
    open "$url"
  elif compat_is_wsl && compat_has_cmd wslview; then
    wslview "$url"
  elif compat_is_wsl && [ -x /mnt/c/Windows/System32/cmd.exe ]; then
    # Hand the URL to the Windows browser; /c start needs the URL quoted empty-title first.
    /mnt/c/Windows/System32/cmd.exe /c start "" "$url" >/dev/null 2>&1
  elif compat_has_cmd xdg-open; then
    xdg-open "$url" >/dev/null 2>&1
  else
    printf 'Open this in a browser: %s\n' "$url"
  fi
}

# Does <host> resolve? getent is glibc-only, dscacheutil is macOS-only; try both and
# fall back to a ping probe on musl systems that have neither.
compat_dns_lookup() {
  local host="$1"
  if compat_has_cmd getent; then
    getent hosts "$host" 2>/dev/null | head -1 && return 0
  fi
  if compat_has_cmd dscacheutil; then
    dscacheutil -q host -a name "$host" 2>/dev/null | awk '/ip_address/ {print $2; exit}' | grep -q . && {
      dscacheutil -q host -a name "$host" 2>/dev/null | awk '/ip_address/ {print $2 "  " "'"$host"'"; exit}'
      return 0
    }
  fi
  if compat_has_cmd ping; then
    ping -c1 -W1 "$host" >/dev/null 2>&1 && { printf '%s resolves\n' "$host"; return 0; }
  fi
  return 1
}

# ------------------------------------------------------------------------- openssl

# Prefer a real OpenSSL over macOS's LibreSSL.
#
# /usr/bin/openssl on macOS is LibreSSL, which has no `-checkhost`. Code that assumes
# OpenSSL semantics silently reports every hostname as failing verification. Resolve the
# best available binary once, and let callers ask whether -checkhost is usable.
_LECO_OPENSSL=""
compat_openssl() {
  if [ -z "$_LECO_OPENSSL" ]; then
    _LECO_OPENSSL="openssl"
    if compat_is_darwin && compat_has_cmd brew; then
      local prefix
      for formula in openssl@3 openssl@1.1 openssl; do
        prefix="$(brew --prefix "$formula" 2>/dev/null)" || continue
        if [ -n "$prefix" ] && [ -x "$prefix/bin/openssl" ]; then
          _LECO_OPENSSL="$prefix/bin/openssl"
          break
        fi
      done
    fi
  fi
  printf '%s\n' "$_LECO_OPENSSL"
}

compat_openssl_has_checkhost() {
  local ssl
  ssl="$(compat_openssl)"
  "$ssl" version 2>/dev/null | grep -qi '^LibreSSL' && return 1
  return 0
}

# compat_cert_covers_host <cert-file> <hostname>
#
# Uses -checkhost where it exists, and otherwise parses the SAN list and matches it in
# awk — including wildcard SANs — so the answer is the same on LibreSSL.
compat_cert_covers_host() {
  local cert="$1" host="$2" ssl
  ssl="$(compat_openssl)"
  if compat_openssl_has_checkhost; then
    "$ssl" x509 -in "$cert" -noout -checkhost "$host" >/dev/null 2>&1
    return $?
  fi
  "$ssl" x509 -in "$cert" -noout -text 2>/dev/null \
    | awk -v want="$host" '
        /Subject Alternative Name/ { grab = 1; next }
        grab {
          n = split($0, parts, ",")
          for (i = 1; i <= n; i++) {
            entry = parts[i]
            sub(/^[ \t]*DNS:/, "", entry)
            gsub(/[ \t]/, "", entry)
            if (entry == "") continue
            if (entry == want) { found = 1; exit }
            if (substr(entry, 1, 2) == "*.") {
              suffix = substr(entry, 2)                 # ".lh" from "*.lh"
              wl = length(want); sl = length(suffix)
              if (wl > sl && substr(want, wl - sl + 1) == suffix) {
                # A wildcard matches exactly one label, so reject a.b.lh against *.lh
                stem = substr(want, 1, wl - sl)
                if (index(stem, ".") == 0) { found = 1; exit }
              }
            }
          }
          grab = 0
        }
        END { exit(found ? 0 : 1) }
      '
}

# ------------------------------------------------------------------------ arrays

# Read stdin lines into a named array, on bash 3.2 as well as 4+.
#   compat_read_lines MYARR < <(some-command)
compat_read_lines() {
  local __name="$1" __line
  eval "$__name=()"
  while IFS= read -r __line || [ -n "$__line" ]; do
    [ -z "$__line" ] && continue
    eval "$__name+=(\"\$__line\")"
  done
}
