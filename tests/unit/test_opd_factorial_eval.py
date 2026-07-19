from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.opd.factorial_eval import (
    ManifestError,
    build_plan,
    cell_command,
    launch,
    plan_dict,
    validate_cell_result,
)


REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "research" / "experiments" / "opd-2b-factorial.example.json"


def _manifest_copy(tmp_path: Path, mutate=None) -> Path:
    raw = json.loads(MANIFEST.read_text())
    raw["evaluation"]["held_out_registration"] = str(
        REPO / "research" / "experiments" / "heldout-quest.json"
    )
    raw["isolation"]["output_root"] = str(tmp_path / "runs")
    raw["isolation"]["sandbox_root"] = str(tmp_path / "sandboxes")
    if mutate:
        mutate(raw)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw))
    return path


def test_manifest_generates_complete_paired_factorial_with_isolation(tmp_path: Path):
    plan = build_plan(_manifest_copy(tmp_path))
    assert len(plan.cells) == 90
    assert {(c.weight, c.recovery) for c in plan.cells} == {
        (weight, recovery)
        for weight in ("base", "r2", "r3")
        for recovery in (False, True)
    }
    assert {c.personality for c in plan.cells} == {
        "grinder", "completionist", "explorer_tinkerer"
    }
    assert len({c.username for c in plan.cells}) == 90
    assert len({c.server_port for c in plan.cells}) == 90
    assert len({c.sandbox for c in plan.cells}) == 90
    assert len({c.run_dir for c in plan.cells}) == 90
    assert plan.episodes == 1
    assert plan.max_parallel == 6
    assert plan.schedule_algorithm == "sha256-rank-v1"
    assert plan.environment_seed_mechanism == "unavailable"
    for pair_id in {c.pair_id for c in plan.cells}:
        pair = [c for c in plan.cells if c.pair_id == pair_id]
        assert {c.recovery for c in pair} == {False, True}
        assert abs(pair[0].schedule_index - pair[1].schedule_index) == 1
    for start in range(0, len(plan.cells), 6):
        batch = plan.cells[start:start + 6]
        assert len({cell.cluster_id for cell in batch}) == 1
        assert len({cell.batch_index for cell in batch}) == 1
    assert all(
        cell.inference_seed == plan.inference_seeds[cell.replicate - 1]
        for cell in plan.cells
    )


def test_preflight_plan_uses_endpoint_placeholders_and_never_resolves_or_launches(tmp_path: Path):
    secret = "https://signed.example.invalid/v1?token=TOP_SECRET"
    plan = build_plan(_manifest_copy(tmp_path), environ={
        "KAETRAM_QWEN_2B_BASE_ENDPOINT": secret,
    })
    payload = plan_dict(plan)
    assert payload["mode"] == "preflight_only"
    assert payload["tool_schema_source"] == "canonical"
    commands = payload["commands"]
    rendered = json.dumps(payload)
    assert secret not in rendered
    assert "TOP_SECRET" not in rendered
    assert all("--models-env" in command for command in commands)
    assert all("--omit-game-knowledge" in command for command in commands)
    assert all("--sandbox" in command for command in commands)
    assert all("--inference-seed" in command for command in commands)
    assert payload["launchability"] == "blocked_environment_rng_unavailable"


def test_cli_dry_run_has_no_endpoint_game_db_or_directory_side_effects(tmp_path: Path):
    manifest = _manifest_copy(tmp_path)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("KAETRAM_QWEN_2B_")
    }
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "opd" / "factorial_eval.py"),
         str(manifest), "--dry-run"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "Nothing was launched" in result.stdout
    assert not (tmp_path / "runs").exists()
    assert not (tmp_path / "sandboxes").exists()


def test_cell_commands_keep_recovery_out_of_argv(tmp_path: Path):
    plan = build_plan(_manifest_copy(tmp_path))
    pair = [c for c in plan.cells if c.pair_id == "rep01-base-grinder"]
    off = next(cell for cell in pair if not cell.recovery)
    on = next(cell for cell in pair if cell.recovery)
    assert off.recovery is False and on.recovery is True
    assert cell_command(plan, off) == cell_command(
        plan, replace(on, cell_id=off.cell_id, username=off.username,
                      server_port=off.server_port, sandbox=off.sandbox, run_dir=off.run_dir,
                      schedule_index=off.schedule_index),
    )


def test_schedule_is_deterministic_and_seed_sensitive_without_breaking_blocks(tmp_path: Path):
    first = build_plan(_manifest_copy(tmp_path))
    second = build_plan(_manifest_copy(tmp_path))
    assert [cell.cell_id for cell in first.cells] == [cell.cell_id for cell in second.cells]

    changed = build_plan(_manifest_copy(
        tmp_path,
        lambda raw: raw["randomization"].update({"schedule_seed": 20260719}),
    ))
    assert [cell.cell_id for cell in first.cells] != [cell.cell_id for cell in changed.cells]
    for start in range(0, len(changed.cells), 6):
        assert len({cell.cluster_id for cell in changed.cells[start:start + 6]}) == 1


