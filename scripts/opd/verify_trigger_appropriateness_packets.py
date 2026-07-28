#!/usr/bin/env python3
"""Independently regenerate and verify an unlabeled appropriateness bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.opd import build_trigger_appropriateness_packets as builder  # noqa: E402


class PacketVerificationError(RuntimeError):
    pass


def _safe_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PacketVerificationError(f"bundle file is missing: {path.name}") from exc
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise PacketVerificationError(f"bundle path is not a regular file: {path.name}")


def _strict_json_bytes(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            payload,
            object_pairs_hook=builder._unique_object,
            parse_constant=builder._reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, builder.PacketBuildError) as exc:
        raise PacketVerificationError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise PacketVerificationError(f"{label} root is not an object")
    return value


def _verify_packet_lines(payload: bytes) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(payload.splitlines(), start=1):
        value = _strict_json_bytes(line, f"judge packet line {line_number}")
        if set(value) != {"schema_version", "item_id", "context", "candidate"}:
            raise PacketVerificationError("judge packet visible key set drift")
        if value.get("schema_version") != builder.PACKET_SCHEMA:
            raise PacketVerificationError("judge packet schema drift")
        if not isinstance(value.get("item_id"), str):
            raise PacketVerificationError("judge packet item identifier is invalid")
        context = value.get("context")
        if not isinstance(context, list) or not context:
            raise PacketVerificationError("judge packet context is invalid")
        for message in context:
            if not isinstance(message, dict) or message.get("role") == "assistant":
                raise PacketVerificationError("assistant reasoning leaked into judge packet")
            allowed = (
                {"role", "content"}
                if message.get("role") in {"system", "user"}
                else {"role", "name", "content"}
            )
            if set(message) != allowed or not isinstance(message.get("content"), str):
                raise PacketVerificationError("judge packet context message drift")
        candidate = value.get("candidate")
        if (
            not isinstance(candidate, dict)
            or set(candidate) != {"name", "arguments"}
            or not isinstance(candidate.get("name"), str)
            or not isinstance(candidate.get("arguments"), dict)
        ):
            raise PacketVerificationError("judge packet candidate drift")
        rows.append(value)
    if len(rows) != 123 or len({row["item_id"] for row in rows}) != 123:
        raise PacketVerificationError("judge packet census is incomplete or duplicated")
    if [row["item_id"] for row in rows] != sorted(row["item_id"] for row in rows):
        raise PacketVerificationError("judge packet ordering drift")
    return rows


def verify_bundle(
    bundle_dir: Path,
    *,
    artifact_dir: Path = builder.DEFAULT_ARTIFACT,
    trust_root_path: Path = builder.DEFAULT_TRUST_ROOT,
    registration_path: Path = builder.DEFAULT_REGISTRATION,
) -> dict[str, Any]:
    bundle_dir = bundle_dir.resolve()
    if bundle_dir.is_symlink() or not bundle_dir.is_dir():
        raise PacketVerificationError("bundle directory is missing or unsafe")
    expected_names = {*builder.OUTPUT_FILENAMES, "manifest.json"}
    actual_names = {path.name for path in bundle_dir.iterdir()}
    if actual_names != expected_names:
        raise PacketVerificationError("bundle path set is not closed")
    observed = {}
    for name in expected_names:
        path = bundle_dir / name
        _safe_file(path)
        observed[name] = path.read_bytes()

    # Validate visible structure and leakage before consulting the sealed key.
    visible_names = (
        "judge-protocol.json",
        "judge-packets.jsonl",
        "reviewer-a.template.jsonl",
        "reviewer-b.template.jsonl",
        "adjudication.template.jsonl",
        "label-seals.template.json",
        "labeling-workflow.md",
    )
    for name in visible_names:
        builder._scan_visible(name, observed[name])
    packets = _verify_packet_lines(observed["judge-packets.jsonl"])
    protocol = _strict_json_bytes(observed["judge-protocol.json"], "judge protocol")
    if (
        protocol.get("schema_version") != builder.PROTOCOL_SCHEMA
        or protocol.get("status") != "unlabeled_design_only"
        or protocol.get("human_gate", {}).get("independent_reviewers") != 2
        or protocol.get("human_gate", {}).get(
            "human_adjudication_required_for_every_disagreement"
        )
        is not True
        or protocol.get("human_gate", {}).get("distinct_third_human_adjudicator")
        is not True
    ):
        raise PacketVerificationError("judge protocol or human gate drift")

    sealed = _strict_json_bytes(observed["sealed-analysis-key.json"], "sealed key")
    if (
        sealed.get("schema_version") != builder.KEY_SCHEMA
        or sealed.get("unique_item_count") != 123
        or sealed.get("valid_occurrence_count") != 590
        or sealed.get("primary_item_count") != 25
        or not isinstance(sealed.get("items"), list)
        or len(sealed["items"]) != 123
    ):
        raise PacketVerificationError("sealed key census drift")
    packet_ids = {row["item_id"] for row in packets}
    key_ids = {row.get("item_id") for row in sealed["items"] if isinstance(row, dict)}
    if packet_ids != key_ids:
        raise PacketVerificationError("judge packets and sealed key disagree")

    manifest = _strict_json_bytes(observed["manifest.json"], "manifest")
    if (
        manifest.get("schema_version") != builder.MANIFEST_SCHEMA
        or manifest.get("status") != "unlabeled_design_only"
        or manifest.get("human_label_gate_satisfied") is not False
        or manifest.get("contains_labels") is not False
        or manifest.get("contains_results") is not False
    ):
        raise PacketVerificationError("manifest improperly claims labels or results")
    records = manifest.get("files")
    if not isinstance(records, list) or [row.get("path") for row in records] != list(
        builder.OUTPUT_FILENAMES
    ):
        raise PacketVerificationError("manifest file inventory drift")
    expected_records = [
        {
            "path": name,
            "size_bytes": len(observed[name]),
            "sha256": hashlib.sha256(observed[name]).hexdigest(),
        }
        for name in builder.OUTPUT_FILENAMES
    ]
    if records != expected_records or manifest.get("tree_sha256") != hashlib.sha256(
        builder.canonical_bytes(expected_records)
    ).hexdigest():
        raise PacketVerificationError("manifest file digest mismatch")
    identity = manifest.get("identity_scan")
    if not isinstance(identity, dict) or identity.get("matches") != 0 or identity.get(
        "passed"
    ) is not True:
        raise PacketVerificationError("manifest identity scan is not clean")
    if identity.get("visible_files") != list(visible_names):
        raise PacketVerificationError("manifest visible identity-scan scope drift")

    # Full semantic closure: independently audit the V2 source, derive every
    # candidate and blind packet again, then require byte identity.
    try:
        expected = builder.build_payloads(
            artifact_dir=artifact_dir,
            trust_root_path=trust_root_path,
            registration_path=registration_path,
        )
    except builder.PacketBuildError as exc:
        raise PacketVerificationError("cannot regenerate expected bundle") from exc
    if set(expected) != set(observed):
        raise PacketVerificationError("regenerated bundle path set drift")
    for name in sorted(expected):
        if observed[name] != expected[name]:
            raise PacketVerificationError(f"bundle differs from regeneration: {name}")
    return {
        "schema_version": builder.MANIFEST_SCHEMA,
        "study_id": manifest["study_id"],
        "status": manifest["status"],
        "unique_item_count": manifest["unique_item_count"],
        "primary_item_count": manifest["primary_item_count"],
        "identity_scan_passed": True,
        "human_label_gate_satisfied": False,
        "contains_results": False,
        "manifest_sha256": hashlib.sha256(observed["manifest.json"]).hexdigest(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, default=builder.DEFAULT_ARTIFACT)
    parser.add_argument("--trust-root", type=Path, default=builder.DEFAULT_TRUST_ROOT)
    parser.add_argument("--registration", type=Path, default=builder.DEFAULT_REGISTRATION)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = verify_bundle(
        args.bundle,
        artifact_dir=args.artifact_dir,
        trust_root_path=args.trust_root,
        registration_path=args.registration,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
