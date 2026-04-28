## PRIMARY OBJECTIVE

You are scored on the **CORE 5** quests. Finish them before anything else.
After the Core 5 are done, advance to EXTRA. Bonus quests are completable
add-ons; off-limits quests are broken — don't waste turns on them.

`interact_npc` only reads dialogue by default. To commit, call
`interact_npc(name, accept_quest_offer=True)` — and only after `query_quest`
returns `live_gate_status.gated: false` (see system.md Rule 10).

**Tier-A signals reminder.** Every `observe()` carries Tier-A signals: `live_gate_status` (per-quest blockers vs current state), `station_locations` (nearest crafting tile per skill), `mob_stats.level/aggressive` (mob threat). Read these and act on them — don't just verbalize.

## QUEST CATALOG

Single source of truth for every quest. Use the **Exact Quest Name** column
verbatim when calling `query_quest(quest_name)`. Ordering within a tier is
suggested play order. `query_quest` returns the full walkthrough, current
stage, items needed, and `live_gate_status` evaluated against your state.

| # | Tier | Exact Quest Name | NPC + coords | Gate / Prereq | Reward / Unlock | Why / one-line action |
|---|------|------------------|--------------|---------------|-----------------|------------------------|
| 1 | **CORE** | Foresting | Forester (216, 114) | None | `ironaxe` | Warmup — 10+10 oak logs, turn in twice |
| 2 | **CORE** | Herbalist's Desperation | Herby Mc. Herb (333, 281) | **None for acceptance** — Foraging 25 is a *progress* gate (paprika at stage 2), NOT an acceptance gate. Talk to Herby Mc. Herb at (333, 281) immediately on arriving at Lakesworld — Stage 0→1 has no skill requirement. | `hotsauce` + 1500 Foraging XP | Turn in blue lily, then paprika + tomato |
| 3 | **CORE** | Rick's Roll | Rick (1088, 833) | None | **1987 gold** | Fish + cook 5 shrimp, deliver `seaweedroll` |
| 4 | **CORE** | Arts and Crafts | Babushka (702, 608) | None | **Crafting unlock on start** | `berylpendant → bowlsmall → stew` (`stew` needs `bowlmedium`) |
| 5 | **CORE** | Sea Activities | Sponge (52, 310) | **`waterguardian` achievement** for undersea | **10000 gold** + sea quest gates | Sponge/Pickle talk chain, then kill `picklemob` |
| 6 | EXTRA | Desert Quest | Dying Soldier (288, 134) | None | Unlocks `crullfield` + `lakesworld` warps | Deliver `cd` to Wife, then return |
| 7 | EXTRA | Royal Drama | Royal Guard (282, 887) | None | **10000 gold** | `royalguard2 → ratnpc → king2` |
| 8 | EXTRA | Royal Pet | King (284, 884) | **Royal Drama** finished | `catpet` (pet) | Deliver 3 books, return to King |
| 9 | EXTRA | Scientist's Potion | Scientist (763, 666) | None | **Alchemy unlock on start** | Talk once and accept |
| 10 | EXTRA | Ancient Lands | Ancient Monument (415, 294) | `icesword` from Ice Knight (808, 813), L62 | `snowpotion` + mountain gate | Bring `icesword` to Monument |
| — | bonus | Anvil's Echoes | Blacksmith (~199, 169) | None | `bronzeboots` | Talk to Blacksmith twice |
| — | bonus | Scavenger | Village Girl (~136, 146) | None | **7500 gold** | Turn in `tomato x2 + strawberry x2 + string x1` |
| — | bonus | Clam Chowder | (coastal NPC) | Practical: Fishing 10 + Cooking 15 + Fletching 3 | **7500 gold** | 5 clams, then 2 chowders, then 2 more |
| — | bonus | Sorcery and Stuff | Sorcerer | None | `staff` (recently fixed — verify on completion) | Re-assemble lost staff; bead farm now placed |
| — | bonus | Evil Santa | Mountain Sherpa | None | (verify) — unlocks `iceworld` warp | Stage-1 door at (525, 340-345); `candykey` from `santaelf` ~1.5% |

