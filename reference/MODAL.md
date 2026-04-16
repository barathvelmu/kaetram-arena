# Modal Infrastructure Reference

> All training, serving, and evaluation for the Kaetram Qwen3.5-9B agent runs on [Modal](https://modal.com). This doc covers the complete setup.

---

## Quick Commands

```bash
# Training
modal run finetune/train_modal.py              # SFT (H100, ~18h)
modal run finetune/train_grpo_modal.py         # GRPO (H100, ~6h)
modal run finetune/train_kto_modal.py          # KTO (H100, ~8h)

# Merge a checkpoint (without full training)
modal run finetune/train_modal.py::merge_checkpoint --checkpoint-name checkpoint-150

# Serving
modal deploy finetune/serve_modal.py           # Deploy finetuned model
modal deploy finetune/serve_modal_base.py      # Deploy base model

# Stop endpoints (save $)
modal app stop kaetram-qwen-serve
modal app stop kaetram-qwen-base

# Check running apps
modal app list
```

---

## Files

| File | Purpose |
|------|---------|
| `finetune/train_modal.py` | SFT training (LoRA on H100) |
| `finetune/train_grpo_modal.py` | GRPO reinforcement learning |
| `finetune/train_kto_modal.py` | KTO preference learning |
| `finetune/serve_modal.py` | SGLang serving for finetuned model (A100) |
| `finetune/serve_modal_base.py` | SGLang serving for base Qwen3.5-9B (A100) |
| `play_qwen.py` | Inference client (calls Modal endpoints via OpenAI SDK) |
| `eval_harness.py` | Eval orchestrator (spawns play_qwen.py against both endpoints) |
| `scripts/run-eval.sh` | Eval launcher (parallel base vs SFT comparison) |

---

## Modal Apps

| App Name | File | GPU | Purpose |
|----------|------|-----|---------|
| `kaetram-qwen-finetune` | train_modal.py | H100 80GB | SFT training |
| `kaetram-qwen-grpo` | train_grpo_modal.py | H100 80GB | GRPO training |
| `kaetram-qwen-kto` | train_kto_modal.py | H100 80GB | KTO training |
| `kaetram-qwen-serve` | serve_modal.py | A100 40GB | Finetuned model inference |
| `kaetram-qwen-base` | serve_modal_base.py | A100 40GB | Base model inference |

---

## Modal Volumes (Persistent Storage)

| Volume Name | Mount Path | Contents |
|-------------|-----------|----------|
| `kaetram-model-cache` | `/model_cache` | HuggingFace model weights cache |
| `kaetram-model-vol` | `/checkpoints` | Training checkpoints, LoRA adapters, merged models |

Volume structure:
```
/checkpoints/
  kaetram-qwen3.5-9b-r9/
    adapter/              # LoRA adapter weights
    merged/               # Full merged safetensors (for SGLang serving)
    training_metrics.json # Loss curves, eval results
    checkpoint-50/        # Intermediate checkpoints (save_steps=50)
    checkpoint-100/
    checkpoint-150/
```

Volumes persist across container restarts. Checkpoints saved during training survive timeouts.

---

## SFT Training (train_modal.py)

### Model & LoRA Config

| Parameter | Value | Notes |
|-----------|-------|-------|
| Base model | `unsloth/Qwen3.5-9B` | Unsloth-optimized, Apache 2.0 |
| MAX_SEQ_LEN | 16,384 | r9: bumped from 8192 to fit larger system prompt |
| LoRA rank (r) | 64 | Increased from 16 in round 2 |
| LoRA alpha | 64 | alpha = r recommended for Qwen3.5 |
| LoRA targets | q/k/v/o/gate/up/down_proj | All attention + MLP projections |
| use_rslora | False | rsLoRA diverged at r=64/alpha=64 (8x effective LR trap) |
| Gradient checkpointing | "unsloth" | Unsloth-optimized, lower VRAM |

### Training Hyperparameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| Batch size | 2 | Per device |
| Gradient accumulation | 8 | Effective batch = 16 |
| Learning rate | 1e-4 | |
| LR scheduler | Cosine | |
| Warmup ratio | 0.05 | |
| Weight decay | 0.01 | |
| Epochs | 1 | Loss converges within 1 epoch |
| Save steps | 50 | Checkpoints to Modal volume |
| Eval steps | 50 | Validation loss check |
| Logging steps | 10 | Loss to stdout |
| save_total_limit | 3 | Keeps last 3 checkpoints |

### Loss Masking

Uses Unsloth's `train_on_responses_only()`:
- `instruction_part="<|im_start|>user\n"` — masks user messages + tool results
- `response_part="<|im_start|>assistant\n"` — trains on assistant responses only
- Qwen3.5 renders `role:tool` as `<|im_start|>user` with `<tool_response>` wrapper, so tool results are correctly masked

### Chat Template Patch

Qwen3.5's stock template strips `<think>` reasoning from intermediate assistant turns (only keeps it on the last turn). `_patch_qwen_chat_template()` fixes this to preserve reasoning on all turns. Verified working with `unsloth/Qwen3.5-9B` tokenizer (transformers 5.5.3).

### Data Augmentation

- **System prompt intro**: 4 paraphrase variants (training only, validation uses original)
- **Personality suffixes**: 3 types × 3 variants each (aggressive, methodical, curious)
- **Body split**: `<game_knowledge>` marker — everything after is kept identical

### Container Image

```
Base: nvidia/cuda:12.6.3-devel-ubuntu22.04
Python: 3.11
Key packages: unsloth[cu128-torch270]>=2025.7.8, transformers>=5.0.0, trl>=0.19.1
flash-attn: compiled from source (needs nvcc from devel image)
```

### Timeout & Cost

| Parameter | Value |
|-----------|-------|
| Timeout | 18 hours |
| GPU | H100 80GB (~$3.95/hr) |
| Typical duration | 12-18h for ~367 steps |
| Typical cost | $50-70 |

**Cost note**: r9 at MAX_SEQ_LEN=16384 runs ~5.5 min/step (vs r8 at 8192 = ~2 min/step) due to gradient offloading overhead. Budget accordingly.

### Training Data Input

Reads from local disk, uploads as bytes to Modal:
- `dataset/qwen_sft/train.json` — training records
- `dataset/qwen_sft/val.json` — validation records  
- `dataset/qwen_sft/metadata.json` — system prompt, tool definitions, personality suffixes

### Email Notifications

Sends email on start/finish/failure via Modal Secrets (`notification_env()`). Includes loss summary and duration.

---

## Serving (serve_modal.py)

### SGLang Engine Config

| Parameter | Value |
|-----------|-------|
| GPU | A100 40GB |
| dtype | bfloat16 |
| context_length | 32,768 |
| mem_fraction_static | 0.92 |
| Min containers | 1 (always warm) |
| Scaledown window | 600s (10 min idle) |
| Request timeout | 300s |

### Model Loading Priority

The serving endpoint checks these locations in order:
1. Cached merged model at `/model_cache/kaetram-merged-r8/`
2. GRPO merged at `/checkpoints/kaetram-qwen3.5-9b-grpo/merged/`
3. SFT merged at `/checkpoints/kaetram-qwen3.5-9b-r8/merged/`
4. Adapter-only (load base + merge adapter on startup)
5. Base model fallback

### API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Status + model name |
| `/v1/models` | GET | List available models |
| `/v1/chat/completions` | POST | Main inference (OpenAI-compatible) |

### Tool Call Parsing

The endpoint parses Qwen3.5 Coder XML from model output:
```xml
<tool_call>
<function=attack>
<parameter=mob_name>Rat</parameter>
</function>
</tool_call>
```

Returns structured `tool_calls` array in OpenAI response format.

### Endpoint URLs

| Model | URL |
|-------|-----|
| Finetuned | `https://patnir411--kaetram-qwen-serve-inference-serve.modal.run/v1` |
| Base | `https://patnir411--kaetram-qwen-base-inference-serve.modal.run/v1` |

---

## Base Model Serving (serve_modal_base.py)

Same as finetuned serving but:
- Always loads `Qwen/Qwen3.5-9B` (no checkpoint logic)
- `min_containers=0` (scales to zero when idle, cheaper)
- `scaledown_window=300s` (5 min)
- Model ID: `"kaetram-base"`

---

## Evaluation Flow

```
run-eval.sh
  ├─ eval_harness.py --models r8-sft=<modal-url> (port 9001)
  └─ eval_harness.py --models base=<modal-url>   (port 9041)
       ├─ Per episode: reset MongoDB → spawn play_qwen.py → collect logs
       ├─ Sub-session continuation (restart every ~30 turns, preserve DB)
       └─ Compute metrics from logs → results.json
```

### Eval Output

```
dataset/eval/runs/
  YYYYMMDD_HHMMSS_[personality]/
    r8-sft/results.json
    r8-sft/episode_001.jsonl
    base/results.json
    base/episode_001.jsonl
  latest → (symlink)
```

### Scenarios

| ID | Name | Turns | Success Criteria |
|----|------|-------|------------------|
| A | Rat Grind | 100 | ≥5 rats killed |
| B | Snek Quest | 200 | ≥1 quest completed |
| C | Multi-Zone | 150 | ≥2 warps |
| D | Open Play | 300 | >10 turns + >50% parse rate |

---

## Checkpoint Management

### Merging a Checkpoint

If training times out, merge an intermediate checkpoint:

```bash
# List what's on the volume
modal volume ls kaetram-model-vol /checkpoints/kaetram-qwen3.5-9b-r9/

# Merge checkpoint-150 into full model
modal run finetune/train_modal.py::merge_checkpoint --checkpoint-name checkpoint-150
```

This produces merged safetensors at `/checkpoints/{experiment}/merged/` ready for `serve_modal.py`.

### Deploying After Merge

```bash
# Update serve_modal.py model loading path if needed, then:
modal deploy finetune/serve_modal.py

# Test
curl https://patnir411--kaetram-qwen-serve-inference-serve.modal.run/health
```

### Resuming Training

Not built in currently. To resume from a checkpoint:
1. Load model + LoRA adapter from checkpoint directory
2. Pass `resume_from_checkpoint` to SFTTrainer
3. Requires ~10 lines of code change to `train_modal.py`

---

## Cost Summary

| Operation | GPU | Duration | Cost |
|-----------|-----|----------|------|
| SFT training (r9) | H100 | 18h (timeout) | ~$70 |
| GRPO training | H100 | 6h | ~$24 |
| KTO training | H100 | 8h | ~$32 |
| Checkpoint merge | H100 | 30 min | ~$2 |
| Finetuned serving (warm) | A100 | per hour | ~$1.10/hr |
| Base serving (idle) | A100 | 0 when idle | $0 idle, ~$1.10/hr active |
| Eval run (3 ep × 2 models) | A100 | ~4h | ~$4.40 |

**Cost optimization**: Stop serving endpoints when not evaluating (`modal app stop`). Base model uses `min_containers=0` so it costs nothing when idle.

---

## Experiment History

| Experiment | Key Change | Dataset | Result |
|------------|-----------|---------|--------|
| r4 | First working LoRA | ~2K records | Baseline |
| r5-r7 | Broken loss masking (`completion_only_loss` silently ignored) | 6,423 | Trained on ALL tokens |
| r8 | Fixed loss masking (`train_on_responses_only`) but wrong system prompt | 6,380 | Worse than base |
| **r9** | Correct system prompt, 100% reasoning, filtered data, no double tool defs | 5,871 | In progress |

### r8 → r9 Fixes

| Issue | r8 | r9 |
|-------|----|----|
| System prompt | Wrong 50-line condensed prompt with fake tool names | Correct `system.md` + `game_knowledge.md` (11K chars) |
| Reasoning | 31% of turns had `<think>` | 100% |
| Tool definitions | Double (markdown table + `tools=` kwarg) | Single (markdown table only) |
| `<memory>` blocks | Present (mismatched inference) | Removed |
| Degenerate data | 255 click_tile spam + 309 stuck loops | Filtered out |
| MAX_SEQ_LEN | 8,192 (55% truncated) | 16,384 |
| EXPERIMENT_NAME | r8 | r9 |

---

## Known Issues & Gotchas

1. **rsLoRA trap**: `use_rslora=True` with `r=alpha=64` gives 8x effective LR. Keep `use_rslora=False`.
2. **Qwen3.5 chat template `<think>` stripping**: Stock template drops intermediate-turn reasoning. Must patch via `_patch_qwen_chat_template()`. Verified against `unsloth/Qwen3.5-9B` tokenizer.
3. **MAX_SEQ_LEN=16384 is slow**: ~5.5 min/step on H100 due to gradient offloading. 8192 was ~2 min/step. Consider batch_size=1 + grad_accum=16 for future runs.
4. **5 tools have zero training examples**: `gather`, `loot`, `buy_item`, `drop_item`, `query_quest` — added to MCP after bulk data collection. Need new collection runs to cover these.
5. **`tools=` removed from `apply_chat_template`**: r9 intentionally does NOT pass `tools=` to avoid double tool definitions. Tools are defined only in the markdown `<tools>` table in the system prompt.
6. **Modal volume commit**: `checkpoint_vol.commit()` is called after training completes. If container is killed mid-save, the latest checkpoint may be incomplete — previous checkpoints are safe.
