# Kaetram Arena — Research Knowledge Base

Compiled knowledge for the this research project. Two independent papers: Paper 1 (Kaetram distillation, ICLR 2027) and Paper 2 (RuneScape adversarial multi-agent, planned).

**Rule:** After any training run, data rebuild, or design decision, update the relevant file here. If no file fits, create one and link it below. Without this, the wiki dies.

**Reliable maintenance flow:**
- LLM compile pass when explicitly requested: `.claude/commands/compile-research.md`
- Cheap VM-safe staleness check: `python3 scripts/check_research_staleness.py`
- VM-safe staleness check with email nudge: `python3 scripts/check_research_staleness.py --notify`
- VM cron-friendly wrapper: `scripts/run_research_staleness_check.sh`

The durable loop is VM cron + the wrapper. The wrapper first runs the staleness checker, then auto-invokes Claude Code with `/compile-research` using `claude-opus-4-6` when stale if Claude CLI is installed and authenticated on the VM. If research files changed, it stages `research/` + `session_log.md`, commits, rebases, and pushes. If Claude CLI is unavailable, it falls back to an email nudge.

---

## Methodology shift at r10

**r1-r9 were largely vibe-coded** — fast exploratory cycles with minimal formal process. Real wins landed (loss masking, observe supervision, prompt parity), but decisions, data choices, and patch motivations were rarely written down. r10 marks the methodological shift: tool surface centralized in `tool_surface.py`, decisions logged in `research/decisions/`, data filters justified empirically against the live corpus, no iteration-history comments left in code, and the pipeline (`extract_turns.py` + `convert_to_qwen.py`) was rewritten without fossils.

---

## Experiments

- [training-runs.md](experiments/training-runs.md) — r1 through r9-SFT (+ r6-KTO smoke test): hyperparams, results, failures, what improved
- [r10-concerns.md](experiments/r10-concerns.md) — r10 design decisions, known limitations (3-turn window ceiling, session_n drift, gate dropouts), Core 3 forecast, r11 experiment candidates
- [data-quality.md](experiments/data-quality.md) — Filters applied, before/after metrics, what got cut and why

## Related Work

- [preference-learning.md](related-work/preference-learning.md) — KTO, DPO, GRPO, Tree-GRPO, Dr. GRPO, DAPO landscape + how we use them
- [agent-sft-landscape.md](related-work/agent-sft-landscape.md) — FireAct, Agent-FLAN, SAD, AgentTrek, AgentRefine, Agent-R1, ToolACE, GamingAgent — foundational agent SFT papers
- [adversarial-agent-landscape-2026-04.md](related-work/adversarial-agent-landscape-2026-04.md) — Adversarial agent safety field map: Apollo, METR, Redwood, Palisade, Haize, Far.AI + where game envs fit

## Decisions

- [why-kto-over-ppo.md](decisions/why-kto-over-ppo.md) — Binary labels from game outcomes, why KTO fits our data, computational tradeoffs
- [r7-hyperparameters.md](decisions/r7-hyperparameters.md) — Research-backed rationale for every r7 SFT + KTO parameter

## Paper

- [contribution.md](paper/contribution.md) — Paper 1: What's novel, framing, outline, key ablations needed
- [VARIABLES.md](paper/VARIABLES.md) — Design-variables catalog (KAE-49): every knob reviewers can question, grouped by layer
- [paper2-runescape-vision.md](paper/paper2-runescape-vision.md) — Paper 2: RuneScape adversarial multi-agent — research tracks, platform (LostCityRS + rs-sdk), prior work, setup TODOs

---

## Recent Major Changes (Apr 24 – May 12, 2026)

