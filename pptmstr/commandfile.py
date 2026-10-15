"""
A break-glass command on disk: ``sessions/<session id>/commands/<command id>/``.

**The layout.** One directory per command, named by its id, holding at most
nine files, each of which is written once::

    request.json      what the agent asked, and when
    run.json          what the operator approved: the string, the shell, the bound
    started.json      the process that came of it: its pid, its group, when it began,
                      and the environment entry everything it starts carries
    end.json          how it ended, and the end of what it printed
    unconfirmed.json  pptmstr quit before it saw the end, and why it did not see it
    gone.json         a later pptmstr looked for the process and saw it was not there
    released.json     the operator let go of the session a process found running held
    output.log        what the process wrote, from the start, up to MAX_LOG_BYTES
    output.tail       the last MAX_TAIL_FILE_BYTES of it, only when the log was cut

The state of a command is which of ``request.json``, ``run.json`` and
``end.json`` exist. There is no status key to disagree with them, and no file is
a later version of another.

``started.json``, ``unconfirmed.json``, ``gone.json`` and ``released.json``
change no state. The
first exists because ``run.json`` is on disk before the process is, and so
cannot name it: it is what lets a process that outlived pptmstr be found from
its record. The second is what a quitting pptmstr leaves for a command it could
not wait for, and it is read only while there is no ``end.json`` -- an end
written after it is the better account. The third is what stops the looking:
a pid is handed out again once its process has gone, so a record that is
looked for at every start would sooner or later be matched against a process
that only has its number, and a process seen gone once stays gone. It is not an
end -- how the command ended is as unknown as it was. The fourth stops the
looking too, and is read into the outcome (``EndUnknown.released``): the
operator saw the process alive and chose to leave it running, so it holds the
session no longer and is not raised again, and what was found then is kept for
whoever asks later. None of the four is needed to read a command, so one
that cannot be read is read as absent and makes nothing unreadable.

**Every file is created once and nothing is overwritten or deleted.** A JSON file
is written whole to a temp name in the command's directory, flushed to the device,
and *linked* to its name; ``output.log`` is opened with ``O_EXCL``. Both fail in
the kernel on a name that is taken, so a second write of any file is
``Unwritten`` and the first one's bytes are as they were. No function here
removes anything but the temp file a write made for itself. These records say
what ran on the operator's machine, outside the sandbox, and a record of that
which could be replaced would be worth less than none.

**Nothing is read back open.** ``read_command`` returns a command that has ended
or a reason, whatever is in the directory: a request with no end reads as
``Lapsed`` and a run with no end as ``EndUnknown``. An asked command is a row
with a Run button and a running one is a row that says a process is alive, and
both are facts about this process and the window the operator is looking at. A
file is not evidence of either -- so no file, however it got there, becomes a
row that can be pressed. The synthesised end is not written back: it is what the
absence of ``end.json`` means, and writing it would turn "pptmstr did not see
this end" into a record that says somebody did.

**Versioning.** Each JSON file carries ``format``, which is ``FORMAT`` and
changes only when a file written by the new build would be misread by this one.
A key this build does not know is ignored. A file whose ``format`` is newer is
read from the keys this build knows, is named in ``Restored.newer``, and nothing
is added to its directory by this build. A required key that is missing or of
the wrong type makes that file unreadable, with the reason; the command it
belongs to is then reported and not shown, and every other command is unaffected.

**Two pptmstr windows.** Exclusive create is the whole of the arrangement: of two
writers of one file the second is refused and told, and neither tears the other.
What it does not order is two *different* files. One window recording a refusal
while another records a run can leave ``run.json`` beside an ``end.json`` that
says there was no run; each write checks for the other's file first, nothing
locks the interval between the check and the link, and a directory that loses
that race reads as ``Unreadable`` -- never as open, and never as a guess.

**Nothing here is in the snapshot, nothing here starts a thread, and every function
that touches the disk blocks.** Which thread calls it belongs to whoever wires this.
"""

from __future__ import annotations

import contextlib
import io
import itertools
import json
import math
import os
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, assert_never

from . import sessionfile
from .command import (
    MAX_CGROUP_CHARS,
    Cancelled,
    CancelledBy,
    Command,
    End,
    EndUnknown,
    Exited,
    Lapsed,
    NotStarted,
    Outcome,
    Refused,
    Released,
    Run,
    Started,
    TimedOut,
    Withdrawn,
    is_tag_digest,
    is_valid_id,
)

# The format this build writes, and the newest whose directory it adds to.
FORMAT = 1

COMMANDS_DIR = "commands"
REQUEST_NAME = "request.json"
RUN_NAME = "run.json"
END_NAME = "end.json"
STARTED_NAME = "started.json"
UNCONFIRMED_NAME = "unconfirmed.json"
GONE_NAME = "gone.json"
RELEASED_NAME = "released.json"
LOG_NAME = "output.log"
TAIL_NAME = "output.tail"

