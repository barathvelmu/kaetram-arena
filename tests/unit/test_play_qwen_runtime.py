from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import play_qwen


class _RecordingMCP:
    def __init__(self) -> None:
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return "ok"


def test_mcp_runtime_defaults_to_active_interpreter(monkeypatch) -> None:
    monkeypatch.setattr(
        play_qwen, "isolated_contract_active", lambda _environment: False
    )
    monkeypatch.delenv("KAETRAM_MCP_PYTHON", raising=False)
    assert play_qwen.resolve_mcp_python() == os.path.abspath(sys.executable)


def test_mcp_runtime_allows_an_explicit_executable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        play_qwen, "isolated_contract_active", lambda _environment: False
    )
    interpreter = tmp_path / "python"
    interpreter.write_text("#!/bin/sh\nexit 0\n")
    interpreter.chmod(0o755)
    monkeypatch.setenv("KAETRAM_MCP_PYTHON", str(interpreter))
    assert play_qwen.resolve_mcp_python() == os.path.abspath(interpreter)


def test_mcp_runtime_preserves_virtualenv_symlink(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        play_qwen, "isolated_contract_active", lambda _environment: False
    )
    base = tmp_path / "base-python"
    base.write_text("#!/bin/sh\nexit 0\n")
    base.chmod(0o755)
    venv = tmp_path / "venv" / "bin"
    venv.mkdir(parents=True)
    invoked = venv / "python"
    invoked.symlink_to(base)
    monkeypatch.setenv("KAETRAM_MCP_PYTHON", str(invoked))
    assert play_qwen.resolve_mcp_python() == os.path.abspath(invoked)
    assert play_qwen.resolve_mcp_python() != str(invoked.resolve())


@pytest.mark.parametrize("value", ["missing-python", "not-executable"])
def test_mcp_runtime_rejects_invalid_override(
    value: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        play_qwen, "isolated_contract_active", lambda _environment: False
    )
    candidate = tmp_path / value
    if value == "not-executable":
        candidate.write_text("not executable\n")
        candidate.chmod(0o644)
    monkeypatch.setenv("KAETRAM_MCP_PYTHON", str(candidate))
    with pytest.raises(RuntimeError, match="KAETRAM_MCP_PYTHON"):
        play_qwen.resolve_mcp_python()


def test_isolated_eval_rejects_mcp_interpreter_override(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        play_qwen, "isolated_contract_active", lambda _environment: True
    )
    monkeypatch.setenv("KAETRAM_MCP_PYTHON", str(tmp_path / "alternate"))
    with pytest.raises(RuntimeError, match="forbidden"):
        play_qwen.resolve_mcp_python()


def test_isolated_mcp_command_preserves_full_contract(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        play_qwen, "isolated_contract_active", lambda _environment: True
    )
    environment = tmp_path / ".venv-unit-tests-a"
    server = tmp_path / "repo" / "mcp_game_server.py"
    command = play_qwen.build_mcp_server_command(
        str(environment / "bin/python"), str(server)
    )
    assert command[0] == str(environment / "bin/python")
    assert command[1:4] == ["-I", "-S", "-B"]
    assert command[command.index("--script") + 1] == str(server)


def test_runtime_never_sends_schema_invalid_call_to_mcp() -> None:
    mcp = _RecordingMCP()
    result, invoked, reason = asyncio.run(
        play_qwen.call_schema_validated_tool(mcp, "navigate", {})
    )
    assert invoked is False
    assert reason == "missing_required_argument"
    assert "rejected by frozen schema" in result
    assert mcp.calls == []


def test_runtime_sends_schema_valid_call_once() -> None:
    mcp = _RecordingMCP()
    result, invoked, reason = asyncio.run(
        play_qwen.call_schema_validated_tool(
            mcp, "navigate", {"x": 10, "y": 20}
        )
    )
    assert (result, invoked, reason) == ("ok", True, "valid")
    assert mcp.calls == [("navigate", {"x": 10, "y": 20})]


def test_detailed_mcp_result_preserves_protocol_error_bit() -> None:
    class _Session:
        async def call_tool(self, name, arguments):
            assert (name, arguments) == ("navigate", {"x": 10, "y": 20})
            return SimpleNamespace(
                isError=True,
                content=[SimpleNamespace(text="path rejected")],
            )

    client = play_qwen.MCPClient("python", "server.py", {})
    client._session = _Session()
    detailed = asyncio.run(
        client.call_tool_detailed("navigate", {"x": 10, "y": 20})
    )
    assert detailed.text == "path rejected"
    assert detailed.is_error is True
    assert asyncio.run(client.call_tool("navigate", {"x": 10, "y": 20})) == (
        "path rejected"
    )


def test_runtime_receipt_distinguishes_mcp_error_from_transport_exception() -> None:
    class _DetailedMCP:
        async def call_tool_detailed(self, name, arguments):
            return play_qwen.TransportResult("application rejected", is_error=True)

    receipt = asyncio.run(
        play_qwen.execute_schema_validated_tool(
            _DetailedMCP(), "navigate", {"x": 10, "y": 20}
        )
    )
    assert receipt["frozen_schema"]["valid"] is True
    assert receipt["mcp"]["attempted"] is True
    assert receipt["mcp"]["protocol_success"] is False
    assert receipt["mcp"]["is_error"] is True
    assert receipt["mcp"]["exception"] is None


def test_execution_evidence_log_is_separate_and_deduplicated() -> None:
    class _Logger:
        def __init__(self):
            self.records = []

        def emit(self, record):
            self.records.append(record)

    logger = _Logger()
    evidence = {
        "mcp": {
            "result_text": "observe: large raw state",
            "result_sha256": "digest",
        },
        "observation": {"before": None, "after": None, "delta": None},
    }
    play_qwen.log_tool_execution_evidence(logger, 2, "call-1", evidence)
    assert len(logger.records) == 1
    record = logger.records[0]
    assert record["type"] == "tool_execution_evidence"
    assert record["tool_use_id"] == "call-1"
    assert "message" not in record
    assert "result_text" not in record["evidence"]["mcp"]
    assert record["evidence_sha256"] == play_qwen.evidence_sha256(
        record["evidence"]
    )
