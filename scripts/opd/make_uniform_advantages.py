"""Build the Arm-C control corpus: uniform clipped self-imitation.

Every finite, nonzero advantage is replaced with the corpus mean absolute
nonzero advantage. Zero-valued mask positions remain zero. The transformer is
fail-closed: it validates record geometry, refuses in-place or accidental
overwrite, detects source mutation between passes, writes atomically, and
records byte-level input/output/script hashes beside the result.

Usage:
  python3 scripts/opd/make_uniform_advantages.py \
      --in dataset/opd_2b/round2_uniform/records_r2_original.jsonl \
      --out dataset/opd_2b/round2_uniform/records.jsonl
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import BinaryIO


MANIFEST_SCHEMA_VERSION = "uniform-advantages-manifest-v2"


class ArtifactBuildError(ValueError):
    """Raised when an immutable derived-artifact contract is violated."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record(raw: bytes, *, line_number: int) -> dict:
    if not raw.strip():
        raise ArtifactBuildError(f"blank JSONL record at line {line_number}")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactBuildError(
            f"invalid UTF-8 JSON at line {line_number}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise ArtifactBuildError(f"record {line_number} is not a JSON object")
    advantages = value.get("advantages")
    if not isinstance(advantages, list) or not advantages:
        raise ArtifactBuildError(
            f"record {line_number} has no nonempty advantages list"
        )
    for field in ("input_ids", "labels", "behavior_logprobs"):
        aligned = value.get(field)
        if aligned is not None and (
            not isinstance(aligned, list) or len(aligned) != len(advantages)
        ):
            raise ArtifactBuildError(
                f"record {line_number} field {field!r} is not aligned with advantages"
            )
    for index, advantage in enumerate(advantages):
        if isinstance(advantage, bool) or not isinstance(advantage, (int, float)):
            raise ArtifactBuildError(
                f"record {line_number} advantage {index} is not numeric"
            )
        if not math.isfinite(float(advantage)):
            raise ArtifactBuildError(
                f"record {line_number} advantage {index} is not finite"
            )
    return value


def _temporary(parent: Path, stem: str) -> tuple[BinaryIO, Path]:
    handle = tempfile.NamedTemporaryFile(
        mode="w+b",
        dir=parent,
        prefix=f".{stem}.",
        suffix=".tmp",
        delete=False,
    )
    return handle, Path(handle.name)


def build_uniform_advantages(src: Path, dst: Path) -> dict:
    """Create and attest a uniform-advantage corpus."""
    src = src.resolve()
    dst = dst.resolve()
    manifest_path = dst.with_suffix(".manifest.json")
    if not src.is_file():
        raise ArtifactBuildError(f"source is not a regular file: {src}")
    if src == dst:
        raise ArtifactBuildError("source and output must be different files")
    if dst.exists() or manifest_path.exists():
        raise ArtifactBuildError(
            f"refusing to overwrite existing output or manifest: {dst}, {manifest_path}"
        )
    if not dst.parent.is_dir():
        raise ArtifactBuildError(f"output directory does not exist: {dst.parent}")

    source_sha256 = _sha256(src)
    total_abs = 0.0
    n_nonzero = n_zero = n_records = 0
    with src.open("rb") as handle:
        for line_number, raw in enumerate(handle, start=1):
            rec = _record(raw, line_number=line_number)
            n_records += 1
            for advantage in rec["advantages"]:
                value = float(advantage)
                if value != 0.0:
                    total_abs += abs(value)
                    n_nonzero += 1
                else:
                    n_zero += 1
    if n_records == 0:
        raise ArtifactBuildError("source contains no records")
    if n_nonzero == 0:
        raise ArtifactBuildError("source contains no nonzero advantages")
    c = total_abs / n_nonzero
    if not math.isfinite(c) or c <= 0:
        raise ArtifactBuildError("derived uniform advantage is not finite and positive")

    output_handle, output_tmp = _temporary(dst.parent, dst.name)
    source_digest_second_pass = hashlib.sha256()
    output_digest = hashlib.sha256()
    try:
        with output_handle:
            with src.open("rb") as source_handle:
                for line_number, raw in enumerate(source_handle, start=1):
                    source_digest_second_pass.update(raw)
                    rec = _record(raw, line_number=line_number)
                    rec["advantages"] = [
                        c if float(value) != 0.0 else 0.0
                        for value in rec["advantages"]
                    ]
                    encoded = (json.dumps(rec, sort_keys=True) + "\n").encode("utf-8")
                    output_handle.write(encoded)
                    output_digest.update(encoded)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        if source_digest_second_pass.hexdigest() != source_sha256:
            raise ArtifactBuildError("source changed between validation and rewrite")

        manifest = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "control": "uniform-clipped-self-imitation",
            "source": str(src),
            "source_sha256": source_sha256,
            "output": str(dst),
            "output_sha256": output_digest.hexdigest(),
            "script_sha256": _sha256(Path(__file__).resolve()),
            "c": c,
            "c_rule": "corpus mean |advantage| over nonzero tokens",
            "n_records": n_records,
            "n_nonzero_tokens": n_nonzero,
            "n_zero_tokens_kept": n_zero,
        }
        manifest_bytes = (
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        manifest_handle, manifest_tmp = _temporary(
            manifest_path.parent, manifest_path.name
        )
        try:
            with manifest_handle:
                manifest_handle.write(manifest_bytes)
                manifest_handle.flush()
                os.fsync(manifest_handle.fileno())
            os.replace(output_tmp, dst)
            os.replace(manifest_tmp, manifest_path)
        finally:
            manifest_tmp.unlink(missing_ok=True)
        return manifest
    finally:
        output_tmp.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="inp", required=True)
    parser.add_argument("--out", dest="out", required=True)
    args = parser.parse_args(argv)
    try:
        manifest = build_uniform_advantages(Path(args.inp), Path(args.out))
    except ArtifactBuildError as exc:
        parser.error(str(exc))
    print(
        f"records={manifest['n_records']}  "
        f"nonzero_adv_tokens={manifest['n_nonzero_tokens']}  "
        f"zero(kept)={manifest['n_zero_tokens_kept']}"
    )
    print(f"pre-registered c = corpus mean |advantage| = {manifest['c']:.6f}")
    print(f"wrote {manifest['output']} + {Path(manifest['output']).with_suffix('.manifest.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
