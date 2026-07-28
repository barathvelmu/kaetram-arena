#!/usr/bin/env python3
"""Owned, zero-cost local services for the live-routing diagnostic.

Nothing starts on import.  The supervisor uses a digest-pinned cached Mongo
image and new process groups so teardown never relies on names, globs, or
unrelated machine state.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from scripts.opd.live_routing_launcher import (
    CLIENT_DIST_RELATIVE_PATH,
    GAME_BUNDLE_RELATIVE_PATH,
    LauncherError,
    _client_dist_inventory,
    attest_game_checkout,
)


MONGO_IMAGE = (
    "mongo@sha256:9bdaeb6dac6e7e762e84e2f84103d1f9bb078fa1ba6bde8bb9d2274f655ad173"
)
SERVICE_PORTS = {"mongo": 27017, "client": 9000, "game": 9191}


class ServiceError(RuntimeError):
    """The owned local service boundary could not be proven."""


class ServiceCleanupError(ServiceError):
    """Cleanup failed, with machine-readable evidence of every attempted step."""

    def __init__(self, message: str, report: dict[str, Any]) -> None:
        super().__init__(message)
        self.report = copy.deepcopy(report)


@dataclass(frozen=True)
class ServiceConfig:
    game_root: Path
    game_revision: str
    server_bundle_sha256: str
    client_dist_inventory_sha256: str
    python_binary: Path
    node_version: str
    node_executable_sha256: str
    docker_client_version: str
    docker_executable_sha256: str
    docker_binary: Path = Path("/usr/local/bin/docker")
    node_binary: Path = Path("/opt/homebrew/opt/node@20/bin/node")
    readiness_timeout_seconds: float = 30.0
    shutdown_timeout_seconds: float = 8.0
    poll_interval_seconds: float = 0.1

    def validate(self) -> None:
        if re.fullmatch(r"[0-9a-f]{40}", self.game_revision) is None:
            raise ServiceError("game revision is not an exact Git commit")
        for label, value in (
            ("server bundle", self.server_bundle_sha256),
            ("client dist", self.client_dist_inventory_sha256),
            ("Node executable", self.node_executable_sha256),
            ("Docker executable", self.docker_executable_sha256),
        ):
            if re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ServiceError(f"{label} digest is invalid")
        if re.fullmatch(r"v20\.[0-9]+\.[0-9]+", self.node_version) is None:
            raise ServiceError("registered Node version is invalid")
        if not self.docker_client_version.startswith("Docker version "):
            raise ServiceError("registered Docker client version is invalid")
        for label, value in (
            ("readiness timeout", self.readiness_timeout_seconds),
            ("shutdown timeout", self.shutdown_timeout_seconds),
            ("poll interval", self.poll_interval_seconds),
        ):
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ServiceError(f"{label} must be positive")


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ServiceError(f"required regular file is missing: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def _group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError as exc:
        raise ServiceError("owned process group cannot be inspected") from exc
    return True


class LiveRoutingServices:
    """Context manager owning Mongo, static client, and game service lifetimes."""

    def __init__(
        self,
        config: ServiceConfig,
        *,
        process_factory: Callable[..., Any] = subprocess.Popen,
        command_runner: Callable[..., Any] = subprocess.run,
        port_probe: Callable[[str, int], bool] = _port_open,
        process_group_lookup: Callable[[int], int] = os.getpgid,
        process_group_exists: Callable[[int], bool] = _group_exists,
        kill_process_group: Callable[[int, int], None] = os.killpg,
        checkout_attestor: Callable[[Path, dict[str, Any]], dict[str, Any]] = attest_game_checkout,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        make_temp: Callable[..., str] = tempfile.mkdtemp,
        remove_tree: Callable[..., None] = shutil.rmtree,
    ) -> None:
        self.config = config
        self._process_factory = process_factory
        self._command_runner = command_runner
        self._port_probe = port_probe
        self._pgid_lookup = process_group_lookup
        self._group_exists = process_group_exists
        self._kill_group = kill_process_group
        self._checkout_attestor = checkout_attestor
        self._monotonic = monotonic
        self._sleep = sleep
        self._make_temp = make_temp
        self._remove_tree = remove_tree
        self._run_root: Path | None = None
        self._container_name: str | None = None
        self._container_may_exist = False
        self._processes: dict[str, Any] = {}
        self._process_groups: dict[str, int] = {}
        self._log_handles: list[Any] = []
        self._evidence: dict[str, Any] | None = None
        self._cleanup_report: dict[str, Any] | None = None

    @property
    def evidence(self) -> dict[str, Any]:
        if self._evidence is None:
            raise ServiceError("service evidence is unavailable before readiness")
        return copy.deepcopy(self._evidence)

    @property
    def cleanup_report(self) -> dict[str, Any] | None:
        return copy.deepcopy(self._cleanup_report)

    def __enter__(self) -> "LiveRoutingServices":
        try:
            self.start()
        except BaseException as startup_error:
            try:
                self.stop()
            except BaseException as teardown_error:
                raise BaseExceptionGroup(
                    "startup and owned-service teardown both failed",
                    [startup_error, teardown_error],
                )
            raise
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        try:
            self.stop()
        except BaseException as teardown_error:
            if exc is not None:
                raise BaseExceptionGroup(
                    "callback and owned-service teardown both failed",
                    [exc, teardown_error],
                )
            raise
        return False

    def _run(self, command: list[str], *, check: bool) -> Any:
        return self._command_runner(
            command,
            check=check,
            capture_output=True,
            text=True,
            timeout=self.config.readiness_timeout_seconds,
        )

    def _preflight(
        self,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
        self.config.validate()
        game_root = self.config.game_root.resolve()
        registration = {
            "live_contract": {
                "game_revision": self.config.game_revision,
                "game_bundle_sha256": self.config.server_bundle_sha256,
                "client_dist_inventory_sha256": self.config.client_dist_inventory_sha256,
            }
        }
        try:
            game = self._checkout_attestor(game_root, registration)
        except LauncherError as exc:
            raise ServiceError(str(exc)) from exc
        docker_sha256 = _sha256_file(self.config.docker_binary.resolve())
        python_sha256 = _sha256_file(self.config.python_binary.resolve())
        node_sha256 = _sha256_file(self.config.node_binary.resolve())
        if node_sha256 != self.config.node_executable_sha256:
            raise ServiceError("Node executable differs from registration")
        if docker_sha256 != self.config.docker_executable_sha256:
            raise ServiceError("Docker executable differs from registration")
        node_version_result = self._run(
            [str(self.config.node_binary.resolve()), "--version"], check=True
        )
        node_version = (node_version_result.stdout or "").strip()
        if node_version != self.config.node_version:
            raise ServiceError("Node version differs from registration")
        docker_version_result = self._run(
            [str(self.config.docker_binary.resolve()), "--version"], check=True
        )
        docker_client_version = (docker_version_result.stdout or "").strip()
        if docker_client_version != self.config.docker_client_version:
            raise ServiceError("Docker client version differs from registration")
        try:
            inspected = self._run(
                [
                    str(self.config.docker_binary.resolve()),
                    "image",
                    "inspect",
                    "--format",
                    "{{json .}}",
                    MONGO_IMAGE,
                ],
                check=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ServiceError("digest-pinned Mongo image is not cached locally") from exc
        try:
            image = json.loads(inspected.stdout)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ServiceError("cached Mongo image inspection is malformed") from exc
        machine = platform.machine().lower()
        expected_architecture = {
            "arm64": "arm64",
            "aarch64": "arm64",
            "x86_64": "amd64",
            "amd64": "amd64",
        }.get(machine)
        image_id = image.get("Id") if isinstance(image, dict) else None
        repo_digests = image.get("RepoDigests") if isinstance(image, dict) else None
        if (
            expected_architecture is None
            or not isinstance(image_id, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None
            or not isinstance(repo_digests, list)
            or MONGO_IMAGE not in repo_digests
            or image.get("Architecture") != expected_architecture
            or image.get("Os") != "linux"
        ):
            raise ServiceError("cached Mongo image identity or platform drift")
        image_attestation = {
            "reference": MONGO_IMAGE,
            "image_id": image_id,
            "architecture": expected_architecture,
            "os": "linux",
        }
        occupied = [
            port for port in SERVICE_PORTS.values() if self._port_probe("127.0.0.1", port)
        ]
        if occupied:
            raise ServiceError(f"registered local service ports are occupied: {occupied}")
        binary_attestation = {
            "node_version": node_version,
            "node_executable_sha256": node_sha256,
            "docker_client_version": docker_client_version,
            "docker_executable_sha256": docker_sha256,
            "python_executable_sha256": python_sha256,
        }
        return game, image_attestation, binary_attestation

    def _aliases(self) -> list[tuple[str, str]]:
        if self._run_root is None:
            raise ServiceError("run root is not allocated")
        return sorted(
            [
                (str(self.config.game_root.resolve()), "$GAME_ROOT"),
                (str(self._run_root.resolve()), "$RUN_ROOT"),
                (str(self.config.docker_binary.resolve()), "$DOCKER"),
                (str(self.config.python_binary.resolve()), "$PYTHON"),
                (str(self.config.node_binary.resolve()), "$NODE20"),
            ],
            key=lambda row: len(row[0]),
            reverse=True,
        )

    def _portable(self, value: str) -> str:
        for actual, alias in self._aliases():
            value = value.replace(actual, alias)
        if value.startswith("/"):
            raise ServiceError("durable evidence retained an unregistered absolute path")
        return value

    def _spawn(
        self,
        label: str,
        command: list[str],
        *,
        cwd: Path,
        environment: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if self._run_root is None:
            raise ServiceError("run root is not allocated")
        log_handle = (self._run_root / f"{label}.log").open("ab")
        self._log_handles.append(log_handle)
        process = self._process_factory(
            command,
            cwd=str(cwd),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        process_group = self._pgid_lookup(process.pid)
        self._processes[label] = process
        self._process_groups[label] = process_group
        if process_group != process.pid:
            raise ServiceError(f"{label} did not start in its owned process group")
        return {
            "command": [self._portable(str(item)) for item in command],
            "pid": process.pid,
            "process_group": process_group,
            "cwd": self._portable(str(cwd.resolve())),
        }

    def _wait_ready(self, label: str, port: int) -> None:
        process = self._processes[label]
        deadline = self._monotonic() + self.config.readiness_timeout_seconds
        while self._monotonic() < deadline:
            if process.poll() is not None:
                raise ServiceError(f"{label} exited before readiness")
            if self._port_probe("127.0.0.1", port):
                return
            self._sleep(self.config.poll_interval_seconds)
        raise ServiceError(f"{label} did not become ready on its registered port")

    def start(self) -> dict[str, Any]:
        if self._run_root is not None:
            raise ServiceError("owned services cannot be started twice")
        game, image_attestation, binary_attestation = self._preflight()
        self._run_root = Path(self._make_temp(prefix="kaetram-live-services-"))
        (self._run_root / "mongo-data").mkdir(mode=0o700)
        suffix = self._run_root.name.removeprefix("kaetram-live-services-")
        if re.fullmatch(r"[A-Za-z0-9_-]+", suffix) is None:
            raise ServiceError("temporary service identifier is unsafe")
        self._container_name = f"kaetram-live-mongo-{suffix}"

        docker = str(self.config.docker_binary.resolve())
        python = str(self.config.python_binary.resolve())
        node = str(self.config.node_binary.resolve())
        game_root = self.config.game_root.resolve()
        server_dir = game_root / "packages/server"
        client_dist = game_root / CLIENT_DIST_RELATIVE_PATH

        mongo_command = [
            docker,
            "run",
            "--rm",
            "--name",
            self._container_name,
            "--pull=never",
            "--publish",
            "127.0.0.1:27017:27017",
            "--mount",
            f"type=bind,source={self._run_root / 'mongo-data'},target=/data/db",
            MONGO_IMAGE,
            "--bind_ip_all",
            "--port",
            "27017",
        ]
        self._container_may_exist = True
        services = {
            "mongo": self._spawn("mongo", mongo_command, cwd=self._run_root),
        }
        self._wait_ready("mongo", SERVICE_PORTS["mongo"])

        client_command = [
            python,
            "-m",
            "http.server",
            "9000",
            "--bind",
            "127.0.0.1",
            "--directory",
            str(client_dist),
        ]
        services["client"] = self._spawn(
            "client", client_command, cwd=self._run_root
        )
        self._wait_ready("client", SERVICE_PORTS["client"])

        game_environment = {
            "HOME": os.environ.get("HOME", "/tmp"),
            "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "NODE_ENV": "e2e",
            "DOTENV_CONFIG_INCLUDE_PROCESS_ENV": "true",
            "HOST": "127.0.0.1",
            "PORT": "9191",
            "ACCEPT_LICENSE": "true",
            "SKIP_DATABASE": "false",
            "DATABASE": "mongodb",
            "MONGODB_HOST": "127.0.0.1",
            "MONGODB_PORT": "27017",
            "MONGODB_DATABASE": "kaetram_e2e",
            "MONGODB_USER": "",
            "MONGODB_PASSWORD": "",
            "MONGODB_SRV": "false",
            "MONGODB_TLS": "false",
            "MONGODB_AUTH_SOURCE": "",
            "API_ENABLED": "false",
            "HUB_ENABLED": "false",
            "DISCORD_ENABLED": "false",
        }
        game_command = [
            node,
            "--enable-source-maps",
            str(game_root / GAME_BUNDLE_RELATIVE_PATH),
            "--host",
            "127.0.0.1",
            "--port",
            "9191",
        ]
        services["game"] = self._spawn(
            "game", game_command, cwd=server_dir, environment=game_environment
        )
        self._wait_ready("game", SERVICE_PORTS["game"])

        client_inventory = _client_dist_inventory(client_dist)
        self._evidence = {
            "schema_version": "kaetram.live-routing-services.v1",
            "lane": {
                "host": "127.0.0.1",
                "mongo_port": 27017,
                "client_port": 9000,
                "game_port": 9191,
                "mongo_database": "kaetram_e2e",
                "model_calls": 0,
                "remote_endpoints": 0,
            },
            "mongo_image": MONGO_IMAGE,
            "mongo_image_attestation": image_attestation,
            "container_name": self._container_name,
            "services": services,
            "environment": {
                key: game_environment[key]
                for key in (
                    "NODE_ENV",
                    "HOST",
                    "PORT",
                    "SKIP_DATABASE",
                    "DATABASE",
                    "MONGODB_HOST",
                    "MONGODB_PORT",
                    "MONGODB_DATABASE",
                    "MONGODB_SRV",
                    "MONGODB_TLS",
                    "API_ENABLED",
                    "HUB_ENABLED",
                    "DISCORD_ENABLED",
                )
            },
            "identity": {
                "game_revision": game["git_head"],
                "server_bundle_sha256": game["bundle_sha256"],
                "client_dist_file_count": client_inventory["file_count"],
                "client_dist_inventory_sha256": client_inventory[
                    "inventory_sha256"
                ],
                **binary_attestation,
            },
        }
        self._evidence["payload_sha256"] = hashlib.sha256(
            json.dumps(
                self._evidence,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        # Final serialization is also the fail-closed path-privacy check.
        encoded = json.dumps(self._evidence, allow_nan=False, sort_keys=True)
        if str(Path.home()) in encoded or str(game_root) in encoded or str(self._run_root) in encoded:
            raise ServiceError("durable service evidence contains an identity-bearing path")
        return self.evidence

    def _terminate_group(self, label: str, first_signal: int) -> None:
        process_group = self._process_groups.get(label)
        if process_group is None or not self._group_exists(process_group):
            return
        try:
            self._kill_group(process_group, first_signal)
        except ProcessLookupError:
            return
        deadline = self._monotonic() + self.config.shutdown_timeout_seconds
        while self._group_exists(process_group) and self._monotonic() < deadline:
            self._sleep(self.config.poll_interval_seconds)
        if self._group_exists(process_group):
            try:
                self._kill_group(process_group, signal.SIGKILL)
            except ProcessLookupError:
                return
        if self._group_exists(process_group):
            raise ServiceError(f"owned {label} process group survived teardown")
        process = self._processes.get(label)
        if process is not None:
            try:
                process.wait(timeout=0)
            except subprocess.TimeoutExpired as exc:
                raise ServiceError(f"owned {label} process was not reaped") from exc

    def _container_command(
        self,
        command: list[str],
        *,
        action: str,
        report: dict[str, Any],
    ) -> Any | None:
        """Run one cleanup command and retain its outcome without short-circuiting."""

        try:
            result = self._command_runner(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.config.shutdown_timeout_seconds,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            report["attempts"].append(
                {
                    "action": action,
                    "outcome": "exception",
                    "error_type": type(exc).__name__,
                }
            )
            return None
        report["attempts"].append(
            {
                "action": action,
                "outcome": "completed" if result.returncode == 0 else "nonzero",
                "returncode": result.returncode,
            }
        )
        return result

    def _container_absent(self, docker: str, report: dict[str, Any]) -> bool | None:
        """Return an exact absence verdict; ``None`` means inspection was ambiguous."""

        if self._container_name is None:
            return True
        result = self._container_command(
            [
                docker,
                "inspect",
                "--type",
                "container",
                "--format",
                "{{.Name}}",
                self._container_name,
            ],
            action="inspect",
            report=report,
        )
        if result is None:
            return None
        if result.returncode == 0:
            if (result.stdout or "").strip() != f"/{self._container_name}":
                report["attempts"][-1]["outcome"] = "malformed"
                return None
            report["attempts"][-1]["outcome"] = "present"
            return False
        error = (result.stderr or "").strip()
        absent_messages = {
            f"Error: No such object: {self._container_name}",
            f"Error: No such container: {self._container_name}",
        }
        if result.returncode == 1 and error in absent_messages:
            report["attempts"][-1]["outcome"] = "absent"
            return True
        report["attempts"][-1]["outcome"] = "ambiguous"
        return None

    def _remove_container(self) -> None:
        if not self._container_may_exist or self._container_name is None:
            return
        docker = str(self.config.docker_binary.resolve())
        report: dict[str, Any] = {
            "schema_version": "kaetram.service-cleanup.v1",
            "container_name": self._container_name,
            "attempts": [],
            "absence_proven": False,
        }
        self._container_command(
            [docker, "stop", "--time", "5", self._container_name],
            action="stop",
            report=report,
        )
        self._container_command(
            [docker, "rm", "--force", self._container_name],
            action="rm",
            report=report,
        )

        # A failed stop/rm may still have achieved cleanup (for example, an
        # --rm container can disappear between the two calls). Trust only an
        # exact inspect verdict, retrying force-removal when it is still present
        # or the daemon response is ambiguous.
        for attempt in range(3):
            absent = self._container_absent(docker, report)
            if absent:
                report["absence_proven"] = True
                self._container_may_exist = False
                self._cleanup_report = report
                return
            if attempt < 2:
                self._container_command(
                    [docker, "rm", "--force", self._container_name],
                    action="rm_retry",
                    report=report,
                )
                self._sleep(self.config.poll_interval_seconds)

        self._cleanup_report = report
        raise ServiceCleanupError(
            "owned Mongo container absence could not be proven", report
        )

    def _prove_ports_closed(self) -> None:
        deadline = self._monotonic() + self.config.shutdown_timeout_seconds
        while self._monotonic() < deadline:
            open_ports = [
                port
                for port in SERVICE_PORTS.values()
                if self._port_probe("127.0.0.1", port)
            ]
            if not open_ports:
                return
            self._sleep(self.config.poll_interval_seconds)
        raise ServiceError(f"owned service ports remained open: {open_ports}")

    def stop(self) -> None:
        if self._run_root is None:
            return
        failures: list[BaseException] = []
        operations = (
            lambda: self._terminate_group("game", signal.SIGINT),
            lambda: self._terminate_group("client", signal.SIGTERM),
            self._remove_container,
            lambda: self._terminate_group("mongo", signal.SIGTERM),
            self._prove_ports_closed,
        )
        for operation in operations:
            try:
                operation()
            except BaseException as exc:
                failures.append(exc)
        for handle in self._log_handles:
            try:
                handle.close()
            except BaseException as exc:
                failures.append(exc)
        self._log_handles.clear()

        # Logs and Mongo data are cleanup evidence. Remove the exact mkdtemp
        # root only after every owned process/container/port has been proven
        # absent; otherwise retain it for diagnosis and a possible retry.
        if not failures:
            run_root = self._run_root
            try:
                self._remove_tree(run_root)
            except BaseException as exc:
                failures.append(exc)
            else:
                self._run_root = None
        if failures:
            if self._cleanup_report is None:
                self._cleanup_report = {
                    "schema_version": "kaetram.service-cleanup.v1",
                    "container_name": self._container_name,
                    "attempts": [],
                    "absence_proven": not self._container_may_exist,
                }
            self._cleanup_report["evidence_root_preserved"] = bool(
                self._run_root is not None and self._run_root.exists()
            )
            self._cleanup_report["failure_types"] = [
                type(failure).__name__ for failure in failures
            ]
        else:
            if self._cleanup_report is None:
                self._cleanup_report = {
                    "schema_version": "kaetram.service-cleanup.v1",
                    "container_name": self._container_name,
                    "attempts": [],
                    "absence_proven": True,
                }
            self._cleanup_report["evidence_root_preserved"] = False
        for failure in failures:
            if isinstance(failure, ServiceCleanupError):
                failure.report = copy.deepcopy(self._cleanup_report)
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise BaseExceptionGroup("owned-service teardown failures", failures)


def run_with_local_services(
    config: ServiceConfig,
    callback: Callable[[dict[str, Any]], Any],
    **dependencies: Any,
) -> Any:
    """Run a supplied orchestrator only while all owned services are ready."""

    with LiveRoutingServices(config, **dependencies) as services:
        return callback(services.evidence)
