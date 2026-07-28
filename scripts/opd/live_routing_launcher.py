#!/usr/bin/env python3
"""Fail-closed local launcher primitives for the live routing diagnostic.

The checked-in registration remains design-only, so this module cannot launch
the study yet.  It provides the audited game/source preflight, sanitized worker
environment, and create-only Mongo ownership layer required by the future
result-bearing session orchestrator.  No service is contacted on import.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from canonical_start import (  # noqa: E402
    CANONICAL_INITIAL_STATE,
    canonical_database_documents,
    initial_state_projection,
)
from scripts.opd.live_routing_diagnostic import (  # noqa: E402
    STATUS,
    load_registration_strict,
    validate_registration,
)
from scripts.opd.execution_evidence import parse_tool_result_json  # noqa: E402
from scripts.opd.live_routing_prelaunch import (  # noqa: E402
    EXPECTED_LANE,
    READY_STATUS,
    PrelaunchError,
    validate_lane,
)
from scripts.opd.response_router import route_content_tool_call  # noqa: E402
from tool_surface import validate_tool_call_arguments  # noqa: E402


GAME_BUNDLE_RELATIVE_PATH = Path("packages/server/dist/main.js")
CLIENT_DIST_RELATIVE_PATH = Path("packages/client/dist")
MONGO_COLLECTIONS = (
    "player_info",
    "player_inventory",
    "player_bank",
    "player_equipment",
    "player_quests",
    "player_achievements",
    "player_skills",
    "player_statistics",
    "player_abilities",
)
LOCK_COLLECTION = "live_routing_diagnostic_locks"
USERNAME_RE = re.compile(r"[a-z0-9_]{1,16}")
SESSION_RE = re.compile(r"[a-z0-9-]{8,80}")
FORBIDDEN_ENV_RE = re.compile(
    r"(API[_-]?KEY|TOKEN|SECRET|ENDPOINT|MODAL|OPENAI|ANTHROPIC|WANDB|COMET)",
    re.IGNORECASE,
)


class LauncherError(RuntimeError):
    pass


class PartialSeedError(LauncherError):
    def __init__(self, message: str, receipt: dict[str, Any]):
        super().__init__(message)
        self.receipt = receipt


@dataclass(frozen=True)
class LaneConfig:
    client_url: str = "http://127.0.0.1:9000"
    game_ws_url: str = "ws://127.0.0.1:9191"
    mongo_uri: str = "mongodb://127.0.0.1:27017/kaetram_e2e"
    mongo_database: str = "kaetram_e2e"

    def validate(self) -> None:
        validate_lane(
            {
                **EXPECTED_LANE,
                "client_url": self.client_url,
                "game_ws_url": self.game_ws_url,
                "mongo_uri": self.mongo_uri,
                "mongo_database": self.mongo_database,
            }
        )


@dataclass(frozen=True)
class SessionSpec:
    trial_id: str
    session_id: str
    phase: str
    username: str
    arm: str

    def validate(self) -> None:
        if not USERNAME_RE.fullmatch(self.username):
            raise LauncherError("username violates the registered Kaetram limit")
        if not SESSION_RE.fullmatch(self.session_id):
            raise LauncherError("session identifier is malformed")
        if self.phase not in ("treatment", "reconnect"):
            raise LauncherError("session phase must be treatment or reconnect")


def session_spec_from_plan(plan: Mapping[str, Any], phase: str) -> SessionSpec:
    session_key = (
        "treatment_session_id" if phase == "treatment" else "reconnect_session_id"
    )
    try:
        spec = SessionSpec(
            trial_id=str(plan["trial_id"]),
            session_id=str(plan[session_key]),
            phase=phase,
            username=str(plan["username"]),
            arm=str(plan["arm"]),
        )
    except KeyError as exc:
        raise LauncherError(f"planned session field missing: {exc}") from exc
    spec.validate()
    return spec


def _parse_tool_json(
    result: Any,
    *,
    expected_name: str,
) -> dict[str, Any] | None:
    if result is None or getattr(result, "is_error", True):
        return None
    return parse_tool_result_json(
        getattr(result, "text", None), expected_name=expected_name
    )


def _projection_from_result(result: Any) -> dict[str, Any] | None:
    value = _parse_tool_json(result, expected_name="observe")
    if value is None:
        return None
    projection = initial_state_projection(value)
    try:
        json.dumps(projection, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError):
        return None
    return projection


RUNTIME_ATTESTATION_KEYS = {
    "schema_version",
    "session_id",
    "mcp_pid",
    "mcp_process_group",
    "mcp_instance_nonce",
    "browser_launch_nonce",
    "browser_nonce_echo",
    "browser_name",
    "browser_version",
    "browser_executable_sha256",
    "page_url",
    "player_username",
    "configured_client_url",
    "configured_game_port",
    "require_existing_account",
    "heartbeats_disabled",
    "loopback_only",
}
RAW_ATTESTATION_KEYS = {"raw_text", "raw_sha256", "parsed"}


def _unwrap_runtime_attestation(attestation: Any) -> dict[str, Any]:
    """Validate and unwrap the lossless evidence envelope, if present."""

    if isinstance(attestation, dict) and set(attestation) == RAW_ATTESTATION_KEYS:
        raw_text = attestation.get("raw_text")
        if not isinstance(raw_text, str) or attestation.get(
            "raw_sha256"
        ) != hashlib.sha256(raw_text.encode("utf-8")).hexdigest():
            raise LauncherError("runtime attestation raw evidence digest mismatch")
        parsed = parse_tool_result_json(
            raw_text, expected_name="__diagnostic_runtime_attestation"
        )
        try:
            reparsed_bytes = json.dumps(
                parsed,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            recorded_bytes = json.dumps(
                attestation.get("parsed"),
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise LauncherError("runtime attestation parsed evidence is invalid")
        if reparsed_bytes != recorded_bytes:
            raise LauncherError("runtime attestation raw evidence parse mismatch")
        return parsed
    if not isinstance(attestation, dict):
        raise LauncherError("runtime attestation is not an object")
    return attestation


def validate_runtime_attestation(
    attestation: Any,
    spec: SessionSpec,
    *,
    worker_pid: int | None = None,
    worker_process_group: int | None = None,
) -> None:
    """Validate the cold MCP/browser identity before recording any outcome."""

    if not isinstance(attestation, dict) or set(attestation) != RUNTIME_ATTESTATION_KEYS:
        raise LauncherError("runtime attestation key set drift")
    if attestation.get("schema_version") != "kaetram.diagnostic-runtime-attestation.v1":
        raise LauncherError("runtime attestation schema drift")
    if attestation.get("session_id") != spec.session_id:
        raise LauncherError("runtime attestation session identity mismatch")
    expected_worker_pid = os.getpid() if worker_pid is None else worker_pid
    expected_group = os.getpgrp() if worker_process_group is None else worker_process_group
    mcp_pid = attestation.get("mcp_pid")
    mcp_group = attestation.get("mcp_process_group")
    if (
        type(mcp_pid) is not int
        or mcp_pid <= 0
        or mcp_pid == expected_worker_pid
        or type(mcp_group) is not int
        or mcp_group != expected_group
    ):
        raise LauncherError("runtime attestation MCP process identity mismatch")
    nonces = (
        attestation.get("mcp_instance_nonce"),
        attestation.get("browser_launch_nonce"),
    )
    if any(
        not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None
        for value in nonces
    ) or len(set(nonces)) != 2:
        raise LauncherError("runtime attestation nonce identity is invalid")
    if attestation.get("browser_nonce_echo") != attestation["browser_launch_nonce"]:
        raise LauncherError("runtime attestation browser nonce echo mismatch")
    executable_sha = attestation.get("browser_executable_sha256")
    if (
        attestation.get("browser_name") != "chromium"
        or not isinstance(attestation.get("browser_version"), str)
        or not attestation["browser_version"]
        or not isinstance(executable_sha, str)
        or re.fullmatch(r"[0-9a-f]{64}", executable_sha) is None
    ):
        raise LauncherError("runtime attestation browser identity is invalid")
    try:
        page = urlsplit(attestation.get("page_url"))
        page_host = page.hostname
        page_port = page.port
    except (TypeError, ValueError) as exc:
        raise LauncherError("runtime attestation page URL is invalid") from exc
    if (
        page.scheme != "http"
        or page_host != "127.0.0.1"
        or page_port != 9000
        or page.path not in ("", "/")
        or page.username is not None
        or page.password is not None
        or page.query
        or page.fragment
    ):
        raise LauncherError("runtime attestation page escaped the loopback lane")
    expected_values = {
        "player_username": spec.username,
        "configured_client_url": "http://127.0.0.1:9000",
        "configured_game_port": "9191",
        "require_existing_account": True,
        "heartbeats_disabled": True,
        "loopback_only": True,
    }
    if any(attestation.get(key) != value for key, value in expected_values.items()):
        raise LauncherError("runtime attestation lane or player identity mismatch")


def validate_runtime_attestation_set(
    rows: Sequence[tuple[SessionSpec, dict[str, Any]]],
) -> None:
    """Prove the registered 9x2 sessions used distinct cold runtimes."""

    if len(rows) != 18:
        raise LauncherError("runtime attestation set must contain exactly 18 sessions")
    sessions = [spec.session_id for spec, _ in rows]
    if len(set(sessions)) != 18:
        raise LauncherError("runtime attestation session IDs are not unique")
    by_trial: dict[str, list[SessionSpec]] = {}
    parsed_rows = []
    for spec, raw_attestation in rows:
        attestation = _unwrap_runtime_attestation(raw_attestation)
        parsed_rows.append((spec, attestation))
        mcp_group = attestation.get("mcp_process_group")
        validate_runtime_attestation(
            attestation,
            spec,
            worker_pid=mcp_group,
            worker_process_group=mcp_group,
        )
        by_trial.setdefault(spec.trial_id, []).append(spec)
    if len(by_trial) != 9 or any(
        len(specs) != 2
        or {spec.phase for spec in specs} != {"treatment", "reconnect"}
        or len({spec.username for spec in specs}) != 1
        or len({spec.arm for spec in specs}) != 1
        for specs in by_trial.values()
    ):
        raise LauncherError("runtime attestation treatment/reconnect pairing drift")
    unique_fields = (
        "mcp_pid",
        "mcp_process_group",
        "mcp_instance_nonce",
        "browser_launch_nonce",
    )
    for field in unique_fields:
        values = [attestation[field] for _, attestation in parsed_rows]
        if len(set(values)) != 18:
            raise LauncherError(f"runtime attestation cold identity reused: {field}")


async def session_worker(
    spec: SessionSpec,
    registration: dict[str, Any],
    *,
    mcp_session_factory: Callable[..., Any] | None = None,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> dict[str, Any]:
    """Run exactly one cold treatment or reconnect session in this process."""

    spec.validate()
    if mcp_session_factory is None:
        from tests.e2e.helpers.mcp_client import mcp_session

        mcp_session_factory = mcp_session

    state_dir = Path(os.environ["KAETRAM_STATE_DIR"])
    state_dir.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    phase: dict[str, Any] = {
        "schema_version": "kaetram.live-routing-session-phase.v1",
        "trial_id": spec.trial_id,
        "session_id": spec.session_id,
        "phase": spec.phase,
        "username": spec.username,
        "arm": spec.arm,
        "runtime_attestation": None,
        "candidate_call_ledger": [],
        "precondition": None,
        "routing": None,
        "immediate": None,
        "delayed": None,
        "delayed_elapsed_monotonic_seconds": None,
        "reconnect": None,
        "worker_elapsed_seconds": None,
    }
    extra_env = {
        key: value for key, value in os.environ.items() if key.startswith("KAETRAM_")
    }
    async with mcp_session_factory(
        username=spec.username,
        password="test",
        client_url="http://127.0.0.1:9000",
        server_port="9191",
        headed=False,
        state_dir=str(state_dir),
        extra_env=extra_env,
        python_executable=sys.executable,
    ) as handle:
        attestation_result = await handle.call_tool(
            "__diagnostic_runtime_attestation", {}
        )
        attestation = _parse_tool_json(
            attestation_result,
            expected_name="__diagnostic_runtime_attestation",
        )
        if attestation is None:
            raise LauncherError("runtime attestation was unavailable")
        validate_runtime_attestation(attestation, spec)
        attestation_raw_text = attestation_result.text
        phase["runtime_attestation"] = {
            "raw_text": attestation_raw_text,
            "raw_sha256": hashlib.sha256(
                attestation_raw_text.encode("utf-8")
            ).hexdigest(),
            "parsed": attestation,
        }

        if spec.phase == "reconnect":
            reconnect_result = await handle.call_tool("observe", {})
            reconnect = _projection_from_result(reconnect_result)
            phase["reconnect"] = {
                "available": reconnect is not None,
                "normalized_projection": reconnect,
                "raw_text": reconnect_result.text,
                "raw_sha256": hashlib.sha256(
                    reconnect_result.text.encode("utf-8")
                ).hexdigest(),
            }
        else:
            before_result = await handle.call_tool("observe", {})
            before = _projection_from_result(before_result)
            phase["precondition"] = {
                "available": before is not None,
                "normalized_projection": before,
                "raw_text": before_result.text,
                "raw_sha256": hashlib.sha256(
                    before_result.text.encode("utf-8")
                ).hexdigest(),
            }
            arm = next(
                (row for row in registration["arms"] if row["arm"] == spec.arm),
                None,
            )
            if arm is None:
                raise LauncherError("worker arm is not registered")
            candidate = registration["candidate"]
            router_status = "not_applicable_structured"
            schema_status = "not_applicable_no_candidate"
            dispatch_attempted = False
            invocation_count = 0
            delivery_status = "not_attempted"
            protocol_success = None
            tool_error = None
            result_json = None
            result_raw_text = None
            result_raw_sha256 = None
            if before == CANONICAL_INITIAL_STATE:
                call = None
                if spec.arm == "structured_direct":
                    call = {"name": candidate["name"], "args": candidate["arguments"]}
                elif spec.arm == "content_recovery_on":
                    decision = route_content_tool_call(candidate["content_envelope"])
                    router_status = decision["status"]
                    if decision["status"] == "promoted" and len(decision["calls"]) == 1:
                        call = decision["calls"][0]
                elif spec.arm == "content_recovery_off":
                    router_status = "disabled_not_evaluated"
                if call is not None:
                    valid, _ = validate_tool_call_arguments(call["name"], call["args"])
                    schema_status = "valid" if valid else "invalid"
                    if valid:
                        dispatch_attempted = True
                        invocation_count = 1
                        call_ledger_row = {
                            "sequence": 1,
                            "name": call["name"],
                            "arguments": call["args"],
                            "delivery_status": "unknown_after_exception",
                            "protocol_success": None,
                            "result_raw_sha256": None,
                        }
                        phase["candidate_call_ledger"].append(call_ledger_row)
                        try:
                            result = await handle.call_tool(call["name"], call["args"])
                        except Exception:
                            delivery_status = "unknown_after_exception"
                        else:
                            delivery_status = "confirmed"
                            protocol_success = not result.is_error
                            result_raw_text = result.text
                            result_raw_sha256 = hashlib.sha256(
                                result.text.encode("utf-8")
                            ).hexdigest()
                            call_ledger_row.update(
                                {
                                    "delivery_status": delivery_status,
                                    "protocol_success": protocol_success,
                                    "result_raw_sha256": result_raw_sha256,
                                }
                            )
                            result_json = _parse_tool_json(
                                result, expected_name=call["name"]
                            )
                            if isinstance(result_json, dict):
                                tool_error = result_json.get("error")
                elif spec.arm != "content_recovery_off":
                    schema_status = "not_applicable_no_candidate"
            phase["routing"] = {
                "router_status": router_status,
                "schema_status": schema_status,
                "dispatch_attempted": dispatch_attempted,
                "candidate_invocation_count": invocation_count,
                "delivery_status": delivery_status,
                "protocol_success": protocol_success,
                "tool_reported_error": tool_error,
                "result_json": result_json,
                "result_raw_text": result_raw_text,
                "result_raw_sha256": result_raw_sha256,
            }
            immediate_result = await handle.call_tool("observe", {})
            immediate_at = time.monotonic()
            immediate = _projection_from_result(immediate_result)
            phase["immediate"] = {
                "available": immediate is not None,
                "normalized_projection": immediate,
                "raw_text": immediate_result.text,
                "raw_sha256": hashlib.sha256(
                    immediate_result.text.encode("utf-8")
                ).hexdigest(),
            }
            delay = registration["runtime_parameters"][
                "minimum_delayed_observation_seconds"
            ]
            await sleep(max(0.0, immediate_at + delay - time.monotonic()))
            delayed_result = await handle.call_tool("observe", {})
            delayed_at = time.monotonic()
            delayed = _projection_from_result(delayed_result)
            phase["delayed"] = {
                "available": delayed is not None,
                "normalized_projection": delayed,
                "raw_text": delayed_result.text,
                "raw_sha256": hashlib.sha256(
                    delayed_result.text.encode("utf-8")
                ).hexdigest(),
            }
            phase["delayed_elapsed_monotonic_seconds"] = delayed_at - immediate_at
    phase["worker_elapsed_seconds"] = time.monotonic() - started
    return phase


def run_session_worker(
    spec: SessionSpec,
    registration_path: Path,
    *,
    python_executable: Path,
    state_dir: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Spawn one owned process group and prove it exits before returning."""

    environment = sanitized_worker_environment(
        os.environ, spec, lane=LaneConfig(), state_dir=state_dir
    )
    # Preserve the selected virtual-environment entry point.  Resolving this
    # symlink launches the base interpreter outside the venv and silently drops
    # the registered MCP/Playwright packages from the cold worker.
    invoked_python = python_executable.expanduser().absolute()
    command = [
        str(invoked_python),
        str(Path(__file__).resolve()),
        "session-worker",
        "--registration",
        str(registration_path.resolve()),
        "--spec-json",
        json.dumps(spec.__dict__, separators=(",", ":"), sort_keys=True),
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        start_new_session=True,
    )
    failure: BaseException | None = None
    failure_traceback = None
    value: dict[str, Any] | None = None
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        if process.returncode != 0:
            raise LauncherError(f"cold session worker failed: {stderr[-2000:]}")
        try:
            parsed = json.loads(
                stdout,
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LauncherError("cold session worker returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise LauncherError("cold session worker result is not an object")
        value = parsed
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        failure = LauncherError("cold session worker exceeded registered timeout")
        failure.__cause__ = exc
        failure_traceback = failure.__traceback__
    except BaseException as exc:
        failure = exc
        failure_traceback = exc.__traceback__
    cleanup = _terminate_owned_process_group(process)
    if cleanup["still_alive"]:
        raise LauncherError("cold session process group could not be terminated")
    if failure is not None:
        raise failure.with_traceback(failure_traceback)
    if cleanup["found_alive"] and not timed_out:
        raise LauncherError("cold session process group survived worker exit")
    if value is None:
        raise LauncherError("cold session worker produced no result")
    return value


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    return True


def _terminate_owned_process_group(
    process: subprocess.Popen,
    *,
    grace_seconds: float = 5.0,
) -> dict[str, bool]:
    """TERM/KILL the owned group on every path, then prove it is gone."""

    process_group = process.pid
    found_alive = _process_group_exists(process_group)
    if not found_alive:
        return {"found_alive": False, "sigkill_required": False, "still_alive": False}

    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        return {"found_alive": True, "sigkill_required": False, "still_alive": False}

    deadline = time.monotonic() + grace_seconds
    while _process_group_exists(process_group) and time.monotonic() < deadline:
        if process.poll() is None:
            try:
                process.wait(timeout=min(0.1, max(0.0, deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                pass
        else:
            time.sleep(0.01)
    if not _process_group_exists(process_group):
        return {"found_alive": True, "sigkill_required": False, "still_alive": False}

    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        return {"found_alive": True, "sigkill_required": True, "still_alive": False}
    kill_deadline = time.monotonic() + grace_seconds
    while _process_group_exists(process_group) and time.monotonic() < kill_deadline:
        if process.poll() is None:
            try:
                process.wait(timeout=min(0.1, max(0.0, kill_deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                pass
        else:
            time.sleep(0.01)
    return {
        "found_alive": True,
        "sigkill_required": True,
        "still_alive": _process_group_exists(process_group),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _client_dist_inventory(client_dist: Path) -> dict[str, Any]:
    """Hash the exact browser-served regular-file tree without following links."""

    if client_dist.is_symlink() or not client_dist.is_dir():
        raise LauncherError("client dist is missing, non-directory, or symlinked")
    rows: list[dict[str, Any]] = []
    for directory, directory_names, file_names in os.walk(
        client_dist, topdown=True, followlinks=False
    ):
        directory_path = Path(directory)
        directory_names.sort()
        file_names.sort()
        for name in directory_names:
            child = directory_path / name
            if child.is_symlink():
                raise LauncherError("client dist contains a symlink")
        for name in file_names:
            child = directory_path / name
            mode = child.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise LauncherError("client dist contains a symlink")
            if not stat.S_ISREG(mode):
                raise LauncherError("client dist contains a non-regular file")
            rows.append(
                {
                    "path": child.relative_to(client_dist).as_posix(),
                    "size_bytes": child.stat().st_size,
                    "sha256": _sha256_file(child),
                }
            )
    if not rows:
        raise LauncherError("client dist contains no regular files")
    encoded = json.dumps(
        rows,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "file_count": len(rows),
        "inventory_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _git(repo: Path, *arguments: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise LauncherError(f"game Git inspection failed: {' '.join(arguments)}") from exc


def attest_game_checkout(game_root: Path, registration: dict[str, Any]) -> dict[str, Any]:
    """Bind a clean game checkout and built server entrypoint to registration."""

    game_root = game_root.resolve()
    top = Path(_git(game_root, "rev-parse", "--show-toplevel").strip()).resolve()
    if top != game_root:
        raise LauncherError("game_root is not the exact Git toplevel")
    head = _git(game_root, "rev-parse", "--verify", "HEAD^{commit}").strip()
    expected = registration.get("live_contract", {})
    if head != expected.get("game_revision"):
        raise LauncherError(f"game revision drift: expected={expected.get('game_revision')}, actual={head}")
    if _git(game_root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise LauncherError("game checkout is not completely clean")
    bundle = game_root / GAME_BUNDLE_RELATIVE_PATH
    if bundle.is_symlink() or not bundle.is_file():
        raise LauncherError("registered game bundle is missing or symlinked")
    bundle_sha = _sha256_file(bundle)
    if bundle_sha != expected.get("game_bundle_sha256"):
        raise LauncherError("built game bundle digest drift")
    client_inventory = _client_dist_inventory(game_root / CLIENT_DIST_RELATIVE_PATH)
    if client_inventory["inventory_sha256"] != expected.get(
        "client_dist_inventory_sha256"
    ):
        raise LauncherError("built client dist inventory digest drift")
    return {
        "git_head": head,
        "worktree_clean": True,
        "bundle_path": GAME_BUNDLE_RELATIVE_PATH.as_posix(),
        "bundle_size_bytes": bundle.stat().st_size,
        "bundle_sha256": bundle_sha,
        "client_dist_file_count": client_inventory["file_count"],
        "client_dist_inventory_sha256": client_inventory["inventory_sha256"],
    }


def sanitized_worker_environment(
    source: Mapping[str, str],
    spec: SessionSpec,
    *,
    lane: LaneConfig | None = None,
    state_dir: Path,
) -> dict[str, str]:
    """Build a minimal environment with no model, remote, or metered credentials."""

    spec.validate()
    lane = lane or LaneConfig()
    lane.validate()
    allowed_inherited = ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL", "DISPLAY")
    environment = {
        key: source[key]
        for key in allowed_inherited
        if key in source and not FORBIDDEN_ENV_RE.search(key)
    }
    environment.update(
        {
            "KAETRAM_USERNAME": spec.username,
            "KAETRAM_PASSWORD": "test",
            "KAETRAM_CLIENT_URL": lane.client_url,
            "KAETRAM_PORT": "9191",
            "GAME_WS_HOST": "127.0.0.1",
            "GAME_WS_PORT": "9191",
            "KAETRAM_MONGO_URI": lane.mongo_uri,
            "KAETRAM_MONGO_DB": lane.mongo_database,
            "KAETRAM_TEST_LANE": "1",
            "KAETRAM_DIAGNOSTIC_LANE": "1",
            "KAETRAM_DIAGNOSTIC_SESSION_ID": spec.session_id,
            "KAETRAM_REQUIRE_EXISTING_ACCOUNT": "1",
            "KAETRAM_DISABLE_HEARTBEATS": "1",
            "KAETRAM_DIAGNOSTIC_LOOPBACK_ONLY": "1",
            "KAETRAM_SERVICE_READINESS_TIMEOUT_SECONDS": "60",
            "KAETRAM_LOGIN_TIMEOUT_SECONDS": "60",
            "KAETRAM_LIVE_SUITE": "0",
            "KAETRAM_HEADED": "0",
            "KAETRAM_STATE_DIR": str(state_dir.resolve()),
            "PYTHONNOUSERSITE": "1",
        }
    )
    forbidden = [key for key in environment if FORBIDDEN_ENV_RE.search(key)]
    if forbidden:
        raise LauncherError(f"worker environment retained forbidden keys: {forbidden}")
    return environment


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if value.__class__.__name__ == "ObjectId":
        return str(value)
    return value


def canonical_documents(username: str) -> dict[str, dict[str, Any]]:
    """Construct the exact nine Mongo documents without writing them."""

    try:
        return canonical_database_documents(username)
    except ValueError as exc:
        raise LauncherError(str(exc)) from exc


class CreateOnlyCanonicalStore:
    """Mongo writer that owns only rows inserted by this exact trial."""

    def __init__(
        self,
        *,
        lane: LaneConfig | None = None,
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.lane = lane or LaneConfig()
        self.lane.validate()
        if client_factory is None:
            from pymongo import MongoClient

            client_factory = lambda uri: MongoClient(uri, serverSelectionTimeoutMS=3000)
        self.client = client_factory(self.lane.mongo_uri)
        self.db = self.client["kaetram_e2e"]

    def close(self) -> None:
        self.client.close()

    def attest_topology(self) -> dict[str, Any]:
        """Require the discovered Mongo topology to remain numeric loopback."""

        from ipaddress import ip_address

        self.client.admin.command("ping")
        nodes = sorted(getattr(self.client, "nodes", set()))
        if not nodes:
            raise LauncherError("Mongo topology did not expose any connected node")
        normalized = []
        for host, port in nodes:
            try:
                address = ip_address(host)
            except ValueError as exc:
                raise LauncherError("Mongo topology host is not numeric") from exc
            if not address.is_loopback or port != 27017:
                raise LauncherError("Mongo topology escaped registered loopback lane")
            normalized.append({"host": str(address), "port": port})
        return {
            "uri": self.lane.mongo_uri,
            "database": "kaetram_e2e",
            "nodes": normalized,
            "loopback_only": True,
        }

    def prove_absent(self, usernames: Sequence[str]) -> dict[str, Any]:
        rows = {}
        for username in usernames:
            if not USERNAME_RE.fullmatch(username):
                raise LauncherError("absence check received invalid username")
            counts = {
                collection: int(
                    self.db[collection].count_documents({"username": username}, limit=1)
                )
                for collection in MONGO_COLLECTIONS
            }
            rows[username] = counts
        return {
            "database": "kaetram_e2e",
            "counts": rows,
            "all_absent": all(
                count == 0
                for counts in rows.values()
                for count in counts.values()
            ),
        }

    def insert_canonical(self, username: str, trial_id: str) -> dict[str, Any]:
        absence = self.prove_absent([username])
        if absence["all_absent"] is not True:
            raise LauncherError("create-only seed refused: username already exists")
        inserted: dict[str, str] = {}
        insertion_order: list[str] = []
        receipt = {
            "database": "kaetram_e2e",
            "username": username,
            "trial_id": trial_id,
            "absence": absence,
            "inserted_ids": inserted,
            "insertion_order": insertion_order,
            "player_info_inserted_last": False,
        }
        try:
            lock = self.db[LOCK_COLLECTION].insert_one(
                {"_id": username, "trial_id": trial_id}
            )
            inserted[LOCK_COLLECTION] = str(lock.inserted_id)
            insertion_order.append(LOCK_COLLECTION)
            documents = canonical_documents(username)
            order = [name for name in MONGO_COLLECTIONS if name != "player_info"]
            order.append("player_info")
            for collection in order:
                result = self.db[collection].insert_one(documents[collection])
                inserted[collection] = str(result.inserted_id)
                insertion_order.append(collection)
            receipt["player_info_inserted_last"] = True
        except Exception as exc:
            raise PartialSeedError(f"create-only seed stopped after partial write: {exc}", receipt) from exc
        return receipt

    def snapshot_owned(self, username: str, inserted_ids: Mapping[str, str]) -> dict[str, Any]:
        documents = {}
        for collection in MONGO_COLLECTIONS:
            expected_id = inserted_ids.get(collection)
            document = self.db[collection].find_one({"username": username})
            if document is None or str(document.get("_id")) != expected_id:
                raise LauncherError(f"owned database identity drift: {collection}")
            documents[collection] = _json_safe(document)
        return {"database": "kaetram_e2e", "username": username, "documents": documents}

    def cleanup_owned(
        self,
        username: str,
        trial_id: str,
        inserted_ids: Mapping[str, str],
    ) -> dict[str, Any]:
        try:
            from bson import ObjectId
        except ImportError as exc:
            raise LauncherError("bson is required for ownership-checked cleanup") from exc
        allowed = {LOCK_COLLECTION, *MONGO_COLLECTIONS}
        if not isinstance(inserted_ids, Mapping) or not set(inserted_ids).issubset(
            allowed
        ):
            raise LauncherError("cleanup received unknown owned identifiers")
        deleted = {}
        failures: list[str] = []
        for collection in MONGO_COLLECTIONS:
            identifier = inserted_ids.get(collection)
            if identifier is None:
                deleted[collection] = 0
                continue
            try:
                if not isinstance(identifier, str):
                    raise ValueError("identifier is not a string")
                result = self.db[collection].delete_one(
                    {"_id": ObjectId(identifier), "username": username}
                )
                deleted[collection] = int(result.deleted_count)
            except Exception as exc:
                deleted[collection] = 0
                failures.append(f"{collection}: {type(exc).__name__}: {exc}")
        lock_identifier = inserted_ids.get(LOCK_COLLECTION)
        if lock_identifier is None:
            lock_deleted = 0
        else:
            try:
                if lock_identifier != username:
                    raise ValueError("identifier differs from username")
                lock_result = self.db[LOCK_COLLECTION].delete_one(
                    {"_id": username, "trial_id": trial_id}
                )
                lock_deleted = int(lock_result.deleted_count)
            except Exception as exc:
                lock_deleted = 0
                failures.append(
                    f"{LOCK_COLLECTION}: {type(exc).__name__}: {exc}"
                )
        try:
            absence = self.prove_absent([username])
        except Exception as exc:
            absence = {
                "database": "kaetram_e2e",
                "counts": {username: {}},
                "all_absent": False,
            }
            failures.append(f"absence: {type(exc).__name__}: {exc}")
        expected_deleted = {
            collection: int(collection in inserted_ids)
            for collection in MONGO_COLLECTIONS
        }
        expected_lock_deleted = int(LOCK_COLLECTION in inserted_ids)
        receipt = {
            "database": "kaetram_e2e",
            "deleted": deleted,
            "lock_deleted": lock_deleted,
            "absence": absence,
            "complete": deleted == expected_deleted
            and lock_deleted == expected_lock_deleted
            and absence["all_absent"],
        }
        if failures:
            error = LauncherError(
                "ownership cleanup encountered failures: " + "; ".join(failures)
            )
            error.cleanup_receipt = receipt
            raise error
        return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--registration", type=Path, required=True)
    preflight.add_argument("--game-root", type=Path, required=True)
    worker = commands.add_parser("session-worker")
    worker.add_argument("--registration", type=Path, required=True)
    worker.add_argument("--spec-json", required=True)
    args = parser.parse_args(argv)
    try:
        registration = load_registration_strict(args.registration)
        if args.command == "session-worker":
            if registration.get("status") != READY_STATUS:
                raise LauncherError("session worker requires a live-ready registration")
            errors = validate_registration(
                registration, expected_status=READY_STATUS
            )
            if errors:
                raise LauncherError(
                    "live-ready registration invalid: " + "; ".join(errors)
                )
            raw_spec = json.loads(
                args.spec_json,
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
            if not isinstance(raw_spec, dict) or set(raw_spec) != {
                "trial_id",
                "session_id",
                "phase",
                "username",
                "arm",
            }:
                raise LauncherError("session worker spec key set drift")
            result = asyncio.run(session_worker(SessionSpec(**raw_spec), registration))
            print(
                json.dumps(
                    result,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            return 0
        status = registration.get("status")
        if status not in (STATUS, READY_STATUS):
            raise LauncherError(f"unrecognized registration status: {status!r}")
        errors = validate_registration(registration, expected_status=status)
        if errors:
            raise LauncherError("design registration invalid: " + "; ".join(errors))
        game = attest_game_checkout(args.game_root, registration)
        if registration.get("status") != READY_STATUS:
            raise LauncherError(
                "live execution refused: registration remains design-only; "
                "the result-bearing 18-session orchestrator is not yet sealed"
            )
    except (LauncherError, PrelaunchError, OSError, ValueError) as exc:
        print(f"live routing launcher refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"game": game, "status": "preflight_only"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
