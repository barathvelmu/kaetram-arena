"""Create-only provenance receipt for records emitted by ``opd_2b_data.py``."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

try:
    from .record_schema import (
        OPD_TRAIN_RECORD_SCHEMA_SHA256,
        OPD_TRAIN_RECORD_SCHEMA_VERSION,
        OPD_TRAIN_RECORD_VALIDATOR_SHA256,
        RecordSchemaError,
        validate_opd_train_record,
    )
except ImportError:  # direct `python scripts/opd/...` execution
    from record_schema import (  # type: ignore[no-redef]
        OPD_TRAIN_RECORD_SCHEMA_SHA256,
        OPD_TRAIN_RECORD_SCHEMA_VERSION,
        OPD_TRAIN_RECORD_VALIDATOR_SHA256,
        RecordSchemaError,
        validate_opd_train_record,
    )


MANIFEST_SCHEMA_VERSION = "opd-data-build-manifest-v1"
BUILDER_RELATIVE_PATH = "scripts/opd/opd_2b_data.py"


class DataManifestError(ValueError):
    """Raised when a generated corpus cannot be sealed honestly."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _count_and_validate_records(path: Path) -> int:
    count = 0
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                raise DataManifestError(f"blank record at line {line_number}")
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DataManifestError(
                    f"invalid JSON at record {line_number}: {exc}"
                ) from exc
            try:
                validate_opd_train_record(value, line_number=line_number)
            except RecordSchemaError as exc:
                raise DataManifestError(str(exc)) from exc
            count += 1
    if count == 0:
        raise DataManifestError("records file contains no records")
    return count


def _count_json_objects(path: Path) -> int:
    count = 0
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                raise DataManifestError(f"blank heldout row at line {line_number}")
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DataManifestError(
                    f"invalid heldout JSON at line {line_number}: {exc}"
                ) from exc
            if not isinstance(value, dict):
                raise DataManifestError(
                    f"heldout row {line_number} is not a JSON object"
                )
            count += 1
    return count


def create_opd_data_manifest(
    *,
    records_path: Path,
    heldout_path: Path,
    manifest_path: Path,
    source_logs: list[Path],
    source_root: Path,
    run_ids: list[str],
    builder_path: Path,
    parameters: dict,
) -> dict:
    """Seal a fresh builder output and its complete source-log inventory."""
    records = records_path.resolve()
    heldout = heldout_path.resolve()
    receipt = manifest_path.resolve()
    root = source_root.resolve()
    builder = builder_path.resolve()
    if not records.is_file() or not heldout.is_file():
        raise DataManifestError("records and heldout outputs must both be regular files")
    if not builder.is_file():
        raise DataManifestError(f"builder is not a regular file: {builder}")
    if receipt.exists():
        raise DataManifestError(f"refusing to overwrite existing manifest: {receipt}")
    if not source_logs:
        raise DataManifestError("source log inventory is empty")
    if (
        not run_ids
        or any(not isinstance(value, str) or not value for value in run_ids)
        or len(set(run_ids)) != len(run_ids)
    ):
        raise DataManifestError("run_ids must be unique nonempty strings")
    if not isinstance(parameters, dict):
        raise DataManifestError("parameters must be a JSON object")

    inventory = []
    for source in sorted(path.resolve() for path in source_logs):
        if not source.is_file():
            raise DataManifestError(f"source log is not a regular file: {source}")
        try:
            relative = source.relative_to(root).as_posix()
        except ValueError as exc:
            raise DataManifestError(f"source log escapes source root: {source}") from exc
        inventory.append(
            {
                "path": relative,
                "sha256": _sha256(source),
                "size_bytes": source.stat().st_size,
            }
        )
    if len({item["path"] for item in inventory}) != len(inventory):
        raise DataManifestError("source log inventory contains duplicate paths")

    n_records = _count_and_validate_records(records)
    n_heldout = _count_json_objects(heldout)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "builder": BUILDER_RELATIVE_PATH,
        "script_sha256": _sha256(builder),
        "source_runs": list(run_ids),
        "source_logs": inventory,
        "source_sha256": _canonical_sha256(inventory),
        "output": str(records),
        "output_sha256": _sha256(records),
        "heldout": str(heldout),
        "heldout_sha256": _sha256(heldout),
        "record_schema_version": OPD_TRAIN_RECORD_SCHEMA_VERSION,
        "record_schema_sha256": OPD_TRAIN_RECORD_SCHEMA_SHA256,
        "record_schema_validator_sha256": OPD_TRAIN_RECORD_VALIDATOR_SHA256,
        "n_records": n_records,
        "n_heldout": n_heldout,
        "parameters": parameters,
    }
    payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            dir=receipt.parent,
            prefix=f".{receipt.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, receipt)
        return manifest
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
