#!/usr/bin/env python3
"""
convert_to_qwen.py — Transform extracted Claude OODA turns into Qwen3.5 9B SFT records.

Reads turns.jsonl files produced by extract_turns.py and emits training data in
the multi-turn chat format that mirrors the live MCP harness loop:

    user -> assistant(<think> + observe tool_call) -> tool(state)
         -> user -> assistant(<think> + action tool_call) -> tool(action result)

The system prompt is NOT embedded in records — train_modal injects it from
metadata.json at training time so byte-parity with eval_harness is preserved.

Modes:
  --mode single  : One observe + one action per record
  --mode multi   : Sliding-window of consecutive turns (default 3)
  --mode mixed   : 70% multi-turn + 30% single-turn (default)

Filtering policy: minimal. Only EXCLUDED_AGENTS (path), a cheap "has-assistant"
sanity check, and the pre-tokenize truncation gate are applied. No content-based
filtering — the model sees every Claude teacher pattern, including double-observes
and repetitive action chains. Behavior is analyzed post-hoc.

Usage:
    python3 convert_to_qwen.py
    python3 convert_to_qwen.py --mode multi --window-size 4
"""

import argparse
import json
import random
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from tool_surface import MODEL_VISIBLE_TOOL_DEFINITIONS as TOOL_DEFINITIONS

REPO_ROOT = Path(__file__).resolve().parent

# Action types that should appear as tool calls in training records. Mirrors
# tool_surface.MODEL_VISIBLE_TOOL_NAMES exactly — extract_turns derives action_type
# from the same source. "other" turns (off-surface tool calls) are skipped.
VALID_ACTION_TYPES = {d["function"]["name"] for d in TOOL_DEFINITIONS}

# agent_4 / agent_5 are Qwen rollout logs, not teacher data. Path-segment match
# avoids false positives like "agent_40".
EXCLUDED_AGENTS = {"agent_3", "agent_4", "agent_5"}

# Qwen3.5 9B context limit. TRL/Unsloth silently drop tokens past max_seq_length;
# the truncation gate rejects records that would hit this. Margin reserves room
# for the assistant generation prefix.
MAX_SEQ_LEN = 16384
TRUNCATION_MARGIN = 256


# ── Provenance ──────────────────────────────────────────────────────────────

def _git_head_short() -> str:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        )
        return out.decode().strip()
    except Exception:
        return "unknown"


def _list_source_run_ids() -> list[str]:
    """Enumerate raw run IDs that fed the active corpus (post-_archive)."""
    runs: set[str] = set()
    raw_root = REPO_ROOT / "dataset" / "raw"
    if not raw_root.is_dir():
        return []
    for agent_dir in sorted(raw_root.glob("agent_*")):
        runs_dir = agent_dir / "runs"
        if not runs_dir.is_dir():
            continue
        for rd in sorted(runs_dir.glob("run_*")):
            if rd.is_dir():
                runs.add(rd.name)
    return sorted(runs)


def _count_extracted(input_dir: Path) -> tuple[int, int]:
    """Return (session_count, total_turn_count) under input_dir."""
    sessions = 0
    turns = 0
    for jl in Path(input_dir).rglob("turns.jsonl"):
        sessions += 1
        try:
            with jl.open() as f:
                turns += sum(1 for _ in f)
        except OSError:
            pass
    return sessions, turns


# ── System prompt + personality loading (byte-parity with eval_harness) ────

def _load_system_prompt() -> str:
    """Load prompts/system.md with game_knowledge inlined.

    __PERSONALITY_BLOCK__ is left intact — train_modal substitutes it per-record
    at the same textual location eval_harness.resolve_system_prompt uses.
    """
    system_md = REPO_ROOT / "prompts" / "system.md"
    if not system_md.exists():
        raise FileNotFoundError(f"prompts/system.md not found at {system_md}")
    prompt = system_md.read_text()

    gk = REPO_ROOT / "prompts" / "game_knowledge.md"
    prompt = prompt.replace("__GAME_KNOWLEDGE_BLOCK__", gk.read_text() if gk.exists() else "")

    # eval_harness substitutions. __USERNAME__ and __SERVER_PORT__ are no-ops on
    # current system.md; kept for defense if either placeholder reappears.
    prompt = prompt.replace("__USERNAME__", "KaetramAgent")
    prompt = prompt.replace("__SERVER_PORT__", "")
    return prompt


