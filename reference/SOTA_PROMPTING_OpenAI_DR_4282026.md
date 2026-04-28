# SOTA Guide to Prompting Tool-Calling LLM Agents

## Core conclusion

The strongest 2025–early 2026 evidence points to a clear shift: high-performing tool-calling agents are no longer mostly about clever “ReAct-style” wording tricks. They are mostly about **context engineering, tool-interface quality, and correct state handoff**. Across official guidance from OpenAI, Anthropic, and Google’s Gemini docs, plus benchmark work from entity["organization","UC Berkeley","Berkeley, CA, US"] and recent tool-description papers, the winning pattern is a **short, high-authority prompt with explicit goals, constraints, and output contract; a small, non-overlapping toolset expressed in native structured schemas; and a harness that faithfully returns the model’s hidden state or tool context between turns when the provider requires it**. Verbose, brittle prompts that try to script every step are increasingly a downgrade, especially on modern reasoning models. citeturn27view3turn9view2turn28view1turn33view0turn19view4turn23view4turn23view0

A second strong conclusion is that **tool descriptions are part of the prompt**, not just metadata. Tool-selection accuracy and argument quality depend heavily on the clarity of the tool contract. Foundational work showed that documentation alone can enable strong zero-shot tool use; later work showed that concise, standardized tool instructions improve usage; 2025–2026 work showed that automatic rewriting of tool descriptions improves robustness on unseen tools and large candidate sets. In practice, many “prompting failures” are actually **bad tool descriptions, overlapping tools, weak schemas, or parser/template mismatches**. citeturn35search0turn17search1turn17search0turn34view0turn29view0turn9view3

## What the evidence now says

The academic arc is consistent. Foundational work such as **Toolformer** argued that models can learn to decide when and how to use tools; **API-Bank** established that tool-augmented LLMs need evaluation on planning, retrieval, and API calling; and the 2025 **Berkeley Function Calling Leaderboard** paper reframed function calling as a core capability for agentic systems rather than a niche API feature. By 2025–2026, evaluation moved further toward realistic multi-step settings: BFCL V4 added web search, memory, and format-sensitivity tracks, while **MCP-Bench** and **MCPVerse** pushed toward large, real-world tool ecosystems where tool retrieval, planning, and cross-tool coordination matter as much as single-call syntax. citeturn14search2turn15search2turn15search1turn31view0turn33view1turn33view2turn33view3turn16search3

The most important practical finding from this literature is that **format and interface choices matter, but not all formatting choices matter equally**. BFCL V4’s format-sensitivity analysis found that models generally do better when the tool documents are presented in **JSON** rather than XML or ad hoc Python-like formats, and that forcing extra tool-call tags can slightly hurt performance and sometimes crater smaller models. At the same time, the same BFCL analysis found no universal advantage for Markdown versus plain text prompt wrappers. In other words: **use formatting to delimit sections, but do not confuse wrapper style with the real lever, which is the structured, semantically precise tool contract itself**. citeturn33view0

Official vendor guidance broadly agrees. OpenAI’s recent prompting guidance says GPT-5.5 tends to work better with **shorter, outcome-first prompts**, not legacy prompt stacks that over-specify the process. Anthropic’s context-engineering guidance says system prompts should be very clear and at the “right altitude,” avoiding both brittle if-else logic and vague high-level fluff. Gemini’s prompt guide similarly emphasizes precise structure, clear delimiters, defined parameters, and careful handling of long context. The convergence is striking: **say what success looks like, define the rules, and stop trying to hand-write the model’s entire internal reasoning path**. citeturn27view3turn9view2turn20view0

The literature on tool documentation pushes the same direction. The original “tool documentation enables zero-shot tool usage” result showed that documentation can outperform or match demonstrations in many settings. Later papers such as **EASYTOOL** and **PLAY2PROMPT** moved from “documentation matters” to “documentation should be rewritten into an agent-optimized interface,” and recent work such as **Trace-Free+** argues that improving descriptions and schemas is a scalable lever even for unseen tools and larger tool sets. The practical lesson is simple: **if you only have time to improve one thing, improve the tool definitions before you start inventing elaborate agent prompts**. citeturn35search0turn17search1turn17search0turn34view0

