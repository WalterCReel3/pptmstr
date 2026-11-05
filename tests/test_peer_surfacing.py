"""
Round three, W3b: what the operator is shown about two linked teams, and how
much a wake names.

- **A.** A wake names at most ``bus.WAKE_DECISIONS_NAMED`` of the decisions the
  lead across a link raised, and counts the rest, so a resume or a restore that
  names a link's whole history (``PeerWakes.heard`` is this run's alone) stays
  one short message.
- **B.** An asker's ``BoardRestored`` that brings back channel decisions the
  operator answered or dismissed names them to the running peer, uncounted.
- **Decided 11.** A delivered ``needs_answer`` message with no reply surfaces as
  a ``PeerQuestionUnanswered`` row once the shell says its age has passed
  (``app._age_questions``, ``intents.PeerQuestionAged``).
- **Held mail.** Peer mail that a link this run does not hold, or a live link
  that does not hand it over, is a ``PeerMailHeld`` row until the link is live
  again or the operator dismisses it. Mail on a link closed in its log, by the
  operator or by the relink sweep, is not (RV-W3 8, RV-W3d 1).
- **RV-W3.** A question to a lead whose session is not running is a row at
  once whether or not it was read; ``PeerWakes.heard`` is marked when the shell
  sends the wake (``DecisionWakeSent``), not when the store orders it; the
  rows' texts say what is true.

- **P4.** Whose move it is on a link (``Snapshot.whose_move``), and every peer
  row in NEEDS YOU has something to press: go to, resume or dismiss.
The reducer is driven directly for what the store decides, and the shell for
the timer. The rows' buttons and texts are drawn against a recording imgui;
nothing here has been checked on a display.
"""

from __future__ import annotations

import dataclasses
import math
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest

from pptmstr import app, boardlog, bus, digest
from pptmstr.boardfacts import outcomes
from pptmstr.boardlog import Clock
from pptmstr.bridge import Bridge
from pptmstr.effects import SessionWoken, WakeReason
from pptmstr.intents import (
    AgentFinished,
    ApprovalRequested,
    BoardRestored,
    ChannelClosed,
    ChannelLinked,
    ChannelsRestored,
    ConcernWithdrawn,
    DecisionAnswered,
    DecisionDismissed,
    DecisionWakeSent,
    DecisionWithdrawn,
    HeldMailDismissed,
    Intent,
    PeerQuestionAged,
    PeerQuestionDismissed,
    PeerWakeSent,
    SessionSettled,
)
from pptmstr.model import (
    EVERY_CHANNEL,
    WITHDRAW_PEER_TOOL,
    AgentState,
    ApprovalNeeded,
    Channel,
    ConcernState,
    DetachRefusal,
    MoveOwner,
    NodeId,
    Obligation,
    ObligationKind,
    PeerMailHeld,
    PeerQuestionUnanswered,
    PeerWakes,
    PendingApproval,
    Recalled,
    Snapshot,
    TranscriptPresence,
    WakeChainHeld,
    WhoseMove,
)
from pptmstr.settings import Settings
from pptmstr.store import Store, _apply
from pptmstr.ui import inbox
from tests.test_peer_wakes import (
    BUILDER,
    C_LEAD,
    LEAD,
    Z_LEAD,
    C,
    S,
    Z,
    _ended,
    _spawned,
    linked,
    raise_on,
    read,
    sent,
    step,
    wake,
)

N = bus.WAKE_DECISIONS_NAMED


def run(snap: Snapshot, *intents: Intent, now: float = 5.0) -> tuple[Snapshot, list[SessionWoken]]:
    every: list[SessionWoken] = []
    for intent in intents:
        snap, woken = step(snap, intent, now)
        every += woken
    return snap, every


def questions(snap: Snapshot) -> list[PeerQuestionUnanswered]:
    return [o for o in snap.needs_you if isinstance(o, PeerQuestionUnanswered)]


def held_rows(snap: Snapshot) -> list[PeerMailHeld]:
    return [o for o in snap.needs_you if isinstance(o, PeerMailHeld)]


# -- A. a wake names a few decisions and counts the rest ----------------------------


def test_the_cap_is_small_and_positive() -> None:
    assert 1 <= N <= 5


def test_a_resume_names_a_few_ended_decisions_and_counts_the_rest() -> None:
    """The restart case, in one run: every answer arrived while z was down."""
    many = N + 2
    snap = _ended(linked(), Z)
    dids = [f"d-{n}" for n in range(many)]
    for did in dids:
        snap, _ = step(snap, raise_on(did=did))
        snap, _ = step(snap, DecisionAnswered(did, "yes"))
    snap, _ = step(snap, _spawned(Z_LEAD, None))

    snap, woken = step(snap, SessionSettled(Z))

    answers = [w for w in woken if w.reason is WakeReason.DECISION_ANSWERED]
    assert [w.subject_id for w in answers] == dids
    message = bus.wake_message(snap, woken)
    named = [did for did in dids if f'read_decisions(decision_id="{did}")' in message]
    assert named == dids[:N]
    assert message.endswith(
        '- and 2 more decision(s) that the lead of session s, card "do a thing", project "w" '
        "(the quoted card title and project are that session's own text, not pptmstr's) "
        "raised were answered or dismissed; read_decisions to see them."
    )


def test_exactly_the_cap_is_named_whole_with_no_count() -> None:
    snap = _ended(linked(), Z)
    for n in range(N):
        snap, _ = step(snap, raise_on(did=f"d-{n}"))
        snap, _ = step(snap, DecisionDismissed(f"d-{n}"))
    snap, _ = step(snap, _spawned(Z_LEAD, None))
    snap, woken = step(snap, SessionSettled(Z))

    message = bus.wake_message(snap, woken)

    assert "more decision(s)" not in message
    assert message.count("was dismissed") == N


def test_the_woken_sessions_own_decisions_are_never_folded() -> None:
    """Its lead asked them: each is named, and none counts toward the cap."""
    snap = linked()
    mine = [f"d-mine-{n}" for n in range(N + 2)]
    for did in mine:
        snap, _ = step(snap, raise_on(by=Z_LEAD, did=did))
    theirs = [f"d-theirs-{n}" for n in range(N + 1)]
    for did in theirs:
        snap, _ = step(snap, raise_on(did=did))
    wakes = [SessionWoken(Z, WakeReason.DECISION_ANSWERED, did) for did in mine + theirs]
    for did in mine + theirs:
        snap, _ = step(snap, DecisionAnswered(did, "yes"))

    message = bus.wake_message(snap, wakes)

    for did in mine + theirs[:N]:
        assert f'read_decisions(decision_id="{did}")' in message
    assert f'read_decisions(decision_id="{theirs[N]}")' not in message
    assert "- and 1 more decision(s)" in message


def test_a_peer_line_names_a_few_open_decisions_and_counts_the_rest() -> None:
    snap, first = step(linked(), raise_on(did="d-0"))
    for n in range(1, N + 3):
        snap, more = step(snap, raise_on(did=f"d-{n}"))
        assert more == []

    line = bus.wake_line(snap, first[0])

    for n in range(N):
        assert f'read_decisions(decision_id="d-{n}")' in line
    assert f'decision_id="d-{N}"' not in line
    assert line.endswith(
        "and 3 more raised on #brand by the lead linked to you; read_decisions to see them."
    )


def test_the_folded_decisions_count_as_told_so_no_wake_is_asked_again() -> None:
    """
    The shell reports every untold id, the counted ones too. Were it to report
    only the named ones, the rest would ask a counted wake each round.
    """
    snap, (first,) = step(linked(), raise_on(did="d-0"))
    for n in range(1, N + 3):
        snap, _ = step(snap, raise_on(did=f"d-{n}"))
    bridge = Bridge()
    shell = SimpleNamespace(bridge=bridge)

    app._say_peer_wakes_spent(shell, snap, [first], [first])  # type: ignore[arg-type]

    (said,) = bridge.drain()
    assert isinstance(said, PeerWakeSent)
    assert said.told == frozenset(f"d-{n}" for n in range(N + 3))
    snap, again = step(snap, said)
    assert again == [] and snap.peer_untold(Z, snap.channels["k1"]) == ()


# -- B. an asker's restore names its ended channel decisions to the running peer -----


_K1 = Channel(id="k1", name="brand", members=frozenset({S, Z}), charter="c", created_at=1.0)
_CLOCK = Clock(wall=5_000.0, monotonic=100.0)


def _logged(*intents: Intent) -> boardlog.Board:
    """``S``'s board log after ``intents``, as the reducer's facts wrote it."""
    snap = linked()
    records: list[boardlog.Record] = []
    for intent in intents:
        after, _ = _apply(snap, intent, 5.0)
        for fact in outcomes(snap, intent, after, 5.0):
            if fact.session_id == S:
                record = boardlog.decode(boardlog.encode(fact.record))
                assert not isinstance(record, boardlog.Malformed)
                records.append(record)
        snap = after
    return boardlog.restore(records)


def _fresh(*, z_running: bool = True) -> Snapshot:
    snap = Snapshot.empty()
    snap, _ = step(snap, _spawned(LEAD, None))
    if z_running:
        snap, _ = step(snap, _spawned(Z_LEAD, None))
    snap, _ = step(snap, ChannelsRestored((_K1,)))
    return snap


_ENDED_ON_DISK = (
    raise_on(did="d-yes"),
    raise_on(did="d-no"),
    raise_on(did="d-back"),
    raise_on(did="d-open"),
    DecisionAnswered("d-yes", "ship it"),
    DecisionDismissed("d-no"),
    DecisionWithdrawn(LEAD, "d-back", request_id="rw", at=4.0),
)


def test_a_restore_names_answered_and_dismissed_decisions_to_the_running_peer() -> None:
    snap, woken = step(_fresh(), BoardRestored(S, _logged(*_ENDED_ON_DISK), _CLOCK))

    assert SessionWoken(Z, WakeReason.DECISION_ANSWERED, "d-yes") in woken
    assert SessionWoken(Z, WakeReason.DECISION_DISMISSED, "d-no") in woken
    # Withdrawn: nothing was decided. Open: named through the link's wake.
    assert not any(w.subject_id in ("d-back", "d-open") for w in woken if w.session_id == Z)
    assert wake(Z) in woken
    assert snap.peer_untold(Z, snap.channels["k1"]) == ("d-open",)
    # Uncounted: the system ordered these.
    assert snap.peer_wakes["k1"].chain == 0
    message = bus.wake_message(snap, [w for w in woken if w.session_id == Z])
    assert (
        'decision d-yes on #brand, which the lead of session s, card "do a thing", project "w" '
        "(the quoted card title and project are that session's own text, not pptmstr's) "
        "raised, was answered"
    ) in message


