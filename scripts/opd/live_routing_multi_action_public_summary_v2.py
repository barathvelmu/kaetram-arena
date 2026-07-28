#!/usr/bin/env python3
"""Build or verify the redacted public summary of the sealed V2 result."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.opd.live_routing_multi_action_diagnostic import (
    ACTIONS,
    ARMS,
    canonical_json,
    canonical_sha256,
    cumulative_predicates,
    multi_action_documents,
    semantic_gameplay_projection,
)
from scripts.opd.live_routing_multi_action_result_verify import verify_package


SCHEMA_VERSION = "kaetram.live-routing-multi-action-public-summary.v3"
STUDY_ID = "local-live-routing-multi-action-v2"
EXPECTED_SOURCE_COMMIT = "65b3bead4ccb59953c0860a5530c6c42199128db"
EXPECTED_MANIFEST_FILE_SHA256 = (
    "b790bfc2522553632cf07a50c1d73632c5bf28514a9ef5984c5455e6898e951a"
)
EXPECTED_REGISTRATION_FILE_SHA256 = (
    "6755e7272fe06d62d2a19e38ad960a7247e32a0b4303be18f3689ce369710558"
)
EQUIPMENT_ALIASES = {
    "coppersword": "coppersword",
    "player/weapon/coppersword": "coppersword",
}
FORBIDDEN_FIELD_NAMES = {
    "absolute_path",
    "browser_launch_nonce",
    "browser_pid",
    "browser_process_group",
    "database_snapshot_ownership",
    "document_ids",
    "endpoint",
    "host",
    "mongo_database",
    "mcp_instance_nonce",
    "mcp_pid",
    "mcp_process_group",
    "nonce",
    "password",
    "pid",
    "port",
    "process_lifecycle",
    "raw_text",
    "reconnect_session_id",
    "run_id",
    "service",
    "session_id",
    "token",
    "treatment_session_id",
    "trial_id",
    "trial_key",
    "username",
}
FORBIDDEN_STRING_PATTERNS = (
    re.compile(r"(?:^|\s)/(?:Users|home|private|var/folders)/"),
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"\b(?:mongodb(?:\+srv)?://|https?://(?:localhost|127\.0\.0\.1))", re.I),
    re.compile(r"\bllrma-[0-9a-f]{8}-t\d{2}\b"),
    re.compile(r"\bma_[0-9a-f]{8}_\d{2}\b"),
)


class PublicSummaryV2Error(ValueError):
    """The private package or public projection is unsafe or inconsistent."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PublicSummaryV2Error(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise PublicSummaryV2Error(f"non-finite JSON constant: {value}")


