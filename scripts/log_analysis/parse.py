"""Shared parsers for Claude harness session logs (JSONL stream-json).

Log shape (verified 2026-04-25):

    types: system (1, line 1) | assistant | user | rate_limit_event | result (1, last)

    assistant.message.content[]: thinking | text | tool_use
    user.message.content[]:      tool_result   (paired by tool_use_id)

    tool_result.content is a STRING containing:
        '{"result": "<INNER STRING>"}'

    For observe(), INNER STRING is:
        '<game_state_json>\\n\\nASCII_MAP:<ascii grid>'

    For other tools, INNER STRING is just the tool's JSON output (also stringified).

    So a full decode of an observe result is THREE json.loads + one split.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable

REPO = Path(__file__).resolve().parents[2]
RAW_DIR = REPO / "dataset" / "raw"

# orchestrate.py writes timestamps in EDT (UTC-4) — keep that in sync.
_EST = timezone(timedelta(hours=-4))


# ── Discovery ────────────────────────────────────────────────────────────────

def list_agent_dirs() -> list[Path]:
    return sorted(p for p in RAW_DIR.glob("agent_*") if p.is_dir())


def list_runs(agent_dir: Path) -> list[Path]:
    """Every `runs/run_*/` dir for an agent, sorted by run_id (chronological)."""
    runs_root = agent_dir / "runs"
    if not runs_root.is_dir():
        return []
    return sorted(p for p in runs_root.glob("run_*") if p.is_dir())


def latest_run(agent_dir: Path) -> Path | None:
    """Most recent run dir (resolves the `logs/` symlink if present)."""
    sym = agent_dir / "logs"
    if sym.is_symlink():
        try:
            target = sym.resolve()
            if target.is_dir():
                return target
        except OSError:
            pass
    runs = list_runs(agent_dir)
    return runs[-1] if runs else None


def latest_session_log(agent_dir: Path) -> Path | None:
    """Most recently modified .log under the agent's latest run."""
    run = latest_run(agent_dir)
    if not run:
        return None
    logs = list(run.glob("session_*.log"))
    return max(logs, key=lambda p: p.stat().st_mtime) if logs else None


def latest_logs_per_agent() -> list[tuple[Path, Path]]:
    """[(agent_dir, log_path)] for each agent that has at least one log."""
    out = []
    for d in list_agent_dirs():
        log = latest_session_log(d)
        if log:
            out.append((d, log))
    return out


def session_meta(log_path: Path) -> dict:
    meta = log_path.with_suffix(".meta.json")
    return json.loads(meta.read_text()) if meta.exists() else {}


def run_meta(run_dir: Path) -> dict:
    f = run_dir / "run.meta.json"
    if not f.is_file():
        return {}
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return {}


# ── Time helpers ─────────────────────────────────────────────────────────────

def parse_session_timestamp(log_path: Path) -> datetime | None:
    """Pull the timestamp from a session log's filename (session_N_YYYYMMDD_HHMMSS.log).

    Returned in EDT — orchestrate.py writes session timestamps in local time
    (the VM's TZ). We tag them as EDT for display consistency with run_id.
    """
    m = re.search(r"session_\d+_(\d{8})_(\d{6})", log_path.name)
    if not m:
        return None
    try:
        ts = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
        return ts.replace(tzinfo=_EST)
    except ValueError:
        return None


def fmt_est(dt: datetime | None) -> str:
    if dt is None:
        return "?"
    return dt.astimezone(_EST).strftime("%Y-%m-%d %H:%M EST")


def fmt_duration(seconds: float | int | None) -> str:
    if seconds is None or seconds < 0:
        return "?"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{sec:02d}s"
    return f"{sec}s"


# ── Decoders ─────────────────────────────────────────────────────────────────

def decode_tool_result_content(raw: str) -> tuple[dict | str, str | None]:
    """Decode a tool_result `.content` string.

    Returns (payload, ascii_map). For observe, payload is the parsed game-state
    dict and ascii_map is the trailing ASCII grid. For other tools, payload is
    a parsed dict and ascii_map is None.

    Falls back to returning the raw string if decoding fails at any layer.
    """
    try:
        wrapper = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw, None
    inner = wrapper.get("result") if isinstance(wrapper, dict) else None
    if not isinstance(inner, str):
        return wrapper, None
    body, sep, ascii_map = inner.partition("\n\nASCII_MAP:")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return inner, None
    return payload, (ascii_map if sep else None)


