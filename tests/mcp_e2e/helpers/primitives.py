"""Packet-level game primitives driven via `page.evaluate`.

Each primitive sends the same packet shape Kaetram's own client would send,
bypassing UI and (intentionally) tile-math for canvas clicks. This mirrors
what `~/projects/kaetram-agent/mcp_game_server.py` does at the Playwright
layer.

Packet numbers: `packages/common/network/packets.ts`
Opcode numbers: `packages/common/network/opcodes.ts`
Enum constants:  `packages/common/network/modules.ts` (ContainerType)

Hardcoded values (readers: update the constants block if Kaetram renumbers):

    Packets
      Equipment = 8, Movement = 11, Target = 14, Container = 21,
      Quest = 23, Respawn = 32, Warp = 39, Store = 40,
      Crafting = 54, LootBag = 56

    Opcodes
      Equipment.Style = 3
      Movement.Request = 0, Movement.Stop = 3
      Target.Talk = 0, Target.Attack = 1, Target.Object = 3
      Container.Remove = 2, Container.Select = 3
      Store.Buy = 2
      Crafting.Craft = 2
      LootBag.Take = 1

    ContainerType.Inventory = 1
"""

from __future__ import annotations

from typing import Any

from playwright.async_api import Page

# Packet ids (kept as Python constants so Playwright evaluate blocks stay string-safe).
PACKETS = {
    "Equipment": 8,
    "Movement": 11,
    "Target": 14,
    "Container": 21,
    "Quest": 23,
    "Respawn": 32,
    "Warp": 39,
    "Store": 40,
    "Crafting": 54,
    "LootBag": 56,
}

OPCODES = {
    "EquipmentStyle": 3,
    "MovementRequest": 0,
    "MovementStop": 3,
    "TargetTalk": 0,
    "TargetAttack": 1,
    "TargetObject": 3,
    "ContainerRemove": 2,
    "ContainerSelect": 3,
    "StoreBuy": 2,
    "CraftingCraft": 2,
    "LootBagTake": 1,
}

CONTAINER_INVENTORY = 1


async def _send(page: Page, packet_id: int, payload: Any) -> None:
    await page.evaluate(
        "([id, body]) => window.game.socket.send(id, body)",
        [packet_id, payload],
    )


async def game_move_to(page: Page, x: int, y: int) -> dict[str, Any]:
    """Trigger the client's own pathing logic (mirrors clicking a tile)."""
    return await page.evaluate(
        """([x, y]) => {
            const player = window.game && window.game.player;
            if (!player) return { error: 'no-player' };
            if (typeof player.go !== 'function') return { error: 'no-go-method' };
            player.go(x, y);
            return { requested: { x, y } };
        }""",
        [x, y],
    )


async def game_cancel_nav(page: Page) -> None:
    await page.evaluate(
        """() => {
            const player = window.game && window.game.player;
            if (!player) return;
            try { if (typeof player.stop === 'function') player.stop(true); } catch (_e) {}
            player.moving = false;
            player.path = null;
        }"""
    )
    await _send(
        page,
        PACKETS["Movement"],
        {
            "opcode": OPCODES["MovementStop"],
            "playerX": None,
            "playerY": None,
            "targetInstance": "",
            "orientation": 1,
        },
    )


async def game_warp(page: Page, warp_id: int) -> None:
    await _send(page, PACKETS["Warp"], {"id": warp_id})


async def game_set_attack_style(page: Page, style: int) -> None:
    """`style` must be a `Modules.AttackStyle` numeric value."""
    await _send(
        page,
        PACKETS["Equipment"],
        {"opcode": OPCODES["EquipmentStyle"], "style": style},
    )


async def game_accept_quest(page: Page, key: str) -> None:
    await _send(page, PACKETS["Quest"], {"key": key})


async def game_buy_item(page: Page, store_key: str, index: int, count: int = 1) -> None:
    await _send(
        page,
        PACKETS["Store"],
        {
            "opcode": OPCODES["StoreBuy"],
            "key": store_key,
            "index": index,
            "count": count,
        },
    )


