import json
import shutil
from pathlib import Path

import pytest

from scripts.opd import audit_trigger_incidence_artifact_v2 as audit
from scripts.opd import export_trigger_incidence_artifact_v2 as exporter
from scripts.opd import trigger_incidence_probe as v1
from scripts.opd import trigger_incidence_probe_v2 as v2
from scripts.opd import verify_trigger_incidence_artifact_v2 as verifier
from tests.unit.test_export_trigger_incidence_artifact import (
    PUBLIC_ATTESTATION_EXTRAS,
    _seal_run,
    _write_json,
    _write_rows,
)
from tests.unit.test_trigger_incidence_probe_v2 import _registration


COMMIT = "a" * 40
FROZEN_COMMIT = "af81627c76bfe9a9febe1864fff43e03dd82e170"


def _health(registration: dict, snapshot: str) -> dict:
    expected = registration["snapshots"][snapshot]
    lock_path = v2.REPO / exporter.SOURCE_SNAPSHOT_LOCK_RELATIVE
    lock = json.loads(lock_path.read_text()) if lock_path.is_file() else None
    lock_sha256 = lock["lock_sha256"] if lock else "2" * 64
    projection = (
        exporter._snapshot_lock_projection(lock, registration) if lock else None
    )
    marker_path = v2.REPO / "runtime-marker.json"
    if marker_path.is_file():
        marker = json.loads(marker_path.read_text())
        receipt = {
            "schema_version": "kaetram.pinned-python-environment-receipt.v1",
            "environment_kind": "local_mlx",
            "marker_sha256": v1.sha256_json(marker),
            "marker": marker,
        }
        runtime_receipt_sha256 = v1.sha256_json(receipt)
    else:
        runtime_receipt_sha256 = "1" * 64
    return {
        "status": "ok",
        "attestation": {
            "api_model": expected["api_model"],
            "checkpoint_sha256": expected["checkpoint_sha256"],
            **registration["endpoint_contract"],
            "deployment_id": f"local-{snapshot}",
            "runtime_environment_receipt_sha256": runtime_receipt_sha256,
            "snapshot_lock_sha256": lock_sha256,
            "snapshot_tree_sha256": (
                projection["checkpoints"][snapshot]["snapshot_tree_sha256"]
                if projection
                else "3" * 64
            ),
            "tokenizer_source_revision": (
                projection["tokenizer_source_revision"] if projection else "4" * 40
            ),
        },
    }


def _seal_gate(
    root: Path,
    registration_path: Path,
    registration: dict,
    snapshot: str,
) -> dict:
    health = _health(registration, snapshot)
    gate = registration["seed_gate"]
    _write_json(
        root / "preflight.json",
        {
            "schema_version": f"{v2.SEED_GATE_SCHEMA}.preflight",
            "study_id": registration["study_id"],
            "snapshot": snapshot,
            "registration_sha256": v1.sha256_file(registration_path),
            "endpoint_health": health,
            "seed_gate": gate,
            "source_git_commit": COMMIT,
            "dirty_paths": [],
        },
    )
    messages = ("alpha", "beta", "gamma", "beta")
    seeds = (100, 101, 102, 101)
    request_ids = ("seed-0", "seed-1", "seed-2", "repeat-1")
    rows = []
    for request_id, seed, content in zip(request_ids, seeds, messages, strict=True):
        message = {"role": "assistant", "content": content}
        rows.append(
            {
                "schema_version": v2.SEED_GATE_SCHEMA,
                "request_id": request_id,
                "seed": seed,
                "status": "ok",
                "latency_seconds": 0.1,
                "attempt_errors": [],
                "response_message": message,
                "semantic_response_sha256": v2.semantic_response_sha256(message),
            }
        )
    _write_rows(root / "results.jsonl", rows)
    _write_json(
        root / "postflight.json",
        {
            "schema_version": f"{v2.SEED_GATE_SCHEMA}.postflight",
            "study_id": registration["study_id"],
            "snapshot": snapshot,
            "endpoint_identity_stable": True,
            "endpoint_health": health,
            "error": None,
        },
    )
    _write_json(
        root / "completed.json",
        {
            "schema_version": f"{v2.SEED_GATE_SCHEMA}.completed",
            "study_id": registration["study_id"],
            "snapshot": snapshot,
            "scheduled_requests": 4,
            "successful_requests": 4,
            "unique_semantic_responses": 3,
            "minimum_unique_semantic_responses": 2,
            "repeated_seed_reproducible": True,
            "endpoint_identity_stable": True,
            "passed": True,
        },
    )
    names = ("preflight.json", "results.jsonl", "postflight.json", "completed.json")
    records = [
        {
            "path": name,
            "size_bytes": (root / name).stat().st_size,
            "sha256": v1.sha256_file(root / name),
        }
        for name in names
    ]
    _write_json(
        root / "artifact-index.json",
        {
            "schema_version": f"{v2.SEED_GATE_SCHEMA}.artifacts",
            "study_id": registration["study_id"],
            "snapshot": snapshot,
            "files": records,
            "tree_sha256": v1.sha256_json(records),
        },
    )
    return health


