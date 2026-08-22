# The board takes an agent's word for what it will touch

**Dated:** 2026-08-21 · **Status:** proposals, none built

Companion to [`../notes/2026-08-21-opus-5-and-work-nobody-asked-for.md`](../notes/2026-08-21-opus-5-and-work-nobody-asked-for.md),
which asks whether Claude Opus 5 initiates work nobody requested and what controls it.
That record answers the first half and leaves the second half open on the evidence. This
one asks what would have to exist here before the second half could be answered at all.

The finding that produces these proposals is not about a model. `Task.touches` is a
declaration an agent makes before it works. Nothing in this repository ever compares it
to what the agent wrote. Every measurement the application computes about a write is
either destroyed at approval or dies with the session, so there is no quantity here that
a scope lever could move, and no way to tell a lever that worked from one that did not.

---

## What is established here, and what is inferred

Four code claims are load-bearing and I read them in this session rather than taking
them from a report:

- `AgentSession._system_prompt` returns `{"type": "preset", "preset": "claude_code",
  "append": briefing}` (`driver.py:1357-1368`). pptmstr's lead runs **on top of Claude
  Code's own preset**, not instead of it.
- `store._apply`'s `ApprovalResolved` arm rebuilds the record as
  `remaining = tuple(p for p in rec.pending if p.id != intent.pending_id)`
  (`store.py:387-393`). The resolved `PendingApproval` leaves, and its `diff` leaves with it.
- `approval.render_diff` (`approval.py:184-193`) computes a real unified diff and
  **reads the current file from disk** so the diff is against what is actually there.
- `Task.touches` is `tuple[str, ...]`, normalised by `normalised_touches`
  (`model.py:639`, `:702`), and its own comment calls it *"the files this task will
  write"* (`model.py:687`) — future tense, which is the whole point.

The rest of the repository map — that `_team` sets four of `AgentDefinition`'s thirteen
fields, that `effort` and `task_budget` are set nowhere in the tree, that the SDK exposes
both — comes from a read-only survey run this session. It is read-derived and cited by
path there, and I have not re-read every line of it.

Two facts are quoted from existing records rather than re-measured:
[`../notes/2026-08-17-what-a-worker-is-given.md`](../notes/2026-08-17-what-a-worker-is-given.md)
measured that project `CLAUDE.md` reaches every pptmstr sub-agent while the
`lead_briefing` append reaches none, and `templates.worker_prompt`'s docstring records
run `84cb7f` setting `AgentDefinition.initialPrompt` on a probe role and the worker
reporting NONE.

Nothing here was built. `make check` was run once at HEAD `2c8a7a3` and is green — ruff,
black, mypy strict over 40 files, 1131 tests — which says PR #11 landed clean and says
nothing about anything proposed below.

## Why this is not a prose problem

[`2026-08-15-a-task-reaches-the-board-without-a-decision.md`](2026-08-15-a-task-reaches-the-board-without-a-decision.md)
settled this and the argument holds without amendment:

> The parts of `templates.py` that actually bind are the parts that are not prose.
> `depends_on` prevented two agents editing one file all day, structurally. "Within
> reason" prevented nothing. Prose is the weakest mechanism available and it is the one
> we reach for because it is the cheapest to write.

The external research arrived at the same place from outside and knew nothing of this
repository. No controlled evidence exists for any prompt-side mitigation of unrequested
work, and the best-instrumented public result is negative: four stacked mitigations,
including a hook injecting *"answer only what was asked"*, with the targeted errors
continuing after each was added. Two independent lines converging is the reason the
constraint from
[`2026-08-21-a-dead-session-costs-more-than-its-approvals.md`](2026-08-21-a-dead-session-costs-more-than-its-approvals.md)
is adopted here without argument: **each item is stated as a structural change or it is
not proposed.**

One argument those records did not have available. Because `CLAUDE.md` reaches every
sub-agent and `lead_briefing` reaches none, a restraint paragraph in `CLAUDE.md` is not
one experiment — it changes every role in every template simultaneously, with no arm left
unchanged to compare against. The cheapest-looking place to put the rule is also the only
place that destroys the ability to find out whether it worked.

## What the repository cannot currently see

`STYLE.md` §2 already states the rule this section is an instance of:

> A model reporting success is not evidence of success. Capture the tool result, the hook
> input, the wire.

`touches` is a model reporting scope. Nothing captures the wire.

| Quantity | Where it exists | Why it cannot be used |
|---|---|---|
| Per-call diff | `PendingApproval.diff`, from `render_diff` | Dropped at `ApprovalResolved` (`store.py:393`) |
| Token spend | `AgentRecord.usage` | Per node, not per task; in-memory |
| Transcript | `AgentRecord.transcript` | In-memory, dies with the session |
| CLI transcript path | captured in `_gate_tool_use` | Read by nothing |
| Declared scope | `Task.touches` | A declaration, never compared to a write |

The diff is the sharpest loss. It is computed on every `Write`/`Edit`/`MultiEdit`, it is
correct, and it is discarded a few hundred lines later by the arm that settles the
approval it belongs to.

---

## The proposals

### 1. The diff the gate already computes is thrown away at approval

`render_diff` reads the file from disk, so once the write lands the "before" is gone and
the diff cannot be recomputed from any later snapshot. This matters because `STYLE.md` §1
says *derive; do not store*, and the obvious objection to retaining anything here is that
rule. It does not apply: a **transient being discarded** is not a derivable fact being
duplicated. There is no snapshot from which files-touched and lines-changed follow after
the fact.

