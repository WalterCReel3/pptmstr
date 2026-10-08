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

**The board is no longer in DETAIL at all.** The alternative was a one-line
summary left behind on the obligation branch, and it was declined in favour of one
pane owning the subject outright.

This pane ships docked behind DETAIL, and that is a default rather than a
constraint: ``layout_condition`` is ``first_use_ever``, so the coded arrangement
applies on first run and the operator's own docking wins from then on and persists
in the ini. An operator who wants the board beside an approval drags it into its
own split once. What is worth the screen is theirs to decide, and this module's
job is to be worth docking somewhere rather than to guess where.

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

## With the typeset seam on

**A native table with typeset bodies, not one typeset document.** The library is a
text engine: it breaks lines, it does not align cells or open a row on a click. So
the rows stay ImGui items -- a tree node with its cells hand-placed at column
offsets measured once a frame, in the seam's faces -- and only what is prose goes
through a view: a specification, the concerns that explain a row, a concern's body.

**A collapsed row is one line and its title is cut, not wrapped.** The cut is
marked with an ellipsis, the whole title is on hover and is the first thing in the
opened row. A row with an owner, a wait or a concern keeps its second line, as it
has without the seam: those are what an operator scans for, and nothing that fits
in a pane this narrow carries them beside a title.

**State is a glyph and then its word**, both in one ink. The glyph is drawn by
ImGui in the UI font, which is the only face with the icons merged; inside typeset
text it would be U+FFFD.

**A concern is one line until it is opened**: participants, subject, and a word at
the right edge only when its state is not the ordinary one. A body is typeset only
for an open row, so eighty concerns are eighty lines and not eighty views.

**The subject has the line and the participants what is left of it** (``people_cell``).
A subject is what tells one concern from the next; who sent it to whom is the same
few role names down the column. So the column is "sender -> recipient" only where
that leaves the subject ``1 - _PEOPLE_SHARE`` of the line, the sender alone where it
does not, and nothing in a pane too narrow for that. It is decided for the column
and not per row, so subjects start at one offset. An opened row says both names
whatever its line showed, and so does the hover of a line that left one out.

**A body is not assumed to be markdown.** ``typeset.prose_parts`` is the rule, the
same one every pane showing a body applies; this module only bounds what it is
handed (``clipped_parts``).

**The character bounds stay.** A capped view bounds what is drawn; what a long body
costs is the parse and the first layout, which are linear in its length and land on
the frame the row is opened. The cut falls on a line end and says how much it
dropped, inside the view.

**Under the cursor, views are kept only for what was drawn this frame.** Which rows
are open is ImGui's to know, so the set of live views is collected as they are drawn
and everything else is let go; a row reopened is laid out again.

**A team the cursor has left keeps its views.** The pane follows the cursor, and the
cursor passes over sessions that are not teams on its way through a mixed queue.
Laying every open row out again on the way back is the cost of a first frame paid
on a keystroke. So a view is keyed by its session and outlives the cursor's absence
for as long as that session is a team in the snapshot, for the ``_HELD_BOARDS`` teams
shown most recently. A view that is not drawn costs nothing a frame.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TypeVar

from imgui_bundle import icons_fontawesome_6 as fa
from imgui_bundle import imgui

from ..board import (
    BoardConcern,
    BoardTask,
    board_concerns,
    board_tasks,
    has_board,
)
from ..bridge import Bridge
from ..intents import TaskAmended
from ..model import ConcernId, ConcernState, Snapshot, TaskState
from ..theme import Color, P
from . import typeset
from .focus import FocusState
from .typeset import Kind, Part, Weight
from .widgets import CTRL_ENTER_SUBMITS, ellipsis, multiline_input, section

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
# One agent note. Tighter than a specification: a concern is one finding, and a
# row carrying several of them should still read as a row.
_MAX_CONCERN_CHARS = 1500

