# Why KTO Over PPO/DPO

We chose KTO (Kahneman-Tversky Optimization) as the first preference learning stage after SFT. This was not obvious — PPO and DPO are more established. Here's the reasoning.

---

## The Problem KTO Solves

SFT treats every Claude action as equally worth imitating. But some sessions end in death loops. Some sessions complete quests. Training the model on both equally teaches it to average good and bad behavior.

We needed a way to say "this session was good, learn more from it" and "this session was bad, learn less from it" — without requiring paired comparisons.

---

## Why Not DPO

DPO needs **pairs**: a good completion and a bad completion for the **same prompt**. Our data doesn't have this. Each game state is unique — the agent is at a specific position, HP, level, with specific mobs nearby. Finding two sessions that start from the same state and diverge is nearly impossible in a live game.

We could construct synthetic pairs by replaying game states and generating multiple completions, but that requires:
1. A game state replay mechanism (we don't have one)
2. Multiple model completions per state (expensive with Claude as teacher)
3. Careful matching to avoid spurious pairs

KTO sidesteps all of this. It just needs a binary label per example.

## Why Not PPO

PPO needs a **critic model** — a separate neural network that estimates the value of each game state. For Qwen3.5-9B:
- Policy model: ~18GB
- Reference model: ~18GB
- Critic model: ~18GB (same architecture)
- Optimizer states: ~12GB

Total: ~66GB. H100 80GB could barely fit it, with no room for batch computation. And PPO is notoriously unstable for LLM training — requires careful tuning of clip ratios, entropy bonuses, and reward normalization.

GRPO avoids the critic model entirely (generates multiple completions, uses relative scoring). But GRPO needs multiple completions per prompt at training time, which multiplies compute.

## Why KTO Fits

KTO needs exactly what we have:
1. **Unpaired examples** — each session scored independently
2. **Binary labels** — top 40% = desirable, bottom 30% = undesirable (middle skipped)
3. **Automated scoring** — XP gain, quest progress, deaths, click_tile rate, stuck rate
4. **Post-SFT application** — builds directly on r6 checkpoint

Memory footprint: model (18GB) + ref_model (18GB) + optimizer (~4GB) = ~40GB on H100 80GB. Comfortable.

The KTO paper (arxiv 2402.01306) showed it matches DPO on MT-Bench within 0.1-0.5 points using unpaired data. For our use case (game actions, not chat quality), binary labels from game outcomes are arguably more signal-rich than human preferences anyway.

---

## Key Design Decisions

### Scoring function (`score_sessions.py`)

Positive signals:
- XP delta (normalized by expected gain for level, not session length)
- Level delta / 3.0 (scales across multi-level sessions)
- Quest-related actions (interact_npc near quest NPCs, accept_quest)
- Progress events (level up, quest stage advance)
- Unique positions visited (exploration breadth)
- Average per-turn quality score

Penalties:
- Respawn count (deaths)
- click_tile rate (fallback actions)
- Repetitive loop rate (stuck behavior)
- Stuck rate (failed navigation)
- Death flags

**Decision:** `level_delta / 3.0` not `/ 1.0` — a session that gains 3 levels shouldn't saturate the score. Codex reviewed and confirmed.

**Decision:** Removed `attack_rate > 0.80` penalty — was biasing against AGGRESSIVE personality sessions, which legitimately spend 80%+ of turns in combat. The personality system is intentionally diverse; the scoring shouldn't penalize one style.

### Labeling thresholds

- Top 40% → desirable
- Bottom 30% → undesirable
- Middle 30% → neutral (skipped)

The gap prevents borderline sessions from being mislabeled. A session with score 0.51 shouldn't be "desirable" just because it's barely above median.

### Sliding windows (`build_kto_dataset.py`)

Window size=5, stride=2. Each window is 5 consecutive turns from a labeled session.

**Why windows, not full sessions?** Sessions are 50-150 turns. KTO works on prompt-completion pairs. A full session is too long for one training example. Windows give the model local context (what happened in the last 5 turns) with a session-level label (was this a good or bad session?).

**Local quality gating:** Even in a "desirable" session, some windows are bad (agent stuck for 5 turns). Positive windows need score >= 0.45. Negative windows need score <= 0.60. This prevents label noise.

### Reference model

Explicit plain-HF `AutoModelForCausalLM` (frozen), not Unsloth's patched model. TRL's `KTOTrainer` internally calls `create_reference_model()`, but Unsloth's PEFT internals can interfere with this. Using a plain HF model as ref avoids the interaction. This is the TRL-documented pattern.

**Risk:** Untested interaction between Unsloth training model and plain-HF ref model inside KTOTrainer. Smoke test (10 steps) is designed to catch this before committing to a full run.

---

## What KTO Won't Fix

- **Action format errors** — model generating malformed tool calls. Fix: guided decoding (KAE-14).
- **Tool selection confusion** — model choosing wrong tool from 18 options. Fix: context-dependent tool filtering (KAE-15).
- **Long-horizon planning** — model forgetting quest objectives after 15 turns. Fix: memory module (KAE-20).
- **Verbosity reward hacking** — longer reasoning getting lower per-token loss. Fix: Dr. GRPO (KAE-12).

KTO teaches judgment (prefer good outcomes). These other issues are about capability (can the model execute correctly).

---

## References

- KTO: arxiv 2402.01306
- DPO: arxiv 2305.18290
- Agent-FLAN: arxiv 2403.12881
- KAE-13 (Linear): full implementation details and run order
