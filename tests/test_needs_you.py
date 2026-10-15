"""
``Snapshot.needs_you``: one projection over every way an agent can block.

The defect this replaced was not that approvals were counted wrongly -- they were
counted correctly, and everything else was not counted at all. So most of what is
worth asserting here is about the *union*: that a question and a crash are
obligations on the same footing as an approval, that they sort together by age, and
that nothing counts one kind while implying it has checked all three.
"""

from __future__ import annotations

import dataclasses
import random

import pytest

from pptmstr.intents import (
    AgentFinished,
    AgentRemoved,
    AgentSpawned,
    ApprovalRequested,
    DecisionAnswered,
    DecisionDismissed,
    DecisionRaised,
    DecisionsRead,
    DecisionWithdrawn,
    Intent,
    StateChanged,
    TaskDeclared,
)
from pptmstr.model import (
    MAX_ANSWER_CHARS,
    MAX_OPEN_DECISIONS_PER_ASKER,
    AgentState,
    Answered,
    ApprovalNeeded,
    AskerFate,
    Decision,
    DecisionOwed,
    Dismissed,
    NodeId,
    ObligationKind,
    PendingApproval,
    QuestionPending,
    SessionFailed,
    Snapshot,
    Standing,
    Task,
    Withdrawn,
)
from pptmstr.store import Store, _apply

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


# -- a supervising lead is not an obligation -------------------------------------


def test_a_supervising_lead_owes_the_operator_nothing() -> None:
    """
    A lead waiting on its workers is waiting on its *workers*. Nothing is owed, so
    nothing is queued.

    This is deliberate and it is the reason SUPERVISING is not read here. The list
    is sorted oldest-first and a lead enters the state when the fan-out begins,
    which is older than every approval the fan-out then produces -- so listing it
    would not merely add a row, it would hold position 0 for the whole fan-out.
    ``ui/focus.py`` takes ``needs_you[0]`` for an unplaced cursor, so the cursor
    that decides which agent an approve applies to would sit on the one node with
    nothing to approve; and "N need you" would count a session that needs nobody,
    which is the exact defect this projection was built to end.

    Where the operator meets a supervising lead instead: the rail's own row and its
    topic, ``ui/compose.py``, and ``ui/health.py``.
    """
    store = Store()
    store.apply(spawn(ROOT), now=1.0)
    store.apply(spawn(CHILD, ROOT), now=1.0)
    store.apply(StateChanged(ROOT, AgentState.SUPERVISING), now=10.0)

    assert store.snapshot().needs_you == ()


def test_a_supervising_lead_still_surfaces_its_own_parked_call() -> None:
    """
    Not an obligation is not the same as invisible. A lead that entered the wait and
    then parked a call of its own is blocked in the ordinary way, and the state must
    not swallow the approval -- an approval nobody can reach is a permanent hang.
    """
    store = Store()
    store.apply(spawn(ROOT), now=1.0)
    store.apply(StateChanged(ROOT, AgentState.SUPERVISING), now=10.0)
    store.apply(ApprovalRequested(ROOT, pending(ROOT, "p1", at=11.0)), now=11.0)

    (obligation,) = store.snapshot().needs_you
    assert isinstance(obligation, ApprovalNeeded)
    assert obligation.node == ROOT


def test_a_supervising_lead_does_not_hide_its_workers_obligations() -> None:
    """
    The queue during a fan-out is the workers' queue. A lead in a new state must not
    change what its children are able to say -- and position 0 must be something the
    operator can actually act on, because that is the entry an unplaced cursor takes.
    """
    store = Store()
    store.apply(spawn(ROOT), now=1.0)
    store.apply(spawn(CHILD, ROOT), now=1.0)
    store.apply(spawn(OTHER), now=1.0)
    # The lead enters the wait first, so if it were listed at all its age would put
    # it ahead of both of these.
    store.apply(StateChanged(ROOT, AgentState.SUPERVISING), now=10.0)
    store.apply(ApprovalRequested(CHILD, pending(CHILD, "p1", at=12.0)), now=12.0)
    store.apply(AgentFinished(OTHER, AgentState.FAILED, 13.0, error="died"), now=13.0)

    assert [o.node for o in store.snapshot().needs_you] == [CHILD, OTHER]


