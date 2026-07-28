from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.build_tmlr_supplement import (
    add_live_routing_projection,
    add_multi_action_review_summary,
    audit_review_tree,
)
from scripts.opd import live_routing_review_projection as projection
from scripts.opd.live_routing_result_verify import VerificationError


def _fixture() -> dict:
    return {
        "pos": {"x": 10, "y": 10},
        "equipment": [],
        "quests": [
            {
                "key": "kept",
                "stage": 2,
                "sub_stage": 0,
                "completed_sub_stages": [],
            }
        ],
        "achievements": [],
        "skills": [],
        "statistics": {},
    }


def _default_expansion() -> dict:
    return {
        "pos": {"x": 10, "y": 10},
        "equipment": [{"key": "", "count": -1, "type": 0}],
        "quests": [
            {
                "key": "default",
                "stage": 0,
                "sub_stage": 0,
                "completed_sub_stages": [],
            },
            {
                "key": "kept",
                "stage": 2,
                "sub_stage": 0,
                "completed_sub_stages": [],
            },
        ],
        "achievements": [{"key": "default", "stage": 0}],
        "skills": [{"type": 0, "experience": 0}],
        "statistics": {
            "averageTimePlayed": 1.0,
            "cheater": False,
            "creationTime": 10,
            "drops": {},
            "lastLogin": 11,
            "loginCount": 2,
            "mobExamines": [],
            "mobKills": {},
            "pvpDeaths": 0,
            "pvpKills": 0,
            "resources": {},
            "totalTimePlayed": 1.0,
        },
    }


def _source() -> tuple[dict, list[dict], dict]:
    fixture = _fixture()
    target = copy.deepcopy(fixture)
    target["pos"] = {"x": 150, "y": 250}
    analysis = {"trials": []}
    receipts = []
    schedule = [
        (1, "structured_direct"),
        (1, "content_recovery_on"),
        (1, "content_recovery_off"),
        (2, "content_recovery_on"),
        (2, "content_recovery_off"),
        (2, "structured_direct"),
        (3, "content_recovery_off"),
        (3, "structured_direct"),
        (3, "content_recovery_on"),
    ]
    for index, (repeat, arm) in enumerate(schedule, 1):
        trial_id = f"private-id-{index}"
        active = arm != "content_recovery_off"
        analysis["trials"].append(
            {
                "trial_id": trial_id,
                "repeat": repeat,
                "arm": arm,
                "validity": "valid",
                "outcome": "pass" if active else "fail",
            }
        )
        client = target if active else fixture
        receipts.append(
            {
                "plan": {"trial_id": trial_id, "schedule_index": index},
                "routing": {
                    "candidate_invocation_count": 1 if active else 0,
                    "delivery_status": "confirmed" if active else "not_attempted",
                },
                "measurements": {
                    "immediate": {"normalized_projection": copy.deepcopy(client)},
                    "delayed": {"normalized_projection": copy.deepcopy(client)},
                    "reconnect": {"normalized_projection": copy.deepcopy(client)},
                    "database": {
                        "normalized_projection": (
                            copy.deepcopy(target)
                            if active
                            else _default_expansion()
                        )
                    },
                },
            }
        )
    registration = {
        "state_fixture": {
            "expected": fixture,
            "database_expected": fixture,
        },
        "measurement": {
            "mudwich_success_region": {
                "x_min": 100,
                "x_max": 200,
                "y_min": 200,
                "y_max": 300,
            }
        },
    }
    return analysis, receipts, registration


def _complete_projection() -> dict:
    analysis, receipts, registration = _source()
    trials = projection._project_rows(analysis, receipts, registration)
    result = {
        "schema_version": projection.PROJECTION_SCHEMA,
        "scope": projection.SCOPE,
        "trials": trials,
        "summary": projection._summarize(trials),
    }
    result["projection_sha256"] = projection._projection_sha256(result)
    return result


def _resign(value: dict) -> None:
    value["projection_sha256"] = projection._projection_sha256(value)


def test_projection_rekeys_and_retains_only_narrow_completed_result() -> None:
    result = _complete_projection()
    projection.validate_review_projection(result)

    assert [row["trial_label"] for row in result["trials"]] == [
        f"trial-{index:02d}" for index in range(1, 10)
    ]
    assert [(row["repeat"], row["arm"]) for row in result["trials"]] == [
        *projection.COMPLETED_SCHEDULE
    ]
    summary = result["summary"]
    assert summary["valid_trials"] == 9
    assert summary["arms"]["structured_direct"]["registered_passes"] == 3
    assert summary["arms"]["content_recovery_on"]["registered_passes"] == 3
    off = summary["arms"]["content_recovery_off"]
    assert off["candidate_not_invoked"] == 3
    assert off["client_baseline_preserved"] == 3
    assert off["strict_database_baseline_preserved"] == 0
    assert off["database_defaults_and_session_bookkeeping_materialized"] == 3
    serialized = json.dumps(result, sort_keys=True)
    for forbidden in (
        "private-id",
        "run_id",
        "trial_id",
        "sha256:",
        "username",
        "endpoint",
        "process_group",
    ):
        assert forbidden not in serialized


