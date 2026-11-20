"""
A journal on disk: the append that cannot overwrite, the strict read, and where a
session's journal lives.

Driven against a real directory, because the properties that matter are properties
of the filesystem primitive: that a name appears whole or not at all, and that a
second writer cannot take a name the first one has.

The records are the board's, read under ``boardlog.KINDS``: the board log is the
journal these properties were first stated for, and its kinds are the ones with
every field shape the codec knows. What the records mean, and the fold over them,
is ``tests/test_boardlog.py``.
"""

from __future__ import annotations

import dataclasses
import inspect
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from pptmstr import boardlog
from pptmstr.boardlog import Claimed, Declared, Record
from pptmstr.journal import codec, files
from pptmstr.journal.codec import Damage, Malformed
from pptmstr.journal.files import Unwritten, Written
from pptmstr.ondisk import TEMP_PREFIX

A = Claimed(at=1.0, task_id="t-1", by="builder")
B = Claimed(at=2.0, task_id="t-1", by="builder-2")


def names(directory: Path) -> list[str]:
    return sorted(os.listdir(directory))


def write(directory: Path, records: tuple[Record, ...]) -> None:
    for record in records:
        written = files.append(directory, record)
        assert isinstance(written, Written), written


# -- what cannot be read ----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # An integer no float can hold: `math.isfinite` raises on it.
        '{"kind": "task-claimed", "at": 1' + "0" * 400 + ', "task_id": "t", "by": "b"}\n',
        # Past the digits Python will parse at all.
        '{"kind": "task-claimed", "at": 1' + "0" * 5000 + ', "task_id": "t", "by": "b"}\n',
        # Nesting past the interpreter's recursion limit.
        "[" * 200_000 + "]" * 200_000 + "\n",
        '{"kind": "task-claimed", "at": 1.0, "task_id": "t", "by": ' + "[" * 200_000 + "\n",
    ],
    ids=["int-over-float", "int-over-digits", "deep-nesting", "deep-nesting-in-a-field"],
)
def test_text_built_to_break_the_parser_is_refused_and_ends_a_log_as_damage(
    text: str, tmp_path: Path
) -> None:
    """
    ``decode`` returns and ``read`` reports damage: neither may raise, because the
    caller is on the frame path and the file may be anybody's. Each of these gets
    past a ``ValueError`` handler as a different exception.
    """
    refused = codec.decode(text, boardlog.KINDS)
    assert isinstance(refused, Malformed) and refused.reason

    write(tmp_path, (A,))
    (tmp_path / "00000001.json").write_text(text)
    write(tmp_path, (B,))

    log = files.read(tmp_path, boardlog.KINDS)
    assert log.records == (A,)
    assert log.damage is not None and (log.damage.ordinal, log.damage.unread) == (1, 1)


@pytest.mark.parametrize("fed", [False, True])
def test_a_record_name_that_is_a_pipe_is_damage_and_does_not_hold_the_reader(
    fed: bool, tmp_path: Path
) -> None:
    """
    A FIFO under a record's name holds an ordinary open until somebody writes to
    it. Read on a thread so that a reader which does wait fails this test rather
    than hanging the suite; fed with a whole record, it is still not a file.
    """
    write(tmp_path, (A,))
    os.mkfifo(tmp_path / "00000001.json")
    # Unfed, the pipe has no writer at all, which is when an ordinary open waits.
    feed = -1
    if fed:
        feed = os.open(tmp_path / "00000001.json", os.O_RDWR | os.O_NONBLOCK)
        os.write(feed, codec.encode(B).encode())
    got: list[codec.Log[boardlog.Record]] = []
    reader = threading.Thread(
        target=lambda: got.append(files.read(tmp_path, boardlog.KINDS)), daemon=True
    )
    try:
        reader.start()
        reader.join(5.0)
        assert not reader.is_alive(), "read() is waiting on a pipe"
    finally:
        if feed != -1:
            os.close(feed)

    assert got[0].records == (A,)
    assert got[0].damage is not None and got[0].damage.ordinal == 1


