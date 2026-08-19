# The protocol's safety property was not the file boundaries; it was that workers pushed back

**Dated:** 2026-08-15 · **Status:** open, no code changed · **Found by:** dogfooding the
splash-panel work · **Companion:**
[`2026-08-15-an-operator-instruction-the-lead-cannot-see.md`](archive/2026-08-15-an-operator-instruction-the-lead-cannot-see.md),
which covers item 2 below in depth and is not restated here.

Symbol names, not line numbers, per the 08-14 convention.

One feature, one lead, six builders, two reviewers, concurrent against a shared board
with file ownership declared per task. The feature is incidental; it was an animated
panel and it does not appear again in this document. What follows is the coordination
behaviour the run exposed. All eleven are observed. None are hypothetical.

Each item says what happened, what the mechanism was, what would have to change, and
whether it is worth fixing or only worth knowing. **Eight are worth fixing, two are
worth knowing, and one is a premise that turned out to be false.** A list that treated
all eleven as bugs would be recommending changes to things that worked.

**Read item 9 first.** It is numbered near the end because it was reported late, and it
is the sharpest one here: it is the only failure the coordinator caused rather than a
worker, and it is the only one that reached the single property everything else depends
on.

## What actually held, and it is not what the design credits

Six agents edited concurrently for a full session. Declaring file ownership inside
`Task.detail` and using `depends_on` to sequence work that would collide did the job it
exists for across almost all of the run, and the independent-tasks-first shape had two
builders productive from the first minute — the property `lead_briefing` spends a
paragraph on ("one worker per independent task ... start those workers together").

But **four separate agents this session refused or corrected a spec they were handed,
and every one of them was right.** A builder refused to delete a constant a reviewer
called unused. A builder refuted a claim the lead had relayed as established. A builder
declined to build a spec an operator had superseded, and held rather than released it. A
builder found another agent in its files, released, waited, and re-claimed. Remove that
behaviour and the same structure, the same board and the same prompts ship a deleted
working constant, a redundant test, a panel in the wrong pane and a corrupted file.

That is the finding this document is organised around: **a version of this team that
followed instructions faithfully would have shipped worse work than one that argued.**
The `worker_prompt` line that produces it — "Say plainly when you disagree with another
agent rather than deferring to it — an agreement nobody tested is worth nothing here" —
is doing more load-bearing work than any mechanism in `store.py`. It is also the
property with no enforcement at all, and the one the structure taxes: refusing costs a
worker something, being wrong costs the lead nothing (item 6).

## Where the separation did hold, and where it did not

The file boundaries worked nearly everywhere. Two sets of files were written by two
agents at once, and the reason is the same in both cases: **the board enforces
sequencing and does not enforce ownership.**
`depends_on` is mechanical — `Task.is_claimable` will not hand out a task whose
dependencies are unfinished, and no agent can override that. The file list is prose
inside `detail`, honoured because every agent read it and chose to. Both breaches came
from participants that did not read the prose: a Makefile target (item 4) and a task
declaration that omitted the dependency (item 9).

So the accurate summary is not "ownership held". It is **`depends_on` held perfectly
everywhere it was used, and every failure of separation was a failure to use it.** No
agent overrode the mechanism; nobody reached it. That is a different problem with a
different fix, and it is why items 4 and 9 sit near the top of the fix list.

The work itself came out well, and these numbers are the lead's report at the close of
the run rather than anything re-run for this document: **878 tests passing, lint and
typecheck clean**, and the artifact the session existed to produce improved on its own
terms — 12 ink bands became 14 buckets, the worst ink-height spread went
0.7260 → 0.4700 em, and the smallest bucket is 3, so no cell is frozen by having nothing
to trade with. Item 10 is the reason the attribution is worth making explicit.

---

## 1. A finding is a claim about a tree at an instant; a task is an instruction about a tree later

**What happened.** A reviewer read the tree and reported a dead test pin. By the time
the lead had triaged it into a task and a builder had claimed it, another agent had
already repaired it. The builder found line numbers matching nothing and defects that
were not there. It reported that instead of reconstructing them, which was right; a
less careful agent would have "fixed" working code, and the fix would have been a
regression with a task id behind it.

