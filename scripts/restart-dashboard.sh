#!/usr/bin/env bash
# Restart the dashboard (stop + start).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$SCRIPT_DIR/stop-dashboard.sh"
"$SCRIPT_DIR/start-dashboard.sh"