def _completed(registration: dict, rows: list[dict], snapshot: str) -> dict:
    return {
        "schema_version": f"{v1.RUN_SCHEMA}.completed",
        "study_id": registration["study_id"],
        "snapshot": snapshot,
        "scheduled_requests": len(rows),
        "successful_requests": len(rows),
        "failed_requests": 0,
        "recovery_opportunities": sum(
            bool(row["recovery_opportunity"]) for row in rows
        ),
        "malformed_emissions": sum(bool(row["malformed_emission"]) for row in rows),
        "structured_tool_responses": sum(
            bool(row["has_structured_tool_call"]) for row in rows
        ),
        "no_structured_tool_call_responses": sum(
            bool(row["no_structured_tool_call"]) for row in rows
        ),
        "endpoint_identity_stable": True,
    }


def _fixture(tmp_path: Path, monkeypatch) -> dict:
    monkeypatch.setattr(audit, "_verify_source_commits", lambda _root, _outer: None)
    monkeypatch.setattr(exporter, "_verification_commit", lambda: COMMIT)
    registration_path, registration = _registration(tmp_path, monkeypatch)
    runtime_marker_path = tmp_path / "runtime-marker.json"
    _write_json(
        runtime_marker_path,
        {
            "schema_version": "kaetram.local-mlx-environment.v3",
            "git_commit": FROZEN_COMMIT,
            "python_version": "3.12.12",
            "installed_tree_sha256": "8" * 64,
        },
    )
    render_contract = {
        "schema_version": "kaetram.local-render-contract.v1",
        "seeded_sampling": {
            "schema_version": "kaetram.mlx-explicit-key-sampling.v1",
            "server_script_sha256": "9" * 64,
        },
    }
    registration["endpoint_contract"].update(
        {
            "render_contract_sha256": v1.sha256_json(render_contract),
            "sampling_contract_sha256": v1.sha256_json(
                render_contract["seeded_sampling"]
            ),
        }
    )
    receipt = {
        "schema_version": "kaetram.pinned-python-environment-receipt.v1",
        "environment_kind": "local_mlx",
        "marker_sha256": v1.sha256_json(
            json.loads(runtime_marker_path.read_text())
        ),
        "marker": json.loads(runtime_marker_path.read_text()),
    }
    endpoint_verify_record_path = tmp_path / "endpoint-verify.json"
    _write_json(
        endpoint_verify_record_path,
        {
            "status": "ok",
            "attestation": {
                "runtime_environment_receipt_sha256": v1.sha256_json(receipt),
                "render_contract_sha256": v1.sha256_json(render_contract),
                "sampling_contract_sha256": v1.sha256_json(
                    render_contract["seeded_sampling"]
                ),
            },
            "render_contract": render_contract,
        },
    )
    excluded_path = tmp_path / registration["state_pool"]["excluded_design"]
    excluded_messages = [{"role": "user", "content": "historical state"}]
    excluded_states = [
        {
            "state_id": f"state-{index:02d}",
            "personality": "completionist",
            "source_log": source_log,
            "source_log_sha256": str(index + 7) * 64,
            "messages_sha256": v1.sha256_json(excluded_messages),
            "messages": excluded_messages,
        }
        for index, source_log in enumerate(
            registration["state_pool"]["excluded_source_logs"], start=1
        )
    ]
    excluded_design = {
        "schema_version": v1.DESIGN_SCHEMA,
        "study_id": "excluded-v1",
        "registration_sha256": "e" * 64,
        "source_log_count": 4,
        "eligible_source_log_count": 2,
        "personality": "completionist",
        "selection_stride": 1,
        "excluded_source_log_count": 1,
        "excluded_source_logs_sha256": v1.sha256_json(["earlier.log"]),
        "states": excluded_states,
        "source_git_commit": COMMIT,
        "dirty_paths": [],
    }
    _write_json(excluded_path, excluded_design)
    registration["state_pool"]["excluded_design_sha256"] = v1.sha256_file(
        excluded_path
    )
    _write_json(registration_path, registration)
    lock = {
        "schema_version": "kaetram-hf-snapshot-lock-v1",
        "snapshots": {
            snapshot: {
                "repo_id": f"private-owner/{snapshot}",
                "revision": "4" * 40,
                "files": [
                    {
                        "path": "model.safetensors-00001-of-00001.safetensors",
                        "sha256": record["checkpoint_sha256"],
                        "size_bytes": 100,
                    },
                    *(
                        [
                            {
                                "path": "tokenizer.json",
                                "sha256": registration["endpoint_contract"][
                                    "tokenizer_sha256"
                                ],
                                "size_bytes": 10,
                            }
                        ]
                        if snapshot == "base"
                        else []
                    ),
                ],
            }
            for snapshot, record in registration["snapshots"].items()
        },
    }
    lock["lock_sha256"] = v1.sha256_json(lock)
    source_lock_path = tmp_path / exporter.SOURCE_SNAPSHOT_LOCK_RELATIVE
    _write_json(source_lock_path, lock)
    monkeypatch.setattr(
        audit,
        "_git_blob",
        lambda _commit, relative: (
            source_lock_path.read_bytes()
            if relative == exporter.SOURCE_SNAPSHOT_LOCK_RELATIVE.as_posix()
            else (audit.REPO / relative).read_bytes()
        ),
    )
    assert set(_health(registration, "base")["attestation"]) == {
        "api_model",
        "checkpoint_sha256",
        *registration["endpoint_contract"],
        *PUBLIC_ATTESTATION_EXTRAS,
    }
    registration_sha = v1.sha256_file(registration_path)
    messages = [
        {"role": "system", "content": "Use tools when useful."},
        {"role": "user", "content": "Continue."},
    ]
    states = [
        {
            "state_id": f"state-{index:02d}",
            "personality": "completionist",
            "source_log": f"dataset/source-{index}.log",
            "source_log_sha256": str(index) * 64,
            "messages_sha256": v1.sha256_json(messages),
            "messages": messages,
        }
        for index in (1, 2)
    ]
    design_dir = tmp_path / "design"
    design = {
        "schema_version": v1.DESIGN_SCHEMA,
        "study_id": registration["study_id"],
        "registration_sha256": registration_sha,
        "source_log_count": 4,
        "eligible_source_log_count": 2,
        "personality": "completionist",
        "selection_stride": 1,
        "excluded_source_log_count": len(
            registration["state_pool"]["excluded_source_logs"]
        ),
        "excluded_source_logs_sha256": v1.sha256_json(
            sorted(registration["state_pool"]["excluded_source_logs"])
        ),
        "states": states,
        "source_git_commit": COMMIT,
        "dirty_paths": [],
    }
    design_path = design_dir / "design.json"
    _write_json(design_path, design)
    _write_json(
        design_dir / "design.receipt.json",
        {
            "schema_version": f"{v1.DESIGN_SCHEMA}.receipt",
            "study_id": registration["study_id"],
            "registration_sha256": registration_sha,
            "design_sha256": v1.sha256_file(design_path),
            "state_count": len(states),
            "selected_source_tree_sha256": v1._source_tree_sha256(states),
            "source_git_commit": COMMIT,
            "dirty_paths": [],
        },
    )

    run_dirs = []
    gate_dirs = []
    for snapshot in registration["snapshots"]:
        gate_dir = tmp_path / "gates" / snapshot
        gate_dir.mkdir(parents=True)
        health = _seal_gate(gate_dir, registration_path, registration, snapshot)
        gate_index = json.loads((gate_dir / "artifact-index.json").read_text())
        run_dir = tmp_path / "runs" / snapshot
        run_dir.mkdir(parents=True)
        _write_json(
            run_dir / "prelaunch.json",
            {
                "schema_version": f"{v1.RUN_SCHEMA}.prelaunch",
                "study_id": registration["study_id"],
                "snapshot": snapshot,
                "registration_sha256": registration_sha,
                "design_sha256": v1.sha256_file(design_path),
                "endpoint_health": health,
                "sampling": registration["sampling"],
                "seed_gate_artifact_index_sha256": v1.sha256_file(
                    gate_dir / "artifact-index.json"
                ),
                "seed_gate_tree_sha256": gate_index["tree_sha256"],
                "source_git_commit": COMMIT,
                "dirty_paths": [],
            },
        )
        rows = []
        schedule_index = 0
        conditions = registration["conditions"]
        for state_index, state in enumerate(states):
            for sample_index in range(
                registration["sampling"]["samples_per_state_condition"]
            ):
                offset = (
                    state_index
                    * registration["sampling"]["samples_per_state_condition"]
                    + sample_index
                ) % len(conditions)
                for condition in conditions[offset:] + conditions[:offset]:
                    content = (
                        f"<function=observe()> sample {sample_index} state {state_index}"
                        if condition["native_tool_schema"] == "present"
                        else f"plain {state_index} {sample_index}"
                    )
                    message = {"role": "assistant", "content": content}
                    rows.append(
                        {
                            "schema_version": v1.RUN_SCHEMA,
                            "snapshot": snapshot,
                            "schedule_index": schedule_index,
                            "state_id": state["state_id"],
                            "state_index": state_index,
                            "sample_index": sample_index,
                            "seed": (
                                registration["sampling"]["base_seed"]
                                + 100 * state_index
                                + sample_index
                            ),
                            "condition_id": condition["condition_id"],
                            "documentation": condition["documentation"],
                            "native_tool_schema": condition["native_tool_schema"],
                            "latency_seconds": 0.1,
                            "attempt_errors": [],
                            "status": "ok",
                            "response_message": message,
                            **v1.classify_response_message(message),
                        }
                    )
                    schedule_index += 1
        _write_rows(run_dir / "results.jsonl", rows)
        _write_json(
            run_dir / "postflight.json",
            {
                "schema_version": f"{v1.RUN_SCHEMA}.postflight",
                "study_id": registration["study_id"],
                "snapshot": snapshot,
                "endpoint_identity_stable": True,
                "endpoint_health": health,
                "error": None,
            },
        )
        _write_json(
            run_dir / "completed.json",
            _completed(registration, rows, snapshot),
        )
        _seal_run(run_dir, registration["study_id"], snapshot)
        run_dirs.append(run_dir)
        gate_dirs.append(gate_dir)

    analysis_dir = tmp_path / "analysis"
    original = v1._git_identity
    v1._git_identity = lambda: {
        "source_git_commit": COMMIT,
        "dirty_paths": [],
    }
    try:
        v2.analyze(
            registration_path,
            design_path,
            run_dirs,
            gate_dirs,
            analysis_dir,
        )
    finally:
        v1._git_identity = original
    return {
        "registration_path": registration_path,
        "design_dir": design_dir,
        "run_dirs": run_dirs,
        "seed_gate_dirs": gate_dirs,
        "analysis_dir": analysis_dir,
        "runtime_environment_marker": runtime_marker_path,
        "endpoint_verify_record": endpoint_verify_record_path,
    }


