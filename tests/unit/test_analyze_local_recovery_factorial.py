from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.opd.analyze_local_recovery_factorial import (
    ARM_VALUE_METRICS,
    AnalysisError,
    _pair_differences,
    _summarize,
    _validate_recovery_receipts,
    _validate_recovery_accounting,
)


def _audit(recovered: dict[str, int], malformed: int = 1) -> dict:
    count = sum(recovered.values())
    return {
        "schema_version": "kaetram-recovery-audit-v1",
        "totals": {
            "sessions": 1,
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
        lambda _: _audit({}, malformed=0),
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


def test_recovery_accounting_rejects_malformed_count_disagreement(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "scripts.opd.analyze_local_recovery_factorial.audit_logs",
        lambda _: _audit({}, malformed=2),
    )
    with pytest.raises(AnalysisError, match="malformed count differs"):
        _validate_recovery_accounting(
            [Path("unused")],
            {
                "raw_action_counts": {},
                "raw_malformed_emissions": 1,
                "raw_recoverable_calls": 0,
                "raw_recoverable_action_counts": {},
            },
            {},
            False,
        )


def test_recovery_accounting_rejects_impossible_error_and_repeat_totals(
    monkeypatch,
) -> None:
    audit = _audit({"observe": 1})
    audit["totals"]["recovered_execution_errors"] = 2
    monkeypatch.setattr(
        "scripts.opd.analyze_local_recovery_factorial.audit_logs",
        lambda _: audit,
    )
    with pytest.raises(AnalysisError, match="errors exceed"):
        _validate_recovery_accounting(
            [Path("unused")],
            {
                "raw_action_counts": {},
                "raw_malformed_emissions": 1,
                "raw_recoverable_calls": 1,
                "raw_recoverable_action_counts": {"observe": 1},
            },
            {"observe": 1},
            True,
        )

    audit["totals"]["recovered_execution_errors"] = 0
    audit["totals"]["repeat_recoveries_within_window"] = 2
    with pytest.raises(AnalysisError, match="repeat recoveries exceed"):
        _validate_recovery_accounting(
            [Path("unused")],
            {
                "raw_action_counts": {},
                "raw_malformed_emissions": 1,
                "raw_recoverable_calls": 1,
                "raw_recoverable_action_counts": {"observe": 1},
            },
            {"observe": 1},
            True,
        )


def test_recovery_accounting_reconciles_modern_raw_and_rewritten_log(
    tmp_path: Path,
) -> None:
    log = tmp_path / "session_1_test.log"
    records = [
        {
            "type": "raw_model_emission",
            "content": '<function=observe()>',
            "tool_calls": [],
        },
        {
            "type": "assistant",
            "message": {"content": [{
                "type": "tool_use",
                "id": "recovered_1_0",
                "name": "observe",
                "input": {},
            }]},
        },
        {
            "type": "user",
            "message": {"content": [{
                "type": "tool_result",
                "tool_use_id": "recovered_1_0",
                "content": "[format] corrected\n\n{\"ok\": true}",
            }]},
        },
    ]
    log.write_text("".join(json.dumps(record) + "\n" for record in records))

    result = _validate_recovery_accounting(
        [log],
        {
            "raw_action_counts": {},
            "raw_malformed_emissions": 1,
            "raw_recoverable_calls": 1,
            "raw_recoverable_action_counts": {"observe": 1},
        },
        {"observe": 1},
        True,
    )
    assert result["malformed_emissions"] == 1
    assert result["recovered_calls"] == 1
    assert result["recovered_execution_successes"] == 1


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


def test_arm_summary_retains_replicate_values_and_descriptive_means() -> None:
    rows = []
    for replicate in (3, 1, 2):
        row = {
            "cell_id": f"rep{replicate:02d}-base-rec-off",
            "replicate": replicate,
            "weight": "base",
            "recovery": False,
            "schedule_index": replicate + 10,
        }
        row.update({metric: replicate for metric in ARM_VALUE_METRICS})
        rows.append(row)

    arm = _summarize(rows)["base-recovery-off"]
    assert arm["cell_ids"] == [
        "rep01-base-rec-off",
        "rep02-base-rec-off",
        "rep03-base-rec-off",
    ]
    assert arm["replicates"] == [1, 2, 3]
    assert arm["missing_replicates"] == []
    assert arm["values"]["malformed_emissions"] == [1, 2, 3]
    assert arm["means"]["malformed_emissions"] == 2
    assert arm["values"]["raw_structured_calls_per_minute"] == [1, 2, 3]
    assert arm["means"]["raw_structured_calls_per_minute"] == 2
    assert arm["pooled_structured_call_emission_rate"] == 1
