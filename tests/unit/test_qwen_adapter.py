"""QwenAdapter contract tests.

Locks the argv shape and env vars QwenAdapter produces, so orchestrate.py
spawns play_qwen.py with the right --personality / --session-n / --server-port
on every session. If this test changes, you've changed the adapter surface
and probably need to verify play_qwen.py still parses the new args.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from cli_adapter import QwenAdapter, QWEN_SFT_ENDPOINT, get_adapter


def test_factory_returns_qwen_adapter():
    a = get_adapter("qwen")
    assert isinstance(a, QwenAdapter)
    assert a.name == "qwen"
    assert a.model == "r10-sft"
    assert a.endpoint == QWEN_SFT_ENDPOINT


def test_factory_endpoint_override():
    a = get_adapter("qwen", qwen_endpoint="http://localhost:9999/v1")
    assert a.endpoint == "http://localhost:9999/v1"


def test_setup_sandbox_writes_system_prompt_and_stores_port():
    a = QwenAdapter()
    with tempfile.TemporaryDirectory() as td:
        sandbox = Path(td)
        a.setup_sandbox(sandbox, system_prompt="HELLO", port="9001", username="QwenCompletionist")
        assert (sandbox / "system_prompt.md").read_text() == "HELLO"
        assert a._port == "9001"
        assert a._username == "QwenCompletionist"


def test_build_command_includes_required_flags():
    a = QwenAdapter()
    with tempfile.TemporaryDirectory() as td:
        a.setup_sandbox(Path(td), system_prompt="x", port="9011", username="QwenGrinder")
        cmd = a.build_command(
            user_prompt="ignored",  # play_qwen rebuilds via shared bootstrap
            system_prompt="ignored",
            max_turns=200,
            personality="grinder",
            session_n=5,
        )
    assert cmd[1].endswith("play_qwen.py")
    assert "--endpoint" in cmd
    assert "--system-prompt" in cmd
    assert "--max-turns" in cmd and "200" in cmd
    assert "--session-n" in cmd and "5" in cmd
    assert "--personality" in cmd and "grinder" in cmd
    assert "--server-port" in cmd and "9011" in cmd


def test_build_command_omits_personality_when_none():
    """personality=None / 'none' falls through to play_qwen.py's default
    (currently 'completionist'). Adapter shouldn't pass an empty --personality
    that would confuse argparse."""
    a = QwenAdapter()
    with tempfile.TemporaryDirectory() as td:
        a.setup_sandbox(Path(td), port="9001", username="QwenCompletionist")
        cmd = a.build_command(
            user_prompt="x", system_prompt="y", max_turns=10,
            personality=None, session_n=1,
        )
    assert "--personality" not in cmd


def test_get_env_includes_username_and_unbuffered():
    a = QwenAdapter()
    with tempfile.TemporaryDirectory() as td:
        a.setup_sandbox(Path(td), port="9001", username="QwenExplorer")
        env = a.get_env()
    assert env["KAETRAM_USERNAME"] == "QwenExplorer"
    assert env["PYTHONUNBUFFERED"] == "1"
