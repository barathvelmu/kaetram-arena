from __future__ import annotations

from pathlib import Path

import pytest

from run_manifest import ManifestError, sha256_json
from scripts.manifest_historical_runs import build_historical_run_digests


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    raw = tmp_path / "dataset" / "raw"
    run = raw / "agent_0" / "runs" / "run_a"
    run.mkdir(parents=True)
    (run / "run.meta.json").write_text('{"run_id":"run_a"}\n')
    (run / "session_1.log").write_text('{"type":"system"}\n')
    source = tmp_path / "SHA256SUMS"
    source.write_text("abc  dataset/raw/agent_0/runs/run_a/session_1.log\n")
    return raw, source


def _build(raw: Path, source: Path) -> dict:
    return build_historical_run_digests(
        raw,
        source_manifest=source,
        groups=["test"],
        claim_runs={"test": ["run_a"]},
        agents=["agent_0"],
    )


def test_digest_manifest_is_deterministic_and_self_identifying(tmp_path: Path) -> None:
    raw, source = _fixture(tmp_path)
    first = _build(raw, source)
    second = _build(raw, source)

    assert first == second
    identity = first.pop("manifest_sha256")
    assert identity == sha256_json(first)
    assert first["complete"]
    assert first["bundle_count"] == 1
    assert first["bundles"][0]["content"]["file_count"] == 2
    assert first["bundles"][0]["content"]["path"] == "agent_0/runs/run_a"


def test_digest_changes_when_run_content_changes(tmp_path: Path) -> None:
    raw, source = _fixture(tmp_path)
    first = _build(raw, source)
    (raw / "agent_0" / "runs" / "run_a" / "session_1.log").write_text(
        '{"type":"assistant"}\n'
    )
    second = _build(raw, source)

    assert (
        first["bundles"][0]["content"]["sha256"]
        != second["bundles"][0]["content"]["sha256"]
    )
    assert first["manifest_sha256"] != second["manifest_sha256"]


def test_missing_run_is_reported_and_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "SHA256SUMS"
    source.write_text("empty\n")
    report = build_historical_run_digests(
        tmp_path / "raw",
        source_manifest=source,
        groups=["test"],
        claim_runs={"test": ["run_missing"]},
        agents=["agent_0"],
    )

    assert not report["complete"]
    assert report["bundle_count"] == 0
    assert report["missing"] == [
        str(tmp_path / "raw" / "agent_0" / "runs" / "run_missing")
    ]


def test_source_manifest_is_required(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="source manifest does not exist"):
        build_historical_run_digests(
            tmp_path / "raw",
            source_manifest=tmp_path / "missing",
            groups=["test"],
            claim_runs={"test": ["run_a"]},
            agents=["agent_0"],
        )
