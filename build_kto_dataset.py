#!/usr/bin/env python3
"""
build_kto_dataset.py — Build a KTO dataset from extracted Claude trajectories.

This script uses session-level desirable/undesirable labels plus local window
quality gates to produce prompt/completion/label examples for TRL KTOTrainer.
It keeps the same native-tool chat format as the SFT pipeline.

Usage:
    python3 score_sessions.py --input dataset/extracted/ --output dataset/qwen_kto/session_scores.json
    python3 build_kto_dataset.py --input dataset/extracted/ --scores dataset/qwen_kto/session_scores.json --output dataset/qwen_kto/
"""

import argparse
import json
import random
from pathlib import Path

from convert_to_qwen import (
    DEFAULT_MEMORY,
    PERSONALITY_SUFFIXES,
    SYSTEM_PROMPT,
    TOOL_DEFINITIONS,
    build_assistant_message,
    build_tool_result_message,
    build_user_message,
    detect_personality,
    find_latest_memory,
    is_desert_quest_waste,
    load_turns_by_session,
    score_turn,
)


def _window_score(turns: list[dict]) -> float:
    if not turns:
        return 0.0
    turn_scores = [score_turn(t) for t in turns]
    mean_turn_score = sum(turn_scores) / len(turn_scores)

    actions = [t.get("action_type", "") for t in turns]
    click_tile_frac = sum(1 for a in actions if a == "click_tile") / max(1, len(actions))
    repetitive = False
    for i in range(max(0, len(actions) - 2)):
        if actions[i] and actions[i] == actions[i + 1] == actions[i + 2]:
            repetitive = True
            break

    last_turn = turns[-1]
    last_action = last_turn.get("action_type", "")
    last_reasoning = (last_turn.get("reasoning") or "").strip()

    score = mean_turn_score
    if last_action in {"attack", "navigate", "interact_npc", "talk_npc", "quest_accept"}:
        score += 0.05
    if last_action == "click_tile" and len(last_reasoning) < 30:
        score -= 0.20
    score -= 0.20 * click_tile_frac
    if repetitive:
        score -= 0.25

    return max(0.0, min(1.0, score))


def _build_window_examples(
    session: str,
    session_turns: list[dict],
    session_label: bool,
    session_score: float,
    personality: str | None,
    window_size: int,
    stride: int,
    keep_personality: bool,
    positive_window_floor: float,
    negative_window_ceiling: float,
) -> list[dict]:
    sys_prompt = SYSTEM_PROMPT
    if keep_personality and personality and personality in PERSONALITY_SUFFIXES:
        sys_prompt += PERSONALITY_SUFFIXES[personality]

    records = []
    n = len(session_turns)
    starts = list(range(0, n, stride))
    if starts and starts[-1] + window_size < n:
        starts.append(max(0, n - window_size))

    for start in starts:
        end = min(start + window_size, n)
        raw_window = session_turns[start:end]

        window = []
        for t in raw_window:
            gs = t.get("game_state", {}) or {}
            if not gs.get("player_position"):
                continue
            if not t.get("action_structured"):
                continue
            if is_desert_quest_waste(t):
                continue
            if t.get("action_type") == "update_memory":
                continue
            window.append(t)

        if not window:
            continue

        local_score = _window_score(window)
        if session_label and local_score < positive_window_floor:
            continue
        if (not session_label) and local_score > negative_window_ceiling:
            continue

        memory = find_latest_memory(session_turns, start) or DEFAULT_MEMORY
        messages = [{"role": "system", "content": sys_prompt}]

        for i, turn in enumerate(window):
            prev = window[i - 1] if i > 0 else None
            mem = memory if i == 0 else None

            is_last = i == len(window) - 1
            asst_msg = build_assistant_message(turn, include_thinking=is_last)
            if asst_msg is None:
                continue
            messages.append({"role": "user", "content": build_user_message(turn, prev_turn=prev, memory=mem)})
            messages.append(asst_msg)

            if not is_last:
                tool_result = build_tool_result_message(turn)
                if tool_result is not None:
                    messages.append(tool_result)

        if len(messages) < 3:
            continue

        completion_message = messages[-1]
        if completion_message.get("role") != "assistant":
            continue

        prompt_messages = messages[:-1]
        records.append(
            {
                "session": session,
                "label": session_label,
                "session_score": round(session_score, 4),
                "window_score": round(local_score, 4),
                "prompt_messages": prompt_messages,
                "completion_message": completion_message,
            }
        )

    return records


