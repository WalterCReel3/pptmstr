"""
DETAIL: the whole of the thing under the cursor, wrapped, with nothing clipped.

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

The session's board is not here. It is ``board_pane``, which ships as a tab-mate
rather than a section -- a starting arrangement the operator is free to re-dock,
not a claim about where the board belongs. The reasoning is in that module.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from imgui_bundle import imgui

from ..approval import diff_line_kind
from ..model import (
    ApprovalNeeded,
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
from . import inbox, review, rich_pane, widgets
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
_MAX_DIFF_LINES = 1200
_MAX_ARG_CHARS = 20_000
# Narration is bounded by line, not by character: wrapped text has no uniform row
# height, so a character count predicts neither how much frame it costs nor how far
# it extends. Unbounded wrapped text is what stalls a frame -- the same bound, for
# the same reason, as transcript_pane's _WRAP_WINDOW. Not a statement about how
# tall the pane is; the panes dock and resize freely.
_NARRATION_LINES = 200
_KIND_LABEL: dict[ObligationKind, str] = {
    ObligationKind.APPROVAL: "wants approval",
    ObligationKind.QUESTION: "is waiting on you",
    ObligationKind.FAILURE: "failed",
}

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


def draw(
    snap: Snapshot,
    focus: FocusState,
    state: review.ReviewState,
    pane: DetailState,
    now: float,
) -> None:
    """Render the cursor's obligation in full. Never reads the store."""
    obligation = focus.obligation(snap)
    if obligation is None:
        _nothing_selected(snap, pane, focus)
        return

    _header(snap, obligation, now)
    imgui.separator()

    if imgui.begin_child("##detail"):
        # 0.0 wraps at the right edge of the content region, so the wrap point
        # tracks the pane as it is resized instead of being frozen at whatever
        # width it had when the obligation arrived.
        imgui.push_text_wrap_pos(0.0)
        match obligation:
            case ApprovalNeeded():
                _approval(state, obligation.approval)
            case QuestionPending():
                _question(snap, pane, obligation)
            case SessionFailed():
                _failure(obligation)
        imgui.pop_text_wrap_pos()
    imgui.end_child()


def _nothing_selected(snap: Snapshot, pane: DetailState, focus: FocusState) -> None:
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
    imgui.text_colored(P.text.vec4, record.task or "this session")
    imgui.pop_text_wrap_pos()

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
    _narration(pane, record.transcript, live=live)


def _deliverable(pane: DetailState, node: NodeId, text: str) -> None:
    """
    A sub-agent's answer, whole and fully rendered.

    Not tailed and not clipped, unlike the narration above it. The narration is a
    view of work in progress and drops its head to stay affordable; this is the
    thing the work was for, and a bound on it would be losing exactly the artifact
    the pane exists to show. It is finite by construction -- one final message from
    one sub-agent -- rather than by policy.
    """
    blocks = pane.deliverable_blocks(node, text)
    if not blocks:
        imgui.text_disabled("it delivered nothing")
        return
    # live=None: the sub-agent has stopped, so there is no in-progress block. The
    # same renderer CONTEXT's RICH mode uses, so a list or a fence cannot look like
    # one thing here and another there.
    rich_pane.draw(pane.rich, blocks, None)


def _narration(pane: DetailState, transcript: Transcript, *, live: bool) -> None:
    """
    The turn's prose as wrapped text rather than as markdown blocks.

    **Deliberately not the block renderer that the question view above uses.**
    Rich rendering wants prose that has settled: a mid-turn parse re-runs on every
    frame a token arrives -- measured at ~1.6ms per KB, so a 4KB turn costs a third
    of the frame budget, continuously -- and the incremental parser that would avoid
    that is a large invariant to maintain for a view whose job is to be glanced at.
    Watching output arrive is what wrapping is good at; reading a settled answer is
    what blocks are good at. The two surfaces differ because the reading does.
    """
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


def _header(snap: Snapshot, obligation: Obligation, now: float) -> None:
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
        imgui.set_clipboard_text(plain_text(snap, obligation))

    imgui.push_text_wrap_pos(0.0)
    imgui.text_colored(P.text_strong.vec4, title)
    imgui.text_colored(P.text_dim.vec4, qualifier)
    if marker is not None:
        imgui.text_colored(P.accent.vec4, marker)
    imgui.pop_text_wrap_pos()


