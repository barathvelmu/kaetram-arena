from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import subprocess
import sys
import types
from pathlib import Path

import pytest

from scripts.opd.live_routing_launcher import (
    LOCK_COLLECTION,
    MONGO_COLLECTIONS,
    CreateOnlyCanonicalStore,
    LaneConfig,
    LauncherError,
    PartialSeedError,
    SessionSpec,
    _client_dist_inventory,
    _descendant_process_groups,
    _diagnostic_browser_process_groups,
    _direct_child_process_groups,
    _parse_tool_json,
    _suspend_owned_process_group,
    _terminate_owned_process_group,
    attest_game_checkout,
    canonical_documents,
    run_session_worker,
    session_worker,
    sanitized_worker_environment,
    validate_runtime_attestation,
    validate_runtime_attestation_set,
)


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_game_checkout_attestation_binds_clean_commit_and_bundle(tmp_path: Path) -> None:
    root = tmp_path / "game"
    bundle = root / "packages/server/dist/main.js"
    client_file = root / "packages/client/dist/index.html"
    bundle.parent.mkdir(parents=True)
    client_file.parent.mkdir(parents=True)
    bundle.write_bytes(b"reviewed game bundle\n")
    client_file.write_bytes(b"reviewed client bundle\n")
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Game Test")
    _git(root, "config", "user.email", "game@example.invalid")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "game")
    head = _git(root, "rev-parse", "HEAD")
    import hashlib

    registration = {
        "live_contract": {
            "game_revision": head,
            "game_bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
            "client_dist_inventory_sha256": _client_dist_inventory(
                client_file.parent
            )["inventory_sha256"],
        }
    }
    receipt = attest_game_checkout(root, registration)
    assert receipt["git_head"] == head
    assert receipt["worktree_clean"] is True
    assert receipt["client_dist_file_count"] == 1
    bundle.write_bytes(b"drift\n")
    with pytest.raises(LauncherError, match="not completely clean"):
        attest_game_checkout(root, registration)


def test_game_checkout_attestation_detects_ignored_client_mutation_and_symlink(
    tmp_path: Path,
) -> None:
    root = tmp_path / "game"
    bundle = root / "packages/server/dist/main.js"
    client_dist = root / "packages/client/dist"
    bundle.parent.mkdir(parents=True)
    client_dist.mkdir(parents=True)
    bundle.write_bytes(b"reviewed game bundle\n")
    (client_dist / "index.html").write_bytes(b"reviewed client bundle\n")
    (root / ".gitignore").write_text("/packages/client/dist/\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Game Test")
    _git(root, "config", "user.email", "game@example.invalid")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "game")
    registration = {
        "live_contract": {
            "game_revision": _git(root, "rev-parse", "HEAD"),
            "game_bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
            "client_dist_inventory_sha256": _client_dist_inventory(client_dist)[
                "inventory_sha256"
            ],
        }
    }
    attest_game_checkout(root, registration)
    (client_dist / "index.html").write_bytes(b"mutated client bundle\n")
    with pytest.raises(LauncherError, match="client dist inventory digest drift"):
        attest_game_checkout(root, registration)
    (client_dist / "index.html").write_bytes(b"reviewed client bundle\n")
    (client_dist / "escape").symlink_to(bundle)
    with pytest.raises(LauncherError, match="client dist contains a symlink"):
        attest_game_checkout(root, registration)


def test_worker_environment_is_local_minimal_and_credential_free(tmp_path: Path) -> None:
    spec = SessionSpec(
        trial_id="trial-0001",
        session_id="llrd-local001-t01-treatment",
        phase="treatment",
        username="lr_local001_01",
        arm="structured_direct",
    )
    environment = sanitized_worker_environment(
        {
            "HOME": "/tmp/home",
            "PATH": "/usr/bin",
            "OPENAI_API_KEY": "must-not-survive",
            "KAETRAM_QWEN_ENDPOINT": "https://paid.invalid",
        },
        spec,
        lane=LaneConfig(),
        state_dir=tmp_path / "state",
    )
    assert environment["KAETRAM_CLIENT_URL"] == "http://127.0.0.1:9000"
    assert environment["KAETRAM_PORT"] == "9191"
    assert environment["KAETRAM_MONGO_DB"] == "kaetram_e2e"
    assert environment["KAETRAM_REQUIRE_EXISTING_ACCOUNT"] == "1"
    assert environment["KAETRAM_DISABLE_HEARTBEATS"] == "1"
    assert environment["KAETRAM_SERVICE_READINESS_TIMEOUT_SECONDS"] == "60"
    assert environment["KAETRAM_LOGIN_TIMEOUT_SECONDS"] == "60"
    assert "OPENAI_API_KEY" not in environment
    assert "KAETRAM_QWEN_ENDPOINT" not in environment


class _ToolResult:
    def __init__(self, text: str, *, is_error: bool = False):
        self.text = text
        self.is_error = is_error


def test_tool_result_parser_is_strict_and_name_bound() -> None:
    assert _parse_tool_json(
        _ToolResult('observe: {"pos":{"x":1,"y":2}}\n\nASCII_MAP:\nignored'),
        expected_name="observe",
    ) == {"pos": {"x": 1, "y": 2}}
    assert _parse_tool_json(
        _ToolResult('{"a":1,"a":2}'), expected_name="observe"
    ) is None
    assert _parse_tool_json(
        _ToolResult('{"a":NaN}'), expected_name="observe"
    ) is None
    assert _parse_tool_json(
        _ToolResult('{"ok":true}', is_error=True), expected_name="observe"
    ) is None


def _session_spec() -> SessionSpec:
    return SessionSpec(
        trial_id="trial-0001",
        session_id="llrd-local001-t01-treatment",
        phase="treatment",
        username="lr_local001_01",
        arm="structured_direct",
    )


