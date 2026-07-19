from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.opd.factorial_analyze import _sign_flip_p, build_analysis
from scripts.opd.factorial_eval import ManifestError, build_plan


REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "research" / "experiments" / "opd-2b-factorial.example.json"


def _plan(tmp_path: Path, replicates: int = 5):
    raw = json.loads(MANIFEST.read_text())
    raw["design"]["replicates"] = replicates
    raw["analysis"]["sample_size"]["planned_replicates"] = replicates
    raw["evaluation"]["held_out_registration"] = str(
        REPO / "research" / "experiments" / "heldout-quest.json"
    )
    raw["isolation"]["output_root"] = str(tmp_path / "runs")
    raw["isolation"]["sandbox_root"] = str(tmp_path / "sandboxes")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(raw))
    return build_plan(manifest)


def _write_results(plan, *, omit_cell: str = "", alternate_sha_cell: str = "") -> None:
    weight_offset = {"base": 0, "r2": 2, "r3": 4}
    for cell in plan.cells:
        if cell.cell_id == omit_cell:
            continue
        value = cell.replicate + weight_offset[cell.weight] + int(cell.recovery)
        path = Path(cell.run_dir) / cell.cell_id / "results.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "meta": {
                "model": cell.cell_id,
                "endpoint": f"env:{cell.endpoint_env}",
                "scenario": plan.scenario,
                "total_episodes": 1,
                "ok_episodes": 1,
                "tool_schema_source": plan.tool_schema_source,
                "include_game_knowledge": not plan.omit_game_knowledge,
                "held_out_quest": plan.held_out_quest,
                "git_sha": "different" if cell.cell_id == alternate_sha_cell else "abc123",
            },
            "episodes": [{
                "episode": 1,
                "status": "ok",
                "returncode": 0,
                "turns_played": 100,
                "core3_stages_advanced": value,
            }],
        }))


def test_analysis_uses_replicates_not_personality_cells_as_n(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    _write_results(plan)
    analysis = build_analysis(plan, "core3_stages_advanced")
    assert analysis["n_cells"] == 90
    assert analysis["n_cluster_arms"] == 30
    assert analysis["n_replicates"] == 5
    recovery_r3 = next(
        effect for effect in analysis["primary_estimands"]
        if effect["name"] == "recovery_on_minus_off_r3"
    )
    assert recovery_r3["paired_deltas"] == [3.0] * 5
    assert recovery_r3["mean_delta"] == 3.0
    assert recovery_r3["exact_two_sided_sign_flip_p"] == 0.0625
    assert recovery_r3["bonferroni_adjusted_p"] == 0.4375
    assert recovery_r3["inference_status"] == "preliminary_only"
    r3_base = next(
        effect for effect in analysis["primary_estimands"]
        if effect["name"] == "r3_minus_base_recovery_off"
    )
    assert r3_base["paired_deltas"] == [12.0] * 5
    recovery_main = next(
        effect for effect in analysis["factorial_main_effects"]
        if effect["name"] == "recovery_main_effect"
    )
    assert recovery_main["paired_deltas"] == [3.0] * 5
    interaction = next(
        effect for effect in analysis["primary_estimands"]
        if effect["name"] == "r3_minus_base_recovery_interaction"
    )
    assert interaction["paired_deltas"] == [0.0] * 5
    assert interaction["exact_two_sided_sign_flip_p"] == 1.0
    assert analysis["sample_size_contract"]["status"] == "pilot_preliminary"


def test_one_replicate_is_explicitly_preliminary_only(tmp_path: Path) -> None:
    plan = _plan(tmp_path, replicates=1)
    _write_results(plan)
    analysis = build_analysis(plan, "core3_stages_advanced")
    assert all(effect["inference_status"] == "preliminary_only" for effect in analysis["effects"])
    assert all(effect["exact_two_sided_sign_flip_p"] is None for effect in analysis["effects"])


def test_analysis_rejects_unregistered_metric_override(tmp_path: Path) -> None:
    plan = _plan(tmp_path, replicates=1)
    _write_results(plan)
    with pytest.raises(ManifestError, match="does not match preregistered"):
        build_analysis(plan, "held_out_quest_completed_delta")


def test_analysis_fails_closed_on_missing_cell(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    _write_results(plan, omit_cell=plan.cells[-1].cell_id)
    with pytest.raises(ManifestError, match="no valid results artifact"):
        build_analysis(plan, "core3_stages_advanced")


def test_analysis_rejects_mixed_source_commits(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    _write_results(plan, alternate_sha_cell=plan.cells[-1].cell_id)
    with pytest.raises(ManifestError, match="multiple source commits"):
        build_analysis(plan, "core3_stages_advanced")


def test_analysis_rejects_missing_metric(tmp_path: Path) -> None:
    plan = _plan(tmp_path, replicates=1)
    _write_results(plan)
    first = plan.cells[0]
    path = Path(first.run_dir) / first.cell_id / "results.json"
    result = json.loads(path.read_text())
    del result["episodes"][0]["core3_stages_advanced"]
    path.write_text(json.dumps(result))
    with pytest.raises(ManifestError, match="must be a finite numeric value"):
        build_analysis(plan)


def test_analysis_rejects_out_of_range_primary_metric(tmp_path: Path) -> None:
    plan = _plan(tmp_path, replicates=1)
    _write_results(plan)
    first = plan.cells[0]
    path = Path(first.run_dir) / first.cell_id / "results.json"
    result = json.loads(path.read_text())
    result["episodes"][0]["core3_stages_advanced"] = 11
    path.write_text(json.dumps(result))
    with pytest.raises(ManifestError, match=r"integer in \[0, 10\]"):
        build_analysis(plan)


def test_exact_sign_flip_scales_past_twenty_replicates() -> None:
    assert _sign_flip_p([1.0] * 25) == 2 / (2 ** 25)
