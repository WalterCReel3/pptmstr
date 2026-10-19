"""
The digest: one session's round as four slots, projected and never stored.

The property the module may not break is an inequality over a whole board -- a
digest never shows fewer unverified items or incidents than the board holds -- so
it is pinned as an invariant over generated boards, against an oracle that reads
the records directly and shares no code with ``digest.py`` or ``board.py``. The
examples below it pin what an invariant cannot: the order, the boundary, and what
each slot is made of.

``generated_round`` is imported by the bus and pane tests, which hold the same
invariant on their own rendering.
"""

from __future__ import annotations

import dataclasses
import random

import pytest

from pptmstr import boardlog
from pptmstr.digest import (
    ChangeKind,
    Digest,
    Doubt,
    Owed,
    change_label,
    changed_heading,
    digest,
    digest_mark,
    first_line,
    incidents_heading,
    owed_counts,
    unverified_heading,
)
from pptmstr.intents import (
    AgentSpawned,
    ApprovalRequested,
    BoardRestored,
    ConcernPosted,
    ConcernWithdrawn,
    DecisionAnswered,
    DecisionRaised,
    DigestRead,
    TaskAmended,
    TaskClaimRequested,
    TaskCompleted,
    TaskDeclared,
    TaskReleased,
    TaskWithdrawn,
)
from pptmstr.model import (
    Basis,
    Concern,
    ConcernState,
    Decision,
    NodeId,
    ObligationKind,
    PendingApproval,
    QuestionPending,
    SessionFailed,
    Snapshot,
    Standing,
    Task,
    TaskState,
)
from pptmstr.store import Store

S1 = "sess-1"
S2 = "sess-2"
LEAD: NodeId = (S1, None)
DEV: NodeId = (S1, "agent-dev")
QA: NodeId = (S1, "agent-qa")
OTHER_LEAD: NodeId = (S2, None)


def team(store: Store, session: str = S1) -> None:
    store.apply(
        AgentSpawned(
            node_id=(session, None),
            parent=None,
            task="lead",
            model="m",
            started_at=0.0,
            template="feature",
        )
    )
    for agent, role in (("agent-dev", "builder"), ("agent-qa", "reviewer")):
        store.apply(
            AgentSpawned(
                node_id=(session, agent),
                parent=(session, None),
                task=role,
                model="m",
                started_at=0.0,
                agent_type=role,
            )
        )


# -- generated boards ---------------------------------------------------------------

_STANDINGS: tuple[Basis | None, ...] = (None, Basis.RUN, Basis.READ, Basis.RELAYED)
# Unsaid, a list, and the stated "nothing was left unrun".
_UNRUN: tuple[str | None, ...] = (
    None,
    None,
    "the probe was never run live\nnor the cross build",
    "",
)


def generated_round(seed: int, *, long_text: bool = False, scale: int = 1) -> Store:
    """
    A session's round built through the reducer from one seed, beside a second
    session whose work must never be counted.

    Every choice the unverified and incident slots turn on is drawn at random and
    independently: whether a task has a specification, each author's standing,
    whether it was claimed, released, completed, what the claimer said was not
    run, and whether a concern was marked and then withdrawn. Times advance by
    one per event, so any integer is a usable ``since``.

    ``long_text`` makes every piece of prose longer than any per-piece bound, and
    ``scale`` multiplies how many tasks and concerns there may be. Together they
    are how a reply is pushed past its ceiling.
    """
    rng = random.Random(seed)
    # Its own stream, so adding withdrawals left every other draw as it was.
    withdraws = random.Random(f"withdraw-{seed}")
    pad = " lorem ipsum dolor" * 220 if long_text else ""
    store = Store()
    team(store, S1)
    team(store, S2)
    clock = iter(float(n) for n in range(1, 10_000))

    for session, lead, dev in ((S1, LEAD, DEV), (S2, OTHER_LEAD, (S2, "agent-dev"))):
        for i in range(rng.randint(0, 14 * scale)):
            tid = f"{session}-t{i}"
            store.apply(
                TaskDeclared(
                    Task(
                        id=tid,
                        title=f"do {tid}",
                        detail=rng.choice(("", f"spec of {tid}{pad}")),
                        basis=rng.choice(_STANDINGS),
                        declared_at=next(clock),
                    ),
                    node_id=lead,
                )
            )
            fate = rng.choice(("pending", "claimed", "released", "completed", "completed"))
            withdrawn = withdraws.random() < 0.2
            if fate == "pending":
                if withdrawn:
                    store.apply(TaskWithdrawn(lead, tid, f"not wanted{pad}", at=next(clock)))
                continue
            store.apply(TaskClaimRequested(dev, request_id=f"k-{tid}", task_id=tid))
            if fate == "released":
                store.apply(TaskReleased(dev, tid, at=next(clock)))
            if fate in ("claimed", "released") and withdrawn:
                store.apply(TaskWithdrawn(lead, tid, f"not wanted{pad}", at=next(clock)))
            if fate == "completed":
                store.apply(
                    TaskCompleted(
                        dev,
                        tid,
                        at=next(clock),
                        result=rng.choice(("", f"did {tid}\nand more{pad}")),
                        basis=rng.choice(_STANDINGS),
                        not_run=(lambda said: said and said + pad)(rng.choice(_UNRUN)),
                    )
                )
        for j in range(rng.randint(0, 6 * scale)):
            cid = f"{session}-c{j}"
            store.apply(
                ConcernPosted(
                    dev,
                    Concern(
                        id=cid,
                        sender=dev,
                        recipient=lead,
                        subject=f"subject of {cid}",
                        body=f"body of {cid}{pad}",
                        posted_at=next(clock),
                        task_id=rng.choice((None, f"{session}-t0", "t-nope")),
                        incident=rng.random() < 0.5,
                    ),
                )
            )
            if rng.random() < 0.25:
                store.apply(ConcernWithdrawn(cid))
    return store


