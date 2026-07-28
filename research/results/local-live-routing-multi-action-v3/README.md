# Prospective multi-action V3 result

Status: complete. The fresh post-amendment package and its separate V3 analysis
both pass their fail-closed verifiers. All 9/9 technical trials are
protocol-valid and pass every prospectively frozen V3 predicate.

The structured-direct and content-recovery-on arms each pass the registered
`equip_item`, `eat_food`, and `warp` predicates in 3/3 technical repeats. The
content-recovery-off arm makes no candidate dispatch and shows none of the
three registered action effects in 3/3 repeats. Immediate, delayed, cold-
reconnect, and database projections are included in the private verification
closure.

This result does not repair or relabel V2. The earlier study remains
`complete_with_failures`: 9/9 protocol-valid but 0/9 full-predicate-pass under
its original measurement. V3 was registered and pushed before the fresh run,
then applied only to that new evidence.

## Evidence boundary

These are dependent technical repeats for three author-fixed calls on one
fixture and one local build, with zero model calls. The result supports a
narrow within-build compositional-operability claim. It does not establish
model quality, action appropriateness, recovery causality, quest utility,
checkpoint or training superiority, statistical independence, or
generalization.

[`public-summary.json`](public-summary.json) is generated from the retained
private package, not transcribed by hand. Its builder re-verifies the unchanged
V2-format package, the prospective V3 Git gate, and the canonical V3 analysis;
then it releases only aggregate counts, exact evidence hashes, and claim
guards. The summary is identity-scrubbed, self-hashed, and verified by canonical
byte equality.

With `PYTHON` set to the pinned Python 3.12 interpreter:

```bash
PYTHON=/path/to/pinned-python3.12
PRIVATE_RUN=/path/to/fresh-multi-action-v3

"$PYTHON" scripts/opd/live_routing_multi_action_public_summary_v3.py verify \
  --result-root "$PRIVATE_RUN/result" \
  --analysis-artifact "$PRIVATE_RUN/analysis-v3.json" \
  --v3-registration research/experiments/local-live-routing-multi-action-v3.json \
  --parent-registration research/experiments/local-live-routing-multi-action-v2.json \
  --repo-root "$PWD" \
  --summary research/results/local-live-routing-multi-action-v3/public-summary.json
```
