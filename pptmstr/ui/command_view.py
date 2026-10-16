"""
A break-glass command at its row: the string, what it hides, and the presses
that run, refuse or stop it.

Not a pane and not wired: nothing here emits an intent or reads the store. The
caller hands over a ``command.Command`` and three callables, and holds one
``CommandViewState``. The decisions this draws are in
``planning/2026-10-13-a-break-glass-command-the-operator-runs.md``.

**It reads as a Bash approval does** (that record's D5): a few one-line
warnings (``warnings``), a bordered body that names ``command``, ``reason`` and
``expects`` as a Bash body names ``command`` and ``description``, and the Bash
row's button line -- a reason box, run, refuse, edit. What is longer than a
line is behind the row's question mark (``explanations``) and nowhere else.

**What is confirmed is what runs.** The command runs outside the sandbox as the
operator, and the operator reading the exact string is the only gate on it. So
the first press of run builds a ``FrozenRun`` from the string then on screen --
as asked, or as edited -- closes the edit box, and from then the body draws
*that value's* text through ``commandtext.visible``, with the warnings made
again from it, while the button reads ``CONFIRM_RUN`` in run's place. The
second press hands the callable that same value's text and nothing else. The
confirmation lasts ``CONFIRM_FOR_S`` and is then dropped.

**Whose words a line is.** Four hands write on this row and each has one way
onto the screen:

- this application's own lines (``Line``), which hold no sentence of anybody's;
- the command, drawn only by ``command_block`` -- escaped, numbered in a gutter,
  cut into rows by ``wrap_shown`` and never by Dear ImGui;
- the asker's reason and expectation, only as the value on the body's
  ``reason =`` and ``expects =`` lines, inside the body's border;
- a program's output, drawn only by ``process_output``, under a legend that says
  it is nobody's instruction.

**No key acts.** Dear ImGui's keyboard navigation activates a focused button on
Space and on Enter, so every button here answers a mouse release only
(``_clicked``). The row has no shortcut of its own; the application's
(``review.Hold``) are the caller's to keep off it.

**A row is closed until the operator opens it.** The cursor comes to rest on a
command by routes the operator did not choose -- the row above was answered, a
row left, this one arrived -- and its asker picks the moment. So resting there
draws ``CLOSED`` and nothing else under the queue's summary line. The body and
every press are drawn only for the one command ``CommandViewState.opened``
names, which is set by a click on that row or the open key with the cursor on
it and by nothing else. It names a command by id, so it cannot pass to another,
and it is dropped when the cursor leaves the row.

**A second press is a second gesture.** An armed act is dropped when the cursor
leaves its row, when the command is no longer in the state the act belongs to,
when a frame went by in which the row was not drawn
(``CommandViewState.attend``), and ``CONFIRM_FOR_S`` after it was armed. It
answers nothing for ``ARM_DELAY_S`` after the first press, so the two clicks of
a double-click are not the two presses; and the first presses answer nothing
for as long after the row was opened, so the click that opened it is not one of
them either.
"""

from __future__ import annotations

import dataclasses
import enum
import functools
import posixpath
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import assert_never

from imgui_bundle import imgui

from .. import commandfile, commandtext
from ..command import (
    COMMAND_TAG,
    DEFAULT_TIMEOUT_S,
    ID_HEX_CHARS,
    MAX_REASON_CHARS,
    MAX_TAIL_CHARS,
    TIMEOUT_CHOICES_S,
    Cancelled,
    CancelledBy,
    Command,
    CommandId,
    CommandState,
    End,
    EndUnknown,
    Exited,
    Lapsed,
    NotStarted,
    Refused,
    Released,
    Survivor,
    TimedOut,
    Withdrawn,
    command_refusal,
    ran,
)
from ..commandrun import PROFILE_WARNING
from ..theme import Color, P, hint_colour
from . import board_pane, decision_view, widgets
from .widgets import author_text, format_elapsed, help_mark, multiline_input, one_line

# Seconds, on Dear ImGui's own clock, before an armed act answers its second
# press, and before a row just opened answers a first one. Longer than the
# double-click interval, which is what it is for.
ARM_DELAY_S = 0.6

# Seconds, on the same clock, an armed run or cancel waits for its second press
# before the button is run or cancel again. A press is answered in the moment
# it was asked for: a confirmation left standing is one a later click, made
# for something else, can land on.
CONFIRM_FOR_S = 5.0

# Seconds a command is marked ``NEW`` after it was asked, on the wall clock
# ``Command.asked_at`` is on. Long enough to be met by an eye already on the
# row, and short enough to be gone before the block has been read through.
NEW_FOR_S = 5.0

# The narrowest the block is cut to, in characters. Wider than the longest
# escape ``visible`` emits, so there is always somewhere to cut that is not
# inside one.
MIN_COLUMNS = 24

_EDIT_BOX_HEIGHT = 96.0
# The reason box on the button line, as wide as a Bash row's.
_REASON_WIDTH = 360.0
# One of the asker's texts as drawn. Past the bound either is accepted at, so
# this only ever cuts a record that came from a file.
_ASKER_CHARS = 4_000


# -- the state the caller holds ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class FrozenRun:
    """
    The run the operator is one press from: this string, under this timeout.

    Built by ``freeze`` at the first press and never changed. What the confirm
    view shows of it is read off ``text`` here, so there is no second copy of
    the string for the screen to have been drawn from.
    """

    command_id: CommandId
    text: str
    timeout_s: int

    @property
    def breakdown(self) -> commandtext.Breakdown:
        return commandtext.breakdown(self.text)

    @property
    def findings(self) -> tuple[commandtext.Finding, ...]:
        return commandtext.invisibles(self.text)


@dataclass(frozen=True, slots=True)
class Stopping:
    """Cancel was pressed once on this running command."""

    command_id: CommandId


@dataclass(frozen=True, slots=True)
class Releasing:
    """Release was pressed once on this command left running unsupervised."""

    command_id: CommandId


Pending = FrozenRun | Stopping | Releasing


def refusal_of_edit(text: str) -> str:
    """
    Why ``text`` cannot be frozen and run, in a sentence, or "" when it can.

    ``command.command_refusal``'s answer: the operator's edit is held to what an
    agent's request is, because it reaches the same shell.
    """
    refusal = command_refusal(text)
    return "" if refusal is None else refusal.sentence


def freeze(command_id: CommandId, text: str, timeout_s: int) -> FrozenRun:
    """
    ``text`` as the run awaiting its second press.

    Raises ``ValueError`` for a string the record would refuse and for a timeout
    that is not one of the choices: a frozen run nobody could start is not
    something to put a "run it" button under.
    """
    refusal = command_refusal(text)
    if refusal is not None:
        raise ValueError(f"this text cannot be frozen: {refusal.value}")
    if timeout_s not in TIMEOUT_CHOICES_S:
        raise ValueError(f"{timeout_s!r} is not one of the timeouts offered")
    return FrozenRun(command_id=command_id, text=text, timeout_s=timeout_s)


