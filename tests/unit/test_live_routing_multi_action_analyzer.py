from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest

from canonical_start import initial_state_projection
from scripts.opd.live_routing_launcher import LOCK_COLLECTION, MONGO_COLLECTIONS
from scripts.opd.live_routing_multi_action_analyzer import (
    MultiActionAnalysisError,
    assemble_trial_receipt,
    analyze_run,
    classify_trial,
)
from scripts.opd.live_routing_multi_action_diagnostic import (
    canonical_sha256,
    expected_observation_fixture,
    multi_action_documents,
    semantic_gameplay_projection,
)
from scripts.opd.live_routing_multi_action_launcher import (
    PHASE_SCHEMA_VERSION,
    TURN_SCHEMA_VERSION,
)


def _observe_payload(*, equipped=False, eaten=False, warped=False) -> dict:
    state = deepcopy(expected_observation_fixture())
    state["finished_quests"] = [{"name": "Miner's Quest"}]
    if equipped:
        state["inventory"] = [row for row in state["inventory"] if row["key"] != "coppersword"]
        state["equipment"] = {"weapon": {"key": "coppersword", "count": 1}}
    if eaten:
        state["inventory"] = [row for row in state["inventory"] if row["key"] != "apple"]
        state["stats"]["hp"] = 45
    if warped:
        state["pos"] = {"x": 189, "y": 158}
    return state


def _semantic_record(payload: dict) -> dict:
    raw = json.dumps(payload, sort_keys=True)
    return {
        "available": True,
        "raw_text": raw,
        "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "semantic_projection": semantic_gameplay_projection(payload),
    }


def _precondition() -> dict:
    payload = _observe_payload()
    raw = json.dumps(payload, sort_keys=True)
    return {
        "available": True,
        "raw_text": raw,
        "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "normalized_projection": initial_state_projection(payload),
    }


