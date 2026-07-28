from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.opd.render_trigger_incidence_paper_table_v3 import RenderError, render


REPO = Path(__file__).resolve().parents[2]


def _complete_summary() -> dict:
    return json.loads(
        (
            REPO
            / "research/artifacts/local-trigger-incidence-v2/analysis/analysis-summary.json"
        ).read_text()
    )


def test_renderer_emits_complete_nonconfirmatory_tables() -> None:
    markdown, latex = render(_complete_summary())

    assert "Different-panel V3" in markdown
    assert "+23.0 pp (14/1/5)" in markdown
    assert "non-confirmatory finite-grid" in markdown
    assert r"\label{tab:trigger-incidence-v3}" in latex
    assert "126/240 state--condition groups" in latex
    assert "broad generalization" in latex


def test_renderer_rejects_incomplete_analysis() -> None:
    summary = copy.deepcopy(_complete_summary())
    summary["cells"].pop()

    with pytest.raises(RenderError, match="cell grid"):
        render(summary)


def test_renderer_reports_failed_direction_without_success_language() -> None:
    summary = copy.deepcopy(_complete_summary())
    summary["directional_replication"]["passed"] = False

    markdown, latex = render(summary)
    assert "criterion is not met" in markdown
    assert "criterion is not met" in latex
