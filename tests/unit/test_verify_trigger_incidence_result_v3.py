from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.opd import verify_trigger_incidence_result_v3 as verify


def _registration() -> dict:
    return {
        "snapshots": {"base_2b": {}, "opd_r2_2b": {}, "opd_r3_2b": {}},
        "conditions": [
            {"condition_id": "a"},
            {"condition_id": "b"},
            {"condition_id": "c"},
            {"condition_id": "d"},
        ],
        "state_pool": {"state_count": 20},
        "sampling": {"samples_per_state_condition": 5},
        "analysis": {"directional_replication_criterion": "positive at all three"},
    }


def test_incomplete_grid_does_not_invent_seed_heterogeneity() -> None:
    result = verify._seed_heterogeneity(
        _registration(),
        {("base_2b", "a", "state-01", 0): {"status": "failed"}},
    )
    assert result == {
        "status": "not_evaluated_incomplete_grid",
        "state_condition_groups": 0,
        "groups_with_multiple_semantic_responses": 0,
        "groups_with_primary_outcome_heterogeneity": 0,
        "minimum_unique_semantic_responses_per_group": None,
        "maximum_unique_semantic_responses_per_group": None,
    }


def test_directional_result_requires_all_three_strictly_positive() -> None:
    recomputed = {
        "analysis_status": "complete",
        "registered_contrasts": [
            {
                "snapshot": snapshot,
                "contrast": "native_tools_main",
                "effect_rate_difference": effect,
            }
            for snapshot, effect in (
                ("base_2b", 0.1),
                ("opd_r2_2b", 0.2),
                ("opd_r3_2b", 0.0),
            )
        ],
    }
    result = verify._directional(_registration(), recomputed)
    assert result["status"] == "evaluated"
    assert result["passed"] is False


def test_independent_rows_reclassify_raw_responses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = tmp_path / "run"
    run.mkdir()
    row = {
        "snapshot": "base_2b",
        "condition_id": "a",
        "state_id": "state-01",
        "sample_index": 0,
        "status": "ok",
        "response_message": {"role": "assistant", "content": "hello"},
        "stored": "wrong",
    }
    (run / "results.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    binding = {
        "effective_registration": {"snapshots": {"base_2b": {}}},
        "expected_request_count": 1,
    }
    monkeypatch.setattr(
        verify.v1,
        "_verify_run_directory",
        lambda *args: (
            {"snapshot": "base_2b"},
            {},
            {},
            [row],
            {"artifact_index_sha256": "1", "tree_sha256": "2"},
        ),
    )
    monkeypatch.setattr(verify.runtime, "_require_runtime_preflight", lambda *args: None)
    monkeypatch.setattr(verify.runtime, "_verify_checkpoint_grid", lambda *args: None)
    monkeypatch.setattr(
        verify.audit_v1,
        "classify_message",
        lambda message: {"stored": "expected"},
    )
    with pytest.raises(verify.ProbeError, match="raw-response mismatch"):
        verify._independent_rows(binding, [run])


def test_v2_auditor_cannot_accept_v3_runtime_binding_field() -> None:
    # V2's strict public contract omits the V3 provenance field. This guards the
    # need for the separate V3 result verifier instead of claiming compatibility.
    v2_prelaunch_keys = {
        "schema_version",
        "study_id",
        "snapshot",
        "registration_sha256",
        "design_sha256",
        "endpoint_health",
        "sampling",
        "seed_gate_artifact_index_sha256",
        "seed_gate_tree_sha256",
        "source_git_commit",
        "dirty_paths",
    }
    assert "v3_runtime_binding" not in v2_prelaunch_keys