def test_a_file_longer_than_a_record_may_be_is_damage_and_is_not_read_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The hazard: a file of any size under a record's name, read whole, is an
    allocation of that size and a ``MemoryError`` out of a function that never
    raises. The file here is a well-formed record one byte over the bound, so a
    reader that took all of it would decode it and report nothing.
    """
    fits = Claimed(at=1.0, task_id="t-1", by="b" * 40)
    monkeypatch.setattr(files, "MAX_RECORD_BYTES", len(codec.encode(fits)))
    write(tmp_path, (fits,))
    over = codec.encode(dataclasses.replace(fits, by="b" * 41))
    assert codec.decode(over, boardlog.KINDS) == dataclasses.replace(fits, by="b" * 41)
    (tmp_path / "00000001.json").write_text(over)
    (tmp_path / "00000002.json").write_text(codec.encode(fits))

    log = files.read(tmp_path, boardlog.KINDS)

    assert log.records == (fits,)
    assert log.damage is not None
    assert (log.damage.ordinal, log.damage.unread) == (1, 1)
    assert "over the" in log.damage.reason


def test_a_record_name_that_is_a_link_is_damage_and_is_not_read_through(
    tmp_path: Path,
) -> None:
    """
    ``append`` only ever hard-links a regular file into place, so a symbolic
    link under a record's name is something else's doing. The link leads to a
    whole record that would decode, so a reader that followed it would report
    nothing; it is refused as a name that cannot be read, as a pipe is, with what
    follows it withheld.
    """
    elsewhere = tmp_path / "elsewhere"
    write(elsewhere, (B,))
    log_dir = tmp_path / "log"
    write(log_dir, (A,))
    os.symlink(elsewhere / "00000000.json", log_dir / "00000001.json")
    (log_dir / "00000002.json").write_text(codec.encode(A))

    log = files.read(log_dir, boardlog.KINDS)

    assert log.records == (A,)
    assert log.damage is not None
    assert (log.damage.ordinal, log.damage.unread) == (1, 1)
    assert "OSError" in log.damage.reason


def test_a_first_record_that_is_a_link_yields_nothing(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    write(elsewhere, (A,))
    log_dir = tmp_path / "log"
    log_dir.mkdir()
    os.symlink(elsewhere / "00000000.json", log_dir / "00000000.json")

    log = files.read(log_dir, boardlog.KINDS)

    assert log.records == ()
    assert log.damage is not None and log.damage.ordinal == 0


def test_a_channel_record_under_a_link_is_not_read_through(tmp_path: Path) -> None:
    """
    A link's log is read by ``files.read``, so the refusal is the one a board
    gets. The planted record is a channel fact in the form ``channels.log`` writes.
    """
    from pptmstr.channels import facts as channel_facts

    created = channel_facts.encode(
        channel_facts.LinkCreated(at=1.0, channel_id="c-1", members=("a", "b"), charter="x")
    )
    elsewhere = tmp_path / "elsewhere"
    write(elsewhere, (created,))
    log_dir = tmp_path / "link"
    log_dir.mkdir()
    os.symlink(elsewhere / "00000000.json", log_dir / "00000000.json")

    log = files.read(log_dir, boardlog.KINDS)

    assert log.records == ()
    assert log.damage is not None and log.damage.ordinal == 0


def test_a_log_reached_through_a_linked_directory_reads_whole(tmp_path: Path) -> None:
    """
    The one link pptmstr makes is a directory: ``boardsync`` leaves the legacy
    session directory as a link to where it moved. ``O_NOFOLLOW`` refuses a link
    only as the last part of a path, so a log read through that link is the log.
    """
    moved = tmp_path / "boards" / "s-1"
    write(moved, (A, B))
    legacy = tmp_path / "legacy" / "s-1"
    legacy.parent.mkdir()
    os.symlink(os.path.relpath(moved, legacy.parent), legacy, target_is_directory=True)

    assert (
        files.read(legacy, boardlog.KINDS)
        == files.read(moved, boardlog.KINDS)
        == codec.Log(records=(A, B))
    )


def test_the_bound_on_a_record_is_the_one_the_request_files_use() -> None:
    """
    Two modules, one figure, for one reason. Spelled twice so neither imports the
    other, and pinned here so they do not drift.
    """
    from pptmstr import requests

    assert files.MAX_RECORD_BYTES == requests.MAX_RECORD_BYTES == 1 << 20


# -- the append -------------------------------------------------------------------


def test_a_listing_that_went_stale_before_the_write_does_not_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The two-writer hazard, constructed rather than raced: another writer lands
    between this append's listing and its link, so the listing it acts on shows
    an empty directory that is not one.
    """
    assert files.append(tmp_path, A) == Written(0, tmp_path / "00000000.json")
    stale: list[str] = []
    monkeypatch.setattr(os, "listdir", lambda directory: stale)

    second = files.append(tmp_path, B)
    monkeypatch.undo()

    assert second == Written(1, tmp_path / "00000001.json")
    assert files.read(tmp_path, boardlog.KINDS).records == (A, B)


