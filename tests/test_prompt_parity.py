"""Byte-exact prompt parity tests between training and eval/inference.

These are regression guards for the r9 bug where training used a hand-paraphrased
2-sentence PERSONALITY_SUFFIXES dict while eval loaded the full ~1.5 KB personality
.md file. The fix (r10) routes both paths through the same prompts/personalities/*.md
files and substitutes at the __PERSONALITY_BLOCK__ placeholder instead of appending.

These tests do NOT need the built dataset and can run before extraction.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _build_train_prompt(personality: str | None) -> str:
    """Reproduce the training-time system prompt the model will see.

    Mirrors finetune/train_modal.py._build_system_prompt when rng is None
    (validation mode — deterministic, no paraphrasing).
    """
    from convert_to_qwen import SYSTEM_PROMPT, PERSONALITY_SUFFIXES

    personality_block = ""
    if personality and personality in PERSONALITY_SUFFIXES:
        personality_block = PERSONALITY_SUFFIXES[personality]
    return SYSTEM_PROMPT.replace("__PERSONALITY_BLOCK__", personality_block)


def _build_eval_prompt(personality: str | None) -> str:
    """Resolve the eval-time system prompt via eval_harness.resolve_system_prompt."""
    from eval_harness import resolve_system_prompt

    # Both paths must use identical username for byte parity. convert_to_qwen
    # hardcodes "KaetramAgent"; pass the same here.
    return resolve_system_prompt(
        project_dir=str(REPO_ROOT),
        username="KaetramAgent",
        personality=personality or "",
    )


def _strip_project_dir(s: str) -> str:
    """eval_harness substitutes __PROJECT_DIR__ with the real path; training doesn't.
    The placeholder doesn't appear in system.md today but strip it defensively so the
    test doesn't spuriously fail if someone adds it.
    """
    return s.replace(str(REPO_ROOT), "__PROJECT_DIR__")


def test_prompt_parity_no_personality():
    train = _strip_project_dir(_build_train_prompt(None))
    evl = _strip_project_dir(_build_eval_prompt(None))
    assert train == evl, (
        f"Drift (no personality): train={len(train)}B vs eval={len(evl)}B\n"
        f"First diff at byte {_first_diff(train, evl)}"
    )


def test_prompt_parity_aggressive():
    train = _strip_project_dir(_build_train_prompt("aggressive"))
    evl = _strip_project_dir(_build_eval_prompt("aggressive"))
    assert train == evl, (
        f"Drift (aggressive): train={len(train)}B vs eval={len(evl)}B\n"
        f"First diff at byte {_first_diff(train, evl)}"
    )


def test_prompt_parity_methodical():
    train = _strip_project_dir(_build_train_prompt("methodical"))
    evl = _strip_project_dir(_build_eval_prompt("methodical"))
    assert train == evl, (
        f"Drift (methodical): train={len(train)}B vs eval={len(evl)}B\n"
        f"First diff at byte {_first_diff(train, evl)}"
    )


def test_prompt_parity_curious():
    train = _strip_project_dir(_build_train_prompt("curious"))
    evl = _strip_project_dir(_build_eval_prompt("curious"))
    assert train == evl, (
        f"Drift (curious): train={len(train)}B vs eval={len(evl)}B\n"
        f"First diff at byte {_first_diff(train, evl)}"
    )


def test_personality_block_is_full_md_file():
    """PERSONALITY_SUFFIXES must contain the full .md file contents, not a paraphrase."""
    from convert_to_qwen import PERSONALITY_SUFFIXES

    for name in ("aggressive", "methodical", "curious"):
        md_path = REPO_ROOT / "prompts" / "personalities" / f"{name}.md"
        expected = md_path.read_text()
        actual = PERSONALITY_SUFFIXES[name]
        assert actual == expected, (
            f"{name}: PERSONALITY_SUFFIXES drifted from {md_path.name} "
            f"({len(actual)}B vs file {len(expected)}B)"
        )


def test_train_modal_source_substitutes_at_placeholder():
    """Static check: finetune/train_modal.py._build_system_prompt must substitute
    personality at __PERSONALITY_BLOCK__, not append. Pre-r10 code used `+=` which
    was the bug.
    """
    src = (REPO_ROOT / "finetune" / "train_modal.py").read_text()
    # The current (r10) implementation must reference the placeholder.
    assert "__PERSONALITY_BLOCK__" in src, (
        "train_modal.py no longer references __PERSONALITY_BLOCK__ placeholder — "
        "personality substitution path has drifted."
    )
    # Stale paraphrase dict must not be defined.
    assert "PERSONALITY_INSTRUCTION_VARIANTS = {" not in src, (
        "train_modal.py still defines PERSONALITY_INSTRUCTION_VARIANTS — this was "
        "the source of the r9 train/eval personality mismatch."
    )


def test_train_kto_source_has_no_stale_personality_variants():
    src = (REPO_ROOT / "finetune" / "train_kto_modal.py").read_text()
    assert "PERSONALITY_INSTRUCTION_VARIANTS = {" not in src, (
        "train_kto_modal.py still defines PERSONALITY_INSTRUCTION_VARIANTS — stale."
    )


def _first_diff(a: str, b: str) -> int:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n
