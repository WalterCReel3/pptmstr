# A team cannot read its own board

**Dated:** 2026-08-17 · **Status:** open, not started · **Found by:** synthesis of six
open records against the tree at `5bbb611`, plus four checks run for this document ·
**Supersedes nothing** — every record below stands and is cited rather than restated ·
**Gathers:**
[`2026-08-17-a-session-premise-is-a-place-not-a-message.md`](2026-08-17-a-session-premise-is-a-place-not-a-message.md),
[`2026-08-15-what-the-board-does-not-carry.md`](2026-08-15-what-the-board-does-not-carry.md),
[`2026-08-15-an-operator-instruction-the-lead-cannot-see.md`](2026-08-15-an-operator-instruction-the-lead-cannot-see.md),
[`2026-08-15-a-task-reaches-the-board-without-a-decision.md`](2026-08-15-a-task-reaches-the-board-without-a-decision.md),
[`2026-08-14-a-role-runs-one-agent.md`](2026-08-14-a-role-runs-one-agent.md) (phases 5–7),
[`2026-08-12-the-board-has-no-surface.md`](2026-08-12-the-board-has-no-surface.md)

Symbol names, not line numbers, per the 08-14 convention.

This record exists because the operator asked for the team fixed in one pass rather than
six. That is a legitimate ask and it needs an argument, because
`what-the-board-does-not-carry.md` item 11 records the opposite hazard: *"a planning
document is necessary and demonstrably not sufficient"*, and a seventh document is the
failure it names. The argument is below. If the thesis does not hold, this should be six
PRs and not one, and that is the first thing to disagree with.

---

## Why these are one record

The four reports the dogfooding produced — premises not seeded, the board not listable,
verification re-run in flight, and work reaching the board without a decision — look like
four features. They have one shape underneath them.

**A team's shared state is written by one participant, read once by another, and shown to
the third never.**

`Task.detail` is the closest thing to an authoritative spec this system has. It is written
by `declare_task` and read in **exactly one place in the entire application**: the string
`claim_task` interpolates into its reply. `BoardTask` has no `detail` field. No module
under `ui/` reads it. The operator cannot see it, the lead cannot read back what it wrote,
and the worker holding it cannot ask for it again — `_pick_claim` requires `is_claimable`,
which requires `PENDING`, so the current owner is told "Nothing claimable right now".

And the premises the operator actually means never reach that state at all. They are
`AgentRecord.task`, a string from one text box, in one agent's transcript, reachable by
nothing — which is the finding
`a-session-premise-is-a-place-not-a-message.md` settled against a measurement.

Read that way the reports collapse:

| report | restated |
|---|---|
| sub-agents are not seeded context | the premises are not an object |
| sub-agents cannot list tasks | the object has no read tool |
| sub-agents re-verify in flight | mostly report 2's symptom — `notes/2026-08-17-what-a-worker-is-given.md` §"Why report 3 happens" argues this, and credits blindness with making defensive re-running *correct* |
| work reaches the board without a decision | the operator is the third participant, and has no surface either |

So the PR is one thing: **give a team shared state it can read, and put the session's
premises into it.** It is not one thing if framed as "fix eight items", and that framing
should be refused even though the eight items are all real.

---

## The keystone: three tools report success they never had

`a-role-runs-one-agent.md` phase 6 records this for `declare_task` and it is still open:
`bus.py` returns `f"Task {task_id} is on the board."` unconditionally, while `store.py`'s
`TaskDeclared` arm drops the declaration two ways — an id that already exists, and
`_would_cycle`. A cycle-rejected declare tells the lead it landed, and the lead then waits
on a task that is not there.

**Checked for this document, and wider than that record noticed.** `TaskCompleted` and
`TaskReleased` carry the same shape. Both arms are guarded on
`t.state is TaskState.CLAIMED and t.claimed_by == intent.node_id`, and both handlers return
their success text unconditionally. Completing a task you do not own is a no-op that
reports *"Anything waiting on it is now claimable."* — so a lead can be told a dependency
cleared when nothing unblocked.

