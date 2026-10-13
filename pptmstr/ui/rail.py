"""
The session rail: one card per root session, grouped by project.

Two orderings exist over the same set of sessions and only one of them can be
position. The inbox is urgency order and reorders constantly; the rail is **stable
spatial order** -- project, then spawn order, never re-sorted. A card grid earns its
space only if position is stable enough to build muscle memory, and sorting cards by
urgency would produce motion instead of a map, leaving two inboxes with the worse
one on the left. Urgency rides on a card as a badge, never as position.

The rail is a second input device onto one cursor, not a second selection. The
highlight is derived from the cursor; clicking a card moves the cursor. It does not
filter -- see ``FocusState.to_node`` for why a filtered inbox is defect 2 in a new
coat.

**A card stands for an agent, and a session is a group of cards.** A sub-agent is
not a row inside its parent; it gets a card of its own, from the same renderer, with
the same density classes and the same focus treatment. That is what gives it room
for the things a row could not hold -- its role, its state, its topic, its spend and
its own model, which a role can override.

A group is always top-level, always present, and collapsed by default. Collapsed,
its sub-agents are one bounded marker on the root's card: a count and the state with
the strongest claim. Opened, they are cards bracketed and indented under it. So the
rail re-flows at the model's pace only inside the groups the operator asked to open.

**Two things are filtered at the draw and nowhere else.** A session off the fleet
has no card and is in no count, because ``projects.roots`` does not list it. An
ended sub-agent nothing is waiting on is folded into one row under its group
(``fleet_view.fold``) when the caller asks for that. Neither touches the snapshot or
the cursor.

**A closed card is not an ended one.** A session recalled from its file
(``AgentRecord.recalled``) was not run by this process, so its card says what the
file recorded and none of what the record's own fields default to: see ``_closed``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import assert_never

from imgui_bundle import imgui

from ..model import (
    AgentRecord,
    AgentState,
    EndedHow,
    NodeId,
    Obligation,
    ObligationKind,
    Recalled,
    Snapshot,
    TranscriptPresence,
)
from ..theme import DISCLOSURE_GLYPH, OBLIGATION_GLYPH, STATE_GLYPH, STATE_LABEL, P
from . import decision_view, fleet_view, projects, session_menu
from .focus import FocusState, Scope, covers
from .widgets import (
    RAIN_WIDTH,
    activity_rain,
    context_cell,
    ellipsis,
    format_elapsed,
    phase_seed,
    short_model,
)

# Lines of text one card gets, by density class. Fixed per class, not per card, and
# the same table for a root and for a sub-agent: a card is a card.
#
# Three classes, not two. Splitting on terminality was wrong at scale -- at twenty
# sessions most cards are working-but-not-blocking, and giving those the same height
# as a blocked one fills the rail with the sessions least likely to be acted on.
# Height tracks obligation, so the vertical budget goes to whatever is waiting.
#
# Nothing here keys on sub-agent count, so a *collapsed* group's height never steps
# when its first sub-agent appears -- see _group for what an open one costs.
# A closed card (``AgentRecord.recalled``) is always "ended": see _density.
_LINES = {"blocked": 3.0, "active": 2.0, "ended": 1.0}

_PAD = 6.0
_SMALL_FONT = 12.5
# Gap between the state glyph and the throbber. Tighter than the default item
# spacing on both sides: the two marks are one reading -- what state, and whether it
# is still moving -- and the pair is charged against the task's width.
_RAIN_GAP = 5.0
# The group bracket: how far a sub-agent's card is inset, and the rule that runs down
# the gutter that inset opens up. Containment is carried by both -- the indent makes
# it structural, the rule makes it visible at a glance -- because either alone reads
# as decoration at the rail's width.
_GROUP_INDENT = 14.0
_RULE_W = 3.0
_RULE_X = 4.0
# Slack around the disclosure marker's measured box, and the narrowest that box is
# allowed to get. It is drawn in a 12.5px font, and open it is one caret -- neither a
# pixel-exact nor a seven-pixel target is one an operator can hit.
_HIT_PAD = 3.0
_HIT_MIN_W = 16.0


@dataclass
class RailState:
    """
    Presentation state for the pane. The cursor is not here -- there is one.

    ``expanded`` holds **session** ids, never node ids: a card stands for an agent
    and cannot be opened, so the only thing that opens is the group the cards live
    in. It never enters the store (design §6), for the same reason ``DetailState``
    and ``TranscriptState`` do not.

    ``unfolded`` holds **root** ids: the sessions whose folded sub-agents the
    operator asked to see. It is what ``fleet_view.fold`` takes as ``opened``, and a
    second set rather than a meaning of ``expanded`` because the two answer
    different questions -- whether the group is open, and whether an open group
    also shows the members nothing is waiting on.
    """

    # A one-shot, armed by ``arm_scroll`` and spent by the focused card. Not a
    # standing "follow the cursor" flag: ``set_scroll_here_y`` re-pins every frame
    # it is called, so a standing one snaps the view back before any wheel event
    # could be noticed -- the failure ``follow_tail`` documents, and the reason it
    # disengages on input rather than on scroll position. Harmless while every card
    # fit on screen; a card taller than the pane cannot be read past the fold.
    scroll_to_focus: bool = True
    expanded: set[str] = field(default_factory=set)
    unfolded: set[NodeId] = field(default_factory=set)
    _scrolled_to: NodeId | None = None

    def toggle(self, session: str) -> None:
        """
        Open or close one session's sub-agents.

        The only thing that ever changes this set. A session is collapsed until the
        operator opens it, and **nothing closes it again on its own** -- not the
        last sub-agent ending, not the session ending. Expanding by default would
        re-flow the map every time a sub-agent is spawned, and auto-collapsing when
        the fleet drains would yank the operator off what they just opened. Both are
        motion at the model's pace, which is the one thing this pane exists not to
        do; the second is tidiness wearing the same costume (see ``OnNode.pinned``).
        """
        if session in self.expanded:
            self.expanded.discard(session)
        else:
            self.expanded.add(session)

    def toggle_ended(self, root: NodeId) -> None:
        """
        Show or fold one session's ended sub-agents.

        The only thing that changes ``unfolded``. Nothing folds a session again on
        its own, for the reason nothing closes a group on its own (``toggle``).
        """
        if root in self.unfolded:
            self.unfolded.discard(root)
        else:
            self.unfolded.add(root)

    def arm_scroll(self, cursor: NodeId | None) -> None:
        """
        Ask for one scroll to the focused card, on the frame the cursor moves.

        Keyed on the node rather than the session, so moving between two cards of
        one group re-pins. Full cards are several times taller than the marker they
        replace, so an open group of five overflows the pane on its own -- without
        this the card the cursor just landed on is routinely below the fold.
        """
        if cursor != self._scrolled_to:
            self._scrolled_to = cursor
            self.scroll_to_focus = True

    def prune(self, snap: Snapshot) -> None:
        """
        Forget sessions the snapshot no longer has.

        Presentation state keyed by store identity leaks without this, and a session
        id that came back -- a relaunch reusing one -- would reopen a card nobody
        asked for.
        """
        self.expanded &= {node[0] for node in snap.order}
        self.unfolded &= set(snap.order)


def _small() -> None:
    imgui.push_font(None, _SMALL_FONT)


def _normal() -> None:
    imgui.pop_font()


def _money(usd: float) -> str:
    return f"${usd:,.2f}"


def _by_session(snap: Snapshot) -> dict[str, list[Obligation]]:
    """
    Obligations indexed by session, built once per frame.

    Everything on a card that counts or badges reads this. Nothing re-derives from
    ``pending``: counting approvals alone is how a project header came to read
    "6 sessions" with no waiting count while that project held a question and a
    crashed session -- defect 1 reproducing itself inside the fix for defect 1.
    """
    out: dict[str, list[Obligation]] = {}
    for obligation in snap.needs_you:
        out.setdefault(obligation.node[0], []).append(obligation)
    return out


def _stopped_on(owed: Sequence[Obligation]) -> list[Obligation]:
    """
    The obligations that mean their node has stopped until the operator acts.

    Every kind but a decision and a command. An approval, a question and a
    failure are read off a node that is parked or dead; a decision and a command
    are records their asker filed, and the asker was told to carry on and may be
    working while either waits.
    """
    return [o for o in owed if o.kind not in _ASKER_CARRIES_ON]


# The kinds that are a record an agent filed and not a state its node is in.
_ASKER_CARRIES_ON = frozenset({ObligationKind.DECISION, ObligationKind.COMMAND})


def _density(rec: AgentRecord, owed: list[Obligation], *, unread: int = 0) -> str:
    """
    Which of the three heights a card is drawn at.

    A card that owes only decisions and commands is not "blocked": nothing on it
    has stopped. It is not "ended" either, whatever its state, because the
    one-line height has no badge and a finished agent's open decision, or a
    command of its that is still running, would then show on no card.

    ``unread`` is the answers this card's agents have not read, and it keeps a
    finished card off the one-line height for the same reason: that height has
    nowhere to say so, and an agent that finished before reading its answer is
    the case the mark is for.
    """
    if rec.recalled is not None:
        # Nothing is owed for a recalled record -- one that comes to owe stops
        # being recalled in the store -- and the taller classes draw a spend, a
        # context ring and an elapsed time, none of which this process observed.
        return "ended"
    if _stopped_on(owed):
        return "blocked"
    if rec.state in (AgentState.DONE, AgentState.CANCELLED) and not owed and not unread:
        return "ended"
    return "active"


def _title(rec: AgentRecord) -> str:
    """
    The one string on a card that tells two of them apart.

    A root is titled by its task -- every root record is named "session", so the name
    cannot do it. A sub-agent is titled by its **role**, which is what the operator
    spawned and the thing its siblings differ by. The driver happens to set ``task``
    to the same string for a sub-agent today; reading ``agent_type`` says which fact
    is meant rather than resting on the two staying equal.
    """
    if rec.node_id[1] is not None:
        return rec.agent_type or rec.task
    return rec.task


def _ago(seconds: float) -> str:
    """
    How long ago, in one unit. Truncated, so it never overstates the age.

    Not ``format_elapsed``: that is a running duration read to the second, and
    this is the age of something that may be weeks old.
    """
    total = int(max(0.0, seconds))
    if total < 60:
        return "<1m ago"
    if total < 3600:
        return f"{total // 60}m ago"
    if total < 86400:
        return f"{total // 3600}h ago"
    return f"{total // 86400}d ago"


# What a closed card says when the conversation its file describes is gone.
TRANSCRIPT_GONE = "transcript gone — can only be removed"


def _closed(recalled: Recalled, now_wall: float | None) -> tuple[str, bool]:
    """
    The one line a closed card carries, and whether it is a warning.

    Everything in it is what the session file recorded. The record's own state is
    DONE and its usage is zero because those fields cannot say nothing, so neither
    is read here: "done" and "$0.00" would both be statements about a run this
    process never saw.

    **How it ended is said in the file's terms.** ``ended_how`` None is a session
    that was killed or whose file does not say, and that is not "closed". A session
    that had failed comes back as an ordinary closed card owing nothing, so its
    line says what happened and takes no alarm colour.

    **The age is against a wall clock handed in**, never the frame's monotonic
    ``now``: ``last_active`` is wall seconds (``model.Recalled``). Without both
    readings the age is said to be unknown rather than left off, so a card with no
    age is told apart from one whose age was never asked for.

    A transcript that is gone replaces the line. The session can be neither
    resumed nor forked, and how long ago it closed is no longer what the operator
    needs from the card.
    """
    if recalled.transcript is TranscriptPresence.GONE:
        return TRANSCRIPT_GONE, True
    match recalled.ended_how:
        case EndedHow.CLOSED | EndedHow.SHUTDOWN:
            how = "closed"
        case EndedHow.FAILED:
            how = "failed, then closed"
        case None:
            how = "ended without closing"
        case _:
            assert_never(recalled.ended_how)
    if recalled.last_active is None or now_wall is None:
        return f"{how} · last active not known", False
    return f"{how} · {_ago(now_wall - recalled.last_active)}", False


def _health(rec: AgentRecord) -> None:
    """
    The context ring, on the cards that have a context to report.

    **A sub-agent gets none, and must not get a hollow one.** ``ContextPolled`` fires
    only for a session's own client, there is one client per session, and a sub-agent
    has none to ask -- so the value does not exist. ``context_cell(None)`` does not
    render that absence: it renders an empty ring, ``"--"``, and the tooltip "context
    not yet polled". On a sub-agent that tooltip promises a reading that will never
    arrive, and an empty ring reads as "0% used, healthy", which is the exact opposite
    of a gap. Omitting the slot is the honest rendering, and the width it frees is
    what the model slot is drawn in.

    Keyed on ``node_id[1]``, not ``parent``: an approval arriving for an agent whose
    spawn hook never fired mints a record with ``parent`` unset, and that record is a
    sub-agent whatever it is attached to. Asking ``parent`` hands the hollow ring to
    the one card already flagged as recovered from a failure.
    """
    if rec.node_id[1] is not None:
        return
    context_cell(rec.context)
    imgui.same_line()


# The order a card's badges are drawn in, left to right: what stops work first --
# a parked call, then a dead session, then a turn that ended, then a decision its
# asker may be working past, then a command, which stops nothing either and is
# said again in words under the title (``commands_mark``).
_BADGE_ORDER = (
    ObligationKind.APPROVAL,
    ObligationKind.FAILURE,
    ObligationKind.QUESTION,
    ObligationKind.DECISION,
    ObligationKind.COMMAND,
)


def _badges(owed: Sequence[Obligation]) -> tuple[tuple[ObligationKind, int], ...]:
    """
    What a card's badges say: each kind it owes with how many, in ``_BADGE_ORDER``.

    One badge per kind and not one number under one glyph. A parked call and
    three decisions under the approval mark reads as four calls waiting to be
    approved, and the count is the part of a badge that is acted on.
    """
    return tuple(
        (kind, count) for kind in _BADGE_ORDER if (count := sum(1 for o in owed if o.kind is kind))
    )


# Between two badges on one card, and between the last badge and the title.
_BADGE_GAP = 3.0
_BADGE_PAD = 5.0


def _badge_text(kind: ObligationKind, count: int) -> str:
    return f"{OBLIGATION_GLYPH[kind]} {count}"


def _draw_unread(unread: int) -> None:
    """
    Beside the state label, that an answer the operator sent has not been read.

    On this line and not among the badges: a badge is what the operator owes,
    and this is the reverse. The topic is drawn after it and is the string that
    gives way.
    """
    if not unread:
        return
    imgui.same_line()
    imgui.text_colored(P.warn.vec4, decision_view.unread_mark(unread))


def commands_waiting(owed: Sequence[Obligation]) -> int:
    """How many of a card's obligations are commands, asked or running."""
    return sum(1 for o in owed if o.kind is ObligationKind.COMMAND)