# Lines of typeset text an opened row's specification shows before the view scrolls
# on its own: a little over half of the pane at its default size, so one long
# specification does not put the rest of the board two screens away.
_SPEC_LINES = 24
# The same for one concern's body, and set past what ``_MAX_CONCERN_CHARS`` comes to
# at the width this pane docks at. It is not there to cut a note short. A capped
# view is the only one that scrolls sideways, and a pasted traceback is wider than
# the pane.
_NOTE_LINES = 40
# Characters of a task id in its column. Ids are a lead's to choose, and one long
# one would otherwise take the title's room on every row.
_MAX_ID_CELLS = 14
# The most of a concern's line its participants may take from its subject. Both
# coded layouts dock this pane narrower than "sender -> recipient" fits in at this
# share, so there the column is the sender; a pane dragged wider gets both.
_PEOPLE_SHARE = 0.3
# Teams whose open rows keep their views while the cursor is somewhere else. A view
# holds its document and a layout for every width it was drawn at, and a finished
# team stays in the snapshot, so "every team ever looked at" has no bound of its own.
_HELD_BOARDS = 6

_CONCERN_STATE_LABEL: dict[ConcernState, str] = {
    ConcernState.POSTED: "waiting",
    ConcernState.DELIVERED: "delivered",
    ConcernState.WITHDRAWN: "withdrawn",
}

# Only ever a row type from .board; named so bound_rows can serve both tables and
# the id lists without three copies of the same slice.
_T = TypeVar("_T")

# What names a held view: its session, what it is of, and the ids of that.
_Key = tuple[str, ...]


@dataclass
class BoardState:
    """
    What the operator is part-way through typing, keyed by task id.

    Here rather than in the store for the reason ``ReviewState`` gives: a half-typed
    correction is not a fact about the world. Amending is the one thing this pane
    does that changes anything, and an amendment abandoned mid-sentence must leave
    no trace.
    """

    # The task whose specification is open for editing, or None.
    editing: str | None = None
    drafts: dict[str, str] = field(default_factory=dict)
    # The typeset views of the rows that are open, the parts each was built from
    # beside the text they were made of, and the keys drawn in the current frame.
    # A key starts with its session's id.
    views: typeset.ViewCache = field(default_factory=typeset.ViewCache)
    parts: dict[_Key, tuple[str, tuple[Part, ...]]] = field(default_factory=dict)
    shown: set[_Key] = field(default_factory=set)
    # The teams this pane has shown, the latest last: whose views ``_release`` keeps.
    held: list[str] = field(default_factory=list)


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


def draw(snap: Snapshot, focus: FocusState, pane: BoardState, bridge: Bridge) -> None:
    """
    Render the board of the session under the cursor. Never reads the store.

    Follows the cursor exactly as DETAIL and CONTEXT do. It flips between teams as
    the operator moves through the inbox, which is the honest cost of not being a
    second selection: a pane that held still would need a pointer of its own, and
    that pointer is the thing the layout deleted.
    """
    node = focus.node(snap)
    if node is None:
        _release(pane, snap, None)
        imgui.text_disabled("no session under the cursor")
        return

    session_id = node[0]
    if not has_board(snap, session_id):
        _release(pane, snap, None)
        imgui.text_disabled("this session is not a team")
        return

    rows = board_tasks(snap, session_id)
    concerns = board_concerns(snap, session_id)
    _summary(counts(rows))
    # Drafts for tasks that have left the board keep no value and would otherwise
    # accumulate for the life of the process.
    _prune(pane, {r.id for r in rows})

    if imgui.begin_child("##board"):
        imgui.push_text_wrap_pos(0.0)
        pane.shown = set()
        if typeset.enabled(typeset.Pane.BOARD):
            _typeset_tasks(session_id, rows, {c.id: c for c in concerns}, pane, bridge)
            _typeset_concerns(session_id, concerns, pane)
        else:
            _tasks(rows, {c.id: c for c in concerns}, pane, bridge)
            _concerns(concerns)
        _release(pane, snap, session_id)
        imgui.pop_text_wrap_pos()
    imgui.end_child()


def _prune(pane: BoardState, live: set[str]) -> None:
    for task_id in [t for t in pane.drafts if t not in live]:
        del pane.drafts[task_id]
    if pane.editing is not None and pane.editing not in live:
        pane.editing = None


