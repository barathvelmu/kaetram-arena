# OPD 2B weights × recovery factorial runbook

This protocol compares three frozen 2B checkpoints (`base`, `r2`, `r3`) across
the recovery affordance off/on. The manifest expands every replicate into the
complete 3 × 2 factorial and pairs recovery off/on within each
`replicate × weights` block.

**Prerequisite:** merge/deploy PR #40's canonical-schema support before any
live run. The confirmatory launcher requires
`evaluation.tool_schema_source="canonical"` and exports
`KAETRAM_TOOL_SCHEMA_SOURCE=canonical` to every cell; it will not run the
historical live-schema render.

The checked-in example is
[`opd-2b-factorial.example.json`](opd-2b-factorial.example.json). It is safe by
default: `execution.allow_launch` is `false`, and the launcher defaults to
preflight without resolving endpoint variables, touching MongoDB, starting a
game server, or creating run directories.

The schema-v2 manifest also separates three randomness controls that must not
be conflated:

- `schedule_seed` deterministically orders launch batches; it does not seed a
  game or model.
- `inference_seeds` registers one base sampling seed per replicate. All arms in
  that replicate share it, and `play_qwen.py` derives a distinct 31-bit request
  seed from `sha256-session-turn-v1:<base>:<session>:<turn>` before sending the
  OpenAI-compatible `seed` field. The base, r2, and r3 endpoints validate that
  field and pass it to SGLang generation.
- `environment_seed` describes the game-world RNG mechanism. It is currently
  and truthfully registered as `unavailable`.

## Preflight and launch interlock

Set these variables in the operator environment; do not put endpoint URLs in
the manifest:

```bash
export KAETRAM_QWEN_2B_BASE_ENDPOINT=...
export KAETRAM_QWEN_2B_R2_ENDPOINT=...
export KAETRAM_QWEN_2B_R3_ENDPOINT=...
```

Validate the full plan without launching anything:

```bash
python3 scripts/opd/factorial_eval.py \
  research/experiments/opd-2b-factorial.example.json \
  --dry-run
```

Live execution requires three independent operator actions: use a reviewed
manifest copy with `execution.allow_launch=true`, pass `--execute`, and pass
`--confirm-launch` with the exact experiment ID. This is intentionally not a
copy-paste command: launching starts game processes and consumes inference
compute. The launcher also refuses missing endpoint variables or any existing
cell run directory.

There is presently a fourth, intentional fail-closed interlock: confirmatory
launch is blocked because Kaetram-Open routes combat, drops, movement, resource,
and other gameplay randomness through unseeded `Math.random()`. A schedule seed
is not an environment seed, and model sampling determinism does not repair this
game-side limitation. Dry-run/preflight remains available for review.

The defensible unblock is an upstream Kaetram change that routes every
gameplay-random draw through one seeded per-server PRNG, initializes it from an
explicit server configuration value, and exposes enough startup/health
attestation to verify that the configured seed was accepted. After that exists,
this manifest/launcher contract must be reviewed and extended for that real
mechanism. Merely exporting an environment variable that Kaetram does not read
is not acceptable.

Endpoint URLs remain environment-indirected through the launcher,
`eval_harness.py`, and `play_qwen.py`. Process arguments, preflight plans,
session init records, and result metadata contain only `env:VARIABLE_NAME`, not
the URL.

The preflight plan and `results.json` record `tool_schema_source`, the registered
inference seed, schedule algorithm/seed/index, batch, cluster and pair IDs, and
the environment-seed mechanism. The launcher validates all of those fields
before accepting a cell artifact. Verify the canonical schema and provenance
before analysis.

## Isolation and pairing

Every cell has a unique username, server port, sandbox, output directory, and
cell ID. Preflight rejects an incomplete/duplicate grid, missing recovery mate,
duplicate isolation value, unexpected weight label, invalid port range, or
unlocked held-out registration. Recovery is set explicitly per child process:
the off cell removes `KAETRAM_TOOL_RECOVERY`; the on cell sets it to `1`.
`execution.max_parallel` is a hard launch cap; the checked-in design uses six.
Schema v2 requires exactly six: each randomized batch is one complete
`replicate × weight` cluster. SHA-256 ranking randomizes whole cluster order,
then personality-pair order within a cluster, while keeping each recovery
off/on pair adjacent. This preserves both recovery pairing and the registered
cluster analysis regardless of schedule seed.

The confirmatory unit is the independent `replicate`, not an individual agent
episode. Each replicate contains the three fixed historical personality lanes
(`grinder`, `completionist`, `explorer_tinkerer`) under all six weights ×
recovery arms. Each lane runs exactly one DB-reset episode; the launcher rejects
`episodes != 1`, so additional independent observations must be added through
`design.replicates`. Aggregate the three personality strata within each
`replicate × arm`, then make paired arm comparisons across the five replicate
clusters. Do not report the 90 lane cells as `n=90`; the confirmatory sample is
`n=5` independent replicate clusters per arm. `pair_id` pairs recovery off/on
within a replicate, weight, and personality; `cluster_id` groups all three
personality lanes for a replicate and weight.

## Held-out no-walkthrough condition

The registered task is `Desert Quest`; its locked record is
[`heldout-quest.json`](heldout-quest.json). The provenance claim is scoped to
the repository-resident OPD seed definitions and teacher-grading artifacts
checked on 2026-07-18. It does not attest to untracked or external data.

For this condition:

- `eval_harness.py` omits `prompts/game_knowledge.md` and removes the two static
  Desert Quest utility hints still present in `prompts/system.md`.
- The evaluator targets the quest by name but supplies no route, NPC, item, or
  stage solution.
- At the MCP boundary, `query_quest("Desert Quest")` retains only observable
  accepted/stage/finished state and live gate status. Walkthrough, advisory,
  NPC, item, recipe, boss, reward, and station fields are redacted. Other
  quests and normal runs are unchanged.
- OPD seed scripts fail before mutation if they include the reserved quest.
  The 2B teacher-grading builder fails before endpoint calls if an input
  trajectory touches a registered quest/NPC alias.
- Success is completion of this exact quest, with DB-authoritative stage and
  completion deltas recorded in `results.json`.

To run the frozen Core-3 protocol instead, set `omit_game_knowledge=false` and
set both `held_out_quest` and `held_out_registration` to empty strings. The same
weights × recovery and isolation validation still applies.

## Operational caveats

- The example requests five independent replicates × six arms × three fixed
  personality lanes × one 30-minute episode (90 cell-episodes total); review
  the resulting time and endpoint cost before enabling it.
- Ports must be free, MongoDB must be reachable, and the Kaetram server tree
  expected by `eval_harness.py` must exist on the execution host.
- The launcher runs bounded batches of at most `execution.max_parallel` cells.
  A child-launch exception terminates already-started batch siblings, but
  created run directories remain as an audit trail and must not be reused.
- This change adds infrastructure only; no endpoint was deployed and no live
  evaluation was run while preparing it.
