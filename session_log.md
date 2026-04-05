# Session Log
_Keep under 30 lines. Update at end of every session. Most recent first._

---

## 2026-04-05 — KTO Runtime Fixes + Research Loop + Latest Data Rebuild

**KTO pipeline is built and running:** `score_sessions.py`, `build_kto_dataset.py`, `inspect_kto_dataset.py`, and `finetune/train_kto_modal.py` are in place. KTO dataset built at `2771 train / 273 val` from scored Claude sessions. Current smoke test moved off the explicit ref-model path to `ref_model=None + precompute_ref_log_probs=True` after repeated H100 OOMs; latest run is the first to get through reference-log-prob precompute instead of dying immediately.

**Latest SFT rebuild completed on VM:** re-extracted newest Claude logs and rebuilt `dataset/qwen_sft` to `3957 train / 488 val = 4445 total` (up from `3853 / 465`). Quality stayed usable but not uniformly better: click_tile rose `4.7% -> 5.6%`, repetitive loops `0.2% -> 0.3%`, avg think stayed ~`423`, empty think `0`. Good enough for `r7`, but not a dramatic jump.

**Research knowledge base seeded and tightened:** added `research/` with experiments / related-work / decisions / paper framing. Tightened stale claims: KTO docs now reflect the current `ref_model=None + precompute_ref_log_probs=True` path; data-quality docs now reflect the latest `3957/488` build; contribution framing softened to avoid overclaiming novelty before eval.

**Loop is now real on the VM:** added `scripts/check_research_staleness.py` + `scripts/run_research_staleness_check.sh`, wired email nudges through `notifications.py`, and installed a real VM cron job (`00:07` daily) to run the staleness check. This is VM-side and independent of laptop / tmux / live Claude sessions. `.claude/scheduled_tasks.lock` is ignored and not part of the real loop.

**Current strategy:** keep `r7` as pure SFT if run, keep memory separate, finish the KTO smoke test before changing objectives again. Biggest blocker for a paper remains eval, not more infrastructure.

---

## 2026-04-04 — Research Deep Dive + Training Pipeline Hardening

**39-paper research survey** across SFT distillation, GRPO/RL, game agents, data quality. Created 10 Linear issues (KAE-10 to KAE-19) with prioritized roadmap. Key papers: Structured Agent Distillation (loss masking), ORAK (3-stream SFT), Tree-GRPO, KTO, LIMA.

**8 PRs merged (#15-#22):**
- Loss masking via `completion_only_loss=True` — stops training on game state tokens
- Quality scoring upgrade — reasoning-action alignment, mismatch penalties
- click_tile filter — removed 913 blind no-reasoning click_tiles from multi-turn windows (37.9% → 4.7%)
- Agent 3/4 code-level exclusion — `EXCLUDED_AGENTS` set + raw data deleted from VM
- play_qwen.py crash fix — safe context trimming at message group boundaries
- Realistic JSON tool results — replaces fake "Targeting mob" strings
- Native MCP tool dispatch in play_qwen.py — model calls attack(Rat) directly, dispatch maps to JS helpers
- Prompt alignment in play_qwen.py + play_qwen.sh — lists native tools, not browser_run_code
- Modal timeout bumped to 24h, epochs reduced to 2 (overfitting risk with r=64 on 3.2K records)
- serve_modal.py updated to r5-mcp-tools checkpoint

**Data quality round 2:** Extracted 149 new sessions from Niral's 8h orchestrator run. Added repetitive loop filter (23% → 0.2%), reasoning trimming (avg 1654 → 426 chars, zero over 800), single-turn click_tile filter. IIFE wrapper fix for native tool dispatch.

**Final dataset (rebuilt):** 3,853 train / 465 val. click_tile 4.7%, repetitive 0.2%, attack 15.2%, navigate 27.8%, interact_npc 11.7%. Avg reasoning 426 chars. Zero empty. Verified by Codex.

**r6 trained and tested:** Niral's r6-optimized completed. Deployed on Modal, Qwen played end-to-end (native tools, real game state). Model is rough but harness works. Serve stopped to save cost. Next: retrain on rebuilt dataset (r7), then KTO (Stage 2).

---

## 2026-04-03 — Data Audit + Personality Finalization

**Deep audit of all agent logs (189 sessions, 289MB on VM):**
- Confirmed MCP architecture working: 100% semantic tool calls, avg 88 actions/session, 37K thinking chars/session
- Agents at level 70-73, fighting real mid-game content (Bandits, Cow Warriors, Scary Skeletons)
- Attack fix: `post_attack` field added — agent can now confirm damage dealt, eliminates click_tile fallback in combat

**Personalities finalized — dropping to 3:**
- EFFICIENT (agent_3) dropped: 45% click_tile rate, lowest level (37), broken behavior
- METHODICAL prompt rewritten: hard rules (HP < 60% eat first, 2+ food before quest mobs), no more catch-22 prep loop
- Active: AGGRESSIVE (agent_0), METHODICAL (agent_1), CURIOUS (agent_2)
- 3 orthogonal decision axes: combat approach / HP-gated preparation / exploration-first

**Current state:** 3 agents running, training job kicked off on Modal in parallel.

## 2026-04-02 — NPC Interaction Fix + Prompt Rewrite

**Critical bugs fixed:**
- `interact_npc` 95% failure → 100% success (when NPC reachable). Root cause: Chebyshev vs Manhattan adjacency + walking to NPC tile instead of orthogonal neighbor.
- Wife NPC unreachable: wrong door (194,218 = Sorcerer, not Wife). Correct: (310,264).
- Warp cooldown spam: tool now auto-waits internally (up to 25s).
- equip_item: now verifies result, returns equipped true/false with reason.
- MCP "pending" detection: orchestrator auto-restarts stuck sessions.
- Added `drop_item` tool, eat_food HP-full check, login retry loop.

**Prompt rewrite (research-informed):**
- XML tags, calm language (Claude 4.6 over-triggers on aggressive phrasing), WHY clauses on rules.
- Added SEEK QUEST rule: agents actively seek NPCs when no quest is active.
- Removed Methodical food-before-ACCEPT gate, added Efficient NPC-seeking trigger.
- Trimmed game_knowledge ~800 tokens. Total prompt ~2,340 tokens (under 3K threshold).
- Nav snap radius 10→25 (fixes 54% Lakesworld wall failures).

**Results:** Agent 0 completed full Desert Quest (first multi-stage completion). 365+ sessions collected.

## 2026-04-01 — Data Audit + Cleanup Session

- Deleted agent_4 (39 dead Codex sessions), ~260 stub files, pre-March-28 data
- Rebuilt qwen_sft: 1,233 train / 158 val. Created DATA.md.
