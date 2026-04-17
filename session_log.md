# Session Log
_Keep under 30 lines. Update at end of every session. Most recent first._

---

## 2026-04-17 — Eval Watchdog Landed + Cross-Machine Sync Protocol

**Watchdog shipped to main.** `scripts/eval_watchdog.py` + `eval_harness.py` `--watchdog` flags + dashboard banner, all merged via `feat/eval-watchdog` (`f72c201`). It already earned its keep — caught a real failure during the curious-n10 eval and triggered `curious_n10_recover`, since archived.

**Cross-machine sync protocol added to CLAUDE.md.** An agent tonight edited files on a stale VM checkout before pulling Niral's `c7fe0b8` (DB quest tracking) + `eff051f` (Qwen Live removal). The diff looked like a revert of both; it wasn't (origin/main stayed intact, nothing bad got pushed), but the confusion triggered an argument. New rules: pull-before-edit, branches for shared code, `git stash push -u` safety net for VM sync. All documented in the new CLAUDE.md "Multi-Machine Sync Protocol" section.

**VM external IP changed** 35.224.227.251 → 34.28.111.6 (Niral's `8a04a3a`). All doc references updated.

**Next:** r10 training on verified dataset (6353/587 SFT, 3212/313 KTO). Base vs r9-sft curious eval already archived.

---

## 2026-04-17 — Research Compile Pass

**Compile-research pass.** Restored research/ from git (was deleted when gitignored). Updated 7 files across the knowledge base:
- **training-runs.md**: r9 status → COMPLETE, added r10 entry, updated serving endpoint to r9, updated What's Next with SOTA prompting, DB-authoritative quest tracking, eval infra consolidation
- **preference-learning.md**: Pipeline sequence updated (r9 DONE, eval IN PROGRESS), eval infra updated (removed agent_4/5 refs, added watchdog)
- **contribution.md**: Updated eval status (r9 in progress), replaced stale Qwen Live tab ref with quantitative eval harness
- **INDEX.md**: Updated timeline ref, fixed eval gap (DONE), updated action items (agent_4/5 removed, r9 eval in progress, added r10 item), added SOTA prompting gap
- **r7-hyperparameters.md**: Sequence length updated (8192→16384 in r9), added chat template hardening note
- **data-quality.md**: Updated agent_4/5 note (slots removed)

**Key findings:** r9 eval running but early results (2 episodes) still show base ahead of r9-sft. r10 experiment name set but not launched. No new Linear issues checked (MCP not available for this pass).

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