The general statement is exact and worth keeping: **of the six bus tools, the two that ask
questions round-trip through the effect channel, and the three that assert facts the
reducer may refuse do not.** `claim_task` and `read_inbox` already prove the round trip
works — `ClaimSettled` is emitted on *both* outcomes precisely so an agent is never left
parked. `post_concern` is the sixth and is not in this class: its only refusal is the
pre-emit `resolve_role` check, which it reports honestly through `role_status`.

This is the keystone because three separate records need it and none of them names it as
their own work:

- item 9's auto-depend — *"a refusal guard would need the effect-channel treatment
  `claim_task` and `read_inbox` already have, or it would silently swallow work while
  telling the lead it landed."*
- `a-task-reaches-the-board-without-a-decision.md`'s meter and any sign-off, both of which
  need somewhere to put an answer.
- `a-role-runs-one-agent.md` phase 6, which already scoped the fix — `bridge.ask` before
  the emit, a `ClaimSettled`-style effect, settled in `app.frame()` — and phase 7, which
  is one field on an effect that exists by then.

It also fixes a live defect on its own terms, which is the test for whether it belongs
first: it is worth doing even if everything below is cut.

---

## Three things that are only visible from the synthesis

### 1. A fix one record refused becomes buildable, in the other record's scope

`an-operator-instruction-the-lead-cannot-see.md` closes on two ways to make an amended
spec reach the worker holding it, and prefers the first — the worker re-reads the board
rather than trusting its transcript copy, *"which is the honest reading of 'derive, do not
store'"* — but does not take it, because it *"costs a discipline the workers do not
currently have."*

`what-the-board-does-not-carry.md` item 2 corrects that in one sentence: **"The preferred
option is not a discipline the workers lack — it is one they cannot practise."** There is
no tool. The current owner cannot re-read its own spec by any path in `bus.py`.

Neither record can fix this alone: one owns the amendment and has no read primitive, the
other owns the read primitive and defers the amendment. Put in one PR, the structurally
stronger option is available for the first time, and the weaker one — deliver the
amendment as a concern and accept the transcript copy as a cache with an invalidation
message — does not have to be built.

### 2. A read tool forces a scoping decision both records declined

`ui/board.py`'s `board_tasks` is scoped by `declared_by[0] == session_id`. `store._pick_claim`
applies no session filter at all. **A worker can therefore claim a task it would never have
been shown.** `board_tasks`'s own docstring records the asymmetry and says which of the two
is right *"is a store question — whether the board should be keyed by session at all — and
it is not settled here."* `a-role-runs-one-agent.md`'s hazard notes on phase 7 say the same
thing from the reducer's side.

It has been safe to leave open because nothing shows a worker anything. The moment a board
read exists, the tool has to answer it, and the answer moves a judgement `board.py`
currently makes in presentation into the pure core. This is a decision the PR creates
rather than inherits.

### 3. Two live team defects with no record

Both found while mapping the tree for this document.

**A relaunched or forked team session comes back solo.** `app._launch` takes
`template: str = "solo"`. The launcher passes it; `relaunch` and `fork` are wired as
`lambda task, model, cwd: _launch(state, task, model, cwd)` and drop it. So the two verbs
that exist to re-run work silently downgrade the team shape to one agent, and the failure
is invisible — the session runs, it just has no roles. This is squarely inside "the Team
function is fairly broken" and belongs in this PR regardless of the thesis.

**`initialPrompt` is never set, and had never been measured.** `driver._team` builds each
`AgentDefinition` with `description`, `prompt`, `tools` and `model` only. `memory`, `skills`
and `initialPrompt` are all unset. The premise record notes `initialPrompt` is unset as part
of establishing that premises reach no worker; it does not ask whether setting it would
work. It looked like the best candidate available: a **per-session** channel into a worker,
where every other channel is either per-build (`worker_prompt`) or the lead retyping, and a
*pointer* placed there would clear the no-second-copy refusal.

**Measured, and it is not one — see row 0 below.** The field was set on one probe role and
the worker reported NONE. It is struck from the options rather than left as a hopeful note.

---

## Scope

Ordered by what unblocks what, not by value. Each row says why it sits where it does.

