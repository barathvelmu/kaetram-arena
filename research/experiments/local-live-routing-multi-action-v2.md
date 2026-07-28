# Local multi-action routing diagnostic V2

This frozen, non-confirmatory diagnostic asks one narrow question: on one
audited local Kaetram build and one fixed starting state, can the same routing
paths apply three different state-changing calls in sequence while preserving
the effects of earlier calls?

The study uses no model and no remote or metered service. It has three routing
arms, three technical repeats, and nine fresh players. Every trial has three
single-call turns: `equip_item(slot=3)`, `eat_food(slot=5)`, and
`warp(location=mudwich)`. Their order rotates across repeats, and arm order is
rotated separately. The repeats diagnose reproducibility; they are not
independent samples.

The fixture starts at 30/69 HP with a Copper Sword in slot 3 and one apple in
slot 5. Each active-route turn must issue exactly one frozen call. The recovery-
off arm must issue none. Immediate and delayed observations are collected after
every turn; a cold reconnect and an owned Mongo snapshot are collected after the
final turn. The analyzer checks cumulative effects, so a later action cannot
hide regression of an earlier one.

Passing the diagnostic would show only within-build compositional operability
for these three author-fixed calls. It would not measure model behavior,
recovery causality, quest performance, or generalization. Those claims remain
explicitly prohibited by the machine-readable registration.
