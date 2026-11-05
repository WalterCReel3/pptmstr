"""
Where a declaration's request came from (``model.Provenance``): how its
arguments are read, and that it survives the board log.

The acceptance for round one, item 3, is the round trip: a task declared with a
provenance, written to a log as text, read back and restored into a fresh store
is the task declared. The reading of a bus call's arguments is
``tests/test_bus.py``'s; the parked row's is ``tests/test_approval.py``'s; the
record's field accounting is ``tests/test_boardlog.py``'s.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from pptmstr import boardlog, drawable
from pptmstr.boardfacts import Fact, outcomes
from pptmstr.boardlog import Clock, RestoredTask
from pptmstr.intents import AgentSpawned, BoardRestored, Intent, TaskAmended, TaskDeclared
from pptmstr.model import (
    MAX_CHANNEL_CHARS,
    Charter,
    NodeId,
    NotPlaced,
    Provenance,
    Snapshot,
    Task,
    Unplaced,
    is_concern_id,
    stated_provenance,
)
from pptmstr.store import _apply

S = "s"
LEAD: NodeId = (S, None)
# The log is written on this reading and read back on it, so a restored time is
# the stamp the first store gave it and a task compares equal whole.
_CLOCK = Clock(wall=10_000.0, monotonic=50.0)
# A peer message's id, in the shape the bus mints.
MSG = "c-0123456789ab"


def started() -> Snapshot:
    snap, _ = _apply(Snapshot.empty(), AgentSpawned(LEAD, None, "t", "m", 0.0), 0.0)
    return snap


def apply(snap: Snapshot, *intents: Intent, now: float = 5.0) -> tuple[Snapshot, list[Fact]]:
    facts: list[Fact] = []
    for intent in intents:
        after, _ = _apply(snap, intent, now)
        facts.extend(outcomes(snap, intent, after, now))
        snap = after
    return snap, facts


def through_the_disk(tmp_path: Path, facts: list[Fact]) -> boardlog.Board:
    """
    The board the session's facts restore to, by way of files: each record is
    encoded, written, read and decoded, so a codec that drops or bends a field
    is in the path.
    """
    for fact in facts:
        assert fact.session_id == S
        written = boardlog.append(tmp_path, fact.on_the_wall(_CLOCK), durable=False)
        assert isinstance(written, boardlog.Written), written
    log = boardlog.read(tmp_path)
    assert log.damage is None
    return boardlog.restore_log(log)


def declared(task_id: str = "t-1", **fields: object) -> TaskDeclared:
    task = Task(id=task_id, title="make the hero shot", detail="spec", declared_at=2.0)
    return TaskDeclared(dataclasses.replace(task, **fields), node_id=LEAD)  # type: ignore[arg-type]


# -- reading what a declarer said ---------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "read"),
    [
        (None, Charter.UNSTATED),
        ("", Charter.UNSTATED),
        ("  ", Charter.UNSTATED),
        ("unstated", Charter.UNSTATED),
        (True, Charter.YES),
        ("YES", Charter.YES),
        (" inside ", Charter.YES),
        ("true", Charter.YES),
        (False, Charter.NO),
        ("No", Charter.NO),
        ("outside", Charter.NO),
        ("false", Charter.NO),
    ],
)
def test_a_charter_mark_is_yes_no_or_not_said(raw: object, read: Charter) -> None:
    assert Charter.stated(raw) is read


@pytest.mark.parametrize("raw", ["none", "null", "0", "1", "maybe", 0, 1, ["yes"]])
def test_a_charter_mark_that_is_none_of_the_three_is_refused_and_not_guessed(raw: object) -> None:
    """
    "none" and "null" are no to ``stated_flag``, which claims nothing there. Here
    no is a statement -- the request is outside the charter -- so reading them
    as one would put it in the declarer's mouth.
    """
    with pytest.raises(ValueError, match="is not one of yes, no or unstated"):
        Charter.stated(raw)


def test_an_unknown_mark_is_repeated_only_while_it_is_short() -> None:
    """
    The refusal reaches the agent and DETAIL's one unwrapped line, so a long
    argument is named by its length, as an over-long task_id is.
    """
    with pytest.raises(ValueError) as short:
        Charter.stated("mostly")
    with pytest.raises(ValueError) as long:
        Charter.stated("x" * 100_000)

    assert "'mostly'" in str(short.value)
    assert str(long.value) == (
        "inside_charter (a value of 100000 characters, not repeated here) is not one of "
        "yes, no or unstated. Say yes or no, or omit it."
    )


def test_every_mark_is_truthy_so_unstated_cannot_read_as_no() -> None:
    assert all(Charter) and [c.value for c in Charter] == ["yes", "no", "unstated"]


def test_a_provenance_says_where_it_came_from_in_one_line() -> None:
    assert Provenance("brand", MSG, Charter.YES).said() == (
        f"requested on #brand, re {MSG}, inside charter: yes"
    )
    assert Provenance("brand").said() == "requested on #brand, inside charter: unstated"


def test_there_is_one_spelling_of_no_provenance() -> None:
    """
    An empty channel would be a second way to say "none": a reader asking
    ``provenance is None`` would miss it.
    """
    with pytest.raises(ValueError):
        Provenance("")
    assert stated_provenance(None, None, None) is None
    assert stated_provenance(" ", "", "") is None
    assert stated_provenance(None, None, "unstated") is None


def test_arguments_read_as_a_provenance_are_trimmed_and_their_mark_read() -> None:
    assert stated_provenance(" brand ", f" {MSG} ", "no") == Provenance("brand", MSG, Charter.NO)


@pytest.mark.parametrize(
    ("channel", "re", "inside", "why"),
    [
        (None, MSG, None, "no channel was named"),
        ("", None, "yes", "no channel was named"),
        (["brand"], None, None, "channel is not text"),
        ("brand", 7, None, "re is not text"),
        ("brand", None, "maybe", "is not one of yes, no or unstated"),
    ],
)
def test_arguments_that_are_no_provenance_are_refused_with_the_reason(
    channel: object, re: object, inside: object, why: str
) -> None:
    with pytest.raises(ValueError, match=why):
        stated_provenance(channel, re, inside)


@pytest.mark.parametrize(
    "channel",
    [
        "ops, inside charter: yes",
        "a\tb",
        "a\nb",
        ".hidden",
        "-brand",
        "_brand",
        "a/b",
        "a\\b",
        "a\x00b",
        "\ud800",
        "brand\udc80",
        "Brand",
        "BRAND",
        "bra\u200bnd",
        "brand\u2060",
        "ops\x1b[31m",
        "ops\x7f",
        "br\u0430nd",
        "caf\u00e9",
        "c" * (MAX_CHANNEL_CHARS + 1),
    ],
    ids=[
        "spaced",
        "tab",
        "newline",
        "leading-dot",
        "leading-dash",
        "leading-underscore",
        "slash",
        "backslash",
        "nul",
        "high-surrogate",
        "low-surrogate",
        "capital",
        "capitals",
        "zero-width-space",
        "word-joiner",
        "ansi-escape",
        "delete",
        "cyrillic-a",
        "accented",
        "over-64",
    ],
)
def test_a_channel_is_a_plain_name(channel: str) -> None:
    """
    A channel is named by the operator and becomes a directory, so its name is
    an identity, and nothing may draw like another name: lowercase ASCII
    letters and digits, ``.``, ``_`` and ``-``, a letter or digit first, 64 at
    most. A capital is refused rather than folded, so ``Brand`` is never
    accepted as a spelling of ``brand``. The name is not repeated in the
    refusal.
    """
    with pytest.raises(ValueError, match="channel is") as refused:
        stated_provenance(channel, None, None)
    assert channel not in str(refused.value)


def test_the_channel_rule_matches_the_whole_name() -> None:
    """
    ``stated_provenance`` trims before the rule runs, so this asks the rule
    itself: a pattern anchored with ``$`` would pass a name ending in a newline.
    """
    from pptmstr.model import _channel_refusal

    assert _channel_refusal("brand") is None
    assert _channel_refusal("brand\n") is not None


_NAMES = (
    "brand",
    "brand-2.v_x",
    "0",
    "a.b.",
    "a..b",
    "brand-2.v#x",
    "caf\u00e9",
    "Brand",
    "ops,inside_charter:yes",
    "",
    ".hidden",
    "-a",
    "a/b",
    "a\\b",
    "a\x00b",
    "a b",
    "a\tb",
    "\u00a0",
    "\u200b",
    "ops\x1b[31m",
    "\ud800",
    "brand\udc80",
    "\udcff",
    "\u00e9" * 127,
    "\u00e9" * 128,
    "c" * MAX_CHANNEL_CHARS,
    "c" * (MAX_CHANNEL_CHARS + 1),
    "c" * 255,
    "c" * 256,
)


@pytest.mark.parametrize("name", _NAMES)
def test_every_channel_name_is_a_name_a_directory_can_have(name: str) -> None:
    """
    ``model._channel_refusal`` does not import ``requests.address_refusal``
    (``requests`` imports ``model``), and is narrower than it by construction.
    This keeps the narrowing true: whatever the directory rule refuses, the
    channel rule refuses, and every name the channel rule takes is its own
    drawable form, so the name checked is the name stored and logged. The empty
    name is refused before either runs: a blank channel is no channel.
    """
    from pptmstr import requests
    from pptmstr.model import _channel_refusal

    if not name:
        assert stated_provenance(name, None, None) is None
        return
    if _channel_refusal(name) is None:
        assert requests.address_refusal(name) is None
        assert drawable.text(name) == name
        assert name.isascii() and name == name.lower()


def test_a_plain_name_no_longer_than_64_is_a_channel() -> None:
    assert stated_provenance("brand-2.v_x", None, None) == Provenance("brand-2.v_x")
    assert stated_provenance("0", None, None) == Provenance("0")
    assert stated_provenance("c" * MAX_CHANNEL_CHARS, None, None) is not None
    with pytest.raises(ValueError, match=f"65 characters and the limit is {MAX_CHANNEL_CHARS}"):
        stated_provenance("c" * (MAX_CHANNEL_CHARS + 1), None, None)


@pytest.mark.parametrize(
    "re", ["c-7", "c-0123456789AB", "c-0123456789abc", "d-0123456789ab", "x" * 10_000]
)
def test_re_is_refused_unless_it_is_the_shape_of_a_concern_id(re: str) -> None:
    assert not is_concern_id(re)
    with pytest.raises(ValueError, match="re is not a message id") as refused:
        stated_provenance("brand", re, None)
    assert len(str(refused.value)) < 200


# -- the log ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "provenance",
    [
        Provenance("brand", MSG, Charter.YES),
        Provenance("brand", None, Charter.NO),
        Provenance("brand-2.v_x", None, Charter.UNSTATED),
        None,
    ],
    ids=["whole", "no-message", "punctuated", "none"],
)
def test_a_task_declared_with_a_provenance_is_the_one_restored_from_its_log(
    tmp_path: Path, provenance: Provenance | None
) -> None:
    """
    The acceptance. Declared, amended -- which leaves where the request came
    from alone -- written to disk, read back and restored into a fresh store:
    the task compares equal whole, provenance included. Only a provenance the
    bus takes: a plain channel name, which is its own drawable form, and that is
    what makes the name checked, the name stored and the name logged one string.
    """
    first, facts = apply(
        started(),
        declared(provenance=provenance),
        TaskAmended("t-1", "spec, corrected", node_id=LEAD),
    )
    landed = first.tasks["t-1"]
    assert landed.version == 2 and not drawable.undrawable(landed)
    if provenance is not None:
        assert landed.provenance is not None
        assert (landed.provenance.re is None) is (provenance.re is None)
        assert landed.provenance.inside_charter is provenance.inside_charter
    else:
        assert landed.provenance is None

    board = through_the_disk(tmp_path, facts)
    fresh, _ = _apply(Snapshot.empty(), BoardRestored(S, board, _CLOCK), _CLOCK.monotonic)

    assert fresh.tasks["t-1"] == landed
    report = fresh.restores[S]
    assert report.tasks == 1 and report.unplaced == () and report.adoptable


def test_the_store_that_wrote_a_provenance_takes_its_log_back_as_the_same_board(
    tmp_path: Path,
) -> None:
    """
    Restored over the store that wrote it, the log is the board that store
    holds: a provenance carried differently would read as a different board
    and withhold the whole restore (``STORE_DIFFERS``).
    """
    first, facts = apply(started(), declared(provenance=Provenance("brand", MSG, Charter.NO)))
    board = through_the_disk(tmp_path, facts)

    again, _ = _apply(first, BoardRestored(S, board, _CLOCK), _CLOCK.monotonic)

    assert again.tasks == first.tasks
    report = again.restores[S]
    assert report.unplaced == () and report.tasks == 1 and not report.refused


def test_a_declaration_with_no_provenance_logs_none(tmp_path: Path) -> None:
    _, facts = apply(started(), declared())

    (record,) = [boardlog.decode(boardlog.encode(f.on_the_wall(_CLOCK))) for f in facts]

    assert isinstance(record, boardlog.Declared)
    assert (record.channel, record.re, record.inside_charter) == (None, None, None)


# -- what a restore refuses ---------------------------------------------------------


def _logged(**fields: object) -> RestoredTask:
    base = RestoredTask(
        id="t-1",
        title="T",
        declared_by=LEAD,
        versions=(boardlog.TaskVersion(_CLOCK.wall - 5.0, "spec", LEAD),),
    )
    return dataclasses.replace(base, **fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "fields",
    [
        {"re": MSG},
        {"inside_charter": "yes"},
        {"inside_charter": "no"},
        {"inside_charter": "unstated"},
        {"channel": ""},
        {"channel": "  "},
        {"channel": "brand", "re": ""},
        {"channel": " brand"},
        {"channel": "a/b"},
        {"channel": "brand\\udc80"},
        {"channel": "Brand"},
        {"channel": "bra\u200bnd"},
        {"channel": "caf\u00e9"},
        {"channel": "c" * (MAX_CHANNEL_CHARS + 1)},
        {"channel": "brand", "re": "c-7"},
    ],
    ids=[
        "re-alone",
        "yes-alone",
        "no-alone",
        "unstated-alone",
        "empty-channel",
        "blank-channel",
        "empty-re",
        "untrimmed-channel",
        "path-channel",
        "escaped-channel",
        "capital-channel",
        "zero-width-channel",
        "accented-channel",
        "long-channel",
        "short-re",
    ],
)
def test_a_logged_provenance_the_bus_would_not_have_taken_is_a_task_no_store_produces(
    fields: dict[str, object],
) -> None:
    """
    No declaration logs any of these: the bus strips a blank name to absent and
    refuses a name that is no channel or no message id, by the rule the store
    reads a logged one with (``stated_provenance``). The fold carries the
    fields as written, so the store is where it is refused. ``brand\\udc80``
    is the drawable spelling of a channel the bus refuses, as a log would hold
    it; a capital, a zero-width space or an accent is a name the bus refuses
    under the plain-name rule, which a log written before that rule could hold.
    """
    board = boardlog.Board(tasks=(_logged(**fields),))

    snap, _ = _apply(Snapshot.empty(), BoardRestored(S, board, _CLOCK), _CLOCK.monotonic)

    assert snap.tasks == {}
    assert snap.restores[S].unplaced == (NotPlaced("task", "t-1", Unplaced.INCOHERENT),)


def test_a_logged_provenance_is_placed_as_the_value_it_flattened() -> None:
    board = boardlog.Board(tasks=(_logged(channel="brand", re=MSG, inside_charter="yes"),))

    snap, _ = _apply(Snapshot.empty(), BoardRestored(S, board, _CLOCK), _CLOCK.monotonic)

    assert snap.tasks["t-1"].provenance == Provenance("brand", MSG, Charter.YES)


@pytest.mark.parametrize("logged", [None, "probably", "true"], ids=["absent", "newer", "spelling"])
def test_a_logged_mark_this_build_does_not_define_is_restored_as_unstated(
    logged: str | None,
) -> None:
    """
    The log holds the member's value and nothing else; a word a later build
    defines is a mark this one cannot read, and unstated puts no claim in the
    declarer's mouth. The task is placed, not refused.
    """
    board = boardlog.Board(tasks=(_logged(channel="brand", inside_charter=logged),))

    snap, _ = _apply(Snapshot.empty(), BoardRestored(S, board, _CLOCK), _CLOCK.monotonic)

    assert snap.tasks["t-1"].provenance == Provenance("brand", None, Charter.UNSTATED)


def test_a_mark_is_written_to_the_log_as_its_word(tmp_path: Path) -> None:
    """
    Text, as a basis is, so a later build's member is a word this build reads as
    unstated and not a record that ends the log.
    """
    for mark in Charter:
        _, facts = apply(started(), declared(provenance=Provenance("brand", None, mark)))
        (text,) = [boardlog.encode(f.on_the_wall(_CLOCK)) for f in facts]
        assert f'"inside_charter": "{mark.value}"' in text
