"""
A decision raised on a channel (Decided 12): the asker's lead raises it over a
live link its session is a member of, the store stamps the link it resolved to,
and the lead on the link's other end reads it while that link is live.

Readability keys on the link and never the name, so a decision raised over a
closed link is not read through a later link of the same name. Decisions with
no channel are read exactly as before. Nothing here wakes anyone: an answer
waking every member is round three's.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from pptmstr import boardlog
from pptmstr.boardfacts import outcomes
from pptmstr.boardlog import Clock
from pptmstr.effects import DecisionSettled
from pptmstr.intents import (
    AgentSpawned,
    BoardRestored,
    ChannelClosed,
    ChannelLinked,
    ChannelsRestored,
    DecisionAnswered,
    DecisionRaised,
    DecisionsRead,
    DecisionWithdrawn,
    Intent,
)
from pptmstr.model import (
    Channel,
    Decision,
    DecisionRefusal,
    NodeId,
    Snapshot,
    Standing,
    Unplaced,
)
from pptmstr.store import Store, _apply
from tests.test_bus import _live_bus

S, Z, C = "s", "z", "c"
LEAD: NodeId = (S, None)
BUILDER: NodeId = (S, "a1")
Z_LEAD: NodeId = (Z, None)
Z_BUILDER: NodeId = (Z, "b1")
C_LEAD: NodeId = (C, None)

_NODES: tuple[tuple[NodeId, NodeId | None], ...] = (
    (LEAD, None),
    (BUILDER, LEAD),
    (Z_LEAD, None),
    (Z_BUILDER, Z_LEAD),
    (C_LEAD, None),
)


def _spawns() -> tuple[AgentSpawned, ...]:
    return tuple(
        AgentSpawned(
            node_id=node,
            parent=parent,
            task="t",
            model="claude-opus-5",
            started_at=0.0,
            agent_type=None if parent is None else "builder",
            cwd="/w" if parent is None else None,
        )
        for node, parent in _NODES
    )


def link(cid: str, name: str, *members: str) -> ChannelLinked:
    return ChannelLinked(cid, name, frozenset(members), "charter", 1.0, frozenset(members))


def run(snap: Snapshot, *intents: Intent) -> tuple[Snapshot, tuple[Any, ...]]:
    effects: tuple[Any, ...] = ()
    for intent in intents:
        snap, effects = _apply(snap, intent, 5.0)
    return snap, effects


def started() -> Snapshot:
    """Three running sessions; ``s`` and ``z`` each with a builder; nothing linked."""
    snap, _ = run(Snapshot.empty(), *_spawns())
    return snap


def linked() -> Snapshot:
    """``s`` and ``z`` linked as #brand under ``k1``."""
    snap, _ = run(started(), link("k1", "brand", S, Z))
    return snap


def decision(did: str = "d-1", **fields: Any) -> Decision:
    fields = {"asker": LEAD, **fields}
    return Decision(
        id=did,
        question="ship the hero image?",
        blocks=(),
        if_unanswered="I ship it",
        evidence="",
        recommendation="",
        standing=Standing.OPERATORS,
        reversible=None,
        raised_at=0.0,
        **fields,
    )


def raised(
    snap: Snapshot, by: NodeId = LEAD, did: str = "d-1", channel: str | None = "brand"
) -> tuple[Snapshot, DecisionRefusal | None]:
    after, effects = run(
        snap, DecisionRaised(by, decision(did, asker=by), request_id="rq", at=3.0, channel=channel)
    )
    (settled,) = effects
    assert isinstance(settled, DecisionSettled)
    return after, settled.refusal


def readers(snap: Snapshot, did: str = "d-1") -> set[NodeId]:
    return {node for node, _ in _NODES if did in {d.id for d in snap.decisions_readable_by(node)}}


# -- raising -------------------------------------------------------------------------


def test_a_channel_decision_is_filed_with_the_link_its_name_resolved_to() -> None:
    """
    The channel and link on the record handed in are not taken: the arm stamps
    the live link the intent's name resolves to.
    """
    forged = decision(channel="ops", link="k-forged")
    snap, effects = run(
        linked(), DecisionRaised(LEAD, forged, request_id="rq", at=3.0, channel="brand")
    )

    assert effects == (DecisionSettled(request_id="rq", refusal=None),)
    filed = snap.decisions["d-1"]
    assert (filed.channel, filed.link) == ("brand", "k1")


