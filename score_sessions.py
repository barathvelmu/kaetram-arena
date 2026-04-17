#!/usr/bin/env python3
"""
score_sessions.py — Score extracted Claude gameplay sessions for KTO labeling.

Builds continuous session scores from gameplay outcomes and behavior quality,
then assigns binary desirable/undesirable labels by percentile bands. This is
the missing bridge between cleaned SFT data and a KTO dataset.

Usage:
    python3 score_sessions.py --input dataset/extracted/ --output dataset/qwen_kto/session_scores.json
"""

import argparse
import json
import math
from pathlib import Path

from convert_to_qwen import compute_state_delta, load_turns_by_session, score_turn


def _clamp(val: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, val))


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if pct <= 0:
        return sorted_vals[0]
    if pct >= 1:
        return sorted_vals[-1]
    idx = (len(sorted_vals) - 1) * pct
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return sorted_vals[lo]
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _player_stats(turn: dict) -> dict:
    gs = turn.get("game_state", {}) or {}
    ps = gs.get("player_stats", {}) or {}
    if isinstance(ps, str):
        try:
            ps = json.loads(ps)
        except (json.JSONDecodeError, ValueError):
            ps = {}
    if not isinstance(ps, dict):
        ps = {}
    return ps


def _xp_of(turn: dict) -> int:
    ps = _player_stats(turn)
    return int(ps.get("experience", 0) or 0)


def _level_of(turn: dict) -> int:
    ps = _player_stats(turn)
    return int(ps.get("level", 1) or 1)


def _pos_of(turn: dict) -> tuple[int, int] | None:
    gs = turn.get("game_state", {}) or {}
    pp = gs.get("player_position", {}) or {}
    if isinstance(pp, dict) and "x" in pp and "y" in pp:
        return int(pp["x"]), int(pp["y"])
    return None


def score_session(session: str, turns: list[dict]) -> dict:
    # r10: filter out observe turns from session scoring. Observe is a preparatory
    # action (refresh state) that doesn't reflect gameplay quality, and its
    # interleaving would halve rate metrics (attack_rate, click_tile_rate, etc.)
    # vs pre-r10 scoring. Action turns are what KTO preference learning judges.
    turns = [t for t in turns if t.get("action_type") != "observe"]
    actions = [t.get("action_type", "") for t in turns]
    n_turns = len(turns)
    turn_scores = [score_turn(t) for t in turns]

    xp_start = _xp_of(turns[0]) if turns else 0
    xp_end = _xp_of(turns[-1]) if turns else 0
    xp_delta = max(0, xp_end - xp_start)

    level_start = _level_of(turns[0]) if turns else 1
    level_end = _level_of(turns[-1]) if turns else 1
    level_delta = max(0, level_end - level_start)

    respawn_count = sum(1 for a in actions if a == "respawn")
    click_tile_count = sum(1 for a in actions if a == "click_tile")
    quest_action_count = sum(
        1 for a in actions if a in {"interact_npc", "talk_npc", "quest_accept", "query_quest"}
    )
    stuck_action_count = sum(1 for a in actions if a in {"stuck_reset", "nav_cancel"})
    attack_count = sum(1 for a in actions if a == "attack")
    gather_count = sum(1 for a in actions if a == "gather")
    loot_count = sum(1 for a in actions if a == "loot")
    buy_count = sum(1 for a in actions if a == "buy_item")
    clear_combat_count = sum(1 for a in actions if a == "clear_combat")

    repetitive_triples = 0
    for i in range(max(0, len(actions) - 2)):
        if actions[i] and actions[i] == actions[i + 1] == actions[i + 2]:
            repetitive_triples += 1

    unique_positions = {
        pos for pos in (_pos_of(t) for t in turns) if pos is not None
    }

    progress_events = 0
    death_flags = 0
    quest_stages_advanced = 0
    quests_completed = 0
    quests_accepted = 0
    for i in range(len(turns) - 1):
        delta = compute_state_delta(
            turns[i].get("game_state", {}) or {},
            turns[i + 1].get("game_state", {}) or {},
        )
        if delta.get("xp_delta", 0) > 0 or delta.get("level_delta", 0) > 0:
            progress_events += 1
        if delta.get("died"):
            death_flags += 1
        quest_stages_advanced += delta.get("quest_stage_advances", 0)
        quests_completed += delta.get("quest_completions", 0)
        quests_accepted += len(delta.get("new_quests", []))

    click_tile_rate = click_tile_count / max(1, n_turns)
    stuck_rate = stuck_action_count / max(1, n_turns)
    repetitive_ratio = repetitive_triples / max(1, n_turns - 2)
    attack_rate = attack_count / max(1, n_turns)
    avg_turn_score = sum(turn_scores) / max(1, len(turn_scores))

    # Resource / economy activity: gathering, looting and shop use all indicate
    # engaged, diversified play. Treated as a mild positive, capped low so it
    # can never outweigh XP / quest progress.
    economy_actions = gather_count + loot_count + buy_count
    economy_score = _clamp(economy_actions / 6.0)

    # Quest progression: completions worth most, stage advances next, accepts least
    quest_progress_score = _clamp(
        (quests_completed * 1.0 + quest_stages_advanced * 0.4 + quests_accepted * 0.2) / 2.0
    )

    positive = 0.0
    positive += 0.15 * _clamp(xp_delta / 300.0)
    positive += 0.13 * _clamp(level_delta / 3.0)
    positive += 0.20 * quest_progress_score
    positive += 0.10 * _clamp(progress_events / 4.0)
    positive += 0.14 * _clamp(len(unique_positions) / 20.0)
    positive += 0.13 * _clamp(avg_turn_score)
    positive += 0.05 * economy_score

    negative = 0.0
    negative += 0.20 * _clamp(respawn_count / 2.0)
    negative += 0.25 * _clamp(click_tile_rate / 0.35)
    negative += 0.20 * _clamp(repetitive_ratio / 0.15)
    negative += 0.10 * _clamp(stuck_rate / 0.20)
    negative += 0.10 * _clamp(death_flags / 2.0)

    raw_score = positive - negative
    score = _clamp(0.5 + (raw_score / 2.0))

    return {
        "session": session,
        "turns": n_turns,
        "xp_start": xp_start,
        "xp_end": xp_end,
        "xp_delta": xp_delta,
        "level_start": level_start,
        "level_end": level_end,
        "level_delta": level_delta,
        "avg_turn_score": round(avg_turn_score, 4),
        "respawn_count": respawn_count,
        "death_flags": death_flags,
        "click_tile_count": click_tile_count,
        "click_tile_rate": round(click_tile_rate, 4),
        "quest_action_count": quest_action_count,
        "quest_stages_advanced": quest_stages_advanced,
        "quests_completed": quests_completed,
        "quests_accepted": quests_accepted,
        "quest_progress_score": round(quest_progress_score, 4),
        "stuck_action_count": stuck_action_count,
        "stuck_rate": round(stuck_rate, 4),
        "attack_count": attack_count,
        "attack_rate": round(attack_rate, 4),
        "gather_count": gather_count,
        "loot_count": loot_count,
        "buy_count": buy_count,
        "clear_combat_count": clear_combat_count,
        "economy_score": round(economy_score, 4),
        "repetitive_triples": repetitive_triples,
        "repetitive_ratio": round(repetitive_ratio, 4),
        "unique_positions": len(unique_positions),
        "progress_events": progress_events,
        "score": round(score, 4),
    }


