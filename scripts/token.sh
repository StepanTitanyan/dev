#!/bin/bash
# Prints today's API token from .env
# Usage: ./scripts/token.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/../.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: .env not found at $ENV_FILE"
  exit 1
fi

set -a && source "$ENV_FILE" && set +a

# Resolve Python — skips Windows Store stubs
_find_python() {
  for candidate in \
    "/c/Python314/python.exe" \
    "/c/Python313/python.exe" \
    "/c/Python312/python.exe" \
    "/c/Python311/python.exe" \
    "/c/Python310/python.exe" \
    "python3" "python" "py"; do
    if command -v "$candidate" &>/dev/null; then
      version=$("$candidate" --version 2>&1)
      if echo "$version" | grep -q "^Python [0-9]"; then
        echo "$candidate"
        return
      fi
    fi
  done
}

PY=$(_find_python)
if [ -z "$PY" ]; then
  echo "ERROR: Python not found."
  exit 1
fi

$PY -c "
import hmac, hashlib, os
from datetime import datetime, timezone
base = os.environ['API_BASE_TOKEN']
d = datetime.now(timezone.utc).strftime('%Y-%m-%d')
print(hmac.new(base.encode(), d.encode(), hashlib.sha256).hexdigest())
"
