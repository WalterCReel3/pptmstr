"""
A decision awaiting the operator: what its body says, and the box that answers it.

Not a pane. A decision is the fourth kind of obligation and is answered where the
other three are -- at its row in the inbox -- with DETAIL carrying the same body
unclipped. Both draw it through here, so the two cannot word a standing or a
reversibility differently.

**Whose words a line is.** Every text on a ``Decision`` is its asker's: the
question, what it blocks, what happens if it goes unanswered, the evidence and
the recommendation. Each is drawn by ``board_pane.author``, behind the bar that
marks a line as not this application's own, and under a heading that is. The
headings, the standing, the reversibility and what became of the asker are
composed here from the record's structure and hold none of its sentences.

**The recommendation is its own block, drawn last.** The record holds it apart
from the evidence so that what is known can be read before what the asker wants
done with it; ``sections`` keeps the two as two entries and ``draw_body`` puts a
rule between them.

**The answer is final and is sent as typed.** The store keeps the first answer
and ignores a second, so sending is two presses: the first shows what is about to
go and holds the box read-only, the second sends the draft exactly as it stands
-- nothing stripped, nothing joined. An answer with nothing to read in it is not
sendable; closing a decision without answering is what dismiss is for.

**A draft is cleared by the store and by nothing here.** Sending emits the intent
and leaves the draft where it is. It goes when the snapshot shows the decision
answered or dismissed, so a draft whose answer did not land -- its asker withdrew
first -- is still in hand, and is shown beside the withdrawn decision until the
operator discards it.

**A closed decision says what the open one did.** Its row has left the queue, so
DETAIL's list is the only place it is read: the standing, the reversibility and
everything its asker wrote is there, and ``closed_text`` puts the whole of one on the
clipboard, the answer included. What the operator sent and its asker has not
read is said above that list, outside the triangle, and on the asker's card in
the rail -- nothing wakes an asker for an answer, so one can sit.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import assert_never

from imgui_bundle import imgui

from ..board import role_name
from ..bridge import Bridge
from ..intents import DecisionAnswered, DecisionDismissed
from ..model import (
    MAX_ANSWER_CHARS,
    Answered,
    AskerFate,
    Decision,
    DecisionId,
    DecisionOwed,
    Dismissed,
    NodeId,
    Snapshot,
    Standing,
    Withdrawn,
)
from ..theme import P
from . import board_pane
from .widgets import format_elapsed, multiline_input

# One text of a decision as the inbox row shows it. The row is scanned; DETAIL
# has the room, and a text cut here says so and says where the rest is.
ROW_CHARS = 1500
# The same text in DETAIL. A bound on frame cost, as that pane's are.
DETAIL_CHARS = 20_000
# Closed decisions listed for one session, newest first.
_MAX_CLOSED = 20

_ROW_BODY_LINES = 16.0
_ANSWER_BOX_HEIGHT = 72.0

# What a line of the asker's is behind on the clipboard, and what a line of the
# operator's answer is behind. The bus's two marks (``bus._QUOTE``, ``bus._ANSWER``)
# spelled again, because importing the bus would bring the SDK into the UI layer;
# a test holds each pair to one answer. Using the operator's mark for the asker
# would make a copied decision read as answered by whoever pasted it.
ASKER_MARK = "| "
ANSWER_MARK = "> "


class Act(enum.Enum):
    """The two ways the operator ends a decision. Each takes a second press."""

    SEND = "send"
    DISMISS = "dismiss"


@dataclass
class DecisionState:
    """
    What the operator is part-way through, keyed by decision id.

    Presentation state, for the reason ``ReviewState`` gives: a half-typed answer
    is not a fact about the world. Keyed by decision and not by node or by row,
    so moving the cursor to another row and back finds the draft as it was left.
    """

    drafts: dict[DecisionId, str] = field(default_factory=dict)
    # The act waiting for its second press, and the decision it is on.
    confirming: tuple[DecisionId, Act] | None = None

    def attend(self, snap: Snapshot, focused: DecisionId | None) -> None:
        """
        Once per frame, before a decision row is drawn.

        Drops the drafts the store has finished with: a decision that is answered
        or dismissed, and one the store no longer holds. A withdrawn decision
        keeps its draft -- the operator did not choose that ending, and the text
        may be worth more than the question was.

        An act waits for its second press only while its row is the one under
        the cursor. Left armed across a change of row, the second press would be
        made by somebody who had since read something else.
        """
        for decision_id in [d for d in self.drafts if not _keeps_draft(snap, d)]:
            del self.drafts[decision_id]
        if self.confirming is not None:
            decision = snap.decisions.get(self.confirming[0])
            if decision is None or not decision.is_open or self.confirming[0] != focused:
                self.confirming = None

    def pending(self, decision_id: DecisionId) -> Act | None:
        """The act waiting for its second press on this decision, if one is."""
        if self.confirming is None or self.confirming[0] != decision_id:
            return None
        return self.confirming[1]


def _keeps_draft(snap: Snapshot, decision_id: DecisionId) -> bool:
    decision = snap.decisions.get(decision_id)
    if decision is None:
        return False
    return decision.is_open or isinstance(decision.outcome, Withdrawn)


# -- pure helpers, testable without a GL context -------------------------------


@dataclass(frozen=True, slots=True)
class Line:
    """One line in this application's own voice. ``warn`` picks the colour."""

    text: str
    warn: bool = False