def commands_mark(count: int) -> str:
    """What a card says, in words, of the commands waiting on the operator."""
    return f"{count} command{'' if count == 1 else 's'} waiting"


def _draw_commands(count: int) -> None:
    """
    Beside the state label, that a command asked from this card is waiting.

    In words as well as a badge, and in the alarm colour: the badge is a glyph
    and a number among up to four others, and this is the one thing on a card
    that asks the operator to run something as themselves. The topic is drawn
    after it and is the string that gives way.
    """
    if not count:
        return
    imgui.same_line()
    imgui.text_colored(P.obligation(ObligationKind.COMMAND).vec4, commands_mark(count))


def _claim(state: AgentState) -> int:
    """
    How much of the operator's attention a sub-agent in this state has a claim on.

    A ``match`` with ``assert_never`` rather than a table: this is read inside a
    draw call, and a lookup that missed a member would raise there and take the
    frame down rather than the card. Adding a state is a type error at this line
    instead.
    """
    match state:
        case AgentState.AWAITING_APPROVAL:
            return 6
        case AgentState.FAILED:
            return 5
        case AgentState.RATE_LIMITED:
            return 4
        case AgentState.AWAITING_INPUT:
            return 3
        case AgentState.CALLING_TOOL | AgentState.RUNNING_TOOL | AgentState.THINKING:
            return 2
        # Below working. A supervising agent is reachable and worth showing, but it
        # is not blocked on anything the operator holds -- ranking it with
        # AWAITING_INPUT would put "you could talk to this" above a sibling that is
        # actually stopped and waiting. Only a root reaches this state today; the arm
        # is what keeps the match total, and the rank is what it would mean if a
        # sub-agent ever fanned out of its own.
        case AgentState.SUPERVISING:
            return 1
        case AgentState.SPAWNING:
            return 1
        case AgentState.DONE | AgentState.CANCELLED:
            return 0
        case _:
            assert_never(state)


