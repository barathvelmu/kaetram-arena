"""Fail-closed verification of trainer-facing OPD records and their receipt."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from pathlib import PurePosixPath

from . import make_uniform_advantages, opd_data_manifest, resample_records
from .record_schema import (
    OPD_TRAIN_RECORD_SCHEMA_SHA256,
    OPD_TRAIN_RECORD_SCHEMA_VERSION,
    OPD_TRAIN_RECORD_VALIDATOR_SHA256,
    RecordSchemaError,
    validate_opd_train_record,
)


SUPPORTED_MANIFESTS = {
    make_uniform_advantages.MANIFEST_SCHEMA_VERSION,
    opd_data_manifest.MANIFEST_SCHEMA_VERSION,
    resample_records.MANIFEST_SCHEMA_VERSION,
}


class TrainingRecordBundleError(ValueError):
    """Raised when records are not byte- and schema-bound to their receipt."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _positive_int(value: object) -> bool:
    return _nonnegative_int(value) and value > 0


def _digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise TrainingRecordBundleError(message)


def _load_records(path: Path) -> list[dict]:
    records: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            _require(bool(line.strip()), f"blank JSONL record at line {line_number}")
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise TrainingRecordBundleError(
                    f"invalid JSON at record {line_number}: {exc}"
                ) from exc
            try:
                records.append(validate_opd_train_record(value, line_number=line_number))
            except RecordSchemaError as exc:
                raise TrainingRecordBundleError(str(exc)) from exc
    _require(bool(records), "records file contains no records")
    return records


def _verify_uniform(manifest: dict, records: list[dict]) -> None:
    _require(
        manifest.get("control") == "uniform-clipped-self-imitation",
        "uniform manifest has the wrong control",
    )
    c = manifest.get("c")
    _require(
        isinstance(c, (int, float))
        and not isinstance(c, bool)
        and math.isfinite(float(c))
        and float(c) > 0,
        "uniform manifest c must be finite and positive",
    )
    _require(
        manifest.get("c_rule") == "corpus mean |advantage| over nonzero tokens",
        "uniform manifest has the wrong c_rule",
    )
    nonzero = 0
    zero = 0
    for record in records:
        for advantage in record["advantages"]:
            if float(advantage) == 0.0:
                zero += 1
            else:
                nonzero += 1
                _require(
                    float(advantage) == float(c),
                    "uniform record contains a nonzero advantage different from c",
                )
    _require(manifest.get("n_records") == len(records), "uniform n_records mismatch")
    _require(
        manifest.get("n_nonzero_tokens") == nonzero,
        "uniform n_nonzero_tokens mismatch",
    )
    _require(
        manifest.get("n_zero_tokens_kept") == zero,
        "uniform n_zero_tokens_kept mismatch",
    )


