from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import copy
from pathlib import Path

import pytest

import scripts.opd.live_routing_orchestrator as orchestrator
import scripts.opd.live_routing_services as services_module
from canonical_start import canonical_database_documents
from scripts.opd.live_routing_analyzer import (
    canonical_json_bytes,
    canonical_sha256,
    validate_trial_envelope,
)
from scripts.opd.live_routing_orchestrator import (
    EXPECTED_INSERTION_ORDER,
    MONGO_COLLECTIONS,
    OrchestrationError,
    TrialExecution,
    assemble_trial_receipt,
    attest_python_runtime,
    build_runtime_preflight,
    create_result_root,
    publish_bytes_create_only,
    publish_json_create_only,
    publish_completed_package,
    publish_trial_receipt,
    run_exact_trial_sequence,
    run_orchestration,
)
from scripts.opd.live_routing_launcher import PartialSeedError
from scripts.opd.live_routing_prelaunch import EXPECTED_LANE, build_prelaunch_payload
from scripts.opd.live_routing_result_verify import verify_package_or_raise
from tests.unit.test_live_routing_analyzer import _unsigned_receipt
from tests.unit.test_live_routing_prelaunch import _ready_repo
from tests.unit.test_live_routing_result_verify import _services_evidence


class Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def monotonic(self) -> float:
        self.value += 0.001
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def _plans() -> list[dict]:
    arms = (
        "structured_direct",
        "content_recovery_on",
        "content_recovery_off",
        "content_recovery_on",
        "content_recovery_off",
        "structured_direct",
        "content_recovery_off",
        "structured_direct",
        "content_recovery_on",
    )
    return [
        {
            "schedule_index": index,
            "trial_id": f"trial-{index:02d}",
            "trial_key": f"key-{index:02d}",
            "repeat": (index - 1) // 3 + 1,
            "arm": arm,
            "username": f"lr_local_{index:02d}",
            "treatment_session_id": f"llrd-local-t{index:02d}-treatment",
            "reconnect_session_id": f"llrd-local-t{index:02d}-reconnect",
        }
        for index, arm in enumerate(arms, start=1)
    ]


def _registration() -> dict:
    return {
        "runtime_parameters": {"minimum_disconnect_settle_seconds": 1.5},
        "live_contract": {
            "browser_name": "chromium",
            "browser_version": "149.0.7827.55",
            "browser_executable_sha256": "b" * 64,
        },
    }


def _runtime(spec, number: int) -> dict:
    parsed = {
        "schema_version": "kaetram.diagnostic-runtime-attestation.v1",
        "session_id": spec.session_id,
        "mcp_pid": 20_000 + number,
        "mcp_process_group": 10_000 + number,
        "mcp_instance_nonce": f"{number:032x}",
        "browser_launch_nonce": f"{number + 100:032x}",
        "browser_nonce_echo": f"{number + 100:032x}",
        "browser_name": "chromium",
        "browser_version": "149.0.7827.55",
        "browser_executable_sha256": "b" * 64,
        "page_url": "http://127.0.0.1:9000/",
        "player_username": spec.username,
        "configured_client_url": "http://127.0.0.1:9000",
        "configured_game_port": "9191",
        "require_existing_account": True,
        "heartbeats_disabled": True,
        "loopback_only": True,
    }
    raw = "__diagnostic_runtime_attestation: " + json.dumps(
        parsed, separators=(",", ":"), sort_keys=True
    )
    return {
        "raw_text": raw,
        "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "parsed": parsed,
    }


def _measure(label: str) -> dict:
    raw = f'observe: {{"label":"{label}"}}'
    return {
        "available": True,
        "raw_text": raw,
        "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "normalized_projection": {"label": label},
    }


