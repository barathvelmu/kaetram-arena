# TMLR submission audit — 2026-07-28

## Venue fit

TMLR's two acceptance questions are unusually well matched to the present
paper: whether its claims have accurate, convincing, clearly explained
evidence, and whether some part of the TMLR audience would find the findings
interesting. Novelty or a state-of-the-art result is not independently required.
The manuscript should therefore remain an evidence-bounded tool-agent audit,
not be inflated back into an unsupported training-method paper.

Official sources:

- Author guide: <https://jmlr.org/tmlr/author-guide.html>
- Acceptance criteria: <https://jmlr.org/tmlr/acceptance-criteria.html>
- Editorial policies: <https://jmlr.org/tmlr/editorial-policies.html>
- Submission instructions: <https://jmlr.org/tmlr/submissions.html>
- FAQ: <https://jmlr.org/tmlr/faq.html>

## Format and policy gates

- Review is double-blind. The manuscript and supplement must remain anonymous.
- The official TMLR style and template are mandatory and must not be modified to
  change fonts, margins, or layout.
- There is no formal page limit, although an unusually long main body may delay
  review. TMLR's FAQ specifically warns that more than 12 pages of main content
  falls outside the normal short-review timeline. References and appendix are
  not the main body.
- The supplement may be at most 100 MB, must be PDF or ZIP, must directly
  support the paper, and must also be anonymous.
- The work cannot overlap with a paper published at, accepted to, or
  simultaneously under review at another archival peer-reviewed venue. An arXiv
  preprint is allowed, but the anonymous TMLR submission must not link to an
  identity-bearing version.
- Every listed author must have an active OpenReview profile, know about the
  submission, meet the venue's contribution criteria, and accept responsibility
  for the final work.
- TMLR permits LLMs as assistive tools but requires an explicit first-page
  disclosure and leaves full responsibility with the human authors. The current
  title footnote satisfies that disclosure format; the authors must still
  personally verify every claim, result, and citation.
- TMLR applies an annual authorship-quota budget. Every author must have enough
  remaining budget in OpenReview before the submission can be entered.
- A concrete broader-impact statement is required when the work poses a
  significant risk of harm. This manuscript includes a bounded broader-impact
  paragraph even though the experiments use an isolated game and no human data.

## Current local gate status

| Gate | Status | Evidence / remaining action |
|---|---|---|
| Official style assets | Pass | Pinned template provenance and byte hashes are checked in. |
| PDF format | Pass on current draft | US Letter; embedded fonts; automated build and layout-warning gate. |
| Anonymous manuscript | Pass on current draft | Source, PDF text, metadata, and embedded URLs are scanned. The review and archival builds now seed distinct `\pdftrailerid` strings; a single shared string previously gave both PDFs an identical trailer `/ID`, which linked them directly. |
| Main-body length | Pass | References begin on page 12; total PDF is 17 pages including references and appendix. Main body stays within the 12-page short-review guidance. |
| V2 trigger evidence | Pass | Anonymous 1,200-row projection independently recomputes. |
| V3 trigger evidence | Pass | Different-panel run completed 1,200/1,200 requests; public and anonymous verifiers recompute the registered result and post-hoc routing decomposition. |
| Multi-action evidence | Pass | V2 remains failed; fresh V3 is separately reported 9/9 under the prospective amendment. |
| Anonymous supplement | Pass | Fail-closed 52-file ZIP includes the final review PDF, both trigger panels, V3 post-hoc evidence, standalone verifiers, and a hash-free multi-action summary. Both panels pass from a fresh extraction. |
| Claim/evidence audit | Pass | Three adversarial passes found no remaining statistical-overreach, novelty, or narrative blocker after the final wording corrections. |
| Named archival version | Pass | Separate TMLR preprint build names Barath Velmurugan, MIT, and the MIT email; its public provenance roots are isolated from the anonymous source and PDF. |
| Author list and consent | Human gate | Determine qualifying authors, order, OpenReview profiles, and explicit consent before submission. |
| TMLR authorship budget | Human gate | Check each author's remaining annual submission budget in OpenReview. |
| Parallel-submission check | Human gate | Confirm the work is not under review at another archival venue on submission day. |

## Certification boundary

TMLR's Outstanding certificate is an exceptional, field-wide distinction and
can be awarded long after publication. Featured certification similarly adds a
high novelty/significance threshold beyond ordinary acceptance. Neither label
should drive claim inflation. The defensible goal is first to satisfy the two
acceptance criteria with a transparent, interesting failure analysis and an
unusually reproducible evidence chain.
