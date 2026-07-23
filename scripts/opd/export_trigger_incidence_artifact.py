#!/usr/bin/env python3
"""Verify and export the complete local trigger-incidence evidence bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


RUN_FILES = (
    "prelaunch.json",
    "results.jsonl",
    "postflight.json",
    "completed.json",
)
ANALYSIS_FILES = ("analysis-summary.json", "cells.csv", "contrasts.csv")
EXPORT_SCHEMA = "kaetram.local-trigger-incidence-public-artifact.v1"


class ExportError(RuntimeError):
    """Raised when the source bundle is incomplete, inconsistent, or unsafe."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(f"cannot read JSON file: {path}") from exc
    if not isinstance(value, dict):
        raise ExportError(f"JSON root must be an object: {path}")
    return value


def verify_index(root: Path, data_files: tuple[str, ...]) -> tuple[dict, str]:
    index_path = root / "artifact-index.json"
    index = load_json(index_path)
    if root.is_symlink() or not root.is_dir():
        raise ExportError(f"artifact root must be a regular directory: {root}")
    expected_names = {*data_files, "artifact-index.json"}
    actual_names = {item.name for item in root.iterdir()}
    if actual_names != expected_names:
        raise ExportError(f"unexpected artifact membership: {root}")
    if any(item.is_symlink() or not item.is_file() for item in root.iterdir()):
        raise ExportError(f"artifact entries must be regular files: {root}")
    records = index.get("files")
    if (
        not isinstance(records, list)
        or tuple(record.get("path") for record in records) != data_files
        or index.get("tree_sha256") != sha256_json(records)
    ):
        raise ExportError(f"invalid artifact index contract: {index_path}")
    for record in records:
        if set(record) != {"path", "size_bytes", "sha256"}:
            raise ExportError(f"invalid artifact descriptor: {index_path}")
        source = root / record["path"]
        if (
            source.stat().st_size != record["size_bytes"]
            or sha256_file(source) != record["sha256"]
        ):
            raise ExportError(f"artifact hash mismatch: {source}")
    return index, sha256_file(index_path)


def scan_forbidden(paths: list[Path], fragments: tuple[str, ...]) -> None:
    active = tuple(fragment for fragment in fragments if fragment)
    if not active:
        return
    for path in paths:
        try:
            text = path.read_text(errors="strict")
        except UnicodeDecodeError as exc:
            raise ExportError(f"expected UTF-8 text artifact: {path}") from exc
        for fragment in active:
            if fragment in text:
                raise ExportError(f"forbidden identity/path fragment in {path}: {fragment}")


