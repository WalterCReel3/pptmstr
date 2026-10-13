"""
The board log: records as text, the append that cannot overwrite, the strict read,
and the fold that restores a board.

Driven against a real directory, because the properties that matter are properties
of the filesystem primitive: that a name appears whole or not at all, and that a
second writer cannot take a name the first one has.

Mostly invariants over generated sequences rather than examples. The generator is
seeded ``random`` so a failure names a seed that reproduces it.
"""

from __future__ import annotations

import dataclasses
import inspect
import os
import random
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import get_args

import pytest

from pptmstr import boardlog
from pptmstr.boardlog import (
    Amended,
    Board,
    Claimed,
    Clock,
    Closed,
    Completed,
    Damage,
    Declared,
    Delivered,
    Edited,
    Handed,
    Malformed,
    Posted,
    Reclaimed,
    Record,
    RecordingStopped,
    Released,
    Unrecognised,
    Unwritten,
    Withdrawn,
    WritesAccrued,
    Written,
)
from pptmstr.model import ApprovedWrites, Concern, ConcernState, Task, TaskState

# -- generated records ------------------------------------------------------------

# What agent prose actually contains, plus what breaks a careless codec: the
# newline that is also the terminator, a brace pair that looks like a record
# boundary, a lone surrogate UTF-8 will not encode, and a body past PIPE_BUF.
_TEXTS = (
    "",
    "plain",
    "two\nlines\n",
    'a "quoted" \\ backslash',
    "café 日本語 \U0001f600",
    "lone \ud800 surrogate",
    "nul \x00 and \r\n",
    '}\n{"kind": "task-claimed"}',
    "x" * 70_000,
)
_ADDRESSES = ("lead", "builder", "builder-2", "reviewer")
# A party's node, as a record holds one: unrecorded, a root, a sub-agent. Few
# enough that an address meets a node it has met before, and a different one.
_NODES: tuple[tuple[str, ...], ...] = ((), (), ("s",), ("s", "a1"), ("s", "a2"), ("z", "a1"))
_TASK_IDS = ("t-1", "t-2", "t-3", "final-review")
_CONCERN_IDS = ("c-1", "c-2", "c-3")
_TIMES = (0.0, 1.5, -3.25, 1_760_000_000.123456, 1e-300, 4e18)
# Empty is "none stated" on a declaration and "withdrawn" on an amendment.
_PROPERTIES = ("", "a restored board is never short a fact", "café \ud800 stays put")


def _text(rng: random.Random) -> str:
    return rng.choice(_TEXTS)


def _maybe(rng: random.Random, value: str) -> str | None:
    return value if rng.random() < 0.5 else None


def _some(rng: random.Random, pool: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(rng.choice(pool) for _ in range(rng.randrange(0, 4)))


def _future(rng: random.Random) -> Unrecognised:
    """A record of a kind a later build defines, as ``decode`` would hand it back."""
    at = rng.choice(_TIMES)
    kind = rng.choice(("decision-raised", "decision-answered", "request-referenced"))
    text = f'{{"kind": "{kind}", "at": {at!r}, "number": {rng.randrange(9)}}}'
    return Unrecognised(at=at, kind=kind, text=text)


_GENERATORS: dict[type, Callable[[random.Random], Record]] = {
    Declared: lambda r: Declared(
        at=r.choice(_TIMES),
        task_id=r.choice(_TASK_IDS),
        title=_text(r),
        declared_by=r.choice(_ADDRESSES),
        detail=_text(r),
        depends_on=_some(r, _TASK_IDS),
        touches=_some(r, ("a.py", "pkg/b.py", "café.md")),
        basis=_maybe(r, "read"),
        property=r.choice(_PROPERTIES),
        declared_by_id=r.choice(_NODES),
    ),
    Claimed: lambda r: Claimed(
        at=r.choice(_TIMES),
        task_id=r.choice(_TASK_IDS),
        by=r.choice(_ADDRESSES),
        by_id=r.choice(_NODES),
    ),
    Released: lambda r: Released(
        at=r.choice(_TIMES),
        task_id=r.choice(_TASK_IDS),
        by=r.choice(_ADDRESSES),
        by_id=r.choice(_NODES),
    ),
    Reclaimed: lambda r: Reclaimed(
        at=r.choice(_TIMES),
        task_id=r.choice(_TASK_IDS),
        by=r.choice(_ADDRESSES),
        holder=r.choice(_ADDRESSES),
        by_id=r.choice(_NODES),
        holder_id=r.choice(_NODES),
    ),
    Handed: lambda r: Handed(
        at=r.choice(_TIMES),
        task_id=r.choice(_TASK_IDS),
        by=r.choice(_ADDRESSES),
        version=r.randrange(0, 4),
        by_id=r.choice(_NODES),
    ),
    Completed: lambda r: Completed(
        at=r.choice(_TIMES),
        task_id=r.choice(_TASK_IDS),
        by=r.choice(_ADDRESSES),
        result=_text(r),
        basis=_maybe(r, "run"),
        not_run=r.choice((None, "", "the cross presets", _text(r))),
        by_id=r.choice(_NODES),
    ),
    Closed: lambda r: Closed(at=r.choice(_TIMES), task_id=r.choice(_TASK_IDS), reason=_text(r)),
    Amended: lambda r: Amended(
        at=r.choice(_TIMES),
        task_id=r.choice(_TASK_IDS),
        detail=_text(r),
        by=_maybe(r, r.choice(_ADDRESSES)),
        property=r.choice((None, *_PROPERTIES)),
        basis=_maybe(r, "relayed"),
        by_id=r.choice(_NODES),
    ),
    WritesAccrued: lambda r: WritesAccrued(
        at=r.choice(_TIMES),
        task_id=r.choice(_TASK_IDS),
        paths=_some(r, ("a.py", "pkg/b.py")),
        unplaced=_some(r, ("/etc/hosts",)),
        lines_added=r.randrange(0, 500),
        lines_removed=r.randrange(0, 500),
    ),
    Posted: lambda r: Posted(
        at=r.choice(_TIMES),
        concern_id=r.choice(_CONCERN_IDS),
        sender=r.choice(_ADDRESSES),
        recipient=r.choice(_ADDRESSES),
        subject=_text(r),
        body=_text(r),
        task_id=_maybe(r, r.choice(_TASK_IDS)),
        basis=_maybe(r, "relayed"),
        incident=r.random() < 0.5,
        sender_id=r.choice(_NODES),
        recipient_id=r.choice(_NODES),
    ),
    Edited: lambda r: Edited(at=r.choice(_TIMES), concern_id=r.choice(_CONCERN_IDS), body=_text(r)),
    Withdrawn: lambda r: Withdrawn(at=r.choice(_TIMES), concern_id=r.choice(_CONCERN_IDS)),
    Delivered: lambda r: Delivered(at=r.choice(_TIMES), concern_ids=_some(r, _CONCERN_IDS)),
    RecordingStopped: lambda r: RecordingStopped(at=r.choice(_TIMES), reason=_text(r)),
    Unrecognised: _future,
}


def sequence(seed: int, length: int = 40) -> tuple[Record, ...]:
    """
    ``length`` records of every kind in no sensible order.

    Deliberately not a plausible session. Ids come from small pools, so the
    sequence is full of claims before declarations, second declarations and marks
    on concerns nobody posted -- the inputs a fold must be total over.
    """
    rng = random.Random(seed)
    kinds = list(_GENERATORS)
    return tuple(_GENERATORS[rng.choice(kinds)](rng) for _ in range(length))


def write(directory: Path, records: tuple[Record, ...]) -> None:
    for record in records:
        # Not durable: the flush to the device is the one line here that does not
        # change what a reader sees, and it is most of the suite's runtime.
        written = boardlog.append(directory, record, durable=False)
        assert isinstance(written, Written), written


SEEDS = range(60)


# -- the vocabulary ---------------------------------------------------------------


def test_every_record_kind_is_generated_so_a_new_one_cannot_skip_the_round_trip() -> None:
    """
    The guard the module docstring promises. A kind added to ``Record`` and not to
    the generator would persist, restore, and be exercised by nothing here.
    """
    assert set(_GENERATORS) == set(get_args(Record))

    produced = {type(r) for seed in SEEDS for r in sequence(seed)}
    assert produced == set(_GENERATORS)


def test_the_generator_reaches_every_value_a_stated_field_can_take() -> None:
    """
    The round trip covers a field only as far as the generator varies it, and
    the three-valued ones are where a codec that folds two values together hides.
    """
    records = [r for seed in SEEDS for r in sequence(seed)]

    not_run = {r.not_run for r in records if isinstance(r, Completed)}
    assert {None, "", "the cross presets"} <= not_run
    amended = {r.property for r in records if isinstance(r, Amended)}
    assert {None, *_PROPERTIES} <= amended
    assert {r.property for r in records if isinstance(r, Declared)} == set(_PROPERTIES)
    assert {r.incident for r in records if isinstance(r, Posted)} == {True, False}


def test_kind_names_are_distinct() -> None:
    defined = [cls for cls in get_args(Record) if cls is not Unrecognised]
    assert len({cls.KIND for cls in defined}) == len(defined)
    assert {cls.KIND: cls for cls in defined} == boardlog._KINDS


def test_the_stop_marker_is_a_record_and_not_a_board_fact() -> None:
    """
    ``Known`` is what a reducer step can yield, and ``boardfacts`` is held to
    deriving every member of it. No intent stops a recording.
    """
    assert RecordingStopped in get_args(Record)
    assert RecordingStopped not in get_args(boardlog.Known)


# -- one record as text -----------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_a_record_survives_its_own_text(seed: int) -> None:
    for record in sequence(seed):
        assert boardlog.decode(boardlog.encode(record)) == record


@pytest.mark.parametrize("seed", range(12))
def test_no_proper_prefix_of_a_record_reads_as_a_record(seed: int) -> None:
    """
    Half a record is never a whole one, at every cut and not only the obvious ones.

    The cut one byte short is the one that matters: it is a complete JSON object
    missing only its terminator.
    """
    rng = random.Random(seed)
    for record in sequence(seed, length=12):
        text = boardlog.encode(record)
        cuts = (
            range(len(text))
            if len(text) < 400
            else [0, 1, len(text) - 2, len(text) - 1, *rng.sample(range(len(text)), 40)]
        )
        for cut in cuts:
            assert isinstance(boardlog.decode(text[:cut]), Malformed), (record, cut)


def test_a_field_added_later_reads_from_a_log_written_before_it() -> None:
    old = '{"kind": "task-completed", "at": 2.0, "task_id": "t-1", "by": "builder"}\n'

    assert boardlog.decode(old) == Completed(at=2.0, task_id="t-1", by="builder")


def test_a_record_written_before_a_fact_was_logged_restores_it_as_unstated() -> None:
    """
    None of the keys below existed when these were written. Each must come back as
    the value that claims nothing -- above all a completion with no ``not_run``,
    which is unsaid and not the statement that everything was run.
    """
    old = (
        '{"kind": "task-declared", "at": 1.0, "task_id": "t-1", "title": "T",'
        ' "declared_by": "lead", "detail": "spec"}\n',
        '{"kind": "task-claimed", "at": 2.0, "task_id": "t-1", "by": "builder"}\n',
        '{"kind": "task-amended", "at": 3.0, "task_id": "t-1", "detail": "spec 2"}\n',
        '{"kind": "task-completed", "at": 4.0, "task_id": "t-1", "by": "builder",'
        ' "result": "done"}\n',
        '{"kind": "concern-posted", "at": 5.0, "concern_id": "c-1", "sender": "builder",'
        ' "recipient": "lead", "subject": "s", "body": "b"}\n',
    )
    records = [boardlog.decode(text) for text in old]
    assert not any(isinstance(r, Malformed) for r in records)

    board = boardlog.restore(r for r in records if not isinstance(r, Malformed))

    (task,) = board.tasks
    assert task.not_run is None
    assert task.not_run != ""
    assert task.property == ""
    assert task.released_at is None
    assert [v.property for v in task.versions] == ["", ""]
    (concern,) = board.concerns
    assert concern.incident is False


def test_unsaid_and_nothing_left_unrun_are_two_texts_on_disk() -> None:
    """The distinction the type keeps has to survive being written down."""
    unsaid = Completed(at=1.0, task_id="t", by="b", result="r", not_run=None)
    all_run = Completed(at=1.0, task_id="t", by="b", result="r", not_run="")

    assert boardlog.encode(unsaid) != boardlog.encode(all_run)
    assert boardlog.decode(boardlog.encode(unsaid)) == unsaid
    assert boardlog.decode(boardlog.encode(all_run)) == all_run


def test_a_key_this_build_does_not_know_does_not_cost_the_record() -> None:
    newer = '{"kind": "task-claimed", "at": 2.0, "task_id": "t-1", "by": "builder", "lease": 30}\n'

    assert boardlog.decode(newer) == Claimed(at=2.0, task_id="t-1", by="builder")


@pytest.mark.parametrize(
    "text",
    [
        '{"kind": "task-claimed", "at": 2.0, "task_id": "t-1"}\n',  # no `by`
        '{"kind": "task-claimed", "at": 2.0, "task_id": 7, "by": "b"}\n',
        '{"kind": "task-claimed", "at": 2.0, "task_id": null, "by": "b"}\n',
        '{"kind": "task-claimed", "at": "2.0", "task_id": "t-1", "by": "b"}\n',
        '{"kind": "task-claimed", "at": true, "task_id": "t-1", "by": "b"}\n',
        '{"kind": "task-claimed", "at": NaN, "task_id": "t-1", "by": "b"}\n',
        '{"kind": "task-claimed", "at": Infinity, "task_id": "t-1", "by": "b"}\n',
        '{"kind": "task-claimed", "task_id": "t-1", "by": "b"}\n',
        '{"at": 2.0, "task_id": "t-1", "by": "b"}\n',
        '{"kind": 3, "at": 2.0}\n',
        '{"kind": "decision-raised"}\n',  # a future kind still owes a time
        '{"kind": "task-writes-accrued", "at": 2.0, "task_id": "t", "lines_added": true}\n',
        '{"kind": "task-writes-accrued", "at": 2.0, "task_id": "t", "lines_added": 1.5}\n',
        '{"kind": "task-writes-accrued", "at": 2.0, "task_id": "t", "paths": "a.py"}\n',
        '{"kind": "task-writes-accrued", "at": 2.0, "task_id": "t", "paths": ["a.py", 3]}\n',
        '{"kind": "concern-posted", "at": 1.0, "concern_id": "c", "sender": "a",'
        ' "recipient": "b", "subject": "s", "body": "b", "task_id": 4}\n',
        '{"kind": "concern-posted", "at": 1.0, "concern_id": "c", "sender": "a",'
        ' "recipient": "b", "subject": "s", "body": "b", "incident": 1}\n',
        '{"kind": "concern-posted", "at": 1.0, "concern_id": "c", "sender": "a",'
        ' "recipient": "b", "subject": "s", "body": "b", "incident": "true"}\n',
        '{"kind": "concern-posted", "at": 1.0, "concern_id": "c", "sender": "a",'
        ' "recipient": "b", "subject": "s", "body": "b", "incident": null}\n',
        '{"kind": "task-completed", "at": 2.0, "task_id": "t", "by": "b", "not_run": false}\n',
        '{"kind": "task-declared", "at": 2.0, "task_id": "t", "title": "T",'
        ' "declared_by": "lead", "property": null}\n',
        '["task-claimed"]\n',
        "null\n",
        "\n",
        "",
    ],
)
def test_text_that_is_not_a_record_is_refused_with_a_reason(text: str) -> None:
    refused = boardlog.decode(text)

    assert isinstance(refused, Malformed)
    assert refused.reason


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
    refused = boardlog.decode(text)
    assert isinstance(refused, Malformed) and refused.reason

    write(tmp_path, (A,))
    (tmp_path / "00000001.json").write_text(text)
    write(tmp_path, (B,))

    log = boardlog.read(tmp_path)
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
        os.write(feed, boardlog.encode(B).encode())
    got: list[boardlog.Log] = []
    reader = threading.Thread(target=lambda: got.append(boardlog.read(tmp_path)), daemon=True)
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
    monkeypatch.setattr(boardlog, "MAX_RECORD_BYTES", len(boardlog.encode(fits)))
    write(tmp_path, (fits,))
    over = boardlog.encode(dataclasses.replace(fits, by="b" * 41))
    assert boardlog.decode(over) == dataclasses.replace(fits, by="b" * 41)
    (tmp_path / "00000001.json").write_text(over)
    (tmp_path / "00000002.json").write_text(boardlog.encode(fits))

    log = boardlog.read(tmp_path)

    assert log.records == (fits,)
    assert log.damage is not None
    assert (log.damage.ordinal, log.damage.unread) == (1, 1)
    assert "over the" in log.damage.reason


def test_the_bound_on_a_record_is_the_one_the_request_files_use() -> None:
    """
    Two modules, one figure, for one reason. Spelled twice so neither imports the
    other, and pinned here so they do not drift.
    """
    from pptmstr import requests

    assert boardlog.MAX_RECORD_BYTES == requests.MAX_RECORD_BYTES == 1 << 20


def test_an_integer_time_is_a_time() -> None:
    """JSON does not distinguish ``2`` from ``2.0``, and another writer may not either."""
    record = boardlog.decode('{"kind": "concern-withdrawn", "at": 2, "concern_id": "c"}\n')

    assert record == Withdrawn(at=2.0, concern_id="c")
    assert isinstance(record, Withdrawn) and isinstance(record.at, float)


# -- the round trip ---------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_what_is_read_is_what_was_written_and_restores_to_the_same_board(
    seed: int, tmp_path: Path
) -> None:
    """
    ``restore(read(write(records))) == restore(records)``, by way of the stronger
    claim that the records themselves come back equal and in order.
    """
    records = sequence(seed)
    write(tmp_path / "log", records)

    log = boardlog.read(tmp_path / "log")

    assert log.damage is None
    assert log.records == records
    assert boardlog.restore(log.records) == boardlog.restore(records)


