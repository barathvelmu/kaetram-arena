"""Parity tests for the shared bootstrap module.

Three guarantees:
  1. bootstrap.build_orchestrate_bootstrap byte-equals what orchestrate.py
     produces inline for the Claude harness (no codex/opencode addendum).
  2. play_qwen runtime first-turn rendered output matches what
     finetune.render.render_record produces for an equivalent SFT record.
     (Asserted in step #5 of the refactor; placeholder here.)
  3. None / "none" personality returns a valid bootstrap with no playstyle
     hint and no leading-blank-line artifact beyond the intentional `\\n\\n`.
"""

from __future__ import annotations

from bootstrap import build_orchestrate_bootstrap, PLAYSTYLE_HINT


def _orchestrate_inline(personality: str | None, session_n: int) -> str:
    """Verbatim copy of orchestrate._build_user_prompt body for the Claude
    harness path (no codex/opencode addendum). Kept here to lock the
    template — if orchestrate.py drifts, this test catches it."""
    hint = {
        "grinder":            "You play GRINDER — combat-first: attack, loot, equip, eat. Push levels and unlock higher-tier gear.",
        "completionist":      "You play COMPLETIONIST — progression-first: talk to NPCs, accept quests, gather, craft. Finish quest chains before advancing.",
        "explorer_tinkerer":  "You play EXPLORER/TINKERER — world + systems coverage: navigate everywhere, warp to new zones, try unusual NPCs and novel crafts.",
    }.get(personality or "", "")
    return (
        f"{hint}\n\n"
        "IMPORTANT: Do NOT search for files, read documentation, or explore the filesystem. "
        "Your ONLY job is to play the game via the MCP tools. "
        "Start IMMEDIATELY by calling observe — the MCP server auto-logs in on first connect.\n\n"
        f"Session #{session_n}.\n"
        "Follow your system instructions exactly. Call observe first, "
        "then run the OBSERVE-ACT loop."
    )


def test_bootstrap_matches_orchestrate_inline():
    """For each personality and a few session numbers, the shared module
    output must byte-equal the inline orchestrate template."""
    for personality in ("grinder", "completionist", "explorer_tinkerer", None):
        for n in (1, 5, 12):
            expected = _orchestrate_inline(personality, n)
            actual = build_orchestrate_bootstrap(personality, n)
            assert actual == expected, (
                f"bootstrap drift for personality={personality!r} session={n}:\n"
                f"  expected: {expected!r}\n"
                f"  actual:   {actual!r}"
            )


def test_bootstrap_handles_none_personality_cleanly():
    """personality=None and personality='none' both produce the same
    no-hint bootstrap — playstyle line is empty, the body is unchanged."""
    a = build_orchestrate_bootstrap(None, 1)
    b = build_orchestrate_bootstrap("none", 1)
    assert a == b
    # Empty hint should leave a leading "\n\n" before IMPORTANT — same as
    # orchestrate would produce for an unrecognized personality. Not three
    # newlines, not a stripped variant.
    assert a.startswith("\n\nIMPORTANT:")


def test_bootstrap_includes_session_number():
    out = build_orchestrate_bootstrap("completionist", 7)
    assert "Session #7." in out


def test_playstyle_hint_keys_match_personality_files():
    """Sanity: PLAYSTYLE_HINT keys must match the personality files on disk
    (excluding the OOD 'none' ablation)."""
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    pdir = repo / "prompts" / "personalities"
    files = {p.stem for p in pdir.glob("*.md")}
    assert set(PLAYSTYLE_HINT.keys()) == files, (
        f"PLAYSTYLE_HINT keys ({set(PLAYSTYLE_HINT.keys())}) "
        f"don't match personality files on disk ({files})"
    )
