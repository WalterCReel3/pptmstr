"""
A channel's own values: the ``Channel`` record and its id, the rule a channel's
name keeps, a charter mark, and the refusals and marks the peer tools, the
link form and a link's thread speak in.

Imports nothing from pptmstr, so ``model`` imports it and nothing here can
reach a ``Snapshot``. That is the requirement this module is named
for, and ``tests/test_layers.py`` holds it to it. It is pure: no disk, no
clock, no entropy.

What a ``Snapshot`` field or a ``model`` alias is needed to state stays in
``model``: ``PEER_WAKE_CHAIN`` and ``PeerWakes`` (keyed by ``DecisionId``),
``peer_addressed`` (over ``NodeId``), the thread rows that hold a ``Concern``,
a ``Decision`` or a ``PendingApproval``, and the three channel obligations,
which are members of ``Obligation``. The id aliases other than ``ChannelId``
are ``model``'s until they move to ``ids``.

The design is ``planning/2026-11-07-channels-and-ids-have-one-owner-each.md``.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Mapping
from dataclasses import dataclass


class Charter(enum.Enum):
    """
    A declarer's answer to "is this request inside the channel's charter?".

    **Three members, unstated among them.** Unlike ``Basis``, not saying is a
    member and not ``None``: a provenance always carries an answer, and a
    reader that tests ``if not provenance.inside_charter`` cannot read
    unstated as no, because every member is truthy.

    **The log carries the value, as text** (``boardlog.Declared``), for the
    reason ``Declared.basis`` is text: a member a later build adds is then a
    word this build reads as unstated, not a record it cannot read and a log
    that ends at it.
    """

    YES = "yes"
    NO = "no"
    UNSTATED = "unstated"

    @classmethod
    def stated(cls, raw: object) -> Charter:
        """
        The member a declarer's own spelling names, and ``ValueError`` for
        anything that is none of the three.

        Not ``stated_flag``. That one reads ``"none"`` and ``"null"`` as no,
        which for a two-valued flag claims nothing; here no is a statement (the
        request is outside the charter) and reading an author's "none" as one
        would put it in their mouth. So the spellings are few and an unknown one
        is refused rather than guessed. Here rather than in ``bus.py`` for the
        reason ``Basis.stated`` is: the parked row reads the same argument the
        handler stores.

        An unknown value is echoed only when it is short. The refusal is not a
        place to print back an argument of any length, so a long one is named by
        its length, as an over-long task_id is.
        """
        if raw is None:
            return cls.UNSTATED
        if isinstance(raw, bool):
            return cls.YES if raw else cls.NO
        word = str(raw).strip().lower()
        if word in _CHARTER_YES:
            return cls.YES
        if word in _CHARTER_NO:
            return cls.NO
        if word in _CHARTER_UNSTATED:
            return cls.UNSTATED
        text = str(raw)
        named = (
            repr(text)
            if len(text) <= _CHARTER_ECHOED
            else f"(a value of {len(text)} characters, not repeated here)"
        )
        raise ValueError(
            f"inside_charter {named} is not one of yes, no or unstated. Say yes or no, "
            f"or omit it."
        )


# How an author spells each of the three answers to "inside the charter?".
_CHARTER_YES = frozenset({"yes", "true", "inside"})
_CHARTER_NO = frozenset({"no", "false", "outside"})
_CHARTER_UNSTATED = frozenset({"", "unstated"})
# The longest unknown charter mark a refusal repeats: room for a word or two.
_CHARTER_ECHOED = 32


# What a channel may be called (``_channel_refusal``), and the longest it may be.
MAX_CHANNEL_CHARS = 64
_CHANNEL_NAME = re.compile(rf"[a-z0-9][a-z0-9._-]{{0,{MAX_CHANNEL_CHARS - 1}}}")


def _channel_refusal(name: str) -> str | None:
    """
    Why ``name`` cannot be a channel's name, or None when it can.

    **Plain:** ``^[a-z0-9][a-z0-9._-]{0,63}$`` -- lowercase ASCII letters and
    digits, ``.``, ``_`` and ``-``, starting with a letter or a digit. The
    operator names a channel, and the name becomes a directory once channels
    are linked, so it is an identity two leads must both read the same way.
    That is why nothing is allowed that draws like something else: no
    invisible or control character (a zero-width space, an ANSI escape), no
    letter from another script that draws as a Latin one, and no capitals, so
    ``Brand`` and ``brand`` are not two channels on a filesystem that folds
    case and one on another.

    A name that passes is also one ``requests.address_refusal`` takes -- no
    separator, no NUL, no leading dot, encodable, short of an entry's length --
    and is its own drawable form (``drawable.text``), so the name checked is
    the name the store keeps and the log writes. Both follow from the pattern
    and are not checked again here (this module imports nothing from pptmstr); a test
    keeps the two rules in step.

    No whitespace, so a name is one word in the sentences that print it. That
    does not stop a name from imitating the mark -- "ops.inside_charter-yes"
    passes -- and nothing here tries to: what keeps the queue row honest is that
    the row puts the mark before the channel (``approval.summarize``), so a
    name can only follow it.
    The name is not repeated: a refusal is not a place to print it back.
    """
    if len(name) > MAX_CHANNEL_CHARS:
        return (
            f"channel is {len(name)} characters and the limit is {MAX_CHANNEL_CHARS}. "
            f"It is a name; put the rest in detail."
        )
    if _CHANNEL_NAME.fullmatch(name) is None:
        return (
            "channel is not a name a channel can have. A channel's name is lowercase "
            "ASCII letters and digits, '.', '_' and '-', and starts with a letter or digit."
        )
    return None


def is_channel_name(name: str) -> bool:
    """Whether ``name`` is one a channel can have (``_channel_refusal``)."""
    return _channel_refusal(name) is None


def channel_refusal(name: str) -> str | None:
    """
    The sentence a tool refuses ``name`` with when it is not one a channel can
    have, or None when it is (``_channel_refusal``).
    """
    return _channel_refusal(name)


# A channel's id: a uuid the shell mints when the operator links two leads. Not
# the name, which can be linked again once the channel under it is closed.
ChannelId = str

# How many sessions a link joins. The record holds a set, so relaxing this is a
# rule changed here and not a format changed in the log.
LINK_MEMBERS = 2


class LinkRefusal(enum.Enum):
    """
    Why the operator's link of two leads was not made (``Snapshot.link_refusal``).
    """

    BAD_NAME = "the name is not one a channel can have"
    NOT_A_PAIR = "a link joins exactly two sessions"
    ID_HELD = "a channel under that id is already held"
    NAME_LIVE = "a live channel already has that name"
    NOT_RUNNING = "a member is not a running session"
    NOT_RECORDING = "a member's board is not being recorded"


@dataclass(frozen=True, slots=True)
class Channel:
    """
    A link between leads, made by the operator, under a plain name.

    Lives in its own log (``channels.log``), never in a member's board log. Closing
    a link sets ``closed_at`` and nothing removes it: a closed channel stays in
    the store, and its name can be linked again under a new id.

    ``members`` is a set of session ids, though linking refuses any size but
    ``LINK_MEMBERS``: a record made as a pair and widened later would otherwise
    be misread as a pair by an older build.

    ``charter`` is the operator's text as it was typed. It is written to the log
    from here, so the store holds it verbatim (``store._VERBATIM``) and a pane
    draws it through a helper that takes any ``str``.

    Both times are wall-clock times, minted by the shell with the intent: they
    are written to the log and read back by another process.
    """

    id: ChannelId
    name: str
    members: frozenset[str]
    charter: str
    created_at: float
    closed_at: float | None = None

    @property
    def live(self) -> bool:
        """Whether the link is open: it has not been closed."""
        return self.closed_at is None

    def joins(self, *sessions: str) -> bool:
        """Whether the link is open and every one of ``sessions`` is a member."""
        return self.live and all(s in self.members for s in sessions)


class EveryChannel(enum.Enum):
    """
    "Every channel", as a reader of peer mail asks for it
    (``Snapshot.peer_inbox_of``).

    A value of its own and not None, because on ``Concern.channel`` None means
    team mail: a caller handing a concern's channel through as the scope would
    otherwise ask for every channel's mail when it held a team concern. With
    this, the type says which of the two a caller meant.
    """

    EVERY = "every channel"


EVERY_CHANNEL = EveryChannel.EVERY

# Which peer mail a lead asks to read: the links of one name, or every link.
ChannelScope = str | EveryChannel


class MoveOwner(enum.Enum):
    """
    Who has to act next on a link (``Snapshot.whose_move``).

    ``OPERATOR``: something on the link is parked on the operator. ``MEMBER``:
    a member session's lead, which is running and will be woken or is already
    at work. ``UNREACHABLE``: a member session's lead has to act and its session
    is not running, so nothing moves until somebody resumes it. ``IDLE``:
    nobody owes anything on it.
    """

    OPERATOR = "operator"
    MEMBER = "member"
    UNREACHABLE = "unreachable"
    IDLE = "idle"


@dataclass(frozen=True, slots=True)
class WhoseMove:
    """
    Whose move it is on one link, derived from the store each time it is
    asked (``Snapshot.whose_move``) and never stored.

    ``session`` is the member session whose lead has to act, for ``MEMBER``
    and ``UNREACHABLE``, and None otherwise. ``since`` is the oldest instant
    among the things that make it that owner's move, on the store's clock, as
    every obligation's ``since`` is; None for ``IDLE``.
    """

    owner: MoveOwner
    session: str | None = None
    since: float | None = None


class ThreadMark(enum.Enum):
    """
    Where one message on a link stands, as its thread says it
    (``Snapshot.thread``). Read from the message's state first and its times
    after: a message withdrawn after its lead read it is WITHDRAWN with
    ``delivered_at`` set, so neither the state alone ("never read") nor the
    time alone ("still delivered") says which it is.

    ``ANSWERED`` is a message some message from its recipient's session
    answers (``Snapshot.is_answered``), whether or not it asked for one. A
    withdrawn message is marked withdrawn even when a reply cites it.
    """

    POSTED = "posted"
    READ = "read"
    ANSWERED = "answered"
    WITHDRAWN_UNREAD = "withdrawn before it was read"
    WITHDRAWN_READ = "read, then withdrawn by its sender"


@dataclass(frozen=True, slots=True)
class ThreadWakeHeld:
    """
    A member's wake the link's chain held (``PeerWakes.held``), since
    ``since`` on the store's clock: the other thing on a link that is the
    operator's move without being a message or a decision.
    """

    session: str
    since: float


# The bus tool a lead withdraws a peer message with, as the gate is handed its
# name. Its arguments name no channel, so the link a parked withdrawal is on is
# the withdrawn message's (``Snapshot.parked_link``).
WITHDRAW_PEER_TOOL = "mcp__pptmstr__withdraw_peer_message"


class PeerRefusal(enum.Enum):
    """
    Why a lead's message to a peer was not sent (``Snapshot.peer_route`` and
    ``Snapshot.peer_send_refusal``).

    ``NOT_A_LEAD`` is a worker that should ask its own lead to send it.
    ``CLOSED`` is a name whose every link this store holds is closed;
    ``NO_SUCH_CHANNEL`` is a name no link this store holds has, most likely a
    typo -- or a link this run did not restore. ``NOT_A_MEMBER`` is a live link
    between two other sessions, which this sender cannot see into. Those three
    are apart for the store's tests and the operator's reading, and the sender
    is told one sentence for all of them (``channels.text._peer_refusal_text``): which it
    is would tell a lead probing names whether a link it is not on exists.
    ``ID_HELD`` is a message id the store already holds, which a minted id
    never is.

    The last three bind a send to the link it was written for, so a link made
    under the same name between the call and the filing does not take it to
    another lead. ``LINKED_AFTER`` is a link that went live in this process
    after the gate saw the call. ``OTHER_LINK`` is an ``re`` naming a peer
    message sent over a different link than the one the name resolves to now.
    ``UNHELD_RE`` is an ``re`` naming a message this store does not hold, so
    which link it came over cannot be asked: after a restart whose sender's
    session is not resumed yet, the message an answer cites may have come over
    a link since closed, and the name may now reach another lead.

    ``NOT_APPLIED`` is the member ``_apply`` never returns: the fallback for a
    request the store never saw, as on ``TaskRefusal``.
    """

    NOT_A_LEAD = "not_a_lead"
    NO_SUCH_CHANNEL = "no_such_channel"
    CLOSED = "closed"
    NOT_A_MEMBER = "not_a_member"
    ID_HELD = "id_held"
    LINKED_AFTER = "linked_after"
    OTHER_LINK = "other_link"
    UNHELD_RE = "unheld_re"
    NOT_APPLIED = "not_applied"


class PeerWithdrawRefusal(enum.Enum):
    """
    Why a withdrawal by an agent (``intents.ConcernWithdrawn`` with a node)
    was not applied (``store._message_withdrawal_refusal``).

    ``NOT_YOURS`` is a message this store does not hold, or one another agent
    sent: one member for both, so a lead cannot tell an id that exists from
    one that does not by trying to withdraw it. ``NOT_A_MEMBER`` is a peer
    message sent over a link that is closed, or that the withdrawer's session
    is no longer on. ``WITHDRAWN`` is one already withdrawn. ``ANSWERED`` is
    one its recipient's session has answered: the answer cites it, and
    withdrawing the question would leave the answer about nothing.
    ``ALREADY_READ`` is a concern inside a team that its recipient has read;
    only a peer message is withdrawn after it is read. ``TEAM_MAIL`` is a
    concern inside a team asked of ``withdraw_peer_message``, which withdraws
    only a message between teams (``intents.ConcernWithdrawn.peer_only``).

    ``NOT_APPLIED`` is the member ``_apply`` never returns: the fallback for a
    request the store never saw, as on ``PeerRefusal``.
    """

    NOT_YOURS = "not_yours"
    NOT_A_MEMBER = "not_a_member"
    WITHDRAWN = "withdrawn"
    ANSWERED = "answered"
    ALREADY_READ = "already_read"
    TEAM_MAIL = "team_mail"
    NOT_APPLIED = "not_applied"


class CitedQuestion(enum.Enum):
    """
    Where the message a withdrawn answer cited (its ``re``) stands once the
    answer is withdrawn (``Snapshot.cited_question``), so the withdrawer is
    told what is true of it and not that it is always owed again.

    ``OWED_AGAIN`` is a question its withdrawer is owed an answer to now
    (``Snapshot.awaiting_answer``). The rest are why not: ``ASKED_NONE``, it
    asked for no answer; ``WITHDRAWN``, its own sender withdrew it;
    ``STILL_ANSWERED``, another message from the withdrawer's session answers
    it; ``LINK_NOT_LIVE``, the link it came over is closed or not restored;
    ``UNREAD``, the withdrawer's lead has not been handed it, and it is owed
    once it is; ``NOT_HELD``, this run does not hold it.
    """

    OWED_AGAIN = "owed_again"
    ASKED_NONE = "asked_none"
    WITHDRAWN = "withdrawn"
    STILL_ANSWERED = "still_answered"
    LINK_NOT_LIVE = "link_not_live"
    UNREAD = "unread"
    NOT_HELD = "not_held"


@dataclass(frozen=True, slots=True)
class PeerIdentity:
    """
    Who a lead is, as another lead's link names it: its session id, its card's
    title and its project. Derived from the store's root record for the session
    (``peer_identity``) each time it is asked, never stored, so a session id
    names one lead alike in every reply and across a resume that restores the
    record.

    ``title`` is the card's title as the fleet draws it for a root
    (``ui.session_menu.card_title``): the record's task, typed by the operator or
    an agent, and never this tool's words. ``project`` is the last component of
    the directory the session's writes are measured from (``session_base``, or
    ``cwd`` on a record that has none) -- the name the fleet's project lane
    shows (``ui.projects.project_key``) without the filesystem read the lane
    makes. Either is empty where the store holds no such fact, and ``held`` is
    False when it holds no root record for the session at all.

    Both are drawable (``drawable.text``): the record holds its task and its
    directories verbatim (``store._VERBATIM``), and this value is what carries
    them into a lead's tool replies, its wakes and the operator's rows.
    """

    session: str
    title: str = ""
    project: str = ""
    held: bool = False


def logged_charter(text: str | None) -> Charter:
    """
    A logged charter mark as the member it names. One this build does not define,
    or none, reads as unstated, for the reason ``store._stated`` reads a basis so.
    """
    try:
        return Charter.UNSTATED if text is None else Charter(text)
    except ValueError:
        return Charter.UNSTATED


def channel_as_linked(
    channel: str, link: ChannelId | None, channels: Mapping[ChannelId, Channel]
) -> bool:
    """
    Whether a logged record's ``channel`` is one a step could have written for
    ``link``: a channel name, and the link's own name where the store holds the
    link. A link the store does not hold yet is not asked about its name here;
    the read asks it then (``Snapshot.peer_deliverable``,
    ``Snapshot.channel_decision_readable``).
    """
    if not is_channel_name(channel):
        return False
    held = None if link is None else channels.get(link)
    return held is None or held.name == channel
