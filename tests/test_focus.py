"""
The single cursor.

Worth testing hard because every way this breaks is invisible on screen: a cursor
pointed at the wrong agent renders exactly like one pointed at the right agent, and
the consequence is an approval applied to work the operator was not looking at.
"""

from __future__ import annotations

from pptmstr.command import Command, End, Exited
from pptmstr.intents import (
    AgentFinished,
    AgentSpawned,
    ApprovalRequested,
    ApprovalResolved,
    CommandApproved,
    CommandEnded,
    CommandRefused,
    CommandRequested,
    CommandWithdrawn,
    StateChanged,
)
from pptmstr.model import AgentState, CommandOwed, NodeId, PendingApproval, Snapshot
from pptmstr.store import Store
from pptmstr.ui.focus import FocusState, OnDeparted, OnNode, OnObligation, Scope

ROOT: NodeId = ("s1", None)
CHILD: NodeId = ("s1", "agent-a")
OTHER: NodeId = ("s2", None)


def spawn(node: NodeId, parent: NodeId | None = None) -> AgentSpawned:
    return AgentSpawned(
        node_id=node,
        parent=parent,
        task="a task",
        model="claude-sonnet-5",
        started_at=0.0,
        cwd="/x/proj",
    )


def pending(node: NodeId, pid: str, *, at: float) -> PendingApproval:
    return PendingApproval(
        id=pid,
        node=node,
        tool_name="Write",
        tool_use_id=f"tu-{pid}",
        raw_args={},
        summary=f"Write {pid}",
        requested_at=at,
    )


def loaded() -> Store:
    """Three obligations across two sessions, oldest first: p1, p2, p3."""
    store = Store()
    store.apply(spawn(ROOT), now=0.0)
    store.apply(spawn(CHILD, ROOT), now=0.0)
    store.apply(spawn(OTHER), now=0.0)
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1", at=1.0)), now=1.0)
    store.apply(ApprovalRequested(CHILD, pending(CHILD, "p2", at=2.0)), now=2.0)
    store.apply(ApprovalRequested(OTHER, pending(OTHER, "p3", at=3.0)), now=3.0)
    return store


# -- the derived half ----------------------------------------------------------


def test_the_node_is_derived_from_the_obligation() -> None:
    """
    The defect this design exists to prevent.

    There is no second field to set, so there is no way for the node under the
    cursor to disagree with the obligation under the cursor.
    """
    store = loaded()
    focus = FocusState()
    focus.settle(store.snapshot())
    focus.move(store.snapshot(), 1)

    snap = store.snapshot()
    assert focus.obligation(snap) is not None
    assert focus.obligation(snap).approval.id == "p2"  # type: ignore[union-attr]
    assert focus.node(snap) == CHILD


def test_an_empty_world_focuses_nothing() -> None:
    focus = FocusState()
    focus.settle(Store().snapshot())
    assert focus.target is None
    assert focus.node(Store().snapshot()) is None


def test_the_oldest_obligation_is_taken_by_default() -> None:
    store = loaded()
    focus = FocusState()
    focus.settle(store.snapshot())
    assert focus.obligation(store.snapshot()).approval.id == "p1"  # type: ignore[union-attr]


# -- movement ------------------------------------------------------------------


def test_movement_clamps_rather_than_wraps() -> None:
    """
    Wrapping in a work queue means an operator holding `j` at the bottom silently
    restarts at the top and re-reads work already dealt with.
    """
    store = loaded()
    snap = store.snapshot()
    focus = FocusState()
    focus.settle(snap)

    focus.move(snap, -1)
    assert focus.obligation(snap).approval.id == "p1"  # type: ignore[union-attr]

    for _ in range(5):
        focus.move(snap, 1)
    assert focus.obligation(snap).approval.id == "p3"  # type: ignore[union-attr]


