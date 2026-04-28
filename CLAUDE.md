# CLAUDE.md — Kaetram AI Agent (Developer Reference)

> This file is for the human developer using Claude Code interactively. The
> agent subprocess launched by `play.sh` does not read this file — its
> instructions live in `prompts/system.md`. Do not add agent behavioral
> instructions here.

This is an autonomous AI agent that plays Kaetram (a 2D pixel MMORPG) using a
custom MCP server (`mcp_server/` package, entry point `mcp_game_server.py`)
that exposes typed game tools (observe, attack, navigate, etc.). The agent
calls structured tools — never writes JavaScript. Gameplay sessions are
collected as SFT/KTO training data for Qwen3.5 9B.

For current run state, training results, and what's in flight: read
`session_log.md`. This file is the stable reference that doesn't change weekly.

---

## Session startup

At the start of every new session:
1. Read this file.
2. Read `session_log.md` (recent decisions and context).
3. Read `.claude/commands/training-summary/history.json` if it exists (reward trends).
4. Then ask what the user wants to do.

At the end of every session, append to `session_log.md` (keep under 30 lines).
After any big change (training infra, dataset rebuild, architecture shift),
update `session_log.md` immediately, commit, push to GitHub, and sync the
VM if it runs that code.

---

## Multi-machine sync protocol

Two machines (laptop + GCP VM `34.28.111.6`) share `origin/main`. Edits on a
stale checkout produce diffs that look like reverts of missing commits — and
silently are. The 2026-04-17 cofounder incident is why this section exists.

1. **Pull before edit.** `git fetch origin && git pull --ff-only` on the
   machine you're about to touch. If non-ff, investigate; local has diverged.
2. **Branch for shared code, direct for solo lanes.** Push to `feat/…` /
   `chore/…` for anything your cofounder might edit (`eval_harness.py`,
   `dashboard/`, `prompts/`, `finetune/`, `scripts/`). Direct to main only
   for solo lanes (`research/`, `session_log.md`, `.claude/memory/`).
3. **VM sync when unsure:** `git stash push -u -m "safety-$(date +%s)"`
   before pulling. Stash-first means nothing is destroyed if a cofounder
   commit conflicts.

---

## Research knowledge base (`research/`)

Compiled knowledge: `experiments/`, `related-work/`, `decisions/`, `paper/`,
`INDEX.md`. Not stream-of-consciousness — `session_log.md` is the scratchpad
and `.claude/memory/` is per-user context.

After a training run, data rebuild, or design decision, update the matching
file under `research/` and link new files from `INDEX.md`. Maintenance is a
VM cron loop (`scripts/run_research_staleness_check.sh` → `/compile-research`
→ commit + push). Session-local Claude crons die with the session and are
not durable automation.

---

## Architecture

A harness CLI (Claude / Codex / Gemini / OpenCode) talks stdio to the
`mcp_server/` package, which drives Playwright on a Chromium browser pointed
at the Kaetram client (:9000). `state_extractor.js` exposes JS helpers
(`window.__extractGameState`, `__attackMob`, etc.) consumed by MCP tools via
`page.evaluate()`. Session logs flow `extract_turns.py` → `convert_to_qwen.py`
→ Qwen SFT/KTO records. `orchestrate.py` runs N agents in parallel, each
with its own game server, sandbox, MCP process, browser, and Xvfb display
(stride `+10` on the game-server WS port to leave room for `apiPort = P+1`,
dormant unless `API_ENABLED=true`).

**Livestream pipeline.** Each agent runs Xvfb + `ffmpeg x11grab` writing HLS
segments to `/tmp/hls/agent_N/`, served under `/hls/agent_N/*` on :8080 —
decoupled from `observe()` cadence so tiles keep streaming during long
thinking turns. `mcp_server.state_heartbeat` POSTs `window.__latestGameState`
to `/ingest/state` (300 ms) and tails the session log to `/ingest/activity`
(1 s); the dashboard rebroadcasts both over the WebSocket relay on :8081.
Full reference: `dashboard/DASHBOARD.md`.

