from __future__ import annotations

import copy
from pathlib import Path

import pytest

from scripts.opd import serving_regime_parity_probe as parity
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


def test_prior_v2_trust_root_and_thinking_source_are_verified() -> None:
    source_commit = "86c9452c7e205745983385ba29cfc48f714508cd"
    verified = parity._verify_prior_v2_identity(source_commit)
    assert verified["successful_requests"] == 1200
    assert verified["thinking_enabled_literals_verified"] is True
    assert len(verified["thinking_enabled_endpoint_source_sha256"]) == 64
    _registration, registration_sha256 = load_registration(REGISTRATION)
    receipts = parity._experiment_code_receipts(
        source_commit,
        registration_sha256,
    )
    assert [record["path"] for record in receipts] == [
        path.as_posix() for path in parity.EXPERIMENT_CODE_PATHS
    ]


def _synthetic_ok_row() -> dict:
    message = {"role": "assistant", "content": "No action.", "tool_calls": []}
    return {
        "schema_version": parity.RUN_SCHEMA,
        "snapshot": "base_2b",
        "schedule_index": 0,
        "state_id": "state-04",
        "state_index": 3,
        "sample_index": 0,
        "condition_id": "python-docs_no-tools",
        "seed": 1,
        "messages_sha256": "a" * 64,
        "tools_sha256": "b" * 64,
        "request_payload_sha256": "c" * 64,
        "latency_seconds": 1.0,
        "attempt_errors": [],
        "status": "ok",
        "finish_reason": "stop",
        "usage": {},
        "response_message": message,
        **parity.trigger.classify_response_message(message),
    }


def test_result_row_schema_is_fail_closed() -> None:
    row = _synthetic_ok_row()
    parity._validate_new_row("base_2b", row)
    row.pop("response_message")
    with pytest.raises(ParityError, match="result-row schema"):
        parity._validate_new_row("base_2b", row)


def test_result_row_rejects_invalid_value_types() -> None:
    row = _synthetic_ok_row()
    row["latency_seconds"] = True
    with pytest.raises(ParityError, match="malformed result-row value"):
        parity._validate_new_row("base_2b", row)

    row = _synthetic_ok_row()
    row["latency_seconds"] = float("nan")
    with pytest.raises(ParityError, match="malformed result-row value"):
        parity._validate_new_row("base_2b", row)

    row = _synthetic_ok_row()
    row["has_content"] = 1
    with pytest.raises(ParityError, match="malformed result-row value"):
        parity._validate_new_row("base_2b", row)


@pytest.mark.parametrize(
    "payload",
    ['{"same": 1, "same": 2}', '{"value": NaN}'],
)
def test_strict_json_rejects_duplicate_keys_and_nonfinite_values(payload: str) -> None:
    with pytest.raises(ParityError):
        parity._loads_strict(payload)


def _conditions(registration: dict) -> dict[str, dict]:
    source_registration, _design, _grid = parity._source_inputs(registration)
    return {
        item["condition_id"]: item for item in source_registration["conditions"]
    }


def test_historical_arm_is_bound_and_reclassified() -> None:
    registration, _digest = load_registration(REGISTRATION)
    rows, identity = parity._historical_thinking_rows(
        registration, "base_2b", _conditions(registration)
    )
    assert len(rows) == 340
    assert len(identity["artifact_index_sha256"]) == 64


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("seed", -1, "grid mismatch"),
        ("schedule_index", -1, "binding mismatch"),
        ("documentation", "tampered", "factor labels mismatch"),
        ("recovery_opportunity", None, "does not reclassify"),
    ],
)
def test_historical_arm_rejects_semantic_tampering(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    error: str,
) -> None:
    registration, _digest = load_registration(REGISTRATION)
    results_path = (
        ROOT / registration["source_panel"]["thinking_arm_artifact"]
        / "runs/base_2b/results.jsonl"
    )
    rows = parity._read_rows(results_path)
    selected = next(row for row in rows if row["state_index"] == 3)
    selected[field] = (
        not selected[field] if field == "recovery_opportunity" else value
    )
    monkeypatch.setattr(parity, "_read_rows", lambda _path: rows)
    with pytest.raises(ParityError, match=error):
        parity._historical_thinking_rows(
            registration, "base_2b", _conditions(registration)
        )
