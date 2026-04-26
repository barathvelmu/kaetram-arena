#!/usr/bin/env python3
"""Comprehensive log analyzer for the currently running Claude harness agents.

Usage:
    python3 scripts/log_analysis/analyze.py                  # full report (default)
    python3 scripts/log_analysis/analyze.py status           # one-line per agent
    python3 scripts/log_analysis/analyze.py quests           # quest-focused detail
    python3 scripts/log_analysis/analyze.py tools            # tool call breakdown
    python3 scripts/log_analysis/analyze.py recent [-n 10]   # last N tool calls per agent
    python3 scripts/log_analysis/analyze.py errors           # all tool errors
    python3 scripts/log_analysis/analyze.py thinking [-n 5]  # last N thinking blocks per agent
    python3 scripts/log_analysis/analyze.py agent <N>        # deep-dive single agent

All commands operate on the LATEST session log per agent_dir under dataset/raw/.
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
    deaths,
    first_observe,
    latest_logs_per_agent,
    latest_observe,
    parse_session,
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
    """One-line per agent — the quickest possible health check."""
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
    """All tool errors across agents — helps spot systemic issues."""
    for agent_dir, sv in views:
        aid = _agent_id(agent_dir)
        errs = [tc for tc in sv.tool_calls if tc.is_error]
        if not errs:
            continue
        print(_bar(f"agent_{aid} — {len(errs)} errors of {len(sv.tool_calls)}"))
        # Group by short_name + error prefix
        groups: dict[tuple[str, str], int] = {}
        sample: dict[tuple[str, str], str] = {}
        for tc in errs:
            key = (tc.short_name, (tc.result_error or "")[:60])
            groups[key] = groups.get(key, 0) + 1
            sample.setdefault(key, tc.result_error or "")
        for (tool, prefix), count in sorted(groups.items(), key=lambda kv: -kv[1]):
            print(f"    [{count}x] {tool}: {sample[(tool, prefix)][:140]}")


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


def _load_views(only_agent: int | None = None, include_stale: bool = False) -> list[tuple[Path, SessionView]]:
    pairs = latest_logs_per_agent()
    if only_agent is not None:
        pairs = [(d, l) for d, l in pairs if d.name == f"agent_{only_agent}"]
    elif not include_stale:
        now = dt.datetime.now().timestamp()
        pairs = [(d, l) for d, l in pairs if (now - l.stat().st_mtime) <= _STALE_SECONDS]
    out: list[tuple[Path, SessionView]] = []
    for agent_dir, log in pairs:
        out.append((agent_dir, parse_session(log)))
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
    p.add_argument("--stale", action="store_true",
                   help="include sessions whose log hasn't been touched in 10+ minutes "
                        "(default: only currently-running agents)")
    args = p.parse_args()

    cmd = args.cmd or "full"

    if cmd == "agent":
        views = _load_views(only_agent=args.agent_id)
        if not views:
            print(f"No log found for agent_{args.agent_id}", file=sys.stderr)
            return 1
        cmd_agent(views[0], n_recent=args.n)
        return 0

    views = _load_views(include_stale=args.stale)
    if not views:
        print("No active agent logs in the last 10 minutes. Pass --stale to include older sessions.",
              file=sys.stderr)
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
    elif cmd == "full":   cmd_full(views)
    else:
        p.print_help()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
