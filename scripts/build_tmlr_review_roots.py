#!/usr/bin/env python3
"""Generate untracked TeX macros for anonymous review-artifact trust roots."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_tmlr_supplement import (
    V3_ARTIFACT,
    V3_RESULTS,
    build_review_artifact,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="kaetram-review-roots-") as temporary:
        root = Path(temporary)
        v2 = build_review_artifact(root / "artifact-v2")
        v3 = build_review_artifact(
            root / "artifact-v3",
            artifact=V3_ARTIFACT,
            results=V3_RESULTS,
            registration_relative=Path("design/effective-registration.json"),
        )
    payload = (
        "% Generated locally; do not commit or submit outside the anonymous PDF.\n"
        f"\\def\\VTwoReviewIndex{{{v2}}}\n"
        f"\\def\\VThreeReviewIndex{{{v3}}}\n"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary_output.write_text(payload)
    temporary_output.replace(args.output)
    print(f"generated anonymous review roots at {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
