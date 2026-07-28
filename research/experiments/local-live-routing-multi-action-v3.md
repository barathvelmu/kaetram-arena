# Multi-action V3 measurement amendment

V3 is a prospective measurement amendment to the unchanged V2 execution
lane. It does not edit, overwrite, or retrospectively rescore the V2 result.
The V2 package remains reproducible at source commit
`65b3bead4ccb59953c0860a5530c6c42199128db` and retains its exact verdict:
9/9 protocol-valid technical trials, `complete_with_failures`.

The amendment fixes two measurement mismatches discovered only after V2 was
run. First, Kaetram's client observation names the equipped sword
`player/weapon/coppersword`, while Mongo names the same frozen item
`coppersword`. V3 freezes those two strings—and no inferred alternatives—as
equivalent. Second, the no-recovery arm is no longer required to retain exact
HP equality. Kaetram passively regenerates HP, so HP-only drift is not evidence
of a registered action. Instead, the no-recovery predicate requires zero
candidate dispatches and absence of the registered equip, eat, and warp effect
signatures at every immediate, delayed, reconnect, and database observation.

All execution details, actions, arm schedule, fixture, receipt schemas, local
services, and zero-cost constraints remain V2-exact. A future run is eligible
only when its clean V2 prelaunch Git head already contains byte-identical V3
registration, measurement code, and tests. The July 28 V2 run is explicitly
ineligible. No V3 live run has been performed.

After a future V2-format result package passes its unchanged verifier, create
the V3 score as a sibling artifact rather than adding a file to the immutable
V2 package:

```bash
python scripts/opd/live_routing_multi_action_measurement_v3.py analyze \
  --result-root /local/future-run/result \
  --analysis-artifact /local/future-run/analysis-v3.json \
  --registration research/experiments/local-live-routing-multi-action-v3.json \
  --parent-registration research/experiments/local-live-routing-multi-action-v2.json \
  --repo-root "$PWD"

python scripts/opd/live_routing_multi_action_measurement_v3.py verify \
  --result-root /local/future-run/result \
  --analysis-artifact /local/future-run/analysis-v3.json \
  --registration research/experiments/local-live-routing-multi-action-v3.json \
  --parent-registration research/experiments/local-live-routing-multi-action-v2.json \
  --repo-root "$PWD"
```

Both modes first reverify the entire V2 package and the prospective Git gate.
The output binds the parent manifest, registration, eligible source commit, and
recomputed V3 analysis; verification requires canonical byte equality.

This remains a small within-build operability diagnostic. Technical repeats
are not independent, V3 and V2 must be reported separately, and neither can
support claims about model quality, causal benefit, training superiority,
quest performance, or generalization.
