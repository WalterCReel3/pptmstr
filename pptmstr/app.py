"""
Application shell: runner setup, docking layout, and the frame loop (design §4.1).

The frame loop is the whole architecture in eight lines -- drain intents, apply
them, take exactly one snapshot, build from it, set the idle predicate. Everything
else in this package exists to keep those five steps honest.
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import dataclasses
import os
import queue
import signal
import tempfile
import threading
import time
import traceback
from collections.abc import Callable, Iterable
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import assert_never

from imgui_bundle import hello_imgui, imgui, immapp

from . import board as board_mod
from . import (
    boardfacts,
    boardlog,
    boardsync,
    cli_version,
    commandfile,
    commandrun,
    drawable,
    effects,
    fleetsync,
    layout_ini,
    model_catalog,
    sessionfile,
    templates,
    theme,
    tree,
)
from . import brief as brief_mod
from . import settings as settings_mod
from . import version as version_mod
from .approval import Policy, requires_containment
from .bridge import Bridge
from .command import (
    COMMAND_TAG,
    Command,
    CommandId,
    CommandState,
    End,
    EndUnknown,
    NotStarted,
    Released,
    Survivor,
)
from .command import Withdrawn as CommandWithdrawnEnd
from .driver import AgentSession, UncontainedAutonomy
from .fake_driver import FakeDriver
from .intents import (
    BoardRestored,
    CommandApproved,
    CommandEnded,
    CommandRefused,
    CommandsRestored,
    CommandStopAsked,
    FailedSessionClosed,
    FailureAcknowledged,
    Intent,
    SessionDetached,
)
from .log import LOG
from .model import (
    MAX_COMMAND_REFUSAL_CHARS,
    AgentRecord,
    AgentState,
    DetachRefusal,
    LaunchSpec,
    NodeId,
    Snapshot,
    TranscriptPresence,
)
from .pool import SessionPool
from .sessions import SessionRow
from .store import Observer, Store
from .theme import P
from .transcript import SegmentKind, Transcript
from .ui import (
    board_pane,
    brief_pane,
    command_view,
    compose,
    decision_view,
    detail,
    digest_pane,
    health,
    inbox,
    launcher,
    main_menu,
    rail,
    resume_form,
    review,
    session_details,
    session_menu,
    settings_dialog,
    transcript_pane,
    typeset,
    widgets,
)
from .ui import focus as focus_mod
from .ui.widgets import flat_line, format_elapsed

# The two arrangements of C. Same panels in both; the rail is in the same place in
# each, and the cursor survives the switch. That shared anchor is the whole
# mitigation for mode disorientation -- without it this is worse than either
# arrangement alone.
TRIAGE = "TRIAGE"
FOCUS = "FOCUS"
# The key that switches to each layout: read by ``_handle_layout_keys`` and shown
# beside the layout in the View menu (``main_menu``), so the two cannot disagree.
LAYOUT_KEYS: dict[str, int] = {TRIAGE: int(imgui.Key.tab), FOCUS: int(imgui.Key.enter)}


@dataclass(frozen=True, slots=True)
class LaunchStarted:
    """
    The session was handed to the pool.
    """


@dataclass(frozen=True, slots=True)
class LaunchRefused:
    """
    Nothing was handed to the pool, and why, in words fit to put in front of the
    operator beside the draft they pressed launch on.
    """

    reason: str


@dataclass(frozen=True, slots=True)
class LaunchHeldOnChanges:
    """
    Nothing was handed to the pool because the checkout a worktree was to be made
    from has uncommitted changes, or ``status`` could not say (``launcher.
    UncommittedFound``), and for no other reason. The draft goes back accepting
    them, so the operator's next press of it launches.
    """

    reason: str


# What ``_launch``'s coroutine returns. A value and not an exception or a log line:
# the decision is made on the asyncio thread, after the modal has closed, and the
# only thing that travels back to the draw thread is what the coroutine returns.
LaunchOutcome = LaunchStarted | LaunchRefused | LaunchHeldOnChanges


@dataclass(frozen=True, slots=True)
class _InFlight:
    """
    A launch whose outcome has not been read yet: the future ``Bridge.submit``
    returned for it, the draft the modal pressed it from if the modal did, and the
    word for where it came from.

    ``resumed`` is set for a launch the resume form pressed: the session it
    resumes and the form's draft, which is where a failure of it is handed back
    (``ResumeForms.report_failure``) instead of to the launcher.
    """

    future: concurrent.futures.Future[LaunchOutcome]
    draft: launcher.Draft | None
    origin: str
    resumed: tuple[str, resume_form.Draft] | None = None


@dataclass(frozen=True, slots=True)
class CommandHost:
    """
    What a break-glass command would be run by, as found at startup: the
    operator's login shell, or None when nothing names one that can be run, and
    what the runner takes out of pptmstr's environment first, in words.

    ``no_shell`` is why there is none, in ``commandrun``'s words, and None when
    there is one.
    """

    shell: str | None
    stripped_env: tuple[str, ...]
    no_shell: str | None = None


def _command_host() -> CommandHost:
    """
    Look once for the shell and the environment a command would be given.

    At startup and never in a frame: finding the shell stats a file and reads
    the password database.
    """
    found = commandrun.shell_for()
    stripped = commandrun.environment_changes()
    if isinstance(found, commandrun.Shell):
        return CommandHost(shell=found.path, stripped_env=stripped)
    return CommandHost(shell=None, stripped_env=stripped, no_shell=found.reason)


@dataclass(frozen=True, slots=True)
class CommandFileReport:
    """
    What became of one record of a command: ``name`` is the file
    (``commandfile.REQUEST_NAME``, ``END_NAME`` or ``STARTED_NAME``) and
    ``result`` what the write answered.
    """

    command_id: CommandId
    name: str
    result: commandfile.Written | commandfile.Unwritten


@dataclass(frozen=True, slots=True)
class CommandShortfalls:
    """
    What reading sessions' command records back fell short by, as counts: the
    log has each one's reason. ``unreadable`` are command directories that gave
    no command, ``newer`` commands a later build wrote a file of, and
    ``unlisted`` sessions whose ``commands/`` was not read whole.
    """

    unreadable: int = 0
    newer: int = 0
    unlisted: int = 0


_FILES_CLOSED = "pptmstr was shutting down"

_Write = Callable[[Command, Path | None], commandfile.Written | commandfile.Unwritten]


class CommandFiles:
    """
    The records of a break-glass command that the runner does not write, kept
    off the draw thread: ``request.json`` when a command is asked, and
    ``end.json`` for an end no process came before -- refused, withdrawn, or
    approved and never handed to a runner. ``run.json`` and every other end are
    the runner's own, written on its command's worker.

    **One worker, in the order submitted**, started by the first submit, so a
    command's request is on disk before anything queued behind it. ``request``
    and ``end`` queue and return; ``drain`` hands the draw thread what was
    settled since it last asked. ``report`` may be called from any thread: the
    runner's callback says through it that an end it wrote did not land.

    It also holds which sessions have had their records read back in this
    process (``claim_restore``), so each is read once, and what those reads
    fell short by (``shortfalls``): both are written by the recall worker and
    by the asyncio thread, under one lock, and ``shortfalls`` is replaced whole.

    A daemon, as the other writers' workers are: a write held by a dead mount
    must not hold the exit, and ``close`` waits a bounded time for it.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root
        self.shortfalls = CommandShortfalls()
        self._jobs: queue.SimpleQueue[tuple[_Write, Command, str] | None] = queue.SimpleQueue()
        self._reports: queue.SimpleQueue[CommandFileReport] = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._closed = False
        self._restored: set[str] = set()

    def request(self, command: Command) -> None:
        """Queue ``request.json`` for a command that was just asked."""
        self._submit(commandfile.write_request, command, commandfile.REQUEST_NAME)

    def end(self, command: Command) -> None:
        """Queue ``end.json`` for a command that ended with no run recorded."""
        self._submit(commandfile.write_end, command, commandfile.END_NAME)

    def report(self, report: CommandFileReport) -> None:
        self._reports.put(report)

    def drain(self) -> tuple[CommandFileReport, ...]:
        out: list[CommandFileReport] = []
        while True:
            try:
                out.append(self._reports.get_nowait())
            except queue.Empty:
                return tuple(out)

    def claim_restore(self, session_id: str) -> bool:
        """
        Whether the caller is the one to read ``session_id``'s records back:
        True once per session, and False ever after.
        """
        with self._lock:
            if session_id in self._restored:
                return False
            self._restored.add(session_id)
            return True

    def fell_short(self, unreadable: int, newer: int, unlisted: int) -> None:
        with self._lock:
            held = self.shortfalls
            self.shortfalls = CommandShortfalls(
                unreadable=held.unreadable + unreadable,
                newer=held.newer + newer,
                unlisted=held.unlisted + unlisted,
            )

    def close(self, timeout: float) -> bool:
        """
        Let the worker finish what is queued, for at most ``timeout`` seconds.
        False when it had not finished. Nothing is written after this: a later
        submit is reported unwritten.
        """
        with self._lock:
            self._closed = True
            worker = self._worker
        if worker is None:
            return True
        self._jobs.put(None)
        worker.join(timeout)
        return not worker.is_alive()

    def _submit(self, write: _Write, command: Command, name: str) -> None:
        with self._lock:
            closed = self._closed
            if not closed and self._worker is None:
                self._worker = threading.Thread(
                    target=self._work, name="command-files", daemon=True
                )
                self._worker.start()
        if closed:
            self.report(CommandFileReport(command.id, name, commandfile.Unwritten(_FILES_CLOSED)))
            return
        self._jobs.put((write, command, name))

    def _work(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            write, command, name = job
            result: commandfile.Written | commandfile.Unwritten
            try:
                result = write(command, self.root)
            except Exception as exc:  # noqa: BLE001 - one record's failure must not end the worker
                result = commandfile.Unwritten(f"{type(exc).__name__}: {exc}")
            self.report(CommandFileReport(command.id, name, result))


# Why an approved command is ended at once in a process with no runner.
_NO_RUNNER = "this build is not running commands: "
_NO_RUNNER_FAKE = "the fake driver runs nothing on this machine"
_NO_RUNNER_NONE = "no runner was started"

# How long past its own timeout a command handed to the runner is waited for
# before the shell ends it itself (``_check_for_overdue_commands``). The runner
# needs, beyond the timeout, the time between its two signals, its wait for the
# shell to be reaped, its last read of the pipe and two small writes; the rest
# is margin for a disk that is slow and not dead.
COMMAND_OVERDUE_S = commandrun.KILL_GRACE_S + commandrun.REAP_S + commandrun.DRAIN_S + 60.0

OVERDUE = (
    "pptmstr's runner reported no end for it within its timeout and"
    f" {COMMAND_OVERDUE_S:.0f}s more"
)


def _command_ended(state: AppState) -> Callable[[commandrun.Ended], None]:
    """
    The runner's callback: a command's end, put on the queue as the intent that
    closes it, and a report when the runner could not write ``end.json`` or
    ``started.json``. Processes it started that outlived it in a session of
    their own are logged. What was taken out of the environment for this command's
    directory, beyond what is taken out for every directory and shown on its
    row, is logged: the row is drawn before the directory is looked at.

    **Called on the command's worker thread.** ``Bridge.emit`` is a put on a
    ``queue.Queue`` and is the crossing every other thread uses; the report is a
    put on a ``SimpleQueue``. Neither blocks, and nothing here touches the store
    or a pane. It raises nothing: the runner hands what a callback raises to
    ``threading.excepthook``, and the intent would be lost with it.
    """

    def on_end(ended: commandrun.Ended) -> None:
        try:
            end = ended.command.end
            if end is None:
                return
            state.bridge.emit(CommandEnded(ended.command.id, end))
            files = state.command_files
            if files is not None and isinstance(ended.record, commandfile.Unwritten):
                files.report(
                    CommandFileReport(ended.command.id, commandfile.END_NAME, ended.record)
                )
            if files is not None and isinstance(ended.started, commandfile.Unwritten):
                files.report(
                    CommandFileReport(ended.command.id, commandfile.STARTED_NAME, ended.started)
                )
            if end.escaped_remark is not None:
                LOG.warn("command", f"{ended.command.id}: {end.escaped_remark}")
            host = state.command_host
            shown = () if host is None else host.stripped_env
            for line in ended.taken_out:
                if line not in shown:
                    LOG.warn("command", f"{ended.command.id} was run without it: {line}")
        except Exception as exc:  # noqa: BLE001 - see the docstring: this must not raise
            LOG.error("command", f"a command's end was not reported: {type(exc).__name__}: {exc}")

    return on_end


def _end_unstarted(state: AppState, command: Command, reason: str) -> None:
    """
    End, as ``NotStarted``, a command the store ordered run and no runner took.
    On the draw thread.

    The end goes through the queue like the runner's, and is applied on the
    next frame. Its ``end.json`` is queued here and says nothing was approved:
    no ``run.json`` was written for it, and an end that follows a run is
    refused without one (``commandfile.write_end``).
    """
    end = End(outcome=NotStarted(reason), at=time.time())
    state.command_handed.pop(command.id, None)
    state.bridge.emit(CommandEnded(command.id, end))
    LOG.error("command", f"{command.id} was approved and did not start: {reason}")
    _not_done(state, f"{command.id} was approved and did not start: {reason}")
    files = state.command_files
    if files is not None:
        files.end(dataclasses.replace(command, run=None, stop_asked_at=None, end=end))


def _run_command(state: AppState, command: Command) -> None:
    """
    Carry out the store's order to run ``command``: hand it to the runner, or
    end it here. **Every order ends in one of the two**, so no command is left
    running with nothing behind it.

    ``Runner.start`` reserves a slot, starts a thread and returns: no file is
    touched and nothing is waited for, except where no thread can be made at
    all, which the runner answers by producing the end on this thread.

    What is handed over is the record the store built from the approval, whole:
    the string, the shell, the directory and the timeout are its ``run``'s.
    """
    runner = state.command_runner
    if runner is None:
        why = _NO_RUNNER_FAKE if state.fake else _NO_RUNNER_NONE
        _end_unstarted(state, command, _NO_RUNNER + why)
        return
    try:
        runner.start(command)
    except Exception as exc:  # noqa: BLE001 - an order that raised is still owed an end
        LOG.debug("command", traceback.format_exc())
        _end_unstarted(state, command, f"the runner did not take it: {type(exc).__name__}: {exc}")
        return
    bound = command.run.timeout_s if command.run is not None else 0.0
    state.command_handed[command.id] = time.monotonic() + bound + COMMAND_OVERDUE_S


def _stop_command(state: AppState, command_id: CommandId) -> None:
    """
    Carry out the store's order to kill a running command. The end is the
    runner's to report; this only asks.
    """
    runner = state.command_runner
    if runner is None:
        return
    try:
        runner.stop(command_id, at=time.time())
    except Exception as exc:  # noqa: BLE001 - said to the operator, who believes it was asked
        reason = f"{type(exc).__name__}: {exc}"
        LOG.error("command", f"{command_id}: the cancel did not reach the runner: {reason}")
        _not_done(
            state,
            f"{command_id} was not cancelled: the runner could not be asked ({reason});"
            " its timeout still ends it",
        )


def _check_for_overdue_commands(state: AppState, now: float | None = None) -> None:
    """
    End, as ``EndUnknown``, a command that was handed to the runner and that
    the runner has reported no end for ``COMMAND_OVERDUE_S`` past its timeout.

    The runner ends everything it takes, and this is for the case where that
    did not hold -- its worker died, or is held by a write that will not
    return. Without it such a command is "running" for good: its row stays, its
    session can run nothing else, and no cancel reaches a worker that is gone.
    ``EndUnknown`` is the honest end: the process may have finished, may have
    been killed, and may be alive. Nothing is written for it, since no
    ``end.json`` is what that outcome means on disk. The runner is told to give
    the command up (``_abandon_command``), and the end it records if its worker
    ever returns is ``EndUnknown`` too.

    On the monotonic clock, as the runner's timeout is: the wall clock moves
    across a suspend and the timeout does not.
    """
    if not state.command_handed:
        return
    snap = state.frame_snap
    if snap is None:
        return
    now = time.monotonic() if now is None else now
    for command_id, due in list(state.command_handed.items()):
        held = snap.commands.get(command_id)
        if held is None or held.state is not CommandState.RUNNING:
            del state.command_handed[command_id]
        elif now >= due:
            del state.command_handed[command_id]
            state.bridge.emit(CommandEnded(command_id, End(outcome=EndUnknown(), at=time.time())))
            LOG.error("command", f"{command_id}: {OVERDUE}; it is shown as ended, end unknown")
            killed = _abandon_command(state, command_id)
            _not_done(state, f"{command_id}: {OVERDUE}. {killed}")


ABANDONED = (
    "Every process group found in its shell's session is being sent SIGKILL and its session"
    " here can be given another command; whether anything of it survived was not seen"
)
NOT_ABANDONED = "It may still be running: the runner could not be told to give it up"
NOT_HELD = (
    "It may still be running: the runner no longer holds it, so there was nothing it" " could kill"
)


def _abandon_command(state: AppState, command_id: CommandId) -> str:
    """
    Have the runner give up a command the store has just ended without it, and
    say what that did. The runner frees the session's slot and kills what it
    finds in the shell's session from a thread of its own (``Runner.abandon``):
    without that the store would show the session free while the runner turned
    every later command of it away.
    """
    runner = state.command_runner
    if runner is None:
        return NOT_ABANDONED
    try:
        held = runner.abandon(command_id)
    except Exception as exc:  # noqa: BLE001 - said to the operator in the same notice
        LOG.error("command", f"{command_id}: not abandoned: {type(exc).__name__}: {exc}")
        return NOT_ABANDONED
    return ABANDONED if held else NOT_HELD


def _record_commands(state: AppState) -> Observer:
    """
    The commands' half of the observer: what each applied step means for a
    command's files, queued for ``CommandFiles``, and which commands came from
    a file.

    Derived from the two snapshots and not from the intent, so a request the
    store refused writes nothing: a command that appeared is asked, and gets
    its ``request.json``; one that gained an end with no run was refused or
    withdrawn, and gets its ``end.json``. A command that appeared under a
    ``CommandsRestored`` is one a file already holds, and is only noted
    (``AppState.command_restored``).

    **It does not raise**, for the reason ``_record_board`` gives. A step it
    could not derive is a record a command does not get, said once.
    """

    def observe(before: Snapshot, intent: Intent, after: Snapshot, now: float) -> None:
        if after.commands is before.commands:
            return
        try:
            files = state.command_files
            from_file = isinstance(intent, CommandsRestored)
            for command_id, command in after.commands.items():
                was = before.commands.get(command_id)
                if was is None:
                    if from_file:
                        state.command_restored.add(command_id)
                    elif files is not None and command.state is CommandState.ASKED:
                        state.command_pending.add(command_id)
                        files.request(command)
                elif (
                    files is not None
                    and was.end is None
                    and command.end is not None
                    and command.run is None
                ):
                    files.end(command)
        except Exception as exc:  # noqa: BLE001 - see the docstring: this must not raise
            reason = f"{type(exc).__name__}: {exc}"
            if state.command_observer_failed != reason:
                state.command_observer_failed = reason
                LOG.error("command", f"a command's record could not be derived: {reason}")
                LOG.debug("command", traceback.format_exc())

    return observe


_RECORD_OF = {
    commandfile.REQUEST_NAME: "what was asked",
    commandfile.END_NAME: "how it ended",
    commandfile.STARTED_NAME: "which process it became",
}


def _read_command_reports(state: AppState) -> None:
    """
    Fold what ``CommandFiles`` settled since the last frame into what the rows
    are drawn with, on the draw thread: a request that is now on disk may be
    run, and a record that did not land is kept, in words, for its row and for
    DETAIL. The first failure of a command is the one kept, and it is logged
    once.
    """
    files = state.command_files
    if files is None:
        return
    for report in files.drain():
        if report.name == commandfile.REQUEST_NAME:
            state.command_pending.discard(report.command_id)
        if isinstance(report.result, commandfile.Written):
            continue
        if report.command_id in state.command_unrecorded:
            continue
        what = _RECORD_OF.get(report.name, report.name)
        said = f"{what} is not recorded on disk: {report.result.reason}"
        state.command_unrecorded[report.command_id] = said
        LOG.error("command", f"{report.command_id}: {said}")


NOT_RUN = "not run: "
NOT_REFUSED = "not refused: "
NOT_CANCELLED = "not cancelled: "
NO_SESSION_DIRECTORY = (
    "this session's record names no directory, and a command runs in its session's"
)
REQUEST_UNRECORDED = "nothing runs without a record of what was asked, and this command has none"
REQUEST_PENDING = (
    "what was asked is still being written to disk, and nothing runs before it is."
    " Press run again"
)
NO_LONGER_ASKED = "it is no longer waiting for you"
NO_LONGER_RUNNING = "it is not running"


def _approve_command(state: AppState, command_id: CommandId, text: str, timeout_s: int) -> None:
    """
    The row's second press of Run: send the operator's approval, or say why
    nothing was sent.

    **The store is asked first** (``Snapshot.command_approval_refusal``), on
    the snapshot the frame was drawn from. The reducer's arm is a silent no-op
    for an approval it would not apply, and nobody is parked on one to be told.

    The shell is the one found at startup and the directory is the session's
    own, read off its root's record: the tool told the agent the command runs
    in the session's directory. A command whose request is not on disk is not
    approved here, although the runner would end it ``NotStarted`` for the same
    reason: that would spend the asker's request on a failure this already
    knows of, and the operator can refuse it with the reason instead.
    """
    snap = state.frame_snap
    if snap is None:
        return
    refusal = snap.command_approval_refusal(command_id, text, timeout_s)
    if refusal is not None:
        _not_done(state, NOT_RUN + (_ended_first(snap, command_id) or refusal.sentence))
        return
    host = state.command_host
    if host is None or host.shell is None:
        why = "no shell was looked for" if host is None else (host.no_shell or "no usable shell")
        _not_done(state, f"{NOT_RUN}there is no shell to give it to ({why})")
        return
    root = snap.nodes.get((snap.commands[command_id].session_id, None))
    cwd = root.cwd if root is not None else None
    if not cwd:
        _not_done(state, NOT_RUN + NO_SESSION_DIRECTORY)
        return
    if command_id in state.command_unrecorded:
        _not_done(state, f"{NOT_RUN}{REQUEST_UNRECORDED} ({state.command_unrecorded[command_id]})")
        return
    if command_id in state.command_pending:
        _not_done(state, NOT_RUN + REQUEST_PENDING)
        return
    state.bridge.emit(
        CommandApproved(
            command_id=command_id,
            text=text,
            shell=host.shell,
            cwd=cwd,
            timeout_s=timeout_s,
            at=time.time(),
        )
    )
    state.command_approving.add(command_id)


WITHDRAWN_FIRST = "its asker withdrew it before it could run"
NOT_APPLIED = (
    "the approval was not applied, and nothing was started. Read the row again: it"
    " says where the command stands now"
)


def _ended_first(snap: Snapshot, command_id: CommandId) -> str | None:
    """
    Why a command the operator pressed Run on has no run, when the reason is
    that it ended first, or None.
    """
    held = snap.commands.get(command_id)
    if held is None or held.end is None or held.run is not None:
        return None
    if isinstance(held.end.outcome, CommandWithdrawnEnd):
        return WITHDRAWN_FIRST
    return None


def _check_for_unstarted_runs(state: AppState) -> None:
    """
    Say so when an approval the operator sent started nothing.

    The row asks the store before it sends (``_approve_command``), on the
    snapshot the frame was drawn from, and the asker's withdrawal may already
    be in the queue behind that snapshot. The reducer then applies the
    withdrawal and drops the approval without a word, and the operator, who
    pressed Run twice, would be left believing a command ran.

    Called once a frame after the queue is applied, so every id here was sent
    in an earlier frame and has been through the reducer.
    """
    if not state.command_approving:
        return
    snap = state.frame_snap
    if snap is None:
        return
    sent, state.command_approving = state.command_approving, set()
    for command_id in sorted(sent):
        held = snap.commands.get(command_id)
        if held is not None and held.run is not None:
            continue
        _not_done(state, NOT_RUN + (_ended_first(snap, command_id) or NOT_APPLIED))


def _refuse_command(state: AppState, command_id: CommandId, reason: str) -> None:
    """
    The row's second press of Refuse. A reason over the store's bound is not
    sent and not cut: the arm would drop it without a word.
    """
    snap = state.frame_snap
    if snap is None:
        return
    held = snap.commands.get(command_id)
    if held is None or held.state is not CommandState.ASKED:
        _not_done(state, NOT_REFUSED + NO_LONGER_ASKED)
        return
    if len(reason) > MAX_COMMAND_REFUSAL_CHARS:
        _not_done(
            state,
            f"{NOT_REFUSED}the reason is {len(reason) - MAX_COMMAND_REFUSAL_CHARS:,} characters"
            f" over the {MAX_COMMAND_REFUSAL_CHARS:,} one may be. Nothing was sent",
        )
        return
    state.bridge.emit(CommandRefused(command_id=command_id, reason=reason, at=time.time()))


def _cancel_command(state: AppState, command_id: CommandId) -> None:
    """The row's second press of Cancel, for a command that is running."""
    snap = state.frame_snap
    if snap is None:
        return
    held = snap.commands.get(command_id)
    if held is None or held.state is not CommandState.RUNNING:
        _not_done(state, NOT_CANCELLED + NO_LONGER_RUNNING)
        return
    state.bridge.emit(CommandStopAsked(command_id=command_id, at=time.time()))


def _command_rows(state: AppState) -> inbox.CommandRows | None:
    """
    What the inbox and DETAIL draw a command with this frame, or None when no
    shell was looked for.

    The three presses are the operator's and reach the store as intents; what
    the store then orders is carried out by ``apply_intents``. With no shell
    the view offers no Run, and refuse still works.
    """
    host = state.command_host
    if host is None:
        return None
    restored: AbstractSet[CommandId] = state.command_restored
    return inbox.CommandRows(
        state=state.commands,
        shell=host.shell,
        stripped_env=host.stripped_env,
        actions=command_view.Actions(
            on_run=lambda command_id, text, timeout_s: _approve_command(
                state, command_id, text, timeout_s
            ),
            on_refuse=lambda command_id, reason: _refuse_command(state, command_id, reason),
            on_stop=lambda command_id: _cancel_command(state, command_id),
        ),
        now=state.frame_wall,
        wired=True,
        unrecorded=state.command_unrecorded,
        restored=restored,
        on_check=lambda command_id: _check_survivor(state, command_id),
        checked=state.command_checked,
        on_release=lambda command_id: _release_survivor(state, command_id),
    )


@dataclass
class AppState:
    """
    Everything the callbacks need. Not application state in the store's sense -- it
    is the wiring, plus the presentation state the store deliberately does not own.
    """

    store: Store
    bridge: Bridge
    settings: settings_mod.Settings
    # One cursor. It is either on an obligation or on a node, and whichever it is
    # on, the other is derived -- see ui/focus.py for why two were unfixable.
    focus: focus_mod.FocusState = field(default_factory=focus_mod.FocusState)
    # The card an open Session menu was opened for; None while it is closed.
    menu_latch: main_menu.Latch | None = None
    review: review.ReviewState = field(default_factory=review.ReviewState)
    detail_pane_state: detail.DetailState = field(default_factory=detail.DetailState)
    compose: compose.ComposeState = field(default_factory=compose.ComposeState)
    # The operator's unsent answers to decisions, typed at the inbox row and read
    # by DETAIL: one object, so the two panes cannot hold different drafts.
    decisions: decision_view.DecisionState = field(default_factory=decision_view.DecisionState)
    # What the operator is part-way through on a break-glass command's row: an
    # edit, a timeout, an armed press. One object for the inbox and DETAIL.
    commands: command_view.CommandViewState = field(default_factory=command_view.CommandViewState)
    # The shell a command would be given to and what is taken out of its
    # environment, looked for once by ``_start``. None is a state nothing looked
    # for, and then no pane is handed anything to draw a command with
    # (``_command_rows``): "no usable shell" is a finding, and this is not one.
    command_host: CommandHost | None = None
    # The one runner of break-glass commands in this process, or None: this is
    # the fake driver's run. With none, an order to run is ended at once
    # (``_run_command``).
    command_runner: commandrun.Runner | None = None
    # The writer of the command records the runner does not write, or None
    # under the fake driver, which records nothing.
    command_files: CommandFiles | None = None
    # Commands whose ``request.json`` is queued and not yet reported. Run is
    # not sent for one of these. Draw thread only, as the three below are.
    command_pending: set[CommandId] = field(default_factory=set)
    # Why a command's record is not on disk, in words, by command: the first
    # failure of each. Shown on its row and in DETAIL.
    command_unrecorded: dict[CommandId, str] = field(default_factory=dict)
    # The commands that reached the store from a file and not from an agent in
    # this window. A file does not say whether the asker read the result.
    command_restored: set[CommandId] = field(default_factory=set)
    # When each command handed to the runner is overdue, on the monotonic
    # clock. See ``_check_for_overdue_commands``.
    command_handed: dict[CommandId, float] = field(default_factory=dict)
    # The approvals sent since the queue was last applied, to be looked for in
    # the next snapshot. See ``_check_for_unstarted_runs``.
    command_approving: set[CommandId] = field(default_factory=set)
    # What the last look for each command left running unsupervised found, in
    # words, and the looks that have answered and not been read yet: a look
    # reads ``/proc`` and a file, on a thread of its own.
    command_checked: dict[CommandId, str] = field(default_factory=dict)
    command_checks: queue.SimpleQueue[tuple[CommandId, str]] = field(
        default_factory=queue.SimpleQueue
    )
    # The command observer's last failure, so one that fails on every step is
    # said once. See ``_record_commands``.
    command_observer_failed: str | None = None
    launcher: launcher.LauncherState = field(default_factory=launcher.LauncherState)
    settings_dialog: settings_dialog.DialogState = field(
        default_factory=settings_dialog.DialogState
    )
    # Every session's resume form, and which one is up. Its own modal and not
    # the launcher's: see ``ui/resume_form``.
    resume: resume_form.ResumeForms = field(default_factory=resume_form.ResumeForms)
    # Which models the launcher offers. Probed once on a worker at startup and the list
    # in code until that answers, so it is readable from the first frame and there is no
    # "not yet known" state for a caller to draw.
    catalog: model_catalog.Catalog = field(default_factory=model_catalog.Catalog)
    rail: rail.RailState = field(default_factory=rail.RailState)
    # The card menus of the fleet pane, and the two modals they open: Details and
    # the close confirmation. One object, which is also what the frame asks
    # whether its keys belong to an open menu (``_handle_keys``).
    cards: session_menu.MenuState = field(default_factory=session_menu.MenuState)
    board: board_pane.BoardState = field(default_factory=board_pane.BoardState)
    brief: brief_pane.BriefState = field(default_factory=brief_pane.BriefState)
    transcripts: transcript_pane.TranscriptState = field(
        default_factory=transcript_pane.TranscriptState
    )
    # How tall SESSION's composer and the separator above it were last frame,
    # which is what the scrollback above them leaves room for (``_scrollback_height``).
    # Until a frame has measured it, a little more than the one measured at the
    # defaults: 152 px of composer and 11 of separator at 1500x900.
    composer_room: float = 170.0
    frame: int = 0
    # Set when the palette changes; consumed at the top of the next frame. See
    # _apply_theme_if_dirty for why this cannot simply happen in post_init.
    theme_dirty: bool = True
    runner: hello_imgui.RunnerParams | None = None
    # The snapshot for the frame currently being built. Read once in pre_new_frame
    # and handed to every panel, so two panels can never disagree about the world.
    frame_snap: Snapshot | None = None
    frame_now: float = 0.0
    # The wall clock, read once beside ``frame_now``. What a closed card's "3h
    # ago" is measured against: its times are a file's, and the monotonic clock
    # above means nothing to the process that wrote them.
    frame_wall: float = 0.0
    # When the parked-future count first exceeded what the queue is showing.
    # None while they agree. See _check_for_lost_approvals.
    lost_since: float | None = None
    lost_reported: bool = False
    # Same idea for the bus's crossing. See _check_for_stranded_requests.
    stranded_since: float | None = None
    stranded_reported: bool = False
    # Latched while the frame loop is not draining and agents are parked behind it.
    # No "since" of its own: Bridge stamps every drain, so the age is a fact the
    # Bridge already holds and a second copy here would be one more thing to keep
    # true. See _check_for_drain_stall.
    stall_reported: bool = False
    driver: FakeDriver | None = None
    pool: SessionPool | None = None
    # Every launch whose outcome has not been read yet. Appended by ``_launch``, which
    # the command line's tasks call on the asyncio thread and everything else calls on
    # the draw thread; read and emptied by ``_reap_launches`` on the draw thread. It
    # is unlocked, and what makes that sound is in ``_reap_launches``.
    launches: list[_InFlight] = field(default_factory=list)
    # Layout asked for on the command line, applied once the runner is up.
    # switch_layout needs a live runner, so it cannot happen during setup.
    pending_layout: str | None = None
    # A pane to bring to the front of its tab bar, by its label, applied at the
    # top of the next frame after any layout switch asked for with it -- the pane
    # may exist only in the layout being switched to.
    pending_pane: str | None = None
    # The one appender of board logs in this process, or None when no board is
    # recorded: ``Settings.board_log`` is off, or this is the fake driver. None
    # is the whole of the switch -- with no writer nothing is loaded, submitted
    # or created. See ``_record_board``.
    board_writer: boardsync.BoardWriter | None = None
    # Why ``settings.board_log`` is off when that is not what the file says: what
    # ``settings.read`` could not read at startup. None for a clean read. It is
    # the reason the pane gives for an unrecorded board, and what ``_save`` hands
    # ``settings.save`` so that the fail-safe value is never written back.
    board_log_unread: settings_mod.BoardLogUnread | None = None
    # The first save ``settings.save`` refused, kept because the operator is told
    # of it once (``_save``) and the status bar goes on saying it. None while
    # every save has been taken.
    unsaved: settings_mod.Unsaved | None = None
    # What is known of each session's log, by session id. The asyncio thread adds
    # a session when its launch is taken by the pool and never touches the entry
    # again; the draw thread replaces entries that exist and adds none. So no key
    # has two writers, and it is unlocked for the reason ``launches`` is.
    board_logs: dict[str, board_pane.LogState] = field(default_factory=dict)
    # The addresses each restored session's log had for its earlier agents, kept
    # because a session is read back once per process and may be relaunched more
    # than once: every later run under the id is constructed with the same table.
    # Read and written on the asyncio thread only.
    restored_addresses: dict[str, dict[str, str | None]] = field(default_factory=dict)
    # The newest session the pool took under each id. A relaunch under an id this
    # process has already run is constructed with that run's address table
    # (``_carried_addresses``): the store still holds the earlier run's sub-agent
    # records and the board names them, so a driver that started an empty table
    # would hand their names out again. Kept after the pool lets a session go,
    # because the records are. Written on the asyncio thread only, which is the
    # thread a session's table changes on. The draw thread reads one entry of it,
    # in ``_held``, for the facts Details shows that only the session object has:
    # one lookup, and attributes that are each written whole.
    ran: dict[str, AgentSession] = field(default_factory=dict)
    # The one writer of session files in this process. Always there, the fake
    # driver's run included, because it is also what answers "when was this
    # session last active" (``FleetWriter.last_active``). It writes only for a
    # session it was told to admit, and ``fake`` is what stops the telling.
    fleet_writer: fleetsync.FleetWriter = field(default_factory=fleetsync.FleetWriter)
    # Whether this is the scripted driver's run. No session is admitted to the
    # fleet writer and no fleet is recalled: a file written here would come back
    # at the next real start as a card for a session the CLI never had.
    fake: bool = False
    # What ``boardsync.migrate`` did at startup, or None when it was not run:
    # board logging is off, or this is the fake driver. Read for the reason a
    # session's log was left at the old path (``_left_behind``).
    migration: boardsync.Migration | None = None
    # What the session files said the fleet was, or None until the recall worker
    # has answered -- and for good under the fake driver. Assigned once and
    # whole by that worker; the draw thread reads the reference.
    fleet: fleetsync.Fleet | None = None
    # Where the CLI's naming puts each recalled session's transcript, by session
    # id, for the line a closed card's transcript pane draws. Worked out by the
    # recall worker, because the naming resolves the directory, and assigned
    # whole before the first recalled card is emitted.
    closed_paths: dict[str, str] = field(default_factory=dict)
    # The last refusal the store gave to taking each session off the fleet, by
    # session id. An entry is dropped when a later ask for the session is taken.
    # Written by ``apply_intents`` alone.
    detach_refused: dict[str, DetachRefusal] = field(default_factory=dict)
    # The fleet observer's last failure, so one that fails on every step is said
    # once. See ``_record_fleet``.
    fleet_observer_failed: str | None = None
    # Why the recall worker ended without an answer, or None. ``fleet`` stays
    # None then, which is also what it is before the worker has answered, so
    # this is what tells the status bar the closed cards are not coming.
    recall_failed: str | None = None
    # The last text each session was handed in this window, by session id: the
    # opening text of its latest run that reached the CLI, or a later send its
    # client took. What the resume form's message box opens with for a session
    # that ran here; a closed card's comes from its file (``Recalled``). Written
    # on the asyncio thread, one whole string per key; the draw thread reads one
    # entry when a form is opened.
    last_sent: dict[str, str] = field(default_factory=dict)
    # What a card's control was asked to do and did not, in words, oldest first:
    # a removal the store or the pool refused, a gate move the driver or the
    # directory refused. Shown until dismissed (``_draw_not_done``). Appended
    # and cleared on the draw thread only.
    not_done: list[str] = field(default_factory=list)
    # How many frames the notice for ``not_done`` has been drawn for; its keys
    # are read from the second, as a card's confirmation reads its own.
    not_done_frames: int = 0
    # Held across "read or move a session's gate, then queue what the file is
    # to say of it". Two threads do that: the draw thread moves a gate and
    # queues the move, and the asyncio thread reads the gate a run started
    # under and queues the launch. The writer keeps the later of two values
    # queued for one key, so the read and its queueing must not straddle the
    # other thread's move and its queueing, or the file keeps the older rung.
    # Nothing blocks with it held: a queueing performs no IO.
    gate_writes: threading.Lock = field(default_factory=threading.Lock, repr=False)


# Why no board is recorded in a process that has no writer, for the board pane:
# the two that are somebody's choice. The third is ``AppState.board_log_unread``.
_LOG_SWITCHED_OFF = "switched off: board_log in settings.json"
_LOG_FAKE = "the fake driver's boards are not logged"


def apply_intents(state: AppState) -> None:
    """
    Drain the queue, apply it, answer what it asked, and hand what it did to
    the board log and to the session files. The part of a frame that changes the
    store, and the only production caller of ``Store.apply_all``.

    **No file is touched here.** ``_record_board``, ``_record_fleet`` and
    ``_record_commands`` derive values and queue them, and each writer's
    ``drain`` copies a list under a lock no write holds; the writes are on the
    writers' own threads. An order to run a command is handed to the runner,
    which does everything for it on a thread of that command's own.

    **The observer is built whether or not a board is recorded.** A session's
    file is written with ``board_log`` off as well, and the two halves fail
    apart: see ``_observer``.
    """
    writer = state.board_writer
    # One clock for the whole batch, the same one the frame is built against, so a
    # node's state_since and the wait times drawn from it agree to the frame.
    settled = state.store.apply_all(
        state.bridge.drain(), now=state.frame_now, observe=_observer(state, writer)
    )
    # Answered here, in the same breath as the apply that produced them. Every
    # effect corresponds to an intent this batch applied, so nothing is replied to
    # speculatively and nothing survives the frame -- which is what keeps a bus
    # request from becoming a second kind of lost approval.
    for effect in settled:
        if isinstance(effect, effects.SessionDetachSettled):
            # The operator's own ask and not an agent's: nobody is parked on it,
            # so it is kept for the surface that asked and the bridge is not
            # handed an effect it has no request for.
            _note_detach(state, effect)
            continue
        if isinstance(effect, effects.CommandToRun):
            # An order to this shell and not an answer to anybody. Handed to
            # the bridge it would be dropped, and the command would be running
            # with nothing behind it.
            _run_command(state, effect.command)
            continue
        if isinstance(effect, effects.CommandToStop):
            _stop_command(state, effect.command_id)
            continue
        state.bridge.settle(effect)
    if writer is not None:
        _read_board_reports(state, writer.drain())
    _read_fleet_reports(state.fleet_writer.drain())
    _read_command_reports(state)


def _note_detach(state: AppState, effect: effects.SessionDetachSettled) -> None:
    """
    Keep what the store said to an ask to take a session off the fleet: the
    refusal, for whichever surface shows it, or nothing once the ask was taken.
    """
    if effect.refusal is None:
        state.detach_refused.pop(effect.session_id, None)
        return
    state.detach_refused[effect.session_id] = effect.refusal
    LOG.warn(
        "fleet",
        f"{effect.session_id} was not removed from the fleet: {effect.refusal.name}",
    )
    # The menu greys the item while the store would refuse, so this is an ask
    # that was good when it was drawn and not when it was applied.
    _not_done(state, f"not removed from the fleet: {session_menu.REMOVAL_REFUSED[effect.refusal]}")


def _not_done(state: AppState, what: str) -> None:
    """
    Put a control's refusal in front of the operator (``_draw_not_done``). On
    the draw thread. The same sentence twice in a row is said once.
    """
    if not state.not_done or state.not_done[-1] != what:
        state.not_done.append(what)


def _observer(state: AppState, writer: boardsync.BoardWriter | None) -> Observer:
    """
    The observer one batch is applied under: the board log's half, when a board
    is recorded, then the session files', then the commands'.

    **Halves that cannot fail each other.** Each catches everything it
    raises, for the reason ``_record_board`` gives, and each in its own handler:
    the board half's ends every board log for the run, which is the right answer
    to a fact that could not be derived and the wrong one to a session file that
    could not be.
    """
    board = None if writer is None else _record_board(state, writer)
    fleet = _record_fleet(state)
    commands = _record_commands(state)

    def observe(before: Snapshot, intent: Intent, after: Snapshot, now: float) -> None:
        if board is not None:
            board(before, intent, after, now)
        fleet(before, intent, after, now)
        commands(before, intent, after, now)

    return observe


def _record_fleet(state: AppState) -> Observer:
    """
    The session files' half of the observer: what each applied step means for a
    session's file (``fleetsync.changes``), queued for the fleet writer.

    Derived for every step and submitted for every session. Which of them have a
    file is the writer's to know: it drops a change for a session it was never
    told to admit, which is every session of the scripted driver.

    The wall clock is read once per batch, on the first step, as the board
    half's is.

    **It does not raise, and a failure stops nothing.** The step that could not
    be derived is one write a file does not get; the next step is derived as
    usual, because each change says what is true after it and not what moved.
    Said once per distinct failure and not once per step.
    """
    wall: list[float] = []

    def observe(before: Snapshot, intent: Intent, after: Snapshot, now: float) -> None:
        try:
            if not wall:
                wall.append(time.time())
            for change in fleetsync.changes(before, intent, after, wall[0]):
                state.fleet_writer.submit(change)
        except Exception as exc:  # noqa: BLE001 - see the docstring: this must not raise
            reason = f"{type(exc).__name__}: {exc}"
            if state.fleet_observer_failed != reason:
                state.fleet_observer_failed = reason
                LOG.error(
                    "fleet",
                    f"a session file change could not be derived ({reason}); the file"
                    " keeps what it said until that session's next change",
                )
                LOG.debug("fleet", traceback.format_exc())

    return observe


def _read_fleet_reports(reports: tuple[fleetsync.Report, ...]) -> None:
    """
    Say what the fleet writer settled since the last frame that the operator is
    owed a line for, on the draw thread.

    A session whose file is not this build's to write is said once: the writer
    reports the latch once. A change of whether a session is on the fleet that
    did not reach its file is said every time, because the next start will show
    the fleet as it was before the operator changed it. Any other write that did
    not land is the file missing a time or a cost, and is in the debug log.
    """
    for report in reports:
        match report:
            case fleetsync.Latched(session_id=session_id, reason=reason):
                LOG.warn(
                    "fleet",
                    f"{session_id}: its session file is not written again in this run:"
                    f" {reason}",
                )
            case fleetsync.Outcome(session_id=session_id, result=result):
                if report.written:
                    continue
                assert isinstance(result, sessionfile.Unwritten)
                if report.lost_fleet:
                    LOG.error(
                        "fleet",
                        f"{session_id}: whether it is on the fleet"
                        f" ({report.keys['in_fleet']}) did not reach its session file, so"
                        f" the next start may show it as it was: {result.reason}",
                    )
                else:
                    LOG.debug("fleet", f"{session_id}: session file not written: {result.reason}")
            case _:
                assert_never(report)


def _record_board(state: AppState, writer: boardsync.BoardWriter) -> Observer:
    """
    The observer one batch is applied under: each applied intent's board facts,
    queued for the log of the session they belong to.

    **A restore is settled here, in the order it was applied.** The store's
    report on a ``BoardRestored`` is the first moment anything knows whether the
    log may be appended to (``Restore.adoptable``), and the session's own first
    record can be the next intent in the same batch. So the log is adopted, or
    the session marked unrecorded, on the step that applied the restore.

    **Only** ``Fact.on_the_wall`` **is queued.** The record ``outcomes`` builds
    carries the store's monotonic stamp, which counts from boot and means
    nothing to the process that restores it. The reading it is converted with
    is taken once per batch, on the first fact, which is after the frame's own
    clock and so after every stamp in the batch.

    **It does not raise.** ``Store.apply_all`` abandons the whole batch when its
    observer does, and by then the intents are out of the queue: every agent
    parked on one of them would wait on an answer nothing will send. A fact
    that could not be derived is a hole in a log, so it ends the recording of
    every session being recorded -- which session it was is what could not be
    derived -- and the logs are told so.
    """
    reading: list[boardlog.Clock] = []

    def observe(before: Snapshot, intent: Intent, after: Snapshot, now: float) -> None:
        try:
            if isinstance(intent, BoardRestored):
                _settle_restore(state, writer, intent.session_id, after)
            for fact in boardfacts.outcomes(before, intent, after, now):
                entry = state.board_logs.get(fact.session_id)
                if entry is None:
                    continue
                if entry.awaiting:
                    # The queue's order rules this out for a session this process
                    # launched. Were it to happen the fact is in no log, and a
                    # log adopted afterwards would be missing it from its middle.
                    state.board_logs[fact.session_id] = dataclasses.replace(
                        entry,
                        awaiting=False,
                        why_not="its board changed before its log had been applied",
                    )
                    continue
                if not entry.recording:
                    continue
                if not reading:
                    reading.append(boardlog.Clock(wall=time.time(), monotonic=time.monotonic()))
                writer.submit(fact.session_id, fact.on_the_wall(reading[0]))
        except Exception as exc:  # noqa: BLE001 - see the docstring: this must not raise
            reason = f"a board fact could not be derived ({type(exc).__name__}: {exc})"
            LOG.error("board", f"{reason}; every board log is closed for this run")
            LOG.debug("board", traceback.format_exc())
            _stop_recording_everything(state, writer, reason)

    return observe


def _settle_restore(
    state: AppState, writer: boardsync.BoardWriter, session_id: str, after: Snapshot
) -> None:
    """
    Decide, from the store's report, whether a resumed session's log is this
    process's to append to.

    Adopted only when the read found the whole log in the session's own
    directory and the report says a record written behind the log would come
    from a reducer that holds everything the log says. Anything less and
    the session runs unrecorded for the life of the process, with the reason
    kept for the pane: appending would put a second history behind a board
    nobody restored.

    Only for a session whose restore this process is waiting on. A
    ``BoardRestored`` for any other is not this function's to act on.
    """
    entry = state.board_logs.get(session_id)
    if entry is None or not entry.awaiting:
        return
    # What the read already found is first: a log with records in a second
    # place, or only at the legacy path, is not appended to whatever the store
    # made of the half that was read (``boardsync.Found.unrecorded``).
    why = entry.why_not or board_pane.unrecorded_reason(after.restores.get(session_id))
    if why:
        LOG.warn("board", f"{session_id} is running unrecorded: {why}")
        state.board_logs[session_id] = dataclasses.replace(entry, awaiting=False, why_not=why)
        return
    writer.adopt(session_id)
    state.board_logs[session_id] = dataclasses.replace(entry, awaiting=False, recording=True)


def _stop_recording_everything(state: AppState, writer: boardsync.BoardWriter, reason: str) -> None:
    """
    End the recording of every session being recorded, and queue the record
    that makes each log say so (``boardlog.RecordingStopped``).

    ``marked`` starts False and is corrected when the marker's own outcome is
    read: until then nothing on disk says the log ends early.
    """
    for session_id, entry in list(state.board_logs.items()):
        if not entry.recording:
            continue
        state.board_logs[session_id] = dataclasses.replace(
            entry, recording=False, stopped=True, marked=False, why_not=reason
        )
        writer.submit(
            session_id,
            boardlog.RecordingStopped(
                at=time.time(), reason=reason[: boardsync.MARKER_REASON_CHARS]
            ),
        )


def _landed(result: boardlog.Written | boardlog.Unwritten | None) -> bool | None:
    """
    Whether a stop marker is on disk where a restore will read it, or None when
    none was attempted.
    """
    if result is None:
        return None
    return isinstance(result, boardlog.Written) and result.gap is None


def _read_board_reports(state: AppState, reports: tuple[boardsync.Report, ...]) -> None:
    """
    Fold what the writer settled since the last frame into ``board_logs``, on
    the draw thread.

    Kept as state and not shown as events: the writer's list is emptied by the
    read, so a line drawn from one frame's reports would be on screen for one
    frame. A session's entry therefore counts every record of its that a
    restore will not read back, and holds the reason its recording stopped for
    as long as the session is on screen.

    A stop is logged once, when it is read. The writer reports it once.
    """
    for report in reports:
        entry = state.board_logs.get(report.session_id)
        match report:
            case boardsync.Stopped(reason=reason, marker=marker):
                LOG.error(
                    "board",
                    f"{report.session_id} is no longer being recorded: {reason}",
                )
                if entry is not None:
                    state.board_logs[report.session_id] = dataclasses.replace(
                        entry, recording=False, stopped=True, marked=_landed(marker), why_not=reason
                    )
            case boardsync.Outcome(record=record, result=result):
                if entry is None:
                    continue
                if isinstance(record, boardlog.RecordingStopped):
                    # The shell's own marker (``_stop_recording_everything``).
                    state.board_logs[report.session_id] = dataclasses.replace(
                        entry, marked=_landed(result)
                    )
                elif not report.kept:
                    state.board_logs[report.session_id] = dataclasses.replace(
                        entry, unkept=entry.unkept + 1
                    )
            case _:
                assert_never(report)


def board_logging(state: AppState) -> board_pane.Logging:
    """
    What the board pane is told about the logs, for this frame.
    """
    if state.board_log_unread is not None:
        # Before the two below: ``board_log`` is off here as well, and "switched
        # off" would say the operator chose it.
        return board_pane.Logging(
            unread=f"{state.board_log_unread.value} ({settings_mod.settings_path()})"
        )
    if state.board_writer is None:
        return board_pane.Logging(
            off=_LOG_SWITCHED_OFF if not state.settings.board_log else _LOG_FAKE
        )
    return board_pane.Logging(sessions=state.board_logs)


def begin_frame(state: AppState) -> None:
    """
    Runs before any panel draws.

    Order is the invariant: intents are applied *before* the snapshot is taken (I4),
    and the snapshot is taken exactly once (I2). Calling store.snapshot() again in a
    panel is a bug even when it looks harmless, because the two results can differ
    and the frame then renders torn state.
    """
    state.frame += 1
    state.frame_now = time.monotonic()
    state.frame_wall = time.time()

    _apply_theme_if_dirty(state)
    # Between frames: the only place the atlas is added to, and the only moment
    # ImGui's font size is the UI font's and not one a pane pushed.
    typeset.pre_frame()
    _keep_baked_sizes()
    if state.pending_layout is not None:
        hello_imgui.switch_layout(state.pending_layout)
        state.pending_layout = None
    if state.pending_pane is not None:
        pane, state.pending_pane = state.pending_pane, None
        if state.runner is not None:
            # After the switch above, so the name is looked up in the layout that
            # is now current. A name that layout does not have is left alone.
            state.runner.docking_params.focus_dockable_window(pane)
    apply_intents(state)
    state.frame_snap = state.store.snapshot()
    state.cards.prune(state.frame_snap)
    state.transcripts.prune(state.frame)
    # Before anything draws, so no pane can render a cursor pointing at an
    # obligation that was answered between frames.
    state.focus.settle(state.frame_snap)
    _check_for_overdue_commands(state)
    _check_for_unstarted_runs(state)
    _read_survivor_checks(state)
    # Shortcuts are handled once per frame, before any panel draws, so they behave
    # the same whichever pane happens to have focus.
    _handle_keys(state)


def _handle_keys(state: AppState) -> None:
    """
    The frame's bare keys: the approval shortcuts, then the card menu's key and
    the layout keys.

    One function because the later ones need what the first decided. ``review.Hold``
    is stepped once a frame, inside ``review.handle_keys``, and whether that step
    let the keys through is the answer for every bare key in the application --
    the Enter, Tab or Escape of a sentence whose text box was removed is no more a
    layout key than its ``a`` is an approval, and its ``m`` opens no menu.

    **An Escape the find row took is not theirs**, on the frame it closed the row
    or on a repeat of that press while it is held (``FindBar.spent_escape``): it
    was aimed at the row, and read here it would also cancel an argument edit,
    dropping what was typed, or leave FOCUS. One answer, asked once, for every
    reader below.

    **An open card menu takes all of them.** Keyboard navigation is off, so an
    open popup does not claim the keyboard and reads no key by itself: the menu
    reads its own arrows, Enter and Escape as it draws. Left to this function as
    well, the Enter that chooses an item would switch the layout and an ``a``
    pressed over an open menu would approve a call. The hold is not stepped on
    those frames either. A menu takes no text, so there is no typing for it to
    miss, and a hold that was standing when the menu opened is still standing
    when it closes. Details and the close confirmation are modal popups, which do
    claim the keyboard, and the readers below defer to that as they do for the
    launcher.
    """
    # Before anything returns: asking is what lets a press the find row took go
    # once it is released (``FindBar.spent_escape``).
    spent = state.transcripts.find.spent_escape(
        imgui.get_frame_count(), imgui.is_key_down(imgui.Key.escape)
    )
    busy = state.cards.claim_keys()
    snap = state.frame_snap
    if snap is None or busy:
        return
    review.handle_keys(snap, state.focus, state.review, state.bridge, escape_spent=spent)
    if not state.review.hold.free:
        return
    _handle_card_key(state)
    _handle_layout_keys(state, escape_spent=spent)


def _keep_baked_sizes() -> None:
    """
    Keep every font size ImGui has rasterised from being discarded and moved
    (``theme.keep_baked_sizes`` says why), after the seam's turn: a change of
    family or of size is known by then, and is what lets go of the old sizes.

    Whatever it raises is logged once and the frame goes on. This runs ahead of
    the intents, and a frame that applies none is worse than one whose glyphs
    may come out at the wrong size.
    """
    try:
        theme.keep_baked_sizes(typeset.gate.epoch())
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        if _PANEL_ERRORS.get("baked sizes") != detail:
            _PANEL_ERRORS["baked sizes"] = detail
            LOG.error("ui", f"baked sizes: {detail}")
            LOG.debug("ui", traceback.format_exc())


# Opens the focused card's menu. A bare letter, like every other key that acts on
# what the cursor is on, and one that ``review.SHORTCUTS`` and the layout keys do
# not use.
CARD_MENU_KEY = imgui.Key.m


def _handle_card_key(state: AppState) -> None:
    """
    ``m`` opens the menu of the card the cursor is on. Reached only on a frame
    whose keys ``review.Hold`` let through: see ``_handle_keys``.

    Only asked for here. The fleet pane opens it, on the card it draws as
    focused, and an ask no card takes is gone at the next frame
    (``MenuState.claim_keys``). Not with a modifier down: Ctrl+M is not this key.
    """
    io = imgui.get_io()
    if _modal_up(state) or io.want_capture_keyboard:
        return
    if io.key_ctrl or io.key_alt or io.key_super:
        return
    if imgui.is_key_pressed(CARD_MENU_KEY, False):
        state.cards.open_from_key = True


def _modal_up(state: AppState) -> bool:
    """
    Whether one of the application's own forms is up: New Task, the resume
    form or the settings dialog. Each is a modal whose focus can sit on a combo,
    where the keyboard is not captured and a bare key would otherwise reach the
    frame as well (see ``_handle_layout_keys``). Last frame's value, for the
    reason given there.
    """
    return state.launcher.is_open or state.resume.is_open or state.settings_dialog.is_open


def _handle_layout_keys(state: AppState, *, escape_spent: bool = False) -> None:
    """
    Tab triages, Enter focuses, Esc returns. Reached only on a frame whose keys
    ``review.Hold`` let through: see ``_handle_keys``.

    Esc is shared with "cancel the edit I am in the middle of", and the edit wins:
    yanking the layout out from under someone mid-diff is the failure mode this
    whole two-mode design has to avoid, and an operator pressing Esc over an open
    argument editor means the editor.

    A modal wins over both: any of ``_modal_up``'s.
    ``want_capture_keyboard`` covers the usual case -- the launcher's task box has
    the keyboard -- but not a modal whose focus sits on a combo, where Esc would
    otherwise dismiss the modal *and* change layout in the same frame. ``is_open``
    here is last frame's value, which is the one that matches the frame the
    operator was looking at when they pressed the key.

    ``escape_spent`` is an Escape the find row took (``_handle_keys``): not one
    that returns.

    A chord is not one of these keys. Ctrl+Tab is ImGui's own window switcher and
    Ctrl+Enter sends a prompt; neither captures the keyboard while the mouse is over
    a typeset view, which submits no item, so with only the capture guard both would
    also change the layout. The same rule as ``review.handle_keys``.
    """
    snap = state.frame_snap
    io = imgui.get_io()
    if snap is None or _modal_up(state) or io.want_capture_keyboard:
        return
    if io.key_ctrl or io.key_alt or io.key_super:
        return
    current = hello_imgui.current_layout_name()
    if imgui.is_key_pressed(imgui.Key(LAYOUT_KEYS[TRIAGE])) and current != TRIAGE:
        hello_imgui.switch_layout(TRIAGE)
    elif imgui.is_key_pressed(imgui.Key(LAYOUT_KEYS[FOCUS])) and current != FOCUS:
        hello_imgui.switch_layout(FOCUS)
    elif (
        imgui.is_key_pressed(imgui.Key.escape)
        and current == FOCUS
        and state.review.editing is None
        and not escape_spent
    ):
        hello_imgui.switch_layout(TRIAGE)


# Last error each panel reported, so a panel failing every frame logs once rather
# than 60 times a second and flushes the ring buffer of everything useful.
_PANEL_ERRORS: dict[str, str] = {}


def guarded(name: str, draw: Callable[[], None]) -> Callable[[], None]:
    """
    Wrap a panel so a bug in it cannot take the application down with it.

    This matters more here than in most UIs. The window owns every live agent
    session and every parked approval, so an unhandled exception in one panel's
    draw code destroys work that is not recoverable -- an operator loses running
    agents to a rendering bug in a pane they were not even looking at.

    Safe only because ImGui error recovery is enabled in post_init. Catching
    mid-draw leaves the ImGui stack unbalanced (a begin_child with no end_child),
    and with recovery asserts on that aborts the process -- trading one crash for
    another. With recovery on, ImGui unwinds the stack itself and logs what was
    left open. Verified before relying on it.

    What was raised is drawn through ``drawable``: an exception can quote the very
    text a text call refused, and this is the one place a second raise has nothing
    above it to catch it.

    A pane that shows parked calls says more than that it failed (``_reviewing``):
    what it had on screen is not known, so nothing is approved until it draws.
    """

    def wrapper() -> None:
        try:
            draw()
        except Exception as exc:
            detail = drawable.text(f"{type(exc).__name__}: {exc}")
            if _PANEL_ERRORS.get(name) != detail:
                _PANEL_ERRORS[name] = detail
                LOG.error("ui", f"{name}: {detail}")
                LOG.debug("ui", traceback.format_exc())
            imgui.text_colored(P.danger.vec4, f"{name} failed to draw")
            imgui.text_wrapped(detail)
            imgui.text_disabled("the rest of the app, and every running agent, is unaffected")
            if name in _REVIEW_PANES:
                imgui.push_text_wrap_pos(0.0)
                imgui.text_colored(P.warn.vec4, _APPROVE_HELD)
                imgui.pop_text_wrap_pos()

    return wrapper


# The panes that show a parked call, by the label each is docked under, and the
# name each reports to the review state by. A draw of one of these that raises
# holds every approve (``review.withheld``).
_REVIEW_PANES = {"NEEDS YOU": review.INBOX, "DETAIL": review.DETAIL}

_APPROVE_HELD = (
    "nothing is approved while this pane cannot draw: a, A and the approve buttons "
    "send nothing. r rejects the call under the cursor at once when it is the queue "
    "that cannot draw, and closing a session ends its parked calls."
)


def _reviewing(state: AppState, label: str, draw: Callable[[], None]) -> Callable[[], None]:
    """
    A pane that shows parked calls, telling the review state how each draw of it
    came out, and raising what the pane raised for ``guarded`` to say.

    Here, around the pane's own function, and not in ``guarded``: that one wraps
    every callback the runner has and holds no state, and this is the wiring of
    two particular panes to the state that decides an approve. A draw that returns
    is one that reached its end, which is all "whole" means here -- how much of a
    body it set is the pane's own report (``ReviewState.body_shown``).
    """
    pane = _REVIEW_PANES[label]

    def wrapper() -> None:
        frame = int(imgui.get_frame_count())
        try:
            draw()
        except Exception:
            state.review.pane_drew(pane, frame, False)
            raise
        state.review.pane_drew(pane, frame, True)

    return wrapper


# How long the counts may disagree before it is treated as a lost approval rather
# than the ordinary lag between a gate parking and the UI applying its intent.
_LOST_APPROVAL_GRACE_S = 3.0

# A bus request is answered by the frame that applies its intent, so it should be
# outstanding for a fraction of one frame. This is generous by three orders of
# magnitude on purpose: it is a wedge detector, not a latency budget.
_STRANDED_REQUEST_GRACE_S = 5.0

# How long the frame loop may go without draining, with agents parked behind it,
# before the host is reported wedged.
#
# Long enough that nothing a live host does reaches it: idling runs at fps_idle and
# still drains every frame, and even a pathological single-frame stall is orders of
# magnitude under this. Short enough to be worth having -- the failure it names cost
# ~18h in session 7f0b40c2, and the whole complaint about APPROVAL_TIMEOUT_S as a
# host-death backstop is that six hours is not a detection latency, it is an outage.
_DRAIN_STALL_S = 30.0

# How often the loop-side watchdog wakes. Each tick is three integer comparisons
# and a subtraction against thresholds measured in seconds, so this is a resolution
# choice and not a cost one.
_WATCHDOG_POLL_S = 1.0


async def watch(state: AppState) -> None:
    """
    Run every watchdog, on the asyncio loop, forever.

    **This lives here rather than in ``begin_frame`` because the frame loop is one
    of the things being watched.** All three of these checks were previously called
    from the frame loop, which makes them unable to report the failure that matters
    most: a UI thread that has died or wedged stops draining, stops applying
    intents, and stops running its own watchdogs in the same instant. The evidence
    is a park nobody answers, and the thread that would have said so is the thread
    that is gone.

    The asyncio loop is the right host because it is where the parked agents
    themselves are: if it is dead too, there is nothing left to warn about.

    Reads the store and never writes it. ``Store`` is documented as not thread-safe
    and that is about writes -- ``snapshot()`` is a single attribute read of a
    frozen value, which is exactly what a watchdog on another thread can afford to
    take. Every mutation still happens on the frame loop.

    One clock reading per tick, taken here rather than inside each check, so three
    warnings raised on the same tick describe the same instant. Passing it in is
    also what makes the thresholds testable at all: they are measured in tens of
    seconds and no test can afford to wait one out.

    A tick that raises is logged and the loop continues. An exception escaping this
    coroutine kills the task and takes all three watchdogs with it, silently, which
    is the one failure mode a watchdog cannot have -- the same reason ``guarded``
    exists for the draw path.
    """
    while True:
        await asyncio.sleep(_WATCHDOG_POLL_S)
        try:
            now = time.monotonic()
            _check_for_lost_approvals(state, now)
            _check_for_stranded_requests(state, now)
            _check_for_drain_stall(state, now)
        except Exception as exc:
            LOG.error("watchdog", f"{type(exc).__name__}: {exc}")


def _stalled_transcripts(snap: Snapshot) -> tuple[Transcript, ...]:
    """
    Where to write a host-stall warning so the operator meets it on the way back in.

    The nodes with approvals on screen first, because a stall is a fact about the
    call they are holding. When the store shows none, the drain that would have put
    them there is the thing that stopped -- so the session roots take it instead,
    and the warning is not lost for being unable to name its node.

    Not a root recalled from its session file. Nothing runs behind a closed
    card, so no approval of its can be parked, and the warning would be the only
    text in a transcript that was never loaded.
    """
    parked = tuple(dict.fromkeys(p.node for p in snap.approvals))
    nodes = parked or tuple(
        n for n in snap.order if n[1] is None and n in snap.nodes and snap.nodes[n].recalled is None
    )
    return tuple(snap.nodes[n].transcript for n in nodes if n in snap.nodes)


def _check_for_drain_stall(state: AppState, now: float | None = None) -> None:
    """
    Agents are parked at the gate and nothing is taking the queue: the host is wedged.

    **Warn only. This must never deny.** Its false-positive mode is an OS suspend or
    a closed lid, which stalls the drain and the clock together and is precisely the
    overnight park the gate exists to protect -- a deny arm here would auto-refuse
    the approvals the mechanism was built to keep alive. Whether host-fully-dead
    self-detects, and so whether a deny arm is ever needed, is probe P2's question
    and is not answered here.

    The two conditions are both necessary. A stalled drain with nothing parked is an
    idle application and says nothing; parked approvals with a live drain is the
    ordinary state of an operator who has not looked yet, and is I8 working.

    Latched, so a wedged host logs once rather than once per poll for as long as it
    stays wedged; cleared as soon as a drain lands, so a recovered host can report
    again if it wedges twice.
    """
    at = time.monotonic() if now is None else now
    parked = state.bridge.parked_count
    stalled_for = state.bridge.drain_stalled_for(at)
    if parked == 0 or stalled_for < _DRAIN_STALL_S:
        state.stall_reported = False
        return
    if state.stall_reported:
        return
    state.stall_reported = True
    LOG.error(
        "gate",
        f"the frame loop has not drained for {stalled_for:.0f}s with {parked} approval(s) "
        "parked - this host looks wedged, and no approval can be answered until it "
        "recovers. Nothing has been denied",
    )
    for transcript in _stalled_transcripts(state.store.snapshot()):
        transcript.append(
            SegmentKind.ERROR,
            f"\n--- the pptmstr host stopped draining {stalled_for:.0f}s ago with "
            f"{parked} approval(s) parked; no decision has been made ---\n",
        )


def _check_for_stranded_requests(state: AppState, now: float | None = None) -> None:
    """
    A bus request that outlives several frames was dropped.

    Unlike a parked approval, this has no surface at all -- an agent awaiting
    ``claim_task`` is not an obligation and appears nowhere in ``needs_you``, by
    design. So there is nothing on screen that would look wrong.

    Worth a watchdog because the store commits the domain change at *apply* time
    while the answer travels separately: a lost effect leaves the board reading
    "claimed" while the agent that claimed it was eventually told, at shutdown,
    that there was nothing to claim. The two disagree and neither side complains.

    Found by the `research` template reviewing this codebase, which is the first
    thing the team feature has been used for.

    ``now`` is the clock to measure against, defaulting to the frame's. A caller
    that already has one reading passes it, so every check in a sweep agrees about
    the instant it is describing.
    """
    at = state.frame_now if now is None else now
    outstanding = state.bridge.asking_count
    if outstanding == 0:
        state.stranded_since = None
        state.stranded_reported = False
        return
    if state.stranded_since is None:
        state.stranded_since = at
        return
    waited = at - state.stranded_since
    if waited >= _STRANDED_REQUEST_GRACE_S and not state.stranded_reported:
        state.stranded_reported = True
        LOG.error(
            "bus",
            f"{outstanding} bus request(s) unanswered for {waited:.0f}s - "
            "an agent is blocked on a reply the frame loop never settled",
        )


def _check_for_lost_approvals(state: AppState, now: float | None = None) -> None:
    """
    Every parked agent must be visible as something the operator can answer.

    The Bridge knows how many agents are blocked on a future; the store knows how
    many approvals are on screen. They should agree. When the Bridge is holding
    more than the queue shows, an agent is blocked behind an approval nobody can
    reach -- which is a permanent hang that looks like the agent simply stopped.

    Worth checking rather than trusting, because every way this can happen is a
    silent one: an intent for a node the store never learned about, a dropped
    emit, an ordering assumption that does not hold. The failure has no natural
    symptom, so it needs an unnatural one.

    Debounced: parking registers the future before the intent reaches the store,
    so the counts legitimately disagree for a frame or two.

    Reads the store directly rather than the frame's snapshot. Off the frame loop
    there is no frame, and a host whose UI thread has stopped applying intents is
    the case this is worth the most in -- the parked approvals pile up on the Bridge
    while the store's view of them stays exactly where the last drain left it.
    """
    at = state.frame_now if now is None else now
    snap = state.store.snapshot()
    parked = state.bridge.parked_count
    # Approvals only, not the whole obligation list: this compares parked futures to
    # what the operator can reach, and a question or a crash has no future behind it.
    visible = len(snap.approvals)

    if parked <= visible:
        state.lost_since = None
        state.lost_reported = False
        return

    if state.lost_since is None:
        state.lost_since = at
        return
    if at - state.lost_since < _LOST_APPROVAL_GRACE_S:
        return
    if not state.lost_reported:
        state.lost_reported = True
        LOG.error(
            "gate",
            f"{parked - visible} agent(s) blocked on an approval that is not in the "
            "queue - they cannot be answered and will hang until the hook times out",
        )


def _apply_theme_if_dirty(state: AppState) -> None:
    """
    Push the palette into ImGuiStyle, at most once per theme change.

    This cannot live in ``post_init``, which is the obvious home for it.
    hello_imgui applies its own built-in theme (``tweaked_theme``, defaulting to
    darcula_darker) as part of runner setup, and there is no "none" among the
    options -- so a style set in post_init is overwritten before the first frame
    and the app silently wears hello_imgui's theme instead of ours. Doing it at the
    top of a frame lands after that.

    Still a per-change operation, not a per-frame one: ImGuiStyle persists across
    frames, and rewriting fifty colours every frame to achieve nothing would be a
    real cost on a path that runs at 60fps.
    """
    if not state.theme_dirty:
        return
    theme.apply_style()
    if state.runner is not None:
        state.runner.imgui_window_params.background_color = P.bg.vec4
    state.theme_dirty = False


def _containment_refusal(check: cli_version.FloorCheck) -> str | None:
    """
    Why a contained launch cannot proceed against this version reading, or None.

    Pure, so the three outcomes can be exercised without spawning anything.

    ``Unreadable`` refuses. ``planning/2026-09-03-a-dangerously-autonomous-mode.md``
    §8c measured that the CLI accepts an unrecognised settings key silently, so a CLI
    that cannot be interrogated is a CLI that may ignore every sandbox key in
    ``spec.containment`` and start a session the operator believes is contained.
    ``cli_version`` keeps "could not tell" separate from "checked and fine" precisely
    so this decision can be made here rather than collapsed there.
    """
    match check:
        case cli_version.MeetsFloor():
            return None
        case cli_version.BelowFloor(version=version, floor=floor):
            return (
                f"the CLI reports {version}, below the {floor} floor for the sandbox keys "
                "this mode sets - an older CLI accepts them silently and runs uncontained"
            )
        case cli_version.Unreadable(detail=detail, floor=floor):
            return (
                f"the CLI version could not be read ({detail}), so the {floor} floor for "
                "the sandbox keys this mode sets cannot be confirmed"
            )
        case _:
            assert_never(check)


# After the directory, in the log and in the launcher's notice. It names both things
# ``launcher.refusal_for`` answers yes to, as that function's own message does.
_MOVED_INSIDE = (
    "is inside pptmstr's own checkout, or cannot be resolved at all, so a session that "
    "writes there without asking was not started"
)


def _live_sessions(snap: Snapshot | None) -> frozenset[str]:
    """
    The ids of the sessions on screen that have not ended: every root whose state is
    not terminal.

    What New Task's session list marks and the resume form warns about, because a
    session resumed under one of these ids ends the run that holds it. Read from the
    snapshot and not from the pool: a root that has not ended is what the operator
    sees as running, and the pool's own table also holds sessions whose run is over.

    A walk over the node table, so it is for a frame the launcher is drawn on and
    not for every frame.
    """
    if snap is None:
        return frozenset()
    return frozenset(
        node[0] for node, rec in snap.nodes.items() if node[1] is None and not rec.state.is_terminal
    )


def _resume_ends(state: AppState, showing: str | None) -> frozenset[str]:
    """
    The ids a surface that resumes ``showing`` warns about: the sessions a
    resume would end a run of. Every root that has not ended
    (``_live_sessions``), and ``showing`` itself when the pool still has a run
    under its id. ``showing`` is the session on the resume form, or the one
    picked in New Task's session list; None is neither.

    The second is a session that failed with its process still attached -- an
    error result leaves the client connected -- or one closed a moment ago and
    still being ended. Its root is terminal, so the snapshot does not call it
    live, and pressing resume ends that process all the same
    (``SessionPool.submit``). Asked of the pool for the one session about to
    be resumed and not for every card.
    """
    live = _live_sessions(state.frame_snap)
    pool = state.pool
    if showing is None or showing in live or pool is None:
        return live
    return live | {showing} if pool.has_run((showing, None)) else live


@dataclass(frozen=True, slots=True)
class _Unreadable:
    """A session's log could not be read at all, and why."""

    reason: str


@dataclass(frozen=True, slots=True)
class _Logged:
    """
    What reading a resumed session's log gave, and the one reading of both
    clocks taken when it was read (``intents.BoardRestored.clock``).

    ``unrecorded`` is ``boardsync.Found.unrecorded``: why nothing may be
    appended for the session whatever the store makes of ``board``, or None
    when its own directory holds everything that was found for it.
    """

    board: boardlog.Board | boardsync.NoLog | _Unreadable
    clock: boardlog.Clock
    unrecorded: str | None = None


def _read_log(writer: boardsync.BoardWriter, session_id: str) -> _Logged:
    """
    Read one session's log, from the two places ``writer`` looks. Runs on a
    worker thread: it lists directories and reads every file in one.

    ``boardsync.find`` and not ``load``: where the board was read, and whether
    it may be appended behind, are the half of the answer this launch acts on.

    Whatever the read raises is a value. A launch is worth more than its log, so
    a log that cannot be read is a session that runs unrecorded and says so, not
    a launch that is refused.
    """
    board: boardlog.Board | boardsync.NoLog | _Unreadable
    unrecorded: str | None = None
    try:
        found = boardsync.find(session_id, writer.root, writer.legacy)
        board, unrecorded = found.board, found.unrecorded
    except Exception as exc:  # noqa: BLE001 - reported as the reason it is
        board = _Unreadable(f"{type(exc).__name__}: {exc}")
    return _Logged(board, boardlog.Clock(wall=time.time(), monotonic=time.monotonic()), unrecorded)


def _left_behind(state: AppState, session_id: str) -> str | None:
    """
    Why the move at startup left this session's log at the old path, or None
    when it left none there: ``boardsync.Left.reason`` for the entry of that
    name.
    """
    migration = state.migration
    if migration is None:
        return None
    return next((left.reason for left in migration.left if left.name == session_id), None)


def _why_unrecorded(state: AppState, session_id: str, unrecorded: str) -> str:
    """
    ``boardsync.Found.unrecorded`` as the pane is given it: the reason, and
    after it what the move at startup said of the session's old directory when
    that is why there are two places.
    """
    left = _left_behind(state, session_id)
    if left is None:
        return unrecorded
    return f"{unrecorded}. Log left at the old path when pptmstr started: {left}"


def _newest(board: boardlog.Board) -> float | None:
    """
    The latest wall-clock time a restored board carries, or None for one that
    carries none. What the pane calls "written up to": the board shows no later
    fact, which is not a claim that the log holds no later record.
    """
    times: list[float] = []
    for task in board.tasks:
        times += [version.at for version in task.versions]
        times += [at for at in (task.completed_at, task.released_at) if at is not None]
    for concern in board.concerns:
        times.append(concern.posted_at)
        if concern.delivered_at is not None:
            times.append(concern.delivered_at)
    return max(times, default=None)


def _addresses_of(board: boardlog.Board, session_id: str) -> dict[str, str | None]:
    """
    Every address a logged board has for a sub-agent of ``session_id``, with
    the agent id it belonged to: what ``AgentSession`` is constructed with so a
    new agent is never given an earlier agent's name.

    A party an older log knows by address alone is in it with no id. Nothing can
    be handed that address back, and nothing else may take it either.

    Built by ``board.reserved``, as the board's own copy of this table is from
    the ``Restore.parties`` the store makes of the same log (``board._addresses``):
    the driver allocates from this one and the board names agents from that one,
    so they have to be one table.
    """
    named: list[tuple[str, str | None]] = [
        (address, node[1])
        for node, address in board.addresses
        if node[0] == session_id and node[1] is not None
    ]

    def bare(party: boardlog.Party | None) -> None:
        if isinstance(party, str):
            named.append((party, None))

    for task in board.tasks:
        for party in (task.declared_by, task.holder, *task.held_by, *task.released_by):
            bare(party)
        for version in task.versions:
            bare(version.by)
    for concern in board.concerns:
        bare(concern.sender)
        bare(concern.recipient)
    return board_mod.reserved(named)


def _carried_addresses(state: AppState, session_id: str | None) -> dict[str, str | None]:
    """
    The address table a session is constructed with: nothing for a new id, and
    for a resumed one every address its earlier agents answered to -- what the
    log read in this process names (``_addresses_of``) and what the last run of
    it in this process had in its own table, which is the log's plus whatever
    that run allocated.

    One table and not the first that is found, because the two are different
    facts about the same names and ``board.reserved`` is what says how they
    combine.
    """
    if session_id is None:
        return {}
    earlier = state.ran.get(session_id)
    tables = (
        state.restored_addresses.get(session_id, {}),
        {} if earlier is None else earlier.addresses(),
    )
    return board_mod.reserved(pair for table in tables for pair in table.items())


def _open_board_log(state: AppState, session_id: str, logged: _Logged | None) -> None:
    """
    Decide how a session the pool has just taken is recorded, and hand a logged
    board to the store. On the asyncio thread, **with no await between
    ``pool.submit`` returning and this**.

    That is the ordering guarantee. ``Bridge.to_ui`` is one queue with one
    consumer, and a session emits a bus intent only from a handler the SDK runs
    as a task, which cannot run until this coroutine next yields. So a
    ``BoardRestored`` emitted here is behind the root's announce -- which
    ``submit`` emits itself and which says nothing of a board -- and ahead of
    everything else the session will ever say.

    **After the pool's answer and not before it**, so a launch the pool refused
    restores nothing: a board in the store with no row to show it would hold
    its ids against every other session. A session that is taken and then fails
    to start -- refused for its directory by ``AgentSession.run``, a CLI that
    does not connect -- has been announced, so its row stays, failed, and the
    board is drawn on it.

    ``logged`` is None for a session that did not resume: its id was minted for
    this launch, so no log can exist under it, and it is adopted outright.

    **A log the read found in two places, or only at the legacy path, is never
    adopted** (``_Logged.unrecorded``). What was read is still restored, so the
    board is on screen; the session runs unrecorded with the reason, which names
    both directories, as one whose restore came back short does.

    **A session this process already has an entry for is left exactly as it is.**
    That is a relaunch under an id this window has already run: the store holds
    that board and is ahead of the disk by whatever the writer has queued, so
    the log on disk is a prefix of it and this process is its only writer.
    Reading it back would show the store a board that differs from its own, and
    the store's refusal of that is indistinguishable from the same id open in a
    second window. The cost is that a second window writing to that log is not
    noticed here; the next restore reads it as contested.
    """
    writer = state.board_writer
    if writer is None or session_id in state.board_logs:
        return
    directory = writer.directory(session_id)
    if directory is None:
        state.board_logs[session_id] = board_pane.LogState(
            directory="",
            recording=False,
            why_not="its session id is not one directory name, so it has no log",
        )
        return
    entry = board_pane.LogState(directory=str(directory))
    board = None if logged is None else logged.board
    unrecorded = (
        None
        if logged is None or logged.unrecorded is None
        else _why_unrecorded(state, session_id, logged.unrecorded)
    )
    match board:
        case None | boardsync.NoLog():
            if unrecorded is not None:
                LOG.warn("board", f"{session_id} is running unrecorded: {unrecorded}")
                state.board_logs[session_id] = dataclasses.replace(
                    entry, recording=False, why_not=unrecorded
                )
                return
            writer.adopt(session_id)
            state.board_logs[session_id] = entry
        case _Unreadable(reason=reason):
            LOG.warn("board", f"{session_id} is running unrecorded: {reason}")
            state.board_logs[session_id] = dataclasses.replace(
                entry, recording=False, why_not=f"its log could not be read ({reason})"
            )
        case boardlog.Board():
            assert logged is not None
            state.board_logs[session_id] = dataclasses.replace(
                entry,
                recording=False,
                awaiting=True,
                # Read by ``_settle_restore`` when the store has applied the
                # board: a reason here is what keeps the log from being adopted.
                why_not=unrecorded or "",
                newest=_newest(board),
                stops=tuple(stop.reason for stop in board.stopped),
            )
            state.bridge.emit(BoardRestored(session_id, board, logged.clock))
        case _:
            assert_never(board)


def _board_log_unopened(state: AppState, session_id: str, exc: Exception) -> None:
    """
    Say that a session the pool has taken runs with no board log, because
    opening one raised.

    The entry is what stops the rest: no fact of the session is handed to the
    writer, a board that was read and not handed to the store is not waited
    for, and a relaunch under the id does not read the log back against a store
    that never held it. The pane says why from the same entry.
    """
    reason = f"{type(exc).__name__}: {exc}"
    LOG.error(
        "board",
        f"{session_id} started and is running unrecorded: its board log could not be "
        f"opened ({reason})",
    )
    LOG.debug("board", traceback.format_exc())
    entry = state.board_logs.get(session_id) or board_pane.LogState(directory="")
    state.board_logs[session_id] = dataclasses.replace(
        entry,
        recording=False,
        awaiting=False,
        why_not=f"its log could not be opened ({reason})",
    )


def _launch(
    state: AppState,
    spec: LaunchSpec,
    draft: launcher.Draft | None = None,
    *,
    origin: str = "launch",
    resumed: tuple[str, resume_form.Draft] | None = None,
) -> None:
    """
    Start a session. Safe from the UI thread; the pool is touched on the loop.

    The future is kept, and ``_reap_launches`` tells the launcher if no session
    started. ``draft`` is what the launcher modal was showing when it was pressed, so
    that it can be handed back; a relaunch, a fork and a task from the command line
    have none and name themselves in ``origin`` instead, which is the word the
    launcher's notice uses for a failure that is not about the text under it.
    ``resumed`` is the resume form's equivalent of ``draft``: the session and
    the form's draft, so a resume that starts nothing goes back to that form.
    A resume pressed in New Task carries New Task's ``draft`` like any launch
    from there, and goes back to New Task.

    **Every resume comes through here whatever surface pressed it**, and is
    treated alike: the session is announced under ``spec.task`` and sent
    ``spec.message`` when the spec has one, no brief is written for it
    (``_seed_brief``), and it passes each check a launch passes. A spec whose
    title is only a label is given the task the session's file recorded, where
    one did (``_recorded_title``).

    Takes the whole spec rather than loose arguments. ``relaunch`` and ``fork`` build
    one from an ``AgentRecord``, and row 9's defect was that they could build a
    partial one without the type system noticing -- a template dropped from a
    positional list started a team session solo. A field missing from a ``LaunchSpec``
    is a field the constructor names.

    A None ``template`` still means solo, defaulted here rather than at each call
    site because a record that is not a session root carries None and a caller that
    had to remember the fallback is a caller that can forget it.
    """
    pool = state.pool
    if pool is None:
        # Decided already, and reported the way every other outcome is so that this
        # function touches the launcher from no thread but the one that draws it.
        LOG.error("app", f"a {origin} in {spec.cwd} had no session pool to start in")
        decided: concurrent.futures.Future[LaunchOutcome] = concurrent.futures.Future()
        decided.set_result(LaunchRefused("there is no session pool to start it in"))
        state.launches.append(_InFlight(decided, draft, origin, resumed))
        return
    if not spec.model:
        # A record recalled from a file that names no model. The launch is worth
        # more than the field, and the log says which model it was given.
        spec = dataclasses.replace(spec, model=model_catalog.DEFAULT_MODEL)
        LOG.info("app", f"that {origin} named no model; started with {spec.model}")
    # An unknown name falls back to solo rather than refusing the launch: the task
    # the operator typed is worth more than the team shape they mistyped, and the
    # log line says which one ran.
    shape = (templates.by_name(spec.template) if spec.template else None) or templates.SOLO

    async def go() -> LaunchOutcome:
        """
        Every way this ends without a session is a ``LaunchRefused``, an unexpected
        exception included: the caller reads a value, and an exception left on a
        future nobody is obliged to inspect is a launch that vanished.
        """
        try:
            return await start()
        except Exception as exc:  # noqa: BLE001 - reported as the refusal it is
            reason = f"{type(exc).__name__}: {exc}"
            LOG.error("app", f"a {origin} in {spec.cwd} failed before it started: {reason}")
            # Beside the one line and not in it, as ``guarded`` does for a panel:
            # the reason is what the launcher shows the operator, and a bug on this
            # path is only locatable from the frames.
            LOG.debug("app", traceback.format_exc())
            return LaunchRefused(reason)

    async def start() -> LaunchOutcome:
        if spec.containment is not None:
            # Read on a worker, awaited here. ``check_installed_cli`` spawns
            # ``claude --version`` and waits up to ten seconds for it, so the two
            # threads it must not run on are the frame loop -- I7, and the reason
            # ``ui/launcher`` scans sessions on a worker -- and this asyncio loop,
            # which hosts every live session, the gate's parked futures and the
            # watchdogs that report on them. ``driver`` places ``tag_session`` the
            # same way for the same reason.
            #
            # Only for a contained launch: an ordinary one neither pays for the
            # subprocess nor can be refused by it.
            #
            # No path is passed because nothing on this path sets
            # ``ClaudeAgentOptions.cli_path``, so the SDK resolves the binary by the
            # rule ``cli_version.resolve_cli_path`` reproduces. Setting that option
            # anywhere in this launch means passing the same path here, or the
            # version read describes a binary the session will not spawn.
            refusal = _containment_refusal(await asyncio.to_thread(cli_version.check_installed_cli))
            if refusal is not None:
                LOG.error("app", f"refused a contained launch in {spec.cwd}: {refusal}")
                return LaunchRefused(refusal)
            # Asked again here, of the string the session is about to be started
            # with, and after the await above rather than before it. The launcher
            # asked when the button was pressed, and the version read can take
            # seconds. A path that leads into pptmstr's own checkout by now starts
            # nothing: the launcher
            # downgrades such a draft to the ordinary gate because the operator is
            # looking at it, and nobody is looking at this.
            #
            # Not the last word, and not the guarantee. The pool may queue this
            # session at its cap or hold it behind a run that is still ending, for
            # as long as other sessions live, so ``AgentSession.run`` asks again
            # immediately before it spawns and that is the check a start cannot get
            # past (``driver.OwnCheckoutRefused``, which also says what remains
            # across the process boundary). This one is kept for what it alone can
            # do: refuse before a session exists, so the operator gets the draft
            # back in the launcher and not a failed row with a brief seeded for it.
            if launcher.refusal_for(spec.cwd) is not None:
                LOG.error("app", f"refused a contained launch in {spec.cwd}: {_MOVED_INSIDE}")
                return LaunchRefused(f"{spec.cwd} {_MOVED_INSIDE}")
        # The worktree ``spec.cwd`` names, made before the session exists: the
        # session, its brief and its announce all read the cwd before any process
        # does. After the containment checks, so a launch they refuse makes nothing.
        # On a worker for the reason the version read is: ``git worktree add`` checks
        # out every file and runs the operator's hooks.
        #
        # A refusal holds the launch with git's reason and the draft goes back; the
        # session is never started anywhere else instead.
        if spec.worktree is not None:
            if state.fake:
                return LaunchRefused("--fake makes no worktrees; clear the branch to launch")
            made = await asyncio.to_thread(launcher.make_worktree, spec.worktree)
            match made:
                case None:
                    LOG.info("app", f"worktree at {spec.cwd} on {spec.worktree.branch} is ready")
                case launcher.WorktreeRefused(reason=reason):
                    LOG.error("app", f"refused a launch into a worktree at {spec.cwd}: {reason}")
                    return LaunchRefused(reason)
                case launcher.UncommittedFound(reason=reason):
                    LOG.warn("app", f"held a launch into a worktree at {spec.cwd}: {reason}")
                    return LaunchHeldOnChanges(reason)
                case _:
                    assert_never(made)
        # Before the session exists, because it is constructed with what the log
        # says: the addresses its earlier agents answered to. Read on a worker for
        # the reason the version is.
        #
        # Only for a resume, and only for an id this process has no entry for --
        # ``_open_board_log`` says why a known one is not read back. A session
        # that is not resuming reads no log at all.
        logged: _Logged | None = None
        if spec.resume is not None:
            writer = state.board_writer
            if writer is not None and spec.resume not in state.board_logs:
                logged = await asyncio.to_thread(_read_log, writer, spec.resume)
                if isinstance(logged.board, boardlog.Board):
                    state.restored_addresses[spec.resume] = _addresses_of(logged.board, spec.resume)
            # Its commands too, and here for the same reason: before the pool
            # has the session, so what its files hold is on the queue ahead of
            # anything its run asks.
            if state.command_files is not None:
                await asyncio.to_thread(_restore_commands, state, spec.resume)
        addresses = _carried_addresses(state, spec.resume)
        titled = await _recorded_title(state, spec)
        session = AgentSession(
            state.bridge,
            titled.task,
            model=spec.model,
            cwd=spec.cwd,
            # Travels with cwd: the pair is what a write is placed against, and a
            # launch that carried one without the other would measure the session
            # in units nothing else agrees with.
            session_base=spec.session_base,
            containment=spec.containment,
            brief=spec.brief,
            template=shape,
            # The launch's own number when it named one, the operator's setting
            # otherwise. Tested against `is None` and never for truth: `0` is a
            # session that may not spawn at all, and `or` would quietly replace that
            # deliberate answer with the setting it was chosen instead of.
            subagent_cap=(
                state.settings.subagent_cap if spec.subagent_cap is None else spec.subagent_cap
            ),
            # From the operator's settings rather than the spec: which MCP servers
            # exist is a property of the machine they are sitting at, not of the piece
            # of work they just typed.
            connectors=state.settings.admitted_connectors,
            # Travels with containment, as it does on the spec: at the top rung the
            # policy releases ``Bash`` from the gate and the containment is the only
            # thing bounding what a released ``Bash`` reaches, so a session given one
            # without the other is half an arrangement nobody chose.
            #
            # The last point the spec is read for the rung, too: from here the session
            # owns its policy and the live value is the only one a display may use.
            # Rendering the spec would keep showing the rung the operator asked for at
            # launch after the session had left it.
            policy=spec.policy,
            resume=spec.resume,
            restored_addresses=addresses,
            message=spec.message,
            task_is_label=titled.task_is_label,
        )
        # Ahead of the pool's answer, so a launch the pool then refuses leaves the
        # directory this wrote behind it, under a session id nothing ran as. The
        # order is fixed from the other side: ``submit`` announces, and the announce
        # carries the path. The refusal is not asked for first because ``submit`` is
        # the only thing that says whether the pool takes a session, and a second
        # question put ahead of it would be the same fact with two readers to keep
        # in step. The window is a contained launch whose version read was still
        # out when the application began to quit.
        _seed_brief(session, shape, scratch=state.fake)
        # Before the pool has it: ``submit`` announces, the announce is applied
        # on the draw thread, and a change derived from it for a session the
        # writer has not been told of is dropped. Admitting creates nothing, so
        # a launch the pool then refuses leaves no file.
        if not state.fake:
            state.fleet_writer.admit(session.session_id)
        # What the store is not told of a run -- the text it was opened with,
        # its gate, whether it is contained, its cap -- is said when the run
        # has reached the CLI and not here. The pool may discard this session
        # before it starts, and ``AgentSession.run`` may refuse it; a file that
        # already held this launch's message would have the resume form offer a
        # message nobody was sent, under a gate nothing ran with.
        session.on_started = lambda: _note_started(state, titled, session)
        if not pool.submit(session):
            # The version read above is awaited, so a launch pressed before the
            # window closed can arrive here after the pool has begun shutting down.
            LOG.warn("app", f"that {origin} in {spec.cwd} started nothing: the pool is shut")
            return LaunchRefused("the application is shutting down, so no session was started")
        state.ran[session.session_id] = session
        # Nothing may be awaited between the pool's answer and this call.
        #
        # Caught here and not left to ``go``: the pool has taken the session, so
        # an exception from this point is not a launch that started nothing, and
        # ``go`` would hand the launcher a refusal for a session that is running.
        try:
            _open_board_log(state, session.session_id, logged)
        except Exception as exc:  # noqa: BLE001 - the session runs; say its board is unrecorded
            _board_log_unopened(state, session.session_id, exc)
        # Behind the pool's answer, so that the log says a launch happened only for
        # one the pool took: a refusal by the version check, by the pool, or by an
        # exception out of either has returned or raised above this line. Which of
        # the two words it is is worth one: a resumed session runs under an id that
        # already has a transcript, so "launched" alone would leave the log unable
        # to explain why a node appeared with history behind it.
        opening = "resumed" if spec.resume else "launched"
        LOG.info("app", f"{opening} in {spec.cwd} as {shape.name}: {titled.task[:60]}")
        return LaunchStarted()

    state.launches.append(_InFlight(state.bridge.submit(go()), draft, origin, resumed))


async def _recorded_title(state: AppState, spec: LaunchSpec) -> LaunchSpec:
    """
    ``spec`` under the task its session's file recorded, when ``spec`` resumes a
    session and has only a label for a title; ``spec`` itself otherwise.

    A resume pressed in New Task knows the session by a row of the CLI's
    listing, which carries a summary of the conversation and no task
    (``LauncherState.resumed``), and the announce puts the spec's title on the
    card. For a session this application ran before and no longer shows -- one
    removed from the fleet in an earlier run -- the file is the one place the
    task still is. Read on a worker, as everything else a resume reads before
    it starts is.

    Nothing is read under ``--fake``, which writes no files, and a file that is
    absent, unreadable or records no task leaves the label standing: the card
    then says its title is no task, and Fork and Retry refuse it.
    """
    if spec.resume is None or not spec.task_is_label or state.fake:
        return spec
    found = await asyncio.to_thread(sessionfile.read, spec.resume)
    if not isinstance(found, sessionfile.SessionFile) or not found.task.strip():
        return spec
    return dataclasses.replace(spec, task=found.task, task_is_label=False)


def _note_started(state: AppState, spec: LaunchSpec, session: AgentSession) -> None:
    """
    A run reached the CLI and was handed its opening text: say in the session's
    file what it was started with (``fleetsync.launched``), and keep the text as
    the last thing this window sent the session.

    ``AgentSession.on_started`` for every session ``_launch`` builds, so it runs
    on the asyncio thread, once per run. The gate is the session's own at this
    moment and not the spec's: it can have been moved between the launch and
    the connect, and the file says what the run is under. Read and queued
    under ``AppState.gate_writes``, so a move made on the draw thread is queued
    wholly before this or wholly after it and the later value is the gate's.
    """
    state.last_sent[session.session_id] = spec.task if spec.message is None else spec.message
    with state.gate_writes:
        state.fleet_writer.submit(
            fleetsync.launched(
                spec,
                session.session_id,
                session.policy,
                session_details.is_contained(session.containment),
                time.time(),
                subagent_cap=session.subagent_cap,
            )
        )


def _launch_ended(
    future: concurrent.futures.Future[LaunchOutcome],
) -> LaunchRefused | LaunchHeldOnChanges | None:
    """
    Why a finished launch started no session, or None when it started one.

    Total over the three ways a future ends. ``go`` turns its own exceptions into a
    ``LaunchRefused``, so the other two arms are a coroutine cancelled by the loop
    shutting down and anything that escaped regardless; neither is a session, and
    both are refusals. The outcome is handed on as its variant, not as its words:
    what the launcher does with it depends on which it is.
    """
    if future.cancelled():
        return LaunchRefused("the launch was cancelled before a session started")
    exc = future.exception()
    if exc is not None:
        return LaunchRefused(f"{type(exc).__name__}: {exc}")
    outcome = future.result()
    match outcome:
        case LaunchStarted():
            return None
        case LaunchRefused() | LaunchHeldOnChanges():
            return outcome
        case _:
            assert_never(outcome)


def _why_not_started(future: concurrent.futures.Future[LaunchOutcome]) -> str | None:
    """
    ``_launch_ended``'s words alone, or None for a launch that started a session.
    For saying and for tests; nothing decides on it.
    """
    ended = _launch_ended(future)
    return None if ended is None else ended.reason


def _reap_launches(state: AppState) -> None:
    """
    Read the outcome of every launch that has one, on the draw thread.

    This is the crossing, and it is the one ``Bridge.submit`` already provides: the
    concurrent future it returns "for cancellation and error reporting". The frame
    polls it rather than blocking on it. An intent through the store is the other
    road across and the wrong one here -- a launch that started nothing changes no
    fact the snapshot holds, and a launcher draft is presentation state the store
    deliberately does not own.

    A started launch is dropped and nothing else happens: the session is the pool's
    from there, and its node is where the operator sees it. A launch that started
    nothing goes to ``LauncherState.report_failure`` with its draft, or with None and
    the word for where it came from. The launcher is the surface for those too: it is
    the one place that reopens itself, and a relaunch that did nothing has no row, no
    node and no pane of its own to be shown in.

    A resume pressed from the resume form goes back to that session's form
    instead, with the form's draft (``ResumeForms.report_failure``): the text
    belongs on the surface that resumes that session, and New Task would offer
    a fresh one. Only when no such form exists any more is it the launcher's.

    **The list is shared with the asyncio thread and is not locked.** The loop only
    appends; this iterates a copy and removes the entries it has read, one at a time.
    Assigning a filtered list back instead would drop an entry appended between the
    copy and the assignment, which is a launch nobody ever reads.

    Costs a truth test on an empty list on every frame that has nothing in flight.
    """
    if not state.launches:
        return
    for entry in list(state.launches):
        if not entry.future.done():
            continue
        state.launches.remove(entry)
        ended = _launch_ended(entry.future)
        match ended:
            case None:
                continue
            case LaunchRefused(reason=reason):
                accept = False
            case LaunchHeldOnChanges(reason=reason):
                accept = True
            case _:
                assert_never(ended)
        if entry.resumed is not None and _resume_failed(state, *entry.resumed, reason):
            continue
        state.launcher.report_failure(entry.draft, reason, entry.origin, accept_uncommitted=accept)


def _form_up(state: AppState, but: str | None = None) -> bool:
    """
    Whether one of the application's modals other than the "Not done" notice is
    up or has been asked for: New Task, a resume form, Details, a card menu's
    question, or the settings dialog. ``but`` names a session whose own resume
    form does not count.

    **What must not be opened over.** Every one of these is a popup opened at
    the root, and Dear ImGui holds one popup per level: opening a second closes
    the first, with whatever the operator was doing in it. Last frame's values,
    which is what each modal's own flag is.
    """
    cards = state.cards
    resume = state.resume
    return (
        state.launcher.is_open
        or (resume.busy and resume.showing != but)
        or cards.details.node is not None
        or cards.confirming is not None
        or cards.gating is not None
        or state.settings_dialog.is_open
    )


def _resume_failed(state: AppState, session_id: str, draft: resume_form.Draft, reason: str) -> bool:
    """
    Tell the operator a resume pressed from the resume form started nothing, and
    put what they typed back on that session's form. False when no such form
    exists any more, and nothing was said.

    **At once when nothing else is up**: the form is asked for again with the
    reason over the message. **Otherwise by the "Not done" notice**, which
    waits for whatever is up to be put away (``_draw_not_done``). The refusal
    can arrive seconds after the press -- a contained resume reads the CLI's
    version first -- and by then the operator may be in New Task, Details,
    another session's form or a notice; reopening the form there would close
    what they are in (``_form_up``), and saying nothing would leave a card that
    stays closed for no reason on screen. The session's own form being up is
    neither case: the failure is drawn on it.
    """
    resume = state.resume
    if resume.on_screen(session_id):
        return resume.report_failure(session_id, draft, reason)
    in_the_way = _form_up(state) or bool(state.not_done)
    if not resume.report_failure(session_id, draft, reason, show=not in_the_way):
        return False
    if in_the_way:
        rec = state.store.snapshot().get((session_id, None))
        name = flat_line(rec.task).strip() if rec is not None else ""
        LOG.warn("app", f"the resume of {session_id} did not start: {reason}")
        _not_done(
            state,
            f"the resume of {name[:80] or session_id} did not start: {reason}. What you "
            f"typed is kept on its resume form: {session_menu.RESUME_LABEL} on its card.",
        )
    return True


def _seed_brief(
    session: AgentSession, shape: templates.WorkTemplate, *, scratch: bool = False
) -> None:
    """
    Write the launch text as the session's first premise, for a team.

    **This is where the premises already are.** The record that asked for a brief
    diagnoses the gap as *"the premises the operator means are not in project
    memory -- they are in the launch text box"*: that text reaches the lead as its
    first user message and reaches a worker through nothing but the lead choosing to
    re-express it. Writing it here is what makes it addressable, and it costs the
    operator no new habit -- they already typed it.

    Between construction and ``pool.submit``, which is the only window where the
    session id exists and nothing has spawned yet. ``submit`` announces, ``run``
    connects, and a worker cannot be told a path that was not set before either.

    Skipped for a solo session, which has no workers to seed, and skipped when the
    operator named a directory -- that is a session pointed at an existing brief,
    typically a fork inheriting its parent's, and seeding over it would bury the
    premises it was launched to continue.

    **Nothing is written for a resume.** Its text is the next message of a
    conversation and not the premises of one, and the directory this would
    write to is the session's own, since a resume keeps its id: the entry would
    be appended to the brief the session already has. A resumed team that was
    handed no directory is given that one when it exists (``brief.existing_dir``,
    which also finds one written under the legacy layout), so its workers are
    still told where the premises are whichever surface resumed it; when neither
    exists the session runs with no brief, as it did.

    ``scratch`` is ``--fake``'s: the brief is written under a new temporary
    directory and a resume looks for none, because that run writes nothing into
    the configuration directory and reads nothing a real run left there.

    A failure does not stop the launch. The work the operator asked for is worth
    more than the seeding of it, and the log says which happened rather than
    leaving a team quietly unbriefed.
    """
    if not shape.roles or session.brief or not session.task.strip():
        return
    if session.resume is not None:
        existing = (
            None if scratch else brief_mod.existing_dir(session.session_id, session.cwd or ".")
        )
        if existing is not None:
            session.brief = str(existing)
        return
    root = Path(tempfile.mkdtemp(prefix="pptmstr-fake-briefs-")) if scratch else None
    directory = brief_mod.session_dir(session.session_id, root)
    if directory is None:
        LOG.warn("app", f"no brief seeded: {session.session_id!r} is not a session id")
        return
    try:
        brief_mod.write_entry(directory, session.task)
    except OSError as exc:
        LOG.warn("app", f"no brief seeded ({exc}); workers will not be told one exists")
        return
    session.brief = str(directory)
    LOG.info("app", f"seeded brief at {directory}")


def _policy_of(state: AppState, node: NodeId | None) -> Policy | None:
    """
    The gate policy the session behind a node is answering with right now, or None
    if no session holds it.

    Read off the session and never off the record or the spec: it narrows when the
    phase ends, so any copy taken earlier describes a session that has already
    moved. One dict lookup on the draw thread, in the same class as the pool counts
    the status bar reads -- the attribute is monotone and written as a whole
    constant, so a torn schedule can only show the wider value one frame longer.
    """
    pool = state.pool
    if pool is None or node is None:
        return None
    session = pool.session_for(node)
    return None if session is None else session.policy


def _revoke_policy(state: AppState, node: NodeId | None) -> None:
    """
    End a session's relaxed phase now, rather than waiting for the call that ends
    it by parking.

    Called straight from the draw thread instead of through the Bridge. The write
    is one constant and idempotent and ``revoke_policy`` is its only writer, so
    there is no ordering for a hop to buy -- and the hop would cost the operator a
    frame in which the display still reads the phase they just ended.
    """
    pool = state.pool
    if pool is None or node is None:
        return
    session = pool.session_for(node)
    if session is None:
        return
    with state.gate_writes:
        before = session.policy
        session.revoke_policy()
        if session.policy is not before:
            # The file's gate is the one a closed card says the session last
            # ran under, so a narrowing is written like any other move.
            _note_gate(state, node, session)


def _note_gate(state: AppState, node: NodeId, session: AgentSession) -> None:
    """
    Say in the session's file that its gate is the one ``session`` holds now
    (``fleetsync.gate_changed``). Called with ``AppState.gate_writes`` held, by
    the two functions that move a gate.

    **Only for a session with a run.** The pool keeps the object of a session
    whose run ended by itself, and moving that object's gate moves nothing: no
    process is gated by it, and the next run is built from a spec. The file's
    gate is the rung the session last *ran* under, which a closed card and the
    resume form both show, so a move made after the run is not written.
    """
    pool = state.pool
    if pool is None or not pool.has_run(node):
        return
    state.fleet_writer.submit(fleetsync.gate_changed(node[0], session.policy))


def _set_policy(state: AppState, node: NodeId, rung: Policy) -> None:
    """
    Move a running session's gate to ``rung``, now, if the move is one that may
    be made; say why not otherwise (``_not_done``).

    On the draw thread, for ``_revoke_policy``'s reason, and called by the card
    menu outside every ImGui begin/end pair, which is what lets it catch.

    Two things can refuse, and neither is asked by the other. **The directory**:
    a rung that requires containment is not granted to a session whose directory
    is inside pptmstr's own checkout, or cannot be located -- the question the
    launcher, ``_launch`` and ``AgentSession.run`` each ask before such a session
    starts (``launcher.refusal_for``), and which ``AgentSession.set_policy``
    does not ask at all, because until a menu could widen a live session there
    was no way to reach the rung except by starting under it. Asked on the way
    to such a rung and not on the way down from one. **The driver's guard**: a
    session started with no containment cannot be given a rung that needs it
    (``driver.UncontainedAutonomy``). The menu offers that move as a resume and
    not as a live one, so the raise is for a menu drawn before the facts changed.

    A move the driver took is written to the session's file.
    """
    pool = state.pool
    session = None if pool is None else pool.session_for(node)
    if session is None:
        _not_done(state, f"the gate was not moved to {rung.value}: {session_menu.NOT_HELD}")
        return
    before = session.policy
    if rung is before:
        return
    if requires_containment(rung):
        refusal = launcher.refusal_for(session.cwd or ".", named="this session's directory")
        if refusal is not None:
            LOG.warn("app", f"{node[0]} was not moved to {rung.value}: {refusal}")
            _not_done(state, f"the gate was not moved to {rung.value}: {refusal}")
            return
    with state.gate_writes:
        try:
            session.set_policy(rung)
        except UncontainedAutonomy as exc:
            LOG.warn("app", f"{node[0]} was not moved to {rung.value}: {exc}")
            _not_done(state, f"the gate was not moved to {rung.value}: {exc}")
            return
        LOG.info("app", f"{node[0]}: gate moved from {before.value} to {session.policy.value}")
        _note_gate(state, node, session)


def _relaxed_count(state: AppState) -> int:
    """
    How many live sessions are running under something other than ``STRICT``.

    ``list()`` rather than a walk of the values: the pool is mutated on the asyncio
    thread, and the copy completes without yielding to it where a loop over the view
    can be interrupted mid-iteration.
    """
    pool = state.pool
    if pool is None:
        return 0
    return sum(1 for s in list(pool.sessions.values()) if s.policy is not Policy.STRICT)


def _session_action(state: AppState, coro_factory: Callable[[SessionPool], object]) -> None:
    """Run a pool operation on the asyncio thread."""
    pool = state.pool
    if pool is None:
        return
    state.bridge.submit(coro_factory(pool))  # type: ignore[arg-type]


# What the session controls do, once each. Every surface that offers one of these
# -- HEALTH, the failure row and the composer in NEEDS YOU, the SESSION pane, a
# card's menu -- is handed these functions, so no two of them can come to differ.


def _interrupt(state: AppState, node: NodeId) -> None:
    _session_action(state, lambda p: p.interrupt(node))


def _close(state: AppState, node: NodeId) -> None:
    """
    Close a session: end its run through the pool, which reports the end.

    **A session that failed and has no run left is closed in the store
    instead** (``intents.FailedSessionClosed``). The pool has nothing to end
    for it and would report nothing, and its root would stay in FAILED, which
    is an end a later message undoes and so one the session can never leave the
    fleet from. Whether a run is left is the pool's answer, read here at the
    click (``SessionPool.has_run``), and the intent carries it.

    The pool is not asked to close that one as well: it would find only the
    ended session object, and a resume of the session racing this would be the
    run it ended.
    """
    pool = state.pool
    snap = state.frame_snap
    root = None if snap is None else snap.get((node[0], None))
    if (
        pool is not None
        and root is not None
        and root.state is AgentState.FAILED
        and not pool.has_run(node)
    ):
        state.bridge.emit(FailedSessionClosed((node[0], None), run_held=False))
        return
    _session_action(state, lambda p: p.close(node))


def _detach(state: AppState, node: NodeId) -> None:
    """
    Take an ended session off the fleet: its card goes, and its file says so,
    so that it is not recalled at the next start. Nothing is deleted.

    **The pool is asked first, and with a run under the id nothing is asked of
    the store.** The store's own rule covers what a snapshot holds -- every
    agent of the session ended for good, nothing owed -- and a snapshot does not
    hold whether a process is attached: a session that failed keeps its client,
    and one being relaunched is announced only when the run before it has gone.
    The store's answer comes back as an effect (``_note_detach``), and the file
    is written from the step that took the session off (``fleetsync.changes``).
    """
    pool = state.pool
    if pool is not None and pool.has_run(node):
        _not_done(state, f"not removed from the fleet: {session_menu.RUN_ATTACHED}")
        return
    state.bridge.emit(SessionDetached(node[0]))


def _from_a_label(state: AppState, spec: LaunchSpec, what: str) -> bool:
    """
    Whether ``spec`` would open a new session with a title for a prompt, having
    said so if it would (``_not_done``).

    Fork and Retry start ``LaunchSpec.from_record``, whose prompt is the
    record's task, and for a session whose original task nothing recorded that
    is the CLI's label for the conversation (``AgentRecord.titled_by_label``):
    a paid session instructed by a summary, in one click. Asked here, of the
    spec, because these two functions are what every surface is handed -- a
    card's menu greys the item first, and the failure row and HEALTH's footer
    hand a spec over with nothing asked of the record.
    """
    if not spec.task_is_label:
        return False
    _not_done(state, f"not {what}: {session_menu.TASK_NOT_RECORDED}")
    return True


def _fork(state: AppState, spec: LaunchSpec) -> None:
    if not _from_a_label(state, spec, "forked"):
        _launch(state, spec, origin="fork")


def _fork_of(state: AppState, node: NodeId | None, spec: LaunchSpec) -> None:
    """
    Fork the session behind ``node``, for a surface that builds the spec from
    the record without asking anything of it (HEALTH).

    Not from a card whose title is only the CLI's label for the conversation,
    or is empty (``session_menu.task_missing``): that string is no prompt, and
    this would start a paid session with it in one click. The card's own menu
    greys the item for the same reason; here it is said after the press. The
    record is asked as well as the spec (``_from_a_label``) because an empty
    task is a fact about the record that no spec says.
    """
    snap = state.frame_snap
    root = None if snap is None or node is None else snap.get((node[0], None))
    if root is not None and session_menu.task_missing(root):
        _not_done(state, f"not forked: {session_menu.TASK_NOT_RECORDED}")
        return
    _fork(state, spec)


def _relaunch(state: AppState, spec: LaunchSpec) -> None:
    if not _from_a_label(state, spec, "relaunched"):
        _launch(state, spec, origin="relaunch")


def _dismiss(state: AppState, node: NodeId) -> None:
    state.bridge.emit(FailureAcknowledged(node))


def _send(state: AppState, node: NodeId, text: str) -> None:
    """
    Send a session the operator's message, and note it in the session's file as
    the last thing they sent it (``fleetsync.sent``).

    **Noted only for a send a client took.** ``SessionPool.send`` says whether
    the text was handed to a connected client or dropped for want of one, and
    that answer is the whole of the test: a session that has not connected, one
    whose run is over, and a relaunch held behind a run that is still ending
    all drop the text, and the last of them does it under a row that reads
    live. The note is queued after the send has returned, so one that raised is
    not noted either. The message box of the resume form opens with what is
    noted here, one press from being sent again.
    """

    async def go(pool: SessionPool) -> None:
        if await pool.send(node, text):
            state.last_sent[node[0]] = text
            state.fleet_writer.submit(fleetsync.sent(node[0], text, time.time()))

    _session_action(state, go)


def _open_resume(state: AppState, rec: AgentRecord, rung: Policy | None = None) -> None:
    """
    Open the resume form for a card's session: the menu's "Resume…", and with
    ``rung`` its "Resume as …" and "Continue as …". Nothing is started from
    here; the form's own button is the step that resumes. **The one way the
    form opens**: a session picked in New Task's session list is resumed by New
    Task (``_launcher_pressed``).

    What the form shows as facts is read here, once, from whichever of two
    places knows the session:

    - **A closed card** (``AgentRecord.recalled``): its session file's account,
      which the recall worker put on the record. Nothing is read from disk on
      this thread. A field the file did not have is given as not known and
      never as the record field's default: no team recorded is
      ``resume_form.UnknownTeam`` and not solo, no gate is
      ``sessionfile.UnknownPolicy`` and not strict, and a title that is only
      the CLI's label for the conversation is passed as a label
      (``AgentRecord.titled_by_label``), so the resume announces the session
      under the title its card has and still says it is no task.
    - **A session this window ran**: its record, the session object for the
      gate, containment and cap it holds now, and the last text this window
      sent it (``AppState.last_sent``).

    The gate the form opens on is strict unless ``rung`` names one, whatever
    the session last ran under (``ResumeForms.open_for``).
    """
    session_id = rec.node_id[0]
    recalled = rec.recalled
    template: str | None | resume_form.UnknownTeam = rec.template or resume_form.UnknownTeam()
    policy: Policy | sessionfile.UnknownPolicy
    if recalled is not None:
        policy = recalled.policy or sessionfile.UnknownPolicy()
        contained = recalled.contained is True
        cap = recalled.subagent_cap
        last = recalled.last_message
    else:
        held = _held(state, rec.node_id)
        session = state.ran.get(session_id)
        policy = rec.policy if held is None else held.policy
        contained = held is not None and held.contained
        cap = None if session is None else session.subagent_cap
        last = state.last_sent.get(session_id)
    state.resume.open_for(
        session_id=session_id,
        cwd=rec.cwd or "",
        task=rec.task,
        task_is_label=rec.titled_by_label,
        template=template,
        policy=policy,
        contained=contained,
        model=rec.model,
        subagent_cap=cap,
        brief=rec.brief,
        last_message=last,
        as_policy=rung,
    )


def _launcher_pressed(state: AppState, spec: LaunchSpec, draft: launcher.Draft) -> None:
    """
    New Task's launch button: hand its spec to ``_launch`` with the draft a
    failure is returned to. A new session's spec goes as it is.

    **A resume's is given the title its card has.** New Task builds a resume
    from a row of the CLI's listing, so its spec is titled by the CLI's label
    for the conversation (``LauncherState.resumed``). The announce replaces the
    session's record with one carrying the spec's title, so for a session the
    store has a record of -- on the fleet, live or closed, or removed from it
    in this window -- the label would rename the card. The record's own task
    goes instead, with the record's word on whether it is one. A session the
    store has no record of keeps the label here, and ``_launch`` asks its file.

    The message, the directory, the gate and everything chosen in New Task are
    the spec's and are not touched: both surfaces that resume build their spec
    through ``launcher.resume_spec``, and this is the same ``_launch``.
    """
    if spec.resume is not None:
        rec = state.store.snapshot().get((spec.resume, None))
        if rec is not None:
            spec = dataclasses.replace(spec, task=rec.task, task_is_label=rec.titled_by_label)
    _launch(state, spec, draft)


def _resume_pressed(state: AppState, pressed: resume_form.Launch) -> None:
    """
    The resume form's button: hand its spec to ``_launch``, with the session and
    the draft a failure is returned to.

    ``pressed.spec`` carries the typed text as ``message`` and the session's own
    task as ``task`` (``resume_form.press``), and nothing here rewrites either.
    """
    assert pressed.spec.resume is not None
    _launch(state, pressed.spec, origin="resume", resumed=(pressed.spec.resume, pressed.draft))


def _show_health(state: AppState, node: NodeId) -> None:
    """
    Bring HEALTH to a card's session: the cursor on that session, the layout that
    has the pane, and the pane in front of its tab-mates.

    HEALTH follows the cursor and is not pointed anywhere by itself, so moving
    the cursor is the whole of "to that session" -- what a click on the card
    does, through the same call. A session that owes something puts the cursor on
    what it owes, as that click would.
    """
    snap = state.frame_snap
    if snap is None:
        return
    state.focus.to_node(snap, (node[0], None), scope=focus_mod.Scope.SESSION)
    if hello_imgui.current_layout_name() != FOCUS:
        state.pending_layout = FOCUS
    state.pending_pane = "HEALTH"


def _card_actions(state: AppState) -> session_menu.Actions:
    """
    What a card's menu calls: the functions the other surfaces are handed.
    """
    return session_menu.Actions(
        interrupt=lambda node: _interrupt(state, node),
        close=lambda node: _close(state, node),
        fork=lambda spec: _fork(state, spec),
        revoke_policy=lambda node: _revoke_policy(state, node),
        relaunch=lambda spec: _relaunch(state, spec),
        dismiss=lambda node: _dismiss(state, node),
        resume=lambda rec: _open_resume(state, rec),
        health=lambda node: _show_health(state, node),
        detach=lambda node: _detach(state, node),
        resume_as=lambda rec, rung: _open_resume(state, rec, rung),
        set_policy=lambda node, rung: _set_policy(state, node, rung),
    )


def _card_wiring(state: AppState) -> session_menu.Wiring:
    """
    What the fleet pane draws card menus with: the actions, and the three
    things a menu asks that no record says.

    ``running`` is the pool's answer to "is a run attached to this session"
    (``SessionPool.has_run``), which is the shell's half of whether a card may
    leave the fleet and of what Close does to a failed one. With no pool there
    is no run. ``contained`` is ``_held``'s, the driver guard's notion.
    """
    pool = state.pool

    def contained(node: NodeId) -> bool | None:
        held = _held(state, node)
        return None if held is None else held.contained

    return session_menu.Wiring(
        state=state.cards,
        actions=_card_actions(state),
        gate=lambda node: _policy_of(state, node),
        snap=state.frame_snap,
        running=lambda node: pool is not None and pool.has_run(node),
        contained=contained,
    )


def _held(state: AppState, node: NodeId) -> session_details.Held | None:
    """
    What the session object behind a node says right now, for Details, or None
    when this window holds none for it: the fake driver's sessions, which are
    never in the pool, and a session recalled from its file, which this process
    has not run. Details reads the second from the record (``AgentRecord.recalled``)
    and says its facts are the session file's.

    The pool's session while it has one, and otherwise the last one it took
    under the id (``AppState.ran``): the object of a session closed in this
    window still says whether it was contained and where its transcript is.

    ``contained`` is the driver guard's notion and not "a settings string was
    given": a blank one is no containment (``session_details.is_contained``).
    """
    pool = state.pool
    session = None if pool is None else pool.session_for(node)
    running = session is not None
    if session is None:
        session = state.ran.get(node[0])
    if session is None:
        return None
    return session_details.Held(
        policy=session.policy,
        contained=session_details.is_contained(session.containment),
        transcript=session.transcript_path,
        running=running,
    )


def _draw_card_overlays(state: AppState) -> None:
    """
    The two modals a card's menu opens, at root level for the launcher's reason.

    The disk is read here and once: on the frame Details is asked for, for the
    node it was asked for (``session_details.look``). Everything else in the
    modal is rebuilt from the frame's snapshot.
    """
    snap = state.frame_snap
    if snap is None:
        return
    cards = state.cards
    session_menu.draw_confirm(cards, snap, _card_actions(state))
    details = cards.details
    node = details.node
    if node is None:
        return
    held = _held(state, node)
    rec = snap.get(node)
    if details.looked is None and rec is not None:
        details.looked = session_details.look(rec, None if held is None else held.transcript)
    shown = session_details.view(
        snap,
        node,
        held=held,
        looked=details.looked,
        logging=board_logging(state),
        now=state.frame_now,
        wall_now=time.time(),
        # The root's, which is the only activity the writer is told of. Not
        # read for a recalled session: its row is its file's.
        last_active=state.fleet_writer.last_active(node[0]) if node[1] is None else None,
    )
    session_details.draw(details, shown)


# ``###`` keeps the window's identity whatever the words in front of it.
NOT_DONE_TITLE = "Not done###not-done"
_NOT_DONE_WIDTH = 520.0


def _draw_not_done(state: AppState) -> None:
    """
    What a card's control was asked to do and did not, until it is dismissed.

    A modal, at root level with the other things a card's menu opens. A refusal
    here is of something the operator pressed: the menu that offered it has
    closed, the card looks as it did, and a line in LOG is in a pane that is
    not resident. **It waits for any other modal to be put away** (``_form_up``):
    a refusal can arrive while the operator is in one -- a resume refused after
    its version read, a removal the store refused -- and a popup opened at the
    root closes the one already there. Enter or Escape dismisses, from the second frame on, for
    the reason a card's confirmation waits one (``MenuState.confirm_frames``).
    """
    if not state.not_done:
        state.not_done_frames = 0
        return
    if state.not_done_frames == 0:
        if _form_up(state):
            # Its turn comes when that is put away. Opened now it would close
            # it, and the lines lose nothing by waiting.
            return
        imgui.open_popup(NOT_DONE_TITLE)
    viewport = imgui.get_main_viewport()
    imgui.set_next_window_pos(viewport.get_center(), imgui.Cond_.appearing, imgui.ImVec2(0.5, 0.5))
    imgui.set_next_window_size_constraints(
        imgui.ImVec2(_NOT_DONE_WIDTH, 0.0), imgui.ImVec2(_NOT_DONE_WIDTH, viewport.work_size.y)
    )
    opened, _ = imgui.begin_popup_modal(NOT_DONE_TITLE, None, imgui.WindowFlags_.always_auto_resize)
    if not opened:
        # Something closed it from outside. The lines are kept and asked for
        # again: an unread refusal is the one thing this must not drop.
        state.not_done_frames = 0
        return
    listening = state.not_done_frames > 0
    state.not_done_frames += 1
    imgui.push_text_wrap_pos(0.0)
    for line in state.not_done:
        imgui.text_colored(P.warn.vec4, line)
        imgui.spacing()
    imgui.pop_text_wrap_pos()
    dismissed = bool(imgui.button("ok"))
    imgui.same_line()
    imgui.text_disabled("Enter or Esc dismisses")
    if listening:
        dismissed = dismissed or bool(
            imgui.is_key_pressed(imgui.Key.enter, False)
            or imgui.is_key_pressed(imgui.Key.keypad_enter, False)
            or imgui.is_key_pressed(imgui.Key.escape)
        )
    if dismissed:
        state.not_done.clear()
        state.not_done_frames = 0
        imgui.close_current_popup()
    imgui.end_popup()


def _menus(state: AppState) -> None:
    """
    The menu bar: built from the frame's facts (``main_menu``), and what was
    chosen carried out after every menu is closed.

    **An open menu takes the frame's bare keys**, as an open card menu does, and
    through the same flag (``MenuState.shown``, read by ``claim_keys``): the
    Session menu holds the focused card's items, and a ``k`` that moved the cursor
    under it would rebuild it for another card, which the next click would then
    act on. Any menu, not only Session: an ``a`` pressed over an open Theme menu
    approves nothing either.

    **An open Session menu is the card's it was opened for** (``state.menu_latch``),
    not the cursor's: the cursor also moves with no key pressed -- work arriving
    in another session takes an unpinned cursor to it (``FocusState.settle``) --
    and a menu that followed it would put that session's Interrupt under a
    pointer aimed at this one's. Latched on the frame the bar is first seen
    open, from the facts that frame was drawn with; it cannot be clicked earlier,
    since the click that opens it is not also one on an item. Held while any
    menu is open, not only Session: the pointer sliding along an open bar to
    View and back reopens Session with no click, and that is the same opening
    (``main_menu.opening`` carries it over the frame ImGui yields between the
    two, with neither drawn). Let go when the bar has no menu open, so each
    opening is for the card the cursor is on then.
    """
    facts = _menu_facts(state)
    menus = main_menu.build(facts)
    drawn = main_menu.draw(menus)
    held = drawn.open is not None or (state.menu_latch is not None and main_menu.opening(menus))
    state.menu_latch = main_menu.Latch(facts.card) if held else None
    if drawn.open is not None:
        state.cards.shown = True
    if drawn.chosen is not None:
        _perform(state, drawn.chosen)


def _menu_facts(state: AppState) -> main_menu.Facts:
    """
    What the menu bar is built from, read each frame: a pane added to the runner
    after setup is listed as soon as it is there.

    The card is the latched one while the bar is open (``_menus``), the cursor's
    otherwise. A latched card that has left the fleet is still the card -- the
    record last read -- with every item refused, rather than whichever card
    the cursor fell to. Find opens the row of the log the panes draw, and they
    follow the cursor, so it is refused while the cursor is off the latched card.
    """
    runner = hello_imgui.get_runner_params()
    windows = tuple(
        main_menu.Window(window.label, window.is_visible)
        for window in runner.docking_params.dockable_windows
        if window.include_in_view_menu and window.can_be_closed
    )
    card = None
    find_refused = None
    snap = state.frame_snap
    latch = state.menu_latch
    if latch is None:
        node = state.focus.node(snap) if snap is not None else None
    else:
        node = None if latch.card is None else latch.card.record.node_id
    record = snap.get(node) if snap is not None and node is not None else None
    if snap is not None and record is not None and snap.off_fleet(record.node_id[0]):
        record = None
    if latch is not None and latch.card is not None and record is None:
        card = dataclasses.replace(latch.card, gone=main_menu.CARD_GONE)
        find_refused = main_menu.CARD_GONE
    elif snap is not None and record is not None:
        wiring = _card_wiring(state)
        card = main_menu.Card(
            record,
            session_menu.entries(record, wiring.gate(record.node_id), wiring.facts(record.node_id)),
        )
        if latch is not None and state.focus.node(snap) != record.node_id:
            find_refused = main_menu.FIND_NOT_SHOWN
        else:
            find_refused = _find_refused(runner, snap, record)
    return main_menu.Facts(
        settings=state.settings,
        layout=hello_imgui.current_layout_name(),
        layouts=tuple(LAYOUT_KEYS.items()),
        windows=windows,
        status_bar=runner.imgui_window_params.show_status_bar,
        card=card,
        typeset_available=typeset.available(),
        typeset_obtainable=typeset.obtainable(),
        typeset_reason=typeset.unavailable_reason() or typeset.diff_renderer(),
        find_refused=find_refused,
    )


_LOG_PANES = ("CONTEXT", "SESSION")
"""The panes that draw the focused card's log with its find row."""


def _find_refused(
    runner: hello_imgui.RunnerParams, snap: Snapshot, record: AgentRecord
) -> str | None:
    """
    Why no pane can take a find for ``record`` now, or None. The log panes follow
    the cursor, so a find asked for is the focused card's; it is drawn where the
    pane draws the log, which it does not for a session recalled from its file
    (``widgets.closed_line``), and nowhere when neither log pane is shown.
    """
    if widgets.closed_line(snap.get((record.node_id[0], None))) is not None:
        return main_menu.FIND_NO_LOG
    shown = {window.label for window in runner.docking_params.dockable_windows if window.is_visible}
    if not shown.intersection(_LOG_PANES):
        return main_menu.FIND_NO_PANE
    return None


def _perform(state: AppState, act: main_menu.Act) -> None:
    """
    What choosing a menu item does: each the function its other control calls.
    """
    match act:
        case main_menu.NewTask():
            state.launcher.request_open()
        case main_menu.OpenSettings():
            state.settings_dialog.request_open()
        case main_menu.SwitchLayout(name=name):
            # At the next frame's start, where every other switch is made.
            state.pending_layout = name
        case main_menu.ResetLayout():
            hello_imgui.get_runner_params().docking_params.layout_reset = True
        case main_menu.ShowWindow(label=label, visible=visible):
            for window in hello_imgui.get_runner_params().docking_params.dockable_windows:
                if window.label == label:
                    window.is_visible = visible
        case main_menu.ShowStatusBar(visible=visible):
            hello_imgui.get_runner_params().imgui_window_params.show_status_bar = visible
        case main_menu.CardChoice(node=node, action=action, rung=rung):
            record = state.frame_snap.get(node) if state.frame_snap is not None else None
            if record is None:
                return
            wiring = _card_wiring(state)
            session_menu.choose(
                action,
                record,
                wiring.gate(node),
                state.cards,
                wiring.actions,
                rung=rung,
                facts=wiring.facts(node),
            )
        case main_menu.ShowEndedSubagents(shown=shown):
            _switch_hide_ended(state, not shown)
        case main_menu.FindText():
            # The pane opens the row, and gives its field the keyboard, as it
            # draws: what the pane's own Ctrl+F does.
            state.transcripts.find.show()
        case main_menu.SetTheme(name=name):
            _switch_theme(state, name)
        case main_menu.WrapComposers(on=on):
            _switch_wrap(state, on)
        case main_menu.TypesetText(on=on):
            _switch_typeset(state, typeset_on=on)
        case _:
            assert_never(act)


_TYPESET_FIELDS = (
    "typeset",
    "typeset_family",
    "typeset_roles",
    "typeset_panes",
    "typeset_scale",
)
"""
The settings the seam is told. A change to any of them is one ``configure``.
"""


def _apply_settings(state: AppState, new: settings_mod.Settings) -> None:
    """
    Make ``new`` the settings: save them, and carry out what the difference means.

    **The one place a setting changes after start-up.** The menu and the settings
    dialog both end here, so a setting cannot be saved by one of them and applied
    by the other. What has to happen is read off the difference between the two
    values rather than passed in by the caller: a caller that had to say "and tell
    the seam" is a caller that can forget to, and the setting would then hold until
    restart, which for typeset text makes the comparison the switch exists for a
    comparison of two launches.

    Saved at the point of mutation rather than on exit: a preference that survives
    only a clean shutdown is one that silently resets after the first crash.

    Settings equal to the ones in force do nothing at all -- no write, no
    ``configure`` -- which is what makes a control safe to draw every frame.
    """
    old = state.settings
    if new == old:
        return
    state.settings = new
    _save(state)
    moved = [
        each.name for each in fields(new) if getattr(new, each.name) != getattr(old, each.name)
    ]
    if "theme" in moved:
        theme.set_theme(new.theme)
        state.theme_dirty = True
    if any(name in moved for name in _TYPESET_FIELDS):
        _configure_typeset(new)
    if "concurrency_cap" in moved:
        cap = new.concurrency_cap

        async def resize(pool: SessionPool) -> None:
            # On the loop, where every other mutation of the pool happens. Raising
            # it starts queued sessions; lowering it stops nothing that is running.
            pool.set_cap(cap)

        _session_action(state, resize)
    if "fps_idle" in moved and state.runner is not None:
        state.runner.fps_idling.fps_idle = new.fps_idle
    if "context_mode" in moved:
        # The pane's own buttons choose for the session and would otherwise hide
        # this change until the next launch.
        state.transcripts.mode = None
    LOG.info("settings", _described(new, moved))


def _described(settings: settings_mod.Settings, moved: list[str]) -> str:
    """
    One log line for a change: each field that moved and what it is now, with the
    structured ones summarised rather than printed.
    """
    said = []
    for name in moved:
        match name:
            case "typeset_roles":
                said.append(f"{len(settings.typeset_roles)} role(s) set apart")
            case "typeset_panes":
                off = ", ".join(pane for pane, on in settings.typeset_panes if not on)
                said.append(f"panes off: {off or 'none'}")
            case "admitted_connectors":
                said.append(f"{len(settings.admitted_connectors)} connector(s)")
            case _:
                said.append(f"{name} {getattr(settings, name)!r}")
    return ", ".join(said)


def _switch_theme(state: AppState, name: str) -> None:
    _apply_settings(state, state.settings.merged(theme=name))


def _switch_wrap(state: AppState, wrap: bool) -> None:
    _apply_settings(state, state.settings.merged(wrap_inputs=wrap))


def _switch_typeset(
    state: AppState,
    *,
    typeset_on: bool | None = None,
    scale: float | None = None,
    family: str | None = None,
    role: tuple[str, str | None, int | None] | None = None,
    pane: tuple[str, bool] | None = None,
) -> None:
    """
    Change how text is set: the menu's switch, and what a test or a script reaches
    for. ``_apply_settings`` saves it and tells the seam in the same breath.

    ``role`` is a role's name with the slot and the step to give it, either left
    ``None`` to keep what it has; ``pane`` is a pane's name and whether it is on.
    """
    changed = state.settings
    if typeset_on is not None:
        changed = changed.merged(typeset=typeset_on)
    if scale is not None:
        changed = changed.merged(typeset_scale=scale)
    if family is not None:
        changed = changed.merged(typeset_family=family)
    if role is not None:
        changed = changed.with_role(role[0], slot=role[1], step=role[2])
    if pane is not None:
        changed = changed.with_pane(*pane)
    _apply_settings(state, changed)


def _configure_typeset(settings: settings_mod.Settings) -> None:
    """
    Hand the seam everything the settings say about typeset text. The settings keep
    names; the seam owns what they mean and drops one it does not know.
    """
    typeset.configure(
        typeset=settings.typeset,
        scale=settings.typeset_scale,
        proportional=settings.typeset_family,
        roles=typeset.roles_from(
            (each.role, each.slot, each.step) for each in settings.typeset_roles
        ),
        panes=typeset.panes_from(settings.typeset_panes),
    )


def _switch_hide_ended(state: AppState, hide: bool) -> None:
    """
    Hide ended sub-agents from the fleet pane, or draw them all, persisting
    immediately as ``_switch_wrap`` does. Which ones are hidden is the fleet
    pane's to decide (``ui/fleet_view``); nothing leaves the store.
    """
    _apply_settings(state, state.settings.merged(hide_ended_subagents=hide))


def _save(state: AppState) -> None:
    """
    Persist ``state.settings``, and say so when that is refused.

    The only caller of ``settings.save``, so the reason a read left behind
    reaches every save. A refusal changes nothing for the run -- the theme the
    operator picked is on screen -- which is why it has to be said: the change
    is gone at the next start and nothing else would show it. Logged once, at
    the first refusal, and latched for the status bar; every later change in
    this run is refused for the same reason.
    """
    refused = settings_mod.save(state.settings, unread=state.board_log_unread)
    if refused is not None and state.unsaved is None:
        state.unsaved = refused
        LOG.error("settings", refused.told)


@dataclass(frozen=True, slots=True)
class FleetCounts:
    """
    What the status bar says of the fleet. ``text`` is the count; ``focusable``
    is whether Enter has a session to go to.
    """

    text: str
    focusable: bool


def fleet_counts(snap: Snapshot) -> FleetCounts:
    """
    How many agents ran in this window and are on the fleet, and beside it how
    many closed cards there are. Pure.

    A closed card is a session this process has not run, and "5 agents" over
    four of them reads as five things that might be working. A session the
    operator removed is in neither number. ``focusable`` is a root among the
    first: the cursor rests on nothing else by default (``FocusState.settle``),
    so with only closed cards Enter would open FOCUS on no session.
    """
    ran = inbox.ran_here(snap)
    closed = sum(
        1
        for node, rec in snap.nodes.items()
        if rec.recalled is not None and not snap.off_fleet(node[0])
    )
    text = f"{len(ran)} agents" + (f" · {closed} closed" if closed else "")
    return FleetCounts(text, any(rec.node_id[1] is None for rec in ran))


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def startup_shortfalls(
    migration: boardsync.Migration | None,
    fleet: fleetsync.Fleet | None,
    recall_failed: str | None = None,
    commands: CommandShortfalls | None = None,
) -> str | None:
    """
    Everything startup could not do, as one status-bar segment, or None when
    there is nothing. Pure. Counts and kinds; the log has each one's reason.

    From the board logs' move: logs left at the old path, the ones among them
    whose session has a log at both, and a legacy directory that could not be
    listed. From the recall: session files that are there and cannot be read, a
    sessions directory that was not read whole, and a listing of the CLI's
    transcripts that failed, which leaves every closed card unable to say
    whether it can be resumed. ``recall_failed`` is a recall that gave no
    answer at all: its worker raised, and no closed card was put back.
    ``commands`` is what reading sessions' command records back fell short by,
    at the recall and at each resume since.
    """
    parts: list[str] = []
    if migration is not None:
        twice = sum(1 for left in migration.left if left.twice)
        once = len(migration.left) - twice
        if once:
            parts.append(_count(once, "board log", "board logs") + " left at the old path")
        if twice:
            parts.append(_count(twice, "board log", "board logs") + " in two places")
        unlinked = sum(1 for moved in migration.moved if moved.unlinked is not None)
        if unlinked:
            parts.append(_count(unlinked, "moved board log", "moved board logs") + " not linked")
        if migration.unlisted is not None:
            parts.append("old board logs not listed")
    if fleet is not None:
        if fleet.unreadable:
            parts.append(
                _count(len(fleet.unreadable), "session file", "session files") + " unreadable"
            )
        if fleet.problem is not None:
            parts.append("session files not all read")
        if fleet.unlisted is not None:
            parts.append("transcripts not listed")
    if recall_failed is not None:
        parts.append("closed sessions not recalled")
    if commands is not None:
        if commands.unreadable:
            parts.append(
                _count(commands.unreadable, "command record", "command records") + " unreadable"
            )
        if commands.newer:
            parts.append(
                _count(commands.newer, "command record", "command records") + " from a newer build"
            )
        if commands.unlisted:
            parts.append("command records not all read")
    if not parts:
        return None
    return "at startup: " + ", ".join(parts) + " (see LOG)"


def _status_bar(state: AppState) -> None:
    snap = state.frame_snap
    if snap is None:
        return
    waiting = len(snap.needs_you)
    counted = fleet_counts(snap)
    active = snap.any_active

    imgui.text_disabled(counted.text)
    imgui.same_line()
    imgui.text_disabled("|")
    imgui.same_line()
    if waiting:
        # The one number worth colouring here: it is the only thing in this bar that
        # is waiting on the operator rather than reporting on the machine.
        #
        # It counts obligations, not approvals. Counting approvals is what let this
        # read "nothing awaiting review" while a session sat on an unanswered
        # question and another had crashed -- the bar was reporting on one of the
        # three ways an agent can be blocked and implying it had checked all three.
        oldest = format_elapsed(state.frame_now - min(o.since for o in snap.needs_you))
        imgui.text_colored(P.state_awaiting.vec4, f"{waiting} need you")
        imgui.same_line()
        imgui.text_disabled(f"(oldest {oldest})")
    else:
        imgui.text_colored(P.ok.vec4, "nothing needs you")
        if hello_imgui.current_layout_name() == TRIAGE and counted.focusable:
            # Suggest, never switch. Yanking the layout out from under someone
            # mid-diff is the failure mode a two-mode design has to avoid, and an
            # emptying queue is exactly when the operator is most likely to be
            # still reading the thing they just answered.
            imgui.same_line()
            imgui.text_disabled("- Enter to focus a session")
    imgui.same_line()
    imgui.text_disabled("|")
    imgui.same_line()
    imgui.text_disabled("running" if active else f"idle @ {state.settings.fps_idle:g}fps")
    if state.lost_reported:
        # Loud on purpose: this means an agent is stuck somewhere the operator
        # cannot reach it, and the whole point is that it has no other symptom.
        imgui.same_line()
        imgui.text_colored(
            P.danger.vec4,
            f"| {state.bridge.parked_count - len(snap.approvals)} blocked, not in queue",
        )
    if state.pool is not None:
        imgui.same_line()
        imgui.text_disabled("|")
        imgui.same_line()
        queued = state.pool.queued_count
        running, cap = state.pool.running_count, state.pool.cap
        text = f"{running}/{cap} sessions"
        if queued:
            text += f", {queued} queued"
        if running >= cap:
            # At cap a launch silently becomes a queue entry rather than a session.
            # Not a refusal, but the one reading of this counter the operator cannot
            # afford to skim, so it is the one reading that is not grey.
            imgui.text_colored(P.warn.vec4, text)
        else:
            imgui.text_disabled(text)
        # In the status bar because it is the one surface both layouts keep. HEALTH
        # names the session and carries the lever, but it is a FOCUS pane, so on its
        # own a widened gate would be invisible from the layout the operator works
        # the queue in.
        relaxed = _relaxed_count(state)
        if relaxed:
            imgui.same_line()
            imgui.text_colored(P.warn.vec4, f"| {relaxed} on a widened gate")
    if state.unsaved is not None:
        # The log line is said once and LOG is not a resident pane; this is what
        # is still there when the operator next looks.
        imgui.same_line()
        imgui.text_colored(P.warn.vec4, f"| settings not saved: {state.unsaved.why.value}")
    short = startup_shortfalls(
        state.migration,
        state.fleet,
        state.recall_failed,
        None if state.command_files is None else state.command_files.shortfalls,
    )
    if short is not None:
        # One segment for everything startup could not do, for the reason the
        # one above is here: each was logged once, and this is what stays.
        imgui.same_line()
        imgui.text_colored(P.warn.vec4, f"| {short}")


def _split(initial: str, new: str, direction: imgui.Dir, ratio: float) -> hello_imgui.DockingSplit:
    """
    Splits are declared parent-first: each carves a new named space off one that
    already exists, so a space has to be created before it can be split again.
    """
    s = hello_imgui.DockingSplit()
    s.initial_dock = initial
    s.new_dock = new
    s.direction = direction
    s.ratio = ratio
    return s


def _window(
    label: str, dock: str, fn: Callable[[], None], flags: int = 0
) -> hello_imgui.DockableWindow:
    w = hello_imgui.DockableWindow()
    w.label = label
    w.dock_space_name = dock
    w.gui_function = guarded(label, fn)
    w.imgui_window_flags = flags
    return w


SESSION_FLAGS = int(imgui.WindowFlags_.no_scrollbar)
"""
SESSION's own window flags. Its scrollback is a child that scrolls, sized to
leave the composer the rest, so the pane itself never has anything to scroll by
a bar. It can hold more than its room for the frame the composer grows in,
because the composer is measured a frame late (``_scrollback_height``); a bar
of the pane's own shown for that would narrow the scrollback, and every line of
the log would be set again, twice, as it came and went.
"""


def _closed_path(state: AppState, node: NodeId | None) -> str | None:
    """
    Where the CLI keeps the transcript of the closed session behind ``node``,
    for the transcript pane's line, or None. One dict lookup: the paths were
    worked out by the recall worker (``_recall_fleet``).
    """
    return None if node is None else state.closed_paths.get(node[0])


_SCROLLBACK_FLOOR = 80.0


def _scrollback_height(avail: float, composer_room: float) -> float:
    """
    How tall SESSION's scrollback is: the room the pane has, less what its composer
    took last frame, and never less than a few lines.

    Measured and not a constant, because the composer is as tall as what it says:
    two lines for a sub-agent, one for a session that ended, the reply box and its
    buttons for one that runs. A fixed reserve under the real height is what made
    the pane overflow and scroll. Last frame's height cannot move this one's: the
    composer's height does not depend on how tall the scrollback is, so the two do
    not chase each other.
    """
    return max(avail - composer_room, _SCROLLBACK_FLOOR)


def _panels(state: AppState) -> dict[str, Callable[[], None]]:
    """
    Every pane's draw function, built once and shared by both layouts.

    The same objects appear in TRIAGE and FOCUS. A pane that existed twice would be
    two panes that happened to look alike, and the scroll position, the half-typed
    reply and the expanded row would all reset on every mode switch.
    """

    def rail_pane() -> None:
        # frame_snap is set in pre_new_frame, which always runs before a panel
        # draws. Guarded anyway rather than asserted: a panel is not the place to
        # discover a callback-ordering change.
        if state.frame_snap is not None:
            rail.draw(
                state.frame_snap,
                state.focus,
                state.rail,
                state.frame_now,
                _card_wiring(state),
                hide_ended=state.settings.hide_ended_subagents,
                now_wall=state.frame_wall,
            )

    def inbox_pane() -> None:
        if state.frame_snap is None or state.pool is None:
            return
        inbox.draw(
            state.frame_snap,
            state.focus,
            state.review,
            state.compose,
            state.bridge,
            inbox.InboxActions(
                send=lambda node, text: _send(state, node, text),
                interrupt=lambda node: _interrupt(state, node),
                close=lambda node: _close(state, node),
                dismiss=lambda node: _dismiss(state, node),
                relaunch=lambda spec: _relaunch(state, spec),
            ),
            state.frame_now,
            wrap=state.settings.wrap_inputs,
            decisions=state.decisions,
            commands=_command_rows(state),
        )

    def context_pane() -> None:
        # Follows the cursor. Not independently selectable -- that second selection
        # is exactly what let an operator approve one agent's write while reading
        # another agent's transcript.
        if state.frame_snap is not None:
            node = state.focus.node(state.frame_snap)
            transcript_pane.draw(
                state.frame_snap,
                state.transcripts,
                node,
                _closed_path(state, node),
                start_in=transcript_pane.mode_named(state.settings.context_mode),
                show_reasoning=state.settings.show_reasoning,
            )

    def detail_pane() -> None:
        # Follows the cursor, exactly as CONTEXT does, and for the same reason: a
        # pane here that could be pointed somewhere else is the old DETAIL, and the
        # old DETAIL is how an operator approved one agent's write while reading
        # another agent's diff.
        if state.frame_snap is not None:
            detail.draw(
                state.frame_snap,
                state.focus,
                state.review,
                state.detail_pane_state,
                state.frame_now,
                state.decisions,
                _command_rows(state),
            )

    def board_pane_fn() -> None:
        # Follows the cursor, like DETAIL and CONTEXT. A board that held still while
        # the cursor moved would need a pointer of its own, and that second pointer
        # is what this layout deleted.
        if state.frame_snap is not None:
            board_pane.draw(
                state.frame_snap, state.focus, state.board, state.bridge, board_logging(state)
            )

    def digest_pane_fn() -> None:
        # Follows the cursor, as BOARD does: it is that session's board, read.
        if state.frame_snap is not None:
            digest_pane.draw(state.frame_snap, state.focus, state.frame_now)

    def brief_pane_fn() -> None:
        # Follows the cursor, like every other pane in this space. A brief belongs
        # to one session and there is no second cursor to point at another.
        if state.frame_snap is not None:
            brief_pane.draw(state.frame_snap, state.focus, state.brief)

    def session_pane() -> None:
        """FOCUS: the conversation, with its composer, in one pane."""
        if state.frame_snap is None:
            return
        node = state.focus.node(state.frame_snap)
        session = (node[0], None) if node else None
        height = _scrollback_height(imgui.get_content_region_avail().y, state.composer_room)
        if imgui.begin_child("##scrollback", imgui.ImVec2(0, height)):
            transcript_pane.draw(
                state.frame_snap,
                state.transcripts,
                node,
                _closed_path(state, node),
                start_in=transcript_pane.mode_named(state.settings.context_mode),
                show_reasoning=state.settings.show_reasoning,
            )
        imgui.end_child()
        below = imgui.get_content_region_avail().y
        imgui.separator()
        compose.draw_conversation(
            state.frame_snap,
            state.compose,
            session,
            send=lambda n, text: _send(state, n, text),
            interrupt=lambda n: _interrupt(state, n),
            close=lambda n: _close(state, n),
            wrap=state.settings.wrap_inputs,
        )
        state.composer_room = below - imgui.get_content_region_avail().y

    def health_pane() -> None:
        if state.frame_snap is None:
            return
        node = state.focus.node(state.frame_snap)
        health.draw(
            state.frame_snap,
            node,
            health.HealthActions(
                interrupt=lambda node: _interrupt(state, node),
                close=lambda node: _close(state, node),
                fork=lambda spec: _fork_of(state, node, spec),
                revoke_policy=lambda node: _revoke_policy(state, node),
            ),
            state.frame_now,
            _policy_of(state, node),
            now_wall=state.frame_wall,
        )

    return {
        "FLEET": rail_pane,
        "NEEDS YOU": _reviewing(state, "NEEDS YOU", inbox_pane),
        "CONTEXT": context_pane,
        "DETAIL": _reviewing(state, "DETAIL", detail_pane),
        "BOARD": board_pane_fn,
        "DIGEST": digest_pane_fn,
        "BRIEF": brief_pane_fn,
        "SESSION": session_pane,
        "HEALTH": health_pane,
        "LOG": _log_panel,
    }


def _draw_overlays(state: AppState) -> None:
    """
    Root-level drawing, after every docked window.

    The launcher lives here rather than in a panel for two reasons: a popup opened
    inside a docked window is scoped to that window, and the shortcut that opens it
    has to be evaluated inside a frame, which rules out ``begin_frame`` where the
    rest of the key handling lives. The settings dialog is here for both reasons
    too, and ahead of the pool check: it needs no pool.
    """
    launcher.handle_shortcut(state.launcher)
    # Not over another modal (``_form_up``) or the "Not done" notice: two modals
    # opened from the root replace each other. The dialog itself being up does not
    # block the chord, which closes it again.
    settings_dialog.handle_shortcut(
        state.settings_dialog,
        blocked=not state.settings_dialog.is_open and (_form_up(state) or bool(state.not_done)),
    )
    settings_dialog.draw(
        state.settings_dialog,
        state.settings,
        lambda change: _apply_settings(state, settings_dialog.changed(state.settings, change)),
    )
    # Ahead of the return for want of a pool: Details needs none.
    _draw_card_overlays(state)
    _draw_not_done(state)
    # Ahead of the draw, so a launch that failed is on screen the frame it is known,
    # and ahead of the return below, so one refused for want of a pool is still read.
    _reap_launches(state)
    pool = state.pool
    if pool is None:
        return
    # Started here rather than before the first frame: the probe is a network call and
    # the list in code is already drawable, so waiting on it would trade a usable
    # launcher for a slower splash. `request` is a no-op after the first one.
    state.catalog.request()
    state.catalog.poll()
    launcher.draw(
        state.launcher,
        running=pool.running_count,
        queued=pool.queued_count,
        cap=pool.cap,
        launch=lambda spec, draft: _launcher_pressed(state, spec, draft),
        wrap=state.settings.wrap_inputs,
        # The setting in force, which the modal both states and offers to override for
        # one launch. Without it the cap section has no number to name and the override
        # box has nothing to be an override of -- `launcher._cap_line` refuses to
        # substitute a plausible one, so this argument is what puts the figure on
        # screen at all.
        subagent_cap=state.settings.subagent_cap,
        models=state.catalog.models,
        # With the picked session's own run counted, so a pick that would end a
        # process the snapshot no longer calls live says so as the form does.
        live=(
            _resume_ends(state, state.launcher.picker.selected)
            if state.launcher.is_open
            else frozenset()
        ),
    )
    # Beside the launcher and under its rules: root level, and a press here
    # reaches ``_launch`` as a press there does. Asked for by a card and by
    # nothing drawn above (``_open_resume``).
    resume_form.draw(
        state.resume,
        launch=lambda pressed: _resume_pressed(state, pressed),
        wrap=state.settings.wrap_inputs,
        subagent_cap=state.settings.subagent_cap,
        models=state.catalog.models,
        live=_resume_ends(state, state.resume.showing) if state.resume.is_open else frozenset(),
    )


def _triage_layout(panels: dict[str, Callable[[], None]]) -> hello_imgui.DockingParams:
    """
    A: the queue is the application.

    The operator is a bottleneck by design, so the screen is the bottleneck's work
    surface. One centre pane merging approvals, questions and failures, and a rail
    that is scanned rather than browsed. Every *decision* is made at the row under
    the cursor, so no second pane can be pointed at a different agent than the one
    an ``a`` would approve -- DETAIL is a wider rendering of that same row, not a
    second selection. Dispatch is not here at all: it is Ctrl+N, from either layout,
    because intent belongs to a session rather than to an arrangement of the screen.
    """
    params = hello_imgui.DockingParams()
    params.layout_name = TRIAGE
    d = imgui.Dir
    params.docking_splits = [
        _split("MainDockSpace", "RailSpace", d.left, 0.21),
        # 0.40 starved the inbox: identity, wait and call summary all have to fit
        # between the rail and this pane, and the summary is what got cut.
        _split("MainDockSpace", "ContextSpace", d.right, 0.32),
    ]
    params.dockable_windows = [
        _window("FLEET", "RailSpace", panels["FLEET"]),
        _window("NEEDS YOU", "MainDockSpace", panels["NEEDS YOU"]),
        # First tab in the space, and deliberately in front of CONTEXT: the pane
        # answering "what exactly am I approving" outranks the one answering "what
        # was this agent doing beforehand", and the queue is what the operator is
        # here for. A tab-mate rather than a fourth split -- the 0.32 width was
        # already chosen so the inbox keeps room for identity, wait and summary.
        _window("DETAIL", "ContextSpace", panels["DETAIL"]),
        # Behind DETAIL as a starting position: the queue is what the operator is
        # here for, and a team's board is context for a decision rather than the
        # decision. Only a default -- layout_condition is first_use_ever, so an
        # operator who wants the board in its own split drags it there once and the
        # ini keeps it. Absent in the common case without any layout help -- most
        # sessions are solo, and the pane draws its own "not a team" line rather
        # than an empty table.
        _window("BOARD", "ContextSpace", panels["BOARD"]),
        # The tab after BOARD: a reading of that board, organised with it and not
        # in it. Declared in BOARD's space in both layouts, and ``main`` is what
        # puts it in BOARD's node for a layout saved before it existed.
        _window("DIGEST", "ContextSpace", panels["DIGEST"]),
        # Behind BOARD. A brief is written once and amended rarely, where the board
        # moves every few minutes -- the resident tab should be the one that changes.
        _window("BRIEF", "ContextSpace", panels["BRIEF"]),
        _window("CONTEXT", "ContextSpace", panels["CONTEXT"]),
        # Reachable, not resident. LOG is a debugging surface, and its old status as
        # tab-mate to the review queue -- in front of it, at that -- was precisely
        # the inversion this layout exists to correct.
        _window("LOG", "ContextSpace", panels["LOG"]),
    ]
    return params


def _focus_layout(panels: dict[str, Callable[[], None]]) -> hello_imgui.DockingParams:
    """
    B: the conversation is the application.

    The unit of work is a session and an approval is part of that conversation. The
    rail stays exactly where it is in TRIAGE, which is the shared anchor that keeps
    the mode switch from being disorienting.
    """
    params = hello_imgui.DockingParams()
    params.layout_name = FOCUS
    d = imgui.Dir
    params.docking_splits = [
        _split("MainDockSpace", "RailSpace", d.left, 0.21),
        _split("MainDockSpace", "HealthSpace", d.right, 0.26),
    ]
    params.dockable_windows = [
        _window("FLEET", "RailSpace", panels["FLEET"]),
        _window("SESSION", "MainDockSpace", panels["SESSION"], SESSION_FLAGS),
        _window("HEALTH", "HealthSpace", panels["HEALTH"]),
        # Behind HEALTH here, in front of CONTEXT in TRIAGE. The pane is the same
        # object either way; what differs is what the arrangement is for. FOCUS is
        # for steering one session, and its facts -- cost, cwd, context headroom --
        # are the ones worth resident space.
        _window("DETAIL", "HealthSpace", panels["DETAIL"]),
        # FOCUS is for steering one session, and a team's board is a fact about
        # that session in the same class as its cost and its context headroom --
        # which is why it sits with HEALTH here rather than with the queue.
        _window("BOARD", "HealthSpace", panels["BOARD"]),
        _window("DIGEST", "HealthSpace", panels["DIGEST"]),
        _window("BRIEF", "HealthSpace", panels["BRIEF"]),
        _window("LOG", "HealthSpace", panels["LOG"]),
    ]
    return params


def _keep_saved_layouts(ini: Path) -> None:
    """
    Give a layout saved before DIGEST existed an entry for it, in BOARD's node, so
    that hello_imgui does not rebuild the operator's arrangement from code on the
    first launch that declares the pane (``layout_ini``).

    Before the runner starts, because the runner reads the file once it does. A
    file that cannot be read or replaced is said in the log and the app starts:
    the cost is that rebuild, which is a layout to drag back and not a session lost.
    """
    try:
        done = layout_ini.keep_layout_for(ini, "DIGEST", "BOARD")
    except (OSError, UnicodeDecodeError) as exc:
        LOG.warn(
            "app",
            f"the saved layout in {ini} could not be given an entry for DIGEST"
            f" ({type(exc).__name__}: {exc}); the layouts may come up as the default ones",
        )
        return
    if done is not None:
        LOG.info("app", done)


def _log_panel() -> None:
    """Orchestrator diagnostics. Agent output belongs in that agent's transcript."""
    entries, _ = LOG.snapshot()
    for entry in entries[-400:]:
        colour = {
            "DEBUG": P.text_dim,
            "INFO": P.text_dim,
            "WARN": P.warn,
            "ERROR": P.danger,
        }[entry.level.name]
        imgui.text_colored(colour.vec4, f"[{entry.source}] {entry.text}")


# How much longer than the pool's own bound the draw thread waits for
# ``SessionPool.shutdown`` to return. The pool gives up by itself and says which
# run it gave up on; this covers the hop to the loop and back.
_SHUTDOWN_MARGIN_S = 5.0


def _shut_down(state: AppState) -> None:
    """
    End the sessions, then the loop they run on.

    The bridge is stopped whatever the pool's shutdown does. ``Bridge.stop`` is
    what releases the parked approvals and cancels whatever is left on the loop, so
    a shutdown that times out or raises is the case that needs it most.
    """
    try:
        _close_board_log(state)
        _close_fleet(state)
        _close_commands(state)
        if state.driver is not None:
            state.driver.stop()
        if state.pool is not None:
            # Cancel sessions before the loop dies, so no CLI subprocess outlives
            # the window that was supervising it.
            state.bridge.submit(state.pool.shutdown()).result(
                timeout=state.pool.shutdown_bound_s + _SHUTDOWN_MARGIN_S
            )
    finally:
        state.bridge.stop()


def _close_board_log(state: AppState) -> None:
    """
    Let the writer finish what is queued, for at most ``boardsync.CLOSE_TIMEOUT``,
    and log every record that did not reach its log.

    Ahead of the pool's shutdown and not behind it. The frame loop has returned,
    so no further fact will be derived and the queue is as long as it will get;
    waiting behind the pool would spend the writer's seconds on a session that
    will not end. The quit is therefore bounded by this wait plus the pool's.

    It raises nothing: the pool's shutdown and ``Bridge.stop`` come after it,
    and a failure to flush a log must not leave a CLI subprocess running.
    """
    writer = state.board_writer
    if writer is None:
        return
    try:
        reports = writer.close()
    except Exception as exc:  # noqa: BLE001 - the shutdown behind this must still run
        LOG.error("board", f"the board logs could not be closed: {type(exc).__name__}: {exc}")
        return
    lost: dict[str, list[str]] = {}
    for report in reports:
        if isinstance(report, boardsync.Outcome) and not report.kept:
            reason = (
                report.result.reason
                if isinstance(report.result, boardlog.Unwritten)
                else "written behind a break in the log"
            )
            lost.setdefault(report.session_id, []).append(reason)
    for session_id, reasons in lost.items():
        LOG.error(
            "board",
            f"{len(reasons)} board record(s) of {session_id} are not in its log at exit:"
            f" {reasons[0]}",
        )


def _close_fleet(state: AppState) -> None:
    """
    Say in each live session's file that pptmstr quit with it running, let the
    fleet writer finish for at most ``fleetsync.CLOSE_TIMEOUT``, and log every
    write that did not land.

    Beside the board log's close and ahead of the pool's shutdown, for that
    function's reasons: the frame loop has returned, so nothing further will be
    derived, and the wait must not be spent behind a session that will not end.
    The live sessions are the roots the store holds that have not ended. A
    session the pool goes on to end is not observed ending -- no frame applies
    it -- so this is the only end its file gets.

    It raises nothing, as ``_close_board_log`` does not.
    """
    writer = state.fleet_writer
    try:
        live = sorted(_live_sessions(state.store.snapshot()))
        for change in fleetsync.shutdown(live, time.time()):
            writer.submit(change)
        reports = writer.close()
    except Exception as exc:  # noqa: BLE001 - the shutdown behind this must still run
        LOG.error("fleet", f"the session files could not be closed: {type(exc).__name__}: {exc}")
        return
    for report in reports:
        if isinstance(report, fleetsync.Outcome) and not report.written:
            assert isinstance(report.result, sessionfile.Unwritten)
            LOG.error(
                "fleet",
                f"{report.session_id}: its session file was not written at exit"
                f" ({', '.join(sorted(report.keys))}): {report.result.reason}",
            )
    _read_fleet_reports(tuple(r for r in reports if isinstance(r, fleetsync.Latched)))


# How long the quit waits for command records still queued to reach the disk.
_COMMAND_FILES_CLOSE_S = 2.0


def _close_commands(state: AppState) -> None:
    """
    Kill every running command's process group and wait a bounded time for
    their ends (``Runner.close``), and log what could not be confirmed dead.

    **Quitting kills what is running; closing a session does not.** Each
    command ends ``Cancelled(SHUTDOWN)`` in its ``end.json``, written by the
    runner on that command's worker. The ``CommandEnded`` each one emits is
    applied by no frame, because none follows: the file is the record, and the
    next start reads it. A command that is only asked gets no end here and
    reads back as lapsed.

    The queued records go first, for at most ``_COMMAND_FILES_CLOSE_S``: a
    ``request.json`` still queued is what a run's record follows. Ahead of the
    pool's shutdown, for ``_close_board_log``'s reasons, and it raises nothing,
    as that does not. The quit is longer by at most the two bounds.
    """
    try:
        files = state.command_files
        if files is not None and not files.close(_COMMAND_FILES_CLOSE_S):
            LOG.error("command", "command records were still being written at exit")
        runner = state.command_runner
        if runner is None:
            return
        running = [
            c.id
            for c in state.store.snapshot().commands.values()
            if c.state is CommandState.RUNNING
        ]
        unconfirmed = runner.close(commandrun.CLOSE_TIMEOUT)
        if running:
            LOG.warn(
                "command",
                f"{len(running)} running command(s) were killed at exit"
                f" ({', '.join(running)}); how each ended is in its {commandfile.END_NAME}",
            )
        for left in unconfirmed:
            LOG.error(
                "command",
                f"{left.command_id} could not be confirmed dead at exit"
                f" (its shell's session {left.pid}): {left.reason}",
            )
        _record_unconfirmed(state, unconfirmed)
    except Exception as exc:  # noqa: BLE001 - the shutdown behind this must still run
        LOG.error("command", f"the commands could not be closed: {type(exc).__name__}: {exc}")


def _record_unconfirmed(state: AppState, unconfirmed: Iterable[commandrun.Unconfirmed]) -> None:
    """
    Put on disk, beside each command's own record, that pptmstr quit without
    seeing it end. The log this is also said to is in memory and on a standard
    output nobody may be reading, and both are gone with the window; the next
    start reads this (``commandfile.write_unconfirmed``).

    Only for a command whose end was not recorded. One that was killed and not
    seen to go has an ``end.json`` that says so itself.
    """
    files = state.command_files
    if files is None:
        return
    snap = state.store.snapshot()
    for left in unconfirmed:
        if left.reason != commandrun.NOT_RECORDED_IN_TIME:
            continue
        held = snap.commands.get(left.command_id)
        if held is None:
            continue
        written = commandfile.write_unconfirmed(
            held.session_id,
            held.id,
            f"pptmstr quit, sent its shell's session SIGTERM, and did not see it end:"
            f" {left.reason}",
            time.time(),
            left.pid,
            files.root,
        )
        if isinstance(written, commandfile.Unwritten):
            LOG.error("command", f"{held.id}: that was not recorded either: {written.reason}")


def _look(command: Command, root: Path | None) -> commandrun.Looked | None:
    """
    What is alive on this machine, now, of a run nobody recorded the end of.
    None when there is nothing to look for: the command is not such a run --
    one the operator released (``released.json``) is not, since that is read
    into its outcome -- no ``started.json`` names its process, or a
    ``gone.json`` says an earlier look saw that process gone. **Reads the disk
    and ``/proc``.**

    A process seen gone is not looked for again. Its pid is handed out to
    something else in time, and a record asked about at every start would in
    the end be asked about a process that only has its number.
    """
    end = command.end
    if end is None or end.outcome != EndUnknown():
        return None
    started = commandfile.read_started(command.session_id, command.id, root)
    if started is None:
        return None
    if commandfile.read_gone(command.session_id, command.id, root) is not None:
        return None
    return commandrun.survivor(started)


def _as_found(command: Command, found: commandrun.Looked, root: Path | None) -> Command:
    """
    ``command``, a run with no recorded end, as a look at the machine leaves
    it. **Writes ``gone.json``** for a process seen gone.

    A file never says a process is alive (``commandfile.read_command``); this
    is where the machine's answer is put on the record. Found -- with its
    shell, or by the tag its processes carry -- the command is one left
    running unsupervised: still ended, never runnable, and owed a row that
    says how to end it. Processes found in its session number with no shell
    and no tag are not known to be the command's: the record says what was
    found (``End.unconfirmed``) and names no survivor, so no row offers to
    kill them. Gone, and not looked for, both leave it a plain
    ``EndUnknown``, which claims nothing about what is alive.
    """
    end = command.end
    assert end is not None
    match found:
        case Survivor():
            outcome = EndUnknown(survivor=found)
            return dataclasses.replace(command, end=dataclasses.replace(end, outcome=outcome))
        case commandrun.Gone(why=why):
            written = commandfile.write_gone(command.session_id, command.id, why, time.time(), root)
            if (
                isinstance(written, commandfile.Unwritten)
                and written.reason != commandfile.ALREADY_RECORDED
            ):
                LOG.warn(
                    "command",
                    f"{command.id}: that its process is gone was not recorded, so it is"
                    f" looked for again at the next start: {written.reason}",
                )
            return command
        case commandrun.Unidentified():
            said = commandrun.unidentified_line(found)
            unconfirmed = said if end.unconfirmed is None else f"{end.unconfirmed}. Also: {said}"
            return dataclasses.replace(
                command, end=dataclasses.replace(end, unconfirmed=unconfirmed)
            )
        case commandrun.NotLooked():
            return command
        case _:
            assert_never(found)


STILL_THERE = "looked again: it is still there"
GONE = "looked again: it was not found, and how it ended is not known: "
# After a ``Gone`` of a record with no tag, which is found by its session alone.
GONE_UNTAGGED = ". A process that left that session with setsid is not something pptmstr can find"

UNIDENTIFIED = (
    "looked again: its shell is gone and what is in its session cannot be said to be this"
    " command, so there is no kill line; its record says what was found"
)
NOT_LOOKED = "could not look again, so this row is as it was: "
RECORD_UNREADABLE = "the record of its process can no longer be read"


def _check_survivor(state: AppState, command_id: CommandId) -> None:
    """
    The row's 'check again' for a command left running unsupervised: look for
    its session once more, off this thread, and put what was found on the
    queue.

    Found, the record is brought up to date. Gone, the command becomes a plain
    ``EndUnknown``: its row leaves the queue and its session may be given
    another command, and ``gone.json`` is written so that it is not looked for
    again. Found without its shell and without a process that carries its tag,
    it becomes an ``EndUnknown`` whose record says what is there and offers
    nothing to kill it with. **Not looked for --
    ``/proc`` could not be read -- nothing is put on the queue**: the row and
    the hold on its session stay as they were, and the line under the row says
    why. A look that fails is not a look that found nothing.

    No ``end.json`` is written by any of these: how the command ended is
    still not known.
    """
    snap = state.frame_snap
    held = None if snap is None else snap.commands.get(command_id)
    files = state.command_files
    if held is None or held.unsupervised is None or held.end is None:
        return
    root = None if files is None else files.root
    plain = dataclasses.replace(held, end=dataclasses.replace(held.end, outcome=EndUnknown()))

    def look() -> None:
        try:
            found = _look(plain, root) or commandrun.NotLooked(RECORD_UNREADABLE)
            if isinstance(found, commandrun.NotLooked):
                state.command_checks.put((command_id, NOT_LOOKED + found.reason))
                return
            now = _as_found(plain, found, root)
            assert now.end is not None
            state.bridge.emit(CommandEnded(command_id, now.end))
            state.command_checks.put((command_id, _looked_again(found)))
        except Exception as exc:  # noqa: BLE001 - a look that failed is said, not raised
            state.command_checks.put((command_id, f"{NOT_LOOKED}{type(exc).__name__}: {exc}"))

    threading.Thread(target=look, name=f"command-check-{command_id}", daemon=True).start()


def _release_survivor(state: AppState, command_id: CommandId) -> None:
    """
    The row's second press of 'release' on a command left running
    unsupervised: the operator lets go of the session it holds, and leaves
    what is running to run. **Nothing is signalled**, and nothing is looked
    for again.

    What is recorded is what the operator was shown: the count of the last
    look (``Released.alive``) and, in ``released.json``, the line that finds
    those processes. Not a fresh look -- a look now could find something
    other than what the operator decided on. The file is written before the
    store is told, off this thread, and a file that could not be written is
    logged and released all the same: the session is let go of in this
    window, and the next start raises the row again.
    """
    snap = state.frame_snap
    held = None if snap is None else snap.commands.get(command_id)
    left = None if held is None else held.unsupervised
    if held is None or left is None or held.end is None:
        return
    released = Released(at=time.time(), alive=left.alive)
    end = dataclasses.replace(held.end, outcome=EndUnknown(released=released))
    look = commandrun.look_line(left)
    files = state.command_files
    session_id = held.session_id

    def record() -> None:
        try:
            if files is not None:
                written = commandfile.write_released(
                    session_id, command_id, released, look, files.root
                )
                if isinstance(written, commandfile.Unwritten):
                    LOG.error(
                        "command",
                        f"{command_id}: its release was not recorded, so the next start shows"
                        f" it again: {written.reason}",
                    )
            LOG.warn(
                "command",
                f"{session_id}: {command_id} released by the operator with {left.alive}"
                f" process(es) still running, not signalled and no longer watched: {look}",
            )
        finally:
            state.bridge.emit(CommandEnded(command_id, end))

    threading.Thread(target=record, name=f"command-release-{command_id}", daemon=True).start()


def _looked_again(found: Survivor | commandrun.Gone | commandrun.Unidentified) -> str:
    """
    What the line under a row says a second look found.
    """
    match found:
        case Survivor():
            return STILL_THERE
        case commandrun.Gone(why=why):
            tagged = why.endswith(commandrun.NONE_TAGGED) or why == commandrun.ANOTHER_BOOT
            return GONE + why + ("" if tagged else GONE_UNTAGGED)
        case commandrun.Unidentified():
            return UNIDENTIFIED
        case _:
            assert_never(found)


def _read_survivor_checks(state: AppState) -> None:
    """Take what the looks that have answered found. On the draw thread."""
    while True:
        try:
            command_id, said = state.command_checks.get_nowait()
        except queue.Empty:
            return
        state.command_checked[command_id] = f"{said} ({time.strftime('%H:%M:%S')})"


def _log_look(session_id: str, command_id: CommandId, found: commandrun.Looked) -> None:
    """
    Say in the log what a start found of a run with no recorded end, where
    that is something the operator has to act on or was not told.
    """
    match found:
        case Survivor():
            outside = f", and {len(found.escaped)} outside it" if found.escaped else ""
            what = (
                f"its shell's session {found.pid}, {found.members} process(es){outside}"
                if found.shell
                else f"its shell (pid {found.pid}) is gone and {found.alive} process(es)"
                f" started with its {COMMAND_TAG} are alive"
            )
            LOG.error(
                "command",
                f"{session_id}: {command_id} is STILL RUNNING with nothing holding it:"
                f" {what}. To end it: {commandrun.kill_line(found)}",
            )
        case commandrun.Unidentified():
            LOG.error(
                "command", f"{session_id}: {command_id}: {commandrun.unidentified_line(found)}"
            )
        case commandrun.NotLooked(reason=reason):
            # Where nothing can ever be looked for, saying so of every record at
            # every start says nothing.
            if reason not in (commandrun.NO_PROC, commandrun.NOT_ON_RECORD):
                LOG.warn(
                    "command",
                    f"{session_id}: {command_id} has no recorded end and could not be looked"
                    f" for, so whether it is still running is not known: {reason}",
                )
        case commandrun.Gone():
            pass
        case _:
            assert_never(found)


def _restore_commands(state: AppState, session_id: str) -> None:
    """
    Read one session's command records back and put what they hold on the
    queue (``intents.CommandsRestored``). **Blocks on the disk**: called on the
    recall worker, and on a worker thread from a resume.

    Once per session per process (``CommandFiles.claim_restore``): after the
    first read this window holds everything the files do, and the store skips
    a command it already has.

    Nothing comes back open -- ``commandfile.restore`` yields only ended
    commands, and the store refuses a batch that holds any other. A run with
    no recorded end is looked for on this machine (``_look``), and one found
    alive comes back ended all the same, as a command left running
    unsupervised. What could not be read is logged here, once, and counted for
    the status bar.

    **A command this window already holds is not looked for.** A session
    resumed in the window that is running its command has that command on disk
    as a run with no end, and its process alive: found, it would be reported
    as running with nothing holding it while this window's runner holds it.
    The store skips such a record, and so does the look.
    """
    files = state.command_files
    if files is None or not files.claim_restore(session_id):
        return
    restored = commandfile.restore(session_id, files.root)
    if restored.commands:
        held = state.store.snapshot().commands
        looked = []
        for command in restored.commands:
            found = None if command.id in held else _look(command, files.root)
            if found is None:
                looked.append(command)
                continue
            looked.append(_as_found(command, found, files.root))
            _log_look(session_id, command.id, found)
        state.bridge.emit(CommandsRestored(session_id, tuple(looked)))
    for command_id, reason in restored.unreadable:
        LOG.warn(
            "command",
            f"{session_id}: the record of {command_id} cannot be read, so it is not shown"
            f" and its files are left as they are: {reason}",
        )
    for command_id in restored.newer:
        LOG.warn(
            "command",
            f"{session_id}: {command_id} has a file written by a newer build; it is read"
            " from the keys this one knows",
        )
    if restored.problem is not None:
        LOG.warn("command", f"{session_id}: its commands were not all read: {restored.problem}")
    if restored.unreadable or restored.newer or restored.problem is not None:
        files.fell_short(
            len(restored.unreadable), len(restored.newer), int(restored.problem is not None)
        )


# What lists the sessions the CLI can resume, for the recall: every project's
# transcripts. ``fleetsync.recall``'s own default, named so ``_start`` can be
# handed another.
Lister = Callable[[], Iterable[SessionRow]]


# Where the README says what to do about a board log left at the old path, by
# the heading it is under. A test holds this to a heading the README has.
README_BOARD_LOG_HEADING = "The board is written to disk, and nothing deletes it"
_README_BOARD_LOG = f"README, '{README_BOARD_LOG_HEADING}'"


def _migrate_board_logs() -> boardsync.Migration:
    """
    Carry the board logs kept under the old path to their sessions' own
    directories (``boardsync.migrate``), and say what was not carried.

    **Synchronous, once, before the window opens and before any writer exists**:
    a log read or appended to while this ran could be reached by either name.
    One listing and a rename per entry, and unbounded on a mount that hangs --
    so the line before the call is what a start that never finishes is
    attributed to.
    """
    LOG.info(
        "board",
        f"moving board logs from {boardsync.legacy_root()} to {sessionfile.root()}"
        " (one rename each; a start that stops here is waiting on that directory)",
    )
    migration = boardsync.migrate()
    if migration.unlisted is not None:
        LOG.error(
            "board",
            f"{boardsync.legacy_root()} could not be listed, so no board log was moved"
            f" ({migration.unlisted}); a session resumed from one there runs unrecorded",
        )
    for moved in migration.moved:
        if moved.unlinked is not None:
            LOG.warn(
                "board",
                f"{moved.session_id}: its board log was moved to {moved.target}, and no"
                f" link could be left at {moved.source} ({moved.unlinked}); another pptmstr"
                " appending by the old name starts a second history there",
            )
    for left in migration.left:
        LOG.warn(
            "board",
            f"{left.name}: its board log was left at the old path {left.source}:"
            f" {left.reason}."
            + (
                " It has records in two places and runs unrecorded when resumed, until one"
                f" history is left ({_README_BOARD_LOG})"
                if left.twice
                else " It is read from there and runs unrecorded when resumed, until that"
                f" directory is moved ({_README_BOARD_LOG})"
            ),
        )
    if migration.moved:
        LOG.info("board", f"moved {len(migration.moved)} board log(s)")
    return migration


def _recall_fleet(state: AppState, list_sessions: Lister | None) -> None:
    """
    Read the session files and put each session that is on the fleet back as a
    closed card. **The body of the recall worker**: ``fleetsync.recall`` reads a
    file per session and then lists every transcript the CLI holds.

    Each card is an intent through the bridge, so the store builds it on the
    draw thread like any other record, in the order the files gave: most
    recently active first. The fleet writer is told of the session before the
    card is emitted, since removing the card is a change to that file.

    Each recalled session's command records are read back behind its card
    (``_restore_commands``), every one of them ended.

    **A card is all this makes of a session.** Nothing is handed to the pool, no board log is
    read and nothing is added to ``board_logs``: a session with an entry there
    would have its log skipped when it is resumed (``_launch``).

    Where the CLI's naming puts each transcript is worked out here, because the
    naming resolves the directory, and assigned whole before the first card:
    the pane that draws it looks a path up and computes none. Not for a session
    the CLI no longer lists, whose card says the transcript is gone.

    Everything that fell short is logged once, here, and ``state.fleet`` is what
    the status bar goes on saying it from.
    """
    try:
        root = state.fleet_writer.root
        fleet = (
            fleetsync.recall(root)
            if list_sessions is None
            else fleetsync.recall(root, list_sessions)
        )
        paths: dict[str, str] = {}
        for card in fleet.sessions:
            if card.cwd and card.recalled.transcript is not TranscriptPresence.GONE:
                try:
                    where = session_details.transcript_for(card.cwd, card.session_id)
                    paths[card.session_id] = str(where)
                except Exception:  # noqa: BLE001 - the card is worth more than its path
                    continue
        state.closed_paths = paths
        for card in fleet.sessions:
            state.fleet_writer.admit(card.session_id)
            state.bridge.emit(card)
        _say_recall(fleet)
        # After the cards and before the answer is published: a reader that
        # waits for ``fleet`` finds every restored command already queued.
        for card in fleet.sessions:
            try:
                _restore_commands(state, card.session_id)
            except Exception as exc:  # noqa: BLE001 - the cards are worth more than this
                LOG.error(
                    "command",
                    f"{card.session_id}: its commands could not be read back:"
                    f" {type(exc).__name__}: {exc}",
                )
        state.fleet = fleet
    except Exception as exc:  # noqa: BLE001 - a worker's exception is otherwise never seen
        reason = f"{type(exc).__name__}: {exc}"
        LOG.error("fleet", f"the fleet could not be recalled: {reason}")
        LOG.debug("fleet", traceback.format_exc())
        # Kept for the status bar: the log line is said once, and ``fleet``
        # staying None is also what a recall still on its way looks like.
        state.recall_failed = reason


def _say_recall(fleet: fleetsync.Fleet) -> None:
    """
    One line for what was recalled and one for each way the recall fell short.
    """
    gone = sum(1 for s in fleet.sessions if s.recalled.transcript is TranscriptPresence.GONE)
    LOG.info(
        "fleet",
        f"recalled {len(fleet.sessions)} closed session(s)"
        + (f", {gone} with no transcript left" if gone else "")
        + (f", {len(fleet.killed)} that never recorded an end" if fleet.killed else "")
        + (f"; {len(fleet.detached)} removed from the fleet earlier" if fleet.detached else ""),
    )
    for session_id, reason in fleet.unreadable:
        LOG.warn(
            "fleet",
            f"{session_id}: its session file cannot be read, so it has no card and the"
            f" file is left as it is: {reason}",
        )
    if fleet.problem is not None:
        LOG.warn("fleet", f"the session files were not all read: {fleet.problem}")
    if fleet.unlisted is not None:
        LOG.warn(
            "fleet",
            "the CLI's transcripts could not be listed, so no closed card can say whether"
            f" its session can still be resumed: {fleet.unlisted}",
        )


def _start(
    args: argparse.Namespace,
    found: settings_mod.Loaded,
    *,
    list_sessions: Lister | None = None,
) -> AppState:
    """
    The state a process begins with: what the settings file said, the command
    line over it, a board writer only where a board is to be recorded, and the
    closed fleet on its way.

    ``found.board_log_unread`` is kept beside the settings and not folded into
    them. ``board_log`` is already off wherever it is set, so no writer is built;
    the reason is what tells the pane this was not the operator's choice and
    tells ``_save`` not to write it back.

    **The order is the content.**

    1. The board logs are moved, where a board is to be recorded, before any
       writer exists and before anything can be resumed (``_migrate_board_logs``).
    2. The state and its writers are built. Neither writer touches the disk
       until it is handed something.
    3. The recall worker is started, last: it emits into a bridge that exists
       and admits to a writer that exists. Its cards arrive on whichever frame
       follows its read; a launch that beats one wins, since a recall replaces
       no record (``intents.SessionRecalled``).

    Under the fake driver none of the three touches the disk: no move, no board
    writer, no recall, and a fleet writer that is told of no session. Nor is
    there a command runner or a writer of command records: a command approved
    there is ended at once as never started.

    ``list_sessions`` is the lister the recall asks; None is every transcript
    the CLI holds, which is the operator's real ``~/.claude/projects``.
    """
    loaded = found.settings
    if args.theme:
        loaded = loaded.merged(theme=args.theme)
    if args.fps_idle is not None:
        loaded = loaded.merged(fps_idle=args.fps_idle)
    if args.cap is not None:
        loaded = loaded.merged(concurrency_cap=args.cap)

    recorded = loaded.board_log and not args.fake
    migration = _migrate_board_logs() if recorded else None

    state = AppState(
        store=Store(),
        bridge=Bridge(),
        settings=loaded,
        board_log_unread=found.board_log_unread,
        fake=bool(args.fake),
        migration=migration,
    )
    state.pool = SessionPool(state.bridge, cap=state.settings.concurrency_cap)
    if recorded:
        # Builds nothing on disk: a session's directory is made by its first record.
        state.board_writer = boardsync.BoardWriter()
    state.pending_layout = args.layout
    state.command_host = _command_host()
    if state.command_host.no_shell is not None:
        LOG.warn(
            "command",
            f"no command an agent asks for can be run from here: {state.command_host.no_shell}",
        )
    if not args.fake:
        # Neither touches the disk or starts a thread until it is handed
        # something. On the fleet writer's root, which is where a session's
        # other files are.
        state.command_files = CommandFiles(state.fleet_writer.root)
        state.command_runner = commandrun.Runner(
            _command_ended(state), root=state.fleet_writer.root
        )
        # A daemon, as the writers' workers are: a listing held by a dead mount
        # must not hold the exit.
        threading.Thread(
            target=_recall_fleet, args=(state, list_sessions), name="fleet-recall", daemon=True
        ).start()
    return state


# The signals that end pptmstr as closing its window does. Without a handler
# ``SIGTERM`` and ``SIGHUP`` kill the process where it stands: no ``finally``, so
# nothing closes the runner, and a running command is left with no timeout and
# no window. ``SIGINT`` without one is Python's ``KeyboardInterrupt``, raised in
# whichever frame callback runs next and thrown through the window loop's C++.
EXIT_SIGNALS = (signal.SIGTERM, signal.SIGHUP, signal.SIGINT)


def exit_on_signals(request_exit: Callable[[], None]) -> Callable[[], None]:
    """
    Have each of ``EXIT_SIGNALS`` ask the window loop to end, so that the
    ``finally`` in ``main`` runs and ``_shut_down`` kills what is running.
    Returns what ``main`` calls once the loop has returned.

    The handler takes no lock: Python runs it on the main thread between two
    bytecodes, which may be inside anything. The first thing it does is put the
    default action back for **every** one of the signals, so that the second
    signal, whichever it is, kills a shutdown that has hung -- a terminal that
    closes sends ``SIGHUP`` and a session manager follows with ``SIGTERM``.
    For ``SIGINT`` that is the kernel's default and not Python's.

    After the loop has returned the handler asks for nothing:
    ``request_exit`` belongs to a loop that no longer exists, and the shutdown
    the signal wants is already under way. The first signal then only arms the
    second.

    **What this does not cover.** ``SIGKILL`` and the kernel's out-of-memory
    killer cannot be handled, and a crash in native code runs nothing. After
    those a command is still running; its record names its shell's session,
    and the next start finds it (``_look``).
    """
    loop_over = False

    def handler(signum: int, frame: object) -> None:
        for each in EXIT_SIGNALS:
            signal.signal(each, signal.SIG_DFL)
        if not loop_over:
            request_exit()

    def the_loop_returned() -> None:
        nonlocal loop_over
        loop_over = True

    for signum in EXIT_SIGNALS:
        signal.signal(signum, handler)
    return the_loop_returned


def _ask_window_to_close() -> None:
    hello_imgui.get_runner_params().app_shall_exit = True


def _text_overrides(
    args: argparse.Namespace, loaded: settings_mod.Settings
) -> settings_mod.Settings:
    """
    The command line's say over how text is set, for this run and not saved, as
    ``_start`` gives it the say over the theme, the idle rate and the cap.

    Apart from ``_start`` because only ``main``'s parser declares these four: a
    caller of ``_start`` that builds its own command line names none of them.
    """
    if args.typeset is not None:
        loaded = loaded.merged(typeset=args.typeset == "on")
    if args.prose is not None:
        loaded = loaded.merged(
            **settings_mod.legacy_faces("mono" if args.prose == "mono" else "roboto")
        )
    if args.faces is not None:
        loaded = loaded.merged(**settings_mod.legacy_faces(args.faces))
    if args.text_scale is not None:
        loaded = loaded.merged(typeset_scale=args.text_scale)
    return loaded


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="pptmstr - multi-agent orchestrator")
    # Answered by `pptmstr.__main__` before this module is imported; declared
    # here so that --help lists it and a caller of this function gets the same.
    ap.add_argument(
        "--version",
        action="store_true",
        help="print what is running and what the tree pins, open no window, and exit",
    )
    ap.add_argument("--theme", default=None, help="override the persisted theme")
    ap.add_argument("--fake", action="store_true", help="run the fake driver (no SDK, no cost)")
    ap.add_argument(
        "--task",
        action="append",
        default=None,
        help="run a real agent on this task; repeat for concurrent sessions",
    )
    ap.add_argument("--model", default=None, help="model for --task")
    ap.add_argument("--cap", type=int, default=None, help="max concurrent sessions")
    ap.add_argument("--cwd", default=".", help="working directory for --task sessions")
    ap.add_argument(
        "--template",
        default="solo",
        choices=templates.names(),
        help="team shape for --task sessions",
    )
    ap.add_argument("--fps-idle", type=float, default=None)
    # Like --theme: overrides for this run, not saved. A screenshot run has a
    # throwaway HOME and reads nobody's settings, so comparing the two renderers
    # from the command line needs a way to say which one.
    ap.add_argument(
        "--typeset",
        choices=("on", "off"),
        default=None,
        help="set text through the layout library, or with the renderers before it",
    )
    ap.add_argument(
        "--prose",
        choices=("proportional", "mono"),
        default=None,
        help="typeset prose proportional, or every role monospace (--faces mono)",
    )
    ap.add_argument(
        "--faces",
        choices=tuple(settings_mod.LEGACY_FACES),
        default=None,
        help="a named set of role choices, as a saved typeset_faces is read; wins over --prose",
    )
    ap.add_argument(
        "--text-scale",
        type=float,
        default=None,
        help="typeset text's size as a multiple of the UI font's",
    )
    ap.add_argument(
        "--layout",
        default=None,
        choices=(TRIAGE, FOCUS),
        help=f"start in {TRIAGE} (the queue) or {FOCUS} (one conversation)",
    )
    args = ap.parse_args(argv)
    if args.version:
        return version_mod.main()

    state = _start(args, settings_mod.read())
    state.settings = _text_overrides(args, state.settings)
    theme.set_theme(state.settings.theme)
    _configure_typeset(state.settings)

    runner = hello_imgui.RunnerParams()
    runner.app_window_params.window_title = "pptmstr"
    runner.app_window_params.window_geometry.size = (1500, 900)
    runner.app_window_params.restore_previous_geometry = True
    runner.imgui_window_params.show_menu_bar = True
    runner.imgui_window_params.show_menu_app = False
    # Our own View, drawn by ``_menus``: hello_imgui's is drawn first whatever is
    # asked, and its labels and items are not ours to choose (``main_menu``).
    runner.imgui_window_params.show_menu_view = False
    runner.imgui_window_params.show_menu_view_themes = False
    runner.imgui_window_params.show_status_bar = True
    runner.imgui_window_params.show_status_fps = False
    runner.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.provide_full_screen_dock_space
    )
    runner.imgui_window_params.background_color = P.bg.vec4
    panels = _panels(state)
    runner.docking_params = _triage_layout(panels)
    runner.alternative_docking_layouts = [_focus_layout(panels)]
    runner.remember_selected_alternative_layout = True
    state.runner = runner
    runner.ini_folder_type = hello_imgui.IniFolderType.app_user_config_folder
    # None is a runner that keeps no ini, and then there is no saved layout to keep.
    ini = hello_imgui.ini_settings_location(runner)
    if ini is not None:
        _keep_saved_layouts(Path(ini))

    runner.fps_idling.enable_idling = True
    runner.fps_idling.fps_idle = state.settings.fps_idle

    def post_init() -> None:
        # Recover from an unbalanced ImGui stack instead of aborting the process.
        # This is what makes `guarded` safe: a panel that raises mid-draw leaves a
        # child or table open, and the default behaviour is to assert and die.
        io = imgui.get_io()
        io.config_error_recovery = True
        io.config_error_recovery_enable_assert = False
        io.config_error_recovery_enable_debug_log = True
        io.config_error_recovery_enable_tooltip = False
        LOG.info("app", f"theme {state.settings.theme}")

    def pre_frame() -> None:
        begin_frame(state)
        # Full speed while anything is genuinely working; idle while everything is
        # done or parked on the operator. AWAITING_APPROVAL counts as idle, which is
        # I8 showing up as a CPU number rather than a claim.
        snap = state.frame_snap
        working = snap is not None and snap.any_active
        runner.fps_idling.enable_idling = not working

    runner.callbacks.load_additional_fonts = theme.load_fonts
    runner.callbacks.post_init = post_init
    # Guarded too: an exception here would stop intents being applied, which is
    # bad, but killing the window loses every session, which is worse.
    runner.callbacks.pre_new_frame = guarded("frame", pre_frame)
    runner.callbacks.show_menus = guarded("menu", lambda: _menus(state))
    runner.callbacks.show_status = guarded("status bar", lambda: _status_bar(state))
    runner.callbacks.post_render_dockable_windows = guarded(
        "overlays", lambda: _draw_overlays(state)
    )
    # The text library's context goes before the ImGui context its fonts are on.
    runner.callbacks.before_exit = typeset.unload

    state.bridge.start()
    # On the loop, not on the frame loop: the frame loop is one of the things being
    # watched, and a watchdog dies with the thread it runs on. See `watch`.
    state.bridge.submit(watch(state))
    if args.fake:
        state.driver = FakeDriver(state.bridge)
        state.bridge.submit(state.driver.run())
    if args.task:

        _cli_cwd = os.path.realpath(args.cwd)

        async def launch_initial() -> None:
            # Submitting from the loop thread keeps every mutation of the pool on
            # one thread, so it needs no lock of its own.
            for task_text in args.task:
                _launch(
                    state,
                    LaunchSpec(
                        task=task_text,
                        model=args.model or model_catalog.DEFAULT_MODEL,
                        # Resolved for the same reason `LauncherState.spec` resolves
                        # it: a relative cwd makes every absolute write unplaced and
                        # the divergence reading silently empty. `--cwd` defaults to
                        # ".", so the headless path is the one most likely to hit it.
                        cwd=_cli_cwd,
                        session_base=tree.session_base(_cli_cwd),
                        template=args.template,
                    ),
                    origin="command-line task",
                )

        state.bridge.submit(launch_initial())
        LOG.info("app", f"{len(args.task)} task(s), cap {state.settings.concurrency_cap}")

    the_loop_returned = exit_on_signals(_ask_window_to_close)
    try:
        immapp.run(runner)
    finally:
        the_loop_returned()
        _shut_down(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
