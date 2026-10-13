"""
The fold: which sub-agents of a session are drawn and which become a count.

Every snapshot here is built by the real reducer, so a clause is tested against the
shape the store actually gives the fact it reads -- an obligation as ``needs_you``
lists it, a claim as the claim arm records it -- and not against a record assembled
to match the rule.
"""

from __future__ import annotations

import random
from typing import Any

import pytest

from pptmstr.intents import (
    AgentFinished,
    AgentResumed,
    AgentSpawned,
    ApprovalRequested,
    ConcernPosted,
    DecisionAnswered,
    DecisionDismissed,
    DecisionRaised,
    DecisionsRead,
    DecisionWithdrawn,
    FailureAcknowledged,
    InboxRead,
    Intent,
    StateChanged,
    TaskClaimRequested,
    TaskCompleted,
    TaskDeclared,
    TaskReleased,
)
from pptmstr.model import (
    AgentState,
    Answered,
    Concern,
    ConcernState,
    Decision,
    Dismissed,
    NodeId,
    PendingApproval,
    Snapshot,
    Standing,
    Task,
    TaskState,
)
from pptmstr.store import Store
from pptmstr.ui.fleet_view import Folded, fold

ROOT: NodeId = ("sess-1", None)
A: NodeId = ("sess-1", "agent-a")
B: NodeId = ("sess-1", "agent-b")
# A sub-agent of A, so A has something beneath it.
A1: NodeId = ("sess-1", "agent-a1")
OTHER: NodeId = ("sess-2", None)
OTHER_A: NodeId = ("sess-2", "agent-x")


def spawn(node: NodeId, parent: NodeId | None = None, *, at: float = 0.0) -> AgentSpawned:
    return AgentSpawned(node_id=node, parent=parent, task="t", model="m", started_at=at)


def done(node: NodeId, *, at: float = 5.0) -> AgentFinished:
    return AgentFinished(node, AgentState.DONE, ended_at=at)


def decision(did: str, asker: NodeId) -> Decision:
    return Decision(
        id=did,
        asker=asker,
        question="q",
        blocks=(),
        if_unanswered="carry on",
        evidence="e",
        recommendation="r",
        standing=Standing.TAKEN,
        reversible=True,
        raised_at=0.0,
    )


def concern(cid: str, sender: NodeId, recipient: NodeId) -> Concern:
    return Concern(id=cid, sender=sender, recipient=recipient, subject="s", body="b", posted_at=1.0)


def ended_pair() -> Store:
    """A session whose two sub-agents have both ended, owing and holding nothing."""
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(A, ROOT, at=1.0))
    store.apply(spawn(B, ROOT, at=1.0))
    store.apply(done(A))
    store.apply(done(B))
    return store


def folded_of(store: Store, cursor: NodeId | None = None) -> Folded:
    return fold(store.snapshot(), ROOT, cursor, True)


# -- the baseline every clause is a departure from -----------------------------


def test_an_ended_sub_agent_that_owes_and_holds_nothing_is_folded() -> None:
    view = folded_of(ended_pair())

    assert view.folded == (A, B)
    assert view.drawn == ()
    assert view.count == 2
    assert view.opened is False


@pytest.mark.parametrize("state", [AgentState.DONE, AgentState.CANCELLED])
def test_every_declared_end_folds(state: AgentState) -> None:
    store = ended_pair()
    store.apply(spawn(A1, ROOT, at=1.0))
    store.apply(AgentFinished(A1, state, ended_at=5.0))

    assert A1 in folded_of(store).folded


def test_the_root_is_never_in_either_list() -> None:
    view = folded_of(ended_pair())

    assert ROOT not in view.folded and ROOT not in view.drawn


# -- one case per clause --------------------------------------------------------


@pytest.mark.parametrize(
    "state",
    [s for s in AgentState if not s.is_terminal],
)
def test_a_sub_agent_that_has_not_ended_is_drawn(state: AgentState) -> None:
    store = ended_pair()
    store.apply(spawn(A, ROOT, at=1.0))
    store.apply(StateChanged(A, state))
    assert store.snapshot().nodes[A].state is state

    view = folded_of(store)

    assert view.drawn == (A,)
    assert view.folded == (B,)


