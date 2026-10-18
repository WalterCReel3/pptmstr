"""
A message between leads is a ``model.Concern`` with a channel, read through its
own inbox and filed under its own record kind.

Three pins, each catching what the others cannot. The kind pin sees what an older
build dispatches on and nothing of the store; the round trip sees the store and
nothing of the kind; the inbox split sees which tool a message reaches. The
fourth test is what the kind buys: a build that predates peer messages reads one
as a record it does not know, never as team mail.
"""

from __future__ import annotations

import dataclasses
import json
import random

import pytest

from pptmstr import boardlog
from pptmstr.boardfacts import outcomes
from pptmstr.boardlog import Clock, Delivered, PeerPosted, Unrecognised
from pptmstr.effects import InboxDelivered
from pptmstr.intents import AgentSpawned, BoardRestored, ConcernPosted, InboxRead, Intent
from pptmstr.model import Concern, ConcernState, NodeId, Snapshot
from pptmstr.store import _apply

S, Z = "s", "z"
LEAD: NodeId = (S, None)
BUILDER: NodeId = (S, "a1")
Z_LEAD: NodeId = (Z, None)

_WROTE = Clock(wall=10_000.0, monotonic=500.0)
_READ = Clock(wall=10_600.0, monotonic=40.0)
_SHIFT = (_READ.monotonic - _READ.wall) - (_WROTE.monotonic - _WROTE.wall)

# Every kind a build before peer messages decodes. A peer message filed under any
# of these is read by such a build as whatever that kind means there. Written out
# rather than read off ``boardlog._KINDS``, which ``peer-posted`` has joined: a
# set derived from it would let the pin pass with the kind in it.
PRE_PEER_KINDS = frozenset(
    {
        "task-declared",
        "task-claimed",
        "task-released",
        "task-reclaimed",
        "task-version-handed",
        "task-completed",
        "task-closed",
        "task-amended",
        "task-writes-accrued",
        "concern-posted",
        "concern-edited",
        "concern-withdrawn",
        "concerns-delivered",
        "recording-stopped",
    }
)


def started() -> Snapshot:
    snap = Snapshot.empty()
    for node, parent, agent_type in (
        (LEAD, None, None),
        (BUILDER, LEAD, "builder"),
        (Z_LEAD, None, None),
    ):
        spawned = AgentSpawned(
            node_id=node,
            parent=parent,
            task="do a thing",
            model="claude-opus-5",
            started_at=0.0,
            agent_type=agent_type,
            cwd="/w" if parent is None else None,
        )
        snap, _ = _apply(snap, spawned, 0.0)
    return snap


def peer(cid: str = "c-1", **fields: object) -> Concern:
    values: dict[str, object] = {
        "id": cid,
        "sender": LEAD,
        "recipient": Z_LEAD,
        "subject": "subject",
        "body": "body",
        "posted_at": 3.0,
        "channel": "brand",
        "needs_answer": True,
        "re": "c-0",
        "quiet": False,
        "task_id": "t-1",
        "incident": True,
    }
    values.update(fields)
    return Concern(**values)  # type: ignore[arg-type]


def team(cid: str, sender: NodeId, recipient: NodeId, at: float) -> Concern:
    return Concern(cid, sender, recipient, "subject", "body", posted_at=at, channel=None)


def apply(snap: Snapshot, *intents: Intent) -> tuple[Snapshot, list[boardlog.Record]]:
    records: list[boardlog.Record] = []
    for intent in intents:
        after, _ = _apply(snap, intent, 5.0)
        records.extend(f.on_the_wall(_WROTE) for f in outcomes(snap, intent, after, 5.0))
        snap = after
    return snap, records


# -- (a) the kind pin --------------------------------------------------------------


