#!/usr/bin/env python3
"""Independently audit an anonymous V3 trigger-incidence public bundle.

The auditor needs only the exported directory.  It does not contact a model,
read the private historical archive, or trust producer-side summary tables.
It verifies the closed file inventory, the two-stage V3 binding, the complete
request grid, all raw run and seed-gate envelopes, extended local-runtime
attestations, and independently recomputes the registered finite-grid result.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any


REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.opd import audit_trigger_incidence_artifact as audit_v1  # noqa: E402
from scripts.opd import audit_trigger_incidence_artifact_v2 as audit_v2  # noqa: E402
from scripts.opd import canonicalize  # noqa: E402
from tool_surface import MODEL_VISIBLE_TOOL_DEFINITIONS  # noqa: E402


AuditError = audit_v2.AuditError
sha256_file = audit_v2.sha256_file
sha256_json = audit_v2.sha256_json
load_object = audit_v2.load_object
_load_jsonl = audit_v2._load_jsonl

PUBLIC_SCHEMA = "kaetram.local-trigger-incidence-public-artifact.v3"
AUDIT_SCHEMA = "kaetram.local-trigger-incidence-v3-public-audit.v1"
V3_REGISTRATION_SCHEMA = "kaetram.local-trigger-incidence-v3-registration.v1"
V3_STUDY_ID = "local-trigger-incidence-seeded-v3"
RUN_SCHEMA = "kaetram.local-trigger-incidence-run.v1"
SEED_GATE_SCHEMA = "kaetram.local-trigger-incidence-seed-gate.v1"
ANALYSIS_SCHEMA = "kaetram.local-trigger-incidence-analysis.v1"
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
SAFE_SNAPSHOT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
IDENTITY_PATTERNS = {
    "absolute macOS user path": re.compile(rb"/Users/", re.IGNORECASE),
    "absolute Linux home path": re.compile(rb"/home/", re.IGNORECASE),
    "author handle": re.compile(rb"(?:barath|patnir)", re.IGNORECASE),
    "deployment hostname": re.compile(rb"modal\.run", re.IGNORECASE),
    "email-like identifier": re.compile(rb"[\w.+-]+@[\w.-]+"),
}
SNAPSHOT_PROJECTION = Path(
    "research/experiments/provenance/public-hf-snapshot-projection.json"
)
RUNTIME_PROJECTION = Path(
    "research/experiments/provenance/local-runtime-projection.json"
)
EXCLUDED_DESIGN = Path(
    "research/artifacts/local-trigger-incidence-v2/design/design.json"
)


def _safe_relative(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise AuditError("artifact path must be a nonempty string")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise AuditError(f"unsafe artifact path: {value!r}")
    if pure.as_posix() != value:
        raise AuditError(f"non-canonical artifact path: {value!r}")
    return Path(*pure.parts)


def _expected_paths(snapshots: list[str]) -> set[str]:
    paths = {
        "registration.json",
        "result-verification.json",
        "design/effective-registration.json",
        "design/frozen-v2-registration.json",
        "design/design.json",
        "design/design.receipt.json",
        "design/v3-preparation.receipt.json",
        "design/expected-request-grid.jsonl",
        EXCLUDED_DESIGN.as_posix(),
        "analysis/analysis-summary.json",
        "analysis/cells.csv",
        "analysis/contrasts.csv",
        "analysis/artifact-index.json",
        SNAPSHOT_PROJECTION.as_posix(),
        RUNTIME_PROJECTION.as_posix(),
    }
    for snapshot in snapshots:
        paths.update(
            f"{container}/{snapshot}/{name}"
            for container, names in (
                (
                    "runs",
                    (
                        "prelaunch.json",
                        "results.jsonl",
                        "postflight.json",
                        "completed.json",
                        "artifact-index.json",
                    ),
                ),
                (
                    "seed-gates",
                    (
                        "preflight.json",
                        "results.jsonl",
                        "postflight.json",
                        "completed.json",
                        "artifact-index.json",
                    ),
                ),
            )
            for name in names
        )
    return paths


def _verify_outer_inventory(root: Path) -> tuple[dict, list[str]]:
    if root.is_symlink() or not root.is_dir():
        raise AuditError("artifact root must be a regular directory")
    index = load_object(root / "artifact-index.json")
    if index.get("schema_version") != PUBLIC_SCHEMA:
        raise AuditError("unexpected V3 public-artifact schema")
    records = index.get("files")
    if not isinstance(records, list) or not records:
        raise AuditError("public artifact has no file inventory")
    seen: list[str] = []
    normalized = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "path",
            "size_bytes",
            "sha256",
        }:
            raise AuditError("invalid public file record")
        relative = _safe_relative(record["path"])
        text = relative.as_posix()
        if text == "artifact-index.json" or text in seen:
            raise AuditError(f"duplicate public file record: {text}")
        path = root / relative
        if (
            path.is_symlink()
            or not path.is_file()
            or isinstance(record.get("size_bytes"), bool)
            or not isinstance(record.get("size_bytes"), int)
            or path.stat().st_size != record["size_bytes"]
            or HEX64.fullmatch(str(record.get("sha256", ""))) is None
            or sha256_file(path) != record["sha256"]
        ):
            raise AuditError(f"public artifact mismatch: {text}")
        seen.append(text)
        normalized.append(record)
    if seen != sorted(seen):
        raise AuditError("public file inventory is not ordered")
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual != {*seen, "artifact-index.json"}:
        raise AuditError("public artifact contains missing or unindexed files")
    if index.get("tree_sha256") != sha256_json(normalized):
        raise AuditError("public artifact tree digest mismatch")
    return index, seen


def _scan_anonymity(root: Path, files: list[str]) -> None:
    for relative in files:
        payload = (root / relative).read_bytes()
        for label, pattern in IDENTITY_PATTERNS.items():
            if pattern.search(payload):
                raise AuditError(f"identity leak ({label}) in {relative}")


def _verify_registration(root: Path) -> tuple[dict, dict]:
    v3 = load_object(root / "registration.json")
    effective = load_object(root / "design/effective-registration.json")
    if (
        v3.get("schema_version") != V3_REGISTRATION_SCHEMA
        or v3.get("study_id") != V3_STUDY_ID
        or v3.get("status") != "registered_execution_prohibited"
        or v3.get("claim_boundary", {}).get("confirmatory") is not False
        or effective.get("study_id") != V3_STUDY_ID
        or effective.get("status") != "registered_before_outcomes"
        or effective.get("claim_boundary") != v3.get("claim_boundary")
    ):
        raise AuditError("V3 registration identity or claim boundary is invalid")
    prohibited = " ".join(v3["claim_boundary"].get("prohibited", [])).lower()
    if not all(term in prohibited for term in ("recovery benefit", "generalization")):
        raise AuditError("V3 non-confirmatory claim boundary is incomplete")
    audit_v2._verify_registration(effective)
    if tuple(effective["snapshots"]) != ("base_2b", "opd_r2_2b", "opd_r3_2b"):
        raise AuditError("V3 checkpoint order is not frozen")
    frozen = v3.get("frozen_v2_protocol")
    if (
        not isinstance(frozen, dict)
        or frozen.get("sha256")
        != sha256_file(root / "design/frozen-v2-registration.json")
    ):
        raise AuditError("frozen V2 registration is not bound")
    baseline = load_object(root / "design/frozen-v2-registration.json")
    for field in frozen.get("inherit_exactly", []):
        if effective.get(field) != baseline.get(field):
            raise AuditError(f"V3 changed inherited V2 field: {field}")
    state_pool = v3["state_pool"]
    excluded = load_object(root / EXCLUDED_DESIGN)
    excluded_paths = [state["source_log"] for state in excluded.get("states", [])]
    expected = copy.deepcopy(baseline)
    expected["study_id"] = V3_STUDY_ID
    expected["status"] = "registered_before_outcomes"
    expected["purpose"] = v3["purpose"]
    expected["state_pool"] = {
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
    expected["claim_boundary"] = v3["claim_boundary"]
    expected["analysis"]["estimand_unit"] = (
        "The registered finite set of 100 state-seed pairs per checkpoint-condition "
        "on the V3 panel. States are retained observations from one historical "
        "evaluation rollout and are not independent population draws."
    )
    expected["analysis"]["directional_replication_criterion"] = (
        "The V2 native-tools main effect is confirmed on the different V3 state "
        "pool only if the registered rate difference is strictly positive at Base, "
        "R2, and R3 on the complete V3 grid. Documentation and interaction contrasts "
        "have no directional criterion."
    )
    expected["analysis"]["reporting"] = (
        "Report exact finite-grid cell rates, paired rate differences, the number "
        "of states with positive, negative, or zero paired differences, the V3 "
        "directional criterion, and registered seed-heterogeneity counts. Do not "
        "report p-values, confidence intervals, binomial bounds, or population claims."
    )
    expected["provenance"]["source_identity"] = (
        "The V3 preparation receipt verifies the registered historical archive, "
        "complete matched and eligible source-log SHA closures, all run identities, "
        "the exact excluded V2 panel, selected source logs, rendered messages, and "
        "the clean pushed registration commit."
    )
    expected["provenance"]["execution_gate"] = (
        "No request may be issued until registration and design are committed and "
        "pushed, and the independent V3 verifier reports execution_ready=true."
    )
    if effective != expected:
        raise AuditError("effective V3 registration is not the frozen transformation")
    return v3, effective


def _validate_messages(messages: Any) -> None:
    if not isinstance(messages, list) or not messages:
        raise AuditError("design state has no messages")
    audit_v2._validate_design_messages(messages)


def _verify_design(root: Path, v3: dict, effective: dict) -> dict:
    design_path = root / "design/design.json"
    design = load_object(design_path)
    receipt = load_object(root / "design/design.receipt.json")
    prep = load_object(root / "design/v3-preparation.receipt.json")
    effective_sha = sha256_file(root / "design/effective-registration.json")
    design_sha = sha256_file(design_path)
    states = design.get("states")
    excluded = load_object(root / EXCLUDED_DESIGN)
    excluded_states = excluded.get("states")
    prep_keys = {
        "schema_version",
        "study_id",
        "v3_registration_sha256",
        "effective_registration_sha256",
        "design_sha256",
        "selected_source_tree_sha256",
        "source_audit",
        "v2_overlap_count",
        "outcomes_inspected_for_selection",
        "execution_authorized",
        "next_gate",
        "source_git_commit",
        "dirty_paths",
    }
    if (
        not isinstance(states, list)
        or len(states) != 20
        or not isinstance(excluded_states, list)
        or len(excluded_states) != 20
        or set(design) != audit_v2.DESIGN_KEYS
        or set(receipt) != audit_v2.DESIGN_RECEIPT_KEYS
        or set(prep) != prep_keys
        or design.get("study_id") != V3_STUDY_ID
        or design.get("registration_sha256") != effective_sha
        or receipt.get("registration_sha256") != effective_sha
        or receipt.get("design_sha256") != design_sha
        or prep.get("v3_registration_sha256")
        != sha256_file(root / "registration.json")
        or prep.get("effective_registration_sha256") != effective_sha
        or prep.get("design_sha256") != design_sha
        or prep.get("execution_authorized") is not False
        or prep.get("outcomes_inspected_for_selection") is not False
        or prep.get("v2_overlap_count") != 0
        or design.get("dirty_paths") != []
        or receipt.get("dirty_paths") != []
        or prep.get("dirty_paths") != []
        or HEX40.fullmatch(str(design.get("source_git_commit", ""))) is None
        or receipt.get("source_git_commit") != design.get("source_git_commit")
        or prep.get("source_git_commit") != design.get("source_git_commit")
        or effective["state_pool"].get("excluded_design")
        != EXCLUDED_DESIGN.as_posix()
        or effective["state_pool"].get("excluded_design_sha256")
        != sha256_file(root / EXCLUDED_DESIGN)
        or design.get("source_log_count")
        != v3["state_pool"].get("matched_source_log_count")
        or design.get("eligible_source_log_count")
        != v3["state_pool"].get("eligible_source_log_count")
        or design.get("selection_stride")
        != max(1, design["eligible_source_log_count"] // 40)
        or design.get("excluded_source_log_count") != 20
        or design.get("excluded_source_logs_sha256")
        != sha256_json(sorted(effective["state_pool"]["excluded_source_logs"]))
    ):
        raise AuditError("V3 design receipts or two-stage freeze are invalid")
    source_audit = prep.get("source_audit")
    if (
        not isinstance(source_audit, dict)
        or source_audit.get("source_run_id") != v3["state_pool"]["source_run_id"]
        or source_audit.get("matched_source_log_count")
        != v3["state_pool"]["matched_source_log_count"]
        or source_audit.get("eligible_source_log_count")
        != v3["state_pool"]["eligible_source_log_count"]
        or source_audit.get("reconstructable_decision_state_count")
        != v3["state_pool"]["reconstructable_decision_state_count"]
        or source_audit.get("matched_source_logs_sha256")
        != v3["state_pool"]["matched_source_logs_sha256"]
        or source_audit.get("matched_source_metadata_sha256")
        != v3["state_pool"]["matched_source_metadata_sha256"]
        or source_audit.get("eligible_source_logs_sha256")
        != v3["state_pool"]["eligible_source_logs_sha256"]
    ):
        raise AuditError("V3 source-audit receipt is invalid")
    selected_paths: set[str] = set()
    selected_messages: set[str] = set()
    for index, state in enumerate(states, start=1):
        source = _safe_relative(state.get("source_log"))
        _validate_messages(state.get("messages"))
        if (
            set(state) != audit_v2.DESIGN_STATE_KEYS
            or
            state.get("state_id") != f"state-{index:02d}"
            or state.get("personality") != "completionist"
            or v3["state_pool"]["source_run_id"] not in source.as_posix()
            or HEX64.fullmatch(str(state.get("source_log_sha256", ""))) is None
            or state.get("messages_sha256") != sha256_json(state["messages"])
        ):
            raise AuditError("V3 design state identity is invalid")
        selected_paths.add(source.as_posix())
        selected_messages.add(state["messages_sha256"])
    excluded_paths = set()
    excluded_messages = set()
    for state in excluded_states:
        source = _safe_relative(state.get("source_log"))
        _validate_messages(state.get("messages"))
        if (
            set(state) != audit_v2.DESIGN_STATE_KEYS
            or HEX64.fullmatch(str(state.get("source_log_sha256", ""))) is None
            or state.get("messages_sha256") != sha256_json(state["messages"])
        ):
            raise AuditError("excluded V2 state identity is invalid")
        excluded_paths.add(source.as_posix())
        excluded_messages.add(state["messages_sha256"])
    if (
        len(selected_paths) != 20
        or len(selected_messages) != 20
        or selected_paths & excluded_paths
        or selected_messages & excluded_messages
    ):
        raise AuditError("V3 panel is duplicated or overlaps the V2 panel")
    selected_records = [
        {
            "state_id": state["state_id"],
            "personality": state["personality"],
            "source_log": state["source_log"],
            "source_log_sha256": state["source_log_sha256"],
            "messages_sha256": state["messages_sha256"],
        }
        for state in states
    ]
    tree = sha256_json(selected_records)
    if (
        receipt.get("selected_source_tree_sha256") != tree
        or prep.get("selected_source_tree_sha256") != tree
    ):
        raise AuditError("V3 selected-state tree digest is invalid")
    return design


def _expected_metadata_grid(registration: dict, design: dict) -> list[dict]:
    rows = []
    conditions = registration["conditions"]
    samples = int(registration["sampling"]["samples_per_state_condition"])
    base_seed = int(registration["sampling"]["base_seed"])
    for snapshot in registration["snapshots"]:
        schedule = 0
        for state_index, state in enumerate(design["states"]):
            for sample_index in range(samples):
                offset = (state_index * samples + sample_index) % len(conditions)
                for condition in conditions[offset:] + conditions[:offset]:
                    rows.append(
                        {
                            "schema_version": RUN_SCHEMA,
                            "snapshot": snapshot,
                            "schedule_index": schedule,
                            "state_id": state["state_id"],
                            "state_index": state_index,
                            "sample_index": sample_index,
                            "seed": base_seed + 100 * state_index + sample_index,
                            "condition_id": condition["condition_id"],
                            "documentation": condition["documentation"],
                            "native_tool_schema": condition["native_tool_schema"],
                        }
                    )
                    schedule += 1
    return rows


def _expected_payload_grid(registration: dict, design: dict) -> list[dict]:
    metadata = _expected_metadata_grid(registration, design)
    by_state = {state["state_id"]: state for state in design["states"]}
    snapshots = registration["snapshots"]
    conditions = {item["condition_id"]: item for item in registration["conditions"]}
    sampling = registration["sampling"]
    tools_sha = sha256_json(MODEL_VISIBLE_TOOL_DEFINITIONS)
    result = []
    for row in metadata:
        condition = conditions[row["condition_id"]]
        messages = copy.deepcopy(by_state[row["state_id"]]["messages"])
        if condition["documentation"] == "canonical_docs":
            for message in messages:
                if message.get("role") == "system":
                    message["content"] = canonicalize.docify_system_prompt(
                        message["content"]
                    )
        payload = {
            "model": snapshots[row["snapshot"]]["api_model"],
            "messages": messages,
            "max_tokens": sampling["max_tokens"],
            "temperature": sampling["temperature"],
            "top_p": sampling["top_p"],
            "top_k": sampling["top_k"],
            "presence_penalty": sampling["presence_penalty"],
            "seed": row["seed"],
        }
        current_tools = None
        if condition["native_tool_schema"] == "present":
            payload["tools"] = MODEL_VISIBLE_TOOL_DEFINITIONS
            current_tools = tools_sha
        result.append(
            {
                "schema_version": "kaetram.local-trigger-incidence-expected-request.v1",
                "snapshot": row["snapshot"],
                "schedule_index": row["schedule_index"],
                "state_id": row["state_id"],
                "state_index": row["state_index"],
                "sample_index": row["sample_index"],
                "condition_id": row["condition_id"],
                "seed": row["seed"],
                "messages_sha256": sha256_json(messages),
                "tools_sha256": current_tools,
                "request_payload_sha256": sha256_json(payload),
            }
        )
    return result


def _verify_projections(root: Path, registration: dict) -> tuple[dict, dict]:
    snapshot = load_object(root / SNAPSHOT_PROJECTION)
    runtime = load_object(root / RUNTIME_PROJECTION)
    unsigned_snapshot = dict(snapshot)
    projection_digest = unsigned_snapshot.pop("projection_sha256", None)
    unsigned_runtime = dict(runtime)
    runtime_digest = unsigned_runtime.pop("projection_sha256", None)
    if (
        snapshot.get("schema_version")
        != "kaetram-hf-snapshot-lock-public-projection-v1"
        or projection_digest != sha256_json(unsigned_snapshot)
        or runtime.get("schema_version") != "kaetram.local-runtime-public-projection.v1"
        or runtime_digest != sha256_json(unsigned_runtime)
        or runtime.get("runtime_environment_receipt_sha256")
        != sha256_json(runtime.get("runtime_environment_receipt"))
        or runtime.get("render_contract_sha256")
        != sha256_json(runtime.get("render_contract"))
        or runtime.get("sampling_contract_sha256")
        != sha256_json(runtime.get("render_contract", {}).get("seeded_sampling"))
        or runtime.get("render_contract_sha256")
        != registration["endpoint_contract"]["render_contract_sha256"]
        or runtime.get("sampling_contract_sha256")
        != registration["endpoint_contract"]["sampling_contract_sha256"]
    ):
        raise AuditError("snapshot or runtime projection is invalid")
    if set(snapshot.get("checkpoints", {})) != set(registration["snapshots"]):
        raise AuditError("snapshot projection checkpoint membership is invalid")
    return snapshot, runtime


def _verify_health_extended(
    health: Any,
    registration: dict,
    snapshot_name: str,
    snapshot_projection: dict,
    runtime_projection: dict,
) -> None:
    audit_v2._verify_health(health, registration, snapshot_name)
    attestation = health["attestation"]
    checkpoint = snapshot_projection["checkpoints"][snapshot_name]
    render = runtime_projection["render_contract"]
    expected_deployment = (
        f"local-mlx-lm-{render['engine_version']}-{snapshot_name}-"
        f"{checkpoint['revision'][:12]}-"
        f"{runtime_projection['render_contract_sha256'][:12]}"
    )
    if (
        attestation.get("snapshot_tree_sha256")
        != checkpoint.get("snapshot_tree_sha256")
        or attestation.get("snapshot_lock_sha256")
        != snapshot_projection.get("source_lock_sha256")
        or attestation.get("tokenizer_source_revision")
        != snapshot_projection.get("tokenizer_source_revision")
        or attestation.get("runtime_environment_receipt_sha256")
        != runtime_projection.get("runtime_environment_receipt_sha256")
        or attestation.get("render_contract_sha256")
        != runtime_projection.get("render_contract_sha256")
        or attestation.get("sampling_contract_sha256")
        != runtime_projection.get("sampling_contract_sha256")
        or attestation.get("deployment_id") != expected_deployment
    ):
        raise AuditError(f"{snapshot_name}: extended endpoint identity is invalid")


def _binding_from(value: Any) -> dict:
    expected_keys = {
        "schema_version",
        "study_id",
        "v3_registration_sha256",
        "effective_registration_sha256",
        "design_sha256",
        "design_source_git_commit",
        "execution_commit",
        "execution_verification_sha256",
        "execution_verifier_sha256",
        "expected_request_count",
        "expected_request_grid_sha256",
        "claim_boundary_sha256",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected_keys
        or value.get("schema_version")
        != "kaetram.local-trigger-incidence-v3-runtime-binding.v1"
        or value.get("study_id") != V3_STUDY_ID
        or HEX40.fullmatch(str(value.get("design_source_git_commit", ""))) is None
        or HEX40.fullmatch(str(value.get("execution_commit", ""))) is None
        or any(
            HEX64.fullmatch(str(value.get(key, ""))) is None
            for key in expected_keys
            if key.endswith("sha256")
        )
    ):
        raise AuditError("V3 runtime binding is invalid")
    return value


def _verify_binding(
    binding: dict,
    root: Path,
    registration: dict,
    design: dict,
) -> None:
    grid = _expected_metadata_grid(registration, design)
    if (
        binding["v3_registration_sha256"] != sha256_file(root / "registration.json")
        or binding["effective_registration_sha256"]
        != sha256_file(root / "design/effective-registration.json")
        or binding["design_sha256"] != sha256_file(root / "design/design.json")
        or binding["design_source_git_commit"] != design["source_git_commit"]
        or binding["expected_request_count"] != len(grid)
        or binding["expected_request_grid_sha256"] != sha256_json(grid)
        or binding["claim_boundary_sha256"]
        != sha256_json(registration["claim_boundary"])
    ):
        raise AuditError("V3 runtime binding differs from embedded frozen inputs")


def _verify_run(
    root: Path,
    registration: dict,
    design: dict,
    snapshot: str,
    binding: dict,
    snapshot_projection: dict,
    runtime_projection: dict,
) -> tuple[dict, list[dict]]:
    audit_v2._verify_internal_index(
        root, ("prelaunch.json", "results.jsonl", "postflight.json", "completed.json")
    )
    pre = load_object(root / "prelaunch.json")
    post = load_object(root / "postflight.json")
    completed = load_object(root / "completed.json")
    rows = _load_jsonl(root / "results.jsonl")
    if (
        pre.get("snapshot") != snapshot
        or pre.get("study_id") != V3_STUDY_ID
        or pre.get("registration_sha256")
        != sha256_file(root.parents[1] / "design/effective-registration.json")
        or pre.get("design_sha256")
        != sha256_file(root.parents[1] / "design/design.json")
        or pre.get("sampling") != registration["sampling"]
        or pre.get("source_git_commit") != binding["execution_commit"]
        or pre.get("dirty_paths") != []
        or _binding_from(pre.get("v3_runtime_binding")) != binding
        or post.get("snapshot") != snapshot
        or post.get("endpoint_identity_stable") is not True
        or post.get("endpoint_health") != pre.get("endpoint_health")
        or post.get("error") is not None
    ):
        raise AuditError(f"{snapshot}: V3 run envelope is invalid")
    _verify_health_extended(
        pre["endpoint_health"],
        registration,
        snapshot,
        snapshot_projection,
        runtime_projection,
    )
    expected_rows = [
        row for row in _expected_metadata_grid(registration, design)
        if row["snapshot"] == snapshot
    ]
    if len(rows) != len(expected_rows):
        raise AuditError(f"{snapshot}: V3 run grid is incomplete")
    observed = {}
    for row, expected in zip(rows, expected_rows, strict=True):
        if any(row.get(key) != value for key, value in expected.items()):
            raise AuditError(f"{snapshot}: V3 row schedule is invalid")
        key = (snapshot, row["condition_id"], row["state_id"], row["sample_index"])
        if key in observed:
            raise AuditError(f"{snapshot}: duplicate V3 row")
        if row.get("status") == "ok":
            message = row.get("response_message")
            expected_outcome = audit_v1.classify_message(message)
            if any(
                row.get(field) != value
                for field, value in expected_outcome.items()
            ):
                raise AuditError(f"{snapshot}: V3 raw outcome does not reclassify")
            audit_v2._validate_response_message(message)
        elif row.get("status") == "failed":
            if audit_v1.OUTCOME_FIELDS.intersection(row):
                raise AuditError(f"{snapshot}: failed row contains outcomes")
        else:
            raise AuditError(f"{snapshot}: unknown V3 row status")
        if (
            isinstance(row.get("latency_seconds"), bool)
            or not isinstance(row.get("latency_seconds"), (int, float))
            or not math.isfinite(row["latency_seconds"])
            or row["latency_seconds"] < 0
            or not isinstance(row.get("attempt_errors"), list)
        ):
            raise AuditError(f"{snapshot}: invalid V3 request metadata")
        observed[key] = row
    try:
        audit_v2._verify_detailed_outcomes(observed)
    except audit_v2.AuditError as exc:
        raise AuditError(str(exc)) from exc
    expected_completed = audit_v2._expected_completed(registration, snapshot, rows)
    if completed != expected_completed:
        raise AuditError(f"{snapshot}: V3 completed totals are invalid")
    index = load_object(root / "artifact-index.json")
    return {
        "artifact_index_sha256": sha256_file(root / "artifact-index.json"),
        "tree_sha256": index["tree_sha256"],
        "health": pre["endpoint_health"],
        "seed_gate_artifact_index_sha256": pre["seed_gate_artifact_index_sha256"],
        "seed_gate_tree_sha256": pre["seed_gate_tree_sha256"],
    }, rows


def _verify_gate(
    root: Path,
    registration: dict,
    snapshot: str,
    binding: dict,
    expected_health: dict,
) -> dict:
    # V2's gate verifier is semantically independent; use a temporary logical
    # registration hash check here to account for V3's extra binding field.
    audit_v2._verify_internal_index(
        root, ("preflight.json", "results.jsonl", "postflight.json", "completed.json")
    )
    pre = load_object(root / "preflight.json")
    post = load_object(root / "postflight.json")
    completed = load_object(root / "completed.json")
    rows = _load_jsonl(root / "results.jsonl")
    gate = registration["seed_gate"]
    expected_requests = [
        (f"seed-{index}", int(gate["base_seed"]) + index)
        for index in range(int(gate["distinct_seed_count"]))
    ]
    expected_requests.append(
        (
            f"repeat-{gate['repeat_seed_index']}",
            int(gate["base_seed"]) + int(gate["repeat_seed_index"]),
        )
    )
    hashes = []
    if len(rows) != len(expected_requests):
        raise AuditError(f"{snapshot}: seed gate row count is invalid")
    for row, (request_id, seed) in zip(rows, expected_requests, strict=True):
        digest = audit_v2._semantic_response_sha256(row.get("response_message"))
        if (
            row.get("request_id") != request_id
            or row.get("seed") != seed
            or row.get("status") != "ok"
            or row.get("semantic_response_sha256") != digest
        ):
            raise AuditError(f"{snapshot}: seed gate row is invalid")
        hashes.append(digest)
    distinct = int(gate["distinct_seed_count"])
    repeat = int(gate["repeat_seed_index"])
    unique = len(set(hashes[:distinct]))
    reproducible = hashes[repeat] == hashes[-1]
    expected_completed = {
        "schema_version": f"{SEED_GATE_SCHEMA}.completed",
        "study_id": V3_STUDY_ID,
        "snapshot": snapshot,
        "scheduled_requests": len(rows),
        "successful_requests": len(rows),
        "unique_semantic_responses": unique,
        "minimum_unique_semantic_responses": int(
            gate["minimum_unique_semantic_responses"]
        ),
        "repeated_seed_reproducible": reproducible,
        "endpoint_identity_stable": True,
        "passed": reproducible
        and unique >= int(gate["minimum_unique_semantic_responses"]),
    }
    if (
        pre.get("snapshot") != snapshot
        or pre.get("registration_sha256") != binding["effective_registration_sha256"]
        or pre.get("endpoint_health") != expected_health
        or pre.get("seed_gate") != gate
        or pre.get("source_git_commit") != binding["execution_commit"]
        or pre.get("dirty_paths") != []
        or _binding_from(pre.get("v3_runtime_binding")) != binding
        or post.get("endpoint_health") != expected_health
        or post.get("endpoint_identity_stable") is not True
        or post.get("error") is not None
        or completed != expected_completed
        or completed["passed"] is not True
    ):
        raise AuditError(f"{snapshot}: V3 seed gate does not verify")
    index = load_object(root / "artifact-index.json")
    return {
        "artifact_index_sha256": sha256_file(root / "artifact-index.json"),
        "tree_sha256": index["tree_sha256"],
    }


def _verify_analysis(
    root: Path,
    registration: dict,
    design: dict,
    binding: dict,
    rows: dict[tuple, dict],
    run_ids: dict[str, dict],
) -> dict:
    audit_v2._verify_internal_index(
        root, ("analysis-summary.json", "cells.csv", "contrasts.csv")
    )
    analysis = load_object(root / "analysis-summary.json")
    recomputed = audit_v1.recompute_summary(registration, design, rows)
    complete = recomputed["analysis_status"] == "complete"
    heterogeneity = (
        audit_v2._recompute_seed_heterogeneity(registration, rows)
        if complete
        else {
            "status": "not_evaluated_incomplete_grid",
            "state_condition_groups": 0,
            "groups_with_multiple_semantic_responses": 0,
            "groups_with_primary_outcome_heterogeneity": 0,
            "minimum_unique_semantic_responses_per_group": None,
            "maximum_unique_semantic_responses_per_group": None,
        }
    )
    effects = {
        item["snapshot"]: item["effect_rate_difference"]
        for item in recomputed["registered_contrasts"]
        if item["contrast"] == "native_tools_main"
    }
    directional = {
        "criterion": registration["analysis"]["directional_replication_criterion"],
        "status": "evaluated" if complete else "not_evaluated_incomplete_grid",
        "native_tools_effects": effects,
        "passed": (
            all(effects.get(snapshot, 0) > 0 for snapshot in registration["snapshots"])
            if complete else None
        ),
    }
    expected_inputs = sorted(
        (
            {
                "snapshot": snapshot,
                "artifact_index_sha256": identity["artifact_index_sha256"],
                "tree_sha256": identity["tree_sha256"],
            }
            for snapshot, identity in run_ids.items()
        ),
        key=lambda item: item["snapshot"],
    )
    provenance = analysis.get("analysis_code_provenance")
    if (
        analysis.get("schema_version") != ANALYSIS_SCHEMA
        or analysis.get("study_id") != V3_STUDY_ID
        or analysis.get("registration_sha256")
        != binding["effective_registration_sha256"]
        or analysis.get("design_sha256") != binding["design_sha256"]
        or analysis.get("claim_boundary") != registration["claim_boundary"]
        or analysis.get("input_runs") != expected_inputs
        or _binding_from(analysis.get("v3_runtime_binding")) != binding
        or not isinstance(provenance, dict)
        or provenance.get("source_git_commit") != binding["execution_commit"]
        or provenance.get("dirty_paths") != []
    ):
        raise AuditError("V3 analysis identity or provenance is invalid")
    for key, value in recomputed.items():
        if analysis.get(key) != value:
            raise AuditError(f"V3 independent analysis mismatch: {key}")
    if analysis.get("registered_seed_heterogeneity") != heterogeneity:
        raise AuditError("V3 seed heterogeneity does not recompute")
    if analysis.get("directional_replication") != directional:
        raise AuditError("V3 directional criterion does not recompute")
    if (root / "cells.csv").read_bytes() != audit_v2._csv_bytes(recomputed["cells"]):
        raise AuditError("V3 cells CSV does not recompute")
    if (root / "contrasts.csv").read_bytes() != audit_v2._csv_bytes(
        recomputed["registered_contrasts"]
    ):
        raise AuditError("V3 contrasts CSV does not recompute")
    return analysis


def audit_artifact(root: Path) -> dict:
    outer, files = _verify_outer_inventory(root)
    _scan_anonymity(root, files)
    v3, registration = _verify_registration(root)
    snapshots = list(registration["snapshots"])
    if set(files) != _expected_paths(snapshots):
        raise AuditError("V3 public artifact path membership is not canonical")
    design = _verify_design(root, v3, registration)
    payload_grid = _load_jsonl(root / "design/expected-request-grid.jsonl")
    if payload_grid != _expected_payload_grid(registration, design):
        raise AuditError("V3 expected request payload grid is invalid")
    snapshot_projection, runtime_projection = _verify_projections(root, registration)

    bindings = []
    for snapshot in snapshots:
        bindings.append(
            _binding_from(
                load_object(root / "runs" / snapshot / "prelaunch.json").get(
                    "v3_runtime_binding"
                )
            )
        )
        bindings.append(
            _binding_from(
                load_object(root / "seed-gates" / snapshot / "preflight.json").get(
                    "v3_runtime_binding"
                )
            )
        )
    bindings.append(
        _binding_from(
            load_object(root / "analysis/analysis-summary.json").get(
                "v3_runtime_binding"
            )
        )
    )
    binding = bindings[0]
    if any(item != binding for item in bindings[1:]):
        raise AuditError("V3 runtime bindings disagree across artifacts")
    _verify_binding(binding, root, registration, design)

    all_rows: dict[tuple, dict] = {}
    run_ids = {}
    gate_ids = {}
    for snapshot in snapshots:
        run_id, rows = _verify_run(
            root / "runs" / snapshot,
            registration,
            design,
            snapshot,
            binding,
            snapshot_projection,
            runtime_projection,
        )
        gate_id = _verify_gate(
            root / "seed-gates" / snapshot,
            registration,
            snapshot,
            binding,
            run_id["health"],
        )
        if (
            run_id["seed_gate_artifact_index_sha256"]
            != gate_id["artifact_index_sha256"]
            or run_id["seed_gate_tree_sha256"] != gate_id["tree_sha256"]
        ):
            raise AuditError(f"{snapshot}: run is not bound to its seed gate")
        run_ids[snapshot] = run_id
        gate_ids[snapshot] = gate_id
        for row in rows:
            key = (snapshot, row["condition_id"], row["state_id"], row["sample_index"])
            if key in all_rows:
                raise AuditError("duplicate V3 row across checkpoints")
            all_rows[key] = row
    analysis = _verify_analysis(
        root / "analysis", registration, design, binding, all_rows, run_ids
    )

    receipt = load_object(root / "result-verification.json")
    if (
        receipt.get("schema_version")
        != "kaetram.local-trigger-incidence-v3-result-verification.v1"
        or receipt.get("study_id") != V3_STUDY_ID
        or receipt.get("execution_commit") != binding["execution_commit"]
        or receipt.get("design_sha256") != binding["design_sha256"]
        or receipt.get("expected_request_grid_sha256")
        != binding["expected_request_grid_sha256"]
        or receipt.get("analysis_artifact_index_sha256")
        != sha256_file(root / "analysis/artifact-index.json")
        or receipt.get("run_artifact_indexes")
        != {
            snapshot: run_ids[snapshot]["artifact_index_sha256"]
            for snapshot in sorted(snapshots)
        }
        or receipt.get("seed_gate_artifact_indexes")
        != {
            snapshot: gate_ids[snapshot]["artifact_index_sha256"]
            for snapshot in sorted(snapshots)
        }
        or receipt.get("independent_recomputation") is not True
    ):
        raise AuditError("V3 result-verification receipt is invalid")

    if (
        outer.get("study_id") != V3_STUDY_ID
        or outer.get("execution_source_git_commit") != binding["execution_commit"]
        or outer.get("design_source_git_commit") != binding["design_source_git_commit"]
        or outer.get("registration_sha256") != sha256_file(root / "registration.json")
        or outer.get("effective_registration_sha256")
        != sha256_file(root / "design/effective-registration.json")
        or outer.get("design_sha256") != sha256_file(root / "design/design.json")
    ):
        raise AuditError("V3 outer manifest identity is invalid")
    return {
        "schema_version": AUDIT_SCHEMA,
        "study_id": V3_STUDY_ID,
        "artifact_tree_sha256": outer["tree_sha256"],
        "execution_commit": binding["execution_commit"],
        "scheduled_requests": analysis["scheduled_requests"],
        "successful_requests": analysis["successful_requests"],
        "failed_requests": analysis["failed_requests"],
        "directional_replication": analysis["directional_replication"],
        "independent_recomputation": True,
        "anonymous": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    print(json.dumps(audit_artifact(args.artifact), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
