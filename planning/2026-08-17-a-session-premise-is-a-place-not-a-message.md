# A session's premises are a place, not a message

**Dated:** 2026-08-17 · **Status:** open, not started · **Found by:** dogfooding —
three operator reports — and settled against a measurement rather than a reading ·
**Probe:** `scripts/verify_worker_context.py`, run `04e4db` · **Companions:**
[`2026-08-15-what-the-board-does-not-carry.md`](2026-08-15-what-the-board-does-not-carry.md),
[`2026-08-15-an-operator-instruction-the-lead-cannot-see.md`](2026-08-15-an-operator-instruction-the-lead-cannot-see.md),
[`2026-08-14-the-transcript-outlives-the-window-and-our-record-of-it-does-not.md`](2026-08-14-the-transcript-outlives-the-window-and-our-record-of-it-does-not.md)

Symbol names, not line numbers, per the 08-14 convention. The observations this
argument was assembled from are in
[`notes/2026-08-17-what-a-worker-is-given.md`](../notes/2026-08-17-what-a-worker-is-given.md),
which this record supersedes.

---

## The report, and why the obvious diagnosis was wrong

Workers on a team relitigate decisions the session's originating document already
settled: research points rehashed, approaches reworked, arguments had again.

The obvious reading is that workers cannot see the document. That reading was checked
and **it is false in the half that matters.** `verify_worker_context.py` plants one
nonce per channel, denies every tool that could reach a nonce another way, and takes
answers back through a probe-owned MCP tool so what is captured is a call rather than
a sentence about one. Run `04e4db`, `claude-sonnet-5`:

- **Project CLAUDE.md reaches a worker.** All four workers reported the canary, with
  `AgentDefinition.memory` unset. The arm that set `memory="project"` was
  indistinguishable, so **that field is not the lever.**
- **`lead_briefing` does not reach a worker.** All four answered NONE. This was the
  negative control, and its passing is what makes the other rows readable.
- **The agent registry is conditional on the tool grant.** The role inheriting every
  tool named its siblings *and* the built-in agent types — the init message's `agents`
  array exactly. The role carrying an explicit list without `SendMessage` or `Agent`
  answered NONE.
- **No per-agent init exists on the wire.** Two init messages arrived and they are
  identical. A worker's context is not readable by observation; asking it is the only
  way.

So a worker on this project already reads project memory, and through it the pointers
to `STYLE.md` and `planning/` — and it relitigates anyway.

**The premises the operator means are not in project memory. They are in the launch
text box.** `LauncherState.spec` returns `(task, model, cwd, template)`; `app._launch`
hands `task` to `AgentSession`; `run` sends it as the lead's first user message and
`announce` puts it on a card. `AgentRecord` stores it as `task: str`. Sub-agents do not
share conversation history and `initialPrompt` is unset, so the only paths from those
premises to a worker are the lead re-expressing them in a spawn prompt or in a
`Task.detail`.

**The gap is not that workers cannot read. It is that there is nothing to read.** That
inverts the fix: this is not a plumbing problem, and the plumbing half is already done.

---

## What changes

A session may carry a **brief**: a directory of ordered entries, on disk, outside the
working tree. The store holds its **path and never its content**.

```
~/.claude/projects/<cwd-slug>/briefs/<session-id>/
  000-premises.md
  001-amendment.md
```

Three properties, each load-bearing:

**One home.** `an-operator-instruction-the-lead-cannot-see.md` refuses "adding a CC of
operator prompts to the lead" on `STYLE.md` §1 grounds — a stored duplicate kept true
by whoever remembers to update it, named as this codebase's historic defect shape.
Pasting premises into every worker's prompt is that shape exactly. A path is a pointer;
a copy is a copy. **The store must never hold the content, or this record has proposed
the defect it cites.**

**Outside the working tree, because that is where this project's durable state already
is.** The 08-14 record measured it: the CLI writes a JSONL transcript per session under
`~/.claude/projects/<cwd-slug>/` whether we ask or not — 149 files for this repository.
The brief belongs beside the transcripts it explains. Putting it *in* the tree would
mean every launch dirties the working tree, straight into item 4's untracked-file trap
and the "stage a new file immediately" rule that exists because of it.

**Optional, and `task: str` stays.** Most sessions are `solo` with a one-line task, and
`LauncherState` is a modal with one text box. A brief is the shape a *team* needs;
making it mandatory taxes the common case for a problem the common case does not have.

---

## Mutability: append-only, and the rule already says so

