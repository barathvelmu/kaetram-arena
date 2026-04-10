#!/usr/bin/env bash
# Restart the Qwen agent. Stops, optionally resets, then starts.
#
# Usage:
#   ./scripts/restart-qwen.sh                # restart, preserve progress
#   ./scripts/restart-qwen.sh --reset        # restart from Level 1
#   ./scripts/restart-qwen.sh --max-turns 500

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Restarting Qwen agent ==="

# Stop first
"$SCRIPT_DIR/stop-qwen.sh"

sleep 2

# Pass all args through to start
"$SCRIPT_DIR/start-qwen.sh" "$@"
