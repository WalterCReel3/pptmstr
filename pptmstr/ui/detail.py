"""
DETAIL: the whole of the thing under the cursor, with nothing clipped.

**This is not the DETAIL pane the inbox replaced.** That one was a second cursor:
``review.selected`` was independently assignable, so the diff on screen could
belong to a different agent than the row about to be approved. This pane reads
``focus.obligation`` and has no selection of its own -- like CONTEXT, it is a
projection of the one cursor, and it offers no way to move it. Nothing here can
disagree with the inbox, because there is nothing here to disagree *with*.

It exists because the inbox row is a **scanning** surface and clipping is what
makes scanning work. Fixed columns and ``ellipsis`` are why a queue of twenty rows
is readable at a glance; they are also why a Bash command, a rejected Edit anchor
or a stack trace goes off the right edge. Two surfaces with opposite rules beats
one surface compromising between them.

Two kinds of loss are being repaired here, and only one is about pixels:

* **Pixel clipping.** ``ellipsis`` in the row, ``ChildFlags_.borders`` bodies of a
  fixed height, and no wrap in the expanded raw-args fallback.
* **Clipping at the source.** ``approval.summarize`` cuts to 90 characters before
  the store ever sees the string, so for a Bash call the summary in the row is a
  *lossy* rendering of ``raw_args["command"]`` and no amount of window width
  recovers it. The arguments block below is the only place the full call exists.

Wrapping rules out ``ImGuiListClipper`` -- wrapped rows have no uniform height for
it to step over (see ``transcript_pane`` for the same collision). So everything
here is bounded by count and **says when it truncated**. A pane whose whole purpose
is "nothing is lost" must not quietly lose things at the bottom.

## Two renderers

``typeset.enabled(Pane.DETAIL)`` chooses, per frame. Off, every body is ImGui text
as described above. On, **markdown is set by the typesetting library and
everything else is still ImGui's**, through the seam, in views held in
``DetailState.views``:

* **What the library sets**: a sub-agent's deliverable, a turn's prose settled
  or still arriving, and the prose argument of a parked call (a concern's body,
  a spawn's prompt, a plan). Prose breaks at its spaces, and text can be
  selected and copied from it.
* **What it does not**: a call's other arguments, and an error. Their columns
  are content, and by pptmstr's measurement the library does not keep a
  monospace column outside its diff view. They go through the seam, which
  hands text that is not markdown to ImGui. An argument or an error of several
  lines is set each line as written and the pane's child scrolls sideways to a
  long one; one of **one** line is wrapped to the pane.
* **An approval's diff is the library's diff view where the library that is
  running keeps a column, and this pane's own lines where it does not**
  (``typeset.diff_adopted``, a measurement; ``typeset.diffs`` has the numbers,
  and on the library pptmstr is built against the answer is yes). See
  ``_diff`` for what differs between the two and what may not.
* **A body somebody wrote is read by ``typeset.prose_parts``**, the rule the inbox
  row, the board and the brief use, so a concern reads the same here and in its row.
* **A turn still arriving is a stream**, bounded from its start. See ``_narration``.
* **Nothing is built over more than one frame.** A text is read and laid out in
  the call that first draws it: 15 ms for 400 lines of markdown and 66 ms for
  2,000, once (``scripts/profile_seam.py``, 2026-10-12, load average 0.4); a
  frame after that is 0.09 ms whatever the length, and a new width 7 ms at 2,000
  lines. So the bound on a document is ``review.SETTLED_LINES``, past which it
  shows its head and a button that sets the rest.
* **A parked call's arguments are held to that bound under either renderer**, and
  to no other: each argument is set as far as the bound, the lines past it are
  counted to ``review.body_shown``, and an approve waits until they are asked
  for (``_offer_rest``). This pane can be the only one showing a call, so what
  it leaves out it must be able to bring in.
* The ``copy`` button is unchanged and still carries the whole obligation.

The session's board is not here. It is ``board_pane``, which ships as a tab-mate
rather than a section -- a starting arrangement the operator is free to re-dock,
not a claim about where the board belongs. The reasoning is in that module.
"""

from __future__ import annotations

import pprint
from collections.abc import Hashable, Iterable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from typing import Any, assert_never

from imgui_bundle import imgui

from .. import commandfile, commandtext
from ..approval import DiffText, command_shown, diff_line_kind
from ..command import Command, CommandId, Refused, ran
from ..model import (
    ApprovalNeeded,
    CommandOwed,
    DecisionOwed,
    NodeId,
    Obligation,
    ObligationKind,
    PendingApproval,
    QuestionPending,
    SessionFailed,
    Snapshot,
)
from ..theme import OBLIGATION_GLYPH, P
from ..transcript import SegmentKind, Transcript
from . import command_view, decision_view, inbox, review, rich_pane, typeset, widgets
from .blocks import Block, BlockCursor
from .focus import FocusState
from .widgets import format_elapsed

# Bounds. Generous, because a truncating detail pane is a contradiction, and every
# one of them announces itself in the pane when it bites.
#
# Prose has no bound here: ``rich_pane.draw`` windows by rendered line count and
# announces the drop itself, and a second character-level cap stacked on top of it
# would mean two policies and two differently-worded truncation notices over one
# body of text.
#
# How long a diff this pane draws a wrapped line to an item. Not a cut: a longer
# one is rows in a child that scrolls (``_diff_lines``).
_MAX_DIFF_LINES = 1200
# An error's bound, and only an error's: nothing is decided over one. A parked
# call's arguments are bounded by ``review.SETTLED_LINES`` and the rest offered.
_MAX_ERROR_CHARS = 20_000
# Narration is bounded by line, not by character: wrapped text has no uniform row
# height, so a character count predicts neither how much frame it costs nor how far
# it extends. Unbounded wrapped text is what stalls a frame -- the same bound, for
# the same reason, as transcript_pane's _WRAP_WINDOW. Not a statement about how
# tall the pane is; the panes dock and resize freely.
_NARRATION_LINES = 200
# Typeset only. How far a turn still arriving grows past ``_NARRATION_LINES`` before
# its start is cut again. A stream cannot let go of its head, so a new cut is the
# whole of what is kept read again in one frame: 15 ms for 400 lines, measured.
# Cutting at every line would pay that every line; one cut in two hundred does not.
# The bound itself is for the frames in between: a frame that reads a piece costs
# 0.7 ms with 400 lines held and 1.9 ms with 2,000, and a turn has no length limit.
_NARRATION_STEP = 200
# The narrowest content region in which two columns of a diff each hold a line of
# code: below it side-by-side is two columns of horizontal scrolling.
_SIDE_BY_SIDE_PX = 1000.0
# The longest single line set broken at its spaces; a longer one is one run of
# characters and is cut at the pane's edge.
_SENTENCE_CHARS = 2000
_EDITED_NOTICE = (
    "you have edited these arguments: everything below is the call as the agent "
    "made it, and approving sends YOUR EDIT instead"
)
_KIND_LABEL: dict[ObligationKind, str] = {
    ObligationKind.APPROVAL: "wants approval",
    ObligationKind.QUESTION: "is waiting on you",
    ObligationKind.FAILURE: "failed",
    ObligationKind.DECISION: "wants a decision",
    ObligationKind.COMMAND: "asks you to run a command",
}
# Ended commands listed for one session, newest first.
_MAX_ENDED = 20

_DIFF_COLOUR = {
    "add": "diff_add",
    "remove": "diff_remove",
    "meta": "accent",
    "context": "diff_context",
}


