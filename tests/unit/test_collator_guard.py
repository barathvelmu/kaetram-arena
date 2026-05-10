"""TRL #3927 collator guard test — verifies `make_checked_collator` aborts loud
when any batch row has all labels masked to -100.

Why this test exists: `train_modal.make_checked_collator` is the load-bearing
safety net for TRL issue #3927 (still OPEN in trl 0.24.0 as of May 2026,
https://github.com/huggingface/trl/issues/3927). With `train_on_responses_only`
the inner collator can produce a batch row whose labels are entirely -100 if
truncation eats every assistant token; per-record loss then silently goes to
zero. `convert_to_qwen._drop_overlong` is the upstream gate; this collator is
the fail-loud net if render parity drifts.

Without a test, we have no proof the assertion ever fires. If a future refactor
breaks the wrapper (e.g. swaps `.any(dim=-1)` for `.all(dim=-1)` by typo, or
silently swallows the RuntimeError), training would silently zero its loss and
we wouldn't notice until eval shows a base-quality model.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "finetune"))


def _make_batch(label_rows):
    """Build a fake collator output {input_ids, labels} from a list of label
    rows. Each row is a list of ints; -100 is the mask sentinel."""
    import torch
    return {
        "input_ids": torch.tensor([[abs(x) if x != -100 else 0 for x in row] for row in label_rows]),
        "labels": torch.tensor(label_rows),
    }


def _identity_collator(features):
    """Test stand-in for the real DataCollatorForLanguageModeling.
    Features are pre-built {input_ids, labels} dicts — pass through."""
    import torch
    if isinstance(features[0], dict):
        return {
            k: torch.stack([f[k] for f in features]) if torch.is_tensor(features[0][k]) else features[0][k]
            for k in features[0]
        }
    return features


def test_passes_through_normal_batch():
    """A batch with at least one trained token per row must pass unchanged."""
    try:
        import torch  # noqa: F401
    except ImportError:
        pytest.skip("torch not installed")
    from train_modal import make_checked_collator

    inner = lambda features: features  # noqa: E731 — arity-matching identity
    wrapper = make_checked_collator(inner)

    good_batch = _make_batch([
        [-100, -100, 5, 7, 9],   # row 0: 3 trained tokens
        [-100, 3, -100, 4, -100],  # row 1: 2 trained tokens
    ])
    out = wrapper(good_batch)
    assert "labels" in out
    assert out["labels"].shape == (2, 5)


def test_raises_on_all_masked_row():
    """A batch with even ONE row of all -100 must raise RuntimeError loud."""
    try:
        import torch  # noqa: F401
    except ImportError:
        pytest.skip("torch not installed")
    from train_modal import make_checked_collator

    inner = lambda features: features  # noqa: E731
    wrapper = make_checked_collator(inner)

    bad_batch = _make_batch([
        [-100, -100, 5, 7, 9],         # row 0: trained
        [-100, -100, -100, -100, -100],  # row 1: ALL MASKED
        [-100, 3, -100, 4, -100],      # row 2: trained
    ])

    with pytest.raises(RuntimeError) as excinfo:
        wrapper(bad_batch)

    msg = str(excinfo.value)
    assert "TRL #3927" in msg, f"error message must reference TRL #3927: {msg}"
    assert "1/3 records" in msg, (
        f"error message must report which row index is bad; got: {msg}"
    )
    assert "[1]" in msg, (
        f"error message must include bad row index list [1]; got: {msg}"
    )


def test_raises_on_all_rows_masked():
    """Edge case: every row masked. Must still raise (not silently skip)."""
    try:
        import torch  # noqa: F401
    except ImportError:
        pytest.skip("torch not installed")
    from train_modal import make_checked_collator

    inner = lambda features: features  # noqa: E731
    wrapper = make_checked_collator(inner)

    fully_bad = _make_batch([
        [-100, -100, -100],
        [-100, -100, -100],
    ])

    with pytest.raises(RuntimeError) as excinfo:
        wrapper(fully_bad)
    assert "2/2 records" in str(excinfo.value)


def test_passes_through_when_no_labels_key():
    """If the inner collator returns a batch without `labels` (unusual but
    possible during inference-style passes), the wrapper should pass through
    rather than crash."""
    try:
        import torch
    except ImportError:
        pytest.skip("torch not installed")
    from train_modal import make_checked_collator

    inner = lambda features: features  # noqa: E731
    wrapper = make_checked_collator(inner)

    no_labels = {"input_ids": torch.tensor([[1, 2, 3]])}
    out = wrapper(no_labels)
    assert out is no_labels


def test_passes_through_when_labels_not_tensor():
    """Defensive: if labels is e.g. a list (not a Tensor), the wrapper
    should not assume Tensor methods."""
    try:
        import torch
    except ImportError:
        pytest.skip("torch not installed")
    from train_modal import make_checked_collator

    inner = lambda features: features  # noqa: E731
    wrapper = make_checked_collator(inner)

    list_labels = {"input_ids": torch.tensor([[1]]), "labels": [-100, 5, 7]}
    out = wrapper(list_labels)
    assert out is list_labels
