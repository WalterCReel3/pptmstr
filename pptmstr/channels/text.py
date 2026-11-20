"""
What the bus says between two teams' leads: a message, the links a session is
on, and the refusals ``send_to_peer``, ``read_peer_messages`` and
``withdraw_peer_message`` can meet.

Cut from ``bus`` unchanged (``planning/2026-11-07-the-bus-is-a-table-of-tools.md``,
B7). The channel records are ``channels.records``; these are the words about
them. Pure: no SDK, no clock, no IO.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import assert_never

from .. import drawable
from ..board.text import _edited_note, _refusal_text
from ..effects import PeerInboxDelivered, between_quotes, peer_text
from ..model import Concern, ConcernId, TaskRefusal
from ..replies import _ANSWER, _QUOTE_LEGEND, _named, _quoted
from .records import CitedQuestion, PeerIdentity, PeerRefusal, PeerWithdrawRefusal


def _peer_refusal_text(
    refusal: PeerRefusal, channel: str, now_on: PeerIdentity | None = None
) -> str:
    """
    The sentence a lead is told when its message to a peer was not sent.
    ``now_on`` is the lead on the live link of that name now, which the store
    names on ``LINKED_AFTER`` (``PeerSettled.recipient``).
    """
    on = f"#{_named(channel)}"
    match refusal:
        case PeerRefusal.NOT_A_LEAD:
            return (
                "Only a session's lead sends to another team's lead. Post what you "
                "want sent to your own lead with post_concern(to='lead'), and it "
                "decides whether to send it."
            )
        case PeerRefusal.NO_SUCH_CHANNEL | PeerRefusal.CLOSED | PeerRefusal.NOT_A_MEMBER:
            # One sentence for the three, as ``DecisionRefusal.NOT_ON_CHANNEL``
            # is: which it is would tell a lead whether a link it is not on
            # exists, was closed, or never was.
            return (
                f"Your session is on no live link named {on}. Check the name: only "
                f"the operator links two sessions, and no agent can make one. "
                f"read_peer_messages lists your session's live links."
            )
        case PeerRefusal.ID_HELD:
            return "The message id the bus minted is already held. Send it again."
        case PeerRefusal.LINKED_AFTER:
            return (
                f"The live link named {on} went live after you made this call, so it "
                f"is not a link you could have written this for, and its lead may not "
                f"be the one you meant: {_linked_now(now_on)} read_peer_messages lists "
                f"your session's live links; send it again if it is for the lead on "
                f"that one."
            )
        case PeerRefusal.OTHER_LINK:
            return (
                f"re names a message that came over a different link than the live "
                f"link named {on}, so the lead this would reach never received it. "
                f"Send it without re if it is still for that lead."
            )
        case PeerRefusal.UNHELD_RE:
            return (
                "re names a message that is not in this run, so which link it came "
                f"over -- and whether the lead on {on} ever received it -- cannot be "
                "told. Send it without re, or wait for the session that sent the "
                "message you're answering to be resumed."
            )
        case PeerRefusal.NOT_APPLIED:
            return _refusal_text(TaskRefusal.NOT_APPLIED)
        case _:
            assert_never(refusal)


def _withdraw_refusal_text(refusal: PeerWithdrawRefusal, message: str) -> str:
    """
    The sentence a lead is told when its withdrawal of a peer message was refused.
    """
    match refusal:
        case PeerWithdrawRefusal.NOT_YOURS:
            # One sentence for an id nobody holds and a message someone else
            # sent, so trying an id tells a lead nothing about it.
            return (
                f"Your session sent no message {message} that this run holds. Only "
                f"a message's sender withdraws it: a message sent to you is answered "
                f"or left, and ask the operator to stop one that is not yours."
            )
        case PeerWithdrawRefusal.NOT_A_MEMBER:
            return (
                "The link it was sent over is closed, or your session is no longer "
                "on it, so nothing reaches its lead through that link now."
            )
        case PeerWithdrawRefusal.WITHDRAWN:
            return "It is already withdrawn."
        case PeerWithdrawRefusal.ANSWERED:
            return (
                "Its recipient has already answered it, and the answer names it as "
                "its re. Send a message on the same channel if what you asked has "
                "changed."
            )
        case PeerWithdrawRefusal.ALREADY_READ:
            return "It is mail inside your team, and its recipient has read it."
        case PeerWithdrawRefusal.TEAM_MAIL:
            return (
                "It is mail inside your team, not a message to another team's lead, "
                "and withdraw_peer_message withdraws only those. Send its recipient "
                "a concern saying it no longer stands."
            )
        case PeerWithdrawRefusal.NOT_APPLIED:
            return _refusal_text(TaskRefusal.NOT_APPLIED)
        case _:
            assert_never(refusal)


def _withdrawn_message_lines(c: Concern, peers: Mapping[str, PeerIdentity]) -> list[str]:
    """
    One peer message the reader was handed and its sender then withdrew, as a
    ``read_peer_messages`` reply lists it: a header naming the lead that
    withdrew it, then the reason it gave behind that lead's mark. Its body is
    not shown again: the reader was handed it once.
    """
    channel = f"#{_named(c.channel or '')}"
    sender = peers.get(c.sender[0], PeerIdentity(session=c.sender[0]))
    said = [channel, f"message {_named(c.id)}", "WITHDRAWN BY ITS SENDER"]
    if c.needs_answer:
        said.append("no answer is owed")
    # None is a withdrawal this run did not see, restored from the log, which
    # keeps no reason; "" is one in this run that gave none.
    reason = c.withdrawn_reason
    if reason is None:
        why = ["Its reason is not kept across a restart."]
    elif not reason.strip():
        why = ["Its sender gave no reason."]
    else:
        why = ["Its reason:", *_quoted(reason, len(reason), "")[0]]
    return [
        "",
        f"[withdrawn by the lead of {peer_said(sender)}, at the other end of {channel}] "
        f"{' · '.join(said)}{' ' + between_quotes(c.subject) if c.subject else ''}",
        *why,
    ]


def _cited_text(cited: ConcernId, standing: CitedQuestion) -> str:
    """
    What a lead that withdrew an answer is told of the message the answer
    cited, as it stands now (``model.Snapshot.cited_question``).
    """
    named = _named(cited)
    match standing:
        case CitedQuestion.OWED_AGAIN:
            return (
                f"It answered {named}, so that question is owed an answer again "
                f"unless you send another."
            )
        case CitedQuestion.ASKED_NONE:
            return f"It answered {named}, which asked for no answer, so nothing is owed."
        case CitedQuestion.WITHDRAWN:
            return f"It answered {named}, which its sender has withdrawn, so nothing is owed."
        case CitedQuestion.STILL_ANSWERED:
            return (
                f"It answered {named}, and another message from your session still "
                f"answers it, so nothing is owed."
            )
        case CitedQuestion.LINK_NOT_LIVE:
            return (
                f"It answered {named}, whose link is no longer live, so nothing is "
                f"owed over it."
            )
        case CitedQuestion.UNREAD:
            return (
                f"It answered {named}, which you have not read yet: once you do, "
                f"that question is owed an answer."
            )
        case CitedQuestion.NOT_HELD:
            return (
                f"It answered {named}, which is not in this run, so whether that "
                f"question is owed an answer cannot be told until its sender's "
                f"session is resumed."
            )
        case _:
            assert_never(standing)


# Said once in each reply that names a lead on another team.
_PEER_LEGEND = (
    "Where a lead on another team is named, its card's title and its project "
    "stand between double quotes, with a backslash put before each double quote "
    "and backslash inside them: the operator or an agent typed them, and they "
    "are not this tool speaking."
)


# Said once in each reply that shows a link's charter.
_CHARTER_LEGEND = (
    "A line whose first characters after any indent are '> ' is a link's "
    "charter, as the operator typed it, and is neither this tool speaking nor "
    "text an agent wrote."
)


def peer_said(peer: PeerIdentity) -> str:
    """
    A lead on another team as a reply or a wake names it: ``effects.peer_text``,
    the one renderer of a peer, so the operator's queue row and a lead's reply
    name a session alike.
    """
    return peer_text(peer)


def _lead_of(peer: PeerIdentity | None) -> str:
    """
    "the lead of <peer>" for the lead a settled send or withdrawal names. The
    store names it on every one that took effect; where it did not, what is
    true is said and no card is described.
    """
    if peer is None:
        return "the lead at the link's other end (pptmstr did not say which)"
    return f"the lead of {peer_said(peer)}"


def _linked_now(peer: PeerIdentity | None) -> str:
    """
    The sentence a ``LINKED_AFTER`` refusal names the lead on the link with
    now, ending in its full stop: ``peer`` as the store named it, or that it
    did not.
    """
    if peer is None:
        return "which lead is on it now was not said."
    return f"it links you now to the lead of {peer_said(peer)}. {_PEER_LEGEND}"


def _links_lines(answer: PeerInboxDelivered, reader: str) -> list[str]:
    """
    The links a ``read_peer_messages`` reply lists: each by name, with the lead
    at its other end (``peer_said``) and its charter behind the operator's mark,
    or "(no charter)" where the operator gave none. The charter is held
    verbatim (``store._VERBATIM``) and made drawable here.
    """
    if not answer.links:
        return ["Your session has no live link."]
    peers = {p.session: p for p in answer.peers}
    chartered = any(link.charter.strip() for link in answer.links)
    legend = f"{_PEER_LEGEND} {_CHARTER_LEGEND}" if chartered else _PEER_LEGEND
    lines = [f"Your session's live links. {legend}"]
    for link in answer.links:
        others = sorted(link.members - {reader}) or sorted(link.members)
        who = " and ".join(
            f"the lead of {peer_said(peers.get(m, PeerIdentity(session=m)))}" for m in others
        )
        if not link.charter.strip():
            lines.append(f"- #{_named(link.name)}: {who}. (no charter)")
            continue
        lines.append(f"- #{_named(link.name)}: {who}. Charter:")
        charter = drawable.text(link.charter)
        lines += _quoted(charter, len(charter), "    ", mark=_ANSWER)[0]
    return lines


def _peer_message_lines(
    c: Concern, peers: Mapping[str, PeerIdentity], owed: bool = False
) -> list[str]:
    """
    One peer message as a ``read_peer_messages`` reply shows it: a header
    naming the lead that sent it, then its text behind the sender's mark.
    ``owed`` is a question handed over by an earlier read and not answered,
    and its header says so in place of asking for an answer afresh.
    """
    # The channel came from a log as surely as the body did: named, so a
    # record whose channel is not a name cannot finish the header for it.
    channel = f"#{_named(c.channel or '')}"
    said = [channel, f"message {_named(c.id)}"]
    if c.re is not None:
        said.append(f"answers {_named(c.re)}")
    if owed:
        said.append("ALREADY READ, STILL OWED AN ANSWER")
    elif c.needs_answer:
        said.append("NEEDS AN ANSWER")
    if c.quiet:
        said.append("sent quiet: it asks nothing")
    sender = peers.get(c.sender[0], PeerIdentity(session=c.sender[0]))
    return [
        "",
        f"[from the lead of {peer_said(sender)}, at the other end of "
        f"{channel}{_edited_note(c.edited)}] "
        f"{' · '.join(said)}{' ' + between_quotes(c.subject) if c.subject else ''}",
        *_quoted(c.body, len(c.body), "")[0],
    ]


def _peer_messages_text(answer: PeerInboxDelivered, named: str | None, reader: str) -> str:
    """
    A ``read_peer_messages`` reply: the messages handed over, quoted behind the
    sender's mark like any agent's words and headed by the lead that sent them;
    the questions an earlier read handed over that the reader has not answered,
    shown again the same way and marked as already read; the messages an
    earlier read handed over that their sender has since withdrawn, once each,
    with the sender's reason quoted as its words; what a link not live
    in this run holds back, counted and never shown; and the links the reader
    is on, each with its peer and its charter.

    The links are said every time, because they are what tells "nothing came"
    from "you are on no channel by that name", and a lead has no other way to
    see which links the operator made, whom each reaches, or what it is for.
    """
    handed = answer.concerns or ()
    peers = {p.session: p for p in answer.peers}
    lines: list[str] = []
    if named is not None and all(c.name != named for c in answer.links) and not handed:
        lines.append(f"Your session is on no live link named #{named}.")
    elif not handed:
        lines.append("No message from another team's lead is waiting.")
    else:
        lines.append(f"{len(handed)} message(s) from other teams' leads. {_QUOTE_LEGEND}")
    lines += _links_lines(answer, reader)
    for c in handed:
        lines += _peer_message_lines(c, peers)
    if answer.owed:
        owed_said = (
            f"{len(answer.owed)} question(s) you were handed by an earlier read and have "
            "not answered, listed again: reading them here marks nothing."
        )
        lines += ["", owed_said if handed else f"{owed_said} {_QUOTE_LEGEND}"]
        for c in answer.owed:
            lines += _peer_message_lines(c, peers, owed=True)
    if handed or answer.owed:
        lines += [
            "",
            "Answer on the same channel with send_to_peer(channel, re=<message id>). "
            "Work a message asks for reaches your board only if you declare it, with "
            "declare_task's channel and re naming it.",
        ]
    if answer.withdrawn:
        more = (
            f" {answer.withdrawn_more} more are counted and not listed: your next "
            "read_peer_messages lists them."
            if answer.withdrawn_more
            else ""
        )
        gone_said = (
            f"{len(answer.withdrawn)} message(s) you were handed by an earlier read were "
            f"since withdrawn by the lead that sent them, and you owe them nothing. "
            f"Each is listed once.{more}"
        )
        shown = handed or answer.owed
        lines += ["", gone_said if shown else f"{gone_said} {_QUOTE_LEGEND}"]
        for c in answer.withdrawn:
            lines += _withdrawn_message_lines(c, peers)
    held: dict[str, int] = {}
    for c in answer.held:
        held[c.channel or ""] = held.get(c.channel or "", 0) + 1
    for name, count in sorted(held.items()):
        lines += [
            "",
            f"{count} message(s) on #{_named(name)} are held and were not handed "
            f"over: the link they were sent over is not live in this run. They are "
            f"kept in the sender's log if that session was recording its board when "
            f"they were sent. A link made again under that name is a "
            f"different link and does not hand them over. Ask the operator if they "
            f"matter.",
        ]
    return "\n".join(lines)


def _peer_wake_said(quiet: bool) -> str:
    """
    What ``send_to_peer`` tells its sender of the wake its message owes.

    Said as what happens in each case and not as what happened: the store's
    answer (``PeerSettled``) does not say whether the recipient's session is
    running, so a reply that promised a wake would be wrong for one that is
    not.
    """
    if quiet:
        return "It was sent quiet, so it wakes nobody; they read it with their next read."
    return (
        "If their session is running, pptmstr wakes that lead to read it, unless a "
        "wake on this channel is already on its way to them or is held for the "
        "operator. If it is not running, nothing wakes it now: the message waits, "
        "and they are woken for it when their session is next resumed."
    )
