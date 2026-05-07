"""observe() — Game state observation tool."""

import json as _json
import os

from mcp.server.fastmcp import Context

from mcp_server.core import get_page, log_tool, mcp
from mcp_server.js import OBSERVE_SCRIPT
from mcp_server.mob_stats import mob_info


def _enrich_mobs(gs_obj: dict) -> dict:
    """Add `level` and `aggressive` to each nearby mob entry from mobs.json.

    The browser-side observe payload only carries `name/x/y/dist/dir/hp/max_hp/
    reachable` per mob. Cross-referencing the in-game mob name against the
    bundled stat table gives the agent the level + aggro flag inline — so it
    can compare nearby.mobs[].level against stats.level without recalling
    the MOB PROGRESSION table from prompt context.
    """
    nearby = gs_obj.get("nearby") if isinstance(gs_obj, dict) else None
    if not isinstance(nearby, dict):
        return gs_obj
    mobs = nearby.get("mobs")
    if not isinstance(mobs, list):
        return gs_obj
    for m in mobs:
        if not isinstance(m, dict):
            continue
        info = mob_info(m.get("name"))
        if not info:
            continue
        if "level" not in m:
            m["level"] = info["level"]
        if "aggressive" not in m:
            m["aggressive"] = info["aggressive"]
    return gs_obj


@mcp.tool()
async def observe(ctx: Context) -> str:
    """Observe the current game state.

    Returns a unified view (~700-900 tokens) optimized for decision-making:
    - Player: pos, stats, equipment, skills
    - Status: dead, stuck, nav, indoors, combat target
    - Nearby: categorized NPCs, mobs, resources, ground items — with
      direction (N/S/E/W) and distance from player
    - Inventory: stacked by item key with counts
    - Quests: active and finished
    - Events: recent chat, combat, XP, NPC dialogue
    - ASCII map: terrain layout with entity symbols
    """
    log_tool("observe")
    page = await get_page(ctx)

    state_dir = os.environ.get("KAETRAM_STATE_DIR", "/tmp")

    result = await page.evaluate(OBSERVE_SCRIPT)

    # Enrich each nearby mob with `level` + `aggressive` from the bundled
    # mob stats table. Done Python-side rather than in JS to avoid coupling
    # observe.js to the data files. Survives a missing/corrupt JSON payload
    # by leaving `result` untouched on any decode error.
    try:
        if "\n\nASCII_MAP:" in result:
            head, sep, tail = result.partition("\n\nASCII_MAP:")
            gs_obj = _json.loads(head)
            gs_obj = _enrich_mobs(gs_obj)
            result = _json.dumps(gs_obj) + sep + tail
        else:
            gs_obj = _json.loads(result)
            gs_obj = _enrich_mobs(gs_obj)
            result = _json.dumps(gs_obj)
    except (ValueError, TypeError):
        pass

    # Write game_state.json for the dashboard (live state, no log parsing).
    try:
        gs_json = result.split("\n\nASCII_MAP:")[0] if "\n\nASCII_MAP:" in result else result
        if not gs_json.startswith("ERROR"):
            with open(os.path.join(state_dir, "game_state.json"), "w") as f:
                f.write(gs_json)
    except Exception:
        pass

    return result
