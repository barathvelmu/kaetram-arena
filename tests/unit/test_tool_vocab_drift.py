from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from convert_to_qwen import TOOL_DEFINITIONS  # noqa: E402
from tool_surface import (  # noqa: E402
    LEGACY_HIDDEN_TOOL_NAMES,
    MODEL_VISIBLE_TOOL_NAMES,
)

SYSTEM_PROMPT = REPO_ROOT / "prompts" / "system.md"
MCP_SERVER = REPO_ROOT / "mcp_game_server.py"


def _system_prompt_tool_names() -> tuple[str, ...]:
    text = SYSTEM_PROMPT.read_text()
    block = text.split("<tools>", 1)[1].split("</tools>", 1)[0]
    names = []
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("| `"):
            continue
        match = re.search(r"`([^`]+)`", line)
        if not match:
            continue
        raw = match.group(1)
        names.append(raw.split("(", 1)[0])
    return tuple(names)


def _exported_mcp_tool_names() -> tuple[str, ...]:
    text = MCP_SERVER.read_text()
    return tuple(
        re.findall(r"@mcp\.tool\(\)\s+async def ([a-zA-Z_][a-zA-Z0-9_]*)\(", text)
    )


def test_system_prompt_matches_curated_model_visible_surface():
    assert _system_prompt_tool_names() == MODEL_VISIBLE_TOOL_NAMES


def test_convert_to_qwen_metadata_matches_curated_surface():
    metadata_tools = tuple(tool["function"]["name"] for tool in TOOL_DEFINITIONS)
    assert metadata_tools == MODEL_VISIBLE_TOOL_NAMES


def test_play_qwen_filters_to_curated_surface():
    source = (REPO_ROOT / "play_qwen.py").read_text()
    assert "from tool_surface import MODEL_VISIBLE_TOOL_NAMES" in source
    assert "if name not in MODEL_VISIBLE_TOOL_NAMES:" in source
    assert "return [n for n in self._tools if n in MODEL_VISIBLE_TOOL_NAMES]" in source


def test_live_mcp_server_exports_exact_curated_surface():
    exported = _exported_mcp_tool_names()
    assert set(exported) == set(MODEL_VISIBLE_TOOL_NAMES)
    for hidden in LEGACY_HIDDEN_TOOL_NAMES:
        assert hidden not in exported