def test_a_failed_sub_agent_nobody_has_acknowledged_is_drawn_until_it_is() -> None:
    store = ended_pair()
    store.apply(spawn(A, ROOT, at=1.0))
    store.apply(AgentFinished(A, AgentState.FAILED, ended_at=5.0, error="boom"))
    assert any(o.node == A for o in store.snapshot().needs_you)

    assert folded_of(store).drawn == (A,)

    store.apply(FailureAcknowledged(A))
    assert folded_of(store).folded == (A, B)


def test_an_ended_sub_agent_with_an_open_decision_is_drawn_until_it_is_ended_and_read() -> None:
    """
    The decision clause and the unread-answer clause are two facts that hand over
    to each other: the obligation goes when the operator answers, and the answer
    is then waiting for its asker.
    """
    store = ended_pair()
    store.apply(DecisionRaised(A, decision("d-1", A)))
    assert [o.node for o in store.snapshot().needs_you] == [A]
    assert folded_of(store).drawn == (A,), "the operator owes it an answer"

    store.apply(DecisionAnswered("d-1", "yes"))
    assert store.snapshot().needs_you == ()
    assert folded_of(store).drawn == (A,), "it has not read the answer"

    store.apply(DecisionsRead(A, "r", at=9.0, decision_id="d-1"))
    assert folded_of(store).folded == (A, B)


def test_a_dismissal_it_has_not_read_keeps_it_drawn() -> None:
    store = ended_pair()
    store.apply(DecisionRaised(A, decision("d-1", A)))
    store.apply(DecisionDismissed("d-1"))

    assert folded_of(store).drawn == (A,)


def test_the_roots_read_of_a_sub_agents_answer_does_not_fold_the_sub_agent() -> None:
    store = ended_pair()
    store.apply(DecisionRaised(A, decision("d-1", A)))
    store.apply(DecisionAnswered("d-1", "yes"))
    store.apply(DecisionsRead(ROOT, "r", at=9.0, decision_id="d-1"))

    assert folded_of(store).drawn == (A,)


def test_a_decision_it_withdrew_itself_does_not_keep_it_drawn() -> None:
    store = ended_pair()
    store.apply(DecisionRaised(A, decision("d-1", A)))
    store.apply(DecisionWithdrawn(A, "d-1"))
    held = store.snapshot().decisions["d-1"]
    assert held.outcome is not None and held.first_delivered_at is None

    assert folded_of(store).folded == (A, B)


def test_an_ended_sub_agent_holding_a_claimed_task_is_drawn_until_it_lets_go() -> None:
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(A, ROOT, at=1.0))
    store.apply(spawn(B, ROOT, at=1.0))
    store.apply(TaskDeclared(Task(id="t1", title="one"), node_id=ROOT))
    store.apply(TaskDeclared(Task(id="t2", title="two"), node_id=ROOT))
    store.apply(TaskClaimRequested(A, request_id="k1", task_id="t1"))
    store.apply(TaskClaimRequested(B, request_id="k2", task_id="t2"))
    store.apply(done(A))
    store.apply(done(B))
    tasks = store.snapshot().tasks
    assert (tasks["t1"].claimed_by, tasks["t2"].claimed_by) == (A, B)

    assert folded_of(store).drawn == (A, B)

    store.apply(TaskCompleted(A, "t1", at=6.0))
    assert store.snapshot().tasks["t1"].state is TaskState.COMPLETED
    assert folded_of(store).folded == (A,), "a finished task is not held"

    store.apply(TaskReleased(B, "t2"))
    assert store.snapshot().tasks["t2"].state is TaskState.PENDING
    assert folded_of(store).folded == (A, B)


def test_an_ended_sub_agent_with_mail_waiting_is_drawn_until_it_reads_it() -> None:
    store = ended_pair()
    store.apply(ConcernPosted(ROOT, concern("c1", ROOT, A)))
    assert folded_of(store).drawn == (A,)
    assert folded_of(store).folded == (B,), "mail it sent is not mail waiting for it"

    store.apply(InboxRead(A, "r", at=9.0))
    assert store.snapshot().concerns["c1"].state is ConcernState.DELIVERED
    assert folded_of(store).folded == (A, B)


def test_the_node_under_the_cursor_is_drawn() -> None:
    store = ended_pair()

    assert folded_of(store, cursor=B).drawn == (B,)
    assert folded_of(store, cursor=B).folded == (A,)
    assert folded_of(store, cursor=ROOT).folded == (A, B)
    assert folded_of(store, cursor=OTHER).folded == (A, B)


