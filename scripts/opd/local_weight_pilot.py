#!/usr/bin/env python3
"""Run the preregistered zero-cost local weights pilot.

This launcher is intentionally separate from the confirmatory factorial
launcher. It executes a small feasibility pilot, preserves every cell, and
labels the resulting evidence as exploratory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from run_manifest import canonical_json_bytes, sha256_json  # noqa: E402
from scripts.local_mlx_endpoint import SUPPORTED_MODELS  # noqa: E402


SCHEMA_VERSION = "kaetram.local-weight-pilot.v1"
PILOT_STATUS = "preregistered_exploratory"
WEIGHTS = ("base_2b", "opd_r2_2b", "opd_r3_2b")
ENDPOINT_ENV = "KAETRAM_LOCAL_PILOT_ENDPOINT"
ENDPOINT_HOST = "127.0.0.1"
ENDPOINT_PORT = 9801
BACKEND_PORT = 9802
SHA256_RE = re.compile(r"[0-9a-f]{64}")


class PilotError(RuntimeError):
    """Raised when the exploratory pilot cannot preserve its contract."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _require_clean_git(repo: Path, label: str) -> str:
    try:
        top = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain=v1"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        revision = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PilotError(f"cannot inspect {label} git checkout") from exc
    if Path(top).resolve() != repo.resolve():
        raise PilotError(f"{label} path is not its git toplevel")
    if status:
        raise PilotError(f"{label} checkout must be clean")
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise PilotError(f"{label} revision is not an exact commit")
    return revision


def _validate_schedule(raw: dict) -> None:
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise PilotError(f"manifest schema_version must be {SCHEMA_VERSION}")
    if raw.get("status") != PILOT_STATUS:
        raise PilotError(f"manifest status must be {PILOT_STATUS}")
    boundary = raw.get("claim_boundary")
    if not isinstance(boundary, dict) or boundary.get("confirmatory") is not False:
        raise PilotError("pilot must be explicitly non-confirmatory")
    protocol = raw.get("protocol")
    if not isinstance(protocol, dict):
        raise PilotError("manifest protocol must be an object")
    expected_protocol = {
        "scenario": "D",
        "duration_seconds": 300,
        "episodes_per_cell": 1,
        "personality": "completionist",
        "prompt_agent_name": "EvalCompletionist",
        "include_game_knowledge": True,
        "recovery": False,
        "tool_schema_source": "canonical",
        "mongo_database": "kaetram_devlopment",
        "schedule_algorithm": "sha256-rank-v1",
        "schedule_seed": 20260723,
        "environment_seed_mechanism": "kaetram-environment-rng-attestation/v2",
        "environment_rng_algorithm": "mulberry32-sha256-v1",
    }
    mismatches = {
        key: {"expected": value, "actual": protocol.get(key)}
        for key, value in expected_protocol.items()
        if protocol.get(key) != value
    }
    if mismatches:
        raise PilotError(f"unreviewed pilot protocol: {mismatches}")
    models = raw.get("models")
    if not isinstance(models, dict) or tuple(models) != WEIGHTS:
        raise PilotError(f"models must be ordered exactly as {list(WEIGHTS)}")
    for snapshot in WEIGHTS:
        if models[snapshot].get("api_model") != SUPPORTED_MODELS[snapshot]:
            raise PilotError(f"{snapshot} has an unreviewed API model")

    cells = raw.get("cells")
    if not isinstance(cells, list) or len(cells) != 9:
        raise PilotError("pilot must contain exactly nine cells")
    ids = [cell.get("cell_id") for cell in cells if isinstance(cell, dict)]
    if len(set(ids)) != 9 or any(
        not isinstance(cell_id, str)
        or re.fullmatch(r"rep0[1-3]-(?:base|r2|r3)", cell_id) is None
        for cell_id in ids
    ):
        raise PilotError("pilot cell IDs must be unique and reviewed")
    if [cell.get("schedule_index") for cell in cells] != list(range(9)):
        raise PilotError("pilot schedule indices must be contiguous and ordered")

    pilot_id = raw.get("pilot_id")
    for replicate in (1, 2, 3):
        block = [cell for cell in cells if cell.get("replicate") == replicate]
        if {cell.get("snapshot") for cell in block} != set(WEIGHTS):
            raise PilotError(f"replicate {replicate} must contain all weight arms")
        if len({cell.get("inference_seed") for cell in block}) != 1:
            raise PilotError(f"replicate {replicate} inference seed is not paired")
        if len({cell.get("environment_seed") for cell in block}) != 1:
            raise PilotError(f"replicate {replicate} environment seed is not paired")
        expected_order = sorted(
            WEIGHTS,
            key=lambda weight: hashlib.sha256(
                f"{pilot_id}|{replicate}|{weight}".encode()
            ).hexdigest(),
        )
        actual_order = [
            cell["snapshot"]
            for cell in sorted(block, key=lambda cell: cell["schedule_index"])
        ]
        if actual_order != expected_order:
            raise PilotError(f"replicate {replicate} schedule hash does not match")


