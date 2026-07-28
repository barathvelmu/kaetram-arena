from __future__ import annotations

import asyncio
import json
from math import nan

import pytest

from scripts.opd.execution_evidence import (
    ToolNotAttemptedError,
    TransportResult,
    compact_evidence_record,
    evidence_sha256,
    execute_tool_call_with_evidence,
    normalize_observation,
    observation_delta,
    parse_tool_result_json,
)
from tool_surface import (
    MODEL_VISIBLE_TOOL_EFFECT_CLASSES,
    MODEL_VISIBLE_TOOL_NAMES,
    MODEL_VISIBLE_TOOL_SCHEMA_SHA256,
)


def _observation(*, hp: int = 50, apple_count: int = 2) -> dict:
    return {
        "pos": {"x": 328, "y": 892},
        "stats": {"hp": hp, "max_hp": 69, "level": 1, "xp": 0},
        "status": {
            "dead": False,
            "stuck": False,
            "nav": "idle",
            "indoors": False,
            "combat": None,
        },
        "equipment": {},
        "skills": {},
        "inventory": [
            {"slot": 4, "slots": [7, 4], "key": "apple", "count": apple_count},
            {"slot": 0, "slots": [0], "key": "bronzeaxe", "count": 1},
        ],
        "active_quests": [],
        "finished_quests": [{"name": "Miner's Quest"}],
        "nearby": {"mobs": [{"name": "Rat", "distance": 3}]},
        "events": ["volatile"],
    }


def _observe_result(**kwargs) -> TransportResult:
    return TransportResult(
        "observe: " + json.dumps(_observation(**kwargs)) + "\n\nASCII_MAP:\n..."
    )


def test_effect_registry_exhaustively_covers_frozen_surface() -> None:
    assert set(MODEL_VISIBLE_TOOL_EFFECT_CLASSES) == set(MODEL_VISIBLE_TOOL_NAMES)
    assert MODEL_VISIBLE_TOOL_EFFECT_CLASSES["observe"] == "observation"
    assert MODEL_VISIBLE_TOOL_EFFECT_CLASSES["query_quest"] == "read_only"


def test_tool_result_parser_is_strict_and_strips_diagnostics() -> None:
    assert parse_tool_result_json(
        'observe: {"pos":{"x":1,"y":2}}\n\nASCII_MAP:\nmap',
        expected_name="observe",
    ) == {"pos": {"x": 1, "y": 2}}
    assert parse_tool_result_json('{"ok":true}') == {"ok": True}
    assert parse_tool_result_json("observe: not-json", expected_name="observe") is None
    assert parse_tool_result_json("[1,2]") is None
    assert parse_tool_result_json('{"x":NaN}') is None
    assert parse_tool_result_json('{"x":1,"x":2}') is None


def test_schema_invalid_call_never_reaches_observer_or_mcp() -> None:
    calls = []

    async def call_tool(name, arguments):
        calls.append((name, arguments))
        return TransportResult("unexpected")

    async def observe():
        calls.append(("observe", {}))
        return _observe_result()

    receipt = asyncio.run(
        execute_tool_call_with_evidence(
            call_tool, "navigate", {}, observe_immediate=observe
        )
    )
    assert receipt["frozen_schema"] == {
        "sha256": MODEL_VISIBLE_TOOL_SCHEMA_SHA256,
        "valid": False,
        "reason": "missing_required_argument",
    }
    assert receipt["mcp"]["attempted"] is False
    assert receipt["observation"]["status"] == "not_applicable_schema_invalid"
    assert calls == []