def _attestation(offset: int) -> dict:
    parsed = {
        "mcp_pid": offset + 1,
        "mcp_process_group": offset + 1,
        "mcp_instance_nonce": f"{offset + 1:032x}",
        "browser_pid": offset + 2,
        "browser_process_group": offset + 2,
        "browser_launch_nonce": f"{offset + 2:032x}",
    }
    raw = json.dumps(parsed, sort_keys=True)
    return {"raw_text": raw, "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(), "parsed": parsed}


def _turn(sequence: int, action: str, payload: dict, *, active: bool) -> dict:
    result = json.dumps({"ok": True}, sort_keys=True) if active else None
    return {
        "schema_version": TURN_SCHEMA_VERSION,
        "sequence": sequence,
        "action": action,
        "router_status": "not_applicable_structured" if active else "disabled_not_evaluated",
        "schema_status": "valid" if active else "not_applicable_no_candidate",
        "dispatch_attempted": active,
        "delivery_status": "confirmed" if active else "not_attempted",
        "protocol_success": True if active else None,
        "tool_reported_error": None,
        "result_json": {"ok": True} if active else None,
        "result_raw_text": result,
        "result_raw_sha256": hashlib.sha256(result.encode()).hexdigest() if result else None,
        "immediate": _semantic_record(payload),
        "delayed": _semantic_record(payload),
        "delayed_elapsed_monotonic_seconds": 1.5,
    }


def _plan(arm: str) -> dict:
    return {
        "trial_id": "trial-0001",
        "username": "ma_local001_01",
        "arm": arm,
        "action_order": ["equip_item", "eat_food", "warp"],
        "treatment_session_id": "llrma-local001-t01-treatment",
        "reconnect_session_id": "llrma-local001-t01-reconnect",
    }


def _phases(arm: str) -> tuple[dict, dict, dict]:
    active = arm != "content_recovery_off"
    states = (
        [_observe_payload(equipped=True), _observe_payload(equipped=True, eaten=True), _observe_payload(equipped=True, eaten=True, warped=True)]
        if active
        else [_observe_payload(), _observe_payload(), _observe_payload()]
    )
    plan = _plan(arm)
    treatment = {
        "schema_version": PHASE_SCHEMA_VERSION,
        "trial_id": plan["trial_id"],
        "session_id": plan["treatment_session_id"],
        "phase": "treatment",
        "username": plan["username"],
        "arm": arm,
        "action_order": plan["action_order"],
        "runtime_attestation": _attestation(100),
        "process_lifecycle": {},
        "precondition": _precondition(),
        "turns": [
            _turn(index, action, state, active=active)
            for index, (action, state) in enumerate(zip(plan["action_order"], states, strict=True), start=1)
        ],
        "candidate_call_ledger": [],
        "reconnect": None,
        "worker_elapsed_seconds": 12.0,
    }
    if active:
        treatment["candidate_call_ledger"] = [
            {
                "sequence": turn["sequence"],
                "name": turn["action"],
                "arguments": {"slot": 3} if turn["action"] == "equip_item" else {"slot": 5} if turn["action"] == "eat_food" else {"location": "mudwich"},
                "delivery_status": turn["delivery_status"],
                "protocol_success": turn["protocol_success"],
                "result_raw_sha256": turn["result_raw_sha256"],
            }
            for turn in treatment["turns"]
        ]
    reconnect_payload = states[-1]
    reconnect = {
        "schema_version": PHASE_SCHEMA_VERSION,
        "trial_id": plan["trial_id"],
        "session_id": plan["reconnect_session_id"],
        "phase": "reconnect",
        "username": plan["username"],
        "arm": arm,
        "action_order": plan["action_order"],
        "runtime_attestation": _attestation(200),
        "process_lifecycle": {},
        "precondition": None,
        "turns": [],
        "candidate_call_ledger": [],
        "reconnect": _semantic_record(reconnect_payload),
        "worker_elapsed_seconds": 3.0,
    }
    documents = multi_action_documents(plan["username"])
    for index, name in enumerate(MONGO_COLLECTIONS, start=1):
        documents[name]["_id"] = f"id-{index:02d}"
    if active:
        documents["player_info"]["x"] = 189
        documents["player_info"]["y"] = 158
        documents["player_info"]["hitPoints"] = 45
        documents["player_inventory"]["slots"][3] = {"index": 3, "key": "", "count": 0, "enchantments": {}}
        documents["player_inventory"]["slots"][5] = {"index": 5, "key": "", "count": 0, "enchantments": {}}
        documents["player_equipment"]["equipments"] = [
            {"type": 0, "key": "coppersword", "count": 1}
        ]
    snapshot = {"database": "kaetram_e2e", "username": plan["username"], "documents": documents}
    return treatment, reconnect, snapshot


def _receipt(arm: str) -> dict:
    treatment, reconnect, snapshot = _phases(arm)
    username = _plan(arm)["username"]
    identifiers = {
        LOCK_COLLECTION: username,
        **{name: snapshot["documents"][name]["_id"] for name in MONGO_COLLECTIONS},
    }
    absence = {
        "database": "kaetram_e2e",
        "counts": {username: {name: 0 for name in MONGO_COLLECTIONS}},
        "all_absent": True,
    }
    seed = {
        "database": "kaetram_e2e",
        "username": username,
        "trial_id": _plan(arm)["trial_id"],
        "fixture_schema_version": "kaetram.multi-action-fixture.v2",
        "absence": absence,
        "inserted_ids": identifiers,
        "insertion_order": [
            LOCK_COLLECTION,
            *(name for name in MONGO_COLLECTIONS if name != "player_info"),
            "player_info",
        ],
        "player_info_inserted_last": True,
    }
    events = [
        "absence_confirmed", "seed_completed", "treatment_started",
        "treatment_finished", "treatment_settle_finished", "reconnect_started",
        "reconnect_finished", "reconnect_settle_finished",
        "database_snapshot_recorded", "cleanup_completed", "cleanup_absence_confirmed",
    ]
    event_times = [0.0, 1.0, 2.0, 3.0, 4.5, 5.0, 6.0, 7.5, 8.0, 9.0, 10.0]
    cleanup = {
        "database": "kaetram_e2e",
        "deleted": {name: 1 for name in MONGO_COLLECTIONS},
        "lock_deleted": 1,
        "absence": absence,
        "complete": True,
    }
    return assemble_trial_receipt(
        plan=_plan(arm),
        treatment=treatment,
        reconnect=reconnect,
        database_snapshot=snapshot,
        cleanup=cleanup,
        seed=seed,
        parent_event_ledger=[
            {"event": event, "monotonic_seconds": at}
            for event, at in zip(events, event_times, strict=True)
        ],
        global_absence=absence,
        registration_sha256="a" * 64,
    )


def test_active_trial_passes_all_cumulative_predicates(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.opd.live_routing_multi_action_analyzer._attestation_and_lifecycle_valid",
        lambda *_: True,
    )
    analysis = classify_trial(_receipt("structured_direct"))
    assert analysis["validity"] == "valid"
    assert analysis["outcome"] == "pass"
    assert analysis["action_predicates"] == {
        "equip_item": True, "eat_food": True, "warp": True
    }


def test_off_trial_passes_only_with_zero_dispatch_and_baseline(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.opd.live_routing_multi_action_analyzer._attestation_and_lifecycle_valid",
        lambda *_: True,
    )
    analysis = classify_trial(_receipt("content_recovery_off"))
    assert analysis["validity"] == "valid"
    assert analysis["outcome"] == "pass"
    assert all(value is None for value in analysis["action_predicates"].values())


def test_lost_prior_effect_is_a_valid_behavioral_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.opd.live_routing_multi_action_analyzer._attestation_and_lifecycle_valid",
        lambda *_: True,
    )
    receipt = _receipt("structured_direct")
    lost = _observe_payload(eaten=True, warped=True)
    receipt["reconnect"]["reconnect"] = _semantic_record(lost)
    receipt["payload_sha256"] = canonical_sha256(
        {key: value for key, value in receipt.items() if key != "payload_sha256"}
    )
    analysis = classify_trial(receipt)
    assert analysis["validity"] == "valid"
    assert analysis["outcome"] == "fail"
    assert "reconnect_equip_item_predicate_failed" in analysis["failure_reasons"]


