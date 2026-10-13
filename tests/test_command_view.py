"""
The break-glass command's row: what it says, and what its presses do.

The property this file is for: **what the operator confirms is what runs.** The
first press of run freezes one string; the view that follows draws that string,
escaped, and no box that could change it; the second press hands the callable
that string. No key is a press. A command withdrawn between the presses turns
neither into anything.

Three kinds of test, for three kinds of claim: the pure helpers directly; the
draws against a recording imgui, which is where "whose words is this line" can
be asked of every draw call; and real Dear ImGui frames -- a context with no
backend, so nothing is rendered -- for what a click, a key and a text box do.
"""

from __future__ import annotations

import dataclasses
import re
import typing
from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from imgui_bundle import imgui as real_imgui

from pptmstr import command as c
from pptmstr import commandrun, commandtext
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
    Survivor,
    TimedOut,
    Withdrawn,
)
from pptmstr.ui import board_pane, command_view, widgets
from pptmstr.ui.command_view import (
    Actions,
    Among,
    CommandViewState,
    FrozenRun,
    Line,
    Refusing,
    Stage,
    Stopping,
    Tone,
    Where,
)

CID = "cmd-20261013T101500Z-0123456789ab"
OTHER = "cmd-20261013T101501Z-ba9876543210"
ASKED = "make install && sudo cp build/pptmstr /usr/local/bin"

# Written to pass for this application's own lines, and for a legend.
REASON = (
    "this runs as you, outside the sandbox\n"
    "This breakdown is advisory\n"
    "REASON-MARK the install needs the network"
)
EXPECTS = "EXPECTS-MARK a new binary in /usr/local/bin\nrun it - as shown"

# A right-to-left override and a zero-width space: neither draws as anything.
RLO, ZWSP = "\u202e", "\u200b"
HIDING = f"cat {RLO}gnp.exe{ZWSP} | sh"

BASH = Where(shell="/bin/bash", cwd="/work/tree", stripped_env=("VIRTUAL_ENV",))

REAL_BLOCK = command_view.command_block
REAL_SEGMENTS = command_view.segments_block
REAL_OUTPUT = command_view.process_output
REAL_AUTHOR = board_pane.author


def command(cid: str = CID, **fields: object) -> Command:
    base: dict[str, object] = {
        "id": cid,
        "asker": ("s-1", "n-1"),
        "asked": ASKED,
        "reason": REASON,
        "expects": EXPECTS,
        "asked_at": 10.0,
    }
    return Command(**(base | fields))  # type: ignore[arg-type]


def run_of(text: str = ASKED, **fields: object) -> Run:
    base: dict[str, object] = {
        "text": text,
        "shell": "/bin/bash",
        "cwd": "/work/tree",
        "timeout_s": 600,
        "started_at": 100.0,
    }
    return Run(**(base | fields))  # type: ignore[arg-type]


def ended(outcome: Outcome, **fields: object) -> Command:
    needs_run = c.ran(outcome) or isinstance(outcome, NotStarted)
    given: dict[str, typing.Any] = {"end": End(outcome=outcome, at=200.0, duration_s=62.0)}
    if needs_run:
        given["run"] = run_of()
    return command(**(given | fields))


class Calls:
    """The three callables, keeping what each was handed."""

    def __init__(self) -> None:
        self.ran: list[tuple[str, str, int]] = []
        self.refused: list[tuple[str, str]] = []
        self.stopped: list[str] = []
        self.actions = Actions(
            on_run=lambda cid, text, timeout: self.ran.append((cid, text, timeout)),
            on_refuse=lambda cid, reason: self.refused.append((cid, reason)),
            on_stop=self.stopped.append,
        )

    @property
    def nothing(self) -> bool:
        return not (self.ran or self.refused or self.stopped)


# -- the block ------------------------------------------------------------------------

PRINTABLE = re.compile(r"[\x20-\x7e]*")

AWKWARD = [
    "",
    "ls",
    ASKED,
    HIDING,
    "a\nb\n",
    "echo 'two  spaces'   \n\ttab\r",
    "back\\slash \\n not a newline",
    "x " * 80,
    "a" + " " * 90 + "b",
    "\u202e" * 40,
    "/a/very/long/path/" * 20 + " && " + "token" * 30,
]


@pytest.mark.parametrize("text", AWKWARD)
def test_the_blocks_lines_are_the_escaped_command_and_nothing_but_printable_ascii(
    text: str,
) -> None:
    lines = command_view.block_lines(text)

    assert "\n".join(lines) == commandtext.visible(text)
    assert all(PRINTABLE.fullmatch(line) for line in lines)


def _escape_spans(line: str) -> list[tuple[int, int]]:
    """Where each escape of ``visible`` lies in ``line``, read independently of the module."""
    spans, index = [], 0
    while index < len(line):
        if line[index] != "\\":
            index += 1
            continue
        following = line[index + 1]
        if following in "\\nr":
            length = 2
        elif following == "x":
            length = 4
        else:
            length = line.index("}", index) - index + 1
        spans.append((index, index + length))
        index += length
    return spans


@pytest.mark.parametrize("text", AWKWARD)
@pytest.mark.parametrize("width", [24, 37, 80])
def test_a_wrapped_line_loses_nothing_gains_nothing_and_no_escape_is_cut_in_two(
    text: str, width: int
) -> None:
    for line in command_view.block_lines(text):
        rows = command_view.wrap_shown(line, width)

        assert "".join(rows) == line
        assert all(len(row) <= width for row in rows)
        assert all(rows[:-1]), "a row that is not the last holds something"
        cuts, at = [], 0
        for row in rows[:-1]:
            at += len(row)
            cuts.append(at)
        for begin, end in _escape_spans(line):
            assert not any(begin < cut < end for cut in cuts)


def test_a_cut_is_never_made_beside_a_space_where_the_line_has_anywhere_else() -> None:
    line = "sudo rm -rf / home/wreel --no-preserve-root && echo done > /dev/null"

    for width in range(24, len(line)):
        rows = command_view.wrap_shown(line, width)
        assert "".join(rows) == line
        for above, below in zip(rows, rows[1:], strict=False):
            assert not above.endswith(" ") and not below.startswith(" "), (width, rows)


