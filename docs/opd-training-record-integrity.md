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
create-only receipts. The trainer accepts only those three receipt types and
binds:

- the exact JSONL SHA-256;
- record-schema and validator identities;
- the exact attestor/transformer source identity; and
- transformation-specific parameters and record/token counts; and
- the recursively embedded parent receipt for every derived corpus.

Before reading a source log, the base corpus builder requires every declared run
to resolve to at least one log and snapshots every log's bytes. Parse failures,
empty sessions, or a run with no usable action state abort the build. The same
inventory is re-hashed before sealing, so the receipt cannot bind end-of-build
bytes different from those consumed.

Both scoring endpoints must expose complete `/health` identity attestations.
Their deployment and checkpoint identities must match the requested artifacts,
their tokenizer hashes must match each other and the local immutable
`tokenizer.json`, and their attestations are checked again after scoring. The
receipt also hashes every local renderer/parser/guard/schema source file that
materially participates in the build, the held-out bytes, and all material
parameters.

The base builder refuses to resume into any pre-existing output: a partial
build must be retained separately and a fresh sealed build started. Root
receipt emission exists only inside that exclusive fresh-build path; there is
no callable post-hoc builder attestor.

Each transformer requires the source's adjacent receipt, validates its complete
chain before reading records, and embeds that parent plus its canonical digest
in the new receipt. This prevents a uniform or resampled corpus from being
accepted after its builder/model/source/parameter ancestry is removed. Copying
a JSONL file without its matching receipt, editing any link, or training with a
newer validator against an old receipt is an error. Paths are informational so
a byte-identical bundle can be staged at a different mount point; content
hashes are authoritative.

Historical v1/v2 receipts and post-hoc identity receipts are intentionally not
accepted. A prospective trainable corpus must be freshly built under this
chain.

## Zero-spend probes

`cook_grade_probe.py` and `defect_origin_probe.py` accept loopback endpoints by
default. Each configured hostname must resolve exclusively to loopback
addresses. Remote URLs fail before any HTTP client is used.

The deliberately conspicuous `--allow-metered-remote-endpoints` escape hatch is
reserved for a separately authorized run. The zero-spend protocol never passes
it.
