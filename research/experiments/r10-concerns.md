# r10 — concerns, expectations, and r11 candidates

**Status:** r10 SFT dataset rebuilt 2026-05-10 (9,363 records). Training not yet launched.
Three qwen-base runs completed (see Base results below). This doc captures the
design decisions, their known limitations, and the cheap experiments worth
trying for r11.

## What r10 actually is

| Layer | Choice |
|---|---|
| **Source** | 5 Claude runs × 3 personalities × ~9 sessions/run = **135 sessions, 19,152 raw turns** |
| **Window** | **3-turn sliding** windows (`convert_to_qwen.py:344`, `window_size=3`, `stride=1`); single-turn (observe→action) pairs too |
| **Truncation gate** | 16,384 tokens; **4,799 records dropped (~33%)**; `kept_p50=13,825`, `kept_p99=16,086` |
| **Records** | 8,510 train / 853 val = 9,363 total |
| **Thinking ratio** | 75% with-think, 25% no-think (Qwen3 Thinking Mode Fusion) |
| **Session_n distribution** | Healthy spread 1–16 (mode = #5 at 10%, #1 only 5%) — multi-session is *trained-on*, not a runtime workaround |
| **Bootstrap** | `bootstrap.build_orchestrate_bootstrap(personality, session_n)` — byte-identical at train and runtime |
| **Tokenizer** | `unsloth/Qwen3.5-9B` for training; `Qwen/Qwen3.5-9B` for serving (SGLang can't load unsloth's tokenizer_config.json; reverted May 12). `patch_qwen_chat_template` normalizes both. |
| **Model** | Qwen3.5-9B base, LoRA r=64 α=64, `use_rslora=False`, bf16, H100 80GB, 1 epoch, LR=1e-4 |
| **Runtime** | Warm-session loop in `play_qwen.py`: 1 process per agent slot, MCP/Chromium/login persist; conversation rotates on context_overflow |
| **Eval** | Time-based scenarios (`duration_minutes`); one play_qwen per episode; aggregate metrics across internal session rollovers |

## Design decisions + why

### 1. 3-turn windows (the deepest choice)
**Why:** maximizes record count from a small Claude corpus, fits comfortably under
16K, and most Kaetram decisions ARE locally decidable from the observe payload
(stage, gates, nearby NPCs, STUCK_CHECK, events). Trains an observe→action
*policy* rather than a planner.

**Cost we're accepting:** the model never sees the multi-turn arcs Claude
actually demonstrated (50-150 turns within a session). Strategy that requires
remembering "I tried X 20 turns ago, switch to Y" is **structurally absent
from training**. This is the real ceiling on Core 3, not Qwen's 16K window.

### 2. Warm-session loop (Qwen-only)
**Why:** Qwen's 16K cap forces conversation to roll every ~10 turns. Old design
killed `play_qwen` + respawned the whole stack per rollover (~18-25s wasted ×
~150 sessions/3hr = 45+ min lost per run). Warm loop keeps MCP/browser/login
alive; only the `messages` list resets. Smoke-test confirmed 2.7× more
productive turns/min.

**Train/test alignment:** training data already has 95% of records with N>1
on mid-game state — exactly what warm-session produces. The refactor is
*more* aligned with training than the old cold-restart cycle, not less.

### 3. `tools=` divergence between SFT and base serve
**Why:** SFT was trained without `tools=` (tool spec is in the system prompt),
so SFT serve drops `tools=` for training/serve parity. Base wasn't finetuned
on our format — it needs Qwen's native chat-template tool block, which only
fires when `tools=` is passed. `serve_modal_base.py` honors `tools=` (and
converts OpenAI-string arguments → dict + strips inline `<tool_call>` XML
before `apply_chat_template`).

**Asymmetry is intentional:** both endpoints end up producing the same
on-wire format (`<tool_call><function=NAME>...</function></tool_call>`)
that the regex parser already handles. Base goes via template-injection;
SFT via training. **Apples-to-apples on output format; not on training
substrate (which is the point of the comparison).**

### 4. Dropping --max-turns / --session-n from the Qwen path
**Why:** `args.max_turns` was never the binding constraint at training-data
collection time (sessions ended via rate-limit / Stop-hook / agent decision)
or at runtime (sessions roll on context_overflow). Eval is time-based
instead. Removes a never-firing safety bound from the contract.

## What to expect on Core 3

### Base + SFT eval matrix (n=3 base / n=2 SFT, all 3h, clean)

**Correction note (2026-05-20):** the prior table mislabeled
`run_20260512_120516` as a base run — its `harness_meta_template.json`
shows `model: r10-sft`. That run was also on the pre-fix `play_qwen.py`
JSON-string code path (`tool_call.arguments | items` template
constraint, fixed in commit `7bf7c8d`). Buggy SFT data deleted from
the corpus; clean re-runs landed 2026-05-19 → 2026-05-20.

| Run | Harness | Duration | Grinder | Completionist | Explorer | Stages/30 |
|-----|---------|----------|---------|---------------|----------|-----------|
| `run_20260510_173852` (3h) | base | 1,742 turns | 1 | 3✅ | 3✅ | **7** |
| `run_20260510_211339` (6h) | base | 3,449 turns | 1 | 3✅ | 3✅ | **7** |
| `run_20260519_223921` (3h) | base | 1,737 turns | 1 | 3✅ | 3✅ | **7** |
| `run_20260520_014319` (3h) | r10-sft | 1,560 turns | 0 | 3✅ | 0 | **3** |
| `run_20260520_044433` (3h) | r10-sft | 1,449 turns | 0 | 1 | 0 | **1** |

Numbers are Core 3 stages (Foresting/Herbalist's/Rick's Roll, max 10
per agent). Foresting completion shown as 3✅.

- **Base is identically reproducible.** Three runs (two 3h, one 6h) all
  hit `(grinder=1, completionist=3✅, explorer=3✅) = 7/30`. Zero
  variance. The 6h didn't outperform 3h — more time doesn't help
  without memory. Reproduction at this fidelity is itself notable.
- **SFT regresses by 3.0×** on the headline metric (base 7/30 vs SFT
  2.0/30 mean, n=2 clean).
- **Foresting completion rate** is the cleanest single-quest delta:
  - Base: 6 of 9 attempts succeeded (3 runs × 3 agents) = **67%**
  - SFT: 1 of 6 attempts succeeded (2 runs × 3 agents) = **17%**
  - **4× drop in completion rate.**
- **Herbalist's + Rick's Roll: 0 progress across every run.** This is a
  teacher ceiling — Claude itself doesn't reliably accept these (Apr 28
  strike-team audit). Neither base nor SFT can transcend the teacher.
