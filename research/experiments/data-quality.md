# Data Quality

How raw Claude gameplay sessions became clean SFT training data. Documents every filter, threshold, and quality gate in the pipeline, with before/after metrics.

---

## Pipeline Overview

```
509 raw logs (agents 0-2 on VM, as of April 8)
  → extract_turns.py (OODA turn extraction)
    → 395 extracted session dirs (132 + 129 + 134 across agents 0-2)
      ⚠ 114 raw sessions not yet extracted — re-run extract_turns.py
  → convert_to_qwen.py (quality scoring + format conversion)
    → 3,957 train / 488 val (4,445 total Qwen3.5 9B SFT records)
```

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

## Final Dataset Composition (latest rebuild)

| Metric | Value |
|--------|-------|
| Train records | 3,957 |
| Val records | 488 |
| Split method | 90/10 stratified by session |
| Avg reasoning | 423 chars |
| Max reasoning | 800 chars |
| click_tile rate | 5.6% |
| Repetitive rate | 0.3% |
| attack rate | 14.5% |
| navigate rate | 27.9% |
| interact_npc rate | 11.2% |
| Empty reasoning | 0% |

**Source data:**
- Agent 0 (AGGRESSIVE): Level 57-73, combat-heavy sessions
- Agent 1 (METHODICAL): Level 60-71, quest-focused sessions (April 3+ only — pre-April 3 contaminated by catch-22 prompt)
- Agent 2 (CURIOUS): Level 58-70, exploration-heavy sessions

**Interpretation:** the latest rebuild added more usable data but was not uniformly cleaner than the earlier `3853/465` build. Net gain was `+127` records over the previous compiled SFT set, while `click_tile` noise rose slightly (`4.7% -> 5.6%`) and repetitive loops stayed low (`0.2% -> 0.3%`). This is still a usable `r7` foundation, but not a dramatic quality jump.

---

## Known Remaining Issues

1. **Training on game state format:** Loss masking (r4+) handles this, but early runs (r1-r3) trained on everything.
2. **Personality imbalance:** AGGRESSIVE produces more combat turns, CURIOUS more NPC interactions. Stratified split by session helps but doesn't guarantee action-type balance.
3. **Session length bias:** Long sessions (100+ turns) dominate the dataset. Short sessions (< 20 turns) are often crashes or rate-limit kills.
4. **Qwen tokenizer mismatch:** Qwen3.5 and Qwen3-VL share a base but have different special tokens. Training uses Qwen3.5 tokenizer; must match at inference.
5. **New-session marginal quality:** Recent raw Claude logs did add data, but the last rebuild only produced a modest net increase after filtering. More collection helps only if it adds genuinely diverse, higher-signal sessions.

---

## Research References

- LIMA (arxiv 2305.11206): 1,000 clean examples match 50K+ noisy for instruction following
- Structured Agent Distillation (arxiv 2505.13820): Loss masking = +8pp task success
- RAG-MCP (arxiv 2505.03275): Reasoning degrades above ~19 tools and ~3K token prompts
