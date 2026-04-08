#!/usr/bin/env python3
"""
cli_adapter.py — Abstraction layer for different AI CLI tools (Claude Code, Codex).

Provides adapters that encapsulate CLI-specific differences: command construction,
sandbox configuration, environment variables, and log parsing.
"""

import json
import shutil
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
MCP_JSON = PROJECT_DIR / ".mcp.json"

# Disallowed tools for the game agent (prevent filesystem exploration;
# agent should only use mcp__kaetram__* tools).
CLAUDE_DISALLOWED_TOOLS = "Bash Glob Grep Agent Edit WebFetch WebSearch Write Skill Read ToolSearch CronList CronCreate CronDelete NotebookEdit TodoWrite TaskCreate TaskUpdate TaskGet TaskList TaskOutput TaskStop EnterPlanMode ExitPlanMode EnterWorktree ExitWorktree RemoteTrigger"

# Venv python path for MCP server subprocess
VENV_PYTHON = str(PROJECT_DIR / ".venv" / "bin" / "python3")


def _resolve_mcp_template(sandbox_dir: Path, port: str = "", username: str = "ClaudeBot") -> str:
    """Resolve .mcp.json template variables for a given sandbox directory.

    Only includes the 'kaetram' MCP server — other servers (linear, etc.)
    are stripped to avoid startup delays and resource contention.
    """
    import json as _json
    raw = _json.loads(MCP_JSON.read_text())
    # Keep only kaetram server for agent sandboxes
    kaetram_cfg = raw.get("mcpServers", {}).get("kaetram")
    if not kaetram_cfg:
        raise RuntimeError("No 'kaetram' server in .mcp.json template")
    text = _json.dumps({"mcpServers": {"kaetram": kaetram_cfg}}, indent=2)
    screenshot_dir = str(sandbox_dir / "state")
    return (text
            .replace("__VENV_PYTHON__", VENV_PYTHON)
            .replace("__PROJECT_DIR__", str(PROJECT_DIR))
            .replace("__SCREENSHOT_DIR__", screenshot_dir)
            .replace("__SERVER_PORT__", str(port))
            .replace("__USERNAME__", username)
            )


