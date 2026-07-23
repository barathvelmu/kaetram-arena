from __future__ import annotations

import sys
from pathlib import Path

import pytest

import play_qwen


def test_mcp_runtime_defaults_to_active_interpreter(monkeypatch) -> None:
    monkeypatch.delenv("KAETRAM_MCP_PYTHON", raising=False)
    assert play_qwen.resolve_mcp_python() == str(Path(sys.executable).resolve())


def test_mcp_runtime_allows_an_explicit_executable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    interpreter = tmp_path / "python"
    interpreter.write_text("#!/bin/sh\nexit 0\n")
    interpreter.chmod(0o755)
    monkeypatch.setenv("KAETRAM_MCP_PYTHON", str(interpreter))
    assert play_qwen.resolve_mcp_python() == str(interpreter.resolve())


@pytest.mark.parametrize("value", ["missing-python", "not-executable"])
def test_mcp_runtime_rejects_invalid_override(
    value: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    candidate = tmp_path / value
    if value == "not-executable":
        candidate.write_text("not executable\n")
        candidate.chmod(0o644)
    monkeypatch.setenv("KAETRAM_MCP_PYTHON", str(candidate))
    with pytest.raises(RuntimeError, match="KAETRAM_MCP_PYTHON"):
        play_qwen.resolve_mcp_python()
