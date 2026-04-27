#!/usr/bin/env bash
# collect_sft_data.sh — End-to-end SFT data collection pipeline
#
# Usage:
#   ./scripts/collect_sft_data.sh [N_AGENTS] [HOURS]
#   ./scripts/collect_sft_data.sh 3 8        # 3 agents for 8 hours
#   ./scripts/collect_sft_data.sh 2           # 2 agents, run until ctrl-c
#   ./scripts/collect_sft_data.sh             # defaults: 3 agents, no time limit
#
# Steps:
#   1. Check that the shared Kaetram client is running on port 9000
#   2. Launch orchestrate.py with N agents (each gets its own server)
#   3. On completion, run extract_turns.py on all collected logs
#   4. Run convert_to_qwen.py to produce final SFT dataset
#   5. Print stats

set -euo pipefail

# ── --help / -h guard (auto-injected) ────────────────────────────────────────
for _arg in "$@"; do
  case "$_arg" in
    -h|--help)
      awk 'NR==1{next} /^#/{sub(/^# ?/,""); print; next} {exit}' "$0"
      exit 0
      ;;
  esac
done


PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

# Parse args
N_AGENTS="${1:-3}"
HOURS="${2:-}"
N_CLAUDE=""
N_CODEX=""
for arg in "$@"; do
  case "$arg" in
    --codex) N_CODEX="-1";;
    --claude) N_CLAUDE="-1";;
  esac
done
# Also parse --claude N / --codex N with values
PREV=""
for arg in "$@"; do
  if [ "$PREV" = "--claude" ] && [[ "$arg" =~ ^[0-9]+$ ]]; then
    N_CLAUDE="$arg"
  elif [ "$PREV" = "--codex" ] && [[ "$arg" =~ ^[0-9]+$ ]]; then
    N_CODEX="$arg"
  fi
  PREV="$arg"
done

echo "=== Kaetram SFT Data Collection Pipeline ==="
echo "  Agents: $N_AGENTS"
echo "  Hours: ${HOURS:-unlimited}"
echo "  SFT corpus: agent_0, agent_1, agent_2 only"
if [ -n "$N_CLAUDE" ] && [ -n "$N_CODEX" ]; then
  echo "  CLI: Mixed (Claude + Codex)"
elif [ -n "$N_CODEX" ]; then
  echo "  CLI: Codex"
else
  echo "  CLI: Claude Code"
fi
echo ""

# Step 1: Check shared client on port 9000
echo "--- Step 1: Checking Kaetram client on port 9000 ---"
if curl -s --max-time 2 http://localhost:9000 >/dev/null 2>&1; then
  echo "  Client is running on port 9000."
else
  echo "  WARNING: Kaetram client not detected on port 9000."
  echo "  The game client serves static assets. Agents will try localhost:9000."
  echo "  Start it with: ./scripts/start-kaetram.sh"
  echo ""
  read -p "  Continue anyway? [y/N] " -n 1 -r
  echo ""
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 1
  fi
fi
echo ""

# Step 2: Run orchestrator
echo "--- Step 2: Running orchestrator ($N_AGENTS agents) ---"
ORCH_ARGS="--agents $N_AGENTS"
if [ -n "$HOURS" ]; then
  ORCH_ARGS="$ORCH_ARGS --hours $HOURS"
fi
[ -n "$N_CLAUDE" ] && ORCH_ARGS="$ORCH_ARGS --claude $N_CLAUDE"
[ -n "$N_CODEX" ] && ORCH_ARGS="$ORCH_ARGS --codex $N_CODEX"

python3 orchestrate.py $ORCH_ARGS
echo ""

# Step 3: Extract turns from all collected logs
echo "--- Step 3: Extracting turns from session logs ---"
RAW_DIR="$PROJECT_DIR/dataset/raw"
EXTRACTED_DIR="$PROJECT_DIR/dataset/extracted"
PYTHON_BIN="$PROJECT_DIR/.venv/bin/python3"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="python3"
fi

if [ -d "$RAW_DIR" ]; then
  for agent_dir in "$RAW_DIR"/agent_{0,1,2}/logs; do
    if [ -d "$agent_dir" ]; then
      agent_name="$(basename "$(dirname "$agent_dir")")"
      agent_output_dir="$EXTRACTED_DIR/$agent_name"
      echo "  Processing $agent_dir -> $agent_output_dir ..."
      "$PYTHON_BIN" extract_turns.py --log-dir "$agent_dir" --output-dir "$agent_output_dir"
    fi
  done
fi

echo ""

# Step 4: Convert to Qwen3.5 SFT format
echo "--- Step 4: Converting to Qwen3.5 9B SFT format ---"
"$PYTHON_BIN" convert_to_qwen.py --input "$EXTRACTED_DIR" --output "$PROJECT_DIR/dataset/qwen_sft"
echo ""

# Step 5: Print stats
echo "--- Step 5: Dataset Summary ---"
TRAIN="$PROJECT_DIR/dataset/qwen_sft/train.json"
VAL="$PROJECT_DIR/dataset/qwen_sft/val.json"

if [ -f "$TRAIN" ]; then
  TRAIN_COUNT=$("$PYTHON_BIN" -c "import json; print(len(json.load(open('$TRAIN'))))")
  VAL_COUNT=$("$PYTHON_BIN" -c "import json; print(len(json.load(open('$VAL'))))")
  echo "  Train examples: $TRAIN_COUNT"
  echo "  Val examples:   $VAL_COUNT"
  echo "  Total:          $((TRAIN_COUNT + VAL_COUNT))"
  echo "  Output:         $PROJECT_DIR/dataset/qwen_sft/"
else
  echo "  No dataset produced. Check logs for errors."
fi

echo ""
echo "=== Pipeline complete ==="
