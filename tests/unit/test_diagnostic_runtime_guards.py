from __future__ import annotations

import re

from mcp_server.login import login_timeout_seconds, require_existing_account
from scripts.opd.live_routing_diagnostic import DESIGN_SOURCE_PATHS, REPO_ROOT
from tests.e2e.helpers.mcp_client import _registered_timeout


CORE_SOURCE = (REPO_ROOT / "mcp_server/core.py").read_text()


def test_existing_account_gate_defaults_off(monkeypatch) -> None:
    monkeypatch.delenv("KAETRAM_REQUIRE_EXISTING_ACCOUNT", raising=False)
    assert require_existing_account() is False


def test_existing_account_gate_accepts_only_explicit_truthy_values(monkeypatch) -> None:
    for value in ("1", "true", "TRUE", "yes"):
        monkeypatch.setenv("KAETRAM_REQUIRE_EXISTING_ACCOUNT", value)
        assert require_existing_account() is True
    monkeypatch.setenv("KAETRAM_REQUIRE_EXISTING_ACCOUNT", "0")
    assert require_existing_account() is False


def test_login_timeout_uses_registered_positive_finite_value(monkeypatch) -> None:
    monkeypatch.setenv("KAETRAM_LOGIN_TIMEOUT_SECONDS", "60")
    assert login_timeout_seconds() == 60.0
    for value in ("0", "-1", "nan", "inf", "-inf", "invalid"):
        monkeypatch.setenv("KAETRAM_LOGIN_TIMEOUT_SECONDS", value)
        assert login_timeout_seconds() == 18.0


def test_service_readiness_timeout_uses_positive_finite_value(monkeypatch) -> None:
    name = "KAETRAM_SERVICE_READINESS_TIMEOUT_SECONDS"
    monkeypatch.setenv(name, "60")
    assert _registered_timeout(name, 20.0) == 60.0
    for value in ("0", "-1", "nan", "inf", "-inf", "invalid"):
        monkeypatch.setenv(name, value)
        assert _registered_timeout(name, 20.0) == 20.0


def test_source_contract_covers_every_eagerly_loaded_browser_script() -> None:
    loader = (REPO_ROOT / "mcp_server/js/__init__.py").read_text()
    loaded = set(re.findall(r'_load\("([^"/]+\.js)"\)', loader))
    registered = {
        path.removeprefix("mcp_server/js/")
        for path in DESIGN_SOURCE_PATHS
        if path.startswith("mcp_server/js/") and path.endswith(".js")
    }
    assert loaded == registered


def test_source_contract_covers_complete_mcp_server_python_tree() -> None:
    discovered = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "mcp_server").rglob("*.py")
    }
    registered = {
        path for path in DESIGN_SOURCE_PATHS if path.startswith("mcp_server/")
    }
    assert discovered <= registered
    assert "mcp_game_server.py" in DESIGN_SOURCE_PATHS


def test_diagnostic_browser_rewrites_attested_client_endpoint_to_exact_lane() -> None:
    """The frozen client emits 0.0.0.0:9001; the diagnostic lane is local 9191."""

    marker = 'await context.add_init_script("""(() => {'
    diagnostic_script = CORE_SOURCE.split(marker, 1)[1].split('})()""")', 1)[0]
    assert "parsed.protocol !== 'ws:'" in diagnostic_script
    assert "parsed.username || parsed.password" in diagnostic_script
    assert "parsed.hostname = '127.0.0.1'" in diagnostic_script
    assert "parsed.port = '9191'" in diagnostic_script
    assert "parsed.hostname !== '127.0.0.1'" not in diagnostic_script
    assert "parsed.port !== '9191'" not in diagnostic_script


def test_diagnostic_browser_has_one_order_independent_websocket_wrapper() -> None:
    """The ordinary port-only wrapper must not stack on the diagnostic wrapper."""

    assert "if port and not diagnostic_loopback_only:" in CORE_SOURCE
    assert "diagnostic browser policy requires KAETRAM_PORT=9191" in CORE_SOURCE
    assert "--disable-background-networking" in CORE_SOURCE
    assert "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1" in CORE_SOURCE


def test_diagnostic_lane_bypasses_unsealed_optional_data(
    monkeypatch, tmp_path
) -> None:
    import json

    import mcp_server.mob_stats as mob_stats
    import mcp_server.resource_gates as resource_gates
    import mcp_server.tools.observe as observe

    (tmp_path / "mobs.json").write_text(
        json.dumps({"canary": {"name": "Canary", "level": 99}})
    )
    for name in ("trees.json", "rocks.json", "foraging.json", "fishing.json"):
        (tmp_path / name).write_text(
            json.dumps({"canary": {"name": "Canary", "levelRequirement": 99}})
        )
    monkeypatch.setenv("KAETRAM_DIAGNOSTIC_LANE", "1")
    monkeypatch.setenv("KAETRAM_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        observe,
        "load_quest_walkthroughs",
        lambda: (_ for _ in ()).throw(AssertionError("walkthrough read")),
    )
    monkeypatch.setattr(observe, "_WALKTHROUGH_BY_NAME", None)
    assert mob_stats._load_mobs() == {}
    assert resource_gates._load_gates() == {}
    assert observe._walkthrough_by_name() == {}


def test_diagnostic_lane_disables_login_auto_warp() -> None:
    login_source = (REPO_ROOT / "mcp_server/login.py").read_text()
    assert 'os.environ.get("KAETRAM_DIAGNOSTIC_LANE") != "1"' in login_source
    assert "and tutorial_unfinished" in login_source
