#!/usr/bin/env python3
"""Comprehensive log analyzer for Kaetram agent runs.

Usage:
    python3 scripts/log_analysis/analyze.py                  # full report (default)
    python3 scripts/log_analysis/analyze.py status           # one-line per agent (latest run)
    python3 scripts/log_analysis/analyze.py runs [-n 10]     # recent runs across all agents
    python3 scripts/log_analysis/analyze.py timeline [-n 30] # chronological event stream (latest run)
    python3 scripts/log_analysis/analyze.py tier_a           # Tier-A adoption metrics per agent
    python3 scripts/log_analysis/analyze.py quests           # quest-focused detail
    python3 scripts/log_analysis/analyze.py tools            # tool call breakdown
    python3 scripts/log_analysis/analyze.py recent [-n 10]   # last N tool calls per agent
    python3 scripts/log_analysis/analyze.py errors           # categorized errors + next-action transitions
    python3 scripts/log_analysis/analyze.py thinking [-n 5]  # last N thinking blocks per agent
    python3 scripts/log_analysis/analyze.py agent <N>        # deep-dive single agent

Defaults to LATEST session log per agent. Pass `--run <run_id>` to scope to a
specific run, or `--all-runs` to span the entire history (heavy commands only).
`--stale` includes agents whose log is >10 min old (default: only currently
running).
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from scripts.log_analysis.parse import (  # noqa: E402
    SessionView,
    categorize_error,
    deaths,
    first_observe,
    fmt_duration,
    fmt_est,
    latest_logs_per_agent,
    latest_observe,
    latest_runs_per_agent,
    parse_run,
    parse_session_auto,
    parse_session_timestamp,
    runs_per_agent,
    tier_a_signals,
    tool_call_counts,
    tool_error_counts,
)


# ── Formatting helpers ──────────────────────────────────────────────────────

def _fmt_pos(p: dict | None) -> str:
    if not isinstance(p, dict):
        return "?"
    return f"({p.get('x','?')},{p.get('y','?')})"


def _fmt_quests(qs: list | None) -> str:
    if not qs:
        return "—"
    parts = []
    for q in qs:
        if not isinstance(q, dict):
            continue
        name = q.get("name", "?")
        stage = q.get("stage")
        sc = q.get("stage_count")
        if stage is not None and sc is not None:
            parts.append(f"{name} {stage}/{sc}")
        else:
            parts.append(name)
    return ", ".join(parts) if parts else "—"


def _age(path: Path) -> str:
    age_s = dt.datetime.now().timestamp() - path.stat().st_mtime
    if age_s < 60:
        return f"{int(age_s)}s ago"
    if age_s < 3600:
        return f"{int(age_s/60)}m ago"
    return f"{age_s/3600:.1f}h ago"


def _agent_id(agent_dir: Path) -> str:
    return agent_dir.name.replace("agent_", "")


_ARCH_ABBREV = {
    "grinder": "grinder",
    "completionist": "complet",
    "explorer_tinkerer": "explor",
    "explorer": "explor",
}


def _personality(sv: SessionView, short: bool = False) -> str:
    p = sv.meta.get("personality", "?")
    return _ARCH_ABBREV.get(p, p[:10]) if short else p


def _bar(label: str) -> str:
    return f"\n{'─' * 4} {label} {'─' * max(0, 70 - len(label))}\n"


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_status(views: list[tuple[Path, SessionView]]) -> None:
    """One-line per agent — the quickest possible health check.

    Header line shows the run_id + EST start time so we know which run we're
    looking at when comparing across days.
    """
    # Header context: pull run.meta from each agent's latest run.
    runs = latest_runs_per_agent()
    if runs:
        primary = runs[0]
        print(f"  Run: {primary.run_id}   started: {fmt_est(primary.started_at)}   "
              f"elapsed: {fmt_duration(primary.duration_s)}")
        print()
    print(f"{'agent':<6}{'arch':<9}{'lvl':<5}{'hp':<11}{'pos':<13}"
          f"{'turns':<7}{'errs':<6}{'rl':<4}finished | active")
    print("─" * 110)
    for agent_dir, sv in views:
        aid = _agent_id(agent_dir)
        arch = _personality(sv, short=True)
        gs = latest_observe(sv) or {}
        stats = gs.get("stats", {}) or {}
        lvl = stats.get("level", "?")
        hp = f"{stats.get('hp','?')}/{stats.get('max_hp','?')}"
        pos = _fmt_pos(gs.get("pos"))
        turns = sv.n_assistant
        err_total = sum(1 for tc in sv.tool_calls if tc.is_error)
        rl = sv.rate_limit_events
        finished = ", ".join(q.get("name", "?") for q in (gs.get("finished_quests") or []) if isinstance(q, dict)) or "—"
        active = _fmt_quests(gs.get("active_quests"))
        print(f"{aid:<6}{arch:<14}{str(lvl):<5}{hp:<11}{pos:<13}"
              f"{str(turns):<7}{str(err_total):<6}{str(rl):<4}{finished} | {active}")


def cmd_quests(views: list[tuple[Path, SessionView]]) -> None:
    """Quest progression detail per agent."""
    for agent_dir, sv in views:
        aid = _agent_id(agent_dir)
        print(_bar(f"agent_{aid} ({_personality(sv)})  {sv.log_path.name}"))
        gs0 = first_observe(sv) or {}
        gs = latest_observe(sv) or {}
        print(f"  start: finished={[q.get('name') for q in gs0.get('finished_quests') or []]}  "
              f"active={[q.get('name') for q in gs0.get('active_quests') or []]}")
        print(f"  now:   finished={[q.get('name') for q in gs.get('finished_quests') or []]}  "
              f"active={_fmt_quests(gs.get('active_quests'))}")

        # quest-relevant tool calls
        npc_calls = [tc for tc in sv.tool_calls if tc.short_name == "interact_npc"]
        npc_count: dict[str, int] = {}
        accepts = 0
        for tc in npc_calls:
            npc = tc.input.get("npc_name", "?")
            npc_count[npc] = npc_count.get(npc, 0) + 1
            if isinstance(tc.result_payload, dict) and tc.result_payload.get("quest_accepted"):
                accepts += 1
        if npc_count:
            top = sorted(npc_count.items(), key=lambda kv: -kv[1])[:6]
            print(f"  NPC talks: {dict(top)}    quest_accepted events: {accepts}")
        qq = [tc for tc in sv.tool_calls if tc.short_name == "query_quest"]
        if qq:
            print(f"  query_quest: {[tc.input.get('quest_name','?') for tc in qq]}")


def cmd_tools(views: list[tuple[Path, SessionView]]) -> None:
    """Tool call distribution + error rates per agent."""
    for agent_dir, sv in views:
        aid = _agent_id(agent_dir)
        print(_bar(f"agent_{aid} ({_personality(sv)})  {len(sv.tool_calls)} calls"))
        ec = tool_error_counts(sv)
        for tool, (errs, total) in sorted(ec.items(), key=lambda kv: -kv[1][1]):
            pct_err = (errs / total * 100) if total else 0
            tag = f"  ERR {errs}/{total} ({pct_err:.0f}%)" if errs else ""
            print(f"    {tool:<20} {total:>4}{tag}")


def cmd_recent(views: list[tuple[Path, SessionView]], n: int) -> None:
    """Last N tool calls per agent — very useful for 'what's it doing right now'."""
    for agent_dir, sv in views:
        aid = _agent_id(agent_dir)
        print(_bar(f"agent_{aid} ({_personality(sv)}) — last {n} tool calls"))
        for tc in sv.tool_calls[-n:]:
            inp = ", ".join(f"{k}={v!r}" for k, v in list(tc.input.items())[:3])
            err = " [ERR]" if tc.is_error else ""
            err_msg = f"  → {tc.result_error[:80]}" if tc.is_error else ""
            print(f"    #{tc.idx:<3} {tc.short_name:<16} {inp}{err}{err_msg}")