def test_the_cursor_is_identity_not_position() -> None:
    """
    A new, older obligation must not drag the cursor onto a different agent.

    ``needs_you`` is age-sorted, so anything arriving with an earlier timestamp
    shifts every later row down one. Under a positional cursor the operator reading
    a diff would find `approve` applied to the row that slid into place beneath it.
    """
    store = loaded()
    focus = FocusState()
    focus.settle(store.snapshot())
    focus.move(store.snapshot(), 1)
    assert focus.obligation(store.snapshot()).approval.id == "p2"  # type: ignore[union-attr]

    # A call parked earlier than everything already queued arrives late.
    store.apply(ApprovalRequested(OTHER, pending(OTHER, "p0", at=0.5)), now=9.0)
    snap = store.snapshot()
    focus.settle(snap)

    assert [o.key.split(":")[-1] for o in snap.needs_you] == ["p0", "p1", "p2", "p3"]
    assert focus.obligation(snap).approval.id == "p2"  # type: ignore[union-attr]
    assert focus.node(snap) == CHILD


def test_answering_lands_on_what_took_its_place() -> None:
    """
    Working a backlog must not throw the cursor back to the top after each answer.
    """
    store = loaded()
    focus = FocusState()
    focus.settle(store.snapshot())
    focus.move(store.snapshot(), 1)  # on p2, index 1

    store.apply(AgentFinished(CHILD, AgentState.CANCELLED, ended_at=5.0), now=5.0)
    snap = store.snapshot()
    focus.settle(snap)

    # p2 is gone; index 1 is now p3.
    assert focus.obligation(snap).approval.id == "p3"  # type: ignore[union-attr]


def test_answering_the_last_one_holds_the_session() -> None:
    """
    Clearing the queue must not also discard where the operator was looking -- the
    context pane would blank at the exact moment they are deciding what to do next.
    """
    store = Store()
    store.apply(spawn(ROOT), now=0.0)
    store.apply(spawn(OTHER), now=0.0)
    store.apply(StateChanged(OTHER, AgentState.AWAITING_INPUT), now=4.0)

    focus = FocusState()
    focus.settle(store.snapshot())
    assert focus.node(store.snapshot()) == OTHER

    store.apply(AgentFinished(OTHER, AgentState.DONE, ended_at=6.0), now=6.0)
    snap = store.snapshot()
    focus.settle(snap)

    assert snap.needs_you == ()
    assert focus.target == OnNode(OTHER)
    assert focus.node(snap) == OTHER


def test_a_vanished_node_is_released() -> None:
    store = Store()
    store.apply(spawn(ROOT), now=0.0)
    focus = FocusState()
    focus.settle(store.snapshot())
    assert focus.target == OnNode(ROOT)

    store.apply(AgentFinished(ROOT, AgentState.DONE, ended_at=1.0), now=1.0)
    store.apply(__import__("pptmstr.intents", fromlist=["x"]).AgentRemoved(ROOT), now=2.0)
    snap = store.snapshot()
    focus.settle(snap)
    assert focus.target is None


# -- the rail as a second input device, not a second selection -----------------


def test_clicking_a_card_moves_to_that_session_s_oldest_obligation() -> None:
    store = loaded()
    snap = store.snapshot()
    focus = FocusState()
    focus.settle(snap)

    focus.to_node(snap, OTHER, scope=Scope.SESSION)
    assert focus.obligation(snap).approval.id == "p3"  # type: ignore[union-attr]


def test_a_card_click_reaches_its_sub_agents_obligations() -> None:
    """
    A root answers for its whole session, and it has to keep doing so.

    A sub-agent parked on approval under a thinking root is the common case, so a
    click on the parent that reached only the parent's own obligations would stop
    reaching the parked call -- leaving the operator poking at the one card that
    looks like the way in.
    """
    store = Store()
    store.apply(spawn(ROOT), now=0.0)
    store.apply(spawn(CHILD, ROOT), now=0.0)
    store.apply(ApprovalRequested(CHILD, pending(CHILD, "sub", at=2.0)), now=2.0)

    snap = store.snapshot()
    focus = FocusState()
    focus.to_node(snap, ROOT, scope=Scope.SESSION)

    assert isinstance(focus.target, OnObligation)
    assert focus.node(snap) == CHILD