class FakeStore:
    def __init__(self) -> None:
        self.operations: list[tuple[str, str]] = []

    def insert_canonical(self, username: str, trial_id: str) -> dict:
        self.operations.append(("seed", username))
        ids = {name: f"{username}-{name}" for name in EXPECTED_INSERTION_ORDER}
        return {
            "database": "kaetram_e2e",
            "username": username,
            "trial_id": trial_id,
            "absence": {
                "database": "kaetram_e2e",
                "counts": {username: {name: 0 for name in MONGO_COLLECTIONS}},
                "all_absent": True,
            },
            "inserted_ids": ids,
            "insertion_order": list(EXPECTED_INSERTION_ORDER),
            "player_info_inserted_last": True,
        }

    def snapshot_owned(self, username: str, inserted_ids) -> dict:
        self.operations.append(("snapshot", username))
        documents = canonical_database_documents(username)
        for name in MONGO_COLLECTIONS:
            documents[name]["_id"] = inserted_ids[name]
        return {
            "database": "kaetram_e2e",
            "username": username,
            "documents": documents,
        }

    def cleanup_owned(self, username: str, trial_id: str, inserted_ids) -> dict:
        self.operations.append(("cleanup", username))
        return {
            "database": "kaetram_e2e",
            "deleted": {name: 1 for name in MONGO_COLLECTIONS},
            "lock_deleted": 1,
            "absence": {
                "database": "kaetram_e2e",
                "counts": {username: {name: 0 for name in MONGO_COLLECTIONS}},
                "all_absent": True,
            },
            "complete": True,
        }

    def attest_topology(self) -> dict:
        return {
            "uri": "mongodb://127.0.0.1:27017/kaetram_e2e",
            "database": "kaetram_e2e",
            "nodes": [{"host": "127.0.0.1", "port": 27017}],
            "loopback_only": True,
        }

    def prove_absent(self, usernames) -> dict:
        return {
            "database": "kaetram_e2e",
            "counts": {
                username: {name: 0 for name in MONGO_COLLECTIONS}
                for username in usernames
            },
            "all_absent": True,
        }

    def close(self) -> None:
        self.operations.append(("close", ""))


def _execution_from_valid_receipt(receipt: dict) -> TrialExecution:
    """Invert the analyzer fixture into the launcher's raw execution shape."""

    evidence = receipt["execution_evidence"]
    plan = copy.deepcopy(receipt["plan"])
    username = plan["username"]
    seed = copy.deepcopy(evidence["seed"])
    seed["absence"] = {
        "database": evidence["absence"]["database"],
        "counts": {username: copy.deepcopy(evidence["absence"]["counts"])},
        "all_absent": evidence["absence"]["all_absent"],
    }
    treatment = {
        "runtime_attestation": copy.deepcopy(
            evidence["runtime_attestations"]["treatment"]
        ),
        "precondition": copy.deepcopy(receipt["precondition"]),
        "routing": copy.deepcopy(receipt["routing"]),
        "candidate_call_ledger": copy.deepcopy(evidence["candidate_call_ledger"]),
        "immediate": copy.deepcopy(receipt["measurements"]["immediate"]),
        "delayed": copy.deepcopy(receipt["measurements"]["delayed"]),
        "delayed_elapsed_monotonic_seconds": receipt["measurements"][
            "delayed_elapsed_monotonic_seconds"
        ],
    }
    reconnect = {
        "runtime_attestation": copy.deepcopy(
            evidence["runtime_attestations"]["reconnect"]
        ),
        "reconnect": copy.deepcopy(receipt["measurements"]["reconnect"]),
    }
    database_snapshot = json.loads(receipt["measurements"]["database"]["raw_text"])
    cleanup_evidence = evidence["cleanup"]
    cleanup = {
        "database": cleanup_evidence["database"],
        "deleted": copy.deepcopy(cleanup_evidence["deleted_counts"]),
        "lock_deleted": cleanup_evidence["lock_deleted"],
        "absence": {
            "database": cleanup_evidence["database"],
            "counts": {
                username: copy.deepcopy(cleanup_evidence["post_cleanup_counts"])
            },
            "all_absent": cleanup_evidence["all_absent"],
        },
        "complete": cleanup_evidence["all_absent"],
    }
    return TrialExecution(
        plan=plan,
        seed=seed,
        treatment=treatment,
        reconnect=reconnect,
        database_snapshot=database_snapshot,
        cleanup=cleanup,
        parent_event_ledger=copy.deepcopy(evidence["parent_event_ledger"]),
    )


