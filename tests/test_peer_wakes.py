"""
Decided 10: a message between linked leads wakes the lead it reaches, so that no
message waits on a lead remembering to look -- coalesced per link, never for a
``quiet`` message, held and owed to the operator once a link has woken its
leads ``PEER_WAKE_CHAIN`` times in a row with nothing moving, and at a resume
only once the resumed session's restore is in the store.

The reducer is driven directly for what the store decides (``store._peer_waking``),
and the shell for what it sends (``app._send_wakes``).
"""

from __future__ import annotations

from typing import Any

import pytest

from pptmstr import app, boardlog, bus
from pptmstr.boardlog import Clock, PeerPosted
from pptmstr.bridge import Bridge
from pptmstr.effects import SessionWoken, WakeReason
from pptmstr.intents import (
    AgentFinished,
    AgentSpawned,
    ApprovalRequested,
    ApprovalResolved,
    BoardRestored,
    ChannelClosed,
    ChannelLinked,
    ChannelsRestored,
    ConcernEdited,
    ConcernPosted,
    ConcernWithdrawn,
    DecisionAnswered,
    DecisionDismissed,
    DecisionRaised,
    DecisionWakeSent,
    DecisionWithdrawn,
    Intent,
    PeerInboxRead,
    PeerSent,
    PeerWakeSent,
    SessionSettled,
    TaskClaimRequested,
    TaskCompleted,
    TaskDeclared,
    WakeChainReleased,
)
from pptmstr.model import (
    EVERY_CHANNEL,
    PEER_WAKE_CHAIN,
    AgentState,
    Basis,
    Concern,
    Decision,
    NodeId,
    ObligationKind,
    PeerWakes,
    PendingApproval,
    Provenance,
    Recalled,
    Snapshot,
    Standing,
    Task,
    WakeChainHeld,
)
from pptmstr.settings import Settings
from pptmstr.store import Store, _apply, _concern_wake

S, Z, C = "s", "z", "c"
LEAD: NodeId = (S, None)
BUILDER: NodeId = (S, "a1")
Z_LEAD: NodeId = (Z, None)
C_LEAD: NodeId = (C, None)


def _spawned(node: NodeId, parent: NodeId | None) -> AgentSpawned:
    return AgentSpawned(
        node_id=node,
        parent=parent,
        task="do a thing",
        model="claude-opus-5",
        started_at=0.0,
        agent_type=None if parent is None else "builder",
        cwd="/w" if parent is None else None,
    )


def linked() -> Snapshot:
    """Three running sessions, ``s`` with a builder, and ``s`` and ``z`` linked as k1."""
    snap = Snapshot.empty()
    for node, parent in ((LEAD, None), (BUILDER, LEAD), (Z_LEAD, None), (C_LEAD, None)):
        snap, _ = _apply(snap, _spawned(node, parent), 0.0)
    snap, _ = _apply(
        snap, ChannelLinked("k1", "brand", frozenset({S, Z}), "c", 1.0, frozenset({S, Z})), 1.0
    )
    return snap


_ids = iter(f"c-{n:012x}" for n in range(1, 10**6))


def sent(by: NodeId = LEAD, on: str = "brand", **fields: object) -> PeerSent:
    return PeerSent(by, "rq", next(_ids), on, "the subject", "the body text", 3.0, **fields)  # type: ignore[arg-type]


def read(by: NodeId = Z_LEAD, on: str | Any = EVERY_CHANNEL) -> PeerInboxRead:
    return PeerInboxRead(by, "rr", 4.0, on)


def step(snap: Snapshot, intent: Intent, now: float = 5.0) -> tuple[Snapshot, list[SessionWoken]]:
    after, effects = _apply(snap, intent, now)
    return after, [e for e in effects if isinstance(e, SessionWoken)]


def wake(session: str, link: str = "k1") -> SessionWoken:
    return SessionWoken(session_id=session, reason=WakeReason.PEER_MESSAGE, subject_id=link)


def held(snap: Snapshot) -> list[WakeChainHeld]:
    return [o for o in snap.needs_you if isinstance(o, WakeChainHeld)]


# -- 1. a message that is not quiet wakes the lead it reaches ---------------------


def test_a_message_wakes_the_lead_it_reaches_and_only_that_lead() -> None:
    snap, woken = step(linked(), sent())

    assert woken == [wake(Z)]
    assert snap.peer_wakes["k1"] == PeerWakes(chain=1, pending=frozenset({Z}))


def test_the_wake_line_counts_what_waits_and_carries_no_text_of_it() -> None:
    snap, (woken,) = step(linked(), sent(needs_answer=True))

    line = bus.wake_line(snap, woken)

    assert line == '1 unread on #brand; call read_peer_messages(channel="brand"), which returns it.'
    message = bus.wake_message(snap, [woken])
    assert message.startswith(bus.WAKE_HEADING)
    assert "the subject" not in message and "the body text" not in message


def test_a_refused_send_wakes_nobody() -> None:
    _, woken = step(linked(), sent(on="nowhere"))
    assert woken == []


# -- 2. coalesced per link, cleared by a read ---------------------------------------


def test_a_burst_on_one_link_is_one_wake_and_its_line_counts_the_burst() -> None:
    snap, first = step(linked(), sent())
    snap, second = step(snap, sent())
    snap, third = step(snap, sent())

    assert (first, second, third) == ([wake(Z)], [], [])
    assert snap.peer_wakes["k1"].chain == 1
    assert bus.wake_line(snap, first[0]).startswith("3 unread on #brand")


def test_a_read_clears_the_pending_wake_and_the_next_message_wakes_again() -> None:
    snap, _ = step(linked(), sent())
    snap, _ = step(snap, read())

    assert Z not in snap.peer_wakes["k1"].pending
    _, woken = step(snap, sent())
    assert woken == [wake(Z)]


def test_a_read_of_another_channel_leaves_this_ones_wake_pending() -> None:
    snap, _ = step(
        linked(), ChannelLinked("k2", "ops", frozenset({S, Z}), "c", 1.0, frozenset({S, Z}))
    )
    snap, _ = step(snap, sent(on="brand"))
    snap, _ = step(snap, read(on="ops"))

    assert snap.peer_wakes["k1"].pending == {Z}
    _, woken = step(snap, sent(on="brand"))
    assert woken == []


def test_two_links_wake_apart() -> None:
    snap, _ = step(
        linked(), ChannelLinked("k2", "ops", frozenset({S, Z}), "c", 1.0, frozenset({S, Z}))
    )
    snap, a = step(snap, sent(on="brand"))
    _, b = step(snap, sent(on="ops"))
    assert (a, b) == ([wake(Z, "k1")], [wake(Z, "k2")])


# -- 3. quiet ----------------------------------------------------------------------


def test_a_quiet_message_wakes_nobody_and_is_counted_with_the_next_wake() -> None:
    snap, quiet = step(linked(), sent(quiet=True))
    assert quiet == [] and "k1" not in snap.peer_wakes

    snap, (woken,) = step(snap, sent())
    assert bus.wake_line(snap, woken).startswith("2 unread on #brand")


def test_a_quiet_message_orders_no_wake_even_with_louder_mail_waiting() -> None:
    """
    The send's own flag is what is asked: mail left from before a lead was
    woken does not make a quiet message wake it.
    """
    snap, _ = step(_ended(linked(), Z), sent())
    snap, _ = step(snap, _spawned(Z_LEAD, None))
    _, woken = step(snap, sent(quiet=True))
    assert woken == []


def test_quiet_mail_alone_wakes_nobody_at_a_resume() -> None:
    snap, _ = step(linked(), sent(quiet=True))
    _, woken = step(snap, SessionSettled(Z))
    assert woken == []


# -- 4. the backstop ---------------------------------------------------------------


