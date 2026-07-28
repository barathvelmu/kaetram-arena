#!/usr/bin/env python3
"""Offline receipt assembly and analysis for multi-action routing V2."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from canonical_start import initial_state_projection
from scripts.opd.execution_evidence import parse_tool_result_json
from scripts.opd.live_routing_launcher import (
    LOCK_COLLECTION,
    MONGO_COLLECTIONS,
    SessionSpec,
    validate_process_lifecycle,
    validate_runtime_attestation,
)
from scripts.opd.live_routing_multi_action_diagnostic import (
    ACTION_ARGUMENTS,
    ACTIONS,
    ARMS,
    canonical_sha256,
    cumulative_predicates,
    expected_observation_fixture,
    multi_action_documents,
    semantic_gameplay_projection,
)
from scripts.opd.live_routing_multi_action_launcher import (
    PHASE_SCHEMA_VERSION,
    TURN_SCHEMA_VERSION,
)


RECEIPT_SCHEMA_VERSION = "kaetram.live-routing-multi-action-trial-receipt.v2"
ANALYSIS_SCHEMA_VERSION = "kaetram.live-routing-multi-action-analysis.v2"
EVENT_ORDER = (
    "absence_confirmed", "seed_completed", "treatment_started",
    "treatment_finished", "treatment_settle_finished", "reconnect_started",
    "reconnect_finished", "reconnect_settle_finished",
    "database_snapshot_recorded", "cleanup_completed", "cleanup_absence_confirmed",
)
INSERTION_ORDER = (
    LOCK_COLLECTION,
    *(name for name in MONGO_COLLECTIONS if name != "player_info"),
    "player_info",
)


class MultiActionAnalysisError(ValueError):
    """Raw evidence is malformed, so no scientific outcome can be derived."""


def _json_equal(left: Any, right: Any) -> bool:
    try:
        return json.dumps(left, allow_nan=False, separators=(",", ":"), sort_keys=True) == json.dumps(
            right, allow_nan=False, separators=(",", ":"), sort_keys=True
        )
    except (TypeError, ValueError):
        return False


def _exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise MultiActionAnalysisError(f"{label} key set drift")
    return value


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _zero_counts(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == set(MONGO_COLLECTIONS)
        and all(type(value[name]) is int and value[name] == 0 for name in MONGO_COLLECTIONS)
    )


def _raw_projection(record: Any, *, database: bool = False) -> dict[str, Any]:
    record = _exact(
        record,
        {"available", "raw_text", "raw_sha256", "semantic_projection"},
        "semantic measurement",
    )
    raw = record["raw_text"]
    if not isinstance(raw, str) or record["raw_sha256"] != hashlib.sha256(raw.encode("utf-8")).hexdigest():
        raise MultiActionAnalysisError("semantic measurement raw digest mismatch")
    parsed = parse_tool_result_json(raw, expected_name=None if database else "observe")
    if not isinstance(parsed, dict):
        recomputed = None
    else:
        try:
            recomputed = semantic_gameplay_projection(parsed)
        except (TypeError, ValueError):
            recomputed = None
    if not _json_equal(recomputed, record["semantic_projection"]):
        raise MultiActionAnalysisError("semantic projection differs from raw evidence")
    if record["available"] is not (recomputed is not None):
        raise MultiActionAnalysisError("semantic measurement availability drift")
    return record


def database_measurement(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    raw = json.dumps(snapshot, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return {
        "available": True,
        "raw_text": raw,
        "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "semantic_projection": semantic_gameplay_projection(snapshot),
    }


def assemble_trial_receipt(
    *,
    plan: dict[str, Any],
    treatment: dict[str, Any],
    reconnect: dict[str, Any],
    database_snapshot: dict[str, Any],
    cleanup: dict[str, Any],
    seed: dict[str, Any],
    parent_event_ledger: list[dict[str, Any]],
    global_absence: dict[str, Any],
    registration_sha256: str,
) -> dict[str, Any]:
    """Assemble immutable raw evidence; no pass/fail fields are accepted."""

    documents = database_snapshot.get("documents")
    if not isinstance(documents, Mapping):
        raise MultiActionAnalysisError("database snapshot documents are missing")
    document_ids = {
        name: str(documents.get(name, {}).get("_id", ""))
        for name in MONGO_COLLECTIONS
    }
    execution_evidence = {
        "global_absence": global_absence,
        "trial_absence": seed.get("absence"),
        "seed": seed,
        "process_lifecycles": {
            "treatment": treatment.get("process_lifecycle"),
            "reconnect": reconnect.get("process_lifecycle"),
        },
        "parent_event_ledger": parent_event_ledger,
        "candidate_call_ledger": treatment.get("candidate_call_ledger"),
        "database_snapshot_ownership": {
            "database": database_snapshot.get("database"),
            "username": database_snapshot.get("username"),
            "document_ids": document_ids,
        },
        "cleanup": cleanup,
    }
    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "registration_sha256": registration_sha256,
        "plan": plan,
        "treatment": treatment,
        "reconnect": reconnect,
        "database": database_measurement(database_snapshot),
        "cleanup": cleanup,
        "execution_evidence": execution_evidence,
    }
    receipt["payload_sha256"] = canonical_sha256(receipt)
    return receipt


def _attestation_and_lifecycle_valid(phase: Mapping[str, Any], spec: SessionSpec) -> bool:
    evidence = phase.get("runtime_attestation")
    lifecycle = phase.get("process_lifecycle")
    if not isinstance(evidence, dict) or set(evidence) != {"raw_text", "raw_sha256", "parsed"}:
        return False
    raw = evidence.get("raw_text")
    if not isinstance(raw, str) or evidence.get("raw_sha256") != hashlib.sha256(raw.encode()).hexdigest():
        return False
    parsed = parse_tool_result_json(raw, expected_name="__diagnostic_runtime_attestation")
    if not _json_equal(parsed, evidence.get("parsed")):
        return False
    try:
        validate_runtime_attestation(parsed, spec)
        validate_process_lifecycle(lifecycle, spec, parsed)
    except (TypeError, ValueError, RuntimeError):
        return False
    return True


def _baseline_semantic() -> dict[str, Any]:
    return semantic_gameplay_projection({"documents": multi_action_documents("fixture")})


def classify_trial(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Derive validity and application outcomes without conflating the two."""

    expected_receipt_keys = {
        "schema_version", "registration_sha256", "plan", "treatment", "reconnect",
        "database", "cleanup", "execution_evidence", "payload_sha256",
    }
    if set(receipt) != expected_receipt_keys:
        raise MultiActionAnalysisError("trial receipt key set drift")
    if receipt.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        raise MultiActionAnalysisError("trial receipt schema drift")
    unsigned = {key: value for key, value in receipt.items() if key != "payload_sha256"}
    if receipt.get("payload_sha256") != canonical_sha256(unsigned):
        raise MultiActionAnalysisError("trial receipt self-hash mismatch")
    if not _is_sha256(receipt.get("registration_sha256")):
        raise MultiActionAnalysisError("registration digest is malformed")
    plan = receipt.get("plan")
    if not isinstance(plan, dict):
        raise MultiActionAnalysisError("trial plan is missing")
    arm = plan.get("arm")
    order = plan.get("action_order")
    if arm not in ARMS or not isinstance(order, list) or sorted(order) != sorted(ACTIONS) or len(order) != 3:
        raise MultiActionAnalysisError("trial plan arm/action order drift")
    treatment = receipt.get("treatment")
    reconnect = receipt.get("reconnect")
    if not isinstance(treatment, dict) or not isinstance(reconnect, dict):
        raise MultiActionAnalysisError("session phase evidence is missing")
    if treatment.get("schema_version") != PHASE_SCHEMA_VERSION or reconnect.get("schema_version") != PHASE_SCHEMA_VERSION:
        raise MultiActionAnalysisError("session phase schema drift")
    if treatment.get("phase") != "treatment" or reconnect.get("phase") != "reconnect":
        raise MultiActionAnalysisError("session phase identity drift")
    identity_fields = ("trial_id", "username", "arm", "action_order")
    if any(not _json_equal(treatment.get(key), plan.get(key)) for key in identity_fields):
        raise MultiActionAnalysisError("treatment differs from plan")
    if any(not _json_equal(reconnect.get(key), plan.get(key)) for key in identity_fields):
        raise MultiActionAnalysisError("reconnect differs from plan")
    invalid: list[str] = []
    treatment_spec = SessionSpec(
        trial_id=plan["trial_id"], session_id=plan["treatment_session_id"],
        phase="treatment", username=plan["username"], arm=arm,
    )
    reconnect_spec = SessionSpec(
        trial_id=plan["trial_id"], session_id=plan["reconnect_session_id"],
        phase="reconnect", username=plan["username"], arm=arm,
    )
    if treatment.get("session_id") != treatment_spec.session_id or reconnect.get("session_id") != reconnect_spec.session_id:
        invalid.append("session_identity_mismatch")
    treatment_lifecycle_valid = _attestation_and_lifecycle_valid(treatment, treatment_spec)
    reconnect_lifecycle_valid = _attestation_and_lifecycle_valid(reconnect, reconnect_spec)
    if not treatment_lifecycle_valid or not reconnect_lifecycle_valid:
        invalid.append("cold_session_lifecycle_unconfirmed")
    if treatment_lifecycle_valid and reconnect_lifecycle_valid:
        treatment_identity = treatment["runtime_attestation"]["parsed"]
        reconnect_identity = reconnect["runtime_attestation"]["parsed"]
        distinct_fields = (
            "mcp_pid", "mcp_process_group", "mcp_instance_nonce",
            "browser_pid", "browser_process_group", "browser_launch_nonce",
        )
        if any(
            treatment_identity.get(field) == reconnect_identity.get(field)
            for field in distinct_fields
        ):
            invalid.append("cold_session_identity_reused")
    evidence = receipt.get("execution_evidence")
    evidence = _exact(
        evidence,
        {
            "global_absence", "trial_absence", "seed", "process_lifecycles", "parent_event_ledger",
            "candidate_call_ledger", "database_snapshot_ownership", "cleanup",
        },
        "execution evidence",
    )
    if not _json_equal(evidence["process_lifecycles"], {
        "treatment": treatment.get("process_lifecycle"),
        "reconnect": reconnect.get("process_lifecycle"),
    }):
        raise MultiActionAnalysisError("process lifecycle evidence differs from phases")
    if not _json_equal(evidence["cleanup"], receipt.get("cleanup")):
        raise MultiActionAnalysisError("cleanup evidence differs from receipt")
    seed = evidence.get("seed")
    if not isinstance(seed, Mapping):
        raise MultiActionAnalysisError("seed evidence is missing")
    expected_seed_keys = {
        "database", "username", "trial_id", "fixture_schema_version", "absence",
        "inserted_ids", "insertion_order", "player_info_inserted_last",
    }
    if set(seed) != expected_seed_keys:
        raise MultiActionAnalysisError("seed evidence key set drift")
    global_absence = evidence.get("global_absence")
    absence = evidence.get("trial_absence")
    inserted_ids = seed.get("inserted_ids")
    absence_counts = absence.get("counts") if isinstance(absence, Mapping) else None
    if not (
        _json_equal(absence, seed.get("absence"))
        and isinstance(absence, Mapping)
        and absence.get("database") == "kaetram_e2e"
        and absence.get("all_absent") is True
        and isinstance(absence_counts, Mapping)
        and _zero_counts(absence_counts.get(plan["username"]))
    ):
        invalid.append("global_or_trial_absence_unconfirmed")
    global_counts = (
        global_absence.get("counts") if isinstance(global_absence, Mapping) else None
    )
    if not (
        isinstance(global_absence, Mapping)
        and global_absence.get("database") == "kaetram_e2e"
        and global_absence.get("all_absent") is True
        and isinstance(global_counts, Mapping)
        and plan["username"] in global_counts
        and all(_zero_counts(value) for value in global_counts.values())
    ):
        invalid.append("global_or_trial_absence_unconfirmed")
    if not (
        seed.get("database") == "kaetram_e2e"
        and seed.get("username") == plan["username"]
        and seed.get("trial_id") == plan["trial_id"]
        and seed.get("fixture_schema_version") == "kaetram.multi-action-fixture.v2"
        and isinstance(inserted_ids, Mapping)
        and set(inserted_ids) == set(INSERTION_ORDER)
        and all(isinstance(value, str) and value for value in inserted_ids.values())
        and len(set(inserted_ids.values())) == len(inserted_ids)
        and seed.get("insertion_order") == list(INSERTION_ORDER)
        and seed.get("player_info_inserted_last") is True
    ):
        invalid.append("create_only_seed_unconfirmed")
    ledger = evidence.get("parent_event_ledger")
    event_times: dict[str, float] = {}
    if isinstance(ledger, list):
        for row in ledger:
            if (
                not isinstance(row, Mapping)
                or set(row) != {"event", "monotonic_seconds"}
                or not isinstance(row.get("event"), str)
                or type(row.get("monotonic_seconds")) not in (int, float)
            ):
                event_times = {}
                break
            event_times[row["event"]] = float(row["monotonic_seconds"])
    if not (
        isinstance(ledger, list)
        and [row.get("event") for row in ledger if isinstance(row, Mapping)] == list(EVENT_ORDER)
        and len(event_times) == len(EVENT_ORDER)
        and all(event_times[EVENT_ORDER[index]] < event_times[EVENT_ORDER[index + 1]] for index in range(len(EVENT_ORDER) - 1))
        and event_times["treatment_settle_finished"] - event_times["treatment_finished"] >= 1.5
        and event_times["reconnect_settle_finished"] - event_times["reconnect_finished"] >= 1.5
    ):
        invalid.append("parent_event_order_or_settle_unconfirmed")
    precondition = treatment.get("precondition")
    if not isinstance(precondition, dict) or set(precondition) != {
        "available", "raw_text", "raw_sha256", "normalized_projection"
    }:
        raise MultiActionAnalysisError("precondition evidence key set drift")
    raw = precondition.get("raw_text")
    if not isinstance(raw, str) or precondition.get("raw_sha256") != hashlib.sha256(raw.encode()).hexdigest():
        raise MultiActionAnalysisError("precondition raw digest mismatch")
    parsed = parse_tool_result_json(raw, expected_name="observe")
    try:
        normalized = initial_state_projection(parsed) if isinstance(parsed, dict) else None
    except (TypeError, ValueError):
        normalized = None
    if not _json_equal(normalized, precondition.get("normalized_projection")):
        raise MultiActionAnalysisError("precondition projection differs from raw evidence")
    if precondition.get("available") is not True or not _json_equal(normalized, expected_observation_fixture()):
        invalid.append("precondition_missing_or_mismatch")
    turns = treatment.get("turns")
    if not isinstance(turns, list) or len(turns) != 3:
        raise MultiActionAnalysisError("exactly three turn receipts are required")
    completed: list[str] = []
    action_passes = {name: True for name in ACTIONS}
    failures: list[str] = []
    expected_status = {
        "structured_direct": "not_applicable_structured",
        "content_recovery_on": "promoted",
        "content_recovery_off": "disabled_not_evaluated",
    }[arm]
    expected_dispatch = arm != "content_recovery_off"
    call_ledger = evidence.get("candidate_call_ledger")
    if not isinstance(call_ledger, list):
        raise MultiActionAnalysisError("candidate call ledger is missing")
    expected_call_count = 3 if expected_dispatch else 0
    if len(call_ledger) != expected_call_count:
        failures.append("unexpected_candidate_invocation_count")
    for sequence, row in enumerate(call_ledger, start=1):
        if not isinstance(row, Mapping) or set(row) != {
            "sequence", "name", "arguments", "delivery_status",
            "protocol_success", "result_raw_sha256",
        }:
            raise MultiActionAnalysisError("candidate call ledger row drift")
        if (
            sequence > 3
            or row.get("sequence") != sequence
            or row.get("name") != order[sequence - 1]
            or not _json_equal(row.get("arguments"), ACTION_ARGUMENTS[order[sequence - 1]])
        ):
            raise MultiActionAnalysisError("candidate call ledger contains an unfrozen call")
    for sequence, (turn, action_name) in enumerate(zip(turns, order, strict=True), start=1):
        if not isinstance(turn, dict) or turn.get("schema_version") != TURN_SCHEMA_VERSION:
            raise MultiActionAnalysisError("turn receipt schema drift")
        if turn.get("sequence") != sequence or turn.get("action") != action_name:
            raise MultiActionAnalysisError("turn sequence differs from registered order")
        if turn.get("router_status") != expected_status:
            failures.append(f"turn_{sequence}_unexpected_router_status")
        if turn.get("dispatch_attempted") is not expected_dispatch:
            failures.append(f"turn_{sequence}_unexpected_dispatch")
        if expected_dispatch and sequence <= len(call_ledger):
            ledger_row = call_ledger[sequence - 1]
            for ledger_key, turn_key in (
                ("delivery_status", "delivery_status"),
                ("protocol_success", "protocol_success"),
                ("result_raw_sha256", "result_raw_sha256"),
            ):
                if not _json_equal(ledger_row.get(ledger_key), turn.get(turn_key)):
                    raise MultiActionAnalysisError("turn routing differs from candidate ledger")
        if expected_dispatch:
            if turn.get("schema_status") != "valid":
                failures.append(f"turn_{sequence}_schema_invalid")
            if turn.get("delivery_status") != "confirmed" or turn.get("protocol_success") is not True:
                failures.append(f"turn_{sequence}_protocol_failure")
            if turn.get("delivery_status") == "unknown_after_exception":
                invalid.append("delivery_unknown_after_exception")
            result_raw = turn.get("result_raw_text")
            if isinstance(result_raw, str):
                if turn.get("result_raw_sha256") != hashlib.sha256(result_raw.encode()).hexdigest():
                    raise MultiActionAnalysisError("confirmed result raw digest mismatch")
                reparsed = parse_tool_result_json(result_raw, expected_name=action_name)
                if not _json_equal(reparsed, turn.get("result_json")):
                    raise MultiActionAnalysisError("turn result differs from raw evidence")
            elif turn.get("delivery_status") == "confirmed":
                raise MultiActionAnalysisError("confirmed result raw evidence is missing")
            if turn.get("tool_reported_error") is not None:
                failures.append(f"turn_{sequence}_tool_reported_error")
            completed.append(action_name)
        else:
            if turn.get("schema_status") != "not_applicable_no_candidate" or any(
                turn.get(key) is not None
                for key in ("protocol_success", "result_json", "result_raw_text", "result_raw_sha256", "tool_reported_error")
            ):
                failures.append(f"turn_{sequence}_off_arm_routing_leak")
        immediate = _raw_projection(turn.get("immediate"))
        delayed = _raw_projection(turn.get("delayed"))
        stages_available = (
            immediate.get("available") is True
            and delayed.get("available") is True
            and isinstance(immediate.get("semantic_projection"), dict)
            and isinstance(delayed.get("semantic_projection"), dict)
        )
        if not stages_available:
            invalid.append("semantic_measurement_missing_or_unparseable")
        elapsed = turn.get("delayed_elapsed_monotonic_seconds")
        if type(elapsed) not in (int, float) or elapsed < 1.5:
            invalid.append("delayed_observation_interval_unconfirmed")
        if expected_dispatch and stages_available:
            for stage_name, stage in (("immediate", immediate), ("delayed", delayed)):
                predicates = cumulative_predicates(stage["semantic_projection"], completed)
                for completed_name, passed in predicates.items():
                    action_passes[completed_name] &= passed
                    if not passed:
                        failures.append(
                            f"turn_{sequence}_{stage_name}_{completed_name}_predicate_failed"
                        )
        elif not expected_dispatch and stages_available:
            baseline = _baseline_semantic()
            if not _json_equal(immediate["semantic_projection"], baseline):
                failures.append(f"turn_{sequence}_immediate_off_baseline_failed")
            if not _json_equal(delayed["semantic_projection"], baseline):
                failures.append(f"turn_{sequence}_delayed_off_baseline_failed")
    reconnect_measurement = _raw_projection(reconnect.get("reconnect"))
    database = _raw_projection(receipt.get("database"), database=True)
    database_raw = parse_tool_result_json(
        receipt["database"].get("raw_text"), expected_name=None
    )
    ownership = evidence.get("database_snapshot_ownership")
    ownership = _exact(
        ownership, {"database", "username", "document_ids"}, "snapshot ownership"
    )
    documents = database_raw.get("documents") if isinstance(database_raw, Mapping) else None
    ownership_ids = ownership.get("document_ids")
    snapshot_owned = (
        isinstance(database_raw, Mapping)
        and database_raw.get("database") == "kaetram_e2e"
        and database_raw.get("username") == plan["username"]
        and ownership.get("database") == "kaetram_e2e"
        and ownership.get("username") == plan["username"]
        and isinstance(documents, Mapping)
        and set(documents) == set(MONGO_COLLECTIONS)
        and isinstance(ownership_ids, Mapping)
        and set(ownership_ids) == set(MONGO_COLLECTIONS)
        and isinstance(inserted_ids, Mapping)
        and all(
            isinstance(documents.get(name), Mapping)
            and str(documents[name].get("_id")) == ownership_ids.get(name)
            and documents[name].get("username") == plan["username"]
            and inserted_ids.get(name) == ownership_ids.get(name)
            for name in MONGO_COLLECTIONS
        )
    )
    if not snapshot_owned:
        invalid.append("database_snapshot_ownership_unconfirmed")
    final_available = (
        reconnect_measurement.get("available") is True
        and database.get("available") is True
        and isinstance(reconnect_measurement.get("semantic_projection"), dict)
        and isinstance(database.get("semantic_projection"), dict)
    )
    if not final_available:
        invalid.append("semantic_measurement_missing_or_unparseable")
    final_projection_rows = (
        ("reconnect", reconnect_measurement["semantic_projection"]),
        ("database", database["semantic_projection"]),
    )
    if expected_dispatch and final_available:
        for stage_name, projection in final_projection_rows:
            predicates = cumulative_predicates(projection, order)
            for action_name, passed in predicates.items():
                action_passes[action_name] &= passed
                if not passed:
                    failures.append(f"{stage_name}_{action_name}_predicate_failed")
    elif not expected_dispatch and final_available:
        baseline = _baseline_semantic()
        for stage_name, projection in final_projection_rows:
            if not _json_equal(projection, baseline):
                failures.append(f"{stage_name}_off_baseline_failed")
    cleanup = receipt.get("cleanup")
    cleanup_absent = (
        cleanup.get("all_absent")
        if isinstance(cleanup, dict)
        else None
    )
    if cleanup_absent is None and isinstance(cleanup, dict):
        absence = cleanup.get("absence")
        cleanup_absent = absence.get("all_absent") if isinstance(absence, dict) else None
    cleanup_counts = (
        cleanup.get("absence", {}).get("counts", {}).get(plan["username"])
        if isinstance(cleanup, dict)
        else None
    )
    cleanup_valid = (
        isinstance(cleanup, dict)
        and set(cleanup) == {"database", "deleted", "lock_deleted", "absence", "complete"}
        and cleanup.get("database") == "kaetram_e2e"
        and isinstance(cleanup.get("deleted"), Mapping)
        and set(cleanup["deleted"]) == set(MONGO_COLLECTIONS)
        and all(cleanup["deleted"].get(name) == 1 for name in MONGO_COLLECTIONS)
        and cleanup.get("lock_deleted") == 1
        and cleanup.get("complete") is True
        and cleanup_absent is True
        and _zero_counts(cleanup_counts)
    )
    if not cleanup_valid:
        invalid.append("ownership_cleanup_unconfirmed")
    invalid = list(dict.fromkeys(invalid))
    failures = list(dict.fromkeys(failures))
    if invalid:
        return {
            "schema_version": ANALYSIS_SCHEMA_VERSION,
            "trial_id": plan["trial_id"],
            "arm": arm,
            "validity": "invalid",
            "outcome": "not_assessable",
            "invalid_reasons": invalid,
            "failure_reasons": [],
            "action_predicates": {name: None for name in ACTIONS},
        }
    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "trial_id": plan["trial_id"],
        "arm": arm,
        "validity": "valid",
        "outcome": "pass" if not failures else "fail",
        "invalid_reasons": [],
        "failure_reasons": failures,
        "action_predicates": (
            action_passes if expected_dispatch else {name: None for name in ACTIONS}
        ),
    }