def test_a_restore_names_nothing_to_the_asker_itself() -> None:
    _, woken = step(_fresh(), BoardRestored(S, _logged(*_ENDED_ON_DISK), _CLOCK))
    assert not any(w.session_id == S for w in woken)


def test_what_a_restore_named_is_not_named_again_at_the_peers_resume() -> None:
    snap, _ = step(_fresh(), BoardRestored(S, _logged(*_ENDED_ON_DISK), _CLOCK))
    snap, _ = step(snap, DecisionWakeSent(Z, frozenset({"d-yes", "d-no"})))

    _, settled = step(snap, SessionSettled(Z))

    assert not any(
        w.reason in (WakeReason.DECISION_ANSWERED, WakeReason.DECISION_DISMISSED) for w in settled
    )


def test_a_peer_not_running_at_the_restore_is_named_them_at_its_resume() -> None:
    snap, woken = step(_fresh(z_running=False), BoardRestored(S, _logged(*_ENDED_ON_DISK), _CLOCK))
    assert not any(w.session_id == Z for w in woken)

    snap, _ = step(snap, _spawned(Z_LEAD, None))
    _, settled = step(snap, SessionSettled(Z))

    assert {
        w.subject_id
        for w in settled
        if w.reason in (WakeReason.DECISION_ANSWERED, WakeReason.DECISION_DISMISSED)
    } == {"d-yes", "d-no"}


def test_a_restore_names_only_what_it_brought_back() -> None:
    """
    ``z`` is up again and its restore has not settled. An answer on another of
    its links, given while it was down, is its own settle's to name: ``s``'s
    restore names only ``s``'s decisions.
    """
    snap, _ = run(
        linked(),
        ChannelLinked("k2", "ops", frozenset({C, Z}), "c", 1.0, frozenset({C, Z})),
    )
    snap = _ended(snap, Z)
    snap, _ = run(snap, raise_on(by=C_LEAD, on="ops", did="d-ops"), DecisionAnswered("d-ops", "y"))
    snap, _ = step(snap, _spawned(Z_LEAD, None))

    snap, woken = step(snap, BoardRestored(S, _logged(*_ENDED_ON_DISK), _CLOCK))

    assert {w.subject_id for w in woken if w.session_id == Z} == {"d-yes", "d-no", "k1"}
    _, settled = step(snap, SessionSettled(Z))
    assert SessionWoken(Z, WakeReason.DECISION_ANSWERED, "d-ops") in settled


def test_a_restore_of_a_long_history_is_one_short_wake() -> None:
    """A and B together: the case the cap is for."""
    many = N + 4
    intents: list[Intent] = []
    for n in range(many):
        intents += [raise_on(did=f"d-{n}"), DecisionAnswered(f"d-{n}", "yes")]
    snap, woken = step(_fresh(), BoardRestored(S, _logged(*intents), _CLOCK))

    message = bus.wake_message(snap, [w for w in woken if w.session_id == Z])

    assert message.count("was answered") == N
    assert "- and 4 more decision(s)" in message


# -- Decided 11: the unanswered question ----------------------------------------------


def _asked(snap: Snapshot | None = None) -> tuple[Snapshot, str]:
    """``s`` asks ``z`` a question that needs an answer, and ``z`` reads it at 4.0."""
    question = sent(needs_answer=True)
    snap, _ = run(snap or linked(), question, read())
    return snap, question.concern_id


def test_a_question_is_owed_an_answer_only_once_its_lead_was_handed_it() -> None:
    question = sent(needs_answer=True)
    snap, _ = step(linked(), question)
    assert snap.awaiting_answer() == ()

    snap, _ = step(snap, read())

    (owed,) = snap.awaiting_answer()
    assert (owed.id, owed.delivered_at) == (question.concern_id, 4.0)


def test_a_message_that_needs_no_answer_is_owed_none() -> None:
    snap, _ = run(linked(), sent(), read())
    assert snap.awaiting_answer() == ()


def test_the_shells_word_raises_a_row_for_the_recipient_and_wakes_nobody() -> None:
    snap, cid = _asked()

    snap, woken = step(snap, PeerQuestionAged(cid))

    (row,) = questions(snap)
    assert (row.node, row.question, row.link, row.channel) == (Z_LEAD, cid, "k1", "brand")
    assert (row.kind, row.since) == (ObligationKind.PEER_QUESTION, 4.0)
    assert "the subject" not in row.summary and "the body text" not in row.summary
    assert woken == []


def test_the_sender_is_not_told() -> None:
    """Decided 11: nothing in the sender's session says so, its digest included."""
    snap, cid = _asked()
    snap, _ = step(snap, PeerQuestionAged(cid))

    assert not any(o.node[0] == S for o in snap.needs_you)
    assert digest.digest(snap, S).waiting == ()


def test_an_answer_ends_the_row() -> None:
    snap, cid = _asked()
    snap, _ = step(snap, PeerQuestionAged(cid))

    snap, _ = step(snap, sent(by=Z_LEAD, re=cid))

    assert questions(snap) == [] and snap.questions_aged == frozenset()


def test_an_answer_that_crossed_the_shells_word_wins() -> None:
    snap, cid = _asked()
    snap, _ = step(snap, sent(by=Z_LEAD, re=cid))

    snap, _ = step(snap, PeerQuestionAged(cid))

    assert questions(snap) == [] and snap.questions_aged == frozenset()


def test_a_reply_from_the_sender_is_not_an_answer() -> None:
    snap, cid = _asked()
    snap, _ = run(snap, sent(by=LEAD, re=cid), PeerQuestionAged(cid))
    assert [r.question for r in questions(snap)] == [cid]


def test_an_answer_withdrawn_before_it_was_read_leaves_the_question_owed() -> None:
    snap, cid = _asked()
    answer = sent(by=Z_LEAD, re=cid)
    snap, _ = step(snap, answer)
    assert snap.awaiting_answer() == ()

    snap, _ = step(snap, ConcernWithdrawn(answer.concern_id))

    assert [c.id for c in snap.awaiting_answer()] == [cid]


def test_closing_the_link_ends_the_row() -> None:
    snap, cid = _asked()
    snap, _ = step(snap, PeerQuestionAged(cid))

    snap, _ = step(snap, ChannelClosed("k1", 9.0))

    assert questions(snap) == [] and snap.awaiting_answer() == ()


def test_the_row_stays_when_the_recipient_ends_and_says_so() -> None:
    snap, cid = _asked()
    snap, _ = step(snap, PeerQuestionAged(cid))

    snap = _ended(snap, Z)

    (row,) = questions(snap)
    assert row.summary.endswith("its session is not running")


def test_the_row_does_not_keep_an_ended_session_on_the_fleet() -> None:
    snap, cid = _asked()
    snap, _ = step(snap, PeerQuestionAged(cid))
    snap = _ended(snap, Z)

    assert questions(snap) != []
    assert snap.detach_refusal(Z) is not DetachRefusal.OWES


def test_a_restored_question_is_owed_from_its_logged_delivery() -> None:
    """Re-armed on restore: the delivery comes back on this run's clock."""
    question = sent(needs_answer=True)
    board = _logged(question)
    (logged,) = board.concerns
    delivered = dataclasses.replace(logged, state=ConcernState.DELIVERED, delivered_at=4_900.0)
    board = dataclasses.replace(board, concerns=(delivered,))

    snap, _ = step(_fresh(), BoardRestored(S, board, _CLOCK))

    (owed,) = snap.awaiting_answer()
    assert owed.id == question.concern_id
    # 100 s before the restore's reading on the wall clock is 100 s before it here.
    assert owed.delivered_at == pytest.approx(0.0)


# -- Decided 4: a question whose recipient is not running -----------------------------


@pytest.mark.parametrize("how", [AgentState.FAILED, AgentState.DONE, AgentState.CANCELLED])
def test_a_question_to_a_session_not_running_is_a_row_at_once(how: AgentState) -> None:
    snap, cid = _asked()

    snap, woken = step(snap, AgentFinished(Z_LEAD, how, 4.5))

    (row,) = questions(snap)
    assert (row.node, row.question, row.since, row.running) == (Z_LEAD, cid, 4.0, False)
    # Not "unanswered": its lead read it, and an answer would be in its own log.
    assert row.summary.endswith("has no answer in this run - its session is not running")
    assert "unanswered" not in row.summary
    assert snap.questions_aged == frozenset()  # not the shell's word: the age has not passed
    assert woken == []


def test_a_running_recipient_waits_out_the_age() -> None:
    snap, _ = _asked()
    assert questions(snap) == []


def test_a_resume_before_the_age_takes_the_row_back_to_the_age_rule() -> None:
    snap, cid = _asked()
    snap, _ = step(snap, AgentFinished(Z_LEAD, AgentState.FAILED, 4.5))
    assert questions(snap) != []

    snap, _ = step(snap, _spawned(Z_LEAD, None))

    assert questions(snap) == [] and [c.id for c in snap.awaiting_answer()] == [cid]
    snap, _ = step(snap, PeerQuestionAged(cid))
    (row,) = questions(snap)
    assert row.running and row.summary.endswith("is unanswered past its age")


def test_a_resume_after_the_age_keeps_the_row() -> None:
    """The shell's word came while the session was down: the age has passed either way."""
    snap, cid = _asked()
    snap, _ = run(snap, AgentFinished(Z_LEAD, AgentState.DONE, 4.5), PeerQuestionAged(cid))

    snap, _ = step(snap, _spawned(Z_LEAD, None))

    (row,) = questions(snap)
    assert row.question == cid and row.running


def test_an_answer_or_a_close_ends_a_down_recipients_row() -> None:
    snap, cid = _asked()
    down, _ = step(snap, AgentFinished(Z_LEAD, AgentState.FAILED, 4.5))

    closed, _ = step(down, ChannelClosed("k1", 9.0))
    # An answer reaches the store from the recipient's log as it is restored.
    answered, _ = step(down, sent(by=Z_LEAD, re=cid))

    assert questions(closed) == [] and questions(answered) == []


def test_a_question_to_a_session_not_in_this_run_is_a_row_at_once() -> None:
    """After a restart that restored the asker and not the recipient: off the fleet."""
    question = sent(needs_answer=True)
    board = _logged(question)
    (logged,) = board.concerns
    board = dataclasses.replace(
        board,
        concerns=(dataclasses.replace(logged, state=ConcernState.DELIVERED, delivered_at=4_900.0),),
    )

    snap, _ = step(_fresh(z_running=False), BoardRestored(S, board, _CLOCK))

    (row,) = questions(snap)
    assert row.question == question.concern_id and not row.running


# -- RV-W3 1: a question the lead never read, to a session that is not running --------


