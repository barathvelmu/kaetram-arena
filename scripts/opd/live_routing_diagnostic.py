#!/usr/bin/env python3
"""Validate the frozen zero-cost live routing diagnostic registration.

Live execution is intentionally not hidden in this validator.  A result-bearing
launcher must first provide cold-session isolation and complete persistence
receipts; until then this script freezes and checks the design without starting
MongoDB, a game server, a browser, a model, or a remote endpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from canonical_start import CANONICAL_INITIAL_STATE  # noqa: E402
from scripts.opd.response_router import route_content_tool_call  # noqa: E402
from tool_surface import (  # noqa: E402
    MODEL_VISIBLE_TOOL_EFFECT_CLASSES,
    MODEL_VISIBLE_TOOL_SCHEMA_SHA256,
    validate_tool_call_arguments,
)


SCHEMA_VERSION = "kaetram.live-routing-diagnostic-registration.v1"
STUDY_ID = "local-live-routing-diagnostic-v1"
STATUS = "design_scaffolding_not_live_ready"
PERMITTED_CLAIM = (
    "Preliminary exact-route behavior for one frozen local warp fixture on the "
    "registered build; active-route operability and recovery-off non-routing "
    "are reported separately."
)
ARMS = (
    "structured_direct",
    "content_recovery_on",
    "content_recovery_off",
)
SCHEDULE = (
    ARMS,
    ("content_recovery_on", "content_recovery_off", "structured_direct"),
    ("content_recovery_off", "structured_direct", "content_recovery_on"),
)
PROHIBITED_CLAIMS = [
    "model quality or superiority",
    "causal recovery benefit",
    "quest-performance improvement",
    "checkpoint or training superiority",
    "faithful execution of archived V2 states",
    "generalization across tools, states, models, renderers, or environments",
]
DESIGN_SOURCE_PATHS = (
    "canonical_start.py",
    "mcp_server/js/observe.js",
    "mcp_server/core.py",
    "mcp_server/login.py",
    "mcp_server/tools/navigation.py",
    "mcp_server/tools/test_lane.py",
    "play_qwen.py",
    "scripts/opd/execution_evidence.py",
    "scripts/opd/live_routing_analyzer.py",
    "scripts/opd/live_routing_diagnostic.py",
    "scripts/opd/live_routing_prelaunch.py",
    "scripts/opd/live_routing_result_verify.py",
    "scripts/opd/response_router.py",
    "state_extractor.js",
    "tests/e2e/helpers/mcp_client.py",
    "tests/e2e/helpers/seed.py",
    "tool_surface.py",
)
LIVE_READY_ADDITIONAL_SOURCE_PATHS = (
    "scripts/opd/live_routing_launcher.py",
)
SOURCE_PATHS = DESIGN_SOURCE_PATHS
MEASUREMENT_STAGES = [
    "router decision",
    "frozen-schema verdict",
    "client dispatch attempt",
    "candidate delivery confirmation",
    "MCP protocol result",
    "tool-reported application error",
    "frozen warp application predicate",
    "immediate normalized observation",
    "delayed normalized observation",
    "post-reconnect observation",
    "read-only database projection after close",
]


class RegistrationError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RegistrationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise RegistrationError(f"non-finite JSON constant: {value}")


def load_registration_strict(path: Path) -> dict[str, Any]:
    try:
        registration = json.loads(
            path.read_bytes(),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise RegistrationError(f"registration unreadable: {exc}") from exc
    if not isinstance(registration, dict):
        raise RegistrationError("registration root must be an object")
    return registration


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _json_equal(left: Any, right: Any) -> bool:
    """Compare JSON values without Python's bool/int equality ambiguity."""

    return _canonical_json(left) == _canonical_json(right)


