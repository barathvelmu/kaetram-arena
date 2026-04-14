#!/usr/bin/env python3
"""
eval_harness.py — Standardized evaluation harness for Kaetram AI agents.

Runs N episodes per model with controlled conditions:
1. Resets MongoDB player data between episodes (fresh Level 1)
2. Runs play_qwen.py with fixed max turns
3. Parses session logs for per-episode metrics
4. Outputs aggregated results JSON for eval_compare.py

Usage:
    # Default: 30 episodes, scenario D (open play), both models
    python3 eval_harness.py --episodes 30

    # Specific scenario
    python3 eval_harness.py --episodes 50 --scenario A

    # Custom endpoints
    python3 eval_harness.py \
        --models base=https://...base.../v1 r8-sft=https://...serve.../v1 \
        --episodes 30 --scenario D

    # Single model
    python3 eval_harness.py --models r8-sft=https://...serve.../v1 --episodes 10

Requires: game server running on --server-port, MongoDB in Docker (kaetram-mongo).
"""

import argparse
import json
import math
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default Modal endpoints
DEFAULT_ENDPOINTS = {
    "base": "https://patnir411--kaetram-qwen-base-inference-serve.modal.run/v1",
    "r8-sft": "https://patnir411--kaetram-qwen-serve-inference-serve.modal.run/v1",
}

# Evaluation scenarios from KAE-34 / reference/EVALS.md
SCENARIOS = {
    "A": {
        "name": "Rat Grind",
        "max_turns": 100,
        "description": "Kill 10 rats from Level 1 in Mudwich",
    },
    "B": {
        "name": "Snek Quest",
        "max_turns": 200,
        "description": "Complete Bike Lyson snake quest",
    },
    "C": {
        "name": "Multi-Zone",
        "max_turns": 150,
        "description": "Visit 3+ zones via warping",
    },
    "D": {
        "name": "Open Play",
        "max_turns": 300,
        "description": "300 turns open-ended from Level 1",
    },
}

MONGO_CONTAINER = "kaetram-mongo"
MONGO_DB = "kaetram_devlopment"
MONGO_COLLECTIONS = [
    "player_info", "player_skills", "player_equipment",
    "player_inventory", "player_bank", "player_quests",
    "player_achievements", "player_statistics", "player_abilities",
]


# ---------------------------------------------------------------------------
# MongoDB reset
# ---------------------------------------------------------------------------

def reset_player_db(username: str) -> bool:
    """Delete all MongoDB records for a specific player username."""
    js_parts = [
        f"db.{c}.deleteMany({{username: '{username}'}})"
        for c in MONGO_COLLECTIONS
    ]
    js = "; ".join(js_parts) + "; print('reset_ok');"
    try:
        result = subprocess.run(
            ["docker", "exec", MONGO_CONTAINER, "mongosh", MONGO_DB,
             "--quiet", "--eval", js],
            capture_output=True, text=True, timeout=15,
        )
        return "reset_ok" in result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"  Warning: MongoDB reset failed: {e}")
        return False


# ---------------------------------------------------------------------------
# System prompt resolution
# ---------------------------------------------------------------------------

def resolve_system_prompt(project_dir: str, username: str) -> str:
    """Resolve system.md template with game knowledge, no personality (neutral eval)."""
    system_path = os.path.join(project_dir, "prompts", "system.md")
    knowledge_path = os.path.join(project_dir, "prompts", "game_knowledge.md")

    with open(system_path) as f:
        prompt = f.read()
    knowledge = ""
    if os.path.isfile(knowledge_path):
        with open(knowledge_path) as f:
            knowledge = f.read()

    prompt = prompt.replace("__USERNAME__", username)
    prompt = prompt.replace("__GAME_KNOWLEDGE_BLOCK__", knowledge)
    # No personality for neutral eval — remove the placeholder line
    prompt = prompt.replace("__PERSONALITY_BLOCK__", "")
    prompt = prompt.replace("__PROJECT_DIR__", project_dir)
    prompt = prompt.replace("__SERVER_PORT__", "")
    return prompt


# ---------------------------------------------------------------------------
# Episode runner
# ---------------------------------------------------------------------------

