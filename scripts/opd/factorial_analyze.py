#!/usr/bin/env python3
"""Validate and analyze a completed OPD weights x recovery factorial.

The independent unit is a DB-reset replicate.  The three personality lanes are
summed within each replicate x arm before any contrast is computed; they are
never reported as independent samples.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import random
import statistics
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.opd.factorial_eval import (  # noqa: E402
    Cell,
    ExperimentPlan,
    ManifestError,
    build_plan,
    validate_cell_result,
)


WEIGHT_CONTRASTS = (("r2", "base"), ("r3", "base"), ("r3", "r2"))
DEFAULT_BOOTSTRAP_SAMPLES = 20_000
DEFAULT_BOOTSTRAP_SEED = 20_260_718


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _numeric(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ManifestError(f"{label} must be a finite numeric value")
    return float(value)


def load_cell_metric(plan: ExperimentPlan, cell: Cell, metric: str) -> dict[str, Any]:
    validate_cell_result(plan, cell)
    path = Path(cell.run_dir) / cell.cell_id / "results.json"
    results = json.loads(path.read_text())
    meta = results["meta"]
    episode = results["episodes"][0]
    if meta.get("endpoint") != f"env:{cell.endpoint_env}":
        raise ManifestError(
            f"cell {cell.cell_id} endpoint reference mismatch: {meta.get('endpoint')!r}"
        )
    git_sha = meta.get("git_sha")
    if not isinstance(git_sha, str) or not git_sha.strip():
        raise ManifestError(f"cell {cell.cell_id} has no source git SHA")
    if episode.get("returncode") != 0:
        raise ManifestError(f"cell {cell.cell_id} episode returncode is not zero")
    turns = episode.get("turns_played")
    if isinstance(turns, bool) or not isinstance(turns, int) or turns < 1:
        raise ManifestError(f"cell {cell.cell_id} has no completed agent turns")
    value = _numeric(episode.get(metric), label=f"cell {cell.cell_id} metric {metric}")
    return {
        "cell_id": cell.cell_id,
        "pair_id": cell.pair_id,
        "cluster_id": cell.cluster_id,
        "replicate": cell.replicate,
        "weight": cell.weight,
        "recovery": cell.recovery,
        "personality": cell.personality,
        "metric": metric,
        "value": value,
        "turns_played": turns,
        "git_sha": git_sha,
        "results_path": str(path),
        "results_sha256": _sha256(path),
    }


def cluster_rows(cell_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, str, bool], list[dict[str, Any]]] = {}
    for row in cell_rows:
        grouped.setdefault((row["replicate"], row["weight"], row["recovery"]), []).append(row)
    clusters = []
    for (replicate, weight, recovery), rows in sorted(grouped.items()):
        personalities = {row["personality"] for row in rows}
        if personalities != {"grinder", "completionist", "explorer_tinkerer"} or len(rows) != 3:
            raise ManifestError(
                f"replicate {replicate} {weight} recovery={recovery} lacks three personality lanes"
            )
        clusters.append({
            "replicate": replicate,
            "weight": weight,
            "recovery": recovery,
            "cluster_value": sum(row["value"] for row in rows),
            "personality_values": {
                row["personality"]: row["value"] for row in sorted(rows, key=lambda item: item["personality"])
            },
            "turns_played": sum(row["turns_played"] for row in rows),
            "cell_ids": sorted(row["cell_id"] for row in rows),
        })
    return clusters


def _bootstrap_ci(
    values: list[float], *, samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> list[float] | None:
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    means = sorted(
        statistics.fmean(rng.choice(values) for _ in values)
        for _ in range(samples)
    )
    lo = means[int(0.025 * (samples - 1))]
    hi = means[int(0.975 * (samples - 1))]
    return [lo, hi]


def _sign_flip_p(values: list[float]) -> float | None:
    nonzero = [value for value in values if value != 0]
    if len(values) < 5 or not nonzero:
        return None
    observed = abs(sum(nonzero))
    extreme = 0
    total = 2 ** len(nonzero)
    for signs in itertools.product((-1, 1), repeat=len(nonzero)):
        statistic = abs(sum(sign * value for sign, value in zip(signs, nonzero, strict=True)))
        if statistic >= observed - 1e-12:
            extreme += 1
    return extreme / total


def summarize_effect(
    *, name: str, deltas: list[float], comparisons: int,
) -> dict[str, Any]:
    p_value = _sign_flip_p(deltas)
    return {
        "name": name,
        "n_replicates": len(deltas),
        "paired_deltas": deltas,
        "mean_delta": statistics.fmean(deltas),
        "median_delta": statistics.median(deltas),
        "min_delta": min(deltas),
        "max_delta": max(deltas),
        "bootstrap_95pct_ci_mean": _bootstrap_ci(deltas),
        "exact_two_sided_sign_flip_p": p_value,
        "bonferroni_comparisons": comparisons,
        "bonferroni_adjusted_p": min(1.0, p_value * comparisons) if p_value is not None else None,
        "inference_status": "confirmatory_eligible" if len(deltas) >= 5 else "pilot_only",
    }


def build_analysis(plan: ExperimentPlan, metric: str) -> dict[str, Any]:
    rows = [load_cell_metric(plan, cell, metric) for cell in plan.cells]
    git_shas = sorted({row["git_sha"] for row in rows})
    if len(git_shas) != 1:
        raise ManifestError(f"factorial cells span multiple source commits: {git_shas}")
    clusters = cluster_rows(rows)
    by_arm = {
        (row["replicate"], row["weight"], row["recovery"]): row["cluster_value"]
        for row in clusters
    }
    replicates = sorted({row["replicate"] for row in clusters})
    effects: list[dict[str, Any]] = []
    comparisons = 3 + (2 * len(WEIGHT_CONTRASTS))
    for weight in ("base", "r2", "r3"):
        deltas = [
            by_arm[(replicate, weight, True)] - by_arm[(replicate, weight, False)]
            for replicate in replicates
        ]
        effects.append(summarize_effect(
            name=f"recovery_on_minus_off/{weight}", deltas=deltas, comparisons=comparisons,
        ))
    for recovery in (False, True):
        for treatment, control in WEIGHT_CONTRASTS:
            deltas = [
                by_arm[(replicate, treatment, recovery)] - by_arm[(replicate, control, recovery)]
                for replicate in replicates
            ]
            effects.append(summarize_effect(
                name=f"{treatment}_minus_{control}/recovery_{'on' if recovery else 'off'}",
                deltas=deltas,
                comparisons=comparisons,
            ))
    return {
        "schema_version": "kaetram-opd-factorial-analysis-v1",
        "experiment_id": plan.experiment_id,
        "manifest": plan.manifest,
        "metric": metric,
        "independent_unit": "DB-reset replicate",
        "personality_handling": "summed within replicate x weight x recovery arm",
        "n_replicates": len(replicates),
        "n_cells": len(rows),
        "n_cluster_arms": len(clusters),
        "source_git_sha": git_shas[0],
        "multiple_comparisons": {
            "family": "three recovery contrasts plus six weight contrasts",
            "count": comparisons,
            "adjustment": "Bonferroni",
        },
        "bootstrap": {
            "method": "percentile bootstrap of paired mean delta",
            "samples": DEFAULT_BOOTSTRAP_SAMPLES,
            "seed": DEFAULT_BOOTSTRAP_SEED,
        },
        "effects": effects,
        "clusters": clusters,
        "cells": rows,
    }


def _write_new(path: Path, content: str) -> None:
    if path.exists():
        raise ManifestError(f"refusing to overwrite analysis artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def write_cluster_csv(path: Path, analysis: dict[str, Any]) -> None:
    if path.exists():
        raise ManifestError(f"refusing to overwrite analysis artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("replicate", "weight", "recovery", "cluster_value", "turns_played", "cell_ids"),
        )
        writer.writeheader()
        for row in analysis["clusters"]:
            writer.writerow({
                **{key: row[key] for key in ("replicate", "weight", "recovery", "cluster_value", "turns_played")},
                "cell_ids": ";".join(row["cell_ids"]),
            })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--metric", required=True, help="numeric episode metric to aggregate")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--clusters-csv", type=Path, required=True)
    args = parser.parse_args()
    try:
        existing = [str(path) for path in (args.out, args.clusters_csv) if path.exists()]
        if existing:
            raise ManifestError(
                "refusing to overwrite analysis artifact(s): " + ", ".join(existing)
            )
        analysis = build_analysis(build_plan(args.manifest), args.metric)
        _write_new(args.out, json.dumps(analysis, indent=2, sort_keys=True) + "\n")
        write_cluster_csv(args.clusters_csv, analysis)
    except ManifestError as exc:
        parser.error(str(exc))
    print(args.out)
    print(args.clusters_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
