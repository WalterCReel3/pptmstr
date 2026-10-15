"""
The inbox: everything waiting on the operator, oldest first, in one pane.

This is the application. The operator is a bottleneck by design, so the screen is
the bottleneck's work surface -- approvals, questions and failures merged into a
single queue rather than split across a pane, a tree badge and nothing at all.

**The row under the cursor expands in place.** A parked tool call opens its own
diff and approve/reject/edit, a question opens a composer, a failure opens the
error and a way to clear it, a decision opens what its asker wrote and the box
that answers it. Every decision is made here, at the row, so there is never a diff
on screen belonging to a different agent than the one an ``a`` would approve.

**A break-glass command is the exception: the cursor does not open it.** Its row
reads as a Bash approval's (``command_summary``, ``command_view``), and under the
cursor it says which command it is and that it is closed; its body and its presses
are drawn only once the operator has clicked that row or pressed ``OPEN_KEY`` on
it: the cursor arrives on a row by routes the operator did not choose, and this is
the one row whose text runs as them.

Everything in a row is clipped -- fixed columns, ``ellipsis``, fixed-height bodies
-- because this pane is scanned rather than read. The unclipped, wrapped rendering
of the same obligation lives in ``ui/detail.py``, which follows this pane's cursor
and cannot move it. Two surfaces, one cursor, opposite rules about width.

Identity on every row is the session's **task**, qualified by project and, for a
sub-agent, its name. Every root session is called "session"; at twenty sessions the
old queue read "session" on six rows of eight. The fix that landed for the rail's
cards did not generalise here on its own, because the inbox had only ever been
looked at with seven sessions in one project.
"""

from __future__ import annotations

import functools
import math
import os
import re
from collections.abc import Callable, Hashable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from typing import Any, assert_never

from imgui_bundle import imgui

from .. import approval
from ..approval import DiffText, command_shown, diff_line_kind
from ..board import role_name
from ..bridge import Bridge
from ..command import (
    COMMAND_TAG,
    Command,
    CommandId,
    CommandState,
    Refused,
    Survivor,
    Withdrawn,
)
from ..commandrun import environment_changes, kill_line, look_line
from ..model import (
    AgentRecord,
    AgentState,
    ApprovalNeeded,
    CommandOwed,
    DecisionOwed,
    LaunchSpec,
    NodeId,
    Obligation,
    PendingApproval,
    QuestionPending,
    SessionFailed,
    Snapshot,
)
from ..theme import OBLIGATION_GLYPH, STATE_GLYPH, Face, P, face, faded
from ..transcript import Transcript
from . import command_view, decision_view, projects, review, splash, splash_art, typeset
from .compose import ComposeState, wants_send
from .focus import FocusState
from .widgets import (
    CTRL_ENTER_SUBMITS,
    author_text,
    ellipsis,
    flat_line,
    format_elapsed,
    help_mark,
    multiline_input,
    one_line,
)

# Column origins, measured from the row's left edge. Fixed zones with each field
# ellipsized into its own, rather than laid out with same_line(): letting the fields
# push each other along means a long agent name shifts the wait time under the
# summary, and the summary is the column being scanned down.
_X_GLYPH = 10.0
_X_TITLE = 34.0
_X_QUALIFIER = 254.0
_X_WAIT_END = 458.0
_X_SUMMARY = 476.0

_TITLE_W = _X_QUALIFIER - _X_TITLE - 10.0

# How much of a turn's prose the expanded question row previews in ImGui's own
# text. The box is a fixed 110px, so this only has to be comfortably more than fits.
_PREVIEW_CHARS = 1200
# The typeset preview: the last whole blocks of the turn that come to about this
# many lines of the prose face at ``_PREVIEW_MEASURE`` characters to the line, in a
# view no taller than that many lines. Ten and not the native box's six: the end of
# a turn is as often a table or a list as a sentence, and six lines of either is
# the head of it. A turn that ends on three lines takes three.
_PREVIEW_LINES = 10
_PREVIEW_MEASURE = 96
_QUALIFIER_W = 150.0

_SMALL_FONT = 12.5

# The two spellings the CLI uses for "start a sub-agent". Both are in
# ``approval._REVIEW``, so both park, and a marker that knew only one would be
# silently absent for half the spawns.
_SPAWN_TOOLS = frozenset({"Agent", "Task"})

_SUFFIX = {1: "st", 2: "nd", 3: "rd"}

# The tallest an expanded approval's body gets, in lines of the UI font: the diff
# child's height and a typeset document's cap, so a row costs the queue no more
# under either renderer. A diff the library draws is this many of its own rows,
# under the lines its header says, which are set above it.
_BODY_LINES = 24.0

# The typeset views this pane holds between frames, keyed ``(obligation.key, part)``.
#
# Module-level for the reason ``_ART_FRAMES`` below is: ``draw`` is handed no
# presentation-state object of this pane's own, ``review.ReviewState`` is shared
# with DETAIL -- which draws the same parked call in the same frame and must not
# share a view with this pane, because a view holds one scroll position and one
# origin -- and hanging it on either would mean editing a file this pane does not
# own. Pruned in ``draw`` by the obligations still in the queue, the rule
# ``ReviewState.prune`` applies to everything else remembered about an approval.
_VIEWS = typeset.ViewCache()
_VIEW_PARTS = ("diff", "body", "arguments", "preview")

# The parts last made for a view, beside what they were made from, under the view's
# key and pruned with it. Kept because the seam's body classifier reads every line
# of its text, and because a tuple handed to ``typeset.document`` again unchanged
# is compared by identity and not part by part.
_PARTS: dict[Hashable, tuple[object, tuple[typeset.Part, ...]]] = {}

# The preview last cut for a question row, beside the length of the transcript it
# was cut from, under the view's key and pruned with it. A transcript is append-only,
# so a length that has not moved is a turn that has not.
_PREVIEWS: dict[Hashable, tuple[int, int, str]] = {}

# The width of the widest row of a diff, beside the lines it was measured over,
# under the view's key and pruned with it: every row of a whole-file Write is
# measured once and not once a frame.
_WIDTHS: dict[Hashable, tuple[object, float]] = {}

# What a diff drawn as a pane's own rows holds that cannot be seen, as the
# sentence said over those rows (``hidden_in_rows``), beside the unified string
# it was read from, under the view's key and pruned with it: the string is as
# long as the change, and it is read once and not once a frame.
_HIDDEN: dict[Hashable, tuple[str | None, str | None]] = {}

# The bodies the operator asked to have set whole, past the bound ``_typeset_body``
# puts on one, by the view's key and pruned with it.
_WHOLE: set[Hashable] = set()

_NO_DIFF = "no diff for this call - arguments:"
_WITHHELD = "shown as the agent sent it - approve sends YOUR EDIT, which differs"
_NOT_ON_SCREEN = "the body above is not on screen - approve is held until it is"

# The bus tools' names as the CLI spells them. ``approval.py`` owns the spelling and
# keeps it private; tests/test_inbox_typeset.py fails if these two drift from it.
_POST_CONCERN = "mcp__pptmstr__post_concern"
_DECLARE_TASK = "mcp__pptmstr__declare_task"

# Calls whose substance is prose rather than a file change: for each, the argument
# that is the prose and the arguments that head it, in reading order. Any argument
# named in neither place is still shown, after these -- see ``describe``.
#
# **The one table.** DETAIL reads which argument is prose from here
# (``prose_argument``), so a call cannot be prose in its row and a literal beside it.
#
# An argument is here when its tool says it is text somebody writes for a reader,
# and each is as its tool's schema has it:
#
# - the bus's ``body`` and ``detail`` (``pptmstr.bus``) and a spawn's ``prompt``;
# - ``ExitPlanMode.plan``, the plan file's markdown, with the file's path;
# - ``SendMessage.message``, "Plain text message content" -- or an object, for the
#   protocol messages, which is then a field like any other value not a string;
# - ``TaskCreate.description``, "What needs to be done", and ``TaskUpdate``'s;
# - ``WebFetch.prompt``, "The prompt to run on the fetched content".
#
# Nothing promises any of them is *markdown*, the bus's included, which is why what
# each is read as is ``typeset.prose_parts``' answer and not this table's.
#
# Never here: an argument that is acted on character for character -- a command, a
# file's content, an edit's strings, a pattern, a path, a URL, a query. A markdown
# reading of ``rm *tmp and old*`` draws no stars.
#
# ``AskUserQuestion`` is not here either: its text is a list of questions each
# with options, not one argument, and is set as the structure it is.
DESCRIBED: dict[str, tuple[str, tuple[str, ...]]] = {
    _POST_CONCERN: ("body", ("to", "subject", "task_id")),
    _DECLARE_TASK: ("detail", ("task_id", "title", "depends_on", "touches")),
    "Agent": ("prompt", ("subagent_type", "description")),
    "Task": ("prompt", ("subagent_type", "description")),
    "ExitPlanMode": ("plan", ("planFilePath",)),
    "SendMessage": ("message", ("to", "summary")),
    "TaskCreate": ("description", ("subject", "activeForm")),
    "TaskUpdate": ("description", ("taskId", "subject", "status")),
    "WebFetch": ("prompt", ("url",)),
}
assert _SPAWN_TOOLS <= DESCRIBED.keys()
_LIST_FIELDS = frozenset({"depends_on", "touches"})

# Where a field's value starts, from the left edge of the block. Wide enough for
# the longest name in ``DESCRIBED`` in either prose face.
_X_FIELD_VALUE = 120.0


@dataclass
class InboxActions:
    """
    What the expanded row can do. Passed in so this pane touches no session object
    and no event loop -- everything leaves through the Bridge or the pool, on the
    thread that owns them.
    """

    send: Callable[[NodeId, str], None]
    interrupt: Callable[[NodeId], None]
    close: Callable[[NodeId], None]
    dismiss: Callable[[NodeId], None]
    # task, model, cwd, template. The template is carried rather than defaulted:
    # dropping it turns a retry of a team session into a solo one, and nothing on
    # screen would say so.
    relaunch: Callable[[LaunchSpec], None]


@dataclass(frozen=True, slots=True)
class CommandRows:
    """
    What a break-glass command's row is drawn with that no snapshot holds.

    One value for the inbox and for DETAIL, so the two cannot be handed
    different drafts, shells or clocks.

    ``shell`` and ``stripped_env`` are what the caller found once, at startup
    (``commandrun.shell_for``, ``commandrun.environment_changes``); the
    directory is each session's own and is read off its record (``where``),
    and so is what is taken out of the environment because of that directory.
    ``now`` is the WALL clock: a command's times are written to its files and
    read by another process, so they are not on the frame's monotonic one.

    ``wired`` is whether the three presses reach anything. While it is False
    the row says so and every control on it is drawn disabled: a press that
    armed, confirmed and then did nothing would be a run the operator believed
    they had started.

    ``unrecorded`` is why a command's record is not on disk, in the caller's
    words, for each command that has such a reason. ``restored`` are the
    commands that came from a file and not from an agent in this window: a
    file does not say whether the asker read the result.

    ``on_check`` is the press that has a command left running unsupervised
    looked for again; None draws no such button. ``checked`` is what the last
    look at each found, in the caller's words. ``on_release`` is the second
    press of 'release' on such a command, which lets go of the session it
    holds and signals nothing; None draws no such button.
    """

    state: command_view.CommandViewState
    shell: str | None
    stripped_env: tuple[str, ...]
    actions: command_view.Actions
    now: float
    wired: bool = False
    unrecorded: Mapping[CommandId, str] = field(default_factory=dict)
    restored: AbstractSet[CommandId] = frozenset()
    on_check: Callable[[CommandId], None] | None = None
    checked: Mapping[CommandId, str] = field(default_factory=dict)
    on_release: Callable[[CommandId], None] | None = None


