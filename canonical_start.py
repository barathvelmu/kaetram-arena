"""Single source of truth for the canonical Kaetram benchmark start."""
from __future__ import annotations

import re
from typing import Any


STARTER_INVENTORY = [
    {"slot": 0, "key": "bronzeaxe", "count": 1},
    {"slot": 1, "key": "knife", "count": 1},
    {"slot": 2, "key": "fishingpole", "count": 1},
    {"slot": 3, "key": "coppersword", "count": 1},
    {"slot": 4, "key": "woodenbow", "count": 1},
]
CANONICAL_INITIAL_STATE = {
    "pos": {"x": 328, "y": 892},
    "stats": {"hp": 69, "max_hp": 69, "level": 1, "xp": 0},
    "equipment": {},
    "skills": {},
    "inventory": STARTER_INVENTORY,
    "active_quests": [],
    "finished_quests": ["Miner's Quest"],
    "is_dead": False,
    "indoors": False,
}
CANONICAL_DB_QUESTS = [
    {
        "key": "minersquest",
        "stage": 2,
        "subStage": 0,
        "completedSubStages": [],
    },
]
CANONICAL_DB_TUTORIAL_QUEST = {
    "key": "tutorial",
    "stage": 16,
    "subStage": 0,
    "completedSubStages": [],
}
# bcrypt hash for the frozen local diagnostic password ``test``.  Keeping the
# value beside the canonical fixture avoids importing the PyMongo-backed E2E
# seeder into pure/offline validation code.
CANONICAL_BCRYPT_HASH = (
    "$2a$10$C78OFhflOeBZOXhGo7XHQ.8d9FF5xAjRBrVjxDm.b6.WmgGLgghJG"
)


def canonical_database_documents(username: str) -> dict[str, dict[str, Any]]:
    """Construct the exact diagnostic seed without importing database clients."""

    if not re.fullmatch(r"[a-z0-9_]{1,16}", username):
        raise ValueError("invalid canonical username")
    inventory = [
        {"index": index, "key": "", "count": 0, "enchantments": {}}
        for index in range(25)
    ]
    for item in STARTER_INVENTORY:
        inventory[item["slot"]] = {
            "index": item["slot"],
            "key": item["key"],
            "count": item["count"],
            "enchantments": {},
        }
    blank_slots = [
        {"index": index, "key": "", "count": 0, "enchantments": {}}
        for index in range(25)
    ]
    quests = [
        dict(CANONICAL_DB_TUTORIAL_QUEST),
        *(dict(quest) for quest in CANONICAL_DB_QUESTS),
    ]
    return {
        "player_inventory": {"username": username, "slots": inventory},
        "player_bank": {"username": username, "slots": blank_slots},
        "player_equipment": {"username": username, "equipments": []},
        "player_quests": {"username": username, "quests": quests},
        "player_achievements": {"username": username, "achievements": []},
        "player_skills": {"username": username, "skills": []},
        "player_statistics": {"username": username},
        "player_abilities": {"username": username, "abilities": []},
        "player_info": {
            "username": username,
            "password": CANONICAL_BCRYPT_HASH,
            "email": f"{username}@kaetrambench.test",
            "x": 328,
            "y": 892,
            "userAgent": "kaetram-live-routing-diagnostic",
            "rank": 0,
            "poison": {"type": -1, "remaining": -1},
            "effects": {},
            "hitPoints": 69,
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
        },
    }
CANONICAL_DATABASE_PROJECTION = {
    "pos": {"x": 328, "y": 892},
    "hit_points": 69,
    "inventory": STARTER_INVENTORY,
    "equipment": [],
    "quests": [
        {
            "key": "minersquest",
            "stage": 2,
            "sub_stage": 0,
            "completed_sub_stages": [],
        },
        {
            "key": "tutorial",
            "stage": 16,
            "sub_stage": 0,
            "completed_sub_stages": [],
        },
    ],
    "achievements": [],
    "skills": [],
    "statistics": {},
    "abilities": [],
}


