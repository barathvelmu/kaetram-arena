#!/usr/bin/env python3
"""Parent-only orchestration for the frozen local routing diagnostic.

The module keeps all live dependencies injectable.  Importing it, and its unit
tests, never contact MongoDB, a browser, a game server, or the network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from canonical_start import database_state_projection  # noqa: E402
from scripts.opd.live_routing_analyzer import (  # noqa: E402
    TRIAL_SCHEMA_VERSION,
    analyze_run,
    canonical_json_bytes,
    canonical_sha256,
)
from scripts.opd.live_routing_launcher import (  # noqa: E402
    CreateOnlyCanonicalStore,
    LauncherError,
    PartialSeedError,
    SessionSpec,
    attest_game_checkout,
    run_session_worker,
    session_spec_from_plan,
    validate_runtime_attestation,
    validate_runtime_attestation_set,
)
from scripts.opd.live_routing_diagnostic import validate_registration  # noqa: E402
from scripts.opd.live_routing_prelaunch import (  # noqa: E402
    READY_STATUS,
    load_json_strict,
    verify_prelaunch_receipt,
)
from scripts.opd.live_routing_result_verify import (  # noqa: E402
    MANIFEST_SCHEMA_VERSION,
    validate_runtime_preflight,
    verify_package_or_raise,
)


RECEIPT_DIR = "receipts"
MINIMUM_SETTLE_SECONDS = 1.5
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
EXPECTED_INSERTION_ORDER = (
    "live_routing_diagnostic_locks",
    "player_inventory",
    "player_bank",
    "player_equipment",
    "player_quests",
    "player_achievements",
    "player_skills",
    "player_statistics",
    "player_abilities",
    "player_info",
)
RUNTIME_PACKAGE_NAMES = ("mcp", "playwright", "pymongo")


class OrchestrationError(RuntimeError):
    """The parent cannot preserve the registered execution contract."""


class Store(Protocol):
    def attest_topology(self) -> dict[str, Any]: ...
    def prove_absent(self, usernames: Sequence[str]) -> dict[str, Any]: ...
    def insert_canonical(self, username: str, trial_id: str) -> dict[str, Any]: ...
    def snapshot_owned(
        self, username: str, inserted_ids: Mapping[str, str]
    ) -> dict[str, Any]: ...
    def cleanup_owned(
        self, username: str, trial_id: str, inserted_ids: Mapping[str, str]
    ) -> dict[str, Any]: ...
    def close(self) -> None: ...


WorkerRunner = Callable[[SessionSpec], dict[str, Any]]
TrialCompletedCallback = Callable[["TrialExecution"], None]


@dataclass
class TrialExecution:
    plan: dict[str, Any]
    seed: dict[str, Any]
    treatment: dict[str, Any]
    reconnect: dict[str, Any]
    database_snapshot: dict[str, Any]
    cleanup: dict[str, Any]
    parent_event_ledger: list[dict[str, Any]]


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def attest_python_runtime(
    python_executable: Path,
    live_contract: Mapping[str, Any],
    *,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    """Attest the selected local interpreter without importing its packages here."""

    invoked = python_executable.expanduser().absolute()
    executable = invoked.resolve()
    if not executable.is_file():
        raise OrchestrationError("registered Python executable is missing")
    probe = (
        "import importlib.metadata,json,platform,sys;"
        "print(json.dumps({'python_version':platform.python_version(),"
        "'mcp_version':importlib.metadata.version('mcp'),"
        "'playwright_version':importlib.metadata.version('playwright'),"
        "'pymongo_version':importlib.metadata.version('pymongo')},sort_keys=True))"
    )
    try:
        result = command_runner(
            [str(invoked), "-I", "-c", probe],
            capture_output=True,
            text=True,
            check=False,
            env={"PATH": os.environ.get("PATH", ""), "PYTHONNOUSERSITE": "1"},
        )
    except OSError as exc:
        raise OrchestrationError("registered Python runtime probe is unavailable") from exc
    if result.returncode != 0:
        raise OrchestrationError("registered Python runtime probe failed")
    try:
        versions = json.loads(result.stdout)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise OrchestrationError("registered Python runtime probe returned invalid JSON") from exc
    expected_keys = {
        "python_version",
        "mcp_version",
        "playwright_version",
        "pymongo_version",
    }
    if not isinstance(versions, dict) or set(versions) != expected_keys:
        raise OrchestrationError("registered Python runtime probe key set drift")
    receipt = {
        **versions,
        "python_executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
    }
    for key, actual in receipt.items():
        if live_contract.get(key) != actual:
            raise OrchestrationError(
                f"local runtime differs from registration: {key}"
            )
    return receipt


def validate_registered_browser_runtime(
    attestation: Mapping[str, Any], live_contract: Mapping[str, Any]
) -> None:
    expected = {
        "browser_name": live_contract.get("browser_name"),
        "browser_version": live_contract.get("browser_version"),
        "browser_executable_sha256": live_contract.get(
            "browser_executable_sha256"
        ),
    }
    if any(attestation.get(key) != value for key, value in expected.items()):
        raise OrchestrationError("browser runtime differs from registration")


def _strict_mapping(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise OrchestrationError(f"{label} key set drift")
    return value


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish_bytes_create_only(path: Path, raw: bytes, *, mode: int = 0o444) -> str:
    """Publish one immutable file with exclusive creation and durable metadata."""

    path = path.resolve()
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink():
        raise OrchestrationError("artifact parent must be an existing regular directory")
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OrchestrationError("artifact publication made no progress")
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        _fsync_directory(parent)
    except FileExistsError as exc:
        raise OrchestrationError(f"refusing to overwrite artifact: {path}") from exc
    except OSError as exc:
        raise OrchestrationError(f"artifact publication failed: {path}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return _sha256_bytes(raw)


def publish_json_create_only(path: Path, value: dict[str, Any]) -> str:
    return publish_bytes_create_only(path, canonical_json_bytes(value) + b"\n")


def publish_failure_receipt(
    result_root: Path, *, stage: str, error: BaseException
) -> dict[str, Any]:
    """Retain an immutable, explicitly non-final record for an aborted run."""

    receipt: dict[str, Any] = {
        "schema_version": "kaetram.live-routing-failure.v1",
        "status": "incomplete_not_scientifically_reportable",
        "stage": stage,
        "error_type": type(error).__name__,
        "message": str(error)[:2000],
        "notes": list(getattr(error, "__notes__", ())),
        "cleanup_failure": getattr(error, "cleanup_failure", None),
    }
    receipt["payload_sha256"] = canonical_sha256(receipt)
    publish_json_create_only(result_root / "failure.json", receipt)
    return receipt


def create_result_root(path: Path, *, protected_roots: Sequence[Path]) -> Path:
    """Create an empty result root, refusing repositories and broad targets."""

    path = path.expanduser().resolve()
    if path in {Path("/"), Path.home().resolve()}:
        raise OrchestrationError("result root is too broad")
    for protected in protected_roots:
        protected = protected.expanduser().resolve()
        if path == protected or protected in path.parents:
            raise OrchestrationError("result root must be outside every source repository")
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink():
        raise OrchestrationError("result-root parent must be an existing regular directory")
    try:
        path.mkdir(mode=0o700)
        (path / RECEIPT_DIR).mkdir(mode=0o700)
    except FileExistsError as exc:
        raise OrchestrationError("result root already exists") from exc
    _fsync_directory(path / RECEIPT_DIR)
    _fsync_directory(path)
    _fsync_directory(parent)
    return path


def _validate_plan_sequence(plans: Any) -> list[dict[str, Any]]:
    if not isinstance(plans, list) or len(plans) != 9:
        raise OrchestrationError("prelaunch must contain exactly nine trials")
    if [plan.get("schedule_index") for plan in plans if isinstance(plan, dict)] != list(
        range(1, 10)
    ):
        raise OrchestrationError("trial schedule is not exact and contiguous")
    usernames = [plan.get("username") for plan in plans]
    if len(set(usernames)) != 9:
        raise OrchestrationError("trial usernames are not unique")
    return plans


def _record_event(
    ledger: list[dict[str, Any]], event: str, monotonic: Callable[[], float]
) -> float:
    value = float(monotonic())
    if ledger and value <= ledger[-1]["monotonic_seconds"]:
        raise OrchestrationError("parent monotonic event times are not strictly ordered")
    ledger.append({"event": event, "monotonic_seconds": value})
    return value


def _settle(
    ledger: list[dict[str, Any]],
    *,
    finished_event: str,
    settle_event: str,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    minimum_seconds: float,
) -> None:
    finished = _record_event(ledger, finished_event, monotonic)
    sleep(max(0.0, finished + minimum_seconds - monotonic()))
    settled = _record_event(ledger, settle_event, monotonic)
    if settled - finished < minimum_seconds:
        raise OrchestrationError(f"{settle_event} violated the registered settle interval")


def _runtime_evidence(
    phase: dict[str, Any],
    spec: SessionSpec,
    live_contract: Mapping[str, Any],
) -> dict[str, Any]:
    evidence = _strict_mapping(
        phase.get("runtime_attestation"),
        {"raw_text", "raw_sha256", "parsed"},
        f"{spec.phase} runtime evidence",
    )
    raw = evidence["raw_text"]
    if not isinstance(raw, str) or evidence["raw_sha256"] != hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest():
        raise OrchestrationError("runtime attestation raw evidence digest mismatch")
    process_group = evidence["parsed"].get("mcp_process_group")
    validate_runtime_attestation(
        evidence["parsed"],
        spec,
        worker_pid=process_group,
        worker_process_group=process_group,
    )
    validate_registered_browser_runtime(evidence["parsed"], live_contract)
    return evidence


def _incremental_coldness(
    prior: Sequence[tuple[SessionSpec, dict[str, Any]]],
    spec: SessionSpec,
    evidence: dict[str, Any],
) -> None:
    parsed = evidence["parsed"]
    for field in (
        "mcp_pid",
        "mcp_process_group",
        "mcp_instance_nonce",
        "browser_launch_nonce",
    ):
        if any(old[field] == parsed[field] for _, old in prior):
            raise OrchestrationError(f"cold runtime identity reused: {field}")
    if any(old_spec.session_id == spec.session_id for old_spec, _ in prior):
        raise OrchestrationError("session identity reused")


def run_exact_trial_sequence(
    plans: list[dict[str, Any]],
    registration: dict[str, Any],
    *,
    store: Store,
    worker_runner: WorkerRunner,
    global_absence: dict[str, Any],
    on_trial_completed: TrialCompletedCallback | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[TrialExecution], list[tuple[SessionSpec, dict[str, Any]]]]:
    """Run the registered schedule; all service behavior is injected."""

    plans = _validate_plan_sequence(plans)
    if global_absence.get("database") != "kaetram_e2e" or global_absence.get(
        "all_absent"
    ) is not True:
        raise OrchestrationError("global username absence was not established")
    minimum = registration["runtime_parameters"][
        "minimum_disconnect_settle_seconds"
    ]
    if type(minimum) not in (int, float) or minimum < MINIMUM_SETTLE_SECONDS:
        raise OrchestrationError("registered disconnect settle interval is unsafe")

    executions: list[TrialExecution] = []
    runtimes: list[tuple[SessionSpec, dict[str, Any]]] = []
    for plan in plans:
        ledger: list[dict[str, Any]] = []
        _record_event(ledger, "absence_confirmed", monotonic)
        username = plan["username"]
        trial_id = plan["trial_id"]
        seed: dict[str, Any] | None = None
        inserted_ids: Mapping[str, str] | None = None
        treatment: dict[str, Any] | None = None
        reconnect: dict[str, Any] | None = None
        snapshot: dict[str, Any] | None = None
        cleanup: dict[str, Any] | None = None
        original_error: BaseException | None = None
        original_traceback = None
        try:
            try:
                seed = store.insert_canonical(username, trial_id)
            except PartialSeedError as exc:
                seed = exc.receipt
                raise
            inserted_ids = seed.get("inserted_ids")
            if not isinstance(inserted_ids, dict):
                raise OrchestrationError("seed receipt omitted inserted identities")
            _record_event(ledger, "seed_completed", monotonic)

            treatment_spec = session_spec_from_plan(plan, "treatment")
            _record_event(ledger, "treatment_started", monotonic)
            treatment = worker_runner(treatment_spec)
            treatment_runtime = _runtime_evidence(
                treatment, treatment_spec, registration["live_contract"]
            )
            _incremental_coldness(runtimes, treatment_spec, treatment_runtime)
            runtimes.append((treatment_spec, treatment_runtime["parsed"]))
            _settle(
                ledger,
                finished_event="treatment_finished",
                settle_event="treatment_settle_finished",
                monotonic=monotonic,
                sleep=sleep,
                minimum_seconds=float(minimum),
            )

            reconnect_spec = session_spec_from_plan(plan, "reconnect")
            _record_event(ledger, "reconnect_started", monotonic)
            reconnect = worker_runner(reconnect_spec)
            reconnect_runtime = _runtime_evidence(
                reconnect, reconnect_spec, registration["live_contract"]
            )
            _incremental_coldness(runtimes, reconnect_spec, reconnect_runtime)
            runtimes.append((reconnect_spec, reconnect_runtime["parsed"]))
            _settle(
                ledger,
                finished_event="reconnect_finished",
                settle_event="reconnect_settle_finished",
                monotonic=monotonic,
                sleep=sleep,
                minimum_seconds=float(minimum),
            )

            snapshot = store.snapshot_owned(username, inserted_ids)
            _record_event(ledger, "database_snapshot_recorded", monotonic)
        except BaseException as exc:
            original_error = exc
            original_traceback = exc.__traceback__

        cleanup_error: BaseException | None = None
        if seed is not None:
            candidate_ids = seed.get("inserted_ids")
            if isinstance(candidate_ids, Mapping):
                inserted_ids = candidate_ids
                try:
                    cleanup = store.cleanup_owned(username, trial_id, inserted_ids)
                    _record_event(ledger, "cleanup_completed", monotonic)
                except BaseException as exc:
                    cleanup_error = exc
            else:
                cleanup_error = OrchestrationError(
                    "seed receipt omitted cleanup ownership identities"
                )

        if original_error is not None:
            if cleanup_error is not None:
                detail = {
                    "error_type": type(cleanup_error).__name__,
                    "message": str(cleanup_error)[:2000],
                    "cleanup_receipt": getattr(
                        cleanup_error, "cleanup_receipt", None
                    ),
                }
                original_error.add_note(
                    "ownership cleanup also failed: "
                    f"{detail['error_type']}: {detail['message']}"
                )
                setattr(original_error, "cleanup_failure", detail)
            raise original_error.with_traceback(original_traceback)
        if cleanup_error is not None:
            raise cleanup_error
        if not all(
            isinstance(value, dict)
            for value in (seed, treatment, reconnect, snapshot, cleanup)
        ):
            raise OrchestrationError("completed trial evidence is incomplete")
        # A fully identified but incomplete cleanup is retained as an invalid
        # scientific outcome.  The next trial has a distinct username, so it
        # remains safe to continue. Unknown ownership still raises in the store.
        if cleanup.get("absence", {}).get("all_absent") is True:
            _record_event(ledger, "cleanup_absence_confirmed", monotonic)
        execution = TrialExecution(
            plan=plan,
            seed=seed,
            treatment=treatment,
            reconnect=reconnect,
            database_snapshot=snapshot,
            cleanup=cleanup,
            parent_event_ledger=ledger,
        )
        executions.append(execution)
        if on_trial_completed is not None:
            on_trial_completed(execution)
    validate_runtime_attestation_set(runtimes)
    return executions, runtimes


def _measurement(value: Any, label: str) -> dict[str, Any]:
    return _strict_mapping(
        value,
        {"available", "raw_text", "raw_sha256", "normalized_projection"},
        label,
    )


def _execution_evidence(execution: TrialExecution) -> dict[str, Any]:
    seed = execution.seed
    insertion_order = seed.get("insertion_order")
    if tuple(insertion_order or ()) != EXPECTED_INSERTION_ORDER:
        raise OrchestrationError("seed insertion order evidence drift")
    inserted_ids = seed.get("inserted_ids")
    if not isinstance(inserted_ids, dict) or set(inserted_ids) != set(
        EXPECTED_INSERTION_ORDER
    ):
        raise OrchestrationError("seed inserted-id evidence is incomplete")
    snapshot = execution.database_snapshot
    documents = snapshot.get("documents")
    if not isinstance(documents, dict) or set(documents) != set(MONGO_COLLECTIONS):
        raise OrchestrationError("owned database snapshot is incomplete")
    document_ids = {name: str(documents[name].get("_id", "")) for name in MONGO_COLLECTIONS}
    if any(not value or value != inserted_ids[name] for name, value in document_ids.items()):
        raise OrchestrationError("database snapshot ownership differs from seed")
    cleanup = execution.cleanup
    absence = cleanup.get("absence", {})
    seed_absence = seed.get("absence", {})
    candidate_ledger = execution.treatment.get("candidate_call_ledger")
    if not isinstance(candidate_ledger, list):
        raise OrchestrationError("candidate-call ledger is missing")
    return {
        "absence": {
            "database": seed_absence.get("database"),
            "username": execution.plan["username"],
            "counts": seed_absence.get("counts", {}).get(execution.plan["username"]),
            "all_absent": seed_absence.get("all_absent"),
        },
        "seed": {
            "database": seed.get("database"),
            "username": seed.get("username"),
            "trial_id": seed.get("trial_id"),
            "inserted_ids": inserted_ids,
            "insertion_order": insertion_order,
            "player_info_inserted_last": seed.get("player_info_inserted_last"),
        },
        "runtime_attestations": {
            "treatment": execution.treatment["runtime_attestation"],
            "reconnect": execution.reconnect["runtime_attestation"],
        },
        "parent_event_ledger": execution.parent_event_ledger,
        "candidate_call_ledger": candidate_ledger,
        "database_snapshot_ownership": {
            "database": snapshot.get("database"),
            "username": snapshot.get("username"),
            "document_ids": document_ids,
        },
        "cleanup": {
            "database": cleanup.get("database"),
            "username": execution.plan["username"],
            "trial_id": execution.plan["trial_id"],
            "deleted_counts": cleanup.get("deleted"),
            "lock_deleted": cleanup.get("lock_deleted"),
            "post_cleanup_counts": absence.get("counts", {}).get(
                execution.plan["username"]
            ),
            "all_absent": absence.get("all_absent"),
        },
    }


def assemble_trial_receipt(
    execution: TrialExecution,
    prelaunch: dict[str, Any],
    *,
    previous_payload_sha256: str,
) -> dict[str, Any]:
    """Assemble raw evidence only; the analyzer derives all verdict fields."""

    database_raw = canonical_json_bytes(execution.database_snapshot).decode("utf-8")
    database_projection = database_state_projection(execution.database_snapshot)
    database_measurement = {
        "available": True,
        "raw_text": database_raw,
        "raw_sha256": hashlib.sha256(database_raw.encode("utf-8")).hexdigest(),
        "normalized_projection": database_projection,
    }
    precondition = _measurement(execution.treatment.get("precondition"), "precondition")
    immediate = _measurement(execution.treatment.get("immediate"), "immediate")
    delayed = _measurement(execution.treatment.get("delayed"), "delayed")
    reconnect = _measurement(execution.reconnect.get("reconnect"), "reconnect")
    receipt: dict[str, Any] = {
        "schema_version": TRIAL_SCHEMA_VERSION,
        "study_id": prelaunch["study_id"],
        "run_id": prelaunch["run_id"],
        "registration_sha256": prelaunch["registration"]["sha256"],
        "claim_contract_sha256": prelaunch["claim_contract_sha256"],
        "prelaunch_payload_sha256": prelaunch["payload_sha256"],
        "trial_plan_sha256": prelaunch["trial_plan_sha256"],
        "previous_receipt_payload_sha256": previous_payload_sha256,
        "plan": execution.plan,
        "observed_identity": {
            "username": execution.plan["username"],
            "treatment_session_id": execution.plan["treatment_session_id"],
            "reconnect_session_id": execution.plan["reconnect_session_id"],
            "database_player_id": execution.seed["inserted_ids"]["player_info"],
        },
        "precondition": precondition,
        "routing": execution.treatment.get("routing"),
        "measurements": {
            "immediate": immediate,
            "delayed": delayed,
            "reconnect": reconnect,
            "database": database_measurement,
            "delayed_elapsed_monotonic_seconds": execution.treatment.get(
                "delayed_elapsed_monotonic_seconds"
            ),
        },
        "execution_evidence": _execution_evidence(execution),
    }
    receipt["payload_sha256"] = canonical_sha256(receipt)
    return receipt


def publish_trial_receipt(
    result_root: Path,
    execution: TrialExecution,
    prelaunch: dict[str, Any],
    *,
    previous_payload_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Publish one completed trial at its final immutable package path."""

    receipt = assemble_trial_receipt(
        execution,
        prelaunch,
        previous_payload_sha256=previous_payload_sha256,
    )
    index = execution.plan.get("schedule_index")
    if type(index) is not int or index < 1 or index > 9:
        raise OrchestrationError("completed trial schedule index is invalid")
    relative = f"receipts/trial-{index:02d}.json"
    file_sha = publish_json_create_only(result_root / relative, receipt)
    entry = {
        "schedule_index": index,
        "path": relative,
        "file_sha256": file_sha,
        "receipt_payload_sha256": receipt["payload_sha256"],
    }
    return receipt, entry


