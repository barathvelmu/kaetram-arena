# Session Log
_Keep under 30 lines. Update at end of every session. Most recent first._

---

## 2026-05-22 — final r10-sft vs base eval matrix (n=4 base / n=3 SFT clean)

Two more runs landed since May 20: `run_20260520_143530` (base #4, 3h) and `run_20260520_173902` (sft #3, 3h). **All 4 base runs identical at 7/30 Core 3 stages** (1/3✅/3✅ — zero variance across 12 days, 4 fresh Mongo states). **SFT n=3: [3, 1, 2]**, mean 2.0/30, std 1.0. Stats now robust: Mann-Whitney per-run **p=0.016**, per-agent (n=12 vs 9) **p=0.001**, Fisher Foresting completion (9/12 vs 1/9) **p=0.006 OR=24**. Foresting rate **75% → 11% (6.75× drop)**. Completionist tool-mix: `interact_npc` 5.6× suppressed, `query_quest` 4.8× suppressed, `navigate` 4.49× amplified. **Smoking gun: SFT inference matches corpus within ±1pp** on every key tool (corpus interact_npc 2.4% ↔ SFT 2.1%; navigate 27.6% ↔ 26.4%). Base diverges 2-5× from corpus — chat-model prior leaks through. Cost ~$30 Modal. Data collection complete; next is charts + writing for May 25 post.

## 2026-05-20 — clean r10-sft vs base headline + buggy SFT deleted

Three new 3h runs with `play_qwen.py` JSON-dict fix (commit `7bf7c8d`): `run_20260519_223921` (base), `run_20260520_014319` (sft), `run_20260520_044433` (sft). Combined with prior clean base runs the matrix was **n=3 base / n=2 SFT, all 3h, clean wire**. Headline: **base 7/30 Core 3 stages every single run** (zero variance — three identical 1/3/3 splits), **SFT mean 2.0/30** (3 and 1). **3.5× regression**; `interact_npc` suppressed 6.25× and `navigate` amplified 4.08× on completionist — corpus prior becomes inference prior. Buggy `run_20260512_120516` (mislabeled as base in old docs; actually `r10-sft` per harness_meta) deleted. Cost ~$22 Modal at this point.

---

## 2026-05-11 — log_analysis decoder fix for qwen wire format

`scripts/log_analysis/parse.py:decode_tool_result_content` only handled Claude's `{"result": "<inner>"}` tool_result wrapper. Qwen harness writes raw MCP output bare (no wrapper), so observe payloads — which carry `<JSON>\n\nASCII_MAP:<grid>` — failed `json.loads` with "Extra data" and degraded to raw strings. Result: `latest_observe()` returned None for every qwen session; analyzer reported `?` for lvl/hp/pos and `untouched` for quests on every qwen run. Same MCP, same observe.js, same prompts — only the harness adapter's logging differs. Fixed with a 4-branch decoder (Claude wrapper / qwen non-observe dict / qwen observe with ASCII suffix / plain string). Verified: 477/477 qwen sessions (100% observe→dict, 100% with quest keys), 1049 Claude sessions (zero regression — 32020/32495 parse rate identical to pre-fix), 22 OpenCode sessions (unchanged, separate parser path). Re-ran on `run_20260510_173852` (qwen-base, 3 agents, 3h, 1742 turns): grinder Foresting 1/3 stuck, completionist + explorer **both finished Foresting 3/3** (explorer's first stage advance via `gather(Oak)` not `interact_npc`); Herbalist's + Rick's Roll untouched by all. core3_stages 1/10/3/10/3/10. Format/argument 100%.

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
