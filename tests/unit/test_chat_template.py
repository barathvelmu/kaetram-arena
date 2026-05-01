from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_qwen_chat_templates_preserve_reasoning_content():
    """Regression guard for the Qwen `<think>` patch.

    We keep this source-level so it runs without Modal/Unsloth installs.
    """
    targets = [
        REPO_ROOT / "finetune" / "train_modal.py",
        REPO_ROOT / "finetune" / "serve_modal.py",
        REPO_ROOT / "finetune" / "serve_modal_base.py",
    ]

    for path in targets:
        source = path.read_text()
        assert "{%- if reasoning_content %}" in source
        assert "<think>" in source
        assert "</think>" in source