# -- bodies --------------------------------------------------------------------


def _approval(state: review.ReviewState, pending: PendingApproval) -> None:
    """
    The change first, the call that produced it second.

    For a Write or an Edit the diff *is* the decision; ``content`` is a whole-file
    argument that would push it a screen and a half down. Arguments stay below it
    rather than moving out of the pane -- ``file_path`` is worth a glance and the
    Bash case has nothing else -- but they are the thing you scroll *to*, not the
    thing you scroll *past*.
    """
    imgui.text_colored(P.accent.vec4, pending.tool_name)

    if pending.diff:
        _diff(state, pending.id, pending.diff)
    _arguments(pending.raw_args)
    if not pending.diff:
        # Stated rather than omitted, and stated last: a call with no diff is not
        # missing one, and the arguments above are the whole story for it.
        widgets.section("diff")
        imgui.text_disabled("no diff for this call")


def _diff(state: review.ReviewState, pending_id: str, diff: str) -> None:
    lines = state.diff_lines.get(pending_id)
    if lines is None:
        # Split once per pending item, sharing the inbox's cache rather than
        # keeping a second one. A whole-file Write produces a diff as long as the
        # file, and two panes re-splitting it sixty times a second each is work
        # proportional to the change being reviewed -- backwards, since the large
        # diffs are the ones stared at longest.
        lines = diff.splitlines() or [""]
        state.diff_lines[pending_id] = lines

    widgets.section(f"diff · {len(lines):,} lines")
    for line in lines[:_MAX_DIFF_LINES]:
        # text_wrapped has no coloured variant, and the wrap pos pushed by draw()
        # applies to text_colored too -- so this stays text_colored and keeps the
        # +/- gutter as the non-hue channel (design §6.1).
        imgui.text_colored(getattr(P, _DIFF_COLOUR[diff_line_kind(line)]).vec4, line or " ")
    if len(lines) > _MAX_DIFF_LINES:
        imgui.text_colored(
            P.warn.vec4,
            f"… {len(lines) - _MAX_DIFF_LINES:,} more lines not shown here — "
            "the inbox row scrolls the whole diff",
        )


def _arguments(raw_args: Mapping[str, Any]) -> None:
    widgets.section("arguments")
    if not raw_args:
        imgui.text_disabled("(none)")
        return
    for key, value in raw_args.items():
        imgui.text_colored(P.text_dim.vec4, key)
        imgui.indent(12.0)
        text, omitted = clip(render_value(value), _MAX_ARG_CHARS)
        imgui.text_colored(P.text.vec4, text or "(empty)")
        if omitted:
            imgui.text_colored(P.warn.vec4, f"… {omitted:,} more characters not shown")
        imgui.unindent(12.0)


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


def _failure(obligation: SessionFailed) -> None:
    widgets.section("error")
    imgui.text_colored(P.danger.vec4, obligation.error or "no detail recorded")


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


def plain_text(snap: Snapshot, obligation: Obligation) -> str:
    """
    The obligation as text, for the clipboard.

    ImGui text is not selectable, so without this the one thing an operator most
    wants to do with a long Bash command -- paste it into a shell to understand it
    before approving -- is retyping. Unbounded on purpose: the caps above are about
    frame cost, and the clipboard has no frame.
    """
    title, qualifier = inbox.identity(snap, obligation)
    parts = [f"{_KIND_LABEL[obligation.kind]}: {title} ({qualifier})", ""]

    match obligation:
        case ApprovalNeeded():
            pending = obligation.approval
            parts.append(f"tool: {pending.tool_name}")
            if pending.diff:
                parts += ["", pending.diff]
            parts += [""] + _arg_text(pending.raw_args)
        case QuestionPending():
            record = snap.nodes.get(obligation.node)
            if record is not None:
                parts.append(record.transcript.turn_prose())
        case SessionFailed():
            parts.append(obligation.error or "no detail recorded")

    return "\n".join(parts)


def _arg_text(raw_args: Mapping[str, Any]) -> list[str]:
    return [f"  {key} = {render_value(value)}" for key, value in raw_args.items()]
