# Session Log
_Keep under 30 lines. Update at end of every session. Most recent first._

---

## 2026-04-12 — r8 Fix + Full Pipeline Audit

**r8 loss masking fix committed and pushed (`cb8ec3e`).** `completion_only_loss=True` in r5–r7 was silently a no-op — TRL skips masking with `dataset_text_field="text"` if no `response_template` is set. r5–r7 trained on all tokens including game state JSON. Fix: replaced with `train_on_responses_only(instruction_part="<|im_start|>user\n", response_part="<|im_start|>assistant\n")` from Unsloth, applied after trainer init. Experiment bumped to `kaetram-qwen3.5-9b-r8`.

**Data audit (direct VM inspection).** The "65 pending sessions" in prior docs was wrong — subagent read stale docs, not the filesystem. Reality: 583 total claude logs (200+195+188 across agents 0-2), 650 extracted sessions. All 26 post-r7 logs are **Gemini** — zero new Claude data since Apr 9. `INCLUDED_HARNESSES = {"claude", "unknown"}` filter confirmed working. r8 trains on the same 7,069-record dataset as r7.

**Inference harness solid.** `play_qwen.py` + `serve_modal.py` audit: all 22 tools dispatched, `<think>` patch in place at serve time, dual-path XML+JSON parsing, correct r7 checkpoint. Only minor: message-count truncation (not token-count), non-blocking.

**Linear updated.** KAE-33 created (r8 SFT launch). KAE-13 (KTO) updated — now explicitly depends on r8, not r7. All research docs updated: `training-runs.md` (r8 entry), `data-quality.md` (corrected counts + harness breakdown table), `INDEX.md` (loss masking fix marked done).

**VM needs `git pull` before r8 launch.** VM is at `aff589d` (Apr 11), needs `cb8ec3e`.

---

## 2026-04-10 — Codex + Gemini CLI Integration

**Two new harnesses integrated end-to-end.** Both `--codex` (GPT-5.4) and `--gemini` (Gemini 2.5 Flash) are now drop-in replacements alongside `--claude`. All three share the same MCP server (`mcp_game_server.py`), system prompt, and orchestration pipeline.

