"""Frozen, ordered model-visible tool surface for Kaetram agents.

The MCP server exposes exactly this curated set; the training data and the
student model only ever see these names.
"""

from __future__ import annotations

import hashlib
import json


TOOL_SCHEMA_VERSION = "kaetram_mcp_v1"

MODEL_VISIBLE_TOOL_NAMES = (
    "observe",
    "attack",
    "navigate",
    "warp",
    "interact_npc",
    "eat_food",
    "buy_item",
    "equip_item",
    "drop_item",
    "set_attack_style",
    "cancel_nav",
    "stuck_reset",
    "gather",
    "loot",
    "query_quest",
    "respawn",
    "craft_item",
)

# Versioned execution-effect classes for evidence collection.  These labels do
# not assert that a potentially mutating call will succeed or persist; they
# only determine which measurements are scientifically applicable.
TOOL_EFFECT_CLASS_VERSION = "kaetram-tool-effect-class.v1"
MODEL_VISIBLE_TOOL_EFFECT_CLASSES = {
    "observe": "observation",
    "query_quest": "read_only",
    "attack": "potentially_mutating",
    "navigate": "potentially_mutating",
    "warp": "potentially_mutating",
    "interact_npc": "potentially_mutating",
    "eat_food": "potentially_mutating",
    "buy_item": "potentially_mutating",
    "equip_item": "potentially_mutating",
    "drop_item": "potentially_mutating",
    "set_attack_style": "potentially_mutating",
    "cancel_nav": "potentially_mutating",
    "stuck_reset": "potentially_mutating",
    "gather": "potentially_mutating",
    "loot": "potentially_mutating",
    "respawn": "potentially_mutating",
    "craft_item": "potentially_mutating",
}
if set(MODEL_VISIBLE_TOOL_EFFECT_CLASSES) != set(MODEL_VISIBLE_TOOL_NAMES):
    missing = sorted(
        set(MODEL_VISIBLE_TOOL_NAMES) - set(MODEL_VISIBLE_TOOL_EFFECT_CLASSES)
    )
    extra = sorted(
        set(MODEL_VISIBLE_TOOL_EFFECT_CLASSES) - set(MODEL_VISIBLE_TOOL_NAMES)
    )
    raise RuntimeError(
        "tool effect-class registry does not cover the frozen surface: "
        f"missing={missing}, extra={extra}"
    )