def cmd_errors(views: list[tuple[Path, SessionView]]) -> None:
    """Categorized error breakdown + 'what did the agent do next' transition matrix.

    Surfaces the recurring failure modes (BFS_NO_PATH, STILL_MOVING,
    NPC_NOT_FOUND, STATION_UNREACHABLE, etc.) so we can tell whether the
    agent recovers (e.g., warps after BFS fail per Rule 4a) or loops.
    """
    for agent_dir, sv in views:
        aid = _agent_id(agent_dir)
        errs = [(i, tc) for i, tc in enumerate(sv.tool_calls) if tc.is_error]
        if not errs:
            continue
        print(_bar(f"agent_{aid} — {len(errs)} errors of {len(sv.tool_calls)}"))
        # Per-category counts + next-action transitions.
        cat_counts: dict[str, int] = {}
        next_actions: dict[str, dict[str, int]] = {}
        sample: dict[str, str] = {}
        for i, tc in errs:
            cat = categorize_error(tc.result_error or "")
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
            sample.setdefault(cat, f"{tc.short_name}: {(tc.result_error or '')[:120]}")
            nxt = sv.tool_calls[i+1].short_name if i+1 < len(sv.tool_calls) else "<end>"
            next_actions.setdefault(cat, {})
            next_actions[cat][nxt] = next_actions[cat].get(nxt, 0) + 1
        for cat, count in sorted(cat_counts.items(), key=lambda kv: -kv[1]):
            top_next = sorted(next_actions[cat].items(), key=lambda kv: -kv[1])[:3]
            transitions = ", ".join(f"{n}×{c}" for n, c in top_next)
            print(f"    [{count}x] {cat:<22} → next: {transitions}")
            print(f"           ex: {sample[cat]}")


