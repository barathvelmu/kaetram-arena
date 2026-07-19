# Literature positioning — verified July 18, 2026

## Candidate contribution boundary

The paper is not novel because it uses games, MCP, structured actions, a persistent world, intermediate-state OPD, or on-policy distillation separately. TCOD already uses teacher-success prefixes to initialize intermediate states for multi-turn OPD; Guided-OPD changes live state occupancy through decaying teacher turns; ReOPD explicitly frames multi-turn OPD as reliability-aware prefix-distribution design; and SCoRe trains from verified prefixes immediately before student errors. The candidate contribution is contingent on the planned matched experiments:

1. **Reachability-targeted external-state initialization for on-policy distillation:** directly restore complete, verified persistent world states that need not lie on a successful teacher path; select them using a frozen student-visitation, teacher-advantage, recoverability, and task-relevance rule; train on student rollouts from those states; and compare unseeded evaluation against natural OPD, random-valid and progress-matched resets, TCOD-B2F, and Guided-OPD.
2. **A teacher-forcing copy-prior failure in an agent:** malformed student tool syntax in the context reverses the teacher's local preference even though the teacher does not generate the defect from a clean history.
3. **A measured interface-versus-weights decomposition:** weights transfer one quest-wall competence, while a generation-time recovery affordance resolves a separate syntax defect.

Do not use “to our knowledge” novelty language until the matched comparisons succeed and the literature search is rerun immediately before submission.

## Closest on-policy distillation work

- **GKD / On-Policy Distillation of Language Models** establishes student-generated rollouts with teacher feedback and was published at ICLR 2024. It preempts broad OPD novelty. [ICLR paper](https://proceedings.iclr.cc/paper_files/paper/2024/hash/5be69a584901a26c521c2b51e40a4c20-Abstract-Conference.html)
- **On-Policy Context Distillation** studies context-conditioned teachers, including system-prompt distillation and text-based games. [arXiv:2602.12275](https://arxiv.org/abs/2602.12275)
- **Privileged Information Distillation** uses privileged context to teach multi-turn/action behavior without ordinary cross-vocabulary SFT. [arXiv:2602.04942](https://arxiv.org/abs/2602.04942)
- **TCOD** introduces a temporal curriculum for multi-turn autonomous-agent OPD and reports long-horizon KL instability. [arXiv:2604.24005](https://arxiv.org/abs/2604.24005)
- **Guided-OPD** mixes teacher- and student-generated turns within each rollout and anneals teacher intervention to zero, improving over vanilla OPD and TCOD in ALFWorld, ScienceWorld, and WebShop. It is the strongest current test of whether teacher anchoring already solves the occupancy problem. [arXiv:2606.15912](https://arxiv.org/abs/2606.15912)
- **ReOPD** replays offline teacher prefixes and samples student decision points with a reliability-aware schedule. It preempts generic claims about designing a better prefix or state distribution. [arXiv:2607.04763](https://arxiv.org/abs/2607.04763)
- **SCoRe** identifies a student's earliest error and starts short-horizon reinforcement learning from the verified pre-error prefix. It preempts broad student-failure-localization claims. [arXiv:2509.14257](https://arxiv.org/abs/2509.14257)
- **On-policy Expert Corrections** switches from a student prefix to an expert suffix and is a strong partially on-policy imitation baseline. [arXiv:2512.14895](https://arxiv.org/abs/2512.14895)
- **Step-wise OPD for Small Language Model Agents** uses divergence-weighted supervision after cascading tool errors. [arXiv:2605.07725](https://arxiv.org/abs/2605.07725)
- **Post-Training Is About States, Not Tokens** formalizes the state-distribution view that directly motivates the visitation argument. [arXiv:2605.22731](https://arxiv.org/abs/2605.22731)
- **KAT** identifies low-KL agreement on degraded prefixes and terminates weak supervision. The present copy-prior result is sharper only if it demonstrates wrong-signed endorsement rather than merely absent signal. [arXiv:2606.09471](https://arxiv.org/abs/2606.09471)
- **Rethinking On-Policy Distillation** argues that the teacher must expose genuinely new capabilities and analyzes compatibility between teacher and student behavior. [arXiv:2604.13016](https://arxiv.org/abs/2604.13016)
- **Self-Distillation Enables Continual Learning**, an ICML 2026 Spotlight, evaluates sequential skill accumulation and retention. It is a reason not to label the current same-task rounds “continual learning.” [OpenReview](https://openreview.net/forum?id=HlWA3V6iKF&noteId=DVexpZo7mv)

## Closest game-agent work

- **Orak**, accepted at ICLR 2026, uses MCP to connect LLMs to twelve games, evaluates agentic modules, and provides a gameplay fine-tuning dataset. It invalidates a “first MCP game-agent training” claim. [arXiv:2506.03610](https://arxiv.org/abs/2506.03610)
- **BALROG**, ICLR 2025, evaluates long-horizon language and vision-language agents across games and highlights knowing-versus-doing gaps. [ICLR paper](https://proceedings.iclr.cc/paper_files/paper/2025/hash/f0b1515be276f6ba82b4f2b25e50bef0-Abstract-Conference.html)
- **AgentTrek**, an ICLR 2025 Spotlight, synthesizes trajectories through guided replay from tutorials. It is a close comparison for procedural plans and trajectory collection. [OpenReview](https://openreview.net/forum?id=EEgYUccwsV)
- **lmgame-Bench**, ICLR 2026, studies modular perception, memory, and reasoning harnesses and reports transfer from single-game RL to unseen games and external planning tasks. [arXiv:2505.15146](https://arxiv.org/abs/2505.15146)
- **CRADLE** operates through screenshots and keyboard/mouse control across commercial games and applications, contrasting with Kaetram's symbolic state and typed actions. [OpenReview](https://openreview.net/forum?id=aIAFDFpNXz)
- **Voyager** establishes persistent open-world Minecraft agents with an automatic curriculum and skill library. It preempts generic open-world, persistent, or autonomous-skill-learning claims. [arXiv:2305.16291](https://arxiv.org/abs/2305.16291)
- **GITM** uses structured actions and LLM-generated plans in open-world Minecraft. [arXiv:2305.17144](https://arxiv.org/abs/2305.17144)

## Positioning sentence

> Prior work changes rollout horizons, replays successful prefixes, mixes teacher and student turns, or corrects errors on encountered student trajectories. We isolate a narrower control point: direct, prefix-independent restoration of verified persistent world states selected because the student rarely reaches them and the teacher has a measured conditional advantage there. All headline evaluation begins from the original unseeded world.

## Claims to avoid

- “First MCP game agent” or “first typed game-agent distillation.”
- “First state-aware OPD,” “first intermediate-state OPD,” or “first failure-state curriculum.”
- “Unlike prior work, we initialize from intermediate states.”
- “Embodied agent”; the interface is symbolic and does not test perception or low-level control.
- “Continual learning”; there is no sequential-task retention protocol.
- “Autonomous skill learning”; the prompt includes procedural quest knowledge and there is no learned skill library.
- “World-model acquisition”; the world-model code is deprecated and unevaluated.
- “Capacity is not the lever”; the model-size sweep is underpowered and duration-mismatched.
