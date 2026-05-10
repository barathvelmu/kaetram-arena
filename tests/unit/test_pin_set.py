"""Pin-set sanity for the Modal training image.

Lane A (transformers <5) is empirically dead — `transformers 4.57.4` raises
`KeyError: 'qwen3_5'` on `AutoConfig.from_pretrained("unsloth/Qwen3.5-9B")`.
Lane B versions below were derived from `pip install --dry-run` against
latest Unsloth on 2026-05-09.

This test parses `finetune/train_modal.py` to find the `uv_pip_install`
arglist (the actual deployed pin set) and validates each constraint
against Lane B's invariants. We test the *image* pins, not the local
venv, because:
  - the local venv is unrelated to what Modal runs
  - the pin set is the contract; drift here is what would actually break
    a Modal run
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TRAIN_MODAL = REPO_ROOT / "finetune" / "train_modal.py"


def _extract_pip_pins() -> dict[str, str]:
    """Return {package: full_constraint_string} from the uv_pip_install
    block in train_modal.py. Constraints are the verbatim string passed
    to pip (e.g. 'transformers==5.5.0' → key 'transformers', value '==5.5.0').
    """
    # Strip Python comment lines first so unbalanced parens inside a `#`
    # comment can't terminate the regex match prematurely.
    src_lines = TRAIN_MODAL.read_text().splitlines()
    src = "\n".join(
        ln for ln in src_lines if not ln.lstrip().startswith("#")
    )
    # Match the uv_pip_install(...) block.
    m = re.search(r"\.uv_pip_install\(\s*(.*?)\s*\)", src, re.DOTALL)
    assert m, "uv_pip_install block not found in train_modal.py"
    block = m.group(1)
    pins: dict[str, str] = {}
    # Each arg is a quoted string like "transformers==5.5.0".
    for q in re.findall(r'"([^"]+)"', block):
        # Split on the first comparator.
        m2 = re.match(r"^([A-Za-z0-9_\-\[\]]+)(.*)$", q)
        if not m2:
            continue
        name = m2.group(1).split("[")[0].lower().replace("-", "_")
        pins[name] = m2.group(2)
    return pins


def _extract_flash_attn_pin() -> str | None:
    """flash-attn is installed in a separate run_commands step; extract its
    version pin (e.g. '2.8.3') from either the simple `flash-attn==X.Y.Z`
    form or a direct wheel-URL pin (`flash_attn-X.Y.Z+cuN...`).
    """
    src = TRAIN_MODAL.read_text()
    # Form 1: literal `pip install flash-attn==X.Y.Z`
    m = re.search(r"pip install (flash-attn==[^\s\"']+)", src)
    if m:
        return m.group(1)
    # Form 2: direct wheel URL — pull the version from the wheel filename.
    m = re.search(r"flash_attn-([\d\.]+)\+cu", src)
    if m:
        return f"flash-attn=={m.group(1)} (wheel-URL)"
    return None


def _extract_cuda_image() -> str | None:
    src = TRAIN_MODAL.read_text()
    m = re.search(r'from_registry\(\s*"([^"]+)"', src)
    return m.group(1) if m else None


def test_qwen3_5_requires_transformers_v5():
    """transformers <5 raises KeyError('qwen3_5') on AutoConfig — verified
    empirically. Image pin must be v5."""
    pins = _extract_pip_pins()
    tx = pins.get("transformers")
    assert tx is not None, "transformers not pinned in image"
    # Accept ==5.x.y or >=5.0.0,<6.0.0 etc.
    assert re.search(r"(==5\.|>=5\.)", tx), (
        f"transformers pin {tx!r} does not require v5; Qwen3.5 needs v5"
    )


def test_unsloth_caps_respected_in_pin_set():
    """Unsloth 2026.5.2 caps: transformers<=5.5.0, trl<=0.24.0, torch<2.11,
    datasets<4.4.0. Pinning above any of these forces a silent downgrade
    of unsloth at install time."""
    pins = _extract_pip_pins()
    invariants = [
        ("transformers", "5.5.0", "<="),
        ("trl", "0.24.0", "<="),
        ("torch", "2.11.0", "<"),
        ("datasets", "4.4.0", "<"),
    ]
    for pkg, cap, op in invariants:
        constraint = pins.get(pkg)
        assert constraint is not None, f"{pkg} not pinned in image"
        # The simplest safe check: the pin must be `==X` where X satisfies
        # the cap, OR a range with an explicit upper bound at or below cap.
        m = re.match(r"==([\d\.]+)", constraint)
        if m:
            from packaging.version import Version
            v = Version(m.group(1))
            ok = (v <= Version(cap)) if op == "<=" else (v < Version(cap))
            assert ok, (
                f"{pkg}{constraint} violates Unsloth cap {pkg}{op}{cap}"
            )
        else:
            # Range pin: must contain an upper bound consistent with cap.
            assert (
                f"<={cap}" in constraint
                or f"<{cap}" in constraint
                or any(f"<{c}" in constraint for c in (cap,))
            ), (
                f"{pkg}{constraint} lacks an upper bound at or below "
                f"Unsloth's cap {op}{cap}; pin = will silently downgrade unsloth"
            )


def test_peft_meets_transformers_v5_floor_in_pin_set():
    """transformers main setup.py requires peft>=0.18.0."""
    pins = _extract_pip_pins()
    peft = pins.get("peft")
    assert peft is not None, "peft not pinned"
    # Either ==X with X>=0.18, or >=0.18.0
    m = re.match(r">=([\d\.]+)", peft)
    if m:
        from packaging.version import Version
        assert Version(m.group(1)) >= Version("0.18.0"), (
            f"peft pin {peft} below transformers v5's floor of 0.18.0"
        )
    else:
        m = re.match(r"==([\d\.]+)", peft)
        if m:
            from packaging.version import Version
            assert Version(m.group(1)) >= Version("0.18.0")


def test_huggingface_hub_floor_in_pin_set():
    """transformers main setup.py: huggingface-hub>=1.3.0,<2.0."""
    pins = _extract_pip_pins()
    hub = pins.get("huggingface_hub")
    assert hub is not None, "huggingface_hub not pinned"
    assert ">=1.3" in hub or "==1." in hub, (
        f"huggingface_hub pin {hub} below transformers v5's floor of 1.3.0"
    )
    assert "<2" in hub or "==1." in hub, (
        f"huggingface_hub pin {hub} missing upper bound <2.0"
    )


def test_hf_transfer_not_pinned_on_v5():
    """hf_transfer was removed in transformers v5 (replaced by hf_xet,
    bundled with huggingface_hub>=1.0). Keeping it pinned wastes container
    bytes."""
    pins = _extract_pip_pins()
    assert "hf_transfer" not in pins, (
        "hf_transfer pinned but transformers v5 dropped it in favor of hf_xet"
    )


def test_flash_attn_pinned_to_safe_version():
    """flash-attn 2.7.4.post1 had ABI break against torch 2.7+cu128
    (Dao-AILab/flash-attention#1644). 2.8.3 is the latest stable line as
    of 2026-05-09 with prebuilt wheels; pin explicitly."""
    fa = _extract_flash_attn_pin()
    assert fa is not None, "flash-attn install command not found"
    assert "==" in fa, (
        f"flash-attn must be pinned to a specific version, got {fa!r}"
    )


def test_cuda_image_matches_cu128():
    """torch wheels are cu128. Image must be CUDA 12.8 to avoid nvcc/torch
    minor-version drift breaking flash-attn compilation."""
    img = _extract_cuda_image()
    assert img is not None, "from_registry image not found"
    assert "cuda:12.8" in img, (
        f"CUDA image {img!r} does not match cu128 torch wheels; "
        f"flash-attn build will hit nvcc/torch CUDA-minor mismatch"
    )


def test_torch_pinned_explicitly():
    """Torch must be pinned explicitly; otherwise unsloth's resolver picks
    whatever fits its `<2.11` cap at install time, which can flip across
    Modal builds."""
    pins = _extract_pip_pins()
    torch = pins.get("torch")
    assert torch is not None, "torch not pinned"
    assert torch.startswith("=="), (
        f"torch pin {torch!r} is not ==-pinned; "
        f"resolver may pick different versions across builds"
    )


def test_sftconfig_field_rename_runtime():
    """TRL #3910: `max_seq_length` → `max_length` rename happened in 0.20.0.
    SFTConfig MUST use max_length=. (Unsloth's FastLanguageModel.from_pretrained
    legitimately keeps `max_seq_length=` as its own kwarg — that's a different
    API and unaffected by TRL #3910.)
    """
    src = TRAIN_MODAL.read_text()
    # Find every SFTConfig(...) call body and check its kwargs.
    sft_calls = re.findall(r"SFTConfig\((.*?)\)\s*\n", src, re.DOTALL)
    assert sft_calls, "no SFTConfig(...) call found"
    for body in sft_calls:
        # Strip comment lines so we don't match documentation references.
        code_only = "\n".join(
            ln for ln in body.splitlines() if not ln.lstrip().startswith("#")
        )
        assert not re.search(r"\bmax_seq_length\s*=", code_only), (
            "SFTConfig(...) uses max_seq_length=, but TRL >=0.20 silently "
            "ignores it (TRL #3910), zeroing the loss. Use max_length="
        )
        assert re.search(r"\bmax_length\s*=", code_only), (
            "SFTConfig(...) must pass max_length= to enforce truncation"
        )
