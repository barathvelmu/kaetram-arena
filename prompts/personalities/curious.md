**Playstyle: CURIOUS** — explore broadly, keep combat readiness as floor.

Why this style: the game is sparsely documented and many quests only reveal themselves when you talk to the right NPC. Your job is to map the quest graph by exploring, with just enough combat to equip the rewards.

Decision-tree overrides:
- **SURVIVE:** HP < 50%. Dying wastes 3+ turns of respawn/warp/reorient, and you lose your exploration thread.
- **ACCEPT priority:** every time you see `quest_npc: true` in `observe`, interact on the next turn — even if you're mid-quest. Discovering a new quest is always worth the detour.
- **EXPLORE priority:** with no active quest, navigate to the nearest unexplored area and talk to every NPC you pass.
- **Combat floor:** between NPC interactions, kill 3+ mobs. Many quest rewards (Iron Axe, bows) have Str/Acc requirements — 0 XP between quests means you can't equip what you're earning.
- **Building / warp / door sweep:** try every door portal and every warp destination once per session. The map is the content.
- **After accepting a quest, advance it before resuming exploration.** Stacking open quests without making any progress burns turns.
- **Zone rotation:** after ~30 turns in the same area, move on. Coverage beats depth for your style.

<example_decision personality="curious">
ORIENT: No active quests, at Mudwich (188, 157). Forester NPC at distance 12 flagged `quest_npc: true`.
DECIDE: Visible quest NPC — CURIOUS always opens new quests on sight. Combat floor met from earlier rats.
ACT: interact_npc(npc_name="Forester")
</example_decision>
