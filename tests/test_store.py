"""
Store: copy-on-write, snapshot atomicity, intent ordering, tree shape.

The invariants under test are the ones the whole UI rests on, so they are tested
by observable behaviour (does an old snapshot still describe the old world?)
rather than by poking at internals.
"""

from __future__ import annotations

import pytest

from pptmstr.intents import (
    AgentFinished,
    AgentRemoved,
    AgentSpawned,
    ApprovalRequested,
    ApprovalResolved,
    CompactionObserved,
    ContextPolled,
    StateChanged,
    SubagentProgress,
    TopicChanged,
    UsageAccrued,
)
from pptmstr.model import (
    AgentState,
    ContextPressure,
    ContextSnapshot,
    NodeId,
    PendingApproval,
    UsageRollup,
)
from pptmstr.store import Store

ROOT: NodeId = ("sess-1", None)
CHILD: NodeId = ("sess-1", "agent-a")
OTHER: NodeId = ("sess-2", None)


def spawn(node: NodeId, parent: NodeId | None = None, *, at: float = 0.0) -> AgentSpawned:
    return AgentSpawned(
        node_id=node,
        parent=parent,
        task="do a thing",
        model="claude-opus-5",
        started_at=at,
    )


def pending(node: NodeId, pid: str = "p1", *, at: float = 1.0) -> PendingApproval:
    return PendingApproval(
        id=pid,
        node=node,
        tool_name="Write",
        tool_use_id="tu-1",
        raw_args={"file_path": "/tmp/x", "content": "hi"},
        summary="Write /tmp/x",
        requested_at=at,
    )


# -- copy-on-write / snapshot atomicity ---------------------------------------


def test_snapshot_is_stable_across_later_mutations() -> None:
    """
    I2/I3: a snapshot taken at frame start must keep describing that instant even
    as the world moves on. This is the single most important property here -- if it
    fails, the UI renders a header that disagrees with the tree below it.
    """
    store = Store()
    store.apply(spawn(ROOT))
    before = store.snapshot()

    store.apply(StateChanged(ROOT, AgentState.THINKING, topic="reading store.py"))
    store.apply(spawn(CHILD, ROOT))
    after = store.snapshot()

    assert before is not after
    assert len(before.nodes) == 1
    assert len(after.nodes) == 2

    rec_before = before.get(ROOT)
    rec_after = after.get(ROOT)
    assert rec_before is not None and rec_after is not None
    assert rec_before.state is AgentState.SPAWNING
    assert rec_after.state is AgentState.THINKING
    assert rec_before.topic == "starting"


def test_snapshot_nodes_are_not_mutable_by_callers() -> None:
    """The UI must not be able to write through the snapshot it was handed (I1)."""
    store = Store()
    store.apply(spawn(ROOT))
    snap = store.snapshot()
    with pytest.raises(TypeError):
        snap.nodes[OTHER] = snap.nodes[ROOT]  # type: ignore[index]


def test_records_are_frozen() -> None:
    store = Store()
    store.apply(spawn(ROOT))
    rec = store.snapshot().get(ROOT)
    assert rec is not None
    with pytest.raises((AttributeError, TypeError)):
        rec.state = AgentState.DONE  # type: ignore[misc]


def test_seq_advances_on_every_applied_intent() -> None:
    store = Store()
    assert store.snapshot().seq == 0
    store.apply(spawn(ROOT))
    assert store.snapshot().seq == 1
    store.apply(TopicChanged(ROOT, "x"))
    assert store.snapshot().seq == 2


def test_intent_for_unknown_node_is_a_no_op() -> None:
    """
    Out-of-order or late intents must not create phantom rows. Sub-agent messages
    can arrive around their spawn, and a half-built record would render as an agent
    that does not exist.
    """
    store = Store()
    store.apply(spawn(ROOT))
    seq = store.snapshot().seq
    store.apply(TopicChanged(("nope", None), "ghost"))
    snap = store.snapshot()
    assert snap.seq == seq
    assert list(snap.nodes) == [ROOT]


def test_apply_all_matches_sequential_apply() -> None:
    """Batch draining must not change semantics, only bookkeeping."""
    intents = [
        spawn(ROOT),
        spawn(CHILD, ROOT),
        StateChanged(ROOT, AgentState.THINKING),
        UsageAccrued(ROOT, UsageRollup(input_tokens=10, output_tokens=5)),
    ]
    one = Store()
    for i in intents:
        one.apply(i)
    batch = Store()
    batch.apply_all(intents)

    a, b = one.snapshot(), batch.snapshot()
    assert a.order == b.order
    assert a.any_active == b.any_active
    assert {k: v.state for k, v in a.nodes.items()} == {k: v.state for k, v in b.nodes.items()}
    assert a.nodes[ROOT].usage == b.nodes[ROOT].usage


