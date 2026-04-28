# scripts/log_analysis

Comprehensive log analyzer for the Claude harness. Parses session JSONL logs
under `dataset/raw/agent_*/runs/run_*/` (with a `logs/` symlink pointing to the
latest run) and reports per-agent status, quest
progression, tool-call distribution, errors, recent activity, and reasoning.

By default it operates only on **currently running** agents (logs touched in
the last 10 minutes). Pass `--stale` to include older sessions.

## Usage

```bash
python3 scripts/log_analysis/analyze.py             # full report (default)
python3 scripts/log_analysis/analyze.py status      # one-line per agent + run header
python3 scripts/log_analysis/analyze.py runs -n 10  # last N runs across all agents (run.meta.json)
python3 scripts/log_analysis/analyze.py timeline -n 30   # chronological events for the live run
python3 scripts/log_analysis/analyze.py tier_a      # adoption metrics for the new tools/rules
python3 scripts/log_analysis/analyze.py metrics     # 5 paper metrics — format/argument/tool-sel/core5/turn-eff (Niral 2026-04-28)
python3 scripts/log_analysis/analyze.py quests      # quest progression detail
python3 scripts/log_analysis/analyze.py tools       # tool call counts + error rates
python3 scripts/log_analysis/analyze.py recent -n 8 # last N tool calls per agent
python3 scripts/log_analysis/analyze.py errors      # CATEGORIZED errors + next-action transitions
python3 scripts/log_analysis/analyze.py thinking -n 3   # last N reasoning blocks
python3 scripts/log_analysis/analyze.py agent 1 -n 10   # deep-dive single agent
```

> **Slash command:** also exposed as `/log-analysis` (`.claude/commands/log-analysis.md`)
> for Claude to invoke when the user asks about run state, paper metrics, or per-agent
> stuck-points. Skill is the canonical entry point — `analyze.py` is the implementation.

### Filters
- `--run <run_id>` — scope to a specific historical run (e.g. `--run run_20260427_135613`). Defaults to each agent's latest run.
- `--all-runs` — for `runs` cmd, list every run instead of the recent N.
- `--stale` — include agents whose log hasn't been touched in 10+ min.
- `--claude` / `--opencode` — force a parser (default: auto-detect from meta).

`status` is the fastest signal — run_id, level, HP, pos, quest state for every
running agent. `metrics` emits the 5 paper-claim numbers (format / argument /
tool-selection / Core 5 stages / turn efficiency) per agent — tool-selection is
DEFERRED to a Claude-as-judge sample. `tier_a` is the truth-test for whether
the latest prompt + tool changes are actually changing agent behavior —
query_quest-before-accept rate, BFS→warp adoption, gate-detection events,
drops on inventory_full, mob-level overshoot, station_locations usage, etc.
`errors` now buckets failures into stable categories (BFS_NO_PATH, STILL_MOVING,
NPC_NOT_FOUND, STATION_UNREACHABLE, COMBAT_BLOCKED_WARP, MCP_DISCONNECT,
SKILL_GATED, …) and shows the top 3 follow-up actions per category — directly
diagnoses recovery vs loop.

## Files

- `parse.py` — log parser. Handles the triple-nested JSON encoding
  (`tool_result.content` → `{"result": "..."}` → game-state JSON, with the
  observe-only `\n\nASCII_MAP:` split). Pairs assistant `tool_use` blocks with
  the matching user `tool_result` by id. Each assistant message holds exactly
  ONE block type (thinking | text | tool_use), so thinking/text are tracked
  across messages and consumed by the next tool_use.
- `analyze.py` — CLI. Built on top of `parse.py`; report logic lives here.

If you need a custom one-off analysis, import from `parse.py`:

```python
from scripts.log_analysis.parse import (
    latest_logs_per_agent, parse_session, latest_observe, tool_call_counts,
)
for agent_dir, log_path in latest_logs_per_agent():
    sv = parse_session(log_path)
    gs = latest_observe(sv)  # parsed observe game-state dict (active_quests etc.)
    ...
```
