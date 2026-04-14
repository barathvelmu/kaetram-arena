#!/usr/bin/env python3
"""
eval_offline.py — Offline action prediction accuracy on held-out Claude sessions.

Evaluates distillation fidelity: how well does the student model replicate
the teacher (Claude) model's tool call decisions? No live gameplay needed.

For each held-out record:
1. Extracts the context (all messages up to the last assistant turn)
2. Sends the context to the model endpoint
3. Parses the predicted tool call from the model's response
4. Compares to the ground truth tool call from the training data

Reports: top-1 accuracy, per-tool precision/recall/F1, argument accuracy,
and a confusion matrix.

This is the distillation fidelity metric — analogous to Orak's 90.91%
action accuracy headline number (arXiv 2506.03610).

Usage:
    # Evaluate finetuned model on validation set
    python3 eval_offline.py --data dataset/qwen_sft/val.json \
        --endpoint https://...serve.../v1

    # Compare base vs finetuned
    python3 eval_offline.py --data dataset/qwen_sft/val.json \
        --endpoints base=https://...base.../v1 r8-sft=https://...serve.../v1

    # Limit to N records (for testing)
    python3 eval_offline.py --data dataset/qwen_sft/val.json \
        --endpoint https://...serve.../v1 --limit 20
"""

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from openai import OpenAI


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_ENDPOINTS = {
    "base": "https://patnir411--kaetram-qwen-base-inference-serve.modal.run/v1",
    "r8-sft": "https://patnir411--kaetram-qwen-serve-inference-serve.modal.run/v1",
}


# ---------------------------------------------------------------------------
# Data loading and parsing
# ---------------------------------------------------------------------------

def load_eval_records(data_path: str, limit: int | None = None) -> list[dict]:
    """Load records from val.json and extract (context, ground_truth) pairs.

    Each record has a 'messages' list. We find the last assistant message,
    treat it as ground truth, and everything before it as context.
    """
    with open(data_path) as f:
        records = json.load(f)

    eval_pairs = []
    for record in records:
        messages = record.get("messages", [])
        if not messages:
            continue

        # Find the last assistant message with tool_calls
        last_asst_idx = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "assistant" and messages[i].get("tool_calls"):
                last_asst_idx = i
                break

        if last_asst_idx is None:
            continue

        # Context = everything before the last assistant message
        context = messages[:last_asst_idx]
        if not context:
            continue

        # Ground truth = the last assistant message's tool call
        gt_msg = messages[last_asst_idx]
        gt_tool_calls = gt_msg.get("tool_calls", [])
        if not gt_tool_calls:
            continue

        # Extract first tool call (we predict one tool per turn)
        gt_tc = gt_tool_calls[0]
        gt_func = gt_tc.get("function", {})
        gt_name = gt_func.get("name", "")
        gt_args = gt_func.get("arguments", {})

        if not gt_name:
            continue

        eval_pairs.append({
            "context": context,
            "ground_truth_name": gt_name,
            "ground_truth_args": gt_args,
            "personality": record.get("personality"),
        })

        if limit and len(eval_pairs) >= limit:
            break

    return eval_pairs


# ---------------------------------------------------------------------------
# Tool call parsing (mirrors play_qwen.py logic)
# ---------------------------------------------------------------------------

def parse_tool_calls_from_text(text: str) -> list[dict]:
    """Parse tool calls from model text output.

    Handles multiple formats:
    1. Qwen3.5 Coder XML: <tool_call><function=name><parameter=key>value</parameter></function></tool_call>
    2. JSON in tool_call tags: <tool_call>{"name": "...", "arguments": {...}}</tool_call>
    3. Fallback JSON pattern
    """
    calls = []

    # Pattern 1: Qwen XML format
    xml_pattern = r'<tool_call>\s*<function=(\w+)>(.*?)</function>\s*</tool_call>'
    for match in re.finditer(xml_pattern, text, re.DOTALL):
        name = match.group(1)
        params_block = match.group(2)
        args = {}
        for pmatch in re.finditer(
            r'<parameter=(\w+)>(.*?)</parameter>', params_block, re.DOTALL
        ):
            key = pmatch.group(1)
            val = pmatch.group(2).strip()
            # Try to parse as number/bool
            try:
                val = json.loads(val)
            except (json.JSONDecodeError, ValueError):
                pass
            args[key] = val
        calls.append({"name": name, "arguments": args})

    if calls:
        return calls

    # Pattern 2: JSON in tool_call tags
    json_tc_pattern = r'<tool_call>\s*(\{.*?\})\s*</tool_call>'
    for match in re.finditer(json_tc_pattern, text, re.DOTALL):
        try:
            obj = json.loads(match.group(1))
            name = obj.get("name", "")
            args = obj.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, ValueError):
                    args = {}
            if name:
                calls.append({"name": name, "arguments": args})
        except json.JSONDecodeError:
            continue

    if calls:
        return calls

    # Pattern 3: Bare JSON with "name" and "arguments"
    bare_pattern = r'\{[^{}]*"name"\s*:\s*"(\w+)"[^{}]*"arguments"\s*:\s*(\{[^{}]*\})[^{}]*\}'
    for match in re.finditer(bare_pattern, text):
        name = match.group(1)
        try:
            args = json.loads(match.group(2))
        except json.JSONDecodeError:
            args = {}
        calls.append({"name": name, "arguments": args})

    return calls


