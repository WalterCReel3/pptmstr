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

The one exception is a session that is not a team and holds board facts anyway,
which is a team session resumed under the solo template: the template is the
operator's choice for the launch and is not read back out of the session, and its
restored claims still occupy their ids and their holders. Those are drawn, under a
line that says why there is a board here at all.

**What the board's log cannot vouch for is said above the rows** (``restore_line``,
``recording_line``): a log that held more than this board shows, or a board whose
changes are not being written down. Only those -- a ``Tone.ALARM`` line -- take the
room above the scroll. The routine account (restored whole, recorded in a
directory, recording switched off by choice) is in the session details modal,
worded by the same two functions, so the header says something only when it is
something the operator must not miss. The shell knows the recording state and
hands it over as a ``Logging``; this module reads no file.

**The rows can be filtered by where each one stands** (``RowKind``): open, held,
waiting, done. Which kinds are hidden is this pane's view state, like a row's
disclosure triangle, and the kind is derived from the row each frame. The summary
counts the whole board whatever is hidden, and a line says how many rows the
filter left when it left fewer than all of them.

**Tasks and concerns, and nothing that is a reading of them.** The session's
digest is the DIGEST pane's (``digest_pane``), which is docked with this one and
gates on the same ``shows_board``. It borrows ``Line``, ``tone_colour`` and
``author`` from here so that a quoted body looks the same in both.

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

import dataclasses
import enum
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TypeVar, assert_never

from imgui_bundle import icons_fontawesome_6 as fa
from imgui_bundle import imgui

from ..board import (
    UNDELIVERABLE,
    BoardConcern,
    BoardTask,
    basis_label,
    board_concerns,
    board_tasks,
    closed_label,
    foreign_wait_label,
    has_board,
    incident_marker,
    not_run_label,
    property_label,
    stale_label,
    version_label,
)
from ..bridge import Bridge
from ..intents import TaskAmended, TaskClosed
from ..model import (
    MAX_CLOSE_REASON_CHARS,
    MAX_PROPERTY_CHARS,
    Basis,
    ConcernId,
    ConcernState,
    NotPlaced,
    Restore,
    Snapshot,
    TaskState,
    TranscriptPresence,
    Unplaced,
    normalised_property,
    property_overrun,
)
from ..theme import Color, P
from . import typeset
from .focus import FocusState
from .typeset import Kind, Part, Weight
from .widgets import CTRL_ENTER_SUBMITS, closed_line, ellipsis, multiline_input, section

# The one-line rules live with the other shared drawing helpers. Re-exported under
# this module's name because the decision view and the tests reach them through it.
from .widgets import NUL_SHOWN as NUL_SHOWN
from .widgets import author_text as author_text
from .widgets import one_line as one_line

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
# What a claimer reported on finishing. The specification's bound, because it is the
# same kind of prose and the operator opens a finished row to read it.
_MAX_RESULT_CHARS = 4000
# One agent note. Tighter than a specification: a concern is one finding, and a
# row carrying several of them should still read as a row.
_MAX_CONCERN_CHARS = 1500
# What a claimer listed as not run. A note's bound, for the reason the bus gives.
_MAX_NOT_RUN_CHARS = 1500

# The gutter an agent's text is indented by, in font heights, and the bar drawn in
# it (``author``).
_GUTTER_EMS = 0.75
_BAR_THICKNESS = 2.0

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


class RowKind(enum.Enum):
    """
    Where a task row stands, as the board's filter names it (``row_kind``).

    Four and not ``TaskState``'s three: a pending row that waits on another task
    and one that can be claimed now are different rows to an operator, and the
    difference is ``Task.is_claimable``, which is derived and never stored.
    """

    OPEN = "open"
    HELD = "held"
    WAITING = "waiting"
    DONE = "done"


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
    # The property of the task being amended, as typed so far. Beside ``drafts``
    # and not inside it because the two are amended separately: one left as it
    # was found is not part of the amendment.
    property_drafts: dict[str, str] = field(default_factory=dict)
    # The task whose close box is open, or None. One editor at a time: opening
    # this abandons an amendment in progress, and the reverse.
    closing: str | None = None
    # Why the operator is closing it, as typed so far.
    close_reasons: dict[str, str] = field(default_factory=dict)
    # The first of the two presses has been made for ``closing``. The reason is
    # then shown and no longer a box, so what the second press sends is what the
    # first one was looking at.
    close_confirming: bool = False
    # The kinds of row the operator has hidden. Empty is the whole board.
    hidden: set[RowKind] = field(default_factory=set)

    def stop_closing(self) -> None:
        """
        Leave the close box, keeping nothing of it.
        """
        self.closing = None
        self.close_reasons.clear()
        self.close_confirming = False


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


@dataclass(frozen=True, slots=True)
class LogState:
    """
    What the shell knows about one session's board log. Built and replaced by
    ``app``; read here for ``recording_line`` and ``restore_line``.

    ``recording`` is whether a board fact of this session is handed to the
    writer. False with ``stopped`` unset is a session whose log this process
    never appended to; with it set, one that was being recorded until a record
    did not land.
    """

    # Where the session's records are, as a path the operator can open.
    directory: str
    recording: bool = True
    # A logged board has been read for this session and the store has not said
    # yet what it made of it. Nothing is recorded meanwhile and nothing is said.
    awaiting: bool = False
    # Why not, when ``recording`` is False and ``awaiting`` is.
    why_not: str = ""
    stopped: bool = False
    # Whether the log on disk says recording stopped, which is whether the next
    # restore of it will read as short. None when there was nothing to mark: the
    # log was never this process's to append to.
    marked: bool | None = None
    # Records handed to the writer that a restore will not read back.
    unkept: int = 0
    # The latest time, on the wall clock, the restored board carries, or None
    # for a session that restored nothing.
    newest: float | None = None
    # Why each earlier writer of the restored log stopped recording.
    stops: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Logging:
    """
    The board logs of this process, as the pane is handed them each frame.

    ``off`` is why no board is recorded here at all where that was chosen -- the
    setting, the fake driver -- and ``unread`` is why where it was not: the
    settings file could not be read for the switch, so recording is off as the
    safe reading and nobody asked for that. Both None when a board is recorded.
    ``sessions`` is by session id.
    """

    off: str | None = None
    unread: str | None = None
    sessions: Mapping[str, LogState] = field(default_factory=dict)