@dataclass
class CommandViewState:
    """
    What the operator is part-way through, keyed by command id.

    Presentation state, as ``decision_view.DecisionState`` is: a half-made edit
    is not a fact about the world.
    """

    # The operator's edit of the string. Absent means "as asked".
    edits: dict[CommandId, str] = field(default_factory=dict)
    # The command whose raw edit box is open.
    editing: CommandId | None = None
    # The timeout picked at the row. Absent means the default.
    timeouts: dict[CommandId, int] = field(default_factory=dict)
    # The reason typed for a refusal, kept while the cursor is elsewhere.
    reasons: dict[CommandId, str] = field(default_factory=dict)
    # The act that has had its first press, and when on Dear ImGui's clock.
    pending: Pending | None = None
    armed_at: float = 0.0
    # The one command whose row the operator opened, and when on Dear ImGui's
    # clock. Every other command's row is drawn closed.
    opened: CommandId | None = None
    opened_at: float = 0.0
    # The frame ``attend`` last ran in, by Dear ImGui's count. None before the
    # first. Not part of what two states are compared by: it is when the state
    # was last looked at and nothing the operator did.
    attended: int | None = field(default=None, compare=False)

    def open(self, command_id: CommandId, at: float) -> None:
        """
        The operator's act on this command's row: a click on it, or the open
        key with the cursor on it. ``at`` is Dear ImGui's clock.

        Opening the row that is open changes nothing, so a second click on it
        does not start ``ARM_DELAY_S`` again. Opening another drops an act
        armed on any command but this one.
        """
        if self.opened == command_id:
            return
        self.opened = command_id
        self.opened_at = at
        if self.pending is not None and self.pending.command_id != command_id:
            self.pending = None

    def is_open(self, command_id: CommandId) -> bool:
        return self.opened == command_id

    def attend(
        self, commands: Mapping[CommandId, Command], focused: CommandId | None, frame: int
    ) -> None:
        """
        Once per frame, before any command is drawn. ``focused`` is the command
        under the cursor and ``frame`` Dear ImGui's count of frames.

        Everything typed or picked for a command that is no longer only asked
        is dropped: it was withdrawn, run, refused or lapsed, and none of it can
        be used. The row stops being open when the cursor is on another row or
        the command owes none. An armed act is dropped with it -- the second
        press would be made by somebody who had since read something else --
        and when the frame before this one was not attended: the row was not
        on screen in it (``lapse``).
        """
        self.lapse(frame)
        self.attended = frame
        held = commands.get(self.opened) if self.opened is not None else None
        if self.opened != focused or held is None or not held.owes_row:
            self.opened = None
        asked = {
            command_id
            for command_id, command in commands.items()
            if command.state is CommandState.ASKED
        }
        for drafts in (self.edits, self.timeouts, self.reasons):
            for command_id in [c for c in drafts if c not in asked]:
                del drafts[command_id]
        if self.editing not in asked:
            self.editing = None
        if self.pending is not None and not self._still_armed(commands, focused):
            self.pending = None

    def lapse(self, frame: int) -> None:
        """
        Drop the armed act if a whole frame has gone by that ``attend`` did not
        run in. ``attend`` runs where the queue is drawn, so that is a frame
        the armed row was not on screen for, and the press that armed it does
        not carry across one.
        """
        if self.attended is not None and frame > self.attended + 1:
            self.pending = None

    def _still_armed(
        self, commands: Mapping[CommandId, Command], focused: CommandId | None
    ) -> bool:
        pending = self.pending
        if pending is None or pending.command_id != focused or pending.command_id != self.opened:
            return False
        command = commands.get(pending.command_id)
        if command is None:
            return False
        match pending:
            case FrozenRun():
                return command.state is CommandState.ASKED
            case Stopping():
                return command.state is CommandState.RUNNING and command.stop_asked_at is None
            case Releasing():
                return command.unsupervised is not None
            case _:
                assert_never(pending)

    def expire(self, now: float) -> None:
        """
        Drop an armed run or cancel whose second press has not come within
        ``CONFIRM_FOR_S`` of the first. ``now`` is Dear ImGui's clock. A
        release keeps its own confirmation, which has a back button.
        """
        if isinstance(self.pending, FrozenRun | Stopping) and now - self.armed_at > CONFIRM_FOR_S:
            self.pending = None

    def candidate(self, command: Command) -> str:
        """The string run would freeze: the operator's edit, or what was asked."""
        return self.edits.get(command.id, command.asked)

    def timeout_s(self, command_id: CommandId) -> int:
        return self.timeouts.get(command_id, DEFAULT_TIMEOUT_S)

    def frozen_run(self, command_id: CommandId) -> FrozenRun | None:
        """The run awaiting its second press on this command, if one is."""
        pending = self.pending
        if isinstance(pending, FrozenRun) and pending.command_id == command_id:
            return pending
        return None

    def armed(self, command_id: CommandId) -> Pending | None:
        """The act awaiting its second press on this command, whichever it is."""
        if self.pending is None or self.pending.command_id != command_id:
            return None
        return self.pending


@dataclass(frozen=True, slots=True)
class Where:
    """
    What a command would be run by and in, as the caller found it.

    ``shell`` is None when no usable shell was found, and then run is not
    offered. ``stripped_env`` names what the runner takes out of pptmstr's
    environment before the shell starts, in the caller's words. ``home`` is
    the operator's home directory where the caller knows it, and is used only
    to recognise ``cwd`` written from ``~`` (``commandtext.spellings``).
    """

    shell: str | None
    cwd: str
    stripped_env: tuple[str, ...] = ()
    home: str | None = None

    @property
    def roots(self) -> tuple[str, ...]:
        """
        The session's directory as a command might write it out in full.
        """
        return commandtext.spellings(self.cwd, self.home)


@dataclass(frozen=True, slots=True)
class Among:
    """
    Where a command stands among its asker's, as the caller counted it.

    ``in_session`` counts from one over every command asked in the command's
    session, by any agent and ended ones included: no two commands of a
    session share it. ``ordinal`` counts the same way over this asker's alone,
    and two askers each have a first. ``others_waiting`` is how many more of
    this asker's are still only asked. ``new`` is ``is_new``.
    """

    in_session: int
    ordinal: int
    others_waiting: int = 0
    new: bool = False


@dataclass(frozen=True, slots=True)
class Actions:
    """What the three second presses call. Supplied by the caller, called with no more."""

    on_run: Callable[[CommandId, str, int], None]
    on_refuse: Callable[[CommandId, str], None]
    on_stop: Callable[[CommandId], None]


# -- pure helpers, testable without a context --------------------------------------


class Tone(enum.Enum):
    """How a line of this application's is coloured. ``SEGMENT`` is not one of its lines."""

    PLAIN = "plain"
    DIM = "dim"
    WARN = "warn"
    ALARM = "alarm"
    # A piece of the command, shown escaped. Drawn by ``command_block``.
    SEGMENT = "segment"


@dataclass(frozen=True, slots=True)
class Line:
    """
    One line. ``gutter`` is only a ``SEGMENT``'s: its number, after the operator
    that precedes it.
    """

    text: str
    tone: Tone = Tone.PLAIN
    gutter: str = ""


@dataclass(frozen=True, slots=True)
class Row:
    """One drawn row of a block: what the gutter says, and the characters beside it."""

    gutter: str
    text: str


# What the gutter says on a row that continues the one above. The gutter is
# outside the filled extent that marks a row's characters, so nothing in a
# command can put this there.
CONTINUES = "..."


@functools.lru_cache(maxsize=64)
def block_lines(text: str) -> tuple[str, ...]:
    """
    ``commandtext.visible(text)`` as the lines of the command.

    A line ends where the command has a newline, and ends in the ``\\n`` marker
    ``visible`` puts there; joined by newlines they are ``visible(text)``
    exactly. Only printable ASCII is in them.
    """
    return tuple(commandtext.visible(text).split("\n"))


