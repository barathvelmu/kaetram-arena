"""Regression coverage for the isolated evaluation database lane."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_eval_harness_honors_explicit_mongo_lane() -> None:
    env = {**os.environ, "KAETRAM_MONGO_DB": "kaetram_eval"}
    selected = subprocess.check_output(
        [sys.executable, "-c", "import eval_harness; print(eval_harness.MONGO_DB)"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
    ).strip()

    assert selected == "kaetram_eval"


def test_parallel_eval_reset_and_harness_share_eval_database() -> None:
    launcher = (REPO_ROOT / "scripts" / "run-eval.sh").read_text()

    assert 'export KAETRAM_MONGO_DB="kaetram_eval"' in launcher
    assert "db = c[os.environ['KAETRAM_MONGO_DB']]" in launcher
    assert "c['kaetram_devlopment']" not in launcher