def publish_completed_package(
    result_root: Path,
    *,
    registration: dict[str, Any],
    prelaunch: dict[str, Any],
    prelaunch_raw: bytes,
    runtime_preflight: dict[str, Any],
    prelaunch_file_sha256: str,
    runtime_preflight_file_sha256: str,
    receipts: list[dict[str, Any]],
    entries: list[dict[str, Any]],
    registration_path: Path,
    repo_root: Path,
    expected_head: str,
    verifier: Callable[..., dict[str, Any]] = verify_package_or_raise,
) -> dict[str, Any]:
    """Publish the exact verifier package and immediately verify it offline."""

    if len(receipts) != 9 or len(entries) != 9:
        raise OrchestrationError("final package requires nine published trial receipts")
    previous = prelaunch["payload_sha256"]
    for index, (receipt, entry) in enumerate(zip(receipts, entries, strict=True), start=1):
        expected_path = f"receipts/trial-{index:02d}.json"
        if (
            receipt.get("previous_receipt_payload_sha256") != previous
            or entry.get("schedule_index") != index
            or entry.get("path") != expected_path
            or entry.get("receipt_payload_sha256") != receipt.get("payload_sha256")
        ):
            raise OrchestrationError("published trial receipt chain is inconsistent")
        path = result_root / expected_path
        if not path.is_file() or _sha256_bytes(path.read_bytes()) != entry.get(
            "file_sha256"
        ):
            raise OrchestrationError("published trial receipt file identity drift")
        previous = receipt["payload_sha256"]
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "study_id": prelaunch["study_id"],
        "run_id": prelaunch["run_id"],
        "registration_sha256": prelaunch["registration"]["sha256"],
        "prelaunch_file_sha256": prelaunch_file_sha256,
        "prelaunch_payload_sha256": prelaunch["payload_sha256"],
        "runtime_preflight_file_sha256": runtime_preflight_file_sha256,
        "runtime_preflight_payload_sha256": runtime_preflight["payload_sha256"],
        "claim_contract_sha256": prelaunch["claim_contract_sha256"],
        "trial_plan_sha256": prelaunch["trial_plan_sha256"],
        "entries": entries,
        "final_chain_head": receipts[-1]["payload_sha256"],
    }
    manifest["payload_sha256"] = canonical_sha256(manifest)
    publish_json_create_only(result_root / "manifest.json", manifest)
    analysis = analyze_run(
        registration,
        prelaunch,
        receipts,
        manifest_payload_sha256=manifest["payload_sha256"],
    )
    publish_json_create_only(result_root / "analysis.json", analysis)
    verified = verifier(
        result_root,
        registration_path,
        repo_root=repo_root,
        expected_head=expected_head,
    )
    return {"manifest": manifest, "analysis": analysis, "verified": verified}


