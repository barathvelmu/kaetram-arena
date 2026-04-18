#!/usr/bin/env python3
"""Tests for the data-pipeline drift fixes that just landed in:

  - extract_turns.py        (accept_quest typo, 6 new tools, action_result_raw)
  - convert_to_qwen.py      (TOOL_DEFINITIONS, _prefer_real_tool_result,
                             build_tool_result_message preference, synthesizer
                             no longer hardcodes quest_opened=false)
  - score_sessions.py       (cancel_nav -> nav_cancel drift fix)
  - finetune/train_grpo_modal.py (valid_actions extended)
  - build_kto_dataset.py    (last-action bonus allowlist extended)

These tests are designed to FAIL on the pre-patch code and PASS on the
current (patched) code, proving the fixes work.
"""

from __future__ import annotations

import inspect
import json
import sys
import tempfile
from pathlib import Path

import pytest

# Make the repo root importable when run as `pytest tests/...` or directly.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import extract_turns  # noqa: E402
import convert_to_qwen  # noqa: E402
import score_sessions  # noqa: E402
from extract_turns import (  # noqa: E402
    classify_action,
    extract_turns as run_extract_turns,
    is_browser_action,
    structured_action,
)
from convert_to_qwen import (  # noqa: E402
    TOOL_DEFINITIONS,
    _prefer_real_tool_result,
    _structured_action_to_tool_call,
    build_tool_result_message,
    synthesize_tool_result,
)


NEW_TOOLS = ("gather", "loot", "buy_item", "drop_item", "clear_combat", "query_quest")
CURATED_VISIBLE_TOOLS = ("gather", "loot", "buy_item", "drop_item", "query_quest", "craft_item")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_observe_state(x: int = 188, y: int = 157, hp: int = 50) -> str:
    """Return the raw `text` payload for an observe tool_result.

    Mirrors the real MCP server output: a JSON-stringified dict with a
    `result` key whose value is the inner JSON game state plus an
    ASCII_MAP suffix.
    """
    inner_state = {
        "timestamp": 1700000000,
        "player_position": {"x": x, "y": y},
        "player_stats": {"hp": hp, "max_hp": 100, "level": 5, "experience": 0},
        "nearby_entities": [
            {
                "name": "Forester",
                "type": 1,
                "distance": 2,
                "on_screen": True,
                "click_x": 320,
                "click_y": 240,
                "quest_npc": True,
            }
        ],
        "inventory": [{"slot": 0, "name": "Bronze Axe", "count": 1, "equippable": True}],
        "quests": [],
        "ui_state": {},
    }
    inner_str = json.dumps(inner_state) + "\n\nASCII_MAP:\n@\n"
    return json.dumps({"result": inner_str})


def _make_tool_use_event(line_type: str, content_block: dict) -> dict:
    """Wrap a content block into the JSONL stream-json format `_parse_claude_events` reads."""
    return {"type": line_type, "message": {"content": [content_block]}}


def _write_session_log(events: list[dict]) -> Path:
    """Write a list of stream-json events to a temp .log file and return its path."""
    fd = tempfile.NamedTemporaryFile(
        prefix="test_session_", suffix=".log", delete=False, mode="w"
    )
    for ev in events:
        fd.write(json.dumps(ev) + "\n")
    fd.flush()
    fd.close()
    return Path(fd.name)


def _build_two_turn_log(action_name: str, action_input: dict, action_result_text: str) -> Path:
    """Build a synthetic stream-json log with: observe -> action -> observe.

    The middle action is what we're testing — its tool_result text becomes
    the value extract_turns must persist as `action_result_raw`.
    """
    obs1 = _make_tool_use_event(
        "assistant",
        {
            "type": "tool_use",
            "name": "mcp__kaetram__observe",
            "input": {},
            "id": "obs_t0",
        },
    )
    obs1_result = _make_tool_use_event(
        "user",
        {
            "type": "tool_result",
            "tool_use_id": "obs_t0",
            "content": [{"type": "text", "text": _build_observe_state()}],
        },
    )
    act = _make_tool_use_event(
        "assistant",
        {
            "type": "tool_use",
            "name": action_name,
            "input": action_input,
            "id": "act_t0",
        },
    )
    act_result = _make_tool_use_event(
        "user",
        {
            "type": "tool_result",
            "tool_use_id": "act_t0",
            "content": [{"type": "text", "text": action_result_text}],
        },
    )
    obs2 = _make_tool_use_event(
        "assistant",
        {
            "type": "tool_use",
            "name": "mcp__kaetram__observe",
            "input": {},
            "id": "obs_t1",
        },
    )
    obs2_result = _make_tool_use_event(
        "user",
        {
            "type": "tool_result",
            "tool_use_id": "obs_t1",
            "content": [{"type": "text", "text": _build_observe_state(x=188, y=158)}],
        },
    )
    return _write_session_log([obs1, obs1_result, act, act_result, obs2, obs2_result])


