#!/usr/bin/env python3
"""Pure offline analysis for the frozen local live-routing diagnostic.

Raw receipts never contain author-supplied verdicts.  This module derives
package integrity, trial validity, and behavioral pass/fail separately.  It
imports no browser, MCP, MongoDB, model, or network client.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any
from urllib.parse import urlsplit

from canonical_start import (
    database_state_projection,
    initial_state_projection,
)
from scripts.opd.execution_evidence import parse_tool_result_json


ANALYSIS_SCHEMA_VERSION = "kaetram.live-routing-diagnostic-analysis.v2"
TRIAL_SCHEMA_VERSION = "kaetram.live-routing-trial-receipt.v2"
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
    "precondition",
    "routing",
    "measurements",
    "execution_evidence",
    "payload_sha256",
}
IDENTITY_KEYS = {
    "username",
    "treatment_session_id",
    "reconnect_session_id",
    "database_player_id",
}
PRECONDITION_KEYS = {
    "available",
    "raw_text",
    "raw_sha256",
    "normalized_projection",
}
ROUTING_KEYS = {
    "router_status",
    "schema_status",
    "dispatch_attempted",
    "candidate_invocation_count",
    "delivery_status",
    "protocol_success",
    "tool_reported_error",
    "result_json",
    "result_raw_text",
    "result_raw_sha256",
}
MEASUREMENT_KEYS = {
    "immediate",
    "delayed",
    "reconnect",
    "database",
    "delayed_elapsed_monotonic_seconds",
}
MEASUREMENT_RECORD_KEYS = {
    "available",
    "raw_text",
    "raw_sha256",
    "normalized_projection",
}
EXECUTION_EVIDENCE_KEYS = {
    "absence",
    "seed",
    "runtime_attestations",
    "process_lifecycles",
    "parent_event_ledger",
    "candidate_call_ledger",
    "database_snapshot_ownership",
    "cleanup",
}
MONGO_COLLECTIONS = (
    "player_info",
    "player_inventory",
    "player_bank",
    "player_equipment",
    "player_quests",
    "player_achievements",
    "player_skills",
    "player_statistics",
    "player_abilities",
)
LOCK_COLLECTION = "live_routing_diagnostic_locks"
SEED_INSERTION_ORDER = (
    LOCK_COLLECTION,
    *(name for name in MONGO_COLLECTIONS if name != "player_info"),
    "player_info",
)
ABSENCE_KEYS = {"database", "username", "counts", "all_absent"}
SEED_KEYS = {
    "database",
    "username",
    "trial_id",
    "inserted_ids",
    "insertion_order",
    "player_info_inserted_last",
}
RUNTIME_ATTESTATIONS_KEYS = {"treatment", "reconnect"}
PROCESS_LIFECYCLES_KEYS = {"treatment", "reconnect"}
RAW_ATTESTATION_KEYS = {"raw_text", "raw_sha256", "parsed"}
RUNTIME_ATTESTATION_KEYS = {
    "schema_version",
    "session_id",
    "mcp_pid",
    "mcp_process_group",
    "mcp_instance_nonce",
    "browser_pid",
    "browser_process_group",
    "browser_launch_nonce",
    "browser_nonce_echo",
    "browser_name",
    "browser_version",
    "browser_executable_sha256",
    "page_url",
    "player_username",
    "configured_client_url",
    "configured_game_port",
    "require_existing_account",
    "heartbeats_disabled",
    "loopback_only",
}
PROCESS_LIFECYCLE_KEYS = {
    "schema_version",
    "session_id",
    "owner_receipts",
    "groups",
    "cleanup_order",
    "unexpected_process_groups",
    "closure_proven",
}
PROCESS_GROUP_KEYS = {
    "pid",
    "process_group",
    "identity_source",
    "found_alive",
    "sigkill_required",
    "still_alive",
}
MCP_OWNER_KEYS = {
    "schema_version",
    "session_id",
    "mcp_pid",
    "mcp_process_group",
    "mcp_instance_nonce",
}
BROWSER_OWNER_KEYS = {
    "schema_version",
    "session_id",
    "mcp_pid",
    "mcp_process_group",
    "mcp_instance_nonce",
    "browser_pid",
    "browser_process_group",
    "browser_launch_nonce",
    "browser_executable_sha256",
}
EVENT_KEYS = {"event", "monotonic_seconds"}
EVENT_ORDER = (
    "absence_confirmed",
    "seed_completed",
    "treatment_started",
    "treatment_finished",
    "treatment_settle_finished",
    "reconnect_started",
    "reconnect_finished",
    "reconnect_settle_finished",
    "database_snapshot_recorded",
    "cleanup_completed",
    "cleanup_absence_confirmed",
)
CANDIDATE_CALL_KEYS = {
    "sequence",
    "name",
    "arguments",
    "delivery_status",
    "protocol_success",
    "result_raw_sha256",
}
SNAPSHOT_OWNERSHIP_KEYS = {"database", "username", "document_ids"}
CLEANUP_KEYS = {
    "database",
    "username",
    "trial_id",
    "deleted_counts",
    "lock_deleted",
    "post_cleanup_counts",
    "all_absent",
}


class AnalysisError(ValueError):
    """The artifact envelope is malformed; no scientific analysis is safe."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


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