@dataclass(frozen=True, slots=True)
class _ProseLine:
    """
    The two members ``blocks._LineLike`` asks for, and no more.

    Deliberately not ``transcript_pane.Line``: that carries a run index, which is
    the other pane's way of colouring a kind change across a mixed stream. Turn
    prose is ``OUTPUT`` by construction, so there is no kind change to colour and no
    run to carry. The structural protocol exists so the two panes can each hand the
    parser their own line type without either importing the other's cache types.
    """

    kind: SegmentKind
    text: str


@dataclass
class DetailState:
    """
    Presentation state for this pane (design §6). Never enters the store.

    One memo, not a dict keyed by node: DETAIL renders whichever single obligation
    the cursor is on, so a second entry could only ever be the one the cursor just
    left. That is also why there is no frame-based sweep here as ``TranscriptState``
    has -- the key changing *is* the eviction.
    """

    rich: rich_pane.RichState = field(default_factory=rich_pane.RichState)
    # Whether the narration view is pinned to the newest line. Owned here rather
    # than derived per frame -- see widgets.follow_tail for why scroll position
    # cannot answer this while the view is being pinned.
    narration_follow: bool = True
    # (node, published_length) of the parse held in _blocks. The transcript is
    # append-only, so a length that has not moved means bytes that have not moved.
    _key: tuple[NodeId, int] | None = None
    _blocks: tuple[Block, ...] = ()
    # (node, text) of the parse held in _deliverable_blocks. A second memo rather
    # than a second use of the first: the two parse different strings for the same
    # node -- the turn's prose and the answer handed over on the stop hook -- so one
    # slot would re-parse on every frame that drew both. Keyed by the text and not
    # by its length; see deliverable_blocks.
    _deliverable_key: tuple[NodeId, str] | None = None
    _deliverable_blocks: tuple[Block, ...] = ()
    # The typeset views, this pane's own: a view holds one scroll position and one
    # origin, so the inbox drawing the same parked call holds another. Keyed
    # ``(subject, part)`` and pruned in ``draw`` -- see ``live_subjects``.
    views: typeset.ViewCache = field(default_factory=typeset.ViewCache)
    # The one document the operator asked to have set past review.SETTLED_LINES:
    # a settled text, or a parked call's arguments.
    whole: Hashable | None = None
    # Whether a diff the library draws is two columns, where the pane has the
    # room to offer it.
    side_by_side: bool = False
    # (node, published_length) of the text held in _turn_text; as _key above.
    _turn_key: tuple[NodeId, int] | None = None
    _turn_text: str = ""
    # (pending id, columns, asked for whole) of the parts held in _arguments, with
    # what they leave unset. The same tuple is handed to the seam every frame,
    # which is what lets it skip comparing them.
    _arguments_key: tuple[str, int, bool] | None = None
    _arguments: tuple[typeset.Part, ...] = ()
    _arguments_unset: tuple[int, int] = (0, 0)
    # Where the turn still arriving is cut: (node, step, the text just ahead of the
    # cut) and (characters, lines) ahead of it. See ``saying``.
    _saying_key: tuple[NodeId, int, str] | None = None
    _saying_cut: tuple[int, int] = (0, 0)
    # The text last handed to the stream, so that a frame in which nothing arrived
    # hands it nothing.
    _saying_text: str | None = None

    def prose_blocks(self, node: NodeId, transcript: Transcript) -> Sequence[Block]:
        """
        This turn's prose as markdown blocks, re-parsed only when it changes.

        A throwaway ``BlockCursor`` rather than a retained one, because a question
        is a *finished* turn: the model has stopped and is waiting, so there is no
        live tail for ``live_block`` to track and no incremental state worth keeping
        between frames. Sharing ``TranscriptState``'s cursor was the alternative and
        is worse three ways -- it parses from line 0, so the cost would grow with
        the session that ``turn_prose`` exists to stop paying for; it is fed only in
        RICH mode, so the from-scratch parse is the *common* case here rather than
        the rare one; and touching that cache would keep the largest object this UI
        holds alive for a node nobody is looking at.

        On a settled turn the key stops moving, so this is one parse and then
        pointer comparisons for as long as the operator reads it.
        """
        key = (node, transcript.published_length)
        if self._key != key:
            cursor = BlockCursor()
            cursor.feed(
                [
                    _ProseLine(SegmentKind.OUTPUT, text)
                    for text in transcript.turn_prose().split("\n")
                ]
            )
            # The turn is over, so the trailing paragraph has no next line coming to
            # finalise it -- and it is usually the question itself.
            cursor.finish()
            self._key = key
            self._blocks = tuple(cursor.blocks)
        return self._blocks

    def deliverable_blocks(self, node: NodeId, text: str) -> Sequence[Block]:
        """
        A sub-agent's handed-over answer as markdown blocks.

        Keyed by the text, where ``prose_blocks`` next door is keyed by length.
        The difference is not an inconsistency: a transcript is append-only, so a
        length that has not moved is bytes that have not moved, and a deliverable is
        *replaced* whole, so its length says nothing about its identity. A sub-agent
        woken by a sibling's message answers a second time, and a second answer the
        same length as the first would otherwise render the first.

        The record hands over the same ``str`` object every frame, so the comparison
        is an identity check in the case that runs sixty times a second, and a full
        one only when the answer has actually been replaced.
        """
        key = (node, text)
        if self._deliverable_key != key:
            cursor = BlockCursor()
            cursor.feed([_ProseLine(SegmentKind.OUTPUT, line) for line in text.split("\n")])
            # Nothing more is coming: the sub-agent has stopped. So the trailing
            # paragraph is finalised here rather than left open for a next line.
            cursor.finish()
            self._deliverable_key = key
            self._deliverable_blocks = tuple(cursor.blocks)
        return self._deliverable_blocks

    def turn_text(self, node: NodeId, transcript: Transcript) -> str:
        """
        This turn's prose, read from the transcript only when it has grown.

        The same string object while nothing moved, so the seam's "has the source
        changed" is an identity check on the frames that draw a settled question.
        """
        key = (node, transcript.published_length)
        if self._turn_key != key:
            self._turn_key = key
            self._turn_text = transcript.turn_prose()
        return self._turn_text

    def saying(self, node: NodeId, text: str) -> tuple[str, int]:
        """
        A turn still arriving from where it is cut, and how many lines are ahead of
        the cut.

        ``narration_cut`` reads the turn from its start, so it is asked once per
        ``_NARRATION_STEP`` lines and not once a frame: a turn only grows, so a cut
        made at one length holds until the next step. The few characters ahead of
        the cut are kept and compared, because the same node at the same step is
        not always the same turn -- the cursor can leave during one long turn and
        come back during the next.
        """
        step = max(0, text.count("\n") + 1 - _NARRATION_LINES) // _NARRATION_STEP
        held = self._saying_key
        at = self._saying_cut[0]
        if held is None or held[:2] != (node, step) or text[max(0, at - 80) : at] != held[2]:
            at, dropped = self._saying_cut = narration_cut(
                text, step * _NARRATION_STEP if step else 0
            )
            self._saying_key = (node, step, text[max(0, at - 80) : at])
        return text[at:], self._saying_cut[1]

    def arguments(
        self, pending: PendingApproval, columns: int, whole: bool
    ) -> tuple[tuple[typeset.Part, ...], tuple[int, int]]:
        """
        ``argument_parts`` for a parked call and what they leave unset
        (``lines_past_bound``), built once per call, pane width and asking.

        A parked call's arguments never change -- an edit is a draft in
        ``ReviewState``, and a resolved call leaves the queue -- so its id names them.
        """
        key = (pending.id, columns, whole)
        if self._arguments_key != key:
            self._arguments_key = key
            command = review.shown_command(pending)
            self._arguments = argument_parts(
                pending.tool_name, pending.args, columns, whole=whole, command=command
            )
            self._arguments_unset = (
                (0, 0)
                if whole
                else lines_past_bound(
                    _value_text(pending.tool_name, k, v, columns, command)
                    for k, v in pending.args.items()
                )
            )
        return self._arguments, self._arguments_unset


