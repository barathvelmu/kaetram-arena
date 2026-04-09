## MOB PROGRESSION

XP per kill = mob HP × 2. Transition to harder mobs as you level up.

| Level | Best Target | HP | XP/kill | Location |
|-------|-------------|-----|---------|----------|
| 1-5 | Rat | 20 | 40 | Mudwich (~150-200, 150-170) |
| 5-10 | Goblin | 90 | 180 | West of Mudwich (~190, 204) |
| 10-20 | Spooky Skeleton | 140 | 280 | Cave area (~110-200, 108-200) |
| 20-30 | Snow Rabbit | 340 | 680 | Snow zone (x:445-506, y:251-367) |
| 30-40 | Golem | 350 | 700 | Scattered (x:377-700, y:118-747) |
| 40-50 | Wolf | 350 | 700 | Snow approach (x:268-440, y:254-362) |
| 50-60 | Snow Wolf | 1185 | 2370 | Snow zone (x:451-785, y:251-767) |
| 60-80 | Dark Skeleton | 740 | 1480 | Widespread (x:38-1145, y:411-851) |
| 80+ | Frozen Bat | 825 | 1650 | Ice zone (x:549-701, y:282-367) |

Goblins past L20 give negligible XP. At L60, Dark Skeletons are 8× more efficient.

---

## QUESTS vs ACHIEVEMENTS (important distinction!)

Some NPCs give **achievements**, not quests. Achievements track kills and give rewards but are NOT listed in the quest panel.

### Achievements (NPC task — NOT quests)

| Achievement | NPC | Task | Reward |
|-------------|-----|------|--------|
| Rat Infestation | (auto) | Kill 20 rats | 369 Str XP |
| Boxing Man | Bike Lyson (~166,114) | Kill 25 sneks | **Run ability** + 2000 Str XP |
| Oh Crab! | Bubba (~121,231) | Kill 10 crabs/hermit crabs | 696 Acc XP |
| Zombie Lovers | (auto) | Kill 20 zombies | 4269 Str XP |

Bosses also grant achievements on kill (Water Guardian L36, Skeleton King L32, Ogre Lord L44, Queen Ant L94, Forest Dragon L258). Use `query_quest` for details.

---

## QUESTS

| Quest | NPC (location) | Action | Prereqs | Reward |
|-------|---------------|--------|---------|--------|
| Foresting | Forester (~216,114) | Deliver 20 logs (2×10) | None | Iron Axe |
| Anvil's Echoes | Blacksmith (~199,169) | Talk twice | None | Smithing Boots + 420 Smithing XP |
| Desert Quest | Dying Soldier (~288,134) | Deliver CD to Wife via door (310,264), return | None | Unlocks Crullfield+Lakesworld warps |
| Scavenger | Village Girl (~136,146) → Old Lady via door (147,113) | Deliver 2 tomato + 2 strawberry + 1 string | None | 7500 gold |
| Sorcery | Sorcerer via door (~194,218) | Deliver 3 beads from Warrior Crabs | None | Magic Staff |
| Miner's Quest | Miner (~323,178) | Deliver 15 nisoc ore | None | Miner store + 2000 Mining XP |
| Herbalist | Herby Mc. Herb (~333,281) in Lakesworld | Stage 1: 3 blue lilies. Stage 2: 2 paprika + 2 tomato | None | Hot Sauce + 1500 Foraging XP |
| Royal Drama | Royal Guard 2 (~282,887) | Talk chain (guard → rat → king) | None | 10000 gold |
| Scientist's Potion | Scientist (~763,666) | Talk (1 stage) | None | 2000 Alchemy XP |
| Coder's Glitch | Programmer (~331,890) | Kill Skeleton King (L32, 1850 HP) | Foresting+Desert+Sorcery; Acc15/Str20/Def15 | Club + 5000 Str XP |

More quests available at higher levels — use `query_quest` to discover them.

Use `query_quest(quest_name)` for detailed step-by-step walkthroughs of any quest.

---

## KEY LOCATIONS

**Mudwich** (~188,157) — main hub. Blacksmith: ~199,169 | Village Girl: ~136,146 | Forester: ~216,114

**NPC Stores** — use `buy_item(npc_name, item_index)` to purchase:
- **Babushka** (ingredients store): At (702,608), access via door at **(483,275)**. Items: 0=Blue Lily, 1=Tomato, 2-3=Mushrooms, 4=Egg, 5=Corn, 6=Raw Pork, 7=Raw Chicken. Does NOT sell strawberries.
- **Miner** (~323,178): 0=Coal(50g), 1=Copper Ore(150g), 2=Tin Ore(150g), 3=Bronze Ore(200g), 4=Gold Ore(500g)
- **Forester** (~216,114): 0=Bronze Axe(1000g), 1=Iron Axe(5000g)
- **Clerk** (startshop, Mudwich): 0=Arrow(5g), 1=Knife(500g), 2=Flask(100g), 3=Mana Flask(85g), 4=Burger(450g), 5=Big Flask(550g)

**Door Portals** (walk onto tile to teleport — shown as 'D' on ASCII map):
- (147,113) → Old Lady — Scavenger delivery
- (154,231) or (158,232) → Crab Cave interior (234,662)
- (194,218) → Sorcerer — Sorcery quest NPC
- (201,168) → Anvil cave (requires Anvil's Echoes started)
- (310,264) → Wife — Desert Quest stage 1
- (483,275) → Babushka's building (702,608) — ingredients store

**Warps** — use `warp(location)` to fast travel:
| Destination | warp() arg | Coords | Unlock |
|-------------|-----------|--------|--------|
| Mudwich | `mudwich` | (188,157) | Always available |
| Crullfield | `crullfield` | (266,158) | Complete Desert Quest |
| Lakesworld | `lakesworld` | (319,281) | Complete Desert Quest |
| Patsow | `patsow` | (343,127) | Enter Patsow area (auto-achievement) |
| Aynor | `aynor` | (411,288) | Complete Ancient Lands quest |
| Undersea | `undersea` | (43,313) | Kill Water Guardian (achievement) |

---

## GAME MECHANICS

- Attack styles: Hack (Str+Def), Chop (Acc+Def), Defensive (Def). All give Health XP.
- Doors: walk onto the door tile to teleport ('D' on ASCII map).
- Item drops despawn after 64 seconds — loot promptly.
- Inventory: 25 slots. Drop unwanted items with drop_item to make space.
