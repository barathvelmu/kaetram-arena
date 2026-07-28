from __future__ import annotations

import json
import asyncio
from copy import deepcopy
from pathlib import Path

from scripts.opd.live_routing_launcher import SessionSpec
from scripts.opd.live_routing_multi_action_diagnostic import expected_observation_fixture
from scripts.opd.live_routing_multi_action_launcher import session_worker


class Result:
    def __init__(self, value: dict, *, is_error: bool = False):
        self.text = json.dumps(value, sort_keys=True)
        self.is_error = is_error


def _observe_payload() -> dict:
    value = deepcopy(expected_observation_fixture())
    value["finished_quests"] = [{"name": name} for name in value["finished_quests"]]
    return value


class Handle:
    def __init__(self, spec: SessionSpec):
        self.spec = spec
        self.state = _observe_payload()
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict) -> Result:
        self.calls.append((name, arguments))
        if name == "__diagnostic_runtime_attestation":
            return Result(
                {
                    "schema_version": "kaetram.diagnostic-runtime-attestation.v1",
                    "session_id": self.spec.session_id,
                    "mcp_pid": 2000,
                    "mcp_process_group": 2000,
                    "mcp_instance_nonce": "1" * 32,
                    "browser_pid": 3000,
                    "browser_process_group": 3000,
                    "browser_launch_nonce": "2" * 32,
                    "browser_nonce_echo": "2" * 32,
                    "browser_name": "chromium",
                    "browser_version": "149.0.7827.55",
                    "browser_executable_sha256": "3" * 64,
                    "page_url": "http://127.0.0.1:9000/",
                    "player_username": self.spec.username,
                    "configured_client_url": "http://127.0.0.1:9000",
                    "configured_game_port": "9191",
                    "require_existing_account": True,
                    "heartbeats_disabled": True,
                    "loopback_only": True,
                }
            )
        if name == "observe":
            return Result(deepcopy(self.state))
        if name == "equip_item":
            self.state["inventory"] = [
                row for row in self.state["inventory"] if row["key"] != "coppersword"
            ]
            self.state["equipment"] = {"weapon": {"key": "coppersword", "count": 1}}
            return Result({"equipped": True, "item": "coppersword"})
        if name == "eat_food":
            self.state["inventory"] = [
                row for row in self.state["inventory"] if row["key"] != "apple"
            ]
            self.state["stats"]["hp"] = 45
            return Result({"consumed": True, "healed": 15})
        if name == "warp":
            self.state["pos"] = {"x": 189, "y": 158}
            return Result({"warped": True, "location": "mudwich"})
        raise AssertionError(name)


class Factory:
    def __init__(self, handle: Handle):
        self.handle = handle

    def __call__(self, **_: object) -> "Factory":
        return self

    async def __aenter__(self) -> Handle:
        return self.handle

    async def __aexit__(self, *_: object) -> None:
        return None


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 2.0
        return self.value


async def no_sleep(_: float) -> None:
    return None


def _spec(arm: str, phase: str = "treatment") -> SessionSpec:
    return SessionSpec(
        trial_id="trial-0001",
        session_id=f"llrma-local001-t01-{phase}",
        phase=phase,
        username="ma_local001_01",
        arm=arm,
    )


def _registration() -> dict:
    return {"runtime_parameters": {"minimum_delayed_observation_seconds": 1.5}}


def test_active_worker_executes_three_one_call_turns(monkeypatch, tmp_path: Path) -> None:
    spec = _spec("structured_direct")
    handle = Handle(spec)
    monkeypatch.setattr("scripts.opd.live_routing_multi_action_launcher.os.getpid", lambda: 1000)
    monkeypatch.setattr("scripts.opd.live_routing_multi_action_launcher.os.getpgrp", lambda: 1000)
    monkeypatch.setenv("KAETRAM_STATE_DIR", str(tmp_path / "state"))
    phase = asyncio.run(
        session_worker(
            spec,
            _registration(),
            action_order=["equip_item", "eat_food", "warp"],
            mcp_session_factory=Factory(handle),
            sleep=no_sleep,
            monotonic=Clock(),
        )
    )
    assert len(phase["turns"]) == 3
    assert [row["action"] for row in phase["turns"]] == [
        "equip_item", "eat_food", "warp"
    ]
    assert all(row["dispatch_attempted"] for row in phase["turns"])
    assert all(row["delivery_status"] == "confirmed" for row in phase["turns"])
    action_calls = [name for name, _ in handle.calls if name in {"equip_item", "eat_food", "warp"}]
    assert action_calls == ["equip_item", "eat_food", "warp"]


def test_off_worker_observes_each_turn_but_never_dispatches(monkeypatch, tmp_path: Path) -> None:
    spec = _spec("content_recovery_off")
    handle = Handle(spec)
    monkeypatch.setattr("scripts.opd.live_routing_multi_action_launcher.os.getpid", lambda: 1000)
    monkeypatch.setattr("scripts.opd.live_routing_multi_action_launcher.os.getpgrp", lambda: 1000)
    monkeypatch.setenv("KAETRAM_STATE_DIR", str(tmp_path / "state"))
    phase = asyncio.run(
        session_worker(
            spec,
            _registration(),
            action_order=["warp", "equip_item", "eat_food"],
            mcp_session_factory=Factory(handle),
            sleep=no_sleep,
            monotonic=Clock(),
        )
    )
    assert len(phase["turns"]) == 3
    assert all(row["router_status"] == "disabled_not_evaluated" for row in phase["turns"])
    assert all(row["dispatch_attempted"] is False for row in phase["turns"])
    assert not any(name in {"equip_item", "eat_food", "warp"} for name, _ in handle.calls)
