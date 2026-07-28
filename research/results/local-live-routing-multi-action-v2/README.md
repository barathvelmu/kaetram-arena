# Local multi-action routing diagnostic V2 result

Status: complete with measurement failures. The package verifier passes, all
9/9 technical trials are protocol-valid, and 0/9 pass every prospectively
frozen predicate. The registered verdict is therefore
`complete_with_failures`; it must not be shortened to “9/9 passed.”

The six active-route trials delivered all 18 scheduled calls without a schema,
delivery, protocol, or tool error. The structured arm routed 9/9 calls directly
and the recovery-on arm promoted 9/9 content envelopes. In both active arms,
the registered `eat_food` and `warp` predicates pass in 3/3 technical trials;
the registered `equip_item` predicate passes in 0/3. The recovery-off arm made
no dispatch attempt in any of its nine turns and its candidate ledgers are
empty, but all three trials fail the registered exact-baseline predicate.

## What the raw evidence revealed

A post-outcome audit found two defects in the measurement projection, not in
the transport protocol:

1. The Copper Sword disappears from inventory and appears in equipment in all
   36 relevant active-route measurements. Thirty client/reconnect projections
   encode it as `player/weapon/coppersword`, while six database projections use
   `coppersword`. The frozen predicate accepts the plain key (or a key beginning
   with `copper sword`) but not the namespaced client key. This explains the
   registered 0/3 equipment score in each active arm; it does not authorize a
   post-hoc relabel.
2. In the recovery-off arm, the first immediate observation in each trial is
   exactly the fixture. The other 21/24 semantic measurements differ only
   because passive regeneration changes HP from 30 to 31. Position, inventory,
   and equipment remain at the no-action fixture, and the dispatch ledger stays
   empty. Exact whole-state equality was therefore too strict for the intended
   no-action check.

These findings justify a separately registered prospective correction that
canonicalizes equipment identity and checks absence of the three action-
specific effects instead of equality on regenerating state. V2 remains sealed
and negative under its original predicates.

## Evidence boundary

This was a zero-model, loopback-only diagnostic on one build, one fixture, and
nine dependent technical trials. Even a corrected follow-up can establish only
within-build compositional operability for the three author-fixed calls. It
cannot establish model quality, recovery causality, quest improvement, or
generalization.

The complete private result is retained outside the repository. Its exact
manifest, registration, and analysis hashes—and the anonymous outcome-preserving
projection—are recorded in
[`public-summary.json`](public-summary.json). The projection omits usernames,
session and trial identifiers, process identities, database object identifiers,
absolute paths, raw database text, and service names.