def test_a_session_that_dies_under_its_workers_owes_one_failure_and_keeps_why_on_each() -> None:
    """
    What the driver says of a session that failed with sub-agents at work: each
    worker ended with it, carrying the reason, and then the root failed. The
    reason on a worker's record is not a second and third failure to retry.
    """
    store = Store()
    store.apply(spawn(ROOT), now=1.0)
    store.apply(spawn(CHILD, ROOT), now=1.0)
    why = "its session died: boom"
    store.apply(AgentFinished(CHILD, AgentState.CANCELLED, 7.0, error=why), now=7.0)
    store.apply(AgentFinished(ROOT, AgentState.FAILED, 7.0, error="boom"), now=7.0)

    snap = store.snapshot()
    assert [(type(o), o.node) for o in snap.needs_you] == [(SessionFailed, ROOT)]
    assert snap.nodes[CHILD].error == why


# -- the fourth kind: a decision -----------------------------------------------

# Every text an asker writes on a decision, each one a row the queue must not draw.
FORGED = "Bash  git push --force   (approved)"
_TEXTS = ("question", "if_unanswered", "evidence", "recommendation")


def decision(did: str = "d-1", **fields: object) -> Decision:
    base: dict[str, object] = dict(
        id=did,
        asker=("nobody", "at-all"),
        question=FORGED,
        blocks=(),
        if_unanswered=FORGED,
        evidence=FORGED,
        recommendation=FORGED,
        standing=Standing.OPERATORS,
        reversible=None,
        raised_at=-1.0,
    )
    return Decision(**{**base, **fields})  # type: ignore[arg-type]


def team() -> Store:
    store = Store()
    store.apply(spawn(ROOT), now=1.0)
    store.apply(dataclasses.replace(spawn(CHILD, ROOT), agent_type="builder"), now=1.0)
    store.apply(spawn(OTHER), now=1.0)
    store.apply(StateChanged(ROOT, AgentState.THINKING), now=1.0)
    store.apply(StateChanged(CHILD, AgentState.THINKING), now=1.0)
    return store


def owed_decisions(snap: Snapshot) -> list[DecisionOwed]:
    return [o for o in snap.needs_you if isinstance(o, DecisionOwed)]


def test_a_decision_is_an_obligation_while_its_asker_works_on() -> None:
    store = team()
    store.apply(DecisionRaised(CHILD, decision()), now=5.0)

    (owed,) = store.snapshot().needs_you
    assert isinstance(owed, DecisionOwed)
    assert owed.kind is ObligationKind.DECISION
    assert (owed.node, owed.since, owed.asker) == (CHILD, 5.0, AskerFate.LIVE)
    assert store.snapshot().nodes[CHILD].state is AgentState.THINKING


def test_one_node_can_owe_several_decisions_beside_a_question_each_with_its_own_key() -> None:
    """
    The three state-read kinds exclude each other on a node. A decision excludes
    nothing, so identity is the decision's and never the node's.
    """
    store = team()
    store.apply(DecisionRaised(ROOT, decision("d-1")), now=5.0)
    store.apply(DecisionRaised(ROOT, decision("d-2")), now=6.0)
    store.apply(StateChanged(ROOT, AgentState.AWAITING_INPUT), now=7.0)

    owed = store.snapshot().needs_you
    assert [o.kind for o in owed] == [
        ObligationKind.DECISION,
        ObligationKind.DECISION,
        ObligationKind.QUESTION,
    ]
    assert {o.node for o in owed} == {ROOT}
    assert len({o.key for o in owed}) == 3
    assert [o.key for o in owed[:2]] == ["decision:d-1", "decision:d-2"]


