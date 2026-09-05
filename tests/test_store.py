"""
Store: copy-on-write, snapshot atomicity, intent ordering, tree shape.

The invariants under test are the ones the whole UI rests on, so they are tested
by observable behaviour (does an old snapshot still describe the old world?)
rather than by poking at internals.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib

import pytest

from pptmstr.intents import (
    AgentFinished,
    AgentRemoved,
    AgentResumed,
    AgentSpawned,
    ApprovalRequested,
    ApprovalResolved,
    CompactionObserved,
    ContextPolled,
    FailureAcknowledged,
    StateChanged,
    SubagentDelivered,
    SubagentProgress,
    TaskClaimRequested,
    TaskCompleted,
    TaskDeclared,
    TopicChanged,
    UsageAccrued,
)
from pptmstr.model import (
    AWAITING_TOPIC,
    AgentState,
    ApprovalNeeded,
    ApprovedWrites,
    ContextPressure,
    ContextSnapshot,
    NodeId,
    PendingApproval,
    SessionFailed,
    Task,
    UsageRollup,
    count_diff_lines,
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
    assert store.snapshot().approvals == ()


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
    assert [p.id for p in store.snapshot().approvals] == ["early", "late"]


def test_finishing_clears_a_parked_approval() -> None:
    """A cancelled or failed agent must not leave an orphan row in the queue."""
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(ApprovalRequested(ROOT, pending(ROOT)))
    store.apply(AgentFinished(ROOT, AgentState.CANCELLED, ended_at=5.0))
    snap = store.snapshot()
    assert snap.nodes[ROOT].pending == ()
    assert snap.approvals == ()


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
    assert [p.id for p in snap.approvals] == ["p1", "p2", "p3"]


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
    assert {p.id for p in store.snapshot().approvals} == parked


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
    assert len(store.snapshot().approvals) == 1


def test_finishing_clears_every_pending_approval() -> None:
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1", at=1.0)))
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p2", at=2.0)))
    store.apply(AgentFinished(ROOT, AgentState.CANCELLED, ended_at=9.0))
    assert store.snapshot().nodes[ROOT].pending == ()
    assert store.snapshot().approvals == ()


# -- the team shape a session was launched under ----------------------------------


def test_a_session_records_the_template_it_was_launched_under() -> None:
    """
    A launch-time choice that is not stored is gone: `template` lives on
    driver.AgentSession, and the UI reads one Snapshot and nothing else.
    """
    store = Store()
    store.apply(dataclasses.replace(spawn(ROOT), template="feature"))

    assert store.snapshot().nodes[ROOT].template == "feature"


def test_a_sub_agent_has_no_template_of_its_own() -> None:
    """
    Unlike cwd, this is **not** inherited from the parent. A sub-agent is spawned
    by the CLI and is not the thing a template describes; inheriting it would make
    every sub-agent answer "yes" to "are you a team", which is the question the
    field exists to answer.
    """
    store = Store()
    store.apply(dataclasses.replace(spawn(ROOT), template="feature"))
    store.apply(spawn(CHILD, ROOT))

    assert store.snapshot().nodes[CHILD].template is None


def test_a_session_launched_without_a_template_records_none() -> None:
    store = Store()
    store.apply(spawn(ROOT))

    assert store.snapshot().nodes[ROOT].template is None


# -- a sub-agent's deliverable ---------------------------------------------------


def test_a_delivered_answer_is_kept_whole() -> None:
    """
    The store is where the only copy lives. A sub-agent emits no ResultMessage for
    its own node, so nothing later can reconstruct an answer clipped on the way in.
    """
    answer = "## Findings\n\n" + "\n".join(f"- line {i}" for i in range(200))
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    store.apply(SubagentDelivered(CHILD, answer))

    rec = store.snapshot().get(CHILD)
    assert rec is not None
    assert rec.deliverable == answer


def test_delivering_does_not_move_the_node() -> None:
    """Two intents arrive from one stop hook. If this one also claimed the state,
    which of the two won would depend on their order in the queue."""
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    store.apply(StateChanged(CHILD, AgentState.RUNNING_TOOL, topic="reading"))
    store.apply(SubagentDelivered(CHILD, "done"))

    rec = store.snapshot().get(CHILD)
    assert rec is not None
    assert rec.state is AgentState.RUNNING_TOOL
    assert rec.topic == "reading"


def test_a_second_answer_replaces_the_first() -> None:
    """A sub-agent woken by a sibling answers again, and the newer answer is the one
    the operator is being asked to read."""
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    store.apply(SubagentDelivered(CHILD, "first pass"))
    store.apply(SubagentDelivered(CHILD, "second pass"))

    rec = store.snapshot().get(CHILD)
    assert rec is not None
    assert rec.deliverable == "second pass"


def test_an_answer_for_an_unknown_node_is_a_no_op() -> None:
    store = Store()
    store.apply(spawn(ROOT))
    before = store.snapshot()
    store.apply(SubagentDelivered(CHILD, "from nowhere"))

    assert store.snapshot().nodes == before.nodes


def test_a_node_that_delivered_nothing_has_no_deliverable() -> None:
    """The absence selects the narration over the whole render, so it has to stay
    distinguishable from an empty string."""
    store = Store()
    store.apply(spawn(ROOT))
    rec = store.snapshot().get(ROOT)
    assert rec is not None
    assert rec.deliverable is None


# -- recovering from a terminal state --------------------------------------------


def _failed_then_recovered(store: Store) -> None:
    """A turn that errored, then a later turn that did not."""
    store.apply(spawn(ROOT))
    store.apply(AgentFinished(ROOT, AgentState.FAILED, ended_at=5.0, error="HTTP 529: overloaded"))
    store.apply(StateChanged(ROOT, AgentState.AWAITING_INPUT, topic="waiting for you"))


def test_a_recovered_session_has_no_end_time() -> None:
    """
    ended_at is what stops the elapsed clock and dims it, in the rail and in HEALTH.
    A live session asking for a reply must not also read as finished.
    """
    store = Store()
    _failed_then_recovered(store)

    rec = store.snapshot().get(ROOT)
    assert rec is not None
    assert rec.state is AgentState.AWAITING_INPUT
    assert rec.ended_at is None


def test_a_recovered_session_keeps_what_went_wrong() -> None:
    """
    Deliberate, unlike ended_at: the error text is the record of the failure, and
    nothing reads it except an obligation gated on FAILED, so a stale value is
    invisible while clearing it would destroy the only structured copy.
    """
    store = Store()
    _failed_then_recovered(store)

    rec = store.snapshot().get(ROOT)
    assert rec is not None
    assert rec.error == "HTTP 529: overloaded"
    assert store.snapshot().needs_you and not any(
        isinstance(o, SessionFailed) for o in store.snapshot().needs_you
    )


def test_a_second_failure_is_asked_about_again() -> None:
    """
    acknowledged only means anything while FAILED. Carrying it through a recovery
    would make the *next* failure produce no obligation at all -- the silent loss
    of a failure signal, one recovery later.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(AgentFinished(ROOT, AgentState.FAILED, ended_at=5.0, error="HTTP 529"))
    store.apply(FailureAcknowledged(ROOT))
    store.apply(StateChanged(ROOT, AgentState.AWAITING_INPUT, topic="waiting for you"))
    store.apply(AgentFinished(ROOT, AgentState.FAILED, ended_at=9.0, error="HTTP 500: again"))

    owed = store.snapshot().needs_you
    assert [type(o) for o in owed] == [SessionFailed]