async def game_select_inventory(page: Page, index: int) -> None:
    """Server branches on item flags: edible → consume, equippable → equip."""
    await _send(
        page,
        PACKETS["Container"],
        {
            "opcode": OPCODES["ContainerSelect"],
            "type": CONTAINER_INVENTORY,
            "fromIndex": index,
        },
    )


async def game_drop_item(page: Page, index: int, count: int = 1) -> None:
    await _send(
        page,
        PACKETS["Container"],
        {
            "opcode": OPCODES["ContainerRemove"],
            "type": CONTAINER_INVENTORY,
            "fromIndex": index,
            "value": count,
        },
    )


async def game_respawn(page: Page) -> None:
    await _send(page, PACKETS["Respawn"], [])


async def game_attack(page: Page, instance: str) -> None:
    await _send(page, PACKETS["Target"], [OPCODES["TargetAttack"], instance])


async def game_talk_to(page: Page, instance: str) -> None:
    await _send(page, PACKETS["Target"], [OPCODES["TargetTalk"], instance])


async def game_target_object(page: Page, instance: str) -> None:
    await _send(page, PACKETS["Target"], [OPCODES["TargetObject"], instance])


async def game_take_lootbag(page: Page, index: int) -> None:
    await _send(
        page,
        PACKETS["LootBag"],
        {"opcode": OPCODES["LootBagTake"], "index": index},
    )


async def game_craft(page: Page, recipe_key: str, count: int = 1) -> None:
    await _send(
        page,
        PACKETS["Crafting"],
        {
            "opcode": OPCODES["CraftingCraft"],
            "key": recipe_key,
            "count": count,
        },
    )


async def game_clear_combat(page: Page) -> None:
    """Server has no dedicated packet; clear client-side combat state and let
    the server notice when the player moves away. Tests that need authoritative
    server-side clearing should combine this with movement to break attacker
    range."""
    await page.evaluate(
        """() => {
            const player = window.game && window.game.player;
            if (!player) return;
            try { if (player.combat && typeof player.combat.stop === 'function') player.combat.stop(true); } catch (_e) {}
            try { player.attackers = {}; } catch (_e) {}
            try { if (typeof player.removeTarget === 'function') player.removeTarget(); } catch (_e) {}
        }"""
    )


async def game_stuck_reset(page: Page) -> None:
    """Mirror `mcp_game_server.py`'s stuck_reset: reset the client's movement
    state and request the current tile (no-op move) to force the server to
    re-sync position."""
    await page.evaluate(
        """() => {
            const player = window.game && window.game.player;
            if (!player) return;
            player.moving = false;
            player.path = null;
            try { if (typeof player.stop === 'function') player.stop(true); } catch (_e) {}
        }"""
    )


async def nearest_entity_instance(
    page: Page,
    *,
    name_contains: str,
    entity_type: int = 3,
) -> str | None:
    """Find the nearest entity whose name contains `name_contains`. Type codes
    match `Modules.EntityType`: NPC=1, Item=2, Mob=3, Tree=10, Rock=11,
    Foraging=12, FishSpot=13."""
    return await page.evaluate(
        """({ nameContains, entityType }) => {
            const game = window.game;
            if (!game || !game.player || !game.entities) return null;
            const player = game.player;
            const needle = nameContains.toLowerCase();
            const hits = [];
            for (const [instance, entity] of Object.entries(game.entities.entities || {})) {
                if (entity.type !== entityType) continue;
                if ((entity.hitPoints || 0) <= 0 && entityType === 3) continue;
                const name = (entity.name || entity.key || '').toLowerCase();
                if (!name.includes(needle)) continue;
                const distance =
                    Math.abs((entity.gridX || 0) - (player.gridX || 0)) +
                    Math.abs((entity.gridY || 0) - (player.gridY || 0));
                hits.push({ instance, distance });
            }
            hits.sort((a, b) => a.distance - b.distance);
            return hits.length > 0 ? hits[0].instance : null;
        }""",
        {"nameContains": name_contains, "entityType": entity_type},
    )
