#!/usr/bin/env python3
"""Locate every parser-recoverable call relative to the model's reasoning span.

The registered primary outcome records that a call was recovered from the
response ``content`` field while the structured field was empty.  It does not
record *where* inside that field the call was written.  Under the non-thinking
Qwen3.5 serving regime the content field is dominated by the reasoning span, so
that distinction changes how the result should be read.

This analyzer is explicitly post-outcome.  It does not recompute, redefine, or
relabel the frozen primary outcome; it only classifies rows the frozen analysis
already labelled.  Run it against the released trigger-incidence artifacts:

    python3 scripts/opd/analyze_reasoning_span_localization.py \
        --artifact research/artifacts/local-trigger-incidence-v2 \
        --output research/results/local-trigger-incidence-v2/reasoning-span-localization.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

OPEN_TAG = "<think>"
CLOSE_TAG = "</think>"
CALL_MARKERS = ("<tool_call>", "<function=")


class LocalizationError(RuntimeError):
    """Raised when an artifact cannot support the localization analysis."""


def _first_call_offset(content: str) -> int | None:
    offsets = [content.find(marker) for marker in CALL_MARKERS]
    found = [offset for offset in offsets if offset >= 0]
    return min(found) if found else None


def _inside_reasoning_span(content: str, offset: int) -> bool:
    """True when the offset sits inside an unclosed reasoning span."""
    prefix = content[:offset]
    return prefix.count(OPEN_TAG) > prefix.count(CLOSE_TAG)


def _residual_text(content: str, reasoning: str) -> str:
    """Response body with the reasoning span and its delimiters removed."""
    residual = content
    if reasoning and reasoning in residual:
        residual = residual.replace(f"{OPEN_TAG}{reasoning}{CLOSE_TAG}", "")
        residual = residual.replace(reasoning, "")
    return residual.replace(OPEN_TAG, "").replace(CLOSE_TAG, "").strip()


def _rows(artifact: Path) -> list[dict]:
    paths = sorted(artifact.glob("runs/*/results.jsonl"))
    if not paths:
        raise LocalizationError(f"no run rows under {artifact}")
    rows: list[dict] = []
    for path in paths:
        with path.open() as handle:
            rows.extend(json.loads(line) for line in handle if line.strip())
    return rows


def analyze(artifact: Path) -> dict:
    rows = _rows(artifact)
    recovered = [row for row in rows if row.get("recovery_opportunity")]

    inside = 0
    outside = 0
    unmarked = 0
    in_reasoning_field = 0
    for row in recovered:
        message = row.get("response_message") or {}
        content = message.get("content") or ""
        reasoning = message.get("reasoning") or ""
        offset = _first_call_offset(content)
        if offset is None:
            unmarked += 1
        elif _inside_reasoning_span(content, offset):
            inside += 1
        else:
            outside += 1
        if any(marker in reasoning for marker in CALL_MARKERS):
            in_reasoning_field += 1

    residual_any = 0
    residual_call = 0
    for row in rows:
        message = row.get("response_message") or {}
        residual = _residual_text(message.get("content") or "", message.get("reasoning") or "")
        if residual:
            residual_any += 1
        if any(marker in residual for marker in CALL_MARKERS):
            residual_call += 1

    by_schema: dict[str, int] = {}
    for row in recovered:
        key = str(row.get("native_tool_schema"))
        by_schema[key] = by_schema.get(key, 0) + 1

    return {
        "schema_version": "kaetram.reasoning-span-localization.v1",
        "status": "post_hoc",
        "registered_primary_unchanged": True,
        "study_id": artifact.name,
        "rows": len(rows),
        "recovered_rows": len(recovered),
        "recovered_inside_reasoning_span": inside,
        "recovered_outside_reasoning_span": outside,
        "recovered_without_call_marker": unmarked,
        "recovered_call_text_in_reasoning_field": in_reasoning_field,
        "recovered_rows_by_native_schema": dict(sorted(by_schema.items())),
        "rows_with_text_after_reasoning_span": residual_any,
        "rows_with_call_marker_after_reasoning_span": residual_call,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    result = analyze(args.artifact)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"

    if args.output is None:
        print(rendered, end="")
        return 0
    if args.check:
        if not args.output.exists() or args.output.read_text() != rendered:
            raise SystemExit(f"stale localization artifact: {args.output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