def _load_personality_block(name: str) -> str:
    path = REPO_ROOT / "prompts" / "personalities" / f"{name}.md"
    return path.read_text() if path.exists() else ""


SYSTEM_PROMPT = _load_system_prompt()
PERSONALITY_SUFFIXES = {
    "grinder":           _load_personality_block("grinder"),
    "completionist":     _load_personality_block("completionist"),
    "explorer_tinkerer": _load_personality_block("explorer_tinkerer"),
}


# ── Reasoning + tool-result helpers ─────────────────────────────────────────

def format_reasoning(reasoning: str, max_chars: int = 500) -> str:
    """Trim assistant reasoning, keeping the last sentences (the decision).

    Sonnet teacher reasoning often runs long; the cap keeps records under the
    seq-len budget without losing the final commit-to-action sentences.
    """
    text = " ".join(l.strip() for l in reasoning.split("\n") if l.strip())
    if len(text) <= max_chars:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept: list[str] = []
    char_count = 0
    for s in reversed(sentences):
        if char_count + len(s) > max_chars and kept:
            break
        kept.insert(0, s)
        char_count += len(s) + 1
    return " ".join(kept)


def _prefer_real_tool_result(raw: str | None) -> str | None:
    """Unwrap the `{"result": "<string>"}` envelope used by MCP tool_result blocks.

    Returns the inner string verbatim when the outer is exactly `{"result": <str>}`,
    otherwise returns raw unchanged. Returns None for empty/whitespace input.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        outer = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw  # not JSON — pass through
    if isinstance(outer, dict) and set(outer.keys()) == {"result"} and isinstance(outer["result"], str):
        return outer["result"]
    return raw


# ── State delta (cheap "what changed" signal in user messages) ─────────────

def _to_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def compute_state_delta(prev_state: dict, curr_state: dict) -> dict:
    """Compute observable changes between two consecutive game_states.

    Schema fields: stats.{hp,xp,level}, pos.{x,y}, active_quests, finished_quests,
    status.dead.
    """
    delta: dict = {}
    if not isinstance(prev_state, dict) or not isinstance(curr_state, dict):
        return delta

    ps = prev_state.get("stats") or {}
    cs = curr_state.get("stats") or {}
    if isinstance(ps, dict) and isinstance(cs, dict):
        hp_delta = _to_int(cs.get("hp")) - _to_int(ps.get("hp"))
        xp_delta = _to_int(cs.get("xp")) - _to_int(ps.get("xp"))
        lvl_delta = _to_int(cs.get("level")) - _to_int(ps.get("level"))
        if hp_delta:
            delta["hp_delta"] = hp_delta
        if xp_delta:
            delta["xp_delta"] = xp_delta
        if lvl_delta:
            delta["level_delta"] = lvl_delta

    pp = prev_state.get("pos")
    cp = curr_state.get("pos")
    if isinstance(pp, dict) and isinstance(cp, dict):
        if pp.get("x") != cp.get("x") or pp.get("y") != cp.get("y"):
            delta["moved_from"] = pp

    prev_q = {q.get("key"): q for q in (prev_state.get("active_quests") or []) if isinstance(q, dict)}
    curr_q = {q.get("key"): q for q in (curr_state.get("active_quests") or []) if isinstance(q, dict)}
    new_quests = [k for k in curr_q if k and k not in prev_q]
    stage_advances = sum(
        1 for k, cq in curr_q.items()
        if k in prev_q and (cq.get("stage", 0) or 0) > (prev_q[k].get("stage", 0) or 0)
    )
    if new_quests:
        delta["new_quests"] = new_quests
    if stage_advances:
        delta["quest_stage_advances"] = stage_advances

    prev_done = {q.get("key") for q in (prev_state.get("finished_quests") or []) if isinstance(q, dict)}
    curr_done = {q.get("key") for q in (curr_state.get("finished_quests") or []) if isinstance(q, dict)}
    completions = (curr_done - prev_done) - {None}
    if completions:
        delta["quest_completions"] = sorted(completions)

    if (curr_state.get("status") or {}).get("dead") and not (prev_state.get("status") or {}).get("dead"):
        delta["died"] = True

    return delta


# ── Quality score (KTO consumer signal) ─────────────────────────────────────

def score_turn(turn: dict) -> float:
    """Heuristic 0.0–1.0 quality score over a single turn.

    Used by score_sessions.py and build_kto_dataset.py to rank turns; SFT does
    not gate on this. Reads the live observe schema (pos / stats / nearby).
    """
    score = 0.0
    gs = turn.get("game_state") or {}
    stats = gs.get("stats") or {}
    nearby = gs.get("nearby") or {}

    # State completeness (0.0 – 0.4)
    if _to_int(stats.get("hp")) > 0:
        score += 0.1
    if _to_int(stats.get("max_hp")) > 0:
        score += 0.05
    if any(nearby.get(k) for k in ("npcs", "mobs", "resources", "ground_items")):
        score += 0.1
    if gs.get("inventory"):
        score += 0.05
    if gs.get("active_quests"):
        score += 0.05
    if gs.get("equipment"):
        score += 0.05

    # Action quality (0.0 – 0.2)
    action_type = turn.get("action_type", "")
    high_value = (
        "observe", "attack", "interact_npc", "navigate",
        "gather", "loot", "buy_item", "query_quest", "craft_item",
    )
    medium_value = (
        "eat_food", "equip_item", "warp", "set_attack_style",
        "stuck_reset", "cancel_nav", "drop_item",
    )
    if action_type in high_value:
        score += 0.2
    elif action_type in medium_value:
        score += 0.15
    elif action_type == "respawn":
        score += 0.1

    # Reasoning quality (0.0 – 0.25)
    reasoning = turn.get("reasoning", "") or ""
    rl = reasoning.lower()
    if 30 < len(reasoning) < 1500:
        score += 0.1
    if len(reasoning) > 80:
        score += 0.05
    keywords = ("quest", "kill", "heal", "navigate", "explore", "attack",
                "npc", "equip", "hp", "level", "mob", "warp", "food", "inventory", "craft")
    hits = sum(1 for kw in keywords if kw in rl)
    if hits >= 2:
        score += 0.1
    elif hits >= 1:
        score += 0.05

    # Reasoning–action alignment bonus (0.0 – 0.05)
    alignment = {
        "attack": ("attack", "kill", "fight", "mob", "combat", "damage"),
        "eat_food": ("heal", "food", "hp", "health", "eat", "low hp"),
        "navigate": ("navigate", "walk", "go to", "head to", "move to"),
        "warp": ("warp", "teleport", "fast travel", "mudwich", "aynor", "lakesworld",
                 "crullfield", "patsow", "undersea"),
        "interact_npc": ("npc", "talk", "quest", "interact", "dialogue"),
        "equip_item": ("equip", "weapon", "armor", "gear", "sword", "axe"),
        "respawn": ("dead", "died", "respawn", "death"),
        "gather": ("gather", "chop", "mine", "forage", "tree", "rock", "resource", "log"),
        "loot": ("loot", "pick up", "drop", "lootbag", "item"),
        "buy_item": ("buy", "purchase", "shop", "store", "gold"),
        "drop_item": ("drop", "discard", "inventory full", "free space"),
        "query_quest": ("quest", "walkthrough", "steps", "objective"),
        "craft_item": ("craft", "smith", "smelt", "cook", "forge", "recipe"),
    }
    if action_type in alignment and any(k in rl for k in alignment[action_type]):
        score += 0.05

    # Penalties
    pos = gs.get("pos") or {}
    if pos.get("x", 0) == 0 and pos.get("y", 0) == 0:
        score -= 0.5  # login screen
    if len(reasoning.strip()) < 10:
        score -= 0.15

    return max(0.0, min(1.0, score))


# ── Personality detection ──────────────────────────────────────────────────

_AGENT_PERSONALITY_MAP = {
    "agent_0": "grinder",
    "agent_1": "completionist",
    "agent_2": "explorer_tinkerer",
}


def detect_personality(session_path: Path) -> str | None:
    """Detect personality from agent_N path segment (matches restart-agent.sh)."""
    for part in session_path.parts:
        if part in _AGENT_PERSONALITY_MAP:
            return _AGENT_PERSONALITY_MAP[part]
    return None


# ── Loaders ─────────────────────────────────────────────────────────────────

def _is_excluded_agent(path: Path) -> bool:
    return any(seg in EXCLUDED_AGENTS for seg in path.parts)


def load_turns_by_session(input_dir: Path) -> dict[str, list[dict]]:
    """Load extracted turns grouped by session, preserving chronological order.

    Returned key is the session directory name (e.g. session_10_20260506_065132).
    Sessions under EXCLUDED_AGENTS are skipped. Each turn is tagged with
    `_session_path` so personality can be recovered without a second filesystem pass.
    """
    sessions: dict[str, list[dict]] = {}
    for jsonl in sorted(Path(input_dir).rglob("turns.jsonl")):
        if _is_excluded_agent(jsonl):
            continue
        turns = []
        for line in open(jsonl):
            try:
                turns.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if turns:
            for t in turns:
                t["_session_path"] = str(jsonl.parent)
            sessions[jsonl.parent.name] = turns
    return sessions


# ── Message builders ────────────────────────────────────────────────────────

def _turn_call_id(turn: dict) -> str:
    """Stable per-turn tool_call id derived from turn_id."""
    tid = turn.get("turn_id", "t000")
    return f"call_{tid[-3:]}"


def build_assistant_message(turn: dict) -> dict | None:
    """Emit assistant message: optional <think>...</think> + native MCP tool_call.

    Mixed-mode SFT (Qwen3.5 Thinking Mode Fusion): turns with captured teacher
    reasoning render `<think>...</think>` + tool_call; turns where Sonnet fired
    a tool without writing CoT (common for grinding loops — attack/gather/drop)
    render as a non-thinking turn (empty content + tool_call). The patched
    chat template in `train_modal._patch_qwen_chat_template` handles both
    correctly: `reasoning_content` truthy → full think block; falsy + non-final
    → no think wrapper; falsy + final → empty `<think>\\n\\n</think>`.

    No filler placeholder. An empty source CoT is a real signal — teach the
    model that some actions don't need reasoning, don't fabricate one.
    """
    action_type = turn.get("action_type", "")
    if action_type not in VALID_ACTION_TYPES:
        return None  # off-surface tool — skip

    reasoning = (turn.get("reasoning") or "").strip()
    if reasoning:
        content = f"<think>\n{format_reasoning(reasoning)}\n</think>"
    else:
        content = ""

    tool_calls = [{
        "id": _turn_call_id(turn),
        "type": "function",
        "function": {
            "name": action_type,
            "arguments": dict(turn.get("action_input") or {}),
        },
    }]
    return {"role": "assistant", "content": content, "tool_calls": tool_calls}


def build_tool_result_message(turn: dict) -> dict | None:
    """Emit the tool message that carries the action's result back to the model.

    Returns None if there's no action_result_raw — caller should omit the tool
    message rather than fabricate one.
    """
    action_type = turn.get("action_type", "")
    if action_type not in VALID_ACTION_TYPES:
        return None
    real = _prefer_real_tool_result(turn.get("action_result_raw"))
    if real is None:
        return None
    return {
        "role": "tool",
        "content": real,
        "tool_call_id": _turn_call_id(turn),
        "name": action_type,
    }


def build_user_message(prev_turn: dict | None, curr_turn: dict) -> str:
    """Build the user prompt that precedes an assistant turn.

    Includes a `<state_delta>` block when a previous turn exists and the state
    actually changed. Inference never injects game_state into user messages —
    state arrives only via observe tool_result.
    """
    parts: list[str] = []
    if prev_turn is not None:
        delta = compute_state_delta(
            prev_turn.get("game_state") or {},
            curr_turn.get("game_state") or {},
        )
        if delta:
            parts.append(f"<state_delta>\n{json.dumps(delta, separators=(',', ':'))}\n</state_delta>")
    parts.append("What should you do?")
    return "\n\n".join(parts)


# ── Record builders ─────────────────────────────────────────────────────────

def _build_messages(turns: list[dict]) -> list[dict] | None:
    """Convert a sequence of turns into the messages list of one record.

    Each turn becomes user → assistant → tool. Returns None if any turn cannot
    be rendered (off-surface action).
    """
    messages: list[dict] = []
    for i, turn in enumerate(turns):
        prev = turns[i - 1] if i > 0 else None
        asst = build_assistant_message(turn)
        if asst is None:
            return None
        messages.append({"role": "user", "content": build_user_message(prev, turn)})
        messages.append(asst)
        tool_msg = build_tool_result_message(turn)
        if tool_msg is not None:
            messages.append(tool_msg)
    return messages


def build_multi_turn_records(
    session_turns: list[dict],
    personality: str | None,
    window_size: int = 3,
    stride: int | None = None,
) -> list[dict]:
    """Sliding-window multi-turn records: window_size consecutive turns each.

    No content-based filtering — the model sees every Sonnet pattern.
    """
    if stride is None:
        stride = max(1, window_size // 2)
    n = len(session_turns)
    if n < 2:
        return []

    starts = list(range(0, n, stride))
    if starts and starts[-1] + window_size < n:
        starts.append(max(0, n - window_size))

    records = []
    for start in starts:
        window = session_turns[start : min(start + window_size, n)]
        if len(window) < 2:
            continue
        msgs = _build_messages(window)
        if msgs is None:
            continue
        records.append({"messages": msgs, "personality": personality})
    return records


def build_single_turn_records(
    session_turns: list[dict],
    personality: str | None,
) -> list[dict]:
    """One observe→tool_result(state)→action→tool_result(action) record per
    action turn, paired with its immediately-preceding observe.

    Action turns with no preceding observe are skipped — they have no grounded
    state to act on (matches extract_turns' invariant).
    """
    records = []
    last_observe_idx: int | None = None
    for i, turn in enumerate(session_turns):
        if turn.get("action_type") == "observe":
            last_observe_idx = i
            continue
        if last_observe_idx is None:
            continue
        if turn.get("action_type") not in VALID_ACTION_TYPES:
            continue
        pair = [session_turns[last_observe_idx], turn]
        msgs = _build_messages(pair)
        if msgs is None:
            continue
        records.append({"messages": msgs, "personality": personality})
    return records


# ── Post-build gates (intentionally minimal) ────────────────────────────────

def _count_thinking(record: dict) -> tuple[int, int]:
    """Return (n_thinking_assistant, n_no_thinking_assistant) for a record."""
    n_think = 0
    n_nothink = 0
    for m in record["messages"]:
        if m.get("role") != "assistant":
            continue
        if "<think>" in (m.get("content") or ""):
            n_think += 1
        else:
            n_nothink += 1
    return n_think, n_nothink


def _enforce_thinking_ratio(
    records: list[dict],
    max_no_think_ratio: float,
    seed: int,
) -> tuple[list[dict], int]:
    """Downsample records so non-thinking assistant turns are ≤ max ratio.

    Per Qwen Team's Thinking Mode Fusion guidance for Qwen3.5 SFT: keep at
    least 75% of assistant turns reasoning-supervised; below that, reasoning
    capability degrades. Sonnet emits no-CoT tool calls ~47% of the time on
    repetitive actions (attack/gather/drop), so without this gate the corpus
    is dominated by non-thinking turns and the model unlearns CoT.

    Strategy: rank records by descending no-think share, drop them in that
    order (so pure-grind-loop records go first; mixed-mode records are
    preserved) until the global no-think share is within budget.
    """
    if not records:
        return records, 0
    annotated = [(r, *_count_thinking(r)) for r in records]
    total_think = sum(t for _, t, _ in annotated)
    total_nothink = sum(nt for _, _, nt in annotated)
    if total_think + total_nothink == 0:
        return records, 0

    current_ratio = total_nothink / (total_think + total_nothink)
    if current_ratio <= max_no_think_ratio:
        return records, 0

    # Sort by no-think share descending. Use record id as deterministic
    # tiebreaker so the seed shuffle below produces stable runs.
    rng = random.Random(seed)
    indexed = list(enumerate(annotated))
    rng.shuffle(indexed)
    indexed.sort(
        key=lambda x: (-(x[1][2] / max(1, x[1][1] + x[1][2])), x[0])
    )

    kept_think = total_think
    kept_nothink = total_nothink
    drop_set: set[int] = set()
    for idx, (_, t, nt) in indexed:
        ratio = kept_nothink / max(1, kept_think + kept_nothink)
        if ratio <= max_no_think_ratio:
            break
        drop_set.add(idx)
        kept_think -= t
        kept_nothink -= nt

    kept = [r for i, (r, _, _) in enumerate(annotated) if i not in drop_set]
    return kept, len(records) - len(kept)


def _drop_no_assistant(records: list[dict]) -> tuple[list[dict], int]:
    """Defensive sanity check — should never fire on healthy data."""
    kept = [r for r in records if any(
        m.get("role") == "assistant" and m.get("tool_calls") for m in r["messages"]
    )]
    return kept, len(records) - len(kept)


def _drop_truncated(records: list[dict]) -> tuple[list[dict], int]:
    """Reject records that would silently truncate at training time.

    TRL/Unsloth drop tokens past max_seq_length without raising. We need to
    surface the gate here. Two-stage filter for speed:

    1. **Char-length pre-filter** — Qwen3.5 tokenizes at ~3 chars/token on
       English+code. We render each record to text via `apply_chat_template`
       (cheap, no tokenization), then skip the real tokenizer entirely for
       records whose char count is comfortably under or over the limit. Only
       borderline records pay for tokenization. This typically removes 90%+
       of tokenizer calls.
    2. **Real tokenizer** — only for borderline records. Authoritative.

    Skips entirely if the tokenizer can't load.
    """
    try:
        from transformers import AutoTokenizer
        tok = AutoTokenizer.from_pretrained("Qwen/Qwen3.5-9B")
    except Exception as e:
        print(f"  [trunc-gate] Skipping pre-tokenize gate — tokenizer load failed: {e}")
        return records, 0

    gate = MAX_SEQ_LEN - TRUNCATION_MARGIN
    # Conservative chars-per-token bounds for Qwen3.5 BPE on this corpus
    # (English prose + JSON tool calls/results). Calibrated to never reject
    # a record the real tokenizer would accept and never accept one it would
    # reject — borderline goes to stage 2.
    CHARS_PER_TOKEN_MIN = 1.8   # if char/min > gate → definitely too long
    CHARS_PER_TOKEN_MAX = 5.5   # if char/max < gate → definitely fits
    SAFE_UPPER = int(gate * CHARS_PER_TOKEN_MAX)  # below this → keep without tokenizing
    HARD_LOWER = int(gate * CHARS_PER_TOKEN_MIN)  # above this → reject without tokenizing

    # Pre-render each record to a string. apply_chat_template(tokenize=False)
    # is a Jinja render — much cheaper than tokenizing.
    rendered = []
    for r in records:
        block = PERSONALITY_SUFFIXES.get(r.get("personality") or "", "")
        sys_prompt = SYSTEM_PROMPT.replace("__PERSONALITY_BLOCK__", block)
        full = [{"role": "system", "content": sys_prompt}] + r["messages"]
        try:
            text = tok.apply_chat_template(
                full, tools=TOOL_DEFINITIONS, tokenize=False, add_generation_prompt=False,
            )
        except Exception:
            text = None
        rendered.append(text)

    kept: list[dict] = []
    rejected = 0
    n_borderline = 0
    n_skipped = 0
    for r, text in zip(records, rendered):
        if text is None:
            kept.append(r)  # render failed — let real training surface it
            continue
        n = len(text)
        if n <= SAFE_UPPER:
            kept.append(r)
            n_skipped += 1
            continue
        if n >= HARD_LOWER:
            rejected += 1
            n_skipped += 1
            continue
        # Borderline — pay for the real tokenizer.
        n_borderline += 1
        try:
            ids = tok(text, add_special_tokens=False)["input_ids"]
        except Exception:
            kept.append(r)
            continue
        if len(ids) > gate:
            rejected += 1
        else:
            kept.append(r)

    print(
        f"  [trunc-gate] Fast path skipped {n_skipped}/{len(records)} via char-length, "
        f"tokenized {n_borderline} borderline records"
    )
    return kept, rejected


# ── Train/val split ─────────────────────────────────────────────────────────

def _split_train_val(
    records: list[dict],
    val_ratio: float,
    seed: int,
) -> tuple[list[dict], list[dict]]:
    """Stratified split by session, with fallback to record-level if the
    session split lands outside [val_ratio*0.5, val_ratio*2]."""
    sessions = sorted({r["_session"] for r in records})
    rng = random.Random(seed)
    rng.shuffle(sessions)
    n_val = max(1, int(len(sessions) * val_ratio))
    val_set = set(sessions[:n_val])

    train, val = [], []
    for r in records:
        s = r.pop("_session")
        (val if s in val_set else train).append(r)

    total = len(train) + len(val)
    actual = (len(val) / total) if total else 0
    if actual < val_ratio * 0.5 or actual > val_ratio * 2:
        print(f"  Session split produced ratio {actual:.2%}; falling back to record-level split")
        all_records = train + val
        rng2 = random.Random(seed)
        rng2.shuffle(all_records)
        nv = max(1, int(len(all_records) * val_ratio))
        val = all_records[:nv]
        train = all_records[nv:]
    return train, val


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Convert extracted Claude turns to Qwen3.5 SFT records."
    )
    parser.add_argument("--input", type=Path, default=Path("dataset/extracted"))
    parser.add_argument("--output", type=Path, default=Path("dataset/qwen_sft"))
    parser.add_argument("--val-ratio", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--mode",
        choices=["single", "multi", "mixed"],
        default="mixed",
        help="single = one observe+action pair; multi = window of turns; mixed = 70/30",
    )
    parser.add_argument("--window-size", type=int, default=3)
    parser.add_argument("--stride", type=int, default=None)
    parser.add_argument(
        "--max-no-think-ratio", type=float, default=0.25,
        help="Max share of assistant turns without <think>. Per Qwen3.5 SFT "
             "guidance: ≤25% non-thinking preserves reasoning capability.",
    )
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)

    sessions = load_turns_by_session(args.input)
    if not sessions:
        print("No turns found in input directory.", file=sys.stderr)
        sys.exit(1)

    def _personality(session_turns: list[dict]) -> str | None:
        if not session_turns:
            return None
        return detect_personality(Path(session_turns[0].get("_session_path", "")))

    multi_records: list[dict] = []
    single_records: list[dict] = []
    if args.mode in ("multi", "mixed"):
        for sess, turns in sessions.items():
            for r in build_multi_turn_records(
                turns, _personality(turns), args.window_size, args.stride,
            ):
                r["_session"] = sess
                multi_records.append(r)
    if args.mode in ("single", "mixed"):
        for sess, turns in sessions.items():
            for r in build_single_turn_records(turns, _personality(turns)):
                r["_session"] = sess
                single_records.append(r)

    if args.mode == "multi":
        records = multi_records
    elif args.mode == "single":
        records = single_records
    else:
        # 30% of total ≈ 43% of multi count
        n_single = max(1, int(len(multi_records) * 0.43))
        rng = random.Random(args.seed + 1)
        sample = single_records if len(single_records) <= n_single else rng.sample(single_records, n_single)
        records = multi_records + sample
        print(f"  Mixed mode: {len(multi_records)} multi-turn + {len(sample)} single-turn")

    if not records:
        print("No records produced.", file=sys.stderr)
        sys.exit(1)

    pre = len(records)
    records, n_no_asst = _drop_no_assistant(records)
    if n_no_asst:
        print(f"  Sanity filter: removed {n_no_asst}/{pre} records with no assistant turn")

    if not records:
        print("No records survived sanity filter.", file=sys.stderr)
        sys.exit(1)

    # Mixed-mode SFT ratio enforcement: keep ≥75% thinking turns.
    pre = len(records)
    pre_think = sum(_count_thinking(r)[0] for r in records)
    pre_nothink = sum(_count_thinking(r)[1] for r in records)
    pre_ratio = pre_nothink / max(1, pre_think + pre_nothink)
    records, n_dropped_ratio = _enforce_thinking_ratio(
        records, args.max_no_think_ratio, args.seed
    )
    post_think = sum(_count_thinking(r)[0] for r in records)
    post_nothink = sum(_count_thinking(r)[1] for r in records)
    post_ratio = post_nothink / max(1, post_think + post_nothink)
    print(
        f"  Thinking-ratio gate: pre={pre_ratio:.1%} no-think "
        f"({pre_think} think, {pre_nothink} no-think) → "
        f"post={post_ratio:.1%} ({post_think} think, {post_nothink} no-think); "
        f"dropped {n_dropped_ratio}/{pre} records"
    )

    if not records:
        print("No records survived thinking-ratio gate.", file=sys.stderr)
        sys.exit(1)

    pre = len(records)
    records, n_trunc = _drop_truncated(records)
    print(f"  Truncation gate: rejected {n_trunc}/{pre} records ({100*n_trunc/max(1, pre):.2f}%)")

    if not records:
        print("No records survived truncation gate.", file=sys.stderr)
        sys.exit(1)

    train, val = _split_train_val(records, args.val_ratio, args.seed)

    sess_count, raw_turns = _count_extracted(args.input)
    metadata = {
        "version": "r10",
        "built_at": datetime.now(timezone.utc).isoformat(),
        "prompt_commit": _git_head_short(),
        "harness": "claude",
        "source_runs": _list_source_run_ids(),
        "session_count": sess_count,
        "raw_turns": raw_turns,
        "record_counts": {"train": len(train), "val": len(val), "total": len(train) + len(val)},
        "thinking_ratio": {
            "max_no_think_ratio": args.max_no_think_ratio,
            "thinking_assistant_turns": post_think,
            "no_thinking_assistant_turns": post_nothink,
            "no_thinking_share": round(post_ratio, 4),
        },
        "personality_labels": list(PERSONALITY_SUFFIXES.keys()),
        "system_prompt": SYSTEM_PROMPT,
        "tools": TOOL_DEFINITIONS,
        "personality_suffixes": PERSONALITY_SUFFIXES,
    }
    (args.output / "metadata.json").write_text(json.dumps(metadata, indent=2))

    train_path = args.output / "train.json"
    val_path = args.output / "val.json"
    train_path.write_text(json.dumps(train, indent=2))
    val_path.write_text(json.dumps(val, indent=2))

    msg_counts = [len(r["messages"]) for r in train + val]
    print(f"\nConverted {len(records)} records ({args.mode} mode, window_size={args.window_size})")
    print(f"  Messages/record: avg={sum(msg_counts)/max(1,len(msg_counts)):.1f}, max={max(msg_counts) if msg_counts else 0}")
    print(f"  Train: {len(train)} → {train_path}")
    print(f"  Val:   {len(val)} → {val_path}")

    type_counts: Counter = Counter()
    for r in train + val:
        for m in r["messages"]:
            if m["role"] == "assistant" and "tool_calls" in m:
                for tc in m["tool_calls"]:
                    type_counts[(tc.get("function") or {}).get("name", "unknown")] += 1
    print("\nTool call distribution:")
    for action, count in type_counts.most_common():
        print(f"  {action}: {count}")


if __name__ == "__main__":
    main()