def _release(pane: BoardState, snap: Snapshot, shown: str | None) -> None:
    """
    Let go of the views nothing will draw again, and of the parts they were built from.

    ``shown`` is the team drawn this frame, if one was: of its views, the ones not
    drawn belong to rows that have closed. Every other team's are kept as they are
    while it is still a team in ``snap`` and among the ``_HELD_BOARDS`` shown latest.
    """
    holding = {key[0] for key in pane.parts}
    if shown is not None:
        pane.held = [s for s in pane.held if s != shown] + [shown]
    pane.held = [s for s in pane.held if s in holding and has_board(snap, s)][-_HELD_BOARDS:]
    live = {k for k in pane.parts if k[0] in pane.held and (k[0] != shown or k in pane.shown)}
    pane.views.prune(live)
    for key in [k for k in pane.parts if k not in live]:
        del pane.parts[key]


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


def _tasks(
    rows: Sequence[BoardTask],
    by_id: Mapping[ConcernId, BoardConcern],
    pane: BoardState,
    bridge: Bridge,
) -> None:
    shown, dropped = bound_rows(rows, _MAX_BOARD_TASKS)
    for row in shown:
        _task_row(row, by_id, pane, bridge)
    if dropped:
        imgui.text_colored(P.text_dim.vec4, f"... {dropped} more task(s) not shown")


def _task_row(
    row: BoardTask,
    by_id: Mapping[ConcernId, BoardConcern],
    pane: BoardState,
    bridge: Bridge,
) -> None:
    """
    One task: a headline that is always visible, and the rest behind a triangle.

    What goes in the headline is what an operator scanning for trouble needs
    without opening anything -- who holds it, whether that owner has stopped, what
    it waits on, and whether somebody explained why it is not moving. The
    specification and the file list are what you open a row to read.
    """
    reasons = row_reasons(row, by_id)

    # Every row opens, including one with nothing to show yet. The body stopped
    # being purely informational when it gained the amend control, and a task with
    # no specification is the one most likely to need one written -- a leaf flag
    # here would put the affordance on exactly the rows that do not need it and
    # withhold it from the rows that do.
    flags = int(imgui.TreeNodeFlags_.span_avail_width)

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
        pushed = opened
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

    if not opened:
        return

    if row.touches:
        imgui.text_colored(P.text_dim.vec4, touches_label(row))
    for reason in reasons:
        imgui.text_colored(P.accent.vec4, f"reason: {reason.subject or '(no subject)'}")
        if reason.body:
            body, dropped = clip(reason.body, _MAX_CONCERN_CHARS)
            imgui.text_colored(P.text.vec4, body)
            if dropped:
                imgui.text_colored(P.text_dim.vec4, f"... {dropped} more character(s) not shown")
    if pane.editing == row.id:
        _amend_editor(row, pane, bridge)
    else:
        if row.detail:
            body, dropped = clip(row.detail, _MAX_DETAIL_CHARS)
            imgui.text_colored(P.text.vec4, body)
            if dropped:
                imgui.text_colored(P.text_dim.vec4, f"... {dropped} more character(s) not shown")
        if imgui.small_button(f"amend##{row.id}"):
            pane.editing = row.id
            pane.drafts.setdefault(row.id, row.detail)
    imgui.tree_pop()


def _amend_editor(row: BoardTask, pane: BoardState, bridge: Bridge) -> None:
    """
    The operator rewriting a task's specification in place.

    **This is the only thing in this pane that changes anything**, and it is here
    rather than in a prompt to the lead because the defect it answers is that an
    operator's redirect reached one worker and left no record anywhere else. The
    lead had no copy, the board had no copy, and the worker that obeyed it was
    accused of inventing it. A spec with one home cannot disagree with itself.

    The draft is not applied as it is typed. An amendment is a decision, so it
    lands on an explicit action -- the same reason the launcher does not spawn a
    session per keystroke.

    Cancelling drops the draft rather than keeping it, because a half-written
    correction that survives is one an operator can send later believing they
    finished it.
    """
    submitted, pane.drafts[row.id] = multiline_input(
        f"##amend-{row.id}",
        pane.drafts.get(row.id, row.detail),
        imgui.ImVec2(-1, 120.0),
        wrap=True,
        flags=CTRL_ENTER_SUBMITS,
    )
    imgui.text_disabled("Ctrl+Enter amends, Enter for a new line")

    applied = imgui.small_button(f"amend##apply-{row.id}") or submitted
    imgui.same_line()
    cancelled = imgui.small_button(f"cancel##{row.id}")

    if applied:
        # node_id stays None: this is the operator's, and the store reads that as
        # the authority to rewrite a spec an agent is bound by.
        bridge.emit(TaskAmended(task_id=row.id, detail=pane.drafts[row.id]))
    if applied or cancelled:
        pane.editing = None
        pane.drafts.pop(row.id, None)


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
        imgui.indent()
        imgui.text_colored(P.text.vec4, row.subject or "(no subject)")
        about = about_label(row)
        if about:
            # Its own line, not trailing the participants. The lesson is the same one
            # the task qualifiers taught: `same_line` after any string that may wrap
            # inherits whatever width the wrap left, and near the right edge that is
            # a column one character wide. FOCUS docks this pane at 0.26, which is
            # where it showed up.
            imgui.text_colored((P.warn if row.task_missing else P.text_dim).vec4, about)
        imgui.unindent()
    if dropped:
        imgui.text_colored(P.text_dim.vec4, f"... {dropped} earlier concern(s) not shown")


