"""
The controls on a fleet card: which ones a card offers, and what choosing one does.

A card's menu is mostly a second way to reach levers that already exist -- interrupt
and close from HEALTH and the composers, fork from HEALTH, retry and dismiss from the
failure row in NEEDS YOU. For those it is handed the same callables those surfaces
are (``Actions``), so an item here and the button it stands for cannot come to do
different things. Resume opens the resume form (``ui/resume_form``), which is reached
from here. Three more levers are the menu's own, and no other surface has them: taking
an ended session off the fleet, moving a running session's gate to a rung other than
the lowest, and resuming under a chosen rung. Each of those is an optional callable,
and an item whose callable was not supplied is greyed and says so.

**The Gate rows are** ``gate_menu.rows``, **and nothing here decides what a rung
does.** That table is held against a real ``AgentSession``: a row it calls a live move
is one the driver accepts, and a row it calls a resume is one the driver would refuse.
This module turns each row into a line and adds nothing to the question.

**Pure above the line, Dear ImGui below it.** ``entries`` says what a card offers and
why an item cannot act; ``choose`` says what choosing one calls. Neither touches
imgui. ``draw_popup`` and ``draw_confirm`` are the thin half.

**An item that cannot act is drawn greyed with its reason, never left out.** A lever
that is missing reads as one this card does not have, and a lever that is present and
does nothing reads as a broken application. The reason is on the item's own line and
not in a tooltip: the menu is reachable from the keyboard, and a tooltip is not.

**The keys inside the menu are read here.** The application runs Dear ImGui with
keyboard navigation off (``tests/test_review.py`` pins it and says why), so a popup
answers no arrow key, no Enter and no Escape by itself, and does not claim the
keyboard either. ``MenuState.claim_keys`` is how the frame's other readers of bare
keys are told to stand down while a menu is up. The two modals a menu opens need
no such telling: a modal popup claims the keyboard, which every one of those
readers already defers to.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import assert_never

from imgui_bundle import imgui

from ..approval import Policy, requires_containment
from ..model import (
    AgentRecord,
    AgentState,
    DetachRefusal,
    LaunchSpec,
    NodeId,
    Snapshot,
    TranscriptPresence,
)
from ..theme import STATE_LABEL, P
from . import gate_menu, session_details
from .widgets import RESUME_ITEM, ellipsis, gate_adds

# The popup every card opens, inside that card's own ID scope: one name, one popup
# per card.
POPUP_ID = "##session-menu"
# ``###`` keeps the window's identity while the words in front of it may change.
CONFIRM_TITLE = "Close this session?###session-close"
GATE_TITLE = "Change this session's gate?###session-gate"

# The last line of every menu. Nothing else says the menu answers the keyboard,
# and with no item highlighted when it opens nothing would show it either.
KEYS_HINT = "arrows move, Enter chooses, Esc closes"

_HEAD_WIDTH = 300.0
_CONFIRM_WIDTH = 460.0
# How wide the line under a Gate row is wrapped: a consequence is two or three
# sentences, and unwrapped it would make the popup as wide as the longest one.
_NOTE_WIDTH = 440.0


class Action(enum.Enum):
    """
    Everything a card's menu can ask for.

    ``SET_GATE`` and ``RESUME_AS`` are asked for once per rung, so the member alone
    does not say which line was meant: the ``Item`` carries the rung, and
    ``Item.key`` is what tells two of them apart.
    """

    INTERRUPT = "interrupt"
    FORK = "fork"
    # Move the running session's gate to the item's rung, now.
    SET_GATE = "set gate"
    # Open the resume form under the item's rung.
    RESUME_AS = "resume as"
    DETAILS = "details"
    HEALTH = "health"
    CLOSE = "close"
    RESUME = "resume"
    RETRY = "retry"
    DISMISS = "dismiss"
    # Take an ended session off the fleet.
    REMOVE = "remove"


class Kind(enum.Enum):
    """Which of the three menus a card gets."""

    LIVE_ROOT = "live root"
    ENDED_ROOT = "ended root"
    SUB_AGENT = "sub-agent"


# What names one choosable line of a menu: its action, and with it the rung for
# the two actions that are offered once per rung. The bare member for every other
# item, so a caller that means "Fork" says ``Action.FORK``.
Key = Action | tuple[Action, Policy]


@dataclass(frozen=True, slots=True)
class Item:
    """
    One line of the menu that can be chosen, or says why it cannot.

    ``refused`` is the reason it cannot act right now, and None when it can.
    ``grouped`` is whether it sits under the ``Heading`` before it. ``rung`` is
    set on a Gate row and on nothing else. ``note`` is drawn dim under the line:
    for a Gate row, what choosing it will change and what it will not.
    ``asks`` is whether choosing it opens a question instead of acting.
    """

    action: Action
    label: str
    refused: str | None = None
    grouped: bool = False
    rung: Policy | None = None
    note: str = ""
    asks: bool = False

    @property
    def enabled(self) -> bool:
        return self.refused is None

    @property
    def key(self) -> Key:
        return self.action if self.rung is None else (self.action, self.rung)

    @property
    def text(self) -> str:
        """The line as drawn: the label, and the reason beside it when refused."""
        return self.label if self.refused is None else f"{self.label}  -  {self.refused}"


@dataclass(frozen=True, slots=True)
class Heading:
    """A group's name, above the items that belong to it. Not choosable."""

    label: str