def test_the_durable_write_reads_back_the_same(tmp_path: Path) -> None:
    records = sequence(3, length=8)
    for record in records:
        assert isinstance(boardlog.append(tmp_path, record), Written)

    assert boardlog.read(tmp_path) == boardlog.Log(records=records)


# -- the append -------------------------------------------------------------------

A = Claimed(at=1.0, task_id="t-1", by="builder")
B = Claimed(at=2.0, task_id="t-1", by="builder-2")


def names(directory: Path) -> list[str]:
    return sorted(os.listdir(directory))


def test_a_listing_that_went_stale_before_the_write_does_not_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The two-writer hazard, constructed rather than raced: another writer lands
    between this append's listing and its link, so the listing it acts on shows
    an empty directory that is not one.
    """
    assert boardlog.append(tmp_path, A) == Written(0, tmp_path / "00000000.json")
    stale: list[str] = []
    monkeypatch.setattr(os, "listdir", lambda directory: stale)

    second = boardlog.append(tmp_path, B)
    monkeypatch.undo()

    assert second == Written(1, tmp_path / "00000001.json")
    assert boardlog.read(tmp_path).records == (A, B)


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
            written = boardlog.append(
                tmp_path, Claimed(at=float(n), task_id="t", by=f"w{who}"), durable=False
            )
            if not isinstance(written, Written):
                failures.append(written)
                return

    threads = [threading.Thread(target=run, args=(who,)) for who in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    log = boardlog.read(tmp_path)
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
who, directory, each = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3])
for n in range(each):
    record = boardlog.Claimed(at=float(n), task_id="t", by=who)
    w = boardlog.append(directory, record, durable=False)
    assert isinstance(w, boardlog.Written), w
"""


def test_processes_appending_at_once_lose_nothing(tmp_path: Path) -> None:
    """
    Two windows resuming one session id are two processes, not two threads, and
    each of these lists the directory per append the way a writer with no hint
    does.
    """
    writers, each = 3, 60
    root = str(Path(boardlog.__file__).resolve().parents[1])
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _WRITER, f"w{who}", str(tmp_path), str(each)],
            cwd=root,
            env={**os.environ, "PYTHONPATH": root},
        )
        for who in range(writers)
    ]
    assert [proc.wait(timeout=120) for proc in procs] == [0] * writers

    log = boardlog.read(tmp_path)
    assert log.damage is None
    for who in range(writers):
        mine = [r.at for r in log.records if isinstance(r, Claimed) and r.by == f"w{who}"]
        assert mine == [float(n) for n in range(each)]


def test_an_append_leaves_nothing_behind_but_the_record(tmp_path: Path) -> None:
    boardlog.append(tmp_path, A)
    boardlog.append(tmp_path, B)

    assert names(tmp_path) == ["00000000.json", "00000001.json"]


def test_an_append_cannot_be_told_where_to_start(tmp_path: Path) -> None:
    """
    The hazard: a writer handed a floor below a missing ordinal takes the first
    free name above it, which is the missing record's. A break ``read`` reported
    then reads as a whole log with a newer record ahead of older ones. There is
    no floor to hand over, and a caller that tries is refused loudly.
    """
    assert list(inspect.signature(boardlog.append).parameters) == [
        "directory",
        "record",
        "durable",
    ]
    write(tmp_path, (A, B, A))
    (tmp_path / "00000001.json").unlink()

    with pytest.raises(TypeError):
        boardlog.append(tmp_path, B, after=0)  # type: ignore[call-arg]

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
    before = boardlog.read(tmp_path)
    assert before.records == records[:readable] and before.damage is not None

    first = boardlog.append(tmp_path, B, durable=False)
    second = boardlog.append(tmp_path, A, durable=False)

    assert isinstance(first, Written) and (first.ordinal, first.gap) == (5, gap)
    assert isinstance(second, Written) and (second.ordinal, second.gap) == (6, gap)
    assert all(not (tmp_path / f"{ordinal:08d}.json").exists() for ordinal in lost)
    after = boardlog.read(tmp_path)
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
    monkeypatch.setattr(boardlog, "MAX_RECORD_BYTES", len(boardlog.encode(at_the_bound)))
    over = dataclasses.replace(at_the_bound, by="b" * 41)

    assert isinstance(boardlog.append(tmp_path, at_the_bound), Written)
    refused = boardlog.append(tmp_path, over)

    assert isinstance(refused, Unwritten) and "over the" in refused.reason
    assert names(tmp_path) == ["00000000.json"]
    assert boardlog.read(tmp_path) == boardlog.Log(records=(at_the_bound,))