**Mechanism.** Nothing anywhere carries the instant. `Concern.posted_at` and
`Task.declared_at` are both `time.monotonic()`, so the *ordering* is recoverable from
the store — but ordering is not the question. The question is whether the file still
says what the reviewer read, and no record links a finding to a tree state.

**The obvious structural fix does not work, and that is the interesting part.** The
natural move is to stamp findings with a revision. There is no revision to stamp: nine
agents edit one uncommitted working tree, so `HEAD` does not move for the entire
session and two findings hours apart carry identical shas. A staleness check would need
per-file content hashes captured at read time and re-checked at claim time — real
machinery, in the store or beside it, for a hazard whose per-instance cost is one grep.

**It happened a second time, to the lead, about this document.** While this was being
written the lead relayed a finding as established: the rank-table pin compares
`rank()`'s return value to `RANKS`, so a `main()` mutated to assemble the table some
other way would leave it passing, and the shipped artifact — stdout — was unpinned.
That was true of the tree the finding was read against. It was not true of the tree by
the time it was relayed: the agent that found the gap had added
`test_the_generator_prints_the_table_that_is_checked_in` in the same task, and another
agent settled it by running `main()` down a one-stage pipeline through its own CLI
rather than taking the claim on trust —

```
stock main([]):   printed == source -> True   (19 lines)
one-stage main(): printed == source -> False  (17 vs 19 lines)
```

— which is the mutation the finding said was uncaught, caught. **There is no fourth
instance of the claim-outruns-what-runs pattern; there are three, all fixed.** Recording
a fourth here would have been one, which is why this paragraph exists instead of a
section. The staleness window in this instance was minutes, and both the finder and the
fix were the same agent in the same task — the shortest possible window still produced a
wrong instruction.

**What would have to change: a protocol rule, and it is already half-written.** The
08-14 convention of citing symbols rather than line numbers exists because symbols
survive edits that line numbers do not; it should be stated as what it is, a staleness
mitigation. The other half is the rule the builder followed without being told:
*confirm a reported defect still exists before fixing it; if the spec describes code
that is not there, report that and stop.* Both are prose in `worker_prompt` and cost
nothing.

The second instance adds a constraint the first does not: **the rule has to bind the
lead as well as the worker**, because the lead's copy of a finding goes stale on exactly
the same clock and its version arrives with authority attached. See item 6.

**Worth fixing.** The rule, not the machinery.

## 2. The spec is snapshotted at claim time and nothing can invalidate it

Covered in the companion document, which establishes that `claim_task` interpolates
`won.detail` into the reply once and the worker proceeds from a transcript copy, and
that this is why amendment alone is necessary and not sufficient.

**One thing to add, which the companion leaves open.** It offers two ways forward and
prefers the first: the worker re-reads the board rather than trusting its transcript
copy. **That option has no tool today.** `_pick_claim` given an explicit `task_id`
returns the task only if `is_claimable`, and `is_claimable` requires `PENDING` — so a
worker calling `claim_task("my-own-task")` on the task it currently holds is told
"Nothing claimable right now". The current owner cannot re-read its own spec by any
path in `bus.py`. Whatever else the amendment intent needs, it needs a read primitive
beside it: either a `task_detail(task_id)` tool, or `claim_task` answering the existing
owner idempotently instead of refusing it. The preferred option is not a discipline the
workers lack — it is one they cannot practise.

**Worth fixing**, in the companion's scope.

## 3. A held task and a wedged worker look identical, and the board's stranded signal is the wrong half

**What happened.** A builder held a task claimed without working it, having reasoned
that releasing would republish a superseded spec to the next builder. That was correct
(the companion argues why) and it is the reason the operator redirect was caught at
all. But an operator watching the board saw a row sitting in `CLAIMED` with no reason.

**Mechanism, and it is sharper than "the board cannot express it".** `BoardTask`
already derives `owner_gone` — still `CLAIMED`, claimer finished, failed, cancelled or
gone from the snapshot — and its comment calls it "the derived condition a board owes
its reader". So the board *does* distinguish two kinds of stalled row. It distinguishes
the wrong two. The held task had a live, working, correctly-reasoning owner, so
`owner_gone` was false and the row was pixel-identical to ordinary work in progress.
Meanwhile `BoardTask` carries no `detail` field, and `grep -rn '\.detail' pptmstr/`
finds exactly one read of `Task.detail` in the application — the `claim_task`
interpolation. The spec is written once, read once, by one agent, and displayed
nowhere.