def _write_canonical(path: Path, value: dict) -> bytes:
    raw = canonical_json_bytes(value) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def test_result_root_and_publication_are_create_only_and_outside_repos(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(OrchestrationError, match="outside"):
        create_result_root(source / "output", protected_roots=[source])
    output = create_result_root(tmp_path / "output", protected_roots=[source])
    assert output.is_dir() and (output / "receipts").is_dir()
    with pytest.raises(OrchestrationError, match="already exists"):
        create_result_root(output, protected_roots=[source])

    path = output / "receipt.json"
    digest = publish_json_create_only(path, {"ok": True})
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert stat.S_IMODE(path.stat().st_mode) == 0o444
    with pytest.raises(OrchestrationError, match="overwrite"):
        publish_bytes_create_only(path, b"replacement\n")


def test_exact_parent_sequence_runs_18_cold_workers_and_cleans_after_snapshot() -> None:
    plans = _plans()
    store = FakeStore()
    clock = Clock()
    worker_specs = []

    def worker(spec):
        worker_specs.append(spec)
        runtime = _runtime(spec, len(worker_specs))
        if spec.phase == "reconnect":
            return {
                "runtime_attestation": runtime,
                "reconnect": _measure("reconnect"),
            }
        return {
            "runtime_attestation": runtime,
            "precondition": _measure("precondition"),
            "routing": {},
            "candidate_call_ledger": [],
            "immediate": _measure("immediate"),
            "delayed": _measure("delayed"),
            "delayed_elapsed_monotonic_seconds": 5.0,
        }

    executions, runtimes = run_exact_trial_sequence(
        plans,
        _registration(),
        store=store,
        worker_runner=worker,
        global_absence={"database": "kaetram_e2e", "all_absent": True},
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    assert len(executions) == 9
    assert len(runtimes) == 18
    assert [spec.phase for spec in worker_specs] == [
        phase for _ in range(9) for phase in ("treatment", "reconnect")
    ]
    assert store.operations == [
        operation
        for plan in plans
        for operation in (
            ("seed", plan["username"]),
            ("snapshot", plan["username"]),
            ("cleanup", plan["username"]),
        )
    ]
    expected_events = [
        "absence_confirmed",
        "seed_completed",
        "treatment_started",
        "treatment_finished",
        "treatment_settle_finished",
        "reconnect_started",
        "reconnect_finished",
        "reconnect_settle_finished",
        "database_snapshot_recorded",
        "cleanup_completed",
        "cleanup_absence_confirmed",
    ]
    for execution in executions:
        ledger = execution.parent_event_ledger
        assert [row["event"] for row in ledger] == expected_events
        times = {row["event"]: row["monotonic_seconds"] for row in ledger}
        assert times["treatment_settle_finished"] - times["treatment_finished"] >= 1.5
        assert times["reconnect_settle_finished"] - times["reconnect_finished"] >= 1.5


def test_sequence_refuses_missing_absence_before_any_mutation() -> None:
    store = FakeStore()
    with pytest.raises(OrchestrationError, match="global username absence"):
        run_exact_trial_sequence(
            _plans(),
            _registration(),
            store=store,
            worker_runner=lambda _spec: {},
            global_absence={"database": "kaetram_e2e", "all_absent": False},
        )
    assert store.operations == []


def test_incomplete_owned_cleanup_is_retained_and_later_trials_continue() -> None:
    class IncompleteFirstCleanupStore(FakeStore):
        def cleanup_owned(self, username: str, trial_id: str, inserted_ids) -> dict:
            value = super().cleanup_owned(username, trial_id, inserted_ids)
            if username == _plans()[0]["username"]:
                value["absence"]["counts"][username]["player_info"] = 1
                value["absence"]["all_absent"] = False
                value["complete"] = False
            return value

    store = IncompleteFirstCleanupStore()
    counter = 0

    def worker(spec):
        nonlocal counter
        counter += 1
        result = {
            "runtime_attestation": _runtime(spec, counter),
            "reconnect": _measure("reconnect"),
        }
        if spec.phase == "treatment":
            result.update(
                precondition=_measure("precondition"),
                routing={},
                candidate_call_ledger=[],
                immediate=_measure("immediate"),
                delayed=_measure("delayed"),
                delayed_elapsed_monotonic_seconds=5.0,
            )
        return result

    clock = Clock()
    executions, _ = run_exact_trial_sequence(
        _plans(),
        _registration(),
        store=store,
        worker_runner=worker,
        global_absence={"database": "kaetram_e2e", "all_absent": True},
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    assert len(executions) == 9
    assert executions[0].cleanup["absence"]["all_absent"] is False
    assert executions[0].parent_event_ledger[-1]["event"] == "cleanup_completed"
    assert all(
        execution.parent_event_ledger[-1]["event"] == "cleanup_absence_confirmed"
        for execution in executions[1:]
    )


def test_runtime_probe_binds_exact_interpreter_and_versions(tmp_path: Path) -> None:
    executable = tmp_path / "python"
    executable.write_bytes(b"frozen-python")
    expected = {
        "python_version": "3.12.13",
        "python_executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "mcp_version": "1.28.1",
        "playwright_version": "1.61.0",
        "pymongo_version": "4.17.0",
    }

    def runner(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps(
                {
                    "python_version": "3.12.13",
                    "mcp_version": "1.28.1",
                    "playwright_version": "1.61.0",
                    "pymongo_version": "4.17.0",
                }
            ),
            stderr="",
        )

    assert attest_python_runtime(executable, expected, command_runner=runner) == expected
    drift = {**expected, "mcp_version": "0.0.0"}
    with pytest.raises(OrchestrationError, match="mcp_version"):
        attest_python_runtime(executable, drift, command_runner=runner)


def test_runtime_preflight_is_self_hashed() -> None:
    prelaunch = {
        "study_id": "study",
        "run_id": "local001",
        "registration": {"sha256": "a" * 64},
        "payload_sha256": "b" * 64,
    }
    record = build_runtime_preflight(
        prelaunch,
        game={"game": 1},
        mongo={"mongo": 1},
        python={"python": 1},
        services={"services": 1},
    )
    unsigned = {key: value for key, value in record.items() if key != "payload_sha256"}
    assert record["payload_sha256"] == hashlib.sha256(
        json.dumps(unsigned, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def test_cli_supervises_services_and_forwards_exact_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    live = {
        "game_revision": "7" * 40,
        "game_bundle_sha256": "8" * 64,
        "client_dist_inventory_sha256": "9" * 64,
        "node_version": "v20.20.2",
        "node_executable_sha256": "a" * 64,
        "docker_client_version": "Docker version 29.2.1, build a5c7197",
        "docker_executable_sha256": "b" * 64,
    }
    evidence = {"sealed": "exact-service-evidence"}
    captured: dict = {}

    monkeypatch.setattr(
        orchestrator,
        "load_json_strict",
        lambda _path: (
            {
                "status": orchestrator.READY_STATUS,
                "live_contract": live,
                "runtime_parameters": {
                    "service_readiness_timeout_seconds": 60
                },
            },
            b"",
        ),
    )
    monkeypatch.setattr(orchestrator, "validate_registration", lambda *_a, **_k: [])
    monkeypatch.setattr(
        orchestrator, "verify_prelaunch_receipt", lambda *_a, **_k: []
    )
    monkeypatch.setattr(
        orchestrator, "attest_python_runtime", lambda *_a, **_k: {"ok": True}
    )

    def supervised(config, callback):
        captured["config"] = config
        return callback(evidence)

    def fake_orchestration(**kwargs):
        captured["orchestration"] = kwargs
        return {"status": "offline-test"}

    monkeypatch.setattr(services_module, "run_with_local_services", supervised)
    monkeypatch.setattr(orchestrator, "run_orchestration", fake_orchestration)
    code = orchestrator.main(
        [
            "--registration", str(tmp_path / "registration.json"),
            "--prelaunch", str(tmp_path / "prelaunch.json"),
            "--result-root", str(tmp_path / "result"),
            "--repo-root", str(tmp_path / "repo"),
            "--expected-head", "a" * 40,
            "--game-root", str(tmp_path / "game"),
            "--python", str(tmp_path / "python"),
            "--docker", str(tmp_path / "docker"),
            "--node", str(tmp_path / "node"),
        ]
    )
    assert code == 0
    assert captured["config"].game_revision == live["game_revision"]
    assert captured["config"].readiness_timeout_seconds == 60
    assert captured["orchestration"]["services_evidence"] is evidence
    assert captured["config"].node_version == live["node_version"]
    assert captured["config"].node_executable_sha256 == live[
        "node_executable_sha256"
    ]
    assert captured["config"].docker_client_version == live[
        "docker_client_version"
    ]
    assert captured["config"].docker_executable_sha256 == live[
        "docker_executable_sha256"
    ]
    assert json.loads(capsys.readouterr().out) == {"status": "offline-test"}


def test_cli_refuses_invalid_source_prelaunch_before_starting_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    registration = {
        "status": orchestrator.READY_STATUS,
        "live_contract": {
            "game_revision": "7" * 40,
            "game_bundle_sha256": "8" * 64,
            "client_dist_inventory_sha256": "9" * 64,
        },
        "runtime_parameters": {"service_readiness_timeout_seconds": 60},
    }
    monkeypatch.setattr(
        orchestrator, "load_json_strict", lambda _path: (registration, b"")
    )
    monkeypatch.setattr(orchestrator, "validate_registration", lambda *_a, **_k: [])
    monkeypatch.setattr(
        orchestrator,
        "verify_prelaunch_receipt",
        lambda *_a, **_k: ["source commit differs from exact registered head"],
    )
    monkeypatch.setattr(
        services_module,
        "run_with_local_services",
        lambda *_a, **_k: pytest.fail("services must not start"),
    )
    code = orchestrator.main(
        [
            "--registration", str(tmp_path / "registration.json"),
            "--prelaunch", str(tmp_path / "prelaunch.json"),
            "--result-root", str(tmp_path / "result"),
            "--repo-root", str(tmp_path / "repo"),
            "--expected-head", "a" * 40,
            "--game-root", str(tmp_path / "game"),
            "--python", str(tmp_path / "python"),
        ]
    )
    assert code == 1
    assert "pre-service prelaunch verification failed" in capsys.readouterr().err


def test_assembled_receipt_matches_strict_analyzer_envelope(tmp_path: Path) -> None:
    repo, registration_path, head = _ready_repo(tmp_path)
    registration = json.loads(registration_path.read_text())
    prelaunch = build_prelaunch_payload(
        registration_path,
        repo_root=repo,
        expected_head=head,
        run_id="local001",
        lane=EXPECTED_LANE,
    )
    expected = _unsigned_receipt(registration, prelaunch, prelaunch["trials"][0])
    receipt = assemble_trial_receipt(
        _execution_from_valid_receipt(expected),
        prelaunch,
        previous_payload_sha256=prelaunch["payload_sha256"],
    )
    validate_trial_envelope(receipt)
    assert "isolation" not in receipt
    assert "lifecycle" not in receipt
    expected["previous_receipt_payload_sha256"] = prelaunch["payload_sha256"]
    expected["payload_sha256"] = receipt["payload_sha256"]
    assert canonical_json_bytes(receipt) == canonical_json_bytes(expected)


def test_completed_package_is_create_only_and_passes_real_offline_verifier(
    tmp_path: Path,
) -> None:
    repo, registration_path, head = _ready_repo(tmp_path)
    registration = json.loads(registration_path.read_text())
    prelaunch = build_prelaunch_payload(
        registration_path,
        repo_root=repo,
        expected_head=head,
        run_id="local001",
        lane=EXPECTED_LANE,
    )
    root = create_result_root(tmp_path / "package", protected_roots=[repo])
    prelaunch_raw = canonical_json_bytes(prelaunch) + b"\n"
    prelaunch_file_sha = publish_bytes_create_only(root / "prelaunch.json", prelaunch_raw)
    live = registration["live_contract"]
    runtime_preflight = build_runtime_preflight(
        prelaunch,
        game={
            "git_head": live["game_revision"],
            "worktree_clean": True,
            "bundle_path": "packages/server/dist/main.js",
            "bundle_size_bytes": 1234,
            "bundle_sha256": live["game_bundle_sha256"],
            "client_dist_file_count": 25,
            "client_dist_inventory_sha256": live["client_dist_inventory_sha256"],
        },
        mongo={
            "uri": "mongodb://127.0.0.1:27017/kaetram_e2e",
            "database": "kaetram_e2e",
            "nodes": [{"host": "127.0.0.1", "port": 27017}],
            "loopback_only": True,
        },
        python={
            key: live[key]
            for key in (
                "python_version",
                "python_executable_sha256",
                "mcp_version",
                "playwright_version",
                "pymongo_version",
            )
        },
        services=_services_evidence(live),
    )
    runtime_file_sha = publish_json_create_only(
        root / "runtime-preflight.json", runtime_preflight
    )
    executions = [
        _execution_from_valid_receipt(_unsigned_receipt(registration, prelaunch, plan))
        for plan in prelaunch["trials"]
    ]
    receipts = []
    entries = []
    previous = prelaunch["payload_sha256"]
    for execution in executions:
        receipt, entry = publish_trial_receipt(
            root,
            execution,
            prelaunch,
            previous_payload_sha256=previous,
        )
        receipts.append(receipt)
        entries.append(entry)
        previous = receipt["payload_sha256"]
    result = publish_completed_package(
        root,
        registration=registration,
        prelaunch=prelaunch,
        prelaunch_raw=prelaunch_raw,
        runtime_preflight=runtime_preflight,
        prelaunch_file_sha256=prelaunch_file_sha,
        runtime_preflight_file_sha256=runtime_file_sha,
        receipts=receipts,
        entries=entries,
        registration_path=registration_path,
        repo_root=repo,
        expected_head=head,
    )
    assert result["verified"] == verify_package_or_raise(
        root, registration_path, repo_root=repo, expected_head=head
    )
    assert len(list((root / "receipts").glob("trial-*.json"))) == 9
    assert all(
        stat.S_IMODE(path.stat().st_mode) == 0o444
        for path in root.rglob("*.json")
    )
    with pytest.raises(OrchestrationError, match="overwrite"):
        publish_completed_package(
            root,
            registration=registration,
            prelaunch=prelaunch,
            prelaunch_raw=prelaunch_raw,
            runtime_preflight=runtime_preflight,
            prelaunch_file_sha256=prelaunch_file_sha,
            runtime_preflight_file_sha256=runtime_file_sha,
            receipts=receipts,
            entries=entries,
            registration_path=registration_path,
            repo_root=repo,
            expected_head=head,
        )


def test_run_orchestration_rechecks_runtime_and_removes_temporary_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, registration_path, head = _ready_repo(tmp_path)
    registration = json.loads(registration_path.read_text())
    prelaunch = build_prelaunch_payload(
        registration_path,
        repo_root=repo,
        expected_head=head,
        run_id="local001",
        lane=EXPECTED_LANE,
    )
    prelaunch_path = tmp_path / "prelaunch-source.json"
    _write_canonical(prelaunch_path, prelaunch)
    game_root = tmp_path / "game"
    game_root.mkdir()
    game = {
        "git_head": registration["live_contract"]["game_revision"],
        "worktree_clean": True,
        "bundle_path": "packages/server/dist/main.js",
        "bundle_size_bytes": 1234,
        "bundle_sha256": registration["live_contract"]["game_bundle_sha256"],
        "client_dist_file_count": 25,
        "client_dist_inventory_sha256": registration["live_contract"][
            "client_dist_inventory_sha256"
        ],
    }
    python = {
        key: registration["live_contract"][key]
        for key in (
            "python_version",
            "python_executable_sha256",
            "mcp_version",
            "playwright_version",
            "pymongo_version",
        )
    }
    services_evidence = _services_evidence(registration["live_contract"])
    store = FakeStore()
    topology_calls = 0

    def topology():
        nonlocal topology_calls
        topology_calls += 1
        return {
            "uri": "mongodb://127.0.0.1:27017/kaetram_e2e",
            "database": "kaetram_e2e",
            "nodes": [{"host": "127.0.0.1", "port": 27017}],
            "loopback_only": True,
        }

    store.attest_topology = topology
    game_calls = 0

    def game_attestor(_root, _registration):
        nonlocal game_calls
        game_calls += 1
        return copy.deepcopy(game)

    executions_by_username = {
        plan["username"]: _execution_from_valid_receipt(
            _unsigned_receipt(registration, prelaunch, plan)
        )
        for plan in prelaunch["trials"]
    }
    state_dirs: list[Path] = []

    def session_worker(spec, _registration_path, **kwargs):
        state_dir = kwargs["state_dir"]
        state_dir.mkdir(parents=True)
        (state_dir / "owned-marker").write_text("ephemeral\n")
        state_dirs.append(state_dir)
        execution = executions_by_username[spec.username]
        return copy.deepcopy(
            execution.treatment if spec.phase == "treatment" else execution.reconnect
        )

    package_calls = 0
    source_checks = 0
    real_source_check = orchestrator.verify_prelaunch_receipt

    def source_check(*args, **kwargs):
        nonlocal source_checks
        source_checks += 1
        return real_source_check(*args, **kwargs)

    def package_publisher(root, **kwargs):
        nonlocal package_calls
        package_calls += 1
        assert len(kwargs["receipts"]) == 9
        assert len(kwargs["entries"]) == 9
        assert kwargs["repo_root"] == repo
        assert kwargs["expected_head"] == head
        assert kwargs["runtime_preflight"]["services"] == services_evidence
        assert all(not path.exists() for path in state_dirs)
        assert (root / "prelaunch.json").is_file()
        assert (root / "runtime-preflight.json").is_file()
        return {"manifest": {}, "analysis": {}, "verified": {}}

    monkeypatch.setattr(orchestrator, "attest_python_runtime", lambda *_a, **_k: python)
    monkeypatch.setattr(orchestrator, "verify_prelaunch_receipt", source_check)
    monkeypatch.setattr(orchestrator, "run_session_worker", session_worker)
    monkeypatch.setattr(orchestrator, "publish_completed_package", package_publisher)
    clock = Clock()
    result = run_orchestration(
        registration_path=registration_path,
        prelaunch_path=prelaunch_path,
        result_root=tmp_path / "result",
        repo_root=repo,
        expected_head=head,
        game_root=game_root,
        python_executable=tmp_path / "unused-python",
        services_evidence=services_evidence,
        store_factory=lambda: store,
        game_attestor=game_attestor,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    assert result["game_attestation"] == game
    assert game_calls == 2
    assert topology_calls == 2
    assert source_checks == 2
    assert package_calls == 1
    assert len(state_dirs) == 18
    assert len({path.name for path in state_dirs}) == 18
    assert all(not path.exists() for path in state_dirs)
    assert store.operations[-1] == ("close", "")


def test_trial_three_interrupt_preserves_two_receipts_and_cleans_owned_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, registration_path, head = _ready_repo(tmp_path)
    registration = json.loads(registration_path.read_text())
    prelaunch = build_prelaunch_payload(
        registration_path,
        repo_root=repo,
        expected_head=head,
        run_id="local001",
        lane=EXPECTED_LANE,
    )
    prelaunch_path = tmp_path / "prelaunch-source.json"
    _write_canonical(prelaunch_path, prelaunch)
    game_root = tmp_path / "game"
    game_root.mkdir()
    live = registration["live_contract"]
    game = {
        "git_head": live["game_revision"],
        "worktree_clean": True,
        "bundle_path": "packages/server/dist/main.js",
        "bundle_size_bytes": 1234,
        "bundle_sha256": live["game_bundle_sha256"],
        "client_dist_file_count": 25,
        "client_dist_inventory_sha256": live["client_dist_inventory_sha256"],
    }
    python = {
        key: live[key]
        for key in (
            "python_version",
            "python_executable_sha256",
            "mcp_version",
            "playwright_version",
            "pymongo_version",
        )
    }
    executions_by_username = {
        plan["username"]: _execution_from_valid_receipt(
            _unsigned_receipt(registration, prelaunch, plan)
        )
        for plan in prelaunch["trials"]
    }
    store = FakeStore()
    state_dirs: list[Path] = []

    def session_worker(spec, _registration_path, **kwargs):
        state_dir = kwargs["state_dir"]
        state_dir.mkdir(parents=True)
        state_dirs.append(state_dir)
        if spec.trial_id == prelaunch["trials"][2]["trial_id"]:
            raise KeyboardInterrupt("injected operator interrupt")
        execution = executions_by_username[spec.username]
        return copy.deepcopy(
            execution.treatment if spec.phase == "treatment" else execution.reconnect
        )

    monkeypatch.setattr(orchestrator, "attest_python_runtime", lambda *_a, **_k: python)
    monkeypatch.setattr(orchestrator, "run_session_worker", session_worker)
    clock = Clock()
    result_root = tmp_path / "interrupted-result"
    with pytest.raises(KeyboardInterrupt, match="operator interrupt"):
        run_orchestration(
            registration_path=registration_path,
            prelaunch_path=prelaunch_path,
            result_root=result_root,
            repo_root=repo,
            expected_head=head,
            game_root=game_root,
            python_executable=tmp_path / "unused-python",
            services_evidence=_services_evidence(registration["live_contract"]),
            store_factory=lambda: store,
            game_attestor=lambda *_args: copy.deepcopy(game),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
    assert (result_root / "receipts/trial-01.json").is_file()
    assert (result_root / "receipts/trial-02.json").is_file()
    assert not (result_root / "receipts/trial-03.json").exists()
    assert (result_root / "failure.json").is_file()
    assert not (result_root / "manifest.json").exists()
    assert not (result_root / "analysis.json").exists()
    cleanup_usernames = [
        username for operation, username in store.operations if operation == "cleanup"
    ]
    assert cleanup_usernames == [plan["username"] for plan in prelaunch["trials"][:3]]
    assert all(not state_dir.exists() for state_dir in state_dirs)
    assert store.operations[-1] == ("close", "")


def test_cleanup_failure_is_attached_without_overwriting_worker_error() -> None:
    class CleanupFailureStore(FakeStore):
        def cleanup_owned(self, username: str, trial_id: str, inserted_ids):
            self.operations.append(("cleanup", username))
            raise OrchestrationError("injected cleanup failure")

    store = CleanupFailureStore()

    def worker(_spec):
        raise RuntimeError("injected worker failure")

    with pytest.raises(RuntimeError, match="worker failure") as raised:
        run_exact_trial_sequence(
            _plans(),
            _registration(),
            store=store,
            worker_runner=worker,
            global_absence={"database": "kaetram_e2e", "all_absent": True},
        )
    assert getattr(raised.value, "cleanup_failure") == {
        "error_type": "OrchestrationError",
        "message": "injected cleanup failure",
        "cleanup_receipt": None,
    }
    assert "ownership cleanup also failed" in raised.value.__notes__[0]
    assert store.operations[:2] == [
        ("seed", _plans()[0]["username"]),
        ("cleanup", _plans()[0]["username"]),
    ]


def test_partial_seed_error_exposes_ids_and_triggers_cleanup() -> None:
    class PartialStore(FakeStore):
        def insert_canonical(self, username: str, trial_id: str):
            receipt = super().insert_canonical(username, trial_id)
            receipt["inserted_ids"] = {
                key: receipt["inserted_ids"][key]
                for key in EXPECTED_INSERTION_ORDER[:2]
            }
            receipt["insertion_order"] = list(EXPECTED_INSERTION_ORDER[:2])
            receipt["player_info_inserted_last"] = False
            raise PartialSeedError("injected partial seed", receipt)

    store = PartialStore()
    with pytest.raises(PartialSeedError, match="partial seed"):
        run_exact_trial_sequence(
            _plans(),
            _registration(),
            store=store,
            worker_runner=lambda _spec: pytest.fail("worker must not start"),
            global_absence={"database": "kaetram_e2e", "all_absent": True},
        )
    assert store.operations == [
        ("seed", _plans()[0]["username"]),
        ("cleanup", _plans()[0]["username"]),
    ]


def test_run_orchestration_publishes_nonfinal_failure_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, registration_path, head = _ready_repo(tmp_path)
    registration = json.loads(registration_path.read_text())
    prelaunch = build_prelaunch_payload(
        registration_path,
        repo_root=repo,
        expected_head=head,
        run_id="local001",
        lane=EXPECTED_LANE,
    )
    prelaunch_path = tmp_path / "prelaunch-source.json"
    _write_canonical(prelaunch_path, prelaunch)
    game_root = tmp_path / "game"
    game_root.mkdir()
    live = registration["live_contract"]
    game = {
        "client_dist_inventory_sha256": live["client_dist_inventory_sha256"]
    }
    python = {
        key: live[key]
        for key in (
            "python_version",
            "python_executable_sha256",
            "mcp_version",
            "playwright_version",
            "pymongo_version",
        )
    }

    class BrokenStore(FakeStore):
        def attest_topology(self):
            raise OrchestrationError("topology attestation failed")

    store = BrokenStore()
    monkeypatch.setattr(orchestrator, "attest_python_runtime", lambda *_a, **_k: python)
    result_root = tmp_path / "failed-result"
    with pytest.raises(OrchestrationError, match="topology attestation failed"):
        run_orchestration(
            registration_path=registration_path,
            prelaunch_path=prelaunch_path,
            result_root=result_root,
            repo_root=repo,
            expected_head=head,
            game_root=game_root,
            python_executable=tmp_path / "unused-python",
            services_evidence=_services_evidence(registration["live_contract"]),
            store_factory=lambda: store,
            game_attestor=lambda *_args: game,
        )
    failure_path = result_root / "failure.json"
    failure = json.loads(failure_path.read_text())
    unsigned = {key: value for key, value in failure.items() if key != "payload_sha256"}
    assert failure["stage"] == "mongo_preflight"
    assert failure["status"] == "incomplete_not_scientifically_reportable"
    assert failure["payload_sha256"] == canonical_sha256(unsigned)
    assert stat.S_IMODE(failure_path.stat().st_mode) == 0o444
    assert store.operations[-1] == ("close", "")
