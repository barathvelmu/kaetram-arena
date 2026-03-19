#!/usr/bin/env bash
# Restart the multi-agent training run.
#
# What it does:
#   1. Kills the running orchestrator + all claude agent processes
#   2. Kills game server processes (orchestrator restarts them)
#   3. Preserves session logs in dataset/raw/ (training data)
#   4. Clears transient state (screenshots, game_state, progress) per agent sandbox
#   5. Restarts orchestrator in the "datacol" tmux session
#   6. Ensures dashboard is running on :8080
#
# Usage:
#   ./scripts/restart-agent.sh              # 4 agents, 24 hours (defaults)
#   ./scripts/restart-agent.sh 2            # 2 agents, 24 hours
#   ./scripts/restart-agent.sh 4 8          # 4 agents, 8 hours
#   ./scripts/restart-agent.sh 4 0          # 4 agents, no time limit

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
N_AGENTS="${1:-4}"
HOURS="${2:-24}"

echo "=== Restarting Kaetram training run ==="
echo "  Agents: $N_AGENTS"
echo "  Hours:  ${HOURS}"
echo ""

# ── Step 1: Kill orchestrator + agents ──
echo "Stopping orchestrator and agents..."
# Kill orchestrate.py and its child claude processes
pkill -f "orchestrate.py" 2>/dev/null || true
sleep 1
# Kill any remaining claude -p agent processes
pkill -f "claude.*-p.*IMPORTANT.*play the game" 2>/dev/null || true
# Also kill single-agent mode processes
pkill -f "play.sh" 2>/dev/null || true
pkill -f "claude -p.*Login" 2>/dev/null || true
sleep 2

# ── Step 2: Kill game server instances (not the client on 9000) ──
echo "Stopping game servers (preserving client on :9000)..."
for port in $(seq 9001 10 9071); do
  pid=$(ss -tlnp "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' || true)
  if [ -n "$pid" ]; then
    kill "$pid" 2>/dev/null || true
    echo "  Killed server on :$port (PID $pid)"
  fi
done
sleep 1

# ── Step 3: Preserve logs, clear transient state ──
echo "Clearing agent sandbox state (logs preserved)..."
for i in $(seq 0 $((N_AGENTS - 1))); do
  sandbox="/tmp/kaetram_agent_$i/state"
  if [ -d "$sandbox" ]; then
    rm -f "$sandbox/screenshot.png" \
          "$sandbox/live_screen.png" \
          "$sandbox/game_state.json" \
          "$sandbox/progress.json"
    echo "  Cleared /tmp/kaetram_agent_$i/state/"
  fi
done

# Also clear single-agent state
rm -f "$PROJECT_DIR/state/screenshot.png" \
      "$PROJECT_DIR/state/live_screen.png" \
      "$PROJECT_DIR/state/game_state.json"

# Count preserved logs
LOG_COUNT=$(find "$PROJECT_DIR/dataset/raw" -name "session_*.log" 2>/dev/null | wc -l)
echo "  Preserved $LOG_COUNT session logs in dataset/raw/"
echo ""

# ── Step 4: Ensure Kaetram client is running on :9000 ──
if ! ss -tlnp "sport = :9000" 2>/dev/null | grep -q 9000; then
  echo "WARNING: Kaetram client not running on :9000"
  echo "  Start it first:  ./scripts/start-kaetram.sh"
  echo "  (run in the 'kaetram' tmux session)"
  echo ""
fi

# ── Step 5: Restart dashboard if not running ──
if ! ss -tlnp "sport = :8080" 2>/dev/null | grep -q 8080; then
  echo "Starting dashboard on :8080..."
  cd "$PROJECT_DIR"
  nohup python3 dashboard.py > /tmp/dashboard.log 2>&1 &
  echo "  Dashboard PID: $!"
else
  echo "Dashboard already running on :8080"
fi

# ── Step 6: Launch orchestrator in datacol tmux session ──
echo "Launching orchestrator ($N_AGENTS agents, $HOURS hours)..."

ORCH_CMD="cd $PROJECT_DIR && python3 orchestrate.py --agents $N_AGENTS"
if [ "$HOURS" != "0" ]; then
  ORCH_CMD="$ORCH_CMD --hours $HOURS"
fi
ORCH_CMD="$ORCH_CMD 2>&1 | tee /tmp/orchestrate.log"

# Send to existing datacol session, or create one
if tmux has-session -t datacol 2>/dev/null; then
  # Send Ctrl-C first to clear any leftover prompt, then the command
  tmux send-keys -t datacol C-c 2>/dev/null || true
  sleep 0.5
  tmux send-keys -t datacol "$ORCH_CMD" Enter
else
  tmux new-session -d -s datacol -c "$PROJECT_DIR" "$ORCH_CMD"
fi

echo ""
echo "=== Training run restarted ==="
echo "  Orchestrator: tmux attach -t datacol"
echo "  Dashboard:    http://localhost:8080"
echo "  Logs:         $PROJECT_DIR/dataset/raw/agent_*/logs/"
echo ""
echo "  Monitor: tail -f /tmp/orchestrate.log"