def unverified_oracle(snap: Snapshot, session: str) -> set[tuple[str, Doubt]]:
    """
    What the board holds unverified, read off the records with no help from the
    code under test: one entry per statement its author did not mark as run.
    A withdrawn task holds none: its declarer took the specification back, and
    its result is the declarer's reason, which asserts no work.
    """
    held: set[tuple[str, Doubt]] = set()
    for task in snap.tasks.values():
        if task.declared_by is None or task.declared_by[0] != session:
            continue
        if task.withdrawn:
            continue
        if task.detail and task.basis is not Basis.RUN:
            held.add((task.id, Doubt.SPECIFICATION))
        if task.result and task.result_basis is not Basis.RUN:
            held.add((task.id, Doubt.RESULT))
        if task.state is TaskState.COMPLETED and task.not_run != "":
            held.add((task.id, Doubt.NOT_RUN))
    return held


def incident_oracle(snap: Snapshot, session: str) -> dict[str, float]:
    return {
        c.id: c.posted_at for c in snap.concerns.values() if c.incident and c.sender[0] == session
    }


def broadcast_oracle(snap: Snapshot, session: str) -> set[str]:
    """
    The incidents whose text a board read hands every agent of the session, read
    off the records: the ones naming a task that session declared, and not
    withdrawn. Everything else is mail between two agents or a message the
    operator stopped.
    """
    return {
        c.id
        for c in snap.concerns.values()
        if c.incident
        and c.sender[0] == session
        and c.state is not ConcernState.WITHDRAWN
        and c.task_id is not None
        and c.task_id in snap.tasks
        and (snap.tasks[c.task_id].declared_by or ("", None))[0] == session
    }


SEEDS = range(40)


@pytest.mark.parametrize("seed", SEEDS)
def test_a_digest_holds_every_unverified_statement_the_board_holds(seed: int) -> None:
    """
    The property, at the projection. Equal as sets and equal in number, so neither
    a dropped item nor one counted twice passes -- and the same from every point,
    because work that was not run last round has not been run since.
    """
    snap = generated_round(seed).snapshot()
    held = unverified_oracle(snap, S1)

    for since in (None, 0.0, 7.0, 31.0, 1e9):
        shown = [(item.task_id, item.doubt) for item in digest(snap, S1, since).unverified]
        assert set(shown) == held, (seed, since)
        assert len(shown) == len(held), (seed, since)


@pytest.mark.parametrize("seed", SEEDS)
def test_a_digest_holds_every_incident_the_board_holds_from_any_point(seed: int) -> None:
    """
    The since point sorts incidents into two slots and removes none. Whatever the
    point, the two together are every marked concern of the session, each once,
    and the split is the one the stamps give.
    """
    snap = generated_round(seed).snapshot()
    held = incident_oracle(snap, S1)

    for since in (None, 0.0, 7.0, 31.0, 1e9):
        d = digest(snap, S1, since)
        new = [r.concern.id for r in d.incidents]
        earlier = [r.concern.id for r in d.earlier_incidents]
        assert sorted(new + earlier) == sorted(held), (seed, since)
        assert set(new) == {cid for cid, at in held.items() if since is None or at >= since}


@pytest.mark.parametrize("seed", SEEDS)
def test_an_incident_is_marked_as_on_the_board_exactly_when_a_board_read_shows_it(
    seed: int,
) -> None:
    """
    ``Reported.on_board`` is what an agent-facing surface prints an incident's text
    on, so it is held to the records and not to the projection it is derived from:
    a concern that named no task, named one that does not exist, or was withdrawn
    is never marked, from any point and in either slot.
    """
    snap = generated_round(seed).snapshot()
    broadcast = broadcast_oracle(snap, S1)

    for since in (None, 7.0, 1e9):
        d = digest(snap, S1, since)
        marked = {r.concern.id for r in (*d.incidents, *d.earlier_incidents) if r.on_board}
        assert marked == broadcast, (seed, since)