def _has_exact_keys(value: Any, expected: set[str]) -> bool:
    return isinstance(value, dict) and set(value) == expected


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_trial_identities(
    arms: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    arm_map = {arm.get("arm"): arm for arm in arms if isinstance(arm, dict)}
    trials: list[dict[str, Any]] = []
    schedule_index = 0
    for repeat, order in enumerate(SCHEDULE, start=1):
        for position, arm_name in enumerate(order, start=1):
            schedule_index += 1
            arm = arm_map.get(arm_name, {})
            trials.append(
                {
                    "schedule_index": schedule_index,
                    "repeat": repeat,
                    "position_within_repeat": position,
                    "pair_id": f"repeat-{repeat:02d}",
                    "arm": arm_name,
                    "trial_key": f"llrd-v1-t{schedule_index:02d}",
                    "username_template": f"lr_{{run_id}}_{schedule_index:02d}",
                    "treatment_session_id_template": (
                        f"llrd-{{run_id}}-t{schedule_index:02d}-treatment"
                    ),
                    "reconnect_session_id_template": (
                        f"llrd-{{run_id}}-t{schedule_index:02d}-reconnect"
                    ),
                    "route": arm.get("route"),
                    "recovery": arm.get("recovery"),
                    "expected_candidate_invocations": arm.get(
                        "expected_candidate_invocations"
                    ),
                }
            )
    return trials


def validate_registration(
    registration: dict[str, Any],
    *,
    repo_root: Path | None = None,
    expected_status: str = STATUS,
) -> list[str]:
    """Return every design/source mismatch; this does not authorize a live run."""

    errors: list[str] = []
    expected_root_keys = {
        "schema_version",
        "study_id",
        "status",
        "claim_boundary",
        "zero_cost_contract",
        "source_contract",
        "live_contract",
        "state_fixture",
        "candidate",
        "arms",
        "schedule",
        "trial_identities",
        "measurement",
        "runtime_parameters",
        "invalidity_reasons",
        "failure_policy",
        "reporting",
        "verdict_algorithm",
    }
    if set(registration) != expected_root_keys:
        errors.append("registration top-level key set drift")
    if registration.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version drift")
    if registration.get("study_id") != STUDY_ID:
        errors.append("study_id drift")
    if registration.get("status") != expected_status:
        errors.append(f"registration status drift: expected {expected_status}")

    boundary = _object(registration.get("claim_boundary"))
    if not _has_exact_keys(
        boundary, {"confirmatory", "permitted_claim", "prohibited_claims"}
    ):
        errors.append("claim boundary key set drift")
    if boundary.get("confirmatory") is not False:
        errors.append("diagnostic must remain explicitly non-confirmatory")
    if boundary.get("permitted_claim") != PERMITTED_CLAIM:
        errors.append("permitted claim boundary drift")
    if boundary.get("prohibited_claims") != PROHIBITED_CLAIMS:
        errors.append("prohibited claim boundary drift")

    zero_cost = _object(registration.get("zero_cost_contract"))
    expected_zero_cost = {
        "model_calls": 0,
        "remote_endpoints": "forbidden",
        "metered_services": "forbidden",
        "network_scope": "loopback_only",
        "game_port": 9191,
        "mongo_port": 27017,
        "mongo_database": "kaetram_e2e",
    }
    if not _json_equal(zero_cost, expected_zero_cost):
        errors.append("zero-cost or isolated-lane contract drift")

    candidate = _object(registration.get("candidate"))
    if not _has_exact_keys(
        candidate,
        {
            "name",
            "arguments",
            "canonical_json",
            "sha256",
            "content_envelope",
            "content_envelope_sha256",
        },
    ):
        errors.append("candidate key set drift")
    call = {"name": candidate.get("name"), "arguments": candidate.get("arguments")}
    canonical = _canonical_json(call)
    if candidate.get("canonical_json") != canonical:
        errors.append("candidate canonical JSON mismatch")
    if candidate.get("sha256") != _sha256_text(canonical):
        errors.append("candidate digest mismatch")
    valid, reason = validate_tool_call_arguments(call["name"], call["arguments"])
    if (valid, reason) != (True, "valid"):
        errors.append(f"candidate is not valid under frozen schema: {reason}")
    if MODEL_VISIBLE_TOOL_EFFECT_CLASSES.get(call["name"]) != "potentially_mutating":
        errors.append("candidate is not registered as potentially mutating")
    content = candidate.get("content_envelope")
    if not isinstance(content, str):
        errors.append("content envelope is missing")
    else:
        if candidate.get("content_envelope_sha256") != _sha256_text(content):
            errors.append("content envelope digest mismatch")
        decision = route_content_tool_call(content)
        expected_decision = {
            "status": "promoted",
            "calls": [{"name": call["name"], "args": call["arguments"]}],
            "reason": "valid",
        }
        if decision != expected_decision:
            errors.append(f"strict router no longer promotes frozen candidate: {decision}")

    expected_arms = [
        {
            "arm": "structured_direct",
            "route": "structured",
            "recovery": False,
            "expected_candidate_invocations": 1,
            "required_application_predicate": (
                "measurement.warp_application_acceptance"
            ),
            "expected_stage_outcomes": {
                "router_status": "not_applicable_structured",
                "schema_status": "valid",
                "dispatch_attempted": True,
                "delivery_status": "confirmed",
                "protocol_success": True,
                "tool_reported_error": None,
                "state_predicate": "mudwich_immediate_delayed_reconnect",
            },
        },
        {
            "arm": "content_recovery_on",
            "route": "ordinary_content",
            "recovery": True,
            "required_router_status": "promoted",
            "expected_candidate_invocations": 1,
            "required_application_predicate": (
                "measurement.warp_application_acceptance"
            ),
            "expected_stage_outcomes": {
                "router_status": "promoted",
                "schema_status": "valid",
                "dispatch_attempted": True,
                "delivery_status": "confirmed",
                "protocol_success": True,
                "tool_reported_error": None,
                "state_predicate": "mudwich_immediate_delayed_reconnect",
            },
        },
        {
            "arm": "content_recovery_off",
            "route": "ordinary_content",
            "recovery": False,
            "expected_candidate_invocations": 0,
            "required_state_predicate": (
                "registered_baseline_at_immediate_delayed_reconnect_and_database"
            ),
            "expected_stage_outcomes": {
                "router_status": "disabled_not_evaluated",
                "schema_status": "not_applicable_no_candidate",
                "dispatch_attempted": False,
                "delivery_status": "not_attempted",
                "protocol_success": None,
                "tool_reported_error": None,
                "state_predicate": "baseline_immediate_delayed_reconnect_database",
            },
        },
    ]
    if not _json_equal(registration.get("arms"), expected_arms):
        errors.append("arm semantics drift")

    schedule = registration.get("schedule")
    expected_schedule = [
        {"repeat": index, "arm_order": list(order)}
        for index, order in enumerate(SCHEDULE, start=1)
    ]
    if not _json_equal(schedule, expected_schedule):
        errors.append("balanced schedule drift")
    identities = registration.get("trial_identities")
    expected_identities = expected_trial_identities(expected_arms)
    if not _json_equal(identities, expected_identities):
        errors.append("trial identity plan drift")
    elif any(
        len({row[key] for row in identities}) != 9
        for key in (
            "trial_key",
            "username_template",
            "treatment_session_id_template",
            "reconnect_session_id_template",
        )
    ):
        errors.append("trial identity uniqueness drift")
    elif len(
        {
            row[key]
            for row in identities
            for key in (
                "treatment_session_id_template",
                "reconnect_session_id_template",
            )
        }
    ) != 18:
        errors.append("treatment/reconnect session identity overlap")

    fixture = _object(registration.get("state_fixture"))
    if not _has_exact_keys(fixture, {"source", "expected", "precondition"}):
        errors.append("state fixture key set drift")
    if fixture.get("source") != "canonical_start.CANONICAL_INITIAL_STATE":
        errors.append("canonical state fixture source drift")
    if not _json_equal(fixture.get("expected"), CANONICAL_INITIAL_STATE):
        errors.append("canonical state fixture drift")
    if fixture.get("precondition") != (
        "Every arm must match the complete registered projection before "
        "treatment or the candidate is not invoked."
    ):
        errors.append("canonical precondition drift")
    live = _object(registration.get("live_contract"))
    if not _has_exact_keys(
        live,
        {
            "game_revision",
            "game_bundle_sha256",
            "tool_schema_sha256",
            "cold_mcp_session_per_trial",
            "cold_browser_session_per_trial",
            "fresh_unique_player_per_trial",
            "unique_username_count",
            "mongo_database_explicit_every_operation",
            "runtime_receipts_required",
        },
    ):
        errors.append("live contract key set drift")
    if live.get("game_revision") != "7a3d722e8e200ca44fd959099386b42a5fbe0cb5":
        errors.append("game revision drift")
    if live.get("game_bundle_sha256") != (
        "b0f9e42b0da63dc7bb1f9172136cd8a1361f762e683b72011172db286c256916"
    ):
        errors.append("game bundle digest drift")
    if live.get("tool_schema_sha256") != MODEL_VISIBLE_TOOL_SCHEMA_SHA256:
        errors.append("frozen tool-schema digest drift")
    for gate in (
        "cold_mcp_session_per_trial",
        "cold_browser_session_per_trial",
        "fresh_unique_player_per_trial",
        "runtime_receipts_required",
    ):
        if live.get(gate) is not True:
            errors.append(f"required live isolation gate disabled: {gate}")
    if live.get("unique_username_count") != 9:
        errors.append("exactly nine unique trial usernames are required")
    if live.get("mongo_database_explicit_every_operation") != "kaetram_e2e":
        errors.append("every database operation must explicitly bind the e2e lane")

    measurement = _object(registration.get("measurement"))
    if not _has_exact_keys(
        measurement,
        {
            "candidate_retry_count",
            "delayed_observation_seconds",
            "mudwich_success_region",
            "warp_application_acceptance",
            "stages",
        },
    ):
        errors.append("measurement key set drift")
    if not _json_equal(measurement.get("candidate_retry_count"), 0):
        errors.append("candidate retry count must remain zero")
    if not _json_equal(measurement.get("delayed_observation_seconds"), 5):
        errors.append("delayed observation interval drift")
    if not _json_equal(measurement.get("mudwich_success_region"), {
        "x_min": 180,
        "x_max": 200,
        "y_min": 150,
        "y_max": 170,
    }):
        errors.append("Mudwich success predicate drift")
    if not _json_equal(measurement.get("warp_application_acceptance"), {
        "protocol_success": True,
        "tool_reported_error": None,
        "result_json_required": {"warping": True, "warp_id": 0},
    }):
        errors.append("warp application-acceptance predicate drift")
    if measurement.get("stages") != MEASUREMENT_STAGES:
        errors.append("measurement stages drift")
    expected_runtime_parameters = {
        "service_readiness_timeout_seconds": 60,
        "login_timeout_seconds": 60,
        "mcp_call_timeout_seconds": 120,
        "minimum_delayed_observation_seconds": 5,
        "minimum_disconnect_settle_seconds": 1.5,
        "candidate_retry_count": 0,
        "cold_mcp_process_per_trial": True,
        "cold_browser_profile_per_trial": True,
    }
    if not _json_equal(
        registration.get("runtime_parameters"), expected_runtime_parameters
    ):
        errors.append("runtime parameter contract drift")
    expected_invalidity_reasons = [
        "identity_mismatch_or_reuse",
        "username_absence_unconfirmed",
        "create_only_seed_unconfirmed",
        "cold_session_unconfirmed",
        "wrong_database_or_runtime_lane",
        "precondition_missing_or_mismatch",
        "applicable_measurement_missing_or_unparseable",
        "delivery_unknown_after_exception",
        "unregistered_candidate_retry",
        "session_order_or_settle_violation",
        "cleanup_absence_unconfirmed",
    ]
    if not _json_equal(
        registration.get("invalidity_reasons"), expected_invalidity_reasons
    ):
        errors.append("invalidity reason taxonomy drift")
    if not _json_equal(registration.get("verdict_algorithm"), {
        "valid_trial": (
            "trial identity, isolation, and precondition match; every applicable "
            "stage has a complete receipt; delivery is not unknown; router, "
            "schema, protocol, application, invocation-count, and state-predicate "
            "deviations remain valid failure outcomes"
        ),
        "active_arm_pass": (
            "exactly one candidate result; protocol_success true; no "
            "tool_reported_error; warp result predicate true; immediate, delayed, "
            "and reconnect positions satisfy Mudwich region"
        ),
        "off_arm_pass": (
            "candidate delivery_status not_attempted; immediate, delayed, "
            "reconnect, and database projections equal the registered baseline"
        ),
        "repeat_pass": "all three arms valid and pass",
        "full_grid_release": (
            "release the full descriptive grid whenever all nine trials are "
            "valid, regardless of pass, fail, or mixed outcomes; if any trial "
            "is invalid, release every trial receipt but withhold only the "
            "paired aggregate"
        ),
    }):
        errors.append("verdict algorithm drift")
    expected_failure = {
        "outcome_based_exclusions": "forbidden",
        "invalid_precondition": "retain failure receipt and do not invoke candidate",
        "treatment_retry": "forbidden",
        "transport_exception": "delivery unknown; retain receipt and invalidate trial",
        "incomplete_pair": "retain all receipts and withhold paired verdict",
        "release_rule": (
            "Release the full-grid descriptive verdict only if all nine trials "
            "and all three repeats are valid."
        ),
    }
    if not _json_equal(registration.get("failure_policy"), expected_failure):
        errors.append("failure policy drift")
    reporting = _object(registration.get("reporting"))
    if not _has_exact_keys(
        reporting,
        {
            "scheduled_trials",
            "technical_repeats",
            "independent_sample_claim",
            "p_values",
            "confidence_intervals",
            "equivalence_language",
            "raw_and_hashed_receipts_required",
        },
    ):
        errors.append("reporting key set drift")
    if not _json_equal(reporting.get("scheduled_trials"), 9):
        errors.append("scheduled trial count drift")
    if not _json_equal(reporting.get("technical_repeats"), 3):
        errors.append("technical repeat count drift")
    if reporting.get("independent_sample_claim") is not False:
        errors.append("technical repeats cannot be called independent samples")
    for key in ("p_values", "confidence_intervals", "equivalence_language"):
        if reporting.get(key) != "forbidden":
            errors.append(f"reporting prohibition drift: {key}")
    if reporting.get("raw_and_hashed_receipts_required") is not True:
        errors.append("raw and hashed receipts must remain required")

    source = _object(registration.get("source_contract"))
    if not _has_exact_keys(
        source, {"source_commit", "clean_worktree_required", "files"}
    ):
        errors.append("source contract key set drift")
    if source.get("source_commit") != "sealed_in_create_only_prelaunch_receipt":
        errors.append("source commit prelaunch seal contract drift")
    if source.get("clean_worktree_required") is not True:
        errors.append("clean-worktree launch gate disabled")
    files = source.get("files")
    required_source_paths = set(DESIGN_SOURCE_PATHS)
    if expected_status == "registered_before_live_execution":
        required_source_paths.update(LIVE_READY_ADDITIONAL_SOURCE_PATHS)
    if not isinstance(files, dict) or set(files) != required_source_paths:
        errors.append("source file contract key set drift")
    elif repo_root is not None:
            for relative, expected in sorted(files.items()):
                path = repo_root / relative
                if not path.is_file():
                    errors.append(f"source file missing: {relative}")
                elif _sha256_file(path) != expected:
                    errors.append(f"source file digest drift: {relative}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registration",
        type=Path,
        default=REPO_ROOT / "research/experiments/local-live-routing-diagnostic-v1.json",
    )
    parser.add_argument("--verify-source", action="store_true")
    args = parser.parse_args(argv)
    try:
        registration = load_registration_strict(args.registration)
    except RegistrationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    errors = validate_registration(
        registration,
        repo_root=REPO_ROOT if args.verify_source else None,
    )
    if errors:
        print("live routing diagnostic registration INVALID", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"registration valid: {args.registration}")
    print("scheduled_trials: 9")
    print("model_calls: 0")
    print("live execution: blocked until result-bearing launcher and create-only prelaunch receipt exist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
