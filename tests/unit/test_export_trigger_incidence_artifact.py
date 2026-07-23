import json
from pathlib import Path

import pytest

from scripts.opd.export_trigger_incidence_artifact import (
    ANALYSIS_FILES,
    RUN_FILES,
    EXPORT_SCHEMA,
    ExportError,
    export_bundle,
    sha256_file,
    sha256_json,
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
        "study_id": "study",
        "snapshots": {
            snapshot: {"api_model": snapshot, "checkpoint_sha256": snapshot * 8}
            for snapshot in ("base", "r2", "r3")
        },
        "conditions": [
            {"condition_id": f"condition-{index}"} for index in range(4)
        ],
        "state_pool": {"state_count": 1},
        "sampling": {"samples_per_state_condition": 1},
    }
    _write_json(registration_path, registration)
    registration_sha = sha256_file(registration_path)

    commit = "a" * 40
    design_dir = tmp_path / "design"
    design_path = design_dir / "design.json"
    _write_json(
        design_path,
        {
            "study_id": "study",
            "registration_sha256": registration_sha,
            "source_git_commit": commit,
            "dirty_paths": [],
            "states": [{"state_id": "state-01"}],
        },
    )
    design_sha = sha256_file(design_path)
    _write_json(
        design_dir / "design.receipt.json",
        {
            "registration_sha256": registration_sha,
            "design_sha256": design_sha,
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
                "snapshot": snapshot,
                "endpoint_identity_stable": True,
                "endpoint_health": health,
            },
        )
        _write_json(
            root / "completed.json",
            {
                "snapshot": snapshot,
                "endpoint_identity_stable": True,
                "scheduled_requests": 4,
                "successful_requests": 4,
                "failed_requests": 0,
            },
        )
        index = _seal(root, RUN_FILES, snapshot=snapshot)
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
    _seal(analysis_dir, ANALYSIS_FILES)
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
    _seal(fixture["run_dirs"][0], RUN_FILES, snapshot="base")
    with pytest.raises(ExportError, match="incomplete or inconsistent"):
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
    _seal(fixture["run_dirs"][0], RUN_FILES, snapshot="base")
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
    _seal(fixture["analysis_dir"], ANALYSIS_FILES)
    with pytest.raises(ExportError, match="forbidden identity/path"):
        export_bundle(
            **fixture,
            output_dir=tmp_path / "public",
            forbidden_fragments=("/Users/",),
        )
