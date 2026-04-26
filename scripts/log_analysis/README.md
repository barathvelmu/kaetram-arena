# scripts/log_analysis

Comprehensive log analyzer for the Claude harness. Parses session JSONL logs
under `dataset/raw/agent_*/logs/` and reports per-agent status, quest
progression, tool-call distribution, errors, recent activity, and reasoning.

By default it operates only on **currently running** agents (logs touched in
the last 10 minutes). Pass `--stale` to include older sessions.

## Usage

```bash
python3 scripts/log_analysis/analyze.py             # full report (default)
python3 scripts/log_analysis/analyze.py status      # one-line per agent
python3 scripts/log_analysis/analyze.py quests      # quest progression detail
python3 scripts/log_analysis/analyze.py tools       # tool call counts + error rates
python3 scripts/log_analysis/analyze.py recent -n 8 # last N tool calls per agent
python3 scripts/log_analysis/analyze.py errors      # all tool errors, grouped
python3 scripts/log_analysis/analyze.py thinking -n 3   # last N reasoning blocks
python3 scripts/log_analysis/analyze.py agent 1 -n 10   # deep-dive single agent
```

`status` is the fastest signal — level / HP / pos / quest state for every
running agent in one table.

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
