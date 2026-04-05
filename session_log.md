# Session Log
_Keep under 30 lines. Update at end of every session. Most recent first._

---

## 2026-04-05 — KTO Pipeline Built + Codex/Claude Joint Review

**Why KTO instead of r7 SFT:** SFT treats all Claude data equally — even bad sessions. KTO uses binary desirable/undesirable labels from session outcomes (XP gain, quest progress, deaths, click_tile rate). Teaches Qwen judgment, not just imitation. KTO paper + TRL confirmed: works on expert demonstrations with binary labels, best applied post-SFT (exactly where r6 is).

**4 new files built (untracked, need commit+push to VM):**
- `score_sessions.py` — scores extracted sessions 0-1 from outcome signals, labels top 40% desirable / bottom 30% undesirable
- `build_kto_dataset.py` — sliding windows (size=5, stride=2) → prompt/completion/label. Stratified val split by label. Local window quality gating.
- `finetune/train_kto_modal.py` — KTO on r6 merged. Explicit plain-HF ref_model (avoids Unsloth PEFT internals interacting with TRL). LR=5e-7, β=0.1, desirable_weight capped at 3.0. Dataset sanity + hard fails before training. Smoke test mode (10 steps).
- `inspect_kto_dataset.py` — local dry-run: label balance, session counts, sample prompt/completion pairs before Modal launch.

**Key design decisions (Codex-reviewed):**
- `level_delta/3.0` not /1.0 — scales across multi-level sessions
- Removed `attack_rate>0.80` penalty — was biasing against AGGRESSIVE agent sessions
- Explicit `ref_model` (plain HF AutoModelForCausalLM, frozen) — TRL-documented pattern, no Unsloth interaction risk
- Memory: ~40GB on H100 80GB (18GB model + 18GB ref + optimizer), 39GB headroom

**Context window / memory gap confirmed:** play_qwen.py keeps ~15 turns of rolling context (trims to 40 after 60). Long-horizon goals get lost after ~15 turns. Niral proposed add_memory/remove_memory tools — research confirmed valid (MemGPT, Voyager). Stage 1 fix: persistent memory.txt injected into system prompt via existing Bash tool, no retraining. Stage 2: train explicit tools. Keep separate from KTO run.

**Run order:** commit+push → score_sessions.py → build_kto_dataset.py → inspect_kto_dataset.py → `modal run finetune/train_kto_modal.py --smoke-test` → full run → update serve_modal.py (SFT_EXPERIMENT → r6-kto).

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
