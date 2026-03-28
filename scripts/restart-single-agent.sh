#!/usr/bin/env bash
# Restart a single agent within a running orchestration.
#
# Kills the agent's CLI process so the orchestrator auto-restarts it.
# Optionally resets sandbox state and/or changes personality/harness.
#
# Usage:
#   ./scripts/restart-single-agent.sh 2                    # restart agent 2
#   ./scripts/restart-single-agent.sh 2 --reset             # reset state (fresh level 1)
#   ./scripts/restart-single-agent.sh 2 --codex             # switch to codex harness
#   ./scripts/restart-single-agent.sh 2 --claude             # switch to claude harness
#   ./scripts/restart-single-agent.sh 2 --personality aggressive
#   ./scripts/restart-single-agent.sh 2 --reset --codex --personality curious

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Parse args
AGENT_ID=""
RESET=false
NEW_HARNESS=""
NEW_PERSONALITY=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --reset)       RESET=true; shift;;
    --claude)      NEW_HARNESS="claude"; shift;;
    --codex)       NEW_HARNESS="codex"; shift;;
    --kimi)        NEW_HARNESS="kimi"; shift;;
    --qwen-code)   NEW_HARNESS="qwen-code"; shift;;
    --personality) NEW_PERSONALITY="$2"; shift 2;;
    -h|--help)
      echo "Usage: $0 <agent_id> [--reset] [--claude|--codex|--kimi|--qwen-code] [--personality <name>]"
      echo ""
      echo "  agent_id         Agent number (0-7)"
      echo "  --reset          Clear sandbox state + reset DB (fresh level 1)"
      echo "  --claude         Switch agent to Claude CLI"
      echo "  --codex          Switch agent to Codex CLI"
      echo "  --kimi           Switch agent to Kimi CLI"
      echo "  --qwen-code      Switch agent to Qwen Code CLI"
      echo "  --personality X  Change personality (aggressive/methodical/curious/efficient)"
      exit 0;;
    *)
      if [ -z "$AGENT_ID" ] && [[ "$1" =~ ^[0-9]+$ ]]; then
        AGENT_ID="$1"
      else
        echo "Unknown argument: $1" >&2; exit 1
      fi
      shift;;
  esac
done

if [ -z "$AGENT_ID" ]; then
  echo "ERROR: agent_id required. Usage: $0 <agent_id> [--reset] [--claude|--codex|--kimi|--qwen-code] [--personality <name>]" >&2
  exit 1
fi

SANDBOX="/tmp/kaetram_agent_$AGENT_ID"
METADATA="$SANDBOX/metadata.json"

if [ ! -d "$SANDBOX" ]; then
  echo "ERROR: No sandbox at $SANDBOX — agent $AGENT_ID doesn't exist" >&2
  exit 1
fi

# Read current metadata
if [ -f "$METADATA" ]; then
  CUR_USERNAME=$(python3 -c "import json; print(json.load(open('$METADATA')).get('username','Agent$AGENT_ID'))" 2>/dev/null || echo "Agent$AGENT_ID")
  CUR_HARNESS=$(python3 -c "import json; print(json.load(open('$METADATA')).get('harness','claude'))" 2>/dev/null || echo "claude")
  CUR_PERSONALITY=$(python3 -c "import json; print(json.load(open('$METADATA')).get('personality','efficient'))" 2>/dev/null || echo "efficient")
  CUR_PORT=$(python3 -c "import json; print(json.load(open('$METADATA')).get('server_port', $((9001 + AGENT_ID * 10))))" 2>/dev/null || echo "$((9001 + AGENT_ID * 10))")
else
  CUR_USERNAME="Agent$AGENT_ID"
  CUR_HARNESS="claude"
  CUR_PERSONALITY="efficient"
  CUR_PORT=$((9001 + AGENT_ID * 10))
fi

# Default harness is current harness (or claude if not set)
HARNESS="${NEW_HARNESS:-${CUR_HARNESS:-claude}}"
PERSONALITY="${NEW_PERSONALITY:-$CUR_PERSONALITY}"

# Validate personality
if [ -n "$NEW_PERSONALITY" ]; then
  case "$NEW_PERSONALITY" in
    aggressive|methodical|curious|efficient) ;;
    *) echo "ERROR: Invalid personality '$NEW_PERSONALITY'. Use: aggressive, methodical, curious, efficient" >&2; exit 1;;
  esac
fi