def test_clicking_an_idle_card_selects_the_node_itself() -> None:
    """The gesture with nothing to answer -- which is what opens FOCUS."""
    store = loaded()
    store.apply(spawn(("s3", None)), now=4.0)
    snap = store.snapshot()

    focus = FocusState()
    focus.to_node(snap, ("s3", None), scope=Scope.SESSION)
    assert focus.target == OnNode(("s3", None), pinned=True)
    assert focus.obligation(snap) is None
    assert focus.node(snap) == ("s3", None)


def test_a_deliberately_chosen_session_survives_work_arriving_elsewhere() -> None:
    """
    A pinned node holds. Otherwise FOCUS would be unusable whenever anything is
    queued: the cursor would be dragged back to the inbox on the next frame, and
    the pane showing one conversation would keep changing which one.
    """
    store = loaded()
    store.apply(spawn(("s3", None)), now=4.0)
    snap = store.snapshot()

    focus = FocusState()
    focus.to_node(snap, ("s3", None), scope=Scope.SESSION)
    focus.settle(snap)

    assert snap.needs_you  # there is plenty waiting
    assert focus.target == OnNode(("s3", None), pinned=True)


def test_a_default_node_yields_as_soon_as_there_is_a_queue() -> None:
    """
    The defect this distinction exists for.

    Before any agent asks for anything the cursor has only nodes to point at. If
    that unchosen landing spot sticks, the inbox fills up with rows and expands
    none of them -- and the rail still highlights a card, so every pixel on screen
    looks correct while the pane the layout is built around does nothing.
    """
    store = Store()
    store.apply(spawn(ROOT), now=0.0)
    focus = FocusState()
    focus.settle(store.snapshot())
    assert focus.target == OnNode(ROOT)
    assert focus.target.pinned is False  # type: ignore[union-attr]

    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1", at=3.0)), now=3.0)
    snap = store.snapshot()
    focus.settle(snap)

    assert isinstance(focus.target, OnObligation)
    assert focus.obligation(snap).approval.id == "p1"  # type: ignore[union-attr]


def test_selecting_a_sub_agent_reaches_its_own_call_not_its_sessions_oldest() -> None:
    """
    A card stands for an agent, so selecting one lands on what *it* is asking for.

    The root's approval is older, so a session-wide match would answer it instead
    and the operator would be reading a diff belonging to the parent while the row
    they clicked stayed parked -- the two-cursor failure with one cursor.
    """
    store = Store()
    store.apply(spawn(ROOT), now=0.0)
    store.apply(spawn(CHILD, ROOT), now=0.0)
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1", at=1.0)), now=1.0)
    store.apply(ApprovalRequested(CHILD, pending(CHILD, "p2", at=2.0)), now=2.0)

    snap = store.snapshot()
    focus = FocusState()
    focus.to_node(snap, CHILD, scope=Scope.AGENT)

    assert focus.obligation(snap).approval.id == "p2"  # type: ignore[union-attr]
    assert focus.node(snap) == CHILD


def test_selecting_a_sub_agent_that_owes_nothing_pins_it() -> None:
    """
    Its session has work waiting and that is not this agent's. Widening to the
    session here would mean clicking one row and selecting a sibling.
    """
    store = Store()
    store.apply(spawn(ROOT), now=0.0)
    store.apply(spawn(CHILD, ROOT), now=0.0)
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1", at=1.0)), now=1.0)

    snap = store.snapshot()
    focus = FocusState()
    focus.to_node(snap, CHILD, scope=Scope.AGENT)

    assert focus.target == OnNode(CHILD, pinned=True)
    assert focus.node(snap) == CHILD


