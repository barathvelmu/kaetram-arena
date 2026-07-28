from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from scripts.opd.live_routing_analyzer import analyze_run, canonical_json_bytes, canonical_sha256
from scripts.opd.live_routing_prelaunch import EXPECTED_LANE, build_prelaunch_payload
from scripts.opd.live_routing_result_verify import (
    MANIFEST_SCHEMA_VERSION,
    verify_package,
)
from tests.unit.test_live_routing_analyzer import _resign, _unsigned_receipt
from tests.unit.test_live_routing_prelaunch import _ready_repo


def _write_canonical(path: Path, value: dict) -> bytes:
    raw = canonical_json_bytes(value) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _complete_package(
    tmp_path: Path,
) -> tuple[Path, Path, Path, str]:
    repo, registration_path, head = _ready_repo(tmp_path)
    registration = json.loads(registration_path.read_text())
    prelaunch = build_prelaunch_payload(
        registration_path,
        repo_root=repo,
        expected_head=head,
        run_id="local001",
        lane=EXPECTED_LANE,
    )
    package = tmp_path / "package"
    package.mkdir()
    prelaunch_raw = _write_canonical(package / "prelaunch.json", prelaunch)
    receipts = [
        _unsigned_receipt(registration, prelaunch, plan)
        for plan in prelaunch["trials"]
    ]
    _resign(receipts, prelaunch)
    entries = []
    for index, receipt in enumerate(receipts, start=1):
        relative = f"receipts/trial-{index:02d}.json"
        raw = _write_canonical(package / relative, receipt)
        entries.append(
            {
                "schedule_index": index,
                "path": relative,
                "file_sha256": hashlib.sha256(raw).hexdigest(),
                "receipt_payload_sha256": receipt["payload_sha256"],
            }
        )
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "study_id": prelaunch["study_id"],
        "run_id": prelaunch["run_id"],
        "registration_sha256": prelaunch["registration"]["sha256"],
        "prelaunch_file_sha256": hashlib.sha256(prelaunch_raw).hexdigest(),
        "prelaunch_payload_sha256": prelaunch["payload_sha256"],
        "claim_contract_sha256": prelaunch["claim_contract_sha256"],
        "trial_plan_sha256": prelaunch["trial_plan_sha256"],
        "entries": entries,
        "final_chain_head": receipts[-1]["payload_sha256"],
    }
    manifest["payload_sha256"] = canonical_sha256(manifest)
    _write_canonical(package / "manifest.json", manifest)
    analysis = analyze_run(
        registration,
        prelaunch,
        receipts,
        manifest_payload_sha256=manifest["payload_sha256"],
    )
    _write_canonical(package / "analysis.json", analysis)
    return package, registration_path, repo, head


def test_complete_package_verifies_with_source_seal(tmp_path: Path) -> None:
    package, registration, repo, head = _complete_package(tmp_path)
    assert verify_package(
        package,
        registration,
        repo_root=repo,
        expected_head=head,
    ) == []


def test_extra_file_or_symlink_refuses_package(tmp_path: Path) -> None:
    package, registration, _, _ = _complete_package(tmp_path)
    (package / "posthoc.txt").write_text("exclude trial 1\n")
    assert "package file set drift" in verify_package(package, registration)[0]
    (package / "posthoc.txt").unlink()
    os.symlink(package / "analysis.json", package / "posthoc-link.json")
    assert "package contains symlink" in verify_package(package, registration)[0]


def test_duplicate_json_key_refuses_package(tmp_path: Path) -> None:
    package, registration, _, _ = _complete_package(tmp_path)
    analysis = (package / "analysis.json").read_text().rstrip()
    (package / "analysis.json").write_text(
        analysis[:-1] + ',"verdict":"posthoc"}\n'
    )
    assert "duplicate JSON key" in verify_package(package, registration)[0]


def test_receipt_mutation_breaks_manifest_even_if_receipt_is_rehashed(
    tmp_path: Path,
) -> None:
    package, registration, _, _ = _complete_package(tmp_path)
    path = package / "receipts/trial-01.json"
    receipt = json.loads(path.read_text())
    receipt["routing"]["protocol_success"] = False
    receipt["payload_sha256"] = canonical_sha256(
        {key: value for key, value in receipt.items() if key != "payload_sha256"}
    )
    _write_canonical(path, receipt)
    assert "trial file digest mismatch" in verify_package(package, registration)[0]


def test_analysis_claim_cannot_be_rewritten(tmp_path: Path) -> None:
    package, registration, _, _ = _complete_package(tmp_path)
    path = package / "analysis.json"
    analysis = json.loads(path.read_text())
    analysis["verdict"] = "complete_with_failures"
    analysis["analysis_payload_sha256"] = canonical_sha256(
        {
            key: value
            for key, value in analysis.items()
            if key != "analysis_payload_sha256"
        }
    )
    _write_canonical(path, analysis)
    assert "differs from deterministic recomputation" in verify_package(
        package, registration
    )[0]
