"""Direct-pymongo player seeding for e2e tests.

Writes to the running `kaetram-mongo` Mongo container (port 27017, database
`kaetram_devlopment`). No REST helper required — tests run against any
Kaetram server already up on the host, using unique usernames per test for
isolation.

Collection schemas mirror what Kaetram-Open's server expects on login,
derived from packages/common/database/mongodb/creator.ts.
"""

from __future__ import annotations

import os
from typing import Any, Iterable, Sequence

from pymongo import MongoClient

DEFAULT_MONGO_URI = os.environ.get("KAETRAM_MONGO_URI", "mongodb://127.0.0.1:27017")
DEFAULT_DB_NAME = os.environ.get("KAETRAM_MONGO_DB", "kaetram_devlopment")

# bcrypt hash of password "test" — Kaetram's login compares via bcryptjs.
# Verified against packages/common/database/mongodb/mongodb.ts:92.
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

# Items that are NOT stackable (type: "object" in items.json). Seeding
# `{key: X, count: N}` in a single slot for these silently reduces to a
# single item server-side; quest turn-ins needing count >= N never fire.
# Auto-expanded across N consecutive slots in `_inventory_slots`.
NON_STACKABLE_KEYS: frozenset[str] = frozenset({
    "logs", "bluelily", "tomato", "strawberry", "paprika", "mushroom1",
    "bowlsmall", "bowlmedium", "stew", "clamobject", "clamchowder",
    "beryl", "berylpendant", "string", "cd", "seaweedroll", "rawshrimp",
    "cookedshrimp", "nisocore", "bead", "icesword", "snowpotion", "apple",
    "stick",
})

TUTORIAL_FINISHED_QUEST = {
    "key": "tutorial",
    "stage": 16,
    "subStage": 0,
    "completedSubStages": [],
}


def _client(uri: str = DEFAULT_MONGO_URI) -> MongoClient:
    return MongoClient(uri, serverSelectionTimeoutMS=3000)


def _upsert(db, collection: str, username: str, body: dict[str, Any]) -> None:
    body = dict(body)
    body["username"] = username.lower()
    db[collection].update_one(
        {"username": body["username"]},
        {"$set": body},
        upsert=True,
    )