def draw(
    snap: Snapshot,
    focus: FocusState,
    pane: BoardState,
    bridge: Bridge,
    logging: Logging | None = None,
) -> None:
    """
    Render the board of the session under the cursor. Never reads the store.

    Follows the cursor exactly as DETAIL and CONTEXT do. It flips between teams as
    the operator moves through the inbox, which is the honest cost of not being a
    second selection: a pane that held still would need a pointer of its own, and
    that pointer is the thing the layout deleted.

    ``logging`` is what the shell knows of the board logs. None is a caller with
    nothing to say about them, and then neither line about recording is drawn.
    """
    node = focus.node(snap)
    if node is None:
        _release(pane, snap, None)
        imgui.text_disabled("no session under the cursor")
        return

    session_id = node[0]
    absent = absent_line(snap, session_id)
    if absent is not None:
        _release(pane, snap, None)
        imgui.push_text_wrap_pos(0.0)
        imgui.text_disabled(absent)
        imgui.pop_text_wrap_pos()
        return
    team = has_board(snap, session_id)
    rows = tuple(flat_task(r) for r in board_tasks(snap, session_id))
    concerns = tuple(flat_concern(c) for c in board_concerns(snap, session_id))
    if not shows_board(team, len(rows), len(concerns)):
        _release(pane, snap, None)
        imgui.text_disabled("this session is not a team")
        return

    if not team:
        imgui.push_text_wrap_pos(0.0)
        imgui.text_colored(P.warn.vec4, NOT_A_TEAM_WITH_A_BOARD)
        imgui.pop_text_wrap_pos()
    _summary(counts(rows))
    _log_lines(snap, logging, session_id)
    # Read before the chips are drawn, so a press shows from the next frame and
    # this one draws the rows the frame began with.
    visible = filtered(rows, pane.hidden)
    if rows:
        _filter_chips(pane)
        if len(visible) < len(rows):
            imgui.text_colored(P.text_dim.vec4, showing_label(len(visible), len(rows)))
    # Drafts for tasks that have left the board keep no value and would otherwise
    # accumulate for the life of the process.
    _prune(pane, {r.id for r in rows})
    # A close box for a row that has left the board, or that finished while the
    # operator was typing its reason, is over: there is nothing left to close, and
    # the reason must not wait there for whichever row is opened next.
    if pane.closing is not None and pane.closing not in {r.id for r in rows if closable(r)}:
        pane.stop_closing()

    if imgui.begin_child("##board"):
        imgui.push_text_wrap_pos(0.0)
        pane.shown = set()
        if typeset.enabled(typeset.Pane.BOARD):
            _typeset_tasks(session_id, visible, {c.id: c for c in concerns}, pane, bridge)
            _typeset_concerns(session_id, concerns, pane)
        else:
            _tasks(visible, {c.id: c for c in concerns}, pane, bridge)
            _concerns(concerns)
        _release(pane, snap, session_id)
        imgui.pop_text_wrap_pos()
    imgui.end_child()


def _prune(pane: BoardState, live: set[str]) -> None:
    for drafts in (pane.drafts, pane.property_drafts):
        for task_id in [t for t in drafts if t not in live]:
            del drafts[task_id]
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


def _log_lines(snap: Snapshot, logging: Logging | None, session_id: str) -> None:
    """
    What this board's log cannot vouch for, above the scroll for the reason
    ``_summary`` is: a line that says the rows below are not the whole board, or
    that what happens to them is not being kept, must not be one the rows can
    scroll away. The routine lines are the session details modal's.
    """
    imgui.push_text_wrap_pos(0.0)
    for line in log_lines(snap, logging, session_id):
        if line.tone is Tone.ALARM:
            imgui.text_colored(tone_colour(line.tone).vec4, line.text)
    imgui.pop_text_wrap_pos()


