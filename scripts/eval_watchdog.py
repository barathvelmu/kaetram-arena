#!/usr/bin/env python3
"""
Lightweight watchdog for live eval runs.

Watches:
- endpoint /health
- eval_harness.py / play_qwen.py process presence
- results.json ok-episode count
- latest sandbox session log freshness

If a model arm stops making progress or its endpoint dies, the watchdog writes
status to the run directory and exits nonzero. Optionally, it can terminate the
matching eval processes to stop cost bleed.

Example:
  python3 scripts/eval_watchdog.py \
    --run-dir dataset/eval/runs/20260416_190942_curious_n10_recover \
    --episodes 10 \
    --model base=https://.../v1,/tmp/kaetram_eval_base,9052 \
    --model r9-sft=https://.../v1,/tmp/kaetram_eval_r9-sft,9051 \
    --kill-on-failure
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError, HTTPError
from urllib.request import urlopen


def parse_model_arg(raw: str) -> tuple[str, dict]:
    try:
        name, rest = raw.split("=", 1)
        endpoint, sandbox, port = rest.split(",", 2)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid --model '{raw}', expected name=endpoint,sandbox,port"
        ) from exc
    return name, {
        "endpoint": endpoint.strip(),
        "sandbox": sandbox.strip(),
        "port": str(port).strip(),
    }


def endpoint_health_url(endpoint: str) -> str:
    return endpoint[:-3] + "/health" if endpoint.endswith("/v1") else endpoint.rstrip("/") + "/health"


def probe_health(endpoint: str, timeout: int) -> tuple[bool, str]:
    url = endpoint_health_url(endpoint)
    try:
        with urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return 200 <= resp.status < 300, body[:400]
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return False, repr(exc)


def read_results(results_path: Path) -> tuple[int, int]:
    if not results_path.is_file():
        return 0, 0
    data = json.loads(results_path.read_text())
    episodes = data.get("episodes", [])
    ok_episodes = sum(1 for ep in episodes if ep.get("status") == "ok")
    return len(episodes), ok_episodes


def latest_log_info(sandbox: str) -> tuple[str | None, float | None]:
    log_dir = Path(sandbox) / "logs"
    if not log_dir.is_dir():
        return None, None
    logs = sorted(log_dir.glob("session_*.log"), key=lambda p: p.stat().st_mtime)
    if not logs:
        return None, None
    latest = logs[-1]
    return latest.name, latest.stat().st_mtime


def list_processes() -> list[tuple[int, int, str]]:
    rows = []
    out = subprocess.run(
        ["ps", "-eo", "pid,etimes,args"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    for line in out[1:]:
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        pid_s, etimes_s, args = parts
        try:
            rows.append((int(pid_s), int(etimes_s), args))
        except ValueError:
            continue
    return rows


def matching_pids(process_rows: list[tuple[int, int, str]], run_dir: Path, sandbox: str, port: str) -> dict[str, list[int]]:
    groups = {"eval_harness": [], "play_qwen": [], "game_server": []}
    for pid, _elapsed, args in process_rows:
        if "eval_harness.py" in args and f"--server-port {port}" in args:
            groups["eval_harness"].append(pid)
        if "play_qwen.py" in args and sandbox in args and f"--server-port {port}" in args:
            groups["play_qwen"].append(pid)
        if f"dist/main.js --port {port}" in args:
            groups["game_server"].append(pid)
    return groups


def write_status(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2))


def terminate_pids(pids: list[int]) -> None:
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue


def main() -> int:
    parser = argparse.ArgumentParser(description="Background watchdog for eval runs")
    parser.add_argument("--run-dir", required=True, help="Eval run directory")
    parser.add_argument("--episodes", type=int, required=True, help="Target ok episodes per model")
    parser.add_argument(
        "--model",
        action="append",
        required=True,
        help="Model spec: name=endpoint,sandbox,port",
    )
    parser.add_argument("--interval", type=int, default=30, help="Poll interval seconds")
    parser.add_argument("--stale-seconds", type=int, default=300, help="Max age without progress")
    parser.add_argument("--startup-grace-seconds", type=int, default=240, help="Allow startup with no log yet")
    parser.add_argument("--health-timeout", type=int, default=10, help="HTTP timeout for /health")
    parser.add_argument("--health-fail-threshold", type=int, default=2, help="Consecutive failed health checks before alert")
    parser.add_argument("--kill-on-failure", action="store_true", help="Terminate matching eval/game processes on failure")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    status_path = run_dir / "watchdog_status.json"
    alert_path = run_dir / "watchdog_alert.txt"

    models = {}
    for raw in args.model:
        name, cfg = parse_model_arg(raw)
        models[name] = cfg

    health_failures = {name: 0 for name in models}
    last_progress_ts = {name: time.time() for name in models}

    while True:
        now = time.time()
        process_rows = list_processes()
        payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "run_dir": str(run_dir),
            "episodes_target": args.episodes,
            "models": {},
        }
        failures = []
        all_done = True

        for name, cfg in models.items():
            results_path = run_dir / name / "results.json"
            total_eps, ok_eps = read_results(results_path)
            latest_log, latest_log_mtime = latest_log_info(cfg["sandbox"])
            pids = matching_pids(process_rows, run_dir, cfg["sandbox"], cfg["port"])
            eval_alive = bool(pids["eval_harness"])
            play_alive = bool(pids["play_qwen"])
            gs_alive = bool(pids["game_server"])
            health_ok, health_detail = probe_health(cfg["endpoint"], args.health_timeout)
            if health_ok:
                health_failures[name] = 0
            else:
                health_failures[name] += 1

            progress_candidates = []
            if results_path.exists():
                progress_candidates.append(results_path.stat().st_mtime)
            if latest_log_mtime is not None:
                progress_candidates.append(latest_log_mtime)
            if progress_candidates:
                newest_progress = max(progress_candidates)
                last_progress_ts[name] = max(last_progress_ts[name], newest_progress)
            progress_age = now - last_progress_ts[name]
            proc_uptime = 0
            for pid, elapsed, proc_args in process_rows:
                if pid in pids["eval_harness"]:
                    proc_uptime = max(proc_uptime, elapsed)

            done = ok_eps >= args.episodes
            all_done &= done

            model_status = {
                "ok_episodes": ok_eps,
                "total_recorded_episodes": total_eps,
                "endpoint_healthy": health_ok,
                "endpoint_detail": health_detail,
                "health_failure_streak": health_failures[name],
                "latest_log": latest_log,
                "latest_log_age_seconds": None if latest_log_mtime is None else round(now - latest_log_mtime, 1),
                "last_progress_age_seconds": round(progress_age, 1),
                "eval_harness_pids": pids["eval_harness"],
                "play_qwen_pids": pids["play_qwen"],
                "game_server_pids": pids["game_server"],
                "done": done,
            }
            payload["models"][name] = model_status

            if done:
                continue

            if health_failures[name] >= args.health_fail_threshold:
                failures.append(f"{name}: endpoint unhealthy ({health_detail})")

            if not eval_alive:
                failures.append(f"{name}: eval_harness missing before completion")

            if not gs_alive:
                failures.append(f"{name}: game server missing on port {cfg['port']}")

            if latest_log_mtime is None and proc_uptime <= args.startup_grace_seconds:
                continue

            if progress_age > args.stale_seconds:
                failures.append(f"{name}: stale progress for {int(progress_age)}s")

        write_status(status_path, payload)

        if failures:
            alert_lines = [
                f"[{payload['timestamp']}] WATCHDOG FAILURE",
                *failures,
            ]
            alert_path.write_text("\n".join(alert_lines) + "\n")
            if args.kill_on_failure:
                all_pids = []
                for model_status in payload["models"].values():
                    all_pids.extend(model_status["eval_harness_pids"])
                    all_pids.extend(model_status["play_qwen_pids"])
                    all_pids.extend(model_status["game_server_pids"])
                terminate_pids(sorted(set(all_pids)))
            print("\n".join(alert_lines))
            return 1

        if all_done:
            if alert_path.exists():
                alert_path.unlink()
            print(f"[{payload['timestamp']}] WATCHDOG COMPLETE")
            return 0

        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