@pytest.mark.parametrize("how", [AgentState.FAILED, AgentState.DONE, AgentState.CANCELLED])
def test_an_unread_question_to_a_session_that_stops_is_a_row_at_once(how: AgentState) -> None:
    """RV-W3's R2: sent on a live link, never read, and its lead's session ends."""
    question = sent(needs_answer=True)
    snap, _ = step(linked(), question)
    assert questions(snap) == []  # running: it is woken for it

    snap, woken = step(snap, AgentFinished(Z_LEAD, how, 4.5))

    (row,) = questions(snap)
    assert (row.node, row.question, row.link, row.channel) == (
        Z_LEAD,
        question.concern_id,
        "k1",
        "brand",
    )
    assert (row.running, row.delivered, row.since) == (False, False, 3.0)
    assert row.summary.endswith("is unread and unanswered - its session is not running")
    assert "the subject" not in row.summary and "the body text" not in row.summary
    assert held_rows(snap) == [] and woken == []


def test_an_unread_question_sent_to_a_session_already_down_is_a_row_at_once() -> None:
    question = sent(needs_answer=True)
    snap, _ = step(_ended(linked(), Z), question)

    (row,) = questions(snap)
    assert row.question == question.concern_id and not row.delivered


def test_an_unread_question_to_a_session_not_in_this_run_is_a_row_at_once() -> None:
    question = sent(needs_answer=True)
    snap, _ = step(_fresh(z_running=False), BoardRestored(S, _logged(question), _CLOCK))

    (row,) = questions(snap)
    assert row.question == question.concern_id and not row.delivered
    assert held_rows(snap) == []  # the link hands it over: not held mail


def test_an_unread_question_to_a_recalled_session_is_a_row_at_once() -> None:
    from pptmstr.intents import SessionRecalled
    from pptmstr.model import Recalled

    question = sent(needs_answer=True)
    snap = Snapshot.empty()
    snap, _ = step(snap, _spawned(LEAD, None))
    snap, _ = step(snap, SessionRecalled(Z, "a task", "claude-sonnet-5", Recalled()))
    snap, _ = step(snap, ChannelsRestored((_K1,)))
    snap, _ = step(snap, BoardRestored(S, _logged(question), _CLOCK))

    (row,) = questions(snap)
    assert row.node == Z_LEAD and not row.running and not row.delivered


def test_a_resume_wakes_the_lead_for_an_unread_question_and_takes_its_row_away() -> None:
    question = sent(needs_answer=True)
    snap, _ = step(linked(), question)
    snap, _ = step(snap, AgentFinished(Z_LEAD, AgentState.FAILED, 4.5))
    assert questions(snap) != []

    snap, _ = step(snap, _spawned(Z_LEAD, None))
    snap, woken = step(snap, SessionSettled(Z))

    assert questions(snap) == [] and wake(Z) in woken
    # Read: it is under the age from that read, and no longer unread.
    snap, _ = step(snap, dataclasses.replace(read(), at=7.0))
    assert questions(snap) == []
    (owed,) = snap.awaiting_answer()
    assert owed.delivered_at == 7.0


@pytest.mark.parametrize("ending", ["withdrawn", "closed", "answered"])
def test_an_unread_questions_row_ends_with_a_withdrawal_a_close_or_an_answer(ending: str) -> None:
    question = sent(needs_answer=True)
    snap, _ = step(linked(), question)
    snap, _ = step(snap, AgentFinished(Z_LEAD, AgentState.FAILED, 4.5))
    assert questions(snap) != []

    if ending == "withdrawn":
        snap, _ = step(snap, ConcernWithdrawn(question.concern_id))
    elif ending == "closed":
        snap, _ = step(snap, ChannelClosed("k1", 9.0))
    else:
        # Read, then answered, as the recipient's restored log would bring it.
        snap, _ = run(snap, read(), sent(by=Z_LEAD, re=question.concern_id))

    assert questions(snap) == []
    assert held_rows(snap) == []  # a link closed in its log raises no held-mail row either


@pytest.mark.parametrize("down", [False, True], ids=["aged", "recipient-down"])
def test_a_read_question_its_sender_withdraws_ends_its_row_and_is_owed_nothing(down: bool) -> None:
    """
    P3: the case-study question, read and then dismissed by its asker.
    """
    snap, cid = _asked()
    if down:
        snap, _ = step(snap, AgentFinished(Z_LEAD, AgentState.FAILED, 4.5))
    else:
        snap, _ = step(snap, PeerQuestionAged(cid))
    assert [r.question for r in questions(snap)] == [cid]

    snap, woken = step(snap, ConcernWithdrawn(cid, reason="never mind", node_id=LEAD))

    assert questions(snap) == [] and snap.awaiting_answer() == ()
    assert woken == []
    assert snap.detach_refusal(Z) is not DetachRefusal.OWES


def test_a_withdrawn_question_is_not_named_at_its_recipients_settle() -> None:
    snap, cid = _asked()
    snap, _ = step(snap, ConcernWithdrawn(cid, reason="never mind", node_id=LEAD))

    snap, woken = step(snap, SessionSettled(Z))

    assert snap.peer_owing(Z, snap.channels["k1"]) == ()
    assert not any(w.session_id == Z for w in woken)


def test_a_withdrawal_after_delivery_restores_as_owed_nothing_and_raises_no_row() -> None:
    question = sent(needs_answer=True)
    board = _logged(
        question, read(), ConcernWithdrawn(question.concern_id, reason="x", node_id=LEAD)
    )
    (logged,) = board.concerns
    assert logged.state is ConcernState.WITHDRAWN and not logged.contested

    snap, woken = step(_fresh(), BoardRestored(S, board, _CLOCK))
    snap, settled = step(snap, SessionSettled(Z))

    assert snap.awaiting_answer() == () and questions(snap) == []
    assert snap.peer_owing(Z, snap.channels["k1"]) == ()
    assert not any(w.session_id == Z for w in (*woken, *settled))


def test_an_unread_question_already_answered_raises_no_row() -> None:
    """RV-W3d 5: a reply may cite a question its sender was never handed."""
    question = sent(needs_answer=True)
    snap, _ = run(linked(), question, AgentFinished(Z_LEAD, AgentState.FAILED, 4.5))
    assert questions(snap) != []

    snap, _ = step(snap, sent(by=Z_LEAD, re=question.concern_id))

    assert snap.concerns[question.concern_id].state is ConcernState.POSTED
    assert snap.questions_unread() == () and questions(snap) == []


def test_an_unread_question_answered_by_the_asker_itself_is_still_unread() -> None:
    """The same rule as ``awaiting_answer``: only the recipient's session answers."""
    question = sent(needs_answer=True)
    snap, _ = run(
        linked(),
        question,
        AgentFinished(Z_LEAD, AgentState.FAILED, 4.5),
        sent(by=LEAD, re=question.concern_id),
    )

    assert [c.id for c in snap.questions_unread()] == [question.concern_id]
    assert [r.question for r in questions(snap)] == [question.concern_id]


def test_a_question_that_needs_no_answer_raises_no_row_when_its_lead_is_down() -> None:
    snap, _ = step(linked(), sent())
    snap, _ = step(snap, AgentFinished(Z_LEAD, AgentState.FAILED, 4.5))
    assert questions(snap) == []


def test_an_unread_question_on_a_link_this_run_does_not_hold_is_held_mail_only() -> None:
    """The two rows do not overlap: mail no link hands over is the held-mail row's."""
    snap = _unheld(sent(needs_answer=True), z_running=False)

    assert questions(snap) == []
    (row,) = held_rows(snap)
    assert len(row.messages) == 1


def test_the_inbox_says_why_each_row_is_up() -> None:
    snap, cid = _asked()
    aged, _ = step(snap, PeerQuestionAged(cid))
    down, _ = step(snap, AgentFinished(Z_LEAD, AgentState.FAILED, 4.5))

    (up,) = questions(aged)
    (gone,) = questions(down)
    running, not_running = inbox._peer_question_body(up), inbox._peer_question_body(gone)

    assert "longer ago than the age" in running and "not running" not in running
    assert "not running" in not_running and "does not wait for the age" in not_running
    assert "resumed" in not_running


def test_the_inbox_says_who_sees_a_question_row() -> None:
    """RV-W3 3: the asker is not told, and the lead asked sees it in its digest."""
    snap, cid = _asked()
    aged, _ = step(snap, PeerQuestionAged(cid))
    (row,) = questions(aged)

    body = inbox._peer_question_body(row)

    assert "Neither lead" not in body
    assert "The lead that asked is not told" in body and "read_digest" in body
    (owed,) = digest.digest(aged, Z).waiting
    assert owed.kind is ObligationKind.PEER_QUESTION


def test_the_inbox_says_an_unread_question_was_never_read() -> None:
    question = sent(needs_answer=True)
    snap, _ = run(linked(), question, AgentFinished(Z_LEAD, AgentState.FAILED, 4.5))
    (row,) = questions(snap)

    body = inbox._peer_question_body(row)

    assert "has not read it" in body and "does not wait for the age" in body
    assert "woken for it" in body
    assert "from its delivery" not in body
    # RV-W3d 3 and 4: nothing emits a peer withdrawal, and a session that is not
    # running cannot call read_digest; a resume takes the row away first.
    assert "withdraw" not in body and "read_digest" not in body


def test_only_a_running_leads_question_row_says_its_digest_lists_it() -> None:
    """RV-W3d 3: every variant's closing sentence is true."""
    snap, cid = _asked()
    aged, _ = step(snap, PeerQuestionAged(cid))
    read_then_down, _ = step(snap, AgentFinished(Z_LEAD, AgentState.FAILED, 4.5))
    unread_down, _ = run(
        linked(), sent(needs_answer=True), AgentFinished(Z_LEAD, AgentState.FAILED, 4.5)
    )

    (running,) = questions(aged)
    (delivered,) = questions(read_then_down)
    (unread,) = questions(unread_down)

    assert "read_digest lists it" in inbox._peer_question_body(running)
    assert digest.digest(aged, Z).waiting != ()
    for row in (delivered, unread):
        body = inbox._peer_question_body(row)
        assert "read_digest" not in body
        assert "The lead that asked is not told of this row" in body


def test_a_question_rows_departure_names_its_askers_withdrawal() -> None:
    """
    RV-P4 2: since P3 the asking lead's ``withdraw_peer_message`` ends the row
    after the question was read, and the departure line lists it among the
    ways the row ends.
    """
    snap, cid = _asked()
    down, _ = step(snap, AgentFinished(Z_LEAD, AgentState.FAILED, 4.5))
    (question_row,) = questions(down)

    withdrawn, _ = step(down, ConcernWithdrawn(cid, node_id=LEAD, peer_only=True))

    assert not questions(withdrawn)
    assert "its asker withdrew it" in inbox.departure_line(withdrawn, question_row)


