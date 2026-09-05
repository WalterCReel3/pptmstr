# The instrument is the product

**Dated:** 2026-08-31 · **Status:** argument for a thesis change; nothing here is
decided · **Origin:** a conversation about the psychology of operator–LLM interaction,
serialised for external review

**This is not a planning record.** It settles nothing. It assembles the psychological,
empirical and formal constraints that bear on operator–LLM interaction, derives a set of
recommendations from them, and then argues that pptmstr's stated thesis is aimed at the
weakest layer in the system. A decision belongs in `planning/`.

**Written to be vetted.** Every claim is tagged with how it is known. A reviewer should
be able to attack any line without first reconstructing where it came from.

---

## 0. Verification conventions

| Tag | Meaning |
|---|---|
| **[R]** | Read this session. Quoted verbatim from a file in this repository; path given. Checkable by opening the file. |
| **[C]** | Cited from training data. Author, title and substantive claim are high-confidence; **exact wording is not guaranteed** unless explicitly marked verbatim. Needs a source check. |
| **[U]** | Uncertain. Figures or attributions I believe correct but would not defend without checking. Flagged individually. |
| **[I]** | Inference. Mine, drawn from the tagged material. Not sourced to anyone. |

Nothing in §7 is **[R]** or **[C]**. It is all **[I]** built on them, and it is the part
most worth attacking.

**What I did not read.** `approval.py`, `store.py`, `driver.py`, `model.py`, `bus.py`,
`app.py`, the UI package, the test suite, `STYLE.md` in full, `orchestrator-design.md`
beyond its outline and §2.7, and most of `planning/`. Claims about those files are
second-hand from records that describe them, and are marked. **Several load-bearing
claims in §7 depend on code I have not opened.** They are listed in §10.

---

## 1. Summary

**The recommendations**, in one line each, derived in §5:

1. Premises are discrete referenceable claims, not narrative.
2. Decomposition is the plan; it carries boundaries and criteria, never remedies.
3. Framing has an interior optimum — more rigour in the plan buys execution cost.
4. Gate on consequence, not mechanism.
5. Delete any gate whose rejection has no plausible alternative.
6. Manufacture a gate on plan size, because scope expansion is not an operation.
7. Prefer an absent capability to a gate that denies; decide fail-open/fail-closed on purpose.
8. Partition for cognitive disjointness as well as write disjointness.
9. Two to four held streams; more only behind mechanical gates.
10. Halt on partition invalidation, not on surprise; grade by blast radius.
11. Acceptance criteria are the highest-value artefact a human writes.
12. Postconditions over preconditions for anything the agent supplies.
13. Review to calibrate, not to catch.
14. Capture the wire, not the narration.
15. Prose supplies information; types enforce invariants.

**The thesis**, argued in §7:

> pptmstr's differentiating asset is not the gate. It is that pptmstr is the only
> participant in the system that sees both what was declared and what was done. The
> model sees its own context; the operator sees samples; git sees the result and not the
> intent. Only the orchestrator sits at the join — and it currently discards that
> information at approval. The product should be the durable comparison between
> declaration and execution, because that comparison is the only thing that makes any
> intervention in this domain falsifiable, and because it is the one artefact nothing
> else in the stack can produce.

---

## 2. Four findings that constrain any design

### 2.1 A cognitive artefact changes the task; it does not amplify the person

Norman's account of cognitive artefacts turns on a distinction that most tool design
elides: the **system view** and the **personal view** can diverge. A checklist does not
improve memory — it deletes the remembering and substitutes a reading task. System
performance rises; the person's unaided capability does not, and may fall. **[C]**
Norman, *Cognitive Artifacts*, in Carroll (ed.), *Designing Interaction: Psychology at
the Human-Computer Interface*, 1991.

Hutchins generalises the unit of analysis from the head to the socio-technical system —
representational state propagating across media and people. **[C]** Hutchins, *Cognition
in the Wild*, MIT Press, 1995. Multi-agent orchestration is a literal instance:
division of labour, propagation of representational state, no single participant holding
the whole.

Krakauer's *complementary* versus *competitive* artefact distinction is the sharpest
operator-facing test — complementary artefacts (the abacus) leave you more capable when
removed; competitive ones (GPS) leave you less. **[C, essayistic]** This circulates in
Krakauer's talks and SFI writing rather than as an empirical programme; treat it as a
framing device, not a result, and do not cite it as measured.

**Why it constrains design [I]:** "does this tool help" is two questions with different
answers. A tool can raise throughput while degrading the operator's model of their own
system, and nothing in the tool's telemetry would show it.

### 2.2 Human review is the weakest available verification layer

Four independent mechanisms degrade it, and they compound:

**Low prevalence.** Miss rates in visual search rise sharply when targets are rare —
demonstrated in baggage-screening-shaped tasks. **[C]** Wolfe, Horowitz & Kenner, *Rare
items often missed in visual searches*, Nature 435, 2005. **[I]** Reviewing agent output
is a rare-target search: most output is fine.

**Fluency.** Processing fluency is normally a serviceable proxy for correctness, and
LLM output decouples them — it is maximally fluent independent of whether it is right.
**[C]** Alter & Oppenheimer, *Uniting the Tribes of Fluency to Form a Metacognitive
Nation*, PSPR, 2009, for the fluency-as-cue literature. The decoupling claim is **[I]**.

**Anchoring, and acceptance bias that scales with artefact size.** Generated suggestions
measurably shift users' expressed views, not merely their edits. **[C]** Jakesch, Bhat,
Buschek, Zalmanson & Naaman, *Co-Writing with Opinionated Language Models Affects Users'
Views*, CHI 2023. **[I]** Combined with sunk cost, this predicts that async review is
systematically biased toward acceptance, and that the bias grows with the volume
reviewed — the opposite of the property you want.

**Self-report does not track the outcome.** METR's 2025 randomised trial found
experienced open-source developers working in repositories they knew well were
**~19% slower** with AI assistance while believing they had been sped up. **[U — verify
n, task count, and effect size]** I recall 16 developers and 246 tasks; the direction and
rough magnitude I am confident in, the exact figures I am not. Contrast **[C]** Peng,
Kalliamvakou, Cihon & Demirer, *The Impact of AI on Developer Productivity: Evidence from
GitHub Copilot*, 2023, which found a large speedup (~55%) on a scoped greenfield task.
**[I]** The lesson is not that either is right; it is that **the sign of the effect flips
with task type and self-report does not track it.**

