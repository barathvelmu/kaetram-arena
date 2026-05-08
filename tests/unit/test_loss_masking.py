"""Loss-masking gate for the r10 SFT training pipeline.

Verifies that Unsloth's `train_on_responses_only` masking, when applied to our
Qwen3.5 chat template, trains on assistant content only and masks system/user
turns. Reproduces the scanning logic from `unsloth_zoo/dataset_utils.py` on a
multi-turn sample matching our training format — CPU-only, no GPU required.

Catches regressions where:
  - the chat template changes and the role markers shift
  - assistant turns end up fully masked (model trains on nothing)
  - user/system turns leak into the loss
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


TOKENIZER_ID = "unsloth/Qwen3.5-9B"
INSTRUCTION_PART = "<|im_start|>user\n"
RESPONSE_PART = "<|im_start|>assistant\n"


def _hf_tokenizer_available() -> bool:
    if os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN"):
        return True
    cache_root = Path(os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface"))
    cache_dir = cache_root / "hub" / f"models--{TOKENIZER_ID.replace('/', '--')}"
    return cache_dir.exists()


def _find_token_pattern(input_ids: list[int], pattern_ids: list[int], start: int = 0) -> int:
    plen = len(pattern_ids)
    for i in range(start, len(input_ids) - plen + 1):
        if input_ids[i:i + plen] == pattern_ids:
            return i
    return -1


def _simulate_masking(input_ids: list[int], instruction_ids: list[int], response_ids: list[int]) -> list[int]:
    """Mirror Unsloth's train_on_responses_only label construction.
    Returns labels: -100 for masked tokens, original token id for trained tokens.
    """
    n = len(input_ids)
    labels = [-100] * n
    j = 0
    while j < n:
        resp_pos = _find_token_pattern(input_ids, response_ids, j)
        if resp_pos == -1:
            break
        content_start = resp_pos + len(response_ids)
        user_pos = _find_token_pattern(input_ids, instruction_ids, content_start)
        content_end = n if user_pos == -1 else user_pos
        for i in range(content_start, content_end):
            labels[i] = input_ids[i]
        j = content_end
    return labels


@pytest.mark.skipif(
    not _hf_tokenizer_available(),
    reason=f"{TOKENIZER_ID} not in HF cache and no HF_TOKEN — would hang on download",
)
def test_train_on_responses_only_masks_correctly():
    try:
        from transformers import AutoTokenizer
    except ImportError:
        pytest.skip("transformers not installed")

    try:
        tokenizer = AutoTokenizer.from_pretrained(TOKENIZER_ID, trust_remote_code=True)
    except Exception as e:
        pytest.skip(f"tokenizer load failed: {e}")

    messages = [
        {"role": "system", "content": "You are an AI agent playing Kaetram, a 2D pixel MMORPG."},
        {"role": "user", "content": '{"pos":{"x":188,"y":157}}\n\nASCII_MAP:\n..P..'},
        {"role": "assistant", "content": "<think>\nI see I'm at the village center.\n</think>"},
        {"role": "user", "content": '{"result": "Navigated to Rick at (190, 155)"}'},
        {"role": "assistant", "content": "<think>\nI reached Rick. Time to talk.\n</think>"},
        {"role": "user", "content": '{"result": "Quest accepted: Kill 5 Rats"}'},
        {"role": "assistant", "content": "<think>\nGot the quest. Time to fight.\n</think>"},
    ]

    formatted = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
    input_ids = tokenizer.encode(formatted, add_special_tokens=False)
    instruction_ids = tokenizer.encode(INSTRUCTION_PART, add_special_tokens=False)
    response_ids = tokenizer.encode(RESPONSE_PART, add_special_tokens=False)

    assert instruction_ids, "user marker tokenized to empty — chat template changed?"
    assert response_ids, "assistant marker tokenized to empty — chat template changed?"

    labels = _simulate_masking(input_ids, instruction_ids, response_ids)
    masked = sum(1 for l in labels if l == -100)
    trained = sum(1 for l in labels if l != -100)

    # Each of the 3 assistant turns must have at least one trained token.
    assistant_turns_trained = 0
    j = 0
    while j < len(input_ids):
        pos = _find_token_pattern(input_ids, response_ids, j)
        if pos == -1:
            break
        content_start = pos + len(response_ids)
        if content_start < len(input_ids) and labels[content_start] != -100:
            assistant_turns_trained += 1
        j = content_start + 1

    assert assistant_turns_trained == 3, (
        f"Expected 3 assistant turns to be trained, got {assistant_turns_trained}. "
        f"train_on_responses_only masking has regressed."
    )
    assert trained > 0, "no tokens trained — masking is fully broken"
    assert masked > trained, (
        f"more tokens trained ({trained}) than masked ({masked}) — "
        f"system/user content is leaking into the loss"
    )