def test_projection_is_deterministic_across_private_schedule_order() -> None:
    analysis, receipts, registration = _source()
    original = projection._project_rows(analysis, receipts, registration)
    pairs = list(zip(analysis["trials"], receipts, strict=True))
    pairs.reverse()
    analysis["trials"] = [pair[0] for pair in pairs]
    receipts = [pair[1] for pair in pairs]
    reordered = projection._project_rows(analysis, receipts, registration)
    assert reordered == original


def test_validator_rejects_resigned_headline_drift() -> None:
    result = _complete_projection()
    result["trials"][0]["database_target_reached"] = False
    _resign(result)
    with pytest.raises(projection.ProjectionError, match="claim boundary"):
        projection.validate_review_projection(result)


def test_validator_rejects_identity_field_even_when_resigned() -> None:
    result = _complete_projection()
    result["trials"][0]["username"] = "someone"
    _resign(result)
    with pytest.raises(projection.ProjectionError, match="key set"):
        projection.validate_review_projection(result)


def test_canonical_file_round_trip_and_no_overwrite(tmp_path: Path) -> None:
    result = _complete_projection()
    path = tmp_path / "projection.json"
    projection.write_review_projection(path, result)
    loaded, _ = projection.load_review_projection(path)
    assert loaded == result
    with pytest.raises(projection.ProjectionError, match="overwrite"):
        projection.write_review_projection(path, result)


def test_noncanonical_file_is_rejected(tmp_path: Path) -> None:
    result = _complete_projection()
    path = tmp_path / "projection.json"
    path.write_text(json.dumps(result))
    with pytest.raises(projection.ProjectionError, match="canonical rendered"):
        projection.load_review_projection(path)


def test_supplement_copy_accepts_only_valid_anonymous_projection(
    tmp_path: Path,
) -> None:
    source = tmp_path / "projection.json"
    projection.write_review_projection(source, _complete_projection())

    stage = tmp_path / "stage"
    destination = add_live_routing_projection(stage, source)

    assert destination == stage / "results" / "local-routing-diagnostic-review.json"
    assert destination.read_bytes() == source.read_bytes()
    audit_review_tree(stage)


def test_multi_action_review_summary_preserves_v2_failure_and_fresh_v3(
    tmp_path: Path,
) -> None:
    destination = add_multi_action_review_summary(tmp_path)
    value = json.loads(destination.read_text())

    assert value["v2"]["registered_outcome"]["protocol_valid"] == 9
    assert value["v2"]["registered_outcome"]["full_predicate_pass"] == 0
    assert value["v3"]["outcome"]["protocol_valid"] == 9
    assert value["v3"]["outcome"]["full_predicate_pass"] == 9
    assert value["v3"]["measurement_history"]["v2_relabelled"] is False
    assert "evidence_binding" not in destination.read_text()
    audit_review_tree(tmp_path)


def test_supplement_copy_rejects_resigned_identity_field(tmp_path: Path) -> None:
    value = _complete_projection()
    value["username"] = "private-user"
    _resign(value)
    source = tmp_path / "projection.json"
    source.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

    with pytest.raises(projection.ProjectionError, match="key set"):
        add_live_routing_projection(tmp_path / "stage", source)


def test_builder_fails_closed_before_reading_private_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail(*args, **kwargs):
        raise VerificationError("broken package")

    monkeypatch.setattr(projection, "verify_package_or_raise", fail)
    with pytest.raises(projection.ProjectionError, match="verification failed"):
        projection.build_review_projection(
            tmp_path / "package",
            tmp_path / "registration.json",
            repo_root=tmp_path,
            expected_head="0" * 40,
        )


def test_parity_check_rejects_valid_but_different_projection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    expected = _complete_projection()
    altered = copy.deepcopy(expected)
    altered["projection_sha256"] = "0" * 64
    # Keep the file structurally valid, but make the expected private result differ.
    alternate_expected = copy.deepcopy(expected)
    alternate_expected["projection_sha256"] = "1" * 64
    path = tmp_path / "projection.json"
    projection.write_review_projection(path, expected)
    monkeypatch.setattr(projection, "build_review_projection", lambda *a, **k: alternate_expected)
    with pytest.raises(projection.ProjectionError, match="differs from verified"):
        projection.verify_review_projection_against_package(
            path,
            tmp_path,
            tmp_path / "registration.json",
            repo_root=tmp_path,
            expected_head="0" * 40,
        )


def test_default_expansion_classifier_rejects_nondefault_change() -> None:
    actual = _default_expansion()
    actual["skills"][0]["experience"] = 1
    assert not projection._database_defaults_and_session_bookkeeping_materialized_only(
        actual, _fixture()
    )


def test_materialization_classifier_rejects_unregistered_statistic() -> None:
    actual = _default_expansion()
    actual["statistics"]["questProgress"] = 1
    assert not projection._database_defaults_and_session_bookkeeping_materialized_only(
        actual, _fixture()
    )