def _subs_signal(
    subs: Sequence[AgentRecord], owed: list[Obligation], *, expanded: bool = False
) -> tuple[str, AgentState] | None:
    """
    What a card says about its sub-agents in one bounded string, or None for a
    session that has none.

    **Collapsed**, a count and the state with the strongest claim -- not one label
    per sub-agent. The width varies only with the number of digits, where a label per
    sub grows without bound: at the rail's share of the window three of them run past
    the card border. Losing per-sub detail is the right trade, because the question a
    collapsed card has to answer is only whether there is anything in it.

    **Open, the caret alone.** The count answers "is there anything in here", and that
    question stops being live the moment the members are on screen underneath, each
    showing its own state and its own badge. Keeping it there spends width saying what
    the next four cards already say -- and it is the topic that pays, which is the one
    string on the card saying what this agent is actually doing. Same trade as the
    context ring's width going to the model: a slot that has stopped carrying anything
    gives its space to one that does.

    The glyph is the disclosure either way, so it has to say which way the group is
    open. A marker that always pointed right would be an affordance that lies about
    the state it controls, on the one card whose group is already on screen.
    """
    if not subs:
        return None
    owed_nodes = [o.node for o in _stopped_on(owed)]
    waiting = sum(1 for sub in subs if sub.node_id in owed_nodes)
    worst = max(subs, key=lambda sub: _claim(sub.state)).state
    if expanded:
        return DISCLOSURE_GLYPH[True], worst
    text = f"{DISCLOSURE_GLYPH[False]} {len(subs)} sub" + ("" if len(subs) == 1 else "s")
    if waiting:
        text += f" · {waiting} waiting"
    return text, worst


