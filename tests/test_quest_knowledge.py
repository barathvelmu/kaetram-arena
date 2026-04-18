from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
GAME_KNOWLEDGE = REPO_ROOT / "prompts" / "game_knowledge.md"
QUEST_WALKTHROUGHS = REPO_ROOT / "prompts" / "quest_walkthroughs.json"
SYSTEM_PROMPT = REPO_ROOT / "prompts" / "system.md"

COMPLETABLE_QUESTS = (
    "Tutorial",
    "Foresting",
    "Desert Quest",
    "Anvil's Echoes",
    "Royal Drama",
    "Royal Pet",
    "Sorcery and Stuff",
    "Rick's Roll",
    "Sea Activities",
    "Scientist's Potion",
    "Arts and Crafts",
    "Miner's Quest",
    "Miner's Quest II",
    "Herbalist's Desperation",
    "Scavenger",
    "Clam Chowder",
    "Ancient Lands",
)

BLOCKED_QUESTS = (
    "Evil Santa",
    "The Coder's Glitch",
    "The Coder's Glitch II",
    "Coder's Fallacy",
)


def _load_walkthroughs() -> dict:
    return json.loads(QUEST_WALKTHROUGHS.read_text())


def test_game_knowledge_lists_all_completable_and_blocked_quests():
    text = GAME_KNOWLEDGE.read_text()
    assert "17 completable quests / 21 total" in text
    for quest in COMPLETABLE_QUESTS:
        assert quest in text, f"{quest} missing from game_knowledge.md"
    for quest in BLOCKED_QUESTS:
        assert quest in text, f"{quest} missing from blocked quest table"


def test_game_knowledge_includes_key_runtime_truths():
    text = GAME_KNOWLEDGE.read_text()
    required_snippets = (
        "Tutorial is auto-completed on load",
        "Start **Arts and Crafts** to unlock Crafting",
        "Start **Scientist's Potion** to unlock Alchemy",
        "undersea` access requires the `waterguardian` achievement",
        "Fletching requires a `knife` from Clerk",
        "Ancient Lands",
        "Ice Knight at **(808,813)**"
    )
    for snippet in required_snippets:
        assert snippet in text, f"Missing runtime truth: {snippet}"


def test_quest_walkthroughs_use_canonical_names_and_cover_all_quests():
    data = _load_walkthroughs()
    assert set(data.keys()) == set(COMPLETABLE_QUESTS + BLOCKED_QUESTS)
    assert data["Tutorial"]["name"] == "Tutorial"
    assert "Welcome to Kaetram" not in QUEST_WALKTHROUGHS.read_text()


def test_quest_walkthrough_statuses_match_current_tree_truth():
    data = _load_walkthroughs()
    assert data["Tutorial"]["status"] == "auto_completed"
    assert data["Royal Pet"]["status"] == "working_reward_broken"
    assert data["Sorcery and Stuff"]["status"] == "working_reward_broken"
    for quest in BLOCKED_QUESTS:
        assert data[quest]["status"] == "blocked"
        assert data[quest]["blocked_reason"]


def test_high_impact_quest_fields_are_grounded():
    data = _load_walkthroughs()

    assert data["Sea Activities"]["requirements"]["achievements"] == ["waterguardian"]
    assert data["Sea Activities"]["actual_rewards"] == ["10000 gold"]

    assert data["Scientist's Potion"]["unlocks"]["on_start"] == ["Alchemy interface"]
    assert data["Arts and Crafts"]["unlocks"]["on_start"] == ["Crafting benches"]

    assert data["Anvil's Echoes"]["actual_rewards"] == ["bronzeboots"]
    assert "Does not unlock Smithing" in " ".join(data["Anvil's Echoes"]["reward_caveats"])

    assert data["Clam Chowder"]["crafting_chain"]["clamchowder"] == (
        "clamobject x1 + potato x1 + bowlsmall x1"
    )
    assert "Ice Knight at (808,813)" in " ".join(data["Ancient Lands"]["stage_summary"])


def test_system_prompt_routes_off_catalog_and_uses_query_quest_proactively():
    text = SYSTEM_PROMPT.read_text()
    assert "complete every **completable** quest on the current tree" in text
    assert "New quest, stage change, or any gated / multi-step quest" in text
    assert "choose the earliest unfinished **completable** quest" in text
    assert "If `query_quest` returns `status: blocked`" in text
    assert "\"chop\" (acc+def)" in text
    assert "buy_item(npc_name, item_index, count)" in text
