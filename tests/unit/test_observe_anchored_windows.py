"""Every multi-turn SFT record's first assistant tool_call must be `observe`.

Locks the runtime parity contract from `convert_to_qwen.build_multi_turn_records`
(replay-prefix fix): training records should never start with an action turn,
because the runtime sliding window in `play_qwen.py:_trim_context` always
preserves the most recent observe + tool_response pair.

Single-turn records are also observe-anchored by construction
(`build_single_turn_records` pairs an observe turn with the immediately
following action), so this test treats both shapes uniformly: the FIRST
assistant message in the record must have a `tool_calls[0]` whose function
name is `observe`.

If this fires after a dataset rebuild, either the replay-prefix logic
regressed, or some session emitted records that bypass the multi-turn
builder. Both are bugs.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SFT_DIR = REPO_ROOT / "dataset" / "qwen_sft"
TRAIN = SFT_DIR / "train.json"
VAL = SFT_DIR / "val.json"


def _first_tool_call_name(record: dict) -> str | None:
    """Return the function name of the first tool_call in the first
    assistant message of the record, or None if no tool_call exists."""
    for m in record["messages"]:
        if m["role"] != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            return fn.get("name")
        # First assistant message has no tool_call — text-only assistant.
        return None
    return None


@pytest.mark.skipif(
    not (TRAIN.exists() and VAL.exists()),
    reason="dataset not built",
)
def test_every_record_starts_with_observe():
    """Every record (train and val) must have `observe` as its first
    assistant tool_call.
    """
    offenders: list[tuple[str, int, str | None]] = []
    for split, path in (("train", TRAIN), ("val", VAL)):
        records = json.loads(path.read_text())
        for i, rec in enumerate(records):
            name = _first_tool_call_name(rec)
            if name != "observe":
                offenders.append((split, i, name))
    if offenders:
        # Group by tool_call name for a digestible failure message.
        by_name: dict[str | None, int] = {}
        for _, _, name in offenders:
            by_name[name] = by_name.get(name, 0) + 1
        msg_lines = [
            f"{len(offenders)} records do NOT start with an `observe` tool_call.",
            "Replay-prefix in convert_to_qwen.build_multi_turn_records "
            "should have prepended an observe to every action-starting window.",
            "Top offending first-tool names: "
            + ", ".join(f"{k}={v}" for k, v in sorted(by_name.items(), key=lambda x: -x[1])),
            "First 5 offenders (split, idx, first_tool): "
            + repr(offenders[:5]),
        ]
        pytest.fail("\n".join(msg_lines))
