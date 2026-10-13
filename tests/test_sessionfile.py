"""
The session file: what it reads as, and that a write never costs what it could
not read.

Driven against real files under ``tmp_path``, because the properties that matter
are the filesystem's: a name holds one writer's whole file or another's, and a
name that is a FIFO or a link is not opened as though it were a file.

Every call passes ``root``. Nothing here may reach the operator's config
directory, and the last section pins that the module can be kept from it.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import os
import random
import stat
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from pptmstr import boardlog, sessionfile, settings
from pptmstr.approval import Policy
from pptmstr.sessionfile import (
    FORMAT,
    MAX_FILE_BYTES,
    MAX_LAST_MESSAGE,
    Absent,
    Listing,
    SessionFile,
    UnknownPolicy,
    Unreadable,
    Unwritten,
    Written,
)

SID = "0b9c6a52-5c1e-4d7b-9f0e-2f3f6f1f7a10"

# The plan's table (planning/2026-10-13-building-the-persisted-fleet.md), spelled
# here so that a field added to the record without the plan, or the reverse, fails.
_PLANNED = (
    "format",
    "session_id",
    "in_fleet",
    "created_at",
    "launched_at",
    "last_active",
    "ended_at",
    "ended_how",
    "task",
    "last_message",
    "model",
    "cwd",
    "session_base",
    "template",
    "brief",
    "policy",
    "contained",
    "subagent_cap",
    "cost_usd",
    "error",
)

# One value of the right type for every key ``update`` takes.
_FULL: dict[str, Any] = {
    "in_fleet": False,
    "created_at": 1_760_000_000.5,
    "launched_at": 1_760_000_100.0,
    "last_active": 1_760_000_200.25,
    "ended_at": 1_760_000_300.0,
    "ended_how": "closed",
    "task": "wire the fleet",
    "last_message": "carry on",
    "model": "claude-opus-5-5",
    "cwd": "/home/op/src/thing",
    "session_base": "/home/op/src",
    "template": "team",
    "brief": "/home/op/.claude/briefs/x",
    "policy": "permissive",
    "contained": True,
    "subagent_cap": 4,
    "cost_usd": 1.25,
    "error": "the CLI exited 1",
}


def _put(root: Path, session_id: str, content: bytes | dict[str, Any]) -> Path:
    directory = root / session_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / sessionfile.FILE_NAME
    path.write_bytes(content if isinstance(content, bytes) else json.dumps(content).encode())
    return path


def _raw(root: Path, session_id: str = SID) -> dict[str, Any]:
    loaded = json.loads((root / session_id / sessionfile.FILE_NAME).read_bytes())
    assert isinstance(loaded, dict)
    return loaded


def _names(directory: Path) -> list[str]:
    return sorted(os.listdir(directory))


def _record(found: object) -> SessionFile:
    assert isinstance(found, SessionFile), found
    return found


def _written(result: object) -> Written:
    assert isinstance(result, Written), result
    return result


# -- the record -------------------------------------------------------------------


def test_the_record_has_the_planned_fields_and_the_table_reads_every_one() -> None:
    names = [f.name for f in dataclasses.fields(SessionFile)]
    assert sorted(names) == sorted(_PLANNED)
    assert sorted(sessionfile._FIELDS) == sorted(set(_PLANNED) - {"format", "session_id"})
    assert sorted(_FULL) == sorted(sessionfile._FIELDS)


def test_a_file_with_no_keys_reads_as_the_defaults_and_the_table_agrees() -> None:
    bare = sessionfile.decode({}, session_id=SID)
    assert bare == SessionFile(session_id=SID)
    for key, (_, default) in sessionfile._FIELDS.items():
        assert getattr(bare, key) == default, key
    assert bare.in_fleet is True
    assert bare.format == FORMAT and bare.writable
    assert bare.policy == UnknownPolicy()
    assert bare.contained is False
    assert (bare.created_at, bare.task, bare.last_message) == (None, "", None)


def test_every_field_round_trips_through_a_write(tmp_path: Path) -> None:
    result = _written(sessionfile.update(SID, _FULL, tmp_path))
    assert result.skipped == ()
    assert result.path == tmp_path / SID / "session.json"
    read = _record(sessionfile.read(SID, tmp_path))
    assert read == result.record
    for key, value in _FULL.items():
        expected = Policy(value) if key == "policy" else value
        assert getattr(read, key) == expected, key
        assert type(getattr(read, key)) is type(expected), key
    assert _raw(tmp_path) == {**_FULL, "format": FORMAT, "session_id": SID}


def test_unknown_keys_are_ignored_on_read() -> None:
    read = _record(sessionfile.decode({"task": "t", "owner": {"pid": 7}, "x": [1]}, session_id=SID))
    assert read == SessionFile(session_id=SID, task="t")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("created_at", "yesterday"),
        ("created_at", True),
        ("last_active", None),
        ("ended_at", "x"),
        ("ended_how", "killed"),
        ("ended_how", 3),
        ("task", 5),
        ("task", None),
        ("last_message", ["a"]),
        ("model", {}),
        ("cwd", 1),
        ("brief", 0),
        ("contained", "yes"),
        ("contained", 1),
        ("subagent_cap", 1.5),
        ("subagent_cap", True),
        ("cost_usd", "1.0"),
        ("cost_usd", float("nan")),
        ("cost_usd", float("inf")),
        ("cost_usd", 10**400),
        ("error", 2),
    ],
)
def test_a_known_key_of_the_wrong_type_reads_as_its_default(key: str, value: object) -> None:
    read = _record(sessionfile.decode({key: value, "template": "team"}, session_id=SID))
    assert getattr(read, key) == sessionfile._FIELDS[key][1]
    # One bad value costs its own key and no other.
    assert read.template == "team"


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, [], {}, 0.0])
def test_an_in_fleet_that_is_not_a_bool_is_unreadable_not_defaulted(value: object) -> None:
    found = sessionfile.decode({"in_fleet": value, "task": "t"}, session_id=SID)
    assert isinstance(found, Unreadable)
    assert "in_fleet" in found.reason


@pytest.mark.parametrize("stated", ["another", SID.upper(), SID + " ", "", 5, None, [SID]])
def test_a_session_id_that_is_not_the_directorys_name_is_unreadable(stated: object) -> None:
    found = sessionfile.decode({"session_id": stated}, session_id=SID)
    assert isinstance(found, Unreadable)
    assert "session_id" in found.reason


@pytest.mark.parametrize("fmt", [0, -1, "1", 1.0, 2.5, True, None, [1]])
def test_a_format_that_is_not_a_positive_whole_number_is_unreadable(fmt: object) -> None:
    found = sessionfile.decode({"format": fmt}, session_id=SID)
    assert isinstance(found, Unreadable)
    assert "format" in found.reason


@pytest.mark.parametrize("fmt", [FORMAT + 1, 99, 10**30])
def test_a_newer_format_is_displayed_from_the_known_keys_and_marked_unwritable(fmt: int) -> None:
    read = _record(
        sessionfile.decode(
            {"format": fmt, "task": "t", "policy": "strict", "in_fleet": False, "later": {"a": 1}},
            session_id=SID,
        )
    )
    assert not read.writable
    assert read.format == fmt
    assert (read.task, read.policy, read.in_fleet) == ("t", Policy.STRICT, False)


@pytest.mark.parametrize("rung", list(Policy))
def test_every_rung_this_build_has_reads_as_itself(rung: Policy) -> None:
    assert _record(sessionfile.decode({"policy": rung.value}, session_id=SID)).policy is rung


@pytest.mark.parametrize(
    "stated", ["root", "STRICT", "Strict", " strict", "", "autonomous2", "Policy.STRICT"]
)
def test_a_policy_this_build_does_not_know_is_never_a_rung(stated: str) -> None:
    policy = _record(sessionfile.decode({"policy": stated}, session_id=SID)).policy
    assert policy == UnknownPolicy(stated)
    assert not isinstance(policy, Policy)


@pytest.mark.parametrize("stated", [None, 0, 1, True, ["strict"], {"policy": "strict"}])
def test_a_policy_that_is_not_text_is_unknown_and_carries_nothing(stated: object) -> None:
    assert _record(sessionfile.decode({"policy": stated}, session_id=SID)).policy == UnknownPolicy()


def _shape(rng: random.Random, depth: int = 0) -> Any:
    scalars: tuple[Any, ...] = (
        None,
        True,
        False,
        0,
        1,
        -1,
        2,
        10**40,
        0.5,
        -0.0,
        float("nan"),
        float("inf"),
        "",
        "strict",
        "closed",
        SID,
        "\ud800",
        "x" * 300,
    )
    roll = rng.random()
    if depth > 3 or roll < 0.5:
        return rng.choice(scalars)
    if roll < 0.7:
        return [_shape(rng, depth + 1) for _ in range(rng.randrange(4))]
    keys = list(_PLANNED) + ["unknown", "lock", ""]
    return {
        rng.choice(keys): _shape(rng, depth + 1) for _ in range(rng.randrange(len(_PLANNED) + 3))
    }


_TYPES: dict[str, tuple[type, ...]] = {
    "session_id": (str,),
    "format": (int,),
    "in_fleet": (bool,),
    "created_at": (float, type(None)),
    "launched_at": (float, type(None)),
    "last_active": (float, type(None)),
    "ended_at": (float, type(None)),
    "ended_how": (str, type(None)),
    "task": (str,),
    "last_message": (str, type(None)),
    "model": (str,),
    "cwd": (str,),
    "session_base": (str, type(None)),
    "template": (str, type(None)),
    "brief": (str, type(None)),
    "policy": (Policy, UnknownPolicy),
    "contained": (bool,),
    "subagent_cap": (int, type(None)),
    "cost_usd": (float, type(None)),
    "error": (str, type(None)),
}


@pytest.mark.parametrize("seed", range(8))
def test_decode_is_total_and_a_record_it_gives_is_typed_as_declared(seed: int) -> None:
    rng = random.Random(seed)
    records = unreadable = 0
    for _ in range(1500):
        obj = _shape(rng)
        if rng.random() < 0.6 and not isinstance(obj, dict):
            obj = {key: _shape(rng, 2) for key in rng.sample(_PLANNED, rng.randrange(8))}
        found = sessionfile.decode(obj, session_id=SID)
        if isinstance(found, Unreadable):
            unreadable += 1
            assert found.reason and "\n" not in found.reason
            continue
        records += 1
        assert isinstance(found, SessionFile)
        assert isinstance(obj, dict)
        assert found.session_id == SID
        assert found.format >= 1
        assert found.ended_how in (None, *sessionfile.ENDED_HOW)
        for name, kinds in _TYPES.items():
            value = getattr(found, name)
            assert type(value) in kinds, (name, value)
            if isinstance(value, float):
                assert value == value and abs(value) != float("inf"), name
    # The generator reaches both answers, or the loop above proved half of this.
    assert records > 100 and unreadable > 100


# -- the property: what cannot be read is never replaced --------------------------

_UNREADABLE: dict[str, bytes] = {
    "empty": b"",
    "corrupt": b'{"task": "t", "in_fleet": true,}',
    "truncated": b'{"task": "t", "in_fl',
    "not text": b"\xff\xfe{}",
    "a list": b'[{"task": "t"}]',
    "text": b'"session"',
    "a number": b"7",
    "null": b"null",
    "nan": b'{"cost_usd": NaN, "task": "t"}',
    "in_fleet as text": b'{"in_fleet": "false", "task": "t"}',
    "in_fleet null": b'{"in_fleet": null}',
    "another id": b'{"session_id": "somebody-else", "task": "t"}',
    "format as text": b'{"format": "1", "task": "t"}',
    "format zero": b'{"format": 0}',
    "deeply nested": b'{"x": ' + b"[" * 100_000 + b"]" * 100_000 + b"}",
}


@pytest.mark.parametrize("case", sorted(_UNREADABLE))
def test_a_file_that_cannot_be_read_is_not_replaced(tmp_path: Path, case: str) -> None:
    path = _put(tmp_path, SID, _UNREADABLE[case])
    assert isinstance(sessionfile.read(SID, tmp_path), Unreadable)

    for changes in ({"in_fleet": True, "task": "mine"}, {"last_active": 5.0}, {}, _FULL):
        result = sessionfile.update(SID, changes, tmp_path)
        assert isinstance(result, Unwritten), result
        assert "left as it is" in result.reason

    assert path.read_bytes() == _UNREADABLE[case]
    assert _names(path.parent) == ["session.json"]


def test_a_file_a_newer_build_wrote_is_shown_and_not_replaced(tmp_path: Path) -> None:
    content = json.dumps(
        {"format": FORMAT + 1, "session_id": SID, "task": "t", "in_fleet": False, "v2": {"k": [1]}}
    ).encode()
    path = _put(tmp_path, SID, content)

    shown = _record(sessionfile.read(SID, tmp_path))
    assert (shown.task, shown.in_fleet, shown.writable) == ("t", False, False)

    result = sessionfile.update(SID, {"in_fleet": True}, tmp_path)
    assert isinstance(result, Unwritten)
    assert "newer build" in result.reason and str(FORMAT + 1) in result.reason
    assert path.read_bytes() == content
    assert _names(path.parent) == ["session.json"]


def test_a_refusal_for_one_session_does_not_reach_another(tmp_path: Path) -> None:
    _put(tmp_path, SID, b"{not json")
    assert isinstance(sessionfile.update(SID, {"task": "a"}, tmp_path), Unwritten)
    assert isinstance(sessionfile.update("other", {"task": "b"}, tmp_path), Written)
    assert _record(sessionfile.read("other", tmp_path)).task == "b"


# -- the property: a write keeps what it does not know ----------------------------


def test_a_write_keeps_every_key_it_does_not_know_nested_values_included(tmp_path: Path) -> None:
    later = {
        "owner": {"pid": 4242, "started": 1.5, "hosts": ["a", {"b": None}]},
        "tags": ["x", 2, False, None],
        "lock": None,
        "notes": "café 😀",
        "big": 10**30,
        "": "an empty key",
    }
    _put(tmp_path, SID, {"format": 1, "session_id": SID, "task": "t", "model": "m", **later})

    result = _written(sessionfile.update(SID, {"in_fleet": False, "model": "n"}, tmp_path))

    raw = _raw(tmp_path)
    for key, value in later.items():
        assert raw[key] == value, key
        assert type(raw[key]) is type(value), key
    assert raw == {
        "format": 1,
        "session_id": SID,
        "task": "t",
        "model": "n",
        "in_fleet": False,
        **later,
    }
    assert (result.record.in_fleet, result.record.model, result.record.task) == (False, "n", "t")


def test_a_lone_surrogate_in_a_kept_value_survives_the_write(tmp_path: Path) -> None:
    _put(tmp_path, SID, b'{"later": "\\ud800 half", "task": "\\udfff"}')
    _written(sessionfile.update(SID, {"model": "m"}, tmp_path))
    raw = _raw(tmp_path)
    assert raw["later"] == "\ud800 half" and raw["task"] == "\udfff"


def test_only_the_given_keys_change_and_a_mistyped_known_key_is_left_on_disk(
    tmp_path: Path,
) -> None:
    before = {"session_id": SID, "task": "t", "cost_usd": "a lot", "contained": 1, "cwd": "/w"}
    _put(tmp_path, SID, before)

    result = _written(sessionfile.update(SID, {"last_active": 9}, tmp_path))

    assert _raw(tmp_path) == {**before, "format": FORMAT, "last_active": 9.0}
    assert type(_raw(tmp_path)["last_active"]) is float
    assert result.record.cost_usd is None and result.record.contained is False


def test_an_update_with_no_changes_creates_a_file_that_says_only_what_is_known(
    tmp_path: Path,
) -> None:
    result = _written(sessionfile.update(SID, {}, tmp_path))
    assert _raw(tmp_path) == {"format": FORMAT, "session_id": SID}
    assert result.record == SessionFile(session_id=SID)
    assert stat.S_IMODE(os.stat(result.path).st_mode) == 0o600


def test_a_null_clears_a_key_that_may_be_null(tmp_path: Path) -> None:
    _written(sessionfile.update(SID, _FULL, tmp_path))
    cleared = {"ended_at": None, "ended_how": None, "error": None, "last_message": None}
    result = _written(sessionfile.update(SID, cleared, tmp_path))
    assert result.skipped == ()
    for key in cleared:
        assert _raw(tmp_path)[key] is None
        assert getattr(result.record, key) is None


# -- task and last_message ---------------------------------------------------------


def test_the_task_is_the_first_launchs_and_is_never_overwritten(tmp_path: Path) -> None:
    first = _written(sessionfile.update(SID, {"task": "the first launch"}, tmp_path))
    assert first.skipped == () and first.record.task == "the first launch"

    second = _written(sessionfile.update(SID, {"task": "a resume's text", "model": "m2"}, tmp_path))

    assert second.skipped == ("task",)
    assert second.record.task == "the first launch" and second.record.model == "m2"
    assert _raw(tmp_path)["task"] == "the first launch"


@pytest.mark.parametrize("held", [{}, {"task": ""}, {"task": 5}, {"task": None}])
def test_a_task_is_taken_while_the_file_has_none(tmp_path: Path, held: dict[str, Any]) -> None:
    _put(tmp_path, SID, {"session_id": SID, **held})
    result = _written(sessionfile.update(SID, {"task": "now there is one"}, tmp_path))
    assert result.skipped == ()
    assert _raw(tmp_path)["task"] == "now there is one"


def test_a_last_message_over_the_cap_is_not_stored_and_the_old_one_stays(tmp_path: Path) -> None:
    assert MAX_LAST_MESSAGE == 24_000
    _written(sessionfile.update(SID, {"last_message": "short"}, tmp_path))

    at_the_cap = "m" * MAX_LAST_MESSAGE
    kept = _written(sessionfile.update(SID, {"last_message": at_the_cap}, tmp_path))
    assert kept.skipped == () and _raw(tmp_path)["last_message"] == at_the_cap

    over = _written(
        sessionfile.update(SID, {"last_message": at_the_cap + "!", "cwd": "/w"}, tmp_path)
    )
    assert over.skipped == ("last_message",)
    assert over.record.last_message == at_the_cap
    assert _raw(tmp_path)["last_message"] == at_the_cap
    assert _raw(tmp_path)["cwd"] == "/w"


def test_a_last_message_over_the_cap_on_a_new_file_leaves_the_key_out(tmp_path: Path) -> None:
    result = _written(sessionfile.update(SID, {"last_message": "m" * 24_001}, tmp_path))
    assert result.skipped == ("last_message",)
    assert "last_message" not in _raw(tmp_path)
    assert result.record.last_message is None


# -- what update refuses to be given ----------------------------------------------


@pytest.mark.parametrize(
    "changes",
    [
        {"in_fleet": "false"},
        {"in_fleet": None},
        {"in_fleet": 0},
        {"policy": "root"},
        {"policy": UnknownPolicy("strict")},
        {"policy": None},
        {"created_at": True},
        {"created_at": None},
        {"created_at": "now"},
        {"last_active": float("nan")},
        {"cost_usd": float("inf")},
        {"ended_how": "killed"},
        {"subagent_cap": 1.5},
        {"subagent_cap": True},
        {"task": None},
        {"model": 5},
        {"contained": "yes"},
        {"session_id": SID},
        {"session_id": "another"},
        {"format": FORMAT},
        {"format": FORMAT + 1},
        {"owner": 1},
        {"Task": "t"},
        {"task": "good", "lock": {"pid": 1}},
    ],
)
def test_a_change_the_file_could_not_hold_is_refused_and_nothing_is_made(
    tmp_path: Path, changes: dict[str, Any]
) -> None:
    result = sessionfile.update(SID, changes, tmp_path)
    assert isinstance(result, Unwritten), result
    assert any(repr(key) in result.reason for key in changes)
    assert not (tmp_path / SID).exists()

    path = _put(tmp_path, SID, {"session_id": SID, "task": "t"})
    held = path.read_bytes()
    assert isinstance(sessionfile.update(SID, changes, tmp_path), Unwritten)
    assert path.read_bytes() == held
    assert _names(path.parent) == ["session.json"]


@pytest.mark.parametrize("key", ["session_id", "format"])
def test_the_refusal_of_a_fixed_key_is_not_the_refusal_of_a_misspelled_one(
    tmp_path: Path, key: str
) -> None:
    fixed = sessionfile.update(SID, {key: 1}, tmp_path)
    misspelled = sessionfile.update(SID, {key + "s": 1}, tmp_path)
    assert isinstance(fixed, Unwritten) and isinstance(misspelled, Unwritten)
    assert "the directory and the build decide it" in fixed.reason
    assert "not a key of session.json" in misspelled.reason


@pytest.mark.parametrize("rung", list(Policy))
def test_a_policy_is_given_as_the_rung_or_its_value_and_stored_as_text(
    tmp_path: Path, rung: Policy
) -> None:
    assert _written(sessionfile.update("a", {"policy": rung}, tmp_path)).record.policy is rung
    assert _written(sessionfile.update("b", {"policy": rung.value}, tmp_path)).record.policy is rung
    assert _raw(tmp_path, "a")["policy"] == _raw(tmp_path, "b")["policy"] == rung.value


def test_a_result_over_the_size_a_read_takes_is_refused(tmp_path: Path) -> None:
    path = _put(tmp_path, SID, {"session_id": SID, "task": "t"})
    held = path.read_bytes()

    result = sessionfile.update(SID, {"error": "e" * (MAX_FILE_BYTES + 1)}, tmp_path)

    assert isinstance(result, Unwritten) and "bytes" in result.reason
    assert path.read_bytes() == held
    assert _names(path.parent) == ["session.json"]
    # And what update does write, read takes: the two limits are one number.
    fits = "e" * (MAX_FILE_BYTES - 200)
    assert isinstance(sessionfile.update(SID, {"error": fits}, tmp_path), Written)
    assert _record(sessionfile.read(SID, tmp_path)).error == fits


# -- things under the name that are not a file ------------------------------------


def _valid() -> bytes:
    return json.dumps({"session_id": SID, "task": "t"}).encode()


def test_a_fifo_under_the_name_is_unreadable_at_once_and_left_alone(tmp_path: Path) -> None:
    directory = tmp_path / SID
    directory.mkdir()
    os.mkfifo(directory / "session.json")

    # An ordinary open of a FIFO waits for a writer; these return.
    assert isinstance(sessionfile.read(SID, tmp_path), Unreadable)
    # And one somebody is feeding a well-formed file through is still not a file.
    feed = os.open(directory / "session.json", os.O_RDWR | os.O_NONBLOCK)
    try:
        os.write(feed, _valid())
        fed = sessionfile.read(SID, tmp_path)
        assert isinstance(fed, Unreadable) and "not a regular file" in fed.reason
    finally:
        os.close(feed)
    assert isinstance(sessionfile.update(SID, {"task": "t"}, tmp_path), Unwritten)
    assert sessionfile.listing(tmp_path).sessions[0][0] == SID
    assert isinstance(sessionfile.listing(tmp_path).sessions[0][1], Unreadable)

    assert stat.S_ISFIFO(os.lstat(directory / "session.json").st_mode)
    assert _names(directory) == ["session.json"]


@pytest.mark.parametrize("dangling", [False, True])
def test_a_link_under_the_name_is_not_followed_and_not_replaced(
    tmp_path: Path, dangling: bool
) -> None:
    elsewhere = tmp_path / "elsewhere.json"
    if not dangling:
        elsewhere.write_bytes(_valid())
    root = tmp_path / "sessions"
    (root / SID).mkdir(parents=True)
    link = root / SID / "session.json"
    link.symlink_to(elsewhere)

    assert isinstance(sessionfile.read(SID, root), Unreadable)
    result = sessionfile.update(SID, {"model": "m"}, root)
    assert isinstance(result, Unwritten) and "left as it is" in result.reason

    assert link.is_symlink()
    assert _names(root / SID) == ["session.json"]
    if dangling:
        assert not elsewhere.exists()
    else:
        assert elsewhere.read_bytes() == _valid()


def test_a_file_over_the_size_cap_is_unreadable_and_not_replaced(tmp_path: Path) -> None:
    padded = json.dumps({"session_id": SID, "task": "t", "pad": "p" * MAX_FILE_BYTES}).encode()
    path = _put(tmp_path, SID, padded)

    found = sessionfile.read(SID, tmp_path)
    assert isinstance(found, Unreadable) and str(MAX_FILE_BYTES) in found.reason
    assert isinstance(sessionfile.update(SID, {"model": "m"}, tmp_path), Unwritten)
    assert path.read_bytes() == padded


def test_a_file_exactly_at_the_size_cap_is_read(tmp_path: Path) -> None:
    frame = json.dumps({"session_id": SID, "pad": ""}).encode()
    padded = json.dumps({"session_id": SID, "pad": "p" * (MAX_FILE_BYTES - len(frame))}).encode()
    assert len(padded) == MAX_FILE_BYTES
    _put(tmp_path, SID, padded)
    assert isinstance(sessionfile.read(SID, tmp_path), SessionFile)


def test_a_directory_under_the_name_is_unreadable_and_kept(tmp_path: Path) -> None:
    (tmp_path / SID / "session.json").mkdir(parents=True)
    (tmp_path / SID / "session.json" / "inside").write_text("x")

    assert isinstance(sessionfile.read(SID, tmp_path), Unreadable)
    assert isinstance(sessionfile.update(SID, {"task": "t"}, tmp_path), Unwritten)
    assert (tmp_path / SID / "session.json" / "inside").read_text() == "x"
    assert _names(tmp_path / SID) == ["session.json"]


def test_nothing_there_is_absent_whether_or_not_the_directory_is(tmp_path: Path) -> None:
    assert sessionfile.read(SID, tmp_path) == Absent()
    assert sessionfile.read(SID, tmp_path / "no" / "such" / "root") == Absent()
    (tmp_path / SID / "board").mkdir(parents=True)
    assert sessionfile.read(SID, tmp_path) == Absent()
    assert not (tmp_path / SID / "session.json").exists()


def test_a_file_where_the_session_directory_would_be_is_not_written_over(tmp_path: Path) -> None:
    (tmp_path / SID).write_text("not a directory")
    assert sessionfile.read(SID, tmp_path) == Absent()
    assert isinstance(sessionfile.update(SID, {"task": "t"}, tmp_path), Unwritten)
    assert (tmp_path / SID).read_text() == "not a directory"
    assert sessionfile.listing(tmp_path) == Listing()


# -- ids ---------------------------------------------------------------------------

_NOT_IDS = (
    "",
    ".",
    "..",
    "../x",
    "a/b",
    "/etc",
    "/",
    ".hidden",
    "a\0b",
    "a\\b",
    "x" * 256,
    "\ud800",
)


@pytest.mark.parametrize("session_id", _NOT_IDS)
def test_an_id_that_is_not_one_directory_name_has_no_path_and_no_file(
    tmp_path: Path, session_id: str
) -> None:
    root = tmp_path / "sessions"
    beside = tmp_path / "x"
    assert sessionfile.session_dir(session_id, root) is None
    assert sessionfile.file_path(session_id, root) is None
    assert sessionfile.board_dir(session_id, root) is None

    found = sessionfile.read(session_id, root)
    assert isinstance(found, Unreadable) and "not a session id" in found.reason
    result = sessionfile.update(session_id, {"task": "t"}, root)
    assert isinstance(result, Unwritten) and "not a session id" in result.reason

    assert not root.exists() and not beside.exists()
    assert _names(tmp_path) == []


def test_the_paths_of_an_id_are_inside_its_own_directory(tmp_path: Path) -> None:
    assert sessionfile.session_dir(SID, tmp_path) == tmp_path / SID
    assert sessionfile.file_path(SID, tmp_path) == tmp_path / SID / "session.json"
    assert sessionfile.board_dir(SID, tmp_path) == tmp_path / SID / "board"
    # The same rule as the board log's, by being the same function.
    for session_id in (*_NOT_IDS, SID, "a b", "café"):
        assert sessionfile.session_dir(session_id, tmp_path) == boardlog.session_dir(
            tmp_path, session_id
        )


# -- listing -----------------------------------------------------------------------


def test_a_listing_is_the_session_directories_and_nothing_else(tmp_path: Path) -> None:
    good = _put(tmp_path, "b-good", {"session_id": "b-good", "task": "t"})
    (good.parent / "board").mkdir()
    (good.parent / "board" / "00000000.json").write_text("{}")
    (good.parent / "lock").write_text("4242 1.5")
    (good.parent / ".session-left.tmp").write_text("{")
    _put(tmp_path, "c-corrupt", b"{")
    (tmp_path / "a-board-only" / "board").mkdir(parents=True)
    _put(tmp_path, "d-detached", {"in_fleet": False})
    _put(tmp_path, "e-newer", {"format": FORMAT + 1})

    (tmp_path / "sessions.json").write_text("{}")
    (tmp_path / "stray").write_text("a file, not a session")
    _put(tmp_path, ".cache", {"task": "hidden"})
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    _put(outside, "linked", {"session_id": "linked", "task": "elsewhere"})
    (tmp_path / "linked").symlink_to(outside / "linked", target_is_directory=True)
    os.mkfifo(tmp_path / "pipe")

    found = sessionfile.listing(tmp_path)

    assert found.problem is None
    assert [session_id for session_id, _ in found.sessions] == [
        "a-board-only",
        "b-good",
        "c-corrupt",
        "d-detached",
        "e-newer",
    ]
    by_id = dict(found.sessions)
    assert by_id["a-board-only"] == Absent()
    assert by_id["b-good"] == SessionFile(session_id="b-good", task="t")
    assert isinstance(by_id["c-corrupt"], Unreadable)
    assert _record(by_id["d-detached"]).in_fleet is False
    assert _record(by_id["e-newer"]).writable is False
    for session_id, answer in found.sessions:
        assert answer == sessionfile.read(session_id, tmp_path)


def test_a_root_that_is_not_there_lists_nothing_and_is_not_made(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    assert sessionfile.listing(root) == Listing()
    assert not root.exists()


def test_a_root_that_cannot_be_listed_says_so_and_is_not_an_empty_fleet(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    root.write_text("a file")
    found = sessionfile.listing(root)
    assert found.sessions == () and found.problem is not None
    assert "NotADirectoryError" in found.problem


def test_a_listing_takes_no_more_than_its_cap_and_says_there_were_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for n in range(6):
        _put(tmp_path, f"s{n}", {"task": str(n)})
    monkeypatch.setattr(sessionfile, "MAX_SESSIONS", 6)
    assert sessionfile.listing(tmp_path).problem is None
    assert len(sessionfile.listing(tmp_path).sessions) == 6

    monkeypatch.setattr(sessionfile, "MAX_SESSIONS", 4)
    capped = sessionfile.listing(tmp_path)
    assert len(capped.sessions) == 4
    assert capped.problem is not None and "4" in capped.problem


# -- atomicity ---------------------------------------------------------------------


def _refuse(*_args: object, **_kwargs: object) -> None:
    raise OSError(28, "No space left on device")


def test_a_rename_that_fails_leaves_the_old_file_whole_and_no_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _written(sessionfile.update(SID, _FULL, tmp_path))
    path = tmp_path / SID / "session.json"
    held = path.read_bytes()

    monkeypatch.setattr(os, "replace", _refuse)
    monkeypatch.setattr(os, "rename", _refuse)
    result = sessionfile.update(SID, {"in_fleet": True, "model": "other"}, tmp_path)

    assert isinstance(result, Unwritten) and "No space left" in result.reason
    assert path.read_bytes() == held
    assert _names(path.parent) == ["session.json"]


def test_a_first_write_that_cannot_be_named_leaves_no_file_and_no_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "link", _refuse)
    result = sessionfile.update(SID, {"task": "t"}, tmp_path)
    assert isinstance(result, Unwritten) and "No space left" in result.reason
    assert _names(tmp_path / SID) == []


def test_a_write_that_fails_before_it_is_named_leaves_the_old_file_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _written(sessionfile.update(SID, {"task": "t"}, tmp_path))
    path = tmp_path / SID / "session.json"
    held = path.read_bytes()

    monkeypatch.setattr(os, "fsync", _refuse)
    assert isinstance(sessionfile.update(SID, {"model": "m"}, tmp_path), Unwritten)
    assert path.read_bytes() == held
    assert _names(path.parent) == ["session.json"]


def test_the_file_is_whole_and_flushed_beside_its_name_before_it_takes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _written(sessionfile.update(SID, {"task": "t"}, tmp_path))
    path = tmp_path / SID / "session.json"
    steps: list[Any] = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd: int) -> None:
        steps.append("fsync")
        real_fsync(fd)

    def replace(src: Any, dst: Any) -> None:
        # What is about to take the name is already everything the file will be.
        steps.append(("replace", Path(src).parent, Path(dst), json.loads(Path(src).read_bytes())))
        real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)
    _written(sessionfile.update(SID, {"model": "m"}, tmp_path))

    assert steps == [
        "fsync",
        ("replace", path.parent, path, {"format": 1, "session_id": SID, "task": "t", "model": "m"}),
    ]


def test_a_file_created_under_a_first_write_is_merged_into_not_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / SID / "session.json"
    real_link = os.link
    calls = 0

    def link(src: Any, dst: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            # Another window's first write lands between this one's read and its link.
            path.write_bytes(json.dumps({"task": "theirs", "in_fleet": False, "v2": [1]}).encode())
        real_link(src, dst)

    monkeypatch.setattr(os, "link", link)
    result = _written(sessionfile.update(SID, {"task": "mine", "model": "m"}, tmp_path))

    assert result.skipped == ("task",)
    assert _raw(tmp_path) == {
        "format": FORMAT,
        "session_id": SID,
        "task": "theirs",
        "in_fleet": False,
        "v2": [1],
        "model": "m",
    }
    assert _names(path.parent) == ["session.json"]


def test_an_unreadable_file_created_under_a_first_write_is_not_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / SID / "session.json"
    real_link = os.link

    def link(src: Any, dst: Any) -> None:
        if not path.exists():
            path.write_bytes(b"{half a file")
        real_link(src, dst)

    monkeypatch.setattr(os, "link", link)
    result = sessionfile.update(SID, {"task": "mine"}, tmp_path)

    assert isinstance(result, Unwritten) and "left as it is" in result.reason
    assert path.read_bytes() == b"{half a file"
    assert _names(path.parent) == ["session.json"]


def test_a_name_that_keeps_being_taken_is_given_up_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def link(src: Any, dst: Any) -> None:
        raise FileExistsError(17, "File exists")

    monkeypatch.setattr(os, "link", link)
    result = sessionfile.update(SID, {"task": "mine"}, tmp_path)
    assert isinstance(result, Unwritten) and "another writer" in result.reason
    assert _names(tmp_path / SID) == []


def test_a_reader_never_sees_a_torn_file_while_two_writers_take_turns(tmp_path: Path) -> None:
    _written(sessionfile.update(SID, {"task": "t"}, tmp_path))
    done = threading.Event()
    seen: list[object] = []

    def reader() -> None:
        while not done.is_set():
            found = sessionfile.read(SID, tmp_path)
            if not isinstance(found, SessionFile) or found.task != "t":
                seen.append(found)

    def writer(key: str) -> None:
        for n in range(60):
            result = sessionfile.update(SID, {key: float(n), "error": "e" * (n * 400)}, tmp_path)
            if not isinstance(result, Written):
                seen.append(result)

    watching = threading.Thread(target=reader)
    writers = [threading.Thread(target=writer, args=(k,)) for k in ("last_active", "cost_usd")]
    watching.start()
    for thread in writers:
        thread.start()
    for thread in writers:
        thread.join(timeout=120)
    done.set()
    watching.join(timeout=30)

    assert seen == []
    assert _names(tmp_path / SID) == ["session.json"]
    final = _record(sessionfile.read(SID, tmp_path))
    assert final.task == "t" and 59.0 in (final.last_active, final.cost_usd)


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes to a read-only directory")
def test_a_directory_that_cannot_be_written_is_a_reason_not_a_raise(tmp_path: Path) -> None:
    path = _put(tmp_path, SID, {"session_id": SID, "task": "t"})
    held = path.read_bytes()
    path.parent.chmod(0o500)
    try:
        result = sessionfile.update(SID, {"model": "m"}, tmp_path)
        assert isinstance(result, Unwritten) and "PermissionError" in result.reason
        assert path.read_bytes() == held
    finally:
        path.parent.chmod(0o700)


# -- nothing here reaches the config directory unless it is asked to ---------------

_PUBLIC = sorted(
    name
    for name, value in vars(sessionfile).items()
    if inspect.isfunction(value)
    and value.__module__ == sessionfile.__name__
    and not name.startswith("_")
)


def test_every_public_function_that_touches_a_path_takes_a_root() -> None:
    assert _PUBLIC == [
        "board_dir",
        "decode",
        "file_path",
        "listing",
        "read",
        "root",
        "session_dir",
        "update",
    ]
    for name in _PUBLIC:
        parameters = inspect.signature(getattr(sessionfile, name)).parameters
        if name == "root":
            assert not parameters
        elif name == "decode":
            # Pure: a value in, a value out, and no path.
            assert "root" not in parameters
        else:
            assert parameters["root"].default is None, name


def test_a_given_root_is_the_only_directory_asked_about(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never() -> Path:
        raise AssertionError("the config directory was asked for although a root was given")

    monkeypatch.setattr(settings, "config_dir", never)
    monkeypatch.setattr(Path, "home", never)

    assert sessionfile.session_dir(SID, tmp_path) == tmp_path / SID
    assert sessionfile.file_path(SID, tmp_path) is not None
    assert sessionfile.board_dir(SID, tmp_path) is not None
    assert isinstance(sessionfile.update(SID, {"task": "t"}, tmp_path), Written)
    assert isinstance(sessionfile.read(SID, tmp_path), SessionFile)
    assert len(sessionfile.listing(tmp_path).sessions) == 1


def test_the_default_root_follows_the_config_directory_at_call_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The redirect a fixture makes: the function on the module, replaced after
    # this module was imported. A name bound by from-import would miss it.
    monkeypatch.setattr(settings, "config_dir", lambda: tmp_path / "one")
    assert sessionfile.root() == tmp_path / "one" / "sessions"
    assert isinstance(sessionfile.update(SID, {"task": "t"}), Written)
    assert (tmp_path / "one" / "sessions" / SID / "session.json").is_file()
    assert _record(sessionfile.read(SID)).task == "t"
    assert [session_id for session_id, _ in sessionfile.listing().sessions] == [SID]
    assert sessionfile.board_dir(SID) == tmp_path / "one" / "sessions" / SID / "board"

    monkeypatch.setattr(settings, "config_dir", lambda: tmp_path / "two")
    assert sessionfile.root() == tmp_path / "two" / "sessions"
    assert sessionfile.read(SID) == Absent()


def test_a_config_directory_that_cannot_be_found_is_a_reason_not_a_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def lost() -> Path:
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(settings, "config_dir", lost)
    assert isinstance(sessionfile.read(SID), Unreadable)
    assert isinstance(sessionfile.update(SID, {"task": "t"}), Unwritten)
    assert sessionfile.listing().problem is not None


_IMPORT_ONLY = """
import pathlib, sys
import pptmstr.settings as settings

def never(*args, **kwargs):
    raise SystemExit("a path was computed while pptmstr.sessionfile was imported")

settings.config_dir = never
pathlib.Path.home = never
import pptmstr.sessionfile as sessionfile
paths = [n for n, v in vars(sessionfile).items() if isinstance(v, pathlib.PurePath)]
sys.exit(f"module-level paths: {paths}" if paths else 0)
"""


def test_importing_the_module_computes_no_path() -> None:
    source = str(Path(sessionfile.__file__).resolve().parents[1])
    done = subprocess.run(
        [sys.executable, "-c", _IMPORT_ONLY],
        cwd=source,
        env={**os.environ, "PYTHONPATH": source},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
