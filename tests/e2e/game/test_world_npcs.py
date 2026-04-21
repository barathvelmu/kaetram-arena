"""World NPC integrity — every NPC documented in kaetram_world.py must have
a live spawn in packages/server/data/map/world.json.

Run this after any Kaetram-Open map pull to catch silently moved/removed
NPCs before quest/mcp tests fail with confusing "NPC not visible" errors.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.e2e.helpers.kaetram_world import NPCS

KAETRAM_OPEN = Path(os.environ.get("KAETRAM_OPEN_PATH", Path.home() / "projects" / "Kaetram-Open"))
WORLD_JSON = KAETRAM_OPEN / "packages" / "server" / "data" / "map" / "world.json"


def _load_world():
    if not WORLD_JSON.exists():
        pytest.skip(f"Kaetram-Open not found at {KAETRAM_OPEN}")
    return json.loads(WORLD_JSON.read_text())


@pytest.mark.parametrize("npc_key,expected_coords", sorted(NPCS.items()))
def test_npc_at_documented_coord(npc_key, expected_coords):
    """Every NPC key in kaetram_world.NPCS must be present at the declared
    tile in world.json. If map rebuilds move an NPC, update NPCS (or fix
    the map) — don't just remove the test."""
    world = _load_world()
    W = world["width"]
    x, y = expected_coords
    idx = str(y * W + x)
    entity = (world.get("entities") or {}).get(idx)
    assert entity == npc_key, (
        f"expected {npc_key!r} at ({x},{y}) tile-index {idx}, "
        f"world.json reports {entity!r}. "
        f"Either update kaetram_world.NPCS or investigate the map change."
    )