def test_an_ordinary_state_change_leaves_a_live_session_alone() -> None:
    """The clear is scoped to leaving a terminal state; a running node has nothing
    to recover from and must not have its fields rewritten on every message."""
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(StateChanged(ROOT, AgentState.THINKING, topic="reading store.py"))
    before = store.snapshot().get(ROOT)

    store.apply(StateChanged(ROOT, AgentState.CALLING_TOOL, topic="read store.py"))
    after = store.snapshot().get(ROOT)

    assert before is not None and after is not None
    assert (before.ended_at, before.acknowledged) == (after.ended_at, after.acknowledged)


# -- a declared end is final ------------------------------------------------------
#
# The other half of the boundary tested just above. FAILED is revivable because a
# session that errored and then answered is an ordinary session again. DONE and
# CANCELLED are not: they say somebody ended this agent, and the only intents that
# may move a record off them are AgentFinished and AgentResumed.


def test_a_late_message_cannot_revive_a_finished_subagent() -> None:
    """
    Regression. A sub-agent's final AssistantMessage is on the message stream
    *before* the SubagentStop control frame, but the frame is dispatched
    immediately while the message waits in the SDK's buffer -- so the store sees
    AgentFinished first and the StateChanged the message produces second. The
    answer carries no ToolUseBlock, so it translates to a bare THINKING.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    store.apply(AgentFinished(CHILD, AgentState.DONE, ended_at=7.0))
    store.apply(StateChanged(CHILD, AgentState.THINKING))

    rec = store.snapshot().get(CHILD)
    assert rec is not None
    assert rec.state is AgentState.DONE
    assert rec.ended_at == 7.0


def test_a_late_message_does_not_restart_a_finished_agents_clocks() -> None:
    """
    The observable half. THINKING is active, so the row throbbed and the app never
    idled; ended_at going back to None restarted the elapsed count on a session
    that had already stopped.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(AgentFinished(ROOT, AgentState.DONE, ended_at=7.0))
    store.apply(StateChanged(ROOT, AgentState.THINKING, topic="reading store.py"))

    assert store.snapshot().any_active is False


