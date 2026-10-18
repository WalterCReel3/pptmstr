"""
Where a restore places a logged decision, and why it leaves one out.

The same rules as a task's and a concern's (``store._restored``): a decision is
placed when its id is free and its asker is of the session being restored; the
store's own decision under the id is left alone; a store whose decisions for the
session are not the log's takes nothing from the board. The round trip from the
reducer through the log and back is ``tests/test_boardfacts.py``'s.
"""

from __future__ import annotations

from typing import Any

import pytest

from pptmstr import boardlog
from pptmstr.boardlog import Clock, RestoredDecision
from pptmstr.intents import AgentSpawned, BoardRestored, DecisionRaised
from pptmstr.model import (
    MAX_ANSWER_CHARS,
    Answered,
    Decision,
    Dismissed,
    NodeId,
    NotPlaced,
    Snapshot,
    Standing,
    Unplaced,
    Withdrawn,
)
from pptmstr.store import _apply

S = "sess-1"
ROOT: NodeId = (S, None)
CHILD: NodeId = (S, "agent-a")
OTHER: NodeId = ("sess-2", None)
CLOCK = Clock(wall=5_000.0, monotonic=100.0)


def logged(did: str = "d-1", **fields: Any) -> RestoredDecision:
    base: dict[str, Any] = dict(
        id=did,
        asker=CHILD,
        question="ship with the old schema?",
        blocks=(),
        if_unanswered="I carry on with the old one",
        evidence="3 of 40 rows fail",
        recommendation="keep it",
        standing="taken",
        reversible=None,
        raised_at=CLOCK.wall - 30.0,
    )
    return RestoredDecision(**{**base, **fields})


def restore(snap: Snapshot, *decisions: RestoredDecision, session_id: str = S) -> Snapshot:
    board = boardlog.Board(decisions=decisions)
    after, effects = _apply(snap, BoardRestored(session_id, board, CLOCK), CLOCK.monotonic)
    assert effects == ()
    return after


def unplaced(snap: Snapshot, session_id: str = S) -> tuple[NotPlaced, ...]:
    return snap.restores[session_id].unplaced


def test_a_logged_decision_is_placed_with_its_times_on_this_clock() -> None:
    snap = restore(
        Snapshot.empty(),
        logged(
            outcome=Answered(" yes \n", CLOCK.wall - 20.0),
            first_delivered_at=CLOCK.wall - 10.0,
            channel="brand",
        ),
    )

    assert snap.decisions["d-1"] == Decision(
        id="d-1",
        asker=CHILD,
        question="ship with the old schema?",
        blocks=(),
        if_unanswered="I carry on with the old one",
        evidence="3 of 40 rows fail",
        recommendation="keep it",
        standing=Standing.TAKEN,
        reversible=None,
        raised_at=70.0,
        outcome=Answered(" yes \n", 80.0),
        first_delivered_at=90.0,
        channel="brand",
    )
    report = snap.restores[S]
    assert report.decisions == 1 and report.unplaced == () and not report.short


def test_a_decision_another_session_asked_is_not_this_boards_to_restore() -> None:
    snap = restore(Snapshot.empty(), logged(asker=OTHER))

    assert snap.decisions == {}
    assert unplaced(snap) == (NotPlaced("decision", "d-1", Unplaced.NOT_THIS_SESSIONS),)


def test_an_id_another_sessions_decision_has_is_never_written_over() -> None:
    snap, _ = _apply(Snapshot.empty(), AgentSpawned(OTHER, None, "t", "m", 0.0), 0.0)
    theirs = Decision(
        id="d-1",
        asker=OTHER,
        question="theirs",
        blocks=(),
        if_unanswered="",
        evidence="",
        recommendation="",
        standing=Standing.OPERATORS,
        reversible=None,
        raised_at=0.0,
    )
    snap, _ = _apply(snap, DecisionRaised(OTHER, theirs), 1.0)
    held = snap.decisions["d-1"]

    after = restore(snap, logged())

    assert after.decisions["d-1"] is held
    assert unplaced(after) == (
        NotPlaced("decision", "d-1", Unplaced.OTHER_SESSION_HAS_ID, holder="sess-2"),
    )


