"""
The board writer: order, the thread an append runs on, and what is said about a
record that did not land. Then where the logs are: the session's own directory,
the legacy one, and the move from the second to the first.

Against real directories under ``tmp_path``. Where a test has to see or hold the
append itself, ``boardlog.append`` is wrapped and still called, so what reaches disk
is what the real one wrote.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from pptmstr import boardlog, boardsync, sessionfile, settings
from pptmstr.boardlog import (
    Board,
    Claimed,
    Completed,
    Declared,
    Record,
    RecordingStopped,
    Unwritten,
    Written,
)
from pptmstr.boardsync import (
    BoardWriter,
    Found,
    Left,
    Linked,
    Migration,
    Moved,
    NoLog,
    Outcome,
    Report,
    Stopped,
)


@pytest.fixture
def writer(tmp_path: Path) -> Iterator[BoardWriter]:
    made = BoardWriter(tmp_path / "sessions")
    yield made
    made.close(timeout=10)


def _declared(n: int) -> Declared:
    return Declared(at=float(n), task_id=f"t{n}", title=f"task {n}", declared_by="lead")


def _logged(writer: BoardWriter, session_id: str) -> tuple[Record, ...]:
    directory = writer.directory(session_id)
    assert directory is not None
    log = boardlog.read(directory)
    assert log.damage is None
    return log.records


def _facts(writer: BoardWriter, session_id: str) -> tuple[Record, ...]:
    """What the log holds that is about the board: everything but a stop marker."""
    return tuple(r for r in _logged(writer, session_id) if not isinstance(r, RecordingStopped))


def _marker(writer: BoardWriter, session_id: str) -> RecordingStopped:
    """The one stop marker ``session_id``'s log carries, which must be its last record."""
    *_, last = _logged(writer, session_id)
    assert isinstance(last, RecordingStopped)
    assert [r for r in _logged(writer, session_id) if isinstance(r, RecordingStopped)] == [last]
    return last


def _outcomes(reports: tuple[Report, ...] | list[Report]) -> list[Outcome]:
    return [r for r in reports if isinstance(r, Outcome)]


def _reports(writer: BoardWriter, count: int) -> list[Report]:
    """
    Everything drained up to the ``count``-th outcome, from ``drain`` alone so the
    test never leans on ``close``.
    """
    seen: list[Report] = []
    deadline = time.monotonic() + 10
    while len(_outcomes(seen)) < count and time.monotonic() < deadline:
        seen.extend(writer.drain())
        time.sleep(0.001)
    assert len(_outcomes(seen)) == count
    return seen


def _settle(writer: BoardWriter, count: int) -> list[Outcome]:
    return _outcomes(_reports(writer, count))


# -- order ---------------------------------------------------------------------------


def test_records_reach_disk_in_the_order_they_were_submitted_across_two_sessions(
    writer: BoardWriter,
) -> None:
    submitted: list[tuple[str, Record]] = []
    for n in range(150):
        # Uneven interleaving, so neither session's order is the loop's parity.
        for session_id in ("alpha", "beta") if n % 3 else ("beta", "beta", "alpha"):
            record = _declared(len(submitted))
            submitted.append((session_id, record))
            writer.submit(session_id, record)

    outcomes = _settle(writer, len(submitted))

    assert [(o.session_id, o.record) for o in outcomes] == submitted
    assert all(o.kept for o in outcomes)
    for session_id in ("alpha", "beta"):
        assert list(_logged(writer, session_id)) == [r for s, r in submitted if s == session_id]
    assert writer.outstanding == 0


