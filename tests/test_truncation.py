"""End-to-end truncation gate for the r10 SFT dataset.

Verifies that every training record, when rendered through the actual model
tokenizer + chat template, fits inside `MAX_SEQ_LEN - SAFETY_MARGIN` tokens.

This is the real end-to-end check — not a character-length proxy. It catches
cases where a record looks "small" in bytes but expands after chat-template
wrapping, tool-call formatting, or `<think>` block emission.

Source-of-truth constants match `finetune/train_modal.py`:
    MODEL_ID      = "unsloth/Qwen3.5-9B"
    MAX_SEQ_LEN   = 16384
The 256-token safety margin matches the KAE-42 pre-tokenize gate.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET = REPO_ROOT / "dataset" / "qwen_sft" / "train.json"

MAX_SEQ_LEN = 16384
SAFETY_MARGIN = 256
LIMIT = MAX_SEQ_LEN - SAFETY_MARGIN  # 16128
TOKENIZER_ID = "unsloth/Qwen3.5-9B"


@pytest.mark.skipif(not DATASET.exists(), reason="dataset not built")
def test_no_record_exceeds_max_seq_len():
    """No training record may tokenize to more than MAX_SEQ_LEN - SAFETY_MARGIN.

    Records over the limit get silently truncated by Unsloth's collator during
    training, which drops the final assistant turn (the supervised target) —
    the model trains on "observe then nothing". Catch this before launch.
    """
    try:
        from transformers import AutoTokenizer
    except ImportError:
        pytest.skip("transformers not installed")

    tok = AutoTokenizer.from_pretrained(TOKENIZER_ID)

    with open(DATASET) as f:
        records = json.load(f)

    over: list[tuple[int, int]] = []
    for i, r in enumerate(records):
        messages = r.get("messages")
        if not messages:
            continue
        n = len(tok.apply_chat_template(messages, tokenize=True))
        if n > LIMIT:
            over.append((i, n))

    assert not over, (
        f"{len(over)} records exceed {LIMIT} tokens "
        f"(MAX_SEQ_LEN={MAX_SEQ_LEN}, safety margin={SAFETY_MARGIN}). "
        f"First offenders (index, tokens): {over[:5]}"
    )