def run_episode(
    project_dir: str,
    endpoint: str,
    model_api_name: str,
    sandbox: str,
    max_turns: int,
    system_prompt_file: str,
    username: str,
    server_port: str = "",
) -> dict:
    """Run one play_qwen.py episode as subprocess. Returns run metadata."""
    cmd = [
        sys.executable, os.path.join(project_dir, "play_qwen.py"),
        "--endpoint", endpoint,
        "--model", model_api_name,
        "--sandbox", sandbox,
        "--max-turns", str(max_turns),
        "--system-prompt", system_prompt_file,
        "--project-dir", project_dir,
    ]
    if server_port:
        cmd.extend(["--server-port", server_port])

    env = {**os.environ, "KAETRAM_USERNAME": username}

    start = time.time()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=max(max_turns * 30, 3600),  # generous timeout
            env=env,
        )
        returncode = result.returncode
    except subprocess.TimeoutExpired:
        returncode = -1
        result = type("R", (), {"stdout": "", "stderr": "TIMEOUT"})()
    duration = time.time() - start

    return {
        "returncode": returncode,
        "duration_seconds": round(duration, 1),
        "stdout_tail": (result.stdout or "")[-500:],
        "stderr_tail": (result.stderr or "")[-300:],
    }


def find_latest_log(sandbox: str) -> Path | None:
    """Find the most recently created session log in the sandbox."""
    log_dir = Path(sandbox) / "logs"
    if not log_dir.is_dir():
        return None
    logs = sorted(log_dir.glob("session_*.log"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


# ---------------------------------------------------------------------------
# Log parsing & metrics
# ---------------------------------------------------------------------------

def parse_log(log_path: Path) -> list[dict]:
    """Parse play_qwen.py JSONL log into list of entries."""
    entries = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def read_game_state(sandbox: str) -> dict | None:
    """Read final game_state.json (written by observe() in play_qwen.py)."""
    gs_path = Path(sandbox) / "state" / "game_state.json"
    if not gs_path.is_file():
        return None
    try:
        with open(gs_path) as f:
            return json.loads(f.read())
    except (json.JSONDecodeError, OSError):
        return None


def _entropy(counts: Counter) -> float:
    """Shannon entropy of a Counter in bits."""
    total = sum(counts.values())
    if total == 0:
        return 0.0
    return -sum(
        (c / total) * math.log2(c / total)
        for c in counts.values() if c > 0
    )


def compute_episode_metrics(log_entries: list[dict], game_state: dict | None) -> dict:
    """Compute per-episode metrics from parsed log + final game state."""
    # Count assistant turns and tool calls
    assistant_turns = 0
    tool_calls_valid = 0
    action_counts = Counter()
    deaths = 0
    stuck_resets = 0
    click_tiles = 0

    for entry in log_entries:
        role = entry.get("role", "")

        if role == "assistant":
            assistant_turns += 1
            tc_list = entry.get("tool_calls", [])
            if tc_list:
                tool_calls_valid += 1
                for tc in tc_list:
                    name = tc.get("name", "unknown")
                    action_counts[name] += 1
                    if name == "respawn":
                        deaths += 1
                    elif name == "stuck_reset":
                        stuck_resets += 1
                    elif name == "click_tile":
                        click_tiles += 1

    turns_played = assistant_turns
    tool_parse_rate = tool_calls_valid / max(1, assistant_turns)

    # Extract final state from game_state.json
    xp_delta = 0
    level_delta = 0
    quests_completed = 0
    quests_accepted = 0
    unique_positions = 0

    if game_state:
        ps = game_state.get("player_stats", {})
        if isinstance(ps, str):
            try:
                ps = json.loads(ps)
            except (json.JSONDecodeError, ValueError):
                ps = {}

        xp_delta = int(ps.get("experience", 0) or 0)
        level_delta = max(0, int(ps.get("level", 1) or 1) - 1)

        # Quest info from final state
        quests = game_state.get("quests", {})
        if isinstance(quests, dict):
            for _qname, qdata in quests.items():
                if isinstance(qdata, dict):
                    stage = qdata.get("stage", 0)
                    if stage == 9999 or qdata.get("completed"):
                        quests_completed += 1
                    if stage > 0:
                        quests_accepted += 1
        elif isinstance(quests, list):
            for qdata in quests:
                if isinstance(qdata, dict):
                    stage = qdata.get("stage", 0)
                    if stage == 9999 or qdata.get("completed"):
                        quests_completed += 1
                    if stage > 0:
                        quests_accepted += 1

    xp_per_turn = xp_delta / max(1, turns_played)

    return {
        "turns_played": turns_played,
        "tool_calls_attempted": assistant_turns,
        "tool_calls_valid": tool_calls_valid,
        "tool_parse_rate": round(tool_parse_rate, 4),
        "xp_delta": xp_delta,
        "xp_per_turn": round(xp_per_turn, 4),
        "level_delta": level_delta,
        "deaths": deaths,
        "survived": deaths == 0,
        "quests_completed": quests_completed,
        "quests_accepted": quests_accepted,
        "unique_positions": unique_positions,
        "action_counts": dict(action_counts),
        "action_entropy": round(_entropy(action_counts), 4),
        "stuck_resets": stuck_resets,
        "click_tiles": click_tiles,
    }


# ---------------------------------------------------------------------------
# Scenario success criteria
# ---------------------------------------------------------------------------

def check_scenario_success(scenario: str, metrics: dict) -> bool:
    """Check if an episode met the scenario-specific success criteria."""
    if scenario == "A":
        # Rat Grind: model should have gained XP from killing rats
        return metrics["xp_delta"] > 0 and metrics["action_counts"].get("attack", 0) >= 5
    elif scenario == "B":
        # Snek Quest: completed at least one quest
        return metrics["quests_completed"] >= 1
    elif scenario == "C":
        # Multi-Zone: used warp to visit multiple zones
        return metrics["action_counts"].get("warp", 0) >= 2
    elif scenario == "D":
        # Open Play: no fixed criteria — just played
        return metrics["turns_played"] > 10 and metrics["tool_parse_rate"] > 0.5
    return False


# ---------------------------------------------------------------------------
# Main eval orchestrator
# ---------------------------------------------------------------------------

def run_model_eval(
    model_name: str,
    endpoint: str,
    n_episodes: int,
    scenario: str,
    output_dir: Path,
    project_dir: str,
    username: str,
    server_port: str,
    resume_from: int = 0,
) -> dict:
    """Run all episodes for one model. Returns full results dict."""
    scenario_cfg = SCENARIOS[scenario]
    max_turns = scenario_cfg["max_turns"]
    sandbox = f"/tmp/kaetram_eval_{model_name}"
    model_output_dir = output_dir / model_name
    model_output_dir.mkdir(parents=True, exist_ok=True)

    # Resolve system prompt once, write to temp file
    prompt_text = resolve_system_prompt(project_dir, username)
    prompt_file = model_output_dir / "system_prompt.md"
    prompt_file.write_text(prompt_text)

    # Model API name (what the endpoint expects)
    api_name = "kaetram" if "serve" in endpoint else "kaetram-base"

    print(f"\n{'='*60}")
    print(f"Evaluating: {model_name}")
    print(f"  Endpoint:  {endpoint}")
    print(f"  Scenario:  {scenario} — {scenario_cfg['name']} ({max_turns} turns)")
    print(f"  Episodes:  {n_episodes} (resuming from {resume_from})")
    print(f"  Sandbox:   {sandbox}")
    print(f"  Username:  {username}")
    print(f"{'='*60}\n")

    episodes = []

    # Load existing results if resuming
    results_path = model_output_dir / "results.json"
    if resume_from > 0 and results_path.is_file():
        with open(results_path) as f:
            existing = json.load(f)
        episodes = existing.get("episodes", [])
        print(f"  Loaded {len(episodes)} existing episodes")

    for ep_num in range(resume_from + 1, n_episodes + 1):
        print(f"\n--- Episode {ep_num}/{n_episodes} ---")

        # 1. Reset player data
        print(f"  Resetting MongoDB for {username}...")
        if not reset_player_db(username):
            print(f"  Warning: DB reset may have failed, continuing anyway")

        # Clear sandbox state
        state_dir = Path(sandbox) / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        for f in state_dir.glob("*"):
            if f.is_file():
                f.unlink()

        # 2. Run episode
        print(f"  Running play_qwen.py (max {max_turns} turns)...")
        run_info = run_episode(
            project_dir=project_dir,
            endpoint=endpoint,
            model_api_name=api_name,
            sandbox=sandbox,
            max_turns=max_turns,
            system_prompt_file=str(prompt_file),
            username=username,
            server_port=server_port,
        )

        # 3. Parse results
        log_path = find_latest_log(sandbox)
        if log_path is None:
            print(f"  No log file found — episode failed to start")
            episode = {
                "episode": ep_num,
                "status": "no_log",
                "duration_seconds": run_info["duration_seconds"],
                "returncode": run_info["returncode"],
            }
            episodes.append(episode)
            continue

        # Copy log to eval output directory
        dest_log = model_output_dir / f"episode_{ep_num:03d}.jsonl"
        dest_log.write_text(log_path.read_text())

        log_entries = parse_log(log_path)
        game_state = read_game_state(sandbox)
        metrics = compute_episode_metrics(log_entries, game_state)
        success = check_scenario_success(scenario, metrics)

        episode = {
            "episode": ep_num,
            "status": "ok",
            "success": success,
            "duration_seconds": run_info["duration_seconds"],
            "returncode": run_info["returncode"],
            "log_file": str(dest_log),
            **metrics,
        }
        episodes.append(episode)

        # Progress summary
        print(f"  Done: {metrics['turns_played']} turns, "
              f"TPR={metrics['tool_parse_rate']:.2f}, "
              f"XP={metrics['xp_delta']}, "
              f"deaths={metrics['deaths']}, "
              f"quests={metrics['quests_completed']}, "
              f"{'SUCCESS' if success else 'no-success'} "
              f"({run_info['duration_seconds']:.0f}s)")

        # 4. Save intermediate results (crash-safe)
        _save_results(results_path, model_name, endpoint, scenario, episodes)

    # Final save
    results = _save_results(results_path, model_name, endpoint, scenario, episodes)
    return results


def _save_results(path: Path, model_name: str, endpoint: str, scenario: str,
                  episodes: list[dict]) -> dict:
    """Save results JSON with metadata and aggregated metrics."""
    # Aggregate per-metric arrays for eval_compare.py
    ok_episodes = [e for e in episodes if e.get("status") == "ok"]
    metrics = {}
    if ok_episodes:
        metrics = {
            "quest_completion_rate": [1 if e.get("quests_completed", 0) > 0 else 0 for e in ok_episodes],
            "xp_per_turn": [e.get("xp_per_turn", 0) for e in ok_episodes],
            "survival_rate": [1 if e.get("survived", False) else 0 for e in ok_episodes],
            "tool_parse_rate": [e.get("tool_parse_rate", 0) for e in ok_episodes],
            "deaths_per_session": [e.get("deaths", 0) for e in ok_episodes],
            # Tier 2
            "xp_delta": [e.get("xp_delta", 0) for e in ok_episodes],
            "level_delta": [e.get("level_delta", 0) for e in ok_episodes],
            "action_entropy": [e.get("action_entropy", 0) for e in ok_episodes],
            "stuck_resets": [e.get("stuck_resets", 0) for e in ok_episodes],
            "click_tiles": [e.get("click_tiles", 0) for e in ok_episodes],
            "success_rate": [1 if e.get("success", False) else 0 for e in ok_episodes],
        }

    git_sha = ""
    try:
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except Exception:
        pass

    results = {
        "meta": {
            "model": model_name,
            "endpoint": endpoint,
            "scenario": scenario,
            "scenario_name": SCENARIOS[scenario]["name"],
            "max_turns": SCENARIOS[scenario]["max_turns"],
            "total_episodes": len(episodes),
            "ok_episodes": len(ok_episodes),
            "timestamp": datetime.now().isoformat(),
            "git_sha": git_sha,
        },
        "episodes": episodes,
        "metrics": metrics,
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"  Results saved: {path}")
    return results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Standardized evaluation harness for Kaetram AI agents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 eval_harness.py --episodes 30
  python3 eval_harness.py --episodes 50 --scenario A
  python3 eval_harness.py --models r8-sft=https://your-endpoint/v1 --episodes 10
        """,
    )
    parser.add_argument(
        "--models", nargs="*",
        help="Model definitions as name=endpoint pairs. "
             "Default: base + r8-sft with standard Modal endpoints",
    )
    parser.add_argument(
        "--episodes", type=int, default=30,
        help="Episodes per model (default: 30, paper minimum: 50 for scenario D)",
    )
    parser.add_argument(
        "--scenario", default="D", choices=list(SCENARIOS.keys()),
        help="Evaluation scenario (default: D = Open Play)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("dataset/eval"),
        help="Output directory (default: dataset/eval/)",
    )
    parser.add_argument(
        "--server-port", default="",
        help="Game server WebSocket port (default: auto)",
    )
    parser.add_argument(
        "--username", default="evalbot",
        help="In-game username for eval bot (default: evalbot)",
    )
    parser.add_argument(
        "--project-dir", default=os.path.dirname(os.path.abspath(__file__)),
        help="Project directory",
    )
    parser.add_argument(
        "--resume", type=int, default=0,
        help="Resume from episode N (skip first N episodes)",
    )
    args = parser.parse_args()

    # Parse model definitions
    models = {}
    if args.models:
        for m in args.models:
            if "=" in m:
                name, endpoint = m.split("=", 1)
                models[name] = endpoint
            else:
                print(f"Error: model must be name=endpoint, got: {m}")
                sys.exit(1)
    else:
        models = dict(DEFAULT_ENDPOINTS)

    # Preflight checks
    print("Eval Harness — Preflight Checks")
    print(f"  Scenario: {args.scenario} — {SCENARIOS[args.scenario]['name']}")
    print(f"  Episodes: {args.episodes} per model")
    print(f"  Models:   {', '.join(models.keys())}")
    print(f"  Output:   {args.output_dir}")

    # Check MongoDB
    try:
        check = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}", "--filter", f"name={MONGO_CONTAINER}"],
            capture_output=True, text=True, timeout=5,
        )
        if MONGO_CONTAINER not in check.stdout:
            print(f"\n  WARNING: MongoDB container '{MONGO_CONTAINER}' not found.")
            print(f"  DB resets will fail. Start it: docker start {MONGO_CONTAINER}")
    except FileNotFoundError:
        print("\n  WARNING: docker not found. DB resets will fail.")

    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Run eval for each model
    all_results = {}
    for model_name, endpoint in models.items():
        results = run_model_eval(
            model_name=model_name,
            endpoint=endpoint,
            n_episodes=args.episodes,
            scenario=args.scenario,
            output_dir=args.output_dir,
            project_dir=args.project_dir,
            username=args.username,
            server_port=args.server_port,
            resume_from=args.resume,
        )
        all_results[model_name] = results

    # Print summary
    print(f"\n{'='*60}")
    print("EVAL COMPLETE — Summary")
    print(f"{'='*60}")
    for model_name, results in all_results.items():
        meta = results["meta"]
        metrics = results.get("metrics", {})
        n = meta["ok_episodes"]
        if n == 0:
            print(f"\n  {model_name}: 0 successful episodes")
            continue

        def _mean(vals):
            return sum(vals) / len(vals) if vals else 0

        print(f"\n  {model_name} ({n} episodes):")
        print(f"    Tool Parse Rate:      {_mean(metrics.get('tool_parse_rate', [])):.3f}")
        print(f"    Quest Completion Rate: {_mean(metrics.get('quest_completion_rate', [])):.3f}")
        print(f"    XP per Turn:          {_mean(metrics.get('xp_per_turn', [])):.3f}")
        print(f"    Survival Rate:        {_mean(metrics.get('survival_rate', [])):.3f}")
        print(f"    Deaths per Session:   {_mean(metrics.get('deaths_per_session', [])):.2f}")
        print(f"    Scenario Success:     {_mean(metrics.get('success_rate', [])):.3f}")

    print(f"\nResults saved to: {args.output_dir}/")
    print("Next: python3 eval_compare.py dataset/eval/base/results.json dataset/eval/r8-sft/results.json")


if __name__ == "__main__":
    main()
