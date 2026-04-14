#!/usr/bin/env bash
# Stop the dashboard on :8080. Kills by PID and by port.
set -euo pipefail

# Kill by process name
PID=$(pgrep -f "python3 dashboard.py" || true)
[ -n "$PID" ] && kill -9 "$PID" 2>/dev/null || true

# Kill by port (catches zombies that pgrep misses)
PORT_PID=$(ss -tlnp 2>/dev/null | grep ":8080 " | grep -oP 'pid=\K[0-9]+' | head -1 || true)
[ -n "$PORT_PID" ] && kill -9 "$PORT_PID" 2>/dev/null || true

sleep 1

# Verify
if ss -tlnp 2>/dev/null | grep -q ":8080 "; then
  echo "WARNING: Port 8080 still in use"
else
  echo "Dashboard stopped"
fi