def test_decisions_sort_into_the_queue_by_how_long_they_have_waited() -> None:
    store = team()
    store.apply(StateChanged(OTHER, AgentState.AWAITING_INPUT), now=4.0)
    store.apply(DecisionRaised(CHILD, decision("d-late")), now=9.0)
    store.apply(DecisionRaised(CHILD, decision("d-early"), at=2.0), now=9.5)

    assert [o.since for o in store.snapshot().needs_you] == [2.0, 4.0, 9.0]


@pytest.mark.parametrize("field", _TEXTS)
def test_no_sentence_the_asker_wrote_is_in_the_queue_row(field: str) -> None:
    """
    The queue draws ``summary`` in its own voice beside approvals, so each text
    field is given a line that would read as an approved call, alone, and the row
    must not hold it.
    """
    store = team()
    quiet = {name: "nothing" for name in _TEXTS}
    store.apply(DecisionRaised(CHILD, decision(**{**quiet, field: FORGED})), now=5.0)

    (owed,) = owed_decisions(store.snapshot())
    for word in ("Bash", "push", "force", "approved"):
        assert word not in owed.summary, owed.summary
    assert getattr(owed.decision, field) == FORGED, "and it is still on the record"


def test_the_row_says_who_asked_whose_it_is_whether_it_undoes_and_what_it_blocks() -> None:
    """
    And in that order, with the id last: a row cut to a narrow pane loses its
    end, and what is lost should be a random token.
    """
    store = team()
    for task_id in ("t-4", "t-5"):
        store.apply(TaskDeclared(Task(id=task_id, title="x", declared_at=1.0), node_id=ROOT))
    store.apply(
        DecisionRaised(
            CHILD,
            decision("d-3", blocks=("t-4", "t-5"), standing=Standing.TAKEN, reversible=False),
        ),
        now=5.0,
    )

    (owed,) = owed_decisions(store.snapshot())
    assert owed.summary == (
        "decision from builder: already taken, wants ratifying; "
        "reversible: no; blocks t-4, t-5; d-3"
    )


def test_a_sentence_about_a_task_is_not_a_statement_that_the_decision_blocks_it() -> None:
    """
    ``blocks`` was prose once, read for task ids a word at a time, and "this
    does not hold up t-4" put "blocks t-4" on the row in the queue's own voice.
    It is ids now, and a sentence in their place is not filed at all.
    """
    store = team()
    store.apply(TaskDeclared(Task(id="t-4", title="x", declared_at=1.0), node_id=ROOT))

    store.apply(DecisionRaised(CHILD, decision("d-1", blocks=("this does not hold up t-4",))))
    assert owed_decisions(store.snapshot()) == []

    store.apply(DecisionRaised(CHILD, decision("d-2", question="does not hold up t-4")), now=5.0)
    (owed,) = owed_decisions(store.snapshot())
    assert "t-4" not in owed.summary and "blocks nothing on the board" in owed.summary


def test_more_blocked_tasks_than_the_row_shows_are_counted() -> None:
    store = team()
    ids = tuple(f"t-{n}" for n in range(5))
    for task_id in ids:
        store.apply(TaskDeclared(Task(id=task_id, title="x", declared_at=1.0), node_id=ROOT))
    store.apply(DecisionRaised(ROOT, decision(blocks=(*ids, "t-0"))), now=5.0)

    (owed,) = owed_decisions(store.snapshot())
    assert "; blocks t-0, t-1, t-2 and 2 more; " in owed.summary


