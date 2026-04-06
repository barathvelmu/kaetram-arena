#!/usr/bin/env bash
# Gracefully stop the multi-agent training run, preserving all state for resume.
#
# What it does:
#   1. Sends SIGTERM to orchestrate.py (triggers its graceful shutdown)
#   2. Waits for orchestrator to exit (it stops agents + servers internally)
#   3. Force-kills if timeout exceeded
#   4. Leaves all state intact: .session_counter, logs, dataset
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

# Wait for agent processes to become orphans after orchestrator death
sleep 3

# ── Step 2: Clean up any orphaned agent CLI processes ──
# Match all agent prompt formats: "You play AGGRESSIVE/METHODICAL/CURIOUS", "ClaudeBot", "IMPORTANT", "play the game"
CLAUDE_PIDS=$(pgrep -f "claude -p.*You play\|claude -p.*ClaudeBot\|claude -p.*play the game\|claude -p.*IMPORTANT" 2>/dev/null || true)
if [ -n "$CLAUDE_PIDS" ]; then
  echo "Stopping orphaned claude agent processes..."
  kill -TERM $CLAUDE_PIDS 2>/dev/null || true
  sleep 3
  CLAUDE_PIDS=$(pgrep -f "claude -p.*You play\|claude -p.*ClaudeBot\|claude -p.*play the game\|claude -p.*IMPORTANT" 2>/dev/null || true)
  if [ -n "$CLAUDE_PIDS" ]; then
    kill -9 $CLAUDE_PIDS 2>/dev/null || true
  fi
fi

CODEX_PIDS=$(pgrep -f "codex.*exec" 2>/dev/null || true)
if [ -n "$CODEX_PIDS" ]; then
  echo "Stopping orphaned codex agent processes..."
  kill -TERM $CODEX_PIDS 2>/dev/null || true
  sleep 3
  CODEX_PIDS=$(pgrep -f "codex.*exec" 2>/dev/null || true)
  if [ -n "$CODEX_PIDS" ]; then
    kill -9 $CODEX_PIDS 2>/dev/null || true
  fi
fi

# ── Step 2b: Kill MCP game servers + Playwright browsers ──
MCP_PIDS=$(pgrep -f "mcp_game_server.py" 2>/dev/null || true)
if [ -n "$MCP_PIDS" ]; then
  echo "Stopping MCP game servers..."
  kill -TERM $MCP_PIDS 2>/dev/null || true
  sleep 2
  MCP_PIDS=$(pgrep -f "mcp_game_server.py" 2>/dev/null || true)
  if [ -n "$MCP_PIDS" ]; then
    kill -9 $MCP_PIDS 2>/dev/null || true
  fi
fi
pkill -f "playwright/driver/node" 2>/dev/null || true
pkill -f "npm exec @playwright" 2>/dev/null || true
pkill -f "playwright-mcp" 2>/dev/null || true
pkill -f "game_driver.py" 2>/dev/null || true
# Kill Chrome process groups (Playwright spawns Chrome in its own PGID)
for cpid in $(pgrep -f "chrome-headless-shell" 2>/dev/null); do
  pgid=$(ps -o pgid= -p "$cpid" 2>/dev/null | tr -d ' ')
  [ -n "$pgid" ] && [ "$pgid" != "0" ] && kill -- -"$pgid" 2>/dev/null
done
sleep 1
# Force-kill any survivors
pkill -9 -f "chrome-headless-shell" 2>/dev/null || true
pkill -9 -f "playwright/driver/node" 2>/dev/null || true
pkill -9 -f "npm exec @playwright" 2>/dev/null || true
pkill -9 -f "playwright-mcp" 2>/dev/null || true

# ── Step 3: Stop game servers spawned by orchestrator ──
echo "Stopping game servers..."
for port in $(seq 9001 10 9071); do
  pid=$(ss -tlnp "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' || true)
  if [ -n "$pid" ]; then
    kill "$pid" 2>/dev/null || true
    echo "  Killed server on :$port (PID $pid)"
  fi
done

# Also stop single-agent mode processes and Qwen agent harness
pkill -f "play.sh" 2>/dev/null || true
pkill -f "play_qwen.py" 2>/dev/null || true

# ── Step 3b: Final sweep — kill anything still alive ──
sleep 2
pkill -9 -f "claude -p.*You play\|claude -p.*ClaudeBot\|claude -p.*play the game\|claude -p.*IMPORTANT" 2>/dev/null || true
pkill -9 -f "mcp_game_server.py" 2>/dev/null || true
pkill -9 -f "playwright/driver" 2>/dev/null || true
pkill -9 -f "chrome-headless-shell" 2>/dev/null || true

# ── Step 4: Report preserved state ──
echo ""
echo "=== State preserved for resume ==="
for i in 0 1 2 3 4 5 6 7; do
  SANDBOX="/tmp/kaetram_agent_$i/state"
  if [ -d "$SANDBOX" ]; then
    COUNTER="$SANDBOX/.session_counter"
    if [ -f "$COUNTER" ]; then
      SESSION=$(cat "$COUNTER" 2>/dev/null || echo "?")
      echo "  Agent $i: session #$SESSION"
    fi
  fi
done

LOG_COUNT=$(find "$PROJECT_DIR/dataset/raw" -name "session_*.log" 2>/dev/null | wc -l)
echo ""
echo "  $LOG_COUNT session logs preserved in dataset/raw/"
echo ""
echo "To resume: ./scripts/resume-agent.sh"
echo "To restart fresh: ./scripts/restart-agent.sh"