The 08-14 record states the rule for any store this project adds: *"persist the intent
stream, derive `UsageRollup`, `needs_you` and board state from it on load."* That is
`STYLE.md` §1's *derive; do not store* applied across a restart boundary. Applied to
the brief it answers mutability without a new decision — **persist amendments, derive
the current brief.**

Three arguments converge on the same shape.

**Free mutation destroys the reason for putting it on disk.** Forensics answers *what
did this worker act on*. A file anyone can rewrite answers *what did the file end up
saying*. Those differ exactly in the session that went wrong.

**An append log is a revision that moves.** Item 1 refused content-hashing findings
against the tree, and the reasoning was specific: nine agents edit one uncommitted
tree, `HEAD` does not move all session, so two findings hours apart carry identical
shas. That objection is about the *tree* having no clock. A log we own has one —
position in it is the version, monotonic and free. The staleness answer item 1 priced
and rejected is available here for nothing, and importing none of the machinery it
rejected.

**The codebase already runs this shape.** `Transcript` is append-only and `STYLE.md`
lists it as a deliberate imperative exception, with the rationale attached: a reader
renders a consistent prefix while the writer keeps appending.

### The write primitive is constrained, and rules out one file

08-14 is explicit: `Session::save_to_path` rewriting a whole document per turn is *"not
a slow path, it is a corruption path"* under concurrency, and `settings.save`'s temp
file plus `os.replace` is named as the right primitive.

That also rules out appending to one growing file. A write above `PIPE_BUF` is not
atomic and a reader can catch a partial record. **One file per entry, each written temp
+ `os.replace`**: no locking, no partial reads, no O(n²). It reproduces `Transcript`'s
consistent-prefix property using directory listing where `Transcript` uses
`published_length`.

---

## Amendments may contradict settled premises

**Decided: yes.** A design that forbids contradiction freezes premises at the moment of
least information, which for a dogfooding project is the moment they are written. The
cost of boxing in a pivot exceeds the cost of a wrong amendment, and — the argument
that actually carries it — **the failure mode is observable, so the decision is
testable rather than permanent.** Brief log plus worker transcript answers "worker W
read at T, premise P was superseded at T+n, W's output assumes P" after the fact.

It concentrates authority, and that is the honest cost: a wrong amendment reaches every
worker that reads after it, and unlike a bad `Task.detail` there is no single owner to
refuse it. Four obligations follow, and they are not optional.

**1. Supersession must be visible in what the worker reads.** If derivation renders
only the current state, a superseded premise silently disappears and the log exists but
nobody reading can tell. That is item 8's `Concern.edited` shape — *a fact about the
message that exists nowhere in the message*. The derived brief renders
"X — superseded by 003", not X's absence.

**2. An amendment may contradict; it may not carry a plan.** The refusal in
`a-task-reaches-the-board-without-a-decision.md` stands: *"Work that arrives with a
remedy attached reads as already-decided and invites approval — and the plan is the
part that goes stale."* "The premise that the board is per-session was wrong" is an
amendment. "…so make it global and move `board_tasks` down" is the refused shape. This
line is narrower than the contradiction question and is the one that binds.

**3. An amendment must not read as an order.** The only check on a wrong amendment is
the mechanism `what-the-board-does-not-carry.md` is organised around: workers arguing
from evidence, which it credits with saving the run. Frame an amendment the way
`templates.FEATURE`'s builder prompt already frames a reviewer's concern — *"a finding
to be confirmed or refuted, not an order"* — and reuse that wording rather than
inventing a second register. Presenting amendments as final suppresses the thing that
catches them.

**4. The directory is the unit of reading.** Append-only plus contradiction means a
superseded original is still on disk and still readable. A worker told to "read the
brief" that opens a file named `brief.md` acts on the version that was overturned.
Hence `000-premises.md` and no file called `brief.md` — the footgun only surfaces in a
session that has already gone wrong.

---

## What a brief holds

Enough structure that the distinction the whole thing rests on is legible, and no more.

- **Settled**, with its reasoning. A premise with no reason attached cannot be argued
  with on evidence, only deferred to.
- **Open** — questions the session is expected to answer.
- **Out of scope.**

Not the plan (obligation 2). Not a task list — that is the board, and
`declare_task` already owns it.

**The operator writes it.** `bus.py`'s argument that a field a model fills is a field
the model *chose* does not bind here, because the filler is a person; but the inverse
is why generating a brief from the task string would be wrong, and it is the automated
generation this project exists to avoid.

---

## Why this bears on the other two reports

