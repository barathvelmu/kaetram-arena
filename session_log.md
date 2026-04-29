# Session Log
_Keep under 30 lines. Update at end of every session. Most recent first._

---

## 2026-04-29 — Tool API Auto-Actions + DeepSeek V4 Reasoning Capture + Reachability Patches

**Tool surface simplified.** `attack` auto-loots on kill (response carries `auto_loot: {looted, target}`); `buy_item` and `craft_item` auto-walk to NPC/station — agents no longer call `interact_npc` before `buy_item` or `navigate` before `craft_item`. `interact_npc` return fields disambiguated: `quest_opened` (panel appeared) vs `quest_accepted` (we accepted) vs `quest_offered` (offer name) vs `quest_state_changed` (any quest-list delta — covers turn-ins/stage advances). `set_attack_style` rejects unknowns; `navigate` adds `short_path` status + structured error envelope. `prompts/system.md` Rules 7/8 + tool table updated; old "navigate to station_locations first" guidance retired.

**DeepSeek V4 Pro/Flash now surface reasoning end-to-end.** New `scripts/start-deepseek-proxy.sh` reuses `nim_proxy.py` against `api.deepseek.com:8890` (opencode 1.14.29's `@ai-sdk/openai-compatible` doesn't read `delta.reasoning_content` — issue #24097). `opencode.template.json` reroutes deepseek to `127.0.0.1:8890` with `interleaved.reasoning_content`. `nim_proxy._strip_think_tags_from_history` strips wrapped CoT from assistant message history before forwarding (DeepSeek otherwise echoes prior reasoning + emits malformed `<that>` close tags). `orchestrate.py` manages both proxies; `restart-agent.sh` + `resume-agent.sh` boot them. `dashboard/parsers.py` parses `<think>` blocks + opencode `reasoning` event-parts as 🧠 with per-turn dedupe. **Active 8h run today: 3 agents on `deepseek-v4-pro` (one per archetype).**

**Resume harness/model auto-detect.** `resume-agent.sh` reads prior harness from log mtime (>120s old to avoid in-flight poisoning) and prior opencode model from `/tmp/kaetram_agent_N/opencode.json` so a bare `--hours N` resume preserves harness identity. Without this, orchestrate.py:1804 silently padded missing slots with Claude.

**Reachability sweep landed.** R4/A2 (Mongo autosave race fixed by polling live observe instead of `count_saved_inventory`); R6 stage-2 maze (explicit 4-door chain via `(424,902)` waypoint → `(425,901)` → `(453,904)` → `(453,907)` → `(426,927)` → `(431,920)` → `(455,930)`); A4 rewritten as `test_a4_buy_beryl_from_miner` (canonical buy path; mining off-route per economy patch); S8 door-step coord fix; S9 + A4 `skipif KAETRAM_LIVE_SUITE` (warm-pool in-memory quest state caches MQ at stage 0). `prompts/quest_walkthroughs.json` patched: Rick's Roll 4-door maze with decoy callouts; Sea Activities adds Mermaid `mermaidguard` prereq, fixes door-556 landing lie, adds explicit arena entry door `(693,836) → (858,808)`; A&C adds chained-fletch caveat (force menu closed between crafts).

**Dashboard Tests tab upgraded.** `KAETRAM_DEBUG=1` default for dashboard runs (per-test JSONL trace). Run history rebuilt with slim per-test case chips, JSONL reach-log tail viewer at `/api/test/reach_log`, last-run summary card. `/api/live` + `/api/agents` filter agents whose game-server port isn't listening (kills stale-sandbox UI ghosts after partial resumes). `_kill_helpers.sh` adds `/proc/$pid/cwd` sandbox detection so opencode subprocesses don't escape `kill_scoped` after orchestrate is killed first.

**Next:** assess deepseek-v4-pro 8h run output once it lands; consider whether tool auto-action simplification noticeably reduces wasted turns vs. prior runs.

---

## 2026-04-28 — Economy Patch + Mining-Free Playthrough

**Game-source + prompt patch landed on Niral's lane** to unblock Q2 (Herbalist) and remove Mining from the agent's mental model. Foraging gates dropped twice today (25 → 10 → 5) for `bluelilybush`, `tomatobush`, `paprikabush` — single ~25-blueberry grind unlocks all three Herbalist nodes. Miner shop reframed as a general outfitter: ores deeply cut (coal 3g, copper/tin 5g, gold 20g), beryl added at 20g (so Arts and Crafts no longer needs to mine), copper/tin starter swords (10g), full bronze kit (~560g), full gold kit (~3700g). Ghost-stock high-tier ores and the dead `alloweditems` field removed. Halved consumable prices in startshop + forester. Added price fields to coppersword/tinsword/bronzesword/goldsword so they sell back. Miner's Quest I/II marked off-limits in walkthroughs; pickaxe stays out of agent flow entirely. **Comprehensive .md sweep** also landed across both repos: `game_knowledge.md` (post-merge-conflict revert re-applied), `PLAYTHROUGH.md`, `GAME_SYSTEMS.md`, `QUEST_CITATIONS.md`, reachability `README.md` A4 marked deprecated.

**Kaetram-Open commits:** `005244e62` (foraging Lv5 + miner outfitter + items prices), follow-up doc-fix commit pending.
**kaetram-arena commits:** `421b4e2` (prompts: Foraging Lv5 + mining-free), follow-up doc-fix commit pending.
**Server not restarted** — active test run holds the old state until cycled.

**Next:** monitor next agent run for whether bronze-kit purchase becomes a routine action; if economy still feels frictionless or opaque, consider further price tuning.

---

## 2026-04-28 — Code Red Doc Catch-up + KAE-50 Q2/Q3 Strike Team

**10-day commit gap closed with documentation pass.** 7-agent strike team audited every tracked .md file against the cofounder's ~30 commits since Apr 18. Updates landed: `CLAUDE.md` (4-agent default, test-lane port 9191 / `TEST_AGENT_ID=99` callout), `dataset/DATA.md` (1,422 logs / r10 superseded / Tier-A signals listed / 3 archetypes named), `docs/r10_launch_gate.md` marked SUPERSEDED with pointer to `KAE-50`, `docs/dataset-regeneration-plan.md` marked HISTORICAL, `docs/behavior-audit.md` archival banner added (n=30 audit motivated KAE-46 reframe), `tests/e2e/quests/reachability/README.md` corrected (27 tests not 30, suite score replaced with live 2026-04-28 VM run: 20 PASSED / 2 FAILED on the fast subset).

**KAE-50 Q2 + Q3 strike-team audit landed earlier today** on branch `barathvelmu/kae-50-q2-q3-strike-team` (8 parallel agents, file:line evidence). Findings: **Q2 Herbalist** = decision gap — `game_knowledge.md` claims ~440 blueberry gathers to Lv25 (real number ~873); agents bail after 3-5 gathers; Blue Lily requires Foraging Lv10 but quest stage 0 needs 3 of them, structural wall at L1. **Q3 Rick's Roll** = data hallucination + capability gap — agents invent an "L25 zone gate" that doesn't exist (game_knowledge has shrimp spots at Fishing 1, no level requirement) and pivot to Desert Quest, where they die to L16 Sneks at L8. Live VM run snapshot: 0/3 agents accepted Q2 or Q3 across 38min of the active 4hr Sonnet run. Patch list staged on branch, deferred — Niral confirmed harness/game patches are his lane via KAE-44.

**Linear:** KAE-44 closed (Niral, Core 5 narrowed to 5 quests). KAE-45 closed (e2e harness on GPU server). KAE-46 closed (capability archetypes shipped). KAE-47 closed (PR #29 reviewed). KAE-48 closed (Tests tab onboarded). KAE-49 created (Barath assigned — design-variables paper catalog, VARIABLES.md attachment fetched). KAE-50 created Apr 28 (Sonnet → 100% Core 5; Barath owns Q2 Herbalist + Q3 Rick's Roll, Niral owns Q4 Arts and Crafts + Q5 Sea Activities). KAE-43 cancelled as duplicate of KAE-41.

**Next:** ship Q2/Q3 prompt-data fixes from `barathvelmu/kae-50-q2-q3-strike-team` (game_knowledge grind tables, Rick's Roll L1-safe-route note, Rule 9 tightening) once Niral validates. Capstone parallel.

---

## 2026-04-27 — Tier-A Unblock Pass + xAI/Grok Harness + Log Analyzer Upgrade

**Niral landed two large agent-side commits.** `61cf94f` "Tier-A unblock pass" — `live_gate_status` (per-quest blockers vs current player state), `quest_resume.json` cross-session memory, `recent_failures` injection, `mob_stats` enrichment (level + aggressive flag in every observe), `station_locations` (nearest crafting tile per skill), BFS→warp fallback when navigate fails, `migrate_logs_to_runs.py` (1,384 sessions → 237 runs in new `dataset/raw/agent_*/runs/run_<TS>/` hierarchy). `ef3bac4` wired xAI/Grok-4.1-Fast-Reasoning as 5th harness path (via `opencode.template.json`), kept NIM/Qwen alongside, hardened `nuke-agents.sh` (TERM-then-KILL with 2.5s grace so Mongo flushes), added auto `--help` guards to 18 scripts.

**Log analyzer CLI extended.** `scripts/log_analysis/analyze.py` now supports `status / runs / quests / tools / errors / recent / thinking / agent / timeline / tier_a` subcommands. Status: ~80% complete — timeline command has a stub bug (quest finish events never emitted, lines 317-321), no cost tracking despite `total_cost_usd` being parsed.

**Linear:** KAE-49 (paper-variables catalog) created, assigned to Barath. KAE-50 setup notes drafted for next-day publication.

---

## 2026-04-26 — Tests Tab MJPEG + Dashboard Realtime Perf + Screenshot Pipeline Cleanup

**PR #31 (Niral) merged.** Dashboard "Tests" tab now streams live MJPEG video for headed pytest runs against the test lane (port 9191, db `kaetram_e2e`, `TEST_AGENT_ID=99`, Xvfb display `:198`) — won't impact running data-collection agents. `dashboard/test_runner.py` (+597 lines) persists each run as `/tmp/test_runs/<id>/{progress.json,junit.xml,log.txt,meta.json}`, LRU-prunes to 20. Companion commit `2c9b4e0` ripped out the legacy screenshot writer pipeline (`live_screen.{jpg,png}`, `ScreenshotWatcher`, `/api/screenshots`) and renamed `KAETRAM_SCREENSHOT_DIR` → `KAETRAM_STATE_DIR`. `47271b9` added `log_analysis` CLI scaffolding and cleaned `/api/live`.

**Linear:** KAE-48 created and closed (Tests tab onboarding for Barath).

---

## 2026-04-25 — PR #29: Modular MCP + Core 5 + OpenCode + Capability Archetypes

**THE BIG ONE.** PR #29 merged — `mcp_game_server.py` collapsed from 2039 lines to a 19-line stub; tools split into modular `mcp_server/` package (`{core, helpers, login, mob_stats, resource_gates, state_heartbeat, utils}.py` + `tools/` subdir) preserving the 17-tool surface. Core 5 prompts/tests scaffolded as the canonical quest baseline (`core/test_0{1..5}_*.py` + `extra/`, `bonus/`, `skip/`, `reachability/` tiers — 136 quest tests total). `--opencode` harness added as fourth peer alongside Claude/Codex/Gemini, routes Qwen via NVIDIA NIM free tier through `scripts/nim_proxy.py` (SSE rewriter that surfaces reasoning tokens around opencode bug #5674). Capability archetypes — GRINDER / COMPLETIONIST / EXPLORER_TINKERER — replace AGGRESSIVE/METHODICAL/CURIOUS as the active personality system (KAE-46 closed). Same day: PR #30 synced KaetramGPU forked changes; `fe99dd7` made dashboard ~900× faster on log tail under multi-tab load (permessage-deflate WS, slim heartbeat); `68d63ef` documented archetype rename in `dataset/DATA.md` + `CLAUDE.md` freshness pass.

**Decision:** the frozen r10 dataset retains AGGRESSIVE/METHODICAL/CURIOUS labels in `metadata.json` — rename only goes forward. r10 launch becomes increasingly unlikely on this artifact; framing pivots toward Core 5 completion as the actual benchmark.

**Linear:** KAE-46 closed (archetypes). KAE-47 closed (PR #29 reviewed).

---

## 2026-04-19/20 — Quest Patches + E2E Test Reorg + MCP Smoke Baseline

**Five PRs across two days.** PR #24 (`eb1d67d`, 30 files, +4436/-731) — MCP smoke baseline e2e harness (`tests/mcp_e2e/`, pytest-asyncio, 27/28 pass), `dataset_stats.py` analyzer, MCP login/extractor race fix, lootbag popup, `equip_item`/`craft_item`/`buy_item` hardening, `interact_npc` proximity check. PR #25 reorganized tests into `unit/` + `e2e/` subdirs and nested `mcp` under `e2e/`. PR #26 deprecated REST-helper bootstrap tests, fixed `ToolResult.json()` (strip tool prefix + observe suffixes), made `attack()` only report `killed=true` on real HP transitions. PR #27 expanded e2e quest-phase coverage to 15 completable quests + `set_attack_style` + Anvil's Echoes reward fix. PR #28 dropped dashboard `MAX_AGENTS` 8 → 3. Apr 19 `70c79c0`: prompts updated to reflect Kaetram-Open quest patches (21/21 quests source-completable).

---

## 2026-04-18 — r10 Launch Gate Closed (9 / 11 criteria green)

**Shipped on `feat/kae-42-remaining-patches` (6 commits):** KAE-42 data-pipeline patches (window_size 5→3, observe→observe bigram filter + post-build dedup, observe tool_result entity caps, stale click_tile filter removed, pre-tokenize truncation gate); Qwen3.5-9B thinking-general decode params wired into `serve_modal*.py`; three new regression tests (`test_truncation`, `test_think_roundtrip`, `test_loop_noise`); r10 launch gate doc.

**Dataset rebuild:** `23,382` train / `2,590` val (vs r9's `5,871` / `575`). Observe: `33,291 / 61,412` tool calls = 54%. **All 78 tests pass on rebuilt dataset.** 9 of 11 launch-gate criteria green; smoke SFT + eval matrix never executed — overtaken by Core 5 pivot. Gate marked SUPERSEDED 2026-04-28.

---

## 2026-04-17 — r10 P0 Fixes + Eval Watchdog + Cross-Machine Sync Protocol

**Two P0 bugs fixed:** zero observe supervision in r9 training (`extract_turns.py` was discarding observe tool_use); personality prompt mismatch (training 2-sentence dict vs eval ~1.5KB .md file). Fixed via `extract_turns.py` emitting observe as first-class turn, `convert_to_qwen.py` mapping observe→tool_call, `train_modal.py` substituting at `__PERSONALITY_BLOCK__` placeholder for byte-parity with eval. 23 new regression tests.

**Eval watchdog** shipped (`scripts/eval_watchdog.py` + harness flags + dashboard banner). **Cross-machine sync protocol** added to CLAUDE.md after a stale-checkout incident produced revert-shaped diffs of cofounder commits.

---

_Older entries (r4 through r9 launches, March personality finalization, pre-Apr 17 compile passes) archived — see `research/experiments/training-runs.md` for full history._
