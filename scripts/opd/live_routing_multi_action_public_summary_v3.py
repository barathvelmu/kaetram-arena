#!/usr/bin/env python3
"""Build or verify the anonymous summary of the fresh multi-action V3 run."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.opd.live_routing_multi_action_diagnostic import (  # noqa: E402
    canonical_json,
    canonical_sha256,
)
from scripts.opd.live_routing_multi_action_measurement_v3 import (  # noqa: E402
    verify_analysis_artifact,
)
from scripts.opd.live_routing_multi_action_public_summary_v2 import (  # noqa: E402
    _load_strict,
    _scan_forbidden,
    write_public_summary,
)
from scripts.opd.live_routing_multi_action_result_verify import (  # noqa: E402
    verify_package,
)


SCHEMA_VERSION = "kaetram.live-routing-multi-action-public-summary.v3-result.v1"
STUDY_ID = "local-live-routing-multi-action-v3"
EXPECTED_SOURCE_COMMIT = "cac19929e50495249d5520ba7dc0bb6cdcb93f20"
EXPECTED_MANIFEST_FILE_SHA256 = (
    "39dd655dc2aee60719fe88351a71e6245476b008131eaf4dcfa0fcb1b2396ef6"
)
EXPECTED_V3_ANALYSIS_FILE_SHA256 = (
    "bf2da305212185a3928ddda8960f20629bdc2c1eb36da086d3aafde6a62ce3a7"
)


class PublicSummaryV3Error(ValueError):
    """The retained V3 package or its public projection is not trustworthy."""


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


def build_public_summary(
    result_root: Path,
    analysis_artifact: Path,
    *,
    v3_registration: Path,
    parent_registration: Path,
    repo_root: Path,
) -> dict[str, Any]:
    """Reverify both frozen layers and expose only aggregate V3 outcomes."""

    root = result_root.resolve()
    parent = verify_package(root, repo_root=repo_root)
    v3_verification = verify_analysis_artifact(
        analysis_artifact,
        root,
        v3_registration,
        parent_registration_path=parent_registration,
        repo_root=repo_root,
    )
    artifact = _load_strict(analysis_artifact, label="V3 analysis artifact")
    prelaunch = _load_strict(root / "prelaunch.json", label="prelaunch")
    parent_analysis = _load_strict(root / "analysis.json", label="parent analysis")
    analysis = artifact.get("analysis")
    if not isinstance(analysis, Mapping):
        raise PublicSummaryV3Error("V3 analysis is missing")
    if (
        prelaunch.get("git_head") != EXPECTED_SOURCE_COMMIT
        or _sha256_file(root / "manifest.json") != EXPECTED_MANIFEST_FILE_SHA256
        or _sha256_file(analysis_artifact) != EXPECTED_V3_ANALYSIS_FILE_SHA256
        or parent.get("verified") is not True
        or v3_verification.get("verified") is not True
    ):
        raise PublicSummaryV3Error("inputs are not the retained fresh V3 result")

    expected_arm = {
        "technical_trials": 3,
        "protocol_valid": 3,
        "full_predicate_pass": 3,
        "behavioral_fail": 0,
        "invalid": 0,
    }
    arms = analysis.get("arms")
    if not isinstance(arms, Mapping) or set(arms) != {
        "structured_direct",
        "content_recovery_on",
        "content_recovery_off",
    }:
        raise PublicSummaryV3Error("V3 arm membership drift")
    for arm, row in arms.items():
        if not isinstance(row, Mapping) or any(
            row.get(key) != value for key, value in expected_arm.items()
        ):
            raise PublicSummaryV3Error(f"unexpected V3 outcome for {arm}")
    if (
        analysis.get("verdict") != "complete"
        or analysis.get("technical_trials") != 9
        or analysis.get("protocol_valid") != 9
        or analysis.get("full_predicate_pass") != 9
        or analysis.get("behavioral_fail") != 0
        or analysis.get("invalid") != 0
        or arms["structured_direct"].get("action_predicate_pass")
        != {"equip_item": 3, "eat_food": 3, "warp": 3}
        or arms["content_recovery_on"].get("action_predicate_pass")
        != {"equip_item": 3, "eat_food": 3, "warp": 3}
        or arms["content_recovery_off"].get("action_predicate_pass")
        != {"equip_item": 0, "eat_food": 0, "warp": 0}
    ):
        raise PublicSummaryV3Error("retained V3 aggregate differs from registration")

    public_arms = {
        arm: {
            "technical_trials": row["technical_trials"],
            "protocol_valid": row["protocol_valid"],
            "equip_item": (
                None if arm == "content_recovery_off"
                else row["action_predicate_pass"]["equip_item"]
            ),
            "eat_food": (
                None if arm == "content_recovery_off"
                else row["action_predicate_pass"]["eat_food"]
            ),
            "warp": (
                None if arm == "content_recovery_off"
                else row["action_predicate_pass"]["warp"]
            ),
            "no_registered_action_effect": (
                row["full_predicate_pass"]
                if arm == "content_recovery_off" else None
            ),
            "full_predicate_pass": row["full_predicate_pass"],
        }
        for arm, row in arms.items()
    }
    unsigned: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "study_id": STUDY_ID,
        "status": "complete",
        "evidence_binding": {
            "source_git_commit": EXPECTED_SOURCE_COMMIT,
            "public_summary_builder_file_sha256": _sha256_file(Path(__file__)),
            "parent_result_manifest_file_sha256": _sha256_file(
                root / "manifest.json"
            ),
            "parent_result_manifest_payload_sha256": parent[
                "manifest_payload_sha256"
            ],
            "parent_analysis_file_sha256": _sha256_file(root / "analysis.json"),
            "parent_analysis_payload_sha256": parent_analysis["payload_sha256"],
            "v3_analysis_artifact_file_sha256": _sha256_file(analysis_artifact),
            "v3_analysis_artifact_payload_sha256": artifact["payload_sha256"],
            "v3_analysis_payload_sha256": analysis["payload_sha256"],
            "v3_registration_file_sha256": _sha256_file(v3_registration),
            "parent_package_verifier": "passed",
            "v3_analysis_verifier": "passed",
        },
        "outcome": {
            "technical_trials": 9,
            "technical_repeats": 3,
            "technical_repeats_are_independent": False,
            "protocol_valid": 9,
            "protocol_invalid": 0,
            "full_predicate_pass": 9,
            "behavioral_fail": 0,
            "verdict": "complete",
        },
        "arms": public_arms,
        "measurement_history": {
            "parent_v2_verdict": parent["verdict"],
            "parent_v2_protocol_valid": parent["protocol_valid"],
            "parent_v2_full_predicate_pass": parent["full_predicate_pass"],
            "v2_relabelled": False,
            "fresh_post_amendment_run": True,
        },
        "claim_boundary": {
            "permitted": (
                "Preliminary within-build evidence that the three frozen calls "
                "were routed and their fixture-specific effects persisted under "
                "the prospective V3 measurement amendment."
            ),
            "prohibited": [
                "retroactive validation or relabeling of V2",
                "model quality or superiority",
                "causal recovery benefit",
                "quest-performance improvement",
                "checkpoint or training superiority",
                "generalization across tools, states, models, renderers, games, or environments",
                "statistical independence of technical repeats",
            ],
        },
    }
    summary = {**unsigned, "payload_sha256": canonical_sha256(unsigned)}
    _scan_forbidden(summary)
    return summary


def verify_public_summary(
    summary_path: Path,
    result_root: Path,
    analysis_artifact: Path,
    *,
    v3_registration: Path,
    parent_registration: Path,
    repo_root: Path,
) -> dict[str, Any]:
    observed = _load_strict(summary_path, label="V3 public summary")
    _scan_forbidden(observed)
    unsigned = {key: value for key, value in observed.items() if key != "payload_sha256"}
    if observed.get("payload_sha256") != canonical_sha256(unsigned):
        raise PublicSummaryV3Error("V3 public summary self-hash mismatch")
    expected = build_public_summary(
        result_root,
        analysis_artifact,
        v3_registration=v3_registration,
        parent_registration=parent_registration,
        repo_root=repo_root,
    )
    if observed != expected or summary_path.read_bytes() != _canonical_bytes(expected):
        raise PublicSummaryV3Error("V3 public summary differs from verified evidence")
    return {
        "verified": True,
        "verdict": expected["outcome"]["verdict"],
        "protocol_valid": expected["outcome"]["protocol_valid"],
        "full_predicate_pass": expected["outcome"]["full_predicate_pass"],
        "summary_payload_sha256": expected["payload_sha256"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("build", "verify"):
        child = subparsers.add_parser(command)
        child.add_argument("--result-root", type=Path, required=True)
        child.add_argument("--analysis-artifact", type=Path, required=True)
        child.add_argument("--v3-registration", type=Path, required=True)
        child.add_argument("--parent-registration", type=Path, required=True)
        child.add_argument("--repo-root", type=Path, required=True)
        child.add_argument("--summary", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "build":
            summary = build_public_summary(
                args.result_root,
                args.analysis_artifact,
                v3_registration=args.v3_registration,
                parent_registration=args.parent_registration,
                repo_root=args.repo_root,
            )
            write_public_summary(args.summary, summary)
            result = {"created": True, "summary_payload_sha256": summary["payload_sha256"]}
        else:
            result = verify_public_summary(
                args.summary,
                args.result_root,
                args.analysis_artifact,
                v3_registration=args.v3_registration,
                parent_registration=args.parent_registration,
                repo_root=args.repo_root,
            )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"multi-action V3 public summary failed: {exc}")
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