NOTHING_WRITTEN = "(its asker left this empty)"


@dataclass(frozen=True, slots=True)
class Section:
    """One of the asker's texts, under the heading this module gives it."""

    heading: str
    # The asker's, whole. Never drawn but through ``board_pane.author``.
    text: str
    # What this module says in its place when there is nothing in it to read.
    empty: str = NOTHING_WRITTEN


def asker_role(snap: Snapshot, decision: Decision) -> str:
    """What to call the asker, on one line."""
    return board_pane.one_line(role_name(snap, decision.asker, decision.session_id))


def standing_line(standing: Standing, role: str) -> Line:
    """
    Whose decision it is. A taken one is the warning colour: an answer that
    disagrees with it means work to undo, which an untaken one never does.
    """
    if standing is Standing.OPERATORS:
        return Line("yours to decide: nobody has taken it")
    return Line(f"already taken by {role}, who asks you to ratify it", warn=True)


def reversible_line(reversible: bool | None) -> Line:
    """
    Whether it can be undone, as its asker states it.

    "Did not say" is the warning colour beside "cannot": an asker that left it
    out has not said the decision is safe, and a neutral line would read as if
    it had.
    """
    if reversible is True:
        return Line("its asker says this can be undone")
    if reversible is False:
        return Line("its asker says this cannot be undone", warn=True)
    return Line("its asker did not say whether this can be undone", warn=True)


def fate_line(fate: AskerFate) -> Line | None:
    """
    What an answer will meet, when its asker is not running. None when it is.

    Closed by ``assert_never``: a fate with no arm here would otherwise draw as
    no line at all, which is what a running asker draws.
    """
    match fate:
        case AskerFate.LIVE:
            return None
        case AskerFate.IDLE:
            return Line(
                "its asker has ended its turn: nothing wakes it for an answer, which is"
                " read only once you send that session a prompt",
                warn=True,
            )
        case AskerFate.STOPPED:
            return Line("its asker has stopped: an answer is read only if it is woken", warn=True)
        case AskerFate.UNANNOUNCED:
            return Line(
                "its asker has not been reported as started: it may be working, and"
                " nothing here can say whether it will read an answer",
                warn=True,
            )
        case AskerFate.FAILED:
            return Line("its asker failed: an answer may never be read", warn=True)
        case AskerFate.GONE:
            return Line(
                "its asker's session was closed: nothing will read an answer,"
                " and dismiss closes this",
                warn=True,
            )
        case _:
            assert_never(fate)