def ping_pong(snap: Snapshot, times: int) -> tuple[Snapshot, list[SessionWoken]]:
    """Each lead reads what it was woken for and answers, ``times`` messages in all."""
    every: list[SessionWoken] = []
    leads = (LEAD, Z_LEAD)
    for n in range(times):
        sender, reader = leads[n % 2], leads[(n + 1) % 2]
        snap, woken = step(snap, sent(by=sender))
        every += woken
        snap, _ = step(snap, read(by=reader))
    return snap, every


def test_two_leads_answering_each_other_are_woken_k_times_and_then_held() -> None:
    snap, woken = ping_pong(linked(), PEER_WAKE_CHAIN)
    assert len(woken) == PEER_WAKE_CHAIN
    assert held(snap) == []

    snap, more = step(snap, sent(by=LEAD if PEER_WAKE_CHAIN % 2 == 0 else Z_LEAD))

    assert more == []
    (row,) = held(snap)
    holder = Z if PEER_WAKE_CHAIN % 2 == 0 else S
    assert (row.node, row.link, row.channel, row.kind) == (
        (holder, None),
        "k1",
        "brand",
        ObligationKind.WAKE_CHAIN,
    )
    assert row.since == 5.0
    # A further message adds no wake and no second row.
    snap, again = step(snap, sent(by=LEAD if holder == Z else Z_LEAD))
    assert again == [] and len(held(snap)) == 1


def test_a_held_chain_holds_the_other_lead_too() -> None:
    snap, _ = ping_pong(linked(), PEER_WAKE_CHAIN + 1)
    snap, _ = step(snap, sent(by=Z_LEAD))
    snap, _ = step(snap, sent(by=LEAD))
    assert {o.node for o in held(snap)} == {LEAD, Z_LEAD}


def _chained() -> Snapshot:
    """The chain at its bound, and ``z``'s next wake held."""
    snap, _ = ping_pong(linked(), PEER_WAKE_CHAIN)
    if PEER_WAKE_CHAIN % 2:
        snap, _ = step(snap, sent(by=LEAD))
        snap, _ = step(snap, read(by=Z_LEAD))
    snap, _ = step(snap, sent(by=LEAD))
    assert [o.node for o in held(snap)] == [Z_LEAD]
    return snap


def test_the_operator_releasing_it_wakes_the_held_lead_and_starts_the_count_again() -> None:
    snap, woken = step(_chained(), WakeChainReleased("k1"))

    assert woken == [wake(Z)]
    assert held(snap) == []
    assert snap.peer_wakes["k1"].chain == 1


def test_a_release_of_a_link_with_nothing_held_changes_nothing() -> None:
    snap, _ = step(linked(), sent())
    after, woken = step(snap, WakeChainReleased("k1"))
    assert woken == [] and after.peer_wakes == snap.peer_wakes


def _asked(task_id: str, on: str = "brand", re: str | None = None) -> Task:
    return Task(id=task_id, title="asked for", provenance=Provenance(on, re=re))


def _chained_with(*board: Intent) -> Snapshot:
    """``_chained``, with ``board`` applied to the linked snapshot first."""
    snap = linked()
    for intent in board:
        snap, _ = step(snap, intent)
    snap, _ = ping_pong(snap, PEER_WAKE_CHAIN)
    if PEER_WAKE_CHAIN % 2:
        snap, _ = step(snap, sent(by=LEAD))
        snap, _ = step(snap, read(by=Z_LEAD))
    snap, _ = step(snap, sent(by=LEAD))
    assert [o.node for o in held(snap)] == [Z_LEAD]
    return snap


def _done(task_id: str) -> TaskCompleted:
    return TaskCompleted(BUILDER, task_id, 6.0, result="done", basis=Basis.RUN)


@pytest.mark.parametrize("re", [None, "held", "c-0000deadbeef"], ids=["no-re", "re", "unheld-re"])
def test_work_the_link_asked_for_completed_does_not_start_the_count_again(
    re: str | None,
) -> None:
    """
    RV-W12 2, the lead's ruling: only the operator's acts reset the chain. A
    task naming the link, whatever it cites, is something one lead can
    declare, claim and complete with nobody else involved.
    """
    asked = sent(by=Z_LEAD)
    snap = _chained_with(
        asked,
        read(by=LEAD),
        TaskDeclared(_asked("t-asked", re=asked.concern_id if re == "held" else re), node_id=LEAD),
        TaskClaimRequested(BUILDER, "rq", "t-asked"),
    )

    snap, woken = step(snap, _done("t-asked"))

    assert snap.tasks["t-asked"].state.name == "COMPLETED"
    assert woken == [] and len(held(snap)) == 1


def test_a_lead_completing_a_task_for_the_link_every_round_is_still_held_at_k() -> None:
    """
    RV-W12's F2 run: two leads ping-pong, and each round one declares, claims
    and completes a task naming the link. The chain is held at the bound all
    the same.
    """
    snap = linked()
    leads = (LEAD, Z_LEAD)
    woken: list[SessionWoken] = []
    for n in range(PEER_WAKE_CHAIN):
        sender, reader = leads[n % 2], leads[(n + 1) % 2]
        snap, more = step(snap, sent(by=sender))
        woken += more
        snap, _ = step(snap, read(by=reader))
        snap, _ = step(snap, TaskDeclared(_asked(f"t-{n}"), node_id=LEAD))
        snap, _ = step(snap, TaskClaimRequested(BUILDER, f"rq{n}", f"t-{n}"))
        snap, _ = step(snap, _done(f"t-{n}"))
        assert snap.tasks[f"t-{n}"].state.name == "COMPLETED"

    snap, more = step(snap, sent(by=leads[PEER_WAKE_CHAIN % 2]))

    assert len(woken) == PEER_WAKE_CHAIN and more == []
    assert len(held(snap)) == 1


_OTHER = TaskDeclared(Task(id="t-other", title="unasked"), node_id=LEAD)
_CLAIM_OTHER = TaskClaimRequested(BUILDER, "rq0", "t-other")


@pytest.mark.parametrize(
    ("before", "act"),
    [
        ((), TaskDeclared(_asked("t-new"), node_id=LEAD)),
        ((), TaskDeclared(Task(id="t-new", title="real work"), node_id=Z_LEAD)),
        ((_OTHER,), _CLAIM_OTHER),
        ((_OTHER, _CLAIM_OTHER), _done("t-other")),
    ],
    ids=["declared-for-the-link", "declared-unasked", "claimed", "completed-unasked"],
)
def test_board_activity_that_is_not_the_links_work_done_does_not(
    before: tuple[Intent, ...], act: Intent
) -> None:
    """
    The lead's ruling on RV-W1 4: either team being busy, or a loop that
    declares a task every round, would otherwise mask the loop forever.
    """
    snap, woken = step(_chained_with(*before), act)

    assert woken == [] and len(held(snap)) == 1


def test_work_asked_on_another_channel_or_link_does_not() -> None:
    other = sent(by=Z_LEAD, on="ops")
    snap = _chained_with(
        ChannelLinked("k2", "ops", frozenset({S, Z}), "c", 1.0, frozenset({S, Z})),
        other,
        TaskDeclared(_asked("t-ops", on="ops"), node_id=LEAD),
        TaskClaimRequested(BUILDER, "rq1", "t-ops"),
        # Named for #brand, but answering a message that came over #ops.
        TaskDeclared(_asked("t-mixed", re=other.concern_id), node_id=LEAD),
        TaskClaimRequested(BUILDER, "rq2", "t-mixed"),
    )

    snap, on_ops = step(snap, _done("t-ops"))
    snap, mixed = step(snap, _done("t-mixed"))

    assert on_ops == [] and mixed == [] and len(held(snap)) == 1


def test_work_named_for_the_channel_on_a_board_off_the_link_does_not() -> None:
    c_builder: NodeId = (C, "a9")
    snap = _chained_with(
        _spawned(c_builder, C_LEAD),
        TaskDeclared(_asked("t-c"), node_id=C_LEAD),
        TaskClaimRequested(c_builder, "rq", "t-c"),
    )
    done = TaskCompleted(c_builder, "t-c", 6.0, result="done", basis=Basis.RUN)
    snap, woken = step(snap, done)
    assert snap.tasks["t-c"].state.name == "COMPLETED"
    assert woken == [] and len(held(snap)) == 1