What is worth keeping is not the diff text. It is the count — files touched, lines added
and removed, per resolved approval, accumulated against the agent. Bounded, and it is the
first quantity in this application that describes what an agent did rather than what it
spent.

The design question is where it goes. `STYLE.md` §1's *when the core must answer, widen
the return* suggests an `Effect` rather than a new field, and the exception table makes
`Store` deliberately imperative without authorising it to retain domain data across a
resolution. Cheapest item here, and the one every other item needs.

### 2. `touches` is a declaration nothing compares to the writes

The one native to this design rather than borrowed from the research.

`depends_on` binds because the board computes over it. `touches` feeds that same
computation — the board adds a dependency when two tasks would write the same file — and
is then never consulted again. The gate sees the path of every write:
`_pre_tool_use` → `_gate_tool_use` → `approval.classify`, fail-closed on unrecognised
tools. The claiming task's `touches` is on the board. Comparing the two is a projection
over facts already present, which is the shape `STYLE.md` §1 asks for rather than an
argument against it.

**Observe only. Deny nothing.** Record divergence — a write outside the claiming task's
declared scope — and surface it. That log is simultaneously the dependent variable item 1
is trying to produce and the only artifact in the design that would distinguish what an
agent said it would do from what it did.

Denying is a separate decision and should not ride along with this one. The public
negative result was a hook that *injected an instruction*; a hook that *refuses a write*
is a different mechanism and is untested by anybody. Turning it on before the
false-positive rate is known would convert an unmeasured problem into an unmeasured
obstruction, and the agent that hits it cannot tell the difference.

### 3. `AgentDefinition.effort` is a lever that has never been shown to arrive

`_team` sets four of `AgentDefinition`'s thirteen fields and `effort` is not among them;
`_options` sets neither `effort` nor `task_budget`. Per-role effort is one line.

It is a probe before it is a lever. Run `84cb7f` set `AgentDefinition.initialPrompt` on a
probe role with its canary nowhere else and the worker reported NONE. An SDK field
existing is not evidence that it reaches the agent, and `scripts/verify_worker_context.py`
already establishes the shape that answers this — nonce canaries, everything denied at the
gate but two paths, contaminated arms declared void.

Keep the two questions apart. *Does the field arrive* is answerable here and cheap. *Does
effort narrow scope* is a different question and the external evidence says not to expect
it: the sentence promising that behaviour belongs to Opus 4.7, and Opus 5's own
documentation says effort governs thinking volume rather than what gets produced.

### 4. pptmstr inherits a system prompt it does not control and has never measured

`_system_prompt` appends to the `claude_code` preset. On 2026-07-24 Anthropic removed
over 80% of that preset's text for Opus 5, including the two rules — no comments by
default, no unrequested planning documents — whose absence names the two most-reported
over-delivery symptoms. Both variants ship in the installed CLI, model-gated.

So the preset under pptmstr's lead changes shape depending on which model is selected in
`MODELS`, and nothing here observes that. `CLAUDE_CODE_SIMPLE_SYSTEM_PROMPT=0` forces the
long variant, which makes this an A/B rather than a speculation: same repo, same task set,
two arms, scored on the counts item 1 produces.

This is the experiment nobody in the public corpus has run, and it is the one that would
separate the model from the harness. It is listed last because it is worthless without
item 1 — with no dependent variable it produces two runs and an impression, which is what
every source in the research already offers.

It is also the case the baseline record described: *pointing pptmstr at pptmstr is the
test case, not a soak.*

---

## Ordering

Not a decomposition, for the reason the predecessor records give: work arriving with a
remedy attached reads as already-decided.

- **Cheap, clearly right, and a prerequisite for everything else:** 1
- **Structural, native to the design, and the only new mechanism here:** 2
- **A probe, not a build:** 3
- **Blocked on 1, and the only item that answers the question the research could not:** 4

## Open

- **Whether divergence should ever deny.** Item 2 proposes observing. The case for
  refusing is that `depends_on` binds precisely because it refuses; the case against is
  that a false positive is indistinguishable, from inside the agent, from a broken tool.
  The observation period is what would decide it, and nothing else can.
- **Whether `touches` is at the right granularity to compare against.** A task that
  declares a directory, or declares nothing, produces divergence counts that mean
  something different from one that names three files. The board normalises paths; it does
  not constrain specificity.
- **Where the retained measurement lives.** `AgentRecord` field, or `Effect` returned from
  the reducer. §1 argues for the second and the first is easier, which is the usual shape
  of that mistake.
- **Whether any of this survives contact with a second session writing the same tree.**
  PR #11 merged into this repository partway through the session that produced this
  record, under the agents working in it. A divergence log that cannot tell an agent's
  write from a merge landing underneath it will report noise.

## Not claimed

None of this is evidence that unrequested work will decrease. No item here is a
mitigation, and item 2 in observe-only mode deliberately changes no behaviour at all. The
claim is narrower and is the reason the set is worth building: at present this repository
cannot tell whether any scope lever works, and after item 1 and item 2 it could.

The external findings are not re-derived here. They are recorded, with their provenance
and their gaps, in the companion note — including that the system card was never opened,
that a widely-circulated effectiveness figure has no source, and that a search summariser
returned correct text under a wrong model label three separate times during the
investigation. Anything in this document that leans on those findings inherits those
limits.

Nothing in this document was built. No code was changed to produce it.
