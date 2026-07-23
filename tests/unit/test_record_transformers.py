from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.opd.make_uniform_advantages import (
    ArtifactBuildError as UniformBuildError,
)
from scripts.opd.make_uniform_advantages import build_uniform_advantages
from scripts.opd.resample_records import (
    ArtifactBuildError as ResampleBuildError,
)
from scripts.opd.resample_records import resample_records


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def _record(record_id: int, advantages: list[float]) -> dict:
    size = len(advantages)
    return {
        "record_id": record_id,
        "input_ids": list(range(1, size + 1)),
        "labels": list(range(1, size + 1)),
        "behavior_logprobs": [0.0] * size,
        "advantages": advantages,
        "step_weight": 1.0,
    }


def test_uniform_advantages_preserves_mask_and_attests_bytes(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    output = tmp_path / "uniform.jsonl"
    _write_jsonl(
        source,
        [
            {
                **_record(0, [0.0, -1.0, 3.0]),
                "labels": [-100, 2, 3],
                "behavior_logprobs": [0.0, -1.0, -2.0],
            },
            _record(1, [2.0, 0.0]),
        ],
    )

    manifest = build_uniform_advantages(source, output)
    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert records[0]["advantages"] == [0.0, 2.0, 2.0]
    assert records[1]["advantages"] == [2.0, 0.0]
    assert manifest["c"] == 2.0
    assert manifest["source_sha256"] == _sha256(source)
    assert manifest["output_sha256"] == _sha256(output)
    assert manifest["record_schema_version"] == "kaetram-opd-train-record-v1"
    assert len(manifest["record_schema_sha256"]) == 64
    assert len(manifest["record_schema_validator_sha256"]) == 64
    assert json.loads(output.with_suffix(".manifest.json").read_text()) == manifest


@pytest.mark.parametrize(
    "record,match",
    [
        (_record(0, [0.0, 0.0]), "no nonzero advantages"),
        (
            _record(0, [1.0, float("inf")]),
            "finite numeric list",
        ),
        (
            {**_record(0, [1.0, 2.0]), "input_ids": [1]},
            "aligned",
        ),
    ],
)
def test_uniform_advantages_rejects_invalid_corpora(
    tmp_path: Path,
    record: dict,
    match: str,
) -> None:
    source = tmp_path / "source.jsonl"
    _write_jsonl(source, [record])
    with pytest.raises(UniformBuildError, match=match):
        build_uniform_advantages(source, tmp_path / "output.jsonl")


def test_uniform_advantages_refuses_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    output = tmp_path / "output.jsonl"
    _write_jsonl(source, [_record(0, [1.0])])
    output.write_text("owned-by-user\n")
    with pytest.raises(UniformBuildError, match="refusing to overwrite"):
        build_uniform_advantages(source, output)
    assert output.read_text() == "owned-by-user\n"


def test_resample_is_deterministic_exact_count_and_attested(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    output_a = tmp_path / "resampled-a.jsonl"
    output_b = tmp_path / "resampled-b.jsonl"
    _write_jsonl(
        source,
        [_record(0, [1.0]), _record(1, [2.0]), _record(2, [3.0])],
    )

    manifest_a = resample_records(source, output_a, target=9, seed=42)
    manifest_b = resample_records(source, output_b, target=9, seed=42)
    lines_a = output_a.read_bytes().splitlines()
    lines_b = output_b.read_bytes().splitlines()
    assert len(lines_a) == 9
    assert lines_a == lines_b
    assert lines_a[:3] == source.read_bytes().splitlines()
    assert manifest_a["output_sha256"] == _sha256(output_a)
    assert manifest_a["record_schema_version"] == "kaetram-opd-train-record-v1"
    assert len(manifest_a["record_schema_sha256"]) == 64
    assert len(manifest_a["record_schema_validator_sha256"]) == 64
    assert manifest_a["sampled_indices_sha256"] == manifest_b["sampled_indices_sha256"]
    assert json.loads(output_a.with_suffix(".manifest.json").read_text()) == manifest_a


def test_resample_rejects_malformed_input_and_overwrite(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.jsonl"
    malformed.write_text(json.dumps(_record(0, [1.0])) + "\nnot-json\n")
    with pytest.raises(ResampleBuildError, match="invalid UTF-8 JSON"):
        resample_records(malformed, tmp_path / "out.jsonl", target=3, seed=1)

    source = tmp_path / "source.jsonl"
    output = tmp_path / "existing.jsonl"
    _write_jsonl(source, [_record(0, [1.0])])
    output.write_text("owned-by-user\n")
    with pytest.raises(ResampleBuildError, match="refusing to overwrite"):
        resample_records(source, output, target=2, seed=1)
    assert output.read_text() == "owned-by-user\n"


def test_transformers_reject_noncanonical_json_objects(tmp_path: Path) -> None:
    source = tmp_path / "source.jsonl"
    _write_jsonl(source, [{"advantages": [1.0]}])
    with pytest.raises(UniformBuildError, match="missing required OPD field"):
        build_uniform_advantages(source, tmp_path / "uniform.jsonl")
    with pytest.raises(ResampleBuildError, match="missing required OPD field"):
        resample_records(source, tmp_path / "resampled.jsonl", target=2, seed=1)


@pytest.mark.parametrize(
    "record,match",
    [
        (
            {
                **_record(0, [100.0, 1.0]),
                "labels": [-100, 2],
            },
            "ignored position 0 must have zero",
        ),
        (
            {
                **_record(0, [0.0, 1.0]),
                "labels": [-100, 1],
            },
            "must equal input_id",
        ),
        (
            {
                **_record(0, [0.0, 1.0, 0.0]),
                "labels": [-100, 2, -100],
            },
            "contiguous prefix",
        ),
    ],
)
def test_transformers_reject_training_corrupting_mask_geometry(
    tmp_path: Path,
    record: dict,
    match: str,
) -> None:
    source = tmp_path / "source.jsonl"
    _write_jsonl(source, [record])
    with pytest.raises(UniformBuildError, match=match):
        build_uniform_advantages(source, tmp_path / "uniform.jsonl")
    with pytest.raises(ResampleBuildError, match=match):
        resample_records(source, tmp_path / "resampled.jsonl", target=2, seed=1)
