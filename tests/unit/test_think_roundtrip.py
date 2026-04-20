"""End-to-end `<think>` round-trip test against the real Qwen3.5 tokenizer.

`tests/test_chat_template.py` already asserts that the Jinja fragment
`{%- if reasoning_content %}<think>...</think>` is present in the source of
`train_modal.py` / `serve_modal*.py`. That's a source-level check — it does
NOT catch the QwenLM/Qwen3 issue #1831 bug where the stock chat template
silently drops `<think>` from all assistant messages before `last_query_index`
in multi-turn conversations.

This test closes that gap. We render multi-turn records through the real
`AutoTokenizer.apply_chat_template()` and require that every assistant turn
in the rendered output has a matching `<think>...</think>` block — including
intermediate turns. If Qwen's template strips them, the `<think>` count will
be less than the assistant-turn count and this test fails.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET = REPO_ROOT / "dataset" / "qwen_sft" / "train.json"

TOKENIZER_ID = "unsloth/Qwen3.5-9B"


def _apply_runtime_template_patch(tokenizer):
    """Apply the same `_patch_qwen_chat_template` that training and serving apply.

    Runtime code in `finetune/train_modal.py:_patch_qwen_chat_template` and
    `finetune/serve_modal.py:_patch_qwen_chat_template` swap Qwen3's stock
    template to replace the `last_query_index` gate with a
    `reasoning_content`-based check. Tests must use the SAME patched template
    to represent actual runtime behavior — otherwise we'd be asserting against
    the (broken) stock template and catching a non-existent bug.
    """
    sys.path.insert(0, str(REPO_ROOT / "finetune"))
    try:
        from train_modal import _patch_qwen_chat_template  # type: ignore
    finally:
        sys.path.pop(0)
    _patch_qwen_chat_template(tokenizer)


@pytest.mark.skipif(not DATASET.exists(), reason="dataset not built")
def test_think_survives_roundtrip_for_multi_turn_records():
    """Every assistant turn (including intermediate ones) must retain `<think>`
    after `apply_chat_template` round-trips the messages THROUGH THE PATCHED
    TEMPLATE used by training + serving. This is the true end-to-end guard
    for QwenLM/Qwen3 #1831 — we patch the `last_query_index` gate at runtime,
    and this test verifies the patch actually keeps reasoning on every turn.
    """
    try:
        from transformers import AutoTokenizer
    except ImportError:
        pytest.skip("transformers not installed")

    tok = AutoTokenizer.from_pretrained(TOKENIZER_ID)
    _apply_runtime_template_patch(tok)

    with open(DATASET) as f:
        records = json.load(f)

    # Pick records with >=3 assistant turns so we exercise the intermediate-turn
    # path (the one Qwen's stock template breaks; the patch is supposed to fix).
    samples = [
        r for r in records
        if sum(1 for m in r.get("messages", []) if m.get("role") == "assistant") >= 3
    ][:5]
    assert samples, "no multi-turn records (>=3 assistant turns) found in train.json"

    for i, r in enumerate(samples):
        rendered = tok.apply_chat_template(r["messages"], tokenize=False)
        assistant_count = rendered.count("<|im_start|>assistant")
        think_open = rendered.count("<think>")
        think_close = rendered.count("</think>")

        # Every assistant turn must open a <think>. If the patched template
        # still strips intermediate turns, think_open < assistant_count.
        assert think_open == assistant_count, (
            f"record {i}: {assistant_count} assistant turns but only "
            f"{think_open} <think> opens — intermediate-turn reasoning is "
            f"being dropped (patched template should prevent this)."
        )
        # Tag balance.
        assert think_close == think_open, (
            f"record {i}: unbalanced <think> tags "
            f"({think_open} open, {think_close} close)"
        )