def test_a_concern_with_a_channel_is_filed_under_a_kind_no_older_build_knows() -> None:
    """
    On the encoded kind, which is what ``decode`` dispatches on, and not on the
    record's class: a widened ``Posted`` would pass a class check made loosely
    and be read by every older build as team mail.
    """
    _, records = apply(started(), ConcernPosted(LEAD, peer()))

    kinds = [json.loads(boardlog.encode(record))["kind"] for record in records]

    assert kinds == ["peer-posted"]
    assert kinds[0] not in PRE_PEER_KINDS


def test_a_concern_without_a_channel_is_still_filed_as_it_always_was() -> None:
    _, records = apply(started(), ConcernPosted(BUILDER, team("c-1", BUILDER, LEAD, 3.0)))

    assert [json.loads(boardlog.encode(record))["kind"] for record in records] == ["concern-posted"]


def test_the_frozen_list_is_still_decoded_and_never_held_peer_posted() -> None:
    """
    ``PRE_PEER_KINDS`` is the vocabulary of the builds that predate peer
    messages, and it is frozen: it is never extended. A kind added after it is
    unknown to those builds by definition, so the decoder may grow beyond it
    without this failing. What must hold is that every frozen kind is still
    decoded, and that ``peer-posted`` is one of the kinds beyond it.
    """
    assert PRE_PEER_KINDS <= set(boardlog._KINDS)
    assert "peer-posted" in set(boardlog._KINDS) - PRE_PEER_KINDS


# -- (b) the end-to-end round trip -------------------------------------------------


def test_a_peer_message_restored_into_another_process_keeps_every_field() -> None:
    """
    ``ConcernPosted``, the records it yields, their text, read back, folded and
    handed to an empty store. One message waiting and one already handed over,
    so the delivery record takes the same path for a peer as for a concern.
    """
    waiting = peer("c-1")
    handed = peer(
        "c-2",
        posted_at=3.5,
        state=ConcernState.DELIVERED,
        delivered_at=4.0,
        needs_answer=False,
        re=None,
        quiet=True,
        channel="ops",
        incident=False,
    )
    first, records = apply(started(), ConcernPosted(LEAD, waiting), ConcernPosted(LEAD, handed))
    assert [type(r) for r in records] == [PeerPosted, PeerPosted, Delivered]

    read = [boardlog.decode(boardlog.encode(record)) for record in records]
    assert not any(isinstance(r, boardlog.Malformed) for r in read)
    board = boardlog.restore(r for r in read if not isinstance(r, boardlog.Malformed))
    assert not board.short

    snap, effects = _apply(Snapshot.empty(), BoardRestored(S, board, _READ), _READ.monotonic)

    assert effects == ()
    report = snap.restores[S]
    assert report.unplaced == () and not report.short and report.concerns == 2
    for cid in ("c-1", "c-2"):
        held = first.concerns[cid]
        restored = snap.concerns[cid]
        assert restored == dataclasses.replace(
            held,
            posted_at=held.posted_at + _SHIFT,
            delivered_at=None if held.delivered_at is None else held.delivered_at + _SHIFT,
        )
    # Spelled out, so a field that both sides lost to its default cannot pass.
    one, two = snap.concerns["c-1"], snap.concerns["c-2"]
    assert (one.channel, one.needs_answer, one.re, one.quiet) == ("brand", True, "c-0", False)
    assert (two.channel, two.needs_answer, two.re, two.quiet) == ("ops", False, None, True)
    assert (one.state, two.state) == (ConcernState.POSTED, ConcernState.DELIVERED)
    assert snap.peer_inbox_of(Z_LEAD) == (one,)
    assert snap.inbox_of(Z_LEAD) == ()


# -- (c) the inbox split -----------------------------------------------------------


