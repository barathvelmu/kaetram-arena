#!/usr/bin/env python3
"""
extract_turns.py — Post-process Claude Code session logs into clean OODA turns.

Reads Claude Code stream-json logs (assistant/user events with thinking,
text, tool_use, tool_result blocks) and emits one structured turn per
observe and per MCP action call.

Usage:
    python3 extract_turns.py --log-dir logs/ --output-dir dataset/extracted/
    python3 extract_turns.py --log-file logs/session_2_20260319_060749.log
"""

import argparse
import json
import sys
from pathlib import Path

from tool_surface import MODEL_VISIBLE_TOOL_NAMES

# ── Tool surface (single source of truth) ───────────────────────────────────
MCP_OBSERVE = "mcp__kaetram__observe"
MCP_ACTION_NAMES = {
    f"mcp__kaetram__{t}" for t in MODEL_VISIBLE_TOOL_NAMES if t != "observe"
}


# ── Event parsing ──────────────────────────────────────────────────────────
def parse_session(log_path: Path) -> list[dict]:
    """Walk the JSONL log and yield typed events.

    Event kinds:
      {"kind": "thinking", "text": str, "line": int}
      {"kind": "text", "text": str, "line": int}
      {"kind": "tool_use", "name": str, "id": str, "input": dict, "line": int}
      {"kind": "tool_result", "tool_use_id": str, "raw_text": str,
        "structured": str | None, "line": int}

    For tool_result events, `raw_text` is the verbatim string from the
    message-content block, and `structured` is the harness-pre-flattened
    `tool_use_result.structuredContent.result` string when present (it
    avoids the `{"result": "<escaped json>"}` wrapping).
    """
    events: list[dict] = []
    for i, line in enumerate(open(log_path)):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue

        t = rec.get("type")
        if t not in ("assistant", "user"):
            continue

        # Event-level structured tool result (only on user events that report
        # a tool_use_result). Use the pre-flattened result string when present.
        struct_result: str | None = None
        tur = rec.get("tool_use_result") or {}
        if isinstance(tur, dict):
            sc = tur.get("structuredContent") or {}
            r = sc.get("result") if isinstance(sc, dict) else None
            if isinstance(r, str):
                struct_result = r

        msg = rec.get("message") or {}
        for block in msg.get("content", []) or []:
            if not isinstance(block, dict):
                continue
            bt = block.get("type")

            if bt == "thinking":
                events.append({
                    "kind": "thinking",
                    "text": block.get("thinking", "") or "",
                    "line": i,
                })
            elif bt == "text":
                events.append({
                    "kind": "text",
                    "text": block.get("text", "") or "",
                    "line": i,
                })
            elif bt == "tool_use":
                events.append({
                    "kind": "tool_use",
                    "name": block.get("name", "") or "",
                    "id": block.get("id", "") or "",
                    "input": block.get("input", {}) or {},
                    "line": i,
                })
            elif bt == "tool_result":
                content = block.get("content")
                if isinstance(content, str):
                    raw_text = content
                elif isinstance(content, list):
                    # Defensive: harness today emits string, but spec allows list.
                    raw_text = "".join(
                        item.get("text", "")
                        for item in content
                        if isinstance(item, dict)
                    )
                else:
                    raw_text = ""
                events.append({
                    "kind": "tool_result",
                    "tool_use_id": block.get("tool_use_id", "") or "",
                    "raw_text": raw_text,
                    "structured": struct_result,
                    "line": i,
                })
    return events


# ── Game-state + ASCII map extraction ──────────────────────────────────────
_ASCII_MARKER = "\n\nASCII_MAP:"
_STUCK_MARKER = "STUCK_CHECK:"


def _result_payload(structured: str | None, raw_text: str) -> str | None:
    """Return the inner result string (game-state JSON + optional ASCII map).

    Prefers `tool_use_result.structuredContent.result` when present (already
    unwrapped by the harness). Falls back to parsing the `{"result": "..."}`
    wrapper out of the raw block content. Returns None if neither yields a
    usable string.
    """
    if isinstance(structured, str) and structured:
        return structured
    if not raw_text:
        return None
    try:
        outer = json.loads(raw_text)
    except (json.JSONDecodeError, ValueError):
        return raw_text  # already unwrapped or unparseable; treat as-is
    if isinstance(outer, dict):
        inner = outer.get("result")
        if isinstance(inner, str):
            return inner
        if isinstance(inner, dict):
            return json.dumps(inner)
    if isinstance(outer, str):
        return outer
    return None


