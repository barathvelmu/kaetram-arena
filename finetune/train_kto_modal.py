"""
Modal KTO training script for Kaetram agent preference optimization.

Runs Kahneman-Tversky Optimization on top of an SFT checkpoint (typically r6)
using binary desirable/undesirable labels derived from Claude trajectories.

Usage:
    python3 score_sessions.py --input dataset/extracted/ --output dataset/qwen_kto/session_scores.json
    python3 build_kto_dataset.py --input dataset/extracted/ --scores dataset/qwen_kto/session_scores.json --output dataset/qwen_kto/
    modal run finetune/train_kto_modal.py
"""

import pathlib

import modal
from notifications import format_notification, notification_env

app = modal.App("kaetram-qwen-kto")
_notify_env = notification_env()
_notification_secrets = [modal.Secret.from_dict(_notify_env)] if _notify_env else []

model_cache_vol = modal.Volume.from_name("kaetram-model-cache", create_if_missing=True)
checkpoint_vol = modal.Volume.from_name("kaetram-model-vol", create_if_missing=True)

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
    .run_commands("pip install flash-attn --no-build-isolation")
    .env({"HF_HOME": "/model_cache", "TOKENIZERS_PARALLELISM": "false"})
    .add_local_python_source("notifications")
)

with train_image.imports():
    import unsloth  # noqa: F401,I001
    import datasets
    from trl import KTOConfig, KTOTrainer
    from unsloth import FastLanguageModel


MODEL_ID = "unsloth/Qwen3.5-9B"
# Use the canonical HF tokenizer for chat template formatting — NOT the Unsloth-merged
# r6 tokenizer. Unsloth's save_pretrained_merged modifies the chat_template Jinja to
# inject a tool-system-doc block when tool_calls appear in the full conversation, but
# not in prompt-only renders. This causes _split_completion to fail for every record.
# The canonical tokenizer has the same vocab so tokenization is identical; only the
# template rendering differs.
TEMPLATE_TOKENIZER_ID = "Qwen/Qwen3.5-9B"
BASE_SFT_EXPERIMENT = "kaetram-qwen3.5-9b-r6-optimized"
EXPERIMENT_NAME = "kaetram-qwen3.5-9b-r6-kto"
MAX_SEQ_LEN = 8192
MAX_PROMPT_LEN = 7168
MAX_COMPLETION_LEN = 1024

LORA_R = 64
LORA_ALPHA = 64
LORA_TARGETS = [
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj",
]

BATCH_SIZE = 4
GRAD_ACCUM = 4
LR = 5e-7
WARMUP_RATIO = 0.1
WEIGHT_DECAY = 0.0
EPOCHS = 1
LOGGING_STEPS = 10
SAVE_STEPS = 50
EVAL_STEPS = 50
BETA = 0.1


def _split_completion(prompt_text: str, full_text: str) -> str | None:
    """Split full_text into completion by removing prompt prefix.

    Returns None if the texts diverge — callers should skip the record rather
    than train on a silently wrong split.
    """
    if full_text.startswith(prompt_text):
        return full_text[len(prompt_text):]
    # Template rendered differently — don't guess, skip instead
    print(f"WARNING: full_text does not start with prompt_text (len {len(prompt_text)} vs {len(full_text)}), skipping record")
    return None


