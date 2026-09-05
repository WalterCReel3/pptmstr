# The gate's measurement outlives the approval that produced it

**Dated:** 2026-09-01 · **Status:** feature-team brief; scope is closed, ordering is
the priority; signed off to build 2026-09-01 ·
**Builds:** proposals 1 and 2 of
[`2026-08-21-the-board-takes-an-agents-word-for-what-it-will-touch.md`](2026-08-21-the-board-takes-an-agents-word-for-what-it-will-touch.md) ·
**Bears on:** [`../notes/2026-08-31-the-instrument-is-the-product.md`](../notes/2026-08-31-the-instrument-is-the-product.md)
§6.5.1–2, whose thesis this does **not** adopt

**Objective: produce the first quantity in this application that describes what an
agent did rather than what it spent.** `Task.touches` is a declaration nothing
compares to a write. `PendingApproval.diff` is a correct measurement that is deleted
a few hundred lines after it is computed. This brief keeps the second and compares it
to the first.

**Observe only. Nothing is denied, nothing is blocked, no behaviour changes for any
agent.** Divergence is recorded and surfaced. Whether it should ever refuse is a
separate decision that the observation period exists to inform, and it is not in this
brief.

---

## D1 — The counts are computed in the reducer, not carried in on an `Effect`

The 08-21 record left this open — an `Effect`, a widened return, or a side ledger —
and leaned on `STYLE.md` §1's *when the core must answer, widen the return*. **On
reading `effects.py`, the `Effect` union is the wrong home, and the reason is in its
own docstring.**

An `Effect` exists to answer an agent parked on a future: *"the app loop hands each
effect to the `Bridge`, which completes the waiting future."* Every member carries a
`request_id` for exactly that. A retained-counts member answers nobody, completes no
future, and would put a member in that union whose invariant is that it does not hold.

The narrower finding is that **nothing needs to be carried in at all.** At
`store.py:389` the reducer has already looked the resolved record up and holds it:

```python
if rec is None or rec.pending_by_id(intent.pending_id) is None:
```

That record carries `tool_name`, `raw_args` and `diff` (`model.py:266-283`). The
`ApprovalResolved` intent carries `node_id` and `approved`. `Task.claimed_by`
(`model.py:752`) maps the node to the task holding it. So the file path, the line
counts and the owning task are all in hand at the one instant they are all in hand,
and the accumulation is a pure function of the snapshot and the intent — no clock, no
IO, nothing added to the shell.

This is why the deletion is the whole defect and the plumbing is not. `render_diff`
reads the file from disk at park time (`approval.py:193`), so the "before" state exists
only inside the parked record; once the write lands it is unrecoverable from any later
snapshot. Retention here is not *derive; do not store* being violated — there is no
snapshot the counts follow from afterwards.

**The counter is pure and lives in `model.py` beside the value type.** Not in
`approval.py`: `store.py` imports `board`, `effects`, `intents` and `model` and does not
import the shell module that reads disk, and this brief does not make it.

## D2 — Per-write and per-task are the same build, so the ordering question is moot

The 08-31 note left per-write versus per-task open. Given D1 it collapses: the counts
arrive per resolved approval and the task is reachable through `claimed_by` in the same
arm, so accumulation is where they land rather than a second mechanism. Per-write is what
is measured; per-task is what is stored.

**A write by a node holding no task accumulates nowhere and is not an error.** A lead
that edits a file outside any claimed task is ordinary, and this brief records nothing
about it rather than inventing a task to hang it on.

## The open question this brief cannot answer alone

**`ApprovalResolved.edited_args` makes the retained diff stale.** An operator who edits a
parked call before approving it resolves an approval whose `diff` was rendered from the
*original* arguments. The counts would then describe a write that never happened.

Three options, and the operator picks:

1. **Record as-reviewed and say so.** Cheapest, and honest if the field name carries it.
   The counts mean "what the operator approved", which diverges from disk exactly when
   they edited.
2. **Recompute in the shell on the edited path** and put the fresh counts on the intent.
   Correct, and it is the one case that genuinely needs a widened intent — but the "before"
   file may already have moved between park and resolution, so this is not free either.