def draw(
    snap: Snapshot,
    focus: FocusState,
    state: review.ReviewState,
    pane: DetailState,
    now: float,
    decisions: decision_view.DecisionState | None = None,
    commands: inbox.CommandRows | None = None,
) -> None:
    """
    Render the cursor's obligation in full. Never reads the store.

    ``decisions`` is the inbox's answer drafts, read here and typed nowhere in
    this pane. None is a caller with no inbox beside it, and then no draft is
    mentioned.

    ``commands`` is what the inbox draws a command's row with, and the same
    object: this pane shows the body the row shows, with every control dead.
    It never calls ``CommandViewState.attend``; the inbox does, once a frame.
    """
    typeset_on = typeset.enabled(typeset.Pane.DETAIL)
    subjects = live_subjects(snap, focus)
    if pane.whole is not None and _subject(pane.whole) not in subjects:
        pane.whole = None
    if typeset_on:
        pane.views.prune([key for key in pane.views.keys() if _subject(key) in subjects])
    elif len(pane.views):
        pane.views.clear()

    obligation = focus.obligation(snap)
    if obligation is None:
        _nothing_selected(snap, pane, focus, decisions, now, commands)
        return

    _header(snap, obligation, now, frozenset() if commands is None else commands.restored)
    imgui.separator()

    if _begin_body("##detail", typeset_on):
        # 0.0 wraps at the right edge of the content region, so the wrap point
        # tracks the pane as it is resized instead of being frozen at whatever
        # width it had when the obligation arrived.
        imgui.push_text_wrap_pos(_visible_edge() if typeset_on else 0.0)
        match obligation:
            case ApprovalNeeded():
                _approval(state, pane, obligation)
            case QuestionPending():
                _question(snap, pane, obligation)
            case SessionFailed():
                _failure(pane, obligation)
            case DecisionOwed():
                decision_view.draw_detail(snap, decisions, obligation)
            case CommandOwed():
                inbox.draw_command(snap, commands, obligation.command, enabled=False)
                imgui.spacing()
                imgui.text_disabled(COMMAND_FROM_ROW)
            case _:
                assert_never(obligation)
        # Whatever kind the cursor is on: a session's answered decisions and its
        # ended commands have no row in the queue, so this pane is where they
        # are still read.
        decision_view.draw_closed(snap, decisions, obligation.node[0], now)
        draw_ended_commands(snap, commands, obligation.node[0])
        imgui.pop_text_wrap_pos()
    imgui.end_child()


def _visible_edge() -> float:
    """
    Where native text wraps in a body that can scroll sideways: the right edge of
    what is in sight.

    ``push_text_wrap_pos(0.0)`` means the edge of the *content* there, which a long
    literal line makes wider than the pane -- so a notice wrapped to it runs off
    the side, and the notices are the lines that must not.
    """
    return float(imgui.get_cursor_pos_x() + imgui.get_content_region_avail().x)


def _begin_body(label: str, typeset_on: bool) -> bool:
    """
    Open a scrolling child for a body. ``end_child`` is the caller's, as ever.

    For typeset text the vertical scrollbar is always there and a horizontal one is
    allowed. Always, because a view is measured against the child's width, and a
    scrollbar that appeared when the text outgrew the pane would re-break every
    paragraph on that frame. Horizontal, because a literal line of several is
    never broken, and neither is a word of prose wider than the pane: the seam
    tells the window how far such text runs and the window is what reaches it.
    """
    if not typeset_on:
        return bool(imgui.begin_child(label))
    flags = (
        imgui.WindowFlags_.always_vertical_scrollbar.value
        | imgui.WindowFlags_.horizontal_scrollbar.value
    )
    return bool(imgui.begin_child(label, imgui.ImVec2(0.0, 0.0), 0, flags))


def _nothing_selected(
    snap: Snapshot,
    pane: DetailState,
    focus: FocusState,
    decisions: decision_view.DecisionState | None,
    now: float,
    commands: inbox.CommandRows | None = None,
) -> None:
    """
    No obligation under the cursor. Say which session it *is* on rather than going
    blank, so an empty pane is never confused with a broken one -- and then show
    what that session is saying.

    A running agent owes the operator nothing, which is exactly why this branch used
    to stop after two lines. Under the rule the pane is built on -- the row is where
    you act, DETAIL is what informs the act -- mid-turn prose belongs here, because
    the act it informs is the one you are deciding whether to interrupt.
    """
    node = focus.node(snap)
    record = snap.nodes.get(node) if node is not None else None
    if record is None:
        imgui.text_disabled("nothing needs you")
        return
    assert node is not None

    imgui.push_text_wrap_pos(0.0)
    imgui.text_colored(P.text_dim.vec4, "nothing waiting on you from")
    imgui.text_colored(P.text.vec4, widgets.author_text(record.task) or "this session")
    imgui.pop_text_wrap_pos()

    # Above what the session is saying and not below it: the narration takes
    # whatever height is left, so anything after it is never reached.
    decision_view.draw_closed(snap, decisions, node[0], now)
    draw_ended_commands(snap, commands, node[0])

    # is_active is "mid-turn and will emit more", which is the tense this heading
    # needs. An idle or finished session keeps the section and reads in past tense
    # rather than losing the last thing it said.
    live = record.state.is_active
    if not live and record.deliverable:
        # The deliverable *replaces* the narration rather than sitting under it.
        # Both render this node's words, so drawing both would print the answer
        # twice -- and the point of a whole, settled render is that it reads
        # differently from a running tail. Two registers in one pane is one too many.
        widgets.section("what it delivered")
        _deliverable(pane, node, record.deliverable)
        return

    widgets.section("what it is saying" if live else "what it said")
    _narration(pane, node, record.transcript, live=live)


def _deliverable(pane: DetailState, node: NodeId, text: str) -> None:
    """
    A sub-agent's answer, whole and fully rendered.

    Not tailed and not clipped, unlike the narration above it. The narration is a
    view of work in progress and drops its head to stay affordable; this is the
    thing the work was for, and a bound on it would be losing exactly the artifact
    the pane exists to show. It is finite by construction -- one final message from
    one sub-agent -- rather than by policy.
    """
    if typeset.enabled(typeset.Pane.DETAIL):
        if not text.strip():
            imgui.text_disabled("it delivered nothing")
            return
        # A child, although the dock window scrolls: a view culls to the window it
        # is in, and this one is as tall as the pane rather than as the answer.
        if _begin_body("##deliverable", True):
            _settled_markdown(pane, (node, "deliverable"), text)
        imgui.end_child()
        return
    blocks = pane.deliverable_blocks(node, text)
    if not blocks:
        imgui.text_disabled("it delivered nothing")
        return
    # live=None: the sub-agent has stopped, so there is no in-progress block. The
    # same renderer CONTEXT's RICH mode uses, so a list or a fence cannot look like
    # one thing here and another there.
    rich_pane.draw(pane.rich, blocks, None)


