#!/usr/bin/env python3
"""Build compact, self-identifying digests for recovered historical runs."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Iterable, Mapping

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from run_manifest import ManifestError, hash_path, sha256_json
from scripts.audit_historical_artifacts import AGENTS, CLAIM_RUNS


SCHEMA_VERSION = "kaetram-historical-run-digests-v1"
HISTORICAL_RUNS = {
    **CLAIM_RUNS,
    "r10_source_corpus": (
        "run_20260504_140418",
        "run_20260504_172157",
        "run_20260504_221206",
        "run_20260505_150033",
        "run_20260505_214542",
    ),
}


def _source_manifest_record(path: Path) -> dict:
    return {
        "name": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def build_historical_run_digests(
    raw_root: Path,
    *,
    source_manifest: Path,
    groups: Iterable[str] | None = None,
    claim_runs: Mapping[str, Iterable[str]] = HISTORICAL_RUNS,
    agents: Iterable[str] = AGENTS,
) -> dict:
    """Hash every selected agent/run directory and its relative file tree."""
    selected_groups = tuple(groups) if groups is not None else tuple(sorted(claim_runs))
    unknown = sorted(set(selected_groups) - set(claim_runs))
    if unknown:
        raise ManifestError(f"unknown claim group(s): {', '.join(unknown)}")
    if not source_manifest.is_file():
        raise ManifestError(f"source manifest does not exist: {source_manifest}")

    bundles: list[dict] = []
    missing: list[str] = []
    for group in selected_groups:
        for run_id in claim_runs[group]:
            for agent in sorted(set(agents)):
                run_dir = raw_root / agent / "runs" / run_id
                if not run_dir.is_dir():
                    missing.append(str(run_dir))
                    continue
                descriptor = hash_path(run_dir, root=raw_root)
                bundles.append({
                    "claim_group": group,
                    "run_id": run_id,
                    "agent": agent,
                    "content": descriptor,
                })

    report = {
        "schema_version": SCHEMA_VERSION,
        "raw_root": str(raw_root),
        "source_manifest": _source_manifest_record(source_manifest),
        "claim_groups": {
            group: list(claim_runs[group])
            for group in selected_groups
        },
        "bundle_count": len(bundles),
        "bundles": bundles,
        "missing": missing,
        "complete": not missing,
    }
    report["manifest_sha256"] = sha256_json(report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, default=Path("dataset/raw"))
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument(
        "--groups",
        nargs="+",
        choices=sorted(HISTORICAL_RUNS),
        help="claim groups to include (default: all)",
    )
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="return success even when a required run directory is missing",
    )
    args = parser.parse_args(argv)

    try:
        report = build_historical_run_digests(
            args.raw_root,
            source_manifest=args.source_manifest,
            groups=args.groups,
        )
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
        print(args.out)
    else:
        print(rendered, end="")
    return 0 if report["complete"] or args.report_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