# -- ordering and tree shape ---------------------------------------------------


def test_preorder_puts_children_under_their_parent() -> None:
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(OTHER))
    store.apply(spawn(CHILD, ROOT))
    order = store.snapshot().order
    assert order == (ROOT, CHILD, OTHER)


def test_depth_is_derived_from_parent() -> None:
    grand: NodeId = ("sess-1", "agent-b")
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    store.apply(spawn(grand, CHILD))
    snap = store.snapshot()
    assert [snap.nodes[n].depth for n in (ROOT, CHILD, grand)] == [0, 1, 2]


def test_sibling_order_is_spawn_order_and_stable() -> None:
    """
    I6 rests on this: rows must not shuffle, or ImGui's per-row widget state
    (hover, focus, scroll) follows the wrong agent.
    """
    a: NodeId = ("sess-1", "a")
    b: NodeId = ("sess-1", "b")
    c: NodeId = ("sess-1", "c")
    store = Store()
    store.apply(spawn(ROOT))
    for n in (a, b, c):
        store.apply(spawn(n, ROOT))
    first = store.snapshot().order
    store.apply(StateChanged(b, AgentState.DONE))
    assert store.snapshot().order == first


def test_removing_a_node_removes_its_subtree() -> None:
    grand: NodeId = ("sess-1", "agent-b")
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    store.apply(spawn(grand, CHILD))
    store.apply(spawn(OTHER))
    store.apply(AgentRemoved(ROOT))
    snap = store.snapshot()
    assert list(snap.nodes) == [OTHER]
    assert snap.order == (OTHER,)


def test_orphaned_node_still_appears() -> None:
    """
    A sub-agent whose spawn beat its parent's must stay visible. Dropping it would
    also drop any approval it is blocked on, which would wedge that agent with no
    way for the operator to see why.
    """
    store = Store()
    store.apply(spawn(CHILD, ROOT))  # parent never spawned
    snap = store.snapshot()
    assert snap.order == (CHILD,)
    assert snap.nodes[CHILD].depth == 0


# -- idle predicate ------------------------------------------------------------


@pytest.mark.parametrize(
    "state,expected",
    [
        (AgentState.THINKING, True),
        (AgentState.CALLING_TOOL, True),
        (AgentState.RUNNING_TOOL, True),
        (AgentState.SPAWNING, False),
        (AgentState.AWAITING_APPROVAL, False),
        (AgentState.DONE, False),
        (AgentState.FAILED, False),
        (AgentState.CANCELLED, False),
        (AgentState.RATE_LIMITED, False),
    ],
)
def test_any_active_drives_idling(state: AgentState, expected: bool) -> None:
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(StateChanged(ROOT, state))
    assert store.snapshot().any_active is expected


def test_awaiting_approval_lets_the_app_idle() -> None:
    """
    I8, stated as a test: an agent parked on review costs nothing. If this ever
    flips, the app burns CPU for exactly as long as the operator takes to think.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(StateChanged(ROOT, AgentState.THINKING))
    assert store.snapshot().any_active is True
    store.apply(ApprovalRequested(ROOT, pending(ROOT)))
    assert store.snapshot().any_active is False


# -- approvals -----------------------------------------------------------------


def test_approval_request_parks_the_agent() -> None:
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(ApprovalRequested(ROOT, pending(ROOT)))
    rec = store.snapshot().nodes[ROOT]
    assert rec.state is AgentState.AWAITING_APPROVAL
    assert [p.id for p in rec.pending] == ["p1"]


def test_resolution_clears_pending() -> None:
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(ApprovalRequested(ROOT, pending(ROOT)))
    store.apply(ApprovalResolved(ROOT, "p1", approved=True))
    rec = store.snapshot().nodes[ROOT]
    assert rec.pending == ()
    assert rec.state is AgentState.RUNNING_TOOL
    assert store.snapshot().review_queue == ()


def test_stale_resolution_is_ignored() -> None:
    """
    A second click, or a decision for an approval that has been superseded, must
    not clear the approval that is actually parked -- that would release a tool call
    the operator never looked at.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1")))
    store.apply(ApprovalResolved(ROOT, "p-stale", approved=True))
    rec = store.snapshot().nodes[ROOT]
    assert [p.id for p in rec.pending] == ["p1"]
    assert rec.state is AgentState.AWAITING_APPROVAL