def analyze_run(receipts: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate the exact nine technical trials without inferential claims."""

    if len(receipts) != 9:
        raise MultiActionAnalysisError("exactly nine trial receipts are required")
    analyses = [classify_trial(receipt) for receipt in receipts]
    identities = [row["trial_id"] for row in analyses]
    if len(set(identities)) != 9:
        raise MultiActionAnalysisError("trial identities are not unique")
    plans = [receipt.get("plan") for receipt in receipts]
    if not all(isinstance(plan, Mapping) for plan in plans):
        raise MultiActionAnalysisError("aggregate trial plan is missing")
    for field in ("username", "treatment_session_id", "reconnect_session_id"):
        values = [plan.get(field) for plan in plans]
        if len(set(values)) != 9:
            raise MultiActionAnalysisError(f"aggregate {field} identities are not unique")
    expected_usernames = {plan["username"] for plan in plans}
    global_records = [
        receipt["execution_evidence"].get("global_absence") for receipt in receipts
    ]
    if not all(_json_equal(record, global_records[0]) for record in global_records):
        raise MultiActionAnalysisError("global absence evidence differs across trials")
    global_counts = global_records[0].get("counts") if isinstance(global_records[0], Mapping) else None
    if not (
        isinstance(global_counts, Mapping)
        and set(global_counts) == expected_usernames
        and all(_zero_counts(value) for value in global_counts.values())
    ):
        raise MultiActionAnalysisError("global absence evidence does not cover the exact plan")
    owned_identifiers: list[str] = []
    for receipt in receipts:
        inserted = receipt["execution_evidence"]["seed"].get("inserted_ids")
        if not isinstance(inserted, Mapping):
            raise MultiActionAnalysisError("aggregate inserted identities are missing")
        owned_identifiers.extend(str(value) for value in inserted.values())
    if len(set(owned_identifiers)) != len(owned_identifiers):
        raise MultiActionAnalysisError("owned database identities were reused across trials")
    runtime_identities: list[tuple[Any, ...]] = []
    for receipt in receipts:
        for phase_name in ("treatment", "reconnect"):
            phase = receipt.get(phase_name)
            evidence = phase.get("runtime_attestation") if isinstance(phase, Mapping) else None
            parsed = evidence.get("parsed") if isinstance(evidence, Mapping) else None
            if not isinstance(parsed, Mapping):
                raise MultiActionAnalysisError("aggregate runtime identity is missing")
            runtime_identities.append(
                (
                    parsed.get("mcp_pid"),
                    parsed.get("mcp_process_group"),
                    parsed.get("mcp_instance_nonce"),
                    parsed.get("browser_pid"),
                    parsed.get("browser_process_group"),
                    parsed.get("browser_launch_nonce"),
                )
            )
    for field_index in range(6):
        values = [identity[field_index] for identity in runtime_identities]
        if len(set(values)) != 18:
            raise MultiActionAnalysisError("cold session identities reused across trials")
    arm_rows: dict[str, dict[str, Any]] = {}
    for arm in ARMS:
        rows = [row for row in analyses if row["arm"] == arm]
        if len(rows) != 3:
            raise MultiActionAnalysisError("each arm must contain exactly three trials")
        arm_rows[arm] = {
            "technical_trials": 3,
            "protocol_valid": sum(row["validity"] == "valid" for row in rows),
            "full_predicate_pass": sum(row["outcome"] == "pass" for row in rows),
            "behavioral_fail": sum(row["outcome"] == "fail" for row in rows),
            "invalid": sum(row["validity"] == "invalid" for row in rows),
            "action_predicate_pass": {
                action: sum(row["action_predicates"].get(action) is True for row in rows)
                for action in ACTIONS
            },
        }
    invalid = sum(row["validity"] == "invalid" for row in analyses)
    failures = sum(row["outcome"] == "fail" for row in analyses)
    verdict = (
        "incomplete_with_invalid_trials"
        if invalid
        else "complete_with_failures"
        if failures
        else "complete"
    )
    result: dict[str, Any] = {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "verdict": verdict,
        "technical_trials": 9,
        "technical_repeats": 3,
        "technical_repeats_are_independent": False,
        "protocol_valid": 9 - invalid,
        "full_predicate_pass": sum(row["outcome"] == "pass" for row in analyses),
        "behavioral_fail": failures,
        "invalid": invalid,
        "arms": arm_rows,
        "trials": analyses,
        "wording_guard": (
            "Report protocol-valid and full-predicate-pass counts separately; "
            "never abbreviate protocol-valid trials as passes."
        ),
    }
    result["payload_sha256"] = canonical_sha256(result)
    return result
