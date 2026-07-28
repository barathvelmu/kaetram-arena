#!/usr/bin/env python3
"""Semantically verify and export the seeded trigger-incidence replication."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path, PurePosixPath
from typing import Iterator


REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.opd import export_trigger_incidence_artifact as v1_export  # noqa: E402
from scripts.opd import trigger_incidence_probe as v1_probe  # noqa: E402
from scripts.opd import trigger_incidence_probe_v2 as probe  # noqa: E402


RUN_FILES = v1_export.RUN_FILES
GATE_FILES = (
    "preflight.json",
    "results.jsonl",
    "postflight.json",
    "completed.json",
    "artifact-index.json",
)
ANALYSIS_FILES = v1_export.ANALYSIS_FILES
SNAPSHOT_LOCK_RELATIVE = Path(
    "research/experiments/provenance/public-hf-snapshot-projection.json"
)
REQUEST_GRID_RELATIVE = Path("design/expected-request-grid.jsonl")
RUNTIME_PROJECTION_RELATIVE = Path(
    "research/experiments/provenance/local-runtime-projection.json"
)
SOURCE_SNAPSHOT_LOCK_RELATIVE = Path(
    "research/experiments/provenance/public-hf-snapshots.lock.json"
)
EXPORT_SCHEMA = "kaetram.local-trigger-incidence-public-artifact.v2"
SAFE_SNAPSHOT_ID = v1_export.SAFE_SNAPSHOT_ID
ExportError = v1_export.ExportError
sha256_file = v1_export.sha256_file
sha256_json = v1_export.sha256_json
_SEMANTIC_VERIFY_LOCK = threading.RLock()
CRITICAL_CODE_PATHS = (
    "bootstrap.py",
    "eval_harness.py",
    "finetune/render.py",
    "prompts/game_knowledge.md",
    "prompts/personalities/completionist.md",
    "prompts/system.md",
    "requirements/local-mlx.lock",
    "run_manifest.py",
    "scripts/bootstrap_local_mlx.py",
    "scripts/fetch_hf_snapshot.py",
    "scripts/installed_environment_identity.py",
    "scripts/isolated_python_entry.py",
    "scripts/local_mlx_endpoint.py",
    "scripts/log_analysis/parse.py",
    "scripts/mlx_seeded_server.py",
    "scripts/opd/audit_trigger_incidence_artifact.py",
    "scripts/opd/audit_trigger_incidence_artifact_v2.py",
    "scripts/opd/analyze_structured_call_validity.py",
    "scripts/opd/canonicalize.py",
    "scripts/opd/endpoint_policy.py",
    "scripts/opd/export_trigger_incidence_artifact.py",
    "scripts/opd/export_trigger_incidence_artifact_v2.py",
    "scripts/opd/opd_probe.py",
    "scripts/opd/opd_round1.py",
    "scripts/opd/trigger_incidence_probe.py",
    "scripts/opd/trigger_incidence_probe_v2.py",
    "scripts/opd/verify_trigger_incidence_artifact.py",
    "scripts/opd/verify_trigger_incidence_artifact_v2.py",
    "tool_surface.py",
)


def _critical_code_records(repo: Path = REPO) -> list[dict[str, str]]:
    records = []
    for relative in CRITICAL_CODE_PATHS:
        path = repo / relative
        v1_export._require_regular_file(path)
        records.append({"path": relative, "sha256": sha256_file(path)})
    return records


def _verification_commit(repo: Path = REPO) -> str:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout.strip()
        for relative in CRITICAL_CODE_PATHS:
            blob = subprocess.run(
                ["git", "show", f"{commit}:{relative}"],
                cwd=repo,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).stdout
            if hashlib.sha256(blob).hexdigest() != sha256_file(
                repo / relative
            ):
                raise ExportError(f"critical code differs from Git: {relative}")
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ExportError("cannot bind verification code to Git") from exc
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ExportError("verification commit is invalid")
    return commit


def _snapshot_lock_projection(source: dict, registration: dict) -> dict:
    unsigned = dict(source)
    source_lock_sha256 = unsigned.pop("lock_sha256", None)
    if source_lock_sha256 != sha256_json(unsigned):
        raise ExportError("source snapshot lock digest is invalid")
    snapshots = source.get("snapshots")
    if not isinstance(snapshots, dict):
        raise ExportError("source snapshot lock records are missing")
    checkpoints = {}
    for snapshot, registered in registration["snapshots"].items():
        record = snapshots.get(snapshot)
        files = record.get("files") if isinstance(record, dict) else None
        if not isinstance(files, list):
            raise ExportError(f"source snapshot lock is missing: {snapshot}")
        weights = [
            item
            for item in files
            if item.get("path") == "model.safetensors-00001-of-00001.safetensors"
        ]
        if len(weights) != 1 or weights[0].get("sha256") != registered[
            "checkpoint_sha256"
        ]:
            raise ExportError(f"source snapshot checkpoint mismatch: {snapshot}")
        tree_records = [
            {
                "path": item["path"],
                "size_bytes": item["size_bytes"],
                "identity": item.get("sha256") or item["git_blob_sha1"],
            }
            for item in sorted(files, key=lambda item: item["path"])
        ]
        snapshot_tree_sha256 = sha256_json(
            {
                "repo_id": record["repo_id"],
                "revision": record["revision"],
                "files": tree_records,
            }
        )
        checkpoints[snapshot] = {
            "checkpoint_sha256": weights[0]["sha256"],
            "revision": record.get("revision"),
            "snapshot_tree_sha256": snapshot_tree_sha256,
        }
    base = snapshots.get("base_2b")
    if base is None:
        base = snapshots.get(next(iter(registration["snapshots"])))
    tokenizer = [
        item
        for item in base.get("files", [])
        if isinstance(item, dict) and item.get("path") == "tokenizer.json"
    ]
    if len(tokenizer) != 1 or tokenizer[0].get("sha256") != registration[
        "endpoint_contract"
    ].get("tokenizer_sha256"):
        raise ExportError("source snapshot tokenizer mismatch")
    projection = {
        "schema_version": "kaetram-hf-snapshot-lock-public-projection-v1",
        "source_lock_sha256": source_lock_sha256,
        "tokenizer_source_revision": base.get("revision"),
        "tokenizer_sha256": tokenizer[0]["sha256"],
        "checkpoints": checkpoints,
    }
    projection["projection_sha256"] = sha256_json(projection)
    return projection


def _write_snapshot_lock_projection(target: Path, registration: dict) -> None:
    source = v1_export.load_json(probe.REPO / SOURCE_SNAPSHOT_LOCK_RELATIVE)
    projection = _snapshot_lock_projection(source, registration)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x") as handle:
        json.dump(projection, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _expected_request_grid(registration: dict, design: dict) -> list[dict]:
    records = []
    sampling = registration["sampling"]
    samples_per_state = int(sampling["samples_per_state_condition"])
    tools_sha256 = sha256_json(v1_probe.MODEL_VISIBLE_TOOL_DEFINITIONS)
    for snapshot, snapshot_contract in registration["snapshots"].items():
        schedule_index = 0
        for state_index, state in enumerate(design["states"]):
            for sample_index in range(samples_per_state):
                offset = (state_index * samples_per_state + sample_index) % len(
                    registration["conditions"]
                )
                conditions = registration["conditions"]
                for condition in conditions[offset:] + conditions[:offset]:
                    seed = int(sampling["base_seed"]) + 100 * state_index + sample_index
                    messages = v1_probe.condition_messages(
                        state["messages"], condition["documentation"]
                    )
                    payload = {
                        "model": snapshot_contract["api_model"],
                        "messages": messages,
                        "max_tokens": sampling["max_tokens"],
                        "temperature": sampling["temperature"],
                        "top_p": sampling["top_p"],
                        "top_k": sampling["top_k"],
                        "presence_penalty": sampling["presence_penalty"],
                        "seed": seed,
                    }
                    current_tools_sha256 = None
                    if condition["native_tool_schema"] == "present":
                        payload["tools"] = v1_probe.MODEL_VISIBLE_TOOL_DEFINITIONS
                        current_tools_sha256 = tools_sha256
                    elif condition["native_tool_schema"] != "absent":
                        raise ExportError("unknown native-tool-schema condition")
                    records.append(
                        {
                            "schema_version": (
                                "kaetram.local-trigger-incidence-expected-request.v1"
                            ),
                            "snapshot": snapshot,
                            "schedule_index": schedule_index,
                            "state_id": state["state_id"],
                            "state_index": state_index,
                            "sample_index": sample_index,
                            "condition_id": condition["condition_id"],
                            "seed": seed,
                            "messages_sha256": sha256_json(messages),
                            "tools_sha256": current_tools_sha256,
                            "request_payload_sha256": sha256_json(payload),
                        }
                    )
                    schedule_index += 1
    return records


def _write_expected_request_grid(target: Path, registration: dict, design: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x") as handle:
        for record in _expected_request_grid(registration, design):
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _runtime_projection(marker: dict, endpoint_record: dict, registration: dict) -> dict:
    if marker.get("schema_version") != "kaetram.local-mlx-environment.v3":
        raise ExportError("runtime environment marker schema is invalid")
    if any(isinstance(value, str) and value.startswith("/") for value in marker.values()):
        raise ExportError("runtime environment marker contains an absolute path")
    receipt = {
        "schema_version": "kaetram.pinned-python-environment-receipt.v1",
        "environment_kind": "local_mlx",
        "marker_sha256": sha256_json(marker),
        "marker": marker,
    }
    receipt_sha256 = sha256_json(receipt)
    if (
        endpoint_record.get("status") != "ok"
        or not isinstance(endpoint_record.get("attestation"), dict)
        or not isinstance(endpoint_record.get("render_contract"), dict)
    ):
        raise ExportError("endpoint verification record is invalid")
    attestation = endpoint_record["attestation"]
    render_contract = endpoint_record["render_contract"]
    render_sha256 = sha256_json(render_contract)
    sampling = render_contract.get("seeded_sampling")
    if not isinstance(sampling, dict):
        raise ExportError("render contract lacks seeded-sampling provenance")
    sampling_sha256 = sha256_json(sampling)
    expected = registration["endpoint_contract"]
    if (
        attestation.get("runtime_environment_receipt_sha256") != receipt_sha256
        or attestation.get("render_contract_sha256") != render_sha256
        or attestation.get("sampling_contract_sha256") != sampling_sha256
        or expected.get("render_contract_sha256") != render_sha256
        or expected.get("sampling_contract_sha256") != sampling_sha256
    ):
        raise ExportError("runtime projection differs from endpoint attestation")
    projection = {
        "schema_version": "kaetram.local-runtime-public-projection.v1",
        "runtime_environment_receipt": receipt,
        "runtime_environment_receipt_sha256": receipt_sha256,
        "render_contract": render_contract,
        "render_contract_sha256": render_sha256,
        "sampling_contract_sha256": sampling_sha256,
    }
    projection["projection_sha256"] = sha256_json(projection)
    return projection


def _write_runtime_projection(
    target: Path,
    marker_path: Path,
    endpoint_record_path: Path,
    registration: dict,
) -> None:
    projection = _runtime_projection(
        v1_export.load_json(marker_path),
        v1_export.load_json(endpoint_record_path),
        registration,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x") as handle:
        json.dump(projection, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _safe_registered_path(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ExportError("excluded design path must be a nonempty string")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ExportError("excluded design path is unsafe")
    if pure.as_posix() != value:
        raise ExportError("excluded design path is not canonical")
    return Path(*pure.parts)


@contextlib.contextmanager
def _artifact_repo(root: Path) -> Iterator[None]:
    original = probe.REPO
    probe.REPO = root
    try:
        yield
    finally:
        probe.REPO = original


def _canonical_directories(root: Path, name: str) -> list[Path]:
    container = root / name
    v1_export._require_regular_directory(container)
    directories = sorted(container.iterdir())
    if not directories or any(
        item.is_symlink() or not item.is_dir() for item in directories
    ):
        raise ExportError(f"staged {name} must contain regular directories only")
    return directories


def _semantic_verify_unlocked(staged: Path) -> dict:
    registration_path = staged / "registration.json"
    design_path = staged / "design" / "design.json"
    with _artifact_repo(staged):
        try:
            registration, registration_sha256 = probe.load_registration(
                registration_path
            )
            unsafe_snapshots = [
                snapshot
                for snapshot in registration["snapshots"]
                if SAFE_SNAPSHOT_ID.fullmatch(snapshot) is None
            ]
            if unsafe_snapshots:
                raise ExportError(
                    "registered snapshot IDs must be safe single path components"
                )
            design = probe.load_design(
                design_path,
                registration,
                registration_sha256,
            )
        except v1_probe.ProbeError as exc:
            raise ExportError(
                "producer registration/design verification failed"
            ) from exc

        snapshots = list(registration["snapshots"])
        staged_runs = _canonical_directories(staged, "runs")
        staged_gates = _canonical_directories(staged, "seed-gates")
        if len(staged_runs) != len(snapshots) or len(staged_gates) != len(snapshots):
            raise ExportError("staged bundle does not contain every run and seed gate")

        runs_by_snapshot: dict[str, Path] = {}
        run_envelopes: dict[str, tuple[dict, dict]] = {}
        for run_dir in staged_runs:
            try:
                with probe._v1_protocol_extensions():
                    prelaunch, postflight, _completed, _rows, _identity = (
                        v1_probe._verify_run_directory(run_dir, registration)
                    )
            except v1_probe.ProbeError as exc:
                raise ExportError(
                    f"producer run verification failed: {run_dir}"
                ) from exc
            snapshot = prelaunch["snapshot"]
            if snapshot in runs_by_snapshot:
                raise ExportError(f"duplicate staged run snapshot: {snapshot}")
            v1_export._validate_health_allowlist(
                prelaunch["endpoint_health"], registration, snapshot
            )
            v1_export._validate_health_allowlist(
                postflight["endpoint_health"], registration, snapshot
            )
            runs_by_snapshot[snapshot] = run_dir
            run_envelopes[snapshot] = (prelaunch, postflight)

        gates_by_snapshot: dict[str, Path] = {}
        gate_receipts: dict[str, dict] = {}
        for gate_dir in staged_gates:
            preflight = v1_export.load_json(gate_dir / "preflight.json")
            snapshot = preflight.get("snapshot")
            if snapshot in gates_by_snapshot:
                raise ExportError(f"duplicate staged seed-gate snapshot: {snapshot}")
            if snapshot not in registration["snapshots"]:
                raise ExportError(f"unregistered staged seed gate: {snapshot}")
            prelaunch, _postflight = run_envelopes[snapshot]
            try:
                receipt = probe.verify_seed_gate(
                    gate_dir,
                    registration,
                    registration_sha256,
                    snapshot,
                    prelaunch["endpoint_health"],
                )
            except v1_probe.ProbeError as exc:
                raise ExportError(
                    f"producer seed-gate verification failed: {gate_dir}"
                ) from exc
            if (
                receipt["source_git_commit"] != design["source_git_commit"]
                or prelaunch.get("seed_gate_artifact_index_sha256")
                != receipt["artifact_index_sha256"]
                or prelaunch.get("seed_gate_tree_sha256") != receipt["tree_sha256"]
            ):
                raise ExportError(
                    f"{snapshot}: outcome run is not bound to the staged seed gate"
                )
            gates_by_snapshot[snapshot] = gate_dir
            gate_receipts[snapshot] = receipt

        if set(runs_by_snapshot) != set(snapshots) or set(gates_by_snapshot) != set(
            snapshots
        ):
            raise ExportError(
                "staged runs and seed gates do not match registered checkpoints"
            )

        summary_path = staged / "analysis" / "analysis-summary.json"
        summary = v1_export.load_json(summary_path)
        provenance = summary.get("analysis_code_provenance")
        expected_script_sha256 = sha256_file(Path(probe.__file__).resolve())
        if (
            not isinstance(provenance, dict)
            or set(provenance)
            != {
                "source_git_commit",
                "dirty_paths",
                "analysis_script_sha256",
                "python_version",
            }
            or provenance.get("analysis_script_sha256") != expected_script_sha256
            or provenance.get("python_version") != sys.version.split()[0]
            or provenance.get("dirty_paths") != []
            or re.fullmatch(
                r"[0-9a-f]{40}", str(provenance.get("source_git_commit", ""))
            )
            is None
        ):
            raise ExportError("analysis implementation provenance does not match")

        with tempfile.TemporaryDirectory(
            prefix="kaetram-trigger-v2-reanalysis-"
        ) as raw:
            regenerated = Path(raw) / "analysis"
            try:
                with v1_export._analysis_identity(provenance):
                    probe.analyze(
                        registration_path,
                        design_path,
                        [runs_by_snapshot[snapshot] for snapshot in snapshots],
                        [gates_by_snapshot[snapshot] for snapshot in snapshots],
                        regenerated,
                    )
            except v1_probe.ProbeError as exc:
                raise ExportError("producer semantic reanalysis failed") from exc
            for name in ANALYSIS_FILES:
                existing = staged / "analysis" / name
                reproduced = regenerated / name
                if existing.read_bytes() != reproduced.read_bytes():
                    raise ExportError(
                        f"checked-in analysis differs from raw-data reanalysis: {name}"
                    )

    for name, mapping in (
        ("runs", runs_by_snapshot),
        ("seed-gates", gates_by_snapshot),
    ):
        for snapshot in snapshots:
            current = mapping[snapshot]
            target = staged / name / snapshot
            if current != target:
                current.rename(target)
    return {
        "registration": registration,
        "design": design,
        "summary": summary,
        "analysis_script_sha256": expected_script_sha256,
        "gate_receipts": gate_receipts,
    }


def _semantic_verify(staged: Path) -> dict:
    """Serialize legacy module-identity overrides used by the CLI verifier."""
    with _SEMANTIC_VERIFY_LOCK:
        return _semantic_verify_unlocked(staged)


def _source_files(
    registration_path: Path,
    design_dir: Path,
    run_dirs: list[Path],
    seed_gate_dirs: list[Path],
    analysis_dir: Path,
) -> list[tuple[Path, Path]]:
    registration = v1_export.load_json(registration_path)
    excluded_relative = _safe_registered_path(
        registration.get("state_pool", {}).get("excluded_design")
    )
    excluded_source = probe.REPO / excluded_relative
    sources = [
        (registration_path, Path("registration.json")),
        (design_dir / "design.json", Path("design/design.json")),
        (design_dir / "design.receipt.json", Path("design/design.receipt.json")),
        (excluded_source, excluded_relative),
    ]
    for index, run_dir in enumerate(run_dirs, start=1):
        for name in RUN_FILES:
            sources.append((run_dir / name, Path("runs") / f"input-{index:02d}" / name))
    for index, gate_dir in enumerate(seed_gate_dirs, start=1):
        for name in GATE_FILES:
            sources.append(
                (
                    gate_dir / name,
                    Path("seed-gates") / f"input-{index:02d}" / name,
                )
            )
    for name in ANALYSIS_FILES:
        sources.append((analysis_dir / name, Path("analysis") / name))
    return sources


def export_bundle(
    *,
    registration_path: Path,
    design_dir: Path,
    run_dirs: list[Path],
    seed_gate_dirs: list[Path],
    analysis_dir: Path,
    runtime_environment_marker: Path,
    endpoint_verify_record: Path,
    output_dir: Path,
    forbidden_fragments: tuple[str, ...],
) -> dict:
    if not run_dirs or not seed_gate_dirs:
        raise ExportError("run and seed-gate directories are required")
    if len(run_dirs) != len(seed_gate_dirs):
        raise ExportError("run and seed-gate directory counts differ")
    sources_roots = [
        registration_path,
        design_dir,
        *run_dirs,
        *seed_gate_dirs,
        analysis_dir,
        runtime_environment_marker,
        endpoint_verify_record,
    ]
    v1_export._reject_output_overlap(output_dir, sources_roots)
    for directory in [design_dir, *run_dirs, *seed_gate_dirs, analysis_dir]:
        v1_export._require_regular_directory(directory)
    sources = _source_files(
        registration_path,
        design_dir,
        run_dirs,
        seed_gate_dirs,
        analysis_dir,
    )
    v1_export._reject_output_overlap(
        output_dir, [source for source, _relative in sources]
    )
    for source, _relative in sources:
        v1_export._require_regular_file(source)
    v1_export._require_regular_file(probe.REPO / SOURCE_SNAPSHOT_LOCK_RELATIVE)
    v1_export._require_regular_file(runtime_environment_marker)
    v1_export._require_regular_file(endpoint_verify_record)

    final_output_dir = output_dir
    final_output_dir.parent.mkdir(parents=True, exist_ok=True)
    if final_output_dir.exists() or final_output_dir.is_symlink():
        raise ExportError(
            f"refusing to overwrite export directory: {final_output_dir}"
        )
    output_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{final_output_dir.name}.staging-",
            dir=final_output_dir.parent,
        )
    )

    try:
        for source, relative in sources:
            v1_export._copy_exclusive(source, output_dir / relative)
        registration_for_lock = v1_export.load_json(output_dir / "registration.json")
        _write_snapshot_lock_projection(
            output_dir / SNAPSHOT_LOCK_RELATIVE,
            registration_for_lock,
        )
        _write_expected_request_grid(
            output_dir / REQUEST_GRID_RELATIVE,
            registration_for_lock,
            v1_export.load_json(output_dir / "design" / "design.json"),
        )
        _write_runtime_projection(
            output_dir / RUNTIME_PROJECTION_RELATIVE,
            runtime_environment_marker,
            endpoint_verify_record,
            registration_for_lock,
        )
        verified = _semantic_verify(output_dir)
        public_files = sorted(path for path in output_dir.rglob("*") if path.is_file())
        v1_export._scan_public_text(public_files, forbidden_fragments)
        records = [
            {
                "path": path.relative_to(output_dir).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in public_files
        ]
        registration = verified["registration"]
        excluded_relative = _safe_registered_path(
            registration["state_pool"]["excluded_design"]
        )
        summary = verified["summary"]
        code_files = _critical_code_records()
        manifest = {
            "schema_version": EXPORT_SCHEMA,
            "study_id": registration["study_id"],
            "experiment_source_git_commit": verified["design"]["source_git_commit"],
            "analysis_source_git_commit": summary["analysis_code_provenance"][
                "source_git_commit"
            ],
            "verification_source_git_commit": _verification_commit(),
            "analysis_script_sha256": verified["analysis_script_sha256"],
            "export_script_sha256": sha256_file(Path(__file__).resolve()),
            "verifier_script_sha256": sha256_file(
                REPO / "scripts" / "opd" / "verify_trigger_incidence_artifact_v2.py"
            ),
            "independent_audit_script_sha256": sha256_file(
                REPO / "scripts" / "opd" / "audit_trigger_incidence_artifact_v2.py"
            ),
            "registration_sha256": sha256_file(output_dir / "registration.json"),
            "design_sha256": sha256_file(output_dir / "design" / "design.json"),
            "excluded_design_sha256": sha256_file(output_dir / excluded_relative),
            "snapshot_lock_file_sha256": sha256_file(
                output_dir / SNAPSHOT_LOCK_RELATIVE
            ),
            "code_files": code_files,
            "code_tree_sha256": sha256_json(code_files),
            "files": records,
            "tree_sha256": sha256_json(records),
        }
        index_path = output_dir / "artifact-index.json"
        with index_path.open("x") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        from scripts.opd.verify_trigger_incidence_artifact_v2 import verify_bundle

        verified_manifest = verify_bundle(
            output_dir,
            forbidden_fragments=forbidden_fragments,
        )
        if verified_manifest["tree_sha256"] != manifest["tree_sha256"]:
            raise ExportError("final public-artifact verification disagrees")
        from scripts.opd.audit_trigger_incidence_artifact_v2 import audit_artifact

        audited = audit_artifact(output_dir)
        if audited["artifact_tree_sha256"] != manifest["tree_sha256"]:
            raise ExportError("independent public-artifact audit disagrees")
        if final_output_dir.exists() or final_output_dir.is_symlink():
            raise ExportError(
                f"refusing to overwrite export directory: {final_output_dir}"
            )
        output_dir.rename(final_output_dir)
        return manifest
    except BaseException:
        shutil.rmtree(output_dir)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--design-dir", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, action="append", required=True)
    parser.add_argument("--seed-gate-dir", type=Path, action="append", required=True)
    parser.add_argument("--analysis-dir", type=Path, required=True)
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
        runtime_environment_marker=args.runtime_environment_marker,
        endpoint_verify_record=args.endpoint_verify_record,
        output_dir=args.out_dir,
        forbidden_fragments=tuple(dict.fromkeys((*defaults, *args.forbid))),
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