def _subs_width(signal: tuple[str, AgentState] | None) -> float:
    """
    What the marker will occupy, including the gap ``same_line`` puts before it.

    Measured in the font it is drawn in, so the caller can charge it against a
    budget that is spent absolutely rather than by cursor advance.
    """
    if signal is None:
        return 0.0
    _small()
    width = imgui.calc_text_size(signal[0]).x + imgui.get_style().item_spacing.x
    _normal()
    return width


def _draw_subs(signal: tuple[str, AgentState] | None) -> tuple[float, float, float, float] | None:
    """
    Put the marker beside whatever was drawn last, and return the box it landed in.

    The box is returned rather than the marker being an ``invisible_button``,
    because it sits *inside* a card whose own button was submitted first, and in this
    build the first item claims the hover: a second one stacked on it would need
    ``set_next_item_allow_overlap`` and the one-frame lag that comes with it. The
    marker is the only target this applies to. A sub-agent's card is a sibling in the
    group rather than something inside the parent's box, so it is an ordinary item
    with an ordinary button and ImGui routes its clicks.
    """
    if signal is None:
        return None
    text, worst = signal
    imgui.same_line()
    _small()
    lo = imgui.get_cursor_screen_pos()
    size = imgui.calc_text_size(text)
    imgui.text_colored(P.state(worst).vec4, text)
    _normal()

    # Open, the drawn text is a single caret a few pixels wide, and a hit box
    # measured from it is not a target anyone can hit -- while being the only
    # affordance that closes the group. So the box has a floor.
    #
    # It grows **leftwards**, into the state label, rather than rightwards into the
    # topic. Both neighbours resolve a click as "select this card", so an overshoot
    # either way is harmless in itself; but the topic is the field the freed width
    # was just given to, and a toggle fired by someone aiming at it would undo the
    # reading this change exists to restore. An overshoot onto the state label costs
    # nothing an operator would notice.
    right = lo.x + size.x
    return right - max(size.x, _HIT_MIN_W), lo.y, right, lo.y + size.y


def _on_marker(box: tuple[float, float, float, float] | None) -> bool:
    """Whether the pointer is over the disclosure. Padded; see ``_HIT_PAD``."""
    if box is None:
        return False
    x0, y0, x1, y1 = box
    at = imgui.get_mouse_pos()
    return x0 - _HIT_PAD <= at.x <= x1 + _HIT_PAD and y0 - _HIT_PAD <= at.y <= y1 + _HIT_PAD


@dataclass(frozen=True, slots=True)
class _Box:
    """
    Where a card landed, computed once so the shared tail can close it out without
    re-deriving geometry per density class.
    """

    # The card's own top-left, with any group indent already applied.
    origin: imgui.ImVec2
    # Where the *next* card starts, indent removed. A group indents its sub-agents
    # but must not indent each one relative to the last.
    outer_x: float
    right: float
    height: float


def draw(
    snap: Snapshot,
    focus: FocusState,
    state: RailState,
    now: float,
    menu: session_menu.Wiring | None = None,
    *,
    hide_ended: bool = False,
    now_wall: float | None = None,
) -> None:
    """
    Build the rail from one snapshot. Never reads the store.

    ``menu`` is what a card's context menu is drawn with -- its state, the actions
    it calls and the live gate reading (``session_menu.Wiring``). None draws cards
    that have no menu.

    ``hide_ended`` folds each open group's ended sub-agents that nothing is waiting
    on into one row (``fleet_view.fold``). ``now_wall`` is ``time.time()`` as the
    frame read it, and is what a closed card's age is measured against; ``now`` is
    monotonic and cannot be.
    """
    # Before the empty-rail return, not after: an empty snapshot is the one case
    # where every id in the set is stale.
    state.prune(snap)
    # The one source for everything here that iterates or counts roots, so the
    # empty text, a header's count and the cards cannot disagree. It leaves out
    # sessions off the fleet (``projects.roots``), and a project with none left.
    fleet = projects.group_roots(snap)
    # Keyed on what is drawn and not on ``snap.order``: a fleet whose every session
    # was removed has records and no card, and would otherwise be a blank pane.
    if not fleet:
        imgui.text_disabled("no sessions")
        imgui.spacing()
        imgui.text_disabled("start one from the launcher below.")
        return

    owed_by_session = _by_session(snap)
    cursor = focus.node(snap)
    state.arm_scroll(cursor)

    for project, roots in fleet:
        _project_header(snap, project, roots, owed_by_session)
        for root in roots:
            _group(
                snap,
                focus,
                state,
                rec=snap.nodes[root],
                owed=owed_by_session.get(root[0], []),
                cursor=cursor,
                now=now,
                menu=menu,
                hide_ended=hide_ended,
                now_wall=now_wall,
            )


def header_counts(snap: Snapshot, roots: Sequence[NodeId]) -> str:
    """
    A project's count of sessions, with the closed ones counted apart.

    A closed card is a session this process has not run, and "3 sessions" over two
    of them reads as three things that might be working.
    """
    closed = sum(1 for root in roots if snap.nodes[root].recalled is not None)
    ran = len(roots) - closed
    if not closed:
        return f"{ran} sessions"
    if not ran:
        return f"{closed} closed"
    return f"{ran} sessions · {closed} closed"