def test_a_late_message_cannot_relabel_what_a_finished_agent_was_doing() -> None:
    """
    Unlike the parked guard, which lets the topic through because naming the call
    under review is useful, nothing arriving after the end describes the present.
    The topic a finished sub-agent carries is the first line of its answer.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    store.apply(SubagentProgress(CHILD, "the rail loses its throbber"))
    store.apply(AgentFinished(CHILD, AgentState.DONE, ended_at=7.0))
    store.apply(StateChanged(CHILD, AgentState.CALLING_TOOL, topic="grep -rn latch"))

    rec = store.snapshot().get(CHILD)
    assert rec is not None
    assert rec.topic == "the rail loses its throbber"


def test_a_late_progress_report_cannot_relabel_a_finished_subagent() -> None:
    """
    The same hole, one arm along: `task_progress` arrives as a SystemMessage on the
    same buffered stream, so it inverts against the stop hook the same way. This arm
    already kept the terminal state; the topic was still overwritten, replacing the
    answer's first line with whatever the sub-agent was doing before it answered.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    store.apply(SubagentProgress(CHILD, "the rail loses its throbber"))
    store.apply(AgentFinished(CHILD, AgentState.DONE, ended_at=7.0))
    store.apply(SubagentProgress(CHILD, "Reading store.py"))

    rec = store.snapshot().get(CHILD)
    assert rec is not None
    assert rec.state is AgentState.DONE
    assert rec.topic == "the rail loses its throbber"


def test_a_cancelled_session_is_final_too() -> None:
    """CANCELLED says somebody ended it, which is DONE's statement, not FAILED's."""
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(AgentFinished(ROOT, AgentState.CANCELLED, ended_at=3.0))
    store.apply(StateChanged(ROOT, AgentState.THINKING))

    rec = store.snapshot().get(ROOT)
    assert rec is not None
    assert rec.state is AgentState.CANCELLED
    assert rec.ended_at == 3.0


def test_a_woken_subagent_still_comes_back() -> None:
    """
    The reason DONE can be latched at all. A sibling's SendMessage restarts a
    finished sub-agent under its original id, and the CLI reports it as a second
    SubagentStart -- which the driver turns into AgentResumed, not a StateChanged.
    Latching DONE closes the accidental revival path without closing the real one.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    store.apply(AgentFinished(CHILD, AgentState.DONE, ended_at=7.0))
    store.apply(AgentResumed(CHILD, at=9.0, topic="answering again"))

    rec = store.snapshot().get(CHILD)
    assert rec is not None
    assert rec.state is AgentState.THINKING
    assert rec.ended_at is None
    assert store.snapshot().any_active is True


def test_a_failed_session_is_still_revivable() -> None:
    """
    The recorded decision this fix must not break, asserted from the other side:
    the latch is scoped to the ends that were declared, not to terminality.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(AgentFinished(ROOT, AgentState.FAILED, ended_at=5.0, error="HTTP 529"))
    store.apply(FailureAcknowledged(ROOT))
    store.apply(StateChanged(ROOT, AgentState.THINKING, topic="trying again"))

    rec = store.snapshot().get(ROOT)
    assert rec is not None
    assert rec.state is AgentState.THINKING
    assert rec.ended_at is None
    assert rec.acknowledged is False


