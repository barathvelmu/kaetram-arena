#!/usr/bin/env bash
# Stop the Qwen agent cleanly. Preserves logs and game progress.
set -euo pipefail

echo "Stopping Qwen agent..."

# Kill play_qwen processes
pkill -f "play_qwen.py" 2>/dev/null || true
pkill -f "play_qwen.sh" 2>/dev/null || true

# Kill its MCP server + browser
pkill -f "mcp_game_server.py" 2>/dev/null || true
pkill -9 -f "chromium" 2>/dev/null || true

# Kill tmux session
tmux kill-session -t qwen 2>/dev/null || true

sleep 2

# Report
LOGS=$(ls /tmp/kaetram_agent_4/logs/*.log 2>/dev/null | wc -l)
echo "Stopped. $LOGS session log(s) preserved in /tmp/kaetram_agent_4/logs/"
echo ""
echo "Restart: ./scripts/start-qwen.sh"
echo "Reset:   ./scripts/start-qwen.sh --reset"
