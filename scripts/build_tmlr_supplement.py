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

import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "research" / "artifacts" / "local-trigger-incidence-v2"
RESULTS = ROOT / "research" / "results" / "local-trigger-incidence-v2"
PAPER = ROOT / "output" / "pdf" / "kaetram-tool-routing-tmlr-draft.pdf"
OUTPUT = ROOT / "output" / "supplement" / "kaetram-tmlr-anonymous-supplement.zip"
REVIEW_SCHEMA = "kaetram.local-trigger-incidence-review-artifact.v1"
PACKAGE_SCHEMA = "kaetram.tmlr-anonymous-supplement.v2"
VERIFICATION_CODE = (
    "scripts/opd/analyze_structured_call_validity.py",
    "scripts/opd/audit_trigger_incidence_artifact.py",
    "scripts/opd/canonicalize.py",
    "scripts/opd/response_router.py",
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


def build_review_artifact(destination: Path) -> str:
    """Create the minimal anonymous projection and return its trust root."""

    registration = json.loads((ARTIFACT / "registration.json").read_text())
    design = json.loads((ARTIFACT / "design" / "design.json").read_text())
    analysis = json.loads(
        (ARTIFACT / "analysis" / "analysis-summary.json").read_text()
    )
    write_json(destination / "registration.json", _review_registration(registration))
    write_json(destination / "design" / "design.json", _review_design(design))
    write_json(
        destination / "analysis" / "analysis-summary.json",
        _review_analysis(analysis),
    )
    copy_file(
        RESULTS / "structured-call-validity-posthoc.json",
        destination / "analysis" / "routing-validity-posthoc.json",
    )
    for snapshot in registration["snapshots"]:
        source_run = ARTIFACT / "runs" / snapshot
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


def main() -> int:
    if not ARTIFACT.is_dir() or not PAPER.is_file():
        raise SystemExit("build the sealed artifact and TMLR PDF first")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kaetram-tmlr-supplement-") as temporary:
        stage = Path(temporary) / "kaetram-tmlr-anonymous-supplement"
        trust_root = build_review_artifact(stage / "artifact")
        copy_file(PAPER, stage / "paper.pdf")
        for name in (
            "paper-table-public.md",
            "structured-call-validity-posthoc.json",
        ):
            copy_file(RESULTS / name, stage / "results" / name)
        write_json(
            stage / "results" / "review-artifact-trust-root.json",
            {
                "schema_version": "kaetram.review-artifact-trust-root.v1",
                "artifact_index_sha256": trust_root,
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
        readme = f"""# Anonymous TMLR review supplement

This package contains the review manuscript and a review-only projection of the
V2 artifact. The projection preserves the registered contract, all 20 rendered
states, all 1,200 raw responses, and stored outcomes needed for independent
recomputation. Direct source-history, historical-path, model-host, and endpoint
coordinates are deferred until deanonymization.

Run from this directory:

```bash
python3 scripts/opd/verify_trigger_incidence_review_bundle.py \\
  --artifact-dir artifact \\
  --expected-index-sha256 {trust_root}
```

The command verifies every projected artifact byte, rejects duplicate or
non-finite JSON, checks the complete registered request schedule, reclassifies
the primary parser-recoverable outcome from raw response messages, and
recomputes all registered contrasts, the directional verdict, and response
heterogeneity. The review trust root authenticates this projection only; it is
not a public timestamp and it does not authenticate the deferred provenance.
"""
        (stage / "README.md").write_text(readme)
        records = _inventory(stage)
        manifest = {
            "schema_version": PACKAGE_SCHEMA,
            "review_artifact_index_sha256": trust_root,
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
