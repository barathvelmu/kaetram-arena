#!/usr/bin/env python3
"""Build a deterministic, review-only TMLR supplement.

The checked-in public artifact intentionally retains full provenance.  A
double-blind submission must not copy that artifact verbatim: its Git revisions,
historical source paths, and model-host coordinates are direct identity
locators.  This builder therefore creates a minimal scientific projection with
the raw responses and all fields needed by the standalone verifier, but without
source-history coordinates.  The projection receives a new review-only trust
root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.opd.live_routing_review_projection import load_review_projection


ARTIFACT = ROOT / "research" / "artifacts" / "local-trigger-incidence-v2"
RESULTS = ROOT / "research" / "results" / "local-trigger-incidence-v2"
V3_ARTIFACT = ROOT / "research" / "artifacts" / "local-trigger-incidence-v3"
V3_RESULTS = ROOT / "research" / "results" / "local-trigger-incidence-v3"
MULTI_V2_SUMMARY = (
    ROOT
    / "research"
    / "results"
    / "local-live-routing-multi-action-v2"
    / "public-summary.json"
)
MULTI_V3_SUMMARY = (
    ROOT
    / "research"
    / "results"
    / "local-live-routing-multi-action-v3"
    / "public-summary.json"
)
PAPER = ROOT / "output" / "pdf" / "kaetram-tool-routing-tmlr-draft.pdf"
OUTPUT = ROOT / "output" / "supplement" / "kaetram-tmlr-anonymous-supplement.zip"
REVIEW_SCHEMA = "kaetram.local-trigger-incidence-review-artifact.v1"
PACKAGE_SCHEMA = "kaetram.tmlr-anonymous-supplement.v5"
VERIFICATION_CODE = (
    "scripts/opd/analyze_reasoning_span_localization.py",
    "scripts/opd/analyze_structured_call_validity.py",
    "scripts/opd/audit_trigger_incidence_artifact.py",
    "scripts/opd/canonicalize.py",
    "scripts/opd/live_routing_review_projection.py",
    "scripts/opd/response_router.py",
    "scripts/opd/verify_live_routing_review_projection.py",
    "scripts/opd/verify_trigger_incidence_review_bundle.py",
    "tool_surface.py",
)
SHA40 = re.compile(rb"(?i)(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])")
FORBIDDEN = (
    b"/Users/",
    b"/home/",
    b"github.com/barath",
    b"github.com/patnir",
    b"huggingface.co/patnir",
    b"modal.run",
    b"dataset/raw/",
    b"run_20260608_185339",
    b"kaetram-arena contributors",
)


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
        + b"\n"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def _review_registration(source: dict) -> dict:
    """Keep the registered analysis contract while removing provenance locators."""

    return {
        "schema_version": source["schema_version"],
        "study_id": source["study_id"],
        "status": source["status"],
        "purpose": source["purpose"],
        "snapshots": {
            name: {"api_model": value["api_model"]}
            for name, value in source["snapshots"].items()
        },
        "conditions": source["conditions"],
        "sampling": source["sampling"],
        "state_pool": {
            "state_count": source["state_pool"]["state_count"],
            "decision_turn": source["state_pool"]["decision_turn"],
            "outcome_independence": source["state_pool"]["outcome_independence"],
        },
        "outcomes": source["outcomes"],
        "analysis": source["analysis"],
        "claim_boundary": source["claim_boundary"],
        "review_projection": {
            "source_history_authentication": "deferred_until_deanonymized",
            "model_host_authentication": "deferred_until_deanonymized",
        },
    }


def _review_design(source: dict) -> dict:
    """Retain every rendered state but no source-log coordinate or source hash."""

    return {
        "schema_version": source["schema_version"],
        "study_id": source["study_id"],
        "states": [
            {"state_id": state["state_id"], "messages": state["messages"]}
            for state in source["states"]
        ],
        "review_projection": {
            "historical_source_coordinates": "deferred_until_deanonymized"
        },
    }


def _review_analysis(source: dict) -> dict:
    """Retain stored outcomes used by the independent recomputation only."""

    fields = (
        "schema_version",
        "study_id",
        "analysis_status",
        "scheduled_requests",
        "successful_requests",
        "failed_requests",
        "recovery_opportunities",
        "cells",
        "registered_contrasts",
        "registered_seed_heterogeneity",
        "directional_replication",
        "claim_boundary",
    )
    projected = {field: source[field] for field in fields}
    projected["review_projection"] = {
        "analysis_source_history": "deferred_until_deanonymized"
    }
    return projected


def _review_run_envelope(source: dict, *, kind: str) -> dict:
    projected = {"snapshot": source["snapshot"]}
    if kind in {"postflight", "completed"}:
        projected["endpoint_identity_stable"] = source["endpoint_identity_stable"]
    projected["review_projection"] = {
        "endpoint_and_source_attestation": "deferred_until_deanonymized"
    }
    return projected


def _inventory(root: Path) -> list[dict]:
    records = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.name == "artifact-index.json" and path.parent == root:
            continue
        records.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return records


def build_review_artifact(
    destination: Path,
    *,
    artifact: Path = ARTIFACT,
    results: Path = RESULTS,
    registration_relative: Path = Path("registration.json"),
    include_routing_posthoc: bool = True,
) -> str:
    """Create the minimal anonymous projection and return its trust root."""

    registration = json.loads((artifact / registration_relative).read_text())
    design = json.loads((artifact / "design" / "design.json").read_text())
    analysis = json.loads(
        (artifact / "analysis" / "analysis-summary.json").read_text()
    )
    write_json(destination / "registration.json", _review_registration(registration))
    write_json(destination / "design" / "design.json", _review_design(design))
    write_json(
        destination / "analysis" / "analysis-summary.json",
        _review_analysis(analysis),
    )
    if include_routing_posthoc:
        copy_file(
            results / "structured-call-validity-posthoc.json",
            destination / "analysis" / "routing-validity-posthoc.json",
        )
    for snapshot in registration["snapshots"]:
        source_run = artifact / "runs" / snapshot
        destination_run = destination / "runs" / snapshot
        for kind in ("prelaunch", "postflight", "completed"):
            source = json.loads((source_run / f"{kind}.json").read_text())
            write_json(
                destination_run / f"{kind}.json",
                _review_run_envelope(source, kind=kind),
            )
        copy_file(source_run / "results.jsonl", destination_run / "results.jsonl")
    records = _inventory(destination)
    index = {
        "schema_version": REVIEW_SCHEMA,
        "study_id": registration["study_id"],
        "projection_boundary": {
            "preserved": "registered contract, rendered states, raw responses, outcomes",
            "deferred": "source history, source paths, model host, endpoint attestation",
        },
        "verification_code": [
            {"path": name, "sha256": sha256_file(ROOT / name)}
            for name in VERIFICATION_CODE
        ],
        "files": records,
        "tree_sha256": hashlib.sha256(canonical_json_bytes(records).rstrip()).hexdigest(),
    }
    write_json(destination / "artifact-index.json", index)
    return sha256_file(destination / "artifact-index.json")


def audit_review_tree(root: Path) -> None:
    """Reject direct identity locators in every textual review-package member."""

    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.suffix.lower() == ".pdf":
            continue
        payload = path.read_bytes()
        lowered = payload.lower()
        for forbidden in FORBIDDEN:
            if forbidden.lower() in lowered:
                raise SystemExit(f"identity or endpoint fragment in supplement: {path}")
        if SHA40.search(payload):
            raise SystemExit(f"40-hex source-control fingerprint in supplement: {path}")


def add_live_routing_projection(stage: Path, source: Path) -> Path:
    """Validate and copy the anonymous one-action projection into the ZIP stage."""

    projection, raw = load_review_projection(source)
    if projection["scope"] != "single_fixture_descriptive_routing_check_no_model_calls":
        raise SystemExit("live-routing projection scope drift")
    destination = stage / "results" / "local-routing-diagnostic-review.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    return destination


def add_multi_action_review_summary(stage: Path) -> Path:
    """Add a hash-free anonymous projection of the sealed V2/V3 summaries."""

    v2 = json.loads(MULTI_V2_SUMMARY.read_text())
    v3 = json.loads(MULTI_V3_SUMMARY.read_text())
    if (
        v2.get("status") != "complete_with_failures"
        or v2.get("registered_outcome", {}).get("protocol_valid") != 9
        or v2.get("registered_outcome", {}).get("full_predicate_pass") != 0
        or v3.get("status") != "complete"
        or v3.get("outcome", {}).get("protocol_valid") != 9
        or v3.get("outcome", {}).get("full_predicate_pass") != 9
        or v3.get("measurement_history", {}).get("v2_relabelled") is not False
        or v3.get("measurement_history", {}).get("fresh_post_amendment_run")
        is not True
    ):
        raise SystemExit("multi-action public summaries drifted from the sealed result")
    projection = {
        "schema_version": "kaetram.live-routing-multi-action-review-summary.v1",
        "v2": {
            "status": v2["status"],
            "registered_outcome": v2["registered_outcome"],
            "protocol_delivery": v2["protocol_delivery"],
            "registered_action_predicate_pass_by_arm": v2[
                "registered_action_predicate_pass_by_arm"
            ],
            "measurement_failures": v2["measurement_failures"],
            "post_outcome_measurement_audit": v2[
                "post_outcome_measurement_audit"
            ],
            "claim_boundary": v2["claim_boundary"],
        },
        "v3": {
            "status": v3["status"],
            "outcome": v3["outcome"],
            "arms": v3["arms"],
            "measurement_history": v3["measurement_history"],
            "claim_boundary": v3["claim_boundary"],
        },
        "review_projection": {
            "private_package_authentication": "deferred_until_deanonymized",
            "technical_repeats_are_independent": False,
        },
    }
    destination = stage / "results" / "local-routing-multi-action-review.json"
    write_json(destination, projection)
    return destination


def require_local_untracked_output() -> None:
    """Refuse to build a double-blind ZIP into the public Git index."""

    relative = OUTPUT.relative_to(ROOT).as_posix()
    inside = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--is-inside-work-tree"],
        check=False,
        capture_output=True,
        text=True,
    )
    if inside.returncode != 0:
        return
    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--error-unmatch", "--", relative],
        check=False,
        capture_output=True,
        text=True,
    )
    ignored = subprocess.run(
        ["git", "-C", str(ROOT), "check-ignore", "--quiet", "--", relative],
        check=False,
        capture_output=True,
        text=True,
    )
    if tracked.returncode == 0 or ignored.returncode != 0:
        raise SystemExit(
            "anonymous supplement output must be ignored and absent from the Git index"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-routing-projection",
        type=Path,
        help="validated anonymous projection of the completed local routing diagnostic",
    )
    args = parser.parse_args(argv)
    if not ARTIFACT.is_dir() or not V3_ARTIFACT.is_dir() or not PAPER.is_file():
        raise SystemExit("build both sealed trigger artifacts and the TMLR PDF first")
    require_local_untracked_output()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kaetram-tmlr-supplement-") as temporary:
        stage = Path(temporary) / "kaetram-tmlr-anonymous-supplement"
        v2_trust_root = build_review_artifact(stage / "artifact-v2")
        v3_trust_root = build_review_artifact(
            stage / "artifact-v3",
            artifact=V3_ARTIFACT,
            results=V3_RESULTS,
            registration_relative=Path("design/effective-registration.json"),
        )
        copy_file(PAPER, stage / "paper.pdf")
        for name in (
            "paper-table-public.md",
            "structured-call-validity-posthoc.json",
        ):
            copy_file(RESULTS / name, stage / "results" / "v2" / name)
        copy_file(
            V3_RESULTS / "paper-table-public.md",
            stage / "results" / "v3" / "paper-table-public.md",
        )
        copy_file(
            V3_RESULTS / "structured-call-validity-posthoc.json",
            stage / "results" / "v3" / "structured-call-validity-posthoc.json",
        )
        add_multi_action_review_summary(stage)
        if args.live_routing_projection is not None:
            add_live_routing_projection(stage, args.live_routing_projection)
        write_json(
            stage / "results" / "review-artifact-trust-root.json",
            {
                "schema_version": "kaetram.review-artifact-trust-root.v1",
                "v2_artifact_index_sha256": v2_trust_root,
                "v3_artifact_index_sha256": v3_trust_root,
                "source_history_authentication": "deferred_until_deanonymized",
            },
        )
        for name in VERIFICATION_CODE:
            copy_file(ROOT / name, stage / name)
        (stage / "LICENSE-REVIEW.txt").write_text(
            "Review-package source code is licensed under the MIT License.\n"
            "Copyright (c) 2026 Anonymous authors.\n\n"
            "Permission is hereby granted, free of charge, to any person obtaining "
            "a copy of this software and associated documentation files to deal in "
            "the Software without restriction, subject to preservation of this notice.\n"
        )
        routing_note = ""
        if args.live_routing_projection is not None:
            routing_note = """

