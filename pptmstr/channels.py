"""
The links between leads on disk: one log per link, behind a seam a second
backend can stand behind.

**A link's facts live in its own log**, at ``root() / <name> / <link-id>``,
which is ``config_dir()/channels/<name>/<link-id>/``. Not in a member's board
log: a copy in each member's log makes a log unappendable for good in ordinary
use (an unlink logged by a member closed at link time is a stray) and a link
landing on a session mid-resume goes unrecorded. Not under ``boards/`` either,
which is ``boardsync.migrate``'s legacy root and would be carried off as a
session. The reasoning is in ``planning/2026-10-17-sqlite-inspection/q5-link-home.md``.

**One log per link, not per name** (Decided 9, revised). A name is unique only
among live links, so linking a name again -- after its earlier links were
closed, or were lost to a log that could not be read -- starts a fresh log under
a fresh id. Damage to one log then reaches one link. **Linking a name closes
every earlier link under it** whose log holds no close (``ChannelWriter``), so
a link withheld at startup -- which never reaches the store, and so is never
closed there -- stops counting against the name once the operator links it
again, and no failure leaves a name that can never be linked again.

**A link whose log is not whole restores as not live** (``restore_all``): a log
that was not read whole, holds a fact this build cannot place, or says its
writer stopped is not trusted to hold the link's close, so the link is not
trusted as open. That is "unlink wins", and it is why a close behind damage --
written, and never read back -- cannot leave a link open. The cost is that one
failed write closes a live link at the next start, and the operator links again.

**The seam is an append-only, ordered sequence of typed facts keyed by link**
(``ChannelLog``): append a fact, read every fact in order with where the read
broke, and say whether the log is whole, short or stopped. It has no query and
no update, because a current-state table loses what a log of facts keeps: a
contested fact marked as one, and an unknown kind making the log short rather
than vanishing. Listing the links is there as well, because the one reader --
the startup read of every log -- needs it from any backend, and it answers
"could not look" apart from "none". ``tests/test_channels.py`` holds one
contract test that every implementation must pass; ``FileChannels`` is the only
one today.

**The file implementation is boardlog's directory, unchanged.** One record per
file, *linked* into place: ``os.link`` refuses a name that exists, atomically,
and the writer that lost the race takes the next ordinal, so two windows
appending to one link lose no fact. ``boardlog.read`` gives the readable prefix
and where it broke. A link fact is a kind ``boardlog`` does not define, so it
crosses that layer as ``boardlog.Unrecognised``, whose text is written and read
back byte for byte, and is decoded here. ``boardlog.RecordingStopped`` is
shared: it means the same thing in either log.

**The log holds outcomes, as a board log does.** ``outcomes`` derives the facts
from what the store did with an operator's act, never from the act, so a refused
link writes nothing. ``restore`` folds one log's facts back: a second creation
is ``contested``, a fact about a link other than the log's own is ``stray``, and
a creation of other than ``LINK_MEMBERS`` members is ``unpaired`` (Decided 7, on
restore as on link). Each makes the log short.

**The logs are read once, at startup**, in the single-threaded slot where board
logs are migrated and before any session can start (``app._restore_channels``);
each log's links and its state come from that one read. Appends then go through
``ChannelWriter``, off the thread that draws.

**Nothing here deletes a log, and a name is never renamed**: the name is a
directory.
"""

from __future__ import annotations

import json
import math
import os
import queue
import stat
import threading
import time
from collections import Counter, deque
from collections.abc import Iterable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, ClassVar, Protocol, get_args, get_type_hints

from . import boardlog, settings
from .boardlog import RecordingStopped, Unrecognised

# Re-exported: the seam's "could not look", which the shell matches on.
from .boardlog import Unlisted as Unlisted
from .intents import ChannelClosed, ChannelLinked, Intent
from .model import LINK_MEMBERS, Channel, ChannelId, Snapshot, is_channel_name

# How long ``ChannelWriter.close`` waits for queued facts unless told otherwise.
CLOSE_TIMEOUT = 5.0

# The longest reason a stop marker carries, as ``boardsync.MARKER_REASON_CHARS``.
MARKER_REASON_CHARS = 400

# The longest charter a link is made with, in characters (``app.link_leads``).
# A character costs at most twelve bytes in a record -- an astral one is two
# ``\\uXXXX`` escapes -- so a charter this long is under a tenth of
# ``boardlog.MAX_RECORD_BYTES``, and its link's first fact always fits.
CHARTER_CHARS = 8192


