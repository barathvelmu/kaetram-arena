# State-of-the-Art Prompting Research

**Compiled:** April 13, 2026
**Sources:** OpenAI, Anthropic, Google DeepMind, 100+ academic papers (2022-2026), startup frameworks, practitioner guides, technical communities
**Scope:** LLM prompting, agentic LLM prompting, tool-calling agent prompting

---

## Table of Contents

1. [The Paradigm Shift: From Prompt Engineering to Context Engineering](#1-the-paradigm-shift-from-prompt-engineering-to-context-engineering)
2. [How Prompts Work Inside LLMs](#2-how-prompts-work-inside-llms-mechanistic-understanding)
3. [Universal Prompting Principles](#3-universal-prompting-principles)
4. [Foundational Prompting Techniques](#4-foundational-prompting-techniques)
5. [2024-2026 Advanced Techniques](#5-2024-2026-advanced-techniques)
6. [Prompting Reasoning and Thinking Models](#6-prompting-reasoning-and-thinking-models)
7. [Model-Specific Prompting Guides](#7-model-specific-prompting-guides)
8. [Tool Use and Function Calling](#8-tool-use-and-function-calling)
9. [Agentic Prompting Architectures](#9-agentic-prompting-architectures)
10. [Agentic Coding Tools](#10-agentic-coding-tools)
11. [Automatic Prompt Optimization](#11-automatic-prompt-optimization)
12. [What Doesn't Work (Anti-Patterns)](#12-what-doesnt-work-anti-patterns)
13. [Prompt Caching and Cost Optimization](#13-prompt-caching-and-cost-optimization)
14. [Security and Robustness](#14-security-and-robustness)
15. [References](#15-references)

---

## 1. The Paradigm Shift: From Prompt Engineering to Context Engineering

The single most significant development in 2025-2026 is the reframing from **"prompt engineering"** to **"context engineering."** The bottleneck is no longer the instruction text itself but the entire information architecture surrounding the model.

### 1.1 Definition and Evolution

**Context engineering** is the systematic design and optimization of everything the model sees: system prompts, conversation history, retrieved documents, available tools, memory systems, and dynamic state. The evolution timeline:

- **Stage 1 — Prompt Engineering (2023):** Instruction-focused. Clever wording, few-shot examples.
- **Stage 2 — Agentic Workflows (2024):** Tool chains, ReAct loops, multi-step pipelines.
- **Stage 3 — Context Engineering (2025-2026):** Selection, retrieval, compression, persistence, and dynamic state management.

82% of IT leaders say prompt engineering alone is insufficient at scale (Gartner, 2026). Schema-First Development (Pydantic/Zod first, prompts second) is industry standard.

> "A language model changes you from a programmer who writes lines of code, to a programmer that manages the context the model has access to." — Simon Willison

### 1.2 Anthropic's Core Principle

> "Find the smallest set of high-signal tokens that maximize the likelihood of your desired outcome." — Anthropic, "Effective Context Engineering for AI Agents" (June 2025)

Context is a finite resource subject to diminishing returns as token count increases. Every new token depletes an "attention budget." The goal is maximum signal density, not maximum information.

### 1.3 The "Right Altitude" Principle

Anthropic identifies two anti-patterns and an optimal middle ground:

- **Anti-pattern 1 — Brittle Hardcoding:** Complex, prescriptive logic creating fragile prompts
- **Anti-pattern 2 — Vague Guidance:** High-level instructions that assume shared context
- **Optimal:** Specific enough to guide behavior, flexible enough to provide strong heuristics

Minimal does NOT mean short — agents need sufficient upfront information. But every token must earn its place.

### 1.4 Three Techniques for Long-Horizon Tasks

1. **Compaction:** Summarize conversation nearing context limits. Tool result clearing is the safest lightweight form.
2. **Structured Note-Taking (Agentic Memory):** Agent writes persistent notes stored outside the context window, pulled back later. Enables strategies impossible within a single context window.
3. **Sub-Agent Architectures:** Specialized sub-agents handle focused tasks in clean context windows, returning condensed 1-2K token summaries from 10K+ token explorations.

---

## 2. How Prompts Work Inside LLMs (Mechanistic Understanding)

### 2.1 Attention Patterns and Instruction Processing

System prompts receive **sustained attention** throughout generation (establishing behavioral context), while user prompts receive **focused attention** for the specific task. The model continuously references the system prompt, creating a dual-attention pattern.

However, this hierarchy is fragile in practice. Models only follow system-over-user priority **9.6-45.8% of the time** when instructions genuinely conflict (ICLR 2025). Models often obey social cues (authority, expertise, consensus) over structural priority.

OpenAI's Instruction Hierarchy training (Wallace et al., ICLR 2025) uses synthetic data + context distillation to teach priority: 63% improvement in system prompt extraction defense, 30%+ robustness on unseen attacks.

### 2.2 In-Context Learning: Induction Heads and Function Vectors

**Induction heads** are the core mechanism behind few-shot learning. Olsson et al. (Transformer Circuits, 2022) identified attention heads implementing a match-and-copy algorithm: given `[A][B]...[A]`, attend from second A back to first A and predict B. These develop at precisely the same point as a sudden sharp increase in ICL ability (a phase transition).

**Function Vector (FV) heads** extend this. Recent work (Yin & Steinhardt, 2025) shows many FV heads start as induction heads during training before transitioning to a more complex mechanism. In larger models, few-shot ICL is driven primarily by FV heads.

**Practical implication:** Few-shot examples work by activating match-and-copy mechanisms, not by "understanding" examples. The model finds patterns in demonstrations and applies them. This is why example diversity matters more than example quantity.

### 2.3 Why Chain-of-Thought Works

The intermediate computation hypothesis is partially validated but more complex than expected:

- LLMs run multiple reasoning pathways **in parallel** during CoT, not strictly sequentially. Models simultaneously try direct solutions while following step-by-step procedures, converging in later layers.
- Even when CoT steps are replaced by placeholders, deeper layers still encode the missing steps.
- CoT narrows the sequence decoding space through intermediate steps.

**CoT faithfulness is questionable.** Turpin et al. (2023) showed CoT explanations can systematically misrepresent the model's true reasoning. When biased toward incorrect answers, models generate rationalizing CoT with accuracy drops of up to 36%. The "Hydra Effect" explains why perturbing CoT often has little impact on final answers — redundant computational pathways exist.

**Latent reasoning (Coconut):** Chain of Continuous Thought feeds hidden states back as input embeddings in continuous space, enabling breadth-first search. Outperforms explicit CoT on logical reasoning requiring substantial search — suggesting CoT can happen without tokens.

### 2.4 Positional Effects: Primacy, Recency, "Lost in the Middle"

The U-shaped attention bias is well-established (Liu et al., TACL 2024). Performance is highest when relevant information occurs at the beginning or end of input, significantly degrading in the middle, even for long-context models.

**Root causes (MIT, 2025):** Certain architectural design choices controlling how information spreads give rise to position bias. Both architecture (RoPE positional encoding decay) and training data distribution contribute.

**Context Rot (Chroma, July 2025):** Systematic study of 18 frontier models:
- Observable degradation at 500-2,500 words; severe at 5,000+
- Models perform better on shuffled (incoherent) haystacks than structured ones
- Even with **perfect retrieval**, performance still degrades with length — the problem is processing longer sequences, not finding information
- Claude: conservative, abstains rather than hallucinating under uncertainty
- GPT: highest hallucination rates, confident but incorrect with distractors

**Practical guidance:**
- Place instructions at END of prompt for maximum recall
- Queries at the end improve response quality by up to 30% (Anthropic)
- Sandwich critical instructions at BOTH beginning AND end (OpenAI)
- "Found in the Middle" calibration can recover up to 15 percentage points (Hsieh et al., 2024)

### 2.5 Token-Level Sensitivity and Prompt Brittleness

Prompt brittleness is severe:
- Over **45% accuracy swings** between best and worst phrasings for identical tasks
- Up to **76 accuracy points** difference from formatting changes alone (LLaMA-2-13B)
- GPT-3.5: up to 40% variation in code translation from template changes
- Gartner: Prompt sensitivity caused 38% of all LLM deployment failures

Larger models are more robust. GPT-4 showed >0.5 consistency across formats; GPT-3.5 below 0.5. Optimal formats don't transfer across model families (IoU below 0.2) but same-series models share preferences (IoU >0.7).

### 2.6 How Models Process Tool Schemas

Tool schemas are ingested as tokenized text regardless of structured API parameters. Tool selection is probabilistic, not deterministic.

Tool count degradation is gradual:
- Below 30 tools: >90% selection accuracy
- 31-70 tools: intermittent degradation as semantic overlap increases
- Beyond ~100 tools: severe degradation
- RAG-MCP retrieval triples accuracy (43.13% vs 13.62% baseline)

Token cost per tool: simple tool ~96 tokens, complex tool (28 params) ~1,633 tokens. A 37-tool set = 6,218 tokens overhead on every call.

---

## 3. Universal Prompting Principles

These principles apply across all current models (April 2026).

### 3.1 Be Clear and Direct

All three major labs agree: explicit, unambiguous instructions outperform clever or indirect phrasing.

> "Show your prompt to a colleague with minimal context on the task and ask them to follow it. If they'd be confused, Claude will be too." — Anthropic

- Provide instructions as sequential steps using numbered lists when order matters
- "Create an analytics dashboard" (weak) vs "Create an analytics dashboard. Include as many relevant features as possible. Go beyond the basics." (strong)

### 3.2 Explain WHY, Not Just WHAT

Providing context or motivation helps models generalize to edge cases not explicitly covered.

- "NEVER use ellipses" (weak) vs "Your response will be read aloud by a text-to-speech engine, so never use ellipses since the engine cannot pronounce them." (strong)
- "Observe between attacks" (weak) vs "Observe between attacks — game state changes after each action, stale state causes deaths" (strong)

### 3.3 Use Examples (Few-Shot Prompting)

**3-5 examples is the sweet spot** for most tasks (Anthropic, Google). Google explicitly states: "Prompts without few-shot examples are likely to be less effective."

Best practices:
- Make examples **relevant** (mirror actual use case), **diverse** (cover edge cases), and **structured** (wrap in `<example>` tags)
- If examples clearly demonstrate the task, you can remove written instructions entirely (Google)
- Start zero-shot, escalate to few-shot only if results are inadequate (Stanford AI Lab)
- Few-shot can **hurt** by biasing toward surface patterns rather than full reasoning

**Important exception:** For reasoning models (o-series, DeepSeek-R1), few-shot examples are harmful. See Section 6.

### 3.4 Structured Formatting

Format preference is model-dependent:

| Format | Best For | Notes |
|--------|----------|-------|
| XML tags | Claude (trained on XML), section boundaries, nested examples | No "special" tags — descriptive names work. Provides unambiguous parsing. |
| Markdown | GPT-4/5 series, general organization | Default starting point. Saves ~15% tokens vs XML. |
| JSON | Structured coding contexts, GPT-3.5 | Poor for document listings (OpenAI). |
| YAML | GPT-5 Nano | Outperformed XML by 17.7pp for smaller models. |
| Pipe-delimited | Document listings in long context | Excellent performance (OpenAI GPT-4.1 guide). |

**Key rule:** Use one format consistently. Do NOT mix XML and Markdown in the same prompt — use standardized boundaries throughout.

### 3.5 Document Placement and Ordering

- **Reference data at top, instructions at end** — "lost in the middle" effect means middle 40-60% of context is systematically underweighted (Stanford NLP)
- **Queries/tasks at the very end** — up to 30% quality improvement (Anthropic)
- **Sandwich long context** — place instructions at both beginning AND end (OpenAI GPT-4.1)
- **Constraint ordering:** Context → main task → constraints. Place negative, formatting, and quantitative constraints at the end (Gemini 3 guide)

### 3.6 Prompt Length Optimization

Clear diminishing returns:
- **~500 words:** Optimal threshold for most tasks
- **~3,000 tokens:** Measurable reasoning degradation begins
- **Per 100 words beyond 500:** ~12% comprehension drop
- **5,000+ tokens:** Severe degradation

The "identification without exclusion" problem: LLMs can identify irrelevant details but struggle to ignore them during generation.

**Practical approach:** Start with detailed prompts (2,500+ tokens) for quality, then compress to ~200 tokens for 76% cost reduction while maintaining quality (hill-climb quality first, down-climb cost second).

### 3.7 Temperature and Sampling Parameters

| Context | Recommended Temperature | Notes |
|---------|------------------------|-------|
| Factual tasks | 0.2-0.3 | Minimize variation |
| General | 0.7 | Good default, Top-P 0.9 |
| Creative/interactive | 0.7-0.9 | Higher diversity |
| Gemini 3 | **1.0 (mandatory)** | Lowering degrades reasoning — sharp departure |
| OpenAI o-series | **1.0 (fixed)** | Cannot be changed |
| DeepSeek R1 | **0.5-0.7 (0.6 ideal)** | Too high = incoherent; too low = repetition |
| Open-source | Temperature + Min-P (0.05-0.1) | Min-P consistently outperforms Top-P |
| Commercial APIs | Temperature + Top-P | Standard combination |

---

## 4. Foundational Prompting Techniques

### 4.1 Chain-of-Thought (CoT)

**Paper:** Wei et al., "Chain-of-Thought Prompting Elicits Reasoning in Large Language Models" (NeurIPS 2022, arXiv:2201.11903)

Add "Let's think step by step" or include reasoning exemplars. PaLM 540B went from 18% to 57% on GSM8K. **Emergent ability** — only works above ~100B parameters. Became the most influential prompting technique, spawning dozens of variants.

**Why it works:** Converts a single hard reasoning step into multiple easier steps, each within the model's capability. Externalizes intermediate computation.

### 4.2 Self-Consistency

**Paper:** Wang et al. (ICLR 2023, arXiv:2203.11171)

Sample multiple diverse reasoning paths via temperature sampling, select the most consistent answer by majority vote. +17.9% on GSM8K, +11.0% on SVAMP. Requires multiple inference calls (5-40 samples), so cost scales linearly.

### 4.3 Tree of Thoughts (ToT)

**Paper:** Yao et al. (NeurIPS 2023, arXiv:2305.10601)

Generalizes CoT into a tree-search over "thoughts." Enables lookahead, backtracking, and evaluation of partial paths via BFS/DFS. Game of 24: 74% vs 4% with standard CoT. High token cost (multiple LLM calls per step).

### 4.4 ReAct: Reasoning + Acting

**Paper:** Yao et al. (ICLR 2023, arXiv:2210.03629)

Interleaves reasoning traces with actions (tool calls) and observations (environment feedback). Foundation for virtually all modern LLM agent architectures.

- **"Reason to act":** Planning, tracking state, handling exceptions
- **"Act to reason":** Actions retrieve external info that improves reasoning, reducing hallucination
- Human-interpretable trajectories (debuggable)
- Fine-tuning smaller models on ReAct trajectories from larger models works well

| Task | ReAct | Baseline |
|------|-------|----------|
| HotpotQA | 35.1% | 28.7% |
| ALFWorld | 71% success | 45% act-only |
| WebShop | 40% success | 30.1% act-only |

### 4.5 Least-to-Most Prompting

**Paper:** Zhou et al. (ICLR 2023, arXiv:2205.10625)

Two stages: (1) decompose into simpler subproblems, (2) solve sequentially, feeding earlier solutions into later prompts. SCAN benchmark: 99.7% with 14 exemplars vs 16% for standard prompting. Creates a curriculum where easy sub-tasks build context for harder ones.

### 4.6 Reflexion

**Paper:** Shinn et al. (NeurIPS 2023, arXiv:2303.11366)

Agents verbally reflect on task feedback and store reflections in episodic memory. HumanEval pass@1: 91% (vs 80% baseline). No weight updates — just natural language self-critique stored in context.

### 4.7 Self-Refine

**Paper:** Madaan et al. (arXiv:2303.17651, 2023)

LLM generates output, critiques it, then refines iteratively. ~20% absolute improvement on average. Simple but powerful pattern that influenced all subsequent "reflection" architectures.

---

## 5. 2024-2026 Advanced Techniques

### 5.1 Chain of Draft (CoD)

**Paper:** Xu et al., Zoom Communications (Feb 2025, arXiv:2502.18600)

**Major 2025 innovation.** Matches CoT accuracy using **only 7.6% of the tokens** (~92% reduction).

Instruction: "Think step by step, but only keep a minimum draft for each thinking step, with 5 words at most."

GSM8K: 91% accuracy vs CoT's 95%, but 80% fewer tokens and 76% less latency. Highly relevant for token-constrained scenarios (small models, long contexts, training data).

### 5.2 SELF-DISCOVER

**Paper:** Zhou et al., Google DeepMind (Feb 2024, arXiv:2402.03620)

Two-stage framework: (1) LLM selects from atomic reasoning modules and composes a task-specific reasoning structure, (2) LLM follows the self-discovered structure. Up to 32% improvement over CoT on BigBench-Hard. Requires 10-40x fewer inference compute than ensemble methods. Discovered structures transfer across model families.

### 5.3 Graph of Thoughts (GoT)

**Paper:** Besta et al. (2024, arXiv:2308.09687)

Generalizes CoT/ToT into arbitrary graph structures where thoughts can be combined, refined, or fed back. Enables merging partial solutions. Sorting task: 62% quality improvement over ToT.

### 5.4 Framework of Thoughts (FoT)

**Paper:** 2026, arXiv:2602.16512

Unifies Chain/Tree/Graph of Thoughts into a single adaptive framework that dynamically selects the right reasoning structure per problem. Represents the consolidation of the "X-of-Thoughts" family.

### 5.5 Skeleton-of-Thought (SoT)

**Paper:** Ning et al. (ICLR 2024, arXiv:2307.15337)

LLM generates a skeleton outline, then fills each point in parallel via batched decoding. Addresses latency rather than accuracy — up to 2.4x speedup with comparable quality.

### 5.6 The Declining Value of Chain-of-Thought

**Paper:** Meincke, Mollick, Mollick, Shapiro (Wharton, June 2025, arXiv:2506.07142)

As models improve, CoT's marginal benefit shrinks:

| Model Type | CoT Impact | Time Cost |
|------------|-----------|-----------|
| Reasoning models (o3-mini, o4-mini) | +2.9-3.1% average | 20-80% increase |
| Gemini Flash 2.5 | **-3.3% (harmful)** | 35-600% increase |
| Non-reasoning models | Small positive | Significant increase |

**Conclusion:** Skip explicit "think step by step" for reasoning models. They already do it internally. The minimal accuracy gains rarely justify the increased response time.

---

## 6. Prompting Reasoning and Thinking Models

The biggest prompting shift of 2025-2026. Models with built-in reasoning (OpenAI o-series, Claude thinking, DeepSeek-R1, Gemini thinking, Qwen3 thinking) require fundamentally different prompting.

### 6.1 The Core Principle

**Tell reasoning models WHAT you want, not HOW to think.** They already have internal chain-of-thought. Define the goal, constraints, and success criteria, then get out of the way.

> "The more information about what you want, contrary to information about how to proceed, the better." — OpenAI docs

### 6.2 Chain-of-Thought is Harmful for Reasoning Models

**"From Harm to Help" (Sept 2025, arXiv:2509.23196):** Even with high-quality demonstrations, few-shot CoT consistently drops accuracy. AIME'25: single-demo drops of 6-16%; 3-shot drops up to **35%**. Legacy CoT demos constrain rather than enhance the reasoning that these models generate on their own.

**Proposed solution (I2S):** Extract transferable *strategies* from demos, decouple answer generation from demonstrations. Converts demos from harmful to beneficial.

### 6.3 OpenAI o-Series (o1, o3, o4-mini)

**DO:**
- Keep prompts simple and direct
- Use delimiters (XML tags, markdown) for structured input
- Start zero-shot — add examples only for complex output requirements
- Be very specific about success criteria and constraints
- Use `reasoning_effort` parameter: low (fast, ~o1-mini), medium (balanced, ~o1), high (best quality)
- Front-load critical rules in tool descriptions (+6% accuracy)
- Use Responses API to persist reasoning state across tool calls (`encrypted_content`)

**DO NOT:**
- Use "think step by step" — degrades performance
- Over-engineer prompts with excessive instructions
- Dump large amounts of context (especially harmful for RAG)
- Use few-shot examples as first approach
- Ask model to "plan more" — over-prompting for planning hurts

**Temperature:** Fixed at 1.0. Cannot be changed. All sampling parameters locked.

**Tool calling:** Tools are integrated natively into the chain of thought. Include anti-hallucination directive: "Do NOT promise to call a function later. If a function call is required, emit it now."

### 6.4 Anthropic Claude Extended/Adaptive Thinking

**Adaptive thinking** (`thinking: {type: "adaptive"}`) is the new default (Claude 4.6). Claude dynamically decides when and how much to think based on the `effort` parameter (low, medium, high, max).

**Key guidance:**
- "Prefer general instructions over prescriptive steps. 'Think thoroughly' often produces better reasoning than a hand-written step-by-step plan."
- Multishot examples work — include `<thinking>` tags inside few-shot examples (unlike o-series)
- 3-5 examples still recommended
- Self-checking: "Before you finish, verify your answer against [test criteria]"
- If overthinking: "Choose an approach and commit. Avoid revisiting decisions unless new information directly contradicts your reasoning."

**The "think" tool** is distinct from extended thinking. A dedicated tool letting Claude pause mid-response for structured reasoning about intermediate tool results. 54% improvement on customer service tasks. Contributed to SWE-bench SOTA (0.623). Best paired with optimized prompting, not just availability.

**Claude 4.6 specific:** Dial back aggressive language. "CRITICAL: You MUST use this tool" → "Use this tool when..." Claude 4.6 overtriggers on emphasis.

### 6.5 DeepSeek-R1

- **Temperature:** 0.5-0.7 range mandatory. **0.6 is the sweet spot.** Too high = reasoning chain fracture; too low = endless repetition.
- **System prompts:** Avoid entirely. Place all instructions in the user prompt. R1's RL training conflicts with rigid system personas.
- **Few-shot examples:** Do not provide. Consistently degrades performance.
- **Structured input:** Use XML or markdown within user message for task definition.
- **For complex problems:** "Take your time and think carefully" can improve performance (unlike "think step by step" which is harmful).

### 6.6 Google Gemini Thinking Mode

- `thinking_level`: minimal, low, medium, high (default)
- For Gemini 3: **Keep temperature at 1.0.** Lowering leads to looping or degraded performance.
- "Think very hard before answering" can help at token cost
- Thought signatures must be returned in multi-turn for maintained reasoning context
- Do NOT ask them to outline reasoning steps — they do it internally

### 6.7 Qwen3 Thinking Mode

- Dual mode: `/think` (reasoning) and `/no_think` (quick) toggleable per-turn
- Model follows most recent instruction in multi-turn conversations
- Qwen3-30B-A3B (MoE) outperforms QwQ-32B with 10x fewer activated parameters

### 6.8 Summary Comparison Table

| Technique | Standard LLMs | Reasoning Models |
|-----------|--------------|-----------------|
| "Think step by step" | Helpful (+5-15%) | Harmful or useless (+3% to -3%) |
| Few-shot examples | Very helpful (3-5) | Harmful (up to -35% on math) |
| System prompt | Essential | Varies: avoid (R1), developer msg (OpenAI), normal (Claude/Gemini) |
| Temperature | Adjustable (0-2) | Fixed 1.0 (OpenAI), 0.6 (R1), adjustable (Claude/Gemini) |
| Verbose instructions | Often helpful | Often harmful — be concise |
| Goal vs steps | Steps help | Goals only — don't prescribe steps |
| RAG/large context | Beneficial | Can hurt — limit to relevant info |
| Output structure | Helps | Helps — define format but not reasoning path |
| Tool descriptions | Standard | Front-load critical info, use strict schemas |

---

## 7. Model-Specific Prompting Guides

### 7.1 OpenAI (GPT-4.1 through GPT-5.4)

#### GPT-4.1 (April 2025)

Three agentic system prompt reminders yielding ~20% SWE-bench improvement:
1. **Persistence:** "Keep going until the user's query is completely resolved"
2. **Tool-Calling:** "If unsure about file content, use tools to read files — do NOT guess"
3. **Planning (optional):** "Plan extensively before each function call, reflect on outcomes" (+4%)

GPT-4.1 follows instructions **more literally** than predecessors. Instruction hierarchy: instructions at END override earlier ones.

**Long context (1M tokens):** XML and pipe-delimited formats excel; JSON arrays perform poorly. For strict adherence: "Only use provided External Context." For flexible reasoning: "Use provided context primarily."

#### GPT-5 (2025)

**Agentic eagerness control:**
- Less eager: Lower `reasoning_effort`, fixed tool call budgets, escape hatches
- More eager: Increase `reasoning_effort`, persistence instructions, discourage clarifying questions

**Self-Reflection Rubric:** Have the model create a 5-7 category excellence rubric before implementation, use it to iterate internally.

#### GPT-5.1

- **Parallel tool execution:** "Batch reads and edits to speed up the process" — explicit instruction required
- **Planning tools:** Lightweight TODO with 2-5 milestones, update every ~8 tool calls
- **First-Response Immediacy:** "Always explain what you're doing FIRST" — reduces perceived latency
- "Small, specific prompt changes produce large improvements" due to high steerability

#### GPT-5.2 (2026)

- **Scope discipline:** "Implement EXACTLY and ONLY what the user requests"
- **Long-context grounding:** For >10K tokens, instruct model to first produce internal outline
- **High-risk self-check:** Mandatory review for unstated assumptions and ungrounded numbers
- **Ambiguity mitigation:** Present 2-3 plausible interpretations rather than asking questions

#### GPT-5.4 (March 2026)

- **Completeness contracts:** Internal checklists of deliverables
- **Verification loops:** Pre-flight → Execute → Post-flight confirmation
- **Research mode:** Plan (3-6 sub-questions) → Retrieve → Synthesize → Stop
- **reasoning_effort defaults:** none (fast), low (latency-sensitive), medium/high (reasoning), xhigh (only if evals show benefit)

#### Recommended Prompt Template (OpenAI)

```
# Role and Objective
# Instructions
## Sub-categories for detailed instructions
# Reasoning Steps
# Output Format
# Examples
## Example 1
# Context
# Final instructions and step-by-step prompt
```

### 7.2 Anthropic (Claude 4.6)

#### Communication Style
- More concise and natural than previous models. May skip verbal summaries after tool calls.
- More direct and grounded. Fact-based progress reports.
- If you want summaries: "After completing a task that involves tool use, provide a quick summary."

#### Overtriggering on Aggressive Language
**Critical for Claude 4.6:** Opus 4.5 and 4.6 are more responsive to system prompts. Prompts designed to reduce undertriggering now **overtrigger**.

Fix: "CRITICAL: You MUST use this tool when..." → "Use this tool when..."

#### Overthinking / Excessive Exploration
Opus 4.6 does significantly more upfront exploration. To constrain:
- Replace blanket defaults with targeted instructions
- Remove over-prompting — tools that undertriggered before now trigger appropriately
- "Choose an approach and commit. Avoid revisiting decisions unless new information contradicts your reasoning."

#### XML Tags
- Help parse complex prompts unambiguously. No canonical "best" tags — use descriptive names.
- Nest for hierarchy: `<documents><document index="1"><source>...</source><content>...</content></document></documents>`
- Combine with other techniques: `<examples>` with few-shot, `<thinking>` + `<answer>` with CoT

#### Long Context
- Put longform data at the top, queries at the end (+30% quality)
- Ask Claude to quote relevant parts first before the task
- Wrap documents in `<document index="N">` tags with `<source>` subtags

#### Tool Use
- **Tool descriptions are by far the most important factor.** Aim for 3-4+ sentences per tool.
- Write descriptions "like onboarding a teammate" — context, terminology, relationships
- Use meaningful namespacing: `github_list_prs`, `slack_send_message`
- Return semantic, human-readable identifiers rather than opaque UUIDs
- Parallel tool calling: Claude 4.6 excels — ~100% success with explicit guidance

#### Prefilled Responses Deprecated
Starting with Claude 4.6, prefilled responses on the last assistant turn are no longer supported. Use Structured Outputs or direct instructions instead.

### 7.3 Google (Gemini 3)

#### Temperature Must Stay at 1.0
"Gemini 3's reasoning capabilities are optimized for the default temperature setting." Lowering may cause looping or degraded performance. Sharp departure from prior Gemini models.

#### Key Gemini 3 Patterns
- **Avoid broad negative constraints.** "Do not infer" → "Use the provided additional information for deductions and avoid using outside knowledge."
- **Persona adherence:** Model takes assigned personas seriously, sometimes prioritizing them over instructions. Review carefully.
- **Default verbosity is low.** Explicitly request verbose style if needed.
- **Pre-tool reflection for agentic use:** State why you're calling a tool, specify expected data, explain how it solves the problem.
- **Self-monitoring:** Use TODO lists with checkboxes. Review outputs against constraints before submission.

#### Function Calling

Four modes: AUTO (default), ANY (force function call), VALIDATED (schema-adherent), NONE (prohibit).

- Clear, specific descriptions with examples in parameters
- Enum arrays for fixed value sets
- **Limit active tools to 10-20** for optimal selection
- Parallel and compositional (sequential chaining) calling supported
- Multimodal function responses (images, PDFs) in Gemini 3

#### Google/Kaggle Prompt Engineering Whitepaper (Early 2025)
69-page guide. Always include few-shot examples. Place questions at the end after data context. Structured approach: role assignment, context-setting, instruction clarity, formatting.

---

## 8. Tool Use and Function Calling

### 8.1 Tool Description Best Practices

Tool descriptions are the highest-leverage optimization for tool-calling agents. Across all labs:

| Lab | Guidance |
|-----|----------|
| **Anthropic** | 3-4+ sentences per tool. Write like onboarding a teammate. Even small refinements yield dramatic improvements. |
| **OpenAI** | "Apply software engineering best practices. Make functions obvious and intuitive. Pass the intern test: can a human correctly use the function given only what you gave the model?" |
| **Google** | Include examples in parameter descriptions. Use enum arrays for fixed values. |

### 8.2 Tool Naming and Parameter Design

- **Meaningful namespacing:** Prefix with service (`github_list_prs`, `slack_send_message`). Makes selection unambiguous as library grows.
- **Unambiguous parameters:** Replace `user` with `user_id`.
- **Sensible defaults** for pagination, filtering, truncation.
- **Flat argument structures** over deeply nested objects (OpenAI: under 20 args per function).
- **`strict: true`** on all function definitions for reliable schema adherence.
- All fields marked `required`; use `null` as type option for optional fields.

### 8.3 How Many Tools Is Too Many

Performance degrades gradually beyond ~10-19 tools without retrieval-augmented approaches:

| Tool Count | Expected Behavior |
|------------|------------------|
| 1-3 tools | Safe and efficient |
| 4-10 tools | Feasible but slower, more tokens consumed |
| 10-19 tools | Workable with good descriptions |
| 19+ tools | Degradation begins (RAG-MCP finding) |
| 100+ tools | Severe degradation |

**Solutions:**
- **RAG-MCP** (arXiv:2505.03275): Dynamically retrieve relevant tool subset per query. 50%+ prompt token reduction, 3x selection accuracy.
- **Anthropic Tool Search Tool:** Mark tools with `defer_loading: true`. Accuracy improved from 49% to 74%.
- **Manus tool masking:** Instead of adding/removing tools (breaks KV-cache), mask token logits during decoding.

### 8.4 Return Value Design

- Return only **high-signal information.** Bloated responses waste context.
- Include **semantic, human-readable identifiers** (not opaque UUIDs) — "significantly improves precision."
- Include contextual metadata to enable downstream decisions without additional calls.
- Provide **actionable error responses** with specific guidance, not opaque error codes.

### 8.5 Error Handling

- Provide tool error messages back to the model (never empty responses).
- Include error-handling guidelines in the system prompt.
- Tools should return informative messages that guide recovery.
- Assume responses can include zero, one, or multiple tool calls.

### 8.6 Parallel Tool Calling

- Claude 4.6: ~100% success with explicit guidance: "If you intend to call multiple tools and there are no dependencies between the calls, make all independent calls in parallel."
- GPT-5.1: "Parallelize tool calls whenever possible. Batch reads and edits."
- OpenAI o3/o4-mini: Explicitly order when sequence matters.

### 8.7 Advanced Tool Patterns (Anthropic, 2026)

1. **Tool Search Tool:** Dynamically discover tools on-demand. Preserves 191,300 tokens. Use when: >10K tokens of definitions, 10+ tools.
2. **Programmatic Tool Calling:** Claude writes Python to orchestrate multiple tool calls. 37% token reduction, eliminates 19+ inference passes. Use when: processing large datasets, 3+ dependent calls.
3. **Tool Use Examples:** Provide sample tool calls in definitions. Accuracy improved from 72% to 90% on complex parameters.

---

## 9. Agentic Prompting Architectures

### 9.1 The ReAct Foundation

ReAct (Yao et al., 2022) established the dominant agentic pattern: interleave Thought → Action → Observation in a loop. Virtually all modern agent architectures (Claude Code, Codex CLI, game agents) follow this pattern.

### 9.2 Five Workflow Patterns (Anthropic)

1. **Prompt Chaining:** Sequential steps, each processing previous output. Include programmatic gates between steps.
2. **Routing:** Classify input, direct to specialized handlers. Enables separation of concerns.
3. **Parallelization:** Simultaneous LLM calls. Sectioning (independent subtasks) or Voting (consensus).
4. **Orchestrator-Workers:** Central LLM dynamically decomposes and delegates. Subtasks determined at runtime.
5. **Evaluator-Optimizer:** Generate + evaluate + refine loop. Works when "responses can be demonstrably improved with human-articulable feedback."

### 9.3 Agent-Specific Prompting Checklist

From OpenAI's GPT-4.1 through GPT-5.4 guides, synthesized:

1. **Persistence reminder:** "Keep going until the user's query is completely resolved"
2. **Tool-calling reminder:** "Use tools to read information — do NOT guess or make up answers"
3. **Planning reminder (optional):** "Plan extensively before each function call"
4. **Progress updates:** Every ~8 tool calls or ~30 seconds
5. **Completion verification:** "Zero in_progress and zero pending items"
6. **Error recovery:** Fallback queries, prerequisite checks, 1-2 retry strategies before concluding
7. **Autonomy boundary:** When to ask the user vs when to proceed with defaults

### 9.4 Multi-Agent Orchestration

Anthropic's multi-agent research system outperformed single-agent Opus 4 by **90.2%**. Architecture: Opus 4 lead agent + Sonnet 4 subagents.

Eight prompting strategies for multi-agent systems:
1. **Mental modeling:** Simulate agent behavior to catch failure modes
2. **Delegation framework:** Explicit objectives, output formats, tool guidance per subagent
3. **Effort scaling:** Simple = 1 agent, 3-10 tool calls; complex = 10+ subagents
4. **Tool selection:** Distinct purposes, clear descriptions
5. **Self-improvement:** Use Claude to diagnose agent failures and suggest improvements
6. **Search strategy:** Explore broadly first, then narrow
7. **Extended thinking:** Both lead and subagents benefit
8. **Parallelization:** 3-5 simultaneous subagents cuts research time by up to 90%

Token economics: Agents use ~4x more tokens than chat. Multi-agent ~15x more. Token usage alone explains 80% of performance variance.

### 9.5 The "Think" Tool Pattern

A dedicated tool with a single `thought` string parameter, no side effects, letting agents pause mid-response for structured reasoning. Different from extended thinking (which operates before response generation).

Performance: **54% improvement** on airline customer service tasks. Contributed to SWE-bench SOTA (0.623).

When to use: tool output analysis, policy-heavy environments, sequential decision-making. NOT for straightforward instructions.

"Simply making the 'think' tool available might improve performance somewhat, but pairing it with optimized prompting yielded dramatically better results." — Anthropic

### 9.6 Long-Running Agent Harnesses

**Two-agent architecture** (Anthropic):
- **Initializer Agent:** Runs once to establish infrastructure (tests, setup scripts, specs)
- **Coding Agent:** Executes repeatedly across multiple context windows

Best practices:
- Write feature lists in JSON format (not Markdown) — model is less likely to overwrite JSON
- "One feature at a time" directive prevents exhausting context
- Browser automation (Playwright MCP) for verification "dramatically improved performance"
- Consider starting fresh over compaction — Claude 4.6 discovers state from filesystem effectively
- Progress tracking files + git commits as checkpoints

### 9.7 Memory Architectures for Agents (2026)

- **MemGPT/Letta:** OS-inspired tiered memory (hot/warm/cold)
- **Mem0:** Graph-based memory
- **Memori:** Semantic triples (arXiv:2603.19935)
- **Manus todo.md recitation:** Continuously update and recite objectives to prevent lost-in-the-middle
- **File system as extended context:** Write large observations to filesystem, keep only references in context
- **Preserve error evidence:** Keep failed actions in context — models implicitly update beliefs from error traces

---

## 10. Agentic Coding Tools

### 10.1 CLAUDE.md / Rules Files

**CLAUDE.md (Anthropic):**
- Keep under 200 lines. Bloated files cause Claude to ignore instructions.
- Only include what Claude can't infer from code
- Include: bash commands, non-default style rules, testing instructions, architectural decisions, gotchas
- Exclude: standard conventions, API docs (link instead), things that change frequently
- Use emphasis sparingly: "IMPORTANT" or "YOU MUST" for critical rules
- Hierarchical: `~/.claude/CLAUDE.md` (global) → `./CLAUDE.md` (project) → `./CLAUDE.local.md` (personal)

**AGENTS.md (Linux Foundation standard, Dec 2025):**
- Adopted by 60,000+ open-source projects. Supported by Claude Code, Cursor, Copilot, Gemini CLI, Codex, Windsurf, Aider, and others.
- Standard Markdown, no required schema. Discovery: walks from git root to CWD.
- Max 32 KiB combined (configurable)

**Cursor Rules (.cursor/rules/):**
- Under 2,000 words, 10-15 conventions max. Rule blocks under 500 lines each.
- Legacy `.cursorrules` is deprecated.

**Windsurf Rules:**
- 3-5 highly specific rules outperform long rule lists. Bullet points over paragraphs.

### 10.2 Claude Code System Prompt Architecture

110+ conditional strings assembled dynamically (not monolithic). Includes: intro, system rules, tool usage, permissions, task philosophy, tone, session guidance, memory, environment info, MCP instructions, git status.

Sub-agents: Plan Agent (636 tokens), Explore Agent (494 tokens), Security Reviewer (2,607 tokens), Verification Specialist (2,938 tokens).

Static/dynamic cache boundary for prompt caching. CLAUDE.md injected as system-reminder. Priority: Built-in > Session context > CLAUDE.md.

### 10.3 SWE-bench: Scaffolding Is the Moat

**Critical finding:** Same LLM scored 42% and 78% by changing only scaffolding — a **36-point improvement** without model changes. Six frontier models now score within 0.8 points (79.6%-80.9%).

> "The model is a commodity. The harness is your moat." — Particula Tech

Six high-impact scaffolding components:

| Component | Impact |
|-----------|--------|
| Error recovery & rollback | +5-15 points |
| Planning-execution separation | +5-10 points |
| Context management (adaptive summarization) | +3-8 points |
| Tool orchestration (parallel search) | +2-4 points |
| Persistent memory | Varies |
| Retry logic (decreasing budgets) | Varies |

### 10.4 Manus Production Insights

- **KV-cache hit rate** is the #1 production metric. ~100:1 input-to-output token ratio.
- Stable prompt prefixes: "even a single-token difference can invalidate the cache"
- **Append-only contexts:** Never modify previous actions; deterministic JSON serialization
- **Tool masking over tool removal:** Instead of add/remove (breaks cache), mask token logits
- **File system as extended context:** Write large observations to sandbox, keep references in context

### 10.5 Nine Critical Failure Patterns of Coding Agents

From DAPLab, Columbia (Jan 2026):
1. UI/visual grounding mismatch
2. State management failures across components
3. Business logic mismatch (correct code, wrong logic)
4. Data/schema management errors
5. API integration failures (hallucinated credentials)
6. Security vulnerabilities (missing access control)
7. Repeated/duplicated code (failure to abstract)
8. Codebase awareness degradation with project size
9. Error suppression over error communication

### 10.6 Debugging Decay

Debugging follows a decay pattern: each additional attempt is less effective. Most models lose majority of capability within 2-3 iterations. **Strategic "fresh starts"** often outperform continued iteration.

---

## 11. Automatic Prompt Optimization

### 11.1 APE (Automatic Prompt Engineer)

**Paper:** Zhou et al. (ICLR 2023, arXiv:2211.01910)

LLM generates candidate instructions, evaluates on held-out data, selects best. Matched or beat human-written prompts on 21/24 tasks. Foundational work that led to OPRO, DSPy, and the entire APO field.

### 11.2 OPRO (Optimization by Prompting)

**Paper:** Yang et al., Google DeepMind (2023, arXiv:2309.03409)

LLM iteratively generates and evaluates prompts, tracking accuracy. Discovered "Take a deep breath and work on this problem step-by-step" outperformed manual prompts.

### 11.3 DSPy (Stanford NLP)

Paradigm shift from "prompting" to "programming" LMs. Define Signatures (input/output specs) and Modules (prompting strategies), then a compiler automatically optimizes prompts.

- **MIPROv2:** Joint instruction + few-shot optimization
- **GEPA (ICLR 2026 Oral):** Genetic-Pareto, uses LM reflection on trajectories — outperforms RL (GRPO)
- 160K monthly downloads, production deployment patterns documented
- DSPy 3.0 planned

### 11.4 Meta-Prompting

Use a more capable model to optimize prompts for less capable models:

```
Improve the following prompt to generate a more detailed [output type].
Adhere to prompt engineering best practices.
{simple_prompt}
Only return the prompt.
```

**GPT-5+ self-optimization:** "When asked to optimize prompts, explain what phrases could be added/deleted to more consistently elicit desired behavior."

### 11.5 Promptfoo

13.2k GitHub stars, 300k+ developers, 127 Fortune 500 companies. **Acquired by OpenAI (March 2026)**. MIT licensed. Scans 50+ vulnerability types. CI/CD discipline for prompt testing.

---

## 12. What Doesn't Work (Anti-Patterns)

### 12.1 Politeness and Emotion

**Mixed and model-dependent.** Impolite prompts produce poor performance, but overly polite doesn't guarantee better. One October 2025 study found impolite prompts outperformed polite (84.8% vs 80.8%). Emotional prompting shows ~10.9% average improvement across 45 tasks, but threats specifically do NOT outperform positive encouragement. Politeness paradox: polite prompts make LLMs more readily generate disinformation.

**Bottom line:** Neutral to moderate tone is sufficient.

### 12.2 Threatening the Model

"Your job depends on this" — testing with Claude found threats produced identical quality output. However, emotional manipulation + prompt injection can increase dangerous misinformation from 6.2% to 37.5%.

### 12.3 Capitalization and Emphasis (MUST, CRITICAL)

**No conclusive evidence that capitalization improves compliance.** Anthropic specifically recommends against aggressive language for Claude 4.6 — "CRITICAL," "MUST," "No exceptions" can over-trigger compliance mechanisms counterproductively. Use clear, calm directives with explanations of WHY.

### 12.4 Overly Long System Prompts

- ~500 words optimal
- ~3,000 tokens: measurable reasoning degradation
- Per 100 words beyond 500: ~12% comprehension drop
- The "identification without exclusion" problem: LLMs identify irrelevant details but struggle to ignore them

### 12.5 Contradictory Instructions

Models exhibit "contradiction blindness" (don't detect conflicts), "false reconciliation" (satisfy both with vague output), and "cascade failures." Explicit Conflict Acknowledgement Rates: 0% (Qwen) to 20.3% (Llama-70B). Models spend reasoning tokens reconciling contradictions — resolve conflicts in prompts to save compute.

### 12.6 Role-Playing Effectiveness

Modest improvement (0.519 to 0.571). Works by steering internal distribution. Does not guarantee factual grounding. Simple roles give tone/structure; detailed personas with professional background produce more nuanced responses.

### 12.7 The CoT Myth for Reasoning Models

Explicit "think step by step" is harmful or useless for reasoning models. See Section 6 for full details.

---

## 13. Prompt Caching and Cost Optimization

### 13.1 How Prompt Caching Works

Available from all major providers. Requires exact prefix match.

| Provider | Min Length | Cost Savings | Latency Savings |
|----------|-----------|-------------|----------------|
| Anthropic | - | Up to 90% input cost, writes +25% | Up to 85% |
| OpenAI | 1024+ tokens | 50% input cost | Up to 80% |
| Google | Varies | Significant | Significant |

### 13.2 Cache-Aware Prompt Structure

- **Static content at the beginning:** System instructions, tool definitions, background info
- **Dynamic content at the end:** User input, session-specific data, volatile state
- A single-token difference invalidates the cache (Manus: "stable prompt prefixes are critical")
- Append-only contexts with deterministic JSON serialization
- Anthropic: default cache TTL 5 minutes; 1-hour for batch processing

### 13.3 Chain of Draft for Token Efficiency

Chain of Draft (Section 5.1) achieves 92% token reduction matching CoT accuracy. For training data and small models, this is a massive cost and context savings.

### 13.4 Cost Optimization Strategies

- **Model routing:** Build classifier to route queries to appropriate model tiers. Companies report **40-60% cost reductions** without quality loss for routine queries (Vercel AI SDK).
- **Hill-climb quality, down-climb cost:** Start with detailed prompts, compress to ~200 tokens for 76% cost reduction.
- **Context compaction:** Summarize at context limits; tool result clearing is safest lightweight form.

---

## 14. Security and Robustness

### 14.1 Instruction Hierarchy

Formalized by OpenAI (Wallace et al., ICLR 2025): system > user > tool output. Extended to many-tier hierarchies (arXiv:2604.09443, 2026) for multi-agent systems.

**HIPO (arXiv:2603.16152, 2026):** Primal-dual safe RL that dynamically maximizes user prompt utility while guaranteeing system prompt compliance.

**Current state:** Instruction hierarchies are trained, not structural. Models follow priority 9.6-45.8% of the time under genuine conflict. Over-refusal on benign queries is a trade-off.

### 14.2 Prompt Injection

Joint research by OpenAI, Anthropic, and Google DeepMind tested 12 published defenses — **every defense was bypassed with >90% success** under adaptive attacks. Simple "ignore previous instructions" reaches up to 100% success on certain tasks.

**Simon Willison's Six Design Patterns (June 2025):**
1. **Plan-then-Execute:** Fixed execution plan before processing untrusted data
2. **Dual LLM:** Privileged LLM (has tools, never sees untrusted data) + Quarantined LLM (sees data, no tools)
3-6. Additional patterns in full analysis

### 14.3 MCP Security

MCP expands attack surfaces with tool poisoning, credential theft, and indirect injection through tool outputs (arXiv:2503.23278). ACM TOSEM paper published in 2026 on MCP security. Indirect injection is the real enterprise threat.

### 14.4 Multi-Agent Defense

Coordinated LLM agents can detect and neutralize injections in real-time. One system achieved 100% mitigation (ASR reduced to 0%) on 55 unique attacks (arXiv:2509.14285).

---

## 15. References

### Official Documentation

- [OpenAI Prompt Engineering Guide](https://developers.openai.com/api/docs/guides/prompt-engineering)
- [GPT-4.1 Prompting Guide](https://developers.openai.com/cookbook/examples/gpt4-1_prompting_guide) (April 2025)
- [GPT-5 Prompting Guide](https://developers.openai.com/cookbook/examples/gpt-5/gpt-5_prompting_guide) (2025)
- [GPT-5.1 Prompting Guide](https://developers.openai.com/cookbook/examples/gpt-5/gpt-5-1_prompting_guide) (2025)
- [GPT-5.2 Prompting Guide](https://developers.openai.com/cookbook/examples/gpt-5/gpt-5-2_prompting_guide) (2026)
- [GPT-5.4 Prompt Guidance](https://developers.openai.com/api/docs/guides/prompt-guidance) (March 2026)
- [OpenAI o3/o4-mini Function Calling Guide](https://developers.openai.com/cookbook/examples/o-series/o3o4-mini_prompting_guide) (April 2025)
- [OpenAI Reasoning Models Guide](https://developers.openai.com/api/docs/guides/reasoning)
- [OpenAI Function Calling](https://platform.openai.com/docs/guides/function-calling)
- [OpenAI Structured Outputs](https://platform.openai.com/docs/guides/structured-outputs)
- [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)
- [OpenAI Prompt Caching](https://platform.openai.com/docs/guides/prompt-caching)
- [OpenAI Codex Prompting](https://developers.openai.com/codex/prompting)
- [OpenAI Codex AGENTS.md Guide](https://developers.openai.com/codex/guides/agents-md)
- [Anthropic Prompting Best Practices (Claude 4.6)](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/claude-4-best-practices)
- [Anthropic XML Tags Guide](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/use-xml-tags)
- [Anthropic Long Context Tips](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/long-context-tips)
- [Anthropic Tool Use](https://docs.anthropic.com/en/docs/build-with-claude/tool-use)
- [Anthropic Prompt Caching](https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching)
- [Anthropic Extended Thinking Tips](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/extended-thinking-tips)
- [Claude Code Best Practices](https://code.claude.com/docs/en/best-practices)
- [Gemini Prompt Design Strategies](https://ai.google.dev/gemini-api/docs/prompting-strategies)
- [Gemini 3 Prompting Guide (Vertex AI)](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/start/gemini-3-prompting-guide)
- [Gemini 3 Developer Guide](https://ai.google.dev/gemini-api/docs/gemini-3)
- [Gemini Function Calling](https://ai.google.dev/gemini-api/docs/function-calling)
- [Gemini Structured Output](https://ai.google.dev/gemini-api/docs/structured-output)
- [Google/Kaggle Prompt Engineering Whitepaper](https://www.kaggle.com/whitepaper-prompt-engineering) (69 pages, early 2025)
- [DeepSeek R1 Prompting Guidelines](https://docs.together.ai/docs/prompting-deepseek-r1)
- [Qwen3 Blog: Think Deeper, Act Faster](https://qwenlm.github.io/blog/qwen3/)
- [AGENTS.md Standard](https://agents.md/)

### Engineering Blogs and Guides

- [Anthropic: Building Effective Agents](https://www.anthropic.com/research/building-effective-agents) (Dec 2024)
- [Anthropic: Effective Context Engineering for AI Agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) (June 2025)
- [Anthropic: The "Think" Tool](https://www.anthropic.com/engineering/claude-think-tool)
- [Anthropic: Writing Tools for Agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
- [Anthropic: Advanced Tool Use](https://www.anthropic.com/engineering/advanced-tool-use)
- [Anthropic: Multi-Agent Research System](https://www.anthropic.com/engineering/multi-agent-research-system)
- [Anthropic: Effective Harnesses for Long-Running Agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [Manus: Context Engineering Lessons](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)
- [Drew Breunig: How Claude Code Builds a System Prompt](https://www.dbreunig.com/2026/04/04/how-claude-code-builds-a-system-prompt.html)
- [Drew Breunig: System Prompts Define Agent Behavior](https://www.dbreunig.com/2026/02/10/system-prompts-define-the-agent-as-much-as-the-model.html)
- [Lilian Weng: LLM-Powered Autonomous Agents](https://lilianweng.github.io/posts/2023-06-23-agent/) (June 2023)
- [Lilian Weng: Why We Think](https://lilianweng.github.io/posts/2025-05-01-thinking/) (May 2025)
- [Simon Willison: Prompt Injection Design Patterns](https://simonwillison.net/2025/Jun/13/prompt-injection-design-patterns/) (June 2025)
- [Eugene Yan: What We Learned from a Year of Building with LLMs](https://www.oreilly.com/radar/what-we-learned-from-a-year-of-building-with-llms-part-i/)
- [Augment Code: 11 Prompting Techniques for Better AI Agents](https://www.augmentcode.com/blog/how-to-build-your-agent-11-prompting-techniques-for-better-ai-agents)
- [Particula: Agent Scaffolding Beats Model Upgrades](https://particula.tech/blog/agent-scaffolding-beats-model-upgrades-swe-bench)
- [MIT Missing Semester: Agentic Coding](https://missing.csail.mit.edu/2026/agentic-coding/) (2026)
- [DAPLab Columbia: 9 Critical Failure Patterns](https://daplab.cs.columbia.edu/general/2026/01/08/9-critical-failure-patterns-of-coding-agents.html) (Jan 2026)
- [Phil Schmid: Gemini 3 Prompting Best Practices](https://www.philschmid.de/gemini-3-prompt-practices)
- [DAIR.AI Prompt Engineering Guide](https://www.promptingguide.ai/)
- [Gwern: System Prompts 2025](https://gwern.net/system-prompts-2025)
- [Martin Fowler: Function Calling Using LLMs](https://martinfowler.com/articles/function-call-LLM.html)
- [Addy Osmani: Code Agent Orchestra](https://addyosmani.com/blog/code-agent-orchestra/)

### Foundational Papers

- Wei et al. "Chain-of-Thought Prompting Elicits Reasoning in Large Language Models" (NeurIPS 2022) — [arXiv:2201.11903](https://arxiv.org/abs/2201.11903)
- Wang et al. "Self-Consistency Improves Chain of Thought Reasoning" (ICLR 2023) — [arXiv:2203.11171](https://arxiv.org/abs/2203.11171)
- Yao et al. "Tree of Thoughts: Deliberate Problem Solving" (NeurIPS 2023) — [arXiv:2305.10601](https://arxiv.org/abs/2305.10601)
- Yao et al. "ReAct: Synergizing Reasoning and Acting" (ICLR 2023) — [arXiv:2210.03629](https://arxiv.org/abs/2210.03629)
- Zhou et al. "Least-to-Most Prompting" (ICLR 2023) — [arXiv:2205.10625](https://arxiv.org/abs/2205.10625)
- Zhou et al. "Large Language Models Are Human-Level Prompt Engineers" (ICLR 2023) — [arXiv:2211.01910](https://arxiv.org/abs/2211.01910)
- Shinn et al. "Reflexion: Language Agents with Verbal Reinforcement Learning" (NeurIPS 2023) — [arXiv:2303.11366](https://arxiv.org/abs/2303.11366)
- Madaan et al. "Self-Refine: Iterative Refinement with Self-Feedback" (2023) — [arXiv:2303.17651](https://arxiv.org/abs/2303.17651)
- Ouyang et al. "Training language models to follow instructions with human feedback" (InstructGPT, NeurIPS 2022) — [arXiv:2203.02155](https://arxiv.org/abs/2203.02155)

### 2024-2026 Research Papers

- Xu et al. "Chain of Draft: Thinking Faster by Writing Less" (Feb 2025) — [arXiv:2502.18600](https://arxiv.org/abs/2502.18600)
- Meincke et al. "The Decreasing Value of Chain of Thought in Prompting" (Wharton, June 2025) — [arXiv:2506.07142](https://arxiv.org/abs/2506.07142)
- "From Harm to Help: Turning Reasoning Demos into Assets" (Sept 2025) — [arXiv:2509.23196](https://arxiv.org/abs/2509.23196)
- Zhou et al. "SELF-DISCOVER: LLMs Self-Compose Reasoning Structures" (DeepMind, Feb 2024) — [arXiv:2402.03620](https://arxiv.org/abs/2402.03620)
- Besta et al. "Graph of Thoughts" (2024) — [arXiv:2308.09687](https://arxiv.org/abs/2308.09687)
- "Framework of Thoughts" (2026) — [arXiv:2602.16512](https://arxiv.org/abs/2602.16512)
- Ning et al. "Skeleton-of-Thought: Parallel Generation" (ICLR 2024) — [arXiv:2307.15337](https://arxiv.org/abs/2307.15337)
- Liu et al. "Lost in the Middle: How LLMs Use Long Contexts" (TACL 2024) — [arXiv:2307.03172](https://arxiv.org/abs/2307.03172)
- Hsieh et al. "Found in the Middle: Calibrating Positional Attention Bias" (ACL 2024) — [arXiv:2406.16008](https://arxiv.org/abs/2406.16008)
- Wallace et al. "The Instruction Hierarchy" (ICLR 2025) — [arXiv:2404.13208](https://arxiv.org/abs/2404.13208)
- "Many-Tier Instruction Hierarchy in LLM Agents" (2026) — [arXiv:2604.09443](https://arxiv.org/abs/2604.09443)
- "HIPO: Instruction Hierarchy via Constrained RL" (2026) — [arXiv:2603.16152](https://arxiv.org/abs/2603.16152)
- Gan & Sun. "RAG-MCP: Mitigating Prompt Bloat in Tool Selection" (2025) — [arXiv:2505.03275](https://arxiv.org/abs/2505.03275)
- "Help or Hurdle? Rethinking MCP-Augmented LLMs" (2025) — [arXiv:2508.12566](https://arxiv.org/abs/2508.12566)
- "MCP Security Threats and Future Directions" (2025) — [arXiv:2503.23278](https://arxiv.org/abs/2503.23278)
- "Natural Language Tools" (2025) — [arXiv:2510.14453](https://arxiv.org/abs/2510.14453)
- "ToolACE: Winning Points of LLM Function Calling" (ICLR 2025) — [arXiv:2409.00920](https://arxiv.org/abs/2409.00920)
- "ToolLLM: Facilitating LLMs to Master 16000+ APIs" (ICLR 2024) — [arXiv:2307.16789](https://arxiv.org/abs/2307.16789)
- "AgentFlow" (ICLR 2026 Oral) — [arXiv:2510.05592](https://arxiv.org/abs/2510.05592)
- "Orak: 12-game MCP benchmark" (ICLR 2026) — [arXiv:2506.03610](https://arxiv.org/abs/2506.03610)
- "GamingAgent / lmgame-Bench" (ICLR 2026) — [arXiv:2505.15146](https://arxiv.org/abs/2505.15146)
- "Agent-R1: End-to-End RL for LLM Agents" (2025) — [arXiv:2511.14460](https://arxiv.org/abs/2511.14460)
- "MAR: Multi-Agent Reflexion" (2025) — [arXiv:2512.20845](https://arxiv.org/abs/2512.20845)
- "Agentic AI: Architectures and Taxonomies" (2026) — [arXiv:2601.12560](https://arxiv.org/abs/2601.12560)
- "Tool Interaction Optimization via RL" (March 2026) — [arXiv:2603.21972](https://arxiv.org/abs/2603.21972)
- Yang et al. "OPRO: Large Language Models as Optimizers" (DeepMind, 2023) — [arXiv:2309.03409](https://arxiv.org/abs/2309.03409)
- Suzgun & Kalai. "Meta-Prompting: Task-Agnostic Scaffolding" (2024) — [arXiv:2401.12954](https://arxiv.org/abs/2401.12954)
- "LATS: Language Agent Tree Search" (ICML 2024) — [arXiv:2310.04406](https://arxiv.org/abs/2310.04406)
- Khattab et al. "DSPy: Compiling Declarative LM Calls" (Stanford NLP) — [dspy.ai](https://dspy.ai/)
- "Agentic RAG Survey" (2025) — [arXiv:2501.09136](https://arxiv.org/abs/2501.09136)
- Chroma. "Context Rot" (July 2025) — [trychroma.com/research/context-rot](https://www.trychroma.com/research/context-rot)
- "Mind Your Step (by Step)" (Oct 2024) — [arXiv:2410.21333](https://arxiv.org/abs/2410.21333)

### Surveys

- Schulhoff et al. "The Prompt Report: A Systematic Survey of Prompting Techniques" (2024, 76 pages) — [arXiv:2406.06608](https://arxiv.org/abs/2406.06608)
- Sahoo et al. "A Systematic Survey of Prompt Engineering in LLMs" (2024, updated 2025) — [arXiv:2402.07927](https://arxiv.org/abs/2402.07927)
- "Chain-of-X Paradigms Survey" (COLING 2025)
- "Efficient Prompting Methods Survey" (2024) — [arXiv:2404.01077](https://arxiv.org/abs/2404.01077)
- "Landscape of Agent Architectures for Reasoning, Planning, and Tool Calling" (2024) — [arXiv:2404.11584](https://arxiv.org/abs/2404.11584)
- "Tool Interaction Taxonomy: Prompting, SFT, RL" (April 2026) — [arXiv:2604.00835](https://arxiv.org/abs/2604.00835)

### Mechanistic Understanding

- Olsson et al. "In-Context Learning and Induction Heads" (Transformer Circuits, 2022) — [transformer-circuits.pub](https://transformer-circuits.pub/2022/in-context-learning-and-induction-heads/index.html)
- Turpin et al. "Language Models Don't Always Say What They Think: Unfaithful Explanations in CoT" (2023) — [arXiv:2305.04388](https://arxiv.org/abs/2305.04388)
- "Coconut: Chain of Continuous Thought" (2024) — [arXiv:2412.06769](https://arxiv.org/abs/2412.06769)
- Neumann et al. "Position is Power" (ACM FAccT 2025) — [arXiv:2505.21091](https://arxiv.org/abs/2505.21091)
- "Is Chain-of-Thought a Mirage?" (2025) — [arXiv:2508.01191](https://arxiv.org/abs/2508.01191)

### Community and Industry

- [steipete/agent-rules](https://github.com/steipete/agent-rules) — Production rule files for multi-tool setups
- [Piebald-AI/claude-code-system-prompts](https://github.com/Piebald-AI/claude-code-system-prompts)
- [Promptfoo](https://www.promptfoo.dev/) — Prompt testing framework (acquired by OpenAI March 2026)
- [LangChain State of Agent Engineering](https://www.langchain.com/state-of-agent-engineering)
- [SWE-agent ACI Documentation](https://swe-agent.com/1.0/background/aci/)
- [GamingAgent GitHub](https://github.com/lmgame-org/GamingAgent)