# ---------------------------------------------------------------------------
# Test 1: is_browser_action() recognizes accept_quest
# ---------------------------------------------------------------------------


def test_is_browser_action_recognizes_accept_quest():
    event = {
        "type": "tool_use",
        "name": "mcp__kaetram__accept_quest",
        "input": {},
        "role": "assistant",
    }
    assert is_browser_action(event) is True, (
        "is_browser_action should recognize mcp__kaetram__accept_quest "
        "(this fails on the pre-patch typo `quest_action`)"
    )


# ---------------------------------------------------------------------------
# Test 2: is_browser_action() recognizes all 6 new tools
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("short_name", NEW_TOOLS)
def test_is_browser_action_recognizes_new_tools(short_name):
    full_name = f"mcp__kaetram__{short_name}"
    event = {"type": "tool_use", "name": full_name, "input": {}, "role": "assistant"}
    assert is_browser_action(event) is True, (
        f"is_browser_action should recognize {full_name} after the patch"
    )


# ---------------------------------------------------------------------------
# Test 3: classify_action returns correct short names
# ---------------------------------------------------------------------------


def test_classify_action_accept_quest():
    assert classify_action("", "mcp__kaetram__accept_quest") == "quest_accept"


@pytest.mark.parametrize(
    "tool_name,expected",
    [
        ("mcp__kaetram__gather", "gather"),
        ("mcp__kaetram__loot", "loot"),
        ("mcp__kaetram__buy_item", "buy_item"),
        ("mcp__kaetram__drop_item", "drop_item"),
        ("mcp__kaetram__clear_combat", "clear_combat"),
        ("mcp__kaetram__query_quest", "query_quest"),
    ],
)
def test_classify_action_new_tools(tool_name, expected):
    assert classify_action("", tool_name) == expected


# ---------------------------------------------------------------------------
# Test 4: structured_action returns well-formed strings for new tools
# ---------------------------------------------------------------------------


def test_structured_action_quest_accept():
    out = structured_action("quest_accept", "", {})
    assert out == "quest_accept()"


def test_structured_action_gather():
    out = structured_action("gather", "", {"resource_name": "Oak"})
    assert out == "gather(Oak)"


def test_structured_action_loot():
    out = structured_action("loot", "", {})
    assert out == "loot()"


def test_structured_action_buy_item():
    out = structured_action(
        "buy_item", "", {"npc_name": "Forester", "item_index": 2, "count": 3}
    )
    assert out == "buy_item(Forester, 2, count=3)"


def test_structured_action_drop_item():
    out = structured_action("drop_item", "", {"slot": 7})
    assert out == "drop_item(slot=7)"


def test_structured_action_clear_combat():
    out = structured_action("clear_combat", "", {})
    assert out == "clear_combat()"


def test_structured_action_query_quest():
    out = structured_action("query_quest", "", {"quest_name": "Sorcery"})
    assert out == "query_quest(Sorcery)"


# ---------------------------------------------------------------------------
# Test 5: End-to-end extraction preserves accept_quest turn + action_result_raw
# ---------------------------------------------------------------------------


def test_extraction_preserves_accept_quest_turn_and_raw_result():
    raw_text = '{"result": "Quest accept clicked"}'
    log_path = _build_two_turn_log(
        action_name="mcp__kaetram__accept_quest",
        action_input={},
        action_result_text=raw_text,
    )
    try:
        turns = run_extract_turns(log_path)
    finally:
        log_path.unlink(missing_ok=True)

    assert len(turns) >= 1, "extract_turns should produce at least one valid turn"
    quest_turns = [t for t in turns if t.get("action_type") == "quest_accept"]
    assert quest_turns, (
        f"Expected a quest_accept turn, got action_types="
        f"{[t.get('action_type') for t in turns]}"
    )

    qt = quest_turns[0]
    assert "action_result_raw" in qt, "Each turn should have an action_result_raw field"
    assert qt["action_result_raw"] == raw_text, (
        f"action_result_raw should be the raw tool_result text verbatim. "
        f"got={qt['action_result_raw']!r} expected={raw_text!r}"
    )


# ---------------------------------------------------------------------------
# Test 6: interact_npc quest_opened=true is preserved through extraction
# ---------------------------------------------------------------------------