def test_the_generated_boards_reach_every_case_the_invariants_claim_to_cover() -> None:
    """
    An invariant over boards that never produced a withdrawn incident, or a
    stated "nothing left unrun", would pass for a projection that mishandled
    them. The seeds are checked for the cases instead of trusted to hit them.
    """
    doubts: set[Doubt] = set()
    seen = {
        "all_run": False,
        "withdrawn incident": False,
        "foreign incident": False,
        "incident a row carries": False,
        "incident naming no task": False,
        "incident naming a task that was never declared": False,
        "withdrawn incident naming a task on the board": False,
        "withdrawn task with a specification": False,
        "withdrawn task that was held": False,
    }
    for seed in SEEDS:
        snap = generated_round(seed).snapshot()
        doubts |= {doubt for _, doubt in unverified_oracle(snap, S1)}
        seen["all_run"] |= any(t.not_run == "" for t in snap.tasks.values())
        seen["withdrawn incident"] |= any(
            c.incident and c.state.value == "withdrawn" for c in snap.concerns.values()
        )
        seen["foreign incident"] |= bool(incident_oracle(snap, S2))
        seen["incident a row carries"] |= bool(broadcast_oracle(snap, S1))
        for t in snap.tasks.values():
            if t.withdrawn and t.declared_by is not None and t.declared_by[0] == S1:
                seen["withdrawn task with a specification"] |= bool(t.detail)
                seen["withdrawn task that was held"] |= bool(t.held_by)
        for c in snap.concerns.values():
            if not c.incident or c.sender[0] != S1:
                continue
            seen["incident naming no task"] |= c.task_id is None
            seen["incident naming a task that was never declared"] |= c.task_id == "t-nope"
            seen["withdrawn incident naming a task on the board"] |= (
                c.state is ConcernState.WITHDRAWN and c.task_id in snap.tasks
            )

    assert doubts == set(Doubt)
    assert all(seen.values()), seen


# -- what each slot is made of ------------------------------------------------------


def _round() -> Store:
    """
    A small round with one of everything, at known times:

    t1 declared at 1, completed at 10 with a result. t2 declared at 2, released
    at 11. t3 declared at 20, never claimed. One incident at 5 and one at 21.
    """
    store = Store()
    team(store)
    for tid, at in (("t1", 1.0), ("t2", 2.0), ("t3", 20.0)):
        store.apply(TaskDeclared(Task(id=tid, title=f"do {tid}", declared_at=at), node_id=LEAD))
    store.apply(TaskClaimRequested(DEV, request_id="k1", task_id="t1"))
    store.apply(TaskCompleted(DEV, "t1", at=10.0, result="\n  ran 12 tests  \nand more"))
    store.apply(TaskClaimRequested(QA, request_id="k2", task_id="t2"))
    store.apply(TaskReleased(QA, "t2", at=11.0))
    for cid, at in (("c1", 5.0), ("c2", 21.0)):
        store.apply(
            ConcernPosted(
                DEV,
                Concern(
                    id=cid,
                    sender=DEV,
                    recipient=LEAD,
                    subject=cid,
                    body="b",
                    posted_at=at,
                    incident=True,
                ),
            )
        )
    return store


def test_the_changed_slot_is_what_happened_in_time_order_with_who_and_what() -> None:
    d = digest(_round().snapshot(), S1)

    assert [(c.at, c.kind, c.task_id) for c in d.changed] == [
        (1.0, ChangeKind.DECLARED, "t1"),
        (2.0, ChangeKind.DECLARED, "t2"),
        (10.0, ChangeKind.COMPLETED, "t1"),
        (11.0, ChangeKind.RELEASED, "t2"),
        (20.0, ChangeKind.DECLARED, "t3"),
    ]
    completed = d.changed[2]
    assert (completed.holder, completed.line) == ("builder", "ran 12 tests")
    # A release names nobody: the record keeps who ever declined a task, not who
    # did so last.
    assert d.changed[3].holder is None


def test_the_changed_slot_starts_at_the_point_and_counts_the_point_itself() -> None:
    """
    At the boundary an event is shown. Shown twice across two digests is the
    cheaper way to be wrong than shown in neither.
    """
    snap = _round().snapshot()

    assert [c.task_id for c in digest(snap, S1, since=10.0).changed] == ["t1", "t2", "t3"]
    assert [c.task_id for c in digest(snap, S1, since=10.5).changed] == ["t2", "t3"]
    assert digest(snap, S1, since=99.0).changed == ()


def test_incidents_before_the_point_are_carried_and_not_dropped() -> None:
    d = digest(_round().snapshot(), S1, since=10.0)

    assert [(r.at, r.concern.id) for r in d.incidents] == [(21.0, "c2")]
    assert [(r.at, r.concern.id) for r in d.earlier_incidents] == [(5.0, "c1")]


def test_the_waiting_slot_is_needs_you_for_this_session_in_its_order() -> None:
    """
    Projected from the one list, not re-derived: the snapshot is handed a
    ``needs_you`` its node table does not imply, and the slot follows the list.
    Another session's obligation is not this session's.
    """
    snap = dataclasses.replace(
        _round().snapshot(),
        needs_you=(
            SessionFailed(node=DEV, since=3.0, summary="crashed"),
            QuestionPending(node=OTHER_LEAD, since=4.0, summary="other"),
            QuestionPending(node=LEAD, since=6.0, summary="asked"),
        ),
    )

    assert digest(snap, S1).waiting == (
        Owed(ObligationKind.FAILURE, "builder", 3.0, "crashed"),
        Owed(ObligationKind.QUESTION, "lead", 6.0, "asked"),
    )


