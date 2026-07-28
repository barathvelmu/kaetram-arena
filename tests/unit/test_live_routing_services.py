from __future__ import annotations

import hashlib
import json
import platform
import shutil
import signal
import subprocess
from pathlib import Path

import pytest

from scripts.opd.live_routing_launcher import _client_dist_inventory
from scripts.opd.live_routing_analyzer import canonical_sha256
from scripts.opd.live_routing_services import (
    MONGO_IMAGE,
    LiveRoutingServices,
    ServiceCleanupError,
    ServiceConfig,
    ServiceError,
    run_with_local_services,
)


class _Completed:
    def __init__(
        self, stdout: str = "", *, stderr: str = "", returncode: int = 0
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class _FakeProcess:
    def __init__(self, pid: int, label: str) -> None:
        self.pid = pid
        self.label = label
        self.alive = True
        self.reaped = False

    def poll(self):
        if self.alive:
            return None
        self.reaped = True
        return 0

    def wait(self, timeout=None):
        self.alive = False
        self.reaped = True
        return 0


class _FakeRuntime:
    def __init__(self, *, image_cached: bool = True) -> None:
        self.image_cached = image_cached
        self.commands: list[list[str]] = []
        self.process_calls: list[tuple[list[str], dict]] = []
        self.processes: dict[int, _FakeProcess] = {}
        self.open_ports: set[int] = set()
        self.signals: list[tuple[int, int]] = []
        self.next_pid = 4100
        self.container_exists = False
        self.stop_result = "success"
        self.rm_result = "success"
        self.inspect_results: list[str] = []
        self.sticky_ports: set[int] = set()

    def run(self, command, **_kwargs):
        command = [str(item) for item in command]
        self.commands.append(command)
        if command[-1:] == ["--version"] and Path(command[0]).name == "node":
            return _Completed("v20.20.2\n")
        if command[-1:] == ["--version"] and Path(command[0]).name == "docker":
            return _Completed("Docker version 29.2.1, build a5c7197\n")
        if command[1:3] == ["image", "inspect"]:
            if not self.image_cached:
                raise subprocess.CalledProcessError(1, command)
            architecture = "arm64" if platform.machine().lower() in {
                "arm64",
                "aarch64",
            } else "amd64"
            return _Completed(
                json.dumps(
                    {
                        "Id": "sha256:" + "a" * 64,
                        "RepoDigests": [MONGO_IMAGE],
                        "Architecture": architecture,
                        "Os": "linux",
                    }
                )
            )
        if command[1:3] == ["inspect", "--type"]:
            outcome = (
                self.inspect_results.pop(0)
                if self.inspect_results
                else "present" if self.container_exists else "absent"
            )
            if outcome == "exception":
                raise subprocess.TimeoutExpired(command, timeout=0.2)
            if outcome == "ambiguous":
                return _Completed(stderr="Docker daemon unavailable", returncode=2)
            if outcome == "present":
                return _Completed(f"/{command[-1]}\n")
            if outcome == "absent_docker29":
                return _Completed(
                    stderr=f"Error response from daemon: No such container: {command[-1]}",
                    returncode=1,
                )
            return _Completed(
                stderr=f"Error: No such object: {command[-1]}", returncode=1
            )
        if command[1:2] == ["stop"]:
            self.open_ports.discard(27017)
            if self.stop_result == "exception":
                self.container_exists = False
                raise subprocess.TimeoutExpired(command, timeout=0.2)
            if self.stop_result == "nonzero":
                self.container_exists = False
                return _Completed(stderr="stop failed", returncode=1)
            self.container_exists = False
            return _Completed()
        if command[1:2] == ["rm"]:
            self.open_ports.discard(27017)
            if self.rm_result == "exception":
                raise subprocess.TimeoutExpired(command, timeout=0.2)
            if self.rm_result == "nonzero":
                return _Completed(stderr="rm failed", returncode=1)
            self.container_exists = False
            return _Completed()
        return _Completed()

    def popen(self, command, **kwargs):
        command = [str(item) for item in command]
        self.process_calls.append((command, kwargs))
        if command[1:2] == ["run"]:
            label, port = "mongo", 27017
            self.container_exists = True
        elif "http.server" in command:
            label, port = "client", 9000
        else:
            label, port = "game", 9191
        process = _FakeProcess(self.next_pid, label)
        self.processes[process.pid] = process
        self.next_pid += 1
        self.open_ports.add(port)
        return process

    def port_probe(self, _host: str, port: int) -> bool:
        return port in self.open_ports

    def group_exists(self, process_group: int) -> bool:
        process = self.processes.get(process_group)
        if process and not process.alive and not process.reaped:
            raise PermissionError("unreaped process group is ambiguous")
        return bool(process and process.alive)

    def kill_group(self, process_group: int, sent_signal: int) -> None:
        self.signals.append((process_group, sent_signal))
        process = self.processes[process_group]
        process.alive = False
        port = {"mongo": 27017, "client": 9000, "game": 9191}[process.label]
        if port not in self.sticky_ports:
            self.open_ports.discard(port)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path, runtime: _FakeRuntime):
    game_root = tmp_path / "registered-game"
    server = game_root / "packages/server/dist/main.js"
    client = game_root / "packages/client/dist/index.html"
    server.parent.mkdir(parents=True)
    client.parent.mkdir(parents=True)
    server.write_bytes(b"registered server bundle\n")
    client.write_bytes(b"registered client bundle\n")
    binaries = tmp_path / "binaries"
    binaries.mkdir()
    docker = binaries / "docker"
    python = binaries / "python3"
    node = binaries / "node"
    for path, payload in (
        (docker, b"docker executable"),
        (python, b"python executable"),
        (node, b"node 20 executable"),
    ):
        path.write_bytes(payload)
    config = ServiceConfig(
        game_root=game_root,
        game_revision="7" * 40,
        server_bundle_sha256=_sha(server),
        client_dist_inventory_sha256=_client_dist_inventory(client.parent)[
            "inventory_sha256"
        ],
        docker_binary=docker,
        python_binary=python,
        node_version="v20.20.2",
        node_executable_sha256=_sha(node),
        docker_client_version="Docker version 29.2.1, build a5c7197",
        docker_executable_sha256=_sha(docker),
        node_binary=node,
        readiness_timeout_seconds=0.2,
        shutdown_timeout_seconds=0.2,
        poll_interval_seconds=0.001,
    )

    def attest(_root: Path, registration: dict) -> dict:
        contract = registration["live_contract"]
        return {
            "git_head": contract["game_revision"],
            "bundle_sha256": contract["game_bundle_sha256"],
        }

    run_root = tmp_path / "kaetram-live-services-offline001"

    def make_temp(**_kwargs) -> str:
        run_root.mkdir()
        return str(run_root)

    dependencies = {
        "process_factory": runtime.popen,
        "command_runner": runtime.run,
        "port_probe": runtime.port_probe,
        "process_group_lookup": lambda pid: pid,
        "process_group_exists": runtime.group_exists,
        "kill_process_group": runtime.kill_group,
        "checkout_attestor": attest,
        "make_temp": make_temp,
        "remove_tree": shutil.rmtree,
    }
    return config, dependencies, run_root


