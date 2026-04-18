"""
Modal serving endpoint for BASE (unfinetuned) Qwen3.5-9B.

Used as the baseline comparison against finetuned r7-SFT and r7-KTO models.
Same architecture as serve_modal.py but always loads the base model.

Usage:
    modal deploy finetune/serve_modal_base.py
    # Endpoint: https://patnir411--kaetram-qwen-base-inference-serve.modal.run/v1
"""

import modal

app = modal.App("kaetram-qwen-base")

model_cache_vol = modal.Volume.from_name("kaetram-model-cache", create_if_missing=True)

serve_image = (
    modal.Image.from_registry("nvidia/cuda:12.8.0-devel-ubuntu22.04", add_python="3.11")
    .apt_install("libnuma-dev")
    .pip_install(
        "sglang[all]>=0.5.5",
        "huggingface_hub>=0.34.2",
        "hf-transfer>=0.1.9",
    )
    .env({
        "HF_HOME": "/model_cache",
        "TOKENIZERS_PARALLELISM": "false",
        "HF_HUB_ENABLE_HF_TRANSFER": "1",
        "SGLANG_DISABLE_CUDNN_CHECK": "1",
    })
)

BASE_MODEL_ID = "Qwen/Qwen3.5-9B"
MAX_MODEL_LEN = 32768
GPU_MEMORY_UTILIZATION = 0.92
DTYPE = "bfloat16"

# Qwen3.5-9B thinking mode general defaults (per official model card).
# Matched to serve_modal.py so base vs finetuned comparison uses identical decode config.
# Do NOT enable repetition_penalty / frequency_penalty / DRY — they hurt tool-call JSON.
QWEN_THINK_TEMP = 1.0
QWEN_THINK_TOP_P = 0.95
QWEN_THINK_TOP_K = 20
QWEN_THINK_PRESENCE_PENALTY = 1.5
QWEN_DECODE_MODE = "thinking_general"


def _patch_qwen_chat_template(tokenizer):
    """Patch Qwen 3.5 chat template to preserve <think> in all turns."""
    template = tokenizer.chat_template
    if template is None:
        return
    old = (
        "{%- if loop.index0 > ns.last_query_index %}\n"
        "            {{- '<|im_start|>' + message.role + '\\n<think>\\n' + reasoning_content + '\\n</think>\\n\\n' + content }}\n"
        "        {%- else %}\n"
        "            {{- '<|im_start|>' + message.role + '\\n' + content }}\n"
        "        {%- endif %}"
    )
    new = (
        "{%- if reasoning_content %}\n"
        "            {{- '<|im_start|>' + message.role + '\\n<think>\\n' + reasoning_content + '\\n</think>\\n\\n' + content }}\n"
        "        {%- elif loop.index0 > ns.last_query_index %}\n"
        "            {{- '<|im_start|>' + message.role + '\\n<think>\\n\\n</think>\\n\\n' + content }}\n"
        "        {%- else %}\n"
        "            {{- '<|im_start|>' + message.role + '\\n' + content }}\n"
        "        {%- endif %}"
    )
    if old not in template:
        raise RuntimeError(
            "Qwen 3.5 chat template patch target not found — tokenizer revision has changed "
            "the reasoning_content stripping block. Inspect tokenizer.chat_template, update the "
            "`old` pattern, and re-verify <think> survives in multi-turn apply_chat_template output."
        )
    tokenizer.chat_template = template.replace(old, new)
    print("  Patched Qwen 3.5 chat template: <think> now preserved in all turns")


