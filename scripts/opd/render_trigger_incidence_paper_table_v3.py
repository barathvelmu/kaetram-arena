#!/usr/bin/env python3
"""Audit V3 and deterministically render its manuscript result tables."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.opd.audit_trigger_incidence_artifact_v3 import audit_artifact  # noqa: E402


SNAPSHOTS = ("base_2b", "opd_r2_2b", "opd_r3_2b")
SNAPSHOT_LABELS = {
    "base_2b": "Base",
    "opd_r2_2b": "Round 2",
    "opd_r3_2b": "Round 3",
}
CONDITIONS = (
    "python-docs_no-tools",
    "python-docs_native-tools",
    "canonical-docs_no-tools",
    "canonical-docs_native-tools",
)
CONTRASTS = ("native_tools_main", "canonical_docs_main", "interaction")


class RenderError(ValueError):
    """The audited summary does not have the frozen complete-grid shape."""


def _signed_pp(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RenderError("contrast effect is not numeric")
    return f"{float(value) * 100:+.1f}"


def _index_summary(summary: dict) -> tuple[dict[tuple[str, str], dict], dict[tuple[str, str], dict]]:
    if (
        summary.get("analysis_status") != "complete"
        or summary.get("scheduled_requests") != 1200
        or summary.get("successful_requests") != 1200
        or summary.get("failed_requests") != 0
    ):
        raise RenderError("V3 analysis is not a complete 1,200-request result")
    cells = summary.get("cells")
    contrasts = summary.get("registered_contrasts")
    if not isinstance(cells, list) or not isinstance(contrasts, list):
        raise RenderError("V3 analysis tables are missing")
    cell_index = {(row.get("snapshot"), row.get("condition_id")): row for row in cells}
    contrast_index = {
        (row.get("snapshot"), row.get("contrast")): row for row in contrasts
    }
    if set(cell_index) != {(s, c) for s in SNAPSHOTS for c in CONDITIONS}:
        raise RenderError("V3 cell grid is incomplete or duplicated")
    if set(contrast_index) != {(s, c) for s in SNAPSHOTS for c in CONTRASTS}:
        raise RenderError("V3 contrast grid is incomplete or duplicated")
    return cell_index, contrast_index


def render(summary: dict) -> tuple[str, str]:
    cells, contrasts = _index_summary(summary)
    heterogeneity = summary.get("registered_seed_heterogeneity", {})
    varied = heterogeneity.get("groups_with_primary_outcome_heterogeneity")
    groups = heterogeneity.get("state_condition_groups")
    passed = summary.get("directional_replication", {}).get("passed")
    if (
        isinstance(varied, bool)
        or not isinstance(varied, int)
        or groups != 240
        or not isinstance(passed, bool)
    ):
        raise RenderError("V3 seed or directional summary is incomplete")

    md = [
        "### Different-panel V3 recovery-opportunity incidence",
        "",
        "| Weights | Python / none | Python / native | Canonical / none | Canonical / native |",
        "|---|---:|---:|---:|---:|",
    ]
    tex_rows = []
    for snapshot in SNAPSHOTS:
        values = []
        for condition in CONDITIONS:
            row = cells[(snapshot, condition)]
            opportunities = row.get("recovery_opportunities")
            successful = row.get("successful_requests")
            rate = row.get("opportunity_rate")
            if (
                isinstance(opportunities, bool)
                or not isinstance(opportunities, int)
                or successful != 100
                or isinstance(rate, bool)
                or not isinstance(rate, (int, float))
            ):
                raise RenderError("V3 cell contains an invalid count or rate")
            values.append(f"{opportunities}/100 ({float(rate) * 100:.1f}%)")
        label = SNAPSHOT_LABELS[snapshot]
        md.append(f"| {label} | " + " | ".join(values) + " |")
        tex_rows.append(f"{label} & " + " & ".join(values) + r" \\")

    md.extend(
        [
            "",
            "### Registered finite-grid contrasts",
            "",
            (
                f"Seed check: {groups}/{groups} state-condition groups are complete; "
                f"{varied}/{groups} vary in the primary outcome across five effective "
                "paired seeds."
            ),
            (
                "The inherited all-checkpoint directional criterion is "
                + ("met." if passed else "not met.")
            ),
            "",
            "| Weights | Native schema | Canonical docs | Interaction |",
            "|---|---:|---:|---:|",
        ]
    )
    contrast_tex_rows = []
    for snapshot in SNAPSHOTS:
        values = []
        for contrast in CONTRASTS:
            row = contrasts[(snapshot, contrast)]
            effect = _signed_pp(row.get("effect_rate_difference"))
            triple = "/".join(
                str(row.get(key))
                for key in ("states_positive", "states_negative", "states_zero")
            )
            values.append(f"{effect} ({triple})")
        label = SNAPSHOT_LABELS[snapshot]
        md.append(f"| {label} | " + " | ".join(f"{value} pp" for value in values) + " |")
        contrast_tex_rows.append(f"{label} & " + " & ".join(values) + r" \\")
    md.extend(
        [
            "",
            "Cells report opportunities/successful requests (rate). Contrasts are paired",
            "rate differences in percentage points; parenthetical values are states with",
            "positive/negative/zero effects. This is a non-confirmatory finite-grid",
            "extension on a different retained historical state panel.",
            "",
        ]
    )

    verdict = "met" if passed else "not met"
    tex = "\n".join(
        [
            "% Deterministically rendered from the independently audited V3 artifact.",
            r"\begin{table}[H]",
            r"\centering",
            r"\small",
            r"\caption{Recovery-opportunity incidence on the different-panel V3 finite grid.",
            r"Cells show opportunities/successful requests (rate). Contrasts are paired rate",
            r"differences in percentage points; $+/-/0$ counts states with positive, negative,",
            f"or zero effects. {varied}/{groups} state--condition groups vary in the primary",
            f"outcome across five effective paired seeds; the inherited directional criterion is {verdict}.",
            r"This extension is non-confirmatory and does not establish broad generalization.}",
            r"\label{tab:trigger-incidence-v3}",
            r"\begin{tabular}{lrrrr}",
            r"\toprule",
            r"Weights & Python / none & Python / native & Canonical / none & Canonical / native \\",
            r"\midrule",
            *tex_rows,
            r"\bottomrule",
            r"\end{tabular}",
            "",
            r"\vspace{3pt}",
            r"\begin{tabular}{lrrr}",
            r"\toprule",
            r"Weights & Native schema & Canonical docs & Interaction \\",
            r"\midrule",
            *contrast_tex_rows,
            r"\bottomrule",
            r"\end{tabular}",
            r"\end{table}",
            "",
        ]
    )
    return "\n".join(md), tex


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    audit_artifact(args.artifact)
    summary = json.loads(
        (args.artifact / "analysis" / "analysis-summary.json").read_text()
    )
    markdown, latex = render(summary)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "paper-table-public.md").write_text(markdown)
    (args.out_dir / "paper-table-public.tex").write_text(latex)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
