"""Spawn-point + jail constants — the hardcoded coordinates in modules.ts
must parse correctly and point to sensible tiles.

Regressions of these constants can teleport every new player to invalid
positions silently.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

KAETRAM_OPEN = Path(os.environ.get("KAETRAM_OPEN_PATH", Path.home() / "projects" / "Kaetram-Open"))
MODULES_TS = KAETRAM_OPEN / "packages" / "common" / "network" / "modules.ts"


def _load_constants() -> dict[str, tuple[int, int]]:
    """Parse SPAWN_POINT / TUTORIAL_SPAWN_POINT / JAIL_SPAWN_POINT lines.

    They're written as `CONST: 'x,y'`. Grep-friendly — don't need a full TS
    parser.
    """
    if not MODULES_TS.exists():
        pytest.skip(f"modules.ts not found at {MODULES_TS}")
    text = MODULES_TS.read_text()
    out: dict[str, tuple[int, int]] = {}
    for key in ("SPAWN_POINT", "TUTORIAL_SPAWN_POINT", "JAIL_SPAWN_POINT"):
        m = re.search(rf"{key}:\s*'(\d+),(\d+)'", text)
        if m:
            out[key] = (int(m.group(1)), int(m.group(2)))
    return out


@pytest.mark.parametrize("key", ["SPAWN_POINT", "TUTORIAL_SPAWN_POINT", "JAIL_SPAWN_POINT"])
def test_spawn_constant_defined_and_parseable(key):
    consts = _load_constants()
    assert key in consts, f"{key} missing or malformed in modules.ts"
    x, y = consts[key]
    # Sanity: coordinates should be within a reasonable map range.
    assert 0 < x < 2000 and 0 < y < 2000, f"{key} = ({x}, {y}) looks wrong"
