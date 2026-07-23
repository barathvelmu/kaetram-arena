# Upstream PR #54 integration audit

This fork imports only the reviewable, zero-spend parts of
`patnir411/kaetram-arena#54` (upstream commits
`0d9eb29c0a16a117d1ba524eac27ffa484602de6` and
`7e6e84a55e9b31c8b210f91ac31fd9036ce4a75e`). The import is code provenance,
not independent validation of the maintainer's July result narrative.

## Accepted and hardened

- Rick's-Roll milestone and session-note corrections, including the third
  door-crossing lane.
- Optional canonical-tool-schema rendering in the historical OPD data builder.
  It resolves the repository's frozen model-visible schema and records its
  known digest at startup; arbitrary JSON snapshots are rejected.
- Strict parsing of data-build chunk and counterfactual flags.
- Uniform-advantage and fixed-count resampling transformers. Both now validate
  JSONL structure, reject invalid numerical/sequence geometry, refuse in-place
  or accidental overwrite, write atomically, and emit byte-level source,
  output, script, and parameter receipts.
- Run-level arm statistics. Verification now takes an explicit artifact root
  and fails closed when the required raw r10 session tree is absent.
- Two diagnostic probe implementations are retained as historical utilities.
  They launch nothing and are documented for already-running local endpoints;
  their console output is not paper evidence unless captured in a separately
  registered immutable bundle.

## Deliberately excluded

- The new Modal deployment wrapper. This fork's current work is zero-spend and
  uses local endpoints; no paid-cloud deployment is needed or authorized.
- The ad-hoc tool-definition snapshot. Its normalized digest differs from the
  repository's frozen model-visible schema, so accepting it would reintroduce
  train/serve prompt drift.
- Two post-hoc paper/program-state memos whose causal interpretations are not
  backed by reviewer-accessible run bundles in this checkout. The detailed
  historical experiment ledger remains explicitly caveated and is subordinate
  to `claims-evidence-matrix.md` and `submission-readiness.md`.

## Claim boundary

The imported July arms remain historical, one-run observations until their raw
sessions, exact checkpoint/configuration receipts, database snapshots, scorer
outputs, and protocol-boundary timestamps pass the current evidence validator.
Nothing in this import upgrades E3′, E4, Arm-C, Rick's-Roll, or clean-r1 into a
causal paper result. In particular, the earlier database-lane question is still
resolved by evidence, not recollection: a maintainer states the headline runs
used the separate 9001/9011/9021 orchestrator, but publication requires the
corresponding immutable runtime receipts.

## Local verification

The curated tree was syntax-checked and exercised with the repository's pinned
unit environment. Tests cover missing-evidence failure, deterministic
transformations and hashes, invalid/unaligned advantages, malformed JSONL,
overwrite refusal, strict environment controls, and rejection of arbitrary
tool-schema snapshots. No live endpoint, Modal command, GPU, or paid service
was used during the integration.