def test_a_decision_raised_on_no_channel_is_filed_with_none_whatever_it_was_handed() -> None:
    handed = decision(channel="brand", link="k1")
    snap, _ = run(linked(), DecisionRaised(LEAD, handed, request_id="rq"))

    assert (snap.decisions["d-1"].channel, snap.decisions["d-1"].link) == (None, None)


@pytest.mark.parametrize(
    "fields", [{"channel": "brand"}, {"link": "k1"}], ids=["no-link", "no-channel"]
)
def test_a_decision_names_its_channel_exactly_with_its_link(fields: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="names the link"):
        decision(**fields)


@pytest.mark.parametrize(
    ("by", "channel", "close", "refusal"),
    [
        (BUILDER, "brand", False, DecisionRefusal.NOT_A_LEAD),
        (C_LEAD, "brand", False, DecisionRefusal.NOT_ON_CHANNEL),
        (LEAD, "ops", False, DecisionRefusal.NOT_ON_CHANNEL),
        (LEAD, "brand", True, DecisionRefusal.NOT_ON_CHANNEL),
    ],
    ids=["sub-agent", "not-a-member", "no-such-name", "closed"],
)
def test_a_channel_decision_is_refused_off_a_live_link_of_the_askers_session(
    by: NodeId, channel: str, close: bool, refusal: DecisionRefusal
) -> None:
    snap = linked()
    if close:
        snap, _ = run(snap, ChannelClosed("k1", 2.0))
    snap, why = raised(snap, by=by, channel=channel)

    assert why is refusal
    assert snap.decisions == {}


# -- who reads it -----------------------------------------------------------------


def test_the_asker_and_the_peer_lead_read_it_and_nobody_else_does() -> None:
    snap, why = raised(linked())

    assert why is None
    # The peer's builder does not: a link joins leads.
    assert readers(snap) == {LEAD, Z_LEAD}


def test_the_peer_reads_the_answer_and_its_read_does_not_mark_it_delivered() -> None:
    snap, _ = raised(linked())
    snap, _ = run(snap, DecisionAnswered("d-1", "yes", at=4.0))
    snap, effects = run(
        snap, DecisionsRead(node_id=Z_LEAD, request_id="u", at=5.0, decision_id="d-1")
    )

    (delivered,) = effects
    assert [d.answer for d in delivered.decisions] == ["yes"]
    # Only the asker's own read hands it the answer.
    assert snap.decisions["d-1"].first_delivered_at is None


def test_closing_the_link_takes_it_from_the_peer_and_leaves_the_askers_sessions_rule() -> None:
    snap, _ = raised(linked())
    snap, _ = run(snap, ChannelClosed("k1", 4.0))

    assert readers(snap) == {LEAD}


def test_a_name_linked_again_does_not_carry_a_decision_from_the_closed_link() -> None:
    snap, _ = raised(linked())
    snap, _ = run(snap, ChannelClosed("k1", 4.0), link("k2", "brand", S, Z))

    assert readers(snap) == {LEAD}
    # One raised on the new link is read through it.
    snap, _ = raised(snap, did="d-2")
    assert snap.decisions["d-2"].link == "k2"
    assert readers(snap, "d-2") == {LEAD, Z_LEAD}


def test_a_lead_on_two_links_does_not_carry_a_decision_from_one_into_the_other() -> None:
    snap, _ = run(linked(), link("k3", "ops", S, C))
    snap, _ = raised(snap)

    assert C_LEAD not in readers(snap)


def test_a_decision_with_no_channel_is_read_exactly_as_before() -> None:
    snap, _ = raised(linked(), channel=None)
    worker, _ = raised(snap, by=BUILDER, did="d-2", channel=None)

    assert readers(worker, "d-1") == {LEAD}
    assert readers(worker, "d-2") == {LEAD, BUILDER}


def test_only_the_asker_withdraws_a_channel_decision() -> None:
    snap, _ = raised(linked())

    def withdrawn(by: NodeId) -> DecisionRefusal | None:
        _, effects = run(snap, DecisionWithdrawn(by, "d-1", request_id="w", at=4.0))
        (settled,) = effects
        assert isinstance(settled, DecisionSettled)
        return settled.refusal

    assert withdrawn(Z_LEAD) is DecisionRefusal.NOT_THE_ASKER
    assert withdrawn(LEAD) is None


# -- restored ------------------------------------------------------------------------


