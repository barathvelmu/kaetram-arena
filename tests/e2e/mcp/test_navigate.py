"""Layer A navigate happy path: seed a player, request a short move, assert
the position updates within the timeout.

Layer B test pending a real navigate call once the lane is confirmed green."""

from __future__ import annotations

import pytest

from tests.e2e.helpers.browser import browser_session, login_seeded_player
from tests.e2e.helpers.primitives import game_move_to
from tests.e2e.helpers.seed import cleanup_player, seed_player
from tests.e2e.helpers.wait import wait_for_state


@pytest.mark.mcp_smoke
async def test_layerA_navigate_short_range_happy_path(isolated_lane, unique_username):
    start_x, start_y = 199, 169
    target_x, target_y = 200, 169

    seed_player(
        unique_username,
        helper_url=isolated_lane.db_helper_url,
        position=(start_x, start_y),
    )
    try:
        async with browser_session() as (_browser, _context, page):
            await login_seeded_player(page, unique_username, client_url=isolated_lane.client_url)

            await game_move_to(page, target_x, target_y)

            final = await wait_for_state(
                page,
                lambda s: s["player"]["x"] == target_x and s["player"]["y"] == target_y,
                timeout=8.0,
            )

        assert final["player"]["x"] == target_x
        assert final["player"]["y"] == target_y
    finally:
        cleanup_player(unique_username, helper_url=isolated_lane.db_helper_url)
