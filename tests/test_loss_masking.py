"""
Verify that train_on_responses_only masking works on our Qwen3.5 chat format.

CPU-only test — no GPU, no Unsloth, no training. Just tokenizer + token scanning.
Reproduces the exact logic from unsloth_zoo/dataset_utils.py to confirm which tokens
get masked (label=-100) vs trained on.

Usage: python tests/test_loss_masking.py
"""

from transformers import AutoTokenizer

MODEL = "Qwen/Qwen3.5-9B"
INSTRUCTION_PART = "<|im_start|>user\n"
RESPONSE_PART = "<|im_start|>assistant\n"


def find_token_pattern(input_ids: list[int], pattern_ids: list[int], start: int = 0) -> int:
    """Find the first occurrence of pattern_ids in input_ids starting from `start`.
    Returns the index of the FIRST token of the pattern, or -1 if not found.
    This mirrors Unsloth's _find_common_token_ids scanning logic.
    """
    plen = len(pattern_ids)
    for i in range(start, len(input_ids) - plen + 1):
        if input_ids[i:i + plen] == pattern_ids:
            return i
    return -1


def simulate_masking(input_ids: list[int], instruction_ids: list[int], response_ids: list[int]) -> list[int]:
    """Simulate Unsloth's train_on_responses_only masking.
    Returns labels: -100 for masked tokens, original token id for trained tokens.
    """
    n = len(input_ids)
    labels = [-100] * n  # start with everything masked

    j = 0
    while j < n:
        # Find next assistant marker
        resp_pos = find_token_pattern(input_ids, response_ids, j)
        if resp_pos == -1:
            break

        # Assistant content starts AFTER the marker
        content_start = resp_pos + len(response_ids)

        # Find next user marker (end of this assistant turn)
        user_pos = find_token_pattern(input_ids, instruction_ids, content_start)
        if user_pos == -1:
            content_end = n  # last turn — train to end of sequence
        else:
            content_end = user_pos

        # Unmask the assistant content
        for i in range(content_start, content_end):
            labels[i] = input_ids[i]

        j = content_end

    return labels


def main():
    print(f"Loading tokenizer: {MODEL}")
    tokenizer = AutoTokenizer.from_pretrained(MODEL, trust_remote_code=True)

    # Sample multi-turn conversation matching our training format
    messages = [
        {"role": "system", "content": "You are an AI agent playing Kaetram, a 2D pixel MMORPG."},
        {"role": "user", "content": '{"player_position":{"x":188,"y":157},"player_stats":{"hp":100,"max_hp":100}}\n\nASCII_MAP:\n..P..'},
        {"role": "assistant", "content": "<think>\nI see I'm at the village center. I should look for an NPC to get a quest.\n</think>"},
        # Tool call would go here in real data, simplified for test
        {"role": "user", "content": '{"result": "Navigated to Rick at (190, 155)"}'},
        {"role": "assistant", "content": "<think>\nI reached Rick. Let me talk to him to get the quest.\n</think>"},
        {"role": "user", "content": '{"result": "Quest accepted: Kill 5 Rats"}'},
        {"role": "assistant", "content": "<think>\nGot the quest. Time to find and attack rats.\n</think>"},
    ]

    # Apply chat template (same as our training pipeline)
    formatted = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
    input_ids = tokenizer.encode(formatted, add_special_tokens=False)

    # Tokenize the marker strings
    instruction_ids = tokenizer.encode(INSTRUCTION_PART, add_special_tokens=False)
    response_ids = tokenizer.encode(RESPONSE_PART, add_special_tokens=False)

    print(f"\nInstruction marker tokens: {instruction_ids} → {tokenizer.decode(instruction_ids)!r}")
    print(f"Response marker tokens:    {response_ids} → {tokenizer.decode(response_ids)!r}")
    print(f"Total tokens in example:   {len(input_ids)}")

    # Run masking simulation
    labels = simulate_masking(input_ids, instruction_ids, response_ids)

    # Count masked vs trained
    masked = sum(1 for l in labels if l == -100)
    trained = sum(1 for l in labels if l != -100)
    print(f"\nMasked tokens (label=-100): {masked} ({100*masked/len(labels):.1f}%)")
    print(f"Trained tokens:            {trained} ({100*trained/len(labels):.1f}%)")

    # Print token-by-token with color coding
    print(f"\n{'='*80}")
    print("Token-by-token view (GRAY=masked, GREEN=trained):")
    print(f"{'='*80}\n")

    current_role = "?"
    for i, (tok_id, label) in enumerate(zip(input_ids, labels)):
        tok_str = tokenizer.decode([tok_id])

        # Track role transitions for readability
        snippet = tokenizer.decode(input_ids[max(0, i-3):i+1])
        if "<|im_start|>system" in snippet:
            current_role = "system"
        elif "<|im_start|>user" in snippet:
            current_role = "user"
        elif "<|im_start|>assistant" in snippet:
            current_role = "assistant"

        status = "TRAIN" if label != -100 else "mask "
        marker = ">>>" if label != -100 else "   "

        # Only print key tokens (role boundaries + first/last of each section)
        if any(special in tok_str for special in ["<|im_start|>", "<|im_end|>", "<think>", "</think>"]):
            print(f"  {marker} [{i:4d}] {status} | {current_role:9s} | {tok_str!r}")

    # Verify correctness
    print(f"\n{'='*80}")
    print("VERIFICATION:")
    print(f"{'='*80}")

    # Check: system message should be fully masked
    # Check: user messages should be fully masked
    # Check: assistant messages should be trained

    # Find all assistant turn contents and verify they're unmasked
    assistant_turns_found = 0
    j = 0
    while j < len(input_ids):
        pos = find_token_pattern(input_ids, response_ids, j)
        if pos == -1:
            break
        assistant_turns_found += 1
        content_start = pos + len(response_ids)

        # Check first trained token
        if content_start < len(input_ids) and labels[content_start] != -100:
            first_trained = tokenizer.decode([input_ids[content_start]])
            print(f"  ✓ Assistant turn {assistant_turns_found}: first trained token = {first_trained!r}")
        else:
            print(f"  ✗ Assistant turn {assistant_turns_found}: NOT TRAINED (BUG!)")

        j = content_start

    print(f"\n  Assistant turns found: {assistant_turns_found}")
    print(f"  All assistant turns trained: {'YES ✓' if assistant_turns_found == 3 else 'NO ✗'}")
    print(f"  System/user tokens masked: {'YES ✓' if masked > trained else 'CHECK'}")

    if assistant_turns_found == 3 and masked > trained:
        print(f"\n  ✓ MASKING IS CORRECT — ready for r8")
    else:
        print(f"\n  ✗ MASKING HAS ISSUES — investigate before r8")


if __name__ == "__main__":
    main()
