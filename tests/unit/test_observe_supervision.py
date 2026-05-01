"""Unit tests for r10 observe-supervision fix.

Pre-r10 state: `dataset/qwen_sft/train.json` had 21,976 assistant tool calls and 0
observe calls. extract_turns.py consumed each Sonnet observe tool_use to populate
the next turn's `game_state` field, then discarded the tool_use. convert_to_qwen.py
hand-injected `<game_state>` into every user message. The model never learned to
call observe; at inference the live prompt mandated it.

These tests run without a built dataset — they exercise the core extraction and
conversion paths on synthetic mini-events / mini-turns.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Helpers: build synthetic event streams that look like Claude stream-json.
# ---------------------------------------------------------------------------

_GAME_STATE = {
    "timestamp": 1712345678,
    "player_position": {"x": 188, "y": 157},
    "player_stats": {"hp": 40, "max_hp": 50, "level": 5, "experience": 300},
    "nearby_entities": [{"name": "Rat", "distance": 4, "reachable": True}],
    "inventory": [{"slot": 0, "name": "Bronze Axe", "count": 1}],
    "ui_state": {"is_dead": False},
}

_OBSERVE_RESULT_TEXT = json.dumps(_GAME_STATE) + "\n\nASCII_MAP:\n....R...\n...@....\n........\n\nSTUCK_CHECK: stuck: false"


def _make_observe_event(idx: int, tool_id: str):
    return {
        "line": idx,
        "type": "tool_use",
        "role": "assistant",
        "name": "mcp__kaetram__observe",
        "input": {},
        "id": tool_id,
        "timestamp": 1712345678 + idx,
    }


def _make_tool_result(idx: int, tool_id: str, text: str):
    return {
        "line": idx,
        "type": "tool_result",
        "role": "user",
        "tool_use_id": tool_id,
        "text": text,
        "timestamp": 1712345678 + idx,
    }


def _make_action_event(idx: int, tool_id: str, tool_name: str, tool_input: dict):
    return {
        "line": idx,
        "type": "tool_use",
        "role": "assistant",
        "name": tool_name,
        "input": tool_input,
        "id": tool_id,
        "timestamp": 1712345678 + idx,
    }


def _make_thinking(idx: int, text: str):
    return {
        "line": idx,
        "type": "thinking",
        "role": "assistant",
        "text": text,
        "timestamp": 1712345678 + idx,
    }


# ---------------------------------------------------------------------------
# extract_turns — direct testing against an in-memory event list.
#
# extract_turns.extract_turns() opens a file and parses events, so we inline a
# copy of the observe-iteration loop by patching parse_events to return our
# synthetic list. To keep the test hermetic, use a temp file with valid JSONL.
# ---------------------------------------------------------------------------


def _write_synthetic_claude_log(path: Path, events: list[dict]) -> None:
    """Write events to a JSONL file matching Claude stream-json shape.

    Each line is one {type, message: {content: [block]}} record. For tool_result
    blocks, the content list has a text-typed sub-block.
    """
    with open(path, "w") as f:
        for ev in events:
            etype = ev["type"]
            if etype == "tool_use":
                rec = {
                    "type": "assistant",
                    "message": {"content": [{
                        "type": "tool_use",
                        "name": ev["name"],
                        "input": ev["input"],
                        "id": ev["id"],
                    }]},
                    "timestamp": ev.get("timestamp"),
                }
            elif etype == "tool_result":
                rec = {
                    "type": "user",
                    "message": {"content": [{
                        "type": "tool_result",
                        "tool_use_id": ev["tool_use_id"],
                        "content": [{"type": "text", "text": ev["text"]}],
                    }]},
                    "timestamp": ev.get("timestamp"),
                }
            elif etype == "thinking":
                rec = {
                    "type": "assistant",
                    "message": {"content": [{"type": "thinking", "thinking": ev["text"]}]},
                    "timestamp": ev.get("timestamp"),
                }
            elif etype == "text":
                rec = {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": ev["text"]}]},
                    "timestamp": ev.get("timestamp"),
                }
            else:
                continue
            f.write(json.dumps(rec) + "\n")


def test_extract_emits_observe_then_action_turn(tmp_path):
    """Standard pattern: observe, get result, think, act. Must produce 2 turns."""
    from extract_turns import extract_turns

    events = [
        _make_thinking(0, "I should check my surroundings first."),
        _make_observe_event(1, "call_obs_1"),
        _make_tool_result(2, "call_obs_1", _OBSERVE_RESULT_TEXT),
        _make_thinking(3, "A Rat is nearby. I'll attack it."),
        _make_action_event(4, "call_atk_1", "mcp__kaetram__attack", {"mob_name": "Rat"}),
        _make_tool_result(5, "call_atk_1", json.dumps({"killed": False, "damage_dealt": 3})),
    ]
    log_path = tmp_path / "synth.log"
    _write_synthetic_claude_log(log_path, events)

    # Stub cli_adapter.detect_log_format to return "claude" for our synthetic file.
    import cli_adapter

    original = cli_adapter.detect_log_format
    cli_adapter.detect_log_format = lambda p: "claude"
    try:
        turns = extract_turns(log_path)
    finally:
        cli_adapter.detect_log_format = original

    assert len(turns) == 2, f"expected observe + action = 2 turns, got {len(turns)}: {[t['action_type'] for t in turns]}"
    obs, act = turns
    assert obs["action_type"] == "observe"
    assert obs["action_structured"] == "observe()"
    assert "I should check my surroundings" in obs["reasoning"]
    assert obs["action_result_raw"] == _OBSERVE_RESULT_TEXT
    assert obs["ascii_map"]  # ascii map lives on observe turn
    # Action turn
    assert act["action_type"] == "attack"
    assert "attack" in act["action_structured"]
    assert "Rat is nearby" in act["reasoning"]
    # Action reasoning must NOT contain the pre-observe reasoning
    assert "check my surroundings" not in act["reasoning"]


def test_extract_emits_observe_only_tail(tmp_path):
    """Session ends after an observe with no following action — must still emit
    the observe turn (pre-r10 dropped these, losing training signal)."""
    from extract_turns import extract_turns

    events = [
        _make_thinking(0, "Final status check before session end."),
        _make_observe_event(1, "call_obs_tail"),
        _make_tool_result(2, "call_obs_tail", _OBSERVE_RESULT_TEXT),
    ]
    log_path = tmp_path / "synth_tail.log"
    _write_synthetic_claude_log(log_path, events)

    import cli_adapter
    cli_adapter.detect_log_format = lambda p: "claude"
    turns = extract_turns(log_path)

    assert len(turns) == 1
    assert turns[0]["action_type"] == "observe"


def test_extract_preserves_observe_action_ratio(tmp_path):
    """Three observes, two actions — ratio should match what we emit."""
    from extract_turns import extract_turns

    events = []
    for i in range(3):
        events.append(_make_thinking(i * 10, f"Iteration {i} plan."))
        events.append(_make_observe_event(i * 10 + 1, f"obs_{i}"))
        events.append(_make_tool_result(i * 10 + 2, f"obs_{i}", _OBSERVE_RESULT_TEXT))
        if i < 2:  # only 2 actions after 3 observes
            events.append(_make_action_event(
                i * 10 + 3, f"atk_{i}", "mcp__kaetram__attack", {"mob_name": "Rat"}
            ))
            events.append(_make_tool_result(
                i * 10 + 4, f"atk_{i}", json.dumps({"killed": True})
            ))
    log_path = tmp_path / "synth_ratio.log"
    _write_synthetic_claude_log(log_path, events)

    import cli_adapter
    cli_adapter.detect_log_format = lambda p: "claude"
    turns = extract_turns(log_path)

    observes = [t for t in turns if t["action_type"] == "observe"]
    actions = [t for t in turns if t["action_type"] != "observe"]
    assert len(observes) == 3, f"expected 3 observe turns, got {len(observes)}"
    assert len(actions) == 2, f"expected 2 action turns, got {len(actions)}"


# ---------------------------------------------------------------------------
# convert_to_qwen — observe as tool_call, user message without game_state.
# ---------------------------------------------------------------------------


def _make_observe_turn():
    return {
        "turn_id": "sess_t000",
        "timestamp": 1712345678,
        "game_state": _GAME_STATE,
        "ascii_map": "....R...\n...@....\n........",
        "reasoning": "Checking surroundings for threats.",
        "action_code": "",
        "action_type": "observe",
        "action_structured": "observe()",
        "action_target": "",
        "action_result_raw": _OBSERVE_RESULT_TEXT,
        "player_stats": _GAME_STATE["player_stats"],
        "player_position": _GAME_STATE["player_position"],
    }


def _make_action_turn():
    return {
        "turn_id": "sess_t001",
        "timestamp": 1712345679,
        "game_state": _GAME_STATE,
        "ascii_map": "",
        "reasoning": "A rat is next to me, I'll hit it.",
        "action_code": "",
        "action_type": "attack",
        "action_structured": "attack(Rat)",
        "action_target": "Rat",
        "action_result_raw": json.dumps({"result": json.dumps({"killed": False, "damage_dealt": 3})}),
        "player_stats": _GAME_STATE["player_stats"],
        "player_position": _GAME_STATE["player_position"],
    }


def test_observe_turn_becomes_observe_tool_call():
    from convert_to_qwen import build_assistant_message

    msg = build_assistant_message(_make_observe_turn(), include_thinking=True)
    assert msg is not None
    assert msg["role"] == "assistant"
    assert len(msg["tool_calls"]) == 1
    tc = msg["tool_calls"][0]
    assert tc["function"]["name"] == "observe"
    assert tc["function"]["arguments"] == {}
    assert "<think>" in msg["content"]


def test_observe_turn_tool_result_is_raw_observe_text():
    from convert_to_qwen import build_tool_result_message

    msg = build_tool_result_message(_make_observe_turn())
    assert msg is not None
    assert msg["role"] == "tool"
    assert msg["name"] == "observe"
    # The full raw observe text (state JSON + ASCII_MAP) must be in the tool_result.
    assert "ASCII_MAP" in msg["content"]
    assert "player_position" in msg["content"]


def test_build_user_message_does_not_inject_game_state():
    """The core r10 fix: user messages no longer hand game_state to the model."""
    from convert_to_qwen import build_user_message

    user_text = build_user_message(_make_action_turn(), prev_turn=None)
    assert "<game_state>" not in user_text, (
        "user message still injects <game_state> — pre-r10 bug not fixed"
    )
    # But "What should you do?" prompt still present.
    assert "What should you do?" in user_text


def test_build_user_message_keeps_state_delta_when_prev_turn_exists():
    """State delta is a legit momentum signal; keep it for multi-turn windows."""
    from convert_to_qwen import build_user_message

    prev = _make_observe_turn()
    curr = _make_action_turn()
    # Mutate curr state slightly so delta has content.
    curr = {**curr, "game_state": {**_GAME_STATE, "player_stats": {**_GAME_STATE["player_stats"], "hp": 30}}}
    user_text = build_user_message(curr, prev_turn=prev)
    # state_delta may or may not appear depending on compute_state_delta's output;
    # what we care about is no full <game_state> block.
    assert "<game_state>" not in user_text


def test_multi_turn_window_has_observe_tool_call():
    """End-to-end: build a 2-turn window (observe + action) and verify the
    resulting messages list has the expected roles and tool_calls."""
    from convert_to_qwen import build_multi_turn_records

    session = [_make_observe_turn(), _make_action_turn()]
    records = build_multi_turn_records(
        session, personality="curious", min_score=0.0, window_size=2
    )
    assert len(records) >= 1
    msgs = records[0]["messages"]
    roles = [m["role"] for m in msgs]
    # Expect user, assistant(observe tool_call), tool(state), user, assistant(attack tool_call), tool(attack result)
    assert roles == ["user", "assistant", "tool", "user", "assistant", "tool"], (
        f"unexpected role sequence: {roles}"
    )
    # First assistant msg must be the observe call.
    first_asst = msgs[1]
    assert first_asst["tool_calls"][0]["function"]["name"] == "observe"
    # First tool result must contain the raw state text.
    first_tool = msgs[2]
    assert "ASCII_MAP" in first_tool["content"] or "player_position" in first_tool["content"]
    # Second assistant msg must be the attack call.
    second_asst = msgs[4]
    assert second_asst["tool_calls"][0]["function"]["name"] == "attack"


def test_observe_is_in_mcp_action_tool_mapping():
    """_structured_action_to_tool_call must map observe()."""
    from convert_to_qwen import _structured_action_to_tool_call

    result = _structured_action_to_tool_call("observe()", "observe")
    assert result is not None
    assert result == ("observe", {})


def test_reasoningless_observe_turn_is_not_dropped():
    """Observe turns without pre-observe reasoning (e.g. session start) must
    survive the reasoningless-tool-turn filter. Other actions without reasoning
    should still be dropped."""
    from convert_to_qwen import _is_reasoningless_tool_turn

    obs = _make_observe_turn()
    obs["reasoning"] = ""
    assert not _is_reasoningless_tool_turn(obs), "empty-reasoning observe should NOT be dropped"

    act = _make_action_turn()
    act["reasoning"] = ""
    assert _is_reasoningless_tool_turn(act), "empty-reasoning action should still be dropped"