3. **Record nothing for edited approvals.** No wrong number, a hole in the series.

**Signed: option 1.** An edit is the rarest path, and a documented approximation beats a
second disk read on the resolve path — where the "before" file may have moved between park
and resolution anyway, so option 2 buys correctness it cannot guarantee.

This is the decision in this brief most likely to be wrong, and it was deliberately made
cheap to reverse: **one test and one docstring.** The test named in Item 1 pins the
behaviour explicitly rather than letting it fall out of the implementation, so the
approximation is visible to a reader who did not read this file. If the counts turn out to
mislead on edited approvals under real data, option 2 becomes a widened intent and nothing
above it changes.

## What the counts are, and what they are not

They are **approved writes, not landed writes.** The gate sees a call it permitted; the
tool can still fail afterwards. This is the gate's view by construction and the field
docstrings must say so, because a reader who believes these are filesystem facts will
eventually find a divergence that is a failed tool call.

`approved=False` produces no counts. Nothing was written.

---

## Amendment, 2026-09-02 — three of the decisions above were wrong

Review found six defects in this brief between its signing and the first build. Each was
re-checked against the source by the lead before being acted on. The items below are
amended; the sections above are left as written so the reversal is readable rather than
invisible, which is this repository's convention for a record that was built on.

**D1's task selection was wrong.** `Task.claimed_by` was cited as a function from node to
task. It is a relation: `TaskCompleted` (`store.py:587-589`) sets state and `completed_at`
and deliberately does not clear `claimed_by` — `BoardTask.owner_gone`'s comment records
that as intentional — and only `TaskReleased` clears it. Since `tasks` is a dict in
declaration order, a first-match lookup returns the node's *first-ever* task, completed,
forever. No concurrency is needed to reach this; it is the ordinary path.

The rule is now: filter to `claimed_by == node AND state is CLAIMED`; exactly one match
accumulates; zero accumulates nowhere; **more than one accumulates nowhere and increments
an ambiguity counter.** Guessing by declaration order would pollute the one series this
work exists to produce, and `claimed_at` is not added speculatively — how often the
ambiguity fires is itself a reading worth taking, since "one task at a time" is prose in
`templates.py` enforced by nothing.

There is a second, narrower window: `complete_task` and `release_task` are in `_BUS_AUTO`
and never park, while the CLI dispatches `PreToolUse` for a whole turn concurrently — a
measured fact recorded on `AgentRecord.pending`. So a turn containing
`[Write, complete_task]` completes the task while the write is still parked. Under the
CLAIMED filter that write records nothing rather than recording on a finished row, which
is the better failure and is now a written decision rather than a side effect. **The
counts are therefore a lower bound and the docstrings must say so.**

**D2's Item 1 / Item 2 boundary was in the wrong place.** Item 1 specified files-touched as
derivable from `PendingApproval.diff`. It is not. `render_diff` for MultiEdit never diffs
the file — it loops the edits calling `_unified(old, new, f"edit {i}")` (`approval.py:217-232`),
so a four-edit MultiEdit of one file yields four headers named `edit 1`…`edit 4`, which are
not paths. `Edit`'s fallback can label a diff `(anchor not found in file)` (`:215`).
`NotebookEdit` is in `_REVIEW`, carries `notebook_path` rather than `file_path`, and falls
through to `return None`. A no-op `Write` returns `""` rather than `None`.

Paths therefore come from `raw_args`/`edited_args`; the diff carries lines only. The
accumulator is **a set of distinct normalised paths plus two integers**, decided in Item 1
so Item 2 does not rewrite the value type and every test against it. The set is also what
stops the artefact sentence lying: an integer per approval would report four edits to one
file as four files, and *"declared 3 files, wrote 11"* would mean eleven writes while
reading as eleven files.

**D3 was one decision and is two.** Option 1 stands for the **line counts** — the diff
cannot be recomputed in a pure reducer. It does not transfer to the **path**, which must be
read from `edited_args` when it differs: `driver._park`'s comment names correcting a wrong
path as the first reason edit-then-approve exists, so the single edit the feature was
designed for is the one that would name the wrong file in the artefact. "Edited" is
`edited_args != raw_args`, not `is not None` — `ui/review.handle_keys` sends the
round-tripped original when the operator opens the editor and changes nothing.