def _filter_chips(pane: BoardState) -> None:
    """
    One toggle per ``RowKind``, on as many lines as the pane's width needs. A
    hidden kind is drawn dim and without its frame.
    """
    padding = float(imgui.get_style().frame_padding.x)
    right = float(imgui.get_cursor_screen_pos().x) + float(imgui.get_content_region_avail().x)
    for at, kind in enumerate(RowKind):
        if at:
            imgui.same_line()
            width = float(imgui.calc_text_size(kind.value).x) + 2 * padding
            if float(imgui.get_cursor_screen_pos().x) + width > right:
                imgui.new_line()
        hidden = kind in pane.hidden
        if hidden:
            imgui.push_style_color(imgui.Col_.text, P.text_dim.vec4)
            imgui.push_style_color(imgui.Col_.button, imgui.ImVec4(0.0, 0.0, 0.0, 0.0))
        if imgui.small_button(f"{kind.value}##board-filter"):
            pane.hidden ^= {kind}
        if hidden:
            imgui.pop_style_color(2)


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
    it waits on, whether somebody explained why it is not moving, and, once it is
    finished, how its claimer says it knows and what it says it did not run. The
    specification, the file list and the texts themselves are what you open a row
    to read.
    """
    reasons = row_reasons(row, by_id)

    # Every row opens, including one with nothing to show yet. The body stopped
    # being purely informational when it gained the amend control, and a task with
    # no specification is the one most likely to need one written -- a leaf flag
    # here would put the affordance on exactly the rows that do not need it and
    # withhold it from the rows that do.
    flags = int(imgui.TreeNodeFlags_.span_avail_width)

    # The id is shown flattened and hashed as it is: the hash is what the triangle's
    # state and the amend controls are keyed on, and that is the board's own id.
    opened = imgui.tree_node_ex(f"{one_line(row.id)}  {row.state.value}###{row.id}", flags)
    imgui.same_line()
    imgui.text_colored(P.text.vec4, row.title or "(untitled)")

    # The qualifiers go on their own line rather than trailing the title. Both
    # layouts dock this pane into a narrow space, and ``same_line`` after a wrapped
    # title puts the cursor wherever that title happened to end -- which, near the
    # right edge, is a column one character wide that renders the next string
    # vertically. A fresh line starts at the indent with the full width available.
    owner = owner_label(row)
    blocked = blocked_label(row)
    held_to = property_label(row.property)
    # Only on the closed row. An opened one says the same under the headings over
    # its result and its list, and rows ship closed, so this line is what stands
    # between a finished row and reading as run because nothing said otherwise.
    standing = "" if opened else standing_label(row)
    # On the row open or closed, like the owner: which specification this is and
    # whether its holder has seen it are facts about the row and not a reading of
    # its text. Flattened here because the name in them is an agent's type.
    replaced = one_line(version_label(row))
    stale = one_line(stale_label(row))
    # On the row open or closed: a wait nothing on this board can end is the row
    # the operator has to act on, and it reads as an ordinary wait without this.
    foreign = foreign_wait_label(row)
    if held_to or owner or blocked or reasons or standing or replaced or foreign:
        # An open non-leaf has already pushed a tree indent that everything after it
        # inherits; a closed row or a leaf has not. Indenting unconditionally would
        # therefore put the qualifiers one level deeper on exactly the rows whose
        # body is visible, so the same line moves as a row is opened.
        pushed = opened
        if not pushed:
            imgui.indent()
        if held_to:
            # On the closed row, first, and never clipped: it is one bounded
            # sentence, and it is what the operator ranks a row's notes against
            # without opening it.
            imgui.text_colored(P.text.vec4, held_to)
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
        if foreign:
            imgui.text_colored(P.warn.vec4, foreign)
        if replaced:
            imgui.text_colored(P.text_dim.vec4, replaced)
        if stale:
            # The warning colour: a result filed against a text that has since
            # been replaced, or a holder working to one, is a row the operator
            # should not read as settled.
            imgui.text_colored(P.warn.vec4, stale)
        if standing:
            # A line of its own, after whatever the owner line wrapped to. One
            # colour for two statements, so it is dim only when neither wants the
            # operator (``settled``).
            imgui.text_colored((P.text_dim if settled(row) else P.warn).vec4, standing)
        if not pushed:
            imgui.unindent()

    if not opened:
        return

    if row.touches:
        imgui.text_colored(P.text_dim.vec4, touches_label(row))
    for reason in reasons:
        # An incident is warn-coloured in the log below, and is the same concern
        # here. An unstated basis is warn-coloured on every heading that has one.
        alarming = reason.incident or reason.basis is None
        imgui.text_colored((P.warn if alarming else P.accent).vec4, reason_heading(reason))
        if reason.body:
            body, dropped = clip(reason.body, _MAX_CONCERN_CHARS)
            author(body)
            if dropped:
                imgui.text_colored(P.text_dim.vec4, f"... {dropped} more character(s) not shown")
    if row.result:
        # Ahead of the specification and outside the editing branch: on a finished
        # row this is what the row was opened for, and amending what was asked does
        # not hide what was reported.
        # A close has no basis to be missing, so its heading is not a warning.
        heading_colour = (
            P.text_dim if row.closed_by_operator else _basis_colour(row.result_basis, P.accent)
        )
        imgui.text_colored(heading_colour.vec4, result_heading(row))
        body, dropped = clip(row.result, _MAX_RESULT_CHARS)
        author(body)
        if dropped:
            imgui.text_colored(P.text_dim.vec4, f"... {dropped} more character(s) not shown")
    elif row.state is TaskState.COMPLETED:
        # The closed row says this, and the opened one is the fuller reading.
        imgui.text_colored(P.warn.vec4, NO_RESULT)
    unrun = unrun_heading(row)
    if unrun:
        # Warn-coloured wherever nobody has said the work was run in full, which
        # is every finished row but one whose claimer stated it.
        imgui.text_colored((P.text_dim if row.not_run == "" else P.warn).vec4, unrun)
        if row.not_run:
            body, dropped = clip(row.not_run, _MAX_NOT_RUN_CHARS)
            author(body)
            if dropped:
                imgui.text_colored(P.text_dim.vec4, f"... {dropped} more character(s) not shown")
    if pane.closing == row.id:
        _close_editor(row, pane, bridge)
    elif pane.editing == row.id:
        _amend_editor(row, pane, bridge)
    else:
        heading = spec_heading(row)
        if heading:
            imgui.text_colored(_basis_colour(row.basis, P.text_dim).vec4, heading)
        if row.detail:
            body, dropped = clip(row.detail, _MAX_DETAIL_CHARS)
            author(body)
            if dropped:
                imgui.text_colored(P.text_dim.vec4, f"... {dropped} more character(s) not shown")
        if imgui.small_button(f"amend##{row.id}"):
            # One editor, so one draft: opening this row's abandons whatever was
            # half-typed on another, which would otherwise come back the next time
            # that row is amended, as text the operator had walked away from.
            pane.editing = row.id
            pane.drafts = {row.id: row.detail}
            pane.property_drafts = {row.id: row.property}
            pane.stop_closing()
        if closable(row):
            imgui.same_line()
            if imgui.small_button(f"{CLOSE}##close-{row.id}"):
                # The same one-editor rule from the other side: a half-written
                # amendment does not sit behind the close box to be sent later.
                pane.stop_closing()
                pane.closing = row.id
                pane.editing = None
                pane.drafts = {}
                pane.property_drafts = {}
    imgui.tree_pop()


def author(text: str, colour: Color | None = None) -> None:
    """
    Draw text an agent wrote, behind a bar this pane draws beside nothing else.

    **Every body goes through here** -- a result, a not-run list, a note, a
    specification, and the bodies the digest quotes. They are prose with line
    breaks of their author's choosing, and a line of one is otherwise a line of
    the pane: a result can spell a row headline, a standing line and a heading as
    exactly as this module does. So the difference is not left to the words. The
    text is indented by a gutter and the bar is drawn in it, where no character of
    the text can land, and a line without the bar is the pane speaking.

    The bar is a line on the draw list from the top of the text to the bottom of
    what it wrapped to, not a character ahead of each line. ImGui wraps inside
    ``text_colored`` and reports only the finished rectangle, so a per-line mark
    would be on the lines the author broke and missing from the ones the pane's
    width broke -- which in a narrow dock is most of them.

    Public because a decision's texts are drawn through it too
    (``decision_view``): one bar, so "not the application's own words" looks the
    same in every pane.

    Colour is not the channel, so ``colour`` is free to say what it says elsewhere
    in the pane -- an incident's subject is the warning colour here too. No
    palette role is this pane's to spare for quoted text, and ``text`` and
    ``text_strong`` are the same bytes on two themes.
    """
    gutter = imgui.get_font_size() * _GUTTER_EMS
    top = imgui.get_cursor_screen_pos()
    imgui.indent(gutter)
    imgui.text_colored((P.text if colour is None else colour).vec4, author_text(text))
    imgui.unindent(gutter)
    x = top.x + gutter * 0.35
    imgui.get_window_draw_list().add_line(
        imgui.ImVec2(x, top.y),
        imgui.ImVec2(x, imgui.get_item_rect_max().y),
        P.accent.u32,
        _BAR_THICKNESS,
    )


def author_inline(text: str, colour: Color | None = None) -> None:
    """
    ``author`` for a text that finishes a line the pane began: the same bar in the
    same gutter, ahead of the text, at the cursor and not at the indent.

    ``text`` must already be one line that fits what is left of the row
    (``widgets.ellipsis``). Nothing here wraps, so a text that did not fit would
    run past the pane's edge rather than onto a line without the bar.
    """
    gutter = imgui.get_font_size() * _GUTTER_EMS
    top = imgui.get_cursor_screen_pos()
    imgui.set_cursor_screen_pos(imgui.ImVec2(top.x + gutter, top.y))
    imgui.text_colored((P.text if colour is None else colour).vec4, author_text(text))
    x = top.x + gutter * 0.35
    imgui.get_window_draw_list().add_line(
        imgui.ImVec2(x, top.y),
        imgui.ImVec2(x, imgui.get_item_rect_max().y),
        P.accent.u32,
        _BAR_THICKNESS,
    )


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

    **The property has a box of its own above the specification.** It is part of
    what the task is held to, so the operator who can rewrite one can rewrite the
    other; and it is sent only when it was changed, so correcting a paragraph
    leaves the declared sentence standing. Emptying the box withdraws it.

    An over-long property is not applied and the editor stays open saying why. The
    reducer would store it -- it refuses nothing -- and every surface shows a
    property whole, so this is the only place an operator's one can be bounded.
    """
    imgui.text_disabled(f"must not break (one sentence, {MAX_PROPERTY_CHARS} characters at most):")
    _, pane.property_drafts[row.id] = multiline_input(
        f"##amend-property-{row.id}",
        pane.property_drafts.get(row.id, row.property),
        imgui.ImVec2(-1, 44.0),
        wrap=True,
    )
    held_to = normalised_property(pane.property_drafts[row.id])
    too_long = property_overrun_label(held_to)
    if too_long:
        imgui.text_colored(P.warn.vec4, too_long)

    submitted, pane.drafts[row.id] = multiline_input(
        f"##amend-{row.id}",
        pane.drafts.get(row.id, row.detail),
        imgui.ImVec2(-1, 120.0),
        wrap=True,
        flags=CTRL_ENTER_SUBMITS,
    )
    imgui.text_disabled("Ctrl+Enter amends, Enter for a new line")

    applied = (imgui.small_button(f"amend##apply-{row.id}") or submitted) and not too_long
    imgui.same_line()
    cancelled = imgui.small_button(f"cancel##{row.id}")

    if applied:
        # node_id stays None: this is the operator's, and the store reads that as
        # the authority to rewrite a spec an agent is bound by.
        bridge.emit(
            TaskAmended(
                task_id=row.id,
                detail=pane.drafts[row.id],
                property=None if held_to == row.property else held_to,
            )
        )
    if applied or cancelled:
        pane.editing = None
        pane.drafts.pop(row.id, None)
        pane.property_drafts.pop(row.id, None)


