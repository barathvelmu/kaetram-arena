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

## Compute gates still open

- The launcher must freeze a six-hour Core-3 protocol, not the 30-minute Desert
  Quest example.
- The primary metric, factorial estimands, and difference-in-differences
  interactions must be immutable manifest fields.
- Endpoint labels must be replaced by digest-attested checkpoint, tokenizer,
  adapter, render-manifest, and serving identity.
- #41 sealing must run automatically before a cell starts; manual post-run
  sealing is insufficient.
- Five paired replicates cannot be called confirmatory eligible. The smallest
  two-sided exact sign-flip p-value is 0.0625 before multiplicity correction.
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
- No open PR had a reported GitHub status check at audit time.
