from pathlib import Path

from scripts.verify_public_paper_evidence import verify_public_evidence


REPO = Path(__file__).resolve().parents[2]


def test_checked_in_paper_evidence_reproduces():
    result = verify_public_evidence(REPO)

    assert result["july_score_replay"]["observation_count"] == 21_524
    v1 = result["trigger_incidence_v1"]
    assert v1["scheduled_requests"] == 1_200
    assert v1["successful_requests"] == 1_200
    assert v1["failed_requests"] == 0
    assert v1["independent_cell_count"] == 12
    assert v1["independent_contrast_count"] == 9
    assert v1["state_condition_groups"] == 240
    assert (
        v1["groups_with_identical_semantic_responses"] == 240
    )
    v2 = result["trigger_incidence_v2"]
    assert v2["scheduled_requests"] == 1_200
    assert v2["successful_requests"] == 1_200
    assert v2["failed_requests"] == 0
    assert v2["directional_replication_passed"] is True
    assert v2["groups_with_multiple_semantic_responses"] == 240
    assert v2["groups_with_primary_outcome_heterogeneity"] == 126
    assert v2["native_tools_effects"] == {
        "base_2b": 0.23,
        "opd_r2_2b": 0.13,
        "opd_r3_2b": 0.1,
    }
