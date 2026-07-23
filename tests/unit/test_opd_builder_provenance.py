from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("TWOB_EP", "http://127.0.0.1:8101/v1")
os.environ.setdefault("FOURB_EP", "http://127.0.0.1:8102/v1")

from scripts.opd import opd_2b_data as builder  # noqa: E402
from scripts.opd import opd_data_manifest  # noqa: E402


def _source_log(root: Path, run_id: str, content: str = "session") -> Path:
    path = (
        root
        / "dataset/raw/agent_test/runs"
        / run_id
        / "session_1.log"
    )
    path.parent.mkdir(parents=True)
    path.write_text(content)
    path.with_suffix(".meta.json").write_text('{"persona":"test"}\n')
    return path


def test_source_inventory_is_complete_and_detects_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builder, "REPO", tmp_path)
    source = _source_log(tmp_path, "run_a")
    inventory = builder._snapshot_source_logs(["run_a"])
    assert inventory[0]["run_id"] == "run_a"
    assert inventory[0]["meta_path"].endswith("session_1.meta.json")
    builder._verify_source_snapshot(inventory)

    source.write_text("changed")
    with pytest.raises(RuntimeError, match="changed during"):
        builder._verify_source_snapshot(inventory)
    source.write_text("session")
    inventory = builder._snapshot_source_logs(["run_a"])
    source.with_suffix(".meta.json").write_text('{"persona":"changed"}\n')
    with pytest.raises(RuntimeError, match="metadata changed during"):
        builder._verify_source_snapshot(inventory)
    with pytest.raises(RuntimeError, match="no source logs"):
        builder._snapshot_source_logs(["missing"])
    with pytest.raises(RuntimeError, match="unique"):
        builder._snapshot_source_logs(["run_a", "run_a"])


def test_source_inventory_requires_adjacent_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builder, "REPO", tmp_path)
    source = _source_log(tmp_path, "run_a")
    source.with_suffix(".meta.json").unlink()
    with pytest.raises(RuntimeError, match="no adjacent session metadata"):
        builder._snapshot_source_logs(["run_a"])


def test_declared_parse_failure_is_not_silently_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builder, "REPO", tmp_path)
    _source_log(tmp_path, "run_a")
    inventory = builder._snapshot_source_logs(["run_a"])

    def fail(_path):
        raise ValueError("bad log")

    monkeypatch.setattr(builder, "reconstruct_session", fail)
    with pytest.raises(RuntimeError, match="failed to parse declared"):
        builder.collect_action_states(inventory)


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class _Client:
    def __init__(self, payload: dict):
        self.payload = payload
        self.urls: list[str] = []

    async def get(self, url: str, timeout: int) -> _Response:
        self.urls.append(url)
        assert timeout == 60
        return _Response(self.payload)


def _health() -> dict:
    return {
        "status": "ok",
        "capabilities": ["chat", "score"],
        "attestation": {
            "deployment_id": "student-deployment",
            "api_model": "2b-base",
            "checkpoint_sha256": "a" * 64,
            "tokenizer_sha256": "b" * 64,
            "render_contract_sha256": "c" * 64,
        },
    }


@pytest.mark.asyncio
async def test_endpoint_identity_is_read_from_health_and_must_match() -> None:
    client = _Client(_health())
    actual = await builder._verified_endpoint_attestation(
        client,
        "http://127.0.0.1:8101/v1",
        expected_deployment_id="student-deployment",
        expected_checkpoint_sha256="a" * 64,
    )
    assert actual == _health()["attestation"]
    assert client.urls == ["http://127.0.0.1:8101/health"]

    with pytest.raises(RuntimeError, match="does not match"):
        await builder._verified_endpoint_attestation(
            _Client(_health()),
            "http://127.0.0.1:8101/v1",
            expected_deployment_id="other-deployment",
            expected_checkpoint_sha256="a" * 64,
        )


def test_no_generic_root_attestor_is_exposed() -> None:
    assert not hasattr(opd_data_manifest, "create_opd_data_manifest")
    source = Path(builder.__file__).read_text()
    assert "open(rec_path, \"x\")" in source
    assert "There is intentionally no reusable" in source


def test_material_build_inputs_snapshot_and_detect_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(builder, "REPO", tmp_path)
    monkeypatch.setattr(builder, "BUILD_SOURCE_PATHS", ("one.py", "prompt.md"))
    (tmp_path / "one.py").write_text("one\n")
    (tmp_path / "prompt.md").write_text("prompt\n")
    snapshot = builder._snapshot_build_sources()
    builder._verify_build_source_snapshot(snapshot)
    (tmp_path / "prompt.md").write_text("changed\n")
    with pytest.raises(RuntimeError, match="material build input changed"):
        builder._verify_build_source_snapshot(snapshot)


@pytest.mark.parametrize(
    "relative",
    ["finetune/serve_modal_4b.py", "finetune/serve_modal_2b_opd.py"],
)
def test_scoring_servers_expose_endpoint_identity(relative: str) -> None:
    source = (Path(__file__).parents[2] / relative).read_text()
    assert ".add_local_python_source(\"endpoint_identity\")" in source
    assert "endpoint_attestation(" in source
    assert '"attestation": attestation' in source
