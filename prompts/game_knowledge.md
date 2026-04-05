## ALL QUESTS (21 total — complete them all)

### Quick Reference

| Quest | NPC (location) | Action | Prereqs | Reward |
|-------|---------------|--------|---------|--------|
| Foresting | Forester (~216,114) | Deliver 20 logs (2×10) | None | Iron Axe |
| Anvil's Echoes | Blacksmith (~199,169) | Talk twice | None | Smithing Boots + Smithing skill |
| Desert Quest | Dying Soldier (~288,134) | Deliver CD to Wife via door (310,264), return | None | Unlocks Crullfeld+Lakesworld warps |
| Scavenger | Village Girl (~136,146) → Old Lady via door (147,113) | Deliver 2 tomato + 2 strawberry + 1 string | None | 7500 gold |
| Sorcery | Sorcerer via door (~194,218) | Deliver 3 beads from Warrior Crabs in cave | None | Magic Staff |
| Miner's Quest | Miner (~323,178) | Deliver 15 nisoc ore | None | Miner store + 2000 Mining XP |
| Herbalist | Herbalist (~333,281) in Lakesworld | Stage 1: 3 blue lilies. Stage 2: 2 paprika + 2 tomato | None | Hot Sauce + 1500 Foraging XP |
| Rick's Roll | Rick (beach area) | Deliver 5 cooked shrimp, then seaweed roll to GF via door | None | 1987 gold |
| Arts & Crafts | Cold NPC (mountain) | Craft: beryl pendant → small bowl → stew | None | Crafting bench access |
| Royal Drama | Royal Guard 2 (castle) | Talk to guard → rat in sewers → find king | None | 10000 gold |
| Royal Pet | King (castle, after Royal Drama) | Deliver 3 books to NPCs across map | Royal Drama | Cat Pet |
| Ancient Lands | Monument NPC (icy cave) | Find Ice Sword from Ice Knight (L62) | None | Snow Potion |
| Sea Activities | Sponge (underwater, beach entrance) | Talk chain → kill Sea Cucumber (L88, 1250 HP) | None | 10000 gold |
| Evil Santa | Snow Shepherd Boy (mountain) | Find key → kill Evil Santa (L240, 7500 HP) | None | Ice World access |
| Clam Chowder | Blue Bikini Girl (ice area) | 5 clam meat + 4 clam chowders (Fishing 10, Cooking 15) | None | 7500 gold |
| Coder's Glitch | Coder NPC | Kill Skeleton King (L32, 1850 HP) | Foresting+Desert+Sorcery; Acc15/Str20/Def15 | Club + 5000 Str XP |
| Miner's Quest II | Miner (~323,178) | Deliver 5 tin + 5 copper + 5 bronze bars (smelting) | Miner's Quest; Mining 30 | Mining cave |
| Coder's Glitch II | Coder NPC | Kill 3 bosses: Ogre Lord→Queen Ant→Forest Dragon | CG1+Miner's+Scavenger; Acc25/Str40/Def30 | Shield + XP |
| Scientist's Potion | Scientist (Mudwich house) | Talk (STUB — only stage 0) | None | — |
| Coder's Fallacy | Villager 4 | Talk (STUB — only stage 0) | CG2+Anvil+Scientist; Alch35/Smith45 | — |

### Quest Dependency Chain

```
Foresting ──► Desert Quest ──► Sorcery ──┐
                                          ├──► Coder's Glitch (Acc15/Str20/Def15)
Scavenger ────────────────────────────────┤
Miner's Quest ────────────────────────────┤
                                          └──► Coder's Glitch II (Acc25/Str40/Def30)
                                                    └──► Coder's Fallacy (STUB)
Royal Drama ──► Royal Pet
Miner's Quest ──► Miner's Quest II (Mining 30)
```

### Detailed Walkthroughs