def _project_header(
    snap: Snapshot,
    project: str,
    roots: list[NodeId],
    owed_by_session: dict[str, list[Obligation]],
) -> None:
    imgui.spacing()
    _small()
    imgui.text_colored(P.text_strong.vec4, project.upper())
    imgui.same_line()
    imgui.text_colored(P.text_dim.vec4, header_counts(snap, roots))
    # The union, not the approvals. See _by_session.
    waiting = sum(len(owed_by_session.get(root[0], ())) for root in roots)
    if waiting:
        imgui.same_line()
        imgui.text_colored(P.state_awaiting.vec4, f"· {waiting} waiting")
    _normal()
    imgui.spacing()


def _group(
    snap: Snapshot,
    focus: FocusState,
    state: RailState,
    *,
    rec: AgentRecord,
    owed: list[Obligation],
    cursor: NodeId | None,
    now: float,
    menu: session_menu.Wiring | None = None,
    hide_ended: bool = False,
    now_wall: float | None = None,
) -> None:
    """
    One session: the root's card, and -- when open -- a card per sub-agent under it.

    **What an open group costs, stated plainly.** Its membership is append-only --
    nothing reaps a terminal sub-agent, ``Snapshot.subagents_of`` applies no state
    filter, and ``snap.order`` is never reordered -- but its height is the sum of the
    cards drawn, and that moves with sub-agent *state*. Density classes apply per
    card, so a member parking on an approval promotes its own card a whole class --
    ``active`` to ``blocked``, two lines to three -- and a member finishing demotes it
    to ``ended`` and takes two away. Every card below the one that moved, in this
    group and in every group and project under it, moves with it.

    That is accepted rather than worked around. ``_LINES`` already does exactly this
    every time a root changes state, so it is the existing behaviour applied to more
    cards rather than a new kind of motion -- and it only reaches a group the operator
    opened, which is what makes it the operator's own pace rather than the model's.

    **With ``hide_ended``, an ended member nothing is waiting on leaves the group for
    one row that counts it** (``fleet_view.fold`` is the rule; ``_fold_row`` the row).
    So an open group also shrinks at the model's pace, one card at a time, and that
    is the operator's decision over the argument above for this one kind of member
    (``planning/2026-10-13-a-fleet-that-survives-the-window.md``, D6 and D8): a fleet
    that survives the window fills with finished sub-agents, and a card that owes
    nothing, holds nothing and can be had back with one click is the cheapest thing
    on the pane to give up. It is a filter on this function's loop and nothing else.
    Membership is as append-only as it was, which is what the collapse rule rests on
    (``planning/2026-08-13-an-expansion-outgrows-its-pane.md``): the collapsed
    marker still counts every sub-agent, and a member that is woken is drawn again on
    the next frame because nothing here remembers having folded it. The card under
    the cursor is never folded, so the row cannot take away what the operator is on.
    """
    subs = snap.subagents_of(rec.node_id)
    # A session with nothing in it cannot be open, whatever the set says. Membership
    # is not pruned on the 1->0 transition because there is no such transition:
    # sub-agents are never reaped. This guards the case where a session is expanded
    # by a relaunch reusing its id before it has spawned anything.
    expanded = bool(subs) and rec.node_id[0] in state.expanded
    left = imgui.get_cursor_screen_pos().x

    # One scope for the root's card, and everything it does follows from it: which
    # obligations badge it, which density it takes, whether it lights, and what
    # clicking it selects. Deriving those separately is what let a click and a
    # highlight name different agents.
    #
    # **Collapsed it is session-wide.** The root's card is then the only surface the
    # session has, and a badge that omitted a parked sub-agent is the reported defect
    # again. The marker is not a substitute: it counts *agents* where the badge counts
    # *obligations*, so one sub-agent sitting on three calls reads "1 waiting" beside
    # a badge of 3, and dropping the badge would take the outstanding-call count off
    # the rail entirely.
    #
    # **Open it is the root alone.** The member that owns the obligation now has a
    # card, which takes the badge and the blocked height. "Height tracks obligation"
    # comes out better for it: the obligation is rendered where it lives, and the root
    # drops to active rather than being sized for someone else's parked call.
    scope = Scope.AGENT if expanded else Scope.SESSION
    root_bottom = _card(
        snap,
        focus,
        state,
        rec=rec,
        owed=[o for o in owed if covers(scope, rec.node_id, o.node)],
        now=now,
        signal=_subs_signal(subs, owed, expanded=expanded),
        cursor=cursor,
        scope=scope,
        indent=0.0,
        menu=menu,
        now_wall=now_wall,
    )
    if not expanded:
        return

    folded = fleet_view.fold(snap, rec.node_id, cursor, hide_ended, state.unfolded)
    shown = set(folded.drawn)
    bottom = root_bottom
    for sub in subs:
        if sub.node_id not in shown:
            continue
        bottom = _card(
            snap,
            focus,
            state,
            rec=sub,
            # A member is only ever itself: it has a card, so nothing else has to
            # stand in for it.
            owed=[o for o in owed if covers(Scope.AGENT, sub.node_id, o.node)],
            now=now,
            signal=None,
            cursor=cursor,
            scope=Scope.AGENT,
            indent=_GROUP_INDENT,
            menu=menu,
        )
    if folded.count:
        bottom = _fold_row(state, rec.node_id, folded)
    _bracket(left, root_bottom, bottom)


def fold_label(folded: fleet_view.Folded) -> str:
    """What the fold row reads: how many are folded, and whether they are shown."""
    return f"{folded.count} ended, shown" if folded.opened else f"{folded.count} ended"