def test_an_unchosen_cursor_yields_to_a_queue_in_another_session() -> None:
    """
    The regression the exact-node branch widens rather than causes.

    ``settle`` used to route the unpinned case through ``to_node``, whose fallback
    pins -- so a cursor that had merely landed somewhere promoted itself to a
    deliberate choice the moment work appeared anywhere else, and never yielded
    again. That is verbatim the failure ``OnNode`` is split in two to prevent:
    ``index`` returns None, the inbox draws rows and expands none of them, and the
    rail highlights a card, so every pixel looks correct.

    Two sessions, no sub-agents. The single-session test above passes either way
    because the arriving obligation happens to belong to the parked node.
    """
    store = Store()
    store.apply(spawn(ROOT), now=0.0)
    store.apply(spawn(OTHER), now=0.0)
    focus = FocusState()
    focus.settle(store.snapshot())
    assert focus.target == OnNode(ROOT, pinned=False)

    store.apply(ApprovalRequested(OTHER, pending(OTHER, "p1", at=3.0)), now=3.0)
    snap = store.snapshot()
    focus.settle(snap)

    assert focus.target == OnObligation("approval:p1")
    assert focus.index(snap) == 0
    assert focus.node(snap) == OTHER


def test_a_sub_agent_the_cursor_merely_landed_on_yields_too() -> None:
    """
    ``_last_node`` can hold a sub-agent, so the same unpinned node can now be one.
    Its own scope is narrower, which makes the empty case -- and therefore the pin
    that used to follow it -- more reachable, not less.
    """
    store = Store()
    store.apply(spawn(ROOT), now=0.0)
    store.apply(spawn(CHILD, ROOT), now=0.0)
    focus = FocusState()
    focus.target = OnNode(CHILD, pinned=False)

    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1", at=3.0)), now=3.0)
    snap = store.snapshot()
    focus.settle(snap)

    assert focus.target == OnObligation("approval:p1")
    assert focus.node(snap) == ROOT


# -- closed cards and removed sessions ----------------------------------------------
#
# Snapshots from the reducer (``tests/test_ui_text``): what "recalled" and "off the
# fleet" are is the store's to say.


def _fleet(*, ran: bool = True, closed: bool = True, removed: bool = True) -> Store:
    from tests.test_ui_text import recall, run, run_and_remove

    store = Store()
    # In this order on purpose: the removed session is the oldest root, so a
    # default that took the first of ``order`` would take it.
    if removed:
        run_and_remove(store)
    if closed:
        recall(store)
    if ran:
        run(store)
    return store


def test_the_cursor_defaults_past_removed_and_closed_sessions_to_one_that_ran() -> None:
    from tests.test_ui_text import RAN_ROOT, REMOVED_ROOT

    snap = _fleet().snapshot()
    assert snap.order[0] == REMOVED_ROOT

    focus = FocusState()
    focus.settle(snap)

    assert focus.target == OnNode(RAN_ROOT)


def test_with_only_closed_and_removed_sessions_the_cursor_rests_on_nothing() -> None:
    """
    Startup with a recalled fleet. Landing on the oldest closed card would open
    every pane on "not loaded" before the operator had chosen anything.
    """
    snap = _fleet(ran=False).snapshot()
    assert len(snap.order) == 3

    focus = FocusState()
    focus.settle(snap)

    assert focus.target is None and focus.node(snap) is None


def test_a_click_on_a_closed_card_holds() -> None:
    """Nothing chooses a closed card; the operator still can, and it is a pin."""
    from tests.test_ui_text import CLOSED_ROOT

    snap = _fleet().snapshot()
    focus = FocusState()
    focus.to_node(snap, CLOSED_ROOT, scope=Scope.SESSION)
    focus.settle(snap)
    focus.settle(snap)

    assert focus.target == OnNode(CLOSED_ROOT, pinned=True)
    assert focus.node(snap) == CLOSED_ROOT


