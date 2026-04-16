# Paper: Contribution & Framing

Working notes for the ICLR 2027 submission. This is a thinking document — not paper-ready prose.

---

## One-Sentence Framing

Structured game-agent distillation: we distill a frontier LLM's gameplay reasoning into a 9B model using a typed MCP-style tool API, personality-diverse teacher data, and outcome-based preference refinement.

---

## What's Novel

### 1. MCP-based structured distillation for game agents

Prior game agent work (GamingAgent ICLR 2026, CRADLE, Voyager) has the agent write raw code or click pixels. We use a **custom MCP server with 22 typed game tools** — the teacher (Claude) and student (Qwen) both call the same structured API. This means:
- Training data is naturally structured (tool name + typed arguments, not free-text code)
- No action space mismatch between teacher and student
- The tool API acts as an abstraction layer — game internals change without breaking training data

Conservative version of this claim: this appears meaningfully different from prior game-agent work that relies on raw code generation, browser automation, or pixel clicking. Phrase this as "to our knowledge" until the related-work article is compiled more fully.

### 2. Personality-diverse teacher data

Instead of one teacher policy, we run **3 Claude agents with distinct playstyles** (AGGRESSIVE, METHODICAL, CURIOUS) that produce structurally different training data:
- AGGRESSIVE: combat-heavy, risk-tolerant (HP threshold 30%)
- METHODICAL: preparation-focused, risk-averse (HP threshold 60%, 2+ food before bosses)
- CURIOUS: exploration-first, NPC-prioritized, zone rotation

This is not just data augmentation — each personality appears to produce different **decision boundaries** at similar game states. The student model may learn a richer action distribution than any single teacher would provide, but this needs an explicit ablation.

Personality injection via prompt modification is lightweight (< 20 lines per personality) and doesn't require retraining the teacher. Keep this as a secondary claim until the personality-diversity ablation is run.

### 3. Outcome-based preference refinement (KTO on game sessions)

After SFT, we apply KTO using **game outcomes as reward signals** — XP gain, quest completion, deaths, navigation efficiency. This is interesting in combination:
- KTO is typically applied to chat/instruction data with human labels
- We use automated game metrics as labels — more signal-rich and cheaper than human feedback
- The scoring function is game-specific (not generic RLHF)

---

## What's Interesting But Secondary

### World model for reward shaping
2.2M param Transformer predicting combat outcomes. Interesting, but **not part of the core claim until it improves a downstream metric**. Don't lead with it.

### Multi-harness comparison
Same game, same tools, 5 different AI backends (Claude, Codex, Gemini, Kimi, Qwen Code). Interesting for analysis but not a paper contribution unless we do a rigorous comparison. Claude, Codex, and Gemini are fully integrated end-to-end (Apr 10); Kimi and Qwen Code are WIP.

### Finetuned vs base model live comparison
Dashboard Qwen Live tab (Apr 10) shows split-screen MJPEG streaming of finetuned r7 (agent_4) vs base Qwen3.5-9B (agent_5) playing simultaneously. Useful for qualitative analysis in the paper, but quantitative eval protocol still needed.

### Self-play improvement loop
Planned (KAE-16) but not implemented. If it works, it's a strong contribution: student generates own data → score → retrain → iterate. STaR/ReST-EM pattern applied to game agents.

---

## Key Ablations Needed

| Ablation | What it shows | Status |
|----------|---------------|--------|
| SFT only vs SFT + KTO | KTO improves over pure imitation | Pending r9-KTO (r6-KTO smoke test passed, will rebuild on r9 SFT merged weights) |
| 1 personality vs 3 personalities | Diversity improves student policy | Need to train on AGGRESSIVE-only, compare |
| Loss masking vs full loss | Training on game state tokens hurts | r8 (correct masking) vs r7 (broken masking, same data) — natural ablation. Same dataset, only difference is loss masking. |
| Train/inference alignment | Matching prompts matters | r8 (mismatched prompt) vs r9 (aligned prompt) — r8 eval showed base 2x better than r8-SFT. r9 fixes alignment. |
| 22 tools vs filtered tools | Tool filtering helps small models | Pending KAE-15 implementation. Now at 22 tools (above RAG-MCP 19-tool threshold) — ablation more urgent. |
| With/without click_tile filter | Data quality > quantity | r5 vs pre-filter comparison (have data) |
| ORAK 3-stream vs monolithic SFT | Decomposed training improves action accuracy | Pending KAE-19 |