## Canonical prompt architecture

For a modern tool-calling agent, the best default is a **layered prompt**. Put persistent behavior in the highest-authority channel available to your stack, usually a system or developer message. Put dynamic task context in the user turn. Put tool schemas in the provider’s native tool-definition mechanism, not inline in prose if you can avoid it. When you must inline tool instructions, use a structured representation with explicit sections and clear delimiters; Anthropic explicitly recommends XML tags or Markdown headers for sectioning, and Gemini recommends consistent delimiters such as XML-style tags or headings. citeturn28view0turn9view0turn12view0turn20view0

A strong agent prompt should usually contain these conceptual blocks, in this order for ordinary tasks:

**Role and mission.** One short paragraph. Define what the agent is for, what kinds of tasks it owns, and what “good work” looks like. OpenAI’s reasoning guidance and Anthropic’s docs both support short role-setting over large personality novels. citeturn28view0turn12view4turn7view3

**Operating policy.** State how proactive the agent should be, when it should ask clarifying questions, and what it should do when missing information is minor versus material. Anthropic explicitly exposes this as a design choice: you can prompt for “default to action” or for conservative non-action. OpenAI’s recent personality/collaboration guidance likewise separates “how the assistant sounds” from “how it works.” citeturn13view2turn13view3turn27view3

**Tool policy.** Tell the model when tools are appropriate, when they are not, whether to parallelize independent calls, whether to retry on parameter errors, and whether it must avoid guessing missing required arguments. OpenAI’s function-calling guide explicitly recommends telling the model not to promise future tool calls and to validate arguments rather than guess; Anthropic’s latest prompt guide explicitly supports prompting for parallel tool calls and warns that “if in doubt, use the tool” language can cause over-triggering on newer models. citeturn28view4turn13view0turn13view1

**Verification and stopping condition.** Define what counts as “done.” OpenAI recommends explicit done criteria and verification behavior for research-heavy or agentic tasks, and Anthropic’s agent guidance repeatedly emphasizes evaluation-driven iteration over vague notions of thoroughness. If you want source-backed answers, say that. If you want the agent to double-check tool results before acting, say that. citeturn7view3turn8view4turn29view1

**Output contract.** Specify the final shape of the answer. Ask for the format you need: concise answer, JSON object, patch diff, action summary, citations, etc. Modern models are highly steerable on output contract, and both OpenAI and Gemini explicitly recommend clear output requirements. citeturn7view3turn20view0

For **long-context tasks**, however, reorder the material. Anthropic and Gemini both recommend placing the large context first and the actual question or instruction at the end. The best synthesis is: keep the stable operating policy in the system/developer prompt, but when the user turn contains a lot of retrieved material or documents, place the documents first and the concrete ask last. citeturn12view0turn20view0

The wording should be **specific but not procedural to the point of brittleness**. OpenAI’s reasoning models guidance says to provide the task, constraints, and desired output rather than prescribing every intermediate step. Anthropic says general instructions often beat hand-written step-by-step plans for strong reasoning models. This leads to a useful rule: **use prose instructions for goals and policies; use examples only for brittle interface behavior** such as odd schemas, escaping rules, or specific edge-case selection criteria. citeturn7view3turn12view1turn28view2

## Tool definitions are part of the prompt

The best-performing tool definitions do four jobs at once: they describe **what the tool does**, **when it should be chosen**, **how the arguments should be built**, and **what should happen when the preconditions are not met**. OpenAI’s function-calling guide says function descriptions should clarify both invocation criteria and argument construction. Anthropic’s tool-writing guide says to write descriptions the way you would explain the tool to a new hire, making implicit conventions explicit and insisting on unambiguous parameter names. Qwen’s docs likewise emphasize JSON Schema descriptions and “as much available information as possible” in tool and message specifications. citeturn28view1turn29view0turn24view2

Good tool names are concrete and non-overlapping. Anthropic explicitly warns that bloated or ambiguous tool sets create impossible decision points for agents, and its tool-design guidance recommends tools with clear, distinct purposes. The best tool inventory is usually the **smallest viable tool inventory** that maps naturally to the user’s tasks. If two tools feel interchangeable to a human engineer, they are probably too overlapping for the model as well. Large real-world benchmarks such as MCPVerse and MCP-Bench underline why this matters: when tool sets get large, retrieval and disambiguation, not raw syntax, become the major bottleneck. citeturn9view1turn9view3turn16search3turn33view3

