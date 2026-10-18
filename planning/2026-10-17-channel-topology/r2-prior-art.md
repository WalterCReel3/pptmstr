# R2: prior art on multi-party and hierarchical coordination

**Dated:** 2026-10-17 · **Task:** R2-prior-art · **Question:** open question 5 (topology and
addressability) of [`../2026-10-17-two-leads-in-one-window-share-a-channel.md`](../2026-10-17-two-leads-in-one-window-share-a-channel.md).
Decided items 1–4 there are taken as fixed. This file surveys; it does not recommend.

## How to read this

**Tags.** Every claim carries one:
- **fetched**: from a page fetched with WebFetch in this session. The URL is given.
- **run**: I extracted the text myself (`pdftotext` on a PDF that WebFetch saved) and read it.
- **read**: from a file in this tree.
- **recalled**: from memory. Carries no weight and is listed separately at the end.
- **mine**: my inference from the sources, not a claim any source makes.

**Quote fidelity.** WebFetch returns a small model's extraction of the page, capped at about
125 characters per quote. I did not compare those quotes character by character against the
sources. One check showed the risk: the MAST paper's +9.4% ChatDev figure came back attributed to
two different interventions on two fetches of the same page. Only a later fetch of the appendix
separated them (see S1). The two holonic papers (S12, S13) are the exception: their quotes come
from my own `pdftotext` output.

**Revised (R6, pdftotext).** S1 (MAST, arXiv 2503.13657v3), S2 (2512.08296) and S4 (MacNet,
2406.07155v3) have now been checked against `pdftotext` output of the PDFs the operator downloaded
(`cmd-20261017T215124Z-f6123d05941f`). The extraction was run in both `-layout` and default modes
and searched by phrase. Each quote checked carries its outcome in an R6 block under its source:
**matches** (now tagged run), **differs** (verbatim text given), or **not found**. Text written
before R6 is left as it was, so its "fetched" tags are R2's and R5's, not R6's.

**Evidence kinds**, strongest first:
- **E**: empirical, controlled comparison (benchmark or annotated traces);
- **C**: case report or production write-up with some measurement;
- **D**: design documentation or reference architecture, without measurement;
- **S**: secondary or encyclopedic summary;
- **O**: practitioner opinion.

**The transfer gap applies to every LLM source below (mine).** Each one studies agents inside a
single task run, on benchmarks, mostly with 1–10 agents and without human gates. pptmstr's case is
different: long-lived leads, each owning a board and builders, linked by an operator, gated per rung
(Decided 1–2), with a human answering decisions (Decided 3). An effect measured on a debate
benchmark is evidence about message topology among LLMs. It is not evidence about lead-to-lead
channels until someone checks that it carries over. No source I found studies coordination between
*teams* of LLM agents.

## Options

Options from the specification:
- **O1, group channels:** N leads on one channel, with receipts and "answered" tracked per
  recipient.
- **O2, pairs only:** N>2 goes through coordinator leads. Links are symmetric, hierarchy is a
  convention, and forwarding is by reference with a provenance chain.
- **O3, pairs with an enforced direction:** parent and child. The charter flows down, and
  cross-links are refused.
- **O4, pairs plus fan-out:** one send goes to several pair channels.

Options I added, as the specification asked:
- **O5, shared state instead of messages:** a blackboard or tuple space for a group of leads. The
  specification named it as a possibility; S9, S10 and S11 are its sources.
- **O6, contract-net allocation:** a lead announces a task to several peers, they bid, and it awards
  the task to one. This is an allocation protocol rather than a topology, but it is the classic
  answer to "N parties, who does this?" (S16).
- **Axis A, authority separate from routing (mine, from S14 and S15):** this is a dimension rather
  than an option. Erlang keeps supervision strictly hierarchical while any process can message any
  other. O3 couples the two; O1, O2 and O4 leave authority where Decided 1–2 already put it, on each
  board's own rung.

## Sources

### LLM multi-agent systems: empirical

**S1. MAST, "Why Do Multi-Agent LLM Systems Fail?" (Cemri et al.), arXiv:2503.13657. Kind E.**
Fetched from <https://arxiv.org/abs/2503.13657> and <https://arxiv.org/html/2503.13657>.
- Method: "1600+ annotated traces collected across 7 popular MAS frameworks"; "14 unique modes,
  clustered into 3 categories: (i) system design issues, (ii) inter-agent misalignment, and (iii)
  task verification"; kappa 0.88 on 150 expert-annotated traces.
- Inter-agent failure rates (fetched):
  - FM-2.4 "Information withholding": 0.85%;
  - FM-2.5 "Ignored other agent's input": 1.90%;
  - FM-2.1 "Conversation reset": 2.20%;
  - FM-2.2 "Fail to ask for clarification": 6.80%;
  - FM-2.3 "Task derailment": 7.40%.

  For comparison:
  - FM-1.3 "Step repetition": 15.7%;
  - FM-1.5 "Unaware of termination conditions": 12.4%;
  - FM-3.3 "Incorrect verification": 9.10%.
- Topology is named once among the failure attributions: "AppWorld frequently suffers from
  premature terminations (FM-3.1), potentially due to its star topology" "and lack of a predefined
  workflow making termination conditions less obvious". The word is "potentially", so this is not a
  measured effect.
- Interventions (Appendix H.2, fetched at offset 100000):
  - "refining role-specific prompts to enforce hierarchy and role adherence": ProgramDev
    25.0% → 34.4%;
  - "We modified the framework's topology from a directed acyclic graph (DAG) to a cyclic graph":
    ProgramDev 25.0% → 40.6%.

  The paper also says "topology-based changes are more effective than prompt-based changes for both
  systems" and that neither fix was substantial.