def test_a_pinned_cursor_does_not_stay_on_a_session_that_is_then_removed() -> None:
    from pptmstr.intents import SessionDetached
    from tests.test_ui_text import RAN_ROOT

    store = _fleet(removed=False)
    before = store.snapshot()
    focus = FocusState()
    focus.to_node(before, RAN_ROOT, scope=Scope.SESSION)
    focus.settle(before)
    assert focus.node(before) == RAN_ROOT

    store.apply(AgentFinished(RAN_ROOT, AgentState.DONE, ended_at=9.0), now=9.0)
    store.apply(SessionDetached(RAN_ROOT[0]), now=10.0)
    after = store.snapshot()
    assert after.off_fleet(RAN_ROOT[0]) and RAN_ROOT in after.nodes

    # Before ``settle`` has seen the snapshot: every pane asks ``node``.
    assert focus.node(after) is None
    focus.settle(after)
    # Only a closed card is left, and that is not a default either.
    assert focus.target is None


def test_clearing_the_queue_does_not_return_to_a_session_removed_meanwhile() -> None:
    """
    ``_last_node`` is where the cursor goes when the last obligation is answered.
    A session can be answered, end and be removed between two frames, and the
    cursor must not go back to a card that is no longer drawn.
    """
    from pptmstr.intents import ApprovalResolved, SessionDetached

    store = Store()
    store.apply(spawn(OTHER), now=0.0)
    store.apply(spawn(ROOT), now=0.0)
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1", at=1.0)), now=1.0)
    focus = FocusState()
    focus.settle(store.snapshot())
    assert focus.node(store.snapshot()) == ROOT

    store.apply(ApprovalResolved(ROOT, "p1", approved=True), now=2.0)
    store.apply(AgentFinished(ROOT, AgentState.DONE, ended_at=3.0), now=3.0)
    store.apply(SessionDetached(ROOT[0]), now=4.0)
    snap = store.snapshot()
    assert snap.off_fleet(ROOT[0]) and not snap.needs_you

    focus.settle(snap)

    assert focus.target == OnNode(OTHER)


def test_a_session_that_ended_and_was_not_removed_is_still_returned_to() -> None:
    """The control for the test above: ending is not what drops the cursor."""
    from pptmstr.intents import ApprovalResolved

    store = Store()
    store.apply(spawn(OTHER), now=0.0)
    store.apply(spawn(ROOT), now=0.0)
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1", at=1.0)), now=1.0)
    focus = FocusState()
    focus.settle(store.snapshot())

    store.apply(ApprovalResolved(ROOT, "p1", approved=True), now=2.0)
    store.apply(AgentFinished(ROOT, AgentState.DONE, ended_at=3.0), now=3.0)
    focus.settle(store.snapshot())

    assert focus.target == OnNode(ROOT)


# -- a row that leaves from under the cursor, where a command is involved -------------
#
# A command runs outside the sandbox on the strength of the operator having read
# it. So the cursor is never moved ONTO a command by another row going, and a
# command that goes is not silently replaced by whatever was next.

WALL = 1_790_000_000.0


def _cid(n: int) -> str:
    return f"cmd-20261013T12000{n}Z-{n:012x}"


def _ask(store: Store, n: int, text: str, *, node: NodeId = CHILD, now: float) -> None:
    command = Command(
        id=_cid(n), asker=node, asked=text, reason="why", expects="what", asked_at=WALL + now
    )
    store.apply(CommandRequested(node, command, None, WALL + now), now=now)


def _team() -> Store:
    store = Store()
    store.apply(spawn(ROOT), now=0.0)
    store.apply(spawn(CHILD, ROOT), now=0.0)
    store.apply(spawn(OTHER), now=0.0)
    return store


def _on(focus: FocusState, snap: Snapshot) -> str | None:
    current = focus.obligation(snap)
    return None if current is None else current.key


