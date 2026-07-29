#!/usr/bin/env python3
"""Render evidence-bound TeX macros used by the TMLR manuscript figures.

The visual layer is deliberately thin: every numerical macro is recomputed
from a released analysis or public summary.  The manuscript owns only layout,
labels, and colors.
"""

from __future__ import annotations

import argparse
import json
import math
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "paper" / "tmlr" / "figure-data.tex"
SNAPSHOTS = ("base_2b", "opd_r2_2b", "opd_r3_2b")
SNAPSHOT_NAMES = ("Base", "RoundTwo", "RoundThree")
PARITY_SCHEMA = "kaetram.local-serving-regime-parity-analysis.v1"
PARITY_IDENTITIES = {
    "api_model",
    "chat_template_sha256",
    "checkpoint_sha256",
    "fix_mistral_regex",
    "sampling_contract_sha256",
    "snapshot_lock_sha256",
    "snapshot_tree_sha256",
    "tokenizer_sha256",
    "tokenizer_source_revision",
}


class FigureDataError(RuntimeError):
    """Raised when a public artifact cannot support the expected figure."""


def _read(relative: str) -> dict:
    path = ROOT / relative
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise FigureDataError(f"expected JSON object: {path}")
    return value


def _percentage(value: object) -> str:
    number = Decimal(str(value)) * Decimal("100")
    rounded = number.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if rounded == rounded.to_integral():
        return str(int(rounded))
    return format(rounded, "f")


def _contrast_map(analysis: dict) -> dict[str, dict]:
    rows = analysis.get("registered_contrasts")
    if not isinstance(rows, list):
        raise FigureDataError("analysis is missing registered_contrasts")
    mapped = {
        row["snapshot"]: row
        for row in rows
        if isinstance(row, dict) and row.get("contrast") == "native_tools_main"
    }
    if tuple(mapped) != SNAPSHOTS:
        raise FigureDataError("native-tools contrasts are missing or out of order")
    return mapped


def _posthoc_map(posthoc: dict) -> dict[str, dict]:
    rows = posthoc.get("native_schema_valid_any_route_contrasts")
    if not isinstance(rows, list):
        raise FigureDataError("post-hoc analysis is missing route contrasts")
    mapped = {row["snapshot"]: row for row in rows if isinstance(row, dict)}
    if tuple(mapped) != SNAPSHOTS:
        raise FigureDataError("post-hoc route contrasts are missing or out of order")
    return mapped


def _command(name: str, value: object) -> str:
    return f"\\newcommand{{\\{name}}}{{{value}}}"


def _thousands(value: int) -> str:
    """Render an integer with a LaTeX-safe thousands separator."""
    return f"{value:,}".replace(",", "{,}")


