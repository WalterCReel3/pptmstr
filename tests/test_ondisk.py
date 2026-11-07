"""
``ondisk.read_regular``: the one bounded read every on-disk record goes through.

Each test is one kind of thing that can sit under a record's name. The callers'
own suites check what each of them makes of a refusal; these check the refusal
itself, and that the bounds hold in the read and not only in the answer.
"""

from __future__ import annotations

import contextlib
import os
import threading
import tracemalloc
from pathlib import Path

import pytest

from pptmstr import ondisk
from pptmstr.ondisk import read_regular

LIMIT = 1 << 12


def test_a_regular_file_is_read_whole(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    path.write_bytes(b'{"kind": "x"}\n')

    assert read_regular(path, LIMIT) == b'{"kind": "x"}\n'


def test_an_empty_file_is_empty_bytes(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    path.write_bytes(b"")

    assert read_regular(path, LIMIT) == b""


def test_a_file_of_exactly_the_limit_is_read(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    path.write_bytes(b"x" * LIMIT)

    assert read_regular(path, LIMIT) == b"x" * LIMIT


def test_a_file_longer_than_the_chunk_is_read_whole(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    body = bytes(range(256)) * ((3 * ondisk._CHUNK) // 256 + 7)
    path.write_bytes(body)

    assert read_regular(path, len(body)) == body


def test_a_file_one_byte_over_the_limit_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    path.write_bytes(b"x" * (LIMIT + 1))

    with pytest.raises(OSError, match=f"over the {LIMIT} bytes"):
        read_regular(path, LIMIT)


def test_a_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    # Callers tell absence from damage by this class, so it must not be folded
    # into a plain OSError.
    with pytest.raises(FileNotFoundError):
        read_regular(tmp_path / "absent.json", LIMIT)


def test_a_missing_directory_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_regular(tmp_path / "absent" / "record.json", LIMIT)


def test_a_directory_is_refused(tmp_path: Path) -> None:
    (tmp_path / "record.json").mkdir()

    with pytest.raises(OSError, match="not a regular file"):
        read_regular(tmp_path / "record.json", LIMIT)


def test_a_symlink_to_a_regular_file_is_refused(tmp_path: Path) -> None:
    """
    Wherever it leads: a record is a regular file linked into place, so a symbolic
    link under its name is not one, even when what it points at would read.
    """
    target = tmp_path / "elsewhere.json"
    target.write_bytes(b"{}")
    link = tmp_path / "record.json"
    link.symlink_to(target)

    with pytest.raises(OSError) as raised:
        read_regular(link, LIMIT)
    assert not isinstance(raised.value, FileNotFoundError)


def test_a_dangling_symlink_is_refused_and_is_not_absence(tmp_path: Path) -> None:
    link = tmp_path / "record.json"
    link.symlink_to(tmp_path / "nowhere")

    with pytest.raises(OSError) as raised:
        read_regular(link, LIMIT)
    assert not isinstance(raised.value, FileNotFoundError)


def test_a_file_reached_through_a_linked_directory_still_reads(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "record.json").write_bytes(b"{}")
    (tmp_path / "linked").symlink_to(real, target_is_directory=True)

    assert read_regular(tmp_path / "linked" / "record.json", LIMIT) == b"{}"


def test_a_fifo_is_refused_without_waiting_for_a_writer(tmp_path: Path) -> None:
    fifo = tmp_path / "record.json"
    os.mkfifo(fifo)
    outcome: list[BaseException | bytes] = []

    def attempt() -> None:
        try:
            outcome.append(read_regular(fifo, LIMIT))
        except OSError as exc:
            outcome.append(exc)

    worker = threading.Thread(target=attempt, daemon=True)
    worker.start()
    worker.join(5.0)
    try:
        assert outcome, "the read waited on the FIFO"
    finally:
        # Lets go of a reader that did block.
        with contextlib.suppress(OSError):
            os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
    assert isinstance(outcome[0], OSError) and "not a regular file" in str(outcome[0])


def test_a_file_over_the_limit_by_its_size_is_refused_unread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "record.json"
    with path.open("wb") as handle:
        # Sparse: 64 MiB to a reader, nothing on the disk.
        handle.truncate(64 << 20)
    taken: list[int] = []
    read = os.read

    def counted(fd: int, length: int) -> bytes:
        chunk = read(fd, length)
        taken.append(len(chunk))
        return chunk

    monkeypatch.setattr(os, "read", counted)
    with pytest.raises(OSError, match="over the"):
        read_regular(path, LIMIT)
    assert taken == []


def test_a_file_longer_than_its_size_says_is_not_read_past_the_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The size comes off the descriptor, and the file can grow after it was taken,
    so the limit has to hold in the read as well as in the check.
    """
    path = tmp_path / "record.json"
    path.write_bytes(b" " * (4 * LIMIT))
    fstat = os.fstat
    read = os.read
    taken: list[int] = []

    def empty_by_its_size(fd: int) -> os.stat_result:
        status = fstat(fd)
        return os.stat_result((status.st_mode, *status[1:6], 0, *status[7:10]))

    def counted(fd: int, length: int) -> bytes:
        chunk = read(fd, length)
        taken.append(len(chunk))
        return chunk

    monkeypatch.setattr(os, "fstat", empty_by_its_size)
    monkeypatch.setattr(os, "read", counted)
    with pytest.raises(OSError, match="over the"):
        read_regular(path, LIMIT)
    assert sum(taken) == LIMIT + 1


def test_a_short_file_does_not_cost_the_whole_limit(tmp_path: Path) -> None:
    """
    A limit of a megabyte is a ceiling, not a price: reading a few hundred bytes
    must not allocate it.
    """
    path = tmp_path / "record.json"
    path.write_bytes(b"x" * 300)
    limit = 1 << 20
    read_regular(path, limit)

    tracemalloc.start()
    try:
        read_regular(path, limit)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < limit // 4


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc/self/fd")
def test_no_descriptor_is_left_open(tmp_path: Path) -> None:
    good = tmp_path / "good.json"
    good.write_bytes(b"{}")
    big = tmp_path / "big.json"
    big.write_bytes(b"x" * (LIMIT + 1))
    (tmp_path / "dir.json").mkdir()
    before = set(os.listdir("/proc/self/fd"))

    read_regular(good, LIMIT)
    for refused in (big, tmp_path / "dir.json"):
        with pytest.raises(OSError):
            read_regular(refused, LIMIT)

    assert set(os.listdir("/proc/self/fd")) == before