def test_a_parked_approval_reaches_the_waiting_slot_through_the_store() -> None:
    store = _round()
    store.apply(
        ApprovalRequested(
            DEV,
            PendingApproval(
                id="p1",
                node=DEV,
                tool_name="Bash",
                tool_use_id="tu",
                raw_args={"command": "make dist"},
                summary="make dist",
                requested_at=30.0,
            ),
        )
    )

    (owed,) = digest(store.snapshot(), S1).waiting
    assert owed == Owed(ObligationKind.APPROVAL, "builder", 30.0, "make dist")


def _asked(did: str, question: str) -> Decision:
    return Decision(
        id=did,
        asker=DEV,
        question=question,
        blocks=("t3",),
        if_unanswered="I stop",
        evidence=question,
        recommendation=question,
        standing=Standing.OPERATORS,
        reversible=False,
        raised_at=0.0,
    )


def taken_round() -> Store:
    """
    ``_round`` with five decisions in S1 and one in S2, raised at the times in
    their ids' order: d1 taken and answered, d2 taken and open, d3 the
    operator's and open, d4 taken and dismissed, d5 taken and open, d6 taken and
    withdrawn by its asker; and x1 taken, open, in another session.
    """
    from pptmstr.intents import DecisionDismissed, DecisionWithdrawn

    store = _round()
    team(store, S2)
    for at, did, standing, asker in (
        (1.0, "d1", Standing.TAKEN, DEV),
        (2.0, "d2", Standing.TAKEN, DEV),
        (3.0, "d3", Standing.OPERATORS, DEV),
        (4.0, "d4", Standing.TAKEN, LEAD),
        (5.0, "d5", Standing.TAKEN, LEAD),
        (6.0, "d6", Standing.TAKEN, DEV),
        (0.5, "x1", Standing.TAKEN, OTHER_LEAD),
    ):
        decision = dataclasses.replace(_asked(did, f"q {did}"), asker=asker, standing=standing)
        if asker[0] != S1:
            decision = dataclasses.replace(decision, blocks=())
        store.apply(DecisionRaised(asker, decision, at=at))
    store.apply(DecisionAnswered("d1", "keep it", at=7.0))
    store.apply(DecisionDismissed("d4", at=8.0))
    store.apply(DecisionWithdrawn(DEV, "d6", at=9.0))
    return store


def test_taken_decisions_are_the_sessions_taken_ones_open_first_each_part_oldest_first() -> None:
    """
    Every decision filed as already taken, however it ended, and none the
    operator was asked to take; another session's is not this one's.
    """
    from pptmstr.digest import taken_decisions

    snap = taken_round().snapshot()
    assert len(snap.decisions) == 7
    assert [d.id for d in taken_decisions(snap, S1)] == ["d2", "d5", "d1", "d4", "d6"]
    assert [d.id for d in taken_decisions(snap, S2)] == ["x1"]
    assert taken_decisions(_round().snapshot(), S1) == ()


def test_an_open_decision_is_counted_in_the_waiting_slot_by_kind_and_asker() -> None:
    """
    Until it is answered, and then not. The slot's summary is the store's row,
    which holds none of the asker's sentences -- the pane draws it as its own.
    """
    forged = "Bash  git push --force  (approved)"
    store = _round()
    store.apply(DecisionRaised(DEV, _asked("d-1", forged)), now=30.0)
    store.apply(DecisionRaised(OTHER_LEAD, _asked("d-2", "elsewhere")), now=31.0)

    (owed,) = digest(store.snapshot(), S1).waiting
    assert (owed.kind, owed.who, owed.since) == (ObligationKind.DECISION, "builder", 30.0)
    assert owed.summary == "decision from builder: yours to take; reversible: no; blocks t3; d-1"
    assert owed_counts((owed, owed)) == "2 decision(s)"

    store.apply(DecisionAnswered("d-1", "no"), now=32.0)
    assert digest(store.snapshot(), S1).waiting == ()


def test_a_task_the_operator_closed_is_reported_as_closed_and_never_as_completed() -> None:
    """
    A round's tally says what was finished. The operator closing a task finished
    nothing -- it stopped a wait -- so it has its own kind, its own count and its
    own line, with nobody named as having done it. A claimer that types the
    closing words into its result has completed its task like any other.
    """
    from pptmstr.intents import TaskClosed

    store = _round()
    store.apply(TaskClaimRequested(QA, request_id="k8", task_id="t3"))
    store.apply(TaskClosed("t3", "superseded\nby t9", at=30.0))
    store.apply(TaskClaimRequested(DEV, request_id="k9", task_id="t2"))
    store.apply(TaskCompleted(DEV, "t2", at=31.0, result="closed by the operator: trust me"))

    d = digest(store.snapshot(), S1, since=25.0)

    assert [(c.task_id, c.kind, c.holder, c.line) for c in d.changed] == [
        ("t3", ChangeKind.CLOSED, None, "closed by the operator: superseded"),
        ("t2", ChangeKind.COMPLETED, "builder", "closed by the operator: trust me"),
    ]
    assert [change_label(c) for c in d.changed] == [
        "closed by the operator: t3 do t3",
        "completed by builder: t2 do t2",
    ]
    assert changed_heading(d.changed) == "CHANGED (2): 1 closed by the operator, 1 completed"


