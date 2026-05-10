"""Train↔inference parity asserts on the runtime harness files.

Locks two contracts that, if violated, silently make Qwen's runtime
behavior diverge from what the training data taught it:

  1. play_qwen.py's bootstrap user message MUST equal
     bootstrap.build_orchestrate_bootstrap(personality, session_n) —
     byte-identical to what Claude saw at training-data collection.

  2. play_qwen.py MUST pass all four Qwen3.5 thinking-mode sampling params
     explicitly (temperature, top_p, top_k, presence_penalty) so the
     SGLang server's defaults can never silently win.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAY_QWEN_PY = REPO_ROOT / "play_qwen.py"
CONVERT_TO_QWEN = REPO_ROOT / "convert_to_qwen.py"

# Make convert_to_qwen importable without installing the project.
sys.path.insert(0, str(REPO_ROOT))


def test_play_qwen_bootstrap_uses_shared_module():
    """play_qwen.py must construct its bootstrap via
    bootstrap.build_orchestrate_bootstrap (single source of truth shared
    with orchestrate.py and convert_to_qwen.py). String-literal bootstraps
    in play_qwen.py are forbidden — they create train/eval drift the moment
    the orchestrate template changes."""
    src = PLAY_QWEN_PY.read_text()
    assert "from bootstrap import build_orchestrate_bootstrap" in src, (
        "play_qwen.py must import build_orchestrate_bootstrap from bootstrap.py"
    )
    assert "build_orchestrate_bootstrap(" in src, (
        "play_qwen.py must call build_orchestrate_bootstrap to construct the "
        "user bootstrap message"
    )
    # Forbidden: hardcoded "What should you do?" or any string-literal user
    # message append outside of the tool_response wrapper.
    assert '"What should you do?"' not in src, (
        "play_qwen.py contains the legacy hardcoded bootstrap "
        "'What should you do?' — replace with build_orchestrate_bootstrap()"
    )


def test_play_qwen_pins_qwen_sampling_params():
    """play_qwen.py must explicitly set temperature, top_p, top_k, and
    presence_penalty so server-side defaults can never silently win.
    """
    src = PLAY_QWEN_PY.read_text()
    required = [
        # Either the constant or a literal value — flexible regex.
        (r"\btemperature\s*=", "temperature"),
        (r"\btop_p\s*=", "top_p"),
        (r"\btop_k\b", "top_k"),  # appears inside extra_body dict
        (r"\bpresence_penalty\b", "presence_penalty"),
    ]
    missing = [name for pat, name in required if not re.search(pat, src)]
    assert not missing, (
        f"play_qwen.py is missing explicit sampling kwargs: {missing}. "
        f"Pin all four (temperature, top_p, top_k, presence_penalty) at the "
        f"client.chat.completions.create call site."
    )