def test_a_question_rows_departure_and_keys_say_what_is_true() -> None:
    """RV-W3 4: a resume before the age ends the row and not what is owed."""
    snap, cid = _asked()
    down, _ = step(snap, AgentFinished(Z_LEAD, AgentState.FAILED, 4.5))
    (row,) = questions(down)

    gone = inbox.departure_line(down, row)
    keys = inbox.keys_hint(row)

    assert "no longer owed" not in gone and "no longer shown" in gone
    assert "resumed before the age" in gone
    assert "resume" in keys


def test_a_held_wakes_departure_names_no_board_moving() -> None:
    """Only the operator's act on the link ends a chain (W3a): no board does."""
    chain = WakeChainHeld(Z_LEAD, 1.0, "s", "k1", "brand")

    line = inbox.departure_line(linked(), chain)

    assert "a board moved" not in line and "acted on the link" in line


# -- Decided 11: the shell's one-shot timer ------------------------------------------


@pytest.fixture
def shell() -> Any:
    bridge = Bridge()
    bridge.start()
    store = Store()
    for node in (LEAD, Z_LEAD):
        store.apply(_spawned(node, None))
    store.apply(ChannelsRestored((_K1,)))
    state = app.AppState(store=store, bridge=bridge, settings=Settings(peer_answer_age_s=60.0))

    def frame(at: float, *intents: Any) -> Snapshot:
        state.frame_now = at
        for intent in intents:
            bridge.emit(intent)
        app.apply_intents(state)
        return store.snapshot()

    try:
        yield state, frame
    finally:
        bridge.stop()


def _asked_at(frame: Any, at: float) -> str:
    question = sent(needs_answer=True)
    frame(at - 1.0, question)
    frame(at, dataclasses.replace(read(), at=at))
    return question.concern_id


def test_the_timer_is_armed_at_delivery_and_fires_at_the_age(shell: Any) -> None:
    state, frame = shell
    cid = _asked_at(frame, 100.0)
    assert state.question_due == {cid: 160.0}

    assert questions(frame(159.9)) == []
    frame(160.0)  # the word is sent here
    (row,) = questions(frame(160.1))  # and applied with the next batch

    assert row.question == cid
    assert state.question_due == {}


def test_the_timer_fires_once(shell: Any) -> None:
    state, frame = shell
    cid = _asked_at(frame, 100.0)
    frame(160.0)
    frame(160.1)
    frame(500.0)

    assert [r.question for r in questions(frame(501.0))] == [cid]
    assert state.question_due == {}


def test_an_answer_before_the_age_disarms_the_timer(shell: Any) -> None:
    state, frame = shell
    cid = _asked_at(frame, 100.0)

    frame(130.0, sent(by=Z_LEAD, re=cid))

    assert state.question_due == {}
    assert questions(frame(1_000.0)) == []


def test_an_answer_after_the_row_ends_it(shell: Any) -> None:
    _, frame = shell
    cid = _asked_at(frame, 100.0)
    frame(160.0)
    assert questions(frame(161.0)) != []

    assert questions(frame(170.0, sent(by=Z_LEAD, re=cid))) == []


def test_the_age_is_the_setting(shell: Any) -> None:
    state, frame = shell
    state.settings = Settings(peer_answer_age_s=5.0)
    cid = _asked_at(frame, 100.0)
    assert state.question_due == {cid: 105.0}


def test_a_restore_re_arms_the_timer_from_the_logged_delivery(shell: Any) -> None:
    """
    A new process: the store has the question only through the asker's restore,
    and the shell's timer runs from the delivery the log recorded, so one
    handed over long enough ago fires at once.
    """
    state, frame = shell
    question = sent(needs_answer=True)
    board = _logged(question)
    (logged,) = board.concerns
    board = dataclasses.replace(
        board,
        concerns=(dataclasses.replace(logged, state=ConcernState.DELIVERED, delivered_at=4_900.0),),
    )

    frame(100.0, BoardRestored(S, board, _CLOCK))

    # Delivered 100 s before the restore's reading, so at 0.0 here: due at 60.0,
    # which has passed, so the word went with this frame.
    assert state.store.snapshot().concerns[question.concern_id].delivered_at == 0.0
    assert state.question_due == {question.concern_id: math.inf}
    (row,) = questions(frame(100.1))
    assert row.question == question.concern_id


def test_a_recipient_that_fails_is_a_row_at_once_and_its_resume_runs_the_age_from_delivery(
    shell: Any,
) -> None:
    state, frame = shell
    cid = _asked_at(frame, 100.0)

    (row,) = questions(frame(110.0, AgentFinished(Z_LEAD, AgentState.FAILED, 110.0)))
    assert row.question == cid and not row.running
    assert state.question_due == {cid: 160.0}  # still armed: the age stands behind the row

    assert questions(frame(120.0, _spawned(Z_LEAD, None))) == []
    assert questions(frame(159.9)) == []
    frame(160.0)
    (row,) = questions(frame(160.1))
    assert row.question == cid and row.running


def test_an_answer_applied_and_withdrawn_around_the_word_leaves_the_timer_armed(
    shell: Any,
) -> None:
    """
    RV-W3's R5: one batch applies an answer, the word (let go of, the question
    being answered at that step) and the answer's withdrawal. The question is
    owed again and was never aged, so its row must still come.
    """
    state, frame = shell
    cid = _asked_at(frame, 100.0)
    frame(160.0)  # the word is sent; the timer is parked
    assert state.question_due == {cid: math.inf}
    # The word is in the queue: put the answer ahead of it and the withdrawal after.
    (word,) = state.bridge.drain()
    answer = sent(by=Z_LEAD, re=cid)
    snap = frame(160.05, answer, word, ConcernWithdrawn(answer.concern_id))
    assert questions(snap) == [] and snap.questions_aged == frozenset()

    frame(160.1)  # re-armed at a due that has passed: the word goes again
    (row,) = questions(frame(160.2))

    assert row.question == cid


# -- held peer mail ------------------------------------------------------------------


def _unheld(*messages: Intent, z_running: bool = True) -> Snapshot:
    """
    A new process that restored ``s``'s log of ``messages`` and not the link
    they were sent over (withheld, or not restored): held mail.
    """
    snap = Snapshot.empty()
    snap, _ = step(snap, _spawned(LEAD, None))
    if z_running:
        snap, _ = step(snap, _spawned(Z_LEAD, None))
    snap, _ = step(snap, BoardRestored(S, _logged(*messages), _CLOCK))
    return snap


def test_mail_on_a_link_this_run_does_not_hold_is_a_row_naming_the_channel_and_the_count() -> None:
    snap = _unheld(sent(), sent(quiet=True))

    (row,) = held_rows(snap)
    assert (row.node, row.link, row.channel, len(row.messages)) == (Z_LEAD, "k1", "brand", 2)
    assert row.summary.startswith("#brand: 2 message(s)")
    assert "is not open in this run" in row.summary and row.why in row.summary
    assert "the subject" not in row.summary and "the body text" not in row.summary


def test_mail_on_a_link_the_operator_closed_raises_no_row_and_stays_held() -> None:
    """
    RV-W3 8, the lead's ruling: the close was the operator's act, and a closed
    link is never live again, so a row would return after every restart.
    """
    snap, _ = run(linked(), sent(), sent(quiet=True))

    snap, woken = step(snap, ChannelClosed("k1", 9.0))

    assert held_rows(snap) == [] and woken == []
    assert len(snap.peer_held()) == 2  # still held, and kept in the store
    assert all(c.state is ConcernState.POSTED for c in snap.peer_held())


def test_mail_on_a_link_closed_in_an_earlier_run_raises_no_row_after_a_restart() -> None:
    snap = _unheld(sent())
    assert held_rows(snap) != []

    snap, _ = step(snap, ChannelsRestored((dataclasses.replace(_K1, closed_at=9.0),)))

    assert held_rows(snap) == [] and len(snap.peer_held()) == 1


def _misnamed() -> Snapshot:
    """
    A message naming its live link under another name. Restore turns such a
    record away, so it is put in the store by hand here; a step derives the
    rows again.
    """
    message = sent()
    snap, _ = step(linked(), message)
    stored = snap.concerns[message.concern_id]
    snap = dataclasses.replace(
        snap,
        concerns=MappingProxyType(
            {**snap.concerns, stored.id: dataclasses.replace(stored, channel="other")}
        ),
    )
    snap, _ = step(snap, PeerQuestionAged("c-none"))
    return snap


def test_mail_on_a_live_link_that_does_not_hand_it_over_is_a_row_that_says_so() -> None:
    """
    The third of ``model.why_held``'s cases (``_misnamed``).
    """
    (row,) = held_rows(_misnamed())
    assert row.why == "does not hand these over: they name it #other"
    assert row.channel == "brand"
    assert row.summary.startswith("#brand: 1 message(s) for z held - the link does not hand")


def test_mail_on_a_live_link_is_not_held() -> None:
    snap, _ = run(linked(), sent())
    assert held_rows(snap) == [] and snap.peer_held() == ()


def test_mail_read_before_the_close_is_not_held() -> None:
    snap, _ = run(linked(), sent(), read(), ChannelClosed("k1", 9.0))
    assert held_rows(snap) == []


def test_mail_whose_link_this_run_does_not_hold_is_a_row_until_the_link_comes() -> None:
    """Restored from the sender's log before its link: withheld, or not restored."""
    message = sent()
    board = _logged(message)
    snap = Snapshot.empty()
    for node in (LEAD, Z_LEAD):
        snap, _ = step(snap, _spawned(node, None))

    snap, _ = step(snap, BoardRestored(S, board, _CLOCK))

    (row,) = held_rows(snap)
    assert row.messages == (message.concern_id,)
    assert "not open in this run" in row.summary

    snap, _ = step(snap, ChannelsRestored((_K1,)))

    assert held_rows(snap) == []


def test_a_dismissed_row_goes_and_the_mail_stays() -> None:
    snap = _unheld(sent())
    (row,) = held_rows(snap)

    snap, woken = step(snap, HeldMailDismissed(frozenset(row.messages)))

    assert held_rows(snap) == [] and woken == []
    assert [c.id for c in snap.peer_held()] == list(row.messages)
    assert all(snap.concerns[cid].state is ConcernState.POSTED for cid in row.messages)


