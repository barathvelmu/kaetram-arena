## CURRENT TREE TRUTHS

- Tutorial is auto-completed on load. The starter kit is already granted.
- **21/21 quests are source-completable.** Coder's Glitch chain + Evil Santa unblocked by recent data patches (missing items, `noc`→`npc` typo, stage-1 door, candykey source).
- Evil Santa stage 1 door was placed at (525, 340-345) based on the existing evilsanta dynamic area; **playtest-verify** that the door actually fires before relying on it.
- Start **Arts and Crafts** to unlock Crafting. Start **Scientist's Potion** to unlock Alchemy.
- Smithing, Smelting, and Cooking are always available on station click. Fletching requires a `knife` from Clerk.
- `undersea` access requires the `waterguardian` achievement. **Miner's Quest II** opens the mining cave. **Ancient Lands** opens the mountain gate. **Evil Santa** opens the ice world (`frozentundra`/`iceworld` achievements).
- Trust runtime truth over stale flavor text. Liar quests still active: `Foresting` (Rusted Axe → ironaxe), `Royal Drama`, `Rick's Roll`, `Sea Activities`, `Scientist's Potion`, `Arts and Crafts`.

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
| P1 | Anvil's Echoes | None | `smithingboots` + 420 Smithing XP | Talk to Blacksmith twice |
| P1 | Royal Drama | None | **10000 gold** | `royalguard2 -> ratnpc -> king2` |
| P1 | Royal Pet | **Royal Drama** complete | `catpet` (pet) | Deliver 3 books, then return to King |
| P1 | Sorcery and Stuff | None | `staff` + Sorcerer shop | Farm Hermit Crab Warrior at **(320, 455)** for guaranteed `bead`, turn in x3 |
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
| P4 | Evil Santa | Stage 1 door at **(525, 340-345)** — **verify door fires** before relying on it; need `candykey` (1.5% from `santaelf`) for stage 3 | Kill `santa` (L240) | Talk `snowshepherdboy` → walk door → `santaelfnpc` → farm candykey → kill Santa |
| P4 | The Coder's Glitch | None (typo fixed) | Quest completion | Talk `coder`, kill `skeletonking`, return `skeletonkingtalisman` |
| P4 | The Coder's Glitch II | **Coder's Glitch** complete | Quest completion | Kill `ogrelord` + `queenant` + `forestdragon`, return 3 talismans |
| P4 | Coder's Fallacy | **Coder's Glitch II** + Alchemy 35 + Smithing 45 | "Key to secret room" (flavor only, no item) | Single talk stage |

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

## KEY QUEST LOCATIONS

- **Hermit Crab Warrior** at **(320, 455)** — miniboss, 2550 HP, L35. Guaranteed `bead` drop via `warriorcrab` drop table. Sorcery quest's canonical bead farm; do NOT grind random unusual-table mobs for bead.
- **Ice Knight** at **(808, 813)** — drops `icesword` for Ancient Lands quest.
- **Strawberry drop rate** — ~10% per fruit-table kill (goblins, ogres, ants, hobgoblins) after recent patch. Plan ~20 kills for 2 strawberries.
- **candykey** — 1.5% drop from `santaelf` (needed for Evil Santa stage 3 door).

---

## GAME MECHANICS

- Attack styles: Hack = Str+Def, Chop = Acc+Def, Defensive = Def. All styles also give Health XP.
- `gather` handles trees, rocks, bushes, and fish spots. "No items gained" usually means low skill, wrong tool, or exhausted node.
- `string` = `bluelily` at Crafting Lv1.
- `clamchowder` = `clamobject + potato + bowlsmall` at Cooking Lv15. Fish clams at coastal `clamspot` nodes, not mob drops.
- Item drops despawn after 64s. Inventory has 25 slots.
