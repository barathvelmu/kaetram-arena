from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts.opd import serving_regime_parity_probe as parity
from scripts.opd.verify_serving_regime_parity_bundle import (
    BundleError,
    verify_bundle,
)


ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "research/results/local-serving-regime-parity-v1"
REGISTRATION = ROOT / "research/experiments/local-serving-regime-parity-v1.json"
EXPECTED_INDEX = "155428f1a61b32532752ea0bae0a4f550cccc7d107316a3846760dfe04b0e702"


def test_public_parity_bundle_verifies_from_raw_rows() -> None:
    result = verify_bundle(BUNDLE, EXPECTED_INDEX)
    assert result["new_requests"] == 1020
    assert result["registered_directional_criterion_passed"] is True


def test_bundle_rejects_raw_result_tampering(tmp_path: Path) -> None:
    copied = tmp_path / "bundle"
    shutil.copytree(BUNDLE, copied)
    result = copied / "runs/base_2b/results.jsonl"
    result.write_text(result.read_text() + "\n")
    with pytest.raises((BundleError, parity.ParityError), match="identity|digest"):
        verify_bundle(copied)


def _copied_base_run(tmp_path: Path) -> Path:
    copied = tmp_path / "base_2b"
    shutil.copytree(BUNDLE / "runs/base_2b", copied)
    return copied


def _registration() -> tuple[dict, str]:
    return parity.load_registration(REGISTRATION)


def test_run_rejects_extra_file(tmp_path: Path) -> None:
    copied = _copied_base_run(tmp_path)
    (copied / "extra.txt").write_text("not registered\n")
    registration, digest = _registration()
    with pytest.raises(parity.ParityError, match="directory inventory"):
        parity.verify_run(registration, digest, "base_2b", copied)


def test_run_rejects_omitted_index_record_even_with_recomputed_tree(
    tmp_path: Path,
) -> None:
    copied = _copied_base_run(tmp_path)
    index_path = copied / "artifact-index.json"
    index = json.loads(index_path.read_text())
    index["files"] = [
        record for record in index["files"] if record["path"] != "results.jsonl"
    ]
    index["tree_sha256"] = parity._sha256_json(index["files"])
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
    registration, digest = _registration()
    with pytest.raises(parity.ParityError, match="artifact inventory"):
        parity.verify_run(registration, digest, "base_2b", copied)


def test_run_rejects_postflight_tampering_with_recomputed_index(
    tmp_path: Path,
) -> None:
    copied = _copied_base_run(tmp_path)
    postflight_path = copied / "postflight.json"
    postflight = json.loads(postflight_path.read_text())
    postflight["completed_requests"] -= 1
    postflight_path.write_text(json.dumps(postflight, indent=2, sort_keys=True) + "\n")
    index_path = copied / "artifact-index.json"
    index = json.loads(index_path.read_text())
    record = next(
        record for record in index["files"] if record["path"] == "postflight.json"
    )
    record["size_bytes"] = postflight_path.stat().st_size
    record["sha256"] = parity._sha256_file(postflight_path)
    index["tree_sha256"] = parity._sha256_json(index["files"])
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
    registration, digest = _registration()
    with pytest.raises(parity.ParityError, match="postflight counts"):
        parity.verify_run(registration, digest, "base_2b", copied)


def test_run_rejects_symlinked_artifact(tmp_path: Path) -> None:
    copied = _copied_base_run(tmp_path)
    result = copied / "results.jsonl"
    result.unlink()
    try:
        result.symlink_to(BUNDLE / "runs/base_2b/results.jsonl")
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    registration, digest = _registration()
    with pytest.raises(parity.ParityError, match="directory inventory"):
        parity.verify_run(registration, digest, "base_2b", copied)