def test_mail_not_among_what_was_dismissed_still_raises_a_row() -> None:
    """
    A dismissal is of the mail its row counted, not of the link: anything else
    held there is shown. Here the operator dismissed a row that counted one of
    two messages, as a row drawn before a restore brought the second would.
    """
    early, late = sent(), sent()
    snap = _unheld(early, late)

    snap, _ = step(snap, HeldMailDismissed(frozenset({early.concern_id})))

    (row,) = held_rows(snap)
    assert row.messages == (late.concern_id,)
    assert row.summary.startswith("#brand: 1 message(s)")
    assert snap.held_dismissed == {early.concern_id}


def test_a_withdrawn_message_is_not_held() -> None:
    message = sent()
    snap = _unheld(message)
    assert held_rows(snap) != []

    snap, _ = step(snap, ConcernWithdrawn(message.concern_id))

    assert held_rows(snap) == [] and snap.held_dismissed == frozenset()


def test_the_held_mail_row_says_what_is_true_in_a_digest() -> None:
    snap = _unheld(sent())

    (waiting,) = digest.digest(snap, Z).waiting

    assert waiting.kind is ObligationKind.PEER_HELD
    text = bus._waiting_line(waiting)
    assert "is waiting" not in text and "does not hand it over in this run" in text


def test_neither_new_intent_is_logged() -> None:
    snap, cid = _asked()
    after, _ = _apply(snap, PeerQuestionAged(cid), 5.0)
    assert outcomes(snap, PeerQuestionAged(cid), after, 5.0) == ()
    gone = HeldMailDismissed(frozenset({"c-x"}))
    assert outcomes(snap, gone, _apply(snap, gone, 5.0)[0], 5.0) == ()


def test_the_question_row_text_in_a_digest_is_its_own() -> None:
    snap, cid = _asked()
    snap, _ = step(snap, PeerQuestionAged(cid))

    (owed,) = digest.digest(snap, Z).waiting

    assert owed.kind is ObligationKind.PEER_QUESTION
    line = bus._waiting_line(owed)
    assert "is waiting" not in line
    # True of both reasons a question row is up.
    assert "older than the age" in line and "session is not running" in line
    assert "for longer than the age" not in line


# -- Round four, 2: a question owed across a restart, in either order of resume -------


def _both_logs(*intents: Intent) -> dict[str, boardlog.Board]:
    """Each session's board log after ``intents``, as the reducer's facts wrote it."""
    snap = linked()
    records: dict[str, list[boardlog.Record]] = {S: [], Z: []}
    for intent in intents:
        after, _ = _apply(snap, intent, 5.0)
        for fact in outcomes(snap, intent, after, 5.0):
            record = boardlog.decode(boardlog.encode(fact.record))
            assert not isinstance(record, boardlog.Malformed)
            records.setdefault(fact.session_id, []).append(record)
        snap = after
    return {sid: boardlog.restore(rs) for sid, rs in records.items()}


def _question_on_disk(*, answered: bool) -> tuple[dict[str, boardlog.Board], str]:
    """
    An earlier run: ``s`` asked ``z``, ``z`` read it, and answered or did not.
    The question and its delivery are in ``s``'s log, the answer in ``z``'s.
    """
    question = sent(needs_answer=True)
    intents: list[Intent] = [question, read()]
    if answered:
        intents.append(sent(by=Z_LEAD, re=question.concern_id))
    logs = _both_logs(*intents)
    assert [c.id for c in logs[S].concerns] == [question.concern_id]
    return logs, question.concern_id


def _z_comes_back(
    snap: Snapshot, logs: dict[str, boardlog.Board]
) -> tuple[Snapshot, list[SessionWoken]]:
    """``z`` is resumed: announced, its log restored, then settled (``app._launch``)."""
    snap, _ = step(snap, _spawned(Z_LEAD, None))
    return run(snap, BoardRestored(Z, logs[Z], _CLOCK), SessionSettled(Z))


def test_recipient_first_then_asker_the_askers_restore_wakes_it_for_the_question() -> None:
    logs, cid = _question_on_disk(answered=False)
    snap, settled = _z_comes_back(_fresh(z_running=False), logs)
    assert settled == [] and snap.owed_answers(Z, _K1) == ()

    snap, woken = step(snap, BoardRestored(S, logs[S], _CLOCK))

    assert wake(Z) in woken
    k1 = snap.channels["k1"]
    assert snap.peer_owing(Z, k1) == (cid,) and snap.peer_wakes["k1"].chain == 0
    assert f"question(s) {cid} on #brand" in bus.wake_line(snap, wake(Z))
    # No loop: the shell's word ends it and asks nothing, and a second restore
    # of the same log brings nothing back to name.
    snap, again = step(snap, PeerWakeSent(Z, "k1"))
    assert again == []
    snap, again = step(snap, BoardRestored(S, logs[S], _CLOCK))
    assert wake(Z) not in again and snap.peer_owing(Z, k1) == ()


def test_asker_first_then_recipient_the_recipients_settle_wakes_it() -> None:
    logs, cid = _question_on_disk(answered=False)
    snap, woken = step(_fresh(z_running=False), BoardRestored(S, logs[S], _CLOCK))
    assert not any(w.session_id == Z for w in woken)

    snap, settled = _z_comes_back(snap, logs)

    assert settled == [wake(Z)] and snap.peer_owing(Z, snap.channels["k1"]) == (cid,)
    snap, again = step(snap, PeerWakeSent(Z, "k1"))
    assert again == [] and snap.peer_owing(Z, snap.channels["k1"]) == ()


@pytest.mark.parametrize("asker_first", [False, True], ids=["recipient-first", "asker-first"])
def test_a_recipient_that_answered_in_an_earlier_run_is_not_told_it_owes(
    asker_first: bool,
) -> None:
    """Build 5: the recipient's own log holds its answer, restored ahead of its settle."""
    logs, cid = _question_on_disk(answered=True)
    snap = _fresh(z_running=False)
    woken: list[SessionWoken] = []
    if asker_first:
        snap, _ = step(snap, BoardRestored(S, logs[S], _CLOCK))
        snap, woken = _z_comes_back(snap, logs)
    else:
        snap, _ = _z_comes_back(snap, logs)
        snap, woken = step(snap, BoardRestored(S, logs[S], _CLOCK))

    assert cid in snap.concerns
    assert snap.owed_answers(Z, EVERY_CHANNEL) == ()
    assert snap.peer_wakes.get("k1", None) is None or Z not in snap.peer_wakes["k1"].owing
    assert wake(Z) not in woken


# -- Round four, 2: a read lists the questions owed again, and marks nothing ---------


def _owed_after_reads() -> tuple[Snapshot, str]:
    snap, cid = _asked()
    assert [c.id for c in snap.owed_answers(Z, EVERY_CHANNEL)] == [cid]
    return snap, cid


def _read(snap: Snapshot, intent: Intent) -> tuple[Snapshot, Any]:
    from pptmstr.effects import PeerInboxDelivered

    after, effects = _apply(snap, intent, 9.0)
    (reply,) = [e for e in effects if isinstance(e, PeerInboxDelivered)]
    return after, reply


def test_a_read_lists_an_owed_question_again_after_the_new_mail_and_marks_nothing() -> None:
    snap, cid = _owed_after_reads()
    fresh = sent()
    snap, _ = step(snap, fresh)

    after, reply = _read(snap, read())

    assert [c.id for c in reply.concerns] == [fresh.concern_id]
    assert [c.id for c in reply.owed] == [cid]
    assert after.concerns[cid] is snap.concerns[cid]
    # Only the new message's delivery reaches a log; the owed one's is as it was.
    facts = outcomes(snap, read(), after, 9.0)
    assert [f.record for f in facts] == [boardlog.Delivered(4.0, (fresh.concern_id,))]
    text = bus._peer_messages_text(reply, None, Z)
    assert text.index(f"message {fresh.concern_id}") < text.index(f"message {cid}")
    owed_header = next(line for line in text.splitlines() if f"message {cid}" in line)
    assert "ALREADY READ, STILL OWED AN ANSWER" in owed_header
    assert "NEEDS AN ANSWER ·" not in owed_header and "the body text" in text
    assert "reading them here marks nothing" in text


def test_a_read_of_the_owed_question_alone_still_says_whose_words_are_quoted() -> None:
    snap, cid = _owed_after_reads()
    _, reply = _read(snap, read())
    text = bus._peer_messages_text(reply, None, Z)
    assert reply.concerns == () and [c.id for c in reply.owed] == [cid]
    assert bus._QUOTE_LEGEND in text and "send_to_peer(channel, re=<message id>)" in text


def test_a_read_lists_the_owed_question_every_time_until_it_is_answered() -> None:
    snap, cid = _owed_after_reads()
    snap, first = _read(snap, read())
    snap, second = _read(snap, read())
    assert [c.id for c in first.owed] == [c.id for c in second.owed] == [cid]

    snap, _ = step(snap, sent(by=Z_LEAD, re=cid))
    _, third = _read(snap, read())

    assert third.owed == ()


def test_a_read_of_another_channel_does_not_list_it() -> None:
    snap, _ = _owed_after_reads()
    _, reply = _read(snap, read(on="elsewhere"))
    assert reply.owed == ()


def test_a_read_names_the_owed_questions_sender() -> None:
    snap, cid = _owed_after_reads()
    _, reply = _read(snap, read(on="brand"))
    assert [c.id for c in reply.owed] == [cid]
    assert S in {p.session for p in reply.peers}


def test_a_question_this_read_hands_over_is_not_also_listed_as_owed() -> None:
    snap, cid = _owed_after_reads()
    new = sent(needs_answer=True)
    snap, _ = step(snap, new)

    _, reply = _read(snap, read())

    assert [c.id for c in reply.concerns] == [new.concern_id]
    assert [c.id for c in reply.owed] == [cid]


# -- P4: whose move it is on a link -------------------------------------------------


def _k1(snap: Snapshot) -> Channel:
    return snap.channels["k1"]


def _parked(tool: str, args: dict[str, Any], node: NodeId = LEAD, at: float = 2.5) -> Any:
    return ApprovalRequested(
        node,
        PendingApproval(
            id=f"p-{tool[-6:]}",
            node=node,
            tool_name=tool,
            tool_use_id="tu",
            raw_args=args,
            summary="as the driver had it",
            requested_at=at,
        ),
    )


def test_a_link_nobody_owes_anything_on_is_idle() -> None:
    assert linked().whose_move(_k1(linked())) == WhoseMove(MoveOwner.IDLE)


def test_mail_that_is_not_quiet_is_its_readers_move_from_when_it_was_sent() -> None:
    snap, _ = step(linked(), sent())
    assert snap.whose_move(_k1(snap)) == WhoseMove(MoveOwner.MEMBER, Z, 3.0)


def test_quiet_mail_alone_asks_nothing_of_anybody() -> None:
    snap, _ = step(linked(), sent(quiet=True))
    assert snap.whose_move(_k1(snap)) == WhoseMove(MoveOwner.IDLE)


