from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_qwen_chat_template_patch_present_in_render_module():
    """Regression guard for the Qwen `<think>` patch (QwenLM/Qwen3 #1831).

    Source-level check that runs without Modal/Unsloth installs. The patch
    is the single source of truth in `finetune/render.py`; the four Modal
    entry points (`train_modal.py`, `serve_modal.py`, `serve_modal_base.py`,
    `train_kto_modal.py`) all import from it.
    """
    render_path = REPO_ROOT / "finetune" / "render.py"
    source = render_path.read_text()
    assert "{%- if reasoning_content %}" in source
    assert "<think>" in source
    assert "</think>" in source


def test_modal_entry_points_import_patch_from_render():
    """All Modal entry points must import `patch_qwen_chat_template` from
    `finetune.render` rather than defining it inline (drift hazard)."""
    callers = [
        REPO_ROOT / "finetune" / "train_modal.py",
        REPO_ROOT / "finetune" / "serve_modal.py",
        REPO_ROOT / "finetune" / "serve_modal_base.py",
        REPO_ROOT / "finetune" / "train_kto_modal.py",
    ]
    for path in callers:
        source = path.read_text()
        assert "from render import" in source and "patch_qwen_chat_template" in source, (
            f"{path.name} should import patch_qwen_chat_template from finetune/render.py"
        )
        # No inline copy — the patch fragment string belongs in one place.
        assert "{%- if reasoning_content %}" not in source, (
            f"{path.name} appears to define the chat-template patch inline; "
            f"keep it in finetune/render.py and import patch_qwen_chat_template from there."
        )
