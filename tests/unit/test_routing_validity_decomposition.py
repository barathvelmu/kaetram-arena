"""Regression checks for the sealed post-hoc response-route decomposition."""

from __future__ import annotations

from pathlib import Path

from scripts.opd.analyze_structured_call_validity import analyze_runs


ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "research" / "artifacts" / "local-trigger-incidence-v2" / "runs"


def test_route_decomposition_matches_sealed_v2_rows() -> None:
    result = analyze_runs(
        [
            RUNS / "base_2b" / "results.jsonl",
            RUNS / "opd_r2_2b" / "results.jsonl",
            RUNS / "opd_r3_2b" / "results.jsonl",
        ]
    )
    observed = {
        (cell["snapshot"], cell["native_tool_schema"]): (
            cell.get("schema_valid_structured_rows", 0),
            cell.get("schema_invalid_structured_rows", 0),
            cell.get("schema_valid_recoverable_text_rows", 0),
            cell.get("schema_invalid_recoverable_text_rows", 0),
            cell.get("no_candidate_rows", 0),
            cell["schema_valid_any_route_rows"],
        )
        for cell in result["cells"]
    }
    assert observed == {
        ("base_2b", "absent"): (31, 34, 30, 0, 105, 61),
        ("base_2b", "present"): (59, 1, 76, 0, 64, 135),
        ("opd_r2_2b", "absent"): (49, 24, 30, 1, 96, 79),
        ("opd_r2_2b", "present"): (55, 6, 57, 0, 82, 112),
        ("opd_r3_2b", "absent"): (39, 18, 44, 3, 96, 83),
        ("opd_r3_2b", "present"): (54, 3, 67, 0, 76, 121),
    }
    for cell in result["cells"]:
        categories = (
            cell.get("schema_valid_structured_rows", 0)
            + cell.get("schema_invalid_structured_rows", 0)
            + cell.get("schema_valid_recoverable_text_rows", 0)
            + cell.get("schema_invalid_recoverable_text_rows", 0)
            + cell.get("no_candidate_rows", 0)
        )
        assert categories == cell["rows"] == 200


def test_any_route_native_schema_effect_is_positive_for_every_sample_index() -> None:
    result = analyze_runs(
        [
            RUNS / "base_2b" / "results.jsonl",
            RUNS / "opd_r2_2b" / "results.jsonl",
            RUNS / "opd_r3_2b" / "results.jsonl",
        ]
    )
    aggregate = {
        row["snapshot"]: row["effect_rate_difference"]
        for row in result["native_schema_valid_any_route_contrasts"]
    }
    assert aggregate == {
        "base_2b": 0.37000000000000005,
        "opd_r2_2b": 0.16500000000000004,
        "opd_r3_2b": 0.19,
    }
    per_seed = result["sample_index_native_schema_contrasts"]
    assert len(per_seed) == 15
    assert all(row["effect_rate_difference"] > 0 for row in per_seed)


def test_strict_recovery_replay_promotes_only_unambiguous_valid_candidates() -> None:
    result = analyze_runs(
        [
            RUNS / "base_2b" / "results.jsonl",
            RUNS / "opd_r2_2b" / "results.jsonl",
            RUNS / "opd_r3_2b" / "results.jsonl",
        ]
    )
    assert result["strict_recovery_replay"] == {
        "policy": (
            "structured precedence; exactly one explicit closed tool_call block; "
            "exactly one recovered call; frozen-schema validation"
        ),
        "strict_no_candidate_rows": 519,
        "strict_promoted_rows": 303,
        "strict_quarantine_reason__invalid_tool_call_envelope": 1,
        "strict_quarantine_reason__missing_required_argument": 4,
        "strict_quarantined_rows": 5,
        "strict_structured_preserved_rows": 373,
    }
