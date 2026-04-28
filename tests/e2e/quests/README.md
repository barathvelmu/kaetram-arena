# Quest e2e tests — tier layout

The 5-quest benchmark is the scored objective. The directory layout reflects
that directly: `core/` comes first and maps 1-to-1 to the Core 5 in
`prompts/game_knowledge.md`.

## Layout

```
tests/e2e/quests/
├── core/             ← Scored benchmark. 5 files / 26 tests. The agent's whole job.
│   ├── test_01_foresting.py              (5 tests) Gather 10+10 oak logs → Iron Axe
│   ├── test_02_herbalists_desperation.py (5 tests) Forage blue lily + paprika + tomato (Foraging 25 gate)
│   ├── test_03_ricksroll.py              (4 tests) Fish/cook 5 shrimp → deliver seaweedroll through quest door
│   ├── test_04_artsandcrafts.py          (4 tests) Beryl pendant → bowlsmall → stew (3 stations)
│   └── test_05_seaactivities.py          (8 tests) 7-stage courier chain + picklemob kill
│
├── extra/            ← Bonus progression. 5 files / 15 tests. Run after Core 5 is green.
│   ├── test_01_desertquest.py            (3 tests) Unlocks crullfield + lakesworld warps
│   ├── test_02_royaldrama.py             (4 tests) 10k gold via sewer door
│   ├── test_03_royalpet.py               (5 tests) Deliver 3 books (prereq: Royal Drama)
│   ├── test_04_scientistspotion.py       (1 test)  1-stage Alchemy unlock
│   └── test_05_ancientlands.py           (2 tests) Capstone — needs icesword from Ice Knight (L62)
│
├── bonus/            ← Completable filler. 3 files / 17 tests. Only meaningful if all 10 above are green.
│   ├── test_anvilsechoes.py              (2 tests)  Bronze boots (flavor text lies)
│   ├── test_scavenger.py                 (5 tests)  7500 gold (fake shopping list in dialogue)
│   └── test_clamchowder.py               (10 tests) 7500 gold (Fishing 10 + Cooking 15 + Fletching 3)
│
├── skip/             ← Upstream-broken. 3 files / 10 tests. Kept for regression detection — expected to fail.
│   ├── test_sorcery.py                   (3 tests) Reward item `staff` doesn't exist in item registry
│   ├── test_minersquest.py               (3 tests) Circular nisocrock gate (need item to mine the item)
│   └── test_minersquest2.py              (4 tests) Depends on minersquest (impossible to start)
│
├── reachability/     ← Per-step "can a vanilla player reach this?" audit. 4 files / 27 tests.
│   │                   See `reachability/README.md` for full per-step probe details
│   │                   (H/R/A/S markers, navigate_long, JSONL trace anatomy, debug flags).
│   ├── test_herbalists_steps.py    (6 tests) H1–H6
│   ├── test_ricksroll_steps.py     (6 tests) R1–R6
│   ├── test_artsandcrafts_steps.py (7 tests) A1, A3–A8 (A2 subsumed into A1)
│   ├── test_seaactivities_steps.py (8 tests) S1, S3–S8 + S7'
│   ├── conftest.py                  navigate_long, vanilla seed, debug fixtures
│   └── debug.py                     Per-test JSONL trace logger (autouse)
│
├── conftest.py       ← Shared quest assertion helpers (traverse_door, gather_until_count, ...)
└── test_llm_agent_plays_quest.py  ← LLM-driven harness test (see "LLM harness" below)
```

**Tier totals (verified against `find tests/e2e/quests/ -name 'test_*.py'`):**
core 26 / extra 15 / bonus 17 / skip 10 / reachability 27 = **95 quest tests** + 1 parametrized LLM harness file.

## Why the `skip/` tier exists — DO NOT spend time fixing these

These three quests have **upstream Kaetram-Open bugs** in the game source. The
tests stay in-tree as regression detectors so we notice if upstream fixes them,
but they are **expected to fail** today:

| Quest | Bug | Source |
|---|---|---|
| `sorcery` | Final reward item `staff` does not exist in the item registry — quest finishes server-side but reward grant fails. | Kaetram-Open item registry vs `quests/sorcery.json` |
| `minersquest` | Circular gate: starting the quest requires presenting a `nisocrock`, but mining `nisocrock` rocks requires a pickaxe gated behind starting the quest. | `quests/minersquest.json` step 0 prerequisites |
| `minersquest2` | Hard-depends on `minersquest` being finished — unreachable while `minersquest` is unstartable. | `quests/minersquest2.json` `prerequisites` field |

