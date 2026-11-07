"""
One bounded read of a file that must be a regular file under its own name.

Every on-disk record pptmstr reads back -- a board or channel log entry, a command's
JSON files, a session file, a cross-team request -- is written by linking or renaming
a regular file into place, under a name another process can also write to. Reading
one therefore has the same three requirements wherever it happens, and they are the
whole of this module: the name holds a regular file and not a link, a FIFO or a
directory; reading it cannot block; and reading it costs no more memory than the
file's own size, up to a limit, however large the file claims or turns out to be.

What the bytes mean, and what a refusal is called, stays with each caller.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

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