def _runtime_attestation() -> dict:
    return {
        "schema_version": "kaetram.diagnostic-runtime-attestation.v1",
        "session_id": "llrd-local001-t01-treatment",
        "mcp_pid": 12346,
        "mcp_process_group": 12346,
        "mcp_instance_nonce": "1" * 32,
        "browser_pid": 12347,
        "browser_process_group": 12347,
        "browser_launch_nonce": "2" * 32,
        "browser_nonce_echo": "2" * 32,
        "browser_name": "chromium",
        "browser_version": "123.0",
        "browser_executable_sha256": "3" * 64,
        "page_url": "http://127.0.0.1:9000/",
        "player_username": "lr_local001_01",
        "configured_client_url": "http://127.0.0.1:9000",
        "configured_game_port": "9191",
        "require_existing_account": True,
        "heartbeats_disabled": True,
        "loopback_only": True,
    }


def test_runtime_attestation_binds_exact_cold_session_and_lane() -> None:
    validate_runtime_attestation(
        _runtime_attestation(),
        _session_spec(),
        worker_pid=12344,
        worker_process_group=12344,
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("session_id", "llrd-local001-t02-treatment", "session identity"),
        ("mcp_pid", True, "MCP process identity"),
        ("mcp_process_group", 99999, "MCP process identity"),
        ("mcp_instance_nonce", "not-a-nonce", "nonce identity"),
        ("browser_nonce_echo", "4" * 32, "nonce echo"),
        ("browser_executable_sha256", None, "browser identity"),
        ("page_url", "https://example.invalid/", "loopback lane"),
        ("configured_game_port", "9001", "lane or player identity"),
        ("loopback_only", False, "lane or player identity"),
    ],
)
def test_runtime_attestation_rejects_identity_or_lane_drift(
    field: str, value, message: str
) -> None:
    attestation = _runtime_attestation()
    attestation[field] = value
    with pytest.raises(LauncherError, match=message):
        validate_runtime_attestation(
            attestation,
            _session_spec(),
            worker_pid=12344,
            worker_process_group=12344,
        )


def test_runtime_attestation_rejects_worker_outside_own_session() -> None:
    with pytest.raises(LauncherError, match="worker identity"):
        validate_runtime_attestation(
            _runtime_attestation(),
            _session_spec(),
            worker_pid=12344,
            worker_process_group=12345,
        )


def _runtime_attestation_rows() -> list[tuple[SessionSpec, dict]]:
    rows = []
    index = 0
    for trial_index in range(1, 10):
        for phase in ("treatment", "reconnect"):
            index += 1
            spec = SessionSpec(
                trial_id=f"trial-{trial_index:02d}",
                session_id=f"llrd-local001-t{trial_index:02d}-{phase}",
                phase=phase,
                username=f"lr_local001_{trial_index:02d}",
                arm=(
                    "structured_direct"
                    if trial_index % 3 == 1
                    else "content_recovery_on"
                    if trial_index % 3 == 2
                    else "content_recovery_off"
                ),
            )
            attestation = _runtime_attestation()
            attestation.update(
                {
                    "session_id": spec.session_id,
                    "player_username": spec.username,
                    "mcp_pid": 20_000 + index,
                    "mcp_process_group": 20_000 + index,
                    "mcp_instance_nonce": f"{index:032x}",
                    "browser_pid": 30_000 + index,
                    "browser_process_group": 30_000 + index,
                    "browser_launch_nonce": f"{index + 100:032x}",
                    "browser_nonce_echo": f"{index + 100:032x}",
                }
            )
            rows.append((spec, attestation))
    return rows


def test_runtime_attestation_set_proves_all_18_sessions_are_cold() -> None:
    validate_runtime_attestation_set(_runtime_attestation_rows())


def test_runtime_attestation_set_rejects_cross_session_reuse() -> None:
    rows = _runtime_attestation_rows()
    rows[1][1]["browser_launch_nonce"] = rows[0][1]["browser_launch_nonce"]
    rows[1][1]["browser_nonce_echo"] = rows[0][1]["browser_nonce_echo"]
    with pytest.raises(LauncherError, match="cold identity reused"):
        validate_runtime_attestation_set(rows)


def test_runtime_attestation_set_accepts_and_verifies_raw_envelopes() -> None:
    rows = []
    for spec, parsed in _runtime_attestation_rows():
        raw = "__diagnostic_runtime_attestation: " + json.dumps(
            parsed, separators=(",", ":")
        )
        rows.append(
            (
                spec,
                {
                    "raw_text": raw,
                    "raw_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                    "parsed": parsed,
                },
            )
        )
    validate_runtime_attestation_set(rows)


def _observe_payload(*, canonical: bool = True) -> dict:
    return {
        "pos": {"x": 328 if canonical else 329, "y": 892},
        "stats": {"hp": 69, "max_hp": 69, "level": 1, "xp": 0},
        "equipment": {},
        "skills": {},
        "inventory": [
            {"slot": 0, "key": "bronzeaxe", "count": 1},
            {"slot": 1, "key": "knife", "count": 1},
            {"slot": 2, "key": "fishingpole", "count": 1},
            {"slot": 3, "key": "coppersword", "count": 1},
            {"slot": 4, "key": "woodenbow", "count": 1},
        ],
        "active_quests": [],
        "finished_quests": [{"name": "Miner's Quest"}],
        "is_dead": False,
        "indoors": False,
    }