def root() -> Path:
    """
    The directory every link's log is kept under. Asked of ``settings`` each
    time, so a test's redirection of the configuration directory reaches it. Does
    not create it.
    """
    return settings.config_dir() / "channels"


@dataclass(frozen=True, slots=True, order=True)
class LinkKey:
    """
    Which log: the channel name the link was made under, and its id. Both are
    one directory entry each, so each passes the plain-name rule
    (``model.is_channel_name``); a uuid does.
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
# A board kind of the same name would be decoded by ``boardlog`` before it reached
# the decoder here, and a link fact would read as a board record.
assert not set(_KINDS) & {cls.KIND for cls in get_args(boardlog.Known)}, "a kind is shared"
assert RecordingStopped.KIND not in _KINDS


@dataclass(frozen=True, slots=True)
class Malformed:
    """Why a record of a link kind is not one."""

    reason: str


def encode(fact: Fact) -> boardlog.Record:
    """
    ``fact`` as the record ``boardlog`` writes: its own text, carried as
    ``Unrecognised``, for a link fact; itself for the other two.

    Raises ``ValueError`` for a time that is not finite, as ``boardlog.encode``
    does; ``boardlog.append`` turns that into a value.
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
        return Unrecognised(at=record.at, kind=record.KIND, text=boardlog.encode(record)[:-1])
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
    text -- or ``Malformed``. Strict as ``boardlog``'s codec is: ``true`` is not a
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


# -- the seam ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Appended:
    """A fact is in the log, where a read will find it."""


@dataclass(frozen=True, slots=True)
class NotAppended:
    """A fact is not in the log where a read will find it, and why."""

    reason: str


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


class ChannelLog(Protocol):
    """
    Where link facts are kept: an append-only, ordered sequence of typed facts
    per link (``LinkKey``). Every implementation passes
    ``tests/test_channels.py``'s contract.

    - ``append`` never overwrites a fact, whoever else is appending: one that
      loses a race for a position takes the next, and two writers lose nothing.
      It keeps a fact as appended -- never reordered by its time, never merged
      with an equal one.
    - ``read`` is every fact up to the first that cannot be read, in the order
      appended, and the reason it stopped early; a link with no log is empty.
    - ``state`` is ``judge`` of ``read``: whether the read is the whole of the
      log, and why not. Startup asks ``read`` alone, so a log's links and its
      state come from one read.
    - ``links`` is every link that has a log, sorted, or ``Unlisted`` when
      where they are kept could not be looked at -- never an empty answer for
      that, since "none" lets the operator link a name that is live. Given a
      ``name``, it is that name's links alone, and only where that name's links
      are kept need be looked at.
    """

    def links(self, name: str | None = None) -> tuple[LinkKey, ...] | Unlisted: ...

    def append(self, link: LinkKey, fact: Fact) -> Appended | NotAppended: ...

    def read(self, link: LinkKey) -> Read: ...

    def state(self, link: LinkKey) -> ChannelState: ...


_NOT_A_KEY = "not a link's name and id, so it has no log"


def _withheld_after(position: int, log: boardlog.Log) -> int:
    """
    How many record files follow the one at ``position`` in ``log``: the
    readable ones, then whatever ``log.damage`` withheld. A break that is a
    missing ordinal (``boardlog.read`` calls it ``"missing"``) is no file; one
    that does not decode is a file of its own as well as its ``unread``.
    """
    after = len(log.records) - position - 1
    if log.damage is not None:
        after += log.damage.unread + (0 if log.damage.reason == "missing" else 1)
    return after