def load_kto_dataset(train_bytes: bytes, val_bytes: bytes, metadata_bytes: bytes, tokenizer):
    import json
    from transformers import AutoTokenizer

    metadata = json.loads(metadata_bytes)
    tool_definitions = metadata["tools"]

    # Load the canonical HF tokenizer for chat template formatting only.
    # The Unsloth-merged r6 tokenizer has a modified chat_template that injects
    # tool-system-doc tokens when tool_calls appear in the full conversation but
    # not in prompt-only renders — causing _split_completion to fail for all records.
    # The canonical tokenizer has the same vocabulary so tokenization is identical.
    print(f"Loading template tokenizer ({TEMPLATE_TOKENIZER_ID}) for consistent chat template rendering...")
    fmt_tok = AutoTokenizer.from_pretrained(TEMPLATE_TOKENIZER_ID, trust_remote_code=True)

    def parse_and_format(raw_bytes, split_name: str):
        records = json.loads(raw_bytes)
        rows = []
        skipped = 0
        for rec in records:
            prompt_messages = rec["prompt_messages"]
            completion_message = rec["completion_message"]

            try:
                prompt_text = fmt_tok.apply_chat_template(
                    prompt_messages,
                    tools=tool_definitions,
                    tokenize=False,
                    add_generation_prompt=True,
                )
            except TypeError:
                prompt_text = fmt_tok.apply_chat_template(
                    prompt_messages,
                    tokenize=False,
                    add_generation_prompt=True,
                )

            full_messages = prompt_messages + [completion_message]
            try:
                full_text = fmt_tok.apply_chat_template(
                    full_messages,
                    tools=tool_definitions,
                    tokenize=False,
                    add_generation_prompt=False,
                )
            except TypeError:
                full_text = fmt_tok.apply_chat_template(
                    full_messages,
                    tokenize=False,
                    add_generation_prompt=False,
                )

            completion_text = _split_completion(prompt_text, full_text)
            if completion_text is None:
                skipped += 1
                continue
            rows.append(
                {
                    "prompt": prompt_text,
                    "completion": completion_text,
                    "label": bool(rec["label"]),
                    "session": rec.get("session"),
                    "session_score": rec.get("session_score", 0.0),
                    "window_score": rec.get("window_score", 0.0),
                }
            )

        if skipped:
            print(f"WARNING [{split_name}]: skipped {skipped}/{len(records)} records due to prompt/completion mismatch")
            if skipped > len(records) * 0.05:
                raise RuntimeError(f"Too many skipped records in {split_name} ({skipped}/{len(records)}) — chat template may have drifted")
        return datasets.Dataset.from_list(rows), skipped

    train_ds, train_skipped = parse_and_format(train_bytes, "train")
    val_ds, val_skipped = parse_and_format(val_bytes, "val")
    return train_ds, val_ds, metadata, {"train_skipped": train_skipped, "val_skipped": val_skipped}, fmt_tok


