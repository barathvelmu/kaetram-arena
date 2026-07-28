from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

import scripts.opd.live_routing_multi_action_public_summary_v2 as public


def _record(projection: dict) -> dict:
    return {"available": True, "semantic_projection": deepcopy(projection)}


def _apply_action(projection: dict, action: str, *, database: bool = False) -> None:
    if action == "equip_item":
        projection["inventory"] = [
            row for row in projection["inventory"] if row["key"] != "coppersword"
        ]
        projection["equipment"] = [
            {
                "slot": 4 if database else "weapon",
                "key": "coppersword" if database else "player/weapon/coppersword",
                "count": 1,
            }
        ]
    elif action == "eat_food":
        projection["inventory"] = [
            row for row in projection["inventory"] if row["key"] != "apple"
        ]
        projection["hp"] = 69
    elif action == "warp":
        projection["pos"] = {"x": 189, "y": 158}


def _receipt(arm: str, order: list[str], index: int) -> dict:
    username = f"ma_deadbeef_{index:02d}"
    baseline = public.semantic_gameplay_projection(
        {"documents": public.multi_action_documents(username)}
    )
    active = arm != "content_recovery_off"
    current = deepcopy(baseline)
    turns = []
    for sequence, action in enumerate(order, start=1):
        if active:
            _apply_action(current, action)
        immediate = deepcopy(current)
        delayed = deepcopy(current)
        if not active and sequence == 1:
            delayed["hp"] = 31
            current["hp"] = 31
        turns.append(
            {
                "action": action,
                "schema_status": "valid" if active else "not_applicable_no_candidate",
                "router_status": {
                    "structured_direct": "not_applicable_structured",
                    "content_recovery_on": "promoted",
                    "content_recovery_off": "disabled_not_evaluated",
                }[arm],
                "dispatch_attempted": active,
                "tool_reported_error": None,
                "result_json": (
                    {"equipped": True, "item": "coppersword", "slot": 3}
                    if active and action == "equip_item"
                    else {} if active else None
                ),
                "immediate": _record(immediate),
                "delayed": _record(delayed),
            }
        )
    reconnect = deepcopy(current)
    database = deepcopy(current)
    if active and any(row["key"] == "player/weapon/coppersword" for row in database["equipment"]):
        for row in database["equipment"]:
            if row["key"] == "player/weapon/coppersword":
                row["key"] = "coppersword"
                row["slot"] = 4
    candidate_ledger = (
        [
            {
                "sequence": sequence,
                "delivery_status": "confirmed",
                "protocol_success": True,
            }
            for sequence in range(1, 4)
        ]
        if active
        else []
    )
    return {
        "plan": {
            "arm": arm,
            "action_order": order,
            "expected_candidate_invocations": 3 if active else 0,
            "username": username,
        },
        "treatment": {"turns": turns},
        "reconnect": {"reconnect": _record(reconnect)},
        "database": _record(database),
        "execution_evidence": {"candidate_call_ledger": candidate_ledger},
    }


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "result"
    receipts_dir = root / "receipts"
    receipts_dir.mkdir(parents=True)
    orders = [
        ["equip_item", "eat_food", "warp"],
        ["eat_food", "warp", "equip_item"],
        ["warp", "equip_item", "eat_food"],
    ]
    receipts = []
    index = 0
    for order in orders:
        for arm in public.ARMS:
            index += 1
            receipts.append(_receipt(arm, order, index))
    for index, receipt in enumerate(receipts, start=1):
        (receipts_dir / f"trial-{index:02d}.json").write_text(
            json.dumps(receipt), encoding="utf-8"
        )

    arms = {}
    for arm in public.ARMS:
        arms[arm] = {
            "technical_trials": 3,
            "action_predicate_pass": {
                "equip_item": 0 if arm != "content_recovery_off" else 0,
                "eat_food": 3 if arm != "content_recovery_off" else 0,
                "warp": 3 if arm != "content_recovery_off" else 0,
            },
        }
    analysis = {
        "technical_trials": 9,
        "protocol_valid": 9,
        "invalid": 0,
        "full_predicate_pass": 0,
        "behavioral_fail": 9,
        "verdict": "complete_with_failures",
        "arms": arms,
        "payload_sha256": "a" * 64,
    }
    (root / "analysis.json").write_text(json.dumps(analysis), encoding="utf-8")
    (root / "manifest.json").write_text("{}\n", encoding="utf-8")
    (root / "registration.json").write_text("{}\n", encoding="utf-8")
    (root / "prelaunch.json").write_text(
        json.dumps({"git_head": "f" * 40}), encoding="utf-8"
    )
    monkeypatch.setattr(public, "EXPECTED_SOURCE_COMMIT", "f" * 40)
    monkeypatch.setattr(
        public,
        "EXPECTED_MANIFEST_FILE_SHA256",
        hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        public,
        "EXPECTED_REGISTRATION_FILE_SHA256",
        hashlib.sha256((root / "registration.json").read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        public,
        "verify_package",
        lambda *args, **kwargs: {
            "manifest_payload_sha256": "b" * 64,
            "verdict": "complete_with_failures",
            "protocol_valid": 9,
            "full_predicate_pass": 0,
        },
    )
    return root


def test_builder_derives_registered_and_post_outcome_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _fixture(tmp_path, monkeypatch)
    summary = public.build_public_summary(root, repo_root=tmp_path)
    assert summary["registered_outcome"] == {
        "technical_trials": 9,
        "protocol_valid": 9,
        "protocol_invalid": 0,
        "full_predicate_pass": 0,
        "behavioral_fail": 9,
        "verdict": "complete_with_failures",
    }
    assert summary["protocol_delivery"]["scheduled_active_calls"] == 18
    assert summary["protocol_delivery"]["confirmed_deliveries"] == 18
    assert summary["post_outcome_measurement_audit"] == {
        "status": "descriptive_only_does_not_change_registered_outcome",
        "active_semantic_measurements_per_action": 36,
        "equip_item_semantic_effect_observed": 36,
        "eat_food_semantic_effect_observed": 36,
        "warp_semantic_effect_observed": 36,
        "equip_projection_mismatch": {
            "client_or_reconnect_namespaced_key": 30,
            "database_plain_key": 6,
            "registered_predicate_accepted_namespaced_key": False,
        },
        "off_arm_semantic_measurements": 24,
        "off_arm_exact_baseline_measurements": 3,
        "off_arm_measurements_differing_only_by_passive_hp_regeneration": 21,
    }


def test_create_only_round_trip_and_count_tamper_rejection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _fixture(tmp_path, monkeypatch)
    summary = public.build_public_summary(root, repo_root=tmp_path)
    output = tmp_path / "public-summary.json"
    public.write_public_summary(output, summary)
    assert public.verify_public_summary(output, root, repo_root=tmp_path)["verified"] is True
    with pytest.raises(public.PublicSummaryV2Error, match="already exists"):
        public.write_public_summary(output, summary)
    output.chmod(0o644)
    changed = json.loads(output.read_text(encoding="utf-8"))
    changed["registered_outcome"]["full_predicate_pass"] = 9
    output.write_text(public.canonical_json(changed) + "\n", encoding="utf-8")
    with pytest.raises(public.PublicSummaryV2Error, match="self-hash mismatch"):
        public.verify_public_summary(output, root, repo_root=tmp_path)


@pytest.mark.parametrize(
    "leak",
    [
        {"username": "redacted"},
        {"note": "/Users/reviewer/private/result"},
        {"note": "llrma-deadbeef-t01"},
        {"service": "local-mongo"},
    ],
)
def test_forbidden_identity_path_and_service_scan_fails_closed(leak: dict) -> None:
    with pytest.raises(public.PublicSummaryV2Error, match="forbidden"):
        public._scan_forbidden(leak)


def test_public_summary_cli_is_directly_runnable_from_outside_repo() -> None:
    completed = subprocess.run(
        [sys.executable, str(Path(public.__file__).resolve()), "--help"],
        cwd="/",
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "{build,verify}" in completed.stdout
