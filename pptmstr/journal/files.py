"""
A journal on disk: one directory, one record per file, appended and read in order.

**One file per record, no index.** ``brief.py`` makes the argument and it transfers
whole: a write above ``PIPE_BUF`` is not atomic, a document rewritten per event is a
corruption path, and the directory listing is the consistent prefix. Two windows
writing one journal are two writers, so a record here is *linked* into place by
``ondisk.create_once``: ``os.link`` refuses an existing name atomically, and the
writer that lost takes the next ordinal. Every append lists the directory first and
starts past the highest name there, because a writer told where to start can be
sent into the place of a missing record, and a record read back ahead of ones
written before it is a fact restored out of order.

What a record means is the caller's: ``read`` decodes under the ``codec.Kinds`` it
is handed, and ``append`` writes whatever ``codec.encode`` makes of a record.
"""

from __future__ import annotations

import itertools
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from .. import ondisk
from . import codec

R = TypeVar("R")

# `00000042.json`. The name is the ordinal and nothing else: a kind in the name
# would let two writers link two different names for one ordinal, and exclusivity
# is a property of the name. Eight digits because a long session passes a thousand
# records -- three per task, two per concern -- and zero-padding keeps `ls` in the
# order the reader uses. A log that outgrows the width still orders correctly,
# since the reader sorts by value.
_ORDINAL_WIDTH = 8
# Exactly how ``name`` spells an ordinal, and ASCII digits only: `\d` would take
# digits of other scripts, which ``int`` reads and no append writes.
_RECORD = re.compile(r"^([0-9]{8}|[1-9][0-9]{8,})\.json$")

# The most one record's file may hold. ``append`` refuses a record whose text is
# longer and ``read`` takes no more than this from a file, so what was appended can
# always be read back and a file of any size under a record's name costs a reader
# this much memory and no more. ``requests.MAX_RECORD_BYTES`` is the same figure
# for the same reason.
MAX_RECORD_BYTES = 1 << 20

# The longest name most filesystems take for one directory entry, in bytes.
_MAX_COMPONENT = 255


def session_dir(root: Path, session_id: str) -> Path | None:
    """
    Where one session's journal lives under ``root``, or None for an id that is
    not one directory name.

    The session id is the whole key. Nothing about the working directory or the
    brief enters it, because a resume from another directory and a fork that
    inherited a brief are both the cases where those would name the wrong board.

    **None rather than a path that leaves** ``root``. ``root / "../x"`` is beside
    it and ``root / "/etc"`` is ``/etc``, and ``append`` makes the directory it is
    given. An id with a separator or a NUL, an empty one, one that starts with a
    dot -- ``.`` and ``..`` among them -- one with no encoding as a file name, or
    one longer than a name may be, has no directory. A value and not a raise, for
    the reason ``Unwritten`` is one.
    """
    if (
        not session_id
        or session_id.startswith(".")
        or any(c in session_id for c in ("/", "\\", "\0"))
        or (os.altsep is not None and os.altsep in session_id)
    ):
        return None
    try:
        encoded = os.fsencode(session_id)
    except UnicodeEncodeError:
        return None
    if len(encoded) > _MAX_COMPONENT:
        return None
    return root / session_id


def name(ordinal: int) -> str:
    """The file name of the record at ``ordinal``."""
    return f"{ordinal:0{_ORDINAL_WIDTH}d}.json"


def _ordinals(names: Iterable[str]) -> list[int]:
    """
    The ordinals among ``names``, ascending: each name that is exactly how its own
    ordinal is spelled. ``000000099.json`` is nine digits for 99 and is not one.

    The one definition of which names are records, for the writer and the reader
    both. A name only the writer counted would put every later record past a gap,
    and one only the reader took would be a record no append could have made.
    """
    return sorted(
        int(match.group(1)) for name in names if (match := _RECORD.match(name)) is not None
    )


def _first_gap(ordinals: list[int]) -> int | None:
    """The lowest ordinal missing below the highest of ``ordinals``, or None."""
    return next((want for want, have in enumerate(ordinals) if have != want), None)


@dataclass(frozen=True, slots=True)
class Unlisted:
    """
    A directory exists, or may, and could not be listed, and why.

    Not zero. "No record is here" and "nobody could look" are different answers,
    and a writer that took the second for the first would start a log on top of
    one it could not see.
    """

    reason: str


def next_ordinal(directory: Path) -> int | Unlisted:
    """
    The ordinal after the highest one present, from the listing alone.

    Zero for a directory that is not there or holds no record. ``Unlisted`` for
    one that could not be listed for any other reason -- no permission, a file
    where the directory would be, an IO error for one moment -- as ``read``
    keeps the same two apart.

    This is also what a reader keys a memo on. A directory's mtime does not move
    between two appends made close together, so "has anything been added" is
    answered by the names and not by the timestamp.
    """
    try:
        present = _ordinals(os.listdir(directory))
    except FileNotFoundError:
        return 0
    except OSError as exc:
        return Unlisted(f"{type(exc).__name__}: {exc}")
    return present[-1] + 1 if present else 0


