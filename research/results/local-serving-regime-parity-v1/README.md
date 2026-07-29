# Matched thinking-mode parity study

This directory contains the preregistered, zero-cost local comparison that
resolves the serving-regime ambiguity in the V2 interface study. The first
three V2 states were seen during debugging and are excluded. The confirmatory
panel pairs 17 remaining states × four interfaces × five effective seeds at
each of three fixed checkpoints: 340 paired cells per checkpoint and 1,020 new
thinking-disabled requests.

## Result

The registered criterion passed. Body-only parser-candidate incidence fell
strictly at every checkpoint when thinking was disabled:

| Checkpoint | Thinking on: structured / body-only / none | Thinking off: structured / body-only / none | Body-only change (off − on) | Structured change (off − on) |
|---|---:|---:|---:|---:|
| Base | 103 / 96 / 141 | 303 / 0 / 37 | −28.2 pp | +58.8 pp |
| OPD round 2 | 107 / 75 / 158 | 307 / 0 / 33 | −22.1 pp | +58.8 pp |
| OPD round 3 | 91 / 98 / 151 | 295 / 0 / 45 | −28.8 pp | +60.0 pp |

Across the 1,020 matched cells, all 269 body-only candidates in the retained
thinking-on arm disappeared in the thinking-off arm: 248 became structured
calls and 21 became no-candidate responses. Thinking off produced 905
structured calls, 115 no-candidate responses, and zero body-only candidates.
These pooled counts are presentation only; the registered verdict requires the
strictly negative body-only difference separately at all three checkpoints.

The result measures a matched finite-grid contrast under the registered
thinking-mode render intervention on separately recreated pinned local MLX
environments. The arms match checkpoint, tokenizer, chat-template file,
sampling contract, request payload, tool schema, and state snapshot identities.
They have different source commits and runtime receipts, so this is not claimed
as byte-identical single-factor isolation. It does not isolate provider effects,
validate candidate appropriateness, establish execution, or measure gameplay
utility.

## Files

- `runs/<checkpoint>/results.jsonl` contains all 340 new raw response rows.
- Each run's `artifact-index.json` hashes its prelaunch receipt, raw rows, and
  postflight receipt. All three runs completed without retries or failures and
  retained stable endpoint identities.
- `analysis/analysis-summary.json` verifies both new and retained arms,
  reclassifies every raw response, checks all 1,020 pair bindings, and reports
  checkpoint, schema-stratified, transition, and pooled descriptive counts.
- `analysis/artifact-index.json` hashes the analysis summary.
- `bundle-index.json` binds the three run indexes and the analysis index.

## Exact replay

The checked-in analysis records source commit
`51420b9b0df49ec7389b9f6728c0ef08923ee111`. From a clean checkout of that
commit, rerun:

```bash
python3 -m scripts.opd.serving_regime_parity_probe analyze \
  --registration research/experiments/local-serving-regime-parity-v1.json \
  --run-dir research/results/local-serving-regime-parity-v1/runs/base_2b \
  --run-dir research/results/local-serving-regime-parity-v1/runs/opd_r2_2b \
  --run-dir research/results/local-serving-regime-parity-v1/runs/opd_r3_2b \
  --out-dir /tmp/kaetram-parity-analysis
```

The generated `analysis-summary.json` and `artifact-index.json` must match this
directory byte for byte. The analysis also independently verifies the prior V2
artifact against its published index hash before using any retained
thinking-enabled row.