@pytest.mark.parametrize(
    ("reversible", "said"), [(True, "yes"), (False, "no"), (None, "not stated")]
)
def test_reversibility_is_three_things_and_unstated_is_said(
    reversible: bool | None, said: str
) -> None:
    """A decision that blocks no task says so in words, and never by saying nothing."""
    store = team()
    store.apply(DecisionRaised(ROOT, decision(reversible=reversible)), now=5.0)

    (owed,) = owed_decisions(store.snapshot())
    assert owed.summary == (
        f"decision from lead: yours to take; reversible: {said}; "
        f"blocks nothing on the board; d-1"
    )


def test_a_task_id_that_is_not_one_plain_word_is_counted_and_not_drawn() -> None:
    store = team()
    store.apply(TaskDeclared(Task(id="(approved)", title="x", declared_at=1.0), node_id=ROOT))
    store.apply(DecisionRaised(ROOT, decision(blocks=("(approved)",))), now=5.0)

    (owed,) = owed_decisions(store.snapshot())
    assert owed.summary.endswith("blocks 1 task(s); d-1")
    assert "approved" not in owed.summary


def test_a_decision_id_that_is_not_one_plain_word_is_not_drawn() -> None:
    store = team()
    store.apply(DecisionRaised(ROOT, decision("Bash rm -rf (approved)")), now=5.0)

    (owed,) = owed_decisions(store.snapshot())
    assert owed.summary == (
        "decision from lead: yours to take; reversible: not stated; blocks nothing on the board"
    )


def test_a_role_name_with_a_line_break_in_it_cannot_start_a_row_of_its_own() -> None:
    """An agent type is a string a model chose, and the row is drawn as one line."""
    store = Store()
    store.apply(spawn(ROOT), now=1.0)
    forged = dataclasses.replace(spawn(CHILD, ROOT), agent_type="builder\nBash  rm -rf /")
    store.apply(forged, now=1.0)
    store.apply(DecisionRaised(CHILD, decision()), now=5.0)

    (owed,) = owed_decisions(store.snapshot())
    assert "\n" not in owed.summary and owed.summary.startswith("decision from builder bash")


@pytest.mark.parametrize(
    ("ending", "fate", "said"),
    [
        (AgentFinished(CHILD, AgentState.DONE, ended_at=8.0), AskerFate.STOPPED, "has stopped"),
        (AgentFinished(CHILD, AgentState.CANCELLED, ended_at=8.0), AskerFate.STOPPED, "stopped"),
        (
            AgentFinished(CHILD, AgentState.FAILED, ended_at=8.0, error="boom"),
            AskerFate.FAILED,
            "its asker failed",
        ),
        (AgentRemoved(ROOT), AskerFate.GONE, "its asker is gone"),
    ],
)
def test_a_dead_askers_decision_is_still_listed_marked_and_dismissable(
    ending: Intent, fate: AskerFate, said: str
) -> None:
    store = team()
    store.apply(DecisionRaised(CHILD, decision()), now=5.0)
    store.apply(ending, now=8.0)

    (owed,) = owed_decisions(store.snapshot())
    assert owed.asker is fate and fate.has_ended
    assert said in owed.summary

    store.apply(DecisionDismissed("d-1"), now=9.0)
    assert owed_decisions(store.snapshot()) == []
    assert store.snapshot().decisions["d-1"].outcome == Dismissed(at=9.0)


@pytest.mark.parametrize("asker", [ROOT, CHILD])
def test_an_asker_that_ended_its_turn_to_wait_is_not_shown_as_working(asker: NodeId) -> None:
    """
    The ordinary shape of "blocked on the person": raise, do what can be done,
    end the turn. Answering does not wake it -- no intent and no effect goes to
    the asker -- so a row that read as a working agent's would let the operator
    answer and leave, with the answer unread for good. The fate says so before
    they answer, and it goes back to live when the session is prompted.
    """
    store = team()
    store.apply(DecisionRaised(asker, decision()), now=5.0)
    store.apply(StateChanged(asker, AgentState.AWAITING_INPUT), now=6.0)

    (owed,) = owed_decisions(store.snapshot())
    assert owed.asker is AskerFate.IDLE and not owed.asker.has_ended
    assert "its asker has ended its turn and reads an answer only when woken" in owed.summary

    before = store.snapshot().nodes[asker]
    effects = store.apply(DecisionAnswered("d-1", "go"), now=7.0)
    assert effects == (), "nothing is sent to the asker"
    assert store.snapshot().nodes[asker] == before, "and nothing wakes it"

    store.apply(DecisionRaised(asker, decision("d-2")), now=8.0)
    store.apply(StateChanged(asker, AgentState.THINKING), now=9.0)
    (again,) = owed_decisions(store.snapshot())
    assert again.asker is AskerFate.LIVE and "asker" not in again.summary


