# Analysis and action items from Opus 4.7 — v2

**Generated:** 2026-04-16, ~17:00 UTC
**Method:** v1 produced by 10 parallel sub-agents → v1 red-teamed by 7 counter-investigation sub-agents → recommendation from collaborator red-teamed by 3 verification sub-agents → v2 is the synthesis
**Assignees:** Niral, Barath
**Supersedes:** v1 (analysis_opus_4_7.md, since deleted)

---

## TL;DR

r9 SFT is ~50% trained, converging cleanly (eval_loss 1.10 → 0.99), projected complete ~Apr 17 09:00 UTC. **All six declared r9 fixes are verified live in code.** But there is one P0 train/inference mismatch (Hermes tool block) that needs fixing tonight or r9 eval is worthless. Beyond that, the right paper framing is a **three-tier silent failure taxonomy** (training / tools / environment) — and the environment tier is real: 4 of 21 quests are broken at the Kaetram source level, justifying a small upstream PR. Most "data quality crisis" claims (78% no-think, 27-35% loop windows, etc.) **do not survive verification** and should not drive emergency action.

---

## The three-tier silent-failure taxonomy

This is the strongest paper framing we have. It generalizes beyond our specific bugs and gives reviewers a vocabulary that doesn't exist yet:

> 1. **Training-pipeline silent failures** — loss masking flag no-ops, chat template stripping `<think>`, train/inference prompt drift
> 2. **Tool-layer silent failures** — MCP tools returning success-shaped responses on no-op (loot at distance 0, query_quest returning unreachable waypoints)
> 3. **Environment silent failures** — dead quests, missing item entries, undeliverable boss drops, NPC dialogue handshake failures

Combined: **agentic distillation can fail silently at any layer of the stack, and training loss tells you nothing about it**. Each tier requires different diagnostics. We are the first to taxonomize all three.

---

## P0: Hermes block train/inference mismatch — **fix tonight**

This is the single most important finding. **Verified twice independently** with corrected numbers.

**Location:** `play_qwen.py:241-262`
**Block size:** **9,661 chars / 9,661 UTF-8 bytes / 2,711 Qwen tokens** (corrected — earlier estimate of 6,375 chars / 1,687 tokens was wrong; the second subagent reconstructed the block from `mcp.list_tools()` and measured the actual tokenization)

**What it does:** Appends a Hermes-style `<tool_call>`/`<function=>` schema block to the system prompt at inference time. Includes `# Tools\n\nYou have access to the following functions:` header, 21 JSON tool definitions, and an `<IMPORTANT>` reminder block.

**What training saw:** Verified by inspecting `dataset/qwen_sft/metadata.json` (11,382-char system prompt) — **zero occurrences** of `<tool_call>`, `Hermes`, or `# Tools\n\nYou have access to the following functions`. Training prompt has the markdown `<tools>` table from `prompts/system.md` only. `train_modal.py:297-308` explicitly omits `tools=` kwarg from `apply_chat_template`, so Qwen's native tool injection doesn't fire either.

**Result:** r9 is being trained to emit Qwen-native `<|tool_call|>` JSON output, then asked at inference to follow a 2,711-token Hermes-XML contract it has literally never been conditioned on. **This is r8's exact failure mode in a narrower register**, and the very class of bug r9 was supposed to eliminate.

**Two-line fix paths (pick one):**
- **A.** Move the Hermes append into `convert_to_qwen.py:_load_system_prompt()` so it lands in `metadata.system_prompt` at training time.
- **B.** Drop the Hermes append from `play_qwen.py:241-262` entirely and let inference rely on the markdown `<tools>` table plus Qwen's native tool_call output path.

Either works. **B is simpler.** Without one or the other, the r9 eval is structurally meaningless.

---

## Verification matrix: what's real, what's overstated, what's refuted

### From v1 (my own analysis)

