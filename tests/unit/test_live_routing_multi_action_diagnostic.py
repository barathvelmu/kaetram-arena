from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from scripts.opd.live_routing_multi_action_diagnostic import (
    ACTIONS,
    ACTION_SCHEDULE,
    expected_trial_identities,
    load_registration_strict,
    multi_action_documents,
    route_registered_turn,
    semantic_gameplay_projection,
    cumulative_predicates,
    validate_registration,
)


REGISTRATION = Path("research/experiments/local-live-routing-multi-action-v2.json")


def test_checked_in_registration_is_exact_and_offline_valid() -> None:
    registration = load_registration_strict(REGISTRATION)
    assert validate_registration(registration) == []
    trials = expected_trial_identities()
    assert len(trials) == 9
    assert {row["arm"] for row in trials} == {
        "structured_direct", "content_recovery_on", "content_recovery_off"
    }
    assert all(sum(row["arm"] == arm for row in trials) == 3 for arm in {
        "structured_direct", "content_recovery_on", "content_recovery_off"
    })
    assert {tuple(row["action_order"]) for row in trials} == set(ACTION_SCHEDULE)


def test_registration_detects_action_and_schedule_drift() -> None:
    registration = load_registration_strict(REGISTRATION)
    mutated = deepcopy(registration)
    mutated["actions"][0]["arguments"]["slot"] = 4
    assert "action contract drift" in validate_registration(mutated)
    mutated = deepcopy(registration)
    mutated["trial_identities"][0]["action_order"].reverse()
    assert "trial identity drift" in validate_registration(mutated)


def test_all_registered_routes_are_one_call_or_exactly_off() -> None:
    for action in ACTIONS:
        structured = route_registered_turn("structured_direct", action)
        recovered = route_registered_turn("content_recovery_on", action)
        disabled = route_registered_turn("content_recovery_off", action)
        assert structured["status"] == "not_applicable_structured"
        assert recovered["status"] == "promoted"
        assert len(structured["calls"]) == len(recovered["calls"]) == 1
        assert structured["calls"] == recovered["calls"]
        assert disabled == {
            "status": "disabled_not_evaluated", "calls": [], "reason": None
        }


def test_fixture_is_low_hp_with_one_apple_and_does_not_mutate_canonical() -> None:
    first = multi_action_documents("fixture")
    assert first["player_info"]["hitPoints"] == 30
    assert first["player_inventory"]["slots"][5]["key"] == "apple"
    assert first["player_inventory"]["slots"][5]["count"] == 1
    first["player_inventory"]["slots"][5]["key"] = "changed"
    assert multi_action_documents("fixture")["player_inventory"]["slots"][5]["key"] == "apple"


def test_semantic_projection_ignores_session_bookkeeping_and_default_rows() -> None:
    documents = multi_action_documents("fixture")
    base = semantic_gameplay_projection({"documents": documents})
    mutated = deepcopy(documents)
    mutated["player_info"]["lastAddress"] = "198.51.100.42"
    mutated["player_statistics"]["loginCount"] = 99
    mutated["player_quests"]["quests"].append({"key": "irrelevant"})
    assert semantic_gameplay_projection({"documents": mutated}) == base


def test_cumulative_predicates_require_persisting_prior_effects() -> None:
    projection = {
        "pos": {"x": 189, "y": 158},
        "hp": 45,
        "max_hp": 69,
        "inventory": [{"slot": 0, "key": "bronzeaxe", "count": 1}],
        "equipment": [{"slot": 0, "key": "coppersword", "count": 1}],
    }
    assert cumulative_predicates(projection, ACTIONS) == {
        "equip_item": True, "eat_food": True, "warp": True
    }
    projection["inventory"].append({"slot": 3, "key": "coppersword", "count": 1})
    assert cumulative_predicates(projection, ACTIONS)["equip_item"] is False
