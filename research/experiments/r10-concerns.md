# r10 — concerns, expectations, and r11 candidates

**Status:** r10 SFT in training. Base 3hr run launched 2026-05-10 21:38 EDT for
the baseline. Eval matrix will run r10-sft vs base on Core 3 via
`eval_harness.py` (time-based scenarios, warm-session loop). This doc
captures the design decisions, their known limitations, and the cheap
experiments worth trying for r11.

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
| **Tokenizer** | `unsloth/Qwen3.5-9B` (template fragment differs from upstream Qwen — keep both serve + train on unsloth) |
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

### Base (running now)
- **~0% tool-call format compliance** — fixed by passing `tools=` so the
  chat template enforces XML, but the model has zero game knowledge
- **Likely outcomes:** wanders around Lakesworld spawn, equips items, attacks
  one mob, no quest progress
- **Useful as:** floor for the comparison

### r10-sft (when training finishes)
- ✅ **>95% format compliance** (it was trained on this exact XML)
- ✅ **Game-knowledge retention** — quest names, NPC coords, mob progression,
  shop layouts, decision-tree rules are in the system prompt and SFT
  reinforced their use
- ✅ **Local recovery** — STUCK_CHECK, gate.gated, is_dead, inventory_full all
  produce trained reactions
- ✅ **Foresting + Herbalist's Desperation are structurally tractable** — both
  state-readable, both visible from Mudwich-area observe payloads, both
  have explicit walkthroughs via `query_quest`
- ⚠️ **Rick's Roll uncertain** — requires a 9-leg navigate chain past the
  100-tile cap; SFT was trained on pin-chain examples but the model has to
  remember which leg it's on between context rollovers
- ❌ **50+ turn strategic loops** — "I keep failing on Foraging gate, switch
  to Rick's Roll instead" requires memory the model doesn't have. Expect
  loops that the **stale-watchdog (5 min)** will eventually rescue by
  triggering a cold restart
- ❌ **Cross-session continuation of a half-finished plan** — at session
  rollover, the next session starts from the observe payload; any
  "in-flight reasoning" is gone

### Numeric expectations (rough)
Based on the r9 eval failure (1.5 quests / 28.5 kills / L24 vs base 2.5 /
26.5 / L20 in 30-episode runs) and r10's P0 fixes (observe supervision, prompt
parity, no `format_reasoning` tail-keep):

- **Quest completion rate** > base on Foresting (it's the easiest Core 3),
  parity-or-better on Herbalist's, lower confidence on Rick's Roll
- **Tool-call parse rate** dominantly r10-sft (format compliance is what
  SFT teaches)
- **Level reached** likely lower than r9 because we now prioritize quests
  over grinding (system prompt explicit)
- **Stages-advanced metric** is the headline for Bonferroni-corrected eval
  — this is where short-horizon SFT helps most

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
