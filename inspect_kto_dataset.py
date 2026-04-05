#!/usr/bin/env python3
"""
inspect_kto_dataset.py — Local dry-run to inspect rendered KTO examples before Modal launch.

Renders a sample of prompt/completion pairs using the actual Qwen3.5 chat template,
shows label balance, session distribution, and length stats. Run this before
modal run finetune/train_kto_modal.py to confirm data looks right.

Usage:
    python3 inspect_kto_dataset.py
    python3 inspect_kto_dataset.py --dataset dataset/qwen_kto/ --n 3
"""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Inspect rendered KTO dataset examples")
    parser.add_argument("--dataset", type=Path, default=Path("dataset/qwen_kto"), help="KTO dataset directory")
    parser.add_argument("--n", type=int, default=2, help="Number of examples to render per label (default: 2)")
    parser.add_argument("--render", action="store_true", help="Render via chat template (requires transformers + tokenizer)")
    args = parser.parse_args()

    train_path = args.dataset / "train.json"
    val_path = args.dataset / "val.json"
    meta_path = args.dataset / "metadata.json"

    for p in [train_path, val_path, meta_path]:
        if not p.exists():
            raise SystemExit(f"Missing: {p}\nRun build_kto_dataset.py first.")

    train_records = json.loads(train_path.read_text())
    val_records = json.loads(val_path.read_text())
    meta = json.loads(meta_path.read_text())

    def stats(records, name):
        des = sum(1 for r in records if r["label"])
        udes = len(records) - des
        sessions = len(set(r["session"] for r in records if r.get("session")))
        prompt_msgs = [r["prompt_messages"] for r in records]
        completion_msgs = [r["completion_message"] for r in records]
        # Rough char length of prompt (all messages joined)
        prompt_lens = [sum(len(str(m.get("content", ""))) for m in pm) for pm in prompt_msgs]
        completion_lens = [len(str(cm.get("content", "") or "") + str(cm.get("tool_calls", ""))) for cm in completion_msgs]
        avg_pl = sum(prompt_lens) / max(1, len(prompt_lens))
        avg_cl = sum(completion_lens) / max(1, len(completion_lens))
        print(f"\n{name}: {len(records)} records | {des} desirable ({100*des/max(1,len(records)):.1f}%) | {udes} undesirable | {sessions} sessions")
        print(f"  Avg prompt chars: {avg_pl:.0f} | Avg completion chars: {avg_cl:.0f}")
        return des, udes

    print("=" * 60)
    print("KTO DATASET INSPECTION")
    print("=" * 60)
    print(f"Window size: {meta.get('window_size')} | Stride: {meta.get('stride')}")
    print(f"Positive window floor: {meta.get('positive_window_floor')} | Negative ceiling: {meta.get('negative_window_ceiling')}")
    score_summary = meta.get("score_summary", {})
    if score_summary:
        print(f"Scorer: {score_summary.get('sessions')} sessions | desirable={score_summary.get('desirable_sessions')} | undesirable={score_summary.get('undesirable_sessions')} | neutral={score_summary.get('neutral_sessions')}")

    train_des, train_udes = stats(train_records, "TRAIN")
    val_des, val_udes = stats(val_records, "VAL")

    if train_des == 0 or train_udes == 0:
        print(f"\nERROR: train set missing a class — desirable={train_des}, undesirable={train_udes}")
    if val_des == 0 or val_udes == 0:
        print(f"\nERROR: val set missing a class — desirable={val_des}, undesirable={val_udes}")

    # Sample examples per label
    print(f"\n{'='*60}")
    print(f"SAMPLE EXAMPLES (raw messages, not rendered)")
    print(f"{'='*60}")

    for label, label_name in [(True, "DESIRABLE"), (False, "UNDESIRABLE")]:
        examples = [r for r in train_records if r["label"] == label][:args.n]
        for i, rec in enumerate(examples):
            print(f"\n[{label_name} #{i+1}] session={rec.get('session')} session_score={rec.get('session_score'):.3f} window_score={rec.get('window_score'):.3f}")
            # Last user message (the game state prompt)
            user_msgs = [m for m in rec["prompt_messages"] if m["role"] == "user"]
            if user_msgs:
                content = str(user_msgs[-1].get("content", ""))[:300]
                print(f"  Last user msg ({len(str(user_msgs[-1].get('content','')))} chars): {content!r}...")
            # Completion
            cm = rec["completion_message"]
            tool_calls = cm.get("tool_calls", [])
            if tool_calls:
                tc = tool_calls[0]
                fn = tc.get("function", {})
                print(f"  Completion tool: {fn.get('name')}({json.dumps(fn.get('arguments', {}))[:80]})")
            content = str(cm.get("content", ""))[:200]
            if content:
                print(f"  Completion content ({len(str(cm.get('content','')))} chars): {content!r}...")

    if args.render:
        print(f"\n{'='*60}")
        print("RENDERED (chat template applied)")
        print(f"{'='*60}")
        try:
            from transformers import AutoTokenizer
            tok = AutoTokenizer.from_pretrained("Qwen/Qwen3.5-9B", trust_remote_code=True)
            tools = meta.get("tools", [])
            examples = [r for r in train_records[:2]]
            for i, rec in enumerate(examples):
                label_str = "DESIRABLE" if rec["label"] else "UNDESIRABLE"
                prompt_text = tok.apply_chat_template(rec["prompt_messages"], tools=tools, tokenize=False, add_generation_prompt=True)
                full_text = tok.apply_chat_template(rec["prompt_messages"] + [rec["completion_message"]], tools=tools, tokenize=False, add_generation_prompt=False)
                completion_text = full_text[len(prompt_text):]
                print(f"\n[{label_str} #{i+1}]")
                print(f"  PROMPT ({len(prompt_text)} chars, last 300): ...{prompt_text[-300:]!r}")
                print(f"  COMPLETION ({len(completion_text)} chars): {completion_text[:300]!r}...")
        except Exception as e:
            print(f"Render failed: {e}")
            print("Install transformers and run without --render for raw inspection")

    print(f"\n{'='*60}")
    print("If everything looks right, run:")
    print("  modal run finetune/train_kto_modal.py --smoke-test")
    print("Then if smoke test passes:")
    print("  modal run finetune/train_kto_modal.py")
    print("=" * 60)


if __name__ == "__main__":
    main()