def _export(fixture: dict, output: Path) -> dict:
    return exporter.export_bundle(
        **fixture,
        output_dir=output,
        forbidden_fragments=(),
    )


def _reseal_internal(root: Path, names: tuple[str, ...]) -> None:
    index_path = root / "artifact-index.json"
    index = json.loads(index_path.read_text())
    records = [
        {
            "path": name,
            "size_bytes": (root / name).stat().st_size,
            "sha256": v1.sha256_file(root / name),
        }
        for name in names
    ]
    index["files"] = records
    index["tree_sha256"] = v1.sha256_json(records)
    _write_json(index_path, index)


def _reseal_public(root: Path) -> None:
    index_path = root / "artifact-index.json"
    index = json.loads(index_path.read_text())
    files = sorted(
        path for path in root.rglob("*") if path.is_file() and path != index_path
    )
    records = [
        {
            "path": path.relative_to(root).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": v1.sha256_file(path),
        }
        for path in files
    ]
    index["files"] = records
    index["tree_sha256"] = v1.sha256_json(records)
    _write_json(index_path, index)


def _public_fixture(tmp_path: Path, monkeypatch) -> Path:
    fixture = _fixture(tmp_path, monkeypatch)
    output = tmp_path / "public"
    _export(fixture, output)
    return output


