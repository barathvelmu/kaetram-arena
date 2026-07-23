#!/usr/bin/env python3
"""Verify and descriptively summarize the local weights × recovery factorial."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from eval_harness import compute_episode_metrics, parse_log, validate_eval_session_terminals  # noqa: E402
from run_manifest import sha256_json  # noqa: E402
from scripts.opd.analyze_local_weight_pilot import (  # noqa: E402
    AnalysisError,
    _api_error_count,
    _canonical_start_ok,
    _file_sha256,
    _load_json,
    _ordered_session_logs,
    _validate_cell_attestation,
    _validate_prelaunch,
    _validate_raw_emissions,
    _validate_state_boundaries,
    _verify_artifacts,
)
from scripts.opd.local_weight_pilot import (  # noqa: E402
    RECOVERY_FACTORIAL_SCHEMA_VERSION,
    RECOVERY_INVENTORY_SCHEMA_VERSION,
    RECOVERY_PRELAUNCH_SCHEMA_VERSION,
    load_manifest,
)
from scripts.opd.recovery_audit import audit_logs  # noqa: E402


WEIGHT_LABEL = {
    "base_2b": "base",
    "opd_r2_2b": "r2",
    "opd_r3_2b": "r3",
}

ARM_VALUE_METRICS = (
    "duration_seconds",
    "budget_overrun_seconds",
    "turns",
    "canonical_executed_calls",
    "canonical_executed_calls_per_minute",
    "canonical_tool_bearing_turns",
    "tool_parse_rate",
    "api_errors",
    "sub_sessions",
    "raw_generations",
    "generations_with_structured_call",
    "generations_without_structured_call",
    "structured_call_emission_rate",
    "raw_structured_calls",
    "raw_structured_calls_per_minute",
    "malformed_emissions",
    "recoverable_raw_calls",
    "recovered_calls",
    "recovered_execution_errors",
    "recovered_execution_successes",
    "repeat_recoveries_within_window",
    "core3_stages_advanced",
    "quest_stages_advanced",
    "xp_db_delta",
    "unique_positions",
)


def _validate_recovery_receipts(
    results_root: Path,
    results: dict,
    expected: bool,
    cell_id: str,
) -> None:
    meta = results.get("meta")
    if not isinstance(meta, dict) or meta.get("tool_recovery_enabled") is not expected:
        raise AnalysisError(f"{cell_id}: results recovery identity mismatch")
    raw_dir = results_root / "episode_001_raw"
    paths = [raw_dir / "harness_meta_template.json"]
    session_logs = sorted(raw_dir.glob("session_*.log"))
    session_receipts = sorted(raw_dir.glob("session_*.meta.json"))
    expected_receipts = {path.with_suffix(".meta.json") for path in session_logs}
    if not session_logs or set(session_receipts) != expected_receipts:
        raise AnalysisError(f"{cell_id}: no retained session recovery receipts")
    paths.extend(session_receipts)
    for path in paths:
        receipt = _load_json(path)
        if receipt.get("tool_recovery_enabled") is not expected:
            raise AnalysisError(
                f"{cell_id}: recovery identity mismatch in {path.name}"
            )


def _validate_recovery_accounting(
    session_logs: list[Path],
    raw_metrics: dict,
    canonical_counts: dict,
    recovery_enabled: bool,
) -> dict:
    audit = audit_logs(session_logs)
    if audit.get("schema_version") != "kaetram-recovery-audit-v1":
        raise AnalysisError("recovery audit returned an unknown schema")
    totals = audit.get("totals")
    recovered = audit.get("recovered_by_tool")
    if not isinstance(totals, dict) or not isinstance(recovered, dict):
        raise AnalysisError("recovery audit is malformed")
    audited_sessions = totals.get("sessions")
    malformed = totals.get("malformed_emissions")
    recovered_total = totals.get("recovered_calls")
    recovered_errors = totals.get("recovered_execution_errors")
    repeat_recoveries = totals.get("repeat_recoveries_within_window")
    if not all(type(value) is int and value >= 0 for value in (
        malformed,
        recovered_total,
        recovered_errors,
        repeat_recoveries,
        audited_sessions,
    )):
        raise AnalysisError("recovery audit totals are malformed")
    if audited_sessions != len(session_logs):
        raise AnalysisError("recovery audit session count is inconsistent")
    if recovered_errors > recovered_total:
        raise AnalysisError("recovery execution errors exceed recovered calls")
    if repeat_recoveries > recovered_total:
        raise AnalysisError("repeat recoveries exceed recovered calls")
    if any(not isinstance(name, str) or not name for name in recovered) or any(
        type(value) is not int or value < 0 for value in recovered.values()
    ):
        raise AnalysisError("recovery audit tool counts are malformed")
    if sum(recovered.values()) != recovered_total:
        raise AnalysisError("recovery audit tool counts do not match its total")
    if malformed != raw_metrics["raw_malformed_emissions"]:
        raise AnalysisError(
            "recovery audit malformed count differs from raw endpoint emissions"
        )
    if (
        sum(raw_metrics["raw_recoverable_action_counts"].values())
        != raw_metrics["raw_recoverable_calls"]
    ):
        raise AnalysisError("recoverable raw call accounting is inconsistent")
    if not recovery_enabled and recovered_total:
        raise AnalysisError("recovery-off cell contains recovered calls")
    if recovery_enabled and recovered != raw_metrics["raw_recoverable_action_counts"]:
        raise AnalysisError(
            "recovered calls differ from recoverable raw endpoint emissions"
        )
    expected_counts = Counter(raw_metrics["raw_action_counts"])
    if recovery_enabled:
        expected_counts.update(recovered)
    if dict(expected_counts) != canonical_counts:
        raise AnalysisError(
            "canonical executions differ from raw structured plus recovered calls"
        )
    return {
        "malformed_emissions": raw_metrics["raw_malformed_emissions"],
        "recoverable_raw_calls": raw_metrics["raw_recoverable_calls"],
        "recovered_calls": recovered_total,
        "recovered_execution_errors": recovered_errors,
        "recovered_execution_successes": recovered_total - recovered_errors,
        "repeat_recoveries_within_window": repeat_recoveries,
        "recovered_by_tool": recovered,
    }


def _pair_differences(rows: list[dict]) -> dict:
    indexed = {
        (row["replicate"], row["weight"], row["recovery"]): row for row in rows
    }
    pairs = []
    incomplete = []
    for replicate in (1, 2, 3):
        for weight in ("base", "r2", "r3"):
            off = indexed.get((replicate, weight, False))
            on = indexed.get((replicate, weight, True))
            if off is None or on is None:
                incomplete.append({
                    "replicate": replicate,
                    "weight": weight,
                    "missing": [
                        label for label, row in (("off", off), ("on", on))
                        if row is None
                    ],
                })
                continue
            pairs.append({
                "replicate": replicate,
                "weight": weight,
                "pair_order": (
                    "on-first"
                    if on["schedule_index"] < off["schedule_index"]
                    else "off-first"
                ),
                "off_schedule_index": off["schedule_index"],
                "on_schedule_index": on["schedule_index"],
                "on_minus_off": {
                    metric: round(on[metric] - off[metric], 6)
                    for metric in (
                        "canonical_executed_calls",
                        "canonical_executed_calls_per_minute",
                        "raw_structured_calls",
                        "malformed_emissions",
                        "recovered_calls",
                        "core3_stages_advanced",
                        "quest_stages_advanced",
                        "xp_db_delta",
                        "unique_positions",
                    )
                },
            })
    return {"complete_pairs": pairs, "incomplete_pairs": incomplete}


def _summarize(rows: list[dict]) -> dict:
    result = {}
    for weight in ("base", "r2", "r3"):
        for recovery in (False, True):
            group = sorted(
                (
                    row for row in rows
                    if row["weight"] == weight and row["recovery"] is recovery
                ),
                key=lambda row: row["replicate"],
            )
            key = f"{weight}-recovery-{'on' if recovery else 'off'}"
            values = {
                metric: [row[metric] for row in group]
                for metric in ARM_VALUE_METRICS
            }
            means = {
                metric: (
                    round(statistics.mean(metric_values), 6)
                    if metric_values else None
                )
                for metric, metric_values in values.items()
            }
            result[key] = {
                "n_valid": len(group),
                "n_registered": 3,
                "cell_ids": [row["cell_id"] for row in group],
                "replicates": [row["replicate"] for row in group],
                "missing_replicates": sorted(
                    {1, 2, 3} - {row["replicate"] for row in group}
                ),
                "schedule_indices": [row["schedule_index"] for row in group],
                "values": values,
                "means": means,
                "canonical_executed_calls": [
                    row["canonical_executed_calls"] for row in group
                ],
                "mean_canonical_executed_calls_per_minute": round(
                    statistics.mean(
                        row["canonical_executed_calls_per_minute"] for row in group
                    ),
                    6,
                ) if group else None,
                "raw_generations": sum(row["raw_generations"] for row in group),
                "generations_with_structured_call": sum(
                    row["generations_with_structured_call"] for row in group
                ),
                "generations_without_structured_call": sum(
                    row["generations_without_structured_call"] for row in group
                ),
                "structured_call_emission_rate": round(
                    sum(
                        row["generations_with_structured_call"] for row in group
                    ) / sum(row["raw_generations"] for row in group),
                    6,
                ) if group else None,
                "raw_structured_calls": sum(
                    row["raw_structured_calls"] for row in group
                ),
                "malformed_emissions": sum(
                    row["malformed_emissions"] for row in group
                ),
                "recovered_calls": sum(row["recovered_calls"] for row in group),
                "recovered_execution_successes": sum(
                    row["recovered_execution_successes"] for row in group
                ),
                "api_errors": sum(row["api_errors"] for row in group),
                "zero_turn_cells": sum(row["turns"] == 0 for row in group),
                "core3_stages_advanced": [
                    row["core3_stages_advanced"] for row in group
                ],
                "quest_stages_advanced": [
                    row["quest_stages_advanced"] for row in group
                ],
            }
    return result


def analyze(root: Path, manifest_path: Path) -> dict:
    manifest, manifest_sha256 = load_manifest(manifest_path)
    if manifest.get("schema_version") != RECOVERY_FACTORIAL_SCHEMA_VERSION:
        raise AnalysisError("manifest is not the reviewed recovery factorial")
    prelaunch_path = root / "prelaunch.json"
    completed_path = root / "completed-inventory.json"
    prelaunch = _load_json(prelaunch_path)
    completed = _load_json(completed_path)
    if (
        prelaunch.get("manifest_sha256") != manifest_sha256
        or completed.get("manifest_sha256") != manifest_sha256
    ):
        raise AnalysisError("manifest digest differs across sealed ledgers")
    preflight = _validate_prelaunch(
        manifest,
        prelaunch,
        expected_schema=RECOVERY_PRELAUNCH_SCHEMA_VERSION,
    )
    contract = manifest["artifact_contract"]
    if (
        prelaunch.get("resolved_system_prompt_sha256")
        != contract["system_prompt_sha256"]
        or preflight["tokenizer_sha256"] != contract["tokenizer_sha256"]
        or preflight["render_contract_sha256"] != contract["render_contract_sha256"]
        or preflight["chat_template_sha256"] != contract["chat_template_sha256"]
        or preflight["game_revision"] != contract["game_revision"]
        or preflight["game_bundle_sha256"] != contract["game_bundle_sha256"]
        or preflight["checkpoint_sha256"]
        != {
            snapshot: model["checkpoint_sha256"]
            for snapshot, model in manifest["models"].items()
        }
    ):
        raise AnalysisError("prelaunch artifacts differ from the registration")
    if (
        completed.get("schema_version") != RECOVERY_INVENTORY_SCHEMA_VERSION
        or completed.get("pilot_id") != manifest["pilot_id"]
        or completed.get("claim_boundary") != manifest["claim_boundary"]
    ):
        raise AnalysisError("completed factorial ledger identity is invalid")
    completed_cells = completed.get("cells")
    if not isinstance(completed_cells, list) or len(completed_cells) != 18:
        raise AnalysisError("completed inventory does not contain 18 cells")
    completed_by_id = {
        cell.get("cell_id"): cell for cell in completed_cells
        if isinstance(cell, dict)
    }
    expected_ids = {cell["cell_id"] for cell in manifest["cells"]}
    if len(completed_by_id) != 18 or set(completed_by_id) != expected_ids:
        raise AnalysisError("completed cell IDs differ from registration")
    valid_receipts = sum(
        cell.get("status") == "valid" for cell in completed_cells
    )
    invalid_receipts = len(completed_cells) - valid_receipts
    if (
        completed.get("valid_cells") != valid_receipts
        or completed.get("invalid_cells") != invalid_receipts
    ):
        raise AnalysisError("completed valid/invalid counts differ from cell receipts")

    rows = []
    invalid_cells = []
    files_checked = 0
    protocol = manifest["protocol"]
    for cell in manifest["cells"]:
        cell_id = cell["cell_id"]
        cell_root = root / cell_id
        retained = completed_by_id[cell_id]
        recovery = cell["recovery"]
        if (
            retained.get("snapshot") != cell["snapshot"]
            or retained.get("schedule_index") != cell["schedule_index"]
            or retained.get("recovery_assignment") is not recovery
        ):
            raise AnalysisError(f"{cell_id}: cell receipt identity mismatch")
        inventory_sha = retained.get("artifact_inventory_sha256")
        if isinstance(inventory_sha, str) and inventory_sha:
            files_checked += _verify_artifacts(cell_root, inventory_sha)
        if retained.get("status") != "valid":
            invalid_cells.append({
                "cell_id": cell_id,
                "replicate": cell["replicate"],
                "weight": WEIGHT_LABEL[cell["snapshot"]],
                "recovery": recovery,
                "schedule_index": cell["schedule_index"],
                "returncode": retained.get("returncode"),
                "error": retained.get("error"),
                "artifacts_sealed": bool(inventory_sha),
            })
            continue
        if (
            retained.get("returncode") != 0
            or retained.get("tool_recovery_enabled") is not recovery
            or not isinstance(inventory_sha, str)
            or not inventory_sha
        ):
            raise AnalysisError(f"{cell_id}: valid cell receipt is inconsistent")
        endpoint, endpoint_sha = _validate_cell_attestation(
            cell_root,
            cell["snapshot"],
            manifest["models"][cell["snapshot"]],
            preflight,
        )
        results_root = cell_root / "eval" / cell_id
        results = _load_json(results_root / "results.json")
        meta = results.get("meta")
        episodes = results.get("episodes")
        if not isinstance(meta, dict) or not isinstance(episodes, list) or len(episodes) != 1:
            raise AnalysisError(f"{cell_id}: malformed result shape")
        expected_meta = {
            "model": cell_id,
            "scenario": protocol["scenario"],
            "duration_seconds_budget": protocol["duration_seconds"],
            "include_game_knowledge": protocol["include_game_knowledge"],
            "tool_schema_source": protocol["tool_schema_source"],
            "prompt_agent_name": protocol["prompt_agent_name"],
            "protocol_id": manifest["pilot_id"],
            "experiment_manifest_sha256": manifest_sha256,
            "git_sha": preflight["source_git_commit"],
            "inference_seed": cell["inference_seed"],
            "endpoint_attestation_sha256": endpoint_sha,
            "checkpoint_sha256": endpoint["checkpoint_sha256"],
            "tokenizer_sha256": preflight["tokenizer_sha256"],
            "render_contract_sha256": preflight["render_contract_sha256"],
            "factorial_schedule_algorithm": protocol["schedule_algorithm"],
            "factorial_schedule_seed": protocol["schedule_seed"],
            "factorial_schedule_index": cell["schedule_index"],
            "factorial_batch_index": cell["replicate"] - 1,
            "factorial_cluster_id": f"pilot-rep{cell['replicate']:02d}",
            "factorial_pair_id": (
                f"pilot-rep{cell['replicate']:02d}-"
                f"{WEIGHT_LABEL[cell['snapshot']]}"
            ),
            "environment_seed_mechanism": protocol["environment_seed_mechanism"],
            "environment_seed": cell["environment_seed"],
            "environment_rng_algorithm": protocol["environment_rng_algorithm"],
            "environment_game_revision": preflight["game_revision"],
            "environment_game_bundle_sha256": preflight["game_bundle_sha256"],
            "environment_seed_reason": protocol["environment_seed_reason"],
            "tool_recovery_enabled": recovery,
        }
        mismatches = {
            key: {"expected": value, "actual": meta.get(key)}
            for key, value in expected_meta.items() if meta.get(key) != value
        }
        if mismatches:
            raise AnalysisError(f"{cell_id}: result provenance mismatch {mismatches}")
        rng = meta.get("environment_rng_attestation")
        expected_rng = {
            "schema": protocol["environment_seed_mechanism"],
            "algorithm": protocol["environment_rng_algorithm"],
            "gameRevision": preflight["game_revision"],
            "serverBundleSha256": preflight["game_bundle_sha256"],
            "drawsAtAttestation": 0,
            "seedSha256": hashlib.sha256(
                str(cell["environment_seed"]).encode()
            ).hexdigest(),
        }
        if not isinstance(rng, dict) or any(
            rng.get(key) != value for key, value in expected_rng.items()
        ):
            raise AnalysisError(f"{cell_id}: environment RNG attestation mismatch")
        _validate_recovery_receipts(results_root, results, recovery, cell_id)
        if (
            _file_sha256(results_root / "system_prompt.md")
            != contract["system_prompt_sha256"]
        ):
            raise AnalysisError(f"{cell_id}: resolved system prompt drifted")

        episode = episodes[0]
        if episode.get("status") != "ok" or episode.get("returncode") != 0:
            raise AnalysisError(f"{cell_id}: episode terminal status is invalid")
        state = _load_json(results_root / "episode_001_state.json")
        if not _canonical_start_ok(state):
            raise AnalysisError(f"{cell_id}: canonical first observation mismatch")
        player_before, player_after, qa_before, qa_after = _validate_state_boundaries(
            state, cell_id
        )
        raw_dir = results_root / "episode_001_raw"
        session_logs = _ordered_session_logs(raw_dir)
        try:
            validate_eval_session_terminals(session_logs)
        except RuntimeError as exc:
            raise AnalysisError(f"{cell_id}: invalid terminal chain: {exc}") from exc
        if len(session_logs) != episode.get("sub_sessions"):
            raise AnalysisError(f"{cell_id}: raw session count mismatch")
        entries = []
        for session_log in session_logs:
            entries.extend(parse_log(session_log))
        recomputed = compute_episode_metrics(
            entries, player_before, player_after, qa_before, qa_after
        )
        metric_mismatches = {
            key: {"expected": value, "actual": episode.get(key)}
            for key, value in recomputed.items() if episode.get(key) != value
        }
        if metric_mismatches:
            raise AnalysisError(
                f"{cell_id}: derived metrics mismatch {metric_mismatches}"
            )
        raw_metrics = _validate_raw_emissions(session_logs)
        recovery_metrics = _validate_recovery_accounting(
            session_logs,
            raw_metrics,
            recomputed["action_counts"],
            recovery,
        )
        duration = float(episode["duration_seconds"])
        if duration < protocol["duration_seconds"]:
            raise AnalysisError(f"{cell_id}: episode ended before fixed budget")
        tool_bearing_turns = int(episode["tool_calls_valid"])
        executed_calls = sum(recomputed["action_counts"].values())
        rows.append({
            "cell_id": cell_id,
            "replicate": cell["replicate"],
            "weight": WEIGHT_LABEL[cell["snapshot"]],
            "recovery": recovery,
            "schedule_index": cell["schedule_index"],
            "duration_seconds": duration,
            "budget_overrun_seconds": round(
                duration - protocol["duration_seconds"], 3
            ),
            "turns": int(episode["turns_played"]),
            "canonical_executed_calls": executed_calls,
            "canonical_executed_calls_per_minute": round(
                executed_calls / (duration / 60), 6
            ),
            "canonical_tool_bearing_turns": tool_bearing_turns,
            "tool_parse_rate": float(episode["tool_parse_rate"]),
            "api_errors": _api_error_count(cell_root),
            "sub_sessions": len(session_logs),
            "core3_stages_advanced": int(episode["core3_stages_advanced"]),
            "quest_stages_advanced": int(episode["quest_stages_advanced"]),
            "xp_db_delta": int(episode["xp_db_delta"]),
            "unique_positions": int(episode["unique_positions"]),
            "canonical_action_counts": recomputed["action_counts"],
            "raw_generations": raw_metrics["raw_generations"],
            "generations_with_structured_call": raw_metrics[
                "generations_with_structured_call"
            ],
            "generations_without_structured_call": raw_metrics[
                "generations_without_structured_call"
            ],
            "structured_call_emission_rate": round(
                raw_metrics["generations_with_structured_call"]
                / raw_metrics["raw_generations"],
                6,
            ),
            "raw_structured_calls": raw_metrics["emitted_structured_calls"],
            "raw_structured_calls_per_minute": round(
                raw_metrics["emitted_structured_calls"] / (duration / 60), 6
            ),
            "raw_action_counts": raw_metrics["raw_action_counts"],
            **recovery_metrics,
        })

    index_record = {
        "prelaunch_sha256": _file_sha256(prelaunch_path),
        "completed_inventory_sha256": _file_sha256(completed_path),
        "cell_artifact_inventory_sha256": {
            cell_id: completed_by_id[cell_id]["artifact_inventory_sha256"]
            for cell_id in sorted(completed_by_id)
        },
    }
    return {
        "schema_version": "kaetram.local-weight-recovery-factorial-analysis.v1",
        "pilot_id": manifest["pilot_id"],
        "claim_boundary": manifest["claim_boundary"],
        "manifest_sha256": manifest_sha256,
        "bundle_index": index_record,
        "bundle_index_sha256": sha256_json(index_record),
        "valid_cells": len(rows),
        "invalid_cells": len(invalid_cells),
        "invalid_cell_receipts": invalid_cells,
        "files_rehashed": files_checked,
        "rows": rows,
        "by_arm": _summarize(rows),
        "paired_differences": _pair_differences(rows),
        "overall": {
            "raw_generations": sum(row["raw_generations"] for row in rows),
            "raw_structured_calls": sum(row["raw_structured_calls"] for row in rows),
            "generations_with_structured_call": sum(
                row["generations_with_structured_call"] for row in rows
            ),
            "generations_without_structured_call": sum(
                row["generations_without_structured_call"] for row in rows
            ),
            "structured_call_emission_rate": round(
                sum(row["generations_with_structured_call"] for row in rows)
                / sum(row["raw_generations"] for row in rows),
                6,
            ) if rows else None,
            "canonical_executed_calls": sum(
                row["canonical_executed_calls"] for row in rows
            ),
            "malformed_emissions": sum(
                row["malformed_emissions"] for row in rows
            ),
            "recovered_calls": sum(row["recovered_calls"] for row in rows),
            "recovered_execution_errors": sum(
                row["recovered_execution_errors"] for row in rows
            ),
            "recovered_execution_successes": sum(
                row["recovered_execution_successes"] for row in rows
            ),
            "api_errors": sum(row["api_errors"] for row in rows),
            "zero_turn_cells": sum(row["turns"] == 0 for row in rows),
            "cells_with_core3_progress": sum(
                row["core3_stages_advanced"] > 0 for row in rows
            ),
            "cells_with_any_quest_progress": sum(
                row["quest_stages_advanced"] > 0 for row in rows
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPO / "research/experiments/local-weight-recovery-30m.json",
    )
    parser.add_argument(
        "--expected-bundle-index-sha256",
        help="Fail if the sealed-ledger root differs from this digest.",
    )
    args = parser.parse_args(argv)
    try:
        report = analyze(args.root.resolve(), args.manifest.resolve())
        expected = args.expected_bundle_index_sha256
        if expected is not None and report["bundle_index_sha256"] != expected:
            raise AnalysisError("bundle-index digest differs from expected root")
    except (AnalysisError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
