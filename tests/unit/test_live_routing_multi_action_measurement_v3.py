from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

import scripts.opd.live_routing_multi_action_measurement_v3 as v3


REGISTRATION = Path("research/experiments/local-live-routing-multi-action-v3.json")


def _projection(
    *, hp: int = 30, equipped: str | None = None,
    apple: bool = True, warped: bool = False,
) -> dict:
    inventory = [
        {"slot": 0, "key": "bronzeaxe", "count": 1},
        {"slot": 3, "key": "coppersword", "count": 1},
    ]
    if apple:
        inventory.append({"slot": 5, "key": "apple", "count": 1})
    if equipped is not None:
        inventory = [row for row in inventory if row["key"] != "coppersword"]
    return {
        "pos": {"x": 189, "y": 158} if warped else {"x": 328, "y": 892},
        "hp": hp,
        "max_hp": 69,
        "inventory": inventory,
        "equipment": (
            [] if equipped is None else
            [{"slot": "weapon", "key": equipped, "count": 1}]
        ),
    }


def _record(projection: dict) -> dict:
    return {"available": True, "semantic_projection": projection}


def _receipt(arm: str) -> dict:
    active = arm != "content_recovery_off"
    if active:
        states = [
            _projection(hp=31, equipped="player/weapon/coppersword"),
            _projection(hp=69, equipped="player/weapon/coppersword", apple=False),
            _projection(
                hp=69, equipped="player/weapon/coppersword", apple=False, warped=True
            ),
        ]
        final_database = _projection(
            hp=69, equipped="coppersword", apple=False, warped=True
        )
    else:
        # Passive regeneration is intentionally present and scientifically irrelevant.
        states = [_projection(hp=31), _projection(hp=32), _projection(hp=33)]
        final_database = _projection(hp=34)
    order = ["equip_item", "eat_food", "warp"]
    turns = [
        {
            "action": action,
            "dispatch_attempted": active,
            "immediate": _record(deepcopy(state)),
            "delayed": _record(deepcopy(state)),
        }
        for action, state in zip(order, states, strict=True)
    ]
    return {
        "plan": {"trial_id": "future-v3-trial", "arm": arm, "action_order": order},
        "treatment": {"turns": turns},
        "reconnect": {"reconnect": _record(deepcopy(states[-1]))},
        "database": _record(final_database),
        "execution_evidence": {
            "candidate_call_ledger": (
                [{"sequence": index} for index in range(3)] if active else []
            )
        },
    }


def _parent_valid(arm: str) -> dict:
    return {
        "trial_id": "future-v3-trial",
        "arm": arm,
        "validity": "valid",
        "outcome": "fail",
        "invalid_reasons": [],
    }


def test_checked_in_registration_is_exact_and_explicitly_prospective() -> None:
    registration = v3._load_json_strict(REGISTRATION)
    assert v3.validate_registration(registration) == []
    assert registration["amendment_scope"]["execution_change"] == "none"
    assert registration["amendment_scope"]["receipt_schema_change"] == "none"
    assert registration["prospective_gate"]["forbid_pre_amendment_data"] is True
    assert "retroactive validation of the V2 result" in registration[
        "claim_boundary"
    ]["prohibited_claims"]


def test_registration_rejects_alias_and_reporting_drift() -> None:
    registration = v3._load_json_strict(REGISTRATION)
    changed = deepcopy(registration)
    changed["measurement"]["equipment_key_aliases"]["copper sword"] = "coppersword"
    assert "measurement contract drift" in v3.validate_registration(changed)
    changed = deepcopy(registration)
    changed["reporting"]["never_relabel_v2_failures"] = False
    assert "reporting guard drift" in v3.validate_registration(changed)


def test_equipment_alias_table_is_exhaustive_not_heuristic() -> None:
    assert v3.canonical_item_key("coppersword") == "coppersword"
    assert v3.canonical_item_key("player/weapon/coppersword") == "coppersword"
    assert v3.canonical_item_key("Copper Sword") == "Copper Sword"
    assert v3.canonical_item_key("items/coppersword") == "items/coppersword"