def cmd_runs(_views, n: int = 10, all_runs: bool = False) -> None:
    """List recent runs across all agents with key stats from run.meta.json."""
    runs = runs_per_agent()
    runs.sort(key=lambda r: r.run_id, reverse=True)
    if not all_runs:
        runs = runs[:n]
    print(f"{'run_id':<24}{'agent':<7}{'pers':<14}{'harness':<9}"
          f"{'sessions':<10}{'started_at':<22}{'duration':<10}")
    print("─" * 96)
    for r in runs:
        m = r.meta
        sessions_meta = m.get("session_count")
        sessions_actual = len(r.session_paths)
        sessions = (f"{sessions_actual}" if sessions_meta == sessions_actual
                    else f"{sessions_actual} (meta:{sessions_meta})")
        print(f"{r.run_id:<24}"
              f"{str(m.get('agent_id','?')):<7}"
              f"{(m.get('personality') or '?')[:13]:<14}"
              f"{(m.get('harness') or '?')[:8]:<9}"
              f"{sessions:<10}"
              f"{fmt_est(r.started_at):<22}"
              f"{fmt_duration(r.duration_s):<10}")


def cmd_tier_a(views: list[tuple[Path, SessionView]]) -> None:
    """Per-agent Tier-A adoption metrics (rules + tool fields actually used)."""
    print(f"{'agent':<7}{'qq':<5}{'qq+gate':<9}{'gated':<7}"
          f"{'accepts':<9}{'q→accept':<10}"
          f"{'BFS':<5}{'→warp':<7}{'→retry':<8}"
          f"{'gather✓gate':<13}{'inv_full':<10}{'drop@full':<11}"
          f"{'mob_overshot':<13}{'stations':<9}{'deaths':<7}")
    print("─" * 130)
    for agent_dir, sv in views:
        aid = _agent_id(agent_dir)
        s = tier_a_signals(sv)
        warp_pct = f"{s.bfs_then_warp}({100*s.bfs_then_warp//max(1,s.bfs_fails)}%)"
        retry_pct = f"{s.bfs_then_navigate}({100*s.bfs_then_navigate//max(1,s.bfs_fails)}%)"
        accept_pct = f"{s.accept_with_prior_query}/{s.accept_calls}"
        print(f"{aid:<7}{s.query_quest_calls:<5}{s.query_quest_with_live_gate:<9}"
              f"{s.query_quest_gated_seen:<7}{s.accept_calls:<9}{accept_pct:<10}"
              f"{s.bfs_fails:<5}{warp_pct:<7}{retry_pct:<8}"
              f"{s.gather_with_gate_explained:<13}{s.inv_full_observed:<10}"
              f"{s.drops_after_full:<11}{s.mob_level_overshoot_attacks:<13}"
              f"{s.station_locations_returned:<9}{s.deaths:<7}")
    print()
    print("Legend:")
    print("  qq          query_quest calls this session")
    print("  qq+gate     of those, how many returned a live_gate_status block")
    print("  gated       of those, how many showed gated:true (Rule 9 fallback fired)")
    print("  q→accept    accepts preceded by query_quest within 3 calls (Rule 10 compliance)")
    print("  BFS         navigate calls that returned BFS no-path")
    print("  →warp /     of BFS-fails, what the agent did next")
    print("    →retry      (Rule 4a wants ≥80% →warp)")
    print("  gather✓gate gather calls that returned a structured `gate` block (A2)")
    print("  inv_full    observes that surfaced inventory_summary.full=true")
    print("  drop@full   drop_item within 3 turns of inv_full (correct response)")
    print("  mob_overshot attacks on mobs >+10 levels above player (Rule 11 violation)")
    print("  stations    query_quest calls that returned station_locations (new field)")