class FileChannels:
    """
    ``ChannelLog`` as one directory per link, at ``root / <name> / <link-id>``,
    laid out as ``boardlog`` lays out a board. ``base`` None is ``root()``,
    asked when this is built.
    """

    def __init__(self, base: Path | None = None) -> None:
        self.root = root() if base is None else base

    def _directory(self, link: LinkKey) -> Path | None:
        # The plain-name rule is also what keeps each part one directory entry
        # under the root (``model._channel_refusal``).
        if is_channel_name(link.name) and is_channel_name(link.link_id):
            return self.root / link.name / link.link_id
        return None

    def links(self, name: str | None = None) -> tuple[LinkKey, ...] | Unlisted:
        """
        Every link with a directory under a name under the root, sorted; given
        ``name``, every link under that name's directory, and no other is
        listed.

        A missing root is no links. A root, or a name's directory, that is there
        and cannot be listed is ``Unlisted``, whole: a live link may be in it.
        An entry that is not a plain name is not a link, nor is a name that is a
        file; a link-id entry that is not a directory is listed, and reads as
        damage.
        """
        if name is not None:
            if not is_channel_name(name):
                return ()
            under = self._links_under(name)
            return under if isinstance(under, Unlisted) else tuple(sorted(under))
        try:
            names = os.listdir(self.root)
        except FileNotFoundError:
            return ()
        except OSError as exc:
            return Unlisted(f"{self.root}: {type(exc).__name__}: {exc}")
        found: list[LinkKey] = []
        for each in filter(is_channel_name, names):
            under = self._links_under(each)
            if isinstance(under, Unlisted):
                return under
            found.extend(under)
        return tuple(sorted(found))

    def _links_under(self, name: str) -> list[LinkKey] | Unlisted:
        try:
            ids = os.listdir(self.root / name)
        except (FileNotFoundError, NotADirectoryError):
            return []
        except OSError as exc:
            return Unlisted(f"{self.root / name}: {type(exc).__name__}: {exc}")
        return [LinkKey(name, i) for i in ids if is_channel_name(i)]

    def append(self, link: LinkKey, fact: Fact) -> Appended | NotAppended:
        directory = self._directory(link)
        if directory is None:
            return NotAppended(_NOT_A_KEY)
        written = boardlog.append(directory, encode(fact))
        if isinstance(written, boardlog.Unwritten):
            return NotAppended(written.reason)
        if written.gap is not None:
            return NotAppended(
                f"written behind a break at ordinal {written.gap}, where a read stops"
            )
        return Appended()

    def read(self, link: LinkKey) -> Read:
        directory = self._directory(link)
        if directory is None:
            return Read(broken=_NOT_A_KEY)
        log = boardlog.read(directory)
        if log.damage is not None and not log.records and log.damage.ordinal == 0:
            # ``boardlog.read`` says a directory it cannot list and a first
            # record it cannot read alike; asked again, the listing tells them
            # apart.
            try:
                os.listdir(directory)
            except FileNotFoundError:
                pass
            except OSError as exc:
                return Read(broken=f"its directory cannot be listed: {type(exc).__name__}: {exc}")
        facts: list[Fact] = []
        for position, record in enumerate(log.records):
            fact = decode(record)
            if isinstance(fact, Malformed):
                return Read(
                    tuple(facts),
                    _broken(position, fact.reason, _withheld_after(position, log)),
                    damaged=True,
                )
            facts.append(fact)
        if log.damage is None:
            return Read(tuple(facts))
        damaged = log.damage.reason == "missing" or not _cannot_be_read(
            directory / boardlog._name(log.damage.ordinal)
        )
        return Read(
            tuple(facts),
            _broken(log.damage.ordinal, log.damage.reason, log.damage.unread),
            damaged,
        )

    def state(self, link: LinkKey) -> ChannelState:
        return judge(link, self.read(link))


