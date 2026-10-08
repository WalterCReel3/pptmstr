# A board row ends where its reader cannot follow

**Dated:** 2026-10-08 · **Status:** built, not run live · **Found by:** a team session
whose lead and three workers each hit it, and the operator's statement that it is not
new · **Supersedes nothing** —
[`2026-08-17-a-team-cannot-read-its-own-board.md`](2026-08-17-a-team-cannot-read-its-own-board.md)
stands, and its question 2 is the decision this record adds to rather than reverses.

Symbol names, not line numbers.

---

## What was observed

`read_board` bounds a row's specification at `_MAX_DETAIL_CHARS` (2,000), a note's body
at `_MAX_CONCERN_CHARS` (1,200), and the whole reply at `_MAX_BOARD_CHARS` (30,000).
Each bound announces itself. The specification's announcement ended **"ask the lead for
the rest"**, the note's ended on the count and nothing else, and the whole-board notice
said `claim_task` returns a specification in full "and the agent holding one can be
asked for the rest".

So the only reader ever handed a long specification whole was the one that claimed it.
The session that produced this record reported three readers who needed another task's
specification and could not get it from the board:

- a foundation builder told to design for its consumers, whose tasks it did not hold;
- a reviewer auditing the plan, which holds no task at all;
- a record-writer.

Each asked the lead, and the lead re-sent text from its own context. Those three are
the lead's account, carried in the specification of the task that built this; they were
not re-observed here. One instance was: the builder of this change was launched with a
note that its own specification would arrive whole from `claim_task` and would have been
cut by `read_board`, which is the defect described from the inside.

The operator, 2026-10-08: *"Board truncation is a huge problem that's been plaguing
other efforts. We need to prioritize a fix."* No other effort's transcript was read for
this record, so "plaguing other efforts" rests on that statement.

**The notice was the defect, more than the bound.** A bound that says where the rest is
costs a reader one call. A bound that names the lead costs a concern, an approval under
`STRICT`, the lead's turn, and a second copy of the specification — the lead's
transcript copy, which `templates.lead_briefing` already tells the lead "goes stale and
the board does not".

---

## Why the bounds stay

Not re-argued, because the argument is measured and is in the code that holds it
(`_MAX_BOARD_CHARS`'s comment, `_board_text`'s docstring): twelve rows bounded
per-row came to 50.8 KB in one read and 62.8 KB in another, past the tool's output cap,
so the agent told the board is authoritative could not read it at all. Raising
`_MAX_DETAIL_CHARS` buys back the overflow at a smaller task count. Rows are never
dropped; the prose is what degrades.

That leaves one shape: the listing stays cheap and the rest is **pulled**, one task at
a time, by whoever needs it.

---

## The decision

**A seventh bus tool, `read_task(task_id, part?)`**, returns one task with nothing cut:
the same head line the board row opens on (`_row_head` — state, owner, blockers,
never-declared dependencies, files), the whole specification, and every note on it with
its sender, subject and whole body. Any agent on the board may read any task on it, and
reading claims nothing.

**Every cut on the board now points at it, by id.** A cut specification ends
`... N more character(s): read_task("t3") has all of it`; a cut note,
`... has the whole note`; the whole-board notice names `read_task(task_id)` in place of
`claim_task` and "ask the agent holding it". Only a row that was actually cut carries
the pointer.

### A second tool, not an argument on `read_board`

The lead proposed the tool and invited the other shape. The argument was considered and
loses on one point: the whole-task read has to page (below), and `part` means nothing
without `task_id`. On `read_board` that is a rule between two optional arguments that a
JSON Schema of independent properties cannot state, enforced by a handler refusal —
where a separate tool makes `task_id` simply required. The cost of the second tool is
four name lists (`bus.BUS_TOOLS`, the server's own, `approval._BUS_AUTO`,
`templates.BUS_TOOL_NAMES`), and all four are already pinned against each other by
tests that fail when one drifts.