# The most one JSON file may hold. A write refuses a longer one and a read takes
# no more than this from a name, so what was written can always be read back.
# Text is written as ASCII escapes, at most twelve bytes a character, and the
# longest text any of the three holds is a few thousand characters.
MAX_JSON_BYTES = 1 << 18

# How much of a command's output is kept from its start, and how much of its end
# is kept beside that when the start did not hold all of it.
MAX_LOG_BYTES = 8 << 20
MAX_TAIL_FILE_BYTES = 64 << 10

# The most directory entries one ``restore`` takes from ``commands/``.
MAX_COMMANDS = 10_000

ALREADY_RECORDED = "already recorded"


@dataclass(frozen=True, slots=True)
class Written:
    """
    The file is on disk under ``path``, whole, and was not there before.
    """

    path: Path


@dataclass(frozen=True, slots=True)
class Unwritten:
    """
    Nothing was written, whatever was there is as it was, and why.

    A value rather than an exception for the reason ``boardlog.Unwritten`` is
    one. ``reason`` is ``ALREADY_RECORDED`` exactly when the name was taken.
    """

    reason: str


@dataclass(frozen=True, slots=True)
class Unreadable:
    """
    The directory does not hold a command this build can read, and why. It is
    reported and never shown as a command.
    """

    reason: str


@dataclass(frozen=True, slots=True)
class Restored:
    """
    What one session's ``commands/`` holds.

    ``commands`` are in the order they were asked, and every one of them has
    ended. ``unreadable`` pairs an id with the reason its directory gave no
    command. ``newer`` names the commands, readable or not, that a later build
    wrote a file of. ``problem`` is not None when this is not the whole
    directory: it could not be listed, or it holds more than ``MAX_COMMANDS``
    entries.
    """

    commands: tuple[Command, ...] = ()
    unreadable: tuple[tuple[str, str], ...] = ()
    newer: tuple[str, ...] = ()
    problem: str | None = None


# -- paths ------------------------------------------------------------------------


def commands_dir(session_id: str, root: Path | None = None) -> Path | None:
    """
    Where a session's commands are kept, or None for an id that has no directory.
    """
    directory = sessionfile.session_dir(session_id, root)
    return None if directory is None else directory / COMMANDS_DIR


def command_dir(session_id: str, command_id: str, root: Path | None = None) -> Path | None:
    """
    One command's directory, or None when either id is not one.

    ``command.is_valid_id`` is what stands between ``command_id`` and a path, and
    it is asked here so that no function of this module can join anything else.
    """
    base = commands_dir(session_id, root)
    if base is None or not is_valid_id(command_id):
        return None
    return base / command_id


def _shown(value: object) -> str:
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


def _no_directory(session_id: str, command_id: object) -> str:
    return f"{_shown(session_id)} / {_shown(command_id)} does not name a command's directory"


def _is_real_dir(path: Path) -> bool:
    # ``lstat``: a link to a directory is not a directory this module made.
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def _is_regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


# -- values at the boundary -------------------------------------------------------

_BAD: Any = object()


def _text(value: object) -> Any:
    return value if isinstance(value, str) else _BAD


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


def _pids(value: object) -> Any:
    if isinstance(value, list) and all(_whole(pid) is not _BAD for pid in value):
        return tuple(value)
    return _BAD


def _node(value: object) -> Any:
    if (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], str)
        and (value[1] is None or isinstance(value[1], str))
    ):
        return (value[0], value[1])
    return _BAD


def _need(obj: dict[str, Any], key: str, read: Callable[[object], Any]) -> Any:
    """
    A required key's value. ``ValueError`` when it is missing or not of its type.
    """
    if key not in obj:
        raise ValueError(f"no {key!r}")
    value = read(obj[key])
    if value is _BAD:
        raise ValueError(f"{key!r} is {_shown(obj[key])}")
    return value


def _optional(obj: dict[str, Any], key: str, read: Callable[[object], Any]) -> Any:
    """
    A key's value, or None when it is absent or null. ``ValueError`` when it is
    there and not of its type.
    """
    return None if obj.get(key) is None else _need(obj, key, read)


# -- outcomes ---------------------------------------------------------------------


def _outcome_obj(outcome: Outcome) -> dict[str, Any]:
    match outcome:
        case Exited(code=code):
            return {"kind": "exited", "code": code}
        case TimedOut():
            return {"kind": "timed_out"}
        case Cancelled(by=by):
            return {"kind": "cancelled", "by": by.value}
        case NotStarted(reason=reason):
            return {"kind": "not_started", "reason": reason}
        case Refused(reason=reason):
            return {"kind": "refused", "reason": reason}
        case Withdrawn():
            return {"kind": "withdrawn"}
        case Lapsed():
            return {"kind": "lapsed"}
        case EndUnknown():
            return {"kind": "end_unknown"}
        case _:
            assert_never(outcome)