Supporting: **[C]** Skitka, Mosier & Burdick, *Does automation bias decision-making?*,
IJHCS, 1999 (omission and commission errors under automated aids). **[C]** Fisher, Goddu
& Keil, *Searching for Explanations: How the Internet Inflates Estimates of Internal
Knowledge*, JEP:General, 2015 — the artefact's competence is absorbed into the
self-model.

**This repository has its own instance of it [R]**, `notes/2026-08-21-opus-5-and-work-nobody-asked-for.md`:

> Three times during this investigation a web-search summariser returned *real text with
> the model label swapped*. […] The failure mode is not hallucination, and that is why it
> is dangerous. The prose is accurate, the quotation is verbatim, the URL resolves, and
> the version label is wrong. It passes every plausibility check a reader applies by
> reflex. The only thing that catches it is opening the source.

And its generalisation in the same file **[R]**:

> `STYLE.md` §2 already says *a probe must capture the result, not the narration*, because
> a model reporting success is not evidence of success. This is that rule one level up:
> **a worker reporting a citation is not evidence of a citation.** pptmstr spawns research
> roles whose entire output is prose about documents the lead never opens.

### 2.3 Structure binds; prose does not

The repository's own finding **[R]**,
`planning/2026-08-15-a-task-reaches-the-board-without-a-decision.md`:

> The parts of `templates.py` that actually bind are the parts that are not prose.
> `depends_on` prevented two agents editing one file all day, structurally. "Within
> reason" prevented nothing. Prose is the weakest mechanism available and it is the one
> we reach for because it is the cheapest to write.

And the mechanism it proposes for why **[R]**, same file:

> `CLAUDE.md`'s "How to think here" has seven rules. Four push toward more work […] None
> is a proportionality rule. The document is monotonically pro-rigor, so a new paragraph
> asking for restraint is outvoted by construction.

An independent line reaches the same place **[R]**,
`planning/2026-08-21-the-board-takes-an-agents-word-for-what-it-will-touch.md`:

> The external research arrived at the same place from outside and knew nothing of this
> repository. No controlled evidence exists for any prompt-side mitigation of unrequested
> work, and the best-instrumented public result is negative: four stacked mitigations,
> including a hook injecting *"answer only what was asked"*, with the targeted errors
> continuing after each was added.

**[I] Why it holds, mechanically** — four properties, in increasing order of how damaging
they are:

1. **Sentences compete; types exclude.** An instruction is one input among hundreds to a
   probabilistic process. A constraint on the state space does not vote.
2. **Sentences decay with context; types do not.** A guard that weakens over a session
   fails exactly in long sessions, which is where expensive failures live.
3. **Compliance with a sentence is unobservable; violation of a type is not.** You cannot
   distinguish a working prose guard from a decorative one, because there is no
   counterfactual — so nobody deletes one, briefing documents grow monotonically, and
   every addition dilutes what is already there. Prose guards do not merely fail
   individually; **they degrade each other, and the pruning mechanism does not exist.**
4. **Sentences request behaviour; types change what behaviour is available.** A request
   loses to competing pressure. Availability does not.

**A live instance, offered as evidence a reviewer can check.** The conversation this
document was produced in ran with `templates.lead_briefing(RESEARCH)` as its system
prompt — verbatim, through the roster and the coordination section. That briefing spends
roughly sixty lines instructing the lead to put lines of enquiry on the board, start one
worker per independent task, wait, and read its inbox before answering. None of it was
followed, because a single later line — "Do not call the AgentTool unless the user
requested it" — outvoted all of it. **[I]** One terse, late instruction beat sixty lines
of well-argued briefing, which suggests the failure mode is not merely "prose is weak"
but that **prose loses to shorter, later prose** — so briefing length works against
briefing effect.

**This contradicts `templates.py` [R]**, whose module docstring says:

> **The prompts are the feature.** Roles are cheap; a lead that implements the work itself
> instead of delegating, a lead that runs one agent per role while independent tasks sit
> unclaimed, and workers that agree with each other are the ways a team produces less than
> one agent would. All three are prompt problems.

Both documents are in the repository. `templates.py` says the prompts are the feature;
the 08-15 record says prose is the weakest mechanism available. **[I]** They cannot both
be right, and this contradiction is the most useful thing in the repository, because
resolving it decides what the tool is.

### 2.4 The formal boundary: what can and cannot be enforced

**Decidability sets the tier ordering.** "Did this agent write outside its declared set?"
is a property of a finite execution trace — decidable, cheap, checkable after the fact.
"*Will* it write outside its declared set?" is a prediction about the behaviour of an
arbitrary program, and non-trivial semantic properties of programs are undecidable.
**[C]** Rice, *Classes of recursively enumerable sets and their decision problems*, 1953.
**[I]** Therefore a precondition on an agent-supplied claim cannot verify the claim; it
can only record it. Postconditions can actually check. This is a computability boundary,
not an engineering preference.

**Monitors enforce exactly the safety properties.** The safety/liveness decomposition is
**[C]** Lamport, *Proving the Correctness of Multiprocess Programs*, 1977, formalised by
**[C]** Alpern & Schneider, *Defining Liveness*, IPL, 1985; the enforcement result is
**[C]** Schneider, *Enforceable Security Policies*, ACM TISSEC, 2000. **[I]** "Two agents
never write the same file" is safety — a gate can hold it. "The agent eventually produces
correct code" is liveness — no gate can. Every guard pptmstr can build is a safety
property.

**Correctness cannot be established below the layer where the requirement lives.** **[C]**
Saltzer, Reed & Clark, *End-to-End Arguments in System Design*, ACM TOCS, 1984. **[I]** No
amount of coordination typing yields correct code, because correctness lives at the
endpoints — the specification and the acceptance test — not in the transport between them.

**The specification problem is inverted here.** In classical formal methods the hard part
was writing the spec, not discharging it. **[I]** Here it is worse: you reach for a model
precisely when you cannot fully specify the task, so the domain where LLMs pay is exactly
the domain where formal specification is unavailable. This is not a tooling gap; it is
structural, and it bounds every claim in this document.

