"""Test-lane only MCP tools — conditionally registered.

When KAETRAM_TEST_LANE=1 is set in the MCP subprocess environment (set by
`tests/e2e/helpers/mcp_client.py:mcp_session` for every test run), this
module registers extra tools that the test harness uses to swap player
state between tests in a long-lived session.

These tools are NOT registered in production agent runs (the env var is
only set by the test harness), so the model-visible tool surface is
unchanged for data-collection / eval / production runs.

Tools live here, not in `core.py`, so the conditional registration is
self-contained and the cold path stays clean.
"""

from __future__ import annotations

import asyncio
import os

from mcp.server.fastmcp import Context

from mcp_server.core import log, mcp


# Settle window after navigating to about:blank — gives the server time to
# run handleClose -> save() -> removePlayer before the next login attempt.
# Kaetram's autosave is synchronous-ish (~200-500ms); 1.5s is comfortable.
_DISCONNECT_SETTLE_S = 1.5


if os.environ.get("KAETRAM_TEST_LANE") == "1":

    @mcp.tool()
    async def __test_close_session(ctx: Context) -> str:
        """Test-lane only: close the current game WS by navigating to
        about:blank. The server detects the disconnect, runs
        `handleClose()` (autosaves the current in-memory player state and
        releases the player slot), so a subsequent login can read fresh
        Mongo state.

        After return, the harness should re-write its seed (the autosave
        clobbered whatever was in Mongo before this call) and then call
        `__test_login` to reconnect.

        Conditionally registered behind KAETRAM_TEST_LANE=1.
        """
        page = ctx.request_context.lifespan_context["page"]
        if page is None:
            return "no page — nothing to close"
        try:
            await page.goto("about:blank")
        except Exception as e:
            log(f"[mcp][__test_close_session] page.goto(about:blank) failed: {e}")
            return f"close failed: {e}"
        await asyncio.sleep(_DISCONNECT_SETTLE_S)
        ctx.request_context.lifespan_context["logged_in"] = False
        log(f"[mcp] __test_close_session: WS closed, settled {_DISCONNECT_SETTLE_S}s")
        return "session closed"

    @mcp.tool()
    async def __test_login(ctx: Context) -> str:
        """Test-lane only: run the login flow against the current page.
        Pairs with `__test_close_session` to swap player state between
        tests. Assumes the harness has already (a) closed the prior
        session and (b) written a fresh Mongo seed for the username
        encoded in `KAETRAM_USERNAME`.

        Conditionally registered behind KAETRAM_TEST_LANE=1.
        """
        from mcp_server.login import login_impl

        page = ctx.request_context.lifespan_context["page"]
        if page is None:
            return "no page — browser not yet launched"
        return await login_impl(ctx, page)
