#!/usr/bin/env bash
# Bump platform VERSION and version.json. Usage: ./tools/release/bump-version.sh 0.4.0
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
VER="${1:-}"
if [[ -z "$VER" ]]; then
  echo "Usage: $0 <semver>   e.g. 0.4.0" >&2
  exit 1
fi
if ! [[ "$VER" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?$ ]]; then
  echo "Invalid semver: $VER" >&2
  exit 1
fi
DATE="$(date -u +%Y-%m-%d)"
echo "$VER" >"$ROOT/VERSION"
python3 - "$ROOT/version.json" "$VER" "$DATE" <<'PY'
import json, sys
path, ver, date = sys.argv[1:4]
data = json.loads(open(path, encoding="utf-8").read())
data["version"] = ver
data["released"] = date
if "components" in data and "platform" in data["components"]:
    data["components"]["platform"]["version"] = ver
open(path, "w", encoding="utf-8").write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
PY
echo "Bumped to $VER (released $DATE)"
echo "Next: update CHANGELOG.md, releases/v${VER}.md, docs/RELEASE_NOTES.md, then commit and tag v${VER}"
