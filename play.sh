#!/usr/bin/env bash
# Autonomous Kaetram gameplay loop — supports Claude Code and Codex CLI
set -euo pipefail
unset CLAUDECODE

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="$PROJECT_DIR/state/progress.json"
SYSTEM_PROMPT_FILE="$PROJECT_DIR/prompts/system.md"

# Parse flags
PERSONALITY=""
HARNESS="claude"
CLAUDE_MODEL="sonnet"
CODEX_MODEL="gpt-5.4"
KIMI_MODEL="kimi-k2"
QWEN_CODE_MODEL="qwen3-coder"
for arg in "$@"; do
  case "$arg" in
    --aggressive)  PERSONALITY="aggressive";;
    --methodical)  PERSONALITY="methodical";;
    --curious)     PERSONALITY="curious";;
    --efficient)   PERSONALITY="efficient";;
    --codex)       HARNESS="codex";;
    --kimi)        HARNESS="kimi";;
    --qwen-code)   HARNESS="qwen-code";;
  esac
done
LOG_DIR="$PROJECT_DIR/logs"
MAX_TURNS=150
PAUSE_BETWEEN=10

# Check for required CLI
case "$HARNESS" in
  codex)
    if ! command -v codex &>/dev/null; then
      echo "ERROR: codex CLI not found. Install with: npm install -g @openai/codex"
      exit 1
    fi
    echo "Using Codex CLI (model: $CODEX_MODEL)"
    ;;
  kimi)
    if ! command -v kimi &>/dev/null; then
      echo "ERROR: kimi CLI not found. Install with: curl -LsSf https://code.kimi.com/install.sh | bash"
      exit 1
    fi
    echo "Using Kimi CLI (model: $KIMI_MODEL)"
    ;;
  qwen-code)
    if ! command -v qwen &>/dev/null; then
      echo "ERROR: qwen-code CLI not found. Install with: npm install -g @qwen-code/qwen-code"
      exit 1
    fi
    echo "Using Qwen Code CLI (model: $QWEN_CODE_MODEL)"
    ;;
  *)
    echo "Using Claude Code CLI (model: $CLAUDE_MODEL)"
    ;;
esac

mkdir -p "$LOG_DIR" "$PROJECT_DIR/state"

if [ ! -f "$STATE_FILE" ]; then
  echo '{"sessions":0,"level":1,"active_quests":[],"completed_quests":[],"inventory_summary":[],"kills_this_session":0,"next_objective":"accept quests from NPCs","notes":"fresh start"}' > "$STATE_FILE"
fi

