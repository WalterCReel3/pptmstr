"""
What pptmstr knows about one session, on disk: ``sessions/<session id>/session.json``.

**A session file is decoration, never evidence.** ``sessions.py`` states the rule
and it holds here unchanged: the CLI's transcripts decide which sessions exist, and
a file here adds what a transcript cannot say -- the gate it was granted, whether
it was contained, whether the operator took it off the fleet -- to a session that
does. A file whose transcript is gone describes nothing that can be resumed, and
saying so belongs to whoever holds the transcript listing. Nothing in this module
asks whether a session exists.

**One directory per session, one file per directory, no index.** A write replaces
one session's file whole and touches no other session's, so a window changing one
card cannot undo a change to another. The directory also holds ``board/``, the
session's board log (``board_dir``), and is where a ``lock`` naming an owning
process goes when two windows on one fleet are guarded against. Every entry in it
other than ``session.json`` is ignored here.

**Versioning.** ``format`` is ``FORMAT`` and changes only when a file written by
the new build would be misread by this one. Everything short of that is additive:
a key this build does not know is ignored on read and written back untouched, and
a key the file lacks reads as a default that claims nothing. A file whose
``format`` is newer than ``FORMAT`` is shown from the keys this build knows and is
never written (``SessionFile.writable``), since this build cannot know what its
change would mean to the build that wrote it.

**A file that is there and cannot be read is never replaced.** ``update`` is the
only writer and it refuses, with the reason, whenever ``read`` would not return a
writable record: text that is not JSON, JSON that is not an object, an ``in_fleet``
that is not a bool, a ``session_id`` that is not the directory's name, a newer
``format``, a name that is not a regular file. Each of those is somebody's text --
a later build's, or the operator's with a typo in it -- and a file that parses and
says something else is not a repair of it. The two fatal keys are the two that
change what happens to the session: whether it is on the fleet, and which session
the file is about. Any other known key with a value of the wrong type reads as its
default and is left on disk as it is.

**Nothing here deletes anything.** A session leaves the fleet by ``in_fleet``
becoming false. No function removes a file or a directory but the temp file a
write made for itself.

**Two windows writing one file are last-writer-wins, and the file is never torn.**
A write goes to a temp file in the session's directory, is flushed to the device
and renamed over the name, so a reader sees one writer's whole file or the other's.
``update`` re-reads the file and changes only the keys it was given, so two windows
changing different keys both keep their change -- except when one reads between
the other's read and its rename, where the later rename wins whole. Nothing locks
that interval, and it is also the one in which a file broken by somebody else
after ``update`` read it is renamed over. A file that does not exist yet is
*linked* into place rather than renamed over, so of two windows creating one file
the second merges into the first's, and a name that was free when it was read is
never taken from whatever appeared under it since.

**Nothing here is in the snapshot, nothing here starts a thread, and every function
that touches the disk blocks.** Which thread calls it belongs to whoever wires this.
"""

from __future__ import annotations

import contextlib
import itertools
import json
import math
import os
import stat
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import boardlog, settings
from .approval import Policy

# The format this build writes, and the newest it will write over.
FORMAT = 1

FILE_NAME = "session.json"
_SESSIONS = "sessions"
_BOARD = "board"

# The most a session file may hold. ``update`` refuses to write a longer one and
# ``read`` takes no more than this from a name, so what was written can always be
# read back and a file of any size under the name costs a reader this much memory.
MAX_FILE_BYTES = 1 << 20

# The longest ``last_message`` that is stored. It prefills a form's message box; a
# pasted log is not something an operator sends twice, and it would crowd the file
# towards ``MAX_FILE_BYTES``.
MAX_LAST_MESSAGE = 24_000

# The most directory entries one ``listing`` takes from the root, session or not.
MAX_SESSIONS = 10_000

ENDED_HOW = ("closed", "failed", "shutdown")

# How many times ``update`` starts over when a file it found absent was created
# under it. Each retry is another window's write landing in the same instant.
_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class UnknownPolicy:
    """
    The file does not say which rung the session was granted, in a spelling this
    build knows.

    Its own type and not a member of ``Policy``, so that no reader can act on it
    as a rung: a missing or unrecognised gate is not ``STRICT``, and it is
    certainly not the one the text most resembles. ``stated`` is what the file
    held when that was text, for a reader that shows it.
    """

    stated: str | None = None


