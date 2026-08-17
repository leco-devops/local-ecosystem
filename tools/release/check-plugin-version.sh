#!/usr/bin/env bash
#
# Fail if the Claude Code plugin payload changed without a version bump.
#
# `claude plugin update` compares *version strings*, not commits. If tools/claude-plugin/
# changes while plugin.json keeps the same version, the updater reports success and copies
# nothing — every user who already installed the plugin is frozen at whatever they first
# got, forever, with no error anywhere. That happened here: three commits changed the
# skills and the launcher while the version sat at 0.2.0, and the only way to recover an
# affected machine was a full uninstall/reinstall.
#
# Run before publishing, or from CI on pull requests.
#
#   ./tools/release/check-plugin-version.sh            # compare against origin/main
#   ./tools/release/check-plugin-version.sh <ref>      # compare against any ref

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"

PLUGIN_DIR="tools/claude-plugin"
PLUGIN_MANIFEST="$PLUGIN_DIR/.claude-plugin/plugin.json"
MARKETPLACE="./.claude-plugin/marketplace.json"

BASE="${1:-}"
if [ -z "$BASE" ]; then
  for candidate in origin/main main origin/master master; do
    if git rev-parse --verify --quiet "$candidate" >/dev/null; then
      BASE="$candidate"
      break
    fi
  done
fi

if [ -z "$BASE" ]; then
  echo "❌ No base ref to compare against. Pass one: $0 <ref>" >&2
  exit 2
fi

read_version() {
  # $1 = ref or empty for working tree
  local ref="$1" content
  if [ -z "$ref" ]; then
    content="$(cat "$PLUGIN_MANIFEST")"
  else
    content="$(git show "$ref:$PLUGIN_MANIFEST" 2>/dev/null || true)"
  fi
  [ -n "$content" ] || return 1
  printf '%s' "$content" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version",""))'
}

marketplace_version() {
  python3 - "$MARKETPLACE" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
for entry in data.get("plugins", []):
    if entry.get("name") == "leco":
        print(entry.get("version", ""))
        break
PY
}

CURRENT="$(read_version "" || true)"
BASELINE="$(read_version "$BASE" || true)"
MARKET="$(marketplace_version)"

echo "plugin.json      : ${CURRENT:-<unreadable>}"
echo "marketplace.json : ${MARKET:-<unreadable>}"
echo "baseline ($BASE) : ${BASELINE:-<not present>}"
echo

# 1 · The two manifests must agree, or `claude plugin install` resolves one version and
#     the payload declares another.
if [ -n "$CURRENT" ] && [ -n "$MARKET" ] && [ "$CURRENT" != "$MARKET" ]; then
  echo "❌ Version mismatch: plugin.json says '$CURRENT', marketplace.json says '$MARKET'." >&2
  echo "   Both must match or installs resolve inconsistently." >&2
  exit 1
fi

# 2 · Payload changed but version did not → the silent no-op case.
CHANGED="$(git diff --name-only "$BASE"...HEAD -- "$PLUGIN_DIR" 2>/dev/null || true)"
# Include uncommitted work so this is useful before committing too.
CHANGED="$CHANGED
$(git diff --name-only -- "$PLUGIN_DIR" 2>/dev/null || true)
$(git diff --cached --name-only -- "$PLUGIN_DIR" 2>/dev/null || true)"
CHANGED="$(printf '%s\n' "$CHANGED" | sed '/^$/d' | sort -u)"

if [ -z "$CHANGED" ]; then
  echo "✅ No plugin payload changes since $BASE — no bump required."
  exit 0
fi

echo "Plugin payload files changed:"
printf '%s\n' "$CHANGED" | sed 's/^/   /'
echo

if [ -n "$BASELINE" ] && [ "$CURRENT" = "$BASELINE" ]; then
  echo "❌ The plugin payload changed but the version is still '$CURRENT'." >&2
  echo "   Publishing this way is a no-op for everyone who already installed the plugin:" >&2
  echo "   'claude plugin update' compares version strings and will copy nothing." >&2
  echo >&2
  echo "   Bump the version in BOTH files, then re-run:" >&2
  echo "     $PLUGIN_MANIFEST" >&2
  echo "     $MARKETPLACE" >&2
  exit 1
fi

echo "✅ Plugin payload changed and the version was bumped (${BASELINE:-none} → $CURRENT)."
