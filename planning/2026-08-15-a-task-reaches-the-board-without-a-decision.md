# A task reaches the board without a decision

**Recorded 2026-08-15**, after a splash animation cost eight agents. **Reframed
2026-08-16.** **Priority 1 built 2026-08-18, `c9ab068`, per-declaration** — see
"How the unit was settled" at the end. The rest is not built. This is the diagnosis and the design options,
written down before either is acted on, because the failure it describes is precisely
the one where acting first is the mistake.

The first version of this record was written around the wrong centre. It asked *which
tasks should require approval* and answered *the ones that came from findings*. The
operator's account of the same run puts the defect somewhere else: not in which subset
of the work gets stopped, but in the fact that nothing between a sub-agent's concern and
an agent doing work ever asks the operator anything. The evidence below is the same
evidence. It is re-cut against that question and it says something different.

pptmstr crashed while the run was still open and stranded the session that wrote the
first version, so its figures were taken from seven of eight agents and several
conclusions reached after its last edit never reached it. Those are repaired here.

---

## The run this comes from

A cold-start splash rework — flavour UI, four files of implementation. It ran as:

- **9 tasks declared**, one dropped unrun
- **8 agents**, 932,640 tokens
- individual agent durations up to 13.6h, overlapping

The eighth agent reported only after the crash, at 2026-08-16T11:13Z: 88,775 tokens and
13.6h, the longest of the run. The figures as first recorded — ~844k across seven, up to
7.6h — understated the run by that agent and the duration headline by six hours. This
matters beyond bookkeeping, because *the run at the top of this document is the
baseline* (see **The stress test**), and a baseline that omits its own worst case is one
a changed briefing beats for free.

Every agent did its job well, and several real defects were found on the way, including
two in `driver.py` that were falsely settling live sub-agents. The work was not wasted.
It was disproportionate, and the framework had nothing in it that would have said so.

The suite was green when this was written and was not green when the crash stranded it:
the six tests the last agent added to `tests/test_driver.py` had never been run, and
`scripts/verify_post_tool_use.py` was untracked. Run on 2026-08-16 they failed twice — a
missing import, and an assertion that a closing hook lowers a veto without also
restarting the silence clock that hook carries. Both were test defects rather than
driver defects, and 922 tests pass now. Recorded because the failure mode is the general
one: work that reports itself finished while its verification sits behind an approval
nobody answered.

## What the operator was asked, and when

This is the reframed evidence, and it is the whole of the finding.

| task | origin | tokens | asked before it ran? |
|---|---|---|---|
| `splash-core` | the ask | 96,751 | spawn only |
| `splash-tests` | the ask | 162,637 | spawn only |
| `junction-tests` | the ask | 92,242 | spawn only |
| `renderer` | the ask | 162,470 | spawn only |
| `closed-stream-liveness` | operator-reported symptom | 139,341 | spawn only |
| `photosensitivity-and-palette` | a parked record's open question | 111,480 | spawn only |
| `splash-core-amendments` | a finding | 78,944 | spawn only |
| `in-flight-veto` | a finding | 88,775 | spawn only |
| `final-review` | the lead's own thoroughness | declared, never run | — |

Nine tasks reached the board. **The operator was not asked about any of them.** Six of
the nine were declared in a single burst between roughly 13:50 and 13:55, minutes after
a one-line request to resume a parked splash record and before the operator had said
anything else. The operator's next message, at 13:58, is a bug report — not a response
to a plan, because no plan was put to them.

"Spawn only" is the column that matters, and it is not the mitigation it looks like.
`Task`/`Agent` really are gated (`approval.py:60-61`), so the operator approved eight
spawns, one summary line each, at the moment each fired. But **a spawn approval does not
say what the agent will do.** Workers are told to call `claim_task()`, and `_pick_claim`
(`store.py:509-525`) hands back the oldest claimable task with no session filter and no
role filter. The operator approves *that an agent may start*; the agent then selects its
own work from a board the operator has never seen. Approving the spawn and knowing the
work are two different things, and only the first one exists.

This is why strengthening the spawn gate is not the fix. It is already the strictest
point in the chain and it is asking a question whose answer does not constrain anything.

**What the operator did instead**, because it was the only channel available:

- **14:46:37** — *"DO not start new agents for documents until all implementation is
  done. I don't want thrashing where new agents chase the tail of code in flux."* An
  instruction issued into a run already in flight, about work already declared.
- **21:49** — an agent destroyed its own uncommitted work with `git checkout <file>` on
  the shared branch, with two builders still running.
- **21:50** — the operator halted the theme and palette line **by editing an inter-agent
  message in transit**.

Both interventions are the operator reaching into running work. Neither is a decision
they were offered; both are interceptions they had to invent. That is the symptom, and
the first version of this record named it correctly while proposing a fix aimed
somewhere else.

