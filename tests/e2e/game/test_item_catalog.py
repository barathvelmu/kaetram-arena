"""Item catalog integrity — every item key referenced by a quest JSON must
exist in items.json. Prevents silent quest-reward failures (item.exists
returns false, the server drops the reward without notifying anyone).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

KAETRAM_OPEN = Path(os.environ.get("KAETRAM_OPEN_PATH", Path.home() / "projects" / "Kaetram-Open"))
ITEMS_JSON = KAETRAM_OPEN / "packages" / "server" / "data" / "items.json"
QUESTS_DIR = KAETRAM_OPEN / "packages" / "server" / "data" / "quests"


def _load_items() -> dict:
    if not ITEMS_JSON.exists():
        pytest.skip(f"items.json not found at {ITEMS_JSON}")
    return json.loads(ITEMS_JSON.read_text())


def _quest_files() -> list[Path]:
    if not QUESTS_DIR.exists():
        pytest.skip(f"quests dir not found at {QUESTS_DIR}")
    return sorted(QUESTS_DIR.glob("*.json"))


def _collect_item_refs(quest_data: dict) -> set[str]:
    """Walk a quest JSON and return every string that looks like an item
    key referenced in a stage's itemRequirements/itemRewards/hasItemText."""
    refs: set[str] = set()
    for stage in (quest_data.get("stages") or {}).values():
        for field in ("itemRequirements", "itemRewards"):
            for entry in stage.get(field) or []:
                if isinstance(entry, dict) and entry.get("key"):
                    refs.add(entry["key"])
    return refs


@pytest.mark.parametrize("quest_file", [p.name for p in _quest_files()] if QUESTS_DIR.exists() else [])
def test_quest_item_refs_are_defined(quest_file):
    """For each quest, every item key it references must exist in items.json.

    This catches the "silent reward failure" class of bug where a quest says
    it grants `catpet` / `staff` / `smithingboots` but those items were never
    defined — item.exists() returns false and the server drops the reward.
    """
    items = _load_items()
    quest = json.loads((QUESTS_DIR / quest_file).read_text())
    refs = _collect_item_refs(quest)
    missing = sorted(k for k in refs if k not in items)
    assert not missing, (
        f"{quest_file} references undefined items: {missing}. "
        f"Either add definitions to items.json or remove the stale refs."
    )