def test_a_refused_board_call_is_not_activity() -> None:
    snap, _ = step(linked(), TaskDeclared(Task(id="t1", title="x"), node_id=LEAD))
    snap, _ = ping_pong(snap, PEER_WAKE_CHAIN)
    if PEER_WAKE_CHAIN % 2:
        snap, _ = step(snap, sent(by=LEAD))
        snap, _ = step(snap, read(by=Z_LEAD))
    snap, _ = step(snap, sent(by=LEAD))
    # The same id again is refused, and changes no task.
    snap, woken = step(snap, TaskDeclared(Task(id="t1", title="x"), node_id=LEAD))
    assert woken == [] and len(held(snap)) == 1


def test_the_operator_approving_a_send_on_the_link_starts_the_count_again() -> None:
    snap = _chained()
    parked = PendingApproval(
        id="p1",
        node=LEAD,
        tool_name="mcp__pptmstr__send_to_peer",
        tool_use_id="tu1",
        raw_args={"channel": "brand", "subject": "s", "body": "b"},
        summary="send",
        requested_at=6.0,
    )
    snap, _ = step(snap, ApprovalRequested(LEAD, parked))
    snap, woken = step(snap, ApprovalResolved(LEAD, "p1", approved=True))

    assert woken == [wake(Z)]
    assert held(snap) == []


def test_the_operator_approving_anything_else_does_not() -> None:
    snap = _chained()
    parked = PendingApproval(
        id="p1",
        node=BUILDER,
        tool_name="Write",
        tool_use_id="tu1",
        raw_args={"file_path": "/w/x"},
        summary="write",
        requested_at=6.0,
    )
    snap, _ = step(snap, ApprovalRequested(BUILDER, parked))
    snap, woken = step(snap, ApprovalResolved(BUILDER, "p1", approved=True))
    assert woken == [] and len(held(snap)) == 1


def _waiting(snap: Snapshot) -> str:
    """The id of the newest message waiting unread for ``z`` on k1."""
    return snap.peer_unread(Z, snap.channels["k1"])[-1].id


@pytest.mark.parametrize(
    "act",
    [
        lambda cid: ConcernEdited(cid, body="the operator's rewrite"),
        lambda cid: ConcernWithdrawn(cid),
    ],
    ids=["edited", "withdrawn"],
)
def test_the_operator_changing_a_message_on_the_link_starts_the_count_again(act: Any) -> None:
    snap = _chained()
    snap, _ = step(snap, act(_waiting(snap)))
    assert held(snap) == [] and snap.peer_wakes.get("k1", PeerWakes()).chain <= 1


@pytest.mark.parametrize(
    "act",
    [
        lambda cid: ConcernEdited(cid, body="the body text"),
        lambda cid: ConcernEdited(cid, body="an agent's rewrite", node_id=Z_LEAD),
        lambda cid: ConcernWithdrawn(cid, node_id=Z_LEAD),
        lambda cid: ConcernWithdrawn(cid, node_id=LEAD),
    ],
    ids=["same-body", "agent-edit", "recipient-withdrawal", "sender-withdrawal"],
)
def test_an_edit_or_withdrawal_that_is_no_operator_change_does_not(act: Any) -> None:
    """
    RV-W12 4: only the operator's (no node), and only one that changed the
    message. The store still applies an agent's; whether it should is not this
    rule's question.
    """
    snap = _chained()
    snap, woken = step(snap, act(_waiting(snap)))
    assert woken == [] and len(held(snap)) == 1


@pytest.mark.parametrize(
    "act",
    [lambda cid: ConcernEdited(cid, body="too late"), lambda cid: ConcernWithdrawn(cid)],
    ids=["edited", "withdrawn"],
)
def test_the_operator_editing_a_message_already_read_does_not(act: Any) -> None:
    message = sent(by=LEAD)
    snap = _chained_with(message, read(by=Z_LEAD))
    assert snap.concerns[message.concern_id].state.name == "DELIVERED"

    snap, woken = step(snap, act(message.concern_id))

    assert woken == [] and len(held(snap)) == 1


def test_the_held_lead_reading_anyway_clears_its_row() -> None:
    snap, _ = step(_chained(), read(by=Z_LEAD))
    assert held(snap) == []
    # The chain is not reset by a read: the next wake on the link is held again.
    snap, woken = step(snap, sent(by=LEAD))
    assert woken == [] and len(held(snap)) == 1


def test_closing_the_link_lets_go_of_everything_it_held() -> None:
    snap, _ = step(_chained(), ChannelClosed("k1", 9.0))
    assert held(snap) == [] and "k1" not in snap.peer_wakes


# -- 5. at resume ------------------------------------------------------------------


def _ended(snap: Snapshot, session: str) -> Snapshot:
    snap, _ = step(snap, AgentFinished((session, None), AgentState.DONE, 4.0))
    return snap


def test_a_lead_not_running_is_not_woken_and_nothing_is_left_pending() -> None:
    snap, woken = step(_ended(linked(), Z), sent())
    assert woken == [] and "k1" not in snap.peer_wakes


def test_a_lead_that_ends_with_a_wake_pending_lets_it_go() -> None:
    snap, _ = step(linked(), sent())
    snap = _ended(snap, Z)
    assert snap.peer_wakes["k1"].pending == frozenset()


def test_at_resume_the_lead_is_woken_after_its_restore_and_not_at_its_announce() -> None:
    """
    The shell's order is the announce, the restore, then ``SessionSettled``
    (``app._launch``): only the last wakes.
    """
    snap, _ = step(_ended(linked(), Z), sent())

    snap, at_announce = step(snap, _spawned(Z_LEAD, None))
    empty = boardlog.restore([])
    snap, at_restore = step(snap, BoardRestored(Z, empty, Clock(10_000.0, 6.0)))
    snap, at_settle = step(snap, SessionSettled(Z))

    assert (at_announce, at_restore, at_settle) == ([], [], [wake(Z)])


def test_a_settle_drops_a_wake_left_pending_by_an_earlier_run() -> None:
    snap, _ = step(linked(), sent())
    assert snap.peer_wakes["k1"].pending == {Z}
    _, woken = step(snap, SessionSettled(Z))
    assert woken == [wake(Z)]


def test_a_senders_restore_wakes_the_running_peer_it_brings_mail_back_for() -> None:
    record = PeerPosted(
        at=10_000.0,
        concern_id="c-0123456789ab",
        sender="lead",
        recipient="lead",
        subject="s",
        body="b",
        channel="brand",
        link="k1",
        sender_id=(S,),
        recipient_id=(Z,),
    )
    board = boardlog.restore([record])

    snap, woken = step(linked(), BoardRestored(S, board, Clock(10_600.0, 40.0)))

    assert "c-0123456789ab" in snap.concerns
    assert woken == [wake(Z)]


# -- 6. the team rule is unchanged --------------------------------------------------


def _concern(sender: NodeId, recipient: NodeId) -> Concern:
    return Concern(
        id="c-team",
        sender=sender,
        recipient=recipient,
        subject="s",
        body="b",
        posted_at=2.0,
        channel=None,
    )


def test_the_team_rule_still_wakes_only_a_workers_concern_to_its_own_lead() -> None:
    assert _concern_wake(_concern(BUILDER, LEAD)) != ()
    assert _concern_wake(_concern(LEAD, BUILDER)) == ()
    assert _concern_wake(_concern(LEAD, Z_LEAD)) == ()
    snap, woken = step(linked(), ConcernPosted(BUILDER, _concern(BUILDER, LEAD)))
    assert (
        woken == [SessionWoken(S, WakeReason.CONCERN_TO_LEAD, "c-team", sender=BUILDER)]
        and "k1" not in snap.peer_wakes
    )


# -- the shell ---------------------------------------------------------------------