**Consequence [I]: types govern coordination, never content.** Who writes what, in what
order, under what disjointness, with what dependency structure — formalisable, and mostly
already solved elsewhere. Whether the change is *right* — not formalisable, because that
is the thing that was delegated.

**The coordination layer is a known domain.** Agent orchestration is currently
reinventing, badly, work that exists:

| Improvised here | Existing theory |
|---|---|
| Read-only roles; removing `Edit` | Capability security / POLA. **[C]** Dennis & Van Horn, *Programming Semantics for Multiprogrammed Computations*, CACM, 1966; Miller's object-capability model, 2006. |
| The approval gate | Reference monitor: complete mediation, tamper-proof, verifiable. **[C]** Anderson, *Computer Security Technology Planning Study*, 1972. |
| `touches`, write disjointness | Ownership and separation logic; the frame rule is "disjoint footprints compose". **[C]** Reynolds, *Separation Logic*, LICS 2002; O'Hearn. Rust's borrow checker is the checked version. |
| Halt-and-replan on discovery | Optimistic concurrency control; a partition-invalidating finding is a validation-time conflict. **[C]** Kung & Robinson, *On Optimistic Methods for Concurrency Control*, ACM TODS, 1981. |
| Task pre/postconditions | Design by contract. **[C]** Meyer, *Object-Oriented Software Construction*, 1988/1997. |
| A typed board around an untyped model | Gradual typing and blame. **[C]** Findler & Felleisen, *Contracts for Higher-Order Functions*, ICFP 2002; Wadler & Findler, *Well-typed programs can't be blamed*, ESOP 2009. |

**[I]** The last row is the most underexploited. This *is* the gradual typing situation:
a statically-structured system interoperating with a component whose behaviour is
unverified, with contracts at the boundary. That field already established that
guarantees hold only as strongly as the boundary contracts, and that sound boundary
checking can cost more than anyone admitted — **[C]** Takikawa et al., *Is Sound Gradual
Typing Dead?*, POPL 2016. **[I]** Our analogue of that overhead is operator attention, and
the finding transfers: the sound version may be unaffordable, and the engineering is in
choosing which boundaries to leave unchecked deliberately.

---

## 3. The two axes

### 3.1 Serial vs concurrent — what N streams cost the operator

Supervisory control, not multitasking. **[C]** Sheridan & Verplank's levels-of-automation
framing, 1978.

- **Switch cost, worst on unfinished work.** Task-switch costs decompose into
  reconfiguration plus proactive interference. **[C]** Monsell, *Task switching*, TiCS,
  2003. Residue is worse when the prior task was left unresolved — **[C]** Leroy, *Why is
  it so hard to do my work?*, OBHDP, 2009. **[I]** Supervising running agents means every
  switch is away from an unresolved task, by construction.
- **The unit of load is a schema, not an item.** **[C]** Cowan, *The magical number 4 in
  short-term memory*, BBS, 2001; **[C]** Wickens, *Multiple resources and performance
  prediction*, 2002 — N agent transcripts contend for one verbal/symbolic channel, unlike
  N gauges. **[I]** Two to three models held with fidelity.
- **Monitoring degrades on a clock.** **[C]** Mackworth, 1948 — vigilance decrement within
  20–30 minutes, worse at low event rates. **[I]** Agent supervision is the worst
  vigilance profile available: low event rate, rare critical events.
- **Projection goes before perception.** **[C]** Endsley, *Toward a Theory of Situation
  Awareness in Dynamic Systems*, Human Factors, 1995; **[C]** Endsley & Kiris, *The
  out-of-the-loop performance problem*, 1995. **[I]** Under concurrency you keep level 1
  (what each agent is doing) and lose level 3 (where this is heading). Level 3 is what
  makes intervention early, and early is the only time it is cheap. This is why a
  dashboard showing everything coexists with a session that goes sideways for forty
  minutes: perception was never the thing that failed.
- **Coverage and detection move opposite. [I]** Throughput rises ~linearly with N; per
  stream, attention falls as 1/N, residue grows, and error prevalence falls — and
  detection degrades *nonlinearly* with prevalence (§2.2). So there is a crossover past
  which another agent lowers verified output while raising raw output. The gap between
  those two numbers is invisible, and it is where the damage accumulates.

**The irony is old.** **[C]** Bainbridge, *Ironies of Automation*, Automatica, 1983:
automating the tractable parts leaves the operator the ambiguous residue while eroding
the practice that made them good at it, and asks them to take over at the hardest moment
with the least context.

### 3.2 Synchronous vs asynchronous — trust and delegation

**[C]** Lee & See, *Trust in Automation: Designing for Appropriate Reliance*, Human
Factors, 2004, with **[C]** Parasuraman & Riley, *Humans and Automation: Use, Misuse,
Disuse, Abuse*, Human Factors, 1997. Trust rests on three bases — performance, process,
purpose. **[I]** With an LLM you get noisy performance evidence, clean purpose, and
essentially no process: the transcript is an artefact of the reasoning, not the reasoning.
Trust here is built on the weakest available foundation, which makes it slow to calibrate
and brittle.

**[I] The deeper point is not latency. Asynchrony converts a decision into a review, and
those are different cognitive acts.** Deciding is generative — you bring your own model
and set the frame. Reviewing is evaluative and anchored — the artefact sets the terms
(§2.2). The same person, with the same information, judges differently before and after
the work exists, and the shift is toward ratification.

Two consequences **[I]**:

- **Delegation is only real if the framing decision was synchronous.** Without a
  synchronous frame you did not delegate; you abdicated and then ratified. The felt
  experience is identical, which is the problem.
- **Async review degrades as the artefact grows.** Rejecting a large coherent body of work
  has a visible cost; accepting it has an invisible one. The more you delegate without a
  frame, the less able you become to refuse the result.

**The rule:** synchronous about the frame, asynchronous about the execution. Async
execution without synchronous framing is not a speed/rigour trade — it trades rigour for
nothing, because the review meant to compensate is the review anchoring has already
compromised.

**The repository's baseline run is the demonstration [R]**,
`planning/2026-08-15-a-task-reaches-the-board-without-a-decision.md` — 9 tasks, 8 agents,
932,640 tokens, individual durations to 13.6h:

> **The operator was not asked about any of them.** Six of the nine were declared in a
> single burst between roughly 13:50 and 13:55, minutes after a one-line request […] The
> operator's next message, at 13:58, is a bug report — not a response to a plan, because
> no plan was put to them.