## Key files

| Path | Purpose |
|------|---------|
| `mcp_server/` | Modular MCP package (6 root + 10 `tools/` Python files, 17 model-visible tools). See `mcp_server/README.md`. |
| `mcp_game_server.py` | 19-line stub — entry point that imports `mcp_server.tools` and runs the FastMCP loop. |
| `.mcp.template.json` | Template with placeholders (`__VENV_PYTHON__`, `__PROJECT_DIR__`, …). Resolved per-sandbox to `.mcp.json` by `cli_adapter.py` / `play.sh`. |
| `cli_adapter.py` | Harness abstraction: `ClaudeAdapter`, `CodexAdapter`, `GeminiAdapter`, `OpenCodeAdapter`. |
| `orchestrate.py` | Multi-agent launcher: spawns game servers, Xvfb, ffmpeg, MCP, harness; supervises restarts; tracks rate limits + budget. |
| `play.sh` | Single-agent loop. |
| `state_extractor.js` | Browser-side helpers exposed via `window.__extractGameState()` etc. Called by `mcp_server` only — never by the agent. |
| `mcp_server/resource_gates.py` | Loads resource→skill+level requirements from Kaetram-Open data files at MCP startup. `gather()` uses it to surface a structured `gate` block when "no items collected" is actually a skill-level gate. Override the data dir via `KAETRAM_DATA_DIR`. |
| `mcp_server/mob_stats.py` | Same pattern for mobs.json. `observe()` enriches each `nearby.mobs[]` entry with `level` + `aggressive` so the agent doesn't have to recall the MOB PROGRESSION table by name. |
| `state/quest_resume.json` (per-sandbox) | Written on every `observe()` by `mcp_server/tools/observe.py`. Read by `orchestrate.py` at session start and prepended to the agent prompt as a "Resume from last session" block — closes the per-session amnesia gap so multi-stage Core 5 quests can complete across sessions. |
| `orchestrate.py:_recent_failures_from_prev_session()` | Scans the previous session's log on each new session start, buckets distinct tool errors (BFS-fail × N, NPC arrived: false × N, etc.), and injects a `recent_failures (don't repeat)` block into the resume prompt. Carries cross-session FAILURE memory — agents stop re-trying the same dead-ends across sessions. Distinct from quest_resume's STATE memory. |
| `extract_turns.py` | JSONL log → OODA turn extraction. |
| `convert_to_qwen.py` | Turns → Qwen3.5 9B SFT/GRPO format. |
| `prompts/system.md` | Agent system prompt (~100 lines, XML-tagged). |
| `prompts/game_knowledge.md` | Quest guides, NPC coords, mob stats. |
| `prompts/personalities/*.md` | Archetype overrides (`grinder.md`, `completionist.md`, `explorer_tinkerer.md`). |
| `dashboard/server.py` | Dashboard entry point (HTTP :8080 + WS :8081). Full reference: `dashboard/DASHBOARD.md`. |
| `eval_harness.py` + `scripts/run-eval.sh` | Eval orchestrator: r9-sft vs base on dedicated ports 9061 / 9071. |
| `play_qwen.py` / `play_qwen.sh` | Finetuned-model harness — calls Modal SGLang endpoint, spawns the same MCP server. |
| `tests/e2e/quests/` | Reachability tier — per-step playthrough tests for Core 4 (Herbalist, Rick's Roll, Arts and Crafts, Sea Activities). Each step seeds the cumulative state an agent has at that point per game_knowledge.md. |

## Ports

Game-server port `P` reserves `P+1` for `apiPort` (currently dormant; matches
`start-test-kaetram.sh:45` and `orchestrate.py`). Agents stride by `+10`
(`orchestrate.py:65-67`).

| Port | What |
|------|------|
| 9000 | Kaetram client (HTTP, shared) |
| 9001 + N×10, N ∈ [0,8] | Multi-agent game-server WS. **Standard run is 3 agents — one per archetype** (grinder + completionist + explorer-tinkerer): 9001 / 9011 / 9021. |
| 9191 | Test-lane Kaetram (db `kaetram_e2e`, `TEST_AGENT_ID=99`, Xvfb `:198`) — isolated from data-collection lanes; dashboard Tests tab runs headed pytest against it |
| 9061, 9071 | Eval game servers (r9-sft, base) |
| 9191 | E2E test-lane game server (`scripts/start-test-kaetram.sh`, db `kaetram_e2e`) |
| 27017 | MongoDB (`kaetram-mongo`); per-lane isolation by db name |
| 8080 | Dashboard HTTP (UI + `/hls/agent_N/*` + `/ingest/{state,activity}`) |
| 8081 | Dashboard WebSocket relay (state, activity, heartbeat) |

---

## Managing training runs

| Script | Purpose |
|--------|---------|
| `scripts/restart-agent.sh [N] [H]` | **Primary command.** Kill all agents, reset MongoDB to Level 1, clear sandbox state, relaunch N agents for H hours (0 = no limit). |
| `scripts/resume-agent.sh` | Resume without DB reset. |
| `scripts/restart-single-agent.sh <ID>` | Restart one agent without touching the others. Always clears `.session_counter`. |
| `scripts/nuke-agents.sh` | SIGKILL everything agent-related. |
| `scripts/reset-state.sh [N] [--force]` | Reset Mongo player data without restart. |
| `scripts/start-kaetram.sh` | Single-agent dev game server (Node 20). |
| `scripts/start-test-kaetram.sh` | E2E test-lane server (port 9191, db `kaetram_e2e`). Safe alongside data collection. |
| `scripts/start-nim-proxy.sh` | NIM SSE-rewriting proxy (required for OpenCode reasoning capture; see Gotchas). |
| `scripts/collect_sft_data.sh N H` | End-to-end: orchestrate → extract → convert. |

All restart/resume scripts accept `--claude` / `--codex` / `--gemini` /
`--opencode` and `--grinder` / `--completionist` / `--explorer` (plus
`--hours`, counts per archetype). Run `scripts/restart-agent.sh --help`
for the full surface — examples have been moved out of this file to
prevent drift.

### Harnesses

`--claude` is the primary data-collection harness — fully integrated and the
only one whose turns flow into Qwen SFT training. `--codex` (GPT-5.4),
`--gemini` (Gemini 2.5 Flash), and `--opencode` run the same
orchestrator/dashboard/log paths but their turns are excluded from training
until validated. Use them for cross-harness comparisons, not training data.

`--opencode` is multi-model — pass `--opencode-model <alias|id>` to pick.
Aliases (resolved by `cli_adapter.OPENCODE_MODEL_ALIASES`):

| Alias                | Provider | Model |
|----------------------|----------|-------|
| `grok-4-1-fast`      | xAI direct                    | `xai/grok-4-1-fast-reasoning` |
| `qwen3.5-35a3b`      | NVIDIA NIM (proxy :8889)      | `nvidia/qwen/qwen3.5-35b-a3b` |
| `qwen3.5-397a17b`    | NVIDIA NIM (proxy :8889)      | `nvidia/qwen/qwen3.5-397b-a17b` |
| `qwen3-80a3b`        | NVIDIA NIM (proxy :8889)      | `nvidia/qwen/qwen3-next-80b-a3b-thinking` |
| `deepseek-v4-flash`  | DeepSeek direct (api.deepseek.com) | `deepseek/deepseek-v4-flash` |
| `deepseek-v4-pro`    | DeepSeek direct (api.deepseek.com) | `deepseek/deepseek-v4-pro` |

Provider blocks live in `opencode.template.json`. NIM-routed Qwen models
require `scripts/start-nim-proxy.sh` to be running (idempotent; `restart-agent.sh`
invokes it). DeepSeek requires `DEEPSEEK_API_KEY` in env; xAI uses `XAI_API_KEY`.

### Archetypes

Three orthogonal axes injected into `system.md` via `__PERSONALITY_BLOCK__`:
`--grinder` (combat/leveling), `--completionist` (progression), and
`--explorer-tinkerer` / `--explorer` (world + systems coverage). They're a
*data-factory* mechanism for trajectory diversity, not a paper claim — if
trajectories collapse we drop to two policies. Legacy vibe flags
(`--aggressive / --methodical / --curious`) are removed.

### SFT pipeline

`logs/session_*.log → extract_turns.py → convert_to_qwen.py →
dataset/qwen_sft/{train,val}.json`. Full action vocabulary, modes, record
counts, and lessons from r4-r10: `dataset/DATA.md` and
`research/experiments/training-runs.md`.

---

## Gotchas

- **Node 16/18/20 only** (uWS.js). `nvm use 20` before starting the server. Node 24/25 crashes.
- **`yarn build` after every Kaetram-Open patch.** `yarn start` alone fails. Any quest/mob/map JSON edit under `Kaetram-Open/` needs a rebuild.
- **Game-server port override.** `PORT=X yarn start` doesn't work — Kaetram reads `.env`, not `process.env`. Use `node dist/main.js --port X`. `orchestrate.py` does this.
- **`.mcp.template.json` vs `.mcp.json`.** The template is checked in; `.mcp.json` is the per-sandbox resolved copy. Claude reads the resolved copy via `--mcp-config --strict-mcp-config`.
- **OpenCode reasoning needs the NIM proxy.** NVIDIA NIM streams Qwen reasoning via `delta.reasoning_content`; OpenCode only reads `delta.content`. `scripts/nim_proxy.py` rewrites SSE so reasoning is captured. Start it before `--opencode`.
- **rsLoRA + `alpha=r` is an 8x LR trap.** rsLoRA scales `1/sqrt(r)` not `1/r`. With `r=alpha=64`, effective LR is 8x. r7 diverged. Keep `use_rslora=False` (the comment on `train_modal.py:359` is load-bearing).
- **Counting running agents.** `pgrep -fa "claude -p"` self-matches the shell that ran it (the pattern appears in its own cmdline). Count unique bot IDs from the output (`ClaudeBot[0-9]+`, `CodexBot[0-9]+`, `GeminiBot[0-9]+`, or for opencode: `BigQwenBot[0-9]+` / `GrokBot[0-9]+` / `DeepSeekBot[0-9]+` / `OpenCodeBot[0-9]+` depending on `--opencode-model`), or cross-check against listening game-server ports (`9001 + N×10`) — those are authoritative.
- **OpenCode bot username depends on the model.** The opencode harness splits its in-game username + Mongo player row by model family so dashboard / log analysis can distinguish runs: `*qwen*` → `BigQwenBot` (separate from the local-eval `QwenBot`), `*grok*` → `GrokBot`, `*deepseek*` → `DeepSeekBot`, otherwise `OpenCodeBot`. Logic lives in `cli_adapter.opencode_bot_prefix()` and is mirrored in `restart-single-agent.sh` + `play.sh`.
- **Qwen3 chat template drops `<think>` on intermediate turns** (QwenLM/Qwen3 #1831). Pre-r10 multi-turn records trained action-only on follow-ups. If you touch the tokenizer, re-run `tests/unit/test_think_roundtrip.py` to verify CoT survives `apply_chat_template`.

---

## Agent prompt design principles

Editing `prompts/system.md`, `prompts/game_knowledge.md`, or
`prompts/personalities/*.md`? Full research basis: `reference/SOTA_PROMPTING.md`.
Operating rules: total prompt under ~3K tokens; XML tags for structure
(Claude is trained on them); calm directives (Claude 4.6 over-triggers on
"CRITICAL/MUST"); explain WHY not just WHAT; reference data at top,
decisions at end (middle 40-60% is underweighted); personality = priority
modifiers only, never new rules; one tool per turn; keep the model-visible
tool surface in the high teens.

## Log analysis (`scripts/log_analysis/`)

Primary tool for "how are the agents doing" — parses session JSONL logs
under `dataset/raw/agent_*/runs/run_*/` (with `logs/` symlink to the latest
run) and reports per-agent status, quests, tool distribution, categorized
errors, Tier-A adoption, and reasoning. **Prefer this over LLM subagents for
live status / behavioral audit** — it parses fields directly (`active_quests`,
`live_gate_status.gated`, `inventory_summary.full`, mob `level`, etc.), so the
answer is ground truth not an inference, and it doesn't burn tokens.

By default scopes to currently running agents (log touched in last 10 min);
pass `--stale` for historical sessions, or `--run <run_id>` to scope to any
specific run.

```bash
# Live snapshot
python3 scripts/log_analysis/analyze.py            # full report
python3 scripts/log_analysis/analyze.py status     # one-line per agent + run header