def extract_game_state(structured: str | None, raw_text: str) -> dict | None:
    """Parse the inner JSON game_state from an observe tool_result.

    Strips the ASCII_MAP suffix before json.loads. Returns None on parse
    failure or empty payload.
    """
    payload = _result_payload(structured, raw_text)
    if not payload:
        return None
    json_part = payload.split(_ASCII_MARKER, 1)[0].strip()
    if not json_part:
        return None
    try:
        gs = json.loads(json_part)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(gs, dict):
        return None
    if gs.get("error"):
        return None
    return gs


def extract_ascii_map(structured: str | None, raw_text: str) -> str:
    """Pull the ASCII map section out of an observe tool_result."""
    payload = _result_payload(structured, raw_text)
    if not payload:
        return ""
    idx = payload.find("ASCII_MAP:")
    if idx < 0:
        return ""
    section = payload[idx + len("ASCII_MAP:") :].strip()
    stuck = section.find(_STUCK_MARKER)
    if stuck >= 0:
        section = section[:stuck].strip()
    return section


# ── Action serialization ───────────────────────────────────────────────────
def action_type_for(tool_name: str) -> str:
    """Map `mcp__kaetram__<X>` → `<X>`. Returns 'other' for unknown names."""
    if tool_name == MCP_OBSERVE:
        return "observe"
    if tool_name in MCP_ACTION_NAMES:
        return tool_name.removeprefix("mcp__kaetram__")
    return "other"


def structured_action(action_type: str, tool_input: dict) -> str:
    """Render the canonical structured-action string for SFT.

    One line per tool, covering all tools in `MODEL_VISIBLE_TOOL_NAMES`.
    """
    if action_type == "observe":
        return "observe()"

    if action_type == "attack":
        return f"attack({tool_input.get('mob_name', tool_input.get('target', '?'))})"
    if action_type == "navigate":
        return f"navigate({tool_input.get('x', '?')}, {tool_input.get('y', '?')})"
    if action_type == "warp":
        loc = tool_input.get("location", "?")
        return f"warp({loc.capitalize() if isinstance(loc, str) else loc})"
    if action_type == "interact_npc":
        return f"interact_npc({tool_input.get('npc_name', '?')})"
    if action_type == "eat_food":
        return f"eat_food(slot={tool_input.get('slot', '?')})"
    if action_type == "buy_item":
        npc = tool_input.get("npc_name", "?")
        item = tool_input.get("item_index", "?")
        count = tool_input.get("count", 1)
        return f"buy_item({npc}, {item}, count={count})"
    if action_type == "equip_item":
        return f"equip_item(slot={tool_input.get('slot', '?')})"
    if action_type == "drop_item":
        return f"drop_item(slot={tool_input.get('slot', '?')})"
    if action_type == "set_attack_style":
        style = tool_input.get("style", "?")
        return f"set_attack_style({style.capitalize() if isinstance(style, str) else style})"
    if action_type == "cancel_nav":
        return "cancel_nav()"
    if action_type == "stuck_reset":
        return "stuck_reset()"
    if action_type == "gather":
        return f"gather({tool_input.get('resource_name', tool_input.get('target', '?'))})"
    if action_type == "loot":
        return "loot()"
    if action_type == "query_quest":
        return f"query_quest({tool_input.get('quest_name', '?')})"
    if action_type == "respawn":
        return "respawn()"
    if action_type == "craft_item":
        skill = tool_input.get("skill", "?")
        recipe = tool_input.get("recipe_key", "?")
        count = tool_input.get("count", 1)
        return f"craft_item({skill}, {recipe}, count={count})"

    return f"{action_type}({json.dumps(tool_input)})"