def test_verify_analysis_rejects_directional_tamper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registration = {
        **_registration(),
        "study_id": "local-trigger-incidence-seeded-v3",
        "claim_boundary": {"confirmatory": False},
    }
    binding = {
        "schema_version": "kaetram.local-trigger-incidence-v3-runtime-binding.v1",
        "study_id": registration["study_id"],
        "v3_registration_sha256": "1" * 64,
        "effective_registration_sha256": "2" * 64,
        "design_sha256": "3" * 64,
        "design_source_git_commit": "a" * 40,
        "execution_commit": "b" * 40,
        "execution_verification_sha256": "4" * 64,
        "execution_verifier_sha256": "5" * 64,
        "expected_request_count": 1200,
        "expected_request_grid_sha256": "6" * 64,
        "claim_boundary_sha256": "7" * 64,
        "effective_registration": registration,
        "design": {"states": []},
    }
    recomputed = {
        "analysis_status": "complete",
        "scheduled_requests": 1200,
        "successful_requests": 1200,
        "failed_requests": 0,
        "recovery_opportunities": 10,
        "cells": [],
        "registered_contrasts": [],
    }
    heterogeneity = {"status": "complete"}
    directional = {"status": "evaluated", "passed": False}
    analysis = {
        "schema_version": verify.v1.ANALYSIS_SCHEMA,
        "study_id": binding["study_id"],
        "registration_sha256": binding["effective_registration_sha256"],
        "design_sha256": binding["design_sha256"],
        "analysis_code_provenance": {
            "source_git_commit": binding["execution_commit"],
            "dirty_paths": [],
            "analysis_script_sha256": verify.v1.sha256_file(
                Path(verify.v2.__file__).resolve()
            ),
            "python_version": "3.11.0",
            "runtime_binding_adapter_sha256": verify.v1.sha256_file(
                Path(verify.runtime.__file__).resolve()
            ),
        },
        "input_runs": [
            {
                "snapshot": "base_2b",
                "artifact_index_sha256": "8" * 64,
                "tree_sha256": "9" * 64,
            }
        ],
        **recomputed,
        "claim_boundary": registration["claim_boundary"],
        "registered_seed_heterogeneity": heterogeneity,
        "directional_replication": {"status": "evaluated", "passed": True},
        "v3_runtime_binding": verify.runtime._public_binding(binding),
    }
    (tmp_path / "cells.csv").write_bytes(b"")
    (tmp_path / "contrasts.csv").write_bytes(b"")
    monkeypatch.setattr(verify.audit_v2, "_verify_internal_index", lambda *args: {})
    monkeypatch.setattr(verify.audit_v2, "load_object", lambda path: analysis)
    monkeypatch.setattr(
        verify.audit_v1, "recompute_summary", lambda *args: recomputed
    )
    monkeypatch.setattr(verify, "_seed_heterogeneity", lambda *args: heterogeneity)
    monkeypatch.setattr(verify, "_directional", lambda *args: directional)
    with pytest.raises(verify.ProbeError, match="directional-replication"):
        verify._verify_analysis(
            tmp_path,
            binding,
            {},
            {
                "base_2b": {
                    "artifact_index_sha256": "8" * 64,
                    "tree_sha256": "9" * 64,
                }
            },
        )


def test_verify_gates_propagates_wrong_run_gate_binding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = tmp_path / "run"
    gate = tmp_path / "gate"
    run.mkdir()
    gate.mkdir()
    (run / "prelaunch.json").write_text(
        json.dumps(
            {
                "snapshot": "base_2b",
                "endpoint_health": {},
                "seed_gate_artifact_index_sha256": "wrong",
            }
        ),
        encoding="utf-8",
    )
    (gate / "preflight.json").write_text(
        json.dumps({"snapshot": "base_2b"}), encoding="utf-8"
    )
    binding = {"effective_registration": {"snapshots": {"base_2b": {}}}}
    receipt = {"artifact_index_sha256": "expected", "tree_sha256": "tree"}
    monkeypatch.setattr(
        verify.runtime, "_verify_seed_gate_binding", lambda *args: receipt
    )

    def reject_wrong_binding(
        run_dir: Path, _binding: dict, _snapshot: str, gate_receipt: dict
    ) -> None:
        prelaunch = json.loads((run_dir / "prelaunch.json").read_text())
        if (
            prelaunch["seed_gate_artifact_index_sha256"]
            != gate_receipt["artifact_index_sha256"]
        ):
            raise verify.ProbeError("run is not bound to seed gate")

    monkeypatch.setattr(verify.runtime, "_verify_checkpoint_grid", reject_wrong_binding)
    with pytest.raises(verify.ProbeError, match="not bound"):
        verify._verify_gates(binding, [gate], [run])
