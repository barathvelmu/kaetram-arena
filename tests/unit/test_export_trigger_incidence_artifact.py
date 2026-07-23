import json
from pathlib import Path

import pytest

from scripts.opd.export_trigger_incidence_artifact import (
    ANALYSIS_FILES,
    ANALYSIS_SCHEMA,
    DESIGN_SCHEMA,
    RUN_FILES,
    EXPORT_SCHEMA,
    REGISTRATION_SCHEMA,
    RUN_SCHEMA,
    ExportError,
    export_bundle,
    sha256_file,
    sha256_json,
    source_tree_sha256,
)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _seal(root: Path, names: tuple[str, ...], **identity: object) -> dict:
    records = [
        {
            "path": name,
            "size_bytes": (root / name).stat().st_size,
            "sha256": sha256_file(root / name),
        }
        for name in names
    ]
    index = {**identity, "files": records, "tree_sha256": sha256_json(records)}
    _write_json(root / "artifact-index.json", index)
    return index


def _fixture(tmp_path: Path) -> dict:
    registration_path = tmp_path / "registration.json"
    registration = {
        "schema_version": REGISTRATION_SCHEMA,
        "study_id": "study",
        "snapshots": {
            snapshot: {"api_model": snapshot, "checkpoint_sha256": snapshot * 8}
            for snapshot in ("base", "r2", "r3")
        },
        "conditions": [
            {
                "condition_id": f"condition-{index}",
                "documentation": "python_docs",
                "native_tool_schema": "absent",
            }
            for index in range(4)
        ],
        "state_pool": {"state_count": 1},
        "sampling": {"samples_per_state_condition": 1},
    }
    _write_json(registration_path, registration)
    registration_sha = sha256_file(registration_path)

    commit = "a" * 40
    design_dir = tmp_path / "design"
    design_path = design_dir / "design.json"
    states = [
        {
            "state_id": "state-01",
            "personality": "completionist",
            "source_log": "dataset/raw/source.log",
            "source_log_sha256": "b" * 64,
            "messages_sha256": "c" * 64,
        }
    ]
    _write_json(
        design_path,
        {
            "schema_version": DESIGN_SCHEMA,
            "study_id": "study",
            "registration_sha256": registration_sha,
            "source_git_commit": commit,
            "dirty_paths": [],
            "states": states,
        },
    )
    design_sha = sha256_file(design_path)
    _write_json(
        design_dir / "design.receipt.json",
        {
            "schema_version": f"{DESIGN_SCHEMA}.receipt",
            "study_id": "study",
            "registration_sha256": registration_sha,
            "design_sha256": design_sha,
            "state_count": 1,
            "selected_source_tree_sha256": source_tree_sha256(states),
            "source_git_commit": commit,
            "dirty_paths": [],
        },
    )

    run_dirs = []
    input_runs = []
    for snapshot in registration["snapshots"]:
        root = tmp_path / "runs" / snapshot
        health = {"status": "ok", "attestation": {"api_model": snapshot}}
        _write_json(
            root / "prelaunch.json",
            {
                "schema_version": f"{RUN_SCHEMA}.prelaunch",
                "study_id": "study",
                "snapshot": snapshot,
                "registration_sha256": registration_sha,
                "design_sha256": design_sha,
                "source_git_commit": commit,
                "dirty_paths": [],
                "endpoint_health": health,
            },
        )
        rows = [
            {
                "snapshot": snapshot,
                "condition_id": f"condition-{index}",
                "status": "ok",
            }
            for index in range(4)
        ]
        (root / "results.jsonl").write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
        )
        _write_json(
            root / "postflight.json",
            {
                "schema_version": f"{RUN_SCHEMA}.postflight",
                "study_id": "study",
                "snapshot": snapshot,
                "endpoint_identity_stable": True,
                "endpoint_health": health,
            },
        )
        _write_json(
            root / "completed.json",
            {
                "schema_version": f"{RUN_SCHEMA}.completed",
                "study_id": "study",
                "snapshot": snapshot,
                "endpoint_identity_stable": True,
                "scheduled_requests": 4,
                "successful_requests": 4,
                "failed_requests": 0,
            },
        )
        index = _seal(
            root,
            RUN_FILES,
            schema_version=f"{RUN_SCHEMA}.artifacts",
            study_id="study",
            snapshot=snapshot,
        )
        run_dirs.append(root)
        input_runs.append(
            {
                "snapshot": snapshot,
                "artifact_index_sha256": sha256_file(root / "artifact-index.json"),
                "tree_sha256": index["tree_sha256"],
            }
        )

    analysis_dir = tmp_path / "analysis"
    _write_json(
        analysis_dir / "analysis-summary.json",
        {
            "schema_version": ANALYSIS_SCHEMA,
            "study_id": "study",
            "registration_sha256": registration_sha,
            "design_sha256": design_sha,
            "analysis_status": "complete",
            "scheduled_requests": 12,
            "successful_requests": 12,
            "failed_requests": 0,
            "input_runs": sorted(input_runs, key=lambda row: row["snapshot"]),
            "analysis_code_provenance": {
                "source_git_commit": commit,
                "dirty_paths": [],
            },
        },
    )
    (analysis_dir / "cells.csv").write_text("snapshot,requests\nbase,4\n")
    (analysis_dir / "contrasts.csv").write_text("snapshot,effect\nbase,0\n")
    _seal(
        analysis_dir,
        ANALYSIS_FILES,
        schema_version=f"{ANALYSIS_SCHEMA}.artifacts",
    )
    return {
        "registration_path": registration_path,
        "design_dir": design_dir,
        "run_dirs": run_dirs,
        "analysis_dir": analysis_dir,
    }