def _load_strict(path: Path, *, label: str) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise PublicSummaryV2Error(f"{label} is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise PublicSummaryV2Error(f"{label} must be a regular non-symlink file")
    try:
        value = json.loads(
            path.read_bytes(),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise PublicSummaryV2Error(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise PublicSummaryV2Error(f"{label} root must be an object")
    return value


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_equal(left: Any, right: Any) -> bool:
    try:
        return canonical_json(left) == canonical_json(right)
    except (TypeError, ValueError):
        return False


def _projection(record: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(record, Mapping) or record.get("available") is not True:
        raise PublicSummaryV2Error(f"{label} is unavailable after package verification")
    projection = record.get("semantic_projection")
    if not isinstance(projection, Mapping):
        raise PublicSummaryV2Error(f"{label} semantic projection is unavailable")
    return projection


def _action_effects(projection: Mapping[str, Any]) -> dict[str, bool]:
    inventory = projection.get("inventory")
    equipment = projection.get("equipment")
    pos = projection.get("pos")
    if not isinstance(inventory, list) or not isinstance(equipment, list):
        return {action: False for action in ACTIONS}
    inventory_keys = {
        EQUIPMENT_ALIASES.get(row.get("key"), row.get("key"))
        for row in inventory
        if isinstance(row, Mapping)
    }
    equipment_keys = {
        EQUIPMENT_ALIASES.get(row.get("key"), row.get("key"))
        for row in equipment
        if isinstance(row, Mapping)
    }
    hp = projection.get("hp")
    return {
        "equip_item": "coppersword" not in inventory_keys and "coppersword" in equipment_keys,
        "eat_food": "apple" not in inventory_keys and type(hp) in (int, float) and hp > 30,
        "warp": bool(
            isinstance(pos, Mapping)
            and type(pos.get("x")) in (int, float)
            and type(pos.get("y")) in (int, float)
            and 188 <= pos["x"] <= 191
            and 157 <= pos["y"] <= 160
        ),
    }


def _active_measurements(receipt: Mapping[str, Any]) -> list[tuple[str, str, Mapping[str, Any]]]:
    order = receipt["plan"]["action_order"]
    turns = receipt["treatment"]["turns"]
    rows: list[tuple[str, str, Mapping[str, Any]]] = []
    completed: list[str] = []
    for sequence, (turn, action) in enumerate(zip(turns, order, strict=True), start=1):
        completed.append(action)
        for stage in ("immediate", "delayed"):
            projection = _projection(turn[stage], f"turn {sequence} {stage}")
            rows.extend((name, stage, projection) for name in completed)
    for stage, record in (
        ("reconnect", receipt["reconnect"]["reconnect"]),
        ("database", receipt["database"]),
    ):
        projection = _projection(record, stage)
        rows.extend((name, stage, projection) for name in ACTIONS)
    return rows


def _off_measurements(receipt: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for sequence, turn in enumerate(receipt["treatment"]["turns"], start=1):
        rows.extend(
            _projection(turn[stage], f"turn {sequence} {stage}")
            for stage in ("immediate", "delayed")
        )
    rows.append(_projection(receipt["reconnect"]["reconnect"], "reconnect"))
    rows.append(_projection(receipt["database"], "database"))
    return rows


def _differs_only_by_hp(projection: Mapping[str, Any], baseline: Mapping[str, Any]) -> bool:
    if _json_equal(projection, baseline):
        return False
    changed = deepcopy(dict(projection))
    changed["hp"] = baseline.get("hp")
    return projection.get("hp") != baseline.get("hp") and _json_equal(changed, baseline)


def _scan_forbidden(value: Any, *, location: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise PublicSummaryV2Error(f"non-string public field at {location}")
            if key.lower() in FORBIDDEN_FIELD_NAMES:
                raise PublicSummaryV2Error(f"forbidden public field at {location}.{key}")
            _scan_forbidden(child, location=f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_forbidden(child, location=f"{location}[{index}]")
    elif isinstance(value, str):
        if any(pattern.search(value) for pattern in FORBIDDEN_STRING_PATTERNS):
            raise PublicSummaryV2Error(f"forbidden identity, path, or service string at {location}")


def build_public_summary(result_root: Path, *, repo_root: Path) -> dict[str, Any]:
    """Reverify the sealed package and derive every released count."""

    if result_root.is_symlink() or not result_root.is_dir():
        raise PublicSummaryV2Error("private result root is missing or unsafe")
    root = result_root.resolve()
    verification = verify_package(root, repo_root=repo_root)
    manifest_path = root / "manifest.json"
    analysis_path = root / "analysis.json"
    registration_path = root / "registration.json"
    prelaunch = _load_strict(root / "prelaunch.json", label="prelaunch")
    analysis = _load_strict(analysis_path, label="analysis")
    receipts = [
        _load_strict(root / "receipts" / f"trial-{index:02d}.json", label=f"trial {index}")
        for index in range(1, 10)
    ]

    manifest_file_sha256 = _sha256_file(manifest_path)
    registration_file_sha256 = _sha256_file(registration_path)
    source_commit = prelaunch.get("git_head")
    if manifest_file_sha256 != EXPECTED_MANIFEST_FILE_SHA256:
        raise PublicSummaryV2Error("private package is not the retained V2 result")
    if registration_file_sha256 != EXPECTED_REGISTRATION_FILE_SHA256:
        raise PublicSummaryV2Error("private registration is not the frozen V2 contract")
    if source_commit != EXPECTED_SOURCE_COMMIT:
        raise PublicSummaryV2Error("private source commit is not the frozen V2 execution")

    active = [row for row in receipts if row["plan"]["arm"] != "content_recovery_off"]
    off = [row for row in receipts if row["plan"]["arm"] == "content_recovery_off"]
    action_measurement_counts = {action: 0 for action in ACTIONS}
    action_effect_counts = {action: 0 for action in ACTIONS}
    namespaced_equipment = 0
    plain_equipment = 0
    for receipt in active:
        for action, _stage, projection in _active_measurements(receipt):
            action_measurement_counts[action] += 1
            action_effect_counts[action] += int(_action_effects(projection)[action])
            if action == "equip_item":
                keys = {
                    row.get("key")
                    for row in projection.get("equipment", [])
                    if isinstance(row, Mapping)
                }
                namespaced_equipment += int("player/weapon/coppersword" in keys)
                plain_equipment += int("coppersword" in keys)
    if len(set(action_measurement_counts.values())) != 1:
        raise PublicSummaryV2Error("active semantic measurement schedule is unbalanced")

    baseline = semantic_gameplay_projection(
        {"documents": multi_action_documents(receipts[0]["plan"]["username"])}
    )
    off_rows = [projection for receipt in off for projection in _off_measurements(receipt)]
    off_exact = sum(_json_equal(row, baseline) for row in off_rows)
    off_hp_only = sum(_differs_only_by_hp(row, baseline) for row in off_rows)

    arm_outcomes = {
        arm: {
            "equip_item": analysis["arms"][arm]["action_predicate_pass"]["equip_item"],
            "eat_food": analysis["arms"][arm]["action_predicate_pass"]["eat_food"],
            "warp": analysis["arms"][arm]["action_predicate_pass"]["warp"],
            "technical_trials": analysis["arms"][arm]["technical_trials"],
        }
        for arm in ARMS
    }
    for action in ACTIONS:
        arm_outcomes["content_recovery_off"][action] = None

    all_turns = [turn for receipt in receipts for turn in receipt["treatment"]["turns"]]
    active_turns = [turn for receipt in active for turn in receipt["treatment"]["turns"]]
    structured_turns = [
        turn
        for receipt in receipts
        if receipt["plan"]["arm"] == "structured_direct"
        for turn in receipt["treatment"]["turns"]
    ]
    recovery_on_turns = [
        turn
        for receipt in receipts
        if receipt["plan"]["arm"] == "content_recovery_on"
        for turn in receipt["treatment"]["turns"]
    ]
    off_turns = [turn for receipt in off for turn in receipt["treatment"]["turns"]]
    scheduled_active_calls = sum(
        receipt["plan"]["expected_candidate_invocations"] for receipt in active
    )
    candidate_rows = [
        row
        for receipt in active
        for row in receipt["execution_evidence"]["candidate_call_ledger"]
    ]
    equip_tool_results = [
        turn.get("result_json")
        for receipt in active
        for turn in receipt["treatment"]["turns"]
        if turn.get("action") == "equip_item"
    ]
    equip_tool_results_confirmed = sum(
        isinstance(result, Mapping)
        and result.get("equipped") is True
        and result.get("item") == "coppersword"
        and result.get("slot") == 3
        for result in equip_tool_results
    )

    namespaced_accepted = cumulative_predicates(
        {
            "inventory": [],
            "equipment": [{"slot": "weapon", "key": "player/weapon/coppersword", "count": 1}],
            "hp": 30,
            "pos": {"x": 328, "y": 892},
        },
        ["equip_item"],
    )["equip_item"]

    if (
        action_measurement_counts != {action: 36 for action in ACTIONS}
        or action_effect_counts != {action: 36 for action in ACTIONS}
        or namespaced_equipment != 30
        or plain_equipment != 6
        or equip_tool_results_confirmed != 6
        or len(off_rows) != 24
        or off_exact != 3
        or off_hp_only != 21
        or any(row.get("hp") != 31 for row in off_rows if not _json_equal(row, baseline))
        or namespaced_accepted is not False
    ):
        raise PublicSummaryV2Error(
            "private receipts do not support the frozen post-outcome audit"
        )

    unsigned: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "study_id": STUDY_ID,
        "status": analysis["verdict"],
        "evidence_binding": {
            "source_git_commit": source_commit,
            "public_summary_builder_file_sha256": _sha256_file(Path(__file__)),
            "result_manifest_file_sha256": manifest_file_sha256,
            "result_manifest_payload_sha256": verification["manifest_payload_sha256"],
            "analysis_file_sha256": _sha256_file(analysis_path),
            "analysis_payload_sha256": analysis["payload_sha256"],
            "registration_file_sha256": registration_file_sha256,
            "package_verifier": "passed",
        },
        "registered_outcome": {
            "technical_trials": analysis["technical_trials"],
            "protocol_valid": analysis["protocol_valid"],
            "protocol_invalid": analysis["invalid"],
            "full_predicate_pass": analysis["full_predicate_pass"],
            "behavioral_fail": analysis["behavioral_fail"],
            "verdict": analysis["verdict"],
        },
        "registered_action_predicate_pass_by_arm": arm_outcomes,
        "protocol_delivery": {
            "active_trials": len(active),
            "scheduled_active_calls": scheduled_active_calls,
            "schema_valid_calls": sum(turn.get("schema_status") == "valid" for turn in active_turns),
            "confirmed_deliveries": sum(row.get("delivery_status") == "confirmed" for row in candidate_rows),
            "protocol_successes": sum(row.get("protocol_success") is True for row in candidate_rows),
            "tool_errors": sum(turn.get("tool_reported_error") is not None for turn in all_turns),
            "structured_direct_router_status": {
                "not_applicable_structured": sum(
                    turn.get("router_status") == "not_applicable_structured"
                    for turn in structured_turns
                )
            },
            "content_recovery_on_router_status": {
                "promoted": sum(turn.get("router_status") == "promoted" for turn in recovery_on_turns)
            },
            "content_recovery_off": {
                "turns": len(off_turns),
                "dispatch_attempted": sum(turn.get("dispatch_attempted") is True for turn in off_turns),
                "candidate_ledger_entries": sum(
                    len(receipt["execution_evidence"]["candidate_call_ledger"])
                    for receipt in off
                ),
            },
        },
        "post_outcome_measurement_audit": {
            "status": "descriptive_only_does_not_change_registered_outcome",
            "active_semantic_measurements_per_action": next(iter(action_measurement_counts.values())),
            "equip_item_semantic_effect_observed": action_effect_counts["equip_item"],
            "eat_food_semantic_effect_observed": action_effect_counts["eat_food"],
            "warp_semantic_effect_observed": action_effect_counts["warp"],
            "equip_projection_mismatch": {
                "client_or_reconnect_namespaced_key": namespaced_equipment,
                "database_plain_key": plain_equipment,
                "registered_predicate_accepted_namespaced_key": namespaced_accepted,
            },
            "off_arm_semantic_measurements": len(off_rows),
            "off_arm_exact_baseline_measurements": off_exact,
            "off_arm_measurements_differing_only_by_passive_hp_regeneration": off_hp_only,
        },
        "measurement_failures": [
            "The registered equipment predicate rejected the client key player/weapon/coppersword even though inventory, client equipment, reconnect, database, and tool-result evidence showed the sword was equipped.",
            "The recovery-off predicate required complete baseline equality, so passive HP regeneration from 30 to 31 caused failure even though position, inventory, equipment, and the empty dispatch ledger showed that no registered action occurred.",
        ],
        "claim_boundary": {
            "permitted": "The registered local diagnostic completed without protocol invalidity, exposed two measurement defects, and preserved exact evidence for a prospective correction.",
            "prohibited": [
                "retroactively counting any V2 trial as a full-predicate pass",
                "model quality or superiority",
                "causal recovery benefit",
                "quest-performance improvement",
                "generalization across tools, states, models, renderers, games, or environments",
                "statistical independence of technical repeats",
            ],
        },
    }
    summary = {**unsigned, "payload_sha256": canonical_sha256(unsigned)}
    _scan_forbidden(summary)
    return summary


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


def write_public_summary(path: Path, summary: Mapping[str, Any]) -> None:
    if path.is_symlink() or path.exists():
        raise PublicSummaryV2Error("public summary output already exists or is unsafe")
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise PublicSummaryV2Error("public summary output parent is missing or unsafe")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        try:
            os.write(descriptor, _canonical_bytes(summary))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise PublicSummaryV2Error(f"cannot create public summary: {exc}") from exc


def verify_public_summary(
    summary_path: Path, result_root: Path, *, repo_root: Path
) -> dict[str, Any]:
    observed = _load_strict(summary_path, label="public summary")
    _scan_forbidden(observed)
    unsigned = {key: value for key, value in observed.items() if key != "payload_sha256"}
    if observed.get("payload_sha256") != canonical_sha256(unsigned):
        raise PublicSummaryV2Error("public summary self-hash mismatch")
    expected = build_public_summary(result_root, repo_root=repo_root)
    if observed != expected:
        raise PublicSummaryV2Error("public summary differs from the verified private package")
    if summary_path.read_bytes() != _canonical_bytes(expected):
        raise PublicSummaryV2Error("public summary is not in canonical byte form")
    return {
        "verified": True,
        "verdict": expected["registered_outcome"]["verdict"],
        "protocol_valid": expected["registered_outcome"]["protocol_valid"],
        "full_predicate_pass": expected["registered_outcome"]["full_predicate_pass"],
        "summary_payload_sha256": expected["payload_sha256"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("build", "verify"):
        child = subparsers.add_parser(command)
        child.add_argument("--result-root", type=Path, required=True)
        child.add_argument("--repo-root", type=Path, required=True)
        child.add_argument("--summary", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "build":
            summary = build_public_summary(args.result_root, repo_root=args.repo_root)
            write_public_summary(args.summary, summary)
            result = {"created": True, "summary_payload_sha256": summary["payload_sha256"]}
        else:
            result = verify_public_summary(
                args.summary, args.result_root, repo_root=args.repo_root
            )
    except (OSError, ValueError, RuntimeError, PublicSummaryV2Error) as exc:
        print(f"multi-action V2 public summary failed: {exc}")
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