This is also the 08-17 record's question 2 arriving from the other side. Item 2 there
endorsed a narrow `task_detail(task_id)`; the operator chose the general read, "easier
to narrow later than to discover the narrow one was never enough." The general read was
the right first tool and its ceiling is what makes the narrow one necessary: they are
not alternatives, the second is how the first stays bounded.

### No new intent, and what that costs

`read_task` emits `BoardRead` and is answered by `BoardDelivered`, exactly as
`read_board` is, and picks its row in the handler. `BoardDelivered`'s docstring already
puts the narrowing there: "which of them an agent is shown is the tool's decision, not
the store's". A `TaskRead` intent would be a second projection of one task, kept in
agreement with `board.board_tasks` by nothing. The store, the reducer, the effect union
and the app's settle loop are untouched.

**The cost is one thing the specification asked for and this does not do.** The task
asked that an unknown id distinguish "no such task" from "declared, but on another
team's board". The handler is answered with its own session's rows and cannot tell
those apart, and the alternative — the store reporting that an id exists elsewhere —
tells one session what is on another's board, which is a read across the boundary
`Task.belongs_to` draws and the 08-18 answer ("session-scoped, without qualification")
closed. So `_no_such_task_text` distinguishes what it can — an empty board, a blank id,
an id that is not here — and for the last one lists the ids that *are* on the board
(the recovery for a misspelling) and says that an id from another session is not
reachable under any spelling (the recovery for the other mistake, which is to stop).
STYLE.md §3's smell is an error that leaves the reader to retry the same wrong thing;
this names both mistakes and both fixes without claiming to know which was made. If
that is judged not enough, the fix is an effect field and belongs with the store, not
in `bus.py`.

### The whole-task read has a ceiling too, and pages rather than cuts

`declare_task` puts no bound on `detail`, and nothing bounds how many notes a task
collects. A whole-task reply with no ceiling moves the unreadable reply from the
longest board to the longest specification.

What is recorded about the cap is two overflows and no ceiling: 50.8 KB and 62.8 KB
both failed, 30,000 characters was set beneath both as a judgement, and
`_MAX_BOARD_CHARS`'s comment says in terms that the real number is the harness's and
unknown here. Nothing measured since changes that, so `_MAX_TASK_CHARS` **is**
`_MAX_BOARD_CHARS` — the same evidence about the same thing, a reply — rather than a
second guessed number.

Past it the task is paged, since being whole is the tool's only job:

- every part opens with `Task t1, part 2 of 4: characters 29785-59568 of 108882.` and
  the literal next call, `read_task("t1", part=3)`; the last says it is the last;
- the parts laid end to end are the text exactly, nothing dropped or repeated at a
  seam, and each whole reply — head included — is within the ceiling;
- the seam is a character count, not a paragraph break, and says so. A seam chosen by
  content is one two reads of an amended text would choose differently;
- a part that does not exist is refused with the count, never clamped to the nearest.

A task that fits is returned bare, with no part head — which is every task this session
had. For scale: one task from the crowded-board fixture (a 4,200-character
specification and two 2,000-character notes) is 8,448 characters whole.

### What the board pays

The pointer is longer than the text it replaces and is charged to the row it appears
on: 2 characters more than "ask the lead" on a cut specification plus the id, and 36
plus the id on a cut note, which previously carried no pointer. On a board over the
whole-reply ceiling those are part of the structural floor, so each row's share of
prose shrinks by that much. Measured on the fixtures: twelve tasks with two notes each
have a floor of 5,028 characters and a share of 2,056 per row; forty tasks with seven
notes have a floor of 41,022, already over the ceiling, and a share of 0 — that board
was over the ceiling before this change as well and is the case `_board_text` documents
as unsolvable by trimming.

### The other lists

- **Stamped.** `read_task` is in `BUS_TOOLS`, so the gate stamps it; unstamped, the
  handler raises `UnstampedCall` before emitting anything.
