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
SOURCE_PATHS = (
    "canonical_start.py",
    "mcp_server/js/observe.js",
    "mcp_server/tools/navigation.py",
    "mcp_server/tools/test_lane.py",
    "play_qwen.py",
    "scripts/opd/execution_evidence.py",
    "scripts/opd/live_routing_diagnostic.py",
    "scripts/opd/response_router.py",
    "state_extractor.js",
    "tests/e2e/helpers/mcp_client.py",
    "tests/e2e/helpers/seed.py",
    "tool_surface.py",
)
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


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_registration(
    registration: dict[str, Any],
    *,
    repo_root: Path | None = None,
) -> list[str]:
    """Return every design/source mismatch; this does not authorize a live run."""

    errors: list[str] = []
    if registration.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version drift")
    if registration.get("study_id") != STUDY_ID:
        errors.append("study_id drift")
    if registration.get("status") != STATUS:
        errors.append("design scaffolding status drift")

    boundary = registration.get("claim_boundary", {})
    if boundary.get("confirmatory") is not False:
        errors.append("diagnostic must remain explicitly non-confirmatory")
    if boundary.get("permitted_claim") != PERMITTED_CLAIM:
        errors.append("permitted claim boundary drift")
    if boundary.get("prohibited_claims") != PROHIBITED_CLAIMS:
        errors.append("prohibited claim boundary drift")

    zero_cost = registration.get("zero_cost_contract", {})
    expected_zero_cost = {
        "model_calls": 0,
        "remote_endpoints": "forbidden",
        "metered_services": "forbidden",
        "network_scope": "loopback_only",
        "game_port": 9191,
        "mongo_port": 27017,
        "mongo_database": "kaetram_e2e",
    }
    if zero_cost != expected_zero_cost:
        errors.append("zero-cost or isolated-lane contract drift")

    candidate = registration.get("candidate", {})
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
        },
        {
            "arm": "content_recovery_off",
            "route": "ordinary_content",
            "recovery": False,
            "expected_candidate_invocations": 0,
            "required_state_predicate": (
                "registered_baseline_at_immediate_delayed_reconnect_and_database"
            ),
        },
    ]
    if registration.get("arms") != expected_arms:
        errors.append("arm semantics drift")

    schedule = registration.get("schedule")
    expected_schedule = [
        {"repeat": index, "arm_order": list(order)}
        for index, order in enumerate(SCHEDULE, start=1)
    ]
    if schedule != expected_schedule:
        errors.append("balanced schedule drift")

    fixture = registration.get("state_fixture", {})
    if fixture.get("source") != "canonical_start.CANONICAL_INITIAL_STATE":
        errors.append("canonical state fixture source drift")
    if fixture.get("expected") != CANONICAL_INITIAL_STATE:
        errors.append("canonical state fixture drift")
    if fixture.get("precondition") != (
        "Every arm must match the complete registered projection before "
        "treatment or the candidate is not invoked."
    ):
        errors.append("canonical precondition drift")
    live = registration.get("live_contract", {})
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

    measurement = registration.get("measurement", {})
    if measurement.get("candidate_retry_count") != 0:
        errors.append("candidate retry count must remain zero")
    if measurement.get("delayed_observation_seconds") != 5:
        errors.append("delayed observation interval drift")
    if measurement.get("mudwich_success_region") != {
        "x_min": 180,
        "x_max": 200,
        "y_min": 150,
        "y_max": 170,
    }:
        errors.append("Mudwich success predicate drift")
    if measurement.get("warp_application_acceptance") != {
        "protocol_success": True,
        "tool_reported_error": None,
        "result_json_required": {"warping": True, "warp_id": 0},
    }:
        errors.append("warp application-acceptance predicate drift")
    if measurement.get("stages") != MEASUREMENT_STAGES:
        errors.append("measurement stages drift")
    if registration.get("verdict_algorithm") != {
        "valid_trial": (
            "precondition matches; router/schema behavior matches arm; delivery "
            "is confirmed by a result for active arms or not attempted for off; "
            "all registered observation and persistence receipts are present"
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
        "full_grid_release": "all three repeats pass; otherwise release incompleteness receipts only",
    }:
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
    if registration.get("failure_policy") != expected_failure:
        errors.append("failure policy drift")
    reporting = registration.get("reporting", {})
    if reporting.get("scheduled_trials") != 9:
        errors.append("scheduled trial count drift")
    if reporting.get("technical_repeats") != 3:
        errors.append("technical repeat count drift")
    if reporting.get("independent_sample_claim") is not False:
        errors.append("technical repeats cannot be called independent samples")
    for key in ("p_values", "confidence_intervals", "equivalence_language"):
        if reporting.get(key) != "forbidden":
            errors.append(f"reporting prohibition drift: {key}")
    if reporting.get("raw_and_hashed_receipts_required") is not True:
        errors.append("raw and hashed receipts must remain required")

    source = registration.get("source_contract", {})
    if source.get("source_commit") != "sealed_in_create_only_prelaunch_receipt":
        errors.append("source commit prelaunch seal contract drift")
    if source.get("clean_worktree_required") is not True:
        errors.append("clean-worktree launch gate disabled")
    files = source.get("files")
    if not isinstance(files, dict) or set(files) != set(SOURCE_PATHS):
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
        registration = json.loads(args.registration.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"registration unreadable: {exc}", file=sys.stderr)
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
