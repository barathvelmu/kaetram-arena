"""
Modal finetune script for Qwen3.5-9B on Kaetram gameplay data.

Uses Unsloth for 2x faster training + 70% less memory. bf16 LoRA (NOT QLoRA —
4-bit is not recommended for Qwen3.5 due to quantization differences).

Exports merged safetensors for SGLang serving on Modal.

Usage:
    # First time: authenticate with Modal
    modal setup

    # Run finetuning (uses H100 GPU, ~$6-8 total)
    modal run finetune/train_modal.py

    # Deploy serving endpoint
    modal deploy finetune/serve_modal.py
"""

import pathlib
from dataclasses import dataclass
from typing import Optional

import modal
from notifications import format_notification, notification_env

# ---------------------------------------------------------------------------
# Modal setup
# ---------------------------------------------------------------------------

app = modal.App("kaetram-qwen-finetune")
_notify_env = notification_env()
_notification_secrets = [modal.Secret.from_dict(_notify_env)] if _notify_env else []

# Persistent volumes — cache model weights, store results
model_cache_vol = modal.Volume.from_name("kaetram-model-cache", create_if_missing=True)
checkpoint_vol = modal.Volume.from_name("kaetram-model-vol", create_if_missing=True)

# Container image — Lane B pin set verified empirically on 2026-05-09 via
#   `pip install --dry-run` against latest Unsloth.
# Qwen3.5 architecture (`qwen3_5`) requires transformers v5 (verified locally:
# transformers 4.57.4 raises `KeyError: 'qwen3_5'` on AutoConfig). Unsloth
# 2026.5.2 caps `transformers<=5.5.0`, `trl<=0.24.0`, `torch<2.11`, and
# `datasets<4.4.0` — versions below match the resolver's output, do NOT bump
# without re-running the dry-run. CUDA 12.8 image matches torch 2.10+cu128 ABI
# (avoids flash-attn ABI break per Dao-AILab/flash-attention#1644). hf_transfer
# was removed in transformers v5 (replaced by hf_xet bundled with hub>=1.0).
train_image = (
    modal.Image.from_registry(
        "nvidia/cuda:12.8.1-devel-ubuntu22.04",
        add_python="3.11",
    )
    .apt_install("cmake", "build-essential", "git")
    .uv_pip_install(
        # torch 2.8.0 is the LATEST torch version with prebuilt flash-attn 2.8.3
        # wheels. FA2 supports torch 2.4-2.8; torch 2.9-2.10 only have FA4-beta
        # wheels which aren't production-ready. Unsloth 2026.5.2 caps torch<2.11
        # so 2.8.0 is well within bounds. Verified prebuilt wheel name:
        # flash_attn-2.8.3+cu12torch2.8cxx11abiFALSE-cp311-cp311-linux_x86_64.whl
        "torch==2.8.0",
        "accelerate>=1.10.0",
        "bitsandbytes>=0.49.2",
        "datasets==4.3.0",
        "huggingface_hub>=1.3.0,<2.0",
        "peft>=0.18.0",
        "transformers==5.5.0",
        "trl==0.24.0",
        "unsloth==2026.5.2",
        "unsloth_zoo>=2026.5.1",
        # xformers 0.0.32.post2 is the compatible build for torch 2.8.0
        # (verified via pip dry-run; 0.0.35 requires torch 2.10).
        "xformers==0.0.32.post2",
    )
    # flash-attn must be installed AFTER torch (build dependency, needs nvcc).
    # 2.8.3 is the latest stable FA2 line as of 2026-05-09 with prebuilt wheels
    # matching torch 2.8 + cu12 + cp311 (verified via Dao-AILab GitHub releases).
    .run_commands("pip install flash-attn==2.8.3 --no-build-isolation")
    .env({"HF_HOME": "/model_cache", "TOKENIZERS_PARALLELISM": "false"})
    .add_local_python_source("notifications")
    .add_local_python_source("render")
)