def test_client_and_database_equipment_keys_both_pass_registered_effect() -> None:
    for raw_key in ("player/weapon/coppersword", "coppersword"):
        effects = v3.registered_action_effects(
            _projection(hp=69, equipped=raw_key, apple=False, warped=True)
        )
        assert effects == {"equip_item": True, "eat_food": True, "warp": True}


def test_off_arm_accepts_hp_only_drift_with_zero_dispatch(monkeypatch) -> None:
    receipt = _receipt("content_recovery_off")
    monkeypatch.setattr(
        v3, "classify_trial_v2", lambda _: _parent_valid("content_recovery_off")
    )
    result = v3.classify_trial_v3(receipt)
    assert result["validity"] == "valid"
    assert result["outcome"] == "pass"
    assert result["failure_reasons"] == []


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("dispatch", "off_arm_nonzero_dispatch"),
        ("equip", "turn_1_immediate_equip_item_effect_present"),
        ("eat", "turn_1_immediate_eat_food_effect_present"),
        ("warp", "turn_1_immediate_warp_effect_present"),
    ],
)
def test_off_arm_fails_on_dispatch_or_registered_effect(monkeypatch, mutation, reason) -> None:
    receipt = _receipt("content_recovery_off")
    if mutation == "dispatch":
        receipt["treatment"]["turns"][0]["dispatch_attempted"] = True
    else:
        kwargs = {
            "equip": {"equipped": "player/weapon/coppersword"},
            "eat": {"hp": 69, "apple": False},
            "warp": {"warped": True},
        }[mutation]
        receipt["treatment"]["turns"][0]["immediate"] = _record(
            _projection(**kwargs)
        )
    monkeypatch.setattr(
        v3, "classify_trial_v2", lambda _: _parent_valid("content_recovery_off")
    )
    result = v3.classify_trial_v3(receipt)
    assert result["validity"] == "valid"
    assert result["outcome"] == "fail"
    assert reason in result["failure_reasons"]


def test_active_arm_accepts_client_key_and_database_key(monkeypatch) -> None:
    receipt = _receipt("structured_direct")
    monkeypatch.setattr(
        v3, "classify_trial_v2", lambda _: _parent_valid("structured_direct")
    )
    result = v3.classify_trial_v3(receipt)
    assert result["outcome"] == "pass"
    assert result["action_predicates"] == {
        "equip_item": True,
        "eat_food": True,
        "warp": True,
    }
    assert result["parent_v2_outcome"] == "fail"


def test_v3_replaces_only_measurement_failures_not_protocol_failures(monkeypatch) -> None:
    receipt = _receipt("structured_direct")
    parent = _parent_valid("structured_direct")
    parent["failure_reasons"] = [
        "turn_1_immediate_equip_item_predicate_failed",
        "turn_1_protocol_failure",
    ]
    monkeypatch.setattr(v3, "classify_trial_v2", lambda _: parent)
    result = v3.classify_trial_v3(receipt)
    assert result["outcome"] == "fail"
    assert result["failure_reasons"] == ["turn_1_protocol_failure"]


def test_parent_invalidity_remains_not_assessable(monkeypatch) -> None:
    receipt = _receipt("structured_direct")
    parent = _parent_valid("structured_direct")
    parent.update(
        validity="invalid",
        outcome="not_assessable",
        invalid_reasons=["delivery_unknown_after_exception"],
    )
    monkeypatch.setattr(v3, "classify_trial_v2", lambda _: parent)
    result = v3.classify_trial_v3(receipt)
    assert result["validity"] == "invalid"
    assert result["outcome"] == "not_assessable"
    assert result["failure_reasons"] == []


def test_prior_v2_prelaunch_is_ineligible_before_git_lookup(
    tmp_path: Path, monkeypatch
) -> None:
    prelaunch = {
        "git_head": v3.PRESERVED_V2_SOURCE_COMMIT,
        "registration": {"sha256": v3.PARENT_REGISTRATION_SHA256},
    }
    monkeypatch.setattr(v3, "verify_v2_prelaunch", lambda *args, **kwargs: prelaunch)
    with pytest.raises(v3.MultiActionV3Error, match="pre-amendment V2 run"):
        v3.verify_prospective_prelaunch(
            tmp_path / "prelaunch.json",
            REGISTRATION,
            parent_registration_path=tmp_path / "v2.json",
            repo_root=tmp_path,
        )


