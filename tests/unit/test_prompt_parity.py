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

REPO_ROOT = Path(__file__).resolve().parents[2]
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


def test_prompt_parity_grinder():
    train = _strip_project_dir(_build_train_prompt("grinder"))
    evl = _strip_project_dir(_build_eval_prompt("grinder"))
    assert train == evl, (
        f"Drift (grinder): train={len(train)}B vs eval={len(evl)}B\n"
        f"First diff at byte {_first_diff(train, evl)}"
    )


def test_prompt_parity_completionist():
    train = _strip_project_dir(_build_train_prompt("completionist"))
    evl = _strip_project_dir(_build_eval_prompt("completionist"))
    assert train == evl, (
        f"Drift (completionist): train={len(train)}B vs eval={len(evl)}B\n"
        f"First diff at byte {_first_diff(train, evl)}"
    )


def test_prompt_parity_explorer_tinkerer():
    train = _strip_project_dir(_build_train_prompt("explorer_tinkerer"))
    evl = _strip_project_dir(_build_eval_prompt("explorer_tinkerer"))
    assert train == evl, (
        f"Drift (explorer_tinkerer): train={len(train)}B vs eval={len(evl)}B\n"
        f"First diff at byte {_first_diff(train, evl)}"
    )


def test_personality_block_is_full_md_file():
    """PERSONALITY_SUFFIXES must contain the full .md file contents, not a paraphrase."""
    from convert_to_qwen import PERSONALITY_SUFFIXES

    for name in ("grinder", "completionist", "explorer_tinkerer"):
        md_path = REPO_ROOT / "prompts" / "personalities" / f"{name}.md"
        expected = md_path.read_text()
        actual = PERSONALITY_SUFFIXES[name]
        assert actual == expected, (
            f"{name}: PERSONALITY_SUFFIXES drifted from {md_path.name} "
            f"({len(actual)}B vs file {len(expected)}B)"
        )


def test_render_substitutes_at_placeholder():
    """Static check: finetune/render.build_system_prompt must substitute
    personality at __PERSONALITY_BLOCK__, not append. Substitution lives in
    `finetune/render.py` (single source of truth); train_modal.py imports it
    from there. Append-style substitution was the r9 bug — guard against
    accidental regression.
    """
    render_src = (REPO_ROOT / "finetune" / "render.py").read_text()
    assert "__PERSONALITY_BLOCK__" in render_src, (
        "finetune/render.py no longer references __PERSONALITY_BLOCK__ placeholder — "
        "personality substitution path has drifted."
    )
    train_src = (REPO_ROOT / "finetune" / "train_modal.py").read_text()
    # train_modal must import the builder, not redefine it.
    assert "from render import" in train_src and "build_system_prompt" in train_src, (
        "train_modal.py should import build_system_prompt from finetune/render.py"
    )
    # Stale paraphrase dict must not be defined anywhere.
    for path in ("finetune/train_modal.py", "finetune/render.py"):
        src = (REPO_ROOT / path).read_text()
        assert "PERSONALITY_INSTRUCTION_VARIANTS = {" not in src, (
            f"{path} still defines PERSONALITY_INSTRUCTION_VARIANTS — this was "
            f"the source of the r9 train/eval personality mismatch."
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


# ── rng-paraphrase path: covers BUG-3 (stale variants) ───────────────────────

_BODY_SPLIT_MARKER = "\n\n<game_knowledge>"


def test_paraphrase_variant_zero_matches_system_md_intro():
    """Variant 0 must be byte-identical to system.md lines 1-9 (the intro
    before `<game_knowledge>`) after `__USERNAME__` substitution.

    This is the "canonical" variant — `build_system_prompt(rng=None)` skips
    paraphrase entirely and uses the loaded system.md, but training rows can
    also land variant 0 under rng. They must produce the same intro.
    """
    from convert_to_qwen import SYSTEM_PROMPT
    from finetune.render import SYSTEM_PROMPT_INTRO_VARIANTS

    body_idx = SYSTEM_PROMPT.index(_BODY_SPLIT_MARKER)
    expected_intro = SYSTEM_PROMPT[:body_idx]
    actual_intro = SYSTEM_PROMPT_INTRO_VARIANTS[0]
    assert actual_intro == expected_intro, (
        f"Variant 0 drifts from system.md intro: "
        f"variant_len={len(actual_intro)}B, system_md_intro_len={len(expected_intro)}B; "
        f"first diff at byte {_first_diff(actual_intro, expected_intro)}. "
        f"Regenerate variant 0 from prompts/system.md lines 1-9 (after __USERNAME__ sub)."
    )


def test_paraphrase_variants_share_body_with_system_md():
    """Every variant in SYSTEM_PROMPT_INTRO_VARIANTS, when used to build a
    paraphrased system prompt, must produce a body byte-identical to the
    canonical (system.md) body. Only the intro before `<game_knowledge>`
    paraphrases — load-bearing rules in the body must stay byte-stable.
    """
    import random as _random

    from convert_to_qwen import SYSTEM_PROMPT, PERSONALITY_SUFFIXES
    from finetune.render import SYSTEM_PROMPT_INTRO_VARIANTS, build_system_prompt

    canonical = build_system_prompt(SYSTEM_PROMPT, None, PERSONALITY_SUFFIXES, rng=None)
    canonical_body = canonical[canonical.index(_BODY_SPLIT_MARKER):]

    for i, variant in enumerate(SYSTEM_PROMPT_INTRO_VARIANTS):
        # Build a Random whose first .choice() yields this variant
        # deterministically by seeding and then verifying the pick.
        for seed in range(64):
            rng = _random.Random(seed)
            if rng.choice(SYSTEM_PROMPT_INTRO_VARIANTS) is variant:
                break
        else:
            # Fallback: seed-search failed (shouldn't with 4 variants and 64 seeds)
            continue
        # Re-seed because the previous .choice() consumed state.
        rng = _random.Random(seed)
        rendered = build_system_prompt(SYSTEM_PROMPT, None, PERSONALITY_SUFFIXES, rng=rng)
        assert _BODY_SPLIT_MARKER in rendered, (
            f"variant {i}: rendered prompt missing {_BODY_SPLIT_MARKER!r} marker"
        )
        rendered_body = rendered[rendered.index(_BODY_SPLIT_MARKER):]
        assert rendered_body == canonical_body, (
            f"variant {i} body drifts from system.md body "
            f"({len(rendered_body)}B vs canonical {len(canonical_body)}B); "
            f"first diff at byte {_first_diff(rendered_body, canonical_body)}. "
            f"Variants must only paraphrase the intro before <game_knowledge>."
        )


def test_paraphrase_variants_all_mention_core_3():
    """Defense against stale variants: every variant must mention the Core 3
    benchmark + interact_npc opt-in clause. The pre-r10 variants used the
    older 'complete all quests' framing without these — that drift was BUG-3.
    """
    from finetune.render import SYSTEM_PROMPT_INTRO_VARIANTS

    for i, variant in enumerate(SYSTEM_PROMPT_INTRO_VARIANTS):
        assert "CORE" in variant or "Core 3" in variant, (
            f"variant {i} missing Core/CORE 3 framing — likely drifted from "
            f"current system.md. Regenerate from prompts/system.md."
        )
        assert "interact_npc" in variant, (
            f"variant {i} missing interact_npc opt-in clause — likely drifted "
            f"from current system.md (line 7)."
        )
        assert "accept_quest_offer" in variant, (
            f"variant {i} missing accept_quest_offer reference — likely drifted "
            f"from current system.md."
        )