def recommendation_line(decision: Decision) -> Line:
    """
    Whether the asker recommended anything, said where it cannot scroll away.

    The recommendation is the last of five blocks and the row's body is a fixed
    height, so without this its existence is below the fold of every decision
    with evidence worth reading.
    """
    if decision.recommendation.strip():
        return Line("its asker recommends an answer: the last block below")
    return Line("its asker recommends nothing", warn=True)


BLOCKS_NOTHING = "(this blocks nothing on the board)"


def blocks_text(decision: Decision) -> str:
    """
    The tasks the decision blocks, one id to a line; empty when it names none.
    The only reader of ``Decision.blocks`` in this module.

    Each id is its asker's spelling of a task on its board, so it is drawn as the
    asker's and made one line first: an id is not checked for line breaks here.
    """
    return "\n".join(board_pane.one_line(task_id) for task_id in decision.blocks)


def standing_facts(decision: Decision, role: str) -> tuple[Line, ...]:
    """What is true of a decision open or closed: whose it is, and whether it undoes."""
    return (standing_line(decision.standing, role), reversible_line(decision.reversible))


def facts(owed: DecisionOwed, role: str) -> tuple[Line, ...]:
    """The lines above the asker's texts, for an open decision."""
    decision = owed.decision
    lines = [*standing_facts(decision, role), recommendation_line(decision)]
    fate = fate_line(owed.asker)
    if fate is not None:
        lines.append(fate)
    return tuple(lines)


def sections(decision: Decision, role: str) -> tuple[Section, ...]:
    """
    The asker's four texts and the tasks it names, in reading order, each under
    its own heading.

    The evidence and the recommendation are two entries and the recommendation
    is the last: what is known, then what the asker wants done with it.
    """
    return (
        Section(f"what {role} asks:", decision.question),
        Section(
            f"the tasks it blocks, as {role} names them:", blocks_text(decision), BLOCKS_NOTHING
        ),
        Section(f"what happens if you do not answer, as {role} states it:", decision.if_unanswered),
        Section(f"the evidence {role} gives:", decision.evidence),
        Section(
            f"what {role} recommends -- its view, apart from the evidence above:",
            decision.recommendation,
        ),
    )


def outcome_label(decision: Decision, now: float) -> str:
    """How a decision ended and how long ago, or that it has not."""
    outcome = decision.outcome
    if outcome is None:
        return "open"
    age = format_elapsed(now - outcome.at)
    if isinstance(outcome, Answered):
        return f"answered by you {age} ago"
    if isinstance(outcome, Dismissed):
        return f"dismissed by you {age} ago, with no answer"
    return f"withdrawn by its asker {age} ago, with no answer"


def delivery_label(decision: Decision, role: str, now: float) -> Line | None:
    """
    Whether the asker has been handed what the operator did. None for a
    decision the operator did nothing to: one still open, and one withdrawn.

    Unread is the warning colour. Nothing wakes an asker for an answer, so an
    answer can sit: this line is the only place that shows.
    """
    outcome = decision.outcome
    if isinstance(outcome, Answered):
        what = "answer"
    elif isinstance(outcome, Dismissed):
        what = "dismissal"
    else:
        return None
    if decision.first_delivered_at is None:
        return Line(f"{what} not yet read by {role}", warn=True)
    age = format_elapsed(now - decision.first_delivered_at)
    return Line(f"{what} first read by {role} {age} ago")


def closed_label(decision: Decision, role: str, now: float) -> str:
    """The line a closed decision is listed under."""
    return f"{board_pane.one_line(decision.id)} from {role}: {outcome_label(decision, now)}"


