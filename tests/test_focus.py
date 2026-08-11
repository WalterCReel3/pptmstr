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
from pptmstr.ui.focus import FocusState, OnNode, OnObligation

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

    focus.to_node(snap, OTHER)
    assert focus.obligation(snap).approval.id == "p3"  # type: ignore[union-attr]


def test_a_card_click_reaches_its_sub_agents_obligations() -> None:
    """
    A card stands for a session, and a sub-agent's parked call is that session's
    obligation -- the operator clicking the card means "deal with this session".
    """
    store = Store()
    store.apply(spawn(ROOT), now=0.0)
    store.apply(spawn(CHILD, ROOT), now=0.0)
    store.apply(ApprovalRequested(CHILD, pending(CHILD, "sub", at=2.0)), now=2.0)

    snap = store.snapshot()
    focus = FocusState()
    focus.to_node(snap, ROOT)

    assert isinstance(focus.target, OnObligation)
    assert focus.node(snap) == CHILD


def test_clicking_an_idle_card_selects_the_node_itself() -> None:
    """The gesture with nothing to answer -- which is what opens FOCUS."""
    store = loaded()
    store.apply(spawn(("s3", None)), now=4.0)
    snap = store.snapshot()

    focus = FocusState()
    focus.to_node(snap, ("s3", None))
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
    focus.to_node(snap, ("s3", None))
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
