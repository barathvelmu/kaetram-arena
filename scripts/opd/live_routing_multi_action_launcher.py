#!/usr/bin/env python3
"""Pure worker/store primitives for the registered multi-action V2 study.

The V2 worker deliberately reuses the audited V1 session identity, runtime
attestation, and create-only ownership machinery.  It does not alter the V1
worker or its historical receipt schema.  Importing this module has no side
effects and never starts a service.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from canonical_start import initial_state_projection
from scripts.opd.live_routing_launcher import (
    LOCK_COLLECTION,
    MONGO_COLLECTIONS,
    CreateOnlyCanonicalStore,
    LauncherError,
    PartialSeedError,
    SessionSpec,
    LaneConfig,
    _DetachedProcessGroup,
    _descendant_process_groups,
    _diagnostic_browser_process_groups,
    _direct_child_process_groups,
    _load_browser_ownership_sidecar,
    _load_mcp_ownership_sidecar,
    _parse_tool_json,
    _process_group_exists,
    _suspend_exact_process_group,
    _suspend_owned_process_group,
    _terminate_owned_process_group,
    sanitized_worker_environment,
    validate_process_lifecycle,
    validate_runtime_attestation,
)
from scripts.opd.live_routing_multi_action_diagnostic import (
    ACTIONS,
    expected_observation_fixture,
    multi_action_documents,
    route_registered_turn,
    semantic_gameplay_projection,
)
from tool_surface import validate_tool_call_arguments


PHASE_SCHEMA_VERSION = "kaetram.live-routing-multi-action-session-phase.v2"
TURN_SCHEMA_VERSION = "kaetram.live-routing-multi-action-turn.v2"


def _raw_record(result: Any, *, expected_name: str = "observe") -> dict[str, Any]:
    parsed = _parse_tool_json(result, expected_name=expected_name)
    raw = getattr(result, "text", None)
    if parsed is None or not isinstance(raw, str):
        return {
            "available": False,
            "raw_text": raw if isinstance(raw, str) else None,
            "raw_sha256": hashlib.sha256(raw.encode()).hexdigest() if isinstance(raw, str) else None,
            "semantic_projection": None,
        }
    try:
        projection = semantic_gameplay_projection(parsed)
        json.dumps(projection, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError):
        projection = None
    return {
        "available": projection is not None,
        "raw_text": raw,
        "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "semantic_projection": projection,
    }


class MultiActionCreateOnlyStore(CreateOnlyCanonicalStore):
    """Use V1's ownership/snapshot/cleanup logic with the frozen V2 seed."""

    def insert_canonical(self, username: str, trial_id: str) -> dict[str, Any]:
        absence = self.prove_absent([username])
        if absence["all_absent"] is not True:
            raise LauncherError("create-only multi-action seed refused: username exists")
        inserted: dict[str, str] = {}
        insertion_order: list[str] = []
        receipt: dict[str, Any] = {
            "database": "kaetram_e2e",
            "username": username,
            "trial_id": trial_id,
            "fixture_schema_version": "kaetram.multi-action-fixture.v2",
            "absence": absence,
            "inserted_ids": inserted,
            "insertion_order": insertion_order,
            "player_info_inserted_last": False,
        }
        try:
            lock = self.db[LOCK_COLLECTION].insert_one(
                {"_id": username, "trial_id": trial_id, "study": "multi-action-v2"}
            )
            inserted[LOCK_COLLECTION] = str(lock.inserted_id)
            insertion_order.append(LOCK_COLLECTION)
            documents = multi_action_documents(username)
            order = [name for name in MONGO_COLLECTIONS if name != "player_info"]
            order.append("player_info")
            for collection in order:
                result = self.db[collection].insert_one(documents[collection])
                inserted[collection] = str(result.inserted_id)
                insertion_order.append(collection)
            receipt["player_info_inserted_last"] = True
        except Exception as exc:
            raise PartialSeedError(
                f"create-only multi-action seed stopped after partial write: {exc}",
                receipt,
            ) from exc
        return receipt


