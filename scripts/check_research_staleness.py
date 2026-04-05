#!/usr/bin/env python3
"""
Lightweight VM-safe staleness check for the compiled research knowledge base.

This is intentionally not an LLM task. It checks whether key research docs are
older than the code, logs, or generated datasets they are supposed to summarize.
Use it from cron on the VM and optionally send an email nudge via notifications.py.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from notifications import format_notification, send_email_notification


@dataclass
class Check:
    target: Path
    sources: list[Path]
    reason: str


def existing(paths: Iterable[Path]) -> list[Path]:
    return [p for p in paths if p.exists()]


def latest_mtime(paths: Iterable[Path]) -> tuple[float | None, Path | None]:
    found = [(p.stat().st_mtime, p) for p in paths if p.exists()]
    if not found:
        return None, None
    found.sort(key=lambda x: x[0], reverse=True)
    return found[0]


def fmt_ts(ts: float | None) -> str:
    if ts is None:
        return "missing"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def build_checks() -> list[Check]:
    return [
        Check(
            target=ROOT / "research" / "experiments" / "training-runs.md",
            sources=existing(
                [
                    ROOT / "session_log.md",
                    ROOT / "finetune" / "train_modal.py",
                    ROOT / "finetune" / "train_kto_modal.py",
                    ROOT / "finetune" / "serve_modal.py",
                    ROOT / "dataset" / "qwen_sft" / "train.json",
                    ROOT / "dataset" / "qwen_kto" / "train.json",
                ]
            ),
            reason="training runs / deployment state changed",
        ),
        Check(
            target=ROOT / "research" / "experiments" / "data-quality.md",
            sources=existing(
                [
                    ROOT / "session_log.md",
                    ROOT / "extract_turns.py",
                    ROOT / "convert_to_qwen.py",
                    ROOT / "dataset" / "qwen_sft" / "train.json",
                    ROOT / "dataset" / "qwen_kto" / "session_scores.json",
                ]
            ),
            reason="data pipeline or built dataset changed",
        ),
        Check(
            target=ROOT / "research" / "paper" / "contribution.md",
            sources=existing(
                [
                    ROOT / "session_log.md",
                    ROOT / "research" / "experiments" / "training-runs.md",
                    ROOT / "research" / "experiments" / "data-quality.md",
                    ROOT / "research" / "related-work" / "preference-learning.md",
                ]
            ),
            reason="paper framing inputs changed",
        ),
        Check(
            target=ROOT / "research" / "INDEX.md",
            sources=existing((ROOT / "research").rglob("*.md")),
            reason="research index may not reflect current files",
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether research/ docs are stale.")
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Send email if stale items are found and SMTP env vars are configured.",
    )
    parser.add_argument(
        "--grace-hours",
        type=float,
        default=1.0,
        help="Allowed lag between a source update and the compiled doc (default: 1h).",
    )
    args = parser.parse_args()

    checks = build_checks()
    stale: list[str] = []
    grace_seconds = args.grace_hours * 3600

    for check in checks:
        target_ts = check.target.stat().st_mtime if check.target.exists() else None
        source_ts, source_path = latest_mtime(check.sources)
        if source_ts is None:
            continue
        if target_ts is None or (source_ts - target_ts) > grace_seconds:
            stale.append(
                "\n".join(
                    [
                        f"Target: {check.target.relative_to(ROOT)}",
                        f"Reason: {check.reason}",
                        f"Doc time: {fmt_ts(target_ts)}",
                        f"Latest source: {source_path.relative_to(ROOT) if source_path else 'missing'}",
                        f"Source time: {fmt_ts(source_ts)}",
                    ]
                )
            )

    if stale:
        print("STALE")
        for item in stale:
            print("---")
            print(item)
        if args.notify:
            subject, body = format_notification(
                "Kaetram Research Docs Stale",
                [
                    f"Repo: {ROOT}",
                    f"Found {len(stale)} stale target(s).",
                    "",
                    *stale,
                    "",
                    "Run /compile-research or update the relevant research/*.md files.",
                ],
            )
            send_email_notification(subject, body)
        return 1

    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