| | work | source | why here |
|---|---|---|---|
| **0** | ~~Two questions on `scripts/verify_worker_context.py`~~ **done, run `84cb7f` — see below** | premise record step 0; §3 above | Group 5 is **void** if the first fails, and the second changes group 5's shape. Cheapest item here by a wide margin |
| **1** | ~~`declare_task`, `complete_task`, `release_task` become question-shaped and answer truthfully~~ **done, `2dddfea`** | phase 6; item 9 | The keystone. Fixes a live defect standalone, and carries the plumbing three later rows need |
| **2** | ~~A board read: the projection moves out of `ui/board.py`, one bus tool exposes it~~ **done, `b5287c3`** | reports 2 and 3; item 2 | Answers the report that is *literally true and a gap in the original specification*. Makes §1 above buildable |
| **3** | ~~`detail` on `BoardTask`; `task_id` on `Concern`; the board rendered in the DETAIL pane~~ **done, `5b42e5c` and `59ee38c` — the pane is BOARD, not DETAIL; see below** | items 2 and 3; board-has-no-surface | Item 3's *"claimed, and there is an open concern about it"* is a projection over data that already exists — no new state, no flag to forget |
| **4** | ~~`Task.touches`, and a `TaskDeclared` arm that appends an auto-dependency rather than refusing~~ **done, `de2b481` — see below** | item 9 | *"the highest-value store change in this document"* — the only change that removes a single point of failure. Needs row 1 |
| **5** | The brief: ~~launch spec structured~~ **step 1 done, `879e425`**; entry writer on `settings.save`'s temp + `os.replace` primitive, a pane that derives and shows supersession, workers told the path with the confirm-or-refute framing | premise record steps 1–3 | Gated on row 0, which passed |
| **6** | An amendment intent for `Task.detail`, `node_id=None`, distinct from `TaskDeclared` | operator-instruction record | Its own record establishes `declare_task` cannot be the path and the guard must not be weakened. Needs row 2 to be sufficient |
| **7** | ~~Sign-off on declaration~~ **done, `c9ab068` — per-declaration** | task-reaches-the-board | ~~**Unit unresolved — see below.**~~ Answered by the operator. Needed row 1 for a reply channel |
| **8** | ~~Prose and Makefile: `format-file`, worker instance addressing, "confirm the defect still exists", evidence class on findings, hold-vs-release with *both* rules stated together, and dropping `worker_prompt`'s *"it is the only way that reaches anyone"*~~ **done, `5a16521`** — `typecheck` **not** widened, see below | items 1, 4, 5, 6, 7, 8, 10 | Free, and unbuilt since 08-15. `notes/…what-a-worker-is-given.md` shows `make format` writing every file is still an active cause of report 3 |
| **9** | ~~`relaunch` and `fork` carry the template~~ **done, `f4c9a6c`** | §3 above | Unrelated to the thesis, one line, and the team is broken without it |

### Row 0 is done — run `84cb7f`, 2026-08-17, `claude-sonnet-5`

Two questions added to `verify_worker_context.py` as two new roles, so questions 1–4 keep
the prompts they answered in run `04e4db` and the two runs are comparable. The negative
control passed across **all six** roles — every one answered NONE to the briefing canary —
so the method still discriminates and nothing below is void. Questions 1 and 3 reproduced
`04e4db` exactly: `memdefault` and `memproject` indistinguishable, `rosterbroad` naming
five siblings, `rosternarrow` NONE.

**5. A worker can read an absolute path outside `cwd`, at both shapes.** The canary was in
the `tool_response` on PostToolUse for both targets — a temp directory merely outside cwd,
and the real `~/.claude/projects/<cwd-slug>/briefs/<id>/000-premises.md` shape the premise
record proposes. The worker's account agreed with the wire in both cases, and the probe
compares them rather than trusting either.

**The premise record's step 0 passes, and group 5 is not void.** The line it flagged —
*"this is not measured, and the whole design rests on it"* — is now measured, and the
design does not have to move into the working tree.

**6. `initialPrompt` does not reach a worker.** The field was set on exactly one role, with
its canary existing nowhere else, and that worker reported NONE. Scoped honestly: what is
measured is `initialPrompt` **plus a spawn prompt from the lead**, which is the only shape
this application can produce — the lead always passes a `prompt` to the `Agent` tool. Which
of the two loses is not separated here, and does not need to be: for pptmstr the channel is
unavailable either way. Telling a worker its brief exists therefore falls back to the
candidates the premise record already lists — `worker_prompt`, the spawn prompt,
`Task.detail`, and the gate's `permissionDecisionReason`.

