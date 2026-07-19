from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.opd.seed_selected_states import (
    SeedPlanError,
    build_seed_plan,
    execute_seed_plan,
)
from scripts.opd.select_target_states import SelectionError, build_selection


REPO = Path(__file__).resolve().parents[2]


def _config(tmp_path: Path, max_states: int = 2) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "experiment_id": "target-test-v1",
        "held_out_registration": str(REPO / "research" / "experiments" / "heldout-quest.json"),
        "random_seed": 7,
        "max_states": max_states,
        "thresholds": {
            "max_student_visit_rate": 0.05,
            "min_teacher_success_rate": 0.6,
            "min_teacher_student_success_gap": 0.3,
            "min_recovery_rate": 0.8,
        },
    }))
    return path


def _candidate(
    state_id: str, *, progress_bin: str, position: int,
    student_visits: int = 1, teacher_successes: int = 8,
    student_successes: int = 2, recoveries: int = 9,
) -> dict:
    return {
        "schema_version": 1,
        "state_id": state_id,
        "snapshot": {
            "position": [position, 20],
            "hit_points": 100,
            "mana": 20,
            "inventory": [],
            "bank": [],
            "equipment": [],
            "quests": [{"key": "foresting", "stage": 1}],
            "achievements": [],
            "skills": [],
            "statistics": {},
            "player_info_overrides": {},
        },
        "progress_bin": progress_bin,
        "source_kind": "direct_snapshot",
        "source_run_ids": ["run_source"],
        "validity": {
            "legal_reachable": True,
            "internally_consistent": True,
            "e2e_seed_verified": True,
        },
        "validity_evidence": {
            key: {
                "artifact_path": f"artifacts/validity/{state_id}-{key}.json",
                "artifact_sha256": "pending",
            }
            for key in ("legal_reachable", "internally_consistent", "e2e_seed_verified")
        },
        "counts": {
            "student_visits": student_visits,
            "natural_student_rollouts": 100,
            "teacher_successes": teacher_successes,
            "teacher_trials": 10,
            "student_successes": student_successes,
            "student_trials": 10,
            "recoveries": recoveries,
            "recovery_trials": 10,
        },
        "task_relevant": True,
        "endpoint_already_completed": False,
    }


def _candidates(tmp_path: Path, rows: list[dict]) -> Path:
    for row in rows:
        for key, evidence in row["validity_evidence"].items():
            if evidence["artifact_sha256"] != "pending":
                continue
            evidence_path = tmp_path / evidence["artifact_path"]
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({"state_id": row["state_id"], "check": key}).encode()
            evidence_path.write_bytes(payload)
            evidence["artifact_sha256"] = hashlib.sha256(payload).hexdigest()
    path = tmp_path / "candidates.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def _pool() -> list[dict]:
    return [
        _candidate("target_a", progress_bin="p1", position=1),
        _candidate("target_b", progress_bin="p2", position=2),
        _candidate("control_a1", progress_bin="p1", position=3, student_visits=20, teacher_successes=4),
        _candidate("control_a2", progress_bin="p1", position=4, student_visits=30, teacher_successes=3),
        _candidate("control_b1", progress_bin="p2", position=5, student_visits=20, teacher_successes=4),
        _candidate("control_b2", progress_bin="p2", position=6, student_visits=30, teacher_successes=3),
    ]


def test_selection_freezes_targeted_and_matched_controls_deterministically(tmp_path: Path) -> None:
    candidates = _candidates(tmp_path, _pool())
    config = _config(tmp_path)
    first = build_selection(candidates, config)
    second = build_selection(candidates, config)
    assert first == second
    assert [row["state_id"] for row in first["arms"]["targeted"]] == ["target_a", "target_b"]
    assert len(first["arms"]["progress_matched"]) == 2
    assert {
        row["progress_bin"] for row in first["arms"]["progress_matched"]
    } == {"p1", "p2"}
    assert all(row["snapshot_sha256"] for rows in first["arms"].values() for row in rows)


