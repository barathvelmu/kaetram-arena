## GAME KNOWLEDGE (Reference)

Factual reference data about the game world. Use this to navigate, plan, and make informed decisions.

---

### MOB REFERENCE

| Mob | HP | ~XP | Location | Good for |
|-----|----|----|----------|----------|
| Rat | 20 | 40 | Near Mudwich | Levels 1-5 |
| Crab | 15 | varies | Beach y=210-230 | Levels 1-10 (many spawns) |
| Batterfly | 65 | 130 | Fields around Mudwich | Levels 5-15 |
| Snek | 85 | 170 | East across bridge x≈220-240, y≈160 | Levels 10-20 |
| Goblin | 90 | 180 | West of village | Levels 15+ |
| Orc / Scorpion / Cobra / Vulture | varies | varies | Desert zone x≈236-319, y≈96-191 | Levels 20-40 |
| Ogre | varies | varies | Patsow plateau x≈321-400 | Levels 40-60 |
| Zombie | varies | varies | Underwater cave | Levels 30-50 |
| Skeleton Pirate | varies | varies | Underwater pirate area | Levels 40-60 |
| Jellyfish | varies | varies | Underwater | Levels 30-50 |

### BOSS REFERENCE

| Boss | HP | Level | Location | Gate |
|------|-----|-------|----------|------|
| Water Guardian | varies | varies | Beach cave underwater | None (miniboss) |
| Ogre Guardian | varies | varies | Patsow | ogreguardian achievement |
| Iron Ogre | varies | varies | Deep Patsow | pathofdeath achievement |
| Baby Sea Dragon | varies | varies | Deep underwater | waterguardian achievement |
| Skeleton King | 1,850 | 75 | Patsow boss area | ogrelord path |
| Ogre Lord | 2,850 | 100 | Deep Patsow | pathofdeath door |
| Hellhound | 4,550 | 185 | Hell dungeon | roadtohell achievement |
| Mermaid | varies | varies | Underwater domain | mermaidguard achievement |
| Queen Ant | varies | 195 | Endgame zone | ogrelord door |
| Forest Dragon | 6,942 | 258 | Under Mudwich | queenant door |
| Santa | 7,500 | 240 | Ice factory | evilsanta quest stage 3 |
| Wind Guardian | 15,000 | 195 | Late-game area | Achievement gated |
| Fulgur (Sky Dinosaur) | 50,000 | 435 | Endgame zone | Unknown |

---

### QUEST REFERENCE

#### Starter Quests (no prerequisites, all from Mudwich)

**Foresting** — Forester (~216, 114)
- Stage 1: Bring 10 logs (chop Oak trees — click tree, wait 5s for chop animation)
- Stage 2: Bring 10 more logs
- Reward: Iron Axe, unlocks Forester shop
- TIP: Oak trees abundant north of Mudwich near Forester

**Miner's Quest** — Miner (~323, 178)
- Deliver 15 nisoc ores (mine ore rocks — click rock, wait 5s)
- Reward: 2000 Mining XP, unlocks Miner shop
- Leads to: Miner's Quest II (requires Mining 30)

**Sorcery and Stuff** — Sorcerer (in village)
- Deliver 3 magic beads
- Reward: Magic Staff, unlocks Sorcerer shop

**Scavenger** — Village Girl 2 (~136, 146) → Old Lady
- Stage 1: Find Grandma (inside a village house near Village Girl)
- Stage 2-3: Gather tomatoes, strawberries, string
- Reward: 7500 gold

**Royal Drama** — Royal Guard 2 → Rat → King 2
- Find the missing king in the sewers
- Reward: 10,000 gold, King appears in village

**Anvil's Echoes** — Blacksmith (~199, 169)
- Find his lost hammer (explore south coast y > 200, cave entrances)
- Return hammer to Blacksmith
- Reward: Smithing Boots, unlocks Smithing skill + blacksmith workshop door

**Herbalist's Desperation** — Herbalist (in village)
- Gather lilies, tomatoes, paprika
- Reward: Hot Sauce, 1500 Foraging XP

**Rick's Roll** — Rick → Rickgf (Lena)
- Cook shrimps, make seaweed roll, deliver to girlfriend
- Reward: 1987 gold

