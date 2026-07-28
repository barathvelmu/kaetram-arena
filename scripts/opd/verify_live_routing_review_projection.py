#!/usr/bin/env python3
"""Verify an anonymous live-routing projection against its private package."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.opd.live_routing_review_projection import (  # noqa: E402
    ProjectionError,
    load_review_projection,
    verify_review_projection_against_package,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projection", type=Path, required=True)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--registration", type=Path)
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--expected-head")
    args = parser.parse_args(argv)
    parity_values = (
        args.package,
        args.registration,
        args.repo_root,
        args.expected_head,
    )
    if any(value is not None for value in parity_values) and not all(
        value is not None for value in parity_values
    ):
        parser.error(
            "--package, --registration, --repo-root, and --expected-head "
            "must be supplied together"
        )
    try:
        if all(value is not None for value in parity_values):
            verify_review_projection_against_package(
                args.projection,
                args.package,
                args.registration,
                repo_root=args.repo_root,
                expected_head=args.expected_head,
            )
            mode = "private-package parity"
        else:
            load_review_projection(args.projection)
            mode = "standalone structure and claim boundary"
    except (OSError, ProjectionError, TypeError, ValueError) as exc:
        print(f"live-routing review projection verification FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"live-routing review projection verification passed ({mode}): {args.projection}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