The file results/local-routing-diagnostic-review.json is a validated anonymous
projection of a separate, model-free, one-action routing diagnostic. It retains
the nine neutral trial rows and descriptive arm totals only. The private source
package, runtime identities, and source-history authentication are deliberately
deferred until deanonymization. Its three repeats per arm are technical repeats,
not independent samples, and the recovery-off registered predicate failed in
all three repeats because exact database equality did not survive login/save
materialization even though no candidate was invoked and client state remained
at baseline.

Validate that projection's canonical encoding, self-hash, nine-row schedule,
recomputed arm totals, and narrow completed-result claim boundary with:

```bash
python3 scripts/opd/verify_live_routing_review_projection.py \\
  --projection results/local-routing-diagnostic-review.json
```
"""
        readme = f"""# Anonymous TMLR review supplement

This package contains the review manuscript and review-only projections of the
V2 and V3 trigger-incidence artifacts. Each projection preserves its registered
contract, all 20 rendered states, all 1,200 raw responses, and stored outcomes
needed for independent recomputation. Direct source-history, historical-path,
model-host, and endpoint coordinates are deferred until deanonymization.

Run from this directory:

```bash
python3 scripts/opd/verify_trigger_incidence_review_bundle.py \\
  --artifact-dir artifact-v2 \\
  --expected-index-sha256 {v2_trust_root}

python3 scripts/opd/verify_trigger_incidence_review_bundle.py \\
  --artifact-dir artifact-v3 \\
  --expected-index-sha256 {v3_trust_root}
```

