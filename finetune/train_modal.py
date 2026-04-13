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

# Container image — CUDA devel base for flash-attn compilation (Qwen3.5 is a unified VLM,
# Unsloth routes through vision.py which needs FA2 compiled with nvcc)
train_image = (
    modal.Image.from_registry(
        "nvidia/cuda:12.6.3-devel-ubuntu22.04",
        add_python="3.11",
    )
    .apt_install("cmake", "build-essential", "git")
    .uv_pip_install(
        "accelerate>=1.9.0",
        "datasets>=3.6.0",
        "hf-transfer>=0.1.9",
        "huggingface_hub>=0.34.2",
        "peft>=0.16.0",
        "transformers>=5.0.0",
        "trl>=0.19.1",
        "unsloth[cu128-torch270]>=2025.7.8",
        "unsloth_zoo>=2025.7.10",
    )
    # flash-attn must be installed AFTER torch (build dependency, needs nvcc from CUDA devel)
    .run_commands("pip install flash-attn --no-build-isolation")
    .env({"HF_HOME": "/model_cache", "TOKENIZERS_PARALLELISM": "false"})
    .add_local_python_source("notifications")
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
MAX_SEQ_LEN = 8192   # Round 6: halved from 16k (median=3.6k, P90=12.7k — 8k covers ~90%)
LORA_R = 64       # Round 2: 4x more capacity (was 16)
LORA_ALPHA = 64   # alpha = r recommended for Qwen3.5
LORA_TARGETS = [
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj",
]

# Training
BATCH_SIZE = 2    # Round 6: doubled (8k context fits batch=2 on H100 80GB)
GRAD_ACCUM = 8    # effective batch = 16 (2 * 8)
LR = 1e-4
WARMUP_RATIO = 0.05
WEIGHT_DECAY = 0.01
MAX_STEPS = -1  # -1 = use num_train_epochs
EPOCHS = 1      # Round 6: 1 epoch — standard SFT, loss converges within epoch 1
SAVE_STEPS = 50
EVAL_STEPS = 50
LOGGING_STEPS = 10

# Loss masking: zero loss on input tokens (Structured Agent Distillation, arxiv 2505.13820)
# Only trains on assistant responses (<think> reasoning + tool calls)
MASK_INPUT_TOKENS = True

# Output
EXPERIMENT_NAME = "kaetram-qwen3.5-9b-r8"


# ---------------------------------------------------------------------------
# Paraphrase augmentation (ORAK ICLR 2026, Consistency Alignment arxiv 2403.14221)
# ---------------------------------------------------------------------------
# Only the intro sentence is paraphrased. The body (entity types, actions, priority
# system, combat, navigation, key info) stays identical — it contains exact type
# numbers, tool signatures, and coordinates that game state JSON references.
# Validation records always use the original prompt for stable measurement.

import random as _random

SYSTEM_PROMPT_INTRO_VARIANTS = [
    # Original
    "You are an AI agent playing Kaetram, a 2D pixel MMORPG. You observe the game via structured game state and an ASCII map, then decide and execute actions.",
    # Paraphrases
    "You control a character in Kaetram, an online 2D RPG. Each turn you receive game state data and an ASCII map, then choose what to do next.",
    "As an AI playing the Kaetram MMORPG, you read structured game state and an ASCII map representation of your surroundings, then select an action.",
    "You are playing Kaetram (a 2D pixel MMORPG) as an automated agent. You perceive the world through structured state data and an ASCII map, then act.",
    "In Kaetram, a 2D online RPG, you are an AI agent. You receive game observations as structured data with an ASCII map and decide your next move.",
    "You operate as an AI player in Kaetram, a pixel-art MMORPG. Your inputs are structured game state and an ASCII map. Pick one action per turn.",
    "Acting as an autonomous agent in the Kaetram game world, you analyze structured game state and an ASCII map each turn, then execute an action.",
    "You are an automated player in Kaetram (2D MMORPG). Observe the game through structured state and ASCII map data, then decide and act.",
]

PERSONALITY_INSTRUCTION_VARIANTS = {
    "aggressive": [
        "Prioritize combat above all. Push into harder zones and fight mobs at the edge of your capability. Accept death as part of progression — re-engage immediately after respawn.",
        "Fight first, think later. Seek out the toughest mobs you can handle and attack relentlessly. Dying is acceptable — get back up and keep fighting.",
        "Maximize combat engagement at all times. Target mobs near or above your level. Deaths are a cost of progress — respawn and resume attacking immediately.",
    ],
    "methodical": [
        "Prepare thoroughly before advancing. Complete quests in order, gather resources, build skills. Keep HP above 60% and always carry food before entering dangerous areas.",
        "Plan carefully and advance step by step. Finish quests sequentially, stock up on supplies, and train skills. Never enter combat below 60% HP or without food.",
        "Take a systematic approach to progression. Build up resources and complete quests in order. Maintain HP above 60% and ensure you have food before fighting.",
    ],
    "curious": [
        "Explore the world broadly. Talk to every NPC, enter every building, warp to new locations. Discovery matters more than efficiency — find quests and areas others miss.",
        "Prioritize discovery and exploration. Visit new areas, interact with all NPCs, and investigate every location. Finding new content matters more than grinding.",
        "Wander and explore as much as possible. Seek out NPCs, new zones, and hidden areas. Exploration takes priority over combat efficiency or quest optimization.",
    ],
    "efficient": [
        "Optimize quest completion. Accept multiple quests, batch objectives, minimize travel. No wasted turns — every action should progress toward a quest or level goal.",
        "Be maximally efficient with every action. Combine quest objectives, reduce unnecessary movement, and focus on leveling through quest completion over grinding.",
        "Streamline progression by batching quests and minimizing idle turns. Every action should move you toward a quest objective or experience gain.",
    ],
}

# Body split marker — everything from this point onward in the system prompt is
# kept identical (exact type numbers, tool signatures, coordinates).
_BODY_SPLIT_MARKER = "\n\n## Entity Types"


def _build_system_prompt(
    base_system_prompt: str,
    personality: str | None,
    personality_suffixes: dict,
    rng: _random.Random | None,
) -> str:
    """Build system prompt, optionally with paraphrased intro and personality.

    When rng is provided (training), randomly selects from intro and personality
    variants. When rng is None (validation), uses the original prompt unchanged.
    """
    if rng is None:
        # Validation: use original prompt as-is
        sys_content = base_system_prompt
        if personality and personality in personality_suffixes:
            sys_content += personality_suffixes[personality]
        return sys_content

    # Training: paraphrase intro, keep body identical
    intro = rng.choice(SYSTEM_PROMPT_INTRO_VARIANTS)
    body_start = base_system_prompt.index(_BODY_SPLIT_MARKER)
    body = base_system_prompt[body_start:]
    sys_content = intro + body

    # Paraphrase personality instructions (keep header, vary description)
    if personality and personality in PERSONALITY_INSTRUCTION_VARIANTS:
        header = f"\n\n## Playstyle: {personality.upper()}\n"
        instruction = rng.choice(PERSONALITY_INSTRUCTION_VARIANTS[personality])
        sys_content += header + instruction

    return sys_content


# ---------------------------------------------------------------------------
# Chat template fix (QwenLM/Qwen3#1831)
# ---------------------------------------------------------------------------
# Stock Qwen 3.5 template drops <think> reasoning from all assistant messages
# before last_query_index. This silently strips CoT from multi-turn training
# data. Fix: always emit <think> when reasoning_content is present.

def _patch_qwen_chat_template(tokenizer):
    """Patch the Qwen 3.5 chat template to preserve <think> in all turns."""
    template = tokenizer.chat_template
    if template is None:
        return

    # The bug: reasoning_content is only emitted for turns after last_query_index
    old = (
        "{%- if loop.index0 > ns.last_query_index %}\n"
        "            {{- '<|im_start|>' + message.role + '\\n<think>\\n' + reasoning_content + '\\n</think>\\n\\n' + content }}\n"
        "        {%- else %}\n"
        "            {{- '<|im_start|>' + message.role + '\\n' + content }}\n"
        "        {%- endif %}"
    )
    # Fix: if reasoning_content exists, always emit it with <think> tags
    new = (
        "{%- if reasoning_content %}\n"
        "            {{- '<|im_start|>' + message.role + '\\n<think>\\n' + reasoning_content + '\\n</think>\\n\\n' + content }}\n"
        "        {%- elif loop.index0 > ns.last_query_index %}\n"
        "            {{- '<|im_start|>' + message.role + '\\n<think>\\n\\n</think>\\n\\n' + content }}\n"
        "        {%- else %}\n"
        "            {{- '<|im_start|>' + message.role + '\\n' + content }}\n"
        "        {%- endif %}"
    )

    if old in template:
        tokenizer.chat_template = template.replace(old, new)
        print("  Patched Qwen 3.5 chat template: <think> now preserved in all turns")
    else:
        print("  WARNING: chat template patch target not found — template may have changed")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_kaetram_dataset(train_bytes: bytes, val_bytes: bytes, metadata_bytes: bytes, tokenizer):
    """Load Kaetram SFT data and format with the chat template.

    Records contain only gameplay messages (no system prompt or tools).
    System prompt and tool definitions are injected from metadata.
    """
    import json

    _patch_qwen_chat_template(tokenizer)

    metadata = json.loads(metadata_bytes)
    system_prompt = metadata["system_prompt"]
    tool_definitions = metadata["tools"]
    personality_suffixes = metadata.get("personality_suffixes", {})

    def parse_and_format(raw_bytes, augment_rng=None):
        records = json.loads(raw_bytes)
        rows = []
        for rec in records:
            # Reconstruct system message with personality (+ paraphrase for training)
            personality = rec.get("personality")
            sys_content = _build_system_prompt(
                system_prompt, personality, personality_suffixes, augment_rng
            )

            messages = [{"role": "system", "content": sys_content}]

            for msg in rec["messages"]:
                m = {"role": msg["role"]}

                # Handle content (may be string, list, or absent for tool-call-only)
                content = msg.get("content")
                if isinstance(content, list):
                    m["content"] = "\n".join(
                        b.get("text", "") for b in content if isinstance(b, dict)
                    )
                elif isinstance(content, str):
                    m["content"] = content
                elif content is None and "tool_calls" not in msg:
                    m["content"] = ""

                # Handle tool_calls (assistant messages calling MCP tools)
                if "tool_calls" in msg:
                    tool_calls = []
                    for tc in msg["tool_calls"]:
                        tc = dict(tc)
                        if "function" in tc:
                            func = dict(tc["function"])
                            args = func.get("arguments", {})
                            if isinstance(args, str):
                                func["arguments"] = json.loads(args)
                            tc["function"] = func
                        tool_calls.append(tc)
                    m["tool_calls"] = tool_calls

                # Handle tool results
                if "tool_call_id" in msg:
                    m["tool_call_id"] = msg["tool_call_id"]
                if "name" in msg and msg["role"] == "tool":
                    m["name"] = msg["name"]

                messages.append(m)

            # Apply chat template with tools
            try:
                formatted = tokenizer.apply_chat_template(
                    messages,
                    tools=tool_definitions,
                    tokenize=False,
                    add_generation_prompt=False,
                )
            except TypeError:
                formatted = tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=False,
                )
            rows.append({"text": formatted})
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
    gpu="H100",  # 80GB VRAM — bf16 LoRA on 9B fits easily
    timeout=18 * 3600,  # 18 hours (r7: 402 steps × ~2min/step ≈ 14h)
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

    # Load model with Unsloth — bf16, NOT 4-bit (QLoRA not recommended for Qwen3.5)
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
        use_rslora=False,  # rsLoRA diverged at r=64/alpha=64 (8x effective LR)
        use_gradient_checkpointing="unsloth",  # Unsloth optimized — lower VRAM
        random_state=42,
    )

    # Load and format dataset
    print("Loading dataset...")
    train_ds, val_ds = load_kaetram_dataset(train_data, val_data, metadata, tokenizer)
    print(f"Train: {len(train_ds)} records, Val: {len(val_ds)} records")

    # SFTConfig — loss masking applied via train_on_responses_only (see below).
    # completion_only_loss=True was broken here: it only works with prompt+completion fields,
    # not with dataset_text_field="text" (no response_template → silently no-ops).
    # Fix: use Unsloth's train_on_responses_only after trainer init (r8, KAE-25).
    # Ref: Structured Agent Distillation arxiv 2505.13820
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
        max_seq_length=MAX_SEQ_LEN,
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

    # Apply response-only loss masking: mask user/system/tool tokens, train on assistant turns only.
    # Qwen3.5 chat format: <|im_start|>user\n ... <|im_end|>\n<|im_start|>assistant\n ...
    # This is the correct fix for completion_only_loss being broken with dataset_text_field="text".
    if MASK_INPUT_TOKENS:
        print("Applying train_on_responses_only (assistant turns only)")
        trainer = train_on_responses_only(
            trainer,
            instruction_part="<|im_start|>user\n",
            response_part="<|im_start|>assistant\n",
        )

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
def main():
    """Upload training data and launch the finetune job."""
    import os
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
