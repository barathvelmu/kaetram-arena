"""Tests for fail-closed evaluation artifact validation."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.validate_eval_results import ValidationError, validate_results


def _write_results(path: Path, *, statuses: list[str], scenario: str = "D") -> None:
    ok_count = statuses.count("ok")
    path.write_text(json.dumps({
        "meta": {
            "scenario": scenario,
            "total_episodes": len(statuses),
            "ok_episodes": ok_count,
        },
        "episodes": [
            {"episode": index, "status": status}
            for index, status in enumerate(statuses, start=1)
        ],
        "metrics": {},
    }))


def test_accepts_complete_matching_arm(tmp_path: Path) -> None:
    path = tmp_path / "results.json"
    _write_results(path, statuses=["ok", "ok"])

    validate_results(path, expected_episodes=2, expected_scenario="D")


@pytest.mark.parametrize(
    ("statuses", "message"),
    [
        (["ok"], "expected=2"),
        (["ok", "no_log"], "ok=1"),
        (["ok", "ok", "ok"], "expected=2"),
    ],
)
def test_rejects_incomplete_or_extra_episodes(
    tmp_path: Path, statuses: list[str], message: str
) -> None:
    path = tmp_path / "results.json"
    _write_results(path, statuses=statuses)

    with pytest.raises(ValidationError, match=message):
        validate_results(path, expected_episodes=2, expected_scenario="D")


def test_rejects_wrong_scenario(tmp_path: Path) -> None:
    path = tmp_path / "results.json"
    _write_results(path, statuses=["ok"], scenario="A")

    with pytest.raises(ValidationError, match="scenario mismatch"):
        validate_results(path, expected_episodes=1, expected_scenario="D")


def test_rejects_missing_and_malformed_files(tmp_path: Path) -> None:
    path = tmp_path / "results.json"
    with pytest.raises(ValidationError, match="missing results"):
        validate_results(path, expected_episodes=1, expected_scenario="D")

    path.write_text("not-json")
    with pytest.raises(ValidationError, match="invalid results"):
        validate_results(path, expected_episodes=1, expected_scenario="D")
