"""Shared model-visible tool surface for Kaetram agents.

The MCP server still carries a few legacy fallback tools for backwards
compatibility and local debugging, but the student model should only see this
smaller, curated action space.
"""

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

LEGACY_HIDDEN_TOOL_NAMES = (
    "login",
    "move",
    "talk_npc",
    "accept_quest",
    "clear_combat",
    "click_tile",
)

MODEL_VISIBLE_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "observe",
            "description": "Observe the current game state. Returns player stats, nearby entities, quests, inventory, and ASCII map.",
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
                    }
                },
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "interact_npc",
            "description": "Walk to an NPC, click through dialogue, and auto-accept or turn in quests.",
            "parameters": {
                "type": "object",
                "properties": {
                    "npc_name": {
                        "type": "string",
                        "description": "Name of the NPC",
                    }
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
                    }
                },
                "required": ["style"],
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
                    },
                },
                "required": ["skill", "recipe_key"],
            },
        },
    },
]