# One character of ``visible``'s output, or one whole escape.
_TOKEN = re.compile(r"\\(?:\\|n|r|x[0-9A-F]{2}|u\{[0-9A-F]+\})|.", re.DOTALL)


@functools.lru_cache(maxsize=512)
def wrap_shown(line: str, width: int) -> tuple[str, ...]:
    """
    One line of ``visible`` output cut into rows of at most ``width`` characters.

    The rows joined are ``line``: nothing is added, dropped or trimmed. A cut is
    made as far right as it can be that is

    1. not inside an escape -- ``\\u{202E}`` stays in one piece -- and
    2. not beside a space, so no space is ever the last character of a row or
       the first of the next, where it would be read as nothing or as an indent.

    That puts a cut inside a word more often than a word wrap would, and a word
    wrap is exactly what cannot be had: it swallows the spaces it breaks at.
    Where no cut in the last quarter of the row satisfies 2 -- a run of spaces
    as wide as that, or single characters with spaces between -- it is dropped
    and the row's filled extent is what shows the space. Only the last quarter
    is looked in, so every row but a line's last is at least three quarters of
    ``width`` and the text decides how tall its block is by its length alone.
    Where none satisfies 1, which takes a ``width`` under ``MIN_COLUMNS``, the
    cut is at ``width``.
    """
    if width < 1:
        raise ValueError(f"a row is at least one character wide, not {width}")
    rows: list[str] = []
    rest = line
    while len(rest) > width:
        bounds = [match.end() for match in _TOKEN.finditer(rest, 0, width + 10)]
        whole = [end for end in bounds if end <= width]
        clear = [
            end
            for end in whole
            if end >= width - width // 4 and rest[end - 1] != " " and rest[end] != " "
        ]
        cut = clear[-1] if clear else whole[-1] if whole else width
        rows.append(rest[:cut])
        rest = rest[cut:]
    rows.append(rest)
    return tuple(rows)


def _rows(shown: str, width: int, gutters: Callable[[int], str]) -> tuple[Row, ...]:
    rows: list[Row] = []
    for index, line in enumerate(shown.split("\n")):
        for position, piece in enumerate(wrap_shown(line, width)):
            rows.append(Row(gutters(index) if position == 0 else CONTINUES, piece))
    return tuple(rows)


def block_rows(text: str, width: int) -> tuple[Row, ...]:
    """
    The command as the block draws it: each line of ``block_lines`` under its
    number, counted from one, and each row a line was cut into under ``CONTINUES``.

    So a row with a number begins where the command begins or has a newline
    (whose marker ends the row above), and a row without one is the block's
    width and nothing in the command.
    """
    return _rows(commandtext.visible(text), width, lambda index: str(index + 1))


@functools.lru_cache(maxsize=64)
def hidden_sentence(text: str) -> str:
    """What ``visible`` had to spell out in ``text``, as one sentence; "" for nothing."""
    return commandtext.describe(commandtext.invisibles(text))


# -- the warnings: one line each, and each said only where it is true ---------------

RUNS_OUTSIDE = "Runs outside the sandbox, as you: your credentials and network."
NOT_CHECKED = "Not checked by your Claude Code permission rules or hooks."
EDITED = "Edited: the agent will be told what actually ran."
NO_SHELL = "No usable shell was found: this cannot be run from here."
NOT_SPLIT = "pptmstr could not cut it into commands: read it whole."
NEW = "New: asked a moment ago."

# Names a login shell that reads a command line the way the warnings assume.
_POSIX_SHELLS = frozenset({"sh", "bash", "dash", "ksh", "mksh", "ash", "zsh"})

# A word that sets a variable for the command after it, and is not the command.
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")

# The longest a program's name is said in a warning; a longer word is cut and
# marked. The block has the word whole.
_NAME_CHARS = 32


def posix_looking(shell: str) -> bool:
    """Whether the shell's name is one of the Bourne family. A name, not a test."""
    return posixpath.basename(shell) in _POSIX_SHELLS


def where_line(shell: str | None, cwd: str, timeout_s: float | None = None) -> Line:
    """
    What runs the command and where, in one line; ``NO_SHELL`` where nothing
    can. ``timeout_s`` is a running command's own, and makes the line its.
    """
    if shell is None:
        return Line(NO_SHELL, Tone.ALARM)
    if timeout_s is not None:
        return Line(
            f"Running in {one_line(cwd)} by {one_line(shell)} -l; killed after"
            f" {timeout_label(timeout_s)}.",
            Tone.WARN,
        )
    return Line(
        f"Runs in {one_line(cwd)} by {one_line(shell)} -l: your profile first, no terminal.",
        Tone.WARN,
    )


def program_name(segment: str) -> str:
    """
    The word a segment starts with that is not a variable being set, escaped as
    the block escapes it. A name, not a reading: ``env``, ``sudo`` and ``xargs``
    are the name of what they run.
    """
    words = segment.split()
    word = next((w for w in words if not _ASSIGNMENT.match(w)), words[0] if words else "")
    shown = commandtext.visible(word) or "''"
    return shown if len(shown) <= _NAME_CHARS else shown[: _NAME_CHARS - 3] + "..."


@functools.lru_cache(maxsize=64)
def text_warnings(text: str, roots: tuple[str, ...] = ()) -> tuple[Line, ...]:
    """
    What the string itself holds that its reader must not miss, a line for
    each that is so: characters ``visible`` had to spell out, programs known to
    run files from the session's directory (``commandtext.RUNS_FROM_THE_TREE``),
    what else the scan noted, and the commands it is a sequence of where it is
    more than one. ``roots`` is that directory as a command might write it out
    (``Where.roots``).

    Every line but the first is the quote-blind scan's reading, and one missing
    proves nothing; ``explanations`` says so behind the question mark.
    """
    lines: list[Line] = []
    hidden = hidden_sentence(text)
    if hidden:
        lines.append(Line(hidden, Tone.ALARM))
    found = commandtext.breakdown(text, roots)
    kinds = [flag.kind for flag in found.flags]
    tree = [kind.value for kind in kinds if kind in commandtext.RUNS_FROM_THE_TREE]
    if tree:
        named = ", ".join(dict.fromkeys(tree))
        lines.append(Line(f"May run files this session can write: {named}.", Tone.WARN))
    noted = [
        kind.value
        for kind in kinds
        if kind not in commandtext.RUNS_FROM_THE_TREE and kind is not commandtext.FlagKind.NOT_SPLIT
    ]
    if noted:
        lines.append(Line(f"Uses {', '.join(dict.fromkeys(noted))}.", Tone.WARN))
    if commandtext.FlagKind.NOT_SPLIT in kinds:
        lines.append(Line(NOT_SPLIT, Tone.WARN))
    elif len(found.segments) > 1:
        names = ", ".join(program_name(segment) for segment in found.segments)
        lines.append(Line(f"{len(found.segments)} commands in sequence: {names}.", Tone.WARN))
    return tuple(lines)