class _FakeMcpHandle:
    def __init__(
        self,
        *,
        attestation_raw: str,
        canonical_precondition: bool = True,
        candidate_text: str = '{"warping":true,"warp_id":0}',
        candidate_exception: Exception | None = None,
    ) -> None:
        self.attestation_raw = attestation_raw
        self.canonical_precondition = canonical_precondition
        self.candidate_text = candidate_text
        self.candidate_exception = candidate_exception
        self.calls: list[tuple[str, dict]] = []
        self.observe_count = 0

    async def call_tool(self, name: str, arguments: dict) -> _ToolResult:
        self.calls.append((name, arguments))
        if name == "__diagnostic_runtime_attestation":
            return _ToolResult(self.attestation_raw)
        if name == "observe":
            self.observe_count += 1
            payload = _observe_payload(
                canonical=self.canonical_precondition or self.observe_count > 1
            )
            return _ToolResult("observe: " + json.dumps(payload, separators=(",", ":")))
        if self.candidate_exception is not None:
            raise self.candidate_exception
        return _ToolResult(self.candidate_text)


class _FakeMcpContext:
    def __init__(self, handle: _FakeMcpHandle) -> None:
        self.handle = handle

    async def __aenter__(self) -> _FakeMcpHandle:
        return self.handle

    async def __aexit__(self, *_args) -> None:
        return None


def _run_fake_worker(
    tmp_path: Path,
    monkeypatch,
    *,
    arm: str = "structured_direct",
    phase: str = "treatment",
    canonical_precondition: bool = True,
    candidate_text: str = '{"warping":true,"warp_id":0}',
    candidate_exception: Exception | None = None,
) -> tuple[dict, _FakeMcpHandle, str]:
    session_id = f"llrd-local001-t01-{phase}"
    spec = SessionSpec(
        trial_id="trial-0001",
        session_id=session_id,
        phase=phase,
        username="lr_local001_01",
        arm=arm,
    )
    attestation = _runtime_attestation()
    fake_mcp_pid = os.getpid() + 100_000
    fake_browser_pid = fake_mcp_pid + 1
    attestation.update(
        {
            "session_id": session_id,
            "mcp_pid": fake_mcp_pid,
            "mcp_process_group": fake_mcp_pid,
            "browser_pid": fake_browser_pid,
            "browser_process_group": fake_browser_pid,
        }
    )
    attestation_raw = "__diagnostic_runtime_attestation: " + json.dumps(
        attestation, separators=(",", ":")
    )
    handle = _FakeMcpHandle(
        attestation_raw=attestation_raw,
        canonical_precondition=canonical_precondition,
        candidate_text=candidate_text,
        candidate_exception=candidate_exception,
    )

    def factory(**_kwargs) -> _FakeMcpContext:
        return _FakeMcpContext(handle)

    async def no_sleep(_seconds: float) -> None:
        return None

    registration = {
        "arms": [{"arm": arm}],
        "candidate": {
            "name": "warp",
            "arguments": {"location": "mudwich"},
            "content_envelope": (
                "<tool_call><function=warp><parameter=location>mudwich"
                "</parameter></function></tool_call>"
            ),
        },
        "runtime_parameters": {"minimum_delayed_observation_seconds": 0},
    }
    worker_pid = os.getpid()
    monkeypatch.setattr(os, "getpgrp", lambda: worker_pid)
    monkeypatch.setenv("KAETRAM_STATE_DIR", str(tmp_path / "state"))
    phase_record = asyncio.run(
        session_worker(
            spec,
            registration,
            mcp_session_factory=factory,
            sleep=no_sleep,
        )
    )
    return phase_record, handle, attestation_raw


@pytest.mark.parametrize("arm", ["structured_direct", "content_recovery_on"])
def test_worker_dispatches_valid_candidate_exactly_once(
    tmp_path: Path, monkeypatch, arm: str
) -> None:
    phase, handle, _ = _run_fake_worker(tmp_path, monkeypatch, arm=arm)
    assert [call for call in handle.calls if call[0] == "warp"] == [
        ("warp", {"location": "mudwich"})
    ]
    assert phase["routing"]["candidate_invocation_count"] == 1
    assert phase["routing"]["delivery_status"] == "confirmed"
    assert phase["candidate_call_ledger"] == [
        {
            "sequence": 1,
            "name": "warp",
            "arguments": {"location": "mudwich"},
            "delivery_status": "confirmed",
            "protocol_success": True,
            "result_raw_sha256": hashlib.sha256(
                b'{"warping":true,"warp_id":0}'
            ).hexdigest(),
        }
    ]


def test_worker_recovery_off_makes_zero_candidate_calls(
    tmp_path: Path, monkeypatch
) -> None:
    phase, handle, _ = _run_fake_worker(
        tmp_path, monkeypatch, arm="content_recovery_off"
    )
    assert not [call for call in handle.calls if call[0] == "warp"]
    assert phase["routing"]["candidate_invocation_count"] == 0
    assert phase["candidate_call_ledger"] == []


def test_worker_precondition_mismatch_makes_zero_candidate_calls(
    tmp_path: Path, monkeypatch
) -> None:
    phase, handle, _ = _run_fake_worker(
        tmp_path, monkeypatch, canonical_precondition=False
    )
    assert not [call for call in handle.calls if call[0] == "warp"]
    assert phase["routing"]["dispatch_attempted"] is False
    assert phase["candidate_call_ledger"] == []


def test_worker_transport_exception_is_unknown_and_never_retried(
    tmp_path: Path, monkeypatch
) -> None:
    phase, handle, _ = _run_fake_worker(
        tmp_path, monkeypatch, candidate_exception=RuntimeError("transport lost")
    )
    assert len([call for call in handle.calls if call[0] == "warp"]) == 1
    assert phase["routing"]["delivery_status"] == "unknown_after_exception"
    assert phase["routing"]["protocol_success"] is None
    assert phase["candidate_call_ledger"] == [
        {
            "sequence": 1,
            "name": "warp",
            "arguments": {"location": "mudwich"},
            "delivery_status": "unknown_after_exception",
            "protocol_success": None,
            "result_raw_sha256": None,
        }
    ]


