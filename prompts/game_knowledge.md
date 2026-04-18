## CURRENT TREE TRUTHS

- Tutorial is auto-completed on load. The starter kit is already granted.
- Full clean completion is impossible on this tree: **17 completable quests / 21 total**.
- Start **Arts and Crafts** to unlock Crafting. Start **Scientist's Potion** to unlock Alchemy.
- Smithing, Smelting, and Cooking are always available on station click. Fletching requires a `knife` from Clerk.
- `undersea` access requires the `waterguardian` achievement. **Miner's Quest II** opens the mining cave. **Ancient Lands** opens the mountain gate.
- Trust runtime truth over stale flavor text. High-impact liar quests: `Foresting`, `Anvil's Echoes`, `Royal Drama`, `Rick's Roll`, `Sea Activities`, `Scientist's Potion`, `Arts and Crafts`, `Clam Chowder`.

---

## MOB PROGRESSION

XP per kill scales with mob HP. Move up quickly once low mobs stop paying.

| Level | Best Target | HP | XP/kill | Location |
|-------|-------------|----|---------|----------|
| 1-5 | Rat | 20 | 40 | Mudwich (~150-200, 150-170) |
| 5-10 | Goblin | 90 | 180 | West of Mudwich (~190, 204) |
| 10-20 | Spooky Skeleton | 140 | 280 | Cave area (~110-200, 108-200) |
| 20-30 | Snow Rabbit | 340 | 680 | Snow zone (x:445-506, y:251-367) |
| 30-40 | Golem | 350 | 700 | Scattered (x:377-700, y:118-747) |
| 40-50 | Wolf | 350 | 700 | Snow approach (x:268-440, y:254-362) |
| 50-60 | Snow Wolf | 1185 | 2370 | Snow zone (x:451-785, y:251-767) |
| 60-80 | Dark Skeleton | 740 | 1480 | Widespread (x:38-1145, y:411-851) |
| 80+ | Frozen Bat | 825 | 1650 | Ice zone (x:549-701, y:282-367) |

Goblins past Lv20 give poor XP. Dark Skeletons are the main efficient late grind.

---

## ACHIEVEMENTS (NOT QUESTS)

These are NPC task chains or kill rewards. They do not show up in the quest panel.

| Achievement | NPC | Task | Reward |
|-------------|-----|------|--------|
| Rat Infestation | Forester (~216,114) | Kill 20 rats / ice rats | 369 Str XP |
| Boxing Man | Bike Lyson (~166,114) | Kill 25 sneks | **Run ability** + 2000 Str XP |
| Oh Crab! | Bubba (~121,231) | Kill 10 crabs / hermit crabs | 696 Acc XP |
| Zombie Lovers | Zombie Girlfriend (undersea) | Kill 20 zombies | 4269 Str XP |

Boss kills also grant achievements. Highest-value route kill: Water Guardian for `undersea`.

---

## QUEST CATALOG

Use exact quest names from this table when calling `query_quest(quest_name)`. Call it before any gated, multi-step, or expensive quest. It returns status, requirements, unlocks, reward caveats, walkthrough, and boss notes.

