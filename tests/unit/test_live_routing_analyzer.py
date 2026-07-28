from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from canonical_start import CANONICAL_DATABASE_PROJECTION
from scripts.opd.live_routing_analyzer import (
    AnalysisError,
    EVENT_ORDER,
    LOCK_COLLECTION,
    MONGO_COLLECTIONS,
    SEED_INSERTION_ORDER,
    analyze_run,
    canonical_sha256,
)
from scripts.opd.live_routing_diagnostic import REPO_ROOT
from scripts.opd.live_routing_prelaunch import bind_trial_ids, derive_trial_identities


REGISTRATION_PATH = (
    REPO_ROOT / "research/experiments/local-live-routing-diagnostic-v1.json"
)
REGISTRATION_SHA = "1" * 64
PRELAUNCH_SHA = "2" * 64
CLAIM_SHA = "3" * 64
MANIFEST_SHA = "4" * 64
RUN_ID = "local001"


def _registration() -> dict:
    return json.loads(REGISTRATION_PATH.read_text())


def _prelaunch(registration: dict) -> dict:
    trials = bind_trial_ids(
        derive_trial_identities(registration, RUN_ID),
        study_id=registration["study_id"],
        run_id=RUN_ID,
        registration_sha256=REGISTRATION_SHA,
    )
    for trial in trials:
        trial.update(
            {
                "mongo_database": "kaetram_e2e",
                "candidate_sha256": registration["candidate"]["sha256"],
                "content_envelope_sha256": registration["candidate"][
                    "content_envelope_sha256"
                ],
                "precondition_sha256": canonical_sha256(
                    registration["state_fixture"]["expected"]
                ),
            }
        )
    return {
        "study_id": registration["study_id"],
        "run_id": RUN_ID,
        "registration": {"sha256": REGISTRATION_SHA},
        "claim_contract_sha256": CLAIM_SHA,
        "payload_sha256": PRELAUNCH_SHA,
        "trials": trials,
        "trial_plan_sha256": canonical_sha256(trials),
    }


def _mudwich_projection(fixture: dict) -> dict:
    projection = copy.deepcopy(fixture)
    projection["pos"] = {"x": 190, "y": 160}
    return projection