**What would have to change.** Two things, and the second matters more than the first:

- **A UI surface.** Put `detail` on `BoardTask`. It is already in the store, it is
  already the closest thing to an authoritative spec, and it is currently invisible to
  the one participant who can correct it. This single addition serves item 2 and the
  companion as well.
- **A derivation, not a new state.** The instinct is a fourth `TaskState` or a
  "held deliberately" flag. `TaskState`'s docstring refuses that shape for `BLOCKED`
  and the refusal applies here: intent stored as a flag is kept true by whoever
  remembers to update it. But the builder *did* record its reason — it posted a concern
  — and **`Concern` has no `task_id`.** Link a concern to a task and "claimed, and
  there is an open concern about it" becomes a projection over data that already
  exists, in the same shape as `blocked_on` deriving from the dependency graph. No new
  state, no flag to forget, and the board gains a reason for every stalled row that
  anyone bothered to explain.

**Worth fixing.**

## 4. Tree-wide tooling crosses every boundary the board relies on

**What happened.** `make format` is `.venv/bin/black pptmstr scripts tests`. A builder
ran it and reformatted another builder's untracked file. Untracked, so `git checkout`
had nothing to restore from: no revert path at all.

**Mechanism.** Per-file ownership is the property the whole arrangement rests on, and
it is prose in `detail` (see the framing section). A Makefile target does not read
prose. One command with a directory argument reaches every file nine agents own.

**"Worth asking what else does" — asked, and the answer is reassuring.** Every other
tree-wide target reads: `lint` is `ruff check` plus `black --check`, `typecheck` is
`mypy pptmstr`, `test` is the whole suite, `check` is those three. `clean` deletes
regenerable caches. **`format` is the only target in the Makefile that writes source
files.** Tree-wide *reading* is not the hazard and must not be discouraged — it is how
item 5 becomes visible in the first place. Tree-wide *writing* is the hazard, and there
is exactly one instance of it.

**What would have to change.** A protocol rule ("format only files you own") plus the
Makefile target that makes the safe path the easy one — a `format-file FILE=...` beside
`format`, so an agent formatting its own work does not have to know the invocation.
Cheapest fix in this document by a wide margin: one rule and four lines of Makefile
against one of the two observed cross-boundary writes of the session.

A second rule is worth attaching, because it is what turned a nuisance into an
unrecoverable one: **`git add` a new file as soon as you create it.** A staged file has
a revert path; an untracked one does not. This costs nothing and would have made item 4
a footnote — and it would have helped item 9 as well, where the recovery also depended
on being able to see what had changed under whom.

**Worth fixing**, early.

## 5. A gate red in someone else's file has no owner

**What happened.** Lint and type errors sat in files nobody's task named. Every builder
that met one refused to fix it, reported it to the lead, and moved on. Every one of
those refusals was correct under the ownership rule. The tree stayed red for the entire
session.

**Mechanism.** The board scopes work by file; the gate is tree-wide by construction.
Work outside every declared file boundary therefore belongs to no task, and "report it
to the lead" is not a mechanism that assigns it to anyone. Correct local behaviour,
bad global outcome, and nothing in between the two.

**What would have to change: nothing in the code.** `depends_on` already expresses
this. A task declared last — *green the gate* — depending on every file-touching task,
claimed by one agent when the others have finished, has no ownership conflict with
anything because everything it could collide with is complete. That is precisely the
job `depends_on` exists to do, and it was simply not declared. Discipline for the lead,
stated in `lead_briefing`.

The alternative — letting builders fix red in files they do not own — trades a red tree
for concurrent writes to one file, which is the failure the structure exists to
prevent. Do not take that trade.

**One gap in the gate itself, while it is in view.** `make typecheck` is
`mypy pptmstr` — it never sees `scripts/` or `tests/`. `make lint` covers all three
directories, so the asymmetry is easy to miss: a tree that passes `make check` has had
two of its three directories typechecked by nobody. Both were checked by hand this
session and nothing keeps them that way. This is a one-line Makefile change and the
only reason to hesitate is that turning it on will surface a backlog — which is this
same problem again, and so belongs in the terminal task rather than left to whoever
trips over it.

**Worth fixing**, and free.

