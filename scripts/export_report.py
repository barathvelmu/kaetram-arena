#!/usr/bin/env python3
"""Export a comprehensive JSON report of all agent training data.

Parses session logs (Claude + OpenCode shapes) plus run/session meta files
and MongoDB into a single JSON file that Claude web/mobile can fetch.

Output: /tmp/kaetram-export/report.json
"""

import fcntl
import json
import os
import re
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

REPORT_SCHEMA_VERSION = 2
DATE_FLOOR = datetime(2026, 4, 25, tzinfo=timezone.utc)
DATE_FLOOR_TS = DATE_FLOOR.timestamp()

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


# ─────────────────────────── log shape detection ───────────────────────────

def _peek_shape(path: Path) -> str:
    """Return 'opencode' or 'claude' based on the first non-empty JSON line."""
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # OpenCode events always carry sessionID + part at the top level.
                if "sessionID" in obj and "part" in obj:
                    return "opencode"
                return "claude"
    except OSError:
        pass
    return "claude"


# ─────────────────────────── parsers ───────────────────────────

_LEVEL_RE = re.compile(r'"level"\s*:\s*(\d+)')
_DEAD_RE = re.compile(r'is_dead[\\":\s]+true')


def _empty_stats(path: Path) -> dict:
    return {
        "file": path.name,
        "agent": path.parts[-4] if len(path.parts) >= 4 else "unknown",
        "tools": Counter(),
        "turns": 0,
        "duration_s": 0,
        "npc_interactions": [],
        "deaths": 0,
        "errors": [],
        "model": "",
        "harness": "",
        "level_start": None,
        "level_end": None,
    }


def parse_claude_log(path: Path) -> dict:
    """Original Claude JSONL shape."""
    stats = _empty_stats(path)
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type", "")

                if etype == "result":
                    stats["turns"] = event.get("num_turns", 0)
                    stats["duration_s"] = event.get("duration_ms", 0) / 1000
                    stats["model"] = event.get("model", "")

                elif etype == "assistant":
                    content = event.get("message", {}).get("content", [])
                    if isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict) and c.get("type") == "tool_use":
                                name = c.get("name", "unknown").replace("mcp__kaetram__", "")
                                stats["tools"][name] += 1
                                if name in ("interact_npc", "talk_npc"):
                                    inp = c.get("input", {}) or {}
                                    stats["npc_interactions"].append({
                                        "tool": name,
                                        "npc": inp.get("npc_name", inp.get("instance_id", "?")),
                                    })

                elif etype == "user":
                    content = event.get("message", {}).get("content", [])
                    if isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict):
                                text = str(c.get("content", "") or c.get("text", ""))
                                if _DEAD_RE.search(text):
                                    stats["deaths"] += 1
                                m = _LEVEL_RE.search(text)
                                if m:
                                    lvl = int(m.group(1))
                                    if 0 < lvl < 200:
                                        if stats["level_start"] is None:
                                            stats["level_start"] = lvl
                                        stats["level_end"] = lvl
    except Exception as e:
        stats["errors"].append(str(e))
    return stats


def parse_opencode_log(path: Path) -> dict:
    """OpenCode JSONL shape (DeepSeek / Qwen / Grok / generic opencode)."""
    stats = _empty_stats(path)
    first_ts = None
    last_ts = None
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = event.get("timestamp")
                if isinstance(ts, (int, float)):
                    if first_ts is None:
                        first_ts = ts
                    last_ts = ts

                etype = event.get("type", "")
                part = event.get("part") or {}

                if etype == "tool_use":
                    state = part.get("state") or {}
                    if state.get("status") not in (None, "completed", "error"):
                        # Skip in-flight placeholders without input/output.
                        pass
                    name = part.get("tool") or "unknown"
                    # Normalise: OpenCode tool ids are bare ("kaetram_observe").
                    name = name.replace("kaetram_", "")
                    stats["tools"][name] += 1
                    stats["turns"] += 1

                    inp = state.get("input") or {}
                    if name in ("interact_npc", "talk_npc"):
                        stats["npc_interactions"].append({
                            "tool": name,
                            "npc": inp.get("npc_name", inp.get("instance_id", "?")),
                        })

                    out = state.get("output")
                    if isinstance(out, str) and out:
                        if _DEAD_RE.search(out):
                            stats["deaths"] += 1
                        m = _LEVEL_RE.search(out)
                        if m:
                            lvl = int(m.group(1))
                            if 0 < lvl < 200:
                                if stats["level_start"] is None:
                                    stats["level_start"] = lvl
                                stats["level_end"] = lvl
    except Exception as e:
        stats["errors"].append(str(e))

    if first_ts is not None and last_ts is not None and last_ts > first_ts:
        stats["duration_s"] = (last_ts - first_ts) / 1000.0
    return stats


