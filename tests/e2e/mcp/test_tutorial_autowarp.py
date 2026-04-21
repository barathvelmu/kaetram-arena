"""Tutorial auto-warp — login-path regression lock.

If a player spawns in the Programmer's tutorial house (x 300-360, y 860-920)
the MCP server's login handler auto-warps to Mudwich. Without this, agents
get stuck attacking walls in the tutorial area gaining only "Loitering" XP.
"""

from __future__ import annotations

import pytest

from tests.e2e.helpers.mcp_client import mcp_session
from tests.e2e.helpers.seed import cleanup_player, seed_player


@pytest.mark.mcp_smoke
async def test_layerB_autowarp_fires_from_tutorial_spawn(isolated_lane, unique_username):
    """Seed at the Programmer's house; first observe must report a position
    OUTSIDE the tutorial zone (warp fired during login)."""
    seed_player(
        unique_username,
        helper_url=isolated_lane.db_helper_url,
        position=(328, 892),  # centre of tutorial zone
        inventory=[{"key": "bronzeaxe", "count": 1}],
    )
    try:
        async with mcp_session(
            username=unique_username,
            client_url=isolated_lane.client_url,
        ) as session:
            data = (await session.call_tool("observe", {})).json() or {}
            pos = data.get("pos") or data.get("player_position") or {}
            x, y = pos.get("x", 0), pos.get("y", 0)
            in_tutorial = (300 <= x <= 360) and (860 <= y <= 920)
            assert not in_tutorial, f"auto-warp did not fire: player at ({x}, {y})"
    finally:
        cleanup_player(unique_username, helper_url=isolated_lane.db_helper_url)


@pytest.mark.mcp_smoke
async def test_layerB_autowarp_respects_non_tutorial_seed(isolated_lane, unique_username):
    """Players seeded outside the tutorial zone must NOT be warped — gates
    the behavior on position, not applied blindly."""
    seed_player(
        unique_username,
        helper_url=isolated_lane.db_helper_url,
        position=(188, 157),  # Mudwich centre
        inventory=[{"key": "bronzeaxe", "count": 1}],
    )
    try:
        async with mcp_session(
            username=unique_username,
            client_url=isolated_lane.client_url,
        ) as session:
            data = (await session.call_tool("observe", {})).json() or {}
            pos = data.get("pos") or data.get("player_position") or {}
            x, y = pos.get("x", 0), pos.get("y", 0)
            # Should stay near Mudwich, nowhere near (328, 892)
            assert 180 <= x <= 200 and 150 <= y <= 170, (
                f"expected Mudwich-area spawn, got ({x}, {y})"
            )
    finally:
        cleanup_player(unique_username, helper_url=isolated_lane.db_helper_url)