def test_supervisor_uses_pinned_cached_image_loopback_and_owned_groups(
    tmp_path: Path,
) -> None:
    runtime = _FakeRuntime()
    config, dependencies, run_root = _fixture(tmp_path, runtime)
    with LiveRoutingServices(config, **dependencies) as services:
        evidence = services.evidence
        assert evidence["payload_sha256"] == canonical_sha256(
            {
                key: value
                for key, value in evidence.items()
                if key != "payload_sha256"
            }
        )
        assert runtime.open_ports == {27017, 9000, 9191}
        assert run_root.is_dir()
        assert evidence["mongo_image"] == MONGO_IMAGE
        assert evidence["lane"] == {
            "host": "127.0.0.1",
            "mongo_port": 27017,
            "client_port": 9000,
            "game_port": 9191,
            "mongo_database": "kaetram_e2e",
            "model_calls": 0,
            "remote_endpoints": 0,
        }
        mongo = evidence["services"]["mongo"]
        assert "--pull=never" in mongo["command"]
        assert "127.0.0.1:27017:27017" in mongo["command"]
        assert MONGO_IMAGE in mongo["command"]
        assert mongo["pid"] == mongo["process_group"]
        assert evidence["services"]["client"]["command"][-4:] == [
            "--bind",
            "127.0.0.1",
            "--directory",
            "$GAME_ROOT/packages/client/dist",
        ]
        assert evidence["services"]["game"]["command"][-4:] == [
            "--host",
            "127.0.0.1",
            "--port",
            "9191",
        ]
        assert evidence["identity"]["node_version"] == config.node_version
        assert evidence["identity"]["node_executable_sha256"] == (
            config.node_executable_sha256
        )
        assert evidence["identity"]["docker_client_version"] == (
            config.docker_client_version
        )
        assert evidence["identity"]["docker_executable_sha256"] == (
            config.docker_executable_sha256
        )
        encoded = json.dumps(evidence)
        assert str(tmp_path) not in encoded
        assert "/Users/" not in encoded
    assert runtime.open_ports == set()
    assert not run_root.exists()
    assert (4102, signal.SIGINT) in runtime.signals
    assert (4101, signal.SIGTERM) in runtime.signals
    assert (4100, signal.SIGTERM) in runtime.signals
    assert any(command[1:3] == ["image", "inspect"] for command in runtime.commands)
    assert any("stop" in command for command in runtime.commands)
    assert any("rm" in command for command in runtime.commands)


