#!/usr/bin/env python3
"""Post-hoc schema-validity decomposition across both response routes."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tool_surface import validate_tool_call_arguments
from scripts.opd.canonicalize import recover_tool_calls


class DiagnosticError(RuntimeError):
    pass


def _reject_constant(value: str) -> None:
    raise DiagnosticError(f"non-finite JSON constant: {value}")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise DiagnosticError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_json_object(payload: str) -> dict:
    try:
        value = json.loads(
            payload,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, DiagnosticError) as exc:
        raise DiagnosticError("tool arguments are not strict JSON") from exc
    if not isinstance(value, dict):
        raise DiagnosticError("tool arguments are not an object")
    def reject_nonfinite(child: Any) -> None:
        if isinstance(child, float) and not math.isfinite(child):
            raise DiagnosticError("tool arguments contain a non-finite number")
        if isinstance(child, dict):
            for nested in child.values():
                reject_nonfinite(nested)
        elif isinstance(child, list):
            for nested in child:
                reject_nonfinite(nested)

    reject_nonfinite(value)
    return value


def validate_structured_call(call: Any) -> tuple[bool, str]:
    if not isinstance(call, dict) or set(call) != {"id", "type", "function"}:
        return False, "invalid_envelope"
    function = call.get("function")
    if call.get("type") != "function" or not isinstance(function, dict):
        return False, "invalid_envelope"
    if set(function) != {"name", "arguments"}:
        return False, "invalid_function_envelope"
    try:
        arguments = strict_json_object(function.get("arguments"))
    except (DiagnosticError, TypeError):
        return False, "invalid_arguments_json"
    return validate_tool_call_arguments(function.get("name"), arguments)


def validate_recovered_call(call: Any) -> tuple[bool, str]:
    """Validate one parser-recovered ``{name, args}`` candidate."""

    if not isinstance(call, dict) or set(call) != {"name", "args"}:
        return False, "invalid_recovered_envelope"
    return validate_tool_call_arguments(call.get("name"), call.get("args"))


def _load_rows(path: Path) -> list[dict]:
    rows = []
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DiagnosticError(f"invalid JSONL at {path}:{line_number}") from exc
        if not isinstance(row, dict):
            raise DiagnosticError(f"JSONL row is not an object at {path}:{line_number}")
        rows.append(row)
    return rows


def analyze_run(path: Path) -> dict:
    cells: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    snapshot = None
    for row in _load_rows(path):
        snapshot = row.get("snapshot", snapshot)
        presence = row.get("native_tool_schema")
        calls = (row.get("response_message") or {}).get("tool_calls") or []
        cell = cells[(str(snapshot), str(presence))]
        cell["rows"] += 1
        if not calls:
            cell["unstructured_rows"] += 1
            content = (row.get("response_message") or {}).get("content") or ""
            recovered = recover_tool_calls(content)
            stored = row.get("recoverable_calls") or []
            if recovered != stored:
                raise DiagnosticError("stored recoverable calls do not reparse exactly")
            if not recovered:
                cell["no_candidate_rows"] += 1
                continue
            verdicts = [validate_recovered_call(call) for call in recovered]
            if all(valid for valid, _reason in verdicts):
                cell["schema_valid_recoverable_text_rows"] += 1
            else:
                cell["schema_invalid_recoverable_text_rows"] += 1
                for _valid, reason in verdicts:
                    if reason != "valid":
                        cell[f"recovery_invalid_reason__{reason}"] += 1
        else:
            cell["structured_rows"] += 1
            verdicts = [validate_structured_call(call) for call in calls]
            if all(valid for valid, _reason in verdicts):
                cell["schema_valid_structured_rows"] += 1
            else:
                cell["schema_invalid_structured_rows"] += 1
                for _valid, reason in verdicts:
                    if reason != "valid":
                        cell[f"structured_invalid_reason__{reason}"] += 1
    records = []
    for (snapshot_name, presence), counts in sorted(cells.items()):
        structured = counts["structured_rows"]
        valid_any = (
            counts["schema_valid_structured_rows"]
            + counts["schema_valid_recoverable_text_rows"]
        )
        records.append(
            {
                "snapshot": snapshot_name,
                "native_tool_schema": presence,
                **dict(sorted(counts.items())),
                "schema_valid_fraction_among_structured": (
                    counts["schema_valid_structured_rows"] / structured
                    if structured
                    else None
                ),
                "schema_valid_any_route_rows": valid_any,
                "schema_valid_any_route_fraction": valid_any / counts["rows"],
            }
        )
    return {
        "schema_version": "kaetram.routing-validity-decomposition.v2",
        "status": "post_hoc",
        "registered_primary_unchanged": True,
        "cells": records,
    }


def analyze_runs(paths: list[Path]) -> dict:
    cells = []
    snapshots = set()
    all_rows = []
    for path in paths:
        result = analyze_run(path)
        all_rows.extend(_load_rows(path))
        current = {cell["snapshot"] for cell in result["cells"]}
        if snapshots.intersection(current):
            raise DiagnosticError("duplicate snapshot across input runs")
        snapshots.update(current)
        cells.extend(result["cells"])
    sorted_cells = sorted(
        cells, key=lambda cell: (cell["snapshot"], cell["native_tool_schema"])
    )
    by_cell = {
        (cell["snapshot"], cell["native_tool_schema"]): cell
        for cell in sorted_cells
    }
    snapshots_sorted = sorted(snapshots)
    native_contrasts = []
    for snapshot in snapshots_sorted:
        absent = by_cell[(snapshot, "absent")]
        present = by_cell[(snapshot, "present")]
        native_contrasts.append(
            {
                "snapshot": snapshot,
                "absent_valid_any_route": absent["schema_valid_any_route_rows"],
                "present_valid_any_route": present["schema_valid_any_route_rows"],
                "requests_per_level": absent["rows"],
                "effect_rate_difference": (
                    present["schema_valid_any_route_fraction"]
                    - absent["schema_valid_any_route_fraction"]
                ),
            }
        )
    seed_contrasts = []
    sample_indexes = sorted({int(row["sample_index"]) for row in all_rows})
    for snapshot in snapshots_sorted:
        for sample_index in sample_indexes:
            subset = [
                row
                for row in all_rows
                if row["snapshot"] == snapshot and row["sample_index"] == sample_index
            ]
            counts = {"absent": 0, "present": 0}
            denominators = {"absent": 0, "present": 0}
            for row in subset:
                level = row["native_tool_schema"]
                denominators[level] += 1
                message = row.get("response_message") or {}
                calls = message.get("tool_calls") or []
                if calls:
                    valid = all(validate_structured_call(call)[0] for call in calls)
                else:
                    recovered = recover_tool_calls(message.get("content") or "")
                    valid = bool(recovered) and all(
                        validate_recovered_call(call)[0] for call in recovered
                    )
                counts[level] += int(valid)
            if denominators["absent"] != denominators["present"]:
                raise DiagnosticError("unbalanced native-schema sample-index contrast")
            denominator = denominators["absent"]
            seed_contrasts.append(
                {
                    "snapshot": snapshot,
                    "sample_index": sample_index,
                    "requests_per_level": denominator,
                    "absent_valid_any_route": counts["absent"],
                    "present_valid_any_route": counts["present"],
                    "effect_rate_difference": (
                        counts["present"] - counts["absent"]
                    ) / denominator,
                }
            )
    return {
        "schema_version": "kaetram.routing-validity-decomposition.v2",
        "status": "post_hoc",
        "registered_primary_unchanged": True,
        "route_categories_mutually_exclusive": True,
        "cells": sorted_cells,
        "native_schema_valid_any_route_contrasts": native_contrasts,
        "sample_index_native_schema_contrasts": seed_contrasts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    result = analyze_runs(args.results)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out:
        if args.out.exists():
            raise DiagnosticError(f"refusing to overwrite: {args.out}")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