def test_extraction_preserves_interact_npc_quest_opened_true():
    inner = {"arrived": True, "npc": "Forester", "quest_opened": True}
    raw_text = json.dumps({"result": json.dumps(inner)})

    log_path = _build_two_turn_log(
        action_name="mcp__kaetram__interact_npc",
        action_input={"npc_name": "Forester"},
        action_result_text=raw_text,
    )
    try:
        turns = run_extract_turns(log_path)
    finally:
        log_path.unlink(missing_ok=True)

    npc_turns = [t for t in turns if t.get("action_type") == "interact_npc"]
    assert npc_turns, (
        f"Expected an interact_npc turn, got action_types="
        f"{[t.get('action_type') for t in turns]}"
    )

    nt = npc_turns[0]
    assert nt.get("action_result_raw") is not None, (
        "interact_npc turn should have action_result_raw populated"
    )
    raw = nt["action_result_raw"]
    assert "quest_opened" in raw, (
        f"quest_opened key should survive extraction (raw={raw!r})"
    )
    assert "true" in raw, (
        f"quest_opened=true should survive extraction (raw={raw!r})"
    )


# ---------------------------------------------------------------------------
# Test 7: convert_to_qwen.build_tool_result_message preserves quest_opened
# ---------------------------------------------------------------------------


def test_build_tool_result_message_uses_action_result_raw():
    raw = '{"result": "{\\"quest_opened\\": true, \\"npc\\": \\"Forester\\", \\"arrived\\": true}"}'
    turn = {
        "turn_id": "session_x_t000",
        "action_type": "interact_npc",
        "action_structured": "interact_npc(Forester)",
        "action_code": '{"npc_name": "Forester"}',
        "action_result_raw": raw,
    }
    msg = build_tool_result_message(turn)
    assert msg is not None, "build_tool_result_message should return a dict"
    assert msg["role"] == "tool"
    content = msg["content"]
    assert isinstance(content, str)
    assert "quest_opened" in content, (
        f"quest_opened key should be preserved from action_result_raw, got={content!r}"
    )
    assert "true" in content, (
        f"quest_opened=true should be preserved, got={content!r}"
    )


# ---------------------------------------------------------------------------
# Test 8: build_tool_result_message fallback no longer hardcodes quest_opened=false
# ---------------------------------------------------------------------------


def test_build_tool_result_message_fallback_omits_quest_opened_false():
    turn_no_real = {
        "turn_id": "session_x_t000",
        "action_type": "interact_npc",
        "action_structured": "interact_npc(Forester)",
        "action_code": '{"npc_name": "Forester"}',
        # No action_result_raw — forces synthesizer fallback
    }
    msg = build_tool_result_message(turn_no_real)
    assert msg is not None
    content = msg["content"]
    # The synthesizer must NOT lie by emitting quest_opened=false. It should
    # either omit the key entirely, or simply not include `false`.
    assert not ("quest_opened" in content and "false" in content), (
        f"Synthesizer must not hardcode quest_opened=false (got={content!r})"
    )


# ---------------------------------------------------------------------------
# Test 9: _structured_action_to_tool_call handles all new tools
# ---------------------------------------------------------------------------


def test_structured_to_tool_call_quest_accept():
    out = _structured_action_to_tool_call("quest_accept()", "quest_accept")
    assert out is not None
    name, args = out
    assert name == "accept_quest"
    assert args == {}


def test_structured_to_tool_call_gather():
    out = _structured_action_to_tool_call("gather(Oak)", "gather")
    assert out is not None
    name, args = out
    assert name == "gather"
    assert args == {"resource_name": "Oak"}


def test_structured_to_tool_call_loot():
    out = _structured_action_to_tool_call("loot()", "loot")
    assert out is not None
    name, args = out
    assert name == "loot"
    assert args == {}


def test_structured_to_tool_call_buy_item():
    """buy_item must be mapped to the buy_item tool with npc_name and item_index.

    See test_structured_to_tool_call_buy_item_count_bug below for the count
    round-trip bug we discovered.
    """
    out = _structured_action_to_tool_call(
        "buy_item(Forester, 2, count=3)", "buy_item"
    )
    assert out is not None
    name, args = out
    assert name == "buy_item"
    assert args.get("npc_name") == "Forester"
    assert args.get("item_index") == 2
    # `count` should be present (the parser sets a default when args[2] exists)
    assert "count" in args


def test_structured_to_tool_call_buy_item_count_roundtrip():
    """Regression: extract_turns emits buy_item(<npc>, <idx>, count=N) and
    convert_to_qwen must round-trip count=N back to the tool_call int arg.
    Previously returned count=1 for all N (silent data loss)."""
    out = _structured_action_to_tool_call(
        "buy_item(Forester, 2, count=3)", "buy_item"
    )
    assert out is not None
    _, args = out
    assert args.get("count") == 3, (
        f"Expected count=3, got {args.get('count')!r}"
    )