def test_uncached_image_refuses_without_starting_any_process(tmp_path: Path) -> None:
    runtime = _FakeRuntime(image_cached=False)
    config, dependencies, run_root = _fixture(tmp_path, runtime)
    with pytest.raises(ServiceError, match="not cached locally"):
        with LiveRoutingServices(config, **dependencies):
            raise AssertionError("unreachable")
    assert runtime.process_calls == []
    assert not run_root.exists()


def test_occupied_port_refuses_before_allocating_or_starting(tmp_path: Path) -> None:
    runtime = _FakeRuntime()
    runtime.open_ports.add(9000)
    config, dependencies, run_root = _fixture(tmp_path, runtime)
    with pytest.raises(ServiceError, match="ports are occupied"):
        with LiveRoutingServices(config, **dependencies):
            raise AssertionError("unreachable")
    assert runtime.process_calls == []
    assert not run_root.exists()


def test_callback_failure_still_tears_down_only_owned_resources(tmp_path: Path) -> None:
    runtime = _FakeRuntime()
    config, dependencies, run_root = _fixture(tmp_path, runtime)

    def fail(evidence: dict):
        assert set(evidence["services"]) == {"mongo", "client", "game"}
        raise ValueError("orchestrator failed")

    with pytest.raises(ValueError, match="orchestrator failed"):
        run_with_local_services(config, fail, **dependencies)
    assert runtime.open_ports == set()
    assert not run_root.exists()
    assert {group for group, _ in runtime.signals} == {4100, 4101, 4102}


def test_processes_receive_sanitized_environment_and_new_sessions(tmp_path: Path) -> None:
    runtime = _FakeRuntime()
    config, dependencies, _ = _fixture(tmp_path, runtime)
    with LiveRoutingServices(config, **dependencies):
        pass
    for _, kwargs in runtime.process_calls:
        assert kwargs["start_new_session"] is True
    _, game_kwargs = runtime.process_calls[-1]
    environment = game_kwargs["env"]
    assert environment["MONGODB_DATABASE"] == "kaetram_e2e"
    assert environment["MONGODB_HOST"] == "127.0.0.1"
    assert environment["API_ENABLED"] == "false"
    assert "OPENAI_API_KEY" not in environment


