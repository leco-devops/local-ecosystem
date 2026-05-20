#!/usr/bin/env bash
# List files changed in a git range for release notes. Usage:
#   ./tools/release/list-release-files.sh v0.2.0..v0.3.0
#   ./tools/release/list-release-files.sh v0.2.0..HEAD
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RANGE="${1:-}"
if [[ -z "$RANGE" ]]; then
  echo "Usage: $0 <git-range>   e.g. v0.2.0..v0.3.0 or abc123..HEAD" >&2
  exit 1
fi
cd "$ROOT"
git diff --name-only "$RANGE" | sort -u