**One correction to the premise record, worth making because it cites the number.** That
record reports *"Two init messages arrived and they are identical"* and concludes no
per-agent init exists. This run produced **seven** inits for a root and six workers, and
they are byte-identical — checked, not eyeballed. So the count does track agents and the
08-17 reading of it was wrong; the conclusion it supported is unchanged and now rests on
the right evidence. **A worker's context is not readable off the wire because the inits
carry nothing per-agent, not because there is only one of them.**

### Rows 1, 9 and 8 are done — 2026-08-18, solo, `2dddfea`, `f4c9a6c`, `5a16521`

Done by hand rather than by a team, for the reason the circularity section gives. Three
things came out of the building that the record did not have.

**Row 1 was wider than "three tools" once the refusals were named.** Splitting
`TaskRefusal` by the recovery it implies rather than by the guard that produced it
turns the two ownership guards into five answers, and the extra pair is real: a
completion refused because nobody holds the task and one refused because the task is
already finished were the same silent no-op, and they mean *claim it first* and *stop*.
`NOT_APPLIED` is the member the reducer never returns — it is what the Bridge hands back
when the frame loop dies before the intent is applied, and it exists because a write
abandoned at shutdown has no ordinary negative to fall back on the way `claim_task` does.

**Row 9 was not one line, and the missing line was the type.** The template was
droppable because `_launch` gave it a default, so three-argument lambdas typechecked
cleanly. Carrying it in the callback type (`Callable[[str, str, str, str | None], None]`)
is what makes the same mistake a mypy error, which is the only guard available for a pane
whose drawing cannot be tested without pixels.

**Row 8's `typecheck` item is not free, and the claim it rests on does not survive being
run.** `what-the-board-does-not-carry.md` item 5 calls widening `make typecheck` to
`scripts/` and `tests/` "a one-line Makefile change" whose only cost is "a backlog".
Measured: **498 findings across 27 files** at the strictness `pyproject.toml` holds the
application to — 162 missing annotations, 116 `arg-type`, 70 `typeddict-item`, and the
`arg-type` hits are mostly tests deliberately holding `list[object]` and narrowing by
hand. Relaxing `strict` for those two directories only moves it to 480, because the bulk
is not annotation hygiene.

So the honest options are to fix the backlog or to decide what strictness test code is
held to, and both are decisions rather than a Makefile edit. `make typecheck-all` was
added outside `check` so the number is visible to whoever takes it; `check` is unchanged
and `make typecheck` still reads one directory of three. **This is the item's own
argument turned back on it** — the reason it did not happen on 08-15 is the reason it is
not happening here, and saying "free" a second time would be the third instance.

---

**Row 8 is prose, and this repository has recorded that prose does not bind.** That
objection is quoted in three of the gathered records and it is correct — but it is an
argument against prose as a *fix for a structural problem*, not against prose at all. Rows
1–7 are the structure. Row 8 is what the workers are told once the structure exists, and
two of its entries (`format-file`, `typecheck`) are not prose.

---

### Row 4 is done — 2026-08-18, solo, `de2b481`

Four things came out of the building that the record did not have. The first two are
limits rather than results, and they are stated here because both are easy to read as
solved from the outside.

**The row-1 dependency is sharper than the table's reason for it.** The table says row 4
needs row 1 because row 1 "carries the plumbing three later rows need", which is true and
is not the binding reason. The binding reason is that this is the only place the board
*changes what an agent asked for*, and an edit the caller is not told about is a board
that silently disagrees with the lead's own plan. Without a reply channel the added edge
would be discovered by a worker that cannot claim the task — the same fact, arriving
later, shaped like a defect report. So `TaskWriteSettled` gained `auto_depends`, which
bends its own recorded argument that one effect serves all three writes because they
"differ only in which refusals they can produce". That is still the right shape: a
completion and a release cannot change their caller's request, so the member is empty
there rather than meaningless, and splitting a second effect out for the single write
that can carry it would give three handlers two shapes to await for one question.

