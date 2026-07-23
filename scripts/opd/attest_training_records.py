"""Create an immutable receipt for a canonical, non-transformed OPD corpus."""
from __future__ import annotations

import argparse
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


MANIFEST_SCHEMA_VERSION = "opd-training-records-manifest-v1"


class AttestationError(ValueError):
    """Raised when a corpus cannot be safely attested."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def attest_training_records(records_path: Path, manifest_path: Path) -> dict:
    records = records_path.resolve()
    receipt = manifest_path.resolve()
    if not records.is_file():
        raise AttestationError(f"records path is not a regular file: {records}")
    if receipt.exists():
        raise AttestationError(f"refusing to overwrite existing manifest: {receipt}")
    if not receipt.parent.is_dir():
        raise AttestationError(f"manifest directory does not exist: {receipt.parent}")

    n_records = 0
    with records.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                raise AttestationError(f"blank JSONL record at line {line_number}")
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AttestationError(
                    f"invalid JSON at record {line_number}: {exc}"
                ) from exc
            try:
                validate_opd_train_record(value, line_number=line_number)
            except RecordSchemaError as exc:
                raise AttestationError(str(exc)) from exc
            n_records += 1
    if n_records == 0:
        raise AttestationError("records file contains no records")

    artifact_sha = _sha256(records)
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "source": str(records),
        "source_sha256": artifact_sha,
        "output": str(records),
        "output_sha256": artifact_sha,
        "script_sha256": _sha256(Path(__file__).resolve()),
        "record_schema_version": OPD_TRAIN_RECORD_SCHEMA_VERSION,
        "record_schema_sha256": OPD_TRAIN_RECORD_SCHEMA_SHA256,
        "record_schema_validator_sha256": OPD_TRAIN_RECORD_VALIDATOR_SHA256,
        "n_records": n_records,
        "operation": "identity-attestation",
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--records", required=True)
    parser.add_argument("--manifest")
    args = parser.parse_args(argv)
    records = Path(args.records)
    receipt = (
        Path(args.manifest)
        if args.manifest
        else records.with_suffix(".manifest.json")
    )
    try:
        manifest = attest_training_records(records, receipt)
    except AttestationError as exc:
        parser.error(str(exc))
    print(
        f"attested {manifest['n_records']} records; "
        f"sha256={manifest['output_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
