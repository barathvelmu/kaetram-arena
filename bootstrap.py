"""Single source of truth for the user bootstrap message Claude saw at
training-data collection time.

Used by:
  - orchestrate.py            (live Claude data collection)
  - convert_to_qwen.py        (SFT record assembly)
  - play_qwen.py              (Qwen runtime / eval)
  - play.sh                   (single-agent dev loop)

The bootstrap is reconstructible from each session's `.meta.json`
(personality + session_n) — session logs do not capture user-side prompts.
"""

PLAYSTYLE_HINT = {
    "grinder":            "You play GRINDER — combat-first: attack, loot, equip, eat. Push levels and unlock higher-tier gear.",
    "completionist":      "You play COMPLETIONIST — progression-first: talk to NPCs, accept quests, gather, craft. Finish quest chains before advancing.",
    "explorer_tinkerer":  "You play EXPLORER/TINKERER — world + systems coverage: navigate everywhere, warp to new zones, try unusual NPCs and novel crafts.",
}


def build_orchestrate_bootstrap(personality: str | None, session_n: int) -> str:
    """Build the user bootstrap message exactly as orchestrate.py emits it
    for the Claude harness. Codex/opencode-specific addendum is NOT included
    here — orchestrate appends it post-bootstrap based on adapter.name.
    """
    hint = PLAYSTYLE_HINT.get(personality or "", "")
    return (
        f"{hint}\n\n"
        "IMPORTANT: Do NOT search for files, read documentation, or explore the filesystem. "
        "Your ONLY job is to play the game via the MCP tools. "
        "Start IMMEDIATELY by calling observe — the MCP server auto-logs in on first connect.\n\n"
        f"Session #{session_n}.\n"
        "Follow your system instructions exactly. Call observe first, "
        "then run the OBSERVE-ACT loop."
    )
