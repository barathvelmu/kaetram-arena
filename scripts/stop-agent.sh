#!/usr/bin/env bash
# Gracefully stop the multi-agent training run, preserving all state for resume.
#
# What it does:
#   1. Sends SIGTERM to orchestrate.py (triggers its graceful shutdown)
#   2. Waits for orchestrator to exit (it stops agents + servers internally)
#   3. Force-kills if timeout exceeded
#   4. Leaves all state intact: progress.json, .session_counter, logs, dataset
#   5. Leaves dashboard running (useful for review)
#
# Usage:
#   ./scripts/stop-agent.sh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TIMEOUT=30

echo "=== Stopping Kaetram training run ==="
echo ""

# ── Step 1: Signal orchestrator to shut down gracefully ──
# Match only the actual python3 orchestrate.py process, not tmux/shell wrappers
ORCH_PID=$(pgrep -f "python3 orchestrate.py" 2>/dev/null | head -1 || true)

if [ -n "$ORCH_PID" ]; then
  echo "Sending SIGTERM to orchestrator (PID $ORCH_PID)..."
  kill -TERM "$ORCH_PID" 2>/dev/null || true

  # Wait for graceful shutdown
  echo "Waiting for orchestrator to shut down (timeout ${TIMEOUT}s)..."
  for i in $(seq 1 "$TIMEOUT"); do
    if ! kill -0 "$ORCH_PID" 2>/dev/null; then
      echo "Orchestrator exited gracefully after ${i}s."
      break
    fi
    sleep 1
  done

  # Force kill if still running
  if kill -0 "$ORCH_PID" 2>/dev/null; then
    echo "Timeout reached. Force killing orchestrator..."
    kill -9 "$ORCH_PID" 2>/dev/null || true
    sleep 1
  fi
else
  echo "No orchestrator running."
fi

# Kill the datacol tmux session (holds shell wrappers around orchestrator)
tmux kill-session -t datacol 2>/dev/null || true

# ── Step 2: Clean up any orphaned claude -p processes ──
CLAUDE_PIDS=$(pgrep -f "claude.*-p.*play the game" 2>/dev/null || true)
if [ -n "$CLAUDE_PIDS" ]; then
  echo "Stopping orphaned claude agent processes..."
  kill -TERM $CLAUDE_PIDS 2>/dev/null || true
  sleep 3
  # Force kill any remaining
  CLAUDE_PIDS=$(pgrep -f "claude.*-p.*play the game" 2>/dev/null || true)
  if [ -n "$CLAUDE_PIDS" ]; then
    kill -9 $CLAUDE_PIDS 2>/dev/null || true
  fi
fi

# ── Step 3: Stop game servers spawned by orchestrator ──
echo "Stopping game servers..."
for port in $(seq 9001 10 9071); do
  pid=$(ss -tlnp "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' || true)
  if [ -n "$pid" ]; then
    kill "$pid" 2>/dev/null || true
    echo "  Killed server on :$port (PID $pid)"
  fi
done

# Also stop single-agent mode processes
pkill -f "play.sh" 2>/dev/null || true

# ── Step 4: Report preserved state ──
echo ""
echo "=== State preserved for resume ==="
for i in 0 1 2 3 4 5 6 7; do
  SANDBOX="/tmp/kaetram_agent_$i/state"
  if [ -d "$SANDBOX" ]; then
    PROGRESS="$SANDBOX/progress.json"
    COUNTER="$SANDBOX/.session_counter"
    if [ -f "$PROGRESS" ]; then
      SESSION=$(cat "$COUNTER" 2>/dev/null || echo "?")
      LEVEL=$(python3 -c "import json; print(json.load(open('$PROGRESS')).get('level', '?'))" 2>/dev/null || echo "?")
      echo "  Agent $i: session #$SESSION, level $LEVEL"
    fi
  fi
done

LOG_COUNT=$(find "$PROJECT_DIR/dataset/raw" -name "session_*.log" 2>/dev/null | wc -l)
echo ""
echo "  $LOG_COUNT session logs preserved in dataset/raw/"
echo ""
echo "To resume: ./scripts/resume-agent.sh"
echo "To restart fresh: ./scripts/restart-agent.sh"
