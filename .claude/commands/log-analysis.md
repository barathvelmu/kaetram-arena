---
description: Trigger when the user asks to analyze a Sonnet/Claude/DeepSeek/etc agent run, score paper metrics, find why agents got stuck, see Tier-A adoption, BFS→warp compliance, error buckets, quest progress, "what happened in the last run", "score this run on the 5 metrics", "did agents finish any Core 5 quests", or wants a per-agent summary of recent activity.
---

Analyze the latest (or specified) agent run on the GCP VM and produce a clean
per-agent + per-quest summary, including the 5 paper metrics from KAE-50 /
KAE-49 (Niral's iMessage 2026-04-28).

**Setup — SSH to the VM and locate the repo:**
```bash
ssh patnir41@34.28.111.6 "cd /home/patnir41/projects/kaetram-agent && pwd"
```

If the user asks about a specific run, scope to it via `--run <run_id>`.
Otherwise default to the latest run per agent (the `analyze.py` default).
For active/stale runs, add `--stale`.

## Step 1 — high-signal one-liner

```bash
ssh patnir41@34.28.111.6 "cd /home/patnir41/projects/kaetram-agent && \
    python3 scripts/log_analysis/analyze.py --stale status"
```

Reports run_id, agent level, HP, position, finished quests, active quests
per agent. Fastest health check — answers "is the run still alive and
where did each agent get to?".

## Step 2 — paper metrics (Niral's 5)

```bash
ssh patnir41@34.28.111.6 "cd /home/patnir41/projects/kaetram-agent && \
    python3 scripts/log_analysis/analyze.py --stale metrics"
```

Emits per-agent: format accuracy, argument accuracy, tool-selection (DEFERRED
— needs Claude-as-judge or hand-label), Core 5 stages completed, turn
efficiency. This is the headline number framework for the paper.

## Step 3 — quest detail

```bash
ssh patnir41@34.28.111.6 "cd /home/patnir41/projects/kaetram-agent && \
    python3 scripts/log_analysis/analyze.py --stale quests"
```

Quest start/end snapshots, accept_quest_offer counts, NPC interaction breakdown.

## Step 4 — Tier-A signal adoption

```bash
ssh patnir41@34.28.111.6 "cd /home/patnir41/projects/kaetram-agent && \
    python3 scripts/log_analysis/analyze.py --stale tier_a"
```

Shows adoption of `query_quest`-before-accept (Rule 10), `live_gate_status`
detection, BFS→warp fallback (Rule 4a, target ≥80%), gather-with-gate-explained,
inv_full + drop@full, mob-level overshoot (Rule 11 violations), station_locations
usage, deaths. Truth-test for whether prompt+tool changes are actually
changing behavior.

## Step 5 — error buckets and recovery

```bash
ssh patnir41@34.28.111.6 "cd /home/patnir41/projects/kaetram-agent && \
    python3 scripts/log_analysis/analyze.py --stale errors"
```

Buckets failures (BFS_NO_PATH, NPC_NOT_FOUND, STILL_MOVING, MOB_NOT_FOUND,
COMBAT_BLOCKED_WARP, MCP_DISCONNECT, SKILL_GATED, ...) and shows the top 3
follow-up actions per bucket. Directly diagnoses recovery vs loop patterns.

## Step 6 — single-agent deep dive (if user asks "why did agent N get stuck")

```bash
ssh patnir41@34.28.111.6 "cd /home/patnir41/projects/kaetram-agent && \
    python3 scripts/log_analysis/analyze.py --stale agent <N> -n 25"
```

Full session-level breakdown for one agent: meta, stats delta, inventory,
deaths, recent tool calls.

## Step 7 — recent reasoning (when behavior is the question, not stats)

```bash
ssh patnir41@34.28.111.6 "cd /home/patnir41/projects/kaetram-agent && \
    python3 scripts/log_analysis/analyze.py --stale thinking -n 5"
```

Last N reasoning blocks per agent — read what the model was *trying* before
each action.

## Step 8 — synthesize for the user

After running the relevant subset of the above (don't run all 7 if not needed —
pick by question type), write a tight summary:

- **Run scope:** which run, how long, total cost, did it finish or get terminated mid-flight
- **Per-agent:** final level, deaths, finished Core 5 quests, top error
- **Top error pattern:** which bucket dominated and what agents did after
- **Tier-A adoption:** any rule fire and get ignored? quote a reasoning block
- **Recommendation:** what to change before the next run (game patch, prompt rule, tool surfacing)

If the user asks about a specific quest (Q1-Q5), grep for the quest name and
NPC across the run's session logs:
```bash
ssh patnir41@34.28.111.6 "cd /home/patnir41/projects/kaetram-agent && \
    grep -li -E 'rick|ricksroll|shrimp|cookedshrimp|seaweedroll' \
        dataset/raw/agent_*/runs/<run_id>/session_*.log"
```

## Filters reference

| Flag | Purpose |
|------|---------|
| `--stale` | include agents whose log is >10 min old (default: only currently-running) |
| `--run <id>` | scope to specific historical run (e.g. `--run run_20260427_172418`) |
| `--all-runs` | for `runs` cmd, list every run instead of recent N |
| `--claude` / `--opencode` | force a parser (default: auto-detect from meta.json) |

## Source

- `scripts/log_analysis/analyze.py` — CLI dispatcher (subcommands: status, runs,
  quests, tools, recent, errors, thinking, agent, full, tier_a, **metrics**, timeline)
- `scripts/log_analysis/parse.py` — log parser (handles triple-nested
  tool_result JSON + observe-only `\n\nASCII_MAP:` split)
- `scripts/log_analysis/README.md` — programmatic API docs
- Niral's metrics proposal: iMessage 2026-04-28 (format / argument / tool-selection
  / stage completion / turn efficiency)

## Output expectations

- Always include the run_id in the summary so the user knows what was analyzed.
- For per-agent metrics, name the personality (`grinder` / `completionist` /
  `explorer_tinkerer`).
- Quote real reasoning blocks when answering "why did agent X do Y?" — the
  agent's own self-narration usually contains the answer.
- If turn-efficiency shows `(asst)`, note the session was truncated mid-flight
  (no result event); `(result)` means clean termination.
