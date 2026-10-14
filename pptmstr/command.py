"""
A break-glass command as a value: what a contained agent asked the operator to
run, what ran, and how it ended.

Nothing here performs IO, reads a clock, or draws. The record is held to one rule:
it cannot say something its parts do not. ``Command.state`` and ``Command.edited``
are read off ``run`` and ``end`` rather than stored beside them, and the
combinations of those two that would describe something that cannot have happened
are refused where the record is built, with ``ValueError``. ``model.py`` leaves
its invariants to the reducer arm that writes each field; these records are also
built from files (``sessions/<id>/commands/<id>/``), which no arm stands in front
of, so the check is in the constructor.

The decisions this builds are in
``planning/2026-10-13-a-break-glass-command-the-operator-runs.md``.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never

from .commandtext import visible
from .shellscan import _split_segments

if TYPE_CHECKING:
    # Annotations only. ``model.py`` is the module that will hold these records
    # on the ``Snapshot``, so an import at runtime would be half of a cycle.
    from .model import NodeId

CommandId = str

# -- bounds -----------------------------------------------------------------------

# The command string, in characters. The operator reads it whole before it runs,
# and one that does not fit a screen is not read.
MAX_COMMAND_CHARS = 2_000

# The command string, in lines. A break-glass command is a line or a few; the
# block draws every line and the breakdown draws each again, so this is what
# keeps both on one screen and a line that differs from its neighbours in
# sight of them. Eight holds a ``cd`` and a chain cut at its operators, or a
# short here-document. More than that is a script, and a script is a file the
# operator can open.
MAX_COMMAND_LINES = 8

# The command string as its block draws it, in characters of
# ``commandtext.visible``. The block's height follows this and not the count
# above: a character outside printable ASCII is drawn as up to ten. It is what
# a command of ``MAX_COMMAND_CHARS`` characters that each draw as themselves
# comes to -- a newline is drawn as two, and there are at most
# ``MAX_COMMAND_LINES`` of those -- so no such command is refused by it; about
# 250 characters outside ASCII, or a thousand backslashes, are.
MAX_COMMAND_SHOWN_CHARS = MAX_COMMAND_CHARS + 2 * MAX_COMMAND_LINES

# Spaces in a row, anywhere but at the end of a line (where each is drawn as
# ``\x20``). A space is the one character the block draws as nothing, so a run
# of them as wide as the block is a row with nothing in it. Sixteen is four
# levels of a four-space indent; it is under ``command_view.MIN_COLUMNS``, so
# no row of the block is blank at any width it is cut to.
MAX_COMMAND_SPACE_RUN = 16

# The pieces ``shellscan`` cuts the command into at ``;``, ``|``, ``&&``, ``||``
# and a newline. The breakdown draws a row or more for each, under the block.
MAX_COMMAND_SEGMENTS = 12

# The asker's reason, and what it expects the command to change. The lines are
# counted as ``str.splitlines`` does: each is drawn on a row of its own between
# the command and the presses that act on it.
MAX_REASON_CHARS = 1_000
MAX_EXPECTS_CHARS = 1_000
MAX_REASON_LINES = 8
MAX_EXPECTS_LINES = 8

# The end of the output handed back to the asker. The whole of it is in a file.
MAX_TAIL_CHARS = 4_000
MAX_TAIL_LINES = 60

# Open requests one agent may hold, and the sub-agents of one session between
# them. As ``MAX_OPEN_DECISIONS_PER_ASKER`` and ``MAX_OPEN_SUBAGENT_DECISIONS``,
# and smaller: each of these is a row asking for the operator's whole machine.
MAX_OPEN_PER_ASKER = 3
MAX_OPEN_SUBAGENTS = 8

# What the operator may pick at the row, in seconds: 2m, 10m, 30m, 2h.
TIMEOUT_CHOICES_S = (120, 600, 1_800, 7_200)
DEFAULT_TIMEOUT_S = 600

# -- ids --------------------------------------------------------------------------

# ``cmd-<utc compact>-<hex>``. The handler mints the id; the two constants are
# here so that it and ``is_valid_id`` cannot disagree about the form.
ID_TIME_FORMAT = "%Y%m%dT%H%M%SZ"
ID_HEX_CHARS = 12

# ``[0-9]`` and not ``\d``, which in a ``str`` pattern matches every Unicode
# decimal digit.
_ID = re.compile(rf"cmd-[0-9]{{8}}T[0-9]{{6}}Z-[0-9a-f]{{{ID_HEX_CHARS}}}")


def is_valid_id(s: object) -> bool:
    """
    Whether ``s`` has the form of a command id.

    An id names a directory, so this is what stands between a string from a file
    or an agent and a path: the form admits no separator, no dot, and no
    character outside ASCII digits, lower-case hex and the fixed letters. The
    whole string must match -- a trailing newline does not.
    """
    return isinstance(s, str) and _ID.fullmatch(s) is not None


# -- what may be asked ------------------------------------------------------------


class RequestRefusal(enum.Enum):
    """
    Why a command string, or the request around it, is one that is never put in
    front of the operator -- and never run after the operator's edit either.

    ``LEADS_WITH_OPTION`` is the one that is not about size or encoding. The
    command reaches the shell as the argument after ``-c``, and a shell reads an
    argument there that begins with ``-`` or ``+`` as more options and not as
    the command: ``bash -l -c '-x'`` fails with "-c: option requires an
    argument", and given a further argument bash would run *that* with tracing
    on. So the string the operator approved would not be the one that ran. Only
    the first character counts, because the shell is handed the string as it
    is: ``' -x'`` is a command named ``-x``, which bash looks up and does not
    find. Measured against ``/bin/bash`` 5.2.15 and ``dash``.

    ``NUL`` and ``LONE_SURROGATE`` are strings that cannot be an argument to a
    process at all. They are apart because the asker fixes them differently.

    ``TOO_MANY_LINES`` and ``BLANK_LINE`` are about what the operator can take
    in: a command padded with empty lines puts the one line that matters a
    screen away from the top of its block, with nothing between to read. A
    line is blank when ``str.strip`` leaves nothing of it. One newline at the
    very end ends the last line and begins none, so it is neither a line nor
    a blank one; a second one is.

    ``SHOWN_TOO_LONG``, ``SPACE_RUN`` and ``TOO_MANY_SEGMENTS`` are about the
    same thing measured where it is drawn: the block and the breakdown are as
    tall as the command's escaped form is long, as its runs of spaces are wide
    and as its segments are many, whatever its length in characters is. So are
    ``REASON_TOO_MANY_LINES`` and ``EXPECTS_TOO_MANY_LINES``, of the two texts
    drawn under it.

    The four about ``reason`` and ``expects`` that are not lengths are there
    because those texts are drawn and stored too: neither character can be
    encoded, so a record holding one could not be written to its file.

    The value is the stable name; ``sentence`` is what the bus says.
    """

    EMPTY = "empty"
    TOO_LONG = "too_long"
    NUL = "nul"
    LONE_SURROGATE = "lone_surrogate"
    LEADS_WITH_OPTION = "leads_with_option"
    TOO_MANY_LINES = "too_many_lines"
    BLANK_LINE = "blank_line"
    SHOWN_TOO_LONG = "shown_too_long"
    SPACE_RUN = "space_run"
    TOO_MANY_SEGMENTS = "too_many_segments"
    REASON_TOO_LONG = "reason_too_long"
    EXPECTS_TOO_LONG = "expects_too_long"
    REASON_TOO_MANY_LINES = "reason_too_many_lines"
    EXPECTS_TOO_MANY_LINES = "expects_too_many_lines"
    REASON_NUL = "reason_nul"
    REASON_LONE_SURROGATE = "reason_lone_surrogate"
    EXPECTS_NUL = "expects_nul"
    EXPECTS_LONE_SURROGATE = "expects_lone_surrogate"

    @property
    def sentence(self) -> str:
        return _SENTENCES[self]


_SENTENCES = {
    RequestRefusal.EMPTY: "The command is empty. Send the exact command line to run.",
    RequestRefusal.TOO_LONG: (
        f"The command is longer than {MAX_COMMAND_CHARS:,} characters, and the operator "
        "has to read all of it before it runs. Put the steps in a script in the "
        "session's directory and ask to run that."
    ),
    RequestRefusal.NUL: (
        "The command contains a NUL character (U+0000), which no program can be "
        "given in an argument."
    ),
    RequestRefusal.LONE_SURROGATE: (
        "The command contains a lone surrogate (U+D800 to U+DFFF), which is not a "
        "character and cannot be encoded for the shell."
    ),
    RequestRefusal.LEADS_WITH_OPTION: (
        "The command begins with '-' or '+'. A shell reads a command string that "
        "begins so as options to itself and not as the command, so what ran would "
        "not be what was approved. Begin with the program's name."
    ),
    RequestRefusal.TOO_MANY_LINES: (
        f"The command has more than {MAX_COMMAND_LINES} lines, and the operator has to "
        "take in all of it before it runs. Put the steps in a script in the session's "
        "directory and ask to run that."
    ),
    RequestRefusal.BLANK_LINE: (
        "The command has a line that is empty or only whitespace. A command is shown "
        "to the operator exactly as it will run, and blank lines push part of it out "
        "of sight. Send it with none. One newline at the very end is not a blank "
        "line; two are."
    ),
    RequestRefusal.SHOWN_TOO_LONG: (
        f"The command is longer than {MAX_COMMAND_SHOWN_CHARS:,} characters as the "
        "operator is shown it: every character outside printable ASCII, every tab and "
        "every space ending a line is spelled out there as an escape of up to ten "
        "characters, and a backslash as two. Use fewer of those, or put the text in a "
        "file in the session's directory and ask to run a command that reads it."
    ),
    RequestRefusal.SPACE_RUN: (
        f"The command has more than {MAX_COMMAND_SPACE_RUN} spaces in a row. They are "
        "drawn as nothing, and a run of them pushes what follows out of the "
        "operator's sight. Send it with shorter runs."
    ),
    RequestRefusal.TOO_MANY_SEGMENTS: (
        f"The command is more than {MAX_COMMAND_SEGMENTS} commands joined by ;, |, && "
        "or || or put on lines of their own, and the operator has to take in each one "
        "before it runs. Put the steps in a script in the session's directory and ask "
        "to run that."
    ),
    RequestRefusal.REASON_TOO_LONG: (
        f"The reason is longer than {MAX_REASON_CHARS:,} characters. Say it shorter, "
        "or name a file that holds the rest."
    ),
    RequestRefusal.EXPECTS_TOO_LONG: (
        f"What the command is expected to change is longer than {MAX_EXPECTS_CHARS:,} "
        "characters. Say it shorter, or name a file that holds the rest."
    ),
    RequestRefusal.REASON_TOO_MANY_LINES: (
        f"The reason has more than {MAX_REASON_LINES} lines. Say it in fewer, or name a "
        "file that holds the rest."
    ),
    RequestRefusal.EXPECTS_TOO_MANY_LINES: (
        f"What the command is expected to change has more than {MAX_EXPECTS_LINES} "
        "lines. Say it in fewer, or name a file that holds the rest."
    ),
    RequestRefusal.REASON_NUL: (
        "The reason contains a NUL character (U+0000), which cannot be shown or "
        "stored. Send the request again without it."
    ),
    RequestRefusal.REASON_LONE_SURROGATE: (
        "The reason contains a lone surrogate (U+D800 to U+DFFF), which is not a "
        "character and cannot be shown or stored. Send the request again without it."
    ),
    RequestRefusal.EXPECTS_NUL: (
        "What the command is expected to change contains a NUL character (U+0000), "
        "which cannot be shown or stored. Send the request again without it."
    ),
    RequestRefusal.EXPECTS_LONE_SURROGATE: (
        "What the command is expected to change contains a lone surrogate (U+D800 to "
        "U+DFFF), which is not a character and cannot be shown or stored. Send the "
        "request again without it."
    ),
}

# The refusals that say the string could not be run as written, whatever the
# bounds are. A ``Run`` is held to these and to none of the others: a size is a
# policy about what the operator is asked to read, and a record written under
# one bound has to stay readable under another.
_UNRUNNABLE = frozenset(
    {
        RequestRefusal.EMPTY,
        RequestRefusal.NUL,
        RequestRefusal.LONE_SURROGATE,
        RequestRefusal.LEADS_WITH_OPTION,
    }
)


def _has_nul(text: str) -> bool:
    return "\x00" in text


def _has_lone_surrogate(text: str) -> bool:
    return any("\ud800" <= ch <= "\udfff" for ch in text)


def command_refusal(command: str) -> RequestRefusal | None:
    """
    Why this string may not be a command, or None.

    The half of ``request_refusal`` that is about the string alone, and the one
    the operator's edit is held to: an edit has no reason and no expectation,
    and it reaches the same shell.

    Nothing is stripped before the tests and nothing is stripped after: the
    string that passes is the string that is shown and run. Leading whitespace
    therefore passes -- see ``RequestRefusal`` for why that is the shell's
    reading too.
    """
    if not command.strip():
        return RequestRefusal.EMPTY
    if _has_nul(command):
        return RequestRefusal.NUL
    if _has_lone_surrogate(command):
        return RequestRefusal.LONE_SURROGATE
    if command[0] in "-+":
        return RequestRefusal.LEADS_WITH_OPTION
    if len(command) > MAX_COMMAND_CHARS:
        return RequestRefusal.TOO_LONG
    # What stands between two newlines, and before the first and after the
    # last -- except after a newline that is the last character, which ends a
    # line and begins none.
    lines = command.split("\n")
    if lines[-1] == "":
        lines.pop()
    if len(lines) > MAX_COMMAND_LINES:
        return RequestRefusal.TOO_MANY_LINES
    if any(not line.strip() for line in lines):
        return RequestRefusal.BLANK_LINE
    if len(visible(command)) > MAX_COMMAND_SHOWN_CHARS:
        return RequestRefusal.SHOWN_TOO_LONG
    # A run that ends its line is drawn, as ``\x20`` each, and the bound above
    # has counted it.
    if any(" " * (MAX_COMMAND_SPACE_RUN + 1) in line.rstrip(" \t") for line in lines):
        return RequestRefusal.SPACE_RUN
    if len(_split_segments(command)) > MAX_COMMAND_SEGMENTS:
        return RequestRefusal.TOO_MANY_SEGMENTS
    return None


def request_refusal(command: str, reason: str, expects: str) -> RequestRefusal | None:
    """
    Why this request may not be put in front of the operator, or None.

    The command's own refusal is reported first: it is the one that would have
    to be fixed whatever the other two fields say.
    """
    refusal = command_refusal(command)
    if refusal is not None:
        return refusal
    if len(reason) > MAX_REASON_CHARS:
        return RequestRefusal.REASON_TOO_LONG
    if len(expects) > MAX_EXPECTS_CHARS:
        return RequestRefusal.EXPECTS_TOO_LONG
    if len(reason.splitlines()) > MAX_REASON_LINES:
        return RequestRefusal.REASON_TOO_MANY_LINES
    if len(expects.splitlines()) > MAX_EXPECTS_LINES:
        return RequestRefusal.EXPECTS_TOO_MANY_LINES
    if _has_nul(reason):
        return RequestRefusal.REASON_NUL
    if _has_lone_surrogate(reason):
        return RequestRefusal.REASON_LONE_SURROGATE
    if _has_nul(expects):
        return RequestRefusal.EXPECTS_NUL
    if _has_lone_surrogate(expects):
        return RequestRefusal.EXPECTS_LONE_SURROGATE
    return None


# -- how it ended -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Exited:
    """The shell exited by itself. ``code`` is its status, zero or not."""

    code: int


@dataclass(frozen=True, slots=True)
class TimedOut:
    """
    No exit within ``Run.timeout_s``, and the shell's session was killed. Not a
    failure of the command: anything that prompts waits forever with no terminal.
    """


class CancelledBy(enum.Enum):
    """Who stopped a running command: the operator at the row, or pptmstr quitting."""

    OPERATOR = "operator"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True, slots=True)
class Cancelled:
    """The shell's session was killed before the shell exited or the timeout fell."""

    by: CancelledBy