def test_a_store_holding_a_decision_the_log_lacks_takes_nothing_from_the_board() -> None:
    """
    The store is ahead of the log or beside it. Placing the log's decision
    beside the store's would make a set of decisions neither writer held.
    """
    snap = restore(Snapshot.empty(), logged("d-1"))
    first = snap.decisions

    after = restore(snap, logged("d-2"))

    assert after.decisions == first
    assert unplaced(after) == (
        NotPlaced("decision", "d-1", Unplaced.STORE_ONLY),
        NotPlaced("decision", "d-2", Unplaced.WITHHELD),
    )
    assert after.restores[S].refused and not after.restores[S].adoptable


def test_a_store_holding_the_decision_differently_takes_nothing_from_the_board() -> None:
    snap = restore(Snapshot.empty(), logged())

    after = restore(snap, logged(outcome=Dismissed(CLOCK.wall - 5.0)))

    assert after.decisions == snap.decisions
    assert unplaced(after) == (NotPlaced("decision", "d-1", Unplaced.STORE_DIFFERS),)


def test_the_same_board_twice_is_no_step_and_a_later_clock_is_the_same_board() -> None:
    board = boardlog.Board(decisions=(logged(outcome=Withdrawn(CLOCK.wall - 5.0)),))
    snap, _ = _apply(Snapshot.empty(), BoardRestored(S, board, CLOCK), CLOCK.monotonic)
    later = Clock(wall=CLOCK.wall + 40.0, monotonic=CLOCK.monotonic + 39.5)

    again, _ = _apply(snap, BoardRestored(S, board, later), later.monotonic)

    assert again is snap


@pytest.mark.parametrize(
    "incoherent",
    [
        logged(standing="a member no build has yet"),
        logged(outcome=Answered("x" * (MAX_ANSWER_CHARS + 1), CLOCK.wall - 5.0)),
        logged(first_delivered_at=CLOCK.wall - 5.0),
        logged(outcome=Withdrawn(CLOCK.wall - 6.0), first_delivered_at=CLOCK.wall - 5.0),
    ],
    ids=["unknown standing", "answer too long", "delivered open", "delivered withdrawn"],
)
def test_a_decision_no_step_of_the_store_leaves_is_left_out(
    incoherent: RestoredDecision,
) -> None:
    snap = restore(Snapshot.empty(), incoherent)

    assert snap.decisions == {}
    assert unplaced(snap) == (NotPlaced("decision", "d-1", Unplaced.INCOHERENT),)


def test_a_question_holding_what_no_store_writes_is_left_out_and_an_answer_is_not() -> None:
    """
    Every text of a decision but the operator's answer is the store's drawable
    form, so a NUL there was written by something else. The answer is held as
    typed (``store._VERBATIM``) and is placed holding one.
    """
    answer = "yes \x00 and \ud800"
    snap = restore(
        Snapshot.empty(),
        logged("d-1", question="a \x00 question"),
        logged("d-2", outcome=Answered(answer, CLOCK.wall - 5.0)),
    )

    assert set(snap.decisions) == {"d-2"}
    assert snap.decisions["d-2"].answer == answer
    assert unplaced(snap) == (NotPlaced("decision", "d-1", Unplaced.UNDRAWABLE),)


def test_a_board_naming_a_decision_twice_places_the_first() -> None:
    snap = restore(Snapshot.empty(), logged(), logged(question="the second"))

    assert snap.decisions["d-1"].question == "ship with the old schema?"
    assert unplaced(snap) == (NotPlaced("decision", "d-1", Unplaced.REPEATED),)


def test_a_restored_decision_blocking_a_task_the_board_lacks_is_still_placed() -> None:
    """
    The raise arm checks ``blocks`` against the board; a restore does not, since
    the task may be one this restore could not place, and the operator's answer
    is not lost over a link.
    """
    snap = restore(Snapshot.empty(), logged(blocks=("t-gone",)))

    assert snap.decisions["d-1"].blocks == ("t-gone",)
    assert unplaced(snap) == ()


def test_a_restored_decision_keeps_its_channel_and_readability_does_not_read_it() -> None:
    """
    ``channel`` is stored and nothing reads it yet: the root of the asker's
    session reads the decision and no other session does, whatever the channel.
    """
    snap = restore(Snapshot.empty(), logged(channel="brand"))
    decision = snap.decisions["d-1"]

    assert decision.channel == "brand"
    assert snap.decisions_readable_by(ROOT) == (decision,)
    assert snap.decisions_readable_by(OTHER) == ()
    assert snap.decisions_readable_by(CHILD) == (decision,)
