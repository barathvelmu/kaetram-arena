"""Checks for the anonymous serving-regime parity review artifact."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from scripts import build_tmlr_review_roots as review_roots
from scripts import build_tmlr_supplement as supplement
from scripts.build_tmlr_supplement import (
    PACKAGE_SCHEMA,
    PARITY_REVIEW_SCHEMA,
    audit_review_tree,
    build_parity_review_artifact,
    canonical_json_bytes,
    write_json,
)
from scripts.opd.verify_serving_regime_parity_review_bundle import (
    ParityReviewError,
    _strict_loads,
    verify_review_artifact,
)


@pytest.fixture(scope="module")
def parity_review_artifact(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, str]:
    root = tmp_path_factory.mktemp("parity-review") / "artifact"
    trust_root = build_parity_review_artifact(root)
    return root, trust_root


def test_parity_review_verifier_recomputes_matched_result(
    parity_review_artifact: tuple[Path, str],
) -> None:
    artifact, trust_root = parity_review_artifact
    result = verify_review_artifact(artifact, trust_root)
    assert result["paired_requests"] == 1020
    assert result["checkpoint_recovery_rate_differences"] == {
        "base_2b": -96 / 340,
        "opd_r2_2b": -75 / 340,
        "opd_r3_2b": -98 / 340,
    }
    assert result["registered_directional_criterion_passed"] is True
    assert result["source_history_authentication"] == "deferred_until_deanonymized"


def test_parity_review_projection_is_anonymous(
    parity_review_artifact: tuple[Path, str],
) -> None:
    artifact, _trust_root = parity_review_artifact
    audit_review_tree(artifact)
    text = "".join(
        path.read_text(errors="replace")
        for path in artifact.rglob("*")
        if path.is_file()
    )
    assert "source_git_commit" not in text
    assert "runtime_environment_receipt_sha256" not in text
    assert "/Users/" not in text
    assert "modal.run" not in text


@pytest.mark.parametrize("payload", ('{"a":1,"a":2}', '{"a":NaN}'))
def test_parity_review_verifier_rejects_ambiguous_json(payload: str) -> None:
    with pytest.raises(ParityReviewError):
        _strict_loads(payload, label="fixture")


def test_parity_review_semantic_tampering_fails_with_new_trust_root(
    parity_review_artifact: tuple[Path, str],
    tmp_path: Path,
) -> None:
    source, _trust_root = parity_review_artifact
    copied = tmp_path / "artifact"
    import shutil

    shutil.copytree(source, copied)
    rows_path = copied / "runs/base_2b/thinking_disabled.jsonl"
    rows = [json.loads(line) for line in rows_path.read_text().splitlines()]
    row = next(item for item in rows if item["response_message"].get("tool_calls"))
    row["response_message"]["tool_calls"] = []
    rows_path.write_bytes(b"".join(canonical_json_bytes(item) for item in rows))
    index_path = copied / "artifact-index.json"
    index = json.loads(index_path.read_text())
    records = supplement._inventory(copied)
    index["files"] = records
    index["tree_sha256"] = hashlib.sha256(
        canonical_json_bytes(records).rstrip()
    ).hexdigest()
    write_json(index_path, index)
    with pytest.raises(ParityReviewError, match="does not recompute"):
        verify_review_artifact(copied, supplement.sha256_file(index_path))


def test_review_roots_emit_parity_macro(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output = tmp_path / "review-roots.tex"
    monkeypatch.setattr(sys, "argv", ["build_tmlr_review_roots.py", "--output", str(output)])
    assert review_roots.main() == 0
    payload = output.read_text()
    assert "\\def\\VTwoReviewIndex{" in payload
    assert "\\def\\VThreeReviewIndex{" in payload
    assert "\\def\\ParityReviewIndex{" in payload
    audit_review_tree(tmp_path)


def test_supplement_v6_contains_parity_trust_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4\n%%EOF\n")
    output = tmp_path / "supplement.zip"
    monkeypatch.setattr(supplement, "PAPER", paper)
    monkeypatch.setattr(supplement, "OUTPUT", output)
    monkeypatch.setattr(supplement, "require_local_untracked_output", lambda: None)
    assert supplement.main([]) == 0
    with zipfile.ZipFile(output) as archive:
        prefix = "kaetram-tmlr-anonymous-supplement/"
        manifest = json.loads(archive.read(prefix + "package-manifest.json"))
        roots = json.loads(
            archive.read(prefix + "results/review-artifact-trust-root.json")
        )
        parity_root = roots["serving_regime_parity_artifact_index_sha256"]
        assert manifest["schema_version"] == PACKAGE_SCHEMA
        assert PACKAGE_SCHEMA == "kaetram.tmlr-anonymous-supplement.v6"
        assert (
            manifest["serving_regime_parity_review_artifact_index_sha256"]
            == parity_root
        )
        parity_index = archive.read(
            prefix + "artifact-serving-regime-parity/artifact-index.json"
        )
        assert hashlib.sha256(parity_index).hexdigest() == parity_root
        assert json.loads(parity_index)["schema_version"] == PARITY_REVIEW_SCHEMA
        assert (
            prefix + "scripts/opd/verify_serving_regime_parity_review_bundle.py"
            in archive.namelist()
        )
        extracted = tmp_path / "extracted"
        archive.extractall(extracted)
    stage = extracted / "kaetram-tmlr-anonymous-supplement"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/opd/verify_serving_regime_parity_review_bundle.py",
            "--artifact-dir",
            "artifact-serving-regime-parity",
            "--expected-index-sha256",
            parity_root,
        ],
        cwd=stage,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["paired_requests"] == 1020