**The origin split does not survive as the axis.** It is still a true description of
where the work came from and is worth keeping for that. But cut by origin, the tokens
fall 653,441 to ask-origin, 111,480 to the parked record, 167,719 to findings. A rule
that gates finding-origin tasks addresses **18% of the run and none of the decisions**.
The 653,441 spent on the fan-out was spent just as silently. The fan-out itself was
correct — genuine parallelism over already-scoped work is what the tool is for, and it
should happen again — but *correct* and *consented to* are different properties, and
only one of them was present.

## The chain, and where the decisions are not

| step | operator decision? | where |
|---|---|---|
| a worker posts a concern to the lead | **yes** | `approval.py:71` — `post_concern` is in `_REVIEW` |
| the lead infers a task from it | no surface exists | — |
| `declare_task` puts it on the board | no | `approval.py:85`, `_BUS_AUTO` |
| a sub-agent claims it | no | `approval.py:84`, `_BUS_AUTO` |
| the sub-agent works it | per write, not per task | `_REVIEW` |

One link is gated, and it is gated for the wrong question. The operator is shown a
concern and asked *may this message be delivered*. What the message then becomes is
work. Approving a delivery reads as "pass it along"; nothing states that the same
keystroke is the last point at which the resulting task could have been stopped.

**The justification for auto-approving the rest is circular, and the code says so out
loud.** `approval.py:105-107`:

> Auto-approving these is what keeps the operator a bottleneck on decisions rather than
> on bookkeeping — *a worker taking the next item off a board the operator already
> approved is not a second decision.*

The reasoning is sound and the premise is false in this build: `declare_task` sits in
the same frozenset as `claim_task`. The claim is auto-approved on the grounds that the
board was approved, and **the board was never approved**. This is not a judgement call
about where a gate belongs; it is a stated assumption that does not hold.

`classify`'s own docstring gets the shape right one level down:

> `Task`/`Agent` require approval deliberately: spawning a sub-agent is a tool call like
> any other, and an orchestrator that gates writes but not the spawning of things that
> write has a hole in it.

The same sentence applies one level up and was not applied there. **The gate is on the
spawn, not on the work.** `subagent_cap` (default 4, `driver.py:146`) bounds how many
agents run at once; it bounds nothing about how much work those agents consume. Once
four spawns are approved, an unbounded board can be drained through them without another
decision. That is the mechanism behind the word *thrashing*.

## Priority 1: sign-off on declaration

`declare_task` is where work comes into existence, so it is where the decision belongs.
Everything downstream — which agent claims it, how many run at once, what each of them
writes — is bookkeeping about work whose existence was never in question. Everything
upstream is a lead thinking, which is free.

This is the one change this record argues for.

The absence of a board surface is deliberately **not** paired with it here. That absence
is real, it is why interception was the only lever available, and it is recorded below
as evidence. Designing it is a separate and larger piece of work with its own record to
be written.

## What a declaration must carry

If a declaration is the thing signed off, it has to carry enough for the decision to be
made in seconds and no more than that — a declaration that costs what a task costs has
moved the problem rather than solved it. The first version derived this list for a
finding record; it survives the reframing because the reader is the same person with the
same question:

- the claim, and where
- **what was verified by running versus by reading**, which this repository already
  treats as the line between a result and a guess
- **the cost if it is never fixed**, which is the triage key; without it everything reads
  as equally actionable, which is how three of these became tasks
- provenance: which agent, under which task
- confirmed, or suspected

**What it must not hold is a plan.** Work that arrives with a remedy attached reads as
already-decided and invites approval — and the plan is the part that goes stale. This
repository has already learned that once, in
`planning/archive/2026-08-15-the-splash-cycles-behind-a-raster-line.md`, which says of its own
parked branch that *the findings below are worth more than the code that was written
against them*. The code did not survive the pause; the findings did. A remedy written now
is written against code that will have moved by the time anyone acts on it.

**Sign-off is a scoping moment, not only an approval one.** The palette question
genuinely needed *an* answer — the naive triple is broken on four of nine palettes and
would have shipped — but it did not need step-optimality proofs and mutation testing. A
binary gate would have either admitted all of that or lost the part that was
load-bearing. What the operator sets is the size, not just the yes.

## The engine is `worker_prompt`, and it is mandatory

`templates.py:215` tells every worker:

> **Before you finish, post a concern to `lead`** naming the thing you are least sure
> about, or what you noticed that nobody asked you to look at.

Every worker is *required* to produce an out-of-scope finding before it finishes. N
workers produce N findings, structurally, whether or not N exist.