- **serve_modal scale-to-zero (May 11).** `min_containers=0` on `kaetram-qwen-serve` — $0/hr when idle vs ~$1500/month always-on. `serve_modal_base.py` still at `min_containers=1` (pending update).
- **SGLang tokenizer fix (May 12).** `serve_modal.py` BASE_MODEL_ID reverted from `unsloth/Qwen3.5-9B` to `Qwen/Qwen3.5-9B` — SGLang's transformers can't read unsloth's `tokenizer_config.json` (tx 5.x class). Merged checkpoint's local tokenizer_config is patched in-place; `patch_qwen_chat_template` normalizes identically across both repos.
- **log_analysis qwen wire-format decoder (May 11).** `parse.py:decode_tool_result_content` fixed 4-branch decoder for Qwen bare MCP output. 477/477 qwen sessions now parse correctly. First qwen-base 3h run results: completionist + explorer both finished Foresting 3/3; Herbalist's + Rick's Roll untouched. `core3_stages_advanced`: 1/10, 3/10, 3/10 across agents.
- **Qwen first-class harness (May 10).** `--qwen-sft N` / `--qwen-base N` in orchestrate, restart-agent, resume-agent, single-restart. `QwenAdapter` auto-labels model from endpoint; dashboard renders QWEN badge + SFT/BASE chip. Cost-tracking reads `usage` from Claude-shaped records. Tool calls work for base (`serve_modal_base.py` honors `tools=` via `apply_chat_template(tools=...)`).
- **Warm-session loop (May 10).** `play_qwen.py` runs one long-lived process per AgentInstance; on context overflow it rotates session log + `.session_counter` internally via `SessionLogger`. MCP/Chromium/login/Xvfb/ffmpeg persist across rollovers. 2.7x throughput improvement (8 sessions / 40 tool calls in 5min vs 5 / 15 cold-start). Eval harness rewritten to time-based scenarios (`duration_minutes`, `--max-duration-seconds`).
- **r10-concerns.md (May 10).** Design decisions, known limitations (3-turn window ceiling, session_n drift, gate dropouts, death-zone unenforceable without memory), Core 3 forecast, r11 experiment candidates. Base 3hr baseline launched.
- **PR #29 — Modular MCP refactor merged.** Split monolithic `mcp_game_server.py` into typed capability modules. Reduced model-visible surface to **17 typed game tools** (was 22), keeping us below the RAG-MCP 19-tool degradation threshold. Deprecated wrappers retained for log back-compat in `extract_turns.py` only.
- **Capability archetypes shipped (KAE-46).** AGGRESSIVE/METHODICAL/CURIOUS personalities replaced by capability archetypes: **completionist / grinder / explorer_tinkerer**. Audit (n=30 hand-coded, n=731 automated) found that "task pressure dominates personality" — agents converge to similar action distributions under quest deadlines. Archetypes capture orthogonal capability axes instead of cosmetic style flavor. Closes the old "Personality ablation results" gap.
- **Tier-A unblock pass.** Cleared blocking deps for the quest benchmark — observe supervision live, prompt parity locked, eval harness wired against new archetypes.
- **OpenCode harness expanded to 6-model registry.** `--opencode-model` flag with aliases: `grok-4-1-fast`, `qwen3.5-35a3b`, `qwen3.5-397a17b`, `qwen3-80a3b`, `deepseek-v4-flash`, `deepseek-v4-pro`. Model-aware bot usernames (BigQwenBot, GrokBot, DeepSeekBot) allow per-model dashboard/log separation.
- **Economy patch (Apr 28).** Foraging gates dropped (25→10→5), mining removed from agent flow entirely, Miner shop reframed as general outfitter. Bronze/gold kits purchasable. Unblocks Herbalist's Desperation.
- **Rule 17 — death-zone exclusion.** 50-turn lockout on the mob that killed the agent, preventing death loops.
- **`analyze.py metrics`** — paper-quality 5-metric scorer for evaluating agent runs against the research metrics. Run-aggregated across every session in the run; Core 3 denominator is **10 stages** (sum of `stages` per quest from `prompts/quest_walkthroughs.json`), so partial progress moves the metric. Companion subcommands: `quest` (per-Core-3 stage timeline + reasoning at each advance + tool/error breakdown while active), `quest --cross-run` (max-stage histogram across every run per agent — answers "where do agents plateau?"), `errors --by-quest` (failures sliced by which Core 3 quest was active).
- **KAE-49 created** — design-variables catalog (`research/paper/VARIABLES.md`).
- **KAE-50 created** — quest benchmark framing for Paper 1.
- **r10 dataset rebuilt (May 7–10).** `dataset/qwen_sft/` regenerated from the post-Core-3 Claude corpus only: 5 runs × 3 agents = 135 sessions, 19,152 raw turns. Mixed-mode thinking ratio gate (≤25% non-thinking assistant turns, `6601b3c` May 8) + strict 16,384-token truncation gate (commit `1175358` May 9, dropped 4,799 overlong records) → **8,510 train / 853 val = 9,363 records** (live counts in `metadata.json`). Provenance in `metadata.json`. Old r10 + backups in `dataset/_archive/`. Launch-gate concept retired — benchmark = live Core 3 completion in `tests/e2e/quests/`.
- **`quest_resume.json` removed (May 7).** Commit `09e611d` dropped cross-session memory injection from the agent entirely. Sessions are now fully amnesic. Resolves the train/eval scaffolding asymmetry confound documented in `contribution.md`.
- **Eval pipeline upgraded (May 7).** `core3_stages_advanced` added as headline metric (capped at 10). `eval_compare` supports N-model Bonferroni FWER correction via `compare_n_models()`. `--episodes` default raised to 50 (paper-minimum). `serve_modal.py` defaults to r10 via env-overridable `SFT_EXPERIMENT`.
- **r10 training ETA (revised May 10).** Truncation gate reduced corpus from 14,162 checked to 9,363 kept records, dropping ETA from ~38h to ~22h on H100. Now fits within Modal's 24h timeout — packing/checkpoint-resume decision no longer blocking.
- **Mixed-mode SFT / Thinking Mode Fusion (May 8).** `convert_to_qwen.py` now emits non-thinking assistant turns (empty content + tool_call) when Sonnet fired a tool without CoT, rather than fabricating a filler `"Assessing situation."` placeholder. The `_enforce_thinking_ratio` gate downsamples records so ≤25% of assistant turns are non-thinking (per Qwen3.5 guidance). Combined with the chat-template patch that handles both thinking and non-thinking turns, the model learns when CoT is needed vs when a reflexive action suffices.
- **Dead-code + cruft sweep (May 8).** Commit `127c77a` removed: `play_opencode.sh`, `scripts/migrate_logs_to_runs.py`, `scripts/chain-runs.sh`, `scripts/format-vertical.sh`, `scripts/cut-highlight.sh`, `scripts/play_session.mjs`, `scripts/analysis/find_warp_route.py`, `scripts/analysis/test_timing.py`, `finetune/convert_gguf.py`, stale `state_extractor.js` helpers, `.gist_id`, `.codex`. Tightened `.gitignore`.
- **DeepSeek V4 reasoning capture (Apr 29).** New SSE-rewriting proxy on `:8890` (`scripts/start-deepseek-proxy.sh`, reuses `nim_proxy.py`) brings DeepSeek V4 Pro/Flash to parity with NIM/Qwen on chain-of-thought capture. OpenCode 1.14.29 doesn't read `delta.reasoning_content` for `@ai-sdk/openai-compatible`, so without the proxy the CoT was billed but dropped. Companion: `_strip_think_tags_from_history` strips wrapped CoT from assistant message history before forwarding (DeepSeek otherwise echoes prior reasoning + emits malformed `<that>` close tags). All 6 OpenCode models now produce surfaced CoT — useful for cross-model thinking-quality analysis.
- **Tool API auto-action consolidation (Apr 29).** `attack` auto-loots on kill, `buy_item` auto-walks to NPC + opens shop, `craft_item` auto-walks to nearest crafting station. `interact_npc` return fields disambiguated into `quest_opened` / `quest_accepted` / `quest_offered` / `quest_state_changed` (was conflated). Effect on training data: shorter trajectories per quest step, fewer "navigate then act" bigrams, more interpretable quest-acceptance signal in extracted turns. Older logs in the dataset still carry the manual patterns; mixing requires harness-aware extraction.
- **Data scale milestone (May 3, updated May 13).** Post-archive-split active corpus: 34 runs / 4,036 sessions across 3 agents (post-Core-3 Claude only). 1,694 non-active sessions archived under `dataset/raw/_archive/` (1,049 Claude + 645 non-Claude). r10 dataset uses 5 source runs / 135 sessions from this active pool. Rick's Roll stage-2+ prompt knowledge shipped May 1 (`154badc`).
- **Quest knowledge parity pass (May 1).** Misalignments fixed between e2e reachability tests and `game_knowledge.md` / `quest_walkthroughs.json`. Key fixes: cooking-station lookup now reads runtime `query_quest.station_locations.cooking` (was an unreachable hard-coded coord); Rick stage-2 puzzle decoys expanded; Rick + Lena turn-ins documented as **TWO `interact_npc` calls** (matches R5 test, explains "Thank you, I'm so touched" stuck-state); Mermaid level fact corrected against `mobs.json`; tomato/paprika coords drifted 1 tile, fixed; chained-craft + 2-call turn-in caveats added to GAME MECHANICS; Coder's Glitch / Glitch II / Coder's Fallacy walkthrough JSON statuses flipped to `off-limits` with `blocked_reason` populated. Unit tests refreshed against current truth; 104 passing, 2 legit skips.
- **Run-scoped log analysis + quest-stage progression (Apr 30).** `scripts/log_analysis/analyze.py` rewritten to aggregate every session in the latest run by default (was: latest session only); `--run <id>` parses every session in a past run, `--session N` drills back down. New subcommand `quest` emits per-quest stage transitions with the trigger tool, the model's reasoning at each advance, NPCs talked to, and tool/error breakdown while each quest was active; `quest --cross-run` produces a max-stage histogram across every run per agent. `errors --by-quest` slices errors by which quest was active. `metrics` denominator computed from `prompts/quest_walkthroughs.json` stage counts and uses last-vs-first-observe delta. OpenCode/DeepSeek parser at parity with Claude (cost + tokens aggregated from `step_finish`, `<think>` extraction). `scripts/export_report.py` rebuilt on the same parser kernel with per-agent cross-run summaries (`agents[id].summary`); `scripts/dataset_stats.py` deleted. EST timestamps now 12-hour AM/PM. `Tier-A` framing retired across all active docs.