def test_a_sub_agent_with_no_record_yet_is_not_reported_as_gone() -> None:
    """
    A sub-agent can call the bus before its start is reported, and it may be
    working. "Gone" on its row tells the operator to dismiss a live agent's
    question. It is gone only when its session's root has no record either, and
    a root with no record is gone by itself.
    """
    early = (ROOT[0], "not-announced-yet")
    store = team()
    store.apply(DecisionRaised(early, decision("d-early")), now=5.0)
    store.apply(DecisionRaised(CHILD, decision("d-child")), now=5.5)
    store.apply(DecisionRaised(("no-such-session", None), decision("d-root")), now=6.0)
    store.apply(DecisionRaised(("no-such-session", "a"), decision("d-orphan")), now=7.0)
    store.apply(AgentRemoved(CHILD), now=7.5)

    fates = {o.decision.id: o.asker for o in owed_decisions(store.snapshot())}
    assert fates == {
        "d-early": AskerFate.UNANNOUNCED,
        # Removed on its own, which nothing does today, reads the same way.
        "d-child": AskerFate.UNANNOUNCED,
        "d-root": AskerFate.GONE,
        "d-orphan": AskerFate.GONE,
    }
    (row,) = [o for o in owed_decisions(store.snapshot()) if o.decision.id == "d-early"]
    assert "its asker has not announced itself yet" in row.summary
    assert not AskerFate.UNANNOUNCED.has_ended

    store.apply(AgentRemoved(ROOT), now=8.0)
    assert {o.asker for o in owed_decisions(store.snapshot())} == {AskerFate.GONE}


@pytest.mark.parametrize(
    ("ended", "within"),
    [
        (AgentState.DONE, AskerFate.STOPPED),
        (AgentState.FAILED, AskerFate.FAILED),
        # What the driver leaves a sub-agent that was still working when its
        # session was closed or relaunched over.
        (AgentState.CANCELLED, AskerFate.STOPPED),
    ],
)
def test_an_asker_of_an_earlier_run_in_this_window_is_gone_and_not_merely_stopped(
    ended: AgentState, within: AskerFate
) -> None:
    """
    The session is started again in the same window: nothing was restarted, so
    the asker still has its record and no restored board names it. "Has stopped"
    on its decision says it can still be woken; nothing in the run that is going
    now wakes it, and every other surface already calls it an agent of an
    earlier run (``Snapshot.of_earlier_run``).

    An agent of the run that is going now is not swept up with it: one that
    stops after the session was started again is stopped, and can be woken.
    """
    store = team()
    store.apply(DecisionRaised(CHILD, decision()), now=5.0)
    store.apply(AgentFinished(CHILD, ended, ended_at=8.0), now=8.0)
    (before,) = owed_decisions(store.snapshot())
    assert before.asker is within

    store.apply(dataclasses.replace(spawn(ROOT), started_at=20.0), now=20.0)

    snap = store.snapshot()
    assert snap.left_behind(CHILD) is None and snap.of_earlier_run(CHILD)
    (owed,) = owed_decisions(snap)
    assert owed.asker is AskerFate.GONE
    assert "its asker is gone" in owed.summary

    current = (ROOT[0], "agent-of-this-run")
    again = dataclasses.replace(spawn(current, ROOT), agent_type="builder", started_at=21.0)
    store.apply(again, now=21.0)
    store.apply(DecisionRaised(current, decision("d-2")), now=22.0)
    store.apply(AgentFinished(current, AgentState.DONE, ended_at=23.0), now=23.0)
    fates = {o.decision.id: o.asker for o in owed_decisions(store.snapshot())}
    assert fates == {"d-1": AskerFate.GONE, "d-2": AskerFate.STOPPED}