## 6. The lead is not a neutral pipe; it is where an observation becomes an instruction

**What happened, twice.**

- A reviewer reported a constant as unused. The lead put it in a task without checking.
  The builder ran one grep, found it used exactly once, and refused.
- The lead relayed a finding about an unpinned generator artifact as established fact
  (item 1). It had been fixed in the same task that found it. A builder ran the mutation
  through the real CLI and refuted it.

The first was someone else's error passing through the lead. The second was the lead's
own. That difference matters less than the thing they share, which is that **an
observation acquires authority by passing through the coordinator, and nothing in the
passage tests it.**

**Mechanism.** A finding and a task are different kinds of object — a claim and an
instruction — and the lead is the component that converts one into the other. `Concern`
has no field distinguishing "I believe X" from "X is established", and nothing in
`TaskDeclared` looks at where a detail came from or how old it is. The conversion is
invisible because it happens in prose, and it is one-way: once a claim is inside
`Task.detail` it reads as specification, and the only evidence of its provenance is
whatever the lead chose to write down.

**Both were caught downstream, by agents with less authority than the sender.** That is
the opposite of how this structure is supposed to fail. A worker refusing a lead pays a
cost the lead does not pay for being wrong — the companion document records a lead
spending a long, confident message accusing a correct builder of fabricating an
instruction — so the mechanism that saved both of these is one the structure actively
taxes.

**On the throughput question the task poses.** The tempting conclusion is that lead
verification replaces builder verification and is cheaper, because the lead checks each
finding once while builders would check one per claimed task — but those counts are
about the same, roughly one finding per task, so there is no arithmetic saving. The
real difference is what each check catches, and they catch different things:

- The **lead's** check kills *wrong* findings, and it must be the lead's, because the
  lead is where a claim acquires authority. Nothing downstream can undo that.
- The **builder's** check kills *stale* findings (item 1), and it must be the builder's,
  because staleness is a function of when the work starts. The lead's check happens
  earlier and is therefore *more* exposed to staleness, not less.

So they do not substitute. The lead verifying does not license builders to stop, and
the builder's grep in this incident would have caught nothing if the finding had merely
been out of date. What the lead's check does buy is that the builder's refusal stops
being the last line of defence — which matters because refusing a lead is expensive for
a worker, and the companion document records what it cost the one that did.

**Worth knowing, with one rule.** The rule is for the lead and it is narrow: *a finding
you did not verify goes into a task as a finding to check, not as an instruction to
carry out.* One word of framing in the `detail` field, and the authority stops
transferring.

## 7. A role's tool grants silently determine the quality of its findings

**What happened.** Both reviewers produced findings that were entirely reads plus
arithmetic: no test run, no script run, nothing rendered. Several rested on assumptions
they could not check. Both said so plainly, and that disclosure is the only reason it
is visible at all.

**Mechanism.** `READ_ONLY_TOOLS` is `Read, Glob, Grep, NotebookRead, WebFetch,
WebSearch`, and the comment above it gives the reason: review roles' "value depends on
their not being able to quietly fix what they were asked to find". That reasoning is
sound and I am not arguing with it. But the grant conflates two capabilities: **cannot
edit** and **cannot execute.** Only the first was intended. The second came along with
it, and it is the one that determines whether a finding is evidence or inference.

This is a live instance of `STYLE.md` §2's own rule — "plumbed through" and "works end
to end" are different claims, and only the first is provable by reading. The review
role is, by its tool grant, structurally incapable of making the second kind of claim
about anything.

**What would have to change, and what should not.** Not "give the reviewer Bash" —
Bash edits files, which discards the property the grant was chosen for. The honest
options are a narrowly-scoped run capability (a tool that runs `make check` and returns
output, nothing else) or accepting the limit and making it legible. The second is
nearly free and captures most of the value: **every finding states its evidence class,
read-derived or run-derived.** Both reviewers did this voluntarily; putting it in the
reviewer prompt makes it reliable, and it lets the lead weight a finding before item 6
turns it into an instruction.

**Worth knowing**, plus the one prompt line. The scoped-run tool is a real option and
not urgent.

## 8. Concerns are not one-way — the premise is false, and the outcome is real anyway

The brief for this document states that concerns are one-way, that builders can post
only to the lead and cannot see each other. **The first half is wrong and I checked it
before writing around it.**