def warnings(
    command: Command,
    text: str,
    where: Where,
    role: str,
    among: Among | None = None,
    *,
    timeout_s: float | None = None,
) -> tuple[Line, ...]:
    """
    Every warning the row says over ``text``, the string that would run or is
    running, one line each: the two that are always true, what runs it and
    where (``where_line``; ``timeout_s`` for a running command), what the
    string holds (``text_warnings``), that it is not what was asked, and that
    it is new or not its asker's only one.
    """
    lines = [Line(RUNS_OUTSIDE, Tone.WARN), Line(NOT_CHECKED, Tone.WARN)]
    lines.append(where_line(where.shell, where.cwd, timeout_s))
    if where.shell is not None and not posix_looking(where.shell):
        lines.append(
            Line(
                f"{one_line(posixpath.basename(where.shell))} may not read it as bash does:"
                " what is said here is bash's reading.",
                Tone.WARN,
            )
        )
    if text != command.asked:
        lines.append(Line(EDITED, Tone.WARN))
    lines += text_warnings(text, where.roots)
    if among is not None and among.new:
        lines.append(Line(NEW, Tone.ALARM))
    if among is not None and among.others_waiting:
        count = among.others_waiting
        lines.append(
            Line(
                f"{role} has {count} other command{'' if count == 1 else 's'} waiting:"
                " read each one.",
                Tone.WARN,
            )
        )
    return tuple(lines)


# What stands behind the row's question mark: the warnings said in full.
RUNS_AS_YOU = (
    "This runs as you, outside the sandbox, with your credentials and your network, and"
    " may execute files this session wrote."
)
RULES_DO_NOT_APPLY = (
    "Your Claude Code allow and deny rules and hooks do not apply to it: pptmstr starts it"
    " and the CLI never sees it."
)
NO_TERMINAL = "There is no terminal: anything that prompts waits until the timeout."
ENVIRONMENT_WHOLE = "Its environment is pptmstr's, with nothing removed."
ENVIRONMENT_CHANGED = "Its environment is pptmstr's, with this changed before the shell starts:"
EXPLAINED = "Running outside the sandbox"


def explanations(text: str, where: Where) -> tuple[str, ...]:
    """
    The paragraphs behind the row's question mark: what each warning is short
    for, what the runner changes in the environment, every note the scan made
    of ``text`` in its own sentence, and ``commandtext.ADVISORY`` last.
    """
    if where.stripped_env:
        changes = "; ".join(one_line(change) for change in where.stripped_env)
        environment = f"{ENVIRONMENT_CHANGED} {changes}."
    else:
        environment = ENVIRONMENT_WHOLE
    flags = commandtext.breakdown(text, where.roots).flags
    return (
        RUNS_AS_YOU,
        RULES_DO_NOT_APPLY,
        PROFILE_WARNING,
        NO_TERMINAL,
        environment,
        *(flag.sentence for flag in flags),
        commandtext.ADVISORY,
    )


_ORDINAL_SUFFIX = {1: "st", 2: "nd", 3: "rd"}


def ordinal(n: int) -> str:
    """``n`` as 1st, 2nd, 3rd, 4th: the teens are what the last digit gets wrong."""
    suffix = "th" if n % 100 in (11, 12, 13) else _ORDINAL_SUFFIX.get(n % 10, "th")
    return f"{n}{suffix}"


def id_tail(command_id: CommandId) -> str:
    """
    The part of an id that is this command's alone: its last ``ID_HEX_CHARS``
    characters. What stands before them is the second it was asked in, which
    two commands asked back to back share.
    """
    return one_line(command_id)[-ID_HEX_CHARS:]


def is_new(command: Command, now: float) -> bool:
    """
    Whether ``command`` was asked within ``NEW_FOR_S`` of ``now``, on the
    clock ``Command.asked_at`` was read from. Read off the record: a command
    that has only come to stand somewhere else in the queue is not new.
    """
    return now - command.asked_at < NEW_FOR_S


def identity_line(command: Command, role: str, among: Among | None, now: float) -> Line:
    """
    Which command this is, in this application's words, for above its body:
    whose, which of its session's and of its asker's, the tail of its id, and
    how long ago it was asked -- things that differ between two commands whose
    text does not, or barely does.

    ``now`` is on the clock ``Command.asked_at`` was read from. ``among`` is
    None for a caller that counted nothing, and then no count is claimed.
    """
    if among is None:
        parts = [f"from {role}"]
    else:
        parts = [
            f"{ordinal(among.in_session)} command in this session,"
            f" {ordinal(among.ordinal)} from {role}"
        ]
    parts.append(id_tail(command.id))
    parts.append(f"asked {format_elapsed(max(0.0, now - command.asked_at))} ago")
    return Line(" \u00b7 ".join(parts), Tone.DIM)


def timeout_label(seconds: float) -> str:
    """A timeout in words: whole minutes or hours where it is one, else as elapsed."""
    whole = int(seconds)
    if whole == seconds and whole >= 3600 and whole % 3600 == 0:
        return f"{whole // 3600} hour{'' if whole == 3600 else 's'}"
    if whole == seconds and whole >= 60 and whole % 60 == 0:
        return f"{whole // 60} minute{'' if whole == 60 else 's'}"
    return format_elapsed(seconds)


ASKED_STATE = "asked: nothing has run. it waits for you to read it and run it or refuse it"
NO_REASON_GIVEN = "refused by you, with no reason given"
WITHDRAWN = "withdrawn by its asker before you acted: nothing ran"
LAPSED = "lapsed - asked before pptmstr last closed and never run"
END_UNKNOWN = "end unknown - was running when pptmstr last closed; whether it finished is not known"


def still_running(left: Survivor) -> str:
    """
    How a command left running unsupervised is said to stand. Not "end
    unknown": what is known of it is that it has not ended. With its shell
    there, the session is named by the shell's pid, which is its number; with
    the shell gone, what is counted is what carries the command's tag.
    """
    if not left.shell:
        return (
            "STILL RUNNING, UNSUPERVISED - no end was recorded, its shell is gone, and"
            f" {left.alive} process{'' if left.alive == 1 else 'es'} started with its"
            f" {COMMAND_TAG} {'is' if left.alive == 1 else 'are'} alive"
        )
    outside = f", and {len(left.escaped)} more outside it" if left.escaped else ""
    return (
        "STILL RUNNING, UNSUPERVISED - no end was recorded and its shell's session"
        f" {left.pid} still has {left.members} process{'' if left.members == 1 else 'es'}"
        f"{outside}"
    )


def released_line(alive: int) -> str:
    """How a command whose session the operator released is said to stand."""
    return (
        f"released by you, {alive} process{'' if alive == 1 else 'es'} still running then -"
        " not signalled, and no longer watched"
    )


STOP_ASKED = "cancel asked: waiting for every process in its shell's session to die"

# What a row that is not open says under the queue's summary line. Said the
# same in the queue and in DETAIL's copy, so it names the row and not "here".
CLOSED = "closed: click its row in NEEDS YOU, or press o on it, to open it"


def closed_lines(command: Command, role: str, among: Among | None, now: float) -> tuple[Line, ...]:
    """
    Everything a command's row says while it is not open: which command it is
    (``identity_line``), how it stands where that is more than asked
    (``row_state``) -- DETAIL has no summary line to say it -- and ``CLOSED``.
    Every word is this application's.
    """
    standing = () if command.state is CommandState.ASKED else (row_state(command, now),)
    return (identity_line(command, role, among, now), *standing, Line(CLOSED, Tone.DIM))