def test_a_later_submission_is_never_appended_before_an_earlier_one_has_returned(
    writer: BoardWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = boardlog.append
    inside = 0
    overlapped: list[Record] = []

    def one_at_a_time(directory: Path, record: Record) -> Written | Unwritten:
        nonlocal inside
        inside += 1
        if inside != 1:
            overlapped.append(record)
        time.sleep(0.0005)
        result = real(directory, record)
        inside -= 1
        return result

    monkeypatch.setattr(boardlog, "append", one_at_a_time)
    for n in range(60):
        writer.submit("alpha" if n % 2 else "beta", _declared(n))
    _settle(writer, 60)

    assert overlapped == []


# -- the thread ------------------------------------------------------------------------


def test_neither_the_append_nor_the_listing_before_it_runs_on_the_submitting_thread(
    writer: BoardWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    appended_on: list[int] = []
    listed_on: list[int] = []
    real_append, real_next = boardlog.append, boardlog.next_ordinal

    def append(directory: Path, record: Record) -> Written | Unwritten:
        appended_on.append(threading.get_ident())
        return real_append(directory, record)

    def next_ordinal(directory: Path) -> int | boardlog.Unlisted:
        listed_on.append(threading.get_ident())
        return real_next(directory)

    monkeypatch.setattr(boardlog, "append", append)
    monkeypatch.setattr(boardlog, "next_ordinal", next_ordinal)

    for n in range(20):
        writer.submit("alpha" if n % 2 else "beta", _declared(n))
    _settle(writer, 20)

    here = threading.get_ident()
    assert len(appended_on) == 20
    assert here not in appended_on
    assert len(set(appended_on)) == 1, "one worker, or the order is not the queue's"
    assert listed_on and set(listed_on) == set(appended_on)


def test_submit_and_drain_return_while_an_append_is_held(
    writer: BoardWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = threading.Event(), threading.Event()
    real = boardlog.append

    def held(directory: Path, record: Record) -> Written | Unwritten:
        entered.set()
        assert release.wait(10)
        return real(directory, record)

    monkeypatch.setattr(boardlog, "append", held)

    writer.submit("alpha", _declared(0))
    assert entered.wait(10)
    writer.submit("alpha", _declared(1))
    writer.submit("fresh", _declared(2))

    assert writer.drain() == ()
    assert writer.outstanding == 3
    # Nothing about the second session has touched the disk from this thread.
    assert not (writer.root / "fresh").exists()

    release.set()
    assert [o.record for o in _settle(writer, 3)] == [_declared(0), _declared(1), _declared(2)]


# -- a record that did not land -----------------------------------------------------


def test_an_append_the_filesystem_refuses_is_drained_as_a_value_and_the_next_one_lands(
    writer: BoardWriter,
) -> None:
    writer.root.mkdir(parents=True)
    (writer.root / "blocked").write_text("a file where the session's directory would be")

    writer.submit("blocked", _declared(0))
    writer.submit("alpha", _declared(1))
    refused, landed = _settle(writer, 2)

    assert refused.session_id == "blocked" and refused.record == _declared(0)
    assert isinstance(refused.result, Unwritten)
    assert not refused.kept
    assert landed.kept
    assert _logged(writer, "alpha") == (_declared(1),)


def test_an_append_that_raises_is_drained_as_a_value_and_does_not_end_the_worker(
    writer: BoardWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = boardlog.append

    def raising(directory: Path, record: Record) -> Written | Unwritten:
        if record == _declared(0):
            raise RuntimeError("the disk is on fire")
        return real(directory, record)

    monkeypatch.setattr(boardlog, "append", raising)

    writer.submit("alpha", _declared(0))
    writer.submit("beta", _declared(1))
    writer.submit("alpha", _declared(2))
    raised, stopped, landed, later = _reports(writer, 3)

    assert isinstance(raised, Outcome) and not raised.kept
    assert raised.result == Unwritten("RuntimeError: the disk is on fire")
    assert isinstance(stopped, Stopped)
    assert (stopped.session_id, stopped.record, stopped.reason) == (
        "alpha",
        _declared(0),
        "RuntimeError: the disk is on fire",
    )
    assert isinstance(landed, Outcome) and landed.kept
    assert isinstance(later, Outcome) and not later.kept
    assert _logged(writer, "beta") == (_declared(1),)
    assert _facts(writer, "alpha") == ()


# -- the first record that does not land ---------------------------------------------


def test_after_one_append_fails_nothing_later_is_appended_for_that_session(
    writer: BoardWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The append fails once and would succeed for every record after it. Appending
    those would leave ordinals 0 and 1 holding the first and third facts, which
    reads back whole.
    """
    real = boardlog.append
    asked: list[Record] = []

    def full_for_a_moment(directory: Path, record: Record) -> Written | Unwritten:
        asked.append(record)
        if record == _declared(1):
            return Unwritten("OSError: [Errno 28] No space left on device")
        return real(directory, record)

    monkeypatch.setattr(boardlog, "append", full_for_a_moment)

    for n in range(4):
        writer.submit("alpha", _declared(n))
    writer.submit("beta", _declared(4))
    reports = _reports(writer, 5)

    first, failed, stopped, third, fourth, other = reports
    assert isinstance(first, Outcome) and first.kept
    assert isinstance(failed, Outcome) and failed.record == _declared(1) and not failed.kept
    assert isinstance(stopped, Stopped)
    assert (stopped.session_id, stopped.record, stopped.reason) == (
        "alpha",
        _declared(1),
        "OSError: [Errno 28] No space left on device",
    )
    for later, record in ((third, _declared(2)), (fourth, _declared(3))):
        assert isinstance(later, Outcome) and later.record == record
        assert isinstance(later.result, Unwritten)
        assert "No space left on device" in later.result.reason
    assert isinstance(other, Outcome) and other.kept

    assert [r for r in asked if not isinstance(r, RecordingStopped)] == [
        _declared(0),
        _declared(1),
        _declared(4),
    ]
    assert _facts(writer, "alpha") == (_declared(0),)
    assert _logged(writer, "beta") == (_declared(4),)


def test_a_record_too_long_to_log_stops_its_session_and_is_reported_once(
    writer: BoardWriter,
) -> None:
    too_long = Declared(
        at=1.0,
        task_id="big",
        title="big",
        declared_by="lead",
        detail="x" * (boardlog.MAX_RECORD_BYTES + 1),
    )

    writer.submit("alpha", _declared(0))
    writer.submit("alpha", too_long)
    writer.submit("alpha", Claimed(2.0, "big", "builder"))
    writer.submit("alpha", _declared(3))
    reports = _reports(writer, 4)

    assert [type(r) for r in reports] == [Outcome, Outcome, Stopped, Outcome, Outcome]
    assert [o.kept for o in _outcomes(reports)] == [True, False, False, False]
    assert _facts(writer, "alpha") == (_declared(0),)


def test_a_record_written_behind_a_break_is_not_reported_as_kept(writer: BoardWriter) -> None:
    writer.submit("alpha", _declared(0))
    writer.submit("alpha", _declared(1))
    first, _ = _settle(writer, 2)
    assert isinstance(first.result, Written)
    os.unlink(first.result.path)

    writer.submit("alpha", _declared(2))
    (behind,) = _settle(writer, 1)

    assert isinstance(behind.result, Written) and behind.result.gap == 0
    assert not behind.kept


@pytest.mark.parametrize("session_id", ["", "..", "a/b", ".hidden", "x" * 300, "nul\0"])
def test_a_session_id_that_names_no_directory_is_a_value_from_submit_and_from_load(
    writer: BoardWriter, session_id: str
) -> None:
    writer.submit(session_id, _declared(0))
    (outcome,) = _settle(writer, 1)

    assert isinstance(outcome.result, Unwritten)
    assert isinstance(boardsync.load(session_id, writer.root), NoLog)
    assert not writer.root.exists()


# -- a log that stopped says so ----------------------------------------------------


def _finished_too_long(task_id: str) -> Completed:
    return Completed(
        at=3.0, task_id=task_id, by="builder", result="x" * (boardlog.MAX_RECORD_BYTES + 1)
    )


def test_a_log_that_stopped_being_written_loads_as_a_short_board_with_the_reason(
    writer: BoardWriter,
) -> None:
    """
    The completion does not land and a later declaration is never appended. The
    prefix on disk is a board with the task still held and nothing after it, and
    without a marker nothing about it says a fact is missing.
    """
    writer.submit("alpha", _declared(0))
    writer.submit("alpha", Claimed(2.0, "t0", "builder"))
    writer.submit("alpha", _finished_too_long("t0"))
    writer.submit("alpha", _declared(4))
    began = time.time()
    reports = _reports(writer, 4)

    (stopped,) = (r for r in reports if isinstance(r, Stopped))
    assert isinstance(stopped.marker, Written) and stopped.marker.gap is None

    board = boardsync.load("alpha", writer.root)
    assert isinstance(board, Board)
    assert [(t.id, t.state.name) for t in board.tasks] == [("t0", "CLAIMED")]
    assert board.short
    assert board.damage is None and not (board.stray or board.unrecognised or board.contested)
    (said,) = board.stopped
    assert said == _marker(writer, "alpha")
    assert said.reason == stopped.reason and "bytes" in said.reason
    assert began - 1 <= said.at <= time.time() + 1, "wall time, like every record's"


def test_a_stopped_log_stays_short_when_a_later_process_adopts_it_and_appends(
    tmp_path: Path,
) -> None:
    """
    The shape the stop exists to prevent: a hole in the middle. The records behind
    it are applied, and the board still says it is missing something.
    """
    first = BoardWriter(tmp_path)
    first.submit("alpha", _declared(0))
    first.submit("alpha", _finished_too_long("t0"))
    first.close(timeout=10)

    second = BoardWriter(tmp_path)
    second.adopt("alpha")
    second.submit("alpha", _declared(7))
    assert all(o.kept for o in _outcomes(second.close(timeout=10)))

    board = boardsync.load("alpha", tmp_path)
    assert isinstance(board, Board)
    assert [t.id for t in board.tasks] == ["t0", "t7"]
    assert board.short and len(board.stopped) == 1


def test_a_session_whose_first_record_did_not_land_loads_as_short_and_not_as_no_log(
    writer: BoardWriter,
) -> None:
    writer.submit("alpha", _finished_too_long("t0"))
    _settle(writer, 1)

    board = boardsync.load("alpha", writer.root)

    assert isinstance(board, Board) and board.short
    assert board.tasks == () and len(board.stopped) == 1


def test_the_marker_lands_when_the_reason_for_the_stop_is_itself_too_long_to_log(
    writer: BoardWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = boardlog.append
    essay = "no room " * boardlog.MAX_RECORD_BYTES

    def failing(directory: Path, record: Record) -> Written | Unwritten:
        if record == _declared(1):
            return Unwritten(essay)
        return real(directory, record)

    monkeypatch.setattr(boardlog, "append", failing)
    writer.submit("alpha", _declared(0))
    writer.submit("alpha", _declared(1))
    _, _, stopped = _reports(writer, 2)

    assert isinstance(stopped, Stopped) and stopped.reason == essay
    assert isinstance(stopped.marker, Written)
    said = _marker(writer, "alpha")
    assert said.reason == essay[: boardsync.MARKER_REASON_CHARS]
    assert len(boardlog.encode(said)) < boardlog.MAX_RECORD_BYTES // 100


def test_a_stop_whose_marker_cannot_land_either_is_reported_as_unmarked(
    writer: BoardWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The disk stays full. The owner is told the log does not say it stopped, and
    the last assertion is the limit the module states: that log restores whole.
    """
    real = boardlog.append
    full = False

    def filling(directory: Path, record: Record) -> Written | Unwritten:
        nonlocal full
        full = full or record == _declared(1)
        if full:
            return Unwritten("OSError: [Errno 28] No space left on device")
        return real(directory, record)

    monkeypatch.setattr(boardlog, "append", filling)
    writer.submit("alpha", _declared(0))
    writer.submit("alpha", _declared(1))
    _, _, stopped = _reports(writer, 2)

    assert isinstance(stopped, Stopped)
    assert stopped.marker == Unwritten("OSError: [Errno 28] No space left on device")
    board = boardsync.load("alpha", writer.root)
    assert isinstance(board, Board) and not board.short


def test_a_marker_whose_append_raises_is_a_value_and_does_not_end_the_worker(
    writer: BoardWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = boardlog.append

    def raising(directory: Path, record: Record) -> Written | Unwritten:
        if directory.parent.name == "alpha":
            raise RuntimeError("gone")
        return real(directory, record)

    monkeypatch.setattr(boardlog, "append", raising)
    writer.submit("alpha", _declared(0))
    writer.submit("beta", _declared(1))
    _, stopped, landed = _reports(writer, 2)

    assert isinstance(stopped, Stopped) and stopped.marker == Unwritten("RuntimeError: gone")
    assert isinstance(landed, Outcome) and landed.kept


def test_the_marker_is_written_once_however_many_records_follow_the_stop(
    writer: BoardWriter,
) -> None:
    writer.submit("alpha", _declared(0))
    writer.submit("alpha", _finished_too_long("t0"))
    for n in range(2, 8):
        writer.submit("alpha", _declared(n))
    reports = _reports(writer, 8)

    assert len([r for r in reports if isinstance(r, Stopped)]) == 1
    assert _logged(writer, "alpha") == (_declared(0), _marker(writer, "alpha"))


def test_a_session_id_that_names_no_directory_stops_with_no_marker(writer: BoardWriter) -> None:
    writer.submit("a/b", _declared(0))
    _, stopped = _reports(writer, 1)

    assert isinstance(stopped, Stopped) and stopped.marker is None
    assert not writer.root.exists()


# -- a directory nobody could look in ------------------------------------------------


def _unlistable_once(monkeypatch: pytest.MonkeyPatch, directory: Path) -> list[str]:
    """``os.listdir(directory)`` fails the first time it is asked, and only then."""
    real = os.listdir
    failed: list[str] = []

    def listdir(path: str | Path) -> list[str]:
        if Path(path) == directory and not failed:
            failed.append(str(path))
            raise OSError(5, "Input/output error")
        return real(path)

    monkeypatch.setattr(os, "listdir", listdir)
    return failed


def test_a_log_that_could_not_be_listed_for_one_moment_is_not_taken_for_a_new_one(
    writer: BoardWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The listing fails once and every call after it succeeds, the append included.
    Read as "no record is here", the session is admitted and the same task is
    declared a second time behind a history nobody restored.
    """
    directory = _someone_elses_log(writer.root, "resumed")
    before = sorted(os.listdir(directory))
    failed = _unlistable_once(monkeypatch, directory)

    writer.submit("resumed", _declared(0))
    writer.submit("resumed", Claimed(2.0, "t0", "builder-2"))
    refused, stopped, later = _reports(writer, 2)

    assert failed
    assert isinstance(refused, Outcome) and isinstance(refused.result, Unwritten)
    assert "could not be listed" in refused.result.reason
    assert "Input/output error" in refused.result.reason
    assert isinstance(stopped, Stopped) and stopped.marker is None
    assert isinstance(later, Outcome) and not later.kept
    assert sorted(os.listdir(directory)) == before
    assert not boardlog.restore_log(boardlog.read(directory)).short


def test_an_adopted_log_is_appended_to_without_asking_what_its_directory_holds(
    writer: BoardWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = _someone_elses_log(writer.root, "resumed")
    writer.adopt("resumed")
    asked: list[Path] = []

    def unlistable(directory: Path) -> boardlog.Unlisted:
        asked.append(directory)
        return boardlog.Unlisted("nobody may look")

    monkeypatch.setattr(boardlog, "next_ordinal", unlistable)

    writer.submit("resumed", _declared(7))

    assert _settle(writer, 1)[0].kept
    assert asked == []
    assert len(boardlog.read(directory).records) == 3


# -- a log nobody restored ---------------------------------------------------------


def _someone_elses_log(root: Path, session_id: str) -> Path:
    directory = sessionfile.board_dir(session_id, root)
    assert directory is not None
    assert isinstance(boardlog.append(directory, _declared(0)), Written)
    assert isinstance(boardlog.append(directory, Claimed(1.0, "t0", "builder")), Written)
    return directory


def test_a_log_this_process_did_not_restore_is_not_appended_to(writer: BoardWriter) -> None:
    directory = _someone_elses_log(writer.root, "resumed")
    before = sorted(os.listdir(directory))

    # The record a reducer that never saw the log would produce: the same task again.
    writer.submit("resumed", _declared(0))
    writer.submit("resumed", Claimed(2.0, "t0", "builder-2"))
    outcomes = _settle(writer, 2)

    assert all(isinstance(o.result, Unwritten) and not o.kept for o in outcomes)
    assert sorted(os.listdir(directory)) == before
    assert not boardlog.restore_log(boardlog.read(directory)).short


def test_the_refusal_is_what_keeps_the_restored_board_uncontested(tmp_path: Path) -> None:
    """
    The hazard the refusal exists for, built without it: the same two records
    appended behind the log restore as a contest.
    """
    directory = _someone_elses_log(tmp_path, "resumed")
    boardlog.append(directory, _declared(0))
    boardlog.append(directory, Claimed(2.0, "t0", "builder-2"))

    assert boardlog.restore_log(boardlog.read(directory)).contested


def test_adopting_a_log_is_what_lets_the_writer_append_to_it(writer: BoardWriter) -> None:
    _someone_elses_log(writer.root, "resumed")

    board = boardsync.load("resumed", writer.root)
    assert isinstance(board, Board)
    assert [(t.id, t.holder) for t in board.tasks] == [("t0", "builder")]
    writer.adopt("resumed")
    writer.submit("resumed", _declared(7))

    (landed,) = _settle(writer, 1)
    assert landed.kept
    assert _logged(writer, "resumed") == (
        _declared(0),
        Claimed(1.0, "t0", "builder"),
        _declared(7),
    )


def test_an_adoption_made_while_the_worker_is_listing_the_directory_counts(
    writer: BoardWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    _someone_elses_log(writer.root, "resumed")
    real = boardlog.next_ordinal

    def adopted_meanwhile(directory: Path) -> int | boardlog.Unlisted:
        found = real(directory)
        writer.adopt("resumed")
        return found

    monkeypatch.setattr(boardlog, "next_ordinal", adopted_meanwhile)
    writer.submit("resumed", _declared(7))

    (landed,) = _settle(writer, 1)
    assert landed.kept
    assert len(_logged(writer, "resumed")) == 3


def test_loading_a_log_without_adopting_it_admits_nothing(writer: BoardWriter) -> None:
    directory = _someone_elses_log(writer.root, "resumed")

    assert isinstance(boardsync.load("resumed", writer.root), Board)
    writer.submit("resumed", _declared(7))

    refused, stopped = _reports(writer, 1)
    assert isinstance(refused, Outcome) and isinstance(refused.result, Unwritten)
    assert isinstance(stopped, Stopped) and stopped.session_id == "resumed"
    # No marker either: the log is whole, and it is somebody else's.
    assert stopped.marker is None
    assert len(boardlog.read(directory).records) == 2


def test_adopting_after_a_record_was_refused_does_not_restart_the_recording(
    writer: BoardWriter,
) -> None:
    """
    The refused record is a fact the log does not have. Records appended after it
    would read back as a whole board without it.
    """
    directory = _someone_elses_log(writer.root, "resumed")
    writer.submit("resumed", Claimed(2.0, "t0", "builder-2"))
    _settle(writer, 1)

    writer.adopt("resumed")
    writer.submit("resumed", _declared(7))

    reports = _reports(writer, 1)
    assert [type(r) for r in reports] == [Outcome], "the break is reported once"
    assert not _outcomes(reports)[0].kept
    assert len(boardlog.read(directory).records) == 2


def test_a_session_with_no_record_on_disk_is_one_this_process_starts(writer: BoardWriter) -> None:
    # Missing entirely, and present holding only what another append left mid-write.
    empty = writer.directory("empty")
    assert empty is not None
    empty.mkdir(parents=True)
    (empty / ".record-abc.tmp").write_text("{")

    writer.submit("missing", _declared(0))
    writer.submit("empty", _declared(1))
    writer.submit("missing", _declared(2))

    assert all(o.kept for o in _settle(writer, 3))
    assert _logged(writer, "missing") == (_declared(0), _declared(2))
    assert _logged(writer, "empty") == (_declared(1),)


def test_a_load_that_finds_nothing_says_so_and_makes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "sessions"

    assert isinstance(boardsync.load("never", root), NoLog)
    assert not root.exists()

    (root / "never" / "board").mkdir(parents=True)
    assert isinstance(boardsync.load("never", root), NoLog)
    assert boardsync.find("never", root) == Found(
        NoLog("nothing is logged for this session"), root / "never" / "board"
    )


@pytest.mark.real_config_dir
def test_load_without_a_root_reads_under_the_sessions_root(tmp_path: Path) -> None:
    # Before anything is written: with the redirect gone this would be the real one.
    assert sessionfile.root() != settings.config_dir() / "sessions"
    _someone_elses_log(sessionfile.root(), "resumed")

    assert isinstance(boardsync.load("resumed"), Board)
    assert boardsync.find("resumed").directory == sessionfile.root() / "resumed" / "board"
    assert isinstance(boardsync.load("resumed", tmp_path), NoLog)


def test_a_damaged_log_loads_as_a_short_board_and_not_as_no_log(writer: BoardWriter) -> None:
    directory = _someone_elses_log(writer.root, "torn")
    (directory / "00000000.json").write_text("{ not a record")

    board = boardsync.load("torn", writer.root)

    assert isinstance(board, Board)
    assert board.short and board.damage is not None and board.damage.ordinal == 0
    assert board.tasks == ()


# -- shutdown ------------------------------------------------------------------------


def test_close_waits_for_what_is_queued_and_returns_it(writer: BoardWriter) -> None:
    for n in range(40):
        writer.submit("alpha", _declared(n))

    outcomes = _outcomes(writer.close(timeout=10))

    assert [o.record for o in outcomes] == [_declared(n) for n in range(40)]
    assert all(o.kept for o in outcomes)
    assert len(_logged(writer, "alpha")) == 40
    assert writer.close(timeout=10) == ()
    assert writer.drain() == ()


def test_close_gives_up_at_its_bound_and_names_every_record_that_did_not_land(
    writer: BoardWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = threading.Event(), threading.Event()
    real = boardlog.append

    def wedged(directory: Path, record: Record) -> Written | Unwritten:
        if record == _declared(1):
            entered.set()
            assert release.wait(30)
        return real(directory, record)

    monkeypatch.setattr(boardlog, "append", wedged)

    writer.submit("alpha", _declared(0))
    writer.submit("alpha", _declared(1))
    writer.submit("beta", _declared(2))
    writer.submit("alpha", _declared(3))
    assert entered.wait(10)

    began = time.monotonic()
    reports = writer.close(timeout=0.2)
    took = time.monotonic() - began
    outcomes = _outcomes(reports)
    assert len(outcomes) == len(reports)

    assert 0.15 < took < 5
    assert [(o.session_id, o.record) for o in outcomes] == [
        ("alpha", _declared(0)),
        ("alpha", _declared(1)),
        ("beta", _declared(2)),
        ("alpha", _declared(3)),
    ]
    assert outcomes[0].kept
    assert all(isinstance(o.result, Unwritten) for o in outcomes[1:])
    in_flight, *queued = (o.result.reason for o in outcomes[1:] if isinstance(o.result, Unwritten))
    assert "may still reach disk" in in_flight
    assert all("not written" in reason for reason in queued)
    assert writer.outstanding == 0

    # Twice is safe, and costs no second wait.
    began = time.monotonic()
    assert writer.close(timeout=5) == ()
    assert time.monotonic() - began < 1

    # What was reported as not written stays unwritten once the append lets go.
    release.set()
    worker = writer._worker
    assert worker is not None
    worker.join(10)
    assert not worker.is_alive()
    assert _declared(3) not in _logged(writer, "alpha")
    assert not (writer.root / "beta").exists()


def test_a_record_submitted_after_close_is_reported_and_not_written(writer: BoardWriter) -> None:
    writer.submit("alpha", _declared(0))
    writer.close(timeout=10)

    writer.submit("alpha", _declared(1))

    (late,) = _outcomes(writer.drain())
    assert late.record == _declared(1) and isinstance(late.result, Unwritten)
    assert _logged(writer, "alpha") == (_declared(0),)


def test_closing_a_writer_that_was_never_used_starts_no_thread_and_makes_no_directory(
    tmp_path: Path,
) -> None:
    writer = BoardWriter(tmp_path / "sessions", tmp_path / "boards")

    assert writer.close(timeout=10) == ()
    assert writer._worker is None
    assert list(tmp_path.iterdir()) == []


# -- where the files go ----------------------------------------------------------------


def test_a_log_is_board_in_the_sessions_directory_and_asking_makes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    config = tmp_path / "xdg" / "pptmstr"

    writer = BoardWriter()

    assert settings.config_dir() == config
    assert boardsync.legacy_root() == config / "boards"
    assert (writer.root, writer.legacy) == (config / "sessions", config / "boards")
    assert writer.directory("alpha") == config / "sessions" / "alpha" / "board"
    assert writer.directory("a/b") is None
    assert writer.close(timeout=10) == ()
    assert not (tmp_path / "xdg").exists()


def test_a_root_a_caller_chose_brings_no_legacy_root_with_it(tmp_path: Path) -> None:
    """
    Otherwise a test's writer under ``tmp_path`` would be refused, or not, by what
    happens to be under the configuration directory's ``boards``.
    """
    _legacy_log(boardsync.legacy_root(), "alpha")

    writer = BoardWriter(tmp_path)
    writer.submit("alpha", _declared(5))

    assert writer.legacy is None
    assert all(o.kept for o in _outcomes(writer.close(timeout=10)))
    assert boardsync.find("alpha", tmp_path).unrecorded is None
    # And with no root chosen, the one under the configuration directory is asked.
    assert boardsync.find("alpha").unrecorded is not None


@pytest.mark.real_config_dir
def test_the_suite_never_gets_the_real_config_directory_as_either_root(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """
    Fails when the conftest redirect is removed: without it these are the operator's
    ``~/.config/pptmstr/sessions`` and ``~/.config/pptmstr/boards``.
    """
    real = settings.config_dir()
    assert not real.is_relative_to(tmp_path_factory.getbasetemp()), "the marker's doing"

    for root, name in ((sessionfile.root(), "sessions"), (boardsync.legacy_root(), "boards")):
        assert root != real / name
        assert root.is_relative_to(tmp_path_factory.getbasetemp())
    assert sessionfile.root().parent == boardsync.legacy_root().parent
    directory = sessionfile.board_dir("alpha")
    assert directory is not None and directory.is_relative_to(tmp_path_factory.getbasetemp())


@pytest.mark.real_config_dir
@pytest.mark.parametrize("config_home", [None, "/nonexistent/config", "relative/config"])
def test_no_setting_of_the_config_variable_gets_a_root_outside_the_suite(
    config_home: str | None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """
    The real root is under ``XDG_CONFIG_HOME`` when that is set and under the home
    when it is not, so whichever the suite was started with, a test that flips it
    reaches the other. Both, and a third place, are tried from one starting state;
    the suite's own starting state is the other half and is not this test's to set.
    """
    if config_home is None:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", config_home)

    for root, name in ((sessionfile.root(), "sessions"), (boardsync.legacy_root(), "boards")):
        assert root != settings.config_dir() / name
        assert root.is_relative_to(tmp_path_factory.getbasetemp())


@pytest.mark.real_config_dir
def test_a_writer_built_without_a_root_writes_under_the_redirected_one() -> None:
    real = settings.config_dir()
    assert sessionfile.root() != real / "sessions"
    writer = BoardWriter()

    writer.submit("alpha", _declared(0))
    (outcome,) = _outcomes(writer.close(timeout=10))

    assert writer.root == sessionfile.root() != real / "sessions"
    assert writer.legacy == boardsync.legacy_root() != real / "boards"
    assert isinstance(outcome.result, Written)
    assert outcome.result.path.parent == sessionfile.root() / "alpha" / "board"
    assert not boardsync.legacy_root().exists()


@pytest.mark.real_config_dir
def test_a_move_asked_for_without_roots_is_between_the_redirected_ones(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """
    ``migrate()`` renames what it finds. Reaching the operator's ``boards`` from a
    test would move their logs, and the test ending would not move them back.
    """
    # Before anything is written or moved: with the redirect gone these are real.
    for root in (sessionfile.root(), boardsync.legacy_root()):
        assert root.is_relative_to(tmp_path_factory.getbasetemp())
    source = _legacy_log(boardsync.legacy_root(), "alpha")

    (moved,) = boardsync.migrate().entries

    assert isinstance(moved, Moved) and moved.unlinked is None
    assert moved.source == source
    assert moved.target == sessionfile.root() / "alpha" / "board"
    assert moved.target.is_relative_to(tmp_path_factory.getbasetemp())


# -- a log at the legacy path ----------------------------------------------------------


@pytest.fixture
def sessions(tmp_path: Path) -> Path:
    return tmp_path / "sessions"


@pytest.fixture
def legacy(tmp_path: Path) -> Path:
    return tmp_path / "boards"


def _legacy_log(legacy: Path, session_id: str) -> Path:
    """A log as a build that kept them under ``boards/`` wrote it: ``t0``, held."""
    directory = legacy / session_id
    assert isinstance(boardlog.append(directory, _declared(0)), Written)
    assert isinstance(boardlog.append(directory, Claimed(1.0, "t0", "builder")), Written)
    return directory


def _files(*roots: Path) -> dict[int, bytes]:
    """
    Every regular file under ``roots``, by inode. Nothing was deleted or rewritten
    between two of these when the earlier one's items are all in the later one:
    a rename keeps the inode, and a copy followed by a removal does not.
    """
    held: dict[int, bytes] = {}
    for root in roots:
        for here, _, names in os.walk(root):
            for name in names:
                path = Path(here) / name
                if not path.is_symlink():
                    held[path.stat().st_ino] = path.read_bytes()
    return held


def _entries(*roots: Path) -> list[tuple[str, str]]:
    """Every name under ``roots`` and what it is, links unfollowed."""
    seen: list[tuple[str, str]] = []
    for root in roots:
        for here, directories, names in os.walk(root):
            for name in (*directories, *names):
                path = Path(here) / name
                kind = (
                    f"link to {os.readlink(path)}"
                    if path.is_symlink()
                    else "directory" if path.is_dir() else "file"
                )
                seen.append((str(path), kind))
    return sorted(seen)


def _tasks(found: Found) -> list[tuple[str, str]]:
    assert isinstance(found.board, Board)
    return [(t.id, t.state.name) for t in found.board.tasks]


def test_a_log_only_at_the_legacy_path_is_restored_from_there_and_not_to_be_appended_to(
    sessions: Path, legacy: Path
) -> None:
    old = _legacy_log(legacy, "resumed")
    before = _entries(legacy)

    found = boardsync.find("resumed", sessions, legacy)

    assert _tasks(found) == [("t0", "CLAIMED")]
    assert isinstance(found.board, Board) and not found.board.short
    assert (found.directory, found.second) == (old, None)
    assert found.unrecorded is not None and "legacy path" in found.unrecorded
    assert str(old) in found.unrecorded and str(sessions / "resumed" / "board") in found.unrecorded
    assert boardsync.load("resumed", sessions, legacy) == found.board
    # Reading it made nothing and moved nothing.
    assert not sessions.exists() and _entries(legacy) == before


def test_the_writer_appends_nothing_for_a_session_whose_log_is_at_the_legacy_path(
    sessions: Path, legacy: Path
) -> None:
    """
    Adopted, as a shell that restored the legacy board and did not ask ``find``
    whether it may be appended behind would. Appending under the new path would
    start a second directory for the session with the older half left behind.
    """
    _legacy_log(legacy, "resumed")
    before = _entries(legacy)
    writer = BoardWriter(sessions, legacy)
    writer.adopt("resumed")

    writer.submit("resumed", _declared(7))
    writer.submit("fresh", _declared(8))
    writer.submit("resumed", _declared(9))
    refused, stopped, landed, later = _reports(writer, 3)
    writer.close(timeout=10)

    assert isinstance(refused, Outcome) and isinstance(refused.result, Unwritten)
    assert "legacy path" in refused.result.reason and str(legacy / "resumed") in (
        refused.result.reason
    )
    assert isinstance(stopped, Stopped) and stopped.marker is None
    assert isinstance(landed, Outcome) and landed.kept
    assert isinstance(later, Outcome) and not later.kept
    assert _entries(legacy) == before
    assert not (sessions / "resumed").exists()
    assert _logged(writer, "fresh") == (_declared(8),)


def test_records_in_both_places_are_two_histories_and_neither_is_appended_to(
    sessions: Path, legacy: Path
) -> None:
    """
    The split: the earlier facts under the new path, the later ones where an older
    build went on writing. Read as "the new one wins", the board is whole, it is
    adopted, and the task the old path finished is held again and done twice.
    """
    new = sessionfile.board_dir("split", sessions)
    assert new is not None
    boardlog.append(new, _declared(0))
    boardlog.append(new, Claimed(1.0, "t0", "builder"))
    boardlog.append(legacy / "split", Completed(2.0, "t0", "builder", "done"))
    before = _entries(sessions, legacy)

    found = boardsync.find("split", sessions, legacy)

    assert _tasks(found) == [("t0", "CLAIMED")]
    assert (found.directory, found.second) == (new, legacy / "split")
    assert found.unrecorded is not None and "two places" in found.unrecorded
    assert str(new) in found.unrecorded and str(legacy / "split") in found.unrecorded

    writer = BoardWriter(sessions, legacy)
    writer.adopt("split")
    writer.submit("split", _declared(7))
    refused, stopped = _reports(writer, 1)
    writer.close(timeout=10)

    assert isinstance(refused, Outcome) and isinstance(refused.result, Unwritten)
    assert "two places" in refused.result.reason
    # No marker: neither directory is this writer's to say anything in.
    assert isinstance(stopped, Stopped) and stopped.marker is None
    assert _entries(sessions, legacy) == before


@pytest.mark.parametrize("left_behind", ["nothing", "a temp file", "no directory"])
def test_a_legacy_directory_that_holds_no_record_is_not_a_second_history(
    sessions: Path, legacy: Path, left_behind: str
) -> None:
    _someone_elses_log(sessions, "resumed")
    if left_behind != "no directory":
        (legacy / "resumed").mkdir(parents=True)
    if left_behind == "a temp file":
        (legacy / "resumed" / ".record-abc.tmp").write_text("{")
    before = _entries(legacy)

    found = boardsync.find("resumed", sessions, legacy)
    writer = BoardWriter(sessions, legacy)
    writer.adopt("resumed")
    writer.submit("resumed", _declared(7))
    outcomes = _outcomes(writer.close(timeout=10))

    assert found == Found(found.board, sessions / "resumed" / "board")
    assert _tasks(found) == [("t0", "CLAIMED")]
    assert all(o.kept for o in outcomes)
    assert len(_logged(writer, "resumed")) == 3
    assert _entries(legacy) == before


def test_a_legacy_entry_nobody_could_list_is_not_taken_for_one_that_holds_nothing(
    sessions: Path, legacy: Path
) -> None:
    _someone_elses_log(sessions, "resumed")
    legacy.mkdir()
    (legacy / "resumed").write_text("a file where the old directory would be")

    found = boardsync.find("resumed", sessions, legacy)

    assert found.second == legacy / "resumed" and found.unrecorded is not None


def test_the_writer_makes_nothing_under_the_legacy_root(sessions: Path, legacy: Path) -> None:
    """
    Whatever becomes of the record: landed, refused as unadopted, stopped with a
    marker, refused for an id that names nothing.
    """
    _someone_elses_log(sessions, "theirs")
    writer = BoardWriter(sessions, legacy)

    writer.submit("alpha", _declared(0))
    writer.submit("alpha", _finished_too_long("t0"))
    writer.submit("theirs", _declared(1))
    writer.submit("a/b", _declared(2))
    reports = writer.close(timeout=10)

    assert [o.kept for o in _outcomes(reports)] == [True, False, False, False]
    assert isinstance(_marker(writer, "alpha"), RecordingStopped)
    assert not legacy.exists()

    # And where the legacy root is there, it is left as it was found.
    _legacy_log(legacy, "old")
    before = _entries(legacy)
    second = BoardWriter(sessions, legacy)
    second.submit("old", _declared(3))
    second.submit("beta", _declared(4))
    second.close(timeout=10)
    assert _entries(legacy) == before


# -- the move --------------------------------------------------------------------------


def test_a_log_at_the_legacy_path_alone_is_renamed_and_a_link_is_left_at_the_old_name(
    sessions: Path, legacy: Path
) -> None:
    old = _legacy_log(legacy, "s-1")
    before = _files(sessions, legacy)

    migration = boardsync.migrate(sessions, legacy)

    new = sessions / "s-1" / "board"
    assert migration == Migration((Moved("s-1", old, new),))
    assert migration.moved == migration.entries and migration.left == ()
    assert before.items() <= _files(sessions, legacy).items() and len(before) == 2
    assert _entries(sessions, legacy) == sorted(
        [
            (str(old), "link to ../sessions/s-1/board"),
            (str(sessions / "s-1"), "directory"),
            (str(new), "directory"),
            (str(new / "00000000.json"), "file"),
            (str(new / "00000001.json"), "file"),
        ]
    )
    # One history, appendable, by either name.
    found = boardsync.find("s-1", sessions, legacy)
    assert found == Found(found.board, new) and _tasks(found) == [("t0", "CLAIMED")]
    assert boardlog.read(old) == boardlog.read(new)


def test_a_moved_log_is_one_this_build_appends_to_once_it_is_adopted(
    sessions: Path, legacy: Path
) -> None:
    """
    The link at the old name leads to the log itself. Taken for a second place
    that holds records, every moved session would run unrecorded.
    """
    _legacy_log(legacy, "s-1")
    boardsync.migrate(sessions, legacy)
    writer = BoardWriter(sessions, legacy)
    writer.adopt("s-1")

    writer.submit("s-1", _declared(7))

    assert all(o.kept for o in _outcomes(writer.close(timeout=10)))
    assert _logged(writer, "s-1") == (_declared(0), Claimed(1.0, "t0", "builder"), _declared(7))


def test_a_build_that_still_appends_by_the_old_name_lands_in_the_one_log(
    sessions: Path, legacy: Path
) -> None:
    """
    ``boardlog.append`` as an older build calls it: by ``boards/<id>``. Without the
    link it makes that directory again, writes ordinal 0 there and reports
    ``Written``.
    """
    old = _legacy_log(legacy, "s-1")
    boardsync.migrate(sessions, legacy)
    new = sessions / "s-1" / "board"

    assert boardlog.next_ordinal(old) == 2
    written = boardlog.append(old, Completed(2.0, "t0", "builder", "done"))
    ours = boardlog.append(new, _declared(7))

    assert isinstance(written, Written) and (written.ordinal, written.gap) == (2, None)
    assert isinstance(ours, Written) and (ours.ordinal, ours.gap) == (3, None)
    assert old.is_symlink()
    assert sorted(p.name for p in new.iterdir()) == [f"{n:08d}.json" for n in range(4)]
    found = boardsync.find("s-1", sessions, legacy)
    assert found.unrecorded is None and found.second is None
    assert _tasks(found) == [("t0", "COMPLETED"), ("t7", "PENDING")]
    assert boardlog.read(old) == boardlog.read(new)


def test_a_session_with_a_log_only_at_the_new_path_is_left_alone(
    sessions: Path, legacy: Path
) -> None:
    _someone_elses_log(sessions, "s-1")
    before = _entries(sessions, legacy)

    assert boardsync.migrate(sessions, legacy) == Migration()
    legacy.mkdir()
    assert boardsync.migrate(sessions, legacy) == Migration()

    assert _entries(sessions, legacy) == before
    assert not (legacy / "s-1").exists()


def test_a_session_with_a_log_at_both_paths_has_neither_moved(sessions: Path, legacy: Path) -> None:
    new = _someone_elses_log(sessions, "s-1")
    old = legacy / "s-1"
    boardlog.append(old, Completed(2.0, "t0", "builder", "done"))
    files, entries = _files(sessions, legacy), _entries(sessions, legacy)

    (left,) = boardsync.migrate(sessions, legacy).entries

    assert isinstance(left, Left) and left.twice
    assert (left.name, left.source) == ("s-1", old)
    assert str(new) in left.reason
    assert _files(sessions, legacy) == files and _entries(sessions, legacy) == entries
    found = boardsync.find("s-1", sessions, legacy)
    assert found.second == old and found.unrecorded is not None
    assert _tasks(found) == [("t0", "CLAIMED")], "not the old path's completion merged in"


def test_an_empty_directory_at_the_new_path_does_not_keep_a_log_at_the_legacy_one(
    sessions: Path, legacy: Path
) -> None:
    """
    ``rename(2)`` takes the place of a directory only when it holds nothing, so
    nothing is lost and the test for "holds nothing" cannot be out of date.
    """
    old = _legacy_log(legacy, "s-1")
    new = sessions / "s-1" / "board"
    new.mkdir(parents=True)
    before = _files(sessions, legacy)

    assert boardsync.migrate(sessions, legacy) == Migration((Moved("s-1", old, new),))

    assert before.items() <= _files(sessions, legacy).items()
    assert len(boardlog.read(new).records) == 2


def test_a_new_path_holding_anything_at_all_is_not_renamed_onto(
    sessions: Path, legacy: Path
) -> None:
    """
    Another append's temp file is not a record, and it is somebody's: the legacy
    log stays where it is, is restored from there, and is not appended to.
    """
    old = _legacy_log(legacy, "s-1")
    new = sessions / "s-1" / "board"
    new.mkdir(parents=True)
    (new / ".record-abc.tmp").write_text("{")
    files, entries = _files(sessions, legacy), _entries(sessions, legacy)

    (left,) = boardsync.migrate(sessions, legacy).entries

    assert isinstance(left, Left) and left.twice
    assert _files(sessions, legacy) == files and _entries(sessions, legacy) == entries
    found = boardsync.find("s-1", sessions, legacy)
    assert (found.directory, found.second) == (old, None)
    assert found.unrecorded is not None and _tasks(found) == [("t0", "CLAIMED")]


def test_a_rename_that_fails_leaves_the_log_where_it_was_and_says_why(
    sessions: Path, legacy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _legacy_log(legacy, "s-1")
    _legacy_log(legacy, "s-2")
    files = _files(sessions, legacy)
    real = os.rename

    def cross_device(source: Path, target: Path) -> None:
        if Path(source).name == "s-1":
            raise OSError(18, "Invalid cross-device link")
        real(source, target)

    monkeypatch.setattr(os, "rename", cross_device)

    left, moved = boardsync.migrate(sessions, legacy).entries

    assert isinstance(left, Left) and not left.twice
    assert "Invalid cross-device link" in left.reason and "could not be renamed" in left.reason
    assert isinstance(moved, Moved) and moved.session_id == "s-2", "one failure is one session's"
    assert files.items() <= _files(sessions, legacy).items()
    assert old.is_dir() and not old.is_symlink()
    assert not (sessions / "s-1" / "board").exists()
    found = boardsync.find("s-1", sessions, legacy)
    assert found.directory == old and found.unrecorded is not None
    assert _tasks(found) == [("t0", "CLAIMED")]


def test_a_sessions_root_that_cannot_be_made_leaves_every_log_where_it_was(
    sessions: Path, legacy: Path
) -> None:
    old = _legacy_log(legacy, "s-1")
    sessions.write_text("a file where the sessions directory would be")
    files, entries = _files(legacy), _entries(legacy)

    (left,) = boardsync.migrate(sessions, legacy).entries

    assert isinstance(left, Left) and left.source == old
    assert _files(legacy) == files and _entries(legacy) == entries
    assert sessions.read_text() == "a file where the sessions directory would be"


def test_entries_that_are_not_a_sessions_log_are_left_and_named(
    sessions: Path, legacy: Path
) -> None:
    legacy.mkdir()
    (legacy / "a-file").write_text("not a log")
    (legacy / ".hidden").mkdir()
    (legacy / ".hidden" / "00000000.json").write_text("{}")
    os.symlink(legacy / ".hidden", legacy / "a-link")
    os.symlink("nowhere", legacy / "dangling")
    files, entries = _files(legacy), _entries(legacy)

    migration = boardsync.migrate(sessions, legacy)

    assert [(e.name, type(e)) for e in migration.entries] == [
        (".hidden", Left),
        ("a-file", Left),
        ("a-link", Left),
        ("dangling", Left),
    ]
    reasons = {e.name: e.reason for e in migration.left}
    assert reasons[".hidden"] == "its name is not a session id"
    assert reasons["a-file"] == "it is not a directory"
    assert "symbolic link" in reasons["a-link"] and "symbolic link" in reasons["dangling"]
    assert not any(e.twice for e in migration.left)
    assert _files(legacy) == files and _entries(legacy) == entries
    assert not sessions.exists()


def test_a_second_start_finds_the_links_and_moves_nothing(sessions: Path, legacy: Path) -> None:
    old = _legacy_log(legacy, "s-1")
    boardsync.migrate(sessions, legacy)
    files, entries = _files(sessions, legacy), _entries(sessions, legacy)

    again = boardsync.migrate(sessions, legacy)

    assert again == Migration((Linked("s-1", old, sessions / "s-1" / "board"),))
    assert again.moved == () and again.left == ()
    assert _files(sessions, legacy) == files and _entries(sessions, legacy) == entries


def test_the_link_leads_to_the_log_when_the_legacy_root_is_itself_a_link(
    tmp_path: Path, sessions: Path, legacy: Path
) -> None:
    """
    A relative link is resolved from where the old name really is, which is not
    beside ``sessions`` when ``boards`` was put somewhere else and linked.
    """
    real = tmp_path / "kept" / "elsewhere" / "boards"
    _legacy_log(real, "s-1")
    os.symlink(real, legacy)

    (moved,) = boardsync.migrate(sessions, legacy).entries

    assert isinstance(moved, Moved) and moved.unlinked is None
    assert os.path.samefile(legacy / "s-1", sessions / "s-1" / "board")
    assert not os.path.isabs(os.readlink(real / "s-1"))
    assert boardsync.find("s-1", sessions, legacy).unrecorded is None


def test_a_link_that_could_not_be_left_is_said_and_the_log_is_still_moved(
    sessions: Path, legacy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _legacy_log(legacy, "s-1")
    before = _files(sessions, legacy)

    def refused(target: str, source: Path) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "symlink", refused)

    (moved,) = boardsync.migrate(sessions, legacy).entries

    assert isinstance(moved, Moved)
    assert moved.unlinked is not None and "Operation not permitted" in moved.unlinked
    assert before.items() <= _files(sessions, legacy).items()
    assert not os.path.lexists(old)
    assert boardsync.find("s-1", sessions, legacy).unrecorded is None


def test_a_legacy_root_that_cannot_be_listed_is_said_and_one_that_is_absent_is_not(
    sessions: Path, legacy: Path
) -> None:
    assert boardsync.migrate(sessions, legacy) == Migration()
    assert not sessions.exists() and not legacy.exists()

    legacy.write_text("a file where the legacy root would be")
    migration = boardsync.migrate(sessions, legacy)

    assert migration.entries == () and migration.unlisted is not None
    assert "NotADirectoryError" in migration.unlisted
    assert not sessions.exists()


def test_the_move_lists_the_legacy_root_once_and_reads_no_record(
    sessions: Path, legacy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    It runs before the window exists, on the thread that will draw it, over every
    log the operator has: its cost is the number of sessions, not their length.
    """
    for n in range(3):
        _legacy_log(legacy, f"s-{n}")
    listed: list[str] = []
    opened: list[str] = []
    real_listdir, real_open = os.listdir, os.open

    def listdir(path: str | Path) -> list[str]:
        listed.append(str(path))
        return real_listdir(path)

    def opening(path: str | Path, *args: int, **kwargs: int) -> int:
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "listdir", listdir)
    monkeypatch.setattr(os, "open", opening)
    for name in ("read", "next_ordinal", "decode", "restore_log"):
        monkeypatch.setattr(boardlog, name, lambda *a, **k: pytest.fail("a log was read"))

    migration = boardsync.migrate(sessions, legacy)

    assert len(migration.moved) == 3
    assert listed == [str(legacy)]
    assert opened == []


# -- the move under a writer that does not know of it ----------------------------------


def test_an_append_that_remakes_the_old_directory_before_the_link_is_two_histories_and_said(
    sessions: Path, legacy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The one interval the link does not cover, forced: another pptmstr's append by
    the old name lands after the rename and before the link. It is told
    ``Written``; this build is told by the link failing, and by every read after.
    """
    old = _legacy_log(legacy, "s-1")
    new = sessions / "s-1" / "board"
    real = os.symlink
    theirs: list[Written | Unwritten] = []

    def after_the_other_writer(target: str, source: Path) -> None:
        theirs.append(boardlog.append(old, Completed(2.0, "t0", "builder", "done")))
        real(target, source)

    monkeypatch.setattr(os, "symlink", after_the_other_writer)

    (moved,) = boardsync.migrate(sessions, legacy).entries

    assert isinstance(theirs[0], Written) and theirs[0].ordinal == 0, "a fresh log, reported kept"
    assert isinstance(moved, Moved)
    assert moved.unlinked is not None and "FileExistsError" in moved.unlinked
    assert len(boardlog.read(new).records) == 2 and len(boardlog.read(old).records) == 1

    found = boardsync.find("s-1", sessions, legacy)
    assert (found.directory, found.second) == (new, old)
    assert found.unrecorded is not None and "two places" in found.unrecorded
    # The new path reads whole: no damage, nothing short. Only ``unrecorded`` says it.
    assert isinstance(found.board, Board) and not found.board.short
    writer = BoardWriter(sessions, legacy)
    writer.adopt("s-1")
    writer.submit("s-1", _declared(7))
    assert not any(o.kept for o in _outcomes(writer.close(timeout=10)))
    assert len(boardlog.read(new).records) == 2
    # And the next start moves nothing onto either.
    (left,) = boardsync.migrate(sessions, legacy).entries
    assert isinstance(left, Left) and left.twice


@pytest.mark.parametrize("round_", range(12))
def test_a_move_under_a_thread_appending_by_the_old_name_never_leaves_a_short_log_adoptable(
    sessions: Path, legacy: Path, round_: int
) -> None:
    """
    Another pptmstr, as a thread: it appends by ``boards/<id>`` before, during and
    after the move and keeps every answer it was given. Whatever the timing, either
    every record it was told is ``Written`` is in the one log this build would
    append to, or ``find`` says the session is not to be appended to.
    """
    old = legacy / "s-1"
    assert isinstance(boardlog.append(old, _declared(0)), Written)
    told: list[tuple[Declared, Written | Unwritten]] = []
    started, stop = threading.Event(), threading.Event()

    def older_build() -> None:
        n = 1
        while not stop.is_set() and n < 4000:
            record = _declared(n)
            told.append((record, boardlog.append(old, record, durable=False)))
            n += 1
            if n > 5 + round_:
                started.set()

    thread = threading.Thread(target=older_build)
    thread.start()
    try:
        assert started.wait(10)
        (moved,) = boardsync.migrate(sessions, legacy).entries
        after = len(told)
        deadline = time.monotonic() + 10
        while len(told) < after + 20 and time.monotonic() < deadline:
            time.sleep(0.0005)
    finally:
        stop.set()
        thread.join(10)
    assert not thread.is_alive() and len(told) >= after + 20

    assert isinstance(moved, Moved)
    kept = [record for record, result in told if isinstance(result, Written)]
    found = boardsync.find("s-1", sessions, legacy)
    assert isinstance(found.board, Board)
    restored = {task.id for task in found.board.tasks}
    if found.unrecorded is None:
        assert moved.unlinked is None and old.is_symlink()
        assert found.directory == sessions / "s-1" / "board"
        assert restored == {"t0"} | {record.task_id for record in kept}
        assert not found.board.short
    else:
        assert moved.unlinked is not None and found.second == old
    # Either way nothing it was told is on disk has gone.
    on_disk = set()
    for place in {sessions / "s-1" / "board", old.resolve()}:
        on_disk |= {r.task_id for r in boardlog.read(place).records if isinstance(r, Declared)}
    assert {record.task_id for record in kept} <= on_disk
