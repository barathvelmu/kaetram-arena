"""Quest-phase catalogue — structured definitions for LLM-driven quest tests.

Each phase specifies:
  - `seed` — Mongo state to inject before the agent logs in (position, items,
    quest stage)
  - `user_prompt` — task description handed to the model as the first user
    message
  - `max_turns` — hard cap for the OODA loop (keeps failing tests fast)
  - `success(snapshot)` — closure that inspects post-run state and returns
    True on pass

The catalogue is grounded in Kaetram-Open/QUEST_CITATIONS.md (verified quest
mechanics) and PLAYTHROUGH.md (recommended step-by-step play).

Unsupported/broken quests use `xfail=True` rather than omitting them — the
test fires, fails as expected, and upgrades to XPASS if Kaetram-Open ever
fixes the underlying issue, giving us a notification trigger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .kaetram_world import QUESTS, adjacent_to


@dataclass
class QuestSnapshot:
    """Minimal view of post-run state that `success` closures inspect."""
    quest_stages: dict[str, int]            # {quest_key: stage}
    quest_finished: dict[str, bool]         # {quest_key: finished?}
    inventory_keys: dict[str, int]          # {item_key: total_count}
    position: tuple[int, int] | None
    hit_points: int | None
    tool_calls: list[str] = field(default_factory=list)  # ordered tool names
    turns_played: int = 0


SuccessFn = Callable[[QuestSnapshot], bool]


@dataclass
class Phase:
    phase_id: str                           # e.g. "accept", "turn_in_stage1"
    seed: dict[str, Any]                    # args forwarded to seed_player()
    user_prompt: str                        # task prompt for the agent
    success: SuccessFn
    max_turns: int = 12
    xfail_reason: str | None = None         # marks the test xfail if set


# -----------------------------------------------------------------------------
# Success-closure factories — keep phase definitions readable
# -----------------------------------------------------------------------------

def stage_advanced(quest_key: str, from_stage: int = 0) -> SuccessFn:
    """Pass if quest stage > `from_stage` after the agent's run."""
    return lambda s: s.quest_stages.get(quest_key, 0) > from_stage


def stage_reached(quest_key: str, at_least: int) -> SuccessFn:
    return lambda s: s.quest_stages.get(quest_key, 0) >= at_least


def quest_finished(quest_key: str) -> SuccessFn:
    return lambda s: s.quest_finished.get(quest_key, False)


def has_item(item_key: str, at_least: int = 1) -> SuccessFn:
    return lambda s: s.inventory_keys.get(item_key, 0) >= at_least


def any_tool_called(*tool_names: str) -> SuccessFn:
    wanted = set(tool_names)
    return lambda s: bool(set(s.tool_calls) & wanted)


def all_of(*fns: SuccessFn) -> SuccessFn:
    return lambda s: all(f(s) for f in fns)


# -----------------------------------------------------------------------------
# Shared seed fragments
# -----------------------------------------------------------------------------

def _seed_at(npc_key: str, *, stage: int, quest_key: str,
             inventory=None, equipment=None, skills=None,
             hit_points: int = 69) -> dict[str, Any]:
    """Seed adjacent to the quest's NPC, at the given quest stage."""
    base_inv: list[dict[str, Any]] = [{"key": "bronzeaxe", "count": 1}]
    if inventory:
        base_inv.extend(inventory)
    return {
        "position": adjacent_to(npc_key),
        "hit_points": hit_points,
        "inventory": base_inv,
        "equipment": equipment,
        "skills": skills,
        "quests": [{"key": quest_key, "stage": stage, "subStage": 0,
                    "completedSubStages": []}],
    }


# -----------------------------------------------------------------------------
# Phase catalogue — only quests WORKING or UNSURE per QUEST_CITATIONS.md
# -----------------------------------------------------------------------------

