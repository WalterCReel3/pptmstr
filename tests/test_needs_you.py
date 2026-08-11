"""
``Snapshot.needs_you``: one projection over every way an agent can block.

The defect this replaced was not that approvals were counted wrongly -- they were
counted correctly, and everything else was not counted at all. So most of what is
worth asserting here is about the *union*: that a question and a crash are
obligations on the same footing as an approval, that they sort together by age, and
that nothing counts one kind while implying it has checked all three.
"""

from __future__ import annotations

from pptmstr.intents import AgentFinished, AgentSpawned, ApprovalRequested, StateChanged
from pptmstr.model import (
    AgentState,
    ApprovalNeeded,
    NodeId,
    ObligationKind,
    PendingApproval,
    QuestionPending,
    SessionFailed,
)
from pptmstr.store import Store

ROOT: NodeId = ("sess-1", None)
CHILD: NodeId = ("sess-1", "agent-a")
OTHER: NodeId = ("sess-2", None)


def spawn(node: NodeId, parent: NodeId | None = None, *, cwd: str | None = None) -> AgentSpawned:
    return AgentSpawned(
        node_id=node,
        parent=parent,
        task="do a thing",
        model="claude-opus-5",
        started_at=0.0,
        cwd=cwd,
    )


def pending(node: NodeId, pid: str, *, at: float) -> PendingApproval:
    return PendingApproval(
        id=pid,
        node=node,
        tool_name="Write",
        tool_use_id=f"tu-{pid}",
        raw_args={"file_path": "/tmp/x"},
        summary="Write /tmp/x",
        requested_at=at,
    )


# -- the union -----------------------------------------------------------------


def test_a_question_is_an_obligation() -> None:
    """
    The case the whole projection exists for.

    A session that ends its turn is waiting on the operator exactly as much as one
    parked on an approval, and before this it appeared in no queue and no count.
    """
    store = Store()
    store.apply(spawn(ROOT), now=1.0)
    store.apply(StateChanged(ROOT, AgentState.AWAITING_INPUT), now=10.0)

    (obligation,) = store.snapshot().needs_you
    assert isinstance(obligation, QuestionPending)
    assert obligation.kind is ObligationKind.QUESTION
    assert obligation.node == ROOT
    assert obligation.since == 10.0


def test_a_failure_is_an_obligation() -> None:
    store = Store()
    store.apply(spawn(ROOT), now=1.0)
    store.apply(AgentFinished(ROOT, AgentState.FAILED, ended_at=7.0, error="boom\ndetail"), now=9.0)

    (obligation,) = store.snapshot().needs_you
    assert isinstance(obligation, SessionFailed)
    assert obligation.error == "boom\ndetail"
    # Summarised to the first line; the rest belongs in the expanded row.
    assert obligation.summary == "boom"
    # It has been waiting since it died, not since the frame noticed.
    assert obligation.since == 7.0


def test_all_three_kinds_sort_together_by_age() -> None:
    """
    Oldest first across kinds, not approvals first.

    Grouping by kind would put a two-second-old approval above a ten-minute-old
    crash, which is the ordering the operator is least served by.
    """
    store = Store()
    store.apply(spawn(ROOT), now=0.0)
    store.apply(spawn(OTHER), now=0.0)
    store.apply(spawn(CHILD, ROOT), now=0.0)

    store.apply(ApprovalRequested(CHILD, pending(CHILD, "recent", at=90.0)), now=90.0)
    store.apply(StateChanged(OTHER, AgentState.AWAITING_INPUT), now=50.0)
    store.apply(AgentFinished(ROOT, AgentState.FAILED, ended_at=20.0, error="died"), now=20.0)

    kinds = [o.kind for o in store.snapshot().needs_you]
    assert kinds == [
        ObligationKind.FAILURE,
        ObligationKind.QUESTION,
        ObligationKind.APPROVAL,
    ]


def test_every_parked_call_is_listed_not_just_the_last() -> None:
    """One turn can park several calls; each is its own obligation."""
    store = Store()
    store.apply(spawn(ROOT), now=0.0)
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1", at=1.0)), now=1.0)
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p2", at=2.0)), now=2.0)

    needs = store.snapshot().needs_you
    assert [o.approval.id for o in needs if isinstance(o, ApprovalNeeded)] == ["p1", "p2"]


def test_approvals_is_the_subset_and_not_a_second_walk() -> None:
    """
    The watchdog's view must be filtered out of the union rather than re-derived.

    An invariant check that builds its own picture of the world is checking itself.
    """
    store = Store()
    store.apply(spawn(ROOT), now=0.0)
    store.apply(spawn(OTHER), now=0.0)
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1", at=5.0)), now=5.0)
    store.apply(StateChanged(OTHER, AgentState.AWAITING_INPUT), now=6.0)

    snap = store.snapshot()
    assert len(snap.needs_you) == 2
    assert [p.id for p in snap.approvals] == ["p1"]


