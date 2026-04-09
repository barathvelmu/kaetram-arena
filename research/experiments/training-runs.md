# Training Runs

History of all Qwen3.5-9B finetuning runs, from initial SFT through KTO preference learning. Each entry records what changed, what broke, and what improved.

---

## Run Timeline

| Run | Date | Type | Records | Key Change | Result |
|-----|------|------|---------|------------|--------|
| r1-r3 | Mar 26-31 | SFT | ~500-800 | Initial training, raw data | Model loaded but poor action quality |
| r4 | Apr 3 | SFT | ~1,200 | Loss masking (KAE-10) | Stopped training on game state tokens |
| r5 | Apr 4 | SFT | 3,853 train / 465 val | Quality filters + native MCP tools | First playable model, deployed on Modal |
| r6 | Apr 4-5 | SFT | 3,853 train / 465 val | Niral's optimized run, 2 epochs | Deployed and tested end-to-end |
| r6-KTO | Apr 5 | KTO | 2,771 train / 273 val KTO windows | Preference learning on scored sessions | Pipeline validated — 10/10 smoke steps, train_loss=0.617, KL active. Awaiting full run. |
| r7 | Apr 9 | SFT | 6,401 train / 583 val | Chat template fix, personality labels, rsLoRA, expanded dataset | Pending launch |
| r7-KTO | Apr 9 | KTO | TBD | Quest progression scoring, rebuilt on r7 SFT | Pending (after r7 SFT) |

---

## r4 — Loss Masking (Apr 3)

**What changed:** Added `DataCollatorForCompletionOnlyLM` with response template `<|im_start|>assistant`. Zeroes loss on all system/user tokens (game state JSON, ASCII maps, prompts). Only trains on assistant responses.

**Why:** Structured Agent Distillation (arxiv 2505.13820) showed +8 percentage points task success from this alone. Model was wasting capacity memorizing game state JSON formatting. (KAE-10)

**Config:** LoRA r=64, alpha=16, 3 epochs, experiment name `r4-lossmasked`.

**Result:** Meaningful quality improvement. Model stopped reproducing game state verbatim in outputs.

---

## r5 — Quality Filters + Native MCP Tools (Apr 4)