def row_state(command: Command, now: float) -> Line:
    """
    Where the command is, in this application's words.

    ``now`` is on the clock ``Run.started_at`` was read from, and is used only
    for how long a running command has run.

    For one that ended, everything its end records is in the one line: how,
    then ``End.remarks`` -- the processes it left that were ended with it, and
    that its own were not confirmed gone, which is the alarm colour whatever
    the outcome beside it.
    """
    end = command.end
    if end is None:
        run = command.run
        if run is None:
            return Line(ASKED_STATE)
        elapsed = format_elapsed(now - run.started_at)
        if command.stop_asked_at is not None:
            return Line(f"running for {elapsed} - {STOP_ASKED}", Tone.WARN)
        return Line(f"running for {elapsed} - cancel kills it", Tone.WARN)
    how = _how_ended(command, end)
    remarks = [one_line(remark) for remark in end.remarks]
    if not remarks:
        return how
    tone = Tone.ALARM if end.unconfirmed is not None or how.tone is Tone.ALARM else Tone.WARN
    return Line(" - ".join([how.text, *remarks]), tone)


def _how_ended(command: Command, end: End) -> Line:
    run = command.run
    took = f" after {format_elapsed(end.duration_s)}"
    outcome = end.outcome
    match outcome:
        case Exited(code=code):
            return Line(f"exit code {code}{took}", Tone.PLAIN if code == 0 else Tone.WARN)
        case TimedOut():
            within = "its timeout" if run is None else timeout_label(run.timeout_s)
            return Line(
                f"timed out - produced no exit within {within}; anything that prompts"
                " waits forever here",
                Tone.WARN,
            )
        case Cancelled(by=by):
            match by:
                case CancelledBy.OPERATOR:
                    return Line(f"cancelled by you{took}", Tone.WARN)
                case CancelledBy.SHUTDOWN:
                    return Line(
                        f"cancelled at shutdown{took}: pptmstr quit while it ran", Tone.WARN
                    )
                case _:
                    assert_never(by)
        case NotStarted(reason=reason):
            return Line(f"not started: {one_line(reason)}", Tone.WARN)
        case Refused(reason=reason):
            if not reason.strip():
                return Line(NO_REASON_GIVEN)
            return Line(f"refused by you: {one_line(reason)}")
        case Withdrawn():
            return Line(WITHDRAWN, Tone.DIM)
        case Lapsed():
            return Line(LAPSED, Tone.DIM)
        case EndUnknown(released=Released(alive=alive)):
            return Line(released_line(alive), Tone.WARN)
        case EndUnknown(survivor=left):
            if left is None:
                return Line(END_UNKNOWN, Tone.WARN)
            return Line(still_running(left), Tone.ALARM)
        case _:
            assert_never(outcome)


def delivery_line(command: Command, role: str, *, restored: bool = False) -> Line | None:
    """
    Whether the asker has been handed how it ended. None while it is open, and
    for a withdrawal, which its asker made.

    Unread is the warning colour: nothing wakes an asker for a result.

    ``restored`` is whether the record came from a file and not from an agent
    in this window. No file holds whether its asker read it, so "not yet read"
    is then true of this window only, and the line says that instead.
    """
    if command.end is None or isinstance(command.end.outcome, Withdrawn):
        return None
    if command.first_delivered_at is None:
        if restored:
            return Line(
                f"from before pptmstr restarted: not read by {role} in this window, and its"
                " files do not say whether it was read before",
                Tone.DIM,
            )
        return Line(f"not yet read by {role}", Tone.WARN)
    return Line(f"read by {role}", Tone.DIM)


def refusable(reason: str) -> bool:
    """Whether a refusal may be sent with this reason. An empty one may."""
    return len(reason) <= MAX_REASON_CHARS


def reason_too_long(reason: str) -> str:
    return (
        f"this is {len(reason) - MAX_REASON_CHARS:,} characters over the {MAX_REASON_CHARS:,}"
        " a reason may be, and is not sent until it is shorter"
    )


def output_shown(tail: str) -> tuple[str, int]:
    """The end of ``tail`` that is drawn, and how many characters before it are not."""
    if len(tail) <= MAX_TAIL_CHARS:
        return tail, 0
    return tail[-MAX_TAIL_CHARS:], len(tail) - MAX_TAIL_CHARS


def output_heading(output_bytes: int, tail: str) -> str:
    if not output_bytes and not tail:
        return "it wrote nothing"
    if not tail:
        return f"it wrote {output_bytes:,} bytes, none of them held here"
    return f"the end of the {output_bytes:,} bytes it wrote:"


def asked_heading(role: str) -> str:
    return f"the command {role} asks you to run - all of it, and exactly:"


class Stage(enum.Enum):
    """Which tense a command's headings are in: it would run, is running, or ran."""

    WOULD_RUN = "would run"
    RUNNING = "running"
    RAN = "ran"
    # Approved as edited and never started: ``NotStarted`` has a run and no process.
    NOT_STARTED = "not started"


def stage_of(command: Command) -> Stage:
    """
    The tense of ``command``'s headings, read off the record. One left running
    unsupervised has an end on record and is running all the same.
    """
    if command.run is None:
        return Stage.WOULD_RUN
    if command.end is None or command.unsupervised is not None:
        return Stage.RUNNING
    return Stage.RAN if ran(command.end.outcome) else Stage.NOT_STARTED


def run_heading(command: Command) -> str:
    """
    What stands over the string of a run with an end on record, where it is
    the string that was asked: in the tense ``stage_of`` reads off it.
    """
    stage = stage_of(command)
    if stage is Stage.RUNNING:
        return RUNNING_HEADING
    if stage is Stage.NOT_STARTED:
        return NOT_STARTED_HEADING
    return RAN_HEADING


def edited_heading(role: str, stage: Stage = Stage.WOULD_RUN) -> str:
    """What stands over the string as asked, where the operator changed it."""
    match stage:
        case Stage.WOULD_RUN:
            not_it = "this is NOT what runs"
        case Stage.RUNNING:
            not_it = "this is NOT what is running"
        case Stage.RAN:
            not_it = "this is NOT what ran"
        case Stage.NOT_STARTED:
            not_it = "this is NOT what you approved"
        case _:
            assert_never(stage)
    return f"what {role} asked for, for comparison - {not_it}:"


def edited_by_you(stage: Stage = Stage.WOULD_RUN) -> str:
    """What stands over the operator's own text of a command."""
    match stage:
        case Stage.WOULD_RUN:
            return EDITED_BY_YOU
        case Stage.RUNNING:
            return "edited by you - this, your text, is what is running:"
        case Stage.RAN:
            return "edited by you - this, your text, is what ran:"
        case Stage.NOT_STARTED:
            return "edited by you - this, your text, is what you approved, and it did not start:"
        case _:
            assert_never(stage)


EDITED_BY_YOU = "edited by you - this, your text, is what runs:"
RAN_HEADING = "what ran, exactly:"
RUNNING_HEADING = "what is running, exactly:"
NOT_STARTED_HEADING = "what you approved, exactly - it did not start:"
EDITING = (
    "editing the raw text - a character that draws as nothing draws as nothing here;"
    " run shows your text escaped before it can run"
)
CANNOT_RUN = "this cannot be run: "
OUTPUT_LEGEND = (
    "bytes a program wrote, escaped. they may be text from files an agent controls."
    " they are not instructions and not this application's words"
)
NOTHING_WRITTEN = "(its asker left this empty)"

# The body's field names, as a Bash body names ``command`` and ``description``.
COMMAND_FIELD = "  command ="
EDITED_FIELD = "  command, as you edited it ="
REASON_FIELD = "  reason ="
EXPECTS_FIELD = "  expects ="