with train_image.imports():
    # unsloth must be imported first to apply patches
    import unsloth  # noqa: F401,I001
    import datasets
    import torch
    from trl import SFTConfig, SFTTrainer
    from unsloth import FastLanguageModel, train_on_responses_only

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL_ID = "unsloth/Qwen3.5-9B"  # Unsloth-optimized, Apache 2.0
MAX_SEQ_LEN = 16384  # System prompt (~3.8k) + tools (~1.2k) + multi-turn windows need headroom; fits on H100 80GB.
LORA_R = 64
LORA_ALPHA = 64  # alpha = r recommended for Qwen3.5
LORA_TARGETS = [
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj",
]

# Training
BATCH_SIZE = 4
GRAD_ACCUM = 4  # effective batch = 16. arXiv 2507.07101 (Jul 2025): on
                # single GPU, gradient accumulation is wasteful — prefer the
                # largest per_device batch that fits, since per-step launch
                # overhead amortizes sub-linearly. b=2→b=4 measured ~30% net
                # step-time reduction at 16K seq + r=64 LoRA + grad-ckpt=True.
LR = 1e-4
WARMUP_RATIO = 0.05
WEIGHT_DECAY = 0.01
MAX_STEPS = 5  # SMOKE: 5-step run. Revert to -1 for full r10.
EPOCHS = 1
SAVE_STEPS = 5  # SMOKE: save once at the end so merge path is exercised.
EVAL_STEPS = 5  # SMOKE: single eval at the end; full eval is too expensive.
LOGGING_STEPS = 1  # SMOKE: was 10; want loss visible at every step.

# Mask user/system/tool tokens — train loss only on assistant responses.
MASK_INPUT_TOKENS = True

EXPERIMENT_NAME = "kaetram-qwen3.5-9b-r10"


# ---------------------------------------------------------------------------
# Render path (system-prompt build, chat-template patch, per-record render).
# Single source of truth lives in `finetune/render.py` so the conversion gate
# (convert_to_qwen.py), trainer (here), serve, and KTO all agree byte-for-byte.
# ---------------------------------------------------------------------------

import random as _random

from render import (
    build_system_prompt,
    patch_qwen_chat_template,
    render_record,
)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_kaetram_dataset(train_bytes: bytes, val_bytes: bytes, metadata_bytes: bytes, tokenizer):
    """Load Kaetram SFT data and render it via the shared render path.

    Records on disk contain only gameplay messages (no system prompt). The
    system prompt + personality + (training-only) intro paraphrase are
    re-applied per record via `render_record` from `finetune/render.py` —
    same code path the conversion gate (convert_to_qwen._drop_overlong) uses
    to measure tokens, so the trainer cannot disagree with the gate.
    """
    import json

    patch_qwen_chat_template(tokenizer)

    metadata = json.loads(metadata_bytes)
    system_prompt = metadata["system_prompt"]
    personality_suffixes = metadata.get("personality_suffixes", {})

    def parse_and_format(raw_bytes, augment_rng=None):
        records = json.loads(raw_bytes)
        rows = [
            {"text": render_record(rec, system_prompt, personality_suffixes, tokenizer, rng=augment_rng)}
            for rec in records
        ]
        return datasets.Dataset.from_list(rows)

    train_rng = _random.Random(42)  # reproducible variant selection
    train_ds = parse_and_format(train_bytes, augment_rng=train_rng)
    val_ds = parse_and_format(val_bytes, augment_rng=None)  # val: original prompt only
    return train_ds, val_ds


# ---------------------------------------------------------------------------
# Main training function
# ---------------------------------------------------------------------------