- Bearing (mine):
  - The inter-agent failures most specific to a channel (withholding, ignoring input) are rare next
    to termination and verification failures. Topology is a minor named cause.
  - ~~In ChatDev, a strictly one-way flow did worse than one with back-edges. That cuts against O3's
    enforced direction.~~

    **Revised (S1 finding 8; R5, fetched <https://arxiv.org/html/2503.13657> at offset 100000).**
    The cyclic change is a supervised review loop: "The process now terminates only when the CTO
    agent confirms that all reviews are properly satisfied, with a maximum iteration cutoff to
    prevent infinite loops." The prompt change also adds authority: "we ensured that only superior
    agents can finalize conversations." Table: ProgramDev-v0 25.0 / 34.4 / 40.6, HumanEval 89.6 /
    90.3 / 91.5 (baseline / prompt / topology). Both interventions add a supervisor's
    validation before termination. That is evidence for validated termination, and it is not
    evidence against directed links: the back-edge runs to a superior. Bearing on O3: none.
  - "Star topology" (a hub) is the paper's suggested cause of premature termination, which tells
    weakly against hubs (O2, O3).
- **Revised (R6, pdftotext; `mast.pdf`, arXiv:2503.13657v3, 26 Oct 2025).** Outcomes:
  - App. H.2, "The process now terminates only when the CTO agent confirms that all reviews are
    properly satisfied, with a maximum iteration cutoff to prevent infinite loops.": **matches**, run.
  - App. H.2, "we ensured that only superior agents can finalize conversations.": **matches**, run.
    The sentence before it gives the reason: "we observed cases where the CPO prematurely ended
    discussions with the CEO without fully addressing constraints."
  - App. H.2, "refining role-specific prompts to enforce hierarchy and role adherence" and "We
    modified the framework's topology from a directed acyclic graph (DAG) to a cyclic graph":
    **match**, run.
  - Table 5 (ChatDev): ProgramDev-v0 25.0 / 34.4 / 40.6; HumanEval 89.6 / 90.3 / 91.5
    (Baseline / Improved prompt / New topology): **matches**, run.
  - The first Interventions bullet's benchmark name **differs**. It says "ProgramDev". The PDF calls
    the set "ProgramDev-v0", "a custom generated set of 32 different tasks ... which consists of
    slightly different questions than the ProgamDev dataset we discussed in Section 4". R5's revision
    above already says ProgramDev-v0.
  - "topology-based changes are more effective than prompt-based changes for both systems"
    (App. H.3): **matches**, run. "neither fix was substantial" is a paraphrase of "they do not
    constitute substantial improvements" (App. H.2).
  - AppWorld: "AppWorld frequently suffers from premature terminations (FM-3.1), potentially due to
    its star topology and lack of a predefined workflow making termination conditions less obvious":
    **matches**, run.
  - Method quotes ("1600+ annotated traces collected across 7 popular MAS frameworks"; "14 unique
    modes, clustered into 3 categories: (i) system design issues, (ii) inter-agent misalignment,
    and (iii) task verification"; κ = 0.88 on "150 traces"): **match**, run.
  - Failure-mode rates: FM-2.5 1.90%, FM-2.1 2.20%, FM-2.2 6.80%, FM-2.3 7.40%, FM-1.3 15.7%,
    FM-1.5 12.4% and FM-3.3 9.10% all **match** the §4 body text, run. FM-2.4 0.85% **matches the
    body text** ("withholding crucial information (FM-2.4, 0.85%)"), but **Figure 1 prints
    "(0.80%)"** for the same mode. The paper is inconsistent with itself here. Neither value
    moves anything.
  - **Not quoted before, and bearing on the Revised bearing above (run):** the paper's own §4
    describes the topology intervention's gain as a verification step: "adding a high-level task
    objective verification step to ChatDev yields a +15.6% improvement in task success on
    ProgramDev (details in Appendix H)". 40.6 − 25.0 = 15.6, the New topology row. So the paper
    itself calls the DAG→cyclic change a verification step. That supports R5's reading (finding 8),
    and it explains the earlier fetch confusion over which intervention a figure belonged to.

**S2. "Towards a Science of Scaling Agent Systems" (Google Research and MIT), arXiv:2512.08296.
Kind E.** Fetched from <https://arxiv.org/abs/2512.08296> and <https://arxiv.org/html/2512.08296>
(the HTML version was read only to character 100000).
- The four architectures:
  - Independent: "agent-to-aggregator only, no peer communication";
  - Centralized: "orchestrator-to-agents only";
  - Decentralized: "all-to-all topology";
  - Hybrid: "orchestrator plus limited peer-to-peer".
- Errors: "Independent systems amplify trace-level errors 17.2×"; "Centralized coordination, however,
  contains this to 4.4×". Table 5: Decentralized 7.8, Hybrid 5.1, single agent 1.0. The authors
  qualify this themselves: error amplification's main effect is not significant once the other
  coordination metrics are controlled for.
- Overhead: "ranging from 0% (SAS) to 515% (Hybrid)". Independent 58%, Decentralized 263%,
  Centralized 285%. Hybrid also took the most turns: 44.3, against 7.2 for a single agent.
- Fit to task: performance relative to a single agent runs from +80.8% (decomposable financial
  reasoning) to −70.0% (sequential planning). Decentralized did better on dynamic web navigation;
  "architecture-task alignment determines collaborative success".
- Limits: agent count in the regression is 1–4. "per-agent reasoning capacity becomes prohibitively
  thin beyond 3–4 agents". The authors do not attempt "exhaustive coverage of all possible
  architectures".
- **Revised (S1 finding 3; R5, fetched, both halves of the HTML).** What the hub does is checking
  and correcting outputs before it combines them: "contains this to 4.4× by enforcing validation
  bottlenecks that intercept errors before aggregation"; "sub-agent outputs pass through an
  orchestrator that cross-checks reasoning steps before aggregation", "enabling detection and
  correction of logical inconsistencies"; "through supervised aggregation". Setup (App. E.2): "3
  sub-agents with 1 orchestrator across maximum 5 orchestration rounds". No human reviewer
  appears in either half. Error amplification's main effect after controls: β = 0.014, p = 0.658
  (Table 4). O2 as specified forwards *by reference* and neither cross-checks nor aggregates, so
  the mechanism does not transfer to O2's coordinator, and the first bullet below is withdrawn.
- Bearing (mine):
  - ~~A hub contains errors best (for O2 and O3).~~ A hub that *validates and aggregates* contains
    errors best. pptmstr's O2 coordinator does neither, so this is for no option as specified.
  - All-to-all costs less overhead than the hub but amplifies errors more (mixed for O1).
  - Hub plus peer links is the most expensive configuration (against O2 once cross-links accumulate).
  - Parallel workers with no peer visibility amplify errors most (against O4, where a fan-out's
    recipients don't see each other).
  - No architecture wins on every task.
- **Revised (R6, pdftotext; `scaling.pdf`, arXiv:2512.08296; the whole PDF, not only to char
  100000).** Outcomes:
  - "Independent systems amplify trace-level errors 17.2× through unchecked error propagation" and
    "Centralized coordination, however, contains this to 4.4× by enforcing validation bottlenecks
    that intercept errors before aggregation" (§1): **match**, run.
  - Table 5 "Error Amp (A_e^trace)": SAS 1.0, Independent 17.2, Decentralized 7.8, Centralized 4.4,
    Hybrid 5.1: **matches**, run. §4.3 gives the CIs: 17.2× "(95% CI: [14.3, 20.1]; no correction
    mechanisms)", 4.4× "([3.8, 5.0]) through supervised aggregation". "through supervised
    aggregation" **matches**, run.
  - "sub-agent outputs pass through an orchestrator that cross-checks reasoning steps before
    aggregation, enabling detection and correction of logical inconsistencies": **matches**, run.
    The next sentence was not quoted before: "In Decentralized architectures, peer debate rounds
    provide similar verification through explicit challenge-response exchanges. These architectures
    achieve 22.7% average error reduction". **So the paper credits verification to peer debate as
    well as to the hub.** Its 22.7% figure covers Centralized, Hybrid and Decentralized together.
  - β = 0.014, p = 0.658: **matches**, run. Table 4 row "log(1 + A_e^trace) 0.014 [−0.047, 0.074]
    0.658†"; §4 prose: "neither the main effect of error amplification (β̂ = 0.014, p = 0.658) nor
    its interaction with tool count ... reaches statistical significance".
  - App. E.2, "3 sub-agents with 1 orchestrator across maximum 5 orchestration rounds": **matches**,
    run. It goes on: "with 3 iterations per agent per round".
  - The four architecture glosses ("agent-to-aggregator only, no peer communication";
    "orchestrator-to-agents only"; "all-to-all topology"; "orchestrator plus limited peer-to-peer"):
    **match**, run.
  - Overhead "ranging from 0% (SAS) to 515% (Hybrid)", with 58 / 263 / 285: **matches**, run. The
    PDF reads "yielding empirical values ranging from 0% (SAS) to 515% (Hybrid)". Turns 44.3 and
    7.2 (Table 5): **match**, run.
  - +80.8% and −70.0%; "architecture-task alignment determines collaborative success";
    "per-agent reasoning capacity becomes prohibitively thin beyond 3–4 agents"; "exhaustive
    coverage of all possible architectures"; n_a range "1–4" (§4 transforms): **match**, run.
  - Abstract (quoted by R4): "architectures without centralized verification tend to propagate
    errors more than those with centralized coordination": **matches**, run.
  - **What the orchestrator does (read, from the PDF).** §3 defines it as Ω, which "determines:
    (i) how sub-agent outputs are aggregated (e.g., majority voting, weighted synthesis), (ii)
    whether the orchestrator can override sub-agent decisions, (iii) whether memory persists across
    coordination rounds, and (iv) termination conditions based on consensus or quality thresholds."
    "Centralized introduces hierarchical verification and bottleneck control." The Independent
    aggregator is the control: it "concatenates sub-agent outputs without cross-validation or
    majority voting". No human reviewer appears anywhere in the PDF's setup. The PDF prints no
    orchestrator prompt, so the evidence goes no further than this definition and the absorption
    paragraph. **Every part of it is about content: the orchestrator aggregates, may override, and
    decides when to stop.** R5's conclusion stands: a coordinator that forwards by reference is not
    this mechanism.

**S3. "Improving Multi-Agent Debate with Sparse Communication Topology" (Li et al.),
arXiv:2406.11776. Kind E.** Fetched from <https://arxiv.org/abs/2406.11776>. The abstract calls
all-to-all a brute-force approach and reports that sparse topologies are "comparable or superior" to
full connectivity at substantially lower compute. The tests were on GPT and Mistral models.
Bearing (mine): against O1's every-member-sees-every-message as a default. It supports sparse
structures (O2, O3) only for debate tasks.

**S4. MacNet, "Scaling Large Language Model-based Multi-Agent Collaboration" (Qian et al.),
arXiv:2406.07155. Kind E.** Fetched from <https://arxiv.org/abs/2406.07155> and
<https://arxiv.org/html/2406.07155v3>.
- Six topologies were tested: chain, star, tree, mesh, layer and random.
- Mean quality:
  - random 0.6522;
  - mesh 0.6316;
  - star 0.6267;
  - chain 0.6078;
  - layer 0.5629, the lowest.
- "the densely connected mesh topology outperforms the moderately dense tree topology, which in turn
  outperforms the sparsely connected chain topology". But: "Overly dense interactions can overwhelm
  agents with information overload". "no single topology consistently excels across all tasks".
- Context: "unrestrained information exchange among agents inevitably leads to context explosion".
  The fix is to propagate only "the refined artifact, rather than the entire dialogue". AgentVerse
  "frequently encounters context explosion issues when scaling beyond thirty agents".
- **Revised (S1 finding 7; R5, fetched <https://arxiv.org/html/2406.07155v3>, means run).**
  Table 1, per benchmark (MMLU, HumanEval, SRDD, CommonGen-Hard) and Quality. † marks a
  significant difference from the chain setting (p ≤ 0.05), as the caption gives it:

  | topology | MMLU | HumanEval | SRDD | CommonGen | Quality |
  |---|---|---|---|---|---|
  | Random | 0.6877 | 0.5244† | 0.8054 | 0.5912 | 0.6522† |
  | Mesh | 0.6825 | 0.5122† | 0.7792† | 0.5525† | 0.6316† |
  | Star | 0.4456† | 0.5549† | 0.7679† | 0.7382† | 0.6267 |
  | Chain | 0.6632 | 0.3720 | 0.8056 | 0.5903 | 0.6078 |
  | Tree | 0.3421† | 0.4878† | 0.8044 | 0.7718† | 0.6015 |
  | Layer | 0.2780† | 0.4939† | 0.7623† | 0.7176† | 0.5629† |

  Recomputing the unweighted mean of the four columns reproduces every Quality cell to 4 places
  (run), so the extraction is at least internally consistent. The list above left out Tree.
  - Tree (0.6015) is below Chain (0.6078) on the mean, and neither Star nor Tree differs
    significantly from Chain on Quality. Star and Mesh are 0.005 apart.
  - The prose "mesh … outperforms the moderately dense tree topology" does not contradict the
    table as S1 says. The paper groups "wider" star-shaped and "deeper" tree-shaped variants
    under *tree topologies* (fetched). The two together average 0.6141, above Chain.
  - "Layer" is named only as "MLP-shaped layered topologies", "balanced in both width and depth".
    The fetch found no connection rule. "Strictly levelled" was my reading, not the paper's.
- Bearing (mine):
  - ~~For denser connection, which favours O1 over trees. This holds only because messages were cut
    down to artifacts.~~ Revised: a single hub (Star) is about level with dense (Mesh); the deep
    tree and Layer are lowest. This separates *one hub* from *multi-level trees*, not dense from
    hub. It still holds only with artifact-only propagation.
  - ~~The layered (strictly levelled) topology scored lowest, which tells against O3.~~ Revised:
    Layer's wiring is not stated, so it maps to no option. Tree's low score tells against *deep*
    O2/O3 trees, not against a one-level star.
  - Passing by reference rather than by transcript is the same move as O2's forward-by-reference and
    as O5.
- **Revised (R6, pdftotext; `macnet.pdf`, arXiv:2406.07155v3, ICLR 2025).** Outcomes:
  - **Table 1, every cell R5 recomputed: matches, run.** All 24 benchmark cells and 6 Quality cells
    for MacNet-Chain/Star/Tree/Mesh/Layer/Random are as in R5's table above. The † marks are where
    R5 put them, checked against both extraction modes. In the default extraction each † sits on
    its own value. Chain carries none; it is the reference.
  - **Caption: matches, run.** "A dagger (†) denotes statistically significant differences (p ≤
    0.05) between a method and our chain-structured setting." Also: "Quality represents the average
    performance over all tasks."
  - **R5's "reproduces every Quality cell to 4 places" differs, slightly (run, `bc`).** The exact
    means are Random .652175, Mesh .6316, Star .62665, Chain .607775, Tree .601525 and Layer
    .56295. Random, Mesh, Chain and Tree round to their cells. Star (.62665) and Layer (.56295)
    are exact half-ties, and the table breaks them in opposite directions: Star up to .6267, Layer
    down to .5629. No single rounding rule gives both, so the Quality column was presumably computed
    from unrounded per-benchmark scores. Every Quality cell agrees with the mean of the printed cells
    to within 0.00005. That is consistency within rounding, not exact reproduction. No ranking
    changes.
  - **The grouping: matches, run.** §2.1: "we focus on three prevalent types—chain, tree, and
    graph—further divided into six representative sub-topologies ... Tree topologies enable agents
    to branch out, interacting in independent directions ...; further categorized into "wider"
    star-shaped and "deeper" tree-shaped topologies. Graph topologies ... further classified into
    fully-connected mesh topologies, MLP-shaped layered topologies, and irregular random
    topologies." The density prose names the families: "higher interaction density ... correlates
    with improved average performance across the three primary topological types. Specifically,
    the densely connected mesh topology outperforms the moderately dense tree topology, which in
    turn outperforms the sparsely connected chain topology" (**matches**, run). In the "Shape"
    paragraph: "with the same density, star-shaped topologies that are "wider" tend to perform
    better than "deeper" tree-shaped ones". So R5's refutation of S1's sub-point stands. The prose
    is about families, and star with tree averages 0.6141, above Chain's 0.6078.
  - "no single topology consistently excels across all tasks"; "unrestrained information exchange
    among agents inevitably leads to context explosion"; "only the refined artifact, rather than the
    entire dialogue, is propagated": **match**, run. "Overly dense interactions can overwhelm agents
    with information overload" **matches apart from case**: the PDF reads "overly dense interactions
    can overwhelm ..." mid-sentence.
  - "MLP-shaped layered topologies" **matches**, run. "balanced in both width and depth" **matches**
    (Implementation Details: "layered structures balanced in both width and depth"). No connection
    rule beyond these is stated. R5's "Layer maps to no option" stands.
  - AgentVerse "frequently encounters context explosion issues when scaling beyond thirty agents"
    **matches, but the quote dropped its subject clause**. Verbatim: "AGENTVERSE implicitly reduces
    to a star topology and frequently encounters context explosion issues when scaling beyond thirty
    agents". The matrix lists this under O1 "against", but the paper ties it to a *star*-reducing
    system that passes context rather than artifacts. See the R6 matrix note.
  - **Reading points not in R2 or R5 (read, from the PDF).**
    - Every MacNet topology is a DAG, run "in a topological order". Only the artifact propagates:
      "This prevents global broadcasting". Each edge carries a "supervisory critic" and each node a
      "compliant actor". "Mesh" is therefore a fully connected DAG with a reviewer on every edge. It
      is not a channel where everyone reads everything, which weakens its mapping to O1 further.
    - The default topology is small: "By default, we employ a topology consisting of approximately
      four nodes", built from "binary trees". The PDF does not tie Table 1 to that default
      explicitly. If Table 1 uses it, "Tree" there is a binary tree two levels deep, set against a
      one-level star. "Deep" in R5 and above means two levels, not many. The paper's account of the
      gap is mechanical: memory control "may cause deeper topologies to lose track of distant
      agents, occasionally resulting in artifact version rollbacks".

**S5. MultiAgentBench / MARBLE (arXiv:2503.01935). Kind E.** Fetched from
<https://arxiv.org/html/2503.01935v1> (read only to character 100000).
- It evaluates "star, chain, tree, and graph topologies". In the research scenario, graph did best.
  Star was comparable. "the tree-based protocol performs poorly, with high token consumption and
  the lowest task and coordination scores".
- The figures are in a plot that the fetch could not read.
- Separately, "increasing the number of agents leads to a decrease in the overall KPI".
- Bearing (mine): a tree was the worst structure in this benchmark, which tells against O2 and O3
  when they are built as trees. A graph was best, which favours O1 or a mesh of pairs. Group size
  hurt, which tells against large O1 groups.
- **Revised (S1 finding 7; R5, fetched <https://arxiv.org/html/2503.01935v1>, to char 100000).**
  §4.3: "both the star and graph protocols yield similar task scores". §3.1.1: the tree is "a
  top-level planner delegates tasks to subordinate planners, which in turn coordinate with
  actors"; the star is "a single central planner assigns tasks to all actors and consolidates
  their feedback". MARBLE agrees with MacNet: one hub ≈ graph, multi-level tree worst. "A graph
  was best, which favours O1" does not separate O1 from a one-level hub. Note the star here
  *consolidates* (aggregates), which O2's forward-by-reference coordinator does not.

**S6. "On the Resilience of LLM-Based Multi-Agent Collaboration with Faulty Agents" (Huang et al.),
arXiv:2408.00989 (v4 title). Kind E.** Fetched from <https://arxiv.org/abs/2408.00989>. The
hierarchical structure "exhibits superior resilience with the lowest performance drop of 5.5%",
"compared to 10.5% and 23.7% of other two structures". The other two are chain-style and
bidirectional. An added reviewer agent, "Inspector … to review and correct messages", recovers "up
to 96.4% errors made by faulty agents". Bearing (mine): a hierarchy tolerates a faulty member best,
which supports O2 and O3. An inspecting party on the path is very effective. pptmstr's operator
gate on reviewed rungs plays that role whatever the topology.

**S7. "An Empirical Study of Group Conformity in Multi-Agent Systems" (Choi et al.),
arXiv:2506.01332. Kind E.** Fetched from <https://arxiv.org/abs/2506.01332>. "LLM agents tend to
align with numerically dominant groups or more intelligent agents"; "significant group conformity
mirroring human behavior". Group size is not varied as a separate factor. Bearing (mine): against O1.
In a shared channel, a minority lead's position is pulled toward the majority's. Pairs remove the
numerical majority, though a coordinator that aggregates could bring it back.

**S8. Who&When, "Which Agent Causes Task Failures and When?" (Zhang et al.), arXiv:2505.00212.
Kind E.** Fetched from <https://arxiv.org/abs/2505.00212>. This is "extensive failure logs from 127
LLM multi-agent systems". The best method gets "53.5% accuracy in identifying failure-responsible
agents" and "only 14.2% in pinpointing failure steps". Bearing (mine): blame is hard to assign after
the fact in any topology. That supports O2's provenance chain, and the store-stamped sender and
order that Decided items already give every option. It does not separate one option from another.

**S9. "Exploring Advanced LLM Multi-Agent Systems Based on Blackboard Architecture" (Han & Zhang),
arXiv:2507.01701. Kind E (benchmark), single paper.** Fetched from <https://arxiv.org/abs/2507.01701>.
Agents "share all the information and others' messages during the whole problem-solving process".
Acting agents are "selected based on the current content of the blackboard", until "a consensus is
reached on the blackboard". The system is "competitive with the SOTA static and dynamic MASs" and
spends "less tokens". The abstract gives no figures. Bearing (mine): for O5, but weak. There is one
paper, no head-to-head comparison with message passing, and it uses a selection mechanism that
pptmstr's leads would not have.

### LLM orchestration: design docs and production write-ups

**S10. Anthropic, "How we built our multi-agent research system". Kind C** (production, some
measurement). Fetched from <https://www.anthropic.com/engineering/multi-agent-research-system>.
- Structure: "an orchestrator-worker pattern". The workers do not talk to each other.
- Costs:
  - "our lead agents execute subagents synchronously"; "the lead agent can't steer subagents,
    subagents can't coordinate, and the entire system can be blocked while waiting for a single
    subagent";
  - "multi-agent systems use about 15× more tokens than chats".
- Failures:
  - "spawning 50 subagents for simple queries";
  - "Without detailed task descriptions, agents duplicate work, leave gaps".
- The relay problem is named: a section headed "Subagent output to a filesystem to minimize the
  'game of telephone.'". Its remedy: "Subagents call tools to store their work in external systems,
  then pass lightweight references back to the coordinator".
- Fit to task: domains "that require all agents to share the same context" "are not a good fit".
  "LLM agents are not yet great at coordinating and delegating to other agents in real time".
  Parallelism "cut research time by up to 90% for complex queries".
- Bearing (mine):
  - For a hub (O2) in production.
  - Against a hub: it blocks, and relaying through it loses information.
  - The remedy is reference-passing (O2's forward-by-reference) or shared artifacts (O5).
  - Fan-out to parallel workers helped them (O4), with duplication as the reported cost.

**S11. Anthropic, "Building effective agents". Kind D/O.** Fetched from
<https://www.anthropic.com/engineering/building-effective-agents>. Orchestrator-workers: "a central
LLM dynamically breaks down tasks, delegates them to worker LLMs, and synthesizes their results".
Advice: "the simplest solution possible, and only increasing complexity when needed"; agents bring
"the potential for compounding errors". Bearing (mine): neutral on topology. It argues for adding
structure only when a measurement says to.

**S17. Cognition, "Don't Build Multi-Agents". Kind O.** Fetched from
<https://cognition.com/blog/dont-build-multi-agents>. "Share context, and share full agent traces,
not just individual messages"; "Actions carry implicit decisions, and conflicting decisions carry bad
results". Parallel subagents produce work "based on conflicting assumptions not prescribed upfront".
Agents "are not quite able to engage in this style of long-context proactive discourse". Bearing
(mine):
- Against O4: parallel recipients of one fan-out decide independently.
- Against O2's summarising relay: messages stand in for traces. Forwarding by reference softens this.
- Against O1 for the same reason: no topology shares traces.

**S18. AutoGen SelectorGroupChat and the group-chat pattern. Kind D.** Fetched from
<https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/selector-group-chat.html>
and
<https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/design-patterns/group-chat.html>.
- Structure: "participants take turns broadcasting messages to all other members"; "only one agent is
  working at a time"; "a Group Chat Manager agent … selects the next agent to speak".
- Precautions in the docs:
  - a message cap "to avoid infinite loop";
  - rule-based selection where it is "more reliable";
  - an example agent that used wrong inputs because it lacked context.
- Bearing (mine): the main framework that implements O1 does not let N agents run free on one
  thread. It adds a turn-taking manager and a termination cap. An O1 for pptmstr would have to decide
  who plays that manager. Leads woken on delivery (proposal item 2) would otherwise all wake on
  every message.

**S19. LangChain multi-agent docs. Kind D.** Fetched from
<https://docs.langchain.com/oss/python/langchain/multi-agent>. Subagents: "All routing passes
through the main agent". It "adds one extra call because results flow back through the main
agent—this overhead provides centralized control". Handoffs "executes sequentially". "At the center
of multi-agent design is context engineering." Two older LangGraph URLs for hierarchical teams
returned redirects or 404s, so I have no fetched statement of why LangGraph nests supervisors.
Bearing (mine): this states the cost of a hub (an extra hop) and what it buys (control). It does not
weigh one against the other.

**S20. OpenAI Agents SDK, handoffs. Kind D.** Fetched from
<https://openai.github.io/openai-agents-python/handoffs/>. "Handoffs allow an agent to delegate tasks
to another agent". By default the new agent "gets to see the entire previous conversation history".
"Input guardrails still apply only to the first agent in the chain". Bearing (mine): this is a pair
transfer. The guardrail line shows a property that a chain of relays can lose, and it bears on O2.
In pptmstr, Decided 1 (each board's own rung governs) already blocks that loss, because work enters
each board through its own gate.

**S21. A2A protocol specification. Kind D.** Fetched from
<https://a2a-protocol.org/latest/specification/> (read only to character 100000). A message is "A
communication turn between a client and a remote agent". Agents can "dynamically find and understand
the capabilities of other agents" through an Agent Card. They work "without needing access to each
other's internal state, memory, or tools". In the part read, I found no group or broadcast
messaging. Bearing (mine): the cross-vendor agent-to-agent standard chose pairwise client-to-server
links plus discovery. That is evidence of a design choice and not of an outcome. Its discovery is the
opposite of pptmstr's "discovery is the link" (proposal item 1).

### Holonic and manufacturing control

**S12. Cardin, Derigent, Trentesaux, "Evolution of holonic control architectures towards Industry
4.0: A short overview", arXiv:1810.09109. Kind S** (review). The PDF was fetched from
<https://arxiv.org/pdf/1810.09109>; the text was extracted with pdftotext (run).
- PROSA (Van Brussel et al. 1998) has product, resource and order holons, plus "Staff holons …
  acting as a global advisor". Staff are advisers, not commanders.
- PROSIS is an "isoarchy … containing no subordination hierarchical links between holons".
- ADACOR keeps holons "organized in a hierarchical structure" in the stationary state. When a
  disturbance hits, it reorganises: "the possibility to change dynamically the holarchy between the
  stationary state and the transient one".
- Costs named:
  - "local decisional entities are subject to, at least, social and temporal myopia … This can lead
    to sub-optimal solutions or even deadlocks. However, myopia is a needed characteristic of
    holonic architectures, ensuring a fast reaction";
  - "adjust the level of autonomy of the local decisional entities against the level of authority of
    the global decisional entities. Such balance is never easy to achieve".
- Bearing (mine):
  - The field's most-cited architecture uses advisory (staff) roles rather than command, which is
    closer to O2's convention than to O3's enforcement.
  - Later architectures moved toward switching between hierarchy and heterarchy. A fixed enforced
    hierarchy is what the field moved away from, which tells against O3.
  - Myopia is a cost of local autonomy, and a pairwise lead sees only its own links.

**S13. Derigent, Cardin, Trentesaux, "Industry 4.0: Contributions of Holonic Manufacturing Control
Architectures and Future Challenges", arXiv:2002.04525. Kind S.** The PDF was fetched; the text was
extracted with pdftotext (run). Dynamic architectures "suffer from two main drawback related to their
possible nervousness (switching too many times, too fast) and their possible temporal myopia … The
cost of switching is thus not really investigated." Bearing (mine): this counters S12's drift away
from fixed hierarchy. Reconfigurable structures have their own uncosted failure modes.

**I found no fetched source that measures a holarchy's bottleneck or latency.** The specification
named those as candidate costs. They remain unsupported here, in either direction.

### Supervision, shared state, allocation

**S14. Erlang/OTP supervision. Kind D.** Fetched from
<https://www.erlang.org/doc/system/design_principles.html> and
<https://www.erlang.org/doc/system/sup_princ.html>.
- "Supervisors are processes that monitor workers. A supervisor can restart a worker if something
  goes wrong."
- If more than MaxR restarts happen in MaxT seconds, "the supervisor terminates all the child
  processes and then itself", and "the next higher-level supervisor takes some action".
- Bearing (mine): a tree is used for *failure escalation*, and it has decades of production use. I
  recall this but did not fetch it (see Recalled).

**S15. Erlang concurrent programming. Kind D.** Fetched from
<https://www.erlang.org/doc/system/conc_prog.html>. "if you know the pid of a process, the `!`
operator can be used to send it a message", "disregarding if the process is on the same node or on a
different node". Names can be registered "so that these names can be used as identities instead of
pids". Bearing (mine): this is Axis A. The system best known for hierarchy enforces it on
*supervision* and not on *messaging*. O3 enforces both.

**S16. Contract Net Protocol (Wikipedia). Kind S.** Fetched from
<https://en.wikipedia.org/wiki/Contract_Net_Protocol>. It is "a task-sharing protocol in
multi-agent systems": call-for-proposals, proposals, award, then inform or cancel. Its listed
problems:
- message load: Smith proposes "to create only short messages";
- "the manager cannot precise what it values most";
- "a contractor that makes a proposal commits to accomplish the task … whatever it takes";
- self-interested contractors over-bid.

Bearing (mine): this is O6. A broadcast is limited to allocation and ends in a pair. The evidence is
an encyclopedia summary.

**Blackboard and tuple space (Wikipedia). Kind S.** Fetched from
<https://en.wikipedia.org/wiki/Blackboard_system> and <https://en.wikipedia.org/wiki/Tuple_space>.
- A blackboard is "a common knowledge base … iteratively updated by a diverse group of specialist
  knowledge sources". A control shell "controls the flow of problem-solving activity".
- A tuple space provides "a repository of tuples that can be accessed concurrently". Consumers
  "retrieve data from the space that match a certain pattern".
- Neither page lists drawbacks.

Bearing (mine): this is O5. Like AutoGen's group chat (S18), a blackboard needs a controller.

### Human organisations

**Brooks.** Kind S. Fetched from <https://en.wikipedia.org/wiki/The_Mythical_Man-Month> and
<https://en.wikipedia.org/wiki/Brooks%27s_law>.
- The "Group intercommunication formula: n(n − 1)/2". 50 developers have 1,225 channels.
- The "surgical team" is "led by one surgeon performing the most critical work".
- Criticism: "Good modularity helps by minimizing the communication overhead". McConnell: "I no
  longer think it's true".

Bearing (mine):
- n(n−1)/2 counts the *pairs* in a full mesh. A full-mesh O2 has that many channels, and so does one
  O1 group implicitly.
- A tree has n−1 links.
- The criticism says modularity, not topology, is the lever.

**Span of control (Wikipedia). Kind S.** Fetched from <https://en.wikipedia.org/wiki/Span_of_control>.
"if the span of control is too large, the supervisor may not have the capacity to supervise
effectively"; "information overload and span of control are positively correlated". Bearing (mine):
this bounds a coordinator lead's fan-in under O2 and O3, and it bounds membership under O1. It gives
no number for LLM leads.

**Conway's law (Wikipedia). Kind S, with empirical studies cited.** Fetched from
<https://en.wikipedia.org/wiki/Conway%27s_law>. Organisations are "constrained to produce designs
which are copies of the communication structures of these organizations". MacCormack et al. found
"strong evidence to support the mirroring hypothesis". Bearing (mine): this is neutral between the
options, but it warns that whatever topology is chosen will shape how operators split work across
teams.

**Team Topologies. Kind D/O.** Fetched from <https://teamtopologies.com/key-concepts> and
<https://teamtopologies.com/news-blogs-newsletters/2025/2/21/team-topologies-interaction-modes-breaking-through-common-misconceptions>.
- Three interaction modes:
  - Collaboration: "working together for a defined period of time to discover new things";
  - X-as-a-Service: "one team provides and one team consumes something 'as a Service'";
  - Facilitation: "one team helps and mentors another team".
- "Organizations often fall into the trap of permanent collaboration", which "increases cognitive
  load, and ultimately slows down the delivery of value".
- The principle is "Eliminate Team Dependencies".

Bearing (mine):
- Every mode is defined between *two* teams, which favours pairs (O2, O4).
- Many-team collaboration is described as a trap when it is permanent, which tells against a
  standing O1 group. A time-boxed one is not covered by that warning.
- The modes suggest the *charter* could carry an interaction mode. That is Axis A again, not a
  topology.

## Option × evidence

Codes are source numbers above. Kinds: E empirical, C case, D design, S secondary, O opinion. Every
"for" and "against" is my mapping of the source onto the option. None of the sources studied pptmstr
lead channels.

| Option | Evidence for | Evidence against |
|---|---|---|
| **O1 group channel** | S4 (E): denser beats tree beats chain, *if* only artifacts propagate. S5 (E): graph best in the research scenario. S2 (E): decentralized better on dynamic web navigation, and less overhead than centralized (263% vs 285%). S18 (D): a mature framework ships it. Team Topologies (D): a time-boxed collaboration is a legitimate mode. | S3 (E): sparse matches dense at lower cost. S7 (E): conformity to the numerical majority. S2 (E): 7.8× error amplification vs 4.4× for a hub. S4 (E): information overload; context explosion beyond ~30 agents. S5 (E): more agents, lower KPI. S18 (D): needs a turn-taking manager and a loop cap. Team Topologies (D/O): permanent multi-team collaboration is a trap. Brooks (S): n(n−1)/2. |
| **O2 pairs + coordinator, convention** | S2 (E): a hub contains errors best (4.4×). S6 (E): hierarchy most resilient to faulty agents. S10 (C): orchestrator-worker in production. S10 (C) and S4 (E): pass references, not transcripts, which matches forward-by-reference. S8 (E): attribution is hard, so provenance has value. S12 (S): PROSA's staff holons advise, not command. Team Topologies (D): modes are pairwise. S21 (D): A2A is pairwise. | S10 (C): the hub blocks on its slowest member, and the "game of telephone". S1 (E): star topology potentially causes premature termination. S5 (E): tree had the lowest scores. S2 (E): hub overhead 285%; hub plus peer links 515%. S19 (D): an extra hop per exchange. S12 (S): myopia of local entities. Span of control (S): the coordinator overloads. S17 (O): messages are not traces. |
| **O3 enforced parent/child** | Everything for O2's hub. S14 (D): supervision trees escalate failure cleanly. S6 (E): hierarchy resilience. | S1 (E): ChatDev improved going from a one-way DAG to cyclic (+15.6 pts). S4 (E): the layered topology scored lowest. S5 (E): tree worst. S15 (D): Erlang does not enforce hierarchy on messaging. S12 (S): holonic control moved from fixed hierarchy toward reconfigurable holarchies. |
| **O4 pairs + fan-out** | S10 (C): parallel subagents cut time by up to 90%. S2 (E): a centralized fan-out gains +80.8% on decomposable tasks. S16 (S): contract-net's call-for-proposals is a bounded broadcast. | S17 (O): parallel recipients make conflicting implicit decisions. S2 (E): workers that cannot see each other amplify errors 17.2×. S10 (C): duplicated work and gaps. S2 (E): no multi-agent setup beat one agent on sequential tasks (−70%). |
| **O5 shared state (added)** | S9 (E, single paper): competitive and fewer tokens. S10 (C): artifacts outside the coordinator avoid the telephone game. S4 (E): artifact-only propagation keeps context linear. | S9: no head-to-head against message passing, and it relies on an agent selector. Blackboard (S): needs a control shell. Drawbacks are undocumented in the fetched pages, which is absence, not evidence. |
| **O6 contract net (added)** | S16 (S): allocation among N without a standing group. | S16 (S): message load; the manager cannot state priorities; over-bidding. There is no LLM evidence. |

**Revised (R5, S1 findings 3, 7, 8).** The matrix above is kept as written. These cells are
withdrawn or changed:
- O1 "for": S4 and S5 "denser beats tree", "graph best". Revised: dense ≈ one hub; both beat
  *multi-level* trees. This no longer separates O1 from a one-level O2 star.
- O2 "for": S2 (E) "a hub contains errors best (4.4×)" and S6 (E). Withdrawn: both mechanisms
  are a party that checks and corrects content (S2's orchestrator; S6's Inspector "to review and
  correct messages"). O2's coordinator forwards by reference and does neither. They are weighed
  for no option as specified.
- O1 "against": S2 (E) "7.8× vs 4.4× for a hub". Withdrawn for the same reason, and because the
  main effect is not significant after controls (β = 0.014, p = 0.658).
- O2 "against": S5 (E) "tree had the lowest scores". Kept for deep trees only.
- O3 "against": S1 (E) "DAG → cyclic +15.6 pts". Withdrawn: the cycle is a supervised review
  loop. S4 "layered lowest": withdrawn, because Layer's wiring is not stated.
- O3 "against" gains: S4 (E) Tree 0.6015, lowest but one, and S5 (E) tree worst. Both are deep
  hierarchies.

**Revised (R6, pdftotext).** Checked against the PDFs. No R5 withdrawal is reversed, and these
cells change:
- O1 "against": S4 (E) "context explosion beyond ~30 agents". **Moved.** The paper says
  AgentVerse "implicitly reduces to a star topology" when it hits this. It is evidence against a
  hub that passes whole context, not against O1. It is weak for O2 too, because O2 forwards by
  reference.
- O1 "for": S4 (E). Weakened further: MacNet's mesh is a DAG with a critic on each edge and
  artifact-only propagation that "prevents global broadcasting". It is not a shared channel.
- O3 "against" (S4 Tree 0.6015): "deep" means a two-level binary tree in a topology of about four
  nodes, if Table 1 uses the stated default.
- S2 cells: no change. The PDF also credits peer debate with "similar verification", which leaves
  S2 weighed for no option, as R5 has it.

## What the evidence does not settle

- No source compares O2 (a hierarchy by convention) with O3 (an enforced one) directly. Each "for
  O3" above is really "for a hub".
- The strongest single quantitative contrast, error containment against overhead in S2, comes from
  configurations of 1–4 agents inside one task. pptmstr leads are heavier units than those agents.
- S4 and S5 favour dense graphs, while S3 and S7 favour sparse or warn against groups. The tasks
  differ: generation and research against debate and opinion. Which kind of task lead-to-lead work
  resembles is an empirical question about pptmstr. The literature does not answer it.

## Recalled (no weight)

- Smith (1980) introduced the contract net in IEEE Transactions on Computers.
- Joe Armstrong's 2003 thesis grounds OTP's "let it crash".
- Supervision trees have decades of telecom production use.
- Koestler's holons are "Janus-faced" (self-assertive and integrative).
- Graicunas's relationship count grows super-linearly with span.

Not fetched. None of these is used above as evidence.

## Not done

- No full-text reading of S3, S6, S7, S8 or S9: abstracts only.
- S2, S5 and S21 were read only to character 100000. S1 was read in two parts; its middle may hold
  more topology content.
- No figures were read: MARBLE's per-topology numbers are in a plot.
- I found no fetched source that measures a holarchy's bottleneck or latency.
- LangGraph's own rationale for hierarchical teams was not obtained (404 and redirect).
- WebSearch result summaries were used only to find pages. Nothing from them is cited unless the page
  itself was fetched.
- **Revised (R6, pdftotext).** S1, S2 and S4 have now been read in full-text `pdftotext` output, but
  only by phrase search for the quotes above and their surrounding paragraphs. They were not read
  cover to cover, so S1's middle may still hold topology content no one has quoted.
  - S5 (MARBLE) was not downloaded. Its R5 quotes stay fetched.
  - S6 was not downloaded either. It stays relayed from the abstract.
