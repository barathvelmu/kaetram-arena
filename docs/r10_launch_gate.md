# R10 Launch Gate
Status: `GATED` — do not launch `r10` until every item below passes.

## Lanes
- Barath: `KAE-42` patches, regression tests, decode-mode alignment, smoke SFT, eval matrix
- Niral: `KAE-41`, game/source knowledge, fresh Claude collection runs

## Verified Facts
- Canonical r9-era SFT taught `0` `observe` calls out of `21,976` assistant tool calls.
- Training prompt and live eval prompt are not identical: `11,546` bytes vs `15,318-15,319` bytes.
- Clean VM eval `20260417_003705_curious` shows `r9-sft` worse than base on quests/achievements.
- Current `main` checkout still shows dead tools in `prompts/system.md` / `convert_to_qwen.py`.
- Current `main` checkout still shows stale click-tile filtering and no verified truncation gate.
- Qwen official decode recommendations exist and current serving params do not fully match them.

## Launch Criteria
- [ ] `tests/test_prompt_parity.py` passes.
- [ ] `tests/test_observe_supervision.py` passes.
- [ ] `tests/test_truncation.py` passes.
- [ ] `tests/test_think_roundtrip.py` passes.
- [ ] `tests/test_tool_vocab_drift.py` passes.
- [ ] `tests/test_loop_noise.py` passes.
- [ ] `KAE-42` patches 1-6 are present on the exact training branch.
- [ ] Dead tools are removed from training prompt + dataset tool definitions.
- [ ] Decode mode is chosen and applied consistently in `serve_modal.py` and `serve_modal_base.py`.
- [ ] Rebuilt SFT dataset has nonzero `observe` coverage.
- [ ] Smoke SFT (`~50` steps) shows no tool call repeated `>=5` times consecutively in any episode.
- [ ] Eval matrix (`none`, `aggressive`) does not show the smoke model collapsing into pathologies.

## Out Of Scope
- `KAE-41` / upstream game-source fixes
- DRY sampler
- Harness loop guardrails
- `r=32` vs `r=64` ablation
- KTO-on-r9 decision

## Execution Sequence
1. Land prompt-parity and observe-supervision fixes.
2. Land and verify `KAE-42` patches on the real training branch.
3. Add the six regression tests above and make them pass.
4. Align serving decode params to one chosen Qwen mode.
5. Run smoke SFT.
6. Run eval matrix on `none` and `aggressive`.
7. If all gates pass, `r10` becomes eligible to launch.

Context: see `KAE-42` for patch details and linked evidence; this doc is the launch gate, not the analysis log.
