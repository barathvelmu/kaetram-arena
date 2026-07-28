#!/usr/bin/env python3
"""Verify a published seeded trigger-incidence artifact without modifying it."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.opd import export_trigger_incidence_artifact_v2 as exporter  # noqa: E402
from scripts.opd.verify_trigger_incidence_artifact import (  # noqa: E402
    _verify_file_inventory,
)


SHA256 = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
INDEX_KEYS = {
    "schema_version",
    "study_id",
    "experiment_source_git_commit",
    "analysis_source_git_commit",
    "analysis_script_sha256",
    "export_script_sha256",
    "verifier_script_sha256",
    "independent_audit_script_sha256",
    "registration_sha256",
    "design_sha256",
    "excluded_design_sha256",
    "snapshot_lock_file_sha256",
    "code_files",
    "code_tree_sha256",
    "files",
    "tree_sha256",
}


def _reject_json_constant(value: str) -> None:
    raise exporter.ExportError(f"non-finite JSON constant is forbidden: {value}")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise exporter.ExportError(f"duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def _strict_json_loads(payload: str, *, label: str) -> Any:
    try:
        value = json.loads(
            payload,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
        _reject_nonfinite_numbers(value)
        return value
    except (json.JSONDecodeError, exporter.ExportError) as exc:
        raise exporter.ExportError(f"invalid strict JSON: {label}") from exc


def _reject_nonfinite_numbers(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise exporter.ExportError("non-finite JSON number is forbidden")
    if isinstance(value, dict):
        for child in value.values():
            _reject_nonfinite_numbers(child)
    elif isinstance(value, list):
        for child in value:
            _reject_nonfinite_numbers(child)


def _verify_strict_json(files: list[Path]) -> None:
    for path in files:
        if path.suffix == ".json":
            value = _strict_json_loads(path.read_text(), label=str(path))
            if not isinstance(value, dict):
                raise exporter.ExportError(f"JSON root must be an object: {path}")
        elif path.suffix == ".jsonl":
            for line_number, line in enumerate(path.read_text().splitlines(), start=1):
                value = _strict_json_loads(
                    line,
                    label=f"{path}:{line_number}",
                )
                if not isinstance(value, dict):
                    raise exporter.ExportError(
                        f"JSONL row must be an object: {path}:{line_number}"
                    )


def _require_hash(value: Any, *, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise exporter.ExportError(f"invalid {label}")
    return value


def _canonical_snapshot_directories(
    artifact_dir: Path,
    name: str,
    snapshots: set[str],
) -> None:
    root = artifact_dir / name
    exporter.v1_export._require_regular_directory(root)
    actual = {
        path.name for path in root.iterdir() if path.is_dir() or path.is_symlink()
    }
    if actual != snapshots:
        raise exporter.ExportError(f"public {name} directories are not canonical")


def verify_bundle(
    artifact_dir: Path,
    *,
    forbidden_fragments: tuple[str, ...] = (),
    expected_index_sha256: str | None = None,
) -> dict:
    exporter.v1_export._require_regular_directory(artifact_dir)
    index_path = artifact_dir / "artifact-index.json"
    exporter.v1_export._require_regular_file(index_path)
    actual_index_sha256 = exporter.sha256_file(index_path)
    if expected_index_sha256 is not None:
        _require_hash(
            expected_index_sha256,
            label="expected artifact-index hash",
            pattern=SHA256,
        )
        if actual_index_sha256 != expected_index_sha256:
            raise exporter.ExportError("artifact-index hash differs from trust root")
    index = exporter.v1_export.load_json(index_path)
    if set(index) != INDEX_KEYS:
        raise exporter.ExportError("artifact index has an invalid field set")
    if index.get("schema_version") != exporter.EXPORT_SCHEMA:
        raise exporter.ExportError("artifact schema version mismatch")
    for label in (
        "analysis_script_sha256",
        "export_script_sha256",
        "verifier_script_sha256",
        "independent_audit_script_sha256",
        "registration_sha256",
        "design_sha256",
        "excluded_design_sha256",
        "snapshot_lock_file_sha256",
        "code_tree_sha256",
        "tree_sha256",
    ):
        _require_hash(index.get(label), label=label, pattern=SHA256)
    for label in ("experiment_source_git_commit", "analysis_source_git_commit"):
        _require_hash(index.get(label), label=label, pattern=COMMIT)

    files = _verify_file_inventory(artifact_dir, index)
    _verify_strict_json([*files, index_path])
    registration = exporter.v1_export.load_json(artifact_dir / "registration.json")
    snapshots_value = registration.get("snapshots")
    if not isinstance(snapshots_value, dict) or not snapshots_value:
        raise exporter.ExportError("registration snapshots are missing")
    snapshots = set(snapshots_value)
    _canonical_snapshot_directories(artifact_dir, "runs", snapshots)
    _canonical_snapshot_directories(artifact_dir, "seed-gates", snapshots)
    from scripts.opd.audit_trigger_incidence_artifact_v2 import (
        AuditError,
        _verify_design,
        _verify_expected_request_grid,
        _verify_public_path_contract,
        _verify_runtime_projection,
        _verify_snapshot_lock,
        _verify_source_commits,
    )

    try:
        _verify_public_path_contract(artifact_dir, index, registration)
        _verify_source_commits(artifact_dir, index)
        _verify_snapshot_lock(artifact_dir, registration, index)
        _verify_runtime_projection(artifact_dir, registration)
        design = _verify_design(artifact_dir, registration)
        _verify_expected_request_grid(artifact_dir, registration, design)
    except AuditError as exc:
        raise exporter.ExportError(str(exc)) from exc

    verified = exporter._semantic_verify(artifact_dir)
    summary = verified["summary"]
    excluded_relative = exporter._safe_registered_path(
        verified["registration"]["state_pool"]["excluded_design"]
    )
    code_files = exporter._critical_code_records()
    expected = {
        "schema_version": exporter.EXPORT_SCHEMA,
        "study_id": verified["registration"]["study_id"],
        "experiment_source_git_commit": verified["design"]["source_git_commit"],
        "analysis_source_git_commit": summary["analysis_code_provenance"][
            "source_git_commit"
        ],
        "analysis_script_sha256": verified["analysis_script_sha256"],
        "export_script_sha256": exporter.sha256_file(Path(exporter.__file__).resolve()),
        "verifier_script_sha256": exporter.sha256_file(Path(__file__).resolve()),
        "independent_audit_script_sha256": exporter.sha256_file(
            REPO / "scripts" / "opd" / "audit_trigger_incidence_artifact_v2.py"
        ),
        "registration_sha256": exporter.sha256_file(artifact_dir / "registration.json"),
        "design_sha256": exporter.sha256_file(artifact_dir / "design" / "design.json"),
        "excluded_design_sha256": exporter.sha256_file(
            artifact_dir / excluded_relative
        ),
        "snapshot_lock_file_sha256": exporter.sha256_file(
            artifact_dir / exporter.SNAPSHOT_LOCK_RELATIVE
        ),
        "code_files": code_files,
        "code_tree_sha256": exporter.sha256_json(code_files),
        "files": index["files"],
        "tree_sha256": index["tree_sha256"],
    }
    if index != expected:
        raise exporter.ExportError("artifact index disagrees with semantic contents")

    normalized_forbidden = tuple(
        dict.fromkeys(fragment for fragment in forbidden_fragments if fragment)
    )
    exporter.v1_export._scan_public_text([*files, index_path], normalized_forbidden)
    directional = summary["directional_replication"]
    return {
        "schema_version": exporter.EXPORT_SCHEMA,
        "study_id": index["study_id"],
        "artifact_index_sha256": actual_index_sha256,
        "tree_sha256": index["tree_sha256"],
        "scheduled_requests": summary["scheduled_requests"],
        "successful_requests": summary["successful_requests"],
        "failed_requests": summary["failed_requests"],
        "recovery_opportunities": summary["recovery_opportunities"],
        "directional_replication_passed": directional["passed"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--forbid", action="append", default=[])
    parser.add_argument("--expected-index-sha256")
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
    result = verify_bundle(
        args.artifact_dir,
        forbidden_fragments=tuple(dict.fromkeys((*defaults, *args.forbid))),
        expected_index_sha256=args.expected_index_sha256,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
