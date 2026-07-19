from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from inference_seed import MAX_INFERENCE_SEED, derive_request_seed, validate_inference_seed
from eval_harness import run_episode


REPO = Path(__file__).resolve().parents[2]
ENDPOINTS = (
    "finetune/serve_modal_2b.py",
    "finetune/serve_modal_2b_opd_r2.py",
    "finetune/serve_modal_2b_opd_r3.py",
)


def test_request_seed_derivation_is_stable_bounded_and_turn_specific():
    first = derive_request_seed(11001, 1, 1)
    assert first == derive_request_seed(11001, 1, 1)
    assert 0 <= first <= MAX_INFERENCE_SEED
    assert len({
        derive_request_seed(11001, 1, 1),
        derive_request_seed(11001, 1, 2),
        derive_request_seed(11001, 2, 1),
        derive_request_seed(11002, 1, 1),
    }) == 4


@pytest.mark.parametrize("value", [True, -1, MAX_INFERENCE_SEED + 1, "11001"])
def test_inference_seed_validation_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        validate_inference_seed(value)


def test_play_qwen_passes_derived_seed_as_openai_request_field():
    source = (REPO / "play_qwen.py").read_text()
    ast.parse(source)
    assert 'completion_kwargs["seed"] = derive_request_seed(' in source
    assert "client.chat.completions.create(**completion_kwargs)" in source


def test_eval_harness_propagates_seed_and_provenance_to_play_qwen(
    tmp_path: Path, monkeypatch
):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("eval_harness.subprocess.run", fake_run)
    provenance = {
        "factorial_schedule_algorithm": "sha256-rank-v1",
        "factorial_schedule_seed": 20260718,
        "factorial_schedule_index": 0,
        "factorial_batch_index": 0,
        "factorial_cluster_id": "rep01-base",
        "factorial_pair_id": "rep01-base-grinder",
        "environment_seed_mechanism": "unavailable",
        "environment_seed": None,
        "environment_seed_reason": "Kaetram gameplay RNG is unavailable",
    }
    run_dir = tmp_path / "run"
    run_episode(
        project_dir=str(REPO),
        endpoint="https://example.invalid/v1",
        model_api_name="2b-base",
        sandbox=str(tmp_path / "sandbox"),
        duration_seconds=1,
        system_prompt_file=str(tmp_path / "prompt.md"),
        username="seedbot",
        run_dir=run_dir,
        inference_seed=11001,
        run_provenance=provenance,
    )
    assert captured["cmd"][captured["cmd"].index("--inference-seed") + 1] == "11001"
    meta = json.loads((run_dir / "harness_meta_template.json").read_text())
    assert meta["inference_seed"] == 11001
    assert all(meta[key] == value for key, value in provenance.items())


@pytest.mark.parametrize("relative_path", ENDPOINTS)
def test_confirmatory_2b_endpoint_passes_validated_seed_to_sglang(relative_path: str):
    source = (REPO / relative_path).read_text()
    ast.parse(source)
    assert 'seed = body.get("seed")' in source
    assert 'seed = validate_inference_seed(seed, label="seed")' in source
    assert 'sampling_params["sampling_seed"] = seed' in source
    assert '"supports_seed": True' in source