def test_a_working_agent_owes_nothing() -> None:
    store = Store()
    store.apply(spawn(ROOT), now=0.0)
    store.apply(StateChanged(ROOT, AgentState.THINKING), now=1.0)
    assert store.snapshot().needs_you == ()


def test_closing_a_session_clears_its_question() -> None:
    """
    Answering an obligation by closing must remove it.

    DONE means the operator closed the session, so a closed session owes nothing --
    otherwise the count never falls and the inbox fills with work already done.
    """
    store = Store()
    store.apply(spawn(ROOT), now=0.0)
    store.apply(StateChanged(ROOT, AgentState.AWAITING_INPUT), now=5.0)
    assert len(store.snapshot().needs_you) == 1

    store.apply(AgentFinished(ROOT, AgentState.DONE, ended_at=8.0), now=8.0)
    assert store.snapshot().needs_you == ()


def test_a_parked_node_owes_an_approval_not_a_question() -> None:
    """
    The kinds are mutually exclusive per node by construction.

    ``pending`` holds a node in AWAITING_APPROVAL, so a late StateChanged claiming
    AWAITING_INPUT must not add a second obligation for the same node -- that would
    double-count one blocked agent and inflate every badge reading the union.
    """
    store = Store()
    store.apply(spawn(ROOT), now=0.0)
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1", at=1.0)), now=1.0)
    store.apply(StateChanged(ROOT, AgentState.AWAITING_INPUT), now=2.0)

    needs = store.snapshot().needs_you
    assert len(needs) == 1
    assert isinstance(needs[0], ApprovalNeeded)


# -- state_since ---------------------------------------------------------------


def test_state_since_moves_only_when_the_state_does() -> None:
    """
    A repeated state must not reset the clock.

    Wait time is what the inbox sorts on. A stream of same-state updates -- which is
    exactly what a busy agent emits -- would otherwise keep an obligation looking
    freshly arrived and hold it at the bottom of the queue forever.
    """
    store = Store()
    store.apply(spawn(ROOT), now=0.0)
    store.apply(StateChanged(ROOT, AgentState.AWAITING_INPUT), now=10.0)
    store.apply(StateChanged(ROOT, AgentState.AWAITING_INPUT, topic="still waiting"), now=40.0)

    assert store.snapshot().nodes[ROOT].state_since == 10.0
    assert store.snapshot().needs_you[0].since == 10.0


def test_state_since_is_stamped_on_a_real_transition() -> None:
    store = Store()
    store.apply(spawn(ROOT), now=3.0)
    assert store.snapshot().nodes[ROOT].state_since == 3.0

    store.apply(StateChanged(ROOT, AgentState.THINKING), now=11.0)
    assert store.snapshot().nodes[ROOT].state_since == 11.0


def test_a_batch_shares_one_instant() -> None:
    """
    apply_all takes the frame clock, so everything applied in one frame agrees.

    Reading the clock per intent would let two obligations created by the same turn
    sort against each other on scheduling noise.
    """
    store = Store()
    store.apply_all(
        [spawn(ROOT), spawn(OTHER), StateChanged(ROOT, AgentState.AWAITING_INPUT)], now=42.0
    )
    snap = store.snapshot()
    assert snap.nodes[ROOT].state_since == 42.0
    assert snap.nodes[OTHER].state_since == 42.0


# -- cwd -----------------------------------------------------------------------


def test_cwd_is_recorded_and_inherited_by_subagents() -> None:
    """
    A sub-agent runs in its session's directory, and its spawn hook is not told one.

    Resolving the inheritance in the store means no emitter has to remember it --
    the alternative is the same fact maintained in two places by convention, which
    is the shape of every sync bug this codebase has had.
    """
    store = Store()
    store.apply(spawn(ROOT, cwd="/home/wreel/Source/orbital"), now=0.0)
    store.apply(spawn(CHILD, ROOT), now=1.0)

    snap = store.snapshot()
    assert snap.nodes[ROOT].cwd == "/home/wreel/Source/orbital"
    assert snap.nodes[CHILD].cwd == "/home/wreel/Source/orbital"


def test_a_recovered_node_inherits_its_session_directory() -> None:
    """
    The placeholder built for an approval from an unannounced agent still has to
    land in the right project lane, or a recovered node is filed on its own.
    """
    store = Store()
    store.apply(spawn(ROOT, cwd="/home/wreel/Source/orbital"), now=0.0)
    store.apply(ApprovalRequested(CHILD, pending(CHILD, "p1", at=1.0)), now=1.0)

    assert store.snapshot().nodes[CHILD].cwd == "/home/wreel/Source/orbital"
