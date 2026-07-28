"""Checks for the standalone anonymous-review verifier."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.opd.verify_trigger_incidence_review_bundle import (
    ReviewVerificationError,
    _strict_loads,
    verify_review_artifact,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = REPO_ROOT / "research" / "artifacts" / "local-trigger-incidence-v2"
TRUST_ROOT = "04a26f53ce24fa9578c0e49d55b946321347f9de2a1dd81e0739822d57978562"


def test_review_verifier_recomputes_sealed_primary_result() -> None:
    result = verify_review_artifact(ARTIFACT, TRUST_ROOT)

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


@pytest.mark.parametrize("payload", ('{"a": 1, "a": 2}', '{"a": NaN}'))
def test_review_verifier_rejects_ambiguous_json(payload: str) -> None:
    with pytest.raises(ReviewVerificationError):
        _strict_loads(payload, label="fixture")


def test_review_verifier_requires_the_recorded_trust_root() -> None:
    with pytest.raises(ReviewVerificationError, match="differs from trust root"):
        verify_review_artifact(ARTIFACT, "0" * 64)
