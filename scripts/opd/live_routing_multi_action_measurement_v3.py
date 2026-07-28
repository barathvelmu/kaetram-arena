#!/usr/bin/env python3
"""Prospective V3 measurement amendment for the unchanged V2 execution lane.

V2 receipts and V2 analysis remain immutable.  This module may score only a
future run whose clean prelaunch Git commit already contains this amendment.
It starts no service and performs no network or model call.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.opd.live_routing_multi_action_analyzer import (
    MultiActionAnalysisError,
    analyze_run as analyze_run_v2,
    classify_trial as classify_trial_v2,
)
from scripts.opd.live_routing_multi_action_diagnostic import (
    ACTIONS,
    ARMS,
    canonical_json,
    canonical_sha256,
)
from scripts.opd.live_routing_multi_action_prelaunch import (
    verify_prelaunch as verify_v2_prelaunch,
)
from scripts.opd.live_routing_multi_action_result_verify import verify_package


SCHEMA_VERSION = "kaetram.live-routing-multi-action-measurement-amendment.v3"
STUDY_ID = "local-live-routing-multi-action-v3"
STATUS = "registered_before_v3_live_execution"
ANALYSIS_SCHEMA_VERSION = "kaetram.live-routing-multi-action-analysis.v3"
ARTIFACT_SCHEMA_VERSION = "kaetram.live-routing-multi-action-analysis-artifact.v3"
PARENT_REGISTRATION_PATH = "research/experiments/local-live-routing-multi-action-v2.json"
PARENT_REGISTRATION_SHA256 = (
    "6755e7272fe06d62d2a19e38ad960a7247e32a0b4303be18f3689ce369710558"
)
PRESERVED_V2_SOURCE_COMMIT = "65b3bead4ccb59953c0860a5530c6c42199128db"
AMENDMENT_PATH = "research/experiments/local-live-routing-multi-action-v3.json"
AMENDMENT_SOURCE_PATHS = (
    AMENDMENT_PATH,
    "scripts/opd/live_routing_multi_action_measurement_v3.py",
    "tests/unit/test_live_routing_multi_action_measurement_v3.py",
)
EQUIPMENT_KEY_ALIASES = {
    "coppersword": "coppersword",
    "player/weapon/coppersword": "coppersword",
}
EXCLUDED_PRIOR_RUN = {
    "run_directory": "kaetram-live-routing-multi-action-run-20260728-v1",
    "prelaunch_git_head": PRESERVED_V2_SOURCE_COMMIT,
    "reason": "collected before this measurement amendment was frozen",
}
REPLACED_V2_MEASUREMENT_FAILURES = re.compile(
    r"^(?:"
    r"turn_[1-3]_(?:immediate|delayed)_(?:equip_item|eat_food|warp)_predicate_failed|"
    r"(?:reconnect|database)_(?:equip_item|eat_food|warp)_predicate_failed|"
    r"turn_[1-3]_(?:immediate|delayed)_off_baseline_failed|"
    r"(?:reconnect|database)_off_baseline_failed"
    r")$"
)


class MultiActionV3Error(ValueError):
    """The prospective measurement contract or evidence is not usable."""


def _load_json_strict(path: Path) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise MultiActionV3Error(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise MultiActionV3Error(f"non-finite JSON constant: {value}")

    try:
        value = json.loads(
            path.read_bytes(),
            object_pairs_hook=unique,
            parse_constant=reject_constant,
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise MultiActionV3Error(f"registration unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise MultiActionV3Error("registration root must be an object")
    return value


def validate_registration(registration: Mapping[str, Any]) -> list[str]:
    """Fail closed on all scientific fields of the small V3 amendment."""

    errors: list[str] = []
    expected_keys = {
        "schema_version", "study_id", "status", "amendment_scope",
        "prospective_gate", "measurement", "claim_boundary", "reporting",
    }
    if set(registration) != expected_keys:
        errors.append("registration top-level key set drift")
    if registration.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema version drift")
    if registration.get("study_id") != STUDY_ID:
        errors.append("study identity drift")
    if registration.get("status") != STATUS:
        errors.append("registration status drift")
    if registration.get("amendment_scope") != {
        "parent_registration_path": PARENT_REGISTRATION_PATH,
        "parent_registration_sha256": PARENT_REGISTRATION_SHA256,
        "preserved_v2_source_commit": PRESERVED_V2_SOURCE_COMMIT,
        "execution_change": "none",
        "receipt_schema_change": "none",
        "measurement_change_only": True,
        "prior_v2_result": EXCLUDED_PRIOR_RUN,
    }:
        errors.append("amendment scope drift")
    if registration.get("prospective_gate") != {
        "require_clean_v2_prelaunch": True,
        "prelaunch_git_head_must_contain": list(AMENDMENT_SOURCE_PATHS),
        "forbid_pre_amendment_data": True,
        "model_calls": 0,
        "remote_endpoints": "forbidden",
        "metered_services": "forbidden",
    }:
        errors.append("prospective gate drift")
    if registration.get("measurement") != {
        "equipment_key_aliases": EQUIPMENT_KEY_ALIASES,
        "equip_item_acceptance": (
            "Canonical coppersword absent from inventory and present in equipment; "
            "only the two explicitly listed raw keys are equivalent."
        ),
        "eat_food_acceptance": (
            "Apple absent from inventory and HP strictly above the registered 30 HP fixture."
        ),
        "warp_acceptance": "Position lies in Mudwich x=188..191, y=157..160.",
        "active_arm_acceptance": (
            "Every completed action predicate persists at immediate, delayed, reconnect, "
            "and database observations."
        ),
        "off_arm_acceptance": (
            "Zero candidate dispatches and none of the three registered action-effect "
            "signatures appears at any immediate, delayed, reconnect, or database observation."
        ),
        "off_arm_hp_policy": (
            "HP-only drift is ignored because passive regeneration is not a "
            "registered action effect."
        ),
    }:
        errors.append("measurement contract drift")
    claim = registration.get("claim_boundary")
    if not isinstance(claim, Mapping) or claim.get("confirmatory") is not False:
        errors.append("claim boundary drift")
    prohibited = claim.get("prohibited_claims") if isinstance(claim, Mapping) else None
    if (
        not isinstance(prohibited, list)
        or "retroactive validation of the V2 result" not in prohibited
    ):
        errors.append("retroactive-claim guard missing")
    reporting = registration.get("reporting")
    if not isinstance(reporting, Mapping) or (
        reporting.get("technical_repeats_are_independent") is not False
        or reporting.get("report_v2_and_v3_separately") is not True
        or reporting.get("never_relabel_v2_failures") is not True
    ):
        errors.append("reporting guard drift")
    return errors


def validate_registration_path(path: Path) -> list[str]:
    return validate_registration(_load_json_strict(path))


def canonical_item_key(value: Any) -> Any:
    """Apply the frozen, exhaustive alias table; do not guess new aliases."""

    return EQUIPMENT_KEY_ALIASES.get(value, value) if isinstance(value, str) else value


def registered_action_effects(projection: Mapping[str, Any]) -> dict[str, bool]:
    """Return the three positive V3 action-effect signatures."""

    inventory = projection.get("inventory")
    equipment = projection.get("equipment")
    pos = projection.get("pos")
    if not isinstance(inventory, list) or not isinstance(equipment, list):
        return {name: False for name in ACTIONS}
    inventory_keys = {
        canonical_item_key(row.get("key"))
        for row in inventory
        if isinstance(row, Mapping)
    }
    equipment_keys = {
        canonical_item_key(row.get("key"))
        for row in equipment
        if isinstance(row, Mapping)
    }
    hp = projection.get("hp")
    return {
        "equip_item": (
            "coppersword" not in inventory_keys
            and "coppersword" in equipment_keys
        ),
        "eat_food": (
            "apple" not in inventory_keys
            and type(hp) in (int, float)
            and hp > 30
        ),
        "warp": bool(
            isinstance(pos, Mapping)
            and type(pos.get("x")) in (int, float)
            and type(pos.get("y")) in (int, float)
            and 188 <= pos["x"] <= 191
            and 157 <= pos["y"] <= 160
        ),
    }


def cumulative_predicates_v3(
    projection: Mapping[str, Any], completed_actions: Sequence[str]
) -> dict[str, bool]:
    effects = registered_action_effects(projection)
    return {name: effects[name] for name in completed_actions}


def _projection(record: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(record, Mapping) or record.get("available") is not True:
        raise MultiActionV3Error(f"{label} is unavailable after V2 validation")
    projection = record.get("semantic_projection")
    if not isinstance(projection, Mapping):
        raise MultiActionV3Error(f"{label} semantic projection is unavailable")
    return projection


def classify_trial_v3(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Apply V3 measurement only after the unchanged V2 integrity validator."""

    parent = classify_trial_v2(receipt)
    plan = receipt.get("plan")
    if not isinstance(plan, Mapping):
        raise MultiActionV3Error("trial plan missing after V2 validation")
    arm = plan.get("arm")
    if arm not in ARMS:
        raise MultiActionV3Error("unregistered arm after V2 validation")
    if parent.get("validity") != "valid":
        return {
            "schema_version": ANALYSIS_SCHEMA_VERSION,
            "trial_id": parent.get("trial_id"),
            "arm": arm,
            "validity": "invalid",
            "outcome": "not_assessable",
            "invalid_reasons": parent.get("invalid_reasons", []),
            "failure_reasons": [],
            "action_predicates": {name: None for name in ACTIONS},
            "parent_v2_outcome": parent.get("outcome"),
        }

    treatment = receipt["treatment"]
    turns = treatment["turns"]
    order = plan["action_order"]
    active = arm != "content_recovery_off"
    # V3 replaces only V2's two frozen measurement definitions. Routing,
    # schema, delivery, protocol, tool-error, and invocation failures survive.
    failures = [
        reason
        for reason in parent.get("failure_reasons", [])
        if not REPLACED_V2_MEASUREMENT_FAILURES.fullmatch(reason)
    ]
    action_passes = {name: True for name in ACTIONS}
    completed: list[str] = []

    if not active:
        ledger = receipt["execution_evidence"]["candidate_call_ledger"]
        if ledger or any(turn.get("dispatch_attempted") is not False for turn in turns):
            failures.append("off_arm_nonzero_dispatch")

    for sequence, (turn, action) in enumerate(zip(turns, order, strict=True), start=1):
        if active:
            completed.append(action)
        for stage in ("immediate", "delayed"):
            projection = _projection(turn[stage], f"turn {sequence} {stage}")
            effects = registered_action_effects(projection)
            if active:
                for completed_action in completed:
                    action_passes[completed_action] &= effects[completed_action]
                    if not effects[completed_action]:
                        failures.append(
                            f"turn_{sequence}_{stage}_{completed_action}_predicate_failed"
                        )
            else:
                for effect, present in effects.items():
                    if present:
                        failures.append(f"turn_{sequence}_{stage}_{effect}_effect_present")

    final_rows = (
        ("reconnect", receipt["reconnect"]["reconnect"]),
        ("database", receipt["database"]),
    )
    for stage, record in final_rows:
        effects = registered_action_effects(_projection(record, stage))
        if active:
            for action in ACTIONS:
                action_passes[action] &= effects[action]
                if not effects[action]:
                    failures.append(f"{stage}_{action}_predicate_failed")
        else:
            for effect, present in effects.items():
                if present:
                    failures.append(f"{stage}_{effect}_effect_present")

    failures = list(dict.fromkeys(failures))
    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "trial_id": parent["trial_id"],
        "arm": arm,
        "validity": "valid",
        "outcome": "pass" if not failures else "fail",
        "invalid_reasons": [],
        "failure_reasons": failures,
        "action_predicates": (
            action_passes if active else {name: None for name in ACTIONS}
        ),
        "parent_v2_outcome": parent["outcome"],
    }