@dataclass(frozen=True, slots=True)
class NotStarted:
    """
    It was approved and no process came of it. ``reason`` is pptmstr's own
    words -- no runner, no usable shell, the directory gone -- never the asker's.
    """

    reason: str


@dataclass(frozen=True, slots=True)
class Refused:
    """The operator refused it. ``reason`` is what they typed, byte for byte."""

    reason: str


@dataclass(frozen=True, slots=True)
class Withdrawn:
    """Its asker took it back before the operator acted."""


@dataclass(frozen=True, slots=True)
class Lapsed:
    """It was still only asked when pptmstr stopped. A request does not outlive the window."""


# The variable a command's shell is given that nobody set. Its value is made
# for the one run and is written nowhere; a record holds a digest of it. What
# it is for, and what it cannot see, is in ``commandrun``.
COMMAND_TAG = "PPTMSTR_COMMAND"

_DIGEST_CHARS = 64

# The most of a ``/proc/<pid>/cgroup`` a record holds.
MAX_CGROUP_CHARS = 4_096


def is_tag_digest(s: object) -> bool:
    """
    Whether ``s`` has the form ``Started.tag`` has: a SHA-256, as 64 lower-case
    hexadecimal digits.
    """
    return (
        isinstance(s, str)
        and len(s) == _DIGEST_CHARS
        and all(char in "0123456789abcdef" for char in s)
    )


