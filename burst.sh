#!/usr/bin/env bash
# Usage:
#   Download both files together in the same repo layout:
#       ./burst.sh
#       ./scripts/burst.py
#   Then run either of these:
#       ./burst.sh http://localhost:8000
#       sh burst.sh http://localhost:8000
#       python3 scripts/burst.py http://localhost:8000 --requests 200
#   For a live hosted service:
#       ./burst.sh https://seat-reservation-system-msi3.onrender.com/ --requests 200
#   If you are running from the project checkout, you may also activate the venv first:
#       . .venv/bin/activate
#   See: python3 scripts/burst.py --help
# One-command burst: ./burst.sh <BASE_URL> [options]
set -euo pipefail
exec python3 "$(dirname "$0")/scripts/burst.py" "$@"