echo "=== Restarting Agent $AGENT_ID ==="
echo "  Username:    $CUR_USERNAME"
echo "  Harness:     $CUR_HARNESS → $HARNESS"
echo "  Personality: $CUR_PERSONALITY → $PERSONALITY"
echo "  Server port: $CUR_PORT"
[ "$RESET" = true ] && echo "  Reset:       YES (fresh level 1)"
echo ""

# ── Step 1: Kill the agent's CLI process ──
echo "Killing agent $AGENT_ID process..."
# Kill processes for each possible harness type
pkill -f "codex.*exec" 2>/dev/null || true
pkill -f "claude.*-p.*$SANDBOX" 2>/dev/null || true
pkill -f "kimi.*-p.*" 2>/dev/null || true
pkill -f "qwen.*-p.*" 2>/dev/null || true
# Also try matching by username in the prompt
pkill -f "$CUR_USERNAME" 2>/dev/null || true
sleep 2

# ── Always clear session counter so agent starts fresh session ──
# (This ensures orchestrator increments the counter and starts a new session,
#  rather than resuming an incomplete one)
STATE_DIR="$SANDBOX/state"
rm -f "$STATE_DIR/.session_counter"
echo "  Cleared session counter — agent will start fresh session on restart"

# ── Step 2: Update metadata if harness or personality changed ──
if [ "$HARNESS" != "$CUR_HARNESS" ] || [ "$PERSONALITY" != "$CUR_PERSONALITY" ]; then
  # Compute new username based on harness
  case "$HARNESS" in
    codex)
      NEW_USERNAME="CodexBot$AGENT_ID"
      MODEL="gpt-5.4"
      ;;
    kimi)
      NEW_USERNAME="KimiBot$AGENT_ID"
      MODEL="kimi-k2"
      ;;
    qwen-code)
      NEW_USERNAME="QwenBot$AGENT_ID"
      MODEL="qwen3-coder"
      ;;
    *)
      # claude or default
      NEW_USERNAME="ClaudeBot$AGENT_ID"
      MODEL="sonnet"
      ;;
  esac

  echo "Updating metadata..."
  python3 -c "
import json
meta = json.load(open('$METADATA')) if True else {}
meta['harness'] = '$HARNESS'
meta['model'] = '$MODEL'
meta['personality'] = '$PERSONALITY'
meta['mode'] = '$PERSONALITY'
meta['username'] = '$NEW_USERNAME'
json.dump(meta, open('$METADATA', 'w'))
print(f'  harness={meta[\"harness\"]}, personality={meta[\"personality\"]}, username={meta[\"username\"]}')
"
fi

# ── Step 3: Reset state if requested ──
if [ "$RESET" = true ]; then
  echo "Resetting sandbox state..."
  STATE_DIR="$SANDBOX/state"
  rm -f "$STATE_DIR/screenshot.png" \
        "$STATE_DIR/live_screen.png" \
        "$STATE_DIR/game_state.json" \
        "$STATE_DIR/progress.json" \
        "$STATE_DIR/.session_counter"
  find "$STATE_DIR" -name "*.png" -delete 2>/dev/null || true

  # Reset MongoDB player data
  MONGO_CONTAINER="kaetram-mongo"
  MONGO_DB="kaetram_devlopment"
  COLLECTIONS=(player_info player_skills player_equipment player_inventory player_bank player_quests player_achievements player_statistics player_abilities)

  # Determine which usernames to clear (all possible bot types for this agent ID)
  USERNAMES="'claudebot${AGENT_ID}','codexbot${AGENT_ID}','kimibot${AGENT_ID}','qwencodebot${AGENT_ID}'"

  if docker ps --format '{{.Names}}' | grep -q "^${MONGO_CONTAINER}$"; then
    echo "Resetting MongoDB for agent $AGENT_ID..."
    for coll in "${COLLECTIONS[@]}"; do
      result=$(docker exec "$MONGO_CONTAINER" mongosh "$MONGO_DB" --quiet --eval '
        var r = db.'"$coll"'.deleteMany({username: {$in: ['"$USERNAMES"']}});
        print(r.deletedCount);
      ' 2>/dev/null)
      [ "$result" != "0" ] && echo "  ${coll}: deleted ${result}"
    done
    echo "  Player will start fresh on login."
  else
    echo "  WARNING: MongoDB container not running — skipping DB reset"
  fi
fi

echo ""
echo "=== Agent $AGENT_ID killed — orchestrator will auto-restart it ==="
echo "  Monitor: tail -f /tmp/orchestrate.log"
