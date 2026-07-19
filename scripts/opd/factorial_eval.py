#!/usr/bin/env python3
"""Manifest-driven, fail-closed launcher for the 2B weights x recovery eval.

Default behavior is preflight only. Launching requires all three of:
  1. execution.allow_launch=true in the manifest,
  2. --execute, and
  3. --confirm-launch matching experiment_id exactly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from heldout_guard import HeldOutGuardError, validate_eval_selection  # noqa: E402
from inference_seed import validate_inference_seed  # noqa: E402


REQUIRED_WEIGHTS = ("base", "r2", "r3")
REQUIRED_RECOVERY = (False, True)
REQUIRED_PERSONALITIES = ("grinder", "completionist", "explorer_tinkerer")
PERSONALITY_CODES = {"grinder": "g", "completionist": "c", "explorer_tinkerer": "e"}
SCHEDULE_ALGORITHM = "sha256-rank-v1"
CLUSTER_SIZE = len(REQUIRED_PERSONALITIES) * len(REQUIRED_RECOVERY)


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class Cell:
    cell_id: str
    pair_id: str
    cluster_id: str
    replicate: int
    weight: str
    recovery: bool
    personality: str
    endpoint_env: str
    api_model: str
    username: str
    server_port: int
    sandbox: str
    run_dir: str
    schedule_index: int
    batch_index: int
    inference_seed: int


@dataclass(frozen=True)
class ExperimentPlan:
    experiment_id: str
    manifest: str
    project_dir: str
    episodes: int
    scenario: str
    personalities: tuple[str, ...]
    tool_schema_source: str
    omit_game_knowledge: bool
    held_out_quest: str
    held_out_registration: str
    allow_launch: bool
    max_parallel: int
    schedule_algorithm: str
    schedule_seed: int
    inference_seeds: tuple[int, ...]
    environment_seed_mechanism: str
    environment_seed: int | None
    environment_seed_reason: str
    cells: tuple[Cell, ...]


def _require(mapping: dict[str, Any], key: str, kind: type, context: str) -> Any:
    value = mapping.get(key)
    if not isinstance(value, kind):
        raise ManifestError(f"{context}.{key} must be {kind.__name__}")
    return value


def load_manifest(path: str | Path) -> tuple[dict[str, Any], Path]:
    manifest_path = Path(path).resolve()
    try:
        raw = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot load manifest {manifest_path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 2:
        raise ManifestError("manifest schema_version must be 2")
    return raw, manifest_path


def build_plan(path: str | Path, *, environ: dict[str, str] | None = None) -> ExperimentPlan:
    raw, manifest_path = load_manifest(path)
    experiment_id = _require(raw, "experiment_id", str, "manifest").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", experiment_id):
        raise ManifestError("experiment_id must be 3-64 lowercase letters, digits, '_' or '-'")

    design = _require(raw, "design", dict, "manifest")
    weights = _require(design, "weights", list, "design")
    recovery = _require(design, "recovery", list, "design")
    replicates = design.get("replicates")
    if tuple(weights) != REQUIRED_WEIGHTS:
        raise ManifestError(f"design.weights must be exactly {list(REQUIRED_WEIGHTS)}")
    if len(recovery) != 2 or set(recovery) != set(REQUIRED_RECOVERY):
        raise ManifestError("design.recovery must contain exactly false and true")
    if isinstance(replicates, bool) or not isinstance(replicates, int) or replicates < 1:
        raise ManifestError("design.replicates must be a positive integer")

    randomization = _require(raw, "randomization", dict, "manifest")
    schedule_algorithm = _require(
        randomization, "schedule_algorithm", str, "randomization"
    )
    if schedule_algorithm != SCHEDULE_ALGORITHM:
        raise ManifestError(
            f"randomization.schedule_algorithm must be '{SCHEDULE_ALGORITHM}'"
        )
    try:
        schedule_seed = validate_inference_seed(
            randomization.get("schedule_seed"), label="randomization.schedule_seed"
        )
    except ValueError as exc:
        raise ManifestError(str(exc)) from exc
    inference_seeds_raw = _require(
        randomization, "inference_seeds", list, "randomization"
    )
    if len(inference_seeds_raw) != replicates:
        raise ManifestError(
            "randomization.inference_seeds must contain exactly one seed per replicate"
        )
    try:
        inference_seeds = tuple(
            validate_inference_seed(seed, label=f"randomization.inference_seeds[{index}]")
            for index, seed in enumerate(inference_seeds_raw)
        )
    except ValueError as exc:
        raise ManifestError(str(exc)) from exc
    if len(set(inference_seeds)) != len(inference_seeds):
        raise ManifestError("randomization.inference_seeds must be unique")
    environment_seed_cfg = _require(
        randomization, "environment_seed", dict, "randomization"
    )
    environment_seed_mechanism = _require(
        environment_seed_cfg, "mechanism", str, "randomization.environment_seed"
    )
    environment_seed = environment_seed_cfg.get("seed")
    environment_seed_reason = _require(
        environment_seed_cfg, "reason", str, "randomization.environment_seed"
    ).strip()
    if environment_seed_mechanism != "unavailable":
        raise ManifestError(
            "randomization.environment_seed.mechanism must be 'unavailable' until Kaetram "
            "implements and verifies an environment RNG seed interface"
        )
    if environment_seed is not None:
        raise ManifestError(
            "randomization.environment_seed.seed must be null when mechanism is unavailable"
        )
    if not environment_seed_reason:
        raise ManifestError("randomization.environment_seed.reason must explain the limitation")

    models = _require(raw, "models", dict, "manifest")
    if set(models) != set(REQUIRED_WEIGHTS):
        raise ManifestError(f"models must define exactly {list(REQUIRED_WEIGHTS)}")
    endpoint_envs: set[str] = set()
    for weight in REQUIRED_WEIGHTS:
        cfg = _require(models, weight, dict, "models")
        endpoint_env = _require(cfg, "endpoint_env", str, f"models.{weight}")
        _require(cfg, "api_model", str, f"models.{weight}")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]+", endpoint_env):
            raise ManifestError(f"models.{weight}.endpoint_env is not a valid environment variable")
        if endpoint_env in endpoint_envs:
            raise ManifestError("each weight must use a distinct endpoint_env")
        endpoint_envs.add(endpoint_env)

    evaluation = _require(raw, "evaluation", dict, "manifest")
    episodes = evaluation.get("episodes")
    if episodes != 1:
        raise ManifestError(
            "confirmatory factorial requires evaluation.episodes=1; use design.replicates "
            "for independent DB-reset repetitions"
        )
    scenario = _require(evaluation, "scenario", str, "evaluation")
    if scenario not in {"A", "B", "C", "D"}:
        raise ManifestError("evaluation.scenario must be A, B, C, or D")
    personalities = evaluation.get("personalities")
    if not isinstance(personalities, list) or tuple(personalities) != REQUIRED_PERSONALITIES:
        raise ManifestError(
            f"evaluation.personalities must be exactly {list(REQUIRED_PERSONALITIES)}"
        )
    omit_game_knowledge = evaluation.get("omit_game_knowledge")
    if not isinstance(omit_game_knowledge, bool):
        raise ManifestError("evaluation.omit_game_knowledge must be boolean")
    tool_schema_source = _require(evaluation, "tool_schema_source", str, "evaluation")
    if tool_schema_source != "canonical":
        raise ManifestError(
            "confirmatory factorial requires evaluation.tool_schema_source='canonical'"
        )
    held_out_value = evaluation.get("held_out_quest", "")
    if not isinstance(held_out_value, str):
        raise ManifestError("evaluation.held_out_quest must be a string when provided")
    held_out_quest = held_out_value
    registration_raw = str(evaluation.get("held_out_registration") or "")
    if held_out_quest and not omit_game_knowledge:
        raise ManifestError("a held-out quest requires evaluation.omit_game_knowledge=true")
    registration = None
    if held_out_quest:
        if not registration_raw:
            raise ManifestError("a held-out quest requires evaluation.held_out_registration")
        registration_path = (
            (REPO / registration_raw).resolve()
            if not Path(registration_raw).is_absolute()
            else Path(registration_raw).resolve()
        )
        try:
            registration = validate_eval_selection(held_out_quest, registration_path)
        except HeldOutGuardError as exc:
            raise ManifestError(str(exc)) from exc
    elif registration_raw:
        raise ManifestError("held_out_registration must be empty when held_out_quest is empty")

    isolation = _require(raw, "isolation", dict, "manifest")
    username_prefix = _require(isolation, "username_prefix", str, "isolation")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", username_prefix):
        raise ManifestError("isolation.username_prefix must be alphanumeric and start with a letter")
    port_start = isolation.get("server_port_start")
    if isinstance(port_start, bool) or not isinstance(port_start, int) or not 1024 <= port_start <= 65529:
        raise ManifestError("isolation.server_port_start must be between 1024 and 65529")
    sandbox_root = Path(_require(isolation, "sandbox_root", str, "isolation"))
    if not sandbox_root.is_absolute() or sandbox_root == Path("/"):
        raise ManifestError("isolation.sandbox_root must be a specific absolute path")
    output_raw = Path(_require(isolation, "output_root", str, "isolation"))
    output_root = output_raw.resolve() if output_raw.is_absolute() else (REPO / output_raw).resolve()

    execution = _require(raw, "execution", dict, "manifest")
    allow_launch = execution.get("allow_launch")
    if not isinstance(allow_launch, bool):
        raise ManifestError("execution.allow_launch must be boolean")
    max_parallel = execution.get("max_parallel")
    if max_parallel != CLUSTER_SIZE:
        raise ManifestError(
            f"execution.max_parallel must be {CLUSTER_SIZE} so each batch is one analysis cluster"
        )

    cells: list[Cell] = []
    for replicate in range(1, replicates + 1):
        for weight in REQUIRED_WEIGHTS:
            cfg = models[weight]
            cluster_id = f"rep{replicate:02d}-{weight}"
            for personality in REQUIRED_PERSONALITIES:
                pair_id = f"{cluster_id}-{personality}"
                for recovery_enabled in REQUIRED_RECOVERY:
                    rec_label = "on" if recovery_enabled else "off"
                    cell_id = f"{pair_id}-recovery-{rec_label}"
                    username = (
                        f"{username_prefix}r{replicate:02d}{weight}"
                        f"{PERSONALITY_CODES[personality]}{int(recovery_enabled)}"
                    )
                    if len(username) > 24:
                        raise ManifestError(f"generated username exceeds 24 characters: {username}")
                    cell_index = len(cells)
                    cells.append(Cell(
                        cell_id=cell_id,
                        pair_id=pair_id,
                        cluster_id=cluster_id,
                        replicate=replicate,
                        weight=weight,
                        recovery=recovery_enabled,
                        personality=personality,
                        endpoint_env=cfg["endpoint_env"],
                        api_model=cfg["api_model"],
                        username=username,
                        server_port=port_start + cell_index,
                        sandbox=str((sandbox_root / experiment_id / cell_id).resolve()),
                        run_dir=str((output_root / experiment_id / cell_id).resolve()),
                        schedule_index=-1,
                        batch_index=-1,
                        inference_seed=inference_seeds[replicate - 1],
                    ))

    if cells[-1].server_port > 65535:
        raise ManifestError("generated server ports exceed 65535; lower server_port_start")
    if max_parallel > len(cells):
        raise ManifestError("execution.max_parallel cannot exceed the generated cell count")

    def rank(label: str) -> bytes:
        return hashlib.sha256(f"{schedule_seed}:{label}".encode()).digest()

    scheduled_cells: list[Cell] = []
    cluster_ids = sorted(
        {cell.cluster_id for cell in cells},
        key=lambda value: (rank(f"cluster:{value}"), value),
    )
    for batch_index, cluster_id in enumerate(cluster_ids):
        cluster = [cell for cell in cells if cell.cluster_id == cluster_id]
        pair_ids = sorted(
            {cell.pair_id for cell in cluster},
            key=lambda value: (rank(f"pair:{value}"), value),
        )
        for pair_id in pair_ids:
            pair = sorted(
                (cell for cell in cluster if cell.pair_id == pair_id),
                key=lambda cell: (rank(f"cell:{cell.cell_id}"), cell.cell_id),
            )
            for cell in pair:
                scheduled_cells.append(replace(
                    cell,
                    schedule_index=len(scheduled_cells),
                    batch_index=batch_index,
                ))

    plan = ExperimentPlan(
        experiment_id=experiment_id,
        manifest=str(manifest_path),
        project_dir=str(REPO),
        episodes=episodes,
        scenario=scenario,
        personalities=REQUIRED_PERSONALITIES,
        tool_schema_source=tool_schema_source,
        omit_game_knowledge=omit_game_knowledge,
        held_out_quest=registration.quest_name if registration else "",
        held_out_registration=str(registration.path) if registration else "",
        allow_launch=allow_launch,
        max_parallel=max_parallel,
        schedule_algorithm=schedule_algorithm,
        schedule_seed=schedule_seed,
        inference_seeds=inference_seeds,
        environment_seed_mechanism=environment_seed_mechanism,
        environment_seed=environment_seed,
        environment_seed_reason=environment_seed_reason,
        cells=tuple(scheduled_cells),
    )
    validate_factorial_plan(plan)
    return plan


def validate_factorial_plan(plan: ExperimentPlan) -> None:
    expected = {
        (replicate, weight, recovery, personality)
        for replicate in range(1, max(cell.replicate for cell in plan.cells) + 1)
        for weight in REQUIRED_WEIGHTS
        for recovery in REQUIRED_RECOVERY
        for personality in REQUIRED_PERSONALITIES
    }
    actual = {(c.replicate, c.weight, c.recovery, c.personality) for c in plan.cells}
    if actual != expected or len(plan.cells) != len(expected):
        raise ManifestError("plan is not a complete, duplicate-free weights x recovery factorial")

    for pair_id in {c.pair_id for c in plan.cells}:
        pair = [c for c in plan.cells if c.pair_id == pair_id]
        if {c.recovery for c in pair} != set(REQUIRED_RECOVERY) or len(pair) != 2:
            raise ManifestError(f"pair {pair_id} must contain recovery off and on exactly once")

    for cluster_id in {c.cluster_id for c in plan.cells}:
        cluster = [c for c in plan.cells if c.cluster_id == cluster_id]
        cluster_cells = {(c.personality, c.recovery) for c in cluster}
        expected_cluster = {
            (personality, recovery)
            for personality in REQUIRED_PERSONALITIES
            for recovery in REQUIRED_RECOVERY
        }
        if cluster_cells != expected_cluster or len(cluster) != len(expected_cluster):
            raise ManifestError(
                f"cluster {cluster_id} must contain all three personality lanes x recovery off/on"
            )
        indices = sorted(c.schedule_index for c in cluster)
        if indices != list(range(indices[0], indices[0] + CLUSTER_SIZE)):
            raise ManifestError(f"cluster {cluster_id} must be one contiguous launch batch")
        if len({c.batch_index for c in cluster}) != 1:
            raise ManifestError(f"cluster {cluster_id} must use one batch_index")

    for pair_id in {c.pair_id for c in plan.cells}:
        pair = sorted(c.schedule_index for c in plan.cells if c.pair_id == pair_id)
        if pair[1] != pair[0] + 1:
            raise ManifestError(f"pair {pair_id} must remain adjacent in the randomized schedule")

    if [c.schedule_index for c in plan.cells] != list(range(len(plan.cells))):
        raise ManifestError("schedule_index must be complete, ordered, and duplicate-free")
    if any(c.inference_seed != plan.inference_seeds[c.replicate - 1] for c in plan.cells):
        raise ManifestError("all cells in a replicate must share its registered inference seed")

    for attr in ("username", "server_port", "sandbox", "run_dir", "cell_id"):
        values = [getattr(c, attr) for c in plan.cells]
        if len(values) != len(set(values)):
            raise ManifestError(f"all cells must have isolated, unique {attr} values")


def cell_command(plan: ExperimentPlan, cell: Cell) -> list[str]:
    cmd = [
        sys.executable,
        str(REPO / "eval_harness.py"),
        "--models-env", f"{cell.cell_id}={cell.endpoint_env}",
        "--model-api-name", cell.api_model,
        "--episodes", str(plan.episodes),
        "--scenario", plan.scenario,
        "--output-dir", cell.run_dir,
        "--project-dir", plan.project_dir,
        "--username", cell.username,
        "--server-port", str(cell.server_port),
        "--sandbox", cell.sandbox,
        "--inference-seed", str(cell.inference_seed),
        "--factorial-schedule-algorithm", plan.schedule_algorithm,
        "--factorial-schedule-seed", str(plan.schedule_seed),
        "--factorial-schedule-index", str(cell.schedule_index),
        "--factorial-batch-index", str(cell.batch_index),
        "--factorial-cluster-id", cell.cluster_id,
        "--factorial-pair-id", cell.pair_id,
        "--environment-seed-mechanism", plan.environment_seed_mechanism,
        "--environment-seed-reason", plan.environment_seed_reason,
    ]
    cmd.extend(["--personality", cell.personality])
    if plan.omit_game_knowledge:
        cmd.append("--omit-game-knowledge")
    if plan.held_out_quest:
        cmd.extend([
            "--held-out-quest", plan.held_out_quest,
            "--held-out-registration", plan.held_out_registration,
        ])
    return cmd


def plan_dict(plan: ExperimentPlan) -> dict[str, Any]:
    payload = asdict(plan)
    payload["mode"] = "preflight_only"
    payload["factorial_validation"] = "passed"
    payload["launchability"] = "blocked_environment_rng_unavailable"
    payload["commands"] = [
        cell_command(plan, cell)
        for cell in plan.cells
    ]
    return payload


def validate_cell_result(plan: ExperimentPlan, cell: Cell) -> None:
    """Require one complete, correctly attributed artifact for a launched cell."""
    path = Path(cell.run_dir) / cell.cell_id / "results.json"
    try:
        results = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cell {cell.cell_id} has no valid results artifact: {exc}") from exc
    if not isinstance(results, dict):
        raise ManifestError(f"cell {cell.cell_id} results root must be an object")
    episodes = results.get("episodes")
    if not isinstance(episodes, list) or len(episodes) != plan.episodes:
        raise ManifestError(
            f"cell {cell.cell_id} recorded {len(episodes) if isinstance(episodes, list) else 0} "
            f"episodes; expected {plan.episodes}"
        )
    expected_ids = list(range(1, plan.episodes + 1))
    if not all(isinstance(episode, dict) for episode in episodes):
        raise ManifestError(f"cell {cell.cell_id} episodes must be objects")
    if [episode.get("episode") for episode in episodes] != expected_ids:
        raise ManifestError(f"cell {cell.cell_id} episode IDs are incomplete or duplicated")
    if any(episode.get("status") != "ok" for episode in episodes):
        raise ManifestError(f"cell {cell.cell_id} contains a failed episode")

    meta = results.get("meta")
    if not isinstance(meta, dict):
        raise ManifestError(f"cell {cell.cell_id} results meta must be an object")
    expected_meta = {
        "model": cell.cell_id,
        "scenario": plan.scenario,
        "total_episodes": plan.episodes,
        "ok_episodes": plan.episodes,
        "tool_schema_source": plan.tool_schema_source,
        "include_game_knowledge": not plan.omit_game_knowledge,
        "held_out_quest": plan.held_out_quest,
        "inference_seed": cell.inference_seed,
        "factorial_schedule_algorithm": plan.schedule_algorithm,
        "factorial_schedule_seed": plan.schedule_seed,
        "factorial_schedule_index": cell.schedule_index,
        "factorial_batch_index": cell.batch_index,
        "factorial_cluster_id": cell.cluster_id,
        "factorial_pair_id": cell.pair_id,
        "environment_seed_mechanism": plan.environment_seed_mechanism,
        "environment_seed": plan.environment_seed,
        "environment_seed_reason": plan.environment_seed_reason,
    }
    mismatches = {
        key: {"expected": expected, "actual": meta.get(key)}
        for key, expected in expected_meta.items()
        if meta.get(key) != expected
    }
    if mismatches:
        raise ManifestError(f"cell {cell.cell_id} result metadata mismatch: {mismatches}")


def require_environment_seed_capability(plan: ExperimentPlan) -> None:
    if plan.environment_seed_mechanism == "unavailable":
        raise ManifestError(
            "launch blocked: Kaetram environment RNG seed is unavailable; scheduling and "
            "inference seeds do not control gameplay Math.random()"
        )


def launch(plan: ExperimentPlan, *, confirmation: str, environ: dict[str, str] | None = None) -> int:
    if not plan.allow_launch:
        raise ManifestError("launch blocked: set execution.allow_launch=true in the reviewed manifest")
    if confirmation != plan.experiment_id:
        raise ManifestError("launch blocked: --confirm-launch must exactly match experiment_id")
    require_environment_seed_capability(plan)
    env_source = os.environ if environ is None else environ
    missing = sorted({c.endpoint_env for c in plan.cells if not env_source.get(c.endpoint_env)})
    if missing:
        raise ManifestError(f"launch blocked: missing endpoint environment variables: {', '.join(missing)}")

    existing = [cell.run_dir for cell in plan.cells if Path(cell.run_dir).exists()]
    if existing:
        raise ManifestError(
            "launch blocked: refusing to reuse existing run directories: " + ", ".join(existing)
        )
    return_code = 0
    for start in range(0, len(plan.cells), plan.max_parallel):
        batch_cells = plan.cells[start:start + plan.max_parallel]
        processes: list[tuple[Cell, subprocess.Popen, Any]] = []
        try:
            for cell in batch_cells:
                run_dir = Path(cell.run_dir)
                run_dir.mkdir(parents=True, exist_ok=False)
                log_handle = (run_dir / "launcher.log").open("w")
                child_env = dict(env_source)
                child_env["KAETRAM_TOOL_SCHEMA_SOURCE"] = plan.tool_schema_source
                if cell.recovery:
                    child_env["KAETRAM_TOOL_RECOVERY"] = "1"
                else:
                    child_env.pop("KAETRAM_TOOL_RECOVERY", None)
                proc = subprocess.Popen(
                    cell_command(plan, cell),
                    cwd=REPO,
                    env=child_env,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                )
                processes.append((cell, proc, log_handle))
        except Exception:
            for _cell, proc, log_handle in processes:
                if proc.poll() is None:
                    proc.terminate()
                log_handle.close()
            raise

        validation_errors = []
        for cell, proc, log_handle in processes:
            rc = proc.wait()
            log_handle.close()
            if rc != 0 and return_code == 0:
                return_code = rc
            elif rc == 0:
                try:
                    validate_cell_result(plan, cell)
                except ManifestError as exc:
                    validation_errors.append(str(exc))
        if validation_errors:
            raise ManifestError(
                "launch blocked after incomplete cell results: " + "; ".join(validation_errors)
            )
        if return_code != 0:
            break
    return return_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--write-plan", type=Path, help="write the validated preflight plan as JSON")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true",
        help="validate and print every cell/command without resolving endpoints or launching (default)",
    )
    mode.add_argument("--execute", action="store_true", help="launch all cells after safety checks")
    parser.add_argument("--confirm-launch", default="", help="must exactly match experiment_id with --execute")
    args = parser.parse_args()
    try:
        plan = build_plan(args.manifest)
        payload = plan_dict(plan)
        print(json.dumps(payload, indent=2))
        if args.write_plan:
            args.write_plan.parent.mkdir(parents=True, exist_ok=True)
            args.write_plan.write_text(json.dumps(payload, indent=2) + "\n")
        if not args.execute:
            print("\nPreflight passed. Nothing was launched.")
            return 0
        return launch(plan, confirmation=args.confirm_launch)
    except ManifestError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