def _default_player_info(username: str, x: int, y: int, **overrides: Any) -> dict[str, Any]:
    return {
        "username": username.lower(),
        "password": FIXED_BCRYPT_HASH,
        "email": f"{username.lower()}@e2e.test",
        "x": x, "y": y,
        "userAgent": "e2e-pytest",
        "rank": 0,
        "poison": {"type": -1, "remaining": -1},
        "effects": {},
        "hitPoints": 100, "mana": 20,
        "orientation": 1,
        "ban": 0, "jail": 0, "mute": 0,
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
    """Build 25-slot inventory array. Non-stackables auto-expanded across
    consecutive slots; stackables keep count in one slot."""
    slots = [{"index": i, "key": "", "count": 0, "enchantments": {}} for i in range(25)]
    pending: list[tuple[int, str, int, dict]] = []
    for raw in items or []:
        idx = int(raw.get("index", 0))
        key = raw.get("key", "")
        count = int(raw.get("count", 0) or 0)
        enchant = raw.get("enchantments", {})
        if not key:
            continue
        if key in NON_STACKABLE_KEYS and count > 1:
            for offset in range(count):
                pending.append((idx + offset, key, 1, enchant))
        else:
            pending.append((idx, key, count, enchant))

    used: set[int] = set()
    for target, key, count, enchant in pending:
        while target < 25 and target in used:
            target += 1
        if target >= 25:
            continue
        used.add(target)
        slots[target] = {"index": target, "key": key, "count": count,
                         "enchantments": enchant}
    return slots


def _with_default_tutorial(quests: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Auto-insert finished tutorial quest so applyTutorialBypass doesn't
    override our seeded spawn position."""
    merged: list[dict[str, Any]] = []
    seen = False
    for q in quests or []:
        merged.append(dict(q))
        if q.get("key") == TUTORIAL_FINISHED_QUEST["key"]:
            seen = True
    if not seen:
        merged.insert(0, dict(TUTORIAL_FINISHED_QUEST))
    return merged


def seed_player(
    username: str,
    *,
    position: Sequence[int] = (15, 15),
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
    helper_url: str = "",   # ignored — kept for back-compat with old callers
    mongo_uri: str = DEFAULT_MONGO_URI,
    db_name: str = DEFAULT_DB_NAME,
) -> dict[str, Any]:
    """Upsert player state into Mongo. Returns the exact seeded payload.

    `helper_url` is accepted-and-ignored for back-compat with the prior
    REST-helper seed API — existing arena test files pass it through.
    """
    x, y = int(position[0]), int(position[1])
    client = _client(mongo_uri)
    try:
        db = client[db_name]
        info = _default_player_info(
            username, x, y, hitPoints=hit_points, mana=mana,
            **(player_info_overrides or {}),
        )
        _upsert(db, "player_info", username, info)

        inv_slots = _inventory_slots(inventory)
        _upsert(db, "player_inventory", username, {"slots": inv_slots})

        if bank is not None:
            _upsert(db, "player_bank", username, {"slots": _inventory_slots(bank)})
        if equipment is not None:
            _upsert(db, "player_equipment", username, {"equipments": list(equipment)})

        merged_quests = _with_default_tutorial(quests)
        _upsert(db, "player_quests", username, {"quests": merged_quests})

        if achievements is not None:
            _upsert(db, "player_achievements", username,
                    {"achievements": list(achievements)})
        if skills is not None:
            _upsert(db, "player_skills", username, {"skills": list(skills)})
        if statistics is not None:
            _upsert(db, "player_statistics", username, dict(statistics))

        return {
            "username": username.lower(),
            "player_info": info,
            "inventory_slots": inv_slots,
            "quests": merged_quests,
        }
    finally:
        client.close()


def cleanup_player(username: str, helper_url: str = "",
                   mongo_uri: str = DEFAULT_MONGO_URI,
                   db_name: str = DEFAULT_DB_NAME) -> dict[str, int]:
    """Delete the player from every collection. Safe on never-seeded names.
    `helper_url` ignored (back-compat)."""
    client = _client(mongo_uri)
    try:
        db = client[db_name]
        return {
            coll: int(db[coll].delete_many({"username": username.lower()}).deleted_count)
            for coll in ALL_COLLECTIONS
        }
    finally:
        client.close()


def snapshot_player(username: str, mongo_uri: str = DEFAULT_MONGO_URI,
                    db_name: str = DEFAULT_DB_NAME) -> dict[str, Any]:
    """Read-only dump of all 9 collections for the user."""
    client = _client(mongo_uri)
    try:
        db = client[db_name]
        out: dict[str, Any] = {"username": username.lower()}
        for coll in ALL_COLLECTIONS:
            out[coll] = db[coll].find_one({"username": username.lower()}, {"_id": 0})
        return out
    finally:
        client.close()


def summarize_snapshot(snap: dict[str, Any]) -> dict[str, Any]:
    """Flatten a snapshot into scalars for before/after diffing."""
    info = snap.get("player_info") or {}
    stats = snap.get("player_statistics") or {}
    skills_doc = snap.get("player_skills") or {}
    quests_doc = snap.get("player_quests") or {}
    inv_doc = snap.get("player_inventory") or {}

    kills = sum(int(v) for v in (stats.get("mobKills") or {}).values()
                if isinstance(v, (int, float)))
    xp = 0
    if isinstance(skills_doc.get("skills"), list):
        for s in skills_doc["skills"]:
            xp += int(s.get("experience", 0) or 0)

    quest_state: dict[str, dict[str, Any]] = {}
    if isinstance(quests_doc.get("quests"), list):
        for q in quests_doc["quests"]:
            key = q.get("key")
            if key:
                quest_state[key] = {
                    "stage": int(q.get("stage", 0) or 0),
                    "sub_stage": int(q.get("subStage", 0) or 0),
                }

    items: list[dict[str, Any]] = []
    if isinstance(inv_doc.get("slots"), list):
        for slot in inv_doc["slots"]:
            if slot.get("key"):
                items.append({
                    "index": slot.get("index"),
                    "key": slot.get("key"),
                    "count": slot.get("count", 0),
                })

    return {
        "position": (
            {"x": info.get("x"), "y": info.get("y")} if info else None
        ),
        "hit_points": info.get("hitPoints"),
        "kills_total": kills,
        "xp_total": xp,
        "quests": quest_state,
        "inventory": items,
    }
DEFAULT_HELPER_URL = ""  # back-compat stub; pymongo-direct seed ignores REST URL