def test_duplicate_snapshot_and_heldout_leak_fail_closed(tmp_path: Path) -> None:
    duplicate = _pool()
    duplicate[1]["snapshot"] = duplicate[0]["snapshot"]
    with pytest.raises(SelectionError, match="duplicate external snapshot"):
        build_selection(_candidates(tmp_path, duplicate), _config(tmp_path))

    leaking = _pool()
    leaking[0]["snapshot"]["quests"] = [{"key": "desertquest", "stage": 1}]
    with pytest.raises(SelectionError, match="held-out quest leakage"):
        build_selection(_candidates(tmp_path, leaking), _config(tmp_path))

    provenance_leak = _pool()
    provenance_leak[0]["source_run_ids"] = ["desertquest-candidate-discovery"]
    with pytest.raises(SelectionError, match="held-out quest leakage"):
        build_selection(_candidates(tmp_path, provenance_leak), _config(tmp_path))


def test_missing_progress_control_is_a_hard_error(tmp_path: Path) -> None:
    rows = _pool()
    rows = [row for row in rows if row["progress_bin"] != "p2" or row["state_id"] == "target_b"]
    with pytest.raises(SelectionError, match="progress-matched control"):
        build_selection(_candidates(tmp_path, rows), _config(tmp_path))


def test_incomplete_snapshot_and_unverifiable_validity_fail_closed(tmp_path: Path) -> None:
    incomplete = _pool()
    del incomplete[0]["snapshot"]["inventory"]
    with pytest.raises(SelectionError, match="complete seed_player record"):
        build_selection(_candidates(tmp_path, incomplete), _config(tmp_path))

    contradictory = _pool()
    contradictory[0]["snapshot"]["player_info_overrides"] = {"x": 999}
    with pytest.raises(SelectionError, match="cannot replace authoritative fields"):
        build_selection(_candidates(tmp_path, contradictory), _config(tmp_path))

    malformed = _pool()
    malformed[0]["snapshot"]["skills"] = ["foraging"]
    with pytest.raises(SelectionError, match="skills must be a list of objects"):
        build_selection(_candidates(tmp_path, malformed), _config(tmp_path))

    unverifiable = _pool()
    unverifiable[0]["validity_evidence"]["legal_reachable"]["artifact_sha256"] = "claimed"
    with pytest.raises(SelectionError, match="artifact_sha256 is invalid"):
        build_selection(_candidates(tmp_path, unverifiable), _config(tmp_path))

    mismatched = _pool()
    mismatched[0]["validity_evidence"]["legal_reachable"]["artifact_sha256"] = "a" * 64
    with pytest.raises(SelectionError, match="digest mismatch"):
        build_selection(_candidates(tmp_path, mismatched), _config(tmp_path))


def test_seed_plan_requires_three_frozen_states_and_preserves_hashes(tmp_path: Path) -> None:
    selection = build_selection(_candidates(tmp_path, _pool()), _config(tmp_path, max_states=2))
    with pytest.raises(SeedPlanError, match="three are required"):
        build_seed_plan(selection, arm="targeted", batch=0)

    larger = _pool() + [
        _candidate("target_c", progress_bin="p3", position=7),
        _candidate("control_c", progress_bin="p3", position=8, student_visits=20, teacher_successes=4),
    ]
    selection = build_selection(_candidates(tmp_path, larger), _config(tmp_path, max_states=3))
    plan = build_seed_plan(selection, arm="targeted", batch=0)
    assert len(plan["assignments"]) == 3
    calls = []
    cleanup_calls = []
    execute_seed_plan(
        plan,
        lambda username, **snapshot: calls.append((username, snapshot)),
        cleanup_calls.append,
    )
    assert cleanup_calls == ["qwengrinder", "qwencompletionist", "qwenexplorer"]
    assert [call[0] for call in calls] == ["qwengrinder", "qwencompletionist", "qwenexplorer"]


def test_seed_plan_detects_snapshot_tampering(tmp_path: Path) -> None:
    rows = _pool() + [
        _candidate("target_c", progress_bin="p3", position=7),
        _candidate("control_c", progress_bin="p3", position=8, student_visits=20, teacher_successes=4),
    ]
    selection = build_selection(_candidates(tmp_path, rows), _config(tmp_path, max_states=3))
    selection["arms"]["targeted"][0]["snapshot"]["hit_points"] = 1
    with pytest.raises(SeedPlanError, match="hash mismatch"):
        build_seed_plan(selection, arm="targeted", batch=0)
