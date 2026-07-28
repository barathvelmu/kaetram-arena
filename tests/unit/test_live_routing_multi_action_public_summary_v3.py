from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.opd.live_routing_multi_action_public_summary_v3 as public


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path, Path]:
    root = tmp_path / "result"
    root.mkdir()
    (root / "manifest.json").write_text("{}\n", encoding="utf-8")
    (root / "prelaunch.json").write_text(
        json.dumps({"git_head": "f" * 40}), encoding="utf-8"
    )
    (root / "analysis.json").write_text(
        json.dumps({"payload_sha256": "1" * 64}), encoding="utf-8"
    )
    active = {
        "technical_trials": 3,
        "protocol_valid": 3,
        "full_predicate_pass": 3,
        "behavioral_fail": 0,
        "invalid": 0,
        "action_predicate_pass": {"equip_item": 3, "eat_food": 3, "warp": 3},
    }
    off = {
        **active,
        "action_predicate_pass": {"equip_item": 0, "eat_food": 0, "warp": 0},
    }
    analysis = {
        "verdict": "complete",
        "technical_trials": 9,
        "protocol_valid": 9,
        "full_predicate_pass": 9,
        "behavioral_fail": 0,
        "invalid": 0,
        "technical_repeats": 3,
        "payload_sha256": "2" * 64,
        "arms": {
            "structured_direct": active,
            "content_recovery_on": active,
            "content_recovery_off": off,
        },
    }
    artifact = tmp_path / "analysis-v3.json"
    artifact.write_text(
        json.dumps({"payload_sha256": "3" * 64, "analysis": analysis}),
        encoding="utf-8",
    )
    v3_registration = tmp_path / "v3.json"
    parent_registration = tmp_path / "v2.json"
    v3_registration.write_text("{}\n", encoding="utf-8")
    parent_registration.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(public, "EXPECTED_SOURCE_COMMIT", "f" * 40)
    monkeypatch.setattr(
        public,
        "EXPECTED_MANIFEST_FILE_SHA256",
        hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        public,
        "EXPECTED_V3_ANALYSIS_FILE_SHA256",
        hashlib.sha256(artifact.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        public,
        "verify_package",
        lambda *args, **kwargs: {
            "verified": True,
            "verdict": "complete_with_failures",
            "protocol_valid": 9,
            "full_predicate_pass": 0,
            "manifest_payload_sha256": "4" * 64,
        },
    )
    monkeypatch.setattr(
        public,
        "verify_analysis_artifact",
        lambda *args, **kwargs: {
            "verified": True,
            "verdict": "complete",
            "protocol_valid": 9,
            "full_predicate_pass": 9,
        },
    )
    return root, artifact, v3_registration, parent_registration


def test_summary_derives_bounded_v3_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, artifact, v3_registration, parent_registration = _fixture(
        tmp_path, monkeypatch
    )
    summary = public.build_public_summary(
        root,
        artifact,
        v3_registration=v3_registration,
        parent_registration=parent_registration,
        repo_root=tmp_path,
    )
    assert summary["outcome"] == {
        "technical_trials": 9,
        "technical_repeats": 3,
        "technical_repeats_are_independent": False,
        "protocol_valid": 9,
        "protocol_invalid": 0,
        "full_predicate_pass": 9,
        "behavioral_fail": 0,
        "verdict": "complete",
    }
    assert summary["arms"]["structured_direct"]["equip_item"] == 3
    assert summary["arms"]["content_recovery_off"]["equip_item"] is None
    assert summary["arms"]["content_recovery_off"][
        "no_registered_action_effect"
    ] == 3
    assert summary["measurement_history"]["v2_relabelled"] is False


def test_summary_round_trip_and_tamper_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, artifact, v3_registration, parent_registration = _fixture(
        tmp_path, monkeypatch
    )
    summary = public.build_public_summary(
        root,
        artifact,
        v3_registration=v3_registration,
        parent_registration=parent_registration,
        repo_root=tmp_path,
    )
    output = tmp_path / "public-v3.json"
    public.write_public_summary(output, summary)
    assert public.verify_public_summary(
        output,
        root,
        artifact,
        v3_registration=v3_registration,
        parent_registration=parent_registration,
        repo_root=tmp_path,
    )["verified"] is True
    output.chmod(0o644)
    changed = json.loads(output.read_text(encoding="utf-8"))
    changed["outcome"]["full_predicate_pass"] = 8
    output.write_text(public.canonical_json(changed) + "\n", encoding="utf-8")
    with pytest.raises(public.PublicSummaryV3Error, match="self-hash"):
        public.verify_public_summary(
            output,
            root,
            artifact,
            v3_registration=v3_registration,
            parent_registration=parent_registration,
            repo_root=tmp_path,
        )


def test_unexpected_arm_result_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, artifact, v3_registration, parent_registration = _fixture(
        tmp_path, monkeypatch
    )
    value = json.loads(artifact.read_text(encoding="utf-8"))
    value["analysis"]["arms"]["structured_direct"]["full_predicate_pass"] = 2
    artifact.write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setattr(
        public,
        "EXPECTED_V3_ANALYSIS_FILE_SHA256",
        hashlib.sha256(artifact.read_bytes()).hexdigest(),
    )
    with pytest.raises(public.PublicSummaryV3Error, match="unexpected V3 outcome"):
        public.build_public_summary(
            root,
            artifact,
            v3_registration=v3_registration,
            parent_registration=parent_registration,
            repo_root=tmp_path,
        )


def test_v3_public_summary_cli_is_directly_runnable_from_outside_repo() -> None:
    completed = subprocess.run(
        [sys.executable, str(Path(public.__file__).resolve()), "--help"],
        cwd="/",
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "{build,verify}" in completed.stdout
