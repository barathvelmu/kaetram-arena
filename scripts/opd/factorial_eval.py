#!/usr/bin/env python3
"""Manifest-driven, fail-closed launcher for the 2B weights x recovery eval.

Default behavior is preflight only. Launching requires all three of:
  1. execution.allow_launch=true in the manifest,
  2. --execute, and
  3. --confirm-launch matching experiment_id exactly.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from heldout_guard import HeldOutGuardError, validate_eval_selection  # noqa: E402


REQUIRED_WEIGHTS = ("base", "r2", "r3")
REQUIRED_RECOVERY = (False, True)
REQUIRED_PERSONALITIES = ("grinder", "completionist", "explorer_tinkerer")
PERSONALITY_CODES = {"grinder": "g", "completionist": "c", "explorer_tinkerer": "e"}


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
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ManifestError("manifest schema_version must be 1")
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
    if isinstance(max_parallel, bool) or not isinstance(max_parallel, int) or max_parallel < 1:
        raise ManifestError("execution.max_parallel must be a positive integer")

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
                    ))

    if cells[-1].server_port > 65535:
        raise ManifestError("generated server ports exceed 65535; lower server_port_start")
    if max_parallel > len(cells):
        raise ManifestError("execution.max_parallel cannot exceed the generated cell count")

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
        cells=tuple(cells),
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
        "held_out_quest": plan.held_out_quest or None,
    }
    mismatches = {
        key: {"expected": expected, "actual": meta.get(key)}
        for key, expected in expected_meta.items()
        if meta.get(key) != expected
    }
    if mismatches:
        raise ManifestError(f"cell {cell.cell_id} result metadata mismatch: {mismatches}")


def launch(plan: ExperimentPlan, *, confirmation: str, environ: dict[str, str] | None = None) -> int:
    if not plan.allow_launch:
        raise ManifestError("launch blocked: set execution.allow_launch=true in the reviewed manifest")
    if confirmation != plan.experiment_id:
        raise ManifestError("launch blocked: --confirm-launch must exactly match experiment_id")
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