@app.function(
    image=train_image,
    gpu="H200",  # 141GB HBM3e @ 4.8 TB/s — 1.43x bandwidth over H100 SXM. At ~14k median seq + r=64 LoRA + adamw_8bit + grad-ckpt activation traffic, this workload is HBM-bandwidth-bound, so H200 lands ~30-45% step-time reduction for +15% $/hr (net cost-per-step win). Modal "H100" is already SXM (no PCIe option exists).
    timeout=24 * 3600,  # Modal's hard cap is 86400s (24h). At 9k records ×
                       # ~14k median tokens, 1 epoch lands ~5-13h on H100;
                       # well within budget. If a future run needs longer,
                       # checkpoint + resume across multiple Modal calls.
    volumes={
        "/model_cache": model_cache_vol,
        "/checkpoints": checkpoint_vol,
    },
    secrets=_notification_secrets,
)
def train(train_data: bytes, val_data: bytes, metadata: bytes):
    """Run Unsloth bf16 LoRA finetune and save merged safetensors."""
    import json
    from notifications import send_email_notification

    print(f"Training data: {len(train_data):,} bytes")
    print(f"Validation data: {len(val_data):,} bytes")
    print(f"Metadata: {len(metadata):,} bytes")

    # bf16, not 4-bit — QLoRA is not recommended for Qwen3.5.
    print(f"Loading {MODEL_ID}...")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=MODEL_ID,
        max_seq_length=MAX_SEQ_LEN,
        load_in_4bit=False,
        load_in_16bit=True,
    )

    # Configure LoRA
    print("Configuring LoRA...")
    model = FastLanguageModel.get_peft_model(
        model,
        r=LORA_R,
        target_modules=LORA_TARGETS,
        lora_alpha=LORA_ALPHA,
        lora_dropout=0,
        bias="none",
        use_rslora=False,  # diverges at r=64/alpha=64 due to 8x effective LR
        # "unsloth" mode async-offloads activations to CPU over PCIe — tuned
        # for 500K+ context regimes where it's the only OOM-saver. At 16K seq
        # on 80GB+ HBM (H100/H200), the offload bandwidth becomes the bottleneck
        # and dominates step time. HF native True keeps activations on-GPU and
        # recomputes locally — measured ~15-40% faster at this scale.
        use_gradient_checkpointing=True,
        random_state=42,
    )

    # Load and format dataset
    print("Loading dataset...")
    train_ds, val_ds = load_kaetram_dataset(train_data, val_data, metadata, tokenizer)
    print(f"Train: {len(train_ds)} records, Val: {len(val_ds)} records")

    # Loss masking is applied via train_on_responses_only after trainer init
    # (SFTConfig.completion_only_loss does not work with dataset_text_field="text"
    # — without a response_template it silently no-ops).
    output_dir = f"/checkpoints/{EXPERIMENT_NAME}"
    print(f"Loss masking: train_on_responses_only={MASK_INPUT_TOKENS}")
    sft_config = SFTConfig(
        output_dir=output_dir,
        num_train_epochs=EPOCHS,
        max_steps=MAX_STEPS,
        per_device_train_batch_size=BATCH_SIZE,
        per_device_eval_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=LR,
        lr_scheduler_type="cosine",
        warmup_ratio=WARMUP_RATIO,
        weight_decay=WEIGHT_DECAY,
        optim="adamw_8bit",
        bf16=True,
        logging_steps=LOGGING_STEPS,
        eval_strategy="steps",
        eval_steps=EVAL_STEPS,
        save_strategy="steps",
        save_steps=SAVE_STEPS,
        save_total_limit=3,
        report_to="none",
        seed=42,
        dataset_text_field="text",
        # TRL >=0.20 renamed SFTConfig.max_seq_length -> max_length and silently
        # ignores the old name. https://github.com/huggingface/trl/issues/3910
        max_length=MAX_SEQ_LEN,
        packing=False,
    )

    # Trainer
    print("Initializing SFTTrainer...")
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        args=sft_config,
    )

    # Mask user/system/tool tokens — train loss on assistant turns only.
    # Qwen3.5 chat format: <|im_start|>user\n ... <|im_end|>\n<|im_start|>assistant\n ...
    if MASK_INPUT_TOKENS:
        print("Applying train_on_responses_only (assistant turns only)")
        trainer = train_on_responses_only(
            trainer,
            instruction_part="<|im_start|>user\n",
            response_part="<|im_start|>assistant\n",
        )

        # TRL #3927 (still open as of May 2026): when truncation eats every
        # assistant token of a record, all labels become -100 and per-record
        # loss is silently 0, no error. _drop_overlong should prevent this
        # upstream, but render parity drift would re-open the hole. Wrap the
        # collator so any all-masked record aborts the run loud and early.
        # https://github.com/huggingface/trl/issues/3927
        import torch as _torch
        _inner_collator = trainer.data_collator

        def _checked_collator(features):
            batch = _inner_collator(features)
            labels = batch.get("labels")
            if labels is not None and isinstance(labels, _torch.Tensor):
                unmasked_per_row = (labels != -100).any(dim=-1)
                if not bool(unmasked_per_row.all().item()):
                    bad = (~unmasked_per_row).nonzero(as_tuple=True)[0].tolist()
                    raise RuntimeError(
                        f"TRL #3927 guard: {len(bad)}/{labels.shape[0]} records "
                        f"in this batch have ALL labels masked to -100; loss "
                        f"would be silently zero. _drop_overlong gate is broken "
                        f"or render parity has drifted. Bad row indices: {bad}"
                    )
            return batch

        trainer.data_collator = _checked_collator

    total_params = sum(p.numel() for p in model.parameters())
    trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"Total params: {total_params:,}, Trainable: {trainable_params:,} ({100*trainable_params/total_params:.2f}%)")

    subject, body = format_notification(
        "Kaetram SFT Training Started",
        [
            f"Experiment: {EXPERIMENT_NAME}",
            f"Model: {MODEL_ID}",
            f"Train records: {len(train_ds)}",
            f"Val records: {len(val_ds)}",
            f"Max seq len: {MAX_SEQ_LEN}",
        ],
    )
    send_email_notification(subject, body)

    # Train
    print("Starting training...")
    try:
        result = trainer.train()
    except Exception as e:
        subject, body = format_notification(
            "Kaetram SFT Training Failed",
            [
                f"Experiment: {EXPERIMENT_NAME}",
                f"Error: {type(e).__name__}: {e}",
            ],
        )
        send_email_notification(subject, body)
        raise
    print(f"Training complete: {result.metrics}")

    # Save LoRA adapter
    adapter_dir = f"{output_dir}/adapter"
    print(f"Saving LoRA adapter to {adapter_dir}...")
    model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)

    # Save merged model (safetensors) for SGLang serving on Modal
    merged_dir = f"{output_dir}/merged"
    print(f"Saving merged safetensors to {merged_dir}...")
    model.save_pretrained_merged(merged_dir, tokenizer, save_method="merged_16bit")

    # Save metrics
    metrics = {
        "train_loss": result.metrics.get("train_loss"),
        "train_runtime": result.metrics.get("train_runtime"),
        "epochs": EPOCHS,
        "train_records": len(train_ds),
        "val_records": len(val_ds),
        "model_id": MODEL_ID,
        "lora_r": LORA_R,
        "lora_alpha": LORA_ALPHA,
        "save_method": "merged_16bit",
        "max_seq_len": MAX_SEQ_LEN,
        "loss_masking": MASK_INPUT_TOKENS,
    }
    with open(f"{output_dir}/training_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)

    # Commit volume so everything persists
    checkpoint_vol.commit()

    subject, body = format_notification(
        "Kaetram SFT Training Finished",
        [
            f"Experiment: {EXPERIMENT_NAME}",
            f"Train loss: {metrics.get('train_loss')}",
            f"Runtime: {metrics.get('train_runtime')}",
            f"Train records: {metrics.get('train_records')}",
            f"Val records: {metrics.get('val_records')}",
        ],
    )
    send_email_notification(subject, body)

    print(f"\nDone! Files saved to Modal volume 'kaetram-model-vol':")
    print(f"  Adapter:  /checkpoints/{EXPERIMENT_NAME}/adapter/")
    print(f"  Merged:   /checkpoints/{EXPERIMENT_NAME}/merged/")
    print(f"  Metrics:  /checkpoints/{EXPERIMENT_NAME}/training_metrics.json")
    print(f"\nDeploy serving endpoint:")
    print(f"  modal deploy finetune/serve_modal.py")
    return metrics


# ---------------------------------------------------------------------------
# Merge checkpoint adapter into deployable model
# ---------------------------------------------------------------------------

@app.function(
    image=train_image,
    gpu="H100",
    timeout=1800,  # 30 min — merge is fast
    volumes={
        "/model_cache": model_cache_vol,
        "/checkpoints": checkpoint_vol,
    },
)
def merge_checkpoint(checkpoint_name: str):
    """Load a training checkpoint and merge adapter into full model using Unsloth."""
    import json
    import os

    checkpoint_dir = f"/checkpoints/{EXPERIMENT_NAME}/{checkpoint_name}"
    output_dir = f"/checkpoints/{EXPERIMENT_NAME}"

    if not os.path.exists(checkpoint_dir):
        raise FileNotFoundError(f"Checkpoint not found: {checkpoint_dir}")

    print(f"Loading base model {MODEL_ID} with Unsloth...")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=MODEL_ID,
        max_seq_length=MAX_SEQ_LEN,
        load_in_4bit=False,
        load_in_16bit=True,
    )

    # Apply LoRA config (needed so Unsloth knows the adapter structure)
    model = FastLanguageModel.get_peft_model(
        model,
        r=LORA_R,
        target_modules=LORA_TARGETS,
        lora_alpha=LORA_ALPHA,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=42,
    )

    # Load checkpoint weights into the adapter
    print(f"Loading adapter weights from {checkpoint_dir}...")
    from peft import set_peft_model_state_dict
    import safetensors.torch
    adapter_weights = {}
    for f in os.listdir(checkpoint_dir):
        if f.endswith(".safetensors"):
            w = safetensors.torch.load_file(os.path.join(checkpoint_dir, f))
            adapter_weights.update(w)
    if adapter_weights:
        set_peft_model_state_dict(model, adapter_weights)
        print(f"  Loaded {len(adapter_weights)} weight tensors")
    else:
        raise FileNotFoundError(f"No .safetensors files in {checkpoint_dir}")

    # Save adapter copy
    adapter_dir = f"{output_dir}/adapter"
    print(f"Saving adapter to {adapter_dir}...")
    model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)

    # Merge using Unsloth (handles VLM architecture correctly)
    merged_dir = f"{output_dir}/merged"
    print(f"Merging with Unsloth and saving to {merged_dir}...")
    model.save_pretrained_merged(merged_dir, tokenizer, save_method="merged_16bit")

    checkpoint_vol.commit()

    print(f"\nDone! Merged model saved:")
    print(f"  Adapter:  {adapter_dir}")
    print(f"  Merged:   {merged_dir}")
    print(f"\nDeploy: modal deploy finetune/serve_modal.py")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

