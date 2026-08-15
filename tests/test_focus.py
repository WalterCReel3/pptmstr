"""
The single cursor.

Worth testing hard because every way this breaks is invisible on screen: a cursor
pointed at the wrong agent renders exactly like one pointed at the right agent, and
the consequence is an approval applied to work the operator was not looking at.
"""

from __future__ import annotations

from pptmstr.intents import AgentFinished, AgentSpawned, ApprovalRequested, StateChanged
from pptmstr.model import AgentState, NodeId, PendingApproval
from pptmstr.store import Store
from pptmstr.ui.focus import FocusState, OnNode, OnObligation, Scope

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
