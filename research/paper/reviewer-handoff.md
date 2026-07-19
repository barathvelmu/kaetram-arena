# Paper 1 reviewer handoff

Updated: July 18, 2026

## The short version

Paper 1 is an audited technical report with a promising central observation, not a finished conference submission. The strongest current result is that round-two visitation-corrected OPD weights pass the prior Herbalist wall in all three agents of one unseeded run, improving the run-level Core-3 score from 12/30 to 15/30 without the format-recovery affordance. The run is not an independent replication, and the three agents inside it are clustered prompt variants rather than three independent trials.

Do not launch expensive confirmatory experiments until the correctness PRs below are merged and the P0 launch checklist is complete.

## Pull-request map and review order

Review and merge in this order:

1. [PR #36 — save time-budgeted evaluation results](https://github.com/patnir411/kaetram-arena/pull/36)
   - Fixes a deterministic post-episode `KeyError` that prevented `results.json` from being saved.
   - Does not change experiment semantics.
2. [PR #37 — align evaluation resets with the eval database](https://github.com/patnir411/kaetram-arena/pull/37)
   - Makes resets and DB-authoritative metrics use the same `kaetram_eval` database as both eval game servers.
   - Fails closed if a player reset cannot be confirmed.
3. [PR #38 — fail closed on incomplete paired evaluations](https://github.com/patnir411/kaetram-arena/pull/38)
   - Preserves real child exit codes, validates both result artifacts, and moves `dataset/eval/latest` only after both arms are complete.
4. [PR #35 — paper audit and research plan](https://github.com/patnir411/kaetram-arena/pull/35) (this PR)
   - Revises the technical report, audits claims and literature, and records the minimum experiment package.

All four PRs are independently reviewable against `main`. PRs #36–#38 intentionally separate result serialization, database semantics, and wrapper completion behavior.

## Linear execution board

Project: [Paper 1 — Reproducible OPD Submission](https://linear.app/niral/project/paper-1-reproducible-opd-submission-74a055f466fd)

Existing historical/context tickets linked into the project:

- [KAE-32 — Paper 1 draft](https://linear.app/niral/issue/KAE-32/paper-1-write-arxiv-draft)
- [KAE-74 — r11/OPD direction](https://linear.app/niral/issue/KAE-74/r11-plan-scaffold-on-policy-distillation-approach-post-narrative-open)
- [KAE-49 — design-variable audit](https://linear.app/niral/issue/KAE-49/catalog-and-defend-every-design-variable-for-the-paper)

Current execution tickets:

- P0: [KAE-76](https://linear.app/niral/issue/KAE-76/paper-p0-review-and-merge-eval-correctness-prs-36-38), [KAE-77](https://linear.app/niral/issue/KAE-77/paper-p0-immutable-run-manifests-and-clean-clone-reproduction), [KAE-78](https://linear.app/niral/issue/KAE-78/paper-p0-version-and-enforce-the-model-visible-tool-render-contract)
- Experiments: [KAE-79](https://linear.app/niral/issue/KAE-79/paper-p1-replicate-the-2b-weights-recovery-factorial) through [KAE-85](https://linear.app/niral/issue/KAE-85/paper-p7-implement-one-strong-matched-budget-alternative)

KAE-79 through KAE-85 are blocked on their relevant P0 tickets so the board does not encourage spending compute on an unfrozen protocol.

## What is currently supported

- Round-two OPD provides a clean within-repository observation of a weights-only improvement: base 12/30 to r2 15/30, with prior Herbalist stage-one passage changing from 0/3 to 3/3 in one unseeded run.
- Round-three reaches 18/30 only with a model-interface recovery affordance. It must be labeled weights plus recovery, not a pure weight result.
- Round-two weights plus recovery reaches 17/30 in one ablation run. This is suggestive, not a replicated factorial estimate.
- The malformed-history copy-prior observation is a plausible mechanism: a teacher can locally prefer continuation of malformed syntax already present in context. The current sample is too small for a general causal claim.
- The historical r10 base/SFT comparison is exploratory evidence of regression, not a clean causal SFT baseline.

## What is not currently supported

- A general claim that visitation-corrected OPD outperforms ordinary on-policy distillation.
- A general claim that OPD outperforms matched off-policy SFT or outcome RL.
- A clean base-versus-SFT causal comparison: training and serving do not share an identical model-visible tool-schema render.
- Independent statistical replication of the 2B OPD result.
- Generalization beyond seeded Core-3 quest procedures or beyond Kaetram.
- Continual learning, autonomous skill learning, world-model learning, or embodied-agent claims.

## P0 launch gate

Before spending additional training or evaluation compute:

- Merge PRs #36–#38 and run their focused tests in a clean clone.
- Pin the Kaetram-Arena and Kaetram-Open commits, environment image, dependencies, model/tokenizer revisions, prompts, decode settings, and database lane.
- Freeze a normalized full tool-schema snapshot and hash.
- Introduce an explicit, versioned render contract shared by new training and serving code.
- Preserve historical r10 and OPD render behavior under explicit legacy labels; do not silently change old endpoints.
- Make every run write an immutable manifest with checkpoint, prompt, schema, harness, environment, sampling, seed, recovery, and artifact hashes.
- Restore or explicitly mark unavailable the raw inputs behind every reported historical number.

The historical r10 checkpoint cannot be repaired by merely passing native tools at serving time. That would create a new train/serve mismatch. Interface parity requires a newly rendered dataset and newly trained checkpoint.

## Minimum publishable experiment package

### WP1 — Frozen-harness weights × recovery factorial

Evaluate base 2B, r2, and r3 weights with recovery off and on under identical fresh-world, prompt, schema, duration, sampling, and hardware conditions. Use independent complete runs as the statistical unit. Preserve raw model emissions before any recovery rewrite.

### WP2 — Natural visitation versus state-seeded OPD

Train two fresh students from the same checkpoint. Hold teacher, optimizer, scored-token budget, environment interactions, recovery setting, and training seeds fixed. Change only natural student visitation versus the documented natural-plus-seeded state mixture. This is the central causal test.

### WP3 — Corrected same-family SFT baseline

Collect successful same-family 4B trajectories and train a fresh 2B student with the same native schema render used at evaluation. Match the OPD action-token or compute budget. The historical Claude-to-9B r10 result is not a substitute.

### WP4 — Recovery-mechanism controls

Compare no recovery, dirty-history retry, canonical-history rewrite plus retry, and current recovery plus execution. Add grammar-constrained decoding if practical. Measure syntax validity, semantic tool accuracy, relapse, quest stages, and calls per hour.

### WP5 — Privileged-context teacher ablation

From matched Rick's Roll states, compare the plain 4B teacher, 4B with a verified successful trajectory in grading context, and a stronger same-family teacher if available. Measure teacher action preference and downstream pole, shrimp, cook, door, and completion milestones.

### WP6 — Held-out transfer

Pre-register at least one quest never used for state seeding or grading. Evaluate with and without walkthrough knowledge. Add a compact general-capability retention suite before and after training.

### WP7 — One strong alternative baseline

Implement either an agent-specific divergence/reliability baseline or matched-interaction outcome RL. A representative implemented baseline is more useful than citing many unimplemented 2026 methods.

## First iteration after merging the correctness PRs

1. Create one immutable smoke-run manifest for base 2B with recovery off.
2. Run a short paired smoke evaluation and verify that both arms save, reset the correct database, fail closed, and preserve raw emissions.
3. Review the artifact bundle before scaling duration or run count.
4. Run the preliminary replicated factorial with at least five independent runs per arm.
5. Use observed run-level variance for a power analysis before the confirmatory run count is locked.

`scripts/run-eval.sh` currently targets the historical base/r10-SFT paired lane. Do not treat it as the 2B factorial launcher without adding explicit 2B endpoint, checkpoint, schema, and recovery configuration to the manifest. Existing OPD operational notes live in `dataset/opd_2b/ROUND1_RUNBOOK.md`; they are historical provenance, not a clean confirmatory protocol.

## Review checklist for the collaborator

- Confirm each correctness PR reproduces its stated failure on `main` and fixes only that failure.
- Decide whether missing historical raw artifacts can be recovered from the original machines, Modal volumes, or backups.
- Approve the explicit render-contract design before any new SFT or OPD dataset is built.
- Choose the held-out quest and freeze it before new training data is generated.
- Approve the primary endpoint and independent run definition before replicated evaluation.
- Agree on a compute ceiling for the preliminary factorial and matched training baselines.
- Keep the technical report language conservative until those results land.

## Paper rewrite gate

The conference manuscript starts only after WP1–WP3 and WP6 have complete immutable artifacts. WP4, WP5, and WP7 determine whether the submission can make a mechanism/method claim or should remain a carefully scoped case study. The final manuscript must fit the chosen venue template and include a truthful disclosure of material LLM assistance.