def test_exports_complete_hash_bound_bundle(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    output = tmp_path / "public"
    manifest = export_bundle(
        **fixture,
        output_dir=output,
        forbidden_fragments=(),
    )
    assert manifest["schema_version"] == EXPORT_SCHEMA
    assert len(manifest["files"]) == 3 + 3 * 5 + 4
    assert (output / "runs" / "r3" / "results.jsonl").is_file()
    assert json.loads((output / "artifact-index.json").read_text()) == manifest


def test_rejects_incomplete_run(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    completed_path = fixture["run_dirs"][0] / "completed.json"
    completed = json.loads(completed_path.read_text())
    completed["successful_requests"] = 3
    _write_json(completed_path, completed)
    _seal(
        fixture["run_dirs"][0],
        RUN_FILES,
        schema_version=f"{RUN_SCHEMA}.artifacts",
        study_id="study",
        snapshot="base",
    )
    with pytest.raises(ExportError, match="incomplete or inconsistent"):
        export_bundle(
            **fixture,
            output_dir=tmp_path / "public",
            forbidden_fragments=(),
        )


def test_rejects_wrong_design_source_tree_receipt(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    receipt_path = fixture["design_dir"] / "design.receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["selected_source_tree_sha256"] = "d" * 64
    _write_json(receipt_path, receipt)
    with pytest.raises(ExportError, match="design, receipt, and registration"):
        export_bundle(
            **fixture,
            output_dir=tmp_path / "public",
            forbidden_fragments=(),
        )


def test_rejects_wrong_analysis_schema(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    summary_path = fixture["analysis_dir"] / "analysis-summary.json"
    summary = json.loads(summary_path.read_text())
    summary["schema_version"] = "unregistered.analysis"
    _write_json(summary_path, summary)
    _seal(
        fixture["analysis_dir"],
        ANALYSIS_FILES,
        schema_version=f"{ANALYSIS_SCHEMA}.artifacts",
    )
    with pytest.raises(ExportError, match="analysis does not bind"):
        export_bundle(
            **fixture,
            output_dir=tmp_path / "public",
            forbidden_fragments=(),
        )


def test_rejects_local_path_leakage(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result_path = fixture["run_dirs"][0] / "results.jsonl"
    result_path.write_text(
        result_path.read_text().replace(
            '"status": "ok"', '"status": "/Users/private"'
        )
    )
    _seal(
        fixture["run_dirs"][0],
        RUN_FILES,
        schema_version=f"{RUN_SCHEMA}.artifacts",
        study_id="study",
        snapshot="base",
    )
    input_runs = []
    for run_dir in fixture["run_dirs"]:
        index = json.loads((run_dir / "artifact-index.json").read_text())
        input_runs.append(
            {
                "snapshot": index["snapshot"],
                "artifact_index_sha256": sha256_file(run_dir / "artifact-index.json"),
                "tree_sha256": index["tree_sha256"],
            }
        )
    summary_path = fixture["analysis_dir"] / "analysis-summary.json"
    summary = json.loads(summary_path.read_text())
    summary["input_runs"] = sorted(input_runs, key=lambda row: row["snapshot"])
    _write_json(summary_path, summary)
    _seal(
        fixture["analysis_dir"],
        ANALYSIS_FILES,
        schema_version=f"{ANALYSIS_SCHEMA}.artifacts",
    )
    with pytest.raises(ExportError, match="forbidden identity/path"):
        export_bundle(
            **fixture,
            output_dir=tmp_path / "public",
            forbidden_fragments=("/Users/",),
        )
