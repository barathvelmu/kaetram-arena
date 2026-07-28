from __future__ import annotations

import hashlib
from pathlib import Path

from scripts.opd.live_routing_multi_action_orchestrator import _file_row


def test_file_row_binds_relative_name_size_and_bytes(tmp_path: Path) -> None:
    artifact = tmp_path / "receipts" / "trial-01.json"
    artifact.parent.mkdir()
    artifact.write_bytes(b'{"sealed":true}\n')
    assert _file_row(tmp_path, "receipts/trial-01.json") == {
        "path": "receipts/trial-01.json",
        "size_bytes": len(artifact.read_bytes()),
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
    }