See `Kaetram-Open/QUEST_CITATIONS.md` for the source-of-truth runtime status.
**If you find yourself debugging one of these, stop** — the fix is upstream in
Kaetram-Open's quest JSON, not in our test harness.

## LLM harness — `test_llm_agent_plays_quest.py`

This is **not** a deterministic stage test. It runs an actual LLM through the
MCP harness phase-by-phase via `tests/e2e/helpers/quest_phases.py`, asserting
each phase's success criterion. Phases come from the verified-working catalogue
(broken phases are auto-`xfail`ed).

**Supported model backends** (resolved by `tests/e2e/helpers/llm_endpoint.py`):
1. Explicit `LLM_ENDPOINT` + `LLM_MODEL` env vars (any OpenAI-compatible base).
2. **Modal** — finetuned Qwen3.5-9B at `kaetram-qwen-serve` (when `MODAL_TOKEN_ID` is set).
3. **Ollama** — local Qwen3.5-4B at `127.0.0.1:11434` (model `qwen3.5:4b-16k`).
4. None reachable → tests skip cleanly with a reason.

**No Sonnet / Claude support yet.** The endpoint resolver only routes Qwen via
Modal or Ollama. Wiring Sonnet would mean adding a third provider branch in
`llm_endpoint.py` and a Claude-flavored adapter in `agent_runner.py`.

Determinism: `temperature=0`, `seed=42` by default.

## Running

All commands assume the test-lane Xvfb display (`DISPLAY=:99`) so Playwright can
launch a real browser; CI and the VM both export this. Drop the prefix only if
you have a real display attached.

```bash
# Core 5 only — the scored benchmark:
./scripts/bench-core.sh
# or: DISPLAY=:99 pytest tests/e2e/quests/core/ -v

# Core + Extra (10 quests):
./scripts/bench-full.sh
# or: DISPLAY=:99 pytest tests/e2e/quests/core/ tests/e2e/quests/extra/ -v

# Every completable quest (13 = core + extra + bonus):
DISPLAY=:99 pytest tests/e2e/quests/core/ tests/e2e/quests/extra/ tests/e2e/quests/bonus/ -v

# Regression check on the upstream-broken quests (expected to fail):
DISPLAY=:99 pytest tests/e2e/quests/skip/ -v

# Reachability audit — see reachability/README.md for full details:
DISPLAY=:99 pytest tests/e2e/quests/reachability/ -m "reachability and not slow" -v   # fast subset
DISPLAY=:99 pytest tests/e2e/quests/reachability/ -m reachability -v                   # full (~22 min)

# LLM harness — needs Modal token OR a running Ollama; otherwise skips:
DISPLAY=:99 pytest tests/e2e/quests/test_llm_agent_plays_quest.py -v
```

## Integration tier — PLANNED, not yet built

A `core/integration/` directory is referenced by `reachability/README.md` as the
middle rung between stage tests (everything pre-seeded) and reachability tests
(minimal seed, per-step). **It does not exist on disk today.**

When/how to build it:

- **Trigger:** Once Core 5 stage tests are stably green AND reachability has
  flagged its open issues, integration fills the gap by playing each quest
  end-to-end from a vanilla seed.
- **Seed contract:** Use `vanilla_seed_kwargs()` from `bench/seed.py` — Mudwich
  spawn + tutorial starter kit only. **No item / XP / gear injection during the
  test run.** The whole point is to prove the quest is completable without
  scaffolding once it has been started.
- **Assertions per quest:** `quest.isFinished == True` AND the documented
  reward (gold / item) is present in the player's inventory or bank after
  turn-in. No per-stage assertions — those belong in stage tests.
- **Layout:** `tests/e2e/quests/core/integration/test_0{1..5}_*.py`, mirroring
  the stage filenames so `pytest -k foresting` picks both up.

Once built, update the reachability README's tier table accordingly — it
currently still flags Integration as "(planned)".

## Why this layout

The agent prompt (`prompts/system.md` + `prompts/game_knowledge.md`) is
organized around Core / Extra / SKIP tiers. The test tree mirrors that exactly,
so the scored objective is the first thing a reader sees, and the broken
quests are quarantined instead of interleaved with the real benchmark.

See `prompts/game_knowledge.md` for the canonical tier definitions and
`../../../Kaetram-Open/QUEST_CITATIONS.md` for the source-of-truth runtime
status of every quest on the current upstream tree.
