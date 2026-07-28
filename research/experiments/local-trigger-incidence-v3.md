# Trigger-incidence V3: different retained state pool

V3 is a prospectively frozen, zero-cost extension of V2. It changes only the
20 retained decision states. The three checkpoints, four interface conditions,
five paired request seeds, renderer hashes, sampling contract, outcomes, and
finite-grid analysis are inherited byte-for-byte from the registered V2
protocol.

The source is the completionist lane of historical run
`run_20260613_112422`, an evaluation rollout of the already-frozen
`2b-opd-r3` checkpoint. It is not the V2 Base-source run
`run_20260608_185339`. The archive contains 1,154 matching session logs, 370
completionist logs, and 367 reconstructable fourth-decision states. The
registration binds path-plus-SHA closures over the full matching logs, all
matching session metadata, and the eligible completionist logs, plus the
archive inventory, archive checksum manifest, and all three run/harness
identities.

This is a different retained state pool, not a new independent run, model
family, renderer, or environment. The rollout was generated after the frozen
R3 checkpoint and is not known to have been training input. It remains part of
the same project and historical evaluation regime, so V3 cannot establish
broad generalization or independence from all development choices.

## Two-stage freeze

Execution is currently prohibited. The preparation command deliberately fails
unless the registration and preparation code are in a clean commit that equals
the branch's pushed upstream:

```bash
python scripts/opd/prepare_trigger_incidence_v3.py \
  --historical-root /path/to/historical-runs \
  --output-dir research/experiments/local-trigger-incidence-v3-design
```

Preparation deterministically materializes the V2-compatible effective
registration, selects 20 states without inspecting any V3 generation outcome,
and writes exact design and provenance receipts. It still records
`execution_authorized=false`.

Commit and push the complete design package before any model request. Then run:

```bash
python scripts/opd/verify_trigger_incidence_v3.py \
  --historical-root /path/to/historical-runs \
  --design-dir research/experiments/local-trigger-incidence-v3-design \
  --require-execution-ready
```

The final flag fails closed unless the registration and all four design files
are byte-identical to a clean pushed `HEAD`, the registration commit is an
ancestor, the source archive still matches every registered digest, the design
rederives exactly, and overlap with V2 is zero. Only a report with
`execution_ready=true` permits a later request run. No endpoint, model, or
network service is started by either command.

## Bound local execution

Use `trigger_incidence_probe_v3.py`, not the V2 runner directly, after the
second-stage gate passes. The adapter preserves the design's preparation
commit while binding each seed gate and outcome run to the later clean,
pushed design `HEAD`. It revalidates the archive, registration, design,
expected 1,200-request grid, loopback endpoint attestation, and passed V2 seed
gate before delegating requests and analysis to the unchanged V2 code.

Verify the runtime binding without starting a model or service:

```bash
python scripts/opd/trigger_incidence_probe_v3.py verify \
  --historical-root /path/to/historical-runs
```

For each of `base_2b`, `opd_r2_2b`, and `opd_r3_2b`, start only the matching
local endpoint, then run its seed gate and 400-request outcome grid into local
storage outside the repository:

```bash
python scripts/opd/trigger_incidence_probe_v3.py seed-gate \
  --historical-root /path/to/historical-runs \
  --endpoint http://127.0.0.1:PORT/v1 \
  --snapshot base_2b \
  --out-dir /local/output/v3/base-seed-gate

python scripts/opd/trigger_incidence_probe_v3.py run \
  --historical-root /path/to/historical-runs \
  --endpoint http://127.0.0.1:PORT/v1 \
  --snapshot base_2b \
  --seed-gate-dir /local/output/v3/base-seed-gate \
  --out-dir /local/output/v3/base-run
```

The endpoint policy rejects non-loopback URLs. Run only one 2B checkpoint at a
time. After all three checkpoint directories exist, `analyze` requires all
three run directories and all three matching seed-gate directories. The
adapter retains the frozen V2 finite-grid math and adds the V3 runtime binding
to the analysis provenance.

Before using any V3 number, run the offline result verifier with the same
three `--run-dir` and `--seed-gate-dir` arguments plus `--analysis-dir`:

```bash
python scripts/opd/verify_trigger_incidence_result_v3.py \
  --historical-root /path/to/historical-runs \
  --run-dir /local/output/v3/base-run \
  --run-dir /local/output/v3/r2-run \
  --run-dir /local/output/v3/r3-run \
  --seed-gate-dir /local/output/v3/base-seed-gate \
  --seed-gate-dir /local/output/v3/r2-seed-gate \
  --seed-gate-dir /local/output/v3/r3-seed-gate \
  --analysis-dir /local/output/v3/analysis
```

It verifies all hashes and bindings, independently reclassifies the raw model
responses, and recomputes cells, contrasts, seed heterogeneity, and the
directional criterion. The strict V2 public exporter is not V3-compatible;
do not present a V2 export as a V3 artifact. Save the verifier's JSON output as
an immutable receipt, then pass it to
`scripts/opd/export_trigger_incidence_artifact_v3.py` together with the three
run directories, three seed-gate directories, analysis directory, anonymous
runtime marker, and local endpoint verification record. The exporter refuses
partial checkpoint membership and publishes only after
`audit_trigger_incidence_artifact_v3.py` independently accepts the staged
bundle. The public auditor recomputes the 1,200-request analysis from raw
responses, enforces both path and rendered-message separation from V2, and
checks the extended snapshot tree, snapshot lock, runtime environment,
renderer, sampling, tokenizer revision, and deterministic deployment identity.

The receipt must be produced in a clean checkout at the execution commit. The
later exporter may run from a hardened code commit because it validates the
saved receipt and every embedded execution binding rather than rewriting the
historical provenance.

## Claim boundary

If executed later, V3 can test whether V2's positive native-schema direction
persists on this finite different state pool. It cannot show recovery benefit,
action appropriateness, execution utility, checkpoint superiority, quest
improvement, or generalization beyond the frozen lineages and retained panels.