@app.function(
    image=train_image,
    gpu="H100",
    timeout=8 * 3600,
    volumes={
        "/model_cache": model_cache_vol,
        "/checkpoints": checkpoint_vol,
    },
    secrets=_notification_secrets,
)
def train(train_data: bytes, val_data: bytes, metadata: bytes, smoke_test: bool = False):
    import json
    import os
    from notifications import send_email_notification

    base_model_path = f"/checkpoints/{BASE_SFT_EXPERIMENT}/merged"
    if not os.path.exists(base_model_path):
        raise FileNotFoundError(
            f"Base SFT merged model not found: {base_model_path}\n"
            f"Expected to run KTO on top of {BASE_SFT_EXPERIMENT}."
        )

    print(f"Loading base SFT model from {base_model_path}...")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=base_model_path,
        max_seq_length=MAX_SEQ_LEN,
        load_in_4bit=False,
        load_in_16bit=True,
    )

    # Load explicit reference model as plain HuggingFace — avoids Unsloth PEFT internals
    # interacting with TRL's create_reference_model(). TRL gets a clean standard model,
    # Unsloth handles only the training model. No interaction between them.
    print(f"Loading reference model from {base_model_path} (standard HF, frozen)...")
    import torch
    from transformers import AutoModelForCausalLM
    ref_model = AutoModelForCausalLM.from_pretrained(
        base_model_path,
        torch_dtype=torch.bfloat16,
        device_map="cuda",
    )
    ref_model.eval()
    for p in ref_model.parameters():
        p.requires_grad_(False)

    print("Configuring LoRA adapter for KTO...")
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

    print("Loading KTO dataset...")
    train_ds, val_ds, meta, skip_stats, fmt_tok = load_kto_dataset(train_data, val_data, metadata, tokenizer)

    # --- Dataset sanity checks ---
    train_des = sum(1 for row in train_ds if row["label"])
    train_udes = len(train_ds) - train_des
    val_des = sum(1 for row in val_ds if row["label"])
    val_udes = len(val_ds) - val_des
    train_sessions = len(set(row["session"] for row in train_ds if row["session"]))
    val_sessions = len(set(row["session"] for row in val_ds if row["session"]))
    avg_prompt_chars = sum(len(row["prompt"]) for row in train_ds) / max(1, len(train_ds))
    avg_completion_chars = sum(len(row["completion"]) for row in train_ds) / max(1, len(train_ds))

    print(f"\n{'='*50}")
    print("DATASET SANITY")
    print(f"{'='*50}")
    print(f"  Train:  {len(train_ds)} records | {train_des} desirable ({100*train_des/max(1,len(train_ds)):.1f}%) | {train_udes} undesirable | {train_sessions} sessions | {skip_stats['train_skipped']} skipped")
    print(f"  Val:    {len(val_ds)} records | {val_des} desirable ({100*val_des/max(1,len(val_ds)):.1f}%) | {val_udes} undesirable | {val_sessions} sessions | {skip_stats['val_skipped']} skipped")
    print(f"  Avg prompt: {avg_prompt_chars:.0f} chars | Avg completion: {avg_completion_chars:.0f} chars")
    print(f"{'='*50}\n")

    if train_des == 0 or train_udes == 0:
        raise RuntimeError(f"KTO requires both classes in train set. Got desirable={train_des}, undesirable={train_udes}. Loosen score_sessions.py thresholds.")
    if val_des == 0 or val_udes == 0:
        raise RuntimeError(f"KTO requires both classes in val set. Got desirable={val_des}, undesirable={val_udes}. Increase val ratio or session count.")
    if len(train_ds) < 50:
        raise RuntimeError(f"Train set too small after formatting: {len(train_ds)} records. Need at least 50.")
    if len(val_ds) < 10:
        raise RuntimeError(f"Val set too small after formatting: {len(val_ds)} records. Need at least 10.")

    desirable = train_des
    undesirable = train_udes
    desirable_weight = 1.0
    undesirable_weight = 1.0
    if desirable and undesirable:
        if desirable > undesirable:
            undesirable_weight = min(desirable / undesirable, 3.0)
        else:
            desirable_weight = min(undesirable / desirable, 3.0)

    if smoke_test:
        print("SMOKE TEST MODE: max_steps=10, eval/save every 5, logging every 1")

    output_dir = f"/checkpoints/{EXPERIMENT_NAME}"
    kto_config = KTOConfig(
        output_dir=output_dir,
        num_train_epochs=EPOCHS,
        max_steps=10 if smoke_test else -1,
        per_device_train_batch_size=BATCH_SIZE,
        per_device_eval_batch_size=BATCH_SIZE,
        gradient_accumulation_steps=GRAD_ACCUM,
        learning_rate=LR,
        lr_scheduler_type="cosine",
        warmup_ratio=WARMUP_RATIO,
        weight_decay=WEIGHT_DECAY,
        logging_steps=1 if smoke_test else LOGGING_STEPS,
        eval_strategy="steps",
        eval_steps=5 if smoke_test else EVAL_STEPS,
        save_strategy="steps",
        save_steps=5 if smoke_test else SAVE_STEPS,
        save_total_limit=3,
        report_to="none",
        seed=42,
        bf16=True,
        optim="adamw_8bit",
        beta=BETA,
        max_length=MAX_SEQ_LEN,
        max_prompt_length=MAX_PROMPT_LEN,
        max_completion_length=MAX_COMPLETION_LEN,
        desirable_weight=desirable_weight,
        undesirable_weight=undesirable_weight,
    )

    print(
        "Initializing KTOTrainer... "
        f"(desirable={desirable}, undesirable={undesirable}, "
        f"weights={desirable_weight:.2f}/{undesirable_weight:.2f})"
    )
    trainer = KTOTrainer(
        model=model,
        ref_model=ref_model,
        args=kto_config,
        train_dataset=train_ds,
        eval_dataset=val_ds,
        processing_class=fmt_tok,  # base tokenizer — Unsloth-merged r6 tokenizer routes through Qwen3-VL processor
    )

    subject, body = format_notification(
        "Kaetram KTO Training Started",
        [
            f"Experiment: {EXPERIMENT_NAME}",
            f"Base SFT: {BASE_SFT_EXPERIMENT}",
            f"Mode: {'smoke-test' if smoke_test else 'full'}",
            f"Train records: {len(train_ds)}",
            f"Val records: {len(val_ds)}",
            f"Desirable/undesirable: {desirable}/{undesirable}",
        ],
    )
    send_email_notification(subject, body)

    print("Starting KTO training...")
    try:
        result = trainer.train()
    except Exception as e:
        subject, body = format_notification(
            "Kaetram KTO Training Failed",
            [
                f"Experiment: {EXPERIMENT_NAME}",
                f"Base SFT: {BASE_SFT_EXPERIMENT}",
                f"Mode: {'smoke-test' if smoke_test else 'full'}",
                f"Error: {type(e).__name__}: {e}",
            ],
        )
        send_email_notification(subject, body)
        raise
    print(f"KTO complete: {result.metrics}")

    adapter_dir = f"{output_dir}/adapter"
    merged_dir = f"{output_dir}/merged"
    print(f"Saving adapter to {adapter_dir}...")
    model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)

    print(f"Saving merged safetensors to {merged_dir}...")
    model.save_pretrained_merged(merged_dir, tokenizer, save_method="merged_16bit")

    metrics = {
        "train_loss": result.metrics.get("train_loss"),
        "train_runtime": result.metrics.get("train_runtime"),
        "epochs": EPOCHS,
        "train_records": len(train_ds),
        "val_records": len(val_ds),
        "base_sft_experiment": BASE_SFT_EXPERIMENT,
        "experiment_name": EXPERIMENT_NAME,
        "beta": BETA,
        "learning_rate": LR,
        "desirable": desirable,
        "undesirable": undesirable,
        "desirable_weight": desirable_weight,
        "undesirable_weight": undesirable_weight,
        "max_seq_len": MAX_SEQ_LEN,
        "max_prompt_len": MAX_PROMPT_LEN,
        "max_completion_len": MAX_COMPLETION_LEN,
        "metadata": {
            "train_sessions": meta.get("train_sessions"),
            "val_sessions": meta.get("val_sessions"),
            "window_size": meta.get("window_size"),
            "stride": meta.get("stride"),
        },
    }
    with open(f"{output_dir}/training_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)

    checkpoint_vol.commit()

    subject, body = format_notification(
        "Kaetram KTO Training Finished",
        [
            f"Experiment: {EXPERIMENT_NAME}",
            f"Base SFT: {BASE_SFT_EXPERIMENT}",
            f"Mode: {'smoke-test' if smoke_test else 'full'}",
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
    print(f"\nDeploy serving endpoint after updating serve_modal.py if needed.")
    return metrics


@app.local_entrypoint()
def main(smoke_test: bool = False):
    """Launch KTO training on Modal.

    Args:
        smoke_test: Run 10 steps only to verify trainer initializes, batches load,
                    metrics log, and checkpoint saves. Use before a full run.
                    modal run finetune/train_kto_modal.py --smoke-test
    """
    import os
    from notifications import send_email_notification

    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    train_path = os.path.join(project_dir, "dataset", "qwen_kto", "train.json")
    val_path = os.path.join(project_dir, "dataset", "qwen_kto", "val.json")
    metadata_path = os.path.join(project_dir, "dataset", "qwen_kto", "metadata.json")

    if not os.path.exists(train_path):
        raise FileNotFoundError(
            f"KTO training data not found: {train_path}\n"
            "Run: python3 build_kto_dataset.py --input dataset/extracted/ --scores dataset/qwen_kto/session_scores.json --output dataset/qwen_kto/"
        )
    if not os.path.exists(metadata_path):
        raise FileNotFoundError(f"Metadata not found: {metadata_path}")

    with open(train_path, "rb") as f:
        train_data = f.read()
    with open(val_path, "rb") as f:
        val_data = f.read()
    with open(metadata_path, "rb") as f:
        metadata = f.read()

    print("Uploading KTO dataset...")
    print(f"  Train: {len(train_data):,} bytes")
    print(f"  Val:   {len(val_data):,} bytes")
    print(f"  Metadata: {len(metadata):,} bytes")
    print(f"  Base SFT: {BASE_SFT_EXPERIMENT}")
    if smoke_test:
        print("  Mode: SMOKE TEST (10 steps)")
    print(f"Launching on Modal H100...")

    subject, body = format_notification(
        "Kaetram KTO Training Launched",
        [
            f"Experiment: {EXPERIMENT_NAME}",
            f"Base SFT: {BASE_SFT_EXPERIMENT}",
            f"Mode: {'smoke-test' if smoke_test else 'full'}",
            f"Train bytes: {len(train_data):,}",
            f"Val bytes: {len(val_data):,}",
            f"Metadata bytes: {len(metadata):,}",
        ],
    )
    send_email_notification(subject, body)

    metrics = train.remote(train_data, val_data, metadata, smoke_test=smoke_test)

    print(f"\n{'='*60}")
    print("KTO TRAINING COMPLETE")
    print(f"{'='*60}")
    print(f"  Loss:     {metrics.get('train_loss', '?'):.4f}")
    print(f"  Runtime:  {metrics.get('train_runtime', 0):.0f}s")
    print(f"  Records:  {metrics.get('train_records')} train / {metrics.get('val_records')} val")
