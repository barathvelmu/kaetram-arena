# Adversarial reviewer simulation — July 18, 2026

## Current verdict

| Reviewer | Score | Confidence | Decision |
|---|---:|---:|---|
| Methods and reproducibility | 2/10 | 5/5 | Strong reject |
| Agent-learning novelty | 4/10 | 4/5 | Reject; promising idea |
| Empirical design and statistics | 1/10 | 5/5 | Strong reject |
| Overall today | 2/10 | — | Do not submit |
| Potential after required work | 7/10 | — | Competitive case study/method paper |

## Fatal findings

1. Round one versus round two is not a causal state-seeding experiment. Initialization, collected data, policy history, data volume, and training differ.
2. There is one full run per OPD arm. The three prompt variants are clustered observations, not independent replications.
3. Historical render contracts and immutable raw bundles are incomplete. Prospective PRs do not retroactively repair old checkpoints.
4. The 18/30 configuration combines weights and recovery. One 17-versus-18 contrast does not identify main or interaction effects.
5. TCOD substantially preempts broad intermediate-state OPD novelty.
6. The copy-prior probe is post-hoc, small, single-family, and uses a length-sensitive score.
7. No held-out quest, no-walkthrough transfer, retention suite, or serious matched baseline has results.
8. Historical r10 raw inputs are missing, its analysis script fails, and the model-visible render contract differs.
9. The old report’s capacity and development-envelope claims mix durations and selected runs.
10. PRs #39–#42 add infrastructure and launchers, not scientific evidence.

## Required experiments

- Matched natural OPD versus targeted failure-state OPD from the same checkpoint.
- Matched TCOD-style success-prefix curriculum.
- Corrected-interface SFT baseline.
- Independent environment and inference seeds; run-level analysis and power calculation.
- Base/round-two/round-three weights crossed with recovery off/on.
- Held-out quest, with/without walkthrough, and general-capability retention.
- Paired copy-prior probes across defects, states, and multiple teachers.
- Clean-clone regeneration from immutable artifacts.

## Reviewer questions the paper must answer

- What is randomized, and what is held fixed?
- Is the gain from state coverage, successful replay, extra data, or teacher grading?
- How is the selected state different from Backplay or TCOD?
- Is the state reachable and internally consistent?
- Does the intervention improve states not adjacent to the seed?
- Does it transfer to a quest never used for seeding or grading?
- How do weights and recovery interact?
- What is the minimum detectable run-level effect?
- Are canonical and malformed sequence scores comparable under different tokenizations?
- Can every plotted point be regenerated from an immutable anonymous bundle?

## Manuscript consequence

The 15-page report remains a historical record. The submission draft is organized around the research question, evidence grades, and confirmatory design. It must not be rewritten into success language until the experiments change the evidence status.
