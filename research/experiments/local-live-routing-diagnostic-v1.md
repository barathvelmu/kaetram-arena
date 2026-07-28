# Local live routing diagnostic v1

Status: registered before live execution. This is a zero-cost, model-free
engineering diagnostic, not a model evaluation. The result-bearing launcher,
owned loopback service supervisor, strict verifier, and source/runtime closure
are implemented. No live result exists yet. Execution is permitted only after
a create-only prelaunch receipt seals the final clean source commit.

## Question

For one frozen, schema-valid local action, do the structured route and the
strict content-recovery route deliver the identical candidate to MCP exactly
once, while the recovery-off route delivers it zero times? If delivered, do
the two active routes satisfy the same registered immediate, delayed, and
post-reconnect state predicates?

## Frozen fixture

Each trial starts from the exact canonical player state in
`canonical_start.py`. The only candidate is:

```json
{"name":"warp","arguments":{"location":"mudwich"}}
```

The content-only representation is exactly:

```text
<tool_call><function=warp><parameter=location>mudwich</parameter></function></tool_call>
```

The registered Mudwich predicate is `180 <= x <= 200` and
`150 <= y <= 170`. Passing that predicate is only a fixture-specific state
check; it is not quest success or evidence about model quality.

## Arms and schedule

Every repeat contains all three arms:

1. `structured_direct`: frozen candidate enters through the structured path,
   passes the shared schema gate, and may be invoked once.
2. `content_recovery_on`: the identical content envelope must be promoted by
   the strict router, pass the same schema gate, and may be invoked once.
3. `content_recovery_off`: the identical envelope remains ordinary content;
   candidate invocation count must be zero.

The preregistered order rotates across three technical repeats: direct/on/off,
on/off/direct, and off/direct/on. These repeats assess operational
repeatability and are not independent samples.

## Isolation and measurement

Every scheduled trial uses one of nine unique players and a cold MCP/browser
session on the loopback-only `kaetram_e2e` lane. That database name must be
passed explicitly to every seed, snapshot, and cleanup operation. The prior
session must close and settle before any reset or seed. The complete registered
pre-state must match before the candidate is allowed to run. Diagnostic
`observe` calls are counted separately from candidate calls.

The nine username and treatment/reconnect session templates are frozen in the
registration. The create-only prelaunch seal resolves them with a unique
eight-character lowercase alphanumeric run ID, so a new run cannot silently
reuse a prior run's identities. The launcher proves that every resolved
username is absent before create-only seeding and records each database-assigned
identity. Completed trial receipts are published immediately and retained if a
later trial or cleanup is interrupted.

Each trial retains the router decision, schema verdict, candidate count, MCP
delivery status, protocol result, tool-reported error, raw result hash,
immediate observation, five-second delayed
observation, post-close/reconnect observation, and read-only database
projection. The candidate is never retried. All scheduled failures remain in
the artifact.

For an active route, application acceptance requires a returned MCP result,
`protocol_success=true`, no top-level tool-reported `error`, and a parsed warp
result containing `warping=true` and `warp_id=0`. A transport exception leaves
delivery unknown and invalidates the trial; it is never counted as one
confirmed delivery. Recovery-off must remain equal to the registered baseline
at the immediate, delayed, reconnect, and database stages.

## Release rule and claim boundary

A trial is valid when its identity, isolation, and precondition match, every
applicable stage is recorded, and delivery is not unknown. Unexpected router,
schema, protocol, application, invocation-count, or state outcomes are valid
failures, not exclusions. An active arm passes only when exactly one result is
received, the registered application predicate passes, and immediate, delayed,
and reconnect positions fall in the Mudwich region. The off arm passes only
when candidate delivery is not attempted and all four state projections remain
at baseline. A repeat passes only when all three arms pass. The full descriptive
grid is released whenever all nine trials are valid, whether the outcomes pass,
fail, or are mixed. If any trial is invalid, every trial receipt is still
released, but only the paired aggregate is withheld. No result is silently
excluded.

Even a perfect diagnostic is only a preliminary one-fixture operability check
before the broader fresh-state paired panel described in the paper. It supports
only this statement: for this build, one fixture, and one frozen action, the
active routes and the off-route control behaved as recorded. It does not
establish model quality, recovery benefit, quest improvement,
checkpoint or training superiority, faithful replay of archived V2 world
states, or generalization across tools, states, renderers, models, or
environments.

No model call, remote endpoint, Modal account, or metered service is permitted.
MongoDB, the static client, and the game server are started as owned local
processes on the registered loopback ports from frozen binaries and a cached,
digest-pinned Mongo image. Optional quest, mob, and resource enrichments are
disabled in this diagnostic lane so mutable data outside the source seal cannot
affect an observation. Source, Python, game, Mongo, and cleanup identities are
rechecked before a completed package is published. Verification establishes
internal consistency of author-attested receipts; it is not an external
timestamp or independent proof that execution occurred.
The machine-readable source of truth is
`research/experiments/local-live-routing-diagnostic-v1.json`.
