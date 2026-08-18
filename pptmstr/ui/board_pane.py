"""
BOARD: the team's shared state, on a surface that belongs to it.

**It used to be a lodger in DETAIL**, drawn on both branches of ``detail.draw``,
and the argument for putting it there was sound at the time: the operator is asked
to approve a message between two agents, and hiding the work either of them holds
would hide it at the moment it informs the act. What changed is not that argument
but what the board *is*. ``read_board`` gave it a second reader, so it stopped
being a view the operator happens to get and became the team's shared state with
the operator as one of its readers; and rows 3 and 4 of the gathering record put a
specification, a file list and a stalled row's reason on every line. A table that
caps itself at sixty rows and prints *"... N more"* inside a pane whose contract is
*a wider rendering of the row under the cursor* was hosting something that wanted
its own scroll.

**The board is no longer in DETAIL at all**, which is the operator's decision and
carries a cost worth stating plainly rather than discovering: BOARD is a tab-mate
of DETAIL, so during an approval the board is one click away and not on screen.
That is the hazard ``_board``'s old docstring argued against, now unmitigated. The
alternative on the table was a one-line summary left behind on the obligation
branch, and it was declined in favour of one pane owning the subject outright.

**No row moves the cursor.** The single-cursor rule in ``focus.py`` is not a DETAIL
implementation detail that a new pane escapes by being new -- "click a task to
focus its claimer" is precisely the second selection that rule exists to refuse,
and an independently-pointable pane here is the old DETAIL, which is how an
operator approved one agent's write while reading another agent's diff.

A disclosure triangle is not that click. It changes what this pane shows about a
row, not which row the application is pointed at, so what ``a`` would approve is
the same before and after it. Its open/closed state lives in ImGui's own per-id
storage rather than in ``AppState``: it is view state with no meaning outside the
frame, and nothing else needs to read it.

Absent, not empty, for a session that is not a team -- ``has_board`` reads the
launched template, so the pane does not appear mid-run on whichever event happened
to arrive first. A team whose board *is* empty says so instead: "no tasks declared
yet" is a lead that has not started, and nothing at all is a lead that is not going
to.

Bounded like every other pane here, and it announces every bound when it bites.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypeVar

from imgui_bundle import imgui

from ..board import (
    BoardConcern,
    BoardTask,
    board_concerns,
    board_tasks,
    has_board,
)
from ..model import ConcernId, ConcernState, Snapshot, TaskState
from ..theme import P
from .focus import FocusState
from .widgets import section

# Rows here are single-line and uniform, so these are about legibility rather than
# frame cost: a board past this size is not being read as a board. Generous, since
# the whole point of the move is that this pane has room.
_MAX_BOARD_TASKS = 120
_MAX_BOARD_CONCERNS = 80
# Dependency ids in one "blocked on" cell. A four-dependency task rendered as
# "blocked on t2 ..." is the same silent truncation as any other, in a smaller box.
_MAX_BLOCKED_IDS = 6
# Files in one row's "writes" line. Long lists are real -- a terminal gate task
# depends on every task that touches a file -- and this is the cell that would
# otherwise wrap into a paragraph.
_MAX_TOUCHES = 8
# The specification, which is prose written by a lead and has no natural bound.
_MAX_DETAIL_CHARS = 4000

_CONCERN_STATE_LABEL: dict[ConcernState, str] = {
    ConcernState.POSTED: "waiting",
    ConcernState.DELIVERED: "delivered",
    ConcernState.WITHDRAWN: "withdrawn",
}

# Only ever a row type from .board; named so bound_rows can serve both tables and
# the id lists without three copies of the same slice.
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class BoardCounts:
    """
    The shape of a board in six numbers.

    A count of rows is not what the summary is for. The line exists so an operator
    arriving at a sixty-row board learns, without reading it, whether anything on it
    needs them -- which is why ``stranded`` and ``undeclared`` are counted separately
    from the states and rendered in a different colour. Those two are the conditions
    nothing clears on its own.
    """

    total: int
    pending: int
    claimed: int
    completed: int
    # Still CLAIMED with an owner that has stopped. Nothing releases these.
    stranded: int
    # Rows waiting on an id that was never declared: a typo, unclaimable forever.
    undeclared: int


def draw(snap: Snapshot, focus: FocusState) -> None:
    """
    Render the board of the session under the cursor. Never reads the store.

    Follows the cursor exactly as DETAIL and CONTEXT do. It flips between teams as
    the operator moves through the inbox, which is the honest cost of not being a
    second selection: a pane that held still would need a pointer of its own, and
    that pointer is the thing the layout deleted.
    """
    node = focus.node(snap)
    if node is None:
        imgui.text_disabled("no session under the cursor")
        return

    session_id = node[0]
    if not has_board(snap, session_id):
        imgui.text_disabled("this session is not a team")
        return

    rows = board_tasks(snap, session_id)
    concerns = board_concerns(snap, session_id)
    _summary(counts(rows))

    if imgui.begin_child("##board"):
        imgui.push_text_wrap_pos(0.0)
        _tasks(rows, {c.id: c for c in concerns})
        _concerns(concerns)
        imgui.pop_text_wrap_pos()
    imgui.end_child()


def _summary(c: BoardCounts) -> None:
    """
    The shape of the board, above the scroll so it does not scroll away.

    Wrapped explicitly: this is drawn outside the child window that sets the wrap
    position for everything else, and this pane is narrow enough in both layouts
    that an unwrapped summary runs off the right edge rather than onto a second
    line.
    """
    if c.total == 0:
        imgui.text_colored(P.text_dim.vec4, "no tasks declared yet")
        return
    imgui.push_text_wrap_pos(0.0)
    imgui.text_colored(P.text_dim.vec4, summary_label(c))
    alarm = alarm_label(c)
    if alarm:
        imgui.text_colored(P.warn.vec4, alarm)
    imgui.pop_text_wrap_pos()


def _tasks(rows: Sequence[BoardTask], by_id: Mapping[ConcernId, BoardConcern]) -> None:
    shown, dropped = bound_rows(rows, _MAX_BOARD_TASKS)
    for row in shown:
        _task_row(row, by_id)
    if dropped:
        imgui.text_colored(P.text_dim.vec4, f"... {dropped} more task(s) not shown")


def _task_row(row: BoardTask, by_id: Mapping[ConcernId, BoardConcern]) -> None:
    """
    One task: a headline that is always visible, and the rest behind a triangle.

    What goes in the headline is what an operator scanning for trouble needs
    without opening anything -- who holds it, whether that owner has stopped, what
    it waits on, and whether somebody explained why it is not moving. The
    specification and the file list are what you open a row to read.
    """
    reasons = row_reasons(row, by_id)
    expandable = bool(row.detail or row.touches or reasons)

    flags = int(imgui.TreeNodeFlags_.span_avail_width)
    if not expandable:
        # A leaf draws no triangle and pushes nothing, so there is no tree_pop to
        # pair with it below.
        flags |= int(imgui.TreeNodeFlags_.leaf) | int(imgui.TreeNodeFlags_.no_tree_push_on_open)

    opened = imgui.tree_node_ex(f"{row.id}  {row.state.value}###{row.id}", flags)
    imgui.same_line()
    imgui.text_colored(P.text.vec4, row.title or "(untitled)")

    # The qualifiers go on their own line rather than trailing the title. Both
    # layouts dock this pane into a narrow space, and ``same_line`` after a wrapped
    # title puts the cursor wherever that title happened to end -- which, near the
    # right edge, is a column one character wide that renders the next string
    # vertically. A fresh line starts at the indent with the full width available.
    owner = owner_label(row)
    blocked = blocked_label(row)
    if owner or blocked or reasons:
        # An open non-leaf has already pushed a tree indent that everything after it
        # inherits; a closed row or a leaf has not. Indenting unconditionally would
        # therefore put the qualifiers one level deeper on exactly the rows whose
        # body is visible, so the same line moves as a row is opened.
        pushed = opened and expandable
        if not pushed:
            imgui.indent()
        if owner:
            # An owner that has stopped is the derived condition TaskState's
            # docstring promised a pane would carry: the row reads CLAIMED forever
            # otherwise.
            imgui.text_colored((P.warn if row.owner_gone else P.text_dim).vec4, owner)
        if blocked:
            if owner:
                imgui.same_line()
            imgui.text_colored((P.warn if row.missing else P.text_dim).vec4, blocked)
        if reasons:
            # Alongside the state rather than behind the triangle, because a row
            # that is not moving *for a recorded reason* is a different row from
            # one that is not moving silently, and that difference is the whole
            # point of the link.
            if owner or blocked:
                imgui.same_line()
            imgui.text_colored(P.accent.vec4, reasons_marker(reasons))
        if not pushed:
            imgui.unindent()

    if not (opened and expandable):
        return

    if row.touches:
        imgui.text_colored(P.text_dim.vec4, touches_label(row))
    for reason in reasons:
        imgui.text_colored(P.accent.vec4, f"reason: {reason}")
    if row.detail:
        body, dropped = clip(row.detail, _MAX_DETAIL_CHARS)
        imgui.text_colored(P.text.vec4, body)
        if dropped:
            imgui.text_colored(P.text_dim.vec4, f"... {dropped} more character(s) not shown")
    imgui.tree_pop()


def _concerns(rows: Sequence[BoardConcern]) -> None:
    if not rows:
        return
    shown, dropped = bound_rows(rows, _MAX_BOARD_CONCERNS, tail=True)
    section("concerns")
    for row in shown:
        # Participants and subject on separate lines, for the reason the task
        # qualifiers are: the subject is the long part, and anything placed after it
        # with ``same_line`` inherits whatever width the wrap left behind.
        imgui.text_colored(P.text_dim.vec4, concern_label(row))
        about = about_label(row)
        if about:
            imgui.same_line()
            imgui.text_colored((P.warn if row.task_missing else P.text_dim).vec4, about)
        imgui.indent()
        imgui.text_colored(P.text.vec4, row.subject or "(no subject)")
        imgui.unindent()
    if dropped:
        imgui.text_colored(P.text_dim.vec4, f"... {dropped} earlier concern(s) not shown")


# -- pure helpers, testable without a GL context -------------------------------


def counts(rows: Sequence[BoardTask]) -> BoardCounts:
    """
    Tally a board. Every row lands in exactly one state bucket.

    ``stranded`` and ``undeclared`` deliberately overlap the state buckets rather
    than partitioning with them: a stranded row is also a claimed one, and hiding it
    from the claimed count would make the three states stop summing to the total.
    """
    return BoardCounts(
        total=len(rows),
        pending=sum(1 for r in rows if r.state is TaskState.PENDING),
        claimed=sum(1 for r in rows if r.state is TaskState.CLAIMED),
        completed=sum(1 for r in rows if r.state is TaskState.COMPLETED),
        stranded=sum(1 for r in rows if r.owner_gone),
        undeclared=sum(1 for r in rows if r.missing),
    )


def summary_label(c: BoardCounts) -> str:
    """
    The board's shape, in the states that are ordinary.

    Zero-count states are dropped rather than printed as "0 claimed": a summary is
    read at a glance, and a fixed set of fields makes the reader parse three numbers
    to learn there is nothing to see.
    """
    parts = [f"{c.total} task(s)"]
    if c.pending:
        parts.append(f"{c.pending} pending")
    if c.claimed:
        parts.append(f"{c.claimed} claimed")
    if c.completed:
        parts.append(f"{c.completed} done")
    return " · ".join(parts)


def alarm_label(c: BoardCounts) -> str:
    """
    The part of the summary that is not ordinary, kept separate so it can be
    coloured separately.

    Both conditions here are ones nothing clears on its own: a stranded row's owner
    is never coming back, and a dependency that was never declared will not become
    satisfied. Empty when the board is healthy, so the warning colour appears on a
    board only when it has earned it.
    """
    parts = []
    if c.stranded:
        parts.append(f"{c.stranded} stranded")
    if c.undeclared:
        # "waiting on a task that was never declared" is what the row says, and it
        # is the right length there and the wrong length here: this line sits above
        # the scroll in a pane both layouts dock narrow, and a summary that wraps to
        # three lines stops being a summary. The row carries the full sentence.
        parts.append(f"{c.undeclared} blocked on a task nobody declared")
    return " · ".join(parts)


def reasons_marker(reasons: tuple[str, ...]) -> str:
    """
    How many concerns explain a row, pluralised.

    A count rather than the subjects themselves: the subjects are behind the
    triangle, and the marker's job on the collapsed row is only to say that an
    explanation exists -- which is the difference between a row stalled for a
    reason and one stalled silently.
    """
    return "1 concern" if len(reasons) == 1 else f"{len(reasons)} concerns"


def row_reasons(row: BoardTask, by_id: Mapping[ConcernId, BoardConcern]) -> tuple[str, ...]:
    """
    The subjects of the concerns that explain a row, oldest first.

    Looked up rather than carried on ``BoardTask`` because the subject already
    exists on the concern row and copying it onto the task row would be two places
    holding one string. An id with no matching concern is skipped rather than
    rendered as a blank line -- the two projections are built from one snapshot in
    one frame, so a miss is not a state either of them can reach.
    """
    out = []
    for cid in row.concerns:
        found = by_id.get(cid)
        if found is not None:
            out.append(found.subject or "(no subject)")
    return tuple(out)


def owner_label(row: BoardTask) -> str:
    """
    Who holds a task, or nothing at all when it is unclaimed.

    A claimer that has finished, failed or left the snapshot is named as such
    rather than shown as an ordinary owner. The task stays CLAIMED forever in that
    case -- the store has no arm that releases work when its worker dies -- so a
    row reading "builder" would report progress that has stopped.
    """
    if row.owner is None:
        return ""
    return f"[{row.owner}, stopped]" if row.owner_gone else f"[{row.owner}]"


def blocked_label(row: BoardTask, limit: int = _MAX_BLOCKED_IDS) -> str:
    """
    What a task is waiting on, or nothing when it is waiting on nothing.

    A dependency naming a task that was never declared is marked, not just listed.
    ``declare_task`` answers "on the board" for a task whose ``depends_on`` names
    an id that does not exist, and ``is_claimable`` deliberately treats that as
    unsatisfied -- so the task is unclaimable forever and this cell is the only
    place the operator can see it. "blocked on t9" is something to wait out;
    "blocked on t9 (never declared)" is something to go and fix.

    Bounded like everything else here, and says so when it bites.
    """
    if not row.blocked_on:
        return ""
    shown, dropped = bound_rows(row.blocked_on, limit)
    ids = ", ".join(f"{d} (never declared)" if d in row.missing else d for d in shown)
    return f"blocked on {ids}" + (f", and {dropped} more" if dropped else "")


def touches_label(row: BoardTask, limit: int = _MAX_TOUCHES) -> str:
    """
    The files a task declared it would write.

    Worth a line of its own because the dependency graph above it is now partly
    derived from this: an edge the board added is not in the lead's plan, and
    ``blocked on t1`` with no sight of the overlap is a wait with no visible cause.
    """
    if not row.touches:
        return ""
    shown, dropped = bound_rows(row.touches, limit)
    return "writes " + ", ".join(shown) + (f", and {dropped} more" if dropped else "")


def about_label(row: BoardConcern) -> str:
    """
    Which task a concern is about, when its sender named one.

    A named task that is not on the board is called out rather than printed plainly,
    for the same reason ``blocked_label`` marks a missing dependency: the store
    accepts the message and does not validate the id, so this is the only place a
    typo becomes visible.
    """
    if row.task_id is None:
        return ""
    return f"about {row.task_id} (never declared)" if row.task_missing else f"about {row.task_id}"


def concern_label(row: BoardConcern) -> str:
    """
    One concern's participants and where it got to.

    An edited concern says so. The operator can rewrite a message on its way
    through the review queue, and what the recipient was actually told is then
    different from what the sender wrote -- which is the fact this log exists to
    keep, and the one nothing else in the UI records after the approval is gone.
    """
    state = _CONCERN_STATE_LABEL[row.state]
    if row.edited:
        state += ", edited by you"
    return f"{row.sender} -> {row.recipient}  ({state})"


def clip(text: str, limit: int) -> tuple[str, int]:
    """
    Bound a string, reporting how much was dropped.

    Returns the count rather than appending an ellipsis, so the caller can render
    the omission as its own coloured line. A silent cap in a pane whose premise is
    "the board is readable" is worse than no pane.
    """
    if len(text) <= limit:
        return text, 0
    return text[:limit], len(text) - limit


def bound_rows(rows: Sequence[_T], limit: int, *, tail: bool = False) -> tuple[tuple[_T, ...], int]:
    """
    Bound a list of rows, reporting how many were dropped.

    ``tail`` picks which end survives, and the two callers want opposite ends. Tasks
    are head-anchored: the board is read in declaration order and the oldest rows are
    the ones with dependents. The concern log is tail-anchored, because an audit trail
    is read from its recent end.
    """
    if len(rows) <= limit:
        return tuple(rows), 0
    dropped = len(rows) - limit
    return (tuple(rows[dropped:]) if tail else tuple(rows[:limit])), dropped
