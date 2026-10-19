"""
The shell around ``sessionfile``: what a reducer step means for a session's file,
the one thread that writes those files, and the read of the fleet at startup.

**Three parts, and only the middle one has state.**

- ``changes`` is pure: the snapshot before a step, the intent, the snapshot after
  and a wall-clock instant give the keys of ``session.json`` that step implies.
  ``launched``, ``sent``, ``gate_changed`` and ``shutdown`` are the same kind of
  value for the four things the store does not hold: what a launch was started
  with, what the operator typed, a live change of gate, and the application
  quitting.
- ``FleetWriter`` takes those values from any thread and writes them on its own.
- ``recall`` reads every session file and one listing of the CLI's transcripts,
  and blocks for both. It is for a worker at startup.

**What a file says is what the store held when the change was derived.** A
session's end, its return to life and its place on the fleet are derived from the
step that made them true and from nothing else, in the order the store applied
them: the observer runs on one thread and ``FleetWriter.submit`` merges under one
lock, so a later derivation always lands over an earlier one. The launch write
comes from another thread and may be merged on either side of the previous run's
end. It is not what clears an end or puts a session back on the fleet for good:
the root's announce is, and ``changes`` derives both from it, after every step of
the run it replaced.

**A session gets a file only when the shell admitted it and a run of it got as far
as the CLI.** ``FleetWriter.admit`` is the shell saying "this is a real session I
am launching, or one whose file I read": a change for any other id is dropped
without a word, which is every session of the scripted driver and every id a
stray intent names. Admission alone creates nothing. A file that does not exist
yet is created only by a change with ``Change.started`` set, which ``changes``
derives when a root leaves SPAWNING for a live state or comes back from an end --
the driver emits either only with a connected client, after the refusals that
precede one. Until then the
session's changes are held in memory, merged, and a launch that is refused or
never connects leaves nothing on disk. The CLI writes its transcript on its own
schedule, so a pptmstr killed in the moment between the first send and the CLI's
first write leaves a file whose transcript ``recall`` reports as gone.

**"Last active"** is the wall time of a root's most recent announce, turn end
(entering AWAITING_INPUT or SUPERVISING), end, or operator send. The store keeps
when a state began, not when a session last did something, so it is derived here
from the transitions, and ``FleetWriter.last_active`` answers for a session this
process has seen one of. It never decreases within a process, whatever the wall
clock does.

**Cost is the file's figure at the start of a run plus what that run has spent.**
A resumed session's record starts again at zero, so the record's total is one
run's and writing it would lower the file's. ``Change.spent`` carries the run's
total with the run it belongs to, and the worker adds it to what the file held
when it first saw that run, and never writes less than the file holds. Written at
a turn end and at an end, not per message: a killed session's file lacks the turn
it was in.

**That sum is right only if a resumed CLI process reports its cost from zero.**
A run's spend is the driver's reading of ``ResultMessage.total_cost_usd``, which
the SDK (0.2.164) documents as a running total over a connection and passes
through from the CLI unparsed; whether a process started with ``--resume`` begins
that total at zero or at what the conversation had already cost is not something
the SDK's source says, and it has not been measured. If it is the second, every
resume adds the session's earlier cost to the file again. So the figure is shown
as an estimate that may double-count across resumes, and is added to nothing.

**An over-long message clears the stored one.** ``sessionfile.update`` skips a
``last_message`` over ``sessionfile.MAX_LAST_MESSAGE`` and keeps the previous
text, which a resume form would then offer as the last thing sent. So such a
message is written as null: the box opens empty. A cut copy is not stored,
because the box it prefills is one press from sending and what it held would not
be what was sent.

**Failures.** ``sessionfile.update`` never raises and says why it wrote nothing.
A file that is unreadable, or that a newer build wrote, is somebody else's text:
the session is latched, reported once as ``Latched``, and not written again by
this writer. Any other failure -- a full disk, a directory that cannot be
entered -- is of the moment, and the session's next change is tried as usual.
What such a failure drops is reported as an ``Outcome``; the one key carried
over is ``in_fleet``, which is tried again whenever the worker next does anything
and once more at ``close``, because it is derived only at the step that changes
it and decides what the next start shows. A session whose file could not be
created keeps everything it is owed and tries again at its next change.

**Two windows.** Each has its own writer and its own lock. Writes to one file are
last-writer-wins per key (``sessionfile``), a cost written by one window is kept
by the other only as a floor, and nothing here guards a session two windows run.

The worker is a daemon thread for the reason ``boardsync``'s is: a write held by
a dead mount must not hold the exit, and a write ended midway tears nothing.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, assert_never

from . import sessionfile
from .approval import Policy
from .intents import (
    AgentFinished,
    AgentRemoved,
    AgentResumed,
    AgentSpawned,
    ApprovalRequested,
    ApprovalResolved,
    BoardRead,
    BoardRestored,
    CommandApproved,
    CommandEnded,
    CommandRefused,
    CommandRequested,
    CommandsRead,
    CommandsRestored,
    CommandStopAsked,
    CommandWithdrawn,
    CompactionObserved,
    ConcernEdited,
    ConcernPosted,
    ConcernWithdrawn,
    ContextPolled,
    DecisionAnswered,
    DecisionDismissed,
    DecisionRaised,
    DecisionsRead,
    DecisionWithdrawn,
    DigestRead,
    FailedSessionClosed,
    FailureAcknowledged,
    InboxRead,
    Intent,
    SessionDetached,
    SessionRecalled,
    StateChanged,
    SubagentDelivered,
    SubagentProgress,
    TaskAmended,
    TaskClaimRequested,
    TaskClosed,
    TaskCompleted,
    TaskDeclared,
    TaskReleased,
    TaskVersionHanded,
    TaskWithdrawn,
    TopicChanged,
    UsageAccrued,
)
from .model import (
    AgentRecord,
    AgentState,
    EndedHow,
    LaunchSpec,
    Recalled,
    Snapshot,
    TranscriptPresence,
)

if TYPE_CHECKING:
    from .sessions import SessionRow

# How long ``close`` waits for queued writes unless told otherwise. A write is
# milliseconds, so this is a bound on a wedged filesystem and not a budget.
CLOSE_TIMEOUT = 5.0

# The most of a failure's text a file is given. It is an exception's message and
# has no bound of its own, and a file over ``sessionfile.MAX_FILE_BYTES`` is not
# written at all.
MAX_ERROR_CHARS = 2_000

_TURN_OVER = frozenset({AgentState.AWAITING_INPUT, AgentState.SUPERVISING})


@dataclass(frozen=True, slots=True)
class Spent:
    """
    What one run of a session has cost so far, and which run.

    ``run`` is the root record's ``started_at``: a new announce builds a new
    record with a later one, so it tells a resumed session's zero from a fall.
    ``usd`` is that record's total, which only grows.
    """

    run: float
    usd: float


@dataclass(frozen=True, slots=True)
class Change:
    """
    Keys of one session's file to set, as one value.

    ``keys`` are ``sessionfile`` keys and are set whatever the file holds.
    ``once`` are set only where the file has no value for the key. ``spent`` is
    not a key: the worker turns it into ``cost_usd`` against what the file held
    when the run began. ``started`` says a run of this session reached the CLI,
    which is what lets a file that does not exist be created.
    """

    session_id: str
    keys: Mapping[str, object] = field(default_factory=dict)
    once: Mapping[str, object] = field(default_factory=dict)
    spent: Spent | None = None
    started: bool = False


# -- what a reducer step implies ---------------------------------------------------


def changes(
    before: Snapshot, intent: Intent, after: Snapshot, now_wall: float
) -> tuple[Change, ...]:
    """
    The session-file changes ``intent`` implies, having taken ``before`` to
    ``after``. Pure; ``now_wall`` is wall-clock seconds, read by the caller.

    Two things are derived, each from the snapshots and not from the intent's
    own fields:

    - **Whether a session is on the fleet**, for every session whose answer to
      ``Snapshot.off_fleet`` the step changed. That is a removal, and it is also
      every way back: the reducer puts a session back on the fleet in a step over
      the whole snapshot, so no list of intents kept here could be the list of
      ways it happens.
    - **What a root's run did**, for the root the intent names: announced, turn
      over, ended, alive again. Only for a record a run in this process stands
      behind -- nothing is derived about a recalled record's run, nor about a
      record this process did not see announced or change state.

    The ``match`` exists to be closed. An intent that can move a root's state is
    in the first arm; the ones that cannot are named, and a new member of
    ``Intent`` is a type error at the default until it is put in one.

    ``SessionRecalled`` derives nothing at all. The step it makes -- no record,
    then a root in DONE -- is an end as far as two snapshots can tell, and
    reading it as one would rewrite every file at every start with an end this
    process never saw.
    """
    ran: Change | None
    match intent:
        case SessionRecalled():
            return ()

        case (
            AgentSpawned()
            | StateChanged()
            | AgentFinished()
            | AgentResumed()
            | ApprovalRequested()
            | ApprovalResolved()
            | SubagentProgress()
            | FailedSessionClosed()
        ):
            ran = _run(before, intent, after, now_wall)

        case (
            SessionDetached()
            | FailureAcknowledged()
            | TopicChanged()
            | UsageAccrued()
            | ContextPolled()
            | CompactionObserved()
            | SubagentDelivered()
            | AgentRemoved()
            | ConcernPosted()
            | InboxRead()
            | ConcernEdited()
            | ConcernWithdrawn()
            | TaskAmended()
            | TaskVersionHanded()
            | TaskDeclared()
            | TaskClaimRequested()
            | TaskCompleted()
            | TaskReleased()
            | TaskClosed()
            | TaskWithdrawn()
            | BoardRead()
            | DigestRead()
            | BoardRestored()
            | DecisionRaised()
            | DecisionAnswered()
            | DecisionDismissed()
            | DecisionWithdrawn()
            | DecisionsRead()
            | CommandRequested()
            | CommandApproved()
            | CommandRefused()
            | CommandWithdrawn()
            | CommandStopAsked()
            | CommandEnded()
            | CommandsRead()
            | CommandsRestored()
        ):
            # Nothing about a run. A removal, and a decision, an approval or a
            # command that brings a session back, are read off the snapshots
            # below.
            ran = None

        case _:
            assert_never(intent)

    out: dict[str, Change] = {}
    for session_id, on_fleet in _fleet(before, after).items():
        out[session_id] = Change(session_id, {"in_fleet": on_fleet})
    if ran is not None:
        moved = out.get(ran.session_id)
        if moved is not None:
            # The announce says a session is on the fleet and so does the
            # comparison, when both speak; the comparison is the definition.
            ran = Change(
                ran.session_id, {**ran.keys, **moved.keys}, ran.once, ran.spent, ran.started
            )
        out[ran.session_id] = ran
    return tuple(out.values())


def _fleet(before: Snapshot, after: Snapshot) -> dict[str, bool]:
    """
    Whether each session is on the fleet after the step, for the sessions the
    step changed that for and that still have a root.

    Only a root flagged ``detached`` on one side can answer differently on the
    two, so those are the ones asked: one pass over each snapshot's records and
    no more, which is what the reducer's own ``_back_on_fleet`` costs per step.
    """
    if before.nodes is after.nodes:
        return {}
    flagged = {
        nid[0]
        for snap in (before, after)
        for nid, rec in snap.nodes.items()
        if rec.detached and nid[1] is None
    }
    return {
        session_id: not after.off_fleet(session_id)
        for session_id in flagged
        if (session_id, None) in after.nodes
        and before.off_fleet(session_id) is not after.off_fleet(session_id)
    }


def _run(before: Snapshot, intent: Intent, after: Snapshot, now_wall: float) -> Change | None:
    node = intent.node_id
    if node is None or node[1] is not None:
        return None
    now = after.nodes.get(node)
    if now is None or now.recalled is not None:
        return None
    was = before.nodes.get(node)
    session_id = node[0]

    if isinstance(intent, AgentSpawned) and now is not was:
        # A root announced. Whatever the file says of an earlier run's end is
        # over, and the session is on the fleet whoever removed it: said here,
        # in store order, so it lands after everything that run derived.
        #
        # The model and the gate are not said here. An announce is a run that
        # was asked for, and those are facts about a run that reached the CLI:
        # ``launched`` says them when one has.
        announced: dict[str, object] = {
            "in_fleet": True,
            "ended_at": None,
            "ended_how": None,
            "error": None,
            "last_active": now_wall,
            "session_base": now.session_base,
        }
        if now.cwd is not None:
            announced["cwd"] = now.cwd
        # Which team a session is, and where its brief is, are facts about the
        # session and not about the launch that continued it: set where the
        # file has none, and otherwise kept. A resume from a surface that could
        # not know the team announces solo, and writing that would make the
        # file forget a team. The resume form offers the choice only where the
        # file has no answer, which is the case this writes.
        once = {
            key: value for key, value in (("template", now.template), ("brief", now.brief)) if value
        }
        return Change(session_id, announced, once=once)

    if was is None or was.recalled is not None or was.state is now.state:
        return None

    keys: dict[str, object] = {}
    if now.state.is_terminal and was.state.is_terminal:
        # An end said a second way: a session that failed, closed afterwards.
        # Its run ended when it failed, so the file keeps that moment, what
        # the failure said and when the session was last active; only how the
        # end is described moves. A failure with no text leaves a file that
        # says "closed" and nothing of having failed.
        keys["ended_how"] = _ended_how(now).value
        if now.error is not None:
            keys["error"] = now.error[:MAX_ERROR_CHARS]
    elif now.state.is_terminal:
        keys["last_active"] = now_wall
        keys["ended_at"] = now_wall
        keys["ended_how"] = _ended_how(now).value
        keys["error"] = None if now.error is None else now.error[:MAX_ERROR_CHARS]
    else:
        if was.state.is_terminal:
            # Alive again: a failed session that answered has no end.
            keys["ended_at"] = None
            keys["ended_how"] = None
        if now.state in _TURN_OVER:
            keys["last_active"] = now_wall
    # A root is live and past SPAWNING only once the driver holds a connected
    # client: the refusals that precede one end it from SPAWNING, and nothing
    # brings an ended root back but a message from a CLI that is there.
    started = (
        not now.state.is_terminal
        and now.state is not AgentState.SPAWNING
        and (was.state is AgentState.SPAWNING or was.state.is_terminal)
    )
    if not keys:
        return Change(session_id, started=True) if started else None
    return Change(
        session_id,
        keys,
        spent=Spent(now.started_at, now.usage.total_cost_usd),
        started=started,
    )


def _ended_how(root: AgentRecord) -> EndedHow:
    """
    How a root that is terminal ended. FAILED is a failure; DONE and CANCELLED
    are ends somebody asked for. Never SHUTDOWN: that is the shell's to say at
    quit, of sessions no step ended.
    """
    return EndedHow.FAILED if root.state is AgentState.FAILED else EndedHow.CLOSED


# -- what the shell says for itself -------------------------------------------------


def _message(text: str) -> str | None:
    """
    ``text`` as ``last_message`` holds it: itself, or null when it is longer than
    a file stores. The module docstring has why null and not a cut copy.
    """
    return text if len(text) <= sessionfile.MAX_LAST_MESSAGE else None


def launched(
    spec: LaunchSpec,
    session_id: str,
    policy_granted: Policy,
    contained: bool,
    now_wall: float,
    *,
    subagent_cap: int | None = None,
) -> Change:
    """
    What a run that reached the CLI adds to the session's file: the things the
    store is not told, and the ones that are facts about a run and not about
    asking for one -- the message it was opened with, its gate, whether it is
    contained, its model. The directory, team and brief are derived from the
    root's announce (``changes``), from the record the store built.

    **For a run that started, and submitted when it has** (``AgentSession.
    on_started``). A launch the pool took and then discarded, or that was
    refused before it connected, sent nothing and was gated by nothing, and a
    file that said otherwise would have the resume form offer a message nobody
    was sent.

    ``contained`` is the driver's notion -- a containment string that is not
    blank -- and ``policy_granted`` the rung the session object was built with.
    ``subagent_cap`` is the number the session runs under; without it the spec's
    own, which is None when the launch left it to the operator's setting.

    ``task`` is the card's title and is given only by a launch that is not a
    resume: ``sessionfile`` keeps the first ``task`` a file is given for good,
    and a resume's ``spec.task`` is the session's own task only when the surface
    that resumed it knew one. ``last_message`` is what the run was opened with:
    ``spec.message`` when the launch has one, and the task otherwise.
    ``created_at`` is set only where the file has none.

    The end of an earlier run is cleared and the session put on the fleet here
    as well as at the announce: a session resumed from the picker after it was
    removed is not one the store knows was removed.
    """
    keys: dict[str, object] = {
        "in_fleet": True,
        "ended_at": None,
        "ended_how": None,
        "error": None,
        "launched_at": now_wall,
        "last_active": now_wall,
        "last_message": _message(spec.task if spec.message is None else spec.message),
        "model": spec.model,
        "policy": policy_granted,
        "contained": contained,
        "subagent_cap": spec.subagent_cap if subagent_cap is None else subagent_cap,
    }
    if spec.resume is None:
        keys["task"] = spec.task
    return Change(session_id, keys, once={"created_at": now_wall})


def sent(session_id: str, text: str, now_wall: float) -> Change:
    """
    The operator sent a live session a message.
    """
    return Change(session_id, {"last_message": _message(text), "last_active": now_wall})


def gate_changed(session_id: str, policy: Policy) -> Change:
    """
    A live session's gate was narrowed or widened to ``policy``, and the driver
    took the change.
    """
    return Change(session_id, {"policy": policy})


def shutdown(session_ids: Iterable[str], now_wall: float) -> tuple[Change, ...]:
    """
    pptmstr is quitting with these sessions still live. The caller names them:
    the roots that are not terminal in the last snapshot it drew.
    """
    return tuple(
        Change(
            session_id,
            {"ended_how": EndedHow.SHUTDOWN.value, "ended_at": now_wall, "last_active": now_wall},
        )
        for session_id in session_ids
    )


# -- the writer ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Outcome:
    """
    What became of one write: every change to a session merged since its last.

    ``keys`` is what was to be set. ``through`` is the sequence number
    ``FleetWriter.submit`` returned for the latest change in it, so a caller that
    kept the number knows whether its change was part of this.
    """

    session_id: str
    keys: Mapping[str, object]
    result: sessionfile.Written | sessionfile.Unwritten
    through: int = 0

    @property
    def written(self) -> bool:
        return isinstance(self.result, sessionfile.Written)

    @property
    def lost_fleet(self) -> bool:
        """
        Whether a change of ``in_fleet`` is in this and did not reach the file.
        The operator is owed a line for every one: the next start will show the
        fleet as it was before they changed it.
        """
        return "in_fleet" in self.keys and not self.written


@dataclass(frozen=True, slots=True)
class Latched:
    """
    A session's file is not this build's to write, and why: it cannot be read,
    or a newer build wrote it. Reported once, after the ``Outcome`` of the write
    that found it. Nothing ends it within the process.
    """

    session_id: str
    reason: str


Report = Outcome | Latched


class _Item:
    """
    Every change to one session not yet written, merged in submission order.
    """

    __slots__ = ("keys", "once", "spent", "started", "through")

    def __init__(self) -> None:
        self.keys: dict[str, object] = {}
        self.once: dict[str, object] = {}
        # By run, in the order the runs were first seen.
        self.spent: dict[float, float] = {}
        self.started = False
        self.through = 0

    def take(self, change: Change, sequence: int) -> None:
        self.keys.update(change.keys)
        for key, value in change.once.items():
            self.once.setdefault(key, value)
        if change.spent is not None:
            run, usd = change.spent.run, change.spent.usd
            self.spent[run] = max(usd, self.spent.get(run, usd))
        self.started = self.started or change.started
        self.through = sequence

    def then(self, later: _Item) -> _Item:
        """
        This with ``later`` merged over it, for an item held back and the one
        that arrived since.
        """
        self.keys.update(later.keys)
        for key, value in later.once.items():
            self.once.setdefault(key, value)
        for run, usd in later.spent.items():
            self.spent[run] = max(usd, self.spent.get(run, usd))
        self.started = self.started or later.started
        self.through = max(self.through, later.through)
        return self


class FleetWriter:
    """
    The one writer of session files. ``admit`` and ``submit`` from any thread,
    ``drain`` on the owner's schedule, ``close`` once at shutdown.

    ``root`` is the sessions root, as ``sessionfile`` takes it: None is the one
    under the configuration directory, asked at each write.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root
        self._changed = threading.Condition(threading.Lock())
        # Read and written only with ``_changed`` held, down to ``_finished``.
        self._pending: dict[str, _Item] = {}
        self._admitted: set[str] = set()
        self._sequence: dict[str, int] = {}
        self._last_active: dict[str, float] = {}
        self._reports: list[Report] = []
        self._worker: threading.Thread | None = None
        self._in_flight: tuple[str, _Item] | None = None
        self._closed = False
        self._abandoned = False
        self._finished = False
        # The worker's alone. ``_ran`` is the sessions a run of which reached the
        # CLI and ``_founded`` the ones whose file is known to exist; ``_held`` is
        # what a session with no file yet is owed; ``_owed`` is an ``in_fleet`` a
        # failed write dropped; ``_based`` is the run a session's cost is counted
        # from and what the file held then.
        self._latched: dict[str, str] = {}
        self._ran: set[str] = set()
        self._founded: set[str] = set()
        self._held: dict[str, _Item] = {}
        self._owed: dict[str, object] = {}
        self._based: dict[str, tuple[float, float]] = {}

    # -- the owner's side ----------------------------------------------------------

    def admit(self, session_id: str) -> None:
        """
        Allow this session's file to be written. Performs no IO.

        Called for a session the shell is about to hand a real driver's pool --
        before the hand-over, since the announce it causes is observed on
        another thread and a change that arrives first is dropped -- and for
        every session ``recall`` read a file of. Never for a scripted session.
        """
        with self._changed:
            self._admitted.add(session_id)

    def submit(self, change: Change) -> int:
        """
        Queue one change and return. Performs no IO and never waits on a write.

        Returns the change's sequence number among those to its session, from 1,
        which is the order they are merged in: a later number's keys win. 0 means
        it was not queued -- the session was never admitted, which is silent, or
        the writer is closed, which is reported.

        Changes to one session waiting on the worker are one item however many
        there are, so a session that flaps costs one write per pass of the worker
        and the queue holds at most one item per session.
        """
        session_id = change.session_id
        with self._changed:
            active = change.keys.get("last_active")
            if isinstance(active, (int, float)) and not isinstance(active, bool):
                latest = max(float(active), self._last_active.get(session_id, float(active)))
                self._last_active[session_id] = latest
                if latest != active:
                    change = Change(
                        session_id,
                        {**change.keys, "last_active": latest},
                        change.once,
                        change.spent,
                        change.started,
                    )
            if session_id not in self._admitted:
                return 0
            if self._closed:
                self._reports.append(
                    Outcome(
                        session_id,
                        dict(change.keys),
                        sessionfile.Unwritten("submitted after the writer was closed"),
                    )
                )
                return 0
            sequence = self._sequence.get(session_id, 0) + 1
            self._sequence[session_id] = sequence
            self._pending.setdefault(session_id, _Item()).take(change, sequence)
            if self._worker is None:
                self._worker = threading.Thread(target=self._run, name="fleetsync", daemon=True)
                self._worker.start()
            self._changed.notify_all()
            return sequence

    def last_active(self, session_id: str) -> float | None:
        """
        The wall time ``session_id`` was last active, as far as this process has
        seen, or None when it has seen nothing of it. Performs no IO.

        Kept for every session a change was submitted for, admitted or not, so
        it answers for a scripted session too. None for a recalled session that
        has not been resumed: its file's figure is ``Recalled.last_active``.
        """
        with self._changed:
            return self._last_active.get(session_id)

    def drain(self) -> tuple[Report, ...]:
        """
        Everything settled since the last call, oldest first. Never waits.
        """
        with self._changed:
            settled = tuple(self._reports)
            self._reports.clear()
        return settled

    @property
    def outstanding(self) -> int:
        """How many sessions have changes no write has been tried for."""
        with self._changed:
            return len(self._pending) + (self._in_flight is not None)

    def close(self, timeout: float = CLOSE_TIMEOUT) -> tuple[Report, ...]:
        """
        Stop taking changes, wait at most ``timeout`` seconds for the queued
        ones, and return everything not yet drained.

        What is still queued when the wait ends is given up and returned as
        ``Unwritten``. The one write that had begun is returned the same way and
        may still reach disk; its reason says so.

        A session held for want of a run that reached the CLI is dropped here
        without a report: it never had a file to be wrong.
        """
        with self._changed:
            self._closed = True
            self._changed.notify_all()
            if not self._abandoned:
                if self._worker is not None:
                    self._changed.wait_for(lambda: self._finished, timeout=max(timeout, 0.0))
                self._abandoned = True
                if self._in_flight is not None:
                    session_id, item = self._in_flight
                    self._reports.append(
                        Outcome(
                            session_id,
                            dict(item.keys),
                            sessionfile.Unwritten(
                                "its write had begun and had not returned when the writer"
                                " closed; it may still reach disk"
                            ),
                            item.through,
                        )
                    )
                for session_id, item in self._pending.items():
                    self._reports.append(
                        Outcome(
                            session_id,
                            dict(item.keys),
                            sessionfile.Unwritten(
                                "still queued when the writer closed; not written"
                            ),
                            item.through,
                        )
                    )
                self._pending.clear()
                self._changed.notify_all()
            settled = tuple(self._reports)
            self._reports.clear()
        return settled

    # -- the worker's side ---------------------------------------------------------

    def _run(self) -> None:
        retrying: deque[str] = deque()
        # Whether something was written since the dropped ``in_fleet`` keys were
        # last tried, and whether they have had their try at close.
        due = swept = False
        while True:
            with self._changed:
                while True:
                    if self._abandoned:
                        return
                    if self._pending:
                        session_id = next(iter(self._pending))
                        item = self._pending.pop(session_id)
                        retry, due = False, True
                        break
                    if not retrying and (due or (self._closed and not swept)):
                        retrying.extend(self._owed)
                        due, swept = False, self._closed
                    if retrying:
                        session_id, item, retry = retrying.popleft(), _Item(), True
                        break
                    if self._closed:
                        self._finished = True
                        self._changed.notify_all()
                        return
                    self._changed.wait()
                self._in_flight = (session_id, item)
            try:
                reports = self._settle(session_id, item, retry)
            # Whatever a write raises is that write's outcome. The worker ending
            # on it would turn every later change into a silent drop.
            except Exception as exc:
                reports = (
                    Outcome(
                        session_id,
                        dict(item.keys),
                        sessionfile.Unwritten(f"{type(exc).__name__}: {exc}"),
                        item.through,
                    ),
                )
            with self._changed:
                if self._abandoned:
                    # ``close`` has already reported this write.
                    return
                self._in_flight = None
                self._reports.extend(reports)
                self._changed.notify_all()

    def _settle(self, session_id: str, item: _Item, retry: bool) -> tuple[Report, ...]:
        """
        Write what ``session_id`` is owed. ``retry`` is a second try at an
        ``in_fleet`` an earlier write dropped, whose failure was reported then
        and is not reported again.
        """
        held = self._held.pop(session_id, None)
        if held is not None:
            item = held.then(item)
        if session_id in self._owed:
            item.keys.setdefault("in_fleet", self._owed[session_id])
        if item.started:
            self._ran.add(session_id)

        latched = self._latched.get(session_id)
        if latched is not None:
            refusal = sessionfile.Unwritten(f"not written: {latched}")
            return (Outcome(session_id, dict(item.keys), refusal, item.through),)

        record: sessionfile.SessionFile | None = None
        if session_id not in self._founded or item.once or item.spent:
            found = sessionfile.read(session_id, self.root)
            reason = _not_ours(found)
            if reason is not None:
                return self._latch(session_id, item, reason)
            if isinstance(found, sessionfile.SessionFile):
                record = found
                self._founded.add(session_id)
            elif session_id not in self._ran:
                # No file, and no run of it has reached the CLI. Kept, not
                # written: a launch that is refused must leave nothing behind.
                self._held[session_id] = item
                return ()

        keys = dict(item.keys)
        for key, value in item.once.items():
            if key not in keys and (record is None or getattr(record, key) is None):
                keys[key] = value
        based = self._based.get(session_id)
        if item.spent:
            based, cost = _cost(based, item.spent, None if record is None else record.cost_usd)
            if cost is not None:
                keys["cost_usd"] = cost
        if not keys:
            self._based_on(session_id, based)
            return ()

        result = sessionfile.update(session_id, keys, self.root)
        outcome = Outcome(session_id, keys, result, item.through)
        if isinstance(result, sessionfile.Written):
            self._founded.add(session_id)
            self._owed.pop(session_id, None)
            self._based_on(session_id, based)
            return (outcome,)

        reason = _not_ours(sessionfile.read(session_id, self.root))
        if reason is not None:
            return (outcome, Latched(session_id, self._latch_on(session_id, reason)))
        if session_id not in self._founded:
            # The file was to be created and was not. Everything it was to hold
            # is tried again at the session's next change.
            self._held[session_id] = item
        elif "in_fleet" in keys:
            self._owed[session_id] = keys["in_fleet"]
        return () if retry else (outcome,)

    def _based_on(self, session_id: str, based: tuple[float, float] | None) -> None:
        if based is not None:
            self._based[session_id] = based

    def _latch_on(self, session_id: str, reason: str) -> str:
        self._latched[session_id] = reason
        self._owed.pop(session_id, None)
        self._held.pop(session_id, None)
        return reason

    def _latch(self, session_id: str, item: _Item, reason: str) -> tuple[Report, ...]:
        refusal = sessionfile.Unwritten(f"not written: {reason}")
        return (
            Outcome(session_id, dict(item.keys), refusal, item.through),
            Latched(session_id, self._latch_on(session_id, reason)),
        )