# The control that opens the close box. The dots say a question follows: nothing
# is closed by pressing it.
CLOSE = "close..."

# What the close box says before anything is typed, so the press is informed.
#
# Every clause is something the reducer's arm does (``intents.TaskClosed``), and
# the one about a holder says what it does not: nothing here stops an agent.
CLOSE_MEANS = (
    "Closing ends this task without anybody doing it: it is finished as closed by "
    "the operator, with the reason below, and cannot be reopened. Whatever waits on "
    "it stops waiting, and the files it declared no longer hold later tasks back. "
    "An agent holding it is NOT stopped. It is sent the reason, which it reads "
    "when it next reads its inbox, and it is told the task was closed if it tries "
    "to finish or release it; until then it may go on writing those files while "
    "another agent takes them up."
)


def close_reason_overrun_label(reason: str) -> str:
    """
    Why a reason cannot be sent, or nothing when it can: it is past
    ``model.MAX_CLOSE_REASON_CHARS``.

    The reducer does not apply a close with a longer reason and has nobody to
    tell, so the refusal is made here, before the first press, with the count.
    """
    over = len(reason) - MAX_CLOSE_REASON_CHARS
    if over <= 0:
        return ""
    return (
        f"{len(reason):,} characters, {over:,} over the {MAX_CLOSE_REASON_CHARS:,} a "
        "reason may be -- it becomes the task's result and is sent to its holder "
        "whole, and the task is not closed until it is shorter"
    )


# What stands where the reason would be when the operator gave none.
NO_REASON = "(no reason given)"


def closable(row: BoardTask) -> bool:
    """
    Whether the operator can close a row: anything not finished, by anyone.
    """
    return row.state is not TaskState.COMPLETED


def confirm_close_label(row: BoardTask) -> str:
    """
    The second press, which names the task so it cannot be mistaken for the first.
    """
    return f"close {one_line(row.id)} for good"


def _close_editor(row: BoardTask, pane: BoardState, bridge: Bridge) -> None:
    """
    The operator closing a task: a reason, and two presses.

    **The way out of a task nothing else can move** -- held by an agent that will
    not finish, waiting on an id another session holds, amended by the only agent
    that would have worked it. It is here, on the row, because the operator is
    the one participant who can end a task for everybody, and a control an agent
    could reach would be an agent taking work from another.

    Two presses because it cannot be undone and a holder may be mid-task. After
    the first the reason is drawn as text and is no longer a box: what the second
    press sends is exactly what the first was looking at, and a keystroke that
    lands late changes nothing.

    The reason is sent as typed, blank included; the store composes what the
    board shows (``model.closing_result``). One over the bound is not sent at
    all, by either press (``close_reason_overrun_label``). ``draw`` drops this editor when the
    row leaves the board or finishes under it, so neither press can reach a task
    other than the one the box was opened on.
    """
    imgui.text_colored(P.warn.vec4, CLOSE_MEANS)
    reason = pane.close_reasons.get(row.id, "")
    closed = False
    if pane.close_confirming:
        if reason.strip():
            author(reason)
        else:
            imgui.text_disabled(NO_REASON)
        too_long = close_reason_overrun_label(reason)
        if too_long:
            imgui.text_colored(P.warn.vec4, too_long)
        pressed = imgui.small_button(f"{confirm_close_label(row)}##close-confirm-{row.id}")
        closed = pressed and not too_long
        imgui.same_line()
        if imgui.small_button(f"keep editing##close-back-{row.id}"):
            pane.close_confirming = False
    else:
        imgui.text_disabled(
            f"why (kept on the board as you type it, {MAX_CLOSE_REASON_CHARS:,} "
            "characters at most):"
        )
        _, reason = multiline_input(
            f"##close-reason-{row.id}", reason, imgui.ImVec2(-1, 60.0), wrap=True
        )
        pane.close_reasons[row.id] = reason
        too_long = close_reason_overrun_label(reason)
        if too_long:
            imgui.text_colored(P.warn.vec4, too_long)
        if imgui.small_button(f"close##close-apply-{row.id}") and not too_long:
            pane.close_confirming = True
    imgui.same_line()
    cancelled = imgui.small_button(f"cancel##close-cancel-{row.id}")

    if closed:
        bridge.emit(TaskClosed(task_id=row.id, reason=reason))
    if closed or cancelled:
        pane.stop_closing()