def decode_kaetram_tool_output(raw: str) -> tuple[dict | str, str | None]:
    """Decode an opencode-style tool output string (no `{"result": ...}` wrapper).

    Kaetram tools return JSON; observe additionally suffixes `\\n\\nASCII_MAP:<grid>`.
    Returns (payload, ascii_map). Falls back to (raw, None) if JSON decode fails.
    """
    if not isinstance(raw, str):
        return raw, None
    body, sep, ascii_map = raw.partition("\n\nASCII_MAP:")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return raw, None
    return payload, (ascii_map if sep else None)


# ── Iteration ────────────────────────────────────────────────────────────────

@dataclass
class ToolCall:
    idx: int                   # ordinal position in the session (0-based)
    line_no: int               # 1-based line number in the log
    name: str                  # e.g. "mcp__kaetram__gather"
    short_name: str            # "gather"
    input: dict
    tool_use_id: str
    thinking: str | None = None    # from the same assistant turn, if any
    text: str | None = None        # ditto
    result_payload: object | None = None
    result_ascii_map: str | None = None
    result_error: str | None = None
    result_raw: str | None = None  # if decode failed

    @property
    def is_observe(self) -> bool:
        return self.short_name == "observe"

    @property
    def is_error(self) -> bool:
        if self.result_error:
            return True
        if isinstance(self.result_payload, dict):
            return bool(self.result_payload.get("error"))
        return False


@dataclass
class SessionView:
    log_path: Path
    meta: dict = field(default_factory=dict)
    tool_calls: list[ToolCall] = field(default_factory=list)
    rate_limit_events: int = 0
    result_summary: dict | None = None
    init_info: dict | None = None
    n_assistant: int = 0
    n_user: int = 0
    n_thinking: int = 0
    n_text: int = 0


def iter_lines(log_path: Path) -> Iterable[tuple[int, dict]]:
    with log_path.open() as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield i, json.loads(line)
            except json.JSONDecodeError:
                continue


def parse_session(log_path: Path) -> SessionView:
    """Walk a session log once and produce a structured view.

    Pairs assistant tool_use blocks with the matching user tool_result by id.
    Decodes each tool_result. Carries thinking/text from the same assistant
    turn onto the tool call (often multiple tool_use per assistant turn — we
    attach the thinking/text to ALL of them for simplicity).
    """
    sv = SessionView(log_path=log_path, meta=session_meta(log_path))
    pending: dict[str, ToolCall] = {}    # tool_use_id -> ToolCall
    idx = 0
    # Each assistant message holds ONE block type (thinking | text | tool_use),
    # so we track the most-recent thinking/text across messages and consume
    # them when a tool_use arrives.
    pending_thinking: str | None = None
    pending_text: str | None = None

    for line_no, rec in iter_lines(log_path):
        rtype = rec.get("type")
        if rtype == "system" and rec.get("subtype") == "init":
            sv.init_info = {
                k: rec.get(k) for k in ("model", "session_id", "tools", "mcp_servers")
            }
            continue
        if rtype == "rate_limit_event":
            # Claude Code emits one of these per API call as a quota-tracking
            # header. status="allowed" is informational (not a throttle) and
            # would otherwise drown the count. Only tally events that
            # actually signal throttling or warning-level utilization.
            info = rec.get("rate_limit_info") or {}
            if info.get("status") in ("allowed_warning", "rate_limited", "blocked"):
                sv.rate_limit_events += 1
            continue
        if rtype == "result":
            sv.result_summary = {
                k: rec.get(k) for k in (
                    "total_cost_usd", "num_turns", "duration_ms",
                    "duration_api_ms", "is_error", "stop_reason",
                    "terminal_reason", "usage", "modelUsage",
                )
            }
            continue
        if rtype == "assistant":
            sv.n_assistant += 1
            content = rec.get("message", {}).get("content", []) or []
            for blk in content:
                btype = blk.get("type")
                if btype == "thinking":
                    sv.n_thinking += 1
                    pending_thinking = blk.get("thinking")
                elif btype == "text":
                    sv.n_text += 1
                    pending_text = blk.get("text")
                elif btype == "tool_use":
                    name = blk.get("name", "")
                    short = name.split("__")[-1] if "__" in name else name
                    tc = ToolCall(
                        idx=idx,
                        line_no=line_no,
                        name=name,
                        short_name=short,
                        input=blk.get("input", {}) or {},
                        tool_use_id=blk.get("id", ""),
                        thinking=pending_thinking,
                        text=pending_text,
                    )
                    idx += 1
                    if tc.tool_use_id:
                        pending[tc.tool_use_id] = tc
                    sv.tool_calls.append(tc)
                    pending_thinking = None
                    pending_text = None
            continue
        if rtype == "user":
            sv.n_user += 1
            content = rec.get("message", {}).get("content", []) or []
            for blk in content:
                if blk.get("type") != "tool_result":
                    continue
                tid = blk.get("tool_use_id")
                tc = pending.pop(tid, None) if tid else None
                if not tc:
                    continue
                raw = blk.get("content")
                if isinstance(raw, list):
                    raw = "".join(
                        x.get("text", "") for x in raw if isinstance(x, dict)
                    )
                if not isinstance(raw, str):
                    raw = json.dumps(raw)
                tc.result_raw = raw
                payload, ascii_map = decode_tool_result_content(raw)
                tc.result_ascii_map = ascii_map
                if isinstance(payload, dict):
                    tc.result_payload = payload
                    if payload.get("error"):
                        tc.result_error = str(payload["error"])[:200]
                else:
                    tc.result_payload = payload  # may be str if decode failed
            continue

    return sv