@dataclass(frozen=True, slots=True)
class SessionFile:
    """
    One session's file, as this build reads it.

    Every default is what a file without the key reads as, and claims nothing: no
    time, no text, not contained, a gate nobody stated. ``in_fleet`` is the one
    default that says something -- a session with a file is on the fleet until
    the operator removes it.

    The times are wall-clock seconds. "Closed" is not a field: at startup every
    session is closed, and a stored flag would be wrong after a crash and wrong
    under two windows. ``ended_at`` and ``ended_how`` are for display and are
    None for a session that is live or was killed.
    """

    session_id: str
    format: int = FORMAT
    in_fleet: bool = True
    created_at: float | None = None
    launched_at: float | None = None
    last_active: float | None = None
    ended_at: float | None = None
    ended_how: str | None = None
    task: str = ""
    last_message: str | None = None
    model: str = ""
    cwd: str = ""
    session_base: str | None = None
    template: str | None = None
    brief: str | None = None
    policy: Policy | UnknownPolicy = UnknownPolicy()
    contained: bool = False
    subagent_cap: int | None = None
    cost_usd: float | None = None
    error: str | None = None

    @property
    def writable(self) -> bool:
        """
        Whether ``update`` will write this file: false for one a newer build wrote.
        """
        return self.format <= FORMAT


@dataclass(frozen=True, slots=True)
class Absent:
    """
    No session file is there. The only finding ``update`` creates one over.
    """


@dataclass(frozen=True, slots=True)
class Unreadable:
    """
    Something is under the name and it is not a session file this build can read.
    It is not shown as a session and it is never written over.
    """

    reason: str


Found = SessionFile | Absent | Unreadable


@dataclass(frozen=True, slots=True)
class Written:
    """
    The file is on disk and ``record`` is what it now reads as.

    ``skipped`` names the keys that were given and deliberately left as they were:
    ``task`` when the file already had one, ``last_message`` when the new one is
    over ``MAX_LAST_MESSAGE``.
    """

    path: Path
    record: SessionFile
    skipped: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Unwritten:
    """
    Nothing was written, the file is as it was, and why.

    A value rather than an exception for the reason ``boardlog.Unwritten`` is one:
    the operator has to be shown it, and a fleet that silently stopped being kept
    is discovered at the next start, by its absence.
    """

    reason: str


@dataclass(frozen=True, slots=True)
class Listing:
    """
    Every session directory under the root, by id, with what its file read as.

    A directory with no ``session.json`` is here as ``Absent``: it is a session
    pptmstr holds something for -- a board log -- and has no file about.

    ``problem`` is not None when this is not the whole root: it could not be
    listed, or it holds more than ``MAX_SESSIONS`` entries.
    """

    sessions: tuple[tuple[str, Found], ...] = ()
    problem: str | None = None


# -- values at the boundary -------------------------------------------------------

# What a decoder returns for a value it does not take.
_BAD: Any = object()


def _text(value: object) -> Any:
    return value if isinstance(value, str) else _BAD


def _flag(value: object) -> Any:
    return value if isinstance(value, bool) else _BAD


def _number(value: object) -> Any:
    # ``bool`` is an ``int`` to ``isinstance``, and ``true`` is not a time.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _BAD
    try:
        number = float(value)
    except OverflowError:
        return _BAD
    return number if math.isfinite(number) else _BAD


def _whole(value: object) -> Any:
    return value if isinstance(value, int) and not isinstance(value, bool) else _BAD


def _how(value: object) -> Any:
    return value if isinstance(value, str) and value in ENDED_HOW else _BAD


def _rung(value: object) -> Any:
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            return Policy(value)
    return _BAD


def _or_null(inner: Callable[[object], Any]) -> Callable[[object], Any]:
    return lambda value: None if value is None else inner(value)