**Most paper-ready now:** loss masking (r7 vs r8), train/inference alignment (r8 vs r9), and click_tile filtering. r8 eval data exists (base outperformed r8-SFT). Personality diversity is promising but still needs a direct ablation. **Eval harness implemented** (Apr 15) — `eval_harness.py`, `eval_compare.py`, `eval_offline.py` ready to produce ablation numbers. r9 training in progress (Apr 16).

---

## Rough Paper Outline

1. **Introduction** — Game environments as testbeds for agent distillation. Problem: frontier LLMs play games well but are too expensive to deploy. Can we distill their gameplay reasoning into a 9B model?

2. **Related Work** — Game-playing agents (GamingAgent, CRADLE, Voyager), agent distillation (SAD, ORAK, AgentArk), preference learning (KTO, GRPO, DPO), world models for planning.

3. **Method**
   - 3.1 Kaetram environment + MCP tool API (22 tools, OODA loop)
   - 3.2 Personality-diverse data collection (3 teacher personalities, how they differ)
   - 3.3 SFT with loss masking and quality filtering
   - 3.4 KTO preference refinement with game outcome scoring
   - 3.5 (if ready) World model reward shaping / GRPO

4. **Experiments**
   - 4.1 Setup: Qwen3.5-9B, Modal H100, dataset stats
   - 4.2 Main results: finetuned model gameplay vs baseline
   - 4.3 Ablations (see table above)
   - 4.4 Qualitative analysis: example game sessions, reasoning quality

5. **Analysis** — What the student model learns vs doesn't learn. Where it fails. Context window limitations. Tool selection accuracy.

6. **Conclusion** — Structured tool APIs appear to make game-agent distillation practical. Outcome-based preference learning is the main post-SFT refinement lever; personality diversity is promising but still partially unverified.

---

## Figures & Tables Needed

| Figure | What it shows | Data source |
|--------|---------------|-------------|
| Architecture diagram | End-to-end pipeline: Claude → MCP → logs → SFT → KTO → Qwen | Manual |
| Action distribution | What actions each personality produces (stacked bar) | extract_turns.py output |
| Training loss curves | r7/r8 SFT + r8-KTO loss over steps | Modal training logs |
| Ablation table | All ablations with metrics | Training runs |
| Example gameplay | Side-by-side: Claude vs finetuned Qwen on same scenario | play_qwen.py screenshots |
| Score distribution | Session scores before/after KTO | score_sessions.py output |

---

## Open Questions

1. **Evaluation metric — IMPLEMENTED (Apr 15):**
   - **Offline action prediction accuracy:** `eval_offline.py` holds out Claude sessions, measures whether finetuned Qwen reproduces Claude's tool call given the same observation. Directly analogous to TiG's 90.91% headline. Avoids circular dependence on KTO reward signal.
   - **Live gameplay metrics:** `eval_harness.py` runs N episodes per model with DB reset between episodes. Log-based metrics: XP/turn, quest completion, deaths, tool call success rate. `eval_compare.py` computes Glass's delta, bootstrap CIs, Bonferroni correction. `scripts/run-eval.sh` wrapper for parallel model runs.
   - Dashboard eval tab shows live progress.
   - Both metrics together give a strong story: "student reproduces teacher at X% and achieves Y% quest completion vs Z% baseline."
   - **Status: implemented, runs pending.**
2. **Baseline:** Vanilla Qwen3.5-9B (no finetuning) deployed as baseline (`serve_modal_base.py`, agent_5). Comparison table: base → r8-SFT (broken alignment, shows what goes wrong) → r9-SFT (fixed alignment) → r9-KTO. The r8→r9 delta tells the data quality story.
3. **Reproducibility:** N=20 runs per model per condition. Same seed conditions. Report mean ± std. Kaetram-Open is public — full reproduction possible.
4. **Core intro framing (vs. all comparables, not just TiG):** "Unlike prior work where LLMs serve as decision advisors for human players (TiG), generate raw code or click pixels (CRADLE, Voyager), or operate in episodic single-player environments (Orak, GamingAgent), our agent operates fully autonomously in a persistent open world using a shared typed tool API as the teacher-student interface." This single sentence covers all five main comparables simultaneously.
5. **Ethics section:** Agent plays a game, no human subjects. Address: compute cost of teacher data collection, environmental impact of 24/7 agent runs.