def load_manifest(path: Path) -> tuple[dict, str]:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise PilotError(f"cannot read pilot manifest: {path}") from exc
    if not isinstance(raw, dict):
        raise PilotError("pilot manifest must be a JSON object")
    _validate_schedule(raw)
    return raw, _sha256_file(path)


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection((ENDPOINT_HOST, port), timeout=0.5):
            return True
    except OSError:
        return False


def _stop_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=15)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def _read_health(process: subprocess.Popen, timeout_seconds: float = 180.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_error = "not contacted"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise PilotError(f"local endpoint exited during startup ({process.returncode})")
        try:
            request = Request(
                f"http://{ENDPOINT_HOST}:{ENDPOINT_PORT}/health",
                headers={"Accept": "application/json"},
            )
            with urlopen(request, timeout=2) as response:
                payload = json.loads(response.read())
            if payload.get("status") == "ok" and isinstance(
                payload.get("attestation"), dict
            ):
                return payload
            last_error = "invalid health payload"
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            last_error = type(exc).__name__
        time.sleep(0.25)
    raise PilotError(f"local endpoint did not become ready: {last_error}")


def _start_endpoint(
    *,
    snapshot: str,
    api_model: str,
    mlx_python: Path,
    snapshots_root: Path,
    log_path: Path,
) -> tuple[subprocess.Popen, dict]:
    if _port_open(ENDPOINT_PORT) or _port_open(BACKEND_PORT):
        raise PilotError("pilot endpoint ports are already in use")
    command = [
        str(mlx_python),
        str(REPO / "scripts/local_mlx_endpoint.py"),
        "--snapshot",
        snapshot,
        "--api-model",
        api_model,
        "--snapshots-root",
        str(snapshots_root),
        "--port",
        str(ENDPOINT_PORT),
        "--backend-port",
        str(BACKEND_PORT),
    ]
    handle = log_path.open("w")
    try:
        process = subprocess.Popen(
            command,
            cwd=REPO,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    finally:
        handle.close()
    try:
        return process, _read_health(process)
    except Exception:
        _stop_process(process)
        raise


def _load_game_attestation(game_dir: Path, game_revision: str) -> dict:
    path = game_dir / "packages/server/dist/kaetram-build-attestation.json"
    try:
        record = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise PilotError("game build attestation is missing or invalid") from exc
    if record.get("schema") != "kaetram-server-build-attestation/v1":
        raise PilotError("unsupported game build attestation")
    if record.get("gameRevision") != game_revision:
        raise PilotError("compiled game server does not attest the clean checkout")
    entrypoint = game_dir / str(record.get("entrypoint", ""))
    expected = record.get("entrypointSha256")
    if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
        raise PilotError("game build attestation has no valid entrypoint digest")
    if not entrypoint.is_file() or _sha256_file(entrypoint) != expected:
        raise PilotError("compiled game server digest differs from its attestation")
    return record


def _write_json(path: Path, value: object) -> str:
    payload = canonical_json_bytes(value) + b"\n"
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _preflight_endpoints(
    manifest: dict,
    mlx_python: Path,
    snapshots_root: Path,
    output_root: Path,
) -> dict[str, dict]:
    receipts: dict[str, dict] = {}
    for snapshot in WEIGHTS:
        process = None
        try:
            process, health = _start_endpoint(
                snapshot=snapshot,
                api_model=manifest["models"][snapshot]["api_model"],
                mlx_python=mlx_python,
                snapshots_root=snapshots_root,
                log_path=output_root / f"preflight-{snapshot}.log",
            )
            receipts[snapshot] = health
        finally:
            _stop_process(process)
    tokenizer_digests = {
        receipt["attestation"].get("tokenizer_sha256")
        for receipt in receipts.values()
    }
    render_digests = {
        receipt["attestation"].get("render_contract_sha256")
        for receipt in receipts.values()
    }
    if len(tokenizer_digests) != 1 or not all(
        isinstance(item, str) and SHA256_RE.fullmatch(item)
        for item in tokenizer_digests
    ):
        raise PilotError("preflight endpoints do not share one tokenizer")
    if len(render_digests) != 1 or not all(
        isinstance(item, str) and SHA256_RE.fullmatch(item)
        for item in render_digests
    ):
        raise PilotError("preflight endpoints do not share one render contract")
    return receipts


def _username(cell: dict) -> str:
    weight = {"base_2b": "B", "opd_r2_2b": "R2", "opd_r3_2b": "R3"}[
        cell["snapshot"]
    ]
    return f"Pilot{weight}R{cell['replicate']:02d}"


def build_eval_command(
    *,
    manifest: dict,
    manifest_sha256: str,
    cell: dict,
    cell_root: Path,
    endpoint_attestation_sha256: str,
    endpoint_attestation: dict,
    game_attestation: dict,
) -> list[str]:
    protocol = manifest["protocol"]
    attestation = endpoint_attestation["attestation"]
    return [
        sys.executable,
        str(REPO / "eval_harness.py"),
        "--models-env",
        f"{cell['cell_id']}={ENDPOINT_ENV}",
        "--episodes",
        "1",
        "--scenario",
        protocol["scenario"],
        "--duration-seconds",
        str(protocol["duration_seconds"]),
        "--protocol-id",
        manifest["pilot_id"],
        "--experiment-manifest-sha256",
        manifest_sha256,
        "--endpoint-attestation-sha256",
        endpoint_attestation_sha256,
        "--checkpoint-sha256",
        attestation["checkpoint_sha256"],
        "--tokenizer-sha256",
        attestation["tokenizer_sha256"],
        "--render-contract-sha256",
        attestation["render_contract_sha256"],
        "--output-dir",
        str(cell_root / "eval"),
        "--server-port",
        str(9901 + 2 * cell["schedule_index"]),
        "--username",
        _username(cell),
        "--prompt-agent-name",
        protocol["prompt_agent_name"],
        "--project-dir",
        str(REPO),
        "--sandbox",
        str(cell_root / "sandbox"),
        "--model-api-name",
        manifest["models"][cell["snapshot"]]["api_model"],
        "--personality",
        protocol["personality"],
        "--inference-seed",
        str(cell["inference_seed"]),
        "--factorial-schedule-algorithm",
        protocol["schedule_algorithm"],
        "--factorial-schedule-seed",
        str(protocol["schedule_seed"]),
        "--factorial-schedule-index",
        str(cell["schedule_index"]),
        "--factorial-batch-index",
        str(cell["replicate"] - 1),
        "--factorial-cluster-id",
        f"pilot-rep{cell['replicate']:02d}",
        "--factorial-pair-id",
        f"pilot-rep{cell['replicate']:02d}",
        "--environment-seed-mechanism",
        protocol["environment_seed_mechanism"],
        "--environment-seed",
        str(cell["environment_seed"]),
        "--environment-rng-algorithm",
        protocol["environment_rng_algorithm"],
        "--environment-game-revision",
        game_attestation["gameRevision"],
        "--environment-game-bundle-sha256",
        game_attestation["entrypointSha256"],
        "--environment-seed-reason",
        protocol["environment_seed_reason"],
    ]


def build_eval_environment(
    base: dict[str, str],
    *,
    manifest: dict,
    game_dir: Path,
    node_binary: Path,
) -> dict[str, str]:
    """Pin DB/schema/recovery lanes instead of inheriting ambient test state."""
    env = dict(base)
    env[ENDPOINT_ENV] = f"http://{ENDPOINT_HOST}:{ENDPOINT_PORT}/v1"
    env["KAETRAM_GAME_DIR"] = str(game_dir)
    env["KAETRAM_NODE_BINARY"] = str(node_binary)
    env["KAETRAM_MONGO_DB"] = manifest["protocol"]["mongo_database"]
    env["KAETRAM_TOOL_SCHEMA_SOURCE"] = manifest["protocol"]["tool_schema_source"]
    env.pop("KAETRAM_TOOL_RECOVERY", None)
    return env


def run_pilot(
    manifest_path: Path,
    *,
    output_root: Path,
    snapshots_root: Path,
    game_dir: Path,
    mlx_python: Path,
    node_binary: Path,
    confirmation: str,
) -> int:
    manifest, manifest_sha256 = load_manifest(manifest_path)
    if confirmation != manifest["pilot_id"]:
        raise PilotError("--confirm must exactly match pilot_id")
    source_revision = _require_clean_git(REPO, "arena")
    game_revision = _require_clean_git(game_dir, "game")
    game_attestation = _load_game_attestation(game_dir, game_revision)
    if output_root in {Path("/"), Path.home().resolve()}:
        raise PilotError("output root is too broad")
    for protected, label in (
        (REPO.resolve(), "arena repository"),
        (game_dir.resolve(), "game repository"),
        (snapshots_root.resolve(), "model snapshots"),
    ):
        try:
            output_root.relative_to(protected)
        except ValueError:
            pass
        else:
            raise PilotError(f"output root must be outside the {label}")
    if output_root.exists():
        raise PilotError("output root already exists; pilot outputs are append-forbidden")
    if not snapshots_root.is_dir():
        raise PilotError("snapshots root does not exist")
    if not mlx_python.is_file() or not node_binary.is_file():
        raise PilotError("MLX Python and Node binary must be explicit existing files")
    node_version = subprocess.run(
        [str(node_binary), "--version"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if not node_version.startswith("v20."):
        raise PilotError(f"Node 20 is required; found {node_version!r}")

    output_root.mkdir(parents=True)
    endpoint_receipts = _preflight_endpoints(
        manifest, mlx_python, snapshots_root, output_root
    )
    prelaunch = {
        "schema_version": "kaetram.local-weight-pilot-prelaunch.v1",
        "pilot_id": manifest["pilot_id"],
        "claim_boundary": manifest["claim_boundary"],
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": manifest_sha256,
        "source_git_commit": source_revision,
        "game_git_commit": game_revision,
        "game_build_attestation": game_attestation,
        "endpoint_receipts": endpoint_receipts,
        "cells": manifest["cells"],
        "runtime": {
            "eval_python": sys.executable,
            "mlx_python": str(mlx_python),
            "node_binary": str(node_binary),
            "node_version": node_version,
        },
    }
    _write_json(output_root / "prelaunch.json", prelaunch)

    inventory: list[dict] = []
    for cell in manifest["cells"]:
        cell_root = output_root / cell["cell_id"]
        cell_root.mkdir()
        process = None
        status = "invalid"
        returncode = None
        error = ""
        try:
            process, health = _start_endpoint(
                snapshot=cell["snapshot"],
                api_model=manifest["models"][cell["snapshot"]]["api_model"],
                mlx_python=mlx_python,
                snapshots_root=snapshots_root,
                log_path=cell_root / "endpoint.log",
            )
            if health != endpoint_receipts[cell["snapshot"]]:
                raise PilotError("live endpoint identity drifted after preflight")
            endpoint_sha = _write_json(
                cell_root / "endpoint-attestation.json", health
            )
            command = build_eval_command(
                manifest=manifest,
                manifest_sha256=manifest_sha256,
                cell=cell,
                cell_root=cell_root,
                endpoint_attestation_sha256=endpoint_sha,
                endpoint_attestation=health,
                game_attestation=game_attestation,
            )
            env = build_eval_environment(
                dict(os.environ),
                manifest=manifest,
                game_dir=game_dir,
                node_binary=node_binary,
            )
            with (cell_root / "eval.log").open("w") as log:
                completed = subprocess.run(
                    command,
                    cwd=REPO,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                )
            returncode = completed.returncode
            results_path = (
                cell_root / "eval" / cell["cell_id"] / "results.json"
            )
            if returncode != 0:
                error = f"eval_harness exited {returncode}"
            elif not results_path.is_file():
                error = "eval_harness produced no results.json"
            else:
                results = json.loads(results_path.read_text())
                episodes = results.get("episodes")
                if not isinstance(episodes, list) or len(episodes) != 1:
                    error = "results do not contain exactly one episode"
                elif episodes[0].get("status") != "ok":
                    error = f"episode status is {episodes[0].get('status')!r}"
                else:
                    status = "valid"
        except Exception as exc:  # preserve the failed cell and continue
            error = f"{type(exc).__name__}: {exc}"
        finally:
            _stop_process(process)
        receipt = {
            "cell_id": cell["cell_id"],
            "snapshot": cell["snapshot"],
            "schedule_index": cell["schedule_index"],
            "status": status,
            "returncode": returncode,
            "error": error,
        }
        _write_json(cell_root / "cell-status.json", receipt)
        inventory.append(receipt)

    completed = {
        "schema_version": "kaetram.local-weight-pilot-inventory.v1",
        "pilot_id": manifest["pilot_id"],
        "manifest_sha256": manifest_sha256,
        "valid_cells": sum(item["status"] == "valid" for item in inventory),
        "invalid_cells": sum(item["status"] != "valid" for item in inventory),
        "cells": inventory,
        "claim_boundary": manifest["claim_boundary"],
    }
    _write_json(output_root / "completed-inventory.json", completed)
    return 0 if completed["invalid_cells"] == 0 else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "manifest",
        type=Path,
        nargs="?",
        default=REPO / "research/experiments/local-weight-pilot.json",
    )
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--snapshots-root", type=Path)
    parser.add_argument("--game-dir", type=Path)
    parser.add_argument("--mlx-python", type=Path)
    parser.add_argument("--node-binary", type=Path)
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)
    try:
        manifest, manifest_sha256 = load_manifest(args.manifest)
        if not args.launch:
            print(json.dumps({
                "mode": "dry_run",
                "pilot_id": manifest["pilot_id"],
                "manifest_sha256": manifest_sha256,
                "cell_count": len(manifest["cells"]),
                "duration_seconds_per_cell": manifest["protocol"]["duration_seconds"],
                "nominal_runtime_seconds": (
                    len(manifest["cells"])
                    * manifest["protocol"]["duration_seconds"]
                ),
                "confirmatory": False,
                "nothing_launched": True,
            }, indent=2, sort_keys=True))
            return 0
        required = {
            "--output-root": args.output_root,
            "--snapshots-root": args.snapshots_root,
            "--game-dir": args.game_dir,
            "--mlx-python": args.mlx_python,
            "--node-binary": args.node_binary,
        }
        missing = [flag for flag, value in required.items() if value is None]
        if missing:
            raise PilotError("launch requires " + ", ".join(missing))
        return run_pilot(
            args.manifest.resolve(),
            output_root=args.output_root.resolve(),
            snapshots_root=args.snapshots_root.resolve(),
            game_dir=args.game_dir.resolve(),
            mlx_python=args.mlx_python.resolve(),
            node_binary=args.node_binary.resolve(),
            confirmation=args.confirm,
        )
    except (PilotError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