> The fan-out itself was correct — genuine parallelism over already-scoped work is what
> the tool is for, and it should happen again — but *correct* and *consented to* are
> different properties, and only one of them was present.

**[I]** Note what this settles: planning on that run *was already serial* — one lead,
thinking alone. Serialising planning bought nothing, because contention between planners
was never the failure. The variable that mattered was synchrony. **Serial-and-asynchronous
is the worst quadrant: it looks careful, produces a coherent artefact, and has no human
in it.**

And why the existing gate did not help **[R]**, same file:

> But **a spawn approval does not say what the agent will do.** […] The operator approves
> *that an agent may start*; the agent then selects its own work from a board the operator
> has never seen. […] **The gate is on the spawn, not on the work.**

---

## 4. Recommendations

### 4.1 Before the work — serial, synchronous, uncontaminated

1. **Premises as discrete referenceable claims, not narrative.** Partition-invalidation
   detection (§4.3) requires enumerable premises; clean supersession requires them too.
2. **Frame premises as findings to confirm or refute, not orders.** **[R]**
   `templates.py`, `worker_prompt` docstring: *"a team that followed its instructions
   faithfully would have shipped worse work than one that argued. Four workers refused or
   corrected a spec they were handed and all four were right."* One operator writing for
   every worker concentrates authority further than any single concern does.
3. **Decompose by decisions hidden, not by files.** **[C]** Parnas, *On the Criteria To Be
   Used in Decomposing Systems into Modules*, CACM, 1972 — a module is characterised by
   the design decision it hides. **[I]** A partition drawn on file boundaries rather than
   decision boundaries produces units that cannot be verified independently, and the
   concurrency saving is then consumed at integration.
4. **Declare per unit: write boundary, dependency edges, acceptance criteria, expected
   size.** The decomposition *is* the plan; everything else is commentary. **[I]** It is
   also the only part of your thinking that converts into something enforceable.
5. **Carry boundaries and criteria; omit remedies.** **[R]** 08-15 record: *"What it must
   not hold is a plan. Work that arrives with a remedy attached reads as already-decided
   and invites approval — and the plan is the part that goes stale."*
6. **Declare expected size.** **[I]** Without a prior commitment, "this turned out bigger"
   has nothing to be bigger than. This single field converts the highest-leverage gate
   from a judgement call into an observation.
7. **Stop before the document becomes rigorous enough to be imitated.** **[R]** 08-15
   record: *"The lead inherits the register of whatever it reads. […] it scales with how
   well the source document is written — so the better the record, the more it costs to
   act on."* **[I]** Framing has an interior optimum, not a monotone return.

### 4.2 Gate design — structural, decided once

8. **Gate on consequence, not mechanism.** **[I]** Scope expansion is not an operation —
   nothing writes when a lead concludes a four-file change is a twelve-file change — so a
   mechanism-based gate is structurally blind to the most expensive event in the system.
9. **Rank by four axes, in this order [I]:** *fan-out* (does this decision determine
   others), *legibility* (can you evaluate it in the seconds you will spend), *meaningful
   alternative* (if you say no, does a different world exist), *irreversibility*.
   Compactly: `value ≈ (consequence if wrong) × (probability you catch it here) ÷ (attention it costs)`.
10. **Delete any gate whose rejection has no plausible alternative.** **[I]** Approving a
    file read is pure attention tax: "don't read it" was never an option. This test
    eliminates most of what is typically gated.
11. **Legibility is the omitted term.** **[I]** A diff is consequential and expensive to
    read, with the defect hidden by fluency at low prevalence. A scope delta is
    consequential and free to read — "3 files declared, 11 written" is one line. Prefer
    gates that are high-consequence *and* cheaply legible; this means gating fewer diffs
    and more plans.
12. **Prefer an absent capability to a gate that denies.** **[R]** `README.md`: *"Review
    roles are given read-only tools on purpose. A reviewer that can quietly fix what it
    was asked to find stops reporting it."* This is already right, and is capability
    security (§2.4) arrived at independently.
13. **A gate's strength is its failure mode, not its rule.** **[I]** Fail-closed is a type;
    fail-open is a sentence with extra steps. **[R]** `README.md` records the right
    default: *"With no operator attached the gate denies rather than hanging."*
14. **Note the conflation already recorded [R]**, `templates.py`: *"The grant conflates two
    capabilities and only one of them was intended: **cannot edit** is the property that
    was chosen, and **cannot execute** came along with it."* A review role cannot make a
    run-derived claim, which is the difference between evidence and inference.

### 4.3 During — concurrent, bounded, desynchronised

15. **Partition for cognitive disjointness as well as write disjointness.** **[I]**
    Different criteria; only the first is usually designed. N agents inside one subsystem
    you hold as a single model cost N switches to maintain 1 model — pure loss.
16. **Two to four held streams.** More only when the extras are fire-and-forget behind
    mechanical gates.
17. **Desynchronise completions; sort the review queue by context, not arrival.** **[I]**
    Five diffs from one agent consecutively cost far less than five from five interleaved.
    Chronological is the natural implementation and the wrong one.
18. **Halt on partition invalidation, not on surprise.** Three mechanical triggers:
    *boundary violation* (work needs a file outside its declared set), *dependency
    inversion* (B needs A, the graph said independent), *premise contradiction* (a stated
    premise is false). All decidable; all mean the plan has stopped describing the work.
19. **Grade the halt by blast radius.** One unit holds; a broken edge pauses that subgraph;
    a falsified premise stops everything downstream of it — which requires knowing which
    units descend from which premises, a second reason for recommendation 1.
20. **The reason it must be structural [I]:** agents do not notice they are stale. A person
    overhears the hallway conversation; an agent in a separate context completes work
    against an hour-old map with full confidence, and staleness leaves no trace in the
    artefact. There is no review process that catches it, because there is nothing to see.

### 4.4 Verification — serial, terminal

21. **Acceptance criteria are the highest-value artefact a human writes.** **[I]**
    Simultaneously the specification (only you can supply it), the endpoint where the
    end-to-end argument says the check must live, and a decidable trace property a machine
    can hold.
