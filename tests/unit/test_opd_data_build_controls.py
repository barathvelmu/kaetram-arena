from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]


def _import_with(extra_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "TWOB_EP": "http://student.invalid/v1",
            "FOURB_EP": "http://teacher.invalid/v1",
            **extra_env,
        }
    )
    return subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import scripts.opd.opd_2b_data as m; "
                "print(m.CHUNK, m.NO_COUNTERFACTUAL, "
                "m._TOOL_SCHEMA_SOURCE, len(m._BUILD_TOOLS or []))"
            ),
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_data_build_controls_accept_strict_registered_values() -> None:
    result = _import_with(
        {
            "OPD_BUILD_CHUNK": "17",
            "OPD_BUILD_NO_CF": "false",
            "OPD_BUILD_TOOL_SCHEMA_SOURCE": "canonical",
        }
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("17 False canonical 17")


def test_data_build_rejects_zero_chunk() -> None:
    result = _import_with({"OPD_BUILD_CHUNK": "0"})
    assert result.returncode != 0
    assert "must be a positive integer" in result.stderr


def test_data_build_rejects_ambiguous_boolean() -> None:
    result = _import_with({"OPD_BUILD_NO_CF": "sometimes"})
    assert result.returncode != 0
    assert "must be one of" in result.stderr


def test_data_build_rejects_arbitrary_tool_snapshot() -> None:
    result = _import_with({"OPD_BUILD_TOOLS_JSON": "/tmp/stale-tools.json"})
    assert result.returncode != 0
    assert "unsafe and unsupported" in result.stderr
