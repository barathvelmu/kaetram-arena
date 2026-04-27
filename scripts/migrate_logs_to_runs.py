#!/usr/bin/env python3
"""One-time migration: restructure flat agent logs into runs/ hierarchy.

Before:
    dataset/raw/agent_N/logs/session_1_20260402_050111.log
    dataset/raw/agent_N/logs/session_2_20260402_053700.log
    ...

After:
    dataset/raw/agent_N/runs/run_20260402_010111/session_1_20260402_050111.log
    dataset/raw/agent_N/runs/run_20260402_010111/session_2_20260402_053700.log
    ...
    dataset/raw/agent_N/logs  →  symlink to latest run dir

Usage:
    python3 scripts/migrate_logs_to_runs.py --dry-run    # preview only
    python3 scripts/migrate_logs_to_runs.py              # execute migration
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_DIR / "dataset" / "raw"
EST = timezone(timedelta(hours=-4))  # EDT (current offset for America/New_York)


def _extract_session_num(filename: str) -> int | None:
    m = re.match(r"session_(\d+)_", filename)
    return int(m.group(1)) if m else None


def _extract_timestamp_from_filename(filename: str) -> str | None:
    """Extract YYYYMMDD_HHMMSS from session filename (UTC)."""
    m = re.search(r"(\d{8}_\d{6})", filename)
    return m.group(1) if m else None


def _utc_ts_to_est(ts_str: str) -> str:
    """Convert YYYYMMDD_HHMMSS (UTC) to EST string."""
    dt = datetime.strptime(ts_str, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
    est_dt = dt.astimezone(EST)
    return est_dt.strftime("%Y%m%d_%H%M%S")


def detect_runs(log_files: list[Path]) -> list[list[Path]]:
    """Group sorted log files into runs by detecting session counter resets.

    A new run starts when:
    - session number drops to 1 (or lower than previous)
    - AND there was a previous session with a higher number
    """
    if not log_files:
        return []

    runs: list[list[Path]] = []
    current_run: list[Path] = []
    prev_session_num = 0

    for f in log_files:
        snum = _extract_session_num(f.name)
        if snum is None:
            current_run.append(f)
            continue

        # New run: session number reset to 1 (or lower)
        if snum <= prev_session_num and snum == 1 and current_run:
            runs.append(current_run)
            current_run = []

        current_run.append(f)
        prev_session_num = snum

    if current_run:
        runs.append(current_run)

    return runs


def compute_run_id(run_files: list[Path]) -> str:
    """Generate run_YYYYMMDD_HHMMSS ID from the first session's timestamp (EST)."""
    for f in run_files:
        ts = _extract_timestamp_from_filename(f.name)
        if ts:
            est_ts = _utc_ts_to_est(ts)
            return f"run_{est_ts}"
    # Fallback: use file mtime of first log
    mtime = run_files[0].stat().st_mtime
    dt = datetime.fromtimestamp(mtime, tz=EST)
    return f"run_{dt.strftime('%Y%m%d_%H%M%S')}"


