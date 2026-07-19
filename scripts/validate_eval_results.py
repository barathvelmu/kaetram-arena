#!/usr/bin/env python3
"""Fail-closed validation for one completed evaluation arm."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


class ValidationError(ValueError):
    """The result artifact is missing, malformed, or incomplete."""


def validate_results(path: Path, expected_episodes: int, expected_scenario: str) -> None:
    if not path.is_file():
        raise ValidationError(f"missing results file: {path}")
    try:
        results = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"invalid results file {path}: {exc}") from exc

    episodes = results.get("episodes", [])
    ok_episodes = [episode for episode in episodes if episode.get("status") == "ok"]
    if len(episodes) != expected_episodes or len(ok_episodes) != expected_episodes:
        raise ValidationError(
            f"incomplete results in {path}: recorded={len(episodes)}, "
            f"ok={len(ok_episodes)}, expected={expected_episodes}"
        )

    meta = results.get("meta", {})
    if meta.get("total_episodes") != expected_episodes:
        raise ValidationError(
            f"total_episodes mismatch in {path}: "
            f"found={meta.get('total_episodes')!r}, expected={expected_episodes}"
        )
    if meta.get("ok_episodes") != expected_episodes:
        raise ValidationError(
            f"ok_episodes mismatch in {path}: "
            f"found={meta.get('ok_episodes')!r}, expected={expected_episodes}"
        )
    if meta.get("scenario") != expected_scenario:
        raise ValidationError(
            f"scenario mismatch in {path}: "
            f"found={meta.get('scenario')!r}, expected={expected_scenario!r}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--episodes", type=int, required=True)
    parser.add_argument("--scenario", required=True)
    args = parser.parse_args()

    try:
        validate_results(args.results, args.episodes, args.scenario)
    except ValidationError as exc:
        parser.exit(1, f"{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