### Off-limits — don't accept these (broken / impossible)

- **Miner's Quest / Miner's Quest II** — circular bug: the only 2 `nisocrock` placements are behind `reqQuest=minersquest2`, and MQ2 requires MQ. Not completable.
- **The Coder's Glitch / Glitch II / Coder's Fallacy** — missing talisman item definitions (`skeletonkingtalisman` etc.); chain blocked.

---

## CURRENT TREE TRUTHS

- Tutorial is auto-finished at spawn; starter kit (bronzeaxe, knife, fishingpole, coppersword, woodenbow) is already in your inventory. Ignore all tutorial dialogue / NPCs.
- 5 CORE + 5 EXTRA + 5 bonus = 15 completable quests; 5 are Off-limits (see table above).
- Start **Arts and Crafts** to unlock Crafting. Start **Scientist's Potion** to unlock Alchemy.
- Smithing, Smelting, and Cooking are always available on station click. Fletching requires a `knife` from Clerk.
- `undersea` access requires the `waterguardian` achievement (kill Water Guardian at (293, 729), L36, 350 HP). **Ancient Lands** (EXTRA #10) opens the mountain gate. **Evil Santa** (bonus) unlocks `iceworld`.
- Liar quests (in-game reward strings disagree with what you actually receive — trust `query_quest`'s `actual_rewards`): `Foresting` (Rusted Axe → ironaxe), `Royal Drama`, `Rick's Roll`, `Sea Activities`, `Scientist's Potion`, `Arts and Crafts`, `Herbalist's Desperation` (Mystical Potion → hotsauce + 1500 Foraging XP), `Anvil's Echoes` (Smithing Gloves → bronzeboots only), `Scavenger` (fake shopping list), `Clam Chowder` (fish clams, don't kill them).

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

## RESOURCE LOCATIONS

Coords are first valid placement in `world.json` — useful as a `navigate(x,y)` target. Stand on a walkable shore/adjacent tile, then `gather(name)`.

| Resource | Skill | Lvl gate | Coords (cluster) | Used by |
|---|---|---|---|---|
| Oak Tree | Lumberjacking | 1 | Mudwich north (~210, 110) | Foresting |
| Blueberry Bush | Foraging | 1 | Mudwich (105–238, 103–209), e.g. (155, 103) | Foraging grind |
| Blue Lily Bush | Foraging | 10 | (278–441, 250–363), e.g. (278, 250) | Arts and Crafts (`string`) |
| Tomato Bush | Foraging | 15 | (113–386, 107–326), e.g. (220, 108) | Herbalist's, Scavenger |
| Paprika Bush | Foraging | 25 | (286–390, 240–484), e.g. (298, 301) | Herbalist's |
| Strawberry Bush | Foraging | 1 | various | Scavenger (bonus) |
| Beryl Rock | Mining | 1 + **pickaxe** | Babushka mine (643–665, 643–656), e.g. (645, 643) | Arts and Crafts |
| Copper Rock | Mining | 1 | (639–670, 634–651) | Smithing |
| Tin Rock | Mining | 1 | (638–666, 628–649) | Smithing |
| Coal Rock | Mining | 1 | (645–668, 627–649) | Smelting |
| Iron Rock | Mining | 15 | (642–646, 588–598) | Smithing |
| Gold Rock | Mining | 40 | (655–665, 639–654) | Smithing |
| Shrimp Fishing Spot | Fishing | 1 + **fishingpole** | (269–383, 328–397), e.g. (325, 360) shore at (324, 360) | Rick's Roll |
| Tuna Fishing Spot | Fishing | 25 | (269–376, 296–402) | — |
| Clam Spot | Fishing | 5 + **fishingpole** | (268–381, 253–398), e.g. (322, 318) | Clam Chowder (bonus) |

⚠️ Spots **in water** require approach from a shore tile, not standing on the spot. If `gather` reports "No resource matching X nearby" but you're at the listed coords, you're probably on the wrong tile — `observe` to see `nearby_entities` and pick the spot with `kind: rock|fish|tree|forage`.

⚠️ Mining beryl needs a **pickaxe** (bronzepickaxe minimum). The starter `bronzeaxe` does NOT mine rocks.

## SKILL PROGRESSION STRATEGY

The skill XP table is steep. Estimated `gather`/`attack` count to hit each level. **XP per gather (verified vs `Kaetram-Open/packages/server/data/foraging.json` + formula in `packages/server/src/info/loader.ts:44-50`):** blueberry 10, corn 15, bluelily 20, tomato 25, paprika 50.

| Skill gate | XP needed | Suggested grind |
|---|---|---|
| Foraging 10 | 1,151 | ~115 blueberry gathers @ 10 XP each, OR ~58 corn @ 20 XP (Mudwich) |
| Foraging 15 | 2,406 | ~241 blueberry, OR switch to bluelily/corn at L10 for ~70 mixed |
| Foraging 25 | 7,833 | ~873 total blueberry gathers, OR optimal hybrid path (~70 blueberry → L10, ~140 bluelily → L15, ~250 tomato → L25 ≈ 460 mixed gathers) |
| Mining 15 | 2,740 | ~140 copper/tin/coal at Lv1+ |
| Fishing 5 | 511 | ~25 shrimp |

For Herbalist's Desperation specifically: pick blue lily early (Lv10 gate), grind to 15 on tomato (better XP/action than blueberry once unlocked), grind to 25 on paprika (highest XP/action of the three). When calling `gather`, the `resource_name` is `Tomato Plant Thingy` (not `Tomato`) and `Paprika Bush` — match `foraging.json` keys exactly.

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

## STORES / WARPS

Use `buy_item(npc_name, item_index, count)`:
- **Babushka** (ingredients store): access via door at **(483,275)**. Items: 0=Blue Lily, 1=Tomato, 2-3=Mushroom, 4=Egg, 5=Corn, 6=Raw Pork, 7=Raw Chicken. ⚠️ Store is unavailable while `Arts and Crafts` is active — Babushka's NPC slot is claimed by quest dialogue. Gather ingredients from the world instead, or finish the quest first. Babushka also sells bluelily (item_index=0) — but Babushka is gated behind Ancient Lands quest (which itself needs the Aynor warp + a door at 463). For Herbalist, gathering is faster than waiting for Ancient Lands.
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

- **Ice Knight** at **(808, 813)** — drops `icesword` for Ancient Lands quest (L62, verified live placement).
- **Strawberry drop rate** — ~8% per fruit-table kill (goblins, ogres, ants, bosses). Plan ~25 kills for 2 strawberries. Only relevant for bonus `Scavenger` quest.
- **Rick's Roll is L1-safe end-to-end.** The shrimp fishing spots near (325, 360) require Fishing 1 only (no level gate, no aggressive mobs in 8-tile radius). The corridor Mudwich (188,157) → door 1025 (379, 388) → Rick (1088, 833) passes through Mudwich outskirts and farmland — no aggressive mobs above L7. Door 1025 is unguarded. Do NOT classify Rick's Roll as 'unreachable' based on travel distance.
- **Rick's Roll route pin-chain.** Long-distance navigation cap is ~100 tiles per `navigate(x,y)` call. Recommended pin chain: (245,170) → (285,190) → (293,242) → (311,254) → (324,301) → (340,345) → (367,348) → (375,370) → traverse_door(379,388 → 1138,800) → navigate(1088,833). Each leg under 100 tiles.

---

## GAME MECHANICS

- Attack styles: Hack = Str+Def, Chop = Acc+Def, Defensive = Def. All styles also give Health XP.
- `string` = `bluelily` at Crafting Lv1.
- `clamchowder` = `clamobject + potato + bowlsmall` at Cooking Lv15. Fish clams at coastal `clamspot` nodes, not mob drops.
- Item drops despawn after 64s. Inventory has 25 slots.
