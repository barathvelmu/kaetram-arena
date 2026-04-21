"""Smoke test: prove the session fixture boots the isolated lane and the
REST helper is reachable. No game actions, no seeded player — the cheapest
possible check that the harness is wired up."""

from __future__ import annotations

import httpx
import pytest

from tool_surface import LEGACY_HIDDEN_TOOL_NAMES, MODEL_VISIBLE_TOOL_NAMES

from tests.e2e.helpers.mcp_client import mcp_session


@pytest.mark.skip(reason="REST helper lane deprecated — ambient Kaetram model uses pymongo-direct seeding")
@pytest.mark.mcp_smoke
async def test_lane_health_endpoints_up(isolated_lane):
    """Fixture-level health is already validated during boot (HTTP for helper
    + client, WS handshake for server). This test re-verifies the two stable
    HTTP endpoints; the server API root (:19102/) is intermittently ReadError
    right after boot and is skipped here — the WS check in the fixture is the
    authoritative server-ready signal."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        helper = await client.get(f"{isolated_lane.db_helper_url}/health")
        assert helper.status_code == 200

        client_page = await client.get(f"{isolated_lane.client_url}/")
        assert client_page.status_code < 500


@pytest.mark.skip(reason="REST helper lane deprecated — ambient Kaetram model uses pymongo-direct seeding")
@pytest.mark.mcp_smoke
async def test_rest_helper_round_trip(isolated_lane, unique_username):
    """Write to and read back a junk player_info doc."""
    url = f"{isolated_lane.db_helper_url}/player_info/username/{unique_username}"
    doc = {"username": unique_username, "x": 1, "y": 2}

    async with httpx.AsyncClient(timeout=5.0) as client:
        created = await client.post(url, json=doc)
        assert created.status_code < 400

        fetched = await client.get(url)
        assert fetched.status_code == 200
        body = fetched.json()
        assert isinstance(body, list)
        assert body and body[0].get("username") == unique_username

        deleted = await client.delete(url)
        assert deleted.status_code < 400


@pytest.mark.mcp_smoke
async def test_live_mcp_tool_surface_matches_curated_surface(
    isolated_lane, unique_username
):
    """The live stdio server should export the exact model-visible tool set and
    keep legacy helpers hidden from agent-facing sessions."""
    async with mcp_session(
        username=unique_username,
        client_url=isolated_lane.client_url,
    ) as session:
        live_tools = await session.list_tools()

    assert set(live_tools) == set(MODEL_VISIBLE_TOOL_NAMES)
    assert len(live_tools) == len(MODEL_VISIBLE_TOOL_NAMES)
    assert not (set(live_tools) & set(LEGACY_HIDDEN_TOOL_NAMES))
