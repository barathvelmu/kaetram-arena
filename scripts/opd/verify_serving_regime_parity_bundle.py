#!/usr/bin/env python3
"""Fail-closed verifier for the public thinking-mode parity bundle."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.opd import serving_regime_parity_probe as parity  # noqa: E402


BUNDLE_SCHEMA = "kaetram.local-serving-regime-parity-bundle.v1"
DEFAULT_ROOT = ROOT / "research/results/local-serving-regime-parity-v1"
EXPECTED_CHILDREN = (
    "README.md",
    "runs/base_2b/artifact-index.json",
    "runs/opd_r2_2b/artifact-index.json",
    "runs/opd_r3_2b/artifact-index.json",
    "analysis/artifact-index.json",
)


class BundleError(RuntimeError):
    """Raised when the public bundle is incomplete or inconsistent."""


def _load(path: Path) -> Any:
    try:
        return parity._loads_strict(path.read_text())
    except (OSError, json.JSONDecodeError, parity.ParityError) as exc:
        raise BundleError(f"cannot read strict JSON: {path}") from exc


def _verify_record(root: Path, record: dict) -> None:
    if set(record) != {"path", "size_bytes", "sha256"}:
        raise BundleError("non-canonical file record")
    relative = Path(str(record["path"]))
    if relative.is_absolute() or ".." in relative.parts:
        raise BundleError("unsafe bundle path")
    path = root / relative
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size != record["size_bytes"]
        or parity._sha256_file(path) != record["sha256"]
    ):
        raise BundleError(f"file identity mismatch: {relative}")


def _counts(rows: list[dict]) -> dict[str, int]:
    return {
        "structured": sum(bool(row["has_structured_tool_call"]) for row in rows),
        "recovery": sum(bool(row["recovery_opportunity"]) for row in rows),
        "no_candidate": sum(parity._route(row) == "no_candidate" for row in rows),
    }


def _verify_checkpoint(
    reported: dict,
    enabled: list[dict],
    disabled: list[dict],
    conditions: dict[str, dict],
) -> None:
    n = len(enabled)
    key = lambda row: (
        row["state_id"], row["condition_id"], row["sample_index"], row["seed"]
    )
    enabled_map = {key(row): row for row in enabled}
    disabled_map = {key(row): row for row in disabled}
    if len(enabled_map) != n or set(enabled_map) != set(disabled_map):
        raise BundleError("paired checkpoint grid differs across arms")
    ec = _counts(enabled)
    dc = _counts(disabled)
    if sum(ec.values()) != n or sum(dc.values()) != n:
        raise BundleError("route categories do not partition the checkpoint")
    expected: dict[str, Any] = {"paired_requests": n}
    for route in ("recovery", "structured", "no_candidate"):
        expected.update({
            f"thinking_{route}_count": ec[route],
            f"thinking_{route}_rate": ec[route] / n,
            f"disabled_{route}_count": dc[route],
            f"disabled_{route}_rate": dc[route] / n,
            f"{route}_rate_difference_disabled_minus_thinking": (
                dc[route] - ec[route]
            ) / n,
        })
    transitions = Counter(
        (parity._route(enabled_map[cell]), parity._route(disabled_map[cell]))
        for cell in sorted(enabled_map)
    )
    expected["route_transitions"] = {
        f"{before}_to_{after}": count
        for (before, after), count in sorted(transitions.items())
    }
    for field, value in expected.items():
        if reported.get(field) != value:
            raise BundleError(f"reported checkpoint field does not recompute: {field}")
    by_schema = reported.get("by_native_schema")
    if not isinstance(by_schema, dict):
        raise BundleError("schema-stratified results are absent")
    for schema in ("absent", "present"):
        cells = [
            cell for cell in enabled_map
            if conditions[cell[1]]["native_tool_schema"] == schema
        ]
        if len(cells) != n // 2 or by_schema.get(schema, {}).get("requests") != n // 2:
            raise BundleError("schema denominator does not recompute")
        for route in ("recovery", "structured", "no_candidate"):
            on = sum(
                parity._route(enabled_map[cell])
                == ("reasoning_stranded" if route == "recovery" else route)
                for cell in cells
            )
            off = sum(
                parity._route(disabled_map[cell])
                == ("reasoning_stranded" if route == "recovery" else route)
                for cell in cells
            )
            record = by_schema[schema]
            if (
                record.get(f"thinking_{route}_count") != on
                or record.get(f"disabled_{route}_count") != off
                or record.get(f"thinking_{route}_rate") != on / len(cells)
                or record.get(f"disabled_{route}_rate") != off / len(cells)
                or record.get(
                    f"{route}_rate_difference_disabled_minus_thinking"
                ) != (off - on) / len(cells)
            ):
                raise BundleError("schema-stratified result does not recompute")


def verify_bundle(root: Path, expected_index_sha256: str | None = None) -> dict:
    index_path = root / "bundle-index.json"
    if expected_index_sha256 and parity._sha256_file(index_path) != expected_index_sha256:
        raise BundleError("bundle index hash differs from the expected trust root")
    index = _load(index_path)
    files = index.get("files")
    if (
        index.get("schema_version") != BUNDLE_SCHEMA
        or index.get("study_id") != "local-serving-regime-parity-v1"
        or not isinstance(files, list)
        or tuple(record.get("path") for record in files) != EXPECTED_CHILDREN
        or index.get("tree_sha256") != parity._sha256_json(files)
    ):
        raise BundleError("invalid bundle index")
    for record in files:
        if not isinstance(record, dict):
            raise BundleError("bundle file record is not an object")
        _verify_record(root, record)

    registration_path = ROOT / "research/experiments/local-serving-regime-parity-v1.json"
    registration, registration_sha256 = parity.load_registration(registration_path)
    if index.get("registration_sha256") != registration_sha256:
        raise BundleError("bundle registration identity mismatch")
    analysis_index = _load(root / "analysis/artifact-index.json")
    analysis_files = analysis_index.get("files")
    if (
        analysis_index.get("schema_version")
        != f"{parity.ANALYSIS_SCHEMA}.artifacts"
        or not isinstance(analysis_files, list)
        or [record.get("path") for record in analysis_files]
        != ["analysis-summary.json"]
        or analysis_index.get("tree_sha256") != parity._sha256_json(analysis_files)
    ):
        raise BundleError("invalid analysis artifact index")
    _verify_record(root / "analysis", analysis_files[0])
    summary = _load(root / "analysis/analysis-summary.json")
    if (
        summary.get("schema_version") != parity.ANALYSIS_SCHEMA
        or summary.get("status") != "complete"
        or summary.get("registration_sha256") != registration_sha256
        or summary.get("analysis_code_provenance", {}).get("source_git_commit")
        != index.get("analysis_source_git_commit")
    ):
        raise BundleError("analysis identity mismatch")

    source_registration, _design, _grid = parity._source_inputs(registration)
    conditions = {
        item["condition_id"]: item for item in source_registration["conditions"]
    }
    reported_rows = summary.get("checkpoint_results")
    if (
        not isinstance(reported_rows, list)
        or [row.get("snapshot") for row in reported_rows]
        != list(registration["snapshots"])
    ):
        raise BundleError("analysis checkpoint rows are incomplete")
    pooled_transitions = Counter()
    for snapshot, reported in zip(registration["snapshots"], reported_rows, strict=True):
        disabled = parity.verify_run(
            registration,
            registration_sha256,
            snapshot,
            root / "runs" / snapshot,
        )
        enabled, _identity = parity._historical_thinking_rows(
            registration, snapshot, conditions
        )
        _verify_checkpoint(reported, enabled, disabled, conditions)
        pooled_transitions.update(reported["route_transitions"])
    directional = all(
        row["recovery_rate_difference_disabled_minus_thinking"] < 0
        for row in reported_rows
    )
    if summary.get("registered_directional_criterion_passed") is not directional:
        raise BundleError("registered directional verdict does not recompute")
    pooled = summary.get("pooled_descriptive_result") or {}
    if (
        pooled.get("paired_requests") != sum(row["paired_requests"] for row in reported_rows)
        or pooled.get("route_transitions") != dict(sorted(pooled_transitions.items()))
    ):
        raise BundleError("pooled result does not recompute")
    return {
        "schema_version": index["schema_version"],
        "study_id": index["study_id"],
        "bundle_index_sha256": parity._sha256_file(index_path),
        "tree_sha256": index["tree_sha256"],
        "new_requests": pooled["paired_requests"],
        "registered_directional_criterion_passed": directional,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--expected-index-sha256")
    args = parser.parse_args()
    try:
        result = verify_bundle(args.bundle, args.expected_index_sha256)
    except (BundleError, parity.ParityError, KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"verification failed: {exc}") from exc
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
