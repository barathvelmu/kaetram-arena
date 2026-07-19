"""Database-lane contract for evaluation resets."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import eval_harness


def test_reset_player_db_targets_configured_database(monkeypatch) -> None:
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout="reset_ok\n")

    monkeypatch.setattr(eval_harness, "MONGO_DB", "kaetram_eval")
    monkeypatch.setattr(eval_harness.subprocess, "run", fake_run)

    assert eval_harness.reset_player_db("EvalBot") is True
    assert calls[0][4] == "kaetram_eval"
    assert "evalbot" in calls[0][-1]


def test_reset_player_db_reports_missing_confirmation(monkeypatch) -> None:
    monkeypatch.setattr(
        eval_harness.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=""),
    )

    assert eval_harness.reset_player_db("EvalBot") is False


def test_required_reset_aborts_on_missing_confirmation(monkeypatch) -> None:
    monkeypatch.setattr(eval_harness, "MONGO_DB", "kaetram_eval")
    monkeypatch.setattr(eval_harness, "reset_player_db", lambda username: False)

    with pytest.raises(RuntimeError, match="kaetram_eval"):
        eval_harness.require_player_db_reset("EvalBot")
