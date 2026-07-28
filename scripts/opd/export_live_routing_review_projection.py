#!/usr/bin/env python3
"""Export an anonymous live-routing projection from a fully verified package."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.opd.live_routing_review_projection import (  # noqa: E402
    ProjectionError,
    build_review_projection,
    write_review_projection,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        projection = build_review_projection(
            args.package,
            args.registration,
            repo_root=args.repo_root,
            expected_head=args.expected_head,
        )
        write_review_projection(args.output, projection)
    except (OSError, ProjectionError, TypeError, ValueError) as exc:
        print(f"live-routing review projection export FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"live-routing review projection written: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