**Normalisation is spelling, not resolution, and the gap is the interesting part.**
`normalised_touches` runs `posixpath.normpath`, so `./pptmstr/store.py` and
`pptmstr/ui/../store.py` are one file. It does *not* resolve against a session's `cwd`,
follow a symlink, or reconcile an absolute path with a relative one — all of which need
the filesystem, and the reducer does no IO. A declarer that mixes absolute and relative
paths gets no protection. That is a reason for the briefing to ask for repository-relative
paths, not a reason to put a `Path.resolve` in a pure function, and the briefing now says
so in those words.

**Session scoping cost coverage here, and the bill is worth naming.** Question 3's answer
— which made the board session-scoped in row 2 — means the overlap check only sees the
declarer's own board. **Two sessions in one working directory get no protection from this
at all.** The alternative is worse: a dependency on another session's task would be a
blocker that never appears on the board waiting for it, unresolvable and invisible at
once. But this is a real gap rather than a solved case, and it is the first place row 2's
answer has cost something.

**The added edge cannot close a cycle, which is what makes the ordering safe.** The
auto-dependency is appended *after* `_would_cycle` has already passed on the declared
ones, which looks like a hole. It is not: a cycle through the new task needs something
that reaches it, and nothing can — its id is not in `tasks` yet, because a duplicate is
refused before this runs, so no existing `depends_on` can name it. Every edge added here
points from a brand-new node at an old one. Pinned by a test rather than left as an
argument.

Verified: 972 tests, `ruff`, `black`, `mypy pptmstr`. Twelve new tests, and a mutation
pass over the ten load-bearing decisions — inert overlap check, dropped normalisation,
COMPLETED not skipped, session filter removed, declared-dependency added twice, caller's
own spelling stored, edges computed then dropped, effect not carrying them, handler not
reporting them, `touches` dropped by the tool — **10/10 caught**.

---

### Row 3 is done — 2026-08-18, solo, `5b42e5c` and `59ee38c`

Split in two because the data and the surface are separable and the surface moved: the
projection landed first, then the board left DETAIL for a pane of its own
([`2026-08-18-the-board-is-a-tenant-of-a-pane-that-owes-it-nothing.md`](2026-08-18-the-board-is-a-tenant-of-a-pane-that-owes-it-nothing.md),
where the four design questions are answered).

**The row-3 table entry says "rendered in the DETAIL pane" and that is now wrong**, which
is worth leaving visible rather than editing out. The scope was written before the pane
record existed, and the operator's decision to move the board out wholesale means DETAIL
now carries no board at all — not even the one-line summary that was offered. That is
recorded with its cost in the companion record.

**The concern link is doing the work item 3 predicted, and the fixture proves it.** The
fake driver's `t2` is claimed by a live, working builder and is not moving; before the
link it was pixel-identical to ordinary progress, because `owner_gone` is false there.
It now reads `1 concern` on the collapsed row and the subject behind the triangle. That
is the whole of item 3's *"a derivation, not a new state"* — no fourth `TaskState`, no
held-deliberately flag, nothing to forget to update.

**A concern naming a task that does not exist is delivered, not refused**, and the
projection reports the dangling id the way `missing` reports an undeclared dependency.
The asymmetry is deliberate: `post_concern`'s other refusal, an unresolvable role, stops
the message reaching anyone at all, whereas a bad task id costs the link and nothing else.

**`post_concern` gained its first test through the live bus.** The test double never had
`resolve_role`, so the one tool that does not park on a future was reaching the store with
no coverage of its wiring at all — which is exactly the class of gap row 1 was about, one
tool over.

---

## What this record does not decide

Four questions, each of which one of the gathered records left open on purpose. Settling
them here by implication is the way this PR goes wrong.

**1. The unit of sign-off.** ~~`a-task-reaches-the-board-without-a-decision.md` names this
its first open question and says it *"decides whether the fix is usable"*.~~
**Answered by the operator, 2026-08-18: per-declaration, over both alternatives.**
Built as `c9ab068` — `declare_task` moves from the bus tools' auto-approve set into
review, which is the whole change, because the gate already parks and edits a call in
flight. The cost below was quoted at the time of the decision and accepted with it:
this fires once per declaration. Per-declaration
would have fired six times inside five minutes on the baseline run, at a moment when the
operator had said one line. Per-plan costs one decision for the same coverage but *"the
board has no vocabulary for a set of tasks and would need one."*

