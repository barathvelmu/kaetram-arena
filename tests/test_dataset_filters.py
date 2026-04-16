from pathlib import Path

from convert_to_qwen import _is_excluded_agent


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_qwen_agent_5_is_excluded_from_sft_data():
    assert _is_excluded_agent(Path("dataset/extracted/agent_5/session_foo/turns.jsonl"))
    assert not _is_excluded_agent(Path("dataset/extracted/agent_0/session_foo/turns.jsonl"))


def test_collect_sft_data_uses_agent_scoped_outputs_without_dead_flags():
    source = (REPO_ROOT / "scripts" / "collect_sft_data.sh").read_text()
    assert 'agent_output_dir="$EXTRACTED_DIR/$agent_name"' in source
    assert "--no-frames" not in source