def _not_ours(found: sessionfile.Found) -> str | None:
    """
    Why a session's file is not this build's to write, or None when it is, or
    is not there.
    """
    if isinstance(found, sessionfile.Unreadable):
        return f"its {sessionfile.FILE_NAME} cannot be read ({found.reason})"
    if isinstance(found, sessionfile.SessionFile) and not found.writable:
        return (
            f"a newer build wrote its {sessionfile.FILE_NAME} (format {found.format},"
            f" and this build writes {sessionfile.FORMAT})"
        )
    return None


def _cost(
    based: tuple[float, float] | None, spent: Mapping[float, float], held: float | None
) -> tuple[tuple[float, float] | None, float | None]:
    """
    The base to count from next and the ``cost_usd`` to write, or None for the
    second when the file's figure stands.

    ``based`` is the run being counted and what the file held when it was first
    seen; ``held`` is what the file holds now. A run later than that one starts
    from the figure reached so far; an earlier one is a report that arrived
    behind its successor and is not counted twice. The result is never below
    ``held``, and is not written when it would say "nothing spent" over a file
    that says nothing.
    """
    cost = held or 0.0
    for run in sorted(spent):
        if based is None or run > based[0]:
            based = (run, cost)
        elif run < based[0]:
            continue
        cost = max(cost, based[1] + spent[run])
    return based, (cost if cost > (held or 0.0) else None)