@dataclass(frozen=True, slots=True)
class Line:
    """
    Something the menu says and nobody chooses: the rung a session is on now, or
    why a group has no rows. ``note`` is drawn dim under it, as an item's is.
    """

    label: str
    note: str = ""
    grouped: bool = True


@dataclass(frozen=True, slots=True)
class Rule:
    """A separator."""


Entry = Item | Heading | Line | Rule

# Why an item that acts on a running session cannot: nothing this window holds is
# running it. Every such lever is a lookup in the pool by session id, and a lookup
# that misses does nothing and says nothing.
NOT_HELD = "no running session is held for this card"
# Why an item whose callable ``Actions`` was built without cannot act.
NOT_WIRED = "not wired in this build"

# The words for Resume's own item, which the lines that send the operator to it
# repeat: ``widgets.RESUME_ITEM``, the name every pane's closed line uses for it.
RESUME_LABEL = RESUME_ITEM
# Close on a session that is mid-turn. The ellipsis is the promise of a second
# step before anything ends, in the character every other such item uses.
CLOSE_ASKS_LABEL = "Close…"
# Why Fork and Retry cannot start a session from a card: the record's title is
# not the task the session was launched with (``task_missing``).
TASK_NOT_RECORDED = f"its original task is not recorded — {RESUME_LABEL} instead"
# Under Close on a failed session, by whether a process is still attached.
CLOSE_FAILED_RUN = "a process is still attached to it: closing ends that process"
CLOSE_FAILED_NO_RUN = "its run is over: closing marks it ended, after which it can be removed"
GATE_NOT_HELD = f"gate not known - {NOT_HELD}"
GATE_NOT_KNOWN = f"gate not known - {RESUME_LABEL} to choose"
# Said under the rows of a running session whose containment nobody reported.
# The rows are then the ones for a session with none: a rung that needs it is
# offered as a resume, which is right for either answer, and never as a live
# move, which is refused for one of them.
CONTAINMENT_NOT_KNOWN = "containment not known - the rows are those of a session without it"

REMOVE_LABEL = "Remove from fleet"
# Why a session cannot leave the fleet yet, by the store's reason for refusing.
# ``FAILED_NOT_CLOSED`` names closing without naming a control: the session's
# run may be over with nothing left on screen that closes it.
REMOVAL_REFUSED = {
    DetachRefusal.NO_SUCH_SESSION: "the store holds no session under this id",
    DetachRefusal.NOT_ENDED: "an agent of this session is still running",
    DetachRefusal.FAILED_NOT_CLOSED: "it failed and has not been closed — close it first",
    DetachRefusal.OWES: "it still needs you — answer or dismiss its rows first",
}
STORE_NOT_ASKED = "not known whether anything of it is still running or owed"
RUN_ATTACHED = "a process is still attached to it"
RUN_NOT_KNOWN = "not known whether a process is still attached"

# The rung ``Actions.revoke_policy`` narrows to. A Gate row for it is that
# lever, which HEALTH also has, and not ``Actions.set_policy``.
_REVOKED_TO = tuple(Policy)[0]


@dataclass(frozen=True, slots=True)
class Facts:
    """
    What a menu is told that neither the record nor the live gate reading says.
    Every field defaults to "nobody said", and an item that needs one that
    nobody said is greyed with that as its reason.

    ``snap`` is the frame's snapshot, asked whether the session may leave the
    fleet (``Snapshot.detach_refusal``). ``run_held`` is the shell's half of the
    same question: whether the pool still has a run under the session's id.
    ``contained`` is whether the running process was started with containment,
    as the driver's guard judges it (``session_details.is_contained``).
    ``wired`` is which of the menu's own levers ``Actions`` has a callable for
    (``Actions.wired``).
    """

    snap: Snapshot | None = None
    run_held: bool | None = None
    contained: bool | None = None
    wired: frozenset[Action] = frozenset()


# A menu nobody told anything.
NOTHING_SAID = Facts()


def kind_of(rec: AgentRecord) -> Kind:
    """
    Read off ``node_id[1]`` and not ``parent``, as ``rail._card`` does and for its
    reason: a record recovered without its spawn has no parent and is a sub-agent.
    """
    if rec.node_id[1] is not None:
        return Kind.SUB_AGENT
    return Kind.ENDED_ROOT if rec.state.is_terminal else Kind.LIVE_ROOT