def test_a_cut_is_not_pulled_far_to_the_left_to_keep_it_from_a_space() -> None:
    """
    The hazard: ``ab x x x x ...`` has one place clear of a space, after its
    first letter. Cutting there makes a row of one character, and a command
    made of such stretches is twice as tall as its length says.
    """
    line = ("ab" + " x" * 47) * 20

    for width in (24, 37, 95):
        rows = command_view.wrap_shown(line, width)
        assert "".join(rows) == line
        assert all(len(row) >= width - width // 4 for row in rows[:-1]), width
        assert len(rows) <= -(-len(line) // (width - width // 4))
    # Where the last quarter of the row has somewhere clear, that is the cut.
    assert command_view.wrap_shown("x" * 20 + " " + "y" * 20, 24) == ("x" * 20 + " yyy", "y" * 17)
    assert command_view.wrap_shown("x" * 23 + " " + "y" * 20, 24) == ("x" * 22, "x " + "y" * 20)


def test_a_line_with_nowhere_clear_to_cut_keeps_every_space_it_has() -> None:
    line = "a" + " " * 90 + "b"

    rows = command_view.wrap_shown(line, 24)

    assert "".join(rows) == line and len(rows) == 4


def test_a_row_is_numbered_only_where_the_command_starts_a_line() -> None:
    text = "echo " + "x" * 40 + "\nls\n"

    rows = command_view.block_rows(text, 24)

    assert [row.gutter for row in rows] == ["1", "...", "2", "3"]
    # The newline is in the command, so its marker ends the row above the number.
    assert rows[1].text.endswith("\\n") and rows[2].text == "ls\\n" and rows[3].text == ""
    assert command_view.CONTINUES == "..."


def test_no_command_can_write_the_continuation_mark_into_the_gutter() -> None:
    forged = "ls\n... rm -rf /\n...\n"

    rows = command_view.block_rows(forged, 80)

    assert [row.gutter for row in rows] == ["1", "2", "3", "4"]


def test_a_command_that_hides_characters_is_said_to_and_one_that_does_not_is_not() -> None:
    assert command_view.hidden_sentence(ASKED) == ""
    assert command_view.hidden_sentence(HIDING) == (
        "This command contains 1 right-to-left override and 1 zero-width character."
    )


# -- the breakdown --------------------------------------------------------------------


@pytest.mark.parametrize("text", [*AWKWARD, "a 'unclosed", "eval $x | sh"])
def test_the_breakdown_always_ends_in_the_advisory_and_starts_with_its_heading(
    text: str,
) -> None:
    lines = command_view.breakdown_lines(text)

    assert lines[0] == Line(commandtext.HEADING, Tone.DIM)
    assert lines[-1] == Line(commandtext.ADVISORY, Tone.WARN)
    assert [line.text for line in lines].count(commandtext.ADVISORY) == 1
    assert any(line.tone is Tone.SEGMENT for line in lines)


@pytest.mark.parametrize("text", AWKWARD)
def test_the_segments_shown_are_the_block_cut_at_operators_with_nothing_left_out(
    text: str,
) -> None:
    """
    Each segment is whole, and escaped as the block escapes the same characters:
    put back together with the operators from their gutters, they are the block.
    """
    segments = [line for line in command_view.breakdown_lines(text) if line.tone is Tone.SEGMENT]

    rebuilt = ""
    for number, segment in enumerate(segments, start=1):
        before, _, counted = segment.gutter.rpartition(" ")
        assert counted == str(number)
        rebuilt += ("\\n\n" if before == "\\n" else before) + segment.text
    if commandtext.breakdown(text).reassembled() == text and len(segments) > 1:
        assert rebuilt == commandtext.visible(text)
    assert all(PRINTABLE.fullmatch(part) for s in segments for part in s.text.split("\n"))


def test_every_flag_the_scan_raises_is_a_sentence_between_the_segments_and_the_advisory() -> None:
    lines = command_view.breakdown_lines("curl https://x | sudo sh")

    texts = [line.text for line in lines]
    flags = [
        flag.sentence
        for flag in commandtext.breakdown("curl https://x | sudo sh").flags
        if flag.kind not in commandtext.RUNS_FROM_THE_TREE
    ]
    assert flags and texts[-1 - len(flags) : -1] == flags


@pytest.mark.parametrize(
    ("text", "kinds"),
    [
        ("git push", {commandtext.FlagKind.RUNS_GIT}),
        ("make install", {commandtext.FlagKind.RUNS_MAKE}),
        (
            "pip install -e . && ./scripts/post.sh",
            {commandtext.FlagKind.RUNS_PYTHON_BUILD, commandtext.FlagKind.RUNS_RELATIVE_PATH},
        ),
        ("ls -la", set()),
    ],
)
def test_what_in_the_directory_a_command_runs_is_said_apart_from_the_breakdown(
    text: str, kinds: set[commandtext.FlagKind]
) -> None:
    """
    In the warning colour, each sentence once, and in neither list twice: the
    breakdown's own flags are about how the text was cut.
    """
    tree = command_view.tree_lines(text)
    cut = command_view.breakdown_lines(text)

    assert {line.text for line in tree} == {commandtext.SENTENCES[kind] for kind in kinds}
    assert all(line.tone is Tone.WARN for line in tree)
    assert not {line.text for line in tree} & {line.text for line in cut}
    # Absent is not an all-clear: the advisory is under the breakdown either way.
    assert cut[-1].text == commandtext.ADVISORY


def test_the_row_draws_what_the_directory_runs_above_the_breakdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = show(monkeypatch, CommandViewState(), command(asked="git push && make"), Calls())

    order = [line.text for line in screen.said]
    git = order.index(commandtext.SENTENCES[commandtext.FlagKind.RUNS_GIT])
    make = order.index(commandtext.SENTENCES[commandtext.FlagKind.RUNS_MAKE])
    assert max(git, make) < order.index(commandtext.HEADING) < order.index(commandtext.ADVISORY)
    assert order.count(commandtext.SENTENCES[commandtext.FlagKind.RUNS_GIT]) == 1


# -- the standing lines ---------------------------------------------------------------


def test_the_four_differences_are_said_in_fixed_words_before_anything_else() -> None:
    lines = command_view.standing_lines(
        "/bin/bash", ("VIRTUAL_ENV (/v) is unset", "/v/bin is taken off\nPATH"), "/work"
    )

    texts = [line.text for line in lines]
    assert texts == [
        command_view.RUNS_AS_YOU,
        command_view.RULES_DO_NOT_APPLY,
        "/bin/bash runs it - login shell: your profile runs first",
        "in /work",
        commandrun.PROFILE_WARNING,
        command_view.ENVIRONMENT_CHANGED,
        "  - VIRTUAL_ENV (/v) is unset",
        "  - /v/bin is taken off PATH",
        command_view.NO_TERMINAL,
    ]
    assert "outside the sandbox" in texts[0] and "credentials" in texts[0]
    assert "may execute files this session wrote" in texts[0]
    assert "allow and deny rules and hooks do not apply" in texts[1]
    assert texts[-1] == "no terminal: a prompt waits until the timeout"
    assert all(line.tone is Tone.WARN for line in lines)


def test_a_shell_that_is_not_one_of_the_bourne_family_is_told_the_breakdown_is_bashs() -> None:
    fish = [line.text for line in command_view.standing_lines("/usr/bin/fish", (), "/work")]
    zsh = [line.text for line in command_view.standing_lines("/usr/bin/zsh", (), "/work")]

    assert any("fish" in text and "bash's reading" in text for text in fish)
    assert not any("bash's reading" in text for text in zsh)
    assert command_view.ENVIRONMENT_WHOLE in zsh


def test_with_no_shell_the_row_says_so_in_the_alarm_colour() -> None:
    lines = command_view.standing_lines(None, (), "/work")

    assert Line(command_view.NO_SHELL, Tone.ALARM) in lines
    # No shell, no profile: the sentence about one would be about nothing.
    assert commandrun.PROFILE_WARNING not in [line.text for line in lines]


def test_every_change_the_runner_makes_to_the_environment_has_a_line_of_its_own() -> None:
    """
    From ``commandrun.environment_changes`` itself, so a removal added there is
    on the row without anybody remembering to list it here.
    """
    environ = {
        "VIRTUAL_ENV": "/v",
        "PATH": "/v/bin:/usr/bin:.:rel/bin",
        "BASH_ENV": "/tmp/hook.sh",
        "ENV": "/tmp/env.sh",
    }
    changes = commandrun.environment_changes(environ)
    texts = [line.text for line in command_view.standing_lines("/bin/bash", changes, "/work")]

    assert len(changes) >= 5
    for change in changes:
        assert f"  - {change}" in texts
    for name in ("VIRTUAL_ENV", "BASH_ENV", "ENV (", "PATH"):
        assert any(name in text for text in texts), name
    assert command_view.ENVIRONMENT_WHOLE not in texts


# -- freezing -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "refusal"),
    [
        ("", c.RequestRefusal.EMPTY),
        ("  \n", c.RequestRefusal.EMPTY),
        ("-rf /", c.RequestRefusal.LEADS_WITH_OPTION),
        ("+x", c.RequestRefusal.LEADS_WITH_OPTION),
        ("ls\x00", c.RequestRefusal.NUL),
        ("ls \ud800", c.RequestRefusal.LONE_SURROGATE),
        ("x" * (c.MAX_COMMAND_CHARS + 1), c.RequestRefusal.TOO_LONG),
    ],
)
def test_an_edit_the_record_would_refuse_cannot_be_frozen_and_is_told_why(
    text: str, refusal: c.RequestRefusal
) -> None:
    assert command_view.refusal_of_edit(text) == refusal.sentence
    with pytest.raises(ValueError):
        command_view.freeze(CID, text, c.DEFAULT_TIMEOUT_S)


def test_a_string_that_may_run_freezes_as_itself_with_nothing_tidied() -> None:
    text = "  make install  \n"

    frozen = command_view.freeze(CID, text, 120)

    assert command_view.refusal_of_edit(text) == ""
    assert frozen == FrozenRun(CID, text, 120)
    assert frozen.breakdown == commandtext.breakdown(text)
    assert frozen.findings == commandtext.invisibles(text)


def test_a_timeout_that_is_not_offered_cannot_be_frozen() -> None:
    with pytest.raises(ValueError):
        command_view.freeze(CID, "ls", 601)


# -- the words for each state ---------------------------------------------------------

OUTCOME_WORDS: dict[type, tuple[Outcome, str, Tone]] = {
    Exited: (Exited(3), "exit code 3 after 1m02s", Tone.WARN),
    TimedOut: (
        TimedOut(),
        "timed out - produced no exit within 10 minutes; anything that prompts waits forever here",
        Tone.WARN,
    ),
    Cancelled: (Cancelled(CancelledBy.OPERATOR), "cancelled by you after 1m02s", Tone.WARN),
    NotStarted: (NotStarted("no runner"), "not started: no runner", Tone.WARN),
    Refused: (Refused("not on\nmain"), "refused by you: not on main", Tone.PLAIN),
    Withdrawn: (Withdrawn(), command_view.WITHDRAWN, Tone.DIM),
    Lapsed: (Lapsed(), "lapsed - asked before pptmstr last closed and never run", Tone.DIM),
    EndUnknown: (
        EndUnknown(),
        "end unknown - was running when pptmstr last closed; whether it finished is not known",
        Tone.WARN,
    ),
}


def test_every_outcome_the_record_has_is_worded() -> None:
    assert set(OUTCOME_WORDS) == set(typing.get_args(Outcome))


@pytest.mark.parametrize("kind", list(OUTCOME_WORDS), ids=lambda kind: kind.__name__)
def test_each_way_of_ending_has_its_own_words(kind: type) -> None:
    outcome, words, tone = OUTCOME_WORDS[kind]

    assert command_view.row_state(ended(outcome), now=300.0) == Line(words, tone)


def test_the_remaining_endings_are_worded_apart_too() -> None:
    def said(outcome: Outcome) -> Line:
        return command_view.row_state(ended(outcome), now=300.0)

    assert said(Exited(0)) == Line("exit code 0 after 1m02s", Tone.PLAIN)
    assert said(Cancelled(CancelledBy.SHUTDOWN)) == Line(
        "cancelled at shutdown after 1m02s: pptmstr quit while it ran", Tone.WARN
    )
    assert said(Refused("  ")) == Line(command_view.NO_REASON_GIVEN)
    words = {said(outcome).text for outcome, _, _ in OUTCOME_WORDS.values()}
    assert len(words) == len(OUTCOME_WORDS)


def test_an_asked_and_a_running_command_say_which_they_are() -> None:
    running = command(run=run_of(started_at=100.0))
    stopping = dataclasses.replace(running, stop_asked_at=150.0)

    assert command_view.row_state(command(), now=162.0) == Line(command_view.ASKED_STATE)
    assert command_view.row_state(running, now=162.0) == Line(
        "running for 1m02s - cancel kills it", Tone.WARN
    )
    assert command_view.STOP_ASKED in command_view.row_state(stopping, now=162.0).text


def test_what_an_end_left_behind_and_did_not_confirm_is_in_the_one_line_of_how_it_ended() -> None:
    """
    ``End.remarks``'s own words, after how it ended: on the row, in DETAIL's
    list and on the clipboard there is one line to read and none to miss.
    """
    plain = ended(Exited(0))
    left = dataclasses.replace(plain, end=dataclasses.replace(plain.end, leftover=2))
    assert plain.end is not None and left.end is not None
    said = command_view.row_state(left, now=300.0)
    assert said.text == f"exit code 0 after 1m02s - {left.end.remarks[0]}"
    assert "2 leftover processes" in said.text and said.tone is Tone.WARN
    none_left = dataclasses.replace(plain, end=dataclasses.replace(plain.end, leftover=0))
    assert command_view.row_state(none_left, now=300.0) == command_view.row_state(plain, 300.0)

    for outcome in (Exited(0), TimedOut(), Cancelled(CancelledBy.OPERATOR), EndUnknown()):
        base = ended(outcome)
        assert base.end is not None
        unsure = dataclasses.replace(
            base, end=dataclasses.replace(base.end, unconfirmed="kill(2) said\nEPERM")
        )
        line = command_view.row_state(unsure, now=300.0)
        assert line.tone is Tone.ALARM, outcome
        assert line.text.startswith(command_view.row_state(base, now=300.0).text)
        assert line.text.endswith("NOT CONFIRMED DEAD: kill(2) said EPERM")


def test_a_command_left_running_is_not_said_to_have_an_unknown_end() -> None:
    """
    The body is drawn under the row's own STILL RUNNING block. "Whether it
    finished is not known" there would contradict it: it is known, and it has
    not.
    """
    still = ended(EndUnknown(Survivor(pid=4242, pgid=77, members=3)))
    one = ended(EndUnknown(Survivor(pid=4242, pgid=4242, members=1)))

    said = command_view.row_state(still, now=300.0)
    assert said.tone is Tone.ALARM
    assert said.text.startswith("STILL RUNNING, UNSUPERVISED")
    assert "its shell's session 4242 still has 3 processes" in said.text
    assert "group" not in said.text
    assert "1 process" in command_view.row_state(one, now=300.0).text
    assert "not known" not in said.text and "end unknown" not in said.text
    assert command_view.row_state(ended(EndUnknown()), now=300.0).text == command_view.END_UNKNOWN


@pytest.mark.parametrize(
    ("cmd", "stage", "tense"),
    [
        (command(), Stage.WOULD_RUN, "runs"),
        (command(run=run_of("make -n")), Stage.RUNNING, "is running"),
        (ended(Exited(0), run=run_of("make -n")), Stage.RAN, "ran"),
        (ended(EndUnknown(), run=run_of("make -n")), Stage.RAN, "ran"),
        (
            ended(EndUnknown(Survivor(pid=4242, pgid=4242, members=1)), run=run_of("make -n")),
            Stage.RUNNING,
            "is running",
        ),
        (ended(NotStarted("no runner"), run=run_of("make -n")), Stage.NOT_STARTED, "approved"),
    ],
)
def test_an_edited_commands_headings_are_in_the_tense_its_record_is_in(
    cmd: Command, stage: Stage, tense: str
) -> None:
    assert command_view.stage_of(cmd) is stage
    assert tense in command_view.edited_by_you(stage)
    assert tense in command_view.edited_heading("builder", stage)
    if stage is not Stage.WOULD_RUN:
        assert "what runs" not in command_view.edited_by_you(stage)
        assert "what runs" not in command_view.edited_heading("builder", stage)
    assert len({command_view.edited_by_you(each) for each in Stage}) == len(Stage)


def test_an_ended_edited_command_is_not_headed_in_the_present_tense(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    done = ended(Exited(0), run=run_of("make -n install"))

    own = show(monkeypatch, CommandViewState(), done, Calls(), attend=False).own()

    assert "edited by you - this, your text, is what ran:" in own
    assert "what builder asked for, for comparison - this is NOT what ran:" in own
    assert not any("what runs" in text for text in own)


def test_a_result_read_back_from_a_file_is_said_to_be_from_before_the_restart() -> None:
    unread = ended(Exited(0))
    read = dataclasses.replace(unread, first_delivered_at=250.0)

    said = command_view.delivery_line(unread, "builder", restored=True)
    assert said is not None and said.tone is Tone.DIM
    assert "from before pptmstr restarted" in said.text and "in this window" in said.text
    assert "not yet read" not in said.text
    # Read in this window is read, wherever the record came from.
    assert command_view.delivery_line(read, "builder", restored=True) == Line(
        "read by builder", Tone.DIM
    )
    assert command_view.delivery_line(ended(Withdrawn()), "builder", restored=True) is None


@pytest.mark.parametrize(
    ("cmd", "heading"),
    [
        (ended(Exited(0)), command_view.RAN_HEADING),
        (ended(c.TimedOut()), command_view.RAN_HEADING),
        (ended(EndUnknown()), command_view.RAN_HEADING),
        (ended(NotStarted("no runner")), command_view.NOT_STARTED_HEADING),
        (
            ended(EndUnknown(Survivor(pid=4242, pgid=4242, members=1))),
            command_view.RUNNING_HEADING,
        ),
    ],
    ids=["exited", "timed-out", "end-unknown", "not-started", "left-running"],
)
def test_a_string_approved_as_asked_is_headed_by_what_became_of_it(
    monkeypatch: pytest.MonkeyPatch, cmd: Command, heading: str
) -> None:
    """
    "What ran, exactly" over a command that was approved and never became a
    process says something ran, and over one still running that it is over.
    """
    assert not cmd.edited
    assert command_view.run_heading(cmd) == heading

    screen = Screen(monkeypatch)
    command_view.draw_ended(cmd, role="builder", now=300.0)

    headings = {
        command_view.RAN_HEADING,
        command_view.RUNNING_HEADING,
        command_view.NOT_STARTED_HEADING,
    }
    assert [text for text in screen.own() if text in headings] == [heading]
    assert "did not start" in command_view.NOT_STARTED_HEADING
    assert "ran" not in command_view.NOT_STARTED_HEADING


# -- which command this is --------------------------------------------------------------


def test_two_commands_that_differ_by_two_letters_differ_in_the_line_above_the_block() -> None:
    """
    The swap: ``pip install requests`` and ``pip install reqeusts``, asked a
    second apart by one agent. The blocks are one glance apart; the line above
    each is not.
    """
    first = command(CID, asked="pip install requests", asked_at=100.0)
    second = command(OTHER, asked="pip install reqeusts", asked_at=101.0)

    one = command_view.identity_lines(first, "builder", Among(4, 1, others_waiting=1), now=162.0)
    two = command_view.identity_lines(second, "builder", Among(5, 2, others_waiting=1), now=162.0)

    assert one[0].text == (
        "asked 1m02s ago \u00b7 4th command asked in this session, 1st from builder"
        " \u00b7 0123456789ab"
    )
    assert two[0].text == (
        "asked 1m01s ago \u00b7 5th command asked in this session, 2nd from builder"
        " \u00b7 ba9876543210"
    )
    assert (
        one[1]
        == two[1]
        == Line(
            "builder has 1 other command waiting - read each one: they are not this one", Tone.WARN
        )
    )
    assert "2 other commands waiting" in (
        command_view.identity_lines(first, "builder", Among(1, 1, others_waiting=2), 162.0)[1].text
    )


def test_two_askers_first_commands_do_not_read_as_one_command() -> None:
    """
    Each asker has a first. Counted over the asker alone, the lines of two
    agents' first commands differ by a role name and the id; the count over
    the session is one no two commands of it share.
    """
    mine = command(CID, asked_at=100.0)
    theirs = command(OTHER, asked_at=100.0)

    one = command_view.identity_lines(mine, "builder", Among(1, 1), now=162.0)[0].text
    two = command_view.identity_lines(theirs, "builder-2", Among(2, 1), now=162.0)[0].text

    assert "1st command asked in this session, 1st from builder \u00b7" in one
    assert "2nd command asked in this session, 1st from builder-2 \u00b7" in two


def test_a_command_just_asked_says_so_in_the_alarm_colour_and_an_old_one_does_not() -> None:
    cmd = command()
    new = command_view.identity_lines(cmd, "builder", Among(1, 1, new=True), now=162.0)
    old = command_view.identity_lines(cmd, "builder", Among(1, 1), now=162.0)

    assert Line(command_view.NEW, Tone.ALARM) in new and command_view.NEW.startswith("NEW")
    assert len(old) == 1 and old[0].tone is Tone.PLAIN
    # A caller that counted nothing claims no count, and the id is still there.
    bare = command_view.identity_lines(cmd, "builder", None, now=162.0)
    assert [line.text for line in bare] == [
        "asked 2m32s ago \u00b7 from builder \u00b7 0123456789ab"
    ]


@pytest.mark.parametrize(
    ("n", "said"),
    [
        (1, "1st"),
        (2, "2nd"),
        (3, "3rd"),
        (4, "4th"),
        (11, "11th"),
        (12, "12th"),
        (13, "13th"),
        (21, "21st"),
        (22, "22nd"),
        (103, "103rd"),
        (111, "111th"),
    ],
)
def test_an_ordinal_is_spelled_as_it_is_said(n: int, said: str) -> None:
    assert command_view.ordinal(n) == said


def test_a_command_is_new_for_a_few_seconds_after_it_was_asked_and_never_again() -> None:
    """
    Read off when it was asked, so nothing that happens to the queue around a
    command makes it new: the row above being answered, another being
    withdrawn, the pane coming back into view.
    """
    cmd = command(asked_at=100.0)

    assert command_view.is_new(cmd, 100.0)
    assert command_view.is_new(cmd, 100.0 + command_view.NEW_FOR_S - 0.1)
    assert not command_view.is_new(cmd, 100.0 + command_view.NEW_FOR_S)
    assert not command_view.is_new(cmd, 700.0)
    # Running, it is the same command, asked when it was.
    assert not command_view.is_new(command(asked_at=100.0, run=run_of()), 700.0)
    assert 2.0 <= command_view.NEW_FOR_S <= 10.0
    # Nothing is kept about where a command stood: there is nothing to restamp.
    assert not hasattr(CommandViewState(), "placed")


def test_the_row_says_which_command_it_is_above_the_block_in_every_state_that_owes_a_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    among = Among(3, 2, others_waiting=1, new=True)

    def drawn(cmd: Command, state: CommandViewState) -> list[str]:
        shown = Screen(monkeypatch)
        state.open(cmd.id, at=0.0)
        state.attend({cmd.id: cmd}, CID, 1)
        command_view.draw_row(
            state, cmd, role="builder", where=BASH, actions=Calls().actions, now=162.0, among=among
        )
        return [line.text for line in shown.said]

    identity = (
        "asked 2m32s ago \u00b7 3rd command asked in this session, 2nd from builder"
        " \u00b7 0123456789ab"
    )
    asked = drawn(command(), CommandViewState())
    assert asked.index(identity) < asked.index(command_view.asked_heading("builder"))
    assert asked.index(command_view.NO_TERMINAL) < asked.index(identity)
    assert command_view.NEW in asked
    assert any("1 other command waiting" in text for text in asked)

    editing = CommandViewState(editing=CID, edits={CID: "make -n"})
    assert identity in drawn(command(), editing)

    confirming = CommandViewState()
    confirming.pending = command_view.freeze(CID, ASKED, c.DEFAULT_TIMEOUT_S)
    confirm = drawn(command(), confirming)
    assert confirm.index(identity) < confirm.index(command_view.asked_heading("builder"))

    running = drawn(command(run=run_of()), CommandViewState())
    assert running.index(identity) < running.index(command_view.RUNNING_HEADING)


def test_a_result_its_asker_has_not_read_says_so_in_the_warning_colour() -> None:
    unread = ended(Exited(0))
    read = dataclasses.replace(unread, first_delivered_at=250.0)

    assert command_view.delivery_line(unread, "builder") == Line(
        "not yet read by builder", Tone.WARN
    )
    assert command_view.delivery_line(read, "builder") == Line("read by builder", Tone.DIM)
    assert command_view.delivery_line(command(), "builder") is None
    assert command_view.delivery_line(ended(Withdrawn()), "builder") is None


@pytest.mark.parametrize(
    ("seconds", "words"),
    [(120, "2 minutes"), (600, "10 minutes"), (1800, "30 minutes"), (7200, "2 hours")],
)
def test_every_timeout_offered_has_words(seconds: int, words: str) -> None:
    assert seconds in c.TIMEOUT_CHOICES_S
    assert command_view.timeout_label(seconds) == words
    assert len(c.TIMEOUT_CHOICES_S) == 4 and command_view.timeout_label(60) == "1 minute"
    assert (
        command_view.timeout_label(3600) == "1 hour" and command_view.timeout_label(90) == "1m30s"
    )


# -- what the state keeps and drops ---------------------------------------------------


def _busy(pending: command_view.Pending | None = None) -> CommandViewState:
    return CommandViewState(
        edits={CID: "ls -la"},
        editing=CID,
        timeouts={CID: 120},
        reasons={CID: "no"},
        pending=pending,
    )


def test_everything_typed_for_a_command_is_there_when_the_cursor_comes_back() -> None:
    state = _busy()
    commands = {CID: command(), OTHER: command(OTHER)}

    state.attend(commands, OTHER, 1)
    state.attend(commands, CID, 1)

    assert state == _busy()


@pytest.mark.parametrize(
    "now",
    [None, ended(Withdrawn()), ended(Refused("no")), command(run=run_of()), ended(Exited(0))],
    ids=["gone", "withdrawn", "refused", "running", "exited"],
)
def test_nothing_typed_for_a_command_outlives_its_being_asked(now: Command | None) -> None:
    """
    With no act armed, too: the pruning is not behind the check of ``pending``.
    """
    for pending in (None, command_view.freeze(CID, "ls -la", 120), Refusing(CID)):
        state = _busy(pending)

        state.attend({} if now is None else {CID: now}, CID, 1)

        assert state == CommandViewState()


@pytest.mark.parametrize("pending", [command_view.freeze(CID, "ls", 600), Refusing(CID)])
def test_an_armed_act_is_dropped_when_the_cursor_leaves_its_row(
    pending: command_view.Pending,
) -> None:
    for focused in (OTHER, None):
        state = CommandViewState(pending=pending, opened=CID)

        state.attend({CID: command(), OTHER: command(OTHER)}, focused, 1)

        assert state.pending is None and state.opened is None

    kept = CommandViewState(pending=pending, opened=CID)
    kept.attend({CID: command()}, CID, 1)
    assert kept.pending == pending and kept.armed(CID) == pending and kept.armed(OTHER) is None
    assert kept.is_open(CID)

    # An act is armed from an open row and from nowhere else: one on a row
    # that is not open is not kept, cursor or no cursor.
    shut = CommandViewState(pending=pending)
    shut.attend({CID: command()}, CID, 1)
    assert shut.pending is None


def test_an_armed_cancel_holds_only_while_the_command_runs_and_no_stop_is_asked() -> None:
    running = command(run=run_of())

    for now, held in [
        (running, True),
        (dataclasses.replace(running, stop_asked_at=120.0), False),
        (ended(Exited(0)), False),
        (command(), False),
    ]:
        state = CommandViewState(pending=Stopping(CID), opened=CID)
        state.attend({CID: now}, CID, 1)
        assert (state.pending is not None) is held


def test_a_frozen_run_is_only_ever_the_one_on_its_own_command() -> None:
    state = CommandViewState(pending=command_view.freeze(CID, "ls", 600))

    assert state.frozen_run(OTHER) is None
    assert state.frozen_run(CID) == FrozenRun(CID, "ls", 600)
    assert CommandViewState(pending=Refusing(CID)).frozen_run(CID) is None


# -- a row is closed until the operator opens it ---------------------------------------


def test_a_row_is_opened_by_being_told_to_and_by_nothing_the_state_is_asked_each_frame() -> None:
    """
    ``open`` is the only thing that sets ``opened``. ``attend`` runs every
    frame with whatever the cursor is on, and must never be what opens a row.
    """
    state = CommandViewState()
    commands = {CID: command(), OTHER: command(OTHER)}

    for frame, focused in enumerate((CID, OTHER, CID, None, CID)):
        state.attend(commands, focused, frame)
        assert state.opened is None and not state.is_open(CID) and not state.is_open(OTHER)

    state.open(CID, at=12.0)
    assert state.is_open(CID) and not state.is_open(OTHER) and state.opened_at == 12.0
    state.attend(commands, CID, 5)
    assert state.is_open(CID)


def test_an_open_row_names_a_command_and_cannot_pass_to_another() -> None:
    """
    The swap, at the state: A is open, A is withdrawn, B is under the cursor
    in the same frame. B is not open, and A coming back would not be either.
    """
    state = CommandViewState()
    state.open(CID, at=1.0)
    both = {CID: command(), OTHER: command(OTHER)}
    state.attend(both, CID, 1)

    state.attend({CID: ended(Withdrawn()), OTHER: command(OTHER)}, OTHER, 2)
    assert state.opened is None and not state.is_open(OTHER)
    state.attend(both, CID, 3)
    assert not state.is_open(CID)

    # Gone from the record altogether, with the cursor's key still naming it.
    state.open(CID, at=1.0)
    state.attend({OTHER: command(OTHER)}, CID, 4)
    assert state.opened is None


@pytest.mark.parametrize(
    ("now", "stays"),
    [
        (command(), True),
        (command(run=run_of()), True),
        (ended(EndUnknown(Survivor(pid=4242, pgid=4242, members=1))), True),
        (ended(Exited(0)), False),
        (ended(Refused("no")), False),
        (ended(EndUnknown()), False),
    ],
    ids=["asked", "running", "left-running", "exited", "refused", "end-unknown"],
)
def test_a_row_stays_open_exactly_while_its_command_owes_one(now: Command, stays: bool) -> None:
    """
    Asked to running is the same row, opened by the same person: it does not close.
    """
    state = CommandViewState()
    state.open(CID, at=1.0)

    state.attend({CID: now}, CID, 1)

    assert state.is_open(CID) is stays


def test_opening_the_open_row_again_does_not_restart_its_clock_and_another_disarms() -> None:
    state = CommandViewState()
    state.open(CID, at=1.0)
    state.pending = command_view.freeze(CID, ASKED, 600)

    state.open(CID, at=9.0)
    assert state.opened_at == 1.0 and state.pending is not None

    state.open(OTHER, at=9.0)
    assert state.is_open(OTHER) and not state.is_open(CID)
    assert state.opened_at == 9.0 and state.pending is None


def test_what_a_closed_row_says_is_which_command_how_it_stands_and_how_to_open_it() -> None:
    among = Among(3, 2, others_waiting=1, new=True)
    cmd = command(asked_at=100.0)

    lines = command_view.closed_lines(cmd, "builder", among, now=162.0)

    assert lines == (
        *command_view.identity_lines(cmd, "builder", among, now=162.0),
        Line(command_view.ASKED_STATE),
        Line(command_view.CLOSED, Tone.DIM),
    )
    first = lines[0].text
    # Age, which of the session's and of the asker's, the asker, and the id's tail.
    for part in ("asked 1m02s ago", "3rd command asked", "2nd from builder", "0123456789ab"):
        assert part in first
    assert "click its row" in command_view.CLOSED and "press o" in command_view.CLOSED
    said = "\n".join(line.text for line in lines)
    assert not any(word in said for word in ("make install", "REASON-MARK", "EXPECTS-MARK"))
    # How it stands is the record's: running, and left running in the alarm colour.
    assert (
        "running for" in command_view.closed_lines(command(run=run_of()), "b", None, 162.0)[1].text
    )
    left = ended(EndUnknown(Survivor(pid=4242, pgid=4242, members=2)))
    assert command_view.closed_lines(left, "b", None, 162.0)[1].tone is Tone.ALARM


@pytest.mark.parametrize(
    "cmd",
    [
        command(),
        command(run=run_of("make -n install")),
        ended(EndUnknown(Survivor(pid=4242, pgid=4242, members=2))),
    ],
    ids=["asked", "running", "left-running"],
)
def test_a_row_that_is_not_open_draws_no_word_of_the_command_and_nothing_to_press(
    monkeypatch: pytest.MonkeyPatch, cmd: Command
) -> None:
    """
    The property, at the draw: the cursor is on the row, every act is armed
    that could be, every button would report a click -- and what is drawn is
    the closed lines and nothing else.
    """
    calls = Calls()
    every = frozenset(
        {
            command_view.RUN,
            command_view.EDIT,
            command_view.REFUSE,
            command_view.CONFIRM_RUN,
            command_view.CONFIRM_REFUSE,
            command_view.CANCEL,
            command_view.CONFIRM_STOP,
        }
    )
    for pending in (None, command_view.freeze(CID, ASKED, 600), Refusing(CID), Stopping(CID)):
        state = CommandViewState(pending=pending, edits={CID: "rm -rf /"}, editing=CID)

        screen = show(monkeypatch, state, cmd, calls, opened=False, press=every, time=500.0)

        expected = command_view.closed_lines(cmd, "builder", None, now=162.0)
        assert [said.text for said in screen.said] == [line.text for line in expected]
        assert screen.buttons() == [] and screen.boxes == [] and screen.children == []
        assert calls.nothing and state.pending is None and not state.is_open(CID)
        assert "make" not in screen.everything() and "rm -rf" not in screen.everything()

    # The control: opened, the same call draws the block.
    opened = show(monkeypatch, CommandViewState(), cmd, calls, press=frozenset(), time=500.0)
    assert opened.by("block") and command_view.CLOSED not in opened.own()


def test_a_row_drawn_with_no_attend_before_it_is_closed_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    DETAIL's copy, and any caller that forgot: the draw asks the state and not its caller.
    """
    screen = show(monkeypatch, CommandViewState(), command(), Calls(), opened=False, attend=False)

    assert screen.own()[-1] == command_view.CLOSED and screen.by("block") == []


@pytest.mark.parametrize("label", [command_view.RUN, command_view.REFUSE])
def test_a_first_press_answers_nothing_until_the_click_that_opened_the_row_is_over(
    monkeypatch: pytest.MonkeyPatch, label: str
) -> None:
    """
    Opening is a click on the row, and the row redraws under the mouse with
    its buttons where the click was. The second click of a double-click is
    not the first press of run.
    """
    state = CommandViewState()
    state.open(CID, at=50.0)
    calls = Calls()
    press = frozenset({label})

    for time in (50.0, 50.2, 50.0 + command_view.ARM_DELAY_S - 0.01):
        show(monkeypatch, state, command(), calls, press=press, time=time, opened=False)
        assert state.pending is None and calls.nothing, time

    show(
        monkeypatch,
        state,
        command(),
        calls,
        press=press,
        time=50.0 + command_view.ARM_DELAY_S,
        opened=False,
    )
    assert state.pending is not None and calls.nothing


def test_cancel_answers_nothing_until_the_click_that_opened_the_row_is_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = CommandViewState()
    state.open(CID, at=50.0)
    running = command(run=run_of())
    press = frozenset({command_view.CANCEL})

    show(monkeypatch, state, running, Calls(), press=press, time=50.3, opened=False)
    assert state.pending is None

    show(monkeypatch, state, running, Calls(), press=press, time=50.7, opened=False)
    assert state.pending == Stopping(CID)


@pytest.mark.parametrize(
    "pending", [command_view.freeze(CID, ASKED, 600), Refusing(CID), Stopping(CID)]
)
def test_an_armed_act_does_not_outlive_a_frame_the_queue_was_not_drawn_in(
    pending: command_view.Pending,
) -> None:
    """
    ``attend`` runs where the queue is drawn. A frame it did not run in is a
    frame the armed row was not on screen for: another layout, a hidden tab.
    """
    cmd = command(run=run_of()) if isinstance(pending, Stopping) else command()
    commands = {CID: cmd}

    def armed() -> CommandViewState:
        state = CommandViewState(pending=pending, opened=CID)
        state.attend(commands, CID, 10)
        assert state.pending == pending
        return state

    every = armed()
    every.attend(commands, CID, 11)
    assert every.pending == pending and every.attended == 11

    skipped = armed()
    skipped.attend(commands, CID, 12)
    assert skipped.pending is None and skipped.attended == 12
    # The row is still the one the operator opened: only the press is dropped.
    assert skipped.is_open(CID)

    # A draw that nobody attended ahead of, two frames on: DETAIL's copy, with
    # the queue not drawn at all.
    unattended = armed()
    unattended.lapse(11)
    assert unattended.pending == pending
    unattended.lapse(12)
    assert unattended.pending is None and unattended.attended == 10

    # A state nothing has attended yet has no frame to have missed.
    fresh = CommandViewState(pending=pending, opened=CID)
    fresh.lapse(500)
    assert fresh.pending == pending


def test_the_row_asks_whether_a_frame_went_by_before_it_draws_an_armed_press(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The wiring: ``draw_row`` calls ``lapse`` with Dear ImGui's frame count.
    """
    state = CommandViewState(pending=command_view.freeze(CID, ASKED, 600), opened=CID)
    state.attend({CID: command()}, CID, 10)
    screen = Screen(monkeypatch, press=frozenset({command_view.CONFIRM_RUN}), time=500.0)
    command_view.imgui.get_frame_count.return_value = 12  # type: ignore[attr-defined]
    calls = Calls()

    command_view.draw_row(
        state, command(), role="builder", where=BASH, actions=calls.actions, now=162.0
    )

    assert calls.nothing and state.pending is None
    assert command_view.CONFIRM_RUN not in screen.buttons()


# -- draws against a recording imgui --------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Said:
    text: str
    # Which of the four hands drew it: "pane" is this application's own voice.
    where: str
    how: str = "text"


class Screen:
    """
    A recording imgui under ``command_view`` and ``board_pane``.

    ``press`` is the buttons that report a click this frame; ``typed`` is what a
    text box returns in place of the value it was handed. ``time`` is Dear
    ImGui's clock. ``released`` is whether the left mouse button came up.
    """

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        press: frozenset[str] = frozenset(),
        typed: str | None = None,
        time: float = 100.0,
        released: bool = True,
    ) -> None:
        self.said: list[Said] = []
        self.clipboard: list[str] = []
        self.boxes: list[tuple[str, str]] = []
        self.children: list[str] = []
        hand = ["pane"]

        def through(name: str, real):  # type: ignore[no-untyped-def]
            def call(*args: object, **kwargs: object) -> None:
                hand.append(name)
                try:
                    real(*args, **kwargs)
                finally:
                    hand.pop()

            return call

        def record(position: int, how: str = "text"):  # type: ignore[no-untyped-def]
            def call(*args: object, **kwargs: object) -> MagicMock:
                self.said.append(Said(str(args[position]), hand[-1], how))
                return MagicMock()

            return call

        def button(label: str, *args: object, **kwargs: object) -> bool:
            self.said.append(Said(label, hand[-1], "button"))
            return label in press

        def child(label: str, *args: object, **kwargs: object) -> bool:
            self.children.append(label)
            return True

        def box(label: str, value: str, size: object, *, wrap: bool, flags: int = 0):  # type: ignore[no-untyped-def]
            self.boxes.append((label, value))
            return False, (value if typed is None else typed)

        fake = MagicMock()
        for name in ("ImVec2", "InputTextFlags_", "ChildFlags_", "MouseButton_", "Key"):
            setattr(fake, name, getattr(real_imgui, name))
        fake.text_colored.side_effect = record(1)
        fake.text_disabled.side_effect = record(0)
        fake.text.side_effect = record(0)
        fake.button.side_effect = button
        fake.small_button.side_effect = button
        fake.radio_button.side_effect = button
        fake.begin_child.side_effect = child
        fake.is_key_pressed.return_value = False
        fake.is_mouse_released.return_value = released
        fake.get_time.return_value = time
        fake.get_font_size.return_value = 13.0
        fake.calc_text_size.side_effect = lambda text: real_imgui.ImVec2(7.0 * len(text), 13.0)
        fake.get_content_region_avail.return_value = real_imgui.ImVec2(700.0, 0.0)
        fake.get_cursor_screen_pos.return_value = real_imgui.ImVec2(0.0, 0.0)
        fake.get_item_rect_max.return_value = real_imgui.ImVec2(0.0, 0.0)
        fake.set_clipboard_text.side_effect = self.clipboard.append
        monkeypatch.setattr(command_view, "imgui", fake)
        monkeypatch.setattr(board_pane, "imgui", fake)
        monkeypatch.setattr(board_pane, "author", through("author", REAL_AUTHOR))
        monkeypatch.setattr(command_view, "command_block", through("block", REAL_BLOCK))
        monkeypatch.setattr(command_view, "segments_block", through("segments", REAL_SEGMENTS))
        monkeypatch.setattr(command_view, "process_output", through("process", REAL_OUTPUT))
        monkeypatch.setattr(command_view, "multiline_input", box)

    def by(self, where: str) -> list[str]:
        return [line.text for line in self.said if line.where == where and line.how == "text"]

    def own(self) -> list[str]:
        return self.by("pane")

    def buttons(self) -> list[str]:
        return [line.text for line in self.said if line.how == "button"]

    def everything(self) -> str:
        return "\n".join(line.text for line in self.said)


def show(
    monkeypatch: pytest.MonkeyPatch,
    state: CommandViewState,
    cmd: Command,
    calls: Calls,
    *,
    where: Where = BASH,
    focused: str | None = CID,
    attend: bool = True,
    opened: bool = True,
    **screen: typing.Any,
) -> Screen:
    """
    One frame of the row: ``attend``, then the draw. The row is one the
    operator opened, unless ``opened`` is False.
    """
    shown = Screen(monkeypatch, **screen)
    if opened and focused == cmd.id:
        # Long enough ago, on the screen's clock, for a first press to answer.
        state.open(cmd.id, at=0.0)
    if attend:
        state.attend({cmd.id: cmd}, focused, 1)
    command_view.draw_row(
        state, cmd, role="builder", where=where, actions=calls.actions, now=162.0, log_path=None
    )
    return shown


def _rows_of(text: str) -> list[str]:
    """What the block draws for ``text`` at the recording screen's width."""
    return [row.text for row in command_view.block_rows(text, 200)]


def _block_text(screen: Screen, where: str = "block") -> list[str]:
    """The characters a block drew, without its gutters."""
    gutters = re.compile(r" *(?:\d+|\.\.\.)")
    return [text for text in screen.by(where) if not gutters.fullmatch(text)]


def test_the_asked_row_reads_top_to_bottom_standing_command_breakdown_asker_presses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = show(monkeypatch, CommandViewState(), command(), Calls())

    order = [line.text for line in screen.said]
    marks = [
        command_view.RUNS_AS_YOU,
        command_view.NO_TERMINAL,
        command_view.asked_heading("builder"),
        ASKED,
        commandtext.HEADING,
        commandtext.ADVISORY,
        REASON,
        EXPECTS,
        command_view.RUN,
    ]
    assert [order.index(mark) for mark in marks] == sorted(order.index(mark) for mark in marks)
    assert order[0] == command_view.RUNS_AS_YOU
    assert screen.buttons() == [
        "2 minutes",
        "10 minutes",
        "30 minutes",
        "2 hours",
        command_view.RUN,
        command_view.EDIT,
        command_view.REFUSE,
    ]
    assert screen.boxes == []


def test_the_reason_and_the_expectation_are_drawn_behind_the_bar_and_nowhere_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The reason is written to pass for the standing line, the advisory and a
    button. Each of those is still on the row once, as this application's.
    """
    state = CommandViewState()
    calls = Calls()
    views = [
        show(monkeypatch, state, command(), calls),
        show(monkeypatch, state, command(), calls, press=frozenset({command_view.RUN})),
        show(monkeypatch, state, command(), calls),
        show(monkeypatch, CommandViewState(), command(run=run_of()), calls),
        show(monkeypatch, CommandViewState(), ended(Exited(0)), calls, attend=False),
    ]

    for screen in views:
        for line in screen.said:
            if line.where != "author":
                assert "REASON-MARK" not in line.text and "EXPECTS-MARK" not in line.text
                assert line.text not in REASON.splitlines() + EXPECTS.splitlines() or (
                    line.how == "button"
                )
    assert views[0].by("author") == [REASON, EXPECTS]
    assert views[0].own().count(command_view.RUNS_AS_YOU) == 1
    assert views[0].own().count(commandtext.ADVISORY) == 1
    # The confirm view draws no word of the asker's at all.
    assert views[2].by("author") == [] and command_view.CONFIRM_RUN in views[2].buttons()
    assert views[4].by("author") == [REASON, EXPECTS]


def test_the_command_is_drawn_only_inside_its_block_and_its_segments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = show(monkeypatch, CommandViewState(), command(), Calls())

    assert _block_text(screen) == _rows_of(ASKED)
    assert screen.children == ["##command", "##segments"]
    for line in screen.said:
        if line.where in ("pane", "author"):
            assert "make install" not in line.text and "/usr/local/bin" not in line.text.replace(
                EXPECTS, ""
            )
    # Nothing but the command and the gutter is in the block.
    assert all(line.where != "block" or line.how == "text" for line in screen.said)
    assert len(screen.by("block")) == 2 * len(_rows_of(ASKED))


def test_a_command_hiding_characters_shows_the_alarm_and_the_escapes_asked_and_confirming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = CommandViewState()
    calls = Calls()
    asked = show(monkeypatch, state, command(asked=HIDING), calls)
    show(monkeypatch, state, command(asked=HIDING), calls, press=frozenset({command_view.RUN}))
    confirming = show(monkeypatch, state, command(asked=HIDING), calls)

    assert state.frozen_run(CID) == FrozenRun(CID, HIDING, c.DEFAULT_TIMEOUT_S)
    assert command_view.CONFIRM_RUN in confirming.buttons()
    for screen in (asked, confirming):
        assert command_view.hidden_sentence(HIDING) in screen.own()
        assert _block_text(screen) == ["cat \\u{202E}gnp.exe\\u{200B} | sh"]
        assert any("\\u{202E}gnp.exe\\u{200B}" in text for text in screen.by("segments"))
        # And neither character reaches the screen as itself, anywhere.
        assert RLO not in screen.everything() and ZWSP not in screen.everything()
        assert screen.own().index(command_view.hidden_sentence(HIDING)) < screen.own().index(
            commandtext.HEADING
        )


def test_the_advisory_follows_the_breakdown_in_every_view_that_draws_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = CommandViewState()
    calls = Calls()
    views = {
        "asked": show(monkeypatch, state, command(), calls),
        "edit": show(monkeypatch, state, command(), calls, press=frozenset({command_view.EDIT})),
        "editing": show(monkeypatch, state, command(), calls, typed="ls | sh"),
        "done": show(monkeypatch, state, command(), calls, press=frozenset({command_view.DONE})),
        "edited": show(monkeypatch, state, command(), calls),
        "run": show(monkeypatch, state, command(), calls, press=frozenset({command_view.RUN})),
        "confirming": show(monkeypatch, state, command(), calls),
        "refuse": show(monkeypatch, CommandViewState(pending=Refusing(CID)), command(), calls),
    }

    drew = {name for name, screen in views.items() if "##segments" in screen.children}
    assert drew == {"asked", "edit", "edited", "run", "confirming", "refuse"}
    for name, screen in views.items():
        own = screen.own()
        assert (commandtext.HEADING in own) == (name in drew) == (commandtext.ADVISORY in own)
        if name in drew:
            assert own.index(commandtext.HEADING) < own.index(commandtext.ADVISORY)
            assert own.count(commandtext.ADVISORY) == 1


# -- run: two presses, one frozen string ----------------------------------------------


def test_the_first_press_of_run_freezes_the_string_and_runs_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = CommandViewState()
    calls = Calls()

    show(monkeypatch, state, command(), calls, press=frozenset({command_view.RUN}))

    assert calls.nothing
    assert state.pending == FrozenRun(CID, ASKED, c.DEFAULT_TIMEOUT_S)


def test_the_second_press_runs_the_frozen_string_and_nothing_the_state_holds_besides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The edit draft is changed under the confirm view, which no operator can do:
    whatever did would still not change what the second press sends.
    """
    edit = "  make install PREFIX=$HOME \n"
    state = CommandViewState(edits={CID: edit}, timeouts={CID: 1800})
    calls = Calls()
    show(monkeypatch, state, command(), calls, press=frozenset({command_view.RUN}))
    frozen = state.pending

    state.edits[CID] = "rm -rf ~"
    state.timeouts[CID] = 7200
    confirming = show(monkeypatch, state, command(), calls, typed="rm -rf /", time=101.0)
    assert calls.nothing
    assert _block_text(confirming) == [*_rows_of(ASKED), *_rows_of(edit)]
    assert confirming.boxes == []

    show(
        monkeypatch,
        state,
        command(),
        calls,
        press=frozenset({command_view.CONFIRM_RUN}),
        time=101.0,
    )

    assert calls.ran == [(CID, edit, 1800)]
    assert isinstance(frozen, FrozenRun) and (frozen.text, frozen.timeout_s) == (edit, 1800)
    assert not calls.refused and not calls.stopped
    assert state.pending is None


def test_the_confirm_view_draws_no_box_and_no_way_to_change_what_is_frozen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = CommandViewState(pending=command_view.freeze(CID, ASKED, 600))

    screen = show(monkeypatch, state, command(), Calls())

    assert screen.boxes == []
    assert screen.buttons() == [command_view.BACK, command_view.CONFIRM_RUN]
    assert command_view.confirm_run_line(FrozenRun(CID, ASKED, 600), False) in screen.own()


def test_the_two_clicks_of_a_double_click_are_not_the_two_presses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = CommandViewState()
    calls = Calls()
    show(monkeypatch, state, command(), calls, press=frozenset({command_view.RUN}), time=50.0)

    confirm = frozenset({command_view.CONFIRM_RUN})
    show(monkeypatch, state, command(), calls, press=confirm, time=50.2)
    assert calls.nothing and isinstance(state.pending, FrozenRun)

    show(monkeypatch, state, command(), calls, press=confirm, time=50.0 + command_view.ARM_DELAY_S)
    assert calls.ran == [(CID, ASKED, c.DEFAULT_TIMEOUT_S)]


@pytest.mark.parametrize(
    "label", [command_view.RUN, command_view.CONFIRM_RUN, command_view.CONFIRM_REFUSE]
)
def test_a_button_activated_by_a_key_is_not_a_press(
    monkeypatch: pytest.MonkeyPatch, label: str
) -> None:
    """
    Dear ImGui reports a button activated by Space or Enter as pressed. The
    mouse was not released on that frame, and that is what the row goes by.
    """
    armed = {
        command_view.RUN: None,
        command_view.CONFIRM_RUN: command_view.freeze(CID, ASKED, 600),
        command_view.CONFIRM_REFUSE: Refusing(CID),
    }[label]
    state = CommandViewState(pending=armed)
    calls = Calls()

    show(monkeypatch, state, command(), calls, press=frozenset({label}), released=False)

    assert calls.nothing and state.pending == armed


def test_back_runs_nothing_and_returns_to_the_row_as_it_was(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = CommandViewState(edits={CID: "ls"}, pending=command_view.freeze(CID, "ls", 600))
    calls = Calls()

    show(monkeypatch, state, command(), calls, press=frozenset({command_view.BACK}))
    after = show(monkeypatch, state, command(), calls)

    assert calls.nothing and state.pending is None and state.edits == {CID: "ls"}
    assert command_view.RUN in after.buttons()


def test_the_timeout_picked_at_the_row_is_the_one_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    state = CommandViewState()
    calls = Calls()

    show(monkeypatch, state, command(), calls, press=frozenset({"2 hours"}))
    assert state.timeouts == {CID: 7200} and state.pending is None
    show(monkeypatch, state, command(), calls, press=frozenset({command_view.RUN}))

    assert state.pending == FrozenRun(CID, ASKED, 7200)


def test_with_no_shell_run_arms_nothing_even_if_the_greyed_button_reports_a_click(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = CommandViewState()
    calls = Calls()

    screen = show(
        monkeypatch,
        state,
        command(),
        calls,
        where=Where(shell=None, cwd="/work"),
        press=frozenset({command_view.RUN}),
    )

    assert state.pending is None and calls.nothing
    assert command_view.NO_SHELL in screen.own()


# -- a withdrawal between the presses -------------------------------------------------


@pytest.mark.parametrize(
    "now",
    [ended(Withdrawn()), ended(Lapsed()), command(run=run_of()), ended(Refused(""))],
    ids=["withdrawn", "lapsed", "running", "refused"],
)
def test_a_command_that_stops_being_asked_between_the_presses_is_not_run(
    monkeypatch: pytest.MonkeyPatch, now: Command
) -> None:
    state = CommandViewState()
    calls = Calls()
    show(monkeypatch, state, command(), calls, press=frozenset({command_view.RUN}))
    assert isinstance(state.pending, FrozenRun)

    confirm = frozenset({command_view.CONFIRM_RUN, command_view.RUN})
    screen = show(monkeypatch, state, now, calls, press=confirm, time=200.0)

    assert calls.nothing
    assert state.pending is None
    assert command_view.CONFIRM_RUN not in screen.buttons()


def test_a_stale_frozen_run_is_not_sent_by_a_draw_nobody_attended_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    ``attend`` is the caller's to call. A row drawn without it, for a command
    withdrawn since the first press, still offers nothing to press.
    """
    state = CommandViewState(pending=command_view.freeze(CID, ASKED, 600))
    calls = Calls()
    press = frozenset({command_view.CONFIRM_RUN, command_view.RUN, command_view.CONFIRM_STOP})

    for now in (ended(Withdrawn()), command(run=run_of())):
        screen = show(monkeypatch, state, now, calls, press=press, attend=False, time=200.0)
        assert calls.nothing
        assert command_view.CONFIRM_RUN not in screen.buttons()

    other = show(monkeypatch, state, command(OTHER), calls, press=press, attend=False, time=200.0)
    assert not calls.ran and command_view.CONFIRM_RUN not in other.buttons()


# -- the edit -------------------------------------------------------------------------


def test_edit_opens_a_box_holding_the_asked_string_and_says_the_box_is_raw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = CommandViewState()
    calls = Calls()
    cmd = command(asked=HIDING)

    show(monkeypatch, state, cmd, calls, press=frozenset({command_view.EDIT}))
    editing = show(monkeypatch, state, cmd, calls)

    assert editing.boxes == [("##edit", HIDING)]
    assert command_view.RAW_NOTE in editing.own()
    assert editing.buttons() == [command_view.DONE, command_view.DISCARD_EDIT]
    assert editing.children == []
    assert state.candidate(cmd) == HIDING and calls.nothing


def test_an_edited_command_is_drawn_under_its_marker_with_what_was_asked_dimmed_above(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    edit = "make install PREFIX=$HOME/.local"
    state = CommandViewState(edits={CID: edit})

    screen = show(monkeypatch, state, command(), Calls())

    own = screen.own()
    assert own.index(command_view.edited_heading("builder")) < own.index(command_view.EDITED_BY_YOU)
    assert command_view.asked_heading("builder") not in own
    assert screen.children == ["##asked", "##command", "##segments"]
    assert _block_text(screen) == [*_rows_of(ASKED), *_rows_of(edit)]
    # The breakdown is of the edit, which is what would run.
    assert any("PREFIX" in text for text in screen.by("segments"))
    assert not any("sudo" in text for text in screen.by("segments"))


def test_an_edit_that_changes_nothing_is_not_called_an_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = show(monkeypatch, CommandViewState(edits={CID: ASKED}), command(), Calls())

    assert command_view.EDITED_BY_YOU not in screen.own()
    assert screen.children == ["##command", "##segments"]


@pytest.mark.parametrize("edit", ["-rf /", "", "+x", "x" * (c.MAX_COMMAND_CHARS + 1)])
def test_an_edit_the_record_would_refuse_is_not_frozen_by_run_and_the_row_says_why(
    monkeypatch: pytest.MonkeyPatch, edit: str
) -> None:
    state = CommandViewState(edits={CID: edit})
    calls = Calls()

    screen = show(monkeypatch, state, command(), calls, press=frozenset({command_view.RUN}))
    show(monkeypatch, state, command(), calls, press=frozenset({command_view.CONFIRM_RUN}))

    assert state.pending is None and calls.nothing
    assert command_view.CANNOT_RUN + command_view.refusal_of_edit(edit) in screen.own()
    assert command_view.refusal_of_edit(edit)


def test_discarding_an_edit_returns_to_what_was_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    state = CommandViewState(edits={CID: "ls"}, editing=CID)
    cmd = command()

    show(monkeypatch, state, cmd, Calls(), press=frozenset({command_view.DISCARD_EDIT}))

    assert state.edits == {} and state.editing is None and state.candidate(cmd) == ASKED


# -- refuse ---------------------------------------------------------------------------


def test_refuse_is_two_presses_and_sends_the_reason_as_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reason = "  not until the branch is rebased \n"
    state = CommandViewState()
    calls = Calls()

    show(monkeypatch, state, command(), calls, press=frozenset({command_view.REFUSE}), time=10.0)
    assert state.pending == Refusing(CID) and calls.nothing
    box = show(monkeypatch, state, command(), calls, typed=reason, time=10.1)
    assert box.boxes == [("##reason", "")] and calls.nothing
    assert box.buttons() == [command_view.BACK, command_view.CONFIRM_REFUSE]

    confirm = frozenset({command_view.CONFIRM_REFUSE})
    show(monkeypatch, state, command(), calls, press=confirm, time=11.0)

    assert calls.refused == [(CID, reason)] and not calls.ran
    assert state.pending is None


def test_a_reason_longer_than_an_askers_may_be_is_not_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reason = "n" * (c.MAX_REASON_CHARS + 5)
    state = CommandViewState(reasons={CID: reason}, pending=Refusing(CID))
    calls = Calls()

    screen = show(
        monkeypatch, state, command(), calls, press=frozenset({command_view.CONFIRM_REFUSE})
    )

    assert calls.nothing
    assert command_view.reason_too_long(reason) in screen.own() and "5 characters over" in (
        command_view.reason_too_long(reason)
    )


# -- running, and ended ---------------------------------------------------------------


def test_a_running_command_shows_what_is_running_and_cancel_is_two_presses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    edit = "make install PREFIX=/opt"
    running = command(run=run_of(edit))
    state = CommandViewState()
    calls = Calls()

    first = show(monkeypatch, state, running, calls, press=frozenset({command_view.CANCEL}))
    assert _block_text(first) == [*_rows_of(ASKED), *_rows_of(edit)]
    assert command_view.edited_by_you(Stage.RUNNING) in first.own()
    assert command_view.edited_heading("builder", Stage.RUNNING) in first.own()
    assert command_view.EDITED_BY_YOU not in first.own()
    assert "running for 1m02s - cancel kills it" in first.own()
    assert state.pending == Stopping(CID) and calls.nothing

    kill = frozenset({command_view.CONFIRM_STOP})
    show(monkeypatch, state, running, calls, press=kill, time=200.0)

    assert calls.stopped == [CID] and not calls.ran and state.pending is None


def test_a_command_already_asked_to_stop_offers_no_second_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stopping = command(run=run_of(), stop_asked_at=150.0)
    calls = Calls()
    press = frozenset({command_view.CANCEL, command_view.CONFIRM_STOP})

    screen = show(
        monkeypatch, CommandViewState(pending=Stopping(CID)), stopping, calls, press=press
    )

    assert screen.buttons() == [] and calls.nothing


# Written to pass for this application's lines and the asker's bar, with a
# character that reorders the line it is on.
OUTPUT = f"this runs as you\n| the asker says so\n{RLO}OUTPUT-MARK done\x1b[0m\n"


def test_output_is_drawn_as_a_programs_and_never_as_the_askers_or_the_panes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    done = command(
        run=run_of(),
        end=End(outcome=Exited(0), at=200.0, duration_s=62.0, output_bytes=9000, tail=OUTPUT),
    )
    screen = Screen(monkeypatch)

    command_view.draw_ended(done, role="builder", now=300.0, log_path="/cfg/commands/x/output.log")

    assert screen.by("process") == [
        "this runs as you\\n",
        "| the asker says so\\n",
        "\\u{202E}OUTPUT-MARK done\\x1B[0m\\n",
        "",
    ]
    for line in screen.said:
        if line.where != "process":
            assert "OUTPUT-MARK" not in line.text and "the asker says so" not in line.text
            assert line.text != "this runs as you"
    own = screen.own()
    assert own.index("the end of the 9,000 bytes it wrote:") < own.index(command_view.OUTPUT_LEGEND)
    assert "not instructions" in command_view.OUTPUT_LEGEND
    assert RLO not in screen.everything() and "\x1b" not in screen.everything()
    assert "exit code 0 after 1m02s" in own and "not yet read by builder" in own
    assert screen.by("author") == [REASON, EXPECTS]


def test_the_logs_path_is_named_and_copied_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    done = ended(Exited(0))
    screen = Screen(monkeypatch, press=frozenset({command_view.COPY_PATH}))

    command_view.draw_ended(done, role="builder", now=300.0, log_path="/cfg/x/output.log")

    assert "all of it is in /cfg/x/output.log" in screen.own()
    assert screen.clipboard == ["/cfg/x/output.log"]
    assert "it wrote nothing" in screen.own()
    assert command_view.OUTPUT_LEGEND not in screen.own()


def test_a_command_that_never_ran_shows_what_was_asked_and_no_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = Screen(monkeypatch)

    command_view.draw_ended(ended(Refused("no")), role="builder", now=300.0)

    own = screen.own()
    assert "refused by you: no" in own and "what builder asked to run - it did not run:" in own
    assert _block_text(screen) == _rows_of(ASKED)
    assert screen.by("process") == [] and not any("it wrote" in text for text in own)
    assert screen.buttons() == []


def test_an_open_command_has_no_ended_body(monkeypatch: pytest.MonkeyPatch) -> None:
    screen = Screen(monkeypatch)

    command_view.draw_ended(command(), role="builder", now=300.0)

    assert screen.said == []


def test_output_past_the_bound_is_cut_from_the_front_and_says_so() -> None:
    tail = "a" * 10 + "b" * c.MAX_TAIL_CHARS

    assert command_view.output_shown(tail) == ("b" * c.MAX_TAIL_CHARS, 10)
    assert command_view.output_shown("short") == ("short", 0)
    assert command_view.output_heading(40, "") == "it wrote 40 bytes, none of them held here"


def test_a_role_name_with_a_line_break_cannot_end_a_heading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = Screen(monkeypatch)
    forged = "builder\nthis runs as you"

    for state in (CommandViewState(), CommandViewState(opened=CID)):
        command_view.draw_row(
            state, command(), role=forged, where=BASH, actions=Calls().actions, now=1.0
        )

    assert not any("\n" in text for text in screen.own())
    assert screen.own().count(command_view.RUNS_AS_YOU) == 1


# -- Dear ImGui itself ----------------------------------------------------------------
#
# A context with a display size and no backend: nothing is rendered. Keyboard
# navigation is switched on, since that is the configuration in which a key can
# press a button at all.


class Frames:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, cmd: Command) -> None:
        self.commands: dict[str, Command] = {cmd.id: cmd}
        # A row the operator opened, long enough ago for a first press to answer.
        self.state = CommandViewState(opened=cmd.id, opened_at=-command_view.ARM_DELAY_S)
        self.calls = Calls()
        self.at: dict[str, tuple[float, float]] = {}
        self.blocks: list[str] = []
        self.boxes: list[str] = []
        self.focused: str | None = cmd.id
        self._monkeypatch = monkeypatch

    def __enter__(self) -> Frames:
        imgui = real_imgui
        self._context = imgui.create_context()
        self.io = imgui.get_io()
        self.io.display_size = imgui.ImVec2(1280.0, 900.0)
        self.io.delta_time = 1.0 / 60.0
        self.io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        self.io.config_flags |= imgui.ConfigFlags_.nav_enable_keyboard
        self.io.set_ini_filename("")
        self.io.add_focus_event(True)
        at = self.at

        def note(label: str) -> None:
            low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
            at[label] = ((low.x + high.x) / 2.0, (low.y + high.y) / 2.0)

        class Noting:
            def __getattr__(self, name: str) -> object:
                return getattr(imgui, name)

            def button(self, label: str) -> bool:
                pressed = imgui.button(label)
                note(label)
                return bool(pressed)

            def radio_button(self, label: str, active: bool) -> bool:
                pressed = imgui.radio_button(label, active)
                note(label)
                return bool(pressed)

        def box(label: str, value: str, size: object, *, wrap: bool, flags: int = 0):  # type: ignore[no-untyped-def]
            result = widgets.multiline_input(label, value, size, wrap=wrap, flags=flags)  # type: ignore[arg-type]
            note(label)
            self.boxes.append(label)
            return result

        def block(text: str, **kwargs: object) -> None:
            self.blocks.append(text)
            REAL_BLOCK(text, **kwargs)  # type: ignore[arg-type]

        self._monkeypatch.setattr(command_view, "imgui", Noting())
        self._monkeypatch.setattr(command_view, "multiline_input", box)
        self._monkeypatch.setattr(command_view, "command_block", block)
        return self

    def __exit__(self, *exc: object) -> None:
        real_imgui.destroy_context(self._context)

    def frame(self, count: int = 1) -> None:
        for _ in range(count):
            self.at.clear()
            self.blocks.clear()
            self.boxes.clear()
            real_imgui.new_frame()
            self.state.attend(self.commands, self.focused, real_imgui.get_frame_count())
            real_imgui.set_next_window_pos(real_imgui.ImVec2(0.0, 0.0))
            real_imgui.set_next_window_size(real_imgui.ImVec2(1200.0, 880.0))
            real_imgui.begin("needs you")
            for cmd in self.commands.values():
                command_view.draw_row(
                    self.state, cmd, role="builder", where=BASH, actions=self.calls.actions, now=1.0
                )
            real_imgui.end()
            real_imgui.end_frame()

    def click(self, label: str) -> None:
        x, y = self.at[label]
        self.io.add_mouse_pos_event(x, y)
        self.frame(2)
        self.io.add_mouse_button_event(0, True)
        self.frame()
        self.io.add_mouse_button_event(0, False)
        self.frame(3)

    def wait(self) -> None:
        """Long enough for an armed act to answer its second press."""
        self.frame(int(command_view.ARM_DELAY_S * 60) + 6)

    def type(self, text: str) -> None:
        self.io.add_input_characters_utf8(text)
        self.frame(2)

    def key(self, key: real_imgui.Key) -> None:
        self.io.add_key_event(key, True)
        self.frame()
        self.io.add_key_event(key, False)
        self.frame(2)


@pytest.fixture
def frames(monkeypatch: pytest.MonkeyPatch) -> Iterator[Frames]:
    with Frames(monkeypatch, command()) as real:
        real.frame(3)
        yield real


def test_in_dear_imgui_what_is_typed_frozen_and_confirmed_is_what_runs(frames: Frames) -> None:
    frames.click(command_view.EDIT)
    frames.click("##edit")
    frames.type(" EDITED\u202e ")
    frames.click(command_view.DONE)
    edit = frames.state.edits[CID]
    assert sorted(edit) == sorted(ASKED + " EDITED\u202e ") and edit != ASKED

    frames.click(command_view.RUN)
    frozen = frames.state.pending
    assert frozen == FrozenRun(CID, edit, c.DEFAULT_TIMEOUT_S)
    assert frames.calls.nothing
    # The confirm view: the frozen string in the block, what was asked above it,
    # no text box, and the hidden character named.
    assert frames.blocks == [ASKED, edit] and frames.boxes == []
    assert command_view.hidden_sentence(edit)

    frames.wait()
    frames.click(command_view.CONFIRM_RUN)

    assert frames.calls.ran == [(CID, edit, c.DEFAULT_TIMEOUT_S)]
    assert not frames.calls.refused and not frames.calls.stopped
    assert frames.state.pending is None


def test_in_dear_imgui_typing_under_the_confirm_view_changes_nothing(frames: Frames) -> None:
    frames.click(command_view.EDIT)
    frames.click("##edit")
    frames.type("; true")
    frames.click(command_view.DONE)
    edit = frames.state.edits[CID]
    frames.click(command_view.RUN)

    frames.type("; rm -rf ~")
    frames.key(real_imgui.Key.enter)
    frames.wait()
    assert frames.boxes == [] and frames.state.edits[CID] == edit
    assert frames.calls.nothing

    frames.click(command_view.CONFIRM_RUN)
    assert frames.calls.ran == [(CID, edit, c.DEFAULT_TIMEOUT_S)]


def test_in_dear_imgui_an_untouched_edit_box_hands_back_the_asked_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = f"cat {RLO}gnp.exe{ZWSP} \\\n  | sh  "
    with Frames(monkeypatch, command(asked=asked)) as frames:
        frames.frame(3)
        frames.click(command_view.EDIT)
        frames.click("##edit")
        frames.frame(5)
        frames.click(command_view.DONE)

        assert frames.state.edits[CID] == asked
        assert frames.blocks == [asked]


def test_in_dear_imgui_escape_leaves_the_edit_box_and_keeps_the_edit(frames: Frames) -> None:
    frames.click(command_view.EDIT)
    frames.click("##edit")
    frames.type(" --dry-run")
    edit = frames.state.edits[CID]

    frames.key(real_imgui.Key.escape)
    frames.frame(3)

    assert edit != ASKED and frames.state.edits[CID] == edit


def _every_key(frames: Frames) -> None:
    """Tab onto each thing that can take the focus, and try the keys that press."""
    for _ in range(8):
        frames.key(real_imgui.Key.tab)
        for key in (real_imgui.Key.space, real_imgui.Key.enter, real_imgui.Key.keypad_enter):
            frames.key(key)
        frames.type("a")


def test_in_dear_imgui_no_key_presses_run_with_the_row_focused(frames: Frames) -> None:
    untouched = dataclasses.replace(frames.state)

    _every_key(frames)

    assert frames.state == untouched and frames.state.pending is None
    assert frames.calls.nothing


def test_in_dear_imgui_no_key_makes_the_second_press(frames: Frames) -> None:
    frames.click(command_view.RUN)
    frames.wait()
    frozen = frames.state.pending
    assert isinstance(frozen, FrozenRun)

    _every_key(frames)

    assert frames.calls.nothing
    assert frames.state.pending == frozen


def test_in_dear_imgui_a_second_click_made_at_once_does_not_run(frames: Frames) -> None:
    frames.click(command_view.RUN)
    assert isinstance(frames.state.pending, FrozenRun)

    frames.click(command_view.CONFIRM_RUN)
    assert frames.calls.nothing and isinstance(frames.state.pending, FrozenRun)

    frames.wait()
    frames.click(command_view.CONFIRM_RUN)
    assert frames.calls.ran == [(CID, ASKED, c.DEFAULT_TIMEOUT_S)]


def test_in_dear_imgui_a_withdrawal_between_the_presses_runs_nothing(frames: Frames) -> None:
    frames.click(command_view.RUN)
    frames.wait()
    where = frames.at[command_view.CONFIRM_RUN]

    frames.commands[CID] = ended(Withdrawn())
    frames.frame()
    assert frames.state.pending is None
    assert command_view.CONFIRM_RUN not in frames.at

    # The click the operator was about to make, where the button was.
    frames.at[command_view.CONFIRM_RUN] = where
    frames.click(command_view.CONFIRM_RUN)
    frames.type("a")
    frames.key(real_imgui.Key.enter)

    assert frames.calls.nothing


def test_in_dear_imgui_the_cursor_leaving_the_row_disarms_it(frames: Frames) -> None:
    frames.click(command_view.RUN)
    frames.wait()

    frames.focused = None
    frames.frame()
    frames.focused = CID
    frames.frame()

    # Not armed, and not open either: the cursor coming back opens nothing.
    assert frames.state.pending is None and not frames.state.is_open(CID)
    assert frames.at == {} and frames.blocks == []
    assert frames.calls.nothing


def test_in_dear_imgui_refusing_sends_what_the_box_held(frames: Frames) -> None:
    frames.click(command_view.REFUSE)
    frames.click("##reason")
    frames.type("not on main ")
    frames.wait()
    frames.click(command_view.CONFIRM_REFUSE)

    assert frames.calls.refused == [(CID, "not on main ")]
    assert not frames.calls.ran


def test_in_dear_imgui_a_command_as_long_as_one_may_be_and_full_of_percent_signs_draws(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = ("printf '%s%n%d' " + "x" * 300 + " \n") * 6
    with Frames(monkeypatch, command(asked=asked[: c.MAX_COMMAND_CHARS])) as frames:
        frames.frame(3)

        assert frames.blocks == [asked[: c.MAX_COMMAND_CHARS]]