def build_runtime_preflight(
    prelaunch: Mapping[str, Any],
    *,
    game: dict[str, Any],
    mongo: dict[str, Any],
    python: dict[str, Any],
    services: dict[str, Any],
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": "kaetram.live-routing-runtime-preflight.v2",
        "study_id": prelaunch["study_id"],
        "run_id": prelaunch["run_id"],
        "registration_sha256": prelaunch["registration"]["sha256"],
        "prelaunch_payload_sha256": prelaunch["payload_sha256"],
        "game": game,
        "mongo": mongo,
        "python": python,
        "services": services,
    }
    record["payload_sha256"] = canonical_sha256(record)
    return record


def run_orchestration(
    *,
    registration_path: Path,
    prelaunch_path: Path,
    result_root: Path,
    repo_root: Path,
    expected_head: str,
    game_root: Path,
    python_executable: Path,
    services_evidence: Mapping[str, Any],
    store_factory: Callable[[], Store] = CreateOnlyCanonicalStore,
    game_attestor: Callable[[Path, dict[str, Any]], dict[str, Any]] = attest_game_checkout,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run and verify the registered study. No service is started by this parent."""

    registration, _ = load_json_strict(registration_path)
    prelaunch, _ = load_json_strict(prelaunch_path)
    if registration.get("status") != READY_STATUS:
        raise OrchestrationError("registration is not live-ready")
    errors = verify_prelaunch_receipt(
        prelaunch_path,
        registration_path,
        repo_root=repo_root,
        expected_head=expected_head,
    )
    if errors:
        raise OrchestrationError("prelaunch verification failed: " + "; ".join(errors))
    plans = _validate_plan_sequence(prelaunch.get("trials"))
    game_attestation = game_attestor(game_root, registration)
    expected_client_inventory = registration["live_contract"].get(
        "client_dist_inventory_sha256"
    )
    if game_attestation.get("client_dist_inventory_sha256") != expected_client_inventory:
        raise OrchestrationError("game-client distribution differs from registration")
    runtime_attestation = attest_python_runtime(
        python_executable, registration["live_contract"]
    )
    root = create_result_root(result_root, protected_roots=(repo_root, game_root))
    stage = "store_initialization"
    store: Store | None = None
    try:
        store = store_factory()
        stage = "mongo_preflight"
        mongo_attestation = store.attest_topology()
        runtime_preflight = build_runtime_preflight(
            prelaunch,
            game=game_attestation,
            mongo=mongo_attestation,
            python=runtime_attestation,
            services=dict(services_evidence),
        )
        validate_runtime_preflight(
            runtime_preflight,
            registration=registration,
            registration_sha256=prelaunch["registration"]["sha256"],
            prelaunch=prelaunch,
        )
        prelaunch_raw = prelaunch_path.read_bytes()
        prelaunch_file_sha = publish_bytes_create_only(
            root / "prelaunch.json", prelaunch_raw
        )
        runtime_preflight_file_sha = publish_json_create_only(
            root / "runtime-preflight.json", runtime_preflight
        )
        stage = "global_absence"
        usernames = [plan["username"] for plan in plans]
        global_absence = store.prove_absent(usernames)
        if global_absence.get("all_absent") is not True:
            raise OrchestrationError("one or more registered usernames already exist")

        timeout = float(registration["runtime_parameters"]["mcp_call_timeout_seconds"])
        published_receipts: list[dict[str, Any]] = []
        published_entries: list[dict[str, Any]] = []
        previous_payload_sha256 = prelaunch["payload_sha256"]

        def publish_completed_trial(execution: TrialExecution) -> None:
            nonlocal previous_payload_sha256
            receipt, entry = publish_trial_receipt(
                root,
                execution,
                prelaunch,
                previous_payload_sha256=previous_payload_sha256,
            )
            published_receipts.append(receipt)
            published_entries.append(entry)
            previous_payload_sha256 = receipt["payload_sha256"]

        with tempfile.TemporaryDirectory(
            prefix=f"kaetram-live-routing-{prelaunch['run_id']}-"
        ) as temporary_state_root:
            temporary_state_root_path = Path(temporary_state_root)

            def worker(spec: SessionSpec) -> dict[str, Any]:
                state_dir = temporary_state_root_path / spec.session_id
                return run_session_worker(
                    spec,
                    registration_path,
                    python_executable=python_executable,
                    state_dir=state_dir,
                    timeout_seconds=timeout,
                )

            stage = "trial_execution"
            executions, _ = run_exact_trial_sequence(
                plans,
                registration,
                store=store,
                worker_runner=worker,
                global_absence=global_absence,
                on_trial_completed=publish_completed_trial,
                monotonic=monotonic,
                sleep=sleep,
            )
        stage = "source_postflight_attestation"
        postflight_source_errors = verify_prelaunch_receipt(
            prelaunch_path,
            registration_path,
            repo_root=repo_root,
            expected_head=expected_head,
        )
        if postflight_source_errors:
            raise OrchestrationError(
                "source/prelaunch identity drifted during trials: "
                + "; ".join(postflight_source_errors)
            )
        stage = "runtime_postflight_attestation"
        if attest_python_runtime(
            python_executable, registration["live_contract"]
        ) != runtime_attestation:
            raise OrchestrationError("Python runtime identity drifted during trials")
        if game_attestor(game_root, registration) != game_attestation:
            raise OrchestrationError("game runtime identity drifted during trials")
        if store.attest_topology() != mongo_attestation:
            raise OrchestrationError("Mongo runtime identity drifted during trials")
        store.close()
        store = None
        stage = "package_publication_and_verification"
        package = publish_completed_package(
            root,
            registration=registration,
            prelaunch=prelaunch,
            prelaunch_raw=prelaunch_raw,
            runtime_preflight=runtime_preflight,
            prelaunch_file_sha256=prelaunch_file_sha,
            runtime_preflight_file_sha256=runtime_preflight_file_sha,
            receipts=published_receipts,
            entries=published_entries,
            registration_path=registration_path,
            repo_root=repo_root,
            expected_head=expected_head,
        )
    except BaseException as exc:
        if store is not None:
            try:
                store.close()
            except BaseException as close_error:
                detail = {
                    "error_type": type(close_error).__name__,
                    "message": str(close_error)[:2000],
                }
                exc.add_note(
                    "store close also failed: "
                    f"{detail['error_type']}: {detail['message']}"
                )
        try:
            publish_failure_receipt(root, stage=stage, error=exc)
        except BaseException:
            pass
        raise
    return {
        "result_root": str(root),
        "game_attestation": game_attestation,
        "python_runtime_attestation": runtime_attestation,
        "mongo_attestation": mongo_attestation,
        **package,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--prelaunch", type=Path, required=True)
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--game-root", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--docker", type=Path, default=Path("/usr/local/bin/docker"))
    parser.add_argument(
        "--node", type=Path, default=Path("/opt/homebrew/opt/node@20/bin/node")
    )
    args = parser.parse_args(argv)
    try:
        # Import lazily so offline inspection of this module never touches the
        # local service layer. The callback receives the exact evidence while
        # the three owned loopback services are still alive.
        from scripts.opd.live_routing_services import (  # noqa: PLC0415
            ServiceConfig,
            ServiceError,
            run_with_local_services,
        )

        registration, _ = load_json_strict(args.registration)
        if registration.get("status") != READY_STATUS:
            raise OrchestrationError("registration is not live-ready")
        registration_errors = validate_registration(
            registration, expected_status=READY_STATUS
        )
        if registration_errors:
            raise OrchestrationError(
                "registration contract invalid: " + "; ".join(registration_errors)
            )
        prelaunch_errors = verify_prelaunch_receipt(
            args.prelaunch,
            args.registration,
            repo_root=args.repo_root,
            expected_head=args.expected_head,
        )
        if prelaunch_errors:
            raise OrchestrationError(
                "pre-service prelaunch verification failed: "
                + "; ".join(prelaunch_errors)
            )
        live = registration["live_contract"]
        # Fail before starting Docker/Node if the registered local Python or
        # package versions drifted. run_orchestration repeats this check.
        attest_python_runtime(args.python, live)
        config = ServiceConfig(
            game_root=args.game_root,
            game_revision=live["game_revision"],
            server_bundle_sha256=live["game_bundle_sha256"],
            client_dist_inventory_sha256=live[
                "client_dist_inventory_sha256"
            ],
            python_binary=args.python,
            node_version=live["node_version"],
            node_executable_sha256=live["node_executable_sha256"],
            docker_client_version=live["docker_client_version"],
            docker_executable_sha256=live["docker_executable_sha256"],
            docker_binary=args.docker,
            node_binary=args.node,
            readiness_timeout_seconds=float(
                registration["runtime_parameters"][
                    "service_readiness_timeout_seconds"
                ]
            ),
        )

        def orchestrate(services_evidence: dict[str, Any]) -> dict[str, Any]:
            return run_orchestration(
                registration_path=args.registration,
                prelaunch_path=args.prelaunch,
                result_root=args.result_root,
                repo_root=args.repo_root,
                expected_head=args.expected_head,
                game_root=args.game_root,
                python_executable=args.python,
                services_evidence=services_evidence,
            )

        result = run_with_local_services(config, orchestrate)
    except (LauncherError, OrchestrationError, ServiceError, OSError, ValueError) as exc:
        print(f"live routing orchestration refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, allow_nan=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
