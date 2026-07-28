#!/usr/bin/env python3
"""Verify the anonymous V2 review artifact without repository history.

This verifier intentionally authenticates the public artifact against its
external trust root and independently recomputes the registered primary
outcome. It does not authenticate private source history or the identity-bearing
full model lock; those checks remain available in the full repository verifier
after deanonymization.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any


REVIEW_ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
if str(REVIEW_ROOT) not in sys.path:
    sys.path.insert(0, str(REVIEW_ROOT))

from scripts.opd import audit_trigger_incidence_artifact as primary_audit  # noqa: E402
from scripts.opd import analyze_structured_call_validity as routing_analysis  # noqa: E402


REVIEW_SCHEMA = "kaetram.local-trigger-incidence-review-artifact.v1"
SHA256 = re.compile(r"[0-9a-f]{64}")


class ReviewVerificationError(RuntimeError):
    """Raised when the anonymous review artifact fails verification."""


def _reject_constant(value: str) -> None:
    raise ReviewVerificationError(f"non-finite JSON constant is forbidden: {value}")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReviewVerificationError(f"duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def _strict_loads(payload: str, *, label: str) -> Any:
    try:
        value = json.loads(
            payload,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, ReviewVerificationError) as exc:
        raise ReviewVerificationError(f"invalid strict JSON: {label}") from exc
    _reject_nonfinite(value)
    return value


def _reject_nonfinite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ReviewVerificationError("non-finite JSON number is forbidden")
    if isinstance(value, dict):
        for child in value.values():
            _reject_nonfinite(child)
    elif isinstance(value, list):
        for child in value:
            _reject_nonfinite(child)


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
        raise ReviewVerificationError("artifact path must be a nonempty string")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ReviewVerificationError(f"unsafe artifact path: {value!r}")
    if pure.as_posix() != value:
        raise ReviewVerificationError(f"non-canonical artifact path: {value!r}")
    return Path(*pure.parts)


def _load_object(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ReviewVerificationError(f"expected regular JSON file: {path}")
    value = _strict_loads(path.read_text(), label=str(path))
    if not isinstance(value, dict):
        raise ReviewVerificationError(f"JSON root must be an object: {path}")
    return value


def _verify_inventory(root: Path, expected_index_sha256: str) -> dict:
    if SHA256.fullmatch(expected_index_sha256) is None:
        raise ReviewVerificationError("expected artifact-index digest is invalid")
    index_path = root / "artifact-index.json"
    if _sha256_file(index_path) != expected_index_sha256:
        raise ReviewVerificationError("artifact-index digest differs from trust root")
    index = _load_object(index_path)
    if index.get("schema_version") != REVIEW_SCHEMA:
        raise ReviewVerificationError("unexpected review artifact schema")
    records = index.get("files")
    if not isinstance(records, list) or not records:
        raise ReviewVerificationError("artifact file inventory is missing")
    normalized = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "path",
            "size_bytes",
            "sha256",
        }:
            raise ReviewVerificationError("invalid artifact file record")
        relative = _safe_path(record["path"])
        name = relative.as_posix()
        path = root / relative
        size = record["size_bytes"]
        digest = record["sha256"]
        if name == "artifact-index.json" or name in seen:
            raise ReviewVerificationError(f"duplicate artifact path: {name}")
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
            raise ReviewVerificationError(f"artifact file digest mismatch: {name}")
        normalized.append({"path": name, "size_bytes": size, "sha256": digest})
    if [record["path"] for record in normalized] != sorted(seen):
        raise ReviewVerificationError("artifact inventory is not ordered")
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual != {*seen, "artifact-index.json"}:
        raise ReviewVerificationError("artifact contains missing or unindexed files")
    if index.get("tree_sha256") != _sha256_json(normalized):
        raise ReviewVerificationError("artifact tree digest mismatch")
    for path in actual:
        current = root / path
        if current.suffix == ".json":
            _load_object(current)
        elif current.suffix == ".jsonl":
            for line_number, line in enumerate(current.read_text().splitlines(), 1):
                value = _strict_loads(line, label=f"{current}:{line_number}")
                if not isinstance(value, dict):
                    raise ReviewVerificationError("JSONL row is not an object")
    return index


def _verify_code_identity(index: dict) -> None:
    """Bind the executing standalone verifier stack to the review trust root."""

    records = index.get("verification_code")
    if not isinstance(records, list) or not records:
        raise ReviewVerificationError("verification-code identity is missing")
    names = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
            raise ReviewVerificationError("invalid verification-code record")
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
            raise ReviewVerificationError(
                f"verification-code digest mismatch: {relative.as_posix()}"
            )
        names.append(relative.as_posix())
    if names != sorted(set(names)):
        raise ReviewVerificationError("verification-code records are not canonical")


def _semantic_digest(message: Any) -> str:
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise ReviewVerificationError("response message is not canonical")
    if not isinstance(message.get("content"), str):
        raise ReviewVerificationError("response content is not a string")
    allowed = {"role", "content", "reasoning", "tool_calls"}
    if not set(message) <= allowed:
        raise ReviewVerificationError("response contains nonsemantic metadata")
    normalized = copy.deepcopy(message)
    tool_calls = normalized.get("tool_calls")
    if tool_calls is not None:
        if not isinstance(tool_calls, list) or not tool_calls:
            raise ReviewVerificationError("response tool-call list is invalid")
        for call in tool_calls:
            if not isinstance(call, dict):
                raise ReviewVerificationError("response tool call is invalid")
            call.pop("id", None)
    return _sha256_json(normalized)


def _seed_heterogeneity(registration: dict, rows: dict[tuple, dict]) -> dict:
    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in rows.values():
        grouped[(row["snapshot"], row["condition_id"], row["state_id"])].append(row)
    expected_groups = (
        len(registration["snapshots"])
        * len(registration["conditions"])
        * int(registration["state_pool"]["state_count"])
    )
    samples = int(registration["sampling"]["samples_per_state_condition"])
    if len(grouped) != expected_groups:
        raise ReviewVerificationError("seed groups do not cover the registered grid")
    semantic_counts = []
    primary_counts = []
    for key, members in sorted(grouped.items()):
        if len(members) != samples or any(row["status"] != "ok" for row in members):
            raise ReviewVerificationError(f"incomplete seed group: {key}")
        semantic_counts.append(
            len({_semantic_digest(row["response_message"]) for row in members})
        )
        primary_counts.append(len({bool(row["recovery_opportunity"]) for row in members}))
    return {
        "state_condition_groups": len(grouped),
        "groups_with_multiple_semantic_responses": sum(value > 1 for value in semantic_counts),
        "groups_with_primary_outcome_heterogeneity": sum(value > 1 for value in primary_counts),
        "minimum_unique_semantic_responses_per_group": min(semantic_counts),
        "maximum_unique_semantic_responses_per_group": max(semantic_counts),
    }


def verify_review_artifact(root: Path, expected_index_sha256: str) -> dict:
    index = _verify_inventory(root, expected_index_sha256)
    _verify_code_identity(index)
    registration = _load_object(root / "registration.json")
    design = _load_object(root / "design" / "design.json")
    stored = _load_object(root / "analysis" / "analysis-summary.json")
    try:
        rows = primary_audit._load_and_check_rows(root, registration, design)
        recomputed = primary_audit.recompute_summary(registration, design, rows)
    except primary_audit.AuditError as exc:
        raise ReviewVerificationError(str(exc)) from exc
    heterogeneity = _seed_heterogeneity(registration, rows)
    routing_stored = _load_object(root / "analysis" / "routing-validity-posthoc.json")
    routing_recomputed = routing_analysis.analyze_runs(
        [root / "runs" / snapshot / "results.jsonl" for snapshot in registration["snapshots"]]
    )
    if routing_stored != routing_recomputed:
        raise ReviewVerificationError("stored routing-validity decomposition does not recompute")
    native_effects = {
        record["snapshot"]: record["effect_rate_difference"]
        for record in recomputed["registered_contrasts"]
        if record["contrast"] == "native_tools_main"
    }
    directional_passed = all(
        native_effects.get(snapshot, 0) > 0 for snapshot in registration["snapshots"]
    )
    stored_heterogeneity = stored.get("registered_seed_heterogeneity", {})
    for field, value in heterogeneity.items():
        if stored_heterogeneity.get(field) != value:
            raise ReviewVerificationError(f"stored heterogeneity mismatch: {field}")
    for field in (
        "scheduled_requests",
        "successful_requests",
        "failed_requests",
        "recovery_opportunities",
    ):
        if stored.get(field) != recomputed[field]:
            raise ReviewVerificationError(f"stored analysis mismatch: {field}")
    if stored.get("registered_contrasts") != recomputed["registered_contrasts"]:
        raise ReviewVerificationError("stored registered contrasts do not recompute")
    if stored.get("directional_replication", {}).get("passed") != directional_passed:
        raise ReviewVerificationError("stored directional verdict does not recompute")
    return {
        "artifact_index_sha256": expected_index_sha256,
        "artifact_tree_sha256": index["tree_sha256"],
        "scheduled_requests": recomputed["scheduled_requests"],
        "successful_requests": recomputed["successful_requests"],
        "failed_requests": recomputed["failed_requests"],
        "recovery_opportunities": recomputed["recovery_opportunities"],
        "native_tools_effects": native_effects,
        "directional_replication_passed": directional_passed,
        "schema_valid_any_route_effects": {
            row["snapshot"]: row["effect_rate_difference"]
            for row in routing_recomputed["native_schema_valid_any_route_contrasts"]
        },
        "positive_sample_index_schema_contrasts": sum(
            row["effect_rate_difference"] > 0
            for row in routing_recomputed["sample_index_native_schema_contrasts"]
        ),
        "sample_index_schema_contrast_count": len(
            routing_recomputed["sample_index_native_schema_contrasts"]
        ),
        "strict_recovery_replay": routing_recomputed["strict_recovery_replay"],
        **heterogeneity,
        "source_history_authentication": "deferred_until_deanonymized",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--expected-index-sha256", required=True)
    args = parser.parse_args(argv)
    result = verify_review_artifact(args.artifact_dir, args.expected_index_sha256)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