def test_withdrawing_the_command_being_read_does_not_put_the_next_one_under_the_cursor() -> None:
    """
    The swap. An agent asks for ``pip install requests`` and for
    ``pip install reqeusts`` and withdraws the first while the operator reads
    it. The second must not open where the first was.
    """
    store = _team()
    _ask(store, 1, "pip install requests", now=2.0)
    _ask(store, 2, "pip install reqeusts", now=3.0)
    focus = FocusState()
    focus.settle(store.snapshot())
    assert _on(focus, store.snapshot()) == f"command:{_cid(1)}"

    store.apply(CommandWithdrawn(CHILD, _cid(1), None, WALL + 4.0), now=4.0)
    snap = store.snapshot()
    assert [o.key for o in snap.needs_you] == [f"command:{_cid(2)}"]
    for _ in range(3):
        focus.settle(snap)
        assert focus.obligation(snap) is None and focus.index(snap) is None

    gone = focus.departed
    assert isinstance(gone, OnDeparted) and gone.position(snap) == 0
    assert gone.after == (f"command:{_cid(2)}",)
    assert isinstance(gone.left, CommandOwed) and gone.left.command.id == _cid(1)
    # The panes that follow the cursor stay on the session being dealt with.
    assert focus.node(snap) == CHILD


def test_a_command_that_arrives_after_the_one_being_read_left_does_not_take_the_cursor() -> None:
    """
    The same swap with the queue empty in between: withdraw, then ask. An
    unchosen cursor yields to a queue, and this one must not.
    """
    store = _team()
    _ask(store, 1, "pip install requests", now=2.0)
    focus = FocusState()
    focus.settle(store.snapshot())

    store.apply(CommandWithdrawn(CHILD, _cid(1), None, WALL + 4.0), now=4.0)
    focus.settle(store.snapshot())
    assert store.snapshot().needs_you == () and focus.departed is not None

    _ask(store, 2, "pip install reqeusts", now=5.0)
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1", at=6.0)), now=6.0)
    snap = store.snapshot()
    focus.settle(snap)
    focus.settle(snap)

    assert focus.obligation(snap) is None
    assert focus.departed is not None and focus.departed.left.key == f"command:{_cid(1)}"


def test_a_row_that_is_not_a_command_is_followed_by_the_command_that_took_its_place() -> None:
    """
    The cursor may come to rest on a command by any route. What it must not do
    there is show the command, and that is not the cursor's to decide: the
    row is closed until the operator opens it (``command_view``,
    ``tests/test_command_rows.py``). So an approval that leaves is followed as
    every row that is not a command is, whatever follows it.
    """
    store = _team()
    store.apply(ApprovalRequested(OTHER, pending(OTHER, "p1", at=1.0)), now=1.0)
    _ask(store, 1, "make install", now=2.0)
    focus = FocusState()
    focus.settle(store.snapshot())
    assert _on(focus, store.snapshot()) == "approval:p1"

    store.apply(AgentFinished(OTHER, AgentState.CANCELLED, ended_at=5.0), now=5.0)
    snap = store.snapshot()
    focus.settle(snap)

    assert _on(focus, snap) == f"command:{_cid(1)}" and focus.departed is None


def test_a_command_asked_into_a_queue_another_row_emptied_takes_the_cursor_like_any_row() -> None:
    """
    An approval, answered, and then a command into the empty queue: the cursor
    was left on a session nobody chose, which yields to the queue. Nothing
    about a command makes that a different case from the one above -- and a
    rule that covered one and not the other is what this replaces.
    """
    store = _team()
    store.apply(ApprovalRequested(OTHER, pending(OTHER, "p1", at=1.0)), now=1.0)
    focus = FocusState()
    focus.settle(store.snapshot())
    store.apply(ApprovalResolved(OTHER, "p1", True), now=2.0)
    focus.settle(store.snapshot())
    assert focus.target == OnNode(OTHER) and focus.departed is None

    _ask(store, 1, "make install", now=3.0)
    snap = store.snapshot()
    focus.settle(snap)

    assert _on(focus, snap) == f"command:{_cid(1)}"