# ---------------------------------------------------------------------------
# Model inference
# ---------------------------------------------------------------------------

def predict_action(
    client: OpenAI,
    model: str,
    context: list[dict],
    metadata_path: str | None = None,
) -> dict | None:
    """Send context to model and parse predicted tool call.

    Returns {"name": str, "arguments": dict} or None if parsing fails.
    """
    # Load tool definitions from metadata if available
    tool_block = ""
    if metadata_path and os.path.isfile(metadata_path):
        with open(metadata_path) as f:
            meta = json.load(f)
        tools = meta.get("tools", [])
        if tools:
            tool_block = "\n\n# Tools\n\nYou have access to the following functions:\n\n<tools>\n"
            for t in tools:
                tool_block += json.dumps(t) + "\n"
            tool_block += """</tools>

If you choose to call a function ONLY reply in the following format with NO suffix:

<tool_call>
<function=example_function_name>
<parameter=example_parameter_1>
value_1
</parameter>
</function>
</tool_call>"""

    # Inject tool block into system prompt
    messages = []
    for msg in context:
        if msg["role"] == "system" and tool_block:
            messages.append({"role": "system", "content": msg["content"] + tool_block})
        elif msg["role"] == "assistant":
            # Strip tool_calls from context (model sees text only)
            messages.append({"role": "assistant", "content": msg.get("content", "")})
        elif msg["role"] == "tool":
            # Convert tool results to user messages (matching play_qwen.py)
            messages.append({"role": "user", "content": f"<tool_response>\n{msg.get('content', '')}\n</tool_response>"})
        else:
            messages.append(msg)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.0,  # deterministic for eval
            max_tokens=1024,
        )
        choice = response.choices[0]
    except Exception as e:
        print(f"    API error: {e}")
        return None

    # Route 1: Structured tool_calls from API
    if choice.message.tool_calls:
        tc = choice.message.tool_calls[0]
        args = tc.function.arguments
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, ValueError):
                args = {}
        return {"name": tc.function.name, "arguments": args}

    # Route 2: Parse from text
    content = choice.message.content or ""
    text_calls = parse_tool_calls_from_text(content)
    if text_calls:
        return text_calls[0]

    return None


# ---------------------------------------------------------------------------
# Metrics computation
# ---------------------------------------------------------------------------

