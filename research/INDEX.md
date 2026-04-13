# Kaetram Arena — Research Knowledge Base

Compiled knowledge for the Kaetram AI agent distillation project. Target: ICLR 2027.

**Rule:** After any training run, data rebuild, or design decision, update the relevant file here. If no file fits, create one and link it below. Without this, the wiki dies.

**Reliable maintenance flow:**
- LLM compile pass when explicitly requested: `.claude/commands/compile-research.md`
- Cheap VM-safe staleness check: `python3 scripts/check_research_staleness.py`
- VM-safe staleness check with email nudge: `python3 scripts/check_research_staleness.py --notify`
- VM cron-friendly wrapper: `scripts/run_research_staleness_check.sh`

The durable loop is VM cron + the wrapper. The wrapper first runs the staleness checker, then auto-invokes Claude Code with `/compile-research` using `claude-opus-4-6` when stale if Claude CLI is installed and authenticated on the VM. If research files changed, it stages `research/` + `session_log.md`, commits, rebases, and pushes. If Claude CLI is unavailable, it falls back to an email nudge.

---

## Experiments

- [training-runs.md](experiments/training-runs.md) — r1 through r7-SFT (+ r6-KTO smoke test): hyperparams, results, failures, what improved
- [data-quality.md](experiments/data-quality.md) — Filters applied, before/after metrics, what got cut and why

## Related Work

- [preference-learning.md](related-work/preference-learning.md) — KTO, DPO, GRPO, Tree-GRPO, Dr. GRPO, DAPO landscape + how we use them
- [agent-sft-landscape.md](related-work/agent-sft-landscape.md) — FireAct, Agent-FLAN, SAD, AgentTrek, AgentRefine, Agent-R1, ToolACE, GamingAgent — foundational agent SFT papers

## Decisions

- [why-kto-over-ppo.md](decisions/why-kto-over-ppo.md) — Binary labels from game outcomes, why KTO fits our data, computational tradeoffs
- [r7-hyperparameters.md](decisions/r7-hyperparameters.md) — Research-backed rationale for every r7 SFT + KTO parameter

## Paper

- [contribution.md](paper/contribution.md) — What's novel, framing, outline, key ablations needed

---

## Gaps (articles needed but no source material yet)

- **Personality ablation results** — Need quantitative comparison (XP/hr, quest completion rate, death rate) across AGGRESSIVE/METHODICAL/CURIOUS. Data exists on VM, not yet analyzed.
- **World model evaluation** — Per-field accuracy, rollout drift, MCTS impact on gameplay. `world/evaluate.py` exists but results not compiled.
- **Agent distillation landscape** — ~~Filled: see [agent-sft-landscape.md](related-work/agent-sft-landscape.md)~~ CRADLE, Voyager still need detailed comparison.
- **Multi-harness analysis** — Claude, Codex, and Gemini all integrated end-to-end (Apr 10). No comparative analysis of harness quality, action patterns, or reasoning quality across backends yet.
- **Finetuned vs base quantitative eval** — Both models deployed on Modal (finetuned r7 + base Qwen3.5-9B). Dashboard shows live split-screen. Still need: defined metrics (quest progress, XP/hr, death rate), N-run evaluation protocol, statistical comparison. This is the paper blocker.
- **Self-play loop design** — STaR, ReST-EM, ETO patterns. Becomes relevant when KAE-16 starts.
- **Tool count scaling analysis** — MCP server grew from 18 → 22 tools (April 8). RAG-MCP threshold is 19. Need to measure tool selection accuracy in student model at 22 tools vs filtered subsets. Informs KAE-15 priority.

## Action Items (data pipeline)

- ~~**Re-extract turns:** Done April 9. 575 sessions extracted → 14,091 turns → 6,423 train / 646 val SFT records.~~
- ~~**Launch r7 SFT:** DONE Apr 10. Final loss 0.072, 14.5h on H100. Deployed on Modal, tested with play_qwen.py — model produces correct tool calls.~~
- ~~**Deploy r7 serving:** DONE Apr 10. serve_modal.py updated to r7, chat template patch applied at inference, verified with curl + play_qwen.py.~~
- ~~**Deploy base model serving:** DONE Apr 10. `serve_modal_base.py` — unfinetuned Qwen3.5-9B on Modal A100 for baseline comparison.~~
- ~~**Qwen agent management:** DONE Apr 10. `start-qwen.sh`, `stop-qwen.sh`, `restart-qwen.sh`, `status-qwen.sh`. Agent slots: agent_4=finetuned (QwenBot), agent_5=base (QwenBase).~~
- ~~**Dashboard Qwen Live tab:** DONE Apr 10. Split-screen MJPEG streaming (finetuned vs base), 4 FPS, `/stream/agent_N` endpoint.~~
- **Launch r7 KTO:** Rebuild KTO dataset on scored sessions, then `modal run finetune/train_kto_modal.py`.
- **Eval protocol:** Define primary metric (quest progress? XP/hr?) and run base vs r7-SFT vs r7-KTO comparison. Baseline endpoint now exists. This is the paper blocker.
- ~~**Loss masking fix:** DONE Apr 12. `completion_only_loss=True` with `dataset_text_field="text"` was silently a no-op (no response_template → TRL skipped masking). Fixed in `finetune/train_modal.py` r8: removed `completion_only_loss`, added `train_on_responses_only(instruction_part="<|im_start|>user\n", response_part="<|im_start|>assistant\n")` after trainer init. Unsloth 2025.7+ re-exports this from TRL. Note: Linear KAE-25 is the MoE-LoRA ticket (unrelated) — this fix has no Linear ticket.~~
- **r8 SFT:** Extract 65 pending sessions on VM → rebuild qwen_sft (~8,600 expected records) → `modal run finetune/train_modal.py`. Loss masking now correct.