def _concerns(rows: Sequence[BoardConcern]) -> None:
    if not rows:
        return
    shown, dropped = bound_rows(rows, _MAX_BOARD_CONCERNS, tail=True)
    section("concerns")
    for row in shown:
        # Participants and subject on separate lines, for the reason the task
        # qualifiers are: the subject is the long part, and anything placed after it
        # with ``same_line`` inherits whatever width the wrap left behind.
        # Waiting mail to a party that is not running is the warning colour: it
        # reads "waiting" on every other row, and this one is waiting for nobody.
        imgui.text_colored((P.warn if row.undeliverable else P.text_dim).vec4, concern_label(row))
        imgui.indent()
        if row.incident:
            imgui.text_colored(P.warn.vec4, incident_marker(True).strip())
        if row.subject:
            # A line that is the sender's from its first character to its last, so
            # it is drawn as one: a subject can otherwise open with the marker
            # above, or with anything else this list says in its own voice.
            author(row.subject, P.warn if row.incident else None)
        else:
            imgui.text_colored(P.text_dim.vec4, NO_SUBJECT)
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

    held_to = property_label(row.property)
    if held_to:
        # First under the headline and never cut, as on the native row: one bounded
        # sentence the operator ranks a row's notes against without opening it.
        # ImGui's own font, which has a fallback: it is an agent's sentence.
        _under_headline(opened)
        imgui.text_colored(P.text.vec4, held_to)
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
    # The native row's other lines, in its words and colours (``_task_row``): a
    # wait nothing here can end, which specification this is and whether its
    # holder has seen it, and on the closed row where a finished one stands.
    for text, colour in _standing_lines(row, opened):
        _under_headline(opened)
        imgui.text_colored(colour.vec4, text)

    if not opened:
        return

    if cut:
        typeset.label(title, colour=P.text)
    if row.touches:
        typeset.label(touches_label(row), weight=Weight.CODE, colour=P.text_dim)
    for reason in reasons:
        alarming = reason.incident or reason.basis is None
        imgui.text_colored((P.warn if alarming else P.accent).vec4, reason_heading(reason))
        if reason.body:
            # Keyed by the row as well: the same concern is drawn again in the log
            # below, and one view cannot be in two places.
            _note(pane, (session_id, "reason", row.id, reason.id), reason.body)
    if row.result:
        heading_colour = (
            P.text_dim if row.closed_by_operator else _basis_colour(row.result_basis, P.accent)
        )
        imgui.text_colored(heading_colour.vec4, result_heading(row))
        _authored(pane, (session_id, "result", row.id), row.result, _MAX_RESULT_CHARS, _SPEC_LINES)
    elif row.state is TaskState.COMPLETED:
        imgui.text_colored(P.warn.vec4, NO_RESULT)
    unrun = unrun_heading(row)
    if unrun:
        imgui.text_colored((P.text_dim if row.not_run == "" else P.warn).vec4, unrun)
        if row.not_run:
            _note(pane, (session_id, "not-run", row.id), row.not_run, _MAX_NOT_RUN_CHARS)
    if pane.closing == row.id:
        _close_editor(row, pane, bridge)
    elif pane.editing == row.id:
        _amend_editor(row, pane, bridge)
    else:
        heading = spec_heading(row)
        if heading:
            imgui.text_colored(_basis_colour(row.basis, P.text_dim).vec4, heading)
        if row.detail:
            _authored(
                pane, (session_id, "spec", row.id), row.detail, _MAX_DETAIL_CHARS, _SPEC_LINES
            )
        _row_controls(row, pane)
    imgui.tree_pop()


def _standing_lines(row: BoardTask, opened: bool) -> list[tuple[str, Color]]:
    """
    The lines a task row draws under its owner line, each with its colour: a
    wait nothing on this board can end, which specification this is, whether its
    holder or its result is working to an earlier one, and -- on the closed row
    only -- where a finished row stands. The typeset row's copy of ``_task_row``'s
    choices; a test holds the two branches to one set of lines.
    """
    lines: list[tuple[str, Color]] = []
    foreign = foreign_wait_label(row)
    if foreign:
        lines.append((foreign, P.warn))
    replaced = one_line(version_label(row))
    if replaced:
        lines.append((replaced, P.text_dim))
    stale = one_line(stale_label(row))
    if stale:
        lines.append((stale, P.warn))
    standing = "" if opened else standing_label(row)
    if standing:
        lines.append((standing, P.text_dim if settled(row) else P.warn))
    return lines


def _row_controls(row: BoardTask, pane: BoardState) -> None:
    """
    The amend button and, on a row that is not finished, the close button: the
    native row's (``_task_row``), with its one-editor rule.
    """
    if imgui.small_button(f"amend##{row.id}"):
        pane.editing = row.id
        pane.drafts = {row.id: row.detail}
        pane.property_drafts = {row.id: row.property}
        pane.stop_closing()
    if closable(row):
        imgui.same_line()
        if imgui.small_button(f"{CLOSE}##close-{row.id}"):
            pane.stop_closing()
            pane.closing = row.id
            pane.editing = None
            pane.drafts = {}
            pane.property_drafts = {}


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


def _fitted(text: str, width: float, *, whole: str = "", colour: Color | None = None) -> bool:
    """
    A strong line cut to ``width``, whole on hover. Returns whether it was cut.

    ``whole`` is what the hover says of a line that left something out without
    being cut, and says in place of ``text`` when it was.
    """
    with typeset.font(Weight.BOLD):
        fitted = ellipsis(text, width)
        imgui.text_colored((P.text if colour is None else colour).vec4, fitted)
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

    if row.incident:
        imgui.text_colored(P.warn.vec4, incident_marker(True).strip())
    if cut:
        typeset.label(subject, colour=P.warn if row.incident else P.text)
    about = "" if row.task_missing else about_label(row)
    said = (participants_label(row), concern_state_label(row), basis_label(row.basis), about)
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
    ink = P.warn if row.incident else P.text
    cut = _fitted(subject, (end - gap if mark else end) - x, whole=whole, colour=ink)
    if mark:
        imgui.same_line(end)
        typeset.label(
            mark, weight=Weight.REGULAR, colour=P.warn if row.incident else getattr(P, role)
        )
    return cut


def _note(pane: BoardState, key: _Key, body: str, limit: int = _MAX_CONCERN_CHARS) -> None:
    _authored(pane, key, body, limit, _NOTE_LINES)


