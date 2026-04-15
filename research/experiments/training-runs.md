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
| r7 | Apr 9-10 | SFT | 6,423 train / 646 val | Chat template fix, personality labels, expanded dataset | COMPLETE. Final loss 0.072. Deployed and tested. rsLoRA attempted and reverted (8x LR trap). |
| r8 | Apr 13-14 | SFT | 6,419 train / 646 val (4 filtered from r7's 6,423) | Loss masking fix (train_on_responses_only) | COMPLETE. Deployed on Modal. Eval harness set up (base vs r8-SFT). |
| r8-KTO | TBD | KTO | TBD | Preference learning on r8 merged weights | Pending r8 completion + Niral greenlight |

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

**Result:** Deployed and tested. Serve stopped to save Modal cost. Superseded by r8 SFT (loss masking fix).

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
3. **rsLoRA attempted and reverted** — Added `use_rslora=True` (Kalajdzievski 2023). Training diverged immediately. With `alpha=r=64`, rsLoRA scales by `alpha/sqrt(r) = 8.0` instead of standard `alpha/r = 1.0` — an 8x effective LR. Reverted to `use_rslora=False` (commit `685f649`). See CLAUDE.md gotchas for details.
4. **Expanded dataset** — 575 sessions extracted (was 395), 14,091 turns → 6,423 train / 646 val (was 3,957/488). ~62% more data. 618 raw session logs on disk.
5. **Quest progression scoring** — KTO session scoring now uses actual quest state deltas (completions, stage advances, new accepts) instead of NPC-talk-count proxy.

**Dataset:** 6,423 train / 646 val (7,069 total). ~23.7M tokens. Action distribution: navigate 27.7%, attack 14.9%, cancel_nav 10.7%, interact_npc 10.7%, warp 9.4%, stuck_reset 7.5%, move 7.1%, click_tile 3.9%.

**Config:** LoRA r=64, alpha=64, `use_rslora=False`, 1 epoch, LR=1e-4, `completion_only_loss=True`, bf16, H100 80GB. See `research/decisions/r7-hyperparameters.md` for parameter rationale.

**Status:** COMPLETE. Launched Apr 9 ~15:12 UTC, finished Apr 10 ~05:30 UTC (~14.5h). Final train loss: 0.072. Loss curve: 2.38 → 0.072, grad norms stable 0.007-0.017 throughout. First attempt died at 8h timeout (step 222/402); retried with 18h cap. Model deployed and tested via `play_qwen.py` — produces correct XML tool calls, follows priority system.

**Estimated:** 402 steps, ~12-14h wall time on H100.

---

## r8 — Loss Masking Fix (Apr 12)

**What changed:**
- **`train_on_responses_only` replaces broken `completion_only_loss`:** r5–r7 used `completion_only_loss=True` in `SFTConfig` with `dataset_text_field="text"`. TRL's `DataCollatorForCompletionOnlyLM` needs a `response_template` to identify where completions start — without one it silently skips masking. r5–r7 trained on ALL tokens including game state JSON, ASCII maps, and system prompts. Fix: removed `completion_only_loss`, added `train_on_responses_only(instruction_part="<|im_start|>user\n", response_part="<|im_start|>assistant\n")` from Unsloth after trainer init. This correctly zeros labels on all non-assistant tokens and trains only on `<think>` reasoning + tool calls.

**Note on r4 vs r5–r7:** r4 used `DataCollatorForCompletionOnlyLM` explicitly with a response template — this worked correctly. r5+ switched to `completion_only_loss=True` without a `response_template`, which silently regressed to full-token loss. r8 returns to correct masking.

**What's the same:** Dataset identical to r7 (6,423 train / 646 val). All 26 post-r7 logs are Gemini — zero new Claude data (verified by direct VM inspection Apr 12). r8 improvement comes entirely from correct loss masking.

**Config:** LoRA r=64, alpha=64, `use_rslora=False`, 1 epoch, LR=1e-4, bf16, H100 80GB. Experiment: `kaetram-qwen3.5-9b-r8`.

**Status:** COMPLETE. Launched Apr 13 ~16:30 UTC, finished ~06:30 UTC Apr 14 on Modal H100. Unsloth 2026.4.2, TRL 0.24.0, Transformers 5.5.0. `train_on_responses_only` applied successfully — 4/6,423 samples removed (all labels -100 after truncation). 402 steps. Merged weights deployed via `serve_modal.py`. Eval harness set up with `dataset/eval/` (base vs r8-SFT system prompts).

---

## r8-KTO — Preference Learning (pending r8 SFT)

**What changed from r6-KTO:**
1. Quest progression scoring weights: XP 15%, levels 15%, quest progression 20% (actual state deltas), progress events 10%, exploration 15%, turn quality 15%.
2. Chat template fix applied to `fmt_tok` in KTO script.
3. Experiment name → `kaetram-qwen3.5-9b-r8-kto`.
4. Will rebuild KTO dataset on r8 extracted data. Base SFT will be r8 merged weights (with correct loss masking).

**Config:** Same as r6-KTO: beta=0.1, LR=5e-7, `ref_model=None + precompute_ref_log_probs=True`, window_size=5, stride=2.

---

## Infrastructure Notes

**Platform:** Modal (H100 80GB for SFT/KTO training, A100 40GB for inference serving). Unsloth for LoRA, TRL for KTO/GRPO trainers. SGLang for inference.

**Serving endpoints (Modal):**
- `kaetram-qwen-serve` — finetuned model (SGLang, A100, `serve_modal.py`) — currently pointed at r8 (will serve after training + deploy)
- `kaetram-qwen-base` — unfinetuned Qwen3.5-9B baseline (SGLang, A100, `serve_modal_base.py`)
- Both scale to 0 when idle ($0 cost). Cold start ~3-6 min (model download + SGLang init).

**Known issues:**
- Unsloth LoRA count mismatch: PEFT save fails when adapter count != expected. Fallback to standard PEFT save implemented (commit 34314ad).
- Qwen3-VL tokenizer routing: Unsloth r6 tokenizer routes through Qwen3-VL processor, causing `processing_class` errors. Fix: use base tokenizer explicitly.
- Orphaned Chromium/MCP processes: Agent restart leaves zombie processes. Fix: process group kill with SIGTERM → SIGKILL timeout (commit 5e1b4df).
- Explicit reference-model KTO runs OOMed repeatedly on H100 80GB. Current workaround is `ref_model=None + precompute_ref_log_probs=True`, which removes the separate reference-model residency cost at training time.
- **Tool count drift (April 8):** MCP server now has **22 tools** (was 18 at r5 training time). New tools: `buy_item`, `gather`, `loot`, `query_quest`. RAG-MCP (arxiv 2505.03275) reports degradation above ~19 tools. Next SFT dataset will include these new tool calls — monitor for tool selection confusion in the student model. Context-dependent tool filtering (KAE-15) becomes more urgent.

---

## What's Next

Immediate: **r8 SFT COMPLETE** (Apr 14). Loss masking correct via `train_on_responses_only`. Deployed on Modal. **Eval harness IMPLEMENTED** (Apr 15): `eval_harness.py` (parallel model runs, log-based metrics), `eval_compare.py` (Glass's delta, bootstrap CIs, Bonferroni), `eval_offline.py` (offline action-prediction accuracy), `scripts/run-eval.sh`, dashboard eval tab. Next: **execute eval runs** (base vs r8-SFT) → r8-KTO → final comparison for paper.

**Qwen agent infrastructure (Apr 10):**
- Finetuned model: agent_4 slot, `QwenBot` username, `start-qwen.sh`
- Base model: agent_5 slot, `QwenBase` username, `start-qwen.sh --base`
- Dashboard: Qwen Live tab with split-screen MJPEG streaming (4 FPS), log polling
- Management: `start-qwen.sh`, `stop-qwen.sh`, `restart-qwen.sh`, `status-qwen.sh`

Backlog (by priority from Linear):
- **High:** Dr. GRPO + DAPO patches for GRPO (KAE-12), guided decoding via GBNF grammar (KAE-14), context-dependent tool filtering (KAE-15)
- **Medium:** Memory module for play_qwen.py — inject memory.txt into system prompt (KAE-20, Stage 1 = no retraining), self-play data loop (KAE-16), world model synthetic rollouts (KAE-17), Tree-GRPO (KAE-18), ORAK 3-stream SFT (KAE-19)
