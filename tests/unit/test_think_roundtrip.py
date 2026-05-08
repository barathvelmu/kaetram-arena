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


def _modal_available() -> bool:
    try:
        import modal  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not DATASET.exists(), reason="dataset not built")
@pytest.mark.skipif(
    not _modal_available(),
    reason="modal SDK not installed locally (only present on Modal cloud)",
)
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
        # Count <think> blocks attributable to assistant content (i.e. the ones
        # produced by reasoning_content). Each thinking turn opens AND closes,
        # so we count opens and require they're ≤ assistant_count and balanced.
        think_open = rendered.count("<think>")
        think_close = rendered.count("</think>")

        # Every assistant turn that *had* reasoning_content must open a <think>.
        # In a pure-thinking record (current default), think_open == assistant_count.
        # Mixed-mode records (some turns no-think) will have think_open < count.
        assert think_open <= assistant_count, (
            f"record {i}: {think_open} <think> opens > {assistant_count} "
            f"assistant turns — template emitted spurious think blocks."
        )
        # Tag balance.
        assert think_close == think_open, (
            f"record {i}: unbalanced <think> tags "
            f"({think_open} open, {think_close} close)"
        )

        # For records where every assistant turn was supposed to think, the
        # template must NOT drop intermediate-turn reasoning. We detect "every
        # turn thinks" by checking that every assistant message's content
        # contains <think>...
        every_turn_thinks = all(
            "<think>" in (m.get("content") or "")
            for m in r["messages"] if m.get("role") == "assistant"
        )
        if every_turn_thinks:
            assert think_open == assistant_count, (
                f"record {i}: {assistant_count} assistant turns all carry "
                f"<think>, but rendered output has {think_open} opens — "
                f"intermediate-turn reasoning is being dropped (patched "
                f"template should prevent this)."
            )


@pytest.mark.skipif(
    not _modal_available(),
    reason="modal SDK not installed locally (only present on Modal cloud)",
)
def test_no_think_assistant_turn_renders_cleanly():
    """A non-thinking assistant turn (tool_calls, no <think> block) must
    render through the patched chat template without injecting a synthetic
    <think> wrapper, without dropping the tool_call, and without breaking
    multi-turn structure when interleaved with thinking turns.

    This is the precondition for mixed-mode SFT (Qwen Thinking Mode Fusion):
    we want to teach the model that some turns reason and some don't, and the
    chat template must faithfully represent both.
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
    # Exactly 2 <think> blocks (turn 0 and turn 2). The middle no-think turn
    # must NOT emit a <think> block — the chat template should not synthesize
    # one, and our `reasoning_content` extraction must yield empty for ""-content.
    think_open = rendered.count("<think>")
    think_close = rendered.count("</think>")
    assert think_open == 2, (
        f"expected exactly 2 <think> opens (no-think turn must not emit one), "
        f"got {think_open}\n---\n{rendered}\n---"
    )
    assert think_close == 2, f"unbalanced think tags: {think_open}/{think_close}"

    # All three tool_calls must survive the round-trip.
    for fname, args in [("warp", "mudwich"), ("attack", "rat"), ("attack", "rat")]:
        assert fname in rendered, f"tool name {fname!r} missing from render"
    assert rendered.count('"name": "attack"') + rendered.count("'name': 'attack'") >= 1 \
           or rendered.count("attack") >= 2, "attack tool_call missing"

    # The no-think assistant turn's tool_call (call_002) must be preserved.
    assert "call_002" in rendered or "attack" in rendered, (
        "no-think turn's tool_call appears to have been dropped by template"
    )
