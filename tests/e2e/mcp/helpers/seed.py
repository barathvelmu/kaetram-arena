"""Seed a player into the isolated Kaetram e2emcp Mongo DB via the E2E REST helper.

Call `seed_player(username, ...)` before a test logs in. The REST helper upserts
each provided collection. Collections not passed are left absent, which the
server's loader treats as empty/default.

All collections use the shapes the server's loader expects:

  player_info        — fields from `packages/common/database/mongodb/creator.ts:22`
  player_inventory   — {slots: SlotData[]}     from `packages/common/types/slot.d.ts`
  player_bank        — {slots: SlotData[]}
  player_equipment   — {equipments: EquipmentData[]}
  player_quests      — {quests: QuestData[]}
  player_achievements — {achievements: AchievementData[]}
  player_skills      — {skills: SkillData[]}
  player_statistics  — flat StatisticsData

Password bcrypt hash is fixed to plaintext "test" (via `Utils.compare` in
`packages/common/database/mongodb/mongodb.ts:92`). If Kaetram's hashing scheme
changes, regenerate the hash and update FIXED_BCRYPT_HASH here.
"""

from __future__ import annotations

from typing import Any, Iterable

import httpx

DEFAULT_HELPER_URL = "http://127.0.0.1:19300/api/v1"
FIXED_BCRYPT_HASH = "$2a$10$C78OFhflOeBZOXhGo7XHQ.8d9FF5xAjRBrVjxDm.b6.WmgGLgghJG"
DEFAULT_PASSWORD = "test"

ALL_COLLECTIONS: tuple[str, ...] = (
    "player_info",
    "player_inventory",
    "player_bank",
    "player_equipment",
    "player_quests",
    "player_achievements",
    "player_skills",
    "player_statistics",
    "player_abilities",
)


def _post(helper_url: str, collection: str, username: str, body: dict[str, Any]) -> None:
    url = f"{helper_url}/{collection}/username/{username}"
    response = httpx.post(url, json=body, timeout=10.0)
    response.raise_for_status()


def _delete(helper_url: str, collection: str, username: str) -> None:
    url = f"{helper_url}/{collection}/username/{username}"
    response = httpx.delete(url, timeout=10.0)
    if response.status_code >= 400 and response.status_code != 404:
        response.raise_for_status()


def _default_player_info(username: str, x: int, y: int, **overrides: Any) -> dict[str, Any]:
    return {
        "username": username,
        "password": FIXED_BCRYPT_HASH,
        "email": f"{username}@e2emcp.test",
        "x": x,
        "y": y,
        "userAgent": "pytest-mcp-e2e",
        "rank": 0,
        "poison": {"type": -1, "remaining": -1},
        "effects": {},
        "hitPoints": 100,
        "mana": 20,
        "orientation": 1,
        "ban": 0,
        "jail": 0,
        "mute": 0,
        "lastWarp": 0,
        "mapVersion": -1,
        "regionsLoaded": [],
        "friends": [],
        "lastServerId": 1,
        "lastAddress": "127.0.0.1",
        "lastGlobalChat": 0,
        "guild": "",
        "pet": "",
        **overrides,
    }


def _inventory_slots(items: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Pad to 25 slots, filling the given items from index 0.

    Each item dict must have `key` and `count` at minimum. `enchantments` defaults
    to `{}` per `packages/common/types/slot.d.ts:10`.
    """
    slots: list[dict[str, Any]] = []
    items = list(items or [])

    for index in range(25):
        if index < len(items):
            raw = items[index]
            slots.append(
                {
                    "index": index,
                    "key": raw.get("key", ""),
                    "count": raw.get("count", 0),
                    "enchantments": raw.get("enchantments", {}),
                }
            )
        else:
            slots.append({"index": index, "key": "", "count": 0, "enchantments": {}})

    return slots


TUTORIAL_FINISHED_QUEST = {
    "key": "tutorial",
    "stage": 16,
    "subStage": 0,
    "completedSubStages": [],
}


def _with_default_tutorial(quests: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Ensure a finished tutorial quest is in the seeded quest list. Without
    this, the server's `applyTutorialBypass` (quests.ts:203) overrides the
    seeded spawn position."""
    tutorial_key = TUTORIAL_FINISHED_QUEST["key"]
    merged: list[dict[str, Any]] = []
    seen_tutorial = False
    for q in quests or []:
        merged.append(q)
        if q.get("key") == tutorial_key:
            seen_tutorial = True
    if not seen_tutorial:
        merged.insert(0, dict(TUTORIAL_FINISHED_QUEST))
    return merged


def seed_player(
    username: str,
    *,
    helper_url: str = DEFAULT_HELPER_URL,
    position: tuple[int, int] = (15, 15),
    hit_points: int = 100,
    mana: int = 20,
    inventory: Iterable[dict[str, Any]] | None = None,
    bank: Iterable[dict[str, Any]] | None = None,
    equipment: Iterable[dict[str, Any]] | None = None,
    quests: Iterable[dict[str, Any]] | None = None,
    achievements: Iterable[dict[str, Any]] | None = None,
    skills: Iterable[dict[str, Any]] | None = None,
    statistics: dict[str, Any] | None = None,
    player_info_overrides: dict[str, Any] | None = None,
) -> None:
    """Upsert every provided player document into the isolated Mongo DB.

    The tutorial quest is auto-marked finished unless the caller supplies its
    own tutorial entry. This keeps the seeded spawn position stable.
    """
    x, y = position
    info = _default_player_info(
        username,
        x,
        y,
        hitPoints=hit_points,
        mana=mana,
        **(player_info_overrides or {}),
    )

    _post(helper_url, "player_info", username, info)
    _post(
        helper_url,
        "player_inventory",
        username,
        {"username": username, "slots": _inventory_slots(inventory)},
    )

    if bank is not None:
        _post(
            helper_url,
            "player_bank",
            username,
            {"username": username, "slots": _inventory_slots(bank)},
        )

    if equipment is not None:
        _post(
            helper_url,
            "player_equipment",
            username,
            {"username": username, "equipments": list(equipment)},
        )

    merged_quests = _with_default_tutorial(quests)
    _post(
        helper_url,
        "player_quests",
        username,
        {"username": username, "quests": merged_quests},
    )

    if achievements is not None:
        _post(
            helper_url,
            "player_achievements",
            username,
            {"username": username, "achievements": list(achievements)},
        )

    if skills is not None:
        _post(
            helper_url,
            "player_skills",
            username,
            {"username": username, "skills": list(skills)},
        )

    if statistics is not None:
        _post(
            helper_url,
            "player_statistics",
            username,
            {"username": username, **statistics},
        )


def cleanup_player(username: str, helper_url: str = DEFAULT_HELPER_URL) -> None:
    """Delete the player's documents from every collection. Safe to call on a
    partially-seeded player."""
    for collection in ALL_COLLECTIONS:
        try:
            _delete(helper_url, collection, username)
        except httpx.HTTPError:
            pass