def _copy_exact(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def export_bundle(
    *,
    registration_path: Path,
    design_dir: Path,
    run_dirs: list[Path],
    analysis_dir: Path,
    output_dir: Path,
    forbidden_fragments: tuple[str, ...],
) -> dict:
    if output_dir.exists():
        raise ExportError(f"refusing to overwrite export directory: {output_dir}")
    if len(run_dirs) == 0:
        raise ExportError("at least one run directory is required")

    registration = load_json(registration_path)
    registration_sha256 = sha256_file(registration_path)
    snapshots = registration.get("snapshots")
    conditions = registration.get("conditions")
    state_pool = registration.get("state_pool")
    sampling = registration.get("sampling")
    if (
        not isinstance(snapshots, dict)
        or not isinstance(conditions, list)
        or not isinstance(state_pool, dict)
        or not isinstance(sampling, dict)
    ):
        raise ExportError("registration lacks the required design objects")

    design_path = design_dir / "design.json"
    receipt_path = design_dir / "design.receipt.json"
    design = load_json(design_path)
    receipt = load_json(receipt_path)
    design_sha256 = sha256_file(design_path)
    source_commit = design.get("source_git_commit")
    if (
        design.get("registration_sha256") != registration_sha256
        or receipt.get("registration_sha256") != registration_sha256
        or receipt.get("design_sha256") != design_sha256
        or receipt.get("source_git_commit") != source_commit
        or design.get("dirty_paths") != []
        or receipt.get("dirty_paths") != []
    ):
        raise ExportError("design, receipt, and registration identities disagree")

    expected_per_run = (
        int(state_pool["state_count"])
        * int(sampling["samples_per_state_condition"])
        * len(conditions)
    )
    run_records = []
    text_sources = [registration_path, design_path, receipt_path]
    seen_snapshots = set()
    for run_dir in run_dirs:
        run_index, run_index_sha256 = verify_index(run_dir, RUN_FILES)
        prelaunch = load_json(run_dir / "prelaunch.json")
        postflight = load_json(run_dir / "postflight.json")
        completed = load_json(run_dir / "completed.json")
        snapshot = completed.get("snapshot")
        if snapshot in seen_snapshots or snapshot not in snapshots:
            raise ExportError(f"duplicate or unregistered snapshot: {snapshot}")
        if (
            prelaunch.get("snapshot") != snapshot
            or postflight.get("snapshot") != snapshot
            or run_index.get("snapshot") != snapshot
            or prelaunch.get("registration_sha256") != registration_sha256
            or prelaunch.get("design_sha256") != design_sha256
            or prelaunch.get("source_git_commit") != source_commit
            or prelaunch.get("dirty_paths") != []
            or not postflight.get("endpoint_identity_stable")
            or not completed.get("endpoint_identity_stable")
            or postflight.get("endpoint_health") != prelaunch.get("endpoint_health")
            or completed.get("scheduled_requests") != expected_per_run
            or completed.get("successful_requests") != expected_per_run
            or completed.get("failed_requests") != 0
        ):
            raise ExportError(f"incomplete or inconsistent run: {run_dir}")
        if len((run_dir / "results.jsonl").read_text().splitlines()) != expected_per_run:
            raise ExportError(f"raw result line count mismatch: {run_dir}")
        seen_snapshots.add(snapshot)
        run_records.append(
            {
                "snapshot": snapshot,
                "artifact_index_sha256": run_index_sha256,
                "tree_sha256": run_index["tree_sha256"],
                "source": run_dir,
            }
        )
        text_sources.extend(run_dir / name for name in (*RUN_FILES, "artifact-index.json"))
    if seen_snapshots != set(snapshots):
        raise ExportError("export requires exactly one complete run per snapshot")

    analysis_index, analysis_index_sha256 = verify_index(
        analysis_dir, ANALYSIS_FILES
    )
    summary = load_json(analysis_dir / "analysis-summary.json")
    expected_total = expected_per_run * len(snapshots)
    expected_inputs = sorted(
        (
            {
                "snapshot": record["snapshot"],
                "artifact_index_sha256": record["artifact_index_sha256"],
                "tree_sha256": record["tree_sha256"],
            }
            for record in run_records
        ),
        key=lambda item: item["snapshot"],
    )
    if (
        summary.get("registration_sha256") != registration_sha256
        or summary.get("design_sha256") != design_sha256
        or summary.get("analysis_status") != "complete"
        or summary.get("scheduled_requests") != expected_total
        or summary.get("successful_requests") != expected_total
        or summary.get("failed_requests") != 0
        or summary.get("input_runs") != expected_inputs
        or summary.get("analysis_code_provenance", {}).get("source_git_commit")
        != source_commit
        or summary.get("analysis_code_provenance", {}).get("dirty_paths") != []
    ):
        raise ExportError("analysis does not bind the complete registered run set")
    text_sources.extend(
        analysis_dir / name for name in (*ANALYSIS_FILES, "artifact-index.json")
    )
    scan_forbidden(text_sources, forbidden_fragments)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
    )
    try:
        _copy_exact(registration_path, temporary / "registration.json")
        _copy_exact(design_path, temporary / "design" / "design.json")
        _copy_exact(receipt_path, temporary / "design" / "design.receipt.json")
        for record in run_records:
            source = record["source"]
            for name in (*RUN_FILES, "artifact-index.json"):
                _copy_exact(source / name, temporary / "runs" / record["snapshot"] / name)
        for name in (*ANALYSIS_FILES, "artifact-index.json"):
            _copy_exact(analysis_dir / name, temporary / "analysis" / name)

        files = []
        for path in sorted(temporary.rglob("*")):
            if path.is_file():
                relative = path.relative_to(temporary).as_posix()
                files.append(
                    {
                        "path": relative,
                        "size_bytes": path.stat().st_size,
                        "sha256": sha256_file(path),
                    }
                )
        manifest = {
            "schema_version": EXPORT_SCHEMA,
            "study_id": registration.get("study_id"),
            "source_git_commit": source_commit,
            "registration_sha256": registration_sha256,
            "design_sha256": design_sha256,
            "analysis_artifact_index_sha256": analysis_index_sha256,
            "analysis_tree_sha256": analysis_index["tree_sha256"],
            "files": files,
            "tree_sha256": sha256_json(files),
        }
        (temporary / "artifact-index.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        temporary.rename(output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--design-dir", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, action="append", required=True)
    parser.add_argument("--analysis-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--forbid", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    defaults = (str(Path.home()), f"/Users/{os.environ.get('USER', '')}")
    manifest = export_bundle(
        registration_path=args.registration,
        design_dir=args.design_dir,
        run_dirs=args.run_dir,
        analysis_dir=args.analysis_dir,
        output_dir=args.out_dir,
        forbidden_fragments=tuple(dict.fromkeys((*defaults, *args.forbid))),
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