# Every key but the two the directory and the build decide (``session_id``,
# ``format``): how its value is read, and what a file without it reads as.
# ``tests/test_sessionfile.py`` holds this against the fields of ``SessionFile``.
_FIELDS: dict[str, tuple[Callable[[object], Any], Any]] = {
    "in_fleet": (_flag, True),
    "created_at": (_number, None),
    "launched_at": (_number, None),
    "last_active": (_number, None),
    "ended_at": (_or_null(_number), None),
    "ended_how": (_or_null(_how), None),
    "task": (_text, ""),
    "last_message": (_or_null(_text), None),
    "model": (_text, ""),
    "cwd": (_text, ""),
    "session_base": (_or_null(_text), None),
    "template": (_or_null(_text), None),
    "brief": (_or_null(_text), None),
    "policy": (_rung, UnknownPolicy()),
    "contained": (_flag, False),
    "subagent_cap": (_or_null(_whole), None),
    "cost_usd": (_or_null(_number), None),
    "error": (_or_null(_text), None),
}


def decode(obj: object, *, session_id: str) -> SessionFile | Unreadable:
    """
    Read a parsed JSON value as the file of the session whose directory is
    ``session_id``. Total: any value gives a record or a reason, and nothing raises.

    Unknown keys are ignored. A missing key reads as its default, and so does a
    known key whose value is of the wrong type -- with three exceptions, each
    ``Unreadable``:

    - ``in_fleet`` that is not a bool. Its default is true, so reading ``"false"``
      as the default would put back a session the operator removed.
    - ``session_id`` that is not ``session_id``. The file is about another session,
      or was copied here by hand.
    - ``format`` that is not a whole number of at least 1, since whether the file
      is newer than this build cannot be told.

    A ``policy`` this build does not know is ``UnknownPolicy`` and never a rung.
    A ``format`` above ``FORMAT`` gives a record whose ``writable`` is false.
    """
    if not isinstance(obj, dict):
        return Unreadable(f"not a JSON object but {_kind(obj)}")
    fmt = obj.get("format", FORMAT)
    if _whole(fmt) is _BAD or fmt < 1:
        return Unreadable("'format' is not a whole number of at least 1")
    stated = obj.get("session_id", session_id)
    if stated != session_id:
        return Unreadable(
            f"'session_id' is {_shown(stated)}, which is not the name of its directory"
        )
    values: dict[str, Any] = {}
    for key, (read_value, default) in _FIELDS.items():
        if key not in obj:
            continue
        value = read_value(obj[key])
        if value is not _BAD:
            values[key] = value
        elif key == "in_fleet":
            return Unreadable(f"'in_fleet' is {_shown(obj[key])}, which is not true or false")
        elif key == "policy":
            values[key] = UnknownPolicy(obj[key] if isinstance(obj[key], str) else None)
        else:
            values[key] = default
    return SessionFile(session_id=session_id, format=fmt, **values)


def _kind(value: object) -> str:
    if value is None:
        return "null"
    return {bool: "a bool", list: "a list", str: "text"}.get(type(value), "a number")


def _shown(value: object) -> str:
    """
    A value for a reason an operator reads: short, and on one line.
    """
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


# -- paths ------------------------------------------------------------------------


def _under(root: Path | None) -> Path:
    # Through the module and at call time: a test redirects ``settings.config_dir``,
    # and a name bound by ``from``-import, or a path computed at import, would be
    # the operator's real directory.
    return root if root is not None else settings.config_dir() / _SESSIONS


def root() -> Path:
    """
    The directory every session's directory is in. Does not create it.
    """
    return _under(None)


def session_dir(session_id: str, root: Path | None = None) -> Path | None:
    """
    Where pptmstr keeps what it knows about one session, or None for an id that
    is not exactly one directory name (``boardlog.session_dir`` has the rule).
    """
    return boardlog.session_dir(_under(root), session_id)


def file_path(session_id: str, root: Path | None = None) -> Path | None:
    """
    The session's ``session.json``, or None for an id that has no directory.
    """
    directory = session_dir(session_id, root)
    return None if directory is None else directory / FILE_NAME


def board_dir(session_id: str, root: Path | None = None) -> Path | None:
    """
    The session's board log, or None for an id that has no directory.
    """
    directory = session_dir(session_id, root)
    return None if directory is None else directory / _BOARD


# -- reading ----------------------------------------------------------------------


def _not_an_id(session_id: str) -> str:
    return f"{_shown(session_id)} is not a session id: it is not one directory name"