class _Pool:
    def __init__(self) -> None:
        self.woken: list[tuple[NodeId, str]] = []

    async def wake(self, node_id: NodeId, text: str) -> bool:
        self.woken.append((node_id, text))
        return True


async def _nothing() -> None:
    return None


@pytest.fixture
def shell() -> Any:
    bridge = Bridge()
    bridge.start()
    store = Store()
    store.apply(_spawned(LEAD, None))
    store.apply(_spawned(Z_LEAD, None))
    store.apply(ChannelLinked("k1", "brand", frozenset({S, Z}), "c", 1.0, frozenset({S, Z})))
    state = app.AppState(store=store, bridge=bridge, settings=Settings())
    pool = _Pool()
    state.pool = pool  # type: ignore[assignment]

    def frame(at: float, *intents: Any) -> list[tuple[NodeId, str]]:
        state.frame_now = at
        for intent in intents:
            bridge.emit(intent)
        app.apply_intents(state)
        bridge.submit(_nothing()).result(timeout=5)
        return pool.woken

    try:
        yield state, frame
    finally:
        bridge.stop()


def _running(session: str) -> Any:
    from pptmstr.intents import StateChanged

    return StateChanged((session, None), AgentState.AWAITING_INPUT)


def test_the_shell_waits_for_a_starting_session_and_then_sends_one_line(shell: Any) -> None:
    state, frame = shell
    frame(100.0, sent(), sent())
    assert frame(100.0 + app.WAKE_WINDOW_S) == []
    assert Z in state.wakes

    (sent_to,) = frame(200.0, _running(Z))

    root, text = sent_to
    assert root == Z_LEAD
    assert text.startswith(bus.WAKE_HEADING)
    assert '2 unread on #brand; call read_peer_messages(channel="brand")' in text


def test_the_shell_drops_a_peer_wake_whose_mail_was_read_before_it_went(shell: Any) -> None:
    state, frame = shell
    frame(99.0, _running(Z))
    frame(100.0, sent())
    frame(100.5, read())
    assert frame(100.0 + app.WAKE_WINDOW_S) == []
    assert state.wakes == {} and state.wake_due == {}


# -- RV-W1 1: a withdrawn message leaves nobody pending ------------------------------


def test_withdrawing_the_last_unread_message_lets_its_pending_wake_go() -> None:
    message = sent()
    snap, _ = step(linked(), message)
    assert snap.peer_wakes["k1"].pending == {Z}

    snap, _ = step(snap, ConcernWithdrawn(message.concern_id))

    assert Z not in snap.peer_wakes.get("k1", PeerWakes()).pending
    _, woken = step(snap, sent())
    assert woken == [wake(Z)]


def test_withdrawing_one_of_two_unread_messages_keeps_the_wake_pending() -> None:
    first = sent()
    snap, _ = step(linked(), first)
    snap, _ = step(snap, sent())
    snap, _ = step(snap, ConcernWithdrawn(first.concern_id))
    assert snap.peer_wakes["k1"].pending == {Z}


def test_a_message_withdrawn_while_its_lead_starts_does_not_stall_the_link(shell: Any) -> None:
    """
    The reviewer's sequence: the wake is ordered while ``z`` is SPAWNING, the
    operator withdraws the message, ``z`` leaves SPAWNING and the shell drops
    the wake as having nothing to read. The next message must still wake it.
    """
    state, frame = shell
    message = sent()
    frame(100.0, message)
    frame(100.5, ConcernWithdrawn(message.concern_id))
    assert frame(200.0, _running(Z)) == []
    assert state.wakes == {}

    frame(300.0, sent())
    (woken,) = frame(300.0 + app.WAKE_WINDOW_S)
    assert woken[0] == Z_LEAD and "1 unread on #brand" in woken[1]


# -- the lead's ruling on RV-W1 5: pending ends when the shell sends the wake -------


def _spent(snap: Snapshot, session: str = Z, link: str = "k1") -> PeerWakeSent:
    """The shell's word that it sent the wake, counting what is unread now."""
    covered = frozenset(c.id for c in snap.peer_unread(session, snap.channels[link]))
    return PeerWakeSent(session, link, covered)


def test_a_lead_woken_that_does_not_read_is_woken_by_the_next_message() -> None:
    snap, first = step(linked(), sent())
    snap, _ = step(snap, _spent(snap))

    assert Z not in snap.peer_wakes["k1"].pending
    _, again = step(snap, sent())
    assert (first, again) == ([wake(Z)], [wake(Z)])


def test_a_burst_before_the_send_is_still_one_wake() -> None:
    snap, first = step(linked(), sent())
    snap, second = step(snap, sent())
    snap, woken_at_send = step(snap, _spent(snap))

    assert (first, second, woken_at_send) == ([wake(Z)], [], [])
    assert snap.peer_wakes["k1"].chain == 1


def test_a_message_filed_between_the_send_and_its_word_is_woken_for() -> None:
    """
    The shell counts the line from one snapshot and says so on the next
    frame; a message applied in between was coalesced into a wake that did
    not count it, and is woken for when the word arrives.
    """
    snap, _ = step(linked(), sent())
    said = _spent(snap)
    snap, between = step(snap, sent())

    snap, woken = step(snap, said)

    assert between == [] and woken == [wake(Z)]
    assert snap.peer_wakes["k1"].pending == {Z}


def test_a_quiet_message_between_the_send_and_its_word_is_not() -> None:
    snap, _ = step(linked(), sent())
    said = _spent(snap)
    snap, _ = step(snap, sent(quiet=True))
    _, woken = step(snap, said)
    assert woken == []


def test_a_wake_dropped_unsent_ends_pending_too() -> None:
    snap, _ = step(linked(), sent())
    snap, woken = step(snap, PeerWakeSent(Z, "k1"))
    # Its mail is all uncovered, so it is asked again at once.
    assert woken == [wake(Z)]


def test_the_shell_says_each_peer_wake_it_sends_with_what_it_counted(shell: Any) -> None:
    state, frame = shell
    frame(99.0, _running(Z))
    frame(100.0, sent(), sent())
    assert len(frame(100.0 + app.WAKE_WINDOW_S)) == 1

    said = [i for i in state.bridge.drain() if isinstance(i, PeerWakeSent)]

    snap = state.store.snapshot()
    unread = frozenset(c.id for c in snap.peer_unread(Z, snap.channels["k1"]))
    assert said == [PeerWakeSent(Z, "k1", unread)] and len(unread) == 2


def test_the_shell_says_a_peer_wake_it_drops_with_nothing_covered(shell: Any) -> None:
    state, frame = shell
    frame(99.0, _running(Z))
    frame(100.0, sent())
    frame(100.5, read())
    assert frame(100.0 + app.WAKE_WINDOW_S) == []

    said = [i for i in state.bridge.drain() if isinstance(i, PeerWakeSent)]

    assert said == [PeerWakeSent(Z, "k1", frozenset())]


def test_through_the_shell_a_lead_that_never_reads_is_woken_for_each_message(
    shell: Any,
) -> None:
    state, frame = shell
    frame(99.0, _running(Z))
    frame(100.0, sent())
    assert len(frame(100.0 + app.WAKE_WINDOW_S)) == 1
    frame(103.0)  # applies the shell's word
    frame(110.0, sent())
    assert len(frame(110.0 + app.WAKE_WINDOW_S)) == 2


# -- RV-W1 3: only the operator's answer to a parked send is the operator acting ---


def _parked_send() -> Snapshot:
    parked = PendingApproval(
        id="p1",
        node=LEAD,
        tool_name="mcp__pptmstr__send_to_peer",
        tool_use_id="tu1",
        raw_args={"channel": "brand", "subject": "s", "body": "b"},
        summary="send",
        requested_at=6.0,
    )
    snap, _ = step(_chained(), ApprovalRequested(LEAD, parked))
    return snap