**Desert Quest** — Dying Soldier (~288, 134, lava area)
- Get CD from soldier, deliver to Wife in tent east of desert (~260, 229)
- Reward: **Unlocks Lakesworld and Crullfield warps** (critical progression)

**Scientist's Potion** — Scientist (in village)
- Talk to him. STUB quest — only 1 stage, incomplete.

**Clam Chowder** — Pretzel (ice area) → Doctor → Old Lady 2
- Make clam chowder, find missing grandmother
- Reward: 7500 gold

**Sea Activities** — Sponge → Sea Cucumber
- Recover stolen money, fight the Sea Cucumber mob
- Reward: 10,000 gold

**Evil Santa** — Sherpa → Santa's Elf
- Infiltrate Santa's factory, kill Santa (7500 HP boss)
- Reward: Unlocks ice world access

**Ancient Lands** — Ancient Monument
- Bring an ice sword to the monument
- Reward: Snow Potion, unlocks mountain passage + Aynor warp

**Arts and Crafts** — Babushka (in village)
- Craft a pendant, bowl, and stew
- Reward: Access to crafting benches

#### Main Quest Chain (prerequisite-gated)

**Coder's Glitch** — The Coder (tutorial area NPC, reachable later)
- Prerequisites: Complete foresting + desertquest + sorcery | Accuracy 15, Strength 20, Defense 15
- Kill Skeleton King (1850 HP, Patsow boss), return talisman
- Reward: 5000 Strength XP + Club weapon

**Coder's Glitch II** — The Coder
- Prerequisites: Complete codersglitch + minersquest + scavenger | Accuracy 25, Strength 40, Defense 30
- Kill Ogre Lord (2850 HP) → Queen Ant → Forest Dragon (6942 HP) — sequential
- Reward: 7500 Accuracy + 4500 Strength + 3000 Defense XP + Iron Round Shield

**Coder's Fallacy** — UNFINISHED (1 empty stage, stub)
- Blocked: scientistspotion is itself a stub, making this unreachable

---

### GAME MECHANICS

#### Gathering (Lumberjacking, Mining, Fishing, Foraging)
- **Click resource node → character auto-harvests in 1-second loops**
- Each loop: `random(0, weapon_level + skill_level) > node_difficulty` = success
- Success: gain 1 item + skill XP. Failure: retry next loop automatically.
- **Trees respawn in 25s**, rocks in 30s, fishing spots don't deplete.
- Higher-tier tools (Iron Axe > Bronze Axe) increase weapon_level bonus = higher success rate.
- You MUST have the right tool equipped: axe for trees, pickaxe for rocks, fishing pole for fish.

#### Crafting
- **Click crafting station** (anvil, cooking pot, alchemy bench, etc.) → opens recipe interface
- Select recipe → click craft. Requires skill level + ingredients. **No failure** — if you meet requirements, it succeeds.
- Crafting stations are inside buildings in Mudwich village.
- Key chains: Ore → Bar (smelting) → Weapon/Armor (smithing). Raw fish → Cooked fish (cooking). Ingredients → Potions (alchemy).