def test_threads_appending_at_once_lose_nothing_and_leave_no_gap(tmp_path: Path) -> None:
    """
    Eight writers, each acting on a listing the others have already outrun. Each
    writer's records must all be there, in the order it wrote them, and the
    ordinals must be contiguous.
    """
    writers, each = 8, 40
    start = threading.Barrier(writers)
    failures: list[object] = []

    def run(who: int) -> None:
        start.wait()
        for n in range(each):
            written = files.append(tmp_path, Claimed(at=float(n), task_id="t", by=f"w{who}"))
            if not isinstance(written, Written):
                failures.append(written)
                return

    threads = [threading.Thread(target=run, args=(who,)) for who in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    log = files.read(tmp_path, boardlog.KINDS)
    assert failures == []
    assert log.damage is None
    assert len(log.records) == writers * each
    for who in range(writers):
        mine = [r.at for r in log.records if isinstance(r, Claimed) and r.by == f"w{who}"]
        assert mine == [float(n) for n in range(each)]
    assert names(tmp_path) == [f"{n:08d}.json" for n in range(writers * each)]


_WRITER = """
import sys
from pathlib import Path
from pptmstr import boardlog
from pptmstr.journal import files
who, directory, each = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3])
for n in range(each):
    record = boardlog.Claimed(at=float(n), task_id="t", by=who)
    w = files.append(directory, record)
    assert isinstance(w, files.Written), w
"""


def test_processes_appending_at_once_lose_nothing(tmp_path: Path) -> None:
    """
    Two windows resuming one session id are two processes, not two threads, and
    each of these lists the directory per append the way a writer with no hint
    does.
    """
    writers, each = 3, 60
    root = str(Path(files.__file__).resolve().parents[2])
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _WRITER, f"w{who}", str(tmp_path), str(each)],
            cwd=root,
            env={**os.environ, "PYTHONPATH": root},
        )
        for who in range(writers)
    ]
    assert [proc.wait(timeout=120) for proc in procs] == [0] * writers

    log = files.read(tmp_path, boardlog.KINDS)
    assert log.damage is None
    for who in range(writers):
        mine = [r.at for r in log.records if isinstance(r, Claimed) and r.by == f"w{who}"]
        assert mine == [float(n) for n in range(each)]


def test_an_append_leaves_nothing_behind_but_the_record(tmp_path: Path) -> None:
    files.append(tmp_path, A)
    files.append(tmp_path, B)

    assert names(tmp_path) == ["00000000.json", "00000001.json"]


def test_an_append_cannot_be_told_where_to_start(tmp_path: Path) -> None:
    """
    The hazard: a writer handed a floor below a missing ordinal takes the first
    free name above it, which is the missing record's. A break ``read`` reported
    then reads as a whole log with a newer record ahead of older ones. There is
    no floor to hand over, and a caller that tries is refused loudly.
    """
    assert list(inspect.signature(files.append).parameters) == [
        "directory",
        "record",
    ]
    write(tmp_path, (A, B, A))
    (tmp_path / "00000001.json").unlink()

    with pytest.raises(TypeError):
        files.append(tmp_path, B, after=0)  # type: ignore[call-arg]

    assert names(tmp_path) == ["00000000.json", "00000002.json"]