That line is also the most valuable one in the file, and it should not be removed. It is
how this run learned that `PreToolUse` does not bracket tool execution, that
`INK_HEIGHT_EM` is not an upper bound on what ImGui rasterises, and that
`git checkout <file>` on a shared branch silently destroys a teammate's uncommitted
work. Its stated reason holds: the result answers the question that was asked, and the
concern is the only channel for everything else.

**A finding has no disposal path.** It reaches a lead with two options — ignore it, or
`declare_task`. There is no third state, so producing a finding is mandatory and free,
disposing of one properly is undefined, and the undefined case collapses into the
expensive one.

That observation stands. What it is *not* is the whole defect. It describes one route
onto an ungated board. The run's own numbers show the other routes carry more: a lead
decomposing an ask, or reading a parked record, reaches the same board through the same
unguarded call. Gating the finding route alone leaves 82% of this run exactly as it was.

Meanwhile every verb on the lead's side is expansionary: *break the work into tasks*,
*start the workers the board needs*, *another agent in the same role is how the board
drains faster*. The only bound in the entire briefing is **"within reason"**
(`templates.py:137`). Three words carrying the whole counterweight.

## Why the guard does not go in the briefing prose

The instinct is to write the rule into `lead_briefing()`. That makes it advice competing
with other advice, and the evidence that this loses is in the repository already.

`CLAUDE.md`'s "How to think here" has seven rules. Four push toward more work —
correctness over speed, trust but verify, fresh documentation reads, don't claim more
than you verified. Two are weak brakes, and one of those points at `planning/`, which is
what turned a park snapshot into a backlog on this run. None is a proportionality rule.
The document is monotonically pro-rigor, so a new paragraph asking for restraint is
outvoted by construction.

The parts of `templates.py` that actually bind are the parts that are not prose.
`depends_on` prevented two agents editing one file all day, structurally. "Within
reason" prevented nothing. Prose is the weakest mechanism available and it is the one we
reach for because it is the cheapest to write.

The reframing does not change the *kind* of fix. It changes where the fix goes, not
whether it has to be structural.

## The lead inherits the register of whatever it reads

*Recovered from the run; argued before the crash and not written down at the time.*

This run resumed from a parked planning record, and that record is written in this
repository's register — every claim cited, every open question preserved, verification
distinguished from reading. The lead matched it. It did not ask what it was looking at,
and a cold-start splash animation does not earn the rigor that a driver-liveness defect
earns.

That is a second engine, distinct from the backlog effect above and not fixed by the
same thing. The backlog effect is about *how many* items a record hands a lead. This is
about *how hard* the lead works each one, and it scales with how well the source
document is written — so the better the record, the more it costs to act on. Note what
it implies for the phrase *verify this rather than taking my word*: written into a
briefing it reads as a standard, but each appearance buys a full investigation.

No remedy is proposed, deliberately. The remedy would be briefing prose, and the section
above argues that briefing prose does not bind. The diagnosis is recorded because a
mechanism with no cheap fix is exactly the one that gets forgotten.

## The board has no operator surface

Recorded as evidence for the diagnosis above, not as a design. It is why interception
was the only lever the operator had, and it is the subject of a successor record.

The board renders inside the DETAIL pane and no row is clickable (`ui/detail.py:468-520`,
`:479-483`). There is no BOARD panel in the layout (`app.py:563-571`). There is no
operator verb for declare, claim, release, or reassign — the only operator-authored
intent in the application is dismissing a failed session. A task claimed by a node that
then dies stays CLAIMED for the life of the session and nobody, human or otherwise, can
free it; `ui/board.py:81` computes `owner_gone` and renders it, which is as far as the
system goes toward doing anything about it.

Sign-off lands first regardless. It is the smaller change, it addresses the decision
rather than the view, and a surface designed before the decision exists would be a
surface for watching rather than for deciding.

## Making the meter visible

`declare_task` returns *"Task X is on the board."* It could return the board size, the
agent count, and the tokens spent.

The first version proposed exactly this and sent the numbers to the lead — the party
that had already decided. Under the reframing the same line is worth more pointed at the
operator, and it is the cheapest thing in this document: one string, at the exact moment
the number can still change a decision. The cost of a task is currently invisible
precisely when it is being paid, and invisible to the only person who did not choose to
pay it.

## The failure on the other side

If this over-corrects, the lead does the work serially itself and the tool's value
evaporates. That failure is **silent**: nobody files a bug saying "this took three hours
instead of forty minutes because we were careful." Over-scoping announces itself with a
growing board; under-delegation announces nothing.

The reframing makes this tension sharper rather than softer, and it is the main argument
against the obvious implementation. Sign-off on every declaration would have meant nine
decisions on this run, six of them inside a five-minute window, at a moment when the
operator had said one line and had no context to judge against. That is not obviously
better than what happened. It is why the unit of sign-off is the first open question
below and not an implementation detail.

## The stress test