def test_a_parked_send_cancelled_with_nobody_answering_is_not_the_operator() -> None:
    cancelled = ApprovalResolved(
        LEAD, "p1", approved=False, reason="cancelled after 600s", from_operator=False
    )
    snap, woken = step(_parked_send(), cancelled)
    assert woken == [] and len(held(snap)) == 1


def test_a_parked_send_the_operator_refused_is() -> None:
    snap, woken = step(_parked_send(), ApprovalResolved(LEAD, "p1", approved=False))
    assert woken == [wake(Z)] and held(snap) == []


def _gate_resolution(cancel: bool, decision: Any = None) -> ApprovalResolved:
    """What the gate emits for a parked call it is cancelled on or handed ``decision``."""
    import time

    from pptmstr.driver import AgentSession

    bridge = Bridge()
    bridge.start()
    try:
        session = AgentSession(bridge, "task")
        args = {"channel": "brand", "subject": "s", "body": "b"}
        hook: Any = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": "/x", "content": "y", **args},
            "tool_use_id": "tu-1",
            "session_id": "s",
            "transcript_path": "/tmp/t.jsonl",
            "cwd": "/tmp",
        }
        context: Any = {}
        task = bridge.submit(session._pre_tool_use(hook, None, context))
        deadline = time.monotonic() + 5.0
        parked: list[Any] = []
        while time.monotonic() < deadline and not parked:
            parked = [i for i in bridge.drain() if isinstance(i, ApprovalRequested)]
            time.sleep(0.01)
        assert parked
        if cancel:
            task.cancel()
        else:
            bridge.resolve(parked[0].pending.id, decision)
        seen: list[ApprovalResolved] = []
        while time.monotonic() < deadline and not seen:
            seen = [i for i in bridge.drain() if isinstance(i, ApprovalResolved)]
            time.sleep(0.01)
        assert seen
        return seen[0]
    finally:
        bridge.stop()


def test_the_gate_says_which_resolutions_were_the_operators() -> None:
    from pptmstr.bridge import Decision

    assert _gate_resolution(cancel=True).from_operator is False
    assert _gate_resolution(False, Decision(approved=False, reason="no")).from_operator is True
    host = Decision(approved=False, reason="host stopped", from_operator=False)
    assert _gate_resolution(False, host).from_operator is False


# -- RV-W1 8: what the system wakes for on its own is not a turn of the exchange ----


def test_wakes_at_a_settle_or_a_restore_are_not_counted() -> None:
    snap, _ = step(linked(), sent())
    assert snap.peer_wakes["k1"].chain == 1
    for _ in range(PEER_WAKE_CHAIN + 2):
        snap, woken = step(snap, SessionSettled(Z))
        assert woken == [wake(Z)]
    assert snap.peer_wakes["k1"].chain == 1 and held(snap) == []


def test_a_settle_wakes_a_lead_even_with_the_chain_at_its_bound() -> None:
    """
    Not held: holding is the backstop on two agents waking each other, and a
    resume is not a turn of theirs. The settle lets go of what the earlier
    run was held for, as of what it was pending for, and the chain is left
    where it was.
    """
    snap = _chained()
    snap, _ = step(snap, read(by=Z_LEAD))
    snap, _ = step(snap, sent(by=Z_LEAD))
    assert [o.node for o in held(snap)] == [LEAD]
    snap, settle_s = step(snap, SessionSettled(S))
    assert settle_s == [wake(S)] and held(snap) == []
    assert snap.peer_wakes["k1"].chain == PEER_WAKE_CHAIN


# -- RV-W1 7: the sender is told what happens, not promised a wake -----------------


def test_the_senders_reply_does_not_promise_a_wake() -> None:
    said = bus._peer_wake_said(quiet=False)
    assert said.startswith("If their session is running")
    assert "next resumed" in said
    assert "wakes nobody" in bus._peer_wake_said(quiet=True)


# -- W2: a channel decision raised wakes the other lead, through the link's wake -----


def decision(did: str = "d-1", by: NodeId = LEAD) -> Decision:
    return Decision(
        id=did,
        asker=by,
        question="ship the hero image?",
        blocks=(),
        if_unanswered="I ship it",
        evidence="the evidence text",
        recommendation="",
        standing=Standing.OPERATORS,
        reversible=None,
        raised_at=0.0,
    )


_dids = iter(f"d-{n:06x}" for n in range(1, 10**6))


def raise_on(by: NodeId = LEAD, on: str | None = "brand", did: str | None = None) -> DecisionRaised:
    return DecisionRaised(by, decision(did or next(_dids), by), request_id="rq", at=3.0, channel=on)


def test_a_raise_on_a_channel_wakes_the_other_lead_and_not_the_asker() -> None:
    snap, woken = step(linked(), raise_on(did="d-1"))

    assert woken == [wake(Z)]
    assert snap.peer_wakes["k1"] == PeerWakes(chain=1, pending=frozenset({Z}), untold=((Z, "d-1"),))


def test_the_line_names_the_decision_by_id_and_carries_none_of_its_text() -> None:
    snap, (woken,) = step(linked(), raise_on(did="d-1"))

    line = bus.wake_line(snap, woken)

    assert line == (
        "decision d-1 was raised on #brand by the lead linked to you. "
        'read_decisions(decision_id="d-1") returns it.'
    )
    message = bus.wake_message(snap, [woken])
    for text in ("ship the hero image?", "I ship it", "the evidence text"):
        assert text not in message


@pytest.mark.parametrize(
    "intent",
    [raise_on(on=None), raise_on(by=C_LEAD), raise_on(by=BUILDER), raise_on(on="nowhere")],
    ids=["no-channel", "not-a-member", "sub-agent", "no-such-channel"],
)
def test_a_raise_that_is_not_filed_on_a_link_wakes_nobody(intent: DecisionRaised) -> None:
    snap, woken = step(linked(), intent)
    assert woken == [] and "k1" not in snap.peer_wakes


def test_a_raise_while_a_message_wake_is_pending_is_coalesced_into_it() -> None:
    snap, first = step(linked(), sent())
    snap, second = step(snap, raise_on(did="d-1"))

    assert (first, second) == ([wake(Z)], [])
    assert snap.peer_wakes["k1"].chain == 1
    line = bus.wake_line(snap, first[0])
    assert line.startswith('1 unread on #brand; call read_peer_messages(channel="brand")')
    assert line.endswith('read_decisions(decision_id="d-1") returns it.')


def test_a_message_while_a_decision_wake_is_pending_is_coalesced_into_it() -> None:
    snap, first = step(linked(), raise_on(did="d-1"))
    snap, second = step(snap, sent())

    assert (first, second) == ([wake(Z)], [])
    assert snap.peer_wakes["k1"].chain == 1
    assert bus.wake_line(snap, first[0]).startswith("1 unread on #brand")


def _told(snap: Snapshot, session: str = Z, link: str = "k1") -> PeerWakeSent:
    """The shell's word that it sent the wake, with the mail and decisions it named."""
    on = snap.channels[link]
    covered = frozenset(c.id for c in snap.peer_unread(session, on))
    return PeerWakeSent(session, link, covered, frozenset(snap.peer_untold(session, on)))


def test_the_wake_sent_ends_pending_and_what_it_named_and_the_next_raise_wakes_again() -> None:
    snap, _ = step(linked(), raise_on())
    snap, at_send = step(snap, _told(snap))

    assert at_send == []
    assert snap.peer_wakes["k1"] == PeerWakes(chain=1)
    _, again = step(snap, raise_on())
    assert again == [wake(Z)]


def test_a_raise_between_the_send_and_its_word_is_woken_for() -> None:
    """The ``told`` race, as ``covered``'s for a message."""
    snap, _ = step(linked(), raise_on(did="d-1"))
    said = _told(snap)
    snap, between = step(snap, raise_on(did="d-2"))

    snap, woken = step(snap, said)

    assert between == [] and woken == [wake(Z)]
    assert snap.peer_wakes["k1"].untold == ((Z, "d-2"),)
    assert snap.peer_wakes["k1"].chain == 2