def task_missing(rec: AgentRecord) -> bool:
    """
    Whether a record's ``task`` is not something to start a new session from:
    it is empty, or it is the CLI's own label for a conversation whose
    original task nothing recorded -- a summary, a first prompt, or the bare id
    (``AgentRecord.titled_by_label``). That is true of a closed card recalled
    from a session file with no task, and stays true of the session when it is
    resumed.

    Fork and Retry both launch ``LaunchSpec.from_record``, whose prompt is the
    record's task, in one step with no form in front of it.
    """
    return not rec.task.strip() or rec.titled_by_label


def mid_turn(rec: AgentRecord) -> bool:
    """
    Whether a turn of this session is under way: it has not ended, and it is not
    waiting for the operator's next message.

    A session parked on an approval or on its sub-agents is mid-turn -- the turn it
    is in has not finished -- and so is one that has not connected yet.
    """
    return not rec.state.is_terminal and rec.state is not AgentState.AWAITING_INPUT


def entries(
    rec: AgentRecord, policy: Policy | None, facts: Facts = NOTHING_SAID
) -> tuple[Entry, ...]:
    """
    The menu for one card.

    ``policy`` is the gate the session behind the card is answering with on this
    frame, or None when no session in the pool holds it -- the live reading
    ``app._policy_of`` takes, which is also what says whether the levers that act
    on a running session have anything to act on. ``facts`` is everything else
    the shell knows.
    """
    kind = kind_of(rec)
    match kind:
        case Kind.SUB_AGENT:
            return (Item(Action.DETAILS, "Details"),)
        case Kind.LIVE_ROOT:
            return _live_root(rec, policy, facts)
        case Kind.ENDED_ROOT:
            return _ended_root(rec, policy, facts)
        case _:
            assert_never(kind)


def _live_root(rec: AgentRecord, policy: Policy | None, facts: Facts) -> tuple[Entry, ...]:
    held = policy is not None
    turn = mid_turn(rec)
    if not held:
        interrupt = NOT_HELD
    elif not turn:
        interrupt = "no turn is under way"
    else:
        interrupt = None
    return (
        Item(Action.INTERRUPT, "Interrupt", interrupt),
        # A running session can be titled by a label too: one resumed from a
        # card whose file recorded no task.
        Item(Action.FORK, "Fork", TASK_NOT_RECORDED if task_missing(rec) else None),
        *_gate(rec, policy, facts, live=True),
        Item(Action.DETAILS, "Details"),
        Item(Action.HEALTH, "Health"),
        Rule(),
        Item(Action.CLOSE, CLOSE_ASKS_LABEL if turn else "Close", None if held else NOT_HELD),
    )


def _ended_root(rec: AgentRecord, policy: Policy | None, facts: Facts) -> tuple[Entry, ...]:
    recalled = rec.recalled
    if recalled is not None and recalled.transcript is TranscriptPresence.GONE:
        # Nothing to resume and nothing to fork a resume from: the conversation
        # is not there. What is left is to read about it and to take it away.
        return (Item(Action.DETAILS, "Details"), *_removal(rec, policy, facts))
    no_task = TASK_NOT_RECORDED if task_missing(rec) else None
    out: list[Entry] = [
        Item(Action.RESUME, RESUME_LABEL),
        Item(Action.FORK, "Fork", no_task),
        *_gate(rec, policy, facts, live=process_attached(policy, facts)),
        Item(Action.DETAILS, "Details"),
    ]
    if rec.state is AgentState.FAILED:
        out += [
            Rule(),
            Item(Action.RETRY, "Retry in a new session", no_task),
            Item(
                Action.DISMISS,
                "Dismiss",
                "already dismissed from NEEDS YOU" if rec.acknowledged else None,
            ),
            _close_failed(facts),
        ]
    out += _removal(rec, policy, facts)
    return tuple(out)


def process_attached(policy: Policy | None, facts: Facts) -> bool:
    """
    Whether an ended root still has a process whose gate can be read and moved:
    the pool says a run is held under its id, and a session object answers for
    its gate. That is a session that failed with its client still connected.

    Its Gate group is then the one a running session gets. "The gate this
    session last ran under; it is not running" is false of that process, the
    rung on its record is stale if it was narrowed while it ran, and a move
    made on the session object does gate whatever the process does next.
    """
    return facts.run_held is True and policy is not None


def _close_failed(facts: Facts) -> Item:
    """
    Close, on a session that failed. A failed session is not over for good until
    it is closed -- its next message would make it an ordinary session again --
    and one that is not over for good cannot be removed from the fleet.

    What closing does depends on whether a process is still attached, which is
    the shell's to say, and the line under the item says which it will be: with
    one, the process is ended; with none, the session is marked ended
    (``intents.FailedSessionClosed``). Where nobody said, the item cannot act:
    the two are different acts and it does not guess between them.
    """
    if facts.run_held is None:
        return Item(Action.CLOSE, "Close", RUN_NOT_KNOWN)
    return Item(
        Action.CLOSE, "Close", note=CLOSE_FAILED_RUN if facts.run_held else CLOSE_FAILED_NO_RUN
    )