@pytest.mark.parametrize(
    "candidate_text",
    [
        '{"warping":true,"warp_id":0,"warp_id":1}',
        '{"warping":true,"warp_id":NaN}',
    ],
)
def test_worker_rejects_duplicate_or_nonfinite_candidate_result(
    tmp_path: Path, monkeypatch, candidate_text: str
) -> None:
    phase, handle, _ = _run_fake_worker(
        tmp_path, monkeypatch, candidate_text=candidate_text
    )
    assert len([call for call in handle.calls if call[0] == "warp"]) == 1
    assert phase["routing"]["result_json"] is None
    assert phase["routing"]["result_raw_text"] == candidate_text


def test_worker_reconnect_is_observe_only(tmp_path: Path, monkeypatch) -> None:
    phase, handle, _ = _run_fake_worker(
        tmp_path, monkeypatch, phase="reconnect"
    )
    assert [name for name, _ in handle.calls] == [
        "__diagnostic_runtime_attestation",
        "observe",
    ]
    assert phase["routing"] is None
    assert phase["reconnect"]["available"] is True
    assert phase["candidate_call_ledger"] == []


def test_worker_retains_exact_raw_runtime_attestation(
    tmp_path: Path, monkeypatch
) -> None:
    phase, _, raw = _run_fake_worker(tmp_path, monkeypatch)
    evidence = phase["runtime_attestation"]
    assert set(evidence) == {"raw_text", "raw_sha256", "parsed"}
    assert evidence["raw_text"] == raw
    assert evidence["raw_sha256"] == hashlib.sha256(raw.encode()).hexdigest()


class _WorkerProcess:
    pid = 4321

    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode

    def communicate(self, timeout=None):
        return self.stdout, self.stderr


def _write_mcp_owner(state_dir: Path, attestation: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "kaetram.diagnostic-mcp-owner.v1",
        "session_id": attestation["session_id"],
        "mcp_pid": attestation["mcp_pid"],
        "mcp_process_group": attestation["mcp_process_group"],
        "mcp_instance_nonce": attestation["mcp_instance_nonce"],
    }
    path = state_dir / "diagnostic-mcp-owner.json"
    path.write_text(
        json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n"
    )
    path.chmod(0o600)


def _write_browser_owner(state_dir: Path, attestation: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "kaetram.diagnostic-browser-owner.v1",
        "session_id": attestation["session_id"],
        "mcp_pid": attestation["mcp_pid"],
        "mcp_process_group": attestation["mcp_process_group"],
        "mcp_instance_nonce": attestation["mcp_instance_nonce"],
        "browser_pid": attestation["browser_pid"],
        "browser_process_group": attestation["browser_process_group"],
        "browser_launch_nonce": attestation["browser_launch_nonce"],
        "browser_executable_sha256": attestation["browser_executable_sha256"],
    }
    path = state_dir / "diagnostic-browser-owner.json"
    path.write_text(
        json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n"
    )
    path.chmod(0o600)


def _mock_no_live_groups(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._suspend_exact_process_group",
        lambda group, **kwargs: False,
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._diagnostic_browser_process_groups",
        lambda session_id: ([], True),
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._descendant_process_groups",
        lambda roots: (set(), True),
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._process_group_exists", lambda group: False
    )


def test_worker_preserves_virtual_environment_entrypoint(
    tmp_path: Path, monkeypatch
) -> None:
    base = tmp_path / "runtime" / "python3.12"
    base.parent.mkdir()
    base.write_bytes(b"base interpreter")
    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(base)
    calls = []
    process = _WorkerProcess('{"ok":true}')

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._terminate_owned_process_group",
        lambda owned: {
            "found_alive": False,
            "sigkill_required": False,
            "still_alive": False,
        },
    )

    _mock_no_live_groups(monkeypatch)
    with pytest.raises(LauncherError, match="omitted runtime attestation"):
        run_session_worker(
            _session_spec(),
            tmp_path / "registration.json",
            python_executable=venv_python,
            state_dir=tmp_path / "state",
            timeout_seconds=1,
        )
    assert calls[0][0][0] == str(venv_python.absolute())
    assert calls[0][0][0] != str(venv_python.resolve())


def test_success_proves_attested_detached_mcp_group_is_gone(
    tmp_path: Path, monkeypatch
) -> None:
    state_dir = tmp_path / "state"
    attestation = _runtime_attestation()
    _write_mcp_owner(state_dir, attestation)
    _write_browser_owner(state_dir, attestation)
    _mock_no_live_groups(monkeypatch)
    phase = {"runtime_attestation": {"parsed": attestation}}
    process = _WorkerProcess(json.dumps(phase))
    cleanup_calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._terminate_owned_process_group",
        lambda owned: cleanup_calls.append(owned.pid)
        or {"found_alive": False, "sigkill_required": False, "still_alive": False},
    )
    result = run_session_worker(
        _session_spec(),
        tmp_path / "registration.json",
        python_executable=Path("/usr/bin/python3"),
        state_dir=state_dir,
        timeout_seconds=1,
    )
    assert result["runtime_attestation"] == phase["runtime_attestation"]
    assert result["process_lifecycle"]["closure_proven"] is True
    assert cleanup_calls == [
        attestation["browser_process_group"],
        attestation["mcp_process_group"],
        4321,
    ]