def build_run_meta(run_id: str, agent_dir: Path, run_files: list[Path]) -> dict:
    """Build run.meta.json from the first session's sidecar metadata."""
    agent_name = agent_dir.name
    agent_id = int(agent_name.replace("agent_", "")) if "agent_" in agent_name else 0

    meta = {
        "run_id": run_id,
        "agent_id": agent_id,
    }

    # Read first session's sidecar for harness/personality/model info
    for f in run_files:
        sidecar = f.with_suffix(".meta.json")
        if sidecar.exists():
            try:
                sm = json.loads(sidecar.read_text())
                meta["personality"] = sm.get("personality", "grinder")
                meta["harness"] = sm.get("harness", "claude")
                meta["model"] = sm.get("model", "sonnet")
                meta["username"] = sm.get("username", f"ClaudeBot{agent_id}")
                break
            except (json.JSONDecodeError, OSError):
                pass

    # Started timestamp from first file
    for f in run_files:
        ts = _extract_timestamp_from_filename(f.name)
        if ts:
            dt_utc = datetime.strptime(ts, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
            est_dt = dt_utc.astimezone(EST)
            meta["started_at"] = est_dt.isoformat()
            break

    meta["session_count"] = len(run_files)
    return meta


def migrate_agent(agent_dir: Path, dry_run: bool = True) -> None:
    logs_dir = agent_dir / "logs"
    runs_dir = agent_dir / "runs"

    if not logs_dir.exists() or logs_dir.is_symlink():
        print(f"  {agent_dir.name}: skipping (logs/ is already a symlink or doesn't exist)")
        return

    # Collect all .log files, sorted by mtime (chronological order)
    log_files = sorted(logs_dir.glob("session_*.log"), key=lambda p: p.stat().st_mtime)
    if not log_files:
        print(f"  {agent_dir.name}: no session logs found")
        return

    # Also collect orphan .meta.json files (no matching .log)
    meta_files = set(logs_dir.glob("session_*.meta.json"))
    log_stems = {f.stem for f in log_files}
    # meta files whose stem (minus .meta) matches a log
    paired_metas = {f for f in meta_files if f.name.replace(".meta.json", "") in log_stems}
    orphan_metas = meta_files - paired_metas

    runs = detect_runs(log_files)
    print(f"  {agent_dir.name}: {len(log_files)} logs → {len(runs)} runs")

    if not runs:
        return

    last_run_dir = None
    for run_files in runs:
        run_id = compute_run_id(run_files)
        run_dir = runs_dir / run_id
        last_run_dir = run_dir

        # Collect paired sidecars for this run
        run_metas = []
        for f in run_files:
            sidecar = f.with_suffix(".meta.json")
            if sidecar.exists():
                run_metas.append(sidecar)

        if dry_run:
            print(f"    {run_id}: {len(run_files)} sessions "
                  f"({run_files[0].name} → {run_files[-1].name})")
        else:
            run_dir.mkdir(parents=True, exist_ok=True)

            # Write run.meta.json
            run_meta = build_run_meta(run_id, agent_dir, run_files)
            (run_dir / "run.meta.json").write_text(json.dumps(run_meta, indent=2))

            # Move log files + their paired sidecars
            for f in run_files:
                dest = run_dir / f.name
                shutil.move(str(f), str(dest))
            for m in run_metas:
                dest = run_dir / m.name
                shutil.move(str(m), str(dest))

            print(f"    {run_id}: moved {len(run_files)} sessions + {len(run_metas)} metas")

    # Handle orphan meta files: move to the latest run dir
    if orphan_metas and not dry_run and last_run_dir:
        for m in orphan_metas:
            dest = last_run_dir / m.name
            shutil.move(str(m), str(dest))
        print(f"    moved {len(orphan_metas)} orphan meta files to latest run")
    elif orphan_metas and dry_run:
        print(f"    {len(orphan_metas)} orphan meta files (will go to latest run)")

    if not dry_run:
        # Remove the now-empty logs/ directory (move any leftover non-session files)
        remaining = list(logs_dir.iterdir())
        if remaining and last_run_dir:
            print(f"    Moving {len(remaining)} leftover items from logs/ to latest run:")
            for r in remaining:
                dest = last_run_dir / r.name
                if r.is_dir():
                    if dest.exists():
                        shutil.rmtree(str(dest))
                    shutil.move(str(r), str(dest))
                else:
                    shutil.move(str(r), str(dest))
                print(f"      {r.name}")

        # Remove logs/ dir (should be empty now)
        try:
            logs_dir.rmdir()
        except OSError:
            shutil.rmtree(str(logs_dir))

        # Create symlink: logs → latest run dir (relative path)
        rel_target = last_run_dir.relative_to(agent_dir)
        logs_dir.symlink_to(rel_target)
        print(f"    symlink: logs/ → {rel_target}")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview migration without moving files")
    parser.add_argument("--agent", type=int, default=None,
                        help="Migrate only this agent (e.g. --agent 0)")
    args = parser.parse_args()

    if args.dry_run:
        print("=== DRY RUN — no files will be moved ===\n")
    else:
        print("=== MIGRATING LOGS TO RUNS HIERARCHY ===\n")

    agent_dirs = sorted(RAW_DIR.glob("agent_*"))
    if args.agent is not None:
        agent_dirs = [d for d in agent_dirs if d.name == f"agent_{args.agent}"]

    if not agent_dirs:
        print("No agent directories found.")
        return 1

    for agent_dir in agent_dirs:
        migrate_agent(agent_dir, dry_run=args.dry_run)

    if args.dry_run:
        print("\nRe-run without --dry-run to execute the migration.")
    else:
        print("\n=== Migration complete ===")

    return 0


if __name__ == "__main__":
    sys.exit(main())
