"""OPD data build: current-student rollouts scored against the 4B teacher.

Round-agnostic: the student endpoint (TWOB_EP) is whatever policy GENERATED the
source rollouts — base-2B for round 1, the 2b-opd-r1 endpoint for round 2 — so
behavior_logprobs always come from the rollout policy and init==generator holds
per round. For every action turn it renders the (context, action) pair ONCE with
the student's own tokenizer (Qwen/Qwen3.5-2B + patch — identical to what the
serve files render) and POSTs the pre-rendered text to BOTH /v1/score
endpoints via the raw-text path. The endpoints tokenize the same string with a
shared vocab, so student and teacher logprobs land on identical token ids — the
Qwen3.5 repos ship different chat-template revisions, which is why the messages
path can't be used across sizes.

Load discipline (the first run wedged both endpoints): the serve classes take
@modal.concurrent(max_inputs=16); this client holds at most PER_EP_CONCURRENCY
in flight per endpoint, renders in a worker thread so the event loop keeps
servicing responses, and submits in chunks. Provenance-sealed builds are
create-only: a crash leaves an explicitly partial artifact that must be moved
aside before a fresh build. The builder never attaches a new receipt to
pre-existing records.

Emits pre-tokenized training records for finetune/train_opd_2b.py:
    input_ids          = ctx_ids + target_ids
    labels             = -100 over context, target ids over the action
    advantages         = 0 over context, -KL_COEF*(logp_student - logp_teacher) over action
    behavior_logprobs  = 0 over context, student logprobs over the action
    step_weight        = 1.5 for turns in the first third of their session, else 1.0
                         (TCOD-style insurance: early-turn states are the best-supported
                         supervision for a small multi-turn student)

Every 10th session is held out (never trained) into heldout.jsonl, carrying the
rendered texts plus both endpoints' build-time logprobs — the post-train KL gate
(scripts/opd/opd_gate.py) re-scores only the new student on these.

Usage (round 2 — student EP is the r1 endpoint):
  TWOB_EP=https://patnir411--kaetram-qwen-2b-opd-inference-serve.modal.run/v1 \
  FOURB_EP=https://.../v1 \
    python3 scripts/opd/opd_2b_data.py --run-ids run_20260610_140358 <seeded_run> \
      --out-dir dataset/opd_2b/round2 \
      --student-artifact-id qwen-2b-r1 --student-artifact-sha256 <64-hex> \
      --teacher-artifact-id qwen-4b --teacher-artifact-sha256 <64-hex> \
      --tokenizer-path /immutable/qwen-tokenizer
  modal volume put kaetram-model-vol dataset/opd_2b/round2/records.jsonl \
      /opd_2b/round2/records.jsonl --force
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
import platform
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "opd"))
sys.path.insert(0, str(REPO / "finetune"))

from canonicalize import docify_system_prompt, is_malformed  # noqa: E402
from opd_probe import reconstruct_session  # noqa: E402
from opd_round1 import turn_to_chat  # noqa: E402
from opd_wall_probe import _frontier, _finished_from_payload  # noqa: E402
from render import patch_qwen_chat_template  # noqa: E402
from heldout_guard import assert_text_not_reserved  # noqa: E402
from opd_data_manifest import (  # noqa: E402
    BUILDER_RELATIVE_PATH,
    MANIFEST_SCHEMA_VERSION,
)
from receipt_chain import (  # noqa: E402
    BUILD_SOURCE_PATHS,
    canonical_sha256,
    is_digest,
    sha256_path,
)
from record_schema import (  # noqa: E402
    OPD_TRAIN_RECORD_SCHEMA_SHA256,
    OPD_TRAIN_RECORD_SCHEMA_VERSION,
    OPD_TRAIN_RECORD_VALIDATOR_SHA256,
)

STUDENT_EP = os.environ["TWOB_EP"].rstrip("/")
TEACHER_EP = os.environ["FOURB_EP"].rstrip("/")

MAX_HIST_MSGS = 28
MAX_SEQ = 16384
KL_COEF = 1.0
HOLDOUT_EVERY = 10        # session-level holdout for the post-train KL gate
EARLY_WEIGHT = 1.5        # step_weight for the first third of each session
# Malformed tool-call parameter key (kwarg written into the key, e.g.
# <parameter=accept_quest_offer=True>) — advantages on these spans are masked.
MALFORMED_PARAM_RE = re.compile(r"<parameter=[^>\n]*=[^>\n]*>")
PER_EP_CONCURRENCY = 6    # per-endpoint in-flight cap (server max_running_requests=8)
CHUNK = 200               # states scored per submission wave
SCORE_TIMEOUT = 240.0
SCORE_RETRIES = 3


def load_student_tokenizer(tokenizer_path: Path):
    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(tokenizer_path, trust_remote_code=True)
    patch_qwen_chat_template(tok)
    return tok


def _emission_text(turn):
    """The model's raw continuation after the generation prompt: the logged
    text block VERBATIM (it already carries the inline <tool_call> XML —
    verified 100% of assistant text blocks in the qwen runs) plus the
    end-of-turn token. No re-synthesis from the parsed tool_calls: that
    doubles every call, and the parsed copy has malformed parameter keys
    already stripped — the verbatim bytes are what the policy emitted and
    what the teacher must grade.

    The emission is appended to ctx_text directly instead of re-rendering the
    completed turn through the template: the template extracts reasoning from
    any '</think>' inside content and re-renders it as a real think block,
    which diverges from the closed-empty-think generation prompt the model was
    actually served (round-1: 4% of base-2B turns; round-2: ~100% of r1 turns,
    whose dialect ends content with a dangling '</think>').

    Returns None for turns whose reasoning was logged as a separate thinking
    block (Claude-shaped logs) — the raw interleaving is lossy to reconstruct.
    """
    if turn.thinking:
        return None
    content = (turn.text or "").strip()
    if not content:
        return None
    return content + "<|im_end|>\n"


def _snapshot_source_logs(run_ids: list[str]) -> list[dict]:
    if not run_ids or len(run_ids) != len(set(run_ids)):
        raise RuntimeError("--run-ids must contain unique run identifiers")
    inventory = []
    for run_id in run_ids:
        logs = sorted((REPO / "dataset" / "raw").glob(
            f"agent_*/runs/{run_id}/session_*.log"
        ))
        if not logs:
            raise RuntimeError(f"declared run has no source logs: {run_id}")
        for path in logs:
            resolved = path.resolve()
            inventory.append({
                "run_id": run_id,
                "path": resolved.relative_to(REPO).as_posix(),
                "sha256": sha256_path(resolved),
                "size_bytes": resolved.stat().st_size,
            })
    inventory.sort(key=lambda item: item["path"])
    if len({item["path"] for item in inventory}) != len(inventory):
        raise RuntimeError("source-log inventory contains duplicate paths")
    return inventory


def _verify_source_snapshot(inventory: list[dict]) -> None:
    for item in inventory:
        path = REPO / item["path"]
        if (
            not path.is_file()
            or path.stat().st_size != item["size_bytes"]
            or sha256_path(path) != item["sha256"]
        ):
            raise RuntimeError(f"source log changed during the build: {item['path']}")


def _directory_digest(root: Path) -> str:
    inventory = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"tokenizer snapshot contains a symlink: {path}")
        if path.is_file():
            inventory.append({
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256_path(path),
                "size_bytes": path.stat().st_size,
            })
    if not inventory:
        raise RuntimeError("tokenizer snapshot contains no regular files")
    return canonical_sha256(inventory)


def collect_action_states(inventory: list[dict]):
    """Per-turn (messages, emission, verb, frontier, session, turn_idx, n_turns,
    holdout) over the rollout logs. messages = [system, bootstrap, ...tail...]
    (context only); emission = the raw action continuation."""
    states = []
    n_no_emission = 0
    states_by_run = defaultdict(int)
    for log_i, source in enumerate(inventory):
        lp = REPO / source["path"]
        try:
            base_messages, turns = reconstruct_session(lp)
        except Exception as exc:
            raise RuntimeError(f"failed to parse declared source log: {lp}") from exc
        # Exclude the always-on system prompt from this scan: it mentions the
        # quest name as a warp prerequisite but contains no walkthrough.  Any
        # model action/reasoning or tool result touching the reserved quest is
        # nevertheless a hard stop before either endpoint grades the session.
        activity_text = json.dumps([
            {
                "text": turn.text,
                "tool_calls": turn.tool_calls,
                "results": [result.result_str for result in results],
            }
            for turn, results in turns
        ])
        assert_text_not_reserved(
            activity_text,
            use="teacher_grading",
            source=str(lp),
        )
        if not turns:
            raise RuntimeError(f"declared source log contains no turns: {lp}")
        holdout = (log_i % HOLDOUT_EVERY) == 0
        rolling = list(base_messages)
        finished: set[str] = set()
        session_states = []
        for turn_idx, (turn, results) in enumerate(turns):
            if turn.tool_calls:
                emission = _emission_text(turn)
                if emission is None:
                    n_no_emission += 1
                if emission is not None:
                    hist = rolling[2:]
                    tail = hist[-MAX_HIST_MSGS:] if len(hist) > MAX_HIST_MSGS else hist
                    session_states.append({
                        "messages": rolling[:2] + list(tail),
                        "emission": emission,
                        "verb": turn.short_tool_names[0],
                        "frontier": _frontier(finished),
                        "session": lp.name,
                        "turn_idx": turn_idx,
                        "holdout": holdout,
                        "source_run": source["run_id"],
                    })
            rolling.append(turn_to_chat(turn))
            for tr in results:
                rolling.append({"role": "tool", "content": tr.result_str, "name": tr.name})
                fin = _finished_from_payload(tr.payload)
                if fin is not None:
                    finished = fin
        n_turns = len(turns)
        for st in session_states:
            st["n_turns"] = n_turns
            states_by_run[st["source_run"]] += 1
        states.extend(session_states)
    missing = sorted({item["run_id"] for item in inventory} - set(states_by_run))
    if missing:
        raise RuntimeError(
            "declared run produced no usable action state(s): " + ", ".join(missing)
        )
    if n_no_emission:
        print(f"  skipped {n_no_emission} tool-call turns with no reconstructible emission "
              f"(empty/thinking-block text)")
    return states


def _render(tok, msgs, emission):
    """Synchronous template render + encode — runs in a worker thread.
    ctx = the exact serving prompt; full = ctx + the raw emission, so the
    prefix property holds by construction (see _emission_text)."""
    ctx_text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    full_text = ctx_text + emission
    ctx_ids = tok.encode(ctx_text, add_special_tokens=False)
    return ctx_text, full_text, ctx_ids


async def _score_raw(client, endpoint, ctx_text, full_text):
    body = {"context_text": ctx_text, "full_text": full_text}
    for attempt in range(SCORE_RETRIES):
        try:
            r = await client.post(f"{endpoint}/score", json=body, timeout=SCORE_TIMEOUT)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 400:
                return None  # malformed turn (e.g. target empty) — skip
        except (httpx.TimeoutException, httpx.HTTPError):
            pass
        await asyncio.sleep(5.0 * (attempt + 1))
    return None


async def build_record(client, tok, st, sem_s, sem_t):
    ctx_text, full_text, ctx_ids = await asyncio.to_thread(
        _render, tok, st["messages"], st["emission"])
    if len(ctx_ids) >= MAX_SEQ:
        return None, "overlong"
    if not full_text.startswith(ctx_text):
        return None, "prefix_mismatch"  # structurally impossible now; kept as a tripwire

    # Counterfactual-canonicalized grading (flip-probe-verified, June 12): for
    # records whose EMISSION carries malformed tool syntax, the TEACHER grades
    # under a context whose system-prompt doc literals are reshaped to non-call
    # prose — its clean-convention preference then yields a corrective negative
    # advantage on the malformed tokens (median -1.21 nats, 86% of states)
    # instead of the +0.09 copy-prior endorsement. Student/behavior scoring
    # always uses the real context.
    counterfactual = is_malformed(st["emission"])
    if counterfactual:
        cf_msgs = [{**st["messages"][0],
                    "content": docify_system_prompt(st["messages"][0]["content"])}] \
                   + st["messages"][1:]
        cf_ctx, cf_full, _ = await asyncio.to_thread(_render, tok, cf_msgs, st["emission"])

    async def scored(ep, sem, c, f):
        async with sem:
            return await _score_raw(client, ep, c, f)

    if counterfactual:
        s_resp, t_resp, t_plain = await asyncio.gather(
            scored(STUDENT_EP, sem_s, ctx_text, full_text),
            scored(TEACHER_EP, sem_t, cf_ctx, cf_full),
            scored(TEACHER_EP, sem_t, ctx_text, full_text))  # kept for the ablation table
    else:
        s_resp, t_resp = await asyncio.gather(
            scored(STUDENT_EP, sem_s, ctx_text, full_text),
            scored(TEACHER_EP, sem_t, ctx_text, full_text))
        t_plain = None
    if not s_resp or not t_resp:
        return None, "score_fail"
    target = s_resp["target_token_ids"]
    if counterfactual and target != t_resp["target_token_ids"]:
        # Token-boundary guard: the emission must tokenize identically after
        # both prefixes. On mismatch fall back to the plain-ctx teacher score
        # and round-2 masking for this record.
        if t_plain and t_plain["target_token_ids"] == target:
            t_resp, t_plain, counterfactual = t_plain, None, False
        else:
            return None, "cf_boundary_mismatch"
    if target != t_resp["target_token_ids"]:
        return None, "target_mismatch"
    if len(ctx_ids) != s_resp["n_context_tokens"]:
        return None, "ctx_len_mismatch"
    if len(ctx_ids) + len(target) > MAX_SEQ:
        return None, "overlong"
    s_lp = s_resp["target_logprobs"]
    t_lp = t_resp["target_logprobs"]

    if st["holdout"]:
        rec = {
            "context_text": ctx_text, "full_text": full_text,
            "teacher_logprobs": t_lp, "student_base_logprobs": s_lp,
            "verb": st["verb"], "frontier": st["frontier"], "session": st["session"],
            "turn_idx": st["turn_idx"],
        }
        return rec, "holdout"

    # Raw advantages; the trainer's ADV_CLAMP=3 handles tails (round-1 recipe —
    # with byte-faithful emissions, large disagreements are signal, not seams).
    adv_t, beh_t, rkls = [], [], []
    for si, ti in zip(s_lp, t_lp):
        if si is None or ti is None:
            adv_t.append(0.0); beh_t.append(0.0)
        else:
            rkl = si - ti
            adv_t.append(-KL_COEF * rkl); beh_t.append(si); rkls.append(rkl)

    # Round-2 abstention masking — now the FALLBACK path only: applied when a
    # flagged record could not be counterfactually graded (boundary mismatch).
    # Counterfactually-graded records keep their flagged spans LIVE: the
    # clean-doc teacher grade there is the corrective signal.
    n_masked = 0
    spans = [(m.start(), m.end()) for m in MALFORMED_PARAM_RE.finditer(full_text)
             if m.start() >= len(ctx_text)]
    if spans and not counterfactual:
        enc = await asyncio.to_thread(
            tok, full_text, add_special_tokens=False, return_offsets_mapping=True)
        if enc["input_ids"][len(ctx_ids):] != target:
            return None, "mask_align_fail"
        offs = enc["offset_mapping"][len(ctx_ids):]
        for i, (a, b) in enumerate(offs):
            if any(a < e and b > s for s, e in spans) and adv_t[i] != 0.0:
                adv_t[i] = 0.0
                n_masked += 1

    rec = {
        "input_ids": ctx_ids + target,
        "labels": [-100] * len(ctx_ids) + list(target),
        "advantages": [0.0] * len(ctx_ids) + adv_t,
        "behavior_logprobs": [0.0] * len(ctx_ids) + beh_t,
        "step_weight": EARLY_WEIGHT if st["turn_idx"] < st["n_turns"] / 3 else 1.0,
        "verb": st["verb"], "frontier": st["frontier"], "session": st["session"],
        "turn_idx": st["turn_idx"],
        "n_action": len(target), "mean_rkl": (sum(rkls) / len(rkls)) if rkls else 0.0,
        "n_masked": n_masked, "n_masked_spans": len(spans),
        "counterfactual": counterfactual,
    }
    if counterfactual and t_plain:
        # Ablation bookkeeping: the plain-ctx teacher logprobs alongside the
        # clean-doc grades actually used for the advantages.
        rec["teacher_logprobs_plain"] = t_plain["target_logprobs"]
    return rec, "ok_cf" if counterfactual else "ok"


def _print_diagnostic(diag):
    print("\n## Pre-train reverse-KL diagnostic  (mean logp_2B - logp_4B, per action token)")
    print("   positive => the 2B is over-confident where the 4B disagrees (OPD suppresses);")
    print("   expect the 2B's weak verbs (eat_food, observe-loops) among the largest.\n")
    by_verb = defaultdict(lambda: [0.0, 0])
    by_front = defaultdict(lambda: [0.0, 0])
    for (verb, front), (s, n) in diag.items():
        by_verb[verb][0] += s; by_verb[verb][1] += n
        by_front[front][0] += s; by_front[front][1] += n
    print("  by tool verb:")
    for verb in sorted(by_verb, key=lambda v: -(by_verb[v][0] / max(by_verb[v][1], 1))):
        s, n = by_verb[verb]
        if n:
            print(f"    {verb:<14} mean_rkl {s/n:+.4f}   ({n} action tokens)")
    print("\n  by Core-3 frontier:")
    for front in ["Foresting", "Herbalist", "Ricks", "post-core"]:
        s, n = by_front.get(front, [0.0, 0])
        if n:
            print(f"    {front:<12} mean_rkl {s/n:+.4f}   ({n} action tokens)")


def _health_url(endpoint: str) -> str:
    parts = urlsplit(endpoint)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise RuntimeError("scoring endpoint must be an HTTP(S) URL")
    base_path = parts.path.rstrip("/")
    if base_path.endswith("/v1"):
        base_path = base_path[:-3]
    return urlunsplit((parts.scheme, parts.netloc, base_path + "/health", "", ""))


async def _verified_endpoint_attestation(
    client: httpx.AsyncClient,
    endpoint: str,
    *,
    expected_deployment_id: str,
    expected_checkpoint_sha256: str,
) -> dict:
    try:
        response = await client.get(_health_url(endpoint), timeout=60)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise RuntimeError("scoring endpoint did not return valid /health JSON") from exc
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise RuntimeError("scoring endpoint is not healthy")
    if "score" not in (payload.get("capabilities") or []):
        raise RuntimeError("scoring endpoint does not attest the score capability")
    attestation = payload.get("attestation")
    fields = {
        "deployment_id",
        "api_model",
        "checkpoint_sha256",
        "tokenizer_sha256",
        "render_contract_sha256",
    }
    if not isinstance(attestation, dict) or set(attestation) != fields:
        raise RuntimeError("scoring endpoint has no complete identity attestation")
    if (
        attestation.get("deployment_id") != expected_deployment_id
        or attestation.get("checkpoint_sha256") != expected_checkpoint_sha256
        or not isinstance(attestation.get("api_model"), str)
        or not attestation["api_model"]
        or any(
            not is_digest(attestation.get(field))
            for field in (
                "checkpoint_sha256",
                "tokenizer_sha256",
                "render_contract_sha256",
            )
        )
    ):
        raise RuntimeError("scoring endpoint identity does not match the requested artifact")
    return attestation


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-ids", nargs="+", default=["run_20260610_140358"])
    ap.add_argument("--out-dir", default="dataset/opd_2b/round2")
    ap.add_argument("--limit", type=int, default=0, help="cap states (0 = all; for smoke tests)")
    ap.add_argument("--student-artifact-id", required=True)
    ap.add_argument("--student-artifact-sha256", required=True)
    ap.add_argument("--teacher-artifact-id", required=True)
    ap.add_argument("--teacher-artifact-sha256", required=True)
    ap.add_argument(
        "--tokenizer-path",
        required=True,
        help="immutable local tokenizer snapshot containing tokenizer.json",
    )
    args = ap.parse_args()
    for name in ("student_artifact_sha256", "teacher_artifact_sha256"):
        value = getattr(args, name)
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            ap.error(f"--{name.replace('_', '-')} must be a lowercase SHA-256")
    for name in ("student_artifact_id", "teacher_artifact_id"):
        value = getattr(args, name)
        if not value.strip() or "://" in value or any(char.isspace() for char in value):
            ap.error(
                f"--{name.replace('_', '-')} must be a non-URL artifact identifier"
            )

    source_inventory = _snapshot_source_logs(args.run_ids)
    _verify_source_snapshot(source_inventory)
    async with httpx.AsyncClient() as identity_client:
        student_attestation, teacher_attestation = await asyncio.gather(
            _verified_endpoint_attestation(
                identity_client,
                STUDENT_EP,
                expected_deployment_id=args.student_artifact_id,
                expected_checkpoint_sha256=args.student_artifact_sha256,
            ),
            _verified_endpoint_attestation(
                identity_client,
                TEACHER_EP,
                expected_deployment_id=args.teacher_artifact_id,
                expected_checkpoint_sha256=args.teacher_artifact_sha256,
            ),
        )
    tokenizer_path = Path(args.tokenizer_path).resolve()
    tokenizer_file = tokenizer_path / "tokenizer.json"
    if not tokenizer_file.is_file():
        ap.error("--tokenizer-path must contain tokenizer.json")
    tokenizer_sha256 = sha256_path(tokenizer_file)
    tokenizer_snapshot_sha256 = _directory_digest(tokenizer_path)
    if (
        student_attestation["tokenizer_sha256"] != tokenizer_sha256
        or teacher_attestation["tokenizer_sha256"] != tokenizer_sha256
    ):
        ap.error(
            "local tokenizer.json does not match both endpoint identity attestations"
        )

    tok = load_student_tokenizer(tokenizer_path)
    states = collect_action_states(source_inventory)
    if args.limit:
        states = states[: args.limit]

    out_dir = REPO / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    rec_path = out_dir / "records.jsonl"
    hold_path = out_dir / "heldout.jsonl"
    manifest_path = out_dir / "records.manifest.json"

    existing = [path for path in (rec_path, hold_path, manifest_path) if path.exists()]
    if existing:
        rendered = ", ".join(str(path) for path in existing)
        sys.exit(
            "FATAL: provenance-sealed builds are create-only; move the partial or "
            f"completed artifacts before retrying: {rendered}"
        )

    n_hold = sum(1 for s in states if s["holdout"])
    print(f"action states to score: {len(states)} ({n_hold} held out at session level) "
          f"from {args.run_ids}", flush=True)

    sem_s = asyncio.Semaphore(PER_EP_CONCURRENCY)
    sem_t = asyncio.Semaphore(PER_EP_CONCURRENCY)
    counts = defaultdict(int)
    diag = defaultdict(lambda: [0.0, 0])
    n_ok = n_hold_done = 0
    n_masked_tokens = n_masked_spans = n_action_tokens = 0

    limits = httpx.Limits(max_connections=PER_EP_CONCURRENCY * 2 + 2)
    async with httpx.AsyncClient(limits=limits) as client:
        if states:  # warm both endpoints with one state before the waves
            first = states[0]
            ctx, full, _ = await asyncio.to_thread(
                _render, tok, first["messages"], first["emission"])
            warm_s, warm_t = await asyncio.gather(
                _score_raw(client, STUDENT_EP, ctx, full),
                _score_raw(client, TEACHER_EP, ctx, full))
            # A dead/undeployed endpoint fails every state in minutes while
            # looking like transient score_fail churn — die loudly instead.
            if not warm_s or not warm_t:
                sys.exit(f"FATAL: warm-up scoring failed "
                         f"(student={'ok' if warm_s else 'FAIL'} @ {STUDENT_EP}, "
                         f"teacher={'ok' if warm_t else 'FAIL'} @ {TEACHER_EP}) — "
                         f"endpoint down or URL wrong; not starting the build.")
        with open(rec_path, "x") as rf, open(hold_path, "x") as hf:
            for i in range(0, len(states), CHUNK):
                chunk = states[i:i + CHUNK]
                results = await asyncio.gather(
                    *(build_record(client, tok, st, sem_s, sem_t) for st in chunk))
                for rec, status in results:
                    counts[status] += 1
                    if status in ("ok", "ok_cf"):
                        rf.write(json.dumps(rec) + "\n")
                        n_ok += 1
                        n_act = rec["n_action"]
                        n_masked_tokens += rec["n_masked"]
                        n_masked_spans += rec["n_masked_spans"]
                        n_action_tokens += n_act
                        diag[(rec["verb"], rec["frontier"])][0] += rec["mean_rkl"] * n_act
                        diag[(rec["verb"], rec["frontier"])][1] += n_act
                    elif status == "holdout":
                        hf.write(json.dumps(rec) + "\n")
                        n_hold_done += 1
                rf.flush(); hf.flush()
                print(f"  {min(i + CHUNK, len(states))}/{len(states)}  {dict(counts)}", flush=True)

    print(f"\n=== build done: {dict(counts)} ===")
    print(f"train records appended: {n_ok} -> {rec_path}")
    print(f"heldout appended:       {n_hold_done} -> {hold_path}")
    if n_action_tokens:
        print(f"malformed-param spans masked: {n_masked_spans} spans / "
              f"{n_masked_tokens} tokens ({n_masked_tokens/n_action_tokens*100:.2f}% of action tokens)")
    _print_diagnostic(diag)
    _verify_source_snapshot(source_inventory)
    async with httpx.AsyncClient() as identity_client:
        ending_student, ending_teacher = await asyncio.gather(
            _verified_endpoint_attestation(
                identity_client,
                STUDENT_EP,
                expected_deployment_id=args.student_artifact_id,
                expected_checkpoint_sha256=args.student_artifact_sha256,
            ),
            _verified_endpoint_attestation(
                identity_client,
                TEACHER_EP,
                expected_deployment_id=args.teacher_artifact_id,
                expected_checkpoint_sha256=args.teacher_artifact_sha256,
            ),
        )
    if ending_student != student_attestation or ending_teacher != teacher_attestation:
        raise RuntimeError("scoring endpoint identity changed during the build")
    # Root receipts are emitted only here, from the two files opened with
    # exclusive mode by this invocation. There is intentionally no reusable
    # post-hoc attestor that could relabel arbitrary existing records.
    build_sources = {
        relative: sha256_path(REPO / relative)
        for relative in BUILD_SOURCE_PATHS
    }
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "builder": BUILDER_RELATIVE_PATH,
        "script_sha256": build_sources[BUILDER_RELATIVE_PATH],
        "source_runs": list(args.run_ids),
        "source_logs": source_inventory,
        "source_sha256": canonical_sha256(source_inventory),
        "output": str(rec_path.resolve()),
        "output_sha256": sha256_path(rec_path),
        "heldout": str(hold_path.resolve()),
        "heldout_sha256": sha256_path(hold_path),
        "record_schema_version": OPD_TRAIN_RECORD_SCHEMA_VERSION,
        "record_schema_sha256": OPD_TRAIN_RECORD_SCHEMA_SHA256,
        "record_schema_validator_sha256": OPD_TRAIN_RECORD_VALIDATOR_SHA256,
        "n_records": n_ok,
        "n_heldout": n_hold_done,
        "build_sources": build_sources,
        "parameters": {
            "student_endpoint_attestation": student_attestation,
            "teacher_endpoint_attestation": teacher_attestation,
            "tokenizer_sha256": tokenizer_sha256,
            "tokenizer_snapshot_sha256": tokenizer_snapshot_sha256,
            "runtime_versions": {
                "python": platform.python_version(),
                "httpx": importlib.metadata.version("httpx"),
                "transformers": importlib.metadata.version("transformers"),
                "tokenizers": importlib.metadata.version("tokenizers"),
            },
            "max_history_messages": MAX_HIST_MSGS,
            "max_sequence_tokens": MAX_SEQ,
            "kl_coefficient": KL_COEF,
            "holdout_every": HOLDOUT_EVERY,
            "early_weight": EARLY_WEIGHT,
            "malformed_parameter_pattern": MALFORMED_PARAM_RE.pattern,
            "counterfactual_grading": True,
            "limit": args.limit,
        },
    }
    payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            dir=manifest_path.parent,
            prefix=f".{manifest_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, manifest_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"sealed build receipt: {manifest_path} ({manifest['output_sha256']})")
    vol_dst = f"/opd_2b/{out_dir.name}/records.jsonl"
    print(f"\nNext: modal volume put kaetram-model-vol {rec_path.relative_to(REPO)} "
          f"{vol_dst} --force")


if __name__ == "__main__":
    asyncio.run(main())