def _logged_board() -> boardlog.Board:
    """The asker's log as the reducer's facts wrote it, through the codec."""
    before = linked()
    intent = DecisionRaised(LEAD, decision(), request_id="rq", at=3.0, channel="brand")
    after, _ = _apply(before, intent, 5.0)
    facts = list(outcomes(before, intent, after, 5.0))
    assert [f.session_id for f in facts] == [S]
    records = [boardlog.decode(boardlog.encode(f.record)) for f in facts]
    assert all(isinstance(r, boardlog.Raised) for r in records)
    return boardlog.restore(r for r in records if isinstance(r, boardlog.Raised))


_K1 = Channel(id="k1", name="brand", members=frozenset({S, Z}), charter="charter", created_at=1.0)
_CLOCK = Clock(wall=5_000.0, monotonic=100.0)


def test_a_restored_channel_decision_is_read_by_the_peer_once_both_logs_are_restored() -> None:
    fresh = started()
    channels_only, _ = run(fresh, ChannelsRestored((_K1,)))
    asker_only, _ = run(fresh, BoardRestored(S, _logged_board(), _CLOCK))
    both, _ = run(channels_only, BoardRestored(S, _logged_board(), _CLOCK))

    assert channels_only.decisions == {}
    assert (asker_only.decisions["d-1"].link, readers(asker_only)) == ("k1", {LEAD})
    assert readers(both) == {LEAD, Z_LEAD}


def test_a_restored_decision_whose_link_restored_closed_is_the_askers_alone() -> None:
    closed = Channel(
        id="k1",
        name="brand",
        members=frozenset({S, Z}),
        charter="charter",
        created_at=1.0,
        closed_at=2.0,
    )
    snap, _ = run(started(), ChannelsRestored((closed,)), BoardRestored(S, _logged_board(), _CLOCK))

    assert readers(snap) == {LEAD}


def _restored_with(**fields: Any) -> Snapshot:
    """``_logged_board``'s decision with ``fields`` changed, restored beside k1."""
    (logged,) = _logged_board().decisions
    board = boardlog.Board(decisions=(dataclasses.replace(logged, **fields),))
    snap, _ = run(started(), ChannelsRestored((_K1,)), BoardRestored(S, board, _CLOCK))
    return snap


@pytest.mark.parametrize(
    "fields",
    [{"asker": BUILDER}, {"channel": "ops"}],
    ids=["a-sub-agents", "named-off-its-link"],
)
def test_a_tampered_channel_decision_is_not_restored(fields: dict[str, Any]) -> None:
    """
    RV6 2 and 4: no step files a sub-agent's channel decision, or one whose
    channel is not its link's name, so a restore places neither -- and the peer
    lead reads neither.
    """
    snap = _restored_with(**fields)

    assert snap.decisions == {}
    assert [n.why for n in snap.restores[S].unplaced] == [Unplaced.INCOHERENT]


@pytest.mark.parametrize(
    "fields",
    [{"asker": BUILDER}, {"channel": "ops"}],
    ids=["a-sub-agents", "named-off-its-link"],
)
def test_the_peer_never_reads_a_channel_decision_no_step_files(fields: dict[str, Any]) -> None:
    """The read asks the same of a record placed before its link was held."""
    snap, _ = run(linked())
    held = dataclasses.replace(decision(), **{"channel": "brand", "link": "k1", **fields})

    assert not snap.channel_decision_readable(held, Z)
    assert snap.channel_decision_readable(dataclasses.replace(held, **_PROPER), Z)


_PROPER: dict[str, Any] = {"asker": LEAD, "channel": "brand"}


def test_a_channel_decision_called_before_the_name_was_linked_again_is_refused() -> None:
    """
    RV5 1's race for a decision: the call was made while #brand joined S and
    Z; approved after the operator closed it and linked #brand as S and C, it
    would be read by C's lead. Refused, and nothing is filed.
    """
    snap = started()
    for intent, now in (
        (link("k1", "brand", S, Z), 1.0),
        (ChannelClosed("k1", 3.0), 3.0),
        (link("k3", "brand", S, C), 10.0),
    ):
        snap, _ = _apply(snap, intent, now)
    asked = DecisionRaised(LEAD, decision(), request_id="rq", at=11.0, channel="brand")

    early, refused = _apply(snap, dataclasses.replace(asked, called_at=5.0), 11.0)
    late, filed = _apply(snap, dataclasses.replace(asked, called_at=10.5), 11.0)

    assert refused == (DecisionSettled(request_id="rq", refusal=DecisionRefusal.LINKED_AFTER),)
    assert early.decisions == {}
    assert filed == (DecisionSettled(request_id="rq", refusal=None),)
    assert late.decisions["d-1"].link == "k3"