def _narration(pane: DetailState, node: NodeId, transcript: Transcript, *, live: bool) -> None:
    """
    What the node has said this turn: watched while it arrives, read once it has.

    **In ImGui's own text it is wrapped text and not the block renderer the
    question view uses.** Rich rendering wants prose that has settled: a mid-turn
    parse re-runs on every frame a token arrives -- measured at ~1.6ms per KB, so a
    4KB turn costs a third of the frame budget, continuously -- and the incremental
    parser that would avoid that is a large invariant to maintain for a view whose
    job is to be glanced at.

    **Typeset sets both halves, each its own way.** A turn that has ended is one
    markdown view, as a question's is. A turn still arriving is a ``typeset.stream``:
    text already committed is laid out once, and the block still open is laid out
    again in each frame that brought more of it -- under a millisecond at the
    lengths ``DetailState.saying`` keeps, which bounds the turn from its start
    because that cost grows with what the stream holds. CONTEXT follows the same
    cursor and shows the same node's text in a view of its own: the two are two
    layouts of one text, and each pays for its own.
    """
    if typeset.enabled(typeset.Pane.DETAIL) and live:
        _live_markdown(pane, node, pane.turn_text(node, transcript))
        return
    if typeset.enabled(typeset.Pane.DETAIL):
        pane.views.drop((node, "saying"))
        pane._saying_text = None
        text = pane.turn_text(node, transcript)
        if not text.strip():
            imgui.text_disabled("nothing said yet this turn")
            return
        if _begin_body("##narration", True):
            _settled_markdown(pane, (node, "narration"), text)
        imgui.end_child()
        return
    prose, dropped = narration_tail(transcript.turn_prose(), _NARRATION_LINES)
    if not prose.strip():
        imgui.text_disabled("nothing said yet this turn")
        return

    if dropped:
        imgui.text_colored(P.warn.vec4, f"… {dropped:,} earlier lines not shown")

    if imgui.begin_child("##narration"):
        imgui.push_text_wrap_pos(0.0)
        imgui.text_colored(P.text.vec4, prose)
        imgui.pop_text_wrap_pos()
        # Only a live turn is worth chasing. On a settled one the flag is left alone
        # so that returning to a running session resumes wherever the operator left
        # it, rather than being silently re-pinned by a session that had stopped.
        if live:
            pane.narration_follow = widgets.follow_tail(pane.narration_follow)
    imgui.end_child()


def _live_markdown(pane: DetailState, node: NodeId, text: str) -> None:
    """
    A turn still arriving, as markdown, pinned to its newest line until the
    operator scrolls up.

    One stream per node, handed the turn from its cut onward whenever the turn has
    grown. Text that extends what the stream holds is appended to it; a new cut, or
    a new turn, is not an extension and is read again from its start, in that frame.

    The child always has its vertical scrollbar and allows a horizontal one: the
    first so that the text does not re-break on the frame it outgrows the pane,
    the second because the seam reports how far a line that cannot be broken
    runs and leaves reaching it to the window.
    """
    if not text.strip():
        imgui.text_disabled("nothing said yet this turn")
        return
    shown, dropped = pane.saying(node, text)
    if dropped:
        imgui.text_colored(P.warn.vec4, f"… {dropped:,} earlier lines not shown")
    stream = typeset.stream(pane.views, (node, "saying"))
    if pane._saying_text is not text or not stream.text:
        stream.set_text(shown)
        pane._saying_text = text
    if _begin_body("##narration", True):
        stream.draw()
        pane.narration_follow = widgets.follow_tail(pane.narration_follow)
    imgui.end_child()


def _settled_markdown(pane: DetailState, key: Hashable, text: str) -> None:
    """
    A finished markdown text, whole unless it is long enough to stall the frame.

    Past ``review.SETTLED_LINES`` the head is set and the rest is one click away,
    with the count said. Not a tail and not a window: this is read from the top,
    and the click sets all of it for as long as the cursor stays.

    Under the bound the text is whole on the frame it is first drawn.
    """
    head, more = review.head_lines(text, review.SETTLED_LINES)
    if not more or pane.whole == key:
        typeset.markdown(pane.views, key, text)
        return
    typeset.markdown(pane.views, key, head)
    _offer_rest(pane, key, more, review.SETTLED_LINES + more)


def _offer_rest(pane: DetailState, key: Hashable, more: int, total: int) -> None:
    """
    Under a document set as far as the bound: how much is not, and the button
    that sets it. The inbox row's words for the same cut (``inbox._offer_whole``).

    One document is whole at a time: asking for another lets the first go back to
    its head, and with it the approve over it, if it is a parked call's.
    """
    imgui.push_text_wrap_pos(_visible_edge())
    imgui.text_colored(
        P.warn.vec4,
        f"… {more:,} more lines not set: all {total:,} would be read and laid out in one frame",
    )
    imgui.pop_text_wrap_pos()
    if imgui.small_button(f"set all {total:,} lines"):
        pane.whole = key


def _header(
    snap: Snapshot,
    obligation: Obligation,
    now: float,
    restored: AbstractSet[CommandId] = frozenset(),
) -> None:
    """
    Whose obligation, of what kind, waiting how long -- the row's own columns, but
    unclipped and allowed to wrap onto as many lines as the title needs.

    The spawn marker comes from ``inbox`` for the same reason ``identity`` does: the
    row and this pane must not be able to disagree about how many sub-agents a yes
    here would make, and one function used twice is the only arrangement in which
    they cannot.
    """
    colour = P.obligation(obligation.kind)
    title, qualifier = inbox.identity(snap, obligation)
    marker = inbox.spawn_marker(snap, obligation)

    imgui.text_colored(colour.vec4, OBLIGATION_GLYPH[obligation.kind])
    imgui.same_line()
    imgui.text_colored(colour.vec4, _KIND_LABEL[obligation.kind])
    imgui.same_line()
    imgui.text_disabled(f"· {format_elapsed(now - obligation.since)}")
    imgui.same_line()
    if imgui.small_button("copy"):
        imgui.set_clipboard_text(plain_text(snap, obligation, restored))

    imgui.push_text_wrap_pos(0.0)
    # The title keeps its author's line breaks: this pane is where a task is read
    # whole, and the wrap gives it the lines.
    imgui.text_colored(P.text_strong.vec4, widgets.author_text(title))
    imgui.text_colored(P.text_dim.vec4, widgets.flat_line(qualifier))
    if marker is not None:
        imgui.text_colored(P.accent.vec4, marker)
    imgui.pop_text_wrap_pos()


# -- bodies --------------------------------------------------------------------


def _approval(state: review.ReviewState, pane: DetailState, obligation: ApprovalNeeded) -> None:
    """
    The change first, the call that produced it second.

    For a Write or an Edit the diff *is* the decision; ``content`` is a whole-file
    argument that would push it a screen and a half down. Arguments stay below it
    rather than moving out of the pane -- ``file_path`` is worth a glance and the
    Bash case has nothing else -- but they are the thing you scroll *to*, not the
    thing you scroll *past*.
    """
    pending = obligation.approval
    imgui.text_colored(P.accent.vec4, pending.tool_name)
    # This pane has no approve button, and it can be the only one showing the
    # call: an ``a`` that was not sent is said here as well as on the inbox row.
    not_sent = review.refusal_for(state, pending.id)
    if not_sent is not None:
        imgui.text_colored(P.warn.vec4, not_sent)

    # Said under either renderer: "what you approve is not what is drawn" is the
    # one line this body may not lose.
    if edit_differs(state, pending):
        imgui.text_colored(P.warn.vec4, _EDITED_NOTICE)
    if pending.shown_args is not None:
        imgui.text_colored(P.warn.vec4, review.ESCAPED)
    # A unified string of white space holds no row and is headed as no diff,
    # whoever would have drawn it.
    has_diff = bool(pending.diff and pending.diff.strip())
    unset = _diff(state, pane, pending, obligation.key) if has_diff else 0
    if typeset.enabled(typeset.Pane.DETAIL):
        unset += _typeset_arguments(pane, pending, obligation.key)
    else:
        unset += _arguments(pane, pending, obligation.key)
    if not has_diff:
        # Stated rather than omitted, and stated last: a call with no diff is not
        # missing one, and the arguments above are the whole story for it.
        widgets.section("diff")
        _may_not_apply(pending.texts)
        imgui.text_disabled("no diff for this call")
    # What ``review.withheld`` holds an approve on: lines of this body the frame
    # did not put on screen. Those past the bound that have not been asked for,
    # under either renderer, and all of a text the library stopped under.
    state.body_shown(review.DETAIL, pending.id, unset, int(imgui.get_frame_count()))


