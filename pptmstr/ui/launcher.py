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
import string
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import assert_never

from imgui_bundle import imgui

from .. import brief as brief_mod
from .. import cli_version, drawable, sandbox, sessionfile, templates, tree
from .. import sessions as sessions_mod
from .. import worktree as worktree_mod
from ..approval import Disposition, Policy, classify, requires_containment
from ..model import LaunchSpec, NewWorktree
from ..model_catalog import DEFAULT_MODEL, FALLBACK_MODELS
from ..sessions import Overlay, SessionRow
from ..theme import P, hint_colour
from . import widgets
from .widgets import Help, Notice, Tone

TITLE = "New Task"

# The rungs of the dial, read off the enum rather than listed, so a preset renamed
# or added in approval.py cannot leave a stale second copy here. The consequence is
# that every member is offerable at launch: a preset that must not be is a
# distinction `Policy` does not currently draw, and adding one means drawing it here
# too.
POLICIES: tuple[Policy, ...] = tuple(Policy)

# Ctrl+N. Key members are plain ints here and do not support ``|`` as enums, so the
# chord is built from int() -- ``imgui.Key.mod_ctrl | imgui.Key.n`` raises.
CHORD = int(imgui.Key.mod_ctrl) | int(imgui.Key.n)

_WIDTH = widgets.DIALOG_WIDTH
_TASK_HEIGHT = 132.0
_LIST_HEIGHT = 176.0