def _gate(rec: AgentRecord, policy: Policy | None, facts: Facts, *, live: bool) -> list[Entry]:
    """
    The Gate group: one line per rung, from ``gate_menu.rows``.

    The rung the rows are measured from is the session object's for a running
    session, because a session that was narrowed is on the narrowed rung
    whatever its record was launched under. ``live`` is that case, which
    includes an ended root with a process still attached
    (``process_attached``). For any other ended one it is the rung last granted
    (``AgentRecord.granted_policy``). Where neither is known there
    are no rows: a row is a move from somewhere, and a guess at the somewhere
    would mislabel every one of them.
    """
    out: list[Entry] = [Heading("Gate")]
    if live:
        if policy is None:
            return [*out, Line(GATE_NOT_HELD)]
        here = policy
    else:
        granted = rec.granted_policy
        if granted is None:
            return [*out, Line(GATE_NOT_KNOWN)]
        here = granted
    # ``contained`` is not read for a session that is not running: its next
    # process has not been started with anything yet.
    for row in gate_menu.rows(here, facts.contained is True, live, adds=gate_adds):
        out.append(_gate_entry(row, facts, live=live))
    if live and facts.contained is None:
        out.append(Line(CONTAINMENT_NOT_KNOWN))
    return out


def _gate_entry(row: gate_menu.Row, facts: Facts, *, live: bool) -> Entry:
    kind = row.kind
    match kind:
        case gate_menu.Kind.CURRENT:
            return Line(row.label, row.consequence)
        case gate_menu.Kind.NARROW_LIVE | gate_menu.Kind.WIDEN_LIVE:
            action = Action.SET_GATE
            wired = row.rung is _REVOKED_TO or action in facts.wired
        case gate_menu.Kind.CONTINUE_AS:
            action = Action.RESUME_AS
            wired = action in facts.wired
        case _:
            assert_never(kind)
    # A rung that needs containment releases every reviewed tool, so reaching it
    # on a running session takes a second step. A narrowing never does: it
    # releases nothing. Nor does a resume of a session that has ended: the row
    # opens the resume form, which shows the rung and starts nothing until its
    # own button is pressed, and a question in front of that form would be a
    # confirmation of a confirmation.
    asks = (
        kind is not gate_menu.Kind.NARROW_LIVE
        and requires_containment(row.rung)
        and (live or action is not Action.RESUME_AS)
    )
    label = row.label
    if asks and not label.endswith("…"):
        label += "…"
    return Item(
        action,
        label,
        None if wired else NOT_WIRED,
        grouped=True,
        rung=row.rung,
        note=row.consequence,
        asks=asks,
    )


def removal_refused(rec: AgentRecord, policy: Policy | None, facts: Facts) -> str | None:
    """
    Why an ended session cannot be taken off the fleet right now, or None when
    it can.

    Two halves, and both have to say yes. The store's: nothing of the session
    can act and nothing of it is owed (``Snapshot.detach_refusal``). The
    shell's: the pool has no run under its id, which no snapshot holds. A half
    nobody answered is a refusal that says so, not a yes.
    """
    if Action.REMOVE not in facts.wired:
        return NOT_WIRED
    if facts.snap is None:
        return STORE_NOT_ASKED
    refusal = facts.snap.detach_refusal(rec.node_id[0])
    if refusal is not None:
        return REMOVAL_REFUSED[refusal]
    if facts.run_held:
        return RUN_ATTACHED
    if facts.run_held is None:
        # Nobody asked the pool. A gate reading is a session object the pool
        # holds, which is as near to an answer as there is; it is not read
        # where the pool did answer, because the pool keeps the object of a
        # session whose run ended by itself.
        return RUN_ATTACHED if policy is not None else RUN_NOT_KNOWN
    return None


def _removal(rec: AgentRecord, policy: Policy | None, facts: Facts) -> list[Entry]:
    """
    "Remove from fleet": a rule and the item, on an ended root and nowhere else.
    """
    return [Rule(), Item(Action.REMOVE, REMOVE_LABEL, removal_refused(rec, policy, facts))]


def find(offered: tuple[Entry, ...], action: Action, rung: Policy | None = None) -> Item | None:
    """
    The item for ``action`` in a menu, or None when the menu does not offer it.
    ``rung`` picks among the items of an action offered once per rung; without
    it the first is returned.
    """
    return next(
        (
            e
            for e in offered
            if isinstance(e, Item) and e.action is action and (rung is None or e.rung is rung)
        ),
        None,
    )


def at(offered: tuple[Entry, ...], key: Key | None) -> Item | None:
    """The item with this key, or None."""
    return next((e for e in offered if isinstance(e, Item) and e.key == key), None)


def step(offered: tuple[Entry, ...], cursor: Key | None, delta: int) -> Key | None:
    """
    Where the keyboard's highlight goes from ``cursor``: over the items that can
    act, wrapping, and from nothing to the first one going down or the last going
    up. None when no item can act.

    A highlight on an item that has since stopped being able to act counts as no
    highlight. The menu is rebuilt from the snapshot on every frame, and the item
    under the highlight can be refused by something the session did.
    """
    able = [e.key for e in offered if isinstance(e, Item) and e.enabled]
    if not able:
        return None
    if cursor not in able:
        return able[0] if delta >= 0 else able[-1]
    return able[(able.index(cursor) + delta) % len(able)]


