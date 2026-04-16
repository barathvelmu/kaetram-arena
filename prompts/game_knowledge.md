## MOB PROGRESSION

XP per kill ≈ mob HP × 2. Switch tiers when the current tier drops below 10% of your XP per turn.

| Level | Best target | HP | XP/kill | Location |
|-------|-------------|-----|---------|----------|
| 1-5 | Rat | 20 | 40 | Mudwich (~150-200, 150-170) |
| 5-10 | Goblin | 90 | 180 | West of Mudwich (~190, 204) |
| 10-20 | Spooky Skeleton | 140 | 280 | Cave (~110-200, 108-200) |
| 20-30 | Snow Rabbit | 340 | 680 | Snow zone (x:445-506, y:251-367) |
| 30-40 | Golem | 350 | 700 | Scattered (x:377-700, y:118-747) |
| 40-50 | Wolf | 350 | 700 | Snow approach (x:268-440, y:254-362) |
| 50-60 | Snow Wolf | 1185 | 2370 | Snow zone (x:451-785, y:251-767) |
| 60-80 | Dark Skeleton | 740 | 1480 | Widespread (x:38-1145, y:411-851) |
| 80+ | Frozen Bat | 825 | 1650 | Ice zone (x:549-701, y:282-367) |

Goblins past Lv 20 yield <10% of a tier-appropriate mob's XP — move on.

---

## QUESTS vs ACHIEVEMENTS

Some NPCs hand out **achievements**, not quests. Achievements track kills and grant rewards but do not appear in the quest panel.

### Achievements (NPC tasks — not quests)

| Achievement | NPC | Task | Reward |
|-------------|-----|------|--------|
| Rat Infestation | (auto) | Kill 20 rats | 369 Str XP |
| Boxing Man | Bike Lyson (~166, 114) | Kill 25 sneks | Run ability + 2000 Str XP |
| Oh Crab! | Bubba (~121, 231) | Kill 10 crabs / hermit crabs | 696 Acc XP |
| Zombie Lovers | (auto) | Kill 20 zombies | 4269 Str XP |

Bosses also grant achievements on kill (Water Guardian Lv 36, Skeleton King Lv 32, Ogre Lord Lv 44, Queen Ant Lv 94, Forest Dragon Lv 258). Use `query_quest` for details.

---

## STARTER QUESTS (Mudwich)

| Quest | NPC (location) | Action | Reward |
|-------|---------------|--------|--------|
| Foresting | Forester (~216, 114) | Deliver 20 logs (2 × 10) | Rusted Axe (item key `ironaxe`) + Forester shop access |
| Anvil's Echoes | Blacksmith (~199, 169) | Talk twice | Bronze Boots + 420 Smithing XP |
| Desert Quest | Dying Soldier (~288, 134) | Deliver CD to Wife via door (310, 264), return | Unlocks Crullfield + Lakesworld warps |
| Scavenger | Village Girl (~136, 146) → Old Lady via door (147, 113) | 2 tomato + 2 strawberry + 1 string | 7500 gold |

Later quests (call `query_quest(name)` for full walkthroughs): Sorcery, Miner's Quest, Herbalist, Royal Drama, Scientist's Potion, Coder's Glitch. More unlock as you level.

---

## KEY LOCATIONS

**Mudwich** (~188, 157) — main hub.
- Blacksmith: ~199, 169
- Village Girl: ~136, 146
- Forester: ~216, 114

### NPC shops (use `buy_item(npc_name, item_index, quantity)`)

- **Babushka** (ingredients) — at (702, 608), reached via door at **(483, 275)**. Items: 0 Blue Lily, 1 Tomato, 2-3 Mushrooms, 4 Egg, 5 Corn, 6 Raw Pork, 7 Raw Chicken. No strawberries here.
- **Miner** (~323, 178) — 0 Coal (50g), 1 Copper Ore (150g), 2 Tin Ore (150g), 3 Bronze Ore (200g), 4 Gold Ore (500g).
- **Forester** (~216, 114) — 0 Bronze Axe (1000g), 1 Iron Axe (5000g).
- **Clerk** (Mudwich `startshop`) — 0 Arrow (5g), 1 Knife (500g), 2 Flask (100g), 3 Mana Flask (85g), 4 Burger (450g), 5 Big Flask (550g).

### Door portals (step on tile to teleport — shown as `D` on the ASCII map)

- (147, 113) → Old Lady (Scavenger delivery)
- (154, 231) / (158, 232) → Crab Cave interior (234, 662)
- (194, 218) → Sorcerer (Sorcery quest NPC)
- (201, 168) → Anvil cave (requires Anvil's Echoes started)
- (310, 264) → Wife (Desert Quest stage 1)
- (483, 275) → Babushka's building (702, 608) — ingredients shop

### Warps (`warp(location)`)

| Destination | arg | Coords | Unlock |
|-------------|-----|--------|--------|
| Mudwich | `mudwich` | (188, 157) | Always |
| Crullfield | `crullfield` | (266, 158) | Complete Desert Quest |
| Lakesworld | `lakesworld` | (319, 281) | Complete Desert Quest |
| Patsow | `patsow` | (343, 127) | Enter Patsow area (auto-achievement) |
| Aynor | `aynor` | (411, 288) | Complete Ancient Lands |
| Undersea | `undersea` | (43, 313) | Kill Water Guardian (achievement) |

---

## GAME MECHANICS

- **Attack styles.** Hack (Str + Def), Chop (Acc + Def), Defensive (Def). All styles also grant Health XP.
- **Doors.** Walk onto the `D` tile to teleport. Some doors are gated by quest state.
- **Ground loot.** Item drops despawn after 64 seconds — `loot()` promptly after kills.
- **Inventory.** 25 slots. At 23+ full, drop low-value items so quest rewards aren't refused.
- **Resource respawn.** Trees 25s, rocks 30s. Skip depleted nodes.
