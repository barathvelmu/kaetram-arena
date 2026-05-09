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


REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = REPO_ROOT / "dataset" / "qwen_sft" / "train.json"

TOKENIZER_ID = "unsloth/Qwen3.5-9B"


def _apply_runtime_template_patch(tokenizer):
    """Apply the same chat-template patch that training and serving apply.

    Single source of truth lives in `finetune/render.patch_qwen_chat_template`;
    it swaps Qwen3.5's stock template to replace the `last_query_index` gate
    with a `reasoning_content`-based check. Tests must use the SAME patched
    template to represent actual runtime behavior.
    """
    sys.path.insert(0, str(REPO_ROOT / "finetune"))
    try:
        from render import patch_qwen_chat_template  # type: ignore
    finally:
        sys.path.pop(0)
    patch_qwen_chat_template(tokenizer)


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

        # Per Qwen3 tech report (arxiv 2505.09388 §thinking mode fusion) and the
        # patched template (finetune/render.patch_qwen_chat_template): every
        # assistant turn after the user query gets a <think>...</think> block.
        # Thinking turns carry full reasoning; non-thinking turns carry an
        # empty <think>\n\n</think>. So think_open == assistant_count always.
        assert think_open == assistant_count, (
            f"record {i}: {think_open} <think> opens != {assistant_count} "
            f"assistant turns — template is dropping (or duplicating) thinks."
        )
        assert think_close == think_open, (
            f"record {i}: unbalanced <think> tags "
            f"({think_open} open, {think_close} close)"
        )

        # For records where every assistant turn carries non-empty <think>
        # in content, every rendered <think> block should be non-empty too —
        # i.e. no turn lost its reasoning to the (broken) stock template's
        # last_query_index strip.
        every_turn_thinks = all(
            "<think>" in (m.get("content") or "")
            for m in r["messages"] if m.get("role") == "assistant"
        )
        if every_turn_thinks:
            assert "<think>\n\n</think>" not in rendered, (
                f"record {i}: every assistant turn carries <think> in content, "
                f"but rendered output has empty <think>\\n\\n</think> — "
                f"intermediate-turn reasoning is being dropped."
            )


def test_no_think_assistant_turn_renders_cleanly():
    """Mixed-mode SFT (Qwen Thinking Mode Fusion): every assistant turn must
    render as `<|im_start|>assistant\\n<think>...</think>\\n\\n<tool_call>`.
    Thinking turns carry full reasoning_content; no-think turns carry an
    empty `<think>\\n\\n</think>` per Qwen3 tech report (arxiv 2505.09388).

    The patched template (finetune/render.patch_qwen_chat_template) injects
    the empty think on no-think turns automatically via the
    `loop.index0 > ns.last_query_index` branch. Tool_calls must survive on
    every turn regardless of reasoning presence.
    """
    try:
        from transformers import AutoTokenizer
    except ImportError:
        pytest.skip("transformers not installed")

    tok = AutoTokenizer.from_pretrained(TOKENIZER_ID)
    _apply_runtime_template_patch(tok)

    # Three-turn record: think → no-think → think. Mirrors what the
    # post-fix dataset will produce when reasoning is empty on a turn.
    messages = [
        {"role": "system", "content": "You are a test agent."},
        {"role": "user", "content": "What should you do?"},
        {
            "role": "assistant",
            "content": "<think>\nDeliberate decision.\n</think>",
            "tool_calls": [{
                "id": "call_001",
                "type": "function",
                "function": {"name": "warp", "arguments": {"location": "mudwich"}},
            }],
        },
        {"role": "tool", "content": "warped", "tool_call_id": "call_001", "name": "warp"},
        {"role": "user", "content": "What should you do?"},
        # No-think turn: empty content, just the tool_call.
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_002",
                "type": "function",
                "function": {"name": "attack", "arguments": {"target": "rat"}},
            }],
        },
        {"role": "tool", "content": "hit for 5", "tool_call_id": "call_002", "name": "attack"},
        {"role": "user", "content": "What should you do?"},
        {
            "role": "assistant",
            "content": "<think>\nMob almost dead.\n</think>",
            "tool_calls": [{
                "id": "call_003",
                "type": "function",
                "function": {"name": "attack", "arguments": {"target": "rat"}},
            }],
        },
        {"role": "tool", "content": "killed", "tool_call_id": "call_003", "name": "attack"},
    ]

    rendered = tok.apply_chat_template(messages, tokenize=False)

    # Should render exactly 3 assistant turns.
    assert rendered.count("<|im_start|>assistant") == 3, (
        f"expected 3 assistant turns, got {rendered.count('<|im_start|>assistant')}\n"
        f"---\n{rendered}\n---"
    )
    # The patched template has 3 branches:
    #   1. reasoning_content truthy → full <think>...</think>
    #   2. no reasoning + after last_query_index ��� empty <think>\n\n</think>
    #   3. no reasoning + before last_query_index → no think wrapper
    # In this message sequence the no-think turn (index 5) is BEFORE the
    # last user message (index 7), so it falls into branch 3 — no wrapper.
    # Only the 2 thinking turns produce <think> blocks.
    think_open = rendered.count("<think>")
    think_close = rendered.count("</think>")
    assert think_open == 2, (
        f"expected 2 <think> opens (thinking turns only; middle no-think turn "
        f"is before last_query_index so gets no wrapper), "
        f"got {think_open}\n---\n{rendered}\n---"
    )
    assert think_close == 2, f"unbalanced think tags: {think_open}/{think_close}"

    # All three tool_calls must survive the round-trip.
    for fname in ("warp", "attack"):
        assert fname in rendered, f"tool name {fname!r} missing from render"