@dataclass(frozen=True, slots=True)
class Actions:
    """
    What the menu calls. Each is the callable its existing control is handed:
    ``app._card_actions`` builds this from the functions ``app._panels`` gives
    HEALTH, NEEDS YOU and the composers.

    ``resume`` opens the resume form for the card's session; ``health`` brings
    HEALTH to the card's session. ``close`` on a failed session with no process
    left marks it ended instead of asking the pool (``app._close``).

    The last three are the menu's own levers. None is one this build has not
    supplied, and its items are greyed with ``NOT_WIRED``. ``detach`` takes the
    session off the fleet. ``resume_as`` opens the resume form for the record's
    session under a rung, and starts nothing; ``resume`` opens the same form
    under the lowest. ``set_policy`` moves a running
    session's gate to a rung: it is called outside every ImGui begin/end pair,
    and it is where a refusal by the driver is caught and shown --
    ``AgentSession.set_policy`` raises, and nothing here catches.
    """

    interrupt: Callable[[NodeId], None]
    close: Callable[[NodeId], None]
    fork: Callable[[LaunchSpec], None]
    revoke_policy: Callable[[NodeId], None]
    relaunch: Callable[[LaunchSpec], None]
    dismiss: Callable[[NodeId], None]
    resume: Callable[[AgentRecord], None]
    health: Callable[[NodeId], None]
    detach: Callable[[NodeId], None] | None = None
    resume_as: Callable[[AgentRecord, Policy], None] | None = None
    set_policy: Callable[[NodeId, Policy], None] | None = None

    @property
    def wired(self) -> frozenset[Action]:
        """Which of the menu's own levers have a callable (``Facts.wired``)."""
        supplied = {
            Action.REMOVE: self.detach,
            Action.RESUME_AS: self.resume_as,
            Action.SET_GATE: self.set_policy,
        }
        return frozenset(action for action, call in supplied.items() if call is not None)


@dataclass(frozen=True, slots=True)
class GateAsk:
    """
    A Gate row that was chosen and is waiting on its second step.

    ``label`` and ``consequence`` are the row's own, as it stood when it was
    chosen: the question shows what the operator read before choosing. ``live``
    is whether the session was running then; a question asked of a session that
    has since ended, or started, is about a row that is no longer in its menu.
    """

    node: NodeId
    action: Action
    rung: Policy
    label: str
    consequence: str
    live: bool


@dataclass
class MenuState:
    """
    Presentation state for the card menus and the two modals they open. One object,
    on ``AppState``; nothing here enters the store.

    At most one popup is open at a time, so the highlight is one value and not one
    per card. What is kept per card is by node id.
    """

    # The key was pressed on this frame: the focused card opens its menu and
    # clears this. Written by ``claim_keys`` every frame, so an ask no card took
    # -- the fleet pane was not drawn, nothing has the cursor -- does not wait for
    # one that will.
    open_from_key: bool = False
    # Asked for from somewhere drawn after the fleet pane (the menu bar): it
    # becomes ``open_from_key`` at the next frame's start.
    ask_next_frame: bool = False
    # A card's menu was drawn on this frame. Read and cleared by ``claim_keys``;
    # a flag only its drawer sets cannot be left standing by a pane that stopped
    # being drawn.
    shown: bool = False
    # The card whose menu the key opened, which is held beside that card rather
    # than where the pointer happens to be. None for a menu the mouse opened.
    keyed: NodeId | None = None
    # The item the keyboard is on, or None before an arrow key has been pressed.
    # A menu starts on nothing, however it was opened: its first item is
    # Interrupt on a working session, and a menu that opened with that
    # highlighted would make ``m`` then Enter abandon a turn.
    cursor: Key | None = None
    # The session a close is being confirmed for, or None.
    confirming: NodeId | None = None
    # How many frames that confirmation has been drawn for. Its keys are read
    # from the second: the Enter that chose "Close…" is still down on the first.
    confirm_frames: int = 0
    # The Gate row being confirmed, or None, and its own count of frames.
    gating: GateAsk | None = None
    gating_frames: int = 0
    details: session_details.DetailsState = field(default_factory=session_details.DetailsState)

    def claim_keys(self) -> bool:
        """
        Once per frame, before anything reads a bare key. Whether this frame's
        keys belong to a card's menu -- it was on screen when the key went down
        -- and so to nobody else.

        Also where a menu asked for from the menu bar becomes this frame's ask.
        """
        busy = self.shown
        self.shown = False
        self.open_from_key = self.ask_next_frame
        self.ask_next_frame = False
        return busy

    def prune(self, snap: Snapshot) -> None:
        """Let go of anything kept for a node the snapshot no longer has."""
        if self.keyed is not None and self.keyed not in snap.nodes:
            self.keyed = None
        if self.confirming is not None and self.confirming not in snap.nodes:
            self.confirming = None
            self.confirm_frames = 0
        if self.gating is not None and self.gating.node not in snap.nodes:
            self.gating = None
            self.gating_frames = 0
        self.details.prune(snap)