def test_future_prelaunch_must_contain_all_frozen_v3_files(
    tmp_path: Path, monkeypatch
) -> None:
    prelaunch = {
        "git_head": "f" * 40,
        "registration": {"sha256": v3.PARENT_REGISTRATION_SHA256},
    }
    monkeypatch.setattr(v3, "verify_v2_prelaunch", lambda *args, **kwargs: prelaunch)
    with pytest.raises(v3.MultiActionV3Error, match="does not contain frozen V3 source"):
        v3.verify_prospective_prelaunch(
            tmp_path / "prelaunch.json",
            REGISTRATION,
            parent_registration_path=tmp_path / "v2.json",
            repo_root=tmp_path,
        )


def _artifact_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    result_root = tmp_path / "result"
    receipts = result_root / "receipts"
    receipts.mkdir(parents=True)
    (result_root / "manifest.json").write_text("{}\n", encoding="utf-8")
    for index in range(1, 10):
        (receipts / f"trial-{index:02d}.json").write_text(
            json.dumps({"receipt": index}), encoding="utf-8"
        )
    registration = tmp_path / "v3.json"
    registration.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        v3,
        "verify_package",
        lambda *args, **kwargs: {
            "verdict": "complete_with_failures",
            "protocol_valid": 9,
            "full_predicate_pass": 0,
            "manifest_payload_sha256": "1" * 64,
        },
    )
    monkeypatch.setattr(
        v3,
        "verify_prospective_prelaunch",
        lambda *args, **kwargs: {
            "prelaunch_git_head": "2" * 40,
            "execution_contract_sha256": v3.PARENT_REGISTRATION_SHA256,
        },
    )
    monkeypatch.setattr(
        v3,
        "analyze_run_v3",
        lambda receipts: {
            "verdict": "complete",
            "protocol_valid": 9,
            "full_predicate_pass": 9,
            "receipt_count": len(receipts),
        },
    )
    return result_root, registration


def test_analysis_artifact_create_only_round_trip_and_tamper_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result_root, registration = _artifact_fixture(tmp_path, monkeypatch)
    artifact = v3.build_analysis_artifact(
        result_root,
        registration,
        parent_registration_path=tmp_path / "v2.json",
        repo_root=tmp_path,
    )
    output = tmp_path / "analysis-v3.json"
    v3.write_analysis_artifact(output, artifact)
    assert v3.verify_analysis_artifact(
        output,
        result_root,
        registration,
        parent_registration_path=tmp_path / "v2.json",
        repo_root=tmp_path,
    )["verified"] is True
    with pytest.raises(v3.MultiActionV3Error, match="already exists"):
        v3.write_analysis_artifact(output, artifact)
    changed = json.loads(output.read_text(encoding="utf-8"))
    changed["analysis"]["full_predicate_pass"] = 8
    output.write_text(v3.canonical_json(changed) + "\n", encoding="utf-8")
    with pytest.raises(v3.MultiActionV3Error, match="differs"):
        v3.verify_analysis_artifact(
            output,
            result_root,
            registration,
            parent_registration_path=tmp_path / "v2.json",
            repo_root=tmp_path,
        )


def test_analysis_artifact_rejects_noncanonical_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result_root, registration = _artifact_fixture(tmp_path, monkeypatch)
    artifact = v3.build_analysis_artifact(
        result_root,
        registration,
        parent_registration_path=tmp_path / "v2.json",
        repo_root=tmp_path,
    )
    output = tmp_path / "analysis-v3.json"
    output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(v3.MultiActionV3Error, match="canonical byte form"):
        v3.verify_analysis_artifact(
            output,
            result_root,
            registration,
            parent_registration_path=tmp_path / "v2.json",
            repo_root=tmp_path,
        )


def test_measurement_v3_is_directly_runnable_from_outside_repo() -> None:
    completed = subprocess.run(
        [sys.executable, str(Path(v3.__file__).resolve()), "--help"],
        cwd="/",
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert "{analyze,verify}" in completed.stdout