`post_concern` resolves its `to` argument through `AgentSession.resolve_role`, which
looks up `self._roles` — every address the session has spawned, not a whitelist of the
lead. A builder can post to `reviewer`, or to `builder-2`, and it lands. `role_status`
even enumerates the alternatives (`known_roles`) when a name misses. And
`worker_prompt` explicitly invites it: "Use `post_concern(to, subject, body)` to raise
something with another role too." The channel exists, it is peer-capable, and the
workers are told about it.

**What is actually true, and it produces the observed failure by a different route.**

- `worker_prompt` also says, of posting to the lead: *"it is the only way that reaches
  anyone."* That sentence is about the pre-finish concern, but read as a statement about
  the channel it is false, and it is the sentence a worker acts on.
- **Instance addressing is documented only to the lead.** `lead_briefing` explains that
  the second agent in a role is `builder-2`; `worker_prompt` never mentions it. A
  builder wanting to reach its peer does not know the peer has a name, and a session
  running six builders is six agents each of whom can reach only the roles it can guess.
- **A concern carries nothing that distinguishes a proposal from a decision.** `Concern`
  is `sender, recipient, subject, body, posted_at, state, edited, delivered_at`. One
  builder's message to the lead — one of three options, explicitly a proposal — was
  acted on as though decided. Once relayed, the original sender is gone and the two
  arrive in the same shape. This is the same class as `Concern.edited`: a fact about the
  message that exists nowhere in the message.
- The lead's inbox is private, so a proposal sent to it is invisible to the peers it
  concerns. That is correct behaviour, but it means the lead is the only participant
  who can tell peers what was decided, and nothing marks its relay as a decision.

**What would have to change.** Two prompt lines, not a channel: teach `worker_prompt`
the instance-address scheme it already teaches the lead, and drop or qualify "the only
way that reaches anyone". Whether `Concern` should carry a kind is a real question and
not settled here — a field a model fills in is a field a model chooses, which is the
argument `bus.py` makes about senders, and it would need the same kind of answer.

**Worth knowing.** The channel needs nothing; the prompts and the record do.

## 9. The mechanism that keeps two agents out of one file is opt-in, and only the coordinator can invoke it

**What happened.** The lead declared `splash-art-deadstage` over the same three files —
`scripts/rank_glyphs.py`, `pptmstr/ui/splash_art.py`, `tests/test_splash_art.py` — that
the still-live `splash-art-fix` already owned, **with no `depends_on` between them.**
Two agents wrote the same three files at the same time. One re-wrapped the other's edit
and independently derived the same correction. The worker noticed, posted a concern,
released the task, waited for the files to go quiet, re-claimed and finished. That
sequence is exactly right and is the only reason this cost minutes rather than a
corrupted file.

**Why this inverts the rest of the document.** Every other failure here was a worker
meeting a limit of the structure, and in every case a worker caught it. This one was
*caused* by the coordinator — and was still caught by a worker. There is no
higher-level participant watching the lead. `depends_on` is the entire mechanism
preventing two agents in one file; it is mechanical and it is unbypassable, and it is
also **opt-in, invoked by hand, by the one participant whose mistakes nothing else
checks.** A structure whose safety property depends on the coordinator remembering
something is a structure with a single point of failure, and the sections above that
credit `depends_on` are crediting a mechanism that was not reached.

Note also what the worker had to do to recover: release, wait, re-claim. That is the
opposite of the companion document's *hold and escalate*, and both are right. The rules
differ because the hazards differ — hold when releasing would republish a spec you
believe is wrong; release when another agent is in your files, because holding a
claimed task does not stop anyone else's writes and only your absence does. Anyone
writing either rule down owes the other one beside it, or the next worker will apply
whichever it read.

**What would have to change.** The lead's proposal — the board refuses a declaration
naming files an unfinished task already claims — is the right instinct, and it needs
three things it does not have:

- **The store would have to know which files a task touches.** Today that is prose in
  `detail`. It would become a structured field, `Task.touches`, supplied by the
  declarer. Worth checking this against `STYLE.md` §1 before proposing it: it is *not*
  a stored duplicate of a derivable fact — nothing else in the snapshot implies which
  files a task will edit, in the same way and for the same reason `declared_by` is
  stored. It passes.
