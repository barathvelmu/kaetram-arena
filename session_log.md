# Session Log
_Keep under 30 lines. Update at end of every session. Most recent first._

---

## 2026-04-15 — r9 Training Launched + New Data Collection

**r8-SFT underperformed base model.** Evals showed base gets 2x kills, higher level, more quests. Root cause: train/inference mismatch (wrong system prompt, 69% turns had no reasoning, 55% records truncated). All fixed in commit `40a2dfc`.

**r9 fixes reviewed and launched.** Niral reviewed, committed second round of fixes (`998b865`): removed double tool definitions (F3), removed `<memory>` block from training prompt (F8), added degenerate record filtering for empty-reasoning and truncated records (F9/F10), bumped EXPERIMENT_NAME to r9 (F14). Both Claudes agreed r9 was good to launch.

**r9 training running on Modal** (launched 23:22 UTC Apr 15 via `modal run finetune/train_modal.py`). Dataset: **5,871 train / 575 val** (down from 6,380 after degenerate filtering). 100% reasoning coverage. Real inference system prompt. MAX_SEQ_LEN=16384.

**3 new Claude Sonnet agents launched** for continued data collection (`orchestrate.py --agents 3 --hours 4`). Verified `extract_turns.py` handles all 22 MCP tools for future data.

**Next:** Monitor r9 training completion, then eval base vs r8 vs r9. KTO run after r9 eval.

---

## 2026-04-15 — Research Compile Pass

**Compile-research pass.** 8 stale items fixed across 6 research files (eval harness gap marked DONE, loss masking refs updated, training-runs/paper/contribution updated for r8). No new gaps identified.

---

## 2026-04-14 — r8 SFT Complete, Eval Harness Ready

**r8 SFT completed** ~06:30 UTC on Modal H100. `train_on_responses_only` loss masking verified. `serve_modal.py` deployed with r8 weights. Eval harness set up (`dataset/eval/` with `base/` and `r8-sft/` subdirs). No eval runs executed yet at this point.
