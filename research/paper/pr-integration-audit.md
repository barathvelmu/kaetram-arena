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
8. merge #48 matched-training launcher after #40/#41 contracts stabilize
9. review and merge stacked #49 preparation adapter after #48; preserve its
   explicit `prepared_not_trained` boundary
10. merge structurally independent #39, #43, and #46 when reviewed
11. merge #35 paper/audit after its cited code contracts stabilize

#42 already contains #40. #44 and #45 each contain #42/#40, so their diffs
should narrow after prerequisites merge.

## Manual-union conflicts

- #36 and #42/#44/#45 all edit `eval_harness.py` result metadata. Preserve the
  time-budget save fix together with held-out/protocol metadata.
- #37 and #42/#44/#45 all edit `eval_harness.py` preflight output. Preserve the
  Mongo database lane together with knowledge/held-out state.
- Choosing either side wholesale drops a required safety property.

## Protocol gates implemented prospectively

- #42 now freezes the six-hour canonical-start Core-3 protocol with no
  intermediate-state evaluation initialization and registered gameplay-RNG seeds, seven
  estimands, familywise alpha, an assumption-driven 20-replicate power contract,
  checkpoint/tokenizer/render/deployment attestations, and a create-only
  prelaunch ledger. It also seals each completed cell's resolved prompt, exact
  pre-rewrite emissions, parsed transition transcript, state-boundary snapshots,
  results, and an exact requested/completed-cell inventory.
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
- #47 records paired per-replicate inference/environment seeds and verifies the
  game startup attestation. Kaetram-Open draft PR #333 implements the game-side
  seeded RNG, but it must be reviewed, merged, deployed, and live-attested.
- #45 now executes a hash-pinned isolated MCP/Mongo reachability checker and binds visitation and
  teacher-advantage estimates to immutable trial artifacts with minimum-count
  and Wilson-bound gates. The checker verifies exact game/harness revisions,
  every action boundary, and a separately canonicalized target player. It
  remains fail-closed because no live certificate exists and schema-v1 candidate
  data must be regenerated.
- The current seeder restores persistent player state rather than complete
  shared-world state; experimental and paper labels must preserve that boundary.
- #48 freezes six primary and four separate mechanism/baseline training arms
  across five shared seeds (50 core cells) and four separately reported
  state--history conditions (20 more cells).
- Stacked #49 adds a hash-pinned preparation adapter for all 14 registered arm
  and history conditions. It verifies source hashes, held-out exclusions,
  arm-specific evidence, budgets, and frozen interfaces, then emits create-only
  normalized records with `prepared_not_trained`/`not_run` status. It does not
  supply verified arm bundles, Guided sampling, the corrected-interface SFT
  adapter, the SCoRe objective, or accelerator execution.

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
- Complete-cell evidence sealing and post-run revalidation suite on #42: 47
  passed; every sealed artifact and requested/completed cell is re-hashed.
- #44 merged the new #42 contract and refuses analysis until the sealed
  completion inventory and every underlying artifact revalidate: 56 focused
  tests passed.
- #47 composed with #42's newest bundle contract and the deterministic game
  handshake: 218 passed, 31 expected skips.
- #45 merged #42's newest bundle/revalidation contract; reachability, selector,
  held-out, factorial, logging, and manifest focused suite: 77 passed.
- #48 corrected matched-training protocol: 17 focused passed; full prospective unit suite
  151 passed with 31 expected dependency/artifact skips.
- Stacked #49 preparation adapter: 22 focused passed; broader unit suite 153
  passed with 31 expected dependency skips. Optional MCP/PyMongo and live
  services remain unavailable for collection/e2e tests.
- No open PR had a reported GitHub status check at audit time.
