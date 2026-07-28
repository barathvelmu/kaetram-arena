from __future__ import annotations

from mcp_server.login import require_existing_account


def test_existing_account_gate_defaults_off(monkeypatch) -> None:
    monkeypatch.delenv("KAETRAM_REQUIRE_EXISTING_ACCOUNT", raising=False)
    assert require_existing_account() is False


def test_existing_account_gate_accepts_only_explicit_truthy_values(monkeypatch) -> None:
    for value in ("1", "true", "TRUE", "yes"):
        monkeypatch.setenv("KAETRAM_REQUIRE_EXISTING_ACCOUNT", value)
        assert require_existing_account() is True
    monkeypatch.setenv("KAETRAM_REQUIRE_EXISTING_ACCOUNT", "0")
    assert require_existing_account() is False
