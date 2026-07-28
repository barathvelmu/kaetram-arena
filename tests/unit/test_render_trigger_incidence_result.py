import copy
import json

import pytest

from scripts.opd import render_trigger_incidence_result as renderer


def _summary():
    snapshots = ("base_2b", "opd_r2_2b", "opd_r3_2b")
    cells = []
    for snapshot_index, snapshot in enumerate(snapshots):
        for condition_index, (condition_id, _label) in enumerate(
            renderer.CONDITION_COLUMNS
        ):
            count = 5 * (snapshot_index + condition_index)
            cells.append(
                {
                    "snapshot": snapshot,
                    "condition_id": condition_id,
                    "successful_requests": 100,
                    "recovery_opportunities": count,
                    "opportunity_rate": count / 100,
                }
            )
    contrast_values = {
        "native_tools_main": 0.05,
        "canonical_docs_main": 0.10,
        "interaction": 0.0,
    }
    contrasts = []
    for snapshot in snapshots:
        for contrast_id, _label in renderer.CONTRAST_COLUMNS:
            contrasts.append(
                {
                    "snapshot": snapshot,
                    "contrast": contrast_id,
                    "effect_rate_difference": contrast_values[contrast_id],
                    "finite_grid_states": 20,
                    "states_positive": 5,
                    "states_negative": 3,
                    "states_zero": 12,
                }
            )
    return {
        "schema_version": renderer.ANALYSIS_SCHEMA,
        "analysis_status": "complete",
        "failed_requests": 0,
        "input_runs": [{"snapshot": item} for item in reversed(snapshots)],
        "cells": cells,
        "registered_contrasts": contrasts,
    }


def test_render_tables_orders_registered_snapshots_and_reports_counts():
    markdown, latex = renderer.render_tables(_summary())

    assert markdown.index("| Base |") < markdown.index("| Round 2 |")
    assert markdown.index("| Round 2 |") < markdown.index("| Round 3 |")
    assert "0/100 (0.0%)" in markdown
    assert "+5.0 pp (5/3/12)" in markdown
    assert "\\label{tab:trigger-incidence}" in latex
    assert "0/100 (0.0\\%)" in latex
    assert "+5.0 (5/3/12)" in latex


def test_render_tables_collapse_identical_seed_replays():
    summary = _summary()
    collapsed_cells = []
    for cell in summary["cells"]:
        count = cell["recovery_opportunities"]
        collapsed_cells.append(
            {
                "snapshot": cell["snapshot"],
                "condition_id": cell["condition_id"],
                "state_outputs": 20,
                "outcome_stable_states": 20,
                "recovery_opportunity_states": count // 5,
                "opportunity_rate": cell["opportunity_rate"],
            }
        )
    seed_audit = {
        "schema_version": renderer.SEED_AUDIT_SCHEMA,
        "study_id": summary.get("study_id"),
        "state_condition_groups": 240,
        "groups_with_identical_semantic_responses": 240,
        "groups_with_multiple_semantic_responses": 0,
        "collapsed_cells": collapsed_cells,
    }

    markdown, latex = renderer.render_tables(summary, seed_audit)

    assert "opportunity states/state outputs" in markdown
    assert "identical semantic" in latex


def test_render_tables_report_effective_seed_replication():
    summary = _summary()
    summary["registered_seed_heterogeneity"] = {
        "status": "complete",
        "state_condition_groups": 240,
        "groups_with_multiple_semantic_responses": 240,
        "groups_with_primary_outcome_heterogeneity": 17,
        "minimum_unique_semantic_responses_per_group": 3,
        "maximum_unique_semantic_responses_per_group": 5,
    }
    summary["directional_replication"] = {
        "criterion": "Every native-tools contrast must be strictly positive.",
        "status": "evaluated",
        "native_tools_effects": {
            snapshot: 0.05 for snapshot in renderer.EXPECTED_SNAPSHOTS
        },
        "passed": True,
    }

    markdown, latex = renderer.render_tables(summary)

    assert "240/240 state-condition groups had multiple semantic responses" in markdown
    assert "17/240 varied in the primary outcome" in markdown
    assert "directional criterion was met" in markdown
    assert "17/240 state--condition" in latex
    assert "direction criterion is met" in latex