def test_review_queue_spans_agents_oldest_first() -> None:
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(OTHER))
    store.apply(ApprovalRequested(OTHER, pending(OTHER, "late", at=99.0)))
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "early", at=1.0)))
    assert [p.id for p in store.snapshot().review_queue] == ["early", "late"]


def test_finishing_clears_a_parked_approval() -> None:
    """A cancelled or failed agent must not leave an orphan row in the queue."""
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(ApprovalRequested(ROOT, pending(ROOT)))
    store.apply(AgentFinished(ROOT, AgentState.CANCELLED, ended_at=5.0))
    snap = store.snapshot()
    assert snap.nodes[ROOT].pending == ()
    assert snap.review_queue == ()


# -- context as a health signal ------------------------------------------------


def ctx(used: int, *, threshold: int | None = 100_000, compactions: int = 0) -> ContextSnapshot:
    return ContextSnapshot(
        used_tokens=used,
        max_tokens=180_000,
        raw_max_tokens=200_000,
        percentage=100.0 * used / 180_000,
        auto_compact_enabled=threshold is not None,
        auto_compact_threshold=threshold,
        model="claude-opus-5",
        polled_at=1.0,
        compactions=compactions,
    )


def test_headroom_is_measured_against_the_compaction_threshold() -> None:
    assert ctx(70_000).tokens_until_compaction == 30_000


def test_headroom_is_none_without_autocompact() -> None:
    """
    Must not silently fall back to the window size: that would answer a different
    question while looking like the same one.
    """
    assert ctx(70_000, threshold=None).tokens_until_compaction is None


def test_pressure_warns_before_compaction() -> None:
    assert ctx(50_000).pressure() is ContextPressure.NOMINAL
    assert ctx(95_000).pressure() is ContextPressure.NEARING_COMPACTION


def test_pressure_is_sticky_once_compacted() -> None:
    """
    A compacted session has already lost reasoning; an emptier window afterwards is
    the symptom of the damage, not evidence of health.
    """
    assert ctx(5_000, compactions=1).pressure() is ContextPressure.COMPACTED


def test_polling_preserves_compaction_history() -> None:
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(ContextPolled(ROOT, ctx(90_000)))
    store.apply(CompactionObserved(ROOT, at=12.0, trigger="auto"))
    # Compaction empties the window; the next poll reports the low number.
    store.apply(ContextPolled(ROOT, ctx(4_000)))

    got = store.snapshot().nodes[ROOT].context
    assert got is not None
    assert got.used_tokens == 4_000
    assert got.compactions == 1
    assert got.last_compaction_at == 12.0
    assert got.pressure() is ContextPressure.COMPACTED


def test_compaction_before_any_poll_is_dropped_not_faked() -> None:
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(CompactionObserved(ROOT, at=3.0, trigger="auto"))
    assert store.snapshot().nodes[ROOT].context is None


# -- usage ---------------------------------------------------------------------


def test_usage_accrues() -> None:
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(UsageAccrued(ROOT, UsageRollup(input_tokens=10, total_cost_usd=0.01)))
    store.apply(UsageAccrued(ROOT, UsageRollup(input_tokens=5, total_cost_usd=0.02)))
    usage = store.snapshot().nodes[ROOT].usage
    assert usage.input_tokens == 15
    assert usage.total_cost_usd == pytest.approx(0.03)


# -- a parked node stays parked ------------------------------------------------


def test_late_state_change_cannot_unpark_a_node() -> None:
    """
    Regression, found by dogfooding. The CLI dispatches PreToolUse *before* it
    delivers the AssistantMessage carrying the ToolUseBlock, so the gate parks the
    node and a StateChanged for the very same tool call lands immediately after.
    Letting it through overwrote AWAITING_APPROVAL with CALLING_TOOL while the
    agent was still blocked: the row read "thinking", the state counted as active
    so the app never idled, and it looked like a hang instead of a review request.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(ApprovalRequested(ROOT, pending(ROOT)))
    store.apply(StateChanged(ROOT, AgentState.CALLING_TOOL, topic="bash pwd"))

    rec = store.snapshot().nodes[ROOT]
    assert rec.state is AgentState.AWAITING_APPROVAL
    assert rec.pending
    # The topic still updates: naming the call under review is useful, not a lie.
    assert rec.topic == "bash pwd"


def test_a_parked_node_keeps_the_app_idle_despite_a_late_state_change() -> None:
    """
    The observable half of the same bug. CALLING_TOOL is an active state, so the
    clobber also pinned the render loop at full speed for as long as the operator
    took to answer -- I8 defeated by an ordering accident.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(ApprovalRequested(ROOT, pending(ROOT)))
    store.apply(StateChanged(ROOT, AgentState.THINKING))
    assert store.snapshot().any_active is False


