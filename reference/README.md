# Manuscript artifact boundary

The current primary audit manuscript is:

- source: `paper/tmlr/main.tex`
- rendered draft: `output/pdf/kaetram-tool-routing-tmlr-draft.pdf`
- anonymous supplement: `output/supplement/kaetram-tmlr-anonymous-supplement.zip`

`reference/naacl_submission.tex` is a historical ACL-format working manuscript,
and `reference/overview.pdf` is a historical technical report. Neither is the
current submission artifact, and their claims have not all survived the evidence
audit.

## Rebuild and validate

From any directory, run:

```bash
./scripts/build_tmlr_submission.sh
python3 scripts/build_tmlr_supplement.py
```

The paper build uses the vendored official TMLR style files, runs BibTeX and the
required LaTeX passes, rejects undefined citations or references and overfull
boxes, verifies US-Letter output, and writes the stable PDF. The supplement
builder then packages that PDF with the sealed V2 evidence and standalone
anonymous verifier. Intermediate files remain under `tmp/pdfs/` and are not
submission artifacts.
