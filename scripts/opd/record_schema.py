"""Canonical core schema for OPD training JSONL records."""
from __future__ import annotations

import hashlib
import json
import math


OPD_TRAIN_RECORD_SCHEMA_VERSION = "kaetram-opd-train-record-v1"
OPD_TRAIN_RECORD_SCHEMA = {
    "version": OPD_TRAIN_RECORD_SCHEMA_VERSION,
    "required": {
        "input_ids": "nonempty list[int>=0]",
        "labels": "aligned list[int] with at least one supervised token",
        "advantages": "aligned list[finite number]",
        "behavior_logprobs": "aligned list[finite number]",
        "step_weight": "finite number > 0",
    },
    "alignment": [
        "input_ids",
        "labels",
        "advantages",
        "behavior_logprobs",
    ],
    "ignored_label": -100,
}
OPD_TRAIN_RECORD_SCHEMA_SHA256 = hashlib.sha256(
    json.dumps(
        OPD_TRAIN_RECORD_SCHEMA,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()


class RecordSchemaError(ValueError):
    """Raised when a record is not a canonical OPD training record."""


def _integer(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _finite_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def validate_opd_train_record(record: object, *, line_number: int) -> dict:
    """Validate the canonical trainer-facing fields while permitting metadata."""
    if not isinstance(record, dict):
        raise RecordSchemaError(f"record {line_number} is not a JSON object")

    missing = [
        field
        for field in OPD_TRAIN_RECORD_SCHEMA["required"]
        if field not in record
    ]
    if missing:
        raise RecordSchemaError(
            f"record {line_number} is missing required OPD field(s): "
            + ", ".join(missing)
        )

    input_ids = record["input_ids"]
    if (
        not isinstance(input_ids, list)
        or not input_ids
        or any(not _integer(value) or value < 0 for value in input_ids)
    ):
        raise RecordSchemaError(
            f"record {line_number} input_ids must be a nonempty list of nonnegative integers"
        )

    expected = len(input_ids)
    labels = record["labels"]
    if (
        not isinstance(labels, list)
        or len(labels) != expected
        or any(not _integer(value) for value in labels)
    ):
        raise RecordSchemaError(
            f"record {line_number} labels must be an integer list aligned with input_ids"
        )
    if all(value == -100 for value in labels):
        raise RecordSchemaError(
            f"record {line_number} has no supervised label token"
        )

    for field in ("advantages", "behavior_logprobs"):
        values = record[field]
        if (
            not isinstance(values, list)
            or len(values) != expected
            or any(not _finite_number(value) for value in values)
        ):
            raise RecordSchemaError(
                f"record {line_number} {field} must be a finite numeric list "
                "aligned with input_ids"
            )

    step_weight = record["step_weight"]
    if not _finite_number(step_weight) or float(step_weight) <= 0:
        raise RecordSchemaError(
            f"record {line_number} step_weight must be finite and positive"
        )
    return record