---

## Gaps (articles needed but no source material yet)

- **World model evaluation** — Per-field accuracy, rollout drift, MCTS impact on gameplay. `world/evaluate.py` exists but results not compiled.
- **Agent distillation landscape (CRADLE, Voyager)** — `agent-sft-landscape.md` covers foundational papers; CRADLE and Voyager still need detailed side-by-side comparison with our MCP-based approach.
- **Self-play loop design** — STaR, ReST-EM, ETO patterns. Becomes relevant when KAE-16 starts.
- **Tool count scaling analysis** — Post PR #29: **17 typed model-visible tools** at inference (reconfirmed May 2, under the RAG-MCP 19-tool threshold; + 2 test-lane-only tools not loaded in production). Need to confirm tool selection accuracy on the trimmed surface; informs KAE-15 priority.
- **Cross-harness / cross-model comparative analysis** — Tooling complete (`analyze.py metrics` 5-metric scorer, `quest --cross-run` histograms, `errors --by-quest`). 6 OpenCode models + Claude/Codex/Gemini integrated with model-aware bot usernames. DeepSeek V4 Pro 8h run completed Apr 29 but results not formally compared. Blocking data: need at least one full multi-model parallel run with matched duration/archetype.
- **SOTA prompting compliance (system.md)** — Deferred May 1 (knowledge parity now 100%). `system.md` is 2,725 words, MUST count reduced to 3 (was "2× overuse"). Still missing `<verification>` block; rule duplication across system.md and game_knowledge.md remains. Prompt-architecture pass is the next prompt-layer task.

## Action Items (data pipeline)

_Completed items (r7/r8 SFT, serving, eval r8, loss masking fix, Qwen agent infra, dashboard tabs) removed — see git history for details._

- **Eval runs (r9):** r9 training COMPLETE Apr 16. Early eval showed r9-SFT underperformed base (1.5 quests / 28.5 kills / L24 vs base 2.5 / 26.5 / L20). Root cause identified → r10 P0 fixes (observe supervision, prompt parity). Full eval matrix never executed against r9.
- **r10 SFT launch:** Dataset rebuilt 2026-05-10 (9,363 records, post-thinking-ratio + truncation gates). ETA ~22h on H100 — fits Modal's 24h timeout. Run launches `kaetram-qwen3.5-9b-r10` on Modal H100; eval matrix compares r10-sft vs r9-sft vs base on Core 3 via N-model Bonferroni pipeline.
- **Launch r9 KTO:** Deferred indefinitely — pipeline focuses on the quest-completion benchmark. Scaffolding intact (`finetune/train_kto_modal.py`, validated via r6-KTO smoke 10/10 steps).
