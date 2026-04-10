#!/usr/bin/env bash
# Start the finetuned Qwen 3.5 9B agent.
#
# Starts a game server (if not running), then launches play_qwen.sh
# in a tmux session called "qwen".
#
# Usage:
#   ./scripts/start-qwen.sh                  # defaults: 300 turns/session, port 9031
#   ./scripts/start-qwen.sh --max-turns 500  # longer sessions
#   ./scripts/start-qwen.sh --reset          # reset player to Level 1 first
#   ./scripts/start-qwen.sh --endpoint URL   # custom Modal endpoint

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
AGENT_ID=4
USERNAME="QwenBot"
SERVER_PORT=9031
MAX_TURNS=300
RESET=false
BASE=false
ENDPOINT="https://patnir411--kaetram-qwen-serve-inference-serve.modal.run/v1"
BASE_ENDPOINT="https://patnir411--kaetram-qwen-base-inference-serve.modal.run/v1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --reset)       RESET=true; shift;;
    --base)        BASE=true; shift;;
    --max-turns)   MAX_TURNS="$2"; shift 2;;
    --endpoint)    ENDPOINT="$2"; shift 2;;
    --port)        SERVER_PORT="$2"; shift 2;;
    --username)    USERNAME="$2"; shift 2;;
    *)             shift;;
  esac
done

if $BASE; then
  ENDPOINT="$BASE_ENDPOINT"
  USERNAME="QwenBase"
  AGENT_ID=5
  echo "*** BASELINE MODE — using unfinetuned Qwen3.5-9B ***"
fi

SANDBOX="/tmp/kaetram_agent_${AGENT_ID}"

# Check if already running
if pgrep -f "play_qwen.py.*agent_${AGENT_ID}" > /dev/null 2>&1; then
  echo "Qwen agent is already running. Use ./scripts/stop-qwen.sh first."
  exit 1
fi

# Reset player data if requested
if $RESET; then
  echo "Resetting $USERNAME to Level 1..."
  source "$PROJECT_DIR/.venv/bin/activate" 2>/dev/null || true
  python3 -c "
from pymongo import MongoClient
c = MongoClient('localhost', 27017)
db = c['kaetram_devlopment']
for col in ['player_info','player_skills','player_equipment','player_inventory','player_bank','player_quests','player_achievements','player_statistics','player_abilities']:
    db[col].delete_many({'username': '${USERNAME,,}'})
print('  Player data cleared')
"
  rm -rf "$SANDBOX/state/"* "$SANDBOX/logs/"*
  echo "  Sandbox cleared"
fi

# Ensure sandbox exists
mkdir -p "$SANDBOX/state" "$SANDBOX/logs"

# Start game server if not running on any port
GAME_RUNNING=false
for port in $SERVER_PORT 9001; do
  if ss -tlnp 2>/dev/null | grep -q ":$port "; then
    SERVER_PORT=$port
    GAME_RUNNING=true
    break
  fi
done
if ! $GAME_RUNNING; then
  echo "Starting game server..."
  "$SCRIPT_DIR/start-kaetram.sh" &
  sleep 8
  SERVER_PORT=9001
  echo "  Game server started on port $SERVER_PORT"
else
  echo "Game server running on port $SERVER_PORT"
fi

# Ensure dashboard is running
if ! pgrep -f "python3 dashboard.py" > /dev/null 2>&1; then
  echo "Starting dashboard on :8080..."
  source "$PROJECT_DIR/.venv/bin/activate" 2>/dev/null || true
  nohup python3 "$PROJECT_DIR/dashboard.py" > /tmp/dashboard.log 2>&1 &
  sleep 2
fi

# Launch in tmux
echo "Launching Qwen agent in tmux session 'qwen'..."
tmux kill-session -t qwen 2>/dev/null || true
tmux new-session -d -s qwen -c "$PROJECT_DIR" \
  "bash -c './play_qwen.sh --server-port $SERVER_PORT --max-turns $MAX_TURNS --endpoint $ENDPOINT --username $USERNAME 2>&1 | tee /tmp/qwen_agent.log'"

echo ""
echo "Qwen agent running:"
echo "  tmux attach -t qwen     # watch live output"
echo "  Dashboard: http://localhost:8080 (Qwen Live tab)"
echo "  Endpoint: $ENDPOINT"
echo "  Port: $SERVER_PORT"
echo "  Max turns/session: $MAX_TURNS"
echo ""
echo "Stop: ./scripts/stop-qwen.sh"
