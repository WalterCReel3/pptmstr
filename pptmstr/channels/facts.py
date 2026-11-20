"""
A link's facts and what they say, with no disk and no thread: the values a
link's log (``channels.log``) holds, their codec, the fold that reads a link back
from them, the judgements on one read of a log, and the facts one step of the
store adds. Pure: no disk, no clock, no thread.

**A link fact crosses the journal as ``codec.Unrecognised``** (``encode``,
``decode``). A link's log is read under the board's kind table
(``boardlog.KINDS``), so a board record misplaced in a link's directory decodes
as the board would decode it, and one that does not is damage. A link fact is a
kind the board does not define, so it crosses that layer as ``Unrecognised``,
whose text is written and read back byte for byte, and is decoded here.
``codec.RecordingStopped`` is shared: it means the same thing in either log.

**The log holds outcomes, as a board log does.** ``outcomes`` derives the facts
from what the store did with an operator's act, never from the act, so a refused
link writes nothing. ``restore`` folds one log's facts back: a second creation
is ``contested``, a fact about a link other than the log's own is ``stray``, and
a creation of other than ``LINK_MEMBERS`` members is ``unpaired`` (Decided 7, on
restore as on link). Each makes the log short.

**One read of a log is judged here and made in ``channels.log``**: ``judge``
says what the log is, and ``owes_close`` whether the writer's sweep closes it.
What stays with the disk is what names ``journal.files.Unlisted``, the disk's
"could not look": the seam (``ChannelLog``), the startup read of every log
(``restore_all``) and the writer's reports.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass, fields
from typing import Any, ClassVar, assert_never, get_args, get_type_hints

from .. import boardlog
from ..intents import (
    AgentFinished,
    AgentRemoved,
    AgentResumed,
    AgentSpawned,
    ApprovalRequested,
    ApprovalResolved,
    BoardRead,
    BoardRestored,
    ChannelClosed,
    ChannelLinked,
    ChannelsRestored,
    CommandApproved,
    CommandEnded,
    CommandRefused,
    CommandRequested,
    CommandsRead,
    CommandsRestored,
    CommandStopAsked,
    CommandWithdrawn,
    CompactionObserved,
    ConcernEdited,
    ConcernPosted,
    ConcernWithdrawn,
    ContextPolled,
    DecisionAnswered,
    DecisionDismissed,
    DecisionRaised,
    DecisionsRead,
    DecisionWakeSent,
    DecisionWithdrawn,
    DigestRead,
    FailedSessionClosed,
    FailureAcknowledged,
    HeldMailDismissed,
    InboxRead,
    Intent,
    PeerInboxRead,
    PeerQuestionAged,
    PeerQuestionDismissed,
    PeerSent,
    PeerWakeSent,
    SessionDetached,
    SessionRecalled,
    SessionSettled,
    StateChanged,
    SubagentDelivered,
    SubagentProgress,
    TaskAmended,
    TaskClaimRequested,
    TaskClosed,
    TaskCompleted,
    TaskDeclared,
    TaskReleased,
    TaskVersionHanded,
    TaskWithdrawn,
    TopicChanged,
    UsageAccrued,
    WakeChainReleased,
)
from ..journal import codec
from ..journal.codec import RecordingStopped, Unrecognised
from ..model import Snapshot
from .records import LINK_MEMBERS, Channel, ChannelId


@dataclass(frozen=True, slots=True, order=True)
class LinkKey:
    """
    Which log: the channel name the link was made under, and its id. Both are
    one directory entry each, so each passes the plain-name rule
    (``records.is_channel_name``); a uuid does.
    """

    name: str
    link_id: ChannelId

    def __str__(self) -> str:
        return f"#{self.name} ({self.link_id})"


# -- the facts ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LinkCreated:
    """
    The operator linked ``members`` as the channel ``channel_id``. The channel's
    name is the log's key and is not repeated. ``at`` is wall-clock and is the
    channel's ``created_at``.
    """

    KIND: ClassVar[str] = "link-created"

    at: float
    channel_id: str
    members: tuple[str, ...]
    charter: str


@dataclass(frozen=True, slots=True)
class LinkClosed:
    """
    The channel ``channel_id`` is closed. ``at`` is its ``closed_at``.

    Written for the operator's close (``intents.ChannelClosed``) and by the
    writer's sweep, which closes every earlier link under a name when the
    operator links that name again (``ChannelWriter._sweep``). The fact does
    not say which: only the writer's report does, a sweep's being a ``Swept``.
    """

    KIND: ClassVar[str] = "link-closed"

    at: float
    channel_id: str


Known = LinkCreated | LinkClosed
# What a link's log holds, as read: a link fact, the writer's statement that it
# stopped appending, or a record of any other kind -- a newer build's, or a board
# record in the wrong directory -- carried and counted.
Fact = Known | RecordingStopped | Unrecognised

_KINDS: dict[str, type[Any]] = {cls.KIND: cls for cls in get_args(Known)}
_HINTS: dict[type[Any], dict[str, Any]] = {cls: get_type_hints(cls) for cls in _KINDS.values()}
# A board kind of the same name would be decoded under ``boardlog.KINDS`` before it reached
# the decoder here, and a link fact would read as a board record.
assert not set(_KINDS) & {cls.KIND for cls in get_args(boardlog.Known)}, "a kind is shared"
assert RecordingStopped.KIND not in _KINDS


@dataclass(frozen=True, slots=True)
class Malformed:
    """Why a record of a link kind is not one."""

    reason: str


def encode(fact: Fact) -> boardlog.Record:
    """
    ``fact`` as the record ``files.append`` writes: its own text, carried as
    ``Unrecognised``, for a link fact; itself for the other two.

    Raises ``ValueError`` for a time that is not finite, as ``codec.encode``
    does; ``files.append`` turns that into a value.
    """
    if isinstance(fact, RecordingStopped | Unrecognised):
        return fact
    payload: dict[str, Any] = {"kind": fact.KIND}
    for each in fields(fact):
        value = getattr(fact, each.name)
        payload[each.name] = list(value) if isinstance(value, tuple) else value
    return Unrecognised(at=fact.at, kind=fact.KIND, text=json.dumps(payload, allow_nan=False))


def decode(record: boardlog.Record) -> Fact | Malformed:
    """
    The fact a record read from a link's directory holds.

    A record of a kind defined here that does not have this kind's shape is
    ``Malformed``: it was written as a link fact and cannot be read as one,
    which is damage and not a newer kind. A board record in a link's directory
    is of no kind a link has, and is carried as ``Unrecognised``.
    """
    if isinstance(record, RecordingStopped):
        return record
    if not isinstance(record, Unrecognised):
        return Unrecognised(at=record.at, kind=record.KIND, text=codec.encode(record)[:-1])
    cls = _KINDS.get(record.kind)
    if cls is None:
        return record
    try:
        payload = json.loads(record.text)
    except (ValueError, RecursionError) as exc:
        return Malformed(f"{record.kind}: not JSON: {exc}")
    if not isinstance(payload, dict):
        return Malformed(f"{record.kind}: not a JSON object")
    values: dict[str, Any] = {}
    for each in fields(cls):
        value = _as(payload.get(each.name), _HINTS[cls][each.name])
        if isinstance(value, Malformed):
            return Malformed(f"{record.kind}.{each.name}: {value.reason}")
        values[each.name] = value
    fact: Known = cls(**values)
    return fact


def _as(value: Any, hint: Any) -> Any:
    """
    ``value`` as the field type ``hint`` -- a finite time, text, or a list of
    text -- or ``Malformed``. Strict as ``codec``'s is: ``true`` is not a
    time and a missing field is not an empty one.
    """
    if hint is float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            return Malformed("not a number")
        try:
            number = float(value)
        except OverflowError:
            return Malformed("too large to be a time")
        return number if math.isfinite(number) else Malformed("not finite")
    if hint is str:
        return value if isinstance(value, str) else Malformed("not text")
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return Malformed("not a list of text")
    return tuple(value)


# -- what a log says ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Whole:
    """The log was read to its end and holds nothing this build cannot place."""


@dataclass(frozen=True, slots=True)
class Short:
    """
    The log holds more than the link restored from it shows, and why: a record
    past a break, of a kind this build does not define, contested, about
    another link, or a link that is not a pair.
    """

    reason: str


@dataclass(frozen=True, slots=True)
class Stopped:
    """A writer stopped appending to the log, and its words for why."""

    reason: str


ChannelState = Whole | Short | Stopped


@dataclass(frozen=True, slots=True)
class Folded:
    """
    What one link's facts say, folded: the link, if the log created it, and
    every fact that could not be applied.
    """

    channel: Channel | None = None
    stray: tuple[Fact, ...] = ()
    contested: tuple[Fact, ...] = ()
    unpaired: tuple[LinkCreated, ...] = ()
    unrecognised: tuple[Unrecognised, ...] = ()
    stopped: tuple[RecordingStopped, ...] = ()


def restore(link: LinkKey, facts: Iterable[Fact]) -> Folded:
    """
    The link ``facts`` describe, as the log ``link`` holds them. Pure.

    The log's own link is created once and closed at most once.

    - A second creation is what two writers each allowed and one reducer would
      not have: ``contested``, and it changes nothing.
    - A fact naming an id other than ``link.link_id`` -- or a close before any
      creation -- is ``stray``.
    - A creation of other than ``LINK_MEMBERS`` distinct members is
      ``unpaired`` and creates nothing: a later build that widened links, or a
      hand, wrote a link this build's routing would misread (Decided 7).
    - A close of a link already closed changes nothing and is not contested:
      two windows that each closed it agree, and closing is idempotent.
    """
    channel: Channel | None = None
    stray: list[Fact] = []
    contested: list[Fact] = []
    unpaired: list[LinkCreated] = []
    unrecognised: list[Unrecognised] = []
    stopped: list[RecordingStopped] = []
    for fact in facts:
        match fact:
            case LinkCreated() | LinkClosed() if fact.channel_id != link.link_id:
                stray.append(fact)
            case LinkCreated():
                if channel is not None or unpaired:
                    contested.append(fact)
                elif len(set(fact.members)) != LINK_MEMBERS:
                    unpaired.append(fact)
                else:
                    channel = Channel(
                        id=fact.channel_id,
                        name=link.name,
                        members=frozenset(fact.members),
                        charter=fact.charter,
                        created_at=fact.at,
                    )
            case LinkClosed():
                if channel is None:
                    stray.append(fact)
                elif channel.live:
                    channel = Channel(
                        id=channel.id,
                        name=channel.name,
                        members=channel.members,
                        charter=channel.charter,
                        created_at=channel.created_at,
                        closed_at=fact.at,
                    )
            case RecordingStopped():
                stopped.append(fact)
            case Unrecognised():
                unrecognised.append(fact)
    return Folded(
        channel=channel,
        stray=tuple(stray),
        contested=tuple(contested),
        unpaired=tuple(unpaired),
        unrecognised=tuple(unrecognised),
        stopped=tuple(stopped),
    )


def assess(folded: Folded, broken: str | None = None) -> ChannelState:
    """
    What a log with these folded facts, and this break if it had one, is.

    Short over stopped: a log that is both is missing facts no record names
    as well as the ones its writer gave up on, and the reason says both.
    """
    reasons = [] if broken is None else [broken]
    if folded.unrecognised:
        kinds = sorted({u.kind for u in folded.unrecognised})
        reasons.append(
            f"{len(folded.unrecognised)} record(s) of a kind this build does not"
            f" define ({', '.join(kinds)})"
        )
    if folded.unpaired:
        counts = ", ".join(str(len(set(u.members))) for u in folded.unpaired)
        reasons.append(f"a link of {counts} member(s), where a link joins {LINK_MEMBERS}")
    if folded.contested:
        reasons.append(f"{len(folded.contested)} record(s) one writer could not have appended")
    if folded.stray:
        reasons.append(f"{len(folded.stray)} record(s) about a link the log never created")
    halted = [f"its writer stopped: {s.reason}" for s in folded.stopped]
    if reasons:
        return Short("; ".join(reasons + halted))
    if halted:
        return Stopped("; ".join(halted))
    return Whole()


# -- one read of a log ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Read:
    """
    One read of a log: every fact up to the first that cannot be read, in the
    order appended, and why the read stopped there, if it stopped early.

    ``damaged`` says the break is in what the log holds -- a record that does
    not decode, or is not a link fact's shape, or an ordinal that is missing --
    so no later read gets past it. False for a break that is a failure to look
    (no permission, an I/O error), which a later read may get past, and for a
    read that did not break.
    """

    facts: tuple[Fact, ...] = ()
    broken: str | None = None
    damaged: bool = False


def judge(link: LinkKey, read: Read) -> ChannelState:
    """What the log ``link`` is, from one read of it. Pure."""
    return assess(restore(link, read.facts), read.broken)


def owes_close(link: LinkKey, read: Read) -> bool | None:
    """
    Whether the next link of ``link``'s name owes it a close, from one read of
    its log: True when the link could ever be read live again (the cases are
    ``ChannelWriter``'s), False when it never can, whatever is appended to the
    log after now, and None when the log holds no link yet -- an empty
    directory, or one whose creation is still being written -- which no close
    is owed now and a later one may be. Pure.
    """
    if read.broken is not None and read.damaged:
        return False
    folded = restore(link, read.facts)
    if folded.stopped or folded.stray or folded.contested:
        return False
    if folded.channel is not None and not folded.channel.live:
        return False
    if read.broken is not None or folded.channel is not None or folded.unpaired:
        return True
    return None


# -- what the store did ------------------------------------------------------------


def outcomes(
    before: Snapshot, intent: Intent, after: Snapshot
) -> tuple[tuple[LinkKey, Known], ...]:
    """
    The facts one applied step adds to link logs, as (link, fact). Pure.

    Read off the two snapshots, so a link the store refused, and a close of a
    channel already closed or never held, add nothing.
    """
    match intent:
        case ChannelLinked():
            made = after.channels.get(intent.channel_id)
            if made is None or intent.channel_id in before.channels:
                return ()
            return (
                (
                    LinkKey(made.name, made.id),
                    LinkCreated(
                        at=made.created_at,
                        channel_id=made.id,
                        members=tuple(sorted(made.members)),
                        charter=made.charter,
                    ),
                ),
            )
        case ChannelClosed():
            was = before.channels.get(intent.channel_id)
            now = after.channels.get(intent.channel_id)
            if was is None or now is None or not was.live or now.closed_at is None:
                return ()
            return ((LinkKey(now.name, now.id), LinkClosed(at=now.closed_at, channel_id=now.id)),)
        case (
            AgentSpawned()
            | FailureAcknowledged()
            | StateChanged()
            | TopicChanged()
            | UsageAccrued()
            | ContextPolled()
            | CompactionObserved()
            | ApprovalRequested()
            | ApprovalResolved()
            | AgentFinished()
            | SubagentProgress()
            | SubagentDelivered()
            | AgentResumed()
            | AgentRemoved()
            | ConcernPosted()
            | InboxRead()
            | PeerSent()
            | PeerInboxRead()
            | ConcernEdited()
            | ConcernWithdrawn()
            | TaskAmended()
            | TaskVersionHanded()
            | TaskDeclared()
            | TaskClaimRequested()
            | TaskCompleted()
            | TaskReleased()
            | TaskClosed()
            | TaskWithdrawn()
            | BoardRead()
            | DigestRead()
            | BoardRestored()
            | DecisionRaised()
            | DecisionAnswered()
            | DecisionDismissed()
            | DecisionWithdrawn()
            | DecisionsRead()
            | CommandRequested()
            | CommandApproved()
            | CommandRefused()
            | CommandWithdrawn()
            | CommandStopAsked()
            | CommandEnded()
            | CommandsRead()
            | CommandsRestored()
            | SessionRecalled()
            | SessionDetached()
            | FailedSessionClosed()
            | ChannelsRestored()
            | SessionSettled()
            | WakeChainReleased()
            | PeerWakeSent()
            | DecisionWakeSent()
            | PeerQuestionAged()
            | HeldMailDismissed()
            | PeerQuestionDismissed()
        ):
            # A restore is read back from these logs, so it writes nothing to
            # them; nothing else here is about a link.
            return ()
        case _:
            assert_never(intent)