def test_a_finished_agent_keeps_a_late_approval_without_coming_back() -> None:
    """
    Pins the store's behaviour for this combination, not an ordering the driver is
    known to produce. The gate emits ``ApprovalRequested`` synchronously on the
    PreToolUse hook path, before it parks, and every route to DONE requires the agent
    to have stopped or its stream to have closed -- so the driver has no route that
    puts a request after the end (see the note on ``_needs_you``). The arm is kept
    because the store applies whatever it is handed: what it must not do is record the
    approval and *also* claim the agent is waiting on the operator.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    store.apply(AgentFinished(CHILD, AgentState.DONE, ended_at=7.0))
    store.apply(ApprovalRequested(CHILD, pending(CHILD, "late")))

    snap = store.snapshot()
    rec = snap.get(CHILD)
    assert rec is not None
    assert rec.state is AgentState.DONE
    assert rec.ended_at == 7.0
    assert [p.id for p in snap.approvals] == ["late"]


def test_a_finished_node_holding_an_approval_owes_only_that_approval() -> None:
    """
    The second of the two constructions ``_needs_you`` rests on.

    A live node carrying an approval is held in AWAITING_APPROVAL by the pending
    guard, which is what keeps the three obligation kinds apart. A node whose end was
    declared is not held there -- it stays DONE -- so the exclusivity has to come from
    somewhere else, and it comes from DONE being a state neither QuestionPending nor
    SessionFailed reads. Pinning it here because the alternative is an invariant that
    holds only as long as nobody adds a fourth kind keyed on a terminal state.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    store.apply(AgentFinished(CHILD, AgentState.DONE, ended_at=7.0))
    store.apply(ApprovalRequested(CHILD, pending(CHILD, "late")))
    # Nothing may relabel it into a state another kind would also claim.
    store.apply(StateChanged(CHILD, AgentState.AWAITING_INPUT, topic="anything"))

    owed = [o for o in store.snapshot().needs_you if o.node == CHILD]
    assert [type(o) for o in owed] == [ApprovalNeeded]