@dataclass(frozen=True, slots=True)
class Started:
    """
    The process a run became, as it was the moment after it was started: what
    finds it again when pptmstr no longer holds it.

    ``pgid`` is the group every signal of pptmstr's is sent to; the shell leads
    it, so it is ``pid`` too. A pid is handed out again once its process and
    its group are gone, so the two numbers alone do not say that a process
    found under them later is this one. ``start_ticks`` (when the shell
    started, in clock ticks since boot) and ``boot_id`` (which boot that was)
    do, and are None where the platform has no ``/proc`` to read them from --
    there, nothing can tell this process from a later one with its pid, and
    nothing claims to.

    ``tag`` is the SHA-256, in hexadecimal, of the value the shell's
    environment was given under ``COMMAND_TAG``, which everything the shell
    starts inherits: what a process of the command is known by once the shell
    is gone or the process has left its session. **The digest and never the
    value**: this record is a file a contained session may come to read, and
    whoever holds the value can start a process that is taken for the
    command's. None in a record of a command that was started without one,
    and nothing is then looked for by it.

    ``cgroup`` is the shell's ``/proc/<pid>/cgroup`` as it was started: the
    one thing a process that cannot be read for the tag can still be tied to
    the command by (``commandrun``). None where it could not be read, and
    nothing is then tied.
    """

    pid: int
    pgid: int
    at: float
    start_ticks: int | None = None
    boot_id: str | None = None
    tag: str | None = None
    cgroup: str | None = None

    def __post_init__(self) -> None:
        # 1 is init, and a group id of 0 or 1 given to ``kill`` is this
        # process's own group or every process there is.
        if self.pid <= 1 or self.pgid <= 1:
            raise ValueError(f"a started process has a pid and a group: {self.pid}, {self.pgid}")
        if self.tag is not None and not is_tag_digest(self.tag):
            # Anything else here would be compared with a digest and never
            # equal one, or would be the value itself, on its way to a file.
            raise ValueError("a tag is the SHA-256 of a value, as 64 hexadecimal digits")
        if self.cgroup is not None and not 0 < len(self.cgroup) <= MAX_CGROUP_CHARS:
            raise ValueError(f"a cgroup is at most {MAX_CGROUP_CHARS} characters, and not none")