- **Tool-call format compliance**: ~100% across all runs; `tools=` lets
  the chat template enforce XML on base; SFT was trained on the format.
- **Completionist tool-mix suppression (the mechanism):**

  | Tool | Base mean (n=3) | SFT mean (n=2) | Ratio |
  |---|---|---|---|
  | `interact_npc` | 62.5 | 10 | **0.16× (6.25× suppression)** |
  | `query_quest` | 68.5 | 13.5 | **0.20× (5.0× suppression)** |
  | `navigate` | 32 | 130.5 | **4.08× (kinetic amplification)** |
  | `observe` | 251 | 179 | 0.71× |

  The training corpus has interact_npc at 3.6% / query_quest at 2.1% /
  navigate at 21.6% / observe at 40.9% (`dataset/qwen_sft/metadata.json`).
  SFT's inference mix tracks the corpus distribution; base's inference
  mix doesn't (it has a dialogue-heavy chat-model prior). This is the
  corpus-prior-becomes-inference-prior story.
- **Useful as:** the quantified r10-sft vs base headline for the
  May 25 writeup. Base 7/30 ↔ SFT 2/30 with non-overlapping verb
  distributions.

### r10-sft actual results (May 19–20)

Pre-eval predictions (preserved for the record) expected SFT > base on
Foresting and ≥ parity on Herbalist's. **Actual result was a 3.5× regression**
— see eval matrix above. What held:

- ✅ **>95% format compliance** — confirmed; both base and SFT near 100%
- ✅ **Game-knowledge retention** — SFT agents use correct NPC names, coords,
  quest references from the system prompt
- ❌ **Foresting + Herbalist's** — SFT regressed catastrophically on Foresting
  (17% vs base 67%) due to corpus-prior verb suppression; Herbalist's + Rick's
  Roll untouched by both (teacher ceiling)
- ❌ **Tool-mix distortion** — `interact_npc` suppressed 6.25×, `navigate`
  amplified 4.08×; inference prior tracks corpus distribution, not task needs
- ❌ **Cross-session continuation** — confirmed absent as predicted

## Concerns we know about

### A. **The 3-turn window is the actual ceiling on long-horizon**
Even infinite runtime context wouldn't help — we didn't train the model to
*use* longer context. The warm-session loop doesn't fix this. r11 should
expand windows.

### B. **33% of multi-turn records were dropped at the 16K truncation gate**
We don't know if the dropouts are systematically the longest, most
strategically-rich windows (long observe payloads = long context = more
likely to drop). If so, the surviving training data is biased toward
*shorter strategic content*. Worth auditing in r11 prep.

### C. **`Session #N` text drifts unboundedly at runtime**
Training saw N=1..16. Warm-session runtime hits N=300+ over 3 hours. The
model probably doesn't condition strongly on N (the bootstrap text only
differs by 1-2 tokens), but it's the one place where runtime escapes the
training distribution. **One-line mitigation if it ever matters:** clamp
the rendered N at 16 or drop it from the bootstrap entirely; rebuild
dataset; retrain. Low effort, possibly zero gain.

### D. **Mongo-persisted character state means session #N's first observe
shows mid-game state, but training data only sometimes mirrors this**
Windows that start mid-Claude-session preserve mid-game observe payloads.
Windows that start at Claude session beginning show fresh-spawn state.
The training distribution mixes these. Runtime distribution after the
first session is *only* mid-game. Minor distribution shift; probably
benign because the model conditions on the observe content not "session
phase".

