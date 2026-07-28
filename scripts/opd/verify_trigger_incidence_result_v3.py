#!/usr/bin/env python3
"""Independently recompute a local V3 trigger-incidence result package.

The V2 public exporter/auditor is intentionally strict and rejects V3's extra
runtime-binding fields and two-commit provenance.  This verifier keeps those
bindings intact, verifies every local gate/run envelope, then uses the
independent V1/V2 audit implementation to reclassify raw responses and
recompute the finite-grid results.  It never starts a model or service.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.opd import audit_trigger_incidence_artifact as audit_v1  # noqa: E402
from scripts.opd import audit_trigger_incidence_artifact_v2 as audit_v2  # noqa: E402
from scripts.opd import prepare_trigger_incidence_v3 as prepare  # noqa: E402
from scripts.opd import trigger_incidence_probe as v1  # noqa: E402
from scripts.opd import trigger_incidence_probe_v2 as v2  # noqa: E402
from scripts.opd import trigger_incidence_probe_v3 as runtime  # noqa: E402


ProbeError = v1.ProbeError
AUDIT_SCHEMA = "kaetram.local-trigger-incidence-v3-result-verification.v1"


def _load_rows(path: Path) -> list[dict]:
    try:
        return audit_v2._load_jsonl(path)
    except audit_v2.AuditError as exc:
        raise ProbeError(str(exc)) from exc


def _independent_rows(
    binding: dict,
    run_dirs: list[Path],
) -> tuple[dict[tuple, dict], dict[str, dict]]:
    rows = {}
    identities = {}
    for run_dir in run_dirs:
        prelaunch, _postflight, _completed, _verified_rows, identity = (
            v1._verify_run_directory(run_dir, binding["effective_registration"])
        )
        snapshot = prelaunch.get("snapshot")
        if snapshot in identities:
            raise ProbeError("duplicate V3 result snapshot")
        runtime._require_runtime_preflight(prelaunch, binding)
        runtime._verify_checkpoint_grid(run_dir, binding, snapshot)
        raw_rows = _load_rows(run_dir / "results.jsonl")
        for row in raw_rows:
            key = (
                row.get("snapshot"),
                row.get("condition_id"),
                row.get("state_id"),
                row.get("sample_index"),
            )
            if key in rows:
                raise ProbeError(f"duplicate V3 result row: {key}")
            if row.get("status") == "ok":
                expected = audit_v1.classify_message(row.get("response_message"))
                if any(row.get(field) != value for field, value in expected.items()):
                    raise ProbeError(f"independent raw-response mismatch: {key}")
            elif row.get("status") == "failed":
                if audit_v1.OUTCOME_FIELDS.intersection(row):
                    raise ProbeError(f"failed V3 row carries outcome data: {key}")
            else:
                raise ProbeError(f"unknown V3 row status: {key}")
            rows[key] = row
        identities[snapshot] = identity
    if set(identities) != set(binding["effective_registration"]["snapshots"]):
        raise ProbeError("V3 result requires each registered checkpoint exactly once")
    expected_count = binding["expected_request_count"]
    if len(rows) != expected_count:
        raise ProbeError("V3 raw rows do not cover the exact registered grid")
    try:
        audit_v2._verify_detailed_outcomes(rows)
    except audit_v2.AuditError as exc:
        raise ProbeError(str(exc)) from exc
    return rows, identities


def _seed_heterogeneity(registration: dict, rows: dict[tuple, dict]) -> dict:
    complete = all(row.get("status") == "ok" for row in rows.values())
    if not complete:
        return {
            "status": "not_evaluated_incomplete_grid",
            "state_condition_groups": 0,
            "groups_with_multiple_semantic_responses": 0,
            "groups_with_primary_outcome_heterogeneity": 0,
            "minimum_unique_semantic_responses_per_group": None,
            "maximum_unique_semantic_responses_per_group": None,
        }
    try:
        return audit_v2._recompute_seed_heterogeneity(registration, rows)
    except audit_v2.AuditError as exc:
        raise ProbeError(str(exc)) from exc


def _directional(registration: dict, recomputed: dict) -> dict:
    complete = recomputed["analysis_status"] == "complete"
    effects = {
        row["snapshot"]: row["effect_rate_difference"]
        for row in recomputed["registered_contrasts"]
        if row["contrast"] == "native_tools_main"
    }
    return {
        "criterion": registration["analysis"]["directional_replication_criterion"],
        "status": "evaluated" if complete else "not_evaluated_incomplete_grid",
        "native_tools_effects": effects,
        "passed": (
            all(
                snapshot in effects and effects[snapshot] > 0
                for snapshot in registration["snapshots"]
            )
            if complete
            else None
        ),
    }


def _verify_gates(
    binding: dict,
    seed_gate_dirs: list[Path],
    run_dirs: list[Path],
) -> dict[str, dict]:
    run_health = {}
    for run_dir in run_dirs:
        prelaunch = prepare._read_json(run_dir / "prelaunch.json")
        run_health[prelaunch.get("snapshot")] = prelaunch.get("endpoint_health")
    gates = {}
    for gate_dir in seed_gate_dirs:
        preflight = prepare._read_json(gate_dir / "preflight.json")
        snapshot = preflight.get("snapshot")
        if snapshot in gates or snapshot not in run_health:
            raise ProbeError("duplicate or unregistered V3 seed gate")
        receipt = runtime._verify_seed_gate_binding(
            gate_dir,
            binding,
            snapshot,
            run_health[snapshot],
        )
        gates[snapshot] = receipt
    if set(gates) != set(binding["effective_registration"]["snapshots"]):
        raise ProbeError("V3 result requires one passed seed gate per checkpoint")
    for run_dir in run_dirs:
        prelaunch = prepare._read_json(run_dir / "prelaunch.json")
        runtime._verify_checkpoint_grid(
            run_dir,
            binding,
            prelaunch["snapshot"],
            gates[prelaunch["snapshot"]],
        )
    return gates


def _verify_analysis(
    analysis_dir: Path,
    binding: dict,
    rows: dict[tuple, dict],
    run_identities: dict[str, dict],
) -> dict:
    try:
        audit_v2._verify_internal_index(
            analysis_dir,
            ("analysis-summary.json", "cells.csv", "contrasts.csv"),
        )
        analysis = audit_v2.load_object(analysis_dir / "analysis-summary.json")
    except audit_v2.AuditError as exc:
        raise ProbeError(str(exc)) from exc
    registration = binding["effective_registration"]
    design = binding["design"]
    recomputed = audit_v1.recompute_summary(registration, design, rows)
    heterogeneity = _seed_heterogeneity(registration, rows)
    directional = _directional(registration, recomputed)
    expected_keys = {
        "schema_version",
        "study_id",
        "registration_sha256",
        "design_sha256",
        "analysis_code_provenance",
        "input_runs",
        "analysis_status",
        "scheduled_requests",
        "successful_requests",
        "failed_requests",
        "recovery_opportunities",
        "claim_boundary",
        "cells",
        "registered_contrasts",
        "registered_seed_heterogeneity",
        "directional_replication",
        "v3_runtime_binding",
    }
    provenance = analysis.get("analysis_code_provenance")
    expected_inputs = sorted(
        (
            {
                "snapshot": snapshot,
                "artifact_index_sha256": identity["artifact_index_sha256"],
                "tree_sha256": identity["tree_sha256"],
            }
            for snapshot, identity in run_identities.items()
        ),
        key=lambda item: item["snapshot"],
    )
    if (
        set(analysis) != expected_keys
        or analysis.get("schema_version") != v1.ANALYSIS_SCHEMA
        or analysis.get("study_id") != binding["study_id"]
        or analysis.get("registration_sha256")
        != binding["effective_registration_sha256"]
        or analysis.get("design_sha256") != binding["design_sha256"]
        or analysis.get("claim_boundary") != registration["claim_boundary"]
        or analysis.get("input_runs") != expected_inputs
        or analysis.get("v3_runtime_binding") != runtime._public_binding(binding)
        or not isinstance(provenance, dict)
        or set(provenance)
        != {
            "source_git_commit",
            "dirty_paths",
            "analysis_script_sha256",
            "python_version",
            "runtime_binding_adapter_sha256",
        }
        or provenance.get("source_git_commit") != binding["execution_commit"]
        or provenance.get("dirty_paths") != []
        or provenance.get("analysis_script_sha256")
        != v1.sha256_file(Path(v2.__file__).resolve())
        or provenance.get("runtime_binding_adapter_sha256")
        != v1.sha256_file(Path(runtime.__file__).resolve())
        or re.fullmatch(r"\d+\.\d+\.\d+", str(provenance.get("python_version", "")))
        is None
    ):
        raise ProbeError("V3 analysis identity or provenance is invalid")
    for field, value in recomputed.items():
        if analysis.get(field) != value:
            raise ProbeError(f"independent V3 analysis mismatch: {field}")
    if analysis.get("registered_seed_heterogeneity") != heterogeneity:
        raise ProbeError("independent V3 seed-heterogeneity mismatch")
    if analysis.get("directional_replication") != directional:
        raise ProbeError("independent V3 directional-replication mismatch")
    if (analysis_dir / "cells.csv").read_bytes() != audit_v2._csv_bytes(
        recomputed["cells"]
    ):
        raise ProbeError("V3 cells.csv differs from independent reanalysis")
    if (analysis_dir / "contrasts.csv").read_bytes() != audit_v2._csv_bytes(
        recomputed["registered_contrasts"]
    ):
        raise ProbeError("V3 contrasts.csv differs from independent reanalysis")
    return analysis


def verify_result(
    registration_path: Path,
    historical_root: Path,
    design_dir: Path,
    run_dirs: list[Path],
    seed_gate_dirs: list[Path],
    analysis_dir: Path,
) -> dict:
    binding = runtime.validate_execution_binding(
        registration_path, historical_root, design_dir
    )
    gates = _verify_gates(binding, seed_gate_dirs, run_dirs)
    rows, run_identities = _independent_rows(binding, run_dirs)
    analysis = _verify_analysis(analysis_dir, binding, rows, run_identities)
    return {
        "schema_version": AUDIT_SCHEMA,
        "study_id": binding["study_id"],
        "execution_commit": binding["execution_commit"],
        "design_sha256": binding["design_sha256"],
        "expected_request_grid_sha256": binding["expected_request_grid_sha256"],
        "analysis_artifact_index_sha256": v1.sha256_file(
            analysis_dir / "artifact-index.json"
        ),
        "run_artifact_indexes": {
            snapshot: identity["artifact_index_sha256"]
            for snapshot, identity in sorted(run_identities.items())
        },
        "seed_gate_artifact_indexes": {
            snapshot: receipt["artifact_index_sha256"]
            for snapshot, receipt in sorted(gates.items())
        },
        "analysis_status": analysis["analysis_status"],
        "scheduled_requests": analysis["scheduled_requests"],
        "successful_requests": analysis["successful_requests"],
        "failed_requests": analysis["failed_requests"],
        "claim_boundary_sha256": binding["claim_boundary_sha256"],
        "independent_recomputation": True,
        "verification_script_sha256": v1.sha256_file(Path(__file__).resolve()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registration", type=Path, default=REPO / prepare.REGISTRATION_PATH
    )
    parser.add_argument("--historical-root", type=Path, required=True)
    parser.add_argument("--design-dir", type=Path, default=REPO / runtime.DESIGN_DIR)
    parser.add_argument("--run-dir", type=Path, action="append", required=True)
    parser.add_argument("--seed-gate-dir", type=Path, action="append", required=True)
    parser.add_argument("--analysis-dir", type=Path, required=True)
    args = parser.parse_args()
    result = verify_result(
        args.registration,
        args.historical_root,
        args.design_dir,
        args.run_dir,
        args.seed_gate_dir,
        args.analysis_dir,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