- **The guard shape already exists.** `TaskDeclared` calls `_would_cycle` and drops a
  declaration that would create a cycle. "Overlaps a live task's files" is the same
  kind of structural invariant, checked in the same arm.
- **But refusal is the wrong verb, and following the existing guard would import a
  defect.** `declare_task` in `bus.py` emits its intent and returns
  `"Task {id} is on the board."` unconditionally — it never learns whether the reducer
  accepted it. **A declaration rejected today for a cycle is reported to the lead as
  successful**, which is its own small item and worth fixing regardless. So a refusal
  guard would need the effect-channel treatment `claim_task` and `read_inbox` already
  have, or it would silently swallow work while telling the lead it landed.

**The better version avoids all of that: do not refuse, auto-depend.** A declaration
naming files a live task holds gets that task appended to its `depends_on`. Nothing is
rejected, so nothing needs a reply channel; the collision becomes a wait, which is
precisely what the lead should have written by hand. The board would then produce the
correct sequencing from the file lists the lead is already writing in prose, and the
mechanism would stop being opt-in.

**Worth fixing**, and it is the highest-value store change in this document.

## 10. The board grants exclusivity on files and none on the gate that reads all of them

**What happened.** `make test` went red twice mid-session inside `test_splash.py`'s
quote group. Both reds recovered within about a minute, both occurred while another
builder was mid-write on that file, and nothing was ever broken.

**Mechanism.** A shared working tree and a shared test suite are a shared mutable
resource. `make test` is `pytest -q` over the whole suite: it imports every test module
from the live tree, so a file being rewritten during collection or execution is read in
whatever state it is in at that instant. File ownership does not help, because the gate
does not respect file ownership — it reads all of them by design. The board grants
exclusivity on files and grants nothing on the gate.

**This makes item 5 materially harder than it looked.** Several agents this session
reported "the tree is red, and not from me" and were right to. At least one of those
reds may have been this rather than a real defect, and there is no way to tell after the
fact. So the unowned-failure problem is not one problem but two stacked on each other:
**the signal is unowned, and it is sometimes not real.** An agent triaging a red it did
not cause cannot distinguish a defect in someone else's file from a snapshot of someone
else's file mid-save, and the two call for opposite responses — report it, or ignore it
entirely.

Worth saying plainly that this also weakens every instruction in this session that told
a worker to run the full gate before reporting done. That instruction is right, and its
output is not trustworthy while anyone else is writing.

**What would have to change.**

- **A protocol rule, immediately: re-run a red before reporting it, and name the file.**
  A transient recovers on the second run and a real failure does not, so one re-run
  separates them at nearly zero cost. Naming the file lets the next reader match it
  against who was writing.
- **A structural answer already exists and is not being used: run the gate on a tree
  nobody is writing.** A git worktree, or any snapshot copy, gives the gate a stable
  read at the cost of a checkout. Verdicts from it are trustworthy in a way verdicts
  from the live tree cannot be while agents are concurrent.
- **Item 5's terminal task solves this for free as a side effect**, which is the best
  argument for it. A *green the gate* task depending on every file-touching task runs
  when the writes have stopped, so it is the one gate run in the session whose result
  means what it says. Everything before it is advisory.

**Worth fixing.** The re-run rule now; the sequencing is item 5 and pays for itself
twice.

## 11. The board's own tool rejects the call its description tells you to make

**What happened.** The first action of at least one builder this session — the author of
this document, so this is first-hand and not a report:

```
claim_task()  ->  Input validation error: 'task_id' is a required property
```

The documented call fails, and `claim_task(task_id="")` succeeds. Fully described in
[`2026-08-13-every-bus-tool-requires-its-optional-arguments.md`](archive/2026-08-13-every-bus-tool-requires-its-optional-arguments.md),
including the second reproduction on `declare_task`, and not restated here. Now on the
board as `bus-tool-schema`, correctly framed as a fresh read of the SDK's `tool` /
`SdkMcpTool` before any fix, since the whole defect is a belief about what the schema
shorthand does.

**Three things this adds beyond that document.**

**It is the same defect shape, in the coordination layer itself.** Every other instance
this session was a claim in the work outrunning what runs. This one is a claim in the
*mechanism that tells agents what they may do*. The component whose entire job is
self-description has a self-description that is false, and the board this document is
about is the thing it misdescribes.

