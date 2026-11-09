"""
Concerns between agents of one team: the wake one owes, and the refusals of a
withdrawal.

Pure: no SDK, no clock, no IO. Nothing outside ``pptmstr.store`` imports it
(``tests/test_layers.py``); ``store._apply`` is the step it is part of.
"""

from __future__ import annotations

from ..channels.records import PeerWithdrawRefusal
from ..effects import Effect, PeerWithdrawSettled, SessionWoken, WakeReason, peer_identity
from ..model import Concern, ConcernState, NodeId, Snapshot, is_operator


def _message_withdrawal_refusal(
    snap: Snapshot, by: NodeId, concern: Concern | None
) -> PeerWithdrawRefusal | None:
    """
    Why the agent ``by`` may not withdraw ``concern``, or None if it may.

    **Only its sender may**: the node the gate stamped on the send, compared
    whole, so a sub-agent of the sending session is not its lead and the
    recipient is never the sender (``model.peer_addressed``). A concern this
    store does not hold and one another agent sent are answered alike
    (``PeerWithdrawRefusal.NOT_YOURS``).

    A concern inside a team only while it waits. A peer message while it
    waits and after its lead read it, since a question read and left open is
    the one a sender most needs to take back; but only over a link that is
    live and still has the sender's session on it, so a lead that has left a
    link reaches nothing through it, and only while no message from its
    recipient's session answers it (``Snapshot.is_answered``).
    """
    if concern is None or concern.sender != by:
        return PeerWithdrawRefusal.NOT_YOURS
    if concern.state is ConcernState.WITHDRAWN:
        return PeerWithdrawRefusal.WITHDRAWN
    if not concern.is_peer:
        return None if concern.state is ConcernState.POSTED else PeerWithdrawRefusal.ALREADY_READ
    link = snap.channels.get(concern.link) if concern.link is not None else None
    if link is None or not link.joins(by[0]):
        return PeerWithdrawRefusal.NOT_A_MEMBER
    if snap.is_answered(concern):
        return PeerWithdrawRefusal.ANSWERED
    return None


def _withdraw_settled(
    snap: Snapshot, after: Snapshot, request_id: str, withdrawn: Concern
) -> PeerWithdrawSettled:
    """
    The answer to a withdrawal ``_message_withdrawal_refusal`` allowed: whom
    the message was for and whether that lead had read it, from the pre-apply
    view ``snap``, and where the message it answered stands, from ``after``,
    where it no longer answers it (``Snapshot.cited_question``).
    """
    return PeerWithdrawSettled(
        request_id=request_id,
        recipient=peer_identity(snap, withdrawn.recipient[0]),
        channel=withdrawn.channel,
        read=withdrawn.state is ConcernState.DELIVERED,
        answers=withdrawn.re,
        cited=after.cited_question(withdrawn),
    )


def _concern_wake(concern: Concern) -> tuple[Effect, ...]:
    """
    The order to wake a lead for a concern one of its own workers addressed to
    it, and nothing for any other concern.

    Only worker to its own lead, so no agent can wake another into a loop: a
    lead's concern to a worker, a worker's to a worker, and anything across
    sessions are read with ``read_inbox`` when the recipient next looks.

    A message between the leads of two linked sessions is not a concern this
    rule is asked of: it arrives through ``PeerSent``, and its wake is
    ``_peer_waking``'s, under a rule of its own that bounds the loop this one
    rules out.
    """
    sender, recipient = concern.sender, concern.recipient
    if sender[1] is None or recipient[1] is not None or sender[0] != recipient[0]:
        return ()
    if is_operator(sender):
        # The store's own notice on the operator's action, not a worker's.
        return ()
    return (
        SessionWoken(
            session_id=recipient[0],
            reason=WakeReason.CONCERN_TO_LEAD,
            subject_id=concern.id,
            sender=sender,
        ),
    )