def choose(
    action: Action,
    rec: AgentRecord,
    policy: Policy | None,
    state: MenuState,
    actions: Actions,
    *,
    rung: Policy | None = None,
    facts: Facts = NOTHING_SAID,
) -> None:
    """
    Do what choosing ``action`` on ``rec``'s card does. ``rung`` says which line
    of an action offered once per rung.

    Asked of the menu as it stands now, so an item the card does not offer, or
    offers refused, does nothing: the frame that drew the item and the keypress
    that chose it are not always the same frame. Which of the menu's own levers
    can act is read off ``actions`` and not off ``facts.wired``.

    **Close does not close a session that is mid-turn.** It asks ``draw_confirm``
    to, and the session is closed by the answer. None of the controls this stands
    beside confirms anything, so the second step is the menu's own. A Gate row
    that ``asks`` is the same: nothing moves until ``draw_confirm`` is answered.
    """
    facts = Facts(facts.snap, facts.run_held, facts.contained, actions.wired)
    item = find(entries(rec, policy, facts), action, rung)
    if item is None or not item.enabled:
        return
    node = rec.node_id
    match action:
        case Action.INTERRUPT:
            actions.interrupt(node)
        case Action.FORK:
            actions.fork(LaunchSpec.from_record(rec))
        case Action.SET_GATE | Action.RESUME_AS:
            if item.rung is None:
                return
            if item.asks:
                state.gating = GateAsk(
                    node, action, item.rung, item.label, item.note, not rec.state.is_terminal
                )
                state.gating_frames = 0
            else:
                _move_gate(action, rec, item.rung, actions)
        case Action.REMOVE:
            if actions.detach is not None:
                actions.detach(node)
        case Action.DETAILS:
            state.details.ask(node)
        case Action.HEALTH:
            actions.health(node)
        case Action.CLOSE:
            if mid_turn(rec):
                state.confirming = node
                state.confirm_frames = 0
            else:
                actions.close(node)
        case Action.RESUME:
            actions.resume(rec)
        case Action.RETRY:
            # The two calls the failure row's "retry in a new session" makes, in
            # its order: a fresh session from the same record, and the failure
            # cleared from the queue.
            actions.relaunch(LaunchSpec.from_record(rec))
            actions.dismiss(node)
        case Action.DISMISS:
            actions.dismiss(node)
        case _:
            assert_never(action)


def _move_gate(action: Action, rec: AgentRecord, rung: Policy, actions: Actions) -> None:
    """
    What a Gate row does once nothing stands between it and doing it.
    """
    if action is Action.RESUME_AS:
        if actions.resume_as is not None:
            actions.resume_as(rec, rung)
    elif rung is _REVOKED_TO:
        actions.revoke_policy(rec.node_id)
    elif actions.set_policy is not None:
        actions.set_policy(rec.node_id, rung)


def gate_ask_stands(ask: GateAsk, rec: AgentRecord | None) -> bool:
    """
    Whether a Gate row's question is still about a row its card offers: the
    session is there, and is running or not as it was when the row was chosen.
    """
    return rec is not None and rec.state.is_terminal is not ask.live


def confirm_text(rec: AgentRecord) -> str:
    """
    What the close confirmation says about the session it is about to end.

    Re-read each frame, because the turn it was asked over can finish while the
    question is on screen, and then the sentence about a turn is no longer true.
    """
    if mid_turn(rec):
        return (
            f"This session is mid-turn ({STATE_LABEL[rec.state]}). Closing ends its run "
            "where it stands: the turn is not finished, and a tool call in flight or an "
            "approval it is waiting on goes with it. Interrupt stops the turn and keeps "
            "the session."
        )
    return (
        "The turn this was asked over has ended since, and the session is waiting for "
        "you. Closing ends the session and frees its slot."
    )


# ---------------------------------------------------------------------------
# Dear ImGui
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Wiring:
    """
    What the fleet pane is handed to draw menus with: the state, the actions, and
    the live gate reading for a node (``app._policy_of``).

    The rest is what ``Facts`` is built from, each None until the shell supplies
    it. ``snap`` is the frame's snapshot. ``running`` is whether the pool holds
    a run under a node's session. ``contained`` is whether the session object
    behind a node was started with containment
    (``session_details.is_contained`` of its settings string), or None where no
    session object is held.
    """

    state: MenuState
    actions: Actions
    gate: Callable[[NodeId], Policy | None]
    snap: Snapshot | None = None
    running: Callable[[NodeId], bool] | None = None
    contained: Callable[[NodeId], bool | None] | None = None

    def facts(self, node: NodeId) -> Facts:
        return Facts(
            snap=self.snap,
            run_held=None if self.running is None else self.running(node),
            contained=None if self.contained is None else self.contained(node),
            wired=self.actions.wired,
        )


