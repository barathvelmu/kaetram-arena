# R10 Launch Gate
Status: `GATED` — do not launch `r10` until every item below passes.

## Lanes
- Barath: `KAE-42` patches, remaining regression tests, decode-mode alignment, smoke SFT, eval matrix
- Niral: `KAE-41`, game/source knowledge, fresh Claude collection runs

## Verified Facts (as of 160d662, 2026-04-17)
- r9-era SFT had `0` `observe` tool_calls / `21,976` assistant turns. **Fixed in r10 rebuild: 26,995 observe calls / 47,267 total (57.1%).**
- r9 training vs eval prompt diverged `11,546` vs `15,318-15,319` bytes. **Fixed in r10: byte-exact parity via shared `prompts/personalities/*.md` path, asserted by `tests/test_prompt_parity.py`.**
- VM eval `20260417_003705_curious` N=2: r9-sft worse than base on quests/achievements (base 2.5 quests / L20 vs r9-sft 1.5 quests / L24 w/ more churn).
- r10 dataset rebuilt: `12,900` train / `1,470` val (vs r9 `5,871` / `575`; +120%).
- 64/64 tests pass locally on `160d662`. Shipping tests: `test_prompt_parity`, `test_observe_supervision`, `test_chat_template`, `test_dataset_filters`, `test_loss_masking`, `test_pipeline_drift`.
- Qwen official decode params not applied in `serve_modal.py` / `serve_modal_base.py` (temp=0.7, top_p=0.9, no `top_k` / `presence_penalty`).

## Launch Criteria
- [x] `tests/test_prompt_parity.py` passes.
- [x] `tests/test_observe_supervision.py` passes.
- [ ] `tests/test_truncation.py` exists and passes (no record > `MAX_SEQ_LEN`).
- [ ] `tests/test_think_roundtrip.py` exists and passes (end-to-end tokenizer round-trip preserves `<think>` on every assistant turn, not just Jinja-fragment presence).
- [x] `tests/test_tool_vocab_drift.py` exists and passes (training tools == curated model-visible surface == live MCP export == `prompts/system.md` `<tools>` block).
- [ ] `tests/test_loop_noise.py` exists and passes (no observe→observe adjacency, no 3+ identical consecutive tool names in any record).
- [ ] `KAE-42` patches landed on training branch (window_size 5→3, observe→observe bigram filter, observe tool_result caps, pre-tokenize gate).
- [x] Dead tools (`accept_quest`, `clear_combat`, `talk_npc`) removed from `prompts/system.md` `<tools>` block + `dataset/qwen_sft/metadata.json` tools[].
- [ ] Decode mode chosen (thinking vs instruct) and applied consistently in `serve_modal.py` and `serve_modal_base.py` per Qwen3.5-9B model card.
- [ ] Smoke SFT (`~50` steps, H100, ~$10) shows no tool call repeated `>=5` times consecutively in any eval episode.
- [ ] Eval matrix (`none`, `aggressive`) on smoke model does not show the r9-class warp/equip/dialogue loop pathology.

## Out Of Scope
- `KAE-41` / upstream game-source fixes (Niral's lane)
- DRY sampler (not production-ready in vLLM/SGLang)
- Harness loop guardrails (P2, not blocking)
- `r=32` vs `r=64` ablation (P2; defensible experiment, not settled law)
- KTO-on-r9 decision (after SFT recipe verified)

## Execution Sequence
1. ~~Land prompt-parity and observe-supervision fixes.~~ **DONE via `160d662`.**
2. Land remaining `KAE-42` patches on main (window_size 5→3, observe-pair filter, entity caps, pre-tokenize gate, dead-tool removal).
3. Add the four remaining regression tests (`test_truncation`, `test_think_roundtrip`, `test_tool_vocab_drift`, `test_loop_noise`) and make them pass against current data.
4. Align `serve_modal.py` + `serve_modal_base.py` decode params to one chosen Qwen mode.
5. Run smoke SFT.
6. Run eval matrix on `none` and `aggressive`.
7. If all gates pass, `r10` becomes eligible to launch.

Context: see `KAE-42` for patch details and linked evidence; this doc is the launch gate, not the analysis log.