def test_resolving_a_late_approval_cannot_revive_a_finished_agent() -> None:
    """
    Reachable two ways, both after the end: the CLI's per-hook timeout cancels the
    gate, and Bridge.stop rejects every parked approval at shutdown. Both resolve
    unapproved, which is the branch that sets THINKING.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    store.apply(AgentFinished(CHILD, AgentState.DONE, ended_at=7.0))
    store.apply(ApprovalRequested(CHILD, pending(CHILD, "late")))
    store.apply(ApprovalResolved(CHILD, "late", approved=False, reason="shutting down"))

    snap = store.snapshot()
    rec = snap.get(CHILD)
    assert rec is not None
    assert rec.state is AgentState.DONE
    assert rec.ended_at == 7.0
    assert rec.pending == ()
    assert snap.any_active is False


def test_an_approval_still_parks_a_live_node() -> None:
    """The latch must not reach a node that has not ended."""
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1")))
    assert store.snapshot().nodes[ROOT].state is AgentState.AWAITING_APPROVAL

    store.apply(ApprovalResolved(ROOT, "p1", approved=False))
    assert store.snapshot().nodes[ROOT].state is AgentState.THINKING


# -- the clock every obligation's wait is measured against ---------------------


def test_an_approval_and_a_question_are_aged_and_sorted_on_one_clock() -> None:
    """
    ``Obligation.since`` is a ``time.monotonic()`` reading whichever kind carries it.

    The three kinds source it from three different fields -- an approval from
    ``PendingApproval.requested_at``, a question from ``AgentRecord.state_since``, a
    failure from ``ended_at`` -- and they are subtracted from one frame clock and
    sorted into one list. Fixing the pair of instants here rather than sampling a
    clock is what makes the sort assertion mean age rather than insertion order.
    """
    frame = 4_000.0
    parked_at = frame - 21_600.0  # six hours before the frame being rendered
    asked_at = frame - 60.0

    store = Store()
    # OTHER is spawned first, so it leads the tree order the walk follows. The sort
    # assertion below is the only thing that can put the older obligation on top, and
    # dropping the sort leaves this list in the opposite order.
    store.apply(spawn(OTHER), now=parked_at)
    store.apply(spawn(ROOT), now=parked_at)
    store.apply(StateChanged(OTHER, AgentState.AWAITING_INPUT), now=asked_at)
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "parked", at=parked_at)), now=asked_at)

    owed = store.snapshot().needs_you
    ages = {o.node: frame - o.since for o in owed}
    assert ages[ROOT] == pytest.approx(21_600.0)
    assert ages[OTHER] == pytest.approx(60.0)
    # Oldest first, so the six-hour park outranks the one-minute question.
    assert [o.node for o in owed] == [ROOT, OTHER]


def test_every_parked_call_is_stamped_from_the_monotonic_clock() -> None:
    """
    Read over the source because no runtime seam can enforce it.

    ``requested_at`` is supplied by whoever constructs a ``PendingApproval``, and the
    store applies the record it is handed rather than restamping it -- restamping
    would tie an approval's age to when the UI thread got round to draining, which
    collapses to zero for every call queued behind a stalled frame loop, which is the
    one case the age is being read for. So the clock has to be right where it is
    taken, and this is the only place that can say so. ``time.time()`` here is not a
    loud failure: it renders as "0s" and sorts last.
    """
    root = pathlib.Path(__file__).resolve().parent.parent / "pptmstr"
    wrong: list[str] = []
    sites = 0
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call):
                continue
            if not (isinstance(call.func, ast.Name) and call.func.id == "PendingApproval"):
                continue
            stamp = next((k.value for k in call.keywords if k.arg == "requested_at"), None)
            if stamp is None:
                continue
            sites += 1
            if ast.unparse(stamp) != "time.monotonic()":
                wrong.append(f"{path.name}:{call.lineno} requested_at={ast.unparse(stamp)}")

    assert sites, "no PendingApproval construction found -- this pin has gone blind"
    assert not wrong, "requested_at must be time.monotonic(): " + "; ".join(wrong)


# -- a lead waiting on its workers -----------------------------------------------


def test_supervising_does_not_hold_the_render_loop_at_full_speed() -> None:
    """
    A supervising lead is doing nothing itself; its workers are.

    Those workers are separate nodes carrying their own active states, so a fan-out
    with anything running in it already holds the loop at full speed through them --
    counting the lead as well would count the same work twice. And a fan-out where
    every worker is itself parked at the gate is a tree that should idle, which is
    I8 as a CPU number rather than a claim.
    """
    assert not AgentState.SUPERVISING.is_active
    assert not AgentState.SUPERVISING.is_terminal


def test_a_supervising_lead_does_not_make_the_tree_look_busy() -> None:
    """
    ``any_active`` drives idling for the whole application, so the property above
    has to hold through the projection and not only on the enum.
    """
    store = Store()
    store.apply(spawn(ROOT), now=1.0)
    store.apply(StateChanged(ROOT, AgentState.SUPERVISING), now=2.0)

    assert store.snapshot().any_active is False


def test_a_supervising_lead_is_kept_busy_by_a_worker_that_is_working() -> None:
    """
    The other half: the loop must stay hot while the fan-out is actually running,
    and it does so through the workers rather than through the lead.
    """
    store = Store()
    store.apply(spawn(ROOT), now=1.0)
    store.apply(spawn(CHILD, ROOT), now=1.0)
    store.apply(StateChanged(ROOT, AgentState.SUPERVISING), now=2.0)
    store.apply(StateChanged(CHILD, AgentState.THINKING), now=2.0)

    assert store.snapshot().any_active is True


def test_a_supervising_lead_can_be_moved_off_the_state_like_any_other() -> None:
    """
    SUPERVISING is not terminal and nothing in the store treats it specially, so the
    turn-over state that follows the wait has to land. A state that stuck would leave
    a finished fan-out reading as one still in progress -- with a composer promising
    an answer that will never come.
    """
    store = Store()
    store.apply(spawn(ROOT), now=1.0)
    store.apply(StateChanged(ROOT, AgentState.SUPERVISING), now=2.0)
    store.apply(StateChanged(ROOT, AgentState.AWAITING_INPUT, topic=AWAITING_TOPIC), now=3.0)

    rec = store.snapshot().nodes[ROOT]
    assert rec.state is AgentState.AWAITING_INPUT
    assert rec.state_since == 3.0


# -- what the resolved approval leaves behind ---------------------------------

# One file, one hunk, two lines in and one out. The ``---``/``+++`` headers are the
# hazard the counter has to survive: they start with the content markers, so a
# counter that tests for those first reads this as three added and two removed.
ONE_FILE_DIFF = """--- a/pptmstr/store.py
+++ b/pptmstr/store.py
@@ -1,3 +1,4 @@
 unchanged