def ps_line(pids: tuple[int, ...]) -> str:
    """
    The command that shows the processes with these pids, to copy.
    """
    return "ps -o pid,pgid,sid,lstart,args -p " + ",".join(str(pid) for pid in pids)


@dataclass(frozen=True, slots=True)
class Survivor:
    """
    What was found alive of a command whose end nobody recorded.

    ``pid`` is the shell's, which is the number of the session and of the
    group it led, and ``pgid`` that group. ``members`` is how many processes
    in that session are known to be the command's: with the shell there
    (``shell``), every live process of the session; with it gone, those that
    carry the command's tag (``Started.tag``), whose pids are ``marked``.

    ``escaped`` are the pids of processes that carry the tag and are in
    another session: what the command started that left with ``setsid``.

    ``groups`` and ``pids`` are what a signal has to be sent to for all of
    those to get it: process groups, ascending, and single processes. With the
    shell there, the groups of its session are all in ``groups``, whatever
    they hold: the session is the command's. Every other process is known by
    its tag alone, and its group is in ``groups`` only when every live process
    of that group carries the tag; otherwise the process is in ``pids``, by
    itself. Both empty in a record nothing counted the groups of, where
    ``pgid`` is the one known -- which only a record with the shell there is.

    ``unread`` is how many live processes of the session number, with the
    shell gone, could not be read for the tag: not counted, not named, and not
    known to be anyone else's. A process that could not be read and is in no
    session of the command's is not in this number, whatever ties it.
    """

    pid: int
    pgid: int
    members: int
    groups: tuple[int, ...] = ()
    pids: tuple[int, ...] = ()
    escaped: tuple[int, ...] = ()
    marked: tuple[int, ...] = ()
    shell: bool = True
    unread: int = 0

    def __post_init__(self) -> None:
        if self.pid <= 1 or self.pgid <= 1 or self.members < 0 or self.unread < 0:
            raise ValueError("a survivor is a group, by its id, with something in it")
        if self.members + len(self.escaped) < 1:
            raise ValueError("a survivor is a group, by its id, with something in it")
        # As ``Started``: 0 and 1 given to ``kill`` are not a group of a command's,
        # and as a pid they are every process there is and init.
        if any(number <= 1 for number in (*self.groups, *self.pids, *self.escaped, *self.marked)):
            raise ValueError(
                f"a survivor's groups and processes are its own: {self.groups}, {self.pids}"
            )
        if not self.shell and (len(self.marked) != self.members or not (self.groups or self.pids)):
            # With the shell gone its pid may be anyone's, so nothing falls
            # back on ``pgid``: what is named is what carried the tag.
            raise ValueError("a survivor with no shell is the processes that carry its tag")
        if self.shell and (self.marked or self.unread):
            raise ValueError(
                "a survivor with its shell is its session, not what was read for a tag"
            )

    @property
    def alive(self) -> int:
        """
        How many processes are known to be the command's, in its session and out.
        """
        return self.members + len(self.escaped)