def test_v2_export_is_self_contained_and_independently_audited(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    output = tmp_path / "public"
    manifest = _export(fixture, output)
    result = audit.audit_artifact(output)

    assert manifest["schema_version"] == exporter.EXPORT_SCHEMA
    assert result["scheduled_requests"] == 48
    assert result["failed_requests"] == 0
    assert result["directional_replication_passed"] is True
    assert (output / "seed-gates" / "r3" / "results.jsonl").is_file()
    projection = json.loads(
        (output / exporter.SNAPSHOT_LOCK_RELATIVE).read_text()
    )
    assert "repo_id" not in json.dumps(projection)
    assert "private-owner" not in json.dumps(projection)
    excluded = json.loads(fixture["registration_path"].read_text())["state_pool"][
        "excluded_design"
    ]
    assert (output / excluded).is_file()


def test_v2_export_rejects_resealed_failed_seed_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    gate = fixture["seed_gate_dirs"][0]
    completed = json.loads((gate / "completed.json").read_text())
    completed["passed"] = False
    _write_json(gate / "completed.json", completed)
    names = ("preflight.json", "results.jsonl", "postflight.json", "completed.json")
    records = [
        {
            "path": name,
            "size_bytes": (gate / name).stat().st_size,
            "sha256": v1.sha256_file(gate / name),
        }
        for name in names
    ]
    index = json.loads((gate / "artifact-index.json").read_text())
    index["files"] = records
    index["tree_sha256"] = v1.sha256_json(records)
    _write_json(gate / "artifact-index.json", index)

    with pytest.raises(exporter.ExportError, match="seed-gate verification"):
        _export(fixture, tmp_path / "public")
    assert not (tmp_path / "public").exists()


def test_v2_independent_audit_rejects_resealed_completed_totals(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = _public_fixture(tmp_path, monkeypatch)
    run = output / "runs" / "base"
    completed = json.loads((run / "completed.json").read_text())
    completed["recovery_opportunities"] += 1
    _write_json(run / "completed.json", completed)
    _reseal_internal(
        run,
        ("prelaunch.json", "results.jsonl", "postflight.json", "completed.json"),
    )
    _reseal_public(output)

    with pytest.raises(audit.AuditError, match="run envelope"):
        audit.audit_artifact(output)


def test_v2_independent_audit_rejects_response_metadata_nonce(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = _public_fixture(tmp_path, monkeypatch)
    gate = output / "seed-gates" / "base"
    result_path = gate / "results.jsonl"
    rows = [json.loads(line) for line in result_path.read_text().splitlines()]
    rows[0]["response_message"]["nonce"] = "counterfeit-diversity"
    rows[0]["semantic_response_sha256"] = v1.sha256_json(
        rows[0]["response_message"]
    )
    _write_rows(result_path, rows)
    _reseal_internal(
        gate,
        ("preflight.json", "results.jsonl", "postflight.json", "completed.json"),
    )
    _reseal_public(output)

    with pytest.raises(audit.AuditError, match="non-canonical schema"):
        audit.audit_artifact(output)


def test_v2_independent_audit_rejects_fabricated_recovered_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = _public_fixture(tmp_path, monkeypatch)
    run = output / "runs" / "base"
    result_path = run / "results.jsonl"
    rows = [json.loads(line) for line in result_path.read_text().splitlines()]
    target = next(row for row in rows if row["recovery_opportunity"])
    target["recoverable_calls"] = [
        {"name": "attack", "args": {"mob_name": "invented"}}
    ]
    _write_rows(result_path, rows)
    _reseal_internal(
        run,
        ("prelaunch.json", "results.jsonl", "postflight.json", "completed.json"),
    )
    _reseal_public(output)

    with pytest.raises(audit.AuditError, match="stored detailed outcome"):
        audit.audit_artifact(output)


def test_v2_verifiers_reject_resealed_request_grid_tamper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = _public_fixture(tmp_path, monkeypatch)
    grid_path = output / exporter.REQUEST_GRID_RELATIVE
    rows = [json.loads(line) for line in grid_path.read_text().splitlines()]
    rows[0]["request_payload_sha256"] = "0" * 64
    _write_rows(grid_path, rows)
    _reseal_public(output)

    with pytest.raises(exporter.ExportError, match="expected request grid"):
        verifier.verify_bundle(output)
    with pytest.raises(audit.AuditError, match="expected request grid"):
        audit.audit_artifact(output)


def test_v2_independent_audit_rejects_nonmessage_design_content() -> None:
    with pytest.raises(audit.AuditError, match="chat object"):
        audit._validate_design_messages([42])


def test_v2_independent_audit_rejects_duplicate_tool_argument_keys() -> None:
    message = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "observe",
                    "arguments": '{"x": 1, "x": 2}',
                },
            }
        ],
    }
    with pytest.raises(audit.AuditError, match="invalid strict JSON"):
        audit._validate_response_message(message)