22. **Postconditions over preconditions for anything agent-supplied** (§2.4).
23. **Run the terminal check after writes have stopped, enforced structurally.** **[R]**
    `templates.py` already states the hazard — *"a suite read while agents are writing
    reads files mid-save"* — as prose, which by §2.3 is the wrong mechanism for it.
24. **Review to calibrate, not to catch.** **[I]** Calibration needs a small random sample;
    detection needs comprehensive coverage and fails anyway (§2.2). The optimum is far
    below where most operators run, and it is not zero — if you never look, trust is
    uncalibrated by construction.

### 4.5 Instrumentation

25. **Capture the wire, not the narration.** **[R]** `STYLE.md` §2, as quoted in the 08-21
    record: *"A model reporting success is not evidence of success. Capture the tool
    result, the hook input, the wire."*
26. **Compare declared against actual for every self-report you rely on.**
27. **Do not put an experimental change in the file that reaches everything.** **[R]**
    08-21 record: *"a restraint paragraph in `CLAUDE.md` is not one experiment — it changes
    every role in every template simultaneously, with no arm left unchanged to compare
    against. The cheapest-looking place to put the rule is also the only place that
    destroys the ability to find out whether it worked."*

### 4.6 What stays prose

28. **Prose supplies information; types enforce invariants.** Prose telling a model
    something it could not know is prose doing its job, and no type substitutes for it.
    **Prose asking for restraint under competing pressure is the tell that a type is
    missing** — it is the one thing prose is worst at.

### 4.7 The enforcement ladder

Referenced throughout. Strength descending; note that strength trades against revisability.

| | Mechanism | Failure mode |
|---|---|---|
| **0 — Nonexistence** | The action has no representation (no edit tool). | None. Cannot fail open. |
| **1 — Construction** | Illegal states unrepresentable (blocked-ness derived, not a settable flag). | None; violations inexpressible. |
| **2 — Precondition** | Checked before; denial blocks. | Real: timeout, absent operator, fatigue, approve-all. |
| **3 — Postcondition** | Checked after; violation reported. | Does not prevent; converts silent failure to loud. |
| **4 — Sampled observation** | Human review. | Partial by construction. **Not a type.** |
| **5 — Sentence** | Compliance requested. | Competes with everything in context. |

**[I]** Two consequences. First: **most tier-5 guards have a nearly free tier-3 version**,
because a postcondition need not prevent anything — it compares two things you already
have. This is the cheapest upgrade from prose and it is skipped because reporting after
the fact does not feel like a fix; it is one, because it converts an unmeasurable guard
into a measurable one. Second: **type what you understand the shape of; leave what you are
still learning at tier 3.** A wrong tier-0/1 constraint makes a legitimate action
impossible with no override. A wrong tier-3 constraint is a noisy report you delete.

**And the trap [I]: where an agent supplies the value, the type covers the shape and never
the truth.** A validated `tuple[str, ...]` has every appearance of a type — schema,
normalisation, a place in the data model. But the type is real only for what it enforces
mechanically (the edges derived from those strings) and absent for what it asserts (that
these are the files that will be written). One is enforced, one is believed, and the
schema makes them look alike. **The test: is there anything that would notice if the
declaration were false?**

---

## 5. The attention optimisation

Fixed budget of operator attention `A`. The objective is not output but **verified**
output. Categories compete: framing, decomposition, gate decisions, artefact review,
synthesis, machinery.

### 5.1 Marginal returns, unequal in a predictable direction [I]

**Decomposition is the only category where attention capitalises.** Framing produces prose
— tier 5, spent once, decays. Review is consumed entirely. Synthesis is consumed.
Decomposition converts into dependency edges, write partitions and acceptance criteria —
tiers 1–3 — which then enforce at zero attention cost on every subsequent instance. That
multiplier exists nowhere else, and it is the most reliable reallocation available.

**Review has the worst return of any category, and is where most attention goes.** Poor at
detection, biased toward acceptance, scaling badly exactly where concurrency was wanted,
and duplicating what an acceptance test does better. Its one irreplaceable function is
calibration, which needs a small sample.

**Gate attention has near-zero marginal value for badly-chosen gates and high value for
well-chosen ones**, so the leverage is pruning the list — a one-time structural act, not
an ongoing expense.

**Synthesis is irreducible.** You are the only node holding the whole picture, which makes
you both the integration point and the single point of failure. **[C]** Clark & Brennan,
*Grounding in Communication*, 1991 — you systematically overestimate how much common
ground you have established.

**Machinery has the highest long-run return and zero short-run return**, and is the only
category that raises the ceiling rather than allocating beneath it.

**The local maximum [I]:** more decomposition, far less review, a short consequence-ranked
gate list, a standing investment in machinery. Most practice sits well away from it in a
consistent direction.

### 5.2 Why the optimum is stable, and why you cannot feel your way to it [I]

Review is legible as work, to yourself and to observers. Its failure mode is invisible —
you never see what you missed. Decomposition pays later; machinery pays much later; both
look like avoiding the work.

The structural reason is worse than incentives: **the objective function is not observable
to the optimiser.** METR's developers were slower while believing they were faster (§2.2).
The gradient is imperceptible, so iterating on felt experience does not converge — it
wanders toward whatever most resembles effort.

**Therefore the binding constraint on improving operator–LLM interaction is not attention.
It is measurement.** The repository states the local version **[R]**, 08-21 record:

> Every measurement the application computes about a write is either destroyed at approval
> or dies with the session, so there is no quantity here that a scope lever could move, and
> no way to tell a lever that worked from one that did not.

**[I]** That generalises past this tool. Until declared-versus-actual is captured durably,
every intervention in this space is unfalsifiable — and unfalsifiable interventions
accumulate rather than getting pruned, which is the same dynamic that makes briefing
documents grow monotonically and weaken as they grow (§2.3).

So the ordering is **instrument → structure → allocate**, and the first step is the one
that gets skipped because it shows no improvement on the run where you do it.

### 5.3 Where the maximum moves [I]

Task type is the axis that moves it most, and it reconciles the conflicting empirical
results. Deep context in a codebase you know: the model's advantage is smallest, your
verification cost highest — optimum is low concurrency, heavy framing. Scoped, unfamiliar,
mechanically checkable: the model's advantage is large, verification is cheap — optimum is
higher concurrency, lighter framing. METR and Peng et al. sampled opposite ends of one
axis rather than contradicting each other.