def test_a_parked_approval_on_an_ended_sub_agent_keeps_it_drawn() -> None:
    """
    An approval that lands on a node whose end was declared is filed and the node
    stays ended, so "ended" alone would fold a row the operator has to answer.
    """
    store = ended_pair()
    store.apply(
        ApprovalRequested(
            A,
            PendingApproval(
                id="p1",
                node=A,
                tool_name="Write",
                tool_use_id="tu",
                raw_args={},
                summary="Write x",
                requested_at=6.0,
            ),
        )
    )
    snap = store.snapshot()
    assert snap.nodes[A].state is AgentState.DONE
    assert [o.node for o in snap.needs_you] == [A]

    assert folded_of(store).drawn == (A,)
    assert folded_of(store).folded == (B,)


# -- nesting ---------------------------------------------------------------------


def nested() -> Store:
    """ROOT -> A -> A1, and ROOT -> B; all three sub-agents ended."""
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(A, ROOT, at=1.0))
    store.apply(spawn(A1, A, at=2.0))
    store.apply(spawn(B, ROOT, at=1.0))
    for node in (A1, A, B):
        store.apply(done(node))
    return store


def test_a_whole_ended_subtree_folds_and_is_counted_node_by_node() -> None:
    store = nested()
    assert store.snapshot().order == (ROOT, A, A1, B)

    view = folded_of(store)

    assert view.folded == (A, A1, B)
    assert view.drawn == ()


def test_an_ended_parent_is_drawn_while_anything_beneath_it_is() -> None:
    store = nested()
    store.apply(AgentResumed(A1, at=6.0))

    view = folded_of(store)

    assert view.drawn == (A, A1), "a drawn node keeps the ancestors it hangs from"
    assert view.folded == (B,)


def test_a_drawn_parent_does_not_keep_its_ended_child_drawn() -> None:
    store = nested()
    store.apply(AgentResumed(A, at=6.0))

    view = folded_of(store)

    assert view.drawn == (A,)
    assert view.folded == (A1, B)


def test_the_cursor_on_a_grandchild_keeps_its_parent_drawn() -> None:
    view = folded_of(nested(), cursor=A1)

    assert view.drawn == (A, A1)
    assert view.folded == (B,)


def test_three_levels_deep_the_whole_chain_above_a_live_node_is_drawn() -> None:
    deep: NodeId = ("sess-1", "agent-a1x")
    store = nested()
    store.apply(spawn(deep, A1, at=3.0))

    view = folded_of(store)

    assert view.drawn == (A, A1, deep)
    assert view.folded == (B,)


# -- the setting, the opened fold, other sessions --------------------------------


def test_with_the_setting_off_nothing_is_folded_or_counted() -> None:
    snap = nested().snapshot()

    view = fold(snap, ROOT, None, False)

    assert view.drawn == (A, A1, B)
    assert view.folded == ()
    assert view.count == 0


def test_an_opened_fold_draws_everything_and_still_counts() -> None:
    store = nested()
    store.apply(AgentResumed(B, at=6.0))
    snap = store.snapshot()

    closed = fold(snap, ROOT, None, True)
    opened = fold(snap, ROOT, None, True, opened={ROOT})

    assert closed.drawn == (B,)
    assert opened.drawn == (A, A1, B)
    assert opened.folded == closed.folded == (A, A1)
    assert (closed.opened, opened.opened) == (False, True)


def test_another_roots_opened_fold_opens_nothing_here() -> None:
    snap = nested().snapshot()

    view = fold(snap, ROOT, None, True, opened={OTHER})

    assert view.drawn == () and view.opened is False


def test_a_session_is_folded_on_its_own_facts() -> None:
    """
    A claim, a letter and a decision in one session are not reasons to draw a
    sub-agent of another, and one session's list never holds another's nodes.
    """
    store = ended_pair()
    store.apply(spawn(OTHER))
    store.apply(spawn(OTHER_A, OTHER, at=1.0))
    store.apply(TaskDeclared(Task(id="t1", title="one"), node_id=OTHER))
    store.apply(TaskClaimRequested(OTHER_A, request_id="k", task_id="t1"))
    store.apply(ConcernPosted(OTHER, concern("c1", OTHER, OTHER_A)))
    store.apply(DecisionRaised(OTHER_A, decision("d-1", OTHER_A)))
    store.apply(done(OTHER_A))
    snap = store.snapshot()

    assert fold(snap, ROOT, None, True).folded == (A, B)
    assert fold(snap, OTHER, None, True) == Folded(drawn=(OTHER_A,), folded=(), opened=False)