# -- the same board through the typeset seam -----------------------------------


@dataclass(frozen=True, slots=True)
class _Columns:
    """
    The widths of a task row's cells, measured once for the rows of one frame.
    """

    glyph: float
    state: float
    ident: float
    gap: float


def _gap() -> float:
    return float(imgui.calc_text_size(" ").x)


def _under_headline(opened: bool) -> None:
    """
    Move the cursor to where a row's first cell starts, for a line beneath it.

    An open node has pushed a tree indent that everything after it inherits and a
    closed one has not, so the distance still to go depends on which this is. A
    plain ``indent`` would also stop short: it is the tree's step, and the first
    cell sits past the arrow and an item spacing.
    """
    lead = imgui.get_tree_node_to_label_spacing() + imgui.get_style().item_spacing.x
    if opened:
        lead -= imgui.get_style().indent_spacing
    imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + lead)


def _task_columns(rows: Sequence[BoardTask]) -> _Columns:
    glyph = max(imgui.calc_text_size(g).x for g in _TASK_GLYPHS)
    with typeset.font(Weight.REGULAR):
        state = max((imgui.calc_text_size(s.value).x for s in {r.state for r in rows}), default=0.0)
    with typeset.font(Weight.CODE):
        cells = min(max((len(r.id) for r in rows), default=0), _MAX_ID_CELLS)
        ident = cells * imgui.calc_text_size("0").x
    return _Columns(glyph=glyph, state=state, ident=ident, gap=_gap())


def _typeset_tasks(
    session_id: str,
    rows: Sequence[BoardTask],
    by_id: Mapping[ConcernId, BoardConcern],
    pane: BoardState,
    bridge: Bridge,
) -> None:
    shown, dropped = bound_rows(rows, _MAX_BOARD_TASKS)
    columns = _task_columns(shown)
    for row in shown:
        _typeset_task_row(row, session_id, by_id, columns, pane, bridge)
    if dropped:
        typeset.label(
            f"... {dropped} more task(s) not shown", weight=Weight.REGULAR, colour=P.text_dim
        )


def _typeset_task_row(
    row: BoardTask,
    session_id: str,
    by_id: Mapping[ConcernId, BoardConcern],
    columns: _Columns,
    pane: BoardState,
    bridge: Bridge,
) -> None:
    """
    One task: glyph, state, id and title on a line, and the rest behind the triangle.

    The node has no label of its own. Its cells are drawn over it at the frame's
    column offsets, so a state reads down a column and a title starts where every
    other title starts.
    """
    reasons = row_reasons(row, by_id)
    opened = imgui.tree_node_ex(f"###{row.id}", int(imgui.TreeNodeFlags_.span_avail_width))
    title = row.title or "(untitled)"
    # A closed row out of sight keeps its line and draws nothing on it. An open one
    # is always measured: whether its title was cut decides a line of its body.
    cut = (opened or imgui.is_item_visible()) and _task_headline(row, title, columns)

    owner = owner_label(row)
    blocked = blocked_label(row)
    if owner or blocked or reasons:
        _under_headline(opened)
        if owner:
            typeset.label(
                owner, weight=Weight.REGULAR, colour=P.warn if row.owner_gone else P.text_dim
            )
        if blocked:
            if owner:
                imgui.same_line()
            typeset.label(
                blocked, weight=Weight.REGULAR, colour=P.warn if row.missing else P.text_dim
            )
        if reasons:
            if owner or blocked:
                imgui.same_line()
            typeset.label(reasons_marker(reasons), weight=Weight.REGULAR, colour=P.accent)

    if not opened:
        return

    if cut:
        typeset.label(title, colour=P.text)
    if row.touches:
        typeset.label(touches_label(row), weight=Weight.CODE, colour=P.text_dim)
    for reason in reasons:
        typeset.label(f"reason: {reason.subject or '(no subject)'}", colour=P.accent)
        if reason.body:
            # Keyed by the row as well: the same concern is drawn again in the log
            # below, and one view cannot be in two places.
            _note(pane, (session_id, "reason", row.id, reason.id), reason.body)
    if pane.editing == row.id:
        _amend_editor(row, pane, bridge)
    else:
        if row.detail:
            if reasons:
                # Where the last concern ends and the specification begins: both
                # are prose in one face, and only the concerns have a heading.
                imgui.separator()
            _body(pane, (session_id, "spec", row.id), row.detail, _MAX_DETAIL_CHARS, _SPEC_LINES)
        if imgui.small_button(f"amend##{row.id}"):
            pane.editing = row.id
            pane.drafts.setdefault(row.id, row.detail)
    imgui.tree_pop()