# What a command's row says above its body while ``CommandRows.wired`` is False.
NOT_WIRED = (
    "not wired in this build: nothing here can run, refuse or cancel a command yet."
    " the controls below are drawn and answer nothing"
)
# What stands where the body would be for a caller that handed no ``CommandRows``.
NO_COMMAND_ROWS = "this pane was given nothing to draw a command with"
# Ahead of why a command's record is not on disk.
UNRECORDED = "not recorded on disk - "
# What a session with no directory on its record is said to run in.
NO_DIRECTORY = "(this session's record names no directory)"


# Ahead of a command's summary on its queue row, by how it stands. A Bash
# approval's row is the command; this one is too, said to run outside.
OUTSIDE = "outside the sandbox · "
RUNNING_OUTSIDE = "running outside the sandbox · "
CANCEL_ASKED = "cancel asked · "
UNSUPERVISED = "STILL RUNNING, unsupervised · "


def command_summary(command: Command) -> str:
    """
    A command's queue row: ``approval.summarize`` of it as a Bash call, which
    is how a Bash approval's row is made, behind how it stands. The string is
    the one that would run or is running.

    Not ``CommandOwed.summary``, which the store composes from structure alone
    and the digest and the rail read: on this row the command is drawn as a
    Bash command is, through ``commandtext.visible`` and flattened, and the
    row's body has it whole.
    """
    run = command.run
    said = approval.summarize("Bash", {"command": command.asked if run is None else run.text})
    if command.unsupervised is not None:
        return UNSUPERVISED + said
    if run is None:
        return OUTSIDE + said
    stopping = CANCEL_ASKED if command.stop_asked_at is not None else ""
    return RUNNING_OUTSIDE + stopping + said


def row_summary(obligation: Obligation) -> str:
    """
    The summary column of a queue row: a command's own (``command_summary``),
    and the store's for every other kind.
    """
    if isinstance(obligation, CommandOwed):
        return command_summary(obligation.command)
    return obligation.summary


def command_role(snap: Snapshot, command: Command) -> str:
    """What to call a command's asker: its role on its own session's board."""
    return role_name(snap, command.asker, command.session_id)