def draw_popup(
    wiring: Wiring,
    rec: AgentRecord,
    *,
    by_mouse: bool,
    focused: bool,
    anchor: tuple[float, float],
) -> None:
    """
    Open this card's menu if it was asked for, and draw it while it is open.

    Called once per card per frame, inside the card's ID scope and after the card
    is drawn. ``by_mouse`` is a right button released over the card; ``focused`` is
    whether the cursor's highlight is on it, which is the card the key opens.
    ``anchor`` is where a menu the key opened is put: the pointer can be anywhere.
    """
    state = wiring.state
    node = rec.node_id
    if by_mouse:
        state.keyed = None
        state.cursor = None
        imgui.open_popup(POPUP_ID)
    elif focused and state.open_from_key:
        state.open_from_key = False
        state.keyed = node
        state.cursor = None
        imgui.open_popup(POPUP_ID)

    if state.keyed == node:
        imgui.set_next_window_pos(imgui.ImVec2(*anchor), imgui.Cond_.always)
    if not imgui.begin_popup(POPUP_ID):
        if state.keyed == node:
            state.keyed = None
        return
    state.shown = True

    policy = wiring.gate(node)
    facts = wiring.facts(node)
    offered = entries(rec, policy, facts)
    # Which card this is. The menu does not move the cursor, so the card it
    # belongs to need not be the highlighted one.
    imgui.text_disabled(ellipsis(_headline(rec), _HEAD_WIDTH))
    imgui.separator()

    chosen: Item | None = None
    if imgui.is_key_pressed(imgui.Key.down_arrow):
        state.cursor = step(offered, state.cursor, 1)
    if imgui.is_key_pressed(imgui.Key.up_arrow):
        state.cursor = step(offered, state.cursor, -1)
    if imgui.is_key_pressed(imgui.Key.enter, False) or imgui.is_key_pressed(
        imgui.Key.keypad_enter, False
    ):
        under = at(offered, state.cursor)
        if under is not None and under.enabled:
            chosen = under
    close = bool(imgui.is_key_pressed(imgui.Key.escape))

    for entry in offered:
        match entry:
            case Rule():
                imgui.separator()
            case Heading(label=label):
                imgui.text_disabled(label)
            case Line():
                if entry.grouped:
                    imgui.indent()
                imgui.text_disabled(entry.label)
                _note(entry.note)
                if entry.grouped:
                    imgui.unindent()
            case Item():
                if entry.grouped:
                    imgui.indent()
                if not entry.enabled:
                    imgui.begin_disabled()
                rung = "" if entry.rung is None else f"-{entry.rung.value}"
                clicked, _ = imgui.selectable(
                    f"{entry.text}##{entry.action.value}{rung}",
                    entry.enabled and state.cursor == entry.key,
                )
                if not entry.enabled:
                    imgui.end_disabled()
                _note(entry.note)
                if entry.grouped:
                    imgui.unindent()
                if clicked:
                    chosen = entry
            case _:
                assert_never(entry)

    imgui.separator()
    imgui.text_disabled(KEYS_HINT)

    if chosen is not None or close:
        state.keyed = None
        state.cursor = None
        imgui.close_current_popup()
    imgui.end_popup()
    # After the popup is closed out: a lever that raised between the begin and
    # the end would leave the frame unbalanced for the rest of its draw.
    if chosen is not None:
        choose(chosen.action, rec, policy, state, wiring.actions, rung=chosen.rung, facts=facts)


def _note(text: str) -> None:
    """The dim line under a menu line, wrapped, and nothing for an empty one."""
    if not text:
        return
    imgui.push_text_wrap_pos(imgui.get_cursor_pos_x() + _NOTE_WIDTH)
    imgui.text_disabled(text)
    imgui.pop_text_wrap_pos()


def _headline(rec: AgentRecord) -> str:
    """The card's own title, as ``rail._title`` reads it."""
    if rec.node_id[1] is not None:
        return rec.agent_type or rec.task
    return rec.task


def draw_confirm(state: MenuState, snap: Snapshot, actions: Actions) -> None:
    """
    The second step of the two things a menu does not do at one keypress: closing
    a session that was mid-turn when it was asked, and a Gate row that ``asks``.

    At root level, with the launcher, and for its reason: a popup opened inside a
    docked window goes with that window. At most one of the two is up: each is
    asked from a menu, and a modal is in front of every card.
    """
    _confirm_close(state, snap, actions)
    _confirm_gate(state, snap, actions)