@pytest.mark.parametrize(
    ("lost", "gap", "readable"),
    [((1,), 1, 1), ((0,), 0, 0), ((1, 2), 1, 1), ((0, 2), 0, 0), ((2, 3), 2, 2)],
    ids=["one", "the-first", "two-together", "two-apart", "the-two-before-the-last"],
)
def test_no_append_lands_in_the_place_of_a_missing_record(
    lost: tuple[int, ...], gap: int, readable: int, tmp_path: Path
) -> None:
    """
    Restoring never invents a fact, and a record read back ahead of ones written
    before it is one. Whatever is missing and however wide, the next records go
    past the highest name there, the writer is told each time, and the log reads
    as the same prefix it read as before them.
    """
    records = (A, B, A, B, A)
    write(tmp_path, records)
    for ordinal in lost:
        (tmp_path / f"{ordinal:08d}.json").unlink()
    before = files.read(tmp_path, boardlog.KINDS)
    assert before.records == records[:readable] and before.damage is not None

    first = files.append(tmp_path, B)
    second = files.append(tmp_path, A)

    assert isinstance(first, Written) and (first.ordinal, first.gap) == (5, gap)
    assert isinstance(second, Written) and (second.ordinal, second.gap) == (6, gap)
    assert all(not (tmp_path / f"{ordinal:08d}.json").exists() for ordinal in lost)
    after = files.read(tmp_path, boardlog.KINDS)
    assert after.records == before.records
    assert after.damage == dataclasses.replace(before.damage, unread=before.damage.unread + 2)


def test_a_record_too_long_to_be_read_back_is_not_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``read`` takes a file over the bound for damage and withholds everything
    after it, so writing one would be the writer breaking its own log. A record
    of exactly the bound is written and read.
    """
    at_the_bound = Claimed(at=1.0, task_id="t-1", by="b" * 40)
    monkeypatch.setattr(files, "MAX_RECORD_BYTES", len(codec.encode(at_the_bound)))
    over = dataclasses.replace(at_the_bound, by="b" * 41)

    assert isinstance(files.append(tmp_path, at_the_bound), Written)
    refused = files.append(tmp_path, over)

    assert isinstance(refused, Unwritten) and "over the" in refused.reason
    assert names(tmp_path) == ["00000000.json"]
    assert files.read(tmp_path, boardlog.KINDS) == codec.Log(records=(at_the_bound,))


def test_a_record_is_flushed_to_the_device_before_it_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Before, not merely at some point: a name over text the device does not hold is
    the torn record a durable write exists to rule out.
    """
    whole = len(codec.encode(A).encode())
    synced: list[tuple[bool, int]] = []

    def fsync(fd: int) -> None:
        # What the kernel holds for the file at this moment. Bytes still in the
        # process's own buffer are not in it, and syncing ahead of them is a sync
        # of nothing.
        synced.append((names(tmp_path) != ["00000000.json"], os.fstat(fd).st_size))

    monkeypatch.setattr(os, "fsync", fsync)

    assert isinstance(files.append(tmp_path, A), Written)

    assert synced == [(True, whole)]
    assert files.read(tmp_path, boardlog.KINDS).records == (A,)


def test_an_append_into_a_whole_log_reports_no_gap(tmp_path: Path) -> None:
    for ordinal, record in enumerate((A, B, A)):
        written = files.append(tmp_path, record)
        assert isinstance(written, Written) and (written.ordinal, written.gap) == (ordinal, None)


def test_an_append_behind_a_missing_record_says_it_will_not_be_read_back(tmp_path: Path) -> None:
    """
    The hazard: a writer told ``Written`` forever while every restore stops at the
    gap. The listing an append already makes shows the gap, at no read.
    """
    write(tmp_path, (A, B, A))
    (tmp_path / "00000001.json").unlink()

    written = files.append(tmp_path, B)

    assert isinstance(written, Written)
    assert (written.ordinal, written.gap) == (3, 1)
    log = files.read(tmp_path, boardlog.KINDS)
    assert log.records == (A,) and log.damage == Damage(1, "missing", 2)