def test_unparking_restores_normal_state_transitions() -> None:
    """The guard must not outlive the approval it protects."""
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1")))
    store.apply(ApprovalResolved(ROOT, "p1", approved=True))
    store.apply(StateChanged(ROOT, AgentState.THINKING))
    assert store.snapshot().nodes[ROOT].state is AgentState.THINKING


def test_subagent_progress_cannot_unpark_either() -> None:
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    store.apply(ApprovalRequested(CHILD, pending(CHILD)))
    store.apply(SubagentProgress(CHILD, "Reading log.py"))

    rec = store.snapshot().nodes[CHILD]
    assert rec.state is AgentState.AWAITING_APPROVAL
    assert rec.topic == "Reading log.py"


def test_finishing_a_parked_node_still_works() -> None:
    """Cancellation and failure must outrank the parked guard, or a dead agent hangs."""
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(ApprovalRequested(ROOT, pending(ROOT)))
    store.apply(AgentFinished(ROOT, AgentState.CANCELLED, ended_at=1.0))
    rec = store.snapshot().nodes[ROOT]
    assert rec.state is AgentState.CANCELLED
    assert rec.pending == ()


def test_awaiting_input_is_idle_not_terminal() -> None:
    """
    A conversation paused on the operator must cost nothing (the I8 argument),
    and must not be mistaken for a finished session -- which is the confusion
    that made an agent asking a question look like one that had finished.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(StateChanged(ROOT, AgentState.AWAITING_INPUT))
    assert store.snapshot().any_active is False
    assert AgentState.AWAITING_INPUT.is_terminal is False
    assert AgentState.DONE.is_terminal is True


# -- several approvals at once from one node -----------------------------------


def test_a_node_can_hold_several_pending_approvals() -> None:
    """
    The defect the tuple exists to fix, and the reason the model was wrong. An
    assistant turn can contain several tool calls; the CLI dispatches PreToolUse
    for all of them concurrently and the gate parks a future for each. Three at
    once was measured against a live agent. A single slot kept the last and
    silently discarded the rest, leaving their agents blocked on futures nobody
    could reach.
    """
    store = Store()
    store.apply(spawn(ROOT))
    for pid in ("p1", "p2", "p3"):
        store.apply(ApprovalRequested(ROOT, pending(ROOT, pid, at=float(pid[1]))))

    snap = store.snapshot()
    assert [p.id for p in snap.nodes[ROOT].pending] == ["p1", "p2", "p3"]
    assert [p.id for p in snap.review_queue] == ["p1", "p2", "p3"]


def test_every_parked_approval_is_visible_in_the_queue() -> None:
    """
    The invariant the watchdog checks, at the store level: what the gate parked and
    what the operator can answer must be the same set.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(OTHER))
    parked = {"a1", "a2", "b1"}
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "a1", at=1.0)))
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "a2", at=2.0)))
    store.apply(ApprovalRequested(OTHER, pending(OTHER, "b1", at=3.0)))
    assert {p.id for p in store.snapshot().review_queue} == parked


def test_resolving_one_leaves_the_others_parked() -> None:
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1", at=1.0)))
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p2", at=2.0)))
    store.apply(ApprovalResolved(ROOT, "p1", approved=True))

    rec = store.snapshot().nodes[ROOT]
    assert [p.id for p in rec.pending] == ["p2"]
    # Still parked: moving to RUNNING_TOOL here would hide the outstanding one and
    # recreate the same bug a layer up.
    assert rec.state is AgentState.AWAITING_APPROVAL
    assert store.snapshot().any_active is False


def test_state_advances_only_when_the_last_one_is_resolved() -> None:
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1", at=1.0)))
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p2", at=2.0)))
    store.apply(ApprovalResolved(ROOT, "p1", approved=True))
    store.apply(ApprovalResolved(ROOT, "p2", approved=True))

    rec = store.snapshot().nodes[ROOT]
    assert rec.pending == ()
    assert rec.state is AgentState.RUNNING_TOOL


def test_the_same_approval_twice_is_not_duplicated() -> None:
    """A re-emitted intent must not create a second row for one parked future."""
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1")))
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1")))
    assert len(store.snapshot().review_queue) == 1


def test_finishing_clears_every_pending_approval() -> None:
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1", at=1.0)))
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p2", at=2.0)))
    store.apply(AgentFinished(ROOT, AgentState.CANCELLED, ended_at=9.0))
    assert store.snapshot().nodes[ROOT].pending == ()
    assert store.snapshot().review_queue == ()