Schemas should be strict, typed, and local. Use enums for closed sets, explicit required fields, and precise field descriptions. OpenAI and DeepSeek both recommend or support strict schema validation; Anthropic offers strict tool use as well. This does not solve every mistake, but it cuts off a large class of argument hallucinations and malformed calls. citeturn8view0turn23view3turn28view4

Examples belong in tool descriptions **only when they teach something the schema does not capture**. OpenAI’s cookbook notes that few-shot prompting can help tool calling especially when the model struggles to construct correct arguments; Gemini says few-shot examples are often powerful for formatting and scoping; Anthropic recommends 3–5 diverse, canonical examples and stresses consistent wrapping. The synthesis is to use examples selectively for: regex escaping, ID formats, date formats, authentication conventions, disambiguating near-neighbor tools, and mapping under-specified user language to normalized parameters. Avoid loading every edge case into the global agent prompt. Put local behavioral examples next to the tool they explain. citeturn28view2turn20view3turn12view2

One subtle but important early-2026 lesson is that **richer tool descriptions often improve success but can increase cost and path length**. The recent Trace-Free+ line of work shows robust gains from better descriptions on unseen tools and under larger candidate sets, while another 2026 study on augmented MCP tool descriptions reports statistically significant accuracy improvements together with more execution steps. So the SOTA answer is not “make descriptions infinitely detailed.” It is “make descriptions complete enough to disambiguate and construct arguments, then stop.” citeturn34view0turn33view4

## Orchestration across turns

The single biggest orchestration mistake is failing to preserve the provider’s hidden or semi-hidden continuity state. OpenAI recommends replaying reasoning items between tool-calling turns in the Responses API. Gemini 3 requires thought signatures to be passed back during function calling and uses tool-context circulation for mixed built-in and custom tools. DeepSeek and Kimi both require `reasoning_content` to be preserved across multi-step tool-calling turns in thinking mode. If you drop these state artifacts, the model may degrade badly or hard-fail. This is not a minor implementation detail; it is part of the prompting contract. citeturn5view4turn19view3turn19view4turn23view4turn23view0

Parallelism should be explicit. Anthropic’s current guidance shows that modern models are good at parallel tool execution and can be prompted to maximize it when the calls are independent. That is the right default for search, file reads, and other fan-out retrieval tasks. But the same prompt should also explicitly prohibit guessing missing parameters and prohibit parallelization when later calls depend on earlier results. The general rule is: **parallelize discovery, serialize dependency chains**. citeturn13view0

For research and retrieval agents, the best prompts define a **search-and-verify loop**, not just a “use web search” instruction. BFCL V4’s web-search benchmark exists precisely because multihop questions require query decomposition, evidence synthesis, and iterative refinement. Anthropic’s context-engineering post recommends “just-in-time” context rather than preloading everything, while its tooling guidance recommends token-efficient search behavior instead of single giant retrievals. The best prompting pattern is therefore: decompose the question, perform multiple targeted searches or lookups, gather enough evidence, then synthesize. citeturn33view1turn9view1turn29view0

Memory should be designed as a first-class tool policy, not a side effect of long chats. Anthropic’s context-engineering guidance highlights compaction and structured note-taking as the main mechanisms for maintaining long-horizon performance, and BFCL V4’s memory track evaluates whether agents can retrieve specific prior facts through memory APIs rather than relying on an ever-growing context window. The correct prompting pattern is to tell the agent **what persistent state matters**, **when to summarize**, and **when to consult external memory versus recent context**. Clearing or compacting stale tool outputs is often safer than replaying them forever. citeturn9view1turn33view2

A final orchestration rule: do not force the model to narrate a giant up-front plan unless your harness specifically needs it. OpenAI’s Codex prompting guide recommends removing prompts that force long plans or status updates during autonomous rollouts because they can cause premature stopping. Anthropic similarly notes that newer Claude models provide better user-facing progress updates without heavy scaffolding. For strong agent models, short commentary plus faithful tool-state replay is usually better than verbose planning theater. citeturn27view2turn13view2

