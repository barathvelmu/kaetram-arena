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

- [training-runs.md](experiments/training-runs.md) — r1 through r6-KTO: hyperparams, results, failures, what improved
- [data-quality.md](experiments/data-quality.md) — Filters applied, before/after metrics, what got cut and why

## Related Work

- [preference-learning.md](related-work/preference-learning.md) — KTO, DPO, GRPO, Tree-GRPO, Dr. GRPO, DAPO landscape + how we use them

## Decisions

- [why-kto-over-ppo.md](decisions/why-kto-over-ppo.md) — Binary labels from game outcomes, why KTO fits our data, computational tradeoffs

## Paper

- [contribution.md](paper/contribution.md) — What's novel, framing, outline, key ablations needed

---

## Gaps (articles needed but no source material yet)

- **Personality ablation results** — Need quantitative comparison (XP/hr, quest completion rate, death rate) across AGGRESSIVE/METHODICAL/CURIOUS. Data exists on VM, not yet analyzed.
- **World model evaluation** — Per-field accuracy, rollout drift, MCTS impact on gameplay. `world/evaluate.py` exists but results not compiled.
- **Agent distillation landscape** — SAD, ORAK, AgentArk, CRADLE, Voyager, GamingAgent. Should be a related-work article once paper framing solidifies.
- **Self-play loop design** — STaR, ReST-EM, ETO patterns. Becomes relevant when KAE-16 starts.
- **Tool count scaling analysis** — MCP server grew from 18 → 22 tools (April 8). RAG-MCP threshold is 19. Need to measure tool selection accuracy in student model at 22 tools vs filtered subsets. Informs KAE-15 priority.

## Action Items (data pipeline)

- **Re-extract turns:** 509 raw sessions but only 395 extracted. Run `python3 extract_turns.py --log-dir logs/ --output-dir dataset/extracted/ --no-frames` to pick up 114 new sessions, then rebuild qwen_sft.