def _mailbag(seed: int) -> Snapshot:
    """Team and peer mail to every node, in every state, posted in no order."""
    rng = random.Random(seed)
    nodes = (LEAD, BUILDER, Z_LEAD)
    snap = started()
    for n in range(30):
        state = rng.choice((ConcernState.POSTED,) * 3 + (ConcernState.DELIVERED,))
        concern = Concern(
            f"c-{n}",
            rng.choice(nodes),
            rng.choice(nodes),
            "subject",
            "body",
            posted_at=rng.uniform(0.0, 10.0),
            state=state,
            delivered_at=1.0 if state is ConcernState.DELIVERED else None,
            channel=rng.choice((None, "brand", "ops")),
        )
        snap, _ = _apply(snap, ConcernPosted(concern.sender, concern), 5.0)
    return snap


@pytest.mark.parametrize("seed", range(20))
def test_the_two_inboxes_split_the_waiting_mail_with_nothing_in_both(seed: int) -> None:
    snap = _mailbag(seed)

    for node in (LEAD, BUILDER, Z_LEAD):
        mine = snap.inbox_of(node)
        peers = snap.peer_inbox_of(node)
        waiting = {
            c.id
            for c in snap.concerns.values()
            if c.recipient == node and c.state is ConcernState.POSTED
        }
        assert {c.id for c in mine} & {c.id for c in peers} == set()
        assert {c.id for c in mine} | {c.id for c in peers} == waiting
        assert all(c.channel is None for c in mine)
        assert all(c.channel is not None for c in peers)
        for box in (mine, peers):
            assert [c.posted_at for c in box] == sorted(c.posted_at for c in box)
        by_channel = [c.id for name in ("brand", "ops") for c in snap.peer_inbox_of(node, name)]
        assert sorted(by_channel) == sorted(c.id for c in peers)


def test_a_team_read_hands_over_no_peer_message_and_files_no_delivery_for_one() -> None:
    """
    ``read_inbox`` is the team's tool. A peer message waiting beside a concern
    is neither handed over by it nor marked delivered in the store or the log.
    """
    snap, _ = apply(
        started(),
        ConcernPosted(LEAD, peer("c-1", recipient=BUILDER)),
        ConcernPosted(LEAD, team("c-2", LEAD, BUILDER, 4.0)),
    )

    after, effects = _apply(snap, InboxRead(BUILDER, request_id="r1", at=6.0), 6.0)
    facts = outcomes(snap, InboxRead(BUILDER, request_id="r1", at=6.0), after, 6.0)

    (answer,) = effects
    assert isinstance(answer, InboxDelivered)
    assert [c.id for c in answer.concerns] == ["c-2"]
    assert after.concerns["c-1"].state is ConcernState.POSTED
    assert [f.record for f in facts] == [Delivered(6.0, ("c-2",))]


# -- (d) a build that predates peer messages -----------------------------------------


def test_a_build_that_predates_peer_messages_reads_one_as_unrecognised_and_short(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    This build with ``peer-posted`` taken out of the decoder stands in for an
    older one. The message is not placed, the board says it is short, and the
    delivery that names it is stray rather than marking anything.
    """
    _, records = apply(
        started(),
        ConcernPosted(LEAD, peer("c-1", state=ConcernState.DELIVERED, delivered_at=4.0)),
    )
    texts = [boardlog.encode(record) for record in records]
    older = {kind: cls for kind, cls in boardlog._KINDS.items() if kind != "peer-posted"}
    assert len(older) == len(boardlog._KINDS) - 1
    monkeypatch.setattr(boardlog, "_KINDS", older)

    read = [boardlog.decode(text) for text in texts]

    assert [type(r) for r in read] == [Unrecognised, Delivered]
    assert isinstance(read[0], Unrecognised) and read[0].kind == "peer-posted"
    board = boardlog.restore(r for r in read if not isinstance(r, boardlog.Malformed))
    assert board.short
    assert board.concerns == ()
    assert [r.kind for r in board.unrecognised] == ["peer-posted"]
    assert board.stray == (read[1],)

    snap, _ = _apply(Snapshot.empty(), BoardRestored(S, board, _READ), _READ.monotonic)
    assert snap.concerns == {}
    assert snap.restores[S].short