### E. **Death-zone exclusion (Rule 16 in system prompt) is unenforceable
without memory**
The rule requires tracking "50 turns since last respawn" — the model can't
do this across session rollovers. The rule fires only within a single
~10-turn warm session. Expect death loops the rule was meant to prevent.

### F. **STUCK_CHECK is the only multi-turn memory signal injected into the
observe**
It tracks `turns_near` and `total` across observes. This is the model's
only persistent "look-back" signal beyond the current frame. r11 could
expand this — inject more state hints (e.g. "last 5 actions attempted",
"recent failures by tool").

### G. **Base eval is a tool-format eval, not a strategy eval**
With `tools=` injected, base learns format from the template every turn.
With training, SFT learns format + knowledge + reactions. The eval delta
mostly measures the *knowledge* gap, since both have format compliance
(though SFT's is more reliable). The model-vs-model comparison may
understate SFT's value because base's format-from-template is a fair
floor.

### I. **play_qwen.py chat template crash on multi-turn (discovered May 16, fixed May 19)**
`play_qwen.py` was passing `json.dumps(fn_args)` (a string) for tool call
arguments, but Qwen3.5's chat template does `tool_call.arguments | items`
which requires a dict. This crashes `apply_chat_template` on turn 2+ when
the context window includes prior tool calls. **Fixed in commit `7bf7c8d`:**
pass `fn_args` directly. Note: `serve_modal_base.py` already had a
server-side string→dict adapter (lines 113-119), so all three qwen-base
runs were **not affected** — only SFT runs through `serve_modal.py` hit
the bug. The buggy May 12 SFT run (`run_20260512_120516`) was deleted.

### H. **No memory across episodes in eval**
Each eval episode resets Mongo + sandbox. The model can't even use
Mongo-persisted progress across episodes. This is *correct* (we want
clean per-episode metrics) but it means the eval measures
single-character lifespans, not learning-over-time.

## r11 candidate experiments (cheap → expensive)

### Tier 1 — same data, different slicing (sub-day experiments)
1. **Expand window_size to 8 or 12.** Same Claude data, slice differently.
   Forces records past the 16K gate; expect ~50% drop rate but the
   surviving records carry actual multi-turn arcs. Compute: same training
   time. **Likely highest-ROI experiment.**
2. **Audit what got dropped at the 16K gate in r10.** If the dropouts are
   systematically the long-strategic windows, we're training on a
   pre-filtered "tactical" subset. Mitigation: split big observes (e.g.
   truncate ASCII map in non-current-turn windows).
3. **Drop `Session #N` from the bootstrap text.** One-liner; rebuild
   dataset; retrain. Removes the only runtime-distribution-drift point.

### Tier 2 — pipeline changes (multi-day)
4. **Memory module (KAE-20).** At session rollover, write a structured
   summary (current quest, last failure, inventory delta, level
   trajectory) into the next session's bootstrap. Requires training on
   "with-memory" records (synthesize from Claude sessions by injecting
   summaries at rollover points).
5. **Expand multi-turn memory signals in observe.** Add "last_5_actions",
   "recent_errors_by_type", "time_in_current_zone" to the observe payload.
   Cheap MCP-side change; SFT just needs records re-generated with the
   new field.
6. **Train the agent to use `query_quest` proactively.** The tool surfaces
   `walkthrough_steps` per stage. r10 sees `query_quest` calls in Claude
   data but the model may underutilize it. A targeted curriculum subset
   could fix this.

### Tier 3 — model + training stack (weeks)
7. **Longer LoRA training** (2 epochs) — diminishing returns historically
   but worth measuring.
8. **Constitutional / preference-tuning over r10 + r11 data.** KTO-style
   "this episode died because of X, prefer Y" labels from eval failures.
   Listed in `research/decisions/why-kto-over-ppo.md`.
9. **Drop to a smaller model with longer trained context** (e.g. Qwen2.5
   long-context variants). Eats Apache-2.0 freshness for runtime headroom.

## Decision for the current run

The 3hr base run is the right baseline as-is. **r10-sft eval will give us
the real number** for SFT impact. Pre-judging r11 priorities ahead of that
number is premature. **Bookmark Tier-1 candidates and revisit after the
r10-sft eval matrix lands.**

## References

- Pipeline source of truth: `convert_to_qwen.py`, `finetune/train_modal.py`,
  `finetune/serve_modal.py`, `finetune/serve_modal_base.py`, `play_qwen.py`
- Dataset provenance: `dataset/qwen_sft/metadata.json`
- Runtime: `orchestrate.py` (warm-session lifecycle), `eval_harness.py`
  (time-based scenarios)
- Adjacent decisions: `research/decisions/r7-hyperparameters.md`,
  `research/decisions/why-kto-over-ppo.md`
- Recent history: `research/experiments/training-runs.md`,
  `research/experiments/data-quality.md`