def test_v2_independent_audit_rejects_resealed_csv(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = _public_fixture(tmp_path, monkeypatch)
    analysis = output / "analysis"
    cells = analysis / "cells.csv"
    cells.write_text(cells.read_text().replace(",0.5,", ",0.5001,", 1))
    _reseal_internal(
        analysis,
        ("analysis-summary.json", "cells.csv", "contrasts.csv"),
    )
    _reseal_public(output)

    with pytest.raises(audit.AuditError, match="cells.csv"):
        audit.audit_artifact(output)


def test_v2_independent_audit_rejects_resealed_checkpoint_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = _public_fixture(tmp_path, monkeypatch)
    run = output / "runs" / "base"
    for name in ("prelaunch.json", "postflight.json"):
        payload = json.loads((run / name).read_text())
        payload["endpoint_health"]["attestation"]["checkpoint_sha256"] = "d" * 64
        _write_json(run / name, payload)
    _reseal_internal(
        run,
        ("prelaunch.json", "results.jsonl", "postflight.json", "completed.json"),
    )
    _reseal_public(output)

    with pytest.raises(audit.AuditError, match="attestation"):
        audit.audit_artifact(output)


def test_v2_verifiers_reject_resealed_extra_internal_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = _public_fixture(tmp_path, monkeypatch)
    (output / "runs" / "unregistered.txt").write_text("tampered\n")
    _reseal_public(output)

    with pytest.raises(exporter.ExportError, match="path set is not canonical"):
        verifier.verify_bundle(output)
    with pytest.raises(audit.AuditError, match="path set is not canonical"):
        audit.audit_artifact(output)


def test_v2_independent_audit_rejects_manifest_commit_tamper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = _public_fixture(tmp_path, monkeypatch)
    index_path = output / "artifact-index.json"
    index = json.loads(index_path.read_text())
    index["experiment_source_git_commit"] = "b" * 40
    _write_json(index_path, index)

    with pytest.raises(audit.AuditError, match="experiment_source_git_commit"):
        audit.audit_artifact(output)


def test_v2_verifiers_reject_duplicate_json_keys(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = _public_fixture(tmp_path, monkeypatch)
    index_path = output / "artifact-index.json"
    payload = index_path.read_text().replace(
        '"study_id":',
        '"study_id": "shadow-value",\n  "study_id":',
        1,
    )
    index_path.write_text(payload)

    with pytest.raises(exporter.ExportError, match="strict JSON"):
        verifier.verify_bundle(output)
    with pytest.raises(audit.AuditError, match="strict JSON"):
        audit.audit_artifact(output)


def test_source_commit_check_binds_registration_and_analysis(tmp_path: Path) -> None:
    root = tmp_path / "artifact"
    root.mkdir()
    shutil.copyfile(
        audit.REPO / "research/experiments/local-trigger-incidence-v2.json",
        root / "registration.json",
    )
    outer = {
        "experiment_source_git_commit": FROZEN_COMMIT,
        "analysis_source_git_commit": FROZEN_COMMIT,
        "analysis_script_sha256": "38e7a31787dd081530535c3d860c1022f80d419e176de98547b0953d20e9440a",
    }
    audit._verify_source_commits(root, outer)

    (root / "registration.json").write_text("{}\n")
    with pytest.raises(audit.AuditError, match="registration differs"):
        audit._verify_source_commits(root, outer)


def test_export_interruption_leaves_no_partial_public_directory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fixture = _fixture(tmp_path, monkeypatch)
    output = tmp_path / "public"
    original = exporter.v1_export._copy_exclusive
    calls = 0

    def interrupt_after_first_copy(source: Path, target: Path) -> None:
        nonlocal calls
        original(source, target)
        calls += 1
        if calls == 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(exporter.v1_export, "_copy_exclusive", interrupt_after_first_copy)
    with pytest.raises(KeyboardInterrupt):
        _export(fixture, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".public.staging-*"))


def test_verifiers_reject_overflowed_json_number(tmp_path: Path, monkeypatch) -> None:
    output = _public_fixture(tmp_path, monkeypatch)
    result_path = output / "runs" / "base" / "results.jsonl"
    result_path.write_text(result_path.read_text().replace("0.1", "1e999", 1))
    _reseal_internal(
        result_path.parent,
        ("prelaunch.json", "results.jsonl", "postflight.json", "completed.json"),
    )
    _reseal_public(output)
    with pytest.raises(exporter.ExportError, match="strict JSON"):
        verifier.verify_bundle(output)
    with pytest.raises(audit.AuditError, match="strict JSON"):
        audit.audit_artifact(output)


def test_independent_design_audit_rejects_duplicate_selected_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = _public_fixture(tmp_path, monkeypatch)
    registration = audit.load_object(output / "registration.json")
    design_path = output / "design" / "design.json"
    design = json.loads(design_path.read_text())
    design["states"][1]["source_log"] = design["states"][0]["source_log"]
    _write_json(design_path, design)
    receipt_path = output / "design" / "design.receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["design_sha256"] = v1.sha256_file(design_path)
    receipt["selected_source_tree_sha256"] = v1._source_tree_sha256(design["states"])
    _write_json(receipt_path, receipt)
    with pytest.raises(audit.AuditError, match="design/exclusion binding"):
        audit._verify_design(output, registration)


def test_public_verifiers_accept_and_enforce_external_index_trust_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = _public_fixture(tmp_path, monkeypatch)
    expected = v1.sha256_file(output / "artifact-index.json")
    assert verifier.verify_bundle(
        output, expected_index_sha256=expected
    )["artifact_index_sha256"] == expected
    assert audit.audit_artifact(
        output, expected_index_sha256=expected
    )["artifact_index_sha256"] == expected
    with pytest.raises(exporter.ExportError, match="trust root"):
        verifier.verify_bundle(output, expected_index_sha256="0" * 64)
    with pytest.raises(audit.AuditError, match="trust root"):
        audit.audit_artifact(output, expected_index_sha256="0" * 64)