def parse_session_log(path: Path) -> dict:
    shape = _peek_shape(path)
    stats = parse_opencode_log(path) if shape == "opencode" else parse_claude_log(path)
    stats["log_shape"] = shape
    # Filename timestamp (used as fallback ordering key).
    m = re.search(r"(\d{8})_(\d{6})", path.name)
    if m:
        d, t = m.group(1), m.group(2)
        stats["started_at"] = f"{d[:4]}-{d[4:6]}-{d[6:8]}T{t[:2]}:{t[2:4]}:{t[4:6]}"
    stats["tools"] = dict(stats["tools"])
    return stats


# ─────────────────────────── per-session cache ───────────────────────────

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


def _cached_parse(path: Path, cache: dict) -> dict:
    """Parse with mtime+size keyed cache. Closed sessions never reparse."""
    st = path.stat()
    key = str(path)
    sig = [int(st.st_mtime), int(st.st_size)]
    entry = cache.get(key)
    if entry and entry.get("sig") == sig:
        return entry["stats"]
    stats = parse_session_log(path)
    cache[key] = {"sig": sig, "stats": stats}
    return stats


# ─────────────────────────── meta files ───────────────────────────

def _read_json(path: Path) -> dict:
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_run_meta(run_dir: Path) -> dict:
    return _read_json(run_dir / "run.meta.json")


def _read_session_meta(log_path: Path) -> dict:
    meta_path = log_path.with_suffix(".meta.json")
    return _read_json(meta_path)


# ─────────────────────────── run grouping ───────────────────────────

def build_runs_for_agent(agent_dir: Path, cache: dict) -> tuple[list[dict], list[dict]]:
    """Return (runs, all_sessions) for one agent_N directory.

    Groups by run_*/ subdirectory (no more session-number-reset heuristic).
    """
    runs: list[dict] = []
    all_sessions: list[dict] = []

    runs_dir = agent_dir / "runs"
    if not runs_dir.exists():
        return runs, all_sessions

    for run_d in sorted(runs_dir.iterdir(), key=lambda p: p.name):
        if not run_d.is_dir() or not run_d.name.startswith("run_"):
            continue

        run_meta = _read_run_meta(run_d)
        # Cheap whole-run skip: if run.meta started_at is before floor and
        # newest log file is also before floor, skip entirely.
        log_files = sorted(run_d.glob("session_*.log"), key=lambda p: p.stat().st_mtime)
        if not log_files:
            continue
        if log_files[-1].stat().st_mtime < DATE_FLOOR_TS:
            continue

        sessions = []
        for lf in log_files:
            try:
                st = lf.stat()
            except OSError:
                continue
            if st.st_size < 1024:
                continue
            if st.st_mtime < DATE_FLOOR_TS:
                continue
            stats = _cached_parse(lf, cache)
            sm = _read_session_meta(lf)
            if sm:
                stats = dict(stats)
                stats["session_index"] = sm.get("session")
                stats["auth_mode"] = sm.get("auth_mode")
                # Prefer meta-declared model over log-derived.
                if sm.get("model"):
                    stats["model"] = sm["model"]
                if sm.get("harness"):
                    stats["harness"] = sm["harness"]
            sessions.append(stats)

        if not sessions:
            continue
        all_sessions.extend(sessions)

        total_turns = sum(s["turns"] for s in sessions)
        total_deaths = sum(s["deaths"] for s in sessions)
        total_duration = sum(s.get("duration_s", 0) for s in sessions)

        level_start = next((s["level_start"] for s in sessions if s.get("level_start") is not None), None)
        level_end = next((s["level_end"] for s in reversed(sessions) if s.get("level_end") is not None), None)

        run_tools: Counter = Counter()
        for s in sessions:
            for tool, count in s["tools"].items():
                run_tools[tool] += count

        run_npcs = []
        for s in sessions:
            run_npcs.extend(s.get("npc_interactions", []))

        # Resolve identity from run.meta with fallback to first session.
        harness = run_meta.get("harness") or sessions[0].get("harness", "")
        model = run_meta.get("model") or sessions[0].get("model", "")
        username = run_meta.get("username", "")
        personality = run_meta.get("personality", "")
        started_at = run_meta.get("started_at") or sessions[0].get("started_at", "")
        ended_at = sessions[-1].get("started_at", "")

        runs.append({
            "run_id": run_d.name,
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
            "level_start": level_start,
            "level_end": level_end,
            "level_gain": (level_end or 0) - (level_start or 0),
            "tool_usage": dict(run_tools.most_common(15)),
            "npc_interactions": len(run_npcs),
            "npcs_talked_to": sorted({n["npc"] for n in run_npcs}),
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
                    "top_tools": dict(Counter(s["tools"]).most_common(5)),
                }
                for s in sessions
            ],
        })

    return runs, all_sessions


