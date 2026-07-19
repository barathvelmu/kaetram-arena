# Minimum experiment plan for Paper 1

## Experimental unit and primary endpoint

The independent unit is a complete run from a fresh, seeded world—not an individual personality agent and not a session rollover. The three prompt variants within a run share a policy, harness, launch, and analysis pipeline and are clustered observations.

Use one preregistered primary endpoint:

**Run-level Core-3 stage gain over six hours, summed over the three agents, evaluated from a fresh unseeded world.**

Report quest-wall passage as a prespecified secondary endpoint and all other tool/error/tempo metrics as mechanism analyses. Use two-sided tests unless a directional alternative is preregistered before data collection. Report every run, confidence intervals, and effect sizes; do not select the best-run envelope.

## P0 — artifact freeze and correctness

Before spending compute:

1. Pin the Kaetram-Open commit or container image.
2. Pin Python/Node dependencies, Hugging Face model and tokenizer revisions, Modal image digest, prompt hashes, tool-schema hash, and sampling configuration.
3. Fix `eval_harness.py` result saving (`max_turns` versus `duration_minutes`).
4. Make base and student receive the same model-visible schema serialization.
5. Replace name-only drift tests with normalized full-schema equality tests.
6. Make dataset provenance fail closed on explicit harness metadata.
7. Store immutable run bundles: raw logs, run manifest, DB seed, analysis output, and checksums.
8. Restore r10 and OPD inputs and demonstrate a one-command clean-clone reproduction of every existing number.

Stop if P0 is not complete. More runs on a moving or unreproducible harness will not strengthen the paper.

## P1 — replicate the weights-only result

Arms:

- Base Qwen3.5-2B, frozen harness, recovery off
- OPD round-two weights, identical harness, recovery off

Protocol:

- Six hours per run, fresh world, eval always unseeded
- Independent recorded environment and inference seeds
- Matched launch time and compute allocation across arms
- At least five runs per arm for a preliminary checkpoint; continue to a power-analysis-driven sample based on observed run-level variance before using confirmatory language

Success criterion: the round-two arm consistently improves the preregistered run-level endpoint and the Herbalist wall without a material regression on Foresting.

## P2 — isolate environment-state seeding

Train two fresh students from the same base checkpoint with every setting identical except rollout-state source:

- Natural student visitation only
- The same natural visitation mixed with the documented seeded failure states

Hold teacher, loss, optimizer, data budget, action-token budget, training seed schedule, and number of environment interactions fixed. Evaluate both from fresh unseeded worlds.

This experiment is the paper. Round one versus round two is not a clean substitute because the rounds differ in more than visitation.

## P3 — separate weights from recovery

Run a factorial evaluation:

| Weights | Recovery off | Recovery on |
|---|---:|---:|
| Base 2B | required | required |
| Round-two | required | existing 17/30 arm, replicate |
| Round-three | required | existing 18/30 arm, replicate |

Primary use: estimate stage gain and format-defect rate attributable to weights, recovery, and their interaction. Preserve raw pre-rewrite model emissions; rewritten logs cannot be the authoritative defect counter.

## P4 — test the copy-prior mechanism

Construct paired histories that differ only in whether the prior assistant tool call is canonical or malformed. For every pair:

1. Score the same canonical and malformed continuations under the teacher.
2. Record teacher log-odds, student log-odds, token position, tool, and state.
3. Repeat across multiple defect forms, states, and at least two teacher sizes or families.
4. Test whether canonicalizing only the grading context changes teacher endorsement and whether training on that signal changes student generation.

This separates copy-prior endorsement, generation propensity, and harness recovery—three quantities the current report sometimes blends.

## P5 — generalization and scaffolding

Required minimum:

- One held-out Kaetram quest not used in state seeding
- With-walkthrough versus no-walkthrough evaluation
- General-capability retention before and after OPD

Preferred:

- A second standard textual/tool-use agent environment
- A second student model family

Without held-out transfer, the work remains a single-codebase case study and should target a workshop or later venue.

## P6 — baselines

At minimum compare against:

- Off-policy SFT with the corrected identical interface
- Plain GKD/OPD under natural student visitation
- A temporal or prefix curriculum comparable to TCOD/BREAD
- Data reweighting or dead-session filtering for the r10 marginal-imbalance hypothesis

Match environment interactions and teacher-scoring budget. Report compute and dollar cost as secondary resource metrics, backed by preserved billing exports.

## Release checklist

- Anonymous code snapshot for review
- Exact environment revision and deterministic setup script
- Dataset card and model card
- Raw and processed run manifests with hashes
- Analysis environment lockfile
- One command to regenerate every table and figure
- Reproducibility checklist, ethics statement, and venue-compliant LLM-use disclosure