def _strict_raw_json(raw_text: Any, *, expected_name: str | None) -> dict[str, Any] | None:
    return parse_tool_result_json(raw_text, expected_name=expected_name)


def _validate_raw_projection(
    record: dict[str, Any],
    *,
    label: str,
    database: bool = False,
) -> None:
    raw_text = record.get("raw_text")
    raw_sha = record.get("raw_sha256")
    if raw_text is None:
        if raw_sha is not None or record.get("normalized_projection") is not None:
            raise AnalysisError(f"{label} missing raw evidence")
        return
    if not isinstance(raw_text, str) or raw_sha != hashlib.sha256(
        raw_text.encode("utf-8")
    ).hexdigest():
        raise AnalysisError(f"{label} raw evidence digest mismatch")
    raw_payload = _strict_raw_json(
        raw_text,
        expected_name=None if database else "observe",
    )
    projection = None
    if isinstance(raw_payload, dict):
        try:
            projection = (
                database_state_projection(raw_payload)
                if database
                else initial_state_projection(raw_payload)
            )
        except (TypeError, ValueError):
            projection = None
    if not json_equal(projection, record.get("normalized_projection")):
        raise AnalysisError(f"{label} projection differs from raw evidence")


def _counts_are_zero(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == set(MONGO_COLLECTIONS)
        and all(type(value[name]) is int and value[name] == 0 for name in MONGO_COLLECTIONS)
    )


def _valid_identifier_map(value: Any, *, include_lock: bool) -> bool:
    expected = set(MONGO_COLLECTIONS)
    if include_lock:
        expected.add(LOCK_COLLECTION)
    return (
        isinstance(value, dict)
        and set(value) == expected
        and all(isinstance(item, str) and item for item in value.values())
        and len(set(value.values())) == len(value)
    )


