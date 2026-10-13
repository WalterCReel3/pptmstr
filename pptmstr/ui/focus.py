"""
The cursor. Singular, and that is the whole point.

The layout this replaces had two: ``view.selected`` was a NodeId that DETAIL
followed, ``review.selected`` was a pending-approval id that TRANSCRIPT and TALK
followed, nothing constrained them to agree, and no pixel indicated when they did
not. An operator could approve a write belonging to one agent while reading the
transcript of another, and both panes looked correct.

That is not fixable by being careful, because the two values were independently
assignable by design. It is fixed by there being one value, with everything else
derived from it:

    the cursor names an obligation, or -- when there is nothing to answer -- a node.
    Whichever it names, the other is computed, never stored.

Identity is a key, never an index. ``needs_you`` is sorted by age and reorders
whenever anything arrives or is answered, so a positional cursor would quietly
retarget itself between frames (I6) -- and this is the cursor that decides which
agent an ``approve`` keystroke applies to.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from ..model import CommandOwed, NodeId, Obligation, Snapshot


class Scope(enum.Enum):
    """
    What a cursor target answers for: itself, or everything in its session.

    **Passed, never inferred, and that is the whole point.** ``node[1] is None`` says
    a node is a root. It cannot say whether that root is standing in for its whole
    session -- which a *collapsed* group's card does, because its sub-agents have no
    card of their own -- or standing only for itself, which the same card does the
    moment the group opens and they get one. No NodeId can express that difference,
    because it is a fact about what is on screen rather than about the node.

    Inferring it meant the highlight and the click each worked it out separately, and
    they disagreed: clicking an open root resolved session-wide, selected one of its
    members, lit *that* card and scrolled away from the one that was clicked. Two
    expansion-aware code paths is how that got in; one scope value shared by both is
    what stops it coming back.
    """

    AGENT = "agent"
    SESSION = "session"


def covers(scope: Scope, node: NodeId, other: NodeId) -> bool:
    """
    Whether ``other`` is an agent that ``node`` answers for under ``scope``.

    The single predicate. A rail card derives its highlight, its click, its badge and
    its density from one call to this with one scope value, so it cannot light for
    one agent and select another.
    """
    return other[0] == node[0] if scope is Scope.SESSION else other == node


def _on_fleet(snap: Snapshot, node: NodeId) -> bool:
    """Whether ``node`` exists and its session has not been taken off the fleet."""
    return node in snap.nodes and not snap.off_fleet(node[0])


def _ran_here(snap: Snapshot, node: NodeId) -> bool:
    """
    Whether ``node`` is the root of a session this process ran and that is on
    the fleet: what an unchosen cursor may rest on.
    """
    record = snap.nodes[node]
    return record.parent is None and record.recalled is None and not snap.off_fleet(node[0])


@dataclass(frozen=True, slots=True)
class OnObligation:
    """Pointed at something waiting on the operator."""

    key: str


@dataclass(frozen=True, slots=True)
class OnNode:
    """
    Pointed at a session rather than at something it is asking for.

    ``pinned`` separates the two ways this happens, and they must not behave alike:

    * **Pinned** -- the operator clicked a card with no obligations. A deliberate
      "show me this session", which is also the gesture that opens FOCUS. It holds
      even when work arrives, because yanking someone off the session they just
      asked for is the same failure as auto-switching the layout under them.
    * **Unpinned** -- nowhere better to point. Either nothing has ever needed
      attention, or the operator just cleared the last obligation. This yields to
      the queue the moment there is a queue again.

    Without the distinction the unpinned case sticks: the cursor settles on a node
    during the seconds before the first agent asks for anything, and then never
    moves to the inbox. The rail highlights a card, the inbox lists rows with none
    of them expanded, and every pixel looks correct.
    """

    node: NodeId
    pinned: bool = False


@dataclass(frozen=True, slots=True)
class OnDeparted:
    """
    Pointed at where a command's row was, and at nothing that can be answered.

    The command under the cursor left the queue: its agent withdrew it, it was
    refused, it ended. ``left`` is that obligation as the cursor last saw it,
    so the queue can say what went.

    **Nothing takes its place under the cursor.** A command's row is the one
    row whose presses all take the mouse, so the press that follows its going
    is a mouse button already on its way down -- and the row that slides into
    the position, opened there, would put its own buttons under it: an
    approval's ``approve`` where ``run it`` was. So this target names no
    obligation, yields to nothing that arrives, and is left only by an act of
    the operator's -- ``move``, ``to_obligation``, ``to_node``.

    It is not what keeps a command from being read unasked. The cursor may come
    to rest on a command by any route, and a command's row shows none of its
    text until the operator opens that row (``command_view.CommandViewState``).

    ``after`` is the keys of the rows that stood below it when it went, in
    their order. Where it was is ahead of the first of them still in the queue
    (``position``): a key and not an index, because the queue goes on changing
    under a mark that stays.
    """

    left: Obligation
    after: tuple[str, ...] = ()

    def position(self, snap: Snapshot) -> int:
        """
        Where in ``snap``'s queue the row was: the index of the row it would
        stand ahead of, and the queue's length when every row that was below
        it has gone too.
        """
        below = set(self.after)
        return next(
            (i for i, o in enumerate(snap.needs_you) if o.key in below), len(snap.needs_you)
        )


Target = OnObligation | OnNode | OnDeparted


@dataclass
class FocusState:
    """
    Presentation state, not store state: where the operator is looking.

    ``_index`` is a memory of where the cursor sat in the queue, kept **only** so
    that a row leaving from under the cursor is followed from where it was: by
    the row that took its place, or, for a command, by a mark of what left
    (``OnDeparted``). It is never the identity -- resolution always goes
    through the key first, and this is consulted only once the key has stopped
    matching anything.
    """

    target: Target | None = None
    _index: int = 0
    # The node the cursor was last resolved to. Not a second cursor: nothing reads
    # it to answer "where is the cursor". It exists so that answering the last
    # obligation leaves the operator looking at the session they just dealt with,
    # instead of being thrown to the top of the tree at the moment the queue empties.
    _last_node: NodeId | None = None
    # The obligation the cursor's key named when it was last resolved or
    # pointed. What ``OnDeparted`` is built from once the key matches nothing.
    _seen: Obligation | None = None

    # -- resolution ------------------------------------------------------------

    def obligation(self, snap: Snapshot) -> Obligation | None:
        """The obligation under the cursor, if it is on one and it still exists."""
        if not isinstance(self.target, OnObligation):
            return None
        return next((o for o in snap.needs_you if o.key == self.target.key), None)

    def node(self, snap: Snapshot) -> NodeId | None:
        """
        The node under the cursor. **Derived**, never stored alongside the
        obligation -- storing it is what let the two disagree.

        Never a node of a session that is off the fleet (``Snapshot.off_fleet``),
        whatever the target still names: every pane that follows the cursor asks
        here, and such a session is drawn nowhere. Asked here as well as in
        ``settle`` so that it holds for a snapshot ``settle`` has not seen.
        """
        current = self.obligation(snap)
        if current is not None:
            return current.node
        if isinstance(self.target, OnNode) and _on_fleet(snap, self.target.node):
            return self.target.node
        if isinstance(self.target, OnDeparted) and _on_fleet(snap, self.target.left.node):
            # The session just dealt with, so the panes that follow the cursor
            # stay where the operator was looking.
            return self.target.left.node
        return None

    @property
    def departed(self) -> OnDeparted | None:
        """Where the row under the cursor was, if it left and the operator has not moved since."""
        return self.target if isinstance(self.target, OnDeparted) else None

    def index(self, snap: Snapshot) -> int | None:
        """Position in the queue, for the rendering pass. Not identity."""
        if not isinstance(self.target, OnObligation):
            return None
        return next(
            (i for i, o in enumerate(snap.needs_you) if o.key == self.target.key),
            None,
        )

    def owed_by(self, snap: Snapshot, node: NodeId, scope: Scope) -> Obligation | None:
        """The oldest obligation ``node`` answers for under ``scope``, if any."""
        owed = [o for o in snap.needs_you if covers(scope, node, o.node)]
        return min(owed, key=lambda o: o.since) if owed else None

    # -- movement --------------------------------------------------------------

    def settle(self, snap: Snapshot) -> None:
        """
        Keep the cursor on something real. Called once per frame, before drawing.

        Three cases, and the second is the one that makes the inbox feel right:

        * still valid -- remember where it sits, and leave it alone.
        * pointed at an obligation that has just left the queue -- land on
          whatever now occupies that position, so a run of approvals can be
          worked without the cursor jumping back to the top between each one.
          **Unless a command is what left**: then the cursor rests on where
          the row was and on no row (``OnDeparted``), until the operator moves
          it. A command may be what is landed on: its row is closed until the
          operator opens it.
        * pointed at nothing, with work waiting -- take the oldest.

        **Nothing chooses a closed card, and nothing stays on a removed one.**
        With no queue the cursor defaults to the first session this process ran
        that is still on the fleet, and to nothing when there is none: a session
        recalled from its file (``AgentRecord.recalled``) has no transcript, no
        board and no health to show, so landing on the oldest of them at startup
        would open every pane on "not loaded". The operator can still point at
        one, and a click holds. A session taken off the fleet
        (``Snapshot.off_fleet``) is dropped as a target, pinned or not: the pin
        said "show me this" about a card that is no longer drawn. That check
        follows the return to ``_last_node`` below, so it is also what keeps
        the cursor from going back to one.
        """
        if isinstance(self.target, OnObligation):
            at = self.index(snap)
            if at is not None:
                self._index = at
                self._seen = snap.needs_you[at]
            elif (left := self._left()) is not None:
                self.target = OnDeparted(left, tuple(o.key for o in snap.needs_you[self._index :]))
            elif snap.needs_you:
                self._index = min(self._index, len(snap.needs_you) - 1)
                self.to_obligation(snap.needs_you[self._index])
            else:
                # Nothing left to answer. Stay on the session just dealt with rather
                # than blanking, so clearing the queue does not also throw away
                # where the operator was looking. _last_node still holds the
                # previous frame's reading, which is exactly the one wanted here.
                self.target = OnNode(self._last_node) if self._last_node else None

        if isinstance(self.target, OnNode):
            if not _on_fleet(snap, self.target.node):
                self.target = None
            elif snap.needs_you and not self.target.pinned:
                # Nothing chose this node; the queue did not exist when the cursor
                # landed here. Now it does, so it takes precedence -- prefer this
                # node's own oldest obligation, and otherwise drop to None so the
                # block below takes the queue's oldest.
                #
                # Deliberately not `to_node`. That pins when the node owes nothing,
                # which is right for a click and wrong here: a pin is the operator
                # saying "show me this", and nothing was said. Routing through it
                # made an unpinned cursor pin *itself* the moment work arrived in
                # any other session, which is precisely the stuck cursor OnNode is
                # split in two to prevent.
                # Inferred here, and only here. A bare OnNode has no card behind it
                # to say what it stands for -- nothing chose it -- so the node's own
                # shape is the only information available, and a root preferring its
                # session keeps the cursor near where the operator was looking. A
                # card must not infer; see Scope.
                node = self.target.node
                owed = self.owed_by(snap, node, Scope.SESSION if node[1] is None else Scope.AGENT)
                if owed is not None:
                    self.to_obligation(owed)
                else:
                    self.target = None
                at = self.index(snap)
                self._index = at if at is not None else 0

        if self.target is None:
            if snap.needs_you:
                self._index = 0
                self.to_obligation(snap.needs_you[0])
            else:
                first = next((n for n in snap.order if _ran_here(snap, n)), None)
                self.target = OnNode(first) if first is not None else None

        # Recorded from the resolved cursor, not from whichever branch ran, so it is
        # set however the cursor got where it is.
        resolved = self.node(snap)
        if resolved is not None:
            self._last_node = resolved

    def _left(self) -> CommandOwed | None:
        """
        The command that has just gone from under the cursor, with a queue
        left or without one; None when what went was not a command, and the
        next row may take the cursor.
        """
        seen = self._seen
        if not isinstance(seen, CommandOwed) or not isinstance(self.target, OnObligation):
            return None
        # A key no snapshot resolved for this cursor: nothing was on screen
        # under it, so there is nothing to say has gone.
        return seen if seen.key == self.target.key else None

    def move(self, snap: Snapshot, delta: int) -> None:
        """
        Step through the queue. Clamped, not wrapped.

        From where a row was (``OnDeparted``), a step down lands on the row
        that took its position and a step up on the one above it -- each
        clamped as any step is, so from where the first row was both land on
        the first row, and from where the last was both land on the last.
        """
        if not snap.needs_you:
            return
        last = len(snap.needs_you) - 1
        if isinstance(self.target, OnDeparted):
            at = self.target.position(snap)
            nxt = max(0, min(last, at + delta - 1 if delta > 0 else at + delta))
        else:
            found = self.index(snap)
            nxt = 0 if found is None else max(0, min(last, found + delta))
        self._index = nxt
        self.to_obligation(snap.needs_you[nxt])

    def to_obligation(self, obligation: Obligation) -> None:
        self.target = OnObligation(obligation.key)
        self._seen = obligation

    def to_node(self, snap: Snapshot, node: NodeId, *, scope: Scope) -> None:
        """
        Point at an agent, by way of the oldest obligation it answers for.

        This is what a click on a rail card does. It moves the one cursor; it does
        **not** filter the inbox. A filtered inbox can hide the oldest item in the
        application while still looking like the inbox, which is defect 2 coming back
        wearing a different coat -- scoping is available one level up, on the project
        header, where it is a deliberate act that can announce itself.

        ``scope`` is required rather than defaulted. A default is the inference this
        parameter exists to remove, and it would be silently wrong for exactly the
        caller that forgot to think about it.
        """
        owed = self.owed_by(snap, node, scope)
        if owed is not None:
            self.to_obligation(owed)
        else:
            # Pinned: an explicit ask for a session with nothing waiting. This is
            # the gesture that opens FOCUS, and it must survive work arriving
            # elsewhere or FOCUS would be unusable whenever the queue is not empty.
            self.target = OnNode(node, pinned=True)