SESSION=0
while true; do
  SESSION=$((SESSION + 1))
  TIMESTAMP=$(date +%Y%m%d_%H%M%S)
  LOG_FILE="$LOG_DIR/session_${SESSION}_${TIMESTAMP}.log"

  echo "=== Session $SESSION starting at $(date) ==="

  SYSTEM=$(sed -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
               -e "s|__USERNAME__|ClaudeBot|g" \
               -e "s|__SERVER_PORT__||g" \
               "$SYSTEM_PROMPT_FILE")

  # Inject game knowledge block (before personality so agent reads world context first)
  if [ -f "$PROJECT_DIR/prompts/game_knowledge.md" ]; then
    GFILE="$PROJECT_DIR/prompts/game_knowledge.md"
  else
    GFILE=""
  fi

  # Inject personality block
  if [ -n "$PERSONALITY" ] && [ -f "$PROJECT_DIR/prompts/personalities/${PERSONALITY}.md" ]; then
    PFILE="$PROJECT_DIR/prompts/personalities/${PERSONALITY}.md"
  else
    PFILE=""
  fi

  SYSTEM=$(python3 -c "
import sys
s = sys.stdin.read()
gfile = '$GFILE'
pfile = '$PFILE'
g = open(gfile).read() if gfile else ''
p = open(pfile).read() if pfile else ''
s = s.replace('__GAME_KNOWLEDGE_BLOCK__', g)
s = s.replace('__PERSONALITY_BLOCK__', p)
sys.stdout.write(s)
" <<< "$SYSTEM")

  # Read previous progress and include in prompt
  PROGRESS=$(cat "$STATE_FILE" 2>/dev/null || echo '{}')

  # Read game state if available (written by the observe step's page.evaluate() call)
  GAME_STATE=""
  if [ -f "$PROJECT_DIR/state/game_state.json" ]; then
    GAME_STATE=$(python3 -c "
import json, sys
d = json.load(open('$PROJECT_DIR/state/game_state.json'))
d['nearby_entities'] = d.get('nearby_entities', [])[:15]
d['inventory'] = d.get('inventory', [])[:15]
d['quests'] = d.get('quests', [])[:10]
d['achievements'] = d.get('achievements', [])[:10]
print(json.dumps(d, separators=(',',':')))
" 2>/dev/null || echo "")
  fi

  GAME_STATE_BLOCK=""
  if [ -n "$GAME_STATE" ]; then
    GAME_STATE_BLOCK="
Previous game state (from last observe step):
${GAME_STATE}
Use nearest_mob.click_x/click_y to click on targets. Use player_position for spatial awareness."
  fi

  PROMPT="IMPORTANT: Do NOT search for files, read documentation, or explore the filesystem. Your ONLY job is to play the game via the browser. Start IMMEDIATELY with the login code block in your system instructions.

Session #${SESSION}. Your previous progress: ${PROGRESS}
${GAME_STATE_BLOCK}
Follow your system instructions exactly. Load tools, then login, then run the OBSERVE-ACT loop: kill mobs, progress quests, explore. Write progress.json before session ends."

  # Run from isolated dir to prevent the CLI from reading this project's CLAUDE.md / AGENTS.md
  SANDBOX="/tmp/kaetram_session_${SESSION}_$$"
  mkdir -p "$SANDBOX"

  case "$HARNESS" in
    codex)
      # Codex: write system prompt file + AGENTS.md, init git repo
      echo "$SYSTEM" > "$SANDBOX/AGENTS.md"
      echo "$SYSTEM" > "$SANDBOX/system_prompt.md"
      git -C "$SANDBOX" init -q

      TIMEOUT_SECS=$((MAX_TURNS * 30))
      (cd "$SANDBOX" && timeout "${TIMEOUT_SECS}s" codex exec "$PROMPT" \
        --model "$CODEX_MODEL" \
        --dangerously-bypass-approvals-and-sandbox \
        --json \
        -c 'model_instructions_file="system_prompt.md"') \
        2>&1 | tee "$LOG_FILE" || true
      ;;

    kimi)
      # Kimi: resolve .mcp.json template to sandbox, enable thinking and stream-json output
      sed -e "s|__VENV_PYTHON__|${PROJECT_DIR}/.venv/bin/python3|g" \
          -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
          -e "s|__SCREENSHOT_DIR__|${SANDBOX}/state|g" \
          "$PROJECT_DIR/.mcp.json" > "$SANDBOX/.mcp.json"

      # Increased timeout for thinking: ~60s per turn
      TIMEOUT_SECS=$((MAX_TURNS * 60))
      (cd "$SANDBOX" && timeout "${TIMEOUT_SECS}s" kimi -p "$PROMPT" \
        --model "$KIMI_MODEL" \
        --yolo \
        --thinking \
        --output-format stream-json \
        --append-system-prompt "$SYSTEM") \
        2>&1 | tee "$LOG_FILE" || true
      ;;

    qwen-code)
      # Qwen Code: resolve .mcp.json template to sandbox
      sed -e "s|__VENV_PYTHON__|${PROJECT_DIR}/.venv/bin/python3|g" \
          -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
          -e "s|__SCREENSHOT_DIR__|${SANDBOX}/state|g" \
          "$PROJECT_DIR/.mcp.json" > "$SANDBOX/.mcp.json"

      (cd "$SANDBOX" && qwen -p "$PROMPT" \
        --model "$QWEN_CODE_MODEL" \
        --yolo \
        --output-format stream-json \
        --append-system-prompt "$SYSTEM") \
        2>&1 | tee "$LOG_FILE" || true
      ;;

    *)
      # Claude: resolve .mcp.json template and pass via --mcp-config (bypasses project .mcp.json)
      sed -e "s|__VENV_PYTHON__|${PROJECT_DIR}/.venv/bin/python3|g" \
          -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
          -e "s|__SCREENSHOT_DIR__|${SANDBOX}/state|g" \
          -e "s|__SERVER_PORT__||g" \
          -e "s|__USERNAME__|ClaudeBot|g" \
          "$PROJECT_DIR/.mcp.json" > "$SANDBOX/.mcp.json"
      (cd "$SANDBOX" && claude -p "$PROMPT" \
        --model "$CLAUDE_MODEL" \
        --max-turns "$MAX_TURNS" \
        --append-system-prompt "$SYSTEM" \
        --dangerously-skip-permissions \
        --disallowedTools "Glob Grep Agent Edit WebFetch WebSearch Write Skill" \
        --mcp-config "$SANDBOX/.mcp.json" \
        --strict-mcp-config \
        --output-format stream-json \
        --verbose) \
        2>&1 | tee "$LOG_FILE" || true
      ;;
  esac

  rm -rf "$SANDBOX"

  # Auto-extract last game state from session log using the CLI adapter
  python3 -c "
import sys
sys.path.insert(0, '$PROJECT_DIR')
from cli_adapter import get_adapter
adapter = get_adapter(harness='$HARNESS')
state = adapter.parse_game_state_from_log(__import__('pathlib').Path('$LOG_FILE'))
if state:
    import json
    with open('$PROJECT_DIR/state/game_state.json', 'w') as f:
        f.write(state)
" 2>/dev/null || true

  echo "=== Session $SESSION ended at $(date) ==="
  echo "Pausing ${PAUSE_BETWEEN}s before next session..."
  sleep "$PAUSE_BETWEEN"
done
