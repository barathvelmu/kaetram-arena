#!/usr/bin/env python3
"""Post-hoc schema-validity diagnostic for structured tool-call envelopes."""

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

from tool_surface import MODEL_VISIBLE_TOOL_DEFINITIONS


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


def _schemas() -> dict[str, dict]:
    return {
        tool["function"]["name"]: tool["function"]["parameters"]
        for tool in MODEL_VISIBLE_TOOL_DEFINITIONS
    }


def validate_structured_call(call: Any) -> tuple[bool, str]:
    if not isinstance(call, dict) or set(call) != {"id", "type", "function"}:
        return False, "invalid_envelope"
    function = call.get("function")
    if call.get("type") != "function" or not isinstance(function, dict):
        return False, "invalid_envelope"
    if set(function) != {"name", "arguments"}:
        return False, "invalid_function_envelope"
    schema = _schemas().get(function.get("name"))
    if schema is None:
        return False, "unknown_function"
    try:
        arguments = strict_json_object(function.get("arguments"))
    except (DiagnosticError, TypeError):
        return False, "invalid_arguments_json"
    properties = schema["properties"]
    unknown = set(arguments) - set(properties)
    if unknown:
        return False, "unknown_argument"
    missing = set(schema.get("required", [])) - set(arguments)
    if missing:
        return False, "missing_required_argument"
    for name, value in arguments.items():
        contract = properties[name]
        expected = contract["type"]
        valid_type = {
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
        }.get(expected, False)
        if not valid_type:
            return False, "wrong_argument_type"
        if "enum" in contract and value not in contract["enum"]:
            return False, "argument_outside_enum"
        if "minimum" in contract and value < contract["minimum"]:
            return False, "argument_below_minimum"
        if "maximum" in contract and value > contract["maximum"]:
            return False, "argument_above_maximum"
    return True, "valid"


def analyze_run(path: Path) -> dict:
    cells: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    snapshot = None
    for line_number, line in enumerate(path.read_text().splitlines(), start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DiagnosticError(f"invalid JSONL at {path}:{line_number}") from exc
        snapshot = row.get("snapshot", snapshot)
        presence = row.get("native_tool_schema")
        calls = (row.get("response_message") or {}).get("tool_calls") or []
        cell = cells[(str(snapshot), str(presence))]
        cell["rows"] += 1
        if not calls:
            cell["unstructured_rows"] += 1
            continue
        cell["structured_rows"] += 1
        verdicts = [validate_structured_call(call) for call in calls]
        if all(valid for valid, _reason in verdicts):
            cell["schema_valid_structured_rows"] += 1
        else:
            cell["schema_invalid_structured_rows"] += 1
            for _valid, reason in verdicts:
                if reason != "valid":
                    cell[f"invalid_reason__{reason}"] += 1
    records = []
    for (snapshot_name, presence), counts in sorted(cells.items()):
        structured = counts["structured_rows"]
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
            }
        )
    return {
        "schema_version": "kaetram.structured-call-validity-diagnostic.v1",
        "status": "post_hoc",
        "registered_primary_unchanged": True,
        "cells": records,
    }


def analyze_runs(paths: list[Path]) -> dict:
    cells = []
    snapshots = set()
    for path in paths:
        result = analyze_run(path)
        current = {cell["snapshot"] for cell in result["cells"]}
        if snapshots.intersection(current):
            raise DiagnosticError("duplicate snapshot across input runs")
        snapshots.update(current)
        cells.extend(result["cells"])
    return {
        "schema_version": "kaetram.structured-call-validity-diagnostic.v1",
        "status": "post_hoc",
        "registered_primary_unchanged": True,
        "cells": sorted(
            cells, key=lambda cell: (cell["snapshot"], cell["native_tool_schema"])
        ),
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
