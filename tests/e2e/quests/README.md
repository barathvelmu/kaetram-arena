# Quest e2e tests — reachability

The quest test surface has been trimmed to a single tier: `reachability/`.
Each test is a per-step quest test, seeded with the cumulative playthrough
state an agent has at that step per `prompts/game_knowledge.md`. Covers
the Core 4 quests an agent must complete (Foresting is excluded — it's
covered by `tests/e2e/game/`). The retired tiers (`core/`, `extra/`,
`bonus/`, `skip/`) are gone.

## Layout

```
tests/e2e/quests/
├── reachability/                   ← Per-step quest reachability audit. See reachability/README.md.
│   ├── test_herbalists_steps.py    H1–H7 (Herbalist's Desperation)
│   ├── test_ricksroll_steps.py     R1–R7 (Rick's Roll)
│   ├── test_artsandcrafts_steps.py A1–A8 (Arts and Crafts)
│   ├── test_seaactivities_steps.py S1, S3–S9 (Sea Activities)
│   ├── conftest.py                  playthrough_seed_kwargs, navigate_long, debug
│   └── debug.py                     Per-test JSONL trace logger (autouse)
│
└── conftest.py       ← Shared quest assertion helpers (traverse_door, gather_until_count, ...)
```

Behavioral / fact-style game tests (item catalog, world NPC coords, navigation
regressions, stackability, etc.) live under `tests/e2e/game/`.

## Running

```bash
# Fast subset (excludes overland walks + combat grinds):
DISPLAY=:99 pytest tests/e2e/quests/reachability/ -m "reachability and not slow" -v

# Full audit:
DISPLAY=:99 pytest tests/e2e/quests/reachability/ -m reachability -v
```

See `reachability/README.md` for per-step coverage tables and the seed
accumulation map.