| Claim | Verdict | Evidence |
|---|---|---|
| Hermes block train/inference mismatch | **CONFIRMED, bigger than first stated** | 9,661 bytes / 2,711 tokens vs zero in training prompt |
| Chat template patch is brittle string-replace | CONFIRMED | `train_modal.py:198-227`, `serve_modal.py:73-98`, no assert |
| `crossroads` warp doesn't exist | CONFIRMED | Real warps in `world.json`: mudwich/aynor/lakesworld/patsow/crullfield/undersea |
| `codersglitch.json:15` `"noc"` typo | CONFIRMED | TS silently drops unknown key; stage 0 unstartable |
| 4 silent training bugs (r5-r8) all real | CONFIRMED with two narrative tilts | r7 had the chat-template fix already (was first run with patch, not affected); r6-KTO unverified |
| Bike Lyson "no quest, agent confabulation" | **REFUTED** | Snek hunt is REAL — `achievements.json:44-60` `boxingman` achievement: 25 sneks → 2,000 strength XP. Live MongoDB shows all 3 bots have `boxingman: stage 0`. The `interact_npc` handshake fails to advance the achievement chain. |
| Agents plateau L20-25 from quest gating | **REFUTED** | Live MongoDB: all bots completed desertquest, foresting, anvilsechoes. Real bottleneck is exploration heuristics. Bots are L42-68. |
| RAG-MCP "19-tool danger line" | **REFUTED (citation failure)** | Real paper (arXiv 2505.03275) says graceful degradation past ~30 tools, sharp past ~100. The "19" number doesn't appear in the paper. |
| LoRA hyperparams "conservative vs SOTA" | **REFUTED on cited comparators** | Tülu 3 SFT uses LR=5e-6 (full FT, not LoRA). ToolACE uses LR=1e-4 LoRA (matches ours). Real critique: "1 epoch is low, ToolACE used 3" |
| Modal min_containers asymmetry "biases comparison" | REFUTED for measured metrics | Cold start affects latency only; eval metrics are turn-counted |
| Sub-session restart "invalidates i.i.d." | OVERSTATED | Bounded-horizon eval matches production (SWE-Bench, WebArena); real concern is just statistical N |
| Bonferroni correction needed | OVERSTATED | Holm-Bonferroni or BH-FDR is more appropriate; with N=1 there's nothing to correct |
| Human eval of trajectories required | REFUTED | Standard for dialog/instruction; not for game agents |
| 78% of turns no-thinking | **WRONG (measurement bug)** | Real is 36% with-think, not 22% — previous join was too strict |
| Attack-loop drift 14.9% → 41.0% | MISLEADING (apples-to-oranges) | r7 14.9% was post-windowing; raw r7 was 30.1%. Real drift 30.1% → 41.1% |
| Personality differentiation "real on 3 axes" | PARTIALLY confirmed | AGG-METH death gap p=0.007 (significant), AGG-CUR p=0.27 (NOT significant). METH eats more (p=0.002). CUR talks more (p<10⁻⁵). But METH attacks the most — the "AGG attacks more" thesis is wrong |
| Niral "may not have read Barath's strategy docs" | **SPECULATION** | Drop the claim — Niral pulled them via merge, can't verify reading from git |
| Ownership lanes coarse | OVERSTATED | `eval_harness.py` and `serve_modal.py` are Niral's, not Barath's |
| 17 stale remote branches | OVERSTATED | Actually 15, all stale-but-merged |
| `research/` going private as paper concern | OVERSTATED | Standard pre-submission hygiene; commit message says "filter-repo deferred to post-publication" |

### From the collaborator's recommendation