## Vendor-specific notes

For **OpenAI**, the highest-confidence pattern is to use the Responses API for reasoning-heavy and tool-heavy agents, keep the prompt outcome-first, preserve reasoning items with `previous_response_id` or by replaying response items, and use strict schemas wherever possible. For long-running tool-heavy flows, OpenAI also recommends using assistant `phase` values such as `commentary` and `final_answer` to reduce early-stopping pathologies. Its cookbook guidance further notes that reasoning effort affects how willingly the model calls tools, so “under-tooling” can sometimes be fixed more cleanly by changing reasoning effort than by bloating the prompt. citeturn26search5turn27view1turn27view3turn28view4

For **Anthropic**, the key ideas are sectioned prompts, canonical examples, explicit but non-aggressive tool guidance, and strong tool descriptions. Claude’s docs recommend XML-tag or header-based organization, advocate 3–5 well-chosen examples, show how to steer toward either proactive action or conservative non-action, and support explicit parallel-tool instructions. Anthropic’s engineering posts go further by reframing the real problem as context engineering and by emphasizing that tool descriptions and response shapes often matter more than agent-loop cleverness. citeturn12view0turn12view2turn13view2turn13view0turn9view2turn29view0

For **Google DeepMind**’s Gemini stack, the two most important rules are structural. First, Gemini’s prompt guidance strongly favors clear delimiters, explicit parameter definitions, and carefully placed critical instructions. Second, Gemini 3’s function-calling stack depends on thought signatures and, when combining built-in and custom tools, on returning all tool-context parts unchanged across turns. If you treat Gemini like a plain stateless chat model while doing tool calling, you leave a lot of performance on the table or trigger validation failures. citeturn20view0turn19view3turn19view4

For **Qwen**, the official guidance is unusually explicit that function calling is heavily template-driven. Qwen recommends Hermes-style tool use for Qwen3, notes that its chat templates already support this in common serving stacks, and warns that stopword-based ReAct-style templates are a poor fit for reasoning models because stop tokens can collide with the model’s thought stream. In other words, with Qwen, the prompt alone is often not the issue; the **chat template, parser, and serving configuration** can dominate outcomes. citeturn24view0turn24view3turn23view7

For **DeepSeek**, the important distinction is between regular tool calling and tool calling in thinking mode. Thinking mode supports multi-turn reasoning with tool calls, but the provider explicitly requires `reasoning_content` to be replayed on subsequent turns that involve tool calls. DeepSeek also provides a `strict` mode in beta for schema adherence, and its V3.1 release notes explicitly frame stronger tool use and multi-step agent tasks as part of the model update. citeturn23view3turn23view4turn25view0

For **Kimi** and **Moonshot AI**, the current docs similarly push developers toward modern tool calling rather than the deprecated `functions` parameter, and the thinking models require `reasoning_content` continuity during multi-step tool use. The Kimi K2.6 docs also constrain `tool_choice` to `auto` or `none` in the relevant thinking configuration to avoid conflicts, and its built-in web-search flow has a special execution contract where the model-generated search arguments are returned back to the model rather than executed directly by the caller. citeturn23view1turn23view0turn23view2

Finally, community evidence from entity["company","GitHub","software hosting platform"] issues, Hugging Face discussions, and entity["company","Reddit","social discussion platform"] threads is most useful as a deployment warning: for open-weight models, **parser and template mismatches can make tool calling look “bad” even when the model itself is capable**. Reports around Qwen and DeepSeek in local stacks repeatedly point to broken or incompatible tool parsers, reasoning-format adapters, or chat templates as the failure point. Treat these sources as practitioner diagnostics rather than primary science, but do not ignore them. citeturn30search2turn30search9turn30search21turn30search23turn30search3

## Reference templates

The most reliable vendor-neutral base prompt I would use today is this. It is a synthesis of the official guidance and benchmark evidence above, not a verbatim vendor prompt. citeturn27view3turn9view2turn20view0turn28view1

