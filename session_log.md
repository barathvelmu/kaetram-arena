# Session Log
_Keep under 30 lines. Update at end of every session. Most recent first._

---

## 2026-05-10 — Qwen first-class + warm-session loop

Three landed:
1. **Qwen first-class peer of Claude/OpenCode** — `--qwen-sft N` / `--qwen-base N` in orchestrate, restart-agent, resume-agent, single-restart. `QwenAdapter` auto-labels model from endpoint; dashboard renders QWEN badge + SFT/BASE chip; dead `mode=="qwen"` filters in `dashboard/api.py` removed; Eval-tab R9→R10 drift fixed. Cost-tracking branch reads `usage` from Claude-shaped records.
2. **Tool calls work for base** — pass `tools=` to chat completions; `serve_modal_base.py` honors via `apply_chat_template(tools=...)`; server-side adapter converts OpenAI string-arguments → dict + strips inline `<tool_call>` XML so Qwen's native template doesn't double-render. SFT serve still drops `tools=` (training parity).
3. **Warm-session loop** — `play_qwen.py` now runs one long-lived process per AgentInstance; on context overflow it rotates session log + `.session_counter` internally via new `SessionLogger` class. MCP/Chromium/login/Xvfb/ffmpeg persist across rollovers. orchestrate respawns only on hard crash. Stale watchdog tightened to 5min for Qwen. **Smoke: 8 sessions / 40 tool calls in 5min vs 5 sessions / 15 tool calls in cold-start version (2.7x).** `eval_harness` rewritten to time-based scenarios (`duration_minutes`, `--max-duration-seconds`); inner sub-session loop removed; one play_qwen per episode aggregates across all session logs. `--max-turns` / `--session-n` dropped from Qwen path entirely.

**Tests added.** `tests/unit/test_play_qwen_log_shape.py` (7) updated for logger-based emitters. New `tests/unit/test_play_qwen_session_loop.py` (7) covers SessionLogger semantics + outer-loop rotation. `tests/unit/test_qwen_adapter.py` (8) updated for new contract (drops --max-turns / --session-n from argv; threads run_dir / harness_meta / max_duration_seconds).

**Docs synced.** `CLAUDE.md`, `README.md`, `research/experiments/training-runs.md`, this log. `dashboard/api.py` `default_models` gets a `qwen` entry; `index.html` `hColors`/`hLabels`; `R9`→`R10`. `QWEN_DEFAULT_ENDPOINT` alias dropped (only test referenced it).

---

## 2026-05-09 — r10 SFT pipeline cleanup + dataset rebuild on full-length CoT

End-to-end audit + fixes. 70 unit tests green. Truncation gate at `kept_max_tokens=16,382`; dataset rebuilt (`dataset/qwen_sft/metadata.json`); training timeout 72h. `convert_to_qwen.py` drops `format_reasoning` (tail-keep is known-bad per arxiv 2512.21002); `<think>` rendered verbatim. `finetune/render.py` SYSTEM_PROMPT_INTRO_VARIANTS regenerated. Data collator gets per-batch `labels != -100` assert (TRL #3927 guard). GRPO/KTO marked deferred. Docs synced across `CLAUDE.md`, `dataset/{DATA.md,qwen_sft/README.md}`, `reference/MODAL.md`, `research/experiments/{training-runs,data-quality}.md`.
