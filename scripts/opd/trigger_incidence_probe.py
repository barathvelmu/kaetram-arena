#!/usr/bin/env python3
"""Prepare, run, and analyze the registered local trigger-incidence probe.

The runner accepts loopback endpoints only. It never launches an endpoint and
never permits the metered-endpoint override used by some development probes.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import csv
import hashlib
import io
import json
import math
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[2]
for import_root in (
    REPO,
    REPO / "scripts" / "opd",
    REPO / "scripts" / "log_analysis",
):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from canonicalize import (  # noqa: E402
    docify_system_prompt,
    is_malformed,
    recover_tool_calls,
)
from endpoint_policy import require_zero_spend_endpoints  # noqa: E402
from opd_probe import reconstruct_session  # noqa: E402
from opd_round1 import turn_to_chat  # noqa: E402
from parse import session_meta  # noqa: E402
from tool_surface import MODEL_VISIBLE_TOOL_DEFINITIONS  # noqa: E402


REGISTRATION_SCHEMA = "kaetram.local-trigger-incidence-registration.v1"
DESIGN_SCHEMA = "kaetram.local-trigger-incidence-design.v1"
RUN_SCHEMA = "kaetram.local-trigger-incidence-run.v1"
ANALYSIS_SCHEMA = "kaetram.local-trigger-incidence-analysis.v1"
KWARG_IN_KEY = re.compile(r"<parameter=[^>\n]*=[^>\n]*>")
PYTHON_CALL = re.compile(r"<function=\w+\s*\(")
CORRUPT_CLOSE = re.compile(
    r"</(?!parameter>|function>|tool_call>|think>)[A-Za-z_]{0,12}>"
)


class ProbeError(RuntimeError):
    """Raised when the registered probe contract cannot be satisfied."""


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return sha256_bytes(payload)


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )


def load_registration(path: Path) -> tuple[dict, str]:
    try:
        registration = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeError(f"cannot load registration: {exc}") from exc
    if (
        not isinstance(registration, dict)
        or registration.get("schema_version") != REGISTRATION_SCHEMA
    ):
        raise ProbeError("unexpected registration schema")
    conditions = registration.get("conditions")
    snapshots = registration.get("snapshots")
    if not isinstance(conditions, list) or len(conditions) != 4:
        raise ProbeError("registration must contain four conditions")
    if not isinstance(snapshots, dict) or not snapshots:
        raise ProbeError("registration has no snapshots")
    condition_ids = [condition.get("condition_id") for condition in conditions]
    if len(set(condition_ids)) != len(condition_ids) or not all(condition_ids):
        raise ProbeError("registration condition IDs must be unique strings")
    return registration, sha256_file(path)


def _git_identity() -> dict:
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if status:
        raise ProbeError("outcome collection requires a clean Arena checkout")
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {"source_git_commit": commit, "dirty_paths": []}


def _render_decision_state(
    log_path: Path,
    *,
    decision_turn: int,
    max_history_messages: int,
) -> list[dict] | None:
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            base_messages, turns = reconstruct_session(log_path)
    except Exception:  # noqa: BLE001 - parseability is the registered gate
        return None
    rolling = list(base_messages)
    for turn_index, (turn, results) in enumerate(turns, start=1):
        if turn_index == decision_turn:
            head, history = rolling[:2], rolling[2:]
            messages = head + history[-max_history_messages:]
            return messages if messages else None
        rolling.append(turn_to_chat(turn))
        for result in results:
            rolling.append(
                {
                    "role": "tool",
                    "content": result.result_str,
                    "name": result.name,
                }
            )
    return None


def prepare_design(
    registration_path: Path,
    historical_root: Path,
    output_path: Path,
) -> dict:
    registration, registration_sha256 = load_registration(registration_path)
    if output_path.exists():
        raise ProbeError(f"refusing to overwrite design: {output_path}")
    state_contract = registration["state_pool"]
    source_glob = state_contract["source_glob"]
    logs = sorted(
        historical_root.glob(source_glob),
        key=lambda item: item.relative_to(historical_root).as_posix(),
    )
    if not logs:
        raise ProbeError(f"no source logs match {source_glob!r}")
    personality = state_contract["personality"]
    eligible_logs = [
        log_path
        for log_path in logs
        if (session_meta(log_path) or {}).get("personality") == personality
    ]
    target = int(state_contract["state_count"])
    stride = max(1, len(eligible_logs) // (2 * target))
    states = []
    for log_path in eligible_logs[::stride]:
        messages = _render_decision_state(
            log_path,
            decision_turn=int(state_contract["decision_turn"]),
            max_history_messages=int(state_contract["max_history_messages"]),
        )
        if messages is None:
            continue
        relative = log_path.relative_to(historical_root).as_posix()
        states.append(
            {
                "state_id": f"state-{len(states) + 1:02d}",
                "personality": personality,
                "source_log": relative,
                "source_log_sha256": sha256_file(log_path),
                "messages_sha256": sha256_json(messages),
                "messages": messages,
            }
        )
        if len(states) == target:
            break
    if len(states) != target:
        raise ProbeError(f"prepared {len(states)} states; registration requires {target}")
    design = {
        "schema_version": DESIGN_SCHEMA,
        "study_id": registration["study_id"],
        "registration_sha256": registration_sha256,
        "source_log_count": len(logs),
        "eligible_source_log_count": len(eligible_logs),
        "personality": personality,
        "selection_stride": stride,
        "states": states,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(output_path, design)
    receipt = {
        "schema_version": f"{DESIGN_SCHEMA}.receipt",
        "study_id": registration["study_id"],
        "registration_sha256": registration_sha256,
        "design_sha256": sha256_file(output_path),
        "state_count": len(states),
        "selected_source_tree_sha256": sha256_json(
            [
                {
                    "source_log": state["source_log"],
                    "source_log_sha256": state["source_log_sha256"],
                    "messages_sha256": state["messages_sha256"],
                }
                for state in states
            ]
        ),
    }
    write_json(output_path.with_suffix(".receipt.json"), receipt)
    return receipt


def load_design(path: Path, registration: dict, registration_sha256: str) -> dict:
    try:
        design = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeError(f"cannot load design: {exc}") from exc
    if design.get("schema_version") != DESIGN_SCHEMA:
        raise ProbeError("unexpected design schema")
    if design.get("study_id") != registration["study_id"]:
        raise ProbeError("design study ID mismatch")
    if design.get("registration_sha256") != registration_sha256:
        raise ProbeError("design registration hash mismatch")
    states = design.get("states")
    if not isinstance(states, list) or len(states) != registration["state_pool"]["state_count"]:
        raise ProbeError("design state count mismatch")
    for state in states:
        messages = state.get("messages")
        if not isinstance(messages, list) or sha256_json(messages) != state.get(
            "messages_sha256"
        ):
            raise ProbeError(f"{state.get('state_id')}: message hash mismatch")
    return design


def surface_families(content: str) -> list[str]:
    families = []
    if KWARG_IN_KEY.search(content):
        families.append("kwarg_in_key")
    if PYTHON_CALL.search(content):
        families.append("python_call")
    if "<tool_call>" in content and CORRUPT_CLOSE.search(content):
        families.append("corrupt_close")
    if not families and is_malformed(content):
        families.append("other_malformed")
    return families


def condition_messages(messages: list[dict], documentation: str) -> list[dict]:
    copied = copy.deepcopy(messages)
    if documentation == "python_docs":
        return copied
    if documentation != "canonical_docs":
        raise ProbeError(f"unknown documentation condition: {documentation}")
    for message in copied:
        if message.get("role") == "system" and isinstance(message.get("content"), str):
            message["content"] = docify_system_prompt(message["content"])
    return copied


def _health_url(endpoint: str) -> str:
    return endpoint[:-3] + "/health" if endpoint.endswith("/v1") else endpoint + "/health"


async def endpoint_health(endpoint: str) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.get(_health_url(endpoint), timeout=10)
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise ProbeError("endpoint health is not ok")
    return payload


async def _request_one(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    *,
    endpoint: str,
    api_model: str,
    messages: list[dict],
    condition: dict,
    sampling: dict,
    state_id: str,
    state_index: int,
    sample_index: int,
    schedule_index: int,
) -> dict:
    seed = int(sampling["base_seed"]) + 100 * state_index + sample_index
    payload: dict[str, Any] = {
        "model": api_model,
        "messages": condition_messages(messages, condition["documentation"]),
        "max_tokens": sampling["max_tokens"],
        "temperature": sampling["temperature"],
        "top_p": sampling["top_p"],
        "top_k": sampling["top_k"],
        "presence_penalty": sampling["presence_penalty"],
        "seed": seed,
    }
    if condition["native_tool_schema"] == "present":
        payload["tools"] = copy.deepcopy(MODEL_VISIBLE_TOOL_DEFINITIONS)
    elif condition["native_tool_schema"] != "absent":
        raise ProbeError("unknown native-tool-schema condition")
    attempts = int(sampling["attempts"])
    started = time.monotonic()
    errors = []
    message: dict[str, Any] | None = None
    async with semaphore:
        for attempt in range(1, attempts + 1):
            try:
                response = await client.post(
                    f"{endpoint}/chat/completions",
                    json=payload,
                    timeout=float(sampling["request_timeout_seconds"]),
                )
                if response.status_code == 200:
                    body = response.json()
                    candidate = body["choices"][0]["message"]
                    if not isinstance(candidate, dict):
                        raise ValueError("response message is not an object")
                    message = candidate
                    break
                errors.append(f"attempt {attempt}: HTTP {response.status_code}")
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
                errors.append(f"attempt {attempt}: {type(exc).__name__}")
            if attempt < attempts:
                await asyncio.sleep(float(attempt))
    common = {
        "schema_version": RUN_SCHEMA,
        "schedule_index": schedule_index,
        "state_id": state_id,
        "state_index": state_index,
        "sample_index": sample_index,
        "seed": seed,
        "condition_id": condition["condition_id"],
        "documentation": condition["documentation"],
        "native_tool_schema": condition["native_tool_schema"],
        "latency_seconds": round(time.monotonic() - started, 6),
        "attempt_errors": errors,
    }
    if message is None:
        return {**common, "status": "failed"}
    content = message.get("content") or ""
    if not isinstance(content, str):
        content = ""
    tool_calls = message.get("tool_calls") or []
    if not isinstance(tool_calls, list):
        tool_calls = []
    recoverable = recover_tool_calls(content) if not tool_calls else []
    return {
        **common,
        "status": "ok",
        "response_message": message,
        "structured_tool_call_count": len(tool_calls),
        "has_structured_tool_call": bool(tool_calls),
        "has_content": bool(content),
        "malformed_emission": is_malformed(content),
        "malformed_families": surface_families(content),
        "recovery_opportunity": bool(recoverable),
        "recoverable_calls": recoverable,
    }


async def run_checkpoint(
    registration_path: Path,
    design_path: Path,
    endpoint: str,
    snapshot: str,
    output_dir: Path,
) -> dict:
    registration, registration_sha256 = load_registration(registration_path)
    design = load_design(design_path, registration, registration_sha256)
    endpoint = require_zero_spend_endpoints([endpoint])[0]
    if snapshot not in registration["snapshots"]:
        raise ProbeError(f"snapshot is not registered: {snapshot}")
    if output_dir.exists():
        raise ProbeError(f"refusing to overwrite outcome directory: {output_dir}")
    health = await endpoint_health(endpoint)
    expected = registration["snapshots"][snapshot]
    endpoint_contract = registration["endpoint_contract"]
    attestation = health.get("attestation")
    if not isinstance(attestation, dict):
        raise ProbeError("endpoint health lacks attestation")
    for key in ("api_model", "checkpoint_sha256"):
        if attestation.get(key) != expected[key]:
            raise ProbeError(f"endpoint {key} does not match registration")
    for key, expected_value in endpoint_contract.items():
        if attestation.get(key) != expected_value:
            raise ProbeError(f"endpoint {key} does not match registration")
    git_identity = _git_identity()
    output_dir.mkdir(parents=True, exist_ok=False)
    design_sha256 = sha256_file(design_path)
    prelaunch = {
        "schema_version": f"{RUN_SCHEMA}.prelaunch",
        "study_id": registration["study_id"],
        "snapshot": snapshot,
        "registration_sha256": registration_sha256,
        "design_sha256": design_sha256,
        "endpoint_health": health,
        "sampling": registration["sampling"],
        **git_identity,
    }
    write_json(output_dir / "prelaunch.json", prelaunch)

    conditions = registration["conditions"]
    sampling = registration["sampling"]
    tasks = []
    schedule_index = 0
    samples_per_state = int(sampling["samples_per_state_condition"])
    semaphore = asyncio.Semaphore(int(sampling["concurrency"]))
    async with httpx.AsyncClient() as client:
        for state_index, state in enumerate(design["states"]):
            for sample_index in range(samples_per_state):
                block_index = state_index * samples_per_state + sample_index
                offset = block_index % len(conditions)
                ordered = conditions[offset:] + conditions[:offset]
                for condition in ordered:
                    tasks.append(
                        _request_one(
                            client,
                            semaphore,
                            endpoint=endpoint,
                            api_model=expected["api_model"],
                            messages=state["messages"],
                            condition=condition,
                            sampling=sampling,
                            state_id=state["state_id"],
                            state_index=state_index,
                            sample_index=sample_index,
                            schedule_index=schedule_index,
                        )
                    )
                    schedule_index += 1
        results = await asyncio.gather(*tasks)
    results.sort(key=lambda row: row["schedule_index"])
    result_path = output_dir / "results.jsonl"
    with result_path.open("x") as handle:
        for row in results:
            row["snapshot"] = snapshot
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    completed = {
        "schema_version": f"{RUN_SCHEMA}.completed",
        "study_id": registration["study_id"],
        "snapshot": snapshot,
        "scheduled_requests": len(results),
        "successful_requests": sum(row["status"] == "ok" for row in results),
        "failed_requests": sum(row["status"] != "ok" for row in results),
        "recovery_opportunities": sum(
            bool(row.get("recovery_opportunity")) for row in results
        ),
        "malformed_emissions": sum(bool(row.get("malformed_emission")) for row in results),
        "structured_tool_responses": sum(
            bool(row.get("has_structured_tool_call")) for row in results
        ),
    }
    write_json(output_dir / "completed.json", completed)
    artifact_records = []
    for name in ("prelaunch.json", "results.jsonl", "completed.json"):
        path = output_dir / name
        artifact_records.append(
            {"path": name, "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}
        )
    index = {
        "schema_version": f"{RUN_SCHEMA}.artifacts",
        "study_id": registration["study_id"],
        "snapshot": snapshot,
        "files": artifact_records,
        "tree_sha256": sha256_json(artifact_records),
    }
    write_json(output_dir / "artifact-index.json", index)
    return completed


def _verify_run_directory(path: Path) -> tuple[dict, list[dict]]:
    index = json.loads((path / "artifact-index.json").read_text())
    for record in index.get("files", []):
        artifact = path / record["path"]
        if (
            artifact.stat().st_size != record["size_bytes"]
            or sha256_file(artifact) != record["sha256"]
        ):
            raise ProbeError(f"{path.name}: artifact mismatch for {record['path']}")
    prelaunch = json.loads((path / "prelaunch.json").read_text())
    rows = []
    for line_number, line in enumerate(
        (path / "results.jsonl").read_text().splitlines(), start=1
    ):
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProbeError(f"{path.name}: malformed result line {line_number}") from exc
        rows.append(row)
    return prelaunch, rows


def exact_sign_flip_p(numerators: list[int]) -> float:
    """Two-sided exact sign-flip p-value for equally scaled paired effects."""
    distribution = Counter({0: 1})
    for value in numerators:
        updated: Counter[int] = Counter()
        for total, count in distribution.items():
            updated[total + value] += count
            updated[total - value] += count
        distribution = updated
    observed = abs(sum(numerators))
    extreme = sum(count for total, count in distribution.items() if abs(total) >= observed)
    return extreme / (2 ** len(numerators))


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        return (math.nan, math.nan)
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    half = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total + z * z / (4 * total * total)
        )
        / denominator
    )
    return (max(0.0, centre - half), min(1.0, centre + half))


def analyze(
    registration_path: Path,
    design_path: Path,
    run_dirs: list[Path],
    output_dir: Path,
) -> dict:
    registration, registration_sha256 = load_registration(registration_path)
    design = load_design(design_path, registration, registration_sha256)
    if output_dir.exists():
        raise ProbeError(f"refusing to overwrite analysis directory: {output_dir}")
    rows = []
    seen_snapshots = set()
    for run_dir in run_dirs:
        prelaunch, run_rows = _verify_run_directory(run_dir)
        snapshot = prelaunch.get("snapshot")
        if snapshot in seen_snapshots or snapshot not in registration["snapshots"]:
            raise ProbeError("run directories must contain each registered snapshot once")
        if (
            prelaunch.get("registration_sha256") != registration_sha256
            or prelaunch.get("design_sha256") != sha256_file(design_path)
        ):
            raise ProbeError(f"{snapshot}: registration/design identity mismatch")
        seen_snapshots.add(snapshot)
        rows.extend(run_rows)
    expected_snapshots = set(registration["snapshots"])
    if seen_snapshots != expected_snapshots:
        raise ProbeError("analysis requires all registered snapshots")

    condition_ids = [item["condition_id"] for item in registration["conditions"]]
    state_ids = [item["state_id"] for item in design["states"]]
    sample_count = int(registration["sampling"]["samples_per_state_condition"])
    expected_keys = {
        (snapshot, condition, state_id, sample_index)
        for snapshot in expected_snapshots
        for condition in condition_ids
        for state_id in state_ids
        for sample_index in range(sample_count)
    }
    by_key = {}
    for row in rows:
        key = (
            row.get("snapshot"),
            row.get("condition_id"),
            row.get("state_id"),
            row.get("sample_index"),
        )
        if key in by_key:
            raise ProbeError(f"duplicate scheduled result: {key}")
        by_key[key] = row
    if set(by_key) != expected_keys:
        raise ProbeError("result schedule does not match the registration")

    complete = all(row.get("status") == "ok" for row in rows)
    cell_rows = []
    for snapshot in registration["snapshots"]:
        for condition in registration["conditions"]:
            subset = [
                by_key[(snapshot, condition["condition_id"], state_id, sample_index)]
                for state_id in state_ids
                for sample_index in range(sample_count)
            ]
            successes = sum(bool(row.get("recovery_opportunity")) for row in subset)
            lower, upper = wilson_interval(successes, len(subset))
            cell_rows.append(
                {
                    "snapshot": snapshot,
                    "condition_id": condition["condition_id"],
                    "documentation": condition["documentation"],
                    "native_tool_schema": condition["native_tool_schema"],
                    "requests": len(subset),
                    "failures": sum(row.get("status") != "ok" for row in subset),
                    "recovery_opportunities": successes,
                    "opportunity_rate": successes / len(subset),
                    "wilson_95_lower": lower,
                    "wilson_95_upper": upper,
                    "malformed_emissions": sum(
                        bool(row.get("malformed_emission")) for row in subset
                    ),
                    "structured_tool_responses": sum(
                        bool(row.get("has_structured_tool_call")) for row in subset
                    ),
                }
            )

    contrasts = []
    if complete:
        condition_lookup = {
            (item["documentation"], item["native_tool_schema"]): item["condition_id"]
            for item in registration["conditions"]
        }
        contrast_specs = (
            ("native_tools_main", 2 * sample_count),
            ("canonical_docs_main", 2 * sample_count),
            ("interaction", sample_count),
        )
        for snapshot in registration["snapshots"]:
            numerators_by_name = {name: [] for name, _ in contrast_specs}
            for state_id in state_ids:
                counts = {}
                for docs in ("python_docs", "canonical_docs"):
                    for tools in ("absent", "present"):
                        condition_id = condition_lookup[(docs, tools)]
                        counts[(docs, tools)] = sum(
                            bool(
                                by_key[
                                    (snapshot, condition_id, state_id, sample_index)
                                ].get("recovery_opportunity")
                            )
                            for sample_index in range(sample_count)
                        )
                numerators_by_name["native_tools_main"].append(
                    counts[("python_docs", "present")]
                    + counts[("canonical_docs", "present")]
                    - counts[("python_docs", "absent")]
                    - counts[("canonical_docs", "absent")]
                )
                numerators_by_name["canonical_docs_main"].append(
                    counts[("canonical_docs", "absent")]
                    + counts[("canonical_docs", "present")]
                    - counts[("python_docs", "absent")]
                    - counts[("python_docs", "present")]
                )
                numerators_by_name["interaction"].append(
                    counts[("canonical_docs", "present")]
                    - counts[("canonical_docs", "absent")]
                    - counts[("python_docs", "present")]
                    + counts[("python_docs", "absent")]
                )
            for name, denominator in contrast_specs:
                numerators = numerators_by_name[name]
                raw_p = exact_sign_flip_p(numerators)
                contrasts.append(
                    {
                        "snapshot": snapshot,
                        "contrast": name,
                        "state_clusters": len(state_ids),
                        "effect_rate_difference": (
                            sum(numerators) / (denominator * len(state_ids))
                        ),
                        "exact_sign_flip_p": raw_p,
                        "bonferroni_p": min(1.0, raw_p * 9),
                    }
                )

    total_successes = sum(row.get("status") == "ok" for row in rows)
    total_opportunities = sum(
        bool(row.get("recovery_opportunity")) for row in rows if row.get("status") == "ok"
    )
    summary = {
        "schema_version": ANALYSIS_SCHEMA,
        "study_id": registration["study_id"],
        "registration_sha256": registration_sha256,
        "design_sha256": sha256_file(design_path),
        "analysis_status": "complete" if complete else "incomplete",
        "scheduled_requests": len(expected_keys),
        "successful_requests": total_successes,
        "failed_requests": len(rows) - total_successes,
        "recovery_opportunities": total_opportunities,
        "zero_event_one_sided_95_upper": (
            1 - math.pow(0.05, 1 / total_successes)
            if total_successes and total_opportunities == 0
            else None
        ),
        "claim_boundary": registration["claim_boundary"],
        "cells": cell_rows,
        "registered_contrasts": contrasts,
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    write_json(output_dir / "analysis-summary.json", summary)
    for filename, records in (("cells.csv", cell_rows), ("contrasts.csv", contrasts)):
        path = output_dir / filename
        if not records:
            path.write_text("")
            continue
        with path.open("x", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
    artifacts = []
    for name in ("analysis-summary.json", "cells.csv", "contrasts.csv"):
        path = output_dir / name
        artifacts.append(
            {"path": name, "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}
        )
    write_json(
        output_dir / "artifact-index.json",
        {
            "schema_version": f"{ANALYSIS_SCHEMA}.artifacts",
            "files": artifacts,
            "tree_sha256": sha256_json(artifacts),
        },
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--registration", type=Path, required=True)
    prepare.add_argument("--historical-root", type=Path, required=True)
    prepare.add_argument("--out", type=Path, required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--registration", type=Path, required=True)
    run.add_argument("--design", type=Path, required=True)
    run.add_argument("--endpoint", required=True)
    run.add_argument("--snapshot", required=True)
    run.add_argument("--out-dir", type=Path, required=True)
    analyze_parser = subparsers.add_parser("analyze")
    analyze_parser.add_argument("--registration", type=Path, required=True)
    analyze_parser.add_argument("--design", type=Path, required=True)
    analyze_parser.add_argument("--run-dir", type=Path, action="append", required=True)
    analyze_parser.add_argument("--out-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "prepare":
        receipt = prepare_design(args.registration, args.historical_root, args.out)
        print(json.dumps(receipt, indent=2, sort_keys=True))
    elif args.command == "run":
        completed = asyncio.run(
            run_checkpoint(
                args.registration,
                args.design,
                args.endpoint,
                args.snapshot,
                args.out_dir,
            )
        )
        print(json.dumps(completed, indent=2, sort_keys=True))
    else:
        summary = analyze(
            args.registration,
            args.design,
            args.run_dir,
            args.out_dir,
        )
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