# ``###`` fixes the header's ID apart from its visible label, so rewording the
# header does not change which section a saved open state belongs to.
_RESUME_ID = "###resume"
# The scrolling child everything above the buttons is drawn in. Named because the
# window ImGui makes for it is found by this label (``scripts/screenshot.py``).
BODY_ID = "##body"
# The task box's label, named because two calls have to agree on it: the widget, and
# the lookup of that widget's text state in ``draw``.
_TASK_ID = "##task"

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
    would return nothing at all for the default.

    The two disagree wherever a worktree exists, this repository included:
    ``planning/2026-10-07-a-session-can-run-in-its-own-worktree.md`` measured five
    rows through the fold against one through the exact match. So a listing asked for
    one directory carries rows recorded in another, each with its own ``cwd``, and the
    directory that was scanned says nothing about where a picked row runs.
    ``launch_directory`` is what answers that, from the row.

    Errors are not caught here. ``enumerate_sessions`` refuses to turn an unreadable
    transcript tree into "no sessions", and swallowing that on the way past would
    undo it -- ``SessionPicker.poll`` renders the failure instead.
    """
    overlay = sessions_mod.load_overlay()
    rows = sessions_mod.enumerate_sessions(cwd=cwd, overlay=overlay, bookmarked_first=True)
    return rows, overlay


@dataclass(frozen=True, slots=True)
class FieldDirectory:
    """
    Nothing is picked, so the launch runs where the cwd field says. ``path`` is the
    field as typed, with only the empty case filled in.
    """

    path: str


@dataclass(frozen=True, slots=True)
class RecordedDirectory:
    """
    A session is picked and the directory its transcript was recorded in is there.
    The launch runs in it, whatever the cwd field says. ``path`` was its own
    ``realpath`` when this was read: no link stood anywhere along it.
    """

    path: str


@dataclass(frozen=True, slots=True)
class RedirectedDirectory:
    """
    A session is picked and the path it was recorded under is a directory only
    through a symbolic link: the path itself, or a component above it.

    ``path`` is where the link leads, which is where the launch runs, and
    ``recorded`` is the spelling the transcript carries. Nothing here can tell a
    session that was recorded through the link from one whose directory was replaced
    by a link afterwards, so neither is claimed; what is known is that the two
    spellings differ, and the screen says both.
    """

    path: str
    recorded: str


@dataclass(frozen=True, slots=True)
class GoneDirectory:
    """
    A session is picked and the directory it was recorded in is not a directory.
    ``path`` is the recorded one. Nothing launches -- see ``LauncherState.launch_hold``
    and ``resume_form.ResumeForm.hold``.
    """

    path: str


@dataclass(frozen=True, slots=True)
class UnrecordedDirectory:
    """
    A session is picked whose row carries no directory at all, so the cwd field is
    the only answer there is. ``path`` is the field, as for ``FieldDirectory``; it is
    a variant of its own because the screen has to say which of the two happened.
    """

    path: str


# Where a launch runs. Every variant carries the one ``path`` that is in force for
# it, so a caller that needs the directory reads ``.path`` and a caller that needs to
# know *why* it is that directory matches on the variant.
LaunchDirectory = (
    FieldDirectory | RecordedDirectory | RedirectedDirectory | GoneDirectory | UnrecordedDirectory
)


def launch_directory(row: SessionRow | None, field: str) -> LaunchDirectory:
    """
    Where a launch with ``row`` picked runs, given what the cwd field holds.

    **The one resolution.** The spec's ``cwd`` and ``session_base``, the brief
    directory a pick recovers, the own-checkout refusal and the text describing the
    sandbox's region all read this, because they are one fact: a resumed conversation
    continues in the tree it was having the conversation about. Nothing downstream
    enforces it. ``scan_sessions`` lists a worktree's sessions from the main checkout,
    and the CLI accepts a resume from any directory and says nothing, so a caller
    that reads the field for itself resumes that session *in the main checkout*.

    **A recorded directory that is gone is not replaced by the field.** The field
    names a tree the conversation was not about, and a resume there is the same
    wrong-tree resume with nothing on screen to show it. It is returned as its own
    variant and the launch is held on it.

    **A recorded path that leads somewhere else through a link is said to.** The
    launch runs where the link leads, because that is where the CLI would start it,
    and it is not held: the path may always have been a link, and then nothing has
    moved. But a path replaced by a link to another tree is the same wrong-tree
    resume the gone case is held for, so the variant carries both spellings and the
    line under the picker names the one the session runs in.

    **A row with no directory uses the field**, and is a variant so the screen can say
    so. Refusing would be the wrong answer here: nothing is known to be wrong, there
    is no recorded tree to be faithful to, and a refusal would leave the session
    unrecoverable from the one screen that exists to recover it. The SDK fills a
    missing ``cwd`` with the project directory it found the transcript under whenever
    it was asked about a directory, which is the only way ``scan_sessions`` asks, so
    on the picker's own route this variant is reached only through an injected scanner.

    One ``stat`` and one ``realpath`` when a row is picked and neither otherwise, so
    a draft nobody picked a session on touches no disk. The draw calls this every
    frame; that is the price of noticing a worktree removed, or a path relinked,
    while the modal is open, and it is an ``lstat`` per component of one path.
    """
    if row is None:
        return FieldDirectory(field)
    if not row.cwd:
        return UnrecordedDirectory(field)
    if not os.path.isdir(row.cwd):
        return GoneDirectory(row.cwd)
    real = os.path.realpath(row.cwd)
    # ``abspath`` and not the recorded string: a trailing separator or a ``..`` is a
    # different spelling of the same path, and no link is involved in either.
    if real != os.path.abspath(row.cwd):
        return RedirectedDirectory(real, row.cwd)
    return RecordedDirectory(real)


def resolved_directory(directory: LaunchDirectory) -> str:
    """
    The directory a launch in ``directory`` is started in, as the one string the
    session is handed.

    A picked session's directory was resolved when ``launch_directory`` read it, and
    is returned as read: resolving it again here would be a second look at a disk
    that can have moved since the first, and the own-checkout refusal and the spawn
    have to be about one look. The field is resolved here, because nothing has
    looked at it yet.

    Raises ``ValueError`` for a field ``realpath`` cannot take, a NUL in it.
    """
    match directory:
        case RecordedDirectory(path=path) | RedirectedDirectory(path=path):
            return path
        case FieldDirectory(path=path) | UnrecordedDirectory(path=path) | GoneDirectory(path=path):
            return os.path.realpath(path)
        case _:
            assert_never(directory)


@dataclass(frozen=True, slots=True)
class Recorded:
    """
    What a session's own file says it ran as, for the three things New Task would
    otherwise choose for it. Each is as the file has it: an empty ``model``, a
    ``template`` of None and a ``subagent_cap`` of None are a file that does not
    say, and are not solo, the default model or the setting.
    """

    model: str
    template: str | None
    subagent_cap: int | None


# Reading a session's record by its id, as a click on its row calls it. A
# parameter for the reason ``Scanner`` is one: the answer is on a disk a test
# should not depend on.
Recorder = Callable[[str], Recorded | None]


def read_recorded(session_id: str) -> Recorded | None:
    """
    The session's file (``sessionfile.read``), or None where there is none that
    can be read: a session older than the build that writes one, or one another
    tool started.

    One small file, read at the click that picks the row and never from a draw
    that did not pick one; ``sessionfile.read`` neither raises nor waits.
    """
    found = sessionfile.read(session_id)
    if not isinstance(found, sessionfile.SessionFile):
        return None
    return Recorded(found.model, found.template, found.subagent_cap)


# What a worker hands back: rows and overlay, or the message from the failure that
# stopped it. Exactly one side is populated.
_ScanResult = tuple[tuple[SessionRow, ...] | None, Overlay | None, str | None]

Scanner = Callable[[str], tuple[tuple[SessionRow, ...], Overlay]]


@dataclass
class SessionPicker:
    """
    The list of sessions that can be resumed: one cached scan, the operator's
    overlay, and a pick.

    **A click on a row in New Task is a pick, and New Task resumes it.** The modal
    stays up with the row lit, the task box becomes the message the session is sent,
    and the launch button starts ``LauncherState.press``'s resume of it. The resume
    form (``ui/resume_form``) is a closed card's and never opens from here; it asks
    ``pick`` only where a session's brief is.

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
    # The picked row, as it read when it was picked, or None. The row and not its id,
    # and never looked up again in ``rows``: every open rescans, a rescan can carry
    # the same id recorded under another directory or not carry it at all, and the
    # row the operator read and clicked is the one that says where the resume runs.
    # A rescan is not an act of theirs.
    picked: SessionRow | None = None
    # The brief directory the picked session wrote its premises into, if it exists.
    #
    # Recoverable, and not a guess: `brief.session_dir` is a function of the
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
        Pick a session to resume by its id, or clear the pick. Re-picking the current
        id clears.

        An id with no row in the listing clears the pick rather than setting one.
        Where a resume runs is read off the row, so a pick without one would be a
        resume with nothing to say where. An id the listing carries twice is answered
        with the first row, which is why a click does not come through here: see
        ``pick``.
        """
        wanted = None if session_id is None or session_id == self.selected else session_id
        self.pick(next((row for row in self.rows if row.session_id == wanted), None), cwd)

    def is_picked(self, row: SessionRow) -> bool:
        """
        Whether ``row`` is the pick: the same session recorded in the same directory.

        Not the id alone, because the listing can carry one id under two directories
        and only one of those rows is where the resume runs. Not the whole row,
        because a rescan restamps a row the operator would still call the one they
        picked.
        """
        return (
            self.picked is not None
            and row.session_id == self.picked.session_id
            and row.cwd == self.picked.cwd
        )

    def pick(self, row: SessionRow | None, cwd: str) -> None:
        """
        Pick ``row`` to resume, or clear the pick with None. Picking the row that is
        already the pick clears it.

        **The row and not its id, because the row is what was clicked.** The SDK can
        list one id under two directories, and a lookup by id answers with the first
        of them: a click on the second would resume in the first's directory, under a
        line naming a directory the operator did not choose.

        ``cwd`` is the cwd field, and it is only the answer for a row that recorded no
        directory. A brief written under the legacy layout is found from
        ``launch_directory`` -- the directory the resumed session will run in -- and
        ``app._seed_brief`` looks from ``LaunchSpec.cwd``, which
        ``LauncherState.resumed`` and ``resume_form.press`` take from the same call,
        so the two agree by construction instead of by coincidence.
        """
        self.picked = None if row is None or self.is_picked(row) else row
        self.recovered_brief = None
        if self.picked is None:
            return
        # Two stats at most, at the click. Offering a path to a directory that does
        # not exist would be the same lie as pre-filling the task.
        directory = brief_mod.existing_dir(
            self.picked.session_id, launch_directory(self.picked, cwd or ".").path
        )
        if directory is not None:
            self.recovered_brief = str(directory)

    @property
    def selected(self) -> str | None:
        """
        The id of the picked session, or None.
        """
        return None if self.picked is None else self.picked.session_id


# ---------------------------------------------------------------------------
# The dangerously autonomous mode, as the launcher sees it
# ---------------------------------------------------------------------------


def gitdir_pointer(text: str) -> str | None:
    """
    The path a ``.git`` *file* points at, or None when the text is not such a pointer.

    Pure. The file is one line, ``gitdir: <path>``, and the path may be relative to
    the directory holding the file -- a submodule's is.
    """
    first = text.split("\n", 1)[0]
    if not first.startswith("gitdir:"):
        return None
    return first[len("gitdir:") :].strip() or None


def _git_file(directory: str) -> tuple[str, str | None] | None:
    """
    What the ``.git`` *file* in ``directory`` says: the directory it points at,
    resolved, and the shared git directory that one's ``commondir`` names, resolved,
    or None for the second when it has no ``commondir``. None altogether when there
    is no such file or it is not a readable pointer.

    Total. ``ValueError`` is caught beside ``OSError`` because a pointer is bytes off
    a disk: one with a NUL in it is refused by ``open`` with a ``ValueError``, and the
    caller is a draw call.
    """
    dot_git = os.path.join(directory, ".git")
    try:
        if not os.path.isfile(dot_git):
            return None
        with open(dot_git, encoding="utf-8", errors="replace") as handle:
            pointer = gitdir_pointer(handle.read(4096))
        if pointer is None:
            return None
        # ``join`` discards ``directory`` for an absolute pointer, which is the usual
        # kind for a worktree.
        admin = os.path.join(directory, pointer)
        try:
            with open(
                os.path.join(admin, "commondir"), encoding="utf-8", errors="replace"
            ) as handle:
                common = handle.read(4096).strip()
        except OSError:
            common = ""
        shared = os.path.realpath(os.path.join(admin, common)) if common else None
        return os.path.realpath(admin), shared
    except (OSError, ValueError):
        return None


def shared_git_dir(directory: str) -> str | None:
    """
    The git directory a linked worktree at ``directory`` shares with its main
    repository, resolved, or None when ``directory`` is not the root of one.

    **Decided by the ``commondir`` file, not by the shape of the pointer.** A linked
    worktree's ``.git`` is a file pointing at ``<git dir>/worktrees/<name>``, and so,
    by shape, is a submodule checked out at ``worktrees/<name>`` -- its pointer ends
    ``modules/worktrees/<name>``. What git itself keys on is that a worktree's admin
    directory holds a ``commondir`` file naming the shared directory and nothing else's
    does: a submodule's and a ``--separate-git-dir`` repository's have none. Measured
    on git 2.39.5 for a worktree of an ordinary repository, of a bare one and of a
    submodule (present, ``../..``), and for a submodule, a submodule at ``worktrees/x``
    and a separate git dir (absent).

    Blocking, and small: one ``stat`` and at most two reads of a line each, with no
    subprocess. ``worktree.detect`` asks git and is the answer for a caller that is
    off the draw thread; this is for the one that is on it, behind a memo.

    Only the directory itself is asked about, and None is not "an ordinary
    directory": a subdirectory of a worktree has no ``.git`` of its own and answers
    None, and so does a pointer that cannot be read. ``git_layout`` is what says
    which of those a launch directory is.
    """
    read = _git_file(directory)
    return None if read is None else read[1]


def _reads_as_git_dir(path: str) -> bool:
    """
    Whether git would take the directory ``path`` as a repository's git directory.

    git asks three things of one and passes over a ``.git`` that fails any of them,
    carrying on to the parent without a word: an ``objects`` directory, a ``refs``
    directory, and a ``HEAD`` that is either ``ref:`` naming something under
    ``refs/`` or a full object id. Measured on git 2.39.5, each of the three removed
    in turn, and pinned by
    ``test_git_passes_over_a_git_directory_the_read_passes_over``.

    **Stricter than git where the two can differ, never looser.** A ``HEAD`` that is
    a symlink is one git still accepts and this does not. Answering no for a
    directory git would use sends the walk upward, where nothing it can find is told
    "only"; answering yes for one git passes over is what would be.

    Total, for the reason ``_git_file`` is.
    """
    try:
        if not os.path.isdir(os.path.join(path, "objects")):
            return False
        if not os.path.isdir(os.path.join(path, "refs")):
            return False
        with open(os.path.join(path, "HEAD"), encoding="utf-8", errors="replace") as handle:
            head = handle.read(4096).strip()
    except (OSError, ValueError):
        return False
    if head.startswith("ref:"):
        return head[len("ref:") :].lstrip().startswith("refs/")
    return len(head) in (40, 64) and all(c in string.hexdigits for c in head)


class WorktreeKind(Enum):
    """
    Which linked worktree a ``LinkedWorktree`` is, by where it sits and what it is a
    worktree of. One of the four was measured.
    """

    # Outside the main checkout of an ordinary repository: the worktree
    # ``planning/2026-10-07-a-session-can-run-in-its-own-worktree.md`` D2 ran its
    # contained sessions in, and the only one.
    BESIDE = "beside"
    # Inside the main checkout's own tree, which is where the CLI's ``--worktree``
    # puts one. D2 lists it as not tested.
    NESTED = "nested"
    # The main checkout's tree is inside the worktree's: a worktree added at a
    # directory above the repository it belongs to. Its root is not under the main
    # checkout, which is all ``NESTED`` asks, and it is not beside it either: the
    # checkout whose files D2 saw refused from outside lies within the session's
    # own directory here, and so does the shared git directory. No fixture.
    AROUND = "around"
    # The shared git directory is not a checkout's ``.git``: a worktree of a bare
    # repository, of a submodule or of a ``--separate-git-dir`` repository. D2 had no
    # fixture for any of them, and none of them has the main checkout its sentences
    # are about.
    UNORDINARY = "unordinary"


@dataclass(frozen=True, slots=True)
class LinkedWorktree:
    """
    The launch directory is a linked git worktree's root, or lies under one.

    ``root`` is the worktree's root and ``shared`` the git directory it shares with
    its main repository, both resolved. ``at_root`` is whether the launch directory
    is ``root`` itself. A contained session was measured in one place only: at the
    root of a worktree whose ``kind`` is ``BESIDE``.
    """

    shared: str
    root: str
    at_root: bool

    @property
    def main_checkout(self) -> str | None:
        """
        The main checkout this worktree belongs to, or None when the shared git
        directory is not a checkout's ``.git`` and there is none to name.
        """
        if os.path.basename(self.shared) != ".git":
            return None
        return os.path.dirname(self.shared)

    @property
    def kind(self) -> WorktreeKind:
        """
        Derived from the two paths, which are already resolved, so it costs no read.

        A bare repository that is itself a directory named ``.git`` reads as an
        ordinary one here. What that worktree is then told is D2's wide statement,
        which claims more reach for ``Bash`` than the unmeasured wording does and not
        less.
        """
        main = self.main_checkout
        if main is None:
            return WorktreeKind.UNORDINARY
        if self.root == main or self.root.startswith(main.rstrip(os.sep) + os.sep):
            return WorktreeKind.NESTED
        if main.startswith(self.root.rstrip(os.sep) + os.sep):
            return WorktreeKind.AROUND
        return WorktreeKind.BESIDE


@dataclass(frozen=True, slots=True)
class OutsideGit:
    """
    The git directory governing the launch directory lies outside it, and the layout
    is not a linked worktree: a submodule, a ``--separate-git-dir`` repository, a
    subdirectory of a checkout, or a pointer whose target has no ``commondir``.

    ``git_dir`` is that directory, resolved.
    """

    git_dir: str


@dataclass(frozen=True, slots=True)
class UnreadGit:
    """
    A ``.git`` entry stands at or above the launch directory that is neither a git
    directory git would use nor a readable pointer to one, and nothing above it
    answers instead. What governs the directory is not known from here.

    ``entry`` is that ``.git`` path: a file that is not a ``gitdir:`` line, or the
    nearest directory git passes over -- see ``_reads_as_git_dir``.
    """

    entry: str


# How git governs a launch directory, as far as it bears on what the containment
# section may say. None is the two layouts ``scripts/verify_sandbox_gate.py`` speaks
# for: a directory under no git at all, and a checkout or clone launched at its root
# with its ``.git`` directory inside it.
GitLayout = LinkedWorktree | OutsideGit | UnreadGit | None


def git_layout(directory: str) -> GitLayout:
    """
    Which git layout ``directory`` is in, found by walking up to the nearest ``.git``.

    **The walk is what keeps "only" off a directory nothing was measured for.**
    ``shared_git_dir`` answers for a directory that holds its own ``.git``; git
    answers for every directory beneath one, and a session launched in a
    subdirectory of a worktree is as much in that worktree as one launched at its
    root. So the nearest ``.git`` at or above ``directory`` that git would use decides,
    the way git's own discovery does, and anything but the two measured-narrow layouts
    is told apart from them.

    **A ``.git`` directory git would not use does not answer.** git passes over one
    that is empty or has no valid ``HEAD`` and keeps walking, so a directory holding
    such a thing is governed from above -- by a worktree or a checkout it is merely
    a subdirectory of -- and taking its ``.git`` at its word would tell it "only".
    The walk passes over it as git does. When nothing above answers either, the
    entry is reported as ``UnreadGit`` rather than as no git at all: a directory
    with a broken repository in it is not one the narrow measurement was taken for.

    A ``.git`` *file* that is not a readable pointer stops the walk, as it stops
    git's -- git refuses the directory outright rather than looking further up.

    A ``.git`` directory reached through a symlink is outside the checkout it
    governs and is reported as such. A bare repository's own directory and a
    repository named only by ``GIT_DIR`` have no ``.git`` entry to find and read as
    no git at all.

    Blocking, and bounded by the depth of the path: two ``stat`` calls a level, the
    reads ``_reads_as_git_dir`` makes at a level holding a ``.git`` directory and
    the ones ``shared_git_dir`` makes at a level holding a file, with no subprocess.
    For the draw thread behind a memo, as that is.

    Total. A directory that cannot be resolved at all answers None; no session
    starts in one, so nothing is claimed about it by the launch that would have.
    """
    try:
        start = os.path.realpath(directory)
    except (OSError, ValueError):
        return None
    here = start
    passed_over: str | None = None
    while True:
        dot_git = os.path.join(here, ".git")
        if os.path.isfile(dot_git):
            read = _git_file(here)
            if read is None:
                return UnreadGit(dot_git)
            admin, shared = read
            if shared is None:
                return OutsideGit(admin)
            return LinkedWorktree(shared=shared, root=here, at_root=here == start)
        if os.path.isdir(dot_git):
            if _reads_as_git_dir(dot_git):
                resolved = os.path.realpath(dot_git)
                if here == start and resolved == dot_git:
                    return None
                return OutsideGit(resolved)
            if passed_over is None:
                passed_over = dot_git
        parent = os.path.dirname(here)
        if parent == here:
            return None if passed_over is None else UnreadGit(passed_over)
        here = parent


@dataclass(frozen=True, slots=True)
class ContainedText:
    """
    What the gate's explanation says the contained half is bounded to.

    ``sandbox`` is what ``Bash`` reaches and ``gate`` is what the write tools are held
    to; they are two mechanisms and are said apart. ``wider`` is what is known, or
    known not to be known, about the sandbox's reach outside the session's directory;
    None for the layouts where the measurement is that there is none.
    """

    sandbox: str
    wider: str | None
    gate: str


# What ``planning/2026-10-07-a-session-can-run-in-its-own-worktree.md`` D2 measured
# from the root of a worktree beside its main checkout, at the strength it measured
# it. The counts are D2's and are said where a row is one run or two, so a single
# observation does not read as a rule. The denials are the named paths that were
# tried, and the one command, and not a boundary: D2's "What was not tested" lists
# other routes to the same hooks and config, and the sentence has to leave them open.
# The main checkout's files are said to be refused in the same breath as its branch
# being movable, because the second reads as the first.
_WORKTREE_MEASURED = (
    "the branches, HEAD and objects there, and its index in the one run that tried. "
    "That moves the branch the main checkout has out, and moved a second worktree's; "
    "the main checkout's own files were refused. It also wrote a new file at the top "
    "of that git directory (two runs, and a third by another spelling of the path), a "
    "rebase-merge/ directory there (one run) and a file in another worktree's admin "
    "directory (one run). Also refused, at the three paths tried: hooks/pre-commit, "
    "config, and the worktree's own config.worktree; and git config, refused on the "
    "config's lock in three runs. No route to the hooks or the config beyond those "
    "four was tried. Measured on CLI 2.1.292."
)

# The one layout D2 ran a contained session in, as every sentence about a layout it
# did not run one in names it. Both halves are load-bearing: "beside" because a
# worktree inside its main checkout is on D2's not-tested list, and "ordinary"
# because no fixture was a worktree of a bare repository or of a submodule.
_MEASURED_LAYOUT = (
    "the root of a linked worktree that sits beside the main checkout of an ordinary " "repository"
)


def _worktree_wider(layout: LinkedWorktree) -> str:
    """
    What is said about ``Bash``'s reach outside a linked worktree.

    **D2's findings are stated as this directory's own for one layout**: the root of
    a worktree whose ``kind`` is ``BESIDE``, which is where they were taken. Every
    other way of arriving at a ``LinkedWorktree`` is shown the same findings under a
    line saying they are not measured here, naming the layout they are from and the
    part of them that cannot carry over -- a nested worktree's directory is inside
    the checkout whose files were refused, a worktree around its main checkout has
    that checkout and the shared git directory inside its own, and a worktree of a
    bare repository or a submodule has no main checkout for the branch sentence to be
    about.
    """
    subject = (
        "that directory is a linked git worktree"
        if layout.at_root
        else f"that directory is inside a linked git worktree, {layout.root},"
    )
    match layout.kind:
        case WorktreeKind.BESIDE:
            where = (
                "that directory is a linked git worktree, and Bash also writes"
                if layout.at_root
                else (
                    f"that directory is inside a linked git worktree, {layout.root}. "
                    "Nothing was measured from a directory under one; from a worktree's "
                    "own root, Bash also writes"
                )
            )
            return (
                f"and outside it: {where} the git directory it shares with its main "
                f"repository, {layout.shared} -- {_WORKTREE_MEASURED}"
            )
        case WorktreeKind.NESTED:
            unlike = (
                f"{subject} that lies inside its main checkout, {layout.main_checkout}, "
                f"and shares the git directory {layout.shared} with it. No contained "
                "session was run in a worktree placed there. What follows was measured "
                f"from {_MEASURED_LAYOUT}, where the main checkout's files lay outside "
                "the session's directory; here they surround it, so their refusal is "
                "not known to hold"
            )
        case WorktreeKind.AROUND:
            unlike = (
                f"{subject} that has its main checkout, {layout.main_checkout}, inside "
                f"it, and with it the git directory the two share, {layout.shared}. No "
                "contained session was run in a worktree placed there. What follows was "
                f"measured from {_MEASURED_LAYOUT}, where the main checkout's files and "
                "that git directory lay outside the session's directory; here both lie "
                "within it, so neither their refusal nor the refusals inside the git "
                "directory are known to hold"
            )
        case WorktreeKind.UNORDINARY:
            unlike = (
                f"{subject} whose shared git directory, {layout.shared}, is not the "
                ".git of an ordinary checkout -- a bare repository's, a submodule's and "
                "a separate git directory's read this way. No contained session was run "
                f"in a worktree of one. What follows was measured from {_MEASURED_LAYOUT}"
                ", and what it says of a main checkout is about that repository's"
            )
        case _:
            assert_never(layout.kind)
    return (
        f"and outside it, not measured: {unlike}. In that layout Bash also wrote the git "
        f"directory that worktree shares with its main repository -- {_WORKTREE_MEASURED}"
    )


def contained_text(directory: LaunchDirectory, layout: GitLayout) -> ContainedText:
    """
    The contained half in words, for the directory the session will run in.

    ``layout`` is ``git_layout`` of that directory, and it decides whether the section
    may say ``Bash`` writes *only* inside it. That word is a measurement,
    ``scripts/verify_sandbox_gate.py``'s, and it was taken for a directory git does
    not reach outside of; it is said for that layout and for no other.

    **A linked worktree gets what D2 measured and no more.**
    ``planning/2026-10-07-a-session-can-run-in-its-own-worktree.md`` D2 measured, on
    CLI 2.1.292, a contained session at the root of a worktree beside its main
    checkout committing to the main repository, moving the branch its main checkout
    has out, rewriting its ``HEAD`` and deleting refs and objects -- and being
    refused three named paths, one ``git config`` and the main checkout's files. See
    ``_WORKTREE_MEASURED``. Sub-agents are on D2's not-tested list, so the fan-out
    sentence the narrow layout carries is replaced here by one saying so.

    **Any other worktree, and any directory under one, is shown the same measurement
    labelled as taken elsewhere.** Nothing was measured from there. The wide
    statement is the nearest one there is, and the narrow one would be a guess in
    the direction that costs. See ``_worktree_wider``.

    **Any other layout whose git directory lies outside the directory is told the
    boundary is not measured**, with that git directory named, so the operator can
    see what the unmeasured part is about. A ``.git`` that could not be read is
    named as that, and is not called a git directory or said to lie anywhere.
    """
    region = _region_phrase(directory)
    reach = (
        f"contained: Bash and anything it starts run in a sandbox that reaches "
        f"{sandbox.ALLOWED_DOMAIN} and nothing else"
    )
    held = (
        f"file writes are held to {region} alone by the gate rather than by the "
        "sandbox, because Write and Edit run in the CLI process -- a target that "
        "cannot be shown to be inside it is refused"
    )
    unmeasured = (
        f"{reach}, and writes inside {region}. Whether it writes anywhere else is not "
        "measured for this layout."
    )
    match layout:
        case None:
            return ContainedText(
                sandbox=(
                    f"{reach}, and writes only inside {region}. Every sub-agent gets that "
                    "same boundary, so a fan-out reaches no further than one agent does."
                ),
                wider=None,
                gate=(
                    "file writes are held to that same directory by the gate rather than "
                    "by the sandbox, because Write and Edit run in the CLI process -- a "
                    "target that cannot be shown to be inside it is refused."
                ),
            )
        case LinkedWorktree():
            return ContainedText(
                sandbox=(
                    f"{reach}, and writes inside {region}. Whether a sub-agent gets that "
                    "same boundary was not measured for a worktree."
                ),
                wider=_worktree_wider(layout),
                gate=f"{held}, the shared git directory included.",
            )
        case OutsideGit(git_dir=git_dir):
            return ContainedText(
                sandbox=unmeasured,
                wider=(
                    f"not measured: the git directory governing that directory lies "
                    f"outside it, {git_dir}, and no contained session was run in a layout "
                    f"like that except at {_MEASURED_LAYOUT} -- where Bash also wrote "
                    "the git directory the worktree shares with its main repository (CLI "
                    "2.1.292). Whether Bash reaches this one, and what a sub-agent gets, "
                    "is not known."
                ),
                gate=f"{held}, that git directory included.",
            )
        case UnreadGit(entry=entry):
            return ContainedText(
                sandbox=unmeasured,
                wider=(
                    f"not measured: {entry} could not be read as a git directory or as "
                    "a pointer to one, so what git makes of that directory is not known "
                    "here, and no contained session was run in one like it. The nearest "
                    f"layout measured is {_MEASURED_LAYOUT} -- where Bash also wrote the "
                    "git directory the worktree shares with its main repository (CLI "
                    "2.1.292). Whether Bash writes outside this directory, and what a "
                    "sub-agent gets, is not known."
                ),
                gate=f"{held}.",
            )
        case _:
            assert_never(layout)


# The tree this pptmstr is running out of. Resolved once at import because it cannot
# move while the process does, and from the package rather than from the process's
# directory so it names pptmstr's own source wherever pptmstr was started.
#
# ``parents[1]`` is the package directory -- this file is ``pptmstr/ui/launcher.py``
# -- and ``source_tree_of`` widens that to the checkout holding it when there is one.
# The widening is the load-bearing half: what needs protecting is not only
# ``approval.py`` but everything beside it that decides what the next launch does.
OWN_CHECKOUT = tree.source_tree_of(str(Path(__file__).resolve().parents[1]))

# ``{named}`` is the directory the refusal is about, in the words the modal uses for
# it. It is a slot because that is not always the cwd field: with a session picked the
# launch runs in the session's own directory, and a refusal that said "this
# directory" under a field naming somewhere else would send the operator to correct
# the one directory that is not the problem.
#
# ``{consequence}`` is what the refusal does to the launch, which is not one thing
# either: a new session is started under the ordinary gate, and a resume is held.
_REFUSAL = (
    "refused for {named} -- {consequence}.\n"
    "it is inside pptmstr's own checkout, or it is a path that cannot be resolved at "
    "all; the check answers yes to both and cannot tell them apart.\n"
    "under this mode that directory is where an agent writes without being asked, and "
    "pptmstr's own approval.py is in this checkout."
)


REFUSAL_DOWNGRADES = "this launch will use the ordinary gate"
REFUSAL_HOLDS = "the resume is held: choose another gate to launch it"


def refusal_for(
    cwd: str,
    *,
    checkout: str = OWN_CHECKOUT,
    named: str = "this directory",
    consequence: str = REFUSAL_DOWNGRADES,
) -> str | None:
    """
    Why the mode cannot be used for ``cwd``, or None when it can.

    ``named`` is how the message refers to ``cwd`` and ``consequence`` what it says
    becomes of the launch. The decision reads neither.

    A contained ``Bash`` writes inside the session's cwd -- and, where cwd is a linked
    git worktree, also inside the git directory that worktree shares with its main
    repository (``planning/2026-10-07-a-session-can-run-in-its-own-worktree.md`` D2,
    measured on CLI 2.1.292). Three paths under that directory were tried and refused
    -- ``hooks/pre-commit``, ``config`` and the worktree's ``config.worktree`` -- and
    that is a statement about those three paths: D2 lists the other routes to the
    hooks and the config as not tried. The write tools are held to cwd alone, by the
    gate.

    The sandbox's protected paths cover ``.claude/**``, ``.mcp.json``, ``.git/hooks``
    and ``.git/config`` -- not ordinary project source. ``cwd`` defaults to ``"."``,
    so the default launch would hand an agent that runs ``Bash`` unattended write
    access to the ``approval.py`` that gates the *next* launch. The alternative
    boundary is "the operator typed a different directory", enforced by nothing.

    **The message names two situations because the predicate cannot separate them.**
    ``tree.lies_inside_checkout`` answers yes both to a directory inside the checkout
    and to a path with no location at all -- a symlink loop, a ``~user`` with no home
    -- and that collapse is deliberate there: a yes costs a refusal the operator can
    see and correct, a no hands an unlocatable directory to an agent that writes
    unattended. A refusal naming only the checkout would be false for the second, and
    would send an operator to move a directory when what they have is a typo.

    ``checkout`` is a parameter so the decision can be exercised against a tree a test
    controls rather than against wherever this file happens to be installed.

    Total, and a path the predicate cannot take at all -- one with a NUL in it raises
    ``ValueError`` out of the resolve -- is the second of those situations, so it is
    refused with the rest. The callers are a draw call and a press of launch, and
    the other answer would be the mode granted for a directory nobody could name.
    """
    try:
        refused = tree.lies_inside_checkout(cwd.strip() or ".", checkout)
    except ValueError:
        refused = True
    return _REFUSAL.format(named=named, consequence=consequence) if refused else None


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

    **Read once, not once per launch.** The binary the SDK spawns cannot change under
    a running process in a way this application would notice, and the alternative puts
    a subprocess behind a checkbox that gets ticked and unticked while the operator
    reads the paragraph beside it.

    The one thing that does count as noticing is a launch that reports back that it
    started nothing: ``LauncherState.report_failure`` replaces the gate with an
    unanswered one, so an answer of "fine" is not what a draft refused on the far side
    of it is offered again under.
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


# Why the launch button is held. Each is behind the question mark of the line
# drawn beside the button (``hold_notice``).
_HOLD_CHECKING = "confirming this machine's CLI can contain the session"
_HOLD_REFUSED = "this CLI cannot contain the session -- untick the mode to launch"
_HOLD_THIN = "the task box is this session's whole brief -- give it more than one line"
# Behind the gate's question mark, after the description of what the mode grants:
# the mode is asked for and the directory allows it, and only the machine is still
# unanswered.
_HOLD_LINE = (
    "confirming the installed CLI is new enough to honour those keys -- launch is held "
    "until it answers"
)

# Why a resume is held, in the words drawn beside or under its button. New Task's
# picked session and the resume form say the same thing for the same reason, so the
# strings live with the function that chooses between them (``resume_gate_hold``).
#
# ``{rung}`` is the rung on the dial. The two situations are named together because
# ``refusal_for`` cannot tell them apart; see its docstring.
RESUME_HOLD_REFUSED = (
    "{rung} is refused here: the directory is inside pptmstr's own checkout, or cannot "
    "be resolved at all -- choose another gate to resume"
)
RESUME_HOLD_CHECKING = _HOLD_CHECKING
RESUME_HOLD_CLI = "this CLI cannot contain the session -- choose another gate to resume"
# New Task's own, for a picked session. The form's sentence for the same directory
# has no list to send the operator back to.
_HOLD_GONE = "the picked session's directory is gone -- pick another, or launch fresh"
# A branch is typed under the cwd field (``plan_worktree``).
_HOLD_READING = "reading the working directory's checkout to place the worktree"
_HOLD_NOT_A_REPO = (
    "the working directory is not in a git repository, so no worktree can be made from "
    "it -- clear the branch to launch there"
)

# What is drawn beside a held button: one line for each reason above, which is
# then the explanation behind that line's question mark. Keyed by the reason and
# not parsed out of it. ``_HELD`` is for a reason with no line of its own.
_HELD = "held"
HOLD_LINES: dict[str, str] = {
    _HOLD_CHECKING: "held: checking this machine's CLI",
    _HOLD_REFUSED: "held: this CLI cannot contain it",
    _HOLD_THIN: "held: the task is one line",
    RESUME_HOLD_CLI: "held: this CLI cannot contain it",
    _HOLD_GONE: "held: its directory is gone",
    _HOLD_READING: "held: reading the checkout",
    _HOLD_NOT_A_REPO: "held: not a git repository",
    **{
        RESUME_HOLD_REFUSED.format(rung=rung.value): f"held: {rung.value} is refused here"
        for rung in Policy
    },
}


def hold_notice(
    reason: str, *, what: str = "launch", lines: dict[str, str] | None = None
) -> Notice:
    """
    A held button's reason as the one line beside it, with the reason in full
    behind its question mark. ``what`` is the button's word for what is held and
    ``lines`` a surface's own reasons, read before ``HOLD_LINES``.
    """
    line = (lines or {}).get(reason) or HOLD_LINES.get(reason, _HELD)
    return Notice(line, Tone.WARN, Help("hold", f"why {what} is held", (reason,)))


def resume_gate_hold(policy: Policy, refusal: str | None, gate: VersionGate) -> str | None:
    """
    Why a resume under ``policy`` may not start, as far as its gate goes, or None.

    ``refusal`` is ``refusal_for``'s answer for the directory the resume is started
    in and ``gate`` the reading of this machine's CLI. Neither is consulted for a
    rung that needs no containment.

    **A rung the directory is refused for holds a resume and does not downgrade
    it**, which is where a resume departs from a new session. ``LauncherState.spec``
    downgrades because the directory is a field on screen, one keystroke from
    correct. A resumed session's directory is the one it was recorded in, so there
    is nothing to correct, and the way past is the dial: a lower rung is a decision
    the operator makes and it is not made for them.

    **The one place this is decided.** New Task's picked session and the resume
    form both ask here (``LauncherState.launch_hold``, ``ResumeForm.hold``), and
    ``resume_spec`` asks again before it builds anything.
    """
    if not requires_containment(policy):
        return None
    if refusal is not None:
        return RESUME_HOLD_REFUSED.format(rung=policy.value)
    if not gate.answered:
        return RESUME_HOLD_CHECKING
    return RESUME_HOLD_CLI if gate.refusal is not None else None


@dataclass(frozen=True, slots=True)
class ResumeHeld:
    """A resume that starts nothing, and the reason in the words drawn by its button."""

    reason: str


def resume_spec(
    *,
    session_id: str,
    cwd: str,
    policy: Policy,
    refusal: str | None,
    gate: VersionGate,
    task: str,
    task_is_label: bool,
    message: str,
    model: str,
    template: str | None,
    brief: str | None,
    subagent_cap: int | None,
) -> LaunchSpec | ResumeHeld:
    """
    The spec that continues ``session_id`` in ``cwd`` under ``policy``, or why
    nothing is started.

    **Every resume's spec is built here, whichever surface pressed it**, so the
    rung and the containment are a pair on all of them: both or neither, and the
    pair is granted by ``resume_gate_hold`` on the ``refusal`` and ``gate`` the
    caller read at its press. A caller cannot be handed a contained spec for a
    directory it was refused for by forgetting to ask first.

    ``cwd`` is the directory as the session is started in it, already resolved
    (``resolved_directory``); whether a resume starts in that directory at all is
    the caller's question, because the two surfaces answer it differently for a
    session that recorded none.

    **The message does not become the session's title.** ``task`` is what the
    announce puts on the record, so it is the title the card has, with
    ``task_is_label`` saying when that is only the CLI's label for the
    conversation, and ``message`` is what the session is sent
    (``AgentSession.run``).
    """
    held = resume_gate_hold(policy, refusal, gate)
    if held is not None:
        return ResumeHeld(held)
    return LaunchSpec(
        task=task,
        task_is_label=task_is_label,
        model=model,
        cwd=cwd,
        session_base=tree.session_base(cwd),
        template=template,
        brief=brief,
        resume=session_id,
        containment=sandbox.containment_settings() if requires_containment(policy) else None,
        policy=policy,
        subagent_cap=subagent_cap,
        message=message,
    )


# Why the cap is on this screen at all: once spawns auto-approve it is the only bound
# on fan-out the policy cannot widen, because `driver._gate_tool_use` denies at cap
# *before* it calls `classify`. Every other bound the gate's explanation names is something
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


# ---------------------------------------------------------------------------
# A new session in a linked worktree
# ---------------------------------------------------------------------------
#
# ``planning/2026-10-15-ui-rework.md`` W1-W7, on the 2026-10-07 worktree record's D3
# and D4. A branch typed under the cwd field asks for the session to run in a new
# worktree of the checkout the field names, at ``worktree.place``'s path, on that
# branch, from that checkout's ``HEAD``. Empty, nothing below is consulted and the
# launch is the one it was before the field existed.
#
# Every git call is off the draw thread: the checkout is read on a worker
# (``CheckoutReading``, the arrangement ``VersionGate`` and ``SessionPicker`` use) and
# the worktree is made in ``app._launch``'s ``go`` (``make_worktree``). What is decided
# from git's answers -- the hold, the path, the words for a refusal -- is pure.

# What a worktree is based on (W1): the HEAD of the checkout the field names, as
# the object id it named when the checkout was read. No fetch and no field for it.

# How many hex digits of an object id the modal shows.
_SHORT_OID = 12


@dataclass(frozen=True, slots=True)
class CheckoutRead:
    """
    What the directory a branch-filled draft names was to git, read on a worker.

    ``directory`` is the cwd field as the read was asked for it, unresolved, so the
    draw can tell whether this answers the field it is showing by a comparison
    rather than a stat. Whether the checkout has uncommitted changes is not here:
    it is asked after the press (``make_worktree``), and ``read_checkout`` says why.

    For a checkout a worktree can be made from, the rest is everything
    ``plan_worktree`` needs so that the draw touches no disk: ``head``, the commit
    its ``HEAD`` named; ``entries``, the worktrees git had registered;
    ``placed_in``, ``worktree.placed_under`` resolved, so a placed path is the
    spelling git lists; ``shared``, the main repository's ``.git`` resolved; and
    ``below``, the part of ``directory`` under the checkout's root when the field
    names a subdirectory.
    """

    directory: str
    checkout: worktree_mod.Checkout
    head: str | worktree_mod.BaseNotACommit | worktree_mod.GitFailed | None = None
    entries: tuple[worktree_mod.WorktreeEntry, ...] | worktree_mod.GitFailed = ()
    placed_in: str | None = None
    shared: str | None = None
    below: str | None = None


CheckoutReader = Callable[[str], CheckoutRead]


def read_checkout(directory: str) -> CheckoutRead:
    """
    ``worktree.detect`` of ``directory`` and, for a checkout a worktree can be made
    from, its ``HEAD`` as a commit and the worktrees git has registered.
    **Blocking**; for a worker only.

    **No** ``status``. This is asked as soon as a branch is typed, before any press,
    of a directory that may be a session's: a linked worktree whose ``.git`` names a
    repository the session built, or a repository it ``git init``-ed, which is a main
    checkout like any other. ``status`` runs that repository's ``clean`` filters,
    and no narrower question that sees a modified file avoids them (measured,
    ``worktree.uncommitted``). ``rev-parse`` and ``worktree list`` were not seen to
    run anything.
    """
    checkout = worktree_mod.detect(directory)
    if not isinstance(checkout, worktree_mod.MainCheckout | worktree_mod.LinkedWorktree):
        return CheckoutRead(directory, checkout)
    main_root = (
        checkout.root if isinstance(checkout, worktree_mod.MainCheckout) else checkout.main_root
    )
    here = os.path.realpath(directory)
    return CheckoutRead(
        directory,
        checkout,
        head=worktree_mod.head_commit(checkout.root),
        entries=worktree_mod.list_worktrees(checkout.root),
        placed_in=os.path.realpath(worktree_mod.placed_under(checkout)),
        shared=os.path.realpath(os.path.join(main_root, ".git")),
        below=None if here == checkout.root else os.path.relpath(here, checkout.root),
    )


@dataclass
class CheckoutReading:
    """
    The last ``CheckoutRead`` of the cwd field, and the one in flight.

    ``VersionGate``'s arrangement: a worker that touches only the queue, and a
    ``poll`` from the draw that is the only writer of the fields. At most one read
    is in flight. A field edited while one is out is read again once it lands, so
    a burst of keystrokes costs two reads and not one per key.

    ``forget`` drops the answer and whatever is in flight, as ``begin_open`` drops
    the git layout: a worktree made or a commit taken since the last open changes
    the answer and not the field. A read that lands after it was forgotten is
    discarded by its generation.
    """

    read: CheckoutRead | None = None
    asked: str | None = None
    _generation: int = 0
    _inbox: queue.SimpleQueue[tuple[int, CheckoutRead]] = field(
        default_factory=queue.SimpleQueue, repr=False
    )

    def want(self, directory: str, *, reader: CheckoutReader) -> threading.Thread | None:
        """
        Ask for ``directory`` to be read, unless it is already answered or a read
        is in flight. Returns the worker so a test can join it; the draw must not.
        """
        if self.asked is not None or (self.read is not None and self.read.directory == directory):
            return None
        self.asked = directory
        generation, inbox = self._generation, self._inbox

        def work() -> None:
            try:
                answer = reader(directory)
            except Exception as exc:  # noqa: BLE001 - surfaced as an answer, not swallowed
                # ``detect`` does not raise. Caught at the thread boundary anyway: an
                # exception escaping here would leave ``asked`` set for good, which
                # is a launch held on "reading" with nothing ever arriving.
                answer = CheckoutRead(
                    directory, worktree_mod.Unclassified(f"{type(exc).__name__}: {exc}")
                )
            inbox.put((generation, answer))

        worker = threading.Thread(target=work, name="pptmstr-checkout-read", daemon=True)
        worker.start()
        return worker

    def poll(self) -> None:
        """Take a read that has landed. A failed queue read otherwise."""
        while True:
            try:
                generation, answer = self._inbox.get_nowait()
            except queue.Empty:
                return
            if generation != self._generation:
                continue
            self.asked = None
            self.read = answer

    def forget(self) -> None:
        self._generation += 1
        self.read = None
        self.asked = None

    def answer_for(self, directory: str) -> CheckoutRead | None:
        """The read of ``directory``, or None when the last one was of another."""
        read = self.read
        return read if read is not None and read.directory == directory else None


@dataclass(frozen=True, slots=True)
class WorktreeHeld:
    """A branch is typed and no worktree can be planned yet, and why."""

    reason: str


# Beside the button when a reason ``plan_worktree`` gives has no line of its own
# in ``HOLD_LINES``: those name the directory or git's words, and are behind the
# question mark whole.
_HOLD_LINE_WORKTREE = "held: no worktree from this directory"


def plan_worktree(
    read: CheckoutRead | None, branch: str, unfinished: Mapping[str, str] | None = None
) -> NewWorktree | WorktreeHeld:
    """
    The worktree a draft with ``branch`` typed launches into, given the read of its
    directory, or why the launch is held. Pure: everything on disk it answers from
    was read on the worker (``read_checkout``), and what this process left
    unfinished is handed in (``UnfinishedWorktrees.snapshot``).

    **The path is final here**, before anything is on disk, because the spec's
    ``cwd`` and ``session_base`` are resolved from it on the draw thread and three
    things read the cwd before a session process exists (the 2026-10-07 record,
    "Invoke"). It is ``worktree.place``'s slug under the resolved
    ``worktree.placed_under``, so it is the spelling git lists the worktree under.

    **Nothing already there is continued in** (W3, as the lead revised it on
    wt-fix-2). A worktree git has registered at the path, or one with the branch
    checked out, holds the launch. Where nothing marks it unfinished, the line says
    that it may be incomplete and how to start a session there instead -- set the
    directory to it and leave the branch empty, which is the launch the modal made before the branch
    field existed -- and otherwise it names the state (``_already_there``). git does not record
    whether the add that made a worktree finished -- one stopped at its limit in
    ``post-checkout`` is registered, unlocked and on its branch while the hook still
    writes (measured, ``tests/test_launch_worktree.py``) -- so no reading of the
    listing can tell a finished tree from a partial one, and a launch with a branch
    typed only ever starts in a tree it has just made.

    A linked worktree is a checkout like any other here (W7): the branch is made
    from its ``HEAD`` and placed under the main repository's name. That name is
    ``LinkedWorktree.main_root``, which the directory claims for itself; the
    operator typed the directory, which is the ground for taking it.
    """
    if read is None:
        return WorktreeHeld(_HOLD_READING)
    checkout = read.checkout
    match checkout:
        case worktree_mod.NotARepository():
            return WorktreeHeld(_HOLD_NOT_A_REPO)
        case worktree_mod.Unclassified(reason=reason):
            return WorktreeHeld(f"no worktree can be made from this directory: {reason}")
        case worktree_mod.MainCheckout() | worktree_mod.LinkedWorktree():
            placed = worktree_mod.place(checkout, branch)
        case _:
            assert_never(checkout)
    if isinstance(placed, worktree_mod.NothingToName):
        return WorktreeHeld(
            f"the branch {branch!r} has no letter or digit to name a directory after"
        )
    head, entries = read.head, read.entries
    if isinstance(head, worktree_mod.BaseNotACommit):
        return WorktreeHeld(
            "the checkout's HEAD is not a commit -- a repository with no commits reads this way"
        )
    if isinstance(head, worktree_mod.GitFailed):
        return WorktreeHeld(f"the checkout's HEAD could not be read: {head.detail}")
    if isinstance(entries, worktree_mod.GitFailed):
        return WorktreeHeld(f"the worktrees git has registered could not be read: {entries.detail}")
    if head is None or read.placed_in is None:
        return WorktreeHeld(_HOLD_READING)
    path = os.path.join(read.placed_in, os.path.basename(placed.path))
    there = _registered(entries, path, placed.branch)
    if there is not None:
        return WorktreeHeld(_already_there(there, unfinished or {}))
    return NewWorktree(
        run_in=placed.run_in,
        main_root=placed.main_root,
        branch=placed.branch,
        path=path,
        base=head,
    )


def _registered(
    entries: tuple[worktree_mod.WorktreeEntry, ...], path: str, branch: str
) -> worktree_mod.WorktreeEntry | None:
    """The worktree git has at ``path``, else the one with ``branch`` checked out."""
    ref = f"refs/heads/{branch}"
    at_path = next((entry for entry in entries if entry.path == path), None)
    return at_path or next((entry for entry in entries if entry.branch == ref), None)


def _continue_there(path: str) -> str:
    """
    The offer of a listed tree to start a session in, worded for a tree nobody here
    can vouch for: git does not record whether the add that made it finished, and
    this process remembers only its own adds, and only until it exits.

    **It names no command to run in the tree.** A session that could write the tree
    can aim its ``.git`` at a repository it built (``worktree``'s module docstring),
    and then any git run there -- ``status`` included -- runs that repository's
    programs as the operator. That is why ``read_checkout`` asks no ``status`` before
    the press, and a hold line does not hand the same call to the operator's shell.
    """
    return (
        "it may be incomplete and was not made by this launch; to continue there, set "
        f"the directory to {path} and leave the branch empty"
    )


# After the " -- " of a hold that offers nothing, as ``_continue_there`` does, and
# for its reason naming nothing to run there.
_NOT_OFFERED = "nothing is offered there"


def _already_there(entry: worktree_mod.WorktreeEntry, unfinished: Mapping[str, str]) -> str:
    """
    Why a launch is held by ``entry``, a worktree git has at the placed path or on
    the branch, in one line that names it and, for a tree that may be complete, how
    to start a session in it.

    **No offer for a tree known not to be finished.** A locked entry is what an add
    in flight, or one killed there, leaves (``worktree.AddTimedOut``); an entry this
    process's own add left unfinished (``UnfinishedWorktrees``) may have a hook still
    writing into it. Those name their state and offer nothing.
    """
    if entry.prunable is not None:
        return (
            f"git has a worktree registered at {entry.path}, whose directory is gone -- "
            "nothing is made while it is registered"
        )
    on = "a detached HEAD" if entry.branch is None else entry.branch
    here = f"git already has a worktree at {entry.path} on {on}"
    state = unfinished.get(entry.path)
    if state is not None:
        return f"{here}: {state} -- {_NOT_OFFERED}"
    if entry.locked is not None:
        locked = f"locked {entry.locked!r}" if entry.locked else "locked"
        return f"{here}, {locked}, and whether it is complete is not known -- {_NOT_OFFERED}"
    return f"{here} -- {_continue_there(entry.path)}"


# What ``UnfinishedWorktrees`` says of a path, after "git already has a worktree at
# P on B: " or after "P: ".
UNFINISHED_MAKING = "this window is still making it"
UNFINISHED_STOPPED = "its creation was stopped and its state is not known"
UNFINISHED_LEFT = "its add failed after checking it out, and it was left as it is"
UNFINISHED_BRANCH_STOPPED = (
    "no worktree was added there, but making its branch was stopped, so the branch "
    "may exist now in a state not known"
)
UNFINISHED_RAISED = "making it ended in an error, and its state is not known"


def unfinished_state(outcome: worktree_mod.CreateOutcome) -> str | None:
    """
    What ``outcome`` leaves at its path that nothing may be started in unseen, or
    None when it leaves nothing there that is unknown: a finished tree, or none.
    """
    match outcome:
        case worktree_mod.AddTimedOut():
            return UNFINISHED_STOPPED
        case worktree_mod.BranchTimedOut():
            return UNFINISHED_BRANCH_STOPPED
        case worktree_mod.AddFailedWorktreeLeft():
            return UNFINISHED_LEFT
        case _:
            return None


class Mark:
    """One ``make_worktree`` call's hold on a path in ``UnfinishedWorktrees``."""

    __slots__ = ()


class UnfinishedWorktrees:
    """
    The paths whose ``create`` this process is running or ran and that it cannot
    call finished: one in flight, or one ended by an outcome that leaves an unknown
    (``unfinished_state``) or by an exception. Keyed by the placed path, which is
    the spelling ``git worktree list`` gives (``plan_worktree``).

    **A path is taken by one call at a time.** ``begin`` tests and sets in one step
    and hands the caller a ``Mark``; only the call holding the mark can change or
    drop the path's entry. A press that is refused because another one holds the
    path therefore cannot take the other's in-flight mark with it.

    **In this process only.** git keeps no such mark, so after a restart a tree
    left this way is an unlocked entry like any other and gets the general offer,
    which says it may be incomplete (``_continue_there``).

    Written on ``make_worktree``'s worker and read by the draw, so it is locked.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_path: dict[str, str] = {}
        self._held: dict[str, Mark] = {}

    def begin(self, path: str) -> tuple[Mark | None, dict[str, str]]:
        """
        Take ``path`` as in flight, unless anything is recorded for it, and the
        record as it stood before: ``(mark, record)``, the mark None when it was not
        taken.
        """
        with self._lock:
            before = dict(self._by_path)
            if path in self._by_path:
                return None, before
            mark = Mark()
            self._by_path[path] = UNFINISHED_MAKING
            self._held[path] = mark
            return mark, before

    def abandoned(self, path: str, mark: Mark) -> None:
        """``mark``'s call ended before ``create`` ran, having made nothing."""
        self._settle(path, mark, None)

    def ended(self, path: str, mark: Mark, outcome: worktree_mod.CreateOutcome) -> None:
        self._settle(path, mark, unfinished_state(outcome))

    def raised(self, path: str, mark: Mark) -> None:
        """``create`` raised under ``mark``: what it did on disk is not known."""
        self._settle(path, mark, UNFINISHED_RAISED)

    def _settle(self, path: str, mark: Mark, state: str | None) -> None:
        with self._lock:
            if self._held.get(path) is not mark:
                return
            del self._held[path]
            if state is None:
                del self._by_path[path]
            else:
                self._by_path[path] = state

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return dict(self._by_path)


UNFINISHED = UnfinishedWorktrees()
"""This process's record; ``make_worktree`` and ``LauncherState`` use it unless given another."""


def worktree_notices(plan: NewWorktree | WorktreeHeld, read: CheckoutRead | None) -> list[Notice]:
    """
    What the modal says under the branch field: where the session will run, or why
    it cannot, and -- once a press was held on them and the operator pressed again
    -- that the checkout's uncommitted changes are not carried.
    """
    if isinstance(plan, WorktreeHeld):
        tone = Tone.QUIET if plan.reason == _HOLD_READING else Tone.WARN
        return [Notice(widgets.author_text(plan.reason), tone)]
    where = widgets.author_text(plan.path)
    below = read.below if read is not None else None
    at_root = (
        f" -- at its root, not at {widgets.author_text(below)} below it"
        if below is not None
        else ""
    )
    said = [
        Notice(
            f"runs in a new worktree at {where}, from {plan.base[:_SHORT_OID]}{at_root}",
            Tone.QUIET,
            Help(
                "worktree",
                "where this session runs",
                (
                    widgets.author_text(
                        f"A new branch {plan.branch!r} is made at {plan.base}, the HEAD of "
                        f"{plan.run_in} when it was read, and checked out at {plan.path}, "
                        "before the session starts. Nothing is fetched."
                    ),
                    "If that HEAD has moved by the press, or anything is already there "
                    "-- the branch elsewhere, a tree at the path -- the launch is held "
                    "with the reason, and this draft comes back.",
                    "Uncommitted changes in the checkout are looked for after the press, "
                    "not before: git status runs programs the repository's config names. "
                    "Changes hold the launch once, and pressing again launches without "
                    "them.",
                ),
            ),
        )
    ]
    if plan.uncommitted_accepted:
        said.append(
            Notice(
                f"{widgets.author_text(plan.run_in)} has uncommitted changes -- "
                "this launch goes ahead without them",
                Tone.WARN,
            )
        )
    return said


def worktree_refusal(outcome: worktree_mod.CreateOutcome, branch: str, path: str) -> str | None:
    """
    Why a launch into a new worktree starts no session, in words, or None when the
    worktree is there to start in. Every variant ``worktree.create`` returns is
    named (W5), and a failure that left something on disk says what.
    """
    left_nothing = "nothing was left on disk"
    match outcome:
        case worktree_mod.Created():
            return None
        case worktree_mod.InvalidBranch(would_be=None):
            return f"git will not take {branch!r} as a branch name"
        case worktree_mod.InvalidBranch(would_be=would_be):
            return f"git reads {branch!r} as {would_be!r}, not as a new branch name"
        case worktree_mod.BranchExists():
            return (
                f"a branch named {branch!r} already exists -- name a new branch, or, to "
                "continue on it, set the directory to a worktree that has it checked out "
                "and leave the branch empty"
            )
        case worktree_mod.BaseNotACommit(base=base):
            return (
                f"the checkout's {base} is not a commit -- a repository with no commits "
                "reads this way"
            )
        case worktree_mod.PathExists():
            # Not a tree git lists, so there is nothing to continue in.
            return (
                f"{path} already exists and git does not list it as a worktree, "
                f"{left_nothing} -- {_NOT_OFFERED}"
            )
        case worktree_mod.PathRegistered(missing=missing):
            gone = ", whose directory is gone" if missing else ""
            return f"git has another worktree registered at {path}{gone}"
        case worktree_mod.SlugTaken(other=other) | worktree_mod.SlugUnjudged(other=other):
            return (
                f"the worktree at {other} would share its transcript directory with {path} "
                "-- choose a branch name that differs in more than punctuation"
            )
        case worktree_mod.GitFailed(step=step, returncode=code, detail=detail):
            return f"git {step} failed ({_exit(code)}): {detail}"
        case worktree_mod.BranchTimedOut(seconds=seconds):
            return (
                f"git branch was stopped after {seconds:g}s -- the branch {branch!r} may "
                "exist now and was not removed"
            )
        case worktree_mod.AddFailed(returncode=code, detail=detail):
            return (
                f"git worktree add failed ({_exit(code)}): {detail} -- the branch was "
                f"removed again and {left_nothing}"
            )
        case worktree_mod.AddFailedWorktreeLeft(returncode=code, detail=detail):
            return (
                f"git worktree add failed ({_exit(code)}): {detail} -- the worktree was "
                f"left registered at {path} with {branch!r} checked out; nothing was removed"
            )
        case worktree_mod.AddFailedPathHeld(
            held_by=held_by, returncode=code, detail=detail, kept=kept
        ):
            theirs = "a detached HEAD" if held_by is None else held_by
            ours = "was removed again" if kept is None else f"was left: {kept}"
            return (
                f"git worktree add failed ({_exit(code)}): {detail} -- another worktree, on "
                f"{theirs}, is registered at {path} and was not touched; the branch "
                f"{branch!r} {ours}"
            )
        case worktree_mod.AddFailedBranchLeft(returncode=code, detail=detail, kept=kept):
            return (
                f"git worktree add failed ({_exit(code)}): {detail} -- no worktree is at "
                f"{path}, and the branch {branch!r} was left: {kept}"
            )
        case worktree_mod.AddTimedOut(seconds=seconds):
            return (
                f"git worktree add was stopped after {seconds:g}s -- the branch {branch!r} "
                f"and whatever is at {path} were left as they are, in a state not known"
            )
        case _:
            assert_never(outcome)


def _exit(code: int | None) -> str:
    return "no exit status" if code is None else f"exit {code}"


# After a refusal that is about the read being out of date: ``report_failure``
# forgets the read, and the modal reads the checkout again.
_READ_AGAIN = "nothing was made, and the modal reads the checkout again"


@dataclass(frozen=True, slots=True)
class WorktreeRefused:
    """``make_worktree`` made nothing a session may start in, and why."""

    reason: str


@dataclass(frozen=True, slots=True)
class UncommittedFound:
    """
    ``make_worktree`` was held on the checkout's uncommitted changes and nothing
    else, or on a ``status`` that could not say: the press that comes back accepting
    them launches (``LauncherState.report_failure``'s ``accept_uncommitted``).
    """

    reason: str


def make_worktree(
    plan: NewWorktree, unfinished: UnfinishedWorktrees | None = None
) -> WorktreeRefused | UncommittedFound | None:
    """
    Make the worktree ``plan`` asks for and return None, or return why no session
    may start. **Blocking**: for ``app._launch``'s worker, never the draw thread or
    the asyncio loop. ``unfinished`` is the process's record (``UNFINISHED``) unless
    another is given.

    **Only ever a new worktree** (W3 as revised on wt-fix-2; ``plan_worktree``
    argues it). The listing is read again first, so that a worktree that appeared
    at the path or on the branch since the read is refused in the modal's own words,
    and then ``preflight``, for a branch or a path that appeared without one; both
    before anything else is asked. ``create`` pre-flights again, and its answer is
    the one that holds.

    **Made at** ``plan.base`` **only while the checkout's** ``HEAD`` **still names
    it**: a ``HEAD`` that moved since the read means the modal showed a base that is
    no longer the checkout's, and the draft goes back to be read again.

    **Uncommitted changes are asked here, after the press**, and nowhere before it.
    ``git status`` runs programs the checkout's ``config`` names, and before the press
    that config may be a session's (``worktree.uncommitted``); here the press has
    already trusted it with ``create``'s hooks and filters. A checkout with changes,
    or one ``status`` could not read, is held once as ``UncommittedFound``, and the
    draft comes back accepting it (``plan.uncommitted_accepted``).

    **The path is taken in** ``unfinished`` **before any check**, and a press that
    finds it taken is refused there, so of two presses of one branch only one runs
    the checks and ``create``, and the other cannot clear the first's mark however
    it ends. The mark stays after an outcome that leaves the path unknown, or an
    exception out of ``create``, so neither this press nor the modal offers it as a
    place to continue (``_already_there``); it is dropped when the checks refuse,
    since nothing was made.

    Two presses of one branch from two processes need no shared record for the
    session's sake: ``git branch`` exits
    zero only for the call that created the ref, so of two creates one makes the
    branch and the other is refused by the pre-flight or by ``git branch`` itself,
    before any ``worktree add`` of its own. The second never starts a session in the
    first's tree, because nothing here starts one in a tree it did not make.

    Missing parents of ``~/.pptmstr/worktrees/<repo>/`` are made by ``git worktree
    add`` itself (W4); nothing here makes a directory, and the checkout the plan
    was read from is never made.
    """
    record = UNFINISHED if unfinished is None else unfinished
    placement = worktree_mod.Placement(
        run_in=plan.run_in, main_root=plan.main_root, branch=plan.branch, path=plan.path
    )
    entries = worktree_mod.list_worktrees(plan.run_in)
    if isinstance(entries, worktree_mod.GitFailed):
        return _refused(worktree_refusal(entries, plan.branch, plan.path))
    mark, known = record.begin(plan.path)
    there = _registered(entries, plan.path, plan.branch)
    if there is not None:
        if mark is not None:
            record.abandoned(plan.path, mark)
        return WorktreeRefused(_already_there(there, known))
    if mark is None:
        # Not listed, and not finished either: whatever is at the path is not offered.
        return WorktreeRefused(f"{plan.path}: {known[plan.path]} -- {_NOT_OFFERED}")
    created = False
    outcome: worktree_mod.CreateOutcome | None = None
    try:
        held = _checked(plan, placement)
        if held is not None:
            return held
        created = True
        outcome = worktree_mod.create(placement, plan.base)
    finally:
        if outcome is not None:
            record.ended(plan.path, mark, outcome)
        elif created:
            record.raised(plan.path, mark)
        else:
            record.abandoned(plan.path, mark)
    return _refused(worktree_refusal(outcome, plan.branch, plan.path))


def _checked(
    plan: NewWorktree, placement: worktree_mod.Placement
) -> WorktreeRefused | UncommittedFound | None:
    """``make_worktree``'s checks before ``create``: why not to run it, or None."""
    cleared = worktree_mod.preflight(placement, plan.base)
    if not isinstance(cleared, worktree_mod.Cleared):
        return _refused(worktree_refusal(cleared, plan.branch, plan.path))
    head = worktree_mod.head_commit(plan.run_in)
    if head != plan.base:
        now = head[:_SHORT_OID] if isinstance(head, str) else worktree_refusal(head, "", "")
        return WorktreeRefused(
            f"the HEAD of {plan.run_in} is no longer {plan.base[:_SHORT_OID]}, the commit the "
            f"modal showed (now: {now}) -- {_READ_AGAIN}"
        )
    if not plan.uncommitted_accepted:
        dirty = worktree_mod.uncommitted(plan.run_in)
        if isinstance(dirty, worktree_mod.GitFailed):
            return UncommittedFound(
                f"whether {plan.run_in} has uncommitted changes could not be read: "
                f"{dirty.detail} -- nothing was made; press launch again to launch anyway"
            )
        if dirty:
            return UncommittedFound(
                f"{plan.run_in} has uncommitted changes, and the worktree will not have "
                "them -- nothing was made; press launch again to launch without them"
            )
    return None


def _refused(reason: str | None) -> WorktreeRefused | None:
    return None if reason is None else WorktreeRefused(reason)


@dataclass(frozen=True, slots=True)
class Draft:
    """
    What the operator had typed and chosen at the moment they pressed launch.

    Taken before the press clears anything, and handed to the ``launch`` callback
    beside the spec. The spec is not enough to put a draft back: it carries the task
    stripped, the directory resolved, the policy as *granted* rather than as asked
    for, and the template by name -- each a one-way step from what was on screen.

    ``picked`` is the session the press resumed, as the row that was clicked, or
    None for a new session. It travels because the press clears the pick: a failed
    resume put back without it would be its message in the task box of a draft
    that starts a new session.

    ``branch`` is the worktree branch field as typed, empty for a launch into the
    directory itself. A refused worktree comes back with it, so the retry is one
    edit away.

    ``uncommitted_accepted`` is the cwd field, as typed, of a draft whose press was
    held because that checkout had uncommitted changes (``UncommittedFound``), or
    None. ``report_failure`` puts it on the draft it hands back, so the next press
    of the same draft launches; a directory edited since is not the one accepted,
    and is asked about again.
    """

    task: str
    cwd: str
    brief: str
    model: str
    template_index: int
    policy: Policy
    subagent_cap: int | None
    picked: SessionRow | None = None
    branch: str = ""
    uncommitted_accepted: str | None = None


def left_by_launch(draft: Draft) -> Draft:
    """
    The fields as the press that launched ``draft`` left them.

    ``LauncherState.launched`` is the press and this is its result as a value: the
    task, the rung, the cap, the picked session and the worktree branch go, and the
    directory, brief, model and team stay. A second spelling of those clears, pinned to the first by
    ``test_what_a_launch_leaves_is_what_the_press_actually_left``. It is a value so
    that "has anything been typed since that launch" is a comparison of the fields
    against it rather than a guess from the task box alone.
    """
    return replace(
        draft,
        task="",
        policy=Policy.STRICT,
        subagent_cap=None,
        picked=None,
        branch="",
        uncommitted_accepted=None,
    )


@dataclass(frozen=True, slots=True)
class FailedLaunch:
    """
    A launch that did not start a session: where it came from, why, and its draft.

    ``draft`` is None for a launch the modal did not make -- a relaunch, a fork, a
    task named on the command line. Those have a reason to show and nothing to give
    back. ``origin`` is the word the notice uses for it, so a reason shown above the
    operator's own text is not read as being about that text.

    ``pressed_past`` is whether a launch has been pressed from the modal while this
    one was waiting. The modal reopens over such a press with this failure on it, and
    a notice reading "that launch did not start" would then be read as the answer to
    the press just made -- which has not been answered, and may have started.
    """

    draft: Draft | None
    reason: str
    origin: str = "launch"
    pressed_past: bool = False


@dataclass
class LauncherState:
    """
    Draft intent for a session that does not exist yet.

    The draft survives a cancel on purpose: dismissing the modal to go look up a
    directory should not cost the paragraph already typed. It is cleared on launch,
    which is the only point at which the intent has gone somewhere -- and a launch
    that turns out to have gone nowhere hands it back; see ``report_failure``.
    """

    task: str = ""
    cwd: str = "."
    # Path to a directory of premises, or empty for a session with only its task.
    # Optional on purpose: most sessions are solo with a one-line task, and making
    # a brief mandatory would tax the common case for a problem it does not have.
    brief: str = ""
    # The model id itself and not an index into the offered list, for the reason
    # ``policy`` below holds a ``Policy``: the list is probed at startup and ordered by
    # the API, so a position means a different model between one run and the next. A
    # name also gives a pinned id somewhere to live when the probe fails and the list
    # falls back -- an index has no way to express a model the list does not contain,
    # and ``--model`` has always accepted one.
    model: str = DEFAULT_MODEL
    # Index into templates.names(). "solo" is first and is the default, so the
    # launcher behaves exactly as it did before teams existed unless asked otherwise.
    template_index: int = 0
    # The gate rung this launch asks for. The value and not an index into
    # ``POLICIES``, so reordering the enum cannot silently move the default off
    # ``STRICT`` -- the one default worth being unable to change by accident.
    #
    # What was *asked for*, which at the top rung is not the same question as what
    # this launch gets -- see ``granted_policy`` and ``dangerous_engaged``. Held as
    # asked rather than as granted so that a refusal does not rewrite the operator's
    # answer behind them: correcting the directory engages the mode without a second
    # trip to the dial, and the refusal on screen is what says which of the two states
    # they are in.
    #
    # Nothing here is written to ``Settings``. 08-11 rejected a persisted toggle
    # because "the operator sets it for the session they are watching and forgets it
    # is set for the four they are not", and ``draw`` resets this at the launch for the
    # same reason one process's fifth session is as unwatched as the next run's first.
    policy: Policy = Policy.STRICT
    # True from the frame the modal is drawn until the frame it stops being drawn.
    # Read by the global key handler, which runs before any drawing and therefore
    # sees the previous frame's value -- which is the correct one, because the key
    # it is deciding about was pressed while that frame was on screen.
    is_open: bool = False
    # The list of sessions the modal offers to resume, and the one picked in it.
    # With a pick this draft is a resume: the task box is the message, the session
    # runs where its row says, and ``press`` builds ``resumed``'s spec.
    picker: SessionPicker = field(default_factory=SessionPicker)
    # An override of ``Settings.subagent_cap`` for this launch, or None to use the
    # setting. None is the untouched default, so a draft nobody edited produces the
    # spec it produced before the control existed.
    #
    # Not mode-specific, and the control is drawn under every rung: the
    # cap bounds fan-out under every policy, and the operator asked for an override at
    # launch rather than an override under the dangerous mode. What the mode changes is
    # only how much rests on it -- see ``_CAP_REASON``.
    subagent_cap: int | None = None
    # A new branch to run this session on, in a new linked worktree of the checkout
    # the cwd field names; empty for a launch into that directory, which is every
    # launch made before the field existed. See ``plan_worktree``. Not for a picked
    # session: a resume runs in its recorded directory.
    branch: str = ""
    # ``Draft.uncommitted_accepted``: the cwd field whose uncommitted changes a held
    # press of this draft was told of, or None.
    uncommitted_accepted: str | None = None
    # The cwd field's directory as git reads it, for a draft with a branch typed.
    checkout: CheckoutReading = field(default_factory=CheckoutReading)
    _open_requested: bool = False
    _focus_task: bool = False
    # The refusal computed for a launch directory, and the directory it was computed
    # for. The draw asks every frame and the answer resolves two paths through the
    # filesystem -- a stat per component on each -- so it is memoised against
    # ``launch_directory()``, which carries the raw field and is the thing that
    # changes. One resolve per directory rather than one per frame. ``None`` as the
    # key means nothing has been computed yet.
    #
    # **What the screen says, and never what a launch is granted.** The key is a
    # spelling and the answer is about the disk under it, so the answer can be stale
    # for as long as the key is unchanged. ``begin_open`` drops it, and ``spec`` does
    # not read it: a press asks again and writes what it found here.
    _refusal_for: LaunchDirectory | None = None
    _refusal: str | None = None
    # ``git_layout`` of a launch directory, and the directory it was read for.
    # Memoised for the reason the refusal is: the read is a walk up the path and two
    # small files, and the draw asks every frame the top rung is engaged. Keyed on the
    # path, which says nothing about whether the disk under it has moved -- a
    # ``git worktree add`` at a path already typed changes the answer and not the key
    # -- so ``begin_open`` drops it, as it drops the refusal, and each open reads again.
    _git_layout_for: str | None = None
    _git_layout: GitLayout = None
    # The second reason the mode can be unavailable, beside the first. The two are read
    # together and they do not end the same way: a directory is wrong in a field the
    # operator is looking at, so a refused one downgrades the launch and says so; a CLI
    # below the floor cannot be corrected from this modal at all, so it holds the
    # launch instead. See ``launch_hold``.
    version_gate: VersionGate = field(default_factory=VersionGate)
    # Launches that were pressed and started nothing, oldest first, each with the
    # draft it was pressed from. A list and not a slot: a launch is decided on the
    # asyncio thread some time after the press, so two can be in flight and both can
    # fail, and the second must not replace the first's draft or its reason.
    failures: list[FailedLaunch] = field(default_factory=list)
    # What was in the fields when a restore replaced it, newest last. A restore is a
    # swap and this is the other half: text the operator typed is never the price of
    # getting a failed launch's text back. Each stays until it is brought back or the
    # operator discards it.
    set_aside: list[Draft] = field(default_factory=list)
    # Set when the fields are written from outside the widgets, and consumed by the
    # next ``draw``. A focused InputText keeps its own copy of the text and writes it
    # back over the caller's every frame, so a task assigned under a focused box is
    # gone one frame later unless the widget is told to read it. See ``draw``. Only
    # the task box needs it: the load that can land under a focused widget is the
    # automatic one, and that one writes nothing to the other text fields that they
    # do not already hold.
    _reload_task: bool = False
    # How tall the buttons and the lines beside them were on the last frame they
    # were drawn, or zero before the first. The body above them is bounded to what
    # that leaves of the display; see ``_modal``.
    _footer_height: float = 0.0

    @property
    def failure_loaded(self) -> bool:
        """
        Whether the oldest failed launch's draft is what the fields hold right now.

        Compared, not remembered. The notice says "the draft below is that launch, as
        it was typed" on the strength of this, and a box edited or cleared since the
        load is not that; it gets the restore offered again instead. It is also what
        makes a press that failure's retry -- see ``launched``.
        """
        return bool(self.failures) and self.failures[0].draft == self.draft()

    def draft(self) -> Draft:
        """
        The fields as they stand, as a value that outlives the launch clearing them.
        """
        return Draft(
            task=self.task,
            cwd=self.cwd,
            brief=self.brief,
            model=self.model,
            template_index=self.template_index,
            policy=self.policy,
            subagent_cap=self.subagent_cap,
            picked=self.picker.picked,
            branch=self.branch,
            uncommitted_accepted=self.uncommitted_accepted,
        )

    def report_failure(
        self,
        draft: Draft | None,
        reason: str,
        origin: str = "launch",
        *,
        accept_uncommitted: bool = False,
    ) -> None:
        """
        A launch did not start a session. Called on the draw thread, by whoever
        learned it. ``draft`` is what the modal was pressed from, or None for a launch
        the modal did not make. ``accept_uncommitted`` says the press was held on
        the checkout's uncommitted changes and nothing else (``UncommittedFound``):
        the draft comes back accepting them, so pressing it again is the "launch
        anyway". Said by the caller from the outcome's type, never read off ``reason``.

        The modal is asked for again: it is the one surface that can put a reason in
        front of the operator, and a failure with no window open has nowhere else to
        be seen. The draft goes back into the fields at once when nothing has been
        typed since its launch, which is the ordinary case -- the press cleared them
        and the answer arrives within a frame or two. Otherwise what is there is the
        operator's and is not this function's to replace: the failure waits behind a
        notice with the restore on a button. See ``_settle_failures``.

        The version gate is put back to unanswered. It is read once and its answer may
        be the thing that was wrong: the launch path reads the CLI's version for
        itself, and a refusal from there with the gate still saying "fine" would hand
        back a top-rung draft with a live button that launches into the same refusal.
        Unanswered, that draft is held until the gate has been read again. Done for
        every failure rather than only that one, because a reason is prose and this
        does not parse it; the cost is one more ``claude --version`` on a worker, and
        only if the top rung is asked for again.

        **Not from inside the ``launch`` callback ``draw`` is handed.** ``draw`` runs
        the press's clears after that callback returns, with the pressed draft still
        in the fields, and ``launched`` takes a failure whose draft is in the fields
        for the one being retried: a failure reported from inside the callback would
        be dropped by the press that produced it, and its text cleared with it.
        ``app._launch`` decides on another thread and ``app._reap_launches`` reports
        on a later frame, which is pinned by
        ``test_a_launch_decided_at_once_is_still_not_reported_from_inside_the_press``.
        """
        if accept_uncommitted and draft is not None:
            draft = replace(draft, uncommitted_accepted=draft.cwd)
        self.failures.append(FailedLaunch(draft, reason, origin))
        self.version_gate = VersionGate()
        # A launch into a worktree that failed may have left one, or found the
        # checkout's HEAD moved: what the read said is not what is there.
        self.checkout.forget()
        self.request_open()
        self._settle_failures()

    def _load(self, draft: Draft) -> None:
        """
        Write ``draft`` into the fields, and have the task box read it.
        """
        self.task = draft.task
        self.cwd = draft.cwd
        self.brief = draft.brief
        self.model = draft.model
        self.template_index = draft.template_index
        # As asked for, the top rung included. The comment at the launch's clear is
        # about a mode outliving the session it was set for; a draft loaded here
        # belongs to no session, so the mode has outlived nothing, and every hold and
        # refusal is asked again before the button goes live.
        self.policy = draft.policy
        self.subagent_cap = draft.subagent_cap
        self.branch = draft.branch
        self.uncommitted_accepted = draft.uncommitted_accepted
        # Cleared first: ``pick`` of the row already picked is how a pick is undone.
        self.picker.pick(None, draft.cwd)
        self.picker.pick(draft.picked, draft.cwd)
        self._reload_task = True

    def _set_fields_aside(self, incoming: Draft) -> None:
        """
        Keep what the fields hold before ``incoming`` replaces it, unless nothing
        would be lost: they are what ``incoming``'s own launch left behind, or the
        same draft is already held somewhere it can be got back from.
        """
        current = self.draft()
        if current == incoming or current == left_by_launch(incoming):
            return
        if current in self.set_aside or any(f.draft == current for f in self.failures):
            return
        self.set_aside.append(current)

    def restore_failed(self) -> None:
        """
        Put the oldest failed launch's draft into the fields. What it displaces is
        set aside, not dropped; see ``restore_set_aside``.
        """
        if not self.failures:
            return
        draft = self.failures[0].draft
        if draft is None:
            return
        self._set_fields_aside(draft)
        self._load(draft)

    def restore_set_aside(self) -> None:
        """
        Bring back the draft most recently set aside. A swap, as ``restore_failed``
        is: what is in the fields takes its place unless it is already held, so
        pressing this straight after a restore undoes the restore exactly.
        """
        if not self.set_aside:
            return
        draft = self.set_aside.pop()
        self._set_fields_aside(draft)
        self._load(draft)

    def discard_set_aside(self) -> None:
        """
        Drop the draft most recently set aside. The one way its text goes, and it is
        the operator's press.
        """
        if self.set_aside:
            self.set_aside.pop()

    def discard_failed(self) -> None:
        """
        Drop the oldest failed launch. The fields are left as they are: if its draft
        is in them, the text is still the operator's to edit or clear -- all but the
        acceptance of uncommitted changes it brought back, which was that launch's.
        """
        if not self.failures:
            return
        dropped = self.failures.pop(0).draft
        if dropped is not None and dropped.uncommitted_accepted == self.uncommitted_accepted:
            self.uncommitted_accepted = None
        self._settle_failures()

    def closed_without_launch(self) -> None:
        """
        The modal was closed by a cancel, an Escape or a click outside it, and not by
        a launch. An acceptance of a checkout's uncommitted changes goes, from the
        fields and from every failed draft still queued: it answered the hold that
        was on screen, and the next time the modal is opened it is asked again.
        """
        self.uncommitted_accepted = None
        self.failures[:] = [
            (
                replace(failure, draft=replace(failure.draft, uncommitted_accepted=None))
                if failure.draft is not None
                else failure
            )
            for failure in self.failures
        ]

    def _settle_failures(self) -> None:
        """
        Load the oldest failed draft if nothing has been typed since its launch.

        That is every field, compared against ``left_by_launch`` of the draft: the
        task box empty, the rung and the cap back where a press puts them, *and*
        the directory, brief, model and team still the ones that launch was pressed
        with. Those four survive a press, so a different one is something
        the operator chose afterwards -- as much theirs as a half-typed task, and
        loading over it would spend it.
        """
        if not self.failures:
            return
        draft = self.failures[0].draft
        # A failure with no draft stays a notice at the head of the list until it is
        # dismissed, and the drafts behind it wait.
        if draft is not None and self.draft() == left_by_launch(draft):
            self._load(draft)

    def launched(self) -> None:
        """
        The launch button's clears, and what they mean for a failed launch on screen.

        A press with a failed draft in the fields exactly as it failed is that
        failure's retry, so the failure goes with it -- if the retry fails too it
        comes back as its own entry, with its own reason. Any other press leaves it
        queued, an edited copy of the draft included: the fields are not that draft,
        nothing has discarded it, and its text is still owed.

        Every failure still queued is marked as pressed past, whichever kind of press
        this was. Each of them predates it, and the next thing the operator sees may
        be one of them loaded into a reopened modal.
        """
        if self.failure_loaded:
            del self.failures[0]
        self.failures[:] = [replace(failure, pressed_past=True) for failure in self.failures]
        self.task = ""
        # Cleared with the task. A mode left on outlives the session it was set for,
        # and the next Ctrl+N is the one the operator types a task into without
        # re-reading the panel above the button -- which is 08-11's "forgets it is set
        # for the four they are not" happening inside one run rather than across two.
        self.policy = Policy.STRICT
        # Cleared with the task, for the same reason the mode is: a number sized for
        # one piece of work outliving it means the next Ctrl+N silently carries a cap
        # the operator chose for something else. Back to None is back to the setting,
        # which is the answer they last gave deliberately.
        self.subagent_cap = None
        # Cleared with the task, because the message has gone to that session: a
        # pick that outlived its launch would send the next thing typed here to the
        # same conversation, under a box the operator reads as a new task.
        self.picker.pick(None, self.cwd)
        # Cleared with the task. The branch was made for that session, and the same
        # name pressed again would only be held on that session's worktree.
        self.branch = ""
        # Cleared with the branch: the operator accepted that launch's uncommitted
        # changes, not the next one's.
        self.uncommitted_accepted = None
        # The launch is about to register a worktree, so the listing read before it
        # no longer says what git has.
        self.checkout.forget()
        # Whatever failed behind the one just retried is next, and the fields are
        # blank for it now.
        self._settle_failures()

    def request_open(self) -> None:
        """Ask for the modal next time it draws. Safe to call when already open."""
        if not self.is_open:
            self._open_requested = True

    def begin_open(self, *, scanner: Scanner = scan_sessions) -> threading.Thread | None:
        """
        Consume a pending open request. The modal is up from this frame on.

        The session scan starts here rather than when the session list is expanded.
        It runs on a worker and never touches a draw call, so a fresh launch -- still
        the common case and still the default -- cannot feel it either way. What
        deferring it to the expand did cost was a click and a wait at the one moment
        the operator is hunting for lost work, which is the moment the picker exists
        for. The section itself stays collapsed; only the rows behind it are ready.

        Every open rescans. A listing from an hour ago is not a listing of what is
        there, and the picker is read by someone deciding which session to trust. The
        git layout of the launch directory is read again on the same ground: it words
        the write boundary, and a worktree made at that path since the last open is
        a wider one. So is the own-checkout refusal: it is an answer about where the
        path leads, and a path that has become a link into pptmstr's checkout since
        the last open is spelled exactly as it was.

        Returns the worker so a caller that genuinely has to wait -- a test -- can
        join it. The draw ignores it and must.
        """
        self._open_requested = False
        self.is_open = True
        self._focus_task = True
        self._git_layout_for = None
        self._refusal_for = None
        self.checkout.forget()
        return self.picker.request_scan(self.cwd.strip() or ".", scanner=scanner)

    @property
    def ready(self) -> bool:
        return bool(self.task.strip())

    def pick_session(self, row: SessionRow, *, recorder: Recorder = read_recorded) -> None:
        """
        A click on ``row`` in the session list: ``SessionPicker.pick`` of it, and
        on a pick the model, the team and the sub-agent cap are set to what the
        session's own record says it ran as.

        **So a resume from here continues the session as what it was**, unless
        the operator says otherwise: the three combos are New Task's, they start
        at solo and the default model, and a resume built from them as they stood
        would restart a team as one agent with nothing on screen saying so.

        **Each is set only where the record states it.** A session with no
        record, or a record that does not name a team this build has, leaves the
        control as it was; nothing is put back to a default. The controls stay
        the operator's after the pick, and a click that clears the pick changes
        none of them.

        ``recorder`` is called once, here, and not at all for a click that clears.
        """
        self.picker.pick(row, self.cwd.strip() or ".")
        picked = self.picker.picked
        if picked is None:
            return
        recorded = recorder(picked.session_id)
        if recorded is None:
            return
        if recorded.model:
            self.model = recorded.model
        names = templates.names()
        if recorded.template in names:
            self.template_index = names.index(recorded.template)
        if recorded.subagent_cap is not None:
            self.subagent_cap = recorded.subagent_cap

    @property
    def wants_worktree(self) -> bool:
        """
        Whether this draft asks for a new worktree: a branch typed, nothing picked.
        """
        return self.picker.picked is None and bool(self.branch.strip())

    def worktree_plan(self) -> NewWorktree | WorktreeHeld | None:
        """
        The worktree this draft launches into, why it is held, or None for a draft
        that asks for none. Reads no disk: the checkout was read on a worker
        (``CheckoutReading``), and this answers from that read only when it was of
        the field as it now stands.

        The branch is taken with the whitespace around it dropped, as the task is.
        """
        if not self.wants_worktree:
            return None
        read = self.checkout.answer_for(self.cwd.strip() or ".")
        plan = plan_worktree(read, self.branch.strip(), UNFINISHED.snapshot())
        if isinstance(plan, NewWorktree) and self.uncommitted_accepted == self.cwd:
            return replace(plan, uncommitted_accepted=True)
        return plan

    def launch_directory(self) -> LaunchDirectory:
        """
        Where this draft launches: the picked session's recorded directory, the
        worktree a typed branch places, or the cwd field.

        Every launch decision on this draft that is about a directory goes through
        here, so the line under the session list, the gate's explanation and the
        spec cannot name different places. With nothing picked and no branch it is
        the field's variant and no disk is touched. With a branch whose worktree is
        planned it is the worktree's path, which need not exist yet: the session
        runs there, and the own-checkout refusal and the gate's wording are about
        it and not about the checkout it was made from.
        """
        return self._launch_directory(self.worktree_plan())

    def _launch_directory(self, plan: NewWorktree | WorktreeHeld | None) -> LaunchDirectory:
        if isinstance(plan, NewWorktree):
            return FieldDirectory(plan.path)
        return launch_directory(self.picker.picked, self.cwd.strip() or ".")

    def git_layout(self) -> GitLayout:
        """
        ``git_layout`` of the launch directory. One read per directory per open of
        the modal, not one per frame.

        A planned worktree is the layout it will be -- a worktree beside its main
        checkout, at its root -- and not what its path reads as before it exists,
        which is no git at all and would word the sandbox's reach as the narrow one.
        """
        plan = self.worktree_plan()
        if isinstance(plan, NewWorktree):
            # Resolved on the worker; a plan is only ever made from a read that has it.
            read = self.checkout.answer_for(self.cwd.strip() or ".")
            shared = read.shared if read is not None else None
            return LinkedWorktree(
                shared=shared or os.path.join(plan.main_root, ".git"),
                root=plan.path,
                at_root=True,
            )
        raw = self.launch_directory().path
        if raw != self._git_layout_for:
            self._git_layout = git_layout(raw)
            self._git_layout_for = raw
        return self._git_layout

    def containment_refusal(self) -> str | None:
        """
        Why the mode cannot be used for the directory this draft launches in, or None.

        **Asked of the spelling the launch is started with**, ``resolved_directory``'s,
        which is what ``spec`` asks it of. ``tree.lies_inside_checkout`` expands ``~``
        and ``os.path.realpath`` does not, so the raw field ``~/x`` would be measured
        against the home directory while the session was started in a literal ``~``
        under this process's directory; asked of the resolved spelling, the screen and
        the press are answering about one place.

        **For the screen.** Memoised against the directory as spelled, so between one
        open of the modal and the next it can describe a disk that has since moved.
        ``spec`` does not read it.
        """
        directory = self.launch_directory()
        if directory != self._refusal_for:
            try:
                started_in = resolved_directory(directory)
            except ValueError:
                # No location at all, which ``refusal_for`` refuses; it is handed the
                # spelling that has none.
                started_in = directory.path
            self._read_refusal(directory, started_in)
        return self._refusal

    def _read_refusal(self, directory: LaunchDirectory, started_in: str) -> str | None:
        """
        Ask the own-checkout question of ``started_in`` now, and leave the answer as
        what the screen says for ``directory``.

        The answer says what a refusal does to the launch, and that follows from
        whether a session is picked (``resume_gate_hold``). A pick changes the
        directory's variant, so the memo's key already tells the two apart.
        """
        # The key is written after the answer it is the key for. Written first, a
        # read that raised would leave the next frame holding this directory's key
        # over the last directory's answer.
        self._refusal = refusal_for(
            started_in,
            named=_region_phrase(directory),
            consequence=REFUSAL_DOWNGRADES if self.picker.picked is None else REFUSAL_HOLDS,
        )
        self._refusal_for = directory
        return self._refusal

    @property
    def dangerous_engaged(self) -> bool:
        """
        Whether this launch actually carries the mode: asked for, and not refused.

        Only the top rung asks. ``PERMISSIVE`` is not a contained mode and never
        consults the directory, so it is not "refused" here -- it is simply not this
        question, and answering False for it is what keeps the containment machinery
        off a rung that does not want it.
        """
        return self.policy is Policy.AUTONOMOUS and self.containment_refusal() is None

    @property
    def granted_policy(self) -> Policy:
        """
        The rung this launch actually runs under, as opposed to the one asked for.

        They differ in exactly one case: ``AUTONOMOUS`` asked for where containment is
        refused. That falls back to ``STRICT`` rather than to ``PERMISSIVE``, and the
        distance is deliberate -- an operator who reached the top rung was not asking
        for the middle one, and quietly seating them one rung down would be a gate
        nobody chose. The refusal is on screen; the launch is the one it describes.

        Never for a picked session: a resume the rung is refused for is held, not
        seated lower (``resume_gate_hold``), so the rung asked for is the only one
        it can run under.
        """
        if self.picker.picked is not None:
            return self.policy
        if self.policy is Policy.AUTONOMOUS and not self.dangerous_engaged:
            return Policy.STRICT
        return self.policy

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

        **Never of a picked session.** The box is then a message, and the session's
        premises are its conversation and its brief.
        """
        if self.picker.picked is not None or self.brief.strip():
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
        see. The button holds, the reason is on the line beside it, and a lower rung
        on the dial is the way past.

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

        None whenever the mode is not engaged, and reaching that answer costs no
        read, no worker and no disabled button: the ordinary launch pays nothing for
        the cases that can hold one.

        **A picked session is held by a resume's rules and not by these.** Its
        recorded directory being gone holds it under every rung, since the field
        is not where that conversation was; and whether its rung may run contained
        is ``resume_gate_hold``'s answer, which holds where a new session would be
        downgraded and is the answer the resume form gives.
        """
        if self.picker.picked is not None:
            if isinstance(self.launch_directory(), GoneDirectory):
                return _HOLD_GONE
            return resume_gate_hold(self.policy, self.containment_refusal(), self.version_gate)
        plan = self.worktree_plan()
        if isinstance(plan, WorktreeHeld):
            return plan.reason
        if not self.dangerous_engaged:
            return None
        if self.premise_is_thin:
            return _HOLD_THIN
        if not self.version_gate.answered:
            return _HOLD_CHECKING
        return _HOLD_REFUSED if self.version_gate.refusal is not None else None

    def spec(self) -> LaunchSpec:
        """
        The draft as the value the driver is handed, for a draft that starts a new
        session. Empty cwd means the repo root.

        A ``LaunchSpec`` rather than a tuple of strings, which is the correction row
        9 paid for: the callback took four loose positionals, so dropping one still
        typechecked and a relaunched team started solo. Adding the brief to a
        positional list would be the same defect waiting on the same signature.

        **Refuses a draft with a session picked**, by ``ValueError``. That draft's
        spec is ``resumed``'s, and one built here would be a new session whose task
        is the message meant for the picked one, downgraded where a resume is held.
        ``press`` is what chooses between the two.

        **One look at the directory, taken here, decides all of it.** The directory
        is read once and resolved once, and the own-checkout refusal is asked of that
        resolved string and of nothing remembered: the memo behind
        ``containment_refusal`` is keyed on a spelling, and a path that became a link
        into pptmstr's checkout after it was first judged is spelled the same. The
        rung and the containment are both taken from that one answer, so they cannot
        be halves of two different readings. What was found is left in the memo, so
        the next frame draws the answer this press was given.
        """
        if self.picker.picked is not None:
            raise ValueError("a session is picked: this draft's spec is resumed()'s")
        plan = self.worktree_plan()
        if isinstance(plan, WorktreeHeld):
            raise ValueError(f"the worktree is held: {plan.reason}")
        directory = self._launch_directory(plan)
        # A planned worktree's path was resolved with the read, and is the cwd as it
        # stands: resolved again here it could name another place than the one the
        # worktree is made at, which ``LaunchSpec`` refuses.
        resolved_cwd = plan.path if plan is not None else resolved_directory(directory)
        # Both or neither, and the pairing is the top rung. ``AUTONOMOUS`` releases
        # every reviewed tool and the containment is the only thing bounding what a
        # released ``Bash`` reaches, so a spec carrying that rung alone is an unattended
        # agent with the whole machine, and one carrying the containment alone is a
        # sandbox around a gate that is still asking. A draft that did not reach the top
        # rung, or reached it in a directory it is refused for, produces the two
        # defaults -- which is the spec this built before the mode existed.
        #
        # ``PERMISSIVE`` is deliberately outside that pairing and carries no
        # containment. What the sandbox bounds is writes and network, which that rung
        # already refuses syntactically, so pairing them would cost the operator
        # working invocations to buy the gate nothing. See ``approval.Policy``.
        engaged = (
            self.policy is Policy.AUTONOMOUS and self._read_refusal(directory, resolved_cwd) is None
        )
        return LaunchSpec(
            task=self.task.strip(),
            model=self.model,
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
            containment=sandbox.containment_settings() if engaged else None,
            # ``granted_policy``'s rule, from this reading rather than from the memo.
            policy=(
                Policy.STRICT if self.policy is Policy.AUTONOMOUS and not engaged else self.policy
            ),
            # Unconditionally, and not gated on ``engaged`` as the containment is. The
            # policy and the containment pair because the top rung without the
            # containment is an unattended agent with the whole machine; a cap is
            # capacity and pairs with nothing, so an operator who sized this launch for
            # two sub-agents gets two whether or not they also reached the top rung.
            subagent_cap=self.subagent_cap,
            # The path is ``cwd`` above; this is what ``app._launch`` needs to make it.
            worktree=plan,
        )

    def resumed(self) -> LaunchSpec | ResumeHeld:
        """
        The spec that continues the picked session, or why nothing is started.
        ``ValueError`` when nothing is picked.

        **The task box is the message and never the title.** The title is the
        row's label, the CLI's name for the conversation, and is passed as a label:
        a row carries no task. ``app._launcher_pressed`` puts the session's own
        task there when the store has a record of it.

        **The directory is the row's**, by ``launch_directory``, and the field
        answers only for a row that recorded none. One look at it, taken here,
        decides the rest as it does in ``spec``: the own-checkout refusal is asked
        of that resolved string and left in the memo for the next frame to draw.

        **The model, the team and the brief are this modal's fields.** A click on
        the row set the first two, and the cap, from the session's record where it
        has one (``pick_session``), and they are the operator's to change after
        it. A team whose brief field is empty is given the session's own brief
        directory, where there is one, by ``app._seed_brief``.
        The spec itself, and whether its rung may run contained, is
        ``resume_spec``'s, as it is for that form.
        """
        row = self.picker.picked
        if row is None:
            raise ValueError("no session is picked: this draft's spec is spec()'s")
        directory = self.launch_directory()
        if isinstance(directory, GoneDirectory):
            return ResumeHeld(_HOLD_GONE)
        resolved_cwd = resolved_directory(directory)
        return resume_spec(
            session_id=row.session_id,
            cwd=resolved_cwd,
            policy=self.policy,
            refusal=self._read_refusal(directory, resolved_cwd),
            gate=self.version_gate,
            task=row.label,
            task_is_label=True,
            message=self.task.strip(),
            model=self.model,
            template=templates.names()[self.template_index],
            brief=self.brief.strip() or None,
            subagent_cap=self.subagent_cap,
        )

    def press(self) -> LaunchSpec | None:
        """
        The spec a press of launch starts, or None when the press found a different
        launch from the one on screen.

        ``spec`` asks the own-checkout question afresh and the line under the dial
        was drawn from a remembered answer. When they disagree the operator
        pressed under a description of some other launch -- the alarm over a directory
        that is now refused, or a refusal over one that is now allowed -- so nothing
        is started: the fields are left as they are, the modal stays up, and the
        next frame draws what this reading found.

        With a session picked the spec is ``resumed``'s and the rule is the same
        one. A resume that reading holds starts nothing, and neither does one
        under a rung that needs containment when the directory or the refusal
        that was remembered for it is not what was found. What is remembered is
        what ``launch_hold`` last read for the button, so a press nothing was drawn
        for has nothing to agree with and starts nothing either.
        """
        if self.picker.picked is None:
            if isinstance(self.worktree_plan(), WorktreeHeld):
                return None
            shown = self.granted_policy
            spec = self.spec()
            return spec if spec.policy is shown else None
        remembered = (self._refusal_for, self._refusal)
        outcome = self.resumed()
        if isinstance(outcome, ResumeHeld):
            return None
        found = (self._refusal_for, self._refusal)
        return outcome if found == remembered or outcome.containment is None else None


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


# Under a session picked in New Task, and on the resume form, when the session is
# one this process is running. The pool
# takes a session submitted under a live id by ending the live run first
# (``SessionPool.submit``), so a resume of such a session is not a message to it:
# it stops one. The sentence says what goes with the run, because "the conversation
# continues" is true as well and reads as the whole of it.
_LIVE_RESUME = (
    "this session is open in this window right now -- resuming ends its run where it "
    "stands, mid-turn if it is in one: a tool call in flight and any approval it is "
    "waiting on go with it, and its sub-agents end with the run: the ones still "
    "working are cancelled. The conversation then continues in a new run. To say "
    "something to the session as it is, cancel and send it a message instead."
)

# On a row of the listing, for the same sessions: the fact is worth having before
# the click as well as after it.
_LIVE_TAG = "open here now"

# One line that is not cut holds this many characters with a question mark
# beside it, at the dialog's width in the body face. A line that quotes a path
# gives the path what its own words leave of this.
LINE_CHARS = 64

# Behind the question mark beside "launch fresh instead", once a row is picked:
# what the launch button now does, and what the pick did to the controls below.
_PICKED_SENDS = "the conversation comes back and the text above is sent to it as its next message"
_PICKED_CHOSEN_HERE = (
    "model, team and sub-agent cap were filled in from the session's own record where it "
    "has one, and are yours to change; a session with no record leaves them as they "
    "were. A team left with no brief directory is given the session's own."
)
_UNPICK = "launch fresh instead"

# Under the task box. What the box is for follows from whether a session is picked.
_TASK_CAPTION = "what should a new session do?  Ctrl+Enter launches, Enter breaks"
_MESSAGE_CAPTION = "sent to the picked session.  Ctrl+Enter launches, Enter breaks"
# Behind the task box's question mark when the team has roles: it changes what
# the box is for, since this text is then the premises every worker is told to
# read and not only the lead's opening message. Not of a message to a picked
# session, which is written into no brief (``app._seed_brief``).
_TEAM_PREMISES = "on a team, this also becomes the premises every worker reads"

# Under the session list for a picked session that this process is running.
_LIVE_LINE = "open in this window now — resuming ends its run"


def task_notice(*, resuming: bool, team: bool) -> Notice:
    """The one line under the task box."""
    if resuming:
        return Notice(_MESSAGE_CAPTION)
    if team:
        return Notice(
            _TASK_CAPTION, Tone.QUIET, Help("task", "the task box, on a team", (_TEAM_PREMISES,))
        )
    return Notice(_TASK_CAPTION)


def live_notice(row: SessionRow | None, live: frozenset[str]) -> Notice | None:
    """
    The line for a session to be resumed that this process is running, or None:
    ``live_resume_line`` behind a question mark, under one line saying what a
    press ends.
    """
    said = live_resume_line(row, live)
    if said is None:
        return None
    return Notice(
        _LIVE_LINE, Tone.WARN, Help("live", "resuming a session that is open here", (said,))
    )


def live_resume_line(row: SessionRow | None, live: frozenset[str]) -> str | None:
    """
    What is said of a session to be resumed when it is live in this process, or
    None when it is not or there is no row.

    ``live`` is the ids of the sessions this process holds that have not ended. It
    is handed in by the caller on every draw and kept nowhere here: which sessions
    are live is the store's fact, and a copy on a draft would be a second one to
    keep true.
    """
    if row is None or row.session_id not in live:
        return None
    return _LIVE_RESUME


def _session_row(picker: SessionPicker, row: SessionRow, now: float, live: frozenset[str]) -> bool:
    """
    One line: bookmark toggle, then a selectable spanning the rest of the row,
    lit when the row is the pick. True on the frame the row is clicked.

    Label, branch and age go into a single selectable string rather than a selectable
    followed by dimmed metadata. A selectable sized to its own text leaves the branch
    and the age outside the hit box, and those are the parts an operator reads last
    and clicks from -- a dead half-row is worse than undifferentiated colour.
    """
    # The directory beside the id: one id listed under two directories is two rows,
    # and two rows under one ID are one widget as far as a click is concerned.
    # ``row.cwd`` is the directory as the CLI recorded it, which a resume runs in,
    # and an id is a text call too: written out one to one, so two directories stay
    # two ids.
    imgui.push_id(drawable.text(f"{row.session_id}\n{row.cwd}"))
    # Colour carries the state as well as the glyph. At this point size "•" and "+"
    # differ by a few pixels of ink, which is not a difference an operator scanning a
    # list of thirty rows for their own bookmarks can act on.
    if row.bookmarked:
        imgui.push_style_color(imgui.Col_.text, P.accent.vec4)
    if widgets.small_button("*" if row.bookmarked else "+"):
        picker.toggle_bookmark(row)
    if row.bookmarked:
        imgui.pop_style_color()
    imgui.set_item_tooltip("remove bookmark" if row.bookmarked else "bookmark this session")
    imgui.same_line()

    meta = "  ·  ".join(
        p
        for p in (
            _LIVE_TAG if row.session_id in live else None,
            row.git_branch,
            age_label(row.last_modified, now),
        )
        if p
    )
    tail = f"  ·  {meta}" if meta else ""
    # ``label`` can be a first prompt, which is a whole paragraph with newlines in
    # it. Flattened before measuring, or the row grows to the height of the prompt.
    head = widgets.flat_line(row.label)
    room = imgui.get_content_region_avail().x - imgui.calc_text_size(tail).x
    clicked, _ = imgui.selectable(
        f"{widgets.ellipsis(head, max(80.0, room))}{tail}", picker.is_picked(row)
    )
    imgui.pop_id()
    return bool(clicked)


def resume_directory_line(directory: LaunchDirectory, field_resolved: str) -> str | None:
    """
    What a resume says about where its session runs, or None for a launch that
    resumes nothing.

    ``field_resolved`` is the directory the caller would otherwise have used, as the
    directory it names: New Task's cwd field. ``resume_form`` has no such field and
    hands in the session's own, so the "NOT the working directory above" halves are
    never drawn there.

    **The directory after "runs in" is the one the launch is started in**, for every
    variant that launches. A recorded path that leads elsewhere through a link is
    named second, as the spelling the transcript carries, and is never the one the
    line says the session runs in.
    """
    match directory:
        case FieldDirectory():
            return None
        case RedirectedDirectory(path=path, recorded=recorded):
            where = "" if path == field_resolved else ", and NOT the working directory above"
            return (
                f"runs in {path} -- NOT in {recorded} as that path reads: {recorded} is "
                "where this session was recorded, and it is a symbolic link or lies "
                f"behind one that leads there{where}"
            )
        case RecordedDirectory(path=path):
            if path == field_resolved:
                return f"runs in {path} -- where this session was recorded"
            return (
                f"runs in {path} -- where this session was recorded, "
                "NOT the working directory above"
            )
        case GoneDirectory(path=path):
            return (
                f"{path} is where this session was recorded, and it is no longer a "
                "directory -- launch is held.\n"
                "a resume is never moved to the working directory above: restore that "
                "directory, pick another session, or launch fresh."
            )
        case UnrecordedDirectory():
            return (
                "this session recorded no directory -- it runs in the working directory "
                f"above, {field_resolved}"
            )
        case _:
            assert_never(directory)


_RUNS_ELSEWHERE = ", NOT the working directory above"
_RUNS_BY_LINK = ", through a link"
RUNS_GONE = "its recorded directory is gone — launch is held"
RUNS_UNRECORDED = "recorded no directory — runs in the working directory above"


def _runs_in(path: str, after: str = "") -> str:
    """``runs in <path><after>``, the path cut from its start to what the line has left."""
    return f"runs in {widgets.path_tail(path, LINE_CHARS - len('runs in ') - len(after))}{after}"


def directory_notice(directory: LaunchDirectory, field_resolved: str) -> Notice | None:
    """
    Where a picked session runs, as one line, with ``resume_directory_line``'s
    sentence for it behind the question mark. None for a launch that resumes
    nothing.

    The colour says how far the directory is from what the cwd field would lead
    an operator to expect: the field's own is quiet, another one warns, and one
    that is gone is the danger it is -- nothing launches.
    """
    said = resume_directory_line(directory, field_resolved)
    if said is None:
        return None
    explained = Help("runs-in", "where this session runs", (said,))
    match directory:
        case FieldDirectory():
            return None
        case RecordedDirectory(path=path):
            if path == field_resolved:
                return Notice(_runs_in(path), Tone.QUIET, explained)
            return Notice(_runs_in(path, _RUNS_ELSEWHERE), Tone.WARN, explained)
        case RedirectedDirectory(path=path):
            return Notice(_runs_in(path, _RUNS_BY_LINK), Tone.WARN, explained)
        case GoneDirectory():
            return Notice(RUNS_GONE, Tone.DANGER, explained)
        case UnrecordedDirectory():
            return Notice(RUNS_UNRECORDED, Tone.WARN, explained)
        case _:
            assert_never(directory)


def scan_notices(scanned: str, error: str | None) -> tuple[Notice, ...]:
    """
    Over the session list: which directory it is a listing for, and a scan that
    failed. The failure is loud and never a silently empty list -- someone
    reading this screen has already lost work once -- and what it said is whole
    behind its question mark.
    """
    listing = Notice(f"sessions in {widgets.path_tail(scanned, 36)} and its git worktrees")
    if error is None:
        return (listing,)
    failed = f"could not read the transcripts: {error}"
    return (
        listing,
        Notice(failed, Tone.DANGER, Help("scan", "the transcripts could not be read", (failed,))),
    )


def _session_list(
    state: LauncherState, *, scanner: Scanner, live: frozenset[str], recorder: Recorder
) -> None:
    """
    The sessions that can be resumed: collapsed until asked for, over rows already
    being scanned. A click on a row picks it, and a click on the pick clears it.

    **The pick is made and kept here, and this modal is what resumes it.** The
    listing reaches what the fleet pane does not show -- a session removed from the
    fleet, one another tool started -- and with a row picked the launch button
    continues that session (``LauncherState.press``). Nothing else is opened.

    The header starts shut so a fresh launch is not asked to look at it, but
    ``LauncherState.begin_open`` put the scan in flight when the modal opened -- so
    unless the cwd field has moved since, expanding costs a header toggle and no
    disk read, and by the time an operator has read this far the rows are usually
    already here. When it has moved, expanding starts a scan for the directory now
    in the field; see ``SessionPicker.set_expanded``.

    The header names the picked session and ``_picked_lines`` is drawn under it
    whether or not it is open: a pick behind a shut header is still what the launch
    button does.
    """
    picker = state.picker
    picker.poll()

    widgets.dialog_field()
    cwd = state.cwd.strip() or "."
    heading = (
        "resume an existing session instead"
        if picker.picked is None
        else f"resuming: {widgets.flat_line(picker.picked.label)}"
    )
    # Cut to the header's own width, less the arrow: a label is a first prompt as
    # often as a summary, and a header does not wrap.
    room = imgui.get_content_region_avail().x - 3.0 * imgui.get_font_size()
    expanded = imgui.collapsing_header(f"{widgets.ellipsis(heading, room)}{_RESUME_ID}")
    picker.set_expanded(expanded, cwd, scanner=scanner)
    if expanded:
        for said in scan_notices(picker.scanned_cwd or cwd, picker.error):
            widgets.notice(said)

        now = time.time()
        clicked: SessionRow | None = None
        if imgui.begin_child(
            "##sessions", imgui.ImVec2(-1.0, _LIST_HEIGHT), imgui.ChildFlags_.borders
        ):
            if picker.scanning and not picker.rows:
                imgui.text_disabled("reading transcripts...")
            elif not picker.rows and picker.error is None:
                imgui.text_disabled("no sessions recorded for this directory")
            for entry in picker.rows:
                if _session_row(picker, entry, now, live):
                    clicked = entry
        imgui.end_child()
        # After the rows, so every row of this frame was drawn against one pick.
        if clicked is not None:
            state.pick_session(clicked, recorder=recorder)

        if widgets.small_button("rescan") and not picker.scanning:
            picker.request_scan(cwd, scanner=scanner)
    _picked_lines(state, cwd, live)


def _picked_lines(state: LauncherState, cwd: str, live: frozenset[str]) -> None:
    """
    Under the session list when a session is picked: one line for where the
    session runs (``directory_notice``, against the cwd field as the directory it
    names), one for what resuming it ends if it is running here, and the way back
    to a new session -- with what the launch button now does, and what the pick
    did to the controls below, behind the question mark beside it.
    """
    picked = state.picker.picked
    if picked is None:
        return
    widgets.dialog_caption()
    directory = state.launch_directory()
    try:
        field_resolved = os.path.realpath(cwd)
    except ValueError:
        # A field ``realpath`` cannot take names no directory; as typed, it is
        # equal to none.
        field_resolved = cwd
    for said in (directory_notice(directory, field_resolved), live_notice(picked, live)):
        if said is not None:
            widgets.notice(said)
    if widgets.small_button(_UNPICK):
        state.picker.pick(None, cwd)
    widgets.help_mark("picked", "resuming a picked session", (_PICKED_SENDS, _PICKED_CHOSEN_HERE))


def _region_phrase(directory: LaunchDirectory) -> str:
    """
    The directory the contained half is bounded to, as the gate's explanation names it.

    "The working directory above" is true only while a field is what the launch
    uses. For a resume the region is the session's directory, and pointing at a
    field would name a tree the sandbox does not cover as the one it does.
    For a recorded path that leads elsewhere through a link it is where the link
    leads: that is the directory the sandbox is given.
    """
    match directory:
        case FieldDirectory() | UnrecordedDirectory():
            return "the working directory above"
        case (
            RecordedDirectory(path=path) | RedirectedDirectory(path=path) | GoneDirectory(path=path)
        ):
            return f"the resumed session's directory, {path}"
        case _:
            assert_never(directory)


def boundary_unstated(directory: LaunchDirectory) -> str | None:
    """
    Why the gate's explanation states no boundary for ``directory``, or None when
    it states one.

    A session whose recorded directory is gone has no directory for a sandbox to be
    given, and ``git_layout`` of a path that is not there reads as the layout the
    narrow measurement was taken for -- so the paragraphs would say ``Bash`` writes
    only inside a directory the line above them says does not exist. Nothing
    launches there (``LauncherState.launch_hold``, ``resume_form.ResumeForm.hold``),
    and nothing is described.
    """
    match directory:
        case GoneDirectory(path=path):
            return (
                f"no boundary is stated: {path}, where the picked session was "
                "recorded, is no longer a directory, so there is nothing for this "
                "mode to be bounded to and nothing launches."
            )
        case FieldDirectory() | RecordedDirectory() | RedirectedDirectory() | UnrecordedDirectory():
            return None
        case _:
            assert_never(directory)


# A tool name no build knows, handed to the classifier to ask what a rung does with
# one. ``approval.classify`` parks it at every rung; asking rather than saying so
# keeps the line under the dial a reading of the classifier, as the probes are.
_UNKNOWN_TOOL = "pptmstr-probe-of-a-tool-no-build-knows"
_UNKNOWN_KIND = "a tool this build has never heard of"


@dataclass(frozen=True, slots=True)
class DialText:
    """
    What is said of the gate dial. ``downgrade`` is None when the rung on the
    dial is the rung the launch gets, and otherwise the line saying which rung it
    gets instead, which is the rung ``adds`` and ``parks`` are about.
    """

    downgrade: str | None
    adds: str
    parks: str


def dial_text(asked: Policy, granted: Policy) -> DialText:
    """
    The lines under the dial, for a draft that asked for ``asked`` and will be
    launched under ``granted``.

    **They describe the rung the launch gets.** The two differ where the top rung is
    refused for the directory (``LauncherState.granted_policy``), and "adds writes,
    spawns ..." over a refusal saying the launch uses the ordinary gate is two
    answers to one question on one screen. The dial keeps the rung that was asked
    for, because that is the control's value, and ``downgrade`` says in words
    which one the launch runs under.

    **The parked half always has something in it.** An unrecognised tool waits for
    the operator at every rung, and it is listed with the probed kinds that do, so
    the top rung -- which parks none of the probed kinds -- reads "still parks only"
    that and not a label with nothing after it.
    """
    adds = widgets.gate_adds(granted)
    parks = list(widgets.gate_parks(granted))
    only = "" if parks else "only "
    if classify(_UNKNOWN_TOOL, {}, granted) is Disposition.REQUIRE_APPROVAL:
        parks.append(_UNKNOWN_KIND)
    return DialText(
        downgrade=(
            None
            if granted is asked
            else REFUSED_RUNS_LOWER.format(asked=asked.value, granted=granted.value)
        ),
        adds=f"adds {', '.join(adds)}" if adds else "nothing runs unattended",
        parks=f"still parks {only}{', '.join(parks)}" if parks else "parks nothing",
    )


def modal_origin(
    pos: tuple[float, float],
    size: tuple[float, float],
    area_pos: tuple[float, float],
    area_size: tuple[float, float],
) -> tuple[float, float] | None:
    """
    Where to move a window at ``pos`` of ``size`` so that it lies inside the area,
    or None when it already does.

    The modal is centred when it appears and grows downward afterwards -- the top
    rung adds a section, an opened session list adds rows -- and a window that grows from a fixed
    origin grows off the bottom of the display, taking the launch button with it.
    Scrolling does not bring that back: the body scrolls inside the window, and the
    part of the window that is off the display stays off it.

    On an axis where the window is larger than the area its start is put at the
    area's, which is the end the title bar is at. ``draw`` bounds the height to the
    area's so that case is a window being resized under it, for a frame.
    """

    def along(at: float, extent: float, low: float, room: float) -> float:
        if extent >= room:
            return low
        return min(max(at, low), low + room - extent)

    moved = (
        along(pos[0], size[0], area_pos[0], area_size[0]),
        along(pos[1], size[1], area_pos[1], area_size[1]),
    )
    return None if moved == pos else moved


# The one line under the gate dial where the rung asked for is not the rung, or
# not the launch, the operator gets. Each changes what a press does, so each is
# on screen without being asked for; why is behind its question mark.
REFUSED_RUNS_LOWER = "{asked} is refused for this directory — this launch runs {granted}"
REFUSED_HOLDS_RESUME = "{rung} is refused for this directory — the resume is held"
CLI_HOLDS = "this machine's CLI cannot contain {rung} — {what} is held"
NO_BOUNDARY = "no boundary: the session's directory is gone — nothing starts"
# The alarm: the one sentence under a dial that is on the top rung and granted.
LOUD_LINE = "this session will write files and run commands without asking"

_ON = (
    "on -- this session writes files, runs commands, spawns sub-agents, declares "
    "board tasks and messages its own agents without asking you. A tool this "
    "build has never heard of is still not released."
)
# The one paragraph that says a boundary is absent.
_NOT_CONTAINED = (
    "not contained: WebFetch and WebSearch run inside the CLI process, which the "
    "sandbox does not wrap, so they reach whatever host the session names. Released "
    "anyway -- an unattended agent that cannot read documentation is the worse trade."
)
_GATE_MARK = "gate"


def gate_notice(
    asked: Policy,
    *,
    resuming: bool,
    directory: LaunchDirectory,
    refusal: str | None,
    gate: VersionGate,
    layout: Callable[[], GitLayout],
    subagent_cap: int | None,
    launches: bool = True,
    also: tuple[str, ...] = (),
) -> Notice:
    """
    The one line under the gate dial, and everything else there is to say about
    the rung behind its question mark. New Task's dial and the resume form's are
    both this.

    **A rung that needs no containment gets what it adds**, quietly, and what it
    still parks behind the mark.

    **The top rung, granted, gets the alarm** (``LOUD_LINE``, ``Tone.LOUD``): the
    session will act without asking, which an operator who cannot see it cannot
    supervise. Behind its mark is what the rung costs, at the strength it was
    measured (``contained_text``): what is released, what the sandbox bounds and
    what it does not, what the gate holds file writes to, the uncontained edge --
    ``WebFetch`` and ``WebSearch`` run in the CLI process, which the sandbox does
    not wrap -- and the sub-agent cap, the one bound the rung cannot widen. The
    credential denials are not claimed: ``sandbox.credentials`` binds sandboxed
    ``Bash`` only.

    **The top rung, not granted, gets the line that says what a press does
    instead**, and the reason behind the mark. Asked in the order the launch
    path asks: a session whose recorded directory is gone has nothing for the
    rung to be bounded to (``boundary_unstated``); a directory the rung is
    refused for runs a new session one rung down and holds a resume
    (``refusal``, ``refusal_for``'s answer); and a CLI that cannot be trusted
    with the sandbox keys holds either (``gate``).

    ``layout`` reads the launch directory's git layout and is called only for the
    alarm, the one case that words a boundary. ``launches`` is False for a resume
    with no directory to start in, which is held whatever the rung and is told so
    elsewhere; ``also`` is a surface's own paragraphs, last behind the mark.
    Reads nothing and starts nothing itself: whoever draws this polls ``gate``.
    """
    name = asked.value
    unstated = boundary_unstated(directory)
    if not requires_containment(asked) or (unstated is None and not launches):
        dial = dial_text(asked, asked)
        return Notice(
            dial.adds,
            Tone.QUIET,
            Help(_GATE_MARK, f"the {name} gate", (dial.adds, dial.parks, *also)),
        )
    if unstated is not None:
        return Notice(
            NO_BOUNDARY,
            Tone.WARN,
            Help(_GATE_MARK, f"why {name} states no boundary", (unstated, *also)),
        )
    what = "the resume" if resuming else "launch"
    if refusal is not None:
        if resuming:
            return Notice(
                REFUSED_HOLDS_RESUME.format(rung=name),
                Tone.WARN,
                Help(_GATE_MARK, f"why {name} is refused", (refusal, *also)),
            )
        lower = dial_text(asked, Policy.STRICT)
        assert lower.downgrade is not None
        return Notice(
            lower.downgrade,
            Tone.WARN,
            Help(_GATE_MARK, f"why {name} is refused", (refusal, lower.adds, lower.parks, *also)),
        )
    if gate.refusal is not None:
        return Notice(
            CLI_HOLDS.format(rung=name, what=what),
            Tone.WARN,
            Help(_GATE_MARK, f"why {name} is held", (gate.refusal, *also)),
        )
    dial = dial_text(asked, asked)
    contained = contained_text(directory, layout())
    return Notice(
        LOUD_LINE,
        Tone.LOUD,
        Help(
            _GATE_MARK,
            f"what {name} releases, and what bounds it",
            (
                _ON,
                contained.sandbox,
                *(() if contained.wider is None else (contained.wider,)),
                # Said apart from the sandbox paragraph because it is a different
                # mechanism, and an operator reasoning about a failure needs to
                # know which one refused them.
                contained.gate,
                _NOT_CONTAINED,
                _cap_line(subagent_cap),
                dial.adds,
                dial.parks,
                *(() if gate.answered else (_HOLD_LINE,)),
                *also,
            ),
        ),
    )


def _gate_notice(state: LauncherState, *, prober: Prober, subagent_cap: int | None) -> Notice:
    """
    ``gate_notice`` for this draft, and the one place the draw starts the read of
    the installed CLI's version.

    **Started from the draw and only for a top rung the directory allows**, so an
    uncontained launch never spawns the subprocess, and neither does one the
    directory already refused: that is not a contained launch, and reading the
    CLI's version for it would answer a question nothing asks. Both calls are
    no-ops once the read has landed.
    """
    needs = requires_containment(state.policy)
    directory = state.launch_directory()
    refusal = state.containment_refusal() if needs else None
    gate = state.version_gate
    if needs and refusal is None and boundary_unstated(directory) is None:
        gate.poll()
        gate.request(prober=prober)
    return gate_notice(
        state.policy,
        resuming=state.picker.picked is not None,
        directory=directory,
        refusal=refusal,
        gate=gate,
        layout=state.git_layout,
        subagent_cap=subagent_cap,
    )


# Under the headline of a failure a launch has been pressed past. It has to stay true
# for as long as that failure is at the head of the list, so it says "since" and not
# "just now", and it does not say the later launch started: that is not known here
# until it is reported, and a failure of it queues behind this one.
_PRESSED_PAST = (
    "a launch has been pressed since, and this notice is not about that one -- if that "
    "one starts nothing it is reported here after this"
)


_FAILED_NOT_HERE = "it was not made from this window, so nothing typed below is part of it"
_FAILED_LOADED = "the draft below is that launch, as it was typed -- nothing was lost"
_FAILED_KEPT = (
    "its draft is kept. restoring it sets what is in the fields below aside, "
    "where it can be brought back; discarding it drops that launch's text for "
    "good"
)


def failure_notice(failure: FailedLaunch, *, loaded: bool, waiting: int) -> Notice:
    """
    The oldest launch that started nothing, as one line: that it did not start,
    how many more are behind it, and as much of the reason as the row holds.
    Behind its question mark, the reason whole and what has become of its draft.

    ``loaded`` is ``LauncherState.failure_loaded`` on this frame, so "as it was
    typed" is not said over a box that has been edited or emptied since.

    A failure a launch has been pressed past is called an earlier one and is said
    not to be that press. The modal reopens by itself over such a press, and the
    same words as a retry that failed again would make a launch that started look
    like one that did not.
    """
    which = f"an earlier {failure.origin}" if failure.pressed_past else f"that {failure.origin}"
    headline = f"{which} did not start a session"
    if failure.draft is None:
        draft = _FAILED_NOT_HERE
    elif loaded:
        draft = _FAILED_LOADED
    else:
        draft = _FAILED_KEPT
    behind = f"{waiting} more launch(es) did not start; each is kept behind this one"
    return Notice(
        f"{headline}{f' ({waiting} more behind it)' if waiting else ''}: {failure.reason}",
        Tone.DANGER,
        Help(
            "failure",
            f"why {which} did not start",
            (
                f"{headline}: {failure.reason}",
                *((_PRESSED_PAST,) if failure.pressed_past else ()),
                draft,
                *((behind,) if waiting else ()),
            ),
        ),
    )


def set_aside_notice(count: int) -> Notice:
    """The drafts a restore set aside, as one line over the two buttons for them."""
    return Notice(
        f"{count} draft(s) set aside by a restore are kept",
        Tone.QUIET,
        Help(
            "set-aside",
            "drafts set aside by a restore",
            (
                f"{count} draft(s) set aside by a restore are kept. bringing "
                "the last one back swaps it with what is in the fields below",
            ),
        ),
    )


def _failure_notice(state: LauncherState) -> None:
    """
    At the top of the modal: ``failure_notice`` for the oldest launch that started
    nothing, over the buttons for its draft, and under it ``set_aside_notice``
    over the buttons for a draft a restore set aside.

    One failure at a time and in order. Each has a draft of its own and the fields
    hold one, so showing two reasons over one draft would leave the operator matching
    them up by hand.
    """
    if not state.failures and not state.set_aside:
        return
    if state.failures:
        failure = state.failures[0]
        loaded = state.failure_loaded
        widgets.notice(failure_notice(failure, loaded=loaded, waiting=len(state.failures) - 1))
        if failure.draft is None or loaded:
            if widgets.small_button("dismiss this notice"):
                state.discard_failed()
        else:
            if widgets.small_button("restore that draft"):
                state.restore_failed()
            imgui.same_line()
            if widgets.small_button("discard it"):
                state.discard_failed()
    # Read after the buttons above, so a restore pressed on this frame shows what it
    # set aside on this frame.
    if state.set_aside:
        widgets.notice(set_aside_notice(len(state.set_aside)))
        # Labels of their own: two buttons reading "discard it" in one window share
        # an ID, and the second would never fire.
        if widgets.small_button("bring back what was set aside"):
            state.restore_set_aside()
        imgui.same_line()
        if widgets.small_button("discard what was set aside"):
            state.discard_set_aside()
    widgets.dialog_group()


def draw(
    state: LauncherState,
    *,
    running: int,
    queued: int,
    cap: int,
    launch: Callable[[LaunchSpec, Draft], None],
    wrap: bool,
    scanner: Scanner = scan_sessions,
    prober: Prober = cli_version.check_installed_cli,
    subagent_cap: int | None = None,
    models: tuple[str, ...] = FALLBACK_MODELS,
    live: frozenset[str] = frozenset(),
    recorder: Recorder = read_recorded,
    reader: CheckoutReader = read_checkout,
) -> None:
    """
    Draw the modal if it has been asked for.

    **A session picked in the session list is resumed from here.** The click picks
    the row and the modal stays as it is; ``launch`` is then handed the spec that
    continues that session, with what is in the task box as its message
    (``LauncherState.resumed``). Nothing in this module opens the resume form.
    ``recorder`` reads the picked session's own record, which that click fills the
    model, the team and the cap from (``LauncherState.pick_session``).

    **Under a control there is one line, and an explanation is behind a question
    mark** (``widgets.notice``, ``widgets.help_mark``). What is on screen without
    a press is a caption, the alarm for a session that will act without asking,
    and a line for each thing that changes what the launch button does.

    ``live`` is the ids of the sessions this process is running. A row of the session
    list that is one of them says so, and picking it says what resuming it ends.
    Empty, the default, is a caller that runs none.

    ``cap`` and ``subagent_cap`` are different bounds and are not interchangeable:
    ``cap`` is how many *sessions* run at once and already has a consequence on this
    screen, while ``subagent_cap`` is how many sub-agents one session may hold.

    ``launch`` is handed the spec and the draft it was built from. The spec is what
    starts a session; the draft is what the caller gives back through
    ``LauncherState.report_failure`` if no session started, since by then the fields
    it came from have been cleared. **It gives it back on a later frame and never
    from inside ``launch``**: the clears run when ``launch`` returns, and a failure
    reported before them is taken for the one this press retried. See
    ``report_failure``.

    ``reader`` is what reads the cwd field's checkout for a typed worktree branch,
    on a worker (``CheckoutReading``); a parameter for the reason ``scanner`` is.

    ``models`` is what the model combo offers -- ``model_catalog.Catalog.models``, which
    is the list in code until the startup probe replaces it. Defaulted rather than
    required so a caller that has not run a probe still draws a usable combo.

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

    # From before the window begins, which is when it reads its padding, to after it
    # ends: the one scope the resume form is drawn under as well.
    with widgets.dialog_style():
        _modal(
            state,
            running=running,
            queued=queued,
            cap=cap,
            launch=launch,
            wrap=wrap,
            scanner=scanner,
            prober=prober,
            subagent_cap=subagent_cap,
            models=models,
            live=live,
            recorder=recorder,
            reader=reader,
        )


def _branch_field(state: LauncherState, *, reader: CheckoutReader) -> None:
    """
    The worktree branch field and the lines under it.

    **The read of the checkout is asked for here and done on a worker**: ``detect``
    and ``status`` are subprocesses, and this is the draw. What is drawn is from the
    last read that has landed, and a field edited since is "reading" until the next
    one does.

    Disabled with a session picked: a resume runs in its recorded directory, and a
    branch under it would be a field that does nothing.
    """
    state.checkout.poll()
    picked = state.picker.picked is not None
    widgets.dialog_field()
    with hint_colour(imgui):
        if picked:
            imgui.begin_disabled()
        imgui.set_next_item_width(-1)
        _, state.branch = imgui.input_text_with_hint(
            "##branch",
            "new branch, to run in a new worktree (optional)"
            " -- leave empty to run in the directory",
            state.branch,
        )
        if picked:
            imgui.end_disabled()
    widgets.dialog_caption()
    imgui.text_disabled("worktree branch")
    if not state.wants_worktree:
        return
    state.checkout.want(state.cwd.strip() or ".", reader=reader)
    plan = state.worktree_plan()
    assert plan is not None
    for said in worktree_notices(plan, state.checkout.answer_for(state.cwd.strip() or ".")):
        widgets.notice(said)


def body_room(work_height: float, top: float, padding: float, footer: float) -> float:
    """
    How tall the modal's scrolling body may be for the buttons under it to be on
    the display: what is left of ``work_height`` after the ``top`` of the window
    (its title bar and the padding under it), the ``padding`` at its bottom and the
    ``footer`` the buttons take.

    Never less than ``_BODY_FLOOR``. On a display too short for both, a body with
    no height would be a window of buttons over nothing, and the floor leaves the
    task box in reach by scrolling. ``widgets.dialog_body_room`` with this modal's
    floor.
    """
    return widgets.dialog_body_room(work_height, top, padding, footer, _BODY_FLOOR)


# The least the body is given: the task box and its caption.
_BODY_FLOOR = _TASK_HEIGHT + 40.0


def _modal(
    state: LauncherState,
    *,
    running: int,
    queued: int,
    cap: int,
    launch: Callable[[LaunchSpec, Draft], None],
    wrap: bool,
    scanner: Scanner,
    prober: Prober,
    subagent_cap: int | None,
    models: tuple[str, ...],
    live: frozenset[str],
    recorder: Recorder,
    reader: CheckoutReader,
) -> None:
    """
    The open modal, begun and ended here. ``draw`` documents the arguments.

    **A scrolling body and, under it, the buttons.** The body is a child that fits
    what is drawn in it up to ``body_room`` and scrolls past that
    (``widgets.begin_dialog_body``), so the launch and cancel buttons are items of
    the window itself: they stay where they are and on the display however long
    an opened session list makes the body.

    The groups of the body, each set off from the next by ``widgets.dialog_group``:
    what the session is to do, where it runs, who runs it, and the gate it runs
    under. The one line under a control is drawn after ``widgets.dialog_caption``,
    which is what puts it nearer the control it describes than the one below.
    """
    # Before anything is drawn: the Escape that closes an explanation is not also
    # this modal's (``widgets.help_showing``).
    explaining = widgets.help_showing()
    viewport = imgui.get_main_viewport()
    imgui.set_next_window_pos(viewport.get_center(), imgui.Cond_.appearing, imgui.ImVec2(0.5, 0.5))
    # One width. Auto-resize fits the height to what is drawn; left to fit the width
    # as well, one unwrapped line widens the whole modal to its length, rightward
    # from where it stood. Every line of text below wraps to this width instead.
    #
    # The height bound restates one auto-resize already applies -- Dear ImGui 1.92.8
    # does not fit a window taller than the viewport's work area -- because the
    # move below is only a way to the launch button while that holds: a window no
    # taller than the area can be put wholly inside it.
    imgui.set_next_window_size_constraints(
        imgui.ImVec2(_WIDTH, 0.0), imgui.ImVec2(_WIDTH, viewport.work_size.y)
    )
    # Where the last frame left it, once it has been placed. Dear ImGui keeps a
    # closed popup's window under its name, so a window not drawn last frame is where
    # the modal stood the last time it was open; and one that appeared last frame
    # was drawn hidden, to be measured, with the centring above still to be applied
    # on this one -- a position set now would take its place.
    last = imgui.internal.find_window_by_name(TITLE)
    if last is not None and last.was_active and not last.appearing:
        moved = modal_origin(
            (last.pos.x, last.pos.y),
            (last.size.x, last.size.y),
            (viewport.work_pos.x, viewport.work_pos.y),
            (viewport.work_size.x, viewport.work_size.y),
        )
        if moved is not None:
            imgui.set_next_window_pos(imgui.ImVec2(*moved), imgui.Cond_.always)
    opened, _ = imgui.begin_popup_modal(TITLE, None, imgui.WindowFlags_.always_auto_resize)
    if not opened:
        # Dismissed by something other than this function -- a click on the blocked
        # background, or ImGui's own Escape handling. Resync so the key handler
        # stops deferring to a modal that is gone.
        state.is_open = False
        state.closed_without_launch()
        return

    # The body fits its content up to the room the display leaves it, and scrolls
    # past that. The footer's height is last frame's: it is drawn after the body
    # it bounds.
    widgets.begin_dialog_body(BODY_ID, state._footer_height, _BODY_FLOOR)

    # For every line of text in the body, which is what holds it to the one width.
    # The wrap position is the window's own, so the footer sets it again.
    imgui.push_text_wrap_pos(0.0)

    # Above everything, the task box included, and before the focus request below --
    # ``set_keyboard_focus_here`` lands on the next widget, which has to stay the box.
    _failure_notice(state)

    if state._focus_task:
        imgui.set_keyboard_focus_here()
        state._focus_task = False

    # Dear ImGui's rule, measured on 1.92.8: an *active* InputText owns the text and
    # copies its own over the caller's every frame, so a value assigned to
    # ``state.task`` from outside is overwritten by the call below unless the widget
    # is told to read the caller's first. A draft is assigned from outside at exactly
    # the moment the box is most likely focused -- a failure reported a frame or two
    # after the press, into a modal reopened with the cursor in it.
    #
    # Only while it is the active item, and that half is as load-bearing as the
    # first. An inactive box reads the caller's buffer by itself, and asking it to
    # reload trips an assertion in the word-wrapped draw (``text_end_display <=
    # text_end``), which arrives here as a ``RuntimeError`` on the draw thread. That
    # is the state the box is in when a restore is pressed with the mouse: the
    # button took the focus on the way down.
    if state._reload_task:
        state._reload_task = False
        task_id = imgui.get_id(_TASK_ID)
        if imgui.internal.get_active_id() == task_id:
            held = imgui.internal.get_input_text_state(task_id)
            if held is not None:
                held.reload_user_buf_and_move_to_end()

    # The same chord the reply boxes use: Ctrl+Enter launches, Enter breaks the
    # line. Sharing it is the point -- a task box and a reply box are both prompts,
    # and the binding that differed here was the one whose misfire costs the most,
    # since a stray Enter mid-sentence spawns a session on half a sentence.
    # escape_clears_all is deliberately absent on top of it: it would eat the key
    # that dismisses the modal.
    submitted, state.task = widgets.multiline_input(
        _TASK_ID,
        state.task,
        imgui.ImVec2(-1, _TASK_HEIGHT),
        wrap=wrap,
        flags=widgets.CTRL_ENTER_SUBMITS,
    )
    widgets.dialog_caption()
    widgets.notice(
        task_notice(
            resuming=state.picker.picked is not None,
            team=bool(templates.BUILT_IN[state.template_index].roles),
        )
    )

    widgets.dialog_group()

    # The working directory is the field worth the label. An orchestrator whose
    # agents all share the launching shell's cwd cannot run work across projects,
    # which is most of the point -- and it is the FLEET rail's grouping key, so a
    # typo does not fail, it quietly files the session under a project that does
    # not exist.
    imgui.set_next_item_width(-1)
    with hint_colour(imgui):
        _, state.cwd = imgui.input_text_with_hint(
            "##cwd", "working directory (agents run here)", state.cwd
        )
    widgets.dialog_caption()
    imgui.text_disabled("working directory")

    _branch_field(state, reader=reader)

    # After the directory because the listing is scoped by it.
    _session_list(state, scanner=scanner, live=live, recorder=recorder)

    # **Not how a brief is created.** A team session writes the task above as its
    # first premise at launch, because that is where the operator's premises already
    # are -- this field is for pointing a session at one that exists, which is what a
    # fork continuing its parent's work needs. Filling it in *suppresses* the seed,
    # so a session told to continue a brief does not bury it under a new one.
    #
    # Empty is the common case and must stay cheap to leave empty.
    widgets.dialog_field()
    imgui.set_next_item_width(-1)
    with hint_colour(imgui):
        _, state.brief = imgui.input_text_with_hint(
            "##brief",
            "continue an existing brief (optional) -- leave empty for a new one",
            state.brief,
        )
    widgets.dialog_caption()
    imgui.text_disabled("brief directory")

    widgets.dialog_group()
    imgui.set_next_item_width(widgets.DIALOG_FIELD_WIDTH)
    # A pick the offered list does not contain is kept and shown rather than corrected:
    # it is what ``--model`` was given, or what a probe offered on an earlier run and no
    # longer does. Dropping it would relaunch on a model the operator did not choose,
    # and silently -- the combo would simply read as something else.
    options = list(models)
    if state.model not in options:
        options.insert(0, state.model)
    changed, chosen = imgui.combo("model", options.index(state.model), options)
    if changed:
        state.model = options[chosen]
    imgui.set_next_item_width(widgets.DIALOG_FIELD_WIDTH)
    _, state.template_index = imgui.combo("team", state.template_index, list(templates.names()))
    # The shape is not obvious from a one-word name, and picking the wrong one is
    # only visible several turns later when workers start appearing -- so the
    # description is on screen rather than a tooltip away.
    widgets.dialog_caption()
    widgets.notice(Notice(templates.BUILT_IN[state.template_index].description))

    # The rung, above both of the controls below because each is read against it: the
    # cap bounds fan-out under every rung, and the containment block is the top rung's
    # own precondition rather than a control in its own right.
    widgets.dialog_group()
    imgui.set_next_item_width(widgets.DIALOG_FIELD_WIDTH)
    changed, picked = imgui.combo("gate", POLICIES.index(state.policy), [p.value for p in POLICIES])
    if changed:
        state.policy = POLICIES[picked]
    # The number in force for *this* launch: the override when there is one, and
    # otherwise the caller's setting. None when the caller supplied no setting,
    # and then there is no override control either -- seeding the box from
    # `DEFAULT_SUBAGENT_CAP` would put a figure on screen that is wrong for
    # exactly the operator who changed the setting. `_cap_line` argues that case.
    effective_cap = subagent_cap if state.subagent_cap is None else state.subagent_cap
    # One line, of the rung the launch gets and not always the one on the dial
    # (``dial_text``): what it adds, or the alarm, or what a press does instead.
    # The name of a rung is not enough to calibrate against -- PERMISSIVE reads as
    # "the gate is off" and it is not -- so what it adds is on screen at the
    # moment of choosing, derived from the classifier rather than described.
    widgets.dialog_caption()
    widgets.notice(_gate_notice(state, prober=prober, subagent_cap=effective_cap))

    # Here, with the ordinary launch fields, and not under the top rung only: the
    # cap bounds fan-out under every policy and the operator asked for an override
    # at launch, not an override under the dangerous mode. What the mode changes
    # is how much rests on the number, which `_CAP_REASON` says behind the gate's
    # question mark.
    if effective_cap is not None:
        widgets.dialog_field()
        imgui.set_next_item_width(widgets.DIALOG_FIELD_WIDTH)
        changed, typed = imgui.input_int("sub-agents", effective_cap)
        if changed:
            # Clamped, not refused. A negative cap is a drag past the end of a
            # spinner rather than an intention worth a message on screen, and zero is
            # kept because zero is a session that may not spawn at all.
            state.subagent_cap = max(0, typed)
        # Inside the guard with the control it labels. Left outside, a caller that
        # supplied no setting got the caption without the spinner -- a screen
        # describing a control that is not there, and asserting a per-launch cap when
        # nothing is overriding anything.
        widgets.dialog_caption()
        widgets.notice(Notice("sub-agents one session may run at once, for this launch only"))

    imgui.pop_text_wrap_pos()
    imgui.end_child()

    # The footer: items of the modal itself, under the child and outside its scroll.
    footer_top = imgui.get_cursor_pos().y
    imgui.push_text_wrap_pos(0.0)
    widgets.dialog_group()

    # Two separate reasons the button can be dead, kept apart because only one of them
    # has something worth printing: an empty task box explains itself, and a mode the
    # machine cannot grant does not.
    hold = state.launch_hold()
    enabled = state.ready and hold is None
    held_lines = (
        {hold: _HOLD_LINE_WORKTREE}
        if hold is not None and isinstance(state.worktree_plan(), WorktreeHeld)
        else None
    )
    if not enabled:
        imgui.begin_disabled()
    clicked = imgui.button("launch", imgui.ImVec2(110.0, 0.0))
    if not enabled:
        imgui.end_disabled()

    imgui.same_line()
    cancelled = imgui.button("cancel", imgui.ImVec2(90.0, 0.0))

    # Beside the buttons, on their row, so a reason coming or going moves nothing.
    if hold is not None:
        imgui.same_line()
        imgui.align_text_to_frame_padding()
        widgets.notice(hold_notice(hold, lines=held_lines))
    elif running >= cap:
        # Only at cap, and only as a consequence. The running/cap counter itself is
        # in the status bar, which stays visible behind the modal -- repeating it
        # here would be the same duplication the omnibox was carrying. What the
        # status bar cannot say is what happens to *this* launch, and a session
        # that sits in SPAWNING while others work looks broken to whoever just
        # pressed the button. Not beside a hold: a launch that is held does not
        # queue.
        imgui.same_line()
        imgui.align_text_to_frame_padding()
        widgets.notice(Notice(f"at cap — this one queues ({queued} ahead of it)", Tone.WARN))

    # Escape is handled explicitly rather than left to ImGui so that the precedence
    # against the layout shortcuts is stated in one place instead of split between
    # this and NavUpdate's behaviour. Not while an explanation is up: that Escape
    # closes the explanation.
    dismissed = cancelled or (not explaining and imgui.is_key_pressed(imgui.Key.escape))
    reopen = False
    pressed = False

    # ``enabled`` and not ``clicked`` alone: ``begin_disabled`` greys the button and
    # does nothing to the keyboard, so Ctrl+Enter in the task box is a second way into
    # this branch and a held launch has to be held on both.
    if enabled and (clicked or submitted):
        # The draft is taken before the clears and travels with the spec, so a launch
        # that starts nothing has something to hand back. See ``report_failure``.
        spec = state.press()
        if spec is not None:
            launch(spec, state.draft())
            state.launched()
            dismissed = True
            pressed = True
            reopen = state.failure_loaded

    if dismissed:
        state.is_open = False
        imgui.close_current_popup()
        if not pressed:
            state.closed_without_launch()
        if reopen:
            # The press left the fields blank and another failed launch's draft went
            # into them. The modal comes back with it rather than leaving a restored
            # draft behind a closed window. Only then: a cancel or Escape has to be
            # able to close a modal with a failure in it, and so does a launch made
            # past a notice that has no draft -- the failure is there on the next
            # Ctrl+N either way.
            state.request_open()

    imgui.pop_text_wrap_pos()
    state._footer_height = imgui.get_cursor_pos().y - footer_top
    imgui.end_popup()