def test_randomization_contract_rejects_missing_environment_seed_attestation(tmp_path: Path):
    def mutate(raw):
        raw["randomization"].pop("environment_seed")

    with pytest.raises(ManifestError, match="environment_seed"):
        build_plan(_manifest_copy(tmp_path, mutate))


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda raw: raw.update({"schema_version": 1}), "schema_version"),
        (
            lambda raw: raw["randomization"].update({"inference_seeds": [11001]}),
            "one seed per replicate",
        ),
        (
            lambda raw: raw["randomization"].update(
                {"inference_seeds": [11001, 11001, 11003, 11004, 11005]}
            ),
            "must be unique",
        ),
        (
            lambda raw: raw["execution"].update({"max_parallel": 5}),
            "one analysis cluster",
        ),
    ],
)
def test_randomization_contract_rejects_unreviewed_shapes(
    tmp_path: Path, mutate, match: str
):
    with pytest.raises(ManifestError, match=match):
        build_plan(_manifest_copy(tmp_path, mutate))


def test_manifest_can_select_frozen_core3_protocol_without_heldout(tmp_path: Path):
    def mutate(raw):
        raw["evaluation"].update({
            "omit_game_knowledge": False,
            "held_out_quest": "",
            "held_out_registration": "",
        })

    plan = build_plan(_manifest_copy(tmp_path, mutate))
    assert not plan.omit_game_knowledge
    assert plan.held_out_quest == ""
    assert all("--held-out-quest" not in cell_command(plan, cell) for cell in plan.cells)


def test_invalid_or_incomplete_factorial_is_rejected(tmp_path: Path):
    def mutate(raw):
        raw["design"]["weights"] = ["base", "r2"]

    with pytest.raises(ManifestError, match="weights"):
        build_plan(_manifest_copy(tmp_path, mutate))


def test_confirmatory_manifest_requires_canonical_tool_schema(tmp_path: Path):
    def mutate(raw):
        raw["evaluation"]["tool_schema_source"] = "live"

    with pytest.raises(ManifestError, match="tool_schema_source='canonical'"):
        build_plan(_manifest_copy(tmp_path, mutate))


def test_confirmatory_manifest_rejects_episode_pseudoreplication(tmp_path: Path):
    def mutate(raw):
        raw["evaluation"]["episodes"] = 10

    with pytest.raises(ManifestError, match="episodes=1"):
        build_plan(_manifest_copy(tmp_path, mutate))


def test_confirmatory_manifest_requires_three_historical_personality_lanes(tmp_path: Path):
    def mutate(raw):
        raw["evaluation"]["personalities"] = ["completionist"]

    with pytest.raises(ManifestError, match="personalities"):
        build_plan(_manifest_copy(tmp_path, mutate))


