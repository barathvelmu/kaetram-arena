# Decision: Acqui-Hire Trajectory & Competitive Intelligence

_Created April 15, 2026. Sourced from Workshop Labs → Thinking Machines deal analysis + AI lab hiring landscape research._

---

## The Model: Workshop Labs → Thinking Machines (April 13, 2026)

**Workshop Labs** (Luke Drago, Oxford + Rudolf Laine, Cambridge CS) existed for 9 months as a 2-person PBC before being absorbed into Mira Murati's Thinking Machines ($2B seed, $12B valuation).

**What made them acqui-hireable:**
1. Rudolf's NeurIPS 2024 paper (SAD benchmark — used by OpenAI in pre-deployment testing)
2. "The Intelligence Curse" essay series → public intellectual visibility (FLI podcast, TIME, Cognitive Revolution)
3. Working private post-training system (engineering proof, not just theory)
4. Mission alignment with acquirer (both about "democratizing post-training")
5. Timing — TML lost 3 co-founders (Zoph, Metz, Tulloch) between Oct 2025 - Jan 2026

**This is the exact trajectory KAE-26 describes for AgentScape → Anthropic.**

---

## AgentScape vs Workshop Labs — Honest Comparison

| Dimension | Workshop Labs | AgentScape | Edge |
|-----------|--------------|------------|------|
| Team credentials | Oxford (History) + Cambridge CS (paused PhD) | MIT + Stanford/Google (both technical) | AgentScape |
| Published papers | 1 NeurIPS 2024 (Rudolf's SAD) | 0 (r9 in progress, paper pending) | Workshop Labs (for now) |
| Working system | Private post-training + inference | End-to-end SFT/KTO pipeline, multi-agent orchestration, eval harness | Comparable |
| Mission alignment to target | "Democratize post-training" → Thinking Machines' Tinker | "Adversarial multi-agent safety" → Anthropic's Agentic Misalignment | AgentScape (more specific) |
| Number of research contributions | 1 thesis ("Intelligence Curse") + 1 system | 2 independent papers (distillation + adversarial safety) | AgentScape |
| Visibility | FLI podcast, TIME, essay series, Substack | Minimal public presence | Workshop Labs (big gap) |
| Entity structure | PBC from day 1 | F-1 constraints until Aug 2026 | Workshop Labs (cleaner) |

**Key takeaway:** Credentials and technical depth are stronger. Paper and visibility are weaker. Paper is the unlock.

---

## Other Recent Comparables (2024-2026)

| Date | Target | Acquirer | Size | Deal |
|------|--------|----------|------|------|
| Mar 2024 | Inflection AI | Microsoft | ~70 | $650M licensing + team hire |
| Jun 2024 | Adept AI | Amazon | ~80% | $330M license + $100M retention |
| Aug 2024 | Character.AI | Google | Key founders | $2.7B licensing |
| Mar 2026 | Dreamer | Meta | 3 co-founders | Full team → Meta Superintelligence Labs |
| Apr 2026 | Workshop Labs | Thinking Machines | 2 co-founders | Mission-aligned absorption |

The mega-deals ($650M+) required $100M+ funding and celebrity founders. Workshop Labs and Dreamer are the relevant scale comparisons: small teams with research credibility + working systems absorbed into well-funded labs.

---

## What Makes Paper 2 the Secret Weapon

Paper 1 (Kaetram distillation) is infrastructure proof. Paper 2 (RuneScape adversarial multi-agent) is the safety contribution.

**Papers that Paper 2 directly extends:**
- Anthropic "Agentic Misalignment" (arXiv 2510.05179) — LLM agents exhibit insider-threat behaviors
- DeepMind "Virtual Agent Economies" (arXiv 2509.10147) — theoretical framing for virtual economy safety
- NeurIPS 2024 "Secret Collusion among AI Agents" — formalizes multi-agent deception

**AgentScape provides the empirical testbed these theoretical papers describe.** Controlled environment for studying adversarial agent behavior, grounded in 20 years of human adversarial data (RuneScape scam taxonomy). Nobody else is doing this empirically.

---

## Gaps to Close (Priority Order)

1. **Paper on arXiv** — categorical shift from "two guys with a repo" to "two researchers with a published system"
2. **Eval results that beat baseline** — quantitative numbers (action accuracy, quest completion, XP/turn)
3. **Visibility** — Twitter thread on paper day, blog post, HN front page, engage rs-sdk community
4. **Anthropic Fellows application** — deadline April 26, 2026

---

## Decision

The acqui-hire trajectory is validated by the Workshop Labs precedent. AgentScape's profile is arguably stronger (both technical, two papers, more specific safety alignment). The critical path is: r9 eval → paper → arXiv → visibility → Anthropic engagement.

**Why:** Small-team acqui-hires are the dominant AI hiring pattern in 2024-2026. Workshop Labs proved it works at the 2-person scale with mission alignment + published research + working code. We have all three ingredients except the published paper.

**How to apply:** Every decision should prioritize paper completion speed. Side quests that don't contribute to eval results or paper writing are deferred until after arXiv submission.