def closed(snap: Snapshot, session_id: str) -> tuple[Decision, ...]:
    """Every decision of a session that has ended, most recently ended first."""
    ended = [
        (d.outcome.at, d.id, d)
        for d in snap.decisions.values()
        if d.session_id == session_id and d.outcome is not None
    ]
    return tuple(d for _, _, d in sorted(ended, key=lambda e: (e[0], e[1]), reverse=True))


def closed_heading(count: int) -> str:
    return f"decisions closed in this session ({count})"


def unread(snap: Snapshot, session_id: str) -> tuple[Decision, ...]:
    """
    The session's decisions the operator ended and their asker has not been
    handed: answered or dismissed, with no delivery stamp. Oldest ending first.
    """
    waiting = [
        d
        for d in reversed(closed(snap, session_id))
        if isinstance(d.outcome, Answered | Dismissed) and d.first_delivered_at is None
    ]
    return tuple(waiting)


def unread_answers(snap: Snapshot, askers: Callable[[NodeId], bool]) -> int:
    """
    How many answers are waiting to be read by an asker ``askers`` accepts.

    Answers only. A dismissal nobody has read costs its asker nothing it was
    waiting for; an answer nobody has read is work proceeding without it.
    """
    return sum(
        1
        for d in snap.decisions.values()
        if isinstance(d.outcome, Answered) and d.first_delivered_at is None and askers(d.asker)
    )


def unread_mark(count: int) -> str:
    """What a rail card says of answers its agent has not read."""
    return f"{count} answer{'' if count == 1 else 's'} unread"


def unread_lines(snap: Snapshot, session_id: str) -> tuple[Line, ...]:
    """
    One line for each asker that has not read what the operator did, drawn above
    the closed list and outside its triangle.
    """
    by_asker: dict[tuple[str, str], list[str]] = {}
    for decision in unread(snap, session_id):
        what = "answered" if isinstance(decision.outcome, Answered) else "dismissed"
        by_asker.setdefault((what, asker_role(snap, decision)), []).append(
            board_pane.one_line(decision.id)
        )
    return tuple(
        Line(f"{what}, not yet read by {role}: {', '.join(ids)}", warn=True)
        for (what, role), ids in by_asker.items()
    )


def sendable(draft: str) -> bool:
    """
    Whether a draft may be sent as an answer.

    One with nothing in it to read may not. The store would keep it -- ``""`` is
    an answer there -- and the asker would be handed an answer that says nothing,
    for good. The draft is judged on a stripped copy and sent as it is. Nor may
    one longer than the store takes: see ``too_long_hint``.
    """
    return bool(draft.strip()) and len(draft) <= MAX_ANSWER_CHARS


def too_long_hint(draft: str) -> str:
    """
    What the box says of a draft past ``model.MAX_ANSWER_CHARS``, with the count.

    The store refuses such an answer and has nobody to tell, so the refusal is
    made here, before the first press, where the operator can still shorten it.
    """
    return (
        f"this is {len(draft) - MAX_ANSWER_CHARS:,} characters over the {MAX_ANSWER_CHARS:,}"
        " an answer may be, and is not sent until it is shorter"
    )


FINAL_HINT = "an answer is final: once sent it cannot be changed or taken back"
EMPTY_HINT = "an empty answer is not sent. dismiss closes this without one"
YOUR_ANSWER = "you answered -- final, exactly as sent:"
EMPTY_ANSWER = "(the answer on record is empty)"
ANSWER_FROM_ROW = "answer or dismiss from its row in the inbox"

SEND = "send answer..."
DISMISS = "dismiss..."
CONFIRM = {Act.SEND: "send it - final", Act.DISMISS: "dismiss it - final"}
BACK = "keep editing"


def confirm_line(act: Act, role: str, draft: str, fate: AskerFate) -> str:
    """What the second press is about to do, in full."""
    if act is Act.SEND:
        text = (
            f"send the {len(draft):,} characters above to {role}, exactly as they stand?"
            " you cannot change the answer afterwards."
        )
        unread = fate_line(fate)
        return text if unread is None else f"{text} {unread.text}."
    text = (
        f"close this without an answer? {role} is told it was dismissed,"
        " and it cannot be reopened."
    )
    if draft:
        text += f" your draft ({len(draft):,} characters) is discarded."
    return text


