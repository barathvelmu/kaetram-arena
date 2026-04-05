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
| r6-KTO | Apr 5 | KTO | 2,771 train / 273 val KTO windows | Preference learning on scored sessions | Smoke test running on `ref_model=None + precompute_ref_log_probs=True` path |

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
1. `score_sessions.py` — Scores sessions 0-1 from: XP delta (normalized), level delta (/3.0), quest actions, progress events, unique positions, avg turn score. Penalties: respawns, click_tile rate, repetitive loops, stuck rate, deaths. Top 40% → desirable, bottom 30% → undesirable.
2. `build_kto_dataset.py` — Sliding windows (size=5, stride=2) over labeled sessions. Local window quality gating: positive floor 0.45, negative ceiling 0.60.
3. `finetune/train_kto_modal.py` — KTO on r6 merged. Current path uses `ref_model=None + precompute_ref_log_probs=True` to avoid keeping a second 9B reference model resident during training. LR=5e-7, beta=0.1, desirable_weight capped at 3.0.
4. `inspect_kto_dataset.py` — Dry-run: label balance, session counts, sample inspection.

**Key design decisions (Codex-reviewed):**
- `level_delta/3.0` not /1.0 — scales across multi-level sessions without saturating
- Removed `attack_rate > 0.80` penalty — was biasing against AGGRESSIVE personality sessions
- Canonical Qwen tokenizer for chat-template formatting — avoids Unsloth/Qwen3-VL template drift that broke prompt/completion splitting
- `ref_model=None + precompute_ref_log_probs=True` — TRL PEFT path. Precomputes reference log probs up front instead of holding a separate reference model in GPU memory during training

**Status:** Code complete and reviewed. Smoke test is actively running. Earlier attempts OOMed with explicit reference-model variants; the current path is the first one that cleared the immediate OOM point and began reference-log-prob precomputation.

---

## Infrastructure Notes

**Platform:** Modal (H100 80GB for KTO, T4/L40S for SFT). Unsloth for LoRA, TRL for KTO/GRPO trainers.

**Known issues:**
- Unsloth LoRA count mismatch: PEFT save fails when adapter count != expected. Fallback to standard PEFT save implemented (commit 34314ad).
- Qwen3-VL tokenizer routing: Unsloth r6 tokenizer routes through Qwen3-VL processor, causing `processing_class` errors. Fix: use base tokenizer explicitly.
- Orphaned Chromium/MCP processes: Agent restart leaves zombie processes. Fix: process group kill with SIGTERM → SIGKILL timeout (commit 5e1b4df).
- Explicit reference-model KTO runs OOMed repeatedly on H100 80GB. Current workaround is `ref_model=None + precompute_ref_log_probs=True`, which removes the separate reference-model residency cost at training time.

---

## What's Next

Immediate: finish r6-KTO smoke test → full run if stable → deploy.

Backlog (by priority from Linear):
- **High:** Dr. GRPO + DAPO patches for GRPO (KAE-12), guided decoding via GBNF grammar (KAE-14), context-dependent tool filtering (KAE-15)
- **Medium:** Self-play data loop (KAE-16), world model synthetic rollouts for GRPO (KAE-17), Tree-GRPO (KAE-18), ORAK 3-stream SFT (KAE-19)
