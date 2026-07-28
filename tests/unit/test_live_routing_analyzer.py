from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts.opd.live_routing_analyzer import (
    AnalysisError,
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


def _unsigned_receipt(registration: dict, prelaunch: dict, plan: dict) -> dict:
    fixture = registration["state_fixture"]["expected"]
    arm = next(row for row in registration["arms"] if row["arm"] == plan["arm"])
    expected = arm["expected_stage_outcomes"]
    active = arm["expected_candidate_invocations"] == 1
    state = _mudwich_projection(fixture) if active else copy.deepcopy(fixture)
    return {
        "schema_version": "kaetram.live-routing-trial-receipt.v1",
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
            "database_player_id": f"player-{plan['schedule_index']:02d}",
        },
        "isolation": {
            "username_absence_confirmed": True,
            "create_only_seed_confirmed": True,
            "cold_mcp_process": True,
            "cold_browser_profile": True,
            "prior_trial_cleanup_confirmed": True,
            "mongo_database_every_operation": "kaetram_e2e",
            "runtime_lane_attested": True,
        },
        "precondition": {
            "available": True,
            "normalized_projection": copy.deepcopy(fixture),
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
            "result_raw_sha256": hashlib.sha256(b"warp result").hexdigest()
            if active
            else None,
        },
        "measurements": {
            "immediate": {"available": True, "normalized_projection": copy.deepcopy(state)},
            "delayed": {"available": True, "normalized_projection": copy.deepcopy(state)},
            "reconnect": {"available": True, "normalized_projection": copy.deepcopy(state)},
            "database": {"available": True, "normalized_projection": copy.deepcopy(state)},
            "delayed_elapsed_monotonic_seconds": 5.0,
        },
        "lifecycle": {
            "candidate_retry_count": 0,
            "event_order_valid": True,
            "treatment_session_closed_and_settled": True,
            "reconnect_session_closed_and_settled": True,
            "cleanup_absence_confirmed": True,
        },
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
        (0, lambda row: row["routing"].update(protocol_success=False), "unexpected_protocol_success"),
        (
            1,
            lambda row: row["routing"].update(
                dispatch_attempted=False,
                candidate_invocation_count=0,
                delivery_status="not_attempted",
                protocol_success=None,
                result_json=None,
                result_raw_sha256=None,
            ),
            "unexpected_dispatch_attempted",
        ),
        (
            2,
            lambda row: row["routing"].update(
                dispatch_attempted=True,
                candidate_invocation_count=1,
                delivery_status="confirmed",
                protocol_success=True,
                result_json={"warping": True, "warp_id": 0},
                result_raw_sha256="5" * 64,
            ),
            "unexpected_dispatch_attempted",
        ),
        (
            0,
            lambda row: row["measurements"]["immediate"][
                "normalized_projection"
            ].update(pos={"x": 1, "y": 1}),
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
            lambda row: row["routing"].update(
                delivery_status="unknown_after_exception"
            ),
            "delivery_unknown_after_exception",
        ),
        (
            lambda row: row["precondition"].update(normalized_projection={}),
            "precondition_missing_or_mismatch",
        ),
        (
            lambda row: row["measurements"]["delayed"].update(
                available=False, normalized_projection=None
            ),
            "applicable_measurement_missing_or_unparseable",
        ),
        (
            lambda row: row["isolation"].update(cold_browser_profile=False),
            "cold_session_unconfirmed",
        ),
        (
            lambda row: row["isolation"].update(
                mongo_database_every_operation="kaetram_devlopment"
            ),
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