def test_a_name_the_reader_ignores_does_not_move_where_the_writer_appends(tmp_path: Path) -> None:
    """
    ``read`` takes only the canonical spelling of an ordinal. A writer that counted
    another spelling -- nine digits for 99 -- would put every later record past a
    gap that nothing but that stray name made.
    """
    write(tmp_path, (A,))
    (tmp_path / "000000099.json").write_text(codec.encode(B))

    assert files.next_ordinal(tmp_path) == 1
    written = files.append(tmp_path, B)

    assert isinstance(written, Written) and (written.ordinal, written.gap) == (1, None)
    assert files.read(tmp_path, boardlog.KINDS) == codec.Log(records=(A, B))


def test_the_directory_is_made_on_the_first_append(tmp_path: Path) -> None:
    directory = files.session_dir(tmp_path / "not" / "yet", "sess-1")

    assert directory is not None
    assert isinstance(files.append(directory, A), Written)
    assert files.read(directory, boardlog.KINDS).records == (A,)


def test_a_directory_that_cannot_be_made_is_a_value(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("in the way")

    refused = files.append(tmp_path / "file" / "log", A)

    assert isinstance(refused, Unwritten)
    assert "NotADirectoryError" in refused.reason


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes through a read-only mode")
def test_a_directory_that_cannot_be_written_is_a_value(tmp_path: Path) -> None:
    directory = tmp_path / "log"
    directory.mkdir()
    directory.chmod(0o500)
    try:
        refused = files.append(directory, A)
    finally:
        directory.chmod(0o700)

    assert isinstance(refused, Unwritten)
    assert "PermissionError" in refused.reason
    assert names(directory) == []


def test_a_filesystem_that_cannot_link_is_a_value_and_leaves_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_links(src: str, dst: Path) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "link", no_links)

    assert isinstance(files.append(tmp_path, A), Unwritten)
    assert names(tmp_path) == []


@pytest.mark.parametrize("at", [float("nan"), float("inf")])
def test_a_record_that_cannot_be_encoded_is_a_value_and_writes_nothing(
    at: float, tmp_path: Path
) -> None:
    refused = files.append(tmp_path, Claimed(at=at, task_id="t", by="b"))

    assert isinstance(refused, Unwritten)
    assert not tmp_path.joinpath("00000000.json").exists()


def test_the_next_ordinal_is_past_the_highest_name_and_past_the_padding(tmp_path: Path) -> None:
    assert files.next_ordinal(tmp_path / "absent") == 0
    assert files.next_ordinal(tmp_path) == 0

    (tmp_path / "00000009.json").write_text("")
    (tmp_path / "00000010.json").write_text("")
    (tmp_path / f"{TEMP_PREFIX}abc.tmp").write_text("")
    (tmp_path / "99999999999.txt").write_text("")
    assert files.next_ordinal(tmp_path) == 11

    (tmp_path / "99999999.json").write_text("")
    (tmp_path / "100000000.json").write_text("")
    assert files.next_ordinal(tmp_path) == 100_000_001