def test_a_root_the_snapshot_does_not_hold_has_nothing_to_draw() -> None:
    snap = ended_pair().snapshot()

    assert fold(snap, ("nowhere", None), None, True) == Folded((), (), False)
    assert fold(Snapshot.empty(), ROOT, A, True) == Folded((), (), False)


# -- reviving --------------------------------------------------------------------


def test_a_folded_sub_agent_that_is_woken_is_drawn_again() -> None:
    """
    The operator's question, as a sequence through the reducer: a lead that
    revives a sub-agent makes it reappear. The wake is ``AgentResumed`` -- a
    ``StateChanged`` does not move a node out of a declared end.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(A, ROOT, at=1.0))
    assert folded_of(store).drawn == (A,)

    store.apply(done(A))
    assert folded_of(store).folded == (A,)

    store.apply(StateChanged(A, AgentState.THINKING))
    assert folded_of(store).folded == (A,), "a late status update is not a wake"

    store.apply(AgentResumed(A, at=8.0))
    woken = folded_of(store)
    assert woken.drawn == (A,)
    assert woken.folded == ()

    store.apply(done(A, at=9.0))
    assert folded_of(store).folded == (A,)


# -- an earlier run's agents ------------------------------------------------------


def earlier_run(*, claim: bool) -> Store:
    store = Store()
    store.apply(spawn(ROOT, at=0.0))
    store.apply(spawn(A, ROOT, at=1.0))
    if claim:
        store.apply(TaskDeclared(Task(id="t1", title="one"), node_id=ROOT))
        store.apply(TaskClaimRequested(A, request_id="k", task_id="t1"))
    store.apply(done(A))
    store.apply(spawn(ROOT, at=10.0))
    assert store.snapshot().of_earlier_run(A)
    return store


def test_an_earlier_runs_agent_holding_a_claim_is_drawn() -> None:
    store = earlier_run(claim=True)
    assert store.snapshot().tasks["t1"].claimed_by == A

    assert folded_of(store).drawn == (A,)


def test_an_earlier_runs_agent_holding_nothing_folds_like_any_other() -> None:
    assert folded_of(earlier_run(claim=False)).folded == (A,)


# -- purity -----------------------------------------------------------------------


def test_folding_changes_nothing_and_answers_the_same_twice() -> None:
    store = nested()
    store.apply(ConcernPosted(ROOT, concern("c1", ROOT, B)))
    store.apply(DecisionRaised(A1, decision("d-1", A1)))
    snap = store.snapshot()
    before = _facts(snap)
    opened = frozenset({ROOT})

    first = fold(snap, ROOT, A, True, opened)
    second = fold(snap, ROOT, A, True, opened)

    assert first == second
    assert store.snapshot() is snap
    assert _facts(snap) == before
    assert opened == frozenset({ROOT})


def _facts(snap: Snapshot) -> tuple[Any, ...]:
    """Every field of a snapshot the fold reads, copied out of its containers."""
    return (
        snap.seq,
        dict(snap.nodes),
        snap.order,
        snap.needs_you,
        dict(snap.concerns),
        dict(snap.tasks),
        dict(snap.decisions),
    )


# -- generated --------------------------------------------------------------------


def _reason_drawn(snap: Snapshot, node: NodeId, cursor: NodeId | None) -> str | None:
    """
    Which clause of the property keeps ``node`` out of the fold, on its own facts
    alone -- or None when none does. Asked of the snapshot's own readers
    (``inbox_of``, ``decisions_of``) and not through anything the fold computes.
    """
    if not snap.nodes[node].state.is_terminal:
        return "not ended"
    if any(o.node == node for o in snap.needs_you):
        return "owed"
    if any(t.claimed_by == node and t.state is TaskState.CLAIMED for t in snap.tasks.values()):
        return "holds a claim"
    if snap.inbox_of(node):
        return "mail waiting"
    if any(
        isinstance(d.outcome, Answered | Dismissed) and d.first_delivered_at is None
        for d in snap.decisions_of(node)
    ):
        return "unread answer"
    if node == cursor:
        return "under the cursor"
    return None


def _generated(rng: random.Random) -> tuple[Snapshot, list[NodeId]]:
    """A session driven through a random run of the intents the fold's facts come from."""
    store = Store()
    store.apply(spawn(ROOT))
    nodes: list[NodeId] = []
    parents: list[NodeId] = [ROOT]
    for index in range(rng.randint(1, 9)):
        node: NodeId = ("sess-1", f"g{index}")
        store.apply(spawn(node, rng.choice(parents), at=1.0 + index))
        nodes.append(node)
        parents.append(node)

    tasks = [f"t{n}" for n in range(4)]
    for tid in tasks:
        store.apply(TaskDeclared(Task(id=tid, title=tid), node_id=ROOT))

    serial = 0
    for _ in range(rng.randint(5, 60)):
        serial += 1
        node = rng.choice(nodes)
        tid = rng.choice(tasks)
        did = f"d{rng.randint(0, 5)}"
        at = 20.0 + serial
        choices: list[Intent] = [
            done(node, at=at),
            done(node, at=at),
            AgentFinished(node, AgentState.CANCELLED, ended_at=at),
            AgentFinished(node, AgentState.FAILED, ended_at=at, error="boom"),
            FailureAcknowledged(node),
            AgentResumed(node, at=at),
            StateChanged(node, AgentState.AWAITING_INPUT),
            TaskClaimRequested(node, request_id=f"k{serial}", task_id=tid),
            TaskReleased(node, tid),
            TaskCompleted(node, tid, at=at),
            ConcernPosted(ROOT, concern(f"c{serial}", ROOT, node)),
            InboxRead(node, f"r{serial}", at=at),
            DecisionRaised(node, decision(f"n{serial}", node)),
            DecisionAnswered(did, "yes"),
            DecisionDismissed(did),
            DecisionWithdrawn(node, did),
            DecisionsRead(node, f"r{serial}", at=at, decision_id=did),
        ]
        intent = rng.choice(choices)
        if isinstance(intent, DecisionRaised):
            intent = DecisionRaised(node, decision(did, node))
        store.apply(intent)
    return store.snapshot(), nodes