`templates.py:14` says **the prompts are the feature**. A feature with no measurement is
a preference. This repository's own rigor is what stresses the orchestration hardest —
its `CLAUDE.md` demands verification, its `planning/` documents preserve open questions,
and both are what a lead over-reads — so pointing pptmstr at pptmstr is the test case,
not a soak.

The run at the top of this document is the baseline. A comparable task under a changed
briefing either beats it or the change did not work. The measurement that matters has
changed with the framing: not tokens saved, but **how many decisions the operator was
offered before the spend, and how many interceptions they had to invent instead.** On
this run those numbers are zero and two.

## Open

- **What unit is signed off** — a task, or a decomposition. Per-task is the obvious
  implementation and would have fired six times in five minutes here. Signing off on a
  *plan* — the shape of the fan-out, before any of it lands — costs one decision for the
  same coverage, but the board has no vocabulary for a set of tasks and would need one.
  This decides whether the fix is usable, and it is the question to answer first.
- **Whether "awaiting sign-off" is stored state on the board.** The precedent cuts in
  favour, unlike the first version's appeal to `depends_on`.
  `planning/2026-08-15-what-the-board-does-not-carry.md:176-182` refuses a fourth
  `TaskState` because blocked-ness is *derived* — computed from other tasks, never
  stored. An operator decision is the opposite: a fact about the outside world that
  nothing can recompute. That is an argument the first version needed and did not make.
- **What a lead may do with an unsigned task while it waits.** Reading it is free and
  sometimes changes an in-flight brief; the worked example from this run is the
  `git checkout` hazard at 21:49, which was worth propagating to the two builders still
  running and cost nothing. Where the line falls between propagating and acting is not
  settled here.
- **What happens with no operator attached.** Headless already denies rather than hangs,
  and a parked approval expires at six hours. A declaration that cannot be signed off is
  a lead with nothing to do, which is a different failure from a lead doing too much.
- **A direct conflict with a same-day sibling.**
  `planning/2026-08-15-what-the-board-does-not-carry.md:532-535` makes it item 0 of its
  ordered list that *"at the start of a session, the lead declares at least one task from
  `planning/`"*. On the evidence here that is the burst this document is about. One of
  the two records is wrong, and they were written the same day without citing each other.
- **Whether any of this belongs in `CLAUDE.md`.** The argument against is that a seventh
  pro-rigor document is what got us here; the argument for is that a lead running without
  pptmstr's briefing has no guard at all.

## No longer settled

The first version closed with: *every finding-origin task is gated, not only expensive
ones.* That is withdrawn as the settled position. It is not wrong so much as small — it
governs 18% of this run's tokens and none of its decisions, and it would have left the
operator exactly as unconsulted about the other 82%. Origin may still earn a place as a
*priority* on a sign-off queue. It is not the axis the guard belongs on.

What replaces it is not settled either, which is the honest state: the defect is located,
sign-off on declaration is the change this record argues for, and the unit of sign-off
has to be answered before any of it is built.


---

## How the unit was settled — 2026-08-18, `c9ab068`

**Per-declaration**, chosen by the operator over the two alternatives this record and the
gathering record put beside it: a declaration budget set at launch, and a per-plan gate.
The cost this record raises against it was quoted at the point of decision and accepted
with it — on the baseline run it fires six times inside five minutes, at a moment when
the operator had said one line.

**The change is one line of policy, which is the argument for it.** `declare_task` moves
out of the bus tools' auto-approve set and into review. Nothing new was needed: the gate
already parks a call, queues it by wait time with everything else, and lets the operator
rewrite it before it runs.

**The auto-approve set was wrong on its own terms, which is sharper than "it should be
gated".** That set is justified by one sentence — auto-approving coordination keeps the
operator a bottleneck on decisions rather than on bookkeeping, because *a worker taking
the next item off a board the operator already approved is not a second decision*. The
premise was false for exactly one member. Nothing had ever approved the board. Claim,
complete and release stay auto-approved and the premise now holds for them.

**A finding from building it, which changes the objection.** This record argues sign-off
*"is a scoping moment, not only an approval one — what the operator sets is the size, not
just the yes"*, and the declaration-budget candidate existed largely to deliver that. It
was already available: editing a parked call rewrites `detail`, `depends_on` and
`touches` through `updatedInput` before the task lands. Per-declaration is therefore not
the binary gate the argument against it assumes.

**No briefing prose accompanies it**, per this record's own section on why the guard does
not go in the prose. That section is why the whole of the fix is a set membership.

**Not built:** the meter, the board's operator surface (now `2026-08-18-the-board-is-a-tenant-of-a-pane-that-owes-it-nothing.md`
and built separately), and the structured fields under "What a declaration must carry".
The last is a schema change this row did not ask for; `detail` carries that as prose today.