def test_a_directory_that_cannot_be_listed_is_not_one_with_no_record_in_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Three failures that are not absence, each with records the listing would have
    shown or could not rule out. Zero for any of them is "a log starts here".
    """
    (tmp_path / "a-file").write_text("where a directory would be")
    in_the_way = files.next_ordinal(tmp_path / "a-file")
    assert isinstance(in_the_way, files.Unlisted) and "NotADirectoryError" in in_the_way.reason

    write(tmp_path / "log", (Declared(at=1.0, task_id="t-1", title="T", declared_by="lead"),))
    real = os.listdir

    def refusing(error: OSError) -> None:
        def listdir(path: str | Path) -> list[str]:
            if Path(path) == tmp_path / "log":
                raise error
            return real(path)

        monkeypatch.setattr(os, "listdir", listdir)

    for error in (PermissionError(13, "Permission denied"), OSError(5, "Input/output error")):
        refusing(error)
        unlisted = files.next_ordinal(tmp_path / "log")
        assert isinstance(unlisted, files.Unlisted), error
        assert type(error).__name__ in unlisted.reason
        assert str(error.strerror) in unlisted.reason

    monkeypatch.setattr(os, "listdir", real)
    assert files.next_ordinal(tmp_path / "log") == 1


def test_a_session_is_keyed_by_its_id_and_nothing_else(tmp_path: Path) -> None:
    assert files.session_dir(tmp_path, "sess-1") == tmp_path / "sess-1"
    uuid = "0958795c-3ae6-423c-99bb-584c17486446"
    assert files.session_dir(tmp_path, uuid) == tmp_path / uuid


@pytest.mark.parametrize(
    "session_id",
    [
        "",
        ".",
        "..",
        "../x",
        "a/b",
        "/etc",
        "/",
        "a\\b",
        "a\0b",
        ".hidden",
        "\ud800",
        "x" * 256,
    ],
)
def test_an_id_that_is_not_one_directory_name_has_no_directory(
    session_id: str, tmp_path: Path
) -> None:
    """
    ``root / "/etc"`` is ``/etc`` and ``root / "../x"`` is beside the root, and
    ``append`` makes whatever directory it is handed. So the answer for such an id
    is no path at all, which a caller cannot append to by mistake.
    """
    assert files.session_dir(tmp_path, session_id) is None


# -- the read ---------------------------------------------------------------------


def test_a_missing_directory_is_an_empty_log(tmp_path: Path) -> None:
    assert files.read(tmp_path / "absent", boardlog.KINDS) == codec.Log()


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads through an unreadable mode")
def test_a_directory_that_cannot_be_listed_does_not_read_as_an_empty_board(tmp_path: Path) -> None:
    directory = tmp_path / "log"
    write(directory, (A,))
    directory.chmod(0o000)
    try:
        log = files.read(directory, boardlog.KINDS)
    finally:
        directory.chmod(0o700)

    assert log.records == ()
    assert log.damage is not None and "PermissionError" in log.damage.reason


def test_names_that_are_not_records_are_not_read(tmp_path: Path) -> None:
    """
    A temp file mid-write above all: tripping on one would fail the reader exactly
    while a write was in flight.
    """
    write(tmp_path, (A, B))
    half = codec.encode(A)[:10]
    (tmp_path / f"{TEMP_PREFIX}x7.tmp").write_text(half)
    (tmp_path / "notes.md").write_text("not ours")
    (tmp_path / "0000000002.json").write_text(codec.encode(A))  # not the canonical name
    (tmp_path / "2.json").write_text(codec.encode(A))
    (tmp_path / "00000002.json.bak").write_text(codec.encode(A))
    # Digits of another script: ``int`` reads them as 2 and no append writes them.
    (tmp_path / "\u0660\u0660\u0660\u0660\u0660\u0660\u0660\u0662.json").write_text(codec.encode(A))
    assert files.next_ordinal(tmp_path) == 2

    assert files.read(tmp_path, boardlog.KINDS) == codec.Log(records=(A, B))


def test_a_torn_last_record_is_reported_with_nothing_withheld(tmp_path: Path) -> None:
    write(tmp_path, (A, B))
    (tmp_path / "00000001.json").write_bytes(b"")

    assert files.read(tmp_path, boardlog.KINDS) == codec.Log(
        records=(A,), damage=Damage(1, "no terminator: the write did not finish", 0)
    )


def test_a_missing_record_ends_the_log_rather_than_being_skipped(tmp_path: Path) -> None:
    """
    Strict about order. The claim after a lost declaration is not applied to a
    board that never saw the task.
    """
    declared = Declared(at=1.0, task_id="t-1", title="T", declared_by="lead")
    write(tmp_path, (A, declared, B, A))
    (tmp_path / "00000001.json").unlink()

    assert files.read(tmp_path, boardlog.KINDS) == codec.Log(
        records=(A,), damage=Damage(1, "missing", 2)
    )


def test_a_log_missing_its_first_record_yields_nothing(tmp_path: Path) -> None:
    write(tmp_path, (A, B))
    (tmp_path / "00000000.json").unlink()

    assert files.read(tmp_path, boardlog.KINDS) == codec.Log(damage=Damage(0, "missing", 1))


def test_bytes_that_are_not_text_are_damage_not_an_exception(tmp_path: Path) -> None:
    write(tmp_path, (A, B))
    (tmp_path / "00000001.json").write_bytes(b"\xff\xfe\n")

    log = files.read(tmp_path, boardlog.KINDS)

    assert log.records == (A,)
    assert log.damage is not None and log.damage.ordinal == 1