def compute_metrics(results: list[dict]) -> dict:
    """Compute accuracy, F1, and confusion matrix from prediction results."""
    total = len(results)
    if total == 0:
        return {"total": 0}

    correct = sum(1 for r in results if r["correct_name"])
    correct_args = sum(1 for r in results if r["correct_args"])
    parsed = sum(1 for r in results if r["predicted_name"] is not None)

    # Per-tool TP/FP/FN
    all_tools = set()
    tp = Counter()
    fp = Counter()
    fn = Counter()

    for r in results:
        gt = r["ground_truth_name"]
        pred = r["predicted_name"]
        all_tools.add(gt)
        if pred:
            all_tools.add(pred)

        if pred == gt:
            tp[gt] += 1
        else:
            fn[gt] += 1
            if pred:
                fp[pred] += 1

    # Per-tool precision/recall/F1
    per_tool = {}
    for tool in sorted(all_tools):
        p = tp[tool] / max(1, tp[tool] + fp[tool])
        r = tp[tool] / max(1, tp[tool] + fn[tool])
        f1 = 2 * p * r / max(1e-12, p + r)
        support = tp[tool] + fn[tool]  # ground truth count
        per_tool[tool] = {
            "precision": round(p, 4),
            "recall": round(r, 4),
            "f1": round(f1, 4),
            "support": support,
        }

    # Confusion matrix (top tools only to keep readable)
    tool_list = sorted(all_tools, key=lambda t: tp[t] + fn[t], reverse=True)
    confusion = defaultdict(Counter)
    for r in results:
        gt = r["ground_truth_name"]
        pred = r["predicted_name"] or "__none__"
        confusion[gt][pred] += 1

    return {
        "total": total,
        "parsed": parsed,
        "parse_rate": round(parsed / total, 4),
        "top1_accuracy": round(correct / total, 4),
        "top1_correct": correct,
        "argument_accuracy": round(correct_args / max(1, correct), 4),
        "argument_correct": correct_args,
        "per_tool": per_tool,
        "confusion": {gt: dict(preds) for gt, preds in confusion.items()},
        "tool_order": tool_list[:15],
    }


