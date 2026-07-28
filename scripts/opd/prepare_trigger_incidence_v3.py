#!/usr/bin/env python3
"""Prepare the prospectively frozen V3 trigger-incidence state panel.

V3 changes only the retained historical state pool.  The request grid,
checkpoint identities, renderer, sampling, outcomes, and analysis remain
byte-bound to the V2 registration.  Preparation is deliberately unavailable
until the V3 registration and this code are in a clean, pushed commit.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.opd import trigger_incidence_probe as v1  # noqa: E402
from scripts.opd import trigger_incidence_probe_v2 as v2  # noqa: E402


REGISTRATION_SCHEMA = "kaetram.local-trigger-incidence-v3-registration.v1"
PREPARATION_SCHEMA = "kaetram.local-trigger-incidence-v3-preparation.v1"
EXPECTED_STUDY_ID = "local-trigger-incidence-seeded-v3"
REGISTRATION_PATH = Path("research/experiments/local-trigger-incidence-v3.json")
HEX64 = re.compile(r"[0-9a-f]{64}")
ProbeError = v1.ProbeError
INHERITED_FIELDS = {
    "snapshots",
    "endpoint_contract",
    "seed_gate",
    "factors",
    "conditions",
    "sampling",
    "outcomes",
}
OVERRIDDEN_FIELDS = {
    "study_id",
    "status",
    "purpose",
    "claim_boundary",
    "state_pool",
    "analysis.directional_replication_criterion",
    "analysis.estimand_unit",
    "analysis.reporting",
    "provenance.source_identity",
    "provenance.execution_gate",
}
IDENTITY_PATTERNS = {
    "macOS user path": re.compile(r"/Users/", re.IGNORECASE),
    "Linux home path": re.compile(r"/home/", re.IGNORECASE),
    "author handle": re.compile(r"(?:barath|patnir)", re.IGNORECASE),
    "deployment hostname": re.compile(r"modal\.run", re.IGNORECASE),
    "email-like identifier": re.compile(r"[\w.+-]+@[\w.-]+"),
}


def _read_json(path: Path) -> Any:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ProbeError(f"duplicate JSON key in {path}: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ProbeError(f"non-finite JSON value in {path}: {value}")

    try:
        if path.is_symlink():
            raise ProbeError(f"refusing symlinked JSON input: {path}")
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_constant,
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeError(f"cannot load JSON {path}: {exc}") from exc


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _validate_repo_relative(value: Any, *, label: str) -> Path:
    path = Path(str(value))
    if not str(value) or path.is_absolute() or ".." in path.parts:
        raise ProbeError(f"{label} must be a repository-relative path")
    return path


def load_registration(path: Path) -> tuple[dict, str]:
    registration = _read_json(path)
    if (
        not isinstance(registration, dict)
        or registration.get("schema_version") != REGISTRATION_SCHEMA
        or registration.get("study_id") != EXPECTED_STUDY_ID
        or registration.get("status") != "registered_execution_prohibited"
    ):
        raise ProbeError("unexpected V3 registration identity or status")
    baseline = registration.get("frozen_v2_protocol")
    state_pool = registration.get("state_pool")
    execution_gate = registration.get("execution_gate")
    if not isinstance(baseline, dict) or not isinstance(state_pool, dict):
        raise ProbeError("V3 registration lacks protocol or state-pool contract")
    if (
        set(baseline.get("inherit_exactly", [])) != INHERITED_FIELDS
        or set(baseline.get("overridden_before_materialization", []))
        != OVERRIDDEN_FIELDS
    ):
        raise ProbeError("V3 registration does not enumerate exact inherited overrides")
    if (
        not isinstance(execution_gate, dict)
        or execution_gate.get("require_registration_and_builder_pushed") is not True
        or execution_gate.get("require_design_committed_and_pushed_before_requests")
        is not True
        or execution_gate.get("outcome_inspection_during_selection") is not False
    ):
        raise ProbeError("V3 registration lacks the fail-closed execution gate")
    for key in ("path", "excluded_design"):
        owner = baseline if key == "path" else state_pool
        _validate_repo_relative(owner.get(key), label=key)
    for key in (
        "sha256",
        "excluded_design_sha256",
        "matched_source_logs_sha256",
        "matched_source_metadata_sha256",
        "eligible_source_logs_sha256",
        "archive_sha256sums_sha256",
        "archive_inventory_sha256",
    ):
        owner = baseline if key == "sha256" else state_pool
        if HEX64.fullmatch(str(owner.get(key, ""))) is None:
            raise ProbeError(f"invalid registered digest: {key}")
    if (
        state_pool.get("source_run_id")
        == registration.get("excluded_v2_source_run_id")
        or int(state_pool.get("state_count", 0)) != 20
        or int(state_pool.get("decision_turn", 0)) != 4
        or state_pool.get("personality") != "completionist"
        or state_pool.get("source_role") != "post_checkpoint_evaluation_rollout"
    ):
        raise ProbeError("V3 is not a different retained 20-state evaluation pool")
    return registration, v1.sha256_file(path)


def _assert_exact_file(root: Path, relative: str, expected: str) -> None:
    path = root / _validate_repo_relative(relative, label="archive file")
    cursor = path
    while cursor != root:
        if cursor.is_symlink():
            raise ProbeError(f"refusing symlinked historical path: {relative}")
        if root not in cursor.parents:
            raise ProbeError(f"historical path escapes root: {relative}")
        cursor = cursor.parent
    if not path.is_file() or v1.sha256_file(path) != expected:
        raise ProbeError(f"historical archive identity mismatch: {relative}")


def _source_rows(paths: list[Path], historical_root: Path) -> list[dict[str, str]]:
    return [
        {
            "path": path.relative_to(historical_root).as_posix(),
            "sha256": v1.sha256_file(path),
        }
        for path in paths
    ]


def verify_source_archive(registration: dict, historical_root: Path) -> dict:
    """Verify archive identity and eligibility without classifying model outcomes."""
    if historical_root.is_symlink() or not historical_root.is_dir():
        raise ProbeError("historical root must be an existing non-symlink directory")
    state_pool = registration["state_pool"]
    source_glob = _validate_repo_relative(
        state_pool.get("source_glob"), label="source_glob"
    ).as_posix()
    if not source_glob.endswith("/session_*.log"):
        raise ProbeError("source_glob must select only session log files")
    _assert_exact_file(
        historical_root,
        "SHA256SUMS",
        state_pool["archive_sha256sums_sha256"],
    )
    _assert_exact_file(
        historical_root,
        "inventory.json",
        state_pool["archive_inventory_sha256"],
    )
    inventory = _read_json(historical_root / "inventory.json")
    opd_group = inventory.get("groups", {}).get("opd_2b", {})
    if (
        inventory.get("schema_version") != "kaetram-historical-artifact-inventory-v2"
        or opd_group.get("complete") is not True
        or state_pool["source_run_id"] not in opd_group.get("run_ids", [])
    ):
        raise ProbeError("source run is not a complete OPD evaluation bundle")

    run_id = state_pool["source_run_id"]
    identities = state_pool.get("run_identity_files")
    if not isinstance(identities, list) or len(identities) != 6:
        raise ProbeError("V3 registration must bind three run and harness identities")
    expected_identity_paths = {
        f"dataset/raw/agent_{agent_id}/runs/{run_id}/{name}"
        for agent_id in range(3)
        for name in ("run.meta.json", "harness_meta_template.json")
    }
    actual_identity_paths = {
        item.get("path") for item in identities if isinstance(item, dict)
    }
    if actual_identity_paths != expected_identity_paths:
        raise ProbeError("V3 run identity path set is incomplete or duplicated")
    for item in identities:
        if not isinstance(item, dict):
            raise ProbeError("invalid run identity entry")
        _assert_exact_file(historical_root, item.get("path", ""), item.get("sha256", ""))

    for agent_id, personality in enumerate(
        ("grinder", "completionist", "explorer_tinkerer")
    ):
        relative = f"dataset/raw/agent_{agent_id}/runs/{run_id}/run.meta.json"
        meta = _read_json(historical_root / relative)
        if (
            meta.get("run_id") != run_id
            or meta.get("agent_id") != agent_id
            or meta.get("personality") != personality
            or meta.get("harness") != "qwen"
            or meta.get("model") != "2b-opd-r3"
            or meta.get("n_agents") != 3
            or float(meta.get("hours_budget", 0)) != 6.0
        ):
            raise ProbeError(f"source evaluation metadata mismatch: agent_{agent_id}")
        harness = _read_json(
            historical_root
            / f"dataset/raw/agent_{agent_id}/runs/{run_id}/harness_meta_template.json"
        )
        if (
            harness.get("agent_id") != agent_id
            or harness.get("personality") != personality
            or harness.get("harness") != "qwen"
            or harness.get("model") != "2b-opd-r3"
        ):
            raise ProbeError(f"source harness metadata mismatch: agent_{agent_id}")

    logs = sorted(
        historical_root.glob(source_glob),
        key=lambda item: item.relative_to(historical_root).as_posix(),
    )
    if any(path.is_symlink() or not path.is_file() for path in logs):
        raise ProbeError("source_glob resolved a symlink or non-file")
    for path in logs:
        cursor = path.parent
        while cursor != historical_root:
            if cursor.is_symlink():
                raise ProbeError("source_glob traverses a symlinked directory")
            if historical_root not in cursor.parents:
                raise ProbeError("source_glob escaped the historical root")
            cursor = cursor.parent
    if len(logs) != int(state_pool["matched_source_log_count"]):
        raise ProbeError("matched source-log count differs from registration")
    rows = _source_rows(logs, historical_root)
    if _canonical_sha256(rows) != state_pool["matched_source_logs_sha256"]:
        raise ProbeError("matched source-log SHA closure differs from registration")
    metadata_paths = [path.with_suffix(".meta.json") for path in logs]
    if (
        len(metadata_paths) != int(state_pool["matched_source_metadata_count"])
        or any(path.is_symlink() or not path.is_file() for path in metadata_paths)
    ):
        raise ProbeError("matched source-metadata count or file type differs")
    metadata_rows = _source_rows(metadata_paths, historical_root)
    if _canonical_sha256(metadata_rows) != state_pool["matched_source_metadata_sha256"]:
        raise ProbeError("matched source-metadata SHA closure differs from registration")

    eligible = [
        path
        for path in logs
        if (v1.session_meta(path) or {}).get("personality")
        == state_pool["personality"]
    ]
    if len(eligible) != int(state_pool["eligible_source_log_count"]):
        raise ProbeError("eligible source-log count differs from registration")
    eligible_rows = _source_rows(eligible, historical_root)
    if _canonical_sha256(eligible_rows) != state_pool["eligible_source_logs_sha256"]:
        raise ProbeError("eligible source-log SHA closure differs from registration")

    reconstructable = sum(
        v1._render_decision_state(
            path,
            decision_turn=int(state_pool["decision_turn"]),
            max_history_messages=int(state_pool["max_history_messages"]),
        )
        is not None
        for path in eligible
    )
    if reconstructable != int(state_pool["reconstructable_decision_state_count"]):
        raise ProbeError("reconstructable-state count differs from registration")
    if reconstructable < int(state_pool["state_count"]):
        raise ProbeError("fewer than 20 fourth-decision states are reconstructable")
    return {
        "source_run_id": run_id,
        "matched_source_log_count": len(logs),
        "eligible_source_log_count": len(eligible),
        "reconstructable_decision_state_count": reconstructable,
        "matched_source_logs_sha256": _canonical_sha256(rows),
        "matched_source_metadata_sha256": _canonical_sha256(metadata_rows),
        "eligible_source_logs_sha256": _canonical_sha256(eligible_rows),
    }


def materialize_effective_registration(registration: dict) -> dict:
    baseline_contract = registration["frozen_v2_protocol"]
    baseline_path = REPO / _validate_repo_relative(
        baseline_contract["path"], label="frozen V2 registration"
    )
    if (
        not baseline_path.is_file()
        or v1.sha256_file(baseline_path) != baseline_contract["sha256"]
    ):
        raise ProbeError("frozen V2 protocol identity mismatch")
    baseline = _read_json(baseline_path)
    effective = copy.deepcopy(baseline)
    state_pool = registration["state_pool"]
    excluded_path = REPO / _validate_repo_relative(
        state_pool["excluded_design"], label="excluded V2 design"
    )
    if (
        not excluded_path.is_file()
        or v1.sha256_file(excluded_path) != state_pool["excluded_design_sha256"]
    ):
        raise ProbeError("excluded V2 design identity mismatch")
    excluded = _read_json(excluded_path)
    excluded_paths = [state["source_log"] for state in excluded.get("states", [])]
    if len(excluded_paths) != 20 or len(set(excluded_paths)) != 20:
        raise ProbeError("excluded V2 design does not contain 20 unique states")

    effective["study_id"] = registration["study_id"]
    effective["status"] = "registered_before_outcomes"
    effective["purpose"] = registration["purpose"]
    effective["state_pool"] = {
        "source_run_id": state_pool["source_run_id"],
        "source_glob": state_pool["source_glob"],
        "personality": state_pool["personality"],
        "state_count": state_pool["state_count"],
        "decision_turn": state_pool["decision_turn"],
        "max_history_messages": state_pool["max_history_messages"],
        "excluded_design": state_pool["excluded_design"],
        "excluded_design_sha256": state_pool["excluded_design_sha256"],
        "excluded_source_logs": excluded_paths,
        "selection": state_pool["selection"],
        "outcome_independence": state_pool["outcome_independence"],
    }
    effective["claim_boundary"] = registration["claim_boundary"]
    effective["analysis"]["estimand_unit"] = (
        "The registered finite set of 100 state-seed pairs per checkpoint-condition "
        "on the V3 panel. States are retained observations from one historical "
        "evaluation rollout and are not independent population draws."
    )
    effective["analysis"]["directional_replication_criterion"] = (
        "The V2 native-tools main effect is confirmed on the different V3 state "
        "pool only if the registered rate difference is strictly positive at Base, "
        "R2, and R3 on the complete V3 grid. Documentation and interaction contrasts "
        "have no directional criterion."
    )
    effective["analysis"]["reporting"] = (
        "Report exact finite-grid cell rates, paired rate differences, the number "
        "of states with positive, negative, or zero paired differences, the V3 "
        "directional criterion, and registered seed-heterogeneity counts. Do not "
        "report p-values, confidence intervals, binomial bounds, or population claims."
    )
    effective["provenance"]["source_identity"] = (
        "The V3 preparation receipt verifies the registered historical archive, "
        "complete matched and eligible source-log SHA closures, all run identities, "
        "the exact excluded V2 panel, selected source logs, rendered messages, and "
        "the clean pushed registration commit."
    )
    effective["provenance"]["execution_gate"] = (
        "No request may be issued until registration and design are committed and "
        "pushed, and the independent V3 verifier reports execution_ready=true."
    )
    for field in INHERITED_FIELDS:
        if effective.get(field) != baseline.get(field):
            raise ProbeError(f"V3 changed frozen V2 field: {field}")
    if (
        effective.get("study_id") != EXPECTED_STUDY_ID
        or effective.get("state_pool", {}).get("source_run_id")
        != registration["state_pool"]["source_run_id"]
        or effective.get("state_pool", {}).get("source_run_id")
        == registration["excluded_v2_source_run_id"]
        or registration["excluded_v2_source_run_id"]
        in effective.get("state_pool", {}).get("source_glob", "")
    ):
        raise ProbeError("V3 materialization retained a study-dependent V2 field")
    return effective


def require_zero_panel_overlap(selected: set[str], excluded: set[str]) -> None:
    overlap = selected & excluded
    if overlap:
        raise ProbeError(
            "V3 selected state overlaps the frozen V2 panel: "
            + ", ".join(sorted(overlap))
        )


def require_identity_safe_design(design: dict) -> None:
    rendered = json.dumps(design, sort_keys=True, ensure_ascii=False)
    matches = [name for name, pattern in IDENTITY_PATTERNS.items() if pattern.search(rendered)]
    if matches:
        raise ProbeError("V3 design contains identity-bearing content: " + ", ".join(matches))


def _git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def require_clean_pushed_registration(registration_path: Path) -> dict:
    try:
        relative = registration_path.resolve().relative_to(REPO.resolve()).as_posix()
    except ValueError as exc:
        raise ProbeError("registration must be inside the Arena repository") from exc
    if relative != REGISTRATION_PATH.as_posix():
        raise ProbeError("unexpected V3 registration path")
    if _git(["status", "--porcelain"]):
        raise ProbeError("V3 preparation requires a clean checkout")
    head = _git(["rev-parse", "HEAD"])
    tracked = _git(["ls-files", "--error-unmatch", relative])
    if tracked != relative or _git(["show", f"HEAD:{relative}"]) != registration_path.read_text(
        encoding="utf-8"
    ).rstrip("\n"):
        raise ProbeError("V3 registration is not frozen at HEAD")
    try:
        upstream = _git(["rev-parse", "@{upstream}"])
    except subprocess.CalledProcessError as exc:
        raise ProbeError("current branch has no pushed upstream") from exc
    if upstream != head:
        raise ProbeError("V3 registration commit is not pushed")
    return {"source_git_commit": head, "dirty_paths": []}


def prepare(
    registration_path: Path, historical_root: Path, output_dir: Path
) -> dict:
    registration, registration_sha256 = load_registration(registration_path)
    git_identity = require_clean_pushed_registration(registration_path)
    source_audit = verify_source_archive(registration, historical_root)
    effective = materialize_effective_registration(registration)
    effective_bytes = (
        json.dumps(effective, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    effective_sha256 = hashlib.sha256(effective_bytes).hexdigest()
    design = v2._derive_design(
        effective, effective_sha256, historical_root, git_identity
    )
    excluded = set(effective["state_pool"]["excluded_source_logs"])
    selected = {state["source_log"] for state in design["states"]}
    require_zero_panel_overlap(selected, excluded)
    require_identity_safe_design(design)
    if any(registration["state_pool"]["source_run_id"] not in path for path in selected):
        raise ProbeError("V3 selected state escaped the registered source run")
    if output_dir.exists():
        raise ProbeError("refusing to overwrite V3 design directory")
    output_dir.mkdir(parents=True, exist_ok=False)
    effective_path = output_dir / "effective-registration.json"
    effective_path.write_bytes(effective_bytes)
    design_path = output_dir / "design.json"
    v1.write_json(design_path, design, exclusive=True)
    design_receipt = {
        "schema_version": f"{v1.DESIGN_SCHEMA}.receipt",
        "study_id": registration["study_id"],
        "registration_sha256": effective_sha256,
        "design_sha256": v1.sha256_file(design_path),
        "state_count": len(design["states"]),
        "selected_source_tree_sha256": v1._source_tree_sha256(design["states"]),
        **git_identity,
    }
    v1.write_json(output_dir / "design.receipt.json", design_receipt, exclusive=True)
    receipt = {
        "schema_version": PREPARATION_SCHEMA,
        "study_id": registration["study_id"],
        "v3_registration_sha256": registration_sha256,
        "effective_registration_sha256": effective_sha256,
        "design_sha256": design_receipt["design_sha256"],
        "selected_source_tree_sha256": design_receipt[
            "selected_source_tree_sha256"
        ],
        "source_audit": source_audit,
        "v2_overlap_count": 0,
        "outcomes_inspected_for_selection": False,
        "execution_authorized": False,
        "next_gate": (
            "Commit and push this complete design package, then run the independent "
            "V3 verifier with --require-execution-ready before issuing requests."
        ),
        **git_identity,
    }
    v1.write_json(output_dir / "v3-preparation.receipt.json", receipt, exclusive=True)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, default=REPO / REGISTRATION_PATH)
    parser.add_argument("--historical-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    receipt = prepare(args.registration, args.historical_root, args.output_dir)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