def where(snap: Snapshot, rows: CommandRows, command: Command) -> command_view.Where:
    """
    What ``command`` would be run by and in: the caller's shell, and the
    directory on the record of the session it was asked in.

    The session's and not the asker's own: a sub-agent has no directory apart
    from its session's, and the session's is where the runner starts the shell.

    What is taken out of the environment is the caller's list and then what
    that directory adds to it (``_taken_out_for``): a search-path entry inside
    the directory is one the runner drops for this session and for no other.
    """
    root = snap.nodes.get((command.session_id, None))
    cwd = root.cwd if root is not None else None
    added = _taken_out_for(cwd, int(rows.now // _DIRECTORY_LOOK_S)) if cwd else ()
    return command_view.Where(
        shell=rows.shell,
        cwd=cwd or NO_DIRECTORY,
        stripped_env=rows.stripped_env + added,
        home=os.path.expanduser("~"),
    )


# How long one look at a session's directory stands, in seconds of the wall
# clock. The look follows the links of every search-path entry, which is a
# read of the disk and not something to do sixty times a second; a link made
# since the last one is on the row within this long.
_DIRECTORY_LOOK_S = 2.0


@functools.lru_cache(maxsize=16)
def _taken_out_for(cwd: str, stamp: int) -> tuple[str, ...]:
    """
    What ``commandrun.environment`` takes out for a command run in ``cwd``
    that it would not take out anywhere: the lines ``environment_changes``
    has with the directory and not without it. **Reads the disk.**

    ``stamp`` is only the cache's: the answer is kept until it changes
    (``_DIRECTORY_LOOK_S``).
    """
    anywhere = set(environment_changes())
    return tuple(line for line in environment_changes(cwd=cwd) if line not in anywhere)


def unrecorded_line(rows: CommandRows | None, command: Command) -> str | None:
    """
    What to say of a command whose record did not reach the disk, or None. On
    one line: the reason is the caller's and may quote an error.
    """
    if rows is None or command.id not in rows.unrecorded:
        return None
    return one_line(f"{UNRECORDED}{rows.unrecorded[command.id]}")


# Nothing on disk says which process holds a command, so this window cannot
# tell a command orphaned by a pptmstr that died from one a second window is
# running and bounding right now. The row is worded to be true of both.
HELD_ELSEWHERE = (
    "if another pptmstr window is running this session, the command is that window's,"
    " and its timeout and its cancel are there: check there before you kill anything."
)

# The button that has a command left running unsupervised looked for again.
CHECK_AGAIN = "check again"
COPY_KILL = "copy the kill line"
COPY_LOOK = "copy the ps line"

# The one line that says what holds the session and the two ways out of it.
WAYS_OUT = (
    f"this holds its session: no other command runs here until it ends ('{CHECK_AGAIN}')"
    " or you release the session"
)
# The two presses that let go of the session, and what stands between them.
RELEASE = "release session..."
CONFIRM_RELEASE = "release - leave it running"
RELEASE_ARMED = "release? nothing is signalled; it runs on, unwatched"
RELEASE_HELP_TITLE = "Releasing a session"
RELEASE_HELP = (
    "A command found running with no pptmstr holding it keeps its session from"
    " running any other command. Two things end that: its processes ending, which"
    f" '{CHECK_AGAIN}' sees, or you releasing the session.",
    "Releasing sends no signal to anything. What was running keeps running, as"
    " you, with nothing of pptmstr's bounding or watching it. Use it for a command"
    " meant to leave something running, such as a server.",
    "The command's record keeps that you released it and how many of its processes"
    " were found at the last look; released.json beside it keeps the line that"
    " shows them. It is not looked for again, and its asker is told.",
)


def survivor_lines(left: Survivor) -> tuple[str, ...]:
    """
    What the row of a command left running unsupervised says above its body.
    Every word is this pane's and every number is from the record and from
    ``/proc``: nothing an agent or a program wrote.

    The two command lines are by themselves, so that each can be read, and
    copied, exactly.

    With the shell there, what is counted is its session and what is said of
    the processes outside it (``Survivor.escaped``) is one more line. With
    the shell gone nothing is its session's by the number alone, and every
    count is of processes that carry the command's tag.
    """
    if not left.shell:
        return _leaderless_lines(left)
    count = f"{left.members} process{'' if left.members == 1 else 'es'}"
    outside = len(left.escaped)
    escaped = (
        (
            f"{outside} more process{'' if outside == 1 else 'es'} of it"
            f" {'is' if outside == 1 else 'are'} alive OUTSIDE that session (pid"
            f" {_pids(left.escaped)}): started with its {COMMAND_TAG} in"
            f" {'its' if outside == 1 else 'their'} environment, after its shell. No"
            " pptmstr ends a process that has left the session; the two lines below"
            " name them too, a whole process group only where every process in it"
            " carries that variable.",
        )
        if outside
        else ()
    )
    return (
        "STILL RUNNING, UNSUPERVISED.",
        f"a pptmstr started this command and no end of it is on record. The session"
        f" its shell leads (pid {left.pid}) still has {count}. This"
        " window does not hold it: no timeout and no cancel of this window's applies,"
        " and it has your credentials and network for as long as it runs.",
        HELD_ELSEWHERE,
        *escaped,
        "to see every process in that session, and that it is this command:",
        look_line(left),
        "to end it, by every process group found in that session:",
        kill_line(left),
        WAYS_OUT,
    )


# How many pids a sentence of a row names. The two command lines name them all.
_MAX_PIDS_SAID = 8


def _pids(pids: tuple[int, ...]) -> str:
    shown = ", ".join(str(pid) for pid in pids[:_MAX_PIDS_SAID])
    more = len(pids) - _MAX_PIDS_SAID
    return shown if more <= 0 else f"{shown} and {more} more"


def _leaderless_lines(left: Survivor) -> tuple[str, ...]:
    """
    ``survivor_lines`` for a command whose shell is gone: what is alive is
    known by the tag in its environment and by nothing else.
    """
    count = f"{left.alive} process{'' if left.alive == 1 else 'es'}"
    where = []
    if left.marked:
        where.append(f"{len(left.marked)} in the session it led")
    if left.escaped:
        where.append(f"{len(left.escaped)} outside it (pid {_pids(left.escaped)})")
    unread = (
        (
            f"{left.unread} more process{'' if left.unread == 1 else 'es'} in that session"
            f" number could not be read - {'it runs' if left.unread == 1 else 'they run'}"
            " as another user, as under sudo, or with an empty environment, as under"
            " env -i - and"
            f" {'is' if left.unread == 1 else 'are'} NOT named below: pptmstr cannot tell"
            f" whether {'it is' if left.unread == 1 else 'they are'} this command's.",
        )
        if left.unread
        else ()
    )
    return (
        "STILL RUNNING, UNSUPERVISED.",
        f"a pptmstr started this command and no end of it is on record. Its shell (pid"
        f" {left.pid}) is gone, and {count} started with its {COMMAND_TAG} in the"
        f" environment, after that shell and in this boot, {'is' if left.alive == 1 else 'are'}"
        f" alive: {' and '.join(where)}. This window does not hold"
        f" {'it' if left.alive == 1 else 'them'}: no timeout and no cancel of this window's"
        " applies, and what runs has your credentials and network for as long as it runs.",
        HELD_ELSEWHERE,
        *unread,
        f"to see {'it' if left.alive == 1 else 'them'}, and that this is the command:",
        look_line(left),
        "to end what carries that variable - a whole process group only where every"
        " process in it does, single processes otherwise:",
        kill_line(left),
        "a process of this command that was started with its environment emptied (env -i,"
        " sudo) is not found this way and is not named.",
        WAYS_OUT,
    )


def _clicked(label: str) -> bool:
    """
    A small button that answers the mouse and nothing else, as every button on
    a command's row does (``command_view``): with keyboard navigation on, Space
    and Enter press a focused button, and no key acts on this row.
    """
    return bool(imgui.small_button(label)) and bool(
        imgui.is_mouse_released(imgui.MouseButton_.left)
    )


def _release(
    rows: CommandRows, command_id: CommandId, on_release: Callable[[CommandId], None], live: bool
) -> None:
    """
    'release', as the command row's run is pressed: the first press arms, a
    confirmation replaces it, and that answers only after
    ``command_view.ARM_DELAY_S`` and only to a mouse release. Dead in a copy
    that is not live, so an armed press has one place to land.
    """
    state = rows.state
    if not live:
        imgui.begin_disabled()
    if isinstance(state.armed(command_id), command_view.Releasing):
        imgui.text_colored(P.warn.vec4, RELEASE_ARMED)
        if command_view._clicked(command_view.BACK):
            state.pending = None
        imgui.same_line()
        if command_view._second_press(state, CONFIRM_RELEASE):
            on_release(command_id)
            state.pending = None
    else:
        if command_view._first_press(state, RELEASE):
            command_view._arm(state, command_view.Releasing(command_id))
        help_mark(f"release:{command_id}", RELEASE_HELP_TITLE, RELEASE_HELP)
    if not live:
        imgui.end_disabled()


def _survivor(rows: CommandRows, command: Command, left: Survivor, live: bool) -> None:
    lines = survivor_lines(left)
    imgui.push_id(f"survivor:{command.id}")
    imgui.push_text_wrap_pos(0.0)
    for index, line in enumerate(lines):
        if line in (look_line(left), kill_line(left)):
            imgui.indent()
            imgui.text_unformatted(line)
            imgui.unindent()
        else:
            imgui.text_colored((P.danger if index < 3 else P.warn).vec4, line)
    said = rows.checked.get(command.id)
    if said is not None:
        imgui.text_colored(P.warn.vec4, one_line(said))
    # Copying is not acting, so these two answer in DETAIL's dead copy as well.
    if _clicked(COPY_LOOK):
        imgui.set_clipboard_text(look_line(left))
    imgui.same_line()
    if _clicked(COPY_KILL):
        imgui.set_clipboard_text(kill_line(left))
    if rows.on_check is not None:
        imgui.same_line()
        if not live:
            imgui.begin_disabled()
        if _clicked(CHECK_AGAIN):
            rows.on_check(command.id)
        if not live:
            imgui.end_disabled()
    if rows.on_release is not None:
        _release(rows, command.id, rows.on_release, live)
    imgui.pop_text_wrap_pos()
    imgui.pop_id()
    imgui.separator()


def draw_command(
    snap: Snapshot,
    rows: CommandRows | None,
    command: Command,
    *,
    wrap: bool = True,
    enabled: bool = True,
) -> None:
    """
    The body of a command that owes a row, through ``command_view`` and
    through nothing else: no text of the command, of its asker or of a program
    is drawn by this pane.

    One thing is said above that body, in this pane's own words: for a
    command left running unsupervised, that it is, and the two lines that find
    and end it (``survivor_lines``); the body under it is the ended command's,
    which offers nothing to press and says the same of how it stands.

    A row the operator has not opened (``CommandViewState.is_open``) gets
    neither: ``command_view.draw_row`` draws its closed lines, which say how
    it stands, and nothing of the command.

    ``enabled`` False draws the same body with no button and no box of the
    command's own, and the survivor's dead. DETAIL passes it: the row is where
    the operator acts, and two copies of one armed press would be two places a
    second press could land.
    """
    if rows is None:
        imgui.text_disabled(NO_COMMAND_ROWS)
        return
    if not rows.wired:
        imgui.text_colored(P.warn.vec4, NOT_WIRED)
    unrecorded = unrecorded_line(rows, command)
    if unrecorded is not None:
        imgui.text_colored(P.warn.vec4, unrecorded)
    live = enabled and rows.wired
    left = command.unsupervised
    if left is not None and rows.state.is_open(command.id):
        _survivor(rows, command, left, live)
    if not live:
        imgui.begin_disabled()
    command_view.draw_row(
        rows.state,
        command,
        role=command_role(snap, command),
        where=where(snap, rows, command),
        actions=rows.actions,
        now=rows.now,
        wrap=wrap,
        among=among(snap, rows, command),
        restored=command.id in rows.restored,
        presses=enabled,
    )
    if not live:
        imgui.end_disabled()


def among(snap: Snapshot, rows: CommandRows, command: Command) -> command_view.Among:
    """
    Where ``command`` stands among its session's and among its asker's: which
    of each it is, counted from one in the order they were asked, how many
    others of its asker's are still only asked, and whether it was asked a
    moment ago.
    """
    session = sorted(
        (c for c in snap.commands.values() if c.session_id == command.session_id),
        key=lambda c: (c.asked_at, c.id),
    )
    same = [c for c in session if c.asker == command.asker]

    def place(among: list[Command]) -> int:
        return 1 + next((i for i, c in enumerate(among) if c.id == command.id), len(among))

    return command_view.Among(
        in_session=place(session),
        ordinal=place(same),
        others_waiting=sum(1 for c in same if c.id != command.id and c.state is CommandState.ASKED),
        new=command_view.is_new(command, rows.now),
    )


# What follows what left, in the mark a departed row leaves.
NOTHING_UNDER_THE_CURSOR = (
    "nothing is open in its place and the cursor is on no row: j, k or a click on a" " row moves it"
)


def departure_line(snap: Snapshot, left: Obligation, now: float = 0.0) -> str:
    """
    What to say of the row that went from under the cursor, in this pane's
    words: what it was, whose, and -- where the snapshot still says -- why it
    is gone. Nothing its agent wrote is in it.

    ``now`` is the wall clock, read only by a command's own account of how it
    ended.
    """
    role = one_line(role_name(snap, left.node, left.node[0]))
    match left:
        case CommandOwed():
            tail = command_view.id_tail(left.command.id)
            went = _command_went(snap, left, now)
            return f"the command you were reading - {tail} from {role} - {went}"
        case ApprovalNeeded():
            tool = one_line(left.approval.tool_name)
            return (
                f"the approval you were on - {tool} from {role} - is no longer waiting:"
                " it was answered, or its agent ended"
            )
        case QuestionPending():
            return f"the turn you were on - {role} waiting for a reply - is no longer waiting"
        case SessionFailed():
            return f"the failed session you were on - {role} - is no longer in the queue"
        case DecisionOwed():
            return (
                f"the decision you were on - {one_line(left.decision.id)} from {role} - is no"
                " longer waiting: it was answered, dismissed or withdrawn"
            )
        case _:
            assert_never(left)


def _command_went(snap: Snapshot, left: CommandOwed, now: float) -> str:
    current = snap.commands.get(left.command.id)
    if current is None:
        return "is no longer on record"
    end = current.end
    if end is None:
        return "is no longer in the queue"
    if isinstance(end.outcome, Withdrawn):
        return "was withdrawn by its asker: nothing ran"
    if isinstance(end.outcome, Refused):
        return "was refused by you: nothing ran"
    return f"has ended: {command_view.row_state(current, now).text}"


def _departed(snap: Snapshot, left: Obligation, now: float) -> None:
    """
    The mark a row leaves where it was when it goes from under the cursor
    (``focus.OnDeparted``): what left, in the frame an open row has, and
    nothing to press.
    """
    imgui.push_text_wrap_pos(0.0)
    imgui.begin_group()
    imgui.indent(10.0)
    imgui.text_colored(P.danger.vec4, departure_line(snap, left, now))
    imgui.text_colored(P.warn.vec4, NOTHING_UNDER_THE_CURSOR)
    imgui.unindent(10.0)
    imgui.end_group()
    imgui.pop_text_wrap_pos()
    low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
    draw = imgui.get_window_draw_list()
    draw.add_rect_filled(low, imgui.ImVec2(low.x + 3.0, high.y), P.focus.u32)
    imgui.spacing()


def _small() -> None:
    imgui.push_font(None, _SMALL_FONT)


def _normal() -> None:
    imgui.pop_font()


def draw(
    snap: Snapshot,
    focus: FocusState,
    state: review.ReviewState,
    compose: ComposeState,
    bridge: Bridge,
    actions: InboxActions,
    now: float,
    wrap: bool = True,
    *,
    decisions: decision_view.DecisionState,
    commands: CommandRows | None = None,
) -> None:
    """
    Build the inbox from one snapshot. Never reads the store.

    ``decisions`` is required. A default would be a fresh one each frame, which
    is an answer box that forgets what was typed into it.

    ``commands`` is None for a caller with nothing to draw a command with, and
    then a command's row says so where its body would be. No default stands in
    for it: a state made here each frame would forget an edit, and a shell
    looked for here would be looked for sixty times a second.
    """
    state.prune({p.id for p in snap.approvals})
    compose.prune(snap)
    if len(_VIEWS) or _PARTS or _PREVIEWS or _WIDTHS or _HIDDEN or _WHOLE:
        live = {(o.key, part) for o in snap.needs_you for part in _VIEW_PARTS}
        _VIEWS.prune(live)
        for stale in _PARTS.keys() - live:
            del _PARTS[stale]
        for stale in _PREVIEWS.keys() - live:
            del _PREVIEWS[stale]
        for stale in _WIDTHS.keys() - live:
            del _WIDTHS[stale]
        for stale in _HIDDEN.keys() - live:
            del _HIDDEN[stale]
        _WHOLE.intersection_update(live)
    # With the other two, ahead of every early return: the frame on which the last
    # decision is answered is one with an empty queue.
    current = focus.obligation(snap)
    decisions.attend(snap, current.decision.id if isinstance(current, DecisionOwed) else None)
    if commands is not None:
        # Once per frame and before any command is drawn, here or in DETAIL:
        # this is what closes a row, and drops an armed press, when the cursor
        # has left it or this pane went a frame without being drawn.
        commands.state.attend(
            snap.commands,
            current.command.id if isinstance(current, CommandOwed) else None,
            int(imgui.get_frame_count()),
        )
        if isinstance(current, CommandOwed) and _open_key(state):
            commands.state.open(current.command.id, float(imgui.get_time()))
    # What left from under the cursor is said in every state the pane has,
    # ahead of the two that return early: an empty queue is where the last row
    # to leave leaves from.
    gone = focus.departed
    where_gone = gone.position(snap) if gone is not None else None
    wall = commands.now if commands is not None else 0.0

    # Before the empty-queue branch, and the ordering is the whole distinction.
    # ``_zero_state`` answers "a fleet exists and owes you nothing"; this answers
    # "there is no fleet". An early return is what makes them mutually exclusive
    # structurally rather than by the two conditions happening to disagree -- an
    # empty node table also implies an empty ``needs_you``, so a later check would
    # be reachable only by luck of ordering.
    #
    # ``nodes`` rather than ``order``, though ``store._preorder`` makes the two
    # equivalent: it re-attaches orphans at the root specifically so nothing in the
    # node table can be missing from the walk, and the lengths are therefore always
    # equal. ``nodes`` is asked anyway because it is the primary fact and ``order``
    # is a projection of it. If that recovery ever regressed, keying on the
    # projection would put a cold-start splash over a live fleet -- silently, and
    # over exactly the orphaned sub-agent that was carrying a pending approval.
    #
    # Note this is *not* the same question as ``_zero_state``'s "is anything live":
    # a fleet of five crashed sessions is not an empty application. Those sessions
    # still need to be seen and dismissed, and they keep the queue's empty state.
    #
    # "No fleet" is no session that ran in this window and is still on it
    # (``ran_here``). Closed cards recalled at startup are in the node table from
    # the first frame, and a session the operator removed stays in it, so the
    # table alone would retire the cold start for every user with a history and
    # ``_zero_state`` would count sessions that never ran. The queue is asked as
    # well because the two are no longer the same fact by construction: whatever
    # is owed is drawn, whoever owes it.
    if not snap.needs_you and not ran_here(snap):
        if gone is not None:
            _departed(snap, gone.left, wall)
        _splash(now)
        return

    if not snap.needs_you:
        if gone is not None:
            _departed(snap, gone.left, wall)
        _zero_state(snap, now)
        return

    imgui.spacing()
    _header(snap, current, now, held=state.hold.held)
    imgui.separator()
    imgui.spacing()

    for index, obligation in enumerate(snap.needs_you):
        # Asked of the cursor again at each row: a click on a row above this
        # one has already moved it, and the mark goes with that click.
        if gone is not None and focus.departed is gone and index == where_gone:
            # Where the row was, ahead of the one that took its position.
            _departed(snap, gone.left, wall)
        _row(
            snap,
            focus,
            state,
            compose,
            bridge,
            actions,
            decisions,
            obligation=obligation,
            expanded=current is not None and obligation.key == current.key,
            now=now,
            wrap=wrap,
            commands=commands,
        )
    if gone is not None and focus.departed is gone and where_gone == len(snap.needs_you):
        _departed(snap, gone.left, wall)

    _batch_controls(snap, focus, state, bridge)


def ran_here(snap: Snapshot) -> tuple[AgentRecord, ...]:
    """
    Every agent that ran in this window and whose session is on the fleet.

    Leaves out a root recalled from its session file (``AgentRecord.recalled``),
    which is a card and not a run, and every record of a session the operator
    took off the fleet (``Snapshot.off_fleet``).
    """
    off = {sid for sid in {nid[0] for nid in snap.nodes} if snap.off_fleet(sid)}
    return tuple(
        rec for nid, rec in snap.nodes.items() if rec.recalled is None and nid[0] not in off
    )


def run_total(snap: Snapshot) -> str:
    """
    The last line of the empty queue: how many agents ran here, and what this
    process has spent.

    **The two halves count different sets, and the line says so when they
    differ.** The count is ``ran_here``: what the operator can see. The spend is
    every record's, a removed session's included, because it was spent in this
    process and a total that fell when a card was removed would be a running
    cost that goes down. A recalled record adds nothing to either: its usage is
    zero and its file's estimate is not this run's (``model.Recalled``).
    """
    shown = ran_here(snap)
    removed = sum(1 for rec in snap.nodes.values() if rec.recalled is None) - len(shown)
    spend = sum(rec.usage.total_cost_usd for rec in snap.nodes.values())
    extra = f", {removed} more removed from the fleet" if removed else ""
    return f"{len(shown)} agents this run{extra} · ${spend:,.2f}"


def _header(snap: Snapshot, current: Obligation | None, now: float, *, held: bool) -> None:
    """
    How many, how long, and what the keyboard does on the open row.

    The third is a sentence, and the pane is as narrow as the operator docks it.
    Where it fits it rides the count's line. Where it does not it takes the lines
    under it, wrapped at the pane's edge: it is the only thing on screen that says
    why a key did nothing, and a window clips what runs past it without a mark.
    """
    oldest = format_elapsed(now - min(o.since for o in snap.needs_you))
    imgui.text_colored(P.state_awaiting.vec4, f"{len(snap.needs_you)} need you")
    imgui.same_line()
    imgui.text_disabled(f"· oldest {oldest}")

    hint = KEYS_HELD if held else header_hint(current)
    imgui.same_line()
    if imgui.calc_text_size(f"· {hint}").x <= imgui.get_content_region_avail().x:
        hint = f"· {hint}"
    else:
        # Ends the count's line where it stands, at the height it already has.
        imgui.new_line()
    imgui.push_text_wrap_pos(0.0)
    if held:
        imgui.text_colored(P.warn.vec4, hint)
    else:
        imgui.text_disabled(hint)
    imgui.pop_text_wrap_pos()


# What the header says while ``review.Hold`` has the shortcuts off. It is the only
# thing on screen that explains a dead ``a``, so it names both ways out.
KEYS_HELD = "keys are off: you were typing in a box · esc or a click turns them back on"


# The one key that is not about the row, said after whatever the row's own keys
# are: it opens the menu of the card the cursor's session has in FLEET
# (``app.CARD_MENU_KEY``, which a test holds this letter to).
MENU_KEY_HINT = "m opens the session's controls"


def keys_hint(current: Obligation | None) -> str:
    """
    What the keyboard does on the row under the cursor.

    Per kind, because the header sits above whichever row is open: "a approve"
    over a decision says a key will answer it, and no key does. Closed by
    ``assert_never`` so a sixth kind is asked what it accepts.
    """
    match current:
        case None:
            return "j/k move"
        case ApprovalNeeded():
            return "j/k move · a approve · r reject · e edit"
        case QuestionPending():
            return "j/k move · reply in the box: Ctrl+Enter sends · no other key acts here"
        case SessionFailed():
            return "j/k move · retry or dismiss with the buttons: no key acts here"
        case DecisionOwed():
            return (
                "j/k move · answer in the box · send and dismiss are buttons,"
                " two presses each: no key acts here"
            )
        case CommandOwed():
            return COMMAND_KEYS
        case _:
            assert_never(current)


# Over a command's row. One key opens it and none does more: no key runs,
# refuses, edits or cancels a command. Each of those is a button that answers a
# mouse release and nothing else, and run and cancel take two.
COMMAND_KEYS = (
    "j/k move · o or a click on the row opens it · no key acts on a command: its"
    " buttons take the mouse, and run and cancel take two presses"
)

# Opens the command row the cursor is on. A bare letter, as every key that acts
# on what the cursor is on is, and one that ``review.SHORTCUTS``, the card
# menu and the layout keys do not use. Not Enter: that is a layout key, and it
# is what keyboard navigation presses a focused button with.
OPEN_KEY = imgui.Key.o


def _open_key(state: review.ReviewState) -> bool:
    """
    Whether this frame carries the operator's ``OPEN_KEY`` as a shortcut and
    not as prose: ``review.Hold`` let the frame's keys through, no widget has
    the keyboard, no popup is up to take it, and no modifier is down.

    ``Hold.free`` is this frame's answer: the application steps the hold
    before any pane draws. A popup is asked about here because the hold is not
    stepped while a card's menu is open, and ``free`` is then an older frame's.
    """
    io = imgui.get_io()
    if not state.hold.free or io.want_capture_keyboard or io.want_text_input:
        return False
    if io.key_ctrl or io.key_alt or io.key_super:
        return False
    anywhere = int(imgui.PopupFlags_.any_popup_id) | int(imgui.PopupFlags_.any_popup_level)
    if imgui.is_popup_open("", anywhere):
        return False
    return bool(imgui.is_key_pressed(OPEN_KEY, False))


def header_hint(current: Obligation | None) -> str:
    """
    The header's sentence while the keys are on: what they do on the open row
    (``keys_hint``), and then the key that opens the session's controls, which
    is the same on every row and is no row's own.
    """
    return f"{keys_hint(current)} · {MENU_KEY_HINT}"


def identity(snap: Snapshot, obligation: Obligation) -> tuple[str, str]:
    """
    The two strings that say whose obligation this is.

    Title identifies, qualifier disambiguates. A sub-agent's parked call belongs to
    the sub-agent but is only findable via the session that spawned it, so the
    session's task is the headline and the sub-agent's name rides alongside it.

    Public because DETAIL heads its pane with the same two strings. One rule, in the
    pane that owns the queue -- two panes naming the same obligation differently is
    a small version of the defect the single cursor exists to prevent.
    """
    session: NodeId = (obligation.node[0], None)
    root = snap.nodes.get(session)
    node = snap.nodes.get(obligation.node)
    # Both are drawn, and both come from fields held as they arrived (a task and a
    # directory are relaunched with): made drawable here, once, for every caller.
    title = author_text(root.task if root else "") or "session"
    project = author_text(projects.project_key(root.cwd if root else None))
    if obligation.node == session:
        return title, project
    sub = (node.agent_type if node else None) or "sub-agent"
    return title, f"{project} / {sub}"


def spawn_marker(snap: Snapshot, obligation: Obligation) -> str | None:
    """
    Where a pending spawn sits in its session's fleet, and how much of that fleet is
    running. ``None`` for every obligation that is not a spawn.

    The row this qualifies reads ``spawn builder: <description>``, which is a
    reasonable thing to say yes to twelve times in a row while nobody ever decides
    to run twelve. ``approval.summarize`` cannot supply the number -- it is pure,
    and is handed a tool name and its arguments and nothing else -- so the shell
    renders it from the snapshot it already holds.

    **The ordinal.** ``len(fleet) + (spawns queued ahead of this one) + 1``, where
    the fleet is every descendant record of the session and "ahead" is position in
    ``snap.approvals``, which is ordered by how long each has waited. The second
    term is what makes the number honest while the board is still being built: an
    approval nobody has answered has no ``AgentRecord`` yet, so a lead that issues
    three ``Agent`` calls in one breath parks three rows that the record count alone
    would call the first sub-agent three times.

    **The live count.** Fleet members that are not terminal. A queued spawn is not
    running -- it is what the operator is being asked about -- so the two numbers
    move independently, and a session whose sub-agents have all finished reads
    ``4th sub-agent · 0 running``. Terminality is the whole predicate here because
    the question is "is it still working": ``_zero_state`` below spells FAILED out
    separately for a different question, and ``rail._density`` deliberately files a
    crashed agent as blocked rather than ended because a crash is an obligation.

    Scoped to ``obligation.node[0]``. The store holds every session at once, so a
    fleet-wide count would tell the operator about work they are not being asked to
    consent to.
    """
    if not isinstance(obligation, ApprovalNeeded):
        return None
    pending = obligation.approval
    if pending.tool_name not in _SPAWN_TOOLS:
        return None

    session = pending.node[0]
    fleet = snap.subagents_of((session, None))
    queued = [p.id for p in snap.approvals if p.tool_name in _SPAWN_TOOLS and p.node[0] == session]
    ahead = queued.index(pending.id) if pending.id in queued else 0
    running = sum(1 for rec in fleet if not rec.state.is_terminal)
    return f"{_ordinal(len(fleet) + ahead + 1)} sub-agent · {running} running"


def _ordinal(n: int) -> str:
    # 11th, 12th and 13th are the exceptions the last digit gets wrong.
    suffix = "th" if n % 100 in (11, 12, 13) else _SUFFIX.get(n % 10, "th")
    return f"{n}{suffix}"


def _row(
    snap: Snapshot,
    focus: FocusState,
    state: review.ReviewState,
    compose: ComposeState,
    bridge: Bridge,
    actions: InboxActions,
    decisions: decision_view.DecisionState,
    *,
    obligation: Obligation,
    expanded: bool,
    now: float,
    wrap: bool = True,
    commands: CommandRows | None = None,
) -> None:
    draw = imgui.get_window_draw_list()
    line_h = imgui.get_text_line_height_with_spacing()
    colour = P.obligation(obligation.kind)

    imgui.push_id(obligation.key)
    origin = imgui.get_cursor_screen_pos()
    width = imgui.get_content_region_avail().x
    imgui.invisible_button("##row", imgui.ImVec2(max(width, 40.0), line_h + 6.0))
    hovered = imgui.is_item_hovered()
    if imgui.is_item_clicked():
        focus.to_obligation(obligation)
        if isinstance(obligation, CommandOwed) and commands is not None:
            # The click is on this row, so it is this command that opens.
            commands.state.open(obligation.command.id, float(imgui.get_time()))

    lo = imgui.ImVec2(origin.x, origin.y)
    hi = imgui.ImVec2(origin.x + width, origin.y + line_h + 6.0)
    if expanded or hovered:
        draw.add_rect_filled(lo, hi, P.panel_alt.u32)
    if expanded:
        draw.add_rect(lo, hi, P.focus.u32, 0.0, 2.0)
        draw.add_rect_filled(lo, imgui.ImVec2(lo.x + 3.0, hi.y), P.focus.u32)

    title, qualifier = identity(snap, obligation)

    imgui.set_cursor_screen_pos(imgui.ImVec2(origin.x + _X_GLYPH, origin.y + 3.0))
    imgui.text_colored(colour.vec4, OBLIGATION_GLYPH[obligation.kind])

    imgui.set_cursor_screen_pos(imgui.ImVec2(origin.x + _X_TITLE, origin.y + 3.0))
    imgui.text_colored(P.text_strong.vec4, ellipsis(title, _TITLE_W))

    _small()
    imgui.set_cursor_screen_pos(imgui.ImVec2(origin.x + _X_QUALIFIER, origin.y + 5.0))
    imgui.text_colored(P.text_dim.vec4, ellipsis(qualifier, _QUALIFIER_W))
    _normal()

    # The only number here that gets worse on its own, and the queue's sort key.
    wait = format_elapsed(now - obligation.since)
    imgui.set_cursor_screen_pos(
        imgui.ImVec2(origin.x + _X_WAIT_END - imgui.calc_text_size(wait).x, origin.y + 3.0)
    )
    imgui.text_colored(colour.vec4, wait)

    # The marker goes in the summary zone, ahead of the summary, for two reasons.
    # It is the only zone whose width is "whatever the window has left" rather than
    # a fixed slot, so a string that varies in length can live in it without moving
    # any other field along (see the column comment above). And it is the column
    # being scanned, which is where a running total has to be if it is going to
    # change the decision the row is asking for. Accent rather than warn: it is a
    # live value, not an alarm, and the threshold at which twelve becomes too many
    # is the operator's to hold, not this pane's.
    summary_x = _X_SUMMARY
    marker = spawn_marker(snap, obligation)
    if marker is not None:
        imgui.set_cursor_screen_pos(imgui.ImVec2(origin.x + summary_x, origin.y + 3.0))
        imgui.text_colored(P.accent.vec4, marker)
        summary_x += imgui.calc_text_size(marker).x + 10.0

    imgui.set_cursor_screen_pos(imgui.ImVec2(origin.x + summary_x, origin.y + 3.0))
    imgui.text_colored(
        P.text.vec4, ellipsis(row_summary(obligation), max(width - summary_x - 12.0, 40.0))
    )

    imgui.set_cursor_screen_pos(imgui.ImVec2(origin.x, hi.y))

    if expanded:
        imgui.indent(10.0)
        match obligation:
            case ApprovalNeeded():
                _expand_approval(state, bridge, obligation, wrap=wrap)
            case QuestionPending():
                _expand_question(snap, compose, actions, obligation, wrap=wrap)
            case SessionFailed():
                _expand_failure(snap, actions, obligation)
            case DecisionOwed():
                decision_view.draw_row(snap, decisions, bridge, obligation, wrap=wrap)
            case CommandOwed():
                draw_command(snap, commands, obligation.command, wrap=wrap)
            case _:
                assert_never(obligation)
        imgui.unindent(10.0)
        imgui.spacing()

    imgui.pop_id()


def _expand_approval(
    state: review.ReviewState,
    bridge: Bridge,
    obligation: ApprovalNeeded,
    *,
    wrap: bool = True,
) -> None:
    """
    The diff and the three decisions, at the point in the queue where the call sits.

    This is what removes the second pane: the row *is* the detail, so there is no
    cursor in a diff view that can be pointed at a different agent than the queue's.
    """
    pending = obligation.approval
    # Lines of the body this frame did not put on screen: none, unless the
    # typesetting library stopped under it (``ViewCache.unbuilt``) or the body is
    # past the bound and its end has not been asked for (``_unset_tail``).
    unset = 0

    if state.editing == pending.id:
        # The draft, in ImGui's own input and all of it: ``unset`` stays zero.
        _editor(state, pending, wrap=wrap)
    else:
        # Both above the body under either renderer: the native one is also what is
        # drawn when the faces did not load.
        if edit_withheld(state, pending):
            _warning(_WITHHELD)
        # The gate predicts this from its own read of the file at park time; the
        # tool is what decides, on the file as it is when the call runs.
        #
        if pending.shown_args is not None:
            _warning(review.ESCAPED)
        for reason in refusals(pending):
            _warning(f"may not apply: {reason}")
        for said in shown_as_new(pending.texts):
            _warning(said)
        if typeset.enabled(typeset.Pane.INBOX):
            unset = _typeset_body(state, pending, obligation.key)
        else:
            _native_body(state, pending, obligation.key)
    # What every approve path is answered from (``review.withheld``), the keys'
    # on the next frame and this row's own button on this one.
    state.body_shown(review.INBOX, pending.id, unset, int(imgui.get_frame_count()))

    imgui.spacing()
    if state.edit_error:
        imgui.text_colored(P.danger.vec4, state.edit_error)
    not_sent = review.refusal_for(state, pending.id)
    if not_sent is not None:
        _warning(not_sent)

    if state.focus_reason:
        imgui.set_keyboard_focus_here()
        state.focus_reason = False
    imgui.set_next_item_width(360.0)
    changed, reason = imgui.input_text_with_hint(
        "##reason", "reason (shown to the agent on reject)", state.reasons.get(pending.id, "")
    )
    if changed:
        state.reasons[pending.id] = reason

    imgui.same_line()
    # Not clickable while this row's body is not on screen, which is the button
    # showing what ``review.resolve`` would answer; the decision is made there,
    # for the click as for the ``a`` key.
    imgui.begin_disabled(unset > 0)
    if imgui.button("approve"):
        review.resolve(bridge, state, pending, approved=True)
    imgui.end_disabled()
    imgui.same_line()
    if imgui.button("reject"):
        review.resolve(bridge, state, pending, approved=False)
    imgui.same_line()
    if state.editing == pending.id:
        if imgui.button("cancel edit"):
            state.editing = None
            state.edits.pop(pending.id, None)
            state.edit_error = None
            # This frame's reading was of the draft in its editor. With the draft
            # gone approve sends the agent's call, whose body this row has not
            # drawn since: there is no reading of that until the next frame.
            state.bodies.pop(pending.id, None)
    elif imgui.button("edit"):
        state.editing = pending.id
        state.edits.setdefault(pending.id, review.pretty_args(pending))
    imgui.same_line()
    _small()
    imgui.text_colored(P.text_dim.vec4, "a / r / e")
    _normal()
    if unset > 0:
        _warning(_NOT_ON_SCREEN)


def _warning(text: str) -> None:
    """
    One line the approver must not miss, wrapped to the pane and not clipped by it.
    """
    imgui.push_text_wrap_pos(0.0)
    imgui.text_colored(P.warn.vec4, text)
    imgui.pop_text_wrap_pos()


def _native_body(state: review.ReviewState, pending: PendingApproval, key: str) -> None:
    """
    What the call would do, in ImGui's own text: its unified diff a line to a row,
    or its arguments. Over the rows, what they hold that cannot be seen
    (``hidden_in_rows``).
    """
    lines = _diff_lines(state, pending)
    if lines:
        view = (key, "diff")
        hidden = hidden_in_rows(view, pending)
        if hidden is not None:
            _warning(hidden)
        _diff_rows(lines, view)
    else:
        _native_arguments(pending)


def hidden_in_rows(view: Hashable, pending: PendingApproval) -> str | None:
    """
    The sentence a pane says over a diff it draws with its own rows, having not
    asked ``typeset.diff`` for it: that the diff holds invisible, bidirectional
    or control characters, and which (``typeset.diff_own_rows_warning``).
    ``None`` for a diff that holds none. For this row and for DETAIL.

    ImGui draws a bidirectional override, a byte-order mark and a C1 control
    with the glyph it draws for a letter its font lacks, and a zero-width space
    as nothing, so the rows alone do not show that one is there.

    **Said only where ``typeset.diff`` was not called for the diff in this
    frame**: handed a diff it gives back to its pane, that says the same
    sentence itself, and a pane saying it as well would say it twice.

    **Asked of the string the rows are made from, which is the store's
    drawable one, and not of the arguments as the agent sent them.** A lone
    surrogate or a NUL is in these rows as ``\\uXXXX``, six characters of
    ASCII that an approver reads, so it is not among what cannot be seen and
    this names neither; the line over a call holding one is the pane's
    ``review.ESCAPED``, which is said where the arguments hold it.

    Read from the unified string once for as long as the record holds that
    string, and not once a frame.
    """
    held = _HIDDEN.get(view)
    if held is None or held[0] is not pending.diff:
        held = _HIDDEN[view] = (pending.diff, typeset.diff_own_rows_warning(pending))
    return held[1]


def _diff_lines(state: review.ReviewState, pending: PendingApproval) -> list[str]:
    """
    The call's unified diff as lines, split once per pending item and not once
    per frame; empty for a call with nothing diff-shaped.

    A whole-file Write produces a diff as long as the file, and re-splitting it
    sixty times a second is work proportional to the change being reviewed --
    backwards, since the large diffs are the ones stared at longest.
    """
    lines = state.diff_lines.get(pending.id)
    if lines is None:
        lines = (pending.diff.splitlines() or [""]) if pending.diff else []
        state.diff_lines[pending.id] = lines
    return lines


def _widest(view: Hashable, lines: Sequence[str]) -> float:
    """
    The width of the widest of ``lines`` in the current font, measured again only
    when ``lines`` is another object: ``_diff_lines`` hands back the same list
    for as long as the call is in the queue.
    """
    held = _WIDTHS.get(view)
    if held is None or held[0] is not lines:
        wide = max((imgui.calc_text_size(line).x for line in lines), default=0.0)
        held = _WIDTHS[view] = (lines, wide)
    return held[1]


def _diff_rows(lines: Sequence[str], view: Hashable) -> None:
    """
    A unified diff a line to a row in ImGui's own text, in a child that fits it
    up to ``_BODY_LINES``. **This pane's own renderer of a diff**: drawn with
    typeset text off, and with it on wherever the library's diff view is not
    (``_typeset_body``).

    A short diff that still needs scrolling is the thing that makes an operator
    stop reading them, so the child is no taller than its lines.

    **A row is not broken, and the child says when one runs past its edge.** The
    wheel scrolls a child sideways whether or not it has a scrollbar, so the end
    of a long row is reachable either way; the scrollbar is what shows there is
    an end to reach, on the row whose approve releases it. It is drawn only when
    a row is wider than the child, and the child is then taller by the bar, so
    that the bar does not cover a line of a diff that otherwise fits.
    """
    line_h = imgui.get_text_line_height_with_spacing()
    style = imgui.get_style()
    inner = imgui.get_content_region_avail().x - 8.0 - 2.0 * style.window_padding.x
    bar = style.scrollbar_size if _widest(view, lines) > inner else 0.0
    body_h = min(_BODY_LINES * line_h, line_h * len(lines) + 10.0 + bar)
    if imgui.begin_child(
        "##body",
        imgui.ImVec2(-8.0, body_h),
        imgui.ChildFlags_.borders,
        imgui.WindowFlags_.horizontal_scrollbar,
    ):
        colours = {
            "add": P.diff_add,
            "remove": P.diff_remove,
            "meta": P.accent,
            "context": P.diff_context,
        }
        clipper = imgui.ListClipper()
        clipper.begin(len(lines))
        while clipper.step():
            for index in range(clipper.display_start, clipper.display_end):
                line = lines[index]
                # The +/- gutter stays in the text regardless of colour: the
                # non-hue channel is what keeps a diff readable in high
                # contrast and to a colour-deficient operator (6.1).
                imgui.text_colored(colours[diff_line_kind(line)].vec4, line or " ")
        clipper.end()
    imgui.end_child()


def argument_text(value: object) -> str:
    """
    One argument as the approver reads it: a string is itself, never its ``repr``.

    A ``repr`` escapes a command's quotes and folds its line breaks into ``\\n``, so
    what is read is not what would run. Anything that is not a string is its
    ``repr``, and so is a string with nothing visible in it, which would otherwise
    be drawn as nothing at all.
    """
    return value if isinstance(value, str) and value.strip() else repr(value)


def _native_arguments(pending: PendingApproval) -> None:
    """
    The arguments of a call with nothing diff-shaped to show, each wrapped to the
    pane and all of it reachable: the block is as tall as its text up to the body
    cap and scrolls past it.

    No diff is a real answer, not a gap: a Bash command has nothing diff-shaped to
    show, and inventing one would be worse than the args.
    """
    style = imgui.get_style()
    shown = argument_rows(pending.tool_name, pending.args, review.shown_command(pending))
    # Measured at the width the text will have, so that the block is sized by the
    # lines wrapping makes. The scrollbar's width is left out of the measure: it is
    # there only when the text is already taller than the cap.
    inner = max(imgui.get_content_region_avail().x - 8.0 - 2.0 * style.window_padding.x, 40.0)
    tall = imgui.get_text_line_height() + sum(
        style.item_spacing.y + imgui.calc_text_size(text, None, False, inner).y for text in shown
    )
    cap = _BODY_LINES * imgui.get_text_line_height_with_spacing()
    body_h = min(cap, tall + 2.0 * style.window_padding.y)
    if imgui.begin_child("##body", imgui.ImVec2(-8.0, body_h), imgui.ChildFlags_.borders):
        imgui.text_colored(P.text_dim.vec4, _NO_DIFF)
        imgui.push_text_wrap_pos(0.0)
        for text in shown:
            imgui.text_colored(P.text.vec4, text)
        imgui.pop_text_wrap_pos()
    imgui.end_child()


def argument_parts(
    raw_args: Mapping[str, object], tool_name: str | None = None, command: str | None = None
) -> tuple[typeset.Part, ...]:
    """
    A call's arguments as one document: each name dim and tight over its value,
    which is set whitespace-exact and broken to the window at any column. A
    Bash command is ``command`` where it is given (``review.shown_command``)
    and otherwise ``approval.command_shown`` of the value, as on every approval
    surface: broken at the edge, a break the command makes would otherwise be
    one the window made.

    That form for every value and not only a command: an argument of a call this
    pane has no reading for is approved as it is, so none of it may be collapsed
    and none of it may run past the edge of a window that does not scroll sideways.

    **Not the form the seam gives the role** (``typeset.multiline_kind``), under
    which DETAIL sets a value of several lines unbroken and scrolls to it. This is
    the row the approve button is on, twenty-four lines tall: a command set
    unbroken here ends at the row's edge with a scrollbar at the foot of a capped
    child, which may itself be scrolled out of sight, and the words past the edge
    are then approved unread. Broken at the edge, every word is in the body the
    operator already scrolls down.
    """
    role = typeset.Role.ARGUMENT
    parts: list[typeset.Part] = []
    for name, value in raw_args.items():
        parts.append(
            typeset.Part(name or '""', typeset.Kind.LITERAL, "text_dim", tight=True, role=role)
        )
        shown = _command_text(tool_name, name, value, command)
        text = argument_text(value) if shown is None else shown
        parts.append(typeset.Part(text, typeset.Kind.WRAPPED_EXACT, role=role))
    return tuple(parts)


def _parts(
    view: Hashable, source: object, make: Callable[[], tuple[typeset.Part, ...]]
) -> tuple[typeset.Part, ...]:
    """
    The parts of ``view``, made again only when ``source`` is another object.

    Identity is the test because a parked call is a frozen record: the same
    arguments are the same objects for as long as the call is in the queue.
    """
    held = _PARTS.get(view)
    if held is None or held[0] is not source:
        held = _PARTS[view] = (source, make())
    return held[1]


@dataclass(frozen=True, slots=True)
class Description:
    """
    A parked call read as a short header and a piece of prose.

    ``fields`` are ``(argument name, value as shown)``. ``body`` is the prose, or
    ``""`` when the call carries none.
    """

    fields: tuple[tuple[str, str], ...]
    body: str


def describe(pending: PendingApproval) -> Description | None:
    """
    The header fields and the prose of a call whose substance is prose
    (``DESCRIBED``): a concern, a task declaration, a spawn, a plan. ``None`` for
    any other call.

    **Every argument lands somewhere.** The ones ``DESCRIBED`` names come first, in
    its order; the rest follow in the call's own. An argument this pane has never
    heard of is still part of what an approval releases, and a header that listed
    only the expected names would be a place to put one where the approver cannot
    see it. For the same reason the prose argument is the body only when it is a
    non-blank string: anything else is shown among the fields as its ``repr``.

    Pure, and cheap enough per frame: it copies a handful of references and joins
    two short lists. The prose itself is not copied.
    """
    shape = DESCRIBED.get(pending.tool_name)
    if shape is None:
        return None
    prose, leading = shape
    args = pending.args
    raw = args.get(prose)
    body = raw if isinstance(raw, str) and raw.strip() else ""
    names = [name for name in leading if name in args]
    names += [name for name in args if name not in leading and (name != prose or not body)]
    return Description(tuple((name, _field_text(name, args[name])) for name in names), body)


def prose_argument(tool_name: str) -> str | None:
    """
    The argument of this tool that is prose by the tool's contract, if it has one.
    """
    shape = DESCRIBED.get(tool_name)
    return None if shape is None else shape[0]


def _field_text(name: str, value: object) -> str:
    """
    A string as itself and a list of names joined, each only where the bus declares
    that type; anything else as its ``repr``, so a value of a type its argument
    should not have looks like what it is rather than like the value it resembles.
    """
    if name in _LIST_FIELDS:
        if isinstance(value, list | tuple) and value:
            items = [item for item in value if isinstance(item, str)]
            if len(items) == len(value):
                return ", ".join(items)
        return repr(value)
    return value if isinstance(value, str) else repr(value)


def edit_withheld(state: review.ReviewState, pending: PendingApproval) -> bool:
    """
    Whether the operator holds an edit of these arguments that the row is not showing.

    The body under a closed editor is drawn from the call as the agent made it,
    while ``review.resolve`` sends the draft. They differ whenever a draft outlives
    its editor -- ``e`` on another row moves the editor and keeps this one's text --
    and then ``a`` approves something other than the diff above it.

    Serialises the arguments to compare, which for a whole-file Write is the file;
    only reached while a draft exists.
    """
    draft = state.edits.get(pending.id)
    if draft is None or state.editing == pending.id:
        return False
    return draft != review.pretty_args(pending)


def refusals(pending: PendingApproval) -> tuple[str, ...]:
    """
    Each distinct reason the gate's read gives for this call's edit having no place
    to land, in the order the parts give them.
    """
    reasons = (text.will_not_apply for text in pending.texts or ())
    return tuple(dict.fromkeys(reason for reason in reasons if reason))


def shown_as_new(texts: Sequence[DiffText] | None) -> tuple[str, ...]:
    """
    One line for each file a call would replace whose content could not be read
    as text -- a binary, a directory, no permission -- and whose diff is
    therefore every line added, exactly as for a file being created.

    Said by the row and by DETAIL above the diff, whoever draws it: the unified
    string cannot tell the two apart, and the library's file header, which
    can, is cut at its view's edge. An Edit of such a file is already said, as
    a reason it may not apply.
    """
    return tuple(
        f"{part.label or '(no path)'}: this file exists and is not readable as text - "
        "the diff shows it as new"
        for part in texts or ()
        if part.unreadable and part.will_not_apply is None
    )


def _typeset_body(state: review.ReviewState, pending: PendingApproval, key: str) -> int:
    """
    What the call would do, with its prose set through the seam: its diff, or its
    description, or failing both its arguments. Answers how many lines of it
    this frame did not put on screen, which holds the row's approve button:
    what the library stopped short of (``ViewCache.unbuilt``), and the end of a
    body longer than one frame should set unasked (``_unset_tail``).

    **The diff is the library's diff view where the library that is running
    keeps a column** (``library_diff``: a measurement, this call's texts, and
    a bound on what one frame is asked to make), no taller than ``_BODY_LINES``
    of its own rows, scrolling inside itself past that and sideways to a long
    row by its own bar. **Otherwise, and whenever that view is not what draws
    this diff** (``typeset.diff`` answers ``None``, having said why), **it is
    this pane's own rows** (``_diff_rows``), as it is without the library.
    A diff the library stopped under, or set less of than it was handed, is
    counted as not on screen, like a body.
    **Arguments are literal text**, which the seam hands to ImGui. **A
    description's prose is set by the library**: a concern's body, a task's
    detail, a spawn's prompt, a plan.

    **No child of this pane's own around a document.** A capped one opens its
    own when it is taller than the cap, and a second scrolling child around
    that would be two wheels and two scrollbars for one body. That child
    scrolls sideways as well as down: a word the library cannot break runs past
    its edge, and the seam tells the child how far.

    **The gate's prediction is said by the row**, above this body, under either
    renderer.
    """
    line_h = imgui.get_text_line_height_with_spacing()
    wide = imgui.get_content_region_avail().x - 8.0
    cap = _BODY_LINES * line_h

    lines = _diff_lines(state, pending)
    if lines:
        view = (key, "diff")
        held = library_diff(state, pending)
        if held is not None:
            # The prediction is the row's to say, above: the view's copy would
            # be in a header row, which is cut at the row's edge.
            shown = typeset.diff(
                _VIEWS,
                view,
                held,
                height_cap=_BODY_LINES * typeset.diff_row_height(),
                width=wide,
                warning=False,
            )
            if shown is not None:
                return _VIEWS.unbuilt(view) or 0
        else:
            # ``typeset.diff`` says this itself over a diff it hands back.
            hidden = hidden_in_rows(view, pending)
            if hidden is not None:
                _warning(hidden)
        _diff_rows(lines, view)
        return 0
    described = describe(pending)
    if described is None:
        imgui.text_colored(P.text_dim.vec4, _NO_DIFF)
        raw = pending.args
        typeset.document(
            _VIEWS,
            (key, "arguments"),
            _parts(
                (key, "arguments"),
                raw,
                lambda: argument_parts(raw, pending.tool_name, review.shown_command(pending)),
            ),
            width=wide,
            height_cap=cap - line_h,
        )
        return _VIEWS.unbuilt((key, "arguments")) or 0

    # The fields are text on the pane's own ground, with no band of their own to
    # stand off the row's focus rectangle the way a diff's file header does.
    imgui.spacing()
    top = imgui.get_cursor_pos_y()
    left = imgui.get_cursor_pos_x()
    # In the chrome's font, names and values both: a native line has one font
    # and no fallback, and the proportional face has no arrow, tick or box
    # rule, so a subject set in it shows a replacement glyph where the agent
    # wrote one. These are words being approved.
    for name, value in described.fields:
        imgui.text_colored(P.text_dim.vec4, name)
        imgui.same_line(left + _X_FIELD_VALUE)
        # Wrapped, not ellipsized: a subject is short enough that this is one line,
        # and when it is not, the rest of it is still something being approved.
        imgui.push_text_wrap_pos(left + wide)
        imgui.text_colored(P.text_strong.vec4, value or '""')
        imgui.pop_text_wrap_pos()
    body = described.body
    if body:
        # The fields come out of the cap, down to a floor: a call with many
        # arguments still gets a readable window onto its prose.
        used = imgui.get_cursor_pos_y() - top
        room = max(cap - used, 8.0 * line_h)
        # ``prose_parts`` and not markdown outright: the seam's one rule for what a
        # body somebody wrote is read as, so this body reads here as it does in
        # DETAIL, on the board and in the brief.
        view = (key, "body")
        more = _unset_tail(view, body)
        typeset.document(
            _VIEWS,
            view,
            _parts(view, body, lambda: typeset.prose_parts(_head(body) if more else body)),
            width=wide,
            height_cap=room,
        )
        if more:
            _offer_whole(view, more)
        return (_VIEWS.unbuilt(view) or 0) + more
    return 0


def library_diff(state: review.ReviewState, pending: PendingApproval) -> typeset.ParkedDiff | None:
    """
    What a pane hands ``typeset.diff`` for this call, or ``None`` where the
    pane draws the call's diff with its own rows. For this row and for DETAIL,
    which ask the same thing of the same call.

    ``None`` where the library's diff view is not what draws a diff
    (``typeset.diff_adopted``, ``typeset.diff_draws``), and **where making the
    library's model would stall the frame that asked for it**: the model is
    ``difflib`` over the lines the call changes, a second and a half for
    changes scattered through 20,000 lines (``typeset.diffs`` has the
    measurement), and the pane's own rows are the whole diff already, made
    when the call parked. Past ``review.SETTLED_LINES`` of such lines the pane
    says so in one line, draws its rows, and offers the library's view; the
    answer is the call's and not the pane's, so asking once is asking for
    both. Nothing is held on it: every row is on screen either way.

    One ``ParkedDiff`` a call, kept in ``state`` until the call leaves the
    queue: both panes draw the model it makes, each in a view of its own.
    """
    if not (typeset.diff_adopted() and typeset.diff_draws(pending)):
        return None
    held = state.diffs.get(pending.id)
    if not isinstance(held, typeset.ParkedDiff):
        held = state.diffs[pending.id] = typeset.ParkedDiff(pending.texts or ())
    if held.work > review.SETTLED_LINES and pending.id not in state.diff_whole:
        _warning(
            f"this diff is the pane's own rows: the library's view of it would match "
            f"{held.work:,} changed lines in one frame"
        )
        if imgui.small_button("use the library's view"):
            state.diff_whole.add(pending.id)
        return None
    return held


def _head(body: str) -> str:
    """
    The start of a body, as far as the bound both panes hold a text to unasked.
    """
    return review.head_lines(body, review.SETTLED_LINES)[0]


def _unset_tail(view: Hashable, body: str) -> int:
    """
    How many lines at the end of ``body`` this row leaves unset: none under
    ``review.SETTLED_LINES``, and none once the operator has asked for all of it.

    A text is read and laid out whole in the frame that first draws it, so a body
    past the bound would stall the queue on the frame the cursor reached its row,
    unasked. Counted a line the way ``review.head_lines`` splits one.
    """
    if view in _WHOLE:
        return 0
    return max(body.count("\n") + 1 - review.SETTLED_LINES, 0)


def _offer_whole(view: Hashable, more: int) -> None:
    """
    Under a body set as far as the bound: how much is not, and the button that
    sets it.

    **The rest is set here and not left to DETAIL**, which holds its own view of
    the call to the same bound and is asked separately. The lines not set are
    reported as not on screen (``_typeset_body``), which holds the approve until
    they are asked for.
    """
    total = review.SETTLED_LINES + more
    _warning(
        f"… {more:,} more lines not set: all {total:,} would be read and laid out in one frame"
    )
    if imgui.small_button(f"set all {total:,} lines"):
        _WHOLE.add(view)
        _PARTS.pop(view, None)


def _command_text(
    tool_name: str | None, key: str, value: object, command: str | None
) -> str | None:
    """
    An argument as ``approval.command_shown`` draws it, or ``None`` for one that
    is not a Bash command: ``command`` where it is given, which is of the
    command as sent (``review.shown_command``), and otherwise of ``value``.
    """
    if tool_name is None:
        return None
    if key == "command" and command is not None:
        return command
    return command_shown(tool_name, key, value)


def argument_rows(tool_name: str, args: Mapping[str, Any], command: str | None = None) -> list[str]:
    """
    The arguments of a call with no diff, as the items ``_native_arguments``
    draws, each wrapped to the pane.

    A Bash command is drawn as ``approval.command_shown`` spells it, a row for
    every line of it under its key, each line but the last ending in the
    newline marker: in a body that wraps, a break the command makes and one the
    pane makes are otherwise the same thing on screen, and a second command
    reads as the tail of the first.

    Every other value is one item, as ``argument_text`` has it.
    """
    rows: list[str] = []
    for key, value in args.items():
        shown = _command_text(tool_name, key, value, command)
        if shown is None:
            rows.append(f"  {key} = {argument_text(value)}")
            continue
        rows.append(f"  {key} =")
        rows += [f"    {line}" for line in shown.split("\n")]
    return rows


def _editor(state: review.ReviewState, pending: object, *, wrap: bool = True) -> None:
    pid = pending.id  # type: ignore[attr-defined]
    imgui.text_colored(P.focus.vec4, "editing arguments - approve runs the corrected call")
    changed, text = multiline_input(
        "##edit",
        state.edits.get(pid, ""),
        imgui.ImVec2(-8.0, 140.0),
        wrap=wrap,
        flags=int(imgui.InputTextFlags_.allow_tab_input),
    )
    if changed:
        state.edits[pid] = text
        state.edit_error = None


def _expand_question(
    snap: Snapshot,
    compose: ComposeState,
    actions: InboxActions,
    obligation: QuestionPending,
    *,
    wrap: bool = True,
) -> None:
    """
    A composer, and a way out.

    ``close`` sits here beside ``send`` because replying is not the only resolution
    and often not the likely one. A batch of parallel research sessions all end
    their turn at once, and the dominant action on each is to read it and close it
    -- and until the session is closed its pool slot is not freed, so the next
    queued session never starts. An inbox offering only a reply box would mean
    leaving the pane to reap every finished session, for a reason the inbox does
    not show.
    """
    node = obligation.node
    record = snap.nodes.get(node)
    if record is None:
        return

    if typeset.enabled(typeset.Pane.INBOX):
        _typeset_preview(record.transcript, obligation.key)
    else:
        _native_preview(record.transcript)
    _reply(compose, actions, node, wrap=wrap)


def _native_preview(transcript: Transcript) -> None:
    """
    The end of the turn's prose in ImGui's own text, in a box of a fixed height.
    """
    # The turn's prose, clipped from the end: the ask is what a turn finishes on,
    # and this box is scanned rather than read. DETAIL carries the whole of it.
    tail = transcript.turn_prose()[-_PREVIEW_CHARS:]
    if imgui.begin_child("##body", imgui.ImVec2(-8.0, 110.0), imgui.ChildFlags_.borders):
        if tail.strip():
            imgui.push_text_wrap_pos(imgui.get_content_region_avail().x)
            imgui.text_colored(P.text.vec4, tail)
            imgui.pop_text_wrap_pos()
        else:
            imgui.text_colored(P.text_dim.vec4, "no output on this turn")
    imgui.end_child()


_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")
# A line that is a row of its own however wide the pane: a list item, a heading,
# a quote, a table row.
_OWN_ROW = re.compile(r"\s*(?:[-*+]\s|\d{1,9}[.)]\s|#{1,6}\s|>|\|)")


def markdown_blocks(source: Sequence[str]) -> list[tuple[int, int]]:
    """
    Where a markdown text can be cut: its blocks, each as the index of its first
    line and one past its last.

    A block is a run of lines between blank ones. A fence is in one block from its
    opening line to its closing one with its blank lines, and a fence nobody closed
    runs to the end, as a markdown reader has it. Whatever touches a fence with no
    blank line between is in the fence's block.
    """
    blocks: list[tuple[int, int]] = []
    start: int | None = None
    fence: str | None = None
    for number, line in enumerate(source):
        if fence is not None:
            closing = line.strip()
            if closing.startswith(fence) and not closing.strip(fence[0]):
                fence = None
            continue
        opened = _FENCE.match(line)
        if opened is not None:
            fence = opened.group(1)
        elif not line.strip():
            if start is not None:
                blocks.append((start, number))
                start = None
            continue
        if start is None:
            start = number
    if start is not None:
        blocks.append((start, len(source)))
    return blocks


def preview_tail(
    prose: str, lines: int = _PREVIEW_LINES, measure: int = _PREVIEW_MEASURE
) -> tuple[str, int]:
    """
    The end of a turn's prose cut where markdown can be cut, and how many lines of
    the turn are ahead of the cut.

    A cut at a character count lands inside a fence, a list or a table, and what
    follows it then reads as something else: the second half of a fence is prose
    and the prose after it is code. So the turn is taken a **block** at a time --
    a run of lines between blank ones, a fence whole with its blank lines -- from
    the end, for as long as the blocks taken come to no more than ``lines`` rows
    at ``measure`` characters to the row.

    Two things take it past that, and the view's cap is what bounds them on
    screen:

    - **The last block is always taken, whole**, whatever its length: a turn that
      ends on a sixty-line fence is previewed as that fence, scrolled.
    - **The cut is never above an indented block.** Indented lines belong to
      whatever is over them -- a list item's second paragraph, a nested item --
      and set without it they are a code block. The cut moves up to the first
      block that starts at the margin.

    The rows are an estimate and only decide where the cut goes: a paragraph is
    its characters over the measure, and a line that is a row of its own (a list
    item, a table row, a line of a fence) is one row or its own length over the
    measure. Pure; no character of what is taken is altered.
    """
    if not prose.strip():
        return "", 0
    source = prose.rstrip().split("\n")
    blocks = markdown_blocks(source)

    def rows(first: int, stop: int) -> float:
        block = source[first:stop]
        if any(_OWN_ROW.match(line) or _FENCE.match(line) for line in block):
            return float(sum(max(1, math.ceil(len(line) / measure)) for line in block))
        return float(max(1, math.ceil(sum(len(line) + 1 for line in block) / measure)))

    taken = len(blocks) - 1
    used = rows(*blocks[taken])
    while taken > 0:
        # Half a row for the air between two blocks.
        more = rows(*blocks[taken - 1]) + 0.5
        if used + more > lines:
            break
        used += more
        taken -= 1
    while taken > 0 and source[blocks[taken][0]].startswith((" ", "\t")):
        taken -= 1
    first = blocks[taken][0]
    return "\n".join(source[first:]), first


def _typeset_preview(transcript: Transcript, key: str) -> None:
    """
    The end of the turn's prose, set through the seam: whole blocks, under a line
    saying how much of the turn is above them.

    **Markdown outright, and not ``typeset.prose_parts``.** That rule is for a body
    nothing promises is markdown. This is the agent's own output, which DETAIL and
    the context log both read as markdown, and DETAIL shows the same turn beside
    this row: one text read two ways in one frame is the thing a single rule per
    kind of text is for.

    As tall as its text up to ``_PREVIEW_LINES`` of the prose face, and scrolling
    past that. Reported to no one (``review.body_shown``): a question is not an
    approval, and nothing is decided on this text that DETAIL does not show whole.
    """
    view = (key, "preview")
    length = transcript.published_length
    held = _PREVIEWS.get(view)
    if held is None or held[0] != length:
        cut, ahead = preview_tail(transcript.turn_prose())
        held = _PREVIEWS[view] = (length, ahead, cut)
    _, dropped, tail = held
    if not tail:
        imgui.text_colored(P.text_dim.vec4, "no output on this turn")
        return
    imgui.spacing()
    if dropped:
        _small()
        imgui.text_colored(
            P.text_dim.vec4, f"… {dropped:,} earlier lines of this turn - DETAIL has all of it"
        )
        _normal()
    typeset.markdown(
        _VIEWS,
        view,
        tail,
        width=imgui.get_content_region_avail().x - 8.0,
        height_cap=_PREVIEW_LINES * typeset.line_height(role=typeset.Role.PROSE),
    )


def _reply(
    compose: ComposeState,
    actions: InboxActions,
    node: NodeId,
    *,
    wrap: bool = True,
) -> None:
    """
    The composer under a question's preview, and the ways out of the turn.
    """
    imgui.spacing()
    draft = compose.replies.get(node, "")
    if compose.focus_reply:
        imgui.set_keyboard_focus_here()
        compose.focus_reply = False
    submitted, draft = multiline_input(
        "##reply",
        draft,
        imgui.ImVec2(-8.0, 54.0),
        wrap=wrap,
        flags=int(imgui.InputTextFlags_.allow_tab_input) | CTRL_ENTER_SUBMITS,
    )
    # Stored every frame: CTRL_ENTER_SUBMITS makes the returned bool mean "sent",
    # so there is no per-keystroke signal left to gate the draft on. Every frame
    # but the one Escape is pressed in: Escape hands back the text the box held
    # when the caret entered it, and storing that is Escape deleting the reply.
    if imgui.is_key_pressed(imgui.Key.escape):
        draft = compose.replies.get(node, "")
    compose.replies[node] = draft
    _small()
    imgui.text_colored(P.text_dim.vec4, "Ctrl+Enter sends, Enter for a new line")
    _normal()

    if wants_send(submitted, imgui.button("send"), draft):
        actions.send(node, draft.strip())
        compose.replies[node] = ""
        compose.focus_reply = True
    imgui.same_line()
    if imgui.button("interrupt"):
        actions.interrupt(node)
    imgui.same_line()
    if imgui.button("close session"):
        actions.close(node)
    imgui.same_line()
    _small()
    imgui.text_colored(P.text_dim.vec4, "closing frees the slot; finishing a turn does not")
    _normal()


def _expand_failure(snap: Snapshot, actions: InboxActions, obligation: SessionFailed) -> None:
    record = snap.nodes.get(obligation.node)
    if imgui.begin_child("##body", imgui.ImVec2(-8.0, 70.0), imgui.ChildFlags_.borders):
        imgui.push_text_wrap_pos(imgui.get_content_region_avail().x)
        imgui.text_colored(P.danger.vec4, author_text(obligation.error or "no detail recorded"))
        imgui.pop_text_wrap_pos()
    imgui.end_child()

    imgui.spacing()
    if record is not None and imgui.button("retry in a new session"):
        # Same task, same directory, same model, same team shape. A crashed session
        # cannot be resumed -- its subprocess is gone -- so the honest offer is a
        # fresh one carrying the same instructions, not a "resume" that would be a
        # new session wearing the old one's name.
        actions.relaunch(LaunchSpec.from_record(record))
        actions.dismiss(obligation.node)
    imgui.same_line()
    if imgui.button("dismiss"):
        actions.dismiss(obligation.node)
    imgui.same_line()
    _small()
    imgui.text_colored(
        P.text_dim.vec4, "dismiss clears it from here; the session stays in the rail"
    )
    _normal()


def _batch_controls(
    snap: Snapshot, focus: FocusState, state: review.ReviewState, bridge: Bridge
) -> None:
    """
    Batch approval, scoped by default and global behind a confirm.

    Only ever counts approvals, and says so. This is the one place where saying
    "3 pending" while the inbox says "5 need you" is correct rather than a
    regression -- the button acts on tool calls, and a question is not one.
    """
    approvals = snap.approvals
    if not approvals:
        return

    imgui.separator()
    # A refused approve is said on the open row of a call it covered. Here when
    # there is no such row: the cursor is on a question, a failure, or nothing.
    refusal = state.refusal
    current = focus.obligation(snap)
    on_row = isinstance(current, ApprovalNeeded) and (
        refusal is not None and current.approval.id in refusal.asked
    )
    if refusal is not None and not on_row:
        _warning(refusal.text)
    node = focus.node(snap)
    if node is not None:
        scoped = sum(1 for p in approvals if p.node == node)
        if scoped:
            record = snap.nodes.get(node)
            label = (record.agent_type if record else None) or "this agent"
            if imgui.button(f"approve all {scoped} from {label}"):
                review.approve_all_for_node(bridge, snap, state, node)
            imgui.same_line()

    total = len(approvals)
    if state.confirming_global:
        imgui.text_colored(P.danger.vec4, f"approve all {total} without reading them?")
        imgui.same_line()
        if imgui.button("yes, approve all"):
            review.approve_everything(bridge, snap, state)
        imgui.same_line()
        if imgui.button("cancel"):
            state.confirming_global = False
    elif imgui.button(f"approve all {total} pending calls..."):
        state.confirming_global = True


# -- the cold start ----------------------------------------------------------------
#
# Two empty states live in this pane and they are not the same emptiness.
# ``_zero_state`` below is a fleet that exists and owes nothing -- it answers "what is
# everyone doing". This one is a fleet that does not exist, which has no "everyone"
# to report on and exactly one useful thing to say: how to start.

_INTRO = "pptmstr runs a fleet of Claude agents, and hands you every decision they reach."

# The chord is spelled out, not merely coloured. STYLE.md's first rule is that hue is
# never the only channel, and an operator on ``high_contrast`` -- where accent and text
# collapse toward each other -- would otherwise have nothing marking this line as an
# instruction. The accent makes it findable; the literal "Ctrl+N" is what makes it
# actionable, and that survives every palette.
#
# Phrased as a suggestion for the same reason app.py:411 phrases its neighbour that
# way: the pane offers the next step, it does not take it.
_HINT_CHORD = "Ctrl+N"
_HINT_TEXT = "- start a session"

_ART_ROWS = len(splash_art.ART)
_ART_COLS = max(len(row) for row in splash_art.ART)

# Module-level, which is the one place this departs from ``splash.ArtFrames``' own
# advice to let the pane own it. ``inbox.draw`` is handed no presentation-state object
# to hang it on, and adding a parameter would mean editing app.py's call site, which
# belongs to nobody on this task.
#
# The deviation is affordable because there is nothing here to invalidate: the key is
# the step index, the art and the ranking are module constants, and colour is applied
# at draw time rather than baked into the memo -- so a theme switch cannot make an
# entry stale and a pane teardown leaks one tuple of 61 strings.
#
# What it buys, measured rather than estimated: ``time.perf_counter`` either side of
# ``splash.art_frame``, once per step across one whole 81-step cycle, gives 0.55ms mean
# and 0.99ms worst against 0.0002ms for a memo hit (2000 hits, same clock, one machine).
# The step rate is 16/s while a window being dragged is awake at 60, so 44 of every 60
# frames ask for a picture that has not changed -- about 24ms a second of re-derivation
# the render thread does not do. That is roughly 4% of the frame budget, not the quarter
# an earlier figure here claimed; still worth a module global, and worth stating at its
# real size, because a number nobody can reproduce is the one that gets built on.
_ART_FRAMES = splash.ArtFrames()

# The three luminance levels, as named palette entries.
#
# The obvious triple -- text_dim, text, text_strong -- must not ship. ``text`` and
# ``text_strong`` are the same bytes on high_contrast and on win311, so the line and the
# row behind it would be one flat bar there; and on cde (1.08:1) and turbo (1.07:1) the
# two separate in hue alone, which theme.py's first rule forbids as a sole channel.
#
# So TRAIL is RASTER's own ink at one step of ``theme.faded``, and the step is forced
# from both sides of faded's twelve-step grid. Step 9 collapses TRAIL into DIM on
# high_contrast -- white at 191/255 over black is #BFBFBF against text_dim's #C0C0C0,
# 1.01:1 -- and step 11 drops TRAIL to within 1.19:1 of RASTER on dark. Step 10 is the
# step that maximises the *smaller* of the two separations, which is the quantity that
# matters, because either one collapsing costs the same thing: a one-row band.
#
# And that is why the triple is pinned next door rather than here.
# ``splash.RASTER_ROWS_PER_SECOND`` is 16 only because the luminance band is two rows,
# and the band is two rows only where TRAIL is seen as part of the line instead of as
# more background. splash.py cannot check that -- it emits an ordinal and imports no
# palette by design -- so its rate constant is an assertion about the three lines below,
# and tests/test_theme.py holds it over all nine palettes.
_TRAIL_ALPHA = 10.0 / 12.0


def _ink(level: splash.Luminance) -> int:
    """
    The packed colour a row at ``level`` is drawn in.

    A chain closed by ``assert_never`` rather than a table, so a new member of
    ``splash.Luminance`` is a mypy error here rather than a KeyError at frame time or,
    worse, a ``.get`` default that renders the new level as an ordinary dim row. That
    totality is what the enum's own docstring asks the pane for, and a dict cannot
    give it.
    """
    if level is splash.Luminance.DIM:
        return P.text_dim.u32
    if level is splash.Luminance.TRAIL:
        return faded(P.text_strong, _TRAIL_ALPHA)
    if level is splash.Luminance.RASTER:
        return P.text_strong.u32
    assert_never(level)


def _splash(now: float) -> None:
    """
    The cold start: what this is, the quote, how to begin, and the art under it.

    Ordered so the two lines an operator has to *act* on are above the fold. The art
    is the last thing drawn and the first thing dropped, because it is the only part
    that can fail to fit.
    """
    imgui.spacing()
    imgui.text_colored(P.text_dim.vec4, _INTRO)
    imgui.spacing()
    _quote(now)
    imgui.spacing()
    imgui.text_colored(P.accent.vec4, _HINT_CHORD)
    imgui.same_line()
    imgui.text_disabled(_HINT_TEXT)
    imgui.spacing()
    _art(now)


def _quote(now: float) -> None:
    """
    The quote, one character at a time, each at its own alpha.

    Draws ``frame.glyphs`` and measures ``frame.lines``, and that split is the whole of
    what this pane has to get right about the glitch. The glyphs carry this frame's
    corrupted characters; the prose carries the block's extent, which must not move when
    a character is corrupted. Drawing the prose would produce a correct, still quote with
    no glitch in it, and measuring the glyphs would tie the space claimed below to an
    animation.

    Through the draw list rather than as a chain of ``text_colored``/``same_line``
    calls, for two reasons that both matter here. ``theme.faded`` returns the *packed*
    colour and memoises it, so a per-character tint is a dict hit rather than the
    per-frame colour arithmetic theme.py's header rules out of panels; and a hundred
    characters as a hundred layout items would put a hundred entries in this window's
    ID stack every frame to draw two lines of prose.

    Positioning is by multiplying out a single advance, which is sound only because
    the UI face is monospace -- the same property the art's column alignment rests on.
    """
    frame = splash.quote_frame(now)
    draw = imgui.get_window_draw_list()
    origin = imgui.get_cursor_screen_pos()
    advance = imgui.calc_text_size("M").x
    pitch = imgui.get_text_line_height_with_spacing()

    for row, (line, alphas) in enumerate(zip(frame.glyphs, frame.alphas, strict=True)):
        y = origin.y + row * pitch
        for column, (char, alpha) in enumerate(zip(line, alphas, strict=True)):
            if char == " ":
                continue
            draw.add_text(imgui.ImVec2(origin.x + column * advance, y), faded(P.text, alpha), char)

    # The draw list writes pixels without advancing the cursor, so the space has to be
    # claimed explicitly or everything below would be drawn on top of the quote.
    imgui.dummy(imgui.ImVec2(advance * max(map(len, frame.lines)), pitch * len(frame.lines)))


def _art(now: float) -> None:
    """
    The art, centred, at the largest size the remaining region allows, a colour a row.

    Returns without drawing when it does not fit. ``fit_size`` clamps up to its floor
    rather than reporting failure, so the answer still has to be checked against the
    room -- a clipped half-silhouette reads as a rendering fault, where nothing reads
    as deliberate.

    Through the draw list, which ``_quote`` also does but for a different reason. Here
    it is the only way to get a colour per row: the whole picture as one text item takes
    one pushed style colour, and one ``text()`` per row would insert
    ``style.item_spacing.y`` between rows and stand the art 60 spacings taller than
    ``required_extent`` just promised the pane it would be. Placing the rows by hand
    moves the pitch onto ``splash.LINE_PITCH_EM``, the constant ``fit_size`` and
    ``required_extent`` are already written against, so the three cannot disagree about
    where row 60 is. ImGui's own multi-line block advances by exactly that same pitch --
    ``calc_text_size`` on the 61-row art returns 61.0000 x size at every even size in
    the clamp range, measured in a live frame -- so this is a reimplementation of what
    the one call was already doing, not a new geometry.

    A row is one ``add_text``, not one per cell as in ``_quote``. ImGui emits no
    vertices for a glyph with no visible rect, so the art's spaces cost nothing to pass
    through and 61 calls draw what 4392 would. Nothing on this route is a printf path,
    so the '%' in the art needs no guarding here.
    """
    avail = imgui.get_content_region_avail()
    size = splash.fit_size(avail.x, avail.y, _ART_ROWS, _ART_COLS)
    width, height = splash.required_extent(size, _ART_ROWS, _ART_COLS)
    if width > avail.x or height > avail.y:
        return

    frame = _ART_FRAMES.frame(splash_art.ART, splash_art.RANK_OF, splash_art.RANKS, now)

    # Centring folds into the x every row is drawn at, so the cursor moves first and the
    # origin is read after it. That keeps the dummy below starting where the ink starts
    # rather than claiming a block offset half a pane to the left of the picture.
    imgui.set_cursor_pos_x(imgui.get_cursor_pos_x() + max(0.0, (avail.x - width) * 0.5))
    draw = imgui.get_window_draw_list()
    origin = imgui.get_cursor_screen_pos()
    pitch = size * splash.LINE_PITCH_EM

    imgui.push_font(face(Face.BODY), size)
    for row, (line, level) in enumerate(zip(frame.lines, frame.luminance, strict=True)):
        draw.add_text(imgui.ImVec2(origin.x, origin.y + row * pitch), _ink(level), line)
    imgui.pop_font()

    # The draw list writes pixels without advancing the cursor, so the space has to be
    # claimed explicitly or everything below would be drawn on top of the art. Exactly
    # ``required_extent``, which is the number already checked against the pane above.
    imgui.dummy(imgui.ImVec2(width, height))


def _zero_state(snap: Snapshot, now: float) -> None:
    """
    An empty queue is the feature, not a blank pane.

    It turns the centre into "nothing needs you -- here is what everyone is doing",
    which is the other half of the operator's loop and the moment they are deciding
    what to start next.
    """
    imgui.spacing()
    imgui.text_colored(P.ok.vec4, f"{STATE_GLYPH[AgentState.DONE]}  nothing needs you")
    imgui.text_colored(P.text_dim.vec4, "agents run until they need to change something.")
    imgui.spacing()
    imgui.separator()
    imgui.spacing()

    live = [
        snap.nodes[n]
        for n in projects.roots(snap)
        if not snap.nodes[n].state.is_terminal and snap.nodes[n].state is not AgentState.FAILED
    ]
    if not live:
        imgui.text_colored(P.text_dim.vec4, "nothing is running either. start something below.")
        return

    imgui.text_colored(P.text_strong.vec4, "what everyone is doing")
    imgui.spacing()
    for record in live:
        imgui.text_colored(P.state(record.state).vec4, f"  {STATE_GLYPH[record.state]}")
        imgui.same_line()
        imgui.text_colored(P.text.vec4, ellipsis(record.task, 320.0))
        imgui.same_line()
        imgui.text_colored(P.text_dim.vec4, f"- {flat_line(record.topic)}")

    imgui.spacing()
    imgui.separator()
    imgui.spacing()
    imgui.text_colored(P.text_dim.vec4, run_total(snap))
