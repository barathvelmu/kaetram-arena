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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

REPO = Path(__file__).resolve().parents[2]
RAW_DIR = REPO / "dataset" / "raw"


# ── Discovery ────────────────────────────────────────────────────────────────

def list_agent_dirs() -> list[Path]:
    return sorted(p for p in RAW_DIR.glob("agent_*") if p.is_dir())


def latest_session_log(agent_dir: Path) -> Path | None:
    """Return the most recently modified .log under agent_dir/logs/, or None."""
    logs = list((agent_dir / "logs").glob("session_*.log"))
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
