#!/usr/bin/env python3
"""Build a deterministic, minimal arXiv source bundle from the named preprint."""

from __future__ import annotations

import hashlib
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "paper" / "tmlr"
BUILD = ROOT / "tmp" / "pdfs" / "tmlr-arxiv-build"
OUTPUT = ROOT / "output" / "arxiv" / "kaetram-tool-routing-arxiv-source.zip"
V2_TABLE = (
    ROOT
    / "research"
    / "results"
    / "local-trigger-incidence-v2"
    / "paper-table-public.tex"
)
V3_TABLE = (
    ROOT
    / "research"
    / "results"
    / "local-trigger-incidence-v3"
    / "paper-table-public.tex"
)
REPLACEMENTS = {
    "../../research/results/local-trigger-incidence-v2/paper-table-public.tex": (
        "v2-paper-table-public.tex"
    ),
    "../../research/results/local-trigger-incidence-v3/paper-table-public.tex": (
        "v3-paper-table-public.tex"
    ),
}


def main() -> int:
    required = {
        "arxiv.tex": SOURCE / "arxiv.tex",
        "arxiv.bbl": BUILD / "arxiv.bbl",
        "references.bib": SOURCE / "references.bib",
        "tmlr.sty": SOURCE / "tmlr.sty",
        "tmlr.bst": SOURCE / "tmlr.bst",
        "fancyhdr.sty": SOURCE / "fancyhdr.sty",
        "figure-data.tex": SOURCE / "figure-data.tex",
        "figures.tex": SOURCE / "figures.tex",
        "v2-paper-table-public.tex": V2_TABLE,
        "v3-paper-table-public.tex": V3_TABLE,
    }
    missing = [str(path) for path in required.values() if not path.is_file()]
    if missing:
        raise SystemExit("missing arXiv dependency: " + ", ".join(missing))
    main_text = (SOURCE / "main.tex").read_text()
    for old, new in REPLACEMENTS.items():
        if old not in main_text:
            raise SystemExit(f"expected table input not found: {old}")
        main_text = main_text.replace(old, new)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kaetram-arxiv-source-") as temporary:
        stage = Path(temporary)
        (stage / "main.tex").write_text(main_text)
        for name, source in required.items():
            (stage / name).write_bytes(source.read_bytes())
        with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(stage.iterdir()):
                info = zipfile.ZipInfo(path.name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())
    digest = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    print(f"built {OUTPUT} ({OUTPUT.stat().st_size} bytes, sha256={digest})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