def _fold_row(state: RailState, root: NodeId, folded: fleet_view.Folded) -> float:
    """
    The row that stands for a group's folded sub-agents. Returns its bottom edge.

    A control and not a card: clicking it shows or folds them and does nothing
    else. It has no context menu, by pointer or by key, and it never takes the
    cursor -- it stands for agents nothing is waiting on, and the cursor is what
    an approval key answers for.

    Drawn after the members and at their indent, so it sits inside the bracket
    where the cards it replaced were. The count is ``Folded.count`` open or
    closed: opening changes what is drawn above it, not what it counts.
    """
    line_h = imgui.get_text_line_height_with_spacing()
    outer = imgui.get_cursor_screen_pos()
    origin = imgui.ImVec2(outer.x + _GROUP_INDENT, outer.y)
    width = max(imgui.get_content_region_avail().x - _GROUP_INDENT, 40.0)
    imgui.set_cursor_screen_pos(origin)

    # The root card's scope, which is closed by now, and a label of its own: no
    # agent id can collide with it.
    imgui.push_id(f"{root[0]}:")
    imgui.invisible_button("##fold", imgui.ImVec2(width, line_h))
    if imgui.is_item_hovered():
        imgui.set_mouse_cursor(imgui.MouseCursor_.hand)
    if imgui.is_item_clicked():
        state.toggle_ended(root)
    imgui.pop_id()

    imgui.set_cursor_screen_pos(imgui.ImVec2(origin.x + _PAD + 4.0, origin.y + 1.0))
    _small()
    imgui.text_colored(P.text_dim.vec4, f"{DISCLOSURE_GLYPH[folded.opened]} {fold_label(folded)}")
    _normal()
    _end(outer.x, origin.y, line_h)
    return origin.y + line_h


def _bracket(left: float, top: float, bottom: float) -> None:
    """
    The rule down an open group's gutter, drawn after its members.

    After, so it is never clipped by a card painted over it; in the gutter the indent
    opens up, so it never paints over one either.

    ``text_dim`` rather than ``border``: a card's own border is the weakest line on
    the pane by design, and a bracket drawn in it disappeared against the panel at
    the rail's width -- an indent with an invisible rule is the containment cue
    halved. Deliberately not a state colour, which would put a mark that changes at
    the model's pace on the one element whose job is to say what belongs to what.
    """
    imgui.get_window_draw_list().add_rect_filled(
        imgui.ImVec2(left + _RULE_X, top),
        imgui.ImVec2(left + _RULE_X + _RULE_W, bottom),
        P.text_dim.u32,
    )