# ── OpenCode harness parser ──────────────────────────────────────────────────
#
# OpenCode JSONL shape (verified 2026-04-26):
#
#   types: text | tool_use | step_start | step_finish
#
#   text:     part = {type:"text", text:<reasoning + spoken text combined>}
#             — Qwen-via-NIM streams reasoning and content into the same text
#               part. We treat it as `thinking` since pre-tool turns are pure
#               reasoning in practice.
#   tool_use: part = {type:"tool", tool:<short_name>, callID, state:{
#                 status:"completed"|"error", input, output|error, time}}
#             — `output` is the raw kaetram tool output string (no {"result":...}
#               wrapper). For observe, suffixed with "\n\nASCII_MAP:<grid>".
#   step_*: ignored.
#
# No rate_limit_event lines, no `result` summary line. Token usage lives on
# step_finish.tokens; we don't aggregate it here.

def parse_session_opencode(log_path: Path) -> SessionView:
    sv = SessionView(log_path=log_path, meta=session_meta(log_path))
    idx = 0
    pending_thinking: str | None = None

    for line_no, rec in iter_lines(log_path):
        rtype = rec.get("type")
        if rtype == "text":
            sv.n_assistant += 1
            sv.n_thinking += 1
            pending_thinking = (rec.get("part") or {}).get("text")
            continue
        if rtype == "tool_use":
            sv.n_user += 1  # treat tool turn as a user-side resolution boundary
            part = rec.get("part") or {}
            state = part.get("state") or {}
            full = part.get("tool", "") or ""
            short = full.split("kaetram_", 1)[-1] if full.startswith("kaetram_") else full
            tc = ToolCall(
                idx=idx,
                line_no=line_no,
                name=full,
                short_name=short,
                input=state.get("input") or {},
                tool_use_id=part.get("callID", ""),
                thinking=pending_thinking,
            )
            idx += 1
            status = state.get("status")
            if status == "error":
                tc.result_error = str(state.get("error", ""))[:200]
            else:
                raw = state.get("output")
                if isinstance(raw, str):
                    tc.result_raw = raw
                    payload, ascii_map = decode_kaetram_tool_output(raw)
                    tc.result_ascii_map = ascii_map
                    tc.result_payload = payload
                    if isinstance(payload, dict) and payload.get("error"):
                        tc.result_error = str(payload["error"])[:200]
            sv.tool_calls.append(tc)
            pending_thinking = None
            continue
        # step_start / step_finish ignored
    return sv


def parse_session_auto(log_path: Path, harness: str | None = None) -> SessionView:
    """Pick the right parser based on harness (from meta.json, or override)."""
    h = harness or session_meta(log_path).get("harness", "claude")
    if h == "opencode":
        return parse_session_opencode(log_path)
    return parse_session(log_path)


# ── Convenience extractors over a parsed session ─────────────────────────────

def latest_observe(sv: SessionView) -> dict | None:
    """Most recent successful observe payload (game state dict)."""
    for tc in reversed(sv.tool_calls):
        if tc.is_observe and isinstance(tc.result_payload, dict) and not tc.is_error:
            return tc.result_payload
    return None