```text
# Role
You are a tool-using agent for [domain].
Your goal is to complete the user's task correctly, efficiently, and safely.

# Scope
You can:
- answer directly when no tool is needed
- use available tools when they materially improve correctness or are required to act
- ask a brief clarification question only if missing information would materially change the result or create meaningful risk

# Tool policy
- Use a tool when the answer depends on external, current, private, or computed information.
- Do not use tools for general knowledge or policy explanations unless a tool is clearly needed.
- Do not guess missing required arguments.
- If a required argument is missing and cannot be inferred safely, ask for it.
- If multiple independent tool calls are needed, make them in parallel.
- If a tool call depends on the output of another, do them sequentially.
- If a tool fails because of an obvious parameter issue, correct it and retry once.
- Do not promise to call a tool later. Either call it now or explain why you cannot.

# Verification
Before finishing:
- check that the task is actually complete
- check that all required constraints were followed
- if the answer depends on tool results, ground the final answer in those results
- if uncertainty remains, say exactly what is uncertain

# Output contract
Return:
1. a brief direct answer or result
2. any necessary supporting details
3. any remaining uncertainty or blocking issue, if applicable

# Style
Be concise, concrete, and task-focused.
Prefer making progress over unnecessary discussion.
```

The tool-description template should usually look like this. Notice that the most important content is not marketing language; it is **selection criteria, argument construction, preconditions, and failure behavior**. citeturn28view1turn29view0turn35search0turn17search1

```text
name: get_order_status

description:
Retrieve status information for a single customer order.

Use when:
- the user asks about the current status, location, ETA, delay, or delivery outcome of a specific order
- another tool requires an order_id as input

Do not use when:
- the user asks about return policy, refund policy, or product catalog information
- the request is about multiple orders at once; use list_customer_orders first

Arguments:
- order_id: string. Use the canonical order identifier, not a human description.
- include_tracking: boolean. Set true only when the user asks for shipment movement or ETA.

Preconditions:
- customer must already be authenticated
- if order_id is missing, first obtain it from the authenticated session or from list_customer_orders

Failure guidance:
- if authentication is missing, do not guess an order_id; ask the user to sign in
- if multiple candidate order_ids exist, ask a short disambiguation question

Examples:
- "Where is my package?" -> if one recent order exists, call with that order_id and include_tracking=true
- "Has order A123 shipped?" -> call with order_id="A123", include_tracking=false
```

For research agents, add a dedicated retrieval block. This is where most “deep research” prompts go wrong: they ask for thoroughness but never define what a good research loop is. citeturn33view1turn29view0turn7view3

```text
# Research policy
- Break the problem into subquestions before searching.
- Prefer multiple focused retrieval actions over one broad search.
- Use current sources for time-sensitive claims.
- Cross-check important factual claims when possible.
- Distinguish sourced findings from your own inference.
- Stop only when the answer is complete enough to satisfy the task's success criteria.
```

For coding agents, add a harness block that reduces common autonomy failures. This is consistent with OpenAI’s Codex guidance and Anthropic’s anti-overengineering guidance. citeturn27view2turn12view3

```text
# Coding policy
- Default to making the requested change, not just suggesting it.
- Avoid over-engineering and avoid unrelated refactors.
- Use standard project tools first.
- Do not create helper scripts or temporary files unless they are clearly necessary.
- If you create temporary artifacts, remove them before finishing.
- Validate the final result with the cheapest reliable check available.
```

## Open questions and limitations

The high-confidence consensus is now strong on **prompt structure, tool-interface quality, and state replay**, but there is still no single universal best prompt for every model family or every serving stack. Benchmarks such as BFCL V4, MCP-Bench, and MCPVerse show that performance depends heavily on the size and realism of the tool environment, and community reports show that open-model outcomes remain unusually sensitive to chat templates, parser implementations, and reasoning-format adapters. That means “best prompt” is still partly a property of the **model-plus-harness**, not just the text string you send. citeturn31view0turn33view0turn33view3turn16search3turn30search2turn30search9

The newest frontier question is not whether better tool descriptions help; it is **how much description is optimal before latency, context pressure, or execution-step count outweigh the gains**. Recent 2026 work suggests the trade-off is real and domain-dependent. So the SOTA practical recommendation is to start with concise-but-complete tool contracts, evaluate, and only then add examples or richer disambiguation where the traces show persistent failures. citeturn34view0turn33view4