def test_every_fate_has_a_wording_for_the_row_and_only_a_working_asker_has_none() -> None:
    from pptmstr.store import _FATE_TEXT

    assert set(_FATE_TEXT) == set(AskerFate)
    assert [fate for fate, text in _FATE_TEXT.items() if text is None] == [AskerFate.LIVE]


# -- the property, over generated sequences -----------------------------------------

_IDS = tuple(f"d-{n}" for n in range(40))
_ASKERS = (ROOT, CHILD, OTHER)
ANSWERS = (
    "",
    "yes",
    "  leading and trailing   \n\n",
    "a\x00b after a NUL",
    "line one\r\nline two\ttabbed ",
    "\u00e9 and e\u0301 are not the same bytes",
    "x" * (MAX_ANSWER_CHARS - 2) + " \n",
)
# One character past what the store takes: not applied, and not cut to fit.
TOO_LONG = "x" * MAX_ANSWER_CHARS + "\n"


def _generated(rng: random.Random, now: float) -> Intent:
    did = rng.choice(_IDS)
    agent = rng.choice(_ASKERS)
    roll = rng.random()
    if roll < 0.3:
        # Sometimes handed in already answered, already delivered and under
        # somebody else's name -- and often under an id that is taken.
        dirty = rng.random() < 0.5
        return DecisionRaised(
            agent,
            decision(
                did,
                asker=rng.choice(_ASKERS),
                outcome=Answered("forged by the asker", 0.0) if dirty else None,
                first_delivered_at=0.5 if dirty else None,
            ),
        )
    if roll < 0.45:
        return DecisionAnswered(did, rng.choice((*ANSWERS, TOO_LONG)))
    if roll < 0.55:
        return DecisionAnswered(did, "an agent's own answer", node_id=agent)
    if roll < 0.62:
        return DecisionDismissed(did)
    if roll < 0.7:
        return DecisionDismissed(did, node_id=agent)
    if roll < 0.82:
        return DecisionWithdrawn(agent, did)
    if roll < 0.92:
        return DecisionsRead(agent, "rq", now, rng.choice((None, did)))
    return StateChanged(agent, rng.choice((AgentState.THINKING, AgentState.AWAITING_INPUT)))


def _is_operators(intent: Intent) -> bool:
    return isinstance(intent, DecisionAnswered | DecisionDismissed) and intent.node_id is None