def _task_headline(row: BoardTask, title: str, columns: _Columns) -> bool:
    """
    The cells of a task's line. Returns whether the title had to be cut to fit.
    """
    imgui.same_line()
    x = imgui.get_cursor_pos_x()
    glyph, role = task_glyph(row)
    ink: Color = getattr(P, role)
    imgui.text_colored(ink.vec4, glyph)
    x += columns.glyph + columns.gap
    imgui.same_line(x)
    typeset.label(row.state.value, weight=Weight.REGULAR, colour=ink)
    x += columns.state + columns.gap
    imgui.same_line(x)
    with typeset.font(Weight.CODE):
        imgui.text_colored(P.text_dim.vec4, ellipsis(row.id, columns.ident))
    x += columns.ident + columns.gap
    imgui.same_line(x)
    return _fitted(title, imgui.get_content_region_avail().x)


def _fitted(text: str, width: float, *, whole: str = "") -> bool:
    """
    A strong line cut to ``width``, whole on hover. Returns whether it was cut.

    ``whole`` is what the hover says of a line that left something out without
    being cut, and says in place of ``text`` when it was.
    """
    with typeset.font(Weight.BOLD):
        fitted = ellipsis(text, width)
        imgui.text_colored(P.text.vec4, fitted)
    cut = fitted != text
    if (cut or whole) and imgui.is_item_hovered():
        imgui.set_tooltip(whole or text)
    return cut


def _typeset_concerns(session_id: str, rows: Sequence[BoardConcern], pane: BoardState) -> None:
    if not rows:
        return
    shown, dropped = bound_rows(rows, _MAX_BOARD_CONCERNS, tail=True)
    section("concerns")
    glyph = max(imgui.calc_text_size(g).x for g in _CONCERN_GLYPHS)
    with typeset.font(Weight.REGULAR):
        people = (
            max(imgui.calc_text_size(participants_label(r)).x for r in shown),
            max(imgui.calc_text_size(r.sender).x for r in shown),
        )
    for row in shown:
        _typeset_concern_row(row, session_id, glyph, people, pane)
    if dropped:
        typeset.label(
            f"... {dropped} earlier concern(s) not shown", weight=Weight.REGULAR, colour=P.text_dim
        )


def _typeset_concern_row(
    row: BoardConcern, session_id: str, glyph: float, people: tuple[float, float], pane: BoardState
) -> None:
    """
    One concern: who to whom and the subject on a line, the finding behind the triangle.

    ``people`` is the width of the widest "sender -> recipient" among the rows, and
    of the widest sender.

    A concern about a task nobody declared says so on a line of its own even when
    closed. It is the one qualifier here that nothing clears, and it is rare enough
    that the line is paid only where it is owed.
    """
    opened = imgui.tree_node_ex(f"###concern-{row.id}", int(imgui.TreeNodeFlags_.span_avail_width))
    subject = row.subject or "(no subject)"
    cut = (opened or imgui.is_item_visible()) and _concern_headline(row, subject, glyph, people)
    if row.task_missing:
        _under_headline(opened)
        typeset.label(about_label(row), weight=Weight.REGULAR, colour=P.warn)
    if not opened:
        return

    if cut:
        typeset.label(subject, colour=P.text)
    about = "" if row.task_missing else about_label(row)
    said = (participants_label(row), concern_state_label(row), about)
    typeset.label(
        " · ".join(part for part in said if part),
        weight=Weight.REGULAR,
        colour=P.text_dim,
    )
    if row.body:
        _note(pane, (session_id, "concern", row.id), row.body)
    imgui.tree_pop()


