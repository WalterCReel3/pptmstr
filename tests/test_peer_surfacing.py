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

The reducer is driven directly for what the store decides, and the shell for
the timer. Nothing here is drawn: the rows' UI is exercised by typecheck and by
the exhaustive matches, not by a display.
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
    PeerWakeSent,
    SessionSettled,
)
from pptmstr.model import (
    AgentState,
    Channel,
    ConcernState,
    DetachRefusal,
    ObligationKind,
    PeerMailHeld,
    PeerQuestionUnanswered,
    Snapshot,
    WakeChainHeld,
)
from pptmstr.settings import Settings
from pptmstr.store import Store, _apply
from pptmstr.ui import inbox
from tests.test_peer_wakes import (
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
        "- and 2 more decision(s) the lead linked to you raised were answered or dismissed; "
        "read_decisions to see them."
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
    assert "decision d-yes on #brand, which the lead linked to you raised, was answered" in message


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
    assert row.summary.endswith("is unanswered - its session is not running")
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


def test_no_peer_rows_departure_promises_a_withdrawal() -> None:
    """RV-W3d 4: no tool or control emits a peer withdrawal."""
    snap, cid = _asked()
    down, _ = step(snap, AgentFinished(Z_LEAD, AgentState.FAILED, 4.5))
    (question_row,) = questions(down)
    held_row = PeerMailHeld(Z_LEAD, 1.0, "s", "k1", "brand", ("c-x",), "is closed")

    for row in (question_row, held_row):
        assert "withdrawn" not in inbox.departure_line(down, row)
    assert "link closed" in inbox.departure_line(down, held_row)


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


def test_mail_on_a_live_link_that_does_not_hand_it_over_is_a_row_that_says_so() -> None:
    """
    The third of ``_why_held``'s cases: a message naming its live link under
    another name. Restore turns such a record away, so it is put in the store
    by hand here; a step derives the rows again.
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

    (row,) = held_rows(snap)
    assert row.why == "does not hand these over"
    assert "not open in this run" not in row.why


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