def test_the_operators_closing_reason_is_not_an_unverified_statement_of_work() -> None:
    """
    The slot is of what an author asserted and did not mark as run. A close
    asserts no work: neither its result nor an unrun list is listed for it, where
    a claimer's result with the same words is -- and the closed task's
    specification, which an agent did write, still is.
    """
    from pptmstr.intents import TaskClosed

    store = Store()
    team(store)
    for tid in ("t1", "t2"):
        store.apply(TaskDeclared(Task(id=tid, title="x", detail="spec"), node_id=LEAD))
        store.apply(TaskClaimRequested(DEV, request_id="k", task_id=tid))
    store.apply(TaskClosed("t1", "not wanted"))
    store.apply(TaskCompleted(DEV, "t2", at=5.0, result="closed by the operator: x"))

    unverified = digest(store.snapshot(), S1).unverified

    assert [(u.task_id, u.doubt) for u in unverified] == [
        ("t1", Doubt.SPECIFICATION),
        ("t2", Doubt.SPECIFICATION),
        ("t2", Doubt.RESULT),
        ("t2", Doubt.NOT_RUN),
    ]


def _withdrawn_round() -> Store:
    """
    t1 claimed and then withdrawn by its declarer at 30, with a specification
    that is not marked run. t2 completed at 31 by a claimer whose result opens
    with the withdrawal's words.
    """
    store = Store()
    team(store)
    for tid in ("t1", "t2"):
        store.apply(
            TaskDeclared(
                Task(id=tid, title=f"do {tid}", detail="spec", declared_at=1.0), node_id=LEAD
            )
        )
        store.apply(TaskClaimRequested(DEV, request_id=f"k-{tid}", task_id=tid))
    store.apply(TaskWithdrawn(LEAD, "t1", "superseded\nby t9", at=30.0))
    store.apply(TaskCompleted(DEV, "t2", at=31.0, result="withdrawn by its declarer: trust me"))
    return store


def test_a_task_its_declarer_withdrew_is_reported_as_withdrawn_and_never_as_completed() -> None:
    """
    A withdrawal finished nothing, like a close, so it has its own kind, count
    and line with nobody named as having done it. Decided by the row's flag: a
    claimer typing the same words has completed its task.
    """
    d = digest(_withdrawn_round().snapshot(), S1, since=25.0)

    assert [(c.task_id, c.kind, c.holder, c.line) for c in d.changed] == [
        ("t1", ChangeKind.WITHDRAWN, None, "withdrawn by its declarer: superseded"),
        ("t2", ChangeKind.COMPLETED, "builder", "withdrawn by its declarer: trust me"),
    ]
    assert [change_label(c) for c in d.changed] == [
        "withdrawn by its declarer: t1 do t1",
        "completed by builder: t2 do t2",
    ]
    assert changed_heading(d.changed) == "CHANGED (2): 1 withdrawn, 1 completed"


def test_a_withdrawal_by_the_lead_or_with_an_edited_reason_says_whose_words_it_is() -> None:
    """
    The lead withdrew t1 in place of the reviewer that declared it and stopped;
    the operator rewrote the lead's reason for t2. Neither line says "withdrawn
    by its declarer" over words the declarer did not write, and the tally counts
    both under the one word.
    """
    from pptmstr.intents import AgentFinished
    from pptmstr.model import REASON_EDITED, WITHDRAWN_BY_LEAD, AgentState

    store = Store()
    team(store)
    store.apply(TaskDeclared(Task(id="t1", title="do t1", declared_at=1.0), node_id=QA))
    store.apply(TaskDeclared(Task(id="t2", title="do t2", declared_at=1.0), node_id=LEAD))
    store.apply(AgentFinished(QA, AgentState.DONE, ended_at=2.0))
    store.apply(TaskWithdrawn(LEAD, "t1", "the plan changed", at=30.0))
    store.apply(TaskWithdrawn(LEAD, "t2", "the operator's words", at=31.0, edited=True))

    d = digest(store.snapshot(), S1, since=25.0)

    assert [change_label(c) for c in d.changed] == [
        f"{WITHDRAWN_BY_LEAD}: t1 do t1",
        f"withdrawn by its declarer, {REASON_EDITED}: t2 do t2",
    ]
    assert [(c.withdrawn_by_lead, c.withdrawal_edited) for c in d.changed] == [
        (True, False),
        (False, True),
    ]
    assert changed_heading(d.changed) == "CHANGED (2): 2 withdrawn"


def test_a_withdrawn_task_is_nowhere_in_the_unverified_slot() -> None:
    """
    Neither its reason, which asserts no work, nor its specification, which its
    declarer has taken back -- where a claimer's result in the same words is
    listed, and a closed task's specification still is.
    """
    unverified = digest(_withdrawn_round().snapshot(), S1).unverified

    assert [(u.task_id, u.doubt) for u in unverified] == [
        ("t2", Doubt.SPECIFICATION),
        ("t2", Doubt.RESULT),
        ("t2", Doubt.NOT_RUN),
    ]


