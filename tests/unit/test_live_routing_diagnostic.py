from __future__ import annotations

import copy
import json
from pathlib import Path

from scripts.opd.live_routing_diagnostic import REPO_ROOT, validate_registration


REGISTRATION = (
    REPO_ROOT / "research/experiments/local-live-routing-diagnostic-v1.json"
)


def _load() -> dict:
    return json.loads(REGISTRATION.read_text())


def test_frozen_registration_and_source_contract_are_valid() -> None:
    assert validate_registration(_load(), repo_root=REPO_ROOT) == []


def test_candidate_or_content_drift_fails_closed() -> None:
    registration = _load()
    registration["candidate"]["arguments"]["location"] = "aynor"
    errors = validate_registration(registration)
    assert "candidate canonical JSON mismatch" in errors
    assert any(
        error.startswith("strict router no longer promotes frozen candidate")
        for error in errors
    )


def test_arm_and_schedule_drift_fails_closed() -> None:
    registration = _load()
    registration["arms"][2]["expected_candidate_invocations"] = 1
    registration["schedule"][0]["arm_order"].reverse()
    errors = validate_registration(registration)
    assert "arm semantics drift" in errors
    assert "balanced schedule drift" in errors


def test_zero_cost_and_claim_boundaries_cannot_be_relaxed() -> None:
    registration = _load()
    registration["zero_cost_contract"]["remote_endpoints"] = "allowed"
    registration["claim_boundary"]["confirmatory"] = True
    registration["claim_boundary"]["prohibited_claims"].pop()
    errors = validate_registration(registration)
    assert "zero-cost or isolated-lane contract drift" in errors
    assert "diagnostic must remain explicitly non-confirmatory" in errors
    assert "prohibited claim boundary drift" in errors


def test_source_file_tampering_is_detected(tmp_path: Path) -> None:
    registration = _load()
    root = tmp_path / "repo"
    for relative in registration["source_contract"]["files"]:
        source = REPO_ROOT / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    (root / "tool_surface.py").write_text("tampered\n")
    errors = validate_registration(registration, repo_root=root)
    assert "source file digest drift: tool_surface.py" in errors


def test_retry_or_independence_overclaim_fails_closed() -> None:
    registration = copy.deepcopy(_load())
    registration["measurement"]["candidate_retry_count"] = 1
    registration["reporting"]["independent_sample_claim"] = True
    errors = validate_registration(registration)
    assert "candidate retry count must remain zero" in errors
    assert "technical repeats cannot be called independent samples" in errors


def test_scientific_contract_fields_are_all_fail_closed() -> None:
    registration = _load()
    registration["claim_boundary"]["permitted_claim"] = "general operability"
    registration["measurement"]["mudwich_success_region"]["x_min"] = 0
    registration["failure_policy"]["treatment_retry"] = "allowed"
    registration["reporting"]["p_values"] = "allowed"
    registration["live_contract"]["game_revision"] = "0" * 40
    registration["state_fixture"]["precondition"] = "best effort"
    errors = validate_registration(registration)
    assert "permitted claim boundary drift" in errors
    assert "Mudwich success predicate drift" in errors
    assert "failure policy drift" in errors
    assert "reporting prohibition drift: p_values" in errors
    assert "game revision drift" in errors
    assert "canonical precondition drift" in errors


def test_design_is_explicitly_not_live_ready_and_seals_validator_source() -> None:
    registration = _load()
    assert registration["status"] == "design_scaffolding_not_live_ready"
    assert "scripts/opd/live_routing_diagnostic.py" in (
        registration["source_contract"]["files"]
    )


def test_measurement_fixture_and_source_key_set_cannot_drift() -> None:
    registration = _load()
    registration["measurement"]["stages"].pop()
    registration["state_fixture"]["source"] = "another.fixture"
    del registration["source_contract"]["files"]["mcp_server/tools/navigation.py"]
    errors = validate_registration(registration)
    assert "measurement stages drift" in errors
    assert "canonical state fixture source drift" in errors
    assert "source file contract key set drift" in errors
