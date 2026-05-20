#!/usr/bin/env bash
# Cloud VM installer — non-interactive friendly wrapper around install-foundation.sh
set -euo pipefail
STACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$STACK_DIR/install-foundation.sh" \
  --mode cloud \
  --non-interactive \
  "$@"
