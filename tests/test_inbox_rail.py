"""
The pure decisions behind the two new panes.

Only the parts that can be checked without a GL context: what a row calls itself,
how tall a card is, and what a badge counts. These are exactly the rules that were
got wrong once already and fixed by looking at pixels, so they are the ones worth
holding in place with something cheaper than a screenshot.
"""

from __future__ import annotations

from types import MappingProxyType

from pptmstr.model import (
    AgentRecord,
    AgentState,
    ApprovalNeeded,
    NodeId,
    ObligationKind,
    PendingApproval,
    QuestionPending,
    SessionFailed,
    Snapshot,
)
from pptmstr.ui import inbox, rail

ROOT: NodeId = ("s1", None)
CHILD: NodeId = ("s1", "agent-a")
OTHER: NodeId = ("s2", None)


def record(
    node: NodeId,
    parent: NodeId | None = None,
    *,
    task: str = "reconcile disagreeing TLE sets",
    state: AgentState = AgentState.THINKING,
    agent_type: str | None = None,
    cwd: str | None = "/x/orbital",
) -> AgentRecord:
    return AgentRecord(
        node_id=node,
        parent=parent,
        depth=0 if parent is None else 1,
        state=state,
        topic="working",
        task=task,
        model="claude-sonnet-5",
        agent_type=agent_type,
        cwd=cwd,
    )


def snapshot(*records: AgentRecord, needs_you: tuple[object, ...] = ()) -> Snapshot:
    return Snapshot(
        seq=1,
        nodes=MappingProxyType({r.node_id: r for r in records}),
        order=tuple(r.node_id for r in records),
        needs_you=needs_you,  # type: ignore[arg-type]
        any_active=True,
    )


def approval(node: NodeId, pid: str = "p1", *, at: float = 1.0) -> ApprovalNeeded:
    return ApprovalNeeded(
        node=node,
        since=at,
        summary="Write /tmp/x",
        approval=PendingApproval(
            id=pid,
            node=node,
            tool_name="Write",
            tool_use_id=f"tu-{pid}",
            raw_args={},
            summary="Write /tmp/x",
            requested_at=at,
        ),
    )


# -- identity ------------------------------------------------------------------


def test_a_row_is_titled_by_the_session_task() -> None:
    """
    Every root is called "session". At twenty sessions the old queue read "session"
    on six rows of eight, and the fix that landed for the rail's cards did not carry
    over on its own because the inbox had only been looked at with seven.
    """
    snap = snapshot(record(ROOT), needs_you=(approval(ROOT),))
    title, qualifier = inbox.identity(snap, approval(ROOT))
    assert title == "reconcile disagreeing TLE sets"
    assert qualifier == "orbital"


def test_a_sub_agents_call_is_titled_by_its_session_and_qualified_by_itself() -> None:
    """
    The approval belongs to the sub-agent, but a sub-agent is only findable through
    the session that spawned it. Session title identifies; project / sub-agent
    qualifies.
    """
    snap = snapshot(record(ROOT), record(CHILD, ROOT, agent_type="code-reviewer"))
    title, qualifier = inbox.identity(snap, approval(CHILD))
    assert title == "reconcile disagreeing TLE sets"
    assert qualifier == "orbital / code-reviewer"


# -- card density --------------------------------------------------------------


def test_height_tracks_obligation_not_terminality() -> None:
    """
    Three classes, not two.

    Splitting on terminality put working-but-not-blocking sessions -- the majority
    at twenty sessions, and the ones least likely to be acted on -- at the same
    height as a blocked one, filling the rail with the cards that matter least.
    """
    blocked = record(ROOT, state=AgentState.AWAITING_APPROVAL)
    active = record(ROOT, state=AgentState.THINKING)
    ended = record(ROOT, state=AgentState.DONE)

    assert rail._density(blocked, [approval(ROOT)], False) == "blocked"
    assert rail._density(active, [], False) == "active"
    assert rail._density(ended, [], False) == "ended"
    assert rail._LINES["blocked"] > rail._LINES["active"] > rail._LINES["ended"]


def test_a_failed_session_is_blocked_not_ended() -> None:
    """A crash is an obligation, so its card gets the height to say so."""
    failed = record(ROOT, state=AgentState.FAILED)
    owed = [SessionFailed(node=ROOT, since=1.0, summary="died", error="died")]
    assert rail._density(failed, owed, False) == "blocked"


def test_sub_agents_earn_a_pip_row() -> None:
    blocked = record(ROOT, state=AgentState.AWAITING_APPROVAL)
    assert rail._LINES[rail._density(blocked, [approval(ROOT)], True)] > rail._LINES["blocked"]


# -- counting ------------------------------------------------------------------


def test_a_card_counts_every_kind_its_session_owes() -> None:
    """
    The habit that reproduced defect 1 inside the fix for defect 1: a project
    header summed approvals and read "6 sessions" with no waiting count while that
    project held a question and a crashed session.
    """
    owed = (
        approval(CHILD),
        QuestionPending(node=OTHER, since=2.0, summary="ended its turn"),
        SessionFailed(node=ROOT, since=3.0, summary="died"),
    )
    snap = snapshot(record(ROOT), record(CHILD, ROOT), record(OTHER), needs_you=owed)

    by_session = rail._by_session(snap)
    # The sub-agent's approval and the root's failure both belong to session s1.
    assert len(by_session["s1"]) == 2
    assert len(by_session["s2"]) == 1


def test_a_mixed_badge_does_not_claim_one_kind() -> None:
    mixed = [approval(ROOT), QuestionPending(node=ROOT, since=2.0, summary="x")]
    assert rail._badge_kind(mixed) is ObligationKind.APPROVAL
    assert rail._badge_kind([SessionFailed(ROOT, 1.0, "died")]) is ObligationKind.FAILURE
