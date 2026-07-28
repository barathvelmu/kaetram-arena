from __future__ import annotations

from pathlib import Path
import json

import pytest

from scripts.opd.live_routing_multi_action_diagnostic import (
    SOURCE_PATHS,
    canonical_sha256,
    load_registration_strict,
)
from scripts.opd.live_routing_multi_action_prelaunch import (
    MultiActionPrelaunchError,
    build_prelaunch,
    source_inventory,
    trial_plan,
    verify_prelaunch,
)


ROOT = Path(__file__).resolve().parents[2]
REGISTRATION = ROOT / "research/experiments/local-live-routing-multi-action-v2.json"


def test_source_inventory_seals_every_v2_module_and_test() -> None:
    rows = source_inventory(ROOT)
    assert [row["path"] for row in rows] == list(SOURCE_PATHS)
    assert all(row["size_bytes"] > 0 and len(row["sha256"]) == 64 for row in rows)
    required = {
        "scripts/opd/live_routing_multi_action_diagnostic.py",
        "scripts/opd/live_routing_multi_action_launcher.py",
        "scripts/opd/live_routing_multi_action_analyzer.py",
        "scripts/opd/live_routing_multi_action_prelaunch.py",
        "scripts/opd/live_routing_multi_action_orchestrator.py",
        "scripts/opd/live_routing_multi_action_result_verify.py",
        "scripts/opd/live_routing_result_verify.py",
        "tests/unit/test_live_routing_multi_action_diagnostic.py",
        "tests/unit/test_live_routing_multi_action_launcher.py",
        "tests/unit/test_live_routing_multi_action_orchestrator.py",
        "tests/unit/test_live_routing_multi_action_analyzer.py",
        "tests/unit/test_live_routing_multi_action_prelaunch.py",
        "tests/unit/test_live_routing_multi_action_result_verify.py",
    }
    assert required.issubset(SOURCE_PATHS)
    v1_sources = set(
        json.loads(
            (ROOT / "research/experiments/local-live-routing-diagnostic-v1.json").read_text()
        )["source_contract"]["files"]
    )
    assert v1_sources.issubset(SOURCE_PATHS)


def test_prelaunch_plan_has_nine_unique_fresh_identities() -> None:
    registration = load_registration_strict(REGISTRATION)
    plans = trial_plan(registration, "deadbeef")
    assert len(plans) == 9
    assert len({row["username"] for row in plans}) == 9
    assert len({row["treatment_session_id"] for row in plans}) == 9
    assert all(len(row["username"]) <= 16 for row in plans)
    with pytest.raises(MultiActionPrelaunchError, match="eight lowercase hex"):
        trial_plan(registration, "NOT-HEX")


def test_built_prelaunch_is_self_hashed_and_source_bound() -> None:
    registration = load_registration_strict(REGISTRATION)
    receipt = build_prelaunch(
        registration,
        registration_raw_sha256="a" * 64,
        repo_root=ROOT,
        git_head="b" * 40,
        run_id="deadbeef",
    )
    unsigned = {key: value for key, value in receipt.items() if key != "payload_sha256"}
    assert receipt["payload_sha256"] == canonical_sha256(unsigned)
    assert receipt["source_inventory_sha256"] == canonical_sha256(receipt["source_inventory"])
    assert receipt["trial_plan_sha256"] == canonical_sha256(receipt["trials"])


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema_version":"a","schema_version":"b"}\n',
        b'{"schema_version":NaN}\n',
    ],
)
def test_prelaunch_verifier_rejects_duplicate_keys_and_nonfinite_json(tmp_path: Path, raw: bytes) -> None:
    prelaunch = tmp_path / "prelaunch.json"
    registration = tmp_path / "registration.json"
    prelaunch.write_bytes(raw)
    registration.write_text("{}\n", encoding="utf-8")
    with pytest.raises(MultiActionPrelaunchError, match="unreadable|duplicate|non-finite"):
        verify_prelaunch(prelaunch, registration, repo_root=ROOT, require_clean_head=False)


def test_prelaunch_verifier_rejects_symlink_inputs(tmp_path: Path) -> None:
    actual = tmp_path / "actual.json"
    actual.write_text("{}\n", encoding="utf-8")
    linked = tmp_path / "linked.json"
    linked.symlink_to(actual)
    with pytest.raises(MultiActionPrelaunchError, match="non-symlink"):
        verify_prelaunch(linked, actual, repo_root=ROOT, require_clean_head=False)