def test_launch_requires_manifest_switch_and_exact_confirmation_without_popen(tmp_path: Path, monkeypatch):
    plan = build_plan(_manifest_copy(tmp_path))
    called = False

    def forbidden_popen(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("Popen must not be reached")

    monkeypatch.setattr("scripts.opd.factorial_eval.subprocess.Popen", forbidden_popen)
    with pytest.raises(ManifestError, match="allow_launch"):
        launch(plan, confirmation=plan.experiment_id, environ={})
    assert not called

    enabled = replace(plan, allow_launch=True)
    with pytest.raises(ManifestError, match="confirm-launch"):
        launch(enabled, confirmation="wrong", environ={})
    assert not called


def test_launch_requires_all_endpoint_environment_variables(tmp_path: Path, monkeypatch):
    plan = replace(build_plan(_manifest_copy(tmp_path)), allow_launch=True)
    monkeypatch.setattr(
        "scripts.opd.factorial_eval.require_environment_seed_capability", lambda _plan: None
    )
    monkeypatch.setattr(
        "scripts.opd.factorial_eval.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("Popen must not be reached"),
    )
    with pytest.raises(ManifestError, match="missing endpoint"):
        launch(plan, confirmation=plan.experiment_id, environ={})


def test_launch_sets_canonical_schema_recovery_and_respects_parallel_cap(tmp_path: Path, monkeypatch):
    plan = replace(build_plan(_manifest_copy(tmp_path)), allow_launch=True)
    secret = "https://signed.example.invalid/v1?token=TOP_SECRET"
    endpoint_env = {
        "KAETRAM_QWEN_2B_BASE_ENDPOINT": secret,
        "KAETRAM_QWEN_2B_R2_ENDPOINT": secret,
        "KAETRAM_QWEN_2B_R3_ENDPOINT": secret,
    }
    captured = []
    active = 0
    maximum_active = 0

    class FakeProcess:
        def __init__(self, args, kwargs):
            nonlocal active, maximum_active
            self.args = args
            self.kwargs = kwargs
            self.returncode = None
            active += 1
            maximum_active = max(maximum_active, active)
            captured.append(self)

        def wait(self):
            nonlocal active
            if self.returncode is None:
                self.returncode = 0
                active -= 1
            return self.returncode

        def poll(self):
            return self.returncode

        def terminate(self):
            self.wait()

    monkeypatch.setattr(
        "scripts.opd.factorial_eval.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(args, kwargs),
    )
    monkeypatch.setattr(
        "scripts.opd.factorial_eval.require_environment_seed_capability", lambda _plan: None
    )
    monkeypatch.setattr("scripts.opd.factorial_eval.validate_cell_result", lambda *args: None)
    assert launch(plan, confirmation=plan.experiment_id, environ=endpoint_env) == 0
    assert len(captured) == 90
    assert maximum_active == plan.max_parallel == 6
    assert all(p.kwargs["env"]["KAETRAM_TOOL_SCHEMA_SOURCE"] == "canonical" for p in captured)
    assert sum("KAETRAM_TOOL_RECOVERY" in p.kwargs["env"] for p in captured) == 45
    assert all(secret not in json.dumps(p.args[0]) for p in captured)


def test_confirmatory_launch_fails_closed_when_environment_rng_is_unavailable(
    tmp_path: Path, monkeypatch
):
    plan = replace(build_plan(_manifest_copy(tmp_path)), allow_launch=True)
    monkeypatch.setattr(
        "scripts.opd.factorial_eval.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("Popen must not be reached"),
    )
    endpoints = {
        cell.endpoint_env: "https://signed.example.invalid/v1"
        for cell in plan.cells
    }
    with pytest.raises(ManifestError, match="environment RNG seed is unavailable"):
        launch(plan, confirmation=plan.experiment_id, environ=endpoints)


def test_cell_result_validation_rejects_failed_or_misattributed_artifacts(tmp_path: Path):
    plan = build_plan(_manifest_copy(tmp_path))
    cell = plan.cells[0]
    result_path = Path(cell.run_dir) / cell.cell_id / "results.json"
    result_path.parent.mkdir(parents=True)

    def write_result(*, status="ok", model=None):
        result_path.write_text(json.dumps({
            "meta": {
                "model": model or cell.cell_id,
                "scenario": plan.scenario,
                "total_episodes": 1,
                "ok_episodes": int(status == "ok"),
                "tool_schema_source": plan.tool_schema_source,
                "include_game_knowledge": not plan.omit_game_knowledge,
                "held_out_quest": plan.held_out_quest,
                "inference_seed": cell.inference_seed,
                "factorial_schedule_algorithm": plan.schedule_algorithm,
                "factorial_schedule_seed": plan.schedule_seed,
                "factorial_schedule_index": cell.schedule_index,
                "factorial_batch_index": cell.batch_index,
                "factorial_cluster_id": cell.cluster_id,
                "factorial_pair_id": cell.pair_id,
                "environment_seed_mechanism": plan.environment_seed_mechanism,
                "environment_seed": None,
                "environment_seed_reason": plan.environment_seed_reason,
            },
            "episodes": [{"episode": 1, "status": status}],
        }))

    write_result()
    validate_cell_result(plan, cell)

    write_result(status="no_log")
    with pytest.raises(ManifestError, match="failed episode"):
        validate_cell_result(plan, cell)

    write_result(model="different-cell")
    with pytest.raises(ManifestError, match="metadata mismatch"):
        validate_cell_result(plan, cell)


def test_cell_result_validation_accepts_frozen_core3_empty_heldout(tmp_path: Path):
    def mutate(raw):
        raw["evaluation"].update({
            "omit_game_knowledge": False,
            "held_out_quest": "",
            "held_out_registration": "",
        })

    plan = build_plan(_manifest_copy(tmp_path, mutate))
    cell = plan.cells[0]
    result_path = Path(cell.run_dir) / cell.cell_id / "results.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(json.dumps({
        "meta": {
            "model": cell.cell_id,
            "scenario": plan.scenario,
            "total_episodes": 1,
            "ok_episodes": 1,
            "tool_schema_source": plan.tool_schema_source,
            "include_game_knowledge": True,
            "held_out_quest": "",
            "inference_seed": cell.inference_seed,
            "factorial_schedule_algorithm": plan.schedule_algorithm,
            "factorial_schedule_seed": plan.schedule_seed,
            "factorial_schedule_index": cell.schedule_index,
            "factorial_batch_index": cell.batch_index,
            "factorial_cluster_id": cell.cluster_id,
            "factorial_pair_id": cell.pair_id,
            "environment_seed_mechanism": plan.environment_seed_mechanism,
            "environment_seed": None,
            "environment_seed_reason": plan.environment_seed_reason,
        },
        "episodes": [{"episode": 1, "status": "ok"}],
    }))

    validate_cell_result(plan, cell)
