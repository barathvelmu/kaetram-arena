# Session Log
_Keep under 30 lines. Update at end of every session. Most recent first._

---

## 2026-05-06 — r10 SFT dataset rebased on post-Core-3 Claude corpus

`dataset/qwen_sft/` rebuilt from the active corpus only: 5 Claude Sonnet runs × 3 agents = 135 sessions, 9,766 raw OODA turns → **9,352 train + 934 val = 10,286 SFT records**. All on the Core 3 prompt (commit `c4dcf8b` or later) under the current grinder/completionist/explorer_tinkerer archetypes.

**Provenance baked in.** `convert_to_qwen.py` now stamps `metadata.json` with `version`, `built_at`, `prompt_commit`, `source_runs[]`, `session_count`, `raw_turns`, `record_counts`, `core3_only`, `harness`, `personality_labels` on every build. Closes the gap that made "what's in r10?" require grepping research docs.

**Archived.** Old r10 (Apr 18, 25,972 records, pre-Core-3, AGGRESSIVE/METHODICAL/CURIOUS labels, includes Sea/A&C trajectories) + 7 sibling backup builds + the pre-Core-3 `extracted/` tree + `qwen_kto_backup_*` + the `archive/` legacy-agents dir all moved to `dataset/_archive/`. `dataset/` top level shrinks to `raw/`, `qwen_sft/`, `qwen_kto/`, `eval/`, `world_model/`, `DATA.md`, `_archive/`. `parse.py` and `convert_to_qwen.py` don't recurse into `_archive/`, so the live pipeline can't mix archived data into r10.

**Launch-gate concept retired.** `docs/r10_launch_gate.md` deleted — the benchmark is live Core 3 completion in `tests/e2e/quests/`, not an SFT-artifact gate. Auto-tests (`test_dataset_filters`, `test_observe_supervision`, `test_truncation`, `test_loop_noise`, `test_think_roundtrip`) remain as the rebuild guard. 20/20 pass on the new build.

**Docs synced.** `dataset/DATA.md` (layout + stats table), `research/INDEX.md` (Recent Major Changes + Action Items), `research/experiments/training-runs.md` (r10 row + r10 section rewritten), `research/experiments/data-quality.md` (counts + corpus rationale), `research/related-work/{agent-sft-landscape,preference-learning}.md`, `research/paper/contribution.md`, `CLAUDE.md` (SFT pipeline section), `dataset/qwen_sft/README.md` (new file — provenance + rebuild instructions).

---

## 2026-05-06 — r10 raw-corpus boundary: archive everything pre-Core-3 + non-Claude

Drew a hard line for the r10 SFT corpus: **post-Core-3 Claude only**. Moved 291 run dirs (198 pre-Core-3 Claude + 81 opencode + 12 gemini + 6 codex, ≈1.92 GB) into `dataset/raw/_archive/<harness>/agent_N/run_*`. Active corpus is now 12 run dirs (4 per agent × 3) totaling ~190 MB across 4 May-4-or-later Claude runs: `run_20260504_140418`, `run_20260504_172157`, `run_20260504_221206`, `run_20260505_150033`. Also archived 3 stray `runs/state/` Apr-27 relics into `_archive/_legacy_state/` and the `_deleted_browser_run_code_sessions.manifest` scrub artifact.

**Layout invariant:** `parse.py:list_runs()` globs `agent_*/runs/run_*` and does not recurse into `_archive/` — so `analyze.py`, `extract_turns.py`, and `convert_to_qwen.py` are physically unable to mix archived data into the live corpus. To inspect archived runs, parse the `_archive/<harness>/agent_N/run_*` paths directly. Docs synced: `dataset/DATA.md` (layout diagram + archive boundary block), `CLAUDE.md` (log-analysis section), `scripts/log_analysis/README.md` (active-corpus note).

**Latest 6h Claude run:** `run_20260505_150033` (May 5, 3 PM EDT, 3-agent fresh restart). agent_1 (completionist) + agent_2 (explorer) finished all 3 Core 3; agent_0 (grinder) plateaued at Rick's Roll 1/4 again. 5,910 turns, $211 across the three agents.

