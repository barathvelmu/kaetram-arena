#!/usr/bin/env python3
"""Pure offline analysis for the frozen local live-routing diagnostic.

Raw receipts never contain author-supplied verdicts.  This module derives
package integrity, trial validity, and behavioral pass/fail separately.  It
imports no browser, MCP, MongoDB, model, or network client.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


ANALYSIS_SCHEMA_VERSION = "kaetram.live-routing-diagnostic-analysis.v1"
TRIAL_SCHEMA_VERSION = "kaetram.live-routing-trial-receipt.v1"
SHA256_KEYS = {
    "registration_sha256",
    "claim_contract_sha256",
    "prelaunch_payload_sha256",
    "trial_plan_sha256",
    "previous_receipt_payload_sha256",
    "payload_sha256",
}
TRIAL_KEYS = {
    "schema_version",
    "study_id",
    "run_id",
    "registration_sha256",
    "claim_contract_sha256",
    "prelaunch_payload_sha256",
    "trial_plan_sha256",
    "previous_receipt_payload_sha256",
    "plan",
    "observed_identity",
    "isolation",
    "precondition",
    "routing",
    "measurements",
    "lifecycle",
    "payload_sha256",
}
IDENTITY_KEYS = {
    "username",
    "treatment_session_id",
    "reconnect_session_id",
    "database_player_id",
}
ISOLATION_KEYS = {
    "username_absence_confirmed",
    "create_only_seed_confirmed",
    "cold_mcp_process",
    "cold_browser_profile",
    "prior_trial_cleanup_confirmed",
    "mongo_database_every_operation",
    "runtime_lane_attested",
}
PRECONDITION_KEYS = {"available", "normalized_projection"}
ROUTING_KEYS = {
    "router_status",
    "schema_status",
    "dispatch_attempted",
    "candidate_invocation_count",
    "delivery_status",
    "protocol_success",
    "tool_reported_error",
    "result_json",
    "result_raw_sha256",
}
MEASUREMENT_KEYS = {
    "immediate",
    "delayed",
    "reconnect",
    "database",
    "delayed_elapsed_monotonic_seconds",
}
MEASUREMENT_RECORD_KEYS = {"available", "normalized_projection"}
LIFECYCLE_KEYS = {
    "candidate_retry_count",
    "event_order_valid",
    "treatment_session_closed_and_settled",
    "reconnect_session_closed_and_settled",
    "cleanup_absence_confirmed",
}


class AnalysisError(ValueError):
    """The artifact envelope is malformed; no scientific analysis is safe."""


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AnalysisError(f"non-canonical JSON value: {exc}") from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def json_equal(left: Any, right: Any) -> bool:
    return canonical_json_bytes(left) == canonical_json_bytes(right)


def _exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise AnalysisError(f"{label} key set drift")
    return value


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def validate_trial_envelope(receipt: dict[str, Any]) -> None:
    _exact_object(receipt, TRIAL_KEYS, "trial receipt")
    if receipt.get("schema_version") != TRIAL_SCHEMA_VERSION:
        raise AnalysisError("trial receipt schema drift")
    for key in SHA256_KEYS:
        if not _is_sha256(receipt.get(key)):
            raise AnalysisError(f"invalid trial digest: {key}")
    _exact_object(receipt.get("observed_identity"), IDENTITY_KEYS, "observed identity")
    _exact_object(receipt.get("isolation"), ISOLATION_KEYS, "isolation")
    _exact_object(receipt.get("precondition"), PRECONDITION_KEYS, "precondition")
    _exact_object(receipt.get("routing"), ROUTING_KEYS, "routing")
    measurements = _exact_object(
        receipt.get("measurements"), MEASUREMENT_KEYS, "measurements"
    )
    for stage in ("immediate", "delayed", "reconnect", "database"):
        _exact_object(measurements.get(stage), MEASUREMENT_RECORD_KEYS, stage)
    _exact_object(receipt.get("lifecycle"), LIFECYCLE_KEYS, "lifecycle")
    unsigned = {key: value for key, value in receipt.items() if key != "payload_sha256"}
    if receipt["payload_sha256"] != canonical_sha256(unsigned):
        raise AnalysisError("trial receipt self-hash mismatch")


def _append_once(values: list[str], reason: str) -> None:
    if reason not in values:
        values.append(reason)


def _position_in_region(projection: Any, region: dict[str, Any]) -> bool:
    if not isinstance(projection, dict) or not isinstance(projection.get("pos"), dict):
        return False
    x = projection["pos"].get("x")
    y = projection["pos"].get("y")
    if type(x) not in (int, float) or type(y) not in (int, float):
        return False
    return (
        region["x_min"] <= x <= region["x_max"]
        and region["y_min"] <= y <= region["y_max"]
    )


def _arm(registration: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [arm for arm in registration["arms"] if arm.get("arm") == name]
    if len(matches) != 1:
        raise AnalysisError(f"registered arm is not unique: {name}")
    return matches[0]


def classify_trial(
    registration: dict[str, Any],
    planned_trial: dict[str, Any],
    receipt: dict[str, Any],
) -> dict[str, Any]:
    """Derive one trial verdict; behavioral deviations remain valid failures."""

    validate_trial_envelope(receipt)
    if not json_equal(receipt.get("plan"), planned_trial):
        raise AnalysisError("trial receipt plan differs from prelaunch plan")

    invalid: list[str] = []
    failures: list[str] = []
    identity = receipt["observed_identity"]
    isolation = receipt["isolation"]
    precondition = receipt["precondition"]
    routing = receipt["routing"]
    measurements = receipt["measurements"]
    lifecycle = receipt["lifecycle"]

    for key in ("username", "treatment_session_id", "reconnect_session_id"):
        if identity.get(key) != planned_trial.get(key):
            _append_once(invalid, "identity_mismatch_or_reuse")
    if not isinstance(identity.get("database_player_id"), str) or not identity[
        "database_player_id"
    ]:
        _append_once(invalid, "identity_mismatch_or_reuse")
    if isolation.get("username_absence_confirmed") is not True:
        _append_once(invalid, "username_absence_unconfirmed")
    if isolation.get("create_only_seed_confirmed") is not True:
        _append_once(invalid, "create_only_seed_unconfirmed")
    if (
        isolation.get("cold_mcp_process") is not True
        or isolation.get("cold_browser_profile") is not True
    ):
        _append_once(invalid, "cold_session_unconfirmed")
    if (
        isolation.get("mongo_database_every_operation") != "kaetram_e2e"
        or isolation.get("runtime_lane_attested") is not True
    ):
        _append_once(invalid, "wrong_database_or_runtime_lane")
    if isolation.get("prior_trial_cleanup_confirmed") is not True:
        _append_once(invalid, "session_order_or_settle_violation")

    fixture = registration["state_fixture"]["expected"]
    if (
        precondition.get("available") is not True
        or not json_equal(precondition.get("normalized_projection"), fixture)
    ):
        _append_once(invalid, "precondition_missing_or_mismatch")

    for stage in ("immediate", "delayed", "reconnect", "database"):
        measurement = measurements[stage]
        if measurement.get("available") is not True or not isinstance(
            measurement.get("normalized_projection"), dict
        ):
            _append_once(invalid, "applicable_measurement_missing_or_unparseable")
    elapsed = measurements.get("delayed_elapsed_monotonic_seconds")
    minimum_delay = registration["runtime_parameters"][
        "minimum_delayed_observation_seconds"
    ]
    if type(elapsed) not in (int, float) or elapsed < minimum_delay:
        _append_once(invalid, "applicable_measurement_missing_or_unparseable")
    if routing.get("delivery_status") == "unknown_after_exception":
        _append_once(invalid, "delivery_unknown_after_exception")
    if not json_equal(
        lifecycle.get("candidate_retry_count"),
        registration["runtime_parameters"]["candidate_retry_count"],
    ):
        _append_once(invalid, "unregistered_candidate_retry")
    if (
        lifecycle.get("event_order_valid") is not True
        or lifecycle.get("treatment_session_closed_and_settled") is not True
        or lifecycle.get("reconnect_session_closed_and_settled") is not True
    ):
        _append_once(invalid, "session_order_or_settle_violation")
    if lifecycle.get("cleanup_absence_confirmed") is not True:
        _append_once(invalid, "cleanup_absence_unconfirmed")

    if invalid:
        return {
            "trial_id": planned_trial["trial_id"],
            "trial_key": planned_trial["trial_key"],
            "repeat": planned_trial["repeat"],
            "arm": planned_trial["arm"],
            "validity": "invalid",
            "outcome": "not_assessable",
            "invalid_reasons": invalid,
            "failure_reasons": [],
        }

    arm = _arm(registration, planned_trial["arm"])
    expected = arm["expected_stage_outcomes"]
    for key in (
        "router_status",
        "schema_status",
        "dispatch_attempted",
        "delivery_status",
        "protocol_success",
        "tool_reported_error",
    ):
        if not json_equal(routing.get(key), expected[key]):
            failures.append(f"unexpected_{key}")
    if not json_equal(
        routing.get("candidate_invocation_count"),
        arm["expected_candidate_invocations"],
    ):
        failures.append("unexpected_candidate_invocation_count")

    if arm["expected_candidate_invocations"] == 1:
        if routing.get("delivery_status") == "confirmed" and not _is_sha256(
            routing.get("result_raw_sha256")
        ):
            failures.append("missing_confirmed_result_digest")
        required = registration["measurement"]["warp_application_acceptance"][
            "result_json_required"
        ]
        result = routing.get("result_json")
        if not isinstance(result, dict) or any(
            not json_equal(result.get(key), value) for key, value in required.items()
        ):
            failures.append("warp_application_predicate_failed")
        region = registration["measurement"]["mudwich_success_region"]
        for stage in ("immediate", "delayed", "reconnect"):
            if not _position_in_region(
                measurements[stage]["normalized_projection"], region
            ):
                failures.append(f"{stage}_mudwich_state_predicate_failed")
    else:
        if routing.get("result_raw_sha256") is not None or routing.get(
            "result_json"
        ) is not None:
            failures.append("off_arm_unexpected_candidate_result")
        for stage in ("immediate", "delayed", "reconnect", "database"):
            if not json_equal(
                measurements[stage]["normalized_projection"], fixture
            ):
                failures.append(f"{stage}_baseline_state_predicate_failed")

    return {
        "trial_id": planned_trial["trial_id"],
        "trial_key": planned_trial["trial_key"],
        "repeat": planned_trial["repeat"],
        "arm": planned_trial["arm"],
        "validity": "valid",
        "outcome": "pass" if not failures else "fail",
        "invalid_reasons": [],
        "failure_reasons": failures,
    }


def analyze_run(
    registration: dict[str, Any],
    prelaunch: dict[str, Any],
    ordered_receipts: list[dict[str, Any]],
    *,
    manifest_payload_sha256: str,
) -> dict[str, Any]:
    """Recompute the complete descriptive result from nine ordered receipts."""

    if not _is_sha256(manifest_payload_sha256):
        raise AnalysisError("manifest payload digest is invalid")
    plans = prelaunch.get("trials")
    if not isinstance(plans, list) or len(plans) != 9 or len(ordered_receipts) != 9:
        raise AnalysisError("exactly nine planned and observed trials are required")
    prelaunch_hash = prelaunch.get("payload_sha256")
    plan_hash = prelaunch.get("trial_plan_sha256")
    registration_hash = prelaunch.get("registration", {}).get("sha256")
    claim_hash = prelaunch.get("claim_contract_sha256")
    for value, label in (
        (prelaunch_hash, "prelaunch"),
        (plan_hash, "trial plan"),
        (registration_hash, "registration"),
        (claim_hash, "claim contract"),
    ):
        if not _is_sha256(value):
            raise AnalysisError(f"{label} digest is invalid")

    classifications: list[dict[str, Any]] = []
    previous_hash = prelaunch_hash
    for plan, receipt in zip(plans, ordered_receipts, strict=True):
        validate_trial_envelope(receipt)
        expected_refs = {
            "study_id": prelaunch["study_id"],
            "run_id": prelaunch["run_id"],
            "registration_sha256": registration_hash,
            "claim_contract_sha256": claim_hash,
            "prelaunch_payload_sha256": prelaunch_hash,
            "trial_plan_sha256": plan_hash,
            "previous_receipt_payload_sha256": previous_hash,
        }
        for key, expected in expected_refs.items():
            if receipt.get(key) != expected:
                raise AnalysisError(f"trial receipt reference drift: {key}")
        classifications.append(classify_trial(registration, plan, receipt))
        previous_hash = receipt["payload_sha256"]

    player_ids = [
        receipt["observed_identity"]["database_player_id"]
        for receipt in ordered_receipts
    ]
    player_id_tokens = [canonical_json_bytes(value) for value in player_ids]
    if len(set(player_id_tokens)) != 9:
        for index, player_id in enumerate(player_ids):
            if player_id_tokens.count(canonical_json_bytes(player_id)) > 1:
                result = classifications[index]
                result["validity"] = "invalid"
                result["outcome"] = "not_assessable"
                _append_once(result["invalid_reasons"], "identity_mismatch_or_reuse")
                result["failure_reasons"] = []

    invalid_ids = [
        result["trial_id"]
        for result in classifications
        if result["validity"] == "invalid"
    ]
    if invalid_ids:
        verdict = "incomplete_no_paired_verdict"
        paired = {
            "status": "withheld_invalid_trials",
            "invalid_trial_ids": invalid_ids,
        }
    else:
        verdict = (
            "complete_all_pass"
            if all(result["outcome"] == "pass" for result in classifications)
            else "complete_with_failures"
        )
        arm_counts = {}
        for arm in ("structured_direct", "content_recovery_on", "content_recovery_off"):
            rows = [result for result in classifications if result["arm"] == arm]
            arm_counts[arm] = {
                "passes": sum(result["outcome"] == "pass" for result in rows),
                "scheduled": 3,
            }
        repeat_outcomes = []
        for repeat in (1, 2, 3):
            rows = [result for result in classifications if result["repeat"] == repeat]
            repeat_outcomes.append(
                {
                    "repeat": repeat,
                    "outcome": (
                        "pass" if all(row["outcome"] == "pass" for row in rows) else "fail"
                    ),
                }
            )
        paired = {
            "status": "released_descriptive_only",
            "technical_repeats_are_independent_samples": False,
            "arm_counts": arm_counts,
            "repeat_outcomes": repeat_outcomes,
            "inferential_statistics": "forbidden",
        }

    analysis: dict[str, Any] = {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "study_id": prelaunch["study_id"],
        "run_id": prelaunch["run_id"],
        "manifest_payload_sha256": manifest_payload_sha256,
        "claim_contract_sha256": claim_hash,
        "claim_boundary": registration["claim_boundary"],
        "verdict": verdict,
        "trials": classifications,
        "paired_aggregate": paired,
    }
    analysis["analysis_payload_sha256"] = canonical_sha256(analysis)
    return analysis
