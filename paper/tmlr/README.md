# TMLR manuscript package

This directory contains the anonymous TMLR review manuscript.

- `main.tex`: submission source.
- `references.bib`: bibliography copied from the audited ACL working draft.
- `tmlr.sty`, `tmlr.bst`, and `fancyhdr.sty`: byte-identical official template
  assets from the pinned TMLR template checkout.
- `TEMPLATE_LICENSE`: upstream template license.
- `TEMPLATE_PROVENANCE.md`: pinned upstream revision and exact asset hashes.

The V2 result table is generated from the sealed analysis and consumed directly
from:

```text
../../research/results/local-trigger-incidence-v2/paper-table-public.tex
```

Build and audit from the repository root with:

```bash
scripts/build_tmlr_submission.sh
```

The build is isolated under `tmp/pdfs/tmlr-build` and writes the reviewed PDF
to `output/pdf/kaetram-tool-routing-tmlr-draft.pdf`.

The review-mode style suppresses the placeholder author block. Do not add author
names, affiliations, repository URLs, local paths, endpoint addresses, or other
identity-bearing material to the review package.
