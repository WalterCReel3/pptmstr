"""
What ``AgentSession.wake`` sends a session when something it waits on arrives,
and the line each kind of arrival is named by.

Cut from ``bus`` unchanged (``planning/2026-11-07-the-bus-is-a-table-of-tools.md``,
B7), so ``app``, which sends no tool reply, reads them without importing ``bus``.
Pure: no SDK, no clock, no IO.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import assert_never

from .addresses import role_name
from .channels.records import PeerIdentity
from .channels.text import peer_said
from .effects import SessionWoken, WakeReason, peer_identity
from .model import NodeId, Snapshot
from .replies import _one_line


def _titled(peers: Iterable[PeerIdentity]) -> str:
    """
    The clause a wake carries beside peer descriptions, naming what of the
    session's own text they quote: a card title, a project, or both
    (``peer_text`` quotes each where it is held and not empty), and nothing
    when they quote neither. A wake carries no legend of its own, and a title
    is a session's first prompt.
    """
    held = [p for p in peers if p.held]
    quoted = [
        what
        for what, said in (
            ("card title", any(" ".join(p.title.split()) for p in held)),
            ("project", any(p.project for p in held)),
        )
        if said
    ]
    if not quoted:
        return ""
    verb = "are" if len(quoted) > 1 else "is"
    return f" (the quoted {' and '.join(quoted)} {verb} that session's own text, not pptmstr's)"


WAKE_HEADING = "This message is from pptmstr, not from the operator. Waiting for you:"


# How many decisions raised on a channel by the lead across it one wake names
# by id, among those the operator ended and among those still open, before
# the rest are counted and left to ``read_decisions``.
#
# Bounded because the store names them again at a resume and a restore with
# no record of what an earlier run said (``model.PeerWakes.heard`` is this
# run's), so after a restart a link's whole history arrives in one wake. Three
# because the operator ending decisions in a burst is the case the wake window
# exists for (``app.WAKE_WINDOW_S``), and three named lines plus a count is
# still a wake that reads at a glance. The ids past it are one call away, in
# the list ``read_decisions`` returns with no id, and a lead that acts on one
# reads it by id whether it was named or not. The woken session's own
# decisions are never folded: its lead asked them, and each is named.
WAKE_DECISIONS_NAMED = 3


# How many questions a lead was handed on a link and owes an answer to its
# settle's wake names by id (``Snapshot.peer_owing``) before the rest are
# counted. The same bound for the same reason: the ids past it are in the
# reply ``read_peer_messages`` gives on that channel, each with its text.
WAKE_QUESTIONS_NAMED = WAKE_DECISIONS_NAMED


def wake_message(snap: Snapshot, wakes: Sequence[SessionWoken]) -> str:
    """
    One message for every wake a session is owed at once: the heading, then a
    line for each thing to read, in the order the store gave them, each said
    once however many times it was woken for.

    Short and factual, and each line names the call that returns what arrived,
    so the session spends its turn reading it and not asking where it is.

    The operator's answers and dismissals of decisions the lead across a
    channel raised are named up to ``WAKE_DECISIONS_NAMED`` and the rest
    counted on one line after them, which names the lead of each session that
    raised one of the counted.
    """
    lines = [WAKE_HEADING]
    seen: set[tuple[WakeReason, str]] = set()
    folded: dict[str, int] = {}
    named = 0
    for wake in wakes:
        key = (wake.reason, wake.subject_id)
        if key in seen:
            continue
        seen.add(key)
        if _across_a_link(snap, wake):
            if named >= WAKE_DECISIONS_NAMED:
                raiser = snap.decisions[wake.subject_id].session_id
                folded[raiser] = folded.get(raiser, 0) + 1
                continue
            named += 1
        lines.append(f"- {wake_line(snap, wake)}")
    if folded:
        peers = [peer_identity(snap, raiser) for raiser in sorted(folded)]
        who = " and ".join(f"the lead of {peer_said(peer)}" for peer in peers)
        lines.append(
            f"- and {sum(folded.values())} more decision(s) that {who}{_titled(peers)} "
            "raised were answered or dismissed; read_decisions to see them."
        )
    return "\n".join(lines)


def _across_a_link(snap: Snapshot, wake: SessionWoken) -> bool:
    """
    Whether ``wake`` is for the operator ending a decision that the lead
    across a channel raised: one of those ``wake_message`` folds past its cap.
    """
    if wake.reason not in (WakeReason.DECISION_ANSWERED, WakeReason.DECISION_DISMISSED):
        return False
    decision = snap.decisions.get(wake.subject_id)
    return (
        decision is not None
        and decision.channel is not None
        and decision.session_id != wake.session_id
    )


def wake_line(snap: Snapshot, wake: SessionWoken) -> str:
    """
    The line of a wake for one thing: what happened to which id, and the call
    that returns it. An id, and a role an agent's address names, are each one
    line (``_one_line``).
    """
    said = _one_line(wake.subject_id)
    match wake.reason:
        case WakeReason.DECISION_ANSWERED | WakeReason.DECISION_DISMISSED:
            how = "answered" if wake.reason is WakeReason.DECISION_ANSWERED else "dismissed"
            decision = snap.decisions.get(wake.subject_id)
            whose = _relayed(snap, decision.asker if decision is not None else None)
            if decision is not None and decision.channel is not None:
                if decision.session_id != wake.session_id:
                    peer = peer_identity(snap, decision.session_id)
                    whose = (
                        f" on #{_one_line(decision.channel)}, which the lead of "
                        f"{peer_said(peer)}{_titled([peer])} raised,"
                    )
            return (
                f'decision {said}{whose} was {how}. read_decisions(decision_id="{said}") '
                "returns it."
            )
        case WakeReason.COMMAND_ENDED:
            command = snap.commands.get(wake.subject_id)
            whose = _relayed(snap, command.asker if command is not None else None)
            return (
                f'command {said}{whose} has ended. read_commands(command_id="{said}") '
                "returns how."
            )
        case WakeReason.CONCERN_TO_LEAD:
            sender = wake.sender
            who = "a worker" if sender is None else _one_line(_role(snap, sender))
            return f"{who} sent you a concern. read_inbox() returns it."
        case WakeReason.PEER_MESSAGE:
            return peer_wake_line(snap, wake)
        case _:
            assert_never(wake.reason)


def peer_wake_line(snap: Snapshot, wake: SessionWoken) -> str:
    """
    A peer message's wake (Decided 10): how many messages wait for the lead on
    the link, counted when the wake is sent and not when it was ordered, so a
    burst coalesced into one wake is counted whole, and which lead is at the
    link's other end (``channels.text.peer_said``), its card's title and project quoted as
    typed and said to be that session's own text (``_titled``). Never a
    message's text, nor its sender's words in any form: what a linked lead
    wrote is read with ``read_peer_messages``, quoted as theirs.

    The decisions raised on the link that the lead is owed a wake for
    (``Snapshot.peer_untold``) are named after the count, by id and never by
    their text, and the count is left out when nothing but those waits. At
    most ``WAKE_DECISIONS_NAMED`` are named and the rest counted. The shell
    reports every one of them as told (``app._say_peer_wakes_spent``), the
    counted ones too: a wake left owed for them would be asked again, counted
    toward the link's chain, for decisions this line already sent the lead to
    ``read_decisions`` for.

    A settle's wake names the questions the lead was handed on the link and
    has not answered (``Snapshot.peer_owing``), by id, at most
    ``WAKE_QUESTIONS_NAMED`` and the rest counted, and says that a read of
    the channel lists them again; the count is left out when nothing else
    waits. Nothing is reported told for them: the store names them only at a
    settle, an asker's restore or the operator's release of a held wake
    (``model.PeerWakes.owing``).
    """
    link = snap.channels.get(wake.subject_id)
    if link is None:
        return "a linked lead sent you a message. read_peer_messages() returns it."
    name = _one_line(link.name)
    count = len(snap.peer_unread(wake.session_id, link))
    untold = [_one_line(did) for did in snap.peer_untold(wake.session_id, link)]
    owing = [_one_line(cid) for cid in snap.peer_owing(wake.session_id, link)]
    said = [
        f'{count} unread on #{name}; call read_peer_messages(channel="{name}"), '
        "which returns it."
    ]
    if (untold or owing) and not count:
        said = []
    others = sorted(link.members - {wake.session_id}) or sorted(link.members)
    peers = [peer_identity(snap, m) for m in others]
    who = " and ".join(f"the lead of {peer_said(peer)}" for peer in peers)
    said.append(f"#{name} links you to {who}{_titled(peers)}.")
    if owing:
        named = ", ".join(owing[:WAKE_QUESTIONS_NAMED])
        more = len(owing) - WAKE_QUESTIONS_NAMED
        said.append(
            f"You were handed question(s) {named}"
            + (f" and {more} more" if more > 0 else "")
            + f" on #{name} and have not answered: read_peer_messages("
            f'channel="{name}") lists them again, marked as already read and still '
            "owed. Answer with send_to_peer(channel, re=<message id>)."
        )
    for did in untold[:WAKE_DECISIONS_NAMED]:
        said.append(
            f"decision {did} was raised on #{name} by the lead linked to you. "
            f'read_decisions(decision_id="{did}") returns it.'
        )
    if len(untold) > WAKE_DECISIONS_NAMED:
        said.append(
            f"and {len(untold) - WAKE_DECISIONS_NAMED} more raised on #{name} by the lead "
            "linked to you; read_decisions to see them."
        )
    return " ".join(said)


def _role(snap: Snapshot, node: NodeId) -> str:
    return role_name(snap, node, node[0])


def _relayed(snap: Snapshot, asker: NodeId | None) -> str:
    """
    For what a sub-agent asked, the words that say whose it was and that the
    lead is the one woken: the sub-agent has no turn of its own to wake.
    """
    if asker is None or asker[1] is None:
        return ""
    role = _one_line(_role(snap, asker))
    return f", which {role} asked (it is not woken; pass it on)"