def test_a_read_of_the_links_mail_leaves_a_decision_wake_owed() -> None:
    snap, _ = step(linked(), sent())
    snap, _ = step(snap, raise_on(did="d-1"))
    snap, _ = step(snap, read())

    assert snap.peer_wakes["k1"].pending == {Z}
    assert snap.peer_wakes["k1"].untold == ((Z, "d-1"),)


def test_a_decision_withdrawn_before_its_wake_went_is_owed_no_longer() -> None:
    snap, _ = step(linked(), raise_on(did="d-1"))
    snap, _ = step(snap, DecisionWithdrawn(LEAD, "d-1", request_id="rw", at=4.0))

    assert "k1" not in snap.peer_wakes or snap.peer_wakes["k1"] == PeerWakes(chain=1)


def test_a_raise_to_a_lead_not_running_wakes_nobody_and_is_named_at_its_resume() -> None:
    snap, woken = step(_ended(linked(), Z), raise_on(did="d-1"))
    assert woken == [] and "k1" not in snap.peer_wakes

    snap, _ = step(snap, _spawned(Z_LEAD, None))
    snap, settled = step(snap, SessionSettled(Z))

    assert settled == [wake(Z)]
    assert snap.peer_wakes["k1"] == PeerWakes(pending=frozenset({Z}), untold=((Z, "d-1"),))


def test_a_settle_names_only_open_decisions_the_other_lead_raised() -> None:
    snap, _ = step(linked(), raise_on(did="d-open"))
    snap, _ = step(snap, raise_on(did="d-done"))
    snap, _ = step(snap, raise_on(by=Z_LEAD, did="d-mine"))
    snap, _ = step(snap, DecisionAnswered("d-done", "yes"))

    snap, _ = step(snap, SessionSettled(Z))

    assert snap.peer_untold(Z, snap.channels["k1"]) == ("d-open",)


def test_an_askers_restore_names_its_open_decisions_to_the_running_peer() -> None:
    from tests.test_channel_decisions import _CLOCK, _K1, _logged_board

    snap = Snapshot.empty()
    for node, parent in ((LEAD, None), (Z_LEAD, None)):
        snap, _ = step(snap, _spawned(node, parent))
    snap, _ = step(snap, ChannelsRestored((_K1,)))

    snap, woken = step(snap, BoardRestored(S, _logged_board(), _CLOCK))

    assert woken == [wake(Z)]
    assert snap.peer_wakes["k1"].untold == ((Z, "d-1"),)
    assert snap.peer_wakes["k1"].chain == 0


def raise_pong(snap: Snapshot, times: int) -> tuple[Snapshot, list[SessionWoken]]:
    """
    Each lead is woken for the other's decision, the shell's word arrives, and it
    raises one back: ``times`` raises in all, with no message and nobody reading.
    """
    every: list[SessionWoken] = []
    leads = (LEAD, Z_LEAD)
    for n in range(times):
        asker, other = leads[n % 2], leads[(n + 1) % 2]
        snap, woken = step(snap, raise_on(by=asker))
        every += woken
        if woken:
            snap, _ = step(snap, _told(snap, other[0]))
    return snap, every


def test_two_leads_raising_decisions_at_each_other_reach_k_and_are_held() -> None:
    snap, woken = raise_pong(linked(), PEER_WAKE_CHAIN)
    assert len(woken) == PEER_WAKE_CHAIN and held(snap) == []

    snap, more = raise_pong(snap, 1) if PEER_WAKE_CHAIN % 2 == 0 else (snap, [])
    if PEER_WAKE_CHAIN % 2:
        snap, more = step(snap, raise_on(by=Z_LEAD))

    assert more == []
    (row,) = held(snap)
    holder = Z if PEER_WAKE_CHAIN % 2 == 0 else S
    assert (row.node, row.link, row.kind) == ((holder, None), "k1", ObligationKind.WAKE_CHAIN)
    # The decision it was held for is still owed, and the release names it.
    (owed,) = snap.peer_untold(holder, snap.channels["k1"])
    snap, released = step(snap, WakeChainReleased("k1"))
    assert released == [wake(holder)] and held(snap) == []
    assert f'read_decisions(decision_id="{owed}")' in bus.wake_line(snap, released[0])


def test_messages_and_raises_count_toward_one_chain() -> None:
    snap, _ = step(linked(), sent())  # z woken: 1
    snap, _ = step(snap, _spent(snap))
    snap, _ = step(snap, raise_on(by=Z_LEAD))  # s woken: 2
    snap, _ = step(snap, _told(snap, S))
    snap, _ = step(snap, sent())  # z woken: 3
    assert snap.peer_wakes["k1"].chain == 3


# -- W2: the operator's answer wakes every member (Decided 12) ----------------------


@pytest.mark.parametrize(
    ("ending", "reason"),
    [
        (DecisionAnswered("d-1", "yes"), WakeReason.DECISION_ANSWERED),
        (DecisionDismissed("d-1"), WakeReason.DECISION_DISMISSED),
    ],
    ids=["answered", "dismissed"],
)
def test_the_operators_answer_wakes_the_asker_once_and_every_other_member(
    ending: Intent, reason: WakeReason
) -> None:
    snap, _ = step(linked(), raise_on(did="d-1"))
    snap, _ = step(snap, _told(snap))

    snap, woken = step(snap, ending)

    assert woken == [SessionWoken(S, reason, "d-1"), SessionWoken(Z, reason, "d-1")]


def test_the_answer_is_not_counted_and_starts_the_links_chain_again() -> None:
    snap, _ = raise_pong(linked(), PEER_WAKE_CHAIN)
    snap, _ = step(snap, raise_on(by=LEAD if PEER_WAKE_CHAIN % 2 == 0 else Z_LEAD, did="d-x"))
    assert len(held(snap)) == 1

    snap, woken = step(snap, DecisionAnswered("d-x", "no"))

    # What was held was owed only d-x, and its answer is a wake of its own.
    assert held(snap) == [] and snap.peer_wakes.get("k1", PeerWakes()).chain == 0
    assert {w.reason for w in woken} == {WakeReason.DECISION_ANSWERED} and len(woken) == 2


def test_the_answer_ends_a_raise_wake_still_owed_for_it() -> None:
    snap, _ = step(linked(), raise_on(did="d-1"))
    snap, _ = step(snap, DecisionAnswered("d-1", "yes"))
    assert snap.peer_untold(Z, snap.channels["k1"]) == ()


def test_the_other_leads_line_says_whose_decision_was_answered() -> None:
    snap, _ = step(linked(), raise_on(did="d-1"))
    snap, (to_s, to_z) = step(snap, DecisionAnswered("d-1", "yes"))

    assert bus.wake_line(snap, to_s) == (
        'decision d-1 was answered. read_decisions(decision_id="d-1") returns it.'
    )
    assert bus.wake_line(snap, to_z) == (
        "decision d-1 on #brand, which the lead linked to you raised, was answered. "
        'read_decisions(decision_id="d-1") returns it.'
    )
    assert "yes" not in bus.wake_message(snap, [to_z])


def test_an_answer_once_the_link_closed_wakes_the_asker_alone() -> None:
    snap, _ = step(linked(), raise_on(did="d-1"))
    snap, _ = step(snap, ChannelClosed("k1", 4.0))
    _, woken = step(snap, DecisionAnswered("d-1", "yes"))
    assert woken == [SessionWoken(S, WakeReason.DECISION_ANSWERED, "d-1")]


def test_a_decision_with_no_channel_is_answered_to_its_asker_as_before() -> None:
    snap, _ = step(linked(), raise_on(on=None, did="d-1"))
    _, woken = step(snap, DecisionAnswered("d-1", "yes"))
    assert woken == [SessionWoken(S, WakeReason.DECISION_ANSWERED, "d-1")]


# -- W2: the shell sends a decision's wake and says what it named --------------------