| Claim | Verdict | Evidence |
|---|---|---|
| codersglitch.json typo, codersglitch2 dead-gated | **CONFIRMED** | See above + missing talismans below |
| 7 missing items in `items.json` | **6/7 CONFIRMED**, 1 refuted | `catpet, staff, skeletonkingtalisman, ogrelordtalisman, queenanttalisman, forestdragontalisman` all missing. `smithingboots` is a phantom claim — never referenced anywhere; real anvilsechoes reward is `bronzeboots` which exists |
| Skeleton King drops zero talismans | **CONFIRMED** | `mobs.json:4381-4432` — drops `goldshield` + `royalrapier` only. Same for ogrelord/queenant/forestdragon. Only `spookyskeleton` drops `skeletonkingtalisman`, and that's moot because the item doesn't exist |
| 29/75 NPCs are empty stubs (38.7%) | **CONFIRMED EXACTLY** | Direct count of `npcs.json` |
| shepherdboy + redbikinigirlnpc block royalpet | PARTIALLY OVERSTATED | Both ARE stubs but quest dialogue comes from quest stage data, not NPC entry. Real royalpet blocker is missing `catpet` item |
| 5/21 quests broken source-side (~24%) | **OVERSTATED** | Actual is 4/21 hard-broken (19%) + 1 typo. Audit found no missing-NPC, missing-mob, or missing-questRequirement issues elsewhere |
| Warrior Crabs cave unreachable | **REFUTED** | Cave IS reachable at `(320, 455)`. The "Hermit Crab Warrior" miniboss is defined in `spawns.json:12-40`. The real bug is `query_quest("Sorcery and Stuff")` returning waypoints that are walls — content bug in our walkthrough, not the game |
| 19% of Sonnet turns are fix-loops | **OVERSTATED** | Strict definition (same tool+args, no progress between calls): **6.8%**. Naive (just same tool ≥3 times): 39.6%. The "19%" figure is unreproducible — likely a midpoint of two methodologies |
| 27-35% of multi-turn SFT windows have loop turn | **REFUTED** | Real is **1.1%** under any progress-aware definition. Off by an order of magnitude |
| attacks-per-observe is 2.3, should be 1 | Numerator confirmed (2.20), normative wrong | "Should be 1" is methodology pedantry — `attack` returns rich state blob; rational range is 1.5-3. Concerning would be >5 (acting blind) or <0.5 (over-observing) |
| `loot()` fails silently at distance 0 | **CONFIRMED** | `mcp_game_server.py:1377-1482` — when nothing lootable, returns `{"message": "No items..."}` with no `error` key, same JSON shape as success |
| `drop_item()` fails silently | **PARTIALLY REFUTED** | `mcp_game_server.py:1102-1164` — DOES emit structured error keys (`{"error": "No item in slot N"}`, `{"dropped": false, "error": "..."}`). Caller must inspect body, but it's not silent |
| 78% of Sonnet turns no-thinking | **WRONG** | Real is 64% no-think (36% with-think). Same measurement bug as in v1 |
| 44% attack spam | OVERSTATED | Real is 41%. And not "spam" — attack-dominant sessions have FEWER deaths than population avg (0.33 vs 0.50) — they're successful farming runs |
| 68% interact_npc soft-fail | **OVERSTATED** | Real is 41.8% fail (34.2% unreachable + 7.6% NPC not found). Still concerning, but not 68% |
| "Distilling Claude's learned helplessness" | LARGELY RHETORICAL | 92% of loop clusters resolve. 33% of resolutions include explicit recovery reasoning in `<think>` (which is GOOD training signal). Genuine concern is the 1% non-resolving clusters |
| "r9 is the first honest training run" | OVERSTATED | r7 added chat-template fix, r8 added loss-masking fix, r9 added prompt alignment. Each step was incrementally honest |
| Hermes block "9,661 bytes lines 238-272" | Bytes confirmed, line range slightly off | Lines 241-262 is exact for the assignment statement; 238 is comment, 263+ is post-block |

### Net: what survives that's actionable

**Source-side game bugs (justify upstream Kaetram-Open PR):**
- 6 missing items in `items.json`
- 4 boss drops missing in `mobs.json`
- 1 typo (`codersglitch.json:15` `"noc"` → `"npc"`)
- = **11 specific fixes**, scoped, high impact (unblocks 3 quest chains)

**Our-side bugs:**
- Hermes block (P0)
- `query_quest` returning unreachable waypoints for "Sorcery and Stuff"
- `loot()` returns success-shaped on no-loot
- `drop_item()` returns `dropped: false` (not silent, but caller must inspect body — could be made an MCP-level error)
- `interact_npc` 41.8% soft-fail (mostly distance/unreachable)
- `--efficient` flag crashes `resume-agent.sh` and `restart-single-agent.sh`
- `crossroads` warp in prompts/docs doesn't exist in game

---

## Confirmed Kaetram-Open upstream PR scope