def test_a_channel_decision_refused_for_its_links_age_says_that_and_no_more() -> None:
    """
    RV5b 2: the link may be the first of its name, so the text says the link
    went live after the call and asserts no earlier link was closed.
    """
    from pptmstr.bus import _decision_refusal_text

    text = _decision_refusal_text(DecisionRefusal.LINKED_AFTER, "d-1", True)

    assert text.startswith("The live link of that name went live after you made this call")
    assert "closed" not in text and "linked again" not in text


def test_the_peer_lead_lists_its_own_sessions_work_before_the_other_leads() -> None:
    """
    RV6 5: in the peer lead's list, its own unread answer is kept ahead of the
    other lead's open decisions, so a list cut to fit drops the other lead's.
    """
    from pptmstr import bus as bus_mod
    from pptmstr.model import Answered

    theirs = [
        dataclasses.replace(
            decision(f"d-{n}", channel="brand", link="k1"), question="Q" * 300, raised_at=n
        )
        for n in range(200)
    ]
    mine = dataclasses.replace(
        decision("d-mine", asker=Z_LEAD, outcome=Answered("yes", 900.0)),
        question="MINE ANSWERED",
        raised_at=999.0,
    )

    listed = bus_mod._decisions_text([*theirs, mine], None, 1_000.0, Z_LEAD, lambda n: "lead")

    assert "did not fit" in listed
    assert "MINE ANSWERED" in listed


def test_a_withdrawal_by_the_other_lead_is_said_as_a_sentence() -> None:
    """RV6 6: "The lead on #brand withdrew it.", capitalised as a sentence."""
    store = _linked_store()
    with _live_bus() as bus:
        bus.call(store, "raise_decision", {**_RAISE, "channel": "brand"}, sender=LEAD)
        (filed,) = store.snapshot().decisions.values()
        store.apply(DecisionWithdrawn(LEAD, filed.id, request_id="w"))
        whole = bus.call(store, "read_decisions", {"decision_id": filed.id}, sender=Z_LEAD)
        listed = bus.call(store, "read_decisions", {}, sender=Z_LEAD)

    assert "The lead on #brand withdrew it." in whole
    assert "The lead on #brand withdrew it." in listed


# -- through the tools ----------------------------------------------------------------


def _linked_store() -> Store:
    store = Store()
    intents: tuple[Intent, ...] = (*_spawns(), link("k1", "brand", S, Z))
    for intent in intents:
        store.apply(intent)
    return store


_RAISE = {"question": "ship it?", "standing": "operators", "if_unanswered": "I ship it"}


def test_a_lead_raises_on_a_channel_and_the_peer_reads_it_through_the_tools() -> None:
    store = _linked_store()
    with _live_bus() as bus:
        said = bus.call(store, "raise_decision", {**_RAISE, "channel": "brand"}, sender=LEAD)
        listed = bus.call(store, "read_decisions", {}, sender=Z_LEAD)
        (filed,) = store.snapshot().decisions.values()
        whole = bus.call(store, "read_decisions", {"decision_id": filed.id}, sender=Z_LEAD)
        stranger = bus.call(store, "read_decisions", {"decision_id": filed.id}, sender=C_LEAD)

    assert (filed.channel, filed.link) == ("brand", "k1")
    assert "raised on #brand" in said
    assert "1 the lead on the other end of a channel raised" in listed
    assert "raised by the lead on #brand, not by you" in whole
    assert "the asker's session is woken and yours is not" in whole
    assert "No decision" in stranger and "is shown to you" in stranger
    assert "raise it again" not in stranger


@pytest.mark.parametrize(
    ("args", "sender", "said"),
    [
        ({"channel": "brand"}, BUILDER, "Only a session's lead raises a decision on a channel"),
        ({"channel": "brand"}, C_LEAD, "No open link of that name has your session"),
        ({"channel": "ops"}, LEAD, "No open link of that name has your session"),
        ({"channel": "Brand"}, LEAD, "not a name a channel can have"),
    ],
    ids=["sub-agent", "not-a-member", "no-such-name", "bad-name"],
)
def test_a_refused_channel_raise_says_so_and_files_nothing(
    args: dict[str, object], sender: NodeId, said: str
) -> None:
    store = _linked_store()
    with _live_bus() as bus:
        text = bus.call(store, "raise_decision", {**_RAISE, **args}, sender=sender)

    assert "NOT raised" in text and said in text
    assert store.snapshot().decisions == {}
