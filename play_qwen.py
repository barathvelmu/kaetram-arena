#!/usr/bin/env python3
"""
play_qwen.py — Lightweight harness for finetuned Qwen3.5-9B Kaetram agent.

Spawns mcp_game_server.py as an MCP subprocess and forwards the model's tool
calls directly to it.  No JS translation layer — the MCP server handles all
browser interaction, combat timers, navigation, etc.

Usage:
    python3 play_qwen.py --endpoint https://your-modal-url/v1 \
        --system-prompt /path/to/system.md \
        --sandbox /tmp/kaetram_agent_4 \
        --username QwenBot
"""

import argparse
import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from openai import OpenAI

# ---------------------------------------------------------------------------
# MCP client — spawns mcp_game_server.py and calls tools over stdio
# ---------------------------------------------------------------------------

class MCPClient:
    """Minimal MCP client that spawns the game server and calls tools."""

    def __init__(self, venv_python: str, server_script: str, env: dict):
        self.venv_python = venv_python
        self.server_script = server_script
        self.env = {**os.environ, **env}
        self._session = None
        self._client = None
        self._tools = {}  # name -> {description, inputSchema}

    async def connect(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=self.venv_python,
            args=[self.server_script],
            env=self.env,
        )
        self._transport = stdio_client(params)
        self._streams = await self._transport.__aenter__()
        read_stream, write_stream = self._streams
        from datetime import timedelta
        self._session = ClientSession(read_stream, write_stream, read_timeout_seconds=timedelta(seconds=120))
        await self._session.__aenter__()
        await self._session.initialize()

        # Discover tools
        result = await self._session.list_tools()
        for tool in result.tools:
            self._tools[tool.name] = {
                "description": tool.description or "",
                "inputSchema": tool.inputSchema or {"type": "object", "properties": {}},
            }
        return list(self._tools.keys())

    async def call_tool(self, name: str, arguments: dict) -> str:
        """Call an MCP tool and return the text result."""
        if not self._session:
            raise RuntimeError("MCP client not connected")
        result = await self._session.call_tool(name, arguments)
        # Concatenate text content from result
        parts = []
        for block in result.content:
            if hasattr(block, "text"):
                parts.append(block.text)
            else:
                parts.append(str(block))
        return "\n".join(parts)

    async def close(self):
        if self._session:
            await self._session.__aexit__(None, None, None)
        if hasattr(self, "_transport"):
            await self._transport.__aexit__(None, None, None)

    def get_tool_definitions(self) -> list[dict]:
        """Return OpenAI-format tool definitions for the chat API."""
        defs = []
        for name, info in self._tools.items():
            if name == "login":
                continue  # login is called internally, not by the model
            defs.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": info["description"],
                    "parameters": info["inputSchema"],
                },
            })
        return defs

    def get_tool_names(self) -> list[str]:
        """Return list of tool names (excluding login)."""
        return [n for n in self._tools if n != "login"]


# ---------------------------------------------------------------------------
# Tool call parsing (handles Qwen3.5 Coder XML format + JSON fallback)
# ---------------------------------------------------------------------------

def parse_tool_calls_from_text(text: str) -> list[dict]:
    """Parse tool calls from model text output.

    Handles multiple formats:
    1. Qwen3.5 Coder XML: <tool_call><function=name><parameter=key>value</parameter></function></tool_call>
    2. JSON in <tool_call> tags: <tool_call>{"name": "...", "arguments": {...}}</tool_call>
    3. ✿TOOL_CALL✿ format
    """
    calls = []

    # Pattern 1: Qwen3.5 Coder XML format
    for m in re.finditer(r"<tool_call>\s*<function=(\w+)>(.*?)</function>\s*</tool_call>", text, re.DOTALL):
        fn_name = m.group(1)
        params_text = m.group(2)
        args = {}
        for pm in re.finditer(r"<parameter=(\w+)>\s*(.*?)\s*</parameter>", params_text, re.DOTALL):
            key = pm.group(1)
            val = pm.group(2).strip()
            # Try to parse as number
            try:
                args[key] = int(val)
            except ValueError:
                try:
                    args[key] = float(val)
                except ValueError:
                    args[key] = val
        calls.append({"name": fn_name, "arguments": args})

    # Pattern 2: JSON inside <tool_call> tags
    if not calls:
        for m in re.finditer(r"<tool_call>\s*(.*?)\s*</tool_call>", text, re.DOTALL):
            try:
                tc = json.loads(m.group(1))
                calls.append(tc)
            except json.JSONDecodeError:
                pass

    # Pattern 3: ✿TOOL_CALL✿ format
    if not calls:
        for m in re.finditer(r"✿TOOL_CALL✿\s*(.*?)(?=✿|$)", text, re.DOTALL):
            try:
                tc = json.loads(m.group(1).strip())
                calls.append(tc)
            except json.JSONDecodeError:
                pass

    return calls


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log_turn(log_file, turn: int, role: str, content: str, tool_calls=None, usage=None):
    """Append a turn record to the session log."""
    # Tool results need full content — observe returns player_stats, quests, entities
    # which are critical for eval metrics. No truncation on tool results.
    max_len = 500 if role == "assistant" else 0
    record = {
        "turn": turn,
        "timestamp": datetime.now().isoformat(),
        "role": role,
        "content": (content[:max_len] if max_len else content) if content else "",
    }
    if tool_calls:
        record["tool_calls"] = tool_calls
    if usage:
        record["usage"] = usage
    with open(log_file, "a") as f:
        f.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------------------