def _authored(pane: BoardState, key: _Key, text: str, limit: int, lines: int) -> None:
    """
    ``_body`` for a text an agent wrote, behind the bar ``author`` draws beside
    one on the native row: in the same gutter, from the top of the view to its
    foot. A line without the bar is the pane speaking, under either renderer.
    """
    gutter = imgui.get_font_size() * _GUTTER_EMS
    top = imgui.get_cursor_screen_pos()
    imgui.indent(gutter)
    _body(pane, key, text, limit, lines)
    imgui.unindent(gutter)
    foot = imgui.get_cursor_screen_pos().y - imgui.get_style().item_spacing.y
    if foot > top.y:
        x = top.x + gutter * 0.35
        imgui.get_window_draw_list().add_line(
            imgui.ImVec2(x, top.y), imgui.ImVec2(x, foot), P.accent.u32, _BAR_THICKNESS
        )


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


class Tone(enum.Enum):
    """How one line is coloured. A closed set, so a new one is drawn."""

    HEADING = "heading"
    ITEM = "item"
    PROSE = "prose"
    # Text an agent wrote, quoted whole. Drawn by ``author`` and not by colour.
    AUTHOR = "author"
    ALARM = "alarm"
    DIVIDER = "divider"


@dataclass(frozen=True, slots=True)
class Line:
    """
    One line a pane composes: its text and how it is coloured.

    The lines above this pane's rows are these, and so is every row of the digest
    (``digest_pane``), which draws them through ``tone_colour`` and ``author``.
    """

    text: str
    tone: Tone


def _basis_colour(basis: Basis | None, stated: Color) -> Color:
    """
    The colour of a heading that carries a basis: ``stated`` when its author gave
    one, the warning colour when it did not.

    "basis unstated" in a heading's ordinary colour is the absence drawn as though
    it were a value. The words are there either way; the colour is what a reader
    scanning a column of headings takes in.
    """
    return P.warn if basis is None else stated


def tone_colour(tone: Tone) -> Color:
    match tone:
        case Tone.HEADING:
            return P.accent
        case Tone.ITEM | Tone.AUTHOR:
            return P.text
        case Tone.PROSE | Tone.DIVIDER:
            return P.text_dim
        case Tone.ALARM:
            return P.warn
        case _:  # pragma: no cover - mypy's exhaustiveness check, not a runtime path
            assert_never(tone)


# Above a board drawn for a session whose template has none.
NOT_A_TEAM_WITH_A_BOARD = (
    "this launch is not a team, and the session holds a board anyway: the rows below"
    " still occupy their ids and their holders"
)


# In place of the board of a session recalled from its file. A closed card shows
# no board: its log is read when the session is resumed and not before.
CLOSED_BOARD = "closed — its board is not loaded until the session is resumed"
# In place of the board of a session the operator took off the fleet. The cursor
# does not rest on one (``focus.FocusState.node``); a caller that hands one over
# anyway is told what it is and is not shown rows as though it were a launch
# that "is not a team".
OFF_FLEET_BOARD = "removed from the fleet — its board is not shown"


def absent_line(snap: Snapshot, session_id: str) -> str | None:
    """
    What BOARD and DIGEST say in place of a board that is not theirs to draw, or
    None for a session whose board is.

    Asked before ``shows_board`` by both, and the order is the point. For these
    two sessions ``has_board`` is false, and "not a team" is what the panes say
    about a false: of a recalled team that is wrong, and of a removed session's
    board, which stays in the store holding its ids, it would be the line that
    explains rows "this launch is not a team" drawn above them.
    """
    record = snap.get((session_id, None))
    if record is not None and record.recalled is not None:
        # "Until it is resumed" is a promise, and not one to make of a session
        # whose transcript is gone: that one gets the line that says so.
        if record.recalled.transcript is TranscriptPresence.GONE:
            return closed_line(record)
        return CLOSED_BOARD
    if snap.off_fleet(session_id):
        return OFF_FLEET_BOARD
    return None


def shows_board(team: bool, tasks: int, concerns: int) -> bool:
    """
    Whether a session's board is drawn: it is a team, or it holds board facts.

    The second is a team session resumed under the solo template. Its restored
    tasks are in the store whichever template the launch named -- held, and
    holding their ids against every other session -- and a pane that read only
    the template would show none of it.
    """
    return team or tasks > 0 or concerns > 0


# Ids named in one line about a restore, before the rest are counted.
_MAX_UNPLACED_IDS = 6
# Of one id in that line. An id is whatever its lead typed, and this line is not
# where a long one is read: the row or the log is.
_MAX_ID_CHARS = 60
# Of one reason a writer gave for stopping, which is an exception's text.
_MAX_REASON_CHARS = 200


def when_label(wall: float) -> str:
    """A wall-clock instant as the operator's own clock would have read it."""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(wall))


def _reason(text: str) -> str:
    """One reason, on one line and bounded, with the cut said."""
    shown, dropped = clip(one_line(text), _MAX_REASON_CHARS)
    return f"{shown} (+{dropped} more characters)" if dropped else shown


def unplaced_reason(left: NotPlaced, off_fleet: Callable[[str], bool] | None = None) -> str:
    """
    Why one logged task or concern is not on the board, in the pane's words.

    The reason's own words, except for an id another session has when that
    session is one the operator took off the fleet. "Another session already has
    this id" sends the operator to look for a session that is on no card; this
    names it and says where it went. ``off_fleet`` is ``Snapshot.off_fleet``,
    asked when the line is drawn because the answer changes while the report
    stands; None is a caller with no snapshot, and the plain reason.
    """
    if (
        left.why is Unplaced.OTHER_SESSION_HAS_ID
        and left.holder is not None
        and off_fleet is not None
        and off_fleet(left.holder)
    ):
        holder = clip(one_line(left.holder), _MAX_ID_CHARS)[0]
        return f"held by a session you removed from the fleet ({holder})"
    return left.why.value


def shortfall(
    restore: Restore,
    stops: Sequence[str] = (),
    off_fleet: Callable[[str], bool] | None = None,
) -> str:
    """
    Everything a session's log held that its board in the store does not show,
    as one clause, or "" when there is nothing.

    One entry per way of falling short, so the reader can tell an id another
    session took from a log that is unreadable past some record. ``stops`` are
    the reasons earlier writers gave for ending the log, where the caller has
    them; the report itself carries only how many.

    Ids are a lead's own words and are flattened like every other one-line field.
    """
    parts: list[str] = []
    if restore.unplaced:
        shown, dropped = bound_rows(restore.unplaced, _MAX_UNPLACED_IDS)
        named = "; ".join(
            f"{left.what} {clip(one_line(left.id), _MAX_ID_CHARS)[0]}:"
            f" {unplaced_reason(left, off_fleet)}"
            for left in shown
        )
        more = f"; and {dropped} more" if dropped else ""
        parts.append(f"{len(restore.unplaced)} not placed ({named}{more})")
    if restore.log_damage is not None:
        parts.append(f"the log is {_reason(restore.log_damage)}")
    if restore.log_stray:
        parts.append(f"{restore.log_stray} record(s) about something the log never introduced")
    if restore.log_unrecognised:
        parts.append(f"{restore.log_unrecognised} record(s) of a kind this build does not know")
    if restore.log_contested:
        parts.append(
            f"{restore.log_contested} record(s) no single writer could have added, left unapplied"
        )
    if restore.log_stopped:
        why = "; ".join(_reason(stop) for stop in stops)
        parts.append(
            f"its writer stopped recording {restore.log_stopped} time(s)"
            + (f" ({why})" if why else "")
            + ", so at least one thing the board held was never written"
        )
    if restore.log_short and not parts:
        parts.append("the log holds more than this board shows, and does not say what")
    return ", ".join(parts)