def _runtime_attestation_valid(
    record: Any,
    *,
    expected_session_id: str,
    expected_username: str,
    live_contract: dict[str, Any],
) -> bool:
    if not isinstance(record, dict) or set(record) != RAW_ATTESTATION_KEYS:
        return False
    raw_text = record.get("raw_text")
    if not isinstance(raw_text, str) or record.get("raw_sha256") != hashlib.sha256(
        raw_text.encode("utf-8")
    ).hexdigest():
        return False
    parsed = _strict_raw_json(
        raw_text, expected_name="__diagnostic_runtime_attestation"
    )
    if not json_equal(parsed, record.get("parsed")):
        return False
    if not isinstance(parsed, dict) or set(parsed) != RUNTIME_ATTESTATION_KEYS:
        return False
    try:
        page = urlsplit(parsed.get("page_url"))
        exact_page = (
            page.scheme == "http"
            and page.hostname == "127.0.0.1"
            and page.port == 9000
            and page.path in ("", "/")
            and page.username is None
            and page.password is None
            and not page.query
            and not page.fragment
        )
    except (TypeError, ValueError):
        exact_page = False
    nonce_values = (
        parsed.get("mcp_instance_nonce"),
        parsed.get("browser_launch_nonce"),
    )
    observed_username = parsed.get("player_username")
    exact_player = (
        isinstance(observed_username, str)
        and observed_username.casefold() == expected_username.casefold()
    )
    return (
        parsed.get("schema_version")
        == "kaetram.diagnostic-runtime-attestation.v1"
        and parsed.get("session_id") == expected_session_id
        and exact_player
        and type(parsed.get("mcp_pid")) is int
        and parsed["mcp_pid"] > 0
        and type(parsed.get("mcp_process_group")) is int
        and parsed["mcp_process_group"] == parsed["mcp_pid"]
        and type(parsed.get("browser_pid")) is int
        and parsed["browser_pid"] > 0
        and type(parsed.get("browser_process_group")) is int
        and parsed["browser_process_group"] == parsed["browser_pid"]
        and parsed["browser_process_group"] != parsed["mcp_process_group"]
        and all(
            isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value)
            for value in nonce_values
        )
        and len(set(nonce_values)) == 2
        and parsed.get("browser_nonce_echo") == parsed.get("browser_launch_nonce")
        and parsed.get("browser_name") == live_contract.get("browser_name")
        and parsed.get("browser_version") == live_contract.get("browser_version")
        and parsed.get("browser_executable_sha256")
        == live_contract.get("browser_executable_sha256")
        and _is_sha256(parsed.get("browser_executable_sha256"))
        and exact_page
        and parsed.get("configured_client_url") == "http://127.0.0.1:9000"
        and parsed.get("configured_game_port") == "9191"
        and parsed.get("require_existing_account") is True
        and parsed.get("heartbeats_disabled") is True
        and parsed.get("loopback_only") is True
    )


def _owner_envelope_parsed(record: Any, expected_keys: set[str]) -> dict | None:
    if not isinstance(record, dict) or set(record) != RAW_ATTESTATION_KEYS:
        return None
    raw = record.get("raw_text")
    if not isinstance(raw, str) or record.get("raw_sha256") != hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest():
        return None
    try:
        parsed = json.loads(
            raw,
            object_pairs_hook=lambda pairs: _unique_object(pairs),
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite constant: {value}")
            ),
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if (
        not isinstance(parsed, dict)
        or set(parsed) != expected_keys
        or not json_equal(parsed, record.get("parsed"))
        or raw.encode("utf-8") != canonical_json_bytes(parsed) + b"\n"
    ):
        return None
    return parsed