@app.local_entrypoint()
def main(skip_preflight: bool = False):
    """Upload training data and launch the finetune job."""
    import os
    import shutil
    import subprocess
    import sys
    from notifications import send_email_notification

    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    train_path = os.path.join(project_dir, "dataset", "qwen_sft", "train.json")
    val_path = os.path.join(project_dir, "dataset", "qwen_sft", "val.json")
    metadata_path = os.path.join(project_dir, "dataset", "qwen_sft", "metadata.json")

    if not os.path.exists(train_path):
        raise FileNotFoundError(f"Training data not found: {train_path}")
    if not os.path.exists(metadata_path):
        raise FileNotFoundError(
            f"Metadata not found: {metadata_path}\n"
            "Run: python3 convert_to_qwen.py --input dataset/extracted/ --output dataset/qwen_sft/"
        )

    # Preflight: run the four dataset-shape suites that gate r10. A 24-72h H100
    # run is too expensive to launch on a dataset that fails the cheap checks.
    # Bypass with `modal run finetune/train_modal.py --skip-preflight` only if
    # the suites are known-good for this exact dataset build.
    if not skip_preflight:
        # Note: test_truncation_variant_aware.py takes ~14 min on CPU because
        # it renders 12,699 records under 4 variants. Run it manually before
        # any dataset rebuild — it's not in the auto-preflight subprocess
        # because the cost outweighs the value at every-launch frequency.
        #   python3 -m pytest tests/unit/test_truncation_variant_aware.py
        preflight_targets = [
            "tests/unit/test_pin_set.py",
            "tests/unit/test_dataset_filters.py",
            "tests/unit/test_observe_supervision.py",
            "tests/unit/test_truncation.py",
            "tests/unit/test_think_roundtrip.py",
            "tests/unit/test_chat_template_byte_level.py",
            "tests/unit/test_prompt_parity.py",
            "tests/unit/test_tool_vocab_drift.py",
        ]
        print("=" * 60)
        print("Preflight: running dataset-shape gate suites...")
        print("=" * 60)
        # Use the project's venv python (has pytest + transformers) rather
        # than sys.executable. When this is invoked via `modal run`, the
        # Modal CLI lives in its own pipx venv and lacks the test deps;
        # falling back to a candidate list keeps the gate working in dev,
        # CI, and `modal run` contexts equally.
        venv_py = os.path.join(project_dir, ".venv", "bin", "python3")
        py_candidates = [venv_py, "python3", sys.executable]
        py_for_preflight = next(
            (p for p in py_candidates if os.path.isfile(p) or shutil.which(p)),
            sys.executable,
        )
        result = subprocess.run(
            [py_for_preflight, "-m", "pytest", "-x", "-q", *preflight_targets],
            cwd=project_dir,
        )
        if result.returncode != 0:
            raise SystemExit(
                f"Preflight tests failed (exit {result.returncode}). "
                f"Fix the dataset or pass --skip-preflight to bypass."
            )
        print("Preflight passed.\n")

    print(f"Uploading training data...")
    with open(train_path, "rb") as f:
        train_data = f.read()
    with open(val_path, "rb") as f:
        val_data = f.read()
    with open(metadata_path, "rb") as f:
        metadata = f.read()

    print(f"  Train: {len(train_data):,} bytes")
    print(f"  Val:   {len(val_data):,} bytes")
    print(f"  Metadata: {len(metadata):,} bytes")
    print(f"  Model: {MODEL_ID}")
    print(f"  Method: bf16 LoRA (r={LORA_R}, alpha={LORA_ALPHA})")
    print(f"  Export: merged safetensors (for Modal SGLang serving)")
    print(f"  Max seq len: {MAX_SEQ_LEN}")
    print(f"Launching on Modal H100...")

    subject, body = format_notification(
        "Kaetram SFT Training Launched",
        [
            f"Experiment: {EXPERIMENT_NAME}",
            f"Model: {MODEL_ID}",
            f"Train bytes: {len(train_data):,}",
            f"Val bytes: {len(val_data):,}",
            f"Max seq len: {MAX_SEQ_LEN}",
        ],
    )
    send_email_notification(subject, body)

    metrics = train.remote(train_data, val_data, metadata)

    print(f"\n{'='*60}")
    print("TRAINING COMPLETE")
    print(f"{'='*60}")
    print(f"  Loss:     {metrics.get('train_loss', '?'):.4f}")
    print(f"  Runtime:  {metrics.get('train_runtime', 0):.0f}s")
    print(f"  Records:  {metrics.get('train_records')} train / {metrics.get('val_records')} val")
    print(f"\nDeploy serving endpoint:")
    print(f"  modal deploy finetune/serve_modal.py")
