from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from run_manifest import sha256_json
from canonical_start import CANONICAL_INITIAL_STATE
from scripts.opd.analyze_local_weight_pilot import (
    AnalysisError,
    _api_error_count,
    _canonical_start_ok,
    _file_sha256,
    _validate_raw_emissions,
    _validate_state_boundaries,
    _verify_artifacts,
    summarize_rows,
)


def _row(weight: str, value: int) -> dict:
    return {
        "weight": weight,
        "valid_tools": value,
        "valid_tools_per_minute": value / 5,
        "turns": value,
        "tool_parse_rate": 1.0,
        "api_errors": 0,
        "raw_generations": value + 1,
        "generations_with_structured_call": value,
        "generations_without_structured_call": 1,
        "emitted_structured_calls": value,
        "budget_overrun_seconds": 10.0,
        "core3_stages_advanced": 0,
        "quest_stages_advanced": 0,
        "xp_db_delta": value,
        "unique_positions": 2,
    }


def test_descriptive_summary_preserves_all_three_cells_per_weight() -> None:
    rows = [
        _row(weight, value)
        for weight in ("base", "r2", "r3")
        for value in (1, 2, 3)
    ]
    summary = summarize_rows(rows)
    assert summary["base"]["valid_tools"] == [1, 2, 3]
    assert summary["base"]["mean_valid_tools"] == 2
    assert summary["r2"]["mean_valid_tools_per_minute"] == 0.4
    assert summary["r3"]["zero_turn_cells"] == 0
    assert all(item["api_errors"] == 0 for item in summary.values())


def test_canonical_start_validator_is_exact() -> None:
    state = {"canonical_first_observation": deepcopy(CANONICAL_INITIAL_STATE)}
    assert _canonical_start_ok(state)
    state["canonical_first_observation"]["is_dead"] = True
    assert not _canonical_start_ok(state)


def _write_inventory(root: Path, records: list[dict]) -> str:
    inventory = {
        "schema_version": "kaetram.local-weight-pilot-artifacts.v1",
        "file_count": len(records),
        "files": records,
        "tree_sha256": sha256_json(records),
    }
    path = root / "artifact-inventory.json"
    path.write_text(json.dumps(inventory, sort_keys=True))
    return _file_sha256(path)


def test_artifact_verifier_rejects_files_outside_sealed_inventory(
    tmp_path: Path,
) -> None:
    retained = tmp_path / "result.json"
    retained.write_text("{}")
    digest = _write_inventory(
        tmp_path,
        [{
            "path": retained.name,
            "size_bytes": retained.stat().st_size,
            "sha256": _file_sha256(retained),
        }],
    )
    assert _verify_artifacts(tmp_path, digest) == 1
    (tmp_path / "unsealed.txt").write_text("late mutation")
    with pytest.raises(AnalysisError, match="file set differs"):
        _verify_artifacts(tmp_path, digest)


def test_artifact_verifier_rejects_path_traversal(tmp_path: Path) -> None:
    digest = _write_inventory(
        tmp_path,
        [{"path": "../outside", "size_bytes": 0, "sha256": "0" * 64}],
    )
    with pytest.raises(AnalysisError, match="unsafe or duplicate"):
        _verify_artifacts(tmp_path, digest)


def test_raw_emission_audit_counts_no_call_generations(tmp_path: Path) -> None:
    log = tmp_path / "session_1.log"
    log.write_text(
        "\n".join([
            json.dumps({
                "type": "raw_model_emission",
                "tool_calls": [],
            }),
            json.dumps({
                "type": "raw_model_emission",
                "tool_calls": [{
                    "name": "warp",
                    "arguments": '{"location":"mudwich"}',
                }],
            }),
        ])
    )
    metrics = _validate_raw_emissions([log])
    assert metrics["raw_generations"] == 2
    assert metrics["emitted_structured_calls"] == 1
    assert metrics["generations_without_structured_call"] == 1


def test_raw_emission_audit_rejects_malformed_arguments(tmp_path: Path) -> None:
    log = tmp_path / "session_1.log"
    log.write_text(json.dumps({
        "type": "raw_model_emission",
        "tool_calls": [{"name": "warp", "arguments": "{bad"}],
    }))
    with pytest.raises(AnalysisError, match="not valid JSON"):
        _validate_raw_emissions([log])


def test_raw_emission_audit_rejects_malformed_jsonl(tmp_path: Path) -> None:
    log = tmp_path / "session_1.log"
    log.write_text("{bad\n")
    with pytest.raises(AnalysisError, match="malformed retained JSONL"):
        _validate_raw_emissions([log])


def test_api_error_audit_reads_retained_stderr(tmp_path: Path) -> None:
    stderr = tmp_path / "sandbox" / "debug" / "stderr.log"
    stderr.parent.mkdir(parents=True)
    stderr.write_text("  [2] API error: transient\ncontinued\n")
    assert _api_error_count(tmp_path) == 1


def test_state_boundary_audit_rejects_missing_db_snapshot() -> None:
    with pytest.raises(AnalysisError, match="missing DB boundary"):
        _validate_state_boundaries({}, "cell")
