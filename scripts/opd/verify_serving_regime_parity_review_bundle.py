#!/usr/bin/env python3
"""Independently verify the anonymous thinking-mode parity review artifact.

The review projection deliberately omits repository revisions, endpoint
coordinates, and private provenance receipts.  Given the review-only artifact
index digest printed in the anonymous paper, this verifier authenticates every
projected byte and recomputes the paired finite-grid routing result directly
from the retained assistant response messages.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any


REVIEW_ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
if str(REVIEW_ROOT) not in sys.path:
    sys.path.insert(0, str(REVIEW_ROOT))

from scripts.opd.canonicalize import recover_tool_calls  # noqa: E402


REVIEW_SCHEMA = "kaetram.local-serving-regime-parity-review-artifact.v1"
REGISTRATION_SCHEMA = "kaetram.local-serving-regime-parity-review-registration.v1"
ANALYSIS_SCHEMA = "kaetram.local-serving-regime-parity-review-analysis.v1"
SNAPSHOTS = ("base_2b", "opd_r2_2b", "opd_r3_2b")
ARMS = ("thinking_enabled", "thinking_disabled")
ROUTES = ("recovery", "structured", "no_candidate")
SHA256 = re.compile(r"[0-9a-f]{64}")
ROW_FIELDS = {
    "arm",
    "snapshot",
    "state_id",
    "state_index",
    "condition_id",
    "sample_index",
    "seed",
    "finish_reason",
    "attempt_errors",
    "response_message",
}


class ParityReviewError(RuntimeError):
    """Raised when the anonymous parity evidence does not verify."""


def _reject_constant(value: str) -> None:
    raise ParityReviewError(f"non-finite JSON constant is forbidden: {value}")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ParityReviewError(f"duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ParityReviewError("non-finite JSON number is forbidden")
    if isinstance(value, dict):
        for child in value.values():
            _reject_nonfinite(child)
    elif isinstance(value, list):
        for child in value:
            _reject_nonfinite(child)


def _strict_loads(payload: str, *, label: str) -> Any:
    try:
        value = json.loads(
            payload,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, ParityReviewError) as exc:
        raise ParityReviewError(f"invalid strict JSON: {label}") from exc
    _reject_nonfinite(value)
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _safe_path(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise ParityReviewError("artifact path must be a nonempty string")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ParityReviewError(f"unsafe artifact path: {value!r}")
    if pure.as_posix() != value:
        raise ParityReviewError(f"non-canonical artifact path: {value!r}")
    return Path(*pure.parts)


def _load_object(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ParityReviewError(f"expected regular JSON file: {path}")
    value = _strict_loads(path.read_text(), label=str(path))
    if not isinstance(value, dict):
        raise ParityReviewError(f"JSON root must be an object: {path}")
    return value


def _verify_inventory(root: Path, expected_index_sha256: str) -> dict:
    if SHA256.fullmatch(expected_index_sha256) is None:
        raise ParityReviewError("expected artifact-index digest is invalid")
    index_path = root / "artifact-index.json"
    if index_path.is_symlink() or not index_path.is_file():
        raise ParityReviewError("artifact index is not a regular file")
    if _sha256_file(index_path) != expected_index_sha256:
        raise ParityReviewError("artifact-index digest differs from trust root")
    index = _load_object(index_path)
    if index.get("schema_version") != REVIEW_SCHEMA:
        raise ParityReviewError("unexpected parity review schema")
    records = index.get("files")
    if not isinstance(records, list) or not records:
        raise ParityReviewError("artifact file inventory is missing")
    normalized = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "path",
            "size_bytes",
            "sha256",
        }:
            raise ParityReviewError("invalid artifact file record")
        relative = _safe_path(record["path"])
        name = relative.as_posix()
        size = record["size_bytes"]
        digest = record["sha256"]
        path = root / relative
        if name == "artifact-index.json" or name in seen:
            raise ParityReviewError(f"duplicate artifact path: {name}")
        seen.add(name)
        if (
            path.is_symlink()
            or not path.is_file()
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or not isinstance(digest, str)
            or SHA256.fullmatch(digest) is None
            or path.stat().st_size != size
            or _sha256_file(path) != digest
        ):
            raise ParityReviewError(f"artifact file digest mismatch: {name}")
        normalized.append({"path": name, "size_bytes": size, "sha256": digest})
    if [record["path"] for record in normalized] != sorted(seen):
        raise ParityReviewError("artifact inventory is not ordered")
    expected_files = {
        "registration.json",
        "analysis-summary.json",
        *(
            f"runs/{snapshot}/{arm}.jsonl"
            for snapshot in SNAPSHOTS
            for arm in ARMS
        ),
    }
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if seen != expected_files or actual != {*expected_files, "artifact-index.json"}:
        raise ParityReviewError("artifact contains missing, extra, or unindexed files")
    if index.get("tree_sha256") != _sha256_json(normalized):
        raise ParityReviewError("artifact tree digest mismatch")
    for name in expected_files:
        path = root / name
        if path.suffix == ".json":
            _load_object(path)
        else:
            for line_number, line in enumerate(path.read_text().splitlines(), 1):
                value = _strict_loads(line, label=f"{path}:{line_number}")
                if not isinstance(value, dict):
                    raise ParityReviewError("JSONL row is not an object")
    return index


def _verify_code_identity(index: dict) -> None:
    records = index.get("verification_code")
    if not isinstance(records, list) or not records:
        raise ParityReviewError("verification-code identity is missing")
    names = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
            raise ParityReviewError("invalid verification-code record")
        relative = _safe_path(record["path"])
        digest = record["sha256"]
        path = REVIEW_ROOT / relative
        if (
            not isinstance(digest, str)
            or SHA256.fullmatch(digest) is None
            or path.is_symlink()
            or not path.is_file()
            or _sha256_file(path) != digest
        ):
            raise ParityReviewError(
                f"verification-code digest mismatch: {relative.as_posix()}"
            )
        names.append(relative.as_posix())
    if names != sorted(set(names)):
        raise ParityReviewError("verification-code records are not canonical")


def _route(message: Any) -> str:
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise ParityReviewError("response message is not canonical")
    if not set(message) <= {"role", "content", "reasoning", "tool_calls"}:
        raise ParityReviewError("response contains nonsemantic metadata")
    content = message.get("content") or ""
    if not isinstance(content, str):
        raise ParityReviewError("response content is not a string")
    tool_calls = message.get("tool_calls") or []
    if not isinstance(tool_calls, list):
        raise ParityReviewError("response tool_calls is not a list")
    if tool_calls:
        if any(not isinstance(call, dict) for call in tool_calls):
            raise ParityReviewError("structured tool call is not an object")
        return "structured"
    return "reasoning_stranded" if recover_tool_calls(content) else "no_candidate"


def _load_rows(path: Path, *, snapshot: str, arm: str) -> list[dict]:
    rows = []
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        value = _strict_loads(line, label=f"{path}:{line_number}")
        if not isinstance(value, dict) or set(value) != ROW_FIELDS:
            raise ParityReviewError("parity row schema is invalid")
        if value.get("snapshot") != snapshot or value.get("arm") != arm:
            raise ParityReviewError("parity row arm or checkpoint label is invalid")
        if value.get("finish_reason") is not None and not isinstance(
            value.get("finish_reason"), str
        ):
            raise ParityReviewError("finish reason is invalid")
        errors = value.get("attempt_errors")
        if errors is not None and (
            not isinstance(errors, list)
            or any(not isinstance(item, str) for item in errors)
        ):
            raise ParityReviewError("attempt-error record is invalid")
        _route(value.get("response_message"))
        rows.append(value)
    return rows


def _counts(rows: list[dict]) -> dict[str, int]:
    routes = Counter(_route(row["response_message"]) for row in rows)
    return {
        "recovery": routes["reasoning_stranded"],
        "structured": routes["structured"],
        "no_candidate": routes["no_candidate"],
    }


def _verify_checkpoint(
    reported: dict,
    enabled: list[dict],
    disabled: list[dict],
    registration: dict,
) -> None:
    panel = registration["confirmatory_panel"]
    conditions = {
        item["condition_id"]: item for item in registration["conditions"]
    }
    expected_indices = panel["state_indices"]
    sample_count = panel["samples_per_state_condition"]
    base_seed = registration["sampling"]["base_seed"]
    expected_cells = {
        (
            f"state-{state_index + 1:02d}",
            condition_id,
            sample_index,
            base_seed + 100 * state_index + sample_index,
        )
        for state_index in expected_indices
        for condition_id in conditions
        for sample_index in range(sample_count)
    }
    key = lambda row: (
        row["state_id"], row["condition_id"], row["sample_index"], row["seed"]
    )
    enabled_map = {key(row): row for row in enabled}
    disabled_map = {key(row): row for row in disabled}
    if (
        len(enabled) != len(expected_cells)
        or len(disabled) != len(expected_cells)
        or set(enabled_map) != expected_cells
        or set(disabled_map) != expected_cells
    ):
        raise ParityReviewError("paired confirmatory grid is incomplete")
    for cell in expected_cells:
        expected_index = int(enabled_map[cell]["state_id"].split("-")[1]) - 1
        if (
            enabled_map[cell]["state_index"] != expected_index
            or disabled_map[cell]["state_index"] != expected_index
        ):
            raise ParityReviewError("state index does not match state ID")
    n = len(expected_cells)
    enabled_counts = _counts(enabled)
    disabled_counts = _counts(disabled)
    if sum(enabled_counts.values()) != n or sum(disabled_counts.values()) != n:
        raise ParityReviewError("route categories do not partition the checkpoint")
    expected: dict[str, Any] = {"paired_requests": n}
    for route in ROUTES:
        on = enabled_counts[route]
        off = disabled_counts[route]
        expected.update({
            f"thinking_{route}_count": on,
            f"thinking_{route}_rate": on / n,
            f"disabled_{route}_count": off,
            f"disabled_{route}_rate": off / n,
            f"{route}_rate_difference_disabled_minus_thinking": (off - on) / n,
        })
    transitions = Counter(
        (
            _route(enabled_map[cell]["response_message"]),
            _route(disabled_map[cell]["response_message"]),
        )
        for cell in sorted(expected_cells)
    )
    expected["route_transitions"] = {
        f"{before}_to_{after}": count
        for (before, after), count in sorted(transitions.items())
    }
    for field, value in expected.items():
        if reported.get(field) != value:
            raise ParityReviewError(
                f"reported checkpoint field does not recompute: {field}"
            )
    by_schema = reported.get("by_native_schema")
    if not isinstance(by_schema, dict):
        raise ParityReviewError("schema-stratified results are absent")
    for schema in ("absent", "present"):
        cells = [
            cell for cell in expected_cells
            if conditions[cell[1]]["native_tool_schema"] == schema
        ]
        record = by_schema.get(schema)
        if not isinstance(record, dict) or record.get("requests") != len(cells):
            raise ParityReviewError("schema denominator does not recompute")
        for route in ROUTES:
            label = "reasoning_stranded" if route == "recovery" else route
            on = sum(
                _route(enabled_map[cell]["response_message"]) == label for cell in cells
            )
            off = sum(
                _route(disabled_map[cell]["response_message"]) == label for cell in cells
            )
            if (
                record.get(f"thinking_{route}_count") != on
                or record.get(f"disabled_{route}_count") != off
                or record.get(f"thinking_{route}_rate") != on / len(cells)
                or record.get(f"disabled_{route}_rate") != off / len(cells)
                or record.get(f"{route}_rate_difference_disabled_minus_thinking")
                != (off - on) / len(cells)
            ):
                raise ParityReviewError("schema-stratified result does not recompute")
    if reported.get("thinking_finish_reason_retained") is not False:
        raise ParityReviewError("historical finish-reason boundary is absent")
    finish_counts = dict(sorted(Counter(
        str(row["finish_reason"]) for row in disabled
    ).items()))
    retried = sum(bool(row["attempt_errors"]) for row in disabled)
    errors = sum(len(row["attempt_errors"] or []) for row in disabled)
    if (
        reported.get("disabled_finish_reason_counts") != finish_counts
        or reported.get("disabled_retried_request_count") != retried
        or reported.get("disabled_attempt_error_count") != errors
        or any(row["finish_reason"] is not None for row in enabled)
        or any(row["attempt_errors"] is not None for row in enabled)
    ):
        raise ParityReviewError("one-arm diagnostics do not recompute")


def verify_review_artifact(root: Path, expected_index_sha256: str) -> dict:
    index = _verify_inventory(root, expected_index_sha256)
    _verify_code_identity(index)
    registration = _load_object(root / "registration.json")
    analysis = _load_object(root / "analysis-summary.json")
    if (
        registration.get("schema_version") != REGISTRATION_SCHEMA
        or registration.get("study_id") != "local-serving-regime-parity-v1"
        or analysis.get("schema_version") != ANALYSIS_SCHEMA
        or analysis.get("study_id") != registration.get("study_id")
        or registration.get("review_projection", {}).get(
            "source_history_authentication"
        ) != "deferred_until_deanonymized"
    ):
        raise ParityReviewError("review registration or analysis identity is invalid")
    panel = registration.get("confirmatory_panel")
    conditions = registration.get("conditions")
    schema_counts = Counter(
        item.get("native_tool_schema")
        for item in conditions
        if isinstance(item, dict)
    ) if isinstance(conditions, list) else Counter()
    if (
        not isinstance(panel, dict)
        or registration.get("snapshots") != list(SNAPSHOTS)
        or registration.get("pilot_disclosure", {}).get("state_indices")
        != [0, 1, 2]
        or panel.get("state_indices") != list(range(3, 20))
        or panel.get("samples_per_state_condition") != 5
        or panel.get("requests_per_checkpoint") != 340
        or not isinstance(conditions, list)
        or len(conditions) != 4
        or len({item.get("condition_id") for item in conditions}) != 4
        or schema_counts != Counter({"absent": 2, "present": 2})
        or analysis.get("pilot_states_excluded") != [0, 1, 2]
    ):
        raise ParityReviewError("review confirmatory contract is invalid")
    reported = analysis.get("checkpoint_results")
    if (
        not isinstance(reported, list)
        or [row.get("snapshot") for row in reported] != list(SNAPSHOTS)
    ):
        raise ParityReviewError("review checkpoint results are incomplete")
    pooled_transitions = Counter()
    for snapshot, result in zip(SNAPSHOTS, reported, strict=True):
        enabled = _load_rows(
            root / "runs" / snapshot / "thinking_enabled.jsonl",
            snapshot=snapshot,
            arm="thinking_enabled",
        )
        disabled = _load_rows(
            root / "runs" / snapshot / "thinking_disabled.jsonl",
            snapshot=snapshot,
            arm="thinking_disabled",
        )
        _verify_checkpoint(result, enabled, disabled, registration)
        pooled_transitions.update(result["route_transitions"])
    directional = all(
        row["recovery_rate_difference_disabled_minus_thinking"] < 0
        for row in reported
    )
    if analysis.get("registered_directional_criterion_passed") is not directional:
        raise ParityReviewError("registered directional verdict does not recompute")
    pooled = analysis.get("pooled_descriptive_result") or {}
    pooled_n = sum(row["paired_requests"] for row in reported)
    if (
        pooled.get("paired_requests") != pooled_n
        or pooled.get("route_transitions") != dict(sorted(pooled_transitions.items()))
    ):
        raise ParityReviewError("pooled descriptive result does not recompute")
    for route in ROUTES:
        on = sum(row[f"thinking_{route}_count"] for row in reported)
        off = sum(row[f"disabled_{route}_count"] for row in reported)
        if (
            pooled.get(f"thinking_{route}_count") != on
            or pooled.get(f"thinking_{route}_rate") != on / pooled_n
            or pooled.get(f"disabled_{route}_count") != off
            or pooled.get(f"disabled_{route}_rate") != off / pooled_n
            or pooled.get(f"{route}_rate_difference_disabled_minus_thinking")
            != (off - on) / pooled_n
        ):
            raise ParityReviewError("pooled route result does not recompute")
    return {
        "schema_version": index["schema_version"],
        "study_id": registration["study_id"],
        "artifact_index_sha256": expected_index_sha256,
        "artifact_tree_sha256": index["tree_sha256"],
        "paired_requests": pooled_n,
        "checkpoint_recovery_rate_differences": {
            row["snapshot"]: row[
                "recovery_rate_difference_disabled_minus_thinking"
            ]
            for row in reported
        },
        "registered_directional_criterion_passed": directional,
        "source_history_authentication": "deferred_until_deanonymized",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--expected-index-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        result = verify_review_artifact(args.artifact_dir, args.expected_index_sha256)
    except (ParityReviewError, KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"verification failed: {exc}") from exc
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