def _concern_headline(
    row: BoardConcern, subject: str, glyph_width: float, people: tuple[float, float]
) -> bool:
    """
    The cells of a concern's line. Returns whether the subject had to be cut to fit.
    """
    gap = _gap()
    imgui.same_line()
    x = imgui.get_cursor_pos_x()
    glyph, role = concern_glyph(row)
    imgui.text_colored(getattr(P, role).vec4, glyph)
    x += glyph_width + gap
    imgui.same_line(x)
    right = x + imgui.get_content_region_avail().x
    who, room = people_cell(*people, right - x)
    if who is not _People.NOBODY:
        said = participants_label(row) if who is _People.BOTH else row.sender
        with typeset.font(Weight.REGULAR):
            imgui.text_colored(P.text_dim.vec4, said)
        x += room + gap
    mark = concern_mark(row)
    end = right
    if mark:
        with typeset.font(Weight.REGULAR):
            end = right - imgui.calc_text_size(mark).x
    imgui.same_line(x)
    whole = "" if who is _People.BOTH else f"{participants_label(row)}  {subject}"
    cut = _fitted(subject, (end - gap if mark else end) - x, whole=whole)
    if mark:
        imgui.same_line(end)
        typeset.label(mark, weight=Weight.REGULAR, colour=getattr(P, role))
    return cut


def _note(pane: BoardState, key: _Key, body: str) -> None:
    _body(pane, key, body, _MAX_CONCERN_CHARS, _NOTE_LINES)


def _body(pane: BoardState, key: _Key, text: str, limit: int, lines: int) -> None:
    """
    The typeset view of one body: bounded to ``limit`` characters, capped at ``lines``.

    The parts are built when the text changes and are otherwise the tuple the view
    was last given, which is what lets the seam skip comparing them.
    """
    held = pane.parts.get(key)
    if held is None or held[0] != text:
        held = (text, clipped_parts(text, limit))
        pane.parts[key] = held
    pane.shown.add(key)
    if held[1]:
        cap = lines * typeset.line_height(mono=False)
        typeset.document(pane.views, key, held[1], height_cap=cap)


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


def reasons_marker(reasons: tuple[BoardConcern, ...]) -> str:
    """
    How many concerns explain a row, pluralised.

    A count rather than the subjects themselves: the subjects are behind the
    triangle, and the marker's job on the collapsed row is only to say that an
    explanation exists -- which is the difference between a row stalled for a
    reason and one stalled silently.
    """
    return "1 concern" if len(reasons) == 1 else f"{len(reasons)} concerns"


def row_reasons(
    row: BoardTask, by_id: Mapping[ConcernId, BoardConcern]
) -> tuple[BoardConcern, ...]:
    """
    The concerns that explain a row, oldest first.

    Looked up rather than carried on ``BoardTask`` because the text already exists
    on the concern row and copying it onto the task row would be two places holding
    one string. An id with no matching concern is skipped rather than rendered as a
    blank line -- the two projections are built from one snapshot in one frame, so a
    miss is not a state either of them can reach.

    Returns the rows rather than their subjects so this pane can show what the
    ``read_board`` tool shows an agent. A subject is a label; the body is the
    finding, and an operator reading only labels is back to asking the team what it
    already wrote down.
    """
    return tuple(by_id[cid] for cid in row.concerns if cid in by_id)


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
    return f"{participants_label(row)}  ({concern_state_label(row)})"


def participants_label(row: BoardConcern) -> str:
    return f"{row.sender} -> {row.recipient}"


def concern_state_label(row: BoardConcern) -> str:
    state = _CONCERN_STATE_LABEL[row.state]
    return state + ", edited by you" if row.edited else state


class _People(enum.Enum):
    """
    What a closed concern's line says of who sent it to whom.
    """

    BOTH = "both"
    SENDER = "sender"
    NOBODY = "nobody"