**It is a local maximum in the strict sense.** Reallocation reaches it. Exceeding it
requires content correctness to become mechanically checkable, which the specification
problem (§2.4) says will not happen for the work where LLMs are most valuable. The
coordination layer can be driven near-optimal with existing theory; the content layer stays
bounded by the quality of your acceptance criteria.

---

## 6. Where this leaves pptmstr

### 6.1 The current thesis, stated fairly [R]

From `README.md`:

> A multi-agent orchestrator with an immediate-mode UI. It runs N Claude agent sessions
> concurrently, keeps every one of them legible at a glance, and lets nothing reach your
> disk that you did not read first.

> The design premise is that **the operator is the bottleneck on purpose**, so the
> interesting engineering is in making that bottleneck cheap.

And the tagline: *"Run a fleet of Claude agents. Hold every string."*

This is a coherent and well-executed thesis. The idling work, the approval-as-runtime-state
design, the sub-agent tree, the reviewable message bus, and read-only review roles are all
correct implementations of it, and several are independently right for reasons the thesis
does not claim (capability security, §2.4).

### 6.2 Four objections [I]

**Objection 1 — the central promise is built on the weakest layer.** "Nothing reaches your
disk that you did not read first" is tier 4: sampled human observation. §2.2 says that
layer has poor detection under low prevalence, is biased toward acceptance, and degrades
with volume. The product's headline guarantee is the one verification mechanism the
evidence says does not work at scale. It delivers the *feeling* of correctness at high
attention cost — and by §2.1, the system view and the personal view can diverge without
anything in the telemetry showing it.

**Objection 2 — the bottleneck is correctly identified and incorrectly sited.** "Make the
bottleneck cheap" was implemented as making *approval* cheap: keyboard-driven, batched,
idle frame rate, `Shift+A`. But the expensive thing is not the keystroke; it is the
judgement. Cheapening the keystroke while the judgement stays expensive produces
approve-all. The baseline run is the proof — eight spawn approvals were given and
constrained nothing, and the operator's actual interventions were *"DO not start new agents
for documents"* typed into a running session and halting a line of work by editing an
inter-agent message in transit. **[R]** *"Neither is a decision they were offered; both are
interceptions they had to invent."*

**Objection 3 — the tool cannot tell whether it is working.** It measures spend and renders
state. It does not measure decisions offered, scope declared versus scope executed, or
outcomes. Its own record says there is no quantity a scope lever could move. **A tool whose
thesis is responsible orchestration, which cannot measure responsibility, is asserting its
value rather than demonstrating it.** The repository has already noticed the version of
this that applies to its own agents — *a worker reporting a citation is not evidence of a
citation* — and has not applied it to itself.

**Objection 4 — the stated feature contradicts the measured finding.** `templates.py` says
the prompts are the feature. The 08-15 record says prose is the weakest mechanism
available and `depends_on` is what actually bound. Both are in the tree. The `lead_briefing`
function is ~60 lines of generated prose; by the repository's own evidence, most of it does
not bind, and by §2.3 its length actively dilutes the lines that might.

### 6.3 The unique asset [I]

Ask what pptmstr has that nothing else in the stack has.

- The **model** sees its own context and not the tree's history.
- The **operator** sees samples, under the constraints of §2.2.
- **git** sees the result and never the intent — it has no record that this change was
  supposed to be three files.
- The **harness** sees one session at a time.

**pptmstr is the only participant that sees both what was declared and what was done.**
Every declaration passes through `declare_task`. Every mutation passes through the gate,
which computes a real unified diff against the file on disk. Both cross one store which is,
by the repository's own description, *"the single audit point for every mutation"* **[R]**.

And it throws the join away. **[R]** From the 08-21 record's inventory:

| Quantity | Where it exists | Why it cannot be used |
|---|---|---|
| Per-call diff | `PendingApproval.diff`, from `render_diff` | Dropped at `ApprovalResolved` |
| Token spend | `AgentRecord.usage` | Per node, not per task; in-memory |
| Transcript | `AgentRecord.transcript` | In-memory, dies with the session |
| CLI transcript path | captured in `_gate_tool_use` | Read by nothing |
| Declared scope | `Task.touches` | A declaration, never compared to a write |

> The diff is the sharpest loss. It is computed on every `Write`/`Edit`/`MultiEdit`, it is
> correct, and it is discarded a few hundred lines later by the arm that settles the
> approval it belongs to.

**[I]** The tool computes the scarcest quantity in the domain, uses it to render one pane,
and deletes it.

### 6.4 The proposed thesis [I]

> **pptmstr is the instrument that makes agent orchestration falsifiable.**
>
> Its product is the durable comparison between what was declared and what was done —
> because that comparison is the only artefact nothing else in the stack can produce, and
> because without it no intervention in this domain can be distinguished from a placebo.
>
> The gate is not the product. The gate is the sensor.

Restating the operator's role under it: **from reviewer to specifier.** The operator's
attention moves from tier 4 (reading everything) to tiers 1–3 (declaring the partition, the
criteria and the expected size, then reading the divergences). This is §5.1's reallocation
expressed as a product decision rather than a discipline.

### 6.5 What changes [I]

1. **Retain what the gate computes.** Not the diff text — the counts: files touched, lines
   added and removed, per resolved approval, accumulated against the task. This is
   proposal 1 of the 08-21 record and it is a precondition for everything else here. The
   `derive; do not store` objection does not apply: **[R]** *"a transient being discarded
   is not a derivable fact being duplicated. There is no snapshot from which files-touched
   and lines-changed follow after the fact."*
2. **Make declared-versus-actual a first-class object.** `touches` versus writes; declared
   size versus spend; acceptance criteria versus terminal gate result. Each is a tier-3
   postcondition — decidable, cheap, and the only kind of check §2.4 permits on an
   agent-supplied claim.
3. **Move the operator surface from writes to divergences.** Reviewing 932,640 tokens is
   not possible. Reading *"task-4 declared 3 files, wrote 11"* is one line. This is
   recommendation 11 made concrete, and it is the single largest attention saving available.
4. **Promote guards down the ladder.** `spawn_order` is advisory prose that could be
   enforced. "Declare a terminal task that greens the gate" is prose describing a scheduling
   invariant the board could hold. "Do not put two agents on work that touches the same
   file" is already a type via `touches` and is *also* stated in prose, which is
   redundancy in the weak direction.