def first_observe(sv: SessionView) -> dict | None:
    for tc in sv.tool_calls:
        if tc.is_observe and isinstance(tc.result_payload, dict) and not tc.is_error:
            return tc.result_payload
    return None


def tool_call_counts(sv: SessionView) -> dict[str, int]:
    out: dict[str, int] = {}
    for tc in sv.tool_calls:
        out[tc.short_name] = out.get(tc.short_name, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def tool_error_counts(sv: SessionView) -> dict[str, tuple[int, int]]:
    """short_name -> (errors, total)."""
    totals: dict[str, int] = {}
    errs: dict[str, int] = {}
    for tc in sv.tool_calls:
        totals[tc.short_name] = totals.get(tc.short_name, 0) + 1
        if tc.is_error:
            errs[tc.short_name] = errs.get(tc.short_name, 0) + 1
    return {k: (errs.get(k, 0), totals[k]) for k in totals}


def deaths(sv: SessionView) -> list[ToolCall]:
    out = []
    for tc in sv.tool_calls:
        if tc.short_name == "respawn":
            out.append(tc)
            continue
        payload = tc.result_payload
        if not isinstance(payload, dict):
            continue
        status = payload.get("status")
        if isinstance(status, dict) and status.get("dead"):
            out.append(tc)
    return out


# ── Run-level view ───────────────────────────────────────────────────────────

@dataclass
class RunView:
    """A run is one orchestrate.py launch — `runs/run_<EST_TS>/` per agent."""
    agent_dir: Path
    run_dir: Path
    meta: dict = field(default_factory=dict)
    session_paths: list[Path] = field(default_factory=list)

    @property
    def run_id(self) -> str:
        return self.meta.get("run_id") or self.run_dir.name

    @property
    def started_at(self) -> datetime | None:
        s = self.meta.get("started_at")
        if not s:
            return None
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            return None

    @property
    def last_activity(self) -> datetime | None:
        if not self.session_paths:
            return self.started_at
        latest_mtime = max(p.stat().st_mtime for p in self.session_paths)
        return datetime.fromtimestamp(latest_mtime, tz=_EST)

    @property
    def duration_s(self) -> float | None:
        a, b = self.started_at, self.last_activity
        if not a or not b:
            return None
        return (b - a).total_seconds()


def parse_run(agent_dir: Path, run_dir: Path) -> RunView:
    """Build a RunView for one run dir (without parsing every session log)."""
    return RunView(
        agent_dir=agent_dir,
        run_dir=run_dir,
        meta=run_meta(run_dir),
        session_paths=sorted(run_dir.glob("session_*.log")),
    )


def runs_per_agent(include_empty: bool = False) -> list[RunView]:
    """Every run for every agent, sorted by run_id."""
    out: list[RunView] = []
    for agent_dir in list_agent_dirs():
        for run_dir in list_runs(agent_dir):
            rv = parse_run(agent_dir, run_dir)
            if include_empty or rv.session_paths:
                out.append(rv)
    return out


def latest_runs_per_agent() -> list[RunView]:
    """The most recent run per agent (one entry per agent)."""
    out = []
    for agent_dir in list_agent_dirs():
        run = latest_run(agent_dir)
        if run:
            out.append(parse_run(agent_dir, run))
    return out


# ── Error categorization ─────────────────────────────────────────────────────

# Buckets the recurring failure modes we've observed across many runs into
# stable categories. New patterns start as OTHER and can be added explicitly.
ERROR_PATTERNS = [
    ("BFS_NO_PATH",         re.compile(r"No BFS path found", re.I)),
    ("STILL_MOVING",        re.compile(r"still moving toward", re.I)),
    ("NPC_NOT_FOUND",       re.compile(r"No NPC matching .* found nearby", re.I)),
    ("MOB_NOT_FOUND",       re.compile(r"No alive mob matching .* nearby", re.I)),
    ("STATION_UNREACHABLE", re.compile(r"(could not reach|no station found).{0,40}(station|skill)", re.I)),
    ("COMBAT_BLOCKED_WARP", re.compile(r"server blocks warp for", re.I)),
    ("HP_FULL",             re.compile(r"HP is full", re.I)),
    ("MCP_DISCONNECT",      re.compile(r"(mcp error|connection closed|not connected)", re.I)),
    ("SKILL_GATED",         re.compile(r"L\d+ required, you are L\d+", re.I)),
    ("ITEMS_NONE",          re.compile(r"items_gained.{0,5}none|no items collected", re.I)),
]


def categorize_error(text: str) -> str:
    if not text:
        return "OTHER"
    for label, rx in ERROR_PATTERNS:
        if rx.search(text):
            return label
    return "OTHER"


# ── Tier-A adoption signal extractor ─────────────────────────────────────────

@dataclass
class TierASignals:
    """Per-session metrics for Rule-10 / A1 / A2 / A3 / mob-level / Rule-4a uptake."""
    query_quest_calls: int = 0
    query_quest_with_live_gate: int = 0      # response carried live_gate_status
    query_quest_gated_seen: int = 0          # live_gate_status.gated == true
    accept_calls: int = 0                    # interact_npc(accept_quest_offer=True)
    accept_with_prior_query: int = 0         # accept preceded by query_quest in last 3 calls
    bfs_fails: int = 0
    bfs_then_warp: int = 0                   # next action after BFS fail was warp
    bfs_then_navigate: int = 0
    gather_with_gate_explained: int = 0      # response had structured `gate` block
    inv_full_observed: int = 0               # observe carried inventory_summary.full == true
    drops_after_full: int = 0                # drop_item within 3 turns of inv_full
    mob_level_overshoot_attacks: int = 0     # attacked mob >+10 levels above player
    deaths: int = 0
    station_locations_returned: int = 0      # query_quest carried station_locations


def tier_a_signals(sv: SessionView) -> TierASignals:
    s = TierASignals()
    last_inv_full_idx = -10
    last_query_idx = -10
    for tc in sv.tool_calls:
        p = tc.result_payload if isinstance(tc.result_payload, dict) else {}
        n = tc.short_name
        if n == "query_quest":
            s.query_quest_calls += 1
            last_query_idx = tc.idx
            if isinstance(p, dict) and "live_gate_status" in p:
                s.query_quest_with_live_gate += 1
                if (p.get("live_gate_status") or {}).get("gated"):
                    s.query_quest_gated_seen += 1
            if isinstance(p, dict) and p.get("station_locations"):
                s.station_locations_returned += 1
        elif n == "interact_npc" and tc.input.get("accept_quest_offer"):
            s.accept_calls += 1
            if tc.idx - last_query_idx <= 3:
                s.accept_with_prior_query += 1
        elif n == "navigate" and tc.is_error and "BFS" in (tc.result_error or ""):
            s.bfs_fails += 1
            # peek next call
            nxt_idx = tc.idx + 1
            if nxt_idx < len(sv.tool_calls):
                nxt = sv.tool_calls[nxt_idx].short_name
                if nxt == "warp":
                    s.bfs_then_warp += 1
                elif nxt == "navigate":
                    s.bfs_then_navigate += 1
        elif n == "gather":
            if isinstance(p, dict) and p.get("gate"):
                s.gather_with_gate_explained += 1
        elif n == "drop_item":
            if tc.idx - last_inv_full_idx <= 3:
                s.drops_after_full += 1
        elif n == "respawn":
            s.deaths += 1
        elif n == "attack":
            # Mob-level overshoot detection: agent attacked something >+10 levels.
            # Need to find the previous observe to know nearby mob levels.
            mob_name = (tc.input or {}).get("mob_name")
            if mob_name and tc.idx > 0:
                # walk back to find the most recent observe
                for back in range(tc.idx - 1, max(-1, tc.idx - 6), -1):
                    bk = sv.tool_calls[back]
                    if bk.short_name != "observe": continue
                    bp = bk.result_payload if isinstance(bk.result_payload, dict) else {}
                    pls = (bp.get("stats") or {}).get("level", 1)
                    for m in ((bp.get("nearby") or {}).get("mobs") or []):
                        if isinstance(m, dict) and m.get("name") == mob_name and m.get("level"):
                            try:
                                if m["level"] - pls > 10:
                                    s.mob_level_overshoot_attacks += 1
                            except (TypeError, ValueError):
                                pass
                            break
                    break
        if n == "observe" and isinstance(p, dict):
            inv = p.get("inventory_summary") or {}
            if inv.get("full"):
                s.inv_full_observed += 1
                last_inv_full_idx = tc.idx
    return s