# Main agent loop
# ---------------------------------------------------------------------------

async def run_agent(args):
    sandbox = Path(args.sandbox)
    state_dir = sandbox / "state"
    log_dir = sandbox / "logs"
    state_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    mcp = None  # ensure cleanup can reference it

    # Session log
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"session_{timestamp}.log"

    # Init OpenAI client (for the finetuned model endpoint)
    client = OpenAI(base_url=args.endpoint, api_key=args.api_key or "not-needed", timeout=300)

    # Spawn MCP game server
    project_dir = args.project_dir
    venv_python = os.path.join(project_dir, ".venv", "bin", "python3")
    server_script = os.path.join(project_dir, "mcp_game_server.py")

    # Register signal handlers so cleanup runs on SIGTERM/SIGINT
    def _signal_handler(sig, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    mcp_env = {
        "KAETRAM_PORT": args.server_port or "",
        "KAETRAM_USERNAME": os.environ.get("KAETRAM_USERNAME", "QwenBot"),
        "KAETRAM_EXTRACTOR": os.path.join(project_dir, "state_extractor.js"),
        "KAETRAM_SCREENSHOT_DIR": str(state_dir),
    }

    mcp = MCPClient(venv_python, server_script, mcp_env)
    print(f"Connecting to MCP game server...")
    tool_names = await mcp.connect()
    print(f"MCP connected. Tools: {tool_names}")

    # Login via MCP server
    print(f"Logging in via MCP server...")
    login_result = await mcp.call_tool("login", {})
    print(f"Login: {login_result[:200]}")

    if "FAILED" in login_result.upper() or "ERROR" in login_result.upper():
        print(f"Login failed, aborting.")
        await mcp.close()
        return

    # Load system prompt
    system_prompt = ""
    if args.system_prompt and os.path.isfile(args.system_prompt):
        system_prompt = open(args.system_prompt).read()
    elif args.system_prompt:
        system_prompt = args.system_prompt

    # Build initial messages
    messages = [{"role": "system", "content": system_prompt}]
    if args.user_prompt:
        messages.append({"role": "user", "content": args.user_prompt})
    else:
        messages.append({"role": "user", "content": "You are logged in. Start playing now. Call observe() first to see the game state."})

    print(f"Harness started: {args.max_turns} max turns, endpoint={args.endpoint}")
    print(f"Log: {log_file}")

    turn = 0
    consecutive_errors = 0

    try:
      while turn < args.max_turns:
        turn += 1

        # Call model
        try:
            response = client.chat.completions.create(
                model=args.model,
                messages=messages,
                tools=mcp.get_tool_definitions(),
                temperature=0.7,
                max_tokens=2048,
            )
            choice = response.choices[0]
            usage = response.usage.model_dump() if getattr(response, "usage", None) else None
            consecutive_errors = 0
        except Exception as e:
            print(f"  [{turn}] API error: {e}")
            consecutive_errors += 1
            if consecutive_errors > 3:
                print("Too many API errors, stopping.")
                break
            time.sleep(5)
            continue

        content = choice.message.content or ""
        tool_calls = choice.message.tool_calls

        if content:
            # Strip thinking for display
            display = re.sub(r"<think>.*?</think>", "[think]", content, flags=re.DOTALL)
            print(f"  [{turn}] Assistant: {display[:120]}...")

        # Route 1: Structured tool_calls from API (server parsed XML into tool_calls)
        if tool_calls:
            # Append assistant content as plain text (it contains the <tool_call> XML)
            messages.append({"role": "assistant", "content": content})
            parsed_calls = []
            for tc in tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments) if isinstance(tc.function.arguments, str) else tc.function.arguments
                except json.JSONDecodeError:
                    fn_args = {}
                parsed_calls.append({"name": fn_name, "args": fn_args})

            log_turn(log_file, turn, "assistant", content, parsed_calls, usage=usage)

            for parsed in parsed_calls:
                fn_name = parsed["name"]
                fn_args = parsed["args"]
                print(f"  [{turn}] → {fn_name}({fn_args})")
                try:
                    result = await mcp.call_tool(fn_name, fn_args)
                except Exception as e:
                    result = f"Error: {e}"
                print(f"  [{turn}] ← {result[:120]}...")

                # Tool result as user message (apply_chat_template renders tool_response under user)
                messages.append({"role": "user", "content": f"<tool_response>\n{result}\n</tool_response>"})
                log_turn(log_file, turn, "tool", f"{fn_name}: {result}")

                # Save game state for dashboard when model calls observe
                if fn_name == "observe" and "\n\nASCII_MAP:" in result:
                    try:
                        (state_dir / "game_state.json").write_text(result.split("\n\nASCII_MAP:")[0])
                    except Exception:
                        pass

        # Route 2: Text-based tool calls (model emitted XML/JSON in content)
        elif content:
            text_calls = parse_tool_calls_from_text(content)
            if text_calls:
                messages.append({"role": "assistant", "content": content})
                normalized_calls = []
                for tc_dict in text_calls:
                    fn_name = tc_dict.get("name", "")
                    fn_args = tc_dict.get("arguments", {})
                    if isinstance(fn_args, str):
                        try:
                            fn_args = json.loads(fn_args)
                        except json.JSONDecodeError:
                            fn_args = {}
                    normalized_calls.append({"name": fn_name, "args": fn_args})

                log_turn(log_file, turn, "assistant", content, normalized_calls, usage=usage)

                for normalized in normalized_calls:
                    fn_name = normalized["name"]
                    fn_args = normalized["args"]
                    print(f"  [{turn}] → {fn_name}({fn_args}) [text-parsed]")
                    try:
                        result = await mcp.call_tool(fn_name, fn_args)
                    except Exception as e:
                        result = f"Error: {e}"
                    print(f"  [{turn}] ← {result[:120]}...")

                    messages.append({"role": "user", "content": f"Tool result ({fn_name}):\n{result}"})
                    log_turn(log_file, turn, "tool", f"{fn_name}: {result}")

                    # Save game state for dashboard when model calls observe
                    if fn_name == "observe" and "\n\nASCII_MAP:" in result:
                        try:
                            (state_dir / "game_state.json").write_text(result.split("\n\nASCII_MAP:")[0])
                        except Exception:
                            pass
            else:
                # Pure text, no tool calls
                messages.append({"role": "assistant", "content": content})
                log_turn(log_file, turn, "assistant", content, usage=usage)
                if choice.finish_reason == "stop":
                    print(f"  [{turn}] Model stopped (no tool call). Continuing...")
                    time.sleep(2)

        # Game state is saved to dashboard when the model calls observe()
        # (see _save_game_state helper called from tool dispatch above)

        # Context window management: trim old messages if too many
        if len(messages) > 60:
            target_keep = 40
            cut_idx = len(messages) - target_keep
            while cut_idx < len(messages) - 10:
                role = messages[cut_idx].get("role", "")
                if role in ("user", "system"):
                    break
                if role == "assistant" and "tool_call_id" not in messages[cut_idx]:
                    break
                cut_idx += 1
            messages = messages[:1] + messages[cut_idx:]

      print(f"\nSession complete: {turn} turns, log: {log_file}")
    except KeyboardInterrupt:
        print(f"\nInterrupted after {turn} turns, cleaning up...")
    finally:
        if mcp:
            await mcp.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Kaetram Qwen agent harness")
    parser.add_argument("--endpoint", required=True, help="OpenAI-compatible API base URL")
    parser.add_argument("--model", default="kaetram", help="Model name")
    parser.add_argument("--api-key", default=None, help="API key (default: not-needed)")
    parser.add_argument("--system-prompt", default=None, help="System prompt file or text")
    parser.add_argument("--user-prompt", default=None, help="Initial user message")
    parser.add_argument("--sandbox", default="/tmp/kaetram_agent_4", help="Sandbox directory")
    parser.add_argument("--max-turns", type=int, default=300, help="Max conversation turns")
    parser.add_argument("--server-port", default="", help="Game server WebSocket port (e.g. 9031)")
    parser.add_argument("--project-dir", default=os.path.dirname(os.path.abspath(__file__)),
                        help="Project directory (for mcp_game_server.py)")
    args = parser.parse_args()
    asyncio.run(run_agent(args))


if __name__ == "__main__":
    main()