@dataclass(frozen=True, slots=True)
class Written:
    """
    A record is on disk.

    ``gap`` **is not None when this record will not be read back.** It is the
    lowest ordinal below this record's with no file, which is where ``read``
    stops, so the record is on disk behind a break and a restore will not reach
    it. The writer is told because nothing else will tell it: no append fills a
    missing ordinal, so the break is permanent and every later append lands
    behind it too.

    It is what the append's own listing shows, without reading a record. It does
    not see a record that is present and does not decode -- a torn write, a
    corrupted file. ``read`` finds those, and None here is not a statement that
    the log is whole.
    """

    ordinal: int
    path: Path
    gap: int | None = None


@dataclass(frozen=True, slots=True)
class Unwritten:
    """
    A record is not on disk, and why.

    A value rather than an exception because the caller is on the frame path, and
    because the operator has to be shown it: a board that silently stopped
    persisting is discovered at the next resume, by its absence.
    """

    reason: str


def append(directory: Path, record: codec.Kinded | codec.Unrecognised) -> Written | Unwritten:
    """
    Append one record. Never overwrites one, whoever else is writing.

    The text is staged in the same directory, flushed to the device, and then
    *linked* to its ordinal's name by ``ondisk.create_once``. A name therefore
    appears complete or not at all, and ``os.link`` fails on a name that exists --
    atomically, in the kernel, which is the guarantee a check before a rename cannot
    give. The writer that lost the name takes the next one, so two writers
    interleave and neither loses a record.

    **Never fills a missing ordinal.** The first name tried is one past the highest
    in a listing taken here, and a collision only moves up, so a record never lands
    below one that was there when its append began. That is why there is no way to
    tell this where to start: a caller's idea of the end is below the truth as soon
    as a second writer exists, and the first free name above a stale floor may be
    the place of a record that was lost. Filling it would turn a break ``read``
    reports into a whole-looking log with a newer fact ahead of older ones. The
    price is the listing, which is linear in the session's length on the thread
    that calls this.

    A record whose text is over ``MAX_RECORD_BYTES`` is refused, since ``read``
    would take it for damage and withhold everything after it.

    The flush before the link is the difference between surviving this process and
    surviving the machine, and costs milliseconds per record on the thread that
    calls this. The directory entry is not flushed: losing the newest names
    shortens the log to a consistent prefix and tears nothing.

    **A process killed inside this leaves its staged file, and nothing removes
    it.** An ``ondisk.TEMP_PREFIX`` name is never a record to ``read`` or to the
    next ordinal, so one left behind costs its bytes and nothing else. Removing
    them is not safe from here: another writer's append in flight is a file of
    exactly that shape, and unlinking it before its link would lose that record.
    """
    try:
        data = codec.encode(record).encode("utf-8")
        if len(data) > MAX_RECORD_BYTES:
            return Unwritten(
                reason=f"{len(data)} bytes, over the {MAX_RECORD_BYTES} a record may be"
            )
        directory.mkdir(parents=True, exist_ok=True)
        present = _ordinals(os.listdir(directory))
        start = present[-1] + 1 if present else 0
        linked = ondisk.create_once(directory, (name(n) for n in itertools.count(start)), data)
    except (OSError, ValueError) as exc:
        return Unwritten(reason=f"{type(exc).__name__}: {exc}")
    return Written(ordinal=start + linked.passed, path=linked.path, gap=_first_gap(present))


def read(directory: Path, kinds: codec.Kinds[R]) -> codec.Log[R | codec.Unrecognised]:
    """
    Every record up to the first one that cannot be read, oldest first, each
    decoded under ``kinds``.

    **Strict about order.** Records are facts about each other -- a claim means
    nothing without its declaration -- so the first ordinal that is absent or does
    not decode ends the log, and nothing after it is applied. What is returned is
    always a prefix of what was written.

    A missing directory is an empty log: a session that has put nothing on its
    board is the common case. A directory that exists and cannot be listed is
    damage, because those two must not read alike.

    Names that are not records are skipped. The directory holds temp files
    mid-write, and a reader that tripped on one would fail exactly while a write
    was in flight.

    **Never raises and never waits**, whatever the files hold: text built to break
    the parser is a record that does not decode, and a name that is a link, is not
    a regular file, or is a longer one than ``append`` writes, is one that cannot be
    read.
    """
    try:
        names = os.listdir(directory)
    except FileNotFoundError:
        return codec.Log()
    except OSError as exc:
        return codec.Log(damage=codec.Damage(ordinal=0, reason=f"{type(exc).__name__}: {exc}"))

    found = _ordinals(names)
    records: list[R | codec.Unrecognised] = []
    for position, ordinal in enumerate(found):
        unread = len(found) - position
        if ordinal != position:
            return codec.Log(tuple(records), codec.Damage(position, "missing", unread))
        try:
            text = ondisk.read_regular(directory / name(ordinal), MAX_RECORD_BYTES).decode("utf-8")
        except (OSError, ValueError) as exc:
            return codec.Log(
                tuple(records), codec.Damage(ordinal, f"{type(exc).__name__}: {exc}", unread - 1)
            )
        record = codec.decode(text, kinds)
        if isinstance(record, codec.Malformed):
            return codec.Log(tuple(records), codec.Damage(ordinal, record.reason, unread - 1))
        records.append(record)
    return codec.Log(tuple(records))