5. **Shrink the briefing.** Every line converted to a type should be deleted from the prose,
   not left as belt-and-braces — because by §2.3 length dilutes. What remains is prose doing
   its proper job: supplying information (the premises, the register, the disagreement
   mandate).
6. **Make runs comparable.** Same task set, two configurations, a measurable difference. The
   08-15 record already specifies the metric **[R]**: *"not tokens saved, but **how many
   decisions the operator was offered before the spend, and how many interceptions they had
   to invent instead.** On this run those numbers are zero and two."* That is a real
   instrument reading and the tool cannot currently produce it.
7. **Reconsider the plan-unit gate.** Per-declaration sign-off shipped at `c9ab068` and, by
   the record's own admission, fires six times in five minutes on the baseline. **[I]** The
   operator has already invented plan-level sign-off outside the board:
   `planning/2026-08-22-four-items-buy-back-session-time.md` is a signed decomposition —
   four items, each naming its `touches`, each with a done-when list, scope explicitly
   closed. The board lacks the type, so the operator wrote it as a document and then pays
   again per declaration. The missing primitive is a **declared set the operator signs once
   and the board holds every member to.**

### 6.6 What does not change [I]

The threading model, the immutable store, the copy-on-write snapshot, idling, the sub-agent
tree, the gate as authentication layer for the bus, read-only review roles, and the
per-write review path for genuinely irreversible operations. None of that is challenged;
most of it is what makes the proposed thesis implementable at all. **The claim is not that
the gate should go. It is that the gate's output is worth more than its veto.**

### 6.7 Counterarguments [I]

**"Removing per-write review is unsafe."** Not proposed. Irreversible operations — network
calls, messages to people, destruction of untracked files, migrations — are a small
enumerable set and stay gated by category. Reversible writes under version control do not
need per-call human review; they need a comparison at the end. The distinction is
recommendation 9's irreversibility axis, applied rather than assumed.

**"This makes it a measurement tool, not an orchestrator."** The measurement is what makes
the orchestration improvable, and it is the differentiated position: every other tool in
this space is optimising the demo. **[R]** `README.md` already argues the adjacent case —
*"That is the right call for a product that wants to feel like magic and the wrong one for
work you are accountable for."* Accountability without measurement is a claim, not a
property.

**The repository's own objection, and the strongest one [R]**, 08-15 record:

> If this over-corrects, the lead does the work serially itself and the tool's value
> evaporates. That failure is **silent**: nobody files a bug saying "this took three hours
> instead of forty minutes because we were careful." Over-scoping announces itself with a
> growing board; under-delegation announces nothing.

**[I]** This is the best argument in the repository against tightening anything, and the
proposed thesis is its answer: **under instrumentation the failure stops being silent.**
Decisions offered, divergence magnitude, and wall-clock to terminal green are all
measurable, and under-delegation shows up as a board that never fans out. The objection is
an argument *for* the instrument, not against it — which is the strongest kind of support
available, since it comes from a record that was arguing the other way.

### 6.8 Falsification [I]

What would show this thesis is wrong:

- **The divergence signal is mostly noise.** If declared-versus-actual diverges on nearly
  every task for benign reasons, the divergence pane is a second thing to ignore and the
  legibility argument collapses. This is checkable cheaply on historical runs if the diffs
  had been kept, and cannot be checked at all now — which is itself evidence for
  proposal 1.
- **Operators do not act on it.** If divergence is surfaced and approve-all persists, the
  problem was never legibility and the psychology in §2.2 has been misapplied.
- **Types do not transfer.** If converting several briefing lines to enforced properties
  produces no measurable change against a preserved prose arm, §2.3's central claim is
  weaker than this document treats it.
- **The specification bound bites harder than argued.** If well-specified acceptance
  criteria turn out to be as expensive to write as the work is to review, §5.1's
  reallocation has no headroom.

Note that **three of these four require the instrument to test.** That is the argument in
compressed form: the thesis is the precondition for evaluating the thesis, which is
uncomfortable and also exactly what §5.2 predicts about a domain with no measurement.

### 6.9 A naming note [I]

"Puppet Master — hold every string" is a control metaphor, and it is the metaphor the
evidence undercuts: holding every string is tier 4, and tier 4 does not hold. If the thesis
moves, the metaphor should follow — toward an instrument panel, a flight recorder, a
control room. Not urgent, and worth noticing that the current name commits to the position
being challenged.

---

## 7. Open questions

- **Where the retained counts live.** `STYLE.md` §1's *derive; do not store* is the
  standing objection; §6.5.1 argues it does not apply, but the mechanism (an `Effect`, a
  widened return, a side ledger) is unresolved and I have not read `STYLE.md` in full.
- **Whether the divergence check is per-write or per-task.** Per-write is cheaper and
  noisier; per-task needs the accumulation.
- **What the signed-set primitive actually is** — a `Plan` record, a task group, a tag on
  `Task`. And whether it interacts badly with cross-session claiming.
- **Whether `c9ab068` should be reverted under a plan-unit gate**, or whether per-declaration
  survives as the fallback for declarations outside an approved set. My inclination is the
  latter, so that the premise auto-approval rests on — *a worker taking the next item off a
  board the operator already approved* — becomes true at the level where the operator has a
  judgement to make.
- **Whether the instrument changes operator behaviour in a direction anyone wants.** §2.1 is
  a warning about exactly this: a system-level improvement can coexist with a personal-level
  degradation, and an instrument that makes the operator better at reading dashboards is not
  obviously making them better at the work.
- **Whether any of this survives contact with `approval.py` and `store.py`**, which I have
  not read.

---

## 8. Bibliography

**Cognitive artefacts and distributed cognition**
- Norman, D. *Cognitive Artifacts.* In Carroll (ed.), *Designing Interaction*, CUP, 1991. **[C]**
- Hutchins, E. *Cognition in the Wild.* MIT Press, 1995. **[C]**
- Clark, H. & Brennan, S. *Grounding in Communication.* 1991. **[C]**
- Krakauer, D. Complementary vs. competitive cognitive artefacts. **[C, essayistic — not an empirical programme]**

