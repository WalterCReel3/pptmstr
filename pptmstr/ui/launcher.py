"""
Starting a session: a modal invoked from anywhere, not a pane that is always there.

This replaces the omnibox that lived along the bottom of TRIAGE. Two things were
wrong with that arrangement, and only the second is about screen space.

The first is conceptual. The unit this application deals in is *intent per
session* -- a task, the directory it runs in, the model that runs it. An
always-present strip implies the opposite: that dispatch is an ambient property of
one arrangement of the screen. It was also absent from FOCUS entirely, so starting
work while attending to a running session meant leaving the session first. A modal
is reachable identically from both arrangements, which is what a per-session intent
actually needs.

The second is that one line of chrome bought a cramped single-line task field. The
prompt is the part of a launch worth the most care and it had the least room.

Imports imgui only for ``draw``; ``LauncherState`` and its decisions are pure, so
the parts worth testing do not need a GL context.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import assert_never

from imgui_bundle import imgui

from .. import brief as brief_mod
from .. import cli_version, sandbox, templates, tree
from .. import sessions as sessions_mod
from ..approval import Policy
from ..model import LaunchSpec
from ..sessions import Overlay, SessionRow
from ..theme import P
from . import widgets

# Verified against the model-config docs at build time (design §7, trap 9). Listed
# rather than free-text so a typo cannot become a session that fails on first turn.
MODELS: tuple[str, ...] = (
    "claude-sonnet-5",
    "claude-opus-5",
    "claude-haiku-4-5-20251001",
    "claude-fable-5",
)

TITLE = "New Task"

# Ctrl+N. Key members are plain ints here and do not support ``|`` as enums, so the
# chord is built from int() -- ``imgui.Key.mod_ctrl | imgui.Key.n`` raises.
CHORD = int(imgui.Key.mod_ctrl) | int(imgui.Key.n)

_WIDTH = 620.0
_TASK_HEIGHT = 132.0
_LIST_HEIGHT = 176.0

# ``###`` fixes the header's ID while its visible label changes with the pick, so
# naming the picked session does not slam the section shut mid-read.
_RESUME_ID = "###resume"

_MINUTE = 60.0
_HOUR = 3600.0
_DAY = 86400.0


def age_label(last_modified_ms: int, now: float) -> str:
    """
    How long ago, in the coarsest unit that still says something.

    ``last_modified`` is epoch **milliseconds** (see ``sessions.SessionRow``), and
    the division is the entire reason this is a function rather than a format
    string. Compared against ``time.time()`` unscaled, every session dates about
    fifty thousand years into the future -- far enough out that it reads as a
    corrupt transcript rather than as a units mistake, so the bug would be chased
    in the wrong module.

    A future timestamp is reported as "just now" rather than as a negative age. A
    clock that moved, or a transcript written on another machine, makes "now" wrong;
    "-3h ago" would make the *session* look wrong, which is the more expensive lie
    on a screen whose whole job is deciding which session to trust.
    """
    seconds = now - last_modified_ms / 1000.0
    if seconds < _MINUTE:
        return "just now"
    if seconds < _HOUR:
        return f"{int(seconds // _MINUTE)}m ago"
    if seconds < _DAY:
        return f"{int(seconds // _HOUR)}h ago"
    return f"{int(seconds // _DAY)}d ago"


def scan_sessions(cwd: str) -> tuple[tuple[SessionRow, ...], Overlay]:
    """
    The blocking half of the picker, named so that what must not run on the draw
    thread has somewhere obvious to be looked at.

    ``enumerate_sessions(cwd=...)`` and not ``same_cwd``. The two narrowings differ
    on git worktrees: the SDK's ``directory`` folds a directory's worktrees in,
    ``same_cwd`` is an exact string match. The fold is what a *recovery* picker
    wants -- work done in a worktree of this repo is this work, and the operator
    hunting for it does not think of it as a different project. The exact match is
    also unusable here for a second reason: the launcher's cwd defaults to ``"."``
    while ``SessionRow.cwd`` is the absolute path the CLI recorded, so ``same_cwd``
    would return nothing at all for the default. This repo has no worktrees, so the
    two have never been observed to disagree and this is a choice made on the
    reasoning rather than on a measurement.

    Errors are not caught here. ``enumerate_sessions`` refuses to turn an unreadable
    transcript tree into "no sessions", and swallowing that on the way past would
    undo it -- ``SessionPicker.poll`` renders the failure instead.
    """
    overlay = sessions_mod.load_overlay()
    rows = sessions_mod.enumerate_sessions(cwd=cwd, overlay=overlay, bookmarked_first=True)
    return rows, overlay


# What a worker hands back: rows and overlay, or the message from the failure that
# stopped it. Exactly one side is populated.
_ScanResult = tuple[tuple[SessionRow, ...] | None, Overlay | None, str | None]

Scanner = Callable[[str], tuple[tuple[SessionRow, ...], Overlay]]


@dataclass
class SessionPicker:
    """
    The resume list: one cached scan, the operator's overlay, and which row is picked.

    The cache is the whole design. ``sessions.enumerate_sessions`` opens, stats and
    head/tail-reads every transcript in the project and shells out to ``git
    worktree``; it is tens of milliseconds against a warm page cache here and
    unbounded cold, and a draw call paying that would stall every frame it ran on.
    So the scan runs on a worker and returns through a queue, and everything the
    draw does afterwards is arithmetic over the tuple the worker left behind.

    Nothing is shared mutable state across the two threads. The worker touches only
    the queue; ``poll`` -- called from the draw -- is the only writer of the fields.

    The scan starts when the modal opens, not when the section is expanded. It is on
    a worker either way, so a fresh launch cannot feel the difference; what deferring
    it bought was a click and a wait at the one moment the operator is hunting for
    lost work. See ``LauncherState.begin_open``.

    Opening early means the cwd field can move after the scan was fired, so the
    section asks again when it is expanded against a directory the last scan did not
    answer for. See ``set_expanded``.
    """

    rows: tuple[SessionRow, ...] = ()
    overlay: Overlay = field(default_factory=dict)
    # The message from a scan that failed, or None. Rendered rather than swallowed:
    # an empty picker shown to an operator hunting for lost work is
    # indistinguishable from "you have no sessions", which is the worst outcome this
    # feature can produce. Rows from the last good scan are kept alongside it.
    error: str | None = None
    scanning: bool = False
    # The directory the cached rows were scanned for, so the list can say which
    # question it is answering rather than leaving it to be assumed.
    scanned_cwd: str = ""
    # The cwd field's value as ``request_scan`` was handed it, unresolved. Compared
    # against the field to decide whether the listing still answers the question the
    # operator is asking. Raw rather than ``scanned_cwd`` because the comparison
    # happens in a draw call and ``realpath`` is a stat per component.
    requested_cwd: str = ""
    # Whether the resume section was open on the previous frame, and the directory an
    # expand asked for that no scan has been started for yet. Both are written only by
    # ``set_expanded``.
    expanded: bool = False
    pending_cwd: str | None = None
    selected: str | None = None
    # The brief directory the picked session wrote its premises into, if it exists.
    #
    # Recoverable, and not a guess: `brief.session_dir` is a function of cwd and
    # session id, and resume continues under the same id. It is the one thing about
    # the original session that does come back -- `task`, `model` and `template` do
    # not, because there is no `AgentRecord` on the far side of the CLI.
    recovered_brief: str | None = None
    _inbox: queue.SimpleQueue[_ScanResult] = field(default_factory=queue.SimpleQueue, repr=False)

    def request_scan(
        self, cwd: str, *, scanner: Scanner = scan_sessions
    ) -> threading.Thread | None:
        """
        Start a scan on a worker thread. Returns without waiting for it.

        A request made while one is already in flight is dropped rather than queued.
        Two scans of the same tree cannot disagree usefully, and the second would
        land after the first and re-order rows under a cursor that had stopped
        moving.

        The worker is returned so a caller that genuinely has to wait -- a test --
        can join it. The draw ignores it and must: waiting is the thing this exists
        to avoid.
        """
        if self.scanning:
            return None
        # Resolved here, and the resolved form is what the worker is given. The SDK
        # canonicalises ``directory`` itself, so this changes nothing about which
        # sessions come back -- it is so the list can *say* what it searched. The
        # launcher's cwd defaults to ``"."``, and a picker captioned "sessions for ."
        # answers a question the operator did not ask. One realpath on a click.
        self.requested_cwd = cwd
        cwd = os.path.realpath(cwd)
        self.scanning = True
        self.error = None
        self.scanned_cwd = cwd
        inbox = self._inbox

        def work() -> None:
            try:
                rows, overlay = scanner(cwd)
            except Exception as exc:  # noqa: BLE001 - surfaced, not swallowed
                # Caught at the thread boundary because an escaping exception here
                # goes to stderr and leaves ``scanning`` true forever, which renders
                # as a picker that spins and never says why. The message is put on
                # screen instead.
                inbox.put((None, None, f"{type(exc).__name__}: {exc}"))
                return
            inbox.put((rows, overlay, None))

        worker = threading.Thread(target=work, name="pptmstr-session-scan", daemon=True)
        worker.start()
        return worker

    def set_expanded(
        self, expanded: bool, cwd: str, *, scanner: Scanner = scan_sessions
    ) -> threading.Thread | None:
        """
        Tell the picker its section is open, and for which directory. Rescans if the
        listing is for a different one.

        The scan fires when the modal opens, which is before the operator has had the
        chance to type the directory they pressed Ctrl+N to work in. Without this the
        listing would be for wherever the *last* launch went, and the whole point of
        the cwd field is that those differ -- so the picker would confidently answer
        the wrong question at the moment its answer is trusted most.

        Only the expand re-asks, not every edit: the cwd field is on screen beside an
        expanded section, and comparing against it per frame would put a transcript
        scan behind every keystroke. Editing after expanding leaves a listing the
        caption still names honestly, with ``rescan`` beside it.

        A request that arrives while the modal-open scan is still in flight is held
        rather than dropped -- ``request_scan`` drops the second of two, and dropping
        this one would restore exactly the wrong-directory listing it exists to
        replace. It starts on the frame the earlier scan lands.

        Returns the worker so a test can join it; the draw ignores it and must.
        """
        if expanded and not self.expanded and cwd != self.requested_cwd:
            self.pending_cwd = cwd
        self.expanded = expanded
        if self.pending_cwd is None or self.scanning:
            return None
        pending, self.pending_cwd = self.pending_cwd, None
        return self.request_scan(pending, scanner=scanner)

    def poll(self) -> None:
        """
        Take a finished scan if one has landed. Costs a non-blocking read otherwise.
        """
        try:
            rows, overlay, error = self._inbox.get_nowait()
        except queue.Empty:
            return
        self.scanning = False
        if error is not None:
            # The previous rows stay. A listing that is a minute stale is worth more
            # than a blank one, and the error beside it says which it is.
            self.error = error
            return
        self.rows = rows or ()
        self.overlay = overlay or {}
        self.error = None

    def toggle_bookmark(self, target: SessionRow) -> None:
        """
        Flip a bookmark, persist it, and update the row in place.

        Takes the row rather than an id so that what is being flipped *from* is the
        state that was drawn. The overlay is the other candidate and it is the wrong
        one: it and the rows are joined at scan time and can only diverge afterwards,
        and on a divergence the operator's click should mean what the button under
        their cursor said, not what a map they cannot see thinks.

        Persisted at the click as the theme and wrap preferences are, rather than at
        exit: a bookmark that survives only a clean shutdown is a bookmark lost by
        the crash it was made to protect against.

        Deliberately does **not** re-sort. Bookmarked-first is the order the list was
        scanned in, not an invariant held under the cursor. Re-sorting on click would
        slide the next row under a mouse still resting on the button that moved it,
        and the operator's following click would land on a session they never read.
        The order settles on the next scan.
        """
        wanted = not target.bookmarked
        self.overlay = sessions_mod.set_bookmark(self.overlay, target.session_id, wanted)
        sessions_mod.save_overlay(self.overlay)
        self.rows = tuple(
            replace(row, bookmarked=wanted) if row.session_id == target.session_id else row
            for row in self.rows
        )

    def select(self, session_id: str | None, cwd: str) -> None:
        """
        Pick a session to resume, or clear the pick. Re-picking the current one clears.

        ``cwd`` is this launch's working directory rather than the row's, because the
        brief directory that matters is the one the resumed session will actually
        derive -- ``app._seed_brief`` builds it from the same two values, so the two
        agree by construction instead of by coincidence.
        """
        self.selected = None if session_id is None or session_id == self.selected else session_id
        self.recovered_brief = None
        if self.selected is None:
            return
        directory = brief_mod.session_dir(brief_mod.default_root(), cwd or ".", self.selected)
        # One stat, at the click. Offering a path to a directory that does not exist
        # would be the same lie as pre-filling the task.
        if directory.is_dir():
            self.recovered_brief = str(directory)

    @property
    def selected_row(self) -> SessionRow | None:
        for row in self.rows:
            if row.session_id == self.selected:
                return row
        return None


# ---------------------------------------------------------------------------
# The dangerously autonomous mode, as the launcher sees it
# ---------------------------------------------------------------------------

# The tree this pptmstr is running out of. Resolved once at import because it cannot
# move while the process does, and from the package rather than from the process's
# directory so it names pptmstr's own source wherever pptmstr was started.
#
# ``parents[1]`` is the package directory -- this file is ``pptmstr/ui/launcher.py``
# -- and ``source_tree_of`` widens that to the checkout holding it when there is one.
# The widening is the load-bearing half: what needs protecting is not only
# ``approval.py`` but everything beside it that decides what the next launch does.
OWN_CHECKOUT = tree.source_tree_of(str(Path(__file__).resolve().parents[1]))

_REFUSAL = (
    "refused for this directory -- this launch will use the ordinary gate.\n"
    "it is inside pptmstr's own checkout, or it is a path that cannot be resolved at "
    "all; the check answers yes to both and cannot tell them apart.\n"
    "under this mode the working directory is the one region an agent writes to "
    "without being asked, and pptmstr's own approval.py is in this checkout."
)


def refusal_for(cwd: str, *, checkout: str = OWN_CHECKOUT) -> str | None:
    """
    Why the mode cannot be used for ``cwd``, or None when it can.

    The sandbox's writable region *is* the session's cwd, and its protected paths
    cover ``.claude/**``, ``.mcp.json``, ``.git/hooks`` and ``.git/config`` -- not
    ordinary project source. ``cwd`` defaults to ``"."``, so the default launch would
    hand an agent that runs ``Bash`` unattended write access to the ``approval.py``
    that gates the *next* launch. The alternative boundary is "the operator typed a
    different directory", enforced by nothing.

    **The message names two situations because the predicate cannot separate them.**
    ``tree.lies_inside_checkout`` answers yes both to a directory inside the checkout
    and to a path with no location at all -- a symlink loop, a ``~user`` with no home
    -- and that collapse is deliberate there: a yes costs a refusal the operator can
    see and correct, a no hands an unlocatable directory to an agent that writes
    unattended. A refusal naming only the checkout would be false for the second, and
    would send an operator to move a directory when what they have is a typo.

    ``checkout`` is a parameter so the decision can be exercised against a tree a test
    controls rather than against wherever this file happens to be installed.
    """
    if tree.lies_inside_checkout(cwd.strip() or ".", checkout):
        return _REFUSAL
    return None


# Reading the installed CLI's version, as the gate below calls it. A parameter so the
# three answers can be exercised without a subprocess and without depending on which
# CLI the machine running the tests happens to have.
Prober = Callable[[], cli_version.FloorCheck]

_UNTICK = "launch is held while the box is ticked; untick it to start under the ordinary gate."


def floor_refusal(check: cli_version.FloorCheck) -> str | None:
    """
    Why the installed CLI cannot be trusted with the mode, or None when it can.

    ``Unreadable`` refuses. ``planning/2026-09-03-a-dangerously-autonomous-mode.md``
    §8c measured that the CLI accepts an unrecognised settings key silently, so a CLI
    that cannot be interrogated is a CLI that may ignore every key in
    ``spec.containment`` and start a session the operator believes is contained.

    **The two refusals are worded apart** because they need different actions: one says
    upgrade, the other says find out which binary answered. STYLE.md §3 names the
    collapse of two mistakes into one message as a smell that has already cost a
    retry here.

    ``app._containment_refusal`` judges the same value for the same reason and is a
    second spelling of this decision -- ``app`` imports this module, so the dependency
    cannot run the other way. The duplication is pinned:
    ``test_the_launcher_and_the_launch_path_refuse_the_same_readings`` fails if the two
    ever disagree about which of the three readings refuses.
    """
    match check:
        case cli_version.MeetsFloor():
            return None
        case cli_version.BelowFloor(version=version, floor=floor):
            return (
                f"refused on this machine -- the installed CLI reports {version}, below the "
                f"{floor} floor for the sandbox keys this mode sets.\n"
                "an older CLI accepts those keys silently and runs uncontained, so a session "
                "started here would be one you believe is sandboxed and is not.\n" + _UNTICK
            )
        case cli_version.Unreadable(detail=detail, floor=floor):
            return (
                f"refused on this machine -- the installed CLI's version could not be read "
                f"({detail}), so the {floor} floor for the sandbox keys this mode sets cannot "
                "be confirmed.\n"
                "'could not tell' is not the same answer as 'fine': a CLI that ignores those "
                "keys starts a session you believe is sandboxed and is not.\n" + _UNTICK
            )
        case _:
            assert_never(check)


@dataclass
class VersionGate:
    """
    Whether the installed CLI can be trusted with the mode. Read once, on a worker.

    ``cli_version.check_installed_cli`` spawns ``claude --version`` and waits up to ten
    seconds for it, so it may not run on the draw thread -- the same constraint that
    puts ``scan_sessions`` on a worker, and the same arrangement: a thread, a queue,
    and a ``poll`` called from the draw that is the only writer of these fields. The
    worker touches nothing but the queue.

    **Read once per process, not once per launch.** The binary the SDK spawns cannot
    change under a running process in a way this application would notice, and the
    alternative puts a subprocess behind a checkbox that gets ticked and unticked while
    the operator reads the paragraph beside it.
    """

    # Why the CLI cannot be trusted, or None. Only an answer once ``answered`` is true:
    # before that it is "nothing has been read yet", which is not "nothing is wrong",
    # and ``LauncherState.launch_hold`` keeps the two apart.
    refusal: str | None = None
    answered: bool = False
    checking: bool = False
    _inbox: queue.SimpleQueue[cli_version.FloorCheck] = field(
        default_factory=queue.SimpleQueue, repr=False
    )

    def request(
        self, *, prober: Prober = cli_version.check_installed_cli
    ) -> threading.Thread | None:
        """
        Start the read on a worker unless one has already run. Returns without waiting.

        The worker is returned so a caller that genuinely has to wait -- a test -- can
        join it. The draw ignores it and must: waiting is the thing this exists to
        avoid.
        """
        if self.answered or self.checking:
            return None
        self.checking = True
        inbox = self._inbox

        def work() -> None:
            try:
                inbox.put(prober())
            except Exception as exc:  # noqa: BLE001 - surfaced as a refusal, not swallowed
                # ``check_installed_cli`` documents that it does not raise. Caught at the
                # thread boundary anyway because an escaping exception here would leave
                # ``checking`` true forever, which renders as a launch button held with
                # no reason on screen and no way past it.
                inbox.put(
                    cli_version.Unreadable(
                        f"{type(exc).__name__}: {exc}", cli_version.SANDBOX_FLOOR
                    )
                )

        worker = threading.Thread(target=work, name="pptmstr-cli-version", daemon=True)
        worker.start()
        return worker

    def poll(self) -> None:
        """
        Take the answer if it has landed. Costs a failed queue read otherwise.
        """
        try:
            check = self._inbox.get_nowait()
        except queue.Empty:
            return
        self.checking = False
        self.answered = True
        self.refusal = floor_refusal(check)


# Beside the launch button, where the two words have to explain a greyed-out control.
# The reason in full is in the containment section the operator ticked the box in.
_HOLD_CHECKING = "confirming this machine's CLI can contain the session"
_HOLD_REFUSED = "this CLI cannot contain the session -- untick the mode to launch"
_HOLD_THIN = "the task box is this session's whole brief -- give it more than one line"
# In the containment section, under the description of what the mode grants: the mode
# is asked for and the directory allows it, and only the machine is still unanswered.
_HOLD_LINE = (
    "confirming the installed CLI is new enough to honour those keys -- launch is held "
    "until it answers"
)

# Why the cap is on this screen at all: once spawns auto-approve it is the only bound
# on fan-out the policy cannot widen, because `driver._gate_tool_use` denies at cap
# *before* it calls `classify`. Every other bound in the paragraph above is something
# the mode relaxes; this one it cannot reach.
_CAP_REASON = (
    "once spawns run unattended that is the only bound left on fan-out, and it is the "
    "one bound this mode cannot widen"
)


def _cap_line(subagent_cap: int | None) -> str:
    """
    The sub-agent cap as the operator should read it, with the number when it is known.

    ``None`` means the caller did not supply one, and the line then states the bound
    without naming it. **A stand-in number would be worse than no number**: the cap in
    force is ``Settings.subagent_cap``, which a caller reads and passes, and printing
    ``driver.DEFAULT_SUBAGENT_CAP`` instead would put a figure on screen that is right
    only until the operator changes the setting -- which is the one case where they are
    reading this line to find out what it says.
    """
    if subagent_cap is None:
        return f"sub-agents are capped by the setting -- {_CAP_REASON}."
    return f"sub-agents are capped at {subagent_cap} -- {_CAP_REASON}."


@dataclass
class LauncherState:
    """
    Draft intent for a session that does not exist yet.

    The draft survives a cancel on purpose: dismissing the modal to go look up a
    directory should not cost the paragraph already typed. It is cleared on launch,
    which is the only point at which the intent has gone somewhere.
    """

    task: str = ""
    cwd: str = "."
    # Path to a directory of premises, or empty for a session with only its task.
    # Optional on purpose: most sessions are solo with a one-line task, and making
    # a brief mandatory would tax the common case for a problem it does not have.
    brief: str = ""
    model_index: int = 0
    # Index into templates.names(). "solo" is first and is the default, so the
    # launcher behaves exactly as it did before teams existed unless asked otherwise.
    template_index: int = 0
    # True from the frame the modal is drawn until the frame it stops being drawn.
    # Read by the global key handler, which runs before any drawing and therefore
    # sees the previous frame's value -- which is the correct one, because the key
    # it is deciding about was pressed while that frame was on screen.
    is_open: bool = False
    # The resume list and the pick made in it. Lives on the draft rather than beside
    # it because a picked session is part of the intent -- it is what decides whether
    # the task typed above starts a conversation or continues one.
    picker: SessionPicker = field(default_factory=SessionPicker)
    # Whether the operator asked for the dangerously autonomous mode on *this* launch,
    # which is not the same question as whether this launch gets it -- see
    # ``dangerous_engaged``. Held as what was asked rather than as what was granted so
    # that a refusal does not rewrite the operator's answer behind them: correcting the
    # directory engages the mode without a second click, and the refusal on screen is
    # what says which of the two states they are in.
    #
    # Nothing here is written to ``Settings``. 08-11 rejected a persisted toggle
    # because "the operator sets it for the session they are watching and forgets it
    # is set for the four they are not", and ``draw`` clears this at the launch for the
    # same reason one process's fifth session is as unwatched as the next run's first.
    dangerous: bool = False
    # An override of ``Settings.subagent_cap`` for this launch, or None to use the
    # setting. None is the untouched default, so a draft nobody edited produces the
    # spec it produced before the control existed.
    #
    # Not mode-specific, and the control is not inside ``_containment_section``: the
    # cap bounds fan-out under every policy, and the operator asked for an override at
    # launch rather than an override under the dangerous mode. What the mode changes is
    # only how much rests on it -- see ``_CAP_REASON``.
    subagent_cap: int | None = None
    _open_requested: bool = False
    _focus_task: bool = False
    # The refusal computed for a cwd field value, and the value it was computed for.
    # The draw asks every frame and the answer resolves two paths through the
    # filesystem -- a stat per component on each -- so it is memoised against the raw
    # field, which is the thing that changes. One resolve per directory typed rather
    # than one per frame. ``None`` as the key means nothing has been computed yet; the
    # field itself is never None, since ``containment_refusal`` substitutes ".".
    _refusal_for: str | None = None
    _refusal: str | None = None
    # The second reason the mode can be unavailable, beside the first. The two are read
    # together and they do not end the same way: a directory is wrong in a field the
    # operator is looking at, so a refused one downgrades the launch and says so; a CLI
    # below the floor cannot be corrected from this modal at all, so it holds the
    # launch instead. See ``launch_hold``.
    version_gate: VersionGate = field(default_factory=VersionGate)

    def request_open(self) -> None:
        """Ask for the modal next time it draws. Safe to call when already open."""
        if not self.is_open:
            self._open_requested = True

    def begin_open(self, *, scanner: Scanner = scan_sessions) -> threading.Thread | None:
        """
        Consume a pending open request. The modal is up from this frame on.

        The session scan starts here rather than when the resume section is expanded.
        It runs on a worker and never touches a draw call, so a fresh launch -- still
        the common case and still the default -- cannot feel it either way. What
        deferring it to the expand did cost was a click and a wait at the one moment
        the operator is hunting for lost work, which is the moment the picker exists
        for. The section itself stays collapsed; only the rows behind it are ready.

        Every open rescans. A listing from an hour ago is not a listing of what is
        there, and the picker is read by someone deciding which session to trust.

        Returns the worker so a caller that genuinely has to wait -- a test -- can
        join it. The draw ignores it and must.
        """
        self._open_requested = False
        self.is_open = True
        self._focus_task = True
        return self.picker.request_scan(self.cwd.strip() or ".", scanner=scanner)

    @property
    def ready(self) -> bool:
        return bool(self.task.strip())

    def containment_refusal(self) -> str | None:
        """
        Why the mode cannot be used for the directory now in the field, or None.

        **The question is asked of the raw field, and ``spec`` resolves it differently.**
        ``tree.lies_inside_checkout`` expands ``~``; ``os.path.realpath`` below does not,
        so a field of ``~/x`` is measured against the home directory and launched as a
        literal ``~`` component under this process's directory. Nothing pins the two
        together. What keeps that from being an exposure rather than a mismatch is that
        the launched spelling names a directory that does not exist on any machine
        without a literal ``~`` directory in it, and a session cannot start in one.
        """
        raw = self.cwd.strip() or "."
        if raw != self._refusal_for:
            self._refusal_for = raw
            self._refusal = refusal_for(raw)
        return self._refusal

    @property
    def dangerous_engaged(self) -> bool:
        """
        Whether this launch actually carries the mode: asked for, and not refused.
        """
        return self.dangerous and self.containment_refusal() is None

    @property
    def premise_is_thin(self) -> bool:
        """
        Whether an unattended launch would start on a premise of one line.

        **Measured on the task box, not on the brief field, and that is the whole of
        why this reads the way it does.** An empty brief field is not a launch without
        premises -- it is the launch that *creates* them: ``app._seed_brief`` writes the
        task text as entry ``000`` of a fresh brief for any template with roles, and
        naming a directory here suppresses that so a fork does not bury what it was
        launched to continue. So a bar on the brief field being empty would refuse the
        ordinary first run of a team and send the operator off to hand-build a directory,
        which is the one thing that field's own comment says is not how briefs are made.
        The thin thing is the text, and under a team the text *is* the brief.

        **The bar is a line break rather than a length**, because every length is a
        number nobody can defend and this one is already taught on screen -- the hint
        under the box says Enter starts a new line and Ctrl+Enter launches. It asks
        whether the operator treated the box as a subject line or as a document, which
        is the only thing about a premise a modal can actually tell.

        **It is trivially defeated by pressing Enter twice, and that is not a defect.**
        There is no adversary on this path: the party being held is the one the hold
        protects, and what it is protecting them from is a habit rather than an
        intention. A bar whose job is to be noticed is done when it is noticed, and an
        unforgeable one here would buy nothing and cost every legitimate two-line launch.
        """
        if self.brief.strip():
            return False
        return "\n" not in self.task.strip()

    def launch_hold(self) -> str | None:
        """
        Why the launch button is held, in the words that fit beside it, or None.

        **Held rather than downgraded, which is where this refusal departs from the
        directory's.** A refused directory produces the ordinary gate and a message,
        because the field that caused it is on screen and one keystroke from correct. A
        CLI below the floor is not correctable from this modal, and its answer arrives
        on a worker *after* the press would have cleared the draft -- so downgrading
        would spend the operator's paragraph on a launch refused somewhere they cannot
        see. The button holds, the reason is in the section above it, and unticking the
        box is the way past.

        **Fail-closed before the answer lands.** A mode that has not been confirmed is
        not a mode that may start: the window between ticking the box and the read
        landing is exactly the window a cold-start CLI widens, and it is the one where
        "could not tell" is most likely to be the eventual answer. The wait is one
        subprocess, once per process, begun on the frame the box was ticked.

        **A thin premise holds too, and it is asked first.** It is a third case and it
        sits with the CLI's rather than with the directory's: a refused directory
        downgrades because the mode *cannot be granted there*, so there is a real launch
        to fall back to and a message explaining which one they got. A one-line premise
        is not a reason the mode cannot be granted -- it is a reason the operator has
        not finished writing -- so there is nothing to fall back to that they would have
        chosen, and downgrading would spend the draft (the press clears ``task``) on a
        gated session they did not ask for. It is asked ahead of the version gate
        because it is the one of the three the operator can act on while the subprocess
        they are otherwise waiting on is still running, and because a machine that
        cannot run the mode is a once-per-machine answer while this is a per-launch one.

        None whenever the mode is not engaged, which is every launch this application
        made before the mode existed -- no read, no worker, no disabled button.
        """
        if not self.dangerous_engaged:
            return None
        if self.premise_is_thin:
            return _HOLD_THIN
        if not self.version_gate.answered:
            return _HOLD_CHECKING
        return _HOLD_REFUSED if self.version_gate.refusal is not None else None

    def spec(self) -> LaunchSpec:
        """
        The draft as the value the driver is handed. Empty cwd means the repo root.

        A ``LaunchSpec`` rather than a tuple of strings, which is the correction row
        9 paid for: the callback took four loose positionals, so dropping one still
        typechecked and a relaunched team started solo. Adding the brief to a
        positional list would be the same defect waiting on the same signature.

        ``resume`` is the picked session or None, and None is the untouched default:
        a draft nobody opened the resume section on produces exactly the spec it did
        before the section existed.
        """
        resolved_cwd = os.path.realpath(self.cwd.strip() or ".")
        # Both or neither, and the pairing is the mode. The policy releases ``Bash``
        # from the gate and the containment is the only thing bounding what a released
        # ``Bash`` reaches, so a spec carrying the policy alone is an unattended agent
        # with the whole machine, and one carrying the containment alone is a sandbox
        # around a gate that is still asking. A draft that did not ask for the mode, or
        # asked in a directory it is refused for, produces the two defaults -- which is
        # the spec this built before the mode existed.
        engaged = self.dangerous_engaged
        return LaunchSpec(
            task=self.task.strip(),
            model=MODELS[self.model_index],
            # Resolved here, because a relative cwd reaches the store and the store
            # cannot resolve one: ``model.relative_write`` returns None for every
            # absolute write path unless the agent's cwd is itself absolute, so a
            # session launched with the default "." records every write as unplaced
            # and ``Task.wrote_outside_declaration`` reads empty for the whole run.
            # The measurement is silent rather than wrong, which is worse.
            #
            # Behaviour-preserving for the agent: the SDK resolves a relative
            # ``ClaudeAgentOptions.cwd`` against this process's directory, which is
            # what ``realpath`` names here (see ui/projects._derive).
            cwd=resolved_cwd,
            # Resolved here, beside the cwd it is derived from, because it is the
            # one place that stats the filesystem on the operator's behalf. The
            # store cannot do it -- the reducer does no IO -- and the driver must
            # not, or two sessions on one directory could disagree about where
            # their writes are measured from.
            session_base=tree.session_base(resolved_cwd),
            template=templates.names()[self.template_index],
            brief=self.brief.strip() or None,
            resume=self.picker.selected,
            containment=sandbox.containment_settings() if engaged else None,
            policy=Policy.AUTONOMOUS if engaged else Policy.STRICT,
            # Unconditionally, and not gated on ``engaged`` as the two above are.
            # Those pair because the policy without the containment is an unattended
            # agent with the whole machine; a cap is capacity and pairs with nothing,
            # so an operator who sized this launch for two sub-agents gets two whether
            # or not they also ticked the mode.
            subagent_cap=self.subagent_cap,
        )


def handle_shortcut(state: LauncherState) -> None:
    """
    Ctrl+N, from either layout and from inside a text field.

    ``route_over_active`` is the load-bearing flag. The other keyboard handlers in
    this application bail on ``want_capture_keyboard``, which is right for bare
    letter keys -- typing "a" into a rejection reason must not approve anything --
    but it would make this shortcut dead in exactly the place it is most wanted,
    the reply box of a session that just prompted the operator for something new.

    Must be called inside a frame, which is why it does not live with the other
    shortcut handling in ``begin_frame``.
    """
    if imgui.shortcut(CHORD, imgui.InputFlags_.route_global | imgui.InputFlags_.route_over_active):
        state.request_open()


def _session_row(picker: SessionPicker, row: SessionRow, cwd: str, now: float) -> None:
    """
    One line: bookmark toggle, then a selectable spanning the rest of the row.

    Label, branch and age go into a single selectable string rather than a selectable
    followed by dimmed metadata. A selectable sized to its own text leaves the branch
    and the age outside the hit box, and those are the parts an operator reads last
    and clicks from -- a dead half-row is worse than undifferentiated colour.
    """
    imgui.push_id(row.session_id)
    # Colour carries the state as well as the glyph. At this point size "•" and "+"
    # differ by a few pixels of ink, which is not a difference an operator scanning a
    # list of thirty rows for their own bookmarks can act on.
    if row.bookmarked:
        imgui.push_style_color(imgui.Col_.text, P.accent.vec4)
    if imgui.small_button("*" if row.bookmarked else "+"):
        picker.toggle_bookmark(row)
    if row.bookmarked:
        imgui.pop_style_color()
    imgui.set_item_tooltip("remove bookmark" if row.bookmarked else "bookmark this session")
    imgui.same_line()

    meta = "  ·  ".join(p for p in (row.git_branch, age_label(row.last_modified, now)) if p)
    tail = f"  ·  {meta}" if meta else ""
    # ``label`` can be a first prompt, which is a whole paragraph with newlines in
    # it. Flattened before measuring, or the row grows to the height of the prompt.
    head = " ".join(row.label.split())
    room = imgui.get_content_region_avail().x - imgui.calc_text_size(tail).x
    picked, _ = imgui.selectable(
        f"{widgets.ellipsis(head, max(80.0, room))}{tail}", row.session_id == picker.selected
    )
    if picked:
        picker.select(row.session_id, cwd)
    imgui.pop_id()


def _resume_section(state: LauncherState, *, scanner: Scanner) -> None:
    """
    The resume picker: collapsed until asked for, over rows already being scanned.

    The header starts shut so a fresh launch looks exactly as it did before this
    section existed, but ``LauncherState.begin_open`` put the scan in flight when the
    modal opened -- so unless the cwd field has moved since, expanding costs a header
    toggle and no disk read, and by the time an operator has read this far the rows
    are usually already here. When it has moved, expanding starts a scan for the
    directory now in the field; see ``SessionPicker.set_expanded``.
    """
    picker = state.picker
    picker.poll()

    imgui.spacing()
    row = picker.selected_row
    heading = (
        "resume an existing session" if row is None else f"resuming: {' '.join(row.label.split())}"
    )
    cwd = state.cwd.strip() or "."
    expanded = imgui.collapsing_header(f"{widgets.ellipsis(heading, _WIDTH - 60.0)}{_RESUME_ID}")
    picker.set_expanded(expanded, cwd, scanner=scanner)
    if not expanded:
        return

    imgui.text_disabled(f"sessions in {picker.scanned_cwd or cwd} and its git worktrees")
    if picker.error is not None:
        # Loud, and never a silently empty list. Someone reading this screen has
        # already lost work once.
        imgui.text_colored(P.danger.vec4, f"could not read the transcripts: {picker.error}")

    now = time.time()
    if imgui.begin_child("##sessions", imgui.ImVec2(-1.0, _LIST_HEIGHT), imgui.ChildFlags_.borders):
        if picker.scanning and not picker.rows:
            imgui.text_disabled("reading transcripts...")
        elif not picker.rows and picker.error is None:
            imgui.text_disabled("no sessions recorded for this directory")
        for entry in picker.rows:
            _session_row(picker, entry, cwd, now)
    imgui.end_child()

    if imgui.small_button("rescan") and not picker.scanning:
        picker.request_scan(cwd, scanner=scanner)
    if row is not None:
        imgui.same_line()
        if imgui.small_button("launch fresh instead"):
            picker.select(None, cwd)
        # The whole of what resume does and does not restore, on screen rather than
        # in a doc. The conversation comes back; nothing that lived in an
        # ``AgentRecord`` does, because there is no record on the far side of the CLI.
        imgui.text_colored(
            P.accent.vec4,
            "the conversation comes back and the task above is sent to it as the next message",
        )
        imgui.text_disabled(
            "model, team and task are this launch's -- they are not read back from the session"
        )
        if picker.recovered_brief is not None and picker.recovered_brief != state.brief.strip():
            # The one exception, and only because the session id does not move:
            # `brief.session_dir` derives the same directory it derived before.
            if imgui.small_button("use this session's brief"):
                state.brief = picker.recovered_brief
            imgui.same_line()
            # Clipped rather than allowed to run off the edge: a brief path is a
            # project slug and a uuid and is wider than the modal on every machine.
            imgui.text_disabled(
                widgets.ellipsis(picker.recovered_brief, imgui.get_content_region_avail().x)
            )


def _containment_section(
    state: LauncherState, *, prober: Prober, subagent_cap: int | None = None
) -> None:
    """
    The dial for the dangerously autonomous mode, and the refusals where it cannot go.

    On the modal rather than as a follow-up because ``planning/2026-08-22`` D3 --
    "displaying the dial is part of shipping the dial, not a follow-up" -- binds
    harder here than at any lower rung: an operator who cannot see that a session is
    under-gated cannot supervise it.

    **The word carries the danger, not the colour.** Hue is never the only channel on
    this screen (design §6.1), and on ``high_contrast`` the danger role moves toward
    the text role -- so the label says "dangerous" in text that survives every
    palette, and the colour only makes it findable.

    **The released set is every tool the gate would otherwise have held**, so the
    label names the kinds rather than one tool: a label that said only ``Bash`` would
    understate what the tick grants, and understating it is worse than saying nothing.

    What the contained half claims is limited to what has been measured, and the
    measurements are per tool rather than per mode. ``scripts/verify_sandbox_gate.py``
    established the egress allowlist and the writable region for a root session's
    ``Bash``; ``scripts/verify_nested_sandbox.py`` established that a sub-agent's
    ``Bash`` gets the same region and the same allowlist, which is what lets this
    screen describe a fan-out in the same sentence as a solo run. The credential
    denials are deliberately not claimed -- ``sandbox.credentials`` binds sandboxed
    ``Bash`` only, so a line telling the operator their keys are denied would be false
    for every tool that runs in the CLI process.

    **The uncontained edge is on screen rather than only in the record.**
    ``WebFetch``/``WebSearch`` are released and run inside the CLI process, which the
    sandbox does not wrap, so no allowlist reaches them. That is a cost the operator
    chose in exchange for an unattended agent that can read documentation, and a
    chosen cost is exactly the kind that has to be visible at the moment of choosing.

    **Two things can refuse the mode and this is where both of them are said.** The
    directory is asked first and the machine only if the directory allowed it: a launch
    the directory already refused is not a contained launch, so reading the CLI's
    version for it would spawn a subprocess to answer a question nothing asks.
    """
    imgui.spacing()
    imgui.separator()
    imgui.spacing()

    _, state.dangerous = imgui.checkbox(
        "dangerous mode: run unattended -- writes, spawns and messages included",
        state.dangerous,
    )
    if not state.dangerous:
        imgui.text_disabled(
            "off -- every write, command, spawn and message stops here for you, as it does today"
        )
        return

    imgui.push_text_wrap_pos(0.0)
    gate = state.version_gate
    refusal = state.containment_refusal()
    if refusal is None:
        # Started from the draw and only from here, so an uncontained launch never
        # spawns the subprocess. Both calls are no-ops once the read has landed, so
        # after the first frame this costs two bool reads and a failed queue get.
        gate.poll()
        gate.request(prober=prober)
        refusal = gate.refusal
    if refusal is not None:
        imgui.text_colored(P.danger.vec4, refusal)
    else:
        imgui.text_colored(
            P.danger.vec4,
            "on -- this session writes files, runs commands, spawns sub-agents, declares "
            "board tasks and messages its own agents without asking you. A tool this "
            "build has never heard of is still not released.",
        )
        imgui.text_disabled(
            f"contained: Bash and anything it starts run in a sandbox that reaches "
            f"{sandbox.ALLOWED_DOMAIN} and nothing else, and writes only inside the working "
            "directory above. Every sub-agent gets that same boundary, so a fan-out reaches "
            "no further than one agent does."
        )
        # Named separately from the sandbox line because it is a different mechanism
        # reaching the same region, and an operator who has to reason about a failure
        # needs to know which one refused them.
        imgui.text_disabled(
            "file writes are held to that same directory by the gate rather than by the "
            "sandbox, because Write and Edit run in the CLI process -- a target that "
            "cannot be shown to be inside it is refused."
        )
        # Warn rather than disabled grey. This is the one line that says a boundary is
        # absent, and it is the line an operator skimming greyed-out reassurance would
        # skip -- which is the reading the rest of this block invites.
        imgui.text_colored(
            P.warn.vec4,
            "not contained: WebFetch and WebSearch run inside the CLI process, which the "
            "sandbox does not wrap, so they reach whatever host the session names. Released "
            "anyway -- an unattended agent that cannot read documentation is the worse trade.",
        )
        imgui.text_disabled(_cap_line(subagent_cap))
        if not gate.answered:
            imgui.text_disabled(_HOLD_LINE)
    imgui.pop_text_wrap_pos()


def draw(
    state: LauncherState,
    *,
    running: int,
    queued: int,
    cap: int,
    launch: Callable[[LaunchSpec], None],
    wrap: bool,
    scanner: Scanner = scan_sessions,
    prober: Prober = cli_version.check_installed_cli,
    subagent_cap: int | None = None,
) -> None:
    """
    Draw the modal if it has been asked for.

    ``cap`` and ``subagent_cap`` are different bounds and are not interchangeable:
    ``cap`` is how many *sessions* run at once and already has a consequence on this
    screen, while ``subagent_cap`` is how many sub-agents one session may hold.

    ``subagent_cap`` is the operator's *setting*, which this modal both displays and
    offers to override for one launch. It is optional because a caller that does not
    know it is better served by a line without a number than by a plausible wrong one
    -- and with no setting to override there is no override control either, since the
    box would have to be seeded from a figure nobody chose. See ``_cap_line``.

    Belongs at root level -- ``post_render_dockable_windows`` -- rather than inside
    a panel. A popup opened from a docked window is scoped to that window, and the
    panel set differs between the two layouts, so a launcher parented to a TRIAGE
    pane would vanish on the switch to FOCUS.
    """
    if state._open_requested:
        state.begin_open(scanner=scanner)
        imgui.open_popup(TITLE)

    if not state.is_open:
        return

    viewport = imgui.get_main_viewport()
    imgui.set_next_window_pos(viewport.get_center(), imgui.Cond_.appearing, imgui.ImVec2(0.5, 0.5))
    imgui.set_next_window_size(imgui.ImVec2(_WIDTH, 0.0), imgui.Cond_.appearing)
    opened, _ = imgui.begin_popup_modal(TITLE, None, imgui.WindowFlags_.always_auto_resize)
    if not opened:
        # Dismissed by something other than this function -- a click on the blocked
        # background, or ImGui's own Escape handling. Resync so the key handler
        # stops deferring to a modal that is gone.
        state.is_open = False
        return

    if state._focus_task:
        imgui.set_keyboard_focus_here()
        state._focus_task = False

    # The same chord the reply boxes use: Ctrl+Enter launches, Enter breaks the
    # line. Sharing it is the point -- a task box and a reply box are both prompts,
    # and the binding that differed here was the one whose misfire costs the most,
    # since a stray Enter mid-sentence spawns a session on half a sentence.
    # escape_clears_all is deliberately absent on top of it: it would eat the key
    # that dismisses the modal.
    submitted, state.task = widgets.multiline_input(
        "##task",
        state.task,
        imgui.ImVec2(-1, _TASK_HEIGHT),
        wrap=wrap,
        flags=widgets.CTRL_ENTER_SUBMITS,
    )
    imgui.text_disabled("what should a new session do?  Ctrl+Enter launches, Enter for a new line")
    # Said here rather than left to be discovered, because it changes what the box is
    # for: on a team, this text is the premises every worker is told to read, not
    # just the lead's opening message.
    if templates.BUILT_IN[state.template_index].roles:
        imgui.text_disabled("on a team, this also becomes the premises every worker reads")

    imgui.spacing()
    imgui.separator()
    imgui.spacing()

    # The working directory is the field worth the label. An orchestrator whose
    # agents all share the launching shell's cwd cannot run work across projects,
    # which is most of the point -- and it is the FLEET rail's grouping key, so a
    # typo does not fail, it quietly files the session under a project that does
    # not exist.
    imgui.set_next_item_width(-1)
    _, state.cwd = imgui.input_text_with_hint(
        "##cwd", "working directory (agents run here)", state.cwd
    )
    imgui.text_disabled("working directory")

    # After the directory because it is scoped by it, and before the brief because it
    # can fill the brief in -- the two fields it touches are the ones either side.
    _resume_section(state, scanner=scanner)

    # **Not how a brief is created.** A team session writes the task above as its
    # first premise at launch, because that is where the operator's premises already
    # are -- this field is for pointing a session at one that exists, which is what a
    # fork continuing its parent's work needs. Filling it in *suppresses* the seed,
    # so a session told to continue a brief does not bury it under a new one.
    #
    # Empty is the common case and must stay cheap to leave empty.
    imgui.spacing()
    imgui.set_next_item_width(-1)
    _, state.brief = imgui.input_text_with_hint(
        "##brief", "continue an existing brief (optional) -- leave empty for a new one", state.brief
    )
    imgui.text_disabled("brief directory")

    imgui.spacing()
    imgui.set_next_item_width(240.0)
    _, state.model_index = imgui.combo("model", state.model_index, list(MODELS))
    _, state.template_index = imgui.combo("team", state.template_index, list(templates.names()))
    # The shape is not obvious from a one-word name, and picking the wrong one is
    # only visible several turns later when workers start appearing -- so the
    # description is on screen rather than a tooltip away.
    imgui.text_disabled(templates.BUILT_IN[state.template_index].description)

    # Here, with the ordinary launch fields, rather than inside the containment
    # section: the cap bounds fan-out under every policy and the operator asked for an
    # override at launch, not an override under the dangerous mode. What the mode
    # changes is how much rests on the number, which is what `_CAP_REASON` says beside
    # it down there.
    #
    # Drawn only when the caller supplied the setting in force, because without one
    # there is no number to offer an override *of* -- and seeding the box from
    # `DEFAULT_SUBAGENT_CAP` would put a figure on screen that is wrong for exactly the
    # operator who changed the setting. `_cap_line` already argues that case.
    effective_cap = subagent_cap if state.subagent_cap is None else state.subagent_cap
    if effective_cap is not None:
        imgui.set_next_item_width(240.0)
        changed, typed = imgui.input_int("sub-agents", effective_cap)
        if changed:
            # Clamped, not refused. A negative cap is a drag past the end of a
            # spinner rather than an intention worth a message on screen, and zero is
            # kept because zero is a session that may not spawn at all.
            state.subagent_cap = max(0, typed)
            effective_cap = state.subagent_cap
        # Inside the guard with the control it labels. Left outside, a caller that
        # supplied no setting got the caption without the spinner -- a screen
        # describing a control that is not there, and asserting a per-launch cap when
        # nothing is overriding anything.
        imgui.text_disabled("sub-agents one session may run at once, for this launch only")

    # The number in force for *this* launch, so the containment section cannot end up
    # naming a different one from the box above it.
    _containment_section(state, prober=prober, subagent_cap=effective_cap)

    imgui.spacing()
    imgui.separator()
    imgui.spacing()

    # Two separate reasons the button can be dead, kept apart because only one of them
    # has something worth printing: an empty task box explains itself, and a mode the
    # machine cannot grant does not.
    hold = state.launch_hold()
    enabled = state.ready and hold is None
    if not enabled:
        imgui.begin_disabled()
    clicked = imgui.button("launch", imgui.ImVec2(110.0, 0.0))
    if not enabled:
        imgui.end_disabled()

    imgui.same_line()
    cancelled = imgui.button("cancel", imgui.ImVec2(90.0, 0.0))

    if hold is not None:
        imgui.same_line()
        imgui.text_colored(P.warn.vec4, hold)

    # Only at cap, and only as a consequence. The running/cap counter itself is in
    # the status bar, which stays visible behind the modal -- repeating it here
    # would be the same duplication the omnibox was carrying. What the status bar
    # cannot say is what happens to *this* launch, and a session that sits in
    # SPAWNING while others work looks broken to whoever just pressed the button.
    if running >= cap:
        imgui.same_line()
        imgui.text_colored(P.warn.vec4, f"at cap - this one will queue ({queued} ahead of it)")

    # Escape is handled explicitly rather than left to ImGui so that the precedence
    # against the layout shortcuts is stated in one place instead of split between
    # this and NavUpdate's behaviour.
    dismissed = cancelled or imgui.is_key_pressed(imgui.Key.escape)

    # ``enabled`` and not ``clicked`` alone: ``begin_disabled`` greys the button and
    # does nothing to the keyboard, so Ctrl+Enter in the task box is a second way into
    # this branch and a held launch has to be held on both.
    if enabled and (clicked or submitted):
        launch(state.spec())
        state.task = ""
        # Cleared with the task. A mode left on outlives the session it was set for,
        # and the next Ctrl+N is the one the operator types a task into without
        # re-reading the panel above the button -- which is 08-11's "forgets it is set
        # for the four they are not" happening inside one run rather than across two.
        state.dangerous = False
        # Cleared with the task, for the same reason the mode is: a number sized for
        # one piece of work outliving it means the next Ctrl+N silently carries a cap
        # the operator chose for something else. Back to None is back to the setting,
        # which is the answer they last gave deliberately.
        state.subagent_cap = None
        # Cleared with the task, and for the same reason it is: the pick has gone
        # somewhere. A pick that outlived its launch would silently resume the same
        # session again on the next Ctrl+N, and the header carrying it would be
        # collapsed while it did.
        state.picker.select(None, state.cwd)
        dismissed = True

    if dismissed:
        state.is_open = False
        imgui.close_current_popup()

    imgui.end_popup()
