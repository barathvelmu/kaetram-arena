#!/usr/bin/env python3
"""Frozen design contract for the zero-cost multi-action routing diagnostic.

This is deliberately separate from ``local-live-routing-diagnostic-v1``.
Nothing here accepts or rewrites a V1 registration or receipt.  The module is
pure/offline: importing and validating the design starts no service and makes
no network connection.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from canonical_start import CANONICAL_BCRYPT_HASH, canonical_database_documents
from scripts.opd.response_router import route_content_tool_call
from tool_surface import validate_tool_call_arguments


SCHEMA_VERSION = "kaetram.live-routing-multi-action-registration.v2"
STUDY_ID = "local-live-routing-multi-action-v2"
STATUS = "registered_before_live_execution"
ARMS = ("structured_direct", "content_recovery_on", "content_recovery_off")
ACTIONS = ("equip_item", "eat_food", "warp")
ARM_SCHEDULE = (
    ARMS,
    ("content_recovery_on", "content_recovery_off", "structured_direct"),
    ("content_recovery_off", "structured_direct", "content_recovery_on"),
)
ACTION_SCHEDULE = (
    ACTIONS,
    ("eat_food", "warp", "equip_item"),
    ("warp", "equip_item", "eat_food"),
)
ACTION_ARGUMENTS: dict[str, dict[str, Any]] = {
    "equip_item": {"slot": 3},
    "eat_food": {"slot": 5},
    "warp": {"location": "mudwich"},
}
ACTION_ENVELOPES = {
    "equip_item": (
        "<tool_call><function=equip_item><parameter=slot>3</parameter>"
        "</function></tool_call>"
    ),
    "eat_food": (
        "<tool_call><function=eat_food><parameter=slot>5</parameter>"
        "</function></tool_call>"
    ),
    "warp": (
        "<tool_call><function=warp><parameter=location>mudwich</parameter>"
        "</function></tool_call>"
    ),
}
SOURCE_PATHS = (
    "canonical_start.py",
    "mcp_game_server.py",
    "mcp_server/__init__.py",
    "mcp_server/core.py",
    "mcp_server/helpers.py",
    "mcp_server/js/__init__.py",
    "mcp_server/js/buy_packet.js",
    "mcp_server/js/inventory_snapshot.js",
    "mcp_server/js/nudge_store.js",
    "mcp_server/js/observe.js",
    "mcp_server/js/shop_ui_state.js",
    "mcp_server/login.py",
    "mcp_server/mob_stats.py",
    "mcp_server/resource_gates.py",
    "mcp_server/state_heartbeat.py",
    "mcp_server/tools/__init__.py",
    "mcp_server/tools/combat.py",
    "mcp_server/tools/crafting.py",
    "mcp_server/tools/gathering.py",
    "mcp_server/tools/inventory.py",
    "mcp_server/tools/navigation.py",
    "mcp_server/tools/npc.py",
    "mcp_server/tools/observe.py",
    "mcp_server/tools/quest.py",
    "mcp_server/tools/shop.py",
    "mcp_server/tools/test_lane.py",
    "mcp_server/utils.py",
    "play_qwen.py",
    "scripts/opd/canonicalize.py",
    "scripts/opd/execution_evidence.py",
    "scripts/opd/live_routing_launcher.py",
    "scripts/opd/live_routing_analyzer.py",
    "scripts/opd/live_routing_diagnostic.py",
    "scripts/opd/live_routing_multi_action_analyzer.py",
    "scripts/opd/live_routing_multi_action_diagnostic.py",
    "scripts/opd/live_routing_multi_action_launcher.py",
    "scripts/opd/live_routing_multi_action_orchestrator.py",
    "scripts/opd/live_routing_multi_action_prelaunch.py",
    "scripts/opd/live_routing_multi_action_result_verify.py",
    "scripts/opd/live_routing_orchestrator.py",
    "scripts/opd/live_routing_prelaunch.py",
    "scripts/opd/live_routing_result_verify.py",
    "scripts/opd/live_routing_services.py",
    "scripts/opd/response_router.py",
    "state_extractor.js",
    "tests/e2e/helpers/mcp_client.py",
    "tests/e2e/helpers/seed.py",
    "tests/unit/test_live_routing_multi_action_analyzer.py",
    "tests/unit/test_live_routing_multi_action_diagnostic.py",
    "tests/unit/test_live_routing_multi_action_launcher.py",
    "tests/unit/test_live_routing_multi_action_orchestrator.py",
    "tests/unit/test_live_routing_multi_action_prelaunch.py",
    "tests/unit/test_live_routing_multi_action_result_verify.py",
    "tool_surface.py",
)


class MultiActionRegistrationError(ValueError):
    """The V2 design cannot be interpreted without changing its meaning."""


def canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise MultiActionRegistrationError(f"non-canonical JSON value: {exc}") from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MultiActionRegistrationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise MultiActionRegistrationError(f"non-finite JSON constant: {value}")


def load_registration_strict(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_bytes(),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise MultiActionRegistrationError(f"registration unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise MultiActionRegistrationError("registration root must be an object")
    return value


def multi_action_documents(username: str) -> dict[str, dict[str, Any]]:
    """Return the exact create-only V2 fixture without touching MongoDB."""

    documents = deepcopy(canonical_database_documents(username))
    documents["player_info"]["hitPoints"] = 30
    documents["player_info"]["userAgent"] = "kaetram-live-routing-multi-action-v2"
    documents["player_inventory"]["slots"][5] = {
        "index": 5,
        "key": "apple",
        "count": 1,
        "enchantments": {},
    }
    # Pin the password even if the canonical helper changes independently.
    documents["player_info"]["password"] = CANONICAL_BCRYPT_HASH
    return documents


def expected_observation_fixture() -> dict[str, Any]:
    return {
        "pos": {"x": 328, "y": 892},
        "stats": {"hp": 30, "max_hp": 69, "level": 1, "xp": 0},
        "equipment": {},
        "skills": {},
        "inventory": [
            {"slot": 0, "key": "bronzeaxe", "count": 1},
            {"slot": 1, "key": "knife", "count": 1},
            {"slot": 2, "key": "fishingpole", "count": 1},
            {"slot": 3, "key": "coppersword", "count": 1},
            {"slot": 4, "key": "woodenbow", "count": 1},
            {"slot": 5, "key": "apple", "count": 1},
        ],
        "active_quests": [],
        "finished_quests": ["Miner's Quest"],
        "is_dead": False,
        "indoors": False,
    }


def semantic_gameplay_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    """Select persistent gameplay state and omit session/default-only rows.

    ``value`` may be an observe payload or an owned Mongo snapshot.  This is a
    semantic projection, not byte equality: volatile session fields and empty
    default collections are intentionally outside the registered outcome.
    """

    documents = value.get("documents") if isinstance(value, Mapping) else None
    if isinstance(documents, Mapping):
        info = documents.get("player_info")
        inventory_doc = documents.get("player_inventory")
        equipment_doc = documents.get("player_equipment")
        if not all(isinstance(row, Mapping) for row in (info, inventory_doc, equipment_doc)):
            raise MultiActionRegistrationError("database gameplay documents are missing")
        slots = inventory_doc.get("slots")
        if not isinstance(slots, list):
            raise MultiActionRegistrationError("database inventory slots are missing")
        inventory = [
            {"slot": row.get("index"), "key": row.get("key"), "count": row.get("count")}
            for row in slots
            if isinstance(row, Mapping) and row.get("key")
        ]
        equipment_rows = equipment_doc.get("equipments", equipment_doc.get("slots", []))
        if not isinstance(equipment_rows, list):
            raise MultiActionRegistrationError("database equipment slots are missing")
        equipment = [
            {
                "slot": row.get("type", row.get("slot")),
                "key": row.get("key"),
                "count": row.get("count", 1),
            }
            for row in equipment_rows
            if isinstance(row, Mapping) and row.get("key")
        ]
        inventory.sort(key=lambda row: (row["slot"] is None, row["slot"]))
        equipment.sort(key=lambda row: (row["slot"] is None, row["slot"]))
        return {
            "pos": {"x": info.get("x"), "y": info.get("y")},
            "hp": info.get("hitPoints"),
            "max_hp": 69,
            "inventory": inventory,
            "equipment": equipment,
        }

    stats = value.get("stats")
    pos = value.get("pos")
    if not isinstance(stats, Mapping) or not isinstance(pos, Mapping):
        raise MultiActionRegistrationError("observe gameplay fields are missing")
    inventory_rows = value.get("inventory", [])
    if not isinstance(inventory_rows, list):
        raise MultiActionRegistrationError("observe inventory is missing")
    equipment_value = value.get("equipment", {})
    inventory = [
        {"slot": row.get("slot"), "key": row.get("key"), "count": row.get("count")}
        for row in inventory_rows
        if isinstance(row, Mapping) and row.get("key")
    ]
    equipment: list[dict[str, Any]] = []
    if isinstance(equipment_value, Mapping):
        for slot, row in equipment_value.items():
            if isinstance(row, Mapping) and (row.get("key") or row.get("name")):
                equipment.append(
                    {
                        "slot": slot,
                        "key": row.get("key", row.get("name")),
                        "count": row.get("count", 1),
                    }
                )
    elif isinstance(equipment_value, list):
        equipment = [
            {
                "slot": row.get("type", row.get("slot")),
                "key": row.get("key", row.get("name")),
                "count": row.get("count", 1),
            }
            for row in equipment_value
            if isinstance(row, Mapping) and (row.get("key") or row.get("name"))
        ]
    inventory.sort(key=lambda row: (row["slot"] is None, row["slot"]))
    equipment.sort(key=lambda row: str(row["slot"]))
    return {
        "pos": {"x": pos.get("x"), "y": pos.get("y")},
        "hp": stats.get("hp"),
        "max_hp": stats.get("max_hp"),
        "inventory": inventory,
        "equipment": equipment,
    }


def expected_trial_identities() -> list[dict[str, Any]]:
    trials: list[dict[str, Any]] = []
    schedule_index = 0
    for repeat, arm_order in enumerate(ARM_SCHEDULE, start=1):
        action_order = ACTION_SCHEDULE[repeat - 1]
        for position, arm in enumerate(arm_order, start=1):
            schedule_index += 1
            trials.append(
                {
                    "schedule_index": schedule_index,
                    "repeat": repeat,
                    "position_within_repeat": position,
                    "pair_id": f"repeat-{repeat:02d}",
                    "arm": arm,
                    "action_order": list(action_order),
                    "trial_key": f"llrma-v2-t{schedule_index:02d}",
                    "username_template": f"ma_{{run_id}}_{schedule_index:02d}",
                    "treatment_session_id_template": (
                        f"llrma-{{run_id}}-t{schedule_index:02d}-treatment"
                    ),
                    "reconnect_session_id_template": (
                        f"llrma-{{run_id}}-t{schedule_index:02d}-reconnect"
                    ),
                    "expected_candidate_invocations": (
                        0 if arm == "content_recovery_off" else 3
                    ),
                }
            )
    return trials


def route_registered_turn(arm: str, action_name: str) -> dict[str, Any]:
    if arm not in ARMS or action_name not in ACTIONS:
        raise MultiActionRegistrationError("unregistered arm or action")
    if arm == "content_recovery_off":
        return {"status": "disabled_not_evaluated", "calls": [], "reason": None}
    if arm == "structured_direct":
        return {
            "status": "not_applicable_structured",
            "calls": [{"name": action_name, "args": deepcopy(ACTION_ARGUMENTS[action_name])}],
            "reason": None,
        }
    return route_content_tool_call(ACTION_ENVELOPES[action_name])


def cumulative_predicates(
    projection: Mapping[str, Any], completed_actions: Sequence[str]
) -> dict[str, bool]:
    """Evaluate all effects that should persist after the completed prefix."""

    completed = set(completed_actions)
    inventory = projection.get("inventory")
    equipment = projection.get("equipment")
    pos = projection.get("pos")
    if not isinstance(inventory, list) or not isinstance(equipment, list):
        return {name: False for name in completed_actions}
    keys = {row.get("key") for row in inventory if isinstance(row, Mapping)}
    equipped = {
        str(row.get("key", "")).lower()
        for row in equipment
        if isinstance(row, Mapping)
    }
    results: dict[str, bool] = {}
    if "equip_item" in completed:
        results["equip_item"] = "coppersword" not in keys and any(
            key == "coppersword" or key.startswith("copper sword") for key in equipped
        )
    if "eat_food" in completed:
        hp = projection.get("hp")
        results["eat_food"] = "apple" not in keys and type(hp) in (int, float) and hp > 30
    if "warp" in completed:
        results["warp"] = bool(
            isinstance(pos, Mapping)
            and type(pos.get("x")) in (int, float)
            and type(pos.get("y")) in (int, float)
            and 188 <= pos["x"] <= 191
            and 157 <= pos["y"] <= 160
        )
    return results


def validate_registration(registration: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_keys = {
        "schema_version", "study_id", "status", "claim_boundary",
        "zero_cost_contract", "source_contract", "live_contract", "state_fixture",
        "actions", "arms", "schedule", "trial_identities", "measurement",
        "runtime_parameters", "reporting",
    }
    if set(registration) != expected_keys:
        errors.append("registration top-level key set drift")
    if registration.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version drift")
    if registration.get("study_id") != STUDY_ID:
        errors.append("study_id drift")
    if registration.get("status") != STATUS:
        errors.append("status drift")
    zero = registration.get("zero_cost_contract")
    if not isinstance(zero, Mapping) or (
        zero.get("model_calls") != 0
        or zero.get("remote_endpoints") != "forbidden"
        or zero.get("metered_services") != "forbidden"
        or zero.get("network_scope") != "loopback_only"
    ):
        errors.append("zero-cost contract drift")
    source = registration.get("source_contract")
    if not isinstance(source, Mapping) or source.get("files") != list(SOURCE_PATHS):
        errors.append("source file sealing set drift")
    if not isinstance(source, Mapping) or source.get("seal") != "create_only_prelaunch_receipt":
        errors.append("source sealing policy drift")
    fixture = registration.get("state_fixture")
    if not isinstance(fixture, Mapping) or fixture.get("expected_observation") != expected_observation_fixture():
        errors.append("state fixture drift")
    action_rows = registration.get("actions")
    expected_actions = [
        {
            "turn": name,
            "name": name,
            "arguments": ACTION_ARGUMENTS[name],
            "canonical_sha256": canonical_sha256(
                {"name": name, "arguments": ACTION_ARGUMENTS[name]}
            ),
            "content_envelope": ACTION_ENVELOPES[name],
            "content_envelope_sha256": hashlib.sha256(
                ACTION_ENVELOPES[name].encode("utf-8")
            ).hexdigest(),
        }
        for name in ACTIONS
    ]
    if action_rows != expected_actions:
        errors.append("action contract drift")
    else:
        for row in action_rows:
            valid, _ = validate_tool_call_arguments(row["name"], row["arguments"])
            decision = route_content_tool_call(row["content_envelope"])
            if not valid or decision.get("status") != "promoted" or decision.get("calls") != [
                {"name": row["name"], "args": row["arguments"]}
            ]:
                errors.append(f"action route/schema invalid: {row['name']}")
    arms = registration.get("arms")
    if not isinstance(arms, list) or [row.get("arm") for row in arms if isinstance(row, Mapping)] != list(ARMS):
        errors.append("arm contract drift")
    if registration.get("schedule") != {
        "arm_orders": [list(row) for row in ARM_SCHEDULE],
        "action_orders": [list(row) for row in ACTION_SCHEDULE],
        "technical_repeats": 3,
        "trials": 9,
        "turns_per_trial": 3,
    }:
        errors.append("counterbalanced schedule drift")
    if registration.get("trial_identities") != expected_trial_identities():
        errors.append("trial identity drift")
    runtime = registration.get("runtime_parameters")
    if not isinstance(runtime, Mapping) or (
        runtime.get("candidate_retry_count") != 0
        or runtime.get("cold_mcp_session_per_trial") is not True
        or runtime.get("cold_browser_session_per_trial") is not True
        or runtime.get("fresh_unique_player_per_trial") is not True
    ):
        errors.append("runtime isolation contract drift")
    reporting = registration.get("reporting")
    if not isinstance(reporting, Mapping) or reporting.get("technical_repeats_are_independent") is not False:
        errors.append("repeat interpretation drift")
    return errors


def validate_registration_path(path: Path) -> list[str]:
    return validate_registration(load_registration_strict(path))