# ─────────────────────────── mongo (meta-driven) ───────────────────────────

def collect_known_usernames() -> dict[str, dict]:
    """Walk run.meta.json files; return {username: {harness, model}}."""
    out: dict[str, dict] = {}
    if not RAW_DIR.exists():
        return out
    for meta in RAW_DIR.glob("agent_*/runs/run_*/run.meta.json"):
        m = _read_json(meta)
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
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "date_range": f"{DATE_FLOOR.date().isoformat()} to present",
        "description": (
            "Kaetram AI Agent training data. Multi-harness (Claude + OpenCode "
            "models: DeepSeek V4, Qwen, Grok). Each 'run' is a fresh Level 1 "
            "start (restart-agent.sh resets DB). Runs contain multiple sessions."
        ),
    }

    agent_data: dict[str, dict] = {}
    all_tools: Counter = Counter()
    harness_breakdown: dict[str, dict] = {}
    model_breakdown: dict[str, dict] = {}
    total_turns = 0
    total_deaths = 0
    total_sessions_count = 0

    if RAW_DIR.exists():
        for agent_dir in sorted(RAW_DIR.glob("agent_*")):
            runs, sessions = build_runs_for_agent(agent_dir, cache)
            if not sessions:
                continue
            for s in sessions:
                for tool, count in s["tools"].items():
                    all_tools[tool] += count
                total_turns += s["turns"]
                total_deaths += s["deaths"]
            total_sessions_count += len(sessions)

            for r in runs:
                h = r.get("harness") or "unknown"
                hb = harness_breakdown.setdefault(h, {"runs": 0, "sessions": 0, "turns": 0, "deaths": 0})
                hb["runs"] += 1
                hb["sessions"] += r["sessions"]
                hb["turns"] += r["total_turns"]
                hb["deaths"] += r["total_deaths"]

                mdl = r.get("model") or "unknown"
                mb = model_breakdown.setdefault(mdl, {"harness": h, "runs": 0, "sessions": 0, "turns": 0, "deaths": 0})
                mb["runs"] += 1
                mb["sessions"] += r["sessions"]
                mb["turns"] += r["total_turns"]
                mb["deaths"] += r["total_deaths"]

            agent_data[agent_dir.name] = {"runs": runs, "session_count": len(sessions)}

    report["overview"] = {
        "total_sessions": total_sessions_count,
        "total_runs": sum(len(d["runs"]) for d in agent_data.values()),
        "total_turns": total_turns,
        "total_deaths": total_deaths,
        "agents": list(agent_data.keys()),
    }

    report["harness_breakdown"] = harness_breakdown
    report["model_breakdown"] = model_breakdown

    report["agents"] = {}
    for agent_name, data in agent_data.items():
        runs = data["runs"]
        best = max(runs, key=lambda r: r.get("level_end") or 0) if runs else None
        report["agents"][agent_name] = {
            "total_sessions": data["session_count"],
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
    print(f"Exported {OUTPUT} ({size_kb:.1f} KB)")
    print(f"  Schema:   v{report['schema_version']}")
    print(f"  Range:    {report['date_range']}")
    print(f"  Runs:     {report['overview']['total_runs']}")
    print(f"  Sessions: {report['overview']['total_sessions']}")
    print(f"  Turns:    {report['overview']['total_turns']}")
    print(f"  Deaths:   {report['overview']['total_deaths']}")
    print(f"  Harnesses:{list(report['harness_breakdown'].keys())}")
    print(f"  Models:   {list(report['model_breakdown'].keys())}")


if __name__ == "__main__":
    main()