def _process_lifecycle_valid(
    record: Any,
    *,
    expected_session_id: str,
    runtime_attestation: dict[str, Any],
) -> bool:
    if not isinstance(record, dict) or set(record) != PROCESS_LIFECYCLE_KEYS:
        return False
    owners = record.get("owner_receipts")
    groups = record.get("groups")
    if (
        record.get("schema_version")
        != "kaetram.session-lifecycle-cleanup.v1"
        or record.get("session_id") != expected_session_id
        or record.get("cleanup_order") != ["browser", "mcp", "worker"]
        or record.get("unexpected_process_groups") != []
        or record.get("closure_proven") is not True
        or not isinstance(owners, dict)
        or set(owners) != {"mcp", "browser"}
        or not isinstance(groups, dict)
        or set(groups) != {"worker", "mcp", "browser"}
    ):
        return False
    mcp_owner = _owner_envelope_parsed(owners["mcp"], MCP_OWNER_KEYS)
    browser_owner = _owner_envelope_parsed(
        owners["browser"], BROWSER_OWNER_KEYS
    )
    if mcp_owner is None or browser_owner is None:
        return False
    if (
        mcp_owner.get("schema_version") != "kaetram.diagnostic-mcp-owner.v1"
        or browser_owner.get("schema_version")
        != "kaetram.diagnostic-browser-owner.v1"
        or mcp_owner.get("session_id") != expected_session_id
        or browser_owner.get("session_id") != expected_session_id
        or type(mcp_owner.get("mcp_pid")) is not int
        or mcp_owner["mcp_pid"] <= 0
        or mcp_owner.get("mcp_process_group") != mcp_owner["mcp_pid"]
        or not isinstance(mcp_owner.get("mcp_instance_nonce"), str)
        or re.fullmatch(r"[0-9a-f]{32}", mcp_owner["mcp_instance_nonce"])
        is None
        or type(browser_owner.get("browser_pid")) is not int
        or browser_owner["browser_pid"] <= 0
        or browser_owner.get("browser_process_group")
        != browser_owner["browser_pid"]
        or browser_owner["browser_process_group"]
        == browser_owner.get("mcp_process_group")
        or not isinstance(browser_owner.get("browser_launch_nonce"), str)
        or re.fullmatch(r"[0-9a-f]{32}", browser_owner["browser_launch_nonce"])
        is None
        or not isinstance(browser_owner.get("browser_executable_sha256"), str)
        or re.fullmatch(
            r"[0-9a-f]{64}", browser_owner["browser_executable_sha256"]
        )
        is None
        or any(
            browser_owner.get(field) != mcp_owner.get(field)
            for field in ("mcp_pid", "mcp_process_group", "mcp_instance_nonce")
        )
    ):
        return False
    expected_sources = {
        "worker": "spawned_worker",
        "mcp": "mcp_owner_receipt",
        "browser": "browser_owner_receipt",
    }
    identities: list[int] = []
    for role, row in groups.items():
        if (
            not isinstance(row, dict)
            or set(row) != PROCESS_GROUP_KEYS
            or type(row.get("pid")) is not int
            or row["pid"] <= 0
            or row.get("process_group") != row["pid"]
            or row.get("identity_source") != expected_sources[role]
            or row.get("found_alive") is not False
            or row.get("sigkill_required") is not False
            or row.get("still_alive") is not False
        ):
            return False
        identities.append(row["pid"])
    return bool(
        len(set(identities)) == 3
        and groups["mcp"]["pid"] == mcp_owner["mcp_pid"]
        and groups["browser"]["pid"] == browser_owner["browser_pid"]
        and all(
            runtime_attestation.get(field) == mcp_owner.get(field)
            and runtime_attestation.get(field) == browser_owner.get(field)
            for field in ("mcp_pid", "mcp_process_group", "mcp_instance_nonce")
        )
        and all(
            runtime_attestation.get(field) == browser_owner.get(field)
            for field in (
                "browser_pid",
                "browser_process_group",
                "browser_launch_nonce",
                "browser_executable_sha256",
            )
        )
    )


def _event_times(value: Any) -> dict[str, float] | None:
    if not isinstance(value, list) or len(value) != len(EVENT_ORDER):
        return None
    times: dict[str, float] = {}
    previous = -math.inf
    for expected, row in zip(EVENT_ORDER, value, strict=True):
        if not isinstance(row, dict) or set(row) != EVENT_KEYS:
            return None
        timestamp = row.get("monotonic_seconds")
        if (
            row.get("event") != expected
            or type(timestamp) not in (int, float)
            or not math.isfinite(timestamp)
            or timestamp <= previous
        ):
            return None
        times[expected] = float(timestamp)
        previous = timestamp
    return times


def _candidate_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, list):
        return None
    for index, row in enumerate(value, start=1):
        if not isinstance(row, dict) or set(row) != CANDIDATE_CALL_KEYS:
            return None
        digest = row.get("result_raw_sha256")
        delivery = row.get("delivery_status")
        protocol = row.get("protocol_success")
        if (
            not json_equal(row.get("sequence"), index)
            or not isinstance(row.get("name"), str)
            or not isinstance(row.get("arguments"), dict)
            or delivery not in ("confirmed", "unknown_after_exception")
            or (delivery == "confirmed" and not _is_sha256(digest))
            or (delivery == "unknown_after_exception" and digest is not None)
            or (delivery == "unknown_after_exception" and protocol is not None)
            or (delivery == "confirmed" and type(protocol) is not bool)
        ):
            return None
    if not value:
        return {
            "dispatch_attempted": False,
            "candidate_invocation_count": 0,
            "delivery_status": "not_attempted",
            "protocol_success": None,
            "result_raw_sha256": None,
        }
    final = value[-1]
    return {
        "dispatch_attempted": True,
        "candidate_invocation_count": len(value),
        "delivery_status": final["delivery_status"],
        "protocol_success": final["protocol_success"],
        "result_raw_sha256": final["result_raw_sha256"],
    }