def cmd_timeline(views: list[tuple[Path, SessionView]], n: int = 30) -> None:
    """Chronological event stream for one agent — sessions, deaths, quest events,
    level-ups, accepts, BFS-fails. Shows the last N events."""
    for agent_dir, sv in views:
        aid = _agent_id(agent_dir)
        ts = parse_session_timestamp(sv.log_path)
        print(_bar(f"agent_{aid} — {sv.log_path.name} (started {fmt_est(ts)})"))
        events: list[tuple[int, str]] = []
        last_level = None
        for tc in sv.tool_calls:
            p = tc.result_payload if isinstance(tc.result_payload, dict) else {}
            if tc.short_name == "respawn":
                events.append((tc.idx, "💀 respawn"))
            elif tc.short_name == "interact_npc" and tc.input.get("accept_quest_offer") \
                 and isinstance(p, dict) and (p.get("quest_accepted") or p.get("quest_opened")):
                events.append((tc.idx, f"📜 ACCEPT {tc.input.get('npc_name')}"))
            elif tc.short_name == "navigate" and tc.is_error and "BFS" in (tc.result_error or ""):
                events.append((tc.idx, f"🚫 BFS no-path → ({tc.input.get('x')},{tc.input.get('y')})"))
            elif tc.short_name == "warp":
                events.append((tc.idx, f"🌀 warp({tc.input.get('location')})"))
            elif tc.short_name == "query_quest":
                lgs = (p.get("live_gate_status") or {}) if isinstance(p, dict) else {}
                tag = " [GATED]" if lgs.get("gated") else ""
                events.append((tc.idx, f"❓ query {tc.input.get('quest_name')}{tag}"))
            if tc.short_name == "observe" and isinstance(p, dict):
                lvl = (p.get("stats") or {}).get("level")
                if lvl is not None and last_level is not None and lvl > last_level:
                    events.append((tc.idx, f"⭐ LEVEL UP {last_level} → {lvl}"))
                if lvl is not None:
                    last_level = lvl
                # Quest finished events
                fin = [(q.get("name") or "?") for q in (p.get("finished_quests") or [])
                       if isinstance(q, dict)]
                # Trigger only on first observe where it appears (not every poll)
                # — handled by tracking a set across the loop:
        for idx, msg in events[-n:]:
            print(f"  #{idx:<4} {msg}")
        if not events:
            print("  (no notable events)")