**Item 2 as written would have reported every write as divergent.** The declaration side is
repository-relative by construction (`normalised_touches`' docstring, `bus.declare_task`'s
schema, `templates.py`); the write side is not, and `_unified`'s absolute-path branch at
`approval.py:252` exists because absolute is the expected shape. "Compare using the same
normalisation" is not the same footing. The reducer bridges it through `AgentRecord.cwd`
by string prefix, purely — and where `cwd` is `None` or the path is not under it, the write
is recorded as **unattributable, a third outcome, never as a divergence.** A units mismatch
reported as an agent exceeding its scope would have made the observation period measure
nothing but itself.

**One finding was outside this work and is boarded separately.** `diff_line_kind`
(`approval.py:271-276`) tests `startswith("---")` before `startswith("-")`, so a deleted
line whose content is `---` — every horizontal rule in every markdown file here — renders
as a header rather than a removal in the review pane. Live at HEAD, colour only, unrelated
to this brief except that it was found while reviewing it. It is **not** merged into the
counter: the counter is stateful over `@@` hunks and the classifier is per-line and
stateless, so they are different functions rather than one duplicated.

**What is still unmeasured.** No `raw_args` has been captured from a live gate, so "the
write path arrives absolute" is inference from that `_unified` branch and from the file
tools' contract. `write-path-shape` is boarded to settle it. It does not block Item 2,
which is specified to handle both shapes and is correct under either answer.

---

## Item 1 — The resolved approval leaves its measurement behind

**Why:** the sharpest loss in the 08-21 inventory, and the prerequisite for everything
after it. Prerequisite for Items 2 and 3.

**Scope (touches):** `pptmstr/model.py` (a frozen counts value; a pure
unified-diff counter; the accumulator field on `Task`), `pptmstr/store.py` (the
`ApprovalResolved` arm at `:387-393` only), `tests/test_store.py`.

**The build:**
- A frozen value carrying files touched, lines added, lines removed.
- A pure function from `PendingApproval.diff` to that value. It parses the unified diff
  already in hand; it does not read disk and does not take a clock.
- Accumulated onto the `Task` the resolving node holds via `claimed_by`. No task, no
  accumulation, no error.
- Docstrings state the two live constraints: these are approved writes rather than landed
  ones, and the retention exists because the "before" state is gone after the write.

**Done when:** a test resolves an approval and asserts the counts land on the claiming
task; a test asserts a resolution by a node holding no task changes nothing and raises
nothing; a test asserts `approved=False` accumulates nothing; a test pins the
`edited_args` behaviour to whichever of the three options is signed off, naming the
constraint rather than the history; `make check` green.

**Not in this item:** any UI, any comparison against `touches`, any persistence beyond the
snapshot, token spend, transcripts.

## Item 2 — The board compares the declaration to the write

**Why:** the mechanism native to this design. `depends_on` binds because the board
computes over it; `touches` feeds that one computation and is consulted nowhere else.

**Depends on:** Item 1. Same file, same reducer arm — this is a sequence, not two
parallel tasks, and the board will serialise it on `touches` regardless.

**Scope (touches):** `pptmstr/model.py` (the divergence value and a pure predicate over
normalised paths), `pptmstr/store.py` (same arm), `tests/test_store.py`.

**The build:**
- Extract the written path from the resolved record's `raw_args` for the file-mutating
  tools. A tool with no path — `Bash`, the network tools — produces no path and no
  divergence claim. **It is not evidence of compliance and the docstring says so**; a
  `Bash` heredoc writing a file is invisible to this and always will be.
- Compare against the claiming task's `touches` using `normalised_touches`' own
  normalisation (`model.py:687`), so a declaration and a write are compared on the same
  footing.
- Record the divergence. Deny nothing, warn no agent, change no `permissionDecisionReason`.

