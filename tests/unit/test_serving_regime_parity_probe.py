from __future__ import annotations

import copy
from pathlib import Path

import pytest

from scripts.opd.serving_regime_parity_probe import (
    ParityError,
    expected_schedule,
    load_registration,
    validate_health,
)


ROOT = Path(__file__).resolve().parents[2]
REGISTRATION = (
    ROOT / "research/experiments/local-serving-regime-parity-v1.json"
)


def test_confirmatory_schedule_excludes_pilot_and_replays_frozen_payloads() -> None:
    registration, digest = load_registration(REGISTRATION)
    assert len(digest) == 64
    assert registration["pilot_disclosure"]["state_indices"] == [0, 1, 2]
    for snapshot in registration["snapshots"]:
        schedule = expected_schedule(registration, snapshot)
        assert len(schedule) == 340
        expected_rows, payloads = zip(*schedule, strict=True)
        assert {row["state_index"] for row in expected_rows} == set(range(3, 20))
        assert all(row["state_index"] not in {0, 1, 2} for row in expected_rows)
        assert all(payload["seed"] == row["seed"] for row, payload in schedule)
        assert len({row["request_payload_sha256"] for row in expected_rows}) == 340


def _health(registration: dict, snapshot: str) -> dict:
    return {
        "status": "ok",
        "attestation": {
            **registration["snapshots"][snapshot],
            **registration["endpoint_contract"],
            "deployment_id": "local-test",
            "runtime_environment_receipt_sha256": "a" * 64,
        },
    }


def test_health_requires_explicit_deployment_parity_mode() -> None:
    registration, _digest = load_registration(REGISTRATION)
    health = _health(registration, "base_2b")
    validate_health(registration, "base_2b", health)

    wrong = copy.deepcopy(health)
    wrong["attestation"]["thinking_mode"] = "enabled"
    with pytest.raises(ParityError, match="thinking_mode"):
        validate_health(registration, "base_2b", wrong)


def test_health_rejects_checkpoint_alias_substitution() -> None:
    registration, _digest = load_registration(REGISTRATION)
    health = _health(registration, "base_2b")
    health["attestation"]["checkpoint_sha256"] = registration["snapshots"][
        "opd_r2_2b"
    ]["checkpoint_sha256"]
    with pytest.raises(ParityError, match="checkpoint_sha256"):
        validate_health(registration, "base_2b", health)
