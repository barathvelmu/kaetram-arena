"""LLM-driven quest progression — qwen (local Ollama or Modal) must play
each phase via MCP tools and achieve the phase's success criterion.

One parametrized test per (quest_key, phase) tuple. Fails go red on the
per-phase row, so diagnosis shows "Foresting turn_in_stage1 failed: stage
stuck at 1". The phase catalogue (`helpers/quest_phases.py`) lists all
verified working quests plus xfail'd broken ones.

Determinism: temperature=0 + seed=42 by default. Tests are skipped cleanly
if no LLM endpoint is resolvable (no Ollama + no Modal token).
"""

from __future__ import annotations

import pytest

from tests.e2e.helpers.agent_runner import clean_sandbox, run_agent_phase
from tests.e2e.helpers.llm_endpoint import skip_if_no_llm
from tests.e2e.helpers.quest_phases import iter_phases
from tests.e2e.helpers.seed import cleanup_player


_ALL_PHASES = iter_phases()


def _phase_id(param):
    quest_key, phase = param
    return f"{quest_key}__{phase.phase_id}"


@pytest.mark.llm
@pytest.mark.parametrize(
    "quest_key,phase",
    _ALL_PHASES,
    ids=[_phase_id(p) for p in _ALL_PHASES],
)
def test_llm_agent_plays_phase(
    isolated_lane, unique_username, quest_key, phase,
):
    """Spawn the MCP agent under LLM control, run the phase, assert success."""
    endpoint = skip_if_no_llm()

    # xfail marking happens AFTER endpoint resolution so we still skip
    # cleanly when LLM is unavailable rather than xfail-without-reason.
    if phase.xfail_reason:
        pytest.xfail(f"{quest_key}/{phase.phase_id}: {phase.xfail_reason}")

    result = run_agent_phase(
        username=unique_username,
        phase=phase,
        endpoint=endpoint,
        game_url=isolated_lane.client_url,
    )
    try:
        assert result.success, (
            f"{quest_key}/{phase.phase_id} failed:\n{result.diagnostic()}"
        )
    finally:
        cleanup_player(unique_username)
        clean_sandbox(result.sandbox_dir)
