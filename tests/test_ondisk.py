"""
``ondisk``: the one bounded read and the two atomic writes every on-disk record
goes through.

For ``read_regular``, each test is one kind of thing that can sit under a
record's name. The callers' own suites check what each of them makes of a
refusal; these check the refusal itself, and that the bounds hold in the read and
not only in the answer.

For ``create_once``, ``replace`` and ``sync_directory``, the tests patch ``os``
itself, as the callers' suites do, so each one also holds that the write reaches
``os.link``, ``os.fsync`` and ``os.replace`` through the module.
"""

from __future__ import annotations

import contextlib
import errno
import os
import stat
import threading
import tracemalloc
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from pptmstr import ondisk
from pptmstr.ondisk import Linked, create_once, read_regular, replace, sync_directory

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


# -- writing ------------------------------------------------------------------------


def _names(directory: Path) -> list[str]:
    return sorted(os.listdir(directory))


def _traced(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """
    Every ``os.fsync``, ``os.link`` and ``os.replace``, in order: a flush as
    ``("fsync", ino, is_dir)``, a placement as ``(name, source name, target)``.
    """
    steps: list[tuple[Any, ...]] = []
    real_fsync, real_link, real_replace = os.fsync, os.link, os.replace

    def fsync(fd: int) -> None:
        status = os.fstat(fd)
        steps.append(("fsync", status.st_ino, stat.S_ISDIR(status.st_mode)))
        real_fsync(fd)

    def link(src: Any, dst: Any) -> None:
        steps.append(("link", Path(src).name, Path(dst)))
        real_link(src, dst)

    def rename(src: Any, dst: Any) -> None:
        steps.append(("replace", Path(src).name, Path(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "link", link)
    monkeypatch.setattr(os, "replace", rename)
    return steps


def _no_space(*args: object) -> None:
    raise OSError(errno.ENOSPC, "No space left on device")


def test_create_once_puts_the_bytes_under_the_first_name_and_leaves_nothing_else(
    tmp_path: Path,
) -> None:
    assert create_once(tmp_path, ("a.json",), b"{}\n") == Linked(tmp_path / "a.json", 0)
    assert (tmp_path / "a.json").read_bytes() == b"{}\n"
    assert _names(tmp_path) == ["a.json"]


@pytest.mark.parametrize("squatter", ["file", "dangling", "fifo", "dir"])
def test_create_once_passes_a_name_taken_by_anything_and_leaves_it_as_it_was(
    tmp_path: Path, squatter: str
) -> None:
    taken = tmp_path / "0.json"
    if squatter == "file":
        taken.write_bytes(b"theirs")
    elif squatter == "dangling":
        taken.symlink_to(tmp_path / "nowhere")
    elif squatter == "fifo":
        os.mkfifo(taken)
    else:
        taken.mkdir()

    linked = create_once(tmp_path, ("0.json", "1.json"), b"mine")

    assert linked == Linked(tmp_path / "1.json", 1)
    assert linked.path.read_bytes() == b"mine"
    assert os.path.lexists(taken)
    if squatter == "file":
        assert taken.read_bytes() == b"theirs"
    assert _names(tmp_path) == ["0.json", "1.json"]


def test_create_once_takes_from_the_names_only_as_far_as_the_first_free_one(
    tmp_path: Path,
) -> None:
    (tmp_path / "0").write_bytes(b"")
    (tmp_path / "1").write_bytes(b"")
    offered: list[int] = []

    def counter() -> Iterator[str]:
        n = 0
        while True:
            offered.append(n)
            yield str(n)
            n += 1

    assert create_once(tmp_path, counter(), b"x").passed == 2
    assert offered == [0, 1, 2]


@pytest.mark.parametrize("names", [(), ("only",)])
def test_create_once_raises_file_exists_when_every_name_is_taken(
    tmp_path: Path, names: tuple[str, ...]
) -> None:
    (tmp_path / "only").write_bytes(b"theirs")

    with pytest.raises(FileExistsError):
        create_once(tmp_path, names, b"mine")

    assert (tmp_path / "only").read_bytes() == b"theirs"
    assert _names(tmp_path) == ["only"]


def test_create_once_flushes_the_staged_file_before_it_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    steps = _traced(monkeypatch)

    linked = create_once(tmp_path, ("a",), b"x")

    (_, ino, is_dir), (call, source, target) = steps
    assert (ino, is_dir) == (linked.path.stat().st_ino, False)
    assert (call, target) == ("link", tmp_path / "a")
    assert source.startswith(ondisk.TEMP_PREFIX)


def test_create_once_that_cannot_link_raises_and_leaves_no_staged_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "link", _no_space)

    with pytest.raises(OSError) as raised:
        create_once(tmp_path, ("a", "b"), b"x")

    assert raised.value.errno == errno.ENOSPC
    assert _names(tmp_path) == []


def test_create_once_that_cannot_flush_names_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "fsync", _no_space)

    with pytest.raises(OSError):
        create_once(tmp_path, ("a",), b"x")

    assert _names(tmp_path) == []


def test_replace_puts_the_bytes_over_the_old_file_and_leaves_nothing_else(
    tmp_path: Path,
) -> None:
    target = tmp_path / "settings.json"
    target.write_bytes(b"old")

    replace(target, b"new", durable=False)

    assert target.read_bytes() == b"new"
    assert _names(tmp_path) == ["settings.json"]


def test_replace_makes_an_absent_file_owner_only(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"

    replace(target, b"new", durable=False)

    assert target.read_bytes() == b"new"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o600, 0o444])