def _by(value: object) -> Any:
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            return CancelledBy(value)
    return _BAD


def _outcome(value: object) -> Outcome:
    """
    The outcome an ``end.json`` states. ``ValueError`` for anything else: a kind
    this build does not know is not read as the one it most resembles.
    """
    if not isinstance(value, dict):
        raise ValueError(f"'outcome' is {_shown(value)}")
    kind = value.get("kind")
    if kind == "exited":
        return Exited(code=_need(value, "code", _whole))
    if kind == "timed_out":
        return TimedOut()
    if kind == "cancelled":
        return Cancelled(by=_need(value, "by", _by))
    if kind == "not_started":
        return NotStarted(reason=_need(value, "reason", _text))
    if kind == "refused":
        return Refused(reason=_need(value, "reason", _text))
    if kind == "withdrawn":
        return Withdrawn()
    if kind == "lapsed":
        return Lapsed()
    if kind == "end_unknown":
        return EndUnknown()
    raise ValueError(f"an outcome of kind {_shown(kind)}")


# -- reading ----------------------------------------------------------------------


def _file_bytes(path: Path, limit: int) -> bytes:
    """
    The bytes of a regular file no longer than ``limit``. ``OSError`` otherwise.

    Judged through the descriptor. ``O_NONBLOCK`` makes opening a FIFO return and
    ``O_NOFOLLOW`` refuses a link: every file here is linked or created as a
    regular file, so a link under the name is not something this module wrote.
    Read to one byte past the limit and no further.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{path.name} is not a regular file")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            held = handle.read(limit + 1)
        if len(held) > limit:
            raise OSError(f"{path.name} is over the {limit} bytes it may be")
        return held
    finally:
        if fd != -1:
            os.close(fd)


def _not_json(constant: str) -> Any:
    # ``json.loads`` takes NaN and Infinity, which are not JSON.
    raise ValueError(f"{constant} is not JSON")


@dataclass(frozen=True, slots=True)
class _Doc:
    """
    One JSON file's object, and whether a later build wrote it.
    """

    obj: dict[str, Any]
    newer: bool


def _doc(directory: Path, name: str) -> _Doc | None:
    """
    The object in one of a command's JSON files, None when no such name exists,
    and ``ValueError`` -- whose text begins with the file's name -- for anything
    that is there and is not a JSON object with a ``format`` this build can place.
    """
    try:
        held = _file_bytes(directory / name, MAX_JSON_BYTES)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(f"{name}: {type(exc).__name__}: {exc}") from None
    try:
        obj = json.loads(held.decode("utf-8"), parse_constant=_not_json)
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"{name}: not JSON ({type(exc).__name__}: {_shown(str(exc))})") from None
    if not isinstance(obj, dict):
        raise ValueError(f"{name}: not a JSON object")
    fmt = _whole(obj.get("format"))
    if fmt is _BAD or fmt < 1:
        raise ValueError(f"{name}: 'format' is {_shown(obj.get('format'))}")
    return _Doc(obj=obj, newer=fmt > FORMAT)


def _in_file(name: str, doc: _Doc, exc: ValueError) -> ValueError:
    later = f" (format {doc.obj['format']}, newer than this build's {FORMAT})" if doc.newer else ""
    return ValueError(f"{name}: {exc}{later}")


def _own_id(doc: _Doc, command_id: str) -> None:
    stated = _need(doc.obj, "id", _text)
    if stated != command_id:
        raise ValueError(f"'id' is {_shown(stated)}, and the directory is {command_id}")


def _run(doc: _Doc, command_id: str) -> Run:
    _own_id(doc, command_id)
    return Run(
        text=_need(doc.obj, "text", _text),
        shell=_need(doc.obj, "shell", _text),
        cwd=_need(doc.obj, "cwd", _text),
        timeout_s=_need(doc.obj, "timeout_s", _number),
        started_at=_need(doc.obj, "started_at", _number),
    )


def _end(doc: _Doc, command_id: str) -> tuple[End, float | None]:
    """
    The end an ``end.json`` states, and when a stop was asked, which the file
    holds because it is a fact about how the command came to end.
    """
    _own_id(doc, command_id)
    end = End(
        outcome=_outcome(doc.obj.get("outcome")),
        at=_need(doc.obj, "at", _number),
        duration_s=_need(doc.obj, "duration_s", _number),
        output_bytes=_need(doc.obj, "output_bytes", _whole),
        tail=_need(doc.obj, "tail", _text),
        # Absent from a record written before each was kept, and None then.
        leftover=_optional(doc.obj, "leftover", _whole),
        unconfirmed=_optional(doc.obj, "unconfirmed", _text),
        escaped=_optional(doc.obj, "escaped", _pids),
        unreadable=_optional(doc.obj, "unreadable", _pids) or (),
    )
    stop = doc.obj.get("stop_asked_at")
    return end, (None if stop is None else _need(doc.obj, "stop_asked_at", _number))


def _missing_end(
    asked_at: float, run: Run | None, unconfirmed: str | None, released: Released | None = None
) -> End:
    """
    What the absence of ``end.json`` means. The time is the last one the files
    state: nothing recorded when pptmstr stopped looking.
    """
    if run is None:
        return End(outcome=Lapsed(), at=asked_at)
    outcome = EndUnknown(released=released)
    return End(outcome=outcome, at=run.started_at, unconfirmed=unconfirmed)


def _released(directory: Path, command_id: str) -> Released | None:
    """
    What ``released.json`` says, or None when there is none that can be read.
    """
    try:
        doc = _doc(directory, RELEASED_NAME)
        if doc is None:
            return None
        _own_id(doc, command_id)
        return Released(at=_need(doc.obj, "at", _number), alive=_need(doc.obj, "alive", _whole))
    except (OSError, ValueError, RuntimeError):
        return None


def _left_unconfirmed(directory: Path, command_id: str) -> str | None:
    """
    What ``unconfirmed.json`` says, or None when there is none that can be read.
    """
    try:
        doc = _doc(directory, UNCONFIRMED_NAME)
        if doc is None:
            return None
        _own_id(doc, command_id)
        return _need(doc.obj, "reason", _text) or None
    except ValueError:
        return None


def _read(session_id: str, command_id: str, root: Path | None) -> tuple[Command | Unreadable, bool]:
    """
    ``read_command``'s answer, and whether any file there is a later build's.
    """
    newer = False
    try:
        directory = command_dir(session_id, command_id, root)
        if directory is None:
            return Unreadable(_no_directory(session_id, command_id)), False
        if not _is_real_dir(directory):
            return Unreadable("no such command directory"), False

        # All three are opened before any is interpreted, so ``newer`` is known
        # even when the first of them is what makes the directory unreadable.
        docs: dict[str, _Doc | None] = {}
        problems: list[str] = []
        for name in (REQUEST_NAME, RUN_NAME, END_NAME):
            try:
                docs[name] = _doc(directory, name)
            except ValueError as exc:
                docs[name] = None
                problems.append(str(exc))
        newer = any(doc is not None and doc.newer for doc in docs.values())
        if problems:
            return Unreadable("; ".join(problems)), newer

        request = docs[REQUEST_NAME]
        if request is None:
            return Unreadable(f"no {REQUEST_NAME}"), newer
        try:
            _own_id(request, command_id)
            asker = _need(request.obj, "asker", _node)
            if asker[0] != session_id:
                raise ValueError(f"'asker' is of session {_shown(asker[0])}, not this one")
            asked = _need(request.obj, "asked", _text)
            reason = _need(request.obj, "reason", _text)
            expects = _need(request.obj, "expects", _text)
            asked_at = _need(request.obj, "asked_at", _number)
        except ValueError as exc:
            raise _in_file(REQUEST_NAME, request, exc) from None

        run: Run | None = None
        run_doc = docs[RUN_NAME]
        if run_doc is not None:
            try:
                run = _run(run_doc, command_id)
            except ValueError as exc:
                raise _in_file(RUN_NAME, run_doc, exc) from None

        stop_asked_at: float | None = None
        end_doc = docs[END_NAME]
        if end_doc is None:
            end = _missing_end(
                asked_at,
                run,
                None if run is None else _left_unconfirmed(directory, command_id),
                None if run is None else _released(directory, command_id),
            )
        else:
            try:
                end, stop_asked_at = _end(end_doc, command_id)
            except ValueError as exc:
                raise _in_file(END_NAME, end_doc, exc) from None

        # ``end`` is never None here, which is the property: the record built
        # from files has ended. ``Command`` refuses the pairs of run and end
        # that describe nothing, and that refusal is a reason like any other.
        return (
            Command(
                id=command_id,
                asker=asker,
                asked=asked,
                reason=reason,
                expects=expects,
                asked_at=asked_at,
                run=run,
                stop_asked_at=stop_asked_at,
                end=end,
            ),
            newer,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        text = str(exc) if type(exc) is ValueError else f"{type(exc).__name__}: {exc}"
        return Unreadable(text), newer


def read_command(
    session_id: str, command_id: str, root: Path | None = None
) -> Command | Unreadable:
    """
    One command, rebuilt from the files that exist, or the reason there is none.

    **Never raises, never waits, and never returns an open command.** With no
    ``end.json`` the end is ``Lapsed`` when only the request exists and
    ``EndUnknown`` when a run does, timed at the last moment the files state.
    A file that is present and cannot be read -- not JSON, not regular, over
    ``MAX_JSON_BYTES``, a required key missing or mistyped, an ``id`` that is
    not the directory's, an asker of another session -- makes the command
    ``Unreadable``, with the file named, as does a set of files ``Command``
    refuses to mean anything (an ``end.json`` of a refusal beside a
    ``run.json``). ``output.log`` and ``output.tail`` are not opened.

    ``first_delivered_at`` is not on disk and reads as None. Whether the
    process of an ``EndUnknown`` is still there is not asked here
    (``EndUnknown.survivor`` is None): that is a fact about the machine and not
    about these files, and ``read_started`` hands over what it takes to ask.
    """
    return _read(session_id, command_id, root)[0]


def read_started(session_id: str, command_id: str, root: Path | None = None) -> Started | None:
    """
    The process a command's run became, as ``started.json`` states it, or None
    when there is no such file or it cannot be read. Never raises, never waits.

    **A ``tag`` that is not a digest reads as no tag** (``is_tag_digest``), and
    the rest of the record is read all the same. A build before this one wrote
    the entry itself there, ``PPTMSTR_COMMAND=<command id>``, which is a value
    anything that can list this directory knows: a process found by it is not
    known to be the command's, so nothing is looked for by it. Such a command
    is then found by its shell's session or not at all, as one with no tag is.
    A ``cgroup`` that is not text of a record's length reads as none.
    """
    try:
        directory = command_dir(session_id, command_id, root)
        if directory is None or not _is_real_dir(directory):
            return None
        doc = _doc(directory, STARTED_NAME)
        if doc is None:
            return None
        _own_id(doc, command_id)
        # Each absent from the record of a command started without it.
        tag = _optional(doc.obj, "tag", _text)
        cgroup = _optional(doc.obj, "cgroup", _text)
        return Started(
            pid=_need(doc.obj, "pid", _whole),
            pgid=_need(doc.obj, "pgid", _whole),
            at=_need(doc.obj, "at", _number),
            start_ticks=_optional(doc.obj, "start_ticks", _whole),
            boot_id=_optional(doc.obj, "boot_id", _text),
            tag=tag if is_tag_digest(tag) else None,
            cgroup=cgroup if cgroup and len(cgroup) <= MAX_CGROUP_CHARS else None,
        )
    except (OSError, ValueError, RuntimeError):
        return None


def read_gone(session_id: str, command_id: str, root: Path | None = None) -> str | None:
    """
    Why a command's process is known to be gone, as ``gone.json`` states it, or
    None when there is no such file or it cannot be read: the process is then
    still to be looked for. Never raises, never waits.
    """
    try:
        directory = command_dir(session_id, command_id, root)
        if directory is None or not _is_real_dir(directory):
            return None
        doc = _doc(directory, GONE_NAME)
        if doc is None:
            return None
        _own_id(doc, command_id)
        return _need(doc.obj, "why", _text) or None
    except (OSError, ValueError, RuntimeError):
        return None


def read_released(session_id: str, command_id: str, root: Path | None = None) -> Released | None:
    """
    The operator's release of the session a command found running held, as
    ``released.json`` states it, or None when there is no such file or it
    cannot be read. Never raises, never waits.
    """
    try:
        directory = command_dir(session_id, command_id, root)
        if directory is None or not _is_real_dir(directory):
            return None
        return _released(directory, command_id)
    except (OSError, ValueError, RuntimeError):
        return None


def restore(session_id: str, root: Path | None = None) -> Restored:
    """
    Every command under a session, in the order asked. Never raises.

    A session with no ``commands/`` is an empty answer. Entries that are not
    directories, links to directories, and names that are not command ids are
    skipped. No more than ``MAX_COMMANDS`` entries are taken, and ``problem``
    says when there were more. Reads up to three small files per command and
    blocks for all of them.
    """
    try:
        base = commands_dir(session_id, root)
        if base is None:
            return Restored(problem=f"{_shown(session_id)} is not a session id")
        ids: list[str] = []
        with os.scandir(base) as entries:
            taken = list(itertools.islice(entries, MAX_COMMANDS + 1))
        for entry in taken[:MAX_COMMANDS]:
            with contextlib.suppress(OSError):
                if is_valid_id(entry.name) and entry.is_dir(follow_symlinks=False):
                    ids.append(entry.name)
    except FileNotFoundError:
        return Restored()
    except (OSError, ValueError, RuntimeError) as exc:
        return Restored(problem=f"{type(exc).__name__}: {exc}")

    commands: list[Command] = []
    unreadable: list[tuple[str, str]] = []
    later: list[str] = []
    for command_id in sorted(ids):
        found, newer = _read(session_id, command_id, root)
        if newer:
            later.append(command_id)
        if isinstance(found, Unreadable):
            unreadable.append((command_id, found.reason))
        else:
            commands.append(found)
    commands.sort(key=lambda command: (command.asked_at, command.id))
    return Restored(
        commands=tuple(commands),
        unreadable=tuple(unreadable),
        newer=tuple(later),
        problem=(
            f"more than the {MAX_COMMANDS} entries a restore takes; the rest are not shown"
            if len(taken) > MAX_COMMANDS
            else None
        ),
    )


def tail(session_id: str, command_id: str, root: Path | None = None) -> str:
    """
    The end of what a command printed: ``output.tail`` when there is one, else
    the last ``MAX_TAIL_FILE_BYTES`` of ``output.log``. Empty when there is
    neither, or what is there is not a regular file. Never raises, never waits.

    Bytes that are not UTF-8 -- a character cut at the start among them -- are
    replaced. At most ``MAX_TAIL_FILE_BYTES`` are read whatever the files hold,
    and an ``output.tail`` that exists is the answer even when it cannot be
    read: the log beside it was cut, so its last bytes are not the end.
    """
    try:
        directory = command_dir(session_id, command_id, root)
        if directory is None or not _is_real_dir(directory):
            return ""
        held = b""
        for name in (TAIL_NAME, LOG_NAME):
            path = directory / name
            if not os.path.lexists(path):
                continue
            with contextlib.suppress(OSError):
                held = _last_bytes(path, MAX_TAIL_FILE_BYTES)
            break
        return held.decode("utf-8", errors="replace")
    except (OSError, ValueError, RuntimeError):
        return ""


def _last_bytes(path: Path, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        status = os.fstat(fd)
        if not stat.S_ISREG(status.st_mode):
            raise OSError(f"{path.name} is not a regular file")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            handle.seek(max(0, status.st_size - limit))
            return handle.read(limit)
    finally:
        if fd != -1:
            os.close(fd)


# -- writing ----------------------------------------------------------------------


def _encoded(obj: dict[str, Any]) -> bytes:
    # ASCII escapes, so the file is bytes any tool shows and a lone surrogate in
    # an agent's text has an encoding. ``allow_nan`` off: NaN is not JSON, and
    # ``_doc`` would refuse to read it back.
    data = (json.dumps(obj, ensure_ascii=True, allow_nan=False, indent=2) + "\n").encode("ascii")
    if len(data) > MAX_JSON_BYTES:
        raise ValueError(f"{len(data)} bytes, over the {MAX_JSON_BYTES} a record may be")
    return data


def _sync_dir(directory: Path) -> None:
    """
    Flush a directory's entries to the device, so a name just linked survives the
    machine and not only this process. Best effort: the name is already there
    and cannot be taken back, so a failure here is not a failure to write.
    """
    with contextlib.suppress(OSError):
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _create(directory: Path, name: str, data: bytes) -> Written | Unwritten:
    """
    Put ``data`` under ``name``, whole, if and only if the name is free.

    ``os.link`` fails on a name that exists, atomically, whatever is under it --
    a file, a link, a FIFO. The bytes are on the device before the name is.
    """
    target = directory / name
    fd, tmp = tempfile.mkstemp(dir=str(directory), prefix=f".{name}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp, target)
        except FileExistsError:
            return Unwritten(ALREADY_RECORDED)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
    _sync_dir(directory)
    return Written(target)


def _refusal_to_add(directory: Path, predecessor: str) -> str | None:
    """
    Why nothing may be added to this directory after ``predecessor``, or None.
    """
    if not _is_real_dir(directory) or not _is_regular(directory / predecessor):
        return f"no {predecessor}: nothing was recorded that this follows"
    for name in (REQUEST_NAME, RUN_NAME, END_NAME):
        try:
            doc = _doc(directory, name)
        except ValueError:
            continue
        if doc is not None and doc.newer:
            return f"{name} was written by a newer build, and this one adds nothing beside it"
    return None


_WRITE_ERRORS = (OSError, ValueError, TypeError, RuntimeError)


def _failed(exc: BaseException) -> Unwritten:
    return Unwritten(f"{type(exc).__name__}: {exc}")


def write_request(command: Command, root: Path | None = None) -> Written | Unwritten:
    """
    Record what was asked. Makes the command's directory, and is the only
    function that does. Never raises.

    Only the request's own fields are written, whatever else ``command`` holds.
    """
    try:
        directory = command_dir(command.session_id, command.id, root)
        if directory is None:
            return Unwritten(_no_directory(command.session_id, command.id))
        data = _encoded(
            {
                "format": FORMAT,
                "id": command.id,
                "asker": [command.asker[0], command.asker[1]],
                "asked": command.asked,
                "reason": command.reason,
                "expects": command.expects,
                "asked_at": command.asked_at,
            }
        )
        # 0700: the directory will hold what a command printed, as the operator.
        directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with contextlib.suppress(FileExistsError):
            directory.mkdir(mode=0o700)
        if not _is_real_dir(directory):
            return Unwritten(f"{directory.name} is there and is not a directory")
        result = _create(directory, REQUEST_NAME, data)
        _sync_dir(directory.parent)
        return result
    except _WRITE_ERRORS as exc:
        return _failed(exc)


def write_run(command: Command, root: Path | None = None) -> Written | Unwritten:
    """
    Record the operator's approval: ``command.run``. Never raises.

    Refused when there is no ``request.json`` (a run cannot be recorded for a
    command never requested) and when there is an ``end.json`` (it ended).
    """
    try:
        run = command.run
        if run is None:
            return Unwritten("the command has no run to record")
        directory = command_dir(command.session_id, command.id, root)
        if directory is None:
            return Unwritten(_no_directory(command.session_id, command.id))
        refusal = _refusal_to_add(directory, REQUEST_NAME)
        if refusal is not None:
            return Unwritten(refusal)
        if os.path.lexists(directory / END_NAME):
            return Unwritten(f"{END_NAME} is there: the command ended, and nothing runs after that")
        data = _encoded(
            {
                "format": FORMAT,
                "id": command.id,
                "text": run.text,
                "shell": run.shell,
                "cwd": run.cwd,
                "timeout_s": run.timeout_s,
                "started_at": run.started_at,
            }
        )
        return _create(directory, RUN_NAME, data)
    except _WRITE_ERRORS as exc:
        return _failed(exc)


def write_end(command: Command, root: Path | None = None) -> Written | Unwritten:
    """
    Record how it ended: ``command.end``, and ``stop_asked_at``. Never raises.

    An end of a command that has a run follows ``run.json`` and is refused
    without it. An end of one that has none -- refused, withdrawn, lapsed, never
    started -- follows ``request.json``, and is refused when a ``run.json`` is
    there, since the two together would say a run both was and was not approved.
    """
    try:
        end = command.end
        if end is None:
            return Unwritten("the command has no end to record")
        directory = command_dir(command.session_id, command.id, root)
        if directory is None:
            return Unwritten(_no_directory(command.session_id, command.id))
        refusal = _refusal_to_add(directory, REQUEST_NAME if command.run is None else RUN_NAME)
        if refusal is not None:
            return Unwritten(refusal)
        if command.run is None and os.path.lexists(directory / RUN_NAME):
            return Unwritten(f"{RUN_NAME} is there, and this end says nothing was approved")
        data = _encoded(
            {
                "format": FORMAT,
                "id": command.id,
                "outcome": _outcome_obj(end.outcome),
                "at": end.at,
                "duration_s": end.duration_s,
                "output_bytes": end.output_bytes,
                "tail": end.tail,
                "stop_asked_at": command.stop_asked_at,
                "leftover": end.leftover,
                "unconfirmed": end.unconfirmed,
                "escaped": None if end.escaped is None else list(end.escaped),
                "unreadable": list(end.unreadable),
            }
        )
        return _create(directory, END_NAME, data)
    except _WRITE_ERRORS as exc:
        return _failed(exc)


def write_started(
    session_id: str, command_id: str, started: Started, root: Path | None = None
) -> Written | Unwritten:
    """
    Record the process a run became. Never raises.

    Refused without a ``run.json``: a process is only ever a run's. Written
    after the process exists, since before that there is no pid to write, so a
    pptmstr that dies between the two leaves a run with no ``started.json`` --
    which reads as it always did, ``EndUnknown`` with nothing to look for.
    """
    try:
        directory = command_dir(session_id, command_id, root)
        if directory is None:
            return Unwritten(_no_directory(session_id, command_id))
        refusal = _refusal_to_add(directory, RUN_NAME)
        if refusal is not None:
            return Unwritten(refusal)
        data = _encoded(
            {
                "format": FORMAT,
                "id": command_id,
                "pid": started.pid,
                "pgid": started.pgid,
                "at": started.at,
                "start_ticks": started.start_ticks,
                "boot_id": started.boot_id,
                "tag": started.tag,
                "cgroup": started.cgroup,
            }
        )
        return _create(directory, STARTED_NAME, data)
    except _WRITE_ERRORS as exc:
        return _failed(exc)


def write_gone(
    session_id: str, command_id: str, why: str, at: float, root: Path | None = None
) -> Written | Unwritten:
    """
    Record that the process ``started.json`` names was looked for at ``at`` and
    seen to be gone, and what showed it. Never raises.

    Refused without a ``started.json``, since there was then nothing to look
    for, and with an empty ``why``, which would read back as no record.
    """
    try:
        directory = command_dir(session_id, command_id, root)
        if directory is None:
            return Unwritten(_no_directory(session_id, command_id))
        if not why:
            return Unwritten("no reason was given for saying it is gone")
        refusal = _refusal_to_add(directory, STARTED_NAME)
        if refusal is not None:
            return Unwritten(refusal)
        data = _encoded({"format": FORMAT, "id": command_id, "why": why, "at": at})
        return _create(directory, GONE_NAME, data)
    except _WRITE_ERRORS as exc:
        return _failed(exc)


def write_released(
    session_id: str,
    command_id: str,
    released: Released,
    look: str,
    root: Path | None = None,
) -> Written | Unwritten:
    """
    Record that the operator let go of the session this command's process,
    found running with no end on record, was holding; how many of its
    processes the last look found (``Released.alive``); and ``look``, the line
    that showed them, for whoever later asks what was left running. Never
    raises.

    Refused without a ``started.json``, since nothing could have been found
    running, and when there is an ``end.json``: the command's end is then
    recorded and nothing of it was left to release.
    """
    try:
        directory = command_dir(session_id, command_id, root)
        if directory is None:
            return Unwritten(_no_directory(session_id, command_id))
        refusal = _refusal_to_add(directory, STARTED_NAME)
        if refusal is not None:
            return Unwritten(refusal)
        if os.path.lexists(directory / END_NAME):
            return Unwritten(f"{END_NAME} is there: its end was recorded")
        data = _encoded(
            {
                "format": FORMAT,
                "id": command_id,
                "at": released.at,
                "alive": released.alive,
                "look": look,
            }
        )
        return _create(directory, RELEASED_NAME, data)
    except _WRITE_ERRORS as exc:
        return _failed(exc)


def write_unconfirmed(
    session_id: str,
    command_id: str,
    reason: str,
    at: float,
    pid: int | None = None,
    root: Path | None = None,
) -> Written | Unwritten:
    """
    Record that pptmstr quit without seeing this command end, and why. Never
    raises.

    Refused without a ``run.json``, and when there is an ``end.json``: the end
    is then recorded, and this would say it was not.
    """
    try:
        directory = command_dir(session_id, command_id, root)
        if directory is None:
            return Unwritten(_no_directory(session_id, command_id))
        refusal = _refusal_to_add(directory, RUN_NAME)
        if refusal is not None:
            return Unwritten(refusal)
        if os.path.lexists(directory / END_NAME):
            return Unwritten(f"{END_NAME} is there: its end was recorded")
        data = _encoded(
            {"format": FORMAT, "id": command_id, "reason": reason, "at": at, "pid": pid}
        )
        return _create(directory, UNCONFIRMED_NAME, data)
    except _WRITE_ERRORS as exc:
        return _failed(exc)


def open_log(session_id: str, command_id: str, root: Path | None = None) -> io.FileIO | Unwritten:
    """
    Create ``output.log`` and return it open for writing, unbuffered, so that
    what a running command has printed is in the file and not in this process.
    Never raises.

    Exclusive, as every file here: a log that exists is ``Unwritten``. Refused
    without a ``run.json``, since output is only ever a run's. Holding it to
    ``MAX_LOG_BYTES`` is the writer's to do -- this hands over a descriptor.
    """
    try:
        directory = command_dir(session_id, command_id, root)
        if directory is None:
            return Unwritten(_no_directory(session_id, command_id))
        refusal = _refusal_to_add(directory, RUN_NAME)
        if refusal is not None:
            return Unwritten(refusal)
        # ``O_EXCL`` with ``O_CREAT`` fails on any name that exists, a link
        # among them, whether or not what it points at does.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        try:
            fd = os.open(directory / LOG_NAME, flags, 0o600)
        except FileExistsError:
            return Unwritten(ALREADY_RECORDED)
        return io.FileIO(fd, "wb")
    except _WRITE_ERRORS as exc:
        return _failed(exc)


def write_tail(
    session_id: str, command_id: str, data: bytes, root: Path | None = None
) -> Written | Unwritten:
    """
    Record the last bytes of an output whose log was cut. Never raises.

    Refused without an ``output.log`` and when ``data`` is over
    ``MAX_TAIL_FILE_BYTES``: a longer one would be read back short, from its
    end, as something other than what was written.
    """
    try:
        directory = command_dir(session_id, command_id, root)
        if directory is None:
            return Unwritten(_no_directory(session_id, command_id))
        if len(data) > MAX_TAIL_FILE_BYTES:
            return Unwritten(f"{len(data)} bytes, over the {MAX_TAIL_FILE_BYTES} a tail may be")
        refusal = _refusal_to_add(directory, LOG_NAME)
        if refusal is not None:
            return Unwritten(refusal)
        return _create(directory, TAIL_NAME, bytes(data))
    except _WRITE_ERRORS as exc:
        return _failed(exc)