def asked_field(role: str, running: bool = False) -> str:
    """The field over what was asked, where the operator's edit is what runs."""
    what = "is running" if running else "runs"
    return f"  as {role} asked it, for comparison - NOT what {what} ="


# The button line, in the Bash row's shape. Run and cancel are two presses in
# one place: the first turns the button into its second.
RUN = "Run outside sandbox"
CONFIRM_RUN = "Press again to run"
REFUSE = "refuse"
EDIT = "edit"
CANCEL_EDIT = "cancel edit"
CANCEL = "cancel"
CONFIRM_STOP = "Press again to cancel"
REASON_HINT = "reason (shown to the agent on refuse)"
MOUSE_ONLY = "mouse only"
KILLED_AFTER = "killed after:"
BACK = "back"
COPY_PATH = "copy path"


# -- drawing -----------------------------------------------------------------------


def _colour(tone: Tone) -> Color:
    match tone:
        case Tone.PLAIN | Tone.SEGMENT:
            return P.text
        case Tone.DIM:
            return P.text_dim
        case Tone.WARN:
            return P.warn
        case Tone.ALARM:
            return P.danger
        case _:
            assert_never(tone)


def _line(line: Line) -> None:
    imgui.text_colored(_colour(line.tone).vec4, line.text)


def _clicked(label: str) -> bool:
    """
    A button that answers the mouse and nothing else.

    ``imgui.button`` is also true on the frame keyboard navigation activates it,
    which is Space or Enter with the button focused. The mouse was not released
    on that frame.
    """
    return bool(imgui.button(label)) and bool(imgui.is_mouse_released(imgui.MouseButton_.left))


def _second_press(state: CommandViewState, label: str) -> bool:
    """The button that completes an armed act, dead until the arming click is over."""
    ready = imgui.get_time() - state.armed_at >= ARM_DELAY_S
    if not ready:
        imgui.begin_disabled()
    pressed = _clicked(label)
    if not ready:
        imgui.end_disabled()
    return pressed and ready


def _first_press(state: CommandViewState, label: str) -> bool:
    """
    A button that begins an act, dead until the click that opened the row is
    over: ``ARM_DELAY_S`` from ``CommandViewState.opened_at``.
    """
    ready = imgui.get_time() - state.opened_at >= ARM_DELAY_S
    if not ready:
        imgui.begin_disabled()
    pressed = _clicked(label)
    if not ready:
        imgui.end_disabled()
    return pressed and ready


def _arm(state: CommandViewState, pending: Pending) -> None:
    state.pending = pending
    state.armed_at = imgui.get_time()


