# Data Quality

How raw Claude gameplay sessions became clean SFT training data. Documents every filter, threshold, and quality gate in the pipeline, with before/after metrics.

---

## Pipeline Overview

```
640 raw logs (agents 0-2 on VM, as of April 12 — no new Claude data since r7)
  → extract_turns.py (OODA turn extraction)
    → 575 extracted session dirs
      → 14,091 turns
  → convert_to_qwen.py (quality scoring + format conversion)
    → 6,423 train / 646 val (7,069 total Qwen3.5 9B SFT records, ~23.7M tokens)
```

Previous pipeline state (April 8): 509 raw → 395 extracted → 3,957/488 (4,445 total). The r7 rebuild (April 9) re-extracted all sessions and produced ~62% more data.

**Verified Apr 12 (direct VM inspection):** 640 raw logs on disk, 650 extracted sessions. Zero new Claude sessions since r7 extraction — all 26 post-Apr-9 logs are Gemini (excluded by `INCLUDED_HARNESSES = {"claude", "unknown"}`). The "65 pending" figure in earlier docs was incorrect (subagent reading stale docs, not the filesystem). r8 trains on the identical 7,069-record dataset as r7. The r8 improvement comes entirely from the loss masking fix.

---

## Exclusions (What Got Removed Entirely)

### Agent exclusions
- **Agent 3 (EFFICIENT):** Dropped April 3. 45% click_tile rate, lowest level reached (37 vs 57-73 for others). Broken behavior — personality prompt created a prep loop where agent alternated between "should I gather food?" and "should I fight?" without doing either. (KAE-1)
- **Agent 4 (Codex):** 39 dead sessions, all stubs. Codex harness had early MCP connection issues. Raw data deleted from VM.
- **Pre-March 28 data:** Before personality prompts were dialed in. METHODICAL was especially contaminated (had a catch-22 food-before-ACCEPT gate that deadlocked quest progression).

### Date cutoff
March 28, 2026. Personalities fully stable from this date. Earlier data from agents 0-2 kept but de-prioritized.

---

## Filters Applied (r5 rebuild, April 4)

### 1. click_tile filter
**Before:** 37.9% of multi-turn window actions were blind click_tile calls with no reasoning.
**After:** 4.7%.
**How:** Removed turns where action = `click_tile(x, y)` AND reasoning < 20 chars. These were mechanical "click somewhere" turns with no decision-making.
**Why it matters:** click_tile is the fallback action — agent clicks a pixel coordinate when it doesn't know what tool to use. Training on this teaches the model to give up instead of reasoning.

### 2. Repetitive loop filter
**Before:** 23% of turns were part of 3+ consecutive identical actions (e.g., `navigate(188, 157)` repeated 5 times).
**After:** 0.2%.
**How:** Detect runs of 3+ identical (action_type, arguments) tuples. Score down the entire run to 0.05 (below min_score threshold).
**Why it matters:** Agent gets stuck against walls or in combat loops. These turns contain no new reasoning — just repeated attempts at the same failed action.

### 3. Reasoning trimming
**Before:** Avg 1,654 chars, some over 5,000.
**After:** Avg 426 chars, max 800.
**How:** Trim to 500 chars in convert_to_qwen.py, prioritizing last 2-3 sentences (the decision) via reversed sentence iteration.
**Why it matters:** Claude's extended thinking produces long reasoning chains. Most of it is restating the game state. The decision (last 2-3 sentences) is what matters for distillation. RAG-MCP (arxiv 2505.03275) confirms reasoning quality degrades when context > 3K tokens.

### 4. Agent 3/4 code-level exclusion
**How:** `EXCLUDED_AGENTS` set in `extract_turns.py`. Skips agent_3 and agent_4 directories entirely. Raw data deleted from VM for agent_4.
**Why separate from date cutoff:** Agent 3 (EFFICIENT) produced data after March 28 but the personality itself was broken. Code-level exclusion is more reliable than date filtering.

### 5. Desert quest waste filter
**How:** Turns where agent repeatedly navigates to x=770-790 and reasoning mentions "wife" or "stuck" are scored down. The Wife NPC was unreachable due to a wrong door coordinate (194,218 = Sorcerer, not Wife at 310,264).
**Fix applied:** Correct coordinates added to game_knowledge.md on April 2. Filter catches legacy data.

---

## Quality Scoring (convert_to_qwen.py)

Each turn is scored 0.0-1.0 on three axes:

| Axis | Weight | What it measures |
|------|--------|-----------------|
| State completeness | 0.4 | Does game state have player_position, player_stats, nearby_entities? |
| Action quality | 0.3 | MCP tool call (0.3) > click_tile (0.05) > no action (0) |
| Reasoning quality | 0.3 | Length (10-500 chars optimal), game keyword presence, no hallucination markers |