| File | Line | Bug | Impact | Fix |
|---|---|---|---|---|
| `packages/server/data/items.json` | — | `catpet` missing | royalpet stage 2 silently fails | Add pet-type item entry |
| `packages/server/data/items.json` | — | `staff` missing | sorcery stage 1 silently fails | Add weaponType=staff item |
| `packages/server/data/items.json` | — | `skeletonkingtalisman` missing | codersglitch unwinnable | Add talisman item |
| `packages/server/data/items.json` | — | `ogrelordtalisman` missing | codersglitch2 unwinnable | Add talisman item |
| `packages/server/data/items.json` | — | `queenanttalisman` missing | codersglitch2 unwinnable | Add talisman item |
| `packages/server/data/items.json` | — | `forestdragontalisman` missing | codersglitch2 unwinnable | Add talisman item |
| `packages/server/data/mobs.json` | 4381-4432 | `skeletonking` missing talisman drop | Boss path to codersglitch turn-in dead | Add quest-gated drop |
| `packages/server/data/mobs.json` | 4435-4483 | `ogrelord` missing talisman drop | codersglitch2 stage 1 dead | Add quest-gated drop |
| `packages/server/data/mobs.json` | 4896-4944 | `queenant` missing talisman drop | codersglitch2 stage 2 dead | Add quest-gated drop |
| `packages/server/data/mobs.json` | 4747-4795 | `forestdragon` missing talisman drop | codersglitch2 stage 3 dead | Add quest-gated drop |
| `packages/server/data/quests/codersglitch.json` | 15 | Typo `"noc": "coder"` | Stage 0 stage-data malformed | Rename field to `"npc"` |

**Drop from PR scope:** `smithingboots` (phantom claim). The real anvilsechoes reward is `bronzeboots` which exists.

---

## Action items (priority ordered, evidence-backed)

### P0 — Tonight (before r9 eval finishes ~Apr 17 09:00 UTC)

- [ ] **Fix Hermes block train/inference mismatch.** Either move append into `convert_to_qwen.py:_load_system_prompt()` or delete `play_qwen.py:241-262`. Without this, r9 eval is meaningless. **Single highest-leverage finding.**
- [ ] **Set `min_containers=1` on `serve_modal_base.py:70`.** Cold-start is symmetric pedantry but cosmetic-cheap; redeploy both endpoints.
- [ ] **Strip `--efficient` flag** from `resume-agent.sh:35`, `restart-single-agent.sh:43,72,77,88-89` (they crash on argparse rejection — `orchestrate.py` only accepts aggressive/methodical/curious). Update `orchestrate.py:13` docstring.

### P1 — Tomorrow (r9 eval day)

- [ ] **Run r9 eval with N≥10 episodes per arm** (base vs r9-SFT, both endpoints warm). Report metrics with **clustered standard errors at the sub-session level** (effective N ≈ N_episodes × ~13 sub-sessions).
- [ ] **Read MongoDB deltas at episode end** for kills/XP/level instead of log-parse heuristics. `pymongo` is already imported for the DB reset in `eval_harness.py:reset_player_db()`.
- [ ] **Capture `response.usage`** in `play_qwen.py` per turn. Required for paper compute-cost table.
- [ ] **Write 2 tests** (60 lines total): `tests/test_chat_template.py` (round-trip 3-turn fixture, assert `<think>` survives in all assistant turns) + `tests/test_loss_mask.py` (assert labels tensor has `-100` outside assistant spans). Would have caught 2 of the 4 historical silent bugs.
- [ ] **Add `assert old in template`** to `train_modal.py:198-227` and `serve_modal.py:73-98` chat template patch. Convert silent no-op to hard failure on Qwen tokenizer revision.

### P2 — This week (before r10)