def test_exited_failure_still_reaps_mcp_group_from_owner_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    state_dir = tmp_path / "state"
    attestation = _runtime_attestation()
    _write_mcp_owner(state_dir, attestation)
    _write_browser_owner(state_dir, attestation)
    _mock_no_live_groups(monkeypatch)
    process = _WorkerProcess("", stderr="failed", returncode=2)
    cleanup_calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._terminate_owned_process_group",
        lambda owned: cleanup_calls.append(owned.pid)
        or {"found_alive": False, "sigkill_required": False, "still_alive": False},
    )
    with pytest.raises(LauncherError, match="cold session worker failed"):
        run_session_worker(
            _session_spec(),
            tmp_path / "registration.json",
            python_executable=Path("/usr/bin/python3"),
            state_dir=state_dir,
            timeout_seconds=1,
        )
    assert cleanup_calls == [
        attestation["browser_process_group"],
        attestation["mcp_process_group"],
        4321,
    ]


def test_malformed_returned_attestation_cannot_skip_any_cleanup(
    tmp_path: Path, monkeypatch
) -> None:
    state_dir = tmp_path / "state"
    attestation = _runtime_attestation()
    _write_mcp_owner(state_dir, attestation)
    _write_browser_owner(state_dir, attestation)
    _mock_no_live_groups(monkeypatch)
    process = _WorkerProcess(json.dumps({"runtime_attestation": {"parsed": {}}}))
    cleanup_calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._terminate_owned_process_group",
        lambda owned: cleanup_calls.append(owned.pid)
        or {"found_alive": False, "sigkill_required": False, "still_alive": False},
    )
    with pytest.raises(LauncherError, match="key set drift"):
        run_session_worker(
            _session_spec(),
            tmp_path / "registration.json",
            python_executable=Path("/usr/bin/python3"),
            state_dir=state_dir,
            timeout_seconds=1,
        )
    assert cleanup_calls == [
        attestation["browser_process_group"],
        attestation["mcp_process_group"],
        4321,
    ]


def test_success_refuses_mcp_group_that_required_forced_cleanup(
    tmp_path: Path, monkeypatch
) -> None:
    state_dir = tmp_path / "state"
    attestation = _runtime_attestation()
    _write_mcp_owner(state_dir, attestation)
    _write_browser_owner(state_dir, attestation)
    _mock_no_live_groups(monkeypatch)
    phase = {"runtime_attestation": {"parsed": attestation}}
    process = _WorkerProcess(json.dumps(phase))
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._process_group_exists", lambda group: False
    )

    def cleanup(owned):
        return {
            "found_alive": owned.pid == attestation["mcp_process_group"],
            "sigkill_required": False,
            "still_alive": False,
        }

    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._terminate_owned_process_group", cleanup
    )
    with pytest.raises(LauncherError, match="MCP process group survived"):
        run_session_worker(
            _session_spec(),
            tmp_path / "registration.json",
            python_executable=Path("/usr/bin/python3"),
            state_dir=state_dir,
            timeout_seconds=1,
        )


@pytest.mark.parametrize(
    ("process", "exception"),
    [
        (_WorkerProcess("not-json"), LauncherError),
        (_WorkerProcess('{"ok":true}', stderr="failed", returncode=2), LauncherError),
    ],
)
def test_worker_failure_paths_always_check_owned_process_group(
    tmp_path: Path, monkeypatch, process: _WorkerProcess, exception: type[BaseException]
) -> None:
    cleanup_calls = []
    _mock_no_live_groups(monkeypatch)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._terminate_owned_process_group",
        lambda owned: cleanup_calls.append(owned.pid)
        or {"found_alive": False, "sigkill_required": False, "still_alive": False},
    )
    with pytest.raises(exception):
        run_session_worker(
            _session_spec(),
            tmp_path / "registration.json",
            python_executable=Path("/usr/bin/python3"),
            state_dir=tmp_path / "state",
            timeout_seconds=1,
        )
    assert cleanup_calls == [4321]


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (KeyboardInterrupt(), KeyboardInterrupt),
        (subprocess.TimeoutExpired(cmd="worker", timeout=1), LauncherError),
    ],
)
def test_worker_interrupt_or_timeout_always_checks_owned_process_group(
    tmp_path: Path, monkeypatch, raised: BaseException, expected: type[BaseException]
) -> None:
    class _InterruptedProcess:
        pid = 4321
        returncode = None

        @staticmethod
        def communicate(timeout=None):
            raise raised

    cleanup_calls = []
    _mock_no_live_groups(monkeypatch)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: _InterruptedProcess())
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._suspend_owned_process_group",
        lambda process: True,
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._direct_child_process_groups",
        lambda parent_pid: [],
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._terminate_owned_process_group",
        lambda owned: cleanup_calls.append(owned.pid)
        or {"found_alive": True, "sigkill_required": False, "still_alive": False},
    )
    with pytest.raises(expected):
        run_session_worker(
            _session_spec(),
            tmp_path / "registration.json",
            python_executable=Path("/usr/bin/python3"),
            state_dir=tmp_path / "state",
            timeout_seconds=1,
        )
    assert cleanup_calls == [4321]


def test_worker_timeout_terminates_discovered_detached_child_group(
    tmp_path: Path, monkeypatch
) -> None:
    class _TimedOutProcess:
        pid = 4321
        returncode = None

        @staticmethod
        def communicate(timeout=None):
            raise subprocess.TimeoutExpired(cmd="worker", timeout=timeout)

    cleanup_calls = []
    _mock_no_live_groups(monkeypatch)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: _TimedOutProcess())
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._suspend_owned_process_group",
        lambda process: True,
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._direct_child_process_groups",
        lambda parent_pid: [5001],
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._terminate_owned_process_group",
        lambda owned: cleanup_calls.append(owned.pid)
        or {"found_alive": True, "sigkill_required": False, "still_alive": False},
    )

    with pytest.raises(LauncherError, match="exceeded registered timeout"):
        run_session_worker(
            _session_spec(),
            tmp_path / "registration.json",
            python_executable=Path("/usr/bin/python3"),
            state_dir=tmp_path / "state",
            timeout_seconds=1,
        )
    assert cleanup_calls == [5001, 4321]