QUEST_PHASES: dict[str, list[Phase]] = {
    "foresting": [
        Phase(
            phase_id="accept",
            seed=_seed_at("forestnpc", stage=0, quest_key="foresting"),
            user_prompt=(
                "You are standing next to the Forester NPC. Start the "
                "Foresting quest by talking to him."
            ),
            max_turns=8,
            success=stage_advanced("foresting"),
        ),
        Phase(
            phase_id="turn_in_stage1",
            seed=_seed_at(
                "forestnpc", stage=1, quest_key="foresting",
                inventory=[{"key": "ironaxe", "count": 1},
                           {"key": "logs", "count": 10}],
                equipment=[{"type": 0, "key": "ironaxe", "count": 1,
                            "ability": -1, "abilityLevel": 0}],
            ),
            user_prompt=(
                "You have 10 Logs. The Forester is adjacent. Turn in the "
                "logs to advance the Foresting quest."
            ),
            max_turns=8,
            success=stage_reached("foresting", 2),
        ),
        Phase(
            phase_id="turn_in_stage2",
            seed=_seed_at(
                "forestnpc", stage=2, quest_key="foresting",
                inventory=[{"key": "ironaxe", "count": 1},
                           {"key": "logs", "count": 10}],
                equipment=[{"type": 0, "key": "ironaxe", "count": 1,
                            "ability": -1, "abilityLevel": 0}],
            ),
            user_prompt=(
                "You have 10 more Logs. Deliver them to the Forester to "
                "finish the Foresting quest."
            ),
            max_turns=8,
            success=quest_finished("foresting"),
        ),
    ],

    "anvilsechoes": [
        Phase(
            phase_id="accept",
            seed=_seed_at("blacksmith", stage=0, quest_key="anvilsechoes"),
            user_prompt=(
                "You are next to the Blacksmith. Start the Anvil's Echoes "
                "quest by talking to him."
            ),
            max_turns=6,
            success=stage_advanced("anvilsechoes"),
        ),
        Phase(
            phase_id="complete",
            seed=_seed_at("blacksmith", stage=1, quest_key="anvilsechoes"),
            user_prompt=(
                "Anvil's Echoes is active. Talk to the Blacksmith again to "
                "complete it."
            ),
            max_turns=6,
            success=quest_finished("anvilsechoes"),
        ),
    ],

    "scientistspotion": [
        Phase(
            phase_id="accept_one_stage_complete",
            seed=_seed_at("scientist", stage=0, quest_key="scientistspotion"),
            user_prompt=(
                "You are standing next to the Scientist. Accept the "
                "Scientist's Potion quest. It is a single-stage quest — "
                "accepting it finishes it."
            ),
            max_turns=6,
            # one-stage quest — accept == complete
            success=stage_advanced("scientistspotion"),
        ),
    ],

    "desertquest": [
        Phase(
            phase_id="accept",
            seed=_seed_at("lavanpc", stage=0, quest_key="desertquest"),
            user_prompt=(
                "You are next to the Dying Soldier in the desert/lava area. "
                "Start the Desert Quest by talking to him."
            ),
            max_turns=6,
            success=stage_advanced("desertquest"),
        ),
    ],

    "minersquest": [
        Phase(
            phase_id="accept",
            seed=_seed_at("miner", stage=0, quest_key="minersquest"),
            user_prompt=(
                "You are next to the Miner. Start the Miner's Quest by "
                "talking to him."
            ),
            max_turns=6,
            success=stage_advanced("minersquest"),
        ),
        Phase(
            phase_id="turn_in_nisocore",
            seed=_seed_at(
                "miner", stage=1, quest_key="minersquest",
                inventory=[{"key": "nisocore", "count": 15}],
            ),
            user_prompt=(
                "You have 15 nisocore. The Miner wants them. Turn in the "
                "ore to complete the quest."
            ),
            max_turns=8,
            success=quest_finished("minersquest"),
        ),
    ],

    "scavenger": [
        Phase(
            phase_id="accept",
            seed=_seed_at("villagegirl2", stage=0, quest_key="scavenger"),
            user_prompt=(
                "You are next to Village Girl. Start the Scavenger quest "
                "by talking to her."
            ),
            max_turns=6,
            success=stage_advanced("scavenger"),
        ),
    ],

    "herbalistdesperation": [
        Phase(
            phase_id="accept",
            seed=_seed_at("herbalist", stage=0, quest_key="herbalistdesperation"),
            user_prompt=(
                "You are next to Herby Mc. Herb. Start the Herbalist's "
                "Desperation quest by talking to him."
            ),
            max_turns=6,
            success=stage_advanced("herbalistdesperation"),
        ),
    ],

    "artsandcrafts": [
        Phase(
            phase_id="accept",
            seed=_seed_at("iamverycoldnpc", stage=0, quest_key="artsandcrafts"),
            user_prompt=(
                "You are next to Babushka. Start the Arts and Crafts quest "
                "by talking to her."
            ),
            max_turns=6,
            success=stage_advanced("artsandcrafts"),
        ),
    ],

    "ricksroll": [
        Phase(
            phase_id="accept",
            seed=_seed_at("rick", stage=0, quest_key="ricksroll"),
            user_prompt=(
                "You are next to Rick. Start the Rick's Roll quest by "
                "talking to him."
            ),
            max_turns=6,
            success=stage_advanced("ricksroll"),
        ),
    ],

    "seaactivities": [
        Phase(
            phase_id="accept",
            seed=_seed_at("sponge", stage=0, quest_key="seaactivities"),
            user_prompt=(
                "You are next to Sponge. Start the Sea Activities quest by "
                "talking to him."
            ),
            max_turns=6,
            success=stage_advanced("seaactivities"),
        ),
    ],

    "royaldrama": [
        Phase(
            phase_id="accept",
            seed=_seed_at("royalguard2", stage=0, quest_key="royaldrama"),
            user_prompt=(
                "You are next to a Royal Guard. Start the Royal Drama "
                "quest by talking to him."
            ),
            max_turns=6,
            success=stage_advanced("royaldrama"),
        ),
    ],

    "royalpet": [
        Phase(
            phase_id="accept",
            seed=_seed_at("king", stage=0, quest_key="royalpet"),
            user_prompt=(
                "You are next to the King. Start the Royal Pet quest by "
                "talking to him."
            ),
            max_turns=6,
            success=stage_advanced("royalpet"),
            xfail_reason="Per QUEST_CITATIONS.md: progression works but "
                         "catpet reward item is undefined; early stages "
                         "still advance.",
        ),
    ],

    "clamchowder": [
        Phase(
            phase_id="accept",
            seed=_seed_at("bluebikinigirlnpc", stage=0, quest_key="clamchowder"),
            user_prompt=(
                "You are next to Pretzel. Start the Clam Chowder quest by "
                "talking to her."
            ),
            max_turns=6,
            success=stage_advanced("clamchowder"),
        ),
    ],

    "sorceryandstuff": [
        Phase(
            phase_id="accept",
            seed=_seed_at("sorcerer", stage=0, quest_key="sorceryandstuff"),
            user_prompt=(
                "You are next to the Sorcerer. Start the Sorcery and Stuff "
                "quest by talking to him."
            ),
            max_turns=6,
            success=stage_advanced("sorceryandstuff"),
        ),
    ],

    "ancientlands": [
        Phase(
            phase_id="accept",
            seed=_seed_at("ancientmanumentnpc", stage=0, quest_key="ancientlands"),
            user_prompt=(
                "You are next to the Ancient Monument. Start the Ancient "
                "Lands quest."
            ),
            max_turns=6,
            success=stage_advanced("ancientlands"),
        ),
        Phase(
            phase_id="turn_in_icesword",
            seed=_seed_at(
                "ancientmanumentnpc", stage=1, quest_key="ancientlands",
                inventory=[{"key": "coppersword", "count": 1},
                           {"key": "icesword", "count": 1}],
                equipment=[{"type": 0, "key": "coppersword", "count": 1,
                            "ability": -1, "abilityLevel": 0}],
            ),
            user_prompt=(
                "You have an Ice Sword. Bring it to the Ancient Monument "
                "to advance the Ancient Lands quest."
            ),
            max_turns=6,
            success=stage_reached("ancientlands", 2),
        ),
    ],

    # -----------------------------------------------------------------------
    # Broken/unreachable quests — xfail'd so regressions here are visible
    # but successful fixes flip to xpass and alert us.
    # -----------------------------------------------------------------------
    "evilsanta": [
        Phase(
            phase_id="accept",
            seed={"position": (188, 157), "quests": [{"key": "evilsanta",
                    "stage": 0, "subStage": 0, "completedSubStages": []}]},
            user_prompt="Start the Evil Santa quest.",
            max_turns=4,
            success=stage_advanced("evilsanta"),
            xfail_reason="Per QUEST_CITATIONS.md: Evil Santa stage-1 door "
                         "recently added (PR #1) but upstream data may lag.",
        ),
    ],
    "codersglitch": [
        Phase(
            phase_id="accept",
            seed={"position": (188, 157), "quests": [{"key": "codersglitch",
                    "stage": 0, "subStage": 0, "completedSubStages": []}]},
            user_prompt="Start The Coder's Glitch quest.",
            max_turns=4,
            success=stage_advanced("codersglitch"),
            xfail_reason="Per QUEST_CITATIONS.md: unusable talisman item "
                         "blocks completion even after noc→npc typo fix.",
        ),
    ],
}


def iter_phases() -> list[tuple[str, Phase]]:
    """Flatten QUEST_PHASES into (quest_key, phase) tuples for parametrize."""
    flat: list[tuple[str, Phase]] = []
    for quest_key, phases in QUEST_PHASES.items():
        for phase in phases:
            flat.append((quest_key, phase))
    return flat
