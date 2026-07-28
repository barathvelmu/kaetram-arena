# Author review guide — start here

## What exists now

- TMLR manuscript source: `paper/tmlr/main.tex`.
- Current review PDF: `output/pdf/kaetram-tool-routing-tmlr-draft.pdf`.
- Named archival wrapper: `paper/tmlr/arxiv.tex`.
- Current named PDF: `output/pdf/kaetram-tool-routing-arxiv.pdf`.
- Minimal arXiv source bundle: `output/arxiv/kaetram-tool-routing-arxiv-source.zip`.
- Anonymous supplement builder: `scripts/build_tmlr_supplement.py`.
- Claim rules: `research/paper/claims-evidence-matrix.md`.
- Current TMLR policy audit: `research/paper/tmlr-submission-audit-2026-07-28.md`.
- Harsh-review record: `research/paper/reviewer-simulation.md`.
- Research and artifact map: `research/INDEX.md`.

The historical NAACL draft and technical report remain provenance records, not
the manuscript to submit.

## What the paper actually claims

The paper is a routing audit, not an OPD training-method paper. Its central
finding is that a model can emit a parser-recoverable tool call in ordinary
assistant text while the executor reads only the structured tool-call field.
On the complete 1,200-request V2 finite grid, native schema exposure increased
this content-only incidence by +23, +13, and +10 percentage points at Base,
round 2, and round 3. The unchanged protocol then completed another 1,200
requests on a different retained panel with zero V2 source-path or
rendered-message overlap; the contrasts remained positive at +13, +5.5, and +9
points. This is robustness across two finite panels in one project lineage, not
independent replication.

The manuscript also preserves negative evidence:

- the 18-cell recovery factorial completed, but no output was eligible for
  recovery, so it did not identify a recovery effect;
- V1's nominal seeds were ineffective, so V1 is discovery evidence only;
- the model-free multi-action V2 measurement remains 9/9 protocol-valid but
  0/9 full-predicate-pass; and
- only the fresh post-amendment multi-action V3 run is reported as 9/9 on both
  counts.

The different-panel trigger-incidence V3 extension is complete on the same
zero-cost local setup. Its producer analysis and separately implemented local,
public-artifact, and anonymous-review verifiers all agree. The artifact contains
all 1,200 raw rows and regenerates the manuscript table.

## What the author should review first

1. Read the abstract, contribution list, limitations, and claim-to-evidence
   table. Those are the highest-leverage places for a scientific wording check.
2. Confirm that the model, game, and historical-development description matches
   what the project actually did.
3. Check the final V3 table against the generated public table; do not edit
   numeric cells by hand.
4. Decide the qualifying author list and order. Every author must meet TMLR's
   contribution criteria, approve the paper, have an active OpenReview profile,
   and accept responsibility for the work.
5. Confirm that no overlapping version is under review at another archival
   venue when this is submitted.

## What remains outside the paper's claim

- Human judgments of whether model-emitted calls are appropriate.
- Downstream quest utility for naturally emitted calls.
- A second model family, renderer, parser, state pool, or game.
- Independent training runs supporting checkpoint or OPD superiority.
- A causal recovery benefit.

These are real scientific limitations, but they are not hidden launch blockers.
The TMLR paper is intentionally scoped to the action-routing mechanism that the
current evidence can support.

## One-sentence status

The zero-cost evidence collection and release QA are complete: both paper modes,
both finite panels, the multi-action diagnostic, the fresh-extraction supplement
check, the anonymity gate, and the arXiv source bundle are green. Only human
authorship/consent, OpenReview-budget, parallel-submission, and upload decisions
remain.