def test_worker_cleanup_still_runs_when_child_discovery_fails(
    tmp_path: Path, monkeypatch
) -> None:
    class _TimedOutProcess:
        pid = 4321
        returncode = None

        @staticmethod
        def communicate(timeout=None):
            raise subprocess.TimeoutExpired(cmd="worker", timeout=timeout)

    cleanup_calls = []
    _mock_no_live_groups(monkeypatch)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: _TimedOutProcess())
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._suspend_owned_process_group",
        lambda process: True,
    )

    def fail_discovery(parent_pid):
        raise LauncherError("discovery failed")

    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._direct_child_process_groups",
        fail_discovery,
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._terminate_owned_process_group",
        lambda owned: cleanup_calls.append(owned.pid)
        or {"found_alive": True, "sigkill_required": False, "still_alive": False},
    )

    with pytest.raises(LauncherError, match="discovery failed") as raised:
        run_session_worker(
            _session_spec(),
            tmp_path / "registration.json",
            python_executable=Path("/usr/bin/python3"),
            state_dir=tmp_path / "state",
            timeout_seconds=1,
        )
    assert isinstance(raised.value.__cause__, LauncherError)
    assert "exceeded registered timeout" in str(raised.value.__cause__)
    assert cleanup_calls == [4321]


def test_worker_cleanup_still_runs_when_detached_cleanup_raises(
    tmp_path: Path, monkeypatch
) -> None:
    class _TimedOutProcess:
        pid = 4321
        returncode = None

        @staticmethod
        def communicate(timeout=None):
            raise subprocess.TimeoutExpired(cmd="worker", timeout=timeout)

    cleanup_calls = []
    _mock_no_live_groups(monkeypatch)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: _TimedOutProcess())
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._suspend_owned_process_group",
        lambda process: True,
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._direct_child_process_groups",
        lambda parent_pid: [5001],
    )

    def cleanup(owned):
        cleanup_calls.append(owned.pid)
        if owned.pid == 5001:
            raise LauncherError("detached cleanup failed")
        return {"found_alive": True, "sigkill_required": False, "still_alive": False}

    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._terminate_owned_process_group", cleanup
    )
    with pytest.raises(LauncherError, match="detached cleanup failed"):
        run_session_worker(
            _session_spec(),
            tmp_path / "registration.json",
            python_executable=Path("/usr/bin/python3"),
            state_dir=tmp_path / "state",
            timeout_seconds=1,
        )
    assert cleanup_calls == [5001, 4321]


def test_direct_child_group_discovery_is_parent_exact(monkeypatch) -> None:
    completed = types.SimpleNamespace(
        stdout="10 1 10\n20 4321 20\n21 9999 20\n30 9999 30\n"
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)
    assert _direct_child_process_groups(4321) == [20]


def test_direct_child_group_discovery_rejects_foreign_group(monkeypatch) -> None:
    completed = types.SimpleNamespace(stdout="21 4321 20\n")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)
    with pytest.raises(LauncherError, match="identity is unsafe"):
        _direct_child_process_groups(4321)


def test_browser_discovery_preserves_group_when_leader_is_missing(monkeypatch) -> None:
    session_id = "llrd-local001-t01-treatment"
    completed = types.SimpleNamespace(
        stdout=(
            f"7002 7001 /browser-helper --kaetram-diagnostic-session={session_id}\n"
        )
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)
    assert _diagnostic_browser_process_groups(session_id) == ([7001], False)


def test_missing_browser_leader_group_is_still_terminated(
    tmp_path: Path, monkeypatch
) -> None:
    class _TimedOutProcess:
        pid = 4321
        returncode = None

        @staticmethod
        def communicate(timeout=None):
            raise subprocess.TimeoutExpired(cmd="worker", timeout=timeout)

    cleanup_calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: _TimedOutProcess())
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._suspend_owned_process_group",
        lambda process: True,
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._direct_child_process_groups",
        lambda parent_pid: [],
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._diagnostic_browser_process_groups",
        lambda session_id: ([7001], False),
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._descendant_process_groups",
        lambda roots: (set(), True),
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._suspend_exact_process_group",
        lambda group, **kwargs: False,
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._process_group_exists", lambda group: False
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._terminate_owned_process_group",
        lambda owned: cleanup_calls.append(owned.pid)
        or {"found_alive": False, "sigkill_required": False, "still_alive": False},
    )
    with pytest.raises(LauncherError, match="leader is not observable"):
        run_session_worker(
            _session_spec(),
            tmp_path / "registration.json",
            python_executable=Path("/usr/bin/python3"),
            state_dir=tmp_path / "state",
            timeout_seconds=1,
        )
    assert cleanup_calls == [7001, 4321]


def test_descendant_discovery_preserves_group_when_leader_is_missing(
    monkeypatch,
) -> None:
    completed = types.SimpleNamespace(stdout="5001 4001 5001\n5002 5001 7001\n")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)
    assert _descendant_process_groups({5001}) == ({7001}, False)