def test_raw_measurement_tampering_is_rejected_even_after_outer_rehash(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.opd.live_routing_multi_action_analyzer._attestation_and_lifecycle_valid",
        lambda *_: True,
    )
    receipt = _receipt("structured_direct")
    receipt["treatment"]["turns"][0]["immediate"]["semantic_projection"]["hp"] = 999
    receipt["payload_sha256"] = canonical_sha256(
        {key: value for key, value in receipt.items() if key != "payload_sha256"}
    )
    with pytest.raises(MultiActionAnalysisError, match="differs from raw evidence"):
        classify_trial(receipt)


def test_invalid_lifecycle_is_not_misreported_as_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.opd.live_routing_multi_action_analyzer._attestation_and_lifecycle_valid",
        lambda *_: False,
    )
    analysis = classify_trial(_receipt("structured_direct"))
    assert analysis["validity"] == "invalid"
    assert analysis["outcome"] == "not_assessable"
    assert analysis["failure_reasons"] == []


def _rehash(receipt: dict) -> None:
    receipt["payload_sha256"] = canonical_sha256(
        {key: value for key, value in receipt.items() if key != "payload_sha256"}
    )


def test_unknown_delivery_is_protocol_invalid(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.opd.live_routing_multi_action_analyzer._attestation_and_lifecycle_valid",
        lambda *_: True,
    )
    receipt = _receipt("structured_direct")
    turn = receipt["treatment"]["turns"][0]
    turn.update(
        delivery_status="unknown_after_exception",
        protocol_success=None,
        result_json=None,
        result_raw_text=None,
        result_raw_sha256=None,
    )
    receipt["execution_evidence"]["candidate_call_ledger"][0].update(
        delivery_status="unknown_after_exception",
        protocol_success=None,
        result_raw_sha256=None,
    )
    _rehash(receipt)
    analysis = classify_trial(receipt)
    assert analysis["validity"] == "invalid"
    assert "delivery_unknown_after_exception" in analysis["invalid_reasons"]