@pytest.mark.parametrize("durable", [True, False])
def test_a_record_is_flushed_to_the_device_before_it_is_named_only_when_asked(
    durable: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Before, not merely at some point: a name over text the device does not hold is
    the torn record a durable write exists to rule out.
    """
    whole = len(boardlog.encode(A).encode())
    synced: list[tuple[bool, int]] = []

    def fsync(fd: int) -> None:
        # What the kernel holds for the file at this moment. Bytes still in the
        # process's own buffer are not in it, and syncing ahead of them is a sync
        # of nothing.
        synced.append((names(tmp_path) != ["00000000.json"], os.fstat(fd).st_size))

    monkeypatch.setattr(os, "fsync", fsync)

    if durable:
        assert isinstance(boardlog.append(tmp_path, A), Written)
    else:
        assert isinstance(boardlog.append(tmp_path, A, durable=False), Written)

    assert synced == ([(True, whole)] if durable else [])
    assert boardlog.read(tmp_path).records == (A,)


def test_an_append_into_a_whole_log_reports_no_gap(tmp_path: Path) -> None:
    for ordinal, record in enumerate((A, B, A)):
        written = boardlog.append(tmp_path, record)
        assert isinstance(written, Written) and (written.ordinal, written.gap) == (ordinal, None)


def test_an_append_behind_a_missing_record_says_it_will_not_be_read_back(tmp_path: Path) -> None:
    """
    The hazard: a writer told ``Written`` forever while every restore stops at the
    gap. The listing an append already makes shows the gap, at no read.
    """
    write(tmp_path, (A, B, A))
    (tmp_path / "00000001.json").unlink()

    written = boardlog.append(tmp_path, B)

    assert isinstance(written, Written)
    assert (written.ordinal, written.gap) == (3, 1)
    log = boardlog.read(tmp_path)
    assert log.records == (A,) and log.damage == Damage(1, "missing", 2)


def test_a_name_the_reader_ignores_does_not_move_where_the_writer_appends(tmp_path: Path) -> None:
    """
    ``read`` takes only the canonical spelling of an ordinal. A writer that counted
    another spelling -- nine digits for 99 -- would put every later record past a
    gap that nothing but that stray name made.
    """
    write(tmp_path, (A,))
    (tmp_path / "000000099.json").write_text(boardlog.encode(B))

    assert boardlog.next_ordinal(tmp_path) == 1
    written = boardlog.append(tmp_path, B)

    assert isinstance(written, Written) and (written.ordinal, written.gap) == (1, None)
    assert boardlog.read(tmp_path) == boardlog.Log(records=(A, B))


def test_the_directory_is_made_on_the_first_append(tmp_path: Path) -> None:
    directory = boardlog.session_dir(tmp_path / "not" / "yet", "sess-1")

    assert directory is not None
    assert isinstance(boardlog.append(directory, A), Written)
    assert boardlog.read(directory).records == (A,)


def test_a_directory_that_cannot_be_made_is_a_value(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("in the way")

    refused = boardlog.append(tmp_path / "file" / "log", A)

    assert isinstance(refused, Unwritten)
    assert "NotADirectoryError" in refused.reason


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes through a read-only mode")
def test_a_directory_that_cannot_be_written_is_a_value(tmp_path: Path) -> None:
    directory = tmp_path / "log"
    directory.mkdir()
    directory.chmod(0o500)
    try:
        refused = boardlog.append(directory, A)
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

    assert isinstance(boardlog.append(tmp_path, A), Unwritten)
    assert names(tmp_path) == []


@pytest.mark.parametrize("at", [float("nan"), float("inf")])
def test_a_record_that_cannot_be_encoded_is_a_value_and_writes_nothing(
    at: float, tmp_path: Path
) -> None:
    refused = boardlog.append(tmp_path, Claimed(at=at, task_id="t", by="b"))

    assert isinstance(refused, Unwritten)
    assert not tmp_path.joinpath("00000000.json").exists()


def test_the_next_ordinal_is_past_the_highest_name_and_past_the_padding(tmp_path: Path) -> None:
    assert boardlog.next_ordinal(tmp_path / "absent") == 0
    assert boardlog.next_ordinal(tmp_path) == 0

    (tmp_path / "00000009.json").write_text("")
    (tmp_path / "00000010.json").write_text("")
    (tmp_path / ".record-abc.tmp").write_text("")
    (tmp_path / "99999999999.txt").write_text("")
    assert boardlog.next_ordinal(tmp_path) == 11

    (tmp_path / "99999999.json").write_text("")
    (tmp_path / "100000000.json").write_text("")
    assert boardlog.next_ordinal(tmp_path) == 100_000_001


def test_a_directory_that_cannot_be_listed_is_not_one_with_no_record_in_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Three failures that are not absence, each with records the listing would have
    shown or could not rule out. Zero for any of them is "a log starts here".
    """
    (tmp_path / "a-file").write_text("where a directory would be")
    in_the_way = boardlog.next_ordinal(tmp_path / "a-file")
    assert isinstance(in_the_way, boardlog.Unlisted) and "NotADirectoryError" in in_the_way.reason

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
        unlisted = boardlog.next_ordinal(tmp_path / "log")
        assert isinstance(unlisted, boardlog.Unlisted), error
        assert type(error).__name__ in unlisted.reason
        assert str(error.strerror) in unlisted.reason

    monkeypatch.setattr(os, "listdir", real)
    assert boardlog.next_ordinal(tmp_path / "log") == 1


def test_a_session_is_keyed_by_its_id_and_nothing_else(tmp_path: Path) -> None:
    assert boardlog.session_dir(tmp_path, "sess-1") == tmp_path / "sess-1"
    uuid = "0958795c-3ae6-423c-99bb-584c17486446"
    assert boardlog.session_dir(tmp_path, uuid) == tmp_path / uuid


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
    assert boardlog.session_dir(tmp_path, session_id) is None


# -- the read ---------------------------------------------------------------------


def test_a_missing_directory_is_an_empty_log(tmp_path: Path) -> None:
    assert boardlog.read(tmp_path / "absent") == boardlog.Log()


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads through an unreadable mode")
def test_a_directory_that_cannot_be_listed_does_not_read_as_an_empty_board(tmp_path: Path) -> None:
    directory = tmp_path / "log"
    write(directory, (A,))
    directory.chmod(0o000)
    try:
        log = boardlog.read(directory)
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
    half = boardlog.encode(A)[:10]
    (tmp_path / ".record-x7.tmp").write_text(half)
    (tmp_path / "notes.md").write_text("not ours")
    (tmp_path / "0000000002.json").write_text(boardlog.encode(A))  # not the canonical name
    (tmp_path / "2.json").write_text(boardlog.encode(A))
    (tmp_path / "00000002.json.bak").write_text(boardlog.encode(A))
    # Digits of another script: ``int`` reads them as 2 and no append writes them.
    (tmp_path / "\u0660\u0660\u0660\u0660\u0660\u0660\u0660\u0662.json").write_text(
        boardlog.encode(A)
    )
    assert boardlog.next_ordinal(tmp_path) == 2

    assert boardlog.read(tmp_path) == boardlog.Log(records=(A, B))


@pytest.mark.parametrize("seed", range(10))
def test_a_record_torn_anywhere_ends_the_log_there_and_keeps_what_came_before(
    seed: int, tmp_path: Path
) -> None:
    """
    The prefix property, for a tear at any record and any byte: everything before
    the torn file is returned, nothing after it is, and the report counts what was
    withheld.
    """
    rng = random.Random(seed)
    records = sequence(seed, length=12)
    write(tmp_path, records)
    torn = rng.randrange(len(records))
    path = tmp_path / f"{torn:08d}.json"
    whole = path.read_bytes()
    path.write_bytes(whole[: rng.randrange(len(whole))])

    log = boardlog.read(tmp_path)

    assert log.records == records[:torn]
    assert log.damage is not None
    assert (log.damage.ordinal, log.damage.unread) == (torn, len(records) - torn - 1)
    assert boardlog.restore(log.records) == boardlog.restore(records[:torn])


def test_a_torn_last_record_is_reported_with_nothing_withheld(tmp_path: Path) -> None:
    write(tmp_path, (A, B))
    (tmp_path / "00000001.json").write_bytes(b"")

    assert boardlog.read(tmp_path) == boardlog.Log(
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

    assert boardlog.read(tmp_path) == boardlog.Log(records=(A,), damage=Damage(1, "missing", 2))


def test_a_log_missing_its_first_record_yields_nothing(tmp_path: Path) -> None:
    write(tmp_path, (A, B))
    (tmp_path / "00000000.json").unlink()

    assert boardlog.read(tmp_path) == boardlog.Log(damage=Damage(0, "missing", 1))


def test_bytes_that_are_not_text_are_damage_not_an_exception(tmp_path: Path) -> None:
    write(tmp_path, (A, B))
    (tmp_path / "00000001.json").write_bytes(b"\xff\xfe\n")

    log = boardlog.read(tmp_path)

    assert log.records == (A,)
    assert log.damage is not None and log.damage.ordinal == 1


def test_records_are_read_in_ordinal_order_not_listing_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = sequence(5, length=12)
    write(tmp_path, records)
    listing = sorted(os.listdir(tmp_path))
    monkeypatch.setattr(os, "listdir", lambda directory: listing[::-1])
    assert boardlog.read(tmp_path).records == records
    monkeypatch.setattr(os, "listdir", lambda directory: listing[5:] + listing[:5])
    assert boardlog.read(tmp_path).records == records


# -- a kind from a later build ----------------------------------------------------


def test_a_kind_this_build_does_not_define_is_carried_and_what_follows_still_applies(
    tmp_path: Path,
) -> None:
    declared = Declared(at=1.0, task_id="t-1", title="T", declared_by="lead")
    write(tmp_path, (declared,))
    text = '{"kind": "decision-raised", "at": 5.0, "number": 1, "question": "which root?"}'
    (tmp_path / "00000001.json").write_text(text + "\n")
    assert isinstance(boardlog.append(tmp_path, A), Written)

    log = boardlog.read(tmp_path)
    future = Unrecognised(at=5.0, kind="decision-raised", text=text)

    assert log == boardlog.Log(records=(declared, future, A))
    board = boardlog.restore(log.records)
    assert board.unrecognised == (future,)
    assert board.stray == ()
    assert board.tasks[0].holder == "builder"
    assert boardlog.encode(future) == text + "\n"


# -- a log whose writer stopped ---------------------------------------------------


def _held() -> tuple[Record, ...]:
    return (
        Declared(at=1.0, task_id="t-1", title="T", declared_by="lead"),
        Claimed(at=2.0, task_id="t-1", by="builder"),
    )


def test_a_stop_marker_makes_the_board_short_and_changes_nothing_on_it() -> None:
    marker = RecordingStopped(at=3.0, reason="1048600 bytes, over the 1048576 a record may be")

    whole = boardlog.restore(_held())
    stopped = boardlog.restore((*_held(), marker))

    assert not whole.short and whole.stopped == ()
    assert stopped.short and stopped.stopped == (marker,)
    assert dataclasses.replace(stopped, stopped=()) == whole
    assert not (stopped.stray or stopped.unrecognised or stopped.contested)


def test_records_after_a_stop_marker_are_applied_and_the_board_stays_short() -> None:
    marker = RecordingStopped(at=3.0, reason="disk full")
    later = Completed(at=4.0, task_id="t-1", by="builder", result="done")

    board = boardlog.restore((*_held(), marker, later))

    assert board.tasks[0].state is TaskState.COMPLETED and not board.tasks[0].contested
    assert board.short and board.stopped == (marker,)


def test_a_stop_marker_read_from_disk_is_carried_by_the_board_the_log_restores(
    tmp_path: Path,
) -> None:
    marker = RecordingStopped(at=3.0, reason="disk full")
    write(tmp_path, (*_held(), marker))

    board = boardlog.restore_log(boardlog.read(tmp_path))

    assert board.damage is None
    assert board.short and board.stopped == (marker,)


def test_a_build_older_than_the_stop_marker_reads_it_as_short_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The marker is only worth writing if every build that can read the log is
    stopped by it. One that predates the kind has no entry for its name.
    """
    text = boardlog.encode(RecordingStopped(at=3.0, reason="disk full"))
    assert '"kind": "recording-stopped"' in text, "the name is the format; it does not change"
    older = {k: v for k, v in boardlog._KINDS.items() if v is not RecordingStopped}
    assert len(older) == len(boardlog._KINDS) - 1
    monkeypatch.setattr(boardlog, "_KINDS", older)

    read = boardlog.decode(text)

    assert isinstance(read, Unrecognised) and read.kind == "recording-stopped"
    board = boardlog.restore((*_held(), read))
    assert board.short and board.unrecognised == (read,)
    assert [(t.id, t.state) for t in board.tasks] == [("t-1", TaskState.CLAIMED)]


def test_a_record_holding_many_times_what_a_board_read_shows_is_not_too_long_to_log() -> None:
    """
    A record too long to log stops its session's recording, so the bound has to
    sit well clear of an ordinary board. The bus does not bound these texts where
    it accepts them; what it shows of each in a board read is the only figure for
    "ordinary" the tree has, and this is that figure twenty-five times over in
    the characters that escape longest.
    """
    from pptmstr import bus, model

    def worst(chars: int) -> str:
        return "\U0001f600" * (chars * 25)

    heaviest = (
        Declared(
            at=1.0,
            task_id="t-1",
            title="T",
            declared_by="lead",
            detail=worst(bus._MAX_DETAIL_CHARS),
            property=worst(model.MAX_PROPERTY_CHARS),
        ),
        Completed(
            at=1.0,
            task_id="t-1",
            by="builder",
            result=worst(bus._MAX_RESULT_CHARS),
            not_run=worst(bus._MAX_NOT_RUN_CHARS),
        ),
        Posted(
            at=1.0,
            concern_id="c-1",
            sender="builder",
            recipient="lead",
            subject="s",
            body=worst(bus._MAX_CONCERN_CHARS),
        ),
    )

    for record in heaviest:
        assert len(boardlog.encode(record).encode()) <= boardlog.MAX_RECORD_BYTES, type(record)


# -- the fold ---------------------------------------------------------------------


def declared(task_id: str = "t-1", at: float = 10.0, detail: str = "") -> Declared:
    return Declared(
        at=at, task_id=task_id, title=task_id.upper(), declared_by="lead", detail=detail
    )


def posted(
    concern_id: str = "c-1",
    at: float = 10.0,
    body: str = "as posted",
    task_id: str | None = None,
    basis: str | None = None,
) -> Posted:
    return Posted(
        at=at,
        concern_id=concern_id,
        sender="builder",
        recipient="lead",
        subject="s",
        body=body,
        task_id=task_id,
        basis=basis,
    )


def test_an_empty_log_is_an_empty_board() -> None:
    assert boardlog.restore(()) == Board()


def test_a_declaration_restores_as_it_landed() -> None:
    record = Declared(
        at=10.0,
        task_id="t-1",
        title="Title",
        declared_by="lead",
        detail="spec",
        depends_on=("t-0", "auto-added"),
        touches=("a.py",),
        basis="read",
    )

    (task,) = boardlog.restore((record,)).tasks

    assert (task.id, task.title, task.declared_by) == ("t-1", "Title", "lead")
    assert (task.detail, task.depends_on, task.touches) == (
        "spec",
        ("t-0", "auto-added"),
        ("a.py",),
    )
    assert (task.basis, task.declared_at, task.version) == ("read", 10.0, 1)
    assert (task.state, task.holder, task.result) == (TaskState.PENDING, None, "")
    assert (task.completed_at, task.completed_version, task.result_basis) == (None, None, None)
    assert (task.property, task.not_run, task.released_at) == ("", None, None)


def test_a_task_claimed_and_completed_keeps_who_did_it_and_what_they_said() -> None:
    board = boardlog.restore(
        (
            declared(),
            Claimed(at=11.0, task_id="t-1", by="builder"),
            Completed(at=12.0, task_id="t-1", by="builder", result="3/3 pass", basis="run"),
        )
    )

    (task,) = board.tasks
    assert (task.state, task.holder) == (TaskState.COMPLETED, "builder")
    assert (task.result, task.result_basis) == ("3/3 pass", "run")
    assert (task.completed_at, task.completed_version) == (12.0, 1)
    assert board.stray == ()


def test_a_completion_by_an_address_that_does_not_hold_the_claim_is_not_applied() -> None:
    """
    One reducer refuses this (``store._ownership_refusal``), so it never logs it.
    A log that holds it was written by two, and the fold does not pick one.
    """
    theirs = Completed(at=12.0, task_id="t-1", by="builder-2", result="done", basis="run")
    board = boardlog.restore((declared(), Claimed(at=11.0, task_id="t-1", by="builder"), theirs))

    (task,) = board.tasks
    assert (task.state, task.holder, task.result) == (TaskState.CLAIMED, "builder", "")
    assert task.contested and board.contested == (theirs,)


_W1_CLAIM = Claimed(at=11.0, task_id="t-1", by="builder")
_W1_DONE = Completed(at=12.0, task_id="t-1", by="builder", result="3/3 pass", basis="run")
_W2_CLAIM = Claimed(at=13.0, task_id="t-1", by="reviewer")
_W2_DONE = Completed(at=14.0, task_id="t-1", by="reviewer", result="read it", basis="read")
_W2_RELEASE = Released(at=14.0, task_id="t-1", by="reviewer")


@pytest.mark.parametrize(
    ("log", "state", "holder", "result", "withheld"),
    [
        # A second window claims what the first finished.
        (
            (_W1_CLAIM, _W1_DONE, declared(detail="B"), _W2_CLAIM),
            TaskState.COMPLETED,
            "builder",
            "3/3 pass",
            (declared(detail="B"), _W2_CLAIM),
        ),
        # ... and gives it back: never PENDING with a result.
        (
            (_W1_CLAIM, _W1_DONE, _W2_CLAIM, _W2_RELEASE),
            TaskState.COMPLETED,
            "builder",
            "3/3 pass",
            (_W2_CLAIM, _W2_RELEASE),
        ),
        # ... or finishes it: never one window's result under the other's claim.
        (
            (_W1_CLAIM, _W1_DONE, _W2_CLAIM, _W2_DONE),
            TaskState.COMPLETED,
            "builder",
            "3/3 pass",
            (_W2_CLAIM, _W2_DONE),
        ),
        # Both claim before either finishes. From the second claim on there are two
        # histories, so the first window's completion is withheld as well.
        (
            (_W1_CLAIM, _W2_CLAIM, _W1_DONE, _W2_DONE),
            TaskState.CLAIMED,
            "builder",
            "",
            (_W2_CLAIM, _W1_DONE, _W2_DONE),
        ),
        # A second declaration alone: whose specification every later record is
        # about is no longer known, so none is applied.
        (
            (declared(detail="B"), _W2_CLAIM, _W2_DONE),
            TaskState.PENDING,
            None,
            "",
            (declared(detail="B"), _W2_CLAIM, _W2_DONE),
        ),
    ],
    ids=[
        "claims-the-finished",
        "releases-the-finished",
        "finishes-the-finished",
        "both-claim",
        "declared-twice",
    ],
)
def test_two_writers_never_restore_as_a_task_neither_of_them_held(
    log: tuple[Record, ...],
    state: TaskState,
    holder: str | None,
    result: str,
    withheld: tuple[Record, ...],
) -> None:
    """
    The hazard: each window has its own store, so each reducer grants what the
    other would refuse, and a fold that applies every record as stated builds a
    task out of both -- a live claim carrying somebody else's run-basis result.
    From the first record one writer could not have appended, the task stands as
    it stood, says so, and keeps everything it did not apply.
    """
    board = boardlog.restore((declared(detail="A"), *log))

    (task,) = board.tasks
    assert (task.state, task.holder, task.result) == (state, holder, result)
    assert task.detail == "A" and task.version == 1
    assert task.contested is True
    assert board.contested == withheld
    assert board.stray == ()
    # What stands is what the records before the contest say, and nothing else.
    cut = log.index(withheld[0])
    assert (
        dataclasses.replace(task, contested=False)
        == boardlog.restore((declared(detail="A"), *log[:cut])).tasks[0]
    )


def test_a_finished_task_is_not_finished_or_given_back_again_by_its_own_holder() -> None:
    """
    ``holder`` survives completion, so "the address that holds it" is not enough:
    a second window's completion by the same address would replace the result.
    """
    second = Completed(at=13.0, task_id="t-1", by="builder", result="a different account")
    back = Released(at=14.0, task_id="t-1", by="builder")

    board = boardlog.restore((declared(), _W1_CLAIM, _W1_DONE, second, back))

    (task,) = board.tasks
    assert (task.state, task.result, task.released_by) == (TaskState.COMPLETED, "3/3 pass", ())
    assert board.contested == (second, back)


@pytest.mark.parametrize(
    "impossible",
    [
        Released(at=11.0, task_id="t-1", by="builder"),
        Completed(at=11.0, task_id="t-1", by="builder", result="x"),
        WritesAccrued(at=11.0, task_id="t-1", paths=("a.py",), lines_added=1),
    ],
    ids=["released", "completed", "writes"],
)
def test_a_record_about_a_claim_nobody_holds_contests_the_task(impossible: Record) -> None:
    board = boardlog.restore((declared(), impossible))

    assert board.contested == (impossible,)
    assert (
        dataclasses.replace(board.tasks[0], contested=False)
        == boardlog.restore((declared(),)).tasks[0]
    )


def test_the_operators_amendment_is_one_writers_at_any_point_and_a_contest_takes_none() -> None:
    """
    The operator may amend a pending, a held and a finished task, so its amendment
    alone never contests. After a contest it is withheld like everything else.
    """
    amend = Amended(at=20.0, task_id="t-1", detail="new")
    for prefix in ((), (_W1_CLAIM,), (_W1_CLAIM, _W1_DONE)):
        board = boardlog.restore((declared(), *prefix, amend))
        assert board.contested == () and board.tasks[0].detail == "new"

    board = boardlog.restore((declared(detail="A"), _W1_CLAIM, _W2_CLAIM, amend))
    assert board.tasks[0].detail == "A"
    assert board.contested == (_W2_CLAIM, amend)


def test_an_agents_amendment_is_one_writers_only_from_a_declarer_that_never_held_the_task() -> None:
    """
    ``store._amender_refusal``, on what the records carry. One reducer turns away an
    amendment by an agent that did not declare the task, and by a declarer that has
    held it at any time -- holding it, finished, or long since given back and the
    task amended since, which is the case ``released_by`` forgets and ``held_by``
    does not.
    """
    by_lead = Amended(at=20.0, task_id="t-1", detail="new", by="lead")
    by_builder = Amended(at=20.0, task_id="t-1", detail="new", by="builder")
    lead_claims = Claimed(at=11.0, task_id="t-1", by="lead")
    lead_gives_back = Released(at=12.0, task_id="t-1", by="lead")
    operator = Amended(at=13.0, task_id="t-1", detail="the operator's")

    for prefix in ((), (_W1_CLAIM,), (_W1_CLAIM, _W1_DONE)):
        board = boardlog.restore((declared(), *prefix, by_lead))
        assert board.contested == () and board.tasks[0].detail == "new"
        assert boardlog.restore((declared(), *prefix, by_builder)).contested == (by_builder,)

    for held in (
        (lead_claims,),
        (lead_claims, Completed(at=12.0, task_id="t-1", by="lead")),
        (lead_claims, lead_gives_back),
        (lead_claims, lead_gives_back, operator),
    ):
        board = boardlog.restore((declared(), *held, by_lead))
        assert board.contested == (by_lead,), held
        assert board.tasks[0].detail != "new"


@pytest.mark.parametrize("before", [(), (_W1_CLAIM,)], ids=["pending", "claimed"])
def test_a_close_finishes_a_task_for_nobody_with_the_composed_result(
    before: tuple[Record, ...],
) -> None:
    """
    ``store._apply``'s close arm, restated: taken in any unfinished state, it
    leaves a finished task no party holds, whose result is the closing words and
    the reason as typed, with nothing a claimer states beside it.
    """
    close = Closed(at=20.0, task_id="t-1", reason=" not wanted\n")

    board = boardlog.restore((declared(), *before, close))

    (task,) = board.tasks
    assert board.contested == () and not board.short
    assert (task.state, task.holder, task.claimed_version) == (TaskState.COMPLETED, None, None)
    assert task.closed_by_operator
    assert (task.completed_at, task.result) == (20.0, "closed by the operator:  not wanted\n")
    assert (task.result_basis, task.not_run) == (None, None)
    assert task.held_by == tuple(record.by for record in before)
    assert boardlog.restore((declared(), Closed(at=20.0, task_id="t-1"))).tasks[0].result == (
        "closed by the operator"
    )


def test_a_close_of_a_finished_task_is_not_one_writers_and_changes_nothing() -> None:
    """
    A reducer leaves a finished task alone, a closed one included.
    """
    close = Closed(at=20.0, task_id="t-1", reason="first")
    again = Closed(at=21.0, task_id="t-1", reason="second")

    finished = boardlog.restore((declared(), _W1_CLAIM, _W1_DONE, close))
    assert finished.contested == (close,)
    assert (finished.tasks[0].result, finished.tasks[0].closed_by_operator) == ("3/3 pass", False)

    twice = boardlog.restore((declared(), close, again))
    assert twice.contested == (again,)
    assert twice.tasks[0].result == "closed by the operator: first"


def test_a_completion_that_types_the_closing_words_is_still_its_claimers() -> None:
    """
    Who ended a task is the record's kind and never its text: a claimer can write
    any result it likes, and a ``Completed`` is one it wrote.
    """
    forged = Completed(at=12.0, task_id="t-1", by="builder", result="closed by the operator: x")

    (task,) = boardlog.restore((declared(), _W1_CLAIM, forged)).tasks

    assert (task.closed_by_operator, task.holder) == (False, "builder")


def test_nothing_can_be_done_to_a_task_once_it_is_closed_but_amend_it() -> None:
    """
    What a reducer refuses on a finished task it refuses on a closed one.
    """
    close = Closed(at=20.0, task_id="t-1", reason="x")
    for later in (
        Claimed(at=21.0, task_id="t-1", by="builder"),
        Completed(at=21.0, task_id="t-1", by="builder"),
        Released(at=21.0, task_id="t-1", by="builder"),
        Reclaimed(at=21.0, task_id="t-1", by="lead", holder="builder"),
    ):
        board = boardlog.restore((declared(), _W1_CLAIM, close, later))
        assert board.contested == (later,), later
    amended = Amended(at=21.0, task_id="t-1", detail="new")
    board = boardlog.restore((declared(), close, amended))
    assert board.contested == () and board.tasks[0].detail == "new"
    assert board.tasks[0].closed_by_operator


def test_a_claim_by_the_agent_that_amended_the_task_is_not_one_writers() -> None:
    """``store._claim_refusal``: the agent that wrote a version is not then bound by it."""
    amended = Amended(at=11.0, task_id="t-1", detail="new", by="lead")
    takes_it = Claimed(at=12.0, task_id="t-1", by="lead")

    board = boardlog.restore((declared(), amended, takes_it))
    assert (board.tasks[0].state, board.contested) == (TaskState.PENDING, (takes_it,))

    # Declared and claimed as declared is the one way an agent does both.
    assert boardlog.restore((declared(), takes_it)).contested == ()
    # An amendment that changed nothing wrote no version.
    same = Amended(at=11.0, task_id="t-1", detail="", by="lead")
    assert boardlog.restore((declared(), same, takes_it)).contested == ()


def test_a_contest_on_one_task_leaves_every_other_task_alone() -> None:
    other = (
        declared("t-2"),
        Claimed(at=11.0, task_id="t-2", by="builder-2"),
        Completed(at=12.0, task_id="t-2", by="builder-2", result="ok"),
    )
    board = boardlog.restore((declared(), _W1_CLAIM, _W2_CLAIM, *other))

    assert [(t.id, t.contested) for t in board.tasks] == [("t-1", True), ("t-2", False)]
    assert board.tasks[1] == boardlog.restore(other).tasks[0]


def test_a_concern_two_writers_disagreed_about_stands_as_it_stood() -> None:
    """
    The same hazard on the other record. One window delivers a concern; the other,
    which never saw that, rewrites or withdraws it. Applied as stated the board says
    the recipient was handed words it never read, or was never handed anything.
    """
    handed = Delivered(at=11.0, concern_ids=("c-1",))
    rewrite = Edited(at=12.0, concern_id="c-1", body="softer")
    stop = Withdrawn(at=13.0, concern_id="c-1")
    again = Delivered(at=14.0, concern_ids=("c-1", "c-2"))

    board = boardlog.restore((posted("c-1"), posted("c-2"), handed, rewrite, stop, again))

    first, second = board.concerns
    assert (first.state, first.body, first.edited, first.delivered_at) == (
        ConcernState.DELIVERED,
        "as posted",
        False,
        11.0,
    )
    assert first.contested is True
    # The delivery that also named a concern nobody disputes still marks that one.
    assert (second.state, second.delivered_at, second.contested) == (
        ConcernState.DELIVERED,
        14.0,
        False,
    )
    assert board.contested == (rewrite, stop, again)

    # Posted twice and still waiting: whose text a later edit rewrites is unknown.
    twice = boardlog.restore((posted("c-1"), posted("c-1", body="a second post"), rewrite))
    assert (twice.concerns[0].body, twice.concerns[0].edited) == ("as posted", False)
    assert twice.concerns[0].contested
    assert twice.contested == (posted("c-1", body="a second post"), rewrite)

    withdrawn = boardlog.restore((posted("c-1"), stop, handed))
    assert withdrawn.concerns[0].state is ConcernState.WITHDRAWN
    assert withdrawn.concerns[0].delivered_at is None and withdrawn.contested == (handed,)


def test_a_delivery_naming_one_concern_twice_hands_it_over_once_and_contests_nothing() -> None:
    """
    The repeat is one writer saying one thing again. Read as a second delivery it
    would mark the concern handed over and disputed by the same record.
    """
    twice = Delivered(at=11.0, concern_ids=("c-1", "c-1", "c-2", "c-1"))

    board = boardlog.restore((posted("c-1"), posted("c-2"), twice))

    assert [(c.state, c.delivered_at, c.contested) for c in board.concerns] == [
        (ConcernState.DELIVERED, 11.0, False)
    ] * 2
    assert board.contested == () and board.stray == ()
    assert board == boardlog.restore(
        (posted("c-1"), posted("c-2"), Delivered(at=11.0, concern_ids=("c-1", "c-2")))
    )

    # A second record is still a second delivery.
    again = boardlog.restore((posted("c-1"), twice, twice))
    assert again.concerns[0].contested and again.contested == (twice,)


def test_known_cost_a_task_finished_after_a_restart_restores_unfinished_and_contested() -> None:
    """
    Not two windows: one process at a time. The first ends holding the task; the
    next gives its agents new addresses, and one of them claims and finishes it.
    The fold cannot tell that from two writers, so the finished work restores as
    held by the agent that is gone, and stays so on every later restore.

    This is a log with no ``Reclaimed`` in it. Once the second claim is logged
    nothing a later process logs changes the answer -- including finishing the task
    a second time.
    """
    first_process: tuple[Record, ...] = (declared(), Claimed(at=11.0, task_id="t-1", by="builder"))
    reclaimed = Claimed(at=20.0, task_id="t-1", by="builder-2")
    finished = Completed(at=21.0, task_id="t-1", by="builder-2", result="3/3 pass", basis="run")

    board = boardlog.restore((*first_process, reclaimed, finished))

    (task,) = board.tasks
    assert (task.state, task.holder, task.result) == (TaskState.CLAIMED, "builder", "")
    assert task.contested is True
    assert board.contested == (reclaimed, finished)
    # The one place a caller that maps tasks onto a store without looking at
    # `contested` is still told.
    assert board.short is True

    later = boardlog.restore(
        (
            *first_process,
            reclaimed,
            finished,
            Claimed(at=30.0, task_id="t-1", by="builder-3"),
            Completed(at=31.0, task_id="t-1", by="builder-3", result="again"),
        )
    )
    assert (later.tasks[0].state, later.tasks[0].holder) == (TaskState.CLAIMED, "builder")
    assert len(later.contested) == 4


def test_a_reclaim_logged_before_the_next_claim_ends_the_contest_a_restart_leaves() -> None:
    first_process: tuple[Record, ...] = (declared(), Claimed(at=11.0, task_id="t-1", by="builder"))
    given_up = Reclaimed(at=19.0, task_id="t-1", by="lead", holder="builder")
    taken = Claimed(at=20.0, task_id="t-1", by="builder-2")
    finished = Completed(at=21.0, task_id="t-1", by="builder-2", result="3/3 pass", basis="run")

    board = boardlog.restore((*first_process, given_up, taken, finished))

    (task,) = board.tasks
    assert (task.state, task.holder, task.result) == (TaskState.COMPLETED, "builder-2", "3/3 pass")
    assert not board.short
    assert task.held_by == ("builder", "builder-2")


def test_a_reclaim_leaves_the_task_pending_and_declined_by_whoever_reclaimed_it() -> None:
    """
    The reclaimer joins ``released_by`` and the holder does not: a bare claim by
    the agent that just put the task back must not be handed it, and the holder
    declined nothing.
    """
    (task,) = boardlog.restore(
        (
            declared(),
            Claimed(at=11.0, task_id="t-1", by="builder"),
            Reclaimed(at=19.0, task_id="t-1", by="lead", holder="builder"),
        )
    ).tasks

    assert (task.state, task.holder, task.claimed_version) == (TaskState.PENDING, None, None)
    assert (task.released_by, task.released_at) == (("lead",), 19.0)
    assert task.held_by == ("builder",)


@pytest.mark.parametrize(
    "impossible",
    [
        # Nobody holds it.
        (Reclaimed(at=19.0, task_id="t-1", by="lead", holder="builder"),),
        # Somebody else does.
        (
            Claimed(at=11.0, task_id="t-1", by="builder-2"),
            Reclaimed(at=19.0, task_id="t-1", by="lead", holder="builder"),
        ),
        # It is finished.
        (
            Claimed(at=11.0, task_id="t-1", by="builder"),
            Completed(at=12.0, task_id="t-1", by="builder"),
            Reclaimed(at=19.0, task_id="t-1", by="lead", holder="builder"),
        ),
    ],
)
def test_a_reclaim_of_a_claim_the_named_holder_does_not_have_contests_the_task(
    impossible: tuple[Record, ...],
) -> None:
    board = boardlog.restore((declared(), *impossible))

    assert board.contested == (impossible[-1],)
    assert board.tasks[0].released_by == ()


@pytest.mark.parametrize(
    ("records", "why"),
    [
        ((declared(), Claimed(at=1.0, task_id="ghost", by="builder")), "stray"),
        ((declared(), Unrecognised(at=1.0, kind="decision-raised", text="{}")), "unrecognised"),
        ((declared(), declared()), "contested"),
        ((posted(), Delivered(at=1.0, concern_ids=("c-1", "ghost"))), "stray"),
    ],
    ids=["stray", "a-later-builds-kind", "contested", "a-delivery-half-applied"],
)
def test_a_board_holding_less_than_its_log_says_so_in_one_answer(
    records: tuple[Record, ...], why: str
) -> None:
    whole = boardlog.restore(records[:1])
    assert whole.short is False

    board = boardlog.restore(records)

    assert getattr(board, why) != ()
    assert board.short is True


def test_a_board_restored_from_a_damaged_log_carries_the_damage(tmp_path: Path) -> None:
    """
    The readable prefix of a broken log is a well-formed board with nothing stray
    and nothing contested. Built from the log, it still answers that it is short.
    """
    write(tmp_path, (declared(), _W1_CLAIM, _W1_DONE))
    assert boardlog.restore_log(boardlog.read(tmp_path)).short is False
    (tmp_path / "00000001.json").unlink()
    log = boardlog.read(tmp_path)

    board = boardlog.restore_log(log)

    assert log.damage == Damage(1, "missing", 1)
    assert board.damage == log.damage and board.short is True
    assert (board.stray, board.unrecognised, board.contested) == ((), (), ())
    assert dataclasses.replace(board, damage=None) == boardlog.restore(log.records)
    assert boardlog.restore(log.records).short is False


def test_a_claim_restores_who_won_rather_than_asking_again() -> None:
    """
    The bare-claim break, at the fold: the record names the task, so a second task
    that is older and just as claimable is not the one that comes back held.
    """
    board = boardlog.restore(
        (
            declared("older", at=1.0),
            declared("newer", at=2.0),
            Claimed(at=3.0, task_id="newer", by="builder"),
        )
    )

    assert [(t.id, t.state, t.holder) for t in board.tasks] == [
        ("older", TaskState.PENDING, None),
        ("newer", TaskState.CLAIMED, "builder"),
    ]


def test_a_released_task_is_pending_and_remembers_each_agent_once() -> None:
    claim = Claimed(at=11.0, task_id="t-1", by="builder")
    release = Released(at=12.0, task_id="t-1", by="builder")
    board = boardlog.restore(
        (
            declared(),
            claim,
            release,
            claim,
            release,
            Claimed(at=13.0, task_id="t-1", by="builder-2"),
            Released(at=14.0, task_id="t-1", by="builder-2"),
        )
    )

    (task,) = board.tasks
    assert (task.state, task.holder) == (TaskState.PENDING, None)
    assert task.released_by == ("builder", "builder-2")


def test_a_release_and_reclaim_ends_held_by_the_second_claimer() -> None:
    (task,) = boardlog.restore(
        (
            declared(),
            Claimed(at=11.0, task_id="t-1", by="builder"),
            Released(at=12.0, task_id="t-1", by="builder"),
            Claimed(at=13.0, task_id="t-1", by="builder-2"),
        )
    ).tasks

    assert (task.state, task.holder, task.released_by) == (
        TaskState.CLAIMED,
        "builder-2",
        ("builder",),
    )


def test_each_amendment_is_a_version_and_the_current_text_is_the_last() -> None:
    (task,) = boardlog.restore(
        (
            declared(detail="first"),
            Amended(at=20.0, task_id="t-1", detail="second"),
            Amended(at=30.0, task_id="t-1", detail="third", by="lead"),
        )
    ).tasks

    assert task.version == 3
    assert task.detail == "third"
    assert task.declared_at == 10.0
    assert task.versions == (
        boardlog.TaskVersion(10.0, "first", "lead"),
        boardlog.TaskVersion(20.0, "second", None),
        boardlog.TaskVersion(30.0, "third", "lead"),
    )


def test_a_basis_describes_the_text_it_came_with_and_not_the_one_that_replaced_it() -> None:
    """``store``'s ``TaskAmended`` arm clears the declarer's basis; so does this."""
    stated = Declared(at=10.0, task_id="t-1", title="T", declared_by="lead", basis="run")

    (before,) = boardlog.restore((stated,)).tasks
    (after,) = boardlog.restore((stated, Amended(at=20.0, task_id="t-1", detail="new"))).tasks

    assert (before.basis, after.basis) == ("run", None)
    assert after.versions[0].basis == "run"


def test_a_result_names_the_version_it_was_a_result_of() -> None:
    """Amending a finished task must not move what the result was written against."""
    (task,) = boardlog.restore(
        (
            declared(detail="first"),
            Amended(at=11.0, task_id="t-1", detail="second"),
            Claimed(at=12.0, task_id="t-1", by="builder"),
            Completed(at=13.0, task_id="t-1", by="builder", result="done"),
            Amended(at=14.0, task_id="t-1", detail="third"),
        )
    ).tasks

    assert (task.completed_version, task.version) == (2, 3)
    assert (task.state, task.holder, task.result) == (TaskState.COMPLETED, "builder", "done")


def test_a_result_is_of_the_version_its_author_was_handed_not_the_one_standing() -> None:
    """
    The specification is replaced twice under the holder. A result stamped with
    the version on the board would say it was written against a text its author
    never read.
    """
    claimed: tuple[Record, ...] = (
        declared(detail="first"),
        Claimed(at=11.0, task_id="t-1", by="builder"),
        Amended(at=12.0, task_id="t-1", detail="second"),
        Amended(at=13.0, task_id="t-1", detail="third"),
    )
    done = Completed(at=15.0, task_id="t-1", by="builder", result="done")

    (unshown,) = boardlog.restore((*claimed, done)).tasks
    assert (unshown.completed_version, unshown.version) == (1, 3)

    shown = Handed(at=14.0, task_id="t-1", by="builder", version=2)
    (task,) = boardlog.restore((*claimed, shown, done)).tasks
    assert (task.completed_version, task.version) == (2, 3)

    # Pending again, nobody has been handed anything.
    (back,) = boardlog.restore(
        (*claimed, shown, Released(at=14.5, task_id="t-1", by="builder"))
    ).tasks
    assert (back.claimed_version, back.completed_version) == (None, None)


@pytest.mark.parametrize(
    "impossible",
    [
        Handed(at=14.0, task_id="t-1", by="builder-2", version=2),  # not the holder
        Handed(at=14.0, task_id="t-1", by="builder", version=3),  # no such version
        Handed(at=14.0, task_id="t-1", by="builder", version=1),  # behind the one held
    ],
)
def test_a_version_handed_to_anyone_but_the_holder_or_out_of_range_contests_the_task(
    impossible: Handed,
) -> None:
    held: tuple[Record, ...] = (
        declared(detail="first"),
        Amended(at=11.0, task_id="t-1", detail="second"),
        Claimed(at=12.0, task_id="t-1", by="builder"),
    )

    board = boardlog.restore((*held, impossible))

    assert board.contested == (impossible,)
    assert board.tasks[0].claimed_version == 2
    # Nobody holds a pending task, so nobody is shown its text either.
    pending = boardlog.restore((declared(), Handed(at=11.0, task_id="t-1", by="lead", version=1)))
    assert pending.tasks[0].contested


@pytest.mark.parametrize(("version", "taken"), [(-1, False), (0, False), (1, True), (2, False)])
def test_a_handed_version_is_bounded_from_one_where_the_claim_recorded_none(
    version: int, taken: bool
) -> None:
    """
    The store's bound, on the same held task: ``TaskVersionHanded`` is dropped
    below version 1 whether or not the claim recorded what it handed over, so a
    restore that took a ``Handed`` 0 would rebuild a board no reducer wrote.

    The guard is asked directly for the half no record sequence reaches: every
    ``Claimed`` this module folds records a version.
    """
    from pptmstr.intents import TaskClaimRequested, TaskDeclared, TaskVersionHanded
    from pptmstr.store import Store, _apply

    (held,) = boardlog.restore((declared(), Claimed(at=11.0, task_id="t-1", by="builder"))).tasks
    unrecorded = dataclasses.replace(held, claimed_version=None)
    shown = Handed(at=12.0, task_id="t-1", by="builder", version=version)

    assert boardlog._one_writers(unrecorded, shown) is taken
    assert boardlog._one_writers(held, shown) is taken

    # The store's arm on the same shape: a held task whose claim recorded none.
    lead, builder = ("s", None), ("s", "a1")
    store = Store()
    store.apply(TaskDeclared(Task(id="t-1", title="T", declared_at=1.0), node_id=lead))
    store.apply(TaskClaimRequested(builder, request_id="k", task_id="t-1"))
    snap = store.snapshot()
    stripped = dataclasses.replace(snap.tasks["t-1"], claimed_version=None)
    after, _ = _apply(
        dataclasses.replace(snap, tasks={**snap.tasks, "t-1": stripped}),
        TaskVersionHanded(node_id=builder, task_id="t-1", version=version),
        2.0,
    )
    assert (after.tasks["t-1"].claimed_version == version) is taken


def test_an_amendment_that_repeats_the_specification_is_no_version_and_still_reoffers() -> None:
    """
    What ``store``'s ``TaskAmended`` arm does with the editor's untouched box: no
    version, and ``released_by`` emptied all the same.
    """
    records: tuple[Record, ...] = (
        Declared(at=10.0, task_id="t-1", title="T", declared_by="lead", detail="A", property="P"),
        Claimed(at=11.0, task_id="t-1", by="builder"),
        Released(at=12.0, task_id="t-1", by="builder"),
    )
    for same in (
        Amended(at=13.0, task_id="t-1", detail="A"),
        Amended(at=13.0, task_id="t-1", detail="A", property="P", basis="run"),
    ):
        (task,) = boardlog.restore((*records, same)).tasks
        assert (task.version, task.basis, task.released_by) == (1, None, ())

    (reheld,) = boardlog.restore(
        (*records, Amended(at=13.0, task_id="t-1", detail="A", property=""))
    ).tasks
    assert (reheld.version, reheld.property) == (2, "")


def test_an_amended_text_carries_the_basis_its_amendment_states() -> None:
    (task,) = boardlog.restore(
        (
            Declared(at=10.0, task_id="t-1", title="T", declared_by="lead", basis="run"),
            Amended(at=20.0, task_id="t-1", detail="new", by="lead", basis="relayed"),
        )
    ).tasks

    assert [v.basis for v in task.versions] == ["run", "relayed"]
    assert task.basis == "relayed"


# -- who a record names -------------------------------------------------------------


def test_a_party_with_a_node_restores_as_the_node_and_one_without_as_its_address() -> None:
    board = boardlog.restore(
        (
            Declared(at=10.0, task_id="t-1", title="T", declared_by="lead", declared_by_id=("s",)),
            Claimed(at=11.0, task_id="t-1", by="builder", by_id=("s", "a7")),
            Declared(at=12.0, task_id="t-2", title="T", declared_by="lead"),
            Claimed(at=13.0, task_id="t-2", by="builder"),
        )
    )

    first, second = board.tasks
    assert (first.declared_by, first.holder) == (("s", None), ("s", "a7"))
    assert (second.declared_by, second.holder) == ("lead", "builder")
    assert board.addresses == ((("s", None), "lead"), (("s", "a7"), "builder"))


def test_two_nodes_the_board_had_no_name_for_are_two_parties() -> None:
    """
    Both would have been drawn "(unknown)". Written under that word they are one
    address, and the second one's release is the first one's.
    """
    ghost = Claimed(at=11.0, task_id="t-1", by="", by_id=("s", "ghost-1"))
    other = Released(at=12.0, task_id="t-1", by="", by_id=("s", "ghost-2"))

    board = boardlog.restore((declared(), ghost, other))

    assert board.contested == (other,)
    assert board.tasks[0].holder == ("s", "ghost-1")
    assert board.addresses == ()
    # The same node is itself.
    same = Released(at=12.0, task_id="t-1", by="", by_id=("s", "ghost-1"))
    assert boardlog.restore((declared(), ghost, same)).contested == ()


def test_a_name_two_agents_have_had_does_not_make_them_one_party() -> None:
    """
    Each process hands out ``builder-2`` in its own spawn order. The agent the next
    process calls that did not make the first one's claim.
    """
    then = Claimed(at=11.0, task_id="t-1", by="builder-2", by_id=("s", "a2"))
    now = Completed(at=30.0, task_id="t-1", by="builder-2", by_id=("s", "a9"), result="mine")

    board = boardlog.restore((declared(), then, now))

    assert board.contested == (now,)
    assert (board.tasks[0].state, board.tasks[0].holder) == (TaskState.CLAIMED, ("s", "a2"))


def test_one_agent_under_two_names_is_one_party_and_keeps_the_later_name() -> None:
    then = Claimed(at=11.0, task_id="t-1", by="builder-2", by_id=("s", "a2"))
    now = Completed(at=30.0, task_id="t-1", by="builder", by_id=("s", "a2"), result="mine")

    board = boardlog.restore((declared(), then, now))

    assert not board.short
    assert board.tasks[0].state is TaskState.COMPLETED
    assert board.addresses == ((("s", "a2"), "builder"),)


def test_a_bare_address_is_never_the_node_that_was_called_it() -> None:
    held = Claimed(at=11.0, task_id="t-1", by="builder", by_id=("s", "a1"))
    by_name = Released(at=12.0, task_id="t-1", by="builder")

    assert boardlog.restore((declared(), held, by_name)).contested == (by_name,)


def test_an_amendment_with_a_node_and_no_address_is_an_agents_not_the_operators() -> None:
    nameless = Amended(at=20.0, task_id="t-1", detail="new", by=None, by_id=("s", "ghost"))

    board = boardlog.restore((declared(), nameless))

    # Not the declarer, so one reducer would have turned it away: read as the
    # operator's it would have been applied.
    assert board.contested == (nameless,)


@pytest.mark.parametrize("node", [("s", "a", "extra"), ("",) * 4])
def test_a_node_field_of_a_length_no_writer_produces_says_nothing(node: tuple[str, ...]) -> None:
    (task,) = boardlog.restore(
        (Declared(at=10.0, task_id="t-1", title="T", declared_by="lead", declared_by_id=node),)
    ).tasks

    assert task.declared_by == "lead"


def test_a_log_written_before_parties_had_nodes_reads_as_addresses() -> None:
    old = '{"kind": "task-claimed", "at": 1.0, "task_id": "t-1", "by": "builder"}\n'

    record = boardlog.decode(old)

    assert record == Claimed(at=1.0, task_id="t-1", by="builder", by_id=())


def test_an_amendment_forgets_who_declined_the_old_text_and_leaves_the_claim() -> None:
    """The two things ``store``'s ``TaskAmended`` arm does besides replace the text."""
    (task,) = boardlog.restore(
        (
            declared(),
            Claimed(at=11.0, task_id="t-1", by="builder"),
            Released(at=12.0, task_id="t-1", by="builder"),
            Claimed(at=13.0, task_id="t-1", by="builder-2"),
            Amended(at=14.0, task_id="t-1", detail="new"),
        )
    ).tasks

    assert task.released_by == ()
    assert (task.state, task.holder) == (TaskState.CLAIMED, "builder-2")


def test_writes_accrue_as_paths_merged_and_lines_summed() -> None:
    (task,) = boardlog.restore(
        (
            declared(),
            # A write is measured against a task somebody is holding open.
            Claimed(at=10.5, task_id="t-1", by="builder"),
            WritesAccrued(at=11.0, task_id="t-1", paths=("a.py",), lines_added=3, lines_removed=1),
            WritesAccrued(
                at=12.0,
                task_id="t-1",
                paths=("a.py", "b.py"),
                unplaced=("/etc/hosts",),
                lines_added=4,
            ),
        )
    ).tasks

    assert task.writes.paths == ("a.py", "b.py")
    assert task.writes.unplaced == ("/etc/hosts",)
    assert (task.writes.lines_added, task.writes.lines_removed) == (7, 1)


def test_a_concern_restores_as_posted_until_something_becomes_of_it() -> None:
    (concern,) = boardlog.restore((posted(task_id="t-9", basis="read"),)).concerns

    assert (concern.id, concern.sender, concern.recipient) == ("c-1", "builder", "lead")
    assert (concern.subject, concern.body, concern.posted_at) == ("s", "as posted", 10.0)
    assert (concern.task_id, concern.basis) == ("t-9", "read")
    assert (concern.state, concern.edited, concern.delivered_at) == (
        ConcernState.POSTED,
        False,
        None,
    )


def test_delivered_mail_restores_delivered_so_it_is_not_handed_over_again() -> None:
    """The inbox break: only the concerns a read named are marked, with its time."""
    board = boardlog.restore(
        (
            posted("c-1"),
            posted("c-2"),
            Delivered(at=20.0, concern_ids=("c-1",)),
            posted("c-3"),
        )
    )

    assert [(c.id, c.state, c.delivered_at) for c in board.concerns] == [
        ("c-1", ConcernState.DELIVERED, 20.0),
        ("c-2", ConcernState.POSTED, None),
        ("c-3", ConcernState.POSTED, None),
    ]


def test_an_edit_and_a_withdrawal_are_what_became_of_a_concern() -> None:
    board = boardlog.restore(
        (
            posted("c-1"),
            posted("c-2"),
            Edited(at=11.0, concern_id="c-1", body="as rewritten"),
            Delivered(at=12.0, concern_ids=("c-1",)),
            Withdrawn(at=13.0, concern_id="c-2"),
        )
    )

    first, second = board.concerns
    assert (first.body, first.edited, first.state) == ("as rewritten", True, ConcernState.DELIVERED)
    assert (second.body, second.edited, second.state) == (
        "as posted",
        False,
        ConcernState.WITHDRAWN,
    )


def test_a_property_stands_until_an_amendment_replaces_or_withdraws_it() -> None:
    """
    ``store``'s ``TaskAmended`` arm: an amendment that names no property rewrites
    the text beside it and leaves it in force.
    """
    stated = Declared(at=10.0, task_id="t-1", title="T", declared_by="lead", property="P1")

    def after(*amendments: Amended) -> boardlog.RestoredTask:
        (task,) = boardlog.restore((stated, *amendments)).tasks
        return task

    silent = Amended(at=20.0, task_id="t-1", detail="new")
    replaced = Amended(at=30.0, task_id="t-1", detail="newer", property="P2")
    withdrawn = Amended(at=40.0, task_id="t-1", detail="newest", property="")

    assert after().property == "P1"
    assert after(silent).property == "P1"
    assert after(silent, replaced).property == "P2"
    assert after(silent, replaced, silent).property == "P2"
    assert after(silent, replaced, withdrawn).property == ""
    assert after(withdrawn, silent).property == ""
    assert [v.property for v in after(silent, replaced, withdrawn).versions] == [
        "P1",
        "P1",
        "P2",
        "",
    ]


def test_a_completion_keeps_what_it_said_was_not_run_in_all_three_values() -> None:
    def finished(not_run: str | None) -> boardlog.RestoredTask:
        (task,) = boardlog.restore(
            (
                declared(),
                Claimed(at=11.0, task_id="t-1", by="builder"),
                Completed(at=12.0, task_id="t-1", by="builder", result="r", not_run=not_run),
            )
        ).tasks
        return task

    (unclaimed,) = boardlog.restore((declared(),)).tasks

    assert unclaimed.not_run is None
    assert finished(None).not_run is None
    assert finished("").not_run == ""
    assert finished("the cross presets").not_run == "the cross presets"


def test_a_release_stamps_when_the_task_last_came_back() -> None:
    """Later claims and amendments leave the stamp; only another release moves it."""
    records: tuple[Record, ...] = (
        declared(),
        Claimed(at=11.0, task_id="t-1", by="builder"),
        Released(at=12.0, task_id="t-1", by="builder"),
        Claimed(at=13.0, task_id="t-1", by="builder-2"),
        Amended(at=14.0, task_id="t-1", detail="new"),
        Released(at=15.0, task_id="t-1", by="builder-2"),
        Claimed(at=16.0, task_id="t-1", by="builder"),
        Completed(at=17.0, task_id="t-1", by="builder"),
    )

    def released_at(upto: int) -> float | None:
        (task,) = boardlog.restore(records[:upto]).tasks
        return task.released_at

    assert [released_at(n) for n in range(1, len(records) + 1)] == [
        None,
        None,
        12.0,
        12.0,
        12.0,
        15.0,
        15.0,
        15.0,
    ]


def test_an_incident_mark_restores_with_the_concern_and_survives_an_edit() -> None:
    marked = dataclasses.replace(posted("c-1"), incident=True)

    board = boardlog.restore(
        (marked, posted("c-2"), Edited(at=11.0, concern_id="c-1", body="as rewritten"))
    )

    assert [(c.id, c.incident) for c in board.concerns] == [("c-1", True), ("c-2", False)]


# -- every board fact has somewhere to go -----------------------------------------

# One session of records that moves every stored field of ``model.Task`` off its
# default: declared with everything stated, declined once, measured, and finished.
_FULL_TASK: tuple[Record, ...] = (
    Declared(
        at=10.0,
        task_id="t-1",
        title="Title",
        declared_by="lead",
        detail="spec",
        depends_on=("t-0",),
        touches=("a.py",),
        basis="read",
        property="P",
    ),
    Amended(
        at=10.5,
        task_id="t-1",
        detail="spec, corrected",
        by="lead",
        property="P2",
        basis="relayed",
    ),
    Claimed(at=11.0, task_id="t-1", by="builder"),
    Released(at=12.0, task_id="t-1", by="builder"),
    Claimed(at=13.0, task_id="t-1", by="builder-2"),
    WritesAccrued(
        at=14.0,
        task_id="t-1",
        paths=("a.py",),
        unplaced=("/etc/hosts",),
        lines_added=3,
        lines_removed=2,
    ),
    Completed(
        at=15.0, task_id="t-1", by="builder-2", result="3/3", basis="run", not_run="the rest"
    ),
)

# ``model.Task`` field -> where ``restore`` puts it, and what ``_FULL_TASK`` makes it.
_TASK_FACTS: dict[str, tuple[str, object]] = {
    "id": ("id", "t-1"),
    "title": ("title", "Title"),
    "detail": ("detail", "spec, corrected"),
    "depends_on": ("depends_on", ("t-0",)),
    "touches": ("touches", ("a.py",)),
    "claimed_by": ("holder", "builder-2"),
    "state": ("state", TaskState.COMPLETED),
    "declared_at": ("declared_at", 10.0),
    "completed_at": ("completed_at", 15.0),
    "declared_by": ("declared_by", "lead"),
    "released_by": ("released_by", ("builder",)),
    "writes": (
        "writes",
        ApprovedWrites(paths=("a.py",), unplaced=("/etc/hosts",), lines_added=3, lines_removed=2),
    ),
    "result": ("result", "3/3"),
    "basis": ("basis", "relayed"),
    "result_basis": ("result_basis", "run"),
    "property": ("property", "P2"),
    "not_run": ("not_run", "the rest"),
    "released_at": ("released_at", 12.0),
    # ``Task.superseded`` is every text but the current one, each with who replaced
    # it and when. ``versions`` is every text with who wrote it and when, so entry
    # n of the first is entry n of the second beside the ``by`` and ``at`` of n+1.
    "superseded": (
        "versions",
        (
            boardlog.TaskVersion(10.0, "spec", "lead", "read", "P"),
            boardlog.TaskVersion(10.5, "spec, corrected", "lead", "relayed", "P2"),
        ),
    ),
    "claimed_version": ("claimed_version", 2),
    "held_by": ("held_by", ("builder", "builder-2")),
}

# One concern marked, tied to a task, rewritten in flight and handed over.
_FULL_CONCERN: tuple[Record, ...] = (
    Posted(
        at=10.0,
        concern_id="c-1",
        sender="builder",
        recipient="lead",
        subject="subject",
        body="as posted",
        task_id="t-1",
        basis="relayed",
        incident=True,
    ),
    Edited(at=11.0, concern_id="c-1", body="as rewritten"),
    Delivered(at=12.0, concern_ids=("c-1",)),
)

_CONCERN_FACTS: dict[str, tuple[str, object]] = {
    "id": ("id", "c-1"),
    "sender": ("sender", "builder"),
    "recipient": ("recipient", "lead"),
    "subject": ("subject", "subject"),
    "body": ("body", "as rewritten"),
    "posted_at": ("posted_at", 10.0),
    "state": ("state", ConcernState.DELIVERED),
    "edited": ("edited", True),
    "delivered_at": ("delivered_at", 12.0),
    "task_id": ("task_id", "t-1"),
    "basis": ("basis", "relayed"),
    "incident": ("incident", True),
}

# Fields of ``model.Task`` and ``model.Concern`` the log deliberately does not
# carry, each with the reason: derived from other fields, meaningful only inside
# the process that set it, or a fact about the fleet rather than about one row.
# A name goes here only with its reason, and a name that is here and also restored
# is an error, since one of the two statements is then false.
_NOT_PERSISTED: dict[type, dict[str, str]] = {
    Task: {},
    Concern: {},
}

# Fields the log does carry that ``_FULL_TASK`` cannot set beside the others,
# each with the test that restores it instead. A close leaves a task with no
# holder and no claimer's statements, so no one history sets it and them.
_RESTORED_APART: dict[type, dict[str, Callable[..., None]]] = {
    Task: {"closed_by_operator": test_a_close_finishes_a_task_for_nobody_with_the_composed_result},
    Concern: {},
}


@pytest.mark.parametrize(
    ("model", "facts", "records", "pick"),
    [
        (Task, _TASK_FACTS, _FULL_TASK, lambda board: board.tasks),
        (Concern, _CONCERN_FACTS, _FULL_CONCERN, lambda board: board.concerns),
    ],
    ids=["Task", "Concern"],
)
def test_every_field_of_a_board_record_has_a_record_that_restores_it_or_is_left_out_by_name(
    model: type,
    facts: dict[str, tuple[str, object]],
    records: tuple[Record, ...],
    pick: Callable[[Board], tuple[object, ...]],
) -> None:
    """
    The pin for a field added to ``model.Task`` or ``model.Concern`` after this
    module was written. Such a field needs no new record kind to be lost, so the
    kind count above does not see it; this walks the model's own fields instead.

    Not only a list of names. Each carried field is restored from records that set
    it and compared with a value that is not what an unwritten field comes back
    as, so a field named here that the fold drops fails as surely as one that was
    never named. A field that is itself a record -- ``Task.writes`` -- is walked the
    same way, one level down: a value built by keyword takes a new field's default
    on both sides of an equality and would compare equal with the fact lost.

    **What this does not show.** The records are built by hand, so this pins that
    every fact has a record able to carry it and a fold that reads it back.
    That ``boardfacts.outcomes`` writes one is ``tests/test_boardfacts.py``'s.
    """
    fields = {field.name for field in dataclasses.fields(model)}
    left_out = _NOT_PERSISTED[model]

    apart = set(_RESTORED_APART[model])
    assert fields - set(facts) - set(left_out) - apart == set(), "a board fact with nowhere to go"
    assert apart - fields == set() and apart & (set(facts) | set(left_out)) == set()
    assert set(facts) - fields == set(), "names a field the model no longer has"
    assert set(left_out) - fields == set(), "excuses a field the model no longer has"
    assert set(facts) & set(left_out) == set()
    assert all(reason.strip() for reason in left_out.values())

    # Written and read back, so the value has been through the text as well.
    decoded = [boardlog.decode(boardlog.encode(record)) for record in records]
    assert decoded == list(records)
    (restored,) = pick(boardlog.restore(records))
    (unwritten,) = pick(boardlog.restore((_MINIMAL[model],)))

    for name, (attribute, expected) in facts.items():
        assert getattr(restored, attribute) == expected, name
        # The value is one the fold could only have got from a record: a restore
        # of the same thing with that fact unstated reads differently.
        assert getattr(unwritten, attribute) != expected or name == "id", name
        if dataclasses.is_dataclass(expected):
            for inner in dataclasses.fields(expected):
                carried = getattr(getattr(restored, attribute), inner.name)
                assert carried != getattr(
                    getattr(unwritten, attribute), inner.name
                ), f"{name}.{inner.name} is not moved off its default by any record here"


# The least a log can say about a task and a concern: the keys that have no
# default. The id is the one value these share with the full records.
_MINIMAL: dict[type, Record] = {
    Task: Declared(at=99.0, task_id="t-1", title="", declared_by=""),
    Concern: Posted(at=99.0, concern_id="c-1", sender="", recipient="", subject="", body=""),
}


def test_a_record_about_something_the_log_never_introduced_is_kept_and_changes_nothing() -> None:
    """
    A board restored short has to say so. Each of these names a task or concern
    with no declaration.
    """
    strays: tuple[Record, ...] = (
        Claimed(at=1.0, task_id="ghost", by="builder"),
        Released(at=1.0, task_id="ghost", by="builder"),
        Completed(at=1.0, task_id="ghost", by="builder"),
        Amended(at=1.0, task_id="ghost", detail="x"),
        WritesAccrued(at=1.0, task_id="ghost", paths=("a.py",)),
        Edited(at=1.0, concern_id="ghost", body="x"),
        Withdrawn(at=1.0, concern_id="ghost"),
        Delivered(at=1.0, concern_ids=("ghost",)),
    )
    honest = (declared(), posted())

    board = boardlog.restore((*honest, *strays))

    assert board.stray == strays
    assert board.contested == ()
    assert without_stray(board) == boardlog.restore(honest)


def without_stray(board: Board) -> Board:
    return Board(
        tasks=board.tasks,
        concerns=board.concerns,
        unrecognised=board.unrecognised,
        contested=board.contested,
    )


def test_a_delivery_naming_one_unknown_concern_still_marks_the_ones_it_knows() -> None:
    mark = Delivered(at=20.0, concern_ids=("c-1", "ghost", "c-2"))

    board = boardlog.restore((posted("c-1"), posted("c-2"), mark))

    assert [c.state for c in board.concerns] == [ConcernState.DELIVERED] * 2
    assert board.stray == (mark,)


@pytest.mark.parametrize("seed", SEEDS)
def test_the_fold_is_total_and_a_record_it_did_not_apply_never_changes_the_board(
    seed: int,
) -> None:
    """
    Over sequences that are mostly nonsense: nothing raises, tasks and concerns
    come back once each in the order the log introduced them, every record is
    applied, stray or withheld as contested, and dropping exactly the ones that
    were not applied restores the same tasks.
    """
    records = sequence(seed, length=80)

    board = boardlog.restore(records)

    first_seen = list(dict.fromkeys(r.task_id for r in records if isinstance(r, Declared)))
    assert [t.id for t in board.tasks] == first_seen
    first_posted = list(dict.fromkeys(r.concern_id for r in records if isinstance(r, Posted)))
    assert [c.id for c in board.concerns] == first_posted
    assert board.unrecognised == tuple(r for r in records if isinstance(r, Unrecognised))

    unapplied = {id(r) for r in (*board.stray, *board.contested)}
    # Tasks only: a delivery can be applied for one concern and withheld for
    # another, so it cannot be dropped whole and the concerns are checked below.
    kept = tuple(r for r in records if id(r) not in unapplied)
    assert boardlog.restore(kept).tasks == tuple(
        dataclasses.replace(t, contested=False) for t in board.tasks
    )
    assert {t.id for t in board.tasks if t.contested} == {
        r.task_id
        for r in board.contested
        if not isinstance(
            r, Posted | Edited | Withdrawn | Delivered | RecordingStopped | Unrecognised
        )
    }


@pytest.mark.parametrize("seed", SEEDS)
def test_no_sequence_restores_a_task_or_concern_one_reducer_could_not_hold(seed: int) -> None:
    """
    The property itself, over the same nonsense: whatever the records say, a
    restored task is in a shape ``store._apply`` can leave one in. A result, its
    basis and what was not run exist only on a finished task; a finished or held
    task names who; a pending one names nobody -- and neither does one the operator
    closed, which is finished, is nobody's, and carries the closing words and
    nothing a claimer states. A concern is delivered exactly when it carries the
    time it was.
    """
    board = boardlog.restore(sequence(seed, length=120))

    for task in board.tasks:
        finished = task.state is TaskState.COMPLETED
        unheld = task.state is TaskState.PENDING or task.closed_by_operator
        assert not task.closed_by_operator or finished, task
        assert (task.holder is None) == unheld, task
        assert (task.completed_at is not None) == finished, task
        assert (task.completed_version is not None) == (finished and not unheld), task
        assert (task.claimed_version is None) == unheld, task
        if task.closed_by_operator:
            assert task.result.startswith("closed by the operator"), task
            assert (task.result_basis, task.not_run) == (None, None), task
        assert task.holder is None or task.holder in task.held_by, task
        assert 1 <= (task.claimed_version or 1) <= task.version, task
        if not finished:
            assert (task.result, task.result_basis, task.not_run) == ("", None, None), task
    for concern in board.concerns:
        assert (concern.delivered_at is not None) == (
            concern.state is ConcernState.DELIVERED
        ), concern


@pytest.mark.parametrize("seed", range(20))
def test_restoring_is_a_function_of_the_records_alone(seed: int) -> None:
    records = sequence(seed)

    assert boardlog.restore(records) == boardlog.restore(iter(list(records)))


# -- times ------------------------------------------------------------------------


def test_a_restored_time_is_as_far_before_now_as_it_was_on_the_wall() -> None:
    clock = Clock(wall=1_000_000.0, monotonic=500.0)

    assert clock.monotonic_at(999_990.0) == 490.0
    assert clock.monotonic_at(1_000_000.0) == 500.0


def test_a_stamp_written_to_the_wall_restores_to_the_stamp_in_the_process_that_wrote_it() -> None:
    clock = Clock(wall=1_000_000.0, monotonic=500.0)

    assert clock.wall_at(490.0) == 999_990.0
    for stamp in (0.0, 12.5, 499.75, 500.0):
        assert clock.monotonic_at(clock.wall_at(stamp)) == stamp


def test_work_restored_after_a_reboot_sorts_before_work_declared_since() -> None:
    """
    The inversion a stored monotonic value produces: the old boot had been up for
    a day, the new one for twelve seconds, and a task restored from the first must
    still be older than one declared in the second.
    """
    old_boot_monotonic = 90_000.0
    clock = Clock(wall=1_760_000_600.0, monotonic=12.0)
    declared_now = 12.5
    assert old_boot_monotonic > declared_now  # what storing the reading would compare

    restored = [clock.monotonic_at(at) for at in (1_760_000_000.0, 1_760_000_300.0)]

    assert restored == sorted(restored)
    assert restored == [-588.0, -288.0]
    assert all(value < declared_now for value in restored)


def test_a_record_stamped_in_the_future_is_not_newer_than_the_restore() -> None:
    """A wall clock stepped back between sessions must not put old work after new."""
    clock = Clock(wall=1_000.0, monotonic=50.0)

    assert clock.monotonic_at(5_000.0) == 50.0