def test_render_tables_reject_inconsistent_replication_verdict():
    summary = _summary()
    summary["registered_seed_heterogeneity"] = {
        "status": "complete",
        "state_condition_groups": 240,
        "groups_with_multiple_semantic_responses": 240,
        "groups_with_primary_outcome_heterogeneity": 17,
        "minimum_unique_semantic_responses_per_group": 3,
        "maximum_unique_semantic_responses_per_group": 5,
    }
    summary["directional_replication"] = {
        "criterion": "Every native-tools contrast must be strictly positive.",
        "status": "evaluated",
        "native_tools_effects": {
            snapshot: 0.05 for snapshot in renderer.EXPECTED_SNAPSHOTS
        },
        "passed": False,
    }

    with pytest.raises(renderer.RenderError, match="annotations are invalid"):
        renderer.render_tables(summary)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda value: value.update(analysis_status="incomplete"), "complete analysis"),
        (lambda value: value.update(failed_requests=1), "zero failed"),
        (lambda value: value["cells"].pop(), "expected 12 cell"),
        (
            lambda value: value["registered_contrasts"][0].update(states_zero=13),
            "invalid trigger-incidence contrast",
        ),
        (
            lambda value: value["cells"][0].update(opportunity_rate=0.5),
            "invalid trigger-incidence cell",
        ),
    ],
)
def test_invalid_summaries_fail_closed(tmp_path, mutate, message):
    summary = _summary()
    mutate(summary)
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(summary))

    if summary.get("analysis_status") != "complete" or summary.get("failed_requests"):
        with pytest.raises(renderer.RenderError, match=message):
            renderer._load_summary(path)
    else:
        with pytest.raises(renderer.RenderError, match=message):
            renderer.render_tables(summary)


def test_cli_is_create_only(tmp_path):
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(_summary()))
    markdown_path = tmp_path / "table.md"
    latex_path = tmp_path / "table.tex"

    assert (
        renderer.main(
            [
                "--summary",
                str(summary_path),
                "--markdown-out",
                str(markdown_path),
                "--latex-out",
                str(latex_path),
            ]
        )
        == 0
    )
    assert markdown_path.is_file()
    assert latex_path.is_file()
    with pytest.raises(renderer.RenderError, match="refusing to overwrite"):
        renderer.main(
            [
                "--summary",
                str(summary_path),
                "--markdown-out",
                str(markdown_path),
                "--latex-out",
                str(latex_path),
            ]
        )


def test_duplicate_snapshot_is_rejected():
    summary = copy.deepcopy(_summary())
    summary["input_runs"][0]["snapshot"] = summary["input_runs"][1]["snapshot"]

    with pytest.raises(renderer.RenderError, match="invalid snapshot"):
        renderer.render_tables(summary)


def test_unregistered_cell_identity_is_rejected():
    summary = copy.deepcopy(_summary())
    summary["cells"][0]["condition_id"] = "invented-condition"

    with pytest.raises(renderer.RenderError, match="cell identities"):
        renderer.render_tables(summary)


def test_unregistered_snapshot_is_rejected():
    summary = copy.deepcopy(_summary())
    summary["input_runs"][0]["snapshot"] = "invented-snapshot"

    with pytest.raises(renderer.RenderError, match="registered snapshots"):
        renderer.render_tables(summary)


def test_renderer_rejects_nonfinite_or_cell_inconsistent_effects():
    summary = _summary()
    summary["cells"][0]["opportunity_rate"] = float("nan")
    with pytest.raises(renderer.RenderError, match="invalid trigger-incidence cell"):
        renderer.render_tables(summary)

    summary = _summary()
    summary["registered_contrasts"][0]["effect_rate_difference"] = 0.99
    with pytest.raises(renderer.RenderError, match="differs from analysis cells"):
        renderer.render_tables(summary)


def test_renderer_rejects_forged_request_and_collapsed_counts():
    summary = _summary()
    summary["cells"][0]["successful_requests"] = 99
    with pytest.raises(renderer.RenderError, match="request count"):
        renderer.render_tables(summary)

    summary = _summary()
    collapsed_cells = [
        {
            "snapshot": cell["snapshot"],
            "condition_id": cell["condition_id"],
            "state_outputs": 20,
            "outcome_stable_states": 20,
            "recovery_opportunity_states": cell["recovery_opportunities"] // 5,
            "opportunity_rate": cell["opportunity_rate"],
        }
        for cell in summary["cells"]
    ]
    collapsed_cells[0]["state_outputs"] = 19
    seed_audit = {
        "schema_version": renderer.SEED_AUDIT_SCHEMA,
        "study_id": summary.get("study_id"),
        "state_condition_groups": 240,
        "groups_with_identical_semantic_responses": 240,
        "groups_with_multiple_semantic_responses": 0,
        "collapsed_cells": collapsed_cells,
    }
    with pytest.raises(renderer.RenderError, match="state count"):
        renderer.render_tables(summary, seed_audit)


def test_markdown_contrast_table_is_contiguous():
    summary = _summary()
    summary["registered_seed_heterogeneity"] = {
        "status": "complete",
        "state_condition_groups": 240,
        "groups_with_multiple_semantic_responses": 240,
        "groups_with_primary_outcome_heterogeneity": 17,
        "minimum_unique_semantic_responses_per_group": 3,
        "maximum_unique_semantic_responses_per_group": 5,
    }
    summary["directional_replication"] = {
        "criterion": "Every native-tools contrast must be strictly positive.",
        "status": "evaluated",
        "native_tools_effects": {
            snapshot: 0.05 for snapshot in renderer.EXPECTED_SNAPSHOTS
        },
        "passed": True,
    }
    markdown, _latex = renderer.render_tables(summary)
    assert (
        "| Weights | Native schema | Canonical docs | Interaction |\n"
        "|---|---:|---:|---:|\n"
        "| Base |"
    ) in markdown