def test_unavailable_projection_is_invalid_not_analyzer_exception(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.opd.live_routing_multi_action_analyzer._attestation_and_lifecycle_valid",
        lambda *_: True,
    )
    receipt = _receipt("structured_direct")
    record = receipt["treatment"]["turns"][1]["delayed"]
    record.update(
        available=False,
        raw_text="unparseable evidence",
        raw_sha256=hashlib.sha256(b"unparseable evidence").hexdigest(),
        semantic_projection=None,
    )
    _rehash(receipt)
    analysis = classify_trial(receipt)
    assert analysis["validity"] == "invalid"
    assert "semantic_measurement_missing_or_unparseable" in analysis["invalid_reasons"]


def test_off_arm_dispatch_leak_is_explicit_behavioral_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.opd.live_routing_multi_action_analyzer._attestation_and_lifecycle_valid",
        lambda *_: True,
    )
    receipt = _receipt("content_recovery_off")
    receipt["treatment"]["turns"][0]["dispatch_attempted"] = True
    _rehash(receipt)
    analysis = classify_trial(receipt)
    assert analysis["validity"] == "valid"
    assert analysis["outcome"] == "fail"
    assert "turn_1_unexpected_dispatch" in analysis["failure_reasons"]


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (
            lambda receipt: receipt["treatment"]["turns"][0].__setitem__(
                "delayed_elapsed_monotonic_seconds", 1.49
            ),
            "delayed_observation_interval_unconfirmed",
        ),
        (lambda receipt: receipt["cleanup"].__setitem__("complete", False), "ownership_cleanup_unconfirmed"),
    ],
)
def test_delay_and_cleanup_failures_are_invalid(monkeypatch, mutate, reason) -> None:
    monkeypatch.setattr(
        "scripts.opd.live_routing_multi_action_analyzer._attestation_and_lifecycle_valid",
        lambda *_: True,
    )
    receipt = _receipt("structured_direct")
    mutate(receipt)
    _rehash(receipt)
    analysis = classify_trial(receipt)
    assert analysis["validity"] == "invalid"
    assert reason in analysis["invalid_reasons"]


