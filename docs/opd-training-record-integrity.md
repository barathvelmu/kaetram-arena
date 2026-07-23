# OPD training-record integrity

The OPD trainer now fails closed unless `--records-path` is paired with
`--records-manifest-path`. Inside the scheduled training job, it verifies the
receipt and every record before model loading or training begins.

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

`opd_2b_data.py`, `make_uniform_advantages.py`, and `resample_records.py` emit
their own create-only receipts. The trainer accepts only those three receipt
types and binds:

- the exact JSONL SHA-256;
- record-schema and validator identities;
- the exact attestor/transformer source identity; and
- transformation-specific parameters and record/token counts.

The base corpus builder also binds the complete source-log inventory, immutable
student and teacher artifact hashes, held-out bytes, and all material build
parameters. It refuses to resume into any pre-existing output: a partial build
must be retained separately and a fresh sealed build started. This prevents a
new builder receipt from being attached post hoc to records produced by an
unknown earlier process.

There is deliberately no generic post-hoc attestor. Such a tool could relabel a
uniform or resampled corpus as “untransformed” and erase its source/parameter
chain. Copying a JSONL file without its matching builder/transformer receipt,
editing either artifact, or training with a newer validator against an old
receipt is an error. Paths in a receipt are informational so a byte-identical
bundle can be staged at a different mount point; the content hash is
authoritative.

## Zero-spend probes

`cook_grade_probe.py` and `defect_origin_probe.py` accept loopback endpoints by
default. Each configured hostname must resolve exclusively to loopback
addresses. Remote URLs fail before any HTTP client is used.

The deliberately conspicuous `--allow-metered-remote-endpoints` escape hatch is
reserved for a separately authorized run. The zero-spend protocol never passes
it.