def test_a_question_read_and_not_answered_is_its_readers_move_from_its_delivery() -> None:
    snap, cid = _asked()
    assert snap.whose_move(_k1(snap)) == WhoseMove(MoveOwner.MEMBER, Z, 4.0)

    snap, _ = step(snap, sent(by=Z_LEAD, re=cid, quiet=True))
    assert snap.whose_move(_k1(snap)) == WhoseMove(MoveOwner.IDLE)


@pytest.mark.parametrize("z_running", [True, False], ids=["running", "not-running"])
def test_a_quiet_question_counts_as_a_question_as_its_row_does(z_running: bool) -> None:
    """
    RV-P4 4: the bus refuses quiet with needs-answer, but a foreign log can
    carry the pair, and NEEDS YOU raises a row for it (``questions_unread``).
    """
    snap = linked() if z_running else _fresh(z_running=False)
    snap, _ = step(snap, sent(needs_answer=True, quiet=True))
    (stored,) = snap.concerns.values()
    assert stored.quiet and stored.needs_answer

    if z_running:
        assert snap.whose_move(_k1(snap)) == WhoseMove(MoveOwner.MEMBER, Z, 3.0)
    else:
        assert len(questions(snap)) == 1
        assert snap.whose_move(_k1(snap)) == WhoseMove(MoveOwner.UNREACHABLE, Z, 3.0)


def test_held_mail_on_a_live_link_is_the_operators_move_while_its_row_stands() -> None:
    """
    RV-P4 3, the lead's ruling: NEEDS YOU's held-mail row is the operator's,
    so the link's mark says so, and stops saying so when the row is dismissed.
    """
    snap = _misnamed()
    (row,) = held_rows(snap)

    assert snap.whose_move(_k1(snap)) == WhoseMove(MoveOwner.OPERATOR, since=row.since)

    snap, _ = step(snap, HeldMailDismissed(concerns=frozenset(row.messages)))
    assert held_rows(snap) == []
    assert snap.whose_move(_k1(snap)) == WhoseMove(MoveOwner.IDLE)


def test_a_read_message_without_a_question_is_nobodys_move() -> None:
    snap, _ = run(linked(), sent(), read())
    assert snap.whose_move(_k1(snap)) == WhoseMove(MoveOwner.IDLE)


def test_a_member_that_owes_and_is_not_running_is_unreachable() -> None:
    snap, _ = _asked()
    snap, _ = step(snap, AgentFinished(Z_LEAD, AgentState.FAILED, 4.5))
    assert snap.whose_move(_k1(snap)) == WhoseMove(MoveOwner.UNREACHABLE, Z, 4.0)


def test_a_question_withdrawn_after_it_was_read_is_nobodys_move() -> None:
    """
    P3: a retracted message is WITHDRAWN with ``delivered_at`` still set, and is
    neither owed nor unread.
    """
    snap, cid = _asked()
    snap, _ = step(snap, ConcernWithdrawn(cid, "asked the wrong lead", node_id=LEAD))
    assert snap.concerns[cid].state is ConcernState.WITHDRAWN
    assert snap.concerns[cid].delivered_at is not None
    assert snap.whose_move(_k1(snap)) == WhoseMove(MoveOwner.IDLE)


def test_an_open_decision_on_the_link_is_the_operators_move_ahead_of_any_lead() -> None:
    snap, _ = run(linked(), sent(), raise_on(did="d-1"))
    assert snap.whose_move(_k1(snap)) == WhoseMove(MoveOwner.OPERATOR, since=3.0)

    snap, _ = step(snap, DecisionAnswered("d-1", "yes"))
    assert snap.whose_move(_k1(snap)) == WhoseMove(MoveOwner.MEMBER, Z, 3.0)


def test_a_decision_raised_on_no_channel_is_not_the_links() -> None:
    snap, _ = step(linked(), raise_on(on=None, did="d-1"))
    assert snap.whose_move(_k1(snap)) == WhoseMove(MoveOwner.IDLE)


@pytest.mark.parametrize(
    "tool, args",
    [
        ("mcp__pptmstr__send_to_peer", {"channel": "brand", "subject": "x"}),
        ("mcp__pptmstr__raise_decision", {"question": "q?", "channel": "brand"}),
    ],
    ids=["send", "channel-decision"],
)
def test_a_call_parked_across_the_link_is_the_operators_move(
    tool: str, args: dict[str, Any]
) -> None:
    snap, _ = step(linked(), _parked(tool, args))
    assert snap.whose_move(_k1(snap)) == WhoseMove(MoveOwner.OPERATOR, since=2.5)


def test_a_call_parked_on_another_name_or_by_a_non_member_is_not_the_links() -> None:
    other = ("mcp__pptmstr__send_to_peer", {"channel": "elsewhere", "subject": "x"})
    snap, _ = step(linked(), _parked(*other))
    outsider = ("mcp__pptmstr__send_to_peer", {"channel": "brand", "subject": "x"})
    snap, _ = step(snap, _parked(*outsider, node=C_LEAD))
    assert snap.whose_move(_k1(snap)) == WhoseMove(MoveOwner.IDLE)


def test_a_parked_withdrawal_is_the_operators_move_on_the_withdrawn_messages_link() -> None:
    """
    Today a withdrawal never parks (``approval._BUS_AUTO``). Once one carrying
    a reason does (P3-fix), it is on the operator, on its message's link.
    """
    snap, cid = _asked()
    snap, _ = step(snap, _parked(WITHDRAW_PEER_TOOL, {"message_id": cid, "reason": "r"}))
    assert snap.whose_move(_k1(snap)) == WhoseMove(MoveOwner.OPERATOR, since=2.5)


def test_the_withdrawal_tool_name_is_the_one_the_gate_knows() -> None:
    from pptmstr import approval

    assert WITHDRAW_PEER_TOOL in approval._BUS_AUTO
    assert WITHDRAW_PEER_TOOL == f"mcp__{approval._BUS_SERVER}__withdraw_peer_message"


def test_a_held_wake_is_the_operators_move_from_when_it_was_held() -> None:
    snap, _ = step(linked(), sent())
    snap = dataclasses.replace(
        snap, peer_wakes=MappingProxyType({"k1": PeerWakes(chain=6, held=((Z, 3.5),))})
    )
    assert snap.whose_move(_k1(snap)) == WhoseMove(MoveOwner.OPERATOR, since=3.5)


def test_an_unreachable_member_comes_before_a_running_one_and_age_breaks_a_tier() -> None:
    """
    Both leads owe: s has unread mail from z, and z owes an answer. z down
    is the operator's to resume, so it is named though s's mail is older.
    """
    older = dataclasses.replace(sent(by=Z_LEAD), at=2.0)
    snap, _ = run(linked(), older, sent(needs_answer=True), read())
    assert snap.whose_move(_k1(snap)) == WhoseMove(MoveOwner.MEMBER, S, 2.0)

    snap, _ = step(snap, AgentFinished(Z_LEAD, AgentState.FAILED, 4.5))
    assert snap.whose_move(_k1(snap)) == WhoseMove(MoveOwner.UNREACHABLE, Z, 4.0)


def test_a_closed_link_is_idle_whatever_waited_on_it() -> None:
    snap, _ = run(linked(), sent(needs_answer=True), ChannelClosed("k1", 4.5))
    assert snap.whose_move(_k1(snap)) == WhoseMove(MoveOwner.IDLE)


# -- P4-perf: whose_move read from one index per frame ---------------------------------


def _scanning_whose_move(snap: Snapshot, link: Channel) -> WhoseMove:
    """
    The oracle: ``whose_move`` as it read the store before ``PeerIndex``, one
    scan of the table per member per call through the readings it names.
    """
    parked = [
        p.requested_at
        for rec in snap.nodes.values()
        for p in rec.pending
        if snap.parked_link(p) == link.id
    ]
    parked += [d.raised_at for d in snap.decisions.values() if d.is_open and d.link == link.id]
    wakes = snap.peer_wakes.get(link.id)
    if wakes is not None:
        parked += [since for _, since in wakes.held]
    parked += [o.since for o in snap.needs_you if isinstance(o, PeerMailHeld) and o.link == link.id]
    if parked:
        return WhoseMove(MoveOwner.OPERATOR, since=min(parked))
    owed: list[tuple[bool, float, str]] = []
    for member in sorted(link.members):
        times = [c.delivered_at or 0.0 for c in snap.owed_answers(member, link)]
        times += [
            c.posted_at for c in snap.peer_unread(member, link) if c.needs_answer or not c.quiet
        ]
        if times:
            owed.append((snap.wakeable(member), min(times), member))
    if not owed:
        return WhoseMove(MoveOwner.IDLE)
    running, since, member = min(owed)
    return WhoseMove(MoveOwner.MEMBER if running else MoveOwner.UNREACHABLE, member, since)


