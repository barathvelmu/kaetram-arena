#!/usr/bin/env bash
# Stop all Qwen agents (agent_4=finetuned, agent_5=base) cleanly.
# Does NOT touch Claude/Codex/Gemini agents (agent_0-3) or their game servers.
# Preserves logs and game progress.
set -euo pipefail

echo "Stopping Qwen agents (agent_4 + agent_5 only)..."

# Kill play_qwen processes (only affects Qwen harness, not Claude's play.sh)
pkill -f "play_qwen.py" 2>/dev/null || true
pkill -f "play_qwen.sh" 2>/dev/null || true

# Kill MCP servers spawned by Qwen agents + their browsers
# NOTE: This kills ALL mcp_game_server.py instances. If Claude agents are running
# concurrently, their MCP servers will also die. Use nuke-agents.sh for full cleanup.
pkill -f "mcp_game_server.py" 2>/dev/null || true
pkill -9 -f "chromium" 2>/dev/null || true

# Kill Qwen tmux sessions only (not datacol which is Claude's)
tmux kill-session -t qwen-4 2>/dev/null || true
tmux kill-session -t qwen-5 2>/dev/null || true
tmux kill-session -t qwen 2>/dev/null || true

# Kill base game server on port 9041 (preserve main server on 9001 for Claude agents)
BASE_PID=$(ss -tlnp 2>/dev/null | grep ":9041 " | grep -oP 'pid=\K[0-9]+' | head -1)
if [ -n "$BASE_PID" ]; then
  kill "$BASE_PID" 2>/dev/null || true
  echo "  Stopped base game server (port 9041, pid $BASE_PID)"
fi

sleep 2

# Report
FT_LOGS=$(find /tmp/kaetram_agent_4/logs/ -name "*.log" 2>/dev/null | wc -l)
BASE_LOGS=$(find /tmp/kaetram_agent_5/logs/ -name "*.log" 2>/dev/null | wc -l)
echo "Stopped."
[ "$FT_LOGS" -gt 0 ] && echo "  r8-SFT:  $FT_LOGS session log(s) in /tmp/kaetram_agent_4/logs/"
[ "$BASE_LOGS" -gt 0 ] && echo "  Base:    $BASE_LOGS session log(s) in /tmp/kaetram_agent_5/logs/"
echo ""
echo "Restart: ./scripts/start-qwen.sh"
echo "Reset:   ./scripts/start-qwen.sh --reset"
