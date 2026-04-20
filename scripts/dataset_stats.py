#!/usr/bin/env python3
"""Extract per-tool statistics from agent session logs.

Each session log is JSONL where relevant records are:
  - {type: "assistant", message.content[i].type: "tool_use", ...}
  - {type: "user",      message.content[i].type: "tool_result", ...}

We pair tool_use → tool_result by `tool_use_id` within a session, classify
success / failure from the result payload, and emit one event dict per call.

Outputs:
  - a flat events.jsonl with one row per tool call (optional --emit)
  - stdout tables: per-tool counts + fail rates, top args, top errors, top
    consecutive-repeat sequences per agent

Usage:
  scripts/dataset_stats.py
  scripts/dataset_stats.py --since 2026-04-19T10:00:00Z
  scripts/dataset_stats.py --agent 0 --repeats --top-k 15
  scripts/dataset_stats.py --emit /tmp/events.jsonl
  scripts/dataset_stats.py --only-tool equip_item --errors
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

DATASET_ROOT = Path.home() / "projects" / "kaetram-agent" / "dataset" / "raw"
TOOL_PREFIX = "mcp__kaetram__"

# Per-tool: which argument to highlight as the "target" for summaries.
TARGET_ARG_BY_TOOL = {
    "attack": "mob_name",
    "gather": "resource_name",
    "interact_npc": "npc_name",
    "warp": "location",
    "buy_item": "npc_name",
    "eat_food": "slot",
    "drop_item": "slot",
    "equip_item": "slot",
    "set_attack_style": "style",
    "craft_item": "recipe_key",
    "navigate": None,  # x/y combo handled separately
    "query_quest": "quest_name",
}

FAIL_PATTERNS = [
    re.compile(r"\berror\b", re.IGNORECASE),
    re.compile(r"\bfail(?:ed|ure|s)?\b", re.IGNORECASE),
    re.compile(r"\bcannot\b", re.IGNORECASE),
    re.compile(r"\bcould not\b", re.IGNORECASE),
    re.compile(r"\bno \w+ matching\b", re.IGNORECASE),
    re.compile(r"\bnot found\b", re.IGNORECASE),
    re.compile(r"\bnot met\b", re.IGNORECASE),
    re.compile(r"\brejected\b", re.IGNORECASE),
    re.compile(r"\bnot\s+reachable\b", re.IGNORECASE),
    re.compile(r"\bpathfinding\s+failed\b", re.IGNORECASE),
    re.compile(r"\bunchanged\b", re.IGNORECASE),
    re.compile(r"\bnot equipped\b", re.IGNORECASE),
]


@dataclass
class Event:
    agent: str
    session_file: str
    session_id: str
    tool_use_id: str
    tool: str
    args: dict[str, Any]
    target: str | None
    succeeded: bool
    fail_reason: str | None
    result_timestamp: float | None
    line_index: int

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def _decode_result_content(raw: Any) -> tuple[dict[str, Any] | None, str, bool]:
    """tool_result.content arrives as either a string or a list. Decode both
    layers of nested JSON when present.

    Returns (decoded_dict_or_None, text_for_regex, outer_has_result_key).
    When `outer_has_result_key` is True, the tool returned a structured
    result — trust it as success even if the inner payload is malformed
    (observe sometimes emits concatenated JSON docs that fail to parse)."""
    if isinstance(raw, list):
        parts = []
        for item in raw:
            if isinstance(item, dict) and "text" in item:
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        raw = "\n".join(parts)
    if not isinstance(raw, str):
        return None, str(raw), False

    decoded: dict[str, Any] | None = None
    text = raw
    outer_has_result = False
    try:
        outer = json.loads(raw)
        if isinstance(outer, dict):
            if "result" in outer and "error" not in outer:
                outer_has_result = True
            inner_raw = outer.get("result", outer)
            if isinstance(inner_raw, str):
                try:
                    inner = json.loads(inner_raw)
                    if isinstance(inner, dict):
                        decoded = inner
                        text = inner_raw
                    else:
                        text = inner_raw
                except json.JSONDecodeError:
                    text = inner_raw
            elif isinstance(inner_raw, dict):
                decoded = inner_raw
                text = json.dumps(inner_raw)
    except json.JSONDecodeError:
        pass
    return decoded, text, outer_has_result


def _classify(
    result_dict: dict[str, Any] | None,
    result_text: str,
    outer_has_result: bool = False,
) -> tuple[bool, str | None]:
    """Return (succeeded, fail_reason). Prefers structured fields; only falls
    back to regex over the text when no dict could be decoded AND the outer
    wrapper didn't already declare success via a `result` key. Without that
    guard, observe payloads get false-flagged because the huge entity blob
    contains words like "error" or "fail" inside field names / entity data."""
    if result_dict is not None:
        if "error" in result_dict and result_dict.get("error"):
            return False, str(result_dict["error"])[:200]
        for key in ("success", "ok", "succeeded"):
            if key in result_dict and isinstance(result_dict[key], bool):
                if not result_dict[key]:
                    reason = (
                        result_dict.get("error")
                        or result_dict.get("reason")
                        or result_dict.get("message")
                    )
                    return False, (str(reason)[:200] if reason else key)
                return True, None
        # Tool-specific booleans like `"dropped": false`, `"equipped": false`
        for key in (
            "dropped",
            "equipped",
            "attacked",
            "navigated",
            "warped",
            "gathered",
            "looted",
            "crafted",
            "purchased",
            "eaten",
        ):
            if key in result_dict and result_dict[key] is False:
                reason = (
                    result_dict.get("error")
                    or result_dict.get("reason")
                    or result_dict.get("message")
                )
                return False, (str(reason)[:200] if reason else key)
        # Successfully decoded a dict with no fail signal — trust it.
        return True, None

    if outer_has_result:
        # Tool returned a structured `{"result": ...}` wrapper even if the
        # inner payload couldn't parse. Trust it.
        return True, None

    if result_text:
        for pat in FAIL_PATTERNS:
            if pat.search(result_text):
                snippet = result_text.strip().splitlines()[0][:200]
                return False, snippet
    return True, None


def _target(tool: str, args: dict[str, Any]) -> str | None:
    arg_name = TARGET_ARG_BY_TOOL.get(tool)
    if arg_name and arg_name in args:
        return str(args[arg_name])
    if tool == "navigate":
        if "x" in args and "y" in args:
            return f"{args['x']},{args['y']}"
    return None


def parse_session(path: Path, agent: str) -> Iterator[Event]:
    pending: dict[str, dict[str, Any]] = {}
    session_id = ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line_index, line in enumerate(fh):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rtype = record.get("type")
                if rtype == "system" and record.get("subtype") == "init":
                    session_id = record.get("session_id", "")
                    continue
                msg = record.get("message") or {}
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    itype = item.get("type")
                    if itype == "tool_use":
                        name = item.get("name", "")
                        if not name.startswith(TOOL_PREFIX):
                            continue
                        pending[item["id"]] = {
                            "tool": name[len(TOOL_PREFIX):],
                            "args": item.get("input") or {},
                            "line_index": line_index,
                        }
                    elif itype == "tool_result":
                        tu_id = item.get("tool_use_id")
                        if not tu_id or tu_id not in pending:
                            continue
                        use = pending.pop(tu_id)
                        decoded, text, has_result = _decode_result_content(item.get("content"))
                        succeeded, fail_reason = _classify(decoded, text, has_result)
                        timestamp = None
                        if decoded and isinstance(decoded.get("timestamp"), (int, float)):
                            timestamp = float(decoded["timestamp"])
                        yield Event(
                            agent=agent,
                            session_file=path.name,
                            session_id=session_id,
                            tool_use_id=tu_id,
                            tool=use["tool"],
                            args=use["args"],
                            target=_target(use["tool"], use["args"]),
                            succeeded=succeeded,
                            fail_reason=fail_reason,
                            result_timestamp=timestamp,
                            line_index=use["line_index"],
                        )
    except FileNotFoundError:
        return


def parse_all(
    root: Path,
    *,
    agent_filter: str | None = None,
    since_epoch: float | None = None,
    only_tool: str | None = None,
) -> Iterator[Event]:
    for agent_dir in sorted(root.glob("agent_*")):
        agent = agent_dir.name
        if agent_filter and agent_filter != agent:
            continue
        logs = agent_dir / "logs"
        if not logs.is_dir():
            continue
        for log in sorted(logs.glob("*.log")):
            if since_epoch is not None and log.stat().st_mtime < since_epoch:
                continue
            for event in parse_session(log, agent):
                if only_tool and event.tool != only_tool:
                    continue
                yield event


# ----------------------------- summary helpers -----------------------------


def per_tool_summary(events: list[Event], top_k: int) -> str:
    rows = defaultdict(lambda: {"total": 0, "fail": 0, "targets": Counter(), "errors": Counter()})
    for event in events:
        row = rows[event.tool]
        row["total"] += 1
        if not event.succeeded:
            row["fail"] += 1
            if event.fail_reason:
                row["errors"][event.fail_reason] += 1
        if event.target is not None:
            row["targets"][event.target] += 1

    lines = [
        f"{'tool':<18} {'calls':>7} {'fail':>6} {'fail%':>6}  top targets",
        "-" * 100,
    ]
    for tool in sorted(rows, key=lambda t: -rows[t]["total"]):
        row = rows[tool]
        fail_pct = (row["fail"] / row["total"]) * 100 if row["total"] else 0.0
        top_targets = ", ".join(
            f"{t}×{c}" for t, c in row["targets"].most_common(top_k)
        )
        lines.append(
            f"{tool:<18} {row['total']:>7} {row['fail']:>6} {fail_pct:>5.1f}%  {top_targets}"
        )
    return "\n".join(lines)


def per_agent_tool_matrix(events: list[Event]) -> str:
    agents = sorted({e.agent for e in events})
    tools = sorted({e.tool for e in events})
    by_cell = defaultdict(lambda: [0, 0])  # [total, fail]
    for event in events:
        cell = by_cell[(event.agent, event.tool)]
        cell[0] += 1
        if not event.succeeded:
            cell[1] += 1

    width = max(14, max((len(t) for t in tools), default=0) + 2)
    header = "tool".ljust(width) + "".join(f"{a:>16}" for a in agents)
    lines = [header, "-" * len(header)]
    for tool in tools:
        row = tool.ljust(width)
        for agent in agents:
            total, fail = by_cell.get((agent, tool), [0, 0])
            if total == 0:
                row += f"{'·':>16}"
            else:
                row += f"{total:>7}/{fail:<3}({(fail/total)*100:>3.0f}%)"
        lines.append(row)
    return "\n".join(lines)


def top_error_messages(events: list[Event], top_k: int) -> str:
    by_tool = defaultdict(Counter)
    for event in events:
        if not event.succeeded and event.fail_reason:
            by_tool[event.tool][event.fail_reason] += 1
    lines = []
    for tool in sorted(by_tool, key=lambda t: -sum(by_tool[t].values())):
        lines.append(f"\n[{tool}]")
        for reason, count in by_tool[tool].most_common(top_k):
            trimmed = reason.replace("\n", " ")[:160]
            lines.append(f"  {count:>5}× {trimmed}")
    return "\n".join(lines) if lines else "(no failures in this window)"


def consecutive_repeats(events: list[Event], min_run: int, top_k: int) -> str:
    """Find runs of identical (tool, target) within a single session.

    Only emits runs of length >= `min_run`, top-k by run length."""
    by_session = defaultdict(list)
    for event in events:
        by_session[(event.agent, event.session_file)].append(event)

    runs: list[tuple[int, str, str, str, str]] = []  # (length, agent, session, tool, target)
    for (agent, session), session_events in by_session.items():
        session_events.sort(key=lambda e: e.line_index)
        current_key: tuple[str, str | None] | None = None
        current_len = 0
        for event in session_events:
            key = (event.tool, event.target)
            if key == current_key:
                current_len += 1
            else:
                if current_key and current_len >= min_run:
                    tool, target = current_key
                    runs.append((current_len, agent, session, tool, str(target)))
                current_key = key
                current_len = 1
        if current_key and current_len >= min_run:
            tool, target = current_key
            runs.append((current_len, agent, session, tool, str(target)))

    runs.sort(key=lambda r: -r[0])
    lines = [f"Top {top_k} consecutive-repeat runs (min {min_run}):", "-" * 80]
    for length, agent, session, tool, target in runs[:top_k]:
        lines.append(f"  {length:>4}× {tool}({target}) — {agent} / {session}")
    return "\n".join(lines)


def per_session_activity(events: list[Event], top_k: int) -> str:
    by_session = defaultdict(lambda: {"calls": 0, "fails": 0, "agent": "", "file": ""})
    for event in events:
        row = by_session[(event.agent, event.session_file)]
        row["calls"] += 1
        row["fails"] += 0 if event.succeeded else 1
        row["agent"] = event.agent
        row["file"] = event.session_file

    rows = sorted(
        by_session.items(), key=lambda kv: -kv[1]["calls"]
    )
    lines = [
        f"Top {top_k} sessions by call volume:",
        f"{'agent':<10} {'calls':>6} {'fails':>6}  file",
        "-" * 90,
    ]
    for (_agent, _file), row in rows[:top_k]:
        lines.append(
            f"{row['agent']:<10} {row['calls']:>6} {row['fails']:>6}  {row['file']}"
        )
    return "\n".join(lines)


def overall_totals(events: list[Event]) -> str:
    if not events:
        return "No events matched the filter."
    total = len(events)
    fails = sum(1 for e in events if not e.succeeded)
    agents = {e.agent for e in events}
    sessions = {(e.agent, e.session_file) for e in events}
    tools = {e.tool for e in events}
    first_ts = min((e.result_timestamp for e in events if e.result_timestamp), default=None)
    last_ts = max((e.result_timestamp for e in events if e.result_timestamp), default=None)
    span = ""
    if first_ts and last_ts:
        span = (
            f"\n  span          {datetime.fromtimestamp(first_ts, tz=timezone.utc)}"
            f" → {datetime.fromtimestamp(last_ts, tz=timezone.utc)}"
        )
    return (
        f"events        {total}\n"
        f"  fails       {fails} ({fails/total*100:.1f}%)\n"
        f"  agents      {len(agents)} ({', '.join(sorted(agents))})\n"
        f"  sessions    {len(sessions)}\n"
        f"  tools       {len(tools)}"
        f"{span}"
    )


# ----------------------------- CLI -----------------------------


def _parse_since(raw: str) -> float:
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DATASET_ROOT)
    parser.add_argument("--agent", help="Filter to this agent dir (e.g. agent_0)")
    parser.add_argument("--since", help="ISO8601 — only parse log files with mtime >= this")
    parser.add_argument("--only-tool", help="Filter to a single tool name (without mcp__kaetram__ prefix)")
    parser.add_argument("--emit", type=Path, help="Write per-call events as JSONL here")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--min-repeat", type=int, default=5)
    parser.add_argument("--sessions", action="store_true", help="Show per-session activity")
    parser.add_argument("--matrix", action="store_true", help="Show per-agent-per-tool matrix")
    parser.add_argument("--repeats", action="store_true", help="Show consecutive-repeat runs")
    parser.add_argument("--errors", action="store_true", help="Show top error messages per tool")
    parser.add_argument(
        "--all", action="store_true", help="Show all sections (sessions, matrix, repeats, errors)"
    )
    args = parser.parse_args(argv)

    since_epoch = _parse_since(args.since) if args.since else None

    events = list(
        parse_all(
            args.root,
            agent_filter=args.agent,
            since_epoch=since_epoch,
            only_tool=args.only_tool,
        )
    )

    if args.emit:
        args.emit.parent.mkdir(parents=True, exist_ok=True)
        with args.emit.open("w", encoding="utf-8") as fh:
            for event in events:
                fh.write(json.dumps(event.to_json(), separators=(",", ":")) + "\n")
        print(f"wrote {len(events)} events → {args.emit}")

    print("=== overall ===")
    print(overall_totals(events))
    print("\n=== per-tool ===")
    print(per_tool_summary(events, args.top_k))

    if args.all or args.matrix:
        print("\n=== per-agent × tool (total/fail(fail%)) ===")
        print(per_agent_tool_matrix(events))
    if args.all or args.sessions:
        print("\n=== top sessions ===")
        print(per_session_activity(events, args.top_k))
    if args.all or args.repeats:
        print("\n=== repeats ===")
        print(consecutive_repeats(events, args.min_repeat, args.top_k))
    if args.all or args.errors:
        print("\n=== top errors ===")
        print(top_error_messages(events, args.top_k))

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