#### Combat
- **Click mob → character auto-walks and auto-attacks**. Combat is passive — no dodging or active abilities needed.
- Damage = function of (attack skill + equipment bonuses) vs (defender's defense + armor). All hits connect, damage varies from 0 to max.
- Attack style determines which skills gain XP: Hack=6 (Str+Def), Chop=7 (Acc+Def), Defensive=3 (Def only).
- **Wait 5-8s after clicking a mob** — don't re-click during combat (interrupts the attack loop).

#### Equipment Requirements
- Iron Axe: Strength 10
- Higher-tier weapons scale similarly — check skill requirements before equipping.
- Equipment comes from quest rewards, mob drops, crafting, and shops.

#### Shops
- **Start Shop** (Clerk): Arrows (5g), Flasks (100g), Burgers (450g)
- **Forester**: Bronze Axe (1000g), Iron Axe (5000g) — buys logs
- **Miner**: Ores — buys ores
- **Sorcerer**: Staves (magic weapons)
- Shops unlock via quest completion (Foresting → Forester shop, Miner's Quest → Miner shop)

---

### KNOWN LOCATIONS

#### Mudwich Village (~x=188, y=157) — warp target, main hub
- **Blacksmith**: ~x=199, y=169 — Anvil's Echoes quest
- **Village Girl 2**: ~x=136, y=146 — Scavenger quest
- **Forester**: ~x=216, y=114 — Foresting quest (north of village)
- **Villager**: ~x=198, y=114 — shop
- **Bike Lyson**: ~x=166, y=114 — NW building
- **Herbalist**: in village — Herbalist's Desperation quest
- **Rick**: in village — Rick's Roll quest
- **Sorcerer**: in village — Sorcery and Stuff quest
- **Scientist**: in village — Scientist's Potion (stub)
- **Royal Guard 2**: in village — Royal Drama quest
- **Babushka**: in village — Arts and Crafts quest

#### Respawn Hub (x=328, y=892)
- Use warp to leave immediately. Don't explore here.

#### Combat Zones
- **Rats**: everywhere near Mudwich, 20 HP — Levels 1-5
- **Crab Beach**: y=210-230, crabs 15 HP — Levels 1-10 (Bubba is here for crabproblem achievement)
- **Batterflies**: fields around Mudwich, 65 HP — Levels 5-15
- **Sneks**: east across bridge x≈220-240, y≈160, 85 HP — Levels 10-20
- **Goblins**: west of village, 90 HP — Levels 15+
- **Desert**: x≈236-319, y≈96-191 past guard at (231,145) — Levels 20-40
- **Patsow/Lavalands**: x≈321-400, y≈101-187, ogres — Levels 40-60
- **Underwater**: accessible via beach cave after killing Water Guardian — Levels 30-60

#### Resource Zones
- **Oak trees**: abundant near Forester (~216, 114) and north of Mudwich
- **Ore rocks (Nisoc)**: near Miner (~323, 178) and scattered through desert
- **Fishing spots**: beach y=210-230 (shrimp), river crossings
- **Foraging bushes**: blueberry bushes near Mudwich, tomato plants in village gardens

#### Key NPCs
- **Guard**: ~x=231, y=145 — marks desert entrance
- **Dying Soldier**: ~x=288, y=134 — Desert Quest start (lava area)
- **Miner**: ~x=323, y=178 — Miner's Quest
- **Bubba**: ~x=121, y=231 — on beach, kill 10 crabs achievement
- **Sherpa**: snow area — Evil Santa quest start
- **Pretzel**: ice area — Clam Chowder quest start
- **Sponge**: underwater/beach — Sea Activities quest start

#### Warp Destinations
- `warp0` = Mudwich (Level 1 req) — always available
- Lakesworld — unlocked by completing Desert Quest
- Crullfield — unlocked by completing Desert Quest
- Undersea — unlocked by waterguardian achievement
- Patsow — unlocked by patsow achievement (reaching the plateau)
- Aynor — unlocked by completing Ancient Lands quest

---

### WORLD PROGRESSION FLOW

```
MUDWICH (hub) ─── 15 starter quests available
    │
    ├─► Foresting → Iron Axe ─────────────────────────┐
    ├─► Desert Quest → unlocks Lakesworld/Crullfield ─┤
    ├─► Sorcery → Magic Staff ────────────────────────┤
    │                                                  │
    │   CODER'S GLITCH (kill Skeleton King) ◄──────────┘
    │       │    requires: Accuracy 15, Str 20, Def 15
    │       │
    │       ├─► + Miner's Quest + Scavenger
    │       │
    │   CODER'S GLITCH II (kill Ogre Lord → Queen Ant → Forest Dragon)
    │       requires: Accuracy 25, Str 40, Def 30
    │
    ├─► [PARALLEL] Underwater: crabproblem → Water Guardian → deeper
    ├─► [PARALLEL] Ice/Mountains: Evil Santa → Ancient Lands → Aynor
    ├─► [PARALLEL] Mining/Crafting: Miner → deep mining caves
    └─► [ENDGAME] Boss hunting: Hellhound → Wind Guardian → Fulgur
```

### ACHIEVEMENT-GATED CONTENT

| Achievement | Trigger | Unlocks |
|-------------|---------|---------|
| crabproblem | Kill 10 crabs for Bubba (~121, 231) | Underwater cave entrance |
| waterguardian | Kill Water Guardian miniboss | Undersea warp + underwater doors |
| ahiddenpath | Walk through hidden entrance | Secret dungeon doors |
| gravemystery | Step through graveyard entrance | Achievement only |