def test_no_generated_fold_breaks_a_clause_and_none_draws_without_a_reason() -> None:
    """
    Both directions, because either alone is satisfied by a rule that does
    nothing: "fold none" never folds wrongly and "fold all" never draws wrongly.
    """
    rng = random.Random(20261013)
    seen_folded = seen_drawn_ended = 0
    reasons: set[str] = set()

    for _ in range(400):
        snap, nodes = _generated(rng)
        cursor = rng.choice([None, None, ROOT, *nodes])
        view = fold(snap, ROOT, cursor, True)

        assert sorted(view.drawn + view.folded, key=snap.order.index) == nodes_in_order(snap)
        assert not set(view.drawn) & set(view.folded)
        assert list(view.drawn) == sorted(view.drawn, key=snap.order.index)
        assert list(view.folded) == sorted(view.folded, key=snap.order.index)

        folded = set(view.folded)
        for node in view.folded:
            assert _reason_drawn(snap, node, cursor) is None, (node, snap.nodes[node])
            beneath = [n for n in snap.order if snap.nodes[n].parent == node]
            assert set(beneath) <= folded, "folded above something that is drawn"
        for node in view.drawn:
            own = _reason_drawn(snap, node, cursor)
            below = any(snap.nodes[n].parent == node for n in view.drawn)
            assert own is not None or below, f"{node} is drawn and nothing says why"
            if own is not None:
                reasons.add(own)
            if snap.nodes[node].state.is_terminal:
                seen_drawn_ended += 1
        seen_folded += len(view.folded)

        opened = fold(snap, ROOT, cursor, True, opened={ROOT})
        assert opened.folded == view.folded
        assert list(opened.drawn) == nodes_in_order(snap)

    # The generator has to reach what the assertions are about, or they pass idle.
    assert seen_folded > 100 and seen_drawn_ended > 100
    assert reasons == {
        "not ended",
        "owed",
        "holds a claim",
        "mail waiting",
        "unread answer",
        "under the cursor",
    }


def nodes_in_order(snap: Snapshot) -> list[NodeId]:
    return [n for n in snap.order if n != ROOT]