def _typeset_arguments(pane: DetailState, pending: PendingApproval, subject: str) -> int:
    """
    The call's arguments through the seam, as one document. Answers how many
    lines of it the frame did not set: those past the bound and not asked for,
    and all of them when the library stopped (``ViewCache.unbuilt``).

    The prose argument of a call that has one is markdown and the library's;
    every other value is literal and ImGui's (``argument_parts``).
    """
    widgets.section("arguments")
    if not pending.args:
        imgui.text_disabled("(none)")
        return 0
    columns = int(imgui.get_content_region_avail().x // imgui.calc_text_size("M").x)
    key = (subject, "arguments")
    parts, (more, total) = pane.arguments(pending, columns, pane.whole == key)
    typeset.document(pane.views, key, parts)
    if more:
        _offer_rest(pane, key, more, total)
    return (pane.views.unbuilt(key) or 0) + more


def _may_not_apply(texts: Sequence[DiffText] | None) -> None:
    """
    The gate's prediction that an edit has nowhere to land, as native lines that
    wrap at the pane's edge. **Predicts**: it is the gate's read of the file when
    the call parked, and the tool decides on the file as it is when the call runs.
    Then each file the diff shows as new because what is there could not be
    read (``inbox.shown_as_new``).
    """
    for reason in refusals(texts):
        imgui.text_colored(P.warn.vec4, f"may not apply: {reason}")
    for said in inbox.shown_as_new(texts):
        imgui.text_colored(P.warn.vec4, said)


def _diff(
    state: review.ReviewState, pane: DetailState, pending: PendingApproval, subject: str
) -> int:
    """
    What the call would change, under one heading and the gate's prediction.
    Answers how many of its lines the frame did not put on screen: none, unless
    the library stopped under the diff view or set less of the diff than it
    was handed (``ViewCache.unbuilt``).

    **Who draws the rows is the seam's answer** (``inbox.library_diff``, the
    question the inbox row asks of the same call): the library's diff view
    where its columns hold, and otherwise, and whenever that view is not what
    draws this diff, this pane's own lines (``_diff_lines``). The heading and
    the prediction are the same lines either way, said before either.

    **What differs, and is meant to.** This pane's own lines are the unified
    string. The library's view is made from the call's texts, culls to the
    pane, and has no bound: every row of the change is in it, a row is never
    broken, and one wider than the pane is scrolled to by the view's own bar.
    Two columns are offered where the pane is ``_SIDE_BY_SIDE_PX`` wide.

    **What may not differ: every line of the diff can be brought on screen in
    this pane, whoever draws it.** The diff shows what the arguments below do
    not -- the lines a Write removes, the file around an Edit -- so a line
    this pane left out would be one an approver here could not read.
    """
    assert pending.diff is not None
    lines = state.diff_lines.get(pending.id)
    if lines is None:
        # Split once per pending item, sharing the inbox's cache rather than
        # keeping a second one. A whole-file Write produces a diff as long as the
        # file, and two panes re-splitting it sixty times a second each is work
        # proportional to the change being reviewed -- backwards, since the large
        # diffs are the ones stared at longest.
        lines = pending.diff.splitlines() or [""]
        state.diff_lines[pending.id] = lines

    widgets.section(f"diff · {len(lines):,} lines")
    _may_not_apply(pending.texts)
    key = (subject, "diff")
    held = inbox.library_diff(state, pending) if typeset.enabled(typeset.Pane.DETAIL) else None
    if held is not None:
        roomy = imgui.get_content_region_avail().x >= _SIDE_BY_SIDE_PX
        if roomy:
            _, pane.side_by_side = imgui.checkbox("side by side", pane.side_by_side)
        # The prediction is said above, from the record: the view's copy would
        # be in a header row, which is cut at the pane's edge.
        shown = typeset.diff(
            pane.views, key, held, side_by_side=roomy and pane.side_by_side, warning=False
        )
        if shown is not None:
            return pane.views.unbuilt(key) or 0
    else:
        # ``typeset.diff`` says this itself over a diff it hands back.
        hidden = inbox.hidden_in_rows(key, pending)
        if hidden is not None:
            imgui.text_colored(P.warn.vec4, hidden)
    _diff_lines(lines, key)
    return 0


def _diff_lines(lines: Sequence[str], view: Hashable) -> None:
    """
    A unified diff in ImGui's own text, every line of it.

    As far as ``_MAX_DIFF_LINES`` a line is an item, wrapped at the pane's edge.
    Wrapped text is laid out again every frame, so a longer diff is not drawn
    that way: it is the inbox row's rows (``inbox._diff_rows``) -- a line to a
    row, unbroken, in a child that scrolls both ways and draws only the rows
    in sight -- under one line saying so. All of it is in reach either way,
    which is why nothing here is reported as not on screen.
    """
    if len(lines) > _MAX_DIFF_LINES:
        imgui.text_colored(
            P.text_dim.vec4,
            f"{len(lines):,} lines is more than this pane wraps ({_MAX_DIFF_LINES:,}): "
            "every line is in the box below, which scrolls",
        )
        inbox._diff_rows(lines, view)
        return
    for line in lines:
        # text_wrapped has no coloured variant, and the wrap pos pushed by draw()
        # applies to text_colored too -- so this stays text_colored and keeps the
        # +/- gutter as the non-hue channel (design §6.1).
        imgui.text_colored(getattr(P, _DIFF_COLOUR[diff_line_kind(line)]).vec4, line or " ")


def _arguments(pane: DetailState, pending: PendingApproval, subject: str) -> int:
    """
    The call's arguments in ImGui's own text. Answers how many lines of them the
    frame did not draw: those past the bound and not asked for.

    The bound is the typeset path's. Wrapped text is measured again in every
    frame it is drawn, so its cost here is per frame and not once; and the rule
    is one, so a call past it reads the same and waits the same whichever
    renderer is on.

    A Bash command is drawn as ``review.shown_command`` has it, which is how
    every approval surface draws one; every other value as ``shown_value``.
    """
    widgets.section("arguments")
    args = pending.args
    if not args:
        imgui.text_disabled("(none)")
        return 0
    key = (subject, "arguments")
    whole = pane.whole == key
    more = total = 0
    command = review.shown_command(pending)
    for name, value in args.items():
        imgui.text_colored(P.text_dim.vec4, name)
        imgui.indent(12.0)
        if name == "command" and command is not None:
            text, rest = bounded(command, whole)
        else:
            text, rest = bounded(shown_value(pending.tool_name, name, value), whole)
        imgui.text_colored(P.text.vec4, text or "(empty)")
        if rest:
            imgui.text_colored(P.warn.vec4, _not_set(rest))
            more += rest
            total += review.SETTLED_LINES + rest
        imgui.unindent(12.0)
    if more:
        _offer_rest(pane, key, more, total)
    return more


def _question(snap: Snapshot, pane: DetailState, obligation: QuestionPending) -> None:
    record = snap.nodes.get(obligation.node)
    if record is None:
        imgui.text_disabled("this session is gone")
        return
    # The turn's prose, not a byte window over the stream. A question sitting
    # behind a large tool result used to render the tool result under this
    # heading -- the machinery displacing the words in the one pane whose job is
    # to inform the reply.
    widgets.section("what it said")
    if typeset.enabled(typeset.Pane.DETAIL):
        text = pane.turn_text(obligation.node, record.transcript)
        if text.strip():
            _settled_markdown(pane, (obligation.key, "prose"), text)
        else:
            imgui.text_disabled("no output on this turn")
        imgui.spacing()
        imgui.text_disabled("reply, interrupt or close from the inbox row")
        return
    blocks = pane.prose_blocks(obligation.node, record.transcript)
    if blocks:
        # live=None: the turn has ended, so there is no in-progress block. Drawn
        # through the same renderer CONTEXT's RICH mode uses, so a list or a fence
        # cannot look like one thing here and another there.
        rich_pane.draw(pane.rich, blocks, None)
    else:
        imgui.text_disabled("no output on this turn")
    imgui.spacing()
    imgui.text_disabled("reply, interrupt or close from the inbox row")


def _failure(pane: DetailState, obligation: SessionFailed) -> None:
    widgets.section("error")
    if typeset.enabled(typeset.Pane.DETAIL):
        # Literal, never markdown: an error is a traceback or a line from a CLI, and
        # an underscore in a path is an underscore. A traceback is set as its
        # lines are and the pane scrolls sideways to the end of a long one, like
        # an argument of several lines; one line is broken at the pane's edge.
        # Not an approval: the library sets the lines where it keeps their
        # columns (``typeset.views.literal_unfit``), so the line breaks that end
        # a captured stream, which neither renderer shows, are left off.
        text, omitted = clip(obligation.error or "no detail recorded", _MAX_ERROR_CHARS)
        text = text.rstrip("\n") or text
        kind = literal_kind(text, typeset.Role.LITERAL)
        typeset.plain(
            pane.views,
            (obligation.key, "error"),
            text,
            wrap=kind is not typeset.Kind.LITERAL,
            exact=kind is not typeset.Kind.WRAPPED,
            colour=P.danger,
            literal=True,
        )
        if omitted:
            imgui.text_colored(P.warn.vec4, f"… {omitted:,} more characters not shown")
        return
    imgui.text_colored(P.danger.vec4, widgets.author_text(obligation.error or "no detail recorded"))


# -- break-glass commands ---------------------------------------------------------

COMMAND_FROM_ROW = "run, refuse or cancel from its row in the inbox: nothing here acts"
ENDED_COPY = "copy"


def ended_commands(snap: Snapshot, session_id: str) -> tuple[Command, ...]:
    """Every command of a session that has ended, most recently ended first."""
    ended = [
        (c.end.at, c.id, c)
        for c in snap.commands.values()
        if c.session_id == session_id and c.end is not None
    ]
    return tuple(c for _, _, c in sorted(ended, key=lambda e: (e[0], e[1]), reverse=True))


def ended_heading(count: int) -> str:
    return f"commands ended in this session ({count})"


def unread_command_lines(
    snap: Snapshot, session_id: str, restored: AbstractSet[CommandId] = frozenset()
) -> tuple[str, ...]:
    """
    One line for each asker that has not been handed how a command of its ended,
    drawn above the list and outside its triangle: nothing wakes an asker for a
    result, so this is where one that sits unread shows.

    A withdrawal is not among them. Its asker made it (``command_view.delivery_line``).
    Nor is a command in ``restored``: it came from a file, a file does not say
    whether its asker read it, and a warning for every command of an earlier
    run would be one the operator learns to read past. Its own body says
    where it is from (``command_view.delivery_line``).
    """
    by_asker: dict[str, list[str]] = {}
    for command in reversed(ended_commands(snap, session_id)):
        delivery = command_view.delivery_line(command, "")
        if delivery is None or command.first_delivered_at is not None:
            continue
        if command.id in restored:
            continue
        by_asker.setdefault(widgets.one_line(inbox.command_role(snap, command)), []).append(
            widgets.one_line(command.id)
        )
    return tuple(
        f"ended, not yet read by {role}: {', '.join(ids)}" for role, ids in by_asker.items()
    )


def ended_label(snap: Snapshot, command: Command, now: float) -> str:
    """
    The line an ended command is listed under: its id, whose it was and how it
    ended, in this pane's words. Nothing its asker or a program wrote; a
    refusal's reason is the operator's own, on one line.
    """
    role = widgets.one_line(inbox.command_role(snap, command))
    return (
        f"{widgets.one_line(command.id)} from {role}: {command_view.row_state(command, now).text}"
    )


def log_path(command: Command) -> str | None:
    """
    Where the whole of what a command wrote is kept, or None for one that
    started no process and so has no log, or whose ids name no directory.
    """
    if command.end is None or not ran(command.end.outcome):
        return None
    directory = commandfile.command_dir(command.session_id, command.id)
    return None if directory is None else str(directory / commandfile.LOG_NAME)


def draw_ended_commands(snap: Snapshot, rows: inbox.CommandRows | None, session_id: str) -> None:
    """
    The session's ended commands, behind a triangle that starts closed, each
    behind one of its own. Draws nothing for a session with none.

    This is where a command is read after its row has left the queue: what
    ran, how it ended, the end of what it wrote, where the rest is, and
    whether its asker has read any of that. Every body is
    ``command_view.draw_ended``'s.
    """
    ended = ended_commands(snap, session_id)
    if not ended:
        return
    now = 0.0 if rows is None else rows.now
    imgui.spacing()
    for line in unread_command_lines(
        snap, session_id, frozenset() if rows is None else rows.restored
    ):
        imgui.text_colored(P.warn.vec4, line)
    label = f"{ended_heading(len(ended))}###ended-commands"
    if not imgui.tree_node_ex(label, int(imgui.TreeNodeFlags_.span_avail_width)):
        return
    imgui.push_text_wrap_pos(0.0)
    for command in ended[:_MAX_ENDED]:
        imgui.push_id(f"ended:{command.id}")
        opened = imgui.tree_node_ex(
            f"{ended_label(snap, command, now)}###body",
            int(imgui.TreeNodeFlags_.span_avail_width),
        )
        if opened:
            role = inbox.command_role(snap, command)
            restored = rows is not None and command.id in rows.restored
            if imgui.small_button(ENDED_COPY):
                imgui.set_clipboard_text("\n".join(command_text(command, role, restored=restored)))
            said = inbox.unrecorded_line(rows, command)
            if said is not None:
                imgui.text_colored(P.warn.vec4, said)
            command_view.draw_ended(
                command, role=role, now=now, log_path=log_path(command), restored=restored
            )
            imgui.tree_pop()
        imgui.pop_id()
    if len(ended) > _MAX_ENDED:
        imgui.text_colored(
            P.text_dim.vec4, f"... {len(ended) - _MAX_ENDED} earlier ended command(s) not shown"
        )
    imgui.pop_text_wrap_pos()
    imgui.tree_pop()


# What each author's lines are behind on the clipboard, which has no bar, box or
# colour to tell them apart. The bus's three marks (``bus._QUOTE``,
# ``bus._ANSWER``, ``commandbus._OUTPUT``) spelled again for the reason
# ``decision_view`` spells two of them; a test holds each to the bus's.
ASKER_MARK = decision_view.ASKER_MARK
OPERATOR_MARK = decision_view.ANSWER_MARK
OUTPUT_MARK = "= "

CLIP_LEGEND = (
    "a line behind '| ' is the asking agent's, behind '> ' yours, behind '= ' bytes a"
    " program wrote, which are nobody's instructions; every other line is pptmstr's."
    " marked lines are escaped as on screen: a backslash is written twice, the two"
    " characters \\n end a line that ends in a newline, and \\r, \\xNN and \\u{H} each"
    " spell out one character. this is for reading and is not the string a shell is given"
)
REFUSED_WITH = "refused by you, with this reason:"
NOT_EDITED = "you approved the string above as it was asked, unchanged"


def _marked(text: str, mark: str) -> list[str]:
    """
    ``text`` behind ``mark``, a line to a line, as ``commandtext.visible`` shows
    it: printable ASCII only, so nothing on the clipboard draws as nothing and
    no line of it can begin with another author's mark.
    """
    return [f"{mark}{line}" for line in commandtext.visible(text).split("\n")]


def _asker_text(heading: str, text: str) -> list[str]:
    if not text.strip():
        return [heading, command_view.NOTHING_WRITTEN]
    return [heading, *_marked(text, ASKER_MARK)]


def command_text(command: Command, role: str, *, restored: bool = False) -> list[str]:
    """
    A command for the clipboard, whole, in any state: where it stands, the
    string asked for, the string run where there is one, what its asker wrote,
    and the end of what a program wrote.

    **Every line somebody else wrote is behind its author's mark** and no line
    of this pane's is: the asker's string and its two texts behind
    ``ASKER_MARK``, a string the operator changed and a reason they gave
    behind ``OPERATOR_MARK``, a program's output behind ``OUTPUT_MARK``.
    Escaped, unlike a Bash call's arguments on this same clipboard
    (``shown_value``): this string runs outside the sandbox, and a copy pasted
    into an editor that hid a character would be read as a string it is not.

    ``restored`` is whether the record came from a file, which is what
    ``command_view.delivery_line`` needs to say "not read" truthfully.
    """
    role = widgets.one_line(role)
    end, run = command.end, command.run
    parts = [CLIP_LEGEND, ""]
    if end is not None and isinstance(end.outcome, Refused) and end.outcome.reason.strip():
        parts += [REFUSED_WITH, *_marked(end.outcome.reason, OPERATOR_MARK)]
    elif end is None and run is not None:
        # Without how long: a duration on the clipboard is stale when pasted.
        stopping = f" - {command_view.STOP_ASKED}" if command.stop_asked_at is not None else ""
        parts.append(f"running{stopping}")
    else:
        # Asked or ended, and neither wording reads a clock.
        parts.append(command_view.row_state(command, 0.0).text)
    delivery = command_view.delivery_line(command, role, restored=restored)
    if delivery is not None:
        parts.append(delivery.text)
    parts += ["", command_view.asked_heading(role), *_marked(command.asked, ASKER_MARK)]
    if run is not None:
        if command.edited:
            said = command_view.edited_by_you(command_view.stage_of(command))
            parts += ["", said, *_marked(run.text, OPERATOR_MARK)]
        else:
            parts += ["", NOT_EDITED]
    parts += ["", *_asker_text(f"why {role} asks, in its words:", command.reason)]
    parts += _asker_text(f"what {role} expects it to change, in its words:", command.expects)
    if end is not None and ran(end.outcome):
        parts += ["", command_view.output_heading(end.output_bytes, end.tail)]
        if end.tail:
            parts += _marked(end.tail, OUTPUT_MARK)
        where = log_path(command)
        if where is not None:
            parts.append(f"all of it is in {widgets.one_line(where)}")
    return parts


# -- pure helpers, testable without a GL context -------------------------------


def render_value(value: Any) -> str:
    """
    An argument value as the agent meant it, not as Python spells it.

    Strings go through unquoted and unescaped: this pane wraps, so a multi-line
    ``content`` should read as the file it will become. ``repr`` would render every
    newline as ``\\n`` and put the whole of a 200-line Write on one wrapped
    paragraph -- which is the loss this pane exists to undo, reintroduced by the
    formatting.

    Everything else keeps ``repr``, where the quoting is the information: a nested
    dict of edits and the bare word None must not look alike.
    """
    return value if isinstance(value, str) else repr(value)


def shown_value(tool_name: str, key: str, value: Any) -> str:
    """
    An argument as this pane draws it: `render_value`, except for the one
    argument that is run rather than read.

    A Bash command goes through ``approval.command_shown``. This pane wraps,
    so a command's own line break and one the pane made at its edge are the
    same thing on screen unless the command's is marked; the marker ahead of
    each real break is what tells them apart. The clipboard copy
    (`plain_text`) stays the raw string, since that is the one a shell would
    be given.
    """
    shown = command_shown(tool_name, key, value)
    return render_value(value) if shown is None else shown


def clip(text: str, limit: int) -> tuple[str, int]:
    """
    Bound a string, reporting how much was dropped.

    Returns the count rather than appending an ellipsis, so the caller can render
    the omission as its own coloured line. A silent cap in a pane whose premise is
    "nothing is lost" is worse than no pane.
    """
    if len(text) <= limit:
        return text, 0
    return text[:limit], len(text) - limit


def narration_tail(prose: str, limit: int) -> tuple[str, int]:
    """
    The last ``limit`` lines of a turn, and how many were dropped ahead of them.

    Tail-anchored where ``clip`` is head-anchored, for the reason the inbox preview
    is: narration is watched rather than read from the top, and the interesting end
    of a turn in progress is the end. Reports the drop for the same reason every
    other bound in this pane does.
    """
    lines = prose.split("\n")
    if len(lines) <= limit:
        return prose, 0
    dropped = len(lines) - limit
    return "\n".join(lines[dropped:]), dropped


def narration_cut(text: str, ahead: int) -> tuple[int, int]:
    """
    Where to cut a markdown text so that about ``ahead`` lines are left out: the
    characters and the lines ahead of the cut.

    The cut is at the start of a block (``inbox.markdown_blocks``) that begins at
    the margin, so nothing after it reads differently for what was left out: not
    inside a fence, whose second half would be prose, and not at an indented line,
    which belongs to the list item over it. It is the first such block at or after
    line ``ahead``; with none there -- the rest of the text is one fence -- it is the
    last one before it, and more than was asked for is kept.
    """
    if ahead <= 0:
        return 0, 0
    source = text.split("\n")
    starts = [
        first
        for first, _ in inbox.markdown_blocks(source)
        if not source[first].startswith((" ", "\t"))
    ]
    later = [first for first in starts if first >= ahead]
    line = later[0] if later else max(starts, default=0)
    return sum(len(source[n]) + 1 for n in range(line)), line


def bounded(text: str, whole: bool) -> tuple[str, int]:
    """
    One argument of a parked call as far as this pane sets it unasked, and how many
    lines follow: all of it once the operator has asked (``whole``), and otherwise
    as far as the bound the inbox row holds the same call to.
    """
    return (text, 0) if whole else review.head_lines(text, review.SETTLED_LINES)


def lines_past_bound(texts: Iterable[str]) -> tuple[int, int]:
    """
    Over a call's arguments as text: how many lines ``bounded`` leaves out of them
    unasked, and how many lines the arguments it cuts have in all.
    """
    more = total = 0
    for text in texts:
        rest = bounded(text, False)[1]
        if rest:
            more += rest
            total += review.SETTLED_LINES + rest
    return more, total


def _not_set(more: int) -> str:
    """
    What is said in place, under an argument cut at the bound. The button is under
    the last argument (``_offer_rest``); an argument above it must not read as whole.
    """
    return f"… {more:,} more lines not set"


def wraps_at_spaces(text: str) -> bool:
    """
    Whether literal text reads the same broken at its spaces as it does as written.

    The seam's form that breaks at spaces makes a run of them one, and drops a
    line's indentation and a blank line; a word wider than the pane it breaks at
    the edge. So it is for one line of single-spaced words -- a subject, a title, a
    path, a one-line command -- which then breaks between words and not through
    them. Anything else is set whitespace-exact: a command shown with its heredoc's
    indentation removed is a different command.

    A line past ``_SENTENCE_CHARS`` is not a sentence, and is cut at the edge.
    """
    return len(text) <= _SENTENCE_CHARS and "\n" not in text and " ".join(text.split()) == text


def argument_parts(
    tool_name: str,
    raw_args: Mapping[str, Any],
    columns: int,
    *,
    whole: bool = False,
    command: str | None = None,
) -> tuple[typeset.Part, ...]:
    """
    A call's arguments as one document: each name dim and tight over its value.

    One document rather than a view per argument, so a selection runs from a name
    through its value into the next, and the whole call is one layout.

    A value is read as prose only where the argument is prose by the tool's contract
    (``inbox.prose_argument``, the row's own table): a concern's body, a task's
    detail, a spawn's prompt, a plan. Even
    there nothing enforces the contract, so what it is read *as* is the seam's
    answer and not this pane's: ``typeset.prose_parts``, the rule the inbox row uses
    for the same body.

    Every other value is the call verbatim, monospace, in one of three forms, and
    which is a fact about the text and not about the pane, so no value changes form
    as the pane is resized:

    * **several lines** -- a Write's ``content``, an Edit's ``old_string``, a command
      with a heredoc -- as the seam sets the role's (``typeset.multiline_kind``):
      unbroken, each line as written, and the pane scrolls sideways to a long one
      as it does to the diff above. A line of code broken mid-word at the pane's
      edge is harder to read than one that runs on;
    * **one line of single-spaced words** -- a path, a subject, a one-line command
      -- broken at its spaces, which alters nothing (``wraps_at_spaces``);
    * **any other single line** with every space kept and broken wherever the edge
      falls (``Kind.WRAPPED_EXACT``): there is no line structure to keep, and one
      line thousands of characters wide is not read by scrolling.

    A value that is not a string is its Python literal broken on its own structure
    (``_value_text``), and so several lines.

    A subject and a title are among them, in the literal face although the inbox row
    sets them in its prose face. The seam has two ways to the prose face and neither
    sets a subject as it was written: a markdown part reads ``*`` and ``_`` as
    emphasis, and a native line (``typeset.label``) has one font with no fallback,
    so the arrow a model puts in a subject is drawn as U+FFFD -- and is not part of
    this document's selection.

    Each value is set as far as ``bounded`` takes it, as the native block is, and
    says in place what it left out. There is no bound by character: a body of any
    length under the line bound is whole here, as it is in the inbox row.
    """
    prose = inbox.prose_argument(tool_name)
    exact = typeset.Kind.WRAPPED_EXACT
    role = typeset.Role.ARGUMENT
    parts: list[typeset.Part] = []
    for name, value in raw_args.items():
        parts.append(typeset.Part(name or '""', exact, "text_dim", tight=True, role=role))
        text, rest = bounded(_value_text(tool_name, name, value, columns, command), whole)
        if not text:
            parts.append(typeset.Part("(empty)", exact, role=role))
        elif not text.strip():
            # Whitespace alone would be set as nothing at all; its repr is something.
            parts.append(typeset.Part(repr(text), exact, role=role))
        elif name == prose and isinstance(value, str):
            parts.extend(typeset.prose_parts(text))
        else:
            parts.append(typeset.Part(text, literal_kind(text, role), role=role))
        if rest:
            parts.append(typeset.Part(_not_set(rest), exact, "warn", role=role))
    return tuple(parts)


def literal_kind(text: str, role: typeset.Role) -> typeset.Kind:
    """
    The form literal text is set in here: the role's own for several lines, and for
    one line broken at its spaces where that alters nothing and at the edge
    otherwise. ``argument_parts`` says why each.
    """
    if "\n" in text.strip("\n"):
        return typeset.multiline_kind(role)
    return typeset.Kind.WRAPPED if wraps_at_spaces(text) else typeset.Kind.WRAPPED_EXACT


def _value_text(
    tool_name: str, key: str, value: Any, columns: int, command: str | None = None
) -> str:
    """
    ``shown_value`` for a pane that breaks literal text at its edge and nowhere else.

    A Bash command is ``approval.command_shown``'s, as on every approval surface:
    ``command`` where it is given (``review.shown_command``, of the command as
    sent), and otherwise of ``value``.
    Any other string is itself. Anything else is still its Python literal, quoting intact,
    but laid out by ``pprint`` to the pane's width: the ``repr`` of a MultiEdit's
    edits is one line thousands of characters long, and cut at the pane's edge it
    is a block of text with no visible structure.
    """
    shown = command if key == "command" and command is not None else None
    shown = shown if shown is not None else command_shown(tool_name, key, value)
    if shown is not None:
        return shown
    if isinstance(value, str):
        return value
    return pprint.pformat(value, width=max(columns, 20), sort_dicts=False)


def edit_differs(state: review.ReviewState, pending: PendingApproval) -> bool:
    """
    Whether approving this call would send something other than what is drawn.

    This pane always draws the call as the agent made it, and ``review.resolve``
    sends the operator's draft when there is one. Unlike the inbox row's own test,
    an open editor does not excuse it: the editor is in the row, and the diff here
    is still of the original while it is being typed in.

    Serialises the arguments to compare, which for a whole-file Write is the file;
    only reached while a draft exists.
    """
    draft = state.edits.get(pending.id)
    return draft is not None and draft != review.pretty_args(pending)


def refusals(texts: Sequence[DiffText] | None) -> tuple[str, ...]:
    """
    Each distinct reason the gate gives for an edit having no place to land, in the
    order the parts give them.
    """
    return tuple(dict.fromkeys(t.will_not_apply for t in texts or () if t.will_not_apply))


def _subject(key: Hashable) -> object:
    """
    Whose view a key names: the first half of ``(subject, part)``.
    """
    return key[0] if isinstance(key, tuple) and key else key


def live_subjects(snap: Snapshot, focus: FocusState) -> set[object]:
    """
    What this pane keeps views for: every obligation still owed, and the cursor's node.

    Wider than "what is drawn this frame" on purpose. Walking the queue with j/k
    draws one obligation at a time, and a view dropped on leaving is laid out again
    on coming back -- tens of milliseconds for a diff and its arguments, each way.
    An obligation's views go when it is settled; a node's narration and deliverable,
    which no obligation holds, go when the cursor leaves the node.
    """
    subjects: set[object] = {obligation.key for obligation in snap.needs_you}
    node = focus.node(snap)
    if node is not None:
        subjects.add(node)
    return subjects


def plain_text(
    snap: Snapshot, obligation: Obligation, restored: AbstractSet[CommandId] = frozenset()
) -> str:
    """
    The obligation as text, for the clipboard.

    ImGui text is not selectable, so without this the one thing an operator most
    wants to do with a long Bash command -- paste it into a shell to understand it
    before approving -- is retyping. Unbounded on purpose: the caps above are about
    frame cost, and the clipboard has no frame.

    ``restored`` are the commands whose records came from a file
    (``inbox.CommandRows.restored``).
    """
    title, qualifier = inbox.identity(snap, obligation)
    parts = [f"{_KIND_LABEL[obligation.kind]}: {title} ({qualifier})", ""]

    match obligation:
        case ApprovalNeeded():
            pending = obligation.approval
            parts.append(f"tool: {pending.tool_name}")
            if pending.diff:
                parts += ["", pending.diff]
            parts += [""] + _arg_text(pending.args)
        case QuestionPending():
            record = snap.nodes.get(obligation.node)
            if record is not None:
                parts.append(record.transcript.turn_prose())
        case SessionFailed():
            parts.append(obligation.error or "no detail recorded")
        case DecisionOwed():
            parts += decision_view.plain_text(snap, obligation)
        case CommandOwed():
            command = obligation.command
            parts += command_text(
                command, inbox.command_role(snap, command), restored=command.id in restored
            )
        case _:
            assert_never(obligation)

    return "\n".join(parts)


def _arg_text(raw_args: Mapping[str, Any]) -> list[str]:
    return [f"  {key} = {render_value(value)}" for key, value in raw_args.items()]