def _cannot_be_read(path: Path) -> bool:
    """
    Whether the record file at ``path`` cannot be opened or read now -- which a
    later read may get past -- as against read and found not to be a record.

    ``boardlog.read`` says both as one damage, in words. Opened without
    blocking for its reason (``boardlog._file_bytes``): a FIFO under a record's
    name is not a regular file, which no later read gets past either.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return True
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return False
        os.read(fd, boardlog.MAX_RECORD_BYTES + 1)
    except OSError:
        return True
    finally:
        os.close(fd)
    return False


def _broken(ordinal: int, reason: str, withheld: int) -> str:
    """Where a read stopped, and how many records after it it withheld."""
    said = f"record {ordinal} cannot be read: {reason}"
    return f"{said}, and {withheld} after it are withheld" if withheld else said


# -- startup -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Restored:
    """
    One link's log as startup read it: the link it folds to, if any, the log's
    state, and why the link is not given to the store, if it is not.
    """

    link: LinkKey
    channel: Channel | None
    state: ChannelState
    withheld: str | None = None


def restore_all(log: ChannelLog) -> tuple[Restored, ...] | Unlisted:
    """
    Every link's log, read once and folded, or ``Unlisted`` when the links
    could not be listed.

    **A link is withheld from the store** -- read, reported, never live -- when
    its log is short or stopped, when the log holds no link, or when it is one
    of several live links under one name: two windows that each linked the
    name, neither seeing the other's. None of the several is trusted over the
    rest. The name is not lost to them: the operator's next link of it closes
    every one (``ChannelWriter``), and only the new link is live after that.

    A link whose id is also a link's under another name -- a directory copied
    by hand, since every link is minted a fresh id -- is withheld as well: the
    store keeps one channel per id, and would keep one of the two silently.
    """
    listed = log.links()
    if isinstance(listed, Unlisted):
        return listed
    first: list[Restored] = []
    for link in listed:
        read = log.read(link)
        folded = restore(link, read.facts)
        state = assess(folded, read.broken)
        withheld: str | None = None
        match state:
            case Short(reason=reason):
                withheld = f"its log is short: {reason}"
            case Stopped(reason=reason):
                withheld = f"its log was stopped: {reason}"
            case Whole() if folded.channel is None:
                withheld = "its log holds no link"
        first.append(Restored(link, folded.channel, state, withheld))
    names: dict[ChannelId, list[str]] = {}
    for link in listed:
        names.setdefault(link.link_id, []).append(link.name)
    second: list[Restored] = []
    for r in first:
        if r.withheld is None and len(names[r.link.link_id]) > 1:
            others = ", ".join(f"#{n}" for n in names[r.link.link_id] if n != r.link.name)
            r = Restored(r.link, r.channel, r.state, f"its id is also a link's under {others}")
        second.append(r)
    # Counted after the duplicate ids are withheld: a link that is never live
    # does not stand against its name's one live link.
    live = Counter(
        r.link.name for r in second if r.withheld is None and r.channel and r.channel.live
    )
    restored: list[Restored] = []
    for r in second:
        if r.withheld is None and r.channel and r.channel.live and live[r.link.name] > 1:
            r = Restored(
                r.link,
                r.channel,
                r.state,
                f"{live[r.link.name]} links under #{r.link.name} are live at once",
            )
        restored.append(r)
    return tuple(restored)


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
        case _:
            return ()


# -- the appender ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Outcome:
    """
    What became of one fact the writer was submitted. ``marker`` is set on the
    fact whose failure stopped the link's recording: what became of the stop
    marker tried after it. A close the sweep made is never one: it is a
    ``SweptClose``, inside the ``Swept`` report of the link that owed it.
    """

    link: LinkKey
    fact: Fact
    result: Appended | NotAppended
    marker: Appended | NotAppended | None = None


@dataclass(frozen=True, slots=True)
class SweptClose:
    """
    What became of the close the sweep appended to ``link``, an earlier link
    under the name just linked. ``created_at`` is ``link``'s creation as its
    log read, or None when the read showed none; ``result`` and ``marker`` are
    as an ``Outcome``'s. The fact appended is a ``LinkClosed`` of ``link``,
    the same record an operator's close writes.
    """

    link: LinkKey
    created_at: float | None
    result: Appended | NotAppended
    marker: Appended | NotAppended | None = None


@dataclass(frozen=True, slots=True)
class Swept:
    """
    What the sweep for ``made``'s creation did (``ChannelWriter._sweep``),
    reported apart from every submitted fact so it is never read as a close
    of ``made`` or as the operator's close.

    ``closes`` is each earlier link it closed or tried to, in the order tried,
    or ``Unlisted`` when the name's links could not be listed and nothing was
    tried. Reported only when one of those is so: a sweep that found nothing
    owed has nothing to say.

    **One report per creation, not per close**: a consumer counting closes
    counts inside ``closes``, never the ``Swept`` reports drained.
    """

    made: LinkKey
    closes: tuple[SweptClose, ...] | Unlisted


# What the writer reports: a submitted fact's ``Outcome``, or a creation's
# ``Swept``, drained before that creation's ``Outcome``.
Report = Outcome | Swept


@dataclass(frozen=True, slots=True)
class Refused:
    """An operator's act the shell did not emit, and why."""

    reason: str


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


