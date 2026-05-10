#!/usr/bin/env python3
"""
play_qwen.py — One Qwen session subprocess. Spawns mcp_game_server.py over
stdio and drives the gameplay loop until context approaches the 16K trained
budget, then exits cleanly so orchestrate can respawn the next session.

Multi-agent runs go through orchestrate.py --qwen-sft / --qwen-base (each
spawns this file via QwenAdapter pointed at the SFT or base Modal endpoint);
solo dev runs invoke directly.

Usage (solo dev):
    python3 play_qwen.py --endpoint https://your-modal-url/v1 \
        --system-prompt prompts/system.md \
        --sandbox /tmp/kaetram_agent_4 \
        --personality completionist --session-n 1
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

from tool_surface import MODEL_VISIBLE_TOOL_NAMES

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
            if name not in MODEL_VISIBLE_TOOL_NAMES:
                continue
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
        """Return the curated model-visible tool list."""
        return [n for n in self._tools if n in MODEL_VISIBLE_TOOL_NAMES]


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# JSONL records → stdout. orchestrate.py captures stdout into the session
# log file under dataset/raw/agent_N/runs/run_*/. Informational prints go
# to stderr so they never pollute the log. For solo dev (no orchestrate),
# pipe stdout to a file: `python3 play_qwen.py ... > my.log`.

_THINK_RE = re.compile(r"<think>(.*?)</think>\s*(.*)", flags=re.DOTALL)


def _split_thinking(content: str) -> tuple[str | None, str | None]:
    """Return (thinking, remaining_text). Either may be None.

    Qwen3.5 emits a single inline `<think>...</think>` block at the start of
    its message; split it out so downstream parsers see the same
    one-block-per-record shape Claude produces (thinking | text | tool_use).
    """
    if not content:
        return None, None
    m = _THINK_RE.match(content)
    if m:
        thinking = (m.group(1) or "").strip() or None
        text = (m.group(2) or "").strip() or None
        return thinking, text
    return None, content.strip() or None


def _map_usage(openai_usage: dict | None) -> dict:
    """Map OpenAI's prompt_tokens/completion_tokens to Anthropic-shaped
    input_tokens/output_tokens so cost tracking + log_analysis read the same
    keys as Claude logs."""
    if not openai_usage:
        return {}
    return {
        "input_tokens": openai_usage.get("prompt_tokens", 0),
        "output_tokens": openai_usage.get("completion_tokens", 0),
    }


def _emit(rec: dict) -> None:
    print(json.dumps(rec), flush=True)


def log_system_init(
    personality: str,
    session_n: int,
    model: str,
    endpoint: str,
    tools: list[str],
) -> None:
    """Emit a Claude-shaped {type:'system', subtype:'init'} record.

    Required by `cli_adapter.detect_log_format` (returns 'claude') and read by
    `parse_session_claude` for `init_info` (model/session_id/tools)."""
    _emit({
        "type": "system",
        "subtype": "init",
        "session_id": f"qwen-s{session_n}",
        "model": model,
        "tools": tools,
        "mcp_servers": [],
        "harness": "qwen",
        "personality": personality,
        "session_n": session_n,
        "endpoint": endpoint,
        "timestamp": datetime.now().isoformat(),
    })


def log_assistant(
    turn: int,
    content: str,
    parsed_calls: list[dict] | None,
    usage: dict | None,
) -> None:
    """Emit one Claude-shaped {type:'assistant'} record per content block.

    Mirrors the on-disk Claude shape: each record holds exactly ONE block
    (thinking | text | tool_use). `parse_session_claude` tracks the most
    recent thinking/text and pairs them with the next tool_use, so we emit in
    that order.

    Token usage is stamped on the LAST assistant record in this turn so
    per-turn cost math reads it exactly once.
    """
    timestamp = datetime.now().isoformat()
    blocks: list[dict] = []
    thinking, text = _split_thinking(content or "")
    if thinking:
        blocks.append({"type": "thinking", "thinking": thinking})
    if text:
        blocks.append({"type": "text", "text": text})
    for parsed in parsed_calls or []:
        blocks.append({
            "type": "tool_use",
            "id": parsed.get("id", ""),
            "name": parsed.get("name", ""),
            "input": parsed.get("args", {}) or {},
        })
    if not blocks:
        return
    mapped_usage = _map_usage(usage)
    for i, blk in enumerate(blocks):
        msg: dict = {"role": "assistant", "content": [blk]}
        if mapped_usage and i == len(blocks) - 1:
            msg["usage"] = mapped_usage
        _emit({
            "type": "assistant",
            "turn": turn,
            "timestamp": timestamp,
            "message": msg,
        })


def log_tool_result(turn: int, tool_use_id: str, name: str, result: str) -> None:
    """Emit a Claude-shaped {type:'user'} record carrying the tool_result."""
    _emit({
        "type": "user",
        "turn": turn,
        "timestamp": datetime.now().isoformat(),
        "message": {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": result,
            }],
        },
        "tool_name": name,
    })


def log_session_end(turn: int, reason: str) -> None:
    """Emit a Claude-shaped {type:'result'} record so `parse_session_claude`
    populates `result_summary` (num_turns, is_error, terminal_reason)."""
    _emit({
        "type": "result",
        "subtype": "session_end",
        "num_turns": turn,
        "result": reason,
        "terminal_reason": reason,
        "is_error": reason in ("api_errors",),
        "timestamp": datetime.now().isoformat(),
    })


def info(msg: str):
    """Informational status line → stderr (keeps stdout clean for log capture)."""
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Context-budget gate
# ---------------------------------------------------------------------------
# Qwen3.5-9B was trained at max_seq_len=16,384. A "session" is bounded by
# context, not turn count: when the next call would push past CONTEXT_BUDGET,
# play_qwen.py exits cleanly with a session_end marker and orchestrate
# respawns with --session-n N+1 (fresh bootstrap, fresh observe).
#
# CONTEXT_BUDGET = 14,336 leaves room for the 2,048-token response.
MAX_SEQ_LEN = 16_384
RESPONSE_BUDGET = 2_048
CONTEXT_BUDGET = MAX_SEQ_LEN - RESPONSE_BUDGET

MODEL_ID_FOR_TOKENIZER = "unsloth/Qwen3.5-9B"
_TOKENIZER: object | None = None


def _get_tokenizer():
    """Lazy-load the Qwen tokenizer once per process (no torch needed)."""
    global _TOKENIZER
    if _TOKENIZER is None:
        from transformers import AutoTokenizer
        _TOKENIZER = AutoTokenizer.from_pretrained(MODEL_ID_FOR_TOKENIZER)
    return _TOKENIZER


def _projected_tokens(messages: list) -> int:
    """Token count of `messages` rendered with add_generation_prompt=True
    (the same shape SGLang will see). transformers v5 returns BatchEncoding
    from apply_chat_template(tokenize=True); pull .input_ids directly."""
    out = _get_tokenizer().apply_chat_template(
        messages, tokenize=True, add_generation_prompt=True
    )
    ids = getattr(out, "input_ids", None)
    if ids is None and isinstance(out, dict):
        ids = out.get("input_ids", [])
    if ids is None:
        ids = out
    if ids and isinstance(ids[0], list):
        ids = ids[0]
    return len(ids)


# ---------------------------------------------------------------------------
# Sampling parameters — Qwen3.5 thinking-mode recommended values.
# Pinned client-side so server-side defaults never silently win.
# Source: Qwen3.5 model card (https://huggingface.co/unsloth/Qwen3.5-9B).
# ---------------------------------------------------------------------------
QWEN_THINK_TEMPERATURE = 1.0
QWEN_THINK_TOP_P = 0.95
QWEN_THINK_TOP_K = 20
QWEN_THINK_PRESENCE_PENALTY = 1.5


async def run_agent(args):
    sandbox = Path(args.sandbox)
    state_dir = sandbox / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    mcp = None  # ensure cleanup can reference it

    # Init OpenAI client (Modal SGLang endpoint)
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
        "KAETRAM_USERNAME": os.environ.get("KAETRAM_USERNAME", "QwenCompletionist"),
        "KAETRAM_EXTRACTOR": os.path.join(project_dir, "state_extractor.js"),
        "KAETRAM_STATE_DIR": str(state_dir),
    }

    mcp = MCPClient(venv_python, server_script, mcp_env)
    info(f"Connecting to MCP game server...")
    tool_names = await mcp.connect()
    info(f"MCP connected. Tools: {tool_names}")

    # Load system prompt
    system_prompt = ""
    if args.system_prompt and os.path.isfile(args.system_prompt):
        system_prompt = open(args.system_prompt).read()
    elif args.system_prompt:
        system_prompt = args.system_prompt

    # Bootstrap mirrors orchestrate.py byte-for-byte via shared module.
    from bootstrap import build_orchestrate_bootstrap
    bootstrap_personality = None if args.personality == "none" else args.personality
    bootstrap_text = build_orchestrate_bootstrap(bootstrap_personality, args.session_n)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": bootstrap_text},
    ]

    info(f"Harness started: max_turns={args.max_turns}, personality={args.personality}, session={args.session_n}, endpoint={args.endpoint}")
    log_system_init(
        personality=args.personality,
        session_n=args.session_n,
        model=args.model,
        endpoint=args.endpoint,
        tools=tool_names,
    )

    turn = 0
    consecutive_errors = 0
    session_end_reason = "max_turns"

    try:
      while turn < args.max_turns:
        # Pre-call overflow gate: if next call would exceed Qwen's trained
        # context budget, end the session cleanly. orchestrate respawns
        # play_qwen.py with --session-n N+1 (fresh bootstrap, fresh observe).
        projected = _projected_tokens(messages)
        if projected > CONTEXT_BUDGET:
            info(f"  [{turn}] Context budget reached ({projected} > {CONTEXT_BUDGET}); ending session.")
            session_end_reason = "context_overflow"
            break

        turn += 1

        # Sampling params pinned to Qwen3.5 thinking-mode recommendations.
        # top_k + presence_penalty go in extra_body (not OpenAI-spec; SGLang
        # forwards them). No `tools=` — system prompt embeds the tool surface.
        try:
            response = client.chat.completions.create(
                model=args.model,
                messages=messages,
                temperature=QWEN_THINK_TEMPERATURE,
                top_p=QWEN_THINK_TOP_P,
                max_tokens=RESPONSE_BUDGET,
                extra_body={
                    "top_k": QWEN_THINK_TOP_K,
                    "presence_penalty": QWEN_THINK_PRESENCE_PENALTY,
                },
            )
            choice = response.choices[0]
            usage = response.usage.model_dump() if getattr(response, "usage", None) else None
            consecutive_errors = 0
        except Exception as e:
            info(f"  [{turn}] API error: {e}")
            consecutive_errors += 1
            if consecutive_errors > 3:
                info("Too many API errors, stopping.")
                session_end_reason = "api_errors"
                break
            time.sleep(5)
            continue

        content = choice.message.content or ""
        tool_calls = choice.message.tool_calls

        if content:
            display = re.sub(r"<think>.*?</think>", "[think]", content, flags=re.DOTALL)
            info(f"  [{turn}] Assistant: {display[:120]}...")

        # Route 1: structured tool_calls (server parsed XML into tool_calls).
        # Preserve the structured form to match training-time record shape.
        if tool_calls:
            structured_calls = []
            parsed_calls = []
            for tc in tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments) if isinstance(tc.function.arguments, str) else tc.function.arguments
                except json.JSONDecodeError:
                    fn_args = {}
                structured_calls.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": fn_name, "arguments": json.dumps(fn_args)},
                })
                parsed_calls.append({"name": fn_name, "args": fn_args, "id": tc.id})

            messages.append({
                "role": "assistant",
                "content": content,
                "tool_calls": structured_calls,
            })
            log_assistant(turn, content, parsed_calls, usage=usage)

            for parsed in parsed_calls:
                fn_name = parsed["name"]
                fn_args = parsed["args"]
                info(f"  [{turn}] → {fn_name}({fn_args})")
                try:
                    result = await mcp.call_tool(fn_name, fn_args)
                except Exception as e:
                    result = f"Error: {e}"
                info(f"  [{turn}] ← {result[:120]}...")

                # Native role=tool — matches build_tool_result_message in
                # convert_to_qwen.py. SGLang renders as <tool_response>.
                messages.append({
                    "role": "tool",
                    "content": result,
                    "tool_call_id": parsed["id"],
                    "name": fn_name,
                })
                log_tool_result(turn, parsed["id"], fn_name, result)

                # Save game state for dashboard when model calls observe.
                if fn_name == "observe" and "\n\nASCII_MAP:" in result:
                    try:
                        (state_dir / "game_state.json").write_text(result.split("\n\nASCII_MAP:")[0])
                    except Exception:
                        pass

        elif content:
            # Text-only response (no tool call). Log and continue — model
            # is trained to emit structured tool_calls; divergence is a
            # serving-side bug, not papered over here.
            messages.append({"role": "assistant", "content": content})
            log_assistant(turn, content, None, usage=usage)
            if choice.finish_reason == "stop":
                info(f"  [{turn}] Model stopped (no tool call). Continuing...")
                time.sleep(2)

      info(f"\nSession complete: {turn} turns, reason={session_end_reason}")
      log_session_end(turn, session_end_reason)
    except KeyboardInterrupt:
        info(f"\nInterrupted after {turn} turns, cleaning up...")
        log_session_end(turn, "interrupted")
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
    parser.add_argument("--sandbox", default="/tmp/kaetram_agent_4", help="Sandbox directory")
    parser.add_argument("--max-turns", type=int, default=300, help="Max conversation turns")
    parser.add_argument("--server-port", default="", help="Game server WebSocket port (e.g. 9031)")
    parser.add_argument("--project-dir", default=os.path.dirname(os.path.abspath(__file__)),
                        help="Project directory (for mcp_game_server.py)")
    parser.add_argument("--personality", default="completionist",
                        choices=["grinder", "completionist", "explorer_tinkerer", "none"],
                        help="Personality block (must match training); shell handles substitution.")
    parser.add_argument("--session-n", type=int, default=1,
                        help="Session number for the orchestrate bootstrap "
                             "(`Session #N`). Mirrors orchestrate.py session counter.")
    args = parser.parse_args()
    asyncio.run(run_agent(args))


if __name__ == "__main__":
    main()