def test_the_shell_sends_a_wake_with_only_a_decision_in_it_and_says_so(shell: Any) -> None:
    state, frame = shell
    frame(99.0, _running(Z))
    frame(100.0, raise_on(did="d-1"))

    (sent_to,) = frame(100.0 + app.WAKE_WINDOW_S)

    assert sent_to[0] == Z_LEAD and 'read_decisions(decision_id="d-1")' in sent_to[1]
    said = [i for i in state.bridge.drain() if isinstance(i, PeerWakeSent)]
    assert said == [PeerWakeSent(Z, "k1", frozenset(), frozenset({"d-1"}))]


def test_the_shell_drops_a_decision_wake_once_the_decision_is_withdrawn(shell: Any) -> None:
    state, frame = shell
    frame(99.0, _running(Z))
    frame(100.0, raise_on(did="d-1"))
    frame(100.5, DecisionWithdrawn(LEAD, "d-1", request_id="rw", at=4.0))

    assert frame(100.0 + app.WAKE_WINDOW_S) == []
    said = [i for i in state.bridge.drain() if isinstance(i, PeerWakeSent)]
    assert said == [PeerWakeSent(Z, "k1")]


# -- RV-W12 1: an answer a lead was down for is named at its resume (Decided 12) ----


def _resumed(snap: Snapshot, session: str = Z) -> tuple[Snapshot, list[SessionWoken]]:
    snap, _ = step(snap, _spawned((session, None), None))
    return step(snap, SessionSettled(session))


@pytest.mark.parametrize(
    ("ending", "reason"),
    [
        (DecisionAnswered("d-1", "yes"), WakeReason.DECISION_ANSWERED),
        (DecisionDismissed("d-1"), WakeReason.DECISION_DISMISSED),
    ],
    ids=["answered", "dismissed"],
)
def test_a_decision_raised_and_ended_while_the_other_lead_was_down_is_named_at_its_resume(
    ending: Intent, reason: WakeReason
) -> None:
    """RV-W12's F1 run."""
    snap = _ended(linked(), Z)
    snap, _ = step(snap, raise_on(did="d-1"))
    snap, _ = step(snap, ending)

    snap, settled = _resumed(snap)

    assert SessionWoken(Z, reason, "d-1") in settled
    # Uncounted: the operator's act, read at a resume.
    assert snap.peer_wakes.get("k1", PeerWakes()).chain == 0
    assert bus.wake_line(snap, SessionWoken(Z, reason, "d-1")).startswith(
        "decision d-1 on #brand, which the lead linked to you raised, was "
    )


def test_an_answer_the_lead_was_woken_for_is_not_named_again_at_its_resume() -> None:
    snap, _ = step(linked(), raise_on(did="d-1"))
    snap, _ = step(snap, DecisionAnswered("d-1", "yes"))
    snap, _ = step(snap, DecisionWakeSent(Z, frozenset({"d-1"})))
    snap = _ended(snap, Z)

    _, settled = _resumed(snap)

    assert [w for w in settled if w.subject_id == "d-1"] == []


def test_an_answer_named_at_one_resume_is_not_named_at_the_next_in_the_same_run() -> None:
    snap = _ended(linked(), Z)
    snap, _ = step(snap, raise_on(did="d-1"))
    snap, _ = step(snap, DecisionAnswered("d-1", "yes"))
    snap, first = _resumed(snap)
    snap, _ = step(snap, DecisionWakeSent(Z, frozenset({"d-1"})))
    snap = _ended(snap, Z)

    _, second = _resumed(snap)

    assert [w.subject_id for w in first if w.reason is WakeReason.DECISION_ANSWERED] == ["d-1"]
    assert [w for w in second if w.subject_id == "d-1"] == []


def test_a_resume_names_no_ended_decision_the_lead_raised_or_that_was_withdrawn() -> None:
    snap = _ended(linked(), Z)
    snap, _ = step(snap, raise_on(did="d-withdrawn"))
    snap, _ = step(snap, DecisionWithdrawn(LEAD, "d-withdrawn", request_id="rw", at=4.0))
    snap, _ = step(snap, raise_on(by=Z_LEAD, did="d-mine"))
    snap, _ = step(snap, DecisionAnswered("d-mine", "yes"))

    _, settled = _resumed(snap)

    assert [w for w in settled if w.reason is not WakeReason.PEER_MESSAGE] == []


def test_a_resume_names_no_answer_on_a_link_since_closed() -> None:
    snap = _ended(linked(), Z)
    snap, _ = step(snap, raise_on(did="d-1"))
    snap, _ = step(snap, DecisionAnswered("d-1", "yes"))
    snap, _ = step(snap, ChannelClosed("k1", 9.0))

    _, settled = _resumed(snap)

    assert settled == []


def test_several_answers_missed_are_named_oldest_first_and_a_reset_keeps_them_told() -> None:
    snap = _ended(linked(), Z)
    snap, _ = step(snap, raise_on(did="d-a"))
    snap, _ = step(snap, raise_on(did="d-b"))
    snap, _ = step(snap, DecisionDismissed("d-b"), now=6.0)
    snap, _ = step(snap, DecisionAnswered("d-a", "yes"), now=7.0)

    snap, settled = _resumed(snap)
    named = [(w.reason, w.subject_id) for w in settled if w.reason is not WakeReason.PEER_MESSAGE]
    assert named == [
        (WakeReason.DECISION_DISMISSED, "d-b"),
        (WakeReason.DECISION_ANSWERED, "d-a"),
    ]
    snap, _ = step(snap, DecisionWakeSent(Z, frozenset({"d-a", "d-b"})))
    # The operator acting on the link starts its chain again and keeps what
    # was told.
    snap, _ = step(snap, sent())
    snap, _ = step(snap, WakeChainReleased("k1"))
    snap, _ = step(snap, ConcernWithdrawn(_waiting(snap)))
    assert snap.peer_wakes["k1"].heard == {(Z, "d-a"), (Z, "d-b")}


# -- RV-W3 2: "told" is the shell's send, not the store's order ----------------------


def test_an_answer_is_not_told_until_the_shell_says_it_sent_the_wake() -> None:
    """The order alone marks nothing: the shell may yet drop it."""
    snap, _ = step(linked(), raise_on(did="d-1"))
    snap, woken = step(snap, DecisionAnswered("d-1", "yes"))

    assert SessionWoken(Z, WakeReason.DECISION_ANSWERED, "d-1") in woken
    assert snap.peer_wakes.get("k1", PeerWakes()).heard == frozenset()

    snap, _ = step(snap, DecisionWakeSent(Z, frozenset({"d-1"})))

    assert snap.peer_wakes["k1"].heard == {(Z, "d-1")}


def test_a_wake_dropped_on_a_failed_spawn_leaves_the_answer_to_the_next_resume() -> None:
    """RV-W3's R1, through the reducer: ordered while starting, never sent."""
    snap = _ended(linked(), Z)
    snap, _ = step(snap, raise_on(did="d-1"))
    snap, _ = step(snap, _spawned(Z_LEAD, None))  # starting: a wake can be ordered
    snap, woken = step(snap, DecisionAnswered("d-1", "yes"))
    assert SessionWoken(Z, WakeReason.DECISION_ANSWERED, "d-1") in woken
    snap, _ = step(snap, AgentFinished(Z_LEAD, AgentState.FAILED, 6.0))

    _, settled = _resumed(snap)

    assert SessionWoken(Z, WakeReason.DECISION_ANSWERED, "d-1") in settled


def test_a_settle_whose_wake_is_dropped_names_the_answer_again_at_the_next() -> None:
    snap = _ended(linked(), Z)
    snap, _ = step(snap, raise_on(did="d-1"))
    snap, _ = step(snap, DecisionAnswered("d-1", "yes"))
    snap, first = _resumed(snap)
    assert SessionWoken(Z, WakeReason.DECISION_ANSWERED, "d-1") in first
    snap, _ = step(snap, AgentFinished(Z_LEAD, AgentState.FAILED, 6.0))

    _, second = _resumed(snap)

    assert SessionWoken(Z, WakeReason.DECISION_ANSWERED, "d-1") in second