def _card(
    snap: Snapshot,
    focus: FocusState,
    state: RailState,
    *,
    rec: AgentRecord,
    owed: list[Obligation],
    now: float,
    signal: tuple[str, AgentState] | None,
    cursor: NodeId | None,
    scope: Scope,
    indent: float,
    menu: session_menu.Wiring | None = None,
    now_wall: float | None = None,
) -> float:
    """
    One agent, root or sub-agent, drawn the same way. Returns its bottom edge.

    A closed card -- a root recalled from its session file -- is the one-line
    height with ``_closed``'s line where the state and the spend would be.

    ``scope`` decides both whether this card is the cursor's and what clicking it
    selects, from the one value its group handed down -- see ``focus.Scope``.

    Which kind of agent it is comes from ``node_id[1]``, not from ``parent``. The two
    come apart on the recovered-placeholder path (``store.py``), which mints a record
    for a sub-agent whose spawn hook never fired and leaves ``parent`` None when the
    root is not in the table -- so ``parent`` answers "is it attached", and the
    question here is "is it a sub-agent". It buys two differences and no others: a
    sub-agent gets no context ring, and the width that frees goes to its model.
    """
    is_sub = rec.node_id[1] is not None
    focused = cursor is not None and covers(scope, rec.node_id, cursor)
    # The same scope as everything else on the card: collapsed, a root's card
    # speaks for its sub-agents' unread answers as it does for their parked calls.
    unread = decision_view.unread_answers(snap, lambda asker: covers(scope, rec.node_id, asker))
    density = _density(rec, owed, unread=unread)

    draw = imgui.get_window_draw_list()
    line_h = imgui.get_text_line_height_with_spacing()
    height = line_h * _LINES[density] + _PAD * 2

    outer = imgui.get_cursor_screen_pos()
    width = max(imgui.get_content_region_avail().x - indent, 40.0)
    origin = imgui.ImVec2(outer.x + indent, outer.y)
    imgui.set_cursor_screen_pos(origin)

    imgui.push_id(f"{rec.node_id[0]}:{rec.node_id[1] or ''}")
    imgui.invisible_button("##card", imgui.ImVec2(width, height))
    hovered = imgui.is_item_hovered()
    # Latched, and acted on at the bottom of this function: a click on the disclosure
    # means something different from a click on the card, and the disclosure's box is
    # not known until it has been drawn.
    clicked = imgui.is_item_clicked()
    # The context menu opens on the release, as Dear ImGui's own do. A press over
    # a card under another card's open menu closes that menu, so by the release
    # this card is hoverable again and the second menu opens in one gesture.
    asked = hovered and imgui.is_mouse_released(imgui.MouseButton_.right)
    marker: tuple[float, float, float, float] | None = None

    lo = imgui.ImVec2(origin.x, origin.y)
    hi = imgui.ImVec2(origin.x + width, origin.y + height)
    box = _Box(origin=origin, outer_x=outer.x, right=hi.x, height=height)

    # Focus is an outline and an edge bar, never a fill.
    #
    # Filling the focused card with P.selection is the obvious choice and it is
    # wrong: every other mark on a card is a saturated state colour, and in
    # high_contrast the selection fill is saturated too, so the sub-agent pips and
    # the state label lose their figure/ground the moment a card becomes current.
    # Constant fill means a card reads identically whether or not it is focused,
    # which is the property that matters when the colours on it are load-bearing
    # (design 6.1). Only rendering all three required themes showed this.
    draw.add_rect_filled(lo, hi, (P.panel_alt if hovered else P.panel).u32)
    # add_rect is (col, rounding, thickness) in this binding, not the C++
    # (col, rounding, flags, thickness) -- the same transposition widgets.py
    # documents for path_stroke. Passing the C++ order typechecks and throws.
    draw.add_rect(lo, hi, (P.focus if focused else P.border).u32, 0.0, 2.0 if focused else 1.0)
    if focused:
        draw.add_rect_filled(lo, imgui.ImVec2(lo.x + 3.0, hi.y), P.focus.u32)
        if state.scroll_to_focus:
            # Drag the rail to the current card, once. Without any of this the
            # derived highlight is a lie at any real N: the inbox happily selects an
            # obligation whose session is entirely below the fold, and the one pane
            # meant to say "here is where this came from" shows nothing.
            #
            # Spending the one-shot here is what lets the operator then scroll. The
            # anchor is this card's full-height button, so a card taller than the
            # pane would otherwise be held at 45% into itself every frame and the
            # rows past the fold could be neither read nor clicked.
            imgui.set_scroll_here_y(0.45)
            state.scroll_to_focus = False

    inner_x = origin.x + _PAD + 4.0
    y = origin.y + _PAD
    # A recalled record is DONE because the field cannot say nothing, and the
    # colour of a finished run on it would be that default speaking.
    colour = P.text_dim if rec.recalled is not None else P.state(rec.state)

    # Glyph, then the throbber for anything genuinely working. It rides beside the
    # state glyph rather than in the badge slot on the right because that slot is the
    # obligation's, and a session can be both working and owing the operator
    # something -- a sub-agent parked on approval under a thinking root is the common
    # case. Motion competing with the hand for the same eight pixels would trade the
    # signal that must be acted on for the one that only has to be believed.
    imgui.set_cursor_screen_pos(imgui.ImVec2(inner_x, y))
    imgui.text_colored(colour.vec4, STATE_GLYPH[rec.state])
    working = rec.state.is_active
    if working:
        imgui.same_line(0.0, _RAIN_GAP)
        activity_rain(now, colour, phase_seed(f"{rec.node_id[0]}:{rec.node_id[1] or ''}"))
    imgui.same_line()

    if density == "ended":
        if rec.recalled is not None:
            # The file's account, right-aligned, and the title in what is left. No
            # spend: the file's figure is an estimate of a run this process did not
            # see, and Details is where it is shown with that said beside it.
            fact, warns = _closed(rec.recalled, now_wall)
            _small()
            fact_w = imgui.calc_text_size(fact).x
            _normal()
            # A file that recorded no task still has the one name nothing else has.
            title = _title(rec) or rec.node_id[0]
            imgui.text_colored(
                P.text_dim.vec4,
                ellipsis(title, width - 54.0 - _PAD - fact_w - _subs_width(signal)),
            )
            marker = _draw_subs(signal)
            _small()
            imgui.set_cursor_screen_pos(imgui.ImVec2(hi.x - _PAD - fact_w, y + 2.0))
            imgui.text_colored((P.warn if warns else P.text_dim).vec4, fact)
            _normal()
        else:
            # One line, and no model on it: the two slots an ended card has left are
            # what it became and what it cost. A model that no longer runs anything is
            # history, and this is the one density with no room to spare for it.
            imgui.text_colored(
                P.text_dim.vec4, ellipsis(_title(rec), width - 150.0 - _subs_width(signal))
            )
            marker = _draw_subs(signal)
            _small()
            imgui.set_cursor_screen_pos(imgui.ImVec2(hi.x - 96.0, y + 2.0))
            imgui.text_colored(P.text_dim.vec4, STATE_LABEL[rec.state])
            imgui.same_line()
            imgui.text_colored(P.text_dim.vec4, _money(rec.usage.total_cost_usd))
            _normal()
        return _tail(
            snap,
            focus,
            state,
            rec,
            box,
            marker,
            scope=scope,
            hovered=hovered,
            clicked=clicked,
            menu=menu,
            asked=asked,
            focused=focused,
        )

    badges = [(kind, _badge_text(kind, count)) for kind, count in _badges(owed)]
    widths = [imgui.calc_text_size(text).x + _BADGE_PAD * 2 for _, text in badges]
    badges_w = sum(widths) + _BADGE_GAP * max(len(badges) - 1, 0)
    # What one badge took, as a floor: a card with one kind is laid out as it was.
    title_room = width - (_PAD * 2 + 30.0) - (max(52.0, badges_w + 16.0) if owed else 42.0)
    if working:
        title_room -= _RAIN_GAP + RAIN_WIDTH
    imgui.text_colored(P.text_strong.vec4, ellipsis(_title(rec), title_room))

    if badges:
        bx = hi.x - _PAD - badges_w
        for (kind, text), badge_w in zip(badges, widths, strict=True):
            draw.add_rect_filled(
                imgui.ImVec2(bx, y - 1.0),
                imgui.ImVec2(bx + badge_w, y + line_h - 3.0),
                P.obligation(kind).u32,
                2.0,
            )
            imgui.set_cursor_screen_pos(imgui.ImVec2(bx + _BADGE_PAD, y))
            imgui.text_colored(P.bg.vec4, text)
            bx += badge_w + _BADGE_GAP
    else:
        elapsed = format_elapsed(
            (rec.ended_at if rec.ended_at is not None else now) - rec.started_at
        )
        _small()
        imgui.set_cursor_screen_pos(
            imgui.ImVec2(hi.x - _PAD - imgui.calc_text_size(elapsed).x, y + 2.0)
        )
        imgui.text_colored(P.text_dim.vec4, elapsed)
        _normal()

    topic_colour = P.danger if rec.state is AgentState.FAILED else P.text_dim
    spend = _money(rec.usage.total_cost_usd)
    # Every card's own model, not its session's. A role can override it and
    # ``Role.model`` rides on ``AgentSpawned``, so on a sub-agent this is the slot
    # that says which -- and the record used to lie about it.
    model = short_model(rec.model).replace("claude-", "")

    if density == "active":
        # One line for everything it is doing: health, state, topic, spend -- and, on
        # a sub-agent, the model in the space the ring would have taken. An agent that
        # wants nothing gets a reading, not a dossier.
        y += line_h
        imgui.set_cursor_screen_pos(imgui.ImVec2(inner_x, y + 2.0))
        _health(rec)
        _small()
        imgui.text_colored(colour.vec4, STATE_LABEL[rec.state])
        marker = _draw_subs(signal)
        _draw_commands(commands_waiting(owed))
        _draw_unread(unread)
        imgui.same_line()
        spend_w = imgui.calc_text_size(spend).x
        model_w = imgui.calc_text_size(model).x + 6.0 if is_sub else 0.0
        # Read off the cursor, so the marker's width is charged automatically and
        # the topic is the one string on the line that gives way.
        room = hi.x - _PAD - spend_w - model_w - 8.0 - imgui.get_cursor_screen_pos().x
        imgui.text_colored(topic_colour.vec4, ellipsis(rec.topic, max(room, 20.0)))
        if is_sub:
            imgui.set_cursor_screen_pos(imgui.ImVec2(hi.x - _PAD - spend_w - model_w, y + 3.0))
            imgui.text_colored(P.text_dim.vec4, model)
        imgui.set_cursor_screen_pos(imgui.ImVec2(hi.x - _PAD - spend_w, y + 3.0))
        imgui.text_colored(P.text_dim.vec4, spend)
        _normal()
        return _tail(
            snap,
            focus,
            state,
            rec,
            box,
            marker,
            scope=scope,
            hovered=hovered,
            clicked=clicked,
            menu=menu,
            asked=asked,
            focused=focused,
        )

    y += line_h
    imgui.set_cursor_screen_pos(imgui.ImVec2(inner_x, y))
    _small()
    imgui.text_colored(colour.vec4, STATE_LABEL[rec.state])
    marker = _draw_subs(signal)
    _draw_commands(commands_waiting(owed))
    _draw_unread(unread)
    imgui.same_line()
    avail = width - (_PAD * 2 + 8.0) - (imgui.get_cursor_screen_pos().x - inner_x)
    imgui.text_colored(topic_colour.vec4, ellipsis(rec.topic, avail))
    _normal()

    # Health, model and spend side by side but never merged: context answers "should
    # I retire this session", cost answers "what has it spent", and a widget that
    # blends them answers neither (design 2.4).
    y += line_h
    imgui.set_cursor_screen_pos(imgui.ImVec2(inner_x, y + 2.0))
    _health(rec)
    _small()
    imgui.text_colored(P.text_dim.vec4, model)
    imgui.set_cursor_screen_pos(imgui.ImVec2(hi.x - _PAD - imgui.calc_text_size(spend).x, y + 3.0))
    imgui.text_colored(P.text.vec4, spend)
    _normal()

    return _tail(
        snap,
        focus,
        state,
        rec,
        box,
        marker,
        scope=scope,
        hovered=hovered,
        clicked=clicked,
        menu=menu,
        asked=asked,
        focused=focused,
    )