- [ ] **Open Kaetram-Open upstream PR** with the 11 fixes table above. Reference: this analysis document.
- [ ] **Re-ground `prompts/game_knowledge.md`:**
  - Remove `crossroads` warp (doesn't exist)
  - Mark Bike Lyson as Snek **achievement** not quest, with the actual reward (2,000 Strength XP + run ability)
  - Fix `query_quest("Sorcery and Stuff")` walkthrough — the cave waypoints `(280,600), (300,550), (315-320, 455)` are walls. Real Hermit Crab Warrior is at `(320, 455)` reachable from south, not from those approaches.
  - Source of truth: `Kaetram-Open/packages/server/data/quests/*.json` and `data/achievements.json`
- [ ] **Tighten `mcp_game_server.py` tool-layer contracts:**
  - `loot()` should return `{"error": "Nothing to loot"}` when distance-0 lootables are empty (not a benign success)
  - `drop_item()` should raise an MCP-level error (not `{"dropped": false}`) when drop fails — current behavior requires the model to inspect the JSON body
  - `interact_npc()` should auto-navigate-then-interact when distance > adjacency, not just fail
- [ ] **Add convert_to_qwen.py filter:**
  - Drop windows where the **last 3 assistant turns** share tool+args signature with no progress between calls (~1% of records — small, surgical)
  - Drop turns where `drop_item` returned `dropped: false` (these are tool bugs, not learnable behavior)

### P3 — This month (paper critical path)

- [ ] **Bump epochs from 1 → 2-3 for r10** (matches ToolACE, recommended by Unsloth for small datasets). Real LR critique replaces "LR is wrong" from v1.
- [ ] **Restart data collection on patched game** with tightened prompts. Target 3K fresh sessions across 3 personalities.
- [ ] **r10 SFT on clean data → r10-KTO → 3-arm eval** (base vs r10 vs r10-KTO).
- [ ] **arXiv draft.** Headline: long-horizon agentic SFT in MMORPG (the environment is the contribution). Section 5: three-tier silent failure taxonomy. Appendix: r5→r10 ablation table.

### Things v1 said to do — DEMOTED or DROPPED based on red-team

- ~~Cap attack-loop oversampling~~ — **DROP.** Attack-dominant sessions are healthy farming, not loops. Drift is smaller than claimed.
- ~~Force `<thinking>` on every turn~~ — **DROP.** Real live-think rate is 36%, normal Sonnet behavior.
- ~~Bonferroni correction~~ — **REPLACE** with Holm-Bonferroni or BH-FDR.
- ~~Human eval of trajectories~~ — **DROP.** Not standard for game agents.
- ~~Drop research/ reproducibility concern~~ — **DROP.** Pre-submission private is standard hygiene.
- ~~"Stop data collection NOW"~~ — **DROP** (collaborator's rec). Real numbers don't justify emergency. Let session finish, tighten for r10.

---

## Paper framing (corrected)

**v1 framing (too generous, multiple competing-work problems):**
- "Structured MCP distillation works" → not novel ([Liu et al. 2025 NeurIPS Spotlight](https://arxiv.org/abs/2505.13820), [Kang et al. 2025 NeurIPS Spotlight](https://arxiv.org/abs/2505.17612) cover this)
- "5 silent failure modes" → 3 of 4 are existing GitHub issues we confirmed
- "3-personality teacher" → [PANDA (ACL 2025)](https://arxiv.org/abs/2504.06868) did 16 personalities at larger scale
- "KTO over PPO" → [MaKTO (NeurIPS 2025)](https://openreview.net/forum?id=WPHpBnKvdq) already applied KTO to multi-agent strategic game (Werewolf)

**v2 framing (defensible, environment + methodology as contribution):**

> **"Long-horizon agentic SFT in an MMORPG: a three-tier silent failure taxonomy and an eval harness for sub-1-hour episodes."**

- **Novelty axis: the environment + the eval methodology.** Long-horizon (100s of turns, persistent world state, navigation, quest dependencies) is genuinely under-served — ALFWorld/WebShop/HotPotQA/Werewolf are all ≤30 steps. The closest precedent is [Orak (2026)](https://arxiv.org/html/2506.03610v2) for video-game agents.
- **Three-tier silent-failure taxonomy** as Section 5 (training / tool / environment) — this *is* novel as a framing. No competing paper categorizes silent failures across all three layers.
- Right venue: **NeurIPS 2025 Workshop on Multi-Turn Interactions in LLMs** (or 2026 equivalent), 4-8 page workshop paper. Main conference is achievable only if r9-KTO produces clean, statistically-significant gains over base.
- Fallback venue if r9 doesn't beat base: **NAACL Insights from Negative Results** (~64% accept) — frame as "we audited 4 popular SFT framework interactions and found systematic silent failures across the agentic stack."

---

## Discussion questions (updated)

**Q1.** Hermes-block fix: do we go with path A (move into training prompt) or path B (delete from inference)? B is simpler; A keeps the explicit format reminder. Recommend B.

**Q2.** Three-tier framing for the paper — does Niral agree this is stronger than v1's "structured distillation" pitch? It concedes algorithmic novelty but wins on environmental + methodological novelty.

**Q3.** Upstream Kaetram-Open PR — who submits, what's the commit message framing? Suggest: "fix(quests): add missing item entries and boss drops for codersglitch chain + royalpet + sorcery." Avoid mentioning AI-agent angle in the PR (clean game contribution stands on its own).

**Q4.** r10 timeline given r9 eval will be done ~Apr 17 09:00 UTC: is the path "r9 eval result → game patch → tighten prompts → 3K fresh sessions → r10 SFT → r10-KTO → 3-arm eval" achievable in 2 weeks, or does the patched-game requirement push us to end of April?

**Q5.** Personality angle in the paper: data shows AGG-METH death gap is statistically significant but AGG-CUR is not. METH attacks the most (contradicting the "aggressive attacks more" thesis). Do we keep 3 personalities or consolidate to 2 (e.g., "high-risk vs cautious")? Smaller axis but stronger signal.

---

## File references

### Confirmed code locations (this analysis)

- `play_qwen.py:241-262` — Hermes tool block append (P0 fix target)
- `play_qwen.py:91-105` — `get_tool_definitions` excludes login → 21 defs
- `train_modal.py:198-227` — Qwen3 chat template patch (`<think>` preservation)
- `train_modal.py:297-308` — `apply_chat_template` without `tools=` (intentional, keeps system prompt clean)
- `train_modal.py:365` — `use_rslora=False` with load-bearing comment about 8× LR trap
- `serve_modal.py:73-98` — Same chat template patch as training
- `serve_modal_base.py:70` — `min_containers=0` (asymmetry to fix)
- `convert_to_qwen.py:60-87` — `_load_system_prompt()` (where to land Hermes block if going path A)
- `convert_to_qwen.py:1004-1007` — `<think>` preservation on every training turn
- `convert_to_qwen.py:1344-1367` — Degenerate filter (F9/F10/F13)
- `mcp_game_server.py:1377-1482` — `loot()` (returns success-shape on no-loot)
- `mcp_game_server.py:1102-1164` — `drop_item()` (structured errors but `dropped: false` shape)
- `mcp_game_server.py:802-807` — `talk_npc` const-reassignment latent bug
- `eval_harness.py:97-115` — `reset_player_db()` (where to add MongoDB delta read)
- `eval_harness.py:536-578` — Sub-session continuation
- `cli_adapter.py:241-262` — Codex stop hook (continuation mechanism)
- `dataset/qwen_sft/metadata.json` — 11,382-char training system prompt (no Hermes block)
- `dataset/qwen_sft/train.json` — 5,871 records, 100% with `<think>`
- `dataset/eval/runs/20260415_211929/` — Only real eval result, N=1 per arm

### Kaetram-Open source bugs (PR scope)

- `Kaetram-Open/packages/server/data/items.json` — 6 missing items
- `Kaetram-Open/packages/server/data/mobs.json:4381-4432` — skeletonking drops
- `Kaetram-Open/packages/server/data/mobs.json:4435-4483` — ogrelord drops
- `Kaetram-Open/packages/server/data/mobs.json:4747-4795` — forestdragon drops
- `Kaetram-Open/packages/server/data/mobs.json:4896-4944` — queenant drops
- `Kaetram-Open/packages/server/data/quests/codersglitch.json:15` — `"noc"` typo
- `Kaetram-Open/packages/server/data/quests/codersglitch2.json:63-76` — Talisman requirements
- `Kaetram-Open/packages/server/data/quests/royalpet.json:96` — catpet reward
- `Kaetram-Open/packages/server/data/quests/sorcery.json:38` — staff reward
- `Kaetram-Open/packages/server/data/achievements.json:44-60` — `boxingman` (Snek hunt is REAL)
- `Kaetram-Open/packages/server/data/spawns.json:12-40` — Hermit Crab Warrior at (320, 455)

### External citations (verified)

**Papers:**
- [arXiv 2505.03275 — RAG-MCP](https://arxiv.org/abs/2505.03275) — Note: paper says ~30 tools graceful, ~100 sharp degradation. Does NOT contain the "19" number that appears in CLAUDE.md.
- [arXiv 2312.03732 — rsLoRA (Kalajdzievski 2023)](https://arxiv.org/abs/2312.03732) — Confirms 1/√r scaling vs standard 1/r
- [arXiv 2409.00920 — ToolACE](https://arxiv.org/abs/2409.00920) — Table 2: LR=1e-4, LoRA r=16/α=32, 3 epochs (matches our LR, recommends more epochs)
- [arXiv 2505.13820 — Structured Agent Distillation (NeurIPS 2025 Spotlight)](https://arxiv.org/abs/2505.13820) — Direct competing work
- [arXiv 2505.17612 — Distilling LLM Agent into Small Models w/ Tools (NeurIPS 2025 Spotlight)](https://arxiv.org/abs/2505.17612) — Direct competing work
- [arXiv 2504.06868 — PANDA Persona Dynamics (ACL 2025)](https://arxiv.org/abs/2504.06868) — 16-personality teacher, our 3 is a subset
- [MaKTO (NeurIPS 2025)](https://openreview.net/forum?id=WPHpBnKvdq) — KTO on multi-agent strategic game (Werewolf)
- [arXiv 2506.03610 — Orak: Foundational Benchmark for LLM Agents on Video Games](https://arxiv.org/html/2506.03610v2) — Closest precedent for our environment

**Upstream issues (the 4 silent training bugs are partly community-known):**
- [QwenLM/Qwen3 #1831 — Chat template `<think>` stripping](https://github.com/QwenLM/Qwen3/issues/1831)
- [huggingface/trl #3781 — `assistant_only_loss` + Liger Kernel silent failure](https://github.com/huggingface/trl/issues/3781)
- [unslothai/unsloth #2771 — `train_on_responses_only` Qwen3 zero-loss](https://github.com/unslothai/unsloth/issues/2771)
- [Qwen3-8B PR #14 (declined) — assistant mask Jinja tags](https://huggingface.co/Qwen/Qwen3-8B/discussions/14)

**Tooling references:**
- [Unsloth pyproject.toml](https://github.com/unslothai/unsloth/blob/main/pyproject.toml) — Confirms TRL pin `>=0.18.2,!=0.19.0,<=0.24.0`
- [HuggingFace blog — rsLoRA explainer](https://huggingface.co/blog/damjan-k/rslora) — Recommends LR adjustment when switching to rsLoRA

**Venues:**
- [NAACL Insights from Negative Results 2025 CFP](https://insights-workshop.github.io/2025/cfp/) — ~64% accept, 4-page short, fits "silent failures" framing
- [NeurIPS 2025 Workshop on Multi-Turn Interactions in LLMs](https://workshop-multi-turn-interaction.github.io/) — Best fit for the long-horizon eval methodology contribution

---

## Methodology corrections for CLAUDE.md

These need fixing in CLAUDE.md before they propagate further:

1. ~~"22 tools is past RAG-MCP ~19-tool danger line"~~ → **"22 tools is within RAG-MCP's ~30-tool comfort zone (graceful degradation begins past 30, sharp past 100). Worth monitoring but not pre-emergency."**
2. ~~"Tülu 3 / ToolACE use 2-3 epochs at 5e-5"~~ → **"ToolACE uses LR=1e-4 LoRA × 3 epochs; consider 2 epochs for r10"**
3. ~~"NeurIPS ML Reproducibility track"~~ → **"NAACL Insights from Negative Results workshop"** (NeurIPS Reproducibility only accepts reproducing OTHER people's work)
4. Add: **"Bike Lyson IS a real Snek-hunt achievement (`achievements.json:44-60`); the 'no quest' framing in earlier analyses was wrong. The agent's Snek-killing belief has source-side truth, but the dialogue handshake fails to advance the achievement past stage 0. Real fix: improve `interact_npc` achievement-start handling, not remove the Snek references."**
5. Add: **"`crossroads` warp does NOT exist in Kaetram. Real warps: mudwich/aynor/lakesworld/patsow/crullfield/undersea. Our prompts and `convert_to_qwen.py:39,57` and `state_extractor.js:583` and `extract_turns.py:611` reference it as a valid target — when called, falls through to mudwich silently."**

---

*Generated by Claude Opus 4.7 (1M context). v1 = 10 sub-agent investigation. v1 → v2 = 7 red-team sub-agents + 3 verification sub-agents (verifying a collaborator's recommendation). All numerical claims in this v2 have been independently verified at least once. Where v1 and the collaborator's recommendation disagreed, both were red-teamed against source code and live data. Sources of remaining uncertainty are explicitly flagged.*