@pytest.mark.parametrize(
    "said",
    [
        frozenset({"d-open"}),  # not ended
        frozenset({"d-mine"}),  # z's own
        frozenset({"d-nowhere"}),  # no such decision
    ],
    ids=["open", "its-own", "unknown"],
)
def test_the_shells_word_marks_only_an_ended_decision_the_other_lead_raised(
    said: frozenset[str],
) -> None:
    snap, _ = step(linked(), raise_on(did="d-open"))
    snap, _ = step(snap, raise_on(by=Z_LEAD, did="d-mine"))
    snap, _ = step(snap, DecisionAnswered("d-mine", "yes"))

    snap, woken = step(snap, DecisionWakeSent(Z, said))

    assert snap.peer_wakes.get("k1", PeerWakes()).heard == frozenset() and woken == []


def _decision_words(state: Any) -> list[DecisionWakeSent]:
    return [i for i in state.bridge.drain() if isinstance(i, DecisionWakeSent)]


def test_the_shell_says_the_decisions_a_sent_wake_named(shell: Any) -> None:
    state, frame = shell
    frame(99.0, _running(Z), raise_on(did="d-1"))
    frame(99.0 + app.WAKE_WINDOW_S)
    state.bridge.drain()
    frame(110.0, DecisionAnswered("d-1", "yes"))

    (sent_to,) = [
        w for w in frame(110.0 + app.WAKE_WINDOW_S) if "d-1" in w[1] and "answered" in w[1]
    ]

    assert sent_to[0] == Z_LEAD
    assert DecisionWakeSent(Z, frozenset({"d-1"})) in _decision_words(state)


def test_through_the_shell_a_spawn_that_fails_in_the_window_is_named_the_answer_at_resume(
    shell: Any,
) -> None:
    """RV-W3's R1: the wake is dropped, nothing is said, and the resume names it."""
    state, frame = shell
    frame(99.0, _running(Z), raise_on(did="d-1"))
    frame(99.0 + app.WAKE_WINDOW_S)
    frame(105.0, AgentFinished(Z_LEAD, AgentState.DONE, 105.0))
    frame(106.0, _spawned(Z_LEAD, None))  # starting again
    frame(107.0, DecisionAnswered("d-1", "yes"))
    frame(108.0, AgentFinished(Z_LEAD, AgentState.FAILED, 108.0))
    before = list(frame(120.0))
    assert not any("d-1" in text and "answered" in text for _, text in before)
    assert _decision_words(state) == []

    frame(130.0, _spawned(Z_LEAD, None))
    frame(131.0, SessionSettled(Z))

    assert any(
        w.reason is WakeReason.DECISION_ANSWERED and w.subject_id == "d-1"
        for w in state.wakes.get(Z, [])
    )


def _owed_both(state: Any, frame: Any) -> None:
    """``z`` is running and owed a peer wake and an answered decision's, due at 110 + window."""
    frame(99.0, _running(Z), raise_on(did="d-1"))
    frame(99.0 + app.WAKE_WINDOW_S)
    state.bridge.drain()
    frame(110.0, DecisionAnswered("d-1", "yes"), sent())


def test_without_a_pool_no_decision_is_said_named_and_the_peer_wake_is_said_dropped(
    shell: Any,
) -> None:
    """RV-W3d 2: no pool means nothing was handed over, so nothing is told."""
    state, frame = shell
    _owed_both(state, frame)
    state.pool = None

    state.frame_now = 110.0 + app.WAKE_WINDOW_S
    app.apply_intents(state)
    said = state.bridge.drain()

    assert [i for i in said if isinstance(i, DecisionWakeSent)] == []
    assert [i for i in said if isinstance(i, PeerWakeSent)] == [PeerWakeSent(Z, "k1")]
    assert Z not in state.wakes


def test_a_wake_the_bridge_refuses_is_kept_and_nothing_is_said(
    shell: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RV-W3d 2: a submit that raises hands nothing over, and loses no wake."""
    state, frame = shell
    _owed_both(state, frame)
    due = 110.0 + app.WAKE_WINDOW_S
    before = list(state.wakes[Z])

    def refuse(coro: Any) -> Any:
        coro.close()
        raise RuntimeError("bridge not started")

    monkeypatch.setattr(state.bridge, "submit", refuse)
    state.frame_now = due
    app.apply_intents(state)
    said = state.bridge.drain()
    monkeypatch.undo()

    assert not any(isinstance(i, (DecisionWakeSent, PeerWakeSent)) for i in said)
    assert state.wakes[Z] == before and state.wake_due[Z] == due + app.WAKE_WINDOW_S

    woken = frame(due + app.WAKE_WINDOW_S)

    assert any(root == Z_LEAD and "d-1" in text for root, text in woken)
    assert DecisionWakeSent(Z, frozenset({"d-1"})) in _decision_words(state)


# -- RV-W12 5: quiet mail left after a withdrawal wakes nobody ----------------------


def test_withdrawing_the_only_loud_message_beside_quiet_ones_lets_the_wake_go() -> None:
    snap, _ = step(linked(), sent(quiet=True))
    loud = sent()
    snap, _ = step(snap, loud)
    assert snap.peer_wakes["k1"].pending == {Z}

    snap, _ = step(snap, ConcernWithdrawn(loud.concern_id))

    assert Z not in snap.peer_wakes.get("k1", PeerWakes()).pending
    _, woken = step(snap, sent())
    assert woken == [wake(Z)]


def test_the_shell_sends_nothing_for_quiet_mail_left_after_a_withdrawal(shell: Any) -> None:
    """RV-W12's F5 run."""
    state, frame = shell
    frame(99.0, _running(Z))
    loud = sent()
    frame(100.0, sent(quiet=True), loud)
    frame(100.5, ConcernWithdrawn(loud.concern_id))

    assert frame(100.0 + app.WAKE_WINDOW_S) == []
    said = [i for i in state.bridge.drain() if isinstance(i, PeerWakeSent)]
    assert said == [PeerWakeSent(Z, "k1")]


# -- RV-W12 needs-a-run: the shell's "sendable" and the store's "wakeable" agree ----


@pytest.mark.parametrize("detached", [False, True], ids=["on-fleet", "detached"])
@pytest.mark.parametrize("recalled", [None, Recalled()], ids=["live", "recalled"])
@pytest.mark.parametrize("state", list(AgentState), ids=lambda s: s.name)
def test_the_shell_sends_to_exactly_the_sessions_the_store_wakes(
    state: AgentState, recalled: Recalled | None, detached: bool
) -> None:
    """
    Were they to differ, a dropped wake would be said, re-asked counted, and
    dropped again every wake window until the link's chain was held for a
    session nobody could wake. ``off_fleet`` needs a root in a final state, which
    the store refuses already.
    """
    import dataclasses
    from types import MappingProxyType

    from pptmstr.store import _wakeable

    base = linked()
    root = dataclasses.replace(
        base.nodes[Z_LEAD], state=state, recalled=recalled, detached=detached
    )
    snap = dataclasses.replace(base, nodes=MappingProxyType({**base.nodes, Z_LEAD: root}))

    assert app._sendable(snap, Z) == _wakeable(snap, Z)
    assert app._sendable(snap, "nobody") == _wakeable(snap, "nobody") is False


def test_a_detached_session_with_mail_is_not_re_asked_toward_the_bound() -> None:
    """RV-W12's parity run, through the reducer: no wake, so nothing to re-ask."""
    from pptmstr.intents import SessionDetached

    snap = _ended(linked(), Z)
    snap, _ = step(snap, SessionDetached(Z))
    assert snap.off_fleet(Z)

    for _ in range(PEER_WAKE_CHAIN + 2):
        snap, woken = step(snap, sent())
        assert woken == []
        snap, again = step(snap, PeerWakeSent(Z, "k1"))
        assert again == []
    assert "k1" not in snap.peer_wakes and held(snap) == []