**Board awareness.** The measurement makes the brief reachable with no new bus tool,
which decouples it from the read primitive report 2 needs. They are no longer
prerequisites for each other, and can be sequenced independently.

**In-flight re-verification.** A brief is the one place a verification policy can live
legitimately. "Verify less" in `lead_briefing` is a restraint instruction competing
against a monotonically pro-rigor `CLAUDE.md`, and
`a-task-reaches-the-board-without-a-decision.md` predicts it loses by construction. But
*"the gate runs once, at the end, owned by the terminal task"* written into a session's
brief is a scoping fact about this work, not a plea for restraint. Same words,
different authority, and it escapes the recorded objection — which does **not**
generalise to all prose, only to restraint.

**The same-day conflict may dissolve.** `what-the-board-does-not-carry.md` makes "the
lead declares a task from `planning/`" its item 0; its sibling argues that is the
backlog burst it warns against, and flags the disagreement as unresolved. If premises
arrive in a brief at launch, that mechanism stops being load-bearing. Dissolving the
conflict is a better outcome than picking a winner, and this record does not pick one.

---

## Not doing

- **Content in the store.** A path is a pointer; content beside the file is the second
  copy `STYLE.md` §1 names. The UI reads and derives at the boundary, as
  `approval.render_diff` already reads a file to build a diff.
- **The brief in the working tree.** Every launch would dirty the tree — item 4's trap,
  and the reason the "stage it immediately" rule exists.
- **One growing file.** Not atomic above `PIPE_BUF`; readers catch partial records.
- **Rewriting a document per amendment.** 08-14 names it a corruption path under
  concurrency, not a slow one.
- **A mandatory brief.** Taxes the `solo` common case for a team problem.
- **Generating a brief from the task string.** The automated generation this project
  is organised against.
- **`AgentDefinition.memory`.** Measured as not the lever; the canary arrives without
  it. Setting it would be a change with no observed effect attached.
- **Reusing `deliverable`.** It already means a finished session's *output* — the
  opposite thing. A brief needs its own word.

---

## Open

- **Whether a worker can read an absolute path outside `cwd`.** `approval.classify`
  auto-approves `Read` regardless of path, but whether the CLI imposes its own
  cwd-relative restriction is **not measured, and the whole design rests on it.** If it
  refuses, the brief has to live in the tree after all and every trade above is
  re-opened. This is one question added to an existing probe.
- **Whether an amendment must name what it supersedes.** Requiring the link makes
  derivation exact and adds ceremony an operator writing at speed will skip — and a
  missing link then reads as pure addition, which is silently wrong. Optional link plus
  strict chronological rendering is honest and cheap. The first few real amendments
  will answer this better than argument will.
- **What makes a worker read it a second time.** The sharpest unresolved item.
  Item 2's defect is not that `Task.detail` is immutable — it is that the worker holds
  a transcript copy nothing can invalidate, and a file moves that copy from the claim
  reply into the `Read` result rather than removing it. **A live brief nobody re-reads
  is a frozen brief with extra steps.** Candidates: prose in `worker_prompt` (which is
  *pro-rigor* and so does not meet the recorded objection to restraint prose); the lead
  posting a concern on amendment (existing bus, no new mechanism, but item 9 is exactly
  that the lead is the participant nothing checks); the gate's
  `permissionDecisionReason`, which is the only channel guaranteed to reach a live
  worker and is adversarial by design.
- **What the card shows.** `AgentSpawned(task=...)` feeds the tree row, so a brief needs
  a short title or the row renders a paragraph.
- **Where derivation lives.** Reading is IO and belongs in the shell; the derived brief
  is a projection and must not be persisted, or *derive; do not store* is violated on
  the way in.

---

## In order, if any of this gets done

0. **Measure the absolute-path read.** One question on
   `verify_worker_context.py`. Everything below is void if it fails, and it is the
   cheapest thing here by a wide margin.
1. **The launch spec becomes optional and structured** — a path beside `task: str`,
   `LauncherState.spec` widened, `AgentRecord` carrying the path and a title. No
   reading yet, no amendments: the smallest change that makes premises addressable.
2. **Write and render.** An entry writer using `settings.save`'s temp + `os.replace`
   primitive, and a pane that derives the current brief from the directory and shows
   supersession (obligation 1).
3. **Tell workers it exists**, with the path, and the "confirm or refute, not an order"
   framing lifted from the builder role (obligations 2 and 3).
4. **Amendments**, once there is evidence about the re-read trigger from steps 1–3
   rather than a guess about it.