def restore_line(
    restore: Restore | None,
    state: LogState | None,
    off_fleet: Callable[[str], bool] | None = None,
) -> Line | None:
    """
    The one line that says this board came out of a log, or that a log was read
    and is not this board. None for a session nothing was restored for.

    In the warning colour whenever the log held anything the board does not.
    """
    if restore is None:
        return None
    stops = () if state is None else state.stops
    short = shortfall(restore, stops, off_fleet)
    if restore.refused:
        return Line(
            "this session's log was read and NOT applied: the board here is not the"
            f" one it describes ({short})",
            Tone.ALARM,
        )
    if restore.at is None:
        return Line(
            f"nothing from this session's log could be placed on this board: {short}",
            Tone.ALARM,
        )
    newest = None if state is None else state.newest
    written = "" if newest is None else f" written up to {when_label(newest)}"
    held = f"{restore.tasks} task(s) and {restore.concerns} concern(s)"
    if not short:
        return Line(f"restored from a log{written}: {held}", Tone.PROSE)
    return Line(
        f"restored SHORT from a log{written}: {held}, and the log held more -- {short}",
        Tone.ALARM,
    )


def unrecorded_reason(restore: Restore | None) -> str:
    """
    Why a resumed session's log is not appended to, given what the store made of
    it. "" when it may be: ``Restore.adoptable``.

    The two refusals read alike from the store and are different mistakes, so
    the words name the one this is: a board the store holds that the log does
    not describe is this session's id open in a second window, or a log that
    something else has written to.
    """
    if restore is None:
        return "the store gave no account of the log that was read for it"
    if restore.adoptable:
        return ""
    if restore.refused:
        return (
            "the board held here is not the one its log describes, which is what the"
            " same session id open in another pptmstr window looks like"
        )
    if restore.at is None:
        return "nothing from its log could be placed"
    return (
        "its log holds more than was restored (the line above says what), and a record"
        " written behind a short log would hide that"
    )


def recording_line(
    off: str | None, state: LogState | None, unread: str | None = None
) -> Line | None:
    """
    The one line that says whether what happens to this board is being written
    down, and where. None only while a restore is still being applied.

    A board that is not recorded is said in the warning colour, except where
    that is what the operator chose: ``off``. ``unread`` is the same outcome
    that nobody chose, and it is read first.
    """
    if unread is not None:
        return Line(f"not recorded: {one_line(unread)}", Tone.ALARM)
    if off is not None:
        return Line(f"not recorded ({off})", Tone.PROSE)
    if state is None:
        return Line(
            "this board is not being recorded: no log was opened for this session",
            Tone.ALARM,
        )
    if state.awaiting:
        return None
    lost = f"; {state.unkept} record(s) since are not in it" if state.unkept else ""
    if state.recording:
        if state.unkept:
            return Line(
                f"recorded in {state.directory}, but {state.unkept} record(s) are behind a"
                " break in the log and will not be read back",
                Tone.ALARM,
            )
        return Line(f"recorded in {state.directory} -- kept until you delete it", Tone.PROSE)
    if state.stopped:
        marked = {
            True: "its log says so, and will restore as short",
            False: "its log does NOT say so, and will restore as though it were whole",
            None: "its log was never this window's to write to",
        }[state.marked]
        return Line(
            f"this board is no longer being recorded: {_reason(state.why_not)}{lost} -- {marked}"
            f" ({state.directory})",
            Tone.ALARM,
        )
    return Line(
        f"this session is running UNRECORDED: {one_line(state.why_not)}. Nothing that"
        " happens to this board from here will survive a restart",
        Tone.ALARM,
    )


def log_lines(snap: Snapshot, logging: Logging | None, session_id: str) -> tuple[Line, ...]:
    """
    Every line about one session's board log, routine and alarm alike: whether it
    was restored (``restore_line``) and whether it is recorded (``recording_line``,
    only when ``logging`` was handed over).

    The board header draws the ``Tone.ALARM`` ones and the session details modal
    all of them, so the two cannot word one fact two ways.
    """
    state = None if logging is None else logging.sessions.get(session_id)
    lines = [restore_line(snap.restores.get(session_id), state, snap.off_fleet)]
    if logging is not None:
        lines.append(recording_line(logging.off, state, logging.unread))
    return tuple(line for line in lines if line is not None)


def row_kind(row: BoardTask) -> RowKind:
    """
    Where a row stands for the filter. A pending row with nothing in
    ``blocked_on`` is ``Task.is_claimable``: ``board_tasks`` fills that field
    from ``Task.blocked_on``, which is what ``is_claimable`` is defined by.
    """
    match row.state:
        case TaskState.COMPLETED:
            return RowKind.DONE
        case TaskState.CLAIMED:
            return RowKind.HELD
        case TaskState.PENDING:
            return RowKind.WAITING if row.blocked_on else RowKind.OPEN
        case _:  # pragma: no cover - mypy's exhaustiveness check, not a runtime path
            assert_never(row.state)


def filtered(rows: Sequence[BoardTask], hidden: Collection[RowKind]) -> tuple[BoardTask, ...]:
    """The rows whose kind is not hidden, in board order."""
    return tuple(r for r in rows if row_kind(r) not in hidden)


def showing_label(shown: int, total: int) -> str:
    """What the filter left, said only when it left fewer rows than the board has."""
    return f"showing {shown} of {total}"