def _file_bytes(path: Path) -> bytes:
    """
    The bytes of a regular file no longer than a session file may be. ``OSError``
    for anything else.

    Judged through the descriptor, not by a ``stat`` of the name beforehand.
    ``O_NONBLOCK`` makes opening a FIFO return and ``O_NOFOLLOW`` refuses a link
    outright: ``update`` only ever renames or links a regular file into place, so
    a link under the name is not something this module wrote. Read to one byte
    past the limit and no further, so the file's size is never the allocation's.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{path.name} is not a regular file")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            held = handle.read(MAX_FILE_BYTES + 1)
        if len(held) > MAX_FILE_BYTES:
            raise OSError(f"{path.name} is over the {MAX_FILE_BYTES} bytes a session file may be")
        return held
    finally:
        if fd != -1:
            os.close(fd)


def _not_json(constant: str) -> Any:
    # ``json.loads`` takes NaN and Infinity, which are not JSON and which this
    # module could not write back.
    raise ValueError(f"{constant} is not JSON")


def _load(path: Path) -> dict[str, Any] | Absent | Unreadable:
    """
    The object in the file, before any key is interpreted.
    """
    try:
        held = _file_bytes(path)
    except (FileNotFoundError, NotADirectoryError):
        return Absent()
    except OSError as exc:
        return Unreadable(f"{type(exc).__name__}: {exc}")
    try:
        obj = json.loads(held.decode("utf-8"), parse_constant=_not_json)
    except (ValueError, RecursionError) as exc:
        return Unreadable(f"not JSON ({type(exc).__name__}: {_shown(str(exc))})")
    if not isinstance(obj, dict):
        return Unreadable(f"not a JSON object but {_kind(obj)}")
    return obj


def read(session_id: str, root: Path | None = None) -> Found:
    """
    One session's file: the record, ``Absent``, or ``Unreadable`` with the reason.

    **Never raises and never waits**, whatever is under the name: a FIFO, a link,
    a directory and a file over ``MAX_FILE_BYTES`` are each ``Unreadable``, as is
    an id that is not one directory name. ``Absent`` means nothing is there at
    all, and is the only answer ``update`` will create a file on.
    """
    try:
        path = file_path(session_id, root)
        if path is None:
            return Unreadable(_not_an_id(session_id))
        found = _load(path)
        if isinstance(found, (Absent, Unreadable)):
            return found
        return decode(found, session_id=session_id)
    except (OSError, ValueError, RuntimeError) as exc:
        return Unreadable(f"{type(exc).__name__}: {exc}")


def listing(root: Path | None = None) -> Listing:
    """
    Every session directory under the root, in id order, with ``read``'s answer
    for each. Never raises.

    A root that does not exist is an empty listing: a first launch. Entries that
    are not directories, links to directories, and directories whose name is not
    a session id are skipped. No more than ``MAX_SESSIONS`` entries are taken,
    and ``problem`` says when there were more.

    It reads one file per session and blocks for all of them.
    """
    try:
        base = _under(root)
        ids: list[str] = []
        with os.scandir(base) as entries:
            taken = list(itertools.islice(entries, MAX_SESSIONS + 1))
        for entry in taken[:MAX_SESSIONS]:
            with contextlib.suppress(OSError):
                if (
                    entry.is_dir(follow_symlinks=False)
                    and boardlog.session_dir(base, entry.name) is not None
                ):
                    ids.append(entry.name)
    except FileNotFoundError:
        return Listing()
    except (OSError, ValueError, RuntimeError) as exc:
        return Listing(problem=f"{type(exc).__name__}: {exc}")
    return Listing(
        sessions=tuple((session_id, read(session_id, base)) for session_id in sorted(ids)),
        problem=(
            f"more than the {MAX_SESSIONS} entries a listing takes; the rest are not shown"
            if len(taken) > MAX_SESSIONS
            else None
        ),
    )


# -- writing ----------------------------------------------------------------------


def _checked(changes: Mapping[str, object]) -> dict[str, Any] | Unwritten:
    """
    ``changes`` as the JSON values they are written as, or the first one that is
    not something a session file holds.

    Refused here and not at the next read, because a value ``decode`` would not
    take is a file this module wrote and then would not write again.
    """
    out: dict[str, Any] = {}
    for key, given in changes.items():
        if key in ("session_id", "format"):
            return Unwritten(f"{key!r} is not changed: the directory and the build decide it")
        if key not in _FIELDS:
            return Unwritten(f"{_shown(key)} is not a key of {FILE_NAME} this build writes")
        decoded = _FIELDS[key][0](given.value if isinstance(given, Policy) else given)
        if decoded is _BAD:
            return Unwritten(f"{key!r} cannot be {_shown(given)}")
        # As it reads back: a whole number given for a time is written as a float.
        out[key] = decoded.value if isinstance(decoded, Policy) else decoded
    return out


def update(
    session_id: str, changes: Mapping[str, object], root: Path | None = None
) -> Written | Unwritten:
    """
    Set the given keys of one session's file, keeping every other key the file
    has, known to this build or not. Never raises.

    The file is read again here and the object on disk is what the keys are set
    on, so a key a later build wrote -- at the top or nested in a value -- is
    written back as it was. ``format`` and ``session_id`` are written when the
    file lacks them and are not otherwise touched. The session's directory is
    made if it is not there.

    **Refused, with nothing written and the bytes on disk as they were**, when:

    - something is under the name that ``read`` calls ``Unreadable``, or the
      file's ``format`` is newer than ``FORMAT``;
    - the id is not one directory name;
    - a key is not one this build writes, or its value is not of the key's type
      (``policy`` takes a ``Policy`` or its value; a rung this build does not
      know cannot be written);
    - the result would be over ``MAX_FILE_BYTES``;
    - the write itself fails. The temp file is removed and the old file is whole.

    **Two keys are given and may be left as they were**, and ``Written.skipped``
    names them when they are:

    - ``task`` is the first launch's text and the card's title. Once the file
      has one that is not empty, it is never overwritten.
    - ``last_message`` longer than ``MAX_LAST_MESSAGE`` characters is not
      stored, and neither is a part of it: the file keeps the one it had.

    Flushed to the device before it is named, so the file survives the machine as
    well as the process. The directory entry is not flushed: losing it leaves the
    previous file, whole.
    """
    try:
        return _update(session_id, changes, root)
    except (OSError, ValueError, RuntimeError) as exc:
        return Unwritten(f"{type(exc).__name__}: {exc}")


def _update(
    session_id: str, changes: Mapping[str, object], root: Path | None
) -> Written | Unwritten:
    directory = session_dir(session_id, root)
    if directory is None:
        return Unwritten(_not_an_id(session_id))
    checked = _checked(changes)
    if isinstance(checked, Unwritten):
        return checked
    path = directory / FILE_NAME

    for _ in range(_ATTEMPTS):
        found = _load(path)
        if isinstance(found, Unreadable):
            return Unwritten(f"{FILE_NAME} is left as it is: {found.reason}")
        raw: dict[str, Any] = {} if isinstance(found, Absent) else found
        before = decode(raw, session_id=session_id)
        if isinstance(before, Unreadable):
            return Unwritten(f"{FILE_NAME} is left as it is: {before.reason}")
        if not before.writable:
            return Unwritten(
                f"{FILE_NAME} is left as it is: a newer build wrote it"
                f" (format {before.format}, and this build writes {FORMAT})"
            )

        skipped: list[str] = []
        for key, value in checked.items():
            if key == "task" and before.task:
                skipped.append(key)
            elif key == "last_message" and value is not None and len(value) > MAX_LAST_MESSAGE:
                skipped.append(key)
            else:
                raw[key] = value
        raw.setdefault("format", FORMAT)
        raw.setdefault("session_id", session_id)
        after = decode(raw, session_id=session_id)
        if isinstance(after, Unreadable):
            return Unwritten(after.reason)

        # ASCII out, so a lone surrogate the file held as an escape goes back as
        # one instead of failing to encode.
        data = (json.dumps(raw, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("ascii")
        if len(data) > MAX_FILE_BYTES:
            return Unwritten(f"{len(data)} bytes, over the {MAX_FILE_BYTES} a session file may be")
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(directory), prefix=".session-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if isinstance(found, Absent):
                # Linked, not renamed: the name was free when it was read, and a
                # rename would replace whatever has been put there since without
                # reading it.
                try:
                    os.link(tmp, path)
                except FileExistsError:
                    continue
            else:
                os.replace(tmp, path)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
        return Written(path=path, record=after, skipped=tuple(skipped))
    return Unwritten(f"{FILE_NAME} kept being created by another writer; not written")