def main():
    parser = argparse.ArgumentParser(description="Score extracted sessions for KTO labels")
    parser.add_argument("--input", type=Path, required=True, help="Extracted dataset directory")
    parser.add_argument("--output", type=Path, required=True, help="Output JSON file")
    parser.add_argument(
        "--desirable-top-pct",
        type=float,
        default=0.40,
        help="Top fraction of sessions labeled desirable (default: 0.40)",
    )
    parser.add_argument(
        "--undesirable-bottom-pct",
        type=float,
        default=0.30,
        help="Bottom fraction of sessions labeled undesirable (default: 0.30)",
    )
    args = parser.parse_args()

    sessions = load_turns_by_session(args.input)
    if not sessions:
        raise SystemExit(f"No sessions found under {args.input}")

    scored = [score_session(session, turns) for session, turns in sorted(sessions.items()) if turns]
    score_values = sorted(s["score"] for s in scored)

    desirable_threshold = _percentile(score_values, 1.0 - args.desirable_top_pct)
    undesirable_threshold = _percentile(score_values, args.undesirable_bottom_pct)

    desirable = 0
    undesirable = 0
    for row in scored:
        label = None
        if row["score"] >= desirable_threshold:
            label = True
            desirable += 1
        elif row["score"] <= undesirable_threshold:
            label = False
            undesirable += 1
        row["label"] = label

    summary = {
        "sessions": len(scored),
        "desirable_sessions": desirable,
        "undesirable_sessions": undesirable,
        "neutral_sessions": len(scored) - desirable - undesirable,
        "desirable_threshold": round(desirable_threshold, 4),
        "undesirable_threshold": round(undesirable_threshold, 4),
        "desirable_top_pct": args.desirable_top_pct,
        "undesirable_bottom_pct": args.undesirable_bottom_pct,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump({"summary": summary, "sessions": scored}, f, indent=2)

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
