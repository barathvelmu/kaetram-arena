from __future__ import annotations

from pathlib import Path

import pytest

from scripts.opd.analyze_local_recovery_factorial import (
    AnalysisError,
    _pair_differences,
    _validate_recovery_receipts,
    _validate_recovery_accounting,
)


def _audit(recovered: dict[str, int], malformed: int = 1) -> dict:
    count = sum(recovered.values())
    return {
        "schema_version": "kaetram-recovery-audit-v1",
        "totals": {
            "malformed_emissions": malformed,
            "recovered_calls": count,
            "recovered_execution_errors": 0,
            "repeat_recoveries_within_window": 0,
        },
        "recovered_by_tool": recovered,
    }


def test_recovery_on_accounts_raw_plus_recovered_calls(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.opd.analyze_local_recovery_factorial.audit_logs",
        lambda _: _audit({"observe": 2}),
    )
    result = _validate_recovery_accounting(
        [Path("unused")],
        {
            "raw_action_counts": {"warp": 1},
            "raw_malformed_emissions": 1,
            "raw_recoverable_calls": 2,
            "raw_recoverable_action_counts": {"observe": 2},
        },
        {"warp": 1, "observe": 2},
        True,
    )
    assert result["recovered_calls"] == 2
    assert result["malformed_emissions"] == 1


def test_recovery_off_rejects_any_recovered_call(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.opd.analyze_local_recovery_factorial.audit_logs",
        lambda _: _audit({"observe": 1}),
    )
    with pytest.raises(AnalysisError, match="recovery-off"):
        _validate_recovery_accounting(
            [Path("unused")],
            {
                "raw_action_counts": {},
                "raw_malformed_emissions": 1,
                "raw_recoverable_calls": 1,
                "raw_recoverable_action_counts": {"observe": 1},
            },
            {"observe": 1},
            False,
        )


def test_recovery_accounting_rejects_unexplained_canonical_call(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.opd.analyze_local_recovery_factorial.audit_logs",
        lambda _: _audit({}),
    )
    with pytest.raises(AnalysisError, match="canonical executions differ"):
        _validate_recovery_accounting(
            [Path("unused")],
            {
                "raw_action_counts": {"warp": 1},
                "raw_malformed_emissions": 0,
                "raw_recoverable_calls": 0,
                "raw_recoverable_action_counts": {},
            },
            {"warp": 1, "observe": 1},
            True,
        )


def test_analysis_requires_one_recovery_receipt_per_session(tmp_path: Path) -> None:
    raw = tmp_path / "episode_001_raw"
    raw.mkdir()
    (raw / "harness_meta_template.json").write_text(
        '{"tool_recovery_enabled":true}'
    )
    (raw / "session_1_test.log").write_text("{}\n")
    with pytest.raises(AnalysisError, match="recovery receipts"):
        _validate_recovery_receipts(
            tmp_path,
            {"meta": {"tool_recovery_enabled": True}},
            True,
            "cell",
        )


def test_pair_differences_require_and_preserve_all_nine_pairs() -> None:
    rows = []
    metrics = (
        "canonical_executed_calls",
        "canonical_executed_calls_per_minute",
        "raw_structured_calls",
        "malformed_emissions",
        "recovered_calls",
        "core3_stages_advanced",
        "quest_stages_advanced",
        "xp_db_delta",
        "unique_positions",
    )
    for replicate in (1, 2, 3):
        for weight in ("base", "r2", "r3"):
            for recovery in (False, True):
                schedule_index = len(rows)
                row = {
                    "replicate": replicate,
                    "weight": weight,
                    "recovery": recovery,
                    "schedule_index": schedule_index,
                }
                row.update({metric: int(recovery) for metric in metrics})
                rows.append(row)
    pairs = _pair_differences(rows)
    assert len(pairs["complete_pairs"]) == 9
    assert all(
        set(pair["on_minus_off"].values()) == {1}
        for pair in pairs["complete_pairs"]
    )