def test_mutating_call_records_before_action_after_exactly_once() -> None:
    order = []
    observations = iter([_observe_result(hp=50, apple_count=2), _observe_result(hp=60, apple_count=1)])

    async def call_tool(name, arguments):
        order.append((name, arguments))
        return TransportResult('eat_food: {"ok":true}')

    async def observe():
        order.append(("observe", {}))
        return next(observations)

    receipt = asyncio.run(
        execute_tool_call_with_evidence(
            call_tool, "eat_food", {"slot": 4}, observe_immediate=observe
        )
    )
    assert order == [
        ("observe", {}),
        ("eat_food", {"slot": 4}),
        ("observe", {}),
    ]
    assert receipt["mcp"]["protocol_success"] is True
    assert receipt["mcp"]["delivery_status"] == "confirmed_by_result"
    delta = receipt["observation"]["delta"]
    assert delta["changed"] is True
    assert {change["path"] for change in delta["changes"]} == {
        "/inventory",
        "/stats/hp",
    }


def test_mcp_error_still_captures_after_state_without_retry() -> None:
    action_calls = 0
    observations = iter([_observe_result(), _observe_result()])

    async def call_tool(name, arguments):
        nonlocal action_calls
        action_calls += 1
        return TransportResult("eat_food: error", is_error=True)

    async def observe():
        return next(observations)

    receipt = asyncio.run(
        execute_tool_call_with_evidence(
            call_tool, "eat_food", {"slot": 4}, observe_immediate=observe
        )
    )
    assert action_calls == 1
    assert receipt["mcp"]["protocol_success"] is False
    assert receipt["mcp"]["is_error"] is True
    assert receipt["observation"]["status"] == "measured"
    assert receipt["observation"]["delta"]["changed"] is False


def test_before_observation_failure_prevents_action() -> None:
    action_calls = 0

    async def call_tool(name, arguments):
        nonlocal action_calls
        action_calls += 1
        return TransportResult("should not run")

    async def observe():
        raise RuntimeError("browser unavailable")

    receipt = asyncio.run(
        execute_tool_call_with_evidence(
            call_tool, "navigate", {"x": 1, "y": 2}, observe_immediate=observe
        )
    )
    assert action_calls == 0
    assert receipt["mcp"]["attempted"] is False
    assert receipt["observation"]["status"] == "before_unavailable_action_not_invoked"


def test_after_observation_failure_never_retries_action() -> None:
    action_calls = 0
    observe_calls = 0

    async def call_tool(name, arguments):
        nonlocal action_calls
        action_calls += 1
        raise RuntimeError("transport closed after send")

    async def observe():
        nonlocal observe_calls
        observe_calls += 1
        if observe_calls == 1:
            return _observe_result()
        raise RuntimeError("cannot read post-state")

    receipt = asyncio.run(
        execute_tool_call_with_evidence(
            call_tool, "navigate", {"x": 1, "y": 2}, observe_immediate=observe
        )
    )
    assert action_calls == 1
    assert observe_calls == 2
    assert receipt["mcp"]["exception"]["type"] == "RuntimeError"
    assert receipt["mcp"]["delivery_status"] == "unknown_after_exception"
    assert receipt["observation"]["status"] == "after_unavailable_action_not_retried"


def test_observe_uses_its_own_result_without_recursive_observer() -> None:
    calls = []

    async def call_tool(name, arguments):
        calls.append((name, arguments))
        return _observe_result()

    async def forbidden_observer():
        raise AssertionError("observe must not recursively observe")

    receipt = asyncio.run(
        execute_tool_call_with_evidence(
            call_tool, "observe", {}, observe_immediate=forbidden_observer
        )
    )
    assert calls == [("observe", {})]
    assert receipt["observation"]["status"] == "observation_readback"
    assert receipt["observation"]["after"]["pos"] == {"x": 328, "y": 892}


def test_read_only_tool_does_not_request_state_delta() -> None:
    calls = []

    async def call_tool(name, arguments):
        calls.append((name, arguments))
        return TransportResult('query_quest: {"accepted":false}')

    async def forbidden_observer():
        raise AssertionError("read-only tool should not be bracketed by observe")

    receipt = asyncio.run(
        execute_tool_call_with_evidence(
            call_tool,
            "query_quest",
            {"quest_name": "Scavenger"},
            observe_immediate=forbidden_observer,
        )
    )
    assert len(calls) == 1
    assert receipt["observation"]["status"] == "not_applicable_read_only"


