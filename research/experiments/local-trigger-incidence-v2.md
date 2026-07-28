# Seeded trigger-incidence replication

Status: complete. All 1,200 registered requests succeeded; the frozen
directional criterion passed.

The first local trigger-incidence study found a 15--30 point increase in
recoverable content-only calls when the native tool schema was present.
However, all five nominal seeds within every state-condition group produced
one semantic response. That made v1 a valid deterministic interface diagnostic,
not a stochastic replication.

V2 repairs that limitation without rewriting v1. It uses the reviewed
request-local MLX sampling contract, requires a model-level seed gate before
any study outcome directory can be created, and selects 20 different source
logs from the same historical Base run. All 20 v1 source paths are explicitly
excluded. Selection is based only on registered paths, metadata, and
parseability.

The seed gate uses an unrelated plain-English prompt with no tool schema. Five
different seeds must yield at least three semantic responses, and an exact
repeat of the third seed must reproduce exactly. The gate is run and retained
separately for Base, R2, and R3. A failed gate prohibits that checkpoint's
outcome launch.

After a passed gate, the design remains the same 3 checkpoints by 4 interface
conditions by 20 states by 5 paired seeds: 1,200 local requests. The primary
outcome and three finite-grid contrasts are unchanged. The directional
replication criterion is fixed in advance: the native-tools contrast must be
strictly positive at all three checkpoints. Documentation and interaction
contrasts have no directional success criterion.

The analysis also reports semantic-response and primary-outcome heterogeneity
across seeds. It reports no p-values or confidence intervals. The states are
outcome-unseen but still come from one historical run, so this is not an
independent population sample and cannot establish training superiority,
recovery benefit, quest improvement, or broad generalization.

All three seed gates passed. Each of the 240 state--condition groups contained
five distinct semantic responses; 126 groups varied on the primary outcome.
The registered native-schema rate differences were +0.23 at Base, +0.13 at R2,
and +0.10 at R3, so the requirement that all three be strictly positive passed.
These are fixed-panel interface contrasts, not independent checkpoint
replications.

A separately labeled post-hoc diagnostic checked both response routes against
the frozen tool contract. Valid candidate counts through either route increased
from 61 to 135 (Base), 79 to 112 (R2), and 83 to 121 (R3) per 200 requests when
native tools were exposed: +0.37, +0.165, and +0.19. All 15
checkpoint-by-sample-index contrasts were positive. A strict frozen-response
router replay preserved 373 structured rows, promoted 303 unambiguous valid
content-only calls, quarantined five, and left 519 no-candidate rows untouched.
The registered primary outcome is unchanged; this diagnostic establishes a
safe parser-to-candidate transition, not successful execution or utility.

The 5 MB full-provenance public bundle contains all raw rows, gates, the
outcome-unseen design, a deterministic 1,200-row expected-request grid, runtime
and complete-snapshot projections, analysis, and code-closure hashes. Producer
and secondary verifiers require the artifact-index hash in
[`../results/local-trigger-incidence-v2/artifact-trust-root.json`](../results/local-trigger-incidence-v2/artifact-trust-root.json).
The double-blind submission instead uses a minimal review projection with a
different trust root and no source-history or model-host coordinates.

Machine-readable registration:
[`local-trigger-incidence-v2.json`](local-trigger-incidence-v2.json).

The versioned runner is
[`scripts/opd/trigger_incidence_probe_v2.py`](../../scripts/opd/trigger_incidence_probe_v2.py).
Its commands were deliberately ordered: `prepare`, then `seed-gate` for a
checkpoint, then `run` with that exact gate directory, and finally `analyze`
with all three run and gate directories. Every command refuses dirty source;
the run accepts loopback endpoints only.