**Codex quirks:** `codex exec` is one-shot (exits when model thinks it's done). Fixed with a Stop Hook (`scripts/codex_stop_hook.py`) that intercepts exit and forces continuation up to max_turns. Also needed `CODEX_HOME` isolation per sandbox (auth.json copy), `stdin=DEVNULL` (was hanging), and `model_reasoning_effort = "medium"`. No reasoning/thinking tokens in output.

**Gemini was cleaner:** Uses Claude-compatible `-p` + `--output-format stream-json` but with flat event structure (`type: "tool_use"` at top level, not nested in `message.content[]`). MCP via `.gemini/settings.json`, turn limit via `maxSessionTurns`, `-y` yolo mode. Needed custom dashboard parsing for flat events.

**Data isolation:** Codex/Gemini logs are collected but excluded from Qwen SFT training. `extract_turns.py` skips them (`[skip]` message). `convert_to_qwen.py` filters by `INCLUDED_HARNESSES = {"claude", "unknown"}`. Each turn is tagged with `harness` from `.meta.json` sidecar.

**Dashboard updated:** Gemini blue badge, Codex amber badge. Flat event parsing for Gemini `tool_use`/`tool_result`. Model extraction from Gemini `init` event. All scripts (nuke, restart, resume, reset-state) handle all harnesses.

---

## 2026-04-09 — r7 SFT Training Live + Chat Template Fix

**r7 training running on Modal** (PID 2602243, started 15:12 UTC, ~42% through at last check: step 171/402, loss ~0.098, eval_loss 0.1034 @ epoch 0.37). First attempt died at Modal's 8h default timeout; retried with 18h cap. ETA ~05:00 UTC April 10. Dataset is **7,069 records (6,423 train / 646 val)**, up from r6's 4,445.

**The real gold of r7: Qwen3 chat template fix (QwenLM/Qwen3 #1831).** Stock template silently drops `<think>` blocks from intermediate assistant turns in multi-turn windows — we were training on reasoning-less completions for every turn except the last. Patched in convert_to_qwen.py. This is the biggest single data-quality change since loss masking.

**Other r7 prep in commit `8095907` (Niral):** personality detection fallback fixed (r6 had personality=None for every record; r7 split is aggressive 40% / methodical 32% / curious 28%), quest progression scoring added to KTO (replaces the NPC-talk-count proxy), research docs (r7-hyperparameters.md, agent-sft-landscape.md 14-paper survey).

**rsLoRA attempted and reverted.** Enabled at r=64/alpha=64 → 8× effective LR → diverged. VM has uncommitted edit flipping `use_rslora=False` in `finetune/train_modal.py:359` with a comment. Note: `research/decisions/r7-hyperparameters.md` still says "rsLoRA: Enabled" — stale. Leaving for tomorrow's cron compile pass to catch (decisions/ isn't directly watched so may need manual fix).

**MCP server now at 22 tools** (added buy_item, gather, loot, query_quest). Past the RAG-MCP 19-tool threshold — KAE-15 (tool filtering) more urgent. `scripts/stop-agent.sh` replaced by `nuke-agents.sh` (SIGKILL everything). Dashboard overhauled with live game_state.json + MongoDB merge.

**Cron loop audit (subagent):** the VM research-staleness cron is mtime-based and hardcoded to 4 targets — doesn't watch `mcp_game_server.py`, `prompts/`, or `research/decisions/`. Last auto-compile runs landed Apr 7 and Apr 8 (commits `6c46fb9`, `716882b`), so the loop is alive but blind in places. Data collection running in parallel (3 agents, ~2h18m left when checked).

**Open question:** `accept_quest` action appears only **8 times** in the full 7,069-record dataset. Way too low given quest activity in logs. Under investigation — possibly a conversion/filter bug in extract_turns.py or convert_to_qwen.py.

---

## 2026-04-08 — Research Compile Pass

**Research KB compile:** Fixed stale facts across all research/ files. Key findings:
- MCP tool count grew 18 → 22 (buy_item, gather, loot, query_quest added). Exceeds RAG-MCP 19-tool threshold — KAE-15 (tool filtering) now more urgent.
- 509 raw sessions on VM but only 395 extracted. 114 sessions pending re-extraction before next SFT rebuild.
- Raw session count updated (443 → 509), paper/contribution tool references updated (18 → 22).
- Added tool count scaling gap to INDEX.md and action item for re-extraction.

**Recent commits (Apr 6-8) not previously compiled:** buy_item tool, tree exhaustion bug fix, warp ID fixes, combat crash guard, door tile pathfinding (88805c7), dashboard overhaul with live game state (c971848), game_knowledge quest walkthrough updates (68e5f2c).

**Current strategy unchanged:** re-extract pending sessions → rebuild qwen_sft → r7-SFT → KTO → eval. Biggest blocker remains eval protocol, not infrastructure.

---

## 2026-04-05 — KTO Runtime Fixes + Research Loop + Latest Data Rebuild

**KTO pipeline validated:** `score_sessions.py`, `build_kto_dataset.py`, `inspect_kto_dataset.py`, and `finetune/train_kto_modal.py` complete. KTO dataset: 2771 train / 273 val. After 5 smoke test attempts (batch OOMs, bitsandbytes+Unsloth cu128 incompatibility, KTOTrainer batch>1 requirement), landed on `ref_model=None + precompute_ref_log_probs=True`. 10/10 smoke steps passed — train_loss=0.617, KL active, eval clean. Save fallback in place (commit 34314ad). Full run awaiting Niral greenlight.

**Latest SFT rebuild completed on VM:** re-extracted newest Claude logs and rebuilt `dataset/qwen_sft` to `3957 train / 488 val = 4445 total` (up from `3853 / 465`). Quality stayed usable but not uniformly better: click_tile rose `4.7% -> 5.6%`, repetitive loops `0.2% -> 0.3%`, avg think stayed ~`423`, empty think `0`. Good enough for `r7`, but not a dramatic jump.

**Research knowledge base seeded and tightened:** added `research/` with experiments / related-work / decisions / paper framing. Tightened stale claims: KTO docs now reflect the current `ref_model=None + precompute_ref_log_probs=True` path; data-quality docs now reflect the latest `3957/488` build; contribution framing softened to avoid overclaiming novelty before eval.

**Loop is now real on the VM:** added `scripts/check_research_staleness.py` + `scripts/run_research_staleness_check.sh`, wired email nudges through `notifications.py`, and installed a real VM cron job (`00:07` daily). The wrapper now tries to auto-run `claude -p "/compile-research"` with `claude-opus-4-6` when docs are stale; if research files changed, it stages `research/` + `session_log.md`, commits, rebases, and pushes. If Claude CLI is unavailable on the VM, it falls back to an email nudge. This is VM-side and independent of laptop / tmux / live local sessions. `.claude/scheduled_tasks.lock` is ignored and not part of the real loop.

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
