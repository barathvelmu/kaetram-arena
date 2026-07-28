#!/usr/bin/env python3
"""Bind the frozen V2 runner to the two-stage V3 execution gate.

The V3 design records the clean, pushed commit at which it was prepared.  The
design is then committed and pushed in a second stage before requests are
allowed.  Consequently, V2's original requirement that the seed-gate/run
commit equal the design-preparation commit cannot hold.  This adapter changes
only that provenance binding: requests, response classification, finite-grid
analysis, contrasts, and seed-heterogeneity calculations remain delegated to
the frozen V2 implementation.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterator

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.opd import prepare_trigger_incidence_v3 as prepare  # noqa: E402
from scripts.opd import trigger_incidence_probe as v1  # noqa: E402
from scripts.opd import trigger_incidence_probe_v2 as v2  # noqa: E402
from scripts.opd import verify_trigger_incidence_v3 as verifier  # noqa: E402


ProbeError = v1.ProbeError
RUNTIME_BINDING_SCHEMA = "kaetram.local-trigger-incidence-v3-runtime-binding.v1"
DESIGN_DIR = Path("research/experiments/local-trigger-incidence-v3-design")
HEX40 = re.compile(r"[0-9a-f]{40}")
_V2_LOAD_DESIGN = v2.load_design


def _repo_relative(path: Path, *, label: str) -> str:
    try:
        relative = path.resolve().relative_to(REPO.resolve()).as_posix()
    except ValueError as exc:
        raise ProbeError(f"{label} must be inside the Arena repository") from exc
    return relative


def _expected_request_grid(registration: dict, design: dict) -> list[dict]:
    """Materialize the exact metadata grid independently of model responses."""
    rows = []
    conditions = registration["conditions"]
    sample_count = int(registration["sampling"]["samples_per_state_condition"])
    base_seed = int(registration["sampling"]["base_seed"])
    for snapshot in registration["snapshots"]:
        schedule_index = 0
        for state_index, state in enumerate(design["states"]):
            for sample_index in range(sample_count):
                block_index = state_index * sample_count + sample_index
                offset = block_index % len(conditions)
                ordered = conditions[offset:] + conditions[:offset]
                for condition in ordered:
                    rows.append(
                        {
                            "schema_version": v1.RUN_SCHEMA,
                            "snapshot": snapshot,
                            "schedule_index": schedule_index,
                            "state_id": state["state_id"],
                            "state_index": state_index,
                            "sample_index": sample_index,
                            "seed": base_seed + 100 * state_index + sample_index,
                            "condition_id": condition["condition_id"],
                            "documentation": condition["documentation"],
                            "native_tool_schema": condition["native_tool_schema"],
                        }
                    )
                    schedule_index += 1
    return rows


def _validate_grid(registration: dict, design: dict) -> list[dict]:
    rows = _expected_request_grid(registration, design)
    snapshot_count = len(registration["snapshots"])
    state_count = len(design["states"])
    sample_count = int(registration["sampling"]["samples_per_state_condition"])
    condition_count = len(registration["conditions"])
    expected_count = snapshot_count * state_count * sample_count * condition_count
    keys = {
        (
            row["snapshot"],
            row["condition_id"],
            row["state_id"],
            row["sample_index"],
        )
        for row in rows
    }
    if len(rows) != expected_count or len(keys) != expected_count:
        raise ProbeError("V3 expected request grid is incomplete or duplicated")
    if (snapshot_count, state_count, sample_count, condition_count, expected_count) != (
        3,
        20,
        5,
        4,
        1200,
    ):
        raise ProbeError("V3 request grid differs from the frozen 3x20x5x4 protocol")
    return rows


def validate_execution_binding(
    registration_path: Path,
    historical_root: Path,
    design_dir: Path,
) -> dict:
    """Return a fail-closed binding for the clean, pushed V3 design HEAD."""
    if _repo_relative(registration_path, label="V3 registration") != (
        prepare.REGISTRATION_PATH.as_posix()
    ):
        raise ProbeError("unexpected V3 registration path")
    if _repo_relative(design_dir, label="V3 design directory") != DESIGN_DIR.as_posix():
        raise ProbeError("unexpected V3 design directory")

    evidence = verifier.verify(
        registration_path,
        historical_root,
        design_dir,
        require_execution_ready=True,
    )
    execution_commit = str(evidence.get("execution_commit", ""))
    if (
        evidence.get("execution_ready") is not True
        or evidence.get("schema_version")
        != "kaetram.local-trigger-incidence-v3-verification.v1"
        or HEX40.fullmatch(execution_commit) is None
    ):
        raise ProbeError("V3 verifier did not authorize execution")

    registration, registration_sha256 = prepare.load_registration(registration_path)
    effective_path = design_dir / "effective-registration.json"
    design_path = design_dir / "design.json"
    effective, effective_sha256 = v2.load_registration(effective_path)
    design = _V2_LOAD_DESIGN(
        design_path,
        effective,
        effective_sha256,
        historical_root=None,
    )
    if effective != prepare.materialize_effective_registration(registration):
        raise ProbeError("runtime registration differs from the frozen V3 materialization")
    if evidence.get("design_sha256") != v1.sha256_file(design_path):
        raise ProbeError("execution-ready evidence does not bind the exact V3 design")
    if evidence.get("study_id") != registration["study_id"]:
        raise ProbeError("execution-ready evidence has the wrong V3 study identity")
    if effective.get("claim_boundary") != registration.get("claim_boundary"):
        raise ProbeError("effective registration changed the V3 claim boundary")

    grid = _validate_grid(effective, design)
    return {
        "schema_version": RUNTIME_BINDING_SCHEMA,
        "study_id": registration["study_id"],
        "v3_registration_sha256": registration_sha256,
        "effective_registration_sha256": effective_sha256,
        "design_sha256": v1.sha256_file(design_path),
        "design_source_git_commit": design["source_git_commit"],
        "execution_commit": execution_commit,
        "execution_verification_sha256": v1.sha256_json(evidence),
        "execution_verifier_sha256": v1.sha256_file(
            Path(verifier.__file__).resolve()
        ),
        "expected_request_count": len(grid),
        "expected_request_grid_sha256": v1.sha256_json(grid),
        "claim_boundary_sha256": v1.sha256_json(registration["claim_boundary"]),
        "effective_registration_path": effective_path,
        "design_path": design_path,
        "effective_registration": effective,
        "design": design,
    }


def _public_binding(binding: dict) -> dict:
    return {
        key: value
        for key, value in binding.items()
        if key not in {
            "effective_registration_path",
            "design_path",
            "effective_registration",
            "design",
        }
    }


def _require_runtime_preflight(preflight: dict, binding: dict) -> None:
    public = _public_binding(binding)
    if (
        preflight.get("source_git_commit") != binding["execution_commit"]
        or preflight.get("dirty_paths") != []
        or preflight.get("registration_sha256")
        != binding["effective_registration_sha256"]
        or preflight.get("v3_runtime_binding") != public
    ):
        raise ProbeError("artifact is not bound to the execution-ready V3 design HEAD")


@contextlib.contextmanager
def _binding_writer(binding: dict) -> Iterator[None]:
    original = v1.write_json

    def write_json(path: Path, value: Any, *, exclusive: bool = False) -> None:
        if (
            path.name == "preflight.json"
            and isinstance(value, dict)
            and value.get("schema_version") == f"{v2.SEED_GATE_SCHEMA}.preflight"
        ):
            value = {**value, "v3_runtime_binding": _public_binding(binding)}
        original(path, value, exclusive=exclusive)

    v1.write_json = write_json
    try:
        yield
    finally:
        v1.write_json = original


@contextlib.contextmanager
def _v1_execution_extensions(binding: dict, gate_receipt: dict) -> Iterator[None]:
    """Adapt only commit provenance around the unchanged V1/V2 execution path."""
    with v2._v1_protocol_extensions(gate_receipt=gate_receipt):
        original_load_design = v1.load_design
        original_write_json = v1.write_json

        def load_design(
            path: Path,
            registration: dict,
            registration_sha256: str,
            *,
            historical_root: Path | None = None,
        ) -> dict:
            del historical_root  # archive/design rederivation was verified before entry
            loaded = _V2_LOAD_DESIGN(
                path,
                registration,
                registration_sha256,
                historical_root=None,
            )
            if v1.sha256_file(path) != binding["design_sha256"]:
                raise ProbeError("runtime design hash drifted after V3 verification")
            return loaded

        def write_json(path: Path, value: Any, *, exclusive: bool = False) -> None:
            if (
                path.name == "prelaunch.json"
                and isinstance(value, dict)
                and value.get("schema_version") == f"{v1.RUN_SCHEMA}.prelaunch"
            ):
                value = {**value, "v3_runtime_binding": _public_binding(binding)}
            original_write_json(path, value, exclusive=exclusive)

        v1.load_design = load_design
        v1.write_json = write_json
        try:
            yield
        finally:
            v1.load_design = original_load_design
            v1.write_json = original_write_json


def _verify_seed_gate_binding(
    seed_gate_dir: Path,
    binding: dict,
    snapshot: str,
    endpoint_health_payload: dict,
) -> dict:
    receipt = v2.verify_seed_gate(
        seed_gate_dir,
        binding["effective_registration"],
        binding["effective_registration_sha256"],
        snapshot,
        endpoint_health_payload,
    )
    try:
        preflight = prepare._read_json(seed_gate_dir / "preflight.json")
    except ProbeError:
        raise
    if not isinstance(preflight, dict):
        raise ProbeError("seed-gate preflight is not a JSON object")
    _require_runtime_preflight(preflight, binding)
    if receipt["source_git_commit"] != binding["execution_commit"]:
        raise ProbeError("seed gate was not executed at the V3 design HEAD")
    return receipt


def _verify_checkpoint_grid(
    run_dir: Path,
    binding: dict,
    snapshot: str,
    gate_receipt: dict | None = None,
) -> None:
    prelaunch, _postflight, _completed, rows, _identity = v1._verify_run_directory(
        run_dir,
        binding["effective_registration"],
    )
    _require_runtime_preflight(prelaunch, binding)
    if gate_receipt is not None and (
        prelaunch.get("seed_gate_artifact_index_sha256")
        != gate_receipt["artifact_index_sha256"]
        or prelaunch.get("seed_gate_tree_sha256") != gate_receipt["tree_sha256"]
    ):
        raise ProbeError(f"{snapshot}: run is not bound to the passed V3 seed gate")
    expected = [
        row
        for row in _expected_request_grid(
            binding["effective_registration"], binding["design"]
        )
        if row["snapshot"] == snapshot
    ]
    if len(rows) != len(expected):
        raise ProbeError(f"{snapshot}: result count does not match the V3 request grid")
    for actual, scheduled in zip(rows, expected, strict=True):
        if any(actual.get(key) != value for key, value in scheduled.items()):
            raise ProbeError(f"{snapshot}: result schedule differs from the V3 grid")


async def run_seed_gate(
    registration_path: Path,
    historical_root: Path,
    design_dir: Path,
    endpoint: str,
    snapshot: str,
    output_dir: Path,
) -> dict:
    binding = validate_execution_binding(registration_path, historical_root, design_dir)
    with _binding_writer(binding):
        completed = await v2.run_seed_gate(
            binding["effective_registration_path"],
            endpoint,
            snapshot,
            output_dir,
        )
    preflight = prepare._read_json(output_dir / "preflight.json")
    _verify_seed_gate_binding(
        output_dir,
        binding,
        snapshot,
        preflight["endpoint_health"],
    )
    return completed


async def run_checkpoint(
    registration_path: Path,
    historical_root: Path,
    design_dir: Path,
    endpoint: str,
    snapshot: str,
    output_dir: Path,
    seed_gate_dir: Path,
) -> dict:
    binding = validate_execution_binding(registration_path, historical_root, design_dir)
    if snapshot not in binding["effective_registration"]["snapshots"]:
        raise ProbeError(f"snapshot is not registered: {snapshot}")
    endpoint = v2.require_zero_spend_endpoints([endpoint])[0]
    health = await v1.endpoint_health(endpoint)
    v1.validate_endpoint_health(health, binding["effective_registration"], snapshot)
    gate_receipt = _verify_seed_gate_binding(
        seed_gate_dir,
        binding,
        snapshot,
        health,
    )
    with _v1_execution_extensions(binding, gate_receipt):
        completed = await v1.run_checkpoint(
            binding["effective_registration_path"],
            binding["design_path"],
            historical_root,
            endpoint,
            snapshot,
            output_dir,
        )
    run_preflight = prepare._read_json(output_dir / "prelaunch.json")
    gate_receipt = _verify_seed_gate_binding(
        seed_gate_dir,
        binding,
        snapshot,
        run_preflight["endpoint_health"],
    )
    _verify_checkpoint_grid(output_dir, binding, snapshot, gate_receipt)
    return completed


def _analysis_design_loader(binding: dict):
    def load_design(
        path: Path,
        registration: dict,
        registration_sha256: str,
        *,
        historical_root: Path | None = None,
    ) -> dict:
        del historical_root
        loaded = _V2_LOAD_DESIGN(
            path,
            registration,
            registration_sha256,
            historical_root=None,
        )
        if v1.sha256_file(path) != binding["design_sha256"]:
            raise ProbeError("analysis design hash drifted after V3 verification")
        rebound = copy.deepcopy(loaded)
        rebound["source_git_commit"] = binding["execution_commit"]
        return rebound

    return load_design


def analyze(
    registration_path: Path,
    historical_root: Path,
    design_dir: Path,
    run_dirs: list[Path],
    seed_gate_dirs: list[Path],
    output_dir: Path,
) -> dict:
    binding = validate_execution_binding(registration_path, historical_root, design_dir)
    gates_by_snapshot = {}
    for gate_dir in seed_gate_dirs:
        preflight = prepare._read_json(gate_dir / "preflight.json")
        snapshot = preflight.get("snapshot")
        if snapshot in gates_by_snapshot:
            raise ProbeError("duplicate V3 seed-gate snapshot")
        gates_by_snapshot[snapshot] = gate_dir
    if set(gates_by_snapshot) != set(binding["effective_registration"]["snapshots"]):
        raise ProbeError("analysis requires one V3 seed gate per checkpoint")
    for run_dir in run_dirs:
        prelaunch, _postflight, _completed, _rows, _identity = (
            v1._verify_run_directory(run_dir, binding["effective_registration"])
        )
        snapshot = prelaunch["snapshot"]
        _require_runtime_preflight(prelaunch, binding)
        gate_preflight = prepare._read_json(
            gates_by_snapshot[snapshot] / "preflight.json"
        )
        _verify_seed_gate_binding(
            gates_by_snapshot[snapshot],
            binding,
            snapshot,
            gate_preflight["endpoint_health"],
        )
        _verify_checkpoint_grid(run_dir, binding, snapshot)

    original = v2.load_design
    v2.load_design = _analysis_design_loader(binding)
    try:
        summary = v2.analyze(
            binding["effective_registration_path"],
            binding["design_path"],
            run_dirs,
            seed_gate_dirs,
            output_dir,
        )
    finally:
        v2.load_design = original

    summary["analysis_code_provenance"]["runtime_binding_adapter_sha256"] = (
        v1.sha256_file(Path(__file__).resolve())
    )
    summary["v3_runtime_binding"] = _public_binding(binding)
    v1.write_json(output_dir / "analysis-summary.json", summary)
    artifacts = []
    for name in ("analysis-summary.json", "cells.csv", "contrasts.csv"):
        artifact = output_dir / name
        artifacts.append(
            {
                "path": name,
                "size_bytes": artifact.stat().st_size,
                "sha256": v1.sha256_file(artifact),
            }
        )
    v1.write_json(
        output_dir / "artifact-index.json",
        {
            "schema_version": f"{v1.ANALYSIS_SCHEMA}.artifacts",
            "files": artifacts,
            "tree_sha256": v1.sha256_json(artifacts),
        },
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("verify", "seed-gate", "run", "analyze"):
        command = subparsers.add_parser(name)
        command.add_argument(
            "--registration", type=Path, default=REPO / prepare.REGISTRATION_PATH
        )
        command.add_argument("--historical-root", type=Path, required=True)
        command.add_argument("--design-dir", type=Path, default=REPO / DESIGN_DIR)
        if name in {"seed-gate", "run"}:
            command.add_argument("--endpoint", required=True)
            command.add_argument("--snapshot", required=True)
            command.add_argument("--out-dir", type=Path, required=True)
        if name == "run":
            command.add_argument("--seed-gate-dir", type=Path, required=True)
        if name == "analyze":
            command.add_argument("--run-dir", type=Path, action="append", required=True)
            command.add_argument(
                "--seed-gate-dir", type=Path, action="append", required=True
            )
            command.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    common = (args.registration, args.historical_root, args.design_dir)
    if args.command == "verify":
        result = _public_binding(validate_execution_binding(*common))
    elif args.command == "seed-gate":
        result = asyncio.run(
            run_seed_gate(*common, args.endpoint, args.snapshot, args.out_dir)
        )
    elif args.command == "run":
        result = asyncio.run(
            run_checkpoint(
                *common,
                args.endpoint,
                args.snapshot,
                args.out_dir,
                args.seed_gate_dir,
            )
        )
    else:
        result = analyze(
            *common,
            args.run_dir,
            args.seed_gate_dir,
            args.out_dir,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
