"""
Decisions: filing, settling, ending and the refusals of a raise or a
withdrawal.

Pure: no SDK, no clock, no IO. Nothing outside ``pptmstr.store`` imports it
(``tests/test_layers.py``); ``store._apply`` is the step it is part of.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from types import MappingProxyType

from ..channels.records import PeerIdentity
from ..effects import DecisionSettled, Effect, SessionWoken, WakeReason
from ..intents import DecisionAnswered, DecisionDismissed, DecisionRaised, DecisionWithdrawn
from ..model import (
    MAX_ANSWER_CHARS,
    MAX_OPEN_DECISIONS_PER_ASKER,
    MAX_OPEN_SUBAGENT_DECISIONS,
    Answered,
    Decision,
    DecisionId,
    DecisionOutcome,
    DecisionRefusal,
    Snapshot,
)
from .owing import _allowance, _woken, blocked_tasks


def _filed(
    decisions: Mapping[DecisionId, Decision], decision: Decision
) -> Mapping[DecisionId, Decision]:
    """``decisions`` with ``decision`` under its id, as a new map."""
    return MappingProxyType({**decisions, decision.id: decision})


def _decision_settled(
    request_id: str | None, refusal: DecisionRefusal | None, peer: PeerIdentity | None = None
) -> tuple[Effect, ...]:
    """The reply to a raise or a withdrawal, or nothing when nobody asked."""
    if request_id is None:
        return ()
    return (DecisionSettled(request_id=request_id, refusal=refusal, peer=peer),)


def _raise_refusal(snap: Snapshot, intent: DecisionRaised) -> DecisionRefusal | None:
    """
    Why a decision may not be filed, or None if it may.

    An id already held is refused before anything is counted, so a retry of a
    raise that landed is told that and not that an allowance is spent.

    A raise on a channel is the lead's, over a live link its session is a
    member of (``DecisionRefusal.NOT_ON_CHANNEL``). Checked against the store
    at the instant of filing, as ``blocks`` is: a link closed between the
    call and this step refuses it.

    ``blocks`` is checked here and not only where it was typed: this is the
    instant the board is known, and a row that says "blocks t3" in the queue's
    own voice has to be naming a task that is there.

    **Two bounds, and the root meets only the first.** An agent may have
    ``MAX_OPEN_DECISIONS_PER_ASKER`` open. The sub-agents of a session may have
    ``MAX_OPEN_SUBAGENT_DECISIONS`` open between them, counting only those whose
    asker has not ended (``AskerFate.has_ended``). Counted across the whole
    session, one worker could spend the allowance of its lead -- the agent with
    the most reason to ask, and the one with nobody to pass the question up to.
    And a finished worker's open decisions are freed only by the operator, so
    counting them would let agents that no longer exist refuse the ones that do.
    """
    asker = intent.node_id
    if intent.decision.id in snap.decisions:
        return DecisionRefusal.ALREADY_RAISED
    channel = intent.channel
    if channel is not None:
        if asker[1] is not None:
            return DecisionRefusal.NOT_A_LEAD
        link = snap.live_channel(channel)
        if link is None or not link.joins(asker[0]):
            return DecisionRefusal.NOT_ON_CHANNEL
        if snap.linked_after(link, intent.called_at):
            return DecisionRefusal.LINKED_AFTER
    if blocked_tasks(snap, intent.decision, asker[0]) != tuple(
        dict.fromkeys(intent.decision.blocks)
    ):
        return DecisionRefusal.UNKNOWN_TASK
    still_open = [d for d in snap.decisions.values() if d.is_open and d.session_id == asker[0]]
    match _allowance(
        snap, still_open, asker, MAX_OPEN_DECISIONS_PER_ASKER, MAX_OPEN_SUBAGENT_DECISIONS
    ):
        case "asker":
            return DecisionRefusal.TOO_MANY_OPEN
        case "workers":
            return DecisionRefusal.SESSION_FULL
        case None:
            return None


def _decided(snap: Snapshot, decision: Decision, reason: WakeReason) -> tuple[Effect, ...]:
    """
    The wakes the operator's answer or dismissal of ``decision`` orders: its
    asker's, as for any decision, and for one raised on a channel every other
    member's lead that reads it over the link (Decided 12), so the lead that
    did not ask hears it from pptmstr and not as the asker's relay.

    The operator's act, so none of these counts toward a link's chain; it
    starts the chain again instead (``_moved``). The asker's session is woken
    once, whichever of its agents asked.
    """
    asker = _woken(decision.asker, reason, decision.id)
    link = None if decision.link is None else snap.channels.get(decision.link)
    if link is None:
        return asker
    return asker + tuple(
        SessionWoken(session_id=member, reason=reason, subject_id=decision.id)
        for member in sorted(link.members - {decision.session_id})
        if snap.channel_decision_readable(decision, member)
    )


def _ended_by_operator(
    decisions: Mapping[DecisionId, Decision],
    intent: DecisionAnswered | DecisionDismissed,
    outcome: DecisionOutcome,
) -> Mapping[DecisionId, Decision] | None:
    """
    ``decisions`` with the one ``intent`` names ended as ``outcome``, or None
    when the intent changes nothing.

    **This is the whole of who may answer or dismiss**: an intent that names a
    node is an agent's and is not applied, and the bus builds no intent without
    one. A decision that has ended is left as it ended, so no later intent --
    the operator's included -- replaces an answer.

    **An answer over ``MAX_ANSWER_CHARS`` is not applied, and is not cut to
    fit.** The decision stays open and in the queue. An answer is stored byte
    for byte or not at all, and one too long for ``read_decisions`` to return
    whole is one its asker could never read. Nobody is waiting on this intent to
    be told, so the surface that sends it refuses the draft first.
    """
    decision = decisions.get(intent.decision_id)
    if intent.node_id is not None or decision is None or not decision.is_open:
        return None
    if isinstance(outcome, Answered) and len(outcome.text) > MAX_ANSWER_CHARS:
        return None
    return _filed(decisions, dataclasses.replace(decision, outcome=outcome))


def _withdrawal_refusal(
    decisions: Mapping[DecisionId, Decision], intent: DecisionWithdrawn
) -> DecisionRefusal | None:
    decision = decisions.get(intent.decision_id)
    if decision is None:
        return DecisionRefusal.NO_SUCH_DECISION
    if decision.asker != intent.node_id:
        return DecisionRefusal.NOT_THE_ASKER
    if not decision.is_open:
        return DecisionRefusal.ALREADY_ENDED
    return None