MODEL_VISIBLE_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "observe",
            "description": "Observe the current game state. Returns pos, stats (hp/max_hp/level/xp), equipment, skills, status, nearby (npcs/mobs/resources/ground_items), inventory, active_quests, finished_quests, events, plus an ASCII map.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "attack",
            "description": "Attack the nearest mob matching the given name. Auto-walks and auto-attacks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mob_name": {
                        "type": "string",
                        "description": "Name of the mob to attack (e.g. 'Rat', 'Snek')",
                    }
                },
                "required": ["mob_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "navigate",
            "description": "Pathfind to grid coordinates using BFS. Handles both short and long-distance movement.",
            "parameters": {
                "type": "object",
                "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "warp",
            "description": "Fast travel to a known location.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "Location name: mudwich, aynor, lakesworld, crullfield, patsow, undersea",
                        "default": "mudwich",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "interact_npc",
            "description": "Walk to an NPC and read dialogue. Quest offers are accepted only when explicitly requested; turn-ins happen through normal dialogue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "npc_name": {
                        "type": "string",
                        "description": "Name of the NPC",
                    },
                    "expect": {
                        "type": "string",
                        "description": "Expected interaction result: dialogue, shop, or any",
                        "default": "dialogue",
                    },
                    "include_ui_state": {
                        "type": "boolean",
                        "description": "Include a best-effort snapshot of the visible UI state",
                        "default": True,
                    },
                    "accept_quest_offer": {
                        "type": "boolean",
                        "description": "Explicitly accept a quest offer encountered during this interaction",
                        "default": False,
                    },
                },
                "required": ["npc_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "eat_food",
            "description": "Consume an edible item from inventory to restore HP.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slot": {
                        "type": "integer",
                        "description": "Inventory slot number",
                    }
                },
                "required": ["slot"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buy_item",
            "description": "Buy an item from an NPC shop. Must be adjacent to the NPC. Item indices start at 0.",
            "parameters": {
                "type": "object",
                "properties": {
                    "npc_name": {
                        "type": "string",
                        "description": "Store NPC name (e.g. 'Forester', 'Miner', 'Clerk')",
                    },
                    "item_index": {
                        "type": "integer",
                        "description": "Index of item in the shop (0-based)",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number to buy (default 1)",
                        "default": 1,
                    },
                },
                "required": ["npc_name", "item_index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "equip_item",
            "description": "Equip an item from inventory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slot": {
                        "type": "integer",
                        "description": "Inventory slot number",
                    }
                },
                "required": ["slot"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "drop_item",
            "description": "Drop an item from inventory to free space.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slot": {
                        "type": "integer",
                        "description": "Inventory slot number (0-24)",
                    }
                },
                "required": ["slot"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_attack_style",
            "description": "Change attack style.",
            "parameters": {
                "type": "object",
                "properties": {
                    "style": {
                        "type": "string",
                        "description": "Style name: hack, chop, defensive",
                        "default": "hack",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_nav",
            "description": "Cancel active navigation.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stuck_reset",
            "description": "Reset stuck detection after repeated failed movement.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gather",
            "description": "Gather from a nearby resource (tree, rock, bush, fish spot). Finds the nearest non-exhausted resource matching the name and collects it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "resource_name": {
                        "type": "string",
                        "description": "Resource name (e.g. 'Oak', 'Nisoc Rock', 'Tomato', 'Blueberry Bush')",
                    }
                },
                "required": ["resource_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "loot",
            "description": "Pick up nearby ground items and lootbag contents.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_quest",
            "description": "Look up quest status, requirements, unlocks, reward caveats, walkthrough, and boss notes for a specific quest.",
            "parameters": {
                "type": "object",
                "properties": {
                    "quest_name": {
                        "type": "string",
                        "description": "Exact or near-exact quest name (e.g. 'Sorcery and Stuff', 'Scavenger', 'Royal Drama')",
                    }
                },
                "required": ["quest_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "respawn",
            "description": "Respawn after death and recover to Mudwich.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "craft_item",
            "description": "Open the relevant production interface, select a recipe key, and craft or cook or smelt the requested amount.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "description": "Production skill: crafting, cooking, smithing, smelting, alchemy, fletching, or chiseling",
                    },
                    "recipe_key": {
                        "type": "string",
                        "description": "Exact recipe key (e.g. 'string', 'berylpendant', 'stew', 'tinbar', 'clamchowder')",
                    },
                    "count": {
                        "type": "integer",
                        "description": "How many to craft (default 1)",
                        "default": 1,
                    },
                },
                "required": ["skill", "recipe_key"],
            },
        },
    },
]


def canonical_tool_schema_json(tool_definitions=MODEL_VISIBLE_TOOL_DEFINITIONS) -> str:
    """Return the stable JSON bytes hashed into dataset/checkpoint contracts.

    Object keys are sorted, while list order (including tool and ``required``
    order) is intentionally preserved because it is model-visible.
    """
    return json.dumps(
        tool_definitions,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def tool_schema_sha256(tool_definitions=MODEL_VISIBLE_TOOL_DEFINITIONS) -> str:
    return hashlib.sha256(canonical_tool_schema_json(tool_definitions).encode()).hexdigest()


# Literal by design: editing any model-visible byte requires an intentional
# schema-version/hash update instead of silently changing future prompts.
MODEL_VISIBLE_TOOL_SCHEMA_SHA256 = "770c9a44b1e656c3798577627ddf08928a5787036e22a5e3358bf78ff6432cfe"
_COMPUTED_TOOL_SCHEMA_SHA256 = tool_schema_sha256()
if _COMPUTED_TOOL_SCHEMA_SHA256 != MODEL_VISIBLE_TOOL_SCHEMA_SHA256:
    raise RuntimeError(
        "frozen model-visible tool schema changed without a version/hash update: "
        f"expected={MODEL_VISIBLE_TOOL_SCHEMA_SHA256}, "
        f"actual={_COMPUTED_TOOL_SCHEMA_SHA256}"
    )


def validate_tool_definitions(tool_definitions) -> None:
    """Fail loudly if a request/dataset does not contain the frozen schema."""
    if tool_definitions is None:
        raise ValueError("tool_definitions cannot be None")
    names = tuple(tool["function"]["name"] for tool in tool_definitions)
    if names != MODEL_VISIBLE_TOOL_NAMES:
        raise ValueError(
            "model-visible tool order/name drift: "
            f"expected={MODEL_VISIBLE_TOOL_NAMES!r}, actual={names!r}"
        )
    actual_hash = tool_schema_sha256(tool_definitions)
    if actual_hash != MODEL_VISIBLE_TOOL_SCHEMA_SHA256:
        raise ValueError(
            "model-visible tool schema drift: "
            f"expected sha256={MODEL_VISIBLE_TOOL_SCHEMA_SHA256}, actual={actual_hash}"
        )


def validate_tool_call_arguments(name, arguments) -> tuple[bool, str]:
    """Validate an executor-bound call against the frozen visible schema.

    The function is deliberately dependency-free so the runtime router and
    offline evidence analyzers use exactly the same gate. It validates the
    subset of JSON Schema present in the frozen contract and rejects Python's
    ``bool`` values where an integer is required.
    """

    definitions = {
        tool["function"]["name"]: tool["function"]["parameters"]
        for tool in MODEL_VISIBLE_TOOL_DEFINITIONS
    }
    schema = definitions.get(name)
    if schema is None:
        return False, "unknown_function"
    if not isinstance(arguments, dict):
        return False, "invalid_arguments_object"
    properties = schema.get("properties", {})
    if set(arguments) - set(properties):
        return False, "unknown_argument"
    if set(schema.get("required", [])) - set(arguments):
        return False, "missing_required_argument"
    for argument_name, value in arguments.items():
        contract = properties[argument_name]
        expected = contract.get("type")
        valid_type = {
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
        }.get(expected, False)
        if not valid_type:
            return False, "wrong_argument_type"
        if "enum" in contract and value not in contract["enum"]:
            return False, "argument_outside_enum"
        if "minimum" in contract and value < contract["minimum"]:
            return False, "argument_below_minimum"
        if "maximum" in contract and value > contract["maximum"]:
            return False, "argument_above_maximum"
    return True, "valid"


def _functional_parameter_schema(tool_definition: dict) -> dict:
    """Normalize the execution-relevant subset of an OpenAI tool schema."""
    parameters = tool_definition["function"]["parameters"]
    properties = {}
    for name, schema in parameters.get("properties", {}).items():
        property_contract = {"type": schema.get("type")}
        if "default" in schema:
            property_contract["default"] = schema["default"]
        properties[name] = property_contract
    return {
        "type": parameters.get("type"),
        "properties": properties,
        "required": frozenset(parameters.get("required") or []),
    }


def validate_live_tool_compatibility(live_tool_definitions) -> None:
    """Abort canonical mode when the discovered MCP execution API drifted.

    Descriptions and Pydantic ``title`` fields are intentionally ignored: the
    frozen snapshot owns model-visible prose, while live MCP owns execution.
    Names and every parameter's type/default/required status must agree.
    """
    frozen_by_name = {
        tool["function"]["name"]: tool for tool in MODEL_VISIBLE_TOOL_DEFINITIONS
    }
    live_by_name = {
        tool["function"]["name"]: tool for tool in live_tool_definitions
    }
    if set(live_by_name) != set(frozen_by_name):
        raise ValueError(
            "live MCP tool-name drift: "
            f"expected={sorted(frozen_by_name)}, actual={sorted(live_by_name)}"
        )
    drift = {}
    for name in MODEL_VISIBLE_TOOL_NAMES:
        frozen = _functional_parameter_schema(frozen_by_name[name])
        live = _functional_parameter_schema(live_by_name[name])
        if live != frozen:
            drift[name] = {"expected": frozen, "actual": live}
    if drift:
        raise ValueError(f"live MCP functional schema drift: {drift!r}")