def draft_line(draft: str) -> str:
    """What DETAIL says of an answer typed at the row and not sent."""
    return f"your draft is {len(draft):,} characters and has not been sent"


WITHDRAWN_DRAFT = "it was withdrawn with this draft of yours unsent:"
EVERYTHING_ASKED = "everything its asker wrote###asked"
COPY = "copy"


def copy_all(count: int) -> str:
    return f"copy all {count} closed decisions"


def _marked(text: str, mark: str) -> list[str]:
    return [f"{mark}{line}" for line in board_pane.author_text(text).splitlines()]


def _section_text(decision: Decision, role: str) -> list[str]:
    parts: list[str] = []
    for part in sections(decision, role):
        parts += ["", part.heading]
        parts += _marked(part.text, ASKER_MARK) if part.text.strip() else [part.empty]
    return parts


def plain_text(snap: Snapshot, owed: DecisionOwed) -> list[str]:
    """
    The decision for the clipboard, whole.

    The clipboard has no bar to put beside the asker's lines, so each one is
    behind ``ASKER_MARK`` and no line of this module's own is.
    """
    role = asker_role(snap, owed.decision)
    return [line.text for line in facts(owed, role)] + _section_text(owed.decision, role)


def closed_text(snap: Snapshot, decision: Decision, now: float) -> list[str]:
    """
    A closed decision for the clipboard, whole: how it ended, whether its asker
    has read that, everything the asker wrote, and the answer as it was sent.
    """
    role = asker_role(snap, decision)
    parts = [closed_label(decision, role, now)]
    delivery = delivery_label(decision, role, now)
    if delivery is not None:
        parts.append(delivery.text)
    parts += [line.text for line in standing_facts(decision, role)]
    parts += _section_text(decision, role)
    answer = decision.answer
    if answer is not None:
        parts += ["", YOUR_ANSWER]
        # split and not splitlines: an answer is sent as typed, and one that ends
        # in a line break did end in one.
        parts += (
            [f"{ANSWER_MARK}{line}" for line in board_pane.author_text(answer).split("\n")]
            if answer
            else [EMPTY_ANSWER]
        )
    return parts


# -- drawing -------------------------------------------------------------------


def _line(line: Line) -> None:
    imgui.text_colored((P.warn if line.warn else P.text).vec4, line.text)


def _authored(text: str, limit: int, rest: str, empty: str = NOTHING_WRITTEN) -> None:
    """One of the asker's texts, behind the bar, with its bound announced."""
    if not text.strip():
        imgui.text_disabled(empty)
        return
    shown, dropped = board_pane.clip(text, limit)
    board_pane.author(shown)
    if dropped:
        imgui.text_colored(P.warn.vec4, f"... {dropped:,} more characters not shown here - {rest}")


def draw_body(snap: Snapshot, owed: DecisionOwed, *, limit: int, rest: str) -> None:
    """
    The facts and everything the asker wrote. Draws no control.

    ``rest`` finishes the line that says a text was cut: where the whole of it
    is, from wherever this is being drawn.
    """
    role = asker_role(snap, owed.decision)
    for fact in facts(owed, role):
        _line(fact)
    _sections(sections(owed.decision, role), limit=limit, rest=rest)


def _sections(parts: tuple[Section, ...], *, limit: int, rest: str) -> None:
    """Each of the asker's texts under its heading, the last behind a rule."""
    for index, part in enumerate(parts):
        imgui.spacing()
        if index == len(parts) - 1:
            # The recommendation. The rule is what keeps it from reading as the
            # evidence's last paragraph.
            imgui.separator()
        imgui.text_colored(P.text_dim.vec4, part.heading)
        _authored(part.text, limit, rest, part.empty)


