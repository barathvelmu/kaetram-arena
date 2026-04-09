# Kaetram AI Agent

An autonomous AI agent that plays [Kaetram](https://github.com/Kaetram/Kaetram-Open), a 2D pixel MMORPG, using a custom MCP server with typed game tools. The agent calls structured tools (observe, attack, navigate, interact_npc, etc.) — never writes JavaScript. Gameplay sessions are collected as SFT/KTO training data for finetuning Qwen3.5 9B.

> **For developers:** see [`CLAUDE.md`](CLAUDE.md) for the full developer reference, [`research/INDEX.md`](research/INDEX.md) for the compiled research knowledge base, and [`session_log.md`](session_log.md) for the most recent decisions.

## What it does

- Logs in, navigates the world, fights monsters, loots drops, talks to NPCs, completes quests
- Extracts real-time game state (nearby entities, combat events, XP) directly from the browser via `page.evaluate()`
- Records every action as a `(game_state, reasoning, action)` tuple
- Runs indefinitely in sessions — each session picks up where the last left off
- Supports multi-agent mode: run N agents in parallel for scaled data collection
- 3 agent playstyles (aggressive, methodical, curious) for diverse training data

## Current status (April 9, 2026)

- **Data collection active.** 3 agents (AGGRESSIVE, METHODICAL, CURIOUS) running on GCP VM.
- **Dataset:** 6,423 train / 646 val Qwen3.5 9B SFT records extracted from ~575 sessions.
- **Training:** `r7` SFT run launched on Modal H100 (April 9). Expanded dataset + chat template fix + rsLoRA + personality labels. See [`research/experiments/training-runs.md`](research/experiments/training-runs.md).
- **KTO pipeline** validated end-to-end (`score_sessions.py` → `build_kto_dataset.py` → `finetune/train_kto_modal.py`). Full `r7-KTO` run pending `r7` SFT completion.
- **World model** (2.2M param Transformer forward dynamics) trained — used for MCTS planning and reward shaping. See [`world/README.md`](world/README.md).

## Architecture

```
play.sh ──────────► Claude Code (Sonnet) ──► mcp_game_server.py (FastMCP) ──► Playwright ──► browser
                          │                        │                              │
                    reads system.md +         22 typed tools                 page.evaluate()
                    game_knowledge.md         (observe, attack,              calls state_extractor.js
                          │                   navigate, warp...)              helpers internally
                          │                        │
                          └──► logs/session_N_*.log (auto-logged JSONL)

                     dashboard (port 8080) ◄─── MongoDB (kaetram_devlopment, port 27017)
```

**`mcp_game_server.py`** — custom FastMCP server exposing 22 typed game tools. Manages Playwright browser internally. Agents call structured tools — never write JavaScript.

**`state_extractor.js`** — injected into browser via `context.add_init_script()`. Exposes `window.__extractGameState()`, `window.__attackMob()`, `window.__navigateTo()`, etc. Called by MCP server internally, never by the agent.

**`prompts/system.md`** — agent system prompt: OODA loop, decision tree, tool descriptions. Uses XML tags for structure. ~90 lines.

**`prompts/game_knowledge.md`** — game-specific knowledge (quest walkthroughs, NPC coords) appended to all agents

## Quick start

### Single-agent mode

Run each in its own terminal:

```bash
# Terminal 1 — Kaetram game server (Node 20 required)
./scripts/start-kaetram.sh

# Terminal 2 — Dashboard (optional, live monitoring)
python3 dashboard.py

# Terminal 3 — Agent loop (must be a separate terminal — see gotchas)
./play.sh
```

> **`play.sh` must always be in its own terminal.** Running it as a subprocess of Claude Code deadlocks both processes on the shared Playwright MCP browser.

### Multi-agent mode (scaled data collection)

Run N agents in parallel, each with its own Kaetram server instance. The preferred entry point is `restart-agent.sh`, which kills stale processes, resets MongoDB player state, clears sandbox state, and launches the orchestrator under tmux (`datacol` session):

```bash
# Default: 4 agents for 24 hours (round-robin personalities)
./scripts/restart-agent.sh

# 4 agents, no time limit
./scripts/restart-agent.sh 4 0

# One of each playstyle
./scripts/restart-agent.sh --aggressive 1 --methodical 1 --curious 1 --hours 0

# Resume without DB reset (preserves character progress)
./scripts/resume-agent.sh --hours 8

# Restart a single agent (0-3) without affecting the others
./scripts/restart-single-agent.sh 2 --reset
```

Each agent gets its own server port (9001, 9011, 9021, 9031), username (`ClaudeBot0`–`ClaudeBot3`), log directory, and personality. All agents get `prompts/game_knowledge.md` (quest guides, NPC coords, mob stats). Resource budget for 3 agents (active collection config): ~2.5 GB RAM, ~27% CPU, ~4.5 GB disk/24h.

> **Harness flags** (`--claude`, `--codex`, `--kimi`, `--qwen-code`): only `--claude` is production-ready and used for data collection. The others exist in `cli_adapter.py` but are WIP and not used for training data. See [`CLAUDE.md`](CLAUDE.md) for details.

### End-to-end data pipeline

```bash
# Orchestrate → extract → convert in one script
./scripts/collect_sft_data.sh 4 24    # 4 agents for 24 hours
```

## Training pipeline

Four-stage pipeline transforms raw Claude session logs into SFT + KTO training data for Qwen3.5 9B:

```
logs/session_*.log  ──►  extract_turns.py  ──►  dataset/extracted/*/turns.jsonl
                                                         │
                                                convert_to_qwen.py  ──►  dataset/qwen_sft/train.json
                                                         │                dataset/qwen_sft/val.json
                                                         │                      │
                                                         │          finetune/train_modal.py (SFT, H100)
                                                         │
                               score_sessions.py + build_kto_dataset.py
                                                         │
                                                dataset/kto/*.json
                                                         │
                                            finetune/train_kto_modal.py (KTO, H100)
```

**Stage 1: Extract turns** — Parses JSONL session logs, identifies OODA cycles (observe + reason + act), extracts game state, reasoning, and structured actions.

```bash
python3 extract_turns.py --log-dir logs/ --output-dir dataset/extracted/
```

**Stage 2: Convert to Qwen format** — Transforms turns into Qwen3.5 9B conversation records with `<think>` reasoning and structured `<action>` tags. 90/10 train/val split stratified by session.

```bash
# Default: mixed mode (70% multi-turn + 30% single-turn), SFT format
python3 convert_to_qwen.py --input dataset/extracted/ --output dataset/qwen_sft/

# Single-turn only (one state → one action per record)
python3 convert_to_qwen.py --input dataset/extracted/ --output dataset/qwen_sft/ --mode single

# Multi-turn with windowed context (state deltas across turns)
python3 convert_to_qwen.py --input dataset/extracted/ --output dataset/qwen_sft/ --mode multi

# GRPO format (prompt-only with reward context for reinforcement learning)
python3 convert_to_qwen.py --input dataset/extracted/ --output dataset/qwen_sft/ --format grpo
```

### Output format (Qwen3.5 9B SFT)

```json
{
  "messages": [
    {"role": "system", "content": [{"type": "text", "text": "<condensed game rules>"}]},
    {"role": "user", "content": [
      {"type": "text", "text": "<game_state>\n{...}\n</game_state>\n\nWhat should you do?"}
    ]},
    {"role": "assistant", "content": [{"type": "text", "text": "<think>\nI see a Rat at distance 2...\n</think>\n<action>\nclick(408, 312)\n</action>"}]}
  ]
}
```

### MCP Tool Vocabulary (22 tools)

| Tool | Description |
|------|-------------|
| `login` | Log into the game |
| `observe` | Game state JSON + ASCII map + stuck check |
| `attack(mob_name)` | Attack nearest mob by name |
| `set_attack_style(style)` | hack, chop, or defensive |
| `navigate(x, y)` | BFS pathfinding to grid coords |
| `move(x, y)` | Short-distance movement (< 15 tiles) |
| `warp(location)` | Fast travel (mudwich, crossroads, lakesworld). Auto-waits combat cooldown. |
| `cancel_nav` | Cancel navigation |
| `interact_npc(npc_name)` | Walk to NPC, talk through all dialogue, auto-accept quest |
| `talk_npc(instance_id)` | Continue dialogue with adjacent NPC |
| `accept_quest` | Manual quest accept |
| `buy_item(npc_name, item_index, count)` | Buy an item from an NPC shop |
| `eat_food(slot)` | Eat food to heal (fails at full HP) |
| `drop_item(slot)` | Drop item to free inventory space |
| `equip_item(slot)` | Equip item (returns success/failure with reason) |
| `clear_combat` | Clear combat state |
| `stuck_reset` | Reset stuck detection |
| `click_tile(x, y)` | Click grid tile (fallback) |
| `respawn` | Respawn after death |
| `gather(resource_name)` | Gather from a tree, rock, bush, or fish spot |
| `loot` | Pick up nearby ground items and lootbag contents |
| `query_quest(quest_name)` | Look up walkthrough for a specific quest |

> **Note:** The tool count grew from 18 → 22 on April 8 (`buy_item`, `gather`, `loot`, `query_quest`). RAG-MCP ([arXiv 2505.03275](https://arxiv.org/abs/2505.03275)) reports tool-selection degradation above ~19 tools; context-dependent tool filtering is tracked under KAE-15. See [`research/experiments/training-runs.md`](research/experiments/training-runs.md).

## Project structure

```
kaetram-agent/
├── mcp_game_server.py       # Custom FastMCP server — 22 typed game tools via Playwright
├── cli_adapter.py           # Harness abstraction (Claude = production; Codex/Kimi/Qwen Code = WIP)
├── play.sh                  # Claude Code agent loop (resolves .mcp.json template)
├── play_qwen.py             # Qwen agent loop — lightweight 2-tool harness
├── play_qwen.sh             # Qwen agent session launcher
├── play_opencode.sh         # OpenCode + Playwright MCP agent launcher
├── orchestrate.py           # Multi-agent launcher + health monitor + MCP detection
├── extract_turns.py         # JSONL log → clean OODA turn extraction
├── convert_to_qwen.py       # Turns → Qwen3.5 9B SFT/GRPO format
├── state_extractor.js       # Injected into browser — game helpers (called by MCP server internally)
├── .mcp.json                # MCP config template (placeholders resolved at launch)
├── dashboard.py             # Live web dashboard launcher (port 8080)
├── qwen_dashboard.py        # Lightweight MJPEG dashboard for Qwen agent (port 8082)
├── opencode.json            # OpenCode provider config (Modal/Ollama endpoints)
├── dashboard/               # Dashboard package (modular)
│   ├── api.py               # API endpoints (DB-first, log-fallback game state)
│   ├── constants.py         # Config (ports, paths, MongoDB connection)
│   ├── db.py                # MongoDB reader — authoritative player state
│   ├── game_state.py        # Game state extraction (DB-based + log-based fallback)
│   ├── handler.py           # HTTP request handler
│   ├── parsers.py           # Session log parsing utilities
│   ├── server.py            # HTTP + WebSocket server
│   └── templates/index.html # Dashboard frontend
├── finetune/                # ML training pipeline
│   ├── SETUP_3060.md        # RTX 3060 local deployment guide
│   ├── train_modal.py       # SFT training on Modal (Unsloth, H100)
│   ├── train_kto_modal.py   # KTO preference learning on Modal (H100)
│   ├── train_grpo_modal.py  # GRPO reinforcement learning on Modal
│   ├── serve_modal.py       # vLLM serving endpoint (OpenAI-compatible)
│   ├── convert_gguf.py      # Model → GGUF Q4_K_M conversion
│   └── merge_and_quantize.py # LoRA merge + GGUF export (local)
├── score_sessions.py        # Score sessions 0-1 for KTO labels (XP/quest/exploration)
├── build_kto_dataset.py     # Build sliding-window KTO prompt/completion/label records
├── inspect_kto_dataset.py   # KTO dataset dry-run and sample inspection
├── research/                # Compiled research knowledge base (see research/INDEX.md)
│   ├── experiments/         # Training run history, data quality metrics
│   ├── related-work/        # Paper surveys (KTO, DPO, GRPO, agent SFT landscape)
│   ├── decisions/           # WHY docs (why KTO over PPO, r7 hyperparameters)
│   └── paper/               # ICLR 2027 contribution framing
├── world/                   # Forward dynamics model (2.2M param Transformer)
│   ├── README.md            # Architecture overview + quickstart
│   ├── schema.py            # State/action encoding (16-dim vectors, 26 actions)
│   ├── model.py             # Transformer forward dynamics model
│   ├── extract_transitions.py # Extract (state, action, next_state) from logs
│   ├── train.py             # Local PyTorch training
│   ├── train_modal.py       # Modal cloud training (T4 GPU)
│   ├── evaluate.py          # Per-field accuracy + rollout drift metrics
│   ├── mcts.py              # MCTS planner for multi-step lookahead
│   └── demo.py              # Interactive terminal demo
├── prompts/
│   ├── system.md            # Base system prompt: login, OODA loop, targeting
│   ├── game_knowledge.md    # Game knowledge: quests, NPCs, mobs (appended to all agents)
│   └── personalities/       # Playstyle DECIDE overrides (aggressive, methodical, curious)
├── scripts/
│   ├── start-kaetram.sh     # Starts Kaetram server (handles nvm use 20)
│   ├── restart-agent.sh     # Primary command: kill + restart agents fresh (resets DB)
│   ├── restart-single-agent.sh # Restart one agent (0-3) without affecting the others
│   ├── resume-agent.sh      # Resume agents without DB reset
│   ├── nuke-agents.sh       # Stop all agents (SIGKILL everything)
│   ├── reset-state.sh       # Reset MongoDB player data only
│   ├── collect_sft_data.sh  # End-to-end: orchestrate → extract → convert
│   ├── check_research_staleness.py      # Detect stale research/ files
│   ├── run_research_staleness_check.sh  # VM cron wrapper: auto-compile + commit + push
│   ├── play_session.mjs     # Standalone Playwright script for manual testing
│   ├── cut-highlight.sh     # Extract highlight clips from recordings
│   └── format-vertical.sh   # Convert clips to 9:16 vertical format
├── .claude/commands/        # Claude Code slash commands
├── dataset/                 # Training data (gitignored)
├── state/                   # Runtime state (gitignored)
├── logs/                    # Claude Code JSONL session logs (gitignored)
├── session_log.md           # Running decision log across sessions
└── CLAUDE.md                # Developer reference for Claude Code
```

## Ports

| Port | What |
|------|------|
| 9000 | Kaetram game client (HTTP, shared across agents) |
| 9001 | Kaetram game server WS (single-agent default) |
| 9001, 9011, 9021, 9031 | Game server WS (multi-agent, one per agent) |
| 8080 | Dashboard |
| 8081 | Dashboard WebSocket relay (realtime screenshot push) |
| 8082 | Qwen dashboard (MJPEG stream) |
| 27017 | MongoDB (`kaetram-mongo` Docker container, db `kaetram_devlopment`) |

## Slash commands

| Command | When to use |
|---------|-------------|
| `/game-session` | Check what's running, get startup commands, see port status |
| `/verify-pipeline` | Confirm data is flowing, inspect latest training record |
| `/training-summary` | Dataset stats, reward trends, best/worst sessions |
| `/compile-research` | LLM compile pass over `research/` — fix stale facts, add missing entries |

## Gotchas

**Playwright subprocess deadlock** — `play.sh` must run in a separate terminal. Spawning it as a subprocess of Claude Code deadlocks both on the shared Playwright MCP browser.

**Node 20 required** — Kaetram uses uWS.js which only supports Node 16/18/20. Node 24/25 crashes on startup.

**Tutorial gate** — New players spawn in the Programmer's house behind a 16-stage tutorial. The agent uses warp to skip this.

**Absolute screenshot paths** — Playwright MCP requires absolute paths. Relative paths cause it to navigate the browser to the path as a URL.

**Multi-agent port conflicts** — If running `orchestrate.py`, kill any existing Kaetram servers first. The orchestrator manages its own server instances.

## Finetuned agent (Qwen3.5 9B)

The finetuned Qwen3.5-9B model can play autonomously using a lightweight 2-tool harness instead of Claude Code:

```bash
# Direct mode — play_qwen.py drives browser via Playwright, hits Modal/Ollama endpoint
./play_qwen.sh

# OpenCode mode — uses OpenCode + Playwright MCP with Ollama/Modal endpoint
./play_opencode.sh

# Monitor Qwen agent (MJPEG dashboard on port 8082)
python3 qwen_dashboard.py
```

**Dual-VM architecture:**
- **GCP VM** (`35.224.227.251`): Hosts Kaetram game server (:9001 WS) + client (:9000 HTTP), runs data collection and the training pipeline.
- **GPU VM** (`73.173.11.56:1738`, RTX 3060 12GB): Runs the finetuned model in Ollama + the `play_qwen.py` / OpenCode harness via Playwright. Connects back to the GCP VM for the game world.

See `finetune/SETUP_3060.md` for local deployment instructions.

## World model

A small Transformer forward dynamics model (2.2M params) predicts combat outcomes for MCTS planning and reward shaping:

```bash
# Extract transitions from session logs
python3 -m world.extract_transitions --log-dir logs/

# Train locally
python3 -m world.train --data dataset/world_model/transitions.pt

# Interactive demo
python3 -m world.demo
```

See `world/README.md` for architecture details.

## License

Tooling layer around [Kaetram-Open](https://github.com/Kaetram/Kaetram-Open) (MPL-2.0).