def _columns(reserved: int) -> int:
    """How many characters fit beside a gutter ``reserved`` characters wide."""
    # The widest of these and not one of them: the faces loaded here are
    # monospaced, and a row cut for a narrower glyph would run past the edge
    # in one that is not.
    cell = max(imgui.calc_text_size(glyph).x for glyph in ("M", "W", "@"))
    fit = int(imgui.get_content_region_avail().x // max(cell, 1.0))
    return max(MIN_COLUMNS, fit - reserved)


def _draw_rows(rows: Sequence[Row], colour: Color) -> None:
    gutter = max(len(row.gutter) for row in rows)
    for row in rows:
        imgui.text_colored(P.text_dim.vec4, row.gutter.rjust(gutter))
        imgui.same_line()
        # Filled behind exactly the row's characters, so a blank at either end
        # of a row has an extent and the gutter is visibly outside it.
        low = imgui.get_cursor_screen_pos()
        size = imgui.calc_text_size(row.text)
        imgui.get_window_draw_list().add_rect_filled(
            low, imgui.ImVec2(low.x + size.x, low.y + size.y), P.panel_alt.u32
        )
        imgui.text_colored(colour.vec4, row.text)


def command_block(text: str, *, key: str = "command", dim: bool = False) -> None:
    """
    The one place a command string is drawn: escaped, whole, in a bordered box
    as tall as it needs to be.

    The box is a child window, so the caller's text wrapping does not reach into
    it and the only cuts are ``wrap_shown``'s.
    """
    flags = int(imgui.ChildFlags_.borders) | int(imgui.ChildFlags_.auto_resize_y)
    if imgui.begin_child(f"##{key}", imgui.ImVec2(-8.0, 0.0), flags):
        lines = len(block_lines(text))
        reserved = max(len(str(lines)), len(CONTINUES)) + 2
        _draw_rows(block_rows(text, _columns(reserved)), P.text_dim if dim else P.text_strong)
    imgui.end_child()


def process_output(text: str) -> None:
    """
    The one place a program's output is drawn: escaped by ``visible``, dimmed,
    behind two bars. The asker's bar is one line and the command's box has a
    numbered gutter; this is neither.
    """
    gutter = imgui.get_font_size()
    top = imgui.get_cursor_screen_pos()
    imgui.indent(gutter)
    imgui.begin_group()
    for row in _rows(commandtext.visible(text), _columns(2), lambda index: ""):
        imgui.text_colored(P.text_dim.vec4, row.text)
    imgui.end_group()
    imgui.unindent(gutter)
    bottom = imgui.get_item_rect_max().y
    draw_list = imgui.get_window_draw_list()
    for offset in (0.25, 0.6):
        x = top.x + gutter * offset
        draw_list.add_line(imgui.ImVec2(x, top.y), imgui.ImVec2(x, bottom), P.text_dim.u32, 1.5)


def _hidden(text: str) -> None:
    sentence = hidden_sentence(text)
    if sentence:
        imgui.text_colored(P.danger.vec4, sentence)


def _command(command: Command, text: str, role: str, heading: str) -> None:
    """
    ``text`` as the command in question, under ``heading``. Where it is not what
    was asked, what was asked is above it, dimmed and labelled as not running,
    in the tense the record is in (``stage_of``).
    """
    if text != command.asked:
        stage = stage_of(command)
        imgui.text_colored(P.text_dim.vec4, edited_heading(role, stage))
        command_block(command.asked, key="asked", dim=True)
        imgui.text_colored(P.warn.vec4, edited_by_you(stage))
    else:
        imgui.text_colored(P.text_strong.vec4, heading)
    command_block(text)
    _hidden(text)


def _authored(text: str) -> None:
    if not text.strip():
        imgui.text_disabled(NOTHING_WRITTEN)
        return
    shown, dropped = board_pane.clip(text, _ASKER_CHARS)
    board_pane.author(shown)
    if dropped:
        imgui.text_colored(P.warn.vec4, f"... {dropped:,} more characters not shown")


def _asker_texts(command: Command, role: str) -> None:
    imgui.spacing()
    imgui.text_colored(P.text_dim.vec4, f"why {role} asks, in its words:")
    _authored(command.reason)
    imgui.text_colored(P.text_dim.vec4, f"what {role} expects it to change, in its words:")
    _authored(command.expects)


def asker_value(text: str) -> str:
    """
    One of the asker's texts as the body's value: whole up to ``_ASKER_CHARS``
    and marked where cut, drawable whatever it holds (``author_text``), and
    ``''`` where it is blank, as a Bash body shows an empty argument.
    """
    if not text.strip():
        return repr(text)
    shown, dropped = board_pane.clip(text, _ASKER_CHARS)
    more = f" ... {dropped:,} more characters not shown" if dropped else ""
    return author_text(shown) + more


def _body(command: Command, text: str, role: str, *, running: bool = False) -> None:
    """
    The body of a run, as a Bash approval's body is drawn: a bordered box with
    each argument under its name. ``text`` is the string that would run or is
    running, in its block; where it is not what was asked, what was asked is
    under it, dimmed and named as not what runs. Then the asker's reason and
    expectation, each the value of its line and nothing else.
    """
    flags = int(imgui.ChildFlags_.borders) | int(imgui.ChildFlags_.auto_resize_y)
    if imgui.begin_child("##body", imgui.ImVec2(-8.0, 0.0), flags):
        # A child is a window of its own, and starts with no wrap.
        imgui.push_text_wrap_pos(0.0)
        edited = text != command.asked
        imgui.text_colored(P.text_dim.vec4, EDITED_FIELD if edited else COMMAND_FIELD)
        command_block(text)
        if edited:
            imgui.text_colored(P.text_dim.vec4, asked_field(role, running))
            command_block(command.asked, key="asked", dim=True)
        imgui.text_colored(P.text.vec4, f"{REASON_FIELD} {asker_value(command.reason)}")
        imgui.text_colored(P.text.vec4, f"{EXPECTS_FIELD} {asker_value(command.expects)}")
        imgui.pop_text_wrap_pos()
    imgui.end_child()


def _warnings(lines: Sequence[Line], key: str, paragraphs: Sequence[str]) -> None:
    """
    The warnings, the first with the question mark that has them in full: a link
    on the line's own baseline, so the line is set as text.
    """
    for index, line in enumerate(lines):
        _line(line)
        if index == 0:
            help_mark(key, EXPLAINED, paragraphs)


def draw_row(
    state: CommandViewState,
    command: Command,
    *,
    role: str,
    where: Where,
    actions: Actions,
    now: float,
    wrap: bool = True,
    log_path: str | None = None,
    among: Among | None = None,
    restored: bool = False,
    presses: bool = True,
) -> None:
    """
    The row for a command, whatever state it is in: ``CLOSED`` under its
    identity and nothing else unless ``state`` says the operator opened it,
    and then all of it.

    The state is read off ``command`` on every call, so a command withdrawn or
    started since the last frame is drawn as what it now is and offers none of
    the presses it offered before. ``role`` is what to call the asker.

    ``among`` is where the command stands among its session's and its asker's
    (``identity_line``), and ``restored`` whether its record came from a file
    (``delivery_line``). ``presses`` False draws the body and no button and no
    box: DETAIL's copy, which is read and not acted from.
    """
    role = one_line(role)
    state.lapse(int(imgui.get_frame_count()))
    state.expire(float(imgui.get_time()))
    imgui.push_id(f"command:{command.id}")
    imgui.push_text_wrap_pos(0.0)
    if not state.is_open(command.id):
        for line in closed_lines(command, role, among, now):
            _line(line)
        imgui.pop_text_wrap_pos()
        imgui.pop_id()
        return
    match command.state:
        case CommandState.ASKED:
            _asked(state, command, role, where, actions, wrap, among, now, presses)
        case CommandState.RUNNING:
            _running(state, command, role, where, actions, now, among, presses)
        case CommandState.ENDED:
            _ended(command, role, now, log_path, restored)
        case _:
            assert_never(command.state)
    imgui.pop_text_wrap_pos()
    imgui.pop_id()


def _asked(
    state: CommandViewState,
    command: Command,
    role: str,
    where: Where,
    actions: Actions,
    wrap: bool,
    among: Among | None,
    now: float,
    presses: bool,
) -> None:
    frozen = state.frozen_run(command.id)
    text = state.candidate(command) if frozen is None else frozen.text
    _line(identity_line(command, role, among, now))
    _warnings(
        warnings(command, text, where, role, among),
        f"glass:{command.id}",
        explanations(text, where),
    )
    if presses and state.editing == command.id:
        _edit_box(state, command, wrap)
        # What the box holds now, which is what run would freeze this frame.
        text = state.candidate(command)
    else:
        _body(command, text, role)
    if presses:
        imgui.spacing()
        _choices(state, command, where, actions, text)


def _choices(
    state: CommandViewState, command: Command, where: Where, actions: Actions, text: str
) -> None:
    """
    The Bash row's button line: the reason box, run, refuse, edit. Then the
    timeout, and why run or refuse is not offered where it is not.
    """
    frozen = state.frozen_run(command.id)
    reason = state.reasons.get(command.id, "")
    imgui.set_next_item_width(_REASON_WIDTH)
    with hint_colour(imgui, dialog=False):
        _, typed = imgui.input_text_with_hint("##reason", REASON_HINT, reason)
    # Escape in a text box hands back what it held when the caret entered it;
    # that frame's value is not the operator's.
    if typed != reason and not imgui.is_key_pressed(imgui.Key.escape):
        reason = state.reasons[command.id] = typed
    refusal = refusal_of_edit(text)
    runnable = not refusal and where.shell is not None
    imgui.same_line()
    if frozen is not None:
        if _second_press(state, CONFIRM_RUN):
            actions.on_run(frozen.command_id, frozen.text, frozen.timeout_s)
            state.pending = None
    else:
        if not runnable:
            imgui.begin_disabled()
        # ``runnable`` and not the click alone: the rule does not rest on what a
        # greyed button reports.
        if _first_press(state, RUN) and runnable:
            _arm(state, freeze(command.id, text, state.timeout_s(command.id)))
            state.editing = None
        if not runnable:
            imgui.end_disabled()
    imgui.same_line()
    sendable = refusable(reason)
    if not sendable:
        imgui.begin_disabled()
    if _first_press(state, REFUSE) and sendable:
        actions.on_refuse(command.id, reason)
        state.pending = None
    if not sendable:
        imgui.end_disabled()
    imgui.same_line()
    if state.editing == command.id:
        if _clicked(CANCEL_EDIT):
            state.edits.pop(command.id, None)
            state.editing = None
    elif _clicked(EDIT):
        state.edits.setdefault(command.id, command.asked)
        state.editing = command.id
        state.pending = None
    imgui.same_line()
    imgui.text_colored(P.text_dim.vec4, MOUSE_ONLY)
    _timeouts(state, command, frozen)
    if refusal:
        imgui.text_colored(P.warn.vec4, CANNOT_RUN + refusal)
    if not sendable:
        imgui.text_colored(P.warn.vec4, reason_too_long(reason))


def _timeouts(state: CommandViewState, command: Command, frozen: FrozenRun | None) -> None:
    """
    The timeout, one line. Fixed while a run is armed: the run frozen is the one
    on screen, its timeout included.
    """
    picked = state.timeout_s(command.id) if frozen is None else frozen.timeout_s
    imgui.text_colored(P.text_dim.vec4, KILLED_AFTER)
    if frozen is not None:
        imgui.begin_disabled()
    for seconds in TIMEOUT_CHOICES_S:
        imgui.same_line()
        chosen = imgui.radio_button(timeout_label(seconds), picked == seconds)
        if chosen and frozen is None and imgui.is_mouse_released(imgui.MouseButton_.left):
            state.timeouts[command.id] = seconds
            picked = seconds
    if frozen is not None:
        imgui.end_disabled()


def _edit_box(state: CommandViewState, command: Command, wrap: bool) -> None:
    """In the body's place, as a Bash row's editor is: the raw text, to change."""
    draft = state.edits.get(command.id, command.asked)
    imgui.text_colored(P.warn.vec4, EDITING)
    _, typed = multiline_input(
        "##edit",
        draft,
        imgui.ImVec2(-8.0, _EDIT_BOX_HEIGHT),
        wrap=wrap,
        flags=int(imgui.InputTextFlags_.allow_tab_input),
    )
    if not imgui.is_key_pressed(imgui.Key.escape):
        state.edits[command.id] = typed


def _running(
    state: CommandViewState,
    command: Command,
    role: str,
    where: Where,
    actions: Actions,
    now: float,
    among: Among | None,
    presses: bool,
) -> None:
    run = command.run
    if run is None:
        return
    _line(row_state(command, now))
    _line(identity_line(command, role, among, now))
    ran_in = dataclasses.replace(where, shell=run.shell, cwd=run.cwd)
    _warnings(
        warnings(command, run.text, ran_in, role, timeout_s=run.timeout_s),
        f"glass:{command.id}",
        explanations(run.text, ran_in),
    )
    _body(command, run.text, role, running=True)
    if not presses or command.stop_asked_at is not None:
        return
    imgui.spacing()
    if isinstance(state.armed(command.id), Stopping):
        if _second_press(state, CONFIRM_STOP):
            actions.on_stop(command.id)
            state.pending = None
    elif _first_press(state, CANCEL):
        _arm(state, Stopping(command.id))
    imgui.same_line()
    imgui.text_colored(P.text_dim.vec4, MOUSE_ONLY)


def draw_ended(
    command: Command,
    *,
    role: str,
    now: float,
    log_path: str | None = None,
    restored: bool = False,
) -> None:
    """
    DETAIL's body for a command that has ended: how, what ran, what the asker
    wrote, and the end of what the process wrote. Draws nothing for one that
    has not ended, and no control but the copy of the log's path.
    """
    if command.end is None:
        return
    imgui.push_id(f"command:{command.id}")
    _ended(command, one_line(role), now, log_path, restored)
    imgui.pop_id()


def _ended(
    command: Command, role: str, now: float, log_path: str | None, restored: bool = False
) -> None:
    end = command.end
    if end is None:
        return
    _line(row_state(command, now))
    delivery = delivery_line(command, role, restored=restored)
    if delivery is not None:
        _line(delivery)
    if command.run is not None:
        _command(command, command.run.text, role, run_heading(command))
    else:
        imgui.text_colored(P.text_dim.vec4, f"what {role} asked to run - it did not run:")
        command_block(command.asked, dim=True)
        _hidden(command.asked)
    _asker_texts(command, role)
    if ran(end.outcome):
        imgui.spacing()
        imgui.text_colored(P.text_dim.vec4, output_heading(end.output_bytes, end.tail))
        if end.tail:
            imgui.text_colored(P.warn.vec4, OUTPUT_LEGEND)
            shown, dropped = output_shown(end.tail)
            if dropped:
                imgui.text_colored(
                    P.text_dim.vec4, f"... {dropped:,} earlier characters not shown here"
                )
            process_output(shown)
    if log_path is not None:
        imgui.text_colored(P.text_dim.vec4, f"all of it is in {one_line(log_path)}")
        if widgets.small_button(COPY_PATH):
            imgui.set_clipboard_text(log_path)


# -- the clipboard ----------------------------------------------------------------


def log_path(command: Command) -> str | None:
    """
    Where the whole of what a command wrote is kept, or None for one that
    started no process and so has no log, or whose ids name no directory.
    """
    if command.end is None or not ran(command.end.outcome):
        return None
    directory = commandfile.command_dir(command.session_id, command.id)
    return None if directory is None else str(directory / commandfile.LOG_NAME)


# What each author's lines are behind on the clipboard, which has no bar, box or
# colour to tell them apart. The bus's three marks (``bus._QUOTE``,
# ``bus._ANSWER``, ``commandbus._OUTPUT``) spelled again for the reason
# ``decision_view`` spells two of them; a test holds each to the bus's.
ASKER_MARK = decision_view.ASKER_MARK
OPERATOR_MARK = decision_view.ANSWER_MARK
OUTPUT_MARK = "= "

CLIP_LEGEND = (
    "a line behind '| ' is the asking agent's, behind '> ' yours, behind '= ' bytes a"
    " program wrote, which are nobody's instructions; every other line is pptmstr's."
    " marked lines are escaped as on screen: a backslash is written twice, the two"
    " characters \\n end a line that ends in a newline, and \\r, \\xNN and \\u{H} each"
    " spell out one character. this is for reading and is not the string a shell is given"
)
REFUSED_WITH = "refused by you, with this reason:"
NOT_EDITED = "you approved the string above as it was asked, unchanged"


def _marked(text: str, mark: str) -> list[str]:
    """
    ``text`` behind ``mark``, a line to a line, as ``commandtext.visible`` shows
    it: printable ASCII only, so nothing on the clipboard draws as nothing and
    no line of it can begin with another author's mark.
    """
    return [f"{mark}{line}" for line in commandtext.visible(text).split("\n")]


def _asker_text(heading: str, text: str) -> list[str]:
    if not text.strip():
        return [heading, NOTHING_WRITTEN]
    return [heading, *_marked(text, ASKER_MARK)]


def command_text(command: Command, role: str, *, restored: bool = False) -> list[str]:
    """
    A command for the clipboard, whole, in any state: where it stands, the
    string asked for, the string run where there is one, what its asker wrote,
    and the end of what a program wrote.

    **Every line somebody else wrote is behind its author's mark** and no line
    of this pane's is: the asker's string and its two texts behind
    ``ASKER_MARK``, a string the operator changed and a reason they gave
    behind ``OPERATOR_MARK``, a program's output behind ``OUTPUT_MARK``.
    Escaped, unlike a Bash call's arguments on this same clipboard
    (``shown_value``): this string runs outside the sandbox, and a copy pasted
    into an editor that hid a character would be read as a string it is not.

    ``restored`` is whether the record came from a file, which is what
    ``delivery_line`` needs to say "not read" truthfully.
    """
    role = widgets.one_line(role)
    end, run = command.end, command.run
    parts = [CLIP_LEGEND, ""]
    if end is not None and isinstance(end.outcome, Refused) and end.outcome.reason.strip():
        parts += [REFUSED_WITH, *_marked(end.outcome.reason, OPERATOR_MARK)]
    elif end is None and run is not None:
        # Without how long: a duration on the clipboard is stale when pasted.
        stopping = f" - {STOP_ASKED}" if command.stop_asked_at is not None else ""
        parts.append(f"running{stopping}")
    else:
        # Asked or ended, and neither wording reads a clock.
        parts.append(row_state(command, 0.0).text)
    delivery = delivery_line(command, role, restored=restored)
    if delivery is not None:
        parts.append(delivery.text)
    parts += ["", asked_heading(role), *_marked(command.asked, ASKER_MARK)]
    if run is not None:
        if command.edited:
            said = edited_by_you(stage_of(command))
            parts += ["", said, *_marked(run.text, OPERATOR_MARK)]
        else:
            parts += ["", NOT_EDITED]
    parts += ["", *_asker_text(f"why {role} asks, in its words:", command.reason)]
    parts += _asker_text(f"what {role} expects it to change, in its words:", command.expects)
    if end is not None and ran(end.outcome):
        parts += ["", output_heading(end.output_bytes, end.tail)]
        if end.tail:
            parts += _marked(end.tail, OUTPUT_MARK)
        where = log_path(command)
        if where is not None:
            parts.append(f"all of it is in {widgets.one_line(where)}")
    return parts