def test_process_that_exits_before_teardown_is_reaped_before_group_probe(
    tmp_path: Path,
) -> None:
    runtime = _FakeRuntime()
    config, dependencies, run_root = _fixture(tmp_path, runtime)

    with LiveRoutingServices(config, **dependencies):
        # Mirrors the docker CLI exiting when its --rm container is stopped.
        runtime.processes[4100].alive = False

    assert runtime.processes[4100].reaped is True
    assert not run_root.exists()


def test_one_teardown_failure_does_not_skip_other_owned_resources(
    tmp_path: Path,
) -> None:
    runtime = _FakeRuntime()
    config, dependencies, run_root = _fixture(tmp_path, runtime)
    normal_kill = runtime.kill_group

    def fail_game_only(process_group: int, sent_signal: int) -> None:
        if process_group == 4102:
            raise PermissionError("injected game teardown failure")
        normal_kill(process_group, sent_signal)

    dependencies["kill_process_group"] = fail_game_only
    with pytest.raises(BaseExceptionGroup):
        with LiveRoutingServices(config, **dependencies):
            pass
    assert not runtime.group_exists(4100)
    assert not runtime.group_exists(4101)
    assert run_root.exists()
    assert any("stop" in command for command in runtime.commands)


def test_keyboard_interrupt_and_teardown_failure_use_base_exception_group(
    tmp_path: Path,
) -> None:
    runtime = _FakeRuntime()
    config, dependencies, _ = _fixture(tmp_path, runtime)

    def fail_all_groups(_process_group: int, _sent_signal: int) -> None:
        raise PermissionError("injected teardown failure")

    dependencies["kill_process_group"] = fail_all_groups
    with pytest.raises(BaseExceptionGroup) as raised:
        with LiveRoutingServices(config, **dependencies):
            raise KeyboardInterrupt()
    assert any(isinstance(item, KeyboardInterrupt) for item in raised.value.exceptions)


def test_stop_and_rm_failures_are_recorded_when_exact_inspect_proves_absence(
    tmp_path: Path,
) -> None:
    runtime = _FakeRuntime()
    runtime.stop_result = "exception"
    runtime.rm_result = "nonzero"
    config, dependencies, run_root = _fixture(tmp_path, runtime)
    services = LiveRoutingServices(config, **dependencies)

    with services:
        pass

    report = services.cleanup_report
    assert report is not None
    assert report["absence_proven"] is True
    assert report["evidence_root_preserved"] is False
    assert [(row["action"], row["outcome"]) for row in report["attempts"]] == [
        ("stop", "exception"),
        ("rm", "nonzero"),
        ("inspect", "absent"),
    ]
    assert not run_root.exists()


def test_container_cleanup_retries_until_exact_inspect_proves_eventual_absence(
    tmp_path: Path,
) -> None:
    runtime = _FakeRuntime()
    runtime.stop_result = "nonzero"
    runtime.rm_result = "nonzero"
    runtime.inspect_results = ["present", "absent"]
    config, dependencies, run_root = _fixture(tmp_path, runtime)
    services = LiveRoutingServices(config, **dependencies)

    with services:
        pass

    report = services.cleanup_report
    assert report is not None and report["absence_proven"] is True
    assert [row["action"] for row in report["attempts"]] == [
        "stop",
        "rm",
        "inspect",
        "rm_retry",
        "inspect",
    ]
    inspect_commands = [
        command for command in runtime.commands if command[1:3] == ["inspect", "--type"]
    ]
    assert inspect_commands
    assert inspect_commands[0][1:6] == [
        "inspect",
        "--type",
        "container",
        "--format",
        "{{.Name}}",
    ]
    assert not run_root.exists()