def _tail(
    snap: Snapshot,
    focus: FocusState,
    state: RailState,
    rec: AgentRecord,
    box: _Box,
    marker: tuple[float, float, float, float] | None,
    *,
    scope: Scope,
    hovered: bool,
    clicked: bool,
    menu: session_menu.Wiring | None = None,
    asked: bool = False,
    focused: bool = False,
) -> float:
    """
    What every density class does once it has drawn its own reading: resolve the
    frame's click, draw the card's menu if it is open, close the card out, and
    report where it ended.

    Two targets share this card's one ``invisible_button`` -- the disclosure and
    everything else -- so the click is resolved in one place and they are mutually
    exclusive. A disclosure that also selected is one gesture doing two things, and
    the one it does second is the one nobody asked for.

    Only the disclosure needs this. A sub-agent's card is a sibling in the group with
    a button of its own, so ImGui routes its clicks and nothing here has to know it
    exists.
    """
    on_marker = hovered and _on_marker(marker)
    if on_marker:
        imgui.set_mouse_cursor(imgui.MouseCursor_.hand)

    if clicked:
        if on_marker:
            state.toggle(rec.node_id[0])
        else:
            # The same scope the highlight was derived from, so the card that
            # lights and the card that was clicked are the same card by construction.
            focus.to_node(snap, rec.node_id, scope=scope)

    if menu is not None:
        # Inside the card's ID scope, so each card's popup is its own. The menu
        # acts on the card it was opened on and does not move the cursor: the
        # cursor is what an approval key answers for, and a right-click that
        # moved it would retarget that key as a side effect of looking.
        session_menu.draw_popup(
            menu,
            rec,
            by_mouse=asked,
            focused=focused,
            # Under the card's first line, inset past the focus bar: a menu the
            # key opened is beside the card it belongs to wherever the pointer is.
            anchor=(box.origin.x + 24.0, box.origin.y + imgui.get_text_line_height() + _PAD),
        )

    _end(box.outer_x, box.origin.y, box.height)
    imgui.pop_id()
    return box.origin.y + box.height


def _end(outer_x: float, top: float, height: float) -> None:
    """
    Return the cursor to just after the card and claim the inter-card gap.

    ``outer_x`` is the group's left edge, not the card's: a card indented inside a
    group must leave the cursor where the *next* card starts, or each member would be
    indented relative to the last and the group would walk off the right of the pane.

    The gap has to be a real item (Dummy) rather than a cursor offset: moving the
    cursor past the last submitted item does not grow the window's content bounds,
    and ImGui asserts on that rather than silently clipping.
    """
    imgui.set_cursor_screen_pos(imgui.ImVec2(outer_x, top + height))
    imgui.dummy(imgui.ImVec2(1.0, 4.0))
