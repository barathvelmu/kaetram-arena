from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.opd.local_weight_pilot import (
    ENDPOINT_ENV,
    PilotError,
    _validate_schedule,
    build_eval_command,
    build_eval_environment,
    build_artifact_inventory,
    load_manifest,
)


REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "research/experiments/local-weight-pilot.json"


def test_registered_pilot_is_small_paired_and_non_confirmatory() -> None:
    raw, digest = load_manifest(MANIFEST)
    assert len(digest) == 64
    assert raw["claim_boundary"]["confirmatory"] is False
    assert raw["protocol"]["duration_seconds"] == 300
    assert len(raw["cells"]) == 9
    for replicate in (1, 2, 3):
        block = [cell for cell in raw["cells"] if cell["replicate"] == replicate]
        assert len({cell["inference_seed"] for cell in block}) == 1
        assert len({cell["environment_seed"] for cell in block}) == 1
        assert {cell["snapshot"] for cell in block} == {
            "base_2b",
            "opd_r2_2b",
            "opd_r3_2b",
        }


def test_dry_run_launches_nothing_and_reports_nominal_runtime(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts/opd/local_weight_pilot.py")],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload == {
        "cell_count": 9,
        "confirmatory": False,
        "duration_seconds_per_cell": 300,
        "manifest_sha256": payload["manifest_sha256"],
        "mode": "dry_run",
        "nominal_runtime_seconds": 2700,
        "nothing_launched": True,
        "pilot_id": "local-render-parity-pilot-v1",
    }
    assert not list(tmp_path.iterdir())


def test_schedule_or_claim_drift_is_rejected() -> None:
    raw = json.loads(MANIFEST.read_text())
    raw["cells"][0]["inference_seed"] = 999
    with pytest.raises(PilotError, match="inference seed is not paired"):
        _validate_schedule(raw)

    raw = json.loads(MANIFEST.read_text())
    raw["claim_boundary"]["confirmatory"] = True
    with pytest.raises(PilotError, match="non-confirmatory"):
        _validate_schedule(raw)


def test_eval_command_uses_endpoint_environment_and_complete_provenance(
    tmp_path: Path,
) -> None:
    manifest, manifest_sha = load_manifest(MANIFEST)
    cell = manifest["cells"][0]
    endpoint = {
        "attestation": {
            "checkpoint_sha256": "a" * 64,
            "tokenizer_sha256": "b" * 64,
            "render_contract_sha256": "c" * 64,
        },
    }
    game = {
        "gameRevision": "d" * 40,
        "entrypointSha256": "e" * 64,
    }
    command = build_eval_command(
        manifest=manifest,
        manifest_sha256=manifest_sha,
        cell=cell,
        cell_root=tmp_path / "cell",
        endpoint_attestation_sha256="f" * 64,
        endpoint_attestation=endpoint,
        game_attestation=game,
    )
    rendered = " ".join(command)
    assert f"{cell['cell_id']}={ENDPOINT_ENV}" in rendered
    assert "http://" not in rendered
    assert "--prompt-agent-name EvalCompletionist" in rendered
    assert "--duration-seconds 300" in rendered
    assert "--environment-seed 41001" in rendered
    assert "--checkpoint-sha256 " + "a" * 64 in rendered
    assert "--tokenizer-sha256 " + "b" * 64 in rendered
    assert "--render-contract-sha256 " + "c" * 64 in rendered


def test_eval_environment_pins_db_schema_and_recovery_off(tmp_path: Path) -> None:
    manifest, _ = load_manifest(MANIFEST)
    env = build_eval_environment(
        {
            "KAETRAM_TOOL_RECOVERY": "1",
            "KAETRAM_MONGO_DB": "ambient_test_lane",
            "KAETRAM_TOOL_SCHEMA_SOURCE": "live",
        },
        manifest=manifest,
        game_dir=tmp_path / "game",
        node_binary=tmp_path / "node",
    )
    assert "KAETRAM_TOOL_RECOVERY" not in env
    assert env["KAETRAM_MONGO_DB"] == "kaetram_devlopment"
    assert env["KAETRAM_TOOL_SCHEMA_SOURCE"] == "canonical"
    assert env[ENDPOINT_ENV] == "http://127.0.0.1:9801/v1"


def test_cell_artifact_inventory_hashes_content_and_rejects_symlinks(
    tmp_path: Path,
) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "result.json").write_text('{"ok":true}\n')
    first = build_artifact_inventory(tmp_path)
    assert first["file_count"] == 1
    assert first["files"][0]["path"] == "nested/result.json"

    (tmp_path / "nested" / "result.json").write_text('{"ok":false}\n')
    second = build_artifact_inventory(tmp_path)
    assert second["tree_sha256"] != first["tree_sha256"]

    (tmp_path / "link").symlink_to(tmp_path / "nested" / "result.json")
    with pytest.raises(PilotError, match="symlink"):
        build_artifact_inventory(tmp_path)