**Done when:** a test declares `touches` and writes inside it and asserts no divergence; a
test writes outside it and asserts the divergence is recorded with the path; a test asserts
a `Bash` resolution records neither divergence nor compliance; a test asserts a write by a
node holding no task records nothing; `make check` green.

**Not in this item:** denying, any change to `classify` or the `_REVIEW` set, any prose
added to `templates.py`, granularity rules about tasks that declare a directory.

## Item 3 — The divergence is one line the operator can read

**Why:** recommendation 3 of the note, and the reason the data is worth having during a
run rather than only across runs. *"task-4 declared 3 files, wrote 11"* is the artefact.

**Depends on:** Item 2.

**Scope (touches):** `pptmstr/board.py` (the projection — the pane must not re-derive
what the board owns, per `BoardDelivered`'s reasoning), `pptmstr/ui/board_pane.py`,
`pptmstr/theme.py` **only if** a new palette role is needed — a missing per-state entry is
a `KeyError` inside a draw call, so if a role is added, every table gets it;
`tests/test_board.py`, `tests/test_board_pane.py`.

**Done when:** a task whose writes stayed inside its declaration draws no divergence
affordance at all; a task that diverged draws the counts and the count of out-of-scope
paths; the projection is tested independently of the pane; `make check` green.

**Not in this item:** a divergence pane of its own, history across sessions, sorting or
filtering the board by divergence, anything in `_needs_you`.

## Item 4 — The gate is green after the writes stop

**Depends on:** Items 1, 2 and 3.

**Scope (touches):** nothing. `make check` and a report.

**Done when:** `make check` is green at a tree nobody is writing, and the result names
what ran. A red result that belongs to a file no item above declared is reported, not
fixed.

---

## Deferred, explicitly — do not pick these up

- **Denying on divergence.** The observation period is what decides it. A false positive
  is indistinguishable, from inside the agent, from a broken tool.
- **The thesis change** of the 08-31 note §6.4, and the rename in §6.9. Three of that
  note's four falsification tests need this instrument to run; it is not evidence for
  itself.
- **The plan-unit gate / reverting `c9ab068`.** §6.5.7's observation is real — the
  operator wrote a signed decomposition as a document because the board has no type for
  one, and this file is the third instance of that. It stays a design claim until there
  are readings.
- **`AgentDefinition.effort`** (08-21 proposal 3) and the **system-prompt A/B**
  (proposal 4). The second is worthless without Items 1 and 2 and says so.
- **Shrinking `templates.py`.** Follows from types that do not exist yet.
- Persisting anything to disk, token spend per task, transcript retention.

## House rules that bind this work

`STYLE.md` before writing. Comments state live constraints, never history — an item here
reverses a recommendation two records made, and the *code* records the constraint that
holds while *this file* keeps the argument. Docstring triple quotes on their own line.
`make check` is part of the build. Commit messages are declarative sentences arguing why,
with no attribution trailers of any kind. Declare `touches` on every board task.

## Verification boundary

**Run:** `make check` at `07ef797` — black over 101 files, mypy over 41, 1257 tests, green.
That is the tree this brief starts from and says nothing about anything proposed in it.

**Read this session, and load-bearing above:** `store.py:387-411` (the resolved record is
in hand before it is dropped), `model.py:256-283` (`PendingApproval` carries `raw_args` and
`diff`), `model.py:717-760` (`Task.claimed_by`, and `touches`' own comment calling it *"the
files this task will write"*), `intents.py:138-148` (`ApprovalResolved` carries
`edited_args`), `effects.py:1-34` and `:137-140` (every `Effect` answers a parked future),
`approval.py:52-99` (`declare_task` is in `_REVIEW`), `store.py:679-691` (`touches` is
consulted only by `_auto_depends`).

**Read, not run:** `approval.py:184-195` — that `render_diff` reads the current file from
disk is taken from its docstring, not from an execution.

**Not checked:** the 08-31 note's `[C]` citations and its METR figures, which need external
sources; and whether `STYLE.md` §1 permits the exception argued in its §6.5.1 — D1 above
argues the exception from the deletion rather than from that rule, so this brief does not
depend on the answer.
