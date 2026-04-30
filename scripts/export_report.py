#!/usr/bin/env python3
"""Export a comprehensive JSON report of all agent training data.

Walks `dataset/raw/agent_*/runs/run_*/` via the shared
`scripts/log_analysis/parse.py` kernel (so log parsing stays in lock-step
with `analyze.py`), aggregates per-run *and* per-agent cross-run rollups,
and serializes to `/tmp/kaetram-export/report.json` for web/mobile fetch.

Output: /tmp/kaetram-export/report.json
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_DIR / "dataset" / "raw"
OUTPUT_DIR = Path("/tmp/kaetram-export")
OUTPUT = OUTPUT_DIR / "report.json"
CACHE_FILE = OUTPUT_DIR / "session_cache.json"
LOCK_FILE = OUTPUT_DIR / ".regen.lock"

DATE_FLOOR = datetime(2026, 4, 25, tzinfo=timezone.utc)
DATE_FLOOR_TS = DATE_FLOOR.timestamp()

# Shared parser kernel — single source of truth for log shape, harness
# detection, cost/token aggregation, and run-level views.
sys.path.insert(0, str(PROJECT_DIR))
from scripts.log_analysis.parse import (  # noqa: E402
    SessionView,
    deaths,
    fmt_est,
    latest_observe,
    list_agent_dirs,
    list_runs,
    parse_run_sessions,
    parse_session_auto,
    run_meta as read_run_meta,
    session_meta as read_session_meta,
)

# MongoDB (optional)
try:
    import pymongo
    _mongo = pymongo.MongoClient("localhost", 27017, serverSelectionTimeoutMS=2000)
    _db = _mongo["kaetram_devlopment"]
    _db.command("ping")
    HAS_MONGO = True
except Exception:
    HAS_MONGO = False
    _db = None


# ─────────────────────────── per-session stats extraction ───────────────────
#
# Reduces a parsed `SessionView` (from `scripts/log_analysis/parse.py`) into
# a small JSON-friendly stats dict suitable for caching and serialization.

def _session_stats(sv: SessionView) -> dict:
    """Per-session stats dict (small, cacheable, JSON-friendly)."""
    p = sv.log_path
    tool_counter: Counter = Counter()
    npc_interactions: list[dict] = []
    for tc in sv.tool_calls:
        tool_counter[tc.short_name] += 1
        if tc.short_name in ("interact_npc", "talk_npc"):
            inp = tc.input or {}
            npc_interactions.append({
                "tool": tc.short_name,
                "npc": inp.get("npc_name", inp.get("instance_id", "?")),
            })

    last = latest_observe(sv) or {}
    stats_block = (last.get("stats") or {}) if isinstance(last, dict) else {}
    level_end = stats_block.get("level") if isinstance(stats_block.get("level"), int) else None

    # level_start: try first observe (cheap re-walk; tool_calls is in memory).
    level_start: int | None = None
    for tc in sv.tool_calls:
        if tc.short_name == "observe" and isinstance(tc.result_payload, dict):
            s = tc.result_payload.get("stats") or {}
            lvl = s.get("level")
            if isinstance(lvl, int):
                level_start = lvl
                break

    rs = sv.result_summary or {}
    duration_s = 0
    if isinstance(rs.get("duration_ms"), (int, float)):
        duration_s = rs["duration_ms"] / 1000.0

    return {
        "file": p.name,
        "agent": p.parts[-4] if len(p.parts) >= 4 else "unknown",
        "tools": dict(tool_counter),
        "turns": sv.num_turns,
        "duration_s": duration_s,
        "npc_interactions": npc_interactions,
        "deaths": len(deaths(sv)),
        "model": (sv.meta or {}).get("model", ""),
        "harness": (sv.meta or {}).get("harness", ""),
        "level_start": level_start,
        "level_end": level_end,
        "session_index": (sv.meta or {}).get("session"),
        "auth_mode": (sv.meta or {}).get("auth_mode"),
        "log_shape": (sv.meta or {}).get("harness", "claude"),
        "total_cost_usd": sv.total_cost_usd,
        "total_tokens": dict(sv.total_tokens) if sv.total_tokens else {},
        "synthetic_summary": bool(rs.get("synthetic")),
        "started_at": _filename_timestamp(p),
    }


def _filename_timestamp(p: Path) -> str:
    """ISO-ish string from session_N_YYYYMMDD_HHMMSS.log filename."""
    import re as _re
    m = _re.search(r"(\d{8})_(\d{6})", p.name)
    if not m:
        return ""
    d, t = m.group(1), m.group(2)
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}T{t[:2]}:{t[2:4]}:{t[4:6]}"


# ─────────────────────────── per-session cache ───────────────────────────
#
# Closed sessions never reparse. Keyed by (mtime, size) to invalidate when a
# log is appended-to or rotated. Cache value is the small stats dict above
# (NOT the full SessionView), so each entry stays a few KB.

def _load_cache() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_FILE.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(cache, f)
        os.replace(tmp, CACHE_FILE)
    except Exception:
        pass


def _cached_session_stats(log_path: Path, cache: dict) -> dict:
    """Parse via shared kernel + cache the small stats dict."""
    st = log_path.stat()
    key = str(log_path)
    sig = [int(st.st_mtime), int(st.st_size)]
    entry = cache.get(key)
    if entry and entry.get("sig") == sig:
        return entry["stats"]
    sv = parse_session_auto(log_path)
    stats = _session_stats(sv)
    cache[key] = {"sig": sig, "stats": stats}
    return stats


# ─────────────────────────── run grouping ───────────────────────────

def _build_run_record(agent_dir: Path, run_dir: Path, cache: dict) -> dict | None:
    """One run dict with session details + run-wide aggregates.

    Returns None when nothing in the run is recent enough to include
    (DATE_FLOOR cheap-skip).
    """
    log_files = sorted(run_dir.glob("session_*.log"), key=lambda p: p.stat().st_mtime)
    if not log_files:
        return None
    if log_files[-1].stat().st_mtime < DATE_FLOOR_TS:
        return None

    rmeta = read_run_meta(run_dir)
    sessions: list[dict] = []
    for lf in log_files:
        try:
            st = lf.stat()
        except OSError:
            continue
        if st.st_size < 1024:
            continue
        if st.st_mtime < DATE_FLOOR_TS:
            continue
        sessions.append(_cached_session_stats(lf, cache))

    if not sessions:
        return None

    total_turns = sum(s["turns"] for s in sessions)
    total_deaths = sum(s["deaths"] for s in sessions)
    total_duration = sum(s.get("duration_s", 0) for s in sessions)
    total_cost = sum(s.get("total_cost_usd") or 0 for s in sessions)

    level_start = next(
        (s["level_start"] for s in sessions if s.get("level_start") is not None),
        None,
    )
    level_end = next(
        (s["level_end"] for s in reversed(sessions) if s.get("level_end") is not None),
        None,
    )

    run_tools: Counter = Counter()
    run_tokens: Counter = Counter()
    for s in sessions:
        for tool, count in s["tools"].items():
            run_tools[tool] += count
        for tk, tv in (s.get("total_tokens") or {}).items():
            if isinstance(tv, (int, float)):
                run_tokens[tk] += int(tv)

    npcs: list[dict] = []
    for s in sessions:
        npcs.extend(s.get("npc_interactions", []))

    harness = rmeta.get("harness") or sessions[0].get("harness", "")
    model = rmeta.get("model") or sessions[0].get("model", "")
    username = rmeta.get("username", "")
    personality = rmeta.get("personality", "")
    started_at = rmeta.get("started_at") or sessions[0].get("started_at", "")
    ended_at = sessions[-1].get("started_at", "")

    return {
        "run_id": run_dir.name,
        "harness": harness,
        "model": model,
        "username": username,
        "personality": personality,
        "started_at": started_at,
        "ended_at": ended_at,
        "sessions": len(sessions),
        "total_turns": total_turns,
        "total_duration_s": round(total_duration),
        "total_duration_min": round(total_duration / 60, 1),
        "total_deaths": total_deaths,
        "total_cost_usd": round(total_cost, 4) if total_cost > 0 else None,
        "total_tokens": dict(run_tokens),
        "level_start": level_start,
        "level_end": level_end,
        "level_gain": (level_end or 0) - (level_start or 0),
        "tool_usage": dict(run_tools.most_common(15)),
        "npc_interactions": len(npcs),
        "npcs_talked_to": sorted({n["npc"] for n in npcs}),
        "session_details": [
            {
                "file": s["file"],
                "session_index": s.get("session_index"),
                "log_shape": s.get("log_shape"),
                "turns": s["turns"],
                "duration_s": round(s.get("duration_s", 0)),
                "deaths": s["deaths"],
                "level_start": s["level_start"],
                "level_end": s["level_end"],
                "cost_usd": s.get("total_cost_usd"),
                "synthetic": s.get("synthetic_summary"),
                "top_tools": dict(Counter(s["tools"]).most_common(5)),
            }
            for s in sessions
        ],
    }


def build_runs_for_agent(agent_dir: Path, cache: dict) -> tuple[list[dict], list[dict]]:
    """Return (runs, all_sessions) for one agent_N directory."""
    runs: list[dict] = []
    all_sessions: list[dict] = []
    for run_dir in list_runs(agent_dir):
        rec = _build_run_record(agent_dir, run_dir, cache)
        if not rec:
            continue
        runs.append(rec)
        # Re-derive session list from the record's session_details + cache
        # so callers can roll up across runs without re-walking the FS.
        all_sessions.extend(
            _cached_session_stats(run_dir / sd["file"], cache)
            for sd in rec["session_details"]
        )
    return runs, all_sessions


# ─────────────────────────── per-agent cross-run rollup ───────────────────

def _agent_summary(runs: list[dict], all_sessions: list[dict]) -> dict:
    """Aggregate stats for one agent across ALL its runs.

    This is the new section in v3. Lets web/mobile clients answer
    "agent_2 across all runs: X total turns, $Y, max level Z" without
    iterating the per-run array themselves.
    """
    if not runs:
        return {}
    tools: Counter = Counter()
    tokens: Counter = Counter()
    harnesses: set[str] = set()
    models: set[str] = set()
    cost_total = 0.0
    has_any_cost = False
    for r in runs:
        for tool, count in (r.get("tool_usage") or {}).items():
            tools[tool] += count
        for tk, tv in (r.get("total_tokens") or {}).items():
            if isinstance(tv, (int, float)):
                tokens[tk] += int(tv)
        if r.get("harness"):
            harnesses.add(r["harness"])
        if r.get("model"):
            models.add(r["model"])
        c = r.get("total_cost_usd")
        if c is not None:
            cost_total += c
            has_any_cost = True

    levels = [r.get("level_end") for r in runs if r.get("level_end") is not None]
    return {
        "total_runs": len(runs),
        "total_sessions": len(all_sessions),
        "total_turns": sum(r["total_turns"] for r in runs),
        "total_deaths": sum(r["total_deaths"] for r in runs),
        "total_cost_usd": round(cost_total, 4) if has_any_cost else None,
        "total_tokens": dict(tokens),
        "level_max": max(levels) if levels else None,
        "level_latest": runs[-1].get("level_end"),
        "tool_usage": dict(tools.most_common(20)),
        "harnesses_used": sorted(harnesses),
        "models_used": sorted(models),
        "first_run": runs[0]["run_id"] if runs else None,
        "latest_run": runs[-1]["run_id"] if runs else None,
        "first_run_started_at": runs[0].get("started_at"),
        "latest_run_started_at": runs[-1].get("started_at"),
    }


# ─────────────────────────── mongo (meta-driven) ───────────────────────────

def collect_known_usernames() -> dict[str, dict]:
    """Walk run.meta.json files; return {username: {harness, model}}."""
    out: dict[str, dict] = {}
    if not RAW_DIR.exists():
        return out
    for meta in RAW_DIR.glob("agent_*/runs/run_*/run.meta.json"):
        try:
            m = json.loads(meta.read_text())
        except (OSError, ValueError):
            continue
        u = m.get("username")
        if not u:
            continue
        out.setdefault(u, {"harness": m.get("harness", ""), "model": m.get("model", "")})
    return out


def get_mongo_state(usernames_meta: dict[str, dict]) -> dict:
    if not HAS_MONGO:
        return {"available": False}
    if not usernames_meta:
        return {"available": True, "agents": {}}

    agents = {}
    for uname, meta in sorted(usernames_meta.items()):
        # Mongo stores usernames lowercased.
        key = uname.lower()
        agent: dict = {"username": uname, "harness": meta.get("harness", ""), "model": meta.get("model", "")}

        info = _db.player_info.find_one({"username": key})
        if not info:
            continue  # never logged in → skip
        agent["level"] = info.get("level", 1)
        agent["hp"] = info.get("hitPoints", 0)
        agent["max_hp"] = info.get("maxHitPoints", 0)
        agent["x"] = info.get("x", 0)
        agent["y"] = info.get("y", 0)

        quests_doc = _db.player_quests.find_one({"username": key})
        if quests_doc:
            quest_data = {}
            for q in quests_doc.get("quests", []) or []:
                if isinstance(q, dict) and q.get("key"):
                    stage = q.get("stage", 0)
                    if stage > 0:
                        quest_data[q["key"]] = {"stage": stage}
            agent["quests"] = quest_data

        stats_doc = _db.player_statistics.find_one({"username": key})
        if stats_doc:
            kills = stats_doc.get("mobKills", {})
            if isinstance(kills, dict):
                agent["total_kills"] = sum(v for v in kills.values() if isinstance(v, (int, float)))

        agents[uname] = agent

    return {
        "available": True,
        "note": "Current run only — resets on restart-agent.sh",
        "agents": agents,
    }


# ─────────────────────────── report assembly ───────────────────────────

def build_report() -> dict:
    cache = _load_cache()
    report: dict = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "generated_at_est": fmt_est(datetime.now(timezone.utc)),
        "date_range": f"{DATE_FLOOR.date().isoformat()} to present",
        "description": (
            "Kaetram AI Agent training data. Multi-harness (Claude + OpenCode "
            "models: DeepSeek V4, Qwen, Grok). Each 'run' is a fresh Level 1 "
            "start (restart-agent.sh resets DB). Runs contain multiple sessions. "
            "Parsing kernel: scripts/log_analysis/parse.py — same one analyze.py uses."
        ),
    }

    agent_data: dict[str, dict] = {}
    all_tools: Counter = Counter()
    harness_breakdown: dict[str, dict] = {}
    model_breakdown: dict[str, dict] = {}
    total_turns = 0
    total_deaths = 0
    total_sessions_count = 0
    total_cost = 0.0
    has_any_cost = False

    if RAW_DIR.exists():
        for agent_dir in list_agent_dirs():
            runs, sessions = build_runs_for_agent(agent_dir, cache)
            if not sessions:
                continue
            for s in sessions:
                for tool, count in s["tools"].items():
                    all_tools[tool] += count
                total_turns += s["turns"]
                total_deaths += s["deaths"]
                if s.get("total_cost_usd") is not None:
                    total_cost += s["total_cost_usd"]
                    has_any_cost = True
            total_sessions_count += len(sessions)

            for r in runs:
                h = r.get("harness") or "unknown"
                hb = harness_breakdown.setdefault(h, {"runs": 0, "sessions": 0, "turns": 0, "deaths": 0, "cost_usd": 0.0})
                hb["runs"] += 1
                hb["sessions"] += r["sessions"]
                hb["turns"] += r["total_turns"]
                hb["deaths"] += r["total_deaths"]
                if r.get("total_cost_usd") is not None:
                    hb["cost_usd"] += r["total_cost_usd"]

                mdl = r.get("model") or "unknown"
                mb = model_breakdown.setdefault(mdl, {"harness": h, "runs": 0, "sessions": 0, "turns": 0, "deaths": 0, "cost_usd": 0.0})
                mb["runs"] += 1
                mb["sessions"] += r["sessions"]
                mb["turns"] += r["total_turns"]
                mb["deaths"] += r["total_deaths"]
                if r.get("total_cost_usd") is not None:
                    mb["cost_usd"] += r["total_cost_usd"]

            agent_data[agent_dir.name] = {"runs": runs, "sessions": sessions}

    # Round breakdown costs.
    for hb in harness_breakdown.values():
        hb["cost_usd"] = round(hb["cost_usd"], 4)
    for mb in model_breakdown.values():
        mb["cost_usd"] = round(mb["cost_usd"], 4)

    report["overview"] = {
        "total_sessions": total_sessions_count,
        "total_runs": sum(len(d["runs"]) for d in agent_data.values()),
        "total_turns": total_turns,
        "total_deaths": total_deaths,
        "total_cost_usd": round(total_cost, 4) if has_any_cost else None,
        "agents": list(agent_data.keys()),
    }

    report["harness_breakdown"] = harness_breakdown
    report["model_breakdown"] = model_breakdown

    report["agents"] = {}
    for agent_name, data in agent_data.items():
        runs = data["runs"]
        sessions = data["sessions"]
        best = max(runs, key=lambda r: r.get("level_end") or 0) if runs else None
        report["agents"][agent_name] = {
            "summary": _agent_summary(runs, sessions),
            "total_sessions": len(sessions),
            "total_runs": len(runs),
            "best_run_level": best.get("level_end") if best else None,
            "best_run_id": best.get("run_id") if best else None,
            "runs": runs,
        }

    report["tool_usage"] = dict(all_tools.most_common(30))
    report["current_game_state"] = get_mongo_state(collect_known_usernames())

    _save_cache(cache)
    return report


# ─────────────────────────── entry point ───────────────────────────

def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Coalesce concurrent regens. Whoever holds the lock writes; others
    # block, then exit immediately (the served file will already be fresh).
    with open(LOCK_FILE, "w") as lockfh:
        fcntl.flock(lockfh, fcntl.LOCK_EX)
        # If someone just regenerated while we were waiting, skip the work.
        try:
            mtime = OUTPUT.stat().st_mtime
        except FileNotFoundError:
            mtime = 0
        if mtime and (time.time() - mtime) < 30:
            print(f"Skipping regen, {OUTPUT} is {int(time.time() - mtime)}s old")
            return

        report = build_report()
        tmp = OUTPUT.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(report, f, indent=2, default=str)
        os.replace(tmp, OUTPUT)

    size_kb = OUTPUT.stat().st_size / 1024
    cost = report["overview"].get("total_cost_usd")
    print(f"Exported {OUTPUT} ({size_kb:.1f} KB)")
    print(f"  Range:    {report['date_range']}")
    print(f"  Runs:     {report['overview']['total_runs']}")
    print(f"  Sessions: {report['overview']['total_sessions']}")
    print(f"  Turns:    {report['overview']['total_turns']}")
    print(f"  Deaths:   {report['overview']['total_deaths']}")
    if cost is not None:
        print(f"  Cost:     ${cost:.2f}")
    print(f"  Harnesses:{list(report['harness_breakdown'].keys())}")
    print(f"  Models:   {list(report['model_breakdown'].keys())}")


if __name__ == "__main__":
    main()