What the build added to the argument: editing a parked declaration rewrites `detail`,
`depends_on` and `touches` through `updatedInput` before the task lands, so the unit is
not the binary gate the objection assumes. That is the record's own *"sign-off is a
scoping moment, not only an approval one"*, available without the budget that was
proposed to deliver it.

A third shape falls out of the brief and is recorded here as a candidate, **not** as a
resolution: the operator sets a **declaration budget** at launch, enforced structurally at
`TaskDeclared`, with declarations past it parking. It fits that record's own line that
sign-off *"is a scoping moment, not only an approval one — what the operator sets is the
size, not just the yes"*, and it survives the prose objection because a number in the store
is not a paragraph in a briefing. Its cost is honest and it is the same cost that record
raises against per-declaration sign-off: the operator has no basis to pick the number at
launch either. The difference is that a wrong budget is paid once and raised, and a wrong
per-task gate is paid every time.

**2. Narrow read or general read.** ~~Item 2 endorses `task_detail(task_id)`.~~
**Answered by the operator, 2026-08-18: general, on the grounds that it is easier to
narrow later than to discover the narrow one was never enough.** That answers report 2
directly rather than answering item 2 and leaving the report open. The counter stands and
is not dismissed — showing every worker the whole board is a wider grant than anything
previously recorded, and may feed the relitigation report 1 is about. It is now a thing to
watch on the next team run rather than a thing to argue: **if relitigation rises, the
narrowing is a filter on `board_tasks`, not a redesign**, because the tool and the pane
share one projection.

**3. Session-scoped or fleet-scoped.** ~~§2 above.~~ **Answered by the operator,
2026-08-18: session-scoped, without qualification.** Implemented as `Task.belongs_to`,
applied by `board_tasks` and `store._pick_claim` alike, so the cross-session claim §2
describes is no longer a state the reducer can enter rather than one nothing happened to
exercise.

A consequence the question did not anticipate, recorded because it is the kind of thing a
later reader will meet as a surprise: **a task with no declarer now belongs to no
session** — not to every session. Nothing in the application builds one, since the gate
stamps every declaration and an unstamped call raises, so the only sources were tests. The
alternative would have left precisely the unattributed tasks claimable by anybody, which
is the hole the question was asked to close.

**4. The same-day conflict.** `what-the-board-does-not-carry.md` item 0 — *"at the start of
a session, the lead declares at least one task from `planning/`"* — against its sibling,
which argues that is the burst it exists to warn about. Both records flag it; neither
settles it. The premise record argues a brief may **dissolve** it rather than pick a
winner, since premises arriving at launch would stop that mechanism being load-bearing.
This PR will take a side by construction. It should take it on purpose.

---

## The circularity, stated rather than worked around

`a-task-reaches-the-board-without-a-decision.md` nominates this exact work as its stress
test — *"pointing pptmstr at pptmstr is the test case"* — with the 8-agent, 932,640-token
splash run as the baseline and the measurement being **how many decisions the operator was
offered before the spend, and how many interceptions they had to invent instead**. On that
run: zero and two.

Running this PR through a team would be that test. Running it through the *current* team is
running the test on the apparatus under repair, and the failure mode is the one every
gathered record describes. Rows 0 and 1 are small, mechanical and have a live defect
attached; doing them by hand costs little and makes the apparatus honest enough to be
worth pointing at itself. Whether rows 2–9 are done solo or as the first supervised team
run is a decision worth taking after row 1 lands, with the baseline numbers recorded
before it starts.

---

## Not doing

Each of these is a refusal already argued in a gathered record. They are listed so the PR
can be checked against them rather than re-deriving them.

- **Brief amendments** (premise record step 4). It says itself they want evidence about the
  re-read trigger from steps 1–3 *"rather than a guess about it"*, and names the hazard:
  *"a live brief nobody re-reads is a frozen brief with extra steps."*
- **Content in the brief store.** A path is a pointer; content beside the file is the second
  copy `STYLE.md` §1 names.
- **The brief in the working tree.** Every launch would dirty it — item 4's untracked-file
  trap.
- **One growing brief file.** Not atomic above `PIPE_BUF`; a reader catches partial records.
- **`AgentDefinition.memory`.** Measured as not the lever, run `04e4db`: the canary arrives
  with the field unset and the two arms were indistinguishable.
