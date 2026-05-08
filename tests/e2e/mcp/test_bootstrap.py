"""Smoke test: prove the session fixture boots the isolated lane and the live
MCP stdio server exposes the curated tool surface."""

from __future__ import annotations

import pytest

from tool_surface import MODEL_VISIBLE_TOOL_NAMES

from tests.e2e.helpers.mcp_client import mcp_session


@pytest.mark.mcp_smoke
async def test_live_mcp_tool_surface_matches_curated_surface(
    isolated_lane, unique_username
):
    """The live stdio server should export exactly the curated model-visible tool set."""
    async with mcp_session(
        username=unique_username,
        client_url=isolated_lane.client_url,
    ) as session:
        live_tools = await session.list_tools()

    assert set(live_tools) == set(MODEL_VISIBLE_TOOL_NAMES)
    assert len(live_tools) == len(MODEL_VISIBLE_TOOL_NAMES)
