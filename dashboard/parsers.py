"""JSONL session log parsers for Claude Code session logs."""

import json
from dashboard.constants import sanitize


def parse_session_log(filepath):
    """Parse a Claude Code JSONL session log. Returns dict with events, turn, cost, tokens, model, duration."""
    events = []
    turn = 0
    cost_usd = 0
    model = ""
    tokens = {"input": 0, "output": 0, "cache_create": 0, "cache_read": 0}
    last_context = 0
    seen_msg_ids = set()
    duration_ms = 0
    num_turns = 0

    try:
        with open(filepath) as fh:
            for line in fh:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue

                t = obj.get("type", "")

                if t == "assistant":
                    msg = obj.get("message", {})
                    if not model:
                        model = msg.get("model", "")
                    msg_id = msg.get("id", "")
                    if msg_id and msg_id not in seen_msg_ids:
                        seen_msg_ids.add(msg_id)
                        usage = msg.get("usage", {})
                        tokens["output"] += usage.get("output_tokens", 0)
                        tokens["cache_create"] += usage.get("cache_creation_input_tokens", 0)
                        tokens["cache_read"] += usage.get("cache_read_input_tokens", 0)
                        tokens["input"] += usage.get("input_tokens", 0)
                        last_context = (usage.get("input_tokens", 0)
                            + usage.get("cache_creation_input_tokens", 0)
                            + usage.get("cache_read_input_tokens", 0))
                    contents = msg.get("content", [])
                    for c in contents:
                        ct = c.get("type", "")
                        if ct == "tool_use":
                            tool = c.get("name", "unknown")
                            tool_display = tool.replace("mcp__playwright__", "pw:")
                            inp = c.get("input", {})
                            summary = ""
                            detail = ""
                            if "code" in inp:
                                detail = inp["code"][:500]
                                code = inp["code"][:120]
                                summary = code.split("return ")[1].split("'")[1] if "return '" in code else code[:80]
                            elif "command" in inp:
                                summary = inp["command"][:80]
                                detail = inp["command"]
                            elif "url" in inp:
                                summary = inp["url"][:80]
                                detail = inp["url"]
                            elif "file_path" in inp:
                                summary = inp["file_path"].split("/")[-1]
                                detail = inp["file_path"]
                            elif "query" in inp:
                                summary = inp["query"][:80]
                                detail = inp.get("query", "")
                            elif "path" in inp:
                                summary = str(inp["path"])[:80]
                            elif "pattern" in inp:
                                summary = inp["pattern"][:80]
                                detail = json.dumps(inp, indent=2)[:500]
                            elif "text" in inp:
                                summary = inp["text"][:80]
                            elif inp:
                                parts = [f"{k}={str(v)[:30]}" for k, v in list(inp.items())[:3]]
                                summary = " ".join(parts)
                            turn += 1
                            events.append({
                                "turn": turn, "type": "tool",
                                "tool": tool_display,
                                "tool_full": tool,
                                "summary": sanitize(summary),
                                "detail": sanitize(detail),
                                "id": c.get("id", ""),
                            })
                        elif ct == "text":
                            text = c.get("text", "")
                            if text.strip():
                                events.append({"turn": turn, "type": "text", "text": sanitize(text)})
                        elif ct == "thinking":
                            thinking = c.get("thinking", "")
                            if thinking.strip():
                                events.append({"turn": turn, "type": "thinking", "text": sanitize(thinking)})

                elif t == "result":
                    cost_usd = obj.get("total_cost_usd", 0)
                    duration_ms = obj.get("duration_ms", 0)
                    num_turns = obj.get("num_turns", 0)

    except Exception:
        pass

    return {
        "events": events,
        "turn": turn,
        "cost_usd": round(cost_usd, 4),
        "model": model,
        "tokens": {
            "input": tokens["input"],
            "output": tokens["output"],
            "cache_create": tokens["cache_create"],
            "cache_read": tokens["cache_read"],
            "context": last_context,
            "total": last_context + tokens["output"],
        },
        "duration_ms": duration_ms,
        "num_turns": num_turns,
    }


def quick_session_summary(filepath):
    """Read cost/turns/model from the result event at end of session log (fast — reads last 10KB only)."""
    cost = 0
    turns = 0
    model = ""
    duration_ms = 0
    try:
        with open(filepath) as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 10240))
            for line in fh:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("type") == "result":
                        cost = obj.get("total_cost_usd", 0)
                        turns = obj.get("num_turns", 0)
                        duration_ms = obj.get("duration_ms", 0)
                        for m in (obj.get("modelUsage") or {}):
                            model = m
                            break
                    elif obj.get("type") == "assistant" and not model:
                        model = obj.get("message", {}).get("model", "")
                except Exception:
                    pass
    except Exception:
        pass
    return {"cost_usd": round(cost, 4), "turns": turns, "model": model, "duration_ms": duration_ms}


def live_session_stats(filepath):
    """Read turn count, context tokens, cost, and model from a session log.

    Single-pass scan of the last ~1MB. Returns metadata only —
    game state is handled separately by game_state module.
    """
    turns = 0
    context_tokens = 0
    output_tokens_total = 0
    model = ""
    cost = 0
    duration_ms = 0
    seen_msg_ids = set()
    try:
        with open(filepath) as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 1048576))
            if size > 1048576:
                fh.readline()
            for line in fh:
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                t = obj.get("type", "")
                if t == "assistant":
                    msg = obj.get("message", {})
                    if not model:
                        model = msg.get("model", "")
                    msg_id = msg.get("id", "")
                    if msg_id and msg_id not in seen_msg_ids:
                        seen_msg_ids.add(msg_id)
                        usage = msg.get("usage", {})
                        inp = usage.get("input_tokens", 0)
                        cache_create = usage.get("cache_creation_input_tokens", 0)
                        cache_read = usage.get("cache_read_input_tokens", 0)
                        out = usage.get("output_tokens", 0)
                        ctx = inp + cache_create + cache_read
                        if ctx > 0:
                            context_tokens = ctx
                        output_tokens_total += out
                    for c in msg.get("content", []):
                        if isinstance(c, dict) and c.get("type") == "tool_use":
                            turns += 1
                elif t == "result":
                    turns = obj.get("num_turns", turns)
                    cost = obj.get("total_cost_usd", 0)
                    duration_ms = obj.get("duration_ms", 0)
                    for m in (obj.get("modelUsage") or {}):
                        model = m
                        break
    except Exception:
        pass
    return {
        "turns": turns,
        "context_tokens": context_tokens,
        "output_tokens": output_tokens_total,
        "model": model,
        "cost_usd": round(cost, 4),
        "duration_ms": duration_ms,
    }