def build_tex_data() -> str:
    recovery = _read("research/results/local-weight-recovery-30m-v1/public-summary.json")
    v1_seed = _read("research/results/local-trigger-incidence-v1/seed-diversity-audit.json")
    v2 = _read("research/artifacts/local-trigger-incidence-v2/analysis/analysis-summary.json")
    v3 = _read("research/artifacts/local-trigger-incidence-v3/analysis/analysis-summary.json")
    posthoc_v2 = _read(
        "research/results/local-trigger-incidence-v2/structured-call-validity-posthoc.json"
    )
    posthoc_v3 = _read(
        "research/results/local-trigger-incidence-v3/structured-call-validity-posthoc.json"
    )
    routing_v2 = _read(
        "research/results/local-live-routing-multi-action-v2/public-summary.json"
    )
    routing_v3 = _read(
        "research/results/local-live-routing-multi-action-v3/public-summary.json"
    )
    span_v2 = _read(
        "research/results/local-trigger-incidence-v2/reasoning-span-localization.json"
    )
    span_v3 = _read(
        "research/results/local-trigger-incidence-v3/reasoning-span-localization.json"
    )
    parity = _read(
        "research/results/local-serving-regime-parity-v1/analysis/analysis-summary.json"
    )

    if recovery.get("recovery_opportunities") != 0:
        raise FigureDataError("recovery factorial no longer has zero opportunities")
    if v1_seed.get("groups_with_multiple_semantic_responses") != 0:
        raise FigureDataError("V1 seed audit no longer supports the frozen diagnosis")
    if v2.get("successful_requests") != 1200 or v3.get("successful_requests") != 1200:
        raise FigureDataError("both interface panels must be complete")
    if routing_v2.get("registered_outcome", {}).get("full_predicate_pass") != 0:
        raise FigureDataError("V2 routing failure must remain preserved")
    if routing_v3.get("outcome", {}).get("full_predicate_pass") != 9:
        raise FigureDataError("V3 fresh routing run is not complete")
    for span in (span_v2, span_v3):
        if not span.get("registered_primary_unchanged"):
            raise FigureDataError("localization must not redefine the primary outcome")
        if span.get("recovered_outside_reasoning_span") != 0:
            raise FigureDataError("localization claim no longer holds for every recovered call")

    span_recovered = span_v2["recovered_rows"] + span_v3["recovered_rows"]
    span_inside = (
        span_v2["recovered_inside_reasoning_span"] + span_v3["recovered_inside_reasoning_span"]
    )
    span_rows = span_v2["rows"] + span_v3["rows"]
    span_residual = (
        span_v2["rows_with_text_after_reasoning_span"]
        + span_v3["rows_with_text_after_reasoning_span"]
    )
    span_residual_calls = (
        span_v2["rows_with_call_marker_after_reasoning_span"]
        + span_v3["rows_with_call_marker_after_reasoning_span"]
    )
    if span_inside != span_recovered:
        raise FigureDataError("pooled localization counts disagree")

    parity_rows = parity.get("checkpoint_results")
    if (
        parity.get("schema_version") != PARITY_SCHEMA
        or parity.get("status") != "complete"
        or not isinstance(parity_rows, list)
        or tuple(row.get("snapshot") for row in parity_rows) != SNAPSHOTS
        or set(parity.get("matched_cross_arm_identities") or []) != PARITY_IDENTITIES
        or not isinstance(parity.get("registered_directional_criterion_passed"), bool)
    ):
        raise FigureDataError("parity analysis identity or checkpoint order is invalid")
    parity_n = parity_rows[0].get("paired_requests")
    if (
        not isinstance(parity_n, int)
        or isinstance(parity_n, bool)
        or parity_n <= 0
        or parity_n % 4
        or any(row.get("paired_requests") != parity_n for row in parity_rows)
    ):
        raise FigureDataError("parity checkpoint denominators are invalid")
    for row in parity_rows:
        for mode in ("thinking", "disabled"):
            counts = [
                row.get(f"{mode}_{route}_count")
                for route in ("structured", "recovery", "no_candidate")
            ]
            if (
                any(not isinstance(value, int) or isinstance(value, bool) or value < 0
                    for value in counts)
                or sum(counts) != parity_n
            ):
                raise FigureDataError("parity route counts do not form a partition")
        for route in ("recovery", "structured"):
            expected_delta = (
                row[f"disabled_{route}_count"] - row[f"thinking_{route}_count"]
            ) / parity_n
            if not math.isclose(
                row.get(f"{route}_rate_difference_disabled_minus_thinking"),
                expected_delta,
                rel_tol=0,
                abs_tol=1e-12,
            ):
                raise FigureDataError("parity rate difference does not match counts")
    directional = all(
        row["recovery_rate_difference_disabled_minus_thinking"] < 0
        for row in parity_rows
    )
    if directional != parity["registered_directional_criterion_passed"]:
        raise FigureDataError("parity directional verdict does not recompute")
    pooled_n = parity.get("pooled_descriptive_result", {}).get("paired_requests")
    if pooled_n != parity_n * len(SNAPSHOTS):
        raise FigureDataError("parity pooled denominator is invalid")
    pilot_states = parity.get("pilot_states_excluded")
    if not isinstance(pilot_states, list) or len(pilot_states) != 3:
        raise FigureDataError("parity pilot exclusion is invalid")

    lines = [
        "% Generated by scripts/opd/render_tmlr_figures.py; do not edit.",
        _command("RecoveryRawGenerations", recovery["raw_generations"]),
        _command("RecoveryEligibleGenerations", recovery["recovery_opportunities"]),
        _command("VOneSeedGroups", v1_seed["state_condition_groups"]),
        _command("VOneDiverseSeedGroups", v1_seed["groups_with_multiple_semantic_responses"]),
        _command("VTwoSuccessfulRequests", _thousands(v2["successful_requests"])),
        _command("VThreeSuccessfulRequests", _thousands(v3["successful_requests"])),
        _command("RoutingVTwoProtocolValid", routing_v2["registered_outcome"]["protocol_valid"]),
        _command("RoutingVTwoFullPass", routing_v2["registered_outcome"]["full_predicate_pass"]),
        _command("RoutingVThreeProtocolValid", routing_v3["outcome"]["protocol_valid"]),
        _command("RoutingVThreeFullPass", routing_v3["outcome"]["full_predicate_pass"]),
        _command("SpanRecoveredCalls", span_recovered),
        _command("SpanInsideReasoning", span_inside),
        _command("SpanTotalRows", _thousands(span_rows)),
        _command("SpanRowsWithTrailingText", span_residual),
        _command("SpanRowsWithTrailingCall", span_residual_calls),
        _command("SpanVTwoRecovered", span_v2["recovered_rows"]),
        _command("SpanVThreeRecovered", span_v3["recovered_rows"]),
        _command("SpanVTwoTrailingText", span_v2["rows_with_text_after_reasoning_span"]),
        _command("SpanVThreeTrailingText", span_v3["rows_with_text_after_reasoning_span"]),
        _command("SpanVTwoBalanced", _thousands(span_v2["rows_with_one_balanced_leading_span"])),
        _command("SpanVTwoRows", _thousands(span_v2["rows"])),
        _command("ParityRequestsPerCheckpoint", parity_n),
        _command("ParityQuarterRequests", parity_n // 4),
        _command("ParityHalfRequests", parity_n // 2),
        _command("ParityThreeQuarterRequests", 3 * parity_n // 4),
        _command("ParityPooledRequests", _thousands(pooled_n)),
        _command("ParityExcludedPilotStates", len(pilot_states)),
        _command(
            "ParityDirectionalVerdict",
            "passed" if directional else "did not pass",
        ),
    ]

    for row, snapshot_name in zip(parity_rows, SNAPSHOT_NAMES, strict=True):
        prefix = f"Parity{snapshot_name}"
        for mode_key, mode_name in (
            ("thinking", "ThinkingOn"),
            ("disabled", "ThinkingOff"),
        ):
            for route_key, route_name in (
                ("structured", "Structured"),
                ("recovery", "BodyOnly"),
                ("no_candidate", "NoCandidate"),
            ):
                lines.append(_command(
                    f"{prefix}{mode_name}{route_name}Count",
                    row[f"{mode_key}_{route_key}_count"],
                ))
        lines.extend([
            _command(
                f"{prefix}BodyOnlyDeltaPP",
                _percentage(row["recovery_rate_difference_disabled_minus_thinking"]),
            ),
            _command(
                f"{prefix}StructuredDeltaPP",
                _percentage(row["structured_rate_difference_disabled_minus_thinking"]),
            ),
        ])

    for version_name, analysis, posthoc in (
        ("VTwo", v2, posthoc_v2),
        ("VThree", v3, posthoc_v3),
    ):
        primary = _contrast_map(analysis)
        valid_any = _posthoc_map(posthoc)
        for snapshot, snapshot_name in zip(SNAPSHOTS, SNAPSHOT_NAMES, strict=True):
            row = primary[snapshot]
            prefix = f"{version_name}{snapshot_name}"
            lines.extend(
                [
                    _command(f"{prefix}Effect", _percentage(row["effect_rate_difference"])),
                    _command(f"{prefix}Positive", row["states_positive"]),
                    _command(f"{prefix}Zero", row["states_zero"]),
                    _command(f"{prefix}Negative", row["states_negative"]),
                    _command(
                        f"{prefix}ValidAnyEffect",
                        _percentage(valid_any[snapshot]["effect_rate_difference"]),
                    ),
                ]
            )

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = build_tex_data()
    if args.check:
        if not args.output.is_file() or args.output.read_text() != expected:
            raise SystemExit(f"stale generated figure data: {args.output}")
        print(f"verified {args.output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(expected)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