class ChannelWriter:
    """
    The one appender of link facts in a process. ``submit`` from any thread,
    ``drain`` on the owner's schedule, ``close`` once at shutdown.

    **An append never runs on the thread that asked for it.** It costs a
    durable flush and a listing, and a mount that hangs would hold the frame.

    **A fact that does not land stops that link's recording for the process**:
    the writer refuses every later fact for the link, a close included, since a
    log missing a fact from its middle restores as a different history. It
    tries once to append a ``RecordingStopped`` saying why, which makes the log
    stopped and the link not live at the next start.

    **A link's creation first closes every earlier link under its name that
    could ever be live again**, and then appends the creation (``_sweep``). A
    link withheld at startup never reaches the store, so nothing else would
    ever close it, and a log of it that reads whole and live -- at the next
    start, or in a newer build -- would count against the name at every
    start. An earlier link is owed a close (``owes_close``) when its log:

    - reads whole, created the link, and holds no close of it -- withheld as
      one of several live, or live in another window;
    - is short only for what a newer build may place: a record of a kind this
      build does not define, which a newer build reads past, or a link of
      other than ``LINK_MEMBERS`` members, which a newer build may hold live;
    - could not be read now -- no permission, an I/O error -- and its readable
      part holds nothing that already rules the link out, since a later read
      may get past the failure and find the link live.

    Every other earlier link is left as it is, byte for byte: a log whose
    break is damage (a record that does not decode, a link fact of the wrong
    shape, a missing ordinal), since no read gets past it to a close appended
    behind it; one that holds a stop marker, a close of its link, a second
    creation or a fact about another link, since none of those is ever read
    as live again; and one that holds no link. That covers a link this process
    stopped writing whose marker landed, which reads stopped; one whose marker
    did not land may read whole and live, and is closed. A close appended to
    none of those adds nothing but a record, at every relink of the name.

    A close is never unsafe ("unlink wins"): it can only take a link out of
    view, so it is appended even to a link whose recording stopped in this
    process, past ``refusal``, and whatever history it leaves, it never leaves
    a link live that was not. It is stamped no earlier than its link's
    creation. A close that does not land stops that link's recording, as any
    fact does, and is reported; the new link is appended either way. A sweep
    close lost without a report, to a kill, is made at the next link of the
    name. An operator's own close lost that way is not: its link reads live at
    the next start and is in the store, so the name is refused as live, and it
    is the operator's close of it then that lands. A window that still shows
    a link closed by another window's sweep keeps it for its run, and it
    restores closed.

    **What a sweep costs**: one listing of the name's directory, and one read
    of each earlier link under the name not already settled in this process.
    Nothing deletes a log, so the reads grow with the number of links the
    name has ever had; a link this writer has found closed, stopped or
    damaged, or has closed itself, is settled for good -- a log is appended
    to, never rewritten -- and not read again by this writer.

    It needs nothing from the startup read: a link whose log was not whole is
    never in the store, so no fact for it is derived (``outcomes``), and a new
    link has a log of its own.
    """

    def __init__(self, log: ChannelLog) -> None:
        self.log = log
        self._changed = threading.Condition(threading.Lock())
        # Read and written only with ``_changed`` held.
        self._queue: queue.SimpleQueue[tuple[LinkKey, Fact] | None] = queue.SimpleQueue()
        self._pending: deque[tuple[LinkKey, Fact]] = deque()
        self._reports: list[Report] = []
        self._worker: threading.Thread | None = None
        self._closed = False
        self._abandoned = False
        # Written by the worker alone, read by ``refusal`` from any thread: the
        # links whose recording stopped in this process, and why.
        self._stopped: dict[LinkKey, str] = {}
        # The worker's alone: earlier links no sweep will ever close.
        self._settled: set[LinkKey] = set()

    def refusal(self, link: LinkKey) -> str | None:
        """
        Why a fact for ``link`` would be refused now, or None when it would be
        appended. Performs no IO; callable from any thread.
        """
        with self._changed:
            stopped = self._stopped.get(link)
        if stopped is not None:
            return f"this link stopped being recorded at an earlier fact ({stopped})"
        return None

    def submit(self, link: LinkKey, fact: Fact) -> None:
        """Queue one fact and return. Performs no IO."""
        with self._changed:
            if self._closed:
                self._reports.append(
                    Outcome(link, fact, NotAppended("submitted after the writer was closed"))
                )
                return
            self._pending.append((link, fact))
            self._queue.put((link, fact))
            if self._worker is None:
                self._worker = threading.Thread(target=self._run, name="channels", daemon=True)
                self._worker.start()

    def drain(self) -> tuple[Report, ...]:
        """Everything settled since the last call, oldest first. Never waits."""
        with self._changed:
            settled = tuple(self._reports)
            self._reports.clear()
        return settled

    def close(self, timeout: float = CLOSE_TIMEOUT) -> tuple[Report, ...]:
        """
        Stop taking facts, wait at most ``timeout`` seconds for the queued ones,
        and return everything not yet drained.

        A fact still queued when the wait ends is given up and reported as not
        appended; the oldest of them may be mid-append, and its reason says it
        may still reach disk.
        """
        with self._changed:
            self._closed = True
            if not self._abandoned:
                self._changed.wait_for(lambda: not self._pending, timeout=max(timeout, 0.0))
                self._abandoned = True
                for position, (link, fact) in enumerate(self._pending):
                    reason = (
                        "its append may have begun when the writer closed; it may still"
                        " reach disk"
                        if position == 0
                        else "still queued when the writer closed; not written"
                    )
                    self._reports.append(Outcome(link, fact, NotAppended(reason)))
                self._pending.clear()
                self._queue.put(None)
            settled = tuple(self._reports)
            self._reports.clear()
        return settled

    def _run(self) -> None:
        while (item := self._queue.get()) is not None:
            with self._changed:
                if self._abandoned:
                    return
            settled = self._settle(*item)
            with self._changed:
                if self._abandoned:
                    # ``close`` has already reported this fact.
                    return
                self._pending.popleft()
                self._reports.extend(settled)
                self._changed.notify_all()

    def _settle(self, link: LinkKey, fact: Fact) -> tuple[Report, ...]:
        """
        What became of ``fact``, after the closes a creation owes the earlier
        links under its name.
        """
        refused = self.refusal(link)
        if refused is not None:
            return (Outcome(link, fact, NotAppended(refused)),)
        swept = self._sweep(link) if isinstance(fact, LinkCreated) else None
        recorded = self._record(link, fact)
        return (recorded,) if swept is None else (swept, recorded)

    def _sweep(self, made: LinkKey) -> Swept | None:
        """
        Close every link under ``made``'s name, other than ``made``, that is
        owed a close (``owes_close``), and say what became of each close; or
        None when none was owed.

        When the name's links cannot be listed nothing is closed, and the
        report says so rather than naming any close.
        """
        listed = self._links(made.name)
        if isinstance(listed, Unlisted):
            return Swept(made, listed)
        swept: list[SweptClose] = []
        for earlier in listed:
            if earlier.name != made.name or earlier == made or earlier in self._settled:
                continue
            read = self._read(earlier)
            owed = owes_close(earlier, read)
            if owed is None:
                continue
            if owed is False:
                self._settled.add(earlier)
                continue
            created = restore(earlier, read.facts).channel
            at = time.time() if created is None else max(time.time(), created.created_at)
            closing = self._record(earlier, LinkClosed(at=at, channel_id=earlier.link_id))
            if isinstance(closing.result, Appended):
                self._settled.add(earlier)
            made_at = None if created is None else created.created_at
            swept.append(SweptClose(earlier, made_at, closing.result, closing.marker))
        return Swept(made, tuple(swept)) if swept else None

    def _record(self, link: LinkKey, fact: Fact) -> Outcome:
        """Append ``fact``, and stop ``link``'s recording if it does not land."""
        result = self._append(link, fact)
        if isinstance(result, Appended):
            return Outcome(link, fact, result)
        with self._changed:
            self._stopped[link] = result.reason
        marker = self._append(
            link, RecordingStopped(at=time.time(), reason=result.reason[:MARKER_REASON_CHARS])
        )
        return Outcome(link, fact, result, marker)

    def _append(self, link: LinkKey, fact: Fact) -> Appended | NotAppended:
        try:
            return self.log.append(link, fact)
        # Whatever an append raises is this fact's outcome: the worker ending on
        # it would drop every later fact without a word.
        except Exception as exc:  # noqa: BLE001
            return NotAppended(f"{type(exc).__name__}: {exc}")

    # The sweep's reads, guarded for ``_append``'s reason. A listing that
    # raises closes nothing and says so; a read that raises shows no close,
    # and the link is closed.

    def _links(self, name: str) -> tuple[LinkKey, ...] | Unlisted:
        try:
            return self.log.links(name)
        except Exception as exc:  # noqa: BLE001
            return Unlisted(f"{type(exc).__name__}: {exc}")

    def _read(self, link: LinkKey) -> Read:
        try:
            return self.log.read(link)
        except Exception as exc:  # noqa: BLE001
            return Read(broken=f"{type(exc).__name__}: {exc}")