def _identified_receipt(arm: str, index: int) -> dict:
    receipt = _receipt(arm)
    plan = receipt["plan"]
    plan["trial_id"] = f"trial-{index:02d}"
    plan["username"] = f"ma_run_{index:02d}"
    plan["treatment_session_id"] = f"llrma-run00001-t{index:02d}-treatment"
    plan["reconnect_session_id"] = f"llrma-run00001-t{index:02d}-reconnect"
    for phase_name in ("treatment", "reconnect"):
        phase = receipt[phase_name]
        phase["trial_id"] = plan["trial_id"]
        phase["username"] = plan["username"]
        phase["session_id"] = plan[f"{phase_name}_session_id"]
        parsed = phase["runtime_attestation"]["parsed"]
        offset = index * 1000 + (0 if phase_name == "treatment" else 100)
        parsed.update(
            mcp_pid=offset + 1,
            mcp_process_group=offset + 1,
            mcp_instance_nonce=f"{offset + 1:032x}",
            browser_pid=offset + 2,
            browser_process_group=offset + 2,
            browser_launch_nonce=f"{offset + 2:032x}",
        )
    evidence = receipt["execution_evidence"]
    seed = evidence["seed"]
    old_username = seed["username"]
    seed["username"] = plan["username"]
    seed["trial_id"] = plan["trial_id"]
    seed["absence"]["counts"][plan["username"]] = seed["absence"]["counts"].pop(old_username)
    identifiers = {LOCK_COLLECTION: plan["username"]}
    for position, name in enumerate(MONGO_COLLECTIONS, start=1):
        identifiers[name] = f"id-{index:02d}-{position:02d}"
    seed["inserted_ids"] = identifiers
    evidence["trial_absence"] = seed["absence"]
    evidence["global_absence"] = seed["absence"]
    evidence["database_snapshot_ownership"]["username"] = plan["username"]
    evidence["database_snapshot_ownership"]["document_ids"] = {
        name: identifiers[name] for name in MONGO_COLLECTIONS
    }
    cleanup = receipt["cleanup"]
    evidence["cleanup"] = cleanup
    database_raw = json.loads(receipt["database"]["raw_text"])
    database_raw["username"] = plan["username"]
    for name in MONGO_COLLECTIONS:
        database_raw["documents"][name]["username"] = plan["username"]
        database_raw["documents"][name]["_id"] = identifiers[name]
    raw = json.dumps(database_raw, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    receipt["database"]["raw_text"] = raw
    receipt["database"]["raw_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
    receipt["database"]["semantic_projection"] = semantic_gameplay_projection(database_raw)
    _rehash(receipt)
    return receipt


def test_aggregate_uses_exact_non_inferential_verdict_wording(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.opd.live_routing_multi_action_analyzer._attestation_and_lifecycle_valid",
        lambda *_: True,
    )
    receipts = [
        _identified_receipt(arm, index)
        for index, arm in enumerate(
            ["structured_direct", "content_recovery_on", "content_recovery_off"] * 3,
            start=1,
        )
    ]
    # The fixtures use structured router labels; make recovery-on labels exact.
    for receipt in receipts:
        if receipt["plan"]["arm"] == "content_recovery_on":
            for turn in receipt["treatment"]["turns"]:
                turn["router_status"] = "promoted"
    global_absence = {
        "database": "kaetram_e2e",
        "counts": {
            receipt["plan"]["username"]: {name: 0 for name in MONGO_COLLECTIONS}
            for receipt in receipts
        },
        "all_absent": True,
    }
    for receipt in receipts:
        receipt["execution_evidence"]["global_absence"] = global_absence
        _rehash(receipt)
    result = analyze_run(receipts)
    assert result["verdict"] == "complete"
    assert result["protocol_valid"] == 9
    assert result["full_predicate_pass"] == 9
    assert result["technical_repeats_are_independent"] is False
    assert "separately" in result["wording_guard"]


def test_unfrozen_candidate_ledger_call_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.opd.live_routing_multi_action_analyzer._attestation_and_lifecycle_valid",
        lambda *_: True,
    )
    receipt = _receipt("structured_direct")
    receipt["execution_evidence"]["candidate_call_ledger"][0]["arguments"] = {"slot": 4}
    _rehash(receipt)
    with pytest.raises(MultiActionAnalysisError, match="unfrozen call"):
        classify_trial(receipt)


def test_parent_event_order_and_snapshot_ownership_are_independent_invalidity_gates(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.opd.live_routing_multi_action_analyzer._attestation_and_lifecycle_valid",
        lambda *_: True,
    )
    receipt = _receipt("structured_direct")
    receipt["execution_evidence"]["parent_event_ledger"][4]["monotonic_seconds"] = 3.1
    receipt["execution_evidence"]["database_snapshot_ownership"]["document_ids"]["player_info"] = "wrong-id"
    _rehash(receipt)
    analysis = classify_trial(receipt)
    assert analysis["validity"] == "invalid"
    assert "parent_event_order_or_settle_unconfirmed" in analysis["invalid_reasons"]
    assert "database_snapshot_ownership_unconfirmed" in analysis["invalid_reasons"]


def test_duplicated_lifecycle_claim_cannot_diverge_from_phase(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.opd.live_routing_multi_action_analyzer._attestation_and_lifecycle_valid",
        lambda *_: True,
    )
    receipt = _receipt("structured_direct")
    receipt["execution_evidence"]["process_lifecycles"]["treatment"] = {"forged": True}
    _rehash(receipt)
    with pytest.raises(MultiActionAnalysisError, match="differs from phases"):
        classify_trial(receipt)