- **Refusing a colliding declaration.** Item 9: auto-depending is strictly better — nothing
  is rejected, so nothing needs a reply channel, and the collision becomes the wait it
  should have been.
- **Content-hashing findings against the tree.** Item 1: a shared uncommitted tree has no
  revision that moves.
- **A `BLOCKED` state or a "held deliberately" flag.** Item 3, and `TaskState`'s own
  docstring. Derive it from a concern that names its task.
- **Giving the review roles Bash.** Item 7. A scoped run capability is the version worth
  building and it is not urgent.
- **Letting builders fix gate failures outside their files.** Item 5: it trades a red tree
  for the concurrent-write failure the board exists to prevent.
- **Reconciling the rail's sub-agent count with the cap's.** They answer different
  questions; a live count is a third projection or nothing.
- **A mandatory brief.** Taxes the `solo` common case for a team problem.
- **Anything that makes a handed-down premise authoritative.** The property
  `what-the-board-does-not-carry.md` is organised around — *"a version of this team that
  followed instructions faithfully would have shipped worse work than one that argued"* —
  is what a seeding mechanism most easily breaks. The distinction the notes draw holds:
  arguing from **evidence** is the property and must survive; arguing from **absence**,
  because the worker has no path to a settled decision, is the waste.

---

## Verification boundary

**Executed for this document**, against the tree at `5bbb611`:

- `grep -rn '\.detail\b' pptmstr/ --include=*.py` → one hit, `bus.py`'s `claim_task`
  interpolation. The 08-15 claim still holds on 08-17.
- `store.py`'s `TaskDeclared`, `TaskCompleted` and `TaskReleased` arms read directly; the
  claimer guards and the two silent drops are quoted from the source, not recalled.
- `bus.py`'s `declare_task`, `complete_task` and `release_task` return strings read
  directly, confirming all three are unconditional.
- `app.py`'s three `_launch` call sites read directly, confirming `relaunch` and `fork`
  omit the template argument.
- `templates.worker_prompt` read directly: it still ends on *"it is the only way that
  reaches anyone"*, and still teaches no instance address. Item 8 is unfixed.

**Run for this document:** `scripts/verify_worker_context.py`, run `84cb7f`, extended with
questions 5 and 6. Its answers are above and its evidence for question 5 is `tool_response`
on the wire rather than a worker's account of it.

**Everything else is reading**, including the map of `driver.py`, `pool.py`, `approval.py`
and `ui/` that this record's scope table rests on. Nothing here was run as a live team
session, and no other number in this document was produced by a run performed for it — the
932,640-token baseline is quoted from the record that measured it, and run `04e4db` is
quoted from `verify_worker_context.py`'s own output.

**Still not measured, and load-bearing:** nothing in row 0 — but rows 1–9 are unbuilt and
every claim about what they will fix is an argument, not a result. The stress-test numbers
this PR is supposed to move (decisions offered, interceptions invented) require a live team
run and none has been performed for this record.

---

## In order, if this gets done

0. ~~**Row 0**, the probe.~~ **done**, run `84cb7f`.
1. ~~**Row 1**, the three lying tools.~~ **done**, `2dddfea`.
2. ~~**Row 9**, `relaunch`/`fork`.~~ **done**, `f4c9a6c`.
3. ~~**Row 8**, the prose and the two Makefile targets.~~ **done**, `5a16521`, less the
   `typecheck` widening, which is not free and is now its own decision.
4. ~~**Row 2**, the board read — after questions 2 and 3 above have answers.~~ **done**,
   `b5287c3`, with both questions answered by the operator first rather than by the code.
5. ~~**Row 4**, `Task.touches`.~~ **done**, `de2b481`, with two limits recorded rather
   than left to be discovered: normalisation is spelling only, and the check does not
   cross sessions.
6. ~~**Row 3**, the board's surface.~~ **done**, `5b42e5c` (the data) and `59ee38c` (the
   pane). It did not land in DETAIL: the companion record's pane was built in the same
   pass, and the board left DETAIL entirely.
7. **Row 5**, the brief — after row 0 says it can exist.
8. **Rows 6 and 7**, amendment and sign-off — after question 1 has an answer, and with the
   baseline numbers recorded before either lands.
