# Pull-request integration audit — July 19, 2026

## Current verdict

Do not start confirmatory compute from any individual open PR. GitHub reports the
branches as mergeable one at a time, but the scientific protocol does not yet
compose across the stack.

## Required merge and rebase order

1. #36 result saving
2. #37 database-lane alignment
3. #38 paired-evaluation completion checks
4. #40 model-visible render contract
5. rebase and resolve #42 factorial launcher
6. functionally integrate #41 immutable manifests and #47 randomness contract
7. rebase #44 factorial analysis and #45 targeted-state curriculum independently
8. merge structurally independent #39, #43, and #46 when reviewed
9. merge #35 paper/audit after its cited code contracts stabilize

#42 already contains #40. #44 and #45 each contain #42/#40, so their diffs
should narrow after prerequisites merge.

## Manual-union conflicts

- #36 and #42/#44/#45 all edit `eval_harness.py` result metadata. Preserve the
  time-budget save fix together with held-out/protocol metadata.
- #37 and #42/#44/#45 all edit `eval_harness.py` preflight output. Preserve the
  Mongo database lane together with knowledge/held-out state.
- Choosing either side wholesale drops a required safety property.

## Protocol gates implemented prospectively

- #42 now freezes the six-hour canonical-unseeded Core-3 protocol, seven
  estimands, familywise alpha, an assumption-driven 20-replicate power contract,
  checkpoint/tokenizer/render/deployment attestations, and a create-only
  prelaunch ledger.
- #44 now verifies the hashed protocol, computes factorial marginal effects and
  difference-in-differences interactions, and cannot promote a five-run pilot
  to confirmatory status.
- The resulting weights-by-recovery plan is 20 replicate clusters, 360 six-hour
  cells, or 2,160 cell-hours. Capacity and cost require explicit operator review.

These are draft implementations pending maintainer review and ordered merge;
they do not make the historical results reproducible.

## Compute gates still open

- Replace all unresolved example checkpoint/tokenizer/render/deployment hashes
  with real immutable attestations from restored endpoints.
- Set and verify the exact clean execution commit.
- Restore Mongo, game services, and model endpoints, then pass the live health
  attestation before any cell starts.
- #47 records schedule and inference seeds, but game-side environment RNG must
  be seeded and attested before launch.
- Raw pre-rewrite emissions and every requested/completed cell must be sealed
  into the run bundle.

## Historical database-bug scope

The April 25 database split affects the dedicated `run-eval.sh` lane. Headline
r10 and June OPD paths used the separate database-aligned orchestrator and are
not implicated by this specific mismatch. Missing raw bundles still prevent an
independent replay of those headline values.

## Audit checks performed

- #42/#44 focused suite: 44 passed.
- #36: 31 passed.
- #37: 30 passed.
- #38: 10 passed.
- #43: 5 passed.
- Real #42 serializer into #44 analyzer on a synthetic 18-cell factorial:
  passed mechanically, but exposed the estimand and provenance gaps above.
- Hardened #42 unit suite: 195 passed, 31 expected skips.
- Hardened composed #42/#44 unit suite: 204 passed, 31 expected skips.
- No open PR had a reported GitHub status check at audit time.