**Bonuses and penalties:**
- +0.05 alignment bonus: reasoning mentions action keywords (e.g., reasoning says "attack" + action is `attack(Rat)`)
- -0.10 mismatch penalty: reasoning says "heal" but action is "attack"
- -0.50 login screen: player at (0,0)
- -0.15 empty reasoning: < 10 chars

**Threshold:** `--min-score 0.3` (default). Turns below this are dropped.

---

## Final Dataset Composition (r7 rebuild, April 9)

| Metric | Value |
|--------|-------|
| Train records | 6,423 |
| Val records | 646 |
| Total | 7,069 |
| Split method | 90/10 stratified by session |
| navigate | 27.7% |
| attack | 14.9% |
| cancel_nav | 10.7% |
| interact_npc | 10.7% |
| warp | 9.4% |
| stuck_reset | 7.5% |
| move | 7.1% |
| click_tile | 3.9% |

**Source data (verified Apr 12 via direct VM inspection):**

| Agent | Total logs | Claude logs | Gemini | Codex | Extracted |
|-------|-----------|-------------|--------|-------|-----------|
| agent_0 (AGGRESSIVE) | 220 | 200 | 12 | 8 | 218 |
| agent_1 (METHODICAL) | 213 | 195 | 11 | 7 | 217 |
| agent_2 (CURIOUS) | 207 | 188 | 10 | 9 | 215 |
| **Total** | **640** | **583** | **33** | **24** | **650** |

Only the 583 claude logs feed into training. Gemini/Codex are collected for comparison but excluded via `INCLUDED_HARNESSES = {"claude", "unknown"}` in `convert_to_qwen.py`. The 650 extracted count slightly exceeds 583 claude logs because extraction runs on all harnesses — the harness filter applies at the convert step.

**Personality split (within claude training data):**
- Agent 0 (AGGRESSIVE): ~39% of dataset, combat-heavy sessions
- Agent 1 (METHODICAL): ~31% of dataset, quest-focused (April 3+ only — pre-April 3 catch-22 prompt)
- Agent 2 (CURIOUS): ~29% of dataset, exploration-heavy sessions

**Previous builds for reference:**
| Build | Train | Val | Total | Notes |
|-------|-------|-----|-------|-------|
| r5 (Apr 4) | 3,853 | 465 | 4,318 | First quality-filtered dataset |
| Apr 5 rebuild | 3,957 | 488 | 4,445 | +127 records, click_tile 5.6% |
| r7 (Apr 9) | 6,423 | 646 | 7,069 | +62% data, chat template fix, personality labels |

**r7-specific improvements:**
- Chat template fix (QwenLM/Qwen3#1831): `<think>` reasoning preserved in all assistant turns, not just the last
- Personality labels: every record tagged with personality (was None for all r6 records)
- click_tile rate down to 3.9% (from 5.6% in previous rebuild)

---

## Known Remaining Issues

1. **Training on game state format:** Loss masking (r4+) handles this, but early runs (r1-r3) trained on everything.
2. **Personality imbalance:** AGGRESSIVE produces more combat turns, CURIOUS more NPC interactions. Stratified split by session helps but doesn't guarantee action-type balance.
3. **Session length bias:** Long sessions (100+ turns) dominate the dataset. Short sessions (< 20 turns) are often crashes or rate-limit kills.
4. **Qwen tokenizer mismatch:** Qwen3.5 and Qwen3-VL share a base but have different special tokens. Training uses Qwen3.5 tokenizer; must match at inference.
5. **No new Claude data since r7:** As of April 12, all 26 logs collected after r7 extraction (Apr 9) are Gemini — zero new Claude sessions. The next SFT rebuild requires a new Claude data collection run to meaningfully grow the dataset. r8 trains on the same 7,069 records as r7.
6. **accept_quest underrepresented:** Only 8 `accept_quest` actions in the full 7,069-record dataset despite active questing in logs. Likely a conversion/filter issue — `interact_npc` auto-accepts most quests, so explicit `accept_quest` calls are rare. May not be a bug.
7. **Multi-harness data exclusion:** Codex and Gemini harness logs are collected but excluded from Qwen SFT training via `INCLUDED_HARNESSES` filter in `convert_to_qwen.py`. Only Claude data trains the student model.

---

## Research References

- LIMA (arxiv 2305.11206): 1,000 clean examples match 50K+ noisy for instruction following
- Structured Agent Distillation (arxiv 2505.13820): Loss masking = +8pp task success
- RAG-MCP (arxiv 2505.03275): Reasoning degrades above ~19 tools and ~3K token prompts