def test_a_statement_marked_run_and_a_task_said_fully_run_are_not_unverified() -> None:
    """
    The other direction of the property: the slot is not padded. A task whose
    three statements were each said to be run contributes nothing, and a task with
    no specification text and no result has no statement of those kinds.
    """
    store = Store()
    team(store)
    store.apply(
        TaskDeclared(Task(id="t1", title="x", detail="spec", basis=Basis.RUN), node_id=LEAD)
    )
    store.apply(TaskDeclared(Task(id="t2", title="x", declared_at=1.0), node_id=LEAD))
    store.apply(TaskClaimRequested(DEV, request_id="k1", task_id="t1"))
    store.apply(TaskCompleted(DEV, "t1", at=2.0, result="ran it", basis=Basis.RUN, not_run=""))

    d = digest(store.snapshot(), S1)
    assert d.unverified == ()
    assert unverified_heading(d.unverified).startswith("UNVERIFIED (0): nothing.")
    assert "nothing checked them" in unverified_heading(d.unverified)


def test_an_empty_unverified_slot_does_not_report_work_as_marked_run() -> None:
    """
    The slot is as empty on a board of title-only tasks nobody has finished as on
    one where every author said ``run``. Its heading is one sentence for both, so
    it may say what is absent and may not say that every result is marked run:
    here there is no result.
    """
    store = Store()
    team(store)
    store.apply(TaskDeclared(Task(id="t1", title="x"), node_id=LEAD))

    d = digest(store.snapshot(), S1)
    heading = unverified_heading(d.unverified)

    assert d.unverified == ()
    assert "Every specification and result" not in heading
    assert "is marked run" not in heading
    assert "No specification or result on the board is marked anything short of run" in heading
    assert "as true of a board with no such text and nothing finished" in heading


def test_each_unverified_item_says_which_statement_and_where_it_stands() -> None:
    store = Store()
    team(store)
    store.apply(
        TaskDeclared(Task(id="t1", title="x", detail="spec", basis=Basis.RELAYED), node_id=LEAD)
    )
    store.apply(TaskClaimRequested(DEV, request_id="k1", task_id="t1"))
    store.apply(TaskCompleted(DEV, "t1", at=2.0, result="r", not_run="the probe"))

    items = digest(store.snapshot(), S1).unverified
    assert [(i.doubt, i.standing, i.text, i.owner) for i in items] == [
        (Doubt.SPECIFICATION, "says: relayed", "", "builder"),
        (Doubt.RESULT, "basis unstated", "", "builder"),
        (Doubt.NOT_RUN, "says what was not run", "the probe", "builder"),
    ]


def test_an_empty_session_has_all_four_slots_and_each_says_it_is_empty() -> None:
    """
    Empty is a statement and is made by name. The incident heading in particular
    says "none marked", which is what an empty slot can support.
    """
    store = Store()
    team(store)
    d = digest(store.snapshot(), S1)

    assert d == Digest(since=None, changed=(), waiting=(), unverified=(), incidents=())
    assert changed_heading(d.changed) == (
        "CHANGED (0): nothing was declared, amended, completed or released."
    )
    assert incidents_heading(d).startswith("INCIDENTS (0): none marked.")
    assert "is not counted here" in incidents_heading(d)


def test_the_incident_heading_counts_the_earlier_ones_it_is_not_listing_as_new() -> None:
    d = digest(_round().snapshot(), S1, since=10.0)
    quiet = digest(_round().snapshot(), S1, since=99.0)

    assert incidents_heading(d) == "INCIDENTS (1 this round, 1 earlier):"
    assert incidents_heading(quiet).startswith(
        "INCIDENTS (0 this round, 2 earlier): none marked this round."
    )


def test_the_first_line_of_a_result_skips_blank_lines_and_is_empty_for_none() -> None:
    assert first_line("\n\n  ran it  \nmore") == "ran it"
    assert first_line("") == "" and first_line(" \n ") == ""


# -- the mark -----------------------------------------------------------------------


def _read(store: Store, node: NodeId, at: float) -> Digest:
    from pptmstr.effects import DigestDelivered

    (effect,) = store.apply(DigestRead(node, request_id="g1", at=at))
    assert isinstance(effect, DigestDelivered) and effect.digest is not None
    return effect.digest


def test_a_first_read_is_the_whole_session_and_the_next_starts_where_it_stopped() -> None:
    """
    The mark is a fact in the snapshot. The first read has none and is handed
    everything; it leaves one, and the second is handed only what came after --
    with the earlier incident carried, and the unverified slot unchanged.
    """
    store = _round()
    assert digest_mark(store.snapshot(), S1) is None

    first = _read(store, LEAD, at=15.0)
    assert first.since is None and len(first.changed) == 5
    assert digest_mark(store.snapshot(), S1) == 15.0

    second = _read(store, LEAD, at=40.0)
    assert second.since == 15.0
    assert [c.task_id for c in second.changed] == ["t3"]
    assert [r.concern.id for r in second.incidents] == ["c2"]
    assert [r.concern.id for r in second.earlier_incidents] == ["c1"]
    assert second.unverified == first.unverified != ()
    assert digest_mark(store.snapshot(), S1) == 40.0


def test_each_reader_has_its_own_mark_and_the_panes_is_the_leads() -> None:
    store = _round()
    _read(store, DEV, at=15.0)

    assert store.snapshot().get(DEV).digest_read_at == 15.0  # type: ignore[union-attr]
    assert digest_mark(store.snapshot(), S1) is None
    assert _read(store, LEAD, at=16.0).since is None


