# Session Log
_Keep under 30 lines. Update at end of every session. Most recent first._

---

## 2026-04-18 — r10 Launch Gate Closed (9 / 11 criteria green)

**Shipped on `feat/kae-42-remaining-patches` (6 commits):** KAE-42 data-pipeline patches (window_size 5→3, observe→observe bigram filter + post-build dedup, observe tool_result entity caps, stale click_tile filter removed, pre-tokenize truncation gate); Qwen3.5-9B thinking-general decode params wired into `serve_modal*.py`; three new regression tests (`test_truncation`, `test_think_roundtrip`, `test_loop_noise`); r10 launch gate doc.

**Key finding:** `test_think_roundtrip` initially "caught" the Qwen3 #1831 `last_query_index` gate stripping `<think>` from intermediate assistant turns — but training + serving both already apply `_patch_qwen_chat_template` at runtime (Niral's fix in `train_modal.py:183`). Test was running against the UNPATCHED tokenizer. Fixed test to import and apply the runtime patch; 5 assistant turns → 5 `<think>` blocks confirmed end-to-end.

**Dataset rebuild:** `23,382` train / `2,590` val (vs r9's `5,871` / `575`). Observe: `33,291 / 61,412` tool calls = 54%. Truncation gate: 0/25,982 rejected. Observe-pair dedup: 10/25,982 (0.038%) dropped.

**All 78 tests pass on rebuilt dataset.** 9 of 11 launch-gate criteria green. Remaining: smoke SFT (50 steps, ~$10) + eval matrix on none+aggressive. See `docs/r10_launch_gate.md`.

**Next:** run smoke SFT on the rebuilt dataset. If no warp/equip/dialogue loops in smoke eval, launch full r10.

---

## 2026-04-17 — r10 P0 Fixes: Observe Supervision + Personality Prompt Parity

**Two P0 bugs found and fixed** (diagnosed from cofounder memo + code audit):

1. **Zero observe supervision in r9 training.** `dataset/qwen_sft/train.json` had 21,976 assistant tool calls, 0 were `observe`. Root cause: `extract_turns.py:875-879` was consuming Sonnet's observe tool_use blocks to populate `game_state` and discarding the tool_use itself; `convert_to_qwen.py:build_user_message` then injected state into every user message. Model was trained in a world where state is free; at inference the live prompt mandates observe. Base called observe 131×/ep, r9-sft only 54×/ep.

2. **Personality prompt mismatch.** Training used `PERSONALITY_SUFFIXES` dict (2 sentences, ~190 bytes); eval loaded full `prompts/personalities/*.md` file (~1.5 KB with concrete rules like "kill 3+ mobs between NPC interactions"). The stale `PERSONALITY_INSTRUCTION_VARIANTS` dict in `train_modal.py:124-145` was silently overriding metadata.

**Fixes landed (A+X+P path per plan):** extract_turns.py emits observe as first-class turn; convert_to_qwen.py maps observe→tool_call, drops `<game_state>` injection, loads full .md personality files; train_modal.py + train_kto_modal.py substitute at `__PERSONALITY_BLOCK__` placeholder (byte-parity with eval_harness); score_sessions.py filters observe from KTO scoring; 23 new regression tests (`tests/test_prompt_parity.py`, `tests/test_observe_supervision.py`, additions to `tests/test_dataset_filters.py`).

**Dataset regenerated.** 12,900 train / 1,470 val (vs 5,871/575 in r9 — +120%). Observe: 26,995 calls (57.1% of 47,267 total). Token budget: 6.9% of records over MAX_SEQ_LEN=16384 — actually slightly better than r9's ~9% because dropped `<game_state>` per-user offsets added observe tool_results. 64/64 tests pass.

**Open question:** window size 5→3 to reduce truncation further. User said hold; discuss first. Launch r10 after that call.

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