- **Auto-approved.** In `approval._BUS_AUTO`. It returns a specification reviewed at
  declaration and notes reviewed at the send, and nothing else; a read that parked
  would put the operator back in the relay this removes the lead from.
- **Optional argument.** `part` is declared through `bus._schema` with
  `required=("task_id",)`, per
  [`archive/2026-08-13-every-bus-tool-requires-its-optional-arguments.md`](archive/2026-08-13-every-bus-tool-requires-its-optional-arguments.md):
  the `{name: type}` shorthand would have marked it required and refused the call the
  description documents.
- **Prompts.** `worker_prompt` and `lead_briefing` each say what the tool is *for* —
  a row that was cut, a task the reader does not hold — and that the lead is not the
  route. The lead's half matters as much: a lead asked for the rest of a specification
  will re-send it unless it knows the worker could have read it.

---

## Verification boundary

**Run:** `ruff check` and `black --check` over `pptmstr/` and `tests/`, `mypy pptmstr`,
and the full suite (3307 passed, 3 xfailed), on the branch this was built on rather than
on `main`. The new tests drive the real MCP
server through `tests/test_bus.py`'s `_Bus`, which meets the same `jsonschema`
validation a live call does, and assert on the text an agent would read.

**Failed first:** every new test was written and run against the unchanged
implementation before any of it existed — 24 failures across `tests/test_bus.py` and
`tests/test_approval.py`, including the three existing pins extended to name the tool.

**Mutation-tested against the current line:** 32 mutations, each one edit to the
shipped code, run in a scratch copy of the tree — the specification or a note clipped
inside `read_task`; either notice reverted; the wrong task returned; a seam overlapping
or dropping a character; the head not charged to the ceiling; no ceiling; a part
clamped; the tool missing from each of the three name lists; the sender defaulted; each
branch of the refusal flattened; `part` made required and `task_id` made optional; mail
shown as notes; the prompt paragraphs removed. 32 of 32 caught.

**Not verified, and it is the claim that matters.** Nothing here was run as a live team
session. The board this session ran on is the application from `main`, not this tree, so
no agent in it has ever been offered `read_task`. Unmeasured, in order of how much
rests on each:

1. **That an agent meeting the notice calls the tool.** The whole fix assumes it.
   STYLE.md §3 records a tool that was listed and used zero times; the notice and the
   prompts are written against that, and writing against it is not evidence.
2. **That the CLI advertises a seventh tool and delivers an integer `part`** through
   the stamp's JSON round trip. The first six do, and the schema path is the one
   `claim_task` uses; this is inference from them.
3. **That 30,000 characters is under the cap for this reply.** Inherited from the board
   read, where it has held; it was never measured as a ceiling, only as below two
   failures.
4. **What a reader does with a task amended between two parts.** The head carries the
   total so the change is visible. Whether an agent notices is not known, and no
   session has yet produced a task large enough to page.

**No probe was written.** `scripts/verify_bus_live.py` is the pattern — a `--team` run
with one specification past `_MAX_DETAIL_CHARS`, asserting from the `PreToolUse` wire
and the tool result, not from the agent's account, that a worker other than the claimer
called `read_task` and that its reply carried the tail of the specification. It is
cheap. It is not here because `scripts/` was outside the files this task declared and
two other builders were writing there; it is the first thing to do with this before
relying on it.

---

## Not doing

- **Raising `_MAX_DETAIL_CHARS` or `_MAX_CONCERN_CHARS`.** Re-creates the overflow.
- **Cross-session task lookup**, including the existence-only form. Above.
- **A `read_note(concern_id)`.** A note is read with its task; a row never has so many
  that paging the task is worse than a second tool, and concern ids appear nowhere an
  agent reads.
- **Marking the abandoned answer.** At shutdown `read_task` says the board is empty, as
  `read_board` does: the `Bridge.ask` fallback is indistinguishable from a real empty
  answer. Recorded as left undone in the 08-13 archive record, where the fix is said to
  belong in `effects.py`; this adds a second tool with the smell and no new argument
  about it.
