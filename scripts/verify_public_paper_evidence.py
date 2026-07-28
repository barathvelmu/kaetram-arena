#!/usr/bin/env python3
"""Verify every checked-in raw-evidence bundle used by the paper."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.opd.audit_trigger_incidence_artifact import (  # noqa: E402
    audit_artifact as audit_trigger_v1,
)
from scripts.opd.audit_trigger_incidence_artifact_v2 import (  # noqa: E402
    audit_artifact as audit_trigger_v2,
)
from scripts.opd.audit_trigger_seed_diversity import audit_seed_diversity  # noqa: E402
from scripts.opd.verify_trigger_incidence_artifact import (  # noqa: E402
    verify_bundle as verify_trigger_v1,
)
from scripts.opd.verify_trigger_incidence_artifact_v2 import (  # noqa: E402
    verify_bundle as verify_trigger_v2,
)
from scripts.score_july_public_artifact import verify_artifact  # noqa: E402


def verify_public_evidence(repo: Path = REPO) -> dict:
    july = verify_artifact(repo / "research" / "artifacts" / "july-score-replay-v1")
    trigger_root = (
        repo / "research" / "artifacts" / "local-trigger-incidence-v1"
    )
    trigger = verify_trigger_v1(trigger_root)
    independent = audit_trigger_v1(trigger_root)
    seed_diversity = audit_seed_diversity(trigger_root)
    if trigger["artifact_index_sha256"] != independent["artifact_index_sha256"]:
        raise RuntimeError("trigger verifier and independent auditor disagree")
    if (
        seed_diversity["artifact_index_sha256"]
        != trigger["artifact_index_sha256"]
    ):
        raise RuntimeError("seed audit examined a different trigger artifact")
    trigger_v2_root = (
        repo / "research" / "artifacts" / "local-trigger-incidence-v2"
    )
    trust_root = json.loads(
        (
            repo
            / "research"
            / "results"
            / "local-trigger-incidence-v2"
            / "artifact-trust-root.json"
        ).read_text()
    )
    expected_index = trust_root["artifact_index_sha256"]
    trigger_v2 = verify_trigger_v2(
        trigger_v2_root, expected_index_sha256=expected_index
    )
    independent_v2 = audit_trigger_v2(
        trigger_v2_root, expected_index_sha256=expected_index
    )
    if trigger_v2["artifact_index_sha256"] != independent_v2[
        "artifact_index_sha256"
    ]:
        raise RuntimeError("v2 trigger verifier and independent auditor disagree")
    return {
        "schema_version": "kaetram.public-paper-evidence-verification.v1",
        "july_score_replay": {
            "artifact_manifest_sha256": july["artifact_index"]["manifest_sha256"],
            "observation_count": july["observation_count"],
            "scores_manifest_sha256": july["scores"]["manifest_sha256"],
        },
        "trigger_incidence_v1": {
            **trigger,
            "independent_cell_count": independent["cell_count"],
            "independent_contrast_count": independent["contrast_count"],
            "state_condition_groups": seed_diversity["state_condition_groups"],
            "groups_with_identical_semantic_responses": seed_diversity[
                "groups_with_identical_semantic_responses"
            ],
            "semantic_response_count_after_deduplication": seed_diversity[
                "semantic_response_count_after_within_group_deduplication"
            ],
        },
        "trigger_incidence_v2": {
            **trigger_v2,
            "groups_with_multiple_semantic_responses": independent_v2[
                "groups_with_multiple_semantic_responses"
            ],
            "groups_with_primary_outcome_heterogeneity": independent_v2[
                "groups_with_primary_outcome_heterogeneity"
            ],
            "native_tools_effects": independent_v2["native_tools_effects"],
            "seed_gate_unique_semantic_responses": independent_v2[
                "seed_gate_unique_semantic_responses"
            ],
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO)
    args = parser.parse_args(argv)
    print(json.dumps(verify_public_evidence(args.repo_root), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
