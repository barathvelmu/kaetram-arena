"""API endpoint methods for the dashboard HTTP handler.

These are mixed into DashboardHandler via APIMixin to keep the handler module small.
"""

import json
import os
import glob
import time
from datetime import datetime

from dashboard.constants import (
    PROJECT_DIR, STATE_DIR, LOG_DIR, DATASET_DIR,
    BASE_SERVER_PORT, PORT_STRIDE, MAX_AGENTS,
    sanitize, get_ss_output, check_process_running,
)
from dashboard.parsers import parse_session_log, quick_session_summary, live_session_stats
from dashboard.game_state import extract_game_state_from_db


_agents_cache = {"data": None, "time": 0}
_AGENTS_CACHE_TTL = 5  # seconds — avoid re-parsing logs and probing ports every 2s


class APIMixin:
    """API endpoint methods mixed into DashboardHandler."""

    def send_json_state(self, qs=None):
        self._send_json({})

    def send_game_state(self, qs=None):
        state_dir = self._resolve_state_dir(qs)
        data = {}
        freshness = -1

        # Priority 1: Direct MongoDB query (authoritative, fast)
        agent_id = qs.get("agent", [None])[0] if qs else None
        # Read username from metadata.json for correct DB lookup (supports Codex agents)
        username = None
        if agent_id is not None:
            metadata_file = os.path.join("/tmp", f"kaetram_agent_{agent_id}", "metadata.json")
            if os.path.isfile(metadata_file):
                try:
                    with open(metadata_file) as mf:
                        meta = json.load(mf)
                    username = meta.get("username", "").lower()
                except Exception:
                    pass
            if not username:
                username = f"claudebot{agent_id}"
        else:
            username = "claudebot0"  # default single-agent
        db_state = extract_game_state_from_db(username)

        # Priority 1: game_state.json (written by MCP observe — live volatile state)
        gs_file = os.path.join(state_dir, "game_state.json")
        if os.path.isfile(gs_file):
            try:
                mtime = os.path.getmtime(gs_file)
                age = time.time() - mtime
                if age < 120:  # fresh within 2 minutes
                    with open(gs_file) as fh:
                        live = json.load(fh)
                    freshness = round(age, 1)
                    if db_state:
                        # Merge: DB has quests/skills/equipment, file has live position/HP/entities
                        data = db_state
                        for k in ("player_stats", "player_position", "nearby_entities",
                                  "nearest_mob", "current_target", "player_count_nearby",
                                  "navigation", "last_combat", "last_xp_event", "ui_state"):
                            if k in live:
                                data[k] = live[k]
                    else:
                        data = live
            except Exception:
                pass

        # Priority 2: DB-only (no live file, or stale)
        if not data and db_state:
            data = db_state
            freshness = -1

        data["freshness_seconds"] = freshness
        self._send_json(data)

    def send_prompt(self):
        prompt_file = os.path.join(PROJECT_DIR, "prompts", "system.md")
        text = ""
        if os.path.isfile(prompt_file):
            try:
                with open(prompt_file) as fh:
                    text = fh.read()
            except Exception:
                text = "(error reading file)"

        # Read game knowledge
        gk_file = os.path.join(PROJECT_DIR, "prompts", "game_knowledge.md")
        game_knowledge = ""
        if os.path.isfile(gk_file):
            try:
                with open(gk_file) as fh:
                    game_knowledge = fh.read()
            except Exception:
                pass

        # Read personality files
        personalities = {}
        pdir = os.path.join(PROJECT_DIR, "prompts", "personalities")
        if os.path.isdir(pdir):
            for name in ("aggressive", "methodical", "curious", "efficient"):
                pfile = os.path.join(pdir, f"{name}.md")
                if os.path.isfile(pfile):
                    try:
                        with open(pfile) as fh:
                            personalities[name] = sanitize(fh.read())
                    except Exception:
                        pass

        self._send_json({
            "content": sanitize(text),
            "file": "prompts/system.md",
            "game_knowledge": sanitize(game_knowledge),
            "personalities": personalities,
        })

    def send_session_log(self):
        log_file = os.path.join(PROJECT_DIR, "session_log.md")
        text = ""
        if os.path.isfile(log_file):
            try:
                with open(log_file) as fh:
                    text = fh.read()
            except Exception:
                text = "(error reading file)"
        self._send_json({"content": sanitize(text), "file": "session_log.md"})

    def send_session_detail(self, name, log_dir=None):
        if not name:
            return self._send_json({"error": "missing name param"})
        safe = os.path.basename(name)
        if log_dir:
            # Validate log_dir is an allowed path
            allowed_dirs = [LOG_DIR]
            for i in range(MAX_AGENTS):
                allowed_dirs.append(os.path.join(DATASET_DIR, "raw", f"agent_{i}", "logs"))
            resolved = os.path.realpath(log_dir)
            if not any(os.path.realpath(d) == resolved for d in allowed_dirs):
                return self._send_json({"error": "invalid log directory"})
            filepath = os.path.join(resolved, safe)
        else:
            filepath = os.path.join(LOG_DIR, safe)
        if not os.path.isfile(filepath):
            return self._send_json({"error": "not found"})

        parsed = parse_session_log(filepath)
        parsed["name"] = safe
        self._send_json(parsed)

    # ── Dataset stats ──

    def send_dataset_stats(self):
        stats = {"raw_sessions": 0, "raw_total_size": 0}
        if os.path.isdir(DATASET_DIR):
            raw_dir = os.path.join(DATASET_DIR, "raw")
            if os.path.isdir(raw_dir):
                raw_logs = glob.glob(os.path.join(raw_dir, "agent_*", "logs", "session_*.log"))
                stats["raw_sessions"] = len(raw_logs)
                stats["raw_total_size"] = sum(os.path.getsize(f) for f in raw_logs)
        self._send_json(stats)

    def send_sft_stats(self):
        """SFT pipeline output stats: extracted turns + Qwen3.5 SFT records."""
        stats = {"extracted": {"files": 0, "total_turns": 0}, "qwen_sft": {"train": 0, "val": 0, "total": 0}}

        extracted_dir = os.path.join(DATASET_DIR, "extracted")
        if os.path.isdir(extracted_dir):
            turns_files = glob.glob(os.path.join(extracted_dir, "**", "turns.jsonl"), recursive=True)
            total_turns = 0
            for tf in turns_files:
                try:
                    with open(tf) as fh:
                        total_turns += sum(1 for line in fh if line.strip())
                except Exception:
                    pass
            stats["extracted"] = {"files": len(turns_files), "total_turns": total_turns}

        qwen_dir = os.path.join(DATASET_DIR, "qwen_sft")
        train_file = os.path.join(qwen_dir, "train.json")
        val_file = os.path.join(qwen_dir, "val.json")
        if os.path.isfile(train_file):
            try:
                train_count = len(json.load(open(train_file)))
                val_count = len(json.load(open(val_file))) if os.path.isfile(val_file) else 0
                stats["qwen_sft"] = {"train": train_count, "val": val_count, "total": train_count + val_count}
            except Exception:
                pass

        self._send_json(stats)

    # ── Raw file viewer ──

    def send_raw_file(self, which, qs=None):
        state_dir = self._resolve_state_dir(qs)
        allowed = {
            "game_state": os.path.join(state_dir, "game_state.json"),
            "session_log": os.path.join(PROJECT_DIR, "session_log.md"),
            "claude_md": os.path.join(PROJECT_DIR, "CLAUDE.md"),
            "state_extractor": os.path.join(PROJECT_DIR, "state_extractor.js"),
            "orchestrate": os.path.join(PROJECT_DIR, "orchestrate.py"),
        }
        path = allowed.get(which)
        if not path or not os.path.isfile(path):
            return self._send_json({"error": "not found", "allowed": list(allowed.keys())})
        try:
            with open(path) as fh:
                content = fh.read()
            self._send_json({"file": which, "path": path, "content": sanitize(content), "size": len(content)})
        except Exception as e:
            self._send_json({"error": str(e)})

    # ── Live status (multi-agent aware) ──

    def send_live_status(self):
        mode = "none"
        agent_count = 0
        # Use /proc scan instead of subprocess fork
        if check_process_running("python3 orchestrate.py"):
            mode = "multi"
            for j in range(MAX_AGENTS):
                meta_file = os.path.join("/tmp", f"kaetram_agent_{j}", "metadata.json")
                if os.path.isfile(meta_file):
                    try:
                        with open(meta_file) as mf:
                            meta = json.load(mf)
                        if meta.get("personality") != "qwen":
                            agent_count += 1
                    except Exception:
                        pass
        if mode == "none" and check_process_running("play.sh"):
            mode = "single"
            agent_count = 1

        agent_running = mode != "none"

        gs_fresh = False
        game_state_age = -1

        screenshot_age = -1
        screenshot_time = ""
        if mode == "multi":
            for i in range(MAX_AGENTS):
                for ss_name in ("live_screen.jpg", "screenshot.png"):
                    ss = os.path.join("/tmp", f"kaetram_agent_{i}", "state", ss_name)
                    if os.path.isfile(ss):
                        mtime = os.path.getmtime(ss)
                        age = int(time.time() - mtime)
                        if screenshot_age < 0 or age < screenshot_age:
                            screenshot_age = age
                            screenshot_time = datetime.fromtimestamp(mtime).strftime("%H:%M:%S")
                        break
        else:
            screenshot = os.path.join(STATE_DIR, "live_screen.jpg")
            if not os.path.isfile(screenshot):
                screenshot = os.path.join(STATE_DIR, "screenshot.png")
            if os.path.isfile(screenshot):
                mtime = os.path.getmtime(screenshot)
                screenshot_age = int(time.time() - mtime)
                screenshot_time = datetime.fromtimestamp(mtime).strftime("%H:%M:%S")

        # Use cached ss output (shared 5s TTL)
        ss_out = get_ss_output()
        active_ports = []
        game_server_up = ":9000" in ss_out
        for i in range(MAX_AGENTS):
            port = BASE_SERVER_PORT + i * PORT_STRIDE
            if f":{port}" in ss_out:
                active_ports.append(port)
        if not game_server_up and active_ports:
            game_server_up = True

        single_sessions = len(glob.glob(os.path.join(LOG_DIR, "session_*.log")))
        multi_sessions = len(glob.glob(os.path.join(DATASET_DIR, "raw", "agent_*", "logs", "session_*.log")))
        total_sessions = single_sessions + multi_sessions

        self._send_json({
            "mode": mode,
            "agent_running": agent_running,
            "agent_count": agent_count,
            "game_state_fresh": gs_fresh,
            "game_server_up": game_server_up,
            "active_ports": active_ports,
            "game_state_age_seconds": game_state_age,
            "screenshot_age_seconds": screenshot_age,
            "screenshot_time": screenshot_time,
            "total_sessions": total_sessions,
            "single_sessions": single_sessions,
            "multi_sessions": multi_sessions,
        })

    # ── Multi-agent endpoint ──

    def send_agents(self):
        # Cache agent data to avoid re-parsing logs and probing ports every 2s
        now = time.time()
        if _agents_cache["data"] is not None and now - _agents_cache["time"] < _AGENTS_CACHE_TTL:
            return self._send_json(_agents_cache["data"])

        # Use cached ss output (shared 5s TTL, no subprocess fork)
        ss_out = get_ss_output()
        listening_ports = set()
        for line in ss_out.splitlines():
            for i in range(MAX_AGENTS):
                port = BASE_SERVER_PORT + i * PORT_STRIDE
                if f":{port}" in line:
                    listening_ports.add(port)

        agents = []
        for i in range(MAX_AGENTS):
            sandbox = os.path.join("/tmp", f"kaetram_agent_{i}")
            if not os.path.isdir(sandbox):
                continue
            # Only show agents that were launched by orchestrator (have metadata.json)
            if not os.path.isfile(os.path.join(sandbox, "metadata.json")):
                continue
            state_dir = os.path.join(sandbox, "state")
            agent = {"id": i, "username": f"Agent{i}", "server_port": BASE_SERVER_PORT + i * PORT_STRIDE}

            metadata_file = os.path.join(sandbox, "metadata.json")
            default_models = {
                "claude": "sonnet",
                "codex": "gpt-5.4",
                "gemini": "gemini-2.5-flash",
                "kimi": "kimi-k2",
                "qwen-code": "qwen3-coder",
            }
            if os.path.isfile(metadata_file):
                try:
                    with open(metadata_file) as mf:
                        meta = json.load(mf)
                    agent["mode"] = meta.get("personality", meta.get("mode", "efficient"))
                    agent["harness"] = meta.get("harness", "claude")
                    agent["harness_model"] = meta.get("model") or default_models.get(agent["harness"], "")
                    if meta.get("username"):
                        agent["username"] = meta["username"]
                except Exception:
                    agent["mode"] = "efficient"
                    agent["harness"] = "claude"
                    agent["harness_model"] = default_models.get("claude", "")
            else:
                agent["mode"] = "efficient"
                agent["harness"] = "claude"
                agent["harness_model"] = default_models.get("claude", "")

            if agent["mode"] == "qwen":
                continue

            for ss_name in ("live_screen.jpg", "screenshot.png"):
                ss = os.path.join(state_dir, ss_name)
                if os.path.isfile(ss):
                    agent["screenshot_age"] = int(time.time() - os.path.getmtime(ss))
                    break

            # Use ss port check instead of raw TCP probe (avoids TIME-WAIT flood on game servers)
            agent["server_healthy"] = agent["server_port"] in listening_ports

            log_dir = os.path.join(DATASET_DIR, "raw", f"agent_{i}", "logs")
            agent["log_dir"] = log_dir
            if os.path.isdir(log_dir):
                logs = glob.glob(os.path.join(log_dir, "session_*.log"))
                agent["session_count"] = len(logs)
                if logs:
                    latest = max(logs, key=os.path.getmtime)
                    agent["last_active"] = int(time.time() - os.path.getmtime(latest))
                    live = live_session_stats(latest)
                    agent["latest_cost"] = live["cost_usd"]
                    agent["latest_model"] = live["model"]
                    agent["turns"] = live["turns"]
                    agent["context_tokens"] = live["context_tokens"]
                    agent["output_tokens"] = live["output_tokens"]
                    # Game state from file (written by MCP observe — cheap read)
                    gs_file = os.path.join(state_dir, "game_state.json")
                    if os.path.isfile(gs_file):
                        try:
                            with open(gs_file) as gf:
                                agent["game_state"] = json.load(gf)
                        except Exception:
                            pass
            else:
                agent["session_count"] = 0

            agents.append(agent)

        _agents_cache["data"] = agents
        _agents_cache["time"] = now
        self._send_json(agents)

    # ── Qwen agent log endpoint ──

    # Incremental log reader cache: {filepath: (file_offset, entries_list)}
    _qwen_log_cache: dict = {}

    def send_qwen_log(self, agent_id: int = 4):
        """Return latest log entries + state from a Qwen agent sandbox.

        Only returns entries from actively running agents. If the agent is
        stopped (no log file modified in last 120s), returns empty entries
        and clears the cache to prevent stale logs from persisting.
        """
        import glob as _glob
        sandbox = f"/tmp/kaetram_agent_{agent_id}"
        state_dir = os.path.join(sandbox, "state")
        log_dir = os.path.join(sandbox, "logs")

        result = {"entries": [], "screenshot_age": 9999, "game_state": {}, "memory": {},
                  "agent_id": agent_id}

        # Screenshot age (check both jpg and png for compat)
        for ss_name in ("live_screen.jpg", "live_screen.png"):
            ss = os.path.join(state_dir, ss_name)
            if os.path.isfile(ss):
                result["screenshot_age"] = time.time() - os.path.getmtime(ss)
                break

        # Game state — only show if recent (< 120s)
        gs_path = os.path.join(state_dir, "game_state.json")
        if os.path.isfile(gs_path):
            gs_age = time.time() - os.path.getmtime(gs_path)
            if gs_age < 120:
                try:
                    with open(gs_path) as f:
                        result["game_state"] = json.load(f)
                except Exception:
                    pass

        # Parse latest log file (incremental — seek to last read position)
        if os.path.isdir(log_dir):
            logs = sorted(_glob.glob(os.path.join(log_dir, "*.log")), key=os.path.getmtime)
            if logs:
                log_path = logs[-1]
                log_age = time.time() - os.path.getmtime(log_path)

                # If latest log is stale (>120s), agent is stopped — clear cache, return empty
                if log_age > 120:
                    APIMixin._qwen_log_cache.pop(agent_id, None)
                    self._send_json(result)
                    return

                cache = APIMixin._qwen_log_cache
                cached_path, offset, entries = cache.get(agent_id, (None, 0, []))
                # Reset cache if log file changed (new session)
                if cached_path != log_path:
                    offset, entries = 0, []
                try:
                    with open(log_path) as f:
                        f.seek(offset)
                        for line in f:
                            try:
                                entries.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
                        cache[agent_id] = (log_path, f.tell(), entries)
                except Exception:
                    pass
                result["entries"] = entries[-200:]

        self._send_json(result)

    # ── Activity feed (multi-agent aware) ──

    def send_activity(self, qs=None):
        agent_id = qs.get("agent", [None])[0] if qs else None
        if agent_id is not None:
            log_dir = os.path.join(DATASET_DIR, "raw", f"agent_{agent_id}", "logs")
        else:
            log_dir = LOG_DIR

        logs = sorted(glob.glob(os.path.join(log_dir, "session_*.log")), key=os.path.getmtime)
        if not logs:
            return self._send_json({"events": [], "turn": 0, "cost_usd": 0})

        latest = logs[-1]
        parsed = parse_session_log(latest)
        parsed["log_file"] = os.path.basename(latest)
        self._send_json(parsed)

    # ── Sessions list (multi-agent aware) ──

    def send_sessions(self, qs=None):
        source = qs.get("source", ["single"])[0] if qs else "single"
        agent_filter = qs.get("agent", [None])[0] if qs else None

        entries = []
        if source == "multi" or source == "all":
            raw_dir = os.path.join(DATASET_DIR, "raw")
            if os.path.isdir(raw_dir):
                if agent_filter is not None:
                    dirs = [os.path.join(raw_dir, f"agent_{agent_filter}", "logs")]
                else:
                    dirs = sorted(glob.glob(os.path.join(raw_dir, "agent_*", "logs")))
                for d in dirs:
                    if not os.path.isdir(d):
                        continue
                    agent_name = os.path.basename(os.path.dirname(d))
                    for log in sorted(glob.glob(os.path.join(d, "*.log")), key=os.path.getmtime, reverse=True)[:20]:
                        name = os.path.basename(log)
                        size = os.path.getsize(log)
                        mtime = datetime.fromtimestamp(os.path.getmtime(log)).strftime("%Y-%m-%d %H:%M:%S")
                        summary = quick_session_summary(log)
                        entries.append({
                            "name": name, "time": mtime, "size": size,
                            "agent": agent_name, "log_dir": d,
                            **summary,
                        })

        if source == "single" or source == "all":
            for log in sorted(glob.glob(os.path.join(LOG_DIR, "*.log")), key=os.path.getmtime, reverse=True)[:50]:
                name = os.path.basename(log)
                size = os.path.getsize(log)
                mtime = datetime.fromtimestamp(os.path.getmtime(log)).strftime("%Y-%m-%d %H:%M:%S")
                summary = quick_session_summary(log)
                entries.append({
                    "name": name, "time": mtime, "size": size,
                    "agent": "single", "log_dir": LOG_DIR,
                    **summary,
                })

        entries.sort(key=lambda e: e["time"], reverse=True)
        self._send_json(entries[:50])

    # ── Eval results ──

    _eval_cache = {"data": None, "mtime": 0}

    def send_eval_latest(self):
        """Return latest eval comparison results from dataset/eval/."""
        eval_dir = os.path.join(DATASET_DIR, "eval")
        if not os.path.isdir(eval_dir):
            return self._send_json({"status": "no_eval_data", "models": []})

        # Find all results.json files
        results_files = sorted(
            glob.glob(os.path.join(eval_dir, "*/results.json")),
            key=os.path.getmtime, reverse=True,
        )
        if not results_files:
            return self._send_json({"status": "no_eval_data", "models": []})

        # Check cache freshness
        newest_mtime = max(os.path.getmtime(f) for f in results_files)
        if APIMixin._eval_cache["data"] and APIMixin._eval_cache["mtime"] >= newest_mtime:
            return self._send_json(APIMixin._eval_cache["data"])

        # Load all model results
        models = []
        for rf in results_files:
            try:
                with open(rf) as f:
                    data = json.load(f)
                models.append(data)
            except (json.JSONDecodeError, OSError):
                continue

        if not models:
            return self._send_json({"status": "no_eval_data", "models": []})

        # Build comparison if we have 2+ models
        comparison = {"status": "ok", "models": []}
        base_data = None
        for m in models:
            meta = m.get("meta", {})
            metrics = m.get("metrics", {})
            episodes = m.get("episodes", [])
            ok_eps = [e for e in episodes if e.get("status") == "ok"]

            # Aggregate action counts across episodes
            action_totals = {}
            for ep in ok_eps:
                for tool, count in ep.get("action_counts", {}).items():
                    action_totals[tool] = action_totals.get(tool, 0) + count

            # Per-episode summary for drill-down
            episode_summaries = []
            for ep in ok_eps:
                episode_summaries.append({
                    "episode": ep.get("episode", 0),
                    "kills": ep.get("kills", 0),
                    "kills_by_mob": ep.get("kills_by_mob", {}),
                    "xp_estimated": ep.get("xp_estimated", 0),
                    "level_reached": ep.get("level_reached", 1),
                    "deaths": ep.get("deaths", 0),
                    "quests_completed": ep.get("quests_completed", 0),
                    "quests_accepted": ep.get("quests_accepted", 0),
                    "unique_positions": ep.get("unique_positions", 0),
                    "turns_played": ep.get("turns_played", 0),
                    "sub_sessions": ep.get("sub_sessions", 0),
                    "duration_seconds": ep.get("duration_seconds", 0),
                    "action_entropy": ep.get("action_entropy", 0),
                    "tool_parse_rate": ep.get("tool_parse_rate", 0),
                    "click_tiles": ep.get("click_tiles", 0),
                    "stuck_resets": ep.get("stuck_resets", 0),
                })

            model_summary = {
                "name": meta.get("model", "unknown"),
                "scenario": meta.get("scenario", "?"),
                "total_episodes": meta.get("total_episodes", 0),
                "ok_episodes": meta.get("ok_episodes", 0),
                "timestamp": meta.get("timestamp", ""),
                "metrics": {},
                "action_distribution": action_totals,
                "episodes": episode_summaries,
            }

            # Compute per-metric summaries
            all_metric_keys = [
                "tool_parse_rate", "quest_completion_rate", "xp_per_turn",
                "survival_rate", "deaths_per_session",
                "kills", "xp_estimated", "level_reached", "level_delta",
                "action_entropy", "success_rate", "stuck_resets", "click_tiles",
            ]
            for key in all_metric_keys:
                vals = metrics.get(key, [])
                if vals:
                    mean = sum(vals) / len(vals)
                    model_summary["metrics"][key] = {
                        "mean": round(mean, 4),
                        "values": vals,
                        "n": len(vals),
                    }

            comparison["models"].append(model_summary)
            if meta.get("model") == "base":
                base_data = model_summary

        # Compute pairwise stats if base + treatment exist
        if base_data and len(comparison["models"]) >= 2:
            import math
            comparison["comparisons"] = []
            for m in comparison["models"]:
                if m["name"] == "base":
                    continue
                tier1 = []
                tier1_metrics = [
                    ("tool_parse_rate", "Tool Parse Rate", "higher"),
                    ("quest_completion_rate", "Quest Completion", "higher"),
                    ("xp_per_turn", "XP per Turn", "higher"),
                    ("survival_rate", "Survival Rate", "higher"),
                    ("deaths_per_session", "Deaths/Session", "lower"),
                ]
                for key, label, direction in tier1_metrics:
                    bv = base_data["metrics"].get(key, {}).get("values", [])
                    tv = m["metrics"].get(key, {}).get("values", [])
                    if not bv or not tv:
                        continue
                    b_mean = sum(bv) / len(bv)
                    t_mean = sum(tv) / len(tv)
                    # Glass's delta
                    # Glass's delta — suppress when N < 3 (not enough data for meaningful SD)
                    if len(bv) >= 3:
                        b_sd = math.sqrt(sum((x - b_mean)**2 for x in bv) / (len(bv)-1))
                        g_delta = (t_mean - b_mean) / max(b_sd, 0.001)
                    else:
                        g_delta = 0.0  # Not enough data
                    if direction == "lower":
                        g_delta = -g_delta
                    tier1.append({
                        "key": key, "label": label, "direction": direction,
                        "base_mean": round(b_mean, 4), "treat_mean": round(t_mean, 4),
                        "delta": round(g_delta, 2),
                    })
                comparison["comparisons"].append({
                    "base": "base", "treatment": m["name"], "tier1": tier1,
                })

        APIMixin._eval_cache = {"data": comparison, "mtime": newest_mtime}
        self._send_json(comparison)

    # ── Eval live status (running eval sessions) ──

    def send_eval_live(self):
        """Return live status from running eval sandboxes (/tmp/kaetram_eval_*)."""
        import glob as _glob
        models = {}
        for sandbox_dir in sorted(_glob.glob("/tmp/kaetram_eval_*")):
            model_name = os.path.basename(sandbox_dir).replace("kaetram_eval_", "")
            state_dir = os.path.join(sandbox_dir, "state")
            log_dir = os.path.join(sandbox_dir, "logs")

            model = {"name": model_name, "active": False, "entries": [],
                     "game_state": {}, "screenshot_age": 9999, "episode": 0, "turn": 0}

            # Screenshot age
            ss_path = os.path.join(state_dir, "live_screen.jpg")
            if os.path.isfile(ss_path):
                model["screenshot_age"] = time.time() - os.path.getmtime(ss_path)
                model["active"] = model["screenshot_age"] < 120

            # Game state (longer staleness window for eval — turns can be slow)
            gs_path = os.path.join(state_dir, "game_state.json")
            if os.path.isfile(gs_path) and (time.time() - os.path.getmtime(gs_path)) < 600:
                try:
                    with open(gs_path) as f:
                        model["game_state"] = json.load(f)
                except Exception:
                    pass

            # Latest session log entries (read all sub-session logs for current episode)
            if os.path.isdir(log_dir):
                logs = sorted(_glob.glob(os.path.join(log_dir, "*.log")), key=os.path.getmtime)
                if logs:
                    model["sub_sessions"] = len(logs)
                    # Read entries from ALL sub-session logs (they accumulate within one episode)
                    all_entries = []
                    try:
                        for lp in logs:
                            with open(lp) as f:
                                for line in f:
                                    try:
                                        all_entries.append(json.loads(line))
                                    except json.JSONDecodeError:
                                        continue
                        model["entries"] = all_entries[-100:]
                        model["turn"] = len([e for e in all_entries if e.get("role") == "assistant"])
                        # Compute cumulative stats across ALL entries (not just last 100)
                        cum_kills = 0
                        cum_tools = 0
                        cum_errors = 0
                        for e in all_entries:
                            if e.get("role") == "tool":
                                c = e.get("content", "")
                                cum_tools += 1
                                if '"error"' in c:
                                    cum_errors += 1
                                if '"killed": true' in c or '"killed":true' in c:
                                    cum_kills += 1
                        model["cumulative"] = {"kills": cum_kills, "tools": cum_tools, "errors": cum_errors}
                    except Exception:
                        pass

            # Completed episode count from results.json
            results_path = os.path.join(DATASET_DIR, "eval", model_name, "results.json")
            if os.path.isfile(results_path):
                try:
                    with open(results_path) as f:
                        rd = json.load(f)
                    ok = [e for e in rd.get("episodes", []) if e.get("status") == "ok"]
                    model["completed_episodes"] = len(ok)
                    model["total_episodes"] = rd.get("meta", {}).get("total_episodes", 0)
                except Exception:
                    pass

            models[model_name] = model

        self._send_json({"models": models, "eval_running": any(m["active"] for m in models.values())})
