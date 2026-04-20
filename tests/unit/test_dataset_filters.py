import json
from pathlib import Path

import pytest

from convert_to_qwen import _is_excluded_agent


REPO_ROOT = Path(__file__).resolve().parent.parent
SFT_DIR = REPO_ROOT / "dataset" / "qwen_sft"


def test_qwen_agent_5_is_excluded_from_sft_data():
    assert _is_excluded_agent(Path("dataset/extracted/agent_5/session_foo/turns.jsonl"))
    assert not _is_excluded_agent(Path("dataset/extracted/agent_0/session_foo/turns.jsonl"))


def test_collect_sft_data_uses_agent_scoped_outputs_without_dead_flags():
    source = (REPO_ROOT / "scripts" / "collect_sft_data.sh").read_text()
    assert 'agent_output_dir="$EXTRACTED_DIR/$agent_name"' in source
    assert "--no-frames" not in source


# ---------------------------------------------------------------------------
# r10 dataset-shape assertions. These guard against two specific regressions:
#   1. `observe` tool_calls vanishing from training data (r9 bug — 0/21976).
#   2. Train vs eval prompt drift (r9 personality mismatch — 2-sentence dict vs
#      full .md file).
#
# Both are conditioned on the dataset being built. CI environments that don't
# build the dataset will skip these; local runs after `convert_to_qwen.py` must
# pass them before launching an SFT run.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not (SFT_DIR / "train.json").exists(), reason="dataset not built")
def test_observe_tool_calls_present_in_training_data():
    """Regression guard: r9 train.json had 0 observe calls in 21,976 tool calls
    because extract_turns consumed Sonnet's observe tool_use to populate
    game_state. r10 emits observe as a first-class turn — expect thousands.
    """
    train = json.loads((SFT_DIR / "train.json").read_text())
    n_observe = 0
    n_total_tool_calls = 0
    for rec in train:
        for msg in rec.get("messages", []):
            if msg.get("role") != "assistant":
                continue
            for tc in msg.get("tool_calls") or []:
                n_total_tool_calls += 1
                if tc.get("function", {}).get("name") == "observe":
                    n_observe += 1

    assert n_total_tool_calls > 0, "train.json has zero tool calls — pipeline broken"
    ratio = n_observe / n_total_tool_calls
    assert n_observe > 1000, (
        f"Observe supervision regression: only {n_observe} observe calls in "
        f"{n_total_tool_calls} tool calls ({ratio:.1%}). Expected thousands "
        f"(Sonnet emits ~30-50% observe)."
    )


@pytest.mark.skipif(not (SFT_DIR / "metadata.json").exists(), reason="dataset not built")
def test_metadata_personality_suffixes_match_md_files_on_disk():
    """Regression guard: r9 personality_suffixes were 2-sentence paraphrases; eval
    loaded the full .md file. r10 must store full .md content in metadata so
    training and eval render the same system prompt at the __PERSONALITY_BLOCK__
    substitution point.
    """
    metadata = json.loads((SFT_DIR / "metadata.json").read_text())
    suffixes = metadata.get("personality_suffixes", {})
    for name in ("aggressive", "methodical", "curious"):
        md_path = REPO_ROOT / "prompts" / "personalities" / f"{name}.md"
        if not md_path.exists():
            pytest.fail(f"{md_path} missing — prompts/personalities/ incomplete")
        expected = md_path.read_text()
        assert suffixes.get(name) == expected, (
            f"metadata personality_suffixes[{name}] drifted from {md_path.name}: "
            f"{len(suffixes.get(name, ''))}B vs file {len(expected)}B"
        )


@pytest.mark.skipif(not (SFT_DIR / "metadata.json").exists(), reason="dataset not built")
def test_metadata_system_prompt_preserves_personality_placeholder():
    """The stored system_prompt must still contain __PERSONALITY_BLOCK__ so
    train_modal._build_system_prompt can substitute at the correct location
    (byte-parity with eval_harness.resolve_system_prompt).
    """
    metadata = json.loads((SFT_DIR / "metadata.json").read_text())
    assert "__PERSONALITY_BLOCK__" in metadata.get("system_prompt", ""), (
        "metadata.system_prompt is missing __PERSONALITY_BLOCK__ placeholder — "
        "training will fall back to empty personality or append, breaking eval parity."
    )
    # Other placeholders should already be resolved to match eval output.
    assert "__GAME_KNOWLEDGE_BLOCK__" not in metadata["system_prompt"]
    assert "__USERNAME__" not in metadata["system_prompt"]


@pytest.mark.skipif(not (SFT_DIR / "train.json").exists(), reason="dataset not built")
def test_training_records_do_not_inject_game_state_in_user_messages():
    """r10 core fix: user messages must stop handing state to the model.
    State now arrives via the tool_result of a preceding observe call.
    """
    train = json.loads((SFT_DIR / "train.json").read_text())
    offenders = 0
    checked = 0
    for rec in train[:500]:  # sample to keep test fast
        for msg in rec.get("messages", []):
            if msg.get("role") == "user":
                checked += 1
                if "<game_state>" in str(msg.get("content", "")):
                    offenders += 1

    assert checked > 0, "no user messages found in sampled records"
    assert offenders == 0, (
        f"{offenders}/{checked} user messages still contain <game_state> — "
        f"build_user_message regressed to pre-r10 behavior."
    )