def test_a_command_that_leaves_is_marked_even_when_what_follows_it_is_not_a_command() -> None:
    store = _team()
    _ask(store, 1, "make install", now=1.0)
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1", at=2.0)), now=2.0)
    focus = FocusState()
    focus.settle(store.snapshot())

    store.apply(CommandRefused(_cid(1), "no", WALL + 3.0), now=3.0)
    snap = store.snapshot()
    focus.settle(snap)

    assert focus.obligation(snap) is None and focus.departed is not None


def test_a_command_that_starts_running_keeps_the_cursor_and_one_that_ends_is_marked() -> None:
    """Asked to running is the same row. Ending is the row leaving."""
    store = _team()
    _ask(store, 1, "make install", now=1.0)
    _ask(store, 2, "make install", now=2.0, node=ROOT)
    focus = FocusState()
    focus.settle(store.snapshot())

    store.apply(
        CommandApproved(_cid(1), "make install", "/bin/sh", "/x/proj", 600, WALL + 3.0), now=3.0
    )
    focus.settle(store.snapshot())
    assert _on(focus, store.snapshot()) == f"command:{_cid(1)}" and focus.departed is None

    end = End(outcome=Exited(code=0), at=WALL + 4.0, duration_s=1.0)
    store.apply(CommandEnded(_cid(1), end), now=4.0)
    snap = store.snapshot()
    focus.settle(snap)
    assert focus.obligation(snap) is None and focus.departed is not None


def test_from_where_a_row_was_a_step_down_is_the_row_that_took_its_place() -> None:
    """
    Opening the next row is the operator's act: ``j`` lands on what slid into
    the position, ``k`` on the row above it, and either ends the mark.
    """

    def departed() -> tuple[FocusState, Snapshot]:
        fresh = Store()
        for intent, now in applied:
            fresh.apply(intent, now=now)
        focus = FocusState()
        focus.settle(fresh.snapshot())
        focus.move(fresh.snapshot(), 1)  # on command 1, index 1
        fresh.apply(CommandWithdrawn(CHILD, _cid(1), None, WALL + 5.0), now=5.0)
        snap = fresh.snapshot()
        focus.settle(snap)
        assert focus.departed is not None and focus.departed.position(snap) == 1
        return focus, snap

    applied = [
        (spawn(ROOT), 0.0),
        (spawn(CHILD, ROOT), 0.0),
        (ApprovalRequested(ROOT, pending(ROOT, "p1", at=1.0)), 1.0),
        *[
            (
                CommandRequested(
                    node,
                    Command(
                        id=_cid(n), asker=node, asked="make", reason="", expects="", asked_at=WALL
                    ),
                    None,
                    WALL + n,
                ),
                1.0 + n,
            )
            for n, node in ((1, CHILD), (2, CHILD), (3, ROOT))
        ],
    ]

    down, snap = departed()
    down.move(snap, 1)
    assert _on(down, snap) == f"command:{_cid(2)}" and down.departed is None

    up, snap = departed()
    up.move(snap, -1)
    assert _on(up, snap) == "approval:p1" and up.departed is None

    clicked, snap = departed()
    clicked.to_obligation(snap.needs_you[2])
    clicked.settle(snap)
    assert _on(clicked, snap) == f"command:{_cid(3)}" and clicked.departed is None

    card, snap = departed()
    card.to_node(snap, ROOT, scope=Scope.AGENT)
    card.settle(snap)
    assert _on(card, snap) == "approval:p1" and card.departed is None


def test_a_step_from_where_the_last_row_was_is_clamped_to_the_queue() -> None:
    store = _team()
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1", at=1.0)), now=1.0)
    _ask(store, 1, "make a", now=2.0)
    focus = FocusState()
    focus.settle(store.snapshot())
    focus.move(store.snapshot(), 1)
    store.apply(CommandWithdrawn(CHILD, _cid(1), None, WALL + 5.0), now=5.0)
    snap = store.snapshot()
    focus.settle(snap)
    assert focus.departed is not None and focus.departed.position(snap) == 1
    assert focus.departed.after == ()

    focus.move(snap, 1)

    assert _on(focus, snap) == "approval:p1"


