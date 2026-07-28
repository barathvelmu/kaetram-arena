from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.opd import prepare_trigger_incidence_v3 as prepare
from scripts.opd import trigger_incidence_probe_v2 as v2


ROOT = Path(__file__).resolve().parents[2]
REGISTRATION = ROOT / "research/experiments/local-trigger-incidence-v3.json"


def _registration() -> dict:
    return json.loads(REGISTRATION.read_text(encoding="utf-8"))


def test_registration_materializes_exact_v2_request_protocol() -> None:
    registration, _ = prepare.load_registration(REGISTRATION)
    effective = prepare.materialize_effective_registration(registration)
    baseline = json.loads(
        (ROOT / registration["frozen_v2_protocol"]["path"]).read_text(
            encoding="utf-8"
        )
    )
    for field in prepare.INHERITED_FIELDS:
        assert effective[field] == baseline[field]
    assert effective["study_id"] == "local-trigger-incidence-seeded-v3"
    assert effective["state_pool"]["source_run_id"] == "run_20260613_112422"
    assert "run_20260613_112422" in effective["state_pool"]["source_glob"]
    assert "run_20260608_185339" not in effective["state_pool"]["source_glob"]
    assert len(effective["state_pool"]["excluded_source_logs"]) == 20


def test_effective_registration_remains_v2_runner_compatible(tmp_path: Path) -> None:
    registration, _ = prepare.load_registration(REGISTRATION)
    effective = prepare.materialize_effective_registration(registration)
    path = tmp_path / "effective.json"
    path.write_text(
        json.dumps(effective, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    loaded, _ = v2.load_registration(path)
    assert loaded == effective


@pytest.mark.parametrize(
    "payload,match",
    [
        ('{"a": 1, "a": 2}', "duplicate JSON key"),
        ('{"a": NaN}', "non-finite JSON value"),
    ],
)
def test_json_loader_rejects_ambiguous_values(
    tmp_path: Path, payload: str, match: str
) -> None:
    path = tmp_path / "bad.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(prepare.ProbeError, match=match):
        prepare._read_json(path)


def test_source_glob_cannot_escape_historical_root(tmp_path: Path) -> None:
    registration = _registration()
    registration["state_pool"]["source_glob"] = "../outside/*.log"
    with pytest.raises(prepare.ProbeError, match="source_glob"):
        prepare.verify_source_archive(registration, tmp_path)


def test_source_archive_hash_drift_fails_closed(tmp_path: Path) -> None:
    registration = _registration()
    (tmp_path / "SHA256SUMS").write_text("mutated\n", encoding="utf-8")
    (tmp_path / "inventory.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(prepare.ProbeError, match="archive identity mismatch"):
        prepare.verify_source_archive(registration, tmp_path)


def test_run_metadata_drift_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registration = _registration()
    run_id = registration["state_pool"]["source_run_id"]
    inventory = {
        "schema_version": "kaetram-historical-artifact-inventory-v2",
        "groups": {"opd_2b": {"complete": True, "run_ids": [run_id]}},
    }
    (tmp_path / "inventory.json").write_text(
        json.dumps(inventory), encoding="utf-8"
    )
    (tmp_path / "SHA256SUMS").write_text("fixture\n", encoding="utf-8")
    personalities = ("grinder", "completionist", "explorer_tinkerer")
    for agent_id, personality in enumerate(personalities):
        directory = (
            tmp_path / f"dataset/raw/agent_{agent_id}/runs/{run_id}"
        )
        directory.mkdir(parents=True)
        meta = {
            "run_id": run_id,
            "agent_id": agent_id,
            "personality": personality,
            "harness": "qwen",
            "model": "2b-opd-r3",
            "n_agents": 3,
            "hours_budget": 6.0,
        }
        if agent_id == 1:
            meta["model"] = "drifted-model"
        (directory / "run.meta.json").write_text(
            json.dumps(meta), encoding="utf-8"
        )
        (directory / "harness_meta_template.json").write_text(
            json.dumps(
                {
                    "agent_id": agent_id,
                    "personality": personality,
                    "harness": "qwen",
                    "model": "2b-opd-r3",
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.setattr(prepare, "_assert_exact_file", lambda *args: None)
    with pytest.raises(prepare.ProbeError, match="agent_1"):
        prepare.verify_source_archive(registration, tmp_path)


def test_panel_overlap_is_rejected() -> None:
    with pytest.raises(prepare.ProbeError, match="overlaps"):
        prepare.require_zero_panel_overlap(
            {"dataset/raw/run-v3/session-1.log"},
            {"dataset/raw/run-v3/session-1.log"},
        )


def test_identity_bearing_design_is_rejected() -> None:
    with pytest.raises(prepare.ProbeError, match="identity-bearing"):
        prepare.require_identity_safe_design(
            {"states": [{"messages": [{"content": "/Users/reviewer/private"}]}]}
        )


def test_dirty_checkout_blocks_preparation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prepare, "_git", lambda args: " M registration.json")
    with pytest.raises(prepare.ProbeError, match="clean checkout"):
        prepare.require_clean_pushed_registration(REGISTRATION)


def test_unpushed_checkout_blocks_preparation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head = "a" * 40
    registration_text = REGISTRATION.read_text(encoding="utf-8").rstrip("\n")

    def fake_git(args: list[str]) -> str:
        if args == ["status", "--porcelain"]:
            return ""
        if args == ["rev-parse", "HEAD"]:
            return head
        if args[0] == "ls-files":
            return prepare.REGISTRATION_PATH.as_posix()
        if args[0] == "show":
            return registration_text
        if args == ["rev-parse", "@{upstream}"]:
            return "b" * 40
        raise AssertionError(args)

    monkeypatch.setattr(prepare, "_git", fake_git)
    with pytest.raises(prepare.ProbeError, match="not pushed"):
        prepare.require_clean_pushed_registration(REGISTRATION)


def test_symlinked_historical_root_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "archive"
    target.mkdir()
    link = tmp_path / "archive-link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(prepare.ProbeError, match="non-symlink"):
        prepare.verify_source_archive(_registration(), link)


def test_prepare_writes_one_closed_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registration = _registration()
    registration_sha = "1" * 64
    effective = prepare.materialize_effective_registration(registration)
    source_audit = {
        "source_run_id": "run_20260613_112422",
        "matched_source_log_count": 1154,
        "eligible_source_log_count": 370,
        "reconstructable_decision_state_count": 367,
        "matched_source_logs_sha256": "2" * 64,
        "eligible_source_logs_sha256": "3" * 64,
    }
    states = []
    for index in range(20):
        messages = [{"role": "user", "content": f"state {index}"}]
        states.append(
            {
                "state_id": f"state-{index + 1:02d}",
                "personality": "completionist",
                "source_log": (
                    "dataset/raw/agent_1/runs/run_20260613_112422/"
                    f"session_{index}.log"
                ),
                "source_log_sha256": "4" * 64,
                "messages_sha256": prepare.v1.sha256_json(messages),
                "messages": messages,
            }
        )

    def fake_derive(
        effective_registration: dict,
        effective_sha: str,
        historical_root: Path,
        git_identity: dict,
    ) -> dict:
        return {
            "schema_version": prepare.v1.DESIGN_SCHEMA,
            "study_id": effective_registration["study_id"],
            "registration_sha256": effective_sha,
            "source_log_count": 1154,
            "eligible_source_log_count": 370,
            "personality": "completionist",
            "selection_stride": 9,
            "excluded_source_log_count": 20,
            "excluded_source_logs_sha256": "5" * 64,
            "states": states,
            **git_identity,
        }

    monkeypatch.setattr(
        prepare,
        "load_registration",
        lambda path: (registration, registration_sha),
    )
    monkeypatch.setattr(
        prepare,
        "require_clean_pushed_registration",
        lambda path: {"source_git_commit": "a" * 40, "dirty_paths": []},
    )
    monkeypatch.setattr(
        prepare, "verify_source_archive", lambda registration, root: source_audit
    )
    monkeypatch.setattr(
        prepare, "materialize_effective_registration", lambda registration: effective
    )
    monkeypatch.setattr(prepare.v2, "_derive_design", fake_derive)
    output = tmp_path / "v3-design"
    receipt = prepare.prepare(REGISTRATION, tmp_path, output)
    assert receipt["execution_authorized"] is False
    assert {path.name for path in output.iterdir()} == {
        "effective-registration.json",
        "design.json",
        "design.receipt.json",
        "v3-preparation.receipt.json",
    }
    with pytest.raises(prepare.ProbeError, match="overwrite"):
        prepare.prepare(REGISTRATION, tmp_path, output)
