#!/usr/bin/env bash
# Run eval harness for base + r8-sft in parallel.
# Each model gets its own game server, username, and sandbox.
#
# Usage:
#   ./scripts/run-eval.sh                    # 3 episodes, scenario D
#   ./scripts/run-eval.sh --episodes 5       # 5 episodes
#   ./scripts/run-eval.sh --scenario A       # Rat Grind (100 turns)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

EPISODES=3
SCENARIO=D

while [[ $# -gt 0 ]]; do
  case "$1" in
    --episodes)  EPISODES="$2"; shift 2;;
    --scenario)  SCENARIO="$2"; shift 2;;
    *)           shift;;
  esac
done

# ── Cleanup ──
echo "Cleaning up previous eval runs..."
pkill -9 -f "eval_harness" 2>/dev/null || true
pkill -9 -f "play_qwen" 2>/dev/null || true
pkill -9 -f "mcp_game_server" 2>/dev/null || true
pkill -9 -f "chrome-headless-shell" 2>/dev/null || true
pkill -9 -f "playwright/driver" 2>/dev/null || true
BASE_GS_PID=$(ss -tlnp 2>/dev/null | grep ":9041 " | grep -oP 'pid=\K[0-9]+' | head -1 || true)
[ -n "$BASE_GS_PID" ] && kill -9 "$BASE_GS_PID" 2>/dev/null || true
sleep 2

# Clean eval sandboxes and results
rm -rf /tmp/kaetram_eval_* dataset/eval/r8-sft dataset/eval/base

# Reset eval player data in MongoDB
source "$PROJECT_DIR/.venv/bin/activate" 2>/dev/null || true
python3 -c "
from pymongo import MongoClient
c = MongoClient('localhost', 27017)
db = c['kaetram_devlopment']
for username in ['evalbotsft', 'evalbotbase']:
    for col in ['player_info','player_skills','player_equipment','player_inventory','player_bank','player_quests','player_achievements','player_statistics','player_abilities']:
        db[col].delete_many({'username': username})
print('  Eval player data cleared')
"

# ── Ensure game servers ──
# Port 9001 (r8-sft) — should already be running
if ! ss -tlnp 2>/dev/null | grep -q ":9001 "; then
  echo "Starting game server on port 9001..."
  (source "$HOME/.nvm/nvm.sh" && nvm use 20 --silent && cd ~/projects/Kaetram-Open/packages/server && \
   ACCEPT_LICENSE=true SKIP_DATABASE=false exec node --enable-source-maps dist/main.js --port 9001) &
  for i in $(seq 1 30); do ss -tlnp 2>/dev/null | grep -q ":9001 " && break; sleep 1; done
fi

# Port 9041 (base)
echo "Starting game server on port 9041..."
(source "$HOME/.nvm/nvm.sh" && nvm use 20 --silent && cd ~/projects/Kaetram-Open/packages/server && \
 ACCEPT_LICENSE=true SKIP_DATABASE=false exec node --enable-source-maps dist/main.js --port 9041) &
for i in $(seq 1 60); do
  if ss -tlnp 2>/dev/null | grep -q ":9041 "; then
    echo "  Game server ready on 9041 (${i}s)"
    break
  fi
  sleep 1
done

# ── Ensure dashboard ──
if ! pgrep -f "python3 dashboard.py" > /dev/null 2>&1; then
  "$SCRIPT_DIR/start-dashboard.sh"
fi

# ── Launch evals in parallel ──
echo ""
echo "Starting eval: $EPISODES episodes × 2 models, scenario $SCENARIO"
echo ""

PYTHONUNBUFFERED=1 python3 "$PROJECT_DIR/eval_harness.py" \
  --models "r8-sft=https://patnir411--kaetram-qwen-serve-inference-serve.modal.run/v1" \
  --episodes "$EPISODES" --scenario "$SCENARIO" \
  --username evalbotSFT --server-port 9001 \
  > /tmp/eval_r8sft.log 2>&1 &
SFT_PID=$!
echo "  r8-SFT eval started (PID $SFT_PID, log: /tmp/eval_r8sft.log)"

PYTHONUNBUFFERED=1 python3 "$PROJECT_DIR/eval_harness.py" \
  --models "base=https://patnir411--kaetram-qwen-base-inference-serve.modal.run/v1" \
  --episodes "$EPISODES" --scenario "$SCENARIO" \
  --username evalbotBase --server-port 9041 \
  > /tmp/eval_base.log 2>&1 &
BASE_PID=$!
echo "  Base eval started (PID $BASE_PID, log: /tmp/eval_base.log)"

echo ""
echo "Both evals running in parallel."
echo "  Dashboard: http://localhost:8080 (Eval tab — live side-by-side + metrics)"
echo "  Logs: tail -f /tmp/eval_r8sft.log"
echo "        tail -f /tmp/eval_base.log"
echo ""
echo "Stop: pkill -f eval_harness"

# ── Monitor loop ──
while kill -0 $SFT_PID 2>/dev/null || kill -0 $BASE_PID 2>/dev/null; do
  sleep 30
  SFT_STATUS="running"
  BASE_STATUS="running"
  kill -0 $SFT_PID 2>/dev/null || SFT_STATUS="done (rc=$(wait $SFT_PID 2>/dev/null; echo $?))"
  kill -0 $BASE_PID 2>/dev/null || BASE_STATUS="done (rc=$(wait $BASE_PID 2>/dev/null; echo $?))"

  SFT_EP=0; BASE_EP=0
  [ -f dataset/eval/r8-sft/results.json ] && SFT_EP=$(python3 -c "import json; print(len([e for e in json.load(open('dataset/eval/r8-sft/results.json'))['episodes'] if e.get('status')=='ok']))" 2>/dev/null || echo 0)
  [ -f dataset/eval/base/results.json ] && BASE_EP=$(python3 -c "import json; print(len([e for e in json.load(open('dataset/eval/base/results.json'))['episodes'] if e.get('status')=='ok']))" 2>/dev/null || echo 0)

  echo "[$(date +%H:%M)] r8-sft: $SFT_STATUS ($SFT_EP/$EPISODES eps) | base: $BASE_STATUS ($BASE_EP/$EPISODES eps)"
done

# ── Cleanup game server on 9041 ──
BASE_GS_PID=$(ss -tlnp 2>/dev/null | grep ":9041 " | grep -oP 'pid=\K[0-9]+' | head -1 || true)
[ -n "$BASE_GS_PID" ] && kill "$BASE_GS_PID" 2>/dev/null || true

echo ""
echo "EVAL COMPLETE"
echo "  Results: dataset/eval/r8-sft/results.json"
echo "           dataset/eval/base/results.json"
echo ""
echo "Compare: python3 eval_compare.py dataset/eval/base/results.json dataset/eval/r8-sft/results.json"
