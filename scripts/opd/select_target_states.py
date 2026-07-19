#!/usr/bin/env python3
"""Freeze targeted external-state and matched-control curricula for OPD."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from heldout_guard import HeldOutGuardError, assert_text_not_reserved, load_registration  # noqa: E402


class SelectionError(ValueError):
    pass


REQUIRED_VALIDITY = ("legal_reachable", "internally_consistent", "e2e_seed_verified")
SNAPSHOT_FIELDS = {
    "position", "hit_points", "mana", "inventory", "bank", "equipment",
    "quests", "achievements", "skills", "statistics", "player_info_overrides",
}
REQUIRED_COUNTS = (
    "student_visits", "natural_student_rollouts",
    "teacher_successes", "teacher_trials",
    "student_successes", "student_trials",
    "recoveries", "recovery_trials",
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _rate(numerator: int, denominator: int, label: str) -> float:
    if isinstance(numerator, bool) or not isinstance(numerator, int) or numerator < 0:
        raise SelectionError(f"{label} numerator must be a nonnegative integer")
    if isinstance(denominator, bool) or not isinstance(denominator, int) or denominator < 1:
        raise SelectionError(f"{label} denominator must be a positive integer")
    if numerator > denominator:
        raise SelectionError(f"{label} numerator cannot exceed denominator")
    return numerator / denominator


def load_config(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise SelectionError(f"cannot load config {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise SelectionError("config schema_version must be 1")
    experiment_id = raw.get("experiment_id")
    if not isinstance(experiment_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", experiment_id):
        raise SelectionError("config experiment_id is invalid")
    thresholds = raw.get("thresholds")
    if not isinstance(thresholds, dict):
        raise SelectionError("config thresholds must be an object")
    for key in (
        "max_student_visit_rate", "min_teacher_success_rate",
        "min_teacher_student_success_gap", "min_recovery_rate",
    ):
        value = thresholds.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise SelectionError(f"thresholds.{key} must be between zero and one")
    max_states = raw.get("max_states")
    if isinstance(max_states, bool) or not isinstance(max_states, int) or max_states < 1:
        raise SelectionError("config max_states must be a positive integer")
    seed = raw.get("random_seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise SelectionError("config random_seed must be an integer")
    registration_raw = raw.get("held_out_registration")
    if not isinstance(registration_raw, str) or not registration_raw:
        raise SelectionError("config held_out_registration is required")
    registration = Path(registration_raw)
    if not registration.is_absolute():
        registration = (REPO / registration).resolve()
    try:
        load_registration(registration)
    except HeldOutGuardError as exc:
        raise SelectionError(str(exc)) from exc
    return {**raw, "_registration_path": registration}


def load_candidates(path: Path, *, registration_path: Path) -> list[dict[str, Any]]:
    path = path.resolve()
    candidates = []
    seen_ids: set[str] = set()
    seen_snapshots: set[str] = set()
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        raise SelectionError(f"cannot read candidates {path}: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SelectionError(f"candidate line {line_number} is invalid JSON: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise SelectionError(f"candidate line {line_number} schema_version must be 1")
        state_id = raw.get("state_id")
        if not isinstance(state_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,95}", state_id):
            raise SelectionError(f"candidate line {line_number} has invalid state_id")
        if state_id in seen_ids:
            raise SelectionError(f"duplicate state_id: {state_id}")
        seen_ids.add(state_id)
        snapshot = raw.get("snapshot")
        if not isinstance(snapshot, dict) or not snapshot:
            raise SelectionError(f"candidate {state_id} snapshot must be a nonempty object")
        missing_snapshot_fields = SNAPSHOT_FIELDS - snapshot.keys()
        unsupported_snapshot_fields = snapshot.keys() - SNAPSHOT_FIELDS
        if missing_snapshot_fields or unsupported_snapshot_fields:
            details = []
            if missing_snapshot_fields:
                details.append(f"missing {sorted(missing_snapshot_fields)}")
            if unsupported_snapshot_fields:
                details.append(f"unsupported {sorted(unsupported_snapshot_fields)}")
            raise SelectionError(
                f"candidate {state_id} snapshot must be a complete seed_player record: "
                + "; ".join(details)
            )
        try:
            assert_text_not_reserved(
                json.dumps(raw, sort_keys=True),
                use="training_seed",
                source=f"candidate {state_id}",
                path=registration_path,
            )
        except HeldOutGuardError as exc:
            raise SelectionError(str(exc)) from exc
        snapshot_sha256 = _sha256_bytes(_canonical_bytes(snapshot))
        if snapshot_sha256 in seen_snapshots:
            raise SelectionError(f"duplicate external snapshot under multiple IDs: {state_id}")
        seen_snapshots.add(snapshot_sha256)
        progress_bin = raw.get("progress_bin")
        if not isinstance(progress_bin, str) or not progress_bin.strip():
            raise SelectionError(f"candidate {state_id} requires progress_bin")
        validity = raw.get("validity")
        if not isinstance(validity, dict) or any(validity.get(key) is not True for key in REQUIRED_VALIDITY):
            raise SelectionError(
                f"candidate {state_id} must pass validity checks: {', '.join(REQUIRED_VALIDITY)}"
            )
        validity_evidence = raw.get("validity_evidence")
        if not isinstance(validity_evidence, dict):
            raise SelectionError(f"candidate {state_id} requires validity_evidence")
        for key in REQUIRED_VALIDITY:
            evidence = validity_evidence.get(key)
            if not isinstance(evidence, dict):
                raise SelectionError(f"candidate {state_id} requires validity_evidence.{key}")
            artifact_path = evidence.get("artifact_path")
            artifact_sha256 = evidence.get("artifact_sha256")
            if not isinstance(artifact_path, str) or not artifact_path.strip():
                raise SelectionError(
                    f"candidate {state_id} validity_evidence.{key}.artifact_path is required"
                )
            if not isinstance(artifact_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", artifact_sha256):
                raise SelectionError(
                    f"candidate {state_id} validity_evidence.{key}.artifact_sha256 is invalid"
                )
            evidence_path = Path(artifact_path)
            if not evidence_path.is_absolute():
                evidence_path = (path.parent / evidence_path).resolve()
            try:
                observed_sha256 = _sha256_file(evidence_path)
            except OSError as exc:
                raise SelectionError(
                    f"candidate {state_id} validity evidence is unavailable: {evidence_path}: {exc}"
                ) from exc
            if observed_sha256 != artifact_sha256:
                raise SelectionError(
                    f"candidate {state_id} validity_evidence.{key} digest mismatch"
                )
        counts = raw.get("counts")
        if not isinstance(counts, dict) or any(key not in counts for key in REQUIRED_COUNTS):
            raise SelectionError(f"candidate {state_id} is missing required repeated-trial counts")
        visit_rate = _rate(counts["student_visits"], counts["natural_student_rollouts"], f"{state_id} visitation")
        teacher_rate = _rate(counts["teacher_successes"], counts["teacher_trials"], f"{state_id} teacher success")
        student_rate = _rate(counts["student_successes"], counts["student_trials"], f"{state_id} student success")
        recovery_rate = _rate(counts["recoveries"], counts["recovery_trials"], f"{state_id} recovery")
        for flag in ("task_relevant", "endpoint_already_completed"):
            if not isinstance(raw.get(flag), bool):
                raise SelectionError(f"candidate {state_id} {flag} must be boolean")
        source_kind = raw.get("source_kind")
        if source_kind not in {"direct_snapshot", "teacher_success_prefix", "student_failure", "valid_state_pool"}:
            raise SelectionError(f"candidate {state_id} has unsupported source_kind")
        source_run_ids = raw.get("source_run_ids")
        if not isinstance(source_run_ids, list) or not source_run_ids or not all(
            isinstance(item, str) and item for item in source_run_ids
        ):
            raise SelectionError(f"candidate {state_id} requires source_run_ids")
        candidates.append({
            **raw,
            "snapshot_sha256": snapshot_sha256,
            "derived": {
                "student_visit_rate": visit_rate,
                "teacher_success_rate": teacher_rate,
                "student_success_rate": student_rate,
                "teacher_student_success_gap": teacher_rate - student_rate,
                "recovery_rate": recovery_rate,
            },
        })
    if not candidates:
        raise SelectionError("candidate file is empty")
    return candidates


def _rank_target(candidate: dict[str, Any]) -> tuple[Any, ...]:
    d = candidate["derived"]
    return (
        d["student_visit_rate"],
        -d["teacher_student_success_gap"],
        -d["teacher_success_rate"],
        candidate["state_id"],
    )


def _valid(candidate: dict[str, Any]) -> bool:
    return (
        candidate["task_relevant"]
        and not candidate["endpoint_already_completed"]
        and all(candidate["validity"].get(key) is True for key in REQUIRED_VALIDITY)
    )


def select_arms(candidates: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    thresholds = config["thresholds"]
    n = config["max_states"]
    valid = [candidate for candidate in candidates if _valid(candidate)]
    targeted_pool = [
        candidate for candidate in valid
        if candidate["derived"]["student_visit_rate"] <= thresholds["max_student_visit_rate"]
        and candidate["derived"]["teacher_success_rate"] >= thresholds["min_teacher_success_rate"]
        and candidate["derived"]["teacher_student_success_gap"] >= thresholds["min_teacher_student_success_gap"]
        and candidate["derived"]["recovery_rate"] >= thresholds["min_recovery_rate"]
    ]
    targeted = sorted(targeted_pool, key=_rank_target)[:n]
    if len(targeted) != n:
        raise SelectionError(f"targeted rule selected {len(targeted)} states; config requires {n}")

    visitation = sorted(
        [candidate for candidate in valid if candidate["derived"]["student_visit_rate"] <= thresholds["max_student_visit_rate"]],
        key=lambda candidate: (candidate["derived"]["student_visit_rate"], candidate["state_id"]),
    )[:n]
    teacher_advantage = sorted(
        [
            candidate for candidate in valid
            if candidate["derived"]["teacher_success_rate"] >= thresholds["min_teacher_success_rate"]
            and candidate["derived"]["teacher_student_success_gap"] >= thresholds["min_teacher_student_success_gap"]
        ],
        key=lambda candidate: (-candidate["derived"]["teacher_student_success_gap"], candidate["state_id"]),
    )[:n]
    if len(visitation) != n or len(teacher_advantage) != n:
        raise SelectionError("single-factor ablation pools do not contain max_states candidates")

    rng = random.Random(config["random_seed"])
    target_ids = {candidate["state_id"] for candidate in targeted}
    control_pool = [candidate for candidate in valid if candidate["state_id"] not in target_ids]
    if len(control_pool) < n:
        raise SelectionError("random-valid control pool is smaller than the targeted arm")
    random_valid = rng.sample(sorted(control_pool, key=lambda candidate: candidate["state_id"]), n)

    progress_matched = []
    used: set[str] = set()
    for target in targeted:
        choices = [
            candidate for candidate in control_pool
            if candidate["progress_bin"] == target["progress_bin"]
            and candidate["state_id"] not in used
        ]
        if not choices:
            raise SelectionError(
                f"no unused progress-matched control for target {target['state_id']} "
                f"in bin {target['progress_bin']!r}"
            )
        choice = rng.choice(sorted(choices, key=lambda candidate: candidate["state_id"]))
        used.add(choice["state_id"])
        progress_matched.append(choice)

    return {
        "targeted": targeted,
        "random_valid": random_valid,
        "progress_matched": progress_matched,
        "visitation_only": visitation,
        "teacher_advantage_only": teacher_advantage,
    }


def build_selection(candidate_path: Path, config_path: Path) -> dict[str, Any]:
    config = load_config(config_path.resolve())
    candidates = load_candidates(
        candidate_path.resolve(), registration_path=config["_registration_path"],
    )
    arms = select_arms(candidates, config)
    config_public = {key: value for key, value in config.items() if not key.startswith("_")}
    return {
        "schema_version": "kaetram-target-state-selection-v1",
        "experiment_id": config["experiment_id"],
        "selection_rule": (
            "valid and task-relevant; low natural student visitation; minimum teacher success; "
            "minimum teacher-student conditional success gap; minimum recoverability"
        ),
        "candidate_file": str(candidate_path.resolve()),
        "candidate_file_sha256": _sha256_file(candidate_path.resolve()),
        "config_file": str(config_path.resolve()),
        "config_file_sha256": _sha256_file(config_path.resolve()),
        "config": config_public,
        "candidate_count": len(candidates),
        "arms": arms,
        "warnings": [
            "Selection freezes a training initializer; it is not an outcome.",
            "All headline evaluation must begin from the original unseeded state.",
            "Direct snapshots are distinct from successful-prefix replay only when source_kind and state provenance are preserved.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidates", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.out.exists():
            raise SelectionError(f"refusing to overwrite frozen selection: {args.out}")
        selection = build_selection(args.candidates, args.config)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(selection, indent=2, sort_keys=True) + "\n")
    except SelectionError as exc:
        parser.error(str(exc))
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
