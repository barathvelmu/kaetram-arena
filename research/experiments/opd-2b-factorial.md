# Frozen 2B weights × recovery Core-3 confirmatory protocol

This launcher preregisters a six-hour, canonical-unseeded Core-3 comparison of
three frozen 2B checkpoints (`base`, `r2`, `r3`) with recovery off/on. It is
infrastructure and a protocol, not a result. No live evaluation was run while
preparing it.

The reviewed input is
[`opd-2b-factorial.example.json`](opd-2b-factorial.example.json). It expands 20
independent fresh-world replicate clusters into the complete 3 weights × 2
recovery × 3 fixed personality-lane design: 360 six-hour cell-episodes. Each
lane runs exactly once after its own DB reset. The launcher rejects any other
duration, a seeded world, extra episodes, missing arm, different personality
set, noncanonical tool schema, or incomplete preregistration.

## Registered outcome and estimands

The primary metric is `core3_stages_advanced`. Within each
`replicate × weights × recovery` arm, sum the DB-authoritative stage deltas from
the grinder, completionist, and explorer/tinkerer lanes. The replicate-arm
outcome is therefore bounded 0–30. A lane is not an independent observation.

The seven ordered primary estimands are frozen in the manifest:

1. r2 − base with recovery off;
2. r3 − base with recovery off;
3. recovery on − off for base;
4. recovery on − off for r2;
5. recovery on − off for r3;
6. the r2-versus-base recovery interaction; and
7. the r3-versus-base recovery interaction.

Familywise alpha is 0.05 across these seven contrasts. The prospective
assumption-driven power record is
[`opd-2b-factorial-power-v1.json`](opd-2b-factorial-power-v1.json). It freezes
20 replicate clusters for 80% target power under a minimum relevant paired
difference of 3 stages and paired-difference SD of at most 3. Those assumptions
are not presented as an empirical variance estimate. Do not reduce the sample
after looking at outcomes.

## Mandatory immutable inputs

PR #40's canonical model-visible schema/render contract and PR #41's immutable
provenance machinery are prerequisites. Before any cell starts, the launcher
now validates and seals all of the following in a create-only
`prelaunch.json`:

- exact clean Git commit and experiment-manifest SHA-256;
- every prompt/personality file digest and the full canonical tool-schema
  digest;
- the power-analysis artifact digest;
- one PR-#41 checkpoint provenance sidecar per weight;
- one endpoint attestation per weight containing immutable deployment ID,
  checkpoint SHA-256, tokenizer SHA-256, and render-contract SHA-256; and
- held-out quest name, registration path, and registration digest (empty for
  this Core-3 protocol, retained explicitly so it cannot silently disappear).

Environment variables still keep endpoint URLs out of commands and artifacts,
but their names are not accepted as model identity. At launch the code queries
each endpoint's `/health` response and requires its `attestation` object to
match the reviewed file exactly. The checked-in provenance files are visibly
marked `unresolved_example`, so the example can be inspected and dry-run but
cannot launch.

A deployable endpoint must return this shape:

```json
{
  "status": "ok",
  "attestation": {
    "deployment_id": "immutable-deployment-id",
    "api_model": "2b-opd-r2",
    "checkpoint_sha256": "<64 lowercase hex>",
    "tokenizer_sha256": "<64 lowercase hex>",
    "render_contract_sha256": "<64 lowercase hex>"
  }
}
```

Replace every example checkpoint/endpoint sidecar with real hash-verified
records, set `protocol.source_git_commit` to the exact clean commit, and only
then review a copy with `execution.allow_launch=true`.

## Safe preflight and launch interlock

Endpoint URLs belong only in the operator environment:

```bash
export KAETRAM_QWEN_2B_BASE_ENDPOINT=...
export KAETRAM_QWEN_2B_R2_ENDPOINT=...
export KAETRAM_QWEN_2B_R3_ENDPOINT=...
```

Dry-run performs no endpoint call, MongoDB access, process launch, or run-dir
creation:

```bash
python3 scripts/opd/factorial_eval.py \
  research/experiments/opd-2b-factorial.example.json \
  --dry-run
```

Live execution requires all three independent operator actions: a reviewed
manifest with `execution.allow_launch=true`, `--execute`, and
`--confirm-launch` exactly equal to its experiment ID. The launcher refuses
missing endpoints, unresolved or mismatched attestations, dirty/wrong Git,
drifted prompt/power/provenance files, any existing cell directory, or an
existing prelaunch ledger.

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

Every cell result must report the same protocol ID, 21,600-second budget,
manifest digest, endpoint-attestation digest, checkpoint digest, tokenizer
digest, render-contract digest, held-out metadata, canonical schema source, and
one successful episode. Missing or misattributed artifacts fail the batch.

## Cost and current blockers

The frozen plan is 2,160 cell-hours. With six simultaneous cells the nominal
lower bound is 360 elapsed hours, before startup and retry overhead; endpoint
capacity and dollar cost must be reviewed before enablement. Real checkpoint
and tokenizer hashes, deployed `/health` attestations, restored endpoints,
MongoDB/game-server infrastructure, and the exact clean execution commit are
still required. The checked-in example intentionally proves that none of these
can be guessed or bypassed.