-gone
+new
+also new
"""


def writing(
    node: NodeId,
    pid: str = "p1",
    *,
    diff: str | None = ONE_FILE_DIFF,
    tool: str = "Write",
    args: dict[str, object] | None = None,
) -> PendingApproval:
    return PendingApproval(
        id=pid,
        node=node,
        tool_name=tool,
        tool_use_id="tu-1",
        raw_args={"file_path": "pptmstr/store.py", "content": "hi"} if args is None else args,
        summary="Write pptmstr/store.py",
        requested_at=1.0,
        diff=diff,
    )


def board(
    store: Store,
    *,
    claimer: NodeId | None = CHILD,
    task_id: str = "t1",
    touches: tuple[str, ...] = (),
) -> None:
    """
    A declared task, claimed by ``claimer`` unless that is None.
    """
    store.apply(TaskDeclared(Task(id=task_id, title="do a thing", touches=touches), node_id=ROOT))
    if claimer is not None:
        store.apply(TaskClaimRequested(claimer, request_id=f"k-{task_id}", task_id=task_id))


def test_count_diff_lines_does_not_read_the_headers_as_changed_lines() -> None:
    assert count_diff_lines(ONE_FILE_DIFF) == (2, 1)


def test_count_diff_lines_of_nothing_is_zero_rather_than_an_error() -> None:
    """
    Bash and the network tools park with ``diff=None``, and that is the majority of
    what an agent does. Raising here would make the reducer's most ordinary case the
    one that throws.
    """
    assert count_diff_lines(None) == (0, 0)
    assert count_diff_lines("") == (0, 0)


def test_a_deleted_horizontal_rule_is_a_removal_and_not_a_header() -> None:
    """
    Why the counter tracks hunk budgets instead of classifying by leading character.
    ``difflib`` prefixes a removed line with ``-``, so deleting the ``---`` rule that
    every planning record in this repository uses emits the physical line ``----``.
    A ``startswith("---")`` test reads that as a file header and loses the removal.
    """
    rule = "--- a/notes/x.md\n+++ b/notes/x.md\n@@ -1,3 +1,2 @@\n heading\n----\n body\n"
    assert count_diff_lines(rule) == (0, 1)


def test_an_approved_write_leaves_its_path_and_counts_on_the_task_its_writer_holds() -> None:
    """
    The whole point: ``PendingApproval.diff`` is dropped from ``pending`` in this
    arm, and after the write lands there is no snapshot it can be recomputed from.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    board(store)
    store.apply(ApprovalRequested(CHILD, writing(CHILD)))
    store.apply(ApprovalResolved(CHILD, "p1", approved=True))

    assert store.snapshot().tasks["t1"].writes == ApprovedWrites(
        paths=("pptmstr/store.py",), lines_added=2, lines_removed=1
    )


def test_one_file_written_three_times_is_one_path_and_three_writes_of_lines() -> None:
    """
    Paths merge as a set and lines sum. Counting an integer per approval instead
    would report this as three files, and the operator-facing line would read
    "wrote 3 files" for one file touched three times.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    board(store)
    for pid in ("p1", "p2", "p3"):
        store.apply(ApprovalRequested(CHILD, writing(CHILD, pid)))
        store.apply(ApprovalResolved(CHILD, pid, approved=True))

    assert store.snapshot().tasks["t1"].writes == ApprovedWrites(
        paths=("pptmstr/store.py",), lines_added=6, lines_removed=3
    )


def test_a_writer_holding_no_task_accumulates_nowhere_and_raises_nothing() -> None:
    """
    A lead editing a file outside any claimed task is ordinary, not an error. There
    is no task to hang the measurement on and none is invented.
    """
    store = Store()
    store.apply(spawn(ROOT))
    board(store, claimer=None)
    store.apply(ApprovalRequested(ROOT, writing(ROOT)))
    store.apply(ApprovalResolved(ROOT, "p1", approved=True))

    snap = store.snapshot()
    assert snap.tasks["t1"].writes == ApprovedWrites()
    assert snap.unattributed_writes == 0
    assert snap.nodes[ROOT].pending == ()


def test_a_write_by_one_worker_does_not_land_on_another_workers_task() -> None:
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    board(store, claimer=CHILD, task_id="mine")
    board(store, claimer=None, task_id="theirs")
    store.apply(ApprovalRequested(CHILD, writing(CHILD)))
    store.apply(ApprovalResolved(CHILD, "p1", approved=True))

    assert store.snapshot().tasks["theirs"].writes == ApprovedWrites()


def test_a_node_holding_two_open_tasks_attributes_its_write_to_neither() -> None:
    """
    Nothing in the snapshot says which of two claimed tasks a write was for, so
    neither gets it and the ambiguity is counted instead. Guessing by declaration
    order would put a made-up attribution into the one series this measures.

    A non-zero count here is a reading about the board rather than the writer: "one
    task at a time" is prose in ``templates.py`` that ``_pick_claim`` does not
    enforce, and this is what makes a breach of it visible instead of silent.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    board(store, claimer=CHILD, task_id="first")
    board(store, claimer=CHILD, task_id="second")
    store.apply(ApprovalRequested(CHILD, writing(CHILD)))
    store.apply(ApprovalResolved(CHILD, "p1", approved=True))

    snap = store.snapshot()
    assert snap.tasks["first"].writes == ApprovedWrites()
    assert snap.tasks["second"].writes == ApprovedWrites()
    assert snap.unattributed_writes == 1