def validate_trial_envelope(receipt: dict[str, Any]) -> None:
    _exact_object(receipt, TRIAL_KEYS, "trial receipt")
    if receipt.get("schema_version") != TRIAL_SCHEMA_VERSION:
        raise AnalysisError("trial receipt schema drift")
    for key in SHA256_KEYS:
        if not _is_sha256(receipt.get(key)):
            raise AnalysisError(f"invalid trial digest: {key}")
    _exact_object(receipt.get("observed_identity"), IDENTITY_KEYS, "observed identity")
    precondition = _exact_object(
        receipt.get("precondition"), PRECONDITION_KEYS, "precondition"
    )
    _validate_raw_projection(precondition, label="precondition")
    _exact_object(receipt.get("routing"), ROUTING_KEYS, "routing")
    measurements = _exact_object(
        receipt.get("measurements"), MEASUREMENT_KEYS, "measurements"
    )
    for stage in ("immediate", "delayed", "reconnect", "database"):
        measurement = _exact_object(
            measurements.get(stage), MEASUREMENT_RECORD_KEYS, stage
        )
        _validate_raw_projection(
            measurement,
            label=stage,
            database=stage == "database",
        )
    evidence = _exact_object(
        receipt.get("execution_evidence"),
        EXECUTION_EVIDENCE_KEYS,
        "execution evidence",
    )
    _exact_object(evidence.get("absence"), ABSENCE_KEYS, "absence evidence")
    _exact_object(evidence.get("seed"), SEED_KEYS, "seed evidence")
    runtime = _exact_object(
        evidence.get("runtime_attestations"),
        RUNTIME_ATTESTATIONS_KEYS,
        "runtime attestations",
    )
    for phase in ("treatment", "reconnect"):
        _exact_object(runtime.get(phase), RAW_ATTESTATION_KEYS, f"{phase} attestation")
    lifecycles = _exact_object(
        evidence.get("process_lifecycles"),
        PROCESS_LIFECYCLES_KEYS,
        "process lifecycles",
    )
    for phase in ("treatment", "reconnect"):
        _exact_object(
            lifecycles.get(phase),
            PROCESS_LIFECYCLE_KEYS,
            f"{phase} process lifecycle",
        )
    ledger = evidence.get("parent_event_ledger")
    if not isinstance(ledger, list):
        raise AnalysisError("parent event ledger is not a list")
    calls = evidence.get("candidate_call_ledger")
    if not isinstance(calls, list):
        raise AnalysisError("candidate call ledger is not a list")
    _exact_object(
        evidence.get("database_snapshot_ownership"),
        SNAPSHOT_OWNERSHIP_KEYS,
        "database snapshot ownership",
    )
    _exact_object(evidence.get("cleanup"), CLEANUP_KEYS, "cleanup evidence")
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
    precondition = receipt["precondition"]
    routing = receipt["routing"]
    measurements = receipt["measurements"]
    execution = receipt["execution_evidence"]

    absence = execution["absence"]
    absence_confirmed = (
        absence.get("database") == "kaetram_e2e"
        and absence.get("username") == planned_trial.get("username")
        and _counts_are_zero(absence.get("counts"))
        and absence.get("all_absent") is True
    )
    seed = execution["seed"]
    inserted_ids = seed.get("inserted_ids")
    seed_confirmed = (
        seed.get("database") == "kaetram_e2e"
        and seed.get("username") == planned_trial.get("username")
        and seed.get("trial_id") == planned_trial.get("trial_id")
        and _valid_identifier_map(inserted_ids, include_lock=True)
        and json_equal(seed.get("insertion_order"), list(SEED_INSERTION_ORDER))
        and seed.get("player_info_inserted_last") is True
    )
    runtime = execution["runtime_attestations"]
    treatment_runtime_valid = _runtime_attestation_valid(
        runtime["treatment"],
        expected_session_id=planned_trial.get("treatment_session_id"),
        expected_username=planned_trial.get("username"),
        live_contract=registration["live_contract"],
    )
    reconnect_runtime_valid = _runtime_attestation_valid(
        runtime["reconnect"],
        expected_session_id=planned_trial.get("reconnect_session_id"),
        expected_username=planned_trial.get("username"),
        live_contract=registration["live_contract"],
    )
    treatment_attestation = runtime["treatment"].get("parsed")
    reconnect_attestation = runtime["reconnect"].get("parsed")
    both_attestations = (
        treatment_runtime_valid
        and reconnect_runtime_valid
        and isinstance(treatment_attestation, dict)
        and isinstance(reconnect_attestation, dict)
    )
    process_lifecycles = execution["process_lifecycles"]
    treatment_lifecycle_valid = bool(
        both_attestations
        and _process_lifecycle_valid(
            process_lifecycles["treatment"],
            expected_session_id=planned_trial.get("treatment_session_id"),
            runtime_attestation=treatment_attestation,
        )
    )
    reconnect_lifecycle_valid = bool(
        both_attestations
        and _process_lifecycle_valid(
            process_lifecycles["reconnect"],
            expected_session_id=planned_trial.get("reconnect_session_id"),
            runtime_attestation=reconnect_attestation,
        )
    )
    cold_mcp_process = bool(
        both_attestations
        and treatment_lifecycle_valid
        and reconnect_lifecycle_valid
        and treatment_attestation["mcp_pid"] != reconnect_attestation["mcp_pid"]
        and treatment_attestation["mcp_process_group"]
        != reconnect_attestation["mcp_process_group"]
        and treatment_attestation["mcp_instance_nonce"]
        != reconnect_attestation["mcp_instance_nonce"]
    )
    cold_browser_profile = bool(
        both_attestations
        and treatment_lifecycle_valid
        and reconnect_lifecycle_valid
        and treatment_attestation["browser_pid"]
        != reconnect_attestation["browser_pid"]
        and treatment_attestation["browser_process_group"]
        != reconnect_attestation["browser_process_group"]
        and treatment_attestation["browser_launch_nonce"]
        != reconnect_attestation["browser_launch_nonce"]
    )
    event_times = _event_times(execution["parent_event_ledger"])
    minimum_settle = registration["runtime_parameters"][
        "minimum_disconnect_settle_seconds"
    ]
    treatment_settled = bool(
        event_times
        and treatment_lifecycle_valid
        and event_times["treatment_settle_finished"]
        - event_times["treatment_finished"]
        >= minimum_settle
    )
    reconnect_settled = bool(
        event_times
        and reconnect_lifecycle_valid
        and event_times["reconnect_settle_finished"]
        - event_times["reconnect_finished"]
        >= minimum_settle
    )
    call_summary = _candidate_summary(execution["candidate_call_ledger"])
    if call_summary is None:
        raise AnalysisError("candidate call ledger is malformed")
    candidate = registration["candidate"]
    if any(
        row.get("name") != candidate["name"]
        or not json_equal(row.get("arguments"), candidate["arguments"])
        for row in execution["candidate_call_ledger"]
    ):
        raise AnalysisError("candidate call ledger contains a non-frozen candidate")
    for key, value in call_summary.items():
        if not json_equal(routing.get(key), value):
            raise AnalysisError(f"routing summary differs from candidate ledger: {key}")

    database_raw = _strict_raw_json(
        measurements["database"].get("raw_text"), expected_name=None
    )
    raw_documents = (
        database_raw.get("documents", database_raw)
        if isinstance(database_raw, dict)
        else None
    )
    ownership = execution["database_snapshot_ownership"]
    ownership_ids = ownership.get("document_ids")
    snapshot_owned = (
        ownership.get("database") == "kaetram_e2e"
        and ownership.get("username") == planned_trial.get("username")
        and database_raw.get("database") == "kaetram_e2e"
        and database_raw.get("username") == planned_trial.get("username")
        and _valid_identifier_map(ownership_ids, include_lock=False)
        and isinstance(raw_documents, dict)
        and set(raw_documents) == set(MONGO_COLLECTIONS)
        and all(
            isinstance(raw_documents.get(name), dict)
            and str(raw_documents[name].get("_id")) == ownership_ids[name]
            and raw_documents[name].get("username") == planned_trial.get("username")
            and inserted_ids.get(name) == ownership_ids[name]
            for name in MONGO_COLLECTIONS
        )
    )
    cleanup = execution["cleanup"]
    deleted = cleanup.get("deleted_counts")
    post_counts = cleanup.get("post_cleanup_counts")
    cleanup_confirmed = (
        cleanup.get("database") == "kaetram_e2e"
        and cleanup.get("username") == planned_trial.get("username")
        and cleanup.get("trial_id") == planned_trial.get("trial_id")
        and isinstance(deleted, dict)
        and set(deleted) == set(MONGO_COLLECTIONS)
        and all(type(deleted[name]) is int and deleted[name] == 1 for name in MONGO_COLLECTIONS)
        and type(cleanup.get("lock_deleted")) is int
        and cleanup.get("lock_deleted") == 1
        and _counts_are_zero(post_counts)
        and cleanup.get("all_absent") is True
    )
    mongo_lane_attested = (
        planned_trial.get("mongo_database") == "kaetram_e2e"
        and absence.get("database") == "kaetram_e2e"
        and seed.get("database") == "kaetram_e2e"
        and ownership.get("database") == "kaetram_e2e"
        and cleanup.get("database") == "kaetram_e2e"
        and both_attestations
    )

    derived_isolation = {
        "username_absence_confirmed": absence_confirmed,
        "create_only_seed_confirmed": seed_confirmed and snapshot_owned,
        "cold_mcp_process": cold_mcp_process,
        "cold_browser_profile": cold_browser_profile,
        "mongo_database_every_operation": (
            "kaetram_e2e" if mongo_lane_attested else "unattested"
        ),
        "runtime_lane_attested": bool(mongo_lane_attested),
        "prior_trial_cleanup_confirmed": True,
    }

    derived_lifecycle = {
        "candidate_retry_count": max(
            0, len(execution["candidate_call_ledger"]) - 1
        ),
        "event_order_valid": event_times is not None,
        "treatment_session_closed_and_settled": treatment_settled,
        "reconnect_session_closed_and_settled": reconnect_settled,
        "cleanup_absence_confirmed": cleanup_confirmed,
    }

    if not seed_confirmed or identity.get("database_player_id") != inserted_ids.get(
        "player_info"
    ):
        _append_once(invalid, "identity_mismatch_or_reuse")

    result_raw = routing.get("result_raw_text")
    result_sha = routing.get("result_raw_sha256")
    if result_raw is None:
        if result_sha is not None or routing.get("result_json") is not None:
            raise AnalysisError("candidate result missing raw evidence")
    elif not isinstance(result_raw, str) or result_sha != hashlib.sha256(
        result_raw.encode("utf-8")
    ).hexdigest():
        raise AnalysisError("candidate result raw evidence digest mismatch")
    else:
        reparsed_result = parse_tool_result_json(
            result_raw, expected_name=registration["candidate"]["name"]
        )
        if not json_equal(reparsed_result, routing.get("result_json")):
            raise AnalysisError("candidate result JSON differs from raw evidence")
        reparsed_error = (
            reparsed_result.get("error") if isinstance(reparsed_result, dict) else None
        )
        if not json_equal(reparsed_error, routing.get("tool_reported_error")):
            raise AnalysisError("tool-reported error differs from raw evidence")

    for key in ("username", "treatment_session_id", "reconnect_session_id"):
        if identity.get(key) != planned_trial.get(key):
            _append_once(invalid, "identity_mismatch_or_reuse")
    if not isinstance(identity.get("database_player_id"), str) or not identity[
        "database_player_id"
    ]:
        _append_once(invalid, "identity_mismatch_or_reuse")
    if derived_isolation["username_absence_confirmed"] is not True:
        _append_once(invalid, "username_absence_unconfirmed")
    if derived_isolation["create_only_seed_confirmed"] is not True:
        _append_once(invalid, "create_only_seed_unconfirmed")
    if (
        derived_isolation["cold_mcp_process"] is not True
        or derived_isolation["cold_browser_profile"] is not True
    ):
        _append_once(invalid, "cold_session_unconfirmed")
    if (
        derived_isolation["mongo_database_every_operation"] != "kaetram_e2e"
        or derived_isolation["runtime_lane_attested"] is not True
    ):
        _append_once(invalid, "wrong_database_or_runtime_lane")

    fixture = registration["state_fixture"]["expected"]
    database_fixture = registration["state_fixture"]["database_expected"]
    if (
        precondition.get("available") is not True
        or not json_equal(precondition.get("normalized_projection"), fixture)
    ):
        _append_once(invalid, "precondition_missing_or_mismatch")

    for stage in ("immediate", "delayed", "reconnect", "database"):
        measurement = measurements[stage]
        raw_payload = parse_tool_result_json(
            measurement.get("raw_text"),
            expected_name="observe" if stage != "database" else None,
        )
        recomputed_projection = None
        if isinstance(raw_payload, dict):
            try:
                recomputed_projection = (
                    database_state_projection(raw_payload)
                    if stage == "database"
                    else initial_state_projection(raw_payload)
                )
            except (TypeError, ValueError):
                recomputed_projection = None
        if not json_equal(
            recomputed_projection, measurement.get("normalized_projection")
        ):
            raise AnalysisError(f"{stage} projection differs from raw evidence")
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
        derived_lifecycle["candidate_retry_count"],
        registration["runtime_parameters"]["candidate_retry_count"],
    ):
        _append_once(invalid, "unregistered_candidate_retry")
    if (
        derived_lifecycle["event_order_valid"] is not True
        or derived_lifecycle["treatment_session_closed_and_settled"] is not True
        or derived_lifecycle["reconnect_session_closed_and_settled"] is not True
    ):
        _append_once(invalid, "session_order_or_settle_violation")
    if derived_lifecycle["cleanup_absence_confirmed"] is not True:
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
            "derived_isolation": derived_isolation,
            "derived_lifecycle": derived_lifecycle,
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
        for stage in ("immediate", "delayed", "reconnect"):
            if not json_equal(
                measurements[stage]["normalized_projection"], fixture
            ):
                failures.append(f"{stage}_baseline_state_predicate_failed")
        if not json_equal(
            measurements["database"]["normalized_projection"],
            database_fixture,
        ):
            failures.append("database_baseline_state_predicate_failed")

    return {
        "trial_id": planned_trial["trial_id"],
        "trial_key": planned_trial["trial_key"],
        "repeat": planned_trial["repeat"],
        "arm": planned_trial["arm"],
        "validity": "valid",
        "outcome": "pass" if not failures else "fail",
        "invalid_reasons": [],
        "failure_reasons": failures,
        "derived_isolation": derived_isolation,
        "derived_lifecycle": derived_lifecycle,
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

    for index, receipt in enumerate(ordered_receipts):
        current_events = _event_times(
            receipt["execution_evidence"]["parent_event_ledger"]
        )
        if index == 0:
            prior_cleanup_confirmed = current_events is not None
        else:
            previous_receipt = ordered_receipts[index - 1]
            previous_events = _event_times(
                previous_receipt["execution_evidence"]["parent_event_ledger"]
            )
            prior_cleanup_confirmed = bool(
                previous_events
                and current_events
                and classifications[index - 1]["derived_lifecycle"][
                    "cleanup_absence_confirmed"
                ] is True
                and previous_events["cleanup_absence_confirmed"]
                < current_events["absence_confirmed"]
            )
        result = classifications[index]
        result["derived_isolation"][
            "prior_trial_cleanup_confirmed"
        ] = prior_cleanup_confirmed
        if not prior_cleanup_confirmed:
            result["validity"] = "invalid"
            result["outcome"] = "not_assessable"
            _append_once(
                result["invalid_reasons"], "session_order_or_settle_violation"
            )
            result["failure_reasons"] = []

    for identity_key in (
        "mcp_pid",
        "mcp_process_group",
        "mcp_instance_nonce",
        "browser_pid",
        "browser_process_group",
        "browser_launch_nonce",
    ):
        owners: dict[bytes, list[int]] = {}
        for index, receipt in enumerate(ordered_receipts):
            runtime = receipt["execution_evidence"]["runtime_attestations"]
            for phase in ("treatment", "reconnect"):
                parsed = runtime[phase].get("parsed")
                identity = parsed.get(identity_key) if isinstance(parsed, dict) else None
                if identity is not None:
                    owners.setdefault(canonical_json_bytes(identity), []).append(index)
        duplicate_indexes = {
            index
            for indexes in owners.values()
            if len(indexes) > 1
            for index in indexes
        }
        for index in duplicate_indexes:
            result = classifications[index]
            result["validity"] = "invalid"
            result["outcome"] = "not_assessable"
            _append_once(result["invalid_reasons"], "cold_session_unconfirmed")
            result["failure_reasons"] = []

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