def test_structured_to_tool_call_drop_item():
    out = _structured_action_to_tool_call("drop_item(slot=7)", "drop_item")
    assert out is not None
    name, args = out
    assert name == "drop_item"
    assert args == {"slot": 7}


def test_structured_to_tool_call_clear_combat():
    out = _structured_action_to_tool_call("clear_combat()", "clear_combat")
    assert out is not None
    name, args = out
    assert name == "clear_combat"
    assert args == {}


def test_structured_to_tool_call_query_quest():
    out = _structured_action_to_tool_call("query_quest(Sorcery)", "query_quest")
    assert out is not None
    name, args = out
    assert name == "query_quest"
    assert args == {"quest_name": "Sorcery"}


# ---------------------------------------------------------------------------
# Test 10: score_sessions.py references nav_cancel (not cancel_nav)
# ---------------------------------------------------------------------------


def test_score_sessions_references_nav_cancel():
    source = inspect.getsource(score_sessions)
    assert "nav_cancel" in source, (
        "score_sessions.py should reference nav_cancel (post-patch action_type name)"
    )


# ---------------------------------------------------------------------------
# Test 11: train_grpo_modal.py valid_actions includes new tools
# ---------------------------------------------------------------------------


def test_train_grpo_modal_valid_actions_includes_new_tools():
    grpo_path = REPO_ROOT / "finetune" / "train_grpo_modal.py"
    assert grpo_path.exists(), f"Expected file: {grpo_path}"
    source = grpo_path.read_text()
    for tool in NEW_TOOLS:
        assert f'"{tool}"' in source, (
            f"train_grpo_modal.py valid_actions should include {tool!r}"
        )
    # Drift fix: nav_cancel must be present
    assert '"nav_cancel"' in source, (
        "train_grpo_modal.py valid_actions should reference nav_cancel"
    )


# ---------------------------------------------------------------------------
# Test 12: extract_turns.py has no phantom quest_action reference
# ---------------------------------------------------------------------------


def test_no_phantom_quest_action_reference():
    extract_path = REPO_ROOT / "extract_turns.py"
    source = extract_path.read_text()
    assert "mcp__kaetram__quest_action" not in source, (
        "Pre-patch typo `mcp__kaetram__quest_action` should be removed"
    )
    assert "mcp__kaetram__accept_quest" in source, (
        "Correct MCP tool name `mcp__kaetram__accept_quest` should be present"
    )


# ---------------------------------------------------------------------------
# Bonus: TOOL_DEFINITIONS in convert_to_qwen.py covers the curated visible tools
# ---------------------------------------------------------------------------


def test_tool_definitions_include_new_tools():
    names = {td["function"]["name"] for td in TOOL_DEFINITIONS}
    for tool in CURATED_VISIBLE_TOOLS:
        assert tool in names, (
            f"convert_to_qwen.TOOL_DEFINITIONS should include {tool!r}, got {sorted(names)}"
        )


# ---------------------------------------------------------------------------
# Bonus: _prefer_real_tool_result unwraps a single layer of {"result": "..."}
# ---------------------------------------------------------------------------


def test_prefer_real_tool_result_unwraps_envelope():
    raw = '{"result": "{\\"quest_opened\\": true}"}'
    out = _prefer_real_tool_result(raw)
    assert out == '{"quest_opened": true}', (
        f"Should unwrap one layer of result envelope, got {out!r}"
    )


def test_prefer_real_tool_result_returns_none_on_empty():
    assert _prefer_real_tool_result(None) is None
    assert _prefer_real_tool_result("") is None
    assert _prefer_real_tool_result("   ") is None


def test_prefer_real_tool_result_passthrough_for_non_envelope():
    raw = '{"foo": "bar"}'
    out = _prefer_real_tool_result(raw)
    assert out == raw


# ---------------------------------------------------------------------------
# Standalone runner (so the file is usable without pytest installed)
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import traceback

    test_fns = []
    for name, obj in sorted(globals().items()):
        if not name.startswith("test_") or not callable(obj):
            continue
        marks = getattr(obj, "pytestmark", [])
        params = []
        for m in marks:
            if getattr(m, "name", "") == "parametrize":
                argnames, argvalues = m.args[0], list(m.args[1])
                if isinstance(argnames, str):
                    argnames_list = [a.strip() for a in argnames.split(",")]
                else:
                    argnames_list = list(argnames)
                for v in argvalues:
                    if not isinstance(v, (tuple, list)):
                        v = (v,)
                    params.append(dict(zip(argnames_list, v)))
        if params:
            for p in params:
                test_fns.append((f"{name}[{p}]", lambda f=obj, p=p: f(**p)))
        else:
            test_fns.append((name, obj))

    passed, failed = 0, 0
    for name, fn in test_fns:
        try:
            fn()
            passed += 1
            print(f"PASS {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