def _random_world(rng: Any) -> tuple[Snapshot, list[Channel]]:
    """
    A store state no step necessarily reaches: three leads and a builder, a
    live link, a closed one and a live one under the closed one's name, then
    mail, decisions, held wakes, parked calls, held-mail rows and session
    states drawn at random -- including what only a foreign log carries
    (quiet with needs-answer, a link misnamed, a link the store does not
    hold, a delivered question with no ``delivered_at``, mail to a builder).
    Times come from a few values, so ties are common. Returns the links to
    ask about: every one the store holds, and one it does not.
    """
    from pptmstr.model import Answered, Concern, Decision, Standing

    snap = linked()
    pair = frozenset({Z, C})
    snap, _ = _apply(snap, ChannelLinked("k2", "ops", pair, "c", 1.0, pair), 1.0)
    if rng.random() < 0.5:
        snap, _ = _apply(snap, ChannelClosed("k2", 1.2), 1.2)
        other = frozenset({S, C})
        snap, _ = _apply(snap, ChannelLinked("k3", "ops", other, "c", 1.3, other), 1.3)
    names = {"k1": "brand", "k2": "ops", "k3": "ops", "k9": "gone"}
    nodes = [LEAD, BUILDER, Z_LEAD, C_LEAD]
    times = [1.0, 2.0, 2.0, 3.0, 4.5]

    def at() -> float:
        return float(rng.choice(times))

    concerns: dict[str, Concern] = {}
    for n in range(rng.randint(0, 24)):
        cid = f"c-{n:012x}"
        sender = rng.choice(nodes)
        recipient = rng.choice([x for x in nodes if x != sender])
        state = rng.choice(list(ConcernState))
        delivered = None if state is ConcernState.POSTED or rng.random() < 0.15 else at()
        if rng.random() < 0.15:
            concerns[cid] = Concern(
                cid, sender, recipient, "s", "b", at(), state, delivered_at=delivered, channel=None
            )
            continue
        link = rng.choice(list(names))
        needs = rng.random() < 0.5
        cites = rng.choice([None, None, "c-ffffffffffff", *concerns])
        concerns[cid] = Concern(
            cid,
            sender,
            recipient,
            "s",
            "b",
            at(),
            state,
            delivered_at=delivered,
            channel=names[link] if rng.random() < 0.85 else "other",
            needs_answer=needs,
            re=cites,
            quiet=rng.random() < (0.2 if needs else 0.4),
            link=link,
        )
    decisions = {}
    for n in range(rng.randint(0, 4)):
        on = rng.choice([None, *names])
        decisions[f"d-{n}"] = Decision(
            id=f"d-{n}",
            asker=rng.choice(nodes),
            question="q?",
            blocks=(),
            if_unanswered="",
            evidence="",
            recommendation="",
            standing=Standing.OPERATORS,
            reversible=None,
            raised_at=at(),
            outcome=None if rng.random() < 0.5 else Answered("yes", 5.0),
            channel=None if on is None else names[on],
            link=on,
        )
    wakes = {
        lid: PeerWakes(held=((rng.choice([S, Z, C]), at()),))
        for lid in names
        if rng.random() < 0.15
    }
    records = dict(snap.nodes)
    tools = ["mcp__pptmstr__send_to_peer", "mcp__pptmstr__raise_decision", WITHDRAW_PEER_TOOL]
    for n in range(rng.randint(0, 4)):
        node = rng.choice(nodes)
        tool = rng.choice(tools)
        args: dict[str, Any] = {"message_id": rng.choice(["c-ffffffffffff", *concerns])}
        if tool != WITHDRAW_PEER_TOOL:
            args = {"channel": rng.choice(["brand", "ops", "gone"]), "subject": "x"}
        pending = PendingApproval(
            id=f"p-{n}",
            node=node,
            tool_name=tool,
            tool_use_id=f"tu-{n}",
            raw_args=args,
            summary="x",
            requested_at=at(),
        )
        records[node] = dataclasses.replace(
            records[node], pending=(*records[node].pending, pending)
        )
    for node in (LEAD, Z_LEAD, C_LEAD):
        roll = rng.random()
        if roll < 0.15:
            records[node] = dataclasses.replace(records[node], state=AgentState.FAILED)
        elif roll < 0.3:
            records[node] = dataclasses.replace(records[node], recalled=Recalled())
    rows = tuple(
        PeerMailHeld(
            node=rng.choice([LEAD, Z_LEAD, C_LEAD]),
            since=at(),
            summary="held",
            link=rng.choice(list(names)),
            channel="brand",
            messages=(),
            why="is closed",
        )
        for _ in range(rng.choice([0, 0, 1, 2]))
    )
    snap = dataclasses.replace(
        snap,
        nodes=MappingProxyType(records),
        concerns=MappingProxyType(concerns),
        decisions=MappingProxyType(decisions),
        peer_wakes=MappingProxyType(wakes),
        needs_you=(*snap.needs_you, *rows),
    )
    stranger = Channel(id="k9", name="gone", members=frozenset({S, Z}), charter="", created_at=1.0)
    return snap, [*snap.channels.values(), stranger]


def test_whose_move_read_from_the_index_is_the_scanning_reading_in_every_state() -> None:
    """
    The equivalence ``PeerIndex`` rests on, over random store states: for
    every link, the index's answer is the scan's, given or built; ``parked_on``
    is the parked calls in store order; ``waiting`` is ``peer_inbox_of``'s
    count for every node. Every tier and owner is reached, so a state space
    that never left one tier does not pass it.
    """
    import random

    reached: set[MoveOwner] = set()
    for seed in range(600):
        snap, links = _random_world(random.Random(seed))
        index = snap.peer_index()
        for link in links:
            want = _scanning_whose_move(snap, link)
            assert snap.whose_move(link, index) == want, (seed, link.id)
            assert snap.whose_move(link) == want, (seed, link.id)
            reached.add(want.owner)
            assert snap.parked_on(link, index) == tuple(
                p
                for rec in snap.nodes.values()
                for p in rec.pending
                if snap.parked_link(p) == link.id
            ), (seed, link.id)
            for node in snap.nodes:
                assert index.waiting.get((node, link.id), 0) == len(
                    snap.peer_inbox_of(node, link)
                ), (seed, link.id, node)
    assert reached == set(MoveOwner)


def test_the_rails_lines_read_from_one_index_are_the_lines_built_alone() -> None:
    """
    ``rail.channel_rows``, given the frame's index or building its own, says
    what the per-card scans said (``peer_inbox_of``'s count and the oracle's
    move), for every card, in random store states.
    """
    import random

    from pptmstr import drawable
    from pptmstr.ui import rail, session_menu

    def scanned(snap: Snapshot, me: str) -> tuple[tuple[Channel, str], ...]:
        out = []
        for link in session_menu.live_links(snap, me):
            peers = ", ".join(session_menu.peer_label(snap, p) for p in sorted(link.members - {me}))
            line = f"#{drawable.text(link.name)} ↔ {peers}"
            waiting = len(snap.peer_inbox_of((me, None), link))
            if waiting:
                line += f" · {waiting} unread"
            move = session_menu.whose_move_text(snap, _scanning_whose_move(snap, link), me)
            out.append((link, f"{line} · {move}"))
        return tuple(out)

    for seed in range(200):
        snap, _ = _random_world(random.Random(seed))
        index = snap.peer_index()
        for rec in snap.nodes.values():
            want = () if rec.node_id[1] is not None else scanned(snap, rec.node_id[0])
            assert rail.channel_rows(snap, rec, index) == want, seed
            assert rail.channel_rows(snap, rec) == want, seed


# -- P4: a question row's dismissal --------------------------------------------------


def test_dismissing_a_question_row_drops_it_and_logs_nothing_and_owes_the_same() -> None:
    snap, cid = _asked()
    aged, _ = step(snap, PeerQuestionAged(cid))
    (row,) = questions(aged)

    dismissal = PeerQuestionDismissed(concern_id=cid, running=row.running)
    after, woken = step(aged, dismissal)

    assert questions(after) == [] and woken == []
    assert outcomes(aged, dismissal, after, 5.0) == ()
    assert [c.id for c in after.awaiting_answer()] == [cid]
    assert after.whose_move(_k1(after)) == WhoseMove(MoveOwner.MEMBER, Z, 4.0)


def test_a_dismissed_down_row_does_not_hide_the_aged_row_after_a_resume() -> None:
    snap, cid = _asked()
    down, _ = step(snap, AgentFinished(Z_LEAD, AgentState.FAILED, 4.5))
    (row,) = questions(down)
    assert row.running is False

    down, _ = step(down, PeerQuestionDismissed(concern_id=cid, running=False))
    assert questions(down) == [] and down.questions_dismissed == {(cid, False)}

    back, _ = run(down, _spawned(Z_LEAD, None), SessionSettled(Z))
    # The resume ended the row the dismissal was for, so it lets go.
    assert back.questions_dismissed == frozenset()
    aged, _ = step(back, PeerQuestionAged(cid))
    (row,) = questions(aged)
    assert (row.question, row.running) == (cid, True)


def test_an_aged_rows_dismissal_lasts_while_it_is_raised_and_goes_with_the_answer() -> None:
    snap, cid = _asked()
    snap, _ = run(snap, PeerQuestionAged(cid), PeerQuestionDismissed(cid, running=True))
    snap, _ = step(snap, sent())  # any later step derives the rows again
    assert questions(snap) == [] and snap.questions_dismissed == {(cid, True)}

    snap, _ = step(snap, sent(by=Z_LEAD, re=cid))
    assert snap.questions_dismissed == frozenset()


def test_an_aged_rows_dismissal_does_not_outlast_its_lead_going_down() -> None:
    """
    Dismissed past its age, then its lead's session stops: the row raised for
    the session being down is shown, and the dismissal lets go, so the aged
    row returns after a resume (the age has passed already).
    """
    snap, cid = _asked()
    snap, _ = run(snap, PeerQuestionAged(cid), PeerQuestionDismissed(cid, running=True))
    assert questions(snap) == []

    down, _ = step(snap, AgentFinished(Z_LEAD, AgentState.FAILED, 4.5))
    assert [(r.question, r.running) for r in questions(down)] == [(cid, False)]
    assert down.questions_dismissed == frozenset()

    back, _ = run(down, _spawned(Z_LEAD, None), SessionSettled(Z))
    assert [(r.question, r.running) for r in questions(back)] == [(cid, True)]


def test_a_dismissal_of_a_question_no_row_is_raised_for_is_not_kept() -> None:
    snap, cid = _asked()
    snap, _ = step(snap, PeerQuestionDismissed(cid, running=True))
    assert snap.questions_dismissed == frozenset()


# -- P4: every peer row in NEEDS YOU has something to press ---------------------------


def _expanded(
    monkeypatch: pytest.MonkeyPatch,
    snap: Snapshot,
    row: Any,
    press: str | None = None,
    **wired: Any,
) -> tuple[list[tuple[str, bool]], list[str], list[Any], list[tuple[str, NodeId]]]:
    """
    ``row`` expanded against a recording imgui: every button with whether it
    was drawn live, every string drawn, what reached the bridge, and which of
    the actions was called with what. ``press`` is the label pressed.
    """
    from unittest.mock import MagicMock

    from imgui_bundle import imgui as real_imgui

    fake = MagicMock()
    fake.get_content_region_avail.return_value = real_imgui.ImVec2(800.0, 400.0)
    dead = [False]
    fake.begin_disabled.side_effect = lambda *a: dead.__setitem__(0, True)
    fake.end_disabled.side_effect = lambda *a: dead.__setitem__(0, False)
    buttons: list[tuple[str, bool]] = []

    def button(label: str, *_a: Any) -> bool:
        buttons.append((label, not dead[0]))
        return label == press

    fake.button.side_effect = button
    monkeypatch.setattr(inbox, "imgui", fake)
    emitted: list[Any] = []
    bridge = SimpleNamespace(emit=emitted.append)
    called: list[tuple[str, NodeId]] = []
    actions = inbox.InboxActions(
        send=lambda n, t: None,
        interrupt=lambda n: None,
        close=lambda n: None,
        dismiss=lambda n: None,
        relaunch=lambda spec: None,
        **{
            name: (lambda node, name=name: called.append((name, node)))
            for name in wired.get("live", ("go_to", "resume"))
        },
    )
    match row:
        case PeerQuestionUnanswered():
            inbox._expand_peer_question(snap, bridge, actions, row)  # type: ignore[arg-type]
        case WakeChainHeld():
            inbox._expand_wake_chain(snap, bridge, actions, row)  # type: ignore[arg-type]
        case PeerMailHeld():
            inbox._expand_peer_held(snap, bridge, actions, row)  # type: ignore[arg-type]
    drawn = [str(c.args[1]) for c in fake.text_colored.call_args_list if len(c.args) > 1]
    return buttons, drawn, emitted, called


