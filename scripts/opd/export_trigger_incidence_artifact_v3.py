#!/usr/bin/env python3
"""Export a closed, anonymous, independently auditable V3 result bundle.

Run the frozen V3 result verifier in its clean execution checkout first and
save its JSON output.  This exporter then copies only the registered inputs,
raw responses, receipts, and anonymous runtime projections into a staging
directory.  The independent public auditor must accept the staged directory
before it is atomically published.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.opd import audit_trigger_incidence_artifact_v3 as audit  # noqa: E402
from scripts.opd import export_trigger_incidence_artifact as v1_export  # noqa: E402
from scripts.opd import export_trigger_incidence_artifact_v2 as v2_export  # noqa: E402


ExportError = v1_export.ExportError
sha256_file = v1_export.sha256_file
sha256_json = v1_export.sha256_json
RUN_FILES = (
    "prelaunch.json",
    "results.jsonl",
    "postflight.json",
    "completed.json",
    "artifact-index.json",
)
GATE_FILES = (
    "preflight.json",
    "results.jsonl",
    "postflight.json",
    "completed.json",
    "artifact-index.json",
)
ANALYSIS_FILES = (
    "analysis-summary.json",
    "cells.csv",
    "contrasts.csv",
    "artifact-index.json",
)
DESIGN_FILES = (
    "effective-registration.json",
    "design.json",
    "design.receipt.json",
    "v3-preparation.receipt.json",
)
SOURCE_SNAPSHOT_LOCK = Path(
    "research/experiments/provenance/public-hf-snapshots.lock.json"
)


def _load(path: Path) -> dict:
    value = v1_export.load_json(path)
    if not isinstance(value, dict):
        raise ExportError(f"expected JSON object: {path}")
    return value


def _snapshot_directory_map(paths: list[Path], envelope: str) -> dict[str, Path]:
    result = {}
    for path in paths:
        v1_export._require_regular_directory(path)
        record = _load(path / envelope)
        snapshot = record.get("snapshot")
        if (
            not isinstance(snapshot, str)
            or audit.SAFE_SNAPSHOT.fullmatch(snapshot) is None
            or snapshot in result
        ):
            raise ExportError("duplicate or unsafe snapshot input")
        result[snapshot] = path
    return result


def _require_closed_directory(path: Path, names: tuple[str, ...]) -> None:
    v1_export._require_regular_directory(path)
    actual = {item.name for item in path.iterdir()}
    if actual != set(names) or any(
        item.is_symlink() or not item.is_file() for item in path.iterdir()
    ):
        raise ExportError(f"input directory membership is not closed: {path}")


def _copy(source: Path, target: Path) -> None:
    v1_export._require_regular_file(source)
    v1_export._copy_exclusive(source, target)


def _binding_from_sources(
    runs: dict[str, Path], gates: dict[str, Path], analysis_dir: Path
) -> dict:
    bindings = []
    for snapshot in sorted(runs):
        bindings.append(
            _load(runs[snapshot] / "prelaunch.json").get("v3_runtime_binding")
        )
        bindings.append(
            _load(gates[snapshot] / "preflight.json").get("v3_runtime_binding")
        )
    bindings.append(
        _load(analysis_dir / "analysis-summary.json").get("v3_runtime_binding")
    )
    if not bindings or any(value != bindings[0] for value in bindings[1:]):
        raise ExportError("V3 runtime bindings disagree across source artifacts")
    try:
        return audit._binding_from(bindings[0])
    except audit.AuditError as exc:
        raise ExportError(str(exc)) from exc


def _validate_result_receipt(
    receipt: dict,
    binding: dict,
    runs: dict[str, Path],
    gates: dict[str, Path],
    analysis_dir: Path,
) -> None:
    expected_runs = {
        snapshot: sha256_file(path / "artifact-index.json")
        for snapshot, path in sorted(runs.items())
    }
    expected_gates = {
        snapshot: sha256_file(path / "artifact-index.json")
        for snapshot, path in sorted(gates.items())
    }
    if (
        receipt.get("schema_version")
        != "kaetram.local-trigger-incidence-v3-result-verification.v1"
        or receipt.get("study_id") != audit.V3_STUDY_ID
        or receipt.get("execution_commit") != binding["execution_commit"]
        or receipt.get("design_sha256") != binding["design_sha256"]
        or receipt.get("expected_request_grid_sha256")
        != binding["expected_request_grid_sha256"]
        or receipt.get("analysis_artifact_index_sha256")
        != sha256_file(analysis_dir / "artifact-index.json")
        or receipt.get("run_artifact_indexes") != expected_runs
        or receipt.get("seed_gate_artifact_indexes") != expected_gates
        or receipt.get("independent_recomputation") is not True
    ):
        raise ExportError("saved V3 result-verification receipt does not bind inputs")


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ExportError("cannot identify exporter source commit") from exc


def export_bundle(
    *,
    registration_path: Path,
    design_dir: Path,
    run_dirs: list[Path],
    seed_gate_dirs: list[Path],
    analysis_dir: Path,
    result_verification: Path,
    runtime_environment_marker: Path,
    endpoint_verify_record: Path,
    output_dir: Path,
    forbidden_fragments: tuple[str, ...],
) -> dict:
    v1_export._require_regular_file(registration_path)
    v1_export._require_regular_directory(design_dir)
    for name in DESIGN_FILES:
        v1_export._require_regular_file(design_dir / name)
    _require_closed_directory(analysis_dir, ANALYSIS_FILES)
    runs = _snapshot_directory_map(run_dirs, "prelaunch.json")
    gates = _snapshot_directory_map(seed_gate_dirs, "preflight.json")
    if set(runs) != set(gates) or len(runs) != 3:
        raise ExportError("export requires exactly three matching runs and seed gates")
    for path in runs.values():
        _require_closed_directory(path, RUN_FILES)
    for path in gates.values():
        _require_closed_directory(path, GATE_FILES)
    v1_export._require_regular_file(result_verification)
    v1_export._require_regular_file(runtime_environment_marker)
    v1_export._require_regular_file(endpoint_verify_record)
    v1_export._require_regular_file(REPO / SOURCE_SNAPSHOT_LOCK)

    registration = _load(registration_path)
    effective = _load(design_dir / "effective-registration.json")
    if (
        registration.get("schema_version") != audit.V3_REGISTRATION_SCHEMA
        or registration.get("study_id") != audit.V3_STUDY_ID
        or set(runs) != set(effective.get("snapshots", {}))
    ):
        raise ExportError("V3 registration and checkpoint inputs disagree")
    binding = _binding_from_sources(runs, gates, analysis_dir)
    receipt = _load(result_verification)
    _validate_result_receipt(receipt, binding, runs, gates, analysis_dir)

    baseline_relative = Path(registration["frozen_v2_protocol"]["path"])
    excluded_relative = Path(registration["state_pool"]["excluded_design"])
    for relative in (baseline_relative, excluded_relative):
        if relative.is_absolute() or ".." in relative.parts:
            raise ExportError("registered source path is unsafe")
        v1_export._require_regular_file(REPO / relative)

    source_roots = [
        registration_path,
        design_dir,
        *runs.values(),
        *gates.values(),
        analysis_dir,
        result_verification,
        runtime_environment_marker,
        endpoint_verify_record,
    ]
    v1_export._reject_output_overlap(output_dir, source_roots)
    final = output_dir
    final.parent.mkdir(parents=True, exist_ok=True)
    if final.exists() or final.is_symlink():
        raise ExportError(f"refusing to overwrite export directory: {final}")
    staging = Path(
        tempfile.mkdtemp(prefix=f".{final.name}.staging-", dir=final.parent)
    )
    try:
        _copy(registration_path, staging / "registration.json")
        for name in DESIGN_FILES:
            _copy(design_dir / name, staging / "design" / name)
        _copy(REPO / baseline_relative, staging / "design/frozen-v2-registration.json")
        _copy(REPO / excluded_relative, staging / audit.EXCLUDED_DESIGN)
        _copy(result_verification, staging / "result-verification.json")
        for snapshot, source in runs.items():
            for name in RUN_FILES:
                _copy(source / name, staging / "runs" / snapshot / name)
        for snapshot, source in gates.items():
            for name in GATE_FILES:
                _copy(source / name, staging / "seed-gates" / snapshot / name)
        for name in ANALYSIS_FILES:
            _copy(analysis_dir / name, staging / "analysis" / name)

        v2_export._write_snapshot_lock_projection(
            staging / audit.SNAPSHOT_PROJECTION,
            effective,
        )
        v2_export._write_runtime_projection(
            staging / audit.RUNTIME_PROJECTION,
            runtime_environment_marker,
            endpoint_verify_record,
            effective,
        )
        grid_path = staging / "design/expected-request-grid.jsonl"
        with grid_path.open("x", encoding="utf-8") as handle:
            for row in audit._expected_payload_grid(
                effective, _load(staging / "design/design.json")
            ):
                handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
                handle.flush()
            os.fsync(handle.fileno())

        public_files = sorted(path for path in staging.rglob("*") if path.is_file())
        v1_export._scan_public_text(public_files, forbidden_fragments)
        records = [
            {
                "path": path.relative_to(staging).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in public_files
        ]
        manifest: dict[str, Any] = {
            "schema_version": audit.PUBLIC_SCHEMA,
            "study_id": audit.V3_STUDY_ID,
            "claim_scope": (
                "Non-confirmatory finite-grid replication on a different retained "
                "historical state panel; not recovery utility or broad generalization."
            ),
            "design_source_git_commit": binding["design_source_git_commit"],
            "execution_source_git_commit": binding["execution_commit"],
            "exporter_source_git_commit": _git_head(),
            "registration_sha256": sha256_file(staging / "registration.json"),
            "effective_registration_sha256": sha256_file(
                staging / "design/effective-registration.json"
            ),
            "design_sha256": sha256_file(staging / "design/design.json"),
            "result_verification_sha256": sha256_file(
                staging / "result-verification.json"
            ),
            "export_script_sha256": sha256_file(Path(__file__).resolve()),
            "independent_audit_script_sha256": sha256_file(
                Path(audit.__file__).resolve()
            ),
            "files": records,
            "tree_sha256": sha256_json(records),
        }
        with (staging / "artifact-index.json").open("x", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        audited = audit.audit_artifact(staging)
        if audited["artifact_tree_sha256"] != manifest["tree_sha256"]:
            raise ExportError("independent V3 public audit disagrees")
        staging.rename(final)
        return manifest
    except BaseException:
        shutil.rmtree(staging)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--design-dir", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, action="append", required=True)
    parser.add_argument("--seed-gate-dir", type=Path, action="append", required=True)
    parser.add_argument("--analysis-dir", type=Path, required=True)
    parser.add_argument("--result-verification", type=Path, required=True)
    parser.add_argument("--runtime-environment-marker", type=Path, required=True)
    parser.add_argument("--endpoint-verify-record", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--forbid", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    defaults = (
        str(Path.home()),
        os.environ.get("USER", ""),
        os.environ.get("LOGNAME", ""),
        "barath",
        "patnir",
    )
    manifest = export_bundle(
        registration_path=args.registration,
        design_dir=args.design_dir,
        run_dirs=args.run_dir,
        seed_gate_dirs=args.seed_gate_dir,
        analysis_dir=args.analysis_dir,
        result_verification=args.result_verification,
        runtime_environment_marker=args.runtime_environment_marker,
        endpoint_verify_record=args.endpoint_verify_record,
        output_dir=args.out_dir,
        forbidden_fragments=tuple(dict.fromkeys((*defaults, *args.forbid))),
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