@dataclass(frozen=True, slots=True)
class Released:
    """
    The operator let go of a session that a command left running unsupervised
    was holding, at ``at`` on the wall clock, with ``alive`` of its processes
    found at the last look before that (``Survivor.alive``).

    Nothing was signalled, and nothing is looked for afterwards: what was
    running may still be, and from here it is the operator's alone.
    """

    at: float
    alive: int

    def __post_init__(self) -> None:
        if self.alive < 1:
            raise ValueError("a release lets go of something that was found running")


@dataclass(frozen=True, slots=True)
class EndUnknown:
    """
    A run was recorded and no end was. pptmstr stopped without recording one, so
    the command may have finished, may have failed, and may still be running.

    ``survivor`` is set when it was looked for and found: the command **is**
    still running, and nothing of pptmstr's bounds it -- no timeout and no
    cancel. It is a field of this outcome and not an outcome or a state of its
    own because the command is not running *here*: nothing can be approved,
    stopped or waited on through this record, which is what ended means. What
    it changes is in ``Command.unsupervised``.

    ``released`` is set instead when the operator let go of the session that
    survivor held: the record says it was released and how many were still
    found, and it no longer holds anything or owes a row.
    """

    survivor: Survivor | None = None
    released: Released | None = None

    def __post_init__(self) -> None:
        if self.survivor is not None and self.released is not None:
            raise ValueError("a command found running holds its session, or was released")