def _verify_resample(manifest: dict, records: list[dict]) -> None:
    original = manifest.get("original_records")
    resampled = manifest.get("resampled_records")
    target = manifest.get("target_records")
    _require(_positive_int(original), "resample original_records must be positive")
    _require(_positive_int(resampled), "resample resampled_records must be positive")
    _require(_positive_int(target), "resample target_records must be positive")
    _require(original + resampled == target, "resample record counts do not add up")
    _require(target == len(records), "resample target_records mismatch")
    _require(
        isinstance(manifest.get("seed"), int)
        and not isinstance(manifest.get("seed"), bool),
        "resample seed must be an integer",
    )
    _require(
        manifest.get("sampling") == "uniform-with-replacement-after-originals",
        "resample manifest has the wrong sampling rule",
    )
    _require(
        _digest(manifest.get("sampled_indices_sha256")),
        "resample sampled_indices_sha256 is invalid",
    )


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _verify_generated(manifest: dict, records: list[dict]) -> None:
    _require(
        manifest.get("builder") == opd_data_manifest.BUILDER_RELATIVE_PATH,
        "generated-record manifest has the wrong builder",
    )
    _require(
        manifest.get("n_records") == len(records),
        "generated-record n_records mismatch",
    )
    inventory = manifest.get("source_logs")
    _require(
        isinstance(inventory, list) and bool(inventory),
        "generated-record source_logs must be nonempty",
    )
    for item in inventory:
        item_path = item.get("path") if isinstance(item, dict) else None
        _require(
            isinstance(item, dict)
            and isinstance(item_path, str)
            and bool(item_path)
            and not PurePosixPath(item_path).is_absolute()
            and ".." not in PurePosixPath(item_path).parts
            and _digest(item.get("sha256"))
            and _nonnegative_int(item.get("size_bytes")),
            "generated-record source log entry is invalid",
        )
    _require(
        inventory == sorted(inventory, key=lambda item: item["path"])
        and len({item["path"] for item in inventory}) == len(inventory),
        "generated-record source log inventory must be sorted and unique",
    )
    _require(
        manifest.get("source_sha256") == _canonical_sha256(inventory),
        "generated-record source inventory digest mismatch",
    )
    _require(
        isinstance(manifest.get("source_runs"), list)
        and bool(manifest["source_runs"])
        and all(isinstance(value, str) and value for value in manifest["source_runs"])
        and len(set(manifest["source_runs"])) == len(manifest["source_runs"]),
        "generated-record source_runs are invalid",
    )
    _require(
        _digest(manifest.get("heldout_sha256"))
        and _nonnegative_int(manifest.get("n_heldout")),
        "generated-record heldout receipt is invalid",
    )
    parameters = manifest.get("parameters")
    required_parameters = {
        "student_tokenizer_id",
        "student_artifact_id",
        "student_artifact_sha256",
        "teacher_artifact_id",
        "teacher_artifact_sha256",
        "max_history_messages",
        "max_sequence_tokens",
        "kl_coefficient",
        "holdout_every",
        "early_weight",
        "malformed_parameter_pattern",
        "counterfactual_grading",
        "limit",
    }
    _require(
        isinstance(parameters, dict)
        and set(parameters) == required_parameters
        and parameters.get("student_tokenizer_id") == "Qwen/Qwen3.5-2B"
        and isinstance(parameters.get("student_artifact_id"), str)
        and bool(parameters["student_artifact_id"])
        and "://" not in parameters["student_artifact_id"]
        and not any(char.isspace() for char in parameters["student_artifact_id"])
        and _digest(parameters.get("student_artifact_sha256"))
        and isinstance(parameters.get("teacher_artifact_id"), str)
        and bool(parameters["teacher_artifact_id"])
        and "://" not in parameters["teacher_artifact_id"]
        and not any(char.isspace() for char in parameters["teacher_artifact_id"])
        and _digest(parameters.get("teacher_artifact_sha256"))
        and parameters.get("max_history_messages") == 28
        and parameters.get("max_sequence_tokens") == 16384
        and parameters.get("kl_coefficient") == 1.0
        and parameters.get("holdout_every") == 10
        and parameters.get("early_weight") == 1.5
        and parameters.get("malformed_parameter_pattern")
        == r"<parameter=[^>\n]*=[^>\n]*>"
        and parameters.get("counterfactual_grading") is True
        and parameters.get("limit") == 0,
        "generated-record parameters are invalid",
    )


def load_verified_training_records(
    records_path: str | Path,
    manifest_path: str | Path,
) -> list[dict]:
    """Return records only after their immutable transformation receipt verifies."""
    records = Path(records_path)
    receipt = Path(manifest_path) if str(manifest_path) else None
    _require(records.is_file(), f"records path is not a regular file: {records}")
    _require(receipt is not None, "--records-manifest-path is required")
    _require(receipt.is_file(), f"records manifest is not a regular file: {receipt}")
    try:
        manifest = json.loads(receipt.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrainingRecordBundleError(f"invalid records manifest: {exc}") from exc
    _require(isinstance(manifest, dict), "records manifest must be a JSON object")

    schema = manifest.get("schema_version")
    _require(schema in SUPPORTED_MANIFESTS, f"unsupported records manifest: {schema!r}")
    _require(_digest(manifest.get("source_sha256")), "source_sha256 is invalid")
    _require(_digest(manifest.get("output_sha256")), "output_sha256 is invalid")
    _require(
        manifest["output_sha256"] == _sha256(records),
        "records SHA-256 does not match the manifest",
    )
    _require(
        manifest.get("record_schema_version") == OPD_TRAIN_RECORD_SCHEMA_VERSION,
        "record schema version mismatch",
    )
    _require(
        manifest.get("record_schema_sha256") == OPD_TRAIN_RECORD_SCHEMA_SHA256,
        "record schema identity mismatch",
    )
    _require(
        manifest.get("record_schema_validator_sha256")
        == OPD_TRAIN_RECORD_VALIDATOR_SHA256,
        "record validator identity mismatch",
    )
    if schema == opd_data_manifest.MANIFEST_SCHEMA_VERSION:
        transformer_path = Path(__file__).with_name("opd_2b_data.py")
    elif schema == make_uniform_advantages.MANIFEST_SCHEMA_VERSION:
        transformer_path = Path(make_uniform_advantages.__file__).resolve()
    else:
        transformer_path = Path(resample_records.__file__).resolve()
    _require(
        transformer_path.is_file()
        and manifest.get("script_sha256") == _sha256(transformer_path),
        "record transformer identity mismatch",
    )

    loaded = _load_records(records)
    if schema == opd_data_manifest.MANIFEST_SCHEMA_VERSION:
        _verify_generated(manifest, loaded)
    elif schema == make_uniform_advantages.MANIFEST_SCHEMA_VERSION:
        _verify_uniform(manifest, loaded)
    else:
        _verify_resample(manifest, loaded)
    return loaded
