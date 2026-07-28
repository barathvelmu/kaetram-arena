from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.opd import audit_trigger_incidence_artifact_v3 as audit
from scripts.opd import export_trigger_incidence_artifact_v3 as export
from scripts.opd import trigger_incidence_probe as probe


ROOT = Path(__file__).resolve().parents[2]
V3_REGISTRATION = ROOT / "research/experiments/local-trigger-incidence-v3.json"
V3_DESIGN = ROOT / "research/experiments/local-trigger-incidence-v3-design"
V2_REGISTRATION = ROOT / "research/experiments/local-trigger-incidence-v2.json"
V2_DESIGN = ROOT / "research/artifacts/local-trigger-incidence-v2/design/design.json"


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _copy_design_fixture(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(V3_REGISTRATION, root / "registration.json")
    (root / "design").mkdir(parents=True, exist_ok=True)
    for name in (
        "effective-registration.json",
        "design.json",
        "design.receipt.json",
        "v3-preparation.receipt.json",
    ):
        shutil.copyfile(V3_DESIGN / name, root / "design" / name)
    shutil.copyfile(V2_REGISTRATION, root / "design/frozen-v2-registration.json")
    target = root / audit.EXCLUDED_DESIGN
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(V2_DESIGN, target)


def _seal(directory: Path, names: tuple[str, ...], *, schema: str, study: str,
          snapshot: str | None = None) -> None:
    records = [
        {
            "path": name,
            "size_bytes": (directory / name).stat().st_size,
            "sha256": audit.sha256_file(directory / name),
        }
        for name in names
    ]
    index = {
        "schema_version": schema,
        "study_id": study,
        "files": records,
        "tree_sha256": audit.sha256_json(records),
    }
    if snapshot is not None:
        index["snapshot"] = snapshot
    _write(directory / "artifact-index.json", index)


def _build_synthetic_public_bundle(root: Path) -> None:
    _copy_design_fixture(root)
    registration = audit.load_object(root / "design/effective-registration.json")
    design = audit.load_object(root / "design/design.json")
    metadata_grid = audit._expected_metadata_grid(registration, design)
    binding = {
        "schema_version": "kaetram.local-trigger-incidence-v3-runtime-binding.v1",
        "study_id": audit.V3_STUDY_ID,
        "v3_registration_sha256": audit.sha256_file(root / "registration.json"),
        "effective_registration_sha256": audit.sha256_file(
            root / "design/effective-registration.json"
        ),
        "design_sha256": audit.sha256_file(root / "design/design.json"),
        "design_source_git_commit": design["source_git_commit"],
        "execution_commit": "b" * 40,
        "execution_verification_sha256": "c" * 64,
        "execution_verifier_sha256": "d" * 64,
        "expected_request_count": len(metadata_grid),
        "expected_request_grid_sha256": audit.sha256_json(metadata_grid),
        "claim_boundary_sha256": audit.sha256_json(registration["claim_boundary"]),
    }
    snapshot_projection_source = (
        ROOT
        / "research/artifacts/local-trigger-incidence-v2"
        / audit.SNAPSHOT_PROJECTION
    )
    runtime_projection_source = (
        ROOT
        / "research/artifacts/local-trigger-incidence-v2"
        / audit.RUNTIME_PROJECTION
    )
    for source, relative in (
        (snapshot_projection_source, audit.SNAPSHOT_PROJECTION),
        (runtime_projection_source, audit.RUNTIME_PROJECTION),
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    old = ROOT / "research/artifacts/local-trigger-incidence-v2"
    all_rows = {}
    run_ids = {}
    gate_ids = {}
    for snapshot in registration["snapshots"]:
        health = audit.load_object(old / "runs" / snapshot / "prelaunch.json")[
            "endpoint_health"
        ]
        gate_dir = root / "seed-gates" / snapshot
        gate_dir.mkdir(parents=True)
        gate_rows = []
        gate_contract = registration["seed_gate"]
        distinct = int(gate_contract["distinct_seed_count"])
        repeat = int(gate_contract["repeat_seed_index"])
        for index in range(distinct):
            message = {
                "role": "assistant",
                "content": f"seeded synthetic response {index}",
            }
            gate_rows.append(
                {
                    "schema_version": audit.SEED_GATE_SCHEMA,
                    "request_id": f"seed-{index}",
                    "seed": int(gate_contract["base_seed"]) + index,
                    "latency_seconds": 0.01,
                    "attempt_errors": [],
                    "status": "ok",
                    "response_message": message,
                    "semantic_response_sha256": (
                        audit.audit_v2._semantic_response_sha256(message)
                    ),
                }
            )
        repeated = dict(gate_rows[repeat])
        repeated["request_id"] = f"repeat-{repeat}"
        gate_rows.append(repeated)
        _write(
            gate_dir / "preflight.json",
            {
                "schema_version": f"{audit.SEED_GATE_SCHEMA}.preflight",
                "study_id": audit.V3_STUDY_ID,
                "snapshot": snapshot,
                "registration_sha256": binding["effective_registration_sha256"],
                "endpoint_health": health,
                "seed_gate": gate_contract,
                "source_git_commit": binding["execution_commit"],
                "dirty_paths": [],
                "v3_runtime_binding": binding,
            },
        )
        (gate_dir / "results.jsonl").write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in gate_rows),
            encoding="utf-8",
        )
        _write(
            gate_dir / "postflight.json",
            {
                "schema_version": f"{audit.SEED_GATE_SCHEMA}.postflight",
                "study_id": audit.V3_STUDY_ID,
                "snapshot": snapshot,
                "endpoint_identity_stable": True,
                "endpoint_health": health,
                "error": None,
            },
        )
        _write(
            gate_dir / "completed.json",
            {
                "schema_version": f"{audit.SEED_GATE_SCHEMA}.completed",
                "study_id": audit.V3_STUDY_ID,
                "snapshot": snapshot,
                "scheduled_requests": len(gate_rows),
                "successful_requests": len(gate_rows),
                "unique_semantic_responses": distinct,
                "minimum_unique_semantic_responses": int(
                    gate_contract["minimum_unique_semantic_responses"]
                ),
                "repeated_seed_reproducible": True,
                "endpoint_identity_stable": True,
                "passed": True,
            },
        )
        _seal(
            gate_dir,
            ("preflight.json", "results.jsonl", "postflight.json", "completed.json"),
            schema=f"{audit.SEED_GATE_SCHEMA}.artifacts",
            study=audit.V3_STUDY_ID,
            snapshot=snapshot,
        )
        gate_index = audit.load_object(gate_dir / "artifact-index.json")
        gate_ids[snapshot] = {
            "artifact_index_sha256": audit.sha256_file(
                gate_dir / "artifact-index.json"
            ),
            "tree_sha256": gate_index["tree_sha256"],
        }

        run_dir = root / "runs" / snapshot
        run_dir.mkdir(parents=True)
        run_rows = []
        for row in (item for item in metadata_grid if item["snapshot"] == snapshot):
            message = {"role": "assistant", "content": "No tool call."}
            result = {
                **row,
                "latency_seconds": 0.01,
                "attempt_errors": [],
                "status": "ok",
                "response_message": message,
                **probe.classify_response_message(message),
            }
            run_rows.append(result)
            key = (
                snapshot,
                row["condition_id"],
                row["state_id"],
                row["sample_index"],
            )
            all_rows[key] = result
        _write(
            run_dir / "prelaunch.json",
            {
                "schema_version": f"{audit.RUN_SCHEMA}.prelaunch",
                "study_id": audit.V3_STUDY_ID,
                "snapshot": snapshot,
                "registration_sha256": binding["effective_registration_sha256"],
                "design_sha256": binding["design_sha256"],
                "endpoint_health": health,
                "sampling": registration["sampling"],
                "seed_gate_artifact_index_sha256": gate_ids[snapshot][
                    "artifact_index_sha256"
                ],
                "seed_gate_tree_sha256": gate_ids[snapshot]["tree_sha256"],
                "source_git_commit": binding["execution_commit"],
                "dirty_paths": [],
                "v3_runtime_binding": binding,
            },
        )
        (run_dir / "results.jsonl").write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in run_rows),
            encoding="utf-8",
        )
        _write(
            run_dir / "postflight.json",
            {
                "schema_version": f"{audit.RUN_SCHEMA}.postflight",
                "study_id": audit.V3_STUDY_ID,
                "snapshot": snapshot,
                "endpoint_identity_stable": True,
                "endpoint_health": health,
                "error": None,
            },
        )
        _write(
            run_dir / "completed.json",
            audit.audit_v2._expected_completed(registration, snapshot, run_rows),
        )
        _seal(
            run_dir,
            ("prelaunch.json", "results.jsonl", "postflight.json", "completed.json"),
            schema=f"{audit.RUN_SCHEMA}.artifacts",
            study=audit.V3_STUDY_ID,
            snapshot=snapshot,
        )
        run_index = audit.load_object(run_dir / "artifact-index.json")
        run_ids[snapshot] = {
            "artifact_index_sha256": audit.sha256_file(run_dir / "artifact-index.json"),
            "tree_sha256": run_index["tree_sha256"],
        }

    recomputed = audit.audit_v1.recompute_summary(registration, design, all_rows)
    heterogeneity = audit.audit_v2._recompute_seed_heterogeneity(registration, all_rows)
    effects = {
        item["snapshot"]: item["effect_rate_difference"]
        for item in recomputed["registered_contrasts"]
        if item["contrast"] == "native_tools_main"
    }
    analysis_dir = root / "analysis"
    analysis_dir.mkdir()
    analysis = {
        "schema_version": audit.ANALYSIS_SCHEMA,
        "study_id": audit.V3_STUDY_ID,
        "registration_sha256": binding["effective_registration_sha256"],
        "design_sha256": binding["design_sha256"],
        "analysis_code_provenance": {
            "source_git_commit": binding["execution_commit"],
            "dirty_paths": [],
            "analysis_script_sha256": "e" * 64,
            "runtime_binding_adapter_sha256": "f" * 64,
            "python_version": "3.12.13",
        },
        "input_runs": sorted(
            (
                {
                    "snapshot": snapshot,
                    "artifact_index_sha256": run_ids[snapshot]["artifact_index_sha256"],
                    "tree_sha256": run_ids[snapshot]["tree_sha256"],
                }
                for snapshot in run_ids
            ),
            key=lambda item: item["snapshot"],
        ),
        **recomputed,
        "claim_boundary": registration["claim_boundary"],
        "registered_seed_heterogeneity": heterogeneity,
        "directional_replication": {
            "criterion": registration["analysis"]["directional_replication_criterion"],
            "status": "evaluated",
            "native_tools_effects": effects,
            "passed": False,
        },
        "v3_runtime_binding": binding,
    }
    _write(analysis_dir / "analysis-summary.json", analysis)
    (analysis_dir / "cells.csv").write_bytes(
        audit.audit_v2._csv_bytes(recomputed["cells"])
    )
    (analysis_dir / "contrasts.csv").write_bytes(
        audit.audit_v2._csv_bytes(recomputed["registered_contrasts"])
    )
    _seal(
        analysis_dir,
        ("analysis-summary.json", "cells.csv", "contrasts.csv"),
        schema=f"{audit.ANALYSIS_SCHEMA}.artifacts",
        study=audit.V3_STUDY_ID,
    )
    _write(
        root / "result-verification.json",
        {
            "schema_version": (
                "kaetram.local-trigger-incidence-v3-result-verification.v1"
            ),
            "study_id": audit.V3_STUDY_ID,
            "execution_commit": binding["execution_commit"],
            "design_sha256": binding["design_sha256"],
            "expected_request_grid_sha256": binding["expected_request_grid_sha256"],
            "analysis_artifact_index_sha256": audit.sha256_file(
                analysis_dir / "artifact-index.json"
            ),
            "run_artifact_indexes": {
                snapshot: run_ids[snapshot]["artifact_index_sha256"]
                for snapshot in sorted(run_ids)
            },
            "seed_gate_artifact_indexes": {
                snapshot: gate_ids[snapshot]["artifact_index_sha256"]
                for snapshot in sorted(gate_ids)
            },
            "analysis_status": "complete",
            "scheduled_requests": 1200,
            "successful_requests": 1200,
            "failed_requests": 0,
            "claim_boundary_sha256": binding["claim_boundary_sha256"],
            "independent_recomputation": True,
            "verification_script_sha256": "1" * 64,
        },
    )
    grid_path = root / "design/expected-request-grid.jsonl"
    grid_path.write_text(
        "".join(
            json.dumps(row, sort_keys=True) + "\n"
            for row in audit._expected_payload_grid(registration, design)
        ),
        encoding="utf-8",
    )
    public_files = sorted(path for path in root.rglob("*") if path.is_file())
    records = [
        {
            "path": path.relative_to(root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": audit.sha256_file(path),
        }
        for path in public_files
    ]
    _write(
        root / "artifact-index.json",
        {
            "schema_version": audit.PUBLIC_SCHEMA,
            "study_id": audit.V3_STUDY_ID,
            "design_source_git_commit": binding["design_source_git_commit"],
            "execution_source_git_commit": binding["execution_commit"],
            "registration_sha256": audit.sha256_file(root / "registration.json"),
            "effective_registration_sha256": binding["effective_registration_sha256"],
            "design_sha256": binding["design_sha256"],
            "files": records,
            "tree_sha256": audit.sha256_json(records),
        },
    )


def test_real_v3_grid_is_complete_and_payload_bound() -> None:
    registration = audit.load_object(V3_DESIGN / "effective-registration.json")
    design = audit.load_object(V3_DESIGN / "design.json")
    metadata = audit._expected_metadata_grid(registration, design)
    payloads = audit._expected_payload_grid(registration, design)
    assert len(metadata) == len(payloads) == 1200
    assert len({row["request_payload_sha256"] for row in payloads}) == 1200
    assert {row["snapshot"] for row in payloads} == {
        "base_2b",
        "opd_r2_2b",
        "opd_r3_2b",
    }


def test_real_v3_design_has_content_as_well_as_path_separation(tmp_path: Path) -> None:
    _copy_design_fixture(tmp_path)
    v3, effective = audit._verify_registration(tmp_path)
    design = audit._verify_design(tmp_path, v3, effective)
    selected = {state["messages_sha256"] for state in design["states"]}
    excluded = {
        state["messages_sha256"]
        for state in audit.load_object(tmp_path / audit.EXCLUDED_DESIGN)["states"]
    }
    assert len(selected) == 20
    assert not selected.intersection(excluded)


def test_duplicate_v3_message_state_fails_closed(tmp_path: Path) -> None:
    _copy_design_fixture(tmp_path)
    v3, effective = audit._verify_registration(tmp_path)
    design_path = tmp_path / "design/design.json"
    design = audit.load_object(design_path)
    design["states"][1]["messages"] = design["states"][0]["messages"]
    design["states"][1]["messages_sha256"] = design["states"][0]["messages_sha256"]
    _write(design_path, design)
    records = [
        {
            "state_id": state["state_id"],
            "personality": state["personality"],
            "source_log": state["source_log"],
            "source_log_sha256": state["source_log_sha256"],
            "messages_sha256": state["messages_sha256"],
        }
        for state in design["states"]
    ]
    tree = audit.sha256_json(records)
    receipt_path = tmp_path / "design/design.receipt.json"
    receipt = audit.load_object(receipt_path)
    receipt["design_sha256"] = audit.sha256_file(design_path)
    receipt["selected_source_tree_sha256"] = tree
    _write(receipt_path, receipt)
    prep_path = tmp_path / "design/v3-preparation.receipt.json"
    prep = audit.load_object(prep_path)
    prep["design_sha256"] = audit.sha256_file(design_path)
    prep["selected_source_tree_sha256"] = tree
    _write(prep_path, prep)
    with pytest.raises(audit.AuditError, match="duplicated or overlaps"):
        audit._verify_design(tmp_path, v3, effective)


def test_outer_inventory_rejects_unindexed_membership(tmp_path: Path) -> None:
    payload = tmp_path / "registration.json"
    payload.write_text("{}\n", encoding="utf-8")
    records = [
        {
            "path": "registration.json",
            "size_bytes": payload.stat().st_size,
            "sha256": audit.sha256_file(payload),
        }
    ]
    _write(
        tmp_path / "artifact-index.json",
        {
            "schema_version": audit.PUBLIC_SCHEMA,
            "files": records,
            "tree_sha256": audit.sha256_json(records),
        },
    )
    audit._verify_outer_inventory(tmp_path)
    (tmp_path / "unindexed.txt").write_text("surprise", encoding="utf-8")
    with pytest.raises(audit.AuditError, match="unindexed"):
        audit._verify_outer_inventory(tmp_path)


@pytest.mark.parametrize(
    "payload",
    (
        b"/Users/private/project",
        b"/home/researcher/run",
        b"contact@example.org",
        b"https://example.modal.run/v1",
    ),
)
def test_anonymity_scan_rejects_identity_fragments(
    tmp_path: Path, payload: bytes
) -> None:
    path = tmp_path / "payload.txt"
    path.write_bytes(payload)
    with pytest.raises(audit.AuditError, match="identity leak"):
        audit._scan_anonymity(tmp_path, ["payload.txt"])


def test_extended_health_checks_snapshot_runtime_and_deployment() -> None:
    registration = audit.load_object(V3_DESIGN / "effective-registration.json")
    health = {
        "status": "ok",
        "attestation": {
            "api_model": "2b-base",
            "checkpoint_sha256": registration["snapshots"]["base_2b"][
                "checkpoint_sha256"
            ],
            **registration["endpoint_contract"],
            "runtime_environment_receipt_sha256": "1" * 64,
            "snapshot_lock_sha256": "2" * 64,
            "snapshot_tree_sha256": "3" * 64,
            "tokenizer_source_revision": "4" * 40,
            "deployment_id": "local-mlx-lm-0.31.3-base_2b-444444444444-"
            + registration["endpoint_contract"]["render_contract_sha256"][:12],
        },
    }
    snapshot_projection = {
        "source_lock_sha256": "2" * 64,
        "tokenizer_source_revision": "4" * 40,
        "checkpoints": {
            "base_2b": {
                "revision": "4" * 40,
                "snapshot_tree_sha256": "3" * 64,
            }
        },
    }
    runtime_projection = {
        "runtime_environment_receipt_sha256": "1" * 64,
        "render_contract_sha256": registration["endpoint_contract"][
            "render_contract_sha256"
        ],
        "sampling_contract_sha256": registration["endpoint_contract"][
            "sampling_contract_sha256"
        ],
        "render_contract": {"engine_version": "0.31.3"},
    }
    audit._verify_health_extended(
        health,
        registration,
        "base_2b",
        snapshot_projection,
        runtime_projection,
    )
    health["attestation"]["snapshot_tree_sha256"] = "9" * 64
    with pytest.raises(audit.AuditError, match="extended endpoint"):
        audit._verify_health_extended(
            health,
            registration,
            "base_2b",
            snapshot_projection,
            runtime_projection,
        )


def test_export_rejects_incomplete_run_set_before_writing(tmp_path: Path) -> None:
    with pytest.raises(export.ExportError):
        export.export_bundle(
            registration_path=V3_REGISTRATION,
            design_dir=V3_DESIGN,
            run_dirs=[],
            seed_gate_dirs=[],
            analysis_dir=tmp_path / "missing-analysis",
            result_verification=tmp_path / "missing-verification.json",
            runtime_environment_marker=tmp_path / "missing-marker.json",
            endpoint_verify_record=tmp_path / "missing-endpoint.json",
            output_dir=tmp_path / "must-not-exist",
            forbidden_fragments=(),
        )
    assert not (tmp_path / "must-not-exist").exists()


def test_full_synthetic_public_bundle_independently_recomputes(tmp_path: Path) -> None:
    _build_synthetic_public_bundle(tmp_path)
    result = audit.audit_artifact(tmp_path)
    assert result["scheduled_requests"] == 1200
    assert result["successful_requests"] == 1200
    assert result["failed_requests"] == 0
    assert result["directional_replication"]["passed"] is False
    assert result["independent_recomputation"] is True
    assert result["anonymous"] is True


def test_full_bundle_response_tamper_fails_closed(tmp_path: Path) -> None:
    _build_synthetic_public_bundle(tmp_path)
    result_path = tmp_path / "runs/base_2b/results.jsonl"
    rows = result_path.read_text(encoding="utf-8").splitlines()
    row = json.loads(rows[0])
    row["recovery_opportunity"] = True
    rows[0] = json.dumps(row, sort_keys=True)
    result_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    # Even if an attacker refreshes the inner and outer byte inventories, the
    # independent raw-response classification must reject the semantic tamper.
    run_dir = result_path.parent
    _seal(
        run_dir,
        ("prelaunch.json", "results.jsonl", "postflight.json", "completed.json"),
        schema=f"{audit.RUN_SCHEMA}.artifacts",
        study=audit.V3_STUDY_ID,
        snapshot="base_2b",
    )
    index = audit.load_object(tmp_path / "artifact-index.json")
    public_files = sorted(
        path for path in tmp_path.rglob("*")
        if path.is_file() and path != tmp_path / "artifact-index.json"
    )
    records = [
        {
            "path": path.relative_to(tmp_path).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": audit.sha256_file(path),
        }
        for path in public_files
    ]
    index["files"] = records
    index["tree_sha256"] = audit.sha256_json(records)
    _write(tmp_path / "artifact-index.json", index)
    with pytest.raises(audit.AuditError, match="raw outcome"):
        audit.audit_artifact(tmp_path)


def test_exporter_round_trip_is_anonymous_and_auditable(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _build_synthetic_public_bundle(source)
    runtime = audit.load_object(source / audit.RUNTIME_PROJECTION)
    marker = tmp_path / "runtime-marker.json"
    endpoint = tmp_path / "endpoint-verify.json"
    _write(
        marker,
        runtime["runtime_environment_receipt"]["marker"],
    )
    base_health = audit.load_object(
        source / "runs/base_2b/prelaunch.json"
    )["endpoint_health"]
    _write(
        endpoint,
        {
            "status": "ok",
            "attestation": base_health["attestation"],
            "render_contract": runtime["render_contract"],
        },
    )
    output = tmp_path / "exported"
    export.export_bundle(
        registration_path=source / "registration.json",
        design_dir=source / "design",
        run_dirs=[
            source / "runs" / name
            for name in ("base_2b", "opd_r2_2b", "opd_r3_2b")
        ],
        seed_gate_dirs=[
            source / "seed-gates" / name
            for name in ("base_2b", "opd_r2_2b", "opd_r3_2b")
        ],
        analysis_dir=source / "analysis",
        result_verification=source / "result-verification.json",
        runtime_environment_marker=marker,
        endpoint_verify_record=endpoint,
        output_dir=output,
        forbidden_fragments=("definitely-not-present",),
    )
    result = audit.audit_artifact(output)
    assert result["scheduled_requests"] == 1200
    assert result["anonymous"] is True