def main():
    parser = argparse.ArgumentParser(description="Build KTO dataset from extracted Claude trajectories")
    parser.add_argument("--input", type=Path, required=True, help="Extracted dataset directory")
    parser.add_argument("--scores", type=Path, required=True, help="Session score JSON from score_sessions.py")
    parser.add_argument("--output", type=Path, required=True, help="Output dataset directory")
    parser.add_argument("--window-size", type=int, default=5, help="Sliding window size (default: 5)")
    parser.add_argument("--stride", type=int, default=2, help="Sliding window stride (default: 2)")
    parser.add_argument(
        "--val-ratio", type=float, default=0.10, help="Validation split ratio by session (default: 0.10)"
    )
    parser.add_argument(
        "--keep-personality",
        action="store_true",
        help="Preserve personality suffixes in prompts (default: off)",
    )
    parser.add_argument(
        "--positive-window-floor",
        type=float,
        default=0.45,
        help="Minimum local window score for positive-session examples (default: 0.45)",
    )
    parser.add_argument(
        "--negative-window-ceiling",
        type=float,
        default=0.60,
        help="Maximum local window score for negative-session examples (default: 0.60)",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    score_data = json.loads(args.scores.read_text())
    session_labels = {
        row["session"]: row
        for row in score_data["sessions"]
        if row.get("label") is not None
    }

    sessions_data = load_turns_by_session(args.input)
    if not sessions_data:
        raise SystemExit(f"No sessions found under {args.input}")

    records = []
    for session, turns in sorted(sessions_data.items()):
        label_row = session_labels.get(session)
        if not label_row:
            continue
        personality = detect_personality(session, args.input)
        records.extend(
            _build_window_examples(
                session=session,
                session_turns=turns,
                session_label=bool(label_row["label"]),
                session_score=float(label_row["score"]),
                personality=personality,
                window_size=args.window_size,
                stride=args.stride,
                keep_personality=args.keep_personality,
                positive_window_floor=args.positive_window_floor,
                negative_window_ceiling=args.negative_window_ceiling,
            )
        )

    if not records:
        raise SystemExit("No KTO examples produced. Loosen thresholds or check score coverage.")

    # Stratified val split: sample val sessions separately from desirable/undesirable pools
    # so val set reflects the same label balance as train, not whatever random luck gives.
    random.seed(args.seed)
    desirable_sessions = sorted(set(r["session"] for r in records if r["label"]))
    undesirable_sessions = sorted(set(r["session"] for r in records if not r["label"]))
    random.shuffle(desirable_sessions)
    random.shuffle(undesirable_sessions)
    n_val_des = max(1, int(len(desirable_sessions) * args.val_ratio))
    n_val_udes = max(1, int(len(undesirable_sessions) * args.val_ratio))
    val_sessions = set(desirable_sessions[:n_val_des]) | set(undesirable_sessions[:n_val_udes])

    train_records = []
    val_records = []
    for rec in records:
        session = rec["session"]
        if session in val_sessions:
            val_records.append(rec)
        else:
            train_records.append(rec)

    metadata = {
        "system_prompt": SYSTEM_PROMPT,
        "tools": TOOL_DEFINITIONS,
        "personality_suffixes": PERSONALITY_SUFFIXES,
        "keep_personality": args.keep_personality,
        "window_size": args.window_size,
        "stride": args.stride,
        "positive_window_floor": args.positive_window_floor,
        "negative_window_ceiling": args.negative_window_ceiling,
        "score_summary": score_data.get("summary", {}),
        "train_records": len(train_records),
        "val_records": len(val_records),
        "train_sessions": len({r["session"] for r in train_records}),
        "val_sessions": len({r["session"] for r in val_records}),
        "train_desirable": sum(1 for r in train_records if r["label"]),
        "train_undesirable": sum(1 for r in train_records if not r["label"]),
    }

    args.output.mkdir(parents=True, exist_ok=True)
    with open(args.output / "train.json", "w") as f:
        json.dump(train_records, f, indent=2)
    with open(args.output / "val.json", "w") as f:
        json.dump(val_records, f, indent=2)
    with open(args.output / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