def analyze_run_v3(receipts: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate V3 scores while retaining the complete V2 aggregate audit."""

    parent = analyze_run_v2(receipts)
    trials = [classify_trial_v3(receipt) for receipt in receipts]
    arms: dict[str, Any] = {}
    for arm in ARMS:
        rows = [row for row in trials if row["arm"] == arm]
        arms[arm] = {
            "technical_trials": len(rows),
            "protocol_valid": sum(row["validity"] == "valid" for row in rows),
            "full_predicate_pass": sum(row["outcome"] == "pass" for row in rows),
            "behavioral_fail": sum(row["outcome"] == "fail" for row in rows),
            "invalid": sum(row["validity"] == "invalid" for row in rows),
            "action_predicate_pass": {
                action: sum(row["action_predicates"].get(action) is True for row in rows)
                for action in ACTIONS
            },
        }
    invalid = sum(row["validity"] == "invalid" for row in trials)
    failed = sum(row["outcome"] == "fail" for row in trials)
    result: dict[str, Any] = {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "verdict": (
            "incomplete_with_invalid_trials" if invalid else
            "complete_with_failures" if failed else "complete"
        ),
        "technical_trials": 9,
        "technical_repeats": 3,
        "technical_repeats_are_independent": False,
        "protocol_valid": 9 - invalid,
        "full_predicate_pass": sum(row["outcome"] == "pass" for row in trials),
        "behavioral_fail": failed,
        "invalid": invalid,
        "arms": arms,
        "trials": trials,
        "parent_v2_analysis_sha256": parent["payload_sha256"],
        "wording_guard": (
            "V3 is a prospective measurement amendment. Report it separately; "
            "never relabel the earlier V2 failures or call protocol-valid trials passes."
        ),
    }
    result["payload_sha256"] = canonical_sha256(result)
    return result


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_artifact_bytes(value: Mapping[str, Any]) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


def build_analysis_artifact(
    result_root: Path,
    registration_path: Path,
    *,
    parent_registration_path: Path,
    repo_root: Path,
) -> dict[str, Any]:
    """Reverify V2 and deterministically derive a separately sealed V3 score."""

    root = result_root.resolve()
    parent_verification = verify_package(root, repo_root=repo_root)
    eligibility = verify_prospective_prelaunch(
        root / "prelaunch.json",
        registration_path,
        parent_registration_path=parent_registration_path,
        repo_root=repo_root,
        require_clean_head=False,
    )
    receipts = [
        _load_json_strict(root / "receipts" / f"trial-{index:02d}.json")
        for index in range(1, 10)
    ]
    analysis = analyze_run_v3(receipts)
    unsigned: dict[str, Any] = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "study_id": STUDY_ID,
        "parent_result_binding": {
            "manifest_file_sha256": _sha256_file(root / "manifest.json"),
            "manifest_payload_sha256": parent_verification[
                "manifest_payload_sha256"
            ],
            "v2_verdict": parent_verification["verdict"],
            "v2_protocol_valid": parent_verification["protocol_valid"],
            "v2_full_predicate_pass": parent_verification[
                "full_predicate_pass"
            ],
        },
        "amendment_binding": {
            "registration_file_sha256": _sha256_file(registration_path),
            "eligible_prelaunch_git_head": eligibility["prelaunch_git_head"],
            "execution_contract_sha256": eligibility[
                "execution_contract_sha256"
            ],
        },
        "analysis": analysis,
    }
    return {**unsigned, "payload_sha256": canonical_sha256(unsigned)}


def write_analysis_artifact(path: Path, artifact: Mapping[str, Any]) -> None:
    """Create, never replace, the sibling V3 analysis artifact."""

    if path.is_symlink() or path.exists():
        raise MultiActionV3Error("V3 analysis output already exists or is unsafe")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise MultiActionV3Error("V3 analysis output parent is missing or unsafe")
    try:
        with path.open("xb") as handle:
            handle.write(_canonical_artifact_bytes(artifact))
    except OSError as exc:
        raise MultiActionV3Error(f"cannot create V3 analysis output: {exc}") from exc


def verify_analysis_artifact(
    artifact_path: Path,
    result_root: Path,
    registration_path: Path,
    *,
    parent_registration_path: Path,
    repo_root: Path,
) -> dict[str, Any]:
    """Recompute the complete artifact and require canonical byte equality."""

    if artifact_path.is_symlink() or not artifact_path.is_file():
        raise MultiActionV3Error("V3 analysis artifact is missing or unsafe")
    observed = _load_json_strict(artifact_path)
    expected = build_analysis_artifact(
        result_root,
        registration_path,
        parent_registration_path=parent_registration_path,
        repo_root=repo_root,
    )
    if observed != expected:
        raise MultiActionV3Error("V3 analysis differs from verified raw receipts")
    if artifact_path.read_bytes() != _canonical_artifact_bytes(expected):
        raise MultiActionV3Error("V3 analysis is not in canonical byte form")
    return {
        "verified": True,
        "verdict": expected["analysis"]["verdict"],
        "protocol_valid": expected["analysis"]["protocol_valid"],
        "full_predicate_pass": expected["analysis"]["full_predicate_pass"],
        "artifact_payload_sha256": expected["payload_sha256"],
    }


def verify_prospective_prelaunch(
    prelaunch_path: Path,
    registration_path: Path,
    *,
    parent_registration_path: Path,
    repo_root: Path,
    require_clean_head: bool = True,
) -> dict[str, Any]:
    """Require the clean V2 prelaunch head to contain this frozen V3 amendment."""

    try:
        prelaunch = verify_v2_prelaunch(
            prelaunch_path,
            parent_registration_path,
            repo_root=repo_root,
            require_clean_head=require_clean_head,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        raise MultiActionV3Error(f"parent V2 prelaunch is invalid: {exc}") from exc
    registration = _load_json_strict(registration_path)
    errors = validate_registration(registration)
    if errors:
        raise MultiActionV3Error("registration invalid: " + "; ".join(errors))
    head = prelaunch.get("git_head")
    if not isinstance(head, str) or len(head) != 40:
        raise MultiActionV3Error("prelaunch Git head is malformed")
    if head == PRESERVED_V2_SOURCE_COMMIT:
        raise MultiActionV3Error("pre-amendment V2 run is ineligible for V3 analysis")
    parent = prelaunch.get("registration")
    if not isinstance(parent, Mapping) or parent.get("sha256") != PARENT_REGISTRATION_SHA256:
        raise MultiActionV3Error("prelaunch does not bind the preserved V2 execution contract")
    root = repo_root.resolve()
    for relative in AMENDMENT_SOURCE_PATHS:
        try:
            blob = subprocess.run(
                ["git", "-C", str(root), "show", f"{head}:{relative}"],
                check=True,
                capture_output=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError) as exc:
            raise MultiActionV3Error(
                f"prelaunch Git head does not contain frozen V3 source: {relative}"
            ) from exc
        current = (root / relative).read_bytes()
        if blob != current:
            raise MultiActionV3Error(f"V3 source differs from prelaunch Git head: {relative}")
    return {
        "eligible": True,
        "prelaunch_git_head": head,
        "amendment_sha256": hashlib.sha256(registration_path.read_bytes()).hexdigest(),
        "execution_contract_sha256": PARENT_REGISTRATION_SHA256,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("analyze", "verify"):
        child = subparsers.add_parser(command)
        child.add_argument("--result-root", type=Path, required=True)
        child.add_argument("--analysis-artifact", type=Path, required=True)
        child.add_argument("--registration", type=Path, required=True)
        child.add_argument("--parent-registration", type=Path, required=True)
        child.add_argument("--repo-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "analyze":
            artifact = build_analysis_artifact(
                args.result_root,
                args.registration,
                parent_registration_path=args.parent_registration,
                repo_root=args.repo_root,
            )
            write_analysis_artifact(args.analysis_artifact, artifact)
            result = {
                "created": True,
                "verdict": artifact["analysis"]["verdict"],
                "protocol_valid": artifact["analysis"]["protocol_valid"],
                "full_predicate_pass": artifact["analysis"][
                    "full_predicate_pass"
                ],
                "artifact_payload_sha256": artifact["payload_sha256"],
            }
        else:
            result = verify_analysis_artifact(
                args.analysis_artifact,
                args.result_root,
                args.registration,
                parent_registration_path=args.parent_registration,
                repo_root=args.repo_root,
            )
    except (OSError, ValueError, RuntimeError, MultiActionV3Error) as exc:
        print(f"multi-action V3 analysis failed: {exc}")
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