def cmd_thinking(views: list[tuple[Path, SessionView]], n: int) -> None:
    """Last N reasoning blocks per agent — shows what the model is currently planning."""
    for agent_dir, sv in views:
        aid = _agent_id(agent_dir)
        print(_bar(f"agent_{aid} ({_personality(sv)}) — last {n} thinking blocks"))
        with_think = [tc for tc in sv.tool_calls if tc.thinking]
        for tc in with_think[-n:]:
            think = (tc.thinking or "").replace("\n", " ")[:400]
            print(f"  → before #{tc.idx} ({tc.short_name}): {think}")


def cmd_agent(view: tuple[Path, SessionView], n_recent: int = 10) -> None:
    """Deep-dive one agent."""
    agent_dir, sv = view
    aid = _agent_id(agent_dir)
    print(_bar(f"agent_{aid} ({_personality(sv)}) — {sv.log_path.name} ({_age(sv.log_path)})"))
    print(f"  meta: {sv.meta}")
    print(f"  turns: {sv.n_assistant} assistant / {sv.n_user} user / "
          f"{sv.n_thinking} thinking / {sv.n_text} text / "
          f"{len(sv.tool_calls)} tool_calls / {sv.rate_limit_events} rate_limit_events")

    gs0 = first_observe(sv) or {}
    gs = latest_observe(sv) or {}
    s0 = (gs0.get("stats") or {})
    s1 = (gs.get("stats") or {})

    def _chg(a, b):
        try: return f"{a}→{b} (+{b-a})"
        except Exception: return f"{a}→{b}"

    print(f"  level: {_chg(s0.get('level','?'), s1.get('level','?'))}    "
          f"xp: {_chg(s0.get('xp','?'), s1.get('xp','?'))}    "
          f"hp now: {s1.get('hp','?')}/{s1.get('max_hp','?')}")
    print(f"  pos start: {_fmt_pos(gs0.get('pos'))}   pos now: {_fmt_pos(gs.get('pos'))}")
    print(f"  finished: {[q.get('name') for q in gs.get('finished_quests') or []]}")
    print(f"  active:   {_fmt_quests(gs.get('active_quests'))}")
    print(f"  inv items: {len(gs.get('inventory') or [])}    "
          f"deaths: {len(deaths(sv))}")

    print(f"\n  tool counts: {tool_call_counts(sv)}")

    err_count = sum(1 for tc in sv.tool_calls if tc.is_error)
    if err_count:
        print(f"  errors: {err_count} (run `analyze.py errors` for detail)")

    print(f"\n  last {n_recent} tool calls:")
    for tc in sv.tool_calls[-n_recent:]:
        inp = ", ".join(f"{k}={v!r}" for k, v in list(tc.input.items())[:3])
        err = " [ERR]" if tc.is_error else ""
        print(f"    #{tc.idx:<3} {tc.short_name:<16} {inp}{err}")


def cmd_full(views: list[tuple[Path, SessionView]]) -> None:
    """Full report: status + quests + tools + recent + errors."""
    print(_bar("STATUS"))
    cmd_status(views)
    print(_bar("QUESTS"))
    cmd_quests(views)
    print(_bar("TOOLS"))
    cmd_tools(views)
    print(_bar("RECENT (last 8)"))
    cmd_recent(views, n=8)
    has_errs = any(tc.is_error for _, sv in views for tc in sv.tool_calls)
    if has_errs:
        print(_bar("ERRORS"))
        cmd_errors(views)


# ── Driver ───────────────────────────────────────────────────────────────────

_STALE_SECONDS = 600  # 10 minutes — log untouched longer than this is from a prior run