# Historical / cross-run
python3 scripts/log_analysis/analyze.py runs -n 10            # last N runs across all agents
python3 scripts/log_analysis/analyze.py runs --all-runs       # every run ever
python3 scripts/log_analysis/analyze.py status --run <run_id> # status for a past run

# Behavioral audits (use these when assessing whether prompt/tool changes worked)
python3 scripts/log_analysis/analyze.py tier_a     # adoption: Rule 10 compliance, BFS→warp rate, A2 gate, mob-overshot, station_locations, drop@full
python3 scripts/log_analysis/analyze.py errors     # CATEGORIZED errors (BFS_NO_PATH, STILL_MOVING, NPC_NOT_FOUND, STATION_UNREACHABLE, …) + top next-action transitions
python3 scripts/log_analysis/analyze.py timeline -n 30   # chronological event stream (warps, accepts, BFS-fails, level-ups, deaths) for the live run

# Drill-downs
python3 scripts/log_analysis/analyze.py quests
python3 scripts/log_analysis/analyze.py tools
python3 scripts/log_analysis/analyze.py recent -n 8
python3 scripts/log_analysis/analyze.py thinking -n 3
python3 scripts/log_analysis/analyze.py agent 1 -n 10
```

**When to reach for which command:**
- Just stopped/restarted agents → `status` to confirm they're up + see run_id/elapsed
- "Did my prompt fix actually change behavior?" → `tier_a` (compares against the rules each fix targets)
- "Why is agent N looping?" → `errors` shows what failed + what it did next (warp vs retry-navigate is the smoking gun for Rule 4a)
- "What did agent N do today?" → `timeline` for an emoji-tagged event stream
- "How does this run compare to last week's?" → `runs -n 20` or `--all-runs`

See `scripts/log_analysis/README.md` for the log-shape reference. To write a
custom one-off analysis, import from `parse.py`:

```python
from scripts.log_analysis.parse import (
    latest_runs_per_agent, parse_run, parse_session_auto,
    tier_a_signals, categorize_error, RunView, SessionView,
)
```

`tier_a_signals(sv)` returns a `TierASignals` dataclass with the same metrics
the `tier_a` CLI command reports — useful for batch comparison across many
runs (e.g. "did Rule 4a uptake go up after we shipped the BFS→warp prompt?").

## Slash commands (`.claude/commands/`)

`/game-session` (stack status), `/verify-pipeline` (confirm data flow),
`/training-summary` (dataset stats), `/compile-research` (refresh `research/`,
also runs from VM cron).

Storage: Kaetram-Open is ~1.3-2 GB installed. See `TEARDOWN.md` for uninstall
or "keep but trim" (~1 GB reclaimed via `node_modules/dist` deletion).