| Phase | Exact Quest Name | Gate / Caveat | Reward / Unlock | One-line action |
|-------|------------------|---------------|-----------------|-----------------|
| P1 | Tutorial | Auto-finished by runtime bypass | Starter kit already granted | Treat as finished; warp to Mudwich if spawned in tutorial area |
| P1 | Foresting | None | `ironaxe` | Turn in logs 10 + 10 to Forester |
| P1 | Desert Quest | None | Unlocks `crullfield` + `lakesworld` warps | Deliver `cd` to Wife, then return |
| P1 | Anvil's Echoes | None | `bronzeboots` | Talk to Blacksmith twice |
| P1 | Royal Drama | None | **10000 gold** | `royalguard2 -> ratnpc -> king2` |
| P1 | Royal Pet | **Royal Drama** complete; `catpet` reward is broken but completion counts | Quest completion | Deliver 3 books, then return to King |
| P1 | Sorcery and Stuff | `staff` reward is broken but completion counts | Quest completion + Sorcerer shop | Turn in `bead x3` |
| P1 | Rick's Roll | None | **1987 gold** | Fish/cook 5 shrimp, then deliver Rick's `seaweedroll` |
| P1 | Sea Activities | **`waterguardian` required for undersea access** | **10000 gold net** + sea quest gates | Sponge/Pickle talk chain, then kill `picklemob` |
| P2 | Scientist's Potion | None | **Alchemy unlock on start** | Talk once and accept |
| P2 | Arts and Crafts | None | **Crafting unlock on start** | `berylpendant -> bowlsmall -> stew` (`stew` needs `bowlmedium`) |
| P3 | Miner's Quest | Practical: Mining 1 + mining weapon | 2000 Mining XP | Turn in `nisocore x15` |
| P3 | Miner's Quest II | **Miner's Quest + Mining 30** | Opens mining cave | Turn in 5 `tinbar`, 5 `copperbar`, 5 `bronzebar` |
| P3 | Herbalist's Desperation | **Foraging 25** practical gate | `hotsauce` + 1500 Foraging XP | Turn in blue lily, then paprika + tomato |
| P3 | Scavenger | Real turn-in is tiny; ignore fake shopping-list dialogue | **7500 gold** | Turn in `tomato x2 + strawberry x2 + string x1` |
| P3 | Clam Chowder | Practical: Fishing 10 + Cooking 15 + Fletching 3 | **7500 gold** | Turn in 5 clams, then 2 chowders, then 2 more chowders |
| P4 | Ancient Lands | Need `icesword` from Ice Knight at **(808,813)** | `snowpotion` + mountain gate | Bring `icesword` to Ancient Monument |

### Skip / Blocked

| Quest | Why To Skip |
|-------|-------------|
| Evil Santa | Missing stage-1 door; ice world stays blocked |
| The Coder's Glitch | `noc` typo + missing `skeletonkingtalisman` item |
| The Coder's Glitch II | Required talismans do not exist |
| Coder's Fallacy | Blocked by `The Coder's Glitch II` prerequisite |

---

## STORES / WARPS

Use `buy_item(npc_name, item_index, count)`:
- **Babushka** (ingredients store): access via door at **(483,275)**. Items: 0=Blue Lily, 1=Tomato, 2-3=Mushroom, 4=Egg, 5=Corn, 6=Raw Pork, 7=Raw Chicken.
- **Miner** (~323,178): 0=Coal, 1=Copper Ore, 2=Tin Ore, 3=Bronze Ore, 4=Gold Ore
- **Forester** (~216,114): 0=Bronze Axe(1000g), 1=Iron Axe(5000g)
- **Clerk** (startshop, Mudwich): 0=Arrow, 1=Knife, 2=Flask, 3=Mana Flask, 4=Burger, 5=Big Flask

**Warps** - use `warp(location)`:
| Destination | warp() arg | Coords | Unlock |
|-------------|------------|--------|--------|
| Mudwich | `mudwich` | (188,157) | Always |
| Crullfield | `crullfield` | (266,158) | Finish Desert Quest |
| Lakesworld | `lakesworld` | (319,281) | Finish Desert Quest |
| Patsow | `patsow` | (343,127) | Enter Patsow area |
| Aynor | `aynor` | (411,288) | Finish Ancient Lands |
| Undersea | `undersea` | (43,313) | Kill Water Guardian |

---

## GAME MECHANICS

- Attack styles: Hack = Str+Def, Chop = Acc+Def, Defensive = Def. All styles also give Health XP.
- `gather` handles trees, rocks, bushes, and fish spots. "No items gained" usually means low skill, wrong tool, or exhausted node.
- `string` = `bluelily` at Crafting Lv1.
- `clamchowder` = `clamobject + potato + bowlsmall` at Cooking Lv15.
- Item drops despawn after 64s. Inventory has 25 slots.