def draw_row(
    snap: Snapshot,
    state: DecisionState,
    bridge: Bridge,
    owed: DecisionOwed,
    *,
    wrap: bool = True,
) -> None:
    """The expanded inbox row: the body in a box that scrolls, and the answer."""
    line_h = imgui.get_text_line_height_with_spacing()
    if imgui.begin_child(
        "##body", imgui.ImVec2(-8.0, _ROW_BODY_LINES * line_h), imgui.ChildFlags_.borders
    ):
        imgui.push_text_wrap_pos(0.0)
        draw_body(snap, owed, limit=ROW_CHARS, rest="DETAIL shows more of it")
        imgui.pop_text_wrap_pos()
    imgui.end_child()
    imgui.spacing()
    draw_answer_box(snap, state, bridge, owed, wrap=wrap)


def draw_answer_box(
    snap: Snapshot,
    state: DecisionState,
    bridge: Bridge,
    owed: DecisionOwed,
    *,
    wrap: bool = True,
) -> None:
    """
    The draft, and the two presses that end the decision.

    The box holds the draft and the draft is stored from it every frame. While an
    act waits for its second press the box is read-only and its value is not
    stored back, so what is sent is what was on screen when the first press was
    made.
    """
    decision = owed.decision
    role = asker_role(snap, decision)
    pending = state.pending(decision.id)
    draft = state.drafts.get(decision.id, "")

    imgui.push_id(f"decision:{decision.id}")
    flags = int(imgui.InputTextFlags_.allow_tab_input)
    if pending is not None:
        flags |= int(imgui.InputTextFlags_.read_only)
    _, typed = multiline_input(
        "##answer", draft, imgui.ImVec2(-8.0, _ANSWER_BOX_HEIGHT), wrap=wrap, flags=flags
    )
    # Escape in a text box puts back what it held when the caret entered it, and
    # hands that back as this frame's value. Storing it would be Escape deleting
    # the draft, so that one frame's value is not kept.
    if pending is None and not imgui.is_key_pressed(imgui.Key.escape):
        draft = typed
        state.drafts[decision.id] = draft

    imgui.push_text_wrap_pos(0.0)
    if pending is None:
        ready = sendable(draft)
        if not ready:
            imgui.begin_disabled()
        # ``ready`` and not the click alone: a disabled button is greyed, and the
        # rule must not rest on what a greyed button reports.
        if imgui.button(SEND) and ready:
            state.confirming = (decision.id, Act.SEND)
        if not ready:
            imgui.end_disabled()
        imgui.same_line()
        if imgui.button(DISMISS):
            state.confirming = (decision.id, Act.DISMISS)
        if ready:
            imgui.text_disabled(FINAL_HINT)
        elif draft.strip():
            imgui.text_colored(P.warn.vec4, too_long_hint(draft))
        else:
            imgui.text_disabled(EMPTY_HINT)
    else:
        imgui.text_colored(P.warn.vec4, confirm_line(pending, role, draft, owed.asker))
        if imgui.button(CONFIRM[pending]):
            # node_id stays None on both: that is what marks them as the
            # operator's, and the store applies neither from anybody else.
            if pending is Act.SEND:
                bridge.emit(DecisionAnswered(decision_id=decision.id, answer=draft))
            else:
                bridge.emit(DecisionDismissed(decision_id=decision.id))
            state.confirming = None
        imgui.same_line()
        if imgui.button(BACK):
            state.confirming = None
    imgui.pop_text_wrap_pos()
    imgui.pop_id()


def draw_detail(snap: Snapshot, state: DecisionState | None, owed: DecisionOwed) -> None:
    """DETAIL's body for the decision under the cursor: all of it, and no control."""
    draw_body(snap, owed, limit=DETAIL_CHARS, rest="copy puts the whole on the clipboard")
    imgui.spacing()
    imgui.separator()
    draft = "" if state is None else state.drafts.get(owed.decision.id, "")
    if draft:
        imgui.text_colored(P.text.vec4, draft_line(draft))
    imgui.text_disabled(ANSWER_FROM_ROW)


