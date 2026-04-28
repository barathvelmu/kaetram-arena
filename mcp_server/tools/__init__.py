"""Import all tool modules so @mcp.tool() decorators fire at import time."""

from mcp_server.tools import (  # noqa: F401
    observe,
    combat,
    navigation,
    npc,
    shop,
    inventory,
    gathering,
    crafting,
    quest,
    test_lane,  # conditionally registers __test_close_session / __test_login when KAETRAM_TEST_LANE=1
)