# A union and not a status beside optional fields: an exit code exists exactly
# when the shell exited, and a refusal's reason exactly when there was a refusal.
Outcome = Exited | TimedOut | Cancelled | NotStarted | Refused | Withdrawn | Lapsed | EndUnknown

_OUTCOME_TYPES = (Exited, TimedOut, Cancelled, NotStarted, Refused, Withdrawn, Lapsed, EndUnknown)


def ran(outcome: Outcome) -> bool:
    """
    Whether a process was started for the command.

    True is "this may have changed the operator's machine", which is the fact
    every surface has to keep apart from the four ways of ending without
    running. ``EndUnknown`` is on the ran side for that reason: what is unknown
    about it is how it ended.
    """
    match outcome:
        case Exited() | TimedOut() | Cancelled() | EndUnknown():
            return True
        case NotStarted() | Refused() | Withdrawn() | Lapsed():
            return False
        case _:
            assert_never(outcome)


def precludes_run(outcome: Outcome) -> bool:
    """
    Whether the outcome is one that can only end a command still in ``asked``.

    Not the negation of ``ran``. ``NotStarted`` is on neither side: the run is
    recorded before anything is started, so an approved command that never
    became a process has a ``Run``, and one turned away before that has none.
    """
    match outcome:
        case Refused() | Withdrawn() | Lapsed():
            return True
        case Exited() | TimedOut() | Cancelled() | EndUnknown() | NotStarted():
            return False
        case _:
            assert_never(outcome)


# -- the record -------------------------------------------------------------------


class CommandState(enum.Enum):
    """
    Where a command is. Never stored: ``Command.state`` reads it off ``run`` and
    ``end``, so there is no state to disagree with them.
    """

    ASKED = "asked"
    RUNNING = "running"
    ENDED = "ended"


@dataclass(frozen=True, slots=True)
class Run:
    """
    The operator's approval, as the facts of what was handed to the shell.

    ``text`` is the string that runs: the argument after ``-c``, byte for byte,
    whether or not the operator changed it. It is held to every
    ``command_refusal`` except the length, so no ``Run`` exists whose string a
    shell would have read as something other than a command.

    ``shell`` is the absolute path of the program that is given it, ``cwd`` the
    directory, ``timeout_s`` the bound the operator picked.
    """

    text: str
    shell: str
    cwd: str
    timeout_s: float
    started_at: float

    def __post_init__(self) -> None:
        refusal = command_refusal(self.text)
        if refusal in _UNRUNNABLE:
            raise ValueError(f"a run's text cannot be run as written: {refusal.value}")
        if not self.shell:
            raise ValueError("a run names the shell it was given to")
        if not self.timeout_s > 0:
            raise ValueError(f"a run is bounded: timeout_s is {self.timeout_s!r}")


