# Paper presentation audit

Date: 2026-07-28

## Why this audit exists

The evidence package was already unusually complete, but the manuscript
presented most of that evidence as prose and exact tables. This audit compares
the paper's visual argument with strong tool-use and multimodal-agent papers.
It is a presentation audit, not a claim that figure count predicts acceptance.

## Reference set

| Paper | Venue / status | Visual inventory | Useful pattern |
|---|---|---:|---|
| Subliminal Learning | reference preprint supplied by the author | 3 figures, 5 tables / 7 pages | dense multi-panel results; little decorative space |
| Mantis | TMLR Outstanding + Featured | 3 figures, 9 tables | conceptual example early; exact tables carry much of the evidence |
| BFCL | ICML 2025 | 9 figures, 3 tables | taxonomy makes the evaluation space legible before detailed results |
| $\tau$-bench | ICLR 2025 | 7 figures, 4 tables | setup and trajectory example appear early |
| ToolSandbox | NeurIPS 2024 | 9 figures, 9 tables | detailed stateful trajectory diagram localizes failure boundaries |
| WAREX | TMLR | 7 figures, 5 tables | architecture schematic precedes mechanism results |

Primary sources:

- TMLR paper and award criteria: <https://jmlr.org/tmlr/papers/>
- Mantis: <https://arxiv.org/abs/2405.01483>
- BFCL: <https://proceedings.mlr.press/v267/patil25a.html>
- $\tau$-bench: <https://arxiv.org/abs/2406.12045>
- ToolSandbox: <https://machinelearning.apple.com/research/toolsandbox-stateful-conversational-llm-benchmark>
- WAREX: <https://openreview.net/pdf?id=o4pXVP8RCD>

Counts are a manual inventory of numbered figures and tables in the cited
versions, used only to calibrate information density.

## Changes adopted

1. Put the mechanism and evidence sequence on page 2. The new figure makes the
   structured-field versus ordinary-content failure understandable before the
   reader reaches the taxonomy.
2. Plot the main result. The two retained panels, all three checkpoints, and
   the sign of all 20 paired state effects are visible in one evidence-bound
   figure. The post-hoc route-validity view is visibly marked post-hoc.
3. Preserve exactness without making it the entry point. Generated V2 and V3
   tables moved to the appendix; they remain byte-derived from sealed analyses.
4. Make the failed measurement part of the contribution. A third figure shows
   the frozen V2 failure, the two localized defects, and the fresh prospective
   V3 correction without relabelling the old result.
5. Bind every displayed number to released JSON. `figure-data.tex` is generated
   by `scripts/opd/render_tmlr_figures.py`; both paper builds fail if it is stale.
6. State where recovered calls sit, not only which channel they reached. A
   post-outcome localization found that all 701 recovered calls on both panels
   lie inside the model's reasoning span, so the figure and caption now say so
   and the normative recommendation is inverted. The generator fails closed if
   that ever stops being true.

## Guardrails

- Do not add plots merely to match another paper's count.
- Do not turn the two retained panels into independent replications.
- Do not add uncertainty intervals that imply an unsampled deployment
  population.
- Do not visually merge registered and post-hoc outcomes.
- Keep exact tables and claim boundaries available to reviewers.