# -- the fleet at startup -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Fleet:
    """
    What the session files say the fleet is.

    ``sessions`` is one ``SessionRecalled`` per readable file whose session is
    on the fleet, most recently active first; applying them in this order is the
    order the cards are drawn in. ``killed`` names the ones among them whose file
    records no end -- pptmstr died, or was killed, with the session live -- which
    is also what ``Recalled.ended_how`` None says on each.

    Not recalled, and named: ``detached`` are the sessions the operator removed;
    ``unreadable`` the directories whose file is there and cannot be read, each
    with why. A directory with no file is neither -- it holds a board log and no
    session file, and is not a card.

    ``problem`` is why this is not the whole root, from ``sessionfile.listing``.
    ``unlisted`` is why the CLI's transcripts could not be listed, in which case
    every ``Recalled.transcript`` is UNKNOWN.
    """

    sessions: tuple[SessionRecalled, ...] = ()
    killed: tuple[str, ...] = ()
    detached: tuple[str, ...] = ()
    unreadable: tuple[tuple[str, str], ...] = ()
    problem: str | None = None
    unlisted: str | None = None


def _transcripts() -> Iterable[SessionRow]:
    # Imported here: ``sessions`` imports the SDK, and nothing else in this
    # module needs it.
    from . import sessions

    return sessions.enumerate_sessions()


