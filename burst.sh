#!/usr/bin/env bash
# Usage:
#   . .venv/bin/activate
#   ./burst.sh http://localhost:8000
#   # or: sh burst.sh http://localhost:8000
# See: python scripts/burst.py --help
# One-command burst: ./burst.sh <BASE_URL> [options]
set -euo pipefail
exec python3 "$(dirname "$0")/scripts/burst.py" "$@"