def people_cell(both: float, sender: float, line: float) -> tuple[_People, float]:
    """
    What the participants' column holds on a line ``line`` pixels wide, and its width.

    ``both`` and ``sender`` are the widths of the widest "sender -> recipient" and
    of the widest sender among the rows, so the answer is one for the column. The
    most that fits in ``_PEOPLE_SHARE`` of the line is what is shown, whole: a name
    cut to make room reads as another name.
    """
    room = line * _PEOPLE_SHARE
    if both <= room:
        return _People.BOTH, both
    if sender <= room:
        return _People.SENDER, sender
    return _People.NOBODY, 0.0


def concern_mark(row: BoardConcern) -> str:
    """
    The word a closed concern carries at its right edge, or nothing.

    Nothing for the ordinary case, delivered as written, so that the rows that say
    something are the ones where something happened: still waiting to be read,
    withdrawn, or rewritten by the operator on its way through.
    """
    parts = []
    if row.state is not ConcernState.DELIVERED:
        parts.append(_CONCERN_STATE_LABEL[row.state])
    if row.edited:
        parts.append("edited")
    return ", ".join(parts)


_TASK_DONE = (fa.ICON_FA_CHECK, "ok")
_TASK_HELD = (fa.ICON_FA_GEARS, "state_running")
_TASK_OPEN = (fa.ICON_FA_CIRCLE, "text_dim")
_TASK_WAITING = (fa.ICON_FA_LOCK, "text_dim")
_TASK_STUCK = (fa.ICON_FA_TRIANGLE_EXCLAMATION, "warn")
_TASK_GLYPHS = tuple(
    glyph for glyph, _ in (_TASK_DONE, _TASK_HELD, _TASK_OPEN, _TASK_WAITING, _TASK_STUCK)
)

_CONCERN_GLYPH: dict[ConcernState, tuple[str, str]] = {
    ConcernState.POSTED: (fa.ICON_FA_CLOCK, "accent"),
    ConcernState.DELIVERED: (fa.ICON_FA_CHECK, "text_dim"),
    ConcernState.WITHDRAWN: (fa.ICON_FA_BAN, "text_dim"),
}
_CONCERN_GLYPHS = tuple(glyph for glyph, _ in _CONCERN_GLYPH.values())


def task_glyph(row: BoardTask) -> tuple[str, str]:
    """
    A task's state as a glyph and the palette role it is inked in.

    Five glyphs for three states, because the two conditions nothing clears are
    derived and are the ones worth seeing down a column: a claim whose owner has
    stopped and a wait on a task nobody declared share the warning triangle. A
    pending row that is merely waiting is a lock, one that can be claimed a dot.
    The word beside the glyph is still the stored state.
    """
    if row.state is TaskState.COMPLETED:
        return _TASK_DONE
    if row.owner_gone or row.missing:
        return _TASK_STUCK
    if row.state is TaskState.CLAIMED:
        return _TASK_HELD
    return _TASK_WAITING if row.blocked_on else _TASK_OPEN


def concern_glyph(row: BoardConcern) -> tuple[str, str]:
    """
    A concern's state as a glyph and the palette role it is inked in.

    An edited concern takes the accent whatever its state, so the glyph and the
    word at the far edge of its line are one colour.
    """
    glyph, role = _CONCERN_GLYPH[row.state]
    return glyph, "accent" if row.edited else role


def clip_lines(text: str, limit: int) -> tuple[str, int]:
    """
    ``clip``, with the cut moved back to the end of a line when there is one near.

    A cut in the middle of a line can land inside a code span or an emphasis and
    change how everything before it on that line is read. A text that is one long
    line is cut where the limit falls.
    """
    if len(text) <= limit:
        return text, 0
    at = text.rfind("\n", limit // 2, limit + 1)
    if at < 0:
        at = limit
    return text[:at], len(text) - at


def clipped_parts(text: str, limit: int) -> tuple[Part, ...]:
    """
    A body as parts, bounded, with the bound said in the view when it bites.
    """
    kept, dropped = clip_lines(text, limit)
    parts = typeset.prose_parts(kept)
    if dropped:
        note = f"... {dropped} more character(s) not shown"
        parts += (Part(note, Kind.WRAPPED, "text_dim"),)
    return parts


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
