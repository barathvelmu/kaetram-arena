#!/usr/bin/env python3
"""Independently audit a published seeded trigger-incidence replication."""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import io
import json
import math
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any


REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.opd import audit_trigger_incidence_artifact as v1_audit  # noqa: E402
from scripts.opd import canonicalize  # noqa: E402
from tool_surface import MODEL_VISIBLE_TOOL_DEFINITIONS  # noqa: E402


PUBLIC_SCHEMA = "kaetram.local-trigger-incidence-public-artifact.v2"
REGISTRATION_SCHEMA = "kaetram.local-trigger-incidence-registration.v1"
ANALYSIS_SCHEMA = "kaetram.local-trigger-incidence-analysis.v1"
RUN_SCHEMA = "kaetram.local-trigger-incidence-run.v1"
SEED_GATE_SCHEMA = "kaetram.local-trigger-incidence-seed-gate.v1"
AUDIT_SCHEMA = "kaetram.local-trigger-incidence-independent-audit.v2"
SHA256 = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
KWARG_IN_KEY = re.compile(r"<parameter=[^>\n]*=[^>\n]*>")
PYTHON_CALL = re.compile(r"<function=\w+\s*\(")
CORRUPT_CLOSE = re.compile(
    r"</(?!parameter>|function>|tool_call>|think>)[A-Za-z_]{0,12}>"
)
AuditError = v1_audit.AuditError
sha256_file = v1_audit.sha256_file
sha256_json = v1_audit.sha256_json
OUTCOME_FIELDS = v1_audit.OUTCOME_FIELDS
INDEX_KEYS = {
    "schema_version",
    "study_id",
    "experiment_source_git_commit",
    "analysis_source_git_commit",
    "verification_source_git_commit",
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
PUBLIC_ATTESTATION_EXTRAS = {
    "deployment_id",
    "runtime_environment_receipt_sha256",
    "snapshot_lock_sha256",
    "snapshot_tree_sha256",
    "tokenizer_source_revision",
}
DESIGN_KEYS = {
    "schema_version",
    "study_id",
    "registration_sha256",
    "source_log_count",
    "eligible_source_log_count",
    "personality",
    "selection_stride",
    "excluded_source_log_count",
    "excluded_source_logs_sha256",
    "states",
    "source_git_commit",
    "dirty_paths",
}
EXCLUDED_DESIGN_KEYS = DESIGN_KEYS - {
    "excluded_source_log_count",
    "excluded_source_logs_sha256",
}
DESIGN_RECEIPT_KEYS = {
    "schema_version",
    "study_id",
    "registration_sha256",
    "design_sha256",
    "state_count",
    "selected_source_tree_sha256",
    "source_git_commit",
    "dirty_paths",
}
DESIGN_STATE_KEYS = {
    "state_id",
    "personality",
    "source_log",
    "source_log_sha256",
    "messages_sha256",
    "messages",
}
SAFE_SNAPSHOT_ID = re.compile(r"[a-z0-9][a-z0-9._-]*")
EXPECTED_CONDITIONS = [
    {
        "condition_id": "python-docs_no-tools",
        "documentation": "python_docs",
        "native_tool_schema": "absent",
    },
    {
        "condition_id": "python-docs_native-tools",
        "documentation": "python_docs",
        "native_tool_schema": "present",
    },
    {
        "condition_id": "canonical-docs_no-tools",
        "documentation": "canonical_docs",
        "native_tool_schema": "absent",
    },
    {
        "condition_id": "canonical-docs_native-tools",
        "documentation": "canonical_docs",
        "native_tool_schema": "present",
    },
]
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


def _reject_json_constant(value: str) -> None:
    raise AuditError(f"non-finite JSON constant is forbidden: {value}")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise AuditError(f"duplicate JSON key is forbidden: {key}")
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
    except (json.JSONDecodeError, AuditError) as exc:
        raise AuditError(f"invalid strict JSON: {label}") from exc


def _reject_nonfinite_numbers(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise AuditError("non-finite JSON number is forbidden")
    if isinstance(value, dict):
        for child in value.values():
            _reject_nonfinite_numbers(child)
    elif isinstance(value, list):
        for child in value:
            _reject_nonfinite_numbers(child)


def load_object(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise AuditError(f"expected regular JSON file: {path}")
    try:
        value = _strict_json_loads(path.read_text(), label=str(path))
    except OSError as exc:
        raise AuditError(f"cannot read JSON file: {path}") from exc
    if not isinstance(value, dict):
        raise AuditError(f"JSON root must be an object: {path}")
    return value


def _safe_relative_path(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise AuditError("artifact path must be a nonempty string")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise AuditError(f"unsafe artifact path: {value!r}")
    if pure.as_posix() != value:
        raise AuditError(f"non-canonical artifact path: {value!r}")
    return Path(*pure.parts)


def _strict_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _verify_registration(registration: dict) -> None:
    snapshots = registration.get("snapshots")
    state_pool = registration.get("state_pool")
    sampling = registration.get("sampling")
    if (
        registration.get("schema_version") != REGISTRATION_SCHEMA
        or not isinstance(registration.get("study_id"), str)
        or not registration["study_id"]
        or not isinstance(snapshots, dict)
        or not snapshots
        or not isinstance(state_pool, dict)
        or not isinstance(sampling, dict)
        or registration.get("conditions") != EXPECTED_CONDITIONS
        or not _strict_positive_int(state_pool.get("state_count"))
        or not _strict_positive_int(sampling.get("samples_per_state_condition"))
        or not isinstance(sampling.get("base_seed"), int)
        or isinstance(sampling.get("base_seed"), bool)
    ):
        raise AuditError("registration structure is invalid")
    excluded = state_pool.get("excluded_source_logs")
    if (
        not isinstance(excluded, list)
        or not excluded
        or len(set(excluded)) != len(excluded)
    ):
        raise AuditError("registration exclusion set is invalid")
    for value in excluded:
        _safe_relative_path(value)
    _safe_relative_path(state_pool.get("excluded_design"))
    if SHA256.fullmatch(str(state_pool.get("excluded_design_sha256", ""))) is None:
        raise AuditError("registration excluded-design hash is invalid")
    for snapshot, record in snapshots.items():
        if (
            not isinstance(snapshot, str)
            or SAFE_SNAPSHOT_ID.fullmatch(snapshot) is None
            or not isinstance(record, dict)
            or set(record) != {"api_model", "checkpoint_sha256"}
            or not isinstance(record.get("api_model"), str)
            or not record["api_model"]
            or SHA256.fullmatch(str(record.get("checkpoint_sha256", ""))) is None
        ):
            raise AuditError("registration snapshot identity is invalid")


def _git_blob(commit: str, relative: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "show", f"{commit}:{relative}"],
            cwd=REPO,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AuditError(f"cannot resolve registered source blob: {relative}") from exc
    return result.stdout


def _verify_source_commits(root: Path, outer: dict) -> None:
    experiment_commit = outer["experiment_source_git_commit"]
    analysis_commit = outer["analysis_source_git_commit"]
    registration_relative = "research/experiments/local-trigger-incidence-v2.json"
    registration_blob = _git_blob(experiment_commit, registration_relative)
    if registration_blob != (root / "registration.json").read_bytes():
        raise AuditError("artifact registration differs from its frozen Git blob")
    analysis_blob = _git_blob(
        analysis_commit,
        "scripts/opd/trigger_incidence_probe_v2.py",
    )
    import hashlib

    if hashlib.sha256(analysis_blob).hexdigest() != outer["analysis_script_sha256"]:
        raise AuditError("analysis script differs from its frozen Git blob")


def _verify_snapshot_lock(
    root: Path,
    registration: dict,
    outer: dict,
) -> dict[str, str]:
    source_blob = _git_blob(
        outer["experiment_source_git_commit"],
        "research/experiments/provenance/public-hf-snapshots.lock.json",
    )
    source = _strict_json_loads(source_blob.decode(), label="frozen snapshot lock")
    if not isinstance(source, dict) or set(source) != {
        "schema_version",
        "snapshots",
        "source",
        "lock_sha256",
    } or source.get("schema_version") != "kaetram-hf-snapshot-lock-v1" or source.get(
        "source"
    ) != "https://huggingface.co":
        raise AuditError("source snapshot lock schema is invalid")
    unsigned = dict(source)
    embedded = unsigned.pop("lock_sha256")
    if SHA256.fullmatch(str(embedded)) is None or sha256_json(unsigned) != embedded:
        raise AuditError("source snapshot lock digest is invalid")
    snapshots = source.get("snapshots")
    if not isinstance(snapshots, dict):
        raise AuditError("source snapshot lock records are missing")
    checkpoints = {}
    for snapshot, registered in registration["snapshots"].items():
        record = snapshots.get(snapshot)
        files = record.get("files") if isinstance(record, dict) else None
        if not isinstance(files, list):
            raise AuditError(
                f"source snapshot lock is missing registered snapshot: {snapshot}"
            )
        paths = [item.get("path") for item in files if isinstance(item, dict)]
        if len(paths) != len(files) or len(set(paths)) != len(paths):
            raise AuditError(f"source snapshot lock file set is invalid: {snapshot}")
        weights = [
            item
            for item in files
            if item.get("path") == "model.safetensors-00001-of-00001.safetensors"
        ]
        if (
            len(weights) != 1
            or weights[0].get("sha256") != registered["checkpoint_sha256"]
            or COMMIT.fullmatch(str(record.get("revision", ""))) is None
        ):
            raise AuditError(f"source snapshot lock checkpoint mismatch: {snapshot}")
        try:
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
        except (KeyError, TypeError) as exc:
            raise AuditError(f"source snapshot tree is invalid: {snapshot}") from exc
        checkpoints[snapshot] = {
            "checkpoint_sha256": weights[0]["sha256"],
            "revision": record["revision"],
            "snapshot_tree_sha256": snapshot_tree_sha256,
        }
    base = snapshots.get("base_2b")
    if base is None:
        base = snapshots.get(next(iter(registration["snapshots"])))
    tokenizer_files = [
        item
        for item in base.get("files", [])
        if isinstance(item, dict) and item.get("path") == "tokenizer.json"
    ]
    if (
        len(tokenizer_files) != 1
        or tokenizer_files[0].get("sha256")
        != registration["endpoint_contract"].get("tokenizer_sha256")
    ):
        raise AuditError("source snapshot lock tokenizer mismatch")
    projection = {
        "schema_version": "kaetram-hf-snapshot-lock-public-projection-v1",
        "source_lock_sha256": embedded,
        "tokenizer_source_revision": base["revision"],
        "tokenizer_sha256": tokenizer_files[0]["sha256"],
        "checkpoints": checkpoints,
    }
    projection["projection_sha256"] = sha256_json(projection)
    public = load_object(
        root
        / "research/experiments/provenance/public-hf-snapshot-projection.json"
    )
    if public != projection:
        raise AuditError("public snapshot-lock projection differs from frozen source")
    return {
        "lock_sha256": embedded,
        "tokenizer_revision": base["revision"],
        "snapshot_trees": {
            snapshot: record["snapshot_tree_sha256"]
            for snapshot, record in checkpoints.items()
        },
    }


def verify_outer_inventory(root: Path) -> dict:
    if root.is_symlink() or not root.is_dir():
        raise AuditError("artifact root must be a regular directory")
    index = load_object(root / "artifact-index.json")
    if set(index) != INDEX_KEYS or index.get("schema_version") != PUBLIC_SCHEMA:
        raise AuditError("unexpected public artifact schema")
    for name in (
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
        if (
            not isinstance(index.get(name), str)
            or SHA256.fullmatch(index[name]) is None
        ):
            raise AuditError(f"invalid public manifest hash: {name}")
    for name in (
        "experiment_source_git_commit",
        "analysis_source_git_commit",
        "verification_source_git_commit",
    ):
        if (
            not isinstance(index.get(name), str)
            or COMMIT.fullmatch(index[name]) is None
        ):
            raise AuditError(f"invalid public manifest commit: {name}")
    code_files = index.get("code_files")
    if not isinstance(code_files, list) or not code_files:
        raise AuditError("public manifest has no critical-code closure")
    expected_code_files = []
    for record in code_files:
        if (
            not isinstance(record, dict)
            or set(record) != {"path", "sha256"}
            or SHA256.fullmatch(str(record.get("sha256", ""))) is None
        ):
            raise AuditError("invalid critical-code record")
        relative = _safe_relative_path(record.get("path"))
        path = REPO / relative
        if path.is_symlink() or not path.is_file():
            raise AuditError(f"critical code file is missing: {relative.as_posix()}")
        expected_code_files.append(
            {"path": relative.as_posix(), "sha256": sha256_file(path)}
        )
        git_blob = _git_blob(index["verification_source_git_commit"], relative.as_posix())
        if hashlib.sha256(git_blob).hexdigest() != record["sha256"]:
            raise AuditError(f"critical code differs from verification Git blob: {relative}")
    if (
        code_files != expected_code_files
        or [record["path"] for record in code_files] != list(CRITICAL_CODE_PATHS)
        or index.get("code_tree_sha256") != sha256_json(code_files)
    ):
        raise AuditError("critical-code closure mismatch")
    records = index.get("files")
    if not isinstance(records, list) or not records:
        raise AuditError("public artifact has no file inventory")
    seen: set[str] = set()
    normalized = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "path",
            "size_bytes",
            "sha256",
        }:
            raise AuditError("invalid public file record")
        relative = _safe_relative_path(record["path"])
        text = relative.as_posix()
        if text == "artifact-index.json" or text in seen:
            raise AuditError(f"duplicate public file record: {text}")
        seen.add(text)
        path = root / relative
        size = record["size_bytes"]
        digest = record["sha256"]
        if (
            path.is_symlink()
            or not path.is_file()
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or not isinstance(digest, str)
            or SHA256.fullmatch(digest) is None
            or path.stat().st_size != size
            or sha256_file(path) != digest
        ):
            raise AuditError(f"public file digest mismatch: {text}")
        normalized.append({"path": text, "size_bytes": size, "sha256": digest})
    if [item["path"] for item in normalized] != sorted(seen):
        raise AuditError("public file inventory is not ordered")
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual != {*seen, "artifact-index.json"}:
        raise AuditError("public artifact contains missing or unindexed files")
    if index.get("tree_sha256") != sha256_json(normalized):
        raise AuditError("public artifact tree digest mismatch")
    return index


def _load_jsonl(path: Path) -> list[dict]:
    if path.is_symlink() or not path.is_file():
        raise AuditError(f"expected regular JSONL file: {path}")
    rows = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        value = _strict_json_loads(line, label=f"{path}:{line_number}")
        if not isinstance(value, dict):
            raise AuditError(f"JSONL row is not an object at {path}:{line_number}")
        rows.append(value)
    return rows


def _verify_public_path_contract(root: Path, outer: dict, registration: dict) -> None:
    snapshots = tuple(registration["snapshots"])
    expected = {
        "registration.json",
        "design/design.json",
        "design/design.receipt.json",
        "design/expected-request-grid.jsonl",
        _safe_relative_path(registration["state_pool"]["excluded_design"]).as_posix(),
        "analysis/analysis-summary.json",
        "analysis/cells.csv",
        "analysis/contrasts.csv",
        "analysis/artifact-index.json",
        "research/experiments/provenance/public-hf-snapshot-projection.json",
        "research/experiments/provenance/local-runtime-projection.json",
    }
    for snapshot in snapshots:
        for container, names in (
            (
                "runs",
                (
                    "prelaunch.json",
                    "results.jsonl",
                    "postflight.json",
                    "completed.json",
                    "artifact-index.json",
                ),
            ),
            (
                "seed-gates",
                (
                    "preflight.json",
                    "results.jsonl",
                    "postflight.json",
                    "completed.json",
                    "artifact-index.json",
                ),
            ),
        ):
            expected.update(f"{container}/{snapshot}/{name}" for name in names)
    observed = {record["path"] for record in outer["files"]}
    if observed != expected:
        raise AuditError("public artifact path set is not canonical")
    for path in root.rglob("*"):
        if path.is_symlink() or (not path.is_file() and not path.is_dir()):
            raise AuditError("public artifact contains a non-regular filesystem node")


def _semantic_response_sha256(message: Any) -> str:
    _validate_response_message(message)
    normalized = copy.deepcopy(message)
    tool_calls = normalized.get("tool_calls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            if isinstance(call, dict):
                call.pop("id", None)
    return sha256_json(normalized)


def _validate_response_message(message: Any) -> None:
    """Reject non-API metadata that could counterfeit semantic diversity."""
    if not isinstance(message, dict):
        raise AuditError("successful row lacks a response object")
    keys = set(message)
    if not {"role", "content"} <= keys or not keys <= {
        "role",
        "content",
        "reasoning",
        "tool_calls",
    }:
        raise AuditError("response object has a non-canonical schema")
    if message["role"] != "assistant" or not isinstance(message["content"], str):
        raise AuditError("response object has invalid role or content")
    if "reasoning" in message and not isinstance(message["reasoning"], str):
        raise AuditError("response object has invalid reasoning")
    if "tool_calls" not in message:
        return
    calls = message["tool_calls"]
    if not isinstance(calls, list) or not calls:
        raise AuditError("response object has invalid tool_calls")
    for call in calls:
        if not isinstance(call, dict) or set(call) != {"function", "id", "type"}:
            raise AuditError("response object has invalid tool-call schema")
        function = call.get("function")
        if (
            call.get("type") != "function"
            or not isinstance(call.get("id"), str)
            or not call["id"]
            or not isinstance(function, dict)
            or set(function) != {"name", "arguments"}
            or not isinstance(function.get("name"), str)
            or not function["name"]
            or not isinstance(function.get("arguments"), str)
        ):
            raise AuditError("response object has invalid tool-call fields")
        arguments = _strict_json_loads(
            function["arguments"], label="response tool-call arguments"
        )
        if not isinstance(arguments, dict):
            raise AuditError("response tool-call arguments are not an object")


def _verify_internal_index(root: Path, names: tuple[str, ...]) -> dict:
    index = load_object(root / "artifact-index.json")
    records = index.get("files")
    expected = []
    for name in names:
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise AuditError(f"sealed artifact is missing: {path}")
        expected.append(
            {
                "path": name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    if records != expected or index.get("tree_sha256") != sha256_json(expected):
        raise AuditError(f"internal artifact index mismatch: {root}")
    actual = {
        path.name for path in root.iterdir() if path.is_file() or path.is_symlink()
    }
    if actual != {*names, "artifact-index.json"} or any(
        path.is_dir() for path in root.iterdir()
    ):
        raise AuditError(f"internal artifact directory is not closed: {root}")
    return index


def _verify_health(health: Any, registration: dict, snapshot: str) -> None:
    if not isinstance(health, dict) or set(health) != {"status", "attestation"}:
        raise AuditError(f"{snapshot}: endpoint health envelope is invalid")
    if health.get("status") != "ok" or not isinstance(health.get("attestation"), dict):
        raise AuditError(f"{snapshot}: endpoint health is not attested")
    attestation = health["attestation"]
    expected_snapshot = registration["snapshots"][snapshot]
    allowed = {
        "api_model",
        "checkpoint_sha256",
        *registration["endpoint_contract"].keys(),
        *PUBLIC_ATTESTATION_EXTRAS,
    }
    if set(attestation) != allowed:
        raise AuditError(f"{snapshot}: endpoint attestation field set is invalid")
    expected = {
        "api_model": expected_snapshot["api_model"],
        "checkpoint_sha256": expected_snapshot["checkpoint_sha256"],
        **registration["endpoint_contract"],
    }
    if any(attestation.get(name) != value for name, value in expected.items()):
        raise AuditError(f"{snapshot}: endpoint attestation mismatches registration")
    for name in (
        "runtime_environment_receipt_sha256",
        "snapshot_lock_sha256",
        "snapshot_tree_sha256",
    ):
        if SHA256.fullmatch(str(attestation.get(name, ""))) is None:
            raise AuditError(f"{snapshot}: invalid endpoint attestation hash: {name}")
    if (
        COMMIT.fullmatch(str(attestation.get("tokenizer_source_revision", ""))) is None
        or not isinstance(attestation.get("deployment_id"), str)
        or not attestation["deployment_id"]
    ):
        raise AuditError(f"{snapshot}: invalid endpoint deployment identity")


def _verify_runtime_projection(root: Path, registration: dict) -> dict:
    projection = load_object(
        root / "research/experiments/provenance/local-runtime-projection.json"
    )
    expected_keys = {
        "schema_version",
        "runtime_environment_receipt",
        "runtime_environment_receipt_sha256",
        "render_contract",
        "render_contract_sha256",
        "sampling_contract_sha256",
        "projection_sha256",
    }
    if set(projection) != expected_keys:
        raise AuditError("runtime projection field set is invalid")
    unsigned = dict(projection)
    embedded_projection = unsigned.pop("projection_sha256")
    receipt = projection.get("runtime_environment_receipt")
    render = projection.get("render_contract")
    if (
        projection.get("schema_version")
        != "kaetram.local-runtime-public-projection.v1"
        or embedded_projection != sha256_json(unsigned)
        or not isinstance(receipt, dict)
        or set(receipt)
        != {"schema_version", "environment_kind", "marker_sha256", "marker"}
        or receipt.get("schema_version")
        != "kaetram.pinned-python-environment-receipt.v1"
        or receipt.get("environment_kind") != "local_mlx"
        or not isinstance(receipt.get("marker"), dict)
        or receipt["marker"].get("schema_version")
        != "kaetram.local-mlx-environment.v3"
        or receipt.get("marker_sha256") != sha256_json(receipt["marker"])
        or projection.get("runtime_environment_receipt_sha256")
        != sha256_json(receipt)
        or not isinstance(render, dict)
        or projection.get("render_contract_sha256") != sha256_json(render)
        or not isinstance(render.get("seeded_sampling"), dict)
        or projection.get("sampling_contract_sha256")
        != sha256_json(render["seeded_sampling"])
        or registration["endpoint_contract"].get("render_contract_sha256")
        != projection.get("render_contract_sha256")
        or registration["endpoint_contract"].get("sampling_contract_sha256")
        != projection.get("sampling_contract_sha256")
    ):
        raise AuditError("runtime projection does not verify")
    if any(
        isinstance(value, str) and value.startswith("/")
        for value in receipt["marker"].values()
    ):
        raise AuditError("runtime projection contains an absolute path")
    return projection


def _validate_design_messages(messages: Any) -> None:
    if not isinstance(messages, list) or not messages:
        raise AuditError("design state messages are missing")
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("role"), str):
            raise AuditError("design message is not a chat object")
        role = message["role"]
        if role in {"system", "user"}:
            valid = set(message) == {"role", "content"}
        elif role == "tool":
            valid = set(message) == {"role", "name", "content"} and isinstance(
                message.get("name"), str
            )
        elif role == "assistant":
            valid = {"role", "content"} <= set(message) <= {
                "role",
                "content",
                "tool_calls",
            }
            if valid and "tool_calls" in message:
                calls = message["tool_calls"]
                valid = isinstance(calls, list) and bool(calls)
                for call in calls if isinstance(calls, list) else []:
                    function = call.get("function") if isinstance(call, dict) else None
                    valid = valid and (
                        isinstance(call, dict)
                        and set(call) == {"function", "id", "type"}
                        and call.get("type") == "function"
                        and isinstance(call.get("id"), str)
                        and bool(call["id"])
                        and isinstance(function, dict)
                        and set(function) == {"name", "arguments"}
                        and isinstance(function.get("name"), str)
                        and bool(function["name"])
                        and isinstance(function.get("arguments"), dict)
                    )
        else:
            valid = False
        if not valid or not isinstance(message.get("content"), str):
            raise AuditError("design message has a non-canonical schema")


def _expected_request_grid(registration: dict, design: dict) -> list[dict]:
    records = []
    sampling = registration["sampling"]
    sample_count = int(sampling["samples_per_state_condition"])
    tools_sha256 = sha256_json(MODEL_VISIBLE_TOOL_DEFINITIONS)
    for snapshot, snapshot_contract in registration["snapshots"].items():
        schedule_index = 0
        for state_index, state in enumerate(design["states"]):
            for sample_index in range(sample_count):
                conditions = registration["conditions"]
                offset = (state_index * sample_count + sample_index) % len(conditions)
                for condition in conditions[offset:] + conditions[:offset]:
                    seed = int(sampling["base_seed"]) + 100 * state_index + sample_index
                    messages = copy.deepcopy(state["messages"])
                    documentation = condition["documentation"]
                    if documentation == "canonical_docs":
                        for message in messages:
                            if message["role"] == "system":
                                message["content"] = canonicalize.docify_system_prompt(
                                    message["content"]
                                )
                    elif documentation != "python_docs":
                        raise AuditError("unknown documentation condition")
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
                        payload["tools"] = MODEL_VISIBLE_TOOL_DEFINITIONS
                        current_tools_sha256 = tools_sha256
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


def _verify_expected_request_grid(root: Path, registration: dict, design: dict) -> None:
    observed = _load_jsonl(root / "design" / "expected-request-grid.jsonl")
    if observed != _expected_request_grid(registration, design):
        raise AuditError("expected request grid differs from registered payloads")


def _verify_design(root: Path, registration: dict) -> dict:
    registration_path = root / "registration.json"
    design_path = root / "design" / "design.json"
    design = load_object(design_path)
    receipt = load_object(root / "design" / "design.receipt.json")
    registration_sha = sha256_file(registration_path)
    states = design.get("states")
    if not isinstance(states, list) or not states:
        raise AuditError("design states are missing")
    excluded_relative = _safe_relative_path(
        registration.get("state_pool", {}).get("excluded_design")
    )
    excluded_path = root / excluded_relative
    excluded = load_object(excluded_path)
    excluded_states = excluded.get("states")
    excluded_paths = (
        [
            state.get("source_log")
            for state in excluded_states
            if isinstance(state, dict)
        ]
        if isinstance(excluded_states, list)
        else []
    )
    registered_excluded = registration["state_pool"].get("excluded_source_logs")
    selected_paths = [state.get("source_log") for state in states]
    state_count = registration["state_pool"].get("state_count")
    integer_counts = (
        design.get("source_log_count"),
        design.get("eligible_source_log_count"),
        design.get("selection_stride"),
        design.get("excluded_source_log_count"),
        receipt.get("state_count"),
    )
    if (
        set(design) != DESIGN_KEYS
        or set(receipt) != DESIGN_RECEIPT_KEYS
        or design.get("schema_version") != "kaetram.local-trigger-incidence-design.v1"
        or design.get("study_id") != registration.get("study_id")
        or design.get("registration_sha256") != registration_sha
        or receipt.get("schema_version")
        != "kaetram.local-trigger-incidence-design.v1.receipt"
        or receipt.get("study_id") != registration.get("study_id")
        or receipt.get("registration_sha256") != registration_sha
        or receipt.get("design_sha256") != sha256_file(design_path)
        or receipt.get("state_count") != len(states)
        or receipt.get("source_git_commit") != design.get("source_git_commit")
        or receipt.get("dirty_paths") != []
        or design.get("dirty_paths") != []
        or COMMIT.fullmatch(str(design.get("source_git_commit", ""))) is None
        or sha256_file(excluded_path)
        != registration["state_pool"].get("excluded_design_sha256")
        or excluded_paths != registered_excluded
        or len(set(registered_excluded or [])) != len(registered_excluded or [])
        or set(selected_paths).intersection(registered_excluded or [])
        or design.get("excluded_source_log_count") != len(registered_excluded or [])
        or design.get("excluded_source_logs_sha256")
        != sha256_json(sorted(registered_excluded or []))
        or design.get("personality") != registration["state_pool"].get("personality")
        or len(states) != state_count
        or not all(_strict_positive_int(value) for value in integer_counts)
        or design["source_log_count"] < design["eligible_source_log_count"]
        or design["eligible_source_log_count"] < state_count
        or design["selection_stride"]
        != max(1, design["eligible_source_log_count"] // (2 * state_count))
        or len(selected_paths) != len(set(selected_paths))
        or not isinstance(excluded, dict)
        or set(excluded) != EXCLUDED_DESIGN_KEYS
        or excluded.get("schema_version")
        != "kaetram.local-trigger-incidence-design.v1"
        or not isinstance(excluded_states, list)
        or len(excluded_states) != len(registered_excluded or [])
    ):
        raise AuditError("design/exclusion binding is invalid")
    selected_records = [
        {
            "state_id": state["state_id"],
            "personality": state["personality"],
            "source_log": state["source_log"],
            "source_log_sha256": state["source_log_sha256"],
            "messages_sha256": state["messages_sha256"],
        }
        for state in states
    ]
    if receipt.get("selected_source_tree_sha256") != sha256_json(selected_records):
        raise AuditError("design receipt source-tree hash is invalid")
    for state_index, state in enumerate(states):
        if not isinstance(state, dict) or set(state) != DESIGN_STATE_KEYS:
            raise AuditError("design state field set is invalid")
        source = _safe_relative_path(state.get("source_log"))
        if (
            state.get("state_id") != f"state-{state_index + 1:02d}"
            or state.get("personality") != registration["state_pool"].get("personality")
            or not source.parts
            or not isinstance(state.get("source_log_sha256"), str)
            or SHA256.fullmatch(state["source_log_sha256"]) is None
            or not isinstance(state.get("messages"), list)
            or state.get("messages_sha256") != sha256_json(state.get("messages"))
        ):
            raise AuditError("design state message hash is invalid")
        _validate_design_messages(state["messages"])
    for state in excluded_states:
        if not isinstance(state, dict) or set(state) != DESIGN_STATE_KEYS:
            raise AuditError("excluded design state field set is invalid")
        _safe_relative_path(state.get("source_log"))
        if (
            SHA256.fullmatch(str(state.get("source_log_sha256", ""))) is None
            or not isinstance(state.get("messages"), list)
            or state.get("messages_sha256") != sha256_json(state.get("messages"))
        ):
            raise AuditError("excluded design state binding is invalid")
        _validate_design_messages(state["messages"])
    return design


def _expected_completed(registration: dict, snapshot: str, rows: list[dict]) -> dict:
    return {
        "schema_version": f"{RUN_SCHEMA}.completed",
        "study_id": registration["study_id"],
        "snapshot": snapshot,
        "scheduled_requests": len(rows),
        "successful_requests": sum(row.get("status") == "ok" for row in rows),
        "failed_requests": sum(row.get("status") != "ok" for row in rows),
        "recovery_opportunities": sum(
            bool(row.get("recovery_opportunity")) for row in rows
        ),
        "malformed_emissions": sum(bool(row.get("malformed_emission")) for row in rows),
        "structured_tool_responses": sum(
            bool(row.get("has_structured_tool_call")) for row in rows
        ),
        "no_structured_tool_call_responses": sum(
            bool(row.get("no_structured_tool_call")) for row in rows
        ),
        "endpoint_identity_stable": True,
    }


def _verify_run(
    root: Path,
    registration: dict,
    registration_sha: str,
    design: dict,
    design_sha: str,
    snapshot: str,
) -> dict:
    index = _verify_internal_index(
        root,
        ("prelaunch.json", "results.jsonl", "postflight.json", "completed.json"),
    )
    prelaunch = load_object(root / "prelaunch.json")
    postflight = load_object(root / "postflight.json")
    completed = load_object(root / "completed.json")
    rows = _load_jsonl(root / "results.jsonl")
    expected_prelaunch_keys = {
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
    expected_postflight_keys = {
        "schema_version",
        "study_id",
        "snapshot",
        "endpoint_identity_stable",
        "endpoint_health",
        "error",
    }
    common_row_keys = {
        "schema_version",
        "snapshot",
        "schedule_index",
        "state_id",
        "state_index",
        "sample_index",
        "seed",
        "condition_id",
        "documentation",
        "native_tool_schema",
        "latency_seconds",
        "attempt_errors",
        "status",
    }
    for row in rows:
        expected_row_keys = (
            common_row_keys | OUTCOME_FIELDS
            if row.get("status") == "ok"
            else common_row_keys
        )
        if (
            set(row) != expected_row_keys
            or row.get("schema_version") != RUN_SCHEMA
            or row.get("status") not in {"ok", "failed"}
            or not isinstance(row.get("attempt_errors"), list)
            or isinstance(row.get("latency_seconds"), bool)
            or not isinstance(row.get("latency_seconds"), (int, float))
            or not math.isfinite(row["latency_seconds"])
            or row["latency_seconds"] < 0
        ):
            raise AuditError(f"{snapshot}: invalid run result envelope")
    expected_completed = _expected_completed(registration, snapshot, rows)
    if (
        set(index)
        != {
            "schema_version",
            "study_id",
            "snapshot",
            "files",
            "tree_sha256",
        }
        or index.get("schema_version") != f"{RUN_SCHEMA}.artifacts"
        or index.get("study_id") != registration["study_id"]
        or index.get("snapshot") != snapshot
        or set(prelaunch) != expected_prelaunch_keys
        or prelaunch.get("schema_version") != f"{RUN_SCHEMA}.prelaunch"
        or prelaunch.get("study_id") != registration["study_id"]
        or prelaunch.get("snapshot") != snapshot
        or prelaunch.get("registration_sha256") != registration_sha
        or prelaunch.get("design_sha256") != design_sha
        or prelaunch.get("sampling") != registration["sampling"]
        or prelaunch.get("source_git_commit") != design["source_git_commit"]
        or prelaunch.get("dirty_paths") != []
        or set(postflight) != expected_postflight_keys
        or postflight.get("schema_version") != f"{RUN_SCHEMA}.postflight"
        or postflight.get("study_id") != registration["study_id"]
        or postflight.get("snapshot") != snapshot
        or postflight.get("endpoint_identity_stable") is not True
        or postflight.get("endpoint_health") != prelaunch.get("endpoint_health")
        or postflight.get("error") is not None
        or completed != expected_completed
    ):
        raise AuditError(f"{snapshot}: run envelope does not verify")
    _verify_health(prelaunch["endpoint_health"], registration, snapshot)
    return {
        "artifact_index_sha256": sha256_file(root / "artifact-index.json"),
        "tree_sha256": index["tree_sha256"],
        "endpoint_health": prelaunch["endpoint_health"],
        "seed_gate_artifact_index_sha256": prelaunch["seed_gate_artifact_index_sha256"],
        "seed_gate_tree_sha256": prelaunch["seed_gate_tree_sha256"],
    }


def _verify_gate(
    root: Path,
    registration: dict,
    registration_sha: str,
    snapshot: str,
    expected_health: dict,
) -> dict:
    index = _verify_internal_index(
        root,
        ("preflight.json", "results.jsonl", "postflight.json", "completed.json"),
    )
    preflight = load_object(root / "preflight.json")
    postflight = load_object(root / "postflight.json")
    completed = load_object(root / "completed.json")
    rows = _load_jsonl(root / "results.jsonl")
    gate = registration["seed_gate"]
    distinct_count = int(gate["distinct_seed_count"])
    repeat_index = int(gate["repeat_seed_index"])
    base_seed = int(gate["base_seed"])
    expected_requests = [
        (f"seed-{index}", base_seed + index) for index in range(distinct_count)
    ]
    expected_requests.append((f"repeat-{repeat_index}", base_seed + repeat_index))
    if len(rows) != len(expected_requests):
        raise AuditError(f"{snapshot}: seed-gate row count mismatch")
    hashes = []
    for row, (request_id, seed) in zip(rows, expected_requests, strict=True):
        digest = _semantic_response_sha256(row.get("response_message"))
        if (
            set(row)
            != {
                "schema_version",
                "request_id",
                "seed",
                "status",
                "latency_seconds",
                "attempt_errors",
                "response_message",
                "semantic_response_sha256",
            }
            or row.get("schema_version") != SEED_GATE_SCHEMA
            or row.get("request_id") != request_id
            or row.get("seed") != seed
            or row.get("status") != "ok"
            or row.get("semantic_response_sha256") != digest
            or not isinstance(row.get("attempt_errors"), list)
            or isinstance(row.get("latency_seconds"), bool)
            or not isinstance(row.get("latency_seconds"), (int, float))
            or not math.isfinite(row["latency_seconds"])
            or row["latency_seconds"] < 0
        ):
            raise AuditError(f"{snapshot}: invalid seed-gate row")
        hashes.append(digest)
    unique = len(set(hashes[:distinct_count]))
    repeatable = hashes[repeat_index] == hashes[-1]
    expected_completed = {
        "schema_version": f"{SEED_GATE_SCHEMA}.completed",
        "study_id": registration["study_id"],
        "snapshot": snapshot,
        "scheduled_requests": len(rows),
        "successful_requests": len(rows),
        "unique_semantic_responses": unique,
        "minimum_unique_semantic_responses": int(
            gate["minimum_unique_semantic_responses"]
        ),
        "repeated_seed_reproducible": repeatable,
        "endpoint_identity_stable": True,
        "passed": (
            repeatable and unique >= int(gate["minimum_unique_semantic_responses"])
        ),
    }
    if (
        set(index)
        != {
            "schema_version",
            "study_id",
            "snapshot",
            "files",
            "tree_sha256",
        }
        or index.get("schema_version") != f"{SEED_GATE_SCHEMA}.artifacts"
        or index.get("study_id") != registration["study_id"]
        or index.get("snapshot") != snapshot
        or set(preflight)
        != {
            "schema_version",
            "study_id",
            "snapshot",
            "registration_sha256",
            "endpoint_health",
            "seed_gate",
            "source_git_commit",
            "dirty_paths",
        }
        or preflight.get("schema_version") != f"{SEED_GATE_SCHEMA}.preflight"
        or preflight.get("study_id") != registration["study_id"]
        or preflight.get("snapshot") != snapshot
        or preflight.get("registration_sha256") != registration_sha
        or preflight.get("seed_gate") != gate
        or preflight.get("endpoint_health") != expected_health
        or preflight.get("dirty_paths") != []
        or COMMIT.fullmatch(str(preflight.get("source_git_commit", ""))) is None
        or set(postflight)
        != {
            "schema_version",
            "study_id",
            "snapshot",
            "endpoint_identity_stable",
            "endpoint_health",
            "error",
        }
        or postflight.get("schema_version") != f"{SEED_GATE_SCHEMA}.postflight"
        or postflight.get("study_id") != registration["study_id"]
        or postflight.get("snapshot") != snapshot
        or postflight.get("endpoint_health") != expected_health
        or postflight.get("endpoint_identity_stable") is not True
        or postflight.get("error") is not None
        or completed != expected_completed
        or expected_completed["passed"] is not True
    ):
        raise AuditError(f"{snapshot}: seed gate does not independently verify")
    _verify_health(expected_health, registration, snapshot)
    return {
        "artifact_index_sha256": sha256_file(root / "artifact-index.json"),
        "tree_sha256": index["tree_sha256"],
        "source_git_commit": preflight["source_git_commit"],
        "unique_semantic_responses": unique,
    }


def _recompute_seed_heterogeneity(
    registration: dict,
    rows: dict[tuple, dict],
) -> dict:
    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in rows.values():
        grouped[(row["snapshot"], row["condition_id"], row["state_id"])].append(row)
    records = []
    sample_count = int(registration["sampling"]["samples_per_state_condition"])
    for key, members in sorted(grouped.items()):
        if len(members) != sample_count or any(
            member["status"] != "ok" for member in members
        ):
            raise AuditError(f"incomplete seed-heterogeneity group: {key}")
        records.append(
            {
                "unique_semantic_responses": len(
                    {
                        _semantic_response_sha256(member["response_message"])
                        for member in members
                    }
                ),
                "primary_outcome_values": len(
                    {bool(member["recovery_opportunity"]) for member in members}
                ),
            }
        )
    expected = (
        len(registration["snapshots"])
        * len(registration["conditions"])
        * int(registration["state_pool"]["state_count"])
    )
    if len(records) != expected:
        raise AuditError("seed-heterogeneity groups do not cover the fixed grid")
    return {
        "status": "complete",
        "state_condition_groups": len(records),
        "groups_with_multiple_semantic_responses": sum(
            record["unique_semantic_responses"] > 1 for record in records
        ),
        "groups_with_primary_outcome_heterogeneity": sum(
            record["primary_outcome_values"] > 1 for record in records
        ),
        "minimum_unique_semantic_responses_per_group": min(
            record["unique_semantic_responses"] for record in records
        ),
        "maximum_unique_semantic_responses_per_group": max(
            record["unique_semantic_responses"] for record in records
        ),
    }


def _verify_detailed_outcomes(rows: dict[tuple, dict]) -> None:
    for key, row in rows.items():
        if row["status"] != "ok":
            continue
        message = row["response_message"]
        _validate_response_message(message)
        content = message.get("content") or ""
        if not isinstance(content, str):
            content = ""
        families = []
        if KWARG_IN_KEY.search(content):
            families.append("kwarg_in_key")
        if PYTHON_CALL.search(content):
            families.append("python_call")
        if "<tool_call>" in content and CORRUPT_CLOSE.search(content):
            families.append("corrupt_close")
        if not families and v1_audit.MALFORMED.search(content):
            families.append("other_malformed")
        tool_calls = message.get("tool_calls") or []
        expected_calls = canonicalize.recover_tool_calls(content) if not tool_calls else []
        calls = row.get("recoverable_calls")
        if (
            row.get("malformed_families") != families
            or not isinstance(calls, list)
            or calls != expected_calls
        ):
            raise AuditError(f"stored detailed outcome mismatch: {key}")
        if bool(calls) != bool(row.get("recovery_opportunity")):
            raise AuditError(f"stored recovery-call detail mismatch: {key}")
        for call in calls:
            if (
                not isinstance(call, dict)
                or set(call) != {"name", "args"}
                or call.get("name") not in v1_audit.TOOL_PARAMETER_ORDER
                or not isinstance(call.get("args"), dict)
            ):
                raise AuditError(f"invalid recovered call detail: {key}")


def _csv_bytes(records: list[dict]) -> bytes:
    if not records:
        return b""
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=list(records[0]))
    writer.writeheader()
    writer.writerows(records)
    return handle.getvalue().encode()


def _verify_analysis(
    root: Path,
    outer: dict,
    registration: dict,
    registration_sha: str,
    design: dict,
    design_sha: str,
    run_identities: dict[str, dict],
    recomputed: dict,
    heterogeneity: dict,
    directional: dict,
) -> dict:
    analysis_root = root / "analysis"
    index = _verify_internal_index(
        analysis_root,
        ("analysis-summary.json", "cells.csv", "contrasts.csv"),
    )
    analysis = load_object(analysis_root / "analysis-summary.json")
    expected_keys = {
        "schema_version",
        "study_id",
        "registration_sha256",
        "design_sha256",
        "analysis_code_provenance",
        "input_runs",
        "analysis_status",
        "scheduled_requests",
        "successful_requests",
        "failed_requests",
        "recovery_opportunities",
        "claim_boundary",
        "cells",
        "registered_contrasts",
        "registered_seed_heterogeneity",
        "directional_replication",
    }
    provenance = analysis.get("analysis_code_provenance")
    expected_inputs = sorted(
        (
            {
                "snapshot": snapshot,
                "artifact_index_sha256": identity["artifact_index_sha256"],
                "tree_sha256": identity["tree_sha256"],
            }
            for snapshot, identity in run_identities.items()
        ),
        key=lambda item: item["snapshot"],
    )
    if (
        set(index) != {"schema_version", "files", "tree_sha256"}
        or index.get("schema_version") != f"{ANALYSIS_SCHEMA}.artifacts"
        or set(analysis) != expected_keys
        or analysis.get("schema_version") != ANALYSIS_SCHEMA
        or analysis.get("study_id") != registration["study_id"]
        or analysis.get("registration_sha256") != registration_sha
        or analysis.get("design_sha256") != design_sha
        or analysis.get("claim_boundary") != registration["claim_boundary"]
        or analysis.get("input_runs") != expected_inputs
        or not isinstance(provenance, dict)
        or set(provenance)
        != {
            "source_git_commit",
            "dirty_paths",
            "analysis_script_sha256",
            "python_version",
        }
        or provenance.get("source_git_commit") != design["source_git_commit"]
        or provenance.get("dirty_paths") != []
        or provenance.get("analysis_script_sha256")
        != sha256_file(REPO / "scripts" / "opd" / "trigger_incidence_probe_v2.py")
        or re.fullmatch(r"\d+\.\d+\.\d+", str(provenance.get("python_version", "")))
        is None
    ):
        raise AuditError("analysis identity or provenance is invalid")
    for field, value in recomputed.items():
        if analysis.get(field) != value:
            raise AuditError(f"independent analysis mismatch: {field}")
    if analysis.get("registered_seed_heterogeneity") != heterogeneity:
        raise AuditError("independent seed-heterogeneity analysis mismatch")
    if analysis.get("directional_replication") != directional:
        raise AuditError("independent directional-replication analysis mismatch")
    if (analysis_root / "cells.csv").read_bytes() != _csv_bytes(recomputed["cells"]):
        raise AuditError("cells.csv does not match independent reanalysis")
    if (analysis_root / "contrasts.csv").read_bytes() != _csv_bytes(
        recomputed["registered_contrasts"]
    ):
        raise AuditError("contrasts.csv does not match independent reanalysis")
    if (
        outer.get("analysis_source_git_commit") != provenance["source_git_commit"]
        or outer.get("analysis_script_sha256") != provenance["analysis_script_sha256"]
    ):
        raise AuditError("public manifest does not bind the analysis implementation")
    return analysis


def audit_artifact(
    root: Path,
    *,
    expected_index_sha256: str | None = None,
) -> dict:
    actual_index_sha256 = sha256_file(root / "artifact-index.json")
    if expected_index_sha256 is not None:
        if SHA256.fullmatch(expected_index_sha256) is None:
            raise AuditError("expected artifact-index hash is invalid")
        if actual_index_sha256 != expected_index_sha256:
            raise AuditError("artifact-index hash differs from trust root")
    outer = verify_outer_inventory(root)
    registration = load_object(root / "registration.json")
    registration_sha = sha256_file(root / "registration.json")
    _verify_registration(registration)
    _verify_source_commits(root, outer)
    lock_identity = _verify_snapshot_lock(root, registration, outer)
    runtime_identity = _verify_runtime_projection(root, registration)
    _verify_public_path_contract(root, outer, registration)
    design = _verify_design(root, registration)
    _verify_expected_request_grid(root, registration, design)
    design_sha = sha256_file(root / "design" / "design.json")
    snapshots = tuple(registration["snapshots"])
    for container in ("runs", "seed-gates"):
        names = {
            path.name
            for path in (root / container).iterdir()
            if path.is_dir() or path.is_symlink()
        }
        if names != set(snapshots):
            raise AuditError(f"{container} directory set is not canonical")

    gates = {}
    run_identities = {}
    for snapshot in snapshots:
        run_dir = root / "runs" / snapshot
        run = _verify_run(
            run_dir,
            registration,
            registration_sha,
            design,
            design_sha,
            snapshot,
        )
        gate = _verify_gate(
            root / "seed-gates" / snapshot,
            registration,
            registration_sha,
            snapshot,
            run["endpoint_health"],
        )
        if (
            run["seed_gate_artifact_index_sha256"] != gate["artifact_index_sha256"]
            or run["seed_gate_tree_sha256"] != gate["tree_sha256"]
            or gate["source_git_commit"] != design["source_git_commit"]
        ):
            raise AuditError(f"{snapshot}: run/gate/design binding is invalid")
        gates[snapshot] = gate
        run_identities[snapshot] = run

    for snapshot, identity in run_identities.items():
        attestation = identity["endpoint_health"]["attestation"]
        if (
            attestation["snapshot_lock_sha256"] != lock_identity["lock_sha256"]
            or attestation["tokenizer_source_revision"]
            != lock_identity["tokenizer_revision"]
            or attestation["snapshot_tree_sha256"]
            != lock_identity["snapshot_trees"][snapshot]
            or attestation["runtime_environment_receipt_sha256"]
            != runtime_identity["runtime_environment_receipt_sha256"]
            or attestation["render_contract_sha256"]
            != runtime_identity["render_contract_sha256"]
            or attestation["sampling_contract_sha256"]
            != runtime_identity["sampling_contract_sha256"]
        ):
            raise AuditError(f"{snapshot}: endpoint identity differs from snapshot lock")

    rows = v1_audit._load_and_check_rows(root, registration, design)
    _verify_detailed_outcomes(rows)
    recomputed = v1_audit.recompute_summary(registration, design, rows)
    heterogeneity = _recompute_seed_heterogeneity(registration, rows)
    native_effects = {
        item["snapshot"]: item["effect_rate_difference"]
        for item in recomputed["registered_contrasts"]
        if item["contrast"] == "native_tools_main"
    }
    directional = {
        "criterion": registration["analysis"]["directional_replication_criterion"],
        "status": "evaluated",
        "native_tools_effects": native_effects,
        "passed": all(native_effects.get(snapshot, 0) > 0 for snapshot in snapshots),
    }
    _verify_analysis(
        root,
        outer,
        registration,
        registration_sha,
        design,
        design_sha,
        run_identities,
        recomputed,
        heterogeneity,
        directional,
    )

    excluded_relative = _safe_relative_path(
        registration["state_pool"]["excluded_design"]
    )
    expected_outer_bindings = {
        "study_id": registration["study_id"],
        "experiment_source_git_commit": design["source_git_commit"],
        "registration_sha256": registration_sha,
        "design_sha256": design_sha,
        "excluded_design_sha256": sha256_file(root / excluded_relative),
        "snapshot_lock_file_sha256": sha256_file(
            root
            / "research/experiments/provenance/public-hf-snapshot-projection.json"
        ),
        "code_files": outer["code_files"],
        "code_tree_sha256": outer["code_tree_sha256"],
        "export_script_sha256": sha256_file(
            REPO / "scripts" / "opd" / "export_trigger_incidence_artifact_v2.py"
        ),
        "verifier_script_sha256": sha256_file(
            REPO / "scripts" / "opd" / "verify_trigger_incidence_artifact_v2.py"
        ),
        "independent_audit_script_sha256": sha256_file(Path(__file__).resolve()),
    }
    for field, value in expected_outer_bindings.items():
        if outer.get(field) != value:
            raise AuditError(f"public manifest binding mismatch: {field}")

    return {
        "schema_version": AUDIT_SCHEMA,
        "study_id": registration["study_id"],
        "artifact_index_sha256": actual_index_sha256,
        "artifact_tree_sha256": outer["tree_sha256"],
        "audit_script_sha256": sha256_file(Path(__file__).resolve()),
        "scheduled_requests": recomputed["scheduled_requests"],
        "successful_requests": recomputed["successful_requests"],
        "failed_requests": recomputed["failed_requests"],
        "recovery_opportunities": recomputed["recovery_opportunities"],
        "seed_gate_unique_semantic_responses": {
            snapshot: gates[snapshot]["unique_semantic_responses"]
            for snapshot in snapshots
        },
        "groups_with_multiple_semantic_responses": heterogeneity[
            "groups_with_multiple_semantic_responses"
        ],
        "groups_with_primary_outcome_heterogeneity": heterogeneity[
            "groups_with_primary_outcome_heterogeneity"
        ],
        "native_tools_effects": native_effects,
        "directional_replication_passed": directional["passed"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--expected-index-sha256")
    args = parser.parse_args(argv)
    print(
        json.dumps(
            audit_artifact(
                args.artifact_dir,
                expected_index_sha256=args.expected_index_sha256,
            ),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