def test_unexpected_descendant_is_cleaned_and_rejects_success(
    tmp_path: Path, monkeypatch
) -> None:
    state_dir = tmp_path / "state"
    attestation = _runtime_attestation()
    _write_mcp_owner(state_dir, attestation)
    _write_browser_owner(state_dir, attestation)
    phase = {"runtime_attestation": {"parsed": attestation}}
    process = _WorkerProcess(json.dumps(phase))
    cleanup_calls = []
    snapshots = iter([({7001}, True), (set(), True)])
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._diagnostic_browser_process_groups",
        lambda session_id: ([], True),
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._descendant_process_groups",
        lambda roots: next(snapshots),
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._suspend_exact_process_group",
        lambda group, **kwargs: False,
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._process_group_exists", lambda group: False
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._terminate_owned_process_group",
        lambda owned: cleanup_calls.append(owned.pid)
        or {"found_alive": False, "sigkill_required": False, "still_alive": False},
    )
    with pytest.raises(LauncherError, match="unexpected detached descendant"):
        run_session_worker(
            _session_spec(),
            tmp_path / "registration.json",
            python_executable=Path("/usr/bin/python3"),
            state_dir=state_dir,
            timeout_seconds=1,
        )
    assert cleanup_calls == [
        7001,
        attestation["browser_process_group"],
        attestation["mcp_process_group"],
        4321,
    ]


def test_final_post_freeze_descendants_are_also_cleaned(
    tmp_path: Path, monkeypatch
) -> None:
    state_dir = tmp_path / "state"
    attestation = _runtime_attestation()
    _write_mcp_owner(state_dir, attestation)
    _write_browser_owner(state_dir, attestation)
    process = _WorkerProcess(
        json.dumps({"runtime_attestation": {"parsed": attestation}})
    )
    snapshots = iter(
        [
            ({7001}, True),
            ({7002}, True),
            ({7003}, True),
            ({7004}, True),
            ({8001}, True),
            ({9001}, True),
        ]
    )
    cleanup_calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._diagnostic_browser_process_groups",
        lambda session_id: ([], True),
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._descendant_process_groups",
        lambda roots: next(snapshots),
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._suspend_exact_process_group",
        lambda group, **kwargs: False,
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._process_group_exists", lambda group: False
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._terminate_owned_process_group",
        lambda owned: cleanup_calls.append(owned.pid)
        or {"found_alive": False, "sigkill_required": False, "still_alive": False},
    )
    with pytest.raises(LauncherError, match="did not stabilize"):
        run_session_worker(
            _session_spec(),
            tmp_path / "registration.json",
            python_executable=Path("/usr/bin/python3"),
            state_dir=state_dir,
            timeout_seconds=1,
        )
    assert set(range(7001, 7005)) | {8001, 9001} <= set(cleanup_calls)


def test_worker_group_is_frozen_before_child_discovery(monkeypatch) -> None:
    process = types.SimpleNamespace(pid=4321)
    signals = []
    monkeypatch.setattr(os, "getpgid", lambda pid: 4321)
    monkeypatch.setattr(
        os, "killpg", lambda group, sent_signal: signals.append((group, sent_signal))
    )
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._process_group_stop_state",
        lambda group: "stopped",
    )
    assert _suspend_owned_process_group(process) is True
    assert signals == [(4321, signal.SIGSTOP)]


def test_worker_group_freeze_rejects_unowned_group(monkeypatch) -> None:
    process = types.SimpleNamespace(pid=4321)
    monkeypatch.setattr(os, "getpgid", lambda pid: 9999)
    with pytest.raises(LauncherError, match="does not own"):
        _suspend_owned_process_group(process)


def test_surviving_process_group_is_killed_and_refuses_success(
    tmp_path: Path, monkeypatch
) -> None:
    state_dir = tmp_path / "state"
    attestation = _runtime_attestation()
    _write_mcp_owner(state_dir, attestation)
    _write_browser_owner(state_dir, attestation)
    _mock_no_live_groups(monkeypatch)
    process = _WorkerProcess(
        json.dumps({"runtime_attestation": {"parsed": attestation}})
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        "scripts.opd.live_routing_launcher._terminate_owned_process_group",
        lambda owned: {
            "found_alive": True,
            "sigkill_required": True,
            "still_alive": False,
        },
    )
    with pytest.raises(LauncherError, match="survived worker exit"):
        run_session_worker(
            _session_spec(),
            tmp_path / "registration.json",
            python_executable=Path("/usr/bin/python3"),
            state_dir=state_dir,
            timeout_seconds=1,
        )


def test_process_group_teardown_escalates_to_kill(monkeypatch) -> None:
    alive = True
    signals = []

    def killpg(process_group: int, sent_signal: int) -> None:
        nonlocal alive
        if sent_signal == 0:
            if not alive:
                raise ProcessLookupError
            return
        signals.append((process_group, sent_signal))
        if sent_signal == signal.SIGKILL:
            alive = False

    class _Process:
        pid = 9876

        @staticmethod
        def poll():
            return 0

    monkeypatch.setattr("scripts.opd.live_routing_launcher.os.killpg", killpg)
    result = _terminate_owned_process_group(_Process(), grace_seconds=0)
    assert signals == [
        (9876, signal.SIGTERM),
        (9876, signal.SIGCONT),
        (9876, signal.SIGKILL),
    ]
    assert result == {
        "found_alive": True,
        "sigkill_required": True,
        "still_alive": False,
    }


def test_canonical_documents_cover_all_player_collections() -> None:
    documents = canonical_documents("lr_local001_01")
    assert set(documents) == set(MONGO_COLLECTIONS)
    assert documents["player_info"]["x"] == 328
    assert documents["player_info"]["y"] == 892
    assert documents["player_info"]["hitPoints"] == 69
    assert [slot["key"] for slot in documents["player_inventory"]["slots"][:5]] == [
        "bronzeaxe",
        "knife",
        "fishingpole",
        "coppersword",
        "woodenbow",
    ]


class _InsertResult:
    def __init__(self, identifier: str):
        self.inserted_id = identifier


class _DeleteResult:
    def __init__(self, deleted_count: int):
        self.deleted_count = deleted_count