def _confirm_close(state: MenuState, snap: Snapshot, actions: Actions) -> None:
    """
    Enter confirms and Escape cancels, read from the second frame on -- see
    ``MenuState.confirm_frames``. A session that has ended since has nothing left
    to close, and the question goes away.
    """
    node = state.confirming
    if node is None:
        return
    rec = snap.get(node)
    if rec is None or rec.state.is_terminal:
        if state.confirm_frames and imgui.begin_popup_modal(CONFIRM_TITLE)[0]:
            imgui.close_current_popup()
            imgui.end_popup()
        state.confirming = None
        state.confirm_frames = 0
        return

    if state.confirm_frames == 0:
        imgui.open_popup(CONFIRM_TITLE)
    viewport = imgui.get_main_viewport()
    imgui.set_next_window_pos(viewport.get_center(), imgui.Cond_.appearing, imgui.ImVec2(0.5, 0.5))
    imgui.set_next_window_size_constraints(
        imgui.ImVec2(_CONFIRM_WIDTH, 0.0), imgui.ImVec2(_CONFIRM_WIDTH, viewport.work_size.y)
    )
    opened, _ = imgui.begin_popup_modal(CONFIRM_TITLE, None, imgui.WindowFlags_.always_auto_resize)
    if not opened:
        state.confirming = None
        state.confirm_frames = 0
        return
    listening = state.confirm_frames > 0
    state.confirm_frames += 1

    imgui.push_text_wrap_pos(0.0)
    imgui.text_colored(P.text_strong.vec4, ellipsis(rec.task, _CONFIRM_WIDTH - 24.0))
    imgui.spacing()
    imgui.text_colored(P.warn.vec4, confirm_text(rec))
    imgui.pop_text_wrap_pos()
    imgui.spacing()

    confirmed = bool(imgui.button("close session"))
    imgui.same_line()
    cancelled = bool(imgui.button("cancel"))
    imgui.same_line()
    imgui.text_disabled("Enter closes it, Esc cancels")
    if listening:
        confirmed = confirmed or bool(
            imgui.is_key_pressed(imgui.Key.enter, False)
            or imgui.is_key_pressed(imgui.Key.keypad_enter, False)
        )
        cancelled = cancelled or bool(imgui.is_key_pressed(imgui.Key.escape))

    # Cancel wins a frame that carries both.
    if cancelled or confirmed:
        if confirmed and not cancelled:
            actions.close(node)
        state.confirming = None
        state.confirm_frames = 0
        imgui.close_current_popup()
    imgui.end_popup()


def gate_confirm_button(ask: GateAsk) -> str:
    """What the confirming button of a Gate row's question is called."""
    if ask.action is Action.RESUME_AS:
        return "open the resume form"
    return f"move to {ask.rung.value}"


def _confirm_gate(state: MenuState, snap: Snapshot, actions: Actions) -> None:
    """
    The second step of a Gate row that reaches a rung which needs containment.

    It shows the row as it was chosen and nothing newer: the line and the
    consequence the operator read. A session that has ended since, or started,
    no longer has that row, and the question goes away unanswered.

    Enter confirms and Escape cancels, from the second frame on, as for a close.
    The lever is pulled after the popup is closed out, for the reason
    ``draw_popup`` gives.
    """
    ask = state.gating
    if ask is None:
        return
    rec = snap.get(ask.node)
    if rec is None or not gate_ask_stands(ask, rec):
        if state.gating_frames and imgui.begin_popup_modal(GATE_TITLE)[0]:
            imgui.close_current_popup()
            imgui.end_popup()
        state.gating = None
        state.gating_frames = 0
        return

    if state.gating_frames == 0:
        imgui.open_popup(GATE_TITLE)
    viewport = imgui.get_main_viewport()
    imgui.set_next_window_pos(viewport.get_center(), imgui.Cond_.appearing, imgui.ImVec2(0.5, 0.5))
    imgui.set_next_window_size_constraints(
        imgui.ImVec2(_CONFIRM_WIDTH, 0.0), imgui.ImVec2(_CONFIRM_WIDTH, viewport.work_size.y)
    )
    opened, _ = imgui.begin_popup_modal(GATE_TITLE, None, imgui.WindowFlags_.always_auto_resize)
    if not opened:
        state.gating = None
        state.gating_frames = 0
        return
    listening = state.gating_frames > 0
    state.gating_frames += 1

    imgui.push_text_wrap_pos(0.0)
    imgui.text_colored(P.text_strong.vec4, ellipsis(rec.task, _CONFIRM_WIDTH - 24.0))
    imgui.spacing()
    imgui.text_colored(P.text.vec4, ask.label)
    imgui.text_colored(P.warn.vec4, ask.consequence)
    imgui.pop_text_wrap_pos()
    imgui.spacing()

    confirmed = bool(imgui.button(gate_confirm_button(ask)))
    imgui.same_line()
    cancelled = bool(imgui.button("cancel"))
    imgui.same_line()
    imgui.text_disabled("Enter confirms, Esc cancels")
    if listening:
        confirmed = confirmed or bool(
            imgui.is_key_pressed(imgui.Key.enter, False)
            or imgui.is_key_pressed(imgui.Key.keypad_enter, False)
        )
        cancelled = cancelled or bool(imgui.is_key_pressed(imgui.Key.escape))

    # Cancel wins a frame that carries both.
    if cancelled or confirmed:
        state.gating = None
        state.gating_frames = 0
        imgui.close_current_popup()
    imgui.end_popup()
    if confirmed and not cancelled:
        _move_gate(ask.action, rec, ask.rung, actions)