---

## 2026-05-04 — Core 3 benchmark refactor

**Benchmark scope contracts to Core 3 (Foresting + Herbalist's Desperation + Rick's Roll).** Offline BFS over `Kaetram-Open/.../world.json` confirms two prior benchmark quests are structurally unreachable from a vanilla Mudwich state — the chain gates form circular dependencies / cross-region disjoints that can't be resolved by any in-game tool sequence. Empirical confirmation lined up: in the most recent 6h Claude run, all three agents finished Foresting + Herbalist's + Rick's Roll; the unreachable pair was never accepted by any agent.

**Refactor surface.** ~30 files swept clean across code (`scripts/log_analysis/{parse,analyze}.py` — `CORE_3_QUEST_NAMES`, `core3_total_stage_count`, `/10` denominator), prompts (`system.md`, `game_knowledge.md`, `quest_walkthroughs.json`, all three personalities), tests (deleted A&C/Sea reachability files + the A&C-specific craft_item test; surgical edits to seed/world helpers, dialog flows, unit knowledge tests, navigation regressions), docs (`CLAUDE.md`, `README.md`, log-analysis README + slash command, archive notes in `docs/`), and the research narrative across `INDEX.md`, `contribution.md`, `training-runs.md`, `data-quality.md`, related-work files. Static layer (`test_static_world_connectivity.py`) verifies Core 3 quest coords against `world.json` BFS in <1s.

**Verification:** `analyze.py metrics --stale` against the 6h run reports `core3_stages: 10/10` per agent (Foresting 3 + Herbalist's 3 + Rick's Roll 4); 12/12 static + unit tests green; pytest collection clean (291 tests).

**Next:** open a single PR for the refactor; restart 3 agents on the new prompt for a fresh data-collection run; tier-D prompt-architecture pass once the new run baselines.

---

## 2026-05-01 — Quest knowledge parity (e2e tests as source-of-truth) + unit-test refresh

**E2E reachability now treated as single source of truth for prompt knowledge.** Parallel audits compared `tests/e2e/quests/reachability/test_*_steps.py` against `prompts/game_knowledge.md` + `prompts/quest_walkthroughs.json`; misalignments fixed across blocker and drift tiers. Top items: Rick's Roll cooking-station now points at runtime `query_quest.station_locations.cooking` (was a hard-coded coord that wasn't always reachable); Rick + Lena turn-ins documented as **TWO `interact_npc` calls** (matches R5 test, explains "Thank you, I'm so touched" stuck-state); tomato/paprika coords drifted 1 tile, fixed; Rick stage-2 puzzle decoys expanded from 1 of 7 to all 7 in `tips`; chained-craft + 2-call turn-in caveats added to GAME MECHANICS; Coder's Glitch / Glitch II / Coder's Fallacy walkthrough JSON statuses flipped to `off-limits` with `blocked_reason` populated.

**Unit tests refreshed.** Pre-existing `parent.parent` path bug fixed across 9 unit-test files (`parents[2]`, fallout from the unit/ + e2e/ split). `test_quest_knowledge.py` rewritten against current truth (completable-quest count, Coder chain in off-limits, Herbalist NPC name parity). `convert_to_qwen.py` PERSONALITY_SUFFIXES + AGENT_PERSONALITY_MAP migrated from legacy aggressive/methodical/curious to current grinder/completionist/explorer_tinkerer. `test_prompt_parity.py`, `test_dataset_filters.py`, `test_tool_vocab_drift.py` updated; vocab-drift now scans `mcp_server/tools/` package (was scanning the 19-line stub). Skipif gates added to `test_think_roundtrip` (modal SDK) and `test_truncation` (HF cache). Result: **104 passed, 2 legit skips** across the full unit suite.

**Deferred:** SOTA prompting compliance (6.3K-token bloat, 2× MUST overuse, missing `<verification>` block, rule duplication). Knowledge-parity must be 100% before restructuring the prompt frame.

---

