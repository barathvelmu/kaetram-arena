"""QwenAdapter contract tests.

Locks the argv shape and env vars QwenAdapter produces. play_qwen runs as a
long-lived warm-session subprocess: orchestrate spawns it once per
AgentInstance and the SessionLogger inside play_qwen handles per-session
files + .session_counter rotation. The adapter therefore does NOT pass
--max-turns or --session-n (both ignored by play_qwen / never accepted in
the new CLI). For warm-session-aware callers (orchestrate, eval_harness)
the run-context attrs (run_dir / harness_meta_path / max_duration_seconds)
are set on the adapter before build_command.
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
            max_turns=200,            # accepted for polymorphism, dropped by Qwen
            personality="grinder",
            session_n=5,              # accepted for polymorphism, dropped by Qwen
        )
    assert cmd[1].endswith("play_qwen.py")
    assert "--endpoint" in cmd
    assert "--system-prompt" in cmd
    # play_qwen no longer accepts these; warm-session loop owns turn/session bookkeeping.
    assert "--max-turns" not in cmd
    assert "--session-n" not in cmd
    assert "--personality" in cmd and "grinder" in cmd
    assert "--server-port" in cmd and "9011" in cmd


def test_build_command_threads_warm_session_context_when_set():
    """orchestrate / eval_harness assign run_dir + harness_meta_path +
    max_duration_seconds on the adapter before build_command. Verify those
    flow through to argv."""
    a = QwenAdapter()
    a.run_dir = Path("/tmp/test_run_dir")
    a.harness_meta_path = Path("/tmp/test_meta.json")
    a.max_duration_seconds = 600
    with tempfile.TemporaryDirectory() as td:
        a.setup_sandbox(Path(td), system_prompt="x", port="9001", username="QwenGrinder")
        cmd = a.build_command(
            user_prompt="x", system_prompt="y", max_turns=0,
            personality="grinder", session_n=1,
        )
    assert "--run-dir" in cmd and "/tmp/test_run_dir" in cmd
    assert "--harness-meta" in cmd and "/tmp/test_meta.json" in cmd
    assert "--max-duration-seconds" in cmd and "600" in cmd


def test_build_command_omits_warm_context_when_unset():
    """Solo-dev invocation leaves run_dir/harness_meta/max_duration unset.
    play_qwen falls through to its own defaults."""
    a = QwenAdapter()
    with tempfile.TemporaryDirectory() as td:
        a.setup_sandbox(Path(td), system_prompt="x", port="9001", username="QwenGrinder")
        cmd = a.build_command(
            user_prompt="x", system_prompt="y", max_turns=0,
            personality="grinder", session_n=1,
        )
    assert "--run-dir" not in cmd
    assert "--harness-meta" not in cmd
    assert "--max-duration-seconds" not in cmd


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