**A written record did not cause the fix, and this document is a written record.** The
defect was found on 08-13, written down with two live reproductions and a first step,
and was still there on 08-15 to greet the first worker of the session. That is the
uncomfortable fact for a post-mortem, and it should not be quietly omitted from one:
**a planning document is necessary and demonstrably not sufficient.** Nothing converts
`planning/` into board items, so an open doc competes for attention with whatever the
operator is currently looking at, and loses. Two of the eleven items here (5 and 9)
have fixes that are free and were already implied by mechanisms the repository had; the
reason they did not happen is the same reason this one did not. If any single process
change comes out of this document, the candidate is *the lead declares a task from an
open planning doc at the start of a session* — which costs one call and is the only
thing here that acts on the backlog rather than adding to it.

**The cost is silent and lands on the careful reader.** A worker that reads the
description and does what it says gets an error; a worker that guesses `""` sails
through. Nothing in the transcript surfaces this to the lead — every builder that hit it
worked around it and said nothing until one thought to mention it, which is the same
shape as item 5's unowned red and item 7's unrunnable reviewer: **a cost absorbed
locally by a competent agent is a cost the coordinator never sees.** That is the general
form of at least four items in this document, and it is the argument for surfacing
things on the board rather than trusting that a problem loud enough will be reported.

**Worth fixing**, and it is already claimed.

---

## In order, if any of this gets done

0. **The process change item 11 argues for**, because without it the rest of this list
   is another open document. *At the start of a session, the lead declares at least one
   task from `planning/`.* One call. It is the only thing here that drains the backlog
   rather than adding to it.
1. **Item 11** — already claimed as `bus-tool-schema`. Every new worker meets it first.
2. **Items 5 and 10 together** — a terminal *green the gate* task with `depends_on`,
   `make typecheck` widened to `scripts/` and `tests/`, and the rule that a red is
   re-run and named before it is reported. One Makefile line and one lead habit; it also
   makes a gate result mean something for the first time.
3. **Item 4** — a rule, a `format-file` target, and "stage new files immediately". One
   of the two observed cross-boundary writes, and the cheapest thing here.
4. **Items 1, 6, 7, 8** — prose in `worker_prompt`, the reviewer prompt, and
   `lead_briefing`: confirm a defect still exists before fixing it (and the lead too,
   before declaring it), label a finding's evidence class, pass an unverified finding on
   as a finding rather than an instruction, and teach workers the instance addresses the
   lead already gets. No code, and together they cover four of the eleven.
5. **Item 9** — `Task.touches`, and a `TaskDeclared` arm that appends an auto-dependency
   rather than refusing. The only change here that removes a single point of failure
   instead of documenting one. Carries a prerequisite worth doing on its own: a
   `declare_task` that reports whether the board actually took the declaration.
6. **Items 2 and 3** — `detail` on `BoardTask`, a read primitive for a claimed spec, and
   a `task_id` on `Concern`. Real store and UI work, and the companion document holds
   the argument for the first of them.

## Not doing

- **Content-hashing findings against the tree.** Reasoned through in item 1: a shared
  uncommitted tree has no revision that moves, so the cheap version does not exist and
  the real version costs more than the hazard.
- **A `BLOCKED`-style state or a "held deliberately" flag on `Task`.** Item 3: intent
  stored as a flag is the shape `TaskState` already refuses. Derive it from a concern
  that names its task.
- **Giving the review roles Bash.** Item 7: it discards the property the read-only grant
  was chosen for. A scoped run capability is the version worth building.
- **Letting builders fix gate failures outside their files.** Item 5: it trades a red
  tree for the concurrent-write failure the board exists to prevent.
- **Refusing a colliding task declaration.** Item 9: auto-depending is strictly better —
  nothing is rejected, so nothing needs a reply channel, and the collision becomes the
  wait it should have been.
- **Trusting a gate result taken while agents are writing.** Item 10: re-run it, or take
  it from a tree nobody is writing, or take it last.
- **Recording a fourth instance of the claim-outruns-what-runs pattern.** There are
  three, all fixed. The candidate fourth was refuted by a run (item 1), and writing it
  down would have been an instance of it.
- **Any code change in this pass.** This document owns no source file: nothing under
  `pptmstr/`, `scripts/` or `tests/` was edited, and `make format` was not run.
