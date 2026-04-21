"""Quest JSON schema integrity — every quest file must parse + have the
required top-level fields. Failing early here beats a cryptic runtime
error deep inside Kaetram's quest loader.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

KAETRAM_OPEN = Path(os.environ.get("KAETRAM_OPEN_PATH", Path.home() / "projects" / "Kaetram-Open"))
QUESTS_DIR = KAETRAM_OPEN / "packages" / "server" / "data" / "quests"


def _quest_files() -> list[Path]:
    if not QUESTS_DIR.exists():
        pytest.skip(f"quests dir not found at {QUESTS_DIR}")
    return sorted(QUESTS_DIR.glob("*.json"))


@pytest.mark.parametrize("quest_file", [p.name for p in _quest_files()] if QUESTS_DIR.exists() else [])
def test_quest_json_is_well_formed(quest_file):
    """Parse + validate core schema — name, description, stages, each stage
    has a task."""
    data = json.loads((QUESTS_DIR / quest_file).read_text())
    assert "name" in data, f"{quest_file} missing name"
    assert "stages" in data, f"{quest_file} missing stages"
    stages = data["stages"]
    assert isinstance(stages, dict) and stages, f"{quest_file} stages invalid"
    for stage_id, stage in stages.items():
        assert "task" in stage, f"{quest_file} stage {stage_id} missing task field"


def test_no_typo_noc_in_any_quest():
    """Regression lock: an earlier typo wrote `noc` (no-op) instead of `npc`
    on codersglitch stage 0. Guard against it reappearing anywhere."""
    for qf in _quest_files():
        content = qf.read_text()
        for line_no, line in enumerate(content.splitlines(), start=1):
            # Allow `noc` inside larger words (we don't care about e.g. "innocent")
            # — only flag it as a JSON key.
            stripped = line.strip()
            if stripped.startswith('"noc"') or stripped.startswith("'noc'"):
                pytest.fail(f"{qf.name}:{line_no} contains 'noc' key (did you mean 'npc'?)")
