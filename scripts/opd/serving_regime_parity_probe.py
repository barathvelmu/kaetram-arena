#!/usr/bin/env python3
"""Run and verify the registered thinking-off parity confirmation.

The study reuses the exact V2 request grid and retained thinking-enabled rows,
but excludes the three states used in the debugging pilot.  New requests differ
only in the endpoint's attested ``enable_thinking=false`` render contract.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

from scripts.opd import trigger_incidence_probe as trigger
from scripts.opd.endpoint_policy import require_zero_spend_endpoints
from tool_surface import MODEL_VISIBLE_TOOL_DEFINITIONS


ROOT = Path(__file__).resolve().parents[2]
REGISTRATION_SCHEMA = "kaetram.local-serving-regime-parity-registration.v1"
RUN_SCHEMA = "kaetram.local-serving-regime-parity-run.v1"
ANALYSIS_SCHEMA = "kaetram.local-serving-regime-parity-analysis.v1"


class ParityError(RuntimeError):
    """Raised when the registered parity contract is not satisfied."""


def _sha256_file(path: Path) -> str:
    return trigger.sha256_file(path)


def _sha256_json(value: Any) -> str:
    return trigger.sha256_json(value)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ParityError(f"cannot read registered JSON: {path}") from exc


def _write_json(path: Path, value: Any, *, exclusive: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "x" if exclusive else "w"
    with path.open(mode, encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _repository_identity() -> dict[str, Any]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    if dirty:
        raise ParityError("refusing a dirty checkout")
    return {"source_git_commit": head, "dirty_paths": []}


def _registered_path(record: dict, key: str, hash_key: str) -> Path:
    relative = Path(str(record.get(key, "")))
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise ParityError(f"invalid registered path: {key}")
    path = ROOT / relative
    if not path.is_file() or _sha256_file(path) != record.get(hash_key):
        raise ParityError(f"registered source identity mismatch: {key}")
    return path


def load_registration(path: Path) -> tuple[dict, str]:
    registration = _load_json(path)
    if (
        not isinstance(registration, dict)
        or registration.get("schema_version") != REGISTRATION_SCHEMA
        or registration.get("study_id") != "local-serving-regime-parity-v1"
        or registration.get("status")
        != "registered_after_pilot_before_confirmatory_outcomes"
    ):
        raise ParityError("invalid parity registration")
    panel = registration.get("confirmatory_panel") or {}
    indices = panel.get("state_indices")
    pilot = (registration.get("pilot_disclosure") or {}).get("state_indices")
    if (
        not isinstance(indices, list)
        or not indices
        or len(indices) != len(set(indices))
        or set(indices).intersection(pilot or [])
        or panel.get("state_count") != len(indices)
        or panel.get("requests_per_checkpoint")
        != len(indices) * int(panel.get("conditions", 0))
        * int(panel.get("samples_per_state_condition", 0))
    ):
        raise ParityError("invalid confirmatory state partition")
    source = registration.get("source_panel") or {}
    _registered_path(source, "registration", "registration_sha256")
    _registered_path(source, "design", "design_sha256")
    if registration.get("endpoint_contract", {}).get("thinking_mode") != "disabled":
        raise ParityError("parity endpoint must attest thinking disabled")
    return registration, _sha256_file(path)


def _source_inputs(registration: dict) -> tuple[dict, dict, list[dict]]:
    source = registration["source_panel"]
    source_registration = _load_json(
        _registered_path(source, "registration", "registration_sha256")
    )
    source_design = _load_json(_registered_path(source, "design", "design_sha256"))
    grid_path = ROOT / source["expected_request_grid"]
    if not grid_path.is_file():
        raise ParityError("registered expected request grid is missing")
    expected_grid = [
        json.loads(line) for line in grid_path.read_text().splitlines() if line.strip()
    ]
    return source_registration, source_design, expected_grid


def _payload_for(
    source_registration: dict,
    source_design: dict,
    expected: dict,
) -> dict:
    snapshots = source_registration["snapshots"]
    state = source_design["states"][int(expected["state_index"])]
    conditions = {
        item["condition_id"]: item for item in source_registration["conditions"]
    }
    condition = conditions[expected["condition_id"]]
    sampling = source_registration["sampling"]
    payload: dict[str, Any] = {
        "model": snapshots[expected["snapshot"]]["api_model"],
        "messages": trigger.condition_messages(
            copy.deepcopy(state["messages"]), condition["documentation"]
        ),
        "max_tokens": sampling["max_tokens"],
        "temperature": sampling["temperature"],
        "top_p": sampling["top_p"],
        "top_k": sampling["top_k"],
        "presence_penalty": sampling["presence_penalty"],
        "seed": expected["seed"],
    }
    if condition["native_tool_schema"] == "present":
        payload["tools"] = copy.deepcopy(MODEL_VISIBLE_TOOL_DEFINITIONS)
    elif condition["native_tool_schema"] != "absent":
        raise ParityError("unknown interface condition")
    if (
        _sha256_json(payload) != expected["request_payload_sha256"]
        or _sha256_json(payload["messages"]) != expected["messages_sha256"]
    ):
        raise ParityError("request payload does not reproduce the frozen V2 grid")
    return payload


def expected_schedule(registration: dict, snapshot: str) -> list[tuple[dict, dict]]:
    source_registration, source_design, grid = _source_inputs(registration)
    if snapshot not in registration["snapshots"]:
        raise ParityError(f"unregistered snapshot: {snapshot}")
    allowed = set(registration["confirmatory_panel"]["state_indices"])
    selected = [
        row
        for row in grid
        if row.get("snapshot") == snapshot and row.get("state_index") in allowed
    ]
    selected.sort(key=lambda row: int(row["schedule_index"]))
    expected_count = registration["confirmatory_panel"]["requests_per_checkpoint"]
    if len(selected) != expected_count:
        raise ParityError("expected request grid is incomplete")
    return [
        (row, _payload_for(source_registration, source_design, row))
        for row in selected
    ]


async def _health(endpoint: str) -> dict:
    health_root = endpoint.removesuffix("/v1")
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{health_root}/health", timeout=10)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise ParityError("endpoint health is not an object")
    return payload


def validate_health(registration: dict, snapshot: str, health: dict) -> None:
    attestation = health.get("attestation")
    if health.get("status") != "ok" or not isinstance(attestation, dict):
        raise ParityError("endpoint health lacks a valid attestation")
    expected = registration["snapshots"][snapshot]
    common = registration["endpoint_contract"]
    for key, value in {**expected, **common}.items():
        if attestation.get(key) != value:
            raise ParityError(f"endpoint attestation mismatch: {key}")


async def _request_one(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    endpoint: str,
    expected: dict,
    payload: dict,
) -> dict:
    errors: list[str] = []
    started = time.monotonic()
    body: dict | None = None
    async with semaphore:
        for attempt in range(1, 4):
            try:
                response = await client.post(
                    f"{endpoint}/chat/completions", json=payload, timeout=360
                )
                if response.status_code == 200:
                    candidate = response.json()
                    if not isinstance(candidate, dict):
                        raise ValueError("response body is not an object")
                    body = candidate
                    break
                errors.append(f"attempt {attempt}: HTTP {response.status_code}")
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                errors.append(f"attempt {attempt}: {type(exc).__name__}")
            if attempt < 3:
                await asyncio.sleep(float(attempt))
    base = {
        "schema_version": RUN_SCHEMA,
        **{key: expected[key] for key in (
            "snapshot",
            "schedule_index",
            "state_id",
            "state_index",
            "sample_index",
            "condition_id",
            "seed",
            "messages_sha256",
            "tools_sha256",
            "request_payload_sha256",
        )},
        "latency_seconds": round(time.monotonic() - started, 6),
        "attempt_errors": errors,
    }
    if body is None:
        return {**base, "status": "failed"}
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return {**base, "status": "failed", "attempt_errors": errors + ["invalid response"]}
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return {**base, "status": "failed", "attempt_errors": errors + ["invalid message"]}
    return {
        **base,
        "status": "ok",
        "finish_reason": choices[0].get("finish_reason"),
        "usage": body.get("usage"),
        "response_message": message,
        **trigger.classify_response_message(message),
    }


async def run(
    registration_path: Path,
    endpoint: str,
    snapshot: str,
    output_dir: Path,
) -> dict:
    registration, registration_sha256 = load_registration(registration_path)
    endpoint = require_zero_spend_endpoints([endpoint])[0]
    if output_dir.exists():
        raise ParityError("refusing to overwrite parity run directory")
    schedule = expected_schedule(registration, snapshot)
    health_before = await _health(endpoint)
    validate_health(registration, snapshot, health_before)
    identity = _repository_identity()
    output_dir.mkdir(parents=True, exist_ok=False)
    prelaunch = {
        "schema_version": f"{RUN_SCHEMA}.prelaunch",
        "study_id": registration["study_id"],
        "snapshot": snapshot,
        "registration_sha256": registration_sha256,
        "expected_requests": len(schedule),
        "endpoint_health": health_before,
        "expected_request_grid_sha256": _sha256_file(
            ROOT / registration["source_panel"]["expected_request_grid"]
        ),
        **identity,
    }
    _write_json(output_dir / "prelaunch.json", prelaunch)
    started = time.monotonic()
    semaphore = asyncio.Semaphore(4)
    async with httpx.AsyncClient() as client:
        rows = await asyncio.gather(*(
            _request_one(client, semaphore, endpoint, expected, payload)
            for expected, payload in schedule
        ))
    results_path = output_dir / "results.jsonl"
    with results_path.open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    health_after = await _health(endpoint)
    validate_health(registration, snapshot, health_after)
    if health_after != health_before:
        raise ParityError("endpoint identity changed during the run")
    completed = sum(row["status"] == "ok" for row in rows)
    postflight = {
        "schema_version": f"{RUN_SCHEMA}.postflight",
        "study_id": registration["study_id"],
        "snapshot": snapshot,
        "completed_requests": completed,
        "failed_requests": len(rows) - completed,
        "duration_seconds": round(time.monotonic() - started, 6),
        "endpoint_health": health_after,
        "endpoint_identity_stable": True,
        "finish_reason_counts": dict(sorted(Counter(
            str(row.get("finish_reason")) for row in rows if row["status"] == "ok"
        ).items())),
    }
    _write_json(output_dir / "postflight.json", postflight)
    artifacts = []
    for name in ("prelaunch.json", "results.jsonl", "postflight.json"):
        item = output_dir / name
        artifacts.append({
            "path": name,
            "size_bytes": item.stat().st_size,
            "sha256": _sha256_file(item),
        })
    index = {
        "schema_version": f"{RUN_SCHEMA}.artifacts",
        "study_id": registration["study_id"],
        "snapshot": snapshot,
        "files": artifacts,
        "tree_sha256": _sha256_json(artifacts),
    }
    _write_json(output_dir / "artifact-index.json", index)
    return {**postflight, "artifact_tree_sha256": index["tree_sha256"]}


def _read_rows(path: Path) -> list[dict]:
    try:
        return [json.loads(line) for line in path.read_text().splitlines() if line]
    except (OSError, json.JSONDecodeError) as exc:
        raise ParityError(f"cannot read result rows: {path}") from exc


def verify_run(
    registration: dict,
    registration_sha256: str,
    snapshot: str,
    run_dir: Path,
) -> list[dict]:
    schedule = expected_schedule(registration, snapshot)
    expected = {
        (row["state_id"], row["condition_id"], row["sample_index"]): row
        for row, _payload in schedule
    }
    prelaunch = _load_json(run_dir / "prelaunch.json")
    postflight = _load_json(run_dir / "postflight.json")
    index = _load_json(run_dir / "artifact-index.json")
    files = index.get("files")
    if not isinstance(files, list) or index.get("tree_sha256") != _sha256_json(files):
        raise ParityError(f"{snapshot}: invalid artifact index")
    for record in files:
        artifact = run_dir / record["path"]
        if (
            not artifact.is_file()
            or artifact.stat().st_size != record["size_bytes"]
            or _sha256_file(artifact) != record["sha256"]
        ):
            raise ParityError(f"{snapshot}: artifact digest mismatch")
    if (
        prelaunch.get("registration_sha256") != registration_sha256
        or prelaunch.get("snapshot") != snapshot
        or prelaunch.get("dirty_paths") != []
        or postflight.get("snapshot") != snapshot
        or postflight.get("endpoint_identity_stable") is not True
        or prelaunch.get("endpoint_health") != postflight.get("endpoint_health")
    ):
        raise ParityError(f"{snapshot}: invalid pre/postflight binding")
    validate_health(registration, snapshot, prelaunch["endpoint_health"])
    rows = _read_rows(run_dir / "results.jsonl")
    if len(rows) != len(expected) or any(row.get("status") != "ok" for row in rows):
        raise ParityError(f"{snapshot}: incomplete confirmatory grid")
    seen = set()
    for row in rows:
        key = (row["state_id"], row["condition_id"], row["sample_index"])
        if key in seen or key not in expected:
            raise ParityError(f"{snapshot}: unexpected or duplicate result row")
        seen.add(key)
        source = expected[key]
        for field in (
            "snapshot", "schedule_index", "state_index", "seed",
            "messages_sha256", "tools_sha256", "request_payload_sha256",
        ):
            if row.get(field) != source.get(field):
                raise ParityError(f"{snapshot}: request binding mismatch: {field}")
        recomputed = trigger.classify_response_message(row["response_message"])
        if any(row.get(field) != value for field, value in recomputed.items()):
            raise ParityError(f"{snapshot}: stored outcome does not reclassify")
    return rows


def _historical_thinking_rows(registration: dict, snapshot: str) -> list[dict]:
    root = ROOT / registration["source_panel"]["thinking_arm_artifact"] / "runs" / snapshot
    rows = _read_rows(root / "results.jsonl")
    allowed = set(registration["confirmatory_panel"]["state_indices"])
    selected = [row for row in rows if row.get("state_index") in allowed]
    expected = registration["confirmatory_panel"]["requests_per_checkpoint"]
    if len(selected) != expected or any(row.get("status") != "ok" for row in selected):
        raise ParityError(f"{snapshot}: historical thinking arm is incomplete")
    return selected


def _route(row: dict) -> str:
    if row["has_structured_tool_call"]:
        return "structured"
    if row["recovery_opportunity"]:
        return "reasoning_stranded"
    return "no_candidate"


def analyze(
    registration_path: Path,
    run_dirs: list[Path],
    output_dir: Path,
) -> dict:
    registration, registration_sha256 = load_registration(registration_path)
    if output_dir.exists():
        raise ParityError("refusing to overwrite parity analysis directory")
    by_snapshot: dict[str, Path] = {}
    for run_dir in run_dirs:
        snapshot = _load_json(run_dir / "prelaunch.json").get("snapshot")
        if snapshot in by_snapshot:
            raise ParityError("duplicate checkpoint run")
        by_snapshot[str(snapshot)] = run_dir
    if set(by_snapshot) != set(registration["snapshots"]):
        raise ParityError("analysis requires all registered checkpoints")
    records = []
    common_runtime_receipts = set()
    for snapshot in registration["snapshots"]:
        disabled = verify_run(
            registration, registration_sha256, snapshot, by_snapshot[snapshot]
        )
        thinking = _historical_thinking_rows(registration, snapshot)
        key = lambda row: (row["state_id"], row["condition_id"], row["sample_index"])
        thinking_map = {key(row): row for row in thinking}
        disabled_map = {key(row): row for row in disabled}
        if set(thinking_map) != set(disabled_map):
            raise ParityError(f"{snapshot}: paired request cells differ")
        n = len(disabled)
        thinking_recovery = sum(row["recovery_opportunity"] for row in thinking)
        disabled_recovery = sum(row["recovery_opportunity"] for row in disabled)
        thinking_structured = sum(row["has_structured_tool_call"] for row in thinking)
        disabled_structured = sum(row["has_structured_tool_call"] for row in disabled)
        transitions = Counter(
            (_route(thinking_map[cell]), _route(disabled_map[cell]))
            for cell in sorted(thinking_map)
        )
        schema_records = {}
        source_registration, _design, _grid = _source_inputs(registration)
        conditions = {
            item["condition_id"]: item for item in source_registration["conditions"]
        }
        for schema in ("absent", "present"):
            cells = [
                cell for cell in thinking_map
                if conditions[cell[1]]["native_tool_schema"] == schema
            ]
            schema_records[schema] = {
                "requests": len(cells),
                "thinking_recovery": sum(
                    thinking_map[cell]["recovery_opportunity"] for cell in cells
                ),
                "disabled_recovery": sum(
                    disabled_map[cell]["recovery_opportunity"] for cell in cells
                ),
                "thinking_structured": sum(
                    thinking_map[cell]["has_structured_tool_call"] for cell in cells
                ),
                "disabled_structured": sum(
                    disabled_map[cell]["has_structured_tool_call"] for cell in cells
                ),
            }
        prelaunch = _load_json(by_snapshot[snapshot] / "prelaunch.json")
        common_runtime_receipts.add(
            prelaunch["endpoint_health"]["attestation"][
                "runtime_environment_receipt_sha256"
            ]
        )
        records.append({
            "snapshot": snapshot,
            "paired_requests": n,
            "thinking_recovery_count": thinking_recovery,
            "thinking_recovery_rate": thinking_recovery / n,
            "disabled_recovery_count": disabled_recovery,
            "disabled_recovery_rate": disabled_recovery / n,
            "recovery_rate_difference_disabled_minus_thinking": (
                disabled_recovery - thinking_recovery
            ) / n,
            "thinking_structured_count": thinking_structured,
            "thinking_structured_rate": thinking_structured / n,
            "disabled_structured_count": disabled_structured,
            "disabled_structured_rate": disabled_structured / n,
            "structured_rate_difference_disabled_minus_thinking": (
                disabled_structured - thinking_structured
            ) / n,
            "route_transitions": {
                f"{before}_to_{after}": count
                for (before, after), count in sorted(transitions.items())
            },
            "by_native_schema": schema_records,
            "disabled_finish_reason_counts": dict(sorted(Counter(
                str(row.get("finish_reason")) for row in disabled
            ).items())),
        })
    directional_pass = all(
        row["recovery_rate_difference_disabled_minus_thinking"] < 0
        for row in records
    )
    summary = {
        "schema_version": ANALYSIS_SCHEMA,
        "study_id": registration["study_id"],
        "status": "complete",
        "registration_sha256": registration_sha256,
        "pilot_states_excluded": registration["pilot_disclosure"]["state_indices"],
        "confirmatory_state_indices": registration["confirmatory_panel"][
            "state_indices"
        ],
        "runtime_environment_consistent_across_checkpoints": (
            len(common_runtime_receipts) == 1
        ),
        "checkpoint_results": records,
        "registered_directional_criterion_passed": directional_pass,
        "claim_boundary": registration["claim_boundary"],
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    _write_json(output_dir / "analysis-summary.json", summary)
    artifacts = [{
        "path": "analysis-summary.json",
        "size_bytes": (output_dir / "analysis-summary.json").stat().st_size,
        "sha256": _sha256_file(output_dir / "analysis-summary.json"),
    }]
    _write_json(output_dir / "artifact-index.json", {
        "schema_version": f"{ANALYSIS_SCHEMA}.artifacts",
        "files": artifacts,
        "tree_sha256": _sha256_json(artifacts),
    })
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--registration", type=Path, required=True)
    run_parser.add_argument("--endpoint", required=True)
    run_parser.add_argument("--snapshot", required=True)
    run_parser.add_argument("--out-dir", type=Path, required=True)
    analyze_parser = subparsers.add_parser("analyze")
    analyze_parser.add_argument("--registration", type=Path, required=True)
    analyze_parser.add_argument("--run-dir", type=Path, action="append", required=True)
    analyze_parser.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "run":
            result = asyncio.run(run(
                args.registration, args.endpoint, args.snapshot, args.out_dir
            ))
        else:
            result = analyze(args.registration, args.run_dir, args.out_dir)
    except ParityError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