@app.cls(
    image=serve_image,
    gpu="A100",
    volumes={"/model_cache": model_cache_vol},
    min_containers=1,
    max_containers=1,
    scaledown_window=600,
    timeout=300,
)
class Inference:
    @modal.enter()
    def load_model(self):
        """Load the BASE Qwen3.5-9B (no finetuning)."""
        print(f"Loading BASE model {BASE_MODEL_ID}...")
        self.loaded_model_path = BASE_MODEL_ID
        import sglang as sgl

        self.engine = sgl.Engine(
            model_path=BASE_MODEL_ID,
            tokenizer_path=BASE_MODEL_ID,
            dtype=DTYPE,
            context_length=MAX_MODEL_LEN,
            mem_fraction_static=GPU_MEMORY_UTILIZATION,
            trust_remote_code=True,
            disable_cuda_graph=True,
        )
        from transformers import AutoTokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL_ID, trust_remote_code=True)
        _patch_qwen_chat_template(self.tokenizer)
        print("SGLang engine ready (BASE model).")

    @modal.asgi_app()
    def serve(self):
        from fastapi import FastAPI, Request
        import time
        import uuid

        web_app = FastAPI()

        @web_app.get("/health")
        async def health():
            return {
                "status": "ok",
                "model": BASE_MODEL_ID,
                "variant": "base",
                "loaded_model_path": getattr(self, "loaded_model_path", None),
                "decode_mode": QWEN_DECODE_MODE,
                "decode_defaults": {
                    "temperature": QWEN_THINK_TEMP,
                    "top_p": QWEN_THINK_TOP_P,
                    "top_k": QWEN_THINK_TOP_K,
                    "presence_penalty": QWEN_THINK_PRESENCE_PENALTY,
                },
            }

        @web_app.get("/v1/models")
        async def list_models():
            return {"data": [{"id": "kaetram-base", "object": "model"}]}

        @web_app.post("/v1/chat/completions")
        async def chat_completions(request: Request):
            import json
            import re as _re
            body = await request.json()
            messages = body.get("messages", [])
            tools = body.get("tools")
            # Qwen3.5-9B thinking-general defaults per model card; caller may override.
            temperature = body.get("temperature", QWEN_THINK_TEMP)
            max_tokens = body.get("max_tokens", 512)
            top_p = body.get("top_p", QWEN_THINK_TOP_P)
            top_k = body.get("top_k", QWEN_THINK_TOP_K)
            presence_penalty = body.get("presence_penalty", QWEN_THINK_PRESENCE_PENALTY)

            prompt = self.tokenizer.apply_chat_template(
                messages,
                tools=tools,
                tokenize=False,
                add_generation_prompt=True,
            )

            output = await self.engine.async_generate(
                prompt,
                sampling_params={
                    "temperature": temperature,
                    "top_p": top_p,
                    "top_k": top_k,
                    "presence_penalty": presence_penalty,
                    "max_new_tokens": max_tokens,
                },
            )
            generated_text = output["text"]
            prompt_tokens = output.get("meta_info", {}).get("prompt_tokens", 0)
            completion_tokens = output.get("meta_info", {}).get("completion_tokens", 0)

            # Parse tool calls (same as finetuned endpoint)
            parsed_tool_calls = []
            for m in _re.finditer(
                r"<tool_call>\s*<function=(\w+)>(.*?)</function>\s*</tool_call>",
                generated_text, _re.DOTALL
            ):
                fn_name = m.group(1)
                params_text = m.group(2)
                args = {}
                for pm in _re.finditer(r"<parameter=(\w+)>\s*(.*?)\s*</parameter>", params_text, _re.DOTALL):
                    args[pm.group(1)] = pm.group(2).strip()
                parsed_tool_calls.append({
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {"name": fn_name, "arguments": json.dumps(args)},
                })

            if not parsed_tool_calls:
                for m in _re.finditer(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", generated_text, _re.DOTALL):
                    try:
                        tc = json.loads(m.group(1))
                        parsed_tool_calls.append({
                            "id": f"call_{uuid.uuid4().hex[:8]}",
                            "type": "function",
                            "function": {"name": tc.get("name", ""), "arguments": json.dumps(tc.get("arguments", {}))},
                        })
                    except json.JSONDecodeError:
                        pass

            msg = {"role": "assistant", "content": generated_text}
            if parsed_tool_calls:
                msg["tool_calls"] = parsed_tool_calls
                finish_reason = "tool_calls"
            else:
                finish_reason = "stop"

            return {
                "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": body.get("model", "kaetram-base"),
                "choices": [{"index": 0, "message": msg, "finish_reason": finish_reason}],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            }

        return web_app
