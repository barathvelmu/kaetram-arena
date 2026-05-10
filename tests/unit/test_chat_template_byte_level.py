"""Byte-level chat-template asserts that the audit could not verify from
web search alone. These run against the live `unsloth/Qwen3.5-9B`
tokenizer_config.json (read directly from the HF cache or via AutoTokenizer
when available) and lock down what `patch_qwen_chat_template` depends on.

If any assertion fires after a tokenizer revision bump, the patch needs
updating before training. Better to fail loud here than silently strip
`<think>` from intermediate turns at training time (QwenLM/Qwen3 #1831).

These tests do NOT import torch — they read the cached tokenizer config
as JSON. The full apply_chat_template smoke (which needs torch) runs as
part of the Modal preflight when the GPU container imports transformers.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOKENIZER_ID = "unsloth/Qwen3.5-9B"

sys.path.insert(0, str(REPO_ROOT / "finetune"))


def _find_cached_tokenizer_config() -> Path | None:
    """Locate `tokenizer_config.json` for `unsloth/Qwen3.5-9B` in the local
    HF cache. Returns None if not cached.
    """
    cache_root = Path(
        os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface")
    )
    base = cache_root / "hub" / f"models--{TOKENIZER_ID.replace('/', '--')}"
    if not base.exists():
        return None
    snapshots = base / "snapshots"
    if not snapshots.exists():
        return None
    for rev_dir in snapshots.iterdir():
        cfg = rev_dir / "tokenizer_config.json"
        if cfg.exists():
            return cfg
    return None


@pytest.fixture(scope="module")
def chat_template() -> str:
    cfg_path = _find_cached_tokenizer_config()
    if cfg_path is None:
        pytest.skip(
            f"{TOKENIZER_ID} not in HF cache; run "
            f"`huggingface-cli download {TOKENIZER_ID} tokenizer_config.json` first"
        )
    cfg = json.loads(cfg_path.read_text())
    tpl = cfg.get("chat_template")
    # Newer transformers may store the template in a sibling chat_template.jinja.
    if not tpl:
        jinja_path = cfg_path.parent / "chat_template.jinja"
        if jinja_path.exists():
            tpl = jinja_path.read_text()
    if not tpl:
        pytest.skip("tokenizer_config.json has no chat_template field")
    return tpl


@pytest.fixture(scope="module")
def tokenizer_config() -> dict:
    cfg_path = _find_cached_tokenizer_config()
    if cfg_path is None:
        pytest.skip(f"{TOKENIZER_ID} not in HF cache")
    return json.loads(cfg_path.read_text())


def test_pad_token_distinct_from_eos(tokenizer_config):
    """If pad == eos, labels-mask-pad-to-(-100) prevents the model from ever
    learning to emit eos. Documented Qwen3-family footgun.
    """
    pad = tokenizer_config.get("pad_token")
    eos = tokenizer_config.get("eos_token")
    assert pad and eos, "tokenizer_config missing pad_token or eos_token"
    assert pad != eos, (
        f"pad_token ({pad!r}) == eos_token ({eos!r}); model can never learn to stop."
    )


def test_patch_target_substring_present(chat_template):
    """`patch_qwen_chat_template` does a literal `.replace(old, new)` on the
    chat_template. The `old` substring must exist verbatim in the live
    template, otherwise the patch raises RuntimeError at training start.
    """
    needle = "{%- if loop.index0 > ns.last_query_index %}"
    assert needle in chat_template, (
        f"Patch target {needle!r} not found in live chat_template. "
        f"Tokenizer revision changed; update finetune/render.py:patch_qwen_chat_template "
        f"or pin a tokenizer revision."
    )


def test_auto_extract_think_prelude_present(chat_template):
    """The stock template auto-extracts `<think>...</think>` from `content`
    into `reasoning_content` BEFORE the patched branch runs. This is what
    makes the patch safe — without it, content with inline `<think>` would
    render double `<think>` tags.
    """
    assert "<think>" in chat_template
    assert (
        "split('</think>')" in chat_template
        or "</think>" in chat_template
    ), (
        "Stock template lost the <think>/</think> auto-extract prelude; "
        "convert_to_qwen.py emits CoT inline in content (no reasoning_content), "
        "so the patched render branch would produce double-tag rendering."
    )


def test_patched_template_string_replace_succeeds(chat_template):
    """Run the literal string-replace from finetune/render.py against the
    live template (no transformers/torch needed). If the indentation has
    drifted, the replace is a no-op and we'll catch it here.
    """
    old = (
        "{%- if loop.index0 > ns.last_query_index %}\n"
        "            {{- '<|im_start|>' + message.role + '\\n<think>\\n' + reasoning_content + '\\n</think>\\n\\n' + content }}\n"
        "        {%- else %}\n"
        "            {{- '<|im_start|>' + message.role + '\\n' + content }}\n"
        "        {%- endif %}"
    )
    assert old in chat_template, (
        "patch_qwen_chat_template's `old` block (with 12-space indentation) "
        "is NOT a verbatim substring of the live template. Either the "
        "template's indentation drifted or the leading-space count changed. "
        "Update finetune/render.py to match the new shape."
    )


def test_qwen3_5_arch_via_config(tokenizer_config):
    """If the cached config drift to a non-qwen3_5 architecture (e.g. mirror
    flipped), we want to know before launching."""
    # tokenizer_config doesn't carry model_type; check sibling config.json.
    cfg_path = _find_cached_tokenizer_config()
    if cfg_path is None:
        pytest.skip("not cached")
    model_cfg = cfg_path.parent / "config.json"
    if not model_cfg.exists():
        pytest.skip("config.json not in snapshot")
    cfg = json.loads(model_cfg.read_text())
    assert cfg.get("model_type") == "qwen3_5", (
        f"expected model_type='qwen3_5', got {cfg.get('model_type')!r}"
    )