These commands verify every projected artifact byte, reject duplicate or
non-finite JSON, checks the complete registered request schedule, reclassifies
the primary parser-recoverable outcome from raw response messages, and
recomputes all registered contrasts, the directional verdict, and response
heterogeneity. The review trust roots authenticate these projections only;
they are not public timestamps and do not authenticate deferred provenance.

The file `results/local-routing-multi-action-review.json` preserves the sealed
aggregate outcomes from the model-free routing diagnostic without source
revisions or private runtime identities. V2 remains 9/9 protocol-valid and
0/9 full-predicate-pass; the fresh post-amendment V3 run is separately 9/9 on
both counts. Its three repeats per arm are dependent technical checks.
{routing_note}
"""
        (stage / "README.md").write_text(readme)
        records = _inventory(stage)
        manifest = {
            "schema_version": PACKAGE_SCHEMA,
            "v2_review_artifact_index_sha256": v2_trust_root,
            "v3_review_artifact_index_sha256": v3_trust_root,
            "files": records,
            "tree_sha256": hashlib.sha256(canonical_json_bytes(records).rstrip()).hexdigest(),
        }
        write_json(stage / "package-manifest.json", manifest)
        audit_review_tree(stage)
        with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(item for item in stage.rglob("*") if item.is_file()):
                relative = Path(stage.name) / path.relative_to(stage)
                info = zipfile.ZipInfo(relative.as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())
    print(
        f"built {OUTPUT} ({OUTPUT.stat().st_size} bytes, "
        f"sha256={sha256_file(OUTPUT)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