def test_a_reader_with_no_record_is_answered_in_full_and_leaves_no_mark() -> None:
    store = _round()
    ghost: NodeId = (S1, "agent-ghost")

    assert _read(store, ghost, at=15.0).since is None
    assert _read(store, ghost, at=16.0).since is None
    assert store.snapshot().get(ghost) is None


def test_a_release_is_stamped_by_its_emitter_or_by_the_frame_it_lands_in() -> None:
    """
    With a time, the emitter's -- the clock a digest mark is taken on. Without
    one, the frame's, so a release from a caller with no clock still has a place
    in a round instead of none.
    """
    stamped, unstamped = Store(), Store()
    for store in (stamped, unstamped):
        team(store)
        store.apply(TaskDeclared(Task(id="t1", title="x"), node_id=LEAD))
        store.apply(TaskClaimRequested(DEV, request_id="k", task_id="t1"))
    stamped.apply(TaskReleased(DEV, "t1", at=7.0), now=99.0)
    unstamped.apply(TaskReleased(DEV, "t1"), now=99.0)

    assert stamped.snapshot().tasks["t1"].released_at == 7.0
    assert unstamped.snapshot().tasks["t1"].released_at == 99.0
    assert Task(id="t", title="t").released_at is None


def test_an_incident_is_listed_with_what_became_of_it() -> None:
    """
    A withdrawn incident stays in the slot, so its line has to say it never
    reached its recipient -- and an edited one that what was delivered is not what
    was written. Without them the list reads as four concerns that all arrived.

    The label is the whole of what is pinned here. That a withdrawn incident's
    text is kept from agents is ``on_board``, pinned below and over generated
    boards, and at the tool in ``tests/test_bus.py``.
    """
    from pptmstr.digest import incident_attribution, incident_label
    from pptmstr.intents import ConcernEdited

    store = Store()
    team(store)
    store.apply(TaskDeclared(Task(id="t1", title="x"), node_id=LEAD))
    for cid, about, basis in (
        ("c1", "t9", Basis.RUN),
        ("c2", None, None),
        ("c3", None, None),
        ("c4", "t1", None),
        ("c5", "t1", None),
    ):
        store.apply(
            ConcernPosted(
                DEV,
                Concern(
                    id=cid,
                    sender=DEV,
                    recipient=LEAD,
                    subject=cid,
                    body="b",
                    posted_at=1.0,
                    incident=True,
                    task_id=about,
                    basis=basis,
                ),
            )
        )
    store.apply(ConcernWithdrawn("c2"))
    store.apply(ConcernEdited("c3", body="softer"))
    store.apply(ConcernWithdrawn("c5"))

    reported = digest(store.snapshot(), S1).incidents
    assert [incident_label(r) for r in reported] == [
        "[builder to lead, says: run, about t9] c1",
        "[builder to lead, basis unstated, withdrawn by the operator before delivery] c2",
        "[builder to lead, basis unstated, edited by the operator before delivery] c3",
        "[builder to lead, basis unstated, about t1] c4",
        "[builder to lead, basis unstated, about t1, withdrawn by the operator before delivery] c5",
    ]
    # Only the one a row of the board carries: c1 names a task nobody declared,
    # c2 and c3 name none, and c5 was withdrawn.
    assert [r.concern.id for r in reported if r.on_board] == ["c4"]
    # The attribution is the label short of the sender's own words, which is the
    # part a surface may print of an incident whose subject it has to withhold.
    for r in reported:
        assert incident_label(r) == f"{incident_attribution(r)} {r.concern.subject}"
        assert incident_attribution(r).endswith("]") and r.concern.subject not in (
            incident_attribution(r)
        )


def _amended_round() -> Store:
    """
    ``_round`` with t1's specification replaced by the lead at 12 and by the
    operator at 22, and t3's replaced at 23 by an amendment that changed nothing.
    """
    store = _round()
    store.apply(TaskAmended("t1", detail="second", node_id=LEAD, at=12.0))
    store.apply(TaskAmended("t1", detail="third", at=22.0))
    store.apply(TaskAmended("t3", detail="", at=23.0))
    return store


def test_each_replaced_specification_is_news_with_who_replaced_it_and_which_version() -> None:
    """
    Two amendments of one task are two lines: a digest that showed only the last
    would report a round in which a specification changed twice as one change.
    An amendment that changed nothing is not news.
    """
    d = digest(_amended_round().snapshot(), S1)
    replaced = [c for c in d.changed if c.kind is ChangeKind.AMENDED]

    assert [(c.at, c.task_id, c.holder, c.version) for c in replaced] == [
        (12.0, "t1", "lead", 2),
        (22.0, "t1", "the operator", 3),
    ]
    assert [c.at for c in d.changed] == sorted(c.at for c in d.changed)
    assert change_label(replaced[0]) == "specification replaced by lead, now v2: t1 do t1"
    assert changed_heading(d.changed).endswith("2 specification replaced")