async def session_worker(
    spec: SessionSpec,
    registration: Mapping[str, Any],
    *,
    action_order: list[str],
    mcp_session_factory: Callable[..., Any] | None = None,
    sleep: Callable[[float], Any] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Run one cold V2 treatment or reconnect session.

    Process-group launch and cleanup remain the responsibility of the audited
    parent lifecycle wrapper.  This function emits the same raw runtime
    attestation envelope required by that wrapper.
    """

    spec.validate()
    if sorted(action_order) != sorted(ACTIONS) or len(action_order) != 3:
        raise LauncherError("multi-action order is not an exact action permutation")
    if mcp_session_factory is None:
        from tests.e2e.helpers.mcp_client import mcp_session

        mcp_session_factory = mcp_session
    state_dir = Path(os.environ["KAETRAM_STATE_DIR"])
    state_dir.mkdir(parents=True, exist_ok=False)
    started = monotonic()
    phase: dict[str, Any] = {
        "schema_version": PHASE_SCHEMA_VERSION,
        "trial_id": spec.trial_id,
        "session_id": spec.session_id,
        "phase": spec.phase,
        "username": spec.username,
        "arm": spec.arm,
        "action_order": list(action_order),
        "runtime_attestation": None,
        "precondition": None,
        "turns": [],
        "candidate_call_ledger": [],
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
        python_executable=os.sys.executable,
    ) as handle:
        attestation_result = await handle.call_tool(
            "__diagnostic_runtime_attestation", {}
        )
        attestation = _parse_tool_json(
            attestation_result, expected_name="__diagnostic_runtime_attestation"
        )
        if attestation is None:
            raise LauncherError("multi-action runtime attestation unavailable")
        validate_runtime_attestation(
            attestation,
            spec,
            worker_pid=os.getpid(),
            worker_process_group=os.getpgrp(),
        )
        attestation_raw = attestation_result.text
        phase["runtime_attestation"] = {
            "raw_text": attestation_raw,
            "raw_sha256": hashlib.sha256(attestation_raw.encode("utf-8")).hexdigest(),
            "parsed": attestation,
        }
        if spec.phase == "reconnect":
            phase["reconnect"] = _raw_record(await handle.call_tool("observe", {}))
        else:
            before_result = await handle.call_tool("observe", {})
            before_parsed = _parse_tool_json(before_result, expected_name="observe")
            before_raw = before_result.text
            phase["precondition"] = {
                "available": before_parsed is not None,
                "raw_text": before_raw,
                "raw_sha256": hashlib.sha256(before_raw.encode("utf-8")).hexdigest(),
                "normalized_projection": (
                    initial_state_projection(before_parsed)
                    if isinstance(before_parsed, dict)
                    else None
                ),
            }
            precondition_ok = (
                phase["precondition"]["normalized_projection"]
                == expected_observation_fixture()
            )
            for sequence, action_name in enumerate(action_order, start=1):
                decision = route_registered_turn(spec.arm, action_name)
                call = decision["calls"][0] if len(decision["calls"]) == 1 else None
                schema_status = "not_applicable_no_candidate"
                dispatch_attempted = False
                delivery_status = "not_attempted"
                protocol_success = None
                result_raw = None
                result_sha = None
                result_json = None
                tool_error = None
                if precondition_ok and call is not None:
                    valid, _ = validate_tool_call_arguments(call["name"], call["args"])
                    schema_status = "valid" if valid else "invalid"
                    if valid:
                        dispatch_attempted = True
                        call_ledger_row = {
                            "sequence": sequence,
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
                            result_raw = result.text
                            result_sha = hashlib.sha256(result_raw.encode("utf-8")).hexdigest()
                            call_ledger_row.update(
                                delivery_status=delivery_status,
                                protocol_success=protocol_success,
                                result_raw_sha256=result_sha,
                            )
                            result_json = _parse_tool_json(result, expected_name=call["name"])
                            if isinstance(result_json, dict):
                                tool_error = result_json.get("error")
                immediate_at = monotonic()
                immediate = _raw_record(await handle.call_tool("observe", {}))
                minimum_delay = float(
                    registration["runtime_parameters"][
                        "minimum_delayed_observation_seconds"
                    ]
                )
                await sleep(max(0.0, immediate_at + minimum_delay - monotonic()))
                delayed = _raw_record(await handle.call_tool("observe", {}))
                delayed_at = monotonic()
                phase["turns"].append(
                    {
                        "schema_version": TURN_SCHEMA_VERSION,
                        "sequence": sequence,
                        "action": action_name,
                        "router_status": decision["status"],
                        "schema_status": schema_status,
                        "dispatch_attempted": dispatch_attempted,
                        "delivery_status": delivery_status,
                        "protocol_success": protocol_success,
                        "tool_reported_error": tool_error,
                        "result_json": result_json,
                        "result_raw_text": result_raw,
                        "result_raw_sha256": result_sha,
                        "immediate": immediate,
                        "delayed": delayed,
                        "delayed_elapsed_monotonic_seconds": delayed_at - immediate_at,
                    }
                )
    phase["worker_elapsed_seconds"] = monotonic() - started
    return phase


def run_session_worker(
    spec: SessionSpec,
    registration_path: Path,
    *,
    action_order: list[str],
    python_executable: Path,
    state_dir: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Spawn one owned V2 worker and prove all tagged process groups closed.

    This is a V2-only adapter around the same create-only ownership sidecars,
    exact process groups, and lifecycle validator used by V1. Unknown detached
    descendants or missing owner evidence fail closed.
    """

    spec.validate()
    environment = sanitized_worker_environment(
        os.environ, spec, lane=LaneConfig(), state_dir=state_dir
    )
    command = [
        str(python_executable.expanduser().absolute()),
        str(Path(__file__).resolve()),
        "session-worker",
        "--registration",
        str(registration_path.resolve()),
        "--spec-json",
        json.dumps(spec.__dict__, separators=(",", ":"), sort_keys=True),
        "--action-order-json",
        json.dumps(action_order, separators=(",", ":")),
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        start_new_session=True,
    )
    value: dict[str, Any] | None = None
    failure: BaseException | None = None
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        if process.returncode != 0:
            raise LauncherError(f"multi-action worker failed: {stderr[-2000:]}")
        value = json.loads(stdout)
        if not isinstance(value, dict):
            raise LauncherError("multi-action worker result is not an object")
    except subprocess.TimeoutExpired:
        failure = LauncherError("multi-action worker exceeded registered timeout")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        failure = LauncherError(f"multi-action worker output invalid: {exc}")
    except BaseException as exc:
        failure = exc

    if process.returncode is None:
        try:
            _suspend_owned_process_group(process)
        except BaseException as exc:
            failure = failure or exc

    mcp_owner = None
    browser_owner = None
    try:
        mcp_owner = _load_mcp_ownership_sidecar(state_dir, spec)
        browser_owner = _load_browser_ownership_sidecar(state_dir, spec)
    except BaseException as exc:
        failure = failure or exc
    attestation = None
    if isinstance(value, dict):
        evidence = value.get("runtime_attestation")
        if isinstance(evidence, dict):
            attestation = evidence.get("parsed")
    if not isinstance(attestation, dict):
        failure = failure or LauncherError("multi-action worker omitted runtime identity")
    else:
        try:
            validate_runtime_attestation(attestation, spec)
        except BaseException as exc:
            failure = failure or exc

    mcp_identity = mcp_owner.get("parsed") if isinstance(mcp_owner, dict) else None
    browser_identity = browser_owner.get("parsed") if isinstance(browser_owner, dict) else None
    if not isinstance(mcp_identity, dict) or not isinstance(browser_identity, dict):
        failure = failure or LauncherError("multi-action process ownership is incomplete")
    owned_groups = {process.pid}
    if isinstance(mcp_identity, dict):
        owned_groups.add(mcp_identity["mcp_process_group"])
    if isinstance(browser_identity, dict):
        owned_groups.add(browser_identity["browser_process_group"])
    discovered_groups: set[int] = set()
    if process.returncode is None:
        try:
            discovered_groups.update(_direct_child_process_groups(process.pid))
        except BaseException as exc:
            failure = failure or exc
    try:
        browser_groups, browser_leaders = _diagnostic_browser_process_groups(spec.session_id)
        discovered_groups.update(browser_groups)
        if browser_groups and not browser_leaders:
            failure = failure or LauncherError("diagnostic browser leader is unobservable")
    except BaseException as exc:
        failure = failure or exc
    unexpected = discovered_groups - owned_groups
    for group in sorted(owned_groups - {process.pid} | unexpected):
        try:
            _suspend_exact_process_group(group, label="multi-action owned descendant")
        except BaseException as exc:
            failure = failure or exc
    try:
        stabilized = False
        for _ in range(4):
            descendants, leaders = _descendant_process_groups(owned_groups | unexpected)
            if not leaders:
                failure = failure or LauncherError("detached descendant leader is unobservable")
            new_groups = descendants - owned_groups - unexpected
            if not new_groups:
                stabilized = True
                break
            unexpected.update(new_groups)
            for group in sorted(new_groups):
                _suspend_exact_process_group(group, label="unexpected multi-action descendant")
        if unexpected or not stabilized:
            failure = failure or LauncherError("unexpected detached descendant process group")
    except BaseException as exc:
        failure = failure or exc

    cleanup_rows: dict[str, dict[str, bool]] = {}
    targets = [
        (f"unexpected-{group}", group) for group in sorted(unexpected)
    ]
    if isinstance(browser_identity, dict):
        targets.append(("browser", browser_identity["browser_process_group"]))
    if isinstance(mcp_identity, dict):
        targets.append(("mcp", mcp_identity["mcp_process_group"]))
    targets.append(("worker", process.pid))
    for label, group in targets:
        try:
            cleanup_rows[label] = _terminate_owned_process_group(
                process if label == "worker" else _DetachedProcessGroup(group)
            )
        except BaseException as exc:
            failure = failure or exc
    if any(_process_group_exists(group) for _, group in targets):
        failure = failure or LauncherError("multi-action owned process group survived cleanup")
    if failure is not None:
        raise failure
    if value is None or mcp_owner is None or browser_owner is None or attestation is None:
        raise LauncherError("multi-action lifecycle evidence is incomplete")
    groups = {
        "worker": {
            "pid": process.pid,
            "process_group": process.pid,
            "identity_source": "spawned_worker",
            **cleanup_rows["worker"],
        },
        "mcp": {
            "pid": mcp_identity["mcp_pid"],
            "process_group": mcp_identity["mcp_process_group"],
            "identity_source": "mcp_owner_receipt",
            **cleanup_rows["mcp"],
        },
        "browser": {
            "pid": browser_identity["browser_pid"],
            "process_group": browser_identity["browser_process_group"],
            "identity_source": "browser_owner_receipt",
            **cleanup_rows["browser"],
        },
    }
    value["process_lifecycle"] = {
        "schema_version": "kaetram.session-lifecycle-cleanup.v1",
        "session_id": spec.session_id,
        "owner_receipts": {"mcp": mcp_owner, "browser": browser_owner},
        "groups": groups,
        "cleanup_order": ["browser", "mcp", "worker"],
        "unexpected_process_groups": [],
        "closure_proven": True,
    }
    validate_process_lifecycle(value["process_lifecycle"], spec, attestation)
    return value


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("session-worker",))
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--spec-json", required=True)
    parser.add_argument("--action-order-json", required=True)
    args = parser.parse_args(argv)
    try:
        registration = json.loads(args.registration.read_text(encoding="utf-8"))
        spec = SessionSpec(**json.loads(args.spec_json))
        action_order = json.loads(args.action_order_json)
        value = asyncio.run(
            session_worker(spec, registration, action_order=action_order)
        )
    except (OSError, ValueError, LauncherError) as exc:
        print(f"multi-action session worker refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
