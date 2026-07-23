"""Recursive validation for OPD builder and transformation receipts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

try:
    from .opd_data_manifest import BUILDER_RELATIVE_PATH, MANIFEST_SCHEMA_VERSION
    from .record_schema import (
        OPD_TRAIN_RECORD_SCHEMA_SHA256,
        OPD_TRAIN_RECORD_SCHEMA_VERSION,
        OPD_TRAIN_RECORD_VALIDATOR_SHA256,
    )
except ImportError:  # direct `python scripts/opd/...` execution
    from opd_data_manifest import (  # type: ignore[no-redef]
        BUILDER_RELATIVE_PATH,
        MANIFEST_SCHEMA_VERSION,
    )
    from record_schema import (  # type: ignore[no-redef]
        OPD_TRAIN_RECORD_SCHEMA_SHA256,
        OPD_TRAIN_RECORD_SCHEMA_VERSION,
        OPD_TRAIN_RECORD_VALIDATOR_SHA256,
    )


UNIFORM_MANIFEST_SCHEMA_VERSION = "uniform-advantages-manifest-v3"
RESAMPLE_MANIFEST_SCHEMA_VERSION = "resampled-records-manifest-v3"
SUPPORTED_MANIFESTS = {
    MANIFEST_SCHEMA_VERSION,
    UNIFORM_MANIFEST_SCHEMA_VERSION,
    RESAMPLE_MANIFEST_SCHEMA_VERSION,
}
BUILD_SOURCE_PATHS = (
    "finetune/render.py",
    "scripts/opd/canonicalize.py",
    "heldout_guard.py",
    "scripts/opd/opd_2b_data.py",
    "scripts/opd/opd_data_manifest.py",
    "scripts/opd/opd_probe.py",
    "scripts/opd/opd_round1.py",
    "scripts/opd/opd_wall_probe.py",
    "scripts/opd/record_schema.py",
    "scripts/opd/receipt_chain.py",
)
SCRIPT_PATHS = {
    MANIFEST_SCHEMA_VERSION: BUILDER_RELATIVE_PATH,
    UNIFORM_MANIFEST_SCHEMA_VERSION: "scripts/opd/make_uniform_advantages.py",
    RESAMPLE_MANIFEST_SCHEMA_VERSION: "scripts/opd/resample_records.py",
}


class ReceiptChainError(ValueError):
    """Raised when a provenance receipt or its parent chain is invalid."""


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReceiptChainError(message)


def _source_path(repo_root: Path, relative: str) -> Path:
    if relative == "finetune/render.py":
        try:
            import render

            return Path(render.__file__).resolve()
        except ImportError:
            pass
    return repo_root / relative


def _verify_attestation(value: object, label: str) -> None:
    _require(isinstance(value, dict), f"{label} endpoint attestation is missing")
    _require(
        set(value) == {
            "deployment_id",
            "api_model",
            "checkpoint_sha256",
            "tokenizer_sha256",
            "render_contract_sha256",
        },
        f"{label} endpoint attestation fields are invalid",
    )
    _require(
        all(
            isinstance(value[field], str)
            and bool(value[field])
            and "://" not in value[field]
            for field in ("deployment_id", "api_model")
        )
        and all(
            is_digest(value[field])
            for field in (
                "checkpoint_sha256",
                "tokenizer_sha256",
                "render_contract_sha256",
            )
        ),
        f"{label} endpoint attestation values are invalid",
    )


def _verify_root(receipt: dict, repo_root: Path) -> None:
    _require(
        receipt.get("builder") == BUILDER_RELATIVE_PATH,
        "builder receipt has the wrong builder",
    )
    inventory = receipt.get("source_logs")
    _require(isinstance(inventory, list) and bool(inventory), "source_logs are empty")
    paths: list[str] = []
    run_ids: set[str] = set()
    for item in inventory:
        _require(isinstance(item, dict), "source log entry is not an object")
        path = item.get("path")
        run_id = item.get("run_id")
        _require(
            isinstance(path, str)
            and bool(path)
            and not PurePosixPath(path).is_absolute()
            and ".." not in PurePosixPath(path).parts
            and isinstance(run_id, str)
            and bool(run_id)
            and is_digest(item.get("sha256"))
            and isinstance(item.get("size_bytes"), int)
            and not isinstance(item.get("size_bytes"), bool)
            and item["size_bytes"] >= 0,
            "source log entry is invalid",
        )
        paths.append(path)
        run_ids.add(run_id)
    _require(paths == sorted(paths) and len(paths) == len(set(paths)), "source logs not unique/sorted")
    declared_runs = receipt.get("source_runs")
    _require(
        isinstance(declared_runs, list)
        and bool(declared_runs)
        and len(declared_runs) == len(set(declared_runs))
        and set(declared_runs) == run_ids,
        "source run coverage does not match the source-log inventory",
    )
    _require(
        receipt.get("source_sha256") == canonical_sha256(inventory),
        "source-log inventory digest mismatch",
    )
    _require(
        isinstance(receipt.get("n_records"), int)
        and receipt["n_records"] > 0
        and isinstance(receipt.get("n_heldout"), int)
        and receipt["n_heldout"] >= 0
        and is_digest(receipt.get("heldout_sha256")),
        "builder record/heldout counts are invalid",
    )
    build_sources = receipt.get("build_sources")
    _require(
        isinstance(build_sources, dict)
        and set(build_sources) == set(BUILD_SOURCE_PATHS),
        "builder source inventory is incomplete",
    )
    for relative in BUILD_SOURCE_PATHS:
        path = _source_path(repo_root, relative)
        _require(
            path.is_file() and build_sources[relative] == sha256_path(path),
            f"builder source identity mismatch: {relative}",
        )
    parameters = receipt.get("parameters")
    _require(isinstance(parameters, dict), "builder parameters are missing")
    _require(
        set(parameters) == {
            "student_endpoint_attestation",
            "teacher_endpoint_attestation",
            "tokenizer_sha256",
            "tokenizer_snapshot_sha256",
            "runtime_versions",
            "max_history_messages",
            "max_sequence_tokens",
            "kl_coefficient",
            "holdout_every",
            "early_weight",
            "malformed_parameter_pattern",
            "counterfactual_grading",
            "limit",
        },
        "builder parameter fields are invalid",
    )
    _verify_attestation(parameters.get("student_endpoint_attestation"), "student")
    _verify_attestation(parameters.get("teacher_endpoint_attestation"), "teacher")
    tokenizer_sha = parameters.get("tokenizer_sha256")
    _require(
        is_digest(tokenizer_sha)
        and is_digest(parameters.get("tokenizer_snapshot_sha256"))
        and parameters["student_endpoint_attestation"]["tokenizer_sha256"] == tokenizer_sha
        and parameters["teacher_endpoint_attestation"]["tokenizer_sha256"] == tokenizer_sha
        and parameters.get("max_history_messages") == 28
        and parameters.get("max_sequence_tokens") == 16384
        and parameters.get("kl_coefficient") == 1.0
        and parameters.get("holdout_every") == 10
        and parameters.get("early_weight") == 1.5
        and parameters.get("malformed_parameter_pattern")
        == r"<parameter=[^>\n]*=[^>\n]*>"
        and parameters.get("counterfactual_grading") is True
        and parameters.get("limit") == 0,
        "builder parameter values are invalid",
    )
    runtime_versions = parameters.get("runtime_versions")
    _require(
        isinstance(runtime_versions, dict)
        and set(runtime_versions) == {"python", "httpx", "transformers", "tokenizers"}
        and all(isinstance(value, str) and bool(value) for value in runtime_versions.values()),
        "builder runtime-version receipt is invalid",
    )


def validate_receipt_chain(
    receipt: object,
    *,
    expected_output_sha256: str,
    repo_root: Path,
) -> dict:
    """Validate one receipt and recursively validate every embedded parent."""
    _require(isinstance(receipt, dict), "receipt must be a JSON object")
    schema = receipt.get("schema_version")
    _require(schema in SUPPORTED_MANIFESTS, f"unsupported records manifest: {schema!r}")
    _require(
        receipt.get("output_sha256") == expected_output_sha256
        and is_digest(expected_output_sha256),
        "receipt output SHA-256 mismatch",
    )
    _require(
        receipt.get("record_schema_version") == OPD_TRAIN_RECORD_SCHEMA_VERSION
        and receipt.get("record_schema_sha256") == OPD_TRAIN_RECORD_SCHEMA_SHA256
        and receipt.get("record_schema_validator_sha256")
        == OPD_TRAIN_RECORD_VALIDATOR_SHA256,
        "record schema/validator identity mismatch",
    )
    script_path = _source_path(repo_root, SCRIPT_PATHS[schema])
    _require(
        script_path.is_file() and receipt.get("script_sha256") == sha256_path(script_path),
        "receipt producer identity mismatch",
    )
    if schema == MANIFEST_SCHEMA_VERSION:
        _verify_root(receipt, repo_root)
    else:
        parent = receipt.get("parent_manifest")
        _require(isinstance(parent, dict), "transformation parent manifest is missing")
        _require(
            receipt.get("parent_manifest_sha256") == canonical_sha256(parent),
            "transformation parent-manifest digest mismatch",
        )
        source_sha = receipt.get("source_sha256")
        _require(is_digest(source_sha), "transformation source SHA-256 is invalid")
        validate_receipt_chain(
            parent,
            expected_output_sha256=source_sha,
            repo_root=repo_root,
        )
    return receipt
