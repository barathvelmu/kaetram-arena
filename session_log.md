# Session Log
_Keep under 30 lines. Update at end of every session. Most recent first._

---

## 2026-05-09 — r10 SFT pipeline cleanup + dataset rebuild on full-length CoT

End-to-end audit and fixes across the SFT pipeline. Test suite now 70 unit tests green (was failing 3). Truncation gate populates `metadata.json::truncation_gate`; rebuild lands at `kept_max_tokens=16,382` (within the 16,384 bound).

**Code fixes (no scope additions, drift removal only):**
- `tests/unit/test_think_roundtrip.py` — broken import (`_patch_qwen_chat_template` from `train_modal`) → `patch_qwen_chat_template` from `finetune/render`. Mixed-mode assertion aligned with patched-template behavior (no-think turn renders empty `<think>\n\n</think>` per Qwen3 Thinking Mode Fusion).
- `finetune/serve_modal.py` + `serve_modal_base.py` — drop `tools=` kwarg from `apply_chat_template` (training never passes it; was emitting a duplicated JSON-schema tool block at serve time).
- `finetune/render.py` — `SYSTEM_PROMPT_INTRO_VARIANTS` regenerated from current `prompts/system.md` (variant 0 byte-identical to the canonical intro; variants 1-3 paraphrase the same framing).
- `convert_to_qwen.py` — delete `format_reasoning` (500-char tail-keep is "known-bad" per arxiv 2512.21002 / 2502.18001); `<think>` rendered verbatim. `_drop_overlong` is the only length authority. Deleted `build_user_message()` (orchestrate bootstrap now reconstructed from `session.meta.json` via `bootstrap.build_orchestrate_bootstrap`). Docstring trimmed.
- `finetune/train_modal.py` — wrap data collator with `(labels != -100).any(dim=-1).all()` per-batch assert (TRL #3927 guard).
- `finetune/train_grpo_modal.py` + `train_kto_modal.py` — DEFERRED status notes added (GRPO missing chat-template patch; KTO has `tools=` drift).

**Tests added.** `tests/unit/test_prompt_parity.py` now covers the rng-paraphrase path: variant 0 byte-equals `system.md` intro; every variant's body byte-equals canonical body after `\n\n<game_knowledge>`; every variant mentions Core 3 + `interact_npc` + `accept_quest_offer`.

**Dataset rebuilt 2026-05-09.** Live counts in `dataset/qwen_sft/metadata.json`. `truncation_gate.kept_max_tokens` ≤ 16,384 by construction. Training timeout bumped to 72h (`train_modal.py`).

**Docs synced.** `CLAUDE.md`, `dataset/DATA.md`, `dataset/qwen_sft/README.md`, `reference/MODAL.md`, `research/experiments/{training-runs,data-quality}.md`. Stale references swept (`_patch_qwen_chat_template` → `patch_qwen_chat_template` in `finetune/render.py`; record counts replaced with pointers to `metadata.json`; removed CLI flags purged).

---

## 2026-05-04 — Quest benchmark scoped to Core 3

Three quests treated as the canonical benchmark (Foresting + Herbalist's Desperation + Rick's Roll); offline BFS over `Kaetram-Open/.../world.json` confirms two prior candidates are structurally unreachable from a vanilla Mudwich state. Static layer (`test_static_world_connectivity.py`) verifies Core 3 quest coords against `world.json` BFS in <1s. Code, prompts, tests, and research narrative all kept in sync via subsequent sweeps.