class CLIAdapter(ABC):
    """Base class for AI CLI tool adapters."""

    def __init__(self, model: str):
        self.model = model

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this adapter (e.g. 'claude', 'codex')."""

    @abstractmethod
    def setup_sandbox(self, sandbox_dir: Path, system_prompt: str | None = None,
                      port: str = "", username: str = "ClaudeBot") -> None:
        """Write CLI-specific config files to the agent sandbox.

        Called each session so that dynamic content (like AGENTS.md for Codex)
        gets refreshed. Idempotent — safe to call multiple times.
        """

    @abstractmethod
    def build_command(
        self,
        user_prompt: str,
        system_prompt: str,
        max_turns: int,
        max_budget_usd: float | None = None,
        auth_mode: str = "subscription",
    ) -> list[str]:
        """Build the CLI command to launch an agent session."""

    @abstractmethod
    def get_env(self) -> dict[str, str]:
        """Extra environment variables for the subprocess."""

    def parse_game_state_from_log(self, log_path: Path) -> str | None:
        """Extract the last game state JSON from a session log file.

        Searches for lines containing both 'player_position' and
        'nearby_entities', extracts the game state, and truncates large arrays.
        Returns a compact JSON string or None.
        """
        try:
            size = log_path.stat().st_size
            tail_size = min(size, 1_048_576)  # read last 1MB
            with open(log_path, "rb") as f:
                if size > tail_size:
                    f.seek(size - tail_size)
                data = f.read().decode("utf-8", errors="replace")

            last_state = None
            for line in data.splitlines():
                if "player_position" in line and "nearby_entities" in line:
                    state_text = self._extract_state_text_from_line(line)
                    if state_text:
                        last_state = state_text

            if not last_state:
                return None

            d = json.loads(last_state)
            d["nearby_entities"] = d.get("nearby_entities", [])[:15]
            d["inventory"] = d.get("inventory", [])[:15]
            d["quests"] = d.get("quests", [])[:10]
            d["achievements"] = d.get("achievements", [])[:10]
            return json.dumps(d, separators=(",", ":"))
        except (OSError, json.JSONDecodeError):
            return None

    @abstractmethod
    def _extract_state_text_from_line(self, line: str) -> str | None:
        """Extract game state JSON string from a single log line.

        Each CLI produces different JSON wrappers around tool results.
        Subclasses implement format-specific extraction.
        """


class ClaudeAdapter(CLIAdapter):
    """Adapter for Claude Code CLI (claude -p)."""

    def __init__(self, model: str = "sonnet"):
        super().__init__(model)
        self._mcp_config_path: str | None = None

    @property
    def name(self) -> str:
        return "claude"

    def setup_sandbox(self, sandbox_dir: Path, system_prompt: str | None = None,
                      port: str = "", username: str = "ClaudeBot") -> None:
        mcp_text = _resolve_mcp_template(sandbox_dir, port=port, username=username)
        mcp_path = sandbox_dir / ".mcp.json"
        mcp_path.write_text(mcp_text)
        self._mcp_config_path = str(mcp_path)

    def build_command(
        self,
        user_prompt: str,
        system_prompt: str,
        max_turns: int,
        max_budget_usd: float | None = None,
        auth_mode: str = "subscription",
    ) -> list[str]:
        cmd = [
            "claude",
            "-p",
            user_prompt,
            "--model",
            self.model,
            "--max-turns",
            str(max_turns),
            "--append-system-prompt",
            system_prompt,
            "--dangerously-skip-permissions",
            "--disallowedTools",
            CLAUDE_DISALLOWED_TOOLS,
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        # Use sandbox MCP config (not project-level .mcp.json)
        if self._mcp_config_path:
            cmd.extend(["--mcp-config", self._mcp_config_path, "--strict-mcp-config"])
        if max_budget_usd is not None and auth_mode == "api_key":
            cmd.extend(["--max-budget-usd", str(max_budget_usd)])
        return cmd

    def get_env(self) -> dict[str, str]:
        return {
            "CLAUDECODE": "",
            "MCP_TIMEOUT": "60000",  # 60s timeout for MCP server startup (3 concurrent browser launches)
        }

    def _extract_state_text_from_line(self, line: str) -> str | None:
        """Claude stream-json: state is in message.content[].text."""
        try:
            obj = json.loads(line)
            for block in obj.get("message", {}).get("content", []):
                text = block.get("text", "") if isinstance(block, dict) else ""
                if "player_position" in text and "nearby_entities" in text:
                    return text
        except (json.JSONDecodeError, AttributeError):
            pass
        return None


class CodexAdapter(CLIAdapter):
    """Adapter for OpenAI Codex CLI (codex exec)."""

    def __init__(self, model: str = "gpt-5.4"):
        super().__init__(model)

    @property
    def name(self) -> str:
        return "codex"

    def setup_sandbox(self, sandbox_dir: Path, system_prompt: str | None = None,
                      port: str = "", username: str = "ClaudeBot") -> None:
        # Write system prompt to a file that we'll reference via -c model_instructions_file.
        # AGENTS.md alone is too weak — Codex treats it as "guidance" not strict instructions.
        # model_instructions_file is injected as developer instructions and is respected.
        if system_prompt:
            (sandbox_dir / "AGENTS.md").write_text(system_prompt)
            (sandbox_dir / "system_prompt.md").write_text(system_prompt)

        # Codex requires a git repo — init one if missing
        git_dir = sandbox_dir / ".git"
        if not git_dir.exists():
            subprocess.run(
                ["git", "init", "-q"],
                cwd=str(sandbox_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        # Playwright MCP is configured globally in ~/.codex/config.toml with
        # --headless --isolated flags (each agent gets its own browser instance).

    def build_command(
        self,
        user_prompt: str,
        system_prompt: str,
        max_turns: int,
        max_budget_usd: float | None = None,
        auth_mode: str = "subscription",
    ) -> list[str]:
        # Codex has no --max-turns; use timeout (estimate ~30s per turn)
        timeout_seconds = max(max_turns * 30, 600)
        return [
            "timeout",
            str(timeout_seconds),
            "codex",
            "exec",
            user_prompt,
            "--model",
            self.model,
            "--dangerously-bypass-approvals-and-sandbox",
            "--json",
            # Inject system prompt as developer instructions (stronger than AGENTS.md)
            "-c", f'model_instructions_file="system_prompt.md"',
        ]

    def get_env(self) -> dict[str, str]:
        # Codex authenticates via `codex login` (account subscription) — no env vars needed
        return {}

    def _extract_state_text_from_line(self, line: str) -> str | None:
        """Codex --json: search for game state in various event structures.

        Codex emits item.started/item.completed events with mcp_tool_call items.
        Game state appears in item.result.content[].text on item.completed events.
        """
        try:
            obj = json.loads(line)

            # Primary: item.completed with result.content[].text
            if obj.get("type") == "item.completed":
                item = obj.get("item", {})
                result = item.get("result", {})
                if isinstance(result, dict):
                    for block in result.get("content", []):
                        text = block.get("text", "") if isinstance(block, dict) else ""
                        if "player_position" in text and "nearby_entities" in text:
                            return text

            # Fallback: message.content[] (older format)
            for block in obj.get("message", {}).get("content", []):
                text = block.get("text", "") if isinstance(block, dict) else ""
                if "player_position" in text and "nearby_entities" in text:
                    return text

            # Fallback: top-level output/result string
            output = obj.get("output", "") or obj.get("result", "")
            if isinstance(output, str) and "player_position" in output and "nearby_entities" in output:
                return output

        except (json.JSONDecodeError, AttributeError, TypeError):
            pass

        # Last resort: raw substring extraction
        if "player_position" not in line or "nearby_entities" not in line:
            return None
        try:
            start = line.find('{"player_position"')
            if start == -1:
                start = line.find('"player_position"')
                if start > 0:
                    brace = line.rfind("{", 0, start)
                    if brace != -1:
                        start = brace
                    else:
                        return None

            if start != -1:
                depth = 0
                for i in range(start, len(line)):
                    if line[i] == "{":
                        depth += 1
                    elif line[i] == "}":
                        depth -= 1
                        if depth == 0:
                            candidate = line[start : i + 1]
                            parsed = json.loads(candidate)
                            if "player_position" in parsed and "nearby_entities" in parsed:
                                return candidate
                            break
        except (json.JSONDecodeError, ValueError):
            pass

        return None


class QwenCodeAdapter(CLIAdapter):
    """Adapter for Qwen Code CLI (qwen -p).

    Qwen Code outputs stream-json format (Gemini CLI fork) with thinking blocks
    embedded in the message.content array, same as Claude. Reasoning tokens are
    automatically captured in session logs and extracted by extract_turns.py.
    """

    def __init__(self, model: str = "qwen3-coder"):
        super().__init__(model)

    @property
    def name(self) -> str:
        return "qwen-code"

    def setup_sandbox(self, sandbox_dir: Path, system_prompt: str | None = None,
                      port: str = "", username: str = "ClaudeBot") -> None:
        # Qwen Code uses its own MCP registry (qwen mcp add), not .mcp.json.
        # Register custom kaetram MCP server globally.
        result = subprocess.run(
            ["qwen", "mcp", "list"], capture_output=True, text=True, timeout=10
        )
        if "kaetram" not in result.stdout:
            screenshot_dir = str(sandbox_dir / "state")
            subprocess.run(
                ["qwen", "mcp", "add", "kaetram", VENV_PYTHON,
                 str(PROJECT_DIR / "mcp_game_server.py")],
                capture_output=True, text=True, timeout=10
            )

    def build_command(
        self,
        user_prompt: str,
        system_prompt: str,
        max_turns: int,
        max_budget_usd: float | None = None,
        auth_mode: str = "subscription",
    ) -> list[str]:
        # Qwen Code is a Gemini CLI fork — similar to Claude Code interface
        return [
            "qwen",
            "-p",
            user_prompt,
            "--model",
            self.model,
            "--yolo",
            "--output-format",
            "stream-json",
            "--max-session-turns",
            str(max_turns),
            "--append-system-prompt",
            system_prompt,
        ]

    def get_env(self) -> dict[str, str]:
        # Qwen Code reads auth from env vars or ~/.qwen/settings.json
        # Caller must set OPENAI_API_KEY and OPENAI_BASE_URL for headless mode
        return {}

    def _extract_state_text_from_line(self, line: str) -> str | None:
        """Qwen Code stream-json: same format as Claude (Gemini CLI fork).
        State is in message.content[].text."""
        try:
            obj = json.loads(line)
            for block in obj.get("message", {}).get("content", []):
                text = block.get("text", "") if isinstance(block, dict) else ""
                if "player_position" in text and "nearby_entities" in text:
                    return text
        except (json.JSONDecodeError, AttributeError):
            pass
        return None


class KimiAdapter(CLIAdapter):
    """Adapter for Kimi CLI (kimi -p).

    Kimi K2 supports extended thinking mode via --thinking flag, which outputs
    detailed reasoning tokens. These are captured in raw log output and extracted
    by extract_turns.py for SFT training. Timeout is increased to ~60s/turn to
    allow for thinking latency.
    """

    def __init__(self, model: str = "kimi-k2"):
        super().__init__(model)

    @property
    def name(self) -> str:
        return "kimi"

    def setup_sandbox(self, sandbox_dir: Path, system_prompt: str | None = None,
                      port: str = "", username: str = "ClaudeBot") -> None:
        # Resolve MCP config template to sandbox
        mcp_text = _resolve_mcp_template(sandbox_dir, port=port, username=username)
        (sandbox_dir / ".mcp.json").write_text(mcp_text)

    def build_command(
        self,
        user_prompt: str,
        system_prompt: str,
        max_turns: int,
        max_budget_usd: float | None = None,
        auth_mode: str = "subscription",
    ) -> list[str]:
        # Kimi K2 supports extended thinking (--thinking) and structured JSON output (--output-format stream-json)
        # This gives us thinking blocks in the same format as Claude for easy parsing
        # Use timeout wrapper like Codex (estimate ~60s per turn to account for thinking time)
        timeout_seconds = max(max_turns * 60, 900)
        return [
            "timeout",
            str(timeout_seconds),
            "kimi",
            "-p",
            user_prompt,
            "--model",
            self.model,
            "--yolo",
            "--thinking",  # Enable extended thinking for better reasoning capture
            "--output-format",
            "stream-json",  # Structured JSON output with thinking blocks
        ]

    def get_env(self) -> dict[str, str]:
        # Kimi reads auth from MOONSHOT_API_KEY env var or ~/.kimi/config.toml
        return {}

    def _extract_state_text_from_line(self, line: str) -> str | None:
        """Kimi stream-json output: extract game state from message.content[].text.

        Since Kimi now outputs --output-format stream-json (same as Claude),
        game state is in the same JSON structure as Claude output.
        """
        try:
            obj = json.loads(line)
            for block in obj.get("message", {}).get("content", []):
                text = block.get("text", "") if isinstance(block, dict) else ""
                if "player_position" in text and "nearby_entities" in text:
                    return text
        except (json.JSONDecodeError, AttributeError):
            pass
        return None


def get_adapter(harness: str = "claude", model: str | None = None) -> CLIAdapter:
    """Factory function to create the appropriate CLI adapter.

    Args:
        harness: one of 'claude', 'codex', 'qwen-code', 'kimi'
        model: optional model override
    """
    if harness == "codex":
        return CodexAdapter(model=model or "gpt-5.4")
    elif harness == "qwen-code":
        return QwenCodeAdapter(model=model or "qwen3-coder")
    elif harness == "kimi":
        return KimiAdapter(model=model or "kimi-k2")
    else:
        return ClaudeAdapter(model=model or "sonnet")


def detect_log_format(log_path: Path) -> str:
    """Detect CLI harness from session log format.

    Reads the first 10 JSON lines looking for format markers:
    - Claude/Qwen-Code: stream-json with claude_code_version or Gemini CLI markers
    - Codex: JSON with thread.started, item.completed events
    - Kimi: raw text or mixed format (no reliable markers — returns 'unknown')

    Returns 'claude', 'qwen-code', 'codex', 'kimi', or 'unknown'.
    """
    try:
        checked = 0
        with open(log_path, "r", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                checked += 1

                # Claude markers
                if obj.get("claude_code_version"):
                    return "claude"
                if obj.get("type") == "system" and obj.get("subtype") == "init":
                    return "claude"
                if obj.get("type") == "assistant" and "message" in obj:
                    msg = obj["message"]
                    if isinstance(msg, dict) and "content" in msg:
                        return "claude"

                # Codex markers
                if obj.get("type") in ("thread.started", "turn.started",
                                        "item.started", "item.completed"):
                    return "codex"
                if "response_id" in obj or obj.get("type") == "response":
                    return "codex"
                if "role" in obj and "message" not in obj:
                    return "codex"
                if obj.get("event") in ("message", "function_call", "function_call_output"):
                    return "codex"

                if checked >= 10:
                    break
    except OSError:
        pass
    return "unknown"
