#!/usr/bin/env bash
# Check Qwen agent status — process, game state, session info.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SANDBOX="/tmp/kaetram_agent_4"

echo "=== Qwen Agent Status ==="

# Process
if pgrep -f "play_qwen.py" > /dev/null 2>&1; then
  echo "Process:  RUNNING ($(pgrep -f play_qwen.py | head -1))"
else
  echo "Process:  STOPPED"
fi

# tmux
if tmux has-session -t qwen 2>/dev/null; then
  echo "tmux:     session 'qwen' exists (tmux attach -t qwen)"
else
  echo "tmux:     no session"
fi

# Game state
if [ -f "$SANDBOX/state/game_state.json" ]; then
  AGE=$(python3 -c "import os,time; print(f'{time.time()-os.path.getmtime(\"$SANDBOX/state/game_state.json\"):.0f}s ago')")
  python3 -c "
import json
gs = json.load(open('$SANDBOX/state/game_state.json'))
ps = gs.get('player_stats', {})
pp = gs.get('player_position', {})
nav = gs.get('navigation', {})
quests = gs.get('quests', [])
inv = gs.get('inventory', [])
print(f'Player:   HP={ps.get(\"hp\",\"?\")}/{ps.get(\"max_hp\",\"?\")}, Lv={ps.get(\"level\",\"?\")}, XP={ps.get(\"experience\",\"?\")}')
print(f'Position: ({pp.get(\"x\",\"?\")}, {pp.get(\"y\",\"?\")})')
print(f'Nav:      {nav.get(\"status\",\"idle\")}')
print(f'Quests:   {len(quests)} active')
print(f'Inventory: {len(inv)} items')
"
  echo "State:    $AGE"
else
  echo "State:    no game_state.json"
fi

# Session logs
if [ -d "$SANDBOX/logs" ]; then
  N_LOGS=$(ls "$SANDBOX/logs/"*.log 2>/dev/null | wc -l)
  if [ "$N_LOGS" -gt 0 ]; then
    LATEST=$(ls -t "$SANDBOX/logs/"*.log | head -1)
    ENTRIES=$(wc -l < "$LATEST")
    echo "Sessions: $N_LOGS log file(s), latest has $ENTRIES entries"
  else
    echo "Sessions: no logs yet"
  fi
else
  echo "Sessions: no log directory"
fi

# Screenshot
if [ -f "$SANDBOX/state/live_screen.jpg" ]; then
  AGE=$(python3 -c "import os,time; print(f'{time.time()-os.path.getmtime(\"$SANDBOX/state/live_screen.jpg\"):.0f}s')")
  echo "Stream:   live_screen.jpg (${AGE} ago)"
else
  echo "Stream:   no screenshot"
fi

# Modal endpoint
echo "Endpoint: https://patnir411--kaetram-qwen-serve-inference-serve.modal.run/v1"
echo ""
