#!/usr/bin/env bash
# Stop all Qwen agents (agent_4=finetuned, agent_5=base) cleanly.
# Does NOT touch Claude/Codex/Gemini agents (agent_0-3) or their game servers.
# Preserves logs and game progress.
#
# Modeled after nuke-agents.sh but scoped to Qwen only.
set -euo pipefail

echo "Stopping Qwen agents (agent_4 + agent_5 only)..."

# 1. Kill play_qwen harness processes
pkill -f "play_qwen.py" 2>/dev/null || true
pkill -f "play_qwen.sh" 2>/dev/null || true

# 2. Kill eval harness (if running)
pkill -f "eval_harness.py" 2>/dev/null || true

# 3. Kill MCP game servers spawned by Qwen agents
#    These are Python mcp_game_server.py processes — same as what nuke-agents kills.
#    NOTE: If Claude agents share the same mcp_game_server.py, this kills theirs too.
pkill -f "mcp_game_server.py" 2>/dev/null || true

# 4. Kill ALL Playwright processes (driver + browsers) — matches nuke-agents.sh
#    These are spawned by mcp_game_server.py for headless browser control.
pkill -9 -f "playwright/driver" 2>/dev/null || true
pkill -9 -f "chrome-headless-shell" 2>/dev/null || true
pkill -9 -f "chromium" 2>/dev/null || true

# 5. Kill Qwen tmux sessions only (not datacol which is Claude's)
tmux kill-session -t qwen-4 2>/dev/null || true
tmux kill-session -t qwen-5 2>/dev/null || true
tmux kill-session -t qwen 2>/dev/null || true

# 6. Kill game server on port 9041 (base agent's dedicated server)
#    Preserve port 9001 for Claude agents.
BASE_PID=$(ss -tlnp 2>/dev/null | grep ":9041 " | grep -oP 'pid=\K[0-9]+' | head -1 || true)
if [ -n "$BASE_PID" ]; then
  kill "$BASE_PID" 2>/dev/null || true
  echo "  Stopped base game server (port 9041, pid $BASE_PID)"
fi

sleep 2

# 7. Verify no orphans remain
ORPHANS=0
pgrep -f "play_qwen" > /dev/null 2>&1 && { echo "  WARNING: play_qwen still alive, sending SIGKILL"; pkill -9 -f "play_qwen" 2>/dev/null || true; ORPHANS=1; }
pgrep -f "chrome-headless-shell" > /dev/null 2>&1 && { echo "  WARNING: chrome-headless-shell still alive, sending SIGKILL"; pkill -9 -f "chrome-headless-shell" 2>/dev/null || true; ORPHANS=1; }
pgrep -f "playwright/driver" > /dev/null 2>&1 && { echo "  WARNING: playwright/driver still alive, sending SIGKILL"; pkill -9 -f "playwright/driver" 2>/dev/null || true; ORPHANS=1; }
[ "$ORPHANS" -eq 1 ] && sleep 1

# Report
FT_LOGS=$(find /tmp/kaetram_agent_4/logs/ -name "*.log" 2>/dev/null | wc -l)
BASE_LOGS=$(find /tmp/kaetram_agent_5/logs/ -name "*.log" 2>/dev/null | wc -l)
echo "Stopped."
[ "$FT_LOGS" -gt 0 ] && echo "  r8-SFT:  $FT_LOGS session log(s) in /tmp/kaetram_agent_4/logs/"
[ "$BASE_LOGS" -gt 0 ] && echo "  Base:    $BASE_LOGS session log(s) in /tmp/kaetram_agent_5/logs/"
echo ""
echo "Restart: ./scripts/start-qwen.sh"
echo "Reset:   ./scripts/start-qwen.sh --reset"