def test_docker29_no_such_container_response_is_exact_absence_proof(
    tmp_path: Path,
) -> None:
    runtime = _FakeRuntime()
    runtime.inspect_results = ["absent_docker29"]
    config, dependencies, run_root = _fixture(tmp_path, runtime)
    services = LiveRoutingServices(config, **dependencies)

    with services:
        pass

    report = services.cleanup_report
    assert report is not None
    assert report["absence_proven"] is True
    assert report["attempts"][-1] == {
        "action": "inspect",
        "outcome": "absent",
        "returncode": 1,
    }
    assert not run_root.exists()


def test_unresolved_container_surfaces_report_and_preserves_evidence_root(
    tmp_path: Path,
) -> None:
    runtime = _FakeRuntime()
    runtime.stop_result = "nonzero"
    runtime.rm_result = "nonzero"
    runtime.inspect_results = ["present", "ambiguous", "present"]
    config, dependencies, run_root = _fixture(tmp_path, runtime)
    services = LiveRoutingServices(config, **dependencies)

    with pytest.raises(ServiceCleanupError) as raised:
        with services:
            pass

    assert raised.value.report["absence_proven"] is False
    assert raised.value.report["evidence_root_preserved"] is True
    assert raised.value.report["failure_types"] == ["ServiceCleanupError"]
    assert services.cleanup_report == raised.value.report
    assert run_root.is_dir()
    assert runtime.open_ports == set()


def test_unclosed_owned_port_preserves_logs_after_other_cleanup_finishes(
    tmp_path: Path,
) -> None:
    runtime = _FakeRuntime()
    runtime.sticky_ports.add(9191)
    config, dependencies, run_root = _fixture(tmp_path, runtime)
    services = LiveRoutingServices(config, **dependencies)

    with pytest.raises(ServiceError, match="ports remained open"):
        with services:
            pass

    report = services.cleanup_report
    assert report is not None
    assert report["absence_proven"] is True
    assert report["evidence_root_preserved"] is True
    assert run_root.is_dir()
    assert runtime.open_ports == {9191}


def test_successful_cleanup_removes_only_its_owned_temporary_root(
    tmp_path: Path,
) -> None:
    runtime = _FakeRuntime()
    config, dependencies, run_root = _fixture(tmp_path, runtime)
    unrelated = tmp_path / "kaetram-live-services-unrelated"
    unrelated.mkdir()
    sentinel = unrelated / "keep.txt"
    sentinel.write_text("unrelated\n")

    with LiveRoutingServices(config, **dependencies):
        pass

    assert not run_root.exists()
    assert sentinel.read_text() == "unrelated\n"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_timeout_is_rejected(tmp_path: Path, value: float) -> None:
    runtime = _FakeRuntime()
    config, dependencies, _ = _fixture(tmp_path, runtime)
    invalid = ServiceConfig(
        **{
            **config.__dict__,
            "readiness_timeout_seconds": value,
        }
    )
    with pytest.raises(ServiceError, match="must be positive"):
        with LiveRoutingServices(invalid, **dependencies):
            raise AssertionError("unreachable")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("node_version", "v20.20.1", "Node version differs from registration"),
        (
            "docker_client_version",
            "Docker version 29.2.0, build stale",
            "Docker client version differs from registration",
        ),
        ("node_executable_sha256", "0" * 64, "Node executable differs"),
        ("docker_executable_sha256", "0" * 64, "Docker executable differs"),
    ],
)
def test_registered_runtime_binary_drift_refuses_before_service_start(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    runtime = _FakeRuntime()
    config, dependencies, run_root = _fixture(tmp_path, runtime)
    invalid = ServiceConfig(**{**config.__dict__, field: value})
    with pytest.raises(ServiceError, match=message):
        with LiveRoutingServices(invalid, **dependencies):
            raise AssertionError("unreachable")
    assert runtime.process_calls == []
    assert not run_root.exists()