def test_a_step_up_from_where_the_first_row_was_is_clamped_to_the_first_row() -> None:
    """
    There is no row above it, and the cursor is the operator's to put on a row.
    """
    store = _team()
    _ask(store, 1, "make a", now=1.0)
    _ask(store, 2, "make b", now=2.0)
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1", at=3.0)), now=3.0)
    focus = FocusState()
    focus.settle(store.snapshot())
    store.apply(CommandWithdrawn(CHILD, _cid(1), None, WALL + 5.0), now=5.0)
    snap = store.snapshot()
    focus.settle(snap)
    assert focus.departed is not None and focus.departed.position(snap) == 0

    focus.move(snap, -1)

    assert _on(focus, snap) == f"command:{_cid(2)}" and focus.departed is None


def test_where_a_row_was_is_kept_by_the_rows_that_stood_below_it_and_not_by_a_number() -> None:
    """
    The queue goes on changing under the mark. A row above it leaving moves
    every index and not where the row was: that is still ahead of the row
    that stood below it, and when that one goes too, ahead of the next.
    """
    store = _team()
    store.apply(ApprovalRequested(OTHER, pending(OTHER, "p1", at=1.0)), now=1.0)
    _ask(store, 1, "make a", now=2.0)
    _ask(store, 2, "make b", now=3.0)
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p2", at=4.0)), now=4.0)
    focus = FocusState()
    focus.settle(store.snapshot())
    focus.move(store.snapshot(), 1)
    store.apply(CommandWithdrawn(CHILD, _cid(1), None, WALL + 5.0), now=5.0)
    snap = store.snapshot()
    focus.settle(snap)
    gone = focus.departed
    assert gone is not None and gone.after == (f"command:{_cid(2)}", "approval:p2")
    assert gone.position(snap) == 1

    # The row above goes: the mark is ahead of command 2, which is now first.
    store.apply(ApprovalResolved(OTHER, "p1", True), now=6.0)
    snap = store.snapshot()
    focus.settle(snap)
    assert focus.departed is gone and gone.position(snap) == 0
    assert snap.needs_you[0].key == f"command:{_cid(2)}"

    # A row arrives at the end: nothing moves.
    _ask(store, 3, "make c", now=7.0, node=ROOT)
    snap = store.snapshot()
    focus.settle(snap)
    assert gone.position(snap) == 0

    # The row it stood ahead of goes: it is ahead of the next that was below.
    store.apply(CommandWithdrawn(CHILD, _cid(2), None, WALL + 8.0), now=8.0)
    snap = store.snapshot()
    focus.settle(snap)
    assert [o.key for o in snap.needs_you] == ["approval:p2", f"command:{_cid(3)}"]
    assert gone.position(snap) == 0

    # Every row that was below it goes: it is after what is left.
    store.apply(ApprovalResolved(ROOT, "p2", True), now=9.0)
    snap = store.snapshot()
    focus.settle(snap)
    assert gone.position(snap) == 1 == len(snap.needs_you)
    # And a step down from there is the last row.
    focus.move(snap, 1)
    assert _on(focus, snap) == f"command:{_cid(3)}"


def test_rows_that_are_not_commands_still_follow_one_another() -> None:
    """
    What this rule does not change, pinned so that widening it is a decision:
    an approval that leaves is followed by the approval that took its place.
    The typed-key hazard of that is ``review.Hold``'s.
    """
    store = loaded()
    focus = FocusState()
    focus.settle(store.snapshot())

    store.apply(AgentFinished(ROOT, AgentState.CANCELLED, ended_at=5.0), now=5.0)
    snap = store.snapshot()
    focus.settle(snap)

    assert _on(focus, snap) == "approval:p2" and focus.departed is None