def flat_task(row: BoardTask) -> BoardTask:
    """
    A row whose one-line fields are each one line (``one_line``).

    The id is left as the board holds it. It is what a draft and an amendment are
    keyed on, and it is flattened where it is drawn.
    """

    def each(values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(one_line(value) for value in values)

    return dataclasses.replace(
        row,
        title=one_line(row.title),
        owner=None if row.owner is None else one_line(row.owner),
        blocked_on=each(row.blocked_on),
        missing=each(row.missing),
        held_elsewhere=each(row.held_elsewhere),
        touches=each(row.touches),
        property=one_line(row.property),
    )


def flat_concern(row: BoardConcern) -> BoardConcern:
    """A concern whose one-line fields are each one line (``one_line``)."""
    return dataclasses.replace(
        row,
        sender=one_line(row.sender),
        recipient=one_line(row.recipient),
        subject=one_line(row.subject),
        task_id=None if row.task_id is None else one_line(row.task_id),
    )


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


def result_heading(row: BoardTask) -> str:
    """
    What a result is introduced as: whose account it is.

    Attributed rather than bare because a result is the claimer's own report of its
    own work, and it reached the board without the review a specification and a
    note both had (``approval._BUS_AUTO``). The operator reading it here is the
    first person to see it.

    On a row the operator closed it is the operator's and reports no work, and
    the heading says that in place of a basis nobody gave.
    """
    if row.closed_by_operator:
        return f"{closed_label(row)}:"
    said = basis_label(row.result_basis)
    return f"result, as {row.owner} reported it ({said}):" if row.owner else f"result ({said}):"


def unrun_heading(row: BoardTask) -> str:
    """
    Where a finished row stands on what was not run, or nothing for a row that is
    not finished.

    Drawn for every completed row, the unstated ones included and one with no
    result included: a finished row with no line here would read as work that was
    run, on the strength of nobody having said otherwise. A list the claimer gave
    is introduced as the claimer's and follows this line. A row the operator
    closed has no claimer to state anything and says what it is in its result's
    heading.
    """
    if row.not_run:
        return f"not run, as {row.owner} states it:" if row.owner else "not run:"
    if row.state is not TaskState.COMPLETED or row.closed_by_operator:
        return ""
    return not_run_label(row.not_run)


# What a finished row with no account of the work says in place of one.
NO_RESULT = "no result reported"


def settled(row: BoardTask) -> bool:
    """
    Whether a finished row's standing asks nothing of the operator: its claimer
    reported a result, said how it knows it, and said nothing was left unrun.

    All three, because the closed row draws them as one line in one colour. Any
    one unstated -- or a list of what was not run -- and the line is a warning.

    A row the operator closed asks nothing more of them: it was their decision.
    """
    if row.closed_by_operator:
        return True
    return bool(row.result) and row.result_basis is not None and row.not_run == ""


def standing_label(row: BoardTask) -> str:
    """
    What a finished row says of itself without being opened, or nothing for a row
    that is not finished.

    Two statements on one line: how the claimer says it knows what it reported,
    and where it stands on what it did not run. Both are drawn for every completed
    row, the unstated ones included, for the reason ``unrun_heading`` gives -- and
    here more than there, since this is the row as it ships. A finished row with
    no result says that rather than naming a basis for a text that is not there.

    The labels are the ones the opened row and ``read_board`` use, so the closed
    row cannot say something a different way from the row behind it.
    """
    if row.state is not TaskState.COMPLETED:
        return ""
    if row.closed_by_operator:
        # In place of a claimer's two statements, neither of which exists.
        return closed_label(row)
    said = f"result {basis_label(row.result_basis)}" if row.result else NO_RESULT
    return f"{said} · {not_run_label(row.not_run)}"


def property_overrun_label(text: str) -> str:
    """Why a property draft cannot be applied, or nothing when it can."""
    over = property_overrun(text)
    if not over:
        return ""
    return (
        f"{len(text)} characters, {over} over the {MAX_PROPERTY_CHARS} a property may "
        f"be -- it is shown whole on every row, so it stays one sentence"
    )


# What a concern with no subject says in place of one.
NO_SUBJECT = "(no subject)"


def subject_label(row: BoardConcern) -> str:
    """A concern's subject, marked when its sender called it an incident."""
    return f"{incident_marker(row.incident)}{row.subject or NO_SUBJECT}"


def spec_heading(row: BoardTask) -> str:
    """
    What a specification is introduced as: how its author says it knows.

    Drawn for every specification, an unmarked one included. A claim inside a
    specification reads as settled by where it is written, and the line that says
    "basis unstated" is what tells the operator nobody said otherwise.

    A declarer can state a basis and write no text. That basis is on the record,
    so it is shown, as a line that says there is no text for it to describe. With
    neither there is nothing to introduce and this is empty.
    """
    if row.detail:
        return f"specification ({basis_label(row.basis)}):"
    return "" if row.basis is None else f"no specification text ({basis_label(row.basis)})"


def reason_heading(reason: BoardConcern) -> str:
    """A note's subject on its row, with how its sender says it knows."""
    return f"reason ({basis_label(reason.basis)}): {subject_label(reason)}"


def blocked_label(row: BoardTask, limit: int = _MAX_BLOCKED_IDS) -> str:
    """
    What a task is waiting on, or nothing when it is waiting on nothing.

    A dependency naming a task that was never declared is marked, not just listed.
    ``declare_task`` answers "on the board" for a task whose ``depends_on`` names
    an id that does not exist, and ``is_claimable`` deliberately treats that as
    unsatisfied -- so the task is unclaimable forever and this cell is the only
    place the operator can see it. "blocked on t9" is something to wait out;
    "blocked on t9 (never declared)" is something to go and fix.

    An id another session's task has is neither: it was declared, on a board
    this one cannot see, and "never declared" would send the lead to declare an
    id that is refused. It is marked as what it is, and the row's own line says
    what ends the wait (``board.foreign_wait_label``).

    Bounded like everything else here, and says so when it bites.
    """
    if not row.blocked_on:
        return ""
    shown, dropped = bound_rows(row.blocked_on, limit)

    def named(dep: str) -> str:
        if dep in row.held_elsewhere:
            return f"{dep} (another session's)"
        return f"{dep} (never declared)" if dep in row.missing else dep

    ids = ", ".join(named(d) for d in shown)
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

    A concern waiting for a party that is not running says that in place of
    "waiting" (``board.UNDELIVERABLE``): nothing will deliver it.
    """
    return f"{participants_label(row)}  ({concern_state_label(row)}; {basis_label(row.basis)})"


def participants_label(row: BoardConcern) -> str:
    return f"{row.sender} -> {row.recipient}"


def concern_state_label(row: BoardConcern) -> str:
    """
    Where a concern got to, as its line says it: a wait for a party that is not
    running is ``board.UNDELIVERABLE`` and not "waiting".
    """
    state = UNDELIVERABLE if row.undeliverable else _CONCERN_STATE_LABEL[row.state]
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
    withdrawn, or rewritten by the operator on its way through -- and first, one
    its sender marked an incident (``board.incident_marker``).
    """
    parts = [incident_marker(True).strip().lower()] if row.incident else []
    if row.state is not ConcernState.DELIVERED:
        parts.append(UNDELIVERABLE if row.undeliverable else _CONCERN_STATE_LABEL[row.state])
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