class _Collection:
    def __init__(self, name: str, order: list[str]):
        self.name = name
        self.order = order
        self.documents: list[dict] = []

    def count_documents(self, query: dict, limit: int = 0) -> int:
        return sum(
            all(document.get(key) == value for key, value in query.items())
            for document in self.documents
        )

    def insert_one(self, document: dict) -> _InsertResult:
        if self.name == LOCK_COLLECTION and any(
            row.get("_id") == document.get("_id") for row in self.documents
        ):
            raise RuntimeError("duplicate lock")
        stored = dict(document)
        identifier = stored.setdefault("_id", f"id-{self.name}-{len(self.documents)}")
        self.documents.append(stored)
        self.order.append(self.name)
        return _InsertResult(identifier)

    def find_one(self, query: dict) -> dict | None:
        for document in self.documents:
            if all(document.get(key) == value for key, value in query.items()):
                return dict(document)
        return None

    def delete_one(self, query: dict) -> _DeleteResult:
        for index, document in enumerate(self.documents):
            if all(document.get(key) == value for key, value in query.items()):
                self.documents.pop(index)
                return _DeleteResult(1)
        return _DeleteResult(0)


class _Database:
    def __init__(self):
        self.order: list[str] = []
        self.collections: dict[str, _Collection] = {}

    def __getitem__(self, name: str) -> _Collection:
        return self.collections.setdefault(name, _Collection(name, self.order))


class _Client:
    def __init__(self):
        self.database = _Database()
        self.nodes = {("127.0.0.1", 27017)}
        self.admin = self

    def __getitem__(self, name: str) -> _Database:
        assert name == "kaetram_e2e"
        return self.database

    def close(self) -> None:
        pass

    def command(self, name: str) -> dict:
        assert name == "ping"
        return {"ok": 1}


def test_create_only_store_refuses_reuse_and_inserts_player_info_last() -> None:
    client = _Client()
    store = CreateOnlyCanonicalStore(client_factory=lambda _: client)
    assert store.attest_topology()["loopback_only"] is True
    receipt = store.insert_canonical("lr_local001_01", "trial-0001")
    assert receipt["absence"]["all_absent"] is True
    assert receipt["player_info_inserted_last"] is True
    assert receipt["insertion_order"] == [
        LOCK_COLLECTION,
        *(name for name in MONGO_COLLECTIONS if name != "player_info"),
        "player_info",
    ]
    assert client.database.order[-1] == "player_info"
    assert set(receipt["inserted_ids"]) == {LOCK_COLLECTION, *MONGO_COLLECTIONS}
    with pytest.raises(LauncherError, match="already exists"):
        store.insert_canonical("lr_local001_01", "trial-0001-retry")


def test_partial_seed_receipt_reports_only_completed_insert_order(
    monkeypatch,
) -> None:
    client = _Client()
    store = CreateOnlyCanonicalStore(client_factory=lambda _: client)

    def fail_insert(_document: dict) -> _InsertResult:
        raise RuntimeError("injected write failure")

    monkeypatch.setattr(client.database["player_bank"], "insert_one", fail_insert)
    with pytest.raises(PartialSeedError) as raised:
        store.insert_canonical("lr_local001_01", "trial-0001")
    assert raised.value.receipt["insertion_order"] == [
        LOCK_COLLECTION,
        "player_inventory",
    ]
    assert set(raised.value.receipt["inserted_ids"]) == {
        LOCK_COLLECTION,
        "player_inventory",
    }
    assert raised.value.receipt["player_info_inserted_last"] is False


def test_partial_seed_cleanup_deletes_only_owned_inserted_rows(
    monkeypatch,
) -> None:
    client = _Client()
    store = CreateOnlyCanonicalStore(client_factory=lambda _: client)

    def fail_insert(_document: dict) -> _InsertResult:
        raise RuntimeError("injected write failure")

    monkeypatch.setattr(client.database["player_bank"], "insert_one", fail_insert)
    with pytest.raises(PartialSeedError) as raised:
        store.insert_canonical("lr_local001_01", "trial-0001")
    monkeypatch.setitem(
        sys.modules, "bson", types.SimpleNamespace(ObjectId=lambda value: value)
    )
    cleanup = store.cleanup_owned(
        "lr_local001_01",
        "trial-0001",
        raised.value.receipt["inserted_ids"],
    )
    assert cleanup["deleted"]["player_inventory"] == 1
    assert all(
        cleanup["deleted"][collection] == 0
        for collection in MONGO_COLLECTIONS
        if collection != "player_inventory"
    )
    assert cleanup["lock_deleted"] == 1
    assert cleanup["absence"]["all_absent"] is True
    assert cleanup["complete"] is True


def test_cleanup_attempts_remaining_owned_rows_after_one_delete_fails(
    monkeypatch,
) -> None:
    client = _Client()
    store = CreateOnlyCanonicalStore(client_factory=lambda _: client)
    seed = store.insert_canonical("lr_local001_01", "trial-0001")
    monkeypatch.setitem(
        sys.modules, "bson", types.SimpleNamespace(ObjectId=lambda value: value)
    )

    def fail_delete(_query: dict) -> _DeleteResult:
        raise RuntimeError("injected delete failure")

    monkeypatch.setattr(
        client.database["player_bank"], "delete_one", fail_delete
    )
    with pytest.raises(LauncherError, match="ownership cleanup") as raised:
        store.cleanup_owned(
            "lr_local001_01", "trial-0001", seed["inserted_ids"]
        )
    receipt = raised.value.cleanup_receipt
    assert receipt["deleted"]["player_bank"] == 0
    assert receipt["deleted"]["player_info"] == 1
    assert receipt["lock_deleted"] == 1
    assert receipt["complete"] is False
    assert client.database["player_bank"].documents
    assert client.database["player_info"].documents == []
    assert client.database[LOCK_COLLECTION].documents == []
