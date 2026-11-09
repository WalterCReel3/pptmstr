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

**The file implementation is a journal, as a board log is** (``pptmstr.journal``).
One record per file, *linked* into place: ``os.link`` refuses a name that exists,
atomically, and the writer that lost the race takes the next ordinal, so two
windows appending to one link lose no fact. ``files.read`` gives the readable
prefix and where it broke. It reads under the board's kind table
(``boardlog.KINDS``), and a link fact's codec, the fold, ``judge``,
``owes_close`` and ``outcomes`` are pure, in ``channels.facts``.

**The logs are read once, at startup**, in the single-threaded slot where board
logs are migrated and before any session can start (``app._restore_channels``);
each log's links and its state come from that one read. Appends then go through
``ChannelWriter``, off the thread that draws.

**Nothing here deletes a log, and a name is never renamed**: the name is a
directory.
"""

from __future__ import annotations

import os
import stat
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .. import boardlog, serialworker, settings
from ..journal import codec, files
from ..journal.codec import RecordingStopped

# Re-exported: the seam's "could not look", which the shell matches on.
from ..journal.files import Unlisted as Unlisted
from ..serialworker import SerialWorker
from .facts import (
    ChannelState,
    Fact,
    LinkClosed,
    LinkCreated,
    LinkKey,
    Malformed,
    Read,
    Short,
    Stopped,
    Whole,
    assess,
    decode,
    encode,
    judge,
    owes_close,
    restore,
)
from .records import Channel, ChannelId, is_channel_name

# The longest charter a link is made with, in characters (``app.link_leads``).
# A character costs at most twelve bytes in a record -- an astral one is two
# ``\\uXXXX`` escapes -- so a charter this long is under a tenth of
# ``files.MAX_RECORD_BYTES``, and its link's first fact always fits.
CHARTER_CHARS = 8192


def root() -> Path:
    """
    The directory every link's log is kept under. Asked of ``settings`` each
    time, so a test's redirection of the configuration directory reaches it. Does
    not create it.
    """
    return settings.config_dir() / "channels"


# -- the seam ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Appended:
    """A fact is in the log, where a read will find it."""


@dataclass(frozen=True, slots=True)
class NotAppended:
    """A fact is not in the log where a read will find it, and why."""

    reason: str


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


def _withheld_after(position: int, log: codec.Log[boardlog.Record]) -> int:
    """
    How many record files follow the one at ``position`` in ``log``: the
    readable ones, then whatever ``log.damage`` withheld. A break that is a
    missing ordinal (``files.read`` calls it ``"missing"``) is no file; one
    that does not decode is a file of its own as well as its ``unread``.
    """
    after = len(log.records) - position - 1
    if log.damage is not None:
        after += log.damage.unread + (0 if log.damage.reason == "missing" else 1)
    return after


class FileChannels:
    """
    ``ChannelLog`` as one directory per link, at ``root / <name> / <link-id>``,
    laid out as a board log is (``journal.files``). ``base`` None is ``root()``,
    asked when this is built.
    """

    def __init__(self, base: Path | None = None) -> None:
        self.root = root() if base is None else base

    def _directory(self, link: LinkKey) -> Path | None:
        # The plain-name rule is also what keeps each part one directory entry
        # under the root (``records._channel_refusal``).
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
        written = files.append(directory, encode(fact))
        if isinstance(written, files.Unwritten):
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
        log = files.read(directory, boardlog.KINDS)
        if log.damage is not None and not log.records and log.damage.ordinal == 0:
            # ``files.read`` says a directory it cannot list and a first
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
            directory / files.name(log.damage.ordinal)
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

    ``files.read`` says both as one damage, in words. Opened without
    blocking for its reason (``ondisk.read_regular``): a FIFO under a record's
    name is not a regular file, which no later read gets past either.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return True
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return False
        os.read(fd, files.MAX_RECORD_BYTES + 1)
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


def _given_up(link: LinkKey, fact: Fact, reason: str) -> Outcome:
    return Outcome(link, fact, NotAppended(reason))


@dataclass(frozen=True, slots=True)
class Refused:
    """An operator's act the shell did not emit, and why."""

    reason: str


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
        self._serial: SerialWorker[LinkKey, Fact, Report] = SerialWorker(
            "channels", self._settle, _given_up
        )
        # Written by the worker alone, read by ``refusal`` from any thread, and
        # held under ``_stopping``: the links whose recording stopped in this
        # process, and why.
        self._stopping = threading.Lock()
        self._stopped: dict[LinkKey, str] = {}
        # The worker's alone: earlier links no sweep will ever close.
        self._settled: set[LinkKey] = set()

    def refusal(self, link: LinkKey) -> str | None:
        """
        Why a fact for ``link`` would be refused now, or None when it would be
        appended. Performs no IO; callable from any thread.
        """
        with self._stopping:
            stopped = self._stopped.get(link)
        if stopped is not None:
            return f"this link stopped being recorded at an earlier fact ({stopped})"
        return None

    def submit(self, link: LinkKey, fact: Fact) -> None:
        """
        Queue one fact and return. Performs no IO. After ``close`` the fact is
        not queued, and its outcome says so.
        """
        self._serial.submit(link, fact)

    def drain(self) -> tuple[Report, ...]:
        """Everything settled since the last call, oldest first. Never waits."""
        return self._serial.drain()

    @property
    def outstanding(self) -> int:
        """How many submitted facts have no outcome yet."""
        return self._serial.outstanding

    def close(self, timeout: float = serialworker.CLOSE_TIMEOUT) -> tuple[Report, ...]:
        """
        Stop taking facts, wait at most ``timeout`` seconds for the queued ones,
        and return everything not yet drained.

        A fact still queued when the wait ends is given up and reported as not
        appended, and the worker appends nothing further for it. The one fact
        the worker had already begun is reported the same way, and it alone may
        still reach disk afterwards: its reason says so.
        """
        return self._serial.close(timeout)

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
        with self._stopping:
            self._stopped[link] = result.reason
        marker = self._append(
            link,
            RecordingStopped(at=time.time(), reason=result.reason[: codec.MARKER_REASON_CHARS]),
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
