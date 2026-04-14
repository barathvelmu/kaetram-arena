#!/usr/bin/env bash
# Start the dashboard on :8080. Kills any existing instance first (by PID and port).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Kill existing — by process name AND by port (catches zombies)
pkill -9 -f "python3 dashboard.py" 2>/dev/null || true
PORT_PID=$(ss -tlnp 2>/dev/null | grep ":8080 " | grep -oP 'pid=\K[0-9]+' | head -1 || true)
[ -n "$PORT_PID" ] && kill -9 "$PORT_PID" 2>/dev/null || true
sleep 1

# Wait for port to free
for i in $(seq 1 10); do
  ss -tlnp 2>/dev/null | grep -q ":8080 " || break
  sleep 1
done

source "$PROJECT_DIR/.venv/bin/activate" 2>/dev/null || true
nohup python3 "$PROJECT_DIR/dashboard.py" > /tmp/dashboard.log 2>&1 &
NEW_PID=$!

# Wait for port
for i in $(seq 1 10); do
  if ss -tlnp 2>/dev/null | grep -q ":8080 "; then
    echo "Dashboard running on :8080 (pid $NEW_PID)"
    exit 0
  fi
  sleep 1
done

echo "WARNING: Dashboard may not have started. Check /tmp/dashboard.log"