# How many pids a remark on an end names. The rest are counted.
MAX_REMARK_PIDS = 4


@dataclass(frozen=True, slots=True)
class End:
    """
    How a command ended, and what it left.

    ``output_bytes`` is everything the process wrote, counted, including what
    was not kept; ``tail`` is the end of it. An outcome with no process has
    neither, and an ``End`` that claims otherwise is refused.

    ``leftover`` is how many processes of the session the command's shell led
    were still alive when the shell exited by itself, and were ended with it:
    0 is "none", and None is "not recorded" -- a record written before this was
    counted, or a ``/proc`` that could not be read. Only an ``Exited`` has one:
    every other end of a run killed the session, shell and all.

    ``unconfirmed`` is why nothing confirms that the command's processes are
    gone, in pptmstr's words, and None when they were seen to go. With it the
    outcome says what pptmstr did, and this says it was not seen to work.

    ``escaped`` are the pids of the processes that carried the command's tag
    (``Started.tag``) and were alive in another session than its shell's when
    it ended: what it started that left with ``setsid``. pptmstr signals none
    of them. Empty is "looked, and found none that could be read"; None is
    "not looked for" -- a record written before this was, a command with no
    tag, or a ``/proc`` that could not be read. They are pids as of ``at``: a
    number is handed out again once its process has gone, so they are shown
    with a line that looks and never with one that kills.

    ``unreadable`` are the pids of the processes alive at the end, outside
    the shell's session, that could not be read for the tag and that two
    things tie to the command: each started no earlier than its shell, and
    each was in the control group the shell was started in
    (``Started.cgroup``). **None of them is known to be the command's.** An
    ``ssh-agent`` it started is one, and so is anything else unreadable that
    was started beside it in that time. Empty is "none such", and also what a
    record written before this was looked for reads as.
    """

    outcome: Outcome
    at: float
    duration_s: float = 0.0
    output_bytes: int = 0
    tail: str = ""
    leftover: int | None = None
    unconfirmed: str | None = None
    escaped: tuple[int, ...] | None = None
    unreadable: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        # The annotation is not enforced at runtime, and a record read from a
        # file is built at runtime.
        if not isinstance(self.outcome, _OUTCOME_TYPES):
            raise ValueError(f"an end has an outcome, not {self.outcome!r}")
        if self.output_bytes < 0 or self.duration_s < 0:
            raise ValueError("an end's output_bytes and duration_s are not negative")
        if not ran(self.outcome) and (self.output_bytes or self.tail):
            raise ValueError(
                f"{type(self.outcome).__name__} started no process, so it has no output"
            )
        if self.leftover is not None and (
            not isinstance(self.outcome, Exited) or self.leftover < 0
        ):
            raise ValueError("leftover counts what a shell that exited by itself left behind")
        if self.unconfirmed is not None and not (ran(self.outcome) and self.unconfirmed):
            raise ValueError("unconfirmed is the reason a started process was not seen to go")
        if self.escaped is not None and (
            not ran(self.outcome) or any(pid <= 1 for pid in self.escaped)
        ):
            raise ValueError("escaped are the pids of processes a started command left")
        if self.unreadable and (not ran(self.outcome) or any(p <= 1 for p in self.unreadable)):
            raise ValueError("unreadable are the pids of processes beside a started command")

    @property
    def remarks(self) -> tuple[str, ...]:
        """
        What ``leftover``, ``escaped``, ``unreadable`` and ``unconfirmed`` say, one sentence
        each, for a surface that shows how the command ended. Empty for an end
        with none of them. Each is at most ``MAX_REMARK_PIDS`` pids long
        whatever the record holds.
        """
        said: list[str] = []
        if self.leftover:
            said.append(
                f"{self.leftover} leftover process{'' if self.leftover == 1 else 'es'} of its "
                "session, still running when its shell exited, "
                f"{'was' if self.leftover == 1 else 'were'} ended with it"
            )
        if self.escaped_remark is not None:
            said.append(self.escaped_remark)
        if self.unreadable_remark is not None:
            said.append(self.unreadable_remark)
        if self.unconfirmed is not None:
            said.append(f"NOT CONFIRMED DEAD: {self.unconfirmed}")
        return tuple(said)

    @property
    def unreadable_remark(self) -> str | None:
        """
        What ``unreadable`` says, or None when it is empty.
        """
        if not self.unreadable:
            return None
        count = len(self.unreadable)
        shown = self.unreadable[:MAX_REMARK_PIDS]
        more = f" and {count - len(shown)} more" if count > len(shown) else ""
        return (
            f"{count} process{'' if count == 1 else 'es'} started during it, beside it"
            f" and outside its shell's session, could not be read for its {COMMAND_TAG}"
            f" and may be its: pid {', '.join(str(pid) for pid in shown)}{more}, as of"
            f" its end. To look: {ps_line(shown)}"
        )

    @property
    def escaped_remark(self) -> str | None:
        """
        What ``escaped`` says, or None when it is empty or was not looked for.
        """
        if not self.escaped:
            return None
        count = len(self.escaped)
        shown = self.escaped[:MAX_REMARK_PIDS]
        more = f" and {count - len(shown)} more" if count > len(shown) else ""
        return (
            f"{count} process{'' if count == 1 else 'es'} it started "
            f"{'is' if count == 1 else 'are'} ALIVE outside its shell's session, and pptmstr"
            f" did not end {'it' if count == 1 else 'them'}: pid "
            f"{', '.join(str(pid) for pid in shown)}{more}, as of its end. To look: "
            f"{ps_line(shown)}"
        )