# ── Turn extraction ────────────────────────────────────────────────────────
def extract_turns(log_path: Path) -> list[dict]:
    """Walk parsed events and emit one turn per observe and per MCP action.

    Reasoning attribution: each turn carries the assistant `thinking` and
    `text` blocks that appeared since the previous emitted turn. The buffer
    clears at every emit, so no two turns ever claim the same reasoning.

    Action turns inherit the most recent observe's `game_state` (the agent
    decides based on what it last saw). Actions that fire before any observe
    are skipped — they have no grounded state.
    """
    events = parse_session(log_path)
    if not events:
        return []

    # Index tool_result events by tool_use_id for fast lookup of action results.
    result_by_id: dict[str, dict] = {
        e["tool_use_id"]: e
        for e in events
        if e["kind"] == "tool_result" and e.get("tool_use_id")
    }

    turns: list[dict] = []
    reasoning_buf: list[str] = []
    last_game_state: dict | None = None
    last_ascii_map: str = ""
    log_stem = log_path.stem

    def _flush_reasoning() -> str:
        text = "\n".join(reasoning_buf).strip()
        reasoning_buf.clear()
        return text

    def _turn_id() -> str:
        return f"{log_stem}_t{len(turns):03d}"

    for ev in events:
        kind = ev["kind"]

        if kind in ("thinking", "text"):
            t = (ev.get("text") or "").strip()
            if t:
                reasoning_buf.append(t)
            continue

        if kind != "tool_use":
            continue

        name = ev.get("name", "")

        if name == MCP_OBSERVE:
            obs_result = result_by_id.get(ev.get("id", ""))
            if obs_result is None:
                # Unmatched observe (truncated session?) — drop.
                continue
            gs = extract_game_state(
                obs_result.get("structured"), obs_result.get("raw_text", "")
            )
            if gs is None:
                continue
            ascii_map = extract_ascii_map(
                obs_result.get("structured"), obs_result.get("raw_text", "")
            )

            turns.append({
                "turn_id": _turn_id(),
                "game_state": gs,
                "ascii_map": ascii_map,
                "reasoning": _flush_reasoning(),
                "action_type": "observe",
                "action_structured": "observe()",
                "action_input": {},
                "action_result_raw": obs_result.get("raw_text", ""),
            })
            last_game_state = gs
            last_ascii_map = ascii_map
            continue

        if name in MCP_ACTION_NAMES:
            if last_game_state is None:
                # Action before any observe — no grounded state to attribute.
                # Drop reasoning along with it (it belongs to the lost action).
                reasoning_buf.clear()
                continue

            action_type = action_type_for(name)
            tool_input = ev.get("input", {}) or {}
            tool_id = ev.get("id", "")
            result = result_by_id.get(tool_id)
            action_result_raw = result.get("raw_text", "") if result else ""

            turns.append({
                "turn_id": _turn_id(),
                "game_state": last_game_state,
                "ascii_map": "",
                "reasoning": _flush_reasoning(),
                "action_type": action_type,
                "action_structured": structured_action(action_type, tool_input),
                "action_input": tool_input,
                "action_result_raw": action_result_raw,
            })
            continue

        # Unknown / non-MCP tool_use — ignore. Reasoning carries forward to
        # whichever turn comes next (defensive; should not happen on current data).

    return turns


# ── CLI ────────────────────────────────────────────────────────────────────
def process_log(log_path: Path, output_dir: Path) -> int:
    """Process a single log file. Returns number of turns extracted."""
    turns = extract_turns(log_path)
    if not turns:
        return 0

    session_dir = output_dir / log_path.stem
    session_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = session_dir / "turns.jsonl"
    with open(jsonl_path, "w") as f:
        for turn in turns:
            f.write(json.dumps(turn, separators=(",", ":")) + "\n")
    return len(turns)


def main():
    parser = argparse.ArgumentParser(
        description="Extract OODA turns from Claude Code session logs"
    )
    parser.add_argument("--log-dir", type=Path, help="Directory of session .log files")
    parser.add_argument("--log-file", type=Path, help="Single log file to process")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("dataset/extracted"),
        help="Output directory (default: dataset/extracted/)",
    )
    args = parser.parse_args()

    if not args.log_dir and not args.log_file:
        parser.error("Provide --log-dir or --log-file")

    args.output_dir.mkdir(parents=True, exist_ok=True)

    logs = [args.log_file] if args.log_file else sorted(args.log_dir.glob("session_*.log"))
    if not logs:
        print("No log files found.", file=sys.stderr)
        sys.exit(1)

    total = 0
    for log_path in logs:
        n = process_log(log_path, args.output_dir)
        if n > 0:
            print(f"  {log_path.name}: {n} turns")
        total += n

    print(f"\nTotal: {total} turns from {len(logs)} logs → {args.output_dir}")


if __name__ == "__main__":
    main()