def test_a_replaced_specification_is_placed_against_the_point_by_when_it_was_replaced() -> None:
    """
    The task was declared and finished before the point. Only the amendment after
    it is this round's, and the one stamped exactly at the point counts.
    """
    snap = _amended_round().snapshot()

    def since(point: float) -> list[tuple[float, ChangeKind]]:
        return [(c.at, c.kind) for c in digest(snap, S1, point).changed if c.task_id == "t1"]

    assert since(12.0) == [(12.0, ChangeKind.AMENDED), (22.0, ChangeKind.AMENDED)]
    assert since(12.5) == [(22.0, ChangeKind.AMENDED)]
    assert since(22.5) == []


# -- a board that came back from its log --------------------------------------------

_CLOCK = boardlog.Clock(wall=5_000.0, monotonic=100.0)
_OLD_DEV = (S1, "agent-old")


def _resumed_round() -> Store:
    """
    A resumed session. Its log held one task finished by a builder of the earlier
    process and one still free; the board is restored, the team starts, and the
    new lead declares a third task and the new builder finishes the free one.
    """
    old = [
        boardlog.Declared(
            at=_CLOCK.wall - 50.0,
            task_id="t1",
            title="old work",
            declared_by="lead",
            declared_by_id=(S1,),
        ),
        boardlog.Claimed(at=_CLOCK.wall - 40.0, task_id="t1", by="builder", by_id=_OLD_DEV),
        boardlog.Completed(
            at=_CLOCK.wall - 30.0, task_id="t1", by="builder", result="done then", by_id=_OLD_DEV
        ),
        boardlog.Declared(
            at=_CLOCK.wall - 20.0,
            task_id="t2",
            title="left over",
            declared_by="lead",
            declared_by_id=(S1,),
        ),
    ]
    store = Store()
    store.apply(BoardRestored(S1, boardlog.restore(old), _CLOCK), now=_CLOCK.monotonic)
    team(store)
    # Declared on the very instant the board came back: the nearest a change of
    # this process can be to the restore.
    store.apply(TaskDeclared(Task(id="t3", title="new work", declared_at=100.0), LEAD))
    store.apply(TaskClaimRequested(DEV, request_id="k", task_id="t2"))
    store.apply(TaskCompleted(DEV, "t2", at=120.0, result="done now"))
    return store


def test_a_resumed_leads_first_digest_says_which_of_its_changes_are_the_logs_history() -> None:
    """
    The resumed lead has no mark, so it is handed the whole board, and a digest is
    what it reports to the operator from. Three of these five happened in a
    process that is gone. Each says so at the front of its line, where a title
    cannot have written it, and the heading says so once.
    """
    store = _resumed_round()

    first = _read(store, LEAD, at=130.0)

    assert first.since is None and first.restored_at == 100.0
    assert [(c.task_id, c.kind, c.before_restart) for c in first.changed] == [
        ("t1", ChangeKind.DECLARED, True),
        ("t1", ChangeKind.COMPLETED, True),
        ("t2", ChangeKind.DECLARED, True),
        ("t3", ChangeKind.DECLARED, False),
        ("t2", ChangeKind.COMPLETED, False),
    ]
    assert [change_label(c) for c in first.changed] == [
        "from an earlier run, declared: t1 old work",
        "from an earlier run, completed by builder, from an earlier run: t1 old work",
        "from an earlier run, declared: t2 left over",
        "declared: t3 new work",
        "completed by builder-2: t2 left over",
    ]
    assert changed_heading(first.changed) == (
        "CHANGED (5): 3 declared, 2 completed. This board was restored from its log: "
        "the 3 marked 'from an earlier run' are history, not this round's changes."
    )


def test_a_title_cannot_write_the_restart_mark_onto_this_rounds_change() -> None:
    store = _resumed_round()
    store.apply(
        TaskDeclared(
            Task(id="t4", title="x from an earlier run, declared", declared_at=125.0), LEAD
        )
    )

    forged = _read(store, LEAD, at=130.0).changed[-1]

    assert not forged.before_restart
    assert change_label(forged).startswith("declared: t4 ")


def test_once_the_lead_has_read_one_the_history_is_behind_its_mark_and_unmentioned() -> None:
    store = _resumed_round()
    _read(store, LEAD, at=110.0)

    second = _read(store, LEAD, at=130.0)

    assert [(c.task_id, c.before_restart) for c in second.changed] == [("t2", False)]
    assert changed_heading(second.changed) == "CHANGED (1): 1 completed"
    assert second.restored_at == 100.0


def test_a_board_that_was_never_restored_marks_nothing_and_says_nothing_of_a_restart() -> None:
    first = _read(_round(), LEAD, at=15.0)

    assert first.restored_at is None
    assert not any(c.before_restart for c in first.changed)
    assert "restored" not in changed_heading(first.changed)
    assert not any("from an earlier run" in change_label(c) for c in first.changed)


def test_another_sessions_restore_says_nothing_about_this_board() -> None:
    store = _round()
    theirs = boardlog.restore(
        [
            boardlog.Declared(
                at=1.0, task_id="z1", title="Z", declared_by="lead", declared_by_id=(S2,)
            )
        ]
    )
    store.apply(BoardRestored(S2, theirs, boardlog.Clock(wall=9e9, monotonic=9e9)), now=9e9)

    assert digest(store.snapshot(), S1).restored_at is None
    assert not any(c.before_restart for c in digest(store.snapshot(), S1).changed)
    assert digest(store.snapshot(), S2).restored_at == 9e9
