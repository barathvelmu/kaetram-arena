from __future__ import annotations

import json
import os
import types

import pytest

from mcp_server.core import (
    _diagnostic_browser_process_identity,
    _publish_diagnostic_browser_owner,
    _publish_diagnostic_owner,
)


def test_diagnostic_mcp_owner_is_create_only_and_canonical(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv("KAETRAM_DIAGNOSTIC_LANE", "1")
    monkeypatch.setenv("KAETRAM_STATE_DIR", str(state_dir))
    monkeypatch.setenv(
        "KAETRAM_DIAGNOSTIC_SESSION_ID", "llrd-local001-t01-treatment"
    )
    state = {
        "mcp_pid": 5001,
        "mcp_process_group": 5001,
        "mcp_instance_nonce": "1" * 32,
    }
    _publish_diagnostic_owner(state)
    path = state_dir / "diagnostic-mcp-owner.json"
    payload = json.loads(path.read_text())
    assert payload == {
        "schema_version": "kaetram.diagnostic-mcp-owner.v1",
        "session_id": "llrd-local001-t01-treatment",
        "mcp_pid": 5001,
        "mcp_process_group": 5001,
        "mcp_instance_nonce": "1" * 32,
    }
    assert path.read_bytes().endswith(b"\n")
    assert os.stat(path).st_mode & 0o077 == 0
    with pytest.raises(RuntimeError, match="creation failed"):
        _publish_diagnostic_owner(state)


def test_diagnostic_mcp_owner_refuses_nonleader_group(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv("KAETRAM_DIAGNOSTIC_LANE", "1")
    monkeypatch.setenv("KAETRAM_STATE_DIR", str(state_dir))
    monkeypatch.setenv(
        "KAETRAM_DIAGNOSTIC_SESSION_ID", "llrd-local001-t01-treatment"
    )
    with pytest.raises(RuntimeError, match="identity is unsafe"):
        _publish_diagnostic_owner(
            {
                "mcp_pid": 5001,
                "mcp_process_group": 5002,
                "mcp_instance_nonce": "1" * 32,
            }
        )


def test_diagnostic_browser_identity_requires_one_tagged_group_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "llrd-local001-t01-treatment"
    completed = types.SimpleNamespace(
        stdout=(
            f"6001 6001 /browser --kaetram-diagnostic-session={session_id}\n"
            f"6002 6001 /browser-helper --kaetram-diagnostic-session={session_id}\n"
        )
    )
    monkeypatch.setattr("mcp_server.core.subprocess.run", lambda *a, **k: completed)
    assert _diagnostic_browser_process_identity(session_id) == (6001, 6001)


def test_diagnostic_browser_owner_is_bound_and_create_only(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    session_id = "llrd-local001-t01-treatment"
    monkeypatch.setenv("KAETRAM_DIAGNOSTIC_LANE", "1")
    monkeypatch.setenv("KAETRAM_STATE_DIR", str(state_dir))
    monkeypatch.setenv("KAETRAM_DIAGNOSTIC_SESSION_ID", session_id)
    state = {
        "mcp_pid": 5001,
        "mcp_process_group": 5001,
        "mcp_instance_nonce": "1" * 32,
        "browser_pid": 6001,
        "browser_process_group": 6001,
        "browser_launch_nonce": "2" * 32,
        "browser_executable_sha256": "3" * 64,
    }
    _publish_diagnostic_browser_owner(state)
    path = state_dir / "diagnostic-browser-owner.json"
    payload = json.loads(path.read_text())
    assert payload["session_id"] == session_id
    assert payload["mcp_process_group"] == 5001
    assert payload["browser_pid"] == payload["browser_process_group"] == 6001
    assert payload["browser_launch_nonce"] == "2" * 32
    assert os.stat(path).st_mode & 0o077 == 0
    with pytest.raises(RuntimeError, match="creation failed"):
        _publish_diagnostic_browser_owner(state)