def test_replace_keeps_the_permission_bits_of_the_file_it_replaces(
    tmp_path: Path, mode: int
) -> None:
    target = tmp_path / "settings.json"
    target.write_bytes(b"old")
    target.chmod(mode)

    replace(target, b"new", durable=False)

    assert stat.S_IMODE(target.stat().st_mode) == mode
    assert target.read_bytes() == b"new"


def test_replace_over_a_link_replaces_the_link_and_not_what_it_points_at(
    tmp_path: Path,
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_bytes(b"theirs")
    elsewhere.chmod(0o644)
    target = tmp_path / "settings.json"
    target.symlink_to(elsewhere)

    replace(target, b"new", durable=False)

    assert not target.is_symlink() and target.read_bytes() == b"new"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert elsewhere.read_bytes() == b"theirs"


def test_replace_over_a_fifo_puts_an_owner_only_file_there(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    os.mkfifo(target, 0o644)

    replace(target, b"new", durable=False)

    assert target.is_file() and target.read_bytes() == b"new"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_replace_over_a_directory_raises_and_leaves_no_staged_file(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    target.mkdir()

    with pytest.raises(IsADirectoryError):
        replace(target, b"new", durable=False)

    assert target.is_dir()
    assert _names(tmp_path) == ["settings.json"]


@pytest.mark.parametrize("durable", [True, False])
def test_replace_flushes_before_the_rename_only_when_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, durable: bool
) -> None:
    target = tmp_path / "settings.json"
    steps = _traced(monkeypatch)

    replace(target, b"new", durable=durable)

    placed = ("replace", steps[-1][1], target)
    assert placed[1].startswith(ondisk.TEMP_PREFIX)
    if durable:
        assert steps == [("fsync", target.stat().st_ino, False), placed]
    else:
        assert steps == [placed]


@pytest.mark.parametrize("failing", ["replace", "fsync", "fchmod"])
def test_replace_that_fails_leaves_the_old_file_whole_and_no_staged_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    target = tmp_path / "settings.json"
    target.write_bytes(b"old")
    monkeypatch.setattr(os, failing, _no_space)

    with pytest.raises(OSError):
        replace(target, b"new", durable=True)

    assert target.read_bytes() == b"old"
    assert _names(tmp_path) == ["settings.json"]


def test_replace_that_cannot_read_the_old_mode_raises_and_leaves_the_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "settings.json"
    target.write_bytes(b"old")
    target.chmod(0o644)

    def unreadable(*args: object, **kwargs: object) -> None:
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(os, "stat", unreadable)

    with pytest.raises(OSError) as raised:
        replace(target, b"new", durable=False)

    monkeypatch.undo()
    assert raised.value.errno == errno.EIO
    assert target.read_bytes() == b"old"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert _names(tmp_path) == ["settings.json"]


def test_sync_directory_flushes_the_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    steps = _traced(monkeypatch)

    sync_directory(tmp_path)

    assert steps == [("fsync", tmp_path.stat().st_ino, True)]


def test_sync_directory_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sync_directory(tmp_path / "absent")
    monkeypatch.setattr(os, "fsync", _no_space)
    sync_directory(tmp_path)
