"""
The fixture ``--fake`` runs on, where it is the only thing that puts a state on
screen. A surface drawn only against this fixture is drawn against whatever this
seeds, so what it seeds is pinned.
"""

from __future__ import annotations

import asyncio

from pptmstr.bridge import Bridge
from pptmstr.fake_driver import FakeDriver
from pptmstr.intents import DecisionAnswered, DecisionDismissed
from pptmstr.model import DecisionOwed, Snapshot, Standing
from pptmstr.store import Store


def _seeded() -> tuple[Snapshot, list[object]]:
    """The store after the fixture's opening tree, and every intent that built it."""
    bridge = Bridge()
    asyncio.run(FakeDriver(bridge)._seed_tree())
    intents = list(bridge.drain())
    store = Store()
    store.apply_all(intents, now=1.0)
    return store.snapshot(), list(intents)


def test_the_fixture_seeds_one_open_decision_and_one_answered() -> None:
    """
    Both lives of a decision: the open one is in the operator's queue with every
    field its row is composed from, and the answered one has left the queue and
    holds an answer nobody has read back yet.
    """
    snap, _ = _seeded()

    by_state = {d.is_open: d for d in snap.decisions.values()}
    assert len(snap.decisions) == 2 and set(by_state) == {True, False}
    waiting, answered = by_state[True], by_state[False]

    owed = [o for o in snap.needs_you if isinstance(o, DecisionOwed)]
    assert [o.decision.id for o in owed] == [waiting.id]
    assert (waiting.standing, waiting.reversible) == (Standing.OPERATORS, False)
    assert waiting.asker[1] is not None and waiting.session_id == answered.session_id
    # It names a task that is on the same session's board.
    assert "t2" in waiting.blocks and snap.tasks["t2"].belongs_to(waiting.session_id)

    assert (answered.standing, answered.reversible) == (Standing.TAKEN, None)
    assert answered.asker[1] is None
    assert answered.answer is not None and "\n" in answered.answer
    assert answered.first_delivered_at is None


def test_both_seeded_decisions_keep_their_evidence_apart_from_their_recommendation() -> None:
    snap, _ = _seeded()

    for decision in snap.decisions.values():
        assert decision.evidence.strip() and decision.recommendation.strip()
        assert decision.evidence != decision.recommendation
        assert decision.recommendation not in decision.evidence


def test_the_fixtures_answer_is_the_operators_and_would_not_land_as_an_agents() -> None:
    """
    The fixture stands in for the person at the pane, so its answer names no
    node. The counterweight: the same answer naming the asker changes nothing,
    which is what keeps this the only way the fixture could have answered.
    """
    snap, intents = _seeded()

    ended = [i for i in intents if isinstance(i, DecisionAnswered | DecisionDismissed)]
    assert [(i.decision_id, i.node_id) for i in ended] == [("fake-d2", None)]

    store = Store()
    as_an_agent = [
        (
            DecisionAnswered(i.decision_id, i.answer, node_id=snap.decisions["fake-d2"].asker)
            if isinstance(i, DecisionAnswered)
            else i
        )
        for i in intents
    ]
    store.apply_all(as_an_agent, now=1.0)  # type: ignore[arg-type]
    assert store.snapshot().decisions["fake-d2"].is_open