**Attention, memory, vigilance**
- Cowan, N. *The magical number 4 in short-term memory.* BBS, 2001. **[C]**
- Monsell, S. *Task switching.* TiCS, 2003. **[C]**
- Leroy, S. *Why is it so hard to do my work?* OBHDP, 2009. **[C]**
- Mackworth, N. *The breakdown of vigilance during prolonged visual search.* 1948. **[C]**
- Wickens, C. *Multiple resources and performance prediction.* 2002. **[C]**
- Wolfe, Horowitz & Kenner. *Rare items often missed in visual searches.* Nature, 2005. **[C]**
- Alter, A. & Oppenheimer, D. *Uniting the Tribes of Fluency…* PSPR, 2009. **[C]**
- Rozenblit, L. & Keil, F. *…an illusion of explanatory depth.* Cognitive Science, 2002. **[C]**
- Fisher, Goddu & Keil. *Searching for Explanations…* JEP:General, 2015. **[C]**

**Automation, trust, supervisory control**
- Bainbridge, L. *Ironies of Automation.* Automatica, 1983. **[C]**
- Sheridan, T. & Verplank, W. Levels of automation, 1978. **[C]**
- Parasuraman, R. & Riley, V. *Humans and Automation: Use, Misuse, Disuse, Abuse.* 1997. **[C]**
- Lee, J. & See, K. *Trust in Automation: Designing for Appropriate Reliance.* 2004. **[C]**
- Endsley, M. *Toward a Theory of Situation Awareness in Dynamic Systems.* 1995. **[C]**
- Endsley, M. & Kiris, E. *The out-of-the-loop performance problem.* 1995. **[C]**
- Skitka, Mosier & Burdick. *Does automation bias decision-making?* 1999. **[C]**
- Kahneman, D. & Klein, G. *Conditions for Intuitive Expertise: A Failure to Disagree.* American Psychologist, 2009. **[C]**

**LLM-specific empirical**
- METR. *Measuring the Impact of Early-2025 AI on Experienced Open-Source Developer Productivity.* 2025. **[U — verify title, n, task count, effect size]**
- Peng, Kalliamvakou, Cihon & Demirer. *The Impact of AI on Developer Productivity: Evidence from GitHub Copilot.* 2023. **[C — verify the ~55% figure]**
- Jakesch et al. *Co-Writing with Opinionated Language Models Affects Users' Views.* CHI 2023. **[C]**

**Formal foundations**
- Rice, H. *Classes of recursively enumerable sets and their decision problems.* 1953. **[C]**
- Lamport, L. *Proving the Correctness of Multiprocess Programs.* 1977. **[C]**
- Alpern, B. & Schneider, F. *Defining Liveness.* IPL, 1985. **[C]**
- Schneider, F. *Enforceable Security Policies.* ACM TISSEC, 2000. **[C]**
- Saltzer, Reed & Clark. *End-to-End Arguments in System Design.* ACM TOCS, 1984. **[C]**
- Parnas, D. *On the Criteria To Be Used in Decomposing Systems into Modules.* CACM, 1972. **[C]**
- Dennis, J. & Van Horn, E. *Programming Semantics for Multiprogrammed Computations.* CACM, 1966. **[C]**
- Anderson, J. *Computer Security Technology Planning Study.* 1972. **[C]**
- Reynolds, J. *Separation Logic.* LICS, 2002. **[C]**
- Kung, H. & Robinson, J. *On Optimistic Methods for Concurrency Control.* ACM TODS, 1981. **[C]**
- Meyer, B. *Object-Oriented Software Construction.* 1988/1997. **[C]**
- Findler, R. & Felleisen, M. *Contracts for Higher-Order Functions.* ICFP, 2002. **[C]**
- Wadler, P. & Findler, R. *Well-typed programs can't be blamed.* ESOP, 2009. **[C]**
- Takikawa et al. *Is Sound Gradual Typing Dead?* POPL, 2016. **[C]**

**In-repository sources** — all **[R]**, quoted from files read this session
- `README.md`, `CLAUDE.md`, `pptmstr/templates.py`, `pptmstr/board.py`, `pptmstr/brief.py`
- `planning/2026-08-15-a-task-reaches-the-board-without-a-decision.md`
- `planning/2026-08-21-the-board-takes-an-agents-word-for-what-it-will-touch.md`
- `planning/2026-08-22-four-items-buy-back-session-time.md`
- `notes/2026-08-21-opus-5-and-work-nobody-asked-for.md`
- `orchestrator-design.md` §2.7 and section outline

---

## 9. Checklist for the reviewing model

**Verify these first — they are load-bearing and I could not check them:**

1. Every **[C]** citation: author, year, venue, and that the substantive claim is what the
   work actually found. Exact wording is not claimed except where marked verbatim.
2. The METR figures specifically. Direction and rough magnitude are what the argument
   needs; the exact n and effect size are **[U]**.
3. That `PendingApproval.diff` is in fact discarded at `ApprovalResolved` — I have this
   from the 08-21 record, not from reading `store.py`.
4. That `render_diff` reads from disk — same provenance.
5. That `Task.touches` is nowhere compared to actual writes — same provenance, and the
   whole of §6.3 rests on it.
6. That `declare_task` is currently in `_REVIEW` rather than `_BUS_AUTO` post-`c9ab068`.
7. Whether `STYLE.md` §1's *derive; do not store* actually permits the exception argued in
   §6.5.1. I have that rule only as quoted in other records.

**Attack these — they are inference and I have marked them as such:**

8. §2.3's claim that the `templates.py` docstring and the 08-15 record are in direct
   contradiction. A reconciliation may exist that I did not find.
9. §5.1's ranking of marginal returns. It is reasoned, not measured, and the whole
   optimisation section depends on it.
10. §6.2's four objections, individually. Objection 2 is the one I would attack first: it
    may conflate "the gate did not constrain scope" with "the gate is mis-sited", when the
    honest reading is that the gate was designed for a different job and does that job.
11. §6.4's thesis. Specifically whether "instrument" is a product anyone wants, or whether
    it is a feature of a product whose thesis is something else.
12. Whether the transfer from supervisory control and automation psychology to
    multi-agent LLM orchestration is warranted. It is principled — the structural analogy
    to a human supervising semi-autonomous processes is tight — but **it is not validated**,
    and the psychology of this specific practice does not exist as a literature. Every
    concurrency number in §3.1 and §4.3 is engineering judgement with a theoretical warrant,
    not a finding.
