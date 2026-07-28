from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.opd import trigger_incidence_probe_v3 as v3


ROOT = Path(__file__).resolve().parents[2]
REGISTRATION = ROOT / "research/experiments/local-trigger-incidence-v3.json"
DESIGN_DIR = ROOT / v3.DESIGN_DIR


def _effective() -> dict:
    conditions = []
    for index, (documentation, tools) in enumerate(
        (
            ("python_docs", "absent"),
            ("python_docs", "present"),
            ("canonical_docs", "absent"),
            ("canonical_docs", "present"),
        )
    ):
        conditions.append(
            {
                "condition_id": f"condition-{index}",
                "documentation": documentation,
                "native_tool_schema": tools,
            }
        )
    return {
        "study_id": "local-trigger-incidence-seeded-v3",
        "snapshots": {"base_2b": {}, "opd_r2_2b": {}, "opd_r3_2b": {}},
        "conditions": conditions,
        "sampling": {"samples_per_state_condition": 5, "base_seed": 9000},
        "claim_boundary": {"confirmatory": False, "prohibited": ["superiority"]},
    }


def _design(source_commit: str = "a" * 40) -> dict:
    return {
        "source_git_commit": source_commit,
        "states": [{"state_id": f"state-{index + 1:02d}"} for index in range(20)],
    }


def _binding() -> dict:
    effective = _effective()
    design = _design()
    grid = v3._expected_request_grid(effective, design)
    return {
        "schema_version": v3.RUNTIME_BINDING_SCHEMA,
        "study_id": effective["study_id"],
        "v3_registration_sha256": "1" * 64,
        "effective_registration_sha256": "2" * 64,
        "design_sha256": "3" * 64,
        "design_source_git_commit": "a" * 40,
        "execution_commit": "b" * 40,
        "expected_request_count": 1200,
        "expected_request_grid_sha256": v3.v1.sha256_json(grid),
        "claim_boundary_sha256": v3.v1.sha256_json(effective["claim_boundary"]),
        "effective_registration_path": DESIGN_DIR / "effective-registration.json",
        "design_path": DESIGN_DIR / "design.json",
        "effective_registration": effective,
        "design": design,
    }


def test_expected_grid_is_exact_frozen_v2_schedule() -> None:
    rows = v3._validate_grid(_effective(), _design())
    assert len(rows) == 1200
    assert len([row for row in rows if row["snapshot"] == "base_2b"]) == 400
    assert rows[0] == {
        "schema_version": v3.v1.RUN_SCHEMA,
        "snapshot": "base_2b",
        "schedule_index": 0,
        "state_id": "state-01",
        "state_index": 0,
        "sample_index": 0,
        "seed": 9000,
        "condition_id": "condition-0",
        "documentation": "python_docs",
        "native_tool_schema": "absent",
    }
    assert rows[4]["condition_id"] == "condition-1"
    assert rows[4]["sample_index"] == 1
    assert rows[4]["schedule_index"] == 4
    assert rows[-1]["snapshot"] == "opd_r3_2b"
    assert rows[-1]["state_id"] == "state-20"
    assert rows[-1]["seed"] == 10904


def test_real_materialized_registration_uses_exact_checkpoint_keys() -> None:
    registration, _ = v3.prepare.load_registration(REGISTRATION)
    effective = v3.prepare.materialize_effective_registration(registration)
    assert tuple(effective["snapshots"]) == (
        "base_2b",
        "opd_r2_2b",
        "opd_r3_2b",
    )
    rows = v3._validate_grid(effective, _design())
    assert {row["snapshot"] for row in rows} == set(effective["snapshots"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("snapshots", {"base_2b": {}, "opd_r2_2b": {}}),
        ("conditions", _effective()["conditions"][:3]),
        ("sampling", {"samples_per_state_condition": 4, "base_seed": 9000}),
    ],
)
def test_grid_shape_drift_fails_closed(field: str, value: object) -> None:
    registration = _effective()
    registration[field] = value
    with pytest.raises(v3.ProbeError, match="frozen 3x20x5x4"):
        v3._validate_grid(registration, _design())


def test_duplicate_grid_keys_fail_closed() -> None:
    design = _design()
    design["states"][1]["state_id"] = "state-01"
    with pytest.raises(v3.ProbeError, match="incomplete or duplicated"):
        v3._validate_grid(_effective(), design)


def test_public_binding_excludes_runtime_objects_and_paths() -> None:
    public = v3._public_binding(_binding())
    assert public["execution_commit"] == "b" * 40
    assert "effective_registration" not in public
    assert "design" not in public
    assert "design_path" not in public


def test_runtime_preflight_accepts_later_execution_commit() -> None:
    binding = _binding()
    preflight = {
        "source_git_commit": binding["execution_commit"],
        "dirty_paths": [],
        "registration_sha256": binding["effective_registration_sha256"],
        "v3_runtime_binding": v3._public_binding(binding),
    }
    v3._require_runtime_preflight(preflight, binding)
    preflight["source_git_commit"] = binding["design_source_git_commit"]
    with pytest.raises(v3.ProbeError, match="execution-ready"):
        v3._require_runtime_preflight(preflight, binding)


def test_seed_gate_must_bind_execution_not_preparation_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    binding = _binding()
    preflight = {
        "source_git_commit": binding["execution_commit"],
        "dirty_paths": [],
        "registration_sha256": binding["effective_registration_sha256"],
        "v3_runtime_binding": v3._public_binding(binding),
    }
    (tmp_path / "preflight.json").write_text(json.dumps(preflight), encoding="utf-8")
    monkeypatch.setattr(
        v3.v2,
        "verify_seed_gate",
        lambda *args, **kwargs: {
            "source_git_commit": binding["design_source_git_commit"]
        },
    )
    with pytest.raises(v3.ProbeError, match="design HEAD"):
        v3._verify_seed_gate_binding(tmp_path, binding, "base_2b", {})