**What changed (8 PRs, #15-#22):**
1. click_tile filter — removed 913 blind no-reasoning click_tiles (37.9% → 4.7%)
2. Repetitive loop filter — consecutive identical actions (23% → 0.2%)
3. Reasoning trimming — avg 1,654 → 426 chars, max capped at 800
4. Agent 3/4 exclusion — EFFICIENT (45% click_tile) and Codex (dead sessions) removed
5. Native MCP tool format — `attack(Rat)` dispatches to JS helpers directly, not `browser_run_code`
6. Realistic JSON tool results — replaced fake "Targeting mob" strings with actual game state changes
7. Reasoning-action alignment scoring — bonus for match, penalty for mismatch
8. Modal timeout 24h, epochs reduced to 2 (overfitting risk with r=64 on 3.2K records)

**Dataset:** 3,853 train / 465 val. Action distribution: navigate 27.8%, attack 15.2%, interact_npc 11.7%, click_tile 4.7%, repetitive 0.2%.

**Config:** LoRA r=64, alpha=16, 2 epochs, `completion_only_loss=True`, experiment `r5-mcp-tools`.

**Result:** First model that plays the game end-to-end via native tool calls. Deployed on Modal, tested with `play_qwen.py`. Model is rough but harness works.

---

## r6 — Optimized Training (Apr 4-5)

**What changed:** Niral's optimized run on same r5 dataset. Specific optimizations not documented — need to backfill from Niral.

**Result:** Deployed and tested. Serve stopped to save Modal cost. This is the current SFT checkpoint that KTO builds on.

---

## r6-KTO — Preference Learning (Apr 5)

**What changed:** Post-SFT preference training using binary desirable/undesirable labels from game outcomes.

**Pipeline (4 new scripts, KAE-13):**
1. `score_sessions.py` — Scores sessions 0-1 from: XP delta (15%), level delta (15%), quest progression via actual state changes (20% — completions 1.0, stage advances 0.4, accepts 0.2), progress events (10%), unique positions (15%), avg turn score (15%). Penalties: respawns, click_tile rate, repetitive loops, stuck rate, deaths. Top 40% → desirable, bottom 30% → undesirable.
2. `build_kto_dataset.py` — Sliding windows (size=5, stride=2) over labeled sessions. Local window quality gating: positive floor 0.45, negative ceiling 0.60.
3. `finetune/train_kto_modal.py` — KTO on r6 merged. Current path uses `ref_model=None + precompute_ref_log_probs=True` to avoid keeping a second 9B reference model resident during training. LR=5e-7, beta=0.1, desirable_weight capped at 3.0.
4. `inspect_kto_dataset.py` — Dry-run: label balance, session counts, sample inspection.

**Key design decisions (Codex-reviewed):**
- `level_delta/3.0` not /1.0 — scales across multi-level sessions without saturating
- Removed `attack_rate > 0.80` penalty — was biasing against AGGRESSIVE personality sessions
- Canonical Qwen tokenizer for chat-template formatting — avoids Unsloth/Qwen3-VL template drift that broke prompt/completion splitting
- `ref_model=None + precompute_ref_log_probs=True` — TRL PEFT path. Precomputes reference log probs up front instead of holding a separate reference model in GPU memory during training

**Status:** Pipeline fully validated. Smoke test ran 10/10 steps cleanly — `train_loss=0.617`, KL divergence active (0.14→0.32 across steps), eval ran at steps 5 and 10. Save fallback in place (commit 34314ad). Ready for full run — Niral to greenlight.

**Smoke test path (5 attempts, each teaching something):**
1. batch=4 → OOM at `rejected_logits` (ref model forward)
2. batch=2, explicit bf16 ref → OOM at `_compute_kl_logps` (KL pass)
3. batch=1 → `ValueError`: KTOTrainer requires batch > 1 (KL dataset mismatching)
4. batch=2, 8-bit ref → `AttributeError: weight.CB` (bitsandbytes + Unsloth cu128 incompatible)
5. `ref_model=None + precompute_ref_log_probs=True`, batch=2 → training passed, save raised Unsloth LoRA mismatch → fallback fix → full pass

---

## r7 — Expanded Dataset + Critical Fixes (Apr 9)

**What changed:**
1. **Chat template fix (QwenLM/Qwen3#1831)** — Stock Qwen 3.5 template silently drops `<think>` reasoning from all assistant messages before `last_query_index` in multi-turn conversations. Our multi-turn training windows (70% of records) had CoT stripped from all intermediate turns — model was learning "action only, no reasoning" for follow-up turns. Patched to always emit `<think>` when `reasoning_content` is present.
2. **Personality labels** — `detect_personality()` was returning None for all records (metadata.json path mismatch). Added fallback mapping from agent_N directory to personality. Dataset now labeled: 39% aggressive, 31% methodical, 29% curious. Paraphrase augmentation now varies personality instructions during training.
3. **rsLoRA** — Added `use_rslora=True`. Scales LoRA by `1/sqrt(r)` instead of `1/r`, which stabilizes training at r=64 (Kalajdzievski 2023). Prevents effective LR from being too aggressive when alpha=r.
4. **Expanded dataset** — 575 sessions extracted (was 395), 14,091 turns → 6,401 train / 583 val (was 3,957/488). 62% more data.
5. **Quest progression scoring** — KTO session scoring now uses actual quest state deltas (completions, stage advances, new accepts) instead of NPC-talk-count proxy.

**Dataset:** 6,401 train / 583 val. ~23.7M tokens. Action distribution: navigate 27.7%, attack 14.9%, cancel_nav 10.7%, interact_npc 10.7%, warp 9.4%, stuck_reset 7.5%, move 7.1%, click_tile 3.9%.

**Config:** LoRA r=64, alpha=64, `use_rslora=True`, 1 epoch, LR=1e-4, `completion_only_loss=True`, bf16, H100 80GB. See `research/decisions/r7-hyperparameters.md` for parameter rationale.

**Estimated:** 400 steps, ~12h wall time on H100.

---

## r7-KTO — Preference Learning on Expanded Data (Apr 9, pending)

**What changed from r6-KTO:**
1. Quest progression scoring weights: XP 15%, levels 15%, quest progression 20% (actual state deltas), progress events 10%, exploration 15%, turn quality 15%.
2. Chat template fix applied to `fmt_tok` in KTO script.
3. Experiment name → `kaetram-qwen3.5-9b-r7-kto`.
4. Will rebuild KTO dataset on r7 extracted data (577 sessions scored, 231 desirable / 173 undesirable / 173 neutral).

**Config:** Same as r6-KTO: beta=0.1, LR=5e-7, `ref_model=None + precompute_ref_log_probs=True`, window_size=5, stride=2.

---

## Infrastructure Notes

**Platform:** Modal (H100 80GB for KTO, T4/L40S for SFT). Unsloth for LoRA, TRL for KTO/GRPO trainers.

**Known issues:**
- Unsloth LoRA count mismatch: PEFT save fails when adapter count != expected. Fallback to standard PEFT save implemented (commit 34314ad).
- Qwen3-VL tokenizer routing: Unsloth r6 tokenizer routes through Qwen3-VL processor, causing `processing_class` errors. Fix: use base tokenizer explicitly.
- Orphaned Chromium/MCP processes: Agent restart leaves zombie processes. Fix: process group kill with SIGTERM → SIGKILL timeout (commit 5e1b4df).
- Explicit reference-model KTO runs OOMed repeatedly on H100 80GB. Current workaround is `ref_model=None + precompute_ref_log_probs=True`, which removes the separate reference-model residency cost at training time.
- **Tool count drift (April 8):** MCP server now has **22 tools** (was 18 at r5 training time). New tools: `buy_item`, `gather`, `loot`, `query_quest`. RAG-MCP (arxiv 2505.03275) reports degradation above ~19 tools. Next SFT dataset will include these new tool calls — monitor for tool selection confusion in the student model. Context-dependent tool filtering (KAE-15) becomes more urgent.

---

## What's Next

Immediate: **Launch r7 SFT** (`modal run finetune/train_modal.py`) → r7 KTO (`modal run finetune/train_kto_modal.py`) → eval (base vs r7-SFT vs r7-KTO). That 3-model comparison is the paper result.

Backlog (by priority from Linear):
- **High:** Dr. GRPO + DAPO patches for GRPO (KAE-12), guided decoding via GBNF grammar (KAE-14), context-dependent tool filtering (KAE-15)
- **Medium:** Memory module for play_qwen.py — inject memory.txt into system prompt (KAE-20, Stage 1 = no retraining), self-play data loop (KAE-16), world model synthetic rollouts (KAE-17), Tree-GRPO (KAE-18), ORAK 3-stream SFT (KAE-19)