def _observe_raw(projection: dict) -> str:
    payload = copy.deepcopy(projection)
    payload["finished_quests"] = [
        {"name": name} for name in projection.get("finished_quests", [])
    ]
    return "observe: " + json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _database_raw(
    projection: dict,
    *,
    username: str,
    document_ids: dict[str, str],
) -> str:
    documents = {
        "player_info": {
            "x": projection["pos"]["x"],
            "y": projection["pos"]["y"],
            "hitPoints": projection["hit_points"],
        },
        "player_inventory": {
            "slots": [
                {
                    "index": item["slot"],
                    "key": item["key"],
                    "count": item["count"],
                }
                for item in projection["inventory"]
            ]
        },
        "player_bank": {"slots": []},
        "player_equipment": {"equipments": projection["equipment"]},
        "player_quests": {
            "quests": [
                {
                    "key": quest["key"],
                    "stage": quest["stage"],
                    "subStage": quest["sub_stage"],
                    "completedSubStages": quest["completed_sub_stages"],
                }
                for quest in projection["quests"]
            ]
        },
        "player_achievements": {"achievements": projection["achievements"]},
        "player_skills": {"skills": projection["skills"]},
        "player_statistics": copy.deepcopy(projection["statistics"]),
        "player_abilities": {"abilities": projection["abilities"]},
    }
    for collection, document in documents.items():
        document["_id"] = document_ids[collection]
        document["username"] = username
    return json.dumps(
        {"database": "kaetram_e2e", "username": username, "documents": documents},
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _measurement(
    projection: dict,
    *,
    database: bool = False,
    username: str | None = None,
    document_ids: dict[str, str] | None = None,
) -> dict:
    raw = (
        _database_raw(
            projection,
            username=username or "",
            document_ids=document_ids or {},
        )
        if database
        else _observe_raw(projection)
    )
    return {
        "available": True,
        "raw_text": raw,
        "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "normalized_projection": copy.deepcopy(projection),
    }


def _set_observation_projection(row: dict, stage: str, projection: dict) -> None:
    row["measurements"][stage] = _measurement(projection)


def _set_candidate_protocol(row: dict, protocol_success: bool) -> None:
    row["routing"]["protocol_success"] = protocol_success
    row["execution_evidence"]["candidate_call_ledger"][-1][
        "protocol_success"
    ] = protocol_success


def _set_no_candidate(row: dict) -> None:
    row["routing"].update(
        dispatch_attempted=False,
        candidate_invocation_count=0,
        delivery_status="not_attempted",
        protocol_success=None,
        result_json=None,
        result_raw_text=None,
        result_raw_sha256=None,
    )
    row["execution_evidence"]["candidate_call_ledger"] = []


def _set_confirmed_candidate(row: dict) -> None:
    raw = 'warp: {"warping":true,"warp_id":0}'
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    row["routing"].update(
        dispatch_attempted=True,
        candidate_invocation_count=1,
        delivery_status="confirmed",
        protocol_success=True,
        result_json={"warping": True, "warp_id": 0},
        result_raw_text=raw,
        result_raw_sha256=digest,
    )
    row["execution_evidence"]["candidate_call_ledger"] = [
        {
            "sequence": 1,
            "name": "warp",
            "arguments": {"location": "mudwich"},
            "delivery_status": "confirmed",
            "protocol_success": True,
            "result_raw_sha256": digest,
        }
    ]


def _set_unknown_delivery(row: dict) -> None:
    row["routing"].update(
        delivery_status="unknown_after_exception",
        protocol_success=None,
        result_json=None,
        result_raw_text=None,
        result_raw_sha256=None,
    )
    row["execution_evidence"]["candidate_call_ledger"][-1].update(
        delivery_status="unknown_after_exception",
        protocol_success=None,
        result_raw_sha256=None,
    )


def _set_attestation_field(record: dict, key: str, value) -> None:
    record["parsed"][key] = value
    raw = "__diagnostic_runtime_attestation: " + json.dumps(
        record["parsed"],
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    record["raw_text"] = raw
    record["raw_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _set_precondition_mismatch(row: dict) -> None:
    projection = copy.deepcopy(row["precondition"]["normalized_projection"])
    projection["pos"] = {"x": 1, "y": 1}
    row["precondition"] = _measurement(projection)


def _set_non_cold_browser(row: dict) -> None:
    runtime = row["execution_evidence"]["runtime_attestations"]
    _set_attestation_field(
        runtime["reconnect"],
        "browser_launch_nonce",
        runtime["treatment"]["parsed"]["browser_launch_nonce"],
    )
    _set_attestation_field(
        runtime["reconnect"],
        "browser_nonce_echo",
        runtime["treatment"]["parsed"]["browser_launch_nonce"],
    )


def _set_wrong_database_lane(row: dict) -> None:
    row["execution_evidence"]["absence"]["database"] = "wrong_database"


def _runtime_attestation(plan: dict, phase: str, live_contract: dict) -> dict:
    index = plan["schedule_index"]
    phase_offset = 1 if phase == "treatment" else 2
    token_base = index * 4 + phase_offset
    parsed = {
        "schema_version": "kaetram.diagnostic-runtime-attestation.v1",
        "session_id": plan[f"{phase}_session_id"],
        "mcp_pid": 1000 + index * 10 + phase_offset,
        "mcp_process_group": 1000 + index * 10 + phase_offset,
        "mcp_instance_nonce": f"{token_base:032x}",
        "browser_pid": 3000 + index * 10 + phase_offset,
        "browser_process_group": 3000 + index * 10 + phase_offset,
        "browser_launch_nonce": f"{token_base + 2:032x}",
        "browser_nonce_echo": f"{token_base + 2:032x}",
        "browser_name": live_contract["browser_name"],
        "browser_version": live_contract["browser_version"],
        "browser_executable_sha256": live_contract["browser_executable_sha256"],
        "page_url": "http://127.0.0.1:9000/",
        "player_username": plan["username"],
        "configured_client_url": "http://127.0.0.1:9000",
        "configured_game_port": "9191",
        "require_existing_account": True,
        "heartbeats_disabled": True,
        "loopback_only": True,
    }
    raw = "__diagnostic_runtime_attestation: " + json.dumps(
        parsed,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return {
        "raw_text": raw,
        "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "parsed": parsed,
    }


def _owner_envelope(parsed: dict, role: str) -> dict:
    if role == "mcp":
        owner = {
            "schema_version": "kaetram.diagnostic-mcp-owner.v1",
            "session_id": parsed["session_id"],
            "mcp_pid": parsed["mcp_pid"],
            "mcp_process_group": parsed["mcp_process_group"],
            "mcp_instance_nonce": parsed["mcp_instance_nonce"],
        }
    else:
        owner = {
            "schema_version": "kaetram.diagnostic-browser-owner.v1",
            "session_id": parsed["session_id"],
            "mcp_pid": parsed["mcp_pid"],
            "mcp_process_group": parsed["mcp_process_group"],
            "mcp_instance_nonce": parsed["mcp_instance_nonce"],
            "browser_pid": parsed["browser_pid"],
            "browser_process_group": parsed["browser_process_group"],
            "browser_launch_nonce": parsed["browser_launch_nonce"],
            "browser_executable_sha256": parsed["browser_executable_sha256"],
        }
    raw = json.dumps(owner, separators=(",", ":"), sort_keys=True) + "\n"
    return {
        "raw_text": raw,
        "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "parsed": owner,
    }


def _process_lifecycle(parsed: dict) -> dict:
    worker_pid = parsed["browser_pid"] + 20_000
    absent = {
        "found_alive": False,
        "sigkill_required": False,
        "still_alive": False,
    }
    return {
        "schema_version": "kaetram.session-lifecycle-cleanup.v1",
        "session_id": parsed["session_id"],
        "owner_receipts": {
            "mcp": _owner_envelope(parsed, "mcp"),
            "browser": _owner_envelope(parsed, "browser"),
        },
        "groups": {
            "worker": {
                "pid": worker_pid,
                "process_group": worker_pid,
                "identity_source": "spawned_worker",
                **absent,
            },
            "mcp": {
                "pid": parsed["mcp_pid"],
                "process_group": parsed["mcp_process_group"],
                "identity_source": "mcp_owner_receipt",
                **absent,
            },
            "browser": {
                "pid": parsed["browser_pid"],
                "process_group": parsed["browser_process_group"],
                "identity_source": "browser_owner_receipt",
                **absent,
            },
        },
        "cleanup_order": ["browser", "mcp", "worker"],
        "unexpected_process_groups": [],
        "closure_proven": True,
    }


def _execution_evidence(
    plan: dict,
    *,
    active: bool,
    result_sha: str | None,
    live_contract: dict,
) -> dict:
    username = plan["username"]
    index = plan["schedule_index"]
    document_ids = {
        collection: f"{plan['trial_id']}-{collection}" for collection in MONGO_COLLECTIONS
    }
    inserted_ids = {
        LOCK_COLLECTION: f"{plan['trial_id']}-{LOCK_COLLECTION}",
        **document_ids,
    }
    base = float(index * 100)
    offsets = (0, 1, 2, 3, 5, 6, 7, 9, 10, 11, 12)
    candidate_calls = (
        [
            {
                "sequence": 1,
                "name": "warp",
                "arguments": {"location": "mudwich"},
                "delivery_status": "confirmed",
                "protocol_success": True,
                "result_raw_sha256": result_sha,
            }
        ]
        if active
        else []
    )
    treatment_runtime = _runtime_attestation(plan, "treatment", live_contract)
    reconnect_runtime = _runtime_attestation(plan, "reconnect", live_contract)
    return {
        "absence": {
            "database": "kaetram_e2e",
            "username": username,
            "counts": {collection: 0 for collection in MONGO_COLLECTIONS},
            "all_absent": True,
        },
        "seed": {
            "database": "kaetram_e2e",
            "username": username,
            "trial_id": plan["trial_id"],
            "inserted_ids": inserted_ids,
            "insertion_order": list(SEED_INSERTION_ORDER),
            "player_info_inserted_last": True,
        },
        "runtime_attestations": {
            "treatment": treatment_runtime,
            "reconnect": reconnect_runtime,
        },
        "process_lifecycles": {
            "treatment": _process_lifecycle(treatment_runtime["parsed"]),
            "reconnect": _process_lifecycle(reconnect_runtime["parsed"]),
        },
        "parent_event_ledger": [
            {"event": event, "monotonic_seconds": base + offset}
            for event, offset in zip(EVENT_ORDER, offsets, strict=True)
        ],
        "candidate_call_ledger": candidate_calls,
        "database_snapshot_ownership": {
            "database": "kaetram_e2e",
            "username": username,
            "document_ids": document_ids,
        },
        "cleanup": {
            "database": "kaetram_e2e",
            "username": username,
            "trial_id": plan["trial_id"],
            "deleted_counts": {collection: 1 for collection in MONGO_COLLECTIONS},
            "lock_deleted": 1,
            "post_cleanup_counts": {collection: 0 for collection in MONGO_COLLECTIONS},
            "all_absent": True,
        },
    }


def _unsigned_receipt(registration: dict, prelaunch: dict, plan: dict) -> dict:
    fixture = registration["state_fixture"]["expected"]
    arm = next(row for row in registration["arms"] if row["arm"] == plan["arm"])
    expected = arm["expected_stage_outcomes"]
    active = arm["expected_candidate_invocations"] == 1
    state = _mudwich_projection(fixture) if active else copy.deepcopy(fixture)
    database_state = copy.deepcopy(CANONICAL_DATABASE_PROJECTION)
    if active:
        database_state["pos"] = {"x": 190, "y": 160}
    result_raw = (
        'warp: {"warping":true,"warp_id":0}' if active else None
    )
    result_sha = (
        hashlib.sha256(result_raw.encode("utf-8")).hexdigest()
        if result_raw is not None
        else None
    )
    execution = _execution_evidence(
        plan,
        active=active,
        result_sha=result_sha,
        live_contract=registration["live_contract"],
    )
    document_ids = execution["database_snapshot_ownership"]["document_ids"]
    return {
        "schema_version": "kaetram.live-routing-trial-receipt.v2",
        "study_id": prelaunch["study_id"],
        "run_id": prelaunch["run_id"],
        "registration_sha256": prelaunch["registration"]["sha256"],
        "claim_contract_sha256": prelaunch["claim_contract_sha256"],
        "prelaunch_payload_sha256": prelaunch["payload_sha256"],
        "trial_plan_sha256": prelaunch["trial_plan_sha256"],
        "previous_receipt_payload_sha256": "",
        "plan": copy.deepcopy(plan),
        "observed_identity": {
            "username": plan["username"],
            "treatment_session_id": plan["treatment_session_id"],
            "reconnect_session_id": plan["reconnect_session_id"],
            "database_player_id": document_ids["player_info"],
        },
        "precondition": {
            **_measurement(fixture),
        },
        "routing": {
            "router_status": expected["router_status"],
            "schema_status": expected["schema_status"],
            "dispatch_attempted": expected["dispatch_attempted"],
            "candidate_invocation_count": arm["expected_candidate_invocations"],
            "delivery_status": expected["delivery_status"],
            "protocol_success": expected["protocol_success"],
            "tool_reported_error": expected["tool_reported_error"],
            "result_json": {"warping": True, "warp_id": 0} if active else None,
            "result_raw_text": result_raw,
            "result_raw_sha256": result_sha,
        },
        "measurements": {
            "immediate": _measurement(state),
            "delayed": _measurement(state),
            "reconnect": _measurement(state),
            "database": _measurement(
                database_state,
                database=True,
                username=plan["username"],
                document_ids=document_ids,
            ),
            "delayed_elapsed_monotonic_seconds": 5.0,
        },
        "execution_evidence": execution,
    }


def _resign(receipts: list[dict], prelaunch: dict) -> None:
    previous = prelaunch["payload_sha256"]
    for receipt in receipts:
        receipt["previous_receipt_payload_sha256"] = previous
        receipt.pop("payload_sha256", None)
        receipt["payload_sha256"] = canonical_sha256(receipt)
        previous = receipt["payload_sha256"]


def _complete() -> tuple[dict, dict, list[dict]]:
    registration = _registration()
    prelaunch = _prelaunch(registration)
    receipts = [
        _unsigned_receipt(registration, prelaunch, plan)
        for plan in prelaunch["trials"]
    ]
    _resign(receipts, prelaunch)
    return registration, prelaunch, receipts


def test_all_nine_pass_releases_descriptive_grid() -> None:
    registration, prelaunch, receipts = _complete()
    analysis = analyze_run(
        registration, prelaunch, receipts, manifest_payload_sha256=MANIFEST_SHA
    )
    assert analysis["verdict"] == "complete_all_pass"
    assert analysis["paired_aggregate"]["status"] == "released_descriptive_only"
    assert all(row["outcome"] == "pass" for row in analysis["trials"])


@pytest.mark.parametrize(
    ("index", "mutate", "expected_reason"),
    [
        (0, lambda row: _set_candidate_protocol(row, False), "unexpected_protocol_success"),
        (
            1,
            _set_no_candidate,
            "unexpected_dispatch_attempted",
        ),
        (
            2,
            _set_confirmed_candidate,
            "unexpected_dispatch_attempted",
        ),
        (
            0,
            lambda row: _set_observation_projection(
                row,
                "immediate",
                {
                    **row["measurements"]["immediate"]["normalized_projection"],
                    "pos": {"x": 1, "y": 1},
                },
            ),
            "immediate_mudwich_state_predicate_failed",
        ),
    ],
)
def test_known_behavior_deviations_are_valid_failures(
    index: int, mutate, expected_reason: str
) -> None:
    registration, prelaunch, receipts = _complete()
    mutate(receipts[index])
    _resign(receipts, prelaunch)
    analysis = analyze_run(
        registration, prelaunch, receipts, manifest_payload_sha256=MANIFEST_SHA
    )
    row = analysis["trials"][index]
    assert row["validity"] == "valid"
    assert row["outcome"] == "fail"
    assert expected_reason in row["failure_reasons"]
    assert analysis["verdict"] == "complete_with_failures"
    assert analysis["paired_aggregate"]["status"] == "released_descriptive_only"


@pytest.mark.parametrize(
    ("mutate", "expected_reason"),
    [
        (
            _set_unknown_delivery,
            "delivery_unknown_after_exception",
        ),
        (
            _set_precondition_mismatch,
            "precondition_missing_or_mismatch",
        ),
        (
            lambda row: row["measurements"]["delayed"].update(
                available=False,
                raw_text=None,
                raw_sha256=None,
                normalized_projection=None,
            ),
            "applicable_measurement_missing_or_unparseable",
        ),
        (
            _set_non_cold_browser,
            "cold_session_unconfirmed",
        ),
        (
            _set_wrong_database_lane,
            "wrong_database_or_runtime_lane",
        ),
    ],
)
def test_integrity_failures_are_invalid_and_withhold_only_aggregate(
    mutate, expected_reason: str
) -> None:
    registration, prelaunch, receipts = _complete()
    mutate(receipts[0])
    _resign(receipts, prelaunch)
    analysis = analyze_run(
        registration, prelaunch, receipts, manifest_payload_sha256=MANIFEST_SHA
    )
    row = analysis["trials"][0]
    assert row["validity"] == "invalid"
    assert row["outcome"] == "not_assessable"
    assert expected_reason in row["invalid_reasons"]
    assert analysis["verdict"] == "incomplete_no_paired_verdict"
    assert analysis["paired_aggregate"]["status"] == "withheld_invalid_trials"
    assert len(analysis["trials"]) == 9


def test_duplicate_database_player_identity_invalidates_affected_trials() -> None:
    registration, prelaunch, receipts = _complete()
    receipts[1]["observed_identity"]["database_player_id"] = receipts[0][
        "observed_identity"
    ]["database_player_id"]
    _resign(receipts, prelaunch)
    analysis = analyze_run(
        registration, prelaunch, receipts, manifest_payload_sha256=MANIFEST_SHA
    )
    assert [row["validity"] for row in analysis["trials"][:2]] == [
        "invalid",
        "invalid",
    ]


def test_candidate_raw_duplicate_key_is_rejected_even_when_rehashed() -> None:
    registration, prelaunch, receipts = _complete()
    raw = 'warp: {"warping":true,"warping":false,"warp_id":0}'
    receipts[0]["routing"].update(
        result_raw_text=raw,
        result_raw_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )
    receipts[0]["execution_evidence"]["candidate_call_ledger"][0][
        "result_raw_sha256"
    ] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    _resign(receipts, prelaunch)
    with pytest.raises(AnalysisError, match="raw evidence"):
        analyze_run(
            registration, prelaunch, receipts, manifest_payload_sha256=MANIFEST_SHA
        )


def test_observation_raw_nonfinite_value_is_rejected_even_when_rehashed() -> None:
    registration, prelaunch, receipts = _complete()
    raw = 'observe: {"pos":{"x":NaN,"y":160}}'
    receipts[0]["measurements"]["immediate"].update(
        raw_text=raw,
        raw_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )
    _resign(receipts, prelaunch)
    with pytest.raises(AnalysisError, match="raw evidence"):
        analyze_run(
            registration, prelaunch, receipts, manifest_payload_sha256=MANIFEST_SHA
        )


def test_database_projection_mismatch_is_rejected_even_when_rehashed() -> None:
    registration, prelaunch, receipts = _complete()
    receipts[0]["measurements"]["database"]["normalized_projection"]["pos"] = {
        "x": 1,
        "y": 1,
    }
    _resign(receipts, prelaunch)
    with pytest.raises(AnalysisError, match="database projection differs from raw evidence"):
        analyze_run(
            registration, prelaunch, receipts, manifest_payload_sha256=MANIFEST_SHA
        )


@pytest.mark.parametrize(
    "identity_field",
    ["mcp_pid", "mcp_process_group", "mcp_instance_nonce"],
)
def test_duplicate_runtime_identity_invalidates_every_affected_trial(
    identity_field: str,
) -> None:
    registration, prelaunch, receipts = _complete()
    first = receipts[0]["execution_evidence"]["runtime_attestations"]["treatment"]
    second = receipts[1]["execution_evidence"]["runtime_attestations"]["treatment"]
    _set_attestation_field(
        second,
        identity_field,
        first["parsed"][identity_field],
    )
    _resign(receipts, prelaunch)
    analysis = analyze_run(
        registration, prelaunch, receipts, manifest_payload_sha256=MANIFEST_SHA
    )
    assert [analysis["trials"][index]["validity"] for index in (0, 1)] == [
        "invalid",
        "invalid",
    ]
    assert analysis["paired_aggregate"]["status"] == "withheld_invalid_trials"


def test_rehashed_runtime_with_nonleader_process_group_is_invalid() -> None:
    registration, prelaunch, receipts = _complete()
    treatment = receipts[0]["execution_evidence"]["runtime_attestations"][
        "treatment"
    ]
    _set_attestation_field(
        treatment,
        "mcp_process_group",
        treatment["parsed"]["mcp_pid"] + 1,
    )
    _resign(receipts, prelaunch)
    analysis = analyze_run(
        registration, prelaunch, receipts, manifest_payload_sha256=MANIFEST_SHA
    )
    assert analysis["trials"][0]["validity"] == "invalid"
    assert "cold_session_unconfirmed" in analysis["trials"][0]["invalid_reasons"]
    assert analysis["paired_aggregate"]["status"] == "withheld_invalid_trials"


def test_rehashed_false_process_closure_withholds_aggregate() -> None:
    registration, prelaunch, receipts = _complete()
    lifecycle = receipts[0]["execution_evidence"]["process_lifecycles"][
        "treatment"
    ]
    lifecycle["closure_proven"] = False
    _resign(receipts, prelaunch)
    analysis = analyze_run(
        registration, prelaunch, receipts, manifest_payload_sha256=MANIFEST_SHA
    )
    assert analysis["trials"][0]["validity"] == "invalid"
    assert "cold_session_unconfirmed" in analysis["trials"][0]["invalid_reasons"]
    assert "session_order_or_settle_violation" in analysis["trials"][0][
        "invalid_reasons"
    ]
    assert analysis["paired_aggregate"]["status"] == "withheld_invalid_trials"


def test_rehashed_wrong_owner_session_withholds_aggregate() -> None:
    registration, prelaunch, receipts = _complete()
    owner = receipts[0]["execution_evidence"]["process_lifecycles"]["treatment"][
        "owner_receipts"
    ]["mcp"]
    owner["parsed"]["session_id"] = "llrd-wrong-session"
    raw = json.dumps(owner["parsed"], separators=(",", ":"), sort_keys=True) + "\n"
    owner["raw_text"] = raw
    owner["raw_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    _resign(receipts, prelaunch)
    analysis = analyze_run(
        registration, prelaunch, receipts, manifest_payload_sha256=MANIFEST_SHA
    )
    assert analysis["trials"][0]["validity"] == "invalid"
    assert "cold_session_unconfirmed" in analysis["trials"][0]["invalid_reasons"]
    assert analysis["paired_aggregate"]["status"] == "withheld_invalid_trials"


def test_snapshot_id_mismatch_cannot_hide_behind_true_seed_summary() -> None:
    registration, prelaunch, receipts = _complete()
    receipts[0]["execution_evidence"]["database_snapshot_ownership"][
        "document_ids"
    ]["player_info"] = "different-player-info-id"
    _resign(receipts, prelaunch)
    analysis = analyze_run(
        registration, prelaunch, receipts, manifest_payload_sha256=MANIFEST_SHA
    )
    assert analysis["trials"][0]["validity"] == "invalid"
    assert "create_only_seed_unconfirmed" in analysis["trials"][0][
        "invalid_reasons"
    ]


def test_short_treatment_settle_is_derived_as_invalid() -> None:
    registration, prelaunch, receipts = _complete()
    ledger = receipts[0]["execution_evidence"]["parent_event_ledger"]
    finished = next(
        row["monotonic_seconds"]
        for row in ledger
        if row["event"] == "treatment_finished"
    )
    next(
        row for row in ledger if row["event"] == "treatment_settle_finished"
    )["monotonic_seconds"] = finished + 1.49
    _resign(receipts, prelaunch)
    analysis = analyze_run(
        registration, prelaunch, receipts, manifest_payload_sha256=MANIFEST_SHA
    )
    assert analysis["trials"][0]["validity"] == "invalid"
    assert "session_order_or_settle_violation" in analysis["trials"][0][
        "invalid_reasons"
    ]


def test_extra_candidate_event_derives_retry_and_invalidates_trial() -> None:
    registration, prelaunch, receipts = _complete()
    calls = receipts[0]["execution_evidence"]["candidate_call_ledger"]
    duplicate = copy.deepcopy(calls[0])
    duplicate["sequence"] = 2
    calls.append(duplicate)
    receipts[0]["routing"]["candidate_invocation_count"] = 2
    _resign(receipts, prelaunch)
    analysis = analyze_run(
        registration, prelaunch, receipts, manifest_payload_sha256=MANIFEST_SHA
    )
    assert analysis["trials"][0]["validity"] == "invalid"
    assert "unregistered_candidate_retry" in analysis["trials"][0][
        "invalid_reasons"
    ]


def test_residual_cleanup_is_derived_as_invalid() -> None:
    registration, prelaunch, receipts = _complete()
    cleanup = receipts[0]["execution_evidence"]["cleanup"]
    cleanup["post_cleanup_counts"]["player_info"] = 1
    cleanup["all_absent"] = False
    _resign(receipts, prelaunch)
    analysis = analyze_run(
        registration, prelaunch, receipts, manifest_payload_sha256=MANIFEST_SHA
    )
    assert analysis["trials"][0]["validity"] == "invalid"
    assert analysis["trials"][1]["validity"] == "invalid"
    assert "cleanup_absence_unconfirmed" in analysis["trials"][0][
        "invalid_reasons"
    ]
    assert "session_order_or_settle_violation" in analysis["trials"][1][
        "invalid_reasons"
    ]


def test_plan_or_self_hash_tampering_refuses_analysis() -> None:
    registration, prelaunch, receipts = _complete()
    receipts[0]["plan"]["arm"] = "content_recovery_off"
    with pytest.raises(AnalysisError, match="self-hash mismatch"):
        analyze_run(
            registration, prelaunch, receipts, manifest_payload_sha256=MANIFEST_SHA
        )
    _resign(receipts, prelaunch)
    with pytest.raises(AnalysisError, match="plan differs"):
        analyze_run(
            registration, prelaunch, receipts, manifest_payload_sha256=MANIFEST_SHA
        )


def test_missing_or_extra_trial_refuses_analysis() -> None:
    registration, prelaunch, receipts = _complete()
    with pytest.raises(AnalysisError, match="exactly nine"):
        analyze_run(
            registration, prelaunch, receipts[:-1], manifest_payload_sha256=MANIFEST_SHA
        )
