# OPD training-record integrity

The OPD trainer now fails closed unless `--records-path` is paired with
`--records-manifest-path`. It verifies the receipt and every record before any
model or accelerator allocation.

The canonical `kaetram-opd-train-record-v2` contract encodes the causal shift
used by the trainer:

- position zero is context and must have label `-100`;
- at least one supervised target must remain after the one-token shift;
- ignored positions have zero advantage and behavior log-probability;
- behavior log-probabilities are finite and non-positive;
- optional `n_action` equals the post-shift supervised-token count.

This prevents a one-token record or a target placed only at position zero from
passing validation even though the trainer would silently discard its signal.

## Receipts

For a canonical corpus that has not been transformed:

```bash
python3 scripts/opd/attest_training_records.py \
  --records /checkpoints/opd_2b/round2/records.jsonl \
  --manifest /checkpoints/opd_2b/round2/records.manifest.json
```

`make_uniform_advantages.py` and `resample_records.py` continue to create their
own receipts. The trainer accepts all three receipt types and binds:

- the exact JSONL SHA-256;
- record-schema and validator identities;
- the exact attestor/transformer source identity; and
- transformation-specific parameters and record/token counts.

Copying a JSONL file without its matching receipt, editing either artifact, or
training with a newer validator against an old receipt is an error. Paths in a
receipt are informational so a byte-identical bundle can be staged at a
different mount point; the content hash is authoritative.

## Zero-spend probes

`cook_grade_probe.py` and `defect_origin_probe.py` accept loopback endpoints by
default. Each configured hostname must resolve exclusively to loopback
addresses. Remote URLs fail before any HTTP client is used.

The deliberately conspicuous `--allow-metered-remote-endpoints` escape hatch is
reserved for a separately authorized run. The zero-spend protocol never passes
it.
