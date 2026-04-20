"""Dataset loop-noise gates.

Two regressions we do not want to re-teach the model:

1. `observe -> observe` adjacency. extract_turns.py historically emitted an
   observe turn even when the previous assistant action was also observe.
   `convert_to_qwen.py` patch #2 (KAE-42) filters the bigram; this test
   guards against regression.

2. 3+ identical consecutive tool calls (same name + same arguments). This is
   the r9 pathology — warp/equip/dialogue loops that burned entire episodes.
   Even one training record demonstrating the pattern encourages the model
   to copy it at inference time.

Both checks run over the built SFT dataset; the tests skip if the dataset
has not been built locally.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET = REPO_ROOT / "dataset" / "qwen_sft" / "train.json"


def _extract_tool_seq(record: dict) -> list[tuple]:
    """List of (tool_name, sorted-args-tuple) per assistant tool_call, in order."""
    seq: list[tuple] = []
    for m in record.get("messages", []):
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function", {}) or {}
            name = fn.get("name") or tc.get("name")
            args = fn.get("arguments", None)
            if args is None:
                args = tc.get("arguments", "{}")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            if isinstance(args, dict):
                key = tuple(sorted(args.items()))
            else:
                key = (args,)
            seq.append((name, key))
    return seq


@pytest.mark.skipif(not DATASET.exists(), reason="dataset not built")
def test_no_observe_observe_bigram():
    """No record may contain an `observe -> observe` assistant adjacency.

    Sonnet occasionally re-observes within a single reasoning step; we do
    not want to supervise that — the second observe is a wasted turn.
    """
    with open(DATASET) as f:
        records = json.load(f)

    offending: list[int] = []
    for i, r in enumerate(records):
        seq = _extract_tool_seq(r)
        for a, b in zip(seq, seq[1:]):
            if a[0] == "observe" and b[0] == "observe":
                offending.append(i)
                break

    assert not offending, (
        f"{len(offending)} records have observe->observe adjacency. "
        f"First offenders (record index): {offending[:5]}"
    )


@pytest.mark.skipif(not DATASET.exists(), reason="dataset not built")
def test_no_3plus_identical_streak():
    """No record may contain 3+ identical consecutive tool calls (same name
    AND same arguments). This is the r9 loop pathology — teaching the model
    even one such streak validates the behavior at inference.
    """
    with open(DATASET) as f:
        records = json.load(f)

    offending: list[tuple[int, str]] = []
    for i, r in enumerate(records):
        seq = _extract_tool_seq(r)
        for j in range(len(seq) - 2):
            if seq[j] == seq[j + 1] == seq[j + 2]:
                offending.append((i, seq[j][0]))
                break

    assert not offending, (
        f"{len(offending)} records have 3+ identical consecutive tool calls. "
        f"First offenders (record index, tool): {offending[:5]}"
    )