def test_normalization_ignores_nearby_events_and_inventory_order() -> None:
    left = _observation()
    right = _observation()
    right["inventory"] = list(reversed(right["inventory"]))
    right["nearby"] = {"mobs": [{"name": "Snek", "distance": 1}]}
    right["events"] = ["different"]
    before = normalize_observation(left)
    after = normalize_observation(right)
    assert before == after
    assert observation_delta(before, after) == {
        "changed": False,
        "change_count": 0,
        "changes": [],
    }
    assert evidence_sha256({"snapshot": before}) == evidence_sha256(
        {"snapshot": after}
    )


def test_normalization_retains_real_nav_combat_and_underlying_slots() -> None:
    payload = _observation()
    payload["status"].update(
        {"nav": "navigating", "combat": {"target": "Rat", "target_hp": "2/5", "dist": 3}}
    )
    projected = normalize_observation(payload)
    assert projected["status"]["nav"] == "navigating"
    assert projected["status"]["combat"] == {"target": "Rat", "target_hp": "2/5"}
    assert projected["inventory"][1]["slots"] == [4, 7]


def test_normalization_rejects_nonfinite_values() -> None:
    payload = _observation()
    payload["stats"]["hp"] = nan
    with pytest.raises(ValueError):
        normalize_observation(payload)


def test_nonfinite_invalid_arguments_return_receipt_without_calls() -> None:
    calls = []

    async def call_tool(name, arguments):
        calls.append((name, arguments))
        return TransportResult("unexpected")

    receipt = asyncio.run(
        execute_tool_call_with_evidence(
            call_tool, "navigate", {"x": nan, "y": 1}
        )
    )
    assert receipt["frozen_schema"]["valid"] is False
    assert receipt["mcp"]["attempted"] is False
    assert receipt["call"]["arguments"]["x"] == {
        "__invalid_nonfinite_float__": "nan"
    }
    assert calls == []


def test_delivery_status_distinguishes_proven_no_send_from_unknown() -> None:
    async def not_sent(name, arguments):
        raise ToolNotAttemptedError("not connected")

    async def unknown(name, arguments):
        raise ConnectionError("connection lost")

    proven = asyncio.run(
        execute_tool_call_with_evidence(not_sent, "navigate", {"x": 1, "y": 2})
    )
    uncertain = asyncio.run(
        execute_tool_call_with_evidence(unknown, "navigate", {"x": 1, "y": 2})
    )
    assert proven["mcp"]["attempted"] is False
    assert proven["mcp"]["delivery_status"] == "not_attempted"
    assert uncertain["mcp"]["attempted"] is True
    assert uncertain["mcp"]["delivery_status"] == "unknown_after_exception"


def test_protocol_success_is_separate_from_tool_reported_error() -> None:
    async def call_tool(name, arguments):
        return TransportResult('warp: {"error":"blocked by combat"}', is_error=False)

    receipt = asyncio.run(
        execute_tool_call_with_evidence(
            call_tool, "warp", {"location": "mudwich"}
        )
    )
    assert receipt["mcp"]["protocol_success"] is True
    assert receipt["mcp"]["tool_reported_error"] == "blocked by combat"


def test_compact_log_record_deduplicates_raw_text_but_keeps_hashes() -> None:
    record = {
        "mcp": {"result_text": "large", "result_sha256": "abc"},
        "observation": {
            "before": {"raw_text": "before", "raw_sha256": "one", "snapshot": {}},
            "after": {"raw_text": "after", "raw_sha256": "two", "snapshot": {}},
        },
    }
    compact = compact_evidence_record(record)
    assert "result_text" not in compact["mcp"]
    assert "raw_text" not in compact["observation"]["before"]
    assert compact["mcp"]["result_sha256"] == "abc"
    assert record["mcp"]["result_text"] == "large"