def _load_views(only_agent: int | None = None, include_stale: bool = False,
                harness_override: str | None = None,
                run_id: str | None = None) -> list[tuple[Path, SessionView]]:
    """Latest session log per agent, optionally scoped to a specific run_id.

    `--run <id>` resolves to the matching `runs/<id>/` dir per agent and
    picks that run's most-recent session log. Without a run filter we use
    the agent's `logs/` symlink (current run).
    """
    if run_id:
        from pathlib import Path as _P
        from scripts.log_analysis.parse import RAW_DIR as _RAW
        pairs: list[tuple[Path, Path]] = []
        for agent_dir in sorted(p for p in _RAW.glob("agent_*") if p.is_dir()):
            run_dir = agent_dir / "runs" / run_id
            if not run_dir.is_dir():
                continue
            logs = sorted(run_dir.glob("session_*.log"), key=lambda p: p.stat().st_mtime)
            if logs:
                pairs.append((agent_dir, logs[-1]))
    else:
        pairs = latest_logs_per_agent()
    if only_agent is not None:
        pairs = [(d, l) for d, l in pairs if d.name == f"agent_{only_agent}"]
    elif not include_stale and not run_id:
        now = dt.datetime.now().timestamp()
        pairs = [(d, l) for d, l in pairs if (now - l.stat().st_mtime) <= _STALE_SECONDS]
    out: list[tuple[Path, SessionView]] = []
    for agent_dir, log in pairs:
        out.append((agent_dir, parse_session_auto(log, harness=harness_override)))
    return out


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("status")
    sub.add_parser("quests")
    sub.add_parser("tools")
    pr = sub.add_parser("recent"); pr.add_argument("-n", type=int, default=8)
    sub.add_parser("errors")
    pt = sub.add_parser("thinking"); pt.add_argument("-n", type=int, default=3)
    pa = sub.add_parser("agent"); pa.add_argument("agent_id", type=int); pa.add_argument("-n", type=int, default=10)
    sub.add_parser("full")
    sub.add_parser("tier_a")
    pn = sub.add_parser("runs"); pn.add_argument("-n", type=int, default=10)
    pl = sub.add_parser("timeline"); pl.add_argument("-n", type=int, default=30)
    p.add_argument("--stale", action="store_true",
                   help="include sessions whose log hasn't been touched in 10+ minutes "
                        "(default: only currently-running agents)")
    p.add_argument("--run", dest="run_id", default=None,
                   help="scope to a specific run_id (e.g. run_20260427_135613). "
                        "Without this, looks at the latest run per agent.")
    p.add_argument("--all-runs", action="store_true",
                   help="for `runs` cmd: show every run, not just the recent N")
    p.add_argument("--opencode", action="store_const", const="opencode", dest="harness",
                   help="force the OpenCode log parser (default: auto-detect from meta.json)")
    p.add_argument("--claude", action="store_const", const="claude", dest="harness",
                   help="force the Claude log parser (default: auto-detect from meta.json)")
    args = p.parse_args()

    cmd = args.cmd or "full"

    # `runs` lists run dirs directly; doesn't need session views.
    if cmd == "runs":
        cmd_runs(None, n=args.n, all_runs=args.all_runs)
        return 0

    if cmd == "agent":
        views = _load_views(only_agent=args.agent_id, harness_override=args.harness, run_id=args.run_id)
        if not views:
            print(f"No log found for agent_{args.agent_id}", file=sys.stderr)
            return 1
        cmd_agent(views[0], n_recent=args.n)
        return 0

    views = _load_views(include_stale=args.stale, harness_override=args.harness, run_id=args.run_id)
    if not views:
        msg = (f"No agent logs found for run {args.run_id}." if args.run_id
               else "No active agent logs in the last 10 minutes. "
                    "Pass --stale to include older sessions.")
        print(msg, file=sys.stderr)
        return 1

    # Header — show file + age for every report
    print(f"# Log analysis  ({dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")
    for agent_dir, sv in views:
        print(f"  agent_{_agent_id(agent_dir)}: {sv.log_path.name}  "
              f"(modified {_age(sv.log_path)}, {len(sv.tool_calls)} tool calls)")

    if cmd == "status":   cmd_status(views)
    elif cmd == "quests": cmd_quests(views)
    elif cmd == "tools":  cmd_tools(views)
    elif cmd == "recent": cmd_recent(views, n=args.n)
    elif cmd == "errors": cmd_errors(views)
    elif cmd == "thinking": cmd_thinking(views, n=args.n)
    elif cmd == "tier_a": cmd_tier_a(views)
    elif cmd == "timeline": cmd_timeline(views, n=args.n)
    elif cmd == "full":   cmd_full(views)
    else:
        p.print_help()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