def test_binding_writer_adds_durable_v3_provenance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    written = {}

    def fake_write(path: Path, value: object, *, exclusive: bool = False) -> None:
        written[path.name] = (value, exclusive)

    monkeypatch.setattr(v3.v1, "write_json", fake_write)
    binding = _binding()
    with v3._binding_writer(binding):
        v3.v1.write_json(
            tmp_path / "preflight.json",
            {"schema_version": f"{v3.v2.SEED_GATE_SCHEMA}.preflight"},
        )
    value, _exclusive = written["preflight.json"]
    assert value["v3_runtime_binding"] == v3._public_binding(binding)


def test_analysis_loader_rebinds_copy_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    design_path = tmp_path / "design.json"
    design_path.write_text("{}", encoding="utf-8")
    binding = _binding()
    binding["design_sha256"] = v3.v1.sha256_file(design_path)
    original = _design()
    monkeypatch.setattr(v3, "_V2_LOAD_DESIGN", lambda *args, **kwargs: original)
    loaded = v3._analysis_design_loader(binding)(design_path, {}, "2" * 64)
    assert loaded["source_git_commit"] == binding["execution_commit"]
    assert original["source_git_commit"] == binding["design_source_git_commit"]


def test_validate_binding_requires_positive_execution_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        v3.verifier,
        "verify",
        lambda *args, **kwargs: {
            "schema_version": "kaetram.local-trigger-incidence-v3-verification.v1",
            "execution_ready": False,
            "execution_commit": None,
        },
    )
    with pytest.raises(v3.ProbeError, match="did not authorize"):
        v3.validate_execution_binding(REGISTRATION, Path("/archive"), DESIGN_DIR)


def test_validate_binding_records_both_freeze_commits_and_hashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effective = _effective()
    design = _design("a" * 40)
    registration = {
        "study_id": effective["study_id"],
        "claim_boundary": effective["claim_boundary"],
    }
    evidence = {
        "schema_version": "kaetram.local-trigger-incidence-v3-verification.v1",
        "study_id": effective["study_id"],
        "execution_ready": True,
        "execution_commit": "b" * 40,
        "design_sha256": "3" * 64,
    }
    monkeypatch.setattr(v3.verifier, "verify", lambda *args, **kwargs: evidence)
    monkeypatch.setattr(
        v3.prepare, "load_registration", lambda path: (registration, "1" * 64)
    )
    monkeypatch.setattr(
        v3.prepare, "materialize_effective_registration", lambda value: effective
    )
    monkeypatch.setattr(
        v3.v2, "load_registration", lambda path: (effective, "2" * 64)
    )
    monkeypatch.setattr(v3, "_V2_LOAD_DESIGN", lambda *args, **kwargs: design)
    monkeypatch.setattr(
        v3.v1,
        "sha256_file",
        lambda path: "3" * 64 if Path(path).name == "design.json" else "4" * 64,
    )
    binding = v3.validate_execution_binding(REGISTRATION, Path("/archive"), DESIGN_DIR)
    assert binding["design_source_git_commit"] == "a" * 40
    assert binding["execution_commit"] == "b" * 40
    assert binding["v3_registration_sha256"] == "1" * 64
    assert binding["effective_registration_sha256"] == "2" * 64
    assert binding["expected_request_count"] == 1200
    assert binding["execution_verification_sha256"] == v3.v1.sha256_json(evidence)


def test_validate_binding_rejects_wrong_registration_path(tmp_path: Path) -> None:
    with pytest.raises(v3.ProbeError, match="unexpected V3 registration path"):
        v3.validate_execution_binding(
            ROOT / "research/experiments/local-trigger-incidence-v2.json",
            tmp_path,
            DESIGN_DIR,
        )


def test_checkpoint_grid_rejects_schedule_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _binding()
    expected = [
        row
        for row in v3._expected_request_grid(
            binding["effective_registration"], binding["design"]
        )
        if row["snapshot"] == "base_2b"
    ]
    rows = [dict(row) for row in expected]
    rows[7]["seed"] += 1
    prelaunch = {
        "source_git_commit": binding["execution_commit"],
        "dirty_paths": [],
        "registration_sha256": binding["effective_registration_sha256"],
        "v3_runtime_binding": v3._public_binding(binding),
    }
    monkeypatch.setattr(
        v3.v1,
        "_verify_run_directory",
        lambda *args: (prelaunch, {}, {}, rows, {}),
    )
    with pytest.raises(v3.ProbeError, match="schedule differs"):
        v3._verify_checkpoint_grid(Path("/run"), binding, "base_2b")


def test_checkpoint_grid_requires_exact_seed_gate_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = _binding()
    rows = [
        row
        for row in v3._expected_request_grid(
            binding["effective_registration"], binding["design"]
        )
        if row["snapshot"] == "base_2b"
    ]
    prelaunch = {
        "source_git_commit": binding["execution_commit"],
        "dirty_paths": [],
        "registration_sha256": binding["effective_registration_sha256"],
        "v3_runtime_binding": v3._public_binding(binding),
        "seed_gate_artifact_index_sha256": "wrong",
        "seed_gate_tree_sha256": "5" * 64,
    }
    monkeypatch.setattr(
        v3.v1,
        "_verify_run_directory",
        lambda *args: (prelaunch, {}, {}, rows, {}),
    )
    with pytest.raises(v3.ProbeError, match="passed V3 seed gate"):
        v3._verify_checkpoint_grid(
            Path("/run"),
            binding,
            "base_2b",
            {"artifact_index_sha256": "4" * 64, "tree_sha256": "5" * 64},
        )
