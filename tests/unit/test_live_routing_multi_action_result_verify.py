from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.opd.live_routing_multi_action_diagnostic import canonical_sha256
from scripts.opd.live_routing_multi_action_result_verify import (
    MANIFEST_SCHEMA_VERSION,
    MultiActionVerificationError,
    verify_package,
)
from scripts.opd.live_routing_result_verify import (
    RUNTIME_PREFLIGHT_SCHEMA_VERSION,
    VerificationError,
    validate_runtime_preflight,
)
from tests.unit.test_live_routing_result_verify import _services_evidence


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _package(root: Path) -> dict:
    analysis = {
        "verdict": "complete_with_failures",
        "protocol_valid": 9,
        "full_predicate_pass": 8,
    }
    _write_json(root / "analysis.json", analysis)
    _write_json(root / "prelaunch.json", {"stub": True})
    _write_json(root / "registration.json", {"stub": True})
    _write_json(root / "runtime-preflight.json", {"stub": True})
    for index in range(1, 10):
        _write_json(
            root / "receipts" / f"trial-{index:02d}.json",
            {"index": index, "plan": {"index": index}, "registration_sha256": "a" * 64},
        )
    relatives = [
        "analysis.json", "prelaunch.json", "registration.json", "runtime-preflight.json",
        *(f"receipts/trial-{index:02d}.json" for index in range(1, 10)),
    ]
    rows = []
    for relative in relatives:
        raw = (root / relative).read_bytes()
        rows.append(
            {"path": relative, "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        )
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "study_id": "local-live-routing-multi-action-v2",
        "run_id": "deadbeef",
        "files": rows,
    }
    manifest["payload_sha256"] = canonical_sha256(manifest)
    _write_json(root / "manifest.json", manifest)
    return analysis


def _patch_scientific_verifiers(monkeypatch, analysis: dict) -> None:
    monkeypatch.setattr(
        "scripts.opd.live_routing_multi_action_result_verify.validate_registration",
        lambda _: [],
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_multi_action_result_verify.verify_prelaunch",
        lambda *_args, **_kwargs: {
            "registration": {"sha256": "a" * 64},
            "trials": [{"index": index} for index in range(1, 10)],
        },
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_multi_action_result_verify.analyze_run",
        lambda _: analysis,
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_multi_action_result_verify.validate_runtime_preflight",
        lambda *_args, **_kwargs: None,
    )


def test_package_verifier_checks_membership_hashes_and_recomputed_analysis(monkeypatch, tmp_path: Path) -> None:
    analysis = _package(tmp_path)
    _patch_scientific_verifiers(monkeypatch, analysis)
    result = verify_package(tmp_path, repo_root=tmp_path)
    assert result == {
        "verified": True,
        "verdict": "complete_with_failures",
        "protocol_valid": 9,
        "full_predicate_pass": 8,
        "manifest_payload_sha256": json.loads((tmp_path / "manifest.json").read_text())["payload_sha256"],
    }


def test_package_verifier_rejects_file_tampering(monkeypatch, tmp_path: Path) -> None:
    analysis = _package(tmp_path)
    _patch_scientific_verifiers(monkeypatch, analysis)
    (tmp_path / "receipts/trial-04.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(MultiActionVerificationError, match="digest mismatch"):
        verify_package(tmp_path, repo_root=tmp_path)


def test_package_verifier_rejects_unlisted_extra_file(monkeypatch, tmp_path: Path) -> None:
    analysis = _package(tmp_path)
    _patch_scientific_verifiers(monkeypatch, analysis)
    (tmp_path / "notes.txt").write_text("not sealed\n", encoding="utf-8")
    with pytest.raises(MultiActionVerificationError, match="extra or missing"):
        verify_package(tmp_path, repo_root=tmp_path)


def test_package_verifier_rejects_symlinked_receipts_parent(monkeypatch, tmp_path: Path) -> None:
    analysis = _package(tmp_path)
    _patch_scientific_verifiers(monkeypatch, analysis)
    receipts = tmp_path / "receipts"
    outside = tmp_path.parent / f"{tmp_path.name}-receipts"
    receipts.rename(outside)
    receipts.symlink_to(outside, target_is_directory=True)
    with pytest.raises(MultiActionVerificationError, match="symlinked or unsafe directory"):
        verify_package(tmp_path, repo_root=tmp_path)


def test_package_verifier_rejects_path_traversal_even_with_valid_self_hash(monkeypatch, tmp_path: Path) -> None:
    analysis = _package(tmp_path)
    _patch_scientific_verifiers(monkeypatch, analysis)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][0]["path"] = "../analysis.json"
    manifest["payload_sha256"] = canonical_sha256(
        {key: value for key, value in manifest.items() if key != "payload_sha256"}
    )
    _write_json(manifest_path, manifest)
    with pytest.raises(MultiActionVerificationError, match="unsafe manifest path"):
        verify_package(tmp_path, repo_root=tmp_path)


def test_runtime_preflight_rejects_fabricated_remote_service_lane() -> None:
    registration = json.loads(
        Path("research/experiments/local-live-routing-multi-action-v2.json").read_text()
    )
    live = registration["live_contract"]
    prelaunch = {
        "study_id": registration["study_id"],
        "run_id": "deadbeef",
        "payload_sha256": "b" * 64,
    }
    services = _services_evidence(live, client_file_count=25)
    services["lane"]["host"] = "198.51.100.2"
    services["payload_sha256"] = canonical_sha256(
        {key: value for key, value in services.items() if key != "payload_sha256"}
    )
    record = {
        "schema_version": RUNTIME_PREFLIGHT_SCHEMA_VERSION,
        "study_id": registration["study_id"],
        "run_id": "deadbeef",
        "registration_sha256": "a" * 64,
        "prelaunch_payload_sha256": "b" * 64,
        "game": {
            "git_head": live["game_revision"],
            "worktree_clean": True,
            "bundle_path": "packages/server/dist/main.js",
            "bundle_size_bytes": 1234,
            "bundle_sha256": live["game_bundle_sha256"],
            "client_dist_file_count": 25,
            "client_dist_inventory_sha256": live["client_dist_inventory_sha256"],
        },
        "mongo": {
            "uri": "mongodb://127.0.0.1:27017/kaetram_e2e",
            "database": "kaetram_e2e",
            "nodes": [{"host": "127.0.0.1", "port": 27017}],
            "loopback_only": True,
        },
        "python": {
            key: live[key]
            for key in (
                "python_version", "python_executable_sha256", "mcp_version",
                "playwright_version", "pymongo_version",
            )
        },
        "services": services,
    }
    record["payload_sha256"] = canonical_sha256(record)
    with pytest.raises(VerificationError, match="escaped the zero-cost lane"):
        validate_runtime_preflight(
            record,
            registration=registration,
            registration_sha256="a" * 64,
            prelaunch=prelaunch,
        )