def test_a_running_leads_question_row_goes_to_it_and_dismisses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap, cid = _asked()
    snap, _ = step(snap, PeerQuestionAged(cid))
    (row,) = questions(snap)

    buttons, drawn, _, called = _expanded(monkeypatch, snap, row, press="go to session z")
    assert buttons == [("go to session z", True), ("dismiss", True)]
    assert called == [("go_to", Z_LEAD)]
    # Both leads named as every lead is told of them.
    assert (
        'It waits on the lead of session z, card "do a thing", project "w"; the lead of'
        ' session s, card "do a thing", project "w" asked it.'
    ) in drawn

    _, _, emitted, _ = _expanded(monkeypatch, snap, row, press="dismiss")
    assert emitted == [PeerQuestionDismissed(concern_id=cid, running=True)]


def test_a_down_leads_question_row_resumes_it_and_dismisses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap, cid = _asked()
    snap, _ = step(snap, AgentFinished(Z_LEAD, AgentState.FAILED, 4.5))
    (row,) = questions(snap)

    buttons, _, _, called = _expanded(monkeypatch, snap, row, press="resume session z")
    assert buttons == [("resume session z", True), ("dismiss", True)]
    assert called == [("resume", Z_LEAD)]

    _, _, emitted, _ = _expanded(monkeypatch, snap, row, press="dismiss")
    assert emitted == [PeerQuestionDismissed(concern_id=cid, running=False)]


def test_resume_is_dead_with_no_card_to_resume_from_and_dismiss_still_acts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap, cid = _asked()
    snap, _ = step(snap, AgentFinished(Z_LEAD, AgentState.FAILED, 4.5))
    (row,) = questions(snap)
    gone = Recalled(transcript=TranscriptPresence.GONE)
    nodes = {**snap.nodes, Z_LEAD: snap.nodes[Z_LEAD].with_(recalled=gone)}
    gone_snap = dataclasses.replace(snap, nodes=MappingProxyType(nodes))

    buttons, _, _, called = _expanded(monkeypatch, gone_snap, row, press="resume session z")
    assert buttons == [("resume session z", False), ("dismiss", True)]
    assert called == []

    unwired, _, _, _ = _expanded(monkeypatch, snap, row, live=())
    assert unwired == [("resume session z", False), ("dismiss", True)]


@pytest.mark.parametrize(
    "row, existing",
    [
        (WakeChainHeld(Z_LEAD, 1.0, "s", "k1", "brand"), "wake them"),
        (PeerMailHeld(Z_LEAD, 1.0, "s", "k1", "brand", ("c-x",), "is closed"), "dismiss"),
    ],
    ids=["wake-chain", "held-mail"],
)
def test_a_held_rows_lead_can_be_gone_to_beside_its_own_button(
    monkeypatch: pytest.MonkeyPatch, row: Any, existing: str
) -> None:
    buttons, drawn, _, called = _expanded(monkeypatch, linked(), row, press="go to session z")
    assert buttons == [("go to session z", True), (existing, True)]
    assert called == [("go_to", Z_LEAD)]
    assert any('the lead of session z, card "do a thing", project "w".' in t for t in drawn)


@pytest.mark.parametrize(
    ("held", "count", "channel", "why"),
    [
        (
            lambda: _unheld(sent(), sent(quiet=True)),
            2,
            "brand",
            "is not open in this run (not restored, or its log not read whole)",
        ),
        # The row names the link by its own name, and the message's other
        # name only as the reason it is held (R4-leftovers 4).
        (_misnamed, 1, "brand", "does not hand these over: they name it #other"),
    ],
    ids=["link-not-in-this-run", "live-link-that-does-not-fit"],
)
def test_a_held_mail_row_says_how_many_wait_why_the_link_holds_them_and_whom_they_are_for(
    monkeypatch: pytest.MonkeyPatch, held: Any, count: int, channel: str, why: str
) -> None:
    """
    Each reason a store-derived row gives for a link that will not hand its
    mail over, read whole in the row's text and in what the cursor says when
    the row goes. A link closed in its log raises no row, so ``model.why_held``'s
    "is closed" never reaches one.
    """
    snap = held()
    (row,) = held_rows(snap)
    assert row.why == why

    _, drawn, _, _ = _expanded(monkeypatch, snap, row)

    assert drawn[:2] == [
        f"{count} message(s) from the lead across #{channel} wait for this lead, and the link"
        f" they were sent over {why}. They are kept in the store and in their sender's log.",
        'They are for the lead of session z, card "do a thing", project "w".',
    ]
    assert inbox.departure_line(snap, row).startswith(
        f"the held mail you were on - #{channel} for "
    )
    assert inbox.departure_line(snap, row).endswith(
        " - is no longer held: it was handed over, or its link closed, or you dismissed it"
    )


def test_no_peer_rows_keys_say_there_is_nothing_to_press() -> None:
    snap, cid = _asked()
    snap, _ = step(snap, PeerQuestionAged(cid))
    rows: list[Obligation] = [
        *questions(snap),
        WakeChainHeld(Z_LEAD, 1.0, "s", "k1", "brand"),
        PeerMailHeld(Z_LEAD, 1.0, "s", "k1", "brand", ("c-x",), "is closed"),
    ]
    for row in rows:
        keys = inbox.keys_hint(row)
        assert "nothing to press" not in keys and "buttons" in keys


def test_a_read_question_whose_lead_is_down_is_not_said_to_be_unanswered() -> None:
    """
    Its lead may have answered in an earlier run; that answer is in its own
    log, which is not read until it is resumed.
    """
    snap, _ = _asked()
    snap, _ = step(snap, AgentFinished(Z_LEAD, AgentState.FAILED, 4.5))
    (row,) = questions(snap)

    body = inbox._peer_question_body(row)

    assert "unanswered" not in body and "unanswered" not in row.summary
    assert "may already have answered" in body and "not read until the session is resumed" in body
    assert "dismissed" in inbox.departure_line(snap, row)


# -- P4-fix: a parked peer row keeps its subject on a narrow pane ------------------------

SEND_TOOL = "mcp__pptmstr__send_to_peer"


def _parked_to(title: str = "short card") -> tuple[Snapshot, ApprovalNeeded]:
    """
    A send from ``s`` across #brand to ``z``, whose card is titled ``title``,
    parked. The default leaves the whole description on the 90-character row.
    """
    snap = linked()
    snap = dataclasses.replace(
        snap,
        nodes=MappingProxyType({**snap.nodes, Z_LEAD: snap.nodes[Z_LEAD].with_(task=title)}),
    )
    snap, _ = step(
        snap, _parked(SEND_TOOL, {"channel": "brand", "subject": "the hero image", "body": "b"})
    )
    (row,) = [o for o in snap.needs_you if isinstance(o, ApprovalNeeded)]
    return snap, row


def _seven_px(monkeypatch: pytest.MonkeyPatch, width: float) -> Any:
    """``inbox``'s imgui with every character 7px wide and a pane ``width`` wide."""
    from unittest.mock import MagicMock

    from imgui_bundle import imgui as real_imgui

    fake = MagicMock()
    fake.ImVec2 = real_imgui.ImVec2
    fake.get_cursor_screen_pos.side_effect = lambda: real_imgui.ImVec2(0.0, 0.0)
    fake.get_content_region_avail.return_value = real_imgui.ImVec2(width, 400.0)
    fake.get_text_line_height_with_spacing.return_value = 18.0
    fake.calc_text_size.side_effect = lambda t: real_imgui.ImVec2(7.0 * len(str(t)), 14.0)
    monkeypatch.setattr(inbox, "imgui", fake)
    monkeypatch.setattr("pptmstr.ui.widgets.imgui", fake)
    return fake


@pytest.mark.parametrize(
    ("title", "described"),
    [
        ("short card", 'session z, card "short card", project "w"'),
        ("a card whose title runs on past anything the row has room for", "session z"),
    ],
    ids=["whole", "head-only"],
)
def test_the_store_marks_where_a_parked_rows_lead_is_described(title: str, described: str) -> None:
    _, row = _parked_to(title)
    pending = row.approval

    assert pending.summary == f"message the lead on #brand ({described}): the hero image"
    assert pending.summary_peer is not None
    start, end = pending.summary_peer
    assert pending.summary[start:end] == described


def test_a_row_that_names_no_lead_marks_no_description() -> None:
    snap = linked()
    snap, _ = step(snap, ChannelClosed("k1", 2.0))
    snap, _ = step(
        snap, _parked(SEND_TOOL, {"channel": "brand", "subject": "the hero image", "body": "b"})
    )
    (row,) = [o for o in snap.needs_you if isinstance(o, ApprovalNeeded)]
    assert "(it reaches no lead now)" in row.approval.summary
    assert row.approval.summary_peer is None


def test_a_narrow_pane_cuts_the_lead_described_and_keeps_the_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    P3-fix 1: the row's string keeps its subject within 90 characters, and
    the pane's pixel cut falls on the description of the lead, not the subject.
    """
    from unittest.mock import MagicMock

    snap, row = _parked_to()
    summary = row.approval.summary
    # Room for all but ten characters of the summary.
    fake = _seven_px(monkeypatch, inbox._X_SUMMARY + 12.0 + 7.0 * (len(summary) - 10))

    mocks = [MagicMock() for _ in range(6)]
    inbox._row(snap, *mocks, obligation=row, expanded=False, now=10.0)  # type: ignore[arg-type]
    drawn = [str(c.args[1]) for c in fake.text_colored.call_args_list if len(c.args) > 1][-1]

    assert drawn == 'message the lead on #brand (session z, card "short card", …): the hero image'


def test_a_pane_too_narrow_for_the_subject_beside_an_ellipsis_cuts_from_the_right(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, row = _parked_to()
    _seven_px(monkeypatch, 0.0)

    shown = inbox.summary_shown(row, 7.0 * 30)

    assert shown == row.approval.summary[:29] + "…"


def test_a_row_with_no_lead_described_is_cut_from_the_right_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, row = _parked_to()
    plain = dataclasses.replace(row, approval=dataclasses.replace(row.approval, summary_peer=None))
    _seven_px(monkeypatch, 0.0)

    shown = inbox.summary_shown(plain, 7.0 * 40)

    assert shown == row.approval.summary[:39] + "…"
