#!/usr/bin/env bash
# Autonomous Kaetram gameplay loop
set -euo pipefail
unset CLAUDECODE

PROJECT_DIR="$HOME/projects/kaetram-agent"
STATE_FILE="$PROJECT_DIR/state/progress.json"
SYSTEM_PROMPT_FILE="$PROJECT_DIR/prompts/system.md"
LOG_DIR="$PROJECT_DIR/logs"
MAX_TURNS=50
PAUSE_BETWEEN=10

mkdir -p "$LOG_DIR" "$PROJECT_DIR/state"

if [ ! -f "$STATE_FILE" ]; then
  echo '{"sessions":0,"milestone":"not_started","level":0,"notes":""}' > "$STATE_FILE"
fi

SESSION=0
while true; do
  SESSION=$((SESSION + 1))
  TIMESTAMP=$(date +%Y%m%d_%H%M%S)
  LOG_FILE="$LOG_DIR/session_${SESSION}_${TIMESTAMP}.log"

  echo "=== Session $SESSION starting at $(date) ==="

  SYSTEM=$(cat "$SYSTEM_PROMPT_FILE")

  claude -p "Session #${SESSION}. Follow your system instructions exactly. Start by running the login code block, then play the game." \
    --model sonnet \
    --max-turns "$MAX_TURNS" \
    --append-system-prompt "$SYSTEM" \
    --dangerously-skip-permissions \
    2>&1 | tee "$LOG_FILE" || true

  echo "=== Session $SESSION ended at $(date) ==="
  echo "Pausing ${PAUSE_BETWEEN}s before next session..."
  sleep "$PAUSE_BETWEEN"
done