def _args_match(gt_args: dict, pred_args: dict) -> bool:
    """Check if predicted arguments match ground truth (flexible comparison)."""
    if not gt_args and not pred_args:
        return True
    if not gt_args or not pred_args:
        return False

    # Compare each ground truth key
    for key, gt_val in gt_args.items():
        pred_val = pred_args.get(key)
        if pred_val is None:
            return False

        # String comparison (case-insensitive for mob names, NPC names)
        if isinstance(gt_val, str) and isinstance(pred_val, str):
            if gt_val.lower() != pred_val.lower():
                return False
        # Numeric comparison (within tolerance for coordinates)
        elif isinstance(gt_val, (int, float)) and isinstance(pred_val, (int, float)):
            if abs(gt_val - pred_val) > 5:  # 5-tile tolerance for coordinates
                return False
        else:
            if str(gt_val) != str(pred_val):
                return False

    return True


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_results(model_name: str, metrics: dict) -> str:
    """Format offline eval results as readable text."""
    lines = []
    lines.append(f"## Offline Eval: {model_name}")
    lines.append(f"")
    lines.append(f"Records evaluated: {metrics['total']}")
    lines.append(f"Parse rate:        {metrics['parse_rate']:.1%} ({metrics['parsed']}/{metrics['total']})")
    lines.append(f"Top-1 accuracy:    {metrics['top1_accuracy']:.1%} ({metrics['top1_correct']}/{metrics['total']})")
    lines.append(f"Argument accuracy: {metrics['argument_accuracy']:.1%} ({metrics['argument_correct']}/{metrics['top1_correct']} correct-name predictions)")
    lines.append(f"")

    # Per-tool table
    lines.append(f"### Per-Tool F1 (sorted by support)")
    lines.append(f"")
    lines.append(f"| Tool | Precision | Recall | F1 | Support |")
    lines.append(f"|------|-----------|--------|----|---------|")
    sorted_tools = sorted(
        metrics["per_tool"].items(),
        key=lambda x: x[1]["support"],
        reverse=True,
    )
    for tool, stats in sorted_tools:
        lines.append(
            f"| {tool} | {stats['precision']:.3f} | {stats['recall']:.3f} | "
            f"{stats['f1']:.3f} | {stats['support']} |"
        )

    # Confusion matrix (compact)
    lines.append(f"")
    lines.append(f"### Confusion Matrix (top tools)")
    lines.append(f"")
    confusion = metrics.get("confusion", {})
    tool_order = metrics.get("tool_order", [])[:10]
    if tool_order and confusion:
        header = "| GT \\ Pred | " + " | ".join(t[:8] for t in tool_order) + " | __none__ |"
        sep = "|" + "|".join("---" for _ in range(len(tool_order) + 2)) + "|"
        lines.append(header)
        lines.append(sep)
        for gt in tool_order:
            preds = confusion.get(gt, {})
            cells = [str(preds.get(t, "")) for t in tool_order]
            none_count = preds.get("__none__", "")
            lines.append(f"| {gt[:8]} | " + " | ".join(cells) + f" | {none_count} |")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Offline action prediction accuracy on held-out Claude sessions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--data", type=str, default="dataset/qwen_sft/val.json",
        help="Path to val.json or eval.json (default: dataset/qwen_sft/val.json)",
    )
    parser.add_argument(
        "--endpoints", nargs="*",
        help="Model endpoints as name=url pairs (default: base + r8-sft)",
    )
    parser.add_argument(
        "--endpoint", type=str, default=None,
        help="Single endpoint URL (shorthand for --endpoints r8-sft=URL)",
    )
    parser.add_argument(
        "--metadata", type=str, default="dataset/qwen_sft/metadata.json",
        help="Metadata JSON with tool definitions (default: dataset/qwen_sft/metadata.json)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Limit number of records to evaluate (for testing)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Save results JSON to file",
    )
    parser.add_argument(
        "--output-md", type=str, default=None,
        help="Save markdown report to file",
    )
    parser.add_argument(
        "--api-key", default="not-needed",
        help="API key for endpoints (default: not-needed)",
    )
    args = parser.parse_args()

    # Parse endpoints
    endpoints = {}
    if args.endpoint:
        endpoints["r8-sft"] = args.endpoint
    elif args.endpoints:
        for e in args.endpoints:
            if "=" in e:
                name, url = e.split("=", 1)
                endpoints[name] = url
            else:
                print(f"Error: endpoint must be name=url, got: {e}")
                sys.exit(1)
    else:
        endpoints = dict(DEFAULT_ENDPOINTS)

    # Load eval records
    print(f"Loading eval data from {args.data}...")
    eval_pairs = load_eval_records(args.data, limit=args.limit)
    print(f"  {len(eval_pairs)} evaluation records loaded")

    if not eval_pairs:
        print("Error: no valid evaluation records found")
        sys.exit(1)

    # Show ground truth distribution
    gt_dist = Counter(ep["ground_truth_name"] for ep in eval_pairs)
    print(f"  Ground truth distribution (top 10):")
    for tool, count in gt_dist.most_common(10):
        print(f"    {tool}: {count} ({count/len(eval_pairs):.1%})")

    all_results = {}
    all_md = []

    for model_name, endpoint in endpoints.items():
        print(f"\n{'='*50}")
        print(f"Evaluating: {model_name} ({endpoint})")
        print(f"{'='*50}")

        client = OpenAI(base_url=endpoint, api_key=args.api_key, timeout=120)
        # Determine model API name
        api_model = "kaetram" if "serve" in endpoint and "base" not in endpoint else "kaetram-base"

        results = []
        for i, pair in enumerate(eval_pairs):
            if (i + 1) % 10 == 0 or i == 0:
                print(f"  [{i+1}/{len(eval_pairs)}] Predicting...")

            prediction = predict_action(
                client=client,
                model=api_model,
                context=pair["context"],
                metadata_path=args.metadata,
            )

            pred_name = prediction["name"] if prediction else None
            pred_args = prediction["arguments"] if prediction else {}
            correct_name = pred_name == pair["ground_truth_name"]
            correct_args = correct_name and _args_match(pair["ground_truth_args"], pred_args)

            results.append({
                "ground_truth_name": pair["ground_truth_name"],
                "ground_truth_args": pair["ground_truth_args"],
                "predicted_name": pred_name,
                "predicted_args": pred_args,
                "correct_name": correct_name,
                "correct_args": correct_args,
                "personality": pair.get("personality"),
            })

            # Rate limiting
            time.sleep(0.1)

        metrics = compute_metrics(results)
        all_results[model_name] = {
            "meta": {
                "model": model_name,
                "endpoint": endpoint,
                "data_path": args.data,
                "records_evaluated": len(results),
            },
            "metrics": metrics,
            "predictions": results,
        }

        # Print summary
        md = format_results(model_name, metrics)
        all_md.append(md)
        print(f"\n{md}")

    # Save results
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        # Strip predictions from saved JSON to keep file small
        save_data = {}
        for name, data in all_results.items():
            save_data[name] = {
                "meta": data["meta"],
                "metrics": data["metrics"],
            }
        with open(args.output, "w") as f:
            json.dump(save_data, f, indent=2)
        print(f"\nResults JSON saved: {args.output}")

    if args.output_md:
        Path(args.output_md).write_text("\n\n---\n\n".join(all_md))
        print(f"Markdown report saved: {args.output_md}")


if __name__ == "__main__":
    main()
