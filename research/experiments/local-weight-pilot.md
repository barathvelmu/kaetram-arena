# Zero-cost matched-weights pilot

`local-weight-pilot.json` preregisters a nine-cell feasibility pilot:
three public weight snapshots crossed with three paired inference/environment
seeds, one five-minute episode per cell, one completionist prompt identity, and
no recovery intervention. It is not the six-hour confirmatory factorial.

The pilot answers an operational question before longer runs consume local
time: after enforcing one tokenizer and one render contract, do the 2B arms
produce enough valid structured actions to justify a larger experiment? The
registered primary diagnostics are action throughput, zero-turn episodes,
tool-parse rate, API errors, and budget overrun. Quest, XP, and movement values
are exploratory. No pilot outcome can establish model superiority or be pooled
into the confirmatory estimate.

Dry-run validation has no model, game, database, or output side effects:

```bash
python scripts/opd/local_weight_pilot.py
```

Launch requires the exact pilot ID plus explicit local runtimes and artifact
roots:

```bash
python scripts/opd/local_weight_pilot.py \
  --launch \
  --confirm local-render-parity-pilot-v1 \
  --output-root /path/to/new/pilot-output \
  --snapshots-root /path/to/kaetram-model-snapshots \
  --game-dir /path/to/clean/Kaetram \
  --mlx-python /path/to/mlx-venv/bin/python \
  --node-binary /path/to/node20
```

The launcher refuses dirty Arena or game checkouts, an already-existing output
root, a game build that does not attest the clean game commit, occupied
loopback endpoint ports, a non-Node-20 runtime, endpoint identity drift, or
cross-arm tokenizer/render mismatches. It preflights all endpoints and seals
`prelaunch.json` before the first outcome. Each cell retains its endpoint
receipt, endpoint/evaluation logs, canonical-start and environment-RNG
receipts, raw session evidence, result file, and validity status. Failed cells
remain in `completed-inventory.json`; there are no outcome-based exclusions.

The nominal model budget is 45 minutes. Local model loading, game startup, and
in-flight generation can make wall time longer. The launcher uses no Modal,
cloud GPU, or paid endpoint.
