#!/usr/bin/env python3
"""Run and verify the registered thinking-off parity confirmation.

The study reuses the exact V2 request grid and retained thinking-enabled rows,
but excludes the three states used in the debugging pilot. New requests apply
the registered ``enable_thinking=false`` render intervention in a separately
recreated pinned environment; analysis verifies every identity that can be
matched and discloses the source/runtime receipts that cannot.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import math
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.opd import trigger_incidence_probe as trigger  # noqa: E402
from scripts.opd.endpoint_policy import require_zero_spend_endpoints  # noqa: E402
from scripts.opd.verify_trigger_incidence_artifact_v2 import (  # noqa: E402
    verify_bundle as verify_v2_bundle,
)
from tool_surface import MODEL_VISIBLE_TOOL_DEFINITIONS  # noqa: E402


REGISTRATION_SCHEMA = "kaetram.local-serving-regime-parity-registration.v1"
RUN_SCHEMA = "kaetram.local-serving-regime-parity-run.v1"
ANALYSIS_SCHEMA = "kaetram.local-serving-regime-parity-analysis.v1"
V2_ARTIFACT_RELATIVE = Path("research/artifacts/local-trigger-incidence-v2")
V2_INDEX_SHA256 = "04a26f53ce24fa9578c0e49d55b946321347f9de2a1dd81e0739822d57978562"
V2_GRID_SHA256 = "e620ec9910b7447d0a37681e7257a86cd588766188b7f5135eb961fbb83bf935"
V2_RUN_SCHEMA = "kaetram.local-trigger-incidence-run.v1"
NEW_RUN_ARTIFACTS = {"prelaunch.json", "results.jsonl", "postflight.json"}
CLASSIFICATION_FIELDS = {
    "structured_tool_call_count",
    "has_structured_tool_call",
    "no_structured_tool_call",
    "has_content",
    "malformed_emission",
    "malformed_families",
    "recovery_opportunity",
    "recoverable_calls",
}
NEW_OK_ROW_FIELDS = {
    "schema_version",
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
    "latency_seconds",
    "attempt_errors",
    "status",
    "finish_reason",
    "usage",
    "response_message",
    *CLASSIFICATION_FIELDS,
}
EXPERIMENT_CODE_PATHS = (
    Path("research/experiments/local-serving-regime-parity-v1.json"),
    Path("scripts/opd/serving_regime_parity_probe.py"),
    Path("scripts/local_mlx_endpoint.py"),
    Path("scripts/mlx_seeded_server.py"),
    Path("scripts/opd/trigger_incidence_probe.py"),
    Path("scripts/opd/canonicalize.py"),
)


class ParityError(RuntimeError):
    """Raised when the registered parity contract is not satisfied."""


def _sha256_file(path: Path) -> str:
    return trigger.sha256_file(path)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return trigger.sha256_json(value)


def _is_lower_hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ParityError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ParityError(f"non-finite JSON constant: {value}")


def _loads_strict(text: str) -> Any:
    return json.loads(
        text,
        object_pairs_hook=_strict_object,
        parse_constant=_reject_constant,
    )


def _load_json(path: Path) -> Any:
    try:
        return _loads_strict(path.read_text())
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


def _git_blob(commit: str, relative: Path) -> bytes:
    if not commit or any(part in {"", ".."} for part in relative.parts):
        raise ParityError("invalid Git source identity")
    completed = subprocess.run(
        ["git", "show", f"{commit}:{relative.as_posix()}"],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ParityError(f"missing source blob at {commit}: {relative}")
    return completed.stdout


def _experiment_code_receipts(
    source_commit: str,
    registration_sha256: str,
) -> list[dict[str, Any]]:
    records = []
    for relative in EXPERIMENT_CODE_PATHS:
        blob = _git_blob(source_commit, relative)
        digest = _sha256_bytes(blob)
        if (
            relative == EXPERIMENT_CODE_PATHS[0]
            and digest != registration_sha256
        ):
            raise ParityError("prelaunch commit registration differs from the run")
        records.append({
            "path": relative.as_posix(),
            "size_bytes": len(blob),
            "sha256": digest,
        })
    return records


def _v2_index() -> dict:
    index_path = ROOT / V2_ARTIFACT_RELATIVE / "artifact-index.json"
    if not index_path.is_file() or _sha256_file(index_path) != V2_INDEX_SHA256:
        raise ParityError("V2 artifact index differs from the prior trust root")
    index = _load_json(index_path)
    files = index.get("files")
    if not isinstance(files, list):
        raise ParityError("V2 artifact index has no file inventory")
    records = {record.get("path"): record for record in files if isinstance(record, dict)}
    grid = records.get("design/expected-request-grid.jsonl")
    if not isinstance(grid, dict) or grid.get("sha256") != V2_GRID_SHA256:
        raise ParityError("V2 request grid is not bound by the prior trust root")
    grid_path = ROOT / V2_ARTIFACT_RELATIVE / "design/expected-request-grid.jsonl"
    if not grid_path.is_file() or _sha256_file(grid_path) != V2_GRID_SHA256:
        raise ParityError("V2 request grid differs from the prior trust root")
    return index


def _verify_prior_v2_identity(source_commit: str) -> dict:
    index_relative = V2_ARTIFACT_RELATIVE / "artifact-index.json"
    if _sha256_bytes(_git_blob(source_commit, index_relative)) != V2_INDEX_SHA256:
        raise ParityError("prelaunch commit does not contain the trusted V2 index")
    grid_relative = V2_ARTIFACT_RELATIVE / "design/expected-request-grid.jsonl"
    if _sha256_bytes(_git_blob(source_commit, grid_relative)) != V2_GRID_SHA256:
        raise ParityError("prelaunch commit does not contain the trusted V2 grid")
    prior_archival_wrapper = _git_blob(source_commit, Path("paper/tmlr/arxiv.tex"))
    if V2_INDEX_SHA256.encode() not in prior_archival_wrapper:
        raise ParityError("prelaunch commit lacks the prior published V2 trust root")
    v2_index = _v2_index()
    historical_source_commit = v2_index.get("experiment_source_git_commit")
    endpoint_record = next(
        (
            record for record in v2_index.get("code_files", [])
            if record.get("path") == "scripts/local_mlx_endpoint.py"
        ),
        None,
    )
    endpoint_blob = _git_blob(
        str(historical_source_commit), Path("scripts/local_mlx_endpoint.py")
    )
    if (
        not isinstance(endpoint_record, dict)
        or _sha256_bytes(endpoint_blob) != endpoint_record.get("sha256")
        or b"enable_thinking=True" not in endpoint_blob
        or b'{"enable_thinking":true}' not in endpoint_blob
    ):
        raise ParityError("trusted V2 source does not attest thinking enabled")
    try:
        verification = verify_v2_bundle(
            ROOT / V2_ARTIFACT_RELATIVE,
            expected_index_sha256=V2_INDEX_SHA256,
        )
    except Exception as exc:  # The independent verifier owns detailed errors.
        raise ParityError("independent V2 bundle verification failed") from exc
    return {
        **verification,
        "thinking_enabled_source_git_commit": historical_source_commit,
        "thinking_enabled_endpoint_source_sha256": endpoint_record["sha256"],
        "thinking_enabled_literals_verified": True,
    }


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
    _v2_index()
    source = registration["source_panel"]
    source_registration = _load_json(
        _registered_path(source, "registration", "registration_sha256")
    )
    source_design = _load_json(_registered_path(source, "design", "design_sha256"))
    grid_path = ROOT / source["expected_request_grid"]
    if not grid_path.is_file() or _sha256_file(grid_path) != V2_GRID_SHA256:
        raise ParityError("registered expected request grid identity mismatch")
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
        return [_loads_strict(line) for line in path.read_text().splitlines() if line]
    except (OSError, json.JSONDecodeError) as exc:
        raise ParityError(f"cannot read result rows: {path}") from exc


def _validate_new_row(snapshot: str, row: dict) -> None:
    if set(row) != NEW_OK_ROW_FIELDS or row.get("schema_version") != RUN_SCHEMA:
        raise ParityError(f"{snapshot}: invalid result-row schema")
    if (
        row.get("status") != "ok"
        or any(
            not isinstance(row.get(field), str)
            for field in ("snapshot", "state_id", "condition_id")
        )
        or any(
            not isinstance(row.get(field), int) or isinstance(row.get(field), bool)
            for field in ("schedule_index", "state_index", "sample_index", "seed")
        )
        or any(
            not _is_lower_hex(row.get(field), 64)
            for field in ("messages_sha256", "request_payload_sha256")
        )
        or (
            row.get("tools_sha256") is not None
            and not _is_lower_hex(row.get("tools_sha256"), 64)
        )
        or not isinstance(row.get("response_message"), dict)
        or not isinstance(row.get("attempt_errors"), list)
        or any(not isinstance(item, str) for item in row["attempt_errors"])
        or not isinstance(row.get("latency_seconds"), (int, float))
        or isinstance(row.get("latency_seconds"), bool)
        or not math.isfinite(row["latency_seconds"])
        or row["latency_seconds"] < 0
        or (
            row.get("finish_reason") is not None
            and not isinstance(row.get("finish_reason"), str)
        )
        or (
            row.get("usage") is not None
            and not isinstance(row.get("usage"), dict)
        )
        or not isinstance(row.get("structured_tool_call_count"), int)
        or isinstance(row.get("structured_tool_call_count"), bool)
        or row["structured_tool_call_count"] < 0
        or any(
            not isinstance(row.get(field), bool)
            for field in (
                "has_structured_tool_call",
                "no_structured_tool_call",
                "has_content",
                "malformed_emission",
                "recovery_opportunity",
            )
        )
        or not isinstance(row.get("malformed_families"), list)
        or any(not isinstance(item, str) for item in row["malformed_families"])
        or not isinstance(row.get("recoverable_calls"), list)
        or any(not isinstance(item, dict) for item in row["recoverable_calls"])
    ):
        raise ParityError(f"{snapshot}: malformed result-row value")


def verify_run(
    registration: dict,
    registration_sha256: str,
    snapshot: str,
    run_dir: Path,
) -> list[dict]:
    if not run_dir.is_dir() or run_dir.is_symlink():
        raise ParityError(f"{snapshot}: run path is not a regular directory")
    directory_entries = list(run_dir.iterdir())
    expected_names = NEW_RUN_ARTIFACTS | {"artifact-index.json"}
    if (
        {entry.name for entry in directory_entries} != expected_names
        or len(directory_entries) != len(expected_names)
        or any(entry.is_symlink() or not entry.is_file() for entry in directory_entries)
    ):
        raise ParityError(f"{snapshot}: non-canonical run directory inventory")
    schedule = expected_schedule(registration, snapshot)
    expected = {
        (row["state_id"], row["condition_id"], row["sample_index"], row["seed"]): row
        for row, _payload in schedule
    }
    prelaunch = _load_json(run_dir / "prelaunch.json")
    postflight = _load_json(run_dir / "postflight.json")
    index = _load_json(run_dir / "artifact-index.json")
    files = index.get("files")
    if (
        index.get("schema_version") != f"{RUN_SCHEMA}.artifacts"
        or index.get("study_id") != registration["study_id"]
        or index.get("snapshot") != snapshot
        or not isinstance(files, list)
        or index.get("tree_sha256") != _sha256_json(files)
    ):
        raise ParityError(f"{snapshot}: invalid artifact index")
    paths = [record.get("path") for record in files if isinstance(record, dict)]
    if (
        len(paths) != len(files)
        or len(paths) != len(set(paths))
        or set(paths) != NEW_RUN_ARTIFACTS
        or any(Path(str(path)).name != path for path in paths)
    ):
        raise ParityError(f"{snapshot}: non-canonical artifact inventory")
    for record in files:
        if (
            not isinstance(record, dict)
            or set(record) != {"path", "size_bytes", "sha256"}
            or not isinstance(record.get("size_bytes"), int)
            or isinstance(record.get("size_bytes"), bool)
            or record["size_bytes"] < 0
            or not _is_lower_hex(record.get("sha256"), 64)
        ):
            raise ParityError(f"{snapshot}: malformed artifact record")
        artifact = run_dir / record["path"]
        if (
            artifact.is_symlink()
            or not artifact.is_file()
            or artifact.stat().st_size != record["size_bytes"]
            or _sha256_file(artifact) != record["sha256"]
        ):
            raise ParityError(f"{snapshot}: artifact digest mismatch")
    if (
        prelaunch.get("schema_version") != f"{RUN_SCHEMA}.prelaunch"
        or prelaunch.get("study_id") != registration["study_id"]
        or prelaunch.get("registration_sha256") != registration_sha256
        or prelaunch.get("snapshot") != snapshot
        or prelaunch.get("dirty_paths") != []
        or not _is_lower_hex(prelaunch.get("source_git_commit"), 40)
        or prelaunch.get("expected_requests") != len(schedule)
        or prelaunch.get("expected_request_grid_sha256") != V2_GRID_SHA256
        or postflight.get("schema_version") != f"{RUN_SCHEMA}.postflight"
        or postflight.get("study_id") != registration["study_id"]
        or postflight.get("snapshot") != snapshot
        or postflight.get("endpoint_identity_stable") is not True
        or prelaunch.get("endpoint_health") != postflight.get("endpoint_health")
        or not isinstance(postflight.get("duration_seconds"), (int, float))
        or isinstance(postflight.get("duration_seconds"), bool)
        or postflight["duration_seconds"] <= 0
    ):
        raise ParityError(f"{snapshot}: invalid pre/postflight binding")
    validate_health(registration, snapshot, prelaunch["endpoint_health"])
    rows = _read_rows(run_dir / "results.jsonl")
    if len(rows) != len(expected) or any(row.get("status") != "ok" for row in rows):
        raise ParityError(f"{snapshot}: incomplete confirmatory grid")
    finish_counts = dict(sorted(Counter(
        str(row.get("finish_reason")) for row in rows
    ).items()))
    if (
        postflight.get("completed_requests") != len(rows)
        or postflight.get("failed_requests") != 0
        or postflight.get("finish_reason_counts") != finish_counts
    ):
        raise ParityError(f"{snapshot}: postflight counts disagree with rows")
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ParityError(f"{snapshot}: result row is not an object")
        _validate_new_row(snapshot, row)
        key = (
            row["state_id"], row["condition_id"], row["sample_index"], row["seed"]
        )
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
        if bool(row["has_structured_tool_call"]) and bool(row["recovery_opportunity"]):
            raise ParityError(f"{snapshot}: routing outcomes are not exclusive")
    return rows


def _historical_thinking_rows(
    registration: dict,
    snapshot: str,
    conditions: dict[str, dict],
) -> tuple[list[dict], dict]:
    root = ROOT / registration["source_panel"]["thinking_arm_artifact"] / "runs" / snapshot
    rows = _read_rows(root / "results.jsonl")
    allowed = set(registration["confirmatory_panel"]["state_indices"])
    selected = [row for row in rows if row.get("state_index") in allowed]
    schedule = expected_schedule(registration, snapshot)
    expected_rows = {
        (row["state_id"], row["condition_id"], row["sample_index"], row["seed"]): row
        for row, _payload in schedule
    }
    if (
        len(selected) != len(expected_rows)
        or any(row.get("status") != "ok" for row in selected)
    ):
        raise ParityError(f"{snapshot}: historical thinking arm is incomplete")
    seen = set()
    for row in selected:
        key = (
            row.get("state_id"), row.get("condition_id"),
            row.get("sample_index"), row.get("seed"),
        )
        if key in seen or key not in expected_rows:
            raise ParityError(f"{snapshot}: historical row grid mismatch")
        seen.add(key)
        source = expected_rows[key]
        for field in (
            "snapshot", "schedule_index", "state_index", "seed",
        ):
            if row.get(field) != source.get(field):
                raise ParityError(f"{snapshot}: historical binding mismatch: {field}")
        condition = conditions[row["condition_id"]]
        if (
            row.get("schema_version") != V2_RUN_SCHEMA
            or row.get("documentation") != condition["documentation"]
            or row.get("native_tool_schema") != condition["native_tool_schema"]
        ):
            raise ParityError(f"{snapshot}: historical factor labels mismatch")
        message = row.get("response_message")
        if not isinstance(message, dict):
            raise ParityError(f"{snapshot}: historical response message is absent")
        recomputed = trigger.classify_response_message(message)
        if any(row.get(field) != value for field, value in recomputed.items()):
            raise ParityError(f"{snapshot}: historical outcome does not reclassify")
        if bool(row["has_structured_tool_call"]) and bool(row["recovery_opportunity"]):
            raise ParityError(f"{snapshot}: historical routes are not exclusive")
    run_index_path = root / "artifact-index.json"
    run_index = _load_json(run_index_path)
    return selected, {
        "artifact_index_sha256": _sha256_file(run_index_path),
        "tree_sha256": run_index.get("tree_sha256"),
        "source_git_commit": _load_json(root / "prelaunch.json").get(
            "source_git_commit"
        ),
        "runtime_environment_receipt_sha256": _load_json(
            root / "prelaunch.json"
        )["endpoint_health"]["attestation"][
            "runtime_environment_receipt_sha256"
        ],
    }


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
    analysis_identity = _repository_identity()
    by_snapshot: dict[str, Path] = {}
    for run_dir in run_dirs:
        snapshot = _load_json(run_dir / "prelaunch.json").get("snapshot")
        if snapshot in by_snapshot:
            raise ParityError("duplicate checkpoint run")
        by_snapshot[str(snapshot)] = run_dir
    if set(by_snapshot) != set(registration["snapshots"]):
        raise ParityError("analysis requires all registered checkpoints")
    new_source_commits = {
        _load_json(run_dir / "prelaunch.json").get("source_git_commit")
        for run_dir in by_snapshot.values()
    }
    if len(new_source_commits) != 1 or None in new_source_commits:
        raise ParityError("new runs do not share one prelaunch source commit")
    new_source_commit = str(next(iter(new_source_commits)))
    v2_verification = _verify_prior_v2_identity(new_source_commit)
    experiment_code_receipts = _experiment_code_receipts(
        new_source_commit,
        registration_sha256,
    )
    source_registration, _source_design, _source_grid = _source_inputs(registration)
    conditions = {
        item["condition_id"]: item for item in source_registration["conditions"]
    }
    records = []
    new_runtime_receipts = set()
    historical_runtime_receipts = set()
    historical_source_commits = set()
    historical_render_contracts = set()
    run_inputs = {}
    parity_fields = (
        "api_model",
        "chat_template_sha256",
        "checkpoint_sha256",
        "fix_mistral_regex",
        "sampling_contract_sha256",
        "snapshot_lock_sha256",
        "snapshot_tree_sha256",
        "tokenizer_sha256",
        "tokenizer_source_revision",
    )
    for snapshot in registration["snapshots"]:
        disabled = verify_run(
            registration, registration_sha256, snapshot, by_snapshot[snapshot]
        )
        thinking, historical_identity = _historical_thinking_rows(
            registration, snapshot, conditions
        )
        key = lambda row: (
            row["state_id"], row["condition_id"], row["sample_index"], row["seed"]
        )
        thinking_map = {key(row): row for row in thinking}
        disabled_map = {key(row): row for row in disabled}
        if set(thinking_map) != set(disabled_map):
            raise ParityError(f"{snapshot}: paired request cells differ")
        n = len(disabled)
        thinking_recovery = sum(row["recovery_opportunity"] for row in thinking)
        disabled_recovery = sum(row["recovery_opportunity"] for row in disabled)
        thinking_structured = sum(row["has_structured_tool_call"] for row in thinking)
        disabled_structured = sum(row["has_structured_tool_call"] for row in disabled)
        thinking_no_candidate = n - thinking_recovery - thinking_structured
        disabled_no_candidate = n - disabled_recovery - disabled_structured
        transitions = Counter(
            (_route(thinking_map[cell]), _route(disabled_map[cell]))
            for cell in sorted(thinking_map)
        )
        if sum(transitions.values()) != n:
            raise ParityError(f"{snapshot}: transition table is incomplete")
        schema_records = {}
        for schema in ("absent", "present"):
            cells = [
                cell for cell in thinking_map
                if conditions[cell[1]]["native_tool_schema"] == schema
            ]
            if len(cells) != n // 2:
                raise ParityError(f"{snapshot}: schema stratum denominator mismatch")
            tr = sum(thinking_map[cell]["recovery_opportunity"] for cell in cells)
            dr = sum(disabled_map[cell]["recovery_opportunity"] for cell in cells)
            ts = sum(thinking_map[cell]["has_structured_tool_call"] for cell in cells)
            ds = sum(disabled_map[cell]["has_structured_tool_call"] for cell in cells)
            tn = len(cells) - tr - ts
            dn = len(cells) - dr - ds
            schema_records[schema] = {
                "requests": len(cells),
                "thinking_recovery_count": tr,
                "thinking_recovery_rate": tr / len(cells),
                "disabled_recovery_count": dr,
                "disabled_recovery_rate": dr / len(cells),
                "recovery_rate_difference_disabled_minus_thinking": (
                    dr - tr
                ) / len(cells),
                "thinking_structured_count": ts,
                "thinking_structured_rate": ts / len(cells),
                "disabled_structured_count": ds,
                "disabled_structured_rate": ds / len(cells),
                "structured_rate_difference_disabled_minus_thinking": (
                    ds - ts
                ) / len(cells),
                "thinking_no_candidate_count": tn,
                "thinking_no_candidate_rate": tn / len(cells),
                "disabled_no_candidate_count": dn,
                "disabled_no_candidate_rate": dn / len(cells),
                "no_candidate_rate_difference_disabled_minus_thinking": (
                    dn - tn
                ) / len(cells),
            }
        prelaunch = _load_json(by_snapshot[snapshot] / "prelaunch.json")
        new_attestation = prelaunch["endpoint_health"]["attestation"]
        historical_prelaunch = _load_json(
            ROOT / registration["source_panel"]["thinking_arm_artifact"]
            / "runs" / snapshot / "prelaunch.json"
        )
        historical_attestation = historical_prelaunch["endpoint_health"]["attestation"]
        for field in parity_fields:
            if new_attestation.get(field) != historical_attestation.get(field):
                raise ParityError(f"{snapshot}: cross-arm identity mismatch: {field}")
        new_runtime_receipts.add(
            new_attestation["runtime_environment_receipt_sha256"]
        )
        historical_runtime_receipts.add(
            historical_attestation["runtime_environment_receipt_sha256"]
        )
        historical_render_contracts.add(
            historical_attestation["render_contract_sha256"]
        )
        historical_source_commits.add(historical_identity["source_git_commit"])
        new_index_path = by_snapshot[snapshot] / "artifact-index.json"
        new_index = _load_json(new_index_path)
        run_inputs[snapshot] = {
            "thinking_enabled": historical_identity,
            "thinking_disabled": {
                "artifact_index_sha256": _sha256_file(new_index_path),
                "tree_sha256": new_index["tree_sha256"],
                "source_git_commit": prelaunch["source_git_commit"],
                "runtime_environment_receipt_sha256": new_attestation[
                    "runtime_environment_receipt_sha256"
                ],
            },
        }
        retried = sum(bool(row.get("attempt_errors")) for row in disabled)
        attempt_errors = sum(len(row.get("attempt_errors") or []) for row in disabled)
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
            "thinking_no_candidate_count": thinking_no_candidate,
            "thinking_no_candidate_rate": thinking_no_candidate / n,
            "disabled_no_candidate_count": disabled_no_candidate,
            "disabled_no_candidate_rate": disabled_no_candidate / n,
            "no_candidate_rate_difference_disabled_minus_thinking": (
                disabled_no_candidate - thinking_no_candidate
            ) / n,
            "route_transitions": {
                f"{before}_to_{after}": count
                for (before, after), count in sorted(transitions.items())
            },
            "by_native_schema": schema_records,
            "thinking_finish_reason_retained": False,
            "disabled_finish_reason_counts": dict(sorted(Counter(
                str(row.get("finish_reason")) for row in disabled
            ).items())),
            "disabled_retried_request_count": retried,
            "disabled_attempt_error_count": attempt_errors,
        })
    if len(new_runtime_receipts) != 1:
        raise ParityError("new checkpoint runs do not share one runtime receipt")
    if (
        len(historical_runtime_receipts) != 1
        or len(historical_source_commits) != 1
        or None in historical_source_commits
        or len(historical_render_contracts) != 1
    ):
        raise ParityError("historical checkpoint runs do not share one source identity")
    directional_pass = all(
        row["recovery_rate_difference_disabled_minus_thinking"] < 0
        for row in records
    )
    pooled_n = sum(row["paired_requests"] for row in records)
    pooled_transitions = Counter()
    for row in records:
        pooled_transitions.update(row["route_transitions"])
    if sum(pooled_transitions.values()) != pooled_n:
        raise ParityError("pooled transition table is incomplete")
    pooled = {"paired_requests": pooled_n}
    for route in ("recovery", "structured", "no_candidate"):
        thinking_count = sum(row[f"thinking_{route}_count"] for row in records)
        disabled_count = sum(row[f"disabled_{route}_count"] for row in records)
        pooled.update({
            f"thinking_{route}_count": thinking_count,
            f"thinking_{route}_rate": thinking_count / pooled_n,
            f"disabled_{route}_count": disabled_count,
            f"disabled_{route}_rate": disabled_count / pooled_n,
            f"{route}_rate_difference_disabled_minus_thinking": (
                disabled_count - thinking_count
            ) / pooled_n,
        })
    pooled["route_transitions"] = dict(sorted(pooled_transitions.items()))
    pooled["interpretation"] = (
        "Presentation-only aggregation across the three fixed checkpoints; it does "
        "not replace the registered all-three-checkpoint directional criterion."
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
        "runtime_environment_consistent_across_new_checkpoints": True,
        "runtime_environment_receipt_equal_across_arms": (
            new_runtime_receipts == historical_runtime_receipts
        ),
        "source_git_commit_equal_across_arms": (
            {new_source_commit} == historical_source_commits
        ),
        "matched_cross_arm_identities": list(parity_fields),
        "intervention": {
            "factor": "generation_template_args.enable_thinking",
            "thinking_enabled_render_contract_sha256": next(iter(
                historical_render_contracts
            )),
            "thinking_disabled_render_contract_sha256": registration[
                "endpoint_contract"
            ]["render_contract_sha256"],
            "separately_recreated_pinned_environments": True,
        },
        "input_identity": {
            "v2_bundle_verification": v2_verification,
            "v2_artifact_index_sha256": V2_INDEX_SHA256,
            "v2_expected_request_grid_sha256": V2_GRID_SHA256,
            "experiment_time_code_receipts": experiment_code_receipts,
            "runs": run_inputs,
        },
        "analysis_code_provenance": {
            **analysis_identity,
            "python_version": sys.version.split()[0],
            "analysis_script_sha256": _sha256_file(Path(__file__).resolve()),
        },
        "checkpoint_results": records,
        "pooled_descriptive_result": pooled,
        "registered_directional_criterion_passed": directional_pass,
        "claim_boundary": registration["claim_boundary"],
        "interpreted_claim_boundary": (
            "The result measures a matched finite-grid contrast under the registered "
            "thinking-mode render intervention on separately recreated pinned local "
            "MLX environments. The arms used different source commits and runtime "
            "receipts; the analysis verifies matching checkpoints, tokenizer, template, "
            "schema, sampling, and snapshot identities but does not prove literal "
            "single-factor isolation, isolate backend/provider differences, validate "
            "recovery, or establish gameplay utility."
        ),
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