def test_the_ambiguity_count_survives_later_intents() -> None:
    """
    The counter is carried through every reduction rather than recomputed, so an
    unrelated intent afterwards must not reset it. Without this the field reads zero
    forever and is indistinguishable from an ambiguity that never fired.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    board(store, claimer=CHILD, task_id="first")
    board(store, claimer=CHILD, task_id="second")
    store.apply(ApprovalRequested(CHILD, writing(CHILD)))
    store.apply(ApprovalResolved(CHILD, "p1", approved=True))
    store.apply(TopicChanged(CHILD, "something else"))

    assert store.snapshot().unattributed_writes == 1


def test_a_refused_approval_accumulates_nothing() -> None:
    """
    Nothing was written, so there is nothing to count. The diff on the record
    describes a write that never happened.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    board(store)
    store.apply(ApprovalRequested(CHILD, writing(CHILD)))
    store.apply(ApprovalResolved(CHILD, "p1", approved=False))

    assert store.snapshot().tasks["t1"].writes == ApprovedWrites()


def test_the_line_counts_are_what_the_operator_reviewed_not_what_reaches_disk() -> None:
    """
    An operator who rewrites a parked call's content before approving it resolves an
    approval whose diff was rendered from the arguments as parked. The line counts
    therefore describe the reviewed call. They cannot be recomputed in the reducer:
    the "before" text came off the disk at park time and this arm does no IO.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    board(store)
    store.apply(ApprovalRequested(CHILD, writing(CHILD)))
    store.apply(
        ApprovalResolved(
            CHILD,
            "p1",
            approved=True,
            edited_args={"file_path": "pptmstr/store.py", "content": "something else entirely"},
        )
    )

    assert store.snapshot().tasks["t1"].writes == ApprovedWrites(
        paths=("pptmstr/store.py",), lines_added=2, lines_removed=1
    )


def test_an_edited_path_is_the_one_recorded() -> None:
    """
    The line counts are an approximation on an edited call; the path is not.
    ``driver._park`` names correcting a wrong path as the first reason
    edit-then-approve exists, so taking the path from ``raw_args`` would name the
    file the operator rejected and miss the one they redirected the write to.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    board(store)
    store.apply(ApprovalRequested(CHILD, writing(CHILD)))
    store.apply(
        ApprovalResolved(
            CHILD,
            "p1",
            approved=True,
            edited_args={"file_path": "pptmstr/board.py"},
        )
    )

    assert store.snapshot().tasks["t1"].writes.paths == ("pptmstr/board.py",)


def test_a_bash_resolution_records_neither_a_path_nor_lines() -> None:
    """
    Bash names no file and parks with no diff, so it measures nothing. **That is not
    evidence of compliance** -- a heredoc writing a file is invisible here and always
    will be. A clean record over a session of Bash calls has earned nothing.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    board(store)
    store.apply(
        ApprovalRequested(
            CHILD, writing(CHILD, diff=None, tool="Bash", args={"command": "echo hi > f"})
        )
    )
    store.apply(ApprovalResolved(CHILD, "p1", approved=True))

    assert store.snapshot().tasks["t1"].writes == ApprovedWrites()


def test_a_notebook_edit_records_its_path_and_no_lines() -> None:
    """
    ``NotebookEdit`` carries ``notebook_path`` rather than ``file_path`` and
    ``render_diff`` returns None for it. Reading only ``file_path`` would record a
    notebook write as no write at all; the lines are simply a lower bound.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    board(store)
    store.apply(
        ApprovalRequested(
            CHILD,
            writing(CHILD, diff=None, tool="NotebookEdit", args={"notebook_path": "notes/x.ipynb"}),
        )
    )
    store.apply(ApprovalResolved(CHILD, "p1", approved=True))

    assert store.snapshot().tasks["t1"].writes == ApprovedWrites(paths=("notes/x.ipynb",))


