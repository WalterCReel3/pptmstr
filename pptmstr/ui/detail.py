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
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from imgui_bundle import imgui

from ..approval import diff_line_kind
from ..model import (
    ApprovalNeeded,
    Obligation,
    ObligationKind,
    PendingApproval,
    QuestionPending,
    SessionFailed,
    Snapshot,
)
from ..theme import OBLIGATION_GLYPH, P
from . import inbox, review
from .focus import FocusState
from .widgets import format_elapsed

# Bounds. Generous, because a truncating detail pane is a contradiction, and every
# one of them announces itself in the pane when it bites.
_MAX_DIFF_LINES = 1200
_MAX_ARG_CHARS = 20_000
_TAIL_CHARS = 12_000

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


def draw(snap: Snapshot, focus: FocusState, state: review.ReviewState, now: float) -> None:
    """Render the cursor's obligation in full. Never reads the store."""
    obligation = focus.obligation(snap)
    if obligation is None:
        _nothing_selected(snap, focus)
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
                _question(snap, obligation)
            case SessionFailed():
                _failure(obligation)
        imgui.pop_text_wrap_pos()
    imgui.end_child()


def _nothing_selected(snap: Snapshot, focus: FocusState) -> None:
    """
    No obligation under the cursor. Say which session it *is* on rather than going
    blank, so an empty pane is never confused with a broken one.
    """
    node = focus.node(snap)
    record = snap.nodes.get(node) if node is not None else None
    if record is None:
        imgui.text_disabled("nothing needs you")
        return
    imgui.push_text_wrap_pos(0.0)
    imgui.text_colored(P.text_dim.vec4, "nothing waiting on you from")
    imgui.text_colored(P.text.vec4, record.task or "this session")
    imgui.pop_text_wrap_pos()


def _header(snap: Snapshot, obligation: Obligation, now: float) -> None:
    """
    Whose obligation, of what kind, waiting how long -- the row's own columns, but
    unclipped and allowed to wrap onto as many lines as the title needs.
    """
    colour = P.obligation(obligation.kind)
    title, qualifier = inbox.identity(snap, obligation)

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
    imgui.pop_text_wrap_pos()


# -- bodies --------------------------------------------------------------------


def _approval(state: review.ReviewState, pending: PendingApproval) -> None:
    imgui.text_colored(P.accent.vec4, pending.tool_name)

    _section("arguments")
    if pending.raw_args:
        for key, value in pending.raw_args.items():
            imgui.text_colored(P.text_dim.vec4, key)
            imgui.indent(12.0)
            text, omitted = clip(render_value(value), _MAX_ARG_CHARS)
            imgui.text_colored(P.text.vec4, text or "(empty)")
            if omitted:
                imgui.text_colored(P.warn.vec4, f"… {omitted:,} more characters not shown")
            imgui.unindent(12.0)
    else:
        imgui.text_disabled("(none)")

    if not pending.diff:
        # Not a gap. A Bash command or a network call has nothing diff-shaped to
        # show, and the arguments above are the whole story for it.
        _section("diff")
        imgui.text_disabled("no diff for this call")
        return

    lines = state.diff_lines.get(pending.id)
    if lines is None:
        # Split once per pending item, sharing the inbox's cache rather than
        # keeping a second one. A whole-file Write produces a diff as long as the
        # file, and two panes re-splitting it sixty times a second each is work
        # proportional to the change being reviewed -- backwards, since the large
        # diffs are the ones stared at longest.
        lines = pending.diff.splitlines() or [""]
        state.diff_lines[pending.id] = lines

    _section(f"diff · {len(lines):,} lines")
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


def _question(snap: Snapshot, obligation: QuestionPending) -> None:
    record = snap.nodes.get(obligation.node)
    if record is None:
        imgui.text_disabled("this session is gone")
        return
    _section("what it said")
    tail = record.transcript.tail(_TAIL_CHARS)
    if tail.strip():
        imgui.text_colored(P.text.vec4, tail)
    else:
        imgui.text_disabled("no output on this turn")
    imgui.spacing()
    imgui.text_disabled("reply, interrupt or close from the inbox row")


def _failure(obligation: SessionFailed) -> None:
    _section("error")
    imgui.text_colored(P.danger.vec4, obligation.error or "no detail recorded")


def _section(label: str) -> None:
    imgui.spacing()
    imgui.separator()
    imgui.text_colored(P.text_dim.vec4, label)
    imgui.spacing()


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
            parts.extend(_arg_text(pending.raw_args))
            if pending.diff:
                parts += ["", pending.diff]
        case QuestionPending():
            record = snap.nodes.get(obligation.node)
            if record is not None:
                parts.append(record.transcript.tail(_TAIL_CHARS))
        case SessionFailed():
            parts.append(obligation.error or "no detail recorded")

    return "\n".join(parts)


def _arg_text(raw_args: Mapping[str, Any]) -> list[str]:
    return [f"  {key} = {render_value(value)}" for key, value in raw_args.items()]