def recall(
    root: Path | None = None,
    list_sessions: Callable[[], Iterable[SessionRow]] = _transcripts,
) -> Fleet:
    """
    Read the fleet from disk. **Blocks**: one read per session file, then one
    call to ``list_sessions``, which opens every transcript the CLI holds. For a
    worker thread. Never raises, writes nothing, and admits nothing: the caller
    hands each recalled id to ``FleetWriter.admit``.

    ``list_sessions`` gives the sessions the CLI can resume, in any order; the
    default is ``sessions.enumerate_sessions()`` over every project. A session
    in it is PRESENT and one that is not is GONE. If it raises, every session is
    UNKNOWN and ``Fleet.unlisted`` says why: "could not look" offers a resume
    that may fail, which is the better of the two wrong cards.

    A file with no ``task`` -- a session first run by an older build and resumed
    under this one -- is titled from the CLI's own line for it, when it has one,
    and says so (``Recalled.task_recorded``): that line is a title and not a
    prompt to start a session from.
    """
    listing = sessionfile.listing(root)
    rows: dict[str, Any] | None
    unlisted: str | None = None
    try:
        rows = {row.session_id: row for row in list_sessions()}
    except Exception as exc:
        rows, unlisted = None, f"{type(exc).__name__}: {exc}"

    kept: list[sessionfile.SessionFile] = []
    detached: list[sessionfile.SessionFile] = []
    unreadable: list[tuple[str, str]] = []
    for session_id, found in listing.sessions:
        if isinstance(found, sessionfile.Unreadable):
            unreadable.append((session_id, found.reason))
        elif isinstance(found, sessionfile.SessionFile):
            (kept if found.in_fleet else detached).append(found)
    kept.sort(key=lambda held: (-_recency(held), held.session_id))

    recalled: list[SessionRecalled] = []
    for held in kept:
        row = None if rows is None else rows.get(held.session_id)
        if rows is None:
            presence = TranscriptPresence.UNKNOWN
        else:
            presence = TranscriptPresence.GONE if row is None else TranscriptPresence.PRESENT
        recalled.append(
            SessionRecalled(
                session_id=held.session_id,
                task=held.task or ("" if row is None else row.label),
                model=held.model,
                recalled=Recalled(
                    transcript=presence,
                    created_at=held.created_at,
                    last_active=held.last_active,
                    ended_how=None if held.ended_how is None else EndedHow(held.ended_how),
                    policy=held.policy if isinstance(held.policy, Policy) else None,
                    contained=held.contained,
                    cost_usd=held.cost_usd,
                    error=held.error,
                    last_message=held.last_message,
                    subagent_cap=held.subagent_cap,
                    task_recorded=bool(held.task),
                ),
                cwd=held.cwd or None,
                session_base=held.session_base,
                template=held.template,
                brief=held.brief,
            )
        )
    return Fleet(
        sessions=tuple(recalled),
        killed=tuple(held.session_id for held in kept if held.ended_how is None),
        detached=tuple(held.session_id for held in detached),
        unreadable=tuple(unreadable),
        problem=listing.problem,
        unlisted=unlisted,
    )


def _recency(held: sessionfile.SessionFile) -> float:
    for at in (held.last_active, held.launched_at, held.created_at):
        if at is not None:
            return at
    return 0.0