@dataclass(frozen=True, slots=True)
class Command:
    """
    One command a contained agent asked the operator to run.

    ``asked`` is the string exactly as the agent sent it, and ``reason`` and
    ``expects`` are the agent's too. None of the three is ever rewritten: what
    the operator ran instead is ``run.text``, beside it.

    ``run`` is set by the operator's approval and ``end`` by whatever closed the
    command. Which of the two are set is the whole of the state:

    ===========  =========  ==========================================
    ``run``      ``end``    state
    ===========  =========  ==========================================
    None         None       asked
    set          None       running
    None         set        ended, never approved
    set          set        ended, after an approval
    ===========  =========  ==========================================

    What is refused at construction, because it would describe something that
    did not happen: an ``end`` whose outcome ran with no ``run``; a ``run`` under
    an outcome that only ends a command nobody approved (``precludes_run``); and
    ``stop_asked_at`` with no ``run``, since there is nothing to stop.

    ``stop_asked_at`` is when the operator asked for a running command to be
    killed. ``first_delivered_at`` is when the asker was first handed the result,
    a fact for the pane as on ``Decision``.
    """

    id: CommandId
    asker: NodeId
    asked: str
    reason: str
    expects: str
    asked_at: float
    run: Run | None = None
    stop_asked_at: float | None = None
    end: End | None = None
    first_delivered_at: float | None = None

    def __post_init__(self) -> None:
        if self.stop_asked_at is not None and self.run is None:
            raise ValueError("a stop was asked of a command that has no run")
        if self.end is None:
            return
        outcome = self.end.outcome
        if ran(outcome) and self.run is None:
            raise ValueError(f"{type(outcome).__name__} is an outcome of a run, and there is none")
        if precludes_run(outcome) and self.run is not None:
            raise ValueError(f"{type(outcome).__name__} ends a command that was never approved")

    @property
    def session_id(self) -> str:
        """The session it was asked in, which is its asker's."""
        return self.asker[0]

    @property
    def state(self) -> CommandState:
        if self.end is not None:
            return CommandState.ENDED
        if self.run is not None:
            return CommandState.RUNNING
        return CommandState.ASKED

    @property
    def is_open(self) -> bool:
        """Asked or running: the two states that owe the operator a row."""
        return self.end is None

    @property
    def edited(self) -> bool:
        """
        Whether the string that ran is not the string that was asked. False
        until there is a run: an edit nobody approved is a draft, not a fact.
        """
        return self.run is not None and self.run.text != self.asked

    @property
    def unsupervised(self) -> Survivor | None:
        """
        What this command left running with nothing of pptmstr's holding it,
        or None.

        Such a command has ended as far as this record goes, and it still does
        two things an ended command does not: it owes the operator a row,
        because only they can end it, and it keeps its session from being given
        another command to run beside it.
        """
        if self.end is None or not isinstance(self.end.outcome, EndUnknown):
            return None
        return self.end.outcome.survivor

    @property
    def owes_row(self) -> bool:
        """Asked, running, or left running unsupervised."""
        return self.end is None or self.unsupervised is not None
