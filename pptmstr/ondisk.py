"""
The disk primitives every record goes through: one bounded read, and the two
atomic writes that put a file under its name.

Every on-disk record pptmstr reads back -- a board or channel log entry, a command's
JSON files, a session file, a cross-team request -- is written by linking or renaming
a regular file into place, under a name another process can also write to. Reading
one therefore has the same three requirements wherever it happens: the name holds a
regular file and not a link, a FIFO or a directory; reading it cannot block; and
reading it costs no more memory than the file's own size, up to a limit, however
large the file claims or turns out to be. That is ``read_regular``.

Writing one is one of two things. ``create_once`` stages the bytes and links them to
the first free name of a sequence the caller gives, so a name is never written over;
``replace`` stages them and renames them over one name, so a reader sees the old file
or the new one and never a part. ``sync_directory`` is the step a caller adds when a
new name must survive the machine as well as the process.

Every write here stages in the target's directory under ``TEMP_PREFIX``, a dot-led
name that no record's name grammar matches. ``os.link``, ``os.fsync`` and
``os.replace`` are called through the ``os`` module, so a test that patches them on
``os`` reaches these writes.

What the bytes mean, what names are tried, and what a refusal is called stay with
each caller. Every write raises; each caller maps that to its own failure shape.
"""

from __future__ import annotations

import contextlib
import errno
import os
import stat
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

# What every write here names its staged file with, before ``tempfile`` adds its
# random part. Dot-led, so a listing filtered by any record's grammar skips it.
TEMP_PREFIX = ".writing-"
_TEMP_SUFFIX = ".tmp"

# What one ``os.read`` asks for. The call allocates what it asks for before it knows
# how much is there, and nearly every record is a few hundred bytes.
_CHUNK = 1 << 16


def read_regular(path: Path, limit: int) -> bytes:
    """
    The bytes of the regular file at ``path``, no more than ``limit`` of them.
    ``OSError`` for anything else: what ``open`` raises, a name that is not a
    regular file, and a file longer than ``limit``.

    The file is judged through the descriptor, never by a ``stat`` of the name
    beforehand: the name may belong to another party, which can swap what it
    points at between a check and an open.

    - ``O_NOFOLLOW`` refuses a symbolic link under the name, wherever it leads. It
      refuses only the last part of the path, so a file reached through a linked
      directory still reads.
    - ``O_NONBLOCK`` makes opening a FIFO return. A FIFO under the name would
      otherwise hold the open until somebody wrote to it, and a reader that waits
      on a file is one any writer to the directory can stop.
    - ``O_CLOEXEC`` keeps the descriptor out of any child started while it is open.

    A file whose size is over ``limit`` is refused unread. The read itself still
    stops one byte past ``limit``, because the size comes from the descriptor and
    a file that grows after it was taken must cost no more than one that was
    already too big. Reading in chunks rather than asking for ``limit + 1`` at once
    is what keeps a short file's read from allocating the whole limit.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        status = os.fstat(fd)
        if not stat.S_ISREG(status.st_mode):
            raise OSError("not a regular file")
        chunks: list[bytes] = []
        held = 0
        while held <= limit and status.st_size <= limit:
            chunk = os.read(fd, min(_CHUNK, limit + 1 - held))
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            held += len(chunk)
        raise OSError(f"over the {limit} bytes it may be")
    finally:
        os.close(fd)


@dataclass(frozen=True, slots=True)
class Linked:
    """
    Where ``create_once`` put the bytes, and how many names it offered were
    already taken before that one.
    """

    path: Path
    passed: int


def create_once(directory: Path, names: Iterable[str], data: bytes) -> Linked:
    """
    Put ``data`` under the first name in ``names`` that is free in ``directory``,
    whole, and never over anything. ``FileExistsError`` when every name offered is
    taken; ``OSError`` for any other failure. The staged file is gone afterwards
    whichever way it ends.

    The bytes are staged once, flushed to the device, and then *linked* to each
    name in turn. ``os.link`` fails on a name that exists, atomically, in the
    kernel, whatever is under it -- a file, a link, a FIFO -- which is the
    guarantee a check before a rename cannot give. A name therefore appears
    complete or not at all, and a writer that dies before the link spends no name.

    ``names`` is consumed lazily and only as far as the first free one, so it
    carries the caller's whole naming policy: one name refuses on collision, and a
    counter takes the next. The flush is what makes a linked name survive the
    machine as well as the process; without it a power loss can leave a name over
    a short file. The directory entry is not flushed here (``sync_directory``).

    A staged file a killed process leaves behind is never removed here, because
    another writer's write in flight has the same shape. Assumes a local
    filesystem with hard links: a network filesystem can report a link as failed
    after it landed, and the caller then writes the record twice.
    """
    fd, tmp = tempfile.mkstemp(dir=str(directory), prefix=TEMP_PREFIX, suffix=_TEMP_SUFFIX)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        tried = directory
        for passed, name in enumerate(names):
            tried = directory / name
            try:
                os.link(tmp, tried)
            except FileExistsError:
                continue
            return Linked(path=tried, passed=passed)
        raise FileExistsError(errno.EEXIST, "every name offered is taken", str(tried))
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


def replace(target: Path, data: bytes, *, durable: bool) -> None:
    """
    Put ``data`` under ``target``, over whatever regular file is there. ``OSError``
    on failure, with the previous file whole and no staged file left.

    Staged in the target's own directory, because rename(2) is atomic only within
    a filesystem, and renamed over the name, so an interrupted write leaves the
    previous file and never a truncated one. The staged file is removed only on
    failure: after the rename its name is free, and removing it then could remove
    whatever took the name next.

    ``durable`` flushes the bytes to the device before the rename; without it a
    process crash still leaves the old file whole, and a power loss leaves it whole
    only if the filesystem orders the rename after the data. It is keyword-only and
    has no default, so each caller states which it is.

    The result keeps the permission bits of the regular file it replaces, read with
    an ``lstat`` just before the bytes are staged; setuid, setgid and sticky are not
    carried onto the new bytes. Where the name is free, or holds a link, a FIFO or
    anything else that is not a regular file, the result is 0600, as ``tempfile``
    makes it: a link is replaced by the file, and what it pointed at is left alone.
    A directory under the name fails the rename. An ``lstat`` that fails for any
    reason but absence raises before anything is staged, so a file whose mode
    cannot be read is never silently made 0600.
    """
    mode = _permissions(target)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=TEMP_PREFIX, suffix=_TEMP_SUFFIX)
    try:
        with os.fdopen(fd, "wb") as handle:
            if mode is not None:
                os.fchmod(handle.fileno(), mode)
            handle.write(data)
            if durable:
                handle.flush()
                os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _permissions(target: Path) -> int | None:
    """
    The permission bits of the regular file at ``target``, or None where the name
    is free or holds something other than a regular file. Not following a link.
    Any other failure raises: writing anyway would put 0600 over a file whose
    bits were meant to survive.
    """
    try:
        status = os.stat(target, follow_symlinks=False)
    except FileNotFoundError:
        return None
    return stat.S_IMODE(status.st_mode) & 0o777 if stat.S_ISREG(status.st_mode) else None


def sync_directory(directory: Path) -> None:
    """
    Flush a directory's entries to the device, so a name just linked into it
    survives the machine and not only this process. Best effort, and never raises:
    the name is already there and cannot be taken back, so a failure here is not a
    failure to write.
    """
    with contextlib.suppress(OSError):
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