def database_state_projection(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Normalize the nine owned Mongo documents without inventing fields."""

    if not isinstance(snapshot, dict):
        raise TypeError("database snapshot must be an object")
    documents = snapshot.get("documents", snapshot)
    if not isinstance(documents, dict):
        raise TypeError("database documents must be an object")

    def document(name: str) -> dict[str, Any]:
        value = documents.get(name)
        if not isinstance(value, dict):
            raise TypeError(f"database document must be an object: {name}")
        return value

    info = document("player_info")
    inventory_doc = document("player_inventory")
    slots = inventory_doc.get("slots")
    if not isinstance(slots, list):
        raise TypeError("player_inventory.slots must be a list")
    inventory = [
        {
            "slot": slot.get("index"),
            "key": slot.get("key"),
            "count": slot.get("count"),
        }
        for slot in slots
        if isinstance(slot, dict) and slot.get("key")
    ]
    inventory.sort(key=lambda item: item["slot"])
    quest_doc = document("player_quests")
    quest_rows = quest_doc.get("quests")
    if not isinstance(quest_rows, list):
        raise TypeError("player_quests.quests must be a list")
    quests = [
        {
            "key": quest.get("key"),
            "stage": quest.get("stage"),
            "sub_stage": quest.get("subStage"),
            "completed_sub_stages": quest.get("completedSubStages"),
        }
        for quest in quest_rows
        if isinstance(quest, dict)
    ]
    quests.sort(key=lambda quest: str(quest.get("key", "")))
    return {
        "pos": {"x": info.get("x"), "y": info.get("y")},
        "hit_points": info.get("hitPoints"),
        "inventory": inventory,
        "equipment": document("player_equipment").get("equipments"),
        "quests": quests,
        "achievements": document("player_achievements").get("achievements"),
        "skills": document("player_skills").get("skills"),
        "statistics": {
            key: value
            for key, value in document("player_statistics").items()
            if key not in {"_id", "username"}
        },
        "abilities": document("player_abilities").get("abilities"),
    }


def seed_canonical_player(username: str, *, db_name: str) -> dict[str, Any]:
    """Create the exact fresh player state used by recovered headline runs."""
    from bench.seed import STARTER_KIT, seed_player

    seeded = seed_player(
        username,
        position=(328, 892),
        hit_points=69,
        mana=20,
        inventory=list(STARTER_KIT),
        bank=[],
        equipment=[],
        quests=CANONICAL_DB_QUESTS,
        achievements=[],
        skills=[],
        statistics={},
        db_name=db_name,
    )
    return {
        "schema_version": "kaetram-canonical-start-receipt-v1",
        "username": username.lower(),
        "database": db_name,
        "expected_first_observation": CANONICAL_INITIAL_STATE,
        "seeded_documents": sorted(
            key
            for key, value in seeded.items()
            if key not in {"username", "player_info"} and value is not None
        ),
    }


def initial_state_projection(payload: dict) -> dict:
    """Select persistent fields that distinguish a clean benchmark player."""
    inventory = [
        {
            "slot": item.get("slot"),
            "key": item.get("key"),
            "count": item.get("count"),
        }
        for item in payload.get("inventory", [])
        if isinstance(item, dict)
    ]
    inventory.sort(key=lambda item: (item["slot"] is None, item["slot"]))
    finished = [
        quest.get("name")
        for quest in payload.get("finished_quests", [])
        if isinstance(quest, dict)
    ]
    return {
        "pos": payload.get("pos"),
        "stats": payload.get("stats"),
        "equipment": payload.get("equipment"),
        "skills": payload.get("skills"),
        "inventory": inventory,
        "active_quests": payload.get("active_quests"),
        "finished_quests": finished,
        "is_dead": payload.get("is_dead"),
        "indoors": payload.get("indoors"),
    }


def state_mismatches(
    actual: dict,
    expected: dict = CANONICAL_INITIAL_STATE,
) -> list[dict]:
    """Return stable field-level differences for audit and launch gates."""
    return [
        {"field": field, "expected": expected[field], "actual": actual.get(field)}
        for field in expected
        if actual.get(field) != expected[field]
    ]
