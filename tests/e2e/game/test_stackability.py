"""Stackability contract — NON_STACKABLE_KEYS in helpers/seed.py must match
the items.json type field. Prevents seed auto-expansion logic from drifting
out of sync with the actual game data.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.e2e.helpers.seed import NON_STACKABLE_KEYS

KAETRAM_OPEN = Path(os.environ.get("KAETRAM_OPEN_PATH", Path.home() / "projects" / "Kaetram-Open"))
ITEMS_JSON = KAETRAM_OPEN / "packages" / "server" / "data" / "items.json"


def _load_items() -> dict:
    if not ITEMS_JSON.exists():
        pytest.skip(f"items.json not found at {ITEMS_JSON}")
    return json.loads(ITEMS_JSON.read_text())


@pytest.mark.parametrize("key", sorted(NON_STACKABLE_KEYS))
def test_non_stackable_key_is_actually_object_type(key):
    """Every key declared NON_STACKABLE in helpers/seed.py must have
    `type: "object"` (or similar non-countable) in items.json. If Kaetram
    changes an item to stackable the seed auto-expansion becomes wrong."""
    items = _load_items()
    entry = items.get(key)
    if entry is None:
        pytest.skip(f"{key} no longer in items.json — remove from NON_STACKABLE_KEYS")
    item_type = entry.get("type", "")
    # "object" = unique per slot. "countable" = stackable.
    # Anything else (weapon, armour, pet) is also non-stackable.
    assert item_type != "countable", (
        f"{key} is stackable (type=countable) — remove from NON_STACKABLE_KEYS"
    )


def test_no_stackable_leaked_into_non_stackable_list():
    """Catch-all: iterate known stackable items and confirm none snuck into
    NON_STACKABLE_KEYS."""
    items = _load_items()
    stackable = {k for k, v in items.items() if v.get("type") == "countable"}
    overlap = NON_STACKABLE_KEYS & stackable
    assert not overlap, (
        f"keys are stackable but listed as NON_STACKABLE: {sorted(overlap)}"
    )
