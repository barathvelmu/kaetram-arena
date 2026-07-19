# Reachability-targeted external-state initialization

This protocol tests a narrower claim than generic intermediate-state OPD.
TCOD-B2F already reaches intermediate states by replaying successful teacher
prefixes, while Guided-OPD mixes teacher and student turns. The proposed arm
instead writes a verified persistent world snapshot directly and selects it
because the natural student rarely reaches it while the teacher has a measured
conditional success advantage there.

## Frozen candidate record

Each JSONL candidate must record:

- a unique `state_id`, full `snapshot`, `progress_bin`, and `source_kind`;
- the source run IDs used to discover or validate it;
- legal-reachability, internal-consistency, and e2e-seed verification flags,
each backed by an artifact path and SHA-256 digest;
- repeated counts for natural student visitation, teacher success, student
  success from the state, and recoverability;
- whether the state is task-relevant and whether it already completes the
  endpoint.

Rates alone are rejected because their denominators cannot be audited. A
snapshot must explicitly cover every field accepted by the database seeder, so
omitted values cannot silently inherit defaults. Duplicate snapshots under
different IDs, unsupported database arguments, unverifiable validity claims,
missing or digest-mismatched evidence, and any held-out-quest leakage are hard
errors. The held-out guard scans the entire candidate record, including source
run IDs and evidence paths, not only the snapshot. Relative evidence paths are
resolved beside the candidate JSONL.

## Selection and controls

```bash
python3 scripts/opd/select_target_states.py artifacts/state-candidates.jsonl \
  --config research/experiments/targeted-state-selection.example.json \
  --out artifacts/target-state-selection.json
```

The output freezes five equal-size arms:

1. combined targeted rule;
2. random valid states;
3. progress-matched valid states;
4. visitation-deficit only;
5. teacher-advantage only.

The combined rule requires a valid, unfinished, task-relevant state; low natural
student visitation; minimum teacher success; minimum teacher-minus-student
conditional success; and minimum recoverability. The random seed, input hashes,
snapshot hashes, source kinds, and selection metrics are preserved.

Preflight one three-agent collection batch without touching MongoDB:

```bash
python3 scripts/opd/seed_selected_states.py \
  artifacts/target-state-selection.json --arm targeted --batch 0
```

Live seeding additionally requires `--execute` and an exact
`--confirm EXPERIMENT_ID:ARM` interlock. Seeded states are training-only. Every
live assignment first deletes that username from every player collection before
writing the complete snapshot, preventing stale ability or schema-version rows
from surviving an upsert. Seeded states are training-only. Every headline
evaluation begins end-to-end from the original unseeded world.

## Falsification boundary

The method claim fails if random or progress-matched states tie the targeted
arm, if a successful-prefix curriculum ties it, if the teacher is unreliable
at selected states, or if gains appear only in seeded evaluation. Report the
probability of reaching the bottleneck, crossing it conditional on arrival,
and completing downstream after crossing.
