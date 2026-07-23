# Historical evidence audits

These derived reports contain no raw session text. They are intended to be
regenerated against an external, immutable artifact recovery.

## Initial player state

`historical-initial-state.json` checks the first model-visible action in every
headline R10 and OPD agent-run. A bundle passes only when:

1. its first recorded tool call is a successful `observe`; and
2. the returned persistent state exactly matches the canonical level-1,
   post-tutorial benchmark start.

The report covers 36 agent-runs and is bound to the external file inventory by
the SHA-256 digest recorded in `source_manifest`.

Regenerate it from the repository root:

```bash
python3 scripts/audit_historical_initial_state.py \
  --raw-root /path/to/recovery/dataset/raw \
  --source-manifest /path/to/recovery/SHA256SUMS \
  --out research/audits/historical-initial-state.json
```

This verifies the state visible to the model before any action. It does not
retroactively attest which database command produced the state, the game
server revision, or shared-world state outside the player snapshot.