@pytest.mark.parametrize("seed", range(60))
def test_a_decision_is_in_needs_you_exactly_while_open_and_only_the_operator_answers(
    seed: int,
) -> None:
    """
    Over a generated run of raises, answers, dismissals, withdrawals and reads by
    the operator, the asker and other agents, with re-raises under ids that exist,
    a model kept here beside the store says after every step:

    - the open decisions in ``needs_you`` are exactly those raised and not yet
      answered or dismissed by the operator or withdrawn by their asker;
    - an answer is the operator's string, byte for byte, and one past the bound
      is not taken at all;
    - a step that was not the operator's left every outcome as it was, unless it
      was the asker withdrawing its own open decision;
    - a decision's asker, once filed, never changes, and it is the node that
      raised it whatever the record handed in said;
    - no agent has more than its cap open.
    """
    rng = random.Random(seed)
    snap = team().snapshot()
    asker_of: dict[str, NodeId] = {}
    open_ids: set[str] = set()
    answers: dict[str, str] = {}

    for step in range(300):
        now = 10.0 + step
        intent = _generated(rng, now)
        before = snap
        snap, _ = _apply(snap, intent, now)

        match intent:
            case DecisionRaised():
                own_open = sum(1 for d in open_ids if asker_of[d] == intent.node_id)
                if intent.decision.id not in asker_of and own_open < MAX_OPEN_DECISIONS_PER_ASKER:
                    asker_of[intent.decision.id] = intent.node_id
                    open_ids.add(intent.decision.id)
            case DecisionAnswered() if _is_operators(intent):
                if intent.decision_id in open_ids and intent.answer != TOO_LONG:
                    open_ids.discard(intent.decision_id)
                    answers[intent.decision_id] = intent.answer
            case DecisionDismissed() if _is_operators(intent):
                open_ids.discard(intent.decision_id)
            case DecisionWithdrawn():
                if asker_of.get(intent.decision_id) == intent.node_id:
                    open_ids.discard(intent.decision_id)
            case _:
                pass

        assert {o.decision.id for o in owed_decisions(snap)} == open_ids, (step, intent)
        assert {d.id: d.asker for d in snap.decisions.values()} == asker_of, (step, intent)
        assert {
            d.id: d.answer for d in snap.decisions.values() if d.answer is not None
        } == answers, (step, intent)
        for owed in owed_decisions(snap):
            assert owed.decision.outcome is None and owed.decision.first_delivered_at is None
        for held in snap.decisions.values():
            if held.first_delivered_at is not None:
                assert isinstance(held.outcome, Answered | Dismissed), (step, intent)

        if not _is_operators(intent):
            for did, was in before.decisions.items():
                now_is = snap.decisions[did]
                if now_is.outcome != was.outcome:
                    assert isinstance(intent, DecisionWithdrawn), (step, intent)
                    assert was.outcome is None and intent.node_id == was.asker
                    assert now_is.outcome == Withdrawn(at=now)

    assert len(answers) > 3, "too few answers for the byte check to have checked anything"
    assert len(asker_of) - len(open_ids) - len(answers) > 0, "nothing dismissed or withdrawn"


@pytest.mark.parametrize("answer", ANSWERS, ids=range(len(ANSWERS)))
def test_the_operators_answer_is_stored_byte_for_byte(answer: str) -> None:
    store = team()
    store.apply(DecisionRaised(CHILD, decision()), now=5.0)
    store.apply(DecisionAnswered("d-1", answer), now=6.0)

    stored = store.snapshot().decisions["d-1"].answer
    assert stored is not None
    assert stored.encode("utf-8", "surrogatepass") == answer.encode("utf-8", "surrogatepass")
    assert len(stored) == len(answer)
    assert owed_decisions(store.snapshot()) == []


def test_the_cap_counts_one_agents_open_decisions_and_frees_as_they_end() -> None:
    store = team()
    for n in range(MAX_OPEN_DECISIONS_PER_ASKER):
        store.apply(DecisionRaised(CHILD, decision(f"d-{n}")), now=5.0 + n)
    store.apply(DecisionRaised(CHILD, decision("d-over")), now=20.0)
    store.apply(DecisionRaised(ROOT, decision("d-leads")), now=21.0)
    store.apply(DecisionRaised(OTHER, decision("d-elsewhere")), now=21.0)

    snap = store.snapshot()
    assert "d-over" not in snap.decisions
    assert "d-leads" in snap.decisions, "a worker at its cap has spent none of its lead's"
    assert "d-elsewhere" in snap.decisions, "another session has its own allowance"
    assert len([o for o in owed_decisions(snap) if o.node == CHILD]) == MAX_OPEN_DECISIONS_PER_ASKER

    store.apply(DecisionAnswered("d-0", "go"), now=22.0)
    store.apply(DecisionRaised(CHILD, decision("d-over")), now=23.0)
    assert "d-over" in store.snapshot().decisions