def draw_closed(snap: Snapshot, state: DecisionState | None, session_id: str, now: float) -> None:
    """
    The session's closed decisions, behind a triangle that starts closed. Draws
    nothing for a session with none.

    This is where an answered decision is read after its row has left the queue:
    the question, the answer as it was sent, and whether its asker has read it.
    """
    ended = closed(snap, session_id)
    if not ended:
        return
    imgui.spacing()
    # Outside the triangle, which starts shut: an answer its asker has not read
    # is the one fact about a closed decision that is still the operator's to act on.
    for line in unread_lines(snap, session_id):
        _line(line)
    label = f"{closed_heading(len(ended))}###closed-decisions"
    if not imgui.tree_node_ex(label, int(imgui.TreeNodeFlags_.span_avail_width)):
        return
    imgui.push_text_wrap_pos(0.0)
    if imgui.small_button(copy_all(len(ended))):
        # Every one, the ones past the bound below included: the bound is on what
        # a frame draws, and the clipboard has no frame.
        whole: list[str] = []
        for decision in ended:
            whole += [*closed_text(snap, decision, now), "", "----", ""]
        imgui.set_clipboard_text("\n".join(whole))
    for decision in ended[:_MAX_CLOSED]:
        role = asker_role(snap, decision)
        imgui.push_id(f"closed:{decision.id}")
        imgui.spacing()
        imgui.text_colored(P.text_strong.vec4, closed_label(decision, role, now))
        delivery = delivery_label(decision, role, now)
        if delivery is not None:
            _line(delivery)
        if imgui.small_button(COPY):
            imgui.set_clipboard_text("\n".join(closed_text(snap, decision, now)))
        parts = sections(decision, role)
        imgui.text_colored(P.text_dim.vec4, parts[0].heading)
        _authored(decision.question, ROW_CHARS, "copy puts the whole on the clipboard")
        answer = decision.answer
        if answer is not None:
            imgui.text_colored(P.text_dim.vec4, YOUR_ANSWER)
            if answer:
                # Behind the bar as well: it is the operator's and not an agent's,
                # and it is still not a line of this pane's.
                board_pane.author(answer)
            else:
                imgui.text_disabled(EMPTY_ANSWER)
        _unsent(state, decision)
        # The rest of what the open decision showed, one press away so the list
        # stays a list: the question and the answer are what it is scanned for.
        if imgui.tree_node_ex(EVERYTHING_ASKED, int(imgui.TreeNodeFlags_.span_avail_width)):
            for fact in standing_facts(decision, role):
                _line(fact)
            _sections(parts[1:], limit=ROW_CHARS, rest="copy puts the whole on the clipboard")
            imgui.tree_pop()
        imgui.pop_id()
    if len(ended) > _MAX_CLOSED:
        imgui.text_colored(
            P.text_dim.vec4,
            f"... {len(ended) - _MAX_CLOSED} earlier closed decision(s) not shown"
            f" - {copy_all(len(ended))} has them",
        )
    imgui.pop_text_wrap_pos()
    imgui.tree_pop()


def _unsent(state: DecisionState | None, decision: Decision) -> None:
    """A draft that outlived its decision, and the way to let go of it."""
    if state is None or not isinstance(decision.outcome, Withdrawn):
        return
    draft = state.drafts.get(decision.id, "")
    if not draft:
        return
    imgui.text_colored(P.warn.vec4, WITHDRAWN_DRAFT)
    board_pane.author(draft)
    if imgui.small_button("copy draft"):
        imgui.set_clipboard_text(draft)
    imgui.same_line()
    if imgui.small_button("discard draft"):
        del state.drafts[decision.id]
