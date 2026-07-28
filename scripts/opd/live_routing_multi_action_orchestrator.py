#!/usr/bin/env python3
"""Parent-only execution and immutable packaging for multi-action V2.

Local services are intentionally injected by the audited service owner.  This
module starts no model and accepts no remote endpoint.  It reuses the V1
create-only result-root, runtime attestation, game attestation, trial sequence,
session-settle, and ownership cleanup machinery while emitting only V2 receipt
schemas.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Callable

from scripts.opd.live_routing_launcher import (
    SessionSpec,
    attest_game_checkout,
)
from scripts.opd.live_routing_orchestrator import (
    TrialExecution,
    attest_python_runtime,
    build_runtime_preflight,
    create_result_root,
    publish_bytes_create_only,
    publish_failure_receipt,
    publish_json_create_only,
    run_exact_trial_sequence,
)
from scripts.opd.live_routing_result_verify import validate_runtime_preflight
from scripts.opd.live_routing_multi_action_analyzer import (
    analyze_run,
    assemble_trial_receipt,
)
from scripts.opd.live_routing_multi_action_diagnostic import (
    canonical_sha256,
    load_registration_strict,
    validate_registration,
)
from scripts.opd.live_routing_multi_action_launcher import (
    MultiActionCreateOnlyStore,
    run_session_worker,
)
from scripts.opd.live_routing_multi_action_prelaunch import verify_prelaunch
from scripts.opd.live_routing_multi_action_result_verify import (
    MANIFEST_SCHEMA_VERSION,
    verify_package,
)


class MultiActionOrchestrationError(RuntimeError):
    pass


def _file_row(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    raw = path.read_bytes()
    return {
        "path": relative,
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def run_orchestration(
    *,
    registration_path: Path,
    prelaunch_path: Path,
    result_root: Path,
    repo_root: Path,
    game_root: Path,
    python_executable: Path,
    services_evidence: dict[str, Any],
    store_factory: Callable[[], Any] = MultiActionCreateOnlyStore,
    game_attestor: Callable[[Path, dict[str, Any]], dict[str, Any]] = attest_game_checkout,
) -> dict[str, Any]:
    registration_path = registration_path.resolve()
    prelaunch_path = prelaunch_path.resolve()
    repo_root = repo_root.resolve()
    prelaunch = verify_prelaunch(
        prelaunch_path, registration_path, repo_root=repo_root, require_clean_head=True
    )
    registration = load_registration_strict(registration_path)
    errors = validate_registration(registration)
    if errors:
        raise MultiActionOrchestrationError("registration invalid: " + "; ".join(errors))
    root = create_result_root(
        result_root,
        protected_roots=(repo_root, game_root.resolve()),
    )
    stage = "runtime_preflight"
    store = None
    receipts: list[dict[str, Any]] = []
    try:
        python_receipt = attest_python_runtime(
            python_executable, registration["live_contract"]
        )
        game_receipt = game_attestor(game_root, registration)
        store = store_factory()
        mongo_receipt = store.attest_topology()
        runtime_preflight = build_runtime_preflight(
            prelaunch,
            game=game_receipt,
            mongo=mongo_receipt,
            python=python_receipt,
            services=services_evidence,
        )
        validate_runtime_preflight(
            runtime_preflight,
            registration=registration,
            registration_sha256=prelaunch["registration"]["sha256"],
            prelaunch=prelaunch,
        )
        publish_bytes_create_only(root / "registration.json", registration_path.read_bytes())
        publish_bytes_create_only(root / "prelaunch.json", prelaunch_path.read_bytes())
        publish_json_create_only(root / "runtime-preflight.json", runtime_preflight)
        plans = prelaunch["trials"]
        global_absence = store.prove_absent([row["username"] for row in plans])
        if global_absence.get("all_absent") is not True:
            raise MultiActionOrchestrationError("one or more planned usernames already exist")
        plan_by_trial = {row["trial_id"]: row for row in plans}
        stage = "trial_execution"
        with tempfile.TemporaryDirectory(prefix=f"kaetram-multi-action-{prelaunch['run_id']}-") as temporary:
            temporary_root = Path(temporary)

            def worker(spec: SessionSpec) -> dict[str, Any]:
                plan = plan_by_trial.get(spec.trial_id)
                if plan is None:
                    raise MultiActionOrchestrationError("worker trial is not registered")
                return run_session_worker(
                    spec,
                    registration_path,
                    action_order=plan["action_order"],
                    python_executable=python_executable,
                    state_dir=temporary_root / spec.session_id,
                    timeout_seconds=float(
                        registration["runtime_parameters"]["worker_timeout_seconds"]
                    ),
                )

            def publish_trial(execution: TrialExecution) -> None:
                receipt = assemble_trial_receipt(
                    plan=execution.plan,
                    treatment=execution.treatment,
                    reconnect=execution.reconnect,
                    database_snapshot=execution.database_snapshot,
                    cleanup=execution.cleanup,
                    seed=execution.seed,
                    parent_event_ledger=execution.parent_event_ledger,
                    global_absence=global_absence,
                    registration_sha256=prelaunch["registration"]["sha256"],
                )
                index = execution.plan["schedule_index"]
                publish_json_create_only(root / "receipts" / f"trial-{index:02d}.json", receipt)
                receipts.append(receipt)

            run_exact_trial_sequence(
                plans,
                registration,
                store=store,
                worker_runner=worker,
                global_absence=global_absence,
                on_trial_completed=publish_trial,
            )
        stage = "source_postflight"
        verify_prelaunch(
            prelaunch_path,
            registration_path,
            repo_root=repo_root,
            require_clean_head=True,
        )
        if attest_python_runtime(python_executable, registration["live_contract"]) != python_receipt:
            raise MultiActionOrchestrationError("Python runtime drifted during execution")
        if game_attestor(game_root, registration) != game_receipt:
            raise MultiActionOrchestrationError("game runtime drifted during execution")
        if store.attest_topology() != mongo_receipt:
            raise MultiActionOrchestrationError("Mongo topology drifted during execution")
        store.close()
        store = None
        stage = "analysis"
        analysis = analyze_run(receipts)
        publish_json_create_only(root / "analysis.json", analysis)
        relative_files = [
            "analysis.json", "prelaunch.json", "registration.json", "runtime-preflight.json",
            *(f"receipts/trial-{index:02d}.json" for index in range(1, 10)),
        ]
        manifest: dict[str, Any] = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "study_id": registration["study_id"],
            "run_id": prelaunch["run_id"],
            "files": [_file_row(root, relative) for relative in relative_files],
        }
        manifest["payload_sha256"] = canonical_sha256(manifest)
        publish_json_create_only(root / "manifest.json", manifest)
        verification = verify_package(root, repo_root=repo_root)
    except BaseException as exc:
        if store is not None:
            try:
                store.close()
            except BaseException as close_error:
                exc.add_note(f"store close also failed: {type(close_error).__name__}: {close_error}")
        try:
            publish_failure_receipt(root, stage=stage, error=exc)
        except BaseException:
            pass
        raise
    return {"result_root": str(root), "analysis": analysis, "verification": verification}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--prelaunch", type=Path, required=True)
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--game-root", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--docker", type=Path, default=Path("/usr/local/bin/docker"))
    parser.add_argument("--node", type=Path, default=Path("/opt/homebrew/opt/node@20/bin/node"))
    args = parser.parse_args(argv)
    try:
        from scripts.opd.live_routing_services import (
            LiveRoutingServices,
            ServiceConfig,
            ServiceError,
        )

        registration = load_registration_strict(args.registration)
        errors = validate_registration(registration)
        if errors:
            raise MultiActionOrchestrationError(
                "registration invalid before service start: " + "; ".join(errors)
            )
        prelaunch = verify_prelaunch(
            args.prelaunch,
            args.registration,
            repo_root=args.repo_root,
            require_clean_head=True,
        )
        if prelaunch["git_head"] != registration.get("source_contract", {}).get(
            "source_commit", prelaunch["git_head"]
        ):
            raise MultiActionOrchestrationError("registered source commit differs from prelaunch")
        live = registration["live_contract"]
        # Fail before Docker/Node if the selected Python runtime is not exact.
        attest_python_runtime(args.python, live)
        config = ServiceConfig(
            game_root=args.game_root,
            game_revision=live["game_revision"],
            server_bundle_sha256=live["game_bundle_sha256"],
            client_dist_inventory_sha256=live["client_dist_inventory_sha256"],
            python_binary=args.python,
            node_version=live["node_version"],
            node_executable_sha256=live["node_executable_sha256"],
            docker_client_version=live["docker_client_version"],
            docker_executable_sha256=live["docker_executable_sha256"],
            docker_binary=args.docker,
            node_binary=args.node,
            readiness_timeout_seconds=float(
                registration["runtime_parameters"]["service_readiness_timeout_seconds"]
            ),
        )
        services = LiveRoutingServices(config)
        with services:
            result = run_orchestration(
                registration_path=args.registration,
                prelaunch_path=args.prelaunch,
                result_root=args.result_root,
                repo_root=args.repo_root,
                game_root=args.game_root,
                python_executable=args.python,
                services_evidence=services.evidence,
            )
        cleanup = services.cleanup_report
        if not isinstance(cleanup, dict) or cleanup.get("absence_proven") is not True:
            raise MultiActionOrchestrationError(
                "owned services finished but cleanup absence was not proven"
            )
    except (OSError, ValueError, MultiActionOrchestrationError, ServiceError) as exc:
        print(f"multi-action orchestration refused: {exc}")
        return 1
    print(json.dumps({**result, "service_cleanup": cleanup}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