def test_an_absolute_write_under_the_agents_cwd_is_recorded_relative() -> None:
    """
    A declaration is repository-relative by construction; a write path is whatever
    the model passed. The two are put in the same units through the agent's own cwd
    so the comparison in ``wrote_outside_declaration`` is on the same footing.
    """
    store = Store()
    store.apply(dataclasses.replace(spawn(ROOT), cwd="/home/w/repo"))
    store.apply(spawn(CHILD, ROOT))
    board(store)
    store.apply(
        ApprovalRequested(
            CHILD, writing(CHILD, args={"file_path": "/home/w/repo/pptmstr/store.py"})
        )
    )
    store.apply(ApprovalResolved(CHILD, "p1", approved=True))

    writes = store.snapshot().tasks["t1"].writes
    assert writes.paths == ("pptmstr/store.py",)
    assert writes.unplaced == ()


def test_an_absolute_write_outside_the_cwd_is_unplaced_rather_than_divergent() -> None:
    """
    A path that cannot be put in the declaration's units is held apart from one that
    can. Reporting a units mismatch as a write outside the declaration would be a
    false accusation, and it is the failure this split exists to prevent.
    """
    store = Store()
    store.apply(dataclasses.replace(spawn(ROOT), cwd="/home/w/repo"))
    store.apply(spawn(CHILD, ROOT))
    board(store, touches=("pptmstr/store.py",))
    store.apply(ApprovalRequested(CHILD, writing(CHILD, args={"file_path": "/etc/hosts"})))
    store.apply(ApprovalResolved(CHILD, "p1", approved=True))

    task = store.snapshot().tasks["t1"]
    assert task.writes.paths == ()
    assert task.writes.unplaced == ("/etc/hosts",)
    assert task.wrote_outside_declaration() == ()


def test_a_write_inside_the_declaration_does_not_diverge() -> None:
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    board(store, touches=("pptmstr/store.py", "tests/test_store.py"))
    store.apply(ApprovalRequested(CHILD, writing(CHILD)))
    store.apply(ApprovalResolved(CHILD, "p1", approved=True))

    assert store.snapshot().tasks["t1"].wrote_outside_declaration() == ()


def test_a_write_outside_the_declaration_is_named() -> None:
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    board(store, touches=("pptmstr/board.py",))
    store.apply(ApprovalRequested(CHILD, writing(CHILD)))
    store.apply(ApprovalResolved(CHILD, "p1", approved=True))

    assert store.snapshot().tasks["t1"].wrote_outside_declaration() == ("pptmstr/store.py",)


def test_a_task_that_declared_nothing_diverges_from_nothing() -> None:
    """
    Empty ``touches`` is the absence of a declaration, not a declaration of no files.
    Reading it the other way puts every write by such a task out of scope and lights
    the affordance on every row that never declared, which is most of them.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    board(store)
    store.apply(ApprovalRequested(CHILD, writing(CHILD)))
    store.apply(ApprovalResolved(CHILD, "p1", approved=True))

    assert store.snapshot().tasks["t1"].wrote_outside_declaration() == ()


def test_a_completed_task_stops_collecting_its_claimers_writes() -> None:
    """
    ``TaskCompleted`` keeps ``claimed_by``, so the state is the only thing that says
    the work is still open. A worker that keeps writing afterwards is no longer
    writing for that task, and counting it would grow a finished measurement.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    board(store)
    store.apply(TaskCompleted(CHILD, "t1", at=5.0))
    store.apply(ApprovalRequested(CHILD, writing(CHILD)))
    store.apply(ApprovalResolved(CHILD, "p1", approved=True))

    snap = store.snapshot()
    assert snap.tasks["t1"].writes == ApprovedWrites()
    assert snap.unattributed_writes == 0


def test_a_stale_resolution_counts_nothing_twice() -> None:
    """
    A double click resolves an approval that has already left ``pending``. The arm
    returns before it measures, so the counts cannot be applied a second time.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    board(store)
    store.apply(ApprovalRequested(CHILD, writing(CHILD)))
    store.apply(ApprovalResolved(CHILD, "p1", approved=True))
    store.apply(ApprovalResolved(CHILD, "p1", approved=True))

    assert store.snapshot().tasks["t1"].writes == ApprovedWrites(
        paths=("pptmstr/store.py",), lines_added=2, lines_removed=1
    )
