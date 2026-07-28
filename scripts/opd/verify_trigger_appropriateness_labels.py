#!/usr/bin/env python3
"""Verify sealed human labels and merge them without computing aggregates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.opd import build_trigger_appropriateness_packets as builder  # noqa: E402
from scripts.opd import verify_trigger_appropriateness_packets as packet_verifier  # noqa: E402


LABEL_SCHEMA = "kaetram.trigger-appropriateness-human-label.v1"
ADJUDICATION_SCHEMA = "kaetram.trigger-appropriateness-adjudication.v1"
SEALS_SCHEMA = "kaetram.trigger-appropriateness-label-seals.v1"
MERGED_SCHEMA = "kaetram.trigger-appropriateness-merged-label.v1"
RECEIPT_SCHEMA = "kaetram.trigger-appropriateness-label-completion.v1"


class LabelVerificationError(RuntimeError):
    pass


def _safe_regular_file(path: Path, label: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise LabelVerificationError(f"{label} is missing") from exc
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise LabelVerificationError(f"{label} is not a regular file")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise LabelVerificationError(f"cannot read {label}") from exc


def _strict_json(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            payload,
            object_pairs_hook=builder._unique_object,
            parse_constant=builder._reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, builder.PacketBuildError) as exc:
        raise LabelVerificationError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise LabelVerificationError(f"{label} root is not an object")
    return value


def _strict_jsonl(payload: bytes, label: str) -> list[dict[str, Any]]:
    rows = []
    for index, line in enumerate(payload.splitlines(), start=1):
        rows.append(_strict_json(line, f"{label} line {index}"))
    if not rows:
        raise LabelVerificationError(f"{label} is empty")
    return rows


def _packet_item_ids(bundle_dir: Path) -> list[str]:
    payload = _safe_regular_file(bundle_dir / "judge-packets.jsonl", "judge packets")
    rows = packet_verifier._verify_packet_lines(payload)
    return [row["item_id"] for row in rows]


def _validate_labels(
    payload: bytes,
    *,
    role: str,
    expected_ids: list[str],
    precondition_values: set[str],
    appropriateness_values: set[str],
) -> list[dict[str, Any]]:
    rows = _strict_jsonl(payload, role)
    if len(rows) != len(expected_ids):
        raise LabelVerificationError(f"{role} does not label every item")
    expected_keys = {
        "schema_version",
        "item_id",
        "reviewer_role",
        "precondition_support",
        "strategic_appropriateness",
        "concise_rationale",
    }
    for expected_id, row in zip(expected_ids, rows, strict=True):
        rationale = row.get("concise_rationale")
        if (
            set(row) != expected_keys
            or row.get("schema_version") != LABEL_SCHEMA
            or row.get("item_id") != expected_id
            or row.get("reviewer_role") != role
            or row.get("precondition_support") not in precondition_values
            or row.get("strategic_appropriateness") not in appropriateness_values
            or not isinstance(rationale, str)
            or not rationale.strip()
            or len(rationale) > 2000
        ):
            raise LabelVerificationError(f"{role} has an incomplete or invalid label")
    return rows


def _validate_seals(
    payload: bytes,
    *,
    reviewer_a_sha256: str,
    reviewer_b_sha256: str,
    adjudication_sha256: str,
) -> dict[str, Any]:
    seals = _strict_json(payload, "label seals")
    expected_keys = {
        "schema_version",
        "reviewer_a",
        "reviewer_b",
        "both_reviewer_files_sealed_before_adjudication",
        "adjudicator_human_id",
        "adjudication_file_sha256",
    }
    reviewer_keys = {
        "human_reviewer_id",
        "file_sha256",
        "independent",
        "completed_without_access_to_other_labels",
        "completed_without_access_to_sealed_key",
    }
    if set(seals) != expected_keys or seals.get("schema_version") != SEALS_SCHEMA:
        raise LabelVerificationError("label seal schema drift")
    identities = []
    for role, expected_sha in (
        ("reviewer_a", reviewer_a_sha256),
        ("reviewer_b", reviewer_b_sha256),
    ):
        record = seals.get(role)
        identity = record.get("human_reviewer_id") if isinstance(record, dict) else None
        if (
            not isinstance(record, dict)
            or set(record) != reviewer_keys
            or not isinstance(identity, str)
            or not identity.strip()
            or len(identity) > 200
            or record.get("file_sha256") != expected_sha
            or record.get("independent") is not True
            or record.get("completed_without_access_to_other_labels") is not True
            or record.get("completed_without_access_to_sealed_key") is not True
        ):
            raise LabelVerificationError(f"{role} is not independently hash-sealed")
        identities.append(identity.strip().casefold())
    adjudicator = seals.get("adjudicator_human_id")
    if (
        len(set(identities)) != 2
        or seals.get("both_reviewer_files_sealed_before_adjudication") is not True
        or not isinstance(adjudicator, str)
        or not adjudicator.strip()
        or len(adjudicator) > 200
        or adjudicator.strip().casefold() in set(identities)
        or seals.get("adjudication_file_sha256") != adjudication_sha256
    ):
        raise LabelVerificationError("reviewer independence or adjudication seal failed")
    return seals


def verify_and_merge(
    *,
    bundle_dir: Path,
    reviewer_a_path: Path,
    reviewer_b_path: Path,
    adjudication_path: Path,
    seals_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    # This regeneration check prevents a relabeled or resealed packet bundle
    # from silently changing the judging task.
    try:
        packet_result = packet_verifier.verify_bundle(bundle_dir)
    except packet_verifier.PacketVerificationError as exc:
        raise LabelVerificationError("unlabeled packet bundle failed verification") from exc
    if packet_result.get("human_label_gate_satisfied") is not False:
        raise LabelVerificationError("input packet bundle is not the unlabeled design")
    bundle_dir = bundle_dir.resolve()
    item_ids = _packet_item_ids(bundle_dir)
    protocol = _strict_json(
        _safe_regular_file(bundle_dir / "judge-protocol.json", "judge protocol"),
        "judge protocol",
    )
    rubric = protocol.get("rubric")
    if not isinstance(rubric, dict):
        raise LabelVerificationError("judge rubric is missing")
    precondition_values = set(rubric.get("precondition_support") or [])
    appropriateness_values = set(rubric.get("strategic_appropriateness") or [])

    reviewer_a_payload = _safe_regular_file(reviewer_a_path, "reviewer A labels")
    reviewer_b_payload = _safe_regular_file(reviewer_b_path, "reviewer B labels")
    adjudication_payload = _safe_regular_file(adjudication_path, "adjudication")
    seals_payload = _safe_regular_file(seals_path, "label seals")
    reviewer_a = _validate_labels(
        reviewer_a_payload,
        role="reviewer_a",
        expected_ids=item_ids,
        precondition_values=precondition_values,
        appropriateness_values=appropriateness_values,
    )
    reviewer_b = _validate_labels(
        reviewer_b_payload,
        role="reviewer_b",
        expected_ids=item_ids,
        precondition_values=precondition_values,
        appropriateness_values=appropriateness_values,
    )
    adjudication = _strict_jsonl(adjudication_payload, "adjudication")
    if len(adjudication) != len(item_ids):
        raise LabelVerificationError("adjudication does not cover every item")
    _validate_seals(
        seals_payload,
        reviewer_a_sha256=hashlib.sha256(reviewer_a_payload).hexdigest(),
        reviewer_b_sha256=hashlib.sha256(reviewer_b_payload).hexdigest(),
        adjudication_sha256=hashlib.sha256(adjudication_payload).hexdigest(),
    )

    adjudication_keys = {
        "schema_version",
        "item_id",
        "adjudication_required",
        "resolved_precondition_support",
        "resolved_strategic_appropriateness",
        "concise_rationale",
    }
    merged = []
    disagreement_count = 0
    for item_id, row_a, row_b, resolution in zip(
        item_ids, reviewer_a, reviewer_b, adjudication, strict=True
    ):
        disagrees = (
            row_a["precondition_support"] != row_b["precondition_support"]
            or row_a["strategic_appropriateness"]
            != row_b["strategic_appropriateness"]
        )
        if (
            set(resolution) != adjudication_keys
            or resolution.get("schema_version") != ADJUDICATION_SCHEMA
            or resolution.get("item_id") != item_id
            or resolution.get("adjudication_required") is not disagrees
        ):
            raise LabelVerificationError("adjudication disagreement map is incomplete")
        if disagrees:
            disagreement_count += 1
            rationale = resolution.get("concise_rationale")
            if (
                resolution.get("resolved_precondition_support")
                not in precondition_values
                or resolution.get("resolved_strategic_appropriateness")
                not in appropriateness_values
                or not isinstance(rationale, str)
                or not rationale.strip()
                or len(rationale) > 2000
            ):
                raise LabelVerificationError("a reviewer disagreement is unresolved")
            final_precondition = resolution["resolved_precondition_support"]
            final_appropriateness = resolution["resolved_strategic_appropriateness"]
            final_rationale = rationale
            resolution_kind = "human_adjudication"
        else:
            if any(
                resolution.get(field) is not None
                for field in (
                    "resolved_precondition_support",
                    "resolved_strategic_appropriateness",
                    "concise_rationale",
                )
            ):
                raise LabelVerificationError("agreement row contains post-hoc adjudication")
            final_precondition = row_a["precondition_support"]
            final_appropriateness = row_a["strategic_appropriateness"]
            final_rationale = None
            resolution_kind = "reviewer_agreement"
        merged.append(
            {
                "schema_version": MERGED_SCHEMA,
                "item_id": item_id,
                "precondition_support": final_precondition,
                "strategic_appropriateness": final_appropriateness,
                "resolution": resolution_kind,
                "adjudication_rationale": final_rationale,
            }
        )
    merged_payload = builder.jsonl_bytes(merged)
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "study_id": packet_result["study_id"],
        "status": "human_label_gate_complete_no_aggregate_computed",
        "packet_manifest_sha256": packet_result["manifest_sha256"],
        "reviewer_a_sha256": hashlib.sha256(reviewer_a_payload).hexdigest(),
        "reviewer_b_sha256": hashlib.sha256(reviewer_b_payload).hexdigest(),
        "adjudication_sha256": hashlib.sha256(adjudication_payload).hexdigest(),
        "seals_sha256": hashlib.sha256(seals_payload).hexdigest(),
        "merged_labels_sha256": hashlib.sha256(merged_payload).hexdigest(),
        "item_count": len(merged),
        "disagreement_count": disagreement_count,
        "missing_label_count": 0,
        "unresolved_disagreement_count": 0,
        "aggregate_computed": False,
    }
    return merged, receipt


def write_completion(
    output_path: Path,
    merged: list[dict[str, Any]],
    receipt: dict[str, Any],
) -> tuple[Path, Path]:
    output_path = output_path.resolve()
    receipt_path = output_path.with_suffix(output_path.suffix + ".receipt.json")
    if output_path.exists() or receipt_path.exists():
        raise LabelVerificationError("refusing to overwrite merged labels or receipt")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged_payload = builder.jsonl_bytes(merged)
    receipt_payload = builder.pretty_bytes(receipt)
    for path, payload in ((output_path, merged_payload), (receipt_path, receipt_payload)):
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    return output_path, receipt_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--reviewer-a", type=Path, required=True)
    parser.add_argument("--reviewer-b", type=Path, required=True)
    parser.add_argument("--adjudication", type=Path, required=True)
    parser.add_argument("--seals", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    merged, receipt = verify_and_merge(
        bundle_dir=args.bundle,
        reviewer_a_path=args.reviewer_a,
        reviewer_b_path=args.reviewer_b,
        adjudication_path=args.adjudication,
        seals_path=args.seals,
    )
    write_completion(args.out, merged, receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