**Scavenger** — Items needed:
- Tomato (2): Forage from Tomato Plant Thingy (type 12, Foraging level 15) — use `gather("Tomato")`. Also ~5% mob drop from vegetables table.
- Strawberry (2): ~8% mob drop from "fruits" loot table. Kill Goblins near (~190,204). Auto-collected when walking over single drops.
- String (1): Very common mob drop (~12% from ordinary table). Also craftable: 1 Blue Lily → 1 String (Crafting level 1).

**Sorcery** — Cave navigation (short hops!):
- Enter Crab Cave door at (154,231) → teleports to (234,662). Requires Crab Problem achievement (kill 10 crabs first).
- Navigate in 30-tile hops: (234,662) → (260,640) → (280,600) → (300,550) → (310,500) → Warrior Crab (~320,455).
- Warrior Crab: L30, 300 HP. Drops bead at 100% rate. Kill 3, collect beads via `loot()`.

**Coder's Glitch** — MAIN STORYLINE:
- Prereqs: Complete Foresting + Desert Quest + Sorcery. Need Accuracy 15, Strength 20, Defense 15.
- Go to Coder NPC. He sends you to kill the Skeleton King in Patsow (east of desert, volcano region).
- Skeleton King: L32, 1850 HP, melee slash. Drops Skeleton King Talisman at 100% ONLY when quest active.
- Return talisman to Coder. Reward: Club weapon + 5000 Strength XP.

**Royal Drama** — Easy 10K gold:
- Talk to Royal Guard 2 at castle. Enter sewers. Talk to Rat NPC. Go deeper. Find King 2. Done.

### Gathering & Skills

**How to gather**: Use `gather(resource_name)` on nearby resource entities. Finds closest matching resource, walks to it, harvests automatically.
- Foraging (bushes, type 12): No tool needed. Blueberry=L1, Corn=L5, Blue Lily=L10, Tomato=L15, Paprika=L25.
- Lumberjacking (trees, type 10): Need axe equipped (you have Bronze Axe). Oak=L1 → Logs.
- Mining (rocks, type 11): Need pickaxe equipped. Nisoc/Coal/Copper/Tin=L1. Smith Bronze Pickaxe: 3 bronze bars + 1 logs at anvil (Smithing L1, requires Anvil's Echoes completed).
- Fishing (fish spots, type 13): Need fishing rod equipped.

**How to loot**: After killing a mob, use `loot()` to pick up nearby dropped items.

**Key crafting recipes**:
- String: 1 Blue Lily → 1 String (Crafting L1)
- Cooked Shrimp: 1 Raw Shrimp at cooking pot (Cooking L1)
- Sticks: 1 Log → 4 Sticks (Fletching L1)
- Small Bowl: 2 Sticks (Fletching L3)
- Bronze Pickaxe: 3 Bronze Bars + 1 Logs at anvil (Smithing L1)

**Equipment progression**: Bronze Axe → Iron Axe (Foresting reward, needs Str 10) → equip quest rewards immediately. After Anvil's Echoes, you can smith better weapons at the anvil.

### Key Locations

**Mudwich** (~188,157) — main hub. Blacksmith: ~199,169 | Village Girl: ~136,146 | Forester: ~216,114

**Door Portals** (walk onto tile to teleport):
- (147,113) → Old Lady — Scavenger
- (154,231) → Crab Cave — Sorcery (needs Crab Problem achievement)
- (194,218) → Sorcerer
- (201,168) → Anvil cave (not needed — quest is talk-only)
- (310,264) → Wife — Desert Quest stage 1

**Warps**: Mudwich (always), Crullfeld (after Desert Quest), Lakesworld (after Desert Quest)

**Danger Zones**: Beach corridor x=105-115 — approach Bubba from NORTH along x=121.

### Game Mechanics

- Attack styles: Hack (Str+Def), Chop (Acc+Def), Defensive (Def). All give Health XP.
- Iron Axe needs Strength 10. Grind Hack style to build Strength.
- Achievements (Snek Problem, Crab Problem) auto-track kills. Return to NPC after count reached.
- Doors: navigate directly onto the door tile coordinate to teleport.
- Gathering: click resource → auto-harvests → item goes to inventory. Trees respawn 25s, rocks 30s.
