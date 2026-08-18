# The board is a tenant of a pane that owes it nothing

**Dated:** 2026-08-18 · **Status:** open, not started · **Found by:** the operator, while
rows 1–2 of [`2026-08-17-a-team-cannot-read-its-own-board.md`](2026-08-17-a-team-cannot-read-its-own-board.md)
were landing · **Related:**
[`2026-08-13-an-expansion-outgrows-its-pane.md`](2026-08-13-an-expansion-outgrows-its-pane.md)
(the same shape, one pane over),
[`2026-08-12-the-board-has-no-surface.md`](2026-08-12-the-board-has-no-surface.md)
(why it went into DETAIL in the first place)

Symbol names, not line numbers, per the 08-14 convention.

**Parked deliberately.** Rows 3 and 4 of the gathering record are next, and both add to
what a board row carries. Building a pane first would mean designing a surface for data
that is still moving. This exists so the observation is not lost, and so the rows that
land in the meantime can be checked against it.

---

## The observation

`detail._board` is drawn on **both** branches of `detail.draw` — the obligation branch and
the no-obligation branch — and its own docstring argues correctly for why. The board is
what tells an operator whether the message they are being asked to approve makes sense, so
hiding it when something is waiting would hide it at the moment it matters most.

That argument is sound and it is what makes the pane crowded. DETAIL's contract is *a
wider rendering of the row under the cursor*, and the board is not that. It is the
session's shared state, and it happens to be rendered by whichever pane had room.

Concretely, DETAIL currently carries, on one branch: the obligation's identity, its diff or
its question or its error, **and** two bounded tables (`_MAX_BOARD_TASKS = 60`,
`_MAX_BOARD_CONCERNS = 40`) that answer a different question about a different subject.
The bounds are the tell. A pane that has to cap its own content at sixty rows and print
*"… N more task(s) not shown"* is a pane hosting something that wanted its own scroll.

## Why now rather than earlier

Two things changed under it.

**The board acquired a second reader.** `read_board` answers a worker from
`board.board_tasks` — the same projection DETAIL draws. The board is no longer a view the
operator happens to get; it is the team's shared state, with the operator as one of its
readers. A surface that exists as a lodger in the pane for something else undersells what
it now is.

**Rows 3 and 4 both add to a row.** `detail` on `BoardTask`, `task_id` on `Concern` (so
"claimed, and there is an open concern about it" derives), and `Task.touches` with its
auto-dependencies. Every one of those is more per row, in a table already capped at sixty
and already sharing a pane.

## The operator's constraint

**Tabbed with DETAIL, for inspection.** Not a fourth split.

That is the right instinct for the same reason DETAIL is already a tab-mate of CONTEXT
rather than a split: the 0.32 `ContextSpace` width in TRIAGE was chosen so the inbox keeps
room for identity, wait and summary, and a fourth column would take it back. It also makes
the pane free to be *absent* in the common case, which matters because most sessions are
solo — `has_board` is already the rule for that and would carry over unchanged.

Worth noting where it would sit in each layout, because the two arrangements answer
different questions and DETAIL is deliberately ordered differently in each:

- **TRIAGE** — `ContextSpace`, behind DETAIL. The queue is what the operator is here for;
  the board is context for a decision, not the decision.
- **FOCUS** — `HealthSpace`. FOCUS is for steering one session, and a team's board is a
  fact about that session in the same class as its cost and its context headroom.

## What is already true, and cheap

- The projection is pure and already outside `ui/` (`board.board_tasks`,
  `board.board_concerns`, `board.has_board`, `board.role_name`). A pane is a renderer over
  values it does not compute.
- `bound_rows` and `_task_row` exist and are reusable.
- Absence for a solo session is a solved question (`has_board`), and the reasoning for
  reading it from the launched template rather than from an empty board is recorded.

## What is not settled, and should not be settled here

**1. Whether DETAIL keeps a board summary.** Moving the tables out wholesale re-opens the
exact hazard `_board`'s docstring closes: an operator approving a message between two
agents with no view of the work either holds. A one-line summary on the obligation branch —
*"6 tasks · 2 claimed · 1 stranded"* — may be the honest split, with the full tables in the
new pane. That is a real design question and the answer decides whether this is a move or a
promotion.

**2. Whether the board pane may move the cursor.** `_board` currently makes no row
clickable, and its reason is specific: a click would flip `focus.obligation` from None to
non-None for a session with work waiting, so the board would vanish under the click that
selected it. A pane of its own does not automatically inherit that constraint — but the
single-cursor rule (`ui/focus.py`) does, and "click a task to focus its claimer" is exactly
the kind of second selection that rule exists to refuse. Wants an argument, not a default.

**3. Which session's board it shows.** DETAIL follows the cursor and cannot be pointed
elsewhere, for a reason the pane records. A board pane that followed the cursor would flip
between teams as the operator moves through the inbox; one that did not would be the
independently-selectable pane the layout deleted. Neither is obviously right and the
question is the same one that produced the current DETAIL.

**4. Whether the concern log belongs with it.** They are two tables under one heading today
because they arrived together. The task board is shared state with a tool behind it; the
concern log is an audit trail. A pane called BOARD may not owe the second one a home.

## Not doing

- **A fourth split.** The operator asked for a tab and the width argument in `_triage_layout`
  already settles it.
- **Making the board pane independently selectable.** That is the old DETAIL, and the old
  DETAIL is how an operator approved one agent's write while reading another agent's diff.
- **Building it before rows 3 and 4 land.** The rows change what a board row carries, and
  designing a surface around data that is still moving is how a pane gets built twice.
