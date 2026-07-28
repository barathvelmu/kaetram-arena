"""Checks for the standalone anonymous-review verifier."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.build_tmlr_supplement import (
    REVIEW_SCHEMA,
    V3_ARTIFACT,
    V3_RESULTS,
    audit_review_tree,
    build_review_artifact,
)
from scripts.opd.verify_trigger_incidence_review_bundle import (
    ReviewVerificationError,
    _strict_loads,
    verify_review_artifact,
)


@pytest.fixture(scope="module")
def review_artifact(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    root = tmp_path_factory.mktemp("review-artifact") / "artifact"
    trust_root = build_review_artifact(root)
    return root, trust_root


def test_review_verifier_recomputes_sealed_primary_result(
    review_artifact: tuple[Path, str],
) -> None:
    artifact, trust_root = review_artifact
    result = verify_review_artifact(artifact, trust_root)

    assert result["scheduled_requests"] == 1200
    assert result["successful_requests"] == 1200
    assert result["failed_requests"] == 0
    assert result["recovery_opportunities"] == 308
    assert result["native_tools_effects"] == {
        "base_2b": 0.23,
        "opd_r2_2b": 0.13,
        "opd_r3_2b": 0.1,
    }
    assert result["directional_replication_passed"] is True
    assert result["minimum_unique_semantic_responses_per_group"] == 5
    assert result["groups_with_primary_outcome_heterogeneity"] == 126
    assert result["source_history_authentication"] == "deferred_until_deanonymized"
    assert result["strict_recovery_replay"]["strict_promoted_rows"] == 303
    assert result["strict_recovery_replay"]["strict_quarantined_rows"] == 5
    assert result["strict_recovery_replay"]["strict_structured_preserved_rows"] == 373


@pytest.mark.parametrize("payload", ('{"a": 1, "a": 2}', '{"a": NaN}'))
def test_review_verifier_rejects_ambiguous_json(payload: str) -> None:
    with pytest.raises(ReviewVerificationError):
        _strict_loads(payload, label="fixture")


def test_review_verifier_requires_the_recorded_trust_root(
    review_artifact: tuple[Path, str],
) -> None:
    artifact, _ = review_artifact
    with pytest.raises(ReviewVerificationError, match="differs from trust root"):
        verify_review_artifact(artifact, "0" * 64)


def test_review_projection_has_no_direct_source_coordinates(
    review_artifact: tuple[Path, str],
) -> None:
    artifact, _ = review_artifact
    audit_review_tree(artifact)
    index = (artifact / "artifact-index.json").read_text()
    assert REVIEW_SCHEMA in index
    assert "source_git_commit" not in index
    assert "run_20260608_185339" not in "".join(
        path.read_text(errors="replace")
        for path in artifact.rglob("*")
        if path.is_file()
    )


def test_review_projection_audit_rejects_bare_git_revision(tmp_path: Path) -> None:
    leak = tmp_path / "leak.json"
    leak.write_text('{"source_git_commit":"af81627c76bfe9a9febe1864fff43e03dd82e170"}')
    with pytest.raises(SystemExit, match="40-hex"):
        audit_review_tree(tmp_path)


def test_v3_review_projection_recomputes_reported_posthoc_result(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact-v3"
    trust_root = build_review_artifact(
        artifact,
        artifact=V3_ARTIFACT,
        results=V3_RESULTS,
        registration_relative=Path("design/effective-registration.json"),
    )
    result = verify_review_artifact(artifact, trust_root)

    assert result["native_tools_effects"] == {
        "base_2b": 0.13,
        "opd_r2_2b": 0.055,
        "opd_r3_2b": 0.09,
    }
    replay = result["strict_recovery_replay"]
    assert replay["strict_no_candidate_rows"] == 312
    assert replay["strict_promoted_rows"] == 352
    assert replay["strict_quarantined_rows"] == 41
    assert replay["strict_structured_preserved_rows"] == 495